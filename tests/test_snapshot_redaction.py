"""Synthetic mail only; no private mailbox, Google or Keychain access."""
import base64
from email.message import EmailMessage
import unittest
from types import SimpleNamespace
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient

from snapshot.redact import extract, redact, load_model, main
from snapshot.store import SnapshotStore
from snapshot.private_service import create_app
from snapshot.ui_server import create_app as create_ui
from tests.test_snapshot_setup import FakeKeyring, SNAPSHOT_NAMESPACE, MODIFY_SCOPE


def mail(body, html=False):
    message = EmailMessage()
    message['From'] = 'Alice Smith <alice@example.test>'
    message['To'] = 'Bob Jones <bob@example.test>'
    message['Subject'] = 'Update for Alice Smith'
    message.set_content(body, subtype='html' if html else 'plain')
    return {'raw': base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip('=')}


class RedactionTests(unittest.TestCase):
    def test_mime_headers_rules_and_consistent_placeholders(self):
        original = mail('Alice Smith: email alice@example.test or call +1 (415) 555-0123. '
                        'Visit https://example.test/private/alice?id=123. Order ID AB12345.')
        text, names, attachments = extract(original)
        result, counts = redact(text, SimpleNamespace(ents=[]), names)
        for secret in ('Alice Smith', 'alice@example.test', '555-0123', 'https://', 'AB12345'):
            self.assertNotIn(secret, result)
        self.assertEqual(result.count('[PERSON_1]'), 2)
        self.assertEqual(counts['PERSON'], 1)
        self.assertEqual(attachments, 0)

    def test_html_scripts_links_and_attachments_are_not_exported(self):
        original = mail('<style>Secret Style</style><script>Secret Script</script>'
                        '<p>Hello Alice Smith</p><a href="https://private.test/token">Update</a>', True)
        text, names, _ = extract(original)
        self.assertNotIn('Secret', text)
        self.assertNotIn('private.test', text)
        result, _ = redact(text, SimpleNamespace(ents=[]), names)
        self.assertNotIn('Alice Smith', result)

    def test_inline_logos_do_not_count_as_file_attachments(self):
        message = EmailMessage()
        message.set_content('Synthetic body text')
        message.add_attachment(b'logo', maintype='image', subtype='png', filename='logo.png', disposition='inline')
        value = {'raw': base64.urlsafe_b64encode(message.as_bytes()).decode()}
        self.assertEqual(extract(value)[2], 0)
        message.add_attachment(b'pdf', maintype='application', subtype='pdf', filename='document.pdf', disposition='inline')
        value['raw'] = base64.urlsafe_b64encode(message.as_bytes()).decode()
        self.assertEqual(extract(value)[2], 1)
        message.add_attachment(b'photo', maintype='image', subtype='png', filename='photo.png')
        value['raw'] = base64.urlsafe_b64encode(message.as_bytes()).decode()
        self.assertEqual(extract(value)[2], 2)

    def test_entity_overlap_does_not_expose_email(self):
        text = 'alice@example.test'
        entity = SimpleNamespace(start_char=0, end_char=5, label_='PERSON')
        result, _ = redact(text, SimpleNamespace(ents=[entity]))
        self.assertEqual(result, '[EMAIL_1]')

    def test_actual_local_model_detects_body_name(self):
        model = load_model()
        text = 'Barack Obama visited Microsoft in Seattle.'
        result, counts = redact(text, model(text))
        self.assertNotIn('Barack Obama', result)
        self.assertIn('PERSON', counts)

    def test_private_command_preserves_originals_and_exports_no_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            job = store.create_job('synthetic-account', 10)
            store.page(job, ['synthetic-gmail-id'], None)
            original = mail('Hello Alice Smith. Email alice@example.test.')
            original['id'] = 'synthetic-gmail-id'
            store.outcome(job, 'synthetic-gmail-id', 'saved', original)
            before = store.path.read_bytes()
            with patch('sys.argv', ['redact', '--directory', directory]), redirect_stdout(io.StringIO()):
                main()
            self.assertEqual(store.path.read_bytes(), before)
            review = next(Path(directory).glob('redaction-review-*'))
            value = json.loads((review / 'message-0001.json').read_text())
            self.assertTrue(value['review_required'])
            for secret in ('Alice Smith', 'alice@example.test', 'synthetic-account', 'synthetic-gmail-id'):
                self.assertNotIn(secret, json.dumps(value))
            self.assertEqual((review.stat().st_mode & 0o777), 0o700)
            self.assertEqual(((review / 'message-0001.json').stat().st_mode & 0o777), 0o600)

    def test_private_api_runs_local_model_and_returns_counts_only(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / 'client.json'
            cfg.write_text(json.dumps({'installed': {'client_id': 'fixture', 'client_secret': 'fake-secret'}}))
            store = SnapshotStore(directory)
            job = store.create_job('fixture-account', 10)
            store.page(job, ['fixture-id'], None)
            store.outcome(job, 'fixture-id', 'saved', mail('Alice Smith: alice@example.test'))
            store.state(job, 'done')
            env = {'GMAIL_SNAPSHOT_ISOLATION_VERIFIED': '1', 'GMAIL_SNAPSHOT_CAPABILITY': 'synthetic-capability',
                   'GMAIL_SNAPSHOT_DIRECTORY': directory, 'GMAIL_SNAPSHOT_CLIENT_CONFIG': str(cfg),
                   'GMAIL_SNAPSHOT_REUSE_MAIN_SETUP': '0'}
            keyring = FakeKeyring({(SNAPSHOT_NAMESPACE, 'fixture'): json.dumps({'scope': MODIFY_SCOPE, 'refresh_token': 'fake-refresh'})})
            def provider(request):
                if request.url.path.endswith('/token'):
                    return httpx.Response(200, json={'access_token': 'fake-access', 'scope': MODIFY_SCOPE})
                return httpx.Response(200, json={'emailAddress': 'fixture-account'})
            real_client = httpx.AsyncClient
            def fake_client(*args, **kwargs):
                return real_client(*args, **kwargs, transport=httpx.MockTransport(provider))
            with patch.dict('os.environ', env), patch('snapshot.private_service.Keyring', return_value=keyring), patch('snapshot.private_service.httpx.AsyncClient', side_effect=fake_client):
                with TestClient(create_app(), base_url='http://127.0.0.1:9000') as client:
                    headers = {'x-snapshot-capability': 'synthetic-capability'}
                    self.assertEqual(client.post('/api/redact', json={'limit': 1}).status_code, 403)
                    self.assertEqual(client.post('/api/redact', headers=headers, json={'limit': True}).status_code, 422)
                    client.post('/api/refresh', headers=headers)
                    self.assertEqual(client.post('/api/redact', headers=headers, json={'limit': 1}).status_code, 200)
                    self.assertEqual(client.post('/api/redact', headers=headers, json={'limit': 1}).status_code, 409)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        summary = client.get('/api/status', headers=headers).json()['redaction']
                        if summary['status'] != 'running':
                            break
                        time.sleep(.03)
                    self.assertEqual(summary['status'], 'done')
                    self.assertEqual(summary['processed'], 1)
                    for secret in ('Alice Smith', 'example.test', directory, 'fixture-id'):
                        self.assertNotIn(secret, json.dumps(summary))

    def test_public_proxy_filters_review_content(self):
        real_client = httpx.AsyncClient
        def provider(request):
            return httpx.Response(200, json={'redaction': {'status': 'done', 'processed': 10,
                'text': 'PRIVATE MAIL', 'directory': 'PRIVATE PATH'}, 'original': 'PRIVATE MAIL'})
        def fake_client(*args, **kwargs):
            return real_client(*args, **kwargs, transport=httpx.MockTransport(provider))
        with patch.dict('os.environ', {'GMAIL_SNAPSHOT_CAPABILITY': 'synthetic-capability'}), patch('snapshot.ui_server.httpx.AsyncClient', side_effect=fake_client):
            with TestClient(create_ui(), base_url='http://127.0.0.1:8766') as client:
                client.get('/')
                response = client.get('/api/snapshot/status')
                self.assertEqual(response.json()['redaction'], {'status': 'done', 'processed': 10})
                self.assertNotIn('PRIVATE', response.text)


if __name__ == '__main__':
    unittest.main()
