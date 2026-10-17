"""Explicit preparation steps never launch downstream work without a button press."""
from pathlib import Path
import unittest
from unittest.mock import patch

from teodub.common import atomic_json, hash_file, Cancelled
from teodub.dialogue_editor import draft_binding
from tests import test_pipeline, test_dialogue_editor


class StepPipelineTests(unittest.TestCase):
    def setUp(self):
        self.f = test_pipeline.PipelineTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_download_only_skips_all_ai_and_upload_and_resumes(self):
        with patch('teodub.capcut_tts.preflight') as preflight, patch('teodub.drive_delivery.upload_completed_video') as drive:
            result = self.f.run_job(mode='download', input='https://example.com/video', asr_model='invalid')
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['outputs'], {'source': str(self.f.source)})
            again = self.f.run_job(mode='download', input='https://example.com/video', asr_model='invalid')
            self.assertEqual(again, result)
            self.assertEqual(self.f.download_count, 1)
            self.f.asr.assert_not_called()
            self.f.translate.assert_not_called()
            self.f.tts.assert_not_called()
            self.f.render.assert_not_called()
            preflight.assert_not_called()
            drive.assert_not_called()

    def test_download_then_translate_then_dub_reuses_source_and_words(self):
        downloaded = self.f.run_job(mode='download', input='https://example.com/video')
        captions = self.f.root/'captions-step'
        translated = self.f.run_job(mode='captions', input=downloaded['outputs']['source'],
            directory=str(captions), stop_file=str(captions/'stop.request'))
        self.assertEqual(translated['status'], 'completed')
        self.f.tts.assert_not_called()
        self.f.render.assert_not_called()
        dubbed = self.f.root/'dub-step'
        srt = translated['outputs']['subtitles']
        self.f.asr.reset_mock()
        self.f.translate.reset_mock()
        result = self.f.run_job(input=downloaded['outputs']['source'], directory=str(dubbed),
            stop_file=str(dubbed/'stop.request'), subtitles=srt, subtitles_sha256=hash_file(srt),
            dialogue_source_sha256=hash_file(self.f.source), require_subtitles=True)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.f.download_count, 1)
        self.f.asr.assert_not_called()
        self.f.translate.assert_not_called()
        self.f.tts.assert_called_once()
        self.f.render.assert_called_once()

    def test_final_step_missing_srt_never_translates(self):
        result = self.f.run_job(require_subtitles=True)
        self.assertEqual(result['status'], 'failed')
        self.f.asr.assert_not_called()
        self.f.translate.assert_not_called()
        self.f.tts.assert_not_called()

    def test_cancel_download_keeps_resumable_job_without_advancing(self):
        self.f.download.side_effect = Cancelled('Đã dừng tải')
        result = self.f.run_job(mode='download', input='https://example.com/video')
        self.assertEqual(result['status'], 'cancelled')
        self.assertTrue(result['resumable'])
        self.f.translate.assert_not_called()
        self.f.tts.assert_not_called()


class StepUITests(unittest.TestCase):
    def test_auto_ignores_old_edited_dialogue_and_translates_current_source(self):
        _old, old_source = self.f.owned_captions()
        self.studio.use_prepared_result()
        self.studio.edit_dialogue()
        self.studio.dialogue_editor.save()
        stale = Path(self.studio.fields['subtitles'].get())
        stale_hash = hash_file(stale)
        engines = test_pipeline.PipelineTests()
        engines.setUp()
        self.addCleanup(engines.doCleanups)
        self.studio.input.delete('1.0', 'end')
        self.studio.input.insert('1.0', str(engines.source))
        with patch.object(self.studio, 'launch') as launch:
            self.studio.start_button.invoke()
        config = launch.call_args.args[0]
        self.assertIsNone(config['subtitles'])
        self.assertNotIn('subtitles_sha256', config)
        self.assertNotIn('dialogue_source_sha256', config)
        self.assertEqual(self.studio.fields['subtitles'].get(), '')
        result = test_pipeline.pipeline.run(config)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(engines.recognition_count, 1)
        self.assertEqual(engines.translation_count, 1)
        self.assertEqual(engines.tts.call_args.args[0], engines.cues)
        self.assertEqual(hash_file(stale), stale_hash)
        self.assertTrue(old_source.is_file())
        engines.asr.reset_mock()
        engines.translate.reset_mock()
        engines.tts.reset_mock()
        engines.render.reset_mock()
        again = test_pipeline.pipeline.run(config)
        self.assertEqual(again['outputs'], result['outputs'])
        engines.asr.assert_not_called()
        engines.translate.assert_not_called()
        engines.tts.assert_not_called()
        engines.render.assert_not_called()

    def test_auto_ignores_missing_srt_but_final_step_rejects_it(self):
        missing = str(self.f.folder/'missing.srt')
        self.studio.fields['subtitles'].set(missing)
        with patch.object(self.studio, 'launch') as launch:
            self.studio.dub_button.invoke()
            launch.assert_not_called()
            self.assertEqual(self.studio.fields['subtitles'].get(), missing)
            self.studio.start_button.invoke()
            self.assertIsNone(launch.call_args.args[0]['subtitles'])

    def test_auto_launch_failure_keeps_selected_dialogue(self):
        self.studio.fields['subtitles'].set(str(self.f.path))
        with patch.object(self.studio, 'launch', side_effect=OSError('worker failed')):
            self.studio.start_button.invoke()
        self.assertEqual(self.studio.fields['subtitles'].get(), str(self.f.path))

    def test_auto_completion_selects_new_dialogue_and_restore_preserves_it(self):
        config, source = self.f.owned_captions()
        config['mode'] = 'full'
        config['subtitles'] = None
        self.studio.current = config
        directory = Path(config['directory'])
        atomic_json(directory/'config.json', config)
        atomic_json(directory/'result.json', {'status': 'completed', 'message': 'Đã xuất video mới.'})
        atomic_json(self.f.folder/'last-job.json', {'directory': str(directory)})
        self.studio.fields['subtitles'].set('old.srt')
        with patch.object(self.studio, 'launch') as launch:
            self.studio.complete()
            self.assertEqual(self.studio.fields['subtitles'].get(), str(self.f.path))
            self.assertEqual(self.studio.input.get('1.0', 'end').strip(), str(source))
            self.assertEqual(self.studio.status.get(), 'Đã xuất video mới.')
            self.studio.restore()
            self.assertEqual(self.studio.fields['subtitles'].get(), str(self.f.path))
            self.studio.dub_button.invoke()
        final = launch.call_args.args[0]
        self.assertTrue(final['require_subtitles'])
        self.assertEqual(final['dialogue_source_sha256'], hash_file(source))

    def test_auto_completion_rejects_tampered_generated_dialogue(self):
        config, _source = self.f.owned_captions()
        config.update(mode='full', subtitles=None)
        self.studio.current = config
        self.f.path.write_text('changed', encoding='utf-8')
        with patch.object(self.studio, 'read_result', return_value={'status': 'completed'}):
            self.studio.complete()
        self.assertEqual(self.studio.fields['subtitles'].get(), '')
        self.assertIn('bị thay đổi', self.studio.status.get())

    def test_saved_but_disabled_region_requires_explicit_export_choice(self):
        from teodub.subtitle_clean_profiles import save_profile
        _config, source = self.f.owned_captions()
        self.studio.use_prepared_result()
        for answer in (None, False, True):
            with self.subTest(answer=answer):
                save_profile(self.studio.data_dir, source, dict(region=[.1, .7, .9, .8]))
                self.studio.sync_clean_source(force=True)
                with patch('teodub.app.messagebox.askyesnocancel', return_value=answer) as question, \
                        patch.object(self.studio, 'launch') as launch:
                    self.studio.dub_button.invoke()
                question.assert_called_once()
                if answer is None:
                    launch.assert_not_called()
                else:
                    self.assertEqual(launch.call_args.args[0]['subtitle_clean']['enabled'], answer)
                    self.assertEqual(self.studio.clean_enabled.get(), answer)

    def test_save_and_enable_button_persists_and_does_not_ask_again(self):
        from teodub.subtitle_clean_ui import SubtitleCleanDialog
        from teodub.subtitle_clean_profiles import load_profile
        _config, source = self.f.owned_captions()
        self.studio.use_prepared_result()
        with patch.object(SubtitleCleanDialog, 'show_original'):
            dialog = SubtitleCleanDialog(self.studio)
        dialog.region = [.1, .7, .9, .8]
        dialog.apply_button.invoke()
        self.assertTrue(load_profile(self.studio.data_dir, source)['enabled'])
        self.assertTrue(self.studio.clean_enabled.get())
        with patch('teodub.app.messagebox.askyesnocancel') as question, patch.object(self.studio, 'launch') as launch:
            self.studio.dub_button.invoke()
        question.assert_not_called()
        self.assertTrue(launch.call_args.args[0]['subtitle_clean']['enabled'])

    def test_prepared_video_save_region_tick_then_dub_with_bound_settings(self):
        from teodub.subtitle_clean_ui import SubtitleCleanDialog
        from teodub.subtitle_clean_profiles import load_profile
        config, source = self.f.owned_captions()
        self.studio.use_prepared_result()
        with patch.object(SubtitleCleanDialog, 'show_original'):
            dialog = SubtitleCleanDialog(self.studio)
        dialog.region = [.1, .7, .9, .8]
        dialog.save()
        self.assertFalse(dialog.winfo_exists())
        self.assertFalse(self.studio.clean_enabled.get())
        self.assertEqual(load_profile(self.studio.data_dir, source)['region'], [.1, .7, .9, .8])
        self.studio.clean_check.invoke()
        self.assertTrue(self.studio.clean_enabled.get())
        with patch.object(self.studio, 'launch') as launch:
            self.studio.dub_button.invoke()
        full = launch.call_args.args[0]
        self.assertTrue(full['require_subtitles'])
        self.assertEqual(full['subtitles'], str(self.f.path))
        self.assertEqual(full['input'], str(source))
        self.assertEqual(full['subtitle_clean']['source_sha256'], hash_file(source))
        self.assertTrue(full['subtitle_clean']['enabled'])
        self.studio.clean_check.invoke()
        self.assertFalse(self.studio.clean_enabled.get())
        self.assertEqual(load_profile(self.studio.data_dir, source)['region'], [.1, .7, .9, .8])

    def test_region_never_leaks_to_a_different_video(self):
        from teodub.subtitle_clean_profiles import save_profile, set_enabled
        _config, source = self.f.owned_captions()
        self.studio.use_prepared_result()
        save_profile(self.studio.data_dir, source, dict(region=[.1, .7, .9, .8]))
        set_enabled(self.studio.data_dir, source, True)
        self.studio.sync_clean_source(force=True)
        self.assertTrue(self.studio.clean_enabled.get())
        other = source.parent/'other.mp4'
        other.write_bytes(b'different source')
        self.studio.input.delete('1.0', 'end')
        self.studio.input.insert('1.0', str(other))
        self.studio.sync_clean_source()
        self.assertFalse(self.studio.clean_enabled.get())
        self.assertNotIn('region', self.studio.clean_options)
        self.studio.clean_check.invoke()
        self.assertFalse(self.studio.clean_enabled.get())
        self.assertIn('lưu vùng', self.studio.status.get())
        self.studio.input.delete('1.0', 'end')
        self.studio.input.insert('1.0', str(source))
        self.studio.sync_clean_source()
        self.assertTrue(self.studio.clean_enabled.get())

    def setUp(self):
        self.f = test_dialogue_editor.DialogueUITests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.studio = self.f.studio

    def test_prepare_buttons_route_without_voice_output_or_drive_requirements(self):
        self.studio.fields['output_dir'].set('')
        self.studio.fields['drive_enabled'].set(True)
        with patch.object(self.studio, 'voice_selection', side_effect=AssertionError('no TTS')), \
                patch.object(self.studio, 'launch') as launch:
            self.studio.download_button.invoke()
            self.assertEqual(launch.call_args.args[0]['mode'], 'download')
            self.assertNotIn('translation_model', launch.call_args.args[0])
            self.studio.captions_button.invoke()
            self.assertEqual(launch.call_args.args[0]['mode'], 'captions')
            self.assertIn('translation_model', launch.call_args.args[0])
            self.assertNotIn('drive_upload', launch.call_args.args[0])

    def test_final_button_requires_subtitles_auto_button_still_allows_translation(self):
        with patch.object(self.studio, 'launch') as launch:
            self.studio.dub_button.invoke()
            launch.assert_not_called()
            self.assertIn('chọn SRT Việt', self.studio.status.get())
            self.studio.start_button.invoke()
            self.assertEqual(launch.call_args.args[0]['mode'], 'full')
            self.assertNotIn('require_subtitles', launch.call_args.args[0])
            self.studio.fields['subtitles'].set(str(self.f.path))
            self.studio.dub_button.invoke()
            self.assertTrue(launch.call_args.args[0]['require_subtitles'])

    def test_download_completion_selects_local_source_only_and_survives_restart(self):
        config, source = self.f.owned_captions()
        config['mode'] = 'download'
        self.studio.current = config
        directory = Path(config['directory'])
        atomic_json(directory/'config.json', config)
        atomic_json(directory/'result.json', {'status': 'completed', 'outputs': {'source': str(source)}})
        atomic_json(self.f.folder/'last-job.json', {'directory': str(directory)})
        self.studio.fields['subtitles'].set(str(self.f.path))
        with patch.object(self.studio, 'launch') as launch:
            self.studio.complete()
            self.assertEqual(self.studio.fields['subtitles'].get(), '')
            self.assertEqual(self.studio.input.get('1.0', 'end').strip(), str(source))
            self.studio.restore()
            self.assertEqual(self.studio.input.get('1.0', 'end').strip(), str(source))
            self.assertEqual(self.studio.config_from_form('captions')['input'], str(source))
            launch.assert_not_called()

    def test_caption_completion_pins_source_without_requiring_editor_save(self):
        config, source = self.f.owned_captions()
        directory = Path(config['directory'])
        atomic_json(directory/'result.json', {'status': 'completed', 'outputs': {'subtitles': str(self.f.path)}})
        atomic_json(self.f.folder/'last-job.json', {'directory': str(directory)})
        self.studio.complete()
        self.studio.restore()
        with patch.object(self.studio, 'launch') as launch:
            self.studio.dub_button.invoke()
        full = launch.call_args.args[0]
        self.assertEqual(full['input'], str(source))
        self.assertEqual(full['subtitles'], str(self.f.path))
        self.assertEqual(full['dialogue_source_sha256'], hash_file(source))
        self.assertEqual(draft_binding(self.f.path, self.f.folder), {'dialogue_source_sha256': hash_file(source)})

    def test_tampered_prepared_words_do_not_launch_final_step(self):
        self.f.owned_captions()
        self.studio.use_prepared_result()
        self.f.path.write_text('modified', encoding='utf-8')
        with patch.object(self.studio, 'launch') as launch:
            self.studio.dub_button.invoke()
        launch.assert_not_called()
        self.assertIn('bị thay đổi', self.studio.status.get())

    def test_busy_steps_cannot_start_extra_worker(self):
        self.studio.campaign_busy = True
        self.studio.refresh_buttons()
        with patch.object(self.studio, 'launch') as launch:
            for button in (self.studio.download_button, self.studio.captions_button, self.studio.dub_button):
                self.assertEqual(str(button['state']), 'disabled')
                button.invoke()
            launch.assert_not_called()

    def test_open_download_result_opens_source_folder(self):
        config, source = self.f.owned_captions()
        self.studio.result = {'status': 'completed', 'outputs': {'source': str(source)}}
        with patch('teodub.app.os.startfile') as opened:
            self.studio.open_result()
        opened.assert_called_once_with(source.parent)

    def test_controls_fit_standard_window_width(self):
        self.f.root.geometry('1120x900')
        self.f.root.update_idletasks()
        parent = self.studio.dub_button.master
        required = sum(button.winfo_reqwidth()+6 for button in (
            self.studio.download_button, self.studio.captions_button, self.studio.dialogue_button, self.studio.dub_button))
        self.assertLess(required, 1000)

    def test_corrupt_prepared_metadata_does_not_crash_restore(self):
        config, source = self.f.owned_captions()
        self.studio.use_prepared_result()
        self.studio.save_preferences()
        atomic_json(Path(config['directory'])/'state.json', {})
        self.studio.restore()
        self.f.spawn.assert_not_called()
