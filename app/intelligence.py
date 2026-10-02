"""V4 account-scoped metrics and opt-in example memory; never mailbox rules."""
import json
import re
import time
from email.utils import parseaddr
from .journal import Journal


def clean(value, limit):
    return re.sub(r'(?:https?://|mailto:)\S+', '[link]', str(value))[:limit]


def tokens(value):
    return set(re.findall(r'[a-z]{3,}', value.casefold())) - {'the', 'and', 'for', 'you', 'your', 'with', 'this', 'from', 'link'}


def overlap(a, b):
    left, right = tokens(a), tokens(b)
    return len(left & right) / max(1, len(left | right))


class Intelligence(Journal):
    def initialize(self, db):
        super().initialize(db)
        db.execute('CREATE TABLE IF NOT EXISTS product_runs (account TEXT,id TEXT,kind TEXT,status TEXT,finished REAL,PRIMARY KEY(account,id))')
        db.execute('CREATE TABLE IF NOT EXISTS metric_messages (account TEXT,message TEXT,bucket TEXT,updated REAL,PRIMARY KEY(account,message))')
        db.execute('CREATE TABLE IF NOT EXISTS learning_settings (account TEXT PRIMARY KEY,enabled INTEGER)')
        db.execute('CREATE TABLE IF NOT EXISTS corrections (account TEXT,message TEXT,data TEXT,updated REAL,PRIMARY KEY(account,message))')

    def explorer_results(self,account,ids):
        """Account-bound result features; never retrieve persisted body text."""
        result={}
        with self.database() as db:
            self.initialize(db)
            for start in range(0,len(ids),500):
                chunk=ids[start:start+500]
                if not chunk:continue
                placeholders=','.join('?' for _ in chunk)
                for mid,bucket in db.execute('SELECT message,bucket FROM metric_messages WHERE account=? AND message IN ('+placeholders+')',[self.key(account),*chunk]):
                    if bucket in ('keep','ready','review'):result[mid]=bucket
                for mid,decision in db.execute('SELECT message,decision FROM reviews WHERE account=? AND message IN ('+placeholders+')',[self.key(account),*chunk]):
                    if decision in ('keep','cleanup','restored'):result[mid]={'keep':'keep','cleanup':'ready','restored':'keep'}[decision]
        return result

    def learning(self, account):
        with self.database() as db:
            self.initialize(db)
            key = self.key(account)
            row = db.execute('SELECT enabled FROM learning_settings WHERE account=?', (key,)).fetchone()
            count = db.execute('SELECT COUNT(*) FROM corrections WHERE account=?', (key,)).fetchone()[0]
            return {'enabled': bool(row and row[0]), 'count': count}

    def set_learning(self, account, enabled):
        with self.database() as db:
            self.initialize(db)
            db.execute('INSERT OR REPLACE INTO learning_settings VALUES (?,?)', (self.key(account), int(enabled)))
        return self.learning(account)

    def clear_learning(self, account):
        with self.database() as db:
            self.initialize(db)
            db.execute('PRAGMA secure_delete=ON')
            db.execute('DELETE FROM corrections WHERE account=?', (self.key(account),))
        return self.learning(account)

    def capture(self, account, message, decision):
        if not self.learning(account)['enabled']:
            return
        if decision not in ('keep', 'cleanup'):
            raise ValueError('Only explicit Keep/Cleanup labels are learning examples')
        data = {'sender': parseaddr(message['sender'])[1].casefold(), 'subject': clean(message['subject'], 300),
                'text': clean(message.get('text', ''), 2000), 'list_id': clean(message.get('list_id', ''), 300), 'decision': decision, 'taxonomy': 'keep-cleanup-v1'}
        with self.database() as db:
            self.initialize(db)
            db.execute('INSERT OR REPLACE INTO corrections VALUES (?,?,?,?)', (self.key(account), message['id'], json.dumps(data), time.time()))

    def retrieve(self, account, message):
        if not self.learning(account)['enabled']:
            return {'status': 'none', 'examples': []}
        sender = parseaddr(message['sender'])[1].casefold()
        with self.database() as db:
            self.initialize(db)
            rows = db.execute('SELECT message,data FROM corrections WHERE account=? AND message!=? ORDER BY updated DESC LIMIT 1000', (self.key(account), message['id'])).fetchall()
        ranked = []
        for message_id, raw in rows:
            example = json.loads(raw)
            if example.get('taxonomy') != 'keep-cleanup-v1':
                continue
            subject = overlap(message['subject'], example['subject'])
            body = overlap(clean(message.get('text', ''), 2000), example['text'])
            same_sender = bool(sender and sender == example['sender'])
            same_list = bool(message.get('list_id') and message['list_id'] == example['list_id'])
            same_domain = bool(sender and '@' in sender and '@' in example['sender'] and sender.split('@')[-1] == example['sender'].split('@')[-1])
            similarity = max(subject, body)
            # Sender/list identity alone never establishes a classification preference.
            if similarity < .25 or not (same_sender or same_list or same_domain or similarity >= .65):
                continue
            score = .6 * similarity + .25 * (same_sender or same_list) + .1 * same_domain
            ranked.append((score, message_id, example))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        chosen = ranked[:3]
        if not chosen:
            return {'status': 'none', 'examples': []}
        strong = [row for row in chosen if row[0] >= .6]
        status = 'weak' if not strong else 'conflict' if len({row[2]['decision'] for row in strong}) > 1 else 'relevant'
        selected = strong if strong else chosen
        return {'status': status, 'examples': [dict(row[2]) for row in selected],
                'provenance': [{'message_id': row[1], 'subject': row[2]['subject'], 'decision': row[2]['decision'], 'similarity': round(row[0], 3)} for row in selected]}

    def record_messages(self, account, messages):
        with self.database() as db:
            self.initialize(db)
            db.executemany('INSERT OR REPLACE INTO metric_messages VALUES (?,?,?,?)', [(self.key(account), m['id'], 'trashed' if m.get('trash_status') == 'trashed' else m['bucket'], time.time()) for m in messages if m['bucket'] != 'not_analyzed'])

    def action_state(self, account, message_id, bucket):
        with self.database() as db:
            self.initialize(db)
            db.execute('UPDATE metric_messages SET bucket=?,updated=? WHERE account=? AND message=?', (bucket, time.time(), self.key(account), message_id))

    def record_run(self, run, kind, messages=()):
        if not run.get('account') or not run.get('id'):
            return
        with self.database() as db:
            self.initialize(db)
            db.execute('INSERT OR REPLACE INTO product_runs VALUES (?,?,?,?,?)', (self.key(run['account']), run['id'], kind, run['status'], time.time()))
        self.record_messages(run['account'], messages)

    def dashboard(self, account):
        with self.database() as db:
            self.initialize(db)
            key = self.key(account)
            runs = {(kind, status): count for kind, status, count in db.execute('SELECT kind,status,COUNT(*) FROM product_runs WHERE account=? GROUP BY kind,status', (key,))}
            recorded = {message: (bucket, updated) for message, bucket, updated in db.execute('SELECT message,bucket,updated FROM metric_messages WHERE account=?', (key,))}
            states = {message: value[0] for message, value in recorded.items()}
            # Legacy explicit history is known, but old ephemeral scan results are not.
            for message, decision, reviewed in db.execute('SELECT message,decision,reviewed FROM reviews WHERE account=?', (key,)):
                backlog_only = db.execute("SELECT 1 FROM actions WHERE account=? AND message=? AND identity!='' AND kind='trash'", (key, message)).fetchone()
                if message not in recorded and backlog_only and decision in ('trashed', 'restored'):
                    continue
                if message not in recorded or reviewed > recorded[message][1]:
                    if decision in ('keep', 'cleanup', 'promotional', 'trashed', 'restored'):
                        states[message] = {'keep': 'keep', 'cleanup': 'ready', 'promotional': 'ready', 'trashed': 'trashed', 'restored': 'keep'}[decision]
                    else:
                        states.setdefault(message, 'review')
            actions = {(kind, outcome): count for kind, outcome, count in db.execute('SELECT kind,outcome,COUNT(*) FROM actions WHERE account=? GROUP BY kind,outcome', (key,))}
            subscriptions = db.execute('SELECT COUNT(*) FROM subscriptions WHERE account=?', (key,)).fetchone()[0]
        return {'account': account, 'total_runs': runs.get(('classification', 'done'), 0), 'stopped_runs': runs.get(('classification', 'stopped'), 0),
                'subscription_scans': sum(n for (kind, status), n in runs.items() if kind == 'discovery' and status == 'done'),
                'backlog_scans': sum(n for (kind, status), n in runs.items() if kind == 'backlog' and status == 'done'),
                'other_stopped_scans': sum(n for (kind, status), n in runs.items() if kind != 'classification' and status == 'stopped'),
                'emails_reviewed': len(states), 'kept': sum(v == 'keep' for v in states.values()),
                'trashed': actions.get(('trash', 'succeeded'), 0), 'restored': actions.get(('restore', 'succeeded'), 0),
                'subscriptions': subscriptions, 'unsubscribe_attempts': sum(n for (kind, outcome), n in actions.items() if kind == 'unsubscribe' and outcome in ('pending', 'failed', 'request_accepted', 'verified')),
                'unsubscribe_accepted': actions.get(('unsubscribe', 'request_accepted'), 0), 'unsubscribe_manual': actions.get(('unsubscribe', 'manual_completed'), 0),
                'unsubscribe_verified': actions.get(('unsubscribe', 'verified'), 0),
                'coverage': 'Run summaries begin with V4. Unique emails include recorded V4 results and known legacy explicit reviews; earlier ephemeral analyses are unavailable. Action totals use the retained successful action journal. Clearing inventory or action history resets those corresponding totals.'}
