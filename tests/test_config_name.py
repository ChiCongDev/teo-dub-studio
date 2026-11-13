"""Tên cấu hình đang dùng trong "Chỉnh sửa cấu hình" (owner 13/11/2026). Display only; local fixtures only."""
import unittest

from teodub import config_presets as presets_module
from teodub.config_presets import OLD_NOTE, OWN, lacks_removal, matching_name, save_preset, state_text
from test_campaign_voice_ui import LinkFixture

A = dict(voice='A', voice_gain=1.0)
B = dict(voice='B', voice_gain=1.4)
PRESETS = [dict(name='Mẫu A', settings=A), dict(name='Mẫu B', settings=B)]


class TextTests(unittest.TestCase):
    def test_the_name_of_the_saved_config_the_boxes_equal(self):
        self.assertEqual(matching_name(PRESETS, dict(B, extra=1)), 'Mẫu B')
        self.assertIsNone(matching_name(PRESETS, dict(voice='B', voice_gain=1.0)))
        self.assertIsNone(matching_name(PRESETS, None))

    def test_an_old_config_without_the_voice_removal_setting_still_names_the_boxes(self):
        old = [dict(name='Cũ', settings=A)]
        self.assertEqual(matching_name(old, dict(A, remove_original_voice=False)), 'Cũ')
        self.assertEqual(matching_name(old, dict(A, remove_original_voice=True)), 'Cũ')
        self.assertTrue(lacks_removal(old, 'Cũ', dict(A, remove_original_voice=True)))
        self.assertFalse(lacks_removal(old, 'Cũ', dict(A, remove_original_voice=False)))
        new = [dict(name='Mới', settings=dict(A, remove_original_voice=True))]
        self.assertEqual(matching_name(new, dict(A, remove_original_voice=True)), 'Mới')
        self.assertIsNone(matching_name(new, dict(A, remove_original_voice=False)))
        self.assertFalse(lacks_removal(new, 'Mới', dict(A, remove_original_voice=True)))

    def test_a_config_that_states_the_setting_is_preferred_over_an_old_one(self):
        both = [dict(name='Mới', settings=dict(A, remove_original_voice=True)), dict(name='Cũ', settings=A)]  # the old one saved last
        self.assertEqual(matching_name(both, dict(A, remove_original_voice=True)), 'Mới')

    def test_the_sentence_tells_when_an_old_config_lacks_the_setting(self):
        old = [dict(name='Cũ', settings=A)]
        on = dict(A, remove_original_voice=True)
        self.assertEqual(state_text(old, on, on), 'Đang dùng: "Cũ"'+OLD_NOTE)
        self.assertEqual(state_text(old, A, A), 'Đang dùng: "Cũ"')
        self.assertIn(OLD_NOTE, state_text(old, A, on))

    def test_the_four_sentences(self):
        self.assertEqual(state_text(PRESETS, A, A), 'Đang dùng: "Mẫu A"')
        self.assertEqual(state_text(PRESETS, dict(voice='Z'), dict(voice='Z')), f'Đang dùng: {OWN}')
        self.assertIn('đã sửa so với mẫu', state_text(PRESETS, A, dict(A, voice_gain=0.5)))
        self.assertIn('chưa lưu cho link', state_text(PRESETS, A, dict(A, voice_gain=0.5)))
        self.assertTrue(state_text(PRESETS, A, B).startswith('Các ô đang giống cấu hình "Mẫu B"'))
        self.assertEqual(state_text(PRESETS, dict(voice='Z'), dict(voice='Y')), f'Đang dùng: {OWN}, đã sửa — các ô đã đổi, chưa lưu cho link')

    def test_unreadable_boxes_say_the_saved_state(self):
        self.assertEqual(state_text(PRESETS, A, None), 'Đang dùng: "Mẫu A"')


class WindowTests(LinkFixture):
    def dialog(self):
        self.link.open_config()
        window = self.link.config_window
        self.addCleanup(lambda: window.winfo_exists() and window.close())
        return window

    def test_a_link_without_a_matching_config_says_it_is_its_own(self):
        window = self.dialog()
        self.assertEqual(window.preset_choice.get(), '')
        self.assertEqual(window.preset_state.get(), f'Đang dùng: {OWN}')

    def test_the_name_shows_in_the_box_when_the_links_settings_equal_a_saved_config(self):
        first = self.dialog()
        settings = presets_module.preset_settings(first.capture())
        first.close()
        save_preset(self.data, 'Mẫu của tôi', settings)
        window = self.dialog()
        self.assertEqual(window.preset_choice.get(), 'Mẫu của tôi')
        self.assertEqual(window.preset_state.get(), 'Đang dùng: "Mẫu của tôi"')
        self.assertEqual(str(window.preset_apply['state']), 'normal')

    def test_an_edit_changes_the_sentence_and_putting_it_back_restores_it(self):
        first = self.dialog()
        settings = presets_module.preset_settings(first.capture())
        first.close()
        save_preset(self.data, 'Mẫu của tôi', settings)
        window = self.dialog()
        window.fields['remove_original_voice'].set(True)
        window.show_state()
        self.assertIn('đã sửa so với mẫu', window.preset_state.get())
        self.assertIn('Mẫu của tôi', window.preset_state.get())
        window.fields['remove_original_voice'].set(False)
        window.show_state()
        self.assertEqual(window.preset_state.get(), 'Đang dùng: "Mẫu của tôi"')

    def test_nothing_is_saved_or_changed_by_the_label(self):
        from teodub.campaign_settings import next_settings
        window = self.dialog()
        window.fields['remove_original_voice'].set(True)
        window.show_state()
        self.assertIsNone(next_settings(self.directory))

    def test_after_saving_the_saved_state_is_what_the_boxes_say(self):
        window = self.dialog()
        window.fields['remove_original_voice'].set(True)
        window.show_state()
        self.assertIn('chưa lưu cho link', window.preset_state.get())
        window.apply()
        window.skip_preset() if window.winfo_exists() else None
        if window.winfo_exists():
            window.show_state()
            self.assertNotIn('chưa lưu cho link', window.preset_state.get())

    def test_the_watch_stops_with_the_window(self):
        window = self.dialog()
        window.close()
        window.watch_state()  # No error on a closed window.


if __name__ == '__main__':
    unittest.main()
