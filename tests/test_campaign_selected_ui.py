"""Chạy video đã chọn on a link's tab (owner 13/11/2026): the button, the confirmation window, the row mark. No worker is started
(subprocess.Popen is patched), no network, Chrome or AI."""
from unittest.mock import Mock, patch
import unittest

from teodub import campaign, campaign_batch
from tests import test_campaign_link_tabs as fixtures


class SelectedUITests(unittest.TestCase):
    setUp = fixtures.LinkTabsUITests.setUp
    cleanup = fixtures.LinkTabsUITests.cleanup
    create = fixtures.LinkTabsUITests.create

    def link(self, count=3):
        directory = self.create('\n'.join(f'https://www.douyin.com/video/{100+n}' for n in range(count)), 'Kênh A')
        link = self.area.links[str(directory)]
        link.refresh(full=True)
        return directory, link

    def state(self, widget):
        return str(widget['state'])

    def test_the_button_is_off_with_no_choice_and_on_with_one_and_chay_is_as_before(self):
        directory, link = self.link()
        self.assertEqual(self.state(link.selected_button), 'disabled')
        link.tree.selection_set(['2'])
        link.update_selected_button()
        self.assertEqual(self.state(link.selected_button), 'normal')
        self.assertEqual(self.state(link.run_button), 'normal')
        link.tree.selection_set([])
        link.update_selected_button()
        self.assertEqual(self.state(link.selected_button), 'disabled')

    def test_the_button_is_off_while_a_single_video_runs(self):
        directory, link = self.link()
        link.tree.selection_set(['1'])
        self.studio.process = Mock()
        link.update_selected_button()
        self.assertEqual(self.state(link.selected_button), 'disabled')
        self.studio.process = None

    def test_the_choice_is_read_in_the_order_the_table_shows(self):
        directory, link = self.link()
        link.tree.selection_set(['3', '1'])
        self.assertEqual(link.selected_numbers(), [1, 3])

    def test_after_the_confirmation_the_chosen_go_first_and_the_link_runs(self):
        directory, link = self.link()
        link.tree.selection_set(['3', '2'])
        child = Mock()
        child.poll.return_value = None
        with patch('teodub.campaign_tabs_ui.messagebox') as box, patch('subprocess.Popen', return_value=child) as spawn:
            box.askokcancel.return_value = True
            link.run_selected()
            self.assertIn('--campaign-batch-worker', spawn.call_args.args[0])
        text = box.askokcancel.call_args.args[1]
        self.assertIn('2 video chưa làm xong: làm trước: 2, 3', text)
        self.assertEqual(campaign.read_json(directory/'campaign.json')['priority'], [2, 3])
        self.assertIn('Đã xếp 2 video chạy trước', link.message.get())

    def test_cancelling_the_confirmation_changes_nothing(self):
        directory, link = self.link()
        link.tree.selection_set(['2'])
        before = (directory/'campaign.json').read_bytes()
        with patch('teodub.campaign_tabs_ui.messagebox') as box, patch('subprocess.Popen') as spawn:
            box.askokcancel.return_value = False
            link.run_selected()
        spawn.assert_not_called()
        self.assertEqual((directory/'campaign.json').read_bytes(), before)

    def test_when_nothing_chosen_can_be_done_the_reasons_are_shown_and_nothing_runs(self):
        directory, link = self.link()
        link.tree.selection_set(['1'])
        answer = dict(entries=[dict(number=1, kind='skip', reason='Đã đăng hoặc đã gửi lên Page; bản trên Facebook không đổi được.',
                                    scheduled=False)], numbers=[])
        with patch('teodub.campaign_selected.plan', return_value=answer), patch('teodub.campaign_tabs_ui.messagebox') as box, \
                patch('subprocess.Popen') as spawn:
            link.run_selected()
        box.askokcancel.assert_not_called()
        self.assertIn('Video 1: bỏ qua. Đã đăng hoặc đã gửi lên Page', box.showinfo.call_args.args[1])
        spawn.assert_not_called()

    def test_a_chosen_video_is_marked_in_its_row_and_in_the_status_line(self):
        directory, link = self.link()
        manifest = campaign.read_json(directory/'campaign.json')
        manifest['priority'] = [2]
        from teodub.common import atomic_json
        atomic_json(directory/'campaign.json', manifest)
        link.manifest_key = None
        link.refresh(full=True)
        self.assertTrue(link.tree.item('2', 'values')[-1].startswith('Chạy trước · '))
        self.assertFalse(link.tree.item('1', 'values')[-1].startswith('Chạy trước'))
        self.assertIn('Chạy trước 1', link.status.get())


if __name__ == '__main__':
    unittest.main()
