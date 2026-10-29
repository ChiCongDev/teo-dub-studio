"""One clean box per burned Vietnamese line (owner 22/10/2026): geometry, the libass script, and real renders.

21/10 the box under the Vietnamese line and the box hiding the video's own caption were two rectangles; where the
own caption sat higher and was narrower, the white showed a step ("lồi lõm"). Now each line has one rectangle with
the text in its middle, large enough to hide the own caption, square corners.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import imageio_ffmpeg
import numpy as np

from teodub import render
from teodub.caption_layout import PADDING, layout, matched_size, place, wrap, write_captions
from teodub.subtitle_band import SCRIPT_HEIGHT, caption_lines

LONG = 'Không biết tiếng Anh, một mình ôm ba lô chu du khắp Đông Nam Á suốt mấy tháng trời'


def box_padding(size):
    """The box's padding in script units: a sixth of the line height (owner 22/10)."""
    return size*PADDING


class MatchedSizeTests(unittest.TestCase):
    def test_missing_own_captions_keep_the_chosen_size(self):
        self.assertEqual(matched_size(16, None, 576), 16)
        self.assertEqual(matched_size(16, [], 576), 16)

    def test_small_own_captions_give_text_as_small_not_the_chosen_size(self):
        # Changed 29/10/2026 (owner): these used to stay at the chosen 16.
        self.assertEqual(matched_size(16, [511, 537], 576), 13)     # Travel video: 26-pixel letters.
        self.assertEqual(matched_size(16, [970, 1018], 1080), 13)   # 48-pixel letters on 1080 lines.
        self.assertEqual(matched_size(16, [739, 764], 1024), 8)     # The owner's câu_ca video: 25-pixel letters on 1024 lines.

    def test_the_text_is_never_smaller_than_the_smallest_allowed_size(self):
        self.assertEqual(matched_size(16, [500, 503], 1080), 8)
        self.assertEqual(matched_size(8, [500, 503], 1080), 8)
        self.assertEqual(matched_size(36, [739, 764], 1024), 8)

    def test_larger_own_captions_give_larger_text_for_the_whole_video(self):
        self.assertEqual(matched_size(16, [527, 566], 576), 20)     # Food channel: 39-pixel letters.
        self.assertEqual(matched_size(16, [664, 707], 720), 17)     # Film recap: 43-pixel letters on 720 lines.

    def test_growth_is_capped(self):
        self.assertEqual(matched_size(16, [450, 576], 576), 24)     # At most 1.5 times the chosen size...
        self.assertEqual(matched_size(30, [300, 576], 576), 36)     # ...and never above the largest size allowed.


class WrapTests(unittest.TestCase):
    def test_a_short_caption_is_one_line(self):
        self.assertEqual(wrap('Chuyến đi mùa hai sắp kết thúc rồi.', 16, 1024, 576, True),
                         ['Chuyến đi mùa hai sắp kết thúc rồi.'])

    def test_a_long_caption_is_balanced_and_keeps_every_word_in_order(self):
        for width, height in ((1920, 1080), (1024, 576), (1080, 1920)):
            with self.subTest(frame=(width, height)):
                lines = wrap(LONG, 16, width, height, True)
                self.assertEqual(' '.join(lines).split(), LONG.split())
                self.assertEqual(len(lines), caption_lines(LONG, 16, width, height, True))
        two = wrap(LONG, 16, 1920, 1080, True)
        self.assertEqual(len(two), 2)
        from teodub.subtitle_band import _width_em
        widths = [_width_em(line, True) for line in two]
        self.assertLess(abs(widths[0]-widths[1]), max(widths)*.35)  # Balanced, not one full line and a stub.

    def test_one_very_long_word_stays_whole(self):
        self.assertEqual(wrap('a'*400, 13, 640, 360, False), ['a'*400])


class PlaceTests(unittest.TestCase):
    W, H, SIZE = 1024, 576, 16

    def scale(self):
        return self.H/SCRIPT_HEIGHT

    def test_without_an_own_caption_the_line_keeps_its_usual_place_on_a_snug_box(self):
        placed = place('Xin chào mọi người.', None, None, self.SIZE, 'bold', self.W, self.H)
        left, top, right, bottom = placed['box']
        line, padding = self.SIZE*self.scale(), box_padding(self.SIZE)*self.scale()
        self.assertAlmostEqual(bottom, self.H-18*self.scale()+padding, delta=1)
        self.assertAlmostEqual(bottom-top, line+2*padding, delta=1.5)
        self.assertAlmostEqual((left+right)/2, self.W/2, delta=.5)
        self.assertEqual(placed['centre'], ((left+right)/2, (top+bottom)/2))

    def test_the_screenshot_case_is_one_rectangle_around_both(self):
        # Video 12, 0:19: the own caption is narrower and sits ~11 px higher than the Vietnamese line's box.
        region, rows = (318., 489., 710., 559.), (511, 537)
        placed = place('Chuyến đi mùa hai sắp kết thúc rồi.', region, rows, self.SIZE, 'bold', self.W, self.H)
        left, top, right, bottom = placed['box']
        self.assertLessEqual(left, region[0])
        self.assertLessEqual(top, region[1])
        self.assertGreaterEqual(right, region[2])
        self.assertGreaterEqual(bottom, region[3])
        self.assertAlmostEqual(placed['centre'][1], (rows[0]+rows[1])/2, delta=1)
        self.assertAlmostEqual(placed['centre'][0], self.W/2, delta=.5)
        alone = place('Chuyến đi mùa hai sắp kết thúc rồi.', None, None, self.SIZE, 'bold', self.W, self.H)['box']
        self.assertAlmostEqual(right-left, alone[2]-alone[0], delta=2)  # The text is wider than the own caption.

    def test_a_wide_own_caption_widens_the_box_evenly_around_the_picture_centre(self):
        # Owner 22/10: always centred; the box reaches the own caption on both sides.
        region, rows = (150., 480., 900., 540.), (490, 530)
        placed = place('Rẻ thật.', region, rows, self.SIZE, 'bold', self.W, self.H)
        left, top, right, bottom = placed['box']
        self.assertEqual((left, right), (124, 900))
        self.assertEqual(placed['centre'][0], self.W/2)

    def test_a_large_own_caption_is_covered_with_the_box_centred(self):
        # The 22/10 example: a big caption (68-918, 481-574) under a two-line Vietnamese line, rows 527-566.
        region = (68., 481., 918., 574.)
        placed = place('Tôi nói này, các bạn sinh viên Đại học Công nghiệp Cáp Nhĩ Tân ơi.', region, (527, 566),
                       self.SIZE, 'bold', self.W, self.H)
        self.assertEqual(placed['box'], (68, 481, 956, 574))
        self.assertEqual(placed['centre'], (512.0, 527.5))

    def test_every_line_is_centred_on_the_picture(self):
        rows = (511, 537)
        for region in ((318., 489., 710., 559.), (60., 508., 813., 552.), (480., 505., 700., 550.), None):
            with self.subTest(region=region):
                placed = place('Nếm một tí thôi.', region, rows if region else None, self.SIZE, 'bold', self.W, self.H)
                self.assertEqual(placed['centre'][0], self.W/2)

    def test_an_own_caption_smaller_than_the_text_but_sticking_out_moves_the_box_only_that_far(self):
        rows = (511, 537)
        usual = place('Chuyến đi mùa hai sắp kết thúc rồi.', None, None, self.SIZE, 'bold', self.W, self.H)
        text_height = usual['box'][3]-usual['box'][1]
        region = (400., 490., 620., 530.)  # Starts above where a box centred on the rows would start.
        left, top, right, bottom = place('Chuyến đi mùa hai sắp kết thúc rồi.', region, rows, self.SIZE, 'bold',
                                         self.W, self.H)['box']
        self.assertLessEqual(top, 490)
        self.assertAlmostEqual(bottom-top, text_height, delta=1.5)
        self.assertAlmostEqual((left+right)/2, self.W/2, delta=.5)

    def test_the_height_is_the_same_for_every_line_of_a_video(self):
        rows = (500, 530)
        tops = {place(text, (300., 488., 700., 542.), rows, self.SIZE, 'bold', self.W, self.H)['box'][1]
                for text in ('Ồ.', 'Đi thôi.', 'Chuyến đi mùa hai sắp kết thúc rồi.')}
        self.assertEqual(len(tops), 1)

    def test_two_lines_get_one_box_tall_enough_for_both(self):
        placed = place(LONG, None, None, self.SIZE, 'bold', self.W, self.H)
        line, padding = self.SIZE*self.scale(), box_padding(self.SIZE)*self.scale()
        left, top, right, bottom = placed['box']
        self.assertEqual(len(placed['lines']), 2)
        self.assertAlmostEqual(bottom-top, 2*line+2*padding, delta=1.5)

    def test_the_box_stays_inside_the_picture_and_still_covers_the_caption(self):
        region = (200., 530., 820., 576.)
        left, top, right, bottom = place('Xin chào.', region, (560, 576), self.SIZE, 'bold', self.W, self.H)['box']
        self.assertEqual(bottom, self.H)
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(top, region[1])
        self.assertLessEqual(left, region[0])
        self.assertGreaterEqual(right, region[2])

    def test_one_entry_per_cue_with_its_times(self):
        cues = [dict(start_ms=0, end_ms=900, text='Một.'), dict(start_ms=1000, end_ms=2000, text='Hai.')]
        placed = layout(cues, [None, None], None, 13, 'regular', 640, 360)
        self.assertEqual([(item['start_ms'], item['end_ms']) for item in placed], [(0, 900), (1000, 2000)])
        with self.assertRaises(ValueError):
            layout(cues, [None], None, 13, 'regular', 640, 360)


class ScriptTests(unittest.TestCase):
    def write(self, placed, box='white'):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name)/'captions.ass'
        write_captions(path, placed, 16, 'bold', box, 1024, 576)
        return path.read_text(encoding='utf-8')

    def test_each_line_is_a_box_then_its_centred_text_at_the_same_times(self):
        cues = [dict(start_ms=0, end_ms=1234, text='Chuyến đi mùa hai sắp kết thúc rồi.')]
        placed = layout(cues, [(318., 489., 710., 559.)], (511, 537), 16, 'bold', 1024, 576)
        script = self.write(placed)
        self.assertIn('PlayResX: 1024', script)
        self.assertIn('WrapStyle: 2', script)
        events = [line for line in script.splitlines() if line.startswith('Dialogue:')]
        self.assertEqual(len(events), 2)
        self.assertTrue(events[0].startswith('Dialogue: 0,0:00:00.00,0:00:01.24,Box,'))
        self.assertIn('\\p1}m 0 0 l', events[0])
        self.assertTrue(events[1].startswith('Dialogue: 1,0:00:00.00,0:00:01.24,Caption,'))
        self.assertIn('{\\an5\\pos(512.0,', events[1])
        self.assertTrue(events[1].endswith('Chuyến đi mùa hai sắp kết thúc rồi.'))

    def test_caption_text_cannot_become_override_tags(self):
        placed = layout([dict(start_ms=0, end_ms=500, text='a {\\fs99} b \\N c')], [None], None, 16, 'bold', 1024, 576)
        text = self.write(placed).splitlines()[-1].split('}', 1)[1]
        self.assertNotIn('{', text)
        self.assertNotIn('\\', text)

    def test_box_colours_follow_the_choice(self):
        placed = layout([dict(start_ms=0, end_ms=500, text='Xin chào.')], [None], None, 16, 'bold', 1024, 576)
        self.assertIn('\\1c&HFFFFFF&', self.write(placed, 'white'))
        black = self.write(placed, 'black')
        self.assertIn('\\1c&H000000&', black)
        self.assertIn('Style: Caption,Arial,32.000,&H00FFFFFF', black)
        with self.assertRaises(ValueError):
            self.write(placed, 'none')


def own_caption_video(path, background, text, margin, size=16):
    """A synthetic video with a white Chinese caption with a black edge burned in for 3.4 s (no user footage)."""
    folder = path.parent
    own = folder/(path.stem+'.srt')
    own.write_text(f'1\n00:00:00,000 --> 00:00:03,400\n{text}\n\n', encoding='utf-8')
    style = (f'FontName=Microsoft YaHei,FontSize={size},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,'
             f'BorderStyle=1,Outline=1.5,Shadow=0,Bold=-1,Alignment=2,MarginV={margin}')
    escaped = str(own).replace('\\', '/').replace(':', '\\:')
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-n', '-f', 'lavfi',
                    '-i', background, '-vf', f"subtitles=filename='{escaped}':original_size=640x360:force_style='{style}'",
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)], check=True, capture_output=True, timeout=60)


class RenderedBoxTests(unittest.TestCase):
    """Real ffmpeg/libass renders; geometry measured on a black picture, where only the box is white."""

    @classmethod
    def setUpClass(cls):
        from tests.test_render import tone_clip
        cls.shared = tempfile.TemporaryDirectory(prefix='teodub-one-box-')
        cls.base = Path(cls.shared.name)
        cls.clips = [tone_clip(cls.base/f'voice {index}.wav', index) for index in range(1, 4)]

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def frame(self, video, seconds):
        import av
        with av.open(str(video)) as container:
            frames = {f.time: f.to_ndarray(format='rgb24') for f in container.decode(video=0)}
        return frames[min(frames, key=lambda moment: abs(moment-seconds))]

    def box_of(self, frame):
        white = frame.min(axis=2) > 235
        rows, columns = np.nonzero(white)
        return white, (rows.min(), rows.max()+1, columns.min(), columns.max()+1)

    def assert_one_clean_rectangle(self, frame):
        """On a black picture the white bounding box is the box: a step would leave black on one of its edges."""
        white, (top, bottom, left, right) = self.box_of(frame)
        for edge in (white[top:bottom, left], white[top:bottom, right-1], white[top, left:right], white[bottom-1, left:right]):
            self.assertGreater(float(np.mean(edge)), .99)
        ink = frame[top:bottom, left:right].max(axis=2) < 160
        rows, columns = np.nonzero(ink)
        self.assertGreater(rows.min(), 0)
        self.assertLess(rows.max(), bottom-top-1)
        self.assertAlmostEqual((columns.min()+columns.max()+1)/2, (right-left)/2, delta=3)  # Text in the middle.
        self.assertAlmostEqual((rows.min()+rows.max()+1)/2, (bottom-top)/2, delta=(bottom-top)*.12)
        return top, bottom, left, right

    def test_own_caption_narrower_and_higher_than_the_line_gives_one_box_that_hides_it(self):
        source = self.base/'higher own caption.mp4'
        own_caption_video(source, 'color=c=black:s=640x360:r=25:d=4.5', '这一路上都是穷游', 26)
        cues = [dict(start_ms=0, end_ms=700, text='Chuyến đi mùa hai sắp kết thúc rồi.'),
                dict(start_ms=1100, end_ms=1800, text='Mai lên tàu đi Yogyakarta.'),
                dict(start_ms=2200, end_ms=2900, text='Ngủ ngon nhé mọi người.')]
        paths = render.render_video(source, cues, self.clips, self.base/'higher', speed=1.,
                                    burn_subtitles=True, subtitle_box='white', subtitle_size=16)
        plan = json.loads(Path(paths['plan']).read_text(encoding='utf-8'))
        self.assertEqual(plan['caption_cover']['layout'], 'one_box')
        self.assertGreaterEqual(plan['caption_cover']['seen'], 2)
        original = self.frame(source, .35)
        rows, columns = np.nonzero(original.min(axis=2) > 150)
        own = (rows.min(), rows.max()+1, columns.min(), columns.max()+1)
        output = self.frame(paths['video'], .35)
        top, bottom, left, right = self.assert_one_clean_rectangle(output)
        self.assertLessEqual(top, own[0])
        self.assertGreaterEqual(bottom, own[1])
        self.assertLessEqual(left, own[2])
        self.assertGreaterEqual(right, own[3])
        self.assertGreater(float(np.mean(self.frame(paths['video'], 3.2).max(axis=2)[own[0]:own[1], own[2]:own[3]] > 150)),
                           .02)  # The own caption shows again when no Vietnamese line is spoken.

    def test_own_captions_larger_than_the_chosen_size_make_every_vietnamese_line_as_large(self):
        source = self.base/'larger own caption.mp4'
        own_caption_video(source, 'color=c=black:s=640x360:r=25:d=4.5', '这一路上都是穷游', 26, size=26)
        cues = [dict(start_ms=0, end_ms=700, text='Chuyến đi mùa hai sắp kết thúc rồi.'),
                dict(start_ms=1100, end_ms=1800, text='Mai lên tàu.'),
                dict(start_ms=2200, end_ms=2900, text='Ngủ ngon nhé.')]
        paths = render.render_video(source, cues, self.clips, self.base/'larger', speed=1.,
                                    burn_subtitles=True, subtitle_box='white', subtitle_size=13)
        plan = json.loads(Path(paths['plan']).read_text(encoding='utf-8'))
        rows = plan['caption_cover']['caption_rows']
        self.assertEqual(plan['subtitle_style']['size'], 13)  # The setting itself is unchanged.
        self.assertEqual(plan['caption_cover']['text_size'], matched_size(13, rows, 360))
        self.assertGreater(plan['caption_cover']['text_size'], 13)
        top, bottom, left, right = self.assert_one_clean_rectangle(self.frame(paths['video'], .35))
        self.assertGreaterEqual(bottom-top, plan['caption_cover']['text_size']*360/SCRIPT_HEIGHT*(1+2*PADDING)-1)

    def test_a_two_line_caption_without_own_captions_is_one_box(self):
        source = self.base/'plain.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-n', '-f', 'lavfi',
                        '-i', 'color=c=black:s=640x360:r=25:d=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)],
                       check=True, capture_output=True, timeout=60)
        paths = render.render_video(source, [dict(start_ms=0, end_ms=1500, text=LONG)], self.clips[:1],
                                    self.base/'two lines', speed=1., burn_subtitles=True, subtitle_box='white')
        self.assertIsNone(json.loads(Path(paths['plan']).read_text(encoding='utf-8'))['caption_cover']['caption_rows'])
        top, bottom, left, right = self.assert_one_clean_rectangle(self.frame(paths['video'], .5))
        line = 13*360/SCRIPT_HEIGHT
        self.assertGreater(bottom-top, 2*line)
        self.assertAlmostEqual(bottom, 360-18*360/SCRIPT_HEIGHT+box_padding(13)*360/SCRIPT_HEIGHT, delta=2)


if __name__ == '__main__':
    unittest.main()
