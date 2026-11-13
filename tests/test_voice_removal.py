"""Bỏ giọng nói gốc, giữ nhạc nền và tiếng động (owner 13/11/2026): the setting, the frozen result of the separation, and the export that
mixes the Vietnamese voice over the sound without the voices. The AI itself is not run here (a fake worker stands in); the export uses
real local FFmpeg fixtures. No network, Chrome or AI."""
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

import av
import numpy as np

from teodub import render, voice_removal
from teodub.audio_mix import validate_audio_mix, validate_voice_removal
from teodub.campaign import CONFIG_KEYS
from teodub.campaign_preview import describe_pipeline_settings
from teodub.campaign_settings import made_with, made_with_everything, validate_settings
from teodub.common import Cancelled, hash_file
from tests import test_campaign_link_tabs as fixtures
from tests.test_campaign_rotation import SETTINGS
from tests.test_render import source_video, tone_clip


def tone_wav(path, hz, seconds=1.5, rate=44100, level=.3):
    t = np.arange(round(seconds*rate))/rate
    data = np.rint(32767*level*np.sin(2*np.pi*hz*t)).astype('<i2')
    with wave.open(str(path), 'wb') as output:
        output.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
        output.writeframes(np.repeat(data[:, None], 2, axis=1).tobytes())


def band(path, hz, width=12):
    """Energy of the sound of an MP4 around `hz`."""
    pieces = []
    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format='fltp', layout='mono', rate=48000)
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                pieces.append(converted.to_ndarray()[0])
    signal = np.concatenate(pieces)
    spectrum = np.abs(np.fft.rfft(signal))**2
    frequencies = np.fft.rfftfreq(len(signal), 1/48000)
    return float(spectrum[(frequencies > hz-width) & (frequencies < hz+width)].sum())


class SettingTests(unittest.TestCase):
    def test_default_is_off_and_only_a_real_boolean_is_accepted(self):
        self.assertEqual(validate_voice_removal({}), dict(remove_original_voice=False))
        self.assertEqual(validate_voice_removal(dict(remove_original_voice=True)), dict(remove_original_voice=True))
        for bad in (1, 0, 'true', None, [], {}):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                validate_voice_removal(dict(remove_original_voice=bad))

    def test_the_two_levels_are_unchanged_by_the_new_setting(self):
        self.assertEqual(validate_audio_mix(dict(remove_original_voice=True)), dict(original_gain=.1, voice_gain=1.))

    def test_a_link_keeps_the_choice_and_it_counts_as_a_change_for_videos_made_before(self):
        self.assertIn('remove_original_voice', CONFIG_KEYS)
        base = dict(SETTINGS, voice='Mai Anh')
        on = validate_settings(dict(base, remove_original_voice=True))
        off = validate_settings(base)
        self.assertTrue(on['remove_original_voice'])
        self.assertFalse(off.get('remove_original_voice', False))
        self.assertFalse(made_with(off, on))     # A finished video made without it is not "the same" as the link now.
        self.assertTrue(made_with(on, on))
        self.assertFalse(made_with_everything(off, on))
        self.assertTrue(made_with(off, base))    # An old config with no key still equals "off".

    def test_the_applied_config_column_text_says_so_only_when_it_is_on(self):
        base = dict(validate_settings(SETTINGS))
        self.assertNotIn('Bỏ giọng nói gốc', describe_pipeline_settings(base))
        self.assertIn('Bỏ giọng nói gốc', describe_pipeline_settings(dict(base, remove_original_voice=True)))


class FakeProcess:
    """Stands in for the worker: writes what it would write, or fails."""
    fail = False
    calls = []

    def __init__(self, command, stdout=None, stderr=None, cwd=None, creationflags=0):
        request = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
        type(self).calls.append(request)
        self.returncode = 3 if self.fail else 0
        if self.fail:
            stdout.write('Mô hình lỗi giả lập')
            return
        tone_wav(request['target'], 900)
        Path(request['status']).write_text(json.dumps(dict(status='completed', percent=100, seconds=1.5)), encoding='utf-8')

    def poll(self):
        return self.returncode

    def terminate(self):
        pass

    def wait(self, timeout=None):
        pass


class SeparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source.mp4'
        source_video(self.source)
        self.model = self.root/'model.th'
        self.model.write_bytes(b'model')
        self.python = self.root/'python.exe'
        self.python.write_bytes(b'')
        for patcher in (patch.object(voice_removal, 'MODEL', self.model), patch.object(voice_removal, 'PYTHON', self.python),
                        patch.object(voice_removal, 'MODEL_SHA', hash_file(self.model)),
                        patch.object(voice_removal, 'subprocess', SimpleNamespace(Popen=FakeProcess, run=subprocess.run, CREATE_NO_WINDOW=0))):
            patcher.start()
            self.addCleanup(patcher.stop)
        FakeProcess.fail, FakeProcess.calls = False, []

    def test_not_installed_and_a_changed_model_are_refused_with_a_clear_message(self):
        self.assertTrue(voice_removal.installed())
        self.python.unlink()
        self.assertFalse(voice_removal.installed())
        with self.assertRaisesRegex(ValueError, 'Chưa cài bộ tách giọng'):
            voice_removal.runtime_ready()
        self.python.write_bytes(b'')
        self.model.write_bytes(b'another model')
        with self.assertRaisesRegex(ValueError, 'Mô hình tách giọng bị thay đổi'):
            voice_removal.runtime_ready()

    def test_a_separation_is_frozen_with_its_binding_and_reused_without_running_again(self):
        result = voice_removal.separate(self.source, self.root/'job'/'voice-removal')
        path = Path(result['path'])
        self.assertTrue(path.is_file())
        self.assertEqual(result['sha256'], hash_file(path))
        self.assertEqual(result['binding'], dict(source_sha256=hash_file(self.source), engine=voice_removal.ENGINE,
                                                 model_sha256=voice_removal.MODEL_SHA))
        self.assertEqual(len(FakeProcess.calls), 1)
        self.assertEqual(FakeProcess.calls[0]['model_sha256'], voice_removal.MODEL_SHA)  # The worker is told which model is allowed.
        again = voice_removal.separate(self.source, self.root/'job'/'voice-removal')
        self.assertEqual((again['path'], again['sha256']), (result['path'], result['sha256']))
        self.assertEqual(len(FakeProcess.calls), 1)
        leftovers = sorted(item.name for item in (self.root/'job'/'voice-removal').iterdir() if item.suffix in ('.partial', '.json')
                           and item.name != 'background.json')
        self.assertEqual(leftovers, [])  # No request or half-made file stays.
        self.assertEqual(voice_removal.verify_background(result, hash_file(self.source))['sha256'], result['sha256'])

    def test_a_failed_run_leaves_no_result_and_says_why(self):
        FakeProcess.fail = True
        with self.assertRaisesRegex(ValueError, 'Mô hình lỗi giả lập'):
            voice_removal.separate(self.source, self.root/'job'/'voice-removal')
        self.assertFalse((self.root/'job'/'voice-removal'/'background.json').exists())
        self.assertFalse((self.root/'job'/'voice-removal'/'background.wav').exists())

    def test_a_stop_request_ends_it_without_a_result(self):
        class Stop:
            def is_set(self):
                return True
        with self.assertRaises(Cancelled):
            voice_removal.separate(self.source, self.root/'job'/'voice-removal', cancel=Stop())
        self.assertEqual(FakeProcess.calls, [])

    def test_a_saved_result_of_another_video_or_a_changed_file_is_never_used(self):
        result = voice_removal.separate(self.source, self.root/'job'/'voice-removal')
        other = self.root/'other.mp4'
        source_video(other, duration=1.0)
        with self.assertRaisesRegex(ValueError, 'không khớp'):
            voice_removal.separate(other, self.root/'job'/'voice-removal')
        with self.assertRaisesRegex(ValueError, 'không khớp'):
            voice_removal.verify_background(result, hash_file(other))
        Path(result['path']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'thay đổi'):
            voice_removal.verify_background(result, hash_file(self.source))
        with self.assertRaisesRegex(ValueError, 'không khớp'):
            voice_removal.separate(self.source, self.root/'job'/'voice-removal')


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory(prefix='teodub-voice-removal-')
        cls.root = Path(cls.shared.name)
        cls.source, cls.silent = cls.root/'source.mp4', cls.root/'silent.mp4'
        source_video(cls.source)                 # Its sound is a 170 Hz tone: the "foreign voice".
        source_video(cls.silent, audio=False)
        cls.stem = cls.root/'background.wav'
        tone_wav(cls.stem, 900)                  # The "music": 900 Hz, what is left once the voice is removed.
        cls.clip = tone_clip(cls.root/'voice.wav', 1)  # The Vietnamese line: about 640 Hz and 1250 Hz.
        cls.cues = [dict(start_ms=100, end_ms=1000, text='Kiểm tra bỏ giọng nói gốc.')]

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='case-', dir=self.root)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.counter = 0

    def background(self, source=None, path=None):
        path = path or self.stem
        return dict(path=str(path), sha256=hash_file(path),
                    binding=dict(source_sha256=hash_file(source or self.source), engine=voice_removal.ENGINE, model_sha256='f'*64))

    def render(self, source=None, **options):
        self.counter += 1
        paths = render.render_video(source or self.source, self.cues, [self.clip], self.directory/str(self.counter), speed=1.,
                                    original_gain=options.pop('original_gain', 1.), voice_gain=1., **options)
        plan = json.loads(Path(paths['plan']).read_text(encoding='utf-8'))
        quality = json.loads(Path(paths['quality']).read_text(encoding='utf-8'))
        self.assertTrue(quality['technical_pass'])
        return paths, plan, quality

    def test_without_the_setting_the_export_is_as_before(self):
        paths, plan, _ = self.render()
        self.assertIsNone(plan['voice_removal'])
        self.assertGreater(band(paths['video'], 170), 1e3)   # The original sound is in it.
        self.assertLess(band(paths['video'], 900), band(paths['video'], 170)*1e-3)

    def test_with_the_setting_the_original_voice_is_gone_and_the_music_and_the_vietnamese_voice_stay(self):
        before, _, _ = self.render()
        paths, plan, quality = self.render(background=self.background())
        self.assertLess(band(paths['video'], 170), band(before['video'], 170)*1e-3)   # The foreign voice is gone.
        self.assertGreater(band(paths['video'], 900), band(before['video'], 170)*.3)  # The music is there at the chosen level.
        self.assertGreater(band(paths['video'], 640, 80), 1e2)                         # The Vietnamese line is there.
        self.assertEqual(plan['voice_removal']['background_sha256'], hash_file(self.stem))
        self.assertTrue(quality['mix_checks'][0]['correlation'] > .99)                 # The independent reference mix agrees.
        self.assertEqual(Path(paths['video']).read_bytes()[:4], Path(before['video']).read_bytes()[:4])

    def test_the_level_slider_is_the_level_of_the_music(self):
        loud, _, _ = self.render(background=self.background(), original_gain=1.)
        quiet, _, _ = self.render(background=self.background(), original_gain=.25)
        self.assertLess(band(quiet['video'], 900), band(loud['video'], 900)*.5)  # (the music still falls under the Vietnamese voice, as before)
        self.assertGreater(band(quiet['video'], 900), 1e2)

    def test_a_background_of_another_video_or_a_changed_one_is_refused(self):
        other = self.root/'other.mp4'
        source_video(other, duration=1.0)
        wrong = self.background(source=other)
        with self.assertRaisesRegex(ValueError, 'không khớp nguồn'):
            self.render(background=wrong)
        changed = self.background()
        tone_wav(self.stem.with_name('moved.wav'), 500)
        changed['path'] = str(self.stem.with_name('moved.wav'))
        with self.assertRaisesRegex(ValueError, 'không khớp nguồn hoặc đã thay đổi'):
            self.render(background=changed)

    def test_a_video_with_no_sound_has_no_voices_to_remove(self):
        paths, plan, _ = self.render(source=self.silent, background=self.background(source=self.silent))
        self.assertIsNone(plan['voice_removal'])
        self.assertLess(band(paths['video'], 900), band(paths['video'], 640, 80)*1e-3)  # Only the Vietnamese voice; no music is added.
        self.assertGreater(band(paths['video'], 640, 80), 1e2)


class WindowTests(unittest.TestCase):
    setUp = fixtures.LinkTabsUITests.setUp
    cleanup = fixtures.LinkTabsUITests.cleanup

    def controls(self, installed):
        import tkinter as tk
        from teodub.audio_mix_ui import AudioMixControls
        fields = dict(original_volume=tk.StringVar(self.root, value='10'), voice_volume=tk.StringVar(self.root, value='100'),
                      remove_original_voice=tk.BooleanVar(self.root, value=False))
        with patch('teodub.voice_removal.installed', return_value=installed):
            return AudioMixControls(self.root, fields), fields

    def test_the_video_don_page_keeps_the_two_levels_and_has_its_own_choice(self):
        self.assertEqual(self.studio.audio_mix_selection(), dict(original_gain=.1, voice_gain=1.))
        self.assertEqual(self.studio.voice_removal_selection(), dict(remove_original_voice=False))
        self.studio.fields['remove_original_voice'].set(True)
        self.assertEqual(self.studio.voice_removal_selection(), dict(remove_original_voice=True))
        self.assertEqual(self.studio.audio_mix_selection(), dict(original_gain=.1, voice_gain=1.))

    def test_loading_a_config_sets_or_clears_the_choice(self):
        self.studio.fill_audio_mix(dict(original_gain=.5, remove_original_voice=True))
        self.assertTrue(self.studio.fields['remove_original_voice'].get())
        self.studio.fill_audio_mix(dict(original_gain=.5))            # An older config has no such choice: off.
        self.assertFalse(self.studio.fields['remove_original_voice'].get())

    def test_the_checkbox_raises_a_low_original_level_once_and_leaves_a_high_one(self):
        widget, fields = self.controls(True)
        self.assertEqual(str(widget.removal_box['state']), 'normal')
        self.assertIn(widget.removal_box, widget.controls)
        fields['remove_original_voice'].set(True)
        widget.removal_clicked()
        self.assertEqual(fields['original_volume'].get(), '70')
        self.assertIn('70%', widget.removal_note['text'])
        fields['original_volume'].set('100')
        widget.removal_clicked()
        self.assertEqual(fields['original_volume'].get(), '100')
        fields['original_volume'].set('20')
        fields['remove_original_voice'].set(False)                     # Un-ticking changes nothing.
        widget.removal_clicked()
        self.assertEqual(fields['original_volume'].get(), '20')

    def test_without_the_tool_the_checkbox_is_off_limits_and_says_how_to_install_it(self):
        widget, _ = self.controls(False)
        self.assertIn('disabled', widget.removal_box.state())
        self.assertNotIn(widget.removal_box, widget.controls)  # The busy-state code never re-enables it.
        self.assertIn('setup_inpaint', widget.removal_note['text'])


class JobTests(unittest.TestCase):
    def setUp(self):
        from teodub.pipeline import Job
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)/'job'
        self.job = Job(dict(directory=str(directory), stop_file=str(directory/'stop.request')))
        self.source = dict(path=str(Path(self.temp.name)/'v.mp4'), sha256='a'*64)

    def test_the_job_makes_it_once_saves_it_and_a_remake_reuses_it(self):
        result = dict(path='x.wav', sha256='b'*64, binding=dict(source_sha256='a'*64, engine=voice_removal.ENGINE, model_sha256=voice_removal.MODEL_SHA))
        with patch('teodub.voice_removal.separate', return_value=result) as separate, \
                patch('teodub.voice_removal.verify_background', side_effect=lambda saved, sha: saved):
            self.assertEqual(self.job.background(self.source), result)
            self.assertEqual(separate.call_count, 1)
            self.assertEqual(self.job.state['background'], result)
            self.assertEqual(json.loads(self.job.state_path.read_text(encoding='utf-8'))['background'], result)
            self.assertEqual(self.job.background(self.source), result)
            self.assertEqual(separate.call_count, 1)  # The saved one is used.

    def test_a_carried_result_that_no_longer_fits_is_made_again_here(self):
        self.job.state['background'] = dict(path='old.wav', sha256='c'*64, binding={})
        fresh = dict(path='new.wav', sha256='d'*64, binding={})
        with patch('teodub.voice_removal.separate', return_value=fresh) as separate:
            self.assertEqual(self.job.background(self.source), fresh)
        separate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
