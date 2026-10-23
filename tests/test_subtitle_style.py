import unittest

from teodub.subtitle_style import ass_box_style, ass_force_style, validate_subtitle_style


class SubtitleStyleTests(unittest.TestCase):
    def test_old_jobs_keep_selectable_caption_mode(self):
        self.assertEqual(validate_subtitle_style({}), {
            'burn_subtitles': False, 'subtitle_color': 'white', 'subtitle_weight': 'bold', 'subtitle_size': 13})

    def test_ass_colors_and_weights_are_real_style_values(self):
        yellow_regular = ass_force_style('yellow', 'regular')
        black_bold = ass_force_style('black', 'bold')
        self.assertIn('PrimaryColour=&H0000FFFF', yellow_regular)
        self.assertIn('Bold=0', yellow_regular)
        self.assertIn('PrimaryColour=&H00000000', black_bold)
        self.assertIn('OutlineColour=&H00FFFFFF', black_bold)
        self.assertIn('Bold=-1', black_bold)

    def test_invalid_values_are_rejected(self):
        for value in (1, 'yes', None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_subtitle_style({'burn_subtitles': value})
        with self.assertRaises(ValueError):
            validate_subtitle_style({'subtitle_color': 'red'})
        with self.assertRaises(ValueError):
            validate_subtitle_style({'subtitle_weight': 'extra-bold'})

    def test_the_box_is_kept_only_for_burned_captions_and_absent_means_none(self):
        self.assertNotIn('subtitle_box', validate_subtitle_style({'burn_subtitles': True}))
        self.assertNotIn('subtitle_box', validate_subtitle_style({'burn_subtitles': True, 'subtitle_box': 'none'}))
        self.assertNotIn('subtitle_box', validate_subtitle_style({'burn_subtitles': False, 'subtitle_box': 'white'}))
        for box in ('white', 'black'):
            self.assertEqual(validate_subtitle_style({'burn_subtitles': True, 'subtitle_box': box})['subtitle_box'], box)
        for box in ('red', '', None, 1, 'White'):
            with self.subTest(box=box), self.assertRaises(ValueError):
                validate_subtitle_style({'burn_subtitles': True, 'subtitle_box': box})

    def test_boxed_caption_is_an_opaque_box_in_the_band_colour_at_the_usual_place(self):
        white = ass_box_style('white', 'bold', 16).split(',')
        for part in ('BorderStyle=3', 'PrimaryColour=&H00000000', 'OutlineColour=&H00FFFFFF', 'BackColour=&H00FFFFFF',
                     'FontSize=16', 'Bold=-1', 'Shadow=0', 'Alignment=2', 'MarginL=22', 'MarginR=22', 'MarginV=18',
                     'Outline=4'):
            self.assertIn(part, white)
        black = ass_box_style('black', 'regular', 13).split(',')
        for part in ('PrimaryColour=&H00FFFFFF', 'OutlineColour=&H00000000', 'Bold=0', 'Outline=3.25'):
            self.assertIn(part, black)
        for box in ('none', 'red'):
            with self.subTest(box=box), self.assertRaises(ValueError):
                ass_box_style(box, 'bold')

    def test_font_size_validation_and_filter(self):
        for size in (8, 13, 20, 36, '24'):
            self.assertEqual(validate_subtitle_style({'subtitle_size': size})['subtitle_size'], int(size))
            self.assertIn(f'FontSize={int(size)}', ass_force_style('white', 'bold', size))
        for size in (True, 7, 37, 13.5, None, '', '13,Outline=0', float('nan')):
            with self.subTest(size=size), self.assertRaises(ValueError):
                validate_subtitle_style({'subtitle_size': size})


if __name__ == '__main__':
    unittest.main()
