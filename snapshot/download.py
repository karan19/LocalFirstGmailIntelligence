"""Bounded, durable Updates downloader."""
from urllib.parse import quote
import httpx

from .errors import diagnostic


async def download(store, gmail, jid, stop):
    job = store.job(jid)
    if (await gmail.request('profile'))['emailAddress'].casefold() != job['account']:
        raise ValueError('account_changed')
    store.state(jid, 'running')
    try:
        while not stop.is_set():
            for mid in store.unfinished(jid):
                if stop.is_set():
                    break
                if store.cached(job['account'], mid):
                    store.outcome(jid, mid, 'reused')
                    continue
                try:
                    message = await gmail.request('messages/' + quote(mid, safe=''), {'format': 'raw'})
                    if message.get('id') != mid or not isinstance(message.get('raw'), str):
                        raise ValueError('invalid_message')
                    labels = set(message.get('labelIds', []))
                    if 'CATEGORY_UPDATES' not in labels or labels & {'SPAM', 'TRASH'}:
                        store.outcome(jid, mid, 'skipped')
                    else:
                        store.outcome(jid, mid, 'saved', message)
                except Exception as error:
                    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 404:
                        store.outcome(jid, mid, 'skipped')
                        continue
                    store.outcome(jid, mid, 'failed')
                    store.record_download_error(jid, diagnostic(error))
                    store.state(jid, 'error')
                    return
            if stop.is_set():
                break
            job = store.job(jid)
            listed = store.listed(jid)
            if job['exhausted'] or (job['requested'] is not None and listed >= job['requested']):
                store.state(jid, 'done')
                return
            size = min(100, job['requested'] - listed) if job['requested'] is not None else 100
            params = {'q': 'category:updates', 'includeSpamTrash': 'false', 'maxResults': size}
            if job['page']:
                params['pageToken'] = job['page']
            page = await gmail.request('messages', params)
            ids = list(dict.fromkeys(m['id'] for m in page.get('messages', [])))[:size]
            cursor = page.get('nextPageToken')
            if cursor and cursor == job['page']:
                raise ValueError('stalled_pagination')
            store.page(jid, ids, cursor)
        store.state(jid, 'paused')
    except Exception:
        store.state(jid, 'error')
        raise
