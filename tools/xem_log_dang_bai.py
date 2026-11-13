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
    shown = [r for r in rows if r['state'] not in ('armed', 'scheduled', 'removed', 'cancelled') or SINCE <= r['due_at'] <= time.time()+3600]
    say(f'Lịch: {len(rows)} dòng; in {len(shown)} dòng lỗi/đã chạy/gần đây.')
    posts = []
    for r in shown:
        say(f"\n-- {r['source_key'].split(':')[-1]:>5} · giờ {when(r['due_at'])} · {r['state']} · phase={r['phase']} · "
            f"thử lúc {r['attempted_at'] or '-'} · xong {r['finished_at'] or '-'}")
        say(f"   {r['message'][:400]}")
        posts.append(r['post_id'])
        for table in ('preparation_retries', 'immediate_requests', 'schedule_recoveries', 'schedule_new_attempts'):
            for x in con.execute(f'SELECT at FROM {table} WHERE schedule_id=?', (r['id'],)):
                say(f'   [{table}] {x[0]}')
    act = db('activity.sqlite3')
    for post in posts:
        events = act.execute('SELECT at,state,message FROM events WHERE post_id=? ORDER BY id', (post,)).fetchall()
        for at, state, message in events[-8:]:
            say(f'   bài {post[:8]} · {at} · {state} · {message[:300]}')
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
