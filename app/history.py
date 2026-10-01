"""Local review history, deliberately separate from training feedback."""
import hashlib
import os
import sqlite3
import time
from contextlib import contextmanager
from .services import ROOT, UserError


class ReviewHistory:
    def __init__(self, path=None):
        self.path = path or ROOT / '.local/history/reviews.sqlite3'

    @contextmanager
    def database(self):
        try:
            if self.path.is_symlink() or self.path.parent.is_symlink():
                raise OSError('Unsafe history path')
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.path.parent.chmod(0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            self.path.chmod(0o600)
            db = sqlite3.connect(self.path)
            try:
                db.execute('CREATE TABLE IF NOT EXISTS reviews (account TEXT, message TEXT, decision TEXT, reviewed REAL, PRIMARY KEY(account,message))')
                with db:
                    yield db
            finally:
                db.close()
        except (OSError, sqlite3.Error):
            raise UserError('Local review history is unavailable. Check disk permissions and retry.') from None

    def key(self, account):
        return hashlib.sha256(account.casefold().encode()).hexdigest()

    def ids(self, account):
        with self.database() as db:
            return {row[0] for row in db.execute('SELECT message FROM reviews WHERE account=?', (self.key(account),))}

    def mark(self, account, messages):
        with self.database() as db:
            db.executemany('INSERT OR REPLACE INTO reviews VALUES (?,?,?,?)', [(self.key(account), message_id, decision, time.time()) for message_id, decision in messages])

    def clear(self):
        with self.database() as db:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('DELETE FROM reviews')
