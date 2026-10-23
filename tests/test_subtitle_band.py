"""The band that hides a video's own captions: geometry matched to libass, a file without caption text."""
from pathlib import Path
import tempfile
import unittest

from teodub.subtitle_band import band_events, caption_lines, write_band

SHORT = 'Chi phí là 818,48 tệ.'
MEDIUM = 'Tám năm chuẩn bị cho một chuyến đi dài, rồi khởi hành một mình từ Quảng Châu.'
LONG = ('Video ghi lại điểm bắt đầu của lộ trình qua Trung Đông, Kavkaz và Trung Á, một kế hoạch cá nhân '
        'có nhiều chặng và nhiều điều chưa biết cần chuẩn bị kỹ lưỡng.')


class CaptionLinesTests(unittest.TestCase):
    def test_line_counts_match_what_libass_draws_for_landscape_videos(self):
        # Measured with the app's own FFmpeg/libass (Arial bold, opaque box) on 21/10/2026.
        for (width, height, size), expected in (((1920, 1080, 16), (1, 2, 3)), ((1280, 720, 13), (1, 2, 2))):
            with self.subTest(frame=(width, height, size)):
                self.assertEqual(tuple(caption_lines(text, size, width, height, True) for text in (SHORT, MEDIUM, LONG)),
                                 expected)

    def test_portrait_videos_wrap_into_many_more_lines(self):
        # libass drew 1, 5 and 9 lines on 1080x1920 at size 16.
        self.assertEqual([caption_lines(text, 16, 1080, 1920, True) for text in (SHORT, MEDIUM, LONG)], [1, 5, 9])

    def test_empty_or_one_word_text_is_one_line(self):
        self.assertEqual(caption_lines('', 16, 1920, 1080, True), 1)
        self.assertEqual(caption_lines('Xin', 16, 1920, 1080, False), 1)
        self.assertEqual(caption_lines('A'*400, 16, 1920, 1080, True), 1)  # libass never breaks inside a word


class BandTests(unittest.TestCase):
    def band(self, text, start=1000, end=2500, size=16, frame=(1920, 1080)):
        events = band_events([dict(start_ms=start, end_ms=end, text=text)], frame[0], frame[1], size, 'bold')
        self.assertEqual(len(events), 1)
        return events[0]

    def test_the_band_covers_the_caption_box_libass_draws_and_most_of_the_width(self):
        event = self.band(SHORT)
        # libass drew this one-line boxed caption over rows 937..1027 at 1080p.
        self.assertLessEqual(event['y'], 937)
        self.assertGreaterEqual(event['y']+event['h'], 1028)
        self.assertLess(event['h'], 91+60)  # A one-line band, not two lines with an empty strip.
        self.assertAlmostEqual(event['w'], 1920*.92, delta=2)
        self.assertAlmostEqual(event['x']+event['w']/2, 960, delta=2)
        self.assertEqual((event['start_ms'], event['end_ms']), (1000, 2500))

    def test_longer_captions_grow_the_band_upwards_from_the_same_bottom(self):
        one, two, three = self.band(SHORT), self.band(MEDIUM), self.band(LONG)
        self.assertLessEqual(two['y'], 877)
        self.assertLessEqual(three['y'], 817)
        self.assertEqual({round(item['y']+item['h']) for item in (one, two, three)}, {round(one['y']+one['h'])})

    def test_the_band_stays_inside_the_picture(self):
        event = self.band(LONG*4, size=36, frame=(320, 240))
        self.assertGreaterEqual(event['y'], 0)
        self.assertLessEqual(event['y']+event['h'], 240)

    def test_close_lines_share_one_continuous_band_and_distant_lines_do_not(self):
        cues = [dict(start_ms=0, end_ms=1000, text=SHORT), dict(start_ms=1800, end_ms=3000, text=SHORT),
                dict(start_ms=4500, end_ms=5000, text=MEDIUM)]
        events = band_events(cues, 1920, 1080, 16, 'bold')
        self.assertEqual([(item['start_ms'], item['end_ms']) for item in events], [(0, 1800), (1800, 3000), (4500, 5000)])
        self.assertLess(events[2]['y'], events[1]['y'])  # The two-line caption gets a taller band.

    def test_no_captions_no_band(self):
        self.assertEqual(band_events([], 1920, 1080, 16, 'bold'), [])


class BandFileTests(unittest.TestCase):
    def write(self, box):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'band.ass'
            cues = [dict(start_ms=1234, end_ms=2345, text=SHORT+' {\\b1}tag')]
            write_band(path, band_events(cues, 1920, 1080, 16, 'bold'), 1920, 1080, box)
            return path.read_text(encoding='utf-8')

    def test_the_band_file_holds_only_drawings_in_pixels_and_never_any_caption_text(self):
        text = self.write('white')
        self.assertIn('PlayResX: 1920', text)
        self.assertIn('PlayResY: 1080', text)
        self.assertIn('Dialogue: 0,0:00:01.23,0:00:02.35,Band,', text)  # Starts no later, ends no earlier.
        self.assertIn('\\p1}m 0 0 l ', text)
        self.assertIn('\\1c&HFFFFFF&', text)
        for caption in ('Chi phí', 'tệ', '\\b1', 'tag'):
            self.assertNotIn(caption, text)

    def test_a_black_band_is_drawn_in_black(self):
        self.assertIn('\\1c&H000000&', self.write('black'))

    def test_only_a_known_box_can_be_written(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            write_band(Path(temporary)/'band.ass', [], 1920, 1080, 'none')


class CampaignCarryTests(unittest.TestCase):
    def test_campaigns_freeze_the_box_and_describe_it(self):
        from teodub.campaign import CONFIG_KEYS
        from teodub.campaign_preview import describe_pipeline_settings
        self.assertIn('subtitle_box', CONFIG_KEYS)
        settings = dict(voice='Mai Anh', voice_provider='local', reading_style='legacy', speed=.9, original_gain=.1,
                        translation_model='gpt-5.6-luna', translation_effort='low', burn_subtitles=True,
                        subtitle_color='white', subtitle_weight='bold', subtitle_size=16)
        self.assertNotIn('Khung', describe_pipeline_settings(settings))
        self.assertIn('Khung trắng', describe_pipeline_settings(dict(settings, subtitle_box='white')))


if __name__ == '__main__':
    unittest.main()
