"""Inpainting contracts and real FFmpeg source-map verification; no model calls."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from teodub.common import Cancelled, atomic_json, hash_file
from teodub.subtitle_clean import ENGINE, MODEL_SHA, clean_video, validate_clean

SETTINGS = dict(enabled=True, engine=ENGINE, region=[.2, .71, .8, .76])


class CleanConfigTests(unittest.TestCase):
    def test_old_jobs_are_off(self):
        self.assertEqual(validate_clean({}), {'enabled': False})

    def test_invalid_masks_are_rejected(self):
        for region in (None, [0, 0, 1, 1], [.5, .3, .2, .4], [0, 0, float('nan'), 1],
                       [-.1, .7, .9, .8], [True, .7, .9, .8]):
            with self.subTest(region=region), self.assertRaises(ValueError):
                validate_clean({'subtitle_clean': dict(enabled=True, region=region)})

    def test_no_silent_engine_switch(self):
        with self.assertRaises(ValueError):
            validate_clean({'subtitle_clean': dict(SETTINGS, engine='something-else')})

    def test_complete_cache_bound_to_source_settings_and_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, cached = root/'source.mp4', root/'picture.mkv'
            source.write_bytes(b'source remains untouched')
            cached.write_bytes(b'checked model output')
            config = dict(subtitle_clean=SETTINGS)
            binding = dict(source_sha256=hash_file(source), model_sha256=MODEL_SHA,
                           settings=validate_clean(config))
            atomic_json(root/'clean.json', dict(binding=binding, sha256=hash_file(cached)))
            with patch('teodub.subtitle_clean.subprocess.Popen') as spawn:
                self.assertEqual(clean_video(source, config, root)['path'], str(cached.resolve()))
                spawn.assert_not_called()
                for mutate in ('settings', 'output', 'source'):
                    if mutate == 'settings':
                        bad = dict(subtitle_clean=dict(SETTINGS, region=[.2, .72, .8, .76]))
                    else:
                        bad = config
                        (cached if mutate == 'output' else source).write_bytes(b'changed')
                    with self.assertRaises(ValueError):
                        clean_video(source, bad, root)

    def test_cancel_before_start_and_off_never_calls_gpu(self):
        with patch('teodub.subtitle_clean.subprocess.Popen') as spawn:
            self.assertIsNone(clean_video('absent', {}, 'absent'))
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(Cancelled):
                clean_video('absent', {'subtitle_clean': SETTINGS}, 'absent', cancel=cancel)
            spawn.assert_not_called()

    def test_cancel_running_worker_terminates_it_without_publishing(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'source.mp4'
            source.write_bytes(b'original')
            cancel = threading.Event()
            process = Mock()
            def poll():
                cancel.set()
                return None
            process.poll.side_effect = poll
            with patch('teodub.subtitle_clean.runtime_ready'), \
                    patch('teodub.subtitle_clean.subprocess.Popen', return_value=process):
                with self.assertRaises(Cancelled):
                    clean_video(source, {'subtitle_clean': SETTINGS}, root/'cache', cancel=cancel)
            process.terminate.assert_called_once()
            process.wait.assert_called_once()
            self.assertFalse((root/'cache'/'clean.json').exists())
            self.assertFalse((root/'cache'/'picture.mkv').exists())
            self.assertEqual(source.read_bytes(), b'original')


class CleanRenderTests(unittest.TestCase):
    def test_real_render_uses_clean_picture_and_original_audio_both_sub_modes(self):
        from tests.test_render import source_video, tone_clip
        from teodub.render import render_video
        import av
        import numpy as np
        import subprocess
        import imageio_ffmpeg
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, picture = root/'source.mp4', root/'picture.mkv'
            source_video(source)
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-n',
                '-f', 'lavfi', '-i', 'color=c=green:s=96x64:r=12:d=1.5', '-c:v', 'ffv1', str(picture)],
                check=True, capture_output=True, timeout=20)
            before = hash_file(source)
            record = dict(path=str(picture), sha256=hash_file(picture), binding=dict(source_sha256=before,
                         model_sha256=MODEL_SHA, settings=SETTINGS))
            clips = [tone_clip(root/'voice.wav', 1)]
            cues = [dict(start_ms=0, end_ms=1400, text='Xin chào mọi người.')]
            for burn in (False, True):
                with self.subTest(burn=burn):
                    outputs = render_video(source, cues, clips, root/f'output-{burn}',
                                           cleaned_picture=record, burn_subtitles=burn)
                    with av.open(outputs['video']) as video:
                        pixels = next(video.decode(video=0)).to_ndarray(format='rgb24')
                    self.assertGreater(np.mean(pixels[:20, :, 1]), np.mean(pixels[:20, :, 0])+60)
                    quality = json.loads(Path(outputs['quality']).read_text(encoding='utf8'))
                    self.assertTrue(quality['technical_pass'])
                    self.assertEqual(quality['source_sha256'], before)
                    plan = json.loads(Path(outputs['plan']).read_text(encoding='utf8'))
                    self.assertEqual(plan['source_text_removal_scope'], 'selected_rectangle_only')
                    self.assertEqual(plan['subtitle_clean']['binding']['model_sha256'], MODEL_SHA)
                    self.assertEqual(plan['original_gain'], .1)
                    self.assertEqual(hash_file(source), before)
            record['binding']['source_sha256'] = 'wrong'
            with self.assertRaisesRegex(ValueError, 'không khớp'):
                render_video(source, cues, clips, root/'invalid', cleaned_picture=record)
            self.assertFalse((root/'invalid').exists())


if __name__ == '__main__':
    unittest.main()
