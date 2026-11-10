"""Đổi giọng on a link's tab (owner 05/11/2026): only that link's voice changes, nothing is ticked by default, and finished videos
are made again only when asked. Local fixtures only; no worker, network or AI."""
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import time
import unittest

from teodub import campaign
from teodub.campaign_batch_controller import CampaignBatchController
from teodub.campaign_settings import next_settings
from teodub.campaign_voice_ui import ChangeVoiceDialog, voice_choices
from teodub.common import atomic_json


class LinkFixture(unittest.TestCase):
    """A link with four videos on a local data folder; no tests of its own (test_campaign_config_ui builds on it too)."""
    def setUp(self):
        from teodub.app import Studio
        from teodub.campaign_tabs_ui import CampaignArea
        from teodub.campaign_ui import CampaignTab
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name).resolve()
        self.root = tk.Tk()
        self.root.withdraw()
        self.notebook = ttk.Notebook(self.root)
        single = ttk.Frame(self.notebook)
        self.notebook.add(single, text='Video đơn')
        self.studio = Studio(self.root, str(self.data), parent=single)
        self.area = CampaignArea(self.notebook, self.studio)
        self.studio.campaign_area = self.area
        self.tab = CampaignTab(self.area.tabs, self.studio)
        self.notebook.add(self.area, text='Chiến dịch hàng loạt')
        self.studio.batch_controller = CampaignBatchController(self.studio, self.tab)
        self.area.attach(self.tab)
        self.addCleanup(self.cleanup)
        self.tab.original_dir.set(str(self.data/'originals'))
        self.tab.output_dir.set(str(self.data/'outputs'))
        links = '\n'.join(f'https://www.douyin.com/video/76900000000000000{n:02d}' for n in range(1, 5))
        self.tab.source.insert('1.0', links)
        self.tab.name.set('Kênh A')
        self.tab.create()
        self.directory = self.tab.directory
        self.link = self.area.links[str(self.directory)]
        self.link.refresh(full=True)

    def cleanup(self):
        for owner in (self.tab, self.area):
            for name in ('timer', 'auto_timer'):
                if getattr(owner, name, None) is not None:
                    owner.after_cancel(getattr(owner, name))
        self.studio.process = None
        self.studio.finish_close()

    def finish(self, numbers):
        manifest = campaign.read_json(self.directory/'campaign.json')
        for item in manifest['items']:
            if item['number'] in numbers:
                item.update(status='completed', message='Xong', outputs=dict(video=str(self.data/f'v{item["number"]}.mp4')))
        if all(item['status'] == 'completed' for item in manifest['items']):
            manifest['status'] = 'completed'  # As the worker leaves a link with nothing left to make.
        atomic_json(self.directory/'campaign.json', manifest)


class ChangeVoiceTests(LinkFixture):
    def dialog(self):
        window = ChangeVoiceDialog(self.link)
        self.addCleanup(lambda: window.winfo_exists() and window.close())
        return window

    def test_the_button_is_on_the_link_tab_and_opens_one_window(self):
        self.assertEqual(str(self.link.voice_button['text']), 'Giọng & âm lượng…')
        self.link.open_voice()
        first = self.link.voice_window
        self.link.open_voice()
        self.assertIs(self.link.voice_window, first)
        self.link.close()
        self.assertFalse(first.winfo_exists())

    def test_the_list_has_tèo_voices_and_nothing_is_ticked_or_changed_by_opening_it(self):
        window = self.dialog()
        labels = list(window.choices)
        self.assertIn('Ngọc Huyền (giọng có sẵn)', labels)
        self.assertEqual(str(window.apply_button['state']), 'normal')
        self.assertFalse(window.redo.get())
        self.assertEqual(window.choice.get(), '(Giữ giọng hiện tại)')
        self.assertEqual((window.original.get(), window.voice_level.get()), ('10', '100'))  # The link's own levels.
        self.assertIsNone(next_settings(self.directory))
        window.apply()  # Nothing changed.
        self.assertIn('Chưa đổi gì', window.status.get())
        self.assertIsNone(next_settings(self.directory))

    def test_only_the_voice_changes_and_nothing_finished_is_touched_without_the_tick(self):
        self.finish({1})
        before = campaign.read_json(self.directory/'campaign.json')['items'][0]
        window = self.dialog()
        window.choice.set('Trúc Ly (giọng có sẵn)')
        window.apply()
        update = next_settings(self.directory)
        self.assertEqual((update['settings']['voice'], update['settings']['voice_provider']), ('Trúc Ly', 'local'))
        self.assertEqual(update['settings']['reference_audio'], '')
        item = campaign.read_json(self.directory/'campaign.json')['items'][0]
        self.assertEqual(item, before)  # The finished video is left alone.
        self.assertFalse(list(self.directory.glob('redo-*.json')))
        self.assertIn('Đã đổi giọng', self.link.message.get())
        self.assertFalse(window.winfo_exists())

    def test_the_tick_also_asks_for_the_finished_videos_to_be_made_again(self):
        self.finish({1, 2})
        window = self.dialog()
        window.choice.set('Trúc Ly (giọng có sẵn)')
        window.redo.set(True)
        window.check()
        self.assertIn('Làm lại được: 2', window.result.get('1.0', 'end'))
        window.apply()
        items = {item['number']: item for item in campaign.read_json(self.directory/'campaign.json')['items']}
        self.assertTrue(items[1].get('redo') and items[2].get('redo'))
        self.assertFalse(items[3].get('redo'))
        self.assertIn('Đã xếp làm lại 2 video', self.link.message.get())

    def test_a_link_with_everything_finished_still_takes_a_voice_for_remaking(self):
        self.finish({1, 2, 3, 4})
        window = self.dialog()
        window.choice.set('Mai Anh (giọng có sẵn)')
        window.voice_level.set('150')
        window.apply()  # Without the tick there is nothing left to make: refused, nothing saved.
        self.assertIn('Chưa áp dụng', window.status.get())
        self.assertIsNone(next_settings(self.directory))
        window.redo.set(True)
        window.apply()
        self.assertEqual(next_settings(self.directory)['settings']['voice'], 'Mai Anh')
        manifest = campaign.read_json(self.directory/'campaign.json')
        self.assertEqual(sum(bool(item.get('redo')) for item in manifest['items']), 4)
        self.assertEqual(manifest['status'], 'paused')

    def test_choices_follow_the_links_own_reading_style_and_engine(self):
        config = dict(tts_model='v3nano', reading_style='conversation', voice_speed=1.1)
        for label, selection in voice_choices(self.data, config):
            if label.startswith('Mai Anh'):
                self.assertEqual((selection['tts_model'], selection['reading_style'], selection['voice_speed']),
                                 ('v3nano', 'conversation', 1.1))
                break
        else:
            self.fail('Mai Anh is missing')

    def test_only_the_levels_change_and_the_voice_stays(self):
        window = self.dialog()
        window.original.set('30')
        window.voice_level.set('150,5')  # Vietnamese decimals are accepted.
        window.apply()
        settings = next_settings(self.directory)['settings']
        self.assertEqual((settings['original_gain'], settings['voice_gain']), (.3, 1.505))
        self.assertEqual((settings['voice'], settings['voice_provider']), ('Mai Anh', 'local'))  # The voice was not asked to change.
        self.assertIn('Đã đổi âm lượng', self.link.message.get())

    def test_voice_and_levels_together_and_a_level_outside_the_range_is_refused(self):
        window = self.dialog()
        window.original.set('250')
        window.apply()
        self.assertIn('0–200%', window.status.get())
        self.assertIsNone(next_settings(self.directory))
        window.original.set('0')  # 0 deliberately mutes the original sound.
        window.choice.set('Trúc Ly (giọng có sẵn)')
        window.apply()
        settings = next_settings(self.directory)['settings']
        self.assertEqual((settings['voice'], settings['original_gain'], settings['voice_gain']), ('Trúc Ly', 0.0, 1.0))
        self.assertIn('giọng và âm lượng', self.link.message.get())

    def test_the_tick_with_only_levels_asks_for_the_finished_videos_again(self):
        self.finish({1, 2})
        window = self.dialog()
        window.voice_level.set('120')
        window.redo.set(True)
        window.apply()
        items = {item['number']: item for item in campaign.read_json(self.directory/'campaign.json')['items']}
        self.assertTrue(items[1].get('redo') and items[2].get('redo'))
        self.assertEqual(next_settings(self.directory)['settings']['voice_gain'], 1.2)

    # 10/11/2026: the Sub buttons of the tab are gone; the tab only shows the Sub the link uses.
    def test_a_link_with_everything_finished_still_shows_its_sub_in_a_read_only_line(self):
        self.finish({1, 2, 3, 4})
        self.link.refresh(full=True)
        self.assertEqual(self.link.sub_summary.get(), 'Sub Việt: không ghi vào hình (đổi trong Chỉnh sửa cấu hình…)')
        for gone in ('burn_check', 'box_picker', 'subtitle_button', 'subtitle_redo_button', 'apply_subtitles', 'open_subtitle_redo'):
            self.assertFalse(hasattr(self.link, gone), gone)

    def test_the_listening_test_needs_a_finished_video_and_changes_nothing(self):
        window = self.dialog()
        window.preview()  # Nothing finished yet: the thread reports why.
        for _ in range(200):
            self.root.update()
            if not window.previewing:
                break
            time.sleep(.05)
        self.assertFalse(window.previewing)
        self.assertIn('Chưa nghe thử được', window.status.get())
        self.assertIsNone(next_settings(self.directory))
        window.voice_level.set('abc')
        window.preview()
        self.assertIn('200%', window.status.get())
