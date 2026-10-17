"""Explicit cluster label changes; no mail bodies or browser-supplied IDs."""
import asyncio
from urllib.parse import quote

import httpx


async def move_to_later(gmail, members, progress):
    summary = {'status': 'running', 'moved': 0, 'already': 0, 'skipped': 0}
    progress(dict(summary))
    try:
        labels = (await gmail.request('labels')).get('labels', [])
        later = next((label['id'] for label in labels
                      if label.get('type') == 'user' and label.get('name') == 'Later'), None)
        semaphore = asyncio.Semaphore(8)

        async def check(mid):
            async with semaphore:
                try:
                    value = await gmail.request('messages/' + quote(mid, safe=''),
                                                {'format': 'minimal', 'fields': 'id,labelIds'})
                except httpx.HTTPStatusError as error:
                    if error.response.status_code == 404:
                        return mid, 'skipped'
                    raise
                if value.get('id') != mid:
                    raise ValueError('Unexpected message ID')
                current = set(value.get('labelIds', []))
                if current & {'SPAM', 'TRASH', 'DRAFT'}:
                    return mid, 'skipped'
                if 'CATEGORY_UPDATES' in current:
                    return mid, 'eligible'
                return mid, 'already' if later and later in current else 'skipped'

        checked = await asyncio.gather(*(check(mid) for mid in dict.fromkeys(members)))
        eligible = [mid for mid, status in checked if status == 'eligible']
        summary.update(already=sum(status == 'already' for _, status in checked),
                       skipped=sum(status == 'skipped' for _, status in checked))
        if eligible and not later:
            # Don't blindly retry creation: a lost response may have created it.
            try:
                label = await gmail.post('labels', {'name': 'Later', 'labelListVisibility': 'labelShow',
                                                    'messageListVisibility': 'show'}, retry=False)
                later = label['id']
            except Exception:
                labels = (await gmail.request('labels')).get('labels', [])
                later = next((label['id'] for label in labels
                              if label.get('type') == 'user' and label.get('name') == 'Later'), None)
                if not later:
                    raise
        for offset in range(0, len(eligible), 1000):
            batch = eligible[offset:offset + 1000]
            await gmail.post('messages/batchModify', {'ids': batch, 'addLabelIds': [later],
                                                     'removeLabelIds': ['CATEGORY_UPDATES']})
            summary['moved'] += len(batch)
            progress(dict(summary))
        summary['status'] = 'done'
    except Exception:
        # A provider failure may follow a successful write. Retrying rechecks Gmail.
        summary['status'] = 'error'
    progress(dict(summary))
    return summary


async def bulk_label_messages(gmail, members, label_name, progress, remove_updates=False):
    """Apply a user label to verified candidates in bounded, resumable batches."""
    summary = dict(status='running', moved=0, already=0, skipped=0, failed=0)
    progress(dict(summary))
    try:
        labels = (await gmail.request('labels')).get('labels', [])
        target = next((v['id'] for v in labels if v.get('type') == 'user' and v.get('name') == label_name), None)
        if not target:
            try:
                target = (await gmail.post('labels', {'name': label_name, 'labelListVisibility': 'labelShow', 'messageListVisibility': 'show'}, retry=False))['id']
            except Exception:
                labels = (await gmail.request('labels')).get('labels', [])
                target = next((v['id'] for v in labels if v.get('type') == 'user' and v.get('name') == label_name), None)
                if not target:
                    raise
        semaphore = asyncio.Semaphore(8)
        async def check(mid):
            async with semaphore:
                try:
                    value = await gmail.request('messages/' + quote(mid, safe=''), {'format': 'minimal', 'fields': 'id,labelIds'})
                    if value.get('id') != mid:
                        raise ValueError('Unexpected message')
                    current = set(value.get('labelIds', []))
                    if current & {'TRASH', 'SPAM', 'DRAFT'}:
                        return mid, 'skipped'
                    if target in current and (not remove_updates or 'CATEGORY_UPDATES' not in current):
                        return mid, 'already'
                    return mid, 'eligible'
                except httpx.HTTPStatusError as error:
                    return mid, 'skipped' if error.response.status_code == 404 else 'failed'
                except Exception:
                    return mid, 'failed'
        ids = list(dict.fromkeys(members))
        for offset in range(0, len(ids), 500):
            checked = await asyncio.gather(*(check(mid) for mid in ids[offset:offset+500]))
            eligible = [mid for mid, outcome in checked if outcome == 'eligible']
            for _, outcome in checked:
                if outcome != 'eligible':
                    summary[outcome] += 1
            if summary['failed']:
                raise ValueError('Message checks failed')
            if eligible:
                try:
                    await gmail.post('messages/batchModify', {'ids': eligible, 'addLabelIds': [target], 'removeLabelIds': ['CATEGORY_UPDATES'] if remove_updates else []})
                except Exception:
                    summary['failed'] += len(eligible)
                    raise
                summary['moved'] += len(eligible)
            progress(dict(summary))
        summary['status'] = 'done'
    except Exception:
        summary['status'] = 'error'
    progress(dict(summary))
    return summary
