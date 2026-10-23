"""Synthetic, local FFmpeg integration tests; no accounts or user footage."""
import itertools
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

import imageio_ffmpeg
import numpy as np

from teodub.common import Cancelled, hash_file
from teodub import render


def source_video(path, audio=True):
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
               '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=12:duration=1.5']
    if audio:
        command += ['-f', 'lavfi', '-i', 'sine=frequency=170:sample_rate=48000:duration=1.5']
    command += ['-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p']
    if audio:
        command += ['-c:a', 'aac']
    command += ['-t', '1.5', str(path)]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                   stdin=subprocess.DEVNULL, timeout=30,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def tone_clip(path, index, duration=.72, rate=22050):
    t = np.arange(round(duration*rate), dtype=np.float64)/rate
    envelope = np.minimum(1, t/.025)*np.minimum(1, (duration-t)/.025)
    # A changing pitch and envelope avoids periodic false matches in voice QC.
    signal = .23*np.sin(2*np.pi*((470+index*170)*t+90*t*t))
    signal += .08*np.sin(2*np.pi*(1190+index*61)*t)
    data = np.rint(32767*signal*envelope).astype('<i2')
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        output.writeframes(data.tobytes())
    return dict(id=index, path=str(path), sha256=hash_file(path),
                duration_s=len(data)/rate, sample_rate=rate)


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory(prefix='teodub-render-test-')
        cls.base = Path(cls.shared.name)
        cls.source = cls.base/'nguồn 钓鱼.mp4'
        cls.silent_source = cls.base/'nguồn không âm.mp4'
        source_video(cls.source)
        source_video(cls.silent_source, audio=False)
        cls.clips = [tone_clip(cls.base/f'giọng {index}.wav', index) for index in range(1, 4)]
        cls.cues = [dict(start_ms=0, end_ms=300, text='Xin chào mọi người.'),
                    dict(start_ms=200, end_ms=700, text='Chúng ta thử tiếng Việt.'),
                    dict(start_ms=600, end_ms=1400, text='Đọc đủ cả ba câu nhé.')]

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='case-', dir=self.base)
        self.directory = Path(self.temporary.name)
        self.output = self.directory/'kết quả lồng tiếng'

    def tearDown(self):
        self.temporary.cleanup()

    def perform(self, **kwargs):
        return render.render_video(kwargs.pop('source', self.source), kwargs.pop('cues', self.cues),
                                   kwargs.pop('clips', self.clips), self.output, **kwargs)

    def test_full_audio_is_serialized_and_picture_is_extended(self):
        before = hash_file(self.source)
        progress = []
        paths = self.perform(progress=lambda message, percent=None: progress.append(percent))
        self.assertEqual(set(paths), {'video', 'subtitles', 'plan', 'quality'})
        self.assertTrue(all(Path(value).is_absolute() and Path(value).is_file() for value in paths.values()))
        self.assertEqual(before, hash_file(self.source))
        self.assertEqual(set(path.name for path in self.output.iterdir()),
                         {f'{self.output.name}.mp4', f'{self.output.name}.srt', 'render-plan.json', 'quality.json'})
        plan = json.loads(Path(paths['plan']).read_text(encoding='utf-8'))
        quality = json.loads(Path(paths['quality']).read_text(encoding='utf-8'))
        self.assertLess(plan['effective_picture_speed'], .9)
        self.assertEqual(plan['original_gain'], .1)
        self.assertEqual(plan['voice_gain'], 1)
        self.assertFalse(plan['source_embedded_text_removed'])
        self.assertEqual(plan['subtitle_mode'], 'soft_mov_text')
        for index, item in enumerate(plan['clips']):
            self.assertAlmostEqual(item['frames']/render.RATE, self.clips[index]['duration_s'], places=4)
            self.assertEqual(item['end_frame']-item['start_frame'], item['frames'])
            if index:
                self.assertGreaterEqual(item['start_frame']-plan['clips'][index-1]['end_frame'], 4800)
        self.assertGreaterEqual(plan['duration_s'], plan['clips'][-1]['end_frame']/render.RATE+.2)
        self.assertTrue(quality['technical_pass'])
        self.assertEqual(quality['cue_count'], 3)
        self.assertEqual(len(quality['voice_checks']), 3)
        self.assertTrue(all(check['correlation']>.9 for check in quality['voice_checks']))
        self.assertTrue(all(.85<check['relative_gain']<1.15 for check in quality['voice_checks']))
        self.assertEqual(quality['video_sha256'], hash_file(paths['video']))
        self.assertEqual(quality['render_plan_sha256'], hash_file(paths['plan']))
        srt = Path(paths['subtitles']).read_text(encoding='utf-8')
        self.assertEqual(srt.count(' --> '), 3)
        for cue in self.cues:
            self.assertIn(cue['text'], srt)
        self.assertEqual(progress[-1], 100)

    def test_source_without_audio_still_contains_every_voice(self):
        paths = self.perform(source=self.silent_source)
        quality = json.loads(Path(paths['quality']).read_text(encoding='utf-8'))
        self.assertEqual(quality['cue_count'], 3)
        self.assertTrue(all(item['correlation']>.9 for item in quality['voice_checks']))

    def test_burned_yellow_regular_subtitles_are_visible_mode_without_mp4_text_track(self):
        import av
        paths = self.perform(burn_subtitles=True, subtitle_color='yellow', subtitle_weight='regular')
        plan = json.loads(Path(paths['plan']).read_text(encoding='utf-8'))
        quality = json.loads(Path(paths['quality']).read_text(encoding='utf-8'))
        self.assertEqual(plan['subtitle_mode'], 'burned_ass')
        self.assertEqual(plan['subtitle_style'], {'color': 'yellow', 'weight': 'regular', 'size': 13})
        self.assertEqual(quality['subtitle_mode'], 'burned_ass')
        self.assertEqual(quality['cue_count'], len(self.cues))
        with av.open(paths['video']) as video:
            self.assertEqual(len(video.streams.subtitles), 0)
        self.assertEqual(Path(paths['subtitles']).read_text(encoding='utf-8').count(' --> '), len(self.cues))

    def test_story_pauses_are_measured_and_do_not_overlap(self):
        paths = self.perform(gap_ms=300)
        plan = json.loads(Path(paths['plan']).read_text(encoding='utf-8'))
        self.assertEqual(plan['gap_ms'], 300)
        for previous, current in zip(plan['clips'], plan['clips'][1:]):
            self.assertGreaterEqual(current['start_frame']-previous['end_frame'], round(render.RATE*.3))
        self.assertEqual(hash_file(self.source), plan['source_sha256'])

    def test_font_size_changes_actual_burned_glyph_area(self):
        import av
        background = self.directory/'black.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-n',
                        '-f', 'lavfi', '-i', 'color=c=black:s=320x240:r=12:d=1.5',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(background)],
                       check=True, capture_output=True, timeout=30)
        areas = []
        for size in (8, 24):
            paths = render.render_video(background, [dict(start_ms=0, end_ms=1000, text='Xin chào')],
                self.clips[:1], self.directory/f'size-{size}', burn_subtitles=True, subtitle_size=size)
            plan = json.loads(Path(paths['plan']).read_text(encoding='utf-8'))
            self.assertEqual(plan['subtitle_style']['size'], size)
            with av.open(paths['video']) as video:
                frame = next(video.decode(video=0)).to_ndarray(format='rgb24')
            areas.append(int(np.sum(np.all(frame > 210, axis=2))))
        self.assertGreater(areas[1], areas[0]*1.5)

    def own_caption_source(self):
        """A moving picture with a long white (Chinese) caption with a black edge burned in for 3.4 seconds."""
        own = self.directory/'own captions.srt'
        own.write_text('1\n00:00:00,000 --> 00:00:03,400\n这里的东西真的很便宜啊我的朋友们\n\n', encoding='utf-8')
        style = ('FontName=Microsoft YaHei,FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,'
                 'BorderStyle=1,Outline=1.5,Shadow=0,Bold=-1,Alignment=2,MarginV=14')
        def burn(target, background):
            escaped = str(own).replace('\\', '/').replace(':', '\\:')
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-n', '-f', 'lavfi',
                            '-i', background, '-vf', f"subtitles=filename='{escaped}':original_size=640x360:force_style='{style}'",
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(target)], check=True, capture_output=True, timeout=60)
        source, truth = self.directory/'own caption.mp4', self.directory/'own caption alone.mp4'
        burn(source, 'testsrc2=size=640x360:rate=25:duration=4.5')
        burn(truth, 'color=c=black:s=640x360:r=25:d=4.5')
        return source, truth

    @staticmethod
    def frames_of(path):
        import av
        with av.open(str(path)) as video:
            return {frame.time: frame.to_ndarray(format='rgb24') for frame in video.decode(video=0)}

    def test_boxed_captions_hide_the_videos_own_caption_snugly_and_only_while_spoken(self):
        source, truth = self.own_caption_source()
        cues = [dict(start_ms=0, end_ms=700, text='Rẻ thật.'), dict(start_ms=1100, end_ms=1800, text='Mua đi.'),
                dict(start_ms=2200, end_ms=2900, text='Ngon lắm.')]
        paths = render.render_video(source, cues, self.clips, self.directory/'boxed', speed=1.,
                                    burn_subtitles=True, subtitle_box='white')
        plan = json.loads(Path(paths['plan']).read_text(encoding='utf-8'))
        self.assertEqual(plan['subtitle_style'], {'color': 'white', 'weight': 'bold', 'size': 13, 'box': 'white'})
        self.assertEqual(plan['caption_cover']['seen'], 3)
        output, original = self.frames_of(paths['video']), self.frames_of(source)
        def at(frames, seconds):
            return frames[min(frames, key=lambda moment: abs(moment-seconds))]
        rows, columns = np.nonzero(at(self.frames_of(truth), .35).min(axis=2) > 150)
        top, bottom, left, right = rows.min(), rows.max()+1, columns.min(), columns.max()+1
        middle = (left+right)//2
        def edge_pixels(frame):  # The black edge of the own caption's two ends, beside the short Vietnamese line.
            ends = np.concatenate([frame[top:bottom, left:middle-90], frame[top:bottom, middle+90:right]], axis=1)
            return int(np.sum(ends.max(axis=2) < 50))
        self.assertGreater(edge_pixels(at(original, .35)), 50)
        self.assertLess(edge_pixels(at(output, .35)), 3)       # Hidden while the Vietnamese line is shown...
        self.assertGreater(edge_pixels(at(output, 3.2)), 50)   # ...and untouched when nobody speaks.
        sides = np.concatenate([at(output, .35)[top:bottom, :16], at(output, .35)[top:bottom, -16:]], axis=1)
        self.assertLess(float(np.mean(sides.min(axis=2) > 235)), .05)  # Snug: no band across the whole picture.

    def test_a_picture_that_cannot_be_analysed_falls_back_to_the_wide_band(self):
        source, _truth = self.own_caption_source()
        with patch('teodub.caption_cover.cover_boxes', side_effect=RuntimeError('decoder trouble')):
            paths = render.render_video(source, [dict(start_ms=0, end_ms=700, text='Rẻ thật.')], self.clips[:1],
                                        self.directory/'fallback', speed=1., burn_subtitles=True, subtitle_box='white')
        plan = json.loads(Path(paths['plan']).read_text(encoding='utf-8'))
        self.assertEqual(plan['caption_cover']['fallback'], 'wide_band')
        frame = self.frames_of(paths['video'])
        first = frame[min(frame)]
        self.assertGreater(float(np.mean(first[320:345, 40:600].min(axis=2) > 235)), .6)  # The wide band is there.

    def test_start_positions_keep_a_later_original_cue(self):
        import av
        cue = dict(start_ms=1000, end_ms=1400, text='Bắt đầu muộn.')
        clip = dict(self.clips[0])
        paths = self.perform(cues=[cue], clips=[clip], speed=1.)
        plan = json.loads(Path(paths['plan']).read_text(encoding='utf-8'))
        self.assertEqual(plan['clips'][0]['start_frame'], 48000)
        self.assertEqual(plan['cues'][0]['start_ms'], 1000)
        def source_rms(path):
            values = []
            resampler = av.AudioResampler(format='fltp', layout='stereo', rate=48000)
            with av.open(str(path)) as container:
                for frame in container.decode(audio=0):
                    for normalized in resampler.resample(frame):
                        if .2 <= normalized.time < .6:
                            values.extend(normalized.to_ndarray().reshape(-1))
            return float(np.sqrt(np.mean(np.asarray(values, dtype=np.float64)**2)))
        # Before narration starts, the original sound really is reduced to 10%.
        ratio = source_rms(paths['video'])/source_rms(self.source)
        self.assertGreater(ratio, .08)
        self.assertLess(ratio, .12)

    def test_existing_output_directory_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output/'teo-dub.mp4'
        sentinel.write_bytes(b'keep this')
        with self.assertRaises(FileExistsError):
            self.perform()
        self.assertEqual(sentinel.read_bytes(), b'keep this')

    def test_missing_and_duplicate_clip_ids_are_rejected(self):
        for clips in (self.clips[:-1], [self.clips[0], self.clips[0], self.clips[2]]):
            with self.subTest(clips=clips), self.assertRaises(ValueError):
                self.perform(clips=clips)
            self.assertFalse(self.output.exists())

    def test_invalid_vietnamese_or_timestamps_rejected(self):
        for replacement in (dict(text=None), dict(text='中国'), dict(text='  '),
                            dict(start_ms=-1), dict(end_ms=0), dict(start_ms=1.5), dict(end_ms=9000)):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                self.perform(cues=[dict(self.cues[0], **replacement), *self.cues[1:]])
            self.assertFalse(self.output.exists())

    def test_changed_clip_hash_rejected_before_export(self):
        clips = [dict(self.clips[0], sha256='0'*64), *self.clips[1:]]
        with self.assertRaisesRegex(ValueError, 'thay đổi'):
            self.perform(clips=clips)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_wrong_real_duration_rejected(self):
        clips = [dict(self.clips[0], duration_s=.1), *self.clips[1:]]
        with self.assertRaisesRegex(ValueError, 'Thời lượng thực'):
            self.perform(clips=clips)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_wrong_sample_rate_rejected(self):
        clips = [dict(self.clips[0], sample_rate=24000), *self.clips[1:]]
        with self.assertRaisesRegex(ValueError, 'Tần số'):
            self.perform(clips=clips)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_silent_tts_file_is_rejected(self):
        silent = self.directory/'giọng lỗi.wav'
        with wave.open(str(silent), 'wb') as file:
            file.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            file.writeframes(bytes(24000*2))
        clip = dict(id=1, path=str(silent), sha256=hash_file(silent), duration_s=1, sample_rate=24000)
        with self.assertRaisesRegex(ValueError, 'không có âm thanh rõ'):
            self.perform(cues=[self.cues[0]], clips=[clip])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_cancelled_before_work_creates_nothing(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            self.perform(cancel=cancel)
        self.assertFalse(self.output.exists())

    def test_cancelled_after_normalization_does_not_publish(self):
        cancel = threading.Event()
        def progress(message, percent=None):
            if percent == 20:
                cancel.set()
        with self.assertRaises(Cancelled):
            self.perform(cancel=cancel, progress=progress)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_quality_gate_detects_missing_voice_in_real_encoded_video(self):
        def silence(path, placements, duration_frames, cancel):
            with wave.open(str(path), 'wb') as file:
                file.setparams((2, 2, render.RATE, 0, 'NONE', 'not compressed'))
                file.writeframes(bytes(duration_frames*4))
        with patch.object(render, '_write_narration', side_effect=silence):
            with self.assertRaisesRegex(ValueError, 'giọng câu'):
                self.perform()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_quality_gate_detects_black_picture_in_real_encoded_video(self):
        real_run = render._run_ffmpeg
        def black_picture(command, directory, duration, cancel, progress):
            filters = Path(command[command.index('-filter_complex_script')+1])
            text = filters.read_text(encoding='utf-8')
            filters.write_text(text.replace('[0:v:0]', '[0:v:0]drawbox=color=black:t=fill,'), encoding='utf-8')
            return real_run(command, directory, duration, cancel, progress)
        with patch.object(render, '_run_ffmpeg', side_effect=black_picture):
            with self.assertRaisesRegex(ValueError, 'bị đen'):
                self.perform()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_source_changed_during_render_blocks_publication(self):
        source = self.directory/'source.mp4'
        source.write_bytes(self.source.read_bytes())
        real_verify = render._verify
        def change_after_verify(*args, **kwargs):
            result = real_verify(*args, **kwargs)
            with source.open('ab') as file:
                file.write(b'changed')
            return result
        with patch.object(render, '_verify', side_effect=change_after_verify):
            with self.assertRaisesRegex(ValueError, 'thay đổi trong lúc dựng'):
                self.perform(source=source)
        self.assertEqual(list(self.output.iterdir()), [])


class SubprocessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='teodub-process-test-')
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_cancel_terminates_the_started_encoder_without_shell(self):
        cancel = threading.Event()
        class Process:
            returncode = None
            terminated = False
            def poll(self):
                cancel.set()
                return self.returncode
            def terminate(self):
                self.terminated = True
                self.returncode = 0
            def wait(self, timeout=None):
                return self.returncode
        process = Process()
        with patch.object(render.subprocess, 'Popen', return_value=process) as launch:
            with self.assertRaises(Cancelled):
                render._run_ffmpeg(['ffmpeg.exe', 'a.mp4'], self.directory, 1, cancel, None)
        self.assertTrue(process.terminated)
        arguments, options = launch.call_args
        self.assertIsInstance(arguments[0], list)
        self.assertNotIn('shell', options)
        self.assertEqual(options['stdin'], subprocess.DEVNULL)

    def test_encoder_timeout_terminates_then_kills_if_needed(self):
        class Process:
            returncode = None
            terminated = killed = False
            def poll(self):
                return self.returncode
            def terminate(self):
                self.terminated = True
            def kill(self):
                self.killed = True
                self.returncode = -9
            def wait(self, timeout=None):
                if self.returncode is None:
                    raise subprocess.TimeoutExpired('ffmpeg.exe', timeout)
                return self.returncode
        process = Process()
        # Awake time grows in steady 30 s polls (a single long jump is sleep, see test_sleep_safe).
        with patch.object(render.subprocess, 'Popen', return_value=process):
            with patch.object(render.time, 'monotonic', side_effect=itertools.count(0, 30)), \
                    patch.object(render.time, 'sleep'):
                with self.assertRaisesRegex(ValueError, 'quá thời gian chờ'):
                    render._run_ffmpeg(['ffmpeg.exe', 'a.mp4'], self.directory, 1, None, None)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)


if __name__ == '__main__':
    unittest.main()
