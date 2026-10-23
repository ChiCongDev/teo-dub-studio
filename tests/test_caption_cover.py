"""Finding a video's own burned-in captions, on synthetic videos made with the app's own FFmpeg/libass."""
from pathlib import Path
import subprocess
import tempfile
import unittest

import imageio_ffmpeg
import numpy as np

from teodub import caption_cover

W, H = 640, 360
LINES = ['这是第一句字幕', '第二句', '我们今天去海边看日落', '好吃', '这里的东西真的很便宜啊',
         '走吧', '明天见', '这个城市很漂亮', '对', '最后一句话了朋友们']


def stamp(seconds):
    whole = int(seconds)
    return '00:00:%02d,%03d' % (whole, round((seconds-whole)*1000))


def write_srt(path, lines):
    with open(path, 'w', encoding='utf-8') as file:
        for index, (start, end, text) in enumerate(lines, 1):
            file.write('%d\n%s --> %s\n%s\n\n' % (index, stamp(start), stamp(end), text))


def filter_path(path):
    return str(path).replace('\\', '/').replace(':', '\\:')


def render(target, background, srt=None, size=16):
    chain = 'null'
    if srt is not None:
        chain = ("subtitles=filename='%s':original_size=%dx%d:force_style='FontName=Microsoft YaHei,FontSize=%d,"
                 "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=1.5,Shadow=0,Bold=-1,"
                 "Alignment=2,MarginV=14'" % (filter_path(srt), W, H, size))
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                    '-i', background, '-vf', chain, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-g', '25', str(target)],
                   check=True, capture_output=True, timeout=120)


def frame_at(path, moment):
    import av
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            if frame.time is not None and frame.time >= moment:
                return frame.to_ndarray(format='rgb24')
    return None


class CaptionCoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='caption-cover-')
        cls.root = Path(cls.temporary.name)
        cls.timeline = [(.3+1.2*index, 1.2+1.2*index, text) for index, text in enumerate(LINES)]
        cls.srt = cls.root/'captions.srt'
        write_srt(cls.srt, cls.timeline)
        moving = 'testsrc2=size=%dx%d:rate=25:duration=12.5' % (W, H)
        cls.video = cls.root/'with captions.mp4'
        render(cls.video, moving, cls.srt)
        # The same captions alone on black: where each caption really is.
        cls.truth = cls.root/'truth.mp4'
        render(cls.truth, 'color=c=black:s=%dx%d:r=25:d=12.5' % (W, H), cls.srt)
        cls.plain = cls.root/'no captions.mp4'
        render(cls.plain, moving)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def truth_box(self, moment):
        light = np.nonzero(frame_at(self.truth, moment).min(axis=2) > 150)
        return light[1].min(), light[0].min(), light[1].max(), light[0].max()

    def test_every_spoken_line_gets_a_box_covering_its_caption(self):
        boxes, report = caption_cover.cover_boxes(self.video, [(start, end) for start, end, _ in self.timeline])
        self.assertEqual(report['lines'], len(LINES))
        self.assertGreaterEqual(report['seen'], len(LINES)-2)
        for (start, end, text), box in zip(self.timeline, boxes):
            with self.subTest(text=text):
                x0, y0, x1, y1 = self.truth_box((start+end)/2)
                self.assertIsNotNone(box)
                self.assertLessEqual(box[0], x0+1)
                self.assertLessEqual(box[1], y0+1)
                self.assertGreaterEqual(box[2], x1-1)
                self.assertGreaterEqual(box[3], y1-1)

    def test_boxes_follow_each_caption_instead_of_spanning_the_picture(self):
        boxes, _report = caption_cover.cover_boxes(self.video, [(start, end) for start, end, _ in self.timeline])
        widths = {text: box[2]-box[0] for (_s, _e, text), box in zip(self.timeline, boxes)}
        self.assertLess(widths['好吃'], widths['这里的东西真的很便宜啊']*.6)
        self.assertLess(max(widths.values()), W*.8)

    def test_a_line_whose_caption_is_missing_gets_the_typical_box_of_the_video(self):
        intervals = [(start, end) for start, end, _ in self.timeline]+[(12.15, 12.45)]  # Nothing burned in here.
        boxes, report = caption_cover.cover_boxes(self.video, intervals)
        self.assertGreaterEqual(report['typical'], 1)
        self.assertIsNotNone(boxes[-1])
        x0, y0, x1, y1 = self.truth_box((self.timeline[0][0]+self.timeline[0][1])/2)
        self.assertLessEqual(boxes[-1][1], y0+1)
        self.assertGreaterEqual(boxes[-1][3], y1-1)

    def test_a_video_without_captions_gets_no_boxes(self):
        boxes, report = caption_cover.cover_boxes(self.plain, [(start, end) for start, end, _ in self.timeline])
        self.assertEqual(boxes, [None]*len(LINES))
        self.assertEqual((report['seen'], report['typical'], report['caption_rows']), (0, 0, None))

    def test_stop_is_honoured_while_scanning(self):
        class Stop(Exception):
            pass
        calls = []
        def check():
            calls.append(1)
            if len(calls) > 3:
                raise Stop()
        with self.assertRaises(Stop):
            caption_cover.cover_boxes(self.video, [(start, end) for start, end, _ in self.timeline], check=check)

    def test_progress_is_reported_and_reaches_the_end(self):
        seen = []
        caption_cover.cover_boxes(self.video, [(start, end) for start, end, _ in self.timeline], progress=seen.append)
        self.assertTrue(seen)
        self.assertEqual(seen, sorted(seen))
        self.assertGreater(seen[-1], .9)


class CaptionShapesTests(unittest.TestCase):
    def make(self, lines, size):
        temporary = tempfile.TemporaryDirectory(prefix='caption-shapes-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        srt = root/'c.srt'
        write_srt(srt, lines)
        video, truth = root/'v.mp4', root/'t.mp4'
        render(video, 'testsrc2=size=%dx%d:rate=25:duration=8' % (W, H), srt, size)
        render(truth, 'color=c=black:s=%dx%d:r=25:d=8' % (W, H), srt, size)
        return video, truth

    def covered(self, lines, size):
        video, truth = self.make(lines, size)
        boxes, _report = caption_cover.cover_boxes(video, [(start, end) for start, end, _ in lines])
        for (start, end, text), box in zip(lines, boxes):
            light = np.nonzero(frame_at(truth, (start+end)/2).min(axis=2) > 150)
            with self.subTest(text=text):
                self.assertIsNotNone(box)
                self.assertLessEqual(box[0], light[1].min()+1)
                self.assertGreaterEqual(box[2], light[1].max()-1)
                self.assertLessEqual(box[1], light[0].min()+1)
                self.assertGreaterEqual(box[3], light[0].max()-1)

    def test_very_large_lettering_is_covered(self):
        self.covered([(.3+1.5*i, 1.5+1.5*i, text) for i, text in enumerate(['今天吃这个', '好香啊', '再来一碗', '真的好吃', '拜拜'])], 30)

    def test_words_separated_by_spaces_are_one_caption(self):
        self.covered([(.3+1.5*i, 1.5+1.5*i, text) for i, text in enumerate(['哎呦太好了      姐', '你好      朋友', '走吧      快点',
                                                                            '对      没错', '明天      见'])], 16)


class StrokeMaskTests(unittest.TestCase):
    """22/10: a light pixel counts only as part of a stroke, darker on both sides (a caption's outlined text)."""

    def test_strokes_count_and_the_edges_of_light_areas_do_not(self):
        rgb = np.full((60, 200, 3), 40, np.uint8)
        rgb[10:50, 10:90] = 255     # A white table square: each edge is dark on one side only.
        rgb[10:50, 120:123] = 255   # A vertical stroke, dark on the left and right.
        rgb[29:32, 140:190] = 255   # A horizontal stroke, dark above and below.
        mask = caption_cover._mask(rgb)
        self.assertTrue(mask[20:40, 120:123].all())
        self.assertTrue(mask[29:32, 150:180].all())
        self.assertFalse(mask[10:50, 10:90].any())

    def test_a_still_scene_does_not_join_a_light_object_to_the_caption(self):
        # Camera on a tripod: a white tray stands still on the caption rows, just left of the caption.
        temporary = tempfile.TemporaryDirectory(prefix='caption-still-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        lines = [(.3+1.2*i, 1.2+1.2*i, text) for i, text in enumerate(['这是第一句字幕', '我们今天去海边', '这里真的很便宜',
                                                                        '明天再见朋友们', '最后一句话了'])]
        srt = root/'c.srt'
        write_srt(srt, lines)
        scene = 'color=c=0x505050:s=%dx%d:r=25:d=6.5,drawbox=x=60:y=314:w=110:h=26:color=white:t=fill' % (W, H)
        video, truth = root/'v.mp4', root/'t.mp4'
        render(video, scene, srt)
        render(truth, 'color=c=black:s=%dx%d:r=25:d=6.5' % (W, H), srt)
        boxes, _report = caption_cover.cover_boxes(video, [(start, end) for start, end, _ in lines])
        for (start, end, text), box in zip(lines, boxes):
            light = np.nonzero(frame_at(truth, (start+end)/2).min(axis=2) > 150)
            with self.subTest(text=text):
                self.assertIsNotNone(box)
                self.assertLessEqual(box[0], light[1].min()+1)    # The caption is covered...
                self.assertGreaterEqual(box[2], light[1].max()-1)
                self.assertGreater(box[0], 170)                   # ...without the tray beside it (60-170).


class CaptionRowsTests(unittest.TestCase):
    """22/10: a box keeps only what lies on the video's caption rows, or forms another whole text line.

    Analysis pixels of a 960x540 picture; the video's caption rows are 480-504 (a 24-pixel line)."""
    BAND = dict(y0=480., y1=504., line=24., typical=400., seen=50)
    SIZE = (960, 540, 960, 540)

    @staticmethod
    def candidate(y0, rows, x0=280, x1=680):
        """rows: (count, first x, last x) for each row from y0."""
        density = sum(r[0] for r in rows)/((x1-x0+1)*len(rows))
        return dict(x0=x0, y0=y0, x1=x1, y1=y0+len(rows)-1, density=density, rows=rows)

    def test_scenery_merged_above_the_caption_is_left_out(self):
        # A pot's rim (sparse, wide) touching the caption from above: one tall candidate 455-504.
        rows = [(30, 40, 900)]*25+[(120, 300, 660)]*25
        box, kind = caption_cover.decide([self.candidate(455, rows, 40, 900)], self.BAND, self.SIZE)
        self.assertEqual(kind, 'seen')
        pad = self.BAND['line']*.2
        self.assertAlmostEqual(box[1], 480-pad)          # Not up to the rim at 455.
        self.assertAlmostEqual(box[0], 300-pad)          # Nor as wide as the rim.
        self.assertAlmostEqual(box[2], 660+pad)

    def test_a_real_second_caption_line_is_kept(self):
        # Two dense text lines with no gap: 456-479 and 480-504.
        rows = [(110, 330, 630)]*24+[(120, 300, 660)]*25
        box, _kind = caption_cover.decide([self.candidate(456, rows)], self.BAND, self.SIZE)
        self.assertLessEqual(box[1], 456)
        self.assertLessEqual(box[0], 300)
        self.assertGreaterEqual(box[2], 660)

    def test_a_separate_line_shaped_candidate_counts_and_thin_scenery_does_not(self):
        caption = self.candidate(480, [(120, 300, 660)]*25)
        line_above = self.candidate(450, [(100, 350, 610)]*22, 350, 610)
        crust = self.candidate(462, [(200, 100, 860)]*6, 100, 860)
        box, _kind = caption_cover.decide([caption, line_above], self.BAND, self.SIZE)
        self.assertLessEqual(box[1], 450)
        box, _kind = caption_cover.decide([caption, crust], self.BAND, self.SIZE)
        self.assertGreater(box[1], 470)
        self.assertGreater(box[0], 280)

    def test_a_caption_slightly_off_the_usual_rows_is_still_covered(self):
        # This line's caption sits 6 pixels (a quarter line) lower than usual.
        box, _kind = caption_cover.decide([self.candidate(486, [(120, 300, 660)]*25)], self.BAND, self.SIZE)
        self.assertGreaterEqual(box[3], 510)

    def test_candidates_without_row_detail_keep_their_whole_box(self):
        old = dict(x0=280, y0=470, x1=680, y1=504, density=.2)
        box, _kind = caption_cover.decide([old], self.BAND, self.SIZE)
        self.assertLessEqual(box[1], 470)

    def test_scans_record_each_rows_extent(self):
        mask = np.zeros((216, 960), bool)
        mask[100:124, 300:660:3] = True   # Text-like strokes.
        found = caption_cover._candidates(mask, 960, 540, 324)
        self.assertTrue(found)
        rows = found[0]['rows']
        self.assertEqual(len(rows), found[0]['y1']-found[0]['y0']+1)
        self.assertTrue(all(n == 0 or a <= b for n, a, b in rows))


if __name__ == '__main__':
    unittest.main()
