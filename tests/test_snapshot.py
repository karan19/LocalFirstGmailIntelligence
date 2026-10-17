"""Synthetic-only tests. Never import the legacy server or open real private stores."""
import asyncio
import json
import tempfile
import unittest
import time
import httpx
from pathlib import Path
from unittest.mock import patch, Mock

from fastapi.testclient import TestClient
from snapshot.download import download
from snapshot.store import SnapshotStore
from snapshot.private_service import DownloadOptions, create_app, private_path
from snapshot.ui_server import create_app as create_ui
from snapshot.launch import arguments, DEFAULT_DIRECTORY


class FakeGmail:
    def __init__(self, account='fixture@example.invalid', fail=None):
        self.account, self.fail, self.calls = account, fail, []

    async def request(self, path, params=None):
        self.calls.append((path, params))
        if path == 'profile':
            return {'emailAddress': self.account}
        if path == 'messages':
            if params.get('pageToken'):
                return {'messages': [{'id': 'b'}, {'id': 'c'}]}
            self.assert_scope(params)
            ids = [{'id': 'a'}, {'id': 'b'}][:params['maxResults']]
            return {'messages': ids, 'nextPageToken': 'next'}
        mid = path.split('/')[-1]
        if mid == self.fail:
            raise RuntimeError('synthetic provider failure containing private text')
        return {'id': mid, 'internalDate': '1000', 'labelIds': ['CATEGORY_UPDATES'], 'raw': 'U3ludGhldGljIGVtYWls'}

    def assert_scope(self, params):
        assert params['q'] == 'category:updates'
        assert params['includeSpamTrash'] == 'false'


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SnapshotStore(self.temp.name)
        self.account = 'fixture@example.invalid'

    async def test_all_pages_deduplicate_and_persist_across_restart(self):
        jid = self.store.create_job(self.account, None)
        await download(self.store, FakeGmail(), jid, asyncio.Event())
        restarted = SnapshotStore(self.temp.name)
        summary = restarted.summary(self.account, jid)
        self.assertEqual(summary['stored'], 3)
        self.assertTrue(summary['job']['coverage_complete'])
        self.assertNotIn('raw', json.dumps(summary))
        self.assertEqual(restarted.summary('another@example.invalid')['stored'], 0)

    async def test_sample_never_claims_complete_coverage(self):
        jid = self.store.create_job(self.account, 1)
        await download(self.store, FakeGmail(), jid, asyncio.Event())
        summary = self.store.summary(self.account, jid)
        self.assertEqual(summary['stored'], 1)
        self.assertFalse(summary['job']['coverage_complete'])

    async def test_failure_resume_drains_saved_page_before_advancing(self):
        jid = self.store.create_job(self.account, None)
        await download(self.store, FakeGmail(fail='b'), jid, asyncio.Event())
        self.assertEqual(self.store.summary(self.account, jid)['stored'], 1)
        self.assertEqual(self.store.job(jid)['status'], 'error')
        self.assertEqual(self.store.download_error(jid)['code'], 'unexpected')
        self.assertNotIn('private text', json.dumps(self.store.download_error(jid)))
        gmail = FakeGmail()
        await download(SnapshotStore(self.temp.name), gmail, jid, asyncio.Event())
        self.assertEqual(gmail.calls[1][0], 'messages/b')
        self.assertTrue(self.store.summary(self.account, jid)['job']['coverage_complete'])
        self.assertIsNone(self.store.download_error(jid))

    async def test_deleted_message_does_not_stop_other_downloads(self):
        class DeletedGmail(FakeGmail):
            async def request(self, path, params=None):
                if path == 'messages/b':
                    request = httpx.Request('GET', 'https://gmail.googleapis.com/synthetic')
                    response = httpx.Response(404, request=request)
                    response.raise_for_status()
                return await super().request(path, params)
        jid = self.store.create_job(self.account, None)
        await download(self.store, DeletedGmail(), jid, asyncio.Event())
        summary = self.store.summary(self.account, jid)
        self.assertEqual(summary['stored'], 2)
        self.assertEqual(summary['job']['status'], 'done')
        self.assertEqual(summary['job']['skipped'], 1)
        self.assertEqual(summary['job']['failed'], 0)

    async def test_committed_listing_survives_crash_before_fetch(self):
        jid = self.store.create_job(self.account, 2)
        self.store.page(jid, ['a', 'b'], 'next')
        await download(SnapshotStore(self.temp.name), FakeGmail(), jid, asyncio.Event())
        self.assertEqual(self.store.summary(self.account, jid)['stored'], 2)

    async def test_resume_refuses_different_account(self):
        jid = self.store.create_job(self.account, 10)
        with self.assertRaises(ValueError):
            await download(self.store, FakeGmail(account='other@example.invalid'), jid, asyncio.Event())
        self.assertEqual(self.store.listed(jid), 0)

    async def test_pause_and_reuse(self):
        jid = self.store.create_job(self.account, 1)
        stop = asyncio.Event(); stop.set()
        await download(self.store, FakeGmail(), jid, stop)
        self.assertEqual(self.store.job(jid)['status'], 'paused')
        await download(self.store, FakeGmail(), jid, asyncio.Event())
        again = self.store.create_job(self.account, 1)
        gmail = FakeGmail()
        await download(self.store, gmail, again, asyncio.Event())
        self.assertFalse(any(path.startswith('messages/') for path, _ in gmail.calls))
        self.assertEqual(self.store.summary(self.account, again)['job']['reused'], 1)


class Boundaries(unittest.TestCase):
    def test_launcher_uses_selected_service_folder(self):
        _, args = arguments([])
        self.assertEqual(args.directory, DEFAULT_DIRECTORY)
        self.assertEqual(args.client_config, DEFAULT_DIRECTORY / 'google-client.json')
        _, custom = arguments(['--directory', '/synthetic/private-root'])
        self.assertEqual(custom.client_config, Path('/synthetic/private-root/google-client.json'))

    def test_reject_dotdot_workspace_private_storage(self):
        root = Path(__file__).resolve().parents[1]
        with self.assertRaises(ValueError):
            private_path(str(root.parent / 'synthetic' / '..' / root.name / 'data'))

    def test_oauth_download_and_restart_over_http_with_synthetic_provider(self):
        import httpx
        from urllib.parse import urlparse, parse_qs
        account = 'fixture@example.invalid'
        saved_tokens = {}
        keyring = Mock()
        keyring.get_password.side_effect = lambda service, client: saved_tokens.get(client)
        keyring.set_password.side_effect = lambda service, client, value: saved_tokens.update({client: value})
        requests = []
        def provider(request):
            requests.append(request)
            if request.url.host == 'oauth2.googleapis.com':
                return httpx.Response(200, json={'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh',
                                              'scope': 'https://www.googleapis.com/auth/gmail.modify'})
            self.assertEqual(request.url.host, 'gmail.googleapis.com')
            if request.url.path.endswith('/profile'):
                return httpx.Response(200, json={'emailAddress': account})
            if request.url.path.endswith('/messages'):
                self.assertEqual(request.url.params['q'], 'category:updates')
                self.assertEqual(request.url.params['maxResults'], '10')
                return httpx.Response(200, json={'messages': [{'id': 'fixture' + str(i)} for i in range(10)]})
            self.assertEqual(request.url.params['format'], 'raw')
            return httpx.Response(200, json={'id': request.url.path.rsplit('/', 1)[1], 'internalDate': '1000',
                                            'labelIds': ['CATEGORY_UPDATES'], 'raw': 'U3ludGhldGljIG9ubHk='})
        real_client = httpx.AsyncClient
        def fake_client(*args, **kwargs):
            return real_client(*args, **kwargs, transport=httpx.MockTransport(provider))
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / 'fixture-client.json'
            cfg.write_text(json.dumps({'installed': {'client_id': 'fixture-client', 'client_secret': 'fixture-secret'}}))
            env = {'GMAIL_SNAPSHOT_ISOLATION_VERIFIED': '1', 'GMAIL_SNAPSHOT_CAPABILITY': 'synthetic-capability',
                   'GMAIL_SNAPSHOT_DIRECTORY': directory, 'GMAIL_SNAPSHOT_CLIENT_CONFIG': str(cfg)}
            with patch.dict('os.environ', env), patch('snapshot.private_service.Keyring', return_value=keyring), patch('snapshot.private_service.httpx.AsyncClient', side_effect=fake_client):
                headers = {'x-snapshot-capability': 'synthetic-capability'}
                with TestClient(create_app(), base_url='http://127.0.0.1:9000') as client:
                    auth = client.post('/api/connect', headers=headers)
                    state = parse_qs(urlparse(auth.json()['url']).query)['state'][0]
                    callback = client.get('/oauth/callback', params={'state': state, 'code': 'fixture-code'}, follow_redirects=False)
                    self.assertIn('connection=success', callback.headers['location'])
                    self.assertEqual(client.post('/api/download', headers=headers, json={'category': 'updates', 'limit': 10}).status_code, 200)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        status = client.get('/api/status', headers=headers).json()
                        if status.get('job', {}).get('status') == 'done':
                            break
                        time.sleep(.02)
                    self.assertEqual(status['stored'], 10)
                    self.assertFalse(status['job']['coverage_complete'])
                    for secret in ('fixture-secret', 'synthetic-access', 'synthetic-refresh', account, 'U3ludGhldGljIG9ubHk='):
                        self.assertNotIn(secret, json.dumps(status))
                with TestClient(create_app(), base_url='http://127.0.0.1:9000') as restarted:
                    restarted.post('/api/refresh', headers=headers)
                    self.assertEqual(restarted.get('/api/status', headers=headers).json()['stored'], 10)
                self.assertFalse(any('/trash' in str(r.url) or 'typesafe' in str(r.url) for r in requests))

    def test_private_api_requires_capability_and_oauth_preserves_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / 'fixture-client.json'
            cfg.write_text(json.dumps({'installed': {'client_id': 'synthetic-client', 'client_secret': 'synthetic-secret'}}))
            env = {'GMAIL_SNAPSHOT_ISOLATION_VERIFIED': '1', 'GMAIL_SNAPSHOT_CAPABILITY': 'fixture-capability',
                   'GMAIL_SNAPSHOT_DIRECTORY': directory, 'GMAIL_SNAPSHOT_CLIENT_CONFIG': str(cfg)}
            with patch.dict('os.environ', env), patch('snapshot.private_service.Keyring', return_value=Mock()):
                app = create_app()
            with TestClient(app, base_url='http://127.0.0.1:9000') as client:
                self.assertEqual(client.post('/api/connect').status_code, 403)
                self.assertEqual(client.get('/api/status', headers={'host': 'evil.example'}).status_code, 403)
                headers = {'x-snapshot-capability': 'fixture-capability'}
                response = client.post('/api/connect', headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('synthetic-secret', response.text)
                from urllib.parse import urlparse, parse_qs
                query = parse_qs(urlparse(response.json()['url']).query)
                self.assertEqual(query['scope'], ['https://www.googleapis.com/auth/gmail.modify'])
                self.assertEqual(query['code_challenge_method'], ['S256'])
                self.assertEqual(client.get('/api/messages', headers=headers).status_code, 404)
                result = client.get('/oauth/callback?state=wrong&code=synthetic', follow_redirects=False)
                self.assertIn('connection=failed', result.headers['location'])

    def test_private_service_is_disabled_without_isolation_attestation(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(RuntimeError):
                create_app()

    def test_reject_workspace_private_storage(self):
        with self.assertRaises(ValueError):
            private_path(str(Path(__file__).resolve().parents[1] / 'private-fixture'))

    def test_limit_and_extra_fields(self):
        for value in (0, -1, 100001, True, '10'):
            with self.assertRaises(ValueError):
                DownloadOptions(limit=value)
        with self.assertRaises(ValueError):
            DownloadOptions(token='secret')

    def test_public_ui_has_session_origin_and_no_content_endpoints(self):
        with patch.dict('os.environ', {'GMAIL_SNAPSHOT_CAPABILITY': ''}):
            app = create_ui()
        with TestClient(app, base_url='http://127.0.0.1:8766') as client:
            self.assertEqual(client.get('/api/snapshot/status').status_code, 403)
            self.assertEqual(client.get('/').status_code, 200)
            self.assertEqual(client.post('/api/snapshot/download').status_code, 403)
            self.assertEqual(client.get('/api/snapshot/status', headers={'host': 'evil.example'}).status_code, 403)
            self.assertEqual(client.get('/api/snapshot/messages').status_code, 404)
            self.assertEqual(client.get('/api/snapshot/status').status_code, 503)


if __name__ == '__main__':
    unittest.main()
