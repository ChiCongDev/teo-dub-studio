"""Chỉnh sửa cấu hình on a link's tab (owner 06/11/2026; choices 1A, 2B, 3A): the Video đơn form for ONE link's next videos, and
optionally its finished videos made again. Local fixtures only; no worker, network or AI."""
import unittest

from teodub import campaign
from teodub.campaign_settings import made_with_everything, next_settings, same_language_models
from teodub.ai_models import CHOICES
from teodub.app import SPEEDS
from test_campaign_voice_ui import LinkFixture


class ConfigDialogTests(LinkFixture):
    def dialog(self):
        self.link.open_config()
        window = self.link.config_window
        self.addCleanup(lambda: window.winfo_exists() and window.close())
        return window

    def items(self):
        return {item['number']: item for item in campaign.read_json(self.directory/'campaign.json')['items']}

    def other_model(self, window, key):
        return next(value for value in CHOICES[key] if value != window.fields[key].get())

    def test_the_button_is_on_the_link_tab_and_opens_one_window(self):
        self.assertEqual(str(self.link.config_button['text']), 'Chỉnh sửa cấu hình…')
        self.link.open_config()
        first = self.link.config_window
        self.link.open_config()
        self.assertIs(self.link.config_window, first)
        self.link.close()
        self.assertFalse(first.winfo_exists())

    def test_it_opens_filled_with_the_links_own_settings_and_saves_nothing(self):
        window = self.dialog()
        self.assertEqual(str(window.save_button['state']), 'normal')
        self.assertEqual(window.fields['voice'].get(), 'Mai Anh')
        self.assertEqual((window.fields['original_volume'].get(), window.fields['voice_volume'].get()), ('10', '100'))
        self.assertFalse(window.fields['burn_subtitles'].get())
        self.assertFalse(window.redo.get())
        self.assertIsNone(next_settings(self.directory))
        window.apply()  # Nothing was changed.
        self.assertIn('Chưa đổi gì', window.status.get())
        self.assertIsNone(next_settings(self.directory))

    def test_every_kind_of_setting_is_saved_for_the_next_videos_and_the_finished_ones_stay(self):
        self.finish({1})
        before = self.items()[1]
        window = self.dialog()
        window.fields['voice'].set('Trúc Ly')
        window.fields['original_volume'].set('30')
        window.fields['burn_subtitles'].set(True)
        window.fields['subtitle_box'].set('white')
        window.fields['subtitle_size'].set('18')
        window.fields['wording_enabled'].set(True)
        window.rules.delete('1.0', 'end')
        window.rules.insert('1.0', 'giá => cả')
        window.fields['speed'].set(next(label for label in SPEEDS if SPEEDS[label] == 1.0))
        model = self.other_model(window, 'translation_model')
        window.model_labels['translation_model'].set(CHOICES['translation_model'][model])
        window.choose_model('translation_model')
        window.apply()
        settings = next_settings(self.directory)['settings']
        self.assertEqual((settings['voice'], settings['original_gain'], settings['speed']), ('Trúc Ly', .3, 1.0))
        self.assertEqual((settings['burn_subtitles'], settings['subtitle_box'], settings['subtitle_size']), (True, 'white', 18))
        self.assertEqual((settings['wording']['enabled'], settings['wording']['rules'][0]['source']), (True, 'giá'))
        self.assertEqual(settings['translation_model'], model)
        self.assertEqual(self.items()[1], before)  # Not ticked: the finished video is left alone.
        self.assertFalse(list(self.directory.glob('redo-*.json')))
        message = self.link.message.get()
        for part in ('giọng và cách đọc', 'âm lượng', 'Sub Việt', 'tốc độ hình', 'từ ngữ', 'mô hình AI'):
            self.assertIn(part, message)
        self.assertFalse(window.winfo_exists())

    def test_the_other_links_settings_and_the_video_don_page_are_not_touched(self):
        page_voice = self.studio.fields['voice'].get()
        window = self.dialog()
        window.fields['voice'].set('Trúc Ly')
        window.apply()
        self.assertEqual(self.studio.fields['voice'].get(), page_voice)
        self.assertEqual(next_settings(self.directory)['settings']['voice'], 'Trúc Ly')

    def test_a_wrong_choice_is_refused_and_nothing_is_saved(self):
        window = self.dialog()
        window.fields['original_volume'].set('250')
        window.apply()
        self.assertIn('Chưa lưu', window.status.get())
        self.assertIsNone(next_settings(self.directory))
        window.fields['original_volume'].set('10')
        window.rules.delete('1.0', 'end')
        window.fields['wording_enabled'].set(True)  # Switched on with an empty table.
        window.apply()
        self.assertIn('Chưa lưu', window.status.get())
        self.assertIsNone(next_settings(self.directory))

    def test_the_tick_makes_the_finished_videos_again_and_the_check_only_reads(self):
        self.finish({1, 2})
        window = self.dialog()
        window.fields['burn_subtitles'].set(True)
        window.fields['subtitle_box'].set('white')
        window.check()
        self.assertIn('Sẽ làm lại: 2 video', window.result.get('1.0', 'end'))
        self.assertIsNone(next_settings(self.directory))
        window.redo.set(True)
        window.apply()
        items = self.items()
        self.assertTrue(items[1].get('redo') and items[2].get('redo'))
        self.assertFalse(items[3].get('redo'))
        self.assertIn('Đã xếp làm lại 2 video', self.link.message.get())

    def test_a_changed_picture_speed_or_wording_also_reaches_the_finished_videos(self):
        self.finish({1})
        window = self.dialog()
        window.fields['speed'].set(next(label for label in SPEEDS if SPEEDS[label] == 1.0))
        window.redo.set(True)
        window.apply()
        self.assertTrue(self.items()[1].get('redo'))

    def test_a_video_made_with_other_ai_models_is_left_alone_when_remaking(self):
        self.finish({1, 2})
        window = self.dialog()
        model = self.other_model(window, 'translation_model')
        window.model_labels['translation_model'].set(CHOICES['translation_model'][model])
        window.choose_model('translation_model')
        window.check()
        text = window.result.get('1.0', 'end')
        self.assertIn('Sẽ làm lại: 0 video', text)
        self.assertIn('tốn AI', text)
        window.redo.set(True)
        window.apply()
        self.assertEqual(next_settings(self.directory)['settings']['translation_model'], model)  # New videos use it.
        self.assertFalse(any(item.get('redo') for item in self.items().values()))
        self.assertIn('Không có video đã xong nào cần làm lại', self.link.message.get())

    def test_a_link_with_everything_finished_needs_the_tick(self):
        self.finish({1, 2, 3, 4})
        window = self.dialog()
        window.fields['voice'].set('Mai Anh')
        window.fields['voice_volume'].set('150')
        window.apply()  # Nothing left to make: refused, nothing saved.
        self.assertIn('Chưa lưu', window.status.get())
        self.assertIsNone(next_settings(self.directory))
        window.redo.set(True)
        window.apply()
        self.assertEqual(next_settings(self.directory)['settings']['voice_gain'], 1.5)
        self.assertEqual(sum(bool(item.get('redo')) for item in self.items().values()), 4)

    def test_going_back_from_a_sample_voice_to_an_ordinary_one_leaves_no_old_sample(self):
        window = self.dialog()
        window.base = dict(window.base, voice='Giọng từ mẫu', reference_audio='C:/old/sample.wav', reference_sha256='ab'*32)
        window.fields['voice'].set('Trúc Ly')
        merged = window.merged_settings()
        self.assertEqual(merged['voice'], 'Trúc Ly')
        self.assertNotIn('reference_audio', merged)
        self.assertNotIn('reference_sha256', merged)

    def test_the_rules_for_what_counts_as_the_same(self):
        config = dict(speed=.9, burn_subtitles=False, voice='Mai Anh', original_gain=.1, voice_gain=1.0, translation_model='a')
        self.assertTrue(made_with_everything(config, dict(config)))
        self.assertFalse(made_with_everything(config, dict(config, speed=1.0)))
        self.assertFalse(made_with_everything(config, dict(config, wording=dict(version='vi-wording-v1', enabled=True,
                                                                                 rules=[dict(source='a', replacement='b')]))))
        self.assertTrue(same_language_models(config, dict(config, speed=1.0)))
        self.assertFalse(same_language_models(config, dict(config, translation_model='b')))


if __name__ == '__main__':
    unittest.main()
