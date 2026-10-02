"""Additional modes discover structure without new classifier inference or Gmail writes."""
import unittest,tempfile
from pathlib import Path
from unittest.mock import Mock
import numpy as np
from tests import test_v5 as fixtures
raw=fixtures.raw
from app.explorer_index import extract
from app.explorer_graph import build_clusters
from app.explorer import ModeOptions
from app.explorer_views import attention,retention
from app.intelligence import Intelligence

class AdditionalModesTests(unittest.TestCase):
    def corpus(self):
        records=[];vectors=[]
        for family,(subject,body) in enumerate([('Ride receipt','Completed journey cost and receipt'),('Monthly statement','Financial monthly statement account record'),('A personal conversation','Discussing plans with a person')]):
            for i in range(3):
                r,_,_=extract(raw(f'{family}-{i}',sender=f'person{i}@source{i}.com',subject=subject,body=body))
                r.update(list_id='',automated=family!=2,reply_header=family==2,recipients=['me@example.com'])
                records.append(r);v=np.zeros(8);v[family]=1;vectors.append(v)
        return records,vectors
    def test_purpose_discovers_cross_sender_groups_without_taxonomy(self):
        records,vectors=self.corpus();clusters,outliers,g=build_clusters(records,vectors,'purpose')
        self.assertEqual(len(clusters),3);self.assertEqual(outliers,[])
        self.assertTrue(all(len(c['senders'])==3 for c in clusters))
        self.assertTrue(all(c['name'].startswith('Shared purpose') for c in clusters))
        self.assertLessEqual(g['edges'],g['nodes']*g['candidate_limit'])
    def test_attention_keeps_conversation_and_machine_signals_separate(self):
        records,vectors=self.corpus();clusters,outliers,g=build_clusters(records,vectors,'attention')
        self.assertFalse(outliers)
        for c in clusters:
            values={attention(records[int(mid[0])*3+int(mid[-1])]) for mid in c['message_ids']}
            self.assertEqual(len(values),1)
        r=records[-1];r.update(reply_header=False,recipients=[])
        self.assertEqual(attention(r),'unknown')
    def test_retention_cues_and_unknown_outliers_are_provisional(self):
        records,vectors=self.corpus();clusters,outliers,g=build_clusters(records,vectors,'retention')
        self.assertEqual({c['name'] for c in clusters},{'Likely transient','Possible durable records'})
        self.assertEqual(len(outliers),3)
        self.assertTrue(all(any('not verified' in reason for reason in c['explanation']) for c in clusters))
        for r in records[6:]:r['_prior_bucket']='keep'
        clusters,outliers,_=build_clusters(records,vectors,'retention')
        self.assertIn('Prior Keep signals',{c['name'] for c in clusters})
        self.assertFalse(outliers)
        self.assertTrue(all('_prior_bucket' not in r for c in clusters for r in c['examples']))
    def test_conflicting_content_cues_do_not_claim_action_resolved(self):
        r,_,_=extract(raw('x',subject='Payment overdue: monthly statement'))
        self.assertEqual(retention(r),'action')
        r['subject']='Your American Express one-time verification code';self.assertEqual(retention(r),'transient')
    def test_prior_features_are_account_scoped_and_contain_no_body_text(self):
        with tempfile.TemporaryDirectory() as directory:
            intel=Intelligence(Path(directory)/'index.sqlite3')
            intel.record_messages('a@example.com',[{'id':'one','bucket':'keep'}])
            intel.record_messages('a@example.com',[{'id':'not-selected','bucket':'ready'}])
            self.assertEqual(intel.explorer_results('a@example.com',['one']),{'one':'keep'})
            self.assertEqual(intel.explorer_results('other@example.com',['one']),{})

class AdditionalModeFlowTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.ExplorerFlowTests.setUp
    asyncSetUp=fixtures.ExplorerFlowTests.asyncSetUp
    async def test_each_view_reuses_index_without_gmail_or_models(self):
        from app.explorer import ExploreOptions
        await self.explorer.start(ExploreOptions(limit=4));await self.explorer.task
        rid=self.explorer.state['report_id'];calls=self.api_gmail.message.await_count
        self.embedder.embed.reset_mock();self.cleanup.classifier.classify.reset_mock()
        for mode in ('purpose','attention','retention'):
            d=await self.explorer.switch_mode(rid,ModeOptions(mode=mode))
            self.assertEqual(d['mode'],mode);self.assertTrue(d['view_note']);self.assertEqual(d['count'],4)
        self.assertEqual(calls,self.api_gmail.message.await_count)
        self.embedder.embed.assert_not_called();self.cleanup.classifier.classify.assert_not_called()
    async def test_retention_cache_changes_when_prior_results_change(self):
        from app.explorer import ExploreOptions
        await self.explorer.start(ExploreOptions(limit=4));await self.explorer.task
        rid=self.explorer.state['report_id']
        first=await self.explorer.switch_mode(rid,ModeOptions(mode='retention'))
        self.cleanup.intelligence.record_messages(self.account,[{'id':'1','bucket':'keep'}])
        second=await self.explorer.switch_mode(rid,ModeOptions(mode='retention'))
        self.assertNotEqual(first['id'],second['id'])
