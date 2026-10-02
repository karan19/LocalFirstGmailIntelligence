import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import server
from app.cleanup import Cleanup, ConfirmedSelection, Decision, EraseFeedback, FeedbackSetting, Gmail, GmailAuthorizationError, MODIFY_SCOPE, Selection, review_qwen, route_result
from app.services import UserError
from app.history import ReviewHistory
from app.cleanup import ScanOptions


def distribution(p):
    return {'CLEANUP': p, 'KEEP': 1-p, 'NEEDS_REVIEW': 0} if p <= .15 or p >= .85 else {'KEEP': .2, 'CLEANUP': .2, 'NEEDS_REVIEW': .6}


def raw(i, labels=None):
    return {'id': str(i), 'labelIds': labels or ['INBOX'], 'internalDate': str(i), 'snippet': 'Sale', 'payload': {'headers': []}}


class RoutingTests(unittest.TestCase):
    def test_explicit_decisions_not_confidence_bands(self):
        self.assertEqual(route_result({'KEEP': .25, 'CLEANUP': .55, 'NEEDS_REVIEW': .2}), ('ready', 'laya_cleanup'))
        self.assertEqual(route_result({'KEEP': .55, 'CLEANUP': .25, 'NEEDS_REVIEW': .2}), ('keep', 'laya_keep'))
        self.assertEqual(route_result({'KEEP': .2, 'CLEANUP': .2, 'NEEDS_REVIEW': .6})[0], 'review')
        for confidence in (30, 60, 90):
            self.assertEqual(route_result(distribution(.5), {'decision': 'CLEANUP', 'confidence': confidence})[0], 'ready')
            self.assertEqual(route_result(distribution(.5), {'decision': 'KEEP', 'confidence': confidence})[0], 'keep')
            self.assertEqual(route_result(distribution(.5), {'decision': 'NEEDS_REVIEW', 'confidence': confidence})[0], 'review')

    def test_modify_oauth_and_request_boundary(self):
        with TestClient(server.app, base_url=server.ORIGIN) as client:
            client.get('/')
            config={'client_id':'id','client_secret':'private'}
            with patch.object(server, 'client_config', return_value=config):
                response=client.post('/api/connect?cleanup=true', headers={'Origin':server.ORIGIN})
            self.assertEqual(response.status_code, 200)
            self.assertIn('gmail.modify',response.json()['url'])
            self.assertNotIn('private',response.json()['url'])
            self.assertNotIn('mail.google.com',response.json()['url'])
            self.assertEqual(client.post('/api/scan').status_code,403)
            self.assertEqual(client.post('/api/scan/x/trash', headers={'Origin':server.ORIGIN},json={'ids':['x']}).status_code,422)
            self.assertEqual(client.get('/cleanup').status_code,200)


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store=Mock()
        self.store.read.return_value={'scope':MODIFY_SCOPE,'refresh_token':'refresh'}
        self.refresh=AsyncMock(return_value={'access_token':'access','scope':MODIFY_SCOPE})
        self.classifier=Mock()
        self.classifier.classify.return_value={'probabilities':distribution(.9),'category':'CLEANUP'}
        self.cleanup=Cleanup(FastAPI(),self.store,self.classifier,lambda:{'client_id':'id'},self.refresh,lambda:False)
        self.history_dir=tempfile.TemporaryDirectory()
        self.addCleanup(self.history_dir.cleanup)
        self.cleanup.history=ReviewHistory(Path(self.history_dir.name)/'history/reviews.sqlite3')
        self.gmail=Mock()
        self.gmail.close=AsyncMock()
        self.gmail.message=AsyncMock(side_effect=lambda i,metadata=False: raw(i))
        self.gmail.request=AsyncMock(return_value={'emailAddress':'test@example.com'})

    def ready(self, ids=('1','2')):
        self.cleanup.run.update(id='run',status='done',account='test@example.com')
        self.cleanup.messages={i:{'id':i,'sender':'sender','subject':'sale','text':'body','received':1,'protected':[],'bucket':'ready','source':'laya_cleanup','trash_status':None} for i in ids}

    async def test_readonly_must_reconnect(self):
        self.store.read.return_value={'scope':'https://www.googleapis.com/auth/gmail.readonly','refresh_token':'refresh'}
        with self.assertRaisesRegex(UserError,'Reconnect'):
            await self.cleanup.start()
        self.refresh.assert_not_called()

    async def test_paginated_five_thousand_fetch_no_more(self):
        self.cleanup.options=ScanOptions(limit=5000)
        pages=[{'messages':[{'id':str(i)} for i in range(n*500+1,(n+1)*500+1)],'nextPageToken':str(n+1)} for n in range(10)]
        async def request(method,path,params=None):
            if path=='profile': return {'emailAddress':'test@example.com'}
            self.assertEqual(method,'GET'); self.assertEqual(params['labelIds'],'INBOX');self.assertEqual(params['maxResults'],500)
            if len(pages)<10:self.assertEqual(params['pageToken'],str(10-len(pages)))
            return pages.pop(0)
        self.gmail.request.side_effect=request
        self.cleanup.run.update(id='run',status='running')
        await self.cleanup.scan(self.gmail)
        self.assertEqual(self.cleanup.run['status'],'done')
        self.assertEqual(len(self.cleanup.messages),5000)
        self.assertEqual(self.gmail.message.await_count,5000)
        self.assertEqual(next(iter(self.cleanup.messages)),'5000')
        self.assertEqual(self.classifier.classify.call_count,5000)
        response=await self.cleanup.results('run',limit=20)
        self.assertEqual(response['total'],5000); self.assertEqual(len(response['items']),20)
        self.assertNotIn('text',response['items'][0])

    async def test_protection_qwen_failure_and_partial_fetch(self):
        self.gmail.request.side_effect=[{'emailAddress':'test@example.com'},{'messages':[{'id':str(i)} for i in range(1,5)]}]
        self.gmail.message.side_effect=[raw(1,['INBOX','STARRED']),raw(2),UserError('missing'),raw(4,['SPAM'])]
        self.classifier.classify.return_value={'category':'NEEDS_REVIEW','probabilities':distribution(.5)}
        self.cleanup.run.update(id='run',status='running')
        with patch('app.cleanup.review_qwen',AsyncMock(side_effect=ValueError('invalid'))) as qwen:
            await self.cleanup.scan(self.gmail)
        self.assertEqual(self.cleanup.run['skipped'],2)
        self.assertEqual(self.cleanup.messages['1']['bucket'],'keep')
        self.assertEqual(self.cleanup.messages['2']['source'],'qwen_error')
        self.assertEqual(qwen.await_count,1)
        with self.assertRaises(UserError):await self.cleanup.decide('run','1',Decision(decision='cleanup'))

    async def test_selection_confirmation_and_protection_recheck(self):
        self.ready()
        for ids in (['foreign'], ['1','1']):
            with self.assertRaises(UserError):await self.cleanup.prepare('run',Selection(ids=ids))
        prepared=await self.cleanup.prepare('run',Selection(ids=['1']))
        with self.assertRaises(UserError):await self.cleanup.trash('run',ConfirmedSelection(ids=['2'],confirmation=prepared['confirmation']))
        prepared=await self.cleanup.prepare('run',Selection(ids=['1']))
        self.gmail.message.return_value=raw(1,['INBOX','STARRED']);self.gmail.message.side_effect=None
        with patch.object(self.cleanup,'authorize',AsyncMock(return_value=self.gmail)):
            result=await self.cleanup.trash('run',ConfirmedSelection(ids=['1'],confirmation=prepared['confirmation']))
        self.assertEqual(result['failed'],1);self.assertEqual(self.cleanup.messages['1']['bucket'],'keep')
        self.assertEqual(self.gmail.request.await_count,1) # profile only, no write
        with self.assertRaises(UserError):await self.cleanup.prepare('run',Selection(ids=['1']))

    async def test_partial_trash_account_binding_and_retry(self):
        self.ready()
        async def request(method,path,params=None):
            if path=='profile':return {'emailAddress':'test@example.com'}
            if path=='messages/2/trash':raise UserError('rate limited')
            return {'id':'1','labelIds':['TRASH']}
        self.gmail.request.side_effect=request
        with patch.object(self.cleanup,'authorize',AsyncMock(return_value=self.gmail)):
            prepared=await self.cleanup.prepare('run',Selection(ids=['1','2']))
            result=await self.cleanup.trash('run',ConfirmedSelection(ids=['1','2'],confirmation=prepared['confirmation']))
            self.assertEqual((result['succeeded'],result['failed']),(1,1))
            with self.assertRaises(UserError):await self.cleanup.prepare('run',Selection(ids=['1']))
            prepared=await self.cleanup.prepare('run',Selection(ids=['2']))
            self.gmail.request.side_effect=None;self.gmail.request.return_value={'emailAddress':'other@example.com'}
            with self.assertRaisesRegex(UserError,'account changed'):
                await self.cleanup.trash('run',ConfirmedSelection(ids=['2'],confirmation=prepared['confirmation']))
        self.assertFalse(self.cleanup.writing)

    async def test_overrides_feedback_opt_in_delete_and_restart(self):
        self.ready()
        with tempfile.TemporaryDirectory() as tmp:
            self.cleanup.feedback_path=Path(tmp)/'feedback/reviews.jsonl'
            await self.cleanup.decide('run','1',Decision(decision='keep'))
            self.assertFalse(self.cleanup.feedback_path.exists())
            await self.cleanup.set_feedback(FeedbackSetting(enabled=True))
            await self.cleanup.decide('run','1',Decision(decision='cleanup'))
            self.assertEqual(json.loads(self.cleanup.feedback_path.read_text())['text'],'body')
            self.assertEqual(self.cleanup.feedback_path.stat().st_mode & 0o777,0o600)
            self.assertEqual(self.cleanup.feedback_path.parent.stat().st_mode & 0o777,0o700)
            await self.cleanup.set_feedback(FeedbackSetting(enabled=False))
            self.assertTrue(self.cleanup.feedback_path.exists())
            with self.assertRaises(UserError):await self.cleanup.erase_feedback(EraseFeedback(confirmed=False))
            await self.cleanup.erase_feedback(EraseFeedback(confirmed=True));self.assertFalse(self.cleanup.feedback_path.exists())
        self.cleanup.reset()
        with self.assertRaises(UserError):await self.cleanup.prepare('run',Selection(ids=['1']))

    async def test_expired_confirmation_and_fatal_fetch_authorization(self):
        self.ready()
        prepared=await self.cleanup.prepare('run',Selection(ids=['1']))
        token=prepared['confirmation']
        self.cleanup.confirmations[token]=(tuple(['1']),-10000)
        with self.assertRaisesRegex(UserError,'expired'):
            await self.cleanup.trash('run',ConfirmedSelection(ids=['1'],confirmation=token))
        self.gmail.request.side_effect=[{'emailAddress':'test@example.com'}, {'messages':[{'id':'1'}]}]
        self.gmail.message.side_effect=GmailAuthorizationError('Reconnect Gmail')
        await self.cleanup.scan(self.gmail)
        self.assertEqual(self.cleanup.run['status'],'error')
        self.assertEqual(self.cleanup.messages,{})
        self.assertIn('Reconnect',self.cleanup.run['error'])

    async def test_category_union_limit_and_history_refill(self):
        self.cleanup.options=ScanOptions(limit=2,categories=['social','promotions'],only_unreviewed=True)
        self.cleanup.history.mark('test@example.com',[('1','keep')])
        pages=[{'messages':[{'id':'1'},{'id':'2'}],'nextPageToken':'next'},{'messages':[{'id':'3'}]}]
        async def request(method,path,params=None):
            if path=='profile':return {'emailAddress':'test@example.com'}
            self.assertEqual(params['q'],'{category:promotions category:social}')
            self.assertEqual(params['labelIds'],'INBOX')
            self.assertEqual(params['maxResults'],2 if len(pages)==2 else 1)
            return pages.pop(0)
        self.gmail.request.side_effect=request
        self.cleanup.run.update(id='run',status='running')
        await self.cleanup.scan(self.gmail)
        self.assertEqual(set(self.cleanup.messages),{'2','3'})
        self.assertEqual(self.cleanup.run['history_excluded'],1)
        self.assertEqual(self.cleanup.history.ids('different@example.com'),set())

    async def test_category_estimates_empty_and_read_filter(self):
        captured=[]
        async def request(method,path,params=None):
            self.assertEqual(method,'GET')
            if path=='profile':return {'emailAddress':'test@example.com'}
            captured.append(params)
            if 'category:social' in params.get('q',''):return {'messages':[],'resultSizeEstimate':0}
            if 'category:forums' in params.get('q',''):raise UserError('Busy')
            return {'messages':[{'id':'private-id'}],'resultSizeEstimate':42}
        self.gmail.request.side_effect=request
        with patch.object(self.cleanup,'authorize',AsyncMock(return_value=self.gmail)):
            result=await self.cleanup.categories(read_status='unread')
        categories={c['id']:c for c in result['categories']}
        self.assertEqual(categories['all']['estimate'],42)
        self.assertFalse(categories['social']['available'])
        self.assertEqual(categories['social']['estimate'],0)
        self.assertIsNone(categories['forums']['available'])
        self.assertEqual(len(captured),6)
        self.assertTrue(all(p['labelIds']=='INBOX' and p['maxResults']==1 for p in captured))
        self.assertTrue(all('is:unread' in p['q'] for p in captured))
        self.assertNotIn('private-id',json.dumps(result))
        self.gmail.message.assert_not_called()

    async def test_read_status_query_and_fresh_label_check(self):
        for status in ('all', 'read', 'unread'):
            with self.subTest(status=status):
                self.cleanup.reset()
                self.cleanup.options=ScanOptions(limit=2,categories=['promotions'],read_status=status)
                captured=[]
                async def request(method,path,params=None):
                    if path=='profile':return {'emailAddress':'test@example.com'}
                    captured.append(params['q'])
                    return {'messages':[{'id':'1'},{'id':'2'}]}
                self.gmail.request.side_effect=request
                self.gmail.message.side_effect=lambda i,metadata=False:raw(i,['INBOX','UNREAD'] if i=='1' else ['INBOX'])
                self.cleanup.run.update(id='run',status='running')
                await self.cleanup.scan(self.gmail)
                self.assertEqual(captured,['{category:promotions}'+('' if status=='all' else ' is:'+status)])
                self.assertEqual(set(self.cleanup.messages),{'1','2'} if status=='all' else {'1'} if status=='unread' else {'2'})
                self.assertEqual(self.cleanup.run['skipped'],0 if status=='all' else 1)
                self.assertTrue(all(m['read_status']==status for m in self.cleanup.messages.values()) if status!='all' else True)
                self.assertTrue(all(call.args[0]=='GET' for call in self.gmail.request.await_args_list))

    async def test_stop_during_laya_retains_current_and_unanalyzed(self):
        self.gmail.request.side_effect=[{'emailAddress':'test@example.com'}, {'messages':[{'id':'1'},{'id':'2'}]}]
        def classify(message):
            self.cleanup.stop_requested=True
            return {'probabilities':distribution(.9),'category':'CLEANUP'}
        self.classifier.classify.side_effect=classify
        self.cleanup.run.update(id='run',status='running')
        await self.cleanup.scan(self.gmail)
        self.assertEqual(self.cleanup.run['status'],'stopped')
        self.assertEqual(self.cleanup.run['laya_completed'],1)
        ready=[m for m in self.cleanup.messages.values() if m['bucket']=='ready'][0]
        pending=[m for m in self.cleanup.messages.values() if m['bucket']=='not_analyzed'][0]
        await self.cleanup.prepare('run',Selection(ids=[ready['id']]))
        with self.assertRaises(UserError):await self.cleanup.prepare('run',Selection(ids=[pending['id']]))
        await self.cleanup.decide('run',pending['id'],Decision(decision='keep'))
        self.assertEqual(self.cleanup.history.ids('test@example.com'),{pending['id']})

    async def test_stop_qwen_retains_pending_ambiguity(self):
        self.gmail.request.side_effect=[{'emailAddress':'test@example.com'}, {'messages':[{'id':'1'},{'id':'2'}]}]
        self.classifier.classify.return_value={'probabilities':distribution(.5),'category':'NEEDS_REVIEW'}
        async def review(message):
            await self.cleanup.stop('run')
            return {'decision':'CLEANUP','confidence':90,'reason':'Sale'}
        self.cleanup.run.update(id='run',status='running')
        with patch('app.cleanup.review_qwen',side_effect=review):await self.cleanup.scan(self.gmail)
        self.assertEqual(self.cleanup.run['status'],'stopped')
        self.assertEqual(self.cleanup.run['qwen_completed'],1)
        self.assertEqual(self.cleanup.run['qwen_total'],2)
        self.assertEqual(self.cleanup.messages['1']['source'],'qwen_pending')
        self.assertEqual(self.cleanup.messages['1']['bucket'],'review')
        self.assertEqual(self.cleanup.history.ids('test@example.com'),set())

    async def test_stop_fetch_does_not_schedule_more(self):
        self.gmail.request.side_effect=[{'emailAddress':'test@example.com'}, {'messages':[{'id':str(i)} for i in range(1,10)]}]
        async def fetch(message_id,metadata=False):
            await self.cleanup.stop('run')
            return raw(message_id)
        self.gmail.message.side_effect=fetch
        self.cleanup.run.update(id='run',status='running')
        await self.cleanup.scan(self.gmail)
        self.assertEqual(self.gmail.message.await_count,1)
        self.assertEqual(self.cleanup.run['fetched'],1)
        self.assertEqual(self.classifier.classify.call_count,0)
        self.assertEqual(self.cleanup.run['status'],'stopped')

    async def test_review_history_separate_from_feedback_and_clear(self):
        self.ready()
        await self.cleanup.mark_reviewed('run',Selection(ids=['1']))
        history=ReviewHistory(self.cleanup.history.path)
        self.assertEqual(history.ids('test@example.com'),{'1'})
        self.assertFalse(self.cleanup.feedback_enabled)
        self.assertEqual(self.cleanup.history.path.stat().st_mode & 0o777,0o600)
        with self.assertRaises(UserError):await self.cleanup.erase_history(EraseFeedback(confirmed=False))
        await self.cleanup.erase_history(EraseFeedback(confirmed=True))
        self.assertEqual(history.ids('test@example.com'),set())
        self.assertFalse(self.cleanup.messages['1']['reviewed'])

    def test_scan_options_reject_invalid_limits_and_categories(self):
        from pydantic import ValidationError
        self.assertEqual(ScanOptions(limit=5000).limit,5000)
        for payload in ({'limit':0},{'limit':5001},{'limit':1.5},{'categories':['arbitrary']},{'categories':['all']},{'read_status':'opened'}):
            with self.assertRaises(ValidationError):ScanOptions(**payload)

    async def test_qwen_strict_json_local_only(self):
        answer={'decision':'CLEANUP','confidence':90,'reason':'A sale offer.'}
        captured=[]
        def handle(request):
            self.assertEqual(request.url.host,'127.0.0.1')
            captured.append(json.loads(request.content))
            return httpx.Response(200,json={'message':{'content':json.dumps(answer)}})
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch('app.cleanup.httpx.AsyncClient',return_value=client):
            result=await review_qwen({'sender':'shop','subject':'sale','text':'buy','token':'SECRET'})
        self.assertEqual(result,answer);self.assertNotIn('SECRET',json.dumps(captured))
        answer['confidence']=101
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch('app.cleanup.httpx.AsyncClient',return_value=client):
            with self.assertRaises(ValueError):await review_qwen({'sender':'s','subject':'s','text':'s'})

    async def test_gmail_backoff_and_auth_error(self):
        calls=[]
        def handle(request):
            calls.append(request)
            return httpx.Response(403 if len(calls)<3 else 200,json={'error':{'errors':[{'reason':'userRateLimitExceeded'}]}} if len(calls)<3 else {'messages':[]})
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch('app.cleanup.httpx.AsyncClient',return_value=client),patch('app.cleanup.asyncio.sleep',AsyncMock()) as sleep:
            gmail=Gmail('token');self.assertEqual(await gmail.request('GET','messages'),{'messages':[]})
            self.assertEqual(sleep.await_count,2);await gmail.close()
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(401)))
        with patch('app.cleanup.httpx.AsyncClient',return_value=client):
            gmail=Gmail('token')
            with self.assertRaisesRegex(UserError,'Reconnect'):await gmail.request('GET','profile')
            await gmail.close()
