import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import Mock,AsyncMock,patch
import numpy as np
from fastapi import FastAPI
from app.explorer import Explorer,ExploreOptions,HandoffOptions,ModeOptions
from app.explorer_index import MailboxIndex,extract,subject_pattern
from app.explorer_graph import build_clusters
from app.services import UserError
from app.cleanup import Selection,ConfirmedSelection
import tests.test_v2 as v2

def raw(mid,sender='notices@news.example.com',subject='Receipt 123',body='A routine purchase receipt. Thank you.',labels=None):
    return {'id':mid,'threadId':'thread-'+mid,'internalDate':'1788000000000','labelIds':labels or ['INBOX'],
            'payload':{'mimeType':'text/plain','headers':[{'name':'From','value':sender},{'name':'Subject','value':subject},{'name':'List-ID','value':'news.example.com'}],
                       'body':{'data':base64.urlsafe_b64encode(body.encode()).decode()}}}

class IndexGraphTests(unittest.TestCase):
    def test_no_bodies_and_account_separation_and_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            index=MailboxIndex(Path(d)/'private/index.sqlite3')
            record,text,digest=extract(raw('one',body='A full confidential sentence never persisted verbatim.'))
            self.assertIn('confidential',text)
            index.save('a@example.com',record,digest,np.ones(8))
            self.assertEqual(index.records('b@example.com',['one']),[])
            self.assertIsNone(index.existing('a@example.com','one','wrong-digest'))
            self.assertEqual(len(index.existing('A@EXAMPLE.COM','one',digest)),8)
            with index.database() as db:
                persisted=db.execute('SELECT data FROM explorer_messages').fetchone()[0]
                self.assertNotIn('A full confidential sentence never persisted verbatim.',persisted)
                self.assertNotIn('body',json.loads(persisted))
            self.assertEqual(index.path.stat().st_mode&0o777,0o600)
            self.assertEqual(index.path.parent.stat().st_mode&0o777,0o700)
            with self.assertRaises(ValueError):index.save('a',record|{'text':text},digest,np.ones(8))
    def test_source_and_repetition_differ_and_outliers_are_not_forced(self):
        records=[];vectors=[]
        for domain in ('alpha.com','beta.com'):
            for family,subject in enumerate(('Ride receipt 101','Monthly statement 101')):
                for i in range(5):
                    r,_,_=extract(raw(f'{domain}-{family}-{i}',sender='notices@'+domain,subject=subject.replace('101',str(i+1))))
                    records.append(r);v=np.zeros(8);v[family]=1;vectors.append(v)
        r,_,_=extract(raw('human',sender='person@gmail.com',subject='A unique personal note',labels=['INBOX']))
        r['automated']=False;records.append(r);v=np.zeros(8);v[6]=1;vectors.append(v)
        source,outs,g=build_clusters(records,vectors,'source');repetition,outs2,g2=build_clusters(records,vectors,'repetition')
        self.assertEqual(len(source),2);self.assertEqual(len(repetition),2)
        self.assertEqual({c['name'] for c in source},{'alpha.com','beta.com'})
        self.assertEqual({c['name'] for c in repetition},{'ride receipt <number>','monthly statement <number>'})
        self.assertIn('human',outs);self.assertIn('human',outs2)
        self.assertLessEqual(g['edges'],len(records)*g['candidate_limit'])
        self.assertTrue(all(c['explanation'] and c['name'] for c in source+repetition))
    def test_campaign_wording_changes_join_but_shared_sender_does_not(self):
        records=[];vectors=[]
        for i,subject in enumerate(["Don’t miss our Anniversary Sale: $1/wk.","Ends soon: The Anniversary Sale. $1/wk.","Ends soon: The Anniversary Sale. $2/wk.","Your subscription payment failed","A personal account conversation"]):
            r,_,_=extract(raw(str(i),sender='nyt@e.nytimes.com',subject=subject))
            records.append(r);vectors.append(np.array([1.,0.] if i<3 else [0.,1.]))
        clusters,outliers,_=build_clusters(records,vectors,'repetition')
        self.assertEqual(len(clusters),1);self.assertEqual(set(clusters[0]['message_ids']),{'0','1','2'})
        self.assertEqual(set(outliers),{'3','4'})

    def test_subject_normalization_and_singleton(self):
        self.assertEqual(subject_pattern('Re: Your code 123456'),subject_pattern('Your code 999999'))
        r,_,_=extract(raw('1'));clusters,outs,graph=build_clusters([r],[np.ones(8)],'source')
        self.assertEqual(clusters,[]);self.assertEqual(outs,['1'])
    def test_all_scope_does_not_exclude_any_folder_and_dates_validate(self):
        self.assertEqual(ExploreOptions(scope='sent').query(),'in:sent')
        with self.assertRaises(ValueError):ExploreOptions(scope='sent',categories=['primary'])
        self.assertEqual(ExploreOptions().scope,'all');self.assertEqual(ExploreOptions().query(),'')
        with self.assertRaises(ValueError):ExploreOptions(after='2026-10-01',before='2026-09-01')
        with self.assertRaises(UserError):ExploreOptions(sender='x" OR in:trash').query()

class ExplorerFlowTests(unittest.IsolatedAsyncioTestCase):
    setUp=v2.CleanupTests.setUp
    async def asyncSetUp(self):
        self.account='test@example.com'
        self.records={str(i):raw(str(i),labels=['TRASH'] if i==4 else ['INBOX']) for i in range(1,5)}
        self.api_gmail=Mock();self.api_gmail.request=AsyncMock(return_value={'messages':[{'id':i} for i in self.records]});self.api_gmail.message=AsyncMock(side_effect=lambda mid:self.records[mid])
        self.subs=Mock()
        @asynccontextmanager
        async def connection(expected=None):
            if expected and expected!=self.account:raise UserError('Account changed')
            yield self.api_gmail,self.account
        self.subs.connection=connection
        self.embedder=Mock();self.embedder.embed.side_effect=lambda texts:[np.ones(8,dtype=np.float32) for _ in texts]
        self.explorer=Explorer(FastAPI(),self.cleanup,self.subs,index=MailboxIndex(self.cleanup.history.path.parent/'explorer.sqlite3'),embedder=self.embedder)
    async def test_ingestion_reuses_embeddings_and_mode_switch_does_not_fetch(self):
        await self.explorer.start(ExploreOptions(limit=4));await self.explorer.task
        self.assertEqual(self.explorer.state['status'],'done')
        params=self.api_gmail.request.call_args.args[2];self.assertEqual(params['includeSpamTrash'],'true');self.assertNotIn('labelIds',params)
        rid=self.explorer.state['report_id'];self.assertEqual((await self.explorer.report(rid))['count'],4)
        calls=self.api_gmail.message.await_count
        await self.explorer.switch_mode(rid,ModeOptions(mode='repetition'))
        self.assertEqual(self.api_gmail.message.await_count,calls)
        self.embedder.embed.reset_mock()
        await self.explorer.start(ExploreOptions(limit=4));await self.explorer.task
        self.embedder.embed.assert_not_called();self.assertEqual(self.explorer.state['cached'],4)
        self.account='other@example.com'
        with self.assertRaises(UserError):await self.explorer.report(rid)
    async def test_cleanup_handoff_exact_cluster_refetch_protection_no_writes(self):
        self.records['2']['labelIds']=['STARRED','INBOX']
        await self.explorer.start(ExploreOptions(limit=4));await self.explorer.task
        rid=self.explorer.state['report_id'];report=await self.explorer.report(rid);cid=report['clusters'][0]['id']
        self.cleanup.start_branch=AsyncMock(return_value={'started':'laya'})
        result=await self.explorer.handoff(rid,cid,HandoffOptions(classifier='laya'))
        self.assertEqual(result['excluded'],1);self.assertEqual(set(self.cleanup.fetched_snapshot),{'1','2','3'})
        self.assertEqual(self.cleanup.fetched_snapshot['2']['protected'],['STARRED'])
        self.cleanup.start_branch.assert_awaited_once_with(result['run_id'],'laya')
        self.assertTrue(all(call.args[0]=='GET' for call in self.api_gmail.request.await_args_list))
        self.assertEqual(self.cleanup.fetched_snapshot['1']['bucket'],'not_analyzed')
    async def test_running_cleanup_blocks_analysis_and_handoff(self):
        self.cleanup.writing=True
        with self.assertRaises(UserError):await self.explorer.start(ExploreOptions(limit=4))
        self.api_gmail.message.assert_not_awaited()
    async def test_corpus_account_bound_message_refetch(self):
        await self.explorer.start(ExploreOptions(limit=4));await self.explorer.task
        viewed=await self.explorer.message('1');self.assertIn('routine',viewed['text'])
        self.account='other@example.com'
        with self.assertRaises(UserError):await self.explorer.message('1')

    async def test_explorer_archive_trash_uses_confirmation_and_live_protection(self):
        v2.CleanupTests.ready(self)
        self.cleanup.run['explorer_handoff']={'cluster':'bound'}
        self.gmail.message.side_effect=None;self.gmail.message.return_value=raw('1',labels=['UNREAD'])
        self.gmail.request.side_effect=lambda method,path,params=None: {'emailAddress':'test@example.com'} if path=='profile' else {'id':'1','labelIds':['TRASH']}
        prepared=await self.cleanup.prepare('run',Selection(ids=['1']))
        with patch.object(self.cleanup,'authorize',AsyncMock(return_value=self.gmail)):
            result=await self.cleanup.trash('run',ConfirmedSelection(ids=['1'],confirmation=prepared['confirmation']))
        self.assertEqual(result['succeeded'],1)
        self.assertTrue(any(c.args[:2]==('POST','messages/1/trash') for c in self.gmail.request.await_args_list))
        v2.CleanupTests.ready(self);self.cleanup.run['explorer_handoff']={'cluster':'bound'}
        self.gmail.request.reset_mock();self.gmail.message.return_value=raw('1',labels=['SENT'])
        prepared=await self.cleanup.prepare('run',Selection(ids=['1']))
        with patch.object(self.cleanup,'authorize',AsyncMock(return_value=self.gmail)):
            result=await self.cleanup.trash('run',ConfirmedSelection(ids=['1'],confirmation=prepared['confirmation']))
        self.assertEqual(result['failed'],1)
        self.assertFalse(any(c.args[0]=='POST' for c in self.gmail.request.await_args_list))
    async def test_restart_finds_only_connected_accounts_latest_report(self):
        await self.explorer.start(ExploreOptions(limit=4));await self.explorer.task
        restored=Explorer(FastAPI(),self.cleanup,self.subs,index=self.explorer.index,embedder=self.embedder)
        self.assertEqual((await restored.current())['report_id'],self.explorer.state['report_id'])
        self.account='other@example.com';self.assertNotIn('report_id',await restored.current())
    async def test_failed_embedding_keeps_index_and_stop_is_account_bound(self):
        self.embedder.embed.side_effect=RuntimeError('no model')
        await self.explorer.start(ExploreOptions(limit=4));await self.explorer.task
        self.assertEqual(self.explorer.state['status'],'error')
        self.account='other@example.com'
        with self.assertRaises(UserError):await self.explorer.stop()

class ExplorerHTTPTests(unittest.TestCase):
    def test_explorer_route_boundary_and_readonly_preview(self):
        from fastapi.testclient import TestClient
        from app import server
        import os
        with TestClient(server.app,base_url=server.ORIGIN) as client:
            self.assertEqual(client.get('/explorer').status_code,200)
            self.assertEqual(client.post('/api/explorer/analyze',json={'limit':100}).status_code,403)
            with patch.dict(os.environ,{'LOCAL_READONLY_PREVIEW':'1'}):
                r=client.post('/api/explorer/reports/r/clusters/c/review',headers={'Origin':server.ORIGIN},json={'classifier':'laya'})
                self.assertEqual(r.status_code,403)
