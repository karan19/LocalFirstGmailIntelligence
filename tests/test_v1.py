import asyncio
import base64
import unittest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app import server
from app.services import CATEGORIES, Classifier, SCOPE, UserError, normalize


class V1Tests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app, base_url=server.ORIGIN)
        self.client.get('/')

    def test_product_routes_serve_ui(self):
        for path in ('/', '/connect', '/test', '/results'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn('<div id="root">', response.text)

    def test_api_requires_local_session_and_origin(self):
        stranger = TestClient(server.app, base_url=server.ORIGIN)
        self.assertEqual(stranger.get('/api/run').status_code, 403)
        self.assertEqual(self.client.post('/api/test').status_code, 403)
        self.assertEqual(self.client.get('/api/run', headers={'host': 'evil.example'}).status_code, 403)

    def test_oauth_only_readonly_with_pkce(self):
        with patch('app.server.client_config', return_value={'client_id': 'fixture', 'client_secret': 'secret'}):
            response = self.client.post('/api/connect', headers={'origin': server.ORIGIN})
        from urllib.parse import urlparse, parse_qs
        params = parse_qs(urlparse(response.json()['url']).query)
        self.assertEqual(params['scope'], [SCOPE])
        self.assertEqual(params['code_challenge_method'], ['S256'])
        self.assertNotIn('client_secret', params)
        self.assertEqual(self.client.get('/oauth/callback?state=wrong&code=fake', follow_redirects=False).headers['location'], '/connect?error=expired')

    def test_normalization_html_and_attachments(self):
        raw = '<style>hidden</style><p>Hello &amp; welcome</p><script>danger</script>'
        msg = {'id': 'a', 'internalDate': '42', 'payload': {'mimeType': 'text/html', 'body': {'data': base64.urlsafe_b64encode(raw.encode()).decode()}, 'headers': [{'name': 'Subject', 'value': 'Hello'}]}}
        result = normalize(msg)
        self.assertEqual(result['text'], 'Hello & welcome')
        self.assertEqual(result['received'], 42)
        msg['payload']['filename'] = 'attachment.html'
        self.assertEqual(normalize(msg)['text'], '')

    def test_classifier_validates_distribution_and_only_email_fields(self):
        classifier = Classifier()
        from unittest.mock import Mock
        classifier.agent = Mock()
        classifier.agent.predict.return_value = {'answers': {'category': {'probabilities': dict(zip(CATEGORIES, [.1, .2, .7]))}}}
        message = {'sender': 'sender', 'subject': 'subject', 'text': 'body', 'access_token': 'secret'}
        result = classifier.classify(message)
        self.assertEqual(result['category'], 'Promotional')
        self.assertNotIn('secret', classifier.agent.predict.call_args.args[0])
        classifier.agent.predict.return_value['answers']['category']['probabilities'][CATEGORIES[0]] = float('nan')
        with self.assertRaises(UserError):
            classifier.classify(message)

    def test_small_inbox_pipeline_keeps_body_out_of_results(self):
        with patch('app.server.client_config', return_value={'client_id':'fixture','client_secret':'fixture'}), patch.object(server.store, 'read', return_value={'refresh_token':'secret'}), patch.object(server.store,'write'), patch('app.server.token_request', new=AsyncMock(return_value={'access_token':'secret'})), patch('app.server.fetch_messages',new=AsyncMock(return_value=[{'id':'a','sender':'x','subject':'y','received':1,'text':'private body'}])), patch.object(server.classifier,'classify',return_value={'category':'Informational','probabilities':dict(zip(CATEGORIES,[.1,.8,.1]))}):
            server.run.update(results=[],completed=0)
            asyncio.run(server.analyze())
        self.assertEqual(server.run['status'],'done')
        self.assertEqual(server.run['total'],1)
        self.assertNotIn('text',server.run['results'][0])
        self.assertNotIn('secret',str(server.run))

    def test_empty_inbox_and_failure(self):
        with patch('app.server.client_config',return_value={'client_id':'x','client_secret':'x'}), patch.object(server.store,'read',return_value={'refresh_token':'x'}), patch.object(server.store,'write'), patch('app.server.token_request',new=AsyncMock(return_value={'access_token':'x'})), patch('app.server.fetch_messages',new=AsyncMock(return_value=[])):
            server.run.update(results=[],completed=0)
            asyncio.run(server.analyze())
        self.assertEqual(server.run['status'],'done')
        self.assertEqual(server.run['total'],0)
        with patch('app.server.client_config',side_effect=UserError('Setup needed')):
            asyncio.run(server.analyze())
        self.assertEqual(server.run['status'],'error')
        self.assertEqual(server.run['error'],'Setup needed')

    def test_gmail_uses_only_get_and_inbox_limit(self):
        import httpx
        from app.services import fetch_messages
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.path.endswith('/messages'):
                return httpx.Response(200, json={'messages': [{'id':'old'}, {'id':'new'}]})
            return httpx.Response(200, json={'id':request.url.path.rsplit('/',1)[1], 'internalDate': '2' if request.url.path.endswith('new') else '1', 'labelIds':['INBOX'], 'payload':{}})
        original = httpx.AsyncClient
        with patch('app.services.httpx.AsyncClient', side_effect=lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)):
            messages = asyncio.run(fetch_messages('fixture-token'))
        self.assertEqual([m['id'] for m in messages], ['new','old'])
        self.assertTrue(all(r.method == 'GET' for r in calls))
        self.assertEqual(calls[0].url.params['labelIds'], 'INBOX')
        self.assertEqual(calls[0].url.params['maxResults'], '10')
        self.assertEqual(calls[0].url.params['includeSpamTrash'], 'false')

    def test_oauth_callback_saves_tokens_not_in_redirect(self):
        config = {'client_id':'fixture','client_secret':'fixture'}
        with patch('app.server.client_config', return_value=config):
            self.client.post('/api/connect', headers={'origin':server.ORIGIN})
        state = next(iter(server.pending))
        with patch('app.server.token_request', new=AsyncMock(return_value={'refresh_token':'fixture-secret', 'access_token':'fixture-access'})), patch.object(server.store,'write') as write:
            response = self.client.get('/oauth/callback?state='+state+'&code=fixture', follow_redirects=False)
        self.assertEqual(response.headers['location'], '/test')
        write.assert_called_once()
        self.assertNotIn('fixture-secret', response.text)
        self.assertNotIn(state, server.pending)

    def test_v1_routes_preserved_without_delete_endpoint(self):
        paths = [r.path for r in server.app.routes]
        self.assertTrue(set(['/api/status','/api/connect','/api/test','/api/run','/api/disconnect']).issubset(paths))
        self.assertFalse(any('delete' in p for p in paths))

if __name__ == '__main__':
    unittest.main()
