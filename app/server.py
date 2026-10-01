import asyncio
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .services import ROOT, SCOPE, Classifier, TokenStore, UserError, client_config, fetch_messages, token_request

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
store = TokenStore()
classifier = Classifier()
session = secrets.token_urlsafe(32)
pending = {}
run = {'status': 'idle', 'completed': 0, 'total': 0, 'results': [], 'error': None}
task = None
ORIGIN = 'http://127.0.0.1:8765'


@app.middleware('http')
async def boundary(request: Request, call_next):
    if request.headers.get('host') != '127.0.0.1:8765':
        return JSONResponse({'error': 'Invalid host'}, status_code=403)
    if request.url.path.startswith('/api/'):
        if request.cookies.get('local_session') != session or (request.method != 'GET' and request.headers.get('origin') != ORIGIN):
            return JSONResponse({'error': 'Reload the local application to continue.'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    if not request.url.path.startswith(('/api/', '/oauth/')):
        response.set_cookie('local_session', session, httponly=True, samesite='strict')
    return response


@app.exception_handler(UserError)
async def user_error(request, exc):
    return JSONResponse({'error': str(exc)}, status_code=400)


@app.get('/api/status')
async def status():
    try:
        config = client_config()
        return {'configured': True, 'connected': bool(store.read(config['client_id']))}
    except UserError as exc:
        return {'configured': False, 'connected': False, 'error': str(exc)}


@app.post('/api/connect')
async def connect():
    config = client_config()
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    pending.clear()
    pending[state] = (verifier, time.monotonic(), config)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return {'url': 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': config['client_id'], 'redirect_uri': ORIGIN + '/oauth/callback',
        'response_type': 'code', 'scope': SCOPE, 'access_type': 'offline', 'prompt': 'consent',
        'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256', 'include_granted_scopes': 'false'})}


@app.get('/oauth/callback')
async def callback(request: Request):
    entry = pending.pop(request.query_params.get('state', ''), None)
    if not entry or time.monotonic() - entry[1] > 600:
        return RedirectResponse('/connect?error=expired')
    verifier, _, config = entry
    if request.query_params.get('error') or not request.query_params.get('code'):
        return RedirectResponse('/connect?error=denied')
    try:
        tokens = await token_request({**config, 'grant_type': 'authorization_code',
            'code': request.query_params['code'], 'code_verifier': verifier, 'redirect_uri': ORIGIN + '/oauth/callback'})
        if not tokens.get('refresh_token'):
            raise UserError('Google did not return persistent authorization. Reconnect Gmail.')
        store.write(config['client_id'], tokens)
    except Exception:
        return RedirectResponse('/connect?error=authorization')
    return RedirectResponse('/test')


async def analyze():
    try:
        config = client_config()
        saved = store.read(config['client_id'])
        if not saved:
            raise UserError('Connect Gmail before starting the test.')
        tokens = await token_request({**config, 'grant_type': 'refresh_token', 'refresh_token': saved['refresh_token']})
        store.write(config['client_id'], {**saved, **tokens})
        messages = await fetch_messages(tokens['access_token'])
        run['total'] = len(messages)
        for message in messages:
            decision = await asyncio.to_thread(classifier.classify, message)
            run['results'].append({k: v for k, v in message.items() if k != 'text'} | decision)
            run['completed'] += 1
        run['status'] = 'done'
    except UserError as exc:
        run.update(status='error', error=str(exc), results=[])
    except Exception:
        run.update(status='error', error='Analysis failed. Check your local runtime and network connection, then retry.', results=[])


@app.post('/api/test')
async def start_test():
    global task
    if task and not task.done():
        return {'started': False}
    run.update(status='running', completed=0, total=0, results=[], error=None)
    task = asyncio.create_task(analyze())
    return {'started': True}


@app.get('/api/run')
async def current_run():
    return run


@app.post('/api/disconnect')
async def disconnect():
    if task and not task.done():
        raise UserError('Wait for analysis to finish before disconnecting.')
    store.delete(client_config()['client_id'])
    run.update(status='idle', completed=0, total=0, results=[], error=None)
    return {'disconnected': True}


class SPA(StaticFiles):
    async def get_response(self, path, scope):
        if path in ('', '.', 'connect', 'test', 'results'):
            path = 'index.html'
        return await super().get_response(path, scope)


if (ROOT / 'frontend/dist').exists():
    app.mount('/', SPA(directory=ROOT / 'frontend/dist'), name='ui')
