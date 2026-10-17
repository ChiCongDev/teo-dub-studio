"""Read completed final answers from existing local Codex chats.

The documented app-server thread/list and thread/read methods do not resume a
chat or ask a model to run. This module deliberately exposes no write, resume,
inference, authentication or general-purpose RPC interface. Each operation owns
its short-lived stdio child and never terminates the user's desktop/daemon.
"""
import json
import math
import os
import queue
import re
import subprocess
import threading
import time

from teodub.codex_translation import find_codex, _environment


REQUEST_TIMEOUT = 15.0
OUTPUT_LIMIT = 8 * 1024 * 1024
MESSAGE_LIMIT = 1000
TEXT_LIMIT = 200000
_THREAD_ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
_READ_METHODS = frozenset(('initialize', 'thread/list', 'thread/read'))


class CodexChatError(ValueError):
    """A bounded, user-facing read-only connection failure."""


def _thread_id(value):
    if not isinstance(value, str) or not _THREAD_ID.fullmatch(value):
        raise CodexChatError('ID cuộc trò chuyện Codex không hợp lệ. Chọn lại cuộc trò chuyện trong danh sách.')
    return value


def _timeout(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 60:
        raise CodexChatError('Thời gian chờ kết nối Codex phải nằm trong khoảng 0–60 giây.')
    return float(value)


def _protocol_error():
    return CodexChatError('Codex trả dữ liệu không đúng định dạng đọc cuộc trò chuyện. Cập nhật Codex rồi thử kết nối lại.')


def _remote_error(error):
    # Do not expose arbitrary server messages (which may contain local paths,
    # conversation contents or credentials) in a UI/error log.
    if isinstance(error, dict) and error.get('code') == -32601:
        return CodexChatError('Bản Codex này chưa hỗ trợ đọc cuộc trò chuyện. Cập nhật Codex; Tèo không tự gửi lại hay chạy AI.')
    if isinstance(error, dict) and error.get('code') == -32602:
        return CodexChatError('Bản Codex này chưa tương thích cách đọc cuộc trò chuyện. Cập nhật Codex rồi chọn lại chat.')
    return CodexChatError('Chưa đọc được cuộc trò chuyện Codex. Mở đúng chat trên máy này, kiểm tra chat còn được lưu rồi thử lại.')


class _ReadClient:
    def __init__(self, timeout=REQUEST_TIMEOUT):
        self.timeout = _timeout(timeout)
        self.process = None
        self.reader = None
        self.messages = queue.Queue(maxsize=64)
        self.stop = threading.Event()
        self.next_id = 0
        self.deadline = None

    def __enter__(self):
        try:
            command = [find_codex(), 'app-server', '--stdio', '-c', 'analytics.enabled=false']
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=_environment(), shell=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0,
            )
            self.deadline = time.monotonic() + self.timeout
            self.reader = threading.Thread(target=self._read_output, daemon=True, name='teo-codex-chat-read')
            self.reader.start()
            self._request('initialize', {'clientInfo': {
                'name': 'teo_dub_page_reader', 'title': 'Tèo Page read-only chat connection', 'version': '1.0.0',
            }})
            self._write({'method': 'initialized'})
            return self
        except OSError:
            self.close()
            raise CodexChatError('Không mở được kết nối đọc Codex trên máy. Kiểm tra Codex đã cài rồi thử lại.') from None
        except Exception:
            self.close()
            raise

    def __exit__(self, *unused):
        self.close()

    def _put(self, value):
        while not self.stop.is_set():
            try:
                self.messages.put(value, timeout=0.05)
                return
            except queue.Full:
                continue

    def _read_output(self):
        total = 0
        try:
            while not self.stop.is_set():
                line = self.process.stdout.readline(OUTPUT_LIMIT - total + 1)
                if not line:
                    self._put(CodexChatError('Kết nối đọc Codex đã đóng trước khi trả kết quả. Mở Codex rồi thử lại.'))
                    return
                total += len(line)
                if total > OUTPUT_LIMIT:
                    self._put(CodexChatError('Lịch sử chat vượt giới hạn đọc an toàn. Dùng một chat lập kế hoạch ngắn hơn.'))
                    return
                if not line.strip():
                    continue
                try:
                    value = json.loads(line.decode('utf-8'))
                except (UnicodeError, ValueError):
                    self._put(_protocol_error())
                    return
                if not isinstance(value, dict):
                    self._put(_protocol_error())
                    return
                self._put(value)
        except (OSError, ValueError):
            if not self.stop.is_set():
                self._put(CodexChatError('Kết nối đọc Codex bị ngắt. Thử kết nối lại; chat hiện tại không bị thay đổi.'))

    def _write(self, value):
        try:
            self.process.stdin.write((json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8'))
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise CodexChatError('Không gửi được yêu cầu đọc Codex. Mở Codex rồi thử kết nối lại.') from None

    def _request(self, method, params):
        if method not in _READ_METHODS:
            raise CodexChatError('Kết nối này chỉ cho phép đọc danh sách và câu trả lời đã hoàn tất.')
        self.next_id += 1
        request_id = self.next_id
        self._write({'id': request_id, 'method': method, 'params': params})
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise CodexChatError('Codex chưa trả kết quả đọc trong thời gian chờ. Thử lại sau; Tèo không chạy AI hay đổi chat.')
            try:
                result = self.messages.get(timeout=remaining)
            except queue.Empty:
                raise CodexChatError('Codex chưa trả kết quả đọc trong thời gian chờ. Thử lại sau; Tèo không chạy AI hay đổi chat.') from None
            if isinstance(result, Exception):
                raise result
            if 'id' not in result:
                if isinstance(result.get('method'), str):
                    continue  # Notifications carry no usable chat history.
                raise _protocol_error()
            # Unexpected server requests are never answered with approval, auth
            # or tool output. A read operation must not need any such request.
            if 'method' in result or result['id'] != request_id or isinstance(result['id'], bool):
                raise _protocol_error()
            if 'error' in result:
                raise _remote_error(result['error'])
            if not isinstance(result.get('result'), dict):
                raise _protocol_error()
            return result['result']

    def close(self):
        process = self.process
        if process is None:
            return
        self.stop.set()
        try:
            process.stdin.close()
        except (OSError, ValueError, AttributeError):
            pass
        try:
            process.wait(timeout=0.35)
        except subprocess.TimeoutExpired:
            # Only the Popen instance created above is touched. No PID search,
            # daemon stop, taskkill or desktop/window close operation is used.
            try:
                process.terminate()
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                pass
        except OSError:
            pass
        if self.reader is not None:
            self.reader.join(timeout=0.2)
        if self.reader is None or not self.reader.is_alive():
            try:
                process.stdout.close()
            except (OSError, ValueError, AttributeError):
                pass
        self.process = None


def _summary(thread):
    if not isinstance(thread, dict):
        raise _protocol_error()
    identifier = _thread_id(thread.get('id'))
    name = thread.get('name')
    preview = thread.get('preview')
    title = name if isinstance(name, str) and name.strip() else preview
    if not isinstance(title, str):
        title = identifier
    status = thread.get('status')
    status = status.get('type') if isinstance(status, dict) else status
    if not isinstance(status, str):
        status = 'unknown'
    updated = thread.get('updatedAt')
    if isinstance(updated, bool) or not isinstance(updated, (int, float)) or not math.isfinite(updated):
        updated = None
    cwd = thread.get('cwd')
    return {'id': identifier, 'title': title[:256], 'status': status[:64],
            'updated_at': updated, 'cwd': cwd[:2048] if isinstance(cwd, str) else ''}


def list_threads(search_term='', limit=30, *, timeout=REQUEST_TIMEOUT):
    """List stored local interactive chats; never resume or run a model."""
    if not isinstance(search_term, str) or len(search_term) > 200:
        raise CodexChatError('Từ tìm chat phải là văn bản tối đa 200 ký tự.')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise CodexChatError('Số chat cần lấy phải từ 1 đến 100.')
    params = {'limit': limit, 'sortKey': 'updated_at', 'sortDirection': 'desc',
              'useStateDbOnly': True, 'archived': False,
              'sourceKinds': ['cli', 'vscode', 'appServer']}
    if search_term.strip():
        params['searchTerm'] = search_term.strip()
    with _ReadClient(timeout) as client:
        result = client._request('thread/list', params)
    records = result.get('data')
    if not isinstance(records, list) or len(records) > limit:
        raise _protocol_error()
    summaries = [_summary(thread) for thread in records]
    if len({item['id'] for item in summaries}) != len(summaries):
        raise _protocol_error()
    return summaries


def read_thread(thread_id, *, timeout=REQUEST_TIMEOUT):
    """Read only explicit final answers from completed turns of one exact chat.

    A runtime status of notLoaded is normal for this independent reader and is
    not proof that the desktop's chat is idle. In-progress/interrupted/failed
    turns, commentary, reasoning, user inputs and tool outputs are excluded.
    """
    thread_id = _thread_id(thread_id)
    with _ReadClient(timeout) as client:
        result = client._request('thread/read', {'threadId': thread_id, 'includeTurns': True})
    thread = result.get('thread')
    summary = _summary(thread)
    if summary['id'] != thread_id:
        raise CodexChatError('Codex trả một chat khác với chat đã chọn. Chọn lại chat; chưa nhập bất kỳ kế hoạch nào.')
    turns = thread.get('turns')
    if not isinstance(turns, list):
        raise _protocol_error()
    messages = []
    for turn in turns:
        if not isinstance(turn, dict):
            raise _protocol_error()
        if turn.get('status') != 'completed':
            continue
        turn_id = turn.get('id')
        items = turn.get('items')
        if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 200 or not isinstance(items, list):
            raise _protocol_error()
        for item in items:
            if not isinstance(item, dict):
                raise _protocol_error()
            if item.get('type') != 'agentMessage' or item.get('phase') != 'final_answer':
                continue
            if item.get('status', 'completed') != 'completed':
                continue
            body = item.get('text')
            if not isinstance(body, str) or len(body) > TEXT_LIMIT:
                raise _protocol_error()
            if body.strip():
                messages.append({'turn_id': turn_id, 'text': body,
                                 'phase': 'final_answer', 'turn_status': 'completed'})
                if len(messages) > MESSAGE_LIMIT:
                    raise CodexChatError('Chat có quá nhiều câu trả lời để nhập an toàn. Dùng một chat lập kế hoạch ngắn hơn.')
    summary['messages'] = messages
    return summary
