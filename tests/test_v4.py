import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from tests import test_v2
from tests.test_v2 import distribution, raw
from app.intelligence import Intelligence
from app.cleanup import Decision


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'reviews.sqlite3'
        self.memory = Intelligence(self.path)
        self.account = 'one@example.com'
        self.example = {'id': 'prior', 'sender': 'Editor <editor@weekly.com>', 'subject': 'Weekly research digest', 'text': 'Research papers and scientific discoveries https://weekly.com/unsubscribe?secret=token', 'list_id': 'research.weekly.com'}

    def test_consent_restart_account_dedup_and_clear(self):
        self.memory.capture(self.account, self.example, 'keep')
        self.assertEqual(self.memory.learning(self.account), {'enabled': False, 'count': 0})
        self.memory.set_learning(self.account, True)
        self.memory.capture(self.account, self.example, 'keep')
        self.memory.capture(self.account, self.example, 'promotional')
        reopened = Intelligence(self.path)
        self.assertEqual(reopened.learning(self.account), {'enabled': True, 'count': 1})
        self.assertFalse(reopened.learning('other@example.com')['enabled'])
        self.assertEqual(reopened.retrieve(self.account, self.example)['status'], 'none')
        future = {**self.example, 'id': 'future'}
        self.assertEqual(reopened.retrieve(self.account, future)['status'], 'relevant')
        self.assertEqual(reopened.retrieve('other@example.com', future)['status'], 'none')
        event = reopened.event(self.account, 'trash', 'succeeded', 'message')
        reopened.set_learning(self.account, False)
        self.assertEqual(reopened.retrieve(self.account, future)['status'], 'none')
        reopened.capture(self.account, future, 'keep')
        self.assertEqual(reopened.learning(self.account)['count'], 1)
        reopened.clear_learning(self.account)
        self.assertEqual(reopened.actions(self.account)['items'][0]['id'], event)
        self.assertNotIn(b'secret=token', self.path.read_bytes())

    def test_sender_alone_is_not_rule_conflict_and_weak(self):
        self.memory.set_learning(self.account, True)
        self.memory.capture(self.account, self.example, 'keep')
        different = {**self.example, 'id': 'later', 'subject': 'Account password reset', 'text': 'Security verification login request'}
        self.assertEqual(self.memory.retrieve(self.account, different)['status'], 'none')
        self.memory.capture(self.account, {**self.example, 'id': 'second'}, 'promotional')
        self.assertEqual(self.memory.retrieve(self.account, {**self.example, 'id': 'later'})['status'], 'conflict')
        self.memory.clear_learning(self.account)
        self.memory.capture(self.account, {**self.example, 'subject': 'Research digest useful information', 'text': ''}, 'keep')
        self.assertEqual(self.memory.retrieve(self.account, {**self.example, 'id': 'later', 'subject': 'Research digest new update', 'text': ''})['status'], 'weak')

    def test_metrics_unique_latest_action_cycles_restart_and_coverage(self):
        run = {'id': 'r1', 'account': self.account, 'status': 'done'}
        messages = [{'id': 'a', 'bucket': 'keep'}, {'id': 'b', 'bucket': 'ready'}, {'id': 'c', 'bucket': 'not_analyzed'}]
        self.memory.record_run(run, 'classification', messages)
        self.memory.record_run(run, 'classification', messages)
        self.memory.record_run({**run, 'id': 'r2', 'status': 'stopped'}, 'classification', messages)
        self.memory.record_run({**run, 'id': 'r3'}, 'discovery')
        self.memory.record_run({**run, 'id': 'r4'}, 'backlog')
        self.memory.record_messages(self.account, [{'id': 'a', 'bucket': 'ready'}])
        event = self.memory.event(self.account, 'trash', 'pending', 'a')
        self.memory.finish(event, 'succeeded'); self.memory.finish(event, 'succeeded')
        self.memory.event(self.account, 'restore', 'succeeded', 'a')
        self.memory.event(self.account, 'trash', 'succeeded', 'a')
        self.memory.event(self.account, 'unsubscribe', 'request_accepted')
        data = Intelligence(self.path).dashboard(self.account)
        self.assertEqual([data[k] for k in ('total_runs', 'stopped_runs', 'emails_reviewed', 'kept', 'trashed', 'restored', 'subscription_scans', 'backlog_scans', 'unsubscribe_accepted', 'unsubscribe_verified')], [1, 1, 2, 0, 2, 1, 1, 1, 1, 0])
        self.assertEqual(self.memory.dashboard('other@example.com')['emails_reviewed'], 0)
        self.assertIn('earlier ephemeral', data['coverage'])
        self.memory.mark(self.account, [('legacy', 'keep')])
        self.assertEqual(self.memory.dashboard(self.account)['emails_reviewed'], 3)


class LearningIntegrationTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_v2.CleanupTests.setUp
    ready = test_v2.CleanupTests.ready
    async def scan_email(self, protected=False):
        email = raw(2, ['INBOX', 'STARRED'] if protected else ['INBOX'])
        email['payload']['headers'] = [{'name': 'From', 'value': 'editor@weekly.com'}, {'name': 'Subject', 'value': 'Weekly research digest'}]
        email['snippet'] = 'Research papers and scientific discoveries'
        self.gmail.message = AsyncMock(return_value=email)
        self.gmail.request.side_effect = [{'emailAddress': 'test@example.com'}, {'messages': [{'id': '2'}]}]
        self.cleanup.run.update(id='run', status='running')
        await self.cleanup.scan(self.gmail)
        return self.cleanup.messages['2']

    async def seed(self):
        self.ready(('1',))
        self.cleanup.messages['1'].update(sender='editor@weekly.com', subject='Weekly research digest', text='Research papers and scientific discoveries')
        self.cleanup.intelligence.set_learning('test@example.com', True)
        await self.cleanup.decide('run', '1', Decision(decision='keep'))

    async def test_memory_triggers_high_laya_qwen_without_probabilities_change(self):
        await self.seed()
        with patch('app.cleanup.review_qwen', AsyncMock(return_value={'decision': 'not_promotional', 'confidence': 'high', 'reason': 'Similar research digest was explicitly kept.'})) as qwen:
            message = await self.scan_email()
        self.assertEqual(message['bucket'], 'keep')
        self.assertEqual(message['source'], 'memory_qwen')
        self.assertEqual(message['probabilities'], distribution(.9))
        self.assertEqual(len(qwen.call_args.args), 2)
        response = await self.cleanup.results('run', bucket='keep')
        self.assertNotIn('text', response['items'][0]); self.assertNotIn('_memory_context', response['items'][0])
        self.assertEqual(self.cleanup.intelligence.dashboard('test@example.com')['total_runs'], 1)

    async def test_memory_failure_and_protection(self):
        await self.seed()
        with patch('app.cleanup.review_qwen', AsyncMock(side_effect=ValueError('model failure'))):
            message = await self.scan_email()
        self.assertEqual((message['bucket'], message['source']), ('review', 'memory_error'))
        with patch('app.cleanup.review_qwen', AsyncMock()) as qwen:
            message = await self.scan_email(protected=True)
        qwen.assert_not_called(); self.assertEqual(message['source'], 'protected')

    async def test_disabled_memory_preserves_v2_path(self):
        await self.seed()
        self.cleanup.intelligence.set_learning('test@example.com', False)
        with patch('app.cleanup.review_qwen', AsyncMock()) as qwen:
            message = await self.scan_email()
        qwen.assert_not_called()
        self.assertEqual(message['source'], 'laya_high_promo')

class EndpointTests(unittest.TestCase):
    def test_account_binding_confirmation_and_boundary(self):
        from fastapi.testclient import TestClient
        from app import server
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def connection():
            yield None, 'current@example.com'
        with tempfile.TemporaryDirectory() as directory:
            history = Intelligence(Path(directory)/'reviews.sqlite3')
            with patch.object(server.cleanup_service, 'history', history), patch.object(server.subscription_service, 'connection', connection):
                with TestClient(server.app, base_url=server.ORIGIN) as client:
                    client.get('/dashboard')
                    headers = {'Origin': server.ORIGIN}
                    self.assertEqual(client.get('/api/v4/learning').json()['enabled'], False)
                    self.assertEqual(client.post('/api/v4/learning', json={'enabled': True, 'account': 'different@example.com'}, headers=headers).status_code, 400)
                    self.assertEqual(client.post('/api/v4/learning', json={'enabled': True, 'account': 'current@example.com'}, headers=headers).status_code, 200)
                    self.assertEqual(client.post('/api/v4/learning/erase', json={'confirmed': False, 'account': 'current@example.com'}, headers=headers).status_code, 400)
                    self.assertEqual(client.post('/api/v4/learning/erase', json={'confirmed': True, 'account': 'different@example.com'}, headers=headers).status_code, 400)
                    self.assertEqual(client.post('/api/v4/learning/erase', json={'confirmed': True, 'account': 'current@example.com'}, headers=headers).status_code, 200)
                    self.assertTrue(client.get('/api/v4/learning').json()['enabled'])
                    self.assertEqual(client.post('/api/v4/learning', json={'enabled': False, 'account': 'current@example.com'}).status_code, 403)

    def test_backlog_actions_do_not_create_analyzed_results(self):
        with tempfile.TemporaryDirectory() as directory:
            history = Intelligence(Path(directory)/'reviews.sqlite3')
            account = 'current@example.com'
            history.event(account, 'trash', 'succeeded', 'metadata-only', identity='newsletter')
            history.mark(account, [('metadata-only', 'trashed')])
            history.action_state(account, 'metadata-only', 'trashed')
            self.assertEqual(history.dashboard(account)['emails_reviewed'], 0)
            history.record_messages(account, [{'id': 'classified', 'bucket': 'keep'}])
            history.mark(account, [('classified', 'promotional')])
            self.assertEqual(history.dashboard(account)['kept'], 0)

    def test_learning_prompt_uses_preferences_schema_and_strips_links(self):
        import asyncio
        import httpx
        from app.cleanup import review_qwen
        captured = []
        def handler(request):
            import json
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={'message': {'content': '{"decision":"keep","confidence":"high","reason":"Matches a prior explicit Keep preference."}'}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        message = {'sender': 'editor@weekly.com', 'subject': 'Research digest', 'text': 'Research https://weekly.com/unsubscribe?secret=token', 'probabilities': distribution(.99)}
        with patch('app.cleanup.httpx.AsyncClient', return_value=client):
            result = asyncio.run(review_qwen(message, [{'sender': 'editor@weekly.com', 'subject': 'Research digest', 'text': 'Research', 'decision': 'keep'}]))
        self.assertEqual(result['decision'], 'not_promotional')
        self.assertEqual(captured[0]['format']['properties']['decision']['enum'], ['keep', 'trash_candidate', 'unsure'])
        self.assertIn('untrusted', captured[0]['messages'][0]['content'])
        self.assertNotIn('secret=token', captured[0]['messages'][1]['content'])
