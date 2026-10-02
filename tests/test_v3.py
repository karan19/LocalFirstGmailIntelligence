import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import server
from app.cleanup import Cleanup, ConfirmedSelection, EraseFeedback, MODIFY_SCOPE, Selection
from app.history import ReviewHistory
from app.journal import Journal
from app.services import UserError
from app.subscriptions import DiscoveryOptions, SubscriptionAction, Subscriptions, UnsubscribeSelection
from app.unsubscribe import capability, matching, parse, post_one_click, public_addresses, safe_url, verify_dkim

ACCOUNT = 'test@example.net'
IDENTITY = 'list:demo.example.org'
FIXTURES = Path(__file__).parent / 'fixtures'


def raw(message_id='1', sender='news@example.org', list_id='demo.example.org', unsubscribe=True, one_click=False, labels=None):
    pairs = [('From', 'Demo <'+sender+'>'), ('Subject', 'Newsletter')]
    if list_id:
        pairs.append(('List-ID', 'Demo <'+list_id+'>'))
    if unsubscribe:
        pairs.append(('List-Unsubscribe', '<https://example.org/unsubscribe/demo>'))
    if one_click:
        pairs.append(('List-Unsubscribe-Post', 'List-Unsubscribe=One-Click'))
    return {'id': message_id, 'internalDate':'1000', 'labelIds': labels if labels is not None else ['INBOX'],
            'payload': {'headers':[{'name':n,'value':v} for n,v in pairs]}}


def record():
    return {'identity':IDENTITY,'list_id':'demo.example.org','name':'Demo','senders':['news@example.org'], 'last_seen':1000,'reference':'1','method':'web'}


class ParsingTests(unittest.TestCase):
    def test_list_id_sender_fallback_and_duplicate_headers(self):
        self.assertEqual(parse(raw())['identity'],IDENTITY)
        self.assertEqual(parse(raw(list_id=None))['identity'],'sender:news@example.org')
        self.assertFalse(parse(raw(list_id=None,unsubscribe=False))['evidence'])
        message=raw(one_click=True)
        message['payload']['headers'].append({'name':'List-Unsubscribe','value':'<https://evil.example.org/>'})
        self.assertFalse(parse(message)['candidate'])
        self.assertIsNone(parse({'id':'1','payload':{'headers':[{'name':'From','value':'a@example.org, b@example.org'}]}}))

    def test_https_mailto_combinations_and_unsupported_schemes(self):
        message=raw(one_click=True)
        message['payload']['headers'][3]['value']='<mailto:unsubscribe@example.org?subject=unsubscribe>, <https://example.org/unsubscribe/demo>'
        parsed=parse(message)
        self.assertTrue(parsed['candidate']);self.assertEqual(parsed['method'],'web')
        message['payload']['headers'][3]['value']='<mailto:unsubscribe@example.org?subject=unsubscribe>'
        self.assertEqual(parse(message)['method'],'mailto');self.assertFalse(parse(message)['candidate'])
        message['payload']['headers'][3]['value']='<javascript:alert(1)>, <http://example.org/unsub>'
        self.assertEqual(parse(message)['method'],'unknown')

    def test_url_rejection_and_public_dns_gate(self):
        for url in ('http://example.org/u','https://localhost/u','https://127.0.0.1/u','https://[::1]/u','https://169.254.169.254/u',
                    'https://user:pass@example.org/u','https://example.org:8080/u','https://example.org/u#fragment','https://example.org/\n',
                    'https://example.org\\@localhost/u','file:///tmp/x','https://service.local/u'):
            self.assertFalse(safe_url(url),url)
        self.assertTrue(safe_url('https://example.org/unsubscribe?token=opaque'))
        self.assertTrue(safe_url('https://[2606:4700:4700::1111]/u'))
        with self.assertRaises(UserError):public_addresses('127.0.0.1')
        resolver=Mock();resolver.resolve.side_effect=[['8.8.8.8'],['::1']]
        with patch('dns.resolver.Resolver',return_value=resolver):
            with self.assertRaises(UserError):public_addresses('example.org')

    def test_exact_identity_and_conservative_sender_evidence(self):
        self.assertTrue(matching(raw(sender='other@example.org'),record()))
        self.assertFalse(matching(raw(list_id='other.example.org'),record()))
        fallback={**record(),'identity':'sender:news@example.org','list_id':None}
        self.assertTrue(matching(raw(list_id=None),fallback))
        self.assertFalse(matching(raw(list_id=None,unsubscribe=False),fallback))
        self.assertFalse(matching(raw(list_id='other.example.org'),fallback))
        self.assertTrue(matching(raw(list_id=None,unsubscribe=False),fallback,broader=True))
        self.assertFalse(matching(raw(sender='another@example.org',list_id=None),fallback,broader=True))

    def test_real_dkim_signature_coverage_tampering_and_forged_authentication(self):
        message=(FIXTURES/'one-click.eml').read_bytes()
        expected=parse(raw(one_click=True))
        dns=Mock();dns.resolve.return_value=[Mock(strings=[(FIXTURES/'dkim-public.txt').read_bytes()])]
        with patch('dns.resolver.Resolver',return_value=dns):
            encoded=lambda data:base64.urlsafe_b64encode(data).decode()
            self.assertTrue(verify_dkim(encoded(message),expected))
            self.assertFalse(verify_dkim(encoded(message.replace(b'Synthetic message',b'Malicious message')),expected))
            self.assertFalse(verify_dkim(encoded(message.replace(b'List-Unsubscribe-Post: List-Unsubscribe=One-Click',b'List-Unsubscribe-Post: Different')),expected))
            self.assertFalse(verify_dkim(encoded(message),{**expected,'sender':'forged@evil.org'}))
            self.assertFalse(verify_dkim(encoded(message.replace(b'list-unsubscribe-post',b'unsigned-header-field')),expected))
            self.assertFalse(verify_dkim(encoded(b'Authentication-Results: mx.google.com; dkim=pass\r\n'+message.split(b'From:')[1]),expected))
            self.assertFalse(verify_dkim('invalid',expected))

    def test_post_body_no_context_redirect_timeout_and_http_failure(self):
        for status in (200,204,302,400,503):
            connection=Mock();connection.getresponse.return_value.status=status
            with patch('app.unsubscribe.public_addresses',return_value=['8.8.8.8']),patch('app.unsubscribe.PinnedHTTPS',return_value=connection) as pinned:
                if 200<=status<300:self.assertEqual(post_one_click('https://example.org/unsubscribe/demo'),status)
                else:
                    with self.assertRaises(UserError):post_one_click('https://example.org/unsubscribe/demo')
                pinned.assert_called_once_with('example.org','8.8.8.8')
                connection.request.assert_called_once_with('POST','/unsubscribe/demo',body=b'List-Unsubscribe=One-Click',headers={'Content-Type':'application/x-www-form-urlencoded'})
                connection.close.assert_called_once()
        connection=Mock();connection.request.side_effect=TimeoutError()
        with patch('app.unsubscribe.public_addresses',return_value=['8.8.8.8']),patch('app.unsubscribe.PinnedHTTPS',return_value=connection):
            with self.assertRaisesRegex(UserError,'timed out'):post_one_click('https://example.org/u')
        with patch('app.unsubscribe.PinnedHTTPS') as conn:
            with self.assertRaises(UserError):post_one_click('https://localhost/u')
            conn.assert_not_called()


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.journal=Journal(Path(self.temp.name)/'history/reviews.sqlite3')

    def test_inventory_restart_deduplication_account_separation_and_keep(self):
        self.journal.observe(ACCOUNT,record(),'1');self.journal.observe(ACCOUNT,record(),'1')
        self.journal.update(ACCOUNT,IDENTITY,keep=True,status='request_accepted')
        self.journal.observe(ACCOUNT,{**record(),'senders':['new@example.org'],'last_seen':2000},'2')
        fresh=Journal(self.journal.path)
        saved=fresh.inventory(ACCOUNT)[0]
        self.assertEqual(saved['count'],2);self.assertTrue(saved['keep']);self.assertEqual(saved['status'],'request_accepted')
        self.assertEqual(saved['senders'],['new@example.org','news@example.org'])
        self.assertEqual(fresh.inventory('other@example.net'),[])
        self.assertEqual(self.journal.path.stat().st_mode&0o777,0o600)
        self.assertEqual(self.journal.path.parent.stat().st_mode&0o777,0o700)
        self.assertNotIn('Newsletter',self.journal.path.read_bytes().decode(errors='ignore'))

    def test_legacy_import_once_and_independent_clearing(self):
        self.journal.mark(ACCOUNT,[('legacy','trashed'),('keep','keep')])
        self.journal.import_v2(ACCOUNT);self.journal.import_v2(ACCOUNT)
        self.assertEqual(self.journal.recoverable(ACCOUNT),{'legacy'})
        self.assertEqual(self.journal.actions(ACCOUNT)['total'],1)
        self.journal.observe(ACCOUNT,record(),'1')
        self.journal.clear_inventory(ACCOUNT)
        self.assertEqual(self.journal.recoverable(ACCOUNT),{'legacy'})
        self.journal.clear_actions(ACCOUNT);self.journal.import_v2(ACCOUNT)
        self.assertEqual(self.journal.recoverable(ACCOUNT),set())

    def test_recovery_after_unknown_write_restart_and_restore(self):
        event=self.journal.event(ACCOUNT,'trash','pending','1')
        self.journal.event(ACCOUNT,'trash','failed','1',detail='Unknown network result')
        self.assertEqual(Journal(self.journal.path).recoverable(ACCOUNT),{'1'})
        self.journal.finish(event,'succeeded')
        self.journal.event(ACCOUNT,'restore','failed','1')
        self.assertEqual(self.journal.recoverable(ACCOUNT),{'1'})
        self.journal.event(ACCOUNT,'restore','succeeded','1')
        self.assertEqual(self.journal.recoverable(ACCOUNT),set())
        self.assertEqual(self.journal.actions('other@example.net')['items'],[])


class V3Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        app=FastAPI()
        self.cleanup=Cleanup(app,Mock(),Mock(),lambda:{},AsyncMock(),lambda:False)
        self.cleanup.history=ReviewHistory(Path(self.temp.name)/'history/reviews.sqlite3')
        self.v3=Subscriptions(app,self.cleanup)
        self.gmail=Mock();self.gmail.close=AsyncMock()
        self.gmail.message=AsyncMock(side_effect=lambda i,metadata=False:raw(i))
        self.gmail.request=AsyncMock(return_value={'emailAddress':ACCOUNT})
        self.cleanup.authorize=AsyncMock(return_value=self.gmail)
        self.v3.journal.observe(ACCOUNT,record(),'1')

    def backlog(self):
        self.v3.run.update(id='scan',status='done',account=ACCOUNT,identity=IDENTITY)
        self.v3.messages={i:{'id':i,'sender':'Demo','subject':'Newsletter','received':1000,'protected':[],'trash_status':None} for i in ('1','2')}
        self.v3.journal.update(ACCOUNT,IDENTITY,status='manual_completed')

    async def test_discovery_archived_pagination_limits_dedup_no_model_or_writes(self):
        self.v3.reset();self.v3.run.update(id='scan',status='running',account=ACCOUNT,limit=3)
        pages=[{'messages':[{'id':'1'},{'id':'2'}],'nextPageToken':'next','resultSizeEstimate':20},{'messages':[{'id':'2'},{'id':'3'}],'nextPageToken':'more'}]
        async def request(method,path,params=None):
            self.assertEqual(method,'GET');self.assertIn('-in:spam -in:trash',params['q']);self.assertNotIn('labelIds',params)
            return pages.pop(0)
        self.gmail.request.side_effect=request
        self.gmail.message.side_effect=lambda i,metadata=False:raw(i,labels=[])
        await self.v3.scan(self.gmail)
        self.assertEqual(self.v3.run['scanned'],3);self.assertFalse(self.v3.run['complete'])
        self.assertEqual(self.v3.run['remaining_estimate'],17)
        self.assertEqual(self.v3.journal.inventory(ACCOUNT)[0]['count'],3)
        self.cleanup.classifier.classify.assert_not_called()

    async def test_stop_retains_inventory_and_marks_partial(self):
        self.v3.run.update(id='scan',status='running',account=ACCOUNT,limit=10)
        self.gmail.request.return_value={'messages':[{'id':str(i)} for i in range(1,9)],'resultSizeEstimate':8}
        async def message(i,metadata=False):
            await self.v3.stop()
            return raw(i)
        self.gmail.message.side_effect=message
        await self.v3.scan(self.gmail)
        self.assertEqual(self.v3.run['status'],'stopped');self.assertFalse(self.v3.run['complete'])
        self.assertEqual(self.gmail.message.await_count,4)
        self.assertEqual(self.v3.journal.inventory(ACCOUNT)[0]['count'],4)

    async def test_exhausted_scope_empty_and_fetch_failure(self):
        self.v3.run.update(id='scan',status='running',account=ACCOUNT)
        self.gmail.request.return_value={'messages':[],'resultSizeEstimate':0}
        await self.v3.scan(self.gmail)
        self.assertTrue(self.v3.run['complete'])
        self.v3.run.update(status='running',scanned=0,listed=0,estimate=None,complete=False)
        self.gmail.request.return_value={'messages':[{'id':'2'}]}
        self.gmail.message.side_effect=UserError('Missing')
        await self.v3.scan(self.gmail)
        self.assertFalse(self.v3.run['complete']);self.assertEqual(self.v3.run['skipped'],1)

    async def test_one_click_requires_confirmation_and_revalidates_dkim(self):
        self.gmail.message.side_effect=lambda i,metadata=False:raw(i,one_click=True)
        with patch('app.subscriptions.capability',AsyncMock(return_value='one_click')),patch('app.subscriptions.post_one_click',return_value=204) as post:
            with self.assertRaises(UserError):await self.v3.unsubscribe(UnsubscribeSelection(identity=IDENTITY))
            post.assert_not_called()
            prepared=await self.v3.prepare_unsubscribe(UnsubscribeSelection(identity=IDENTITY))
            result=await self.v3.unsubscribe(UnsubscribeSelection(identity=IDENTITY,confirmation=prepared['confirmation']))
            self.assertEqual(result['outcome'],'request_accepted')
            self.assertEqual(self.v3.journal.subscription(ACCOUNT,IDENTITY)['status'],'request_accepted')
            post.assert_called_once()
            with self.assertRaises(UserError):await self.v3.unsubscribe(UnsubscribeSelection(identity=IDENTITY,confirmation=prepared['confirmation']))
        prepared=await self.v3.prepare_unsubscribe(UnsubscribeSelection(identity=IDENTITY))
        with patch('app.subscriptions.post_one_click') as post:
            # Signature is unavailable: capability falls back to manual rather than sending.
            result=await self.v3.unsubscribe(UnsubscribeSelection(identity=IDENTITY,confirmation=prepared['confirmation']))
            self.assertEqual(result['outcome'],'manual_required');post.assert_not_called()

    async def test_signed_capability_then_failed_reverification_sends_nothing(self):
        self.gmail.message.side_effect=lambda i,metadata=False:raw(i,one_click=True)
        with patch('app.subscriptions.capability',AsyncMock(side_effect=['one_click','web'])),patch('app.subscriptions.post_one_click') as post:
            prepared=await self.v3.prepare_unsubscribe(UnsubscribeSelection(identity=IDENTITY))
            with self.assertRaisesRegex(UserError,'authentication'):
                await self.v3.unsubscribe(UnsubscribeSelection(identity=IDENTITY,confirmation=prepared['confirmation']))
            post.assert_not_called()

    async def test_unsubscribe_destination_account_change_expiry_failure_and_manual(self):
        with patch('app.subscriptions.post_one_click') as post:
            prepared=await self.v3.prepare_unsubscribe(UnsubscribeSelection(identity=IDENTITY))
            self.gmail.request.return_value={'emailAddress':'other@example.net'}
            with self.assertRaisesRegex(UserError,'account changed'):
                await self.v3.unsubscribe(UnsubscribeSelection(identity=IDENTITY,confirmation=prepared['confirmation']))
            post.assert_not_called()
        self.gmail.request.return_value={'emailAddress':ACCOUNT}
        prepared=await self.v3.prepare_unsubscribe(UnsubscribeSelection(identity=IDENTITY))
        self.v3.confirmations[prepared['confirmation']]=(*self.v3.confirmations[prepared['confirmation']][:3],-10000)
        with self.assertRaisesRegex(UserError,'expired'):await self.v3.unsubscribe(UnsubscribeSelection(identity=IDENTITY,confirmation=prepared['confirmation']))
        prepared=await self.v3.prepare_unsubscribe(UnsubscribeSelection(identity=IDENTITY))
        result=await self.v3.unsubscribe(UnsubscribeSelection(identity=IDENTITY,confirmation=prepared['confirmation']))
        self.assertEqual(result['outcome'],'manual_required')
        await self.v3.preference(SubscriptionAction(identity=IDENTITY,action='manual_completed'))
        self.assertEqual(self.v3.journal.subscription(ACCOUNT,IDENTITY)['status'],'manual_completed')
        self.gmail.message.side_effect=lambda i,metadata=False:raw(i,one_click=True)
        with patch('app.subscriptions.capability',AsyncMock(return_value='one_click')),patch('app.subscriptions.post_one_click',side_effect=UserError('HTTP 503')):
            prepared=await self.v3.prepare_unsubscribe(UnsubscribeSelection(identity=IDENTITY))
            result=await self.v3.unsubscribe(UnsubscribeSelection(identity=IDENTITY,confirmation=prepared['confirmation']))
            self.assertEqual(result['outcome'],'failed');self.assertEqual(self.v3.journal.actions(ACCOUNT)['items'][0]['outcome'],'failed')

    async def test_backlog_matching_protected_archived_and_no_models(self):
        self.v3.run.update(id='scan',status='running',account=ACCOUNT,identity=IDENTITY)
        self.gmail.request.return_value={'messages':[{'id':'1'},{'id':'2'},{'id':'3'}]}
        self.gmail.message.side_effect=[raw('1',labels=[]),raw('2',labels=['STARRED']),raw('3',list_id='another.example.org')]
        await self.v3.scan(self.gmail,record())
        self.assertEqual(set(self.v3.messages),{'1','2'})
        self.assertEqual(self.v3.messages['2']['protected'],['STARRED'])
        self.cleanup.classifier.classify.assert_not_called()
        self.assertNotIn('text',self.v3.messages['1'])

    async def test_cleanup_requires_unsubscribe_exact_confirmation_and_account(self):
        self.backlog()
        self.v3.journal.update(ACCOUNT,IDENTITY,status='active')
        with self.assertRaisesRegex(UserError,'Unsubscribe'):await self.v3.prepare_trash(Selection(ids=['1']))
        self.v3.journal.update(ACCOUNT,IDENTITY,status='request_accepted')
        prepared=await self.v3.prepare_trash(Selection(ids=['1']))
        self.assertEqual(prepared['count'],1)
        with self.assertRaisesRegex(UserError,'selection changed'):await self.v3.trash(ConfirmedSelection(ids=['2'],confirmation=prepared['confirmation']))
        prepared=await self.v3.prepare_trash(Selection(ids=['1']))
        self.gmail.request.return_value={'emailAddress':'other@example.net'}
        with self.assertRaisesRegex(UserError,'account changed'):await self.v3.trash(ConfirmedSelection(ids=['1'],confirmation=prepared['confirmation']))

    async def test_partial_trash_protection_retry_and_restore(self):
        self.backlog()
        self.gmail.message.side_effect=lambda i,metadata=False:raw(i,labels=['INBOX','STARRED'] if i=='2' else [])
        async def request(method,path,params=None):
            return {'emailAddress':ACCOUNT} if path=='profile' else {'id':'1','labelIds':['TRASH']}
        self.gmail.request.side_effect=request
        prepared=await self.v3.prepare_trash(Selection(ids=['1','2']))
        result=await self.v3.trash(ConfirmedSelection(ids=['1','2'],confirmation=prepared['confirmation']))
        self.assertEqual((result['succeeded'],result['failed']),(1,1))
        self.assertEqual(self.v3.journal.recoverable(ACCOUNT),{'1'})
        with self.assertRaises(UserError):await self.v3.prepare_trash(Selection(ids=['1']))
        with self.assertRaises(UserError):await self.v3.prepare_trash(Selection(ids=['2']))
        self.gmail.message.side_effect=lambda i,metadata=False:raw(i,labels=['TRASH'])
        prepared=await self.v3.prepare_restore(Selection(ids=['1']))
        result=await self.v3.restore(ConfirmedSelection(ids=['1'],confirmation=prepared['confirmation']))
        self.assertEqual(result['succeeded'],1);self.assertEqual(self.v3.journal.recoverable(ACCOUNT),set())
        self.assertEqual(self.cleanup.history.ids(ACCOUNT),{'1'})
        self.assertTrue(any(c.args[1]=='messages/1/untrash' for c in self.gmail.request.await_args_list))

    async def test_failed_trash_retry_subset_and_revalidation(self):
        self.backlog()
        async def request(method,path,params=None):
            if path=='profile':return {'emailAddress':ACCOUNT}
            if path=='messages/2/trash':raise UserError('Network result unknown')
            return {'labelIds':['TRASH']}
        self.gmail.request.side_effect=request
        prepared=await self.v3.prepare_trash(Selection(ids=['1','2']))
        result=await self.v3.trash(ConfirmedSelection(ids=['1','2'],confirmation=prepared['confirmation']))
        self.assertEqual(result['failed'],1)
        prepared=await self.v3.prepare_trash(Selection(ids=['2']))
        self.gmail.message.side_effect=lambda i,metadata=False:raw(i,list_id='other.example.org')
        result=await self.v3.trash(ConfirmedSelection(ids=['2'],confirmation=prepared['confirmation']))
        self.assertEqual(result['failed'],1)
        self.assertEqual(sum(c.args[1]=='messages/2/trash' for c in self.gmail.request.await_args_list),1)

    async def test_restore_state_already_restored_missing_partial_and_retry(self):
        for i in ('1','2','3'):self.v3.journal.event(ACCOUNT,'trash','succeeded',i)
        async def message(i,metadata=False):
            if i=='3':raise UserError('No longer available')
            return raw(i,labels=['TRASH'] if i=='1' else ['INBOX'])
        self.gmail.message.side_effect=message
        recovery=await self.v3.recoverable()
        self.assertEqual([m['eligible'] for m in recovery['items']],[True,False,False])
        prepared=await self.v3.prepare_restore(Selection(ids=['1','2','3']))
        result=await self.v3.restore(ConfirmedSelection(ids=['1','2','3'],confirmation=prepared['confirmation']))
        self.assertEqual((result['succeeded'],result['failed'],result['skipped']),(1,1,1))
        self.assertEqual(self.v3.journal.recoverable(ACCOUNT),{'3'})
        with self.assertRaises(UserError):await self.v3.prepare_restore(Selection(ids=['foreign']))

    async def test_clear_confirmation_keep_and_account_separation(self):
        await self.v3.preference(SubscriptionAction(identity=IDENTITY,action='keep'))
        self.assertTrue(self.v3.journal.subscription(ACCOUNT,IDENTITY)['keep'])
        self.assertEqual(self.v3.journal.subscription(ACCOUNT,IDENTITY)['status'],'active')
        with self.assertRaises(UserError):await self.v3.erase_inventory(EraseFeedback(confirmed=False))
        self.v3.journal.event(ACCOUNT,'trash','succeeded','1')
        await self.v3.erase_inventory(EraseFeedback(confirmed=True))
        self.assertEqual(self.v3.journal.recoverable(ACCOUNT),{'1'})
        self.v3.run.update(account=ACCOUNT,identity=IDENTITY,scanned=999,error='private')
        self.gmail.request.return_value={'emailAddress':'other@example.net'}
        state=await self.v3.state()
        self.assertEqual(state['subscriptions'],[]);self.assertEqual(state['messages'],[])
        self.assertEqual(state['run']['scanned'],0);self.assertIsNone(state['run']['error'])
        await self.v3.erase_actions(EraseFeedback(confirmed=True))
        self.assertEqual(self.v3.journal.recoverable(ACCOUNT),{'1'})

    async def test_concurrent_v2_and_v3_actions_are_blocked(self):
        self.v3.working=True
        with self.assertRaises(UserError):await self.cleanup.start()
        with self.assertRaises(UserError):await self.v3.start(DiscoveryOptions())
        self.v3.working=False;self.cleanup.writing=True
        with self.assertRaises(UserError):await self.v3.start(DiscoveryOptions())
        self.cleanup.writing=False
        await self.v3.start(DiscoveryOptions(limit=1))
        self.assertTrue(self.cleanup.busy())
        self.v3.stop_requested=True
        await self.v3.task
        self.assertFalse(self.cleanup.busy())

    async def test_successful_write_survives_journal_result_failure(self):
        self.backlog()
        with patch.object(self.v3.journal,'finish',side_effect=UserError('Disk error')):
            prepared=await self.v3.prepare_trash(Selection(ids=['1']))
            result=await self.v3.trash(ConfirmedSelection(ids=['1'],confirmation=prepared['confirmation']))
        self.assertEqual(result['succeeded'],1)
        self.assertEqual(self.v3.journal.recoverable(ACCOUNT),{'1'})

    def test_new_routes_boundary_and_no_permanent_delete(self):
        with TestClient(server.app,base_url=server.ORIGIN) as client:
            client.get('/')
            self.assertEqual(client.get('/subscriptions').status_code,200)
            self.assertEqual(client.get('/history').status_code,200)
            self.assertEqual(client.post('/api/v3/scan',json={'limit':1}).status_code,403)
            self.assertEqual(client.post('/api/v3/restore',headers={'Origin':server.ORIGIN},json={'ids':['1']}).status_code,422)
        self.assertFalse(any('delete' in r.path or getattr(r,'methods',set()) & {'DELETE'} for r in server.app.routes))

class PinnedConnectionTests(unittest.TestCase):
    def test_connects_to_validated_ip_but_verifies_original_tls_host(self):
        from app.unsubscribe import PinnedHTTPS
        context=Mock();sock=Mock()
        with patch('app.unsubscribe.ssl.create_default_context',return_value=context),patch('app.unsubscribe.socket.create_connection',return_value=sock) as connect:
            connection=PinnedHTTPS('example.org','8.8.8.8')
            connection.connect()
            connect.assert_called_once_with(('8.8.8.8',443),20)
            context.wrap_socket.assert_called_once_with(sock,server_hostname='example.org')
            self.assertIs(connection.sock,context.wrap_socket.return_value)
            context.wrap_socket.side_effect=OSError('TLS error')
            with self.assertRaises(OSError):connection.connect()
            sock.close.assert_called_once()

class InventoryRefreshTests(unittest.TestCase):
    def test_refresh_can_replace_trashed_latest_reference_with_older_live_mail(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal=Journal(Path(tmp)/'history/reviews.sqlite3')
            journal.observe(ACCOUNT,{**record(),'last_seen':2000,'reference':'new'},'new',scan_id='first')
            journal.update(ACCOUNT,IDENTITY,keep=True,status='request_accepted')
            journal.observe(ACCOUNT,{**record(),'last_seen':1000,'reference':'old'},'old',scan_id='second')
            journal.observe(ACCOUNT,{**record(),'last_seen':500,'reference':'older'},'older',scan_id='second')
            saved=journal.subscription(ACCOUNT,IDENTITY)
            self.assertEqual(saved['reference'],'old')
            self.assertTrue(saved['keep']);self.assertEqual(saved['status'],'request_accepted')
            self.assertEqual(saved['count'],3)
