"""Nút "Mở kênh" on a link's tab (owner 31/10/2026): opens the link's channel in Tèo's own Chrome and nothing else. Real Tk
windows; the Chrome helper is replaced by a fake that waits like a real window: no browser, no Douyin, no AI."""
import hashlib
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from tests import test_campaign_append_ui as base


class FakeChrome:
    """Stands in for channel_login.open_chrome_login: reports twice, then waits until released or cancelled."""

    def __init__(self, reason='browser_closed', error=None, announce=True):
        self.calls, self.reason, self.error, self.announce = [], reason, error, announce
        self.release = threading.Event()
        self.cancel = None

    def __call__(self, profile, *, text='', cancel=None, finished=None, progress=None, timeout=1800):
        self.calls.append(dict(profile=profile, text=text, timeout=timeout, finished=finished))
        self.cancel = cancel
        if self.error:
            raise ValueError(self.error)
        if self.announce:
            progress('Đang mở Chrome…')
            progress('Bạn tự đăng nhập Douyin trong Chrome. Khi xong, bấm Đã xong / Lưu phiên…')
        while not self.release.wait(0.02):
            if cancel is not None and cancel.is_set():
                return 'cancelled'
        return self.reason


class OpenChannelTests(unittest.TestCase):
    # The window set-up of the "Lấy thêm video mới…" tests, without re-running those tests here.
    cleanup, channel_link = base.AppendDialogTests.cleanup, base.AppendDialogTests.channel_link

    def setUp(self):
        base.AppendDialogTests.setUp(self)
        self.fake = FakeChrome()
        patcher = patch('teodub.channel_login.open_chrome_login', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.stop_thread)
        self.link = self.channel_link('demo')
        self.tab_a = self.area.open_link(self.link.directory, select=False)

    def stop_thread(self):
        self.fake.release.set()
        thread = self.area.channel_thread
        if thread is not None:
            thread.join(5)

    def wait_for(self, kind_text):
        """Pump the area's messages until the tab says kind_text (the helper thread answers within milliseconds)."""
        for _ in range(250):
            self.area.drain_channel()
            if kind_text in self.tab_a.message.get():
                return
            self.root.update()
            threading.Event().wait(0.02)
        self.fail(f'never saw {kind_text!r}; last: {self.tab_a.message.get()!r}')

    def digest(self, directory):
        return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(directory.rglob('*')) if path.is_file()}

    # The button ----------------------------------------------------------------------------------
    def test_button_only_for_a_link_with_a_channel(self):
        self.tab_a.refresh(full=True)
        self.assertEqual(str(self.tab_a.channel_button['state']), 'normal')
        self.assertEqual(self.tab_a.channel_button['text'], 'Mở kênh')
        pasted = self.area.open_link(self.list_link(), select=False)
        pasted.refresh(full=True)
        self.assertEqual(str(pasted.channel_button['state']), 'disabled')

    def list_link(self):
        from teodub import campaign
        from tests.test_campaign_append_ui import video
        from tests.test_campaign_rotation import SETTINGS
        return campaign.create_campaign(self.data, 'Danh sách dán', [video(900), video(901)], SETTINGS,
                                        str(self.data/'originals'), str(self.data/'outputs'))

    def test_button_stays_on_while_a_single_video_runs(self):
        """Unlike "Lấy thêm video mới…", looking at the channel never touches a running video."""
        self.studio.process = object()
        try:
            self.tab_a.refresh(full=True)
            self.assertEqual(str(self.tab_a.channel_button['state']), 'normal')
            self.assertEqual(str(self.tab_a.more_button['state']), 'disabled')
        finally:
            self.studio.process = None

    # Opening -------------------------------------------------------------------------------------
    def test_opens_this_links_channel_in_the_login_chrome_profile(self):
        self.tab_a.open_channel()
        self.wait_for('Đã mở kênh')
        self.assertEqual(len(self.fake.calls), 1)
        call = self.fake.calls[0]
        self.assertEqual(call['text'], base.CHANNEL)
        self.assertEqual(call['profile'], (self.data/'douyin-channel-browser'/'chrome').resolve())
        self.assertGreater(call['timeout'], 3600)
        self.assertTrue(self.area.channel_open)
        self.assertIn('đóng cửa sổ Chrome đó', self.tab_a.message.get())

    def test_the_other_tab_is_told_to_close_the_open_window_first(self):
        other = self.area.open_link(self.channel_link('Kênh B', numbers=(30, 31)).directory, select=False)
        self.tab_a.open_channel()
        self.wait_for('Đã mở kênh')
        other.open_channel()
        self.assertIn('Đóng cửa sổ Chrome đó trước', other.message.get())
        self.assertEqual(len(self.fake.calls), 1)
        self.assertIs(self.area.channel_tab, self.tab_a)

    def test_closing_the_window_frees_the_button_and_says_so(self):
        self.tab_a.open_channel()
        self.wait_for('Đã mở kênh')
        self.fake.release.set()
        self.wait_for('Đã đóng cửa sổ kênh')
        self.assertFalse(self.area.channel_open)
        self.fake.release.clear()
        self.tab_a.open_channel()
        self.wait_for('Đã mở kênh')
        self.assertEqual(len(self.fake.calls), 2)

    def test_the_long_wait_is_reported_as_such(self):
        self.fake.reason = 'timeout'
        self.tab_a.open_channel()
        self.wait_for('Đã mở kênh')
        self.fake.release.set()
        self.wait_for('quá lâu')
        self.assertFalse(self.area.channel_open)

    def test_a_failed_open_shows_the_reason_and_frees_the_button(self):
        self.fake.error = 'Một cửa sổ Tèo khác đang dùng cùng hồ sơ trình duyệt.'
        self.tab_a.open_channel()
        self.wait_for('Chưa mở được kênh: Một cửa sổ Tèo khác')
        self.assertFalse(self.area.channel_open)

    def test_refused_while_signing_in_or_reading_a_channel(self):
        self.studio.login_pending = True
        self.tab_a.open_channel()
        self.assertIn('Đang đăng nhập hoặc đọc kênh', self.tab_a.message.get())
        self.studio.login_pending = False
        self.tab.scanning = True
        try:
            self.tab_a.message.set('')
            self.tab_a.open_channel()
            self.assertIn('Đang đăng nhập hoặc đọc kênh', self.tab_a.message.get())
        finally:
            self.tab.scanning = False
        self.tab.chrome_login_pending = True
        try:
            self.tab_a.message.set('')
            self.tab_a.open_channel()
            self.assertIn('Đang đăng nhập hoặc đọc kênh', self.tab_a.message.get())
        finally:
            self.tab.chrome_login_pending = False
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(self.area.channel_open)

    def test_a_link_without_a_channel_opens_nothing(self):
        pasted = self.area.open_link(self.list_link(), select=False)
        pasted.open_channel()
        self.assertIn('không có link kênh', pasted.message.get())
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(self.area.channel_open)

    # Nothing else changes ------------------------------------------------------------------------
    def test_no_campaign_file_and_no_global_flag_changes(self):
        before = self.digest(self.data/'campaigns')
        self.tab_a.open_channel()
        self.wait_for('Đã mở kênh')
        self.assertFalse(self.studio.login_pending, 'running links must stay startable while the window is open')
        self.assertFalse(self.tab.chrome_login_pending)
        self.fake.release.set()
        self.wait_for('Đã đóng cửa sổ kênh')
        self.assertEqual(self.digest(self.data/'campaigns'), before)

    def test_run_and_stop_buttons_of_the_link_are_not_blocked(self):
        self.tab_a.open_channel()
        self.wait_for('Đã mở kênh')
        self.tab_a.refresh(full=True)
        self.assertEqual(str(self.tab_a.run_button['state']), 'normal')

    # Closing Tèo and restarting -----------------------------------------------------------------
    def test_closing_the_area_cancels_the_window(self):
        self.tab_a.open_channel()
        self.wait_for('Đã mở kênh')
        self.area.on_destroy(SimpleNamespace(widget=self.area))
        self.assertTrue(self.fake.cancel.is_set())
        self.area.channel_thread.join(5)
        self.assertFalse(self.area.channel_thread.is_alive())

    def test_safe_restart_waits_for_the_window(self):
        from teodub.restart import busy_reasons
        facebook = SimpleNamespace(manager_windows={}, page_bridge=None, sessions={}, page_scheduler=None)

        def reasons():
            return [r for r in busy_reasons(self.studio, self.tab, facebook) if 'cửa sổ kênh' in r]
        self.assertEqual(reasons(), [])
        self.tab_a.open_channel()
        self.wait_for('Đã mở kênh')
        self.assertEqual(len(reasons()), 1)
        self.fake.release.set()
        self.wait_for('Đã đóng cửa sổ kênh')
        self.assertEqual(reasons(), [])

    def test_restart_check_ignores_a_studio_without_the_area(self):
        from teodub.restart import busy_reasons
        facebook = SimpleNamespace(manager_windows={}, page_bridge=None, sessions={}, page_scheduler=None)
        studio = Mock(spec=['login_pending', 'stop_started', 'process', 'window', 'voice_learning_busy'])
        studio.login_pending, studio.stop_started, studio.process = False, None, None
        studio.window.winfo_children.return_value = []
        studio.voice_learning_busy = False
        self.assertEqual([r for r in busy_reasons(studio, self.tab, facebook) if 'cửa sổ kênh' in r], [])


if __name__ == '__main__':
    unittest.main()
