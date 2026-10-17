"""Synthetic scoring, staging, review and explicit bulk acceptance."""
import asyncio
import base64
from email.message import EmailMessage
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from snapshot.cleanup import Cleanup, Jev, protected
from snapshot.store import SnapshotStore
from snapshot.private_service import create_app
from tests.test_snapshot_setup import FakeKeyring, SNAPSHOT_NAMESPACE, MODIFY_SCOPE


def mail(subject, body, sender='fixture@example.test'):
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = sender
    message.set_content(body)
    return {'raw': base64.urlsafe_b64encode(message.as_bytes()).decode(), 'internalDate': '1000'}


class FakeJev:
    calls = []
    def __init__(self, key=None):
        pass
    async def score(self, text):
        self.calls.append(text)
        return 'delete-candidates', .99, 'other'
    async def close(self):
        pass


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_keep_rules_copies_and_default_delete_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(directory)
            job = store.create_job('fixture-account', 3)
            originals = [mail('Foothill Commons', 'Community news'), mail('Monthly statement', 'Your account statement is ready.'), mail('Sale today', 'Save on new shoes this week.')]
            store.page(job, ['0', '1', '2'], None)
            for i, original in enumerate(originals):
                store.outcome(job, str(i), 'saved', original)
            store.state(job, 'done')
            cleanup = Cleanup(store)
            rid = cleanup.create('fixture-account', job)
            scorer = FakeJev()
            scorer.calls = []
            await cleanup.score(rid, 'fixture-account', job, scorer)
            summary = cleanup.summary('fixture-account', job)
            self.assertEqual((summary['keep'], summary['candidates'], summary['review']), (2, 1, 0))
            self.assertEqual(len(scorer.calls), 1)
            self.assertNotIn('fixture@example.test', scorer.calls[0])
            self.assertEqual(cleanup.members(rid), ['2'])
            self.assertEqual(cleanup.verified_members(rid, 'fixture-account'), ['2'])
            copies = list((Path(directory) / 'cleanup-review' / rid).rglob('*.eml'))
            self.assertEqual(len(copies), 3)
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in copies))
            self.assertEqual({path.read_bytes() for path in copies}, {base64.urlsafe_b64decode(value['raw']) for value in originals})
            self.assertEqual([store.original('fixture-account', str(i)) for i in range(3)], originals)
            self.assertEqual(cleanup.review(rid, 'fixture-account', 'delete-candidates', 0)[0]['score'], .99)
            # Resume is idempotent: staged entries aren't sent or copied again.
            await cleanup.score(rid, 'fixture-account', job, scorer)
            self.assertEqual(len(scorer.calls), 1)
            candidate = next((Path(directory) / 'cleanup-review' / rid / 'delete-candidates').glob('*.eml'))
            candidate.unlink()
            with self.assertRaises(OSError):
                cleanup.verified_members(rid, 'fixture-account')

    async def test_jev_payload_and_protected_category_override_score(self):
        requests = []
        def provider(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'answers': {
                'category': {'type': 'choice', 'choice': 'bank', 'probabilities': {'bank': .99, 'apartment': 0, 'other': .01, 'uncertain': 0}},
                'deletability': {'type': 'noul', 'noul': .99}}})
        real = httpx.AsyncClient
        with patch('snapshot.cleanup.httpx.AsyncClient', side_effect=lambda *a, **k: real(*a, **k, transport=httpx.MockTransport(provider))):
            jev = Jev('fake-key')
            try:
                self.assertEqual(await jev.score('Original subject and body'), ('keep', .99, 'bank'))
            finally:
                await jev.close()
        self.assertEqual(requests[0]['state'], 'Original subject and body')
        self.assertEqual(requests[0]['model'], 'jev-latest')
        self.assertNotIn('gmail', json.dumps(requests[0]).lower())
        self.assertEqual(protected('Update', 'FoothillCommons@example.test'), 'apartment')

    async def test_bulk_500_message_writes_and_already_trash(self):
        from snapshot.trash import bulk_trash_messages
        class Gmail:
            def __init__(self):
                self.posts = []
            async def request(self, path, params=None):
                mid = path.split('/')[-1]
                return {'id': mid, 'labelIds': ['TRASH'] if mid == '0' else ['INBOX']}
            async def post(self, path, payload):
                self.posts.append((path, payload))
                return {}
        gmail = Gmail()
        result = await bulk_trash_messages(gmail, [str(i) for i in range(1001)], lambda summary: None)
        self.assertEqual(result['moved'], 1000)
        self.assertEqual(result['already'], 1)
        self.assertEqual([len(payload['ids']) for _, payload in gmail.posts], [499, 500, 1])
        self.assertTrue(all(path == 'messages/batchModify' and payload['addLabelIds'] == ['TRASH'] for path, payload in gmail.posts))

    async def test_bulk_label_bounded_batches_and_preserves_other_labels(self):
        from snapshot.labels import bulk_label_messages
        class Gmail:
            def __init__(self):
                self.posts = []
            async def request(self, path, params=None):
                if path == 'labels':
                    return {'labels': [{'id': 'later', 'name': 'Later', 'type': 'user'}]}
                mid = path.split('/')[-1]
                return {'id': mid, 'labelIds': ['later'] if mid == '0' else ['TRASH'] if mid == '1' else ['INBOX', 'CATEGORY_UPDATES']}
            async def post(self, path, payload):
                self.posts.append((path, payload))
        gmail = Gmail()
        result = await bulk_label_messages(gmail, [str(i) for i in range(1002)], 'Later', lambda v: None)
        self.assertEqual((result['status'], result['moved'], result['already'], result['skipped']), ('done', 1000, 1, 1))
        self.assertEqual([len(v['ids']) for _, v in gmail.posts], [498, 500, 2])
        self.assertTrue(all(v['addLabelIds'] == ['later'] and v['removeLabelIds'] == [] for _, v in gmail.posts))


class CleanupAPITests(unittest.TestCase):
    def test_scoring_never_changes_gmail_until_exact_run_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / 'google-client.json'
            cfg.write_text(json.dumps({'installed': {'client_id': 'fixture', 'client_secret': 'fake-secret'}}))
            store = SnapshotStore(directory)
            jid = store.create_job('fixture-account', 2)
            store.page(jid, ['keep-id', 'delete-id'], None)
            store.outcome(jid, 'keep-id', 'saved', mail('Foothill Commons', 'Your community news'))
            store.outcome(jid, 'delete-id', 'saved', mail('Sale this week', 'Shop new summer styles'))
            store.state(jid, 'done')
            env = {'GMAIL_SNAPSHOT_REGULAR_SESSION': '1', 'GMAIL_SNAPSHOT_CAPABILITY': 'synthetic-capability',
                   'GMAIL_SNAPSHOT_DIRECTORY': directory, 'GMAIL_SNAPSHOT_CLIENT_CONFIG': str(cfg),
                   'GMAIL_SNAPSHOT_REUSE_MAIN_SETUP': '0', 'TYPESAFE_API_KEY': 'fake-key'}
            keyring = FakeKeyring({(SNAPSHOT_NAMESPACE, 'fixture'): json.dumps({'scope': MODIFY_SCOPE, 'refresh_token': 'fake-refresh'})})
            writes = []
            account = ['fixture-account']
            def provider(request):
                if request.url.path.endswith('/token'):
                    return httpx.Response(200, json={'access_token': 'fake-access', 'scope': MODIFY_SCOPE})
                if request.url.path.endswith('/profile'):
                    return httpx.Response(200, json={'emailAddress': account[0]})
                if request.url.path.endswith('/labels'):
                    return httpx.Response(200, json={'labels': [{'id': 'label-later', 'name': 'Later', 'type': 'user'}]})
                if request.method == 'GET':
                    return httpx.Response(200, json={'id': request.url.path.split('/')[-1], 'labelIds': ['CATEGORY_UPDATES']})
                writes.append(json.loads(request.content))
                return httpx.Response(204)
            real = httpx.AsyncClient
            with patch.dict('os.environ', env), patch('snapshot.private_service.Keyring', return_value=keyring), patch('snapshot.private_service.Jev', FakeJev), patch('snapshot.private_service.httpx.AsyncClient', side_effect=lambda *a, **k: real(*a, **k, transport=httpx.MockTransport(provider))):
                with TestClient(create_app(), base_url='http://127.0.0.1:9000') as client:
                    headers = {'x-snapshot-capability': 'synthetic-capability'}
                    client.post('/api/refresh', headers=headers)
                    self.assertEqual(client.post('/api/cleanup-score', headers=headers).json(), {'started': True})
                    for _ in range(100):
                        status = client.get('/api/status', headers=headers).json()['cleanup']
                        if status['status'] != 'running':
                            break
                        time.sleep(.01)
                    self.assertEqual(status['status'], 'done')
                    self.assertEqual((status['keep'], status['candidates']), (1, 1))
                    self.assertEqual(writes, [])
                    review = client.post('/api/cleanup-review', headers=headers, json={'decision': 'delete-candidates', 'offset': 0})
                    self.assertNotIn('delete-id', review.text)
                    self.assertNotIn('fake-key', client.get('/api/status', headers=headers).text)
                    payload = {'run_id': status['id']}
                    self.assertEqual(client.post('/api/cleanup-accept', json=payload).status_code, 403)
                    self.assertEqual(client.post('/api/cleanup-accept', headers=headers, json={'run_id': 'a' * 32}).status_code, 409)
                    self.assertEqual(client.post('/api/cleanup-accept', headers=headers, json={**payload, 'ids': ['keep-id']}).status_code, 422)
                    account[0] = 'another-account'
                    self.assertEqual(client.post('/api/cleanup-accept', headers=headers, json=payload).status_code, 409)
                    self.assertEqual(writes, [])
                    account[0] = 'fixture-account'
                    client.post('/api/refresh', headers=headers)
                    label_payload = {**payload, 'label_name': 'Later', 'remove_updates': True}
                    self.assertEqual(client.post('/api/cleanup-label', json=label_payload).status_code, 403)
                    self.assertEqual(client.post('/api/cleanup-label', headers=headers, json={**label_payload, 'run_id': 'a'*32}).status_code, 409)
                    self.assertEqual(client.post('/api/cleanup-label', headers=headers, json={**label_payload, 'ids': ['keep-id']}).status_code, 422)
                    self.assertEqual(client.post('/api/cleanup-label', headers=headers, json={**label_payload, 'label_name': 'TRASH'}).status_code, 422)
                    account[0] = 'another-account'
                    self.assertEqual(client.post('/api/cleanup-label', headers=headers, json=label_payload).status_code, 409)
                    self.assertEqual(writes, [])
                    account[0] = 'fixture-account'
                    client.post('/api/refresh', headers=headers)
                    self.assertEqual(client.post('/api/cleanup-label', headers=headers, json=label_payload).json(), {'started': True})
                    for _ in range(100):
                        status = client.get('/api/status', headers=headers).json()['cleanup']
                        if status['label']['status'] != 'running':
                            break
                        time.sleep(.01)
                    self.assertEqual(status['label']['status'], 'done')
                    self.assertEqual(status['status'], 'done')
                    self.assertEqual((status['keep'], status['candidates']), (1, 1))
                    self.assertEqual(writes, [{'ids': ['delete-id'], 'addLabelIds': ['label-later'], 'removeLabelIds': ['CATEGORY_UPDATES']}])
                    writes.clear()
                    self.assertEqual(client.post('/api/cleanup-accept', headers=headers, json=payload).json(), {'started': True})
                    for _ in range(100):
                        status = client.get('/api/status', headers=headers).json()['cleanup']
                        if status['trash']['status'] != 'running':
                            break
                        time.sleep(.01)
                    self.assertEqual(status['status'], 'accepted')
                    self.assertEqual(writes[0]['ids'], ['delete-id'])
                    self.assertEqual(writes[0]['addLabelIds'], ['TRASH'])
                    self.assertEqual(len(list(Path(directory).rglob('*.eml'))), 2)
                    self.assertIsNotNone(store.original('fixture-account', 'keep-id'))
