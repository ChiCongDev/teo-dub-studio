"""Hidden Tk tests: fake workers only, no downloads, Codex, TTS or native control."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
import wave
import winsound
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from teodub.app import CAPCUT_VOICES, MAX_LOG_LINES, READING_STYLES, REFERENCE_VOICE, SPEEDS, VOICES, VOICE_SPEED_LABELS, Studio, worker_command
from teodub.common import atomic_json, hash_file
from teodub.speech_plan import STYLES
from teodub.subtitle_style import SUBTITLE_BOXES, SUBTITLE_COLORS, SUBTITLE_WEIGHTS
from teodub.wording import validate_wording


class Worker:
    def __init__(self):
        self.code = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.code = 2


@unittest.skipUnless(sys.platform == 'win32', 'Windows Tk application')
class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sound = self.enterContext(patch('teodub.app.winsound.PlaySound'))
        self.path = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.studio = Studio(self.root, self.path/'data')
        self.addCleanup(self.cleanup)
        self.studio.fields['original_dir'].set(str(self.path/'gốc'))
        self.studio.fields['output_dir'].set(str(self.path/'lồng tiếng'))
        self.worker = Worker()
        self.command = self.enterContext(patch('teodub.app.worker_command', return_value=['fixture-worker']))
        self.spawn = self.enterContext(patch('teodub.app.subprocess.Popen', return_value=self.worker))
        self.open = self.enterContext(patch('teodub.app.os.startfile'))

    def cleanup(self):
        if not self.studio.closed:
            self.studio.process = None
            self.studio.finish_close()

    def start(self, mode='full', *, require_subtitles=False):
        if mode == 'full':
            self.studio.input.insert('1.0', 'Chia sẻ https://v.douyin.com/Example123/ lời giới thiệu')
        self.studio.start(mode, require_subtitles=require_subtitles)
        self.spawn.assert_called_once()
        return self.studio.current

    def test_models_apply_to_new_config_and_are_disabled_during_work(self):
        chosen = dict(asr_model='small', translation_model='gpt-6.1-sol', translation_effort='medium', tts_model='v3nano')
        self.studio.apply_model_selection(chosen)
        self.studio.fields['voice'].set('Minh Quân')
        config = self.start()
        for key,value in chosen.items(): self.assertEqual(config[key],value)
        self.assertEqual(str(self.studio.models_button['state']), 'disabled')
        self.studio.apply_model_selection(dict(chosen,tts_model='v3turbo'))
        self.assertEqual(self.studio.fields['tts_model'].get(),'v3nano')

    def test_wording_is_optional_and_full_job_freezes_the_table_for_supplied_srt(self):
        self.assertFalse(self.studio.fields['wording_enabled'].get())
        self.assertEqual(self.studio.wording_summary.get(), 'Từ ngữ: Tắt')
        chosen = self.studio.apply_wording_selection(True, 'bán => báng\ngiá => giá tham khảo')
        supplied = self.path/'lời Việt.srt'
        supplied.write_text('1\n00:00:00,000 --> 00:00:01,000\nNơi này bán hàng.\n', encoding='utf-8')
        self.studio.fields['subtitles'].set(str(supplied))
        config = self.start(require_subtitles=True)
        self.assertEqual(config['wording'], chosen)
        self.assertEqual(config['subtitles'], str(supplied.resolve()))
        self.assertEqual(str(self.studio.wording_button['state']), 'disabled')
        self.assertIsNone(self.studio.apply_wording_selection(False, 'mua => lấy'))
        self.assertEqual(self.studio.wording_selection(), chosen)
        self.assertEqual(json.loads((Path(config['directory'])/'config.json').read_text(encoding='utf-8'))['wording'], chosen)

    def test_disabled_wording_preserves_the_editable_table_and_old_config_stays_unchanged(self):
        chosen = self.studio.apply_wording_selection(False, 'bán => báng\nmua => lấy')
        other = Studio(self.root, self.studio.data_dir)
        try:
            self.assertEqual(other.wording_selection(), chosen)
            self.assertFalse(other.fields['wording_enabled'].get())
            legacy = dict(mode='full', voice='Mai Anh')
            other.fill_form(legacy)
            self.assertEqual(other.wording_selection(), validate_wording({}))
            self.assertEqual(legacy, dict(mode='full', voice='Mai Anh'))
        finally:
            other.closed = True
            self.root.after_cancel(other.timer)
            other.destroy()

    def test_wording_preferences_survive_last_job_but_resume_uses_frozen_wording(self):
        first = self.studio.apply_wording_selection(True, 'bán => báng')
        original = dict(self.start())
        self.write_result(status='failed', message='Fixture stopped')
        self.studio.complete()
        preferred = self.studio.apply_wording_selection(False, 'mua => lấy\ngiá => giá tham khảo')
        self.spawn.reset_mock()
        other = Studio(self.root, self.studio.data_dir)
        try:
            self.assertEqual(other.wording_selection(), preferred)
            self.assertEqual(other.current, original)
            self.spawn.assert_not_called()
            other.resume()
            self.spawn.assert_called_once()
            self.assertEqual(other.wording_selection(), first)
            self.assertEqual(other.current, original)
        finally:
            other.closed = True
            self.root.after_cancel(other.timer)
            other.destroy()

    def test_wording_does_not_change_voice_preview_or_invalidate_replay(self):
        config = self.start('preview')
        self.assertNotIn('wording', config)
        self.complete_preview()
        self.studio.apply_wording_selection(True, 'bán => báng')
        self.assertTrue(self.studio.matching_preview())
        self.assertEqual(str(self.studio.replay_button['state']), 'normal')

    def test_wording_modal_previews_locally_then_saves_only_on_explicit_save(self):
        self.studio.choose_wording()
        dialog = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
        dialog.withdraw()
        panel = dialog.winfo_children()[0]
        table, sample, preview = [child for child in panel.winfo_children() if isinstance(child, tk.Text)]
        check = next(child for child in panel.winfo_children() if isinstance(child, ttk.Checkbutton))
        buttons = next(child for child in panel.winfo_children() if isinstance(child, ttk.Frame))
        check.invoke()
        sample.delete('1.0', 'end')
        sample.insert('1.0', 'Cửa hàng bán đèn. Có bán!')
        next(child for child in buttons.winfo_children() if child['text'] == 'Xem thử').invoke()
        self.assertEqual(preview.get('1.0', 'end').strip(), 'Cửa hàng báng đèn. Có báng!')
        self.assertFalse(self.studio.fields['wording_enabled'].get())
        self.assertFalse((self.studio.data_dir/'preferences.json').exists())
        self.spawn.assert_not_called()
        next(child for child in buttons.winfo_children() if child['text'] == 'Lưu lựa chọn').invoke()
        self.assertTrue(self.studio.fields['wording_enabled'].get())
        saved = json.loads((self.studio.data_dir/'preferences.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['wording'], self.studio.wording_selection())
        self.spawn.assert_not_called()

    def test_wording_modal_cancel_and_invalid_rules_do_not_save_or_start_worker(self):
        before = self.studio.wording_selection()
        self.studio.choose_wording()
        dialog = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
        dialog.withdraw()
        panel = dialog.winfo_children()[0]
        table = next(child for child in panel.winfo_children() if isinstance(child, tk.Text))
        buttons = next(child for child in panel.winfo_children() if isinstance(child, ttk.Frame))
        table.delete('1.0', 'end')
        table.insert('1.0', 'bán => báng\nbán => bán hàng')
        next(child for child in buttons.winfo_children() if child['text'] == 'Lưu lựa chọn').invoke()
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(self.studio.wording_selection(), before)
        self.assertFalse((self.studio.data_dir/'preferences.json').exists())
        next(child for child in buttons.winfo_children() if child['text'] == 'Hủy').invoke()
        self.assertEqual(self.studio.wording_selection(), before)
        self.spawn.assert_not_called()

    def test_old_config_restores_legacy_models_without_mutating_request(self):
        config = dict(voice='Mai Anh',mode='preview')
        self.studio.fill_form(config)
        self.assertEqual(self.studio.fields['translation_model'].get(),'default')
        self.assertEqual(self.studio.fields['translation_effort'].get(),'medium')
        self.assertEqual(self.studio.fields['tts_model'].get(),'v3turbo')
        self.assertEqual(VOICE_SPEED_LABELS[self.studio.fields['voice_speed'].get()], 1.0)
        self.assertEqual(config,dict(voice='Mai Anh',mode='preview'))

    def test_changing_tts_model_does_not_replay_other_model_preview(self):
        self.start('preview')
        self.complete_preview()
        self.assertTrue(self.studio.matching_preview())
        self.studio.apply_model_selection(dict(tts_model='v3nano'))
        self.assertFalse(self.studio.matching_preview())
        self.assertEqual(str(self.studio.replay_button['state']),'disabled')

    def test_model_preferences_restore_and_nano_voice_change_is_reported(self):
        self.studio.fields['voice'].set('Thùy Dung')
        self.studio.apply_model_selection(dict(tts_model='v3nano',translation_model='gpt-6-luna',translation_effort='low'))
        self.assertIn('Giọng trước không có',self.studio.status.get())
        self.studio.fields['voice'].set('Minh Quân')
        self.studio.save_preferences()
        other=Studio(self.root,self.studio.data_dir)
        try:
            self.assertEqual(other.fields['tts_model'].get(),'v3nano')
            self.assertEqual(other.fields['voice'].get(),'Minh Quân')
            self.assertEqual(other.fields['translation_effort'].get(),'low')
        finally:
            other.closed=True
            self.root.after_cancel(other.timer)
            other.destroy()

    def test_saved_models_survive_last_job_restore_but_resume_keeps_original(self):
        original = dict(self.start())
        self.write_result(status='failed', message='Fixture stopped')
        self.studio.complete()
        chosen = dict(asr_model='small', translation_model='gpt-6.1-sol',
                      translation_effort='high', tts_model='v3nano')
        self.studio.apply_model_selection(chosen)
        self.studio.fields['voice'].set('Minh Quân')
        self.studio.fields['voice_speed'].set(next(label for label, value in VOICE_SPEED_LABELS.items() if value == 1.2))
        self.studio.save_preferences()
        self.spawn.reset_mock()
        other = Studio(self.root, self.studio.data_dir)
        try:
            for key, value in chosen.items():
                self.assertEqual(other.fields[key].get(), value)
            self.assertEqual(other.fields['voice'].get(), 'Minh Quân')
            self.assertEqual(VOICE_SPEED_LABELS[other.fields['voice_speed'].get()], 1.2)
            self.assertEqual(other.current, original)
            self.spawn.assert_not_called()
            other.resume()
            self.spawn.assert_called_once()
            self.assertEqual(other.current, original)
            for key in chosen:
                self.assertEqual(other.fields[key].get(), original[key])
            self.assertEqual(VOICE_SPEED_LABELS[other.fields['voice_speed'].get()], 1.0)
        finally:
            other.closed = True
            self.root.after_cancel(other.timer)
            other.destroy()

    def write_result(self, **values):
        atomic_json(Path(self.studio.current['directory'])/'result.json', values)

    def audio_file(self, path):
        with wave.open(str(path), 'wb') as output:
            output.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            output.writeframes(b'\x01\x00'*24000)
        return path

    def complete_preview(self):
        audio = self.audio_file(Path(self.studio.current['directory'])/'preview.wav')
        self.write_result(status='completed', outputs={'audio': str(audio)})
        self.studio.complete()
        return audio

    def test_start_freezes_unicode_folders_voice_and_single_worker(self):
        self.studio.fields['voice'].set('Trúc Ly')
        self.studio.fields['speed'].set(list(SPEEDS)[1])
        config = self.start()
        self.assertEqual(config['voice'], 'Trúc Ly')
        self.assertEqual(config['voice_provider'], 'local')
        self.assertEqual(config['reading_style'], 'conversation')
        self.assertNotIn('reference_audio', config)
        self.assertEqual(config['speed'], 1.0)
        self.assertEqual(config['original_gain'], .1)
        self.assertTrue(Path(config['original_dir']).is_absolute())
        self.assertIn('gốc', config['original_dir'])
        saved = json.loads((Path(config['directory'])/'config.json').read_text(encoding='utf-8'))
        self.studio.fields['voice'].set('Ngọc Huyền')
        self.assertEqual(self.studio.current, saved)
        self.studio.start()
        self.studio.resume()
        self.spawn.assert_called_once()
        self.assertEqual(str(self.studio.start_button['state']), 'disabled')

    def test_missing_input_and_invalid_srt_fail_before_launch(self):
        self.studio.start()
        self.assertIn('chọn video', self.studio.status.get())
        self.studio.input.insert('1.0', 'https://v.douyin.com/Example123/')
        self.studio.fields['subtitles'].set(str(self.path/'missing.srt'))
        self.studio.dub_prepared()
        self.assertIn('SRT', self.studio.status.get())
        self.spawn.assert_not_called()

    def test_supplied_srt_passes_unchanged_without_ui_read_or_translation(self):
        subtitle = self.path/'lời Việt.srt'
        content = '1\n00:00:00,000 --> 00:00:01,000\nGiữ nguyên câu này.\n'
        subtitle.write_text(content, encoding='utf-8')
        self.studio.fields['subtitles'].set(str(subtitle))
        config = self.start(require_subtitles=True)
        self.assertEqual(config['subtitles'], str(subtitle.resolve()))
        self.assertEqual(subtitle.read_text(encoding='utf-8'), content)

    def test_preview_needs_only_voice_and_plays_only_after_explicit_success(self):
        self.studio.fields['original_dir'].set('')
        self.studio.fields['output_dir'].set('')
        self.studio.fields['subtitles'].set(str(self.path/'missing.srt'))
        config = self.start('preview')
        self.assertEqual(config['input'], '')
        self.assertEqual(config['mode'], 'preview')
        self.assertIsNone(config['subtitles'])
        self.open.assert_not_called()
        audio = Path(config['directory'])/'preview.wav'
        self.audio_file(audio)
        self.write_result(status='completed', message='Đã tạo giọng thử.', outputs={'audio': str(audio)})
        self.worker.code = 0
        self.studio.poll()
        self.sound.assert_called_once_with(str(audio), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        self.open.assert_not_called()
        self.assertEqual(self.studio.progress.get(), 100)
        self.assertEqual(str(self.studio.continue_button['state']), 'disabled')

    def test_preview_does_not_autoplay_unexpected_external_path(self):
        self.start('preview')
        outside = self.path/'other.wav'
        outside.write_bytes(b'placeholder')
        self.write_result(status='completed', outputs={'audio': str(outside)})
        self.studio.complete()
        self.open.assert_not_called()
        self.sound.assert_not_called()

    def test_resume_uses_same_frozen_job_and_removes_only_its_stop_marker(self):
        config = dict(self.start())
        self.studio.stop()
        marker = Path(config['stop_file'])
        self.assertTrue(marker.is_file())
        self.worker.code = 2
        self.write_result(status='cancelled', message='Đã dừng.')
        self.studio.complete()
        self.studio.input.delete('1.0', 'end')
        self.studio.input.insert('1.0', 'https://v.douyin.com/Changed123/')
        self.studio.fields['voice'].set('Ngọc Huyền')
        self.studio.fields['output_dir'].set(str(self.path/'changed'))
        self.spawn.reset_mock()
        self.worker.code = None
        self.studio.resume()
        self.spawn.assert_called_once()
        self.assertEqual(self.studio.current, config)
        self.assertEqual(self.studio.input.get('1.0', 'end').strip(), config['input'])
        self.assertEqual(self.studio.fields['voice'].get(), config['voice'])
        self.assertFalse(marker.exists())

    def test_tampered_stop_path_is_not_removed_or_launched(self):
        config = dict(self.start())
        self.studio.complete()
        outside = self.path/'keep.stop'
        outside.touch()
        tampered = dict(config, stop_file=str(outside))
        atomic_json(Path(config['directory'])/'config.json', tampered)
        self.studio.current = tampered
        self.spawn.reset_mock()
        self.studio.resume()
        self.spawn.assert_not_called()
        self.assertTrue(outside.exists())
        self.assertIn('không khớp', self.studio.status.get())

    def test_restore_never_starts_or_plays_preview(self):
        config = self.start('preview')
        audio = Path(config['directory'])/'preview.wav'
        audio.write_bytes(b'placeholder')
        self.write_result(status='completed', outputs={'audio': str(audio)})
        self.spawn.reset_mock()
        other = Studio(self.root, self.path/'data')
        try:
            self.spawn.assert_not_called()
            self.open.assert_not_called()
            self.assertIsNone(other.process)
            self.assertEqual(other.current, config)
            self.assertEqual(str(other.continue_button['state']), 'disabled')
        finally:
            other.closed = True
            self.root.after_cancel(other.timer)
            other.destroy()

    def test_preview_restore_keeps_selected_video_and_output_folders(self):
        self.studio.input.insert('1.0', 'https://v.douyin.com/KeepMyVideo/')
        self.studio.fields['original_dir'].set(str(self.path/'my-originals'))
        self.studio.fields['output_dir'].set(str(self.path/'my-results'))
        self.start('preview')
        other = Studio(self.root, self.path/'data')
        try:
            self.assertEqual(other.input.get('1.0', 'end').strip(), 'https://v.douyin.com/KeepMyVideo/')
            self.assertEqual(other.fields['original_dir'].get(), str(self.path/'my-originals'))
            self.assertEqual(other.fields['output_dir'].get(), str(self.path/'my-results'))
            self.assertIsNone(other.process)
        finally:
            other.closed = True
            self.root.after_cancel(other.timer)
            other.destroy()

    def test_provider_switch_updates_voices_and_never_leaks_local_reference_to_capcut(self):
        self.studio.fields['voice'].set(REFERENCE_VOICE)
        self.studio.fields['reference_audio'].set(str(self.audio_file(self.path/'mẫu.wav')))
        self.studio.fields['voice_provider'].set('capcut')
        self.assertEqual(tuple(self.studio.voice_picker['values']), CAPCUT_VOICES)
        self.assertEqual(self.studio.fields['voice'].get(), CAPCUT_VOICES[0])
        self.assertTrue(self.studio.login_row.winfo_manager())
        self.assertFalse(self.studio.sample_row.winfo_manager())
        config = self.start('preview')
        self.assertEqual(config['voice_provider'], 'capcut')
        self.assertNotIn('reference_audio', config)
        self.assertNotIn('reference_sha256', config)
        self.assertEqual(str(self.studio.provider_picker['state']), 'disabled')
        self.assertEqual(str(self.studio.login_button['state']), 'disabled')
        self.write_result(status='failed', message='CapCut yêu cầu đăng nhập.')
        self.studio.complete()
        self.assertEqual(self.studio.fields['voice_provider'].get(), 'capcut')
        self.assertIn('yêu cầu đăng nhập', self.studio.status.get())
        self.sound.assert_not_called()

    def test_custom_sample_is_frozen_by_absolute_path_and_hash(self):
        sample = self.audio_file(self.path/'mẫu riêng.wav')
        self.studio.fields['voice'].set(REFERENCE_VOICE)
        self.studio.fields['reference_audio'].set(str(sample))
        self.assertTrue(self.studio.sample_row.winfo_manager())
        config = self.start('preview')
        self.assertEqual(config['voice'], REFERENCE_VOICE)
        self.assertEqual(config['reference_audio'], str(sample.resolve()))
        self.assertEqual(config['reference_sha256'], hash_file(sample))
        self.assertNotIn('reference_label', config)
        self.studio.fields['reference_audio'].set(str(self.path/'different.wav'))
        self.assertEqual(self.studio.current, config)
        self.write_result(status='cancelled')
        self.studio.complete()
        self.spawn.reset_mock()
        self.studio.resume()
        self.assertEqual(self.studio.current, config)
        self.assertEqual(self.studio.fields['reference_audio'].get(), str(sample.resolve()))
        self.spawn.assert_called_once()

    def test_missing_or_relative_sample_refuses_before_worker(self):
        self.studio.fields['voice'].set(REFERENCE_VOICE)
        for path in ('', 'relative.wav', str(self.path/'missing.wav')):
            self.studio.fields['reference_audio'].set(path)
            self.studio.start('preview')
            self.spawn.assert_not_called()
            self.assertIn('mẫu', self.studio.status.get())

    def test_sample_aliases_appear_only_when_files_exist_and_restore_without_extra_keys(self):
        sample = self.audio_file(self.path/'saved-sample.wav')
        alias = 'Cô Gái Hoạt Ngôn (theo mẫu)'
        missing = 'Thanh Niên Tự Tin (theo mẫu)'
        with patch('teodub.app.REFERENCE_ALIASES', {alias: sample, missing: self.path/'missing.wav'}):
            self.studio.update_profile_controls()
            choices = tuple(self.studio.voice_picker['values'])
            self.assertIn(alias, choices)
            self.assertNotIn(missing, choices)
            self.assertTrue(all(voice in choices for voice in VOICES))
            self.studio.fields['voice'].set(alias)
            config = self.studio.config_from_form('preview')
            self.assertEqual(config['voice'], REFERENCE_VOICE)
            self.assertEqual(config['reference_audio'], str(sample.resolve()))
            self.assertEqual(config['reference_sha256'], hash_file(sample))
            self.studio.fields['voice'].set(VOICES[0])
            self.studio.fill_form(config)
            self.assertEqual(self.studio.fields['voice'].get(), alias)
            self.assertIn('chỉ gần giống', self.studio.voice_help.get())
            self.assertFalse(self.studio.sample_row.winfo_manager())
            self.studio.save_preferences()
            saved = json.loads((self.studio.data_dir/'preferences.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['voice'], REFERENCE_VOICE)
            self.assertEqual(saved['reference_audio'], str(sample.resolve()))
            self.assertNotIn('reference_sha256', saved)
            sample.unlink()
            self.studio.fill_form(config)
            self.assertEqual(self.studio.fields['voice'].get(), REFERENCE_VOICE)
            self.assertEqual(self.studio.fields['reference_audio'].get(), str(sample.resolve()))

    def test_legacy_config_resumes_without_adding_provider_or_reference_fields(self):
        config = self.start()
        self.write_result(status='cancelled')
        self.studio.complete()
        config.pop('voice_provider')
        config.pop('reading_style')
        self.studio.current = dict(config)
        atomic_json(Path(config['directory'])/'config.json', config)
        self.studio.fields['voice_provider'].set('capcut')
        self.spawn.reset_mock()
        self.studio.resume()
        self.spawn.assert_called_once()
        self.assertEqual(self.studio.current, config)
        self.assertEqual(self.studio.fields['voice_provider'].get(), 'local')
        self.assertNotIn('voice_provider', self.studio.current)
        self.assertNotIn('reading_style', self.studio.current)
        self.assertEqual(self.studio.fields['reading_style'].get(), 'legacy')
        self.assertEqual(self.studio.style_label.get(), STYLES['legacy']['label'])

    def test_all_reading_styles_are_selectable_and_frozen_for_each_provider_and_mode(self):
        self.studio.input.insert('1.0', 'https://v.douyin.com/Example123/')
        self.assertEqual(tuple(self.studio.style_picker['values']), tuple(STYLES[key]['label'] for key in READING_STYLES))
        for provider in ('local', 'capcut'):
            for style in READING_STYLES:
                for mode in ('full', 'preview'):
                    with self.subTest(provider=provider, style=style, mode=mode):
                        self.studio.fields['voice_provider'].set(provider)
                        self.studio.style_label.set(STYLES[style]['label'])
                        self.studio.choose_style()
                        config = self.studio.config_from_form(mode)
                        self.assertEqual(config['reading_style'], style)
                        self.assertEqual(config['voice_provider'], provider)
                        self.assertEqual(config['voice_speed'], 1.0)
                        self.assertEqual(config['speed'], .9)
                        self.studio.fields['reading_style'].set('conversation')
                        self.studio.fill_form(config)
                        self.assertEqual(self.studio.fields['reading_style'].get(), style)
                        self.assertEqual(self.studio.style_label.get(), STYLES[style]['label'])

    def test_voice_speed_is_selectable_saved_and_invalidated_for_preview(self):
        label = next(label for label, value in VOICE_SPEED_LABELS.items() if value == 1.2)
        self.assertEqual(tuple(self.studio.voice_speed_picker['values']), tuple(VOICE_SPEED_LABELS))
        self.studio.fields['voice_speed'].set(label)
        config = self.start('preview')
        self.assertEqual(config['voice_speed'], 1.2)
        self.complete_preview()
        self.assertTrue(self.studio.matching_preview())
        self.studio.fields['voice_speed'].set(next(iter(VOICE_SPEED_LABELS)))
        self.assertFalse(self.studio.matching_preview())
        self.assertEqual(str(self.studio.replay_button['state']), 'disabled')

    def test_expanded_voice_speeds_reach_preview_full_and_form_restore(self):
        self.studio.input.insert('1.0', 'https://v.douyin.com/Example123/')
        self.assertEqual(VOICE_SPEED_LABELS[self.studio.fields['voice_speed'].get()], 1.0)
        for label, speed in VOICE_SPEED_LABELS.items():
            with self.subTest(speed=speed):
                self.studio.fields['voice_speed'].set(label)
                full = self.studio.config_from_form('full')
                self.assertEqual(full['voice_speed'], speed)
                self.assertEqual(self.studio.config_from_form('preview')['voice_speed'], speed)
                self.studio.fill_profile(full)
                self.assertEqual(self.studio.fields['voice_speed'].get(), label)
        self.spawn.assert_not_called()

    def test_burned_subtitle_style_is_selectable_saved_and_frozen(self):
        self.assertFalse(self.studio.fields['burn_subtitles'].get())
        self.assertEqual(str(self.studio.subtitle_color_picker['state']), 'disabled')
        self.studio.fields['burn_subtitles'].set(True)
        self.assertEqual(str(self.studio.subtitle_color_picker['state']), 'readonly')
        self.studio.subtitle_color_label.set(SUBTITLE_COLORS['yellow']['label'])
        self.studio.choose_subtitle_color()
        self.studio.subtitle_weight_label.set(SUBTITLE_WEIGHTS['regular']['label'])
        self.studio.choose_subtitle_weight()
        config = self.start()
        self.assertTrue(config['burn_subtitles'])
        self.assertEqual(config['subtitle_color'], 'yellow')
        self.assertEqual(config['subtitle_weight'], 'regular')
        saved = json.loads((Path(config['directory'])/'config.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['subtitle_color'], 'yellow')
        self.assertEqual(str(self.studio.subtitle_color_picker['state']), 'disabled')

    def test_caption_box_is_selectable_decides_the_text_colour_and_reaches_the_job(self):
        self.assertEqual(self.studio.fields['subtitle_box'].get(), 'none')
        self.assertEqual(str(self.studio.subtitle_box_picker['state']), 'disabled')
        self.studio.fields['burn_subtitles'].set(True)
        self.assertEqual(str(self.studio.subtitle_box_picker['state']), 'readonly')
        self.assertEqual(str(self.studio.subtitle_color_picker['state']), 'readonly')
        self.studio.subtitle_box_label.set(SUBTITLE_BOXES['white']['label'])
        self.studio.choose_subtitle_box()
        self.assertEqual(self.studio.fields['subtitle_box'].get(), 'white')
        self.assertEqual(str(self.studio.subtitle_color_picker['state']), 'disabled')  # The box decides the text colour.
        config = self.start()
        self.assertEqual(config['subtitle_box'], 'white')
        saved = json.loads((Path(config['directory'])/'config.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['subtitle_box'], 'white')
        self.assertEqual(str(self.studio.subtitle_box_picker['state']), 'disabled')
        old = dict(config)
        old.pop('subtitle_box')
        self.studio.fill_form(old)
        self.assertEqual(self.studio.fields['subtitle_box'].get(), 'none')
        self.assertEqual(self.studio.subtitle_box_label.get(), SUBTITLE_BOXES['none']['label'])

    def test_no_box_keeps_job_configs_exactly_as_before(self):
        self.studio.fields['burn_subtitles'].set(True)
        config = self.start()
        self.assertNotIn('subtitle_box', config)

    def test_subtitle_size_control_reaches_job_and_restores_default_for_old_jobs(self):
        self.assertEqual(self.studio.fields['subtitle_size'].get(), '13')
        self.assertEqual(str(self.studio.subtitle_size_picker['state']), 'disabled')
        self.studio.fields['burn_subtitles'].set(True)
        self.assertEqual(str(self.studio.subtitle_size_picker['state']), 'normal')
        self.studio.fields['subtitle_size'].set('20')
        config = self.start()
        self.assertEqual(config['subtitle_size'], 20)
        self.assertEqual(str(self.studio.subtitle_size_picker['state']), 'disabled')
        self.studio.fill_form(config)
        self.assertEqual(self.studio.fields['subtitle_size'].get(), '20')
        old = dict(config)
        old.pop('subtitle_size')
        self.studio.fill_form(old)
        self.assertEqual(self.studio.fields['subtitle_size'].get(), '13')

    def test_new_subtitle_preferences_survive_last_job_but_resume_restores_job_style(self):
        self.studio.fields['burn_subtitles'].set(True)
        self.studio.fields['subtitle_color'].set('yellow')
        self.studio.fields['subtitle_weight'].set('regular')
        original = dict(self.start())
        self.write_result(status='failed', message='Fixture stopped')
        self.studio.complete()
        self.studio.fields['burn_subtitles'].set(False)
        self.studio.fields['subtitle_color'].set('black')
        self.studio.fields['subtitle_weight'].set('bold')
        self.studio.save_preferences()
        self.spawn.reset_mock()
        other = Studio(self.root, self.studio.data_dir)
        try:
            self.assertFalse(other.fields['burn_subtitles'].get())
            self.assertEqual(other.fields['subtitle_color'].get(), 'black')
            self.assertEqual(other.current, original)
            other.resume()
            self.spawn.assert_called_once()
            self.assertTrue(other.fields['burn_subtitles'].get())
            self.assertEqual(other.fields['subtitle_color'].get(), 'yellow')
            self.assertEqual(other.fields['subtitle_weight'].get(), 'regular')
        finally:
            other.closed = True
            self.root.after_cancel(other.timer)
            other.destroy()

    def test_selected_style_is_saved_and_resume_ignores_changed_dropdown(self):
        self.studio.fields['reading_style'].set('story')
        config = dict(self.start())
        self.assertEqual(str(self.studio.style_picker['state']), 'disabled')
        saved = json.loads((Path(config['directory'])/'config.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['reading_style'], 'story')
        self.write_result(status='cancelled')
        self.studio.complete()
        self.studio.fields['reading_style'].set('advertising')
        self.spawn.reset_mock()
        self.studio.resume()
        self.spawn.assert_called_once()
        self.assertEqual(self.studio.current, config)
        self.assertEqual(self.studio.fields['reading_style'].get(), 'story')

    def test_preferences_preserve_style_without_job_and_old_preferences_choose_new_default(self):
        self.studio.fields['reading_style'].set('advertising')
        self.studio.save_preferences()
        other = Studio(self.root, self.path/'data')
        try:
            self.assertEqual(other.fields['reading_style'].get(), 'advertising')
            self.assertIsNone(other.current)
        finally:
            other.closed = True
            self.root.after_cancel(other.timer)
            other.destroy()
        prefs_path = self.studio.data_dir/'preferences.json'
        prefs = json.loads(prefs_path.read_text(encoding='utf-8'))
        prefs.pop('reading_style')
        atomic_json(prefs_path, prefs)
        other = Studio(self.root, self.path/'data')
        try:
            self.assertEqual(other.fields['reading_style'].get(), 'conversation')
            self.assertIsNone(other.current)
        finally:
            other.closed = True
            self.root.after_cancel(other.timer)
            other.destroy()

    def test_preview_replay_requires_same_reading_style_and_style_change_stops_audio(self):
        self.studio.fields['reading_style'].set('story')
        self.start('preview')
        audio = self.complete_preview()
        self.studio.fields['reading_style'].set('advertising')
        self.assertFalse(self.studio.playing)
        self.assertEqual(str(self.studio.replay_button['state']), 'disabled')
        self.sound.reset_mock()
        self.studio.play_preview()
        self.sound.assert_not_called()
        self.studio.fields['reading_style'].set('story')
        self.assertEqual(str(self.studio.replay_button['state']), 'normal')
        self.studio.play_preview()
        self.sound.assert_called_once_with(str(audio), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def test_legacy_preview_matches_only_legacy_style_and_never_autoplays_on_restore(self):
        config = self.start('preview')
        config.pop('reading_style')
        atomic_json(Path(config['directory'])/'config.json', config)
        audio = self.audio_file(Path(config['directory'])/'preview.wav')
        self.write_result(status='completed', outputs={'audio': str(audio)})
        other = Studio(self.root, self.path/'data')
        try:
            self.assertEqual(other.fields['reading_style'].get(), 'legacy')
            self.assertEqual(str(other.replay_button['state']), 'normal')
            self.sound.assert_not_called()
            self.assertNotIn('reading_style', other.current)
            other.fields['reading_style'].set('conversation')
            self.assertEqual(str(other.replay_button['state']), 'disabled')
        finally:
            other.closed = True
            self.root.after_cancel(other.timer)
            other.destroy()

    def test_unknown_style_rejects_before_worker(self):
        self.studio.fields['reading_style'].set('unsupported-style')
        self.studio.start('preview')
        self.spawn.assert_not_called()
        self.assertIn('Chưa chạy', self.studio.status.get())

    def test_replay_is_inline_without_new_worker_and_stop_and_start_end_playback(self):
        self.start('preview')
        audio = self.complete_preview()
        self.assertTrue(self.studio.playing)
        self.assertEqual(str(self.studio.stop_listen_button['state']), 'normal')
        self.studio.stop_playback()
        self.sound.assert_called_with(None, 0)
        self.assertFalse(self.studio.playing)
        self.spawn.reset_mock()
        self.studio.play_preview()
        self.sound.assert_called_with(str(audio), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        self.spawn.assert_not_called()
        self.open.assert_not_called()
        self.studio.input.insert('1.0', str(self.path/'video.mp4'))
        self.studio.start()
        self.assertFalse(self.studio.playing)
        self.sound.assert_called_with(None, 0)
        self.spawn.assert_called_once()

    def test_profile_changes_stop_playback_and_disable_mismatched_replay(self):
        self.start('preview')
        self.complete_preview()
        self.studio.fields['voice'].set('Trúc Ly')
        self.assertFalse(self.studio.playing)
        self.assertEqual(str(self.studio.replay_button['state']), 'disabled')
        self.sound.reset_mock()
        self.studio.play_preview()
        self.sound.assert_not_called()

    def test_changed_custom_sample_requires_new_preview_without_spending_quota(self):
        sample = self.audio_file(self.path/'sample.wav')
        self.studio.fields['voice'].set(REFERENCE_VOICE)
        self.studio.fields['reference_audio'].set(str(sample))
        self.start('preview')
        self.complete_preview()
        self.studio.stop_playback()
        self.sound.reset_mock()
        self.spawn.reset_mock()
        with sample.open('ab') as output:
            output.write(b'changed')
        self.studio.play_preview()
        self.sound.assert_not_called()
        self.spawn.assert_not_called()
        self.assertIn('đã thay đổi', self.studio.status.get())

    def test_restore_finished_preview_enables_explicit_replay_without_autoplay(self):
        self.start('preview')
        audio = self.audio_file(Path(self.studio.current['directory'])/'preview.wav')
        self.write_result(status='completed', outputs={'audio': str(audio)})
        self.spawn.reset_mock()
        other = Studio(self.root, self.path/'data')
        try:
            self.sound.assert_not_called()
            self.spawn.assert_not_called()
            self.assertEqual(str(other.replay_button['state']), 'normal')
            other.play_preview()
            self.sound.assert_called_once_with(str(audio), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        finally:
            other.stop_playback(refresh=False)
            other.closed = True
            self.root.after_cancel(other.timer)
            other.destroy()

    def test_full_result_and_stopped_preview_never_autoplay(self):
        self.start()
        audio = self.audio_file(Path(self.studio.current['directory'])/'preview.wav')
        self.write_result(status='completed', outputs={'audio': str(audio)})
        self.studio.complete()
        self.sound.assert_not_called()
        self.studio.start('preview')
        self.studio.stop()
        self.complete_preview()
        self.sound.assert_not_called()

    def test_second_preview_cannot_autoplay_previous_audio_when_report_is_wrong(self):
        self.start('preview')
        old_audio = self.complete_preview()
        self.studio.start('preview')
        self.sound.reset_mock()
        self.write_result(status='completed', outputs={'audio': str(old_audio)})
        self.studio.complete()
        self.sound.assert_not_called()
        self.assertEqual(str(self.studio.replay_button['state']), 'disabled')

    def test_login_launcher_is_async_and_does_not_claim_authenticated_or_start_job(self):
        self.studio.fields['voice_provider'].set('capcut')
        with patch('teodub.app.threading.Thread') as thread, patch('teodub.app.open_capcut_login') as login:
            self.studio.login_capcut()
            self.assertTrue(self.studio.login_pending)
            thread.return_value.start.assert_called_once()
            login.assert_not_called()
            self.studio.start('preview')
            self.spawn.assert_not_called()
            thread.call_args.kwargs['target']()
            login.assert_called_once_with()
            self.studio.poll()
        self.assertFalse(self.studio.login_pending)
        self.assertIn('Đăng nhập thủ công', self.studio.status.get())
        self.assertIn('chưa xác nhận', self.studio.status.get())
        self.assertEqual(self.studio.fields['voice_provider'].get(), 'capcut')

    def test_login_error_is_visible_without_switching_provider(self):
        self.studio.fields['voice_provider'].set('capcut')
        with patch('teodub.app.threading.Thread') as thread, patch('teodub.app.open_capcut_login', side_effect=ValueError('Không tìm thấy Edge')):
            self.studio.login_capcut()
            thread.call_args.kwargs['target']()
            self.studio.poll()
        self.assertIn('Không tìm thấy Edge', self.studio.status.get())
        self.assertEqual(self.studio.fields['voice_provider'].get(), 'capcut')
        self.spawn.assert_not_called()

    def test_incremental_unicode_events_and_bounded_log(self):
        config = self.start()
        events = Path(config['directory'])/'events.jsonl'
        encoded = (json.dumps(dict(message='Đang tạo giọng Thùy Dung', percent=42), ensure_ascii=False)+'\n').encode('utf-8')
        split = encoded.index('ù'.encode('utf-8'))+1
        events.write_bytes(encoded[:split])
        self.studio.read_events()
        self.assertNotIn('Thùy', self.studio.log.get('1.0', 'end'))
        with events.open('ab') as stream:
            stream.write(encoded[split:])
        self.studio.read_events()
        self.assertEqual(self.studio.progress.get(), 42)
        self.assertEqual(self.studio.log.get('1.0', 'end').count('Thùy Dung'), 1)
        self.studio.read_events()
        self.assertEqual(self.studio.log.get('1.0', 'end').count('Thùy Dung'), 1)
        for i in range(MAX_LOG_LINES+30):
            self.studio.append(f'Dòng {i}')
        self.assertLessEqual(int(self.studio.log.index('end-1c').split('.')[0]), MAX_LOG_LINES+1)

    def test_progress_ticks_replace_one_status_line_and_log_only_stage_changes_and_errors(self):
        config = self.start()
        events = [dict(stage='download', percent=i, message=f'Đang tải: {i}%') for i in range(1, 60)]
        events += [dict(stage='asr', percent=i, message=f'Đang nhận dạng: {i}%') for i in range(1, 30)]
        events.append(dict(stage='asr', status='failed', message='Lỗi nhận dạng: không có lời nói.'))
        path = Path(config['directory'])/'events.jsonl'
        path.write_text(''.join(json.dumps(event, ensure_ascii=False)+'\n' for event in events), encoding='utf-8')
        self.studio.read_events()
        log = self.studio.log.get('1.0', 'end')
        self.assertEqual(log.count('Đang tải:'), 1)
        self.assertEqual(log.count('Đang nhận dạng:'), 1)
        self.assertIn('Lỗi nhận dạng', log)
        self.assertEqual(self.studio.status.get(), 'Lỗi nhận dạng: không có lời nói.')
        self.assertEqual(self.studio.progress.get(), 29)

    def test_missing_report_is_never_presented_as_success(self):
        self.start()
        self.worker.code = 0
        self.studio.poll()
        self.assertIsNone(self.studio.result)
        self.assertLess(self.studio.progress.get(), 100)
        self.assertIn('Chưa có báo cáo', self.studio.status.get())
        self.assertEqual(str(self.studio.continue_button['state']), 'normal')

    def test_close_stops_then_terminates_only_its_worker_without_blocking_wait(self):
        config = self.start()
        with patch('teodub.app.time.monotonic', return_value=10):
            self.studio.close()
        self.assertTrue(Path(config['stop_file']).exists())
        self.assertFalse(self.worker.terminated)
        with patch('teodub.app.time.monotonic', return_value=16):
            self.studio.poll()
        self.assertTrue(self.worker.terminated)
        self.assertFalse(self.worker.killed)
        with patch('teodub.app.time.monotonic', return_value=19):
            self.studio.poll()
        self.assertTrue(self.worker.killed)

    def test_stop_alone_bounds_unresponsive_model_download_and_allows_resume(self):
        config = dict(self.start())
        with patch('teodub.app.time.monotonic', return_value=100):
            self.studio.stop()
        events = Path(config['directory'])/'events.jsonl'
        events.write_text(json.dumps(dict(stage='models', percent=42, message='Đang tải mô hình 42%'))+'\n', encoding='utf-8')
        with patch('teodub.app.time.monotonic', return_value=107):
            self.studio.poll()
        self.assertFalse(self.worker.terminated)
        self.assertIn('Đang dừng', self.studio.status.get())
        with patch('teodub.app.time.monotonic', return_value=108):
            self.studio.poll()
        self.assertTrue(self.worker.terminated)
        self.worker.code = 2
        self.studio.poll()
        self.assertFalse(self.studio.closed)
        self.assertEqual(str(self.studio.continue_button['state']), 'normal')
        self.spawn.reset_mock()
        self.worker.code = None
        self.studio.resume()
        self.spawn.assert_called_once()
        self.assertEqual(self.studio.current, config)
        self.assertIsNone(self.studio.stop_started)
        self.assertIsNone(self.studio.terminated_at)

    def test_failed_process_launch_does_not_freeze_controls_or_destroy_old_request(self):
        first = dict(self.start())
        self.studio.complete()
        self.spawn.side_effect = OSError('fixture failed start')
        self.studio.start()
        self.assertIsNone(self.studio.process)
        self.assertEqual(self.studio.current, first)
        saved = json.loads((self.studio.data_dir/'last-job.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['directory'], first['directory'])
        self.assertEqual(str(self.studio.start_button['state']), 'normal')
        self.assertIn('fixture failed start', self.studio.status.get())


class EntryPointTests(unittest.TestCase):
    def test_frozen_worker_reuses_executable_with_worker_flag(self):
        with patch('teodub.app.sys.frozen', True, create=True), patch('teodub.app.sys.executable', 'TeoDubStudio.exe'):
            command = worker_command('config.json')
        self.assertEqual(command[:3], ['TeoDubStudio.exe', '--worker', '--config'])

    def test_worker_dispatch_never_constructs_gui(self):
        import launch
        pipeline = ModuleType('teodub.pipeline')
        pipeline.main = Mock(return_value=2)
        with patch.dict(sys.modules, {'teodub.pipeline': pipeline}), patch('teodub.app.main') as ui:
            self.assertEqual(launch.main(['--worker', '--config', 'request.json']), 2)
        pipeline.main.assert_called_once_with(['--config', 'request.json'])
        ui.assert_not_called()

    def test_build_command_is_explicit_and_does_not_package_accounts_or_models(self):
        project = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('studio_build', project/'scripts'/'build_windows.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root/'.venv'/'Scripts'/'python.exe'
            python.parent.mkdir(parents=True)
            python.write_bytes(b'fixture')
            command = module.build_command(root)
        self.assertIn('--onedir', command)
        self.assertIn('--windowed', command)
        self.assertNotIn('--add-data', command)
        self.assertNotIn('--worker', command)


if __name__ == '__main__':
    unittest.main()
