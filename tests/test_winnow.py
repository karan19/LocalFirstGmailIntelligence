import unittest
from unittest.mock import Mock, patch, AsyncMock
import httpx
from app.winnow import WinnowClassifier, WINNOW_INSTRUCTIONS, cleanup_decision
from app.services import UserError
from app.cleanup import ScanOptions, Selection
import tests.test_v2 as v2

class AdapterTests(unittest.TestCase):
    def test_local_typed_contract(self):
        response = Mock()
        response.json.return_value = {'answers': {'category': {'probabilities': {'KEEP':.9,'CLEANUP':.1},'confidence':.6}, 'basis': {'choice':'current_importance'}}}
        with patch('app.winnow.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value = response
            result = WinnowClassifier().classify({'sender':'billing','subject':'Payment due','text':'Please pay','token':'private'})
            args, kwargs = client.return_value.__enter__.return_value.post.call_args
            self.assertEqual(args[0], 'http://127.0.0.1:8091/v1/systemone')
            self.assertNotIn('token', kwargs['json']['state'])
            self.assertEqual(kwargs['json']['questions']['category']['instructions'], WINNOW_INSTRUCTIONS)
            self.assertEqual(set(kwargs['json']['questions']), {'category'})
            self.assertEqual(set(kwargs['json']['questions']['category']['criteria']), {'KEEP','CLEANUP'})
            self.assertEqual(result['decision'],'KEEP')
            self.assertEqual(result['confidence'],60)

    def test_cleanup_threshold_boundaries(self):
        for p, expected in ((.19,'KEEP'),(.2,'NEEDS_REVIEW'),(.6,'NEEDS_REVIEW'),(.61,'CLEANUP'),(.74,'CLEANUP')):
            self.assertEqual(cleanup_decision(p),expected)

    def test_fail_closed_on_unavailable_runtime(self):
        with patch('app.winnow.httpx.Client', side_effect=httpx.ConnectError('offline')):
            with self.assertRaises(UserError):
                WinnowClassifier().classify({'sender':'a','subject':'b','text':'c'})

class DualFlowTests(unittest.IsolatedAsyncioTestCase):
    setUp = v2.CleanupTests.setUp

    async def test_manual_comparison_disagreement_and_qwen(self):
        self.cleanup.options = ScanOptions(limit=1, classifier='both', qwen_mode='manual')
        self.cleanup.winnow = Mock()
        self.cleanup.winnow.classify.return_value = {'decision':'KEEP','category':'KEEP','reason':'Unresolved action','confidence':80,'probabilities':{'KEEP':.9,'CLEANUP':.05,'NEEDS_REVIEW':.05}}
        self.gmail.request.side_effect = [{'emailAddress':'test@example.com'}, {'messages':[{'id':'1'}]}]
        self.cleanup.run.update(id='run',status='running')
        with patch('app.cleanup.review_qwen', new_callable=AsyncMock) as qwen:
            await self.cleanup.scan(self.gmail)
            qwen.assert_not_awaited()
            message = self.cleanup.messages['1']
            self.assertEqual(message['bucket'],'review')
            self.assertEqual(set(message['classifiers']), {'laya','winnow'})
            self.assertEqual((await self.cleanup.comparison('run'))['disagreements'],1)
            qwen.return_value={'decision':'CLEANUP','confidence':90,'reason':'Transient'}
            await self.cleanup.forward_qwen('run',Selection(ids=['1']))
            self.assertEqual(message['bucket'],'ready')
            self.assertEqual(message['classifiers']['winnow']['decision'],'KEEP')
            self.assertTrue(all(c.args[0]=='GET' for c in self.gmail.request.await_args_list))

    async def test_manual_qwen_rejects_protected_and_duplicates(self):
        self.cleanup.run.update(id='run',status='done')
        self.cleanup.messages={'1':{'id':'1','bucket':'keep','protected':['STARRED'],'trash_status':None}}
        with patch('app.cleanup.review_qwen', new_callable=AsyncMock) as qwen:
            with self.assertRaises(UserError):await self.cleanup.forward_qwen('run',Selection(ids=['1']))
            with self.assertRaises(UserError):await self.cleanup.forward_qwen('run',Selection(ids=['1','1']))
            qwen.assert_not_awaited()

class IndependentBranchTests(unittest.IsolatedAsyncioTestCase):
    setUp = v2.CleanupTests.setUp

    async def test_fetch_once_then_independent_classifier_and_qwen_results(self):
        self.cleanup.options = ScanOptions(limit=1, fetch_only=True, qwen_mode='manual')
        self.gmail.request.side_effect = [{'emailAddress':'test@example.com'}, {'messages':[{'id':'1'}]}]
        self.cleanup.run.update(id='run',status='running')
        await self.cleanup.scan(self.gmail)
        self.classifier.classify.assert_not_called()
        self.assertEqual(self.cleanup.run['status'],'done')
        self.assertEqual(len(self.cleanup.fetched_snapshot),1)
        fetches = self.gmail.message.await_count
        self.classifier.classify.return_value = {'decision':'KEEP','probabilities':{'KEEP':.9,'CLEANUP':.05,'NEEDS_REVIEW':.05}}
        await self.cleanup.start_branch('run','laya')
        await self.cleanup.task
        self.assertEqual(self.cleanup.branches['laya']['1']['bucket'],'keep')
        self.cleanup.winnow = Mock()
        self.cleanup.winnow.ready.return_value = True
        self.cleanup.winnow.classify.return_value = {'decision':'CLEANUP','probabilities':{'KEEP':.05,'CLEANUP':.9,'NEEDS_REVIEW':.05}}
        await self.cleanup.start_branch('run','winnow')
        await self.cleanup.task
        self.assertEqual(self.gmail.message.await_count,fetches)
        self.assertEqual(self.cleanup.branches['winnow']['1']['bucket'],'ready')
        with patch('app.cleanup.review_qwen',new_callable=AsyncMock) as qwen:
            qwen.return_value={'decision':'NEEDS_REVIEW','confidence':60,'reason':'Unclear'}
            await self.cleanup.branch_qwen('run','laya',Selection(ids=['1']))
        self.assertEqual(self.cleanup.branches['laya']['1']['bucket'],'review')
        self.assertEqual(self.cleanup.branches['winnow']['1']['bucket'],'ready')
        self.assertNotIn('qwen',self.cleanup.branches['winnow']['1'])
        self.assertNotIn('qwen',self.cleanup.fetched_snapshot['1'])
        self.assertEqual((await self.cleanup.comparison('run'))['disagreements'],1)
        with self.assertRaises(UserError):await self.cleanup.start_branch('run','laya')

    async def test_branch_mailbox_trash_state_is_shared(self):
        self.cleanup.run.update(id='run',status='done')
        self.cleanup.branches={'laya':{'1':{'bucket':'ready','protected':[],'trash_status':'trashed'}},'winnow':{'1':{'bucket':'ready','protected':[],'trash_status':None}}}
        self.cleanup.messages=self.cleanup.branches['laya']
        await self.cleanup.progress('run')
        await self.cleanup.select_branch('run','winnow')
        self.assertEqual(self.cleanup.messages['1']['trash_status'],'trashed')
        with self.assertRaises(UserError):self.cleanup.selected(['1'])

class WinnowThresholdFlowTests(unittest.IsolatedAsyncioTestCase):
    setUp = v2.CleanupTests.setUp

    async def test_only_intermediate_band_runs_qwen_automatically(self):
        self.cleanup.run.update(id='run',status='done',account='test@example.com',fetched=4)
        self.cleanup.fetched_snapshot={str(i):{'id':str(i),'sender':'sender@example.com','subject':'message','text':'body','received':i,'bucket':'not_analyzed','protected':['STARRED'] if i==4 else [],'trash_status':None} for i in range(1,5)}
        self.cleanup.winnow = Mock()
        self.cleanup.winnow.ready.return_value=True
        self.cleanup.winnow.classify.side_effect=[{'decision':cleanup_decision(p),'probabilities':{'KEEP':1-p,'CLEANUP':p}} for p in (.1,.74,.4,.4)]
        with patch('app.cleanup.review_qwen',new_callable=AsyncMock) as qwen:
            qwen.return_value={'decision':'KEEP','confidence':90,'reason':'Important record'}
            await self.cleanup.start_branch('run','winnow')
            await self.cleanup.task
            qwen.assert_awaited_once()
            self.assertEqual(qwen.call_args.args[0]['id'],'3')
        messages=self.cleanup.branches['winnow']
        self.assertEqual(messages['1']['bucket'],'keep')
        self.assertEqual(messages['2']['bucket'],'ready')
        self.assertEqual(messages['3']['bucket'],'keep')
        self.assertEqual(messages['4']['source'],'protected')
        self.assertEqual(self.cleanup.run['qwen_completed'],1)
        self.gmail.request.assert_not_awaited()
