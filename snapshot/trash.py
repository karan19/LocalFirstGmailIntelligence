"""Explicit, recoverable Gmail deletion of server-resolved cluster members."""
import asyncio
from urllib.parse import quote

import httpx

from .errors import diagnostic


async def trash_messages(gmail, members, progress):
    summary = {'status': 'running', 'moved': 0, 'already': 0, 'skipped': 0, 'failed': 0}
    progress(dict(summary))
    semaphore = asyncio.Semaphore(4)

    async def one(mid):
        async with semaphore:
            try:
                path = 'messages/' + quote(mid, safe='')
                value = await gmail.request(path, {'format': 'minimal', 'fields': 'id,labelIds'})
                if value.get('id') != mid:
                    raise ValueError('Unexpected message ID')
                labels = set(value.get('labelIds', []))
                if 'TRASH' in labels:
                    return 'already'
                if 'DRAFT' in labels:
                    return 'skipped'
                value = await gmail.post(path + '/trash', None)
                if value.get('id') != mid or 'TRASH' not in value.get('labelIds', []):
                    raise ValueError('Trash change not confirmed')
                return 'moved'
            except httpx.HTTPStatusError as error:
                return 'skipped' if error.response.status_code == 404 else 'failed'
            except Exception:
                return 'failed'

    ids = list(dict.fromkeys(members))
    for offset in range(0, len(ids), 25):
        results = await asyncio.gather(*(one(mid) for mid in ids[offset:offset + 25]))
        for outcome in results:
            summary[outcome] += 1
        progress(dict(summary))
        if summary['failed']:
            summary['status'] = 'error'
            progress(dict(summary))
            return summary
    summary['status'] = 'done'
    progress(dict(summary))
    return summary


async def bulk_trash_messages(gmail, members, progress):
    """Check each candidate; failures never authorize that message for Trash."""
    summary = {'status': 'running', 'moved': 0, 'already': 0, 'skipped': 0, 'failed': 0,
               'checked': 0, 'phase': 'checking', 'errors': {}}
    progress(dict(summary))
    semaphore = asyncio.Semaphore(4)

    async def check(mid):
        async with semaphore:
            try:
                value = await gmail.request('messages/' + quote(mid, safe=''), {'format': 'minimal', 'fields': 'id,labelIds'})
                if value.get('id') != mid:
                    raise ValueError('Unexpected message')
                labels = set(value.get('labelIds', []))
                outcome = 'already' if 'TRASH' in labels else 'skipped' if 'DRAFT' in labels else 'eligible'
                return mid, outcome, None
            except httpx.HTTPStatusError as error:
                return mid, 'skipped' if error.response.status_code == 404 else 'failed', diagnostic(error)
            except Exception as error:
                return mid, 'failed', diagnostic(error)

    async def checked_message(mid):
        result = await check(mid)
        summary['checked'] += 1
        if summary['checked'] % 25 == 0:
            progress(dict(summary))
        return result

    def record(error, count=1):
        key = error['code'] + (':' + str(error['http_status']) if error['http_status'] is not None else '')
        summary['errors'] = {**summary['errors'], key: summary['errors'].get(key, 0) + count}

    ids = list(dict.fromkeys(members))
    for offset in range(0, len(ids), 500):
        summary['phase'] = 'checking'
        progress(dict(summary))
        checked = await asyncio.gather(*(checked_message(mid) for mid in ids[offset:offset + 500]))
        eligible = [mid for mid, result, _ in checked if result == 'eligible']
        blocking = False
        for _, result, error in checked:
            if result != 'eligible':
                summary[result] += 1
            if result == 'failed':
                record(error)
                blocking |= error['code'] in {'authorization', 'permission', 'rate_limit', 'daily_limit', 'network', 'gmail_unavailable', 'unexpected'}
        if blocking:
            summary['status'] = 'error'
            progress(dict(summary))
            return summary
        # A per-message rejection doesn't prevent confirmed eligible messages
        # from progressing. Failed IDs are never included in the batch.
        try:
            if eligible:
                summary['phase'] = 'applying'
                progress(dict(summary))
                await gmail.post('messages/batchModify', {'ids': eligible, 'addLabelIds': ['TRASH'], 'removeLabelIds': ['INBOX']})
                summary['moved'] += len(eligible)
        except Exception as error:
            summary['status'] = 'error'
            summary['failed'] += len(eligible)
            record(diagnostic(error), len(eligible))
            progress(dict(summary))
            return summary
        progress(dict(summary))
    summary['status'] = 'error' if summary['failed'] else 'done'
    summary['phase'] = 'finished'
    progress(dict(summary))
    return summary
