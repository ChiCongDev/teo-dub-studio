"""24/11/2026 (owner): "mỗi khi đăng xong là phải lấy số liệu", whatever the post ended as. Disposable data folders, fake Chrome
sessions; no browser, no Facebook."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from teodub import after_post_numbers as apn
import test_page_insights as insights_base  # Not imported by name: their own tests would run here again.
import test_page_schedule as schedule_base


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def test_posts_close_together_are_read_once_the_latest_page_first_after_the_later_time(self):
        apn.add(self.data, 'acc', 'p1', delay=180, now=1000)
        entry = apn.add(self.data, 'acc', 'p2', delay=0, now=1010)
        self.assertEqual((entry['pages'], entry['due_at']), (['p2', 'p1'], 1180))
        self.assertEqual(apn.due(self.data, 'run', now=1179), [])
        self.assertEqual([account for account, _ in apn.due(self.data, 'run', now=1180)], ['acc'])

    def test_a_read_handed_to_chrome_is_kept_until_it_ends_and_a_later_teo_does_it_again(self):
        entry = apn.add(self.data, 'acc', 'p1', delay=0, now=1000)
        self.assertTrue(apn.handed_over(self.data, 'acc', entry, 'run1'))
        self.assertEqual(apn.due(self.data, 'run1', now=2000), [])  # This Tèo is reading it.
        self.assertTrue(apn.waiting(self.data, 'acc'))
        self.assertEqual(len(apn.due(self.data, 'run2', now=2000)), 1)  # Tèo closed before it ended: the next Tèo reads again.
        self.assertFalse(apn.finished(self.data, 'acc', 'run2'))
        self.assertTrue(apn.finished(self.data, 'acc', 'run1'))
        self.assertFalse(apn.waiting(self.data, 'acc'))

    def test_a_post_that_ends_while_its_account_is_read_gets_its_own_read(self):
        entry = apn.add(self.data, 'acc', 'p1', delay=0, now=1000)
        apn.handed_over(self.data, 'acc', entry, 'run1')
        apn.add(self.data, 'acc', 'p2', delay=0, now=1100)
        self.assertFalse(apn.finished(self.data, 'acc', 'run1'))  # The new post is not the read that ended.
        self.assertEqual(apn.load(self.data)['acc']['pages'], ['p2', 'p1'])

    def test_a_read_that_cannot_start_is_tried_again_then_given_up(self):
        entry = apn.add(self.data, 'acc', 'p1', delay=0, now=1000)
        for attempt in range(apn.MAX_TRIES-1):
            self.assertTrue(apn.try_again(self.data, 'acc', apn.load(self.data)['acc'], now=2000))
            self.assertEqual(apn.load(self.data)['acc']['due_at'], 2000+apn.RETRY_SECONDS)
        self.assertFalse(apn.try_again(self.data, 'acc', apn.load(self.data)['acc'], now=3000))
        self.assertEqual(apn.load(self.data), {})
        self.assertTrue(apn.try_again(self.data, 'acc', entry, now=3000))  # Nothing left: nothing was given up now.

    def test_a_broken_file_reads_as_empty_and_bad_entries_are_ignored(self):
        path = self.data/'page-publishing'/apn.FILE_NAME
        path.parent.mkdir(parents=True)
        path.write_text('{not json', encoding='utf-8')
        self.assertEqual(apn.load(self.data), {})
        path.write_text(json.dumps(dict(accounts=dict(a=dict(pages=[], due_at=1), b=dict(pages=['p'], due_at='x'),
                                                      c=dict(pages=['p'], due_at=5)))), encoding='utf-8')
        self.assertEqual(list(apn.load(self.data)), ['c'])
        apn.drop(self.data, 'c')
        self.assertEqual(apn.load(self.data), {})


class EveryPostUITests(insights_base.InsightUITests):
    def run(self, result=None):  # Only the tests below, not the inherited ones.
        if self._testMethodName.startswith('test_every_'):
            return super().run(result)
        return unittest.TestResult() if result is None else result

    def log(self):
        path = Path(self.studio.data_dir)/'page-publishing'/'chrome-log.txt'
        return path.read_text(encoding='utf-8') if path.exists() else ''

    def test_every_a_post_that_ended_unclear_or_failed_is_read_three_minutes_later(self):
        account, row = self.page()
        self.tab.after_page_ended(account, row['id'])
        self.assertTrue(self.session.commands.empty())
        self.assertAlmostEqual(apn.load(self.studio.data_dir)[account]['due_at']-time.time(), 180, delta=10)
        self.assertIn('Bài kết thúc chưa rõ kết quả hoặc bị lỗi (Sinh Tồn): chờ 3 phút rồi lấy số liệu.', self.log())
        self.time_passes(account)
        command = self.session.commands.get_nowait()
        self.assertEqual((command['action'], command['page_keys'], command['switch']), ('read_insights', [row['id']], True))

    def test_every_a_closed_chrome_is_opened_for_the_read_and_closes_after(self):
        from teodub.facebook_ui import BrowserSession
        account, row = self.page()
        del self.tab.sessions[account]
        opened = BrowserSession(account, 'Tài khoản chính')
        with patch.object(self.tab, 'open_browser',
                          side_effect=lambda *a, **k: self.tab.sessions.__setitem__(account, opened) or True) as open_browser:
            self.tab.after_page_published(account, row['id'])
        open_browser.assert_called_once()
        self.assertTrue(opened.auto_close)  # Tèo opened it for its own work: it closes it when that is done.
        self.assertEqual(opened.commands.get_nowait()['action'], 'read_insights')
        self.assertEqual(apn.load(self.studio.data_dir)[account]['queued'], self.tab.numbers_run)

    def test_every_chrome_that_cannot_be_opened_is_tried_again_then_given_up(self):
        account, row = self.page()
        del self.tab.sessions[account]
        with patch.object(self.tab, 'open_browser', return_value=False):
            self.tab.after_page_published(account, row['id'])
            self.assertIn('Chưa mở được Chrome để lấy số liệu; thử lại sau 5 phút.', self.log())
            self.time_passes(account)
            self.time_passes(account)
        self.assertIn('Bỏ lượt lấy số liệu: 3 lần không mở được Chrome.', self.log())
        self.assertFalse(apn.waiting(self.studio.data_dir, account))

    def test_every_a_post_being_worked_on_and_a_busy_chrome_go_first(self):
        account, row = self.page()
        with patch.object(self.tab, 'posting_now', return_value=True):
            self.tab.after_page_published(account, row['id'])
        self.assertTrue(self.session.commands.empty())
        self.session.post_actions['r'] = dict(state='running')
        self.tab.run_numbers_after_post()
        self.assertTrue(self.session.commands.empty())
        self.assertTrue(apn.waiting(self.studio.data_dir, account))  # Kept for later, Chrome kept open.
        self.session.post_actions['r'] = dict(state='done')
        self.tab.run_numbers_after_post()
        self.assertEqual(self.session.commands.get_nowait()['action'], 'read_insights')

    def test_every_a_read_a_closed_teo_left_is_done_a_minute_after_opening(self):
        account, row = self.page()
        apn.add(self.studio.data_dir, account, row['id'], delay=0, now=time.time()-3600)
        self.tab.run_numbers_after_post()
        self.assertTrue(self.session.commands.empty())  # The schedules go first just after opening.
        self.tab.numbers_started -= 61
        self.tab.run_numbers_after_post()
        self.assertEqual(self.session.commands.get_nowait()['page_keys'], [row['id']])

    def test_every_a_read_an_earlier_teo_handed_to_chrome_is_done_again(self):
        account, row = self.page()
        entry = apn.add(self.studio.data_dir, account, row['id'], delay=0, now=time.time()-3600)
        apn.handed_over(self.studio.data_dir, account, entry, 'an earlier Tèo')
        self.tab.numbers_started -= 61
        self.tab.run_numbers_after_post()
        self.assertEqual(self.session.commands.get_nowait()['action'], 'read_insights')

    def test_every_chrome_closing_before_the_read_ended_tries_again_later(self):
        account, row = self.page()
        self.tab.after_page_published(account, row['id'])
        self.session.commands.get_nowait()
        self.session.messages.put(('done', None))
        self.drain()
        entry = apn.load(self.studio.data_dir)[account]
        self.assertEqual((entry['queued'], entry['tries']), ('', 1))
        self.assertGreater(entry['due_at'], time.time()+apn.RETRY_SECONDS-30)

    def test_every_result_and_the_reasons_go_to_the_log_and_the_read_is_forgotten(self):
        account, row = self.page()
        self.tab.after_page_published(account, row['id'])
        message = 'Đã lấy 0 số liệu. Chưa lấy được: Sinh Tồn: Chưa thấy số liệu sau 30 giây.'
        self.session.messages.put(('insights_done', dict(account_id=account, message=message, ok=False)))
        self.drain()
        self.assertIn('Kết quả lấy số liệu: '+message, self.log())
        self.assertFalse(apn.waiting(self.studio.data_dir, account))

    def test_every_a_removed_account_has_no_read_left(self):
        account, row = self.page()
        apn.add(self.studio.data_dir, 'f'*32, row['id'], delay=0)
        self.tab.run_numbers_after_post()
        self.assertFalse(apn.waiting(self.studio.data_dir, 'f'*32))
        self.assertTrue(self.session.commands.empty())


class EveryPostScheduleTests(schedule_base.ScheduleTests):
    def run(self, result=None):  # Only the tests below, not the inherited ones.
        if self._testMethodName.startswith('test_every_'):
            return super().run(result)
        return unittest.TestResult() if result is None else result

    def test_every_a_post_that_failed_while_being_worked_on_tells_the_owner(self):
        self.owner.after_page_ended = Mock()
        for state, campaign in (('preparing', 'a'*32), ('submitting', 'b'*32), ('verifying', 'c'*32)):
            row = self.schedule(campaign)  # One video each: Tèo never puts one video twice on a Page.
            self.store.update(row['id'], state=state)
            self.controller.fail(row['id'], 'fixture failure')
            self.assertEqual(self.store.get(row['id'])['state'], 'needs_attention')
        self.assertEqual(self.owner.after_page_ended.call_count, 3)
        self.owner.after_page_ended.assert_called_with(self.account, self.page)

    def test_every_a_schedule_that_never_started_or_the_owner_stopped_is_not_read(self):
        self.owner.after_page_ended = Mock()
        armed = self.schedule()
        self.controller.start(armed['id'])
        self.controller.fail(armed['id'], 'Chrome of the account stayed busy')  # It never started: nothing was posted.
        running = self.schedule('b'*32)
        self.controller.start(running['id'])
        self.store.update(running['id'], state='submitting', attempted_at='fixture')
        self.controller.pause(running['id'])  # The owner pressed Dừng.
        self.controller.fail(running['id'], 'late reply')
        self.owner.after_page_ended.assert_not_called()
        self.assertFalse(apn.waiting(self.directory, self.account))

    def test_every_a_hook_failure_never_breaks_the_schedule(self):
        self.owner.after_page_ended = Mock(side_effect=RuntimeError('ui'))
        row = self.schedule()
        self.store.update(row['id'], state='verifying')
        self.controller.fail(row['id'], 'fixture failure')
        self.assertEqual(self.store.get(row['id'])['state'], 'needs_attention')

    def test_every_teo_closing_mid_post_leaves_the_read_for_the_next_teo(self):
        row = self.schedule()
        self.controller.start(row['id'])
        self.store.update(row['id'], state='submitting', attempted_at='fixture')
        self.controller.close()
        entry = apn.load(self.directory)[self.account]
        self.assertEqual(entry['pages'], [self.page])
        self.assertAlmostEqual(entry['due_at']-time.time(), apn.WAIT_SECONDS, delta=10)

    def test_every_teo_lost_mid_post_reads_when_it_opens_again(self):
        row = self.schedule()
        waiting = self.schedule('b'*32)
        self.store.update(row['id'], state='verifying', attempted_at='fixture')
        self.store.update(waiting['id'], state='armed')
        second = schedule_base.PageScheduleController(self.owner)
        self.addCleanup(second.close)
        self.assertEqual(self.store.get(row['id'])['state'], 'needs_attention')
        self.assertEqual(apn.load(self.directory)[self.account]['pages'], [self.page])  # Only the post that was being worked on.


if __name__ == '__main__':
    unittest.main()
