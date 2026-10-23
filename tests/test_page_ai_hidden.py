"""The AI panel hides the owner-approved controls while keeping everything else and the stored values."""
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests import test_page_ai as ai_fixtures
from teodub.page_ai_ui import PageAIPanel
from teodub.page_schedule import display_time

HIDDEN_TEXTS = {
    'Bài chữ mỗi tuần', 'Gọi AI tiếp theo (UTC+7)', 'Lặp sau', 'ngày · 0 = một lần',
    'Nhập giờ theo dạng YYYY-MM-DD HH:MM.', 'Tôi tự đặt giờ đăng, AI chỉ viết caption', 'Bật lịch gọi AI',
    'Đến giờ hẹn mới dùng hạn mức AI để phân tích và lập kế hoạch.',
    'Tự bật lịch đăng sau khi Tèo kiểm tra kế hoạch',
}
KEPT_TEXTS = {
    'Video mỗi ngày', 'Giờ bài đầu mỗi ngày (HH:MM)', 'Cách nhau (phút)', 'Lưu thiết lập & hẹn giờ', 'Tạm dừng AI',
    'Thêm nguồn…', 'Bỏ nguồn', 'Làm mới nguồn', 'Mục tiêu phát triển', 'Chat chiến lược…', 'Nhịp nội dung & giờ gọi AI',
    '▸ Tùy chọn AI',
}


def texts(widget):
    found = set()
    for child in [widget, *widget.winfo_children()]:
        pass
    stack = [widget]
    while stack:
        item = stack.pop()
        try:
            value = item.cget('text')
        except tk.TclError:
            value = ''
        if value:
            found.add(str(value))
        stack.extend(item.winfo_children())
    return found


class HiddenControlsTests(unittest.TestCase):
    def setUp(self):
        ai_fixtures.PageAITests.setUp(self)
        try:
            self.window = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        self.window.withdraw()
        self.addCleanup(self.window.destroy)
        self.manager = SimpleNamespace(owner=self.owner, account_id=self.account, page_key=self.page,
                                       open_strategy_settings=Mock())
        self.history = ttk.Frame(self.window)
        self.panel = PageAIPanel(ttk.Notebook(self.window), self.manager, history_parent=self.history)

    def test_the_approved_controls_are_not_shown_and_everything_else_is(self):
        shown = texts(self.panel)
        self.assertEqual(shown & HIDDEN_TEXTS, set())
        self.assertEqual(KEPT_TEXTS - shown - texts(self.history), set())

    def test_fresh_page_saves_with_ai_off_and_no_text_posts(self):
        self.panel.set_sources([self.campaign])
        self.panel.save()
        row = self.store.policy(self.account, self.page)
        self.assertFalse(row['enabled'])
        self.assertEqual(row['config']['texts_per_week'], 0)
        self.assertFalse(row['config']['auto_publish'])
        self.assertNotIn('slot_start', row['config'])
        self.assertIn('Lịch gọi AI đang tắt', self.panel.status.get())

    def test_saving_does_not_change_values_the_owner_can_no_longer_see(self):
        config = dict(self.config, days=5, interval_days=5, videos_per_day=2, texts_per_week=0, auto_publish=True,
                      slot_start='11:00', slot_gap=120)
        before = self.store.save(self.account, self.page, config, time.time()+3*86400, True)
        self.panel.destroy()
        self.panel = PageAIPanel(ttk.Notebook(self.window), self.manager, history_parent=ttk.Frame(self.window))
        self.panel.videos.set(1)
        self.panel.save()
        after = self.store.policy(self.account, self.page)
        self.assertEqual(after['next_at'], before['next_at'])
        self.assertEqual(after['enabled'], before['enabled'])
        for key in ('days', 'interval_days', 'texts_per_week', 'auto_publish', 'slot_start', 'slot_gap', 'goal', 'model'):
            self.assertEqual(after['config'][key], before['config'][key], key)
        self.assertEqual(after['config']['videos_per_day'], 1)

    def test_a_stale_time_in_the_hidden_field_never_blocks_saving(self):
        self.panel.set_sources([self.campaign])
        self.panel.due.set('2020-01-01 06:00')
        self.panel.save()
        row = self.store.policy(self.account, self.page)
        self.assertIsNotNone(row)
        self.assertGreater(row['next_at'], time.time())
        self.assertFalse(row['enabled'])

    def test_existing_policy_keeps_its_next_run_even_when_the_hidden_text_is_edited(self):
        config = dict(self.config, texts_per_week=0)
        before = self.store.save(self.account, self.page, config, time.time()+2*86400, True)
        self.panel.destroy()
        self.panel = PageAIPanel(ttk.Notebook(self.window), self.manager, history_parent=ttk.Frame(self.window))
        self.panel.due.set(display_time(time.time()+10*86400))
        self.panel.save()
        self.assertEqual(self.store.policy(self.account, self.page)['next_at'], before['next_at'])


if __name__ == '__main__':
    unittest.main()
