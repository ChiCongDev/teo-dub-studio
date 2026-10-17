"""Link-to-local analysis, provenance and existing TTS application contracts."""
import json
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch
import uuid

from teodub import voice_learning as lab
from teodub.common import Cancelled, hash_file
from tests.test_voice_learning import VoiceLearningTests
from tests.test_voice_learning_ui import VoiceLearningUITests

URL = 'https://www.douyin.com/video/7542118158246645033'


class LinkAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.fixture = VoiceLearningTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_share_link_normalization_duplicate_and_unsupported_sources(self):
        self.assertEqual(lab.video_links(f'video [nguồn]({URL}) {URL}'), [URL])
        self.assertEqual(lab.video_links('https://www.douyin.com/user/name?modal_id=7542118158246645033'), [URL])
        for value in ('', 'https://example.com/video.mp4', 'https://www.douyin.com/user/name',
                      'https://www.douyin.com:1234/video/123', URL+' https://127.0.0.1/video'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lab.video_links(value)

    def test_download_to_full_analysis_and_saved_reference_retains_origin(self):
        import imageio_ffmpeg
        f = self.fixture
        video = f.root/'linked-video.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error',
            '-f', 'lavfi', '-i', 'color=c=blue:s=160x120:r=10:d=12', '-i', str(f.source),
            '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(video)], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        original = hash_file(video)
        metadata = dict(sha256=original, width=160, height=120, duration=12)
        cancel = threading.Event()
        with patch('teodub.download.download_video', return_value=dict(source_path=str(video), metadata=metadata)) as download:
            with patch.object(lab, '_recognizer', return_value=(f.model, 'cuda')):
                report = lab.analyze_sources({**f.config, 'sources':[URL]}, cancel=cancel)
        args, kwargs = download.call_args
        analysis = lab.library_root(f.root)/'analyses'/report['id']
        self.assertEqual(args, (URL, str(analysis/'videos'), str(analysis/'link-01')))
        self.assertIs(kwargs['cancel'], cancel)
        item = report['videos'][0]
        self.assertEqual(item['source_url'], URL)
        self.assertEqual(item['media']['orientation'], 'Ngang')
        self.assertEqual((item['media']['width'], item['media']['height']), (160,120))
        self.assertAlmostEqual(item['segments'][0]['start_s'], 2.1)
        self.assertIn('lời nói tiếng Việt', item['segments'][0]['text'])
        self.assertIn(URL, (analysis/'bao-cao.txt').read_text(encoding='utf-8'))
        self.assertEqual(json.loads((analysis/'analysis.json').read_text(encoding='utf-8'))['videos'], report['videos'])
        profile = lab.save_profile(f.root, report['id'], report['candidates'][0]['id'], 'Từ link')
        self.assertEqual(profile['candidate']['source_url'], URL)
        video.unlink()
        selected = lab.profile_selection(f.root, profile['id'])
        self.assertEqual(hash_file(selected['reference_audio']), profile['reference_sha256'])

    def test_provider_failure_preserves_other_source_without_second_model(self):
        f = self.fixture
        with patch('teodub.download.download_video', side_effect=RuntimeError('429 nguồn đang giới hạn')):
            report = f.analyze(sources=[URL,str(f.source)])
        self.assertEqual(len(report['videos']), 1)
        self.assertEqual(report['errors'][0]['source'], URL)
        self.assertIn('429', report['errors'][0]['message'])
        self.assertTrue(report['candidates'])

    def test_all_downloads_failed_never_loads_model_or_creates_profile(self):
        f = self.fixture
        with patch('teodub.download.download_video', side_effect=RuntimeError('Không tải được')):
            with patch.object(lab, '_recognizer') as recognizer:
                with self.assertRaisesRegex(ValueError, 'Chưa tải'):
                    lab.analyze_sources({**f.config,'sources':[URL]})
        recognizer.assert_not_called()
        self.assertEqual(lab.list_profiles(f.root)[0], [])

    def test_cancelled_download_does_not_continue_or_publish_analysis(self):
        f = self.fixture
        with patch('teodub.download.download_video', side_effect=Cancelled('Dừng tải')):
            with patch.object(lab, '_recognizer') as recognizer:
                with self.assertRaises(Cancelled):
                    lab.analyze_sources({**f.config,'sources':[URL]})
        recognizer.assert_not_called()
        self.assertFalse((lab.library_root(f.root)/'analyses'/f.config['id']/'analysis.json').exists())

    def test_legacy_history_readable_without_new_video_report(self):
        self.assertIn('Lượt cũ', lab.analysis_text(dict(model='small',device='cuda',candidates=[])))


class LinkAnalysisUITests(unittest.TestCase):
    def setUp(self):
        self.fixture = VoiceLearningUITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.tab = self.fixture.tab

    def test_paste_and_analyze_launches_once_and_keeps_main_job(self):
        self.tab.link.set('Video mẫu '+URL)
        process = Mock()
        process.poll.return_value = None
        with patch('teodub.voice_learning_ui.subprocess.Popen', return_value=process) as spawn:
            self.tab.analyze()
            config = json.loads((self.tab.folder/'config.json').read_text(encoding='utf-8'))
            self.assertEqual(config['sources'], [URL])
            self.assertEqual(self.tab.link.get(), '')
            self.tab.analyze()
            spawn.assert_called_once()
            self.assertIsNone(self.fixture.studio.current)
            self.assertIsNone(self.fixture.studio.process)
            self.tab.stop()
            self.assertTrue((self.tab.folder/'stop.request').is_file())
        self.tab.process = None

    def test_invalid_mixed_links_keep_all_existing_sources_and_input(self):
        self.tab.files = ['existing.wav']
        value = URL+' https://example.com/private'
        self.tab.link.set(value)
        self.tab.add_links()
        self.assertEqual(self.tab.files, ['existing.wav'])
        self.assertEqual(self.tab.link.get(), value)
        self.fixture.f.spawn.assert_not_called()

    def test_duplicate_and_limit_do_not_overwrite_source_selection(self):
        self.tab.link.set(URL+' '+URL)
        self.tab.add_links()
        self.tab.link.set(URL)
        self.tab.add_links()
        self.assertEqual(self.tab.files, [URL])
        self.tab.files = [f'{n}.wav' for n in range(10)]
        self.tab.link.set(URL)
        self.tab.add_links()
        self.assertEqual(len(self.tab.files), 10)
        self.assertEqual(self.tab.link.get(), URL)
        self.assertIn('Tối đa', self.tab.status.get())

    def test_saved_report_copy_history_and_existing_auto_apply(self):
        report = self.fixture.analyze_sample()
        self.tab.show_analysis(report)
        self.assertIn('LỜI THOẠI', self.tab.report_text.get('1.0','end'))
        self.tab.notebook.select(self.tab.report_tab)
        self.assertIsNone(self.tab.active_target())
        self.tab.copy_report()
        self.assertIn('BÁO CÁO PHÂN TÍCH', self.tab.clipboard_get())
        self.tab.notebook.select(self.tab.samples_tab)
        self.tab.name.set('Học từ video')
        self.tab.save()
        self.assertIn('Học từ video', self.fixture.studio.fields['voice'].get())
        self.assertEqual(self.fixture.notebook.select(), self.fixture.notebook.tabs()[0])


if __name__ == '__main__':
    unittest.main()
