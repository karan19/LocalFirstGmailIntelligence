"""Synthetic Gmail label writes only."""
import asyncio
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from snapshot.ui_server import create_app

from snapshot.labels import move_to_later


class FakeGmail:
    def __init__(self, labels=None, failure=False):
        self.labels = labels or []
        self.messages = {'updates': ['CATEGORY_UPDATES', 'INBOX', 'STARRED'],
                         'trash': ['CATEGORY_UPDATES', 'TRASH'], 'other': ['INBOX'],
                         'already': ['Later-id', 'INBOX']}
        self.posts = []
        self.failure = failure

    async def request(self, path, params=None):
        if path == 'labels':
            return {'labels': self.labels}
        mid = path.split('/')[-1]
        return {'id': mid, 'labelIds': self.messages[mid]}

    async def post(self, path, payload, **kwargs):
        self.posts.append((path, payload))
        if path == 'labels':
            self.labels.append({'id': 'Later-id', 'type': 'user', 'name': 'Later'})
            return self.labels[-1]
        for mid in payload['ids']:
            self.messages[mid] = [label for label in self.messages[mid] if label != 'CATEGORY_UPDATES'] + ['Later-id']
        if self.failure:
            self.failure = False
            raise RuntimeError('Lost response after successful mutation')
        return {}


class LabelTests(unittest.TestCase):
    def test_existing_label_reused_and_only_updates_moved(self):
        gmail = FakeGmail([{'id': 'Later-id', 'type': 'user', 'name': 'Later'}])
        progress = []
        result = asyncio.run(move_to_later(gmail, ['updates', 'trash', 'other', 'already', 'updates'], progress.append))
        self.assertEqual(result, {'status': 'done', 'moved': 1, 'already': 1, 'skipped': 2})
        self.assertEqual(gmail.posts, [('messages/batchModify', {'ids': ['updates'], 'addLabelIds': ['Later-id'], 'removeLabelIds': ['CATEGORY_UPDATES']})])
        self.assertEqual(set(gmail.messages['updates']), {'INBOX', 'STARRED', 'Later-id'})
        self.assertEqual(progress[-1], result)

    def test_creates_visible_label_and_retry_recovers_unknown_outcome(self):
        gmail = FakeGmail(failure=True)
        first = asyncio.run(move_to_later(gmail, ['updates'], lambda summary: None))
        self.assertEqual(first['status'], 'error')
        self.assertEqual(gmail.posts[0], ('labels', {'name': 'Later', 'labelListVisibility': 'labelShow', 'messageListVisibility': 'show'}))
        second = asyncio.run(move_to_later(gmail, ['updates'], lambda summary: None))
        self.assertEqual(second, {'status': 'done', 'moved': 0, 'already': 1, 'skipped': 0})
        self.assertEqual(len(gmail.posts), 2)

    def test_ui_proxy_requires_origin_and_filters_label_result(self):
        calls = []
        real_client = httpx.AsyncClient
        def provider(request):
            calls.append(request)
            return httpx.Response(200, json={'later': {'status': 'done', 'moved': 3, 'already': 0, 'skipped': 0,
                                                       'ids': ['private-id'], 'account': 'private-account'}})
        def fake_client(*args, **kwargs):
            return real_client(*args, **kwargs, transport=httpx.MockTransport(provider))
        with patch.dict('os.environ', {'GMAIL_SNAPSHOT_CAPABILITY': 'synthetic-capability'}), patch('snapshot.ui_server.httpx.AsyncClient', side_effect=fake_client):
            with TestClient(create_app(), base_url='http://127.0.0.1:8766') as client:
                client.get('/')
                payload = {'run_id': 'a' * 32, 'topic': 0}
                self.assertEqual(client.post('/api/snapshot/later', json=payload).status_code, 403)
                self.assertEqual(calls, [])
                response = client.post('/api/snapshot/later', json=payload, headers={'Origin': 'http://127.0.0.1:8766'})
                self.assertEqual(response.json(), {'later': {'status': 'done', 'moved': 3, 'already': 0, 'skipped': 0}})
                self.assertEqual(calls[0].url.path, '/api/later')
                self.assertEqual(calls[0].headers['x-snapshot-capability'], 'synthetic-capability')

    def test_does_not_create_unused_label(self):
        gmail = FakeGmail()
        result = asyncio.run(move_to_later(gmail, ['trash', 'other'], lambda summary: None))
        self.assertEqual(result['skipped'], 2)
        self.assertEqual(gmail.posts, [])


if __name__ == '__main__':
    unittest.main()
