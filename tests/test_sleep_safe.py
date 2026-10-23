"""Sleep/wake safety of local process limits and render work-folder cleanup; no encoder, GPU or engine runs.

21/10 17:34 the computer was put to sleep during a render and woke at 08:23. The render's
limit counted the 14 h 48 min of sleep, stopped ffmpeg 12 s before the end, and a file
Windows still held turned the real error into WinError 32.
"""
import itertools
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from teodub import render, speech_audio, subtitle_clean
from teodub.common import SLEEP_GAP_S, AwakeTime, atomic_json
from tests import test_pipeline as pipeline_fixtures

NIGHT = 53302.9  # seconds asleep on 21/10 → 22/10


class AwakeTimeTests(unittest.TestCase):
    def test_short_polls_count_and_a_long_gap_is_sleep(self):
        awake = AwakeTime(100.)
        self.assertAlmostEqual(awake.update(110.), 10)
        self.assertAlmostEqual(awake.update(110.+SLEEP_GAP_S), 10+SLEEP_GAP_S)
        self.assertAlmostEqual(awake.update(110.+SLEEP_GAP_S+NIGHT), 10+SLEEP_GAP_S)
        self.assertAlmostEqual(awake.update(115.+SLEEP_GAP_S+NIGHT), 15+SLEEP_GAP_S)

    def test_a_clock_going_backwards_adds_nothing(self):
        awake = AwakeTime(50.)
        self.assertEqual(awake.update(40.), 0)
        self.assertAlmostEqual(awake.update(45.), 5)


class Encoder:
    """Stand-in for a local process that finishes after a number of polls."""
    def __init__(self, polls=None, returncode=0):
        self.polls, self.final, self.returncode = polls, returncode, None
        self.terminated = self.killed = False

    def poll(self):
        if self.returncode is None and self.polls is not None:
            self.polls -= 1
            if self.polls <= 0:
                self.returncode = self.final
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True


class ProcessLimitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='teodub-sleep-safe-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_render_encoder_continues_after_the_computer_wakes(self):
        encoder = Encoder(polls=4)
        clock = iter([0, 10, 10+NIGHT, 20+NIGHT, 30+NIGHT, 40+NIGHT])
        with patch.object(render.subprocess, 'Popen', return_value=encoder), \
                patch.object(render.time, 'monotonic', side_effect=clock), patch.object(render.time, 'sleep'):
            render._run_ffmpeg(['ffmpeg.exe', 'a.mp4'], self.directory, 1, None, None)
        self.assertFalse(encoder.terminated)

    def test_render_encoder_still_stops_after_its_limit_of_awake_time(self):
        encoder = Encoder()
        with patch.object(render.subprocess, 'Popen', return_value=encoder), \
                patch.object(render.time, 'monotonic', side_effect=itertools.count(0, 30)), \
                patch.object(render.time, 'sleep'):
            with self.assertRaisesRegex(ValueError, 'quá thời gian chờ'):
                render._run_ffmpeg(['ffmpeg.exe', 'a.mp4'], self.directory, 1, None, None)
        self.assertTrue(encoder.terminated)

    def inpaint(self, clock, polls):
        source = self.directory/'source.mp4'
        source.write_bytes(b'original picture')
        workers = []

        def start(command, **kwargs):
            request = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
            worker = Encoder(polls=polls)
            original = worker.poll

            def poll():
                if original() == 0 and not Path(request['target']).exists():
                    Path(request['target']).write_bytes(b'cleaned picture')
                    atomic_json(request['status'], dict(status='completed', frames=12, percent=100))
                return worker.returncode
            worker.poll = poll
            workers.append(worker)
            return worker
        with patch.object(subtitle_clean, 'runtime_ready'), \
                patch.object(subtitle_clean.subprocess, 'Popen', side_effect=start), \
                patch.object(subtitle_clean.time, 'monotonic', side_effect=clock), \
                patch.object(subtitle_clean.time, 'sleep'):
            try:
                return subtitle_clean.clean_video(source, {'subtitle_clean': SETTINGS}, self.directory/'clean')
            finally:
                self.workers = workers

    def test_sub_removal_continues_after_the_computer_wakes(self):
        result = self.inpaint(iter([0, 10, 10+NIGHT, 20+NIGHT, 30+NIGHT, 40+NIGHT]), polls=4)
        self.assertEqual(Path(result['path']).read_bytes(), b'cleaned picture')
        self.assertFalse(self.workers[0].terminated)

    def test_sub_removal_still_stops_after_five_awake_minutes_without_progress(self):
        with self.assertRaisesRegex(ValueError, 'không cập nhật sau 5 phút'):
            self.inpaint(itertools.count(0, 30), polls=None)
        self.assertTrue(self.workers[0].terminated)

    def tempo(self, clock, encoder):
        source = self.directory/'voice.wav'
        source.write_bytes(b'RIFF synthetic voice')
        with patch('imageio_ffmpeg.get_ffmpeg_exe', return_value='ffmpeg.exe'), \
                patch.object(speech_audio.subprocess, 'Popen', return_value=encoder), \
                patch.object(speech_audio.time, 'monotonic', side_effect=clock), \
                patch.object(speech_audio.time, 'sleep'):
            speech_audio.apply_tempo(source, self.directory/'paced'/'voice.wav', 1.1)

    def test_voice_tempo_is_not_stopped_by_a_sleep_gap(self):
        # The stand-in finishes with an error code, so reaching that error proves no time limit fired.
        encoder = Encoder(polls=4, returncode=1)
        with self.assertRaisesRegex(ValueError, 'Chưa chỉnh được nhịp đọc'):
            self.tempo(iter([0, 10, 10+NIGHT, 20+NIGHT, 30+NIGHT]), encoder)
        self.assertFalse(encoder.terminated)

    def test_voice_tempo_still_stops_after_its_limit_of_awake_time(self):
        encoder = Encoder()
        with self.assertRaisesRegex(ValueError, 'quá thời gian chờ'):
            self.tempo(itertools.count(0, 30), encoder)
        self.assertTrue(encoder.terminated)


SETTINGS = dict(enabled=True, engine=subtitle_clean.ENGINE, region=[.2, .71, .8, .76])


class RenderWorkFolderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='teodub-render-work-')
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)/'Video 10_20-10-2026'
        self.output.mkdir()

    def test_the_render_error_is_kept_when_a_file_stays_locked(self):
        locked = PermissionError(13, 'mock: the process cannot access the file')
        with patch.object(render.shutil, 'rmtree', side_effect=locked) as remove, \
                patch.object(render.time, 'sleep') as wait:
            with self.assertRaisesRegex(ValueError, 'quá thời gian chờ'):
                with render._work_directory(self.output) as work:
                    (work/'expected.m4a').write_bytes(b'audio')
                    raise ValueError('Dựng video quá thời gian chờ. Bản xuất chưa được công bố.')
        self.assertEqual(remove.call_count, len(render.CLEANUP_WAITS)+1)
        self.assertEqual([call.args[0] for call in wait.call_args_list], list(render.CLEANUP_WAITS))
        self.assertTrue(work.name.startswith(render.WORK_PREFIX))
        self.assertTrue((work/'expected.m4a').is_file())

    def test_a_completed_render_is_not_failed_by_a_locked_work_file(self):
        with patch.object(render.shutil, 'rmtree', side_effect=PermissionError(13, 'mock lock')), \
                patch.object(render.time, 'sleep'):
            with render._work_directory(self.output) as work:
                (work/'final.srt').write_text('1', encoding='utf-8')
        self.assertTrue(work.is_dir())

    def test_work_folder_is_removed_normally(self):
        with render._work_directory(self.output) as work:
            (work/'narration.wav').write_bytes(b'audio')
        self.assertFalse(work.exists())
        self.assertEqual(list(self.output.iterdir()), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing rules')
    def test_a_file_windows_still_holds_is_removed_once_released(self):
        work = Path(tempfile.mkdtemp(prefix=render.WORK_PREFIX, dir=self.output))
        handle = (work/'expected.m4a').open('wb')
        self.addCleanup(handle.close)
        release = threading.Timer(.4, handle.close)
        self.addCleanup(release.cancel)
        release.start()
        self.assertTrue(render._remove_tree(work))
        self.assertFalse(work.exists())

    def test_leftover_work_is_cleared_and_the_empty_folder_removed(self):
        reference = self.output/(render.WORK_PREFIX+'a2n1yv45')/'mix-reference'
        reference.mkdir(parents=True)
        (reference/'expected.m4a').write_bytes(b'audio')
        render.clear_leftover_work(self.output)
        self.assertFalse(self.output.exists())

    def test_published_or_unknown_files_keep_the_folder(self):
        (self.output/(render.WORK_PREFIX+'x1')).mkdir()
        (self.output/'Video 10_20-10-2026.srt').write_text('1', encoding='utf-8')
        (self.output/(render.WORK_PREFIX+'note.txt')).write_text('a file, not a work folder', encoding='utf-8')
        render.clear_leftover_work(self.output)
        self.assertEqual(sorted(p.name for p in self.output.iterdir()),
                         [render.WORK_PREFIX+'note.txt', 'Video 10_20-10-2026.srt'])

    def test_a_missing_folder_is_left_alone(self):
        render.clear_leftover_work(self.output/'absent')
        self.assertTrue(self.output.is_dir())


class RetryAfterFailedRenderTests(unittest.TestCase):
    """Pipeline retry with every engine mocked (fixtures from test_pipeline)."""
    _fixtures = pipeline_fixtures.PipelineTests
    setUp = _fixtures.setUp
    fake_probe, fake_asr, fake_translate = _fixtures.fake_probe, _fixtures.fake_asr, _fixtures.fake_translate
    fake_tts, fake_render, fake_download = _fixtures.fake_tts, _fixtures.fake_render, _fixtures.fake_download
    fake_notes, run_job, state = _fixtures.fake_notes, _fixtures.run_job, _fixtures.state

    def fail_render(self, leftover=True):
        def stopped(source, cues, clips, destination, **kwargs):
            reference = Path(destination)/'.render-work-a2n1yv45'/'mix-reference'
            reference.mkdir(parents=True)
            if leftover:
                (reference/'expected.m4a').write_bytes(b'audio held by Windows when the render stopped')
            raise ValueError('Dựng video quá thời gian chờ. Bản xuất chưa được công bố.')
        self.render.side_effect = stopped
        result = self.run_job()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('quá thời gian chờ', result['message'])
        self.render.side_effect = self.fake_render
        return Path(self.state()['render_directory'])

    def test_retry_clears_the_leftover_and_keeps_the_video_name(self):
        folder = self.fail_render()
        self.assertTrue(folder.is_dir())
        result = self.run_job()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(Path(result['outputs']['video']).parent, folder)
        self.assertFalse(any(p.name.startswith('.render-work-') for p in folder.iterdir()))
        self.assertEqual(self.translation_count, 1)

    def test_a_folder_with_other_files_is_kept_and_the_retry_uses_a_new_name(self):
        folder = self.fail_render()
        (folder/'ghi-chu-cua-toi.txt').write_text('owner note', encoding='utf-8')
        result = self.run_job()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(Path(result['outputs']['video']).parent.name, folder.name+'_lan-2')
        self.assertEqual([p.name for p in folder.iterdir()], ['ghi-chu-cua-toi.txt'])


if __name__ == '__main__':
    unittest.main()
