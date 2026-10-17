"""Service-only persistent store. Never imported by the public UI server."""
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1


class SnapshotStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'mailbox.sqlite3'

    @contextmanager
    def database(self):
        if any(p.is_symlink() for p in (self.directory, *self.directory.parents, self.path)):
            raise ValueError('Private storage must not use symlinks.')
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.directory.chmod(0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        db = sqlite3.connect(self.path)
        try:
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                db.executescript('''
                CREATE TABLE IF NOT EXISTS messages (
                  account TEXT NOT NULL, id TEXT NOT NULL, original TEXT NOT NULL,
                  received INTEGER NOT NULL, saved REAL NOT NULL,
                  PRIMARY KEY(account,id));
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, account TEXT NOT NULL, requested INTEGER,
                  status TEXT NOT NULL, page TEXT, exhausted INTEGER NOT NULL DEFAULT 0,
                  created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS pending (
                  job TEXT NOT NULL REFERENCES jobs(id), message TEXT NOT NULL,
                  state TEXT NOT NULL, PRIMARY KEY(job,message));
                CREATE TABLE IF NOT EXISTS topic_runs (
                  id TEXT PRIMARY KEY, account TEXT NOT NULL, job TEXT NOT NULL,
                  status TEXT NOT NULL, result TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS download_errors (
                  job TEXT PRIMARY KEY REFERENCES jobs(id), code TEXT NOT NULL, http_status INTEGER);
                CREATE TABLE IF NOT EXISTS cleanup_runs (
                  id TEXT PRIMARY KEY, account TEXT NOT NULL, job TEXT NOT NULL,
                  status TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cleanup_entries (
                  run TEXT NOT NULL REFERENCES cleanup_runs(id), message TEXT NOT NULL,
                  decision TEXT NOT NULL, score REAL, category TEXT NOT NULL, filename TEXT NOT NULL,
                  PRIMARY KEY(run,message));
                ''')
                yield db
        finally:
            db.close()

    def create_job(self, account, limit):
        jid = secrets.token_urlsafe(18)
        with self.database() as db:
            db.execute('INSERT INTO jobs(id,account,requested,status,created,updated) VALUES(?,?,?,?,?,?)',
                       (jid, account, limit, 'running', time.time(), time.time()))
        return jid

    def create_topic_run(self, account, job):
        rid = secrets.token_hex(16)
        with self.database() as db:
            db.execute('INSERT INTO topic_runs VALUES(?,?,?,?,?,?)', (rid, account, job, 'running', None, time.time()))
        return rid

    def finish_topic_run(self, rid, status, result=None):
        with self.database() as db:
            db.execute('UPDATE topic_runs SET status=?,result=? WHERE id=?',
                       (status, json.dumps(result) if result is not None else None, rid))

    def latest_topics(self, account, job):
        with self.database() as db:
            row = db.execute('SELECT id,status,result FROM topic_runs WHERE account=? AND job=? ORDER BY created DESC LIMIT 1',
                             (account, job)).fetchone()
        return {'id': row[0], 'status': row[1], 'result': json.loads(row[2]) if row[2] else None} if row else None

    def original(self, account, mid):
        with self.database() as db:
            row = db.execute('SELECT original FROM messages WHERE account=? AND id=?', (account, mid)).fetchone()
        return json.loads(row[0]) if row else None

    def job(self, jid):
        with self.database() as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
            return dict(row) if row else None

    def latest(self):
        with self.database() as db:
            row = db.execute('SELECT id FROM jobs ORDER BY created DESC LIMIT 1').fetchone()
        return self.job(row[0]) if row else None

    def latest_for(self, account):
        with self.database() as db:
            row = db.execute('SELECT id FROM jobs WHERE account=? ORDER BY created DESC LIMIT 1', (account,)).fetchone()
        return self.job(row[0]) if row else None

    def state(self, jid, status):
        with self.database() as db:
            db.execute('UPDATE jobs SET status=?,updated=? WHERE id=?', (status, time.time(), jid))
            if status == 'running':
                db.execute('DELETE FROM download_errors WHERE job=?', (jid,))

    def record_download_error(self, jid, value):
        from .errors import MESSAGES
        code = value.get('code') if value.get('code') in MESSAGES else 'unexpected'
        status = value.get('http_status')
        with self.database() as db:
            db.execute('INSERT OR REPLACE INTO download_errors VALUES(?,?,?)',
                       (jid, code, status if type(status) is int else None))

    def download_error(self, jid):
        with self.database() as db:
            row = db.execute('SELECT code,http_status FROM download_errors WHERE job=?', (jid,)).fetchone()
        return {'code': row[0], 'http_status': row[1]} if row else None

    def listed(self, jid):
        with self.database() as db:
            return db.execute('SELECT COUNT(*) FROM pending WHERE job=?', (jid,)).fetchone()[0]

    def page(self, jid, ids, cursor):
        # Pending IDs and next cursor become durable in one transaction.
        with self.database() as db:
            db.executemany('INSERT OR IGNORE INTO pending VALUES(?,?,?)', [(jid, mid, 'pending') for mid in ids])
            db.execute('UPDATE jobs SET page=?,exhausted=?,updated=? WHERE id=?',
                       (cursor, int(not cursor), time.time(), jid))

    def unfinished(self, jid):
        with self.database() as db:
            return [r[0] for r in db.execute("SELECT message FROM pending WHERE job=? AND state IN ('pending','failed')", (jid,))]

    def cached(self, account, mid):
        with self.database() as db:
            return bool(db.execute('SELECT 1 FROM messages WHERE account=? AND id=?', (account, mid)).fetchone())

    def outcome(self, jid, mid, state, original=None):
        with self.database() as db:
            if original is not None:
                account = db.execute('SELECT account FROM jobs WHERE id=?', (jid,)).fetchone()[0]
                db.execute('INSERT INTO messages VALUES(?,?,?,?,?) ON CONFLICT(account,id) DO UPDATE SET original=excluded.original,received=excluded.received,saved=excluded.saved',
                           (account, mid, json.dumps(original), int(original.get('internalDate', 0)), time.time()))
            db.execute('UPDATE pending SET state=? WHERE job=? AND message=?', (state, jid, mid))

    def summary(self, account, jid=None):
        with self.database() as db:
            count, oldest, newest = db.execute('SELECT COUNT(*),MIN(received),MAX(received) FROM messages WHERE account=?', (account,)).fetchone()
            counts = dict(db.execute('SELECT state,COUNT(*) FROM pending WHERE job=? GROUP BY state', (jid,))) if jid else {}
        job = self.job(jid) if jid else None
        return {'schema_version': SCHEMA_VERSION, 'stored': count, 'oldest': oldest, 'newest': newest,
                'job': None if not job else {'id': jid, 'status': job['status'], 'requested': job['requested'],
                'listed': sum(counts.values()), 'saved': counts.get('saved', 0), 'reused': counts.get('reused', 0),
                'failed': counts.get('failed', 0), 'skipped': counts.get('skipped', 0),
                'pending': counts.get('pending', 0), 'listing_exhausted': bool(job['exhausted']),
                'coverage_complete': job['status'] == 'done' and job['requested'] is None and bool(job['exhausted'])
                and not any(counts.get(k, 0) for k in ('failed', 'pending', 'skipped'))}}
