import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid
import subprocess
from tkinter import ttk

from teodub import voice_learning as lab
from teodub.common import hash_file, atomic_json
from teodub.voice_learning_ui import VoiceLearningTab
from tests import test_ui
from tests import test_voice_learning

REAL_POPEN = subprocess.Popen


class VoiceLearningUITests(unittest.TestCase):
    def setUp(self):
        self.f = test_ui.StudioTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.studio = self.f.studio
        self.notebook = ttk.Notebook(self.f.root)
        self.notebook.add(ttk.Frame(self.notebook), text='Video đơn')
        self.tab = VoiceLearningTab(self.notebook, self.studio)
        self.notebook.add(self.tab, text='Học giọng & phong cách')
        self.addCleanup(self.tab.shutdown)
        self.sample_fixture = test_voice_learning.VoiceLearningTests()
        self.sample_fixture.setUp()
        self.addCleanup(self.sample_fixture.doCleanups)
        # Share a disposable data root, never user's library or running process.
        self.sample_fixture.root = self.studio.data_dir
        self.sample_fixture.config['data_dir'] = str(self.studio.data_dir)

    def install(self, report=None):
        report = report or self.analyze_sample()
        self.tab.show_analysis(report)
        self.tab.name.set('Giọng thử')
        self.tab.style.set('Hoạt ngôn')
        self.tab.save()
        return next(iter(self.tab.profiles.values()))

    def analyze_sample(self):
        with patch('teodub.voice_learning.subprocess.Popen', REAL_POPEN):
            return self.sample_fixture.analyze()

    def make_campaign(self):
        from teodub.campaign_ui import CampaignTab
        from teodub.campaign import create_campaign, read_json
        tab = CampaignTab(self.notebook, self.studio)
        def cleanup():
            tab.after_cancel(tab.timer)
            tab.destroy()
        self.addCleanup(cleanup)
        directory = create_campaign(self.studio.data_dir, 'Chiến dịch thử', [
            dict(url='https://www.douyin.com/video/111', title='Video một'),
            dict(url='https://www.douyin.com/video/222', title='Video hai')], tab.settings,
            str(self.f.path/'gốc'), str(self.f.path/'thành phẩm'))
        tab.directory = directory
        tab.display(read_json(directory/'campaign.json'))
        self.tab.campaign_tab = tab
        self.tab.refresh_buttons()
        return tab

    def test_empty_tab_never_spawns_or_changes_single_job(self):
        self.assertFalse(self.tab.busy)
        self.assertEqual(self.tab.library_tree.get_children(), ())
        self.f.spawn.assert_not_called()
        self.assertIsNone(self.studio.current)

    def test_save_select_restore_and_campaign_use_same_frozen_sample(self):
        from teodub.campaign_ui import CampaignTab
        p = self.install()
        # Owner 05/11/2026: saving only learns the voice; using it is a separate choice.
        self.assertNotEqual(self.studio.voice_selection().get('reference_audio'), p['reference_audio'])
        self.assertIn('Chưa áp dụng cho nơi nào', self.tab.status.get())
        self.tab.apply_profile(p['id'], single=True)
        selection = self.studio.voice_selection()
        self.assertEqual(selection['reference_audio'], p['reference_audio'])
        self.assertEqual(selection['reference_sha256'], p['reference_sha256'])
        self.assertIn('Giọng thử', self.studio.fields['voice'].get())
        self.assertEqual(self.notebook.select(), self.notebook.tabs()[0])
        self.assertIn('Đã áp dụng cho: Video đơn', self.studio.status.get())
        self.studio.restore()
        self.assertEqual(self.studio.voice_selection()['reference_audio'], p['reference_audio'])
        tab = CampaignTab(self.notebook, self.studio)
        try:
            self.assertTrue(tab.capture_settings())
            self.assertEqual(tab.settings['reference_sha256'], selection['reference_sha256'])
            self.assertEqual(tab.settings['reference_audio'], selection['reference_audio'])
            self.assertIn('Giọng thử', tab.creation_summary.get())
        finally:
            tab.after_cancel(tab.timer)
            tab.destroy()
        self.f.spawn.assert_not_called()

    def test_tampered_saved_audio_blocks_synthesis_does_not_pick_preset(self):
        from tests.test_voice_profiles import tone
        p = self.install()
        self.tab.apply_profile(p['id'], single=True)
        chosen = self.studio.fields['voice'].get()
        tone(p['reference_audio'], seconds=5, rate=48000)
        self.studio.start('preview')
        self.f.spawn.assert_not_called()
        self.assertEqual(self.studio.fields['voice'].get(), chosen)
        self.assertIn('Chưa chạy', self.studio.status.get())

    def test_analysis_worker_does_not_replace_main_job_or_launch_twice(self):
        self.tab.files = [str(self.sample_fixture.source)]
        proc = Mock()
        proc.poll.return_value = None
        with patch('teodub.voice_learning_ui.subprocess.Popen', return_value=proc) as spawn:
            self.tab.analyze()
            folder = self.tab.folder
            self.tab.analyze()
            spawn.assert_called_once()
            self.assertIsNone(self.studio.current)
            self.assertIsNone(self.studio.process)
            self.assertTrue(self.studio.voice_learning_busy)
            self.assertEqual(str(self.studio.preview_button['state']), 'disabled')
            self.tab.stop()
            self.assertTrue((folder/'stop.request').is_file())
        self.tab.process = None

    def test_busy_guard_saved_profile_and_preview_binding(self):
        p = self.install()
        self.studio.fields['voice'].set('Mai Anh')
        self.studio.process = self.f.worker
        self.tab.apply()
        self.assertEqual(self.studio.fields['voice'].get(), 'Mai Anh')
        self.studio.process = None
        self.tab.preview = dict(path=p['reference_audio'], sha256=p['reference_sha256'])
        self.tab.preview_binding = (('profile', p['id']), p['reference_sha256'], 'khác lời', 'v3turbo')
        with patch('teodub.voice_learning_ui.winsound.PlaySound') as play:
            self.tab.replay()
            play.assert_not_called()
        self.assertIn('đã đổi', self.tab.status.get())

    def test_auto_save_uses_selected_tts_in_real_video_config_preserves_other_choices(self):
        self.studio.fields['asr_model'].set('medium')
        self.studio.fields['translation_model'].set('gpt-5.6-terra')
        self.studio.fields['subtitle_size'].set(10)
        self.studio.fields['original_volume'].set('25')
        self.tab.tts_model.set('v3nano')
        p = self.install()
        self.tab.apply_profile(p['id'], single=True)
        self.studio.restore()
        self.assertEqual(self.studio.fields['tts_model'].get(), 'v3nano')
        self.studio.input.insert('1.0', 'https://www.douyin.com/video/111')
        self.studio.start('full')
        config = json.loads((Path(self.studio.current['directory'])/'config.json').read_text(encoding='utf-8'))
        self.assertEqual(config['reference_audio'], p['reference_audio'])
        self.assertEqual(config['reference_sha256'], p['reference_sha256'])
        self.assertEqual(config['tts_model'], 'v3nano')
        self.assertEqual(config['reading_style'], 'legacy')
        self.assertEqual(config['voice_speed'], 1.0)
        self.assertEqual(config['asr_model'], 'medium')
        self.assertEqual(config['translation_model'], 'gpt-5.6-terra')
        self.assertEqual(config['subtitle_size'], 10)
        self.assertEqual(config['original_gain'], .25)
        self.f.spawn.assert_called_once()  # Only the explicit video start.

    def test_saving_changes_nothing_anywhere(self):
        campaign = self.make_campaign()
        files = [campaign.directory/'campaign.json', *campaign.directory.glob('jobs/*/config.json')]
        before = {p: p.read_bytes() for p in files}
        voice = self.studio.voice_selection()
        self.install()
        self.assertEqual({p: p.read_bytes() for p in files}, before)
        self.assertFalse((campaign.directory/'next-settings.json').exists())
        self.assertEqual(self.studio.voice_selection(), voice)
        self.assertNotIn('Giọng thử', campaign.creation_summary.get())
        self.f.spawn.assert_not_called()

    def test_campaign_opt_in_during_run_changes_only_future_voice_and_engine(self):
        from teodub.campaign import read_json
        from teodub.campaign_settings import save_next_settings, next_settings, prepare_next_job
        from teodub.pipeline import _job_lock
        campaign = self.make_campaign()
        pending = dict(campaign.settings, translation_model='gpt-5.6-luna', asr_model='small',
            subtitle_size=11, original_gain=.3, voice_gain=.8)
        old = save_next_settings(self.studio.data_dir, campaign.directory, pending)
        report = self.analyze_sample()
        manifest = read_json(campaign.directory/'campaign.json')
        manifest['items'][0]['status'] = 'running'
        atomic_json(campaign.directory/'campaign.json', manifest)
        frozen = {p: p.read_bytes() for p in campaign.directory.glob('jobs/*/config.json')}
        atomic_json(campaign.directory/'campaign-runtime.json', dict(future_settings=1))
        # Single-video rendering/model choices deliberately differ.
        self.studio.fields['translation_model'].set('gpt-5.6-terra')
        self.studio.fields['subtitle_size'].set(9)
        self.studio.campaign_busy = True
        self.tab.tts_model.set('v3nano')
        p = self.install(report)
        with _job_lock(campaign.directory):
            self.tab.apply_profile(p['id'], single=True, directories=[campaign.directory])
        self.studio.campaign_busy = False
        update = next_settings(campaign.directory)
        self.assertNotEqual(update['id'], old['id'])
        for key in ('translation_model', 'asr_model', 'subtitle_size', 'original_gain', 'voice_gain'):
            self.assertEqual(update['settings'][key], old['settings'][key], key)
        self.assertEqual(update['settings']['reference_sha256'], p['reference_sha256'])
        revision = prepare_next_job(campaign.directory, manifest['items'][1])
        actual = read_json(revision/'config.json')
        self.assertEqual(actual['reference_audio'], p['reference_audio'])
        self.assertEqual(actual['tts_model'], 'v3nano')
        self.assertEqual(actual['voice_speed'], 1.0)
        # Exercise the actual reference normalization/dispatch for both paths,
        # with only neural inference mocked (no download or GPU work).
        import numpy as np
        from teodub import tts
        engine = Mock()
        engine.infer.return_value = (.1*np.sin(2*np.pi*220*np.arange(48000)/48000)).astype('float32')
        with patch.object(tts, 'get_model', return_value=engine) as load:
            for label, config in [('single', self.studio.voice_selection()), ('campaign', actual)]:
                tts.synthesize([dict(start_ms=0, end_ms=2000, text='Cùng nghe giọng đã học nhé.')],
                    config['voice'], self.f.path/label, **tts.selection_options(config))
            self.assertEqual(load.call_count, 2)
            for call in load.call_args_list:
                self.assertEqual(call.kwargs['tts_model'], 'v3nano')
        self.assertEqual(engine.infer.call_count, 2)
        calls = engine.infer.call_args_list
        self.assertEqual(hash_file(calls[0].kwargs['ref_audio']), hash_file(calls[1].kwargs['ref_audio']))
        self.assertTrue(all(call.kwargs['use_ref_codes'] for call in calls))
        self.assertTrue(all('voice' not in call.kwargs for call in calls))
        self.assertEqual({p: p.read_bytes() for p in frozen}, frozen)
        self.assertIn('Giọng thử', campaign.summary.get())
        self.assertIn('từ video tiếp theo', self.studio.status.get())
        self.f.spawn.assert_not_called()

    def test_saved_profile_survives_apply_failure_and_retries_without_duplicate(self):
        p = self.install()
        with patch.object(self.studio, 'apply_learned_voice', side_effect=ValueError('Tạm bận')):
            with self.assertRaises(ValueError):
                self.tab.apply_profile(p['id'], single=True)
        self.assertEqual(len(lab.list_profiles(self.studio.data_dir)[0]), 1)
        self.tab.apply_profile(p['id'], single=True)
        self.assertEqual(self.studio.voice_selection()['reference_audio'], p['reference_audio'])
        self.assertEqual(len(lab.list_profiles(self.studio.data_dir)[0]), 1)
        self.f.spawn.assert_not_called()

    def test_apply_needs_a_place_and_changes_only_the_ticked_campaign(self):
        from teodub.campaign import create_campaign
        from teodub.campaign_settings import next_settings
        first = self.make_campaign()
        second = create_campaign(self.studio.data_dir, 'Chiến dịch khác', [
            dict(url='https://www.douyin.com/video/333', title='Video ba')], first.settings,
            str(self.f.path/'gốc'), str(self.f.path/'thành phẩm'))
        voice = self.studio.voice_selection()
        p = self.install()
        with self.assertRaises(ValueError):
            self.tab.apply_profile(p['id'])
        self.assertEqual(self.studio.voice_selection(), voice)
        self.tab.apply_profile(p['id'], directories=[second])
        self.assertEqual(next_settings(second)['settings']['reference_sha256'], p['reference_sha256'])
        self.assertIsNone(next_settings(first.directory))
        self.assertEqual(self.studio.voice_selection(), voice)

    def test_dialog_lists_unfinished_campaigns_with_nothing_ticked(self):
        from teodub.voice_apply_ui import ApplyVoiceDialog
        campaign = self.make_campaign()
        p = self.install()
        dialog = ApplyVoiceDialog(self.tab, p['id'])
        self.addCleanup(dialog.destroy)
        self.assertFalse(dialog.single.get())
        self.assertEqual([d for d, _ in dialog.choices], [campaign.directory])
        self.assertFalse(any(v.get() for _, v in dialog.choices))
        dialog.apply()
        self.assertIn('Chưa tích', dialog.status.get())
        self.assertFalse((campaign.directory/'next-settings.json').exists())
        dialog.choices[0][1].set(True)
        dialog.apply()
        self.assertTrue((campaign.directory/'next-settings.json').exists())
        self.assertFalse(dialog.winfo_exists())

    def test_one_campaign_failure_is_reported_and_others_still_apply(self):
        from teodub.campaign import create_campaign
        from teodub.campaign_settings import next_settings
        first = self.make_campaign()
        second = create_campaign(self.studio.data_dir, 'Chiến dịch khác', [
            dict(url='https://www.douyin.com/video/333', title='Video ba')], first.settings,
            str(self.f.path/'gốc'), str(self.f.path/'thành phẩm'))
        p = self.install()
        real = __import__('teodub.campaign_settings', fromlist=['x']).save_next_voice_settings
        def flaky(data_dir, directory, selection):
            if Path(directory) == first.directory:
                raise ValueError('Worker cũ')
            return real(data_dir, directory, selection)
        with patch('teodub.campaign_settings.save_next_voice_settings', flaky):
            with self.assertRaises(ValueError) as caught:
                self.tab.apply_profile(p['id'], directories=[first.directory, second])
        self.assertIn('Worker cũ', str(caught.exception))
        self.assertIsNone(next_settings(first.directory))
        self.assertEqual(next_settings(second)['settings']['reference_sha256'], p['reference_sha256'])
        self.assertEqual(len(lab.list_profiles(self.studio.data_dir)[0]), 1)

    def test_analysis_history_survives_reopen(self):
        with patch('teodub.voice_learning.subprocess.Popen', REAL_POPEN):
            report = self.sample_fixture.analyze()
        self.tab.refresh_history()
        self.assertEqual(len(self.tab.history), 1)
        self.tab.history_var.set(next(iter(self.tab.history)))
        self.tab.load_history()
        self.assertEqual(self.tab.analysis['id'], report['id'])
        self.assertTrue(self.tab.candidate_tree.get_children())

    def test_restart_waits_for_sample_worker_instead_of_losing_analysis(self):
        from teodub.restart import busy_reasons
        self.studio.voice_learning_busy = True
        facebook = SimpleNamespace(manager_windows={}, page_bridge=None, sessions={}, page_scheduler=None)
        campaign = SimpleNamespace(scanning=False, chrome_login_pending=False)
        reasons = busy_reasons(self.studio, campaign, facebook)
        self.assertTrue(any('phân tích giọng' in reason for reason in reasons))
        self.studio.voice_learning_busy = False


if __name__ == '__main__':
    unittest.main()
