"""Pause result observation without cancelling input or claiming publication."""
from pathlib import Path
import threading
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub.facebook_ui import FacebookTab, BrowserSession
from teodub.page_publish_settings import result_checks_enabled, set_result_checks_enabled
from teodub.page_status import summarize
from tests import test_facebook_ui as facebook_fixtures
from tests import test_page_manager_ui as manager_fixtures
from tests import test_page_schedule_ui as schedule_fixtures


class PausedFacebookUITests(unittest.TestCase):
    setUp = facebook_fixtures.FacebookUITests.setUp

    def test_advanced_toggle_persists_and_only_cancels_result_observers(self):
        session = BrowserSession('account','Synthetic',opened=True)
        self.tab.sessions['account'] = session
        for ident,operation,state in [('prepare','prepare','running'),('publish','publish','running'),
                ('review','review','queued'),('observer','publish','verifying'),
                ('verify','verify','queued'),('inspect','inspect_result','running')]:
            session.post_actions[ident] = dict(operation=operation,state=state,cancel=threading.Event())
        self.assertEqual(self.tab.result_checks_button.winfo_manager(),'')
        self.tab.advanced_button.invoke()
        self.assertEqual(self.tab.result_checks_button.winfo_manager(),'pack')
        self.tab.result_checks_button.invoke()
        self.assertFalse(result_checks_enabled(self.studio.data_dir))
        for ident in ('prepare','publish','review'):
            self.assertFalse(session.post_actions[ident]['cancel'].is_set())
        for ident in ('observer','verify','inspect'):
            self.assertTrue(session.post_actions[ident]['cancel'].is_set())
        self.assertFalse(session.cancel.is_set())
        self.assertIn('Đã tạm dừng',self.tab.result_checks_notice.get())
        self.tab.result_checks_button.invoke()
        self.assertTrue(result_checks_enabled(self.studio.data_dir))
        self.assertIn('Đã bật',self.tab.result_checks_notice.get())
        self.thread.assert_not_called()

    def test_persisted_pause_is_loaded_before_build_and_blocks_direct_check_requests(self):
        set_result_checks_enabled(self.studio.data_dir,False)
        other = FacebookTab(ttk.Notebook(self.root),self.studio)
        self.addCleanup(other.destroy)
        self.assertFalse(other.result_checks_enabled.get())
        for operation in ('verify','inspect_result'):
            self.assertIsNone(other.submit_page_browser('account','page','post',operation))
        self.assertIn('Đã tạm dừng kiểm tra',other.status.get())
        self.assertEqual(other.sessions,{})
        self.thread.assert_not_called()


class PausedScheduleUITests(unittest.TestCase):
    setUp = schedule_fixtures.PageScheduleUITests.setUp
    schedule = schedule_fixtures.PageScheduleUITests.schedule
    panel = schedule_fixtures.PageScheduleUITests.panel
    choose = schedule_fixtures.PageScheduleUITests.choose
    start_schedule = schedule_fixtures.PageScheduleUITests.start_schedule
    pause_schedule = schedule_fixtures.PageScheduleUITests.pause_schedule
    cancel_schedule = schedule_fixtures.PageScheduleUITests.cancel_schedule

    def test_pause_disables_recheck_even_if_controller_reports_eligible(self):
        row=self.schedule()
        self.store.update(row['id'],state='submitted',phase='publish',attempted_at='Submitted earlier')
        set_result_checks_enabled(self.directory,False)
        self.controller.can_follow_result=Mock(return_value=True)
        self.controller.can_inspect_result=Mock(return_value=True)
        self.controller.follow_result=Mock()
        panel=self.panel()
        self.choose(panel,row)
        self.assertEqual(str(panel.check_button['state']),'disabled')
        self.assertEqual(panel.tree.item(row['id'])['values'][2],'Đã gửi — chưa xác minh')
        self.assertIn('Đã tạm dừng kiểm tra',panel.summary.get())
        panel.open_result_check()
        self.assertIsNone(panel.check_window)
        self.controller.follow_result.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()


class PausedManagerUITests(unittest.TestCase):
    setUp = manager_fixtures.PageManagerUITests.setUp

    def test_only_actual_submitted_unknown_is_labelled_sent_unverified(self):
        rows={}
        for kind in ('sent','unsent'):
            row=self.service.store.save_draft(self.a,self.p,'Synthetic '+kind,
                delivery_mode='browser',browser_finish_mode='tool')
            if kind=='sent':
                self.service.store.transition(self.a,self.p,row['id'],'processing','One prior publish action')
            self.service.store.transition(self.a,self.p,row['id'],'unknown','Synthetic unknown')
            rows[kind]=row
        set_result_checks_enabled(self.studio.data_dir,False)
        self.window.reload_data()
        self.assertEqual(self.window.results_tree.item(rows['sent']['id'])['values'][2],'Đã gửi — chưa xác minh')
        self.assertEqual(self.window.results_tree.item(rows['unsent']['id'])['values'][2],'Chưa rõ kết quả')
        self.assertIn('Đã xác nhận đăng: 0',self.window.results_summary.get())
        self.assertIn('Đã gửi, chưa xác minh: 1',self.window.results_summary.get())
        self.assertIn('Cần kiểm tra: 1',self.window.results_summary.get())
        self.window.results_filter.set('Đã gửi, chưa xác minh')
        self.window.refresh_results()
        self.assertEqual(self.window.results_tree.get_children(),(rows['sent']['id'],))


class PausedStatusTests(unittest.TestCase):
    def test_submitted_is_terminal_unverified_count_without_error_or_success(self):
        row=summarize(None,[],[dict(id='sent',state='submitted',due_at=1,message='Paused result checks')])
        self.assertEqual(row['state'],'submitted')
        self.assertEqual(row['label'],'Đã gửi — chưa xác minh')
        self.assertEqual(row['submitted_posts'],1)
        self.assertEqual(row['published_posts'],0)
        self.assertEqual(row['error_posts'],0)
        self.assertIn('Đã tạm dừng',row['detail'])

    def test_paused_setting_stops_claiming_an_existing_verifier_is_running(self):
        row=summarize(None,[],[dict(id='sent',state='verifying',due_at=1,message='Old verifier')],
            checks_enabled=False)
        self.assertEqual(row['state'],'submitted')
        self.assertEqual(row['verifying_posts'],0)
        self.assertEqual(row['submitted_posts'],1)
        self.assertEqual(row['published_posts'],0)


if __name__=='__main__':
    unittest.main()
