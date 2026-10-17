"""Actual frame/FFmpeg tests; fake AI only except separately recorded live QA."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from teodub import video_content as content
from teodub.common import Cancelled, atomic_json, hash_file
from teodub.voice_learning import library_root


def make_video(path, duration=18, audio=False):
    import imageio_ffmpeg
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error',
               '-f', 'lavfi', '-i', f'testsrc2=s=320x240:r=10:d={duration}']
    if audio:
        command += ['-f', 'lavfi', '-i', f'sine=frequency=400:duration={duration}', '-c:a', 'aac']
    command += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-shortest', str(path)]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def answer_for(evidence, seconds=15):
    return dict(videos=[dict(source_id=v['id'], summary='Cảnh minh họa', hook='Mở đầu trực quan',
        story_structure='Chỉ nhận một đoạn', visual_style='Các khối màu', editing_style='Chưa xác định cắt cảnh',
        speech_style='Chưa nhận lời', caption_style='Chưa rõ chữ',
        observations=[dict(frame_id=v['frames'][0]['id'], description='Thấy khối màu.', confidence='quan sát')],
        lessons=['Dùng màu tương phản'], limitations=['Ảnh lấy mẫu']) for v in evidence],
        common_patterns=['Mở đầu trực quan'], new_video=dict(title='Ý tưởng mới', concept='Minh họa một mẹo nhỏ',
        duration_seconds=seconds, scenes=[dict(duration_seconds=d, narration='Cùng xem một cách làm mới.',
        visual='Quay tay thao tác.', on_screen_text='Mẹo nhỏ', editing='Cắt sang cảnh gần.', asset_needed='Video tự quay')
        for d in (5, 5, seconds-10)], production_notes=['Chuẩn bị cảnh của bạn']))


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root/'source.mp4'
        make_video(self.source)
        self.config = dict(id=uuid.uuid4().hex, data_dir=str(self.root), operation='content',
            sources=[str(self.source)], start_s=2, limit_s=8, topic='Mẹo đơn giản, hình của tôi.',
            target_seconds=15, asr_model='small', model='gpt-6.1-sol', effort='medium')
        self.calls = []

    def invoke(self, prompt, schema, **kwargs):
        folder = library_root(self.root)/'content'/self.config['id']
        evidence = json.loads((folder/'evidence.json').read_text(encoding='utf-8'))['videos']
        self.calls.append((prompt, kwargs))
        return answer_for(evidence), dict(image_count=len(kwargs['images']), usage=None)

    def analyze(self):
        return content.analyze_content(self.config, invoke=self.invoke)

    def test_real_sampled_frames_silent_video_and_original_script_survive_restart(self):
        before = hash_file(self.source)
        with patch.object(content, '_recognizer') as asr:
            report = self.analyze()
        asr.assert_not_called()
        ref = report['evidence'][0]
        self.assertEqual(len(ref['frames']), 12)
        self.assertTrue(all(2 <= f['time_s'] < 10 for f in ref['frames']))
        self.assertEqual(hash_file(self.source), before)
        prompt, kwargs = self.calls[0]
        self.assertNotIn(str(self.root), prompt)
        self.assertIn('DỮ LIỆU', prompt)
        self.assertEqual(len(kwargs['images']), 12)
        self.assertEqual(content.load_report(self.root, report['id']), report)
        text = content.report_text(report)
        self.assertIn('2.0–10.0', text)
        self.assertIn('Cảnh 3', text)
        self.assertFalse(report['weights_trained'])
        with self.assertRaisesRegex(ValueError, 'đã xử lý'):
            self.analyze()
        self.assertEqual(len(self.calls), 1)

    def test_audio_gpu_interface_receives_same_range_and_failed_gpu_never_sends_ai(self):
        video = self.root/'with-audio.mp4'
        make_video(video, audio=True)
        self.config['sources'] = [str(video)]
        segment = SimpleNamespace(start=.1, end=2.5, text='Xin chào các bạn.')
        model = Mock()
        model.transcribe.return_value = (iter([segment]), SimpleNamespace(language='vi'))
        with patch.object(content, '_recognizer', return_value=(model, 'cuda')):
            result = self.analyze()
        self.assertEqual(result['device'], 'cuda')
        self.assertEqual(result['evidence'][0]['segments'][0]['start_s'], 2.1)
        self.config['id'] = uuid.uuid4().hex
        with patch.object(content, '_recognizer', side_effect=ValueError('GPU hết bộ nhớ')):
            with self.assertRaisesRegex(ValueError, 'GPU'):
                self.analyze()
        self.assertEqual(len(self.calls), 1)

    def test_wrong_evidence_or_timing_never_publishes_report_or_srt(self):
        for defect in ('id', 'frame', 'timing'):
            self.config['id'] = uuid.uuid4().hex
            def invoke(prompt, schema, **kwargs):
                answer, metadata = self.invoke(prompt, schema, **kwargs)
                if defect == 'id':
                    answer['videos'][0]['source_id'] = 'V2'
                elif defect == 'frame':
                    answer['videos'][0]['observations'][0]['frame_id'] = 'V9-F01'
                else:
                    answer['new_video']['scenes'][0]['duration_seconds'] = 6
                return answer, metadata
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                content.analyze_content(self.config, invoke=invoke)
            folder = library_root(self.root)/'content'/self.config['id']
            self.assertTrue((folder/'answer.json').is_file())
            self.assertFalse((folder/'content.json').exists())
            self.assertFalse((folder/'loi-doc-moi.srt').exists())

    def test_cancel_before_and_after_ai_preserves_no_success_and_no_replay(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(Cancelled):
            content.analyze_content(self.config, cancel=event, invoke=self.invoke)
        self.assertEqual(self.calls, [])
        event.clear()
        def invoke(*args, **kwargs):
            answer = self.invoke(*args, **kwargs)
            event.set()
            return answer
        with self.assertRaises(Cancelled):
            content.analyze_content(self.config, cancel=event, invoke=invoke)
        folder = library_root(self.root)/'content'/self.config['id']
        self.assertTrue((folder/'submitted.json').is_file())
        self.assertFalse((folder/'content.json').exists())
        event.clear()
        with self.assertRaises(ValueError):
            content.analyze_content(self.config, cancel=event, invoke=self.invoke)

    def test_new_script_binds_only_chosen_footage_and_tampered_srt_rejected(self):
        from teodub.dialogue_editor import draft_binding
        report = self.analyze()
        result = content.bind_script_to_video(self.root, report['id'], self.source)
        self.assertEqual(draft_binding(result['subtitles'], self.root)['dialogue_source_sha256'], hash_file(self.source))
        self.assertNotEqual(result['subtitles'], report['srt']['path'])
        short = self.root/'short.mp4'
        make_video(short, duration=3)
        with self.assertRaisesRegex(ValueError, 'ít nhất'):
            content.bind_script_to_video(self.root, report['id'], short)
        Path(report['srt']['path']).write_text('tampered', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'thay đổi'):
            content.load_report(self.root, report['id'])

    def test_public_link_provenance_and_no_implicit_profile_or_publication(self):
        url = 'https://www.youtube.com/watch?v=jNQXAC9IVRw'
        self.config['sources'] = [url]
        with patch.object(content, '_source', return_value=(self.source, {'source_url':url, 'source_title':'Nguồn công khai'})):
            report = self.analyze()
        self.assertEqual(report['evidence'][0]['source_url'], url)
        self.assertFalse((library_root(self.root)/'profiles').exists())
        self.assertFalse((self.root/'page-publishing').exists())

    def test_real_worker_dispatch_and_durable_result_are_not_replayed(self):
        from teodub.voice_learning import worker_main
        folder = library_root(self.root)/'content'/self.config['id']
        atomic_json(folder/'config.json', self.config)
        with patch('teodub.common.protect_worker'), patch('teodub.codex_translation._invoke_codex', side_effect=self.invoke) as invoke:
            self.assertEqual(worker_main(['--config', str(folder/'config.json')]), 0)
            self.assertEqual(worker_main(['--config', str(folder/'config.json')]), 0)
            invoke.assert_called_once()
        outcome = json.loads((folder/'result.json').read_text(encoding='utf-8'))
        self.assertEqual(outcome['status'], 'completed')
        self.assertEqual(outcome['result']['id'], self.config['id'])


class ContentUITests(unittest.TestCase):
    def setUp(self):
        from tests import test_voice_learning_ui
        self.f = test_voice_learning_ui.VoiceLearningUITests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.tab = self.f.tab
        self.panel = self.tab.content_panel

    def test_open_is_passive_and_content_hides_voice_footer(self):
        self.tab.notebook.select(self.panel)
        self.tab.selection_changed()
        self.assertIsNone(self.tab.active_target())
        self.assertFalse(self.tab.preview_frame.winfo_manager())
        self.f.f.spawn.assert_not_called()
        self.tab.notebook.select(self.tab.samples_tab)
        self.tab.selection_changed()
        self.assertEqual(self.tab.preview_frame.winfo_manager(), 'grid')

    def test_paste_and_content_launch_is_explicit_single_frozen_job(self):
        self.tab.link.set('https://youtu.be/jNQXAC9IVRw')
        self.panel.topic.delete('1.0', 'end')
        self.panel.topic.insert('1.0', 'Mẹo câu cá của tôi')
        proc = Mock()
        proc.poll.return_value = None
        with patch('teodub.voice_learning_ui.subprocess.Popen', return_value=proc) as spawn:
            self.panel.analyze()
            self.panel.analyze()
            spawn.assert_called_once()
            config = json.loads((self.tab.folder/'config.json').read_text(encoding='utf-8'))
            self.assertEqual(config['operation'], 'content')
            self.assertEqual(config['sources'], ['https://www.youtube.com/watch?v=jNQXAC9IVRw'])
            self.assertEqual(config['topic'], 'Mẹo câu cá của tôi')
            self.assertEqual(self.tab.folder.parent.name, 'content')
            self.assertIsNone(self.f.studio.current)
            self.tab.stop()
            self.assertTrue((self.tab.folder/'stop.request').exists())
        self.tab.process = None

    def test_invalid_or_excess_sources_never_start_worker(self):
        self.tab.files = ['a.mp4']*4
        self.panel.analyze()
        self.f.f.spawn.assert_not_called()
        self.assertIn('1–3 video', self.tab.status.get())
        self.tab.files = ['a.mp4']
        self.panel.duration.set('nan')
        self.panel.analyze()
        self.f.f.spawn.assert_not_called()

    def test_saved_result_can_apply_to_single_video_without_launch_or_old_source(self):
        from teodub.dialogue_editor import draft_binding
        from tests.test_voice_learning_ui import REAL_POPEN
        f = ContentTests()
        with patch('subprocess.Popen', REAL_POPEN):
            f.setUp()
        self.addCleanup(f.doCleanups)
        f.config['data_dir'] = str(self.f.studio.data_dir)
        # Generate the fixture using this lab's data root, keeping its own media.
        def invoke(prompt, schema, **kwargs):
            p = library_root(self.f.studio.data_dir)/'content'/f.config['id']/'evidence.json'
            return answer_for(json.loads(p.read_text(encoding='utf-8'))['videos']), {}
        with patch('subprocess.Popen', REAL_POPEN):
            report = content.analyze_content(f.config, invoke=invoke)
        self.panel.show(report)
        self.assertTrue(self.panel.history)
        old_current = self.f.studio.current
        with patch('teodub.video_content_ui.filedialog.askopenfilename', return_value=str(f.source)):
            self.panel.apply()
        self.assertEqual(self.f.studio.input.get('1.0', 'end').strip(), str(f.source))
        selected = self.f.studio.fields['subtitles'].get()
        self.assertEqual(draft_binding(selected, self.f.studio.data_dir)['dialogue_source_sha256'], hash_file(f.source))
        self.assertIs(self.f.studio.current, old_current)
        self.f.f.spawn.assert_not_called()


class CodexImageTests(unittest.TestCase):
    def test_attachments_are_explicit_copied_and_tools_remain_disabled(self):
        from teodub import codex_translation as codex
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'some, image.png'
            Image.new('RGB', (32, 32), 'red').save(source)
            calls = []
            def run(command, directory, input_path, env, **kwargs):
                calls.append(command)
                if 'login' in command:
                    return 0, 'Logged in using ChatGPT', ''
                attached = Path(command[command.index('--image')+1])
                self.assertTrue(attached.is_file())
                self.assertNotEqual(attached, source)
                self.assertNotIn(',', str(attached))
                self.assertEqual(command[-1], '-')
                for feature in ('shell_tool', 'multi_agent', 'view_image', 'browser_use'):
                    self.assertIn(feature, command)
                Path(command[command.index('--output-last-message')+1]).write_text('{"ok":true}', encoding='utf-8')
                return 0, '{"type":"turn.completed","usage":{"input_tokens":12}}', ''
            with patch.object(codex, 'find_codex', return_value='codex.exe'), patch.object(codex, '_run_process', side_effect=run):
                result, meta = codex._invoke_codex('inspect', dict(type='object'), images=[source])
            self.assertTrue(result['ok'])
            self.assertEqual(meta['image_count'], 1)
            self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
