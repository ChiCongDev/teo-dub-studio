"""Offline human-review flow: immutable drafts, pipeline routing and hidden Tk UI."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from teodub.common import atomic_json, hash_file
from teodub.dialogue_editor import DialogueEditor, draft_binding, save_dialogue
from teodub.srt import write_srt
from teodub.subtitles import read_vietnamese_srt
from tests import test_pipeline, test_campaign_ui


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'source.srt'
        self.cues = [dict(start_ms=0, end_ms=500, text='Xin chào.'),
                     dict(start_ms=600, end_ms=900, text='Cảm ơn.')]
        write_srt(self.path, self.cues)
        self.digest = hash_file(self.path)

    def save(self, texts):
        return save_dialogue(self.path, texts, self.root, expected_sha256=self.digest,
                             source_sha256='a'*64)

    def test_edits_preserve_timings_original_and_source_binding(self):
        first = self.save(['Chào cả nhà!', '  Hẹn gặp\nlại nhé. '])
        second = self.save(['Xin chào bạn!', 'Tạm biệt.'])
        self.assertNotEqual(first, second)
        self.assertEqual(hash_file(self.path), self.digest)
        edited = read_vietnamese_srt(first)
        self.assertEqual(edited[1]['text'], 'Hẹn gặp lại nhé.')
        self.assertEqual([(x['start_ms'], x['end_ms']) for x in edited], [(0, 500), (600, 900)])
        self.assertEqual(draft_binding(first, self.root), {'dialogue_source_sha256': 'a'*64})

    def test_empty_chinese_wrong_count_rejected_without_files(self):
        for texts in [['', 'Cảm ơn'], ['你好', 'Cảm ơn'], ['Chỉ một câu']]:
            with self.assertRaises(ValueError):
                self.save(texts)
        self.assertFalse((self.root/'dialogue-drafts').exists())

    def test_source_changed_during_edit_rejected(self):
        self.path.write_text('changed', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.save(['A', 'B'])

    def test_managed_draft_tamper_rejected_external_srt_allowed(self):
        target = self.save(['A', 'B'])
        target.write_text('changed', encoding='utf-8')
        with self.assertRaises(ValueError):
            draft_binding(target, self.root)
        self.assertEqual(draft_binding(self.path, self.root), {})


class DialoguePipelineTests(unittest.TestCase):
    def setUp(self):
        self.f = test_pipeline.PipelineTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_prepare_stops_before_voice_render_drive_and_reuses_completed_captions(self):
        with patch('teodub.capcut_tts.preflight') as preflight, patch('teodub.drive_delivery.upload_completed_video') as upload:
            first = self.f.run_job(mode='captions', voice_provider='capcut', voice='invalid')
            self.assertEqual(first['status'], 'completed')
            self.assertEqual(set(first['outputs']), {'subtitles'})
            self.f.tts.assert_not_called()
            self.f.render.assert_not_called()
            preflight.assert_not_called()
            upload.assert_not_called()
            self.f.asr.reset_mock()
            self.f.translate.reset_mock()
            second = self.f.run_job(mode='captions', voice_provider='capcut', voice='invalid')
            self.assertEqual(first, second)
            self.f.asr.assert_not_called()
            self.f.translate.assert_not_called()

    def test_edited_words_reach_tts_and_render_without_translation(self):
        result = self.f.run_job(mode='captions')
        original = Path(result['outputs']['subtitles'])
        digest = hash_file(original)
        target = save_dialogue(original, ['Lời tôi đã sửa.', 'Tự nhiên hơn nhé!'], self.f.root,
                              expected_sha256=digest, source_sha256=hash_file(self.f.source))
        job = self.f.root/'edited-job'
        self.f.asr.reset_mock()
        self.f.translate.reset_mock()
        result = self.f.run_job(directory=str(job), stop_file=str(job/'stop.request'),
            subtitles=str(target), subtitles_sha256=hash_file(target), **draft_binding(target, self.f.root))
        self.assertEqual(result['status'], 'completed')
        self.f.asr.assert_not_called()
        self.f.translate.assert_not_called()
        self.assertEqual(self.f.tts.call_args.args[0][0]['text'], 'Lời tôi đã sửa.')
        self.assertEqual(read_vietnamese_srt(result['outputs']['subtitles'])[1]['text'], 'Tự nhiên hơn nhé!')
        self.assertEqual(hash_file(original), digest)

    def test_wrong_source_and_changed_srt_stop_before_tts(self):
        srt = self.f.root/'custom.srt'
        write_srt(srt, self.f.cues)
        for index, kwargs in enumerate([{'dialogue_source_sha256': 'f'*64}, {'subtitles_sha256': 'f'*64}]):
            job = self.f.root/f'bad-{index}'
            result = self.f.run_job(directory=str(job), stop_file=str(job/'stop.request'), subtitles=str(srt), **kwargs)
            self.assertEqual(result['status'], 'failed')
        self.f.tts.assert_not_called()
        self.f.render.assert_not_called()


class DialogueUITests(unittest.TestCase):
    def setUp(self):
        self.f = test_campaign_ui.CampaignUITests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root, self.studio = self.f.root, self.f.studio
        self.folder = Path(self.f.temp.name)
        self.path = self.folder/'words.srt'
        write_srt(self.path, [dict(start_ms=0, end_ms=500, text='Xin chào.'),
                             dict(start_ms=600, end_ms=950, text='Tạm biệt.')])
        self.studio.input.insert('1.0', 'https://example.com/video')
        self.studio.fields['original_dir'].set(str(self.folder/'original'))
        self.studio.fields['output_dir'].set(str(self.folder/'output'))
        self.enterContext(patch('teodub.app.winsound.PlaySound'))
        self.spawn = self.enterContext(patch('teodub.app.subprocess.Popen'))

    def test_no_srt_editor_does_not_start_translation(self):
        self.studio.fields['reference_audio'].set('missing')
        with patch.object(self.studio, 'voice_selection', side_effect=AssertionError('no TTS needed')), \
                patch.object(self.studio, 'launch') as launch:
            self.studio.edit_dialogue()
        launch.assert_not_called()
        self.assertIn('Tải video và dịch Sub', self.studio.status.get())

    def test_edit_navigation_save_last_sentence_and_full_config_use_draft(self):
        self.studio.fields['subtitles'].set(str(self.path))
        self.studio.edit_dialogue()
        editor = self.studio.dialogue_editor
        editor.edit.delete('1.0', 'end')
        editor.edit.insert('1.0', 'Xin chào cả nhà!')
        editor.move(1)
        editor.edit.delete('1.0', 'end')
        editor.edit.insert('1.0', 'Hẹn gặp lại.')
        editor.save()
        selected = self.studio.fields['subtitles'].get()
        self.assertNotEqual(selected, str(self.path))
        self.assertEqual([c['text'] for c in read_vietnamese_srt(selected)], ['Xin chào cả nhà!', 'Hẹn gặp lại.'])
        config = self.studio.config_from_form('full')
        self.assertEqual(config['subtitles'], selected)
        self.assertEqual(config['subtitles_sha256'], hash_file(selected))
        self.spawn.assert_not_called()

    def test_error_keeps_editor_and_unsaved_close_can_be_cancelled(self):
        callback = Mock()
        editor = DialogueEditor(self.root, self.path, self.folder, callback)
        editor.edit.delete('1.0', 'end')
        editor.save()
        self.assertIn('Câu 1', editor.status.get())
        callback.assert_not_called()
        with patch('teodub.dialogue_editor.messagebox.askyesno', return_value=False):
            editor.close()
        self.assertTrue(editor.winfo_exists())
        editor.destroy()

    def owned_captions(self):
        config = self.studio.config_from_form('captions')
        directory = Path(config['directory'])
        directory.mkdir(parents=True)
        source = self.folder/'downloaded.mp4'
        source.write_bytes(b'fixture')
        prepared = directory/'captions'/'vietnamese.srt'
        prepared.parent.mkdir()
        prepared.write_bytes(self.path.read_bytes())
        self.path = prepared
        atomic_json(directory/'config.json', config)
        atomic_json(directory/'state.json', {'source': {'path': str(source), 'sha256': hash_file(source)},
            'captions': {'path': str(self.path), 'sha256': hash_file(self.path), 'count': 2}})
        self.studio.current = config
        return config, source

    def test_completed_caption_job_selects_words_and_waits_for_next_step(self):
        config, source = self.owned_captions()
        with patch.object(self.studio, 'read_result', return_value={'status': 'completed', 'outputs': {'subtitles': str(self.path)}}):
            self.studio.complete()
        self.assertFalse(hasattr(self.studio, 'dialogue_editor'))
        self.assertEqual(self.studio.fields['subtitles'].get(), str(self.path))
        self.studio.edit_dialogue()
        self.studio.dialogue_editor.save()
        self.assertEqual(self.studio.input.get('1.0', 'end').strip(), str(source))
        full = self.studio.config_from_form('full')
        self.assertEqual(full['dialogue_source_sha256'], hash_file(source))
        self.assertEqual(self.studio.current, config)

    def test_new_input_does_not_reuse_previous_job_captions(self):
        self.owned_captions()
        self.studio.input.delete('1.0', 'end')
        self.studio.input.insert('1.0', 'another.mp4')
        with patch.object(self.studio, 'start') as start:
            self.studio.edit_dialogue()
        start.assert_not_called()
        self.assertIn('Chưa có lời Việt', self.studio.status.get())

    def test_draft_selection_survives_restore_over_old_job(self):
        config, source = self.owned_captions()
        atomic_json(self.folder/'last-job.json', {'directory': config['directory']})
        self.studio.edit_dialogue()
        self.studio.dialogue_editor.save()
        draft = self.studio.fields['subtitles'].get()
        self.studio.restore()
        self.assertEqual(self.studio.fields['subtitles'].get(), draft)
        self.assertEqual(self.studio.input.get('1.0', 'end').strip(), str(source))

    def test_busy_button_disabled_and_no_editor_or_worker_started(self):
        self.studio.campaign_busy = True
        self.studio.refresh_buttons()
        self.assertEqual(str(self.studio.dialogue_button['state']), 'disabled')
        self.studio.edit_dialogue()
        self.spawn.assert_not_called()
        self.assertFalse(hasattr(self.studio, 'dialogue_editor'))
