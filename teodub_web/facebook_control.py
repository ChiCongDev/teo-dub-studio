"""Authenticated web commands; Facebook work belongs to the desktop executor."""
import json
import re
import uuid

from flask import abort, g, jsonify, render_template, request

from teodub.common import hash_file
from teodub.facebook_accounts import facebook_url
from teodub.page_schedule import parse_time
from teodub_web.runner import public_message, verified_output


def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('Chọn đúng tài khoản, Page hoặc bản nháp đã lưu.')
    return value


def fields(data, allowed):
    if not isinstance(data, dict) or set(data)-set(allowed):
        raise ValueError('Yêu cầu có trường chưa được hỗ trợ.')
    return data


def text(value, maximum, label, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError(label+' chưa hợp lệ.')
    value = value.strip()
    if not value and not empty:
        raise ValueError('Điền '+label.lower()+'.')
    return value


def public_command(row):
    result = row.get('result') or {}
    payload = row.get('payload') or {}
    return {**{key: row.get(key, payload.get(key, '')) for key in ('id', 'action', 'operation', 'account_id',
        'page_key', 'post_id', 'state', 'created_at', 'finished_at')},
        **{key: result[key] for key in ('account_id', 'page_key', 'post_id') if key in result},
        'message': public_message(row.get('message', ''))}


class FacebookControl:
    def __init__(self, store):
        # No desktop registry/profile, password, cookie or Graph API is opened by web.
        from teodub.page_command_bridge import PageCommandStore
        self.web = store
        self.mailbox = PageCommandStore(mailbox_root=store.root/'page-control')

    def snapshot(self):
        data = self.mailbox.public_snapshot()
        accounts = data.get('accounts', [])
        pages = data.get('pages', [])
        posts = data.get('posts', [])
        return dict(accounts=accounts, pages=pages, posts=posts, schedules=data.get('schedules', []),
                    content_connections=data.get('content_connections', []), content_plans=data.get('content_plans', []),
                    ai_campaigns=data.get('ai_campaigns',[]),ai_policies=data.get('ai_policies',[]),page_statuses=data.get('page_statuses',[]))

    def ready(self):
        status = self.mailbox.status()
        if not status.get('online') or not status.get('enabled'):
            raise ValueError('Mở Tèo desktop trên máy xử lý và bật Nhận lệnh quản lý Page từ web. '
                             'Web chưa gửi lệnh khi desktop chưa kết nối.')

    def account(self, account_id, snapshot):
        ident(account_id)
        if not any(row['id'] == account_id for row in snapshot['accounts']):
            raise ValueError('Tài khoản Facebook đã thay đổi. Làm mới danh sách.')
        return account_id

    def page(self, account_id, page_key, snapshot):
        self.account(account_id, snapshot)
        ident(page_key)
        row = next((row for row in snapshot['pages'] if row['id'] == page_key
                    and row['account_id'] == account_id), None)
        if not row:
            raise ValueError('Page không thuộc tài khoản đã chọn. Làm mới danh sách.')
        return {key: row.get(key, '') for key in ('name', 'url', 'page_id')}

    def send(self, action, payload, data):
        key = ident(data.get('request_key'))
        self.ready()
        row = self.mailbox.enqueue_remote(action, payload, owner_id=g.user['id'], request_key=key)
        return jsonify(command=public_command(row)), 202

    def view(self):
        snap = self.snapshot()
        # Keep the same selections while polling; never report a cached snapshot as live login.
        for account in snap['accounts']:
            account['pages'] = [row for row in snap['pages'] if row['account_id'] == account['id']]
        for post in snap['posts']:
            post['browser_review'] = post.get('review', {})
            post['message'] = public_message(post.get('message', ''))
        assets = [dict(job_id=job['id'], title=job['title'], filename='Video thành phẩm')
                  for job in self.web.jobs(g.user)
                  if job['owner'] == g.user['id'] and job['state'] == 'completed'
                  and not json.loads(job['spec']).get('prepare_only')]
        status = self.mailbox.status()
        bridge = {key:status.get(key) for key in ('online', 'enabled', 'busy', 'counts')}
        bridge.update(last_seen=status.get('updated_at',0), message=public_message(status.get('message','')))
        for schedule in snap['schedules']:
            schedule['message'] = public_message(schedule.get('message', ''))
        for link in snap['content_connections']:
            link['last_error'] = public_message(link.get('last_error', ''))
        for plan in snap['content_plans']:
            for item in plan.get('items', []):
                item['message'] = public_message(item.get('message', ''))
        return dict(bridge=bridge, **snap, assets=assets,
                    commands=[public_command(row) for row in self.mailbox.list(owner_id=g.user['id'])])


def register_facebook(app, store):
    # Lazy construction: starting the portal does not start any Facebook worker/browser.
    def control():
        return FacebookControl(store)

    def admin():
        if g.user['role'] != 'admin':
            abort(403)

    def guarded(action):
        admin()
        try:
            return action(control())
        except (ValueError, TypeError, KeyError, OSError) as exc:
            if isinstance(exc, (OSError, KeyError)):
                message = 'Chưa đọc được dữ liệu từ desktop. Danh sách và phiên Facebook vẫn được giữ.'
            else:
                message = public_message(str(exc))
            return jsonify(error=message), 409

    @app.get('/facebook')
    def facebook_home():
        admin()
        return render_template('facebook.html')

    @app.get('/api/facebook')
    def facebook_state():
        return guarded(lambda ctl: jsonify(ctl.view()))

    @app.post('/api/facebook/accounts')
    def facebook_account():
        def submit(ctl):
            data = fields(request.get_json(), ('name', 'request_key'))
            return ctl.send('add_account', dict(name=text(data.get('name'), 100, 'Tên tài khoản')), data)
        return guarded(submit)

    @app.post('/api/facebook/pages')
    def facebook_page():
        def submit(ctl):
            data = fields(request.get_json(), ('account_id', 'name', 'url', 'page_id', 'request_key'))
            ctl.account(data.get('account_id'), ctl.snapshot())
            page_id = text(data.get('page_id', ''), 32, 'Page ID', empty=True)
            if page_id and not re.fullmatch(r'[0-9]+', page_id):
                raise ValueError('Page ID phải là số.')
            payload = dict(account_id=data['account_id'], name=text(data.get('name'), 150, 'Tên Page'),
                           url=facebook_url(data.get('url', ''), page=True), page_id=page_id)
            return ctl.send('save_page', payload, data)
        return guarded(submit)

    @app.post('/api/facebook/drafts')
    def facebook_draft():
        def submit(ctl):
            data = fields(request.get_json(), ('account_id', 'page_key', 'body', 'job_id', 'request_key'))
            target = ctl.page(data.get('account_id'), data.get('page_key'), ctl.snapshot())
            body = text(data.get('body', ''), 12000, 'Nội dung bài', empty=True)
            asset = None
            if data.get('job_id'):
                job = store.job(ident(data['job_id']), g.user)
                if not job or job['owner'] != g.user['id'] or job['state'] != 'completed':
                    raise ValueError('Chọn video đã hoàn tất của tài khoản web này.')
                path = verified_output(store, job, 'video')
                stat = path.stat()
                asset = dict(job_id=job['id'], path=str(path), sha256=hash_file(path),
                             size=stat.st_size, mtime_ns=stat.st_mtime_ns)
            if not body and not asset:
                raise ValueError('Điền nội dung bài hoặc chọn video thành phẩm.')
            return ctl.send('save_draft', dict(account_id=data['account_id'], page_key=data['page_key'],
                target=target, body=body, asset=asset), data)
        return guarded(submit)

    @app.post('/api/facebook/commands')
    def facebook_command():
        def submit(ctl):
            data = fields(request.get_json(), ('action', 'account_id', 'page_key', 'post_id',
                'request_key', 'review_key', 'due_at', 'thread_id', 'plan_id', 'plan_digest','config','enabled'))
            action = data.get('action')
            if action not in {'open_account', 'load_pages', 'open_page', 'prepare_post', 'publish_post',
                              'review_post', 'schedule_post', 'sync_content_plan', 'approve_content_plan','save_ai_policy','pause_ai_policy'}:
                raise ValueError('Thao tác chưa được hỗ trợ.')
            snap = ctl.snapshot()
            payload = dict(account_id=ctl.account(data.get('account_id'), snap))
            if action not in {'open_account', 'load_pages'}:
                payload.update(page_key=ident(data.get('page_key')),
                               target=ctl.page(data['account_id'], data.get('page_key'), snap))
            if action.endswith('_post'):
                payload['post_id'] = ident(data.get('post_id'))
                post = next((row for row in snap['posts'] if row['id'] == data['post_id']
                    and row['account_id'] == data['account_id'] and row['page_key'] == data['page_key']), None)
                if not post or post.get('delivery_mode') != 'browser':
                    raise ValueError('Chọn bản nháp đăng qua trình duyệt của đúng Page.')
                payload['post_key'] = post['post_key']
                if action == 'schedule_post':
                    if post['kind'] != 'text' or post.get('media') or post['state'] not in {'draft','failed'}:
                        raise ValueError('Chỉ hẹn giờ bản nháp bài chữ chưa có lượt đăng. Video dùng lịch trong desktop.')
                    payload['due_at'] = parse_time(data.get('due_at'))
                if action == 'publish_post':
                    key = data.get('review_key')
                    if (not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key)
                        or key != post.get('review_key') or post['state'] != 'review'
                        or post.get('browser_finish_mode') != 'tool'):
                        raise ValueError('Xem và duyệt đúng bản thực tế từ Chrome trước khi đăng.')
                    payload['review_key'] = key
            if action == 'sync_content_plan':
                link = next((row for row in snap['content_connections'] if row['account_id'] == data['account_id']
                             and row['page_key'] == data['page_key']), None)
                if not link or link.get('thread_id') != data.get('thread_id'):
                    raise ValueError('Liên kết cuộc trò chuyện trên desktop trước; làm mới để nhận đúng kế hoạch.')
                payload['thread_id'] = link['thread_id']
            if action == 'approve_content_plan':
                value = data.get('plan_id')
                if not isinstance(value, str) or not re.fullmatch(
                        r'[0-9a-f]{32}|[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', value):
                    raise ValueError('Chọn kế hoạch đã nhận của đúng Page.')
                plan_id = str(uuid.UUID(value))
                plan = next((row for row in snap['content_plans'] if row['plan_id'] == plan_id
                             and row['account_id'] == data['account_id'] and row['page_key'] == data['page_key']), None)
                if not plan or plan.get('digest') != data.get('plan_digest'):
                    raise ValueError('Kế hoạch đã đổi hoặc không thuộc Page; làm mới và duyệt lại.')
                payload.update(plan_id=plan_id, plan_digest=plan['digest'])
            if action=='save_ai_policy':
                from teodub.page_ai import validate_config
                config=validate_config(data.get('config'))
                known={c['id'] for c in snap['ai_campaigns']}
                if any(c not in known for c in config['campaigns']):
                    raise ValueError('Nguồn chiến dịch đã đổi; làm mới danh sách.')
                if type(data.get('enabled')) is not bool:
                    raise ValueError('Chọn bật/tắt lịch AI.')
                link=next((c for c in snap['content_connections'] if c['account_id']==data['account_id'] and c['page_key']==data['page_key']),None)
                if not link or link['thread_id']!=data.get('thread_id'):
                    raise ValueError('Chat chiến lược đã đổi; liên kết trên desktop rồi làm mới.')
                payload.update(config=config,due_at=parse_time(data.get('due_at')),enabled=data['enabled'],thread_id=link['thread_id'])
            return ctl.send(action, payload, data)
        return guarded(submit)

    @app.post('/api/facebook/commands/<command_id>/cancel')
    def facebook_cancel(command_id):
        def cancel(ctl):
            row = ctl.mailbox.get(ident(command_id))
            if row.get('owner_id') != g.user['id']:
                abort(404)
            return jsonify(command=public_command(ctl.mailbox.cancel_queued(command_id)))
        return guarded(cancel)
