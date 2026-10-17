"""Local sample storage and ADAM routing; synthetic audio, no real voice or network."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from teodub.adam_voice import (LABEL, AdamImportDialog, checked_reference, import_reference,
                              library_root, saved_reference)
from teodub.common import Cancelled, hash_file
from tests.test_voice_profiles import tone
from tests import test_ui


class AdamLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'sample.wav'
        tone(self.source)

    def test_missing_sample_has_no_files_or_fabricated_default(self):
        self.assertIsNone(saved_reference(self.root))
        self.assertFalse(library_root(self.root).exists())

    def test_import_keeps_original_and_survives_original_removal(self):
        original = self.source.read_bytes()
        result = import_reference(self.source, self.root)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(saved_reference(self.root), result)
        self.source.unlink()
        self.assertEqual(checked_reference(result, self.root), result)

    def test_new_version_does_not_change_old_job_sample(self):
        old = import_reference(self.source, self.root)
        digest = hash_file(old)
        tone(self.source, seconds=5)
        new = import_reference(self.source, self.root)
        self.assertNotEqual(old, new)
        self.assertEqual(hash_file(old), digest)
        self.assertEqual(saved_reference(self.root), new)
        self.assertEqual(checked_reference(old, self.root), old)

    def test_short_sample_or_cancel_preserves_previous_selection(self):
        old = import_reference(self.source, self.root)
        tone(self.source, seconds=1)
        with self.assertRaises(ValueError):
            import_reference(self.source, self.root)
        self.assertEqual(saved_reference(self.root), old)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            import_reference(self.source, self.root, cancel=cancel)
        self.assertEqual(saved_reference(self.root), old)

    def test_tampering_and_outside_path_are_rejected(self):
        path = import_reference(self.source, self.root)
        tone(path, seconds=5, rate=48000)
        with self.assertRaises(ValueError):
            saved_reference(self.root)
        with self.assertRaises(ValueError):
            checked_reference(self.source, self.root)


class AdamUITests(unittest.TestCase):
    def setUp(self):
        self.f = test_ui.StudioTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.studio = self.f.studio
        self.source = self.f.path/'sample.wav'
        tone(self.source)

    def install(self):
        sample = import_reference(self.source, self.studio.data_dir)
        self.studio.apply_adam(sample)
        return sample

    def test_missing_sample_can_be_selected_but_cannot_start_synthesis(self):
        self.assertIn(LABEL, tuple(self.studio.voice_picker['values']))
        self.studio.fields['voice'].set(LABEL)
        self.assertIn('Chưa có mẫu', self.studio.voice_help.get())
        self.studio.start('preview')
        self.f.spawn.assert_not_called()
        self.assertIn('Nhập mẫu ADAM', self.studio.status.get())

    def test_preview_and_full_freeze_real_local_sample_and_keep_controls(self):
        sample = self.install()
        self.studio.input.insert('1.0', 'example.mp4')
        self.studio.fields['voice_speed'].set('1,10× — nhanh hơn')
        for model in ('v3turbo', 'v3nano'):
            self.studio.fields['tts_model'].set(model)
            for mode in ('full', 'preview'):
                config = self.studio.config_from_form(mode)
                self.assertEqual(config['voice_provider'], 'local')
                self.assertEqual(config['voice'], 'Giọng từ mẫu')
                self.assertEqual(config['reference_audio'], str(sample))
                self.assertEqual(config['reference_sha256'], hash_file(sample))
                self.assertEqual(config['tts_model'], model)
                self.assertEqual(config['voice_speed'], 1.1)
        self.f.spawn.assert_not_called()

    def test_replacing_then_restoring_old_config_keeps_old_voice_version(self):
        old = self.install()
        config = self.studio.config_from_form('preview')
        tone(self.source, seconds=5)
        new = self.install()
        self.assertNotEqual(old, new)
        self.studio.fill_profile(config)
        self.assertEqual(self.studio.fields['voice'].get(), LABEL)
        self.assertEqual(self.studio.voice_selection()['reference_audio'], str(old))
        self.assertEqual(saved_reference(self.studio.data_dir), new)

    def test_preferences_restore_adam_without_service_or_worker(self):
        path = self.install()
        self.studio.fields['voice'].set('Mai Anh')
        self.studio.restore()
        self.assertEqual(self.studio.fields['voice'].get(), LABEL)
        self.assertEqual(self.studio.voice_selection()['reference_audio'], str(path))
        self.f.spawn.assert_not_called()

    def test_tampered_sample_restores_identity_but_blocks_new_generation(self):
        path = self.install()
        config = self.studio.config_from_form('preview')
        tone(path, seconds=5, rate=48000)
        self.studio.fill_profile(config)
        self.assertEqual(self.studio.fields['voice'].get(), LABEL)
        self.studio.start('preview')
        self.f.spawn.assert_not_called()
        self.assertIn('Chưa chạy', self.studio.status.get())

    def test_imported_sample_reaches_local_infer_and_cached_preview(self):
        from teodub import tts
        from tests.test_speech import tone as output_tone
        self.install()
        self.studio.fields['reading_style'].set('legacy')
        config = self.studio.config_from_form('preview')
        model = Mock()
        model.infer.return_value = output_tone(1)
        with patch.object(tts, 'get_model', return_value=model) as load:
            audio = tts.preview_voice(config['voice'], self.f.path/'preview', **tts.selection_options(config))
            self.assertTrue(Path(audio).is_file())
            reference = Path(model.infer.call_args.kwargs['ref_audio'])
            metadata = json.loads(reference.with_suffix('.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['source_sha256'], config['reference_sha256'])
            self.assertTrue(model.infer.call_args.kwargs['use_ref_codes'])
            self.assertNotIn('voice', model.infer.call_args.kwargs)
            tts.preview_voice(config['voice'], self.f.path/'preview', **tts.selection_options(config))
            model.infer.assert_called_once()

    def test_campaign_snapshot_uses_adam_sample_and_hash(self):
        from teodub.campaign_ui import CampaignTab
        sample = self.install()
        tab = CampaignTab(self.f.root, self.studio)
        try:
            tab.capture_settings()
            self.assertEqual(tab.settings['voice_provider'], 'local')
            self.assertEqual(tab.settings['voice'], 'Giọng từ mẫu')
            self.assertEqual(tab.settings['reference_audio'], str(sample))
            self.assertEqual(tab.settings['reference_sha256'], hash_file(sample))
            self.assertIn('ADAM', tab.summary.get())
        finally:
            tab.after_cancel(tab.timer)
            tab.destroy()

    def test_busy_prevents_import_or_choice_mutating_active_selection(self):
        path = self.install()
        self.studio.campaign_busy = True
        self.studio.refresh_buttons()
        self.assertEqual(str(self.studio.adam_button['state']), 'disabled')
        with patch('teodub.adam_voice.AdamImportDialog') as dialog:
            self.studio.choose_adam()
            dialog.assert_not_called()
        with self.assertRaises(ValueError):
            self.studio.apply_adam(path)

    def test_import_dialog_reports_failure_and_can_retry(self):
        callback = Mock()
        dialog = AdamImportDialog(self.f.root, self.studio.data_dir, callback)
        dialog.start()
        self.assertIn('Chọn tệp', dialog.status.get())
        dialog.results.put((None, 'Mẫu giọng quá ngắn'))
        dialog.busy = True
        dialog.poll()
        self.assertFalse(dialog.busy)
        self.assertIn('quá ngắn', dialog.status.get())
        callback.assert_not_called()
        dialog.close()
        self.assertTrue(dialog.cancel.is_set())

    def test_import_dialog_accepts_background_result_and_selects_sample(self):
        sample = import_reference(self.source, self.studio.data_dir)
        dialog = AdamImportDialog(self.f.root, self.studio.data_dir, self.studio.apply_adam)
        dialog.results.put((sample, None))
        dialog.poll()
        self.assertEqual(self.studio.fields['voice'].get(), LABEL)
        self.assertFalse(dialog.winfo_exists())
        self.f.spawn.assert_not_called()
