"""Public UI entry point. No Gmail, Keychain or private-store imports."""
import os
import secrets
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'http://127.0.0.1:8766'


def create_app():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    session = secrets.token_urlsafe(32)
    capability = os.environ.get('GMAIL_SNAPSHOT_CAPABILITY', '')

    @app.middleware('http')
    async def boundary(request: Request, call_next):
        if request.headers.get('host') != '127.0.0.1:8766':
            return JSONResponse({'error': 'Invalid host.'}, status_code=403)
        if request.url.path.startswith('/api/'):
            if request.cookies.get('snapshot_session') != session or (request.method != 'GET' and request.headers.get('origin') != ORIGIN):
                return JSONResponse({'error': 'Reload the snapshot application.'}, status_code=403)
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                 'X-Content-Type-Options': 'nosniff',
                                 'Content-Security-Policy': "default-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"})
        if not request.url.path.startswith('/api/'):
            response.set_cookie('snapshot_session', session, httponly=True, samesite='strict')
        return response

    @app.api_route('/api/snapshot/{operation}', methods=['GET', 'POST'])
    async def proxy(operation: str, request: Request):
        allowed = {'status': 'GET', 'connect': 'POST', 'download': 'POST', 'pause': 'POST', 'resume': 'POST', 'refresh': 'POST', 'redact': 'POST', 'cluster': 'POST', 'topics': 'GET', 'later': 'POST', 'trash': 'POST', 'cleanup-score': 'POST', 'cleanup-review': 'POST', 'cleanup-accept': 'POST', 'cleanup-label': 'POST'}
        if allowed.get(operation) != request.method:
            return JSONResponse({'error': 'Unsupported snapshot operation.'}, status_code=404)
        if not capability:
            return JSONResponse({'error': 'Private service is disabled. Complete the isolation check and launch it from your trusted terminal.'}, status_code=503)
        try:
            payload = await request.json() if operation in ('download', 'redact', 'cluster', 'later', 'trash', 'cleanup-review', 'cleanup-accept', 'cleanup-label') else None
            async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
                response = await client.request(request.method, 'http://127.0.0.1:9000/api/' + operation,
                                                headers={'x-snapshot-capability': capability}, json=payload)
            data = response.json()
            if operation == 'topics' and response.is_success:
                groups = []
                for group in data.get('groups', []):
                    groups.append({k: group[k] for k in ('topic', 'count', 'keywords')})
                    if isinstance(group.get('later'), dict):
                        groups[-1]['later'] = {k: v for k, v in group['later'].items() if k in {'status', 'moved', 'already', 'skipped'}}
                    if isinstance(group.get('trash'), dict):
                        groups[-1]['trash'] = {k: v for k, v in group['trash'].items() if k in {'status', 'moved', 'already', 'skipped', 'failed', 'checked', 'phase', 'errors'}}
                    groups[-1]['examples'] = [{k: value for k, value in example.items() if k in {'subject', 'text', 'received'}}
                                              for example in group.get('examples', [])]
                return {'status': data.get('status'), 'run_id': data.get('run_id'), 'groups': groups}
            if operation == 'later' and response.is_success:
                return {'later': {k: v for k, v in data.get('later', {}).items() if k in {'status', 'moved', 'already', 'skipped'}}}
            if operation == 'cleanup-review' and response.is_success:
                return {'examples': [{k: v for k, v in example.items() if k in {'subject', 'text', 'score', 'category'}} for example in data.get('examples', [])]}
            # Defense in depth: don't forward arbitrary service fields or detail errors.
            allowed_fields = {'configured', 'connected', 'stored', 'schema_version', 'oldest', 'newest', 'job', 'error', 'labeling'} if operation == 'status' else {'started', 'pausing', 'refreshed', 'error'}
            if operation == 'connect' and response.is_success and isinstance(data.get('url'), str) and data['url'].startswith('https://accounts.google.com/o/oauth2/v2/auth?'):
                return {'url': data['url']}
            result = {k: v for k, v in data.items() if k in allowed_fields and k != 'job'}
            if operation == 'status' and isinstance(data.get('redaction'), dict):
                result['redaction'] = {k: v for k, v in data['redaction'].items() if k in {'status', 'processed', 'seconds', 'error'}}
            if operation == 'status' and isinstance(data.get('topics'), dict):
                result['topics'] = {k: v for k, v in data['topics'].items() if k in {'status', 'processed', 'skipped', 'seconds', 'groups', 'ungrouped', 'ignore_attachments', 'excluded_attachments', 'phase', 'encoded', 'total'}}
            if operation == 'status' and isinstance(data.get('trash'), dict):
                result['trash'] = {k: v for k, v in data['trash'].items() if k in {'status', 'moved', 'already', 'skipped', 'failed', 'checked', 'phase', 'errors', 'run_id', 'topic'}}
            if operation == 'status' and isinstance(data.get('cleanup'), dict):
                result['cleanup'] = {k: v for k, v in data['cleanup'].items() if k in {'id', 'status', 'processed', 'keep', 'candidates', 'review', 'configured', 'error'}}
                result['cleanup']['trash'] = {k: v for k, v in data['cleanup'].get('trash', {}).items() if k in {'status', 'moved', 'already', 'skipped', 'failed', 'checked', 'phase', 'errors'}}
                result['cleanup']['label'] = {k: v for k, v in data['cleanup'].get('label', {}).items() if k in {'status', 'moved', 'already', 'skipped', 'failed', 'checked', 'phase', 'errors'}}
            if operation == 'status' and 'job' in data:
                fields = {'id', 'status', 'requested', 'listed', 'saved', 'reused', 'failed', 'skipped', 'pending', 'listing_exhausted', 'coverage_complete'}
                result['job'] = {k: v for k, v in data['job'].items() if k in fields} if isinstance(data['job'], dict) else None
            if not response.is_success and 'error' not in result:
                result['error'] = 'The private service declined this operation. Refresh connection status and retry.'
            return JSONResponse(result, status_code=response.status_code)
        except Exception:
            return JSONResponse({'error': 'Private service is unavailable. Check its trusted-terminal launch.'}, status_code=503)

    class SPA(StaticFiles):
        async def get_response(self, path, scope):
            if path in ('', '.', 'cleanup'):
                path = 'index.html'
            return await super().get_response(path, scope)

    app.mount('/', SPA(directory=ROOT / 'frontend/dist'), name='ui')
    return app
