"""24/11/2026 (owner): after EVERY post Tèo ran, whatever it ended as ('Đã đăng', 'Đã gửi', 'Chưa xác minh đã đăng', 'Cần kiểm tra',
'Dừng trước khi bấm Đăng'), the Views numbers of all the Pages of that Facebook account are read. Only a schedule the owner stopped
himself is not.

page-publishing/numbers-after-post.json keeps the reads still to do, one per account: the posted Pages (latest first, they are read
first) and when the read is due. A Tèo closed before the read, or while it ran, does it when it opens again. Written atomically; a
broken file reads as empty. Reading numbers only reads Facebook: nothing is clicked, typed or posted.
"""
import json
import os
from pathlib import Path
import tempfile
import threading
import time

FILE_NAME = 'numbers-after-post.json'
WAIT_SECONDS = 180  # 20/11/2026: Facebook takes a few minutes to take a post in. Also the wait of a read caught up after a restart.
RETRY_SECONDS = 300  # Chrome could not be opened for the read: look again this much later …
MAX_TRIES = 3  # … this many times, then the read is given up (and logged).
_LOCK = threading.Lock()


def _path(data_dir):
    return Path(data_dir).resolve()/'page-publishing'/FILE_NAME


def _valid(entry):
    return (isinstance(entry, dict) and isinstance(entry.get('pages'), list) and entry['pages']
            and all(isinstance(page, str) and page for page in entry['pages'])
            and isinstance(entry.get('due_at'), (int, float)) and not isinstance(entry.get('due_at'), bool))


def load(data_dir):
    """{account_id: dict(pages, due_at, added_at, tries, queued)}; {} when there is no file or it cannot be read."""
    try:
        data = json.loads(_path(data_dir).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    items = data.get('accounts') if isinstance(data, dict) else None
    if not isinstance(items, dict):
        return {}
    return {account: dict(entry, tries=int(entry.get('tries') or 0), queued=str(entry.get('queued') or ''),
                          added_at=float(entry.get('added_at') or entry['due_at']))
            for account, entry in items.items() if isinstance(account, str) and _valid(entry)}


def _write(data_dir, items):
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix='numbers-after-post-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(dict(version=1, accounts=items), handle, ensure_ascii=False, indent=1)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def add(data_dir, account_id, page_key, *, delay, now=None):
    """A post of `page_key` ended: read the account's numbers `delay` seconds from now. A read already waiting for the account takes
    this Page first and the later of the two times (several posts close together are read once). Returns the entry."""
    now = time.time() if now is None else now
    with _LOCK:
        items = load(data_dir)
        old = items.get(account_id)
        pages = [page_key]+[page for page in (old or {}).get('pages', []) if page != page_key]
        due = now+max(0, delay)
        if old and not old['queued']:
            due = max(due, old['due_at'])
        items[account_id] = dict(pages=pages, due_at=due, added_at=now, tries=0, queued='')
        _write(data_dir, items)
        return dict(items[account_id])


def due(data_dir, run, now=None):
    """[(account_id, entry)] whose time has come and that this Tèo (`run`) has not handed to Chrome yet, earliest first. An entry
    handed over by an EARLIER Tèo is due again: that Tèo closed before it heard the read end."""
    now = time.time() if now is None else now
    return sorted(((account, entry) for account, entry in load(data_dir).items()
                   if entry['due_at'] <= now and entry['queued'] != run), key=lambda item: item[1]['due_at'])


def waiting(data_dir, account_id):
    """True while a read is still to do (or running) for the account: Tèo keeps its Chrome open meanwhile."""
    return account_id in load(data_dir)


def _change(data_dir, account_id, expected, change):
    """Apply `change(entry)` (a new entry, or None to remove it) only when the entry is still `expected` (same post time): a post that
    ended meanwhile gets its own read. Returns 'missing' (nothing changed), 'removed' or 'kept'."""
    with _LOCK:
        items = load(data_dir)
        entry = items.get(account_id)
        if entry is None or (expected is not None and entry['added_at'] != expected['added_at']):
            return 'missing'
        new = change(entry)
        if new is None:
            del items[account_id]
        else:
            items[account_id] = new
        _write(data_dir, items)
        return 'removed' if new is None else 'kept'


def handed_over(data_dir, account_id, entry, run):
    """The read was handed to this Tèo's Chrome: it stays in the file until Chrome says it ended (or a later Tèo does it again)."""
    return _change(data_dir, account_id, entry, lambda old: dict(old, queued=run)) == 'kept'


def finished(data_dir, account_id, run):
    """The read this Tèo handed over ended (whatever it read): forget it. A post that ended meanwhile stays (it is not handed over)."""
    with _LOCK:
        items = load(data_dir)
        if items.get(account_id, {}).get('queued') != run:
            return False
        del items[account_id]
        _write(data_dir, items)
        return True


def try_again(data_dir, account_id, entry, *, now=None, run=None):
    """The read could not start (Chrome not opened) or Chrome closed before it ended: look again RETRY_SECONDS later. Returns False
    only when it was given up now (after MAX_TRIES). With `run`, only a read this Tèo handed over is touched."""
    now = time.time() if now is None else now
    if run is not None:
        current = load(data_dir).get(account_id)
        if not current or current['queued'] != run:
            return True
        entry = current
    return _change(data_dir, account_id, entry, lambda old: None if old['tries']+1 >= MAX_TRIES
                   else dict(old, tries=old['tries']+1, due_at=now+RETRY_SECONDS, queued='')) != 'removed'


def drop(data_dir, account_id):
    """Forget the account's read (the account was removed from Tèo)."""
    _change(data_dir, account_id, None, lambda old: None)
