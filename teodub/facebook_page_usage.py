"""Does a saved Page already have work attached? (owner 11/11/2026, for Load Page.) Read only; no lock, nothing written.

Used by Load Page: may a duplicate row be merged away (`in_use`)? Never when it already has schedules, plans, posts or a policy.
A database that cannot be read counts as "in use": the safe answer. (Renaming a Page is page_rename.)"""
from pathlib import Path
import sqlite3



def _rows(path, sql, params=()):
    """Rows of a read-only query; None when the database exists but cannot be read (a missing database has no rows)."""
    path = Path(path)
    if not path.is_file():
        return []
    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)
        return connection.execute(sql, params).fetchall()
    except sqlite3.Error:
        return None
    finally:
        if connection is not None:
            connection.close()


def _count(path, sql, params):
    """The number a COUNT(*) query returns; 0 when the database does not exist; None when it cannot be read."""
    rows = _rows(path, sql, params)
    return None if rows is None else (rows[0][0] if rows else 0)


class PageUsage:
    def __init__(self, data_dir, account_id):
        self.base = Path(data_dir)/'page-publishing'
        self.account_id = account_id

    def in_use(self, page_key):
        """True when the Page has any schedule, plan, post or auto policy (or when that cannot be told)."""
        for name, sql in (('schedules.sqlite3', 'SELECT COUNT(*) FROM schedules WHERE account_id=? AND page_key=?'),
                          ('activity.sqlite3', 'SELECT COUNT(*) FROM posts WHERE account_id=? AND page_key=?'),
                          ('content-plans.sqlite3', 'SELECT COUNT(*) FROM plans WHERE account_id=? AND page_key=?'),
                          ('ai-planning.sqlite3', 'SELECT COUNT(*) FROM policies WHERE account_id=? AND page_key=?')):
            if _count(self.base/name, sql, (self.account_id, page_key)) != 0:
                return True
        return False
