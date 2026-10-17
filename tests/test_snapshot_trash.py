"""Synthetic Gmail trash actions. Never permanently delete mail."""
import asyncio
import unittest

import httpx

from snapshot.trash import trash_messages


class FakeGmail:
    def __init__(self, fail=False):
        self.messages = {'a': ['INBOX', 'CATEGORY_UPDATES'], 'b': ['TRASH'], 'c': ['DRAFT']}
        self.posts = []
        self.fail = fail

    async def request(self, path, params=None):
        mid = path.split('/')[-1]
        if mid not in self.messages:
            request = httpx.Request('GET', 'https://gmail.googleapis.com/synthetic')
            httpx.Response(404, request=request).raise_for_status()
        return {'id': mid, 'labelIds': self.messages[mid]}

    async def post(self, path, payload):
        assert path.endswith('/trash')
        assert payload is None
        self.posts.append(path)
        mid = path.split('/')[-2]
        self.messages[mid] = ['TRASH']
        if self.fail:
            self.fail = False
            raise RuntimeError('PRIVATE provider response, lost after write')
        return {'id': mid, 'labelIds': ['TRASH']}


class TrashTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_messages_only_and_counts_for_already_missing_draft(self):
        gmail = FakeGmail()
        updates = []
        result = await trash_messages(gmail, ['a', 'b', 'c', 'missing', 'a'], updates.append)
        self.assertEqual(result, {'status': 'done', 'moved': 1, 'already': 1, 'skipped': 2, 'failed': 0})
        self.assertEqual(gmail.posts, ['messages/a/trash'])
        self.assertEqual(updates[-1], result)

    async def test_retry_recovers_lost_write_response(self):
        gmail = FakeGmail(fail=True)
        first = await trash_messages(gmail, ['a'], lambda summary: None)
        self.assertEqual(first['status'], 'error')
        self.assertEqual(first['failed'], 1)
        second = await trash_messages(gmail, ['a'], lambda summary: None)
        self.assertEqual(second['already'], 1)
        self.assertEqual(second['status'], 'done')
        self.assertEqual(len(gmail.posts), 1)

    async def test_large_group_runs_in_bounded_chunks(self):
        gmail = FakeGmail()
        gmail.messages = {str(i): ['INBOX'] for i in range(100)}
        updates = []
        result = await trash_messages(gmail, list(gmail.messages), updates.append)
        self.assertEqual(result['moved'], 100)
        self.assertEqual([value['moved'] for value in updates], [0, 25, 50, 75, 100, 100])
        self.assertTrue(all(path.endswith('/trash') for path in gmail.posts))


class BulkTrashTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_message_does_not_block_eligible_but_never_gets_trashed(self):
        from snapshot.trash import bulk_trash_messages
        class Gmail:
            posts = []
            async def request(self, path, params=None):
                mid = path.split('/')[-1]
                if mid == 'bad':
                    httpx.Response(400, request=httpx.Request('GET', 'https://gmail.googleapis.com/private-id'), json={'error': {'message': 'PRIVATE'}}).raise_for_status()
                return {'id': mid, 'labelIds': []}
            async def post(self, path, payload):
                self.posts.append(payload)
        gmail = Gmail()
        result = await bulk_trash_messages(gmail, ['bad', 'good'], lambda v: None)
        self.assertEqual((result['status'], result['moved'], result['failed']), ('error', 1, 1))
        self.assertEqual(result['errors'], {'gmail_request:400': 1})
        self.assertEqual(gmail.posts[0]['ids'], ['good'])
        self.assertNotIn('PRIVATE', str(result))

    async def test_authorization_failure_stops_before_mutation(self):
        from snapshot.trash import bulk_trash_messages
        class Gmail:
            async def request(self, path, params=None):
                httpx.Response(401, request=httpx.Request('GET', 'https://gmail.googleapis.com/private-id')).raise_for_status()
            async def post(self, path, payload):
                raise AssertionError('Must not write')
        result = await bulk_trash_messages(Gmail(), ['one'], lambda v: None)
        self.assertEqual(result['errors'], {'authorization:401': 1})
        self.assertEqual(result['moved'], 0)

    async def test_retry_after_unknown_bulk_write_rechecks_gmail(self):
        from snapshot.trash import bulk_trash_messages
        class Gmail:
            changed = False
            writes = 0
            async def request(self, path, params=None):
                return {'id': path.split('/')[-1], 'labelIds': ['TRASH'] if self.changed else []}
            async def post(self, path, payload):
                self.changed = True
                self.writes += 1
                raise httpx.ReadTimeout('PRIVATE')
        gmail = Gmail()
        first = await bulk_trash_messages(gmail, ['one'], lambda v: None)
        self.assertEqual(first['errors'], {'network': 1})
        second = await bulk_trash_messages(gmail, ['one'], lambda v: None)
        self.assertEqual((second['status'], second['already'], gmail.writes), ('done', 1, 1))
