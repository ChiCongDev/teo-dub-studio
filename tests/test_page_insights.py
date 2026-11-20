"""Views numbers per Page (18/11/2026). The page text below is shaped like the real Facebook text of the probe of 18/11
(nav 'Lượt xem' first, range line, four cards 'number / Tăng N% / N% / label', then other blocks that also say 'Lượt xem')."""
import datetime
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import unittest.mock
from unittest.mock import Mock, patch

import test_facebook_ui as ui
from teodub.common import Cancelled
from teodub import page_insights as pi


def page_text(range_line, cards):
    lines = ['Bảng điều khiển dành cho doanh nghiệp', 'Thông tin chi tiết', 'Lượt xem', 'Thu nhập', 'Tất cả lượt xem', 'Thước phim',
             'Bài viết', 'Tin', range_line]
    for value, change, label in cards:
        lines.append(value)
        if change:
            lines += [change, '﻿' + change.split(' ')[1]]
        lines.append(label)
    return '\n'.join(lines + ['21 Tháng 10', '500', 'Hoạt động đăng', 'Lượt lượt xem theo loại nội dung', 'Bài viết mới đây', '18 Tháng 11 11:02', '196', 'Lượt xem'])


CARDS = [('11.596', 'Tăng 100%', 'Lượt xem'), ('5.272', 'Tăng 100%', 'Lượt xem trong tối thiểu 3 giây'),
         ('664', 'Tăng 100%', 'Lượt xem trong tối thiểu 1 phút'), ('3 ngày 15 giờ', 'Tăng 100%', 'Thời gian xem')]
TEXT_28 = page_text('28 ngày qua: 21 Tháng 10 - 17 Tháng 11', CARDS)
TODAY = datetime.date(2026, 11, 18)


class ParseTests(unittest.TestCase):
    def test_real_shaped_text_gives_four_numbers_with_percent(self):
        label, metrics = pi.parse_page_text(TEXT_28)
        self.assertEqual(label, '28 ngày qua: 21 Tháng 10 - 17 Tháng 11')
        self.assertEqual(metrics['views'], dict(value='11.596', change=100))
        self.assertEqual(metrics['v3']['value'], '5.272')
        self.assertEqual(metrics['v60']['value'], '664')
        self.assertEqual(metrics['watch'], dict(value='3 ngày 15 giờ', change=100))

    def test_a_decrease_is_negative_and_shows_a_down_arrow(self):
        text = page_text('7 ngày qua: 11 Tháng 11 - 17 Tháng 11', [('1.763', 'Giảm 28%', CARDS[1][2])] + CARDS[:1] + CARDS[2:])
        # Order of the cards does not matter: each is found by its label.
        _, metrics = pi.parse_page_text(text)
        self.assertEqual(metrics['v3'], dict(value='1.763', change=-28))
        self.assertEqual(pi.show_metric(metrics['v3']), '1.763 ▼28%')
        self.assertEqual(pi.show_metric(dict(value='5', change=124)), '5 ▲124%')
        self.assertEqual(pi.show_metric(dict(value='5', change=None)), '5')
        self.assertEqual(pi.show_metric(None), '—')

    def test_percent_with_thousands_or_decimals(self):
        text = page_text('Tùy chỉnh: 5 Tháng 11 – 12 Tháng 11', [('9', 'Tăng 1.200%', CARDS[0][2]), ('8', 'Giảm 1,5%', CARDS[1][2])] + CARDS[2:])
        _, metrics = pi.parse_page_text(text)
        self.assertEqual((metrics['views']['change'], metrics['v3']['change']), (1200, -1.5))

    def test_card_without_a_comparison_has_no_percent(self):
        text = page_text('Tháng trước: 1 Tháng 10 – 31 Tháng 10', [('674', None, CARDS[0][2])] + CARDS[1:])
        self.assertIsNone(pi.parse_page_text(text)[1]['views']['change'])

    def test_incomplete_or_other_page_text_is_not_read(self):
        self.assertIsNone(pi.parse_page_text('Đang tải…'))
        self.assertIsNone(pi.parse_page_text(page_text('28 ngày qua: 21 Tháng 10 - 17 Tháng 11', CARDS[:3])))
        self.assertIsNone(pi.parse_page_text(page_text('28 ngày qua: 21 Tháng 10 - 17 Tháng 11',
                                                       [('Tăng 5%', None, CARDS[0][2])] + CARDS[1:])))  # A card without its number.


class RangeTests(unittest.TestCase):
    def test_fixed_month_and_custom_queries(self):
        self.assertEqual(pi.parse_range('7d', TODAY)['query'], 'date_range=LAST_7D')
        self.assertEqual(pi.parse_range('90d', TODAY)['query'], 'date_range=LAST_90D')
        self.assertEqual(pi.parse_range('last_month', TODAY)['query'], 'date_range=LAST_MONTH')
        month = pi.parse_range('this_month', TODAY)
        self.assertEqual(month['query'], 'date_range=CUSTOM&start_date=2026-11-01&end_date=2026-11-17')  # Facebook stops at yesterday.
        custom = pi.parse_range('custom:2026-11-05:2026-11-12', TODAY)
        self.assertEqual(custom['query'], 'date_range=CUSTOM&start_date=2026-11-05&end_date=2026-11-12')

    def test_custom_end_after_yesterday_is_cut_and_bad_ranges_are_refused(self):
        self.assertEqual(pi.parse_range('custom:2026-11-05:2026-11-30', TODAY)['key'], 'custom:2026-11-05:2026-11-17')
        for bad in ('custom:2026-11-12:2026-11-05', 'custom:2026-11-18:2026-11-18', 'custom:abc:def', 'THIS_MONTH', 'custom:'):
            with self.assertRaises(ValueError, msg=bad):
                pi.parse_range(bad, TODAY)

    def test_first_day_of_the_month_has_no_this_month(self):
        with self.assertRaises(ValueError):
            pi.parse_range('this_month', datetime.date(2026, 12, 1))

    def test_date_text_forms(self):
        for text in ('05/11/2026', '5/11/2026', '05-11-2026', '2026-11-05'):
            self.assertEqual(pi.parse_date_text(text), datetime.date(2026, 11, 5))
        with self.assertRaises(ValueError):
            pi.parse_date_text('31/02/2026')

    def test_label_must_match_what_was_asked(self):
        seven, month = pi.parse_range('7d', TODAY), pi.parse_range('last_month', TODAY)
        self.assertTrue(pi.label_matches('7 ngày qua: 11 Tháng 11 - 17 Tháng 11', seven))
        self.assertFalse(pi.label_matches('28 ngày qua: 21 Tháng 10 - 17 Tháng 11', seven))  # Facebook's silent fallback.
        self.assertTrue(pi.label_matches('Tháng trước: 1 Tháng 10 – 31 Tháng 10', month))
        custom = pi.parse_range('custom:2026-11-05:2026-11-12', TODAY)
        self.assertTrue(pi.label_matches('Tùy chỉnh: 5 Tháng 11 – 12 Tháng 11', custom))
        self.assertFalse(pi.label_matches('Tùy chỉnh: 5 Tháng 11 – 13 Tháng 11', custom))
        self.assertFalse(pi.label_matches('28 ngày qua: 21 Tháng 10 - 17 Tháng 11', custom))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = pi.PageInsights(self.temp.name)

    def test_save_load_and_age(self):
        self.assertEqual(self.store.load(), {})
        _, metrics = pi.parse_page_text(TEXT_28)
        self.store.save('a', 'p', '28d', '28 ngày qua: x', metrics, at='2026-11-18T09:00:00')
        entry = self.store.get('a', 'p', '28d')
        self.assertEqual(entry['metrics']['views']['value'], '11.596')
        self.assertIsNone(self.store.get('a', 'p', '7d'))
        self.assertIsNone(self.store.get('a', 'other', '28d'))
        self.assertAlmostEqual(self.store.age_minutes('a', 'p', '28d', now=datetime.datetime(2026, 11, 18, 9, 45)), 45)
        self.assertIsNone(self.store.age_minutes('a', 'p', '7d'))

    def test_broken_file_reads_as_empty_and_custom_ranges_are_capped(self):
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_text('{not json', encoding='utf-8')
        self.assertEqual(self.store.load(), {})
        total = pi.KEEP_CUSTOM+5
        for day in range(1, total+1):
            at = (datetime.datetime(2026, 11, 1)+datetime.timedelta(minutes=day)).isoformat()
            self.store.save('a', 'p', 'custom:2026-10-01:2026-10-%02d' % day, 'Tùy chỉnh', {}, at=at)
        keys = self.store.load()['a:p']
        self.assertEqual(len(keys), pi.KEEP_CUSTOM)
        self.assertNotIn('custom:2026-10-01:2026-10-01', keys)
        self.assertIn('custom:2026-10-01:2026-10-%02d' % total, keys)


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = dict(name='Sinh Tồn', url='https://www.facebook.com/profile.php?id=61595845929185', page_id='61595845929185', username='')
        activity = self.enterContext(patch('teodub.page_activity.PageActivity'))
        activity.return_value.page.return_value = self.target
        self.verify = self.enterContext(patch('teodub.facebook_page_identity.verify_page_actor', return_value=True))
        self.ensure = self.enterContext(patch('teodub.facebook_page_identity.ensure_page_actor',
                                              return_value=dict(switched=False)))
        self.tabs = []
        self.context = Mock()
        self.context.new_page.side_effect = self.new_tab
        self.texts = {}

    def new_tab(self):
        tab = Mock()
        tab.url = pi.BASE_URL
        tab.opened = []

        def goto(url, **kwargs):
            tab.opened.append(url)
        tab.goto.side_effect = goto
        tab.evaluate.side_effect = lambda script: self.texts.get(tab.opened[-1] if tab.opened else '', '')
        self.tabs.append(tab)
        return tab

    def collect(self, ranges, **kwargs):
        return pi.collect(self.context, self.temp.name, 'a'*32, ['b'*32], ranges, today=TODAY, **kwargs)

    def test_after_a_post_the_actor_is_only_checked_and_each_range_is_saved(self):
        self.texts[pi.BASE_URL+'?date_range=LAST_28D'] = TEXT_28
        self.texts[pi.BASE_URL+'?date_range=LAST_7D'] = page_text('7 ngày qua: 11 Tháng 11 - 17 Tháng 11', CARDS)
        result = self.collect(['7d', '28d'])
        self.assertEqual(result['problems'], [])
        self.assertEqual([key for _, key in result['saved']], ['7d', '28d'])
        self.verify.assert_called_once()
        self.ensure.assert_not_called()  # Never switches the identity after a post.
        saved = pi.PageInsights(self.temp.name).get('a'*32, 'b'*32, '28d')
        self.assertEqual(saved['metrics']['v60']['value'], '664')
        for tab in self.tabs:
            tab.close.assert_called_once()

    def test_wrong_identity_saves_nothing_and_opens_no_dashboard(self):
        self.verify.side_effect = ValueError('Danh tính Facebook đã đổi')
        result = self.collect(['28d'])
        self.assertEqual(result['saved'], [])
        self.assertIn('Danh tính Facebook đã đổi', result['problems'][0])
        self.assertEqual(self.tabs, [])

    def test_facebook_falling_back_to_28_days_is_not_saved_as_7_days(self):
        self.texts[pi.BASE_URL+'?date_range=LAST_7D'] = TEXT_28
        with patch.object(pi, 'WAIT_SECONDS', 0):
            result = self.collect(['7d'])
        self.assertEqual(result['saved'], [])
        self.assertIn('không áp dụng khoảng', result['problems'][0])
        self.assertEqual(pi.PageInsights(self.temp.name).load(), {})

    def test_one_failed_range_does_not_stop_the_next(self):
        self.texts[pi.BASE_URL+'?date_range=LAST_28D'] = TEXT_28
        self.texts[pi.BASE_URL+'?date_range=LAST_7D'] = TEXT_28  # Facebook applied another range for the first one.
        with patch.object(pi, 'WAIT_SECONDS', 0):
            result = self.collect(['7d', '28d'])
        self.assertEqual([key for _, key in result['saved']], ['28d'])
        self.assertEqual(len(result['problems']), 1)

    def test_owner_button_may_switch_the_page_identity(self):
        self.texts[pi.BASE_URL+'?date_range=LAST_28D'] = TEXT_28
        result = self.collect(['28d'], switch=True)
        self.assertEqual(len(result['saved']), 1)
        self.ensure.assert_called_once()
        self.verify.assert_not_called()

    def test_not_the_dashboard_is_an_error_not_a_number(self):
        def new_tab():
            tab = self.new_tab()
            tab.url = 'https://www.facebook.com/login/?next=x'
            return tab
        self.context.new_page.side_effect = new_tab
        result = self.collect(['28d'])
        self.assertEqual(result['saved'], [])
        self.assertIn('không mở trang Bảng điều khiển', result['problems'][0])

    def test_stop_request_ends_the_read_and_closes_the_tab(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            self.collect(['28d'], cancel=stop)
        for tab in self.tabs:
            tab.close.assert_called()

    def test_range_that_cannot_be_asked_is_reported(self):
        result = pi.collect(self.context, self.temp.name, 'a'*32, ['b'*32], ['this_month'], today=datetime.date(2026, 12, 1))
        self.assertEqual(result['saved'], [])
        self.assertIn('mùng 1', result['problems'][0])


# What the real pages said (probes of 18/11/2026), word for word.
ZERO_VI = ('Tất cả lượt xem\nThước phim\nBài viết\nTin\n28 ngày qua: 21 Tháng 10 - 17 Tháng 11\n0\nKhông thay đổi\n\ufeff0%\nLượt xem\n'
           '0\nKhông thay đổi\n\ufeff0%\nLượt xem trong tối thiểu 3 giây\n0\nKhông thay đổi\n\ufeff0%\nLượt xem trong tối thiểu 1 phút\n'
           '--\nKhông thay đổi\n\ufeff0%\nThời gian xem\n21 Tháng 10\n26 Tháng 10')
ZERO_VI_LAST_MONTH = ZERO_VI.replace('28 ngày qua: 21 Tháng 10 - 17 Tháng 11', 'Tháng trước')
ENGLISH = ('Business Dashboard\nHome\nInsights\nViews\nEarnings\nEngagement\nAudience\nContent\nEngagement\nAll tools\nCreate a post\n'
           'Videos you post on Facebook are now Reels\nAll views\nReels\nPosts\nStories\nLast 28 days: Oct 21 - Nov 17\n1,275\n48% decrease\n'
           '\ufeff-48%\nViews\n399\n51% decrease\n\ufeff-51%\n3-second views\n68\n74% increase\n\ufeff74%\n1-minute views\n6h 14m\n'
           '56% decrease\n\ufeff-56%\nWatch time\nOct 21\nOct 26\nOct 31')


class LanguageTests(unittest.TestCase):
    def test_a_page_with_no_views_reads_as_zero_with_no_change(self):
        label, metrics = pi.parse_page_text(ZERO_VI)
        self.assertEqual(label, '28 ngày qua: 21 Tháng 10 - 17 Tháng 11')
        self.assertEqual(metrics['views'], dict(value='0', change=0))
        self.assertEqual(metrics['watch'], dict(value='--', change=0))
        self.assertEqual(pi.show_metric(metrics['views']), '0 ▬0%')
        self.assertEqual(pi.show_metric(metrics['watch']), '— ▬0%')  # No watch time: the empty sign, not "--".

    def test_last_month_without_dates_is_read_and_matches_the_range_asked(self):
        label, _ = pi.parse_page_text(ZERO_VI_LAST_MONTH)
        self.assertEqual(label, 'Tháng trước')
        self.assertTrue(pi.label_matches(label, pi.parse_range('last_month', TODAY)))
        self.assertFalse(pi.label_matches(label, pi.parse_range('7d', TODAY)))
        self.assertEqual(pi.span_of(label), 'Tháng trước')

    def test_english_page_is_read_with_its_own_number_and_percent_format(self):
        label, metrics = pi.parse_page_text(ENGLISH)
        self.assertEqual(label, 'Last 28 days: Oct 21 - Nov 17')
        self.assertEqual(metrics['views'], dict(value='1,275', change=-48))
        self.assertEqual(metrics['v3'], dict(value='399', change=-51))
        self.assertEqual(metrics['v60'], dict(value='68', change=74))
        self.assertEqual(metrics['watch'], dict(value='6h 14m', change=-56))
        self.assertEqual(pi.show_metric(metrics['v60']), '68 ▲74%')

    def test_english_percent_with_a_thousands_comma(self):
        text = ENGLISH.replace('74% increase\n\ufeff74%', '1,237% increase\n\ufeff1,237%')
        self.assertEqual(pi.parse_page_text(text)[1]['v60']['change'], 1237)

    def test_english_ranges_match_what_was_asked(self):
        for label, key in (('Last 7 days: Nov 11 - Nov 17', '7d'), ('Last 28 days: Oct 21 - Nov 17', '28d'),
                           ('Last 90 days: Aug 20 - Nov 17', '90d'), ('Last month', 'last_month'),
                           ('Custom: Nov 5 - Nov 12', 'custom:2026-11-05:2026-11-12'), ('Custom: Nov 17 - Nov 17', 'custom:2026-11-17:2026-11-17')):
            self.assertTrue(pi.label_matches(label, pi.parse_range(key, TODAY)), label)
        spec = pi.parse_range('7d', TODAY)
        for label in ('Last 28 days: Oct 21 - Nov 17', 'Last 70 days: x', 'Last month', 'Custom: Nov 5 - Nov 12'):
            self.assertFalse(pi.label_matches(label, spec), label)
        self.assertFalse(pi.label_matches('Custom: Nov 5 - Nov 13', pi.parse_range('custom:2026-11-05:2026-11-12', TODAY)))

    def test_a_range_line_in_the_menu_is_not_mistaken_for_the_numbers(self):
        text = 'Last month\n' + ENGLISH  # An earlier line that looks like a range but has no cards after it.
        self.assertEqual(pi.parse_page_text(text)[0], 'Last 28 days: Oct 21 - Nov 17')


class UnreadableTests(CollectTests):
    def run(self, result=None):  # Only the tests below, not the inherited ones.
        if self._testMethodName.startswith('test_unreadable_'):
            return super().run(result)
        return unittest.TestResult() if result is None else result

    def test_unreadable_first_range_gives_up_on_the_page_at_once(self):
        # The labels are there but nothing Tèo understands: no 30 s wait, and the other ranges are not tried.
        self.texts[pi.BASE_URL+'?date_range=LAST_7D'] = 'Views\nSomething new\nWatch time'
        with patch.object(pi, 'UNDERSTAND_SECONDS', 0):
            result = self.collect(['7d', '28d', '90d'])
        self.assertEqual(result['saved'], [])
        self.assertEqual(len(result['problems']), 1)
        self.assertIn('chưa hiểu cách Facebook trình bày', result['problems'][0])
        self.assertIn('Bỏ qua các khoảng còn lại', result['problems'][0])
        self.assertEqual(len(self.tabs[0].opened), 1)

    def test_unreadable_no_numbers_at_all_gives_up_after_the_wait_not_per_range(self):
        with patch.object(pi, 'WAIT_SECONDS', 0):
            result = self.collect(['7d', '28d'])
        self.assertEqual(len(result['problems']), 1)
        self.assertIn('Chưa thấy số liệu', result['problems'][0])

    def test_unreadable_later_range_does_not_stop_the_next_ones(self):
        self.texts[pi.BASE_URL+'?date_range=LAST_28D'] = TEXT_28
        self.texts[pi.BASE_URL+'?date_range=LAST_90D'] = page_text('90 ngày qua: 20 Tháng 8 - 17 Tháng 11', CARDS)
        with patch.object(pi, 'WAIT_SECONDS', 0):
            result = self.collect(['28d', '60d', '90d'])
        self.assertEqual([key for _, key in result['saved']], ['28d', '90d'])
        self.assertEqual(len(result['problems']), 1)

    def test_unreadable_zero_page_in_english_is_saved(self):
        self.texts[pi.BASE_URL+'?date_range=LAST_28D'] = ENGLISH
        result = self.collect(['28d'])
        self.assertEqual(result['problems'], [])
        saved = pi.PageInsights(self.temp.name).get('a'*32, 'b'*32, '28d')
        self.assertEqual(saved['metrics']['views']['value'], '1,275')


class InsightUITests(unittest.TestCase):
    setUp = ui.FacebookUITests.setUp
    account = ui.FacebookUITests.account
    drain = ui.FacebookUITests.drain

    def page(self):
        from teodub.facebook_ui import BrowserSession
        account = self.account()
        row = self.tab.store.save_page(account, 'Sinh Tồn', 'facebook.com/profile.php?id=123', '123')
        self.tab.reload()
        self.tab.tree.selection_set(row['id'])
        self.session = BrowserSession(account, 'Tài khoản chính', opened=True)
        self.tab.sessions[account] = self.session
        return account, row

    def save(self, account, row, key='28d', text=TEXT_28):
        label, metrics = pi.parse_page_text(text)
        pi.PageInsights(self.studio.data_dir).save(account, row['id'], key, label, metrics, at='2026-11-18T09:30:00')

    def test_row_shows_saved_numbers_and_dashes_for_a_range_not_read(self):
        account, row = self.page()
        self.assertEqual(self.tab.tree.item(row['id'])['values'][1:7], ['—', '—', '—', '—', '', 'chưa có'])
        self.save(account, row)
        self.tab.display_pages()
        values = self.tab.tree.item(row['id'])['values']
        self.assertEqual(values[1:7], ['11.596 ▲100%', '5.272 ▲100%', '664 ▲100%', '3 ngày 15 giờ ▲100%',
                                       '21 Tháng 10 - 17 Tháng 11', '18/11 09:30'])
        self.assertEqual(self.tab.tree.selection(), (row['id'],))  # The refresh keeps the selected Page.

    def test_choosing_another_range_shows_that_range(self):
        account, row = self.page()
        self.save(account, row)
        self.tab.insight_picker.current(self.tab.insight_keys.index('7d'))  # Nothing read for it.
        self.tab.choose_range()
        self.assertEqual(self.tab.tree.item(row['id'])['values'][1], '—')
        self.tab.insight_picker.current(self.tab.insight_keys.index('28d'))
        self.tab.choose_range()
        self.assertEqual(self.tab.tree.item(row['id'])['values'][1], '11.596 ▲100%')

    def test_custom_range_is_offered_and_kept_in_the_list(self):
        account, row = self.page()
        key = 'custom:2026-11-05:2026-11-12'
        self.save(account, row, key, page_text('Tùy chỉnh: 5 Tháng 11 – 12 Tháng 11', CARDS))
        with patch.object(self.tab, 'custom_range_dialog', side_effect=lambda: (
                setattr(self.tab, 'insight_custom', (key, 'Từ 05/11/2026 đến 12/11/2026')),
                setattr(self.tab, 'insight_key', key), self.tab.fill_range_picker(), True)[-1]):
            self.tab.insight_picker.current(len(self.tab.insight_keys))  # The last entry: pick dates.
            self.tab.choose_range()
        self.assertEqual(self.tab.tree.item(row['id'])['values'][1], '11.596 ▲100%')
        self.assertIn(key, self.tab.insight_keys)

    def test_cancelled_custom_dialog_keeps_the_previous_range(self):
        self.page()
        with patch.object(self.tab, 'custom_range_dialog', return_value=False):
            self.tab.insight_picker.current(len(self.tab.insight_keys))
            self.tab.choose_range()
        self.assertEqual(self.tab.insight_key, '28d')
        self.assertEqual(self.tab.insight_picker.current(), self.tab.insight_keys.index('28d'))

    def test_after_a_post_the_usual_ranges_are_queued_without_switching_identity(self):
        account, row = self.page()
        self.tab.after_page_published(account, row['id'])
        command = self.session.commands.get_nowait()
        expected = []
        for key in pi.AFTER_POST:
            try:
                pi.parse_range(key)
                expected.append(key)
            except ValueError:
                pass
        self.assertEqual((command['action'], command['ranges'], command['page_keys'], command['switch']),
                         ('read_insights', expected, [row['id']], False))
        self.assertEqual(self.session.insights_running, 1)

    def test_after_a_post_nothing_is_queued_when_chrome_is_not_open_busy_or_numbers_are_fresh(self):
        account, row = self.page()
        self.session.opened = False
        self.tab.after_page_published(account, row['id'])
        self.assertTrue(self.session.commands.empty())
        self.session.opened = True
        self.session.loading_pages = True
        self.tab.after_page_published(account, row['id'])
        self.assertTrue(self.session.commands.empty())
        self.session.loading_pages = False
        for key in pi.AFTER_POST:
            pi.PageInsights(self.studio.data_dir).save(account, row['id'], pi.parse_range(key)['key'], 'x', {})  # Saved just now.
        self.tab.after_page_published(account, row['id'])
        self.assertTrue(self.session.commands.empty())

    def test_failure_inside_the_hook_never_reaches_the_caller(self):
        account, row = self.page()
        with patch('teodub.facebook_ui.PageInsights', side_effect=RuntimeError('disk')):
            self.tab.after_page_published(account, row['id'])  # Must not raise.

    def test_button_reads_the_chosen_range_and_may_switch_identity(self):
        account, row = self.page()
        self.tab.refresh_buttons()
        self.assertEqual(str(self.tab.insight_one_button['state']), 'normal')
        self.tab.insight_one_button.invoke()
        command = self.session.commands.get_nowait()
        self.assertEqual((command['ranges'], command['switch'], command['page_keys']), (standard(), True, [row['id']]))
        self.assertEqual(str(self.tab.insight_one_button['state']), 'disabled')  # Busy now: no second request.
        self.tab.read_insights_selected()
        self.assertTrue(self.session.commands.empty())

    def test_page_without_an_id_is_not_read(self):
        from teodub.facebook_ui import BrowserSession
        account = self.account()
        row = self.tab.store.save_page(account, 'Chỉ có link', 'facebook.com/chi-co-link')
        self.tab.reload()
        self.tab.tree.selection_set(row['id'])
        session = self.tab.sessions[account] = BrowserSession(account, 'Tài khoản chính', opened=True)
        self.tab.read_insights_selected()
        self.assertTrue(session.commands.empty())
        self.assertIn('chưa có Page ID', self.tab.insight_note.get())

    def test_finished_message_refreshes_the_table_and_frees_the_button(self):
        account, row = self.page()
        self.session.insights_running = 1
        self.save(account, row)
        self.session.messages.put(('insights_done', dict(account_id=account, message='Đã lấy 1 số liệu.', ok=True)))
        self.drain()
        self.assertEqual(self.session.insights_running, 0)
        self.assertEqual(self.tab.insight_note.get(), 'Đã lấy 1 số liệu.')
        self.assertEqual(self.tab.tree.item(row['id'])['values'][1], '11.596 ▲100%')


def standard():
    keys = []
    for key in pi.STANDARD:
        try:
            pi.parse_range(key)
            keys.append(key)
        except ValueError:
            pass
    return keys


class ChoosingShowsSavedTests(InsightUITests):
    """Owner 18/11/2026: choosing a range shows the saved numbers at once; the buttons read every range in one go."""

    def run(self, result=None):  # Only the tests below, not the inherited ones.
        if self._testMethodName.startswith('test_choosing_') or self._testMethodName.startswith('test_buttons_'):
            return super().run(result)
        return unittest.TestResult() if result is None else result

    def pick(self, key):
        self.tab.insight_picker.current(self.tab.insight_keys.index(key))
        self.tab.choose_range()

    def custom(self, key):
        def dialog():
            self.tab.insight_custom = (key, 'Từ 05/11/2026 đến 12/11/2026')
            self.tab.insight_key = key
            self.tab.fill_range_picker()
            return True
        with unittest.mock.patch.object(self.tab, 'custom_range_dialog', side_effect=dialog):
            self.tab.insight_picker.current(len(self.tab.insight_keys))
            self.tab.choose_range()

    def test_buttons_read_every_standard_range_in_one_go(self):
        account, row = self.page()
        self.tab.read_insights_selected()
        command = self.session.commands.get_nowait()
        self.assertEqual((command['ranges'], command['page_keys'], command['switch']), (standard(), [row['id']], True))
        self.assertGreaterEqual(len(command['ranges']), 6)
        self.session.insights_running = 0
        self.tab.read_insights_account()
        self.assertEqual(self.session.commands.get_nowait()['ranges'], standard())

    def test_buttons_also_refresh_the_custom_range_on_screen(self):
        account, row = self.page()
        key = 'custom:2026-11-05:2026-11-12'
        self.save(account, row, key, page_text('Tùy chỉnh: 5 Tháng 11 – 12 Tháng 11', CARDS))
        self.custom(key)
        self.assertTrue(self.session.commands.empty())  # Saved already: shown, not read.
        self.tab.read_insights_selected()
        self.assertEqual(self.session.commands.get_nowait()['ranges'], standard()+[key])

    def test_choosing_a_standard_range_shows_saved_numbers_and_never_uses_chrome(self):
        account, row = self.page()
        label, metrics = pi.parse_page_text(page_text('7 ngày qua: 11 Tháng 11 - 17 Tháng 11', CARDS))
        pi.PageInsights(self.studio.data_dir).save(account, row['id'], '7d', label, metrics, at='2026-10-01T08:00:00')  # Even old.
        self.pick('7d')
        self.assertTrue(self.session.commands.empty())
        self.assertEqual(self.tab.tree.item(row['id'])['values'][1], '11.596 ▲100%')
        self.assertIn('đã lưu', self.tab.insight_note.get())
        self.pick('14d')  # Never read: still no Chrome, the note says which button.
        self.assertTrue(self.session.commands.empty())
        self.assertEqual(self.tab.tree.item(row['id'])['values'][1], '—')
        self.assertIn('Chưa có số liệu', self.tab.insight_note.get())

    def test_choosing_a_new_custom_range_reads_it_once_for_the_selected_page(self):
        account, row = self.page()
        key = 'custom:2026-11-05:2026-11-12'
        self.custom(key)
        command = self.session.commands.get_nowait()
        self.assertEqual((command['ranges'], command['page_keys'], command['switch']), ([key], [row['id']], True))
        self.session.insights_running = 0
        self.save(account, row, key, page_text('Tùy chỉnh: 5 Tháng 11 – 12 Tháng 11', CARDS))
        self.pick('7d')
        self.custom(key)  # Saved now: shown at once.
        self.assertTrue(self.session.commands.empty())
        self.assertEqual(self.tab.tree.item(row['id'])['values'][1], '11.596 ▲100%')

    def test_choosing_a_custom_range_without_a_page_or_while_chrome_is_busy_reads_nothing(self):
        account, row = self.page()
        self.tab.tree.selection_remove(row['id'])
        self.custom('custom:2026-11-05:2026-11-12')
        self.assertTrue(self.session.commands.empty())
        self.assertIn('Chọn một Page', self.tab.insight_note.get())
        self.tab.tree.selection_set(row['id'])
        self.session.loading_pages = True
        self.custom('custom:2026-11-06:2026-11-12')
        self.assertTrue(self.session.commands.empty())
        self.assertIn('đang bận', self.tab.insight_note.get())

    def test_choosing_a_custom_range_opens_chrome_when_it_is_closed(self):
        account, row = self.page()
        del self.tab.sessions[account]
        from teodub.facebook_ui import BrowserSession
        session = BrowserSession(account, 'Tài khoản chính')
        with unittest.mock.patch.object(self.tab, 'open_browser',
                                        side_effect=lambda *a, **k: self.tab.sessions.__setitem__(account, session) or True) as opened:
            self.custom('custom:2026-11-05:2026-11-12')
        opened.assert_called_once()
        self.assertEqual(session.commands.get_nowait()['ranges'], ['custom:2026-11-05:2026-11-12'])

class AutoCloseTests(InsightUITests):
    """Owner 18/11/2026: Chrome Tèo opened for its own work closes when nothing is left; Chrome the owner opened stays."""

    def run(self, result=None):  # Only the tests below, not the inherited ones.
        if self._testMethodName.startswith('test_close_'):
            return super().run(result)
        return unittest.TestResult() if result is None else result

    def idle_session(self, auto=True):
        account, row = self.page()
        self.session.auto_close = auto
        return account, row

    def test_close_after_the_numbers_are_read_when_tem_opened_the_chrome(self):
        account, row = self.idle_session()
        self.session.insights_running = 1
        self.session.messages.put(('insights_done', dict(account_id=account, message='Đã lấy 7 số liệu.', ok=True)))
        self.drain()
        self.assertTrue(self.session.finished.is_set())

    def test_close_never_touches_a_chrome_the_owner_opened(self):
        account, row = self.idle_session(auto=False)
        self.session.insights_running = 1
        self.session.messages.put(('insights_done', dict(account_id=account, message='Đã lấy 7 số liệu.', ok=True)))
        self.drain()
        self.assertFalse(self.session.finished.is_set())
        self.assertFalse(self.tab.close_if_idle(account))

    def test_close_waits_while_chrome_has_other_work(self):
        account, row = self.idle_session()
        for change in (dict(loading_pages=True), dict(insights_running=1), dict(opened=False)):
            for key, value in change.items():
                old = getattr(self.session, key)
                setattr(self.session, key, value)
            self.assertFalse(self.tab.close_if_idle(account), change)
            for key in change:
                setattr(self.session, key, old if key != 'opened' else True)
        self.session.post_actions['r'] = dict(state='running')
        self.assertFalse(self.tab.close_if_idle(account))
        self.session.post_actions['r'] = dict(state='done')
        self.session.commands.put('queued')
        self.assertFalse(self.tab.close_if_idle(account))
        self.session.commands.get_nowait()
        self.session.navigation['n'] = dict(state='queued')
        self.assertFalse(self.tab.close_if_idle(account))
        self.assertFalse(self.session.finished.is_set())
        self.session.navigation['n'] = dict(state='opened')
        self.assertTrue(self.tab.close_if_idle(account))
        self.assertTrue(self.session.finished.is_set())

    def test_close_keeps_chrome_when_a_schedule_is_working_or_due_within_ten_minutes(self):
        account, row = self.idle_session()
        now = time.time()
        for rows in ([dict(state='preparing', due_at=now-5)], [dict(state='armed', due_at=now+9*60)],
                     [dict(state='armed', due_at=now-60)], [dict(state='verifying', due_at=now-600)]):
            with unittest.mock.patch.object(self.tab, 'get_page_scheduler',
                                            return_value=SimpleNamespace(store=SimpleNamespace(list=lambda a, rows=rows: rows))):
                self.assertFalse(self.tab.close_if_idle(account), rows)
        far = [dict(state='armed', due_at=now+3*3600), dict(state='published', due_at=now-60), dict(state='paused', due_at=now+60)]
        with unittest.mock.patch.object(self.tab, 'get_page_scheduler',
                                        return_value=SimpleNamespace(store=SimpleNamespace(list=lambda a: far))):
            self.assertTrue(self.tab.close_if_idle(account))

    def test_close_keeps_chrome_when_the_schedule_cannot_be_read(self):
        account, row = self.idle_session()
        with unittest.mock.patch.object(self.tab, 'get_page_scheduler', side_effect=RuntimeError('db')):
            self.assertFalse(self.tab.close_if_idle(account))

    def test_close_after_a_post_when_there_is_nothing_to_read(self):
        account, row = self.idle_session()
        for key in pi.AFTER_POST:
            pi.PageInsights(self.studio.data_dir).save(account, row['id'], pi.parse_range(key)['key'], 'x', {})  # Read just now.
        self.tab.after_page_published(account, row['id'])
        self.assertTrue(self.session.finished.is_set())

    def test_close_after_a_post_waits_for_the_numbers_read_it_queues(self):
        account, row = self.idle_session()
        self.tab.after_page_published(account, row['id'])
        self.assertFalse(self.session.finished.is_set())
        self.assertEqual(self.session.commands.get_nowait()['action'], 'read_insights')
        self.session.messages.put(('insights_done', dict(account_id=account, message='ok', ok=True)))
        self.drain()
        self.assertTrue(self.session.finished.is_set())

    def test_close_is_not_done_for_a_chrome_the_owner_opened_after_a_post(self):
        account, row = self.idle_session(auto=False)
        for key in pi.AFTER_POST:
            pi.PageInsights(self.studio.data_dir).save(account, row['id'], pi.parse_range(key)['key'], 'x', {})
        self.tab.after_page_published(account, row['id'])
        self.assertFalse(self.session.finished.is_set())

    def test_close_the_owner_using_an_auto_chrome_takes_it_over(self):
        account, row = self.idle_session()
        self.session.opened = True
        with unittest.mock.patch.object(self.tab.store, 'profile_directory', return_value=Path(self.temp.name)):
            self.tab.open_browser('https://www.facebook.com/', account_id=account)
        self.assertFalse(self.session.auto_close)

    def test_close_chrome_opened_for_the_numbers_by_the_button_is_marked_to_close(self):
        account, row = self.page()
        del self.tab.sessions[account]
        from teodub.facebook_ui import BrowserSession
        session = BrowserSession(account, 'Tài khoản chính')
        with unittest.mock.patch.object(self.tab, 'open_browser',
                                        side_effect=lambda *a, **k: self.tab.sessions.__setitem__(account, session) or True):
            self.tab.read_insights_selected()
        self.assertTrue(session.auto_close)

    def test_close_failure_of_the_scheduler_lookup_in_the_hook_never_reaches_the_caller(self):
        account, row = self.idle_session()
        with unittest.mock.patch.object(self.tab, 'close_if_idle', side_effect=RuntimeError('x')):
            self.tab.after_page_published(account, row['id'])  # Must not raise.


class TodayTests(InsightUITests):
    """Owner 18/11/2026: 'Hôm nay' / 'Hôm qua' in the list; choosing 'Hôm nay' reads Facebook again."""

    def run(self, result=None):  # Only the tests below, not the inherited ones.
        if self._testMethodName.startswith('test_day_'):
            return super().run(result)
        return unittest.TestResult() if result is None else result

    def pick(self, key):
        self.tab.insight_picker.current(self.tab.insight_keys.index(key))
        self.tab.choose_range()

    def test_day_ranges_ask_facebook_for_one_day_and_keep_the_date(self):
        today = pi.parse_range('today', TODAY)
        self.assertEqual(today['query'], 'date_range=CUSTOM&start_date=2026-11-18&end_date=2026-11-18')  # Not cut to yesterday.
        self.assertEqual(today['key'], 'custom:2026-11-18:2026-11-18')
        self.assertTrue(pi.label_matches('Tùy chỉnh: 18 Tháng 11 – 18 Tháng 11', today))
        self.assertTrue(pi.label_matches('Custom: Nov 18 - Nov 18', today))
        self.assertEqual(pi.parse_range('yesterday', TODAY)['key'], 'custom:2026-11-17:2026-11-17')
        self.assertEqual(pi.parse_range('today', datetime.date(2026, 11, 19))['key'], 'custom:2026-11-19:2026-11-19')  # Next day: new.
        self.assertEqual(pi.STANDARD[:2], ('today', 'yesterday'))

    def save_day(self, account, row, key, at):
        label, metrics = pi.parse_page_text(TEXT_28)
        pi.PageInsights(self.studio.data_dir).save(account, row['id'], key, label, metrics, at=at)

    def test_day_choosing_today_only_shows_the_saved_numbers_and_never_uses_chrome(self):
        account, row = self.page()
        self.pick('today')
        self.assertTrue(self.session.commands.empty())
        self.assertEqual(self.tab.tree.item(row['id'])['values'][1], '—')
        self.assertIn('Chưa có số liệu', self.tab.insight_note.get())
        self.save_day(account, row, pi.parse_range('today')['key'], datetime.datetime.now().isoformat(timespec='seconds'))
        self.pick('7d')
        self.pick('today')
        self.assertTrue(self.session.commands.empty())
        self.assertEqual(self.tab.tree.item(row['id'])['values'][1], '11.596 ▲100%')
        self.assertIn('chưa đủ ngày', self.tab.insight_note.get())

    def test_day_yesterday_shows_only_numbers_read_after_that_day_ended(self):
        account, row = self.page()
        key = pi.parse_range('yesterday')['key']
        yesterday = datetime.date.today()-datetime.timedelta(days=1)
        # Read during yesterday, when it was still "Hôm nay": part of a day, not shown as yesterday's.
        self.save_day(account, row, key, datetime.datetime.combine(yesterday, datetime.time(15, 0)).isoformat())
        self.pick('yesterday')
        self.assertTrue(self.session.commands.empty())
        self.assertEqual(self.tab.tree.item(row['id'])['values'][1], '—')
        self.assertIn('Chưa có số liệu', self.tab.insight_note.get())
        # Read today, after yesterday was over: its own numbers.
        self.save_day(account, row, key, datetime.datetime.now().isoformat(timespec='seconds'))
        self.pick('7d')
        self.pick('yesterday')
        self.assertTrue(self.session.commands.empty())
        self.assertEqual(self.tab.tree.item(row['id'])['values'][1], '11.596 ▲100%')

    def test_day_after_a_post_reads_yesterday_again_when_it_holds_only_part_of_the_day(self):
        account, row = self.page()
        for key in pi.AFTER_POST:
            pi.PageInsights(self.studio.data_dir).save(account, row['id'], pi.parse_range(key)['key'], 'x', {})  # All read just now.
        self.tab.after_page_published(account, row['id'])
        self.assertTrue(self.session.commands.empty())  # Fresh: nothing to read.
        key = pi.parse_range('yesterday')['key']
        yesterday = datetime.date.today()-datetime.timedelta(days=1)
        pi.PageInsights(self.studio.data_dir).save(account, row['id'], key, 'x', {},
                                                  at=datetime.datetime.combine(yesterday, datetime.time(23, 55)).isoformat())
        self.tab.after_page_published(account, row['id'])
        self.assertEqual(self.session.commands.get_nowait()['ranges'], ['yesterday'])

    def test_day_completeness_rule(self):
        self.assertTrue(pi.is_complete('custom:2026-11-17:2026-11-17', dict(at='2026-11-18T00:05:00')))
        self.assertFalse(pi.is_complete('custom:2026-11-17:2026-11-17', dict(at='2026-11-17T23:59:00')))
        self.assertFalse(pi.is_complete('custom:2026-11-17:2026-11-17', {}))
        self.assertTrue(pi.is_complete('28d', dict(at='2026-11-17T09:00:00')))

    def test_day_buttons_and_posts_include_today_and_yesterday(self):
        account, row = self.page()
        self.tab.read_insights_selected()
        self.assertEqual(self.session.commands.get_nowait()['ranges'][:2], ['today', 'yesterday'])
        self.session.insights_running = 0
        self.tab.after_page_published(account, row['id'])
        self.assertIn('today', self.session.commands.get_nowait()['ranges'])


class SubmittedTests(InsightUITests):
    """20/11/2026: percents with a thousands comma; a post that ends 'Đã gửi' also reads numbers and closes Chrome."""

    def run(self, result=None):  # Only the tests below, not the inherited ones.
        if self._testMethodName.startswith('test_sub_'):
            return super().run(result)
        return unittest.TestResult() if result is None else result

    def test_sub_vietnamese_percent_with_a_thousands_comma(self):
        for text, expected in (('29,469', 29469), ('1.238', 1238), ('1,5', 1.5), ('12', 12), ('103,906', 103906), ('1.200', 1200)):
            self.assertEqual(pi._percent(text), expected, text)
        self.assertEqual(pi._change_of(['Tăng 29,469%']), 29469)
        self.assertEqual(pi._change_of(['Giảm 1,5%']), -1.5)

    def test_sub_submitted_post_waits_then_reads_numbers(self):
        account, row = self.page()
        self.session.auto_close = True
        with patch.object(self.tab, 'after') as later:
            self.tab.after_page_submitted(account, row['id'])
        self.assertEqual(later.call_args[0][0], 180000)
        self.assertTrue(self.session.commands.empty())  # Nothing at once.
        self.assertFalse(self.tab.close_if_idle(account))  # Chrome stays open while waiting.
        later.call_args[0][1]()
        self.assertEqual(self.session.commands.get_nowait()['action'], 'read_insights')

    def test_sub_submitted_post_with_nothing_to_read_closes_chrome(self):
        account, row = self.page()
        self.session.auto_close = True
        at = datetime.datetime.now().isoformat(timespec='seconds')
        for key in pi.AFTER_POST:
            pi.PageInsights(self.studio.data_dir).save(account, row['id'], pi.parse_range(key)['key'], 'x', {}, at=at)
        with patch.object(self.tab, 'after') as later:
            self.tab.after_page_submitted(account, row['id'])
        later.call_args[0][1]()
        self.assertTrue(self.session.finished.is_set())


if __name__ == '__main__':
    unittest.main()
