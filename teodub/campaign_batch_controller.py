"""Desktop actions for a persistent multi-campaign supervisor; never auto-start."""
from pathlib import Path
import subprocess

from teodub.campaign import is_running, read_json


class CampaignBatchController:
    def __init__(self, studio, campaign_tab):
        self.studio, self.campaign_tab = studio, campaign_tab
        self.data_dir = studio.data_dir
        self.directory = None
        self.process = None
        self.log_start = 0
        try:
            saved = read_json(self.data_dir/'last-campaign-batch.json')
            candidate = Path(saved['directory'])
            if candidate.resolve().parent == (self.data_dir/'campaign-batches').resolve() and not candidate.is_symlink():
                self.directory = candidate
        except (OSError, ValueError, KeyError, TypeError):
            pass

    @property
    def busy(self):
        return ((self.process is not None and self.process.poll() is None)
                or (self.directory is not None and is_running(self.directory)))

    def snapshot(self):
        if self.directory is None:
            return None
        try:
            result = read_json(self.directory/'queue.json')
            if not isinstance(result, dict) or not isinstance(result.get('campaigns'), list):
                raise ValueError('Dữ liệu lượt chạy không hợp lệ.')
            if self.busy:
                result['status'] = 'running'
            elif result.get('status') == 'running':
                result['status'] = 'paused'
                result['message'] = 'Lượt chạy bị gián đoạn. Bấm Tiếp tục để chạy phần còn lại.'
            if not self.busy and self.process is not None and self.process.poll() not in (None, 0):
                if result.get('status') == 'ready':
                    result['status'] = 'paused'
                    result['message'] = 'Lượt chạy chưa khởi động được. Kiểm tra lại rồi bấm Tiếp tục.'
                try:
                    with (self.directory/'batch-worker.log').open('rb') as log:
                        log.seek(0, 2)
                        log.seek(max(self.log_start, log.tell()-4096))
                        lines = log.read().decode('utf-8', errors='replace').splitlines()
                    if lines:
                        result['status'] = 'paused'
                        result['message'] = 'Chưa khởi động: '+lines[-1][:500]
                except OSError:
                    pass
            return result
        except (OSError, ValueError):
            return dict(status='paused', message='Chưa đọc được lượt chạy đã lưu.', campaigns=[], active_directory=None)

    def _ensure_idle(self):
        if (self.busy or self.studio.process is not None or self.studio.login_pending
                or self.studio.closing or self.studio.campaign_busy or self.campaign_tab.scanning
                or getattr(self.studio, 'voice_learning_busy', False) is True
                or is_running(self.data_dir/'execution')):
            raise ValueError('Đợi công việc đang chạy hoàn tất hoặc bấm Dừng trước.')

    def start(self, directories):
        self._ensure_idle()
        from teodub.campaign_batch import create_batch
        self.directory = create_batch(self.data_dir, directories)
        self._spawn()

    def resume(self):
        self._ensure_idle()
        if self.directory is None:
            raise ValueError('Chưa có lượt chạy nhiều chiến dịch được lưu.')
        snapshot = self.snapshot()
        if snapshot.get('status') == 'completed':
            raise ValueError('Lượt này đã hoàn tất. Chọn chiến dịch cho một lượt mới.')
        self._spawn()

    def _spawn(self):
        from teodub.app import worker_command
        from teodub.campaign_batch import read_batch
        read_batch(self.directory)
        # Clearing happens before launch; a stop clicked during startup survives.
        (self.directory/'stop.request').unlink(missing_ok=True)
        command = worker_command(self.directory/'unused.json')
        command = command[:command.index('--worker')]+[
            '--campaign-batch-worker', '--directory', str(self.directory), '--keep-stop']
        with (self.directory/'batch-worker.log').open('ab') as log:
            log.seek(0, 2)
            self.log_start = log.tell()
            self.process = subprocess.Popen(command, cwd=str(self.directory), stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.studio.campaign_busy = True
        self.studio.refresh_buttons()
        self.campaign_tab.refresh_buttons()

    def stop(self):
        if self.directory is not None and self.busy:
            (self.directory/'stop.request').touch()
