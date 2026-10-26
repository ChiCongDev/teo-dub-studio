"""Desktop actions for a persistent multi-campaign supervisor; never auto-start.

The one exception is a hand-over the owner asked for: a campaign created while another runs on its own joins it in
turns, and the rotation starts once the running campaign has finished its current video (owner 23/10/2026).
"""
from pathlib import Path
import subprocess

from teodub.campaign import ROTATION_REQUEST, ROTATION_WAIT, is_running, read_json


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

    def start(self, directories, *, order='serial'):
        self._ensure_idle()
        from teodub.campaign_batch import create_batch
        self.directory = create_batch(self.data_dir, directories, order=order)
        self._spawn()

    def rotation(self):
        """The saved queue when it is a rotation (xen kẽ), else None."""
        from teodub.campaign_batch import ROTATE
        if self.directory is None:
            return None
        try:
            queue = read_json(self.directory/'queue.json')
        except (OSError, ValueError):
            return None
        return queue if isinstance(queue, dict) and queue.get('order') == ROTATE else None

    def join(self, directory, running=None):
        """Put a just-created campaign into the work that runs now; never starts anything itself.

        'joined': the running rotation takes it at its next turn. 'handoff': `running` (a campaign running on its
        own) finishes its current video, then both run in turns. 'serial': a one-after-another list is running and
        keeps its order. None: nothing runs.
        """
        from teodub.campaign_batch import add_to_rotation, create_batch
        directory = Path(directory)
        if self.busy and self.directory is not None:
            if self.rotation() is None:
                return 'serial'
            add_to_rotation(self.directory, directory)
            return 'joined'
        queue = self.rotation()
        if queue is not None and queue.get('handoff_from') and queue.get('status') == 'ready':
            # A hand-over is still waiting for the running campaign's current video; the rotation takes this one too.
            add_to_rotation(self.directory, directory)
            return 'handoff'
        if running is None or Path(running).resolve() == directory.resolve():
            return None
        running = Path(running)
        self.directory = create_batch(self.data_dir, [running, directory], order='rotate',
                                      handoff_from=running, first_index=1)
        (running/ROTATION_REQUEST).write_text('Chạy xen kẽ sau video đang làm.\n', encoding='utf-8')
        return 'handoff'

    def complete_handoff(self):
        """Start the rotation the owner asked for once its running campaign has finished its current video.

        Returns 'started', 'paused' (that campaign stopped for another reason: the rotation waits for the owner's
        Chạy / Tiếp tục) or None (nothing to do yet).
        """
        queue = self.rotation()
        if queue is None or not queue.get('handoff_from') or queue.get('status') != 'ready' or self.busy:
            return None
        source = Path(queue['handoff_from'])
        if is_running(source) or is_running(self.data_dir/'execution'):
            return None  # Its current video is still being made.
        manifest = read_json(source/'campaign.json')
        from teodub.campaign_batch import _leave_requested
        # Handed over, finished, or stopped on its own tab (the other links still start).
        if (manifest.get('message') == ROTATION_WAIT or manifest.get('status') in ('completed', 'needs_attention')
                or _leave_requested(self.directory, source.resolve())):
            self._ensure_idle()
            (source/ROTATION_REQUEST).unlink(missing_ok=True)
            self._spawn()
            return 'started'
        (source/ROTATION_REQUEST).unlink(missing_ok=True)
        queue.pop('handoff_from', None)
        queue.update(status='paused', message=f'Chiến dịch {manifest.get("name", "")} đã dừng trước khi chuyển sang '
                     'chạy xen kẽ. Bấm Chạy / Tiếp tục để chạy xen kẽ.')
        from teodub.common import atomic_json
        atomic_json(self.directory/'queue.json', queue)
        return 'paused'

    def includes(self, directory, queue=None):
        queue = queue if queue is not None else self.rotation()
        target = str(Path(directory).resolve())
        return bool(queue) and any(row.get('directory') == target for row in queue.get('campaigns', []))

    # One sub-tab per link (owner 23/10/2026): Chạy / Dừng link này act on that link only. The machine still makes
    # one video at a time, so every running link takes its turn in the one rotation.
    def _standalone(self, probe=False):
        """The campaign running on its own (the campaign tab's worker), if any. probe=True (owner actions only) also
        finds one this window did not start, e.g. after Tèo was reopened while it ran."""
        tab = self.campaign_tab
        process = getattr(tab, 'process', None)
        directory = getattr(tab, 'worker_directory', None)
        if process is not None and process.poll() is None and directory is not None:
            return Path(directory).resolve()
        if probe and not self.busy and is_running(self.data_dir/'execution'):
            for path in sorted((self.data_dir/'campaigns').glob('*/campaign.json')):
                if is_running(path.parent):
                    return path.parent.resolve()
        return None

    def run_link(self, directory):
        """Chạy on a link's tab: 'joined' (takes its turns in the running rotation), 'handoff' (after the video the
        running campaign is making), 'started' (nothing ran) or 'already'."""
        from teodub.campaign_batch import add_to_rotation
        directory = Path(directory).resolve()
        running = self._standalone(probe=True)
        if running == directory:
            return 'already'
        if self.busy and self.directory is not None:
            if self.rotation() is None:
                raise ValueError('Một danh sách đang chạy lần lượt; đợi danh sách xong hoặc dừng nó rồi bấm Chạy.')
            add_to_rotation(self.directory, directory)
            return 'joined'
        queue = self.rotation()
        if queue is not None and queue.get('handoff_from') and queue.get('status') == 'ready':
            add_to_rotation(self.directory, directory)
            return 'handoff'
        if running is not None:
            return self.join(directory, running)
        self._ensure_idle()
        if queue is not None and queue.get('status') in ('ready', 'paused', 'needs_attention') and self.includes(directory, queue):
            # A stopped or paused rotation that has this link: bring it back and run on.
            add_to_rotation(self.directory, directory)
            self._spawn()
            return 'started'
        self.start([directory], order='rotate')
        return 'started'

    def retry_link(self, directory):
        """Thử lại video lỗi on a link's tab (owner 24/10/2026): every video of the link failed now gets one more try in
        its turns, then the link runs like its Chạy, even while other links run. (how many, run_link's outcome); with
        nothing failed, nothing runs. 'already' (it runs on its own): the tries come at its next run."""
        from teodub.campaign import request_retry
        directory = Path(directory).resolve()
        count = request_retry(directory)
        return count, (self.run_link(directory) if count else None)

    def stop_link(self, directory):
        """Dừng link này: only this link stops (its current video ends, keeping finished steps); 'stopping' or None."""
        from teodub.campaign_batch import remove_from_rotation
        from teodub.common import atomic_json
        directory = Path(directory).resolve()
        queue = self.rotation()
        if self._standalone(probe=True) == directory:
            if queue is not None and queue.get('handoff_from') == str(directory) and queue.get('status') == 'ready':
                remove_from_rotation(self.directory, directory)  # The others still start after it.
            (directory/'stop.request').touch()
            return 'stopping'
        row = next((row for row in (queue or {}).get('campaigns', []) if row.get('directory') == str(directory)), None)
        if row is None or row['status'] in ('completed', 'needs_attention', 'stopped'):
            if is_running(directory):  # Running in a one-after-another list: stop it as Dừng always did.
                (directory/'stop.request').touch()
                return 'stopping'
            return None
        if self.busy or (queue.get('handoff_from') and queue.get('status') == 'ready'):
            remove_from_rotation(self.directory, directory)
            if self.busy and queue.get('active_directory') == str(directory):
                (directory/'stop.request').touch()
            return 'stopping'
        from teodub.campaign_batch import LINK_STOPPED
        row.update(status='stopped', message=LINK_STOPPED)  # Nothing runs: the desktop is the only writer now.
        atomic_json(self.directory/'queue.json', queue)
        return 'stopping'

    def link_state(self, directory, manifest):
        """What one link does now, from saved files and the campaign tab's own process (no lock probes)."""
        target = Path(directory).resolve()
        if self._standalone() == target:
            return 'running'
        queue = self.rotation()
        row = next((row for row in (queue or {}).get('campaigns', []) if row.get('directory') == str(target)), None)
        status = manifest.get('status')
        if queue is not None and queue.get('status') == 'running' and self.studio.campaign_busy:
            if row is not None and queue.get('active_directory') == str(target):
                return 'running'
            request = self._waiting_request(target)
            if request == 'join':
                return 'waiting'
            if row is not None and request == 'leave':
                return 'stopped'
            if row is not None:
                return {'pending': 'waiting', 'paused': 'waiting', 'running': 'running', 'stopped': 'stopped',
                        'completed': 'done', 'needs_attention': 'attention'}.get(row['status'], 'waiting')
        if (row is not None and queue.get('status') == 'ready' and queue.get('handoff_from')
                and row['status'] == 'pending' and row['directory'] != queue['handoff_from']):
            return 'handoff'
        if row is not None and row['status'] == 'stopped' and status not in ('completed', 'needs_attention'):
            return 'stopped'
        if status == 'completed':
            return 'done'
        if status == 'needs_attention':
            return 'attention'
        if status == 'running':
            return 'running' if self.studio.campaign_busy else 'interrupted'
        if status == 'paused':
            return 'paused'
        return 'ready'

    def _waiting_request(self, target):
        """'join' or 'leave' when this link's last Chạy / Dừng link này still waits for the running worker, which takes
        them between two videos (owner 24/10/2026: after Chạy the tab still read 'đã dừng', so it was pressed 3 times)."""
        from teodub.campaign_batch import _requests
        last = None
        for path in _requests(self.directory):
            try:
                if read_json(path).get('directory') == str(target):
                    last = 'leave' if path.name.startswith('leave-') else 'join'
            except (OSError, ValueError, AttributeError):
                continue  # Taken by the worker meanwhile, or not a request.
        return last

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
