"""Đổi tên Page (owner 11/11/2026): Facebook renamed a Page ("Review sản phẩm" -> "Sài Thật Nói Thật"). Tèo refuses to post when the name
shown on Facebook differs from the name a post / schedule / plan was approved with, so the new name has to follow into everything of
that Page that has NOT been sent yet. This module does that in one step, all or nothing:

  * posts that are still drafts (or failed) and not removed ........ activity.sqlite3  posts.target_name
  * schedules waiting, armed or paused whose post is such a draft ... schedules.sqlite3 schedules.snapshot (target_name)
  * content plans of the Page (target, and the digest that covers it) content-plans.sqlite3 plans.target / plans.digest
  * the automatic-posting policy of the Page ........................ ai-planning.sqlite3 policies.config (target)

Never touched: anything already sent or whose result is unknown (published, submitted, "unknown" posts, schedules needing attention), the
text of plans and AI runs, and the Page ID, link, time, body and state of anything. Nothing changes unless every check passes."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time

WAITING_STATES = ('paused', 'scheduled', 'armed')
IN_FLIGHT_STATES = ('preparing', 'submitting', 'verifying')
SOON_SECONDS = 15*60


class RenameBlocked(Exception):
    """The rename was not done (nothing was changed); the message says why, in Vietnamese."""


def _plan_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _ai_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_plan_json(value).encode('utf-8')).hexdigest()


@contextmanager
def _open_all(base, names):
    """Connections to the databases that exist, each holding its write lock, in a fixed order. Commit all on success, roll all back on
    any error. A database that is busy for more than a few seconds blocks the rename."""
    connections = {}
    try:
        for name in names:
            path = Path(base)/name
            if not path.is_file():
                continue
            try:
                connection = sqlite3.connect(path, timeout=5, isolation_level=None)
                connection.row_factory = sqlite3.Row
                connections[name] = connection
                connection.execute('BEGIN IMMEDIATE')
            except sqlite3.Error as exc:
                raise RenameBlocked('Cơ sở dữ liệu của Page đang bận hoặc không đọc được ('+name+'); chưa đổi tên.') from exc
        yield connections
        for connection in connections.values():
            connection.execute('COMMIT')
    except BaseException:
        for connection in connections.values():
            try:
                if connection.in_transaction:
                    connection.execute('ROLLBACK')
            except sqlite3.Error:
                pass
        raise
    finally:
        for connection in connections.values():
            connection.close()


def rename_page(data_dir, account_id, page_key, old_name, new_name, *, clock=None):
    """Move the new name into everything unsent of this Page. Returns the counts; raises RenameBlocked (nothing changed) otherwise."""
    old_name, new_name = str(old_name), str(new_name)
    if not old_name or not new_name or old_name == new_name:
        raise RenameBlocked('Tên cũ hoặc tên mới không hợp lệ; chưa đổi tên.')
    clock = time.time() if clock is None else clock
    key = (account_id, page_key)
    base = Path(data_dir)/'page-publishing'
    try:
        with _open_all(base, ('activity.sqlite3', 'schedules.sqlite3', 'content-plans.sqlite3', 'ai-planning.sqlite3')) as db:
            posts = db.get('activity.sqlite3')
            schedules = db.get('schedules.sqlite3')
            plans = db.get('content-plans.sqlite3')
            policies = db.get('ai-planning.sqlite3')
            counts = dict(posts=0, schedules=0, plans=0, policies=0)

            # 1. Is anything of this Page being sent right now? Then not now.
            if posts and posts.execute("SELECT 1 FROM posts WHERE account_id=? AND page_key=? AND state='preparing'", key).fetchone():
                raise RenameBlocked('Page đang chuẩn bị đăng một bài; đợi xong rồi Load Page lại.')
            if policies and policies.execute("SELECT 1 FROM runs WHERE account_id=? AND page_key=? AND state='running'", key).fetchone():
                raise RenameBlocked('Page đang có một lượt AI chạy; đợi xong rồi Load Page lại.')
            rows = schedules.execute('SELECT id,post_id,state,due_at,snapshot FROM schedules WHERE account_id=? AND page_key=?', key).fetchall() if schedules else []
            for row in rows:
                if row['state'] in IN_FLIGHT_STATES:
                    raise RenameBlocked('Page đang có bài đang gửi lên Facebook; đợi xong rồi Load Page lại.')
                if row['state'] in ('scheduled', 'armed') and float(row['due_at']) - clock < SOON_SECONDS:
                    raise RenameBlocked('Page có bài sắp tới giờ đăng (trong 15 phút); đổi tên sau khi bài đó xong.')

            # 2. Schedules that wait: the post and the snapshot must both carry the old name and the post must still be a plain draft.
            moved_posts = set()
            for row in rows:
                if row['state'] not in WAITING_STATES:
                    continue
                snapshot = json.loads(row['snapshot'])
                post = posts.execute('SELECT state,archived,target_name FROM posts WHERE id=?', (row['post_id'],)).fetchone() if posts else None
                if post is None or post['state'] not in ('draft', 'failed') or post['archived']:
                    raise RenameBlocked('Một lịch đang chờ có bài không còn ở dạng bản nháp; kiểm tra lịch của Page trước khi đổi tên.')
                if post['target_name'] == new_name and snapshot.get('target_name') == new_name:
                    continue  # Already moved by an earlier try that could not save the Page list.
                if post['target_name'] != old_name or snapshot.get('target_name') != old_name:
                    raise RenameBlocked('Một lịch đang chờ ghi tên Page khác tên đã lưu; kiểm tra lịch của Page trước khi đổi tên.')
                snapshot['target_name'] = new_name
                schedules.execute('UPDATE schedules SET snapshot=? WHERE id=?', (json.dumps(snapshot, ensure_ascii=False), row['id']))
                counts['schedules'] += 1
                moved_posts.add(row['post_id'])

            # 3. Drafts of the Page (also those of plans not scheduled yet), except those that belong to a schedule that is not waiting
            # (sent, needing attention, removed...): post and schedule must keep saying the same thing.
            if posts:
                kept = {row['post_id'] for row in rows if row['state'] not in WAITING_STATES}
                for post in posts.execute("SELECT id FROM posts WHERE account_id=? AND page_key=? AND state IN ('draft','failed') "
                                          "AND archived=0 AND target_name=?", key+(old_name,)).fetchall():
                    if post['id'] in kept:
                        continue
                    posts.execute('UPDATE posts SET target_name=? WHERE id=?', (new_name, post['id']))
                    counts['posts'] += 1
            for post_id in moved_posts:
                if posts.execute('SELECT target_name FROM posts WHERE id=?', (post_id,)).fetchone()['target_name'] != new_name:
                    raise RenameBlocked('Bản nháp của một lịch chưa đổi được tên; chưa đổi tên.')

            # 4. Plans: the target carries the name and the digest covers the target, so the digest is made again; the old one must be
            # reproducible first (otherwise the plan is not what Tèo thinks it is and nothing is touched).
            if plans:
                for row in plans.execute('SELECT record_id,thread_id,digest,target,document FROM plans WHERE account_id=? AND page_key=?', key).fetchall():
                    target = json.loads(row['target'])
                    if target.get('name') != old_name:
                        continue  # Another name (or already the new one): not this rename's business.
                    document = json.loads(row['document'])
                    if _digest(dict(thread_id=row['thread_id'], target=target, document=document)) != row['digest']:
                        raise RenameBlocked('Một kế hoạch AI không tính lại được mã kiểm tra; chưa đổi tên để giữ an toàn.')
                    target['name'] = new_name
                    plans.execute('UPDATE plans SET target=?,digest=? WHERE record_id=?',
                                  (_plan_json(target), _digest(dict(thread_id=row['thread_id'], target=target, document=document)), row['record_id']))
                    counts['plans'] += 1

            # 5. The automatic-posting policy (same revision: the settings are the same, only the Page name inside is new).
            if policies:
                for row in policies.execute('SELECT config FROM policies WHERE account_id=? AND page_key=?', key).fetchall():
                    config = json.loads(row['config'])
                    if isinstance(config.get('target'), dict) and config['target'].get('name') == old_name:
                        config['target']['name'] = new_name
                        policies.execute('UPDATE policies SET config=? WHERE account_id=? AND page_key=?', (_ai_json(config),)+key)
                        counts['policies'] += 1
            return counts
    except RenameBlocked:
        raise
    except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        raise RenameBlocked('Chưa đổi tên Page được vì dữ liệu lịch/kế hoạch không đọc được: '+str(exc)[:120]) from exc
