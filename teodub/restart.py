"""Explicit desktop handoff. Media workers survive; browser/AI requests drain first."""
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from teodub.common import atomic_json, hash_file


def transfer_directory(data_dir, token):
    if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token):
        raise ValueError('Mã khởi động lại không hợp lệ.')
    root = Path(data_dir).resolve()/'restart-transfers'
    folder = root/token
    if folder.is_symlink() or root.is_symlink() or folder.resolve().parent != root:
        raise ValueError('Thư mục khởi động lại không hợp lệ.')
    return folder


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class AttachedProcess:
    """Own a Windows handle, not just a PID that Windows may have reused."""
    def __init__(self, identity, *, control=True):
        import win32api
        import win32con
        import win32process
        if not isinstance(identity,dict) or type(identity.get('pid')) is not int or identity['pid'] <= 0:
            raise ValueError('Tiến trình đã lưu không hợp lệ.')
        self.pid = identity['pid']
        rights = win32con.SYNCHRONIZE | win32con.PROCESS_QUERY_INFORMATION
        if control:
            rights |= win32con.PROCESS_TERMINATE
        try:
            self.handle = win32api.OpenProcess(rights, False, self.pid)
        except Exception as exc:
            raise ValueError('Tiến trình cũ không còn truy cập được; không tự chạy lại.') from exc
        self.control = control
        if str(win32process.GetProcessTimes(self.handle)['CreationTime']) != identity['created']:
            self.handle.Close()
            raise ValueError('Tiến trình cũ đã kết thúc; không kết nối tới tiến trình khác.')

    def poll(self):
        import win32event
        import win32process
        if win32event.WaitForSingleObject(self.handle, 0) == win32event.WAIT_TIMEOUT:
            return None
        return win32process.GetExitCodeProcess(self.handle)

    def terminate(self):
        import win32api
        if not self.control:
            raise ValueError('Không có quyền dừng tiến trình này.')
        if self.poll() is None:
            win32api.TerminateProcess(self.handle, 1)

    kill = terminate


def process_identity(process):
    import win32api
    import win32con
    import win32process
    try:
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, process.pid)
    except Exception as exc:
        raise ValueError('Chưa xác minh được tiến trình cần bàn giao; giữ Tèo hiện tại.') from exc
    try:
        return dict(pid=process.pid, created=str(win32process.GetProcessTimes(handle)['CreationTime']))
    finally:
        handle.Close()


def process_parents():
    """Windows venv python.exe/pythonw.exe may be a redirector owning a child."""
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),
            ('th32ProcessID',wintypes.DWORD),('th32DefaultHeapID',ctypes.c_size_t),
            ('th32ModuleID',wintypes.DWORD),('cntThreads',wintypes.DWORD),
            ('th32ParentProcessID',wintypes.DWORD),('pcPriClassBase',wintypes.LONG),
            ('dwFlags',wintypes.DWORD),('szExeFile',wintypes.WCHAR*260)]
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [wintypes.HANDLE,ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2,0)
    if handle == ctypes.c_void_p(-1).value:
        raise ValueError('Chưa xác minh được bộ khởi chạy Windows; giữ Tèo hiện tại.')
    result = {}
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(entry)
        valid = kernel.Process32FirstW(handle,ctypes.byref(entry))
        while valid:
            result[entry.th32ProcessID] = entry.th32ParentProcessID
            valid = kernel.Process32NextW(handle,ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    return result


def matches_started_process(identity, process):
    expected = process_identity(process)
    if identity == expected:
        return True
    # Verify the ACK process creation time and its ancestry under this exact
    # live launcher. A different process or reused PID must not authorize exit.
    child = AttachedProcess(identity,control=False)
    if child.poll() is not None or process.poll() is not None:
        return False
    parents = process_parents()
    ident = identity['pid']
    for _ in range(8):
        ident = parents.get(ident,0)
        if ident == expected['pid']:
            return process_identity(process) == expected and process.poll() is None
        if not ident:
            break
    return False


def job_directory(data_dir, directory, family):
    root = Path(data_dir).resolve()/family
    path = Path(directory)
    if (not path.is_absolute() or path.is_symlink() or root.is_symlink()
            or path.resolve().parent != root or not re.fullmatch(r'[0-9a-f]{32}', path.name)):
        raise ValueError('Công việc không thuộc thư mục dữ liệu của Tèo.')
    return path.resolve()


def worker_fingerprint(directory, role):
    if role == 'single':
        return hash_file(directory/'config.json')
    from teodub.campaign_batch import _identity
    if role == 'campaign':
        return _identity(read(directory/'campaign.json'),directory)
    import hashlib
    saved = read(directory/'queue.json')
    frozen = dict(id=saved['id'],campaigns=[{k:row[k] for k in ('directory','name','config_identity')}
        for row in saved['campaigns']])
    return hashlib.sha256(json.dumps(frozen,sort_keys=True).encode('utf-8')).hexdigest()


def schedule_fingerprint(row):
    import hashlib
    keys = ('account_id','page_key','post_id','source','snapshot','due_at','body')
    return hashlib.sha256(json.dumps({k: row[k] for k in keys},ensure_ascii=False,
        sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()


def busy_reasons(studio, campaign, facebook):
    reasons = []
    if getattr(studio, 'voice_learning_busy', False) is True:
        reasons.append('đang phân tích giọng hoặc tạo lời nghe thử; đợi xong hoặc dừng xử lý mẫu')
    children = studio.window.winfo_children()
    if isinstance(children,(list,tuple)):
        pending_windows = list(children)
        managers = list(facebook.manager_windows.values())
        while pending_windows:
            widget = pending_windows.pop()
            if widget.winfo_class() == 'Toplevel' and widget not in managers and widget.winfo_viewable():
                reasons.append('còn cửa sổ biên tập / thiết lập; lưu rồi đóng cửa sổ đó')
            pending_windows.extend(widget.winfo_children())
    if studio.login_pending or campaign.scanning or campaign.chrome_login_pending:
        reasons.append('đang đăng nhập hoặc lấy danh sách video')
    if studio.stop_started is not None and studio.process is not None:
        reasons.append('video đang dừng theo yêu cầu')
    bridge = facebook.page_bridge
    if bridge:
        if bridge.active or bridge.content_receiver.pending:
            reasons.append('đang xử lý yêu cầu Page / kế hoạch')
        if bridge.ai_controller.pending:
            reasons.append('AI đang phân tích kế hoạch')
    for session in facebook.sessions.values():
        if session.pending and not session.opened:
            reasons.append('Chrome đang mở hoặc đóng')
        if session.loading_pages or any(r.get('state') in {'queued','running','opening','verifying'}
                for collection in (session.navigation,session.page_load_requests,session.post_actions)
                for r in collection.values()):
            reasons.append('Facebook đang thao tác; chờ kết quả')
    for manager in facebook.manager_windows.values():
        if not manager.destroyed and (manager.busy or manager.preview_window
                or getattr(getattr(manager,'content_panel',None),'busy',False)):
            reasons.append('cửa sổ Page đang xử lý hoặc duyệt nội dung')
        if not manager.destroyed:
            body = manager.body.get('1.0','end').strip()
            post = manager.store.post(manager.account_id,manager.page_key,manager.post_id) if manager.post_id else None
            if body != (post['body'] if post else '') or manager.paths != ([m['path'] for m in post['media']] if post else []):
                reasons.append('bản nháp Page chưa lưu; bấm Lưu bản nháp trước')
            details = manager.store.details(manager.account_id,manager.page_key)
            if manager.topic.get().strip() != details.get('topic','').strip() or manager.notes.get('1.0','end').strip() != details.get('notes','').strip():
                reasons.append('ghi chú Page chưa lưu; lưu ghi chú trước')
    scheduler = facebook.page_scheduler
    if scheduler:
        for ident in scheduler.active:
            row = scheduler.store.get(ident)
            if row['state'] in {'preparing','submitting','verifying'} or row['request_id']:
                reasons.append('lượt đăng đang xử lý; chờ bằng chứng hoàn tất')
            elif row['state']=='armed' and row['due_at'] <= time.time()+120:
                reasons.append('một bài sắp đến giờ đăng')
    return list(dict.fromkeys(reasons))


def browser_restart_check(context):
    from teodub.facebook_page_identity import _can_switch,HOSTS
    from urllib.parse import urlsplit
    _can_switch(context)
    for tab in context.pages:
        if tab.is_closed() or urlsplit(tab.url).hostname not in HOSTS:
            continue
        if tab.evaluate("""() => [...document.querySelectorAll('[contenteditable="true"]')]
            .some(e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden'
                && !e.closest('[aria-hidden="true"],[inert]') && e.innerText.trim())"""):
            raise ValueError('Chrome còn nội dung đang soạn chưa lưu. Hoàn tất hoặc lưu bản nháp trước khi khởi động lại.')


def checkpoint(studio, campaign, facebook, notebook, browser_accounts):
    result = dict(workers=[], schedules=[], browser_accounts=list(browser_accounts),
        selected_tab=notebook.index(notebook.select()),campaign_directory=None,facebook_account=facebook.account_id)
    for role, owner, family, filename in [('single',studio,'runs','config.json'),
            ('campaign',campaign,'campaigns','campaign.json'),
            ('batch',studio.batch_controller,'campaign-batches','queue.json')]:
        process = owner.process
        if process is None or process.poll() is not None:
            continue
        raw = studio.current['directory'] if role=='single' else (getattr(owner,'worker_directory',None) or owner.directory)
        directory = job_directory(studio.data_dir,raw,family)
        result['workers'].append(dict(role=role,directory=str(directory),family=family,
            config_hash=worker_fingerprint(directory,role),filename=filename,process=process_identity(process)))
    if campaign.directory:
        result['campaign_directory'] = str(job_directory(studio.data_dir,campaign.directory,'campaigns'))
    scheduler = facebook.page_scheduler
    if scheduler:
        for ident in scheduler.active:
            row = scheduler.store.get(ident)
            if row['state'] != 'armed' or row['attempted_at'] or row['request_id']:
                raise ValueError('Lượt đăng chưa tới điểm có thể khởi động lại; tiếp tục chờ.')
            result['schedules'].append(dict(id=ident,account_id=row['account_id'],page_key=row['page_key'],
                fingerprint=schedule_fingerprint(row)))
    return result


def restart_command(data_dir, token, web_data_dir=None):
    project = Path(__file__).resolve().parents[1]
    if getattr(sys,'frozen',False):
        command = [sys.executable]
    else:
        python = project/'.venv'/'Scripts'/'pythonw.exe'
        if not python.is_file():
            raise ValueError('Thiếu môi trường Tèo; giữ ứng dụng hiện tại để kiểm tra.')
        command = [str(python),'-X','utf8','-B',str(project/'launch.py')]
    command += ['--data-dir',str(Path(data_dir).resolve()),'--restart-token',token]
    if web_data_dir:
        command += ['--web-data-dir',str(web_data_dir)]
    return command


def accept_transfer(data_dir, token):
    """Child preflight/ACK before old UI closes; no stores or workers started here."""
    folder = transfer_directory(data_dir,token)
    saved = read(folder/'transfer.json')
    if (saved.get('version') != 1 or saved.get('token') != token or saved.get('state') != 'preflight'
            or Path(saved['data_dir']).resolve() != Path(data_dir).resolve()
            or not 0 <= time.time()-saved['created_at'] < 600):
        raise ValueError('Lượt khởi động lại đã hết hạn hoặc đã được dùng.')
    parent = AttachedProcess(saved['parent'],control=False)
    for name in ('app','campaign_ui','campaign_library_ui','facebook_ui','page_manager_ui','page_ai_ui'):
        importlib.import_module('teodub.'+name)
    atomic_json(folder/'ready.json',dict(token=token,process=process_identity(type('Self',(),{'pid':os.getpid()})())))
    deadline = time.monotonic()+120
    while parent.poll() is None:
        if time.monotonic() >= deadline or (folder/'abort.json').exists():
            raise ValueError('Ứng dụng cũ chưa bàn giao; không mở bộ xử lý thứ hai.')
        time.sleep(.1)
    from teodub.pipeline import _job_lock
    with _job_lock(folder):
        saved = read(folder/'transfer.json')
        if saved.get('state') != 'committed' or saved.get('token') != token or (folder/'abort.json').exists():
            raise ValueError('Chưa có bàn giao hoàn tất; công việc không tự chạy lại.')
        saved['state'] = 'claimed'
        atomic_json(folder/'transfer.json',saved)
    return saved


def restore_transfer(studio, campaign, facebook, notebook, saved, *, attach=AttachedProcess):
    data = saved['checkpoint']
    messages = []
    for worker in data['workers']:
        try:
            path = job_directory(studio.data_dir,worker['directory'],worker['family'])
            expected = {'single':('runs','config.json'),'campaign':('campaigns','campaign.json'),
                'batch':('campaign-batches','queue.json')}[worker['role']]
            if (worker['family'],worker['filename']) != expected or worker_fingerprint(path,worker['role']) != worker['config_hash']:
                raise ValueError('Cấu hình công việc đã đổi; không tự chạy lại.')
            process = attach(worker['process'])
            if worker['role']=='single':
                studio.current = studio.owned_config(path)
                studio.process = process
                studio.result = None
                studio.event_offset = 0
                studio.event_pending = b''
                studio.autoplay_preview = studio.current.get('mode')=='preview'
                studio.read_events()
            elif worker['role']=='campaign':
                campaign.directory, campaign.process = path, process
                campaign.worker_directory = path
            else:
                studio.batch_controller.directory, studio.batch_controller.process = path, process
            messages.append('Đã nối lại '+worker['role']+'; giữ tiến trình cũ.')
        except (OSError,ValueError,KeyError,TypeError) as exc:
            messages.append('Cần kiểm tra công việc: '+str(exc))
    if data.get('campaign_directory'):
        try:
            campaign.directory = job_directory(studio.data_dir,data['campaign_directory'],'campaigns')
            campaign.display(read(campaign.directory/'campaign.json'))
        except (OSError,ValueError,KeyError) as exc:
            messages.append(str(exc))
    registry = facebook.store.load()
    accounts = {r['id'] for r in registry['accounts']}
    for ident in data['browser_accounts']:
        if ident in accounts:
            facebook.open_browser('https://www.facebook.com/',account_id=ident)
    scheduler = facebook.get_page_scheduler() if data['schedules'] else None
    for selected in data['schedules']:
        try:
            row = scheduler.store.get(selected['id'])
            if (row['account_id'],row['page_key']) != (selected['account_id'],selected['page_key']) or schedule_fingerprint(row) != selected['fingerprint']:
                raise ValueError('Lịch/Page/nội dung đã đổi; chưa bật lại.')
            if row['attempted_at'] or row['request_id'] or row['state'] not in {'paused','armed'}:
                raise ValueError('Lượt đăng đã được xử lý hoặc chưa rõ kết quả; kiểm tra Facebook.')
            scheduler.verify_snapshot(row)
            scheduler.start(row['id'])
            messages.append('Đã giữ lịch: '+row['source']['label'])
        except (OSError,ValueError,RuntimeError) as exc:
            messages.append('Chưa bật lại lịch '+selected['id']+': '+str(exc))
    if data.get('facebook_account') in accounts:
        facebook.store.select_account(data['facebook_account'])
        facebook.reload()
    index = data.get('selected_tab',0)
    if type(index) is int and 0 <= index < len(notebook.tabs()):
        notebook.select(index)
    studio.campaign_busy = bool(campaign.process and campaign.process.poll() is None) or studio.batch_controller.busy
    studio.refresh_buttons()
    return messages


class RestartController:
    def __init__(self, studio, campaign, facebook, notebook, status, *, web_data_dir=None):
        self.studio, self.campaign, self.facebook, self.notebook = studio,campaign,facebook,notebook
        self.status = status
        self.web_data_dir = web_data_dir
        self.phase = 'idle'
        self.child = self.folder = None
        self.accounts = []
        self.timer = None

    def show_report(self):
        if not self.folder or not (self.folder/'transfer.json').exists():
            self.status.set('Chưa có lượt khởi động lại trong phiên này.')
            return
        import tkinter as tk
        saved = read(self.folder/'transfer.json')
        modal = tk.Toplevel(self.studio.window)
        modal.title('Chi tiết khởi động lại Tèo')
        modal.geometry('850x520')
        text = tk.Text(modal,wrap='word')
        text.pack(fill='both',expand=True,padx=12,pady=12)
        text.insert('1.0','Trạng thái: '+saved['state']+'\n\n'+'\n'.join(saved.get('report',[]))
            +'\n\nNhật ký khởi động: '+str(self.folder/'startup.log'))
        text.configure(state='disabled')

    def request(self):
        if self.phase != 'idle' or self.studio.closing:
            return
        self.studio.restart_pending = True
        self.phase = 'waiting'
        self.status.set('Đã hẹn khởi động lại; video tiếp tục chạy. Chờ lượt AI/Facebook hoàn tất.')
        self.tick()

    def cancel(self):
        if self.phase=='idle':
            return
        if self.folder:
            try:
                atomic_json(self.folder/'abort.json',dict(at=time.time()))
            except OSError:
                pass  # Uncommitted child also requires parent exit + committed state.
        self.phase = 'idle'
        self.studio.restart_pending = False
        self.studio.restart_draining = False
        if self.timer:
            self.studio.window.after_cancel(self.timer)
            self.timer = None
        self.status.set('Đã hủy khởi động lại; công việc hiện tại tiếp tục.')
        # Profiles that already flushed are reopened rather than left disconnected.
        reopening = list(self.accounts)
        for ident in reopening:
            session = self.facebook.sessions.get(ident)
            if session and not session.pending:
                self.facebook.open_browser('https://www.facebook.com/',account_id=ident)
        self.accounts = []
        if reopening:
            def reopen():
                if self.studio.closed or self.studio.closing or self.phase != 'idle':
                    return
                for ident in reopening:
                    session = self.facebook.sessions.get(ident)
                    if session and not session.pending:
                        self.facebook.open_browser('https://www.facebook.com/',account_id=ident)
            self.studio.window.after(1000,reopen)

    def tick(self):
        self.timer = None
        if self.phase=='idle' or self.studio.closed:
            return
        try:
            if self.phase=='waiting':
                reasons = busy_reasons(self.studio,self.campaign,self.facebook)
                if reasons:
                    self.status.set('Chờ khởi động lại: '+' · '.join(reasons)+'. Video vẫn chạy.')
                else:
                    token = uuid.uuid4().hex
                    self.folder = transfer_directory(self.studio.data_dir,token)
                    atomic_json(self.folder/'transfer.json',dict(version=1,token=token,state='preflight',
                        data_dir=str(self.studio.data_dir),created_at=time.time(),parent=process_identity(type('Self',(),{'pid':os.getpid()})())))
                    with (self.folder/'startup.log').open('ab') as log:
                        self.child = subprocess.Popen(restart_command(self.studio.data_dir,token,self.web_data_dir),
                            stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    self.phase = 'preflight'
                    self.deadline = time.monotonic()+60
                    self.status.set('Đang kiểm tra bản Tèo mới; bản hiện tại và video vẫn chạy.')
            elif self.phase=='preflight':
                if self.child.poll() is not None:
                    raise ValueError('Bản mới chưa mở được; giữ bản hiện tại. Xem '+str(self.folder/'startup.log'))
                if time.monotonic()>self.deadline:
                    raise ValueError('Bản mới chưa phản hồi; giữ công việc hiện tại.')
                if (self.folder/'ready.json').is_file():
                    ready = read(self.folder/'ready.json')
                    if ready.get('token') != self.folder.name or not matches_started_process(ready.get('process',{}),self.child):
                        raise ValueError('Phản hồi bản mới không khớp lượt khởi động lại.')
                    if not busy_reasons(self.studio,self.campaign,self.facebook):
                        self.accounts = [ident for ident,session in self.facebook.sessions.items() if session.pending and session.opened]
                        self.studio.restart_draining = True
                        for ident in self.accounts:
                            session = self.facebook.sessions[ident]
                            session.restart_error = ''
                            session.commands.put(dict(action='restart_close',token=self.folder.name))
                        self.phase = 'draining'
                        self.deadline = time.monotonic()+45
                        self.status.set('Đang lưu phiên Chrome và bàn giao; video không bị dừng.')
            elif self.phase=='draining':
                if self.child.poll() is not None:
                    raise ValueError('Bản mới đã dừng; chưa bàn giao công việc.')
                for ident in self.accounts:
                    session = self.facebook.sessions[ident]
                    if getattr(session,'restart_error',''):
                        raise ValueError(session.restart_error)
                alive = any(session.worker and session.worker.is_alive() for session in self.facebook.sessions.values())
                if time.monotonic()>self.deadline:
                    raise ValueError('Chrome chưa lưu xong phiên; giữ Tèo để kiểm tra.')
                if not alive:
                    self.commit()
                    return
        except (OSError,ValueError,RuntimeError,KeyError) as exc:
            if self.folder and (self.folder/'transfer.json').exists():
                try:
                    saved = read(self.folder/'transfer.json')
                    saved.update(state='failed',report=[str(exc)])
                    atomic_json(self.folder/'transfer.json',saved)
                except (OSError,ValueError):
                    pass  # Disk failure must not keep live work quiesced indefinitely.
            self.cancel()
            self.status.set('Chưa khởi động lại: '+str(exc))
            return
        self.timer = self.studio.window.after(500,self.tick)

    def commit(self):
        reasons = [r for r in busy_reasons(self.studio,self.campaign,self.facebook)
            if r != 'một bài sắp đến giờ đăng']
        if reasons:
            raise ValueError('Chưa bàn giao: '+' · '.join(reasons))
        data = checkpoint(self.studio,self.campaign,self.facebook,self.notebook,self.accounts)
        self.studio.save_preferences()
        self.campaign.save_campaign_preferences()
        saved = read(self.folder/'transfer.json')
        saved.update(state='committed',checkpoint=data)
        atomic_json(self.folder/'transfer.json',saved)
        self.phase = 'committed'
        self.studio.stop_playback(refresh=False)
        # Do not call Studio.close(): it deliberately stops the single worker.
        self.studio.closed = True
        self.studio.window.after_cancel(self.studio.timer)
        self.studio.window.destroy()
