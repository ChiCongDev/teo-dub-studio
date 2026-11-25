"""25/11/2026 (owner): "Mở Shopee" opens an account's own profile in a plain Chrome; while it is open a due post WAITS (never fails,
nothing sent) and goes as soon as that Chrome is closed; the owner is asked before Tèo closes it (Đồng ý / Không / 15 minutes).
Local fixtures only: the "open Chrome" is a held lockfile; no Chrome, Facebook or Shopee."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from teodub import page_schedule_shopee, shopee_browser
from teodub.page_schedule_shopee import read_holds
from tests.test_voice_gate import Gate

_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x1
_OPEN_ALWAYS = 4


class HeldLock:
    """Holds `<profile>/lockfile` the way a running Chrome does (no write sharing)."""

    def __init__(self, chrome_dir):
        self.path = Path(chrome_dir)/'lockfile'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                       wintypes.DWORD, wintypes.HANDLE)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel = kernel
        self.handle = kernel.CreateFileW(str(self.path), _GENERIC_WRITE, _FILE_SHARE_READ, None, _OPEN_ALWAYS, 0x80, None)
        assert self.handle not in (None, wintypes.HANDLE(-1).value)

    def release(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            try:
                self.path.unlink()
            except OSError:
                pass


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.data = self.temp/'data'
        self.chrome = self.temp/'profile'/'chrome'

    def hold(self):
        lock = HeldLock(self.chrome)
        self.addCleanup(lock.release)
        return lock

    def fake_popen(self, pid=None):
        return Mock(return_value=SimpleNamespace(pid=pid or os.getpid()))

    def test_a_profile_is_busy_only_while_its_lockfile_is_held(self):
        self.assertFalse(shopee_browser.profile_busy(self.chrome))  # No profile yet.
        lock = self.hold()
        self.assertTrue(shopee_browser.profile_busy(self.chrome))
        lock.release()
        self.assertFalse(shopee_browser.profile_busy(self.chrome))
        self.chrome.mkdir(parents=True, exist_ok=True)
        (self.chrome/'lockfile').write_text('')  # Left by a power cut: nobody holds it.
        self.assertFalse(shopee_browser.profile_busy(self.chrome))
        self.assertTrue((self.chrome/'lockfile').exists())  # Looked at, never removed.

    def test_open_starts_a_plain_chrome_on_the_profile_at_shopee_and_remembers_it(self):
        popen = self.fake_popen()
        pid = shopee_browser.open_shopee(self.data, 'acc', self.chrome, executable=Path('chrome.exe'), popen=popen)
        args = popen.call_args.args[0]
        self.assertEqual(args, ['chrome.exe', '--user-data-dir='+str(self.chrome), '--new-window', shopee_browser.SHOPEE_URL])
        self.assertFalse(any('remote-debugging' in arg or 'enable-automation' in arg for arg in args))  # Not driven by Tèo.
        record = shopee_browser.read_records(self.data)['acc']
        self.assertEqual((record['pid'], record['created']), (pid, shopee_browser.process_created(pid)))

    def test_open_refuses_a_profile_already_held(self):
        self.hold()
        popen = self.fake_popen()
        with self.assertRaisesRegex(ValueError, 'đang mở'):
            shopee_browser.open_shopee(self.data, 'acc', self.chrome, executable=Path('chrome.exe'), popen=popen)
        popen.assert_not_called()

    def test_only_the_chrome_the_button_opened_counts_as_its_own(self):
        shopee_browser.open_shopee(self.data, 'acc', self.chrome, executable=Path('chrome.exe'), popen=self.fake_popen())
        self.assertIsNone(shopee_browser.opened_by_button(self.data, 'acc', self.chrome))  # Not holding the profile: forgotten.
        self.assertEqual(shopee_browser.read_records(self.data), {})
        shopee_browser.open_shopee(self.data, 'acc', self.chrome, executable=Path('chrome.exe'), popen=self.fake_popen())
        self.hold()
        self.assertEqual(shopee_browser.opened_by_button(self.data, 'acc', self.chrome), os.getpid())
        self.assertIsNone(shopee_browser.opened_by_button(self.data, 'other', self.chrome))
        records = shopee_browser.read_records(self.data)
        records['acc']['created'] += 1  # The same pid now names another process (Windows reuses pids).
        shopee_browser._write_records(self.data, records)
        self.assertIsNone(shopee_browser.opened_by_button(self.data, 'acc', self.chrome))

    def test_closing_never_touches_a_process_that_is_not_the_one_opened(self):
        with patch.object(shopee_browser, '_windows_of') as windows:
            self.assertTrue(shopee_browser.close_gently(os.getpid(), 1))  # Other start time: not ours, nothing sent.
        windows.assert_not_called()

    def test_a_chrome_opened_by_hand_is_never_closed_by_teo(self):
        self.hold()
        with patch.object(shopee_browser, 'close_gently') as close:
            self.assertFalse(shopee_browser.close_account_chrome(self.data, 'acc', self.chrome))
        close.assert_not_called()

    def test_no_kill_anywhere(self):
        text = Path(shopee_browser.__file__).read_text(encoding='utf-8')
        for word in ('TerminateProcess', 'taskkill', '.kill(', '.terminate('):
            self.assertNotIn(word, text)


class WaitTests(Gate):
    def setUp(self):
        super().setUp()
        owner = self.controller.owner
        owner.store = self.accounts
        owner.shopee_waiting = Mock()
        owner.chrome_log = Mock()
        self.chrome_dir = self.accounts.profile_directory(self.account)
        self.lock = None

    def open_shopee(self, confirmed=True):
        self.lock = HeldLock(self.chrome_dir)
        self.addCleanup(self.lock.release)
        if confirmed:
            self.controller.shopee_seen = {self.account: time.time()-60}

    def due(self):
        a = self.finished(2)
        first, second = self.armed(a, 2, (5, 6))
        self.due_now(first)
        return first, second

    def test_a_due_post_waits_armed_while_the_shopee_chrome_is_open_and_goes_when_it_closes(self):
        first, second = self.due()
        self.open_shopee()
        old = self.store.get(first)['due_at']
        self.tick()
        row = self.store.get(first)
        self.assertEqual((row['state'], row['due_at']), ('armed', old))  # Waits: nothing prepared, nothing sent.
        self.assertIn('Đang chờ Chrome Shopee', row['message'])
        self.assertIn(first, read_holds(self.store))
        self.controller.owner.shopee_waiting.assert_called_with(self.account)
        self.controller.owner.submit_page_browser.assert_not_called()
        self.store.update(first, due_at=old)  # Ten minutes later it still waits (the 2-minute rule does not drop it).
        self.controller.shopee_seen = {self.account: time.time()-600}
        hold = read_holds(self.store)
        hold[first]['since_at'] -= 600
        page_schedule_shopee.write_holds(self.store, hold)
        self.tick()
        self.assertEqual(self.store.get(first)['state'], 'armed')
        self.assertIn('đã chờ 10 phút', self.store.get(first)['message'])
        self.lock.release()
        self.tick()
        row = self.store.get(first)
        self.assertGreater(row['due_at'], old)  # Given the time now …
        self.assertIn('Chrome Shopee đã đóng: Tèo đăng ngay', row['message'])
        self.assertEqual(read_holds(self.store), {})
        self.tick()
        self.assertEqual(self.store.get(first)['state'], 'preparing')  # … and posted.
        self.assertEqual(self.store.get(second)['state'], 'armed')

    def test_the_first_seconds_only_confirm_the_chrome_is_really_open(self):
        first, _ = self.due()
        self.open_shopee(confirmed=False)
        self.tick()
        self.assertEqual(read_holds(self.store), {})  # Not yet a wait (Tèo's own Chrome may be just closing) …
        self.assertEqual(self.store.get(first)['state'], 'armed')  # … but nothing is started either.
        self.controller.owner.shopee_waiting.assert_not_called()
        self.lock.release()
        self.tick()
        self.assertEqual(self.store.get(first)['state'], 'preparing')  # Gone at once: posted at its own time, not moved.

    def test_a_post_already_missed_is_never_caught_up_through_the_wait(self):
        first, _ = self.due()
        self.store.update(first, due_at=time.time()-600)
        self.open_shopee()
        self.tick()
        self.assertEqual(read_holds(self.store), {})
        row = self.store.get(first)
        self.assertEqual(row['state'], 'needs_attention')  # The usual path for a missed post, as without Shopee.
        self.assertIn('lỡ giờ', row['message'])
        self.controller.owner.submit_page_browser.assert_not_called()

    def test_teos_own_chrome_is_not_a_shopee_chrome(self):
        first, _ = self.due()
        self.open_shopee()
        self.controller.owner.sessions[self.account] = SimpleNamespace(pending=True, loading_pages=False, post_actions={})
        self.tick()
        self.assertEqual(read_holds(self.store), {})
        self.controller.owner.shopee_waiting.assert_not_called()

    def test_without_a_shopee_chrome_nothing_changes(self):
        first, _ = self.due()
        self.tick()
        self.assertEqual(self.store.get(first)['state'], 'preparing')
        self.assertFalse((Path(self.store.path).parent/page_schedule_shopee.HOLDS_FILE).exists())
        self.controller.owner.shopee_waiting.assert_not_called()

    def test_a_wait_given_another_time_by_hand_is_forgotten(self):
        first, _ = self.due()
        self.open_shopee()
        self.tick()
        self.store.update(first, state='paused')
        self.store.reschedule(first, time.time()+3600)
        self.controller.holds_tidied = 0
        self.tick()
        self.assertEqual(read_holds(self.store), {})


class UiTests(unittest.TestCase):
    """The button and the question, on a real Tk window with a fake tab (no Chrome: open/close are patched)."""

    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        from teodub import shopee_ui
        self.ui = shopee_ui
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.temp = Path(tempfile.mkdtemp())
        tab = ttk.Frame(self.root)
        tab.studio = SimpleNamespace(data_dir=self.temp, closing=False, closed=False)
        tab.store = Mock()
        tab.store.load.return_value = dict(accounts=[dict(id='acc', name='tài khoản cá nhân')])
        tab.store.profile_directory.return_value = self.temp/'profile'/'chrome'
        tab.account_id = 'acc'
        tab.sessions = {}
        tab.destroyed = False
        tab.status = Mock()
        tab.chrome_log = Mock()
        tab.posting_now = Mock(return_value=False)
        tab.schedule_busy_within = Mock(return_value=False)
        tab.shopee_asks = {}
        tab.shopee_snooze = {}
        self.tab = tab
        self.open = patch('teodub.shopee_ui.outside_chrome', return_value=True)
        self.outside = self.open.start()
        self.addCleanup(self.open.stop)

    def ask(self, closable=True):
        with patch.object(shopee_browser, 'opened_by_button', return_value=123 if closable else None):
            self.ui.waiting(self.tab, 'acc')
        ask = self.tab.shopee_asks['acc']
        self.addCleanup(lambda: ask.alive() and ask.window.destroy())
        return ask

    def settle(self, ask):
        if ask.closing is not None:
            ask.closing.join(5)
        ask.tick()

    def test_the_question_says_what_was_approved(self):
        ask = self.ask()
        self.assertEqual(ask.window.title(), 'Tới giờ đăng bài')
        self.assertEqual(ask.text.get(), 'Chrome Shopee sẽ tự đóng sau 15 phút.')
        self.assertTrue(ask.clock.get().startswith('Còn lại: 1'))
        labels = [child.cget('text') for child in ask.buttons.winfo_children()]
        self.assertEqual(labels, ['Đồng ý', 'Không'])
        self.ui.waiting(self.tab, 'acc')
        self.assertIs(self.tab.shopee_asks['acc'], ask)  # Asked once, not every second.

    def test_dong_y_closes_the_shopee_chrome_now(self):
        ask = self.ask()
        with patch.object(shopee_browser, 'close_account_chrome', return_value=True) as close:
            ask.yes()
            self.settle(ask)
        close.assert_called_once()
        self.assertFalse(ask.alive())
        self.assertNotIn('acc', self.tab.shopee_asks)

    def test_khong_closes_nothing_and_asks_again_after_15_minutes(self):
        ask = self.ask()
        with patch.object(shopee_browser, 'close_account_chrome') as close:
            ask.no()
        close.assert_not_called()
        self.assertFalse(ask.alive())
        self.assertGreater(self.tab.shopee_snooze['acc'], time.monotonic()+14*60)
        self.ui.waiting(self.tab, 'acc')
        self.assertNotIn('acc', self.tab.shopee_asks)  # Not again before the 15 minutes …
        self.tab.shopee_snooze['acc'] = time.monotonic()-1
        self.ask()  # … then asked again.

    def test_no_answer_for_15_minutes_closes_it(self):
        ask = self.ask()
        ask.deadline = time.monotonic()-1
        with patch.object(shopee_browser, 'close_account_chrome', return_value=True) as close:
            ask.tick()
            self.settle(ask)
        close.assert_called_once()
        self.assertIn('Hết 15 phút', str(self.tab.chrome_log.call_args_list))

    def test_closed_by_the_owner_ends_the_question(self):
        ask = self.ask()
        self.outside.return_value = False
        ask.tick()
        self.assertFalse(ask.alive())
        self.assertNotIn('acc', self.tab.shopee_snooze)

    def test_a_chrome_that_will_not_close_is_left_alone_and_the_owner_told(self):
        ask = self.ask()
        with patch.object(shopee_browser, 'close_account_chrome', return_value=False):
            ask.yes()
            self.settle(ask)
        self.assertTrue(ask.alive())
        self.assertIn('không tự đóng được', ask.text.get())
        self.assertEqual([c.cget('text') for c in ask.buttons.winfo_children()], ['Đã hiểu'])

    def test_a_chrome_opened_by_hand_gets_a_notice_and_no_close(self):
        ask = self.ask(closable=False)
        self.assertIn('không phải do nút Mở Shopee mở', ask.text.get())
        self.assertEqual([c.cget('text') for c in ask.buttons.winfo_children()], ['Đã hiểu'])
        ask.deadline = time.monotonic()-1
        with patch.object(shopee_browser, 'close_account_chrome') as close:
            ask.tick()
        close.assert_not_called()

    def test_the_button_refuses_while_teos_chrome_is_open_or_a_post_is_near(self):
        with patch('teodub.shopee_ui.messagebox.showinfo') as box, patch.object(shopee_browser, 'open_shopee') as start:
            self.tab.sessions['acc'] = SimpleNamespace(pending=True)
            self.assertFalse(self.ui.open_shopee(self.tab))
            self.assertIn('Chrome của Tèo', box.call_args.args[1])
            self.tab.sessions = {}
            self.tab.schedule_busy_within.return_value = True
            self.assertFalse(self.ui.open_shopee(self.tab))
            self.assertIn('sắp tới giờ đăng', box.call_args.args[1])
            self.assertEqual(self.tab.schedule_busy_within.call_args.kwargs, dict(minutes=15))
        start.assert_not_called()

    def test_the_button_opens_it_otherwise(self):
        with patch.object(shopee_browser, 'open_shopee', return_value=1) as start:
            self.assertTrue(self.ui.open_shopee(self.tab))
        self.assertEqual(start.call_args.args, (self.temp, 'acc', self.temp/'profile'/'chrome'))
        self.assertIn('Mở Shopee', self.tab.chrome_log.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
