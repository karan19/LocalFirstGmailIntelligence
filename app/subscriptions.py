"""V3 subscription discovery, explicitly confirmed cleanup and durable recovery."""
import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field
from .cleanup import Selection, ConfirmedSelection, EraseFeedback, PROTECTED, GmailAuthorizationError
from .journal import Journal
from .services import UserError, normalize
from .unsubscribe import parse, matching, capability, post_one_click


class DiscoveryOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    limit: int = Field(default=1000, ge=1, le=10000, strict=True)
    identity: str | None = None
    broader: bool = False


class SubscriptionAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    identity: str
    action: Literal['keep', 'unkeep', 'manual_completed']


class UnsubscribeSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    identity: str
    confirmation: str | None = None


class Subscriptions:
    def __init__(self, app, cleanup):
        self.cleanup = cleanup
        self.journal = Journal(cleanup.history.path)
        cleanup.journal = self.journal
        cleanup.external_busy = self.busy
        self.task = None
        self.working = False
        self.reset()
        for path, method, handler in [
            ('state', 'GET', self.state), ('scan', 'POST', self.start), ('stop', 'POST', self.stop),
            ('subscription', 'POST', self.preference), ('prepare-unsubscribe', 'POST', self.prepare_unsubscribe),
            ('unsubscribe', 'POST', self.unsubscribe), ('prepare-trash', 'POST', self.prepare_trash),
            ('trash', 'POST', self.trash), ('history', 'GET', self.history), ('recoverable', 'GET', self.recoverable),
            ('prepare-restore', 'POST', self.prepare_restore), ('restore', 'POST', self.restore),
            ('erase-inventory', 'POST', self.erase_inventory), ('erase-actions', 'POST', self.erase_actions)]:
            app.add_api_route('/api/v3/' + path, handler, methods=[method])

    def reset(self):
        self.run = self.empty_run()
        self.messages = {}
        self.confirmations = {}
        self.stop_requested = False

    @staticmethod
    def empty_run():
        return {'id': None, 'status': 'idle', 'scanned': 0, 'listed': 0, 'matched': 0, 'skipped': 0,
                    'estimate': None, 'complete': False, 'remaining_estimate': None, 'error': None,
                    'account': None, 'identity': None, 'broader': False, 'limit': 1000}

    def busy(self):
        return self.working or bool(self.task and not self.task.done())

    def idle(self):
        if self.cleanup.busy() or self.cleanup.legacy_busy():
            raise UserError('Wait for the current scan or mailbox operation to finish.')

    @asynccontextmanager
    async def connection(self, expected=None):
        gmail = await self.cleanup.authorize()
        try:
            account = (await gmail.request('GET', 'profile'))['emailAddress']
            if expected and account.casefold() != expected.casefold():
                raise UserError('Connected Gmail account changed. Refresh and confirm again.')
            yield gmail, account
        finally:
            await gmail.close()

    def record(self, account, identity):
        record = self.journal.subscription(account, identity)
        if not record:
            raise UserError('Subscription unavailable. Refresh or scan again.')
        return record

    def token(self, kind, account, value):
        token = secrets.token_urlsafe(24)
        self.confirmations[token] = (kind, account, value, time.monotonic())
        return token

    def consume(self, token, kind, value):
        entry = self.confirmations.pop(token or '', None)
        if not entry or entry[0] != kind or entry[2] != value or time.monotonic() - entry[3] > 300:
            raise UserError('Confirmation expired or selection changed. Review and confirm again.')
        return entry[1]

    async def state(self):
        async with self.connection() as (_, account):
            same = self.run['account'] and account.casefold() == self.run['account'].casefold()
            return {'account': account, 'subscriptions': self.journal.inventory(account),
                    'run': self.run if same else self.empty_run(),
                    'messages': sorted(self.messages.values(), key=lambda m: m['received'], reverse=True) if same else []}

    async def start(self, payload: DiscoveryOptions):
        self.idle()
        self.working = True
        gmail = None
        try:
            gmail = await self.cleanup.authorize()
            account = (await gmail.request('GET', 'profile'))['emailAddress']
            record = self.record(account, payload.identity) if payload.identity else None
            self.reset()
            self.run.update(id=secrets.token_urlsafe(18), status='running', account=account,
                            identity=payload.identity, broader=payload.broader, limit=payload.limit)
            self.task = asyncio.create_task(self.scan(gmail, record))
            gmail = None # task owns connection
            return {'run_id': self.run['id']}
        finally:
            if gmail:
                await gmail.close()
            self.working = False

    async def stop(self):
        if self.run['status'] == 'running':
            self.stop_requested = True
            self.run['status'] = 'stopping'
        return {'stopping': self.stop_requested}

    async def scan(self, gmail, record=None):
        page, seen, exhausted = None, set(), False
        try:
            while self.run['listed'] < self.run['limit'] and not self.stop_requested:
                query = '-in:spam -in:trash -in:sent -in:drafts'
                if record:
                    value = record['list_id'] or record['senders'][0]
                    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
                    query += (' list:' if record['list_id'] else ' from:') + '"' + escaped + '"'
                params = {'q': query, 'includeSpamTrash': 'false', 'maxResults': min(500, self.run['limit'] - self.run['listed'])}
                if page:
                    params['pageToken'] = page
                listing = await gmail.request('GET', 'messages', params)
                if self.run['estimate'] is None:
                    estimate = listing.get('resultSizeEstimate')
                    self.run['estimate'] = estimate if isinstance(estimate, int) and estimate >= 0 else None
                ids = [item['id'] for item in listing.get('messages', []) if item['id'] not in seen][:self.run['limit'] - self.run['listed']]
                seen.update(ids)
                self.run['listed'] += len(ids)
                for offset in range(0, len(ids), 4):
                    if self.stop_requested:
                        break
                    outcomes = await asyncio.gather(*(self.inspect(gmail, message_id, record) for message_id in ids[offset:offset + 4]), return_exceptions=True)
                    for result in outcomes:
                        if isinstance(result, GmailAuthorizationError):
                            raise result
                        if isinstance(result, BaseException):
                            self.run['skipped'] += 1
                next_page = listing.get('nextPageToken')
                if not next_page:
                    exhausted = True
                    break
                if next_page == page:
                    raise UserError('Gmail returned a repeated page. Partial results are retained; retry the scan.')
                page = next_page
            self.run['complete'] = exhausted and not self.stop_requested and self.run['skipped'] == 0
            self.run['status'] = 'stopped' if self.stop_requested else 'done'
        except UserError as exc:
            self.run.update(status='error', error=str(exc))
        except Exception:
            self.run.update(status='error', error='Subscription scan failed. Partial results are retained; retry after checking Gmail.')
        finally:
            if self.run['estimate'] is not None:
                self.run['remaining_estimate'] = max(0, self.run['estimate'] - self.run['scanned'])
            await gmail.close()
            try:
                self.cleanup.intelligence.record_run(self.run, 'backlog' if record else 'discovery')
            except UserError:
                self.run['error'] = 'Scan retained, but dashboard summary could not be saved.'

    async def inspect(self, gmail, message_id, subscription):
        raw = await gmail.message(message_id, metadata=True)
        self.run['scanned'] += 1
        labels = set(raw.get('labelIds', []))
        if labels & {'SPAM', 'TRASH', 'DRAFT', 'SENT'}:
            return
        record = parse(raw)
        if subscription:
            if not matching(raw, subscription, self.run['broader']):
                return
            message = normalize(raw)
            message.pop('text', None)
            message.update(protected=sorted(labels & PROTECTED), trash_status=None)
            self.messages[message_id] = message
            self.run['matched'] += 1
            if matching(raw, subscription):
                self.journal.track_match(self.run['account'], subscription['identity'], message_id)
        elif record and record['evidence']:
            method = await capability(gmail, record)
            persisted = {k: record[k] for k in ('identity', 'list_id', 'name', 'senders', 'last_seen', 'reference')}
            persisted['method'] = method
            self.journal.observe(self.run['account'], persisted, message_id, scan_id=self.run['id'])
            self.run['matched'] += 1

    async def preference(self, payload: SubscriptionAction):
        self.idle()
        self.working = True
        try:
            async with self.connection() as (_, account):
                self.record(account, payload.identity)
                if payload.action == 'manual_completed':
                    self.journal.event(account, 'unsubscribe', 'manual_completed', identity=payload.identity, detail='User reports manual unsubscribe completed; not independently verified.')
                    self.journal.update(account, payload.identity, status='manual_completed')
                else:
                    self.journal.update(account, payload.identity, keep=payload.action == 'keep')
                return {'updated': True}
        finally:
            self.working = False

    async def prepare_unsubscribe(self, payload: UnsubscribeSelection):
        self.idle()
        self.working = True
        try:
            async with self.connection() as (gmail, account):
                subscription = self.record(account, payload.identity)
                raw = await gmail.message(subscription['reference'], metadata=True)
                record = parse(raw)
                if not record or not matching(raw, subscription) or set(raw.get('labelIds', [])) & {'SPAM', 'TRASH'}:
                    raise UserError('Unsubscribe source message changed or is unavailable. Rescan subscriptions.')
                method = await capability(gmail, record)
                self.journal.update(account, payload.identity, method=method)
                if not record['url']:
                    self.journal.event(account, 'unsubscribe', 'manual_required', identity=payload.identity, detail='No supported unsubscribe link available.')
                    return {'method': 'unknown', 'identity': payload.identity, 'name': subscription['name'], 'sender': record['sender']}
                token = self.token('unsubscribe', account, (payload.identity, record['reference'], record['url'], method))
                return {'confirmation': token, 'identity': payload.identity, 'name': subscription['name'], 'sender': record['sender'], 'method': method,
                        'url': record['url'], 'host': urlsplit(record['url']).hostname}
        finally:
            self.working = False

    async def unsubscribe(self, payload: UnsubscribeSelection):
        self.idle()
        entry = self.confirmations.get(payload.confirmation)
        if not entry or entry[2][0] != payload.identity:
            raise UserError('Review and confirm this unsubscribe first.')
        value = entry[2]
        account = self.consume(payload.confirmation, 'unsubscribe', value)
        self.working = True
        try:
            async with self.connection(account) as (gmail, _):
                subscription = self.record(account, payload.identity)
                raw = await gmail.message(value[1], metadata=True)
                record = parse(raw)
                if not record or not matching(raw, subscription) or set(raw.get('labelIds', [])) & {'SPAM', 'TRASH'}:
                    raise UserError('Subscription source changed. Rescan and confirm again.')
                if record['url'] != value[2]:
                    raise UserError('Unsubscribe destination changed. Review and confirm again.')
                if value[3] != 'one_click':
                    self.journal.event(account, 'unsubscribe', 'manual_required', identity=payload.identity, detail='Manual completion required; no request sent by the app.')
                    self.journal.update(account, payload.identity, status='manual_required')
                    return {'outcome': 'manual_required', 'url': value[2]}
                if await capability(gmail, record) != 'one_click':
                    raise UserError('One-click authentication could not be verified. Use the manual path.')
                event = self.journal.event(account, 'unsubscribe', 'pending', identity=payload.identity, detail='Confirmed request started; completion unknown until a result is recorded.')
                self.journal.update(account, payload.identity, status='unknown')
                try:
                    status = await asyncio.to_thread(post_one_click, record['url'])
                except UserError as exc:
                    self.journal.finish(event, 'failed', str(exc))
                    self.journal.update(account, payload.identity, status='failed')
                    return {'outcome': 'failed', 'error': str(exc)}
                detail = 'Request accepted; unsubscribe not verified.'
                try:
                    self.journal.finish(event, 'request_accepted', f'HTTP {status}; unsubscribe not verified.')
                    self.journal.update(account, payload.identity, status='request_accepted')
                except UserError:
                    detail += ' Local result could not be saved; verify state before retrying.'
                return {'outcome': 'request_accepted', 'detail': detail}
        except UserError as exc:
            try:
                self.journal.event(account, 'unsubscribe', 'failed', identity=payload.identity, detail=str(exc))
            except UserError:
                pass
            raise
        finally:
            self.working = False

    def selected(self, ids):
        if not self.run['identity'] or self.run['status'] not in ('done', 'stopped', 'error') or len(set(ids)) != len(ids):
            raise UserError('Finish a subscription backlog scan and select each message once.')
        messages = [self.messages.get(i) for i in ids]
        if any(not m or m['protected'] or m.get('trash_status') == 'succeeded' for m in messages):
            raise UserError('Selection contains unavailable, already trashed or protected messages. Refresh your selection.')
        return messages

    async def prepare_trash(self, payload: Selection):
        self.idle()
        self.working = True
        try:
            self.selected(payload.ids)
            async with self.connection(self.run['account']) as (_, account):
                record = self.record(account, self.run['identity'])
                if record['status'] not in ('request_accepted', 'manual_completed', 'unsubscribed'):
                    raise UserError('Unsubscribe or mark manual unsubscribe completed before backlog cleanup.')
                token = self.token('trash', account, (self.run['id'], tuple(payload.ids)))
                return {'confirmation': token, 'count': len(payload.ids)}
        finally:
            self.working = False

    async def trash(self, payload: ConfirmedSelection):
        self.idle()
        messages = self.selected(payload.ids)
        account = self.consume(payload.confirmation, 'trash', (self.run['id'], tuple(payload.ids)))
        self.working = True
        try:
            async with self.connection(account) as (gmail, _):
                subscription = self.record(account, self.run['identity'])
                outcomes = []
                for message in messages:
                    event = None
                    try:
                        raw = await gmail.message(message['id'], metadata=True)
                        labels = set(raw.get('labelIds', []))
                        if labels & PROTECTED:
                            message['protected'] = sorted(labels & PROTECTED)
                            raise UserError('Message is now Starred or Important and is protected.')
                        if labels & {'SPAM', 'DRAFT', 'SENT'} or not matching(raw, subscription, self.run['broader']):
                            raise UserError('Message no longer matches this subscription cleanup.')
                        if 'TRASH' in labels:
                            outcome = 'already_trashed'
                            detail = 'Already in Trash; this operation did not move it.'
                            self.journal.event(account, 'trash', outcome, message['id'], subscription['identity'], detail)
                        else:
                            event = self.journal.event(account, 'trash', 'pending', message['id'], subscription['identity'])
                            await gmail.request('POST', 'messages/' + quote(message['id'], safe='') + '/trash')
                            outcome, detail = 'succeeded', ''
                            try:
                                self.journal.finish(event, 'succeeded')
                            except UserError:
                                detail = 'Moved to Trash; journal update failed. The pending recovery record is retained.'
                            try:
                                self.cleanup.history.mark(account, [(message['id'], 'trashed')])
                                self.cleanup.intelligence.action_state(account, message['id'], 'trashed')
                            except UserError:
                                detail = 'Moved to Trash; review history could not be saved. Recovery is journaled.'
                        message.update(trash_status='succeeded', trash_error=detail)
                    except Exception as exc:
                        outcome = 'failed'
                        detail = str(exc) if isinstance(exc, UserError) else 'Result unknown. Check Gmail state before retrying.'
                        try:
                            # Unknown network results remain recoverable via the durable pending record.
                            self.journal.event(account, 'trash', 'failed', message['id'], subscription['identity'], detail)
                        except UserError:
                            detail += ' Local failure history could not be saved.'
                        message.update(trash_status='failed', trash_error=detail)
                    outcomes.append({'id': message['id'], 'status': outcome, 'error': detail})
                return self.summary(outcomes)
        finally:
            self.working = False

    def summary(self, outcomes):
        return {'succeeded': sum(o['status'] == 'succeeded' for o in outcomes), 'failed': sum(o['status'] == 'failed' for o in outcomes),
                'skipped': sum(o['status'] not in ('succeeded', 'failed') for o in outcomes), 'outcomes': outcomes}

    async def history(self, offset: int = 0, limit: int = 100):
        if offset < 0 or not 1 <= limit <= 100:
            raise UserError('Invalid history page.')
        async with self.connection() as (_, account):
            self.journal.import_v2(account)
            return {'account': account, **self.journal.actions(account, offset, limit)}

    async def recoverable(self, offset: int = 0, limit: int = 50):
        if offset < 0 or not 1 <= limit <= 100:
            raise UserError('Invalid recovery page.')
        async with self.connection() as (gmail, account):
            self.journal.import_v2(account)
            ids = sorted(self.journal.recoverable(account))
            items = []
            semaphore = asyncio.Semaphore(4)
            async def inspect(message_id):
                async with semaphore:
                    try:
                        raw = await gmail.message(message_id, metadata=True)
                        item = normalize(raw)
                        item.pop('text', None)
                        eligible = 'TRASH' in raw.get('labelIds', [])
                        return {**item, 'eligible': eligible, 'status': 'In Trash' if eligible else 'No longer in Trash'}
                    except UserError as exc:
                        return {'id': message_id, 'eligible': False, 'status': str(exc)}
            items = await asyncio.gather(*(inspect(i) for i in ids[offset:offset + limit]))
            return {'account': account, 'total': len(ids), 'items': items}

    async def prepare_restore(self, payload: Selection):
        self.idle()
        self.working = True
        try:
            async with self.connection() as (_, account):
                self.journal.import_v2(account)
                if len(set(payload.ids)) != len(payload.ids) or not set(payload.ids) <= self.journal.recoverable(account):
                    raise UserError('Only tracked app Trash messages can be restored.')
                return {'confirmation': self.token('restore', account, tuple(payload.ids)), 'count': len(payload.ids)}
        finally:
            self.working = False

    async def restore(self, payload: ConfirmedSelection):
        self.idle()
        account = self.consume(payload.confirmation, 'restore', tuple(payload.ids))
        self.working = True
        try:
            async with self.connection(account) as (gmail, _):
                if not set(payload.ids) <= self.journal.recoverable(account):
                    raise UserError('Recovery history changed. Refresh and confirm again.')
                outcomes = []
                for message_id in payload.ids:
                    event = None
                    try:
                        raw = await gmail.message(message_id, metadata=True)
                        if 'TRASH' not in raw.get('labelIds', []):
                            outcome, detail = 'already_restored', 'No longer in Trash; no change made.'
                            self.journal.event(account, 'restore', outcome, message_id, detail=detail)
                        else:
                            event = self.journal.event(account, 'restore', 'pending', message_id)
                            await gmail.request('POST', 'messages/' + quote(message_id, safe='') + '/untrash')
                            outcome, detail = 'succeeded', ''
                            try:
                                self.journal.finish(event, 'succeeded')
                            except UserError:
                                detail = 'Restored; journal update failed. Refresh to verify Gmail state.'
                            self.cleanup.note_restored(account, message_id)
                    except Exception as exc:
                        outcome = 'failed'
                        detail = str(exc) if isinstance(exc, UserError) else 'Restore result unknown. Refresh Gmail state before retrying.'
                        try:
                            self.journal.event(account, 'restore', 'failed', message_id, detail=detail)
                        except UserError:
                            detail += ' Local failure history could not be saved.'
                    outcomes.append({'id': message_id, 'status': outcome, 'error': detail})
                return self.summary(outcomes)
        finally:
            self.working = False

    async def erase_inventory(self, payload: EraseFeedback):
        return await self.erase(payload, 'inventory')

    async def erase_actions(self, payload: EraseFeedback):
        return await self.erase(payload, 'actions')

    async def erase(self, payload, kind):
        self.idle()
        if not payload.confirmed:
            raise UserError('Confirm clearing this local data first.')
        self.working = True
        try:
            async with self.connection() as (_, account):
                if kind == 'inventory':
                    self.journal.clear_inventory(account)
                    self.reset()
                else:
                    self.journal.clear_actions(account)
                    self.confirmations.clear()
                return {'cleared': True}
        finally:
            self.working = False
