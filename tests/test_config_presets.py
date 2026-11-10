"""Cấu hình đã lưu (owner 10/11/2026): the file of named presets. Local temp folder only."""
from pathlib import Path
import tempfile
import unittest

from teodub import config_presets as presets


class PresetFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def test_names_are_trimmed_and_checked(self):
        self.assertEqual(presets.clean_name('  Review   sản phẩm '), 'Review sản phẩm')
        for bad in ('', '   ', None, 'x'*61, 'a\x00b'):
            with self.assertRaises(ValueError):
                presets.clean_name(bad)
        self.assertEqual(presets.clean_name('x'*60), 'x'*60)

    def test_a_preset_never_holds_the_google_drive_choice(self):
        kept = presets.preset_settings(dict(voice='A', drive_upload=dict(enabled=True), drive_data_dir='x', speed=.9))
        self.assertEqual(kept, dict(voice='A', speed=.9))

    def test_save_find_replace_rename_delete(self):
        self.assertEqual(presets.read_presets(self.data), [])
        first = presets.save_preset(self.data, 'Mẫu A', dict(voice='A'))
        second = presets.save_preset(self.data, 'Mẫu B', dict(voice='B'))
        self.assertEqual([row['name'] for row in presets.read_presets(self.data)], ['Mẫu A', 'Mẫu B'])
        with self.assertRaises(ValueError):
            presets.save_preset(self.data, 'mẫu  a', dict(voice='Z'))  # Same name in another letter case: refused unless replacing.
        again = presets.save_preset(self.data, 'mẫu  a', dict(voice='Z'), replace=True)
        self.assertEqual(again['id'], first['id'])  # The same entry, in the same place.
        rows = presets.read_presets(self.data)
        self.assertEqual([(row['name'], row['settings']['voice']) for row in rows], [('mẫu a', 'Z'), ('Mẫu B', 'B')])
        self.assertIs(presets.find_by_name(rows, ' MẪU A ') is not None, True)
        self.assertEqual(presets.find_same(rows, dict(voice='B'))['id'], second['id'])
        self.assertIsNone(presets.find_same(rows, dict(voice='nothing')))
        with self.assertRaises(ValueError):
            presets.rename_preset(self.data, first['id'], 'Mẫu B')
        presets.rename_preset(self.data, first['id'], 'Mẫu C')
        presets.delete_preset(self.data, second['id'])
        self.assertEqual([row['name'] for row in presets.read_presets(self.data)], ['Mẫu C'])
        with self.assertRaises(ValueError):
            presets.delete_preset(self.data, second['id'])

    def test_a_damaged_file_or_entry_is_ignored_never_fatal(self):
        (self.data/presets.PRESETS_FILE).write_text('{broken', encoding='utf-8')
        self.assertEqual(presets.read_presets(self.data), [])
        (self.data/presets.PRESETS_FILE).write_text('{"presets": "x"}', encoding='utf-8')
        self.assertEqual(presets.read_presets(self.data), [])
        (self.data/presets.PRESETS_FILE).write_text(
            '{"presets": [1, {"id": "a", "name": "", "settings": {}}, {"id": "b", "name": "Tốt", "settings": {"voice": "A"}}, '
            '{"id": "c", "name": "Thiếu"}]}', encoding='utf-8')
        self.assertEqual([row['name'] for row in presets.read_presets(self.data)], ['Tốt'])
        self.assertEqual(presets.save_preset(self.data, 'Mới', dict(voice='B'))['name'], 'Mới')  # A damaged entry never blocks a save.

    def test_the_list_has_a_limit_and_settings_that_cannot_be_kept_are_refused(self):
        for number in range(presets.MAX_PRESETS):
            presets.save_preset(self.data, f'Mẫu {number}', dict(voice=str(number)))
        with self.assertRaises(ValueError):
            presets.save_preset(self.data, 'Thêm', dict(voice='x'))
        presets.save_preset(self.data, 'Mẫu 0', dict(voice='đổi'), replace=True)  # Replacing is not adding.
        with self.assertRaises(ValueError):
            presets.save_preset(self.data, 'Mẫu 1', dict(voice=object()), replace=True)
        self.assertEqual(presets.find_by_name(presets.read_presets(self.data), 'Mẫu 1')['settings'], dict(voice='1'))


if __name__ == '__main__':
    unittest.main()
