"""Chỉ đọc: gom nhật ký đăng bài của một Page (mặc định "Sài Thật Nói Thật") trong 3 ngày gần nhất vào Desktop\\teo-log-dang-bai.txt.
Không ghi gì vào dữ liệu Tèo. Chạy:  .venv\\Scripts\\python.exe tools\\xem_log_dang_bai.py ["tên Page"]"""
import json
import os
import socket
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

DATA = Path(os.environ['LOCALAPPDATA'])/'TeoDubStudio'
PUB = DATA/'page-publishing'
NAME = (sys.argv[1] if len(sys.argv) > 1 else 'Sài Thật Nói Thật').casefold()
SINCE = time.time()-3*86400
OUT = Path(os.environ['USERPROFILE'])/'Desktop'/'teo-log-dang-bai.txt'
lines = []


def say(text=''):
    lines.append(str(text))


def when(stamp):
    try:
        return datetime.fromtimestamp(float(stamp)).strftime('%d/%m %H:%M:%S')
    except (TypeError, ValueError, OSError):
        return str(stamp)


def db(name):
    return sqlite3.connect('file:'+str(PUB/name).replace('\\', '/')+'?mode=ro', uri=True)


say(f'Máy: {socket.gethostname()} · lúc {datetime.now():%d/%m/%Y %H:%M:%S}')
head = Path(__file__).resolve().parents[1]/'.git'
try:
    ref = (head/'HEAD').read_text().strip()
    commit = (head/ref[5:]).read_text().strip() if ref.startswith('ref: ') and (head/ref[5:]).exists() else ref
    say(f'Bản mã: {commit[:10]}')
except OSError:
    pass
accounts = json.loads((PUB/'accounts.json').read_text(encoding='utf-8'))
names = {a['id']: a.get('name', '') for a in accounts.get('accounts', [])}
pages = [p for p in accounts.get('pages', []) if NAME in str(p.get('name', '')).casefold()]
if not pages:
    say(f'Không thấy Page nào có tên chứa "{NAME}".')
for page in pages:
    say(f"\n=== Page: {page['name']} · tài khoản {names.get(page['account_id'], '?')} · ID {page.get('page_id')} · "
        f"url {page.get('url')} · username {page.get('username', '(chưa có)')}")
    con = db('schedules.sqlite3')
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute('SELECT * FROM schedules WHERE page_key=? ORDER BY due_at', (page['id'],))]
    shown = [r for r in rows if SINCE <= r['due_at'] <= time.time()+3600]
    say(f'Lịch: {len(rows)} dòng; in {len(shown)} dòng có giờ đăng trong 3 ngày qua.')
    posts = []
    for r in shown:
        say(f"\n-- {r['source_key'].split(':')[-1]:>5} · giờ {when(r['due_at'])} · {r['state']} · phase={r['phase']} · "
            f"thử lúc {r['attempted_at'] or '-'} · xong {r['finished_at'] or '-'}")
        say(f"   {r['message'][:400]}")
        posts.append(r['post_id'])
        for table in ('preparation_retries', 'immediate_requests', 'schedule_recoveries', 'schedule_new_attempts'):
            try:
                for x in con.execute(f'SELECT at FROM {table} WHERE schedule_id=?', (r['id'],)):
                    say(f'   [{table}] {x[0]}')
            except sqlite3.Error:
                pass  # This machine never made that table: nothing of that kind happened here.
    act = db('activity.sqlite3')
    for post in posts:
        events = act.execute('SELECT at,state,message FROM events WHERE post_id=? ORDER BY id', (post,)).fetchall()
        for at, state, message in events[-8:]:
            say(f'   bài {post[:8]} · {at} · {state} · {message[:300]}')
        try:  # What Tèo saw when it tried to confirm the post (read only; cut short).
            extra = act.execute('SELECT external_url,manual_url,browser_receipt,browser_tracking FROM posts WHERE id=?', (post,)).fetchone()
            if extra:
                say(f'   bài {post[:8]} · link={extra[0] or "-"} · link tay={extra[1] or "-"}')
                say(f'   bài {post[:8]} · biên nhận: {str(extra[2])[:700]}')
                say(f'   bài {post[:8]} · theo dõi: {str(extra[3])[:900]}')
        except sqlite3.Error:
            pass
say('\n=== Chrome của tài khoản đã mở trang đăng nhập / xác minh (3 ngày)')
# Read only: a COPY of the account's Chrome history is read, so the running Chrome is never touched. Only the Facebook pages that ask
# for a login or a check are printed (time and address), nothing else of the history.
import shutil
import tempfile
GATES = ('/login', '/checkpoint', '/recover', '/two_step_verification', '/two_factor', '/confirmemail', '/authentication')
for account_id in sorted({page['account_id'] for page in pages}):
    history = PUB/'browser-profiles'/account_id/'chrome'/'Default'/'History'
    say(f'-- tài khoản {names.get(account_id, "?")}')
    if not history.exists():
        say('   (không thấy lịch sử Chrome)')
        continue
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder)/'History'
        try:
            shutil.copyfile(history, copy)
            con = sqlite3.connect(copy)
            since = int((SINCE+11644473600)*1_000_000)  # Chrome counts microseconds from 1601.
            found = 0
            hosts = {}  # Which Facebook address Chrome was on, first and last time (14/11: web.facebook.com is not accepted).
            for url, stamp in con.execute('SELECT urls.url, visits.visit_time FROM visits JOIN urls ON urls.id=visits.url '
                                          'WHERE visits.visit_time>=? ORDER BY visits.visit_time', (since,)):
                host = urlsplit(url).hostname or ''
                if host.endswith('facebook.com'):
                    seen = hosts.setdefault(host, [stamp, stamp, 0])
                    seen[1], seen[2] = stamp, seen[2]+1
            for host, (first, last, count) in sorted(hosts.items(), key=lambda item: item[1][0]):
                say(f'   địa chỉ {host}: {count} lần · từ {when(first/1_000_000-11644473600)} đến {when(last/1_000_000-11644473600)}')
            for url, stamp in con.execute('SELECT urls.url, visits.visit_time FROM visits JOIN urls ON urls.id=visits.url '
                                          'WHERE visits.visit_time>=? ORDER BY visits.visit_time', (since,)):
                bits = urlsplit(url)
                if bits.hostname and bits.hostname.endswith('facebook.com') and bits.path.startswith(GATES):
                    found += 1
                    say(f'   {when(stamp/1_000_000-11644473600)} · {bits.hostname}{bits.path[:80]}')
            con.close()
            if not found:
                say('   Không có lần nào Facebook đưa tới trang đăng nhập/xác minh.')
        except (OSError, sqlite3.Error) as exc:
            say(f'   Chưa đọc được lịch sử Chrome: {exc}')

say('\n=== Lệnh gửi cho Chrome (3 ngày)')
try:
    con = db('page-commands.sqlite3')
    for created, action, state, executor, message, started, finished in con.execute(
            'SELECT created,action,state,executor,message,started,finished FROM commands WHERE created>=? ORDER BY created', (SINCE,)):
        say(f'{when(created)} · {action} · {state} · chạy {when(started) if started else "-"} → {when(finished) if finished else "-"} · '
            f'{executor[:20]} · {message[:200]}')
except sqlite3.Error as exc:
    say(f'Không đọc được: {exc}')
say('\n=== Các lần khởi động lại Tèo (3 ngày)')
for folder in sorted((DATA/'restart-transfers').glob('*'), key=lambda p: p.stat().st_mtime):
    if folder.stat().st_mtime >= SINCE:
        say(f'{when(folder.stat().st_mtime)} · {folder.name[:8]} · ' + ', '.join(sorted(p.name for p in folder.iterdir())[:6]))
for name in ('load-page-log.txt',):
    path = PUB/name
    if path.exists():
        say(f'\n({name} sửa lúc {when(path.stat().st_mtime)})')
OUT.write_text('\n'.join(lines), encoding='utf-8')
print('Đã ghi:', OUT)
