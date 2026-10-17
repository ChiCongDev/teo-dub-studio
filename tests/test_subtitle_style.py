import unittest

from teodub.subtitle_style import ass_force_style, validate_subtitle_style


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

    def test_font_size_validation_and_filter(self):
        for size in (8, 13, 20, 36, '24'):
            self.assertEqual(validate_subtitle_style({'subtitle_size': size})['subtitle_size'], int(size))
            self.assertIn(f'FontSize={int(size)}', ass_force_style('white', 'bold', size))
        for size in (True, 7, 37, 13.5, None, '', '13,Outline=0', float('nan')):
            with self.subTest(size=size), self.assertRaises(ValueError):
                validate_subtitle_style({'subtitle_size': size})


if __name__ == '__main__':
    unittest.main()
