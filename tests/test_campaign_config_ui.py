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
        self.assertTrue(window.winfo_exists())  # The name box is open: the settings can be kept to use again.
        window.skip_preset()
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

    def test_the_tick_is_remembered_for_this_link_until_it_is_saved_unticked(self):
        from teodub.campaign_config_ui import REDO_FILE, REDO_NOTE
        self.finish({1, 2})
        window = self.dialog()
        self.assertEqual(window.redo_note.get(), '')
        window.fields['burn_subtitles'].set(True)
        window.fields['subtitle_box'].set('white')
        window.redo.set(True)
        self.assertEqual(window.redo_note.get(), REDO_NOTE)
        window.apply()
        window.skip_preset()
        self.assertTrue((self.directory/REDO_FILE).exists())
        reopened = self.dialog()
        self.assertTrue(reopened.redo.get())
        self.assertEqual(reopened.redo_note.get(), REDO_NOTE)
        self.assertIn('Chưa đổi gì', (reopened.apply(), reopened.status.get())[1])  # Ticked again with no change: nothing is made twice.
        self.assertEqual(sum(bool(item.get('redo')) for item in self.items().values()), 2)
        reopened.fields['subtitle_size'].set('20')
        reopened.redo.set(False)
        reopened.apply()
        reopened.skip_preset()
        again = self.dialog()
        self.assertFalse(again.redo.get())
        self.assertEqual(again.redo_note.get(), '')

    def test_the_tick_is_not_remembered_when_nothing_is_saved_or_the_file_is_damaged(self):
        from teodub.campaign_config_ui import REDO_FILE
        window = self.dialog()
        window.redo.set(True)
        window.apply()  # Nothing changed, so nothing is saved.
        self.assertFalse((self.directory/REDO_FILE).exists())
        window.close()
        (self.directory/REDO_FILE).write_text('{broken', encoding='utf-8')
        self.assertFalse(self.dialog().redo.get())

    def test_a_changed_picture_speed_or_wording_also_reaches_the_finished_videos(self):
        self.finish({1})
        window = self.dialog()
        window.fields['speed'].set(next(label for label in SPEEDS if SPEEDS[label] == 1.0))
        window.redo.set(True)
        window.apply()
        self.assertTrue(self.items()[1].get('redo'))

    def test_a_video_made_with_other_ai_models_is_made_again_and_translated_again(self):
        """Owner 12/11/2026: no longer left alone; it is translated again with AI, and the plan says so."""
        self.finish({1, 2})
        window = self.dialog()
        model = self.other_model(window, 'translation_model')
        window.model_labels['translation_model'].set(CHOICES['translation_model'][model])
        window.choose_model('translation_model')
        window.check()
        text = window.result.get('1.0', 'end')
        self.assertIn('Sẽ làm lại: 2 video', text)
        self.assertIn('dịch lại bằng AI', text)
        window.redo.set(True)
        window.apply()
        self.assertEqual(next_settings(self.directory)['settings']['translation_model'], model)
        self.assertEqual(sorted(n for n, item in self.items().items() if item.get('redo')), [1, 2])

    def test_save_with_nothing_changed_only_queues_the_finished_videos_when_the_tick_is_on(self):
        """Owner 12/11/2026: the settings are already the link's; the tick alone makes Lưu chỉnh sửa queue the finished, unposted videos."""
        self.finish({1, 2})
        first = self.dialog()
        model = self.other_model(first, 'translation_model')
        first.model_labels['translation_model'].set(CHOICES['translation_model'][model])
        first.choose_model('translation_model')
        first.apply()  # Saved for the next videos, the tick is off: the finished ones keep their old version.
        saved = next_settings(self.directory)['id']
        first.close()
        self.assertFalse(any(item.get('redo') for item in self.items().values()))
        second = self.dialog()
        second.redo.set(True)
        second.apply()
        self.assertEqual(next_settings(self.directory)['id'], saved)  # Nothing was saved again.
        self.assertEqual(sorted(n for n, item in self.items().items() if item.get('redo')), [1, 2])
        self.assertIn('dịch lại bằng AI', self.link.message.get())

    def test_save_with_nothing_changed_and_no_tick_still_says_nothing_changed(self):
        self.finish({1, 2})
        window = self.dialog()
        window.apply()
        self.assertIn('Chưa đổi gì', window.status.get())
        self.assertFalse(any(item.get('redo') for item in self.items().values()))

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


    # Cấu hình đã lưu (owner 10/11/2026)
    def presets(self):
        from teodub import config_presets
        return config_presets.read_presets(self.data)

    def save_as(self, window, name):
        window.preset_name.set(name)
        window.save_preset()

    def change_boxes(self, window, voice='Trúc Ly', volume='30'):
        window.fields['voice'].set(voice)
        window.fields['original_volume'].set(volume)
        window.fields['burn_subtitles'].set(True)
        window.fields['subtitle_box'].set('white')

    def test_after_saving_a_name_can_be_given_and_the_window_closes_when_it_is_answered(self):
        window = self.dialog()
        self.assertFalse(window.name_row.winfo_ismapped() or window.preset_offer)
        self.change_boxes(window)
        window.apply()
        self.assertEqual(next_settings(self.directory)['settings']['voice'], 'Trúc Ly')  # The link is saved first.
        self.assertTrue(window.winfo_exists() and window.preset_offer is not None)
        self.assertIn('Không cần', window.status.get())
        self.save_as(window, '  Review   sản phẩm ')
        self.assertFalse(window.winfo_exists())
        rows = self.presets()
        self.assertEqual([row['name'] for row in rows], ['Review sản phẩm'])
        self.assertEqual((rows[0]['settings']['voice'], rows[0]['settings']['original_gain'], rows[0]['settings']['subtitle_box']),
                         ('Trúc Ly', .3, 'white'))

    def test_no_thanks_keeps_the_link_saved_and_makes_no_preset(self):
        window = self.dialog()
        self.change_boxes(window)
        window.apply()
        window.skip_preset()
        self.assertFalse(window.winfo_exists())
        self.assertEqual(self.presets(), [])
        self.assertEqual(next_settings(self.directory)['settings']['voice'], 'Trúc Ly')

    def test_a_preset_is_for_every_link_and_never_holds_the_google_drive_choice(self):
        window = self.dialog()
        self.change_boxes(window)
        window.apply()
        self.save_as(window, 'Mẫu A')
        settings = self.presets()[0]['settings']
        self.assertFalse({'drive_upload', 'drive_data_dir'} & set(settings))
        self.assertTrue((self.data/'config-presets.json').is_file())  # In the data folder, not in one link's folder.
        self.assertFalse(list(self.directory.glob('config-presets*')))

    def test_applying_a_preset_only_fills_the_boxes_until_save_is_pressed(self):
        first = self.dialog()
        self.change_boxes(first)
        first.apply()
        self.save_as(first, 'Mẫu A')
        saved = next_settings(self.directory)
        second = self.dialog()
        self.change_boxes(second, voice='Mai Anh', volume='80')
        second.preset_choice.set('Mẫu A')
        second.update_preset_buttons()
        second.apply_preset()
        self.assertEqual((second.fields['voice'].get(), second.fields['original_volume'].get()), ('Trúc Ly', '30'))
        self.assertIn('Chưa lưu cho link', second.status.get())
        self.assertEqual(next_settings(self.directory), saved)  # Nothing was saved by applying.
        second.fields['voice'].set('Mai Anh')
        second.apply()  # Saving is a separate step, as always.
        self.assertEqual(next_settings(self.directory)['settings']['voice'], 'Mai Anh')

    def test_applying_keeps_the_links_own_google_drive_boxes(self):
        first = self.dialog()
        self.change_boxes(first)
        first.apply()
        self.save_as(first, 'Mẫu A')
        second = self.dialog()
        before = (second.fields['drive_enabled'].get(), dict(second.drive_options))
        second.preset_choice.set('Mẫu A')
        second.apply_preset()
        self.assertEqual((second.fields['drive_enabled'].get(), dict(second.drive_options)), before)

    def test_the_same_name_asks_before_replacing_and_the_other_presets_stay(self):
        from unittest.mock import patch
        window = self.dialog()
        self.change_boxes(window)
        window.apply()
        self.save_as(window, 'Mẫu A')
        window = self.dialog()
        self.change_boxes(window, voice='Mai Anh', volume='80')
        window.offer_current()
        with patch('teodub.campaign_config_ui.messagebox.askyesno', return_value=False) as ask:
            self.save_as(window, 'mẫu a')
        self.assertTrue(ask.called)
        self.assertEqual(self.presets()[0]['settings']['voice'], 'Trúc Ly')  # Refused: the old one stays.
        self.assertIsNotNone(window.preset_offer)
        with patch('teodub.campaign_config_ui.messagebox.askyesno', return_value=True):
            self.save_as(window, 'mẫu a')
        rows = self.presets()
        self.assertEqual((len(rows), rows[0]['settings']['voice']), (1, 'Mai Anh'))

    def test_a_bad_name_is_refused_and_asked_again(self):
        window = self.dialog()
        self.change_boxes(window)
        window.apply()
        for bad in ('', '   ', 'x'*61):
            self.save_as(window, bad)
            self.assertTrue(window.winfo_exists() and window.preset_offer is not None)
            self.assertEqual(self.presets(), [])
        self.assertTrue(window.status.get())

    def test_save_as_preset_button_keeps_the_boxes_without_saving_the_link(self):
        window = self.dialog()
        self.change_boxes(window)
        window.offer_current()
        self.save_as(window, 'Chưa lưu cho link')
        self.assertEqual(len(self.presets()), 1)
        self.assertIsNone(next_settings(self.directory))
        self.assertTrue(window.winfo_exists())  # The window stays: the link was never saved.

    def test_the_same_settings_are_not_offered_a_second_name(self):
        window = self.dialog()
        self.change_boxes(window)
        window.apply()
        self.save_as(window, 'Mẫu A')
        window = self.dialog()
        window.offer_current()  # The boxes are exactly the saved preset.
        self.assertIsNone(window.preset_offer)
        self.assertIn('đã có sẵn với tên "Mẫu A"', window.status.get())

    def test_rename_and_delete_change_only_the_list(self):
        from unittest.mock import patch
        window = self.dialog()
        self.change_boxes(window)
        window.apply()
        self.save_as(window, 'Mẫu A')
        saved = next_settings(self.directory)
        window = self.dialog()
        window.preset_choice.set('Mẫu A')
        window.update_preset_buttons()
        with patch('teodub.campaign_config_ui.simpledialog.askstring', return_value='Mẫu B'):
            window.rename_preset()
        self.assertEqual([row['name'] for row in self.presets()], ['Mẫu B'])
        self.assertEqual(window.preset_choice.get(), 'Mẫu B')
        with patch('teodub.campaign_config_ui.messagebox.askyesno', return_value=False):
            window.delete_preset()
        self.assertEqual(len(self.presets()), 1)
        with patch('teodub.campaign_config_ui.messagebox.askyesno', return_value=True):
            window.delete_preset()
        self.assertEqual(self.presets(), [])
        self.assertEqual(str(window.preset_apply['state']), 'disabled')
        self.assertEqual(next_settings(self.directory), saved)  # No link was changed.

    def test_a_preset_that_cannot_be_used_says_why_and_leaves_the_links_own_boxes(self):
        from teodub import config_presets
        config_presets.save_preset(self.data, 'Hỏng', dict(voice='Giọng không còn', voice_provider='local'))
        window = self.dialog()
        own = (window.fields['voice'].get(), window.fields['original_volume'].get())
        window.preset_choice.set('Hỏng')
        window.apply_preset()
        self.assertIn('Chưa áp dụng được cấu hình "Hỏng"', window.status.get())
        self.assertEqual((window.fields['voice'].get(), window.fields['original_volume'].get()), own)
        self.assertIsNone(next_settings(self.directory))

    def test_a_damaged_presets_file_means_an_empty_list_and_the_window_still_opens(self):
        (self.data/'config-presets.json').write_text('{broken', encoding='utf-8')
        window = self.dialog()
        self.assertEqual(window.presets, [])
        self.assertEqual(str(window.preset_apply['state']), 'disabled')
        window.apply_preset()
        self.assertIn('Chọn một cấu hình', window.status.get())


if __name__ == '__main__':
    unittest.main()
