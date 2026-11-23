"""23/11/2026 (owner): Xóa tài khoản. Tèo's list only, nothing on Facebook; refused while its Chrome is open or a post is being sent;
unsent schedules cancelled, history kept; AI auto-planning of its Pages off; its Chrome profile deleted; other accounts untouched.
Disposable folders only; no browser, no Facebook."""
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub import facebook_account_removal as removal
from teodub.facebook_ui import FacebookTab
from teodub.page_schedule import campaign_video_source
from tests import test_page_schedule as fixtures


class FakeAI:
    def __init__(self, data_dir, enabled=()):
        self.enabled, self.paused = list(enabled), []

    def policies(self):
        return self.enabled

    def pause(self, account, page):
        self.paused.append((account, page))


class RemovalTests(unittest.TestCase):
    setUp = fixtures.ScheduleTests.setUp
    video = fixtures.ScheduleTests.video
    schedule = fixtures.ScheduleTests.schedule
    submit = fixtures.ScheduleTests.submit

    def profile(self, account):
        path = self.accounts.directory/'browser-profiles'/account/'chrome'
        path.mkdir(parents=True)
        (path/'Cookies').write_bytes(b'x'*1000)
        return path.parent

    def ai(self, enabled=()):
        fake = FakeAI(None, enabled)
        self.enterContext(patch.object(removal, 'PageAIStore', return_value=fake))
        return fake

    def test_unsent_schedules_are_cancelled_history_kept_ai_off_profile_deleted(self):
        armed, saved = self.schedule('a'*32), self.schedule('b'*32)
        self.controller.start(armed['id'])
        sent = self.store.update(self.schedule('c'*32)['id'], state='submitted', attempted_at='2026-11-20T10:00:00+07:00')
        source = campaign_video_source(self.directory, *self.video(campaign_id='d'*32))
        other = self.store.create(self.other, self.other_page, source, 'Khác', '#x', time.time()+7200)
        profile, other_profile = self.profile(self.account), self.profile(self.other)
        ai = self.ai([dict(account_id=self.account, page_key=self.page), dict(account_id=self.other, page_key=self.other_page)])
        plan = removal.removal_plan(self.directory, self.account, self.store)
        self.assertEqual((len(plan['pages']), sorted(plan['unsent']), plan['ai'], plan['busy']),
                         (1, sorted([armed['id'], saved['id']]), [self.page], 0))
        text = removal.confirm_text(plan)
        self.assertIn('2 lịch chưa đăng sẽ bị hủy', text)
        self.assertIn('Không xóa gì trên Facebook', text)
        result = removal.remove(self.controller, self.directory, self.account, plan)
        self.assertEqual((result['cancelled'], result['ai'], result['profile_failed']), (2, 1, []))
        self.assertEqual([self.store.get(i)['state'] for i in (armed['id'], saved['id'])], ['cancelled', 'cancelled'])
        self.assertEqual(self.store.get(sent['id'])['state'], 'submitted')  # Sent: stays as history.
        self.assertEqual(ai.paused, [(self.account, self.page)])
        data = self.accounts.load()
        self.assertEqual([row['id'] for row in data['accounts']], [self.other])  # Only the other account is left …
        self.assertEqual([row['id'] for row in data['pages']], [self.other_page])
        self.assertFalse(profile.exists())
        self.assertTrue(other_profile.exists())  # … with its profile …
        self.assertEqual(self.store.get(other['id'])['state'], 'scheduled')  # … and its schedules.
        self.assertIn('Không có gì bị xóa trên Facebook', removal.done_text(plan, result))

    def test_a_post_being_sent_blocks_the_removal(self):
        row = self.store.update(self.schedule('a'*32)['id'], state='submitting', attempted_at='2026-11-23T10:00:00+07:00')
        self.ai()
        plan = removal.removal_plan(self.directory, self.account, self.store)
        self.assertEqual(plan['busy'], 1)
        with self.assertRaisesRegex(ValueError, 'đang có bài'):
            removal.remove(self.controller, self.directory, self.account, plan)
        self.assertIn(self.account, [r['id'] for r in self.accounts.load()['accounts']])  # Nothing removed.
        self.assertEqual(self.store.get(row['id'])['state'], 'submitting')

    def test_a_schedule_that_cannot_be_cancelled_keeps_the_account(self):
        armed = self.schedule('a'*32)
        self.ai()
        plan = removal.removal_plan(self.directory, self.account, self.store)
        with patch.object(self.controller, 'cancel', side_effect=ValueError('Bản nháp đã có thao tác')):
            with self.assertRaisesRegex(ValueError, 'chưa xóa tài khoản'):
                removal.remove(self.controller, self.directory, self.account, plan)
        self.assertIn(self.account, [r['id'] for r in self.accounts.load()['accounts']])
        self.assertEqual(self.store.get(armed['id'])['state'], 'scheduled')

    def test_an_account_without_pages_or_profile_is_simply_removed(self):
        empty = self.accounts.add_account('phú thọ')['id']
        self.ai()
        plan = removal.removal_plan(self.directory, empty, self.store)
        self.assertEqual((plan['pages'], plan['unsent'], plan['profile_mb']), ([], [], 0.0))
        result = removal.remove(self.controller, self.directory, empty, plan)
        self.assertEqual(result, dict(cancelled=0, ai=0, profile_failed=[]))
        self.assertNotIn(empty, [r['id'] for r in self.accounts.load()['accounts']])

    def test_the_selected_account_moves_to_another_one(self):
        self.accounts.select_account(self.account)
        self.ai()
        removal.remove(self.controller, self.directory, self.account, removal.removal_plan(self.directory, self.account, self.store))
        self.assertEqual(self.accounts.load()['selected_account_id'], self.other)


class RemovalUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.studio = SimpleNamespace(data_dir=Path(self.temp.name), closing=False, closed=False,
            login_pending=False, campaign_busy=True, process=object())
        self.enterContext(patch('teodub.facebook_ui.create_page_bridge', return_value=Mock(enabled=True)))
        notebook = ttk.Notebook(self.root)
        self.tab = FacebookTab(notebook, self.studio)
        notebook.add(self.tab, text='Quản lý Page')
        self.addCleanup(self.root.destroy)
        self.enterContext(patch.object(removal, 'PageAIStore', return_value=FakeAI(None)))

    def account(self, name):
        with patch('teodub.facebook_ui.simpledialog.askstring', return_value=name):
            self.assertTrue(self.tab.add_account())
        return self.tab.account_id

    def test_every_row_has_a_delete_cell_and_yes_removes_the_account(self):
        first, second = self.account('Một'), self.account('Hai')
        self.assertEqual(self.tab.account_tree.item(first)['values'][3], '✕ Xóa')
        with patch('teodub.facebook_ui.messagebox.askyesno', return_value=True) as ask:
            self.assertTrue(self.tab.delete_account(second))
        self.assertIn('Xóa tài khoản "Hai" khỏi Tèo?', ask.call_args[0][1])
        self.assertEqual(self.tab.account_tree.get_children(), (first,))
        self.assertEqual(self.tab.account_id, first)
        self.assertIn('Đã xóa tài khoản "Hai"', self.tab.status.get())

    def test_no_keeps_everything(self):
        first = self.account('Một')
        with patch('teodub.facebook_ui.messagebox.askyesno', return_value=False):
            self.assertFalse(self.tab.delete_account(first))
        self.assertEqual(self.tab.account_tree.get_children(), (first,))

    def test_an_open_chrome_blocks_without_asking(self):
        first = self.account('Một')
        self.tab.sessions[first] = SimpleNamespace(pending=True)
        with patch('teodub.facebook_ui.messagebox.askyesno') as ask:
            self.assertFalse(self.tab.delete_account(first))
        ask.assert_not_called()
        self.assertIn('Chrome của tài khoản này đang mở', self.tab.status.get())
        self.assertEqual(self.tab.account_tree.get_children(), (first,))
        self.tab.sessions.pop(first)  # Only a stand-in: the real closing of the tab must not see it.

    def test_the_menu_has_the_delete_entry(self):
        self.account('Một')
        labels = [self.tab.account_menu.entrycget(i, 'label') for i in range(self.tab.account_menu.index('end')+1)]
        self.assertIn('Xóa tài khoản…', labels)


if __name__ == '__main__':
    unittest.main()
