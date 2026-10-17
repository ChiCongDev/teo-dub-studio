"""Passive desktop view of the web queue, without the web runtime or secrets."""
from collections import Counter
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.request import ProxyHandler, Request, build_opener

from teodub.campaign_tracking import latest_progress

PROJECT = Path(__file__).resolve().parents[1]
STATES = {'queued': 'Chờ xử lý', 'running': 'Đang xử lý', 'stopping': 'Đang dừng',
          'completed': 'Hoàn tất', 'review_required': 'Cần nghe duyệt', 'failed': 'Lỗi',
          'interrupted': 'Gián đoạn', 'cancelled': 'Đã dừng'}
PERIODS = ('Tất cả', 'Hôm nay', '7 ngày gần đây', '30 ngày gần đây')
LOCAL_URL = 'http://127.0.0.1:8765/'
ROW_LIMIT = 200


def web_data_root(explicit=None):
    if explicit is not None:
        return Path(explicit).resolve()
    try:
        value = (PROJECT/'tools'/'web-data-root.txt').read_text(encoding='utf-8-sig').strip()
        if value and Path(value).is_absolute():
            return Path(value).resolve()
    except OSError:
        pass
    return (Path(os.environ.get('LOCALAPPDATA', Path.home()))/'TeoDubWeb').resolve()


def period_start(period, now):
    if period == 'Tất cả':
        return None
    if period not in PERIODS:
        raise ValueError('Khoảng thời gian chưa hợp lệ.')
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    days = {'Hôm nay': 0, '7 ngày gần đây': 6, '30 ngày gần đây': 29}[period]
    return (midnight-timedelta(days=days)).timestamp()


def date_text(value):
    try:
        return datetime.fromtimestamp(value).strftime('%d/%m/%Y %H:%M:%S')
    except (ValueError, TypeError, OverflowError, OSError):
        return '—'


def read_object(path):
    try:
        # Progress/result metadata only; never load a video or a credential file.
        if path.stat().st_size > 1024*1024:
            return {}
        result = json.loads(path.read_text(encoding='utf-8'))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def job_directory(root, ident):
    if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{32}', ident):
        return None
    directory = root/'jobs'/ident
    return directory if directory.resolve().is_relative_to(root/'jobs') else None


def output_folder(root, job):
    """Bind a local open action to the job's published output, without hashing on polls."""
    directory = job_directory(root, job['id'])
    if directory is None or job['state'] not in ('completed', 'review_required'):
        return None
    result = read_object(directory/'result.json')
    state = read_object(directory/'state.json')
    outputs = result.get('outputs')
    rendered = state.get('rendered')
    if (result.get('status') not in ('completed', 'review_required') or not isinstance(outputs, dict)
            or not isinstance(rendered, dict) or outputs != rendered.get('outputs')):
        return None
    value = outputs.get('video')
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    try:
        if (path.is_absolute() and path.suffix.lower() == '.mp4' and path.is_file()
                and not path.is_symlink()
                and path.resolve().is_relative_to(root/'outputs'/job['id'])):
            return path.parent
    except OSError:
        pass
    return None


def annotate(root, job):
    try:
        spec = json.loads(job['spec'])
        spec = spec if isinstance(spec, dict) else {}
    except (ValueError, TypeError):
        spec = {}
    job['spec'] = spec
    directory = job_directory(root, job['id'])
    event, percent = latest_progress(directory) if directory is not None else ({}, None)
    if job['state'] == 'queued':
        percent = 0
    elif job['state'] in ('completed', 'review_required'):
        percent = 100
    job['progress'] = f'{percent:.0f}%' if percent is not None else '—'
    job['label'] = STATES.get(job['state'], job['state'])
    job['last_event'] = str(event.get('message') or '')
    job['event_time'] = str(event.get('time') or '')
    if job['state'] == 'running' and job['last_event']:
        job['message'] = job['last_event']
    receipt = read_object(directory/'drive-upload.json') if directory is not None else {}
    job['drive'] = 'Tắt'
    if spec.get('save_drive'):
        job['drive'] = {'staged': 'Đã chép vào thư mục đồng bộ', 'uploaded': 'Đã tải lên Drive',
                        'copying': 'Đang chép', 'failed': 'Lỗi chép vào Drive',
                        'cancelled': 'Đã dừng chép'}.get(receipt.get('state'), 'Chờ thành phẩm')
    return job


def server_online():
    try:
        # Ignore system proxy settings for this loopback-only health check.
        with build_opener(ProxyHandler({})).open(Request(LOCAL_URL+'health'), timeout=.8) as response:
            return response.status == 200 and json.loads(response.read(2048)).get('status') == 'ok'
    except (OSError, ValueError, AttributeError):
        return False


def snapshot(root, *, owner=None, period='Tất cả', state=None, now=None, health=server_online):
    """Consistent SELECT-only snapshot. Missing data never creates a database."""
    root = Path(root).resolve()
    now = time.time() if now is None else now
    start = period_start(period, now)
    if state is not None and state not in STATES:
        raise ValueError('Trạng thái chưa hợp lệ.')
    empty = dict(users=[], counts={}, people=[], jobs=[], visible_total=0, exists=False,
                 online=False, refreshed=now)
    database = root/'portal.sqlite3'
    if not database.is_file():
        return empty
    clauses, args = [], []
    if owner is not None:
        clauses.append('j.owner=?')
        args.append(owner)
    if start is not None:
        clauses.append('j.created>=?')
        args.append(start)
    where = ' WHERE '+' AND '.join(clauses) if clauses else ''
    db = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=.4)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        users = [dict(row) for row in db.execute('SELECT id,username,name,enabled FROM users ORDER BY username')]
        groups = [dict(row) for row in db.execute('''SELECT j.owner,coalesce(u.name,'Tài khoản đã xóa') AS name,
                    coalesce(u.username,'') AS username,j.state,count(*) AS total
                    FROM jobs j LEFT JOIN users u ON u.id=j.owner'''+where+' GROUP BY j.owner,j.state', args)]
        list_where = where
        list_args = list(args)
        if state is not None:
            list_where += (' AND ' if list_where else ' WHERE ')+'j.state=?'
            list_args.append(state)
        visible_total = db.execute('SELECT count(*) FROM jobs j'+list_where, list_args).fetchone()[0]
        rows = [dict(row) for row in db.execute('''SELECT j.id,j.title,j.input,j.spec,j.state,
                    j.created,j.updated,j.message,j.owner,coalesce(u.name,'Tài khoản đã xóa') AS owner_name,
                    coalesce(u.username,'') AS username FROM jobs j LEFT JOIN users u ON u.id=j.owner'''
                    +list_where+''' ORDER BY CASE WHEN j.state IN ('running','stopping') THEN 0
                    WHEN j.state='queued' THEN 1 ELSE 2 END,
                    CASE WHEN j.state='queued' THEN j.created END ASC,j.created DESC,j.id LIMIT ?''',
                    [*list_args, ROW_LIMIT])]
    finally:
        db.close()
    counts, people = Counter(), {}
    for group in groups:
        counts[group['state']] += group['total']
        person = people.setdefault(group['owner'], dict(id=group['owner'], name=group['name'],
                                                       username=group['username'], counts=Counter()))
        person['counts'][group['state']] = group['total']
    return dict(users=users, counts=dict(counts), people=sorted(people.values(), key=lambda p: p['username']),
                jobs=[annotate(root, job) for job in rows], visible_total=visible_total,
                exists=True, online=health(), refreshed=now)
