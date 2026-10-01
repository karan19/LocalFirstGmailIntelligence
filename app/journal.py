"""Account-separated inventory and action journal. No message bodies or URLs."""
import json
import sqlite3
import time
from .history import ReviewHistory
from .services import UserError


class Journal(ReviewHistory):
    def initialize(self, db):
        db.execute('CREATE TABLE IF NOT EXISTS subscriptions (account TEXT, identity TEXT, data TEXT, PRIMARY KEY(account,identity))')
        db.execute('CREATE TABLE IF NOT EXISTS observations (account TEXT, message TEXT, identity TEXT, PRIMARY KEY(account,message))')
        db.execute('CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY, account TEXT, kind TEXT, message TEXT, identity TEXT, outcome TEXT, detail TEXT, created REAL)')
        db.execute('CREATE TABLE IF NOT EXISTS journal_settings (account TEXT PRIMARY KEY, imported INTEGER)')

    def observe(self, account, record, message_id, scan_id=None):
        key = self.key(account)
        with self.database() as db:
            self.initialize(db)
            row = db.execute('SELECT data FROM subscriptions WHERE account=? AND identity=?', (key, record['identity'])).fetchone()
            old = json.loads(row[0]) if row else {}
            # First observation in a refresh replaces a reference that may have
            # left the mailbox; subsequent observations pick the newest source.
            newest = (scan_id is not None and old.get('reference_scan') != scan_id) or record['last_seen'] >= old.get('last_seen', 0)
            merged = {**old, **record} if newest else {**record, **old}
            if scan_id is not None:
                merged['reference_scan'] = scan_id
            merged['senders'] = sorted(set(old.get('senders', [])) | set(record['senders']))
            merged['status'] = old.get('status', 'active')
            merged['keep'] = old.get('keep', False)
            db.execute('INSERT OR REPLACE INTO observations VALUES (?,?,?)', (key, message_id, record['identity']))
            db.execute('INSERT OR REPLACE INTO subscriptions VALUES (?,?,?)', (key, record['identity'], json.dumps(merged)))

    def inventory(self, account):
        with self.database() as db:
            self.initialize(db)
            rows = db.execute('SELECT identity,data FROM subscriptions WHERE account=?', (self.key(account),)).fetchall()
            counts = dict(db.execute('SELECT identity,COUNT(*) FROM observations WHERE account=? GROUP BY identity', (self.key(account),)))
            return [{**json.loads(data), 'count': counts.get(identity, 0)} for identity, data in rows]

    def track_match(self, account, identity, message_id):
        with self.database() as db:
            self.initialize(db)
            db.execute('INSERT OR REPLACE INTO observations VALUES (?,?,?)', (self.key(account), message_id, identity))

    def subscription(self, account, identity):
        return next((r for r in self.inventory(account) if r['identity'] == identity), None)

    def update(self, account, identity, **changes):
        with self.database() as db:
            self.initialize(db)
            row = db.execute('SELECT data FROM subscriptions WHERE account=? AND identity=?', (self.key(account), identity)).fetchone()
            if not row:
                raise UserError('Subscription unavailable. Refresh the inventory.')
            data = {**json.loads(row[0]), **changes}
            db.execute('UPDATE subscriptions SET data=? WHERE account=? AND identity=?', (json.dumps(data), self.key(account), identity))

    def event(self, account, kind, outcome, message='', identity='', detail=''):
        with self.database() as db:
            self.initialize(db)
            return db.execute('INSERT INTO actions (account,kind,message,identity,outcome,detail,created) VALUES (?,?,?,?,?,?,?)',
                              (self.key(account), kind, message, identity, outcome, detail, time.time())).lastrowid

    def finish(self, event_id, outcome, detail=''):
        with self.database() as db:
            self.initialize(db)
            db.execute('UPDATE actions SET outcome=?,detail=? WHERE id=?', (outcome, detail, event_id))

    def import_v2(self, account):
        with self.database() as db:
            self.initialize(db)
            key = self.key(account)
            if db.execute('SELECT 1 FROM journal_settings WHERE account=?', (key,)).fetchone():
                return
            db.execute("INSERT INTO actions (account,kind,message,identity,outcome,detail,created) SELECT account,'trash',message,'','legacy','Imported V2 record; Gmail state must be checked.',reviewed FROM reviews r WHERE account=? AND decision='trashed' AND NOT EXISTS (SELECT 1 FROM actions a WHERE a.account=r.account AND a.message=r.message AND a.kind='trash')", (key,))
            db.execute('INSERT INTO journal_settings VALUES (?,1)', (key,))

    def actions(self, account, offset=0, limit=100):
        with self.database() as db:
            self.initialize(db)
            db.row_factory = sqlite3.Row
            count = db.execute('SELECT COUNT(*) FROM actions WHERE account=?', (self.key(account),)).fetchone()[0]
            rows = db.execute('SELECT id,kind,message,identity,outcome,detail,created FROM actions WHERE account=? ORDER BY id DESC LIMIT ? OFFSET ?', (self.key(account), limit, offset)).fetchall()
            return {'total': count, 'items': [dict(row) for row in rows]}

    def recoverable(self, account):
        with self.database() as db:
            self.initialize(db)
            # Latest Trash/Restore result per message. Pending writes survive crashes.
            rows = db.execute("SELECT a.message,a.kind,a.outcome FROM actions a WHERE a.account=? AND a.message!='' AND a.kind IN ('trash','restore') AND a.id=(SELECT MAX(b.id) FROM actions b WHERE b.account=a.account AND b.message=a.message AND b.kind IN ('trash','restore') AND b.outcome IN ('succeeded','pending','legacy','already_restored')) AND a.outcome IN ('succeeded','pending','legacy')", (self.key(account),)).fetchall()
            return {message for message, kind, outcome in rows if kind == 'trash' or outcome == 'pending'}

    def clear_inventory(self, account):
        with self.database() as db:
            self.initialize(db)
            db.execute('PRAGMA secure_delete=ON')
            for table in ('subscriptions', 'observations'):
                db.execute(f'DELETE FROM {table} WHERE account=?', (self.key(account),))

    def clear_actions(self, account):
        with self.database() as db:
            self.initialize(db)
            db.execute('PRAGMA secure_delete=ON')
            db.execute('DELETE FROM actions WHERE account=?', (self.key(account),))
            # Do not resurrect V2 records on the next visit after deliberate clearing.
            db.execute('INSERT OR REPLACE INTO journal_settings VALUES (?,1)', (self.key(account),))
