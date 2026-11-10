"""Cột "Cấu hình áp dụng" and the redo progress of a row (owner 10/11/2026). Local temp folders only; nothing is written to a link."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from teodub import campaign_preset_column as column
from teodub.campaign_tracking import redo_making, row_tag, tracking_row
from teodub.config_presets import save_preset

A = dict(voice='A', voice_gain=1.0)
B = dict(voice='B', voice_gain=1.4)


class MatchTests(unittest.TestCase):
    def test_a_config_matches_the_preset_whose_every_setting_it_has(self):
        presets = [dict(name='Một', settings=A), dict(name='Hai', settings=B)]
        self.assertEqual(column.preset_of(dict(voice='B', voice_gain=1.4, extra=1), presets), 'Hai')
        self.assertIsNone(column.preset_of(dict(voice='B', voice_gain=1.0), presets))
        self.assertIsNone(column.preset_of(None, presets))

    def test_when_two_presets_match_the_one_saved_last_wins(self):
        presets = [dict(name='Cũ', settings=A), dict(name='Mới', settings=A)]
        self.assertEqual(column.preset_of(A, presets), 'Mới')


class CellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name).resolve()
        self.directory = self.data/'campaigns'/'c1'
        (self.directory/'jobs').mkdir(parents=True)
        save_preset(self.data, 'Mẫu A', A)
        save_preset(self.data, 'Mẫu B', B)
        self.column = column.PresetColumn()

    def job(self, number, config, revision=None):
        base = self.directory/'jobs'/f'{number:04}'
        target = base/'revisions'/revision if revision else base
        target.mkdir(parents=True, exist_ok=True)
        (target/'config.json').write_text(json.dumps(config), encoding='utf-8')
        return base, target

    def item(self, number, status, config, **extra):
        base, _target = self.job(number, config)
        return dict(number=number, status=status, job=str(base), url='u', **extra)

    def cells(self, items, pending=None):
        record = None if pending is None else dict(settings=pending)
        with patch('teodub.campaign_settings.next_settings', return_value=record):
            return self.column.cells(self.data, self.directory, dict(items=items))

    def test_each_kind_of_row_says_what_it_uses(self):
        done = self.item(1, 'completed', A)
        custom = self.item(2, 'completed', dict(voice='Z'))
        waiting = self.item(3, 'pending', dict(voice='Z'))
        excluded = self.item(4, 'excluded', A)
        cells = self.cells([done, custom, waiting, excluded], pending=B)
        self.assertEqual(cells, {1: 'Mẫu A', 2: column.NO_PRESET, 3: 'Mẫu B', 4: column.NONE_YET})

    def test_a_video_not_made_yet_uses_its_own_config_when_nothing_is_pending(self):
        self.assertEqual(self.cells([self.item(1, 'pending', B)]), {1: 'Mẫu B'})

    def test_a_finished_video_waiting_to_be_made_again_shows_old_then_new(self):
        redo = self.item(1, 'completed', A, redo=dict(at='x', previous={}))
        self.assertEqual(self.cells([redo], pending=B), {1: 'Mẫu A'+column.ARROW+'Mẫu B'})

    def test_once_the_new_version_is_being_made_the_arrow_ends_at_its_config(self):
        item = self.item(1, 'completed', A)
        _base, target = self.job(1, B, revision='a'*32)
        item.update(execution_job=str(target), redo=dict(at='x', previous={}))
        self.assertEqual(self.cells([item], pending=A), {1: 'Mẫu A'+column.ARROW+'Mẫu B'})

    def test_a_damaged_config_says_so_and_never_raises(self):
        item = self.item(1, 'completed', A)
        (Path(item['job'])/'config.json').write_text('{broken', encoding='utf-8')
        self.assertEqual(self.cells([item]), {1: 'Cần kiểm tra'})

    def test_the_file_is_parsed_again_only_when_it_changes(self):
        item = self.item(1, 'completed', A)
        self.assertEqual(self.cells([item]), {1: 'Mẫu A'})
        first = self.column.config(item['job'])
        self.assertEqual(self.cells([item]), {1: 'Mẫu A'})
        self.assertIs(self.column.config(item['job']), first)  # Same parsed object: the file was not read again.
        self.job(1, dict(voice='B', voice_gain=1.4))  # The file changed: it is read again.
        self.assertEqual(self.cells([item]), {1: 'Mẫu B'})

    def test_a_deleted_preset_leaves_the_row_without_a_name(self):
        item = self.item(1, 'completed', A)
        (self.data/'config-presets.json').unlink()
        self.assertEqual(self.cells([item]), {1: column.NO_PRESET})


class RedoRowTests(unittest.TestCase):
    def item(self, **extra):
        return dict(number=1, status='completed', title='t', url='u', job='/j', completed_at='2026-11-07T08:54:57+07:00', **extra)

    def test_waiting_for_its_turn_is_not_being_made(self):
        item = self.item(redo=dict(at='x', previous=dict(execution_job='/old')), execution_job='/old')
        self.assertFalse(redo_making(item))
        row = tracking_row(item)
        self.assertEqual((row[3], row[4]), ('Hoàn tất · chờ làm lại giọng', '100%'))
        self.assertEqual(row_tag(item), 'completed')

    def test_being_made_shows_its_own_label_and_real_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'events.jsonl').write_text(json.dumps(dict(stage='voice', percent=69, message='Đã tạo giọng 239/343 câu.'))+'\n',
                                                    encoding='utf-8')
            item = self.item(redo=dict(at='x', previous=dict(execution_job='/old')), execution_job=folder)
            self.assertTrue(redo_making(item))
            row = tracking_row(item, active=True)
            self.assertEqual((row[3], row[4]), ('Đang làm lại giọng', '69%'))
            self.assertIn('239/343', row[6])
            self.assertEqual(row_tag(item), 'running')
            self.assertEqual(tracking_row(item, active=False)[3], 'Làm lại giọng · tạm dừng')

    def test_a_finished_video_without_redo_is_unchanged(self):
        row = tracking_row(self.item())
        self.assertEqual((row[3], row[4]), ('Hoàn tất', '100%'))
        self.assertFalse(redo_making(self.item(execution_job='/x')))


if __name__ == '__main__':
    unittest.main()
