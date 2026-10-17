"""Private entry point: OAuth, credentials, Gmail and originals stay here."""
import asyncio
import base64
import hashlib
import json
import os
import random
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from keyring.backends.macOS import Keyring
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .download import download
from .store import SnapshotStore
from .setup import import_existing_setup
from .errors import diagnostic, message as error_message
from .cleanup import Cleanup, Jev
from .quota import GmailQuota

SCOPE = 'https://www.googleapis.com/auth/gmail.modify'
ROOT = Path(__file__).resolve().parents[1]


class DownloadOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    category: str = 'updates'
    limit: int | None = Field(default=10, ge=1, le=100000, strict=True)


class RedactionOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    limit: int = Field(default=10, ge=1, le=100000, strict=True)


class TopicOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    limit: int = Field(default=100, ge=5, le=100000, strict=True)
    ignore_attachments: bool = Field(default=False, strict=True)


class LaterOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    topic: int = Field(ge=0, strict=True)


class CleanupAccept(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class CleanupLabel(CleanupAccept):
    label_name: str = Field(min_length=1, max_length=225, strict=True)
    remove_updates: bool = Field(default=False, strict=True)

    @field_validator('label_name')
    @classmethod
    def valid_label(cls, value):
        value = value.strip()
        reserved = {'INBOX', 'TRASH', 'SPAM', 'DRAFT', 'SENT', 'UNREAD', 'STARRED', 'IMPORTANT', 'CHAT', 'DRAFTS', 'ALL', 'ALL MAIL'}
        if not value or any(ord(c) < 32 or ord(c) == 127 for c in value) or value.upper() in reserved or value.upper().startswith('CATEGORY_'):
            raise ValueError('Choose a custom Gmail label name.')
        return value


class CleanupReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: str = Field(pattern=r'^(keep|delete-candidates|review)$')
    offset: int = Field(default=0, ge=0, le=100000, strict=True)


class GmailClient:
    def __init__(self, token, refresh=None):
        self.client = httpx.AsyncClient(timeout=60, trust_env=False, headers={'Authorization': 'Bearer ' + token})
        self.refresh = refresh
        self.refresh_lock = asyncio.Lock()
        self.quota = GmailQuota()

    async def request(self, path, params=None):
        return await self._request('GET', path, params=params)

    async def _request(self, method, path, *, params=None, payload=None, retry=True):
        for attempt in range(6 if retry else 1):
            try:
                await self.quota.wait(method, path)
                authorization = self.client.headers['Authorization']
                response = await self.client.request(method, 'https://gmail.googleapis.com/gmail/v1/users/me/' + path,
                                                     params=params, json=payload)
                if response.status_code == 401 and self.refresh:
                    async with self.refresh_lock:
                        if self.client.headers['Authorization'] == authorization:
                            self.client.headers['Authorization'] = 'Bearer ' + await self.refresh()
                    await self.quota.wait(method, path)
                    response = await self.client.request(method, 'https://gmail.googleapis.com/gmail/v1/users/me/' + path,
                                                         params=params, json=payload)
                rate_limited = response.status_code in (403, 429) and diagnostic(
                    httpx.HTTPStatusError('Gmail response', request=response.request, response=response))['code'] == 'rate_limit'
                if retry and (rate_limited or response.status_code in (500, 502, 503, 504)) and attempt < 5:
                    delay = 2 ** attempt + random.random()
                    try:
                        delay = max(delay, float(response.headers.get('Retry-After', '0')))
                    except ValueError:
                        pass
                    if delay <= 60:
                        if rate_limited:
                            self.quota.cooldown(delay)
                        await asyncio.sleep(delay)
                        continue
                response.raise_for_status()
                return response.json() if response.content else {}
            except httpx.TransportError:
                if not retry or attempt == 5:
                    raise
                await asyncio.sleep(2 ** attempt + random.random())

    async def post(self, path, payload, *, retry=True):
        return await self._request('POST', path, payload=payload, retry=retry)

    async def close(self):
        await self.client.aclose()


def private_path(value):
    path = Path(value).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Private paths must not use symlinks.')
    path = path.resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError('Private paths must be outside the project workspace.')
    return path


def create_app():
    # This is an explicit operator attestation, not an OS sandbox implementation.
    if os.environ.get('GMAIL_SNAPSHOT_ISOLATION_VERIFIED') != '1' and os.environ.get('GMAIL_SNAPSHOT_REGULAR_SESSION') != '1':
        raise RuntimeError('Verify protected-agent isolation with dummy fixtures before enabling the private service. See docs/snapshot.md.')
    capability = os.environ['GMAIL_SNAPSHOT_CAPABILITY']
    if len(capability) < 16:
        raise RuntimeError('Private API capability must be generated by the trusted launcher.')
    directory = private_path(os.environ['GMAIL_SNAPSHOT_DIRECTORY'])
    config_path = private_path(os.environ['GMAIL_SNAPSHOT_CLIENT_CONFIG'])
    origin = 'http://127.0.0.1:9000'
    ui_origin = 'http://127.0.0.1:8766'
    store = SnapshotStore(directory)
    cleanup = Cleanup(store)
    keyring = Keyring()
    if os.environ.get('GMAIL_SNAPSHOT_REUSE_MAIN_SETUP') == '1':
        source = os.environ.get('GMAIL_SNAPSHOT_MAIN_CLIENT_CONFIG', str(ROOT / '.local/google-client.json'))
        try:
            import_existing_setup(source, config_path, keyring,
                                  import_authorization=os.environ.get('GMAIL_SNAPSHOT_IMPORT_AUTHORIZATION', '1') == '1')
        except Exception:
            raise RuntimeError('Could not import the main Gmail setup. Check the client configuration and Keychain from the trusted terminal; no main credentials were changed.') from None
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    pending = {}
    state = {'task': None, 'account': None, 'job': None, 'error': None,
             'redaction_task': None, 'redaction': {'status': 'idle', 'processed': 0}, 'topic_task': None, 'label_task': None,
             'trash_task': None, 'trash': {'status': 'idle'},
             'cleanup_task': None, 'cleanup_error': None, 'cleanup_trash': {'status': 'idle'}, 'cleanup_label': {'status': 'idle'},
             'topic_progress': {}}
    stop = asyncio.Event()

    def config():
        data = json.loads(config_path.read_text())['installed']
        if any(not isinstance(data.get(k), str) or not data[k] for k in ('client_id', 'client_secret')):
            raise ValueError('invalid_client_configuration')
        return {k: data[k] for k in ('client_id', 'client_secret')}

    def saved():
        value = keyring.get_password('LocalFirstGmailSnapshot', config()['client_id'])
        return json.loads(value) if value else None

    def save(tokens):
        keyring.set_password('LocalFirstGmailSnapshot', config()['client_id'], json.dumps(tokens))

    async def tokens(data):
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.post('https://oauth2.googleapis.com/token', data=data)
            response.raise_for_status()
            result = response.json()
        if result.get('scope') and set(result['scope'].split()) != {SCOPE}:
            raise ValueError('unexpected_scope')
        return result

    async def connection():
        credentials = saved()
        if not credentials or SCOPE not in credentials.get('scope', '').split():
            raise ValueError('connect_first')
        async def refresh_access():
            refreshed = await tokens({**config(), 'grant_type': 'refresh_token', 'refresh_token': credentials['refresh_token']})
            credentials.update(refreshed)
            save(credentials)
            return refreshed['access_token']
        gmail = GmailClient(await refresh_access(), refresh=refresh_access)
        try:
            account = (await gmail.request('profile'))['emailAddress'].casefold()
        except Exception:
            await gmail.close()
            raise
        state['account'] = account
        return gmail, account

    def busy():
        return any(state[key] and not state[key].done() for key in ('task', 'redaction_task', 'topic_task', 'label_task', 'trash_task', 'cleanup_task'))

    def cleanup_status():
        return {**cleanup.summary(state['account'], state['job']),
                'configured': bool(os.environ.get('TYPESAFE_API_KEY') or (directory / 'jev-api-key.txt').is_file()),
                'error': state['cleanup_error'], 'trash': state['cleanup_trash'], 'label': state['cleanup_label']}

    def topic_status():
        run = store.latest_topics(state['account'], state['job']) if state['account'] and state['job'] else None
        if not run:
            return {'status': 'idle', 'processed': 0, 'groups': 0, 'ungrouped': 0}
        result = run['result'] or {}
        return {**(state['topic_progress'] if run['status'] == 'running' else {}),
                'status': run['status'], 'processed': result.get('processed', 0), 'skipped': result.get('skipped', 0),
                'ignore_attachments': result.get('ignore_attachments', False), 'excluded_attachments': result.get('excluded_attachments', 0),
                'seconds': result.get('seconds'), 'groups': sum(group['topic'] != -1 for group in result.get('groups', [])),
                'ungrouped': sum(group['count'] for group in result.get('groups', []) if group['topic'] == -1)}

    @app.middleware('http')
    async def boundary(request: Request, call_next):
        if request.headers.get('host') != '127.0.0.1:9000':
            return JSONResponse({'error': 'Invalid host.'}, status_code=403)
        if request.url.path.startswith('/api/') and not secrets.compare_digest(request.headers.get('x-snapshot-capability', ''), capability):
            return JSONResponse({'error': 'Private service access denied.'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.exception_handler(Exception)
    async def safe_error(request, exc):
        # Never serialize provider errors, credentials or mail into API responses.
        if request.url.path in ('/api/download', '/api/resume', '/api/refresh'):
            return JSONResponse({'error': error_message(diagnostic(exc))}, status_code=400)
        return JSONResponse({'error': 'Private service operation failed. Check connection or retry the saved download.'}, status_code=400)

    @app.get('/api/status')
    async def status():
        try:
            credentials = saved()
            connected = bool(credentials and SCOPE in credentials.get('scope', '').split())
        except Exception:
            return {'configured': False, 'connected': False, 'error': 'Private OAuth configuration or Keychain is unavailable.'}
        if state['account'] is None:
            return {'configured': True, 'connected': connected, 'stored': None, 'job': None,
                    'schema_version': 1, 'error': state['error'], 'redaction': state['redaction'], 'topics': topic_status(), 'cleanup': cleanup_status(), 'trash': state['trash'], 'labeling': bool(state['label_task'] and not state['label_task'].done())}
        return {'configured': True, 'connected': connected, **store.summary(state['account'], state['job']),
                'error': state['error'], 'redaction': state['redaction'], 'topics': topic_status(), 'cleanup': cleanup_status(), 'trash': state['trash'], 'labeling': bool(state['label_task'] and not state['label_task'].done())}

    async def run_topics(limit, jid, rid, ignore_attachments=False):
        process = None
        output_path = directory / 'topic-runs' / (rid + '.json')
        try:
            environment = dict(os.environ)
            environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'snapshot.topics', '--directory', str(directory), '--job-id', jid,
                '--run-id', rid, '--limit', str(limit), *(['--ignore-attachments'] if ignore_attachments else []), cwd=ROOT, env=environment,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            async for line in process.stdout:
                try:
                    value = json.loads(line)
                    if value.get('phase') in {'loading_model', 'embedding', 'grouping'} and type(value.get('encoded')) is int and type(value.get('total')) is int and 0 <= value['encoded'] <= value['total'] <= limit:
                        state['topic_progress'] = {k: value[k] for k in ('phase', 'encoded', 'total')}
                except (ValueError, TypeError):
                    pass
            await process.wait()
            if process.returncode != 0:
                raise ValueError('topic_run_failed')
            private_path(str(output_path))
            result = json.loads(output_path.read_text())
            if type(result.get('processed')) is not int or not 5 <= result['processed'] <= limit:
                raise ValueError('invalid_topic_result')
            store.finish_topic_run(rid, 'done', result)
        except asyncio.CancelledError:
            if process and process.returncode is None:
                process.terminate()
                await process.wait()
            store.finish_topic_run(rid, 'interrupted')
            raise
        except Exception:
            store.finish_topic_run(rid, 'error')
        finally:
            if output_path.exists() and not output_path.is_symlink():
                output_path.unlink()

    @app.post('/api/cluster')
    async def start_topics(options: TopicOptions):
        if busy():
            raise HTTPException(409, 'Another operation is running.')
        job = store.job(state['job']) if state['job'] else None
        if not job or job['account'] != state['account'] or job['status'] != 'done':
            raise HTTPException(400, 'Complete a download and refresh stored status first.')
        rid = store.create_topic_run(state['account'], job['id'])
        state['topic_progress'] = {'phase': 'starting', 'encoded': 0, 'total': 0}
        state['topic_task'] = asyncio.create_task(run_topics(options.limit, job['id'], rid, options.ignore_attachments))
        return {'started': True}

    @app.get('/api/topics')
    async def topic_results():
        # Original-text viewing is an explicit feature of regular-session mode.
        if os.environ.get('GMAIL_SNAPSHOT_REGULAR_SESSION') != '1':
            raise HTTPException(403, 'Original examples require regular-session mode.')
        run = store.latest_topics(state['account'], state['job']) if state['account'] and state['job'] else None
        if not run or run['status'] != 'done':
            return {'groups': [], 'status': run['status'] if run else 'idle'}
        from .topics import prepare
        groups = []
        for group in run['result']['groups']:
            examples = []
            for mid in group.get('members', group['examples']):
                original = store.original(state['account'], mid)
                if original:
                    subject, text = prepare(original)
                    examples.append({'subject': subject[:300], 'text': text[:1400],
                                     'received': int(original.get('internalDate', 0))})
            groups.append({'topic': group['topic'], 'count': group['count'], 'keywords': group['keywords'], 'examples': examples, 'later': group.get('later'), 'trash': group.get('trash')})
        return {'status': 'done', 'run_id': run['id'], 'groups': groups}

    @app.post('/api/later')
    async def later(options: LaterOptions):
        if busy():
            raise HTTPException(409, 'Another operation is running.')
        account, jid = state['account'], state['job']
        run = store.latest_topics(account, jid) if account and jid else None
        if not run or run['id'] != options.run_id or run['status'] != 'done':
            return JSONResponse({'error': 'This cluster view is outdated. View topic groups again.'}, status_code=409)
        group = next((group for group in run['result']['groups'] if group['topic'] == options.topic), None)
        if not group or not group.get('members'):
            raise HTTPException(400, 'Unknown cluster.')
        state['label_task'] = asyncio.current_task()
        gmail = None
        try:
            gmail, connected_account = await connection()
            if connected_account != account:
                state['job'] = None
                return JSONResponse({'error': 'Gmail account changed. Refresh stored status and view its groups.'}, status_code=409)
            from .labels import move_to_later
            def progress(summary):
                group['later'] = summary
                store.finish_topic_run(run['id'], 'done', run['result'])
            result = await move_to_later(gmail, group['members'], progress)
            return {'later': result}
        finally:
            if gmail:
                await gmail.close()
            state['label_task'] = None

    async def run_trash(gmail, run, group):
        def progress(summary):
            group['trash'] = summary
            state['trash'] = {**summary, 'run_id': run['id'], 'topic': group['topic']}
            store.finish_topic_run(run['id'], 'done', run['result'])
        try:
            from .trash import bulk_trash_messages
            await bulk_trash_messages(gmail, group['members'], progress)
        except asyncio.CancelledError:
            progress({**group.get('trash', {}), 'status': 'interrupted'})
            raise
        except Exception:
            state['trash'] = {**state['trash'], 'status': 'error'}
            try:
                progress({**group.get('trash', {}), 'status': 'error'})
            except Exception:
                pass
        finally:
            await gmail.close()

    @app.post('/api/trash')
    async def start_trash(options: LaterOptions):
        if busy():
            raise HTTPException(409, 'Another operation is running.')
        account, jid = state['account'], state['job']
        run = store.latest_topics(account, jid) if account and jid else None
        if not run or run['id'] != options.run_id or run['status'] != 'done':
            return JSONResponse({'error': 'This cluster view is outdated. View topic groups again.'}, status_code=409)
        group = next((group for group in run['result']['groups'] if group['topic'] == options.topic), None)
        if not group or not group.get('members'):
            raise HTTPException(400, 'Unknown cluster.')
        state['trash_task'] = asyncio.current_task()
        gmail = None
        try:
            gmail, connected_account = await connection()
            if connected_account != account:
                state['job'] = None
                return JSONResponse({'error': 'Gmail account changed. Refresh stored status and view its groups.'}, status_code=409)
            state['trash'] = {'status': 'running', 'run_id': run['id'], 'topic': group['topic'],
                              'moved': 0, 'already': 0, 'skipped': 0, 'failed': 0}
            state['trash_task'] = asyncio.create_task(run_trash(gmail, run, group))
            gmail = None  # The worker owns the connection now.
            return {'started': True}
        finally:
            if gmail:
                await gmail.close()
            if state['trash_task'] is asyncio.current_task():
                state['trash_task'] = None

    async def run_cleanup(rid, account, jid, key):
        jev = Jev(key)
        try:
            await cleanup.score(rid, account, jid, jev)
        except asyncio.CancelledError:
            cleanup.state(rid, 'interrupted')
            raise
        except Exception:
            cleanup.state(rid, 'error')
            state['cleanup_error'] = 'Jev scoring stopped. Check the TypeSafe key, quota, or connection, then resume scoring. Saved copies are retained.'
        finally:
            await jev.close()

    @app.post('/api/cleanup-score')
    async def start_cleanup():
        if busy():
            raise HTTPException(409, 'Another operation is running.')
        job = store.job(state['job']) if state['job'] else None
        if not job or job['status'] != 'done' or job['account'] != state['account']:
            raise HTTPException(400, 'Complete the download and refresh status first.')
        key = os.environ.get('TYPESAFE_API_KEY', '')
        if not key:
            path = private_path(str(directory / 'jev-api-key.txt'))
            try:
                key = path.read_text().strip()
            except OSError:
                pass
        if not key:
            return JSONResponse({'error': 'Save your TypeSafe key privately as jev-api-key.txt in the service folder, then retry.'}, status_code=400)
        run = cleanup.latest(state['account'], job['id'])
        if run and run['status'] in ('error', 'interrupted'):
            rid = run['id']
            cleanup.state(rid, 'running')
        else:
            rid = cleanup.create(state['account'], job['id'])
        state['cleanup_error'] = None
        state['cleanup_trash'] = {'status': 'idle'}
        state['cleanup_label'] = {'status': 'idle'}
        state['cleanup_task'] = asyncio.create_task(run_cleanup(rid, state['account'], job['id'], key))
        return {'started': True}

    @app.post('/api/cleanup-review')
    async def cleanup_review(options: CleanupReview):
        if os.environ.get('GMAIL_SNAPSHOT_REGULAR_SESSION') != '1':
            raise HTTPException(403, 'Original reviews require regular-session mode.')
        run = cleanup.latest(state['account'], state['job']) if state['account'] and state['job'] else None
        if not run:
            return {'examples': []}
        return {'examples': cleanup.review(run['id'], state['account'], options.decision, options.offset)}

    async def accept_cleanup(gmail, rid, members):
        def progress(summary):
            state['cleanup_trash'] = summary
        try:
            from .trash import bulk_trash_messages
            result = await bulk_trash_messages(gmail, members, progress)
            if result['status'] == 'done':
                cleanup.state(rid, 'accepted')
        except asyncio.CancelledError:
            state['cleanup_trash'] = {**state['cleanup_trash'], 'status': 'interrupted'}
            raise
        except Exception:
            state['cleanup_trash'] = {**state['cleanup_trash'], 'status': 'error'}
        finally:
            await gmail.close()

    @app.post('/api/cleanup-accept')
    async def start_accept_cleanup(options: CleanupAccept):
        if busy():
            raise HTTPException(409, 'Another operation is running.')
        account, jid = state['account'], state['job']
        run = cleanup.latest(account, jid) if account and jid else None
        if not run or run['id'] != options.run_id or run['status'] != 'done':
            return JSONResponse({'error': 'Only the current completed cleanup review can be accepted.'}, status_code=409)
        try:
            members = cleanup.verified_members(run['id'], account)
        except Exception:
            return JSONResponse({'error': 'Staged copies are missing, changed, or protected. Run scoring again before accepting Trash.'}, status_code=409)
        if not members:
            raise HTTPException(400, 'No delete candidates in this review.')
        state['cleanup_task'] = asyncio.current_task()
        gmail = None
        try:
            gmail, connected_account = await connection()
            if connected_account != account:
                state['job'] = None
                return JSONResponse({'error': 'Gmail account changed. Refresh stored status.'}, status_code=409)
            state['cleanup_trash'] = {'status': 'running', 'moved': 0, 'already': 0, 'skipped': 0, 'failed': 0}
            state['cleanup_task'] = asyncio.create_task(accept_cleanup(gmail, run['id'], members))
            gmail = None
            return {'started': True}
        finally:
            if gmail:
                await gmail.close()
            if state['cleanup_task'] is asyncio.current_task():
                state['cleanup_task'] = None

    async def label_cleanup(gmail, members, name, remove_updates):
        from .labels import bulk_label_messages
        def progress(summary):
            state['cleanup_label'] = summary
        try:
            await bulk_label_messages(gmail, members, name, progress, remove_updates)
        except asyncio.CancelledError:
            state['cleanup_label'] = {**state['cleanup_label'], 'status': 'interrupted'}
            raise
        except Exception:
            state['cleanup_label'] = {**state['cleanup_label'], 'status': 'error'}
        finally:
            await gmail.close()

    @app.post('/api/cleanup-label')
    async def start_label_cleanup(options: CleanupLabel):
        if busy():
            raise HTTPException(409, 'Another operation is running.')
        account, jid = state['account'], state['job']
        run = cleanup.latest(account, jid) if account and jid else None
        if not run or run['id'] != options.run_id or run['status'] != 'done':
            return JSONResponse({'error': 'Only the current completed cleanup review can be accepted.'}, status_code=409)
        try:
            members = cleanup.verified_members(run['id'], account)
        except Exception:
            return JSONResponse({'error': 'Staged copies are missing, changed, or protected. Run scoring again before applying a label.'}, status_code=409)
        if not members:
            raise HTTPException(400, 'No delete candidates in this review.')
        state['cleanup_task'] = asyncio.current_task()
        gmail = None
        try:
            gmail, connected_account = await connection()
            if connected_account != account:
                state['job'] = None
                return JSONResponse({'error': 'Gmail account changed. Refresh stored status.'}, status_code=409)
            state['cleanup_label'] = {'status': 'running', 'moved': 0, 'already': 0, 'skipped': 0, 'failed': 0}
            state['cleanup_task'] = asyncio.create_task(label_cleanup(gmail, members, options.label_name, options.remove_updates))
            gmail = None
            return {'started': True}
        finally:
            if gmail:
                await gmail.close()
            if state['cleanup_task'] is asyncio.current_task():
                state['cleanup_task'] = None

    async def run_redaction(limit, jid):
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'snapshot.redact', '--directory', str(directory),
                '--limit', str(limit), '--job-id', jid, '--json-summary', cwd=ROOT,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            output, _ = await process.communicate()
            if process.returncode != 0:
                raise ValueError('redaction_failed')
            summary = json.loads(output)
            if type(summary.get('processed')) is not int or not 0 <= summary['processed'] <= limit:
                raise ValueError('invalid_summary')
            state['redaction'] = {'status': 'done', 'processed': summary['processed'], 'seconds': summary['seconds']}
        except asyncio.CancelledError:
            if process and process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        except Exception:
            state['redaction'] = {'status': 'error', 'processed': 0,
                                  'error': 'Local redaction failed. Check model installation in the trusted terminal. Partial review copies may remain private.'}

    @app.post('/api/redact')
    async def start_redaction(options: RedactionOptions):
        if busy():
            raise HTTPException(409, 'Another operation is running.')
        job = store.job(state['job']) if state['job'] else None
        if not job or job['account'] != state['account']:
            raise HTTPException(400, 'Refresh stored status before redaction.')
        state['redaction'] = {'status': 'running', 'processed': 0}
        state['redaction_task'] = asyncio.create_task(run_redaction(options.limit, job['id']))
        return {'started': True}

    @app.post('/api/connect')
    async def connect():
        if busy():
            raise HTTPException(409, 'Pause the download before connecting.')
        cfg = config()
        oauth_state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        pending.clear()
        pending[oauth_state] = (verifier, time.monotonic())
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        return {'url': 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
            'client_id': cfg['client_id'], 'redirect_uri': origin + '/oauth/callback',
            'response_type': 'code', 'scope': SCOPE, 'access_type': 'offline', 'prompt': 'consent',
            'state': oauth_state, 'code_challenge': challenge, 'code_challenge_method': 'S256', 'include_granted_scopes': 'false'})}

    @app.get('/oauth/callback')
    async def callback(request: Request):
        entry = pending.pop(request.query_params.get('state', ''), None)
        try:
            if not entry or time.monotonic() - entry[1] > 600 or busy() or not request.query_params.get('code'):
                raise ValueError('invalid_callback')
            result = await tokens({**config(), 'grant_type': 'authorization_code', 'code': request.query_params['code'],
                                   'code_verifier': entry[0], 'redirect_uri': origin + '/oauth/callback'})
            if not result.get('refresh_token') or set(result.get('scope', '').split()) != {SCOPE}:
                raise ValueError('missing_authorization')
            save(result)
            state.update(account=None, job=None, error=None)
        except Exception:
            return RedirectResponse(ui_origin + '/?connection=failed')
        return RedirectResponse(ui_origin + '/?connection=success')

    async def run(gmail, jid):
        try:
            await download(store, gmail, jid, stop)
            if store.job(jid)['status'] == 'error':
                state['error'] = error_message(store.download_error(jid) or {'code': 'unexpected'})
        except Exception as error:
            value = diagnostic(error)
            state['error'] = error_message(value)
            try:
                store.state(jid, 'error')
                store.record_download_error(jid, value)
            except Exception:
                # Keep the visible diagnostic even if storage itself is unavailable.
                pass
        finally:
            await gmail.close()

    async def start_job(resume, options=None):
        if busy():
            raise HTTPException(409, 'A download is already running.')
        # Reserve before the first await, so simultaneous starts cannot overlap.
        state['task'] = asyncio.current_task()
        gmail = None
        try:
            gmail, account = await connection()
            if resume:
                job = store.latest_for(account)
                if not job or job['status'] == 'done':
                    raise ValueError('nothing_to_resume')
                jid = job['id']
            else:
                jid = store.create_job(account, options.limit)
            stop.clear()
            state.update(job=jid, error=None)
            state['task'] = asyncio.create_task(run(gmail, jid))
            return {'started': True}
        except Exception:
            if gmail:
                await gmail.close()
            state['task'] = None
            raise

    @app.post('/api/download')
    async def start(options: DownloadOptions):
        if options.category != 'updates':
            raise HTTPException(400, 'Only Updates is supported in this experiment.')
        return await start_job(False, options)

    @app.post('/api/resume')
    async def resume():
        return await start_job(True)

    @app.post('/api/pause')
    async def pause():
        stop.set()
        return {'pausing': True}

    @app.post('/api/refresh')
    async def refresh():
        if busy():
            return {'refreshed': False}
        gmail, account = await connection()
        await gmail.close()
        job = store.latest_for(account)
        state['job'] = job['id'] if job else None
        failure = store.download_error(job['id']) if job and job['status'] == 'error' else None
        state['error'] = error_message(failure) if failure else None
        cleanup_run = cleanup.latest(account, state['job']) if job else None
        if cleanup_run and cleanup_run['status'] == 'running':
            cleanup.state(cleanup_run['id'], 'interrupted')
        if job and job['status'] == 'running':
            store.state(job['id'], 'paused')
        run = store.latest_topics(account, state['job']) if job else None
        if run and run['status'] == 'done':
            changed = False
            for group in run['result']['groups']:
                if (group.get('trash') or {}).get('status') == 'running':
                    group['trash']['status'] = 'interrupted'
                    changed = True
            if changed:
                store.finish_topic_run(run['id'], 'done', run['result'])
        if run and run['status'] == 'running':
            store.finish_topic_run(run['id'], 'interrupted')
        return {'refreshed': True}

    return app
