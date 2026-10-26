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


def source_video(path, audio=True, duration=1.5):
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
               '-f', 'lavfi', '-i', f'testsrc2=size=96x64:rate=12:duration={duration}']
    if audio:
        command += ['-f', 'lavfi', '-i', f'sine=frequency=170:sample_rate=48000:duration={duration}']
    command += ['-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p']
    if audio:
        command += ['-c:a', 'aac']
    command += ['-t', str(duration), str(path)]
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


def syllable_clip(path, index, duration, rate=48000):
    """About four syllables a second on a 140 Hz voice, fading at the end like a read line."""
    t = np.arange(round(duration*rate), dtype=np.float64)/rate
    phase = 2*np.pi*np.cumsum(140+20*np.sin(2*np.pi*1.3*t))/rate
    voice = sum(np.sin(k*phase)/k for k in range(1, 12))
    signal = .2*voice*np.clip(np.sin(2*np.pi*4*t), 0, None)**.5*np.minimum(1, (duration-t)/.15)
    data = np.rint(32767*np.clip(signal, -1, 1)).astype('<i2')
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

    def test_full_audio_is_kept_at_the_chosen_picture_speed_with_the_last_frame_held(self):
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
        # Three 0.72 s lines in 1.5 s of picture: read faster (at most 1.25x), never a slowed picture.
        self.assertEqual(plan['effective_picture_speed'], .9)
        self.assertGreater(plan['duration_s'], 1.5/.9)
        self.assertAlmostEqual(plan['last_frame_held_s'], plan['duration_s']-1.5/.9, places=3)
        self.assertEqual(plan['line_fit'], dict(max_tempo=1.25, catch_up_gap_ms=80, faster_lines=3))
        self.assertEqual(plan['original_gain'], .1)
        self.assertEqual(plan['voice_gain'], 1)
        self.assertIsNone(plan['ducking'])
        self.assertFalse(plan['source_embedded_text_removed'])
        self.assertEqual(plan['subtitle_mode'], 'soft_mov_text')
        for index, item in enumerate(plan['clips']):
            self.assertEqual(item['tempo'], 1.25)
            self.assertAlmostEqual(item['frames']/render.RATE, self.clips[index]['duration_s']/1.25, delta=.01)
            self.assertEqual(item['end_frame']-item['start_frame'], item['frames'])
            if index:
                self.assertGreaterEqual(item['start_frame']-plan['clips'][index-1]['end_frame'], 3840)
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

    def test_story_pause_is_kept_on_time_and_shortened_only_to_catch_up(self):
        cues = [dict(start_ms=0, end_ms=300, text='Xin chào mọi người.'),
                dict(start_ms=1000, end_ms=1100, text='Chúng ta thử tiếng Việt.'),
                dict(start_ms=1100, end_ms=1400, text='Đọc đủ cả ba câu nhé.')]
        paths = self.perform(cues=cues, gap_ms=300, speed=1.)
        plan = json.loads(Path(paths['plan']).read_text(encoding='utf-8'))
        self.assertEqual(plan['gap_ms'], 300)
        first, second, third = plan['clips']
        # Line 1 is 0.72 s for 0.7 s of room (1 s minus the 0.3 s pause): read just a little faster.
        self.assertAlmostEqual(first['tempo'], .72/.7, delta=.001)
        self.assertEqual(second['start_frame'], render.RATE)  # On time: at its own moment, the full pause before it.
        self.assertAlmostEqual((second['start_frame']-first['end_frame'])/render.RATE, .3, delta=.01)
        self.assertEqual(second['tempo'], 1.25)  # Line 3 should start 0.1 s later: as fast as allowed...
        self.assertEqual(third['start_frame']-second['end_frame'], round(render.RATE*.08))  # ...then a short pause.
        self.assertEqual(plan['cues'][1]['start_ms'], 1000)
        self.assertEqual(hash_file(self.source), plan['source_sha256'])

    def test_voice_stays_near_its_picture_through_dense_talk(self):
        source = self.directory/'dense talk.mp4'
        source_video(source, duration=6)
        clips = [tone_clip(self.directory/f'dense {index}.wav', index) for index in range(1, 9)]
        cues = [dict(start_ms=index*600, end_ms=index*600+500, text=f'Câu số {index+1}.') for index in range(8)]
        paths = self.perform(source=source, cues=cues, clips=clips, speed=1.)
        plan = json.loads(Path(paths['plan']).read_text(encoding='utf-8'))
        late = [item['start_frame']/render.RATE-cue['start_ms']/1000 for item, cue in zip(plan['clips'], cues)]
        # Placing 0.72 s lines one after another with 0.1 s pauses would put line 8 1.5 s late; now under 0.5 s.
        self.assertEqual(late[0], 0)
        self.assertLess(max(late), .5)
        self.assertTrue(all(value >= 0 for value in late))
        self.assertEqual(plan['effective_picture_speed'], 1.)
        self.assertEqual(plan['last_frame_held_s'], 0)

    def test_fit_tempo_reads_faster_only_when_needed_and_at_most_the_cap(self):
        for frames, room, expected in ((1000, 1000, 1.), (1000, 2000, 1.), (1015, 1000, 1.),
                                       (1100, 1000, 1.1), (2000, 1000, 1.25), (1000, 0, 1.25), (1000, -500, 1.25)):
            with self.subTest(frames=frames, room=room):
                self.assertAlmostEqual(render._fit_tempo(frames, room), expected)

    def test_stretched_line_keeps_its_pitch(self):
        clip = tone_clip(self.directory/'steady.wav', 1, duration=1.)
        normalized, stretched = self.directory/'steady 48k.wav', self.directory/'steady faster.wav'
        frames, _ = render._normalize_clip(clip, normalized, None)
        written = render._stretch_clip(normalized, stretched, 1.25, None)
        self.assertAlmostEqual(written, frames/1.25, delta=render.RATE*.01)
        def peak(path):
            with wave.open(str(path)) as file:
                data = np.frombuffer(file.readframes(file.getnframes()), '<i2').reshape(-1, 2)[:, 0].astype(float)
            middle = data[len(data)//4:len(data)//4+8192]
            return np.argmax(np.abs(np.fft.rfft(middle*np.hanning(len(middle)))))*render.RATE/len(middle)
        self.assertAlmostEqual(peak(stretched), peak(normalized), delta=25)  # A pitch shift would be ~175 Hz.
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            render._stretch_clip(normalized, self.directory/'cancelled.wav', 1.25, cancel)

    def test_a_short_line_read_a_little_faster_keeps_its_exact_length(self):
        # 24/10: atempo ended a faster line up to ~32 ms early and short lines failed the 20 ms check (6 videos).
        for duration, tempo in ((1.2, 1.05), (1.95, 1.04), (.9, 1.021), (1.46, 1.25)):
            with self.subTest(duration=duration, tempo=tempo):
                clip = syllable_clip(self.directory/f'line {duration}.wav', 1, duration)
                normalized, stretched = self.directory/f'line {duration} 48k.wav', self.directory/f'line {tempo}.wav'
                frames, _ = render._normalize_clip(clip, normalized, None)
                written = render._stretch_clip(normalized, stretched, tempo, None)
                self.assertEqual(written, round(frames/tempo))
                with wave.open(str(stretched)) as file:
                    self.assertEqual(file.getnframes(), written)

    def test_a_faster_line_that_would_lose_its_end_is_refused(self):
        clip = syllable_clip(self.directory/'cut line.wav', 1, 1.2)
        normalized = self.directory/'cut line 48k.wav'
        render._normalize_clip(clip, normalized, None)
        # Too little silence after it: atempo keeps back the line's last ~30 ms, which must not be dropped.
        with patch.object(render, 'STRETCH_PAD_S', .001), \
                self.assertRaisesRegex(ValueError, 'Thời lượng câu đọc sau khi đọc nhanh không khớp'):
            render._stretch_clip(normalized, self.directory/'cut line faster.wav', 1.05, None)

    def test_original_sound_drops_under_the_voice_and_returns_in_pauses(self):
        import av
        source = self.directory/'long source.mp4'
        source_video(source, duration=4)
        cue = dict(start_ms=2000, end_ms=2700, text='Giọng Việt ở giữa.')
        paths = self.perform(source=source, cues=[cue], clips=self.clips[:1], speed=1.,
                             original_gain=.56, voice_gain=1.36)
        plan = json.loads(Path(paths['plan']).read_text(encoding='utf-8'))
        self.assertEqual(plan['ducking']['gain_under_voice'], .12)
        self.assertEqual(plan['ducking']['spans'], 1)
        def tone_level(path, start, end):  # Amplitude of the source's 170 Hz tone, apart from the voice's own tones.
            values = []
            with av.open(str(path)) as container:
                resampler = av.AudioResampler(format='fltp', layout='mono', rate=48000)
                for frame in container.decode(audio=0):
                    for converted in resampler.resample(frame):
                        times = converted.time+np.arange(converted.samples)/48000
                        values.append(converted.to_ndarray()[0][(times >= start) & (times < end)])
            data = np.concatenate(values).astype(np.float64)
            t = start+np.arange(len(data))/48000
            return 2*abs(np.mean(data*np.exp(-2j*np.pi*170*t)))
        for start, end, gain in ((.5, 1.5, .56), (2.15, 2.65, .12), (3.3, 3.9, .56)):
            with self.subTest(start=start):
                ratio = tone_level(paths['video'], start, end)/tone_level(source, start, end)
                self.assertAlmostEqual(ratio, gain, delta=gain*.08)

    def test_ducking_curve_fades_and_holds_through_short_pauses(self):
        path = self.directory/'curve.f32'
        placements = [dict(start_frame=round(a*render.RATE), end_frame=round(b*render.RATE))
                      for a, b in ((1., 2.), (2.3, 3.), (4., 4.5))]
        report = render._duck_envelope(path, placements, 5., .25)
        curve = np.fromfile(path, dtype='<f4')
        self.assertEqual(len(curve), 6*render.ENVELOPE_RATE)
        def at(seconds):
            return float(curve[round(seconds*render.ENVELOPE_RATE)])
        self.assertEqual(report['spans'], 2)  # The 0.3 s pause stays low.
        for seconds, value in ((.5, 1), (.85, 1), (1., .25), (1.5, .25), (2.15, .25), (3., .25), (3.3, 1),
                               (3.7, 1), (4.2, .25), (4.8, 1)):
            with self.subTest(seconds=seconds):
                self.assertAlmostEqual(at(seconds), value, places=5)
        rate = render.ENVELOPE_RATE
        self.assertTrue(np.all(np.diff(curve[round(.85*rate):rate+1]) < 0))       # Smoothly down before line 1...
        self.assertTrue(np.all(np.diff(curve[3*rate:round(3.3*rate)+1]) > 0))     # ...and back up after line 2.
        self.assertTrue(.55 < at(.926) < .7 and .55 < at(3.15) < .7)

    def test_lines_spoken_over_the_held_last_frame_read_the_pictures_last_moments(self):
        seen = []
        def boxes(path, intervals, **options):
            seen.extend(intervals)
            return [None]*len(intervals), dict(caption_rows=None)
        cues = [dict(start_ms=0, end_ms=500, text='Một.'), dict(start_ms=1300, end_ms=1800, text='Hai.'),
                dict(start_ms=1900, end_ms=2400, text='Ba.')]
        with patch('teodub.caption_cover.cover_boxes', side_effect=boxes):
            render._caption_boxes(self.source, .5, 2., cues, 1., 96, 64,
                                  dict(subtitle_size=13, subtitle_weight='bold'), None, None)
        np.testing.assert_allclose(seen[0], (.5, 1.))
        np.testing.assert_allclose(seen[1], (1.4, 1.9))   # Ends past the picture: its last half second...
        np.testing.assert_allclose(seen[2], (1.4, 1.9))   # ...as for a line wholly after it.

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
