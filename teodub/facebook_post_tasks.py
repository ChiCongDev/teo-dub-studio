"""Cooperative browser jobs: every Playwright call stays on its owning thread."""
from collections import deque

from teodub.common import check_cancel


class BrowserPostTasks:
    """Interleave upload waits and result reads without overlapping composers."""

    def __init__(self):
        self.tasks = {}
        self.order = deque()

    @property
    def busy(self):
        return any(task['phase'] != 'verifying' for task in self.tasks.values())

    def owns_post(self, account_id, page_key, post_id):
        scope = (account_id, page_key, post_id)
        return any(task['scope'] == scope for task in self.tasks.values())

    def add(self, request_id, iterator, *, scope, cancel, event):
        if request_id in self.tasks or self.owns_post(*scope):
            iterator.close()
            raise ValueError('Lượt bài này đang được xử lý/xác minh; không tạo thêm lượt gửi.')
        self.tasks[request_id] = dict(iterator=iterator, scope=scope, cancel=cancel,
                                      event=event, phase='preparing', message='')
        self.order.append(request_id)

    @staticmethod
    def _close(task):
        # Generator finalizers release post locks and persist an uncertain
        # result if a browser closes after its one permitted publish click.
        task['iterator'].close()

    def step(self):
        if not self.order:
            return
        request_id = self.order.popleft()
        task = self.tasks[request_id]
        try:
            check_cancel(task['cancel'])
            observation = next(task['iterator'])
            if not isinstance(observation, dict) or observation.get('phase') not in {'preparing', 'verifying'}:
                raise ValueError('Phản hồi tiến trình Chrome không hợp lệ; đã dừng lượt này.')
            phase = observation['phase']
            message = str(observation.get('message') or ('Đã gửi; đang xác minh kết quả trên Facebook…'
                if phase == 'verifying' else 'Đang chuẩn bị bài trong Chrome…'))
            if phase != task['phase'] or message != task['message']:
                task['event']('post_verifying' if phase == 'verifying' else 'post_progress', message)
            task.update(phase=phase, message=message)
        except StopIteration as result:
            self.tasks.pop(request_id)
            if not isinstance(result.value, dict):
                task['event']('post_error', 'Chrome chưa trả về kết quả hợp lệ; kiểm tra Page trước khi tiếp tục.')
            else:
                task['event']('post_result', result.value.get('message', 'Đã hoàn tất thao tác bài trong Chrome.'),
                              result=result.value)
        except Exception as exc:
            self.tasks.pop(request_id)
            try:
                self._close(task)
            except Exception:
                pass  # The original, scoped failure remains the useful one.
            task['event']('post_error', str(exc) or type(exc).__name__)
        else:
            self.order.append(request_id)

    def close(self):
        tasks = list(self.tasks.values())
        self.tasks.clear()
        self.order.clear()
        for task in tasks:
            try:
                self._close(task)
            except Exception as exc:
                message = str(exc) or type(exc).__name__
            else:
                message = 'Phiên Chrome đã đóng hoặc gián đoạn. Kiểm tra Page trước; không tự gửi lại.'
            task['event']('post_error', message)
