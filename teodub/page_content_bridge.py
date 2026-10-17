"""Read approved chat plans locally; the web never opens a Codex session."""
from copy import deepcopy
import queue
import threading
import time


class ContentPlanReceiver:
    def __init__(self, bridge):
        self.bridge = bridge
        self.pending = None
        self.results = queue.Queue()
        self.last_sync = {}
        self.closed = False

    def store(self):
        from teodub.page_content_plan import ContentPlanStore
        return ContentPlanStore(self.bridge.owner.studio.data_dir)

    def public_snapshot(self, pages):
        connections, plans = [], []
        store = self.store()
        for page in pages:
            account_id, page_key = page['account_id'], page['id']
            link = store.get_connection(account_id, page_key)
            if not link.get('thread_id'):
                continue
            connections.append(dict(account_id=account_id, page_key=page_key,
                **{key: link.get(key, '') for key in
                   ('thread_id', 'title', 'auto_sync', 'last_sync', 'last_error')}))
            for plan in store.plans(account_id, page_key)[:10]:
                if plan.get('thread_id') != link['thread_id']:
                    continue
                public = {key: plan.get(key, '') for key in
                          ('plan_id', 'title', 'digest', 'state', 'thread_id', 'source_turn_id', 'fetched_at')}
                public.update(account_id=account_id, page_key=page_key, items=[],
                    target={key:plan.get('target',{}).get(key,'') for key in ('name','url','page_id')})
                for item in plan.get('items', []):
                    public['items'].append({key: item.get(key, '') for key in
                        ('id', 'kind', 'due_at', 'body', 'video_key', 'goal', 'schedule_id', 'state', 'message')})
                plans.append(public)
        return dict(content_connections=connections, content_plans=plans)

    def start_read(self, account_id, page_key, *, command_id=''):
        if self.closed:
            raise ValueError('Tèo đang đóng; mở lại để nhận kế hoạch.')
        connection = self.store().get_connection(account_id, page_key)
        if (self.pending and self.pending.get('kind') == 'read' and command_id
                and not self.pending.get('command_id') and not self.pending['cancel'].is_set()
                and (self.pending['account_id'], self.pending['page_key'], self.pending['thread_id']) ==
                    (account_id, page_key, connection.get('thread_id'))):
            self.pending['command_id'] = command_id
            return
        if self.pending is not None:
            raise ValueError('Đang nhận kế hoạch từ Codex; đợi lượt hiện tại xong.')
        if not connection.get('thread_id'):
            raise ValueError('Liên kết cuộc trò chuyện trong tab Kế hoạch từ Codex trên desktop trước.')
        task = dict(account_id=account_id, page_key=page_key, thread_id=connection['thread_id'],
                    command_id=command_id, cancel=threading.Event(), kind='read')
        self.pending = task
        self.last_sync[(account_id, page_key)] = time.monotonic()
        def read():
            try:
                from teodub.codex_chat import read_thread
                snapshot = read_thread(task['thread_id'])
                result = dict(snapshot=snapshot)
            except Exception as exc:
                result = dict(error=str(exc) or 'Chưa đọc được kế hoạch Codex.')
            self.results.put((task, result))
        try:
            threading.Thread(target=read, daemon=True, name='teo-plan-read').start()
        except Exception:
            self.pending = None
            raise

    def start_stage(self, row):
        if self.closed or self.pending is not None:
            raise ValueError('Đang xử lý kế hoạch Codex; đợi lượt hiện tại xong.')
        task = dict(**deepcopy(row['payload']), command_id=row['id'], cancel=threading.Event(), kind='stage')
        self.pending = task
        def stage():
            try:
                result = self.store().stage(task['plan_id'], task['account_id'], task['page_key'],
                                           expected_digest=task['plan_digest'])
                outcome = dict(staged=result)
            except Exception as exc:
                outcome = dict(error=str(exc) or 'Chưa lưu được lịch từ kế hoạch.')
            self.results.put((task, outcome))
        try:
            threading.Thread(target=stage, daemon=True, name='teo-plan-stage').start()
        except Exception:
            self.pending = None
            raise

    def cancel(self, command_id):
        if self.pending and self.pending['command_id'] == command_id:
            self.pending['cancel'].set()

    def observe(self):
        try:
            task, result = self.results.get_nowait()
        except queue.Empty:
            return
        if self.pending is not task:
            return
        self.pending = None
        if self.closed:
            return
        ident = task['command_id']
        active = self.bridge.active
        if ident and (not active or active['id'] != ident):
            return
        if task['cancel'].is_set():
            if ident:
                self.bridge.complete(active, 'failed', 'Lượt xử lý kế hoạch đã dừng; chưa bật lịch mới.')
            return
        if ident and (not self.bridge.enabled or time.time() >= active['expires']):
            self.bridge.complete(active, 'failed', 'Lượt xử lý kế hoạch đã dừng; chưa bật lịch mới, lịch đã lưu được giữ.')
            return
        try:
            if ident:
                self.bridge.validate(active)
            self.bridge.activity.page(task['account_id'], task['page_key'])
            if result.get('error'):
                raise ValueError(result['error'])
            public = dict(account_id=task['account_id'], page_key=task['page_key'])
            if task['kind'] == 'stage':
                self.bridge.validate(active)
                self.store().approve(task['plan_id'], task['account_id'], task['page_key'],
                    self.bridge.owner.get_page_scheduler(), expected_digest=task['plan_digest'])
                public['plan_id'] = task['plan_id']
                message = 'Đã duyệt và bật lịch của kế hoạch. Máy Tèo sẽ đăng khi đến giờ.'
            else:
                link = self.store().get_connection(task['account_id'], task['page_key'])
                if link.get('thread_id') != task['thread_id']:
                    raise ValueError('Cuộc trò chuyện liên kết đã đổi; bỏ phản hồi của lượt cũ.')
                imported = self.store().import_messages(task['account_id'], task['page_key'], result['snapshot'])
                if imported.get('error'):
                    raise ValueError(imported['status'])
                message = imported.get('status') or 'Đã nhận kế hoạch; cần duyệt trước khi bật lịch.'
                if imported.get('plan'):
                    public['plan_id'] = imported['plan'].get('plan_id', '')
            if ident:
                self.bridge.complete(active, 'done', message, public)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            if task['kind'] == 'read':
                link = self.store().get_connection(task['account_id'], task['page_key'])
                if link.get('thread_id') == task['thread_id']:
                    self.store()._error(task['account_id'], task['page_key'], str(exc))
            if ident:
                self.bridge.complete(active, 'failed', str(exc))
            else:
                self.bridge.owner.status.set('Kế hoạch Codex cần kiểm tra: '+str(exc))

    def tick(self):
        if self.closed:
            return
        self.observe()
        if self.pending or self.bridge.active or getattr(self.bridge.owner.studio,'restart_pending',False):
            return
        registry = self.bridge.activity.accounts.load()
        for page in registry['pages']:
            pair = (page['account_id'], page['id'])
            link = self.store().get_connection(*pair)
            if (link.get('thread_id') and link.get('auto_sync')
                    and time.monotonic()-self.last_sync.get(pair, 0) >= 60):
                self.start_read(*pair)
                break

    def dispatch(self, row):
        payload = row['payload']
        if row['action'] == 'sync_content_plan':
            link = self.store().get_connection(payload['account_id'], payload['page_key'])
            if link.get('thread_id') != payload['thread_id']:
                raise ValueError('Cuộc trò chuyện liên kết đã đổi; làm mới trước khi nhận kế hoạch.')
            self.start_read(payload['account_id'], payload['page_key'], command_id=row['id'])
            return None
        plan = self.store().get_plan(payload['plan_id'], payload['account_id'], payload['page_key'])
        if plan.get('digest') != payload['plan_digest']:
            raise ValueError('Kế hoạch đã đổi sau khi duyệt; làm mới và đọc lại.')
        self.start_stage(row)
        return None

    def close(self):
        self.closed = True
        if self.pending:
            self.pending['cancel'].set()
