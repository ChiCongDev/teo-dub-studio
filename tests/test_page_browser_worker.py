"""Browser command dispatch and profile isolation, all workers/browser objects mocked."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from tests import test_facebook_browser as fixture
from teodub.facebook_accounts import FacebookAccounts, META_APPS


class PagePostWorkerTests(unittest.TestCase):
    close_under_lock = fixture.FacebookBrowserTests.close_under_lock
    browse = fixture.FacebookBrowserTests.browse

    def setUp(self):
        fixture.FacebookBrowserTests.setUp(self)
        self.accounts = FacebookAccounts(self.temp.name)
        self.account = self.accounts.add_account('Synthetic account')
        self.profile = self.accounts.profile_directory(self.account['id'])
        self.publisher = Mock()
        self.publisher.prepare.return_value = dict(state='review', message='Fixture prepared, not posted')
        self.publisher.publish.return_value = dict(state='unknown', message='Fixture clicked, result unknown')
        self.publisher.focus_review.return_value = dict(state='review', message='Fixture focus only')
        self.factory = self.enterContext(patch('teodub.facebook_web_post.BrowserPagePublisher', return_value=self.publisher))

    def command(self, **extra):
        return dict(dict(action='page_post', operation='prepare', data_dir=self.temp.name,
                    account_id=self.account['id'], page_key='b'*32, post_id='c'*32, request_id='d'*32), **extra)

    def run_commands(self, *commands):
        for command in commands:
            self.commands.put(command)
        count = 0
        def advance(_):
            nonlocal count
            count += 1
            if count >= len(commands):
                self.finished.set()
        self.page.wait_for_timeout.side_effect = advance
        self.assertEqual(self.browse(), 'finished')

    def post_events(self, kind):
        return [payload for event, payload in self.messages if event == kind]

    def test_prepare_publish_share_publisher_context_and_scoped_events(self):
        def prepare(context, account_id, page_key, post_id, **kwargs):
            self.assertIs(context, self.context)
            self.assertIs(kwargs['cancel'], self.cancel)
            self.assertIs(kwargs['finished'], self.finished)
            kwargs['progress']('Fixture observed upload progress')
            return dict(state='review',message='Fixture prepared')
        self.publisher.prepare.side_effect = prepare
        self.run_commands(self.command(), self.command(operation='publish',request_id='e'*32))
        self.factory.assert_called_once_with(self.temp.name)
        self.publisher.publish.assert_called_once()
        self.launch.assert_called_once()
        progress = self.post_events('post_progress')[0]
        self.assertEqual(progress['request_id'], 'd'*32)
        self.assertEqual(progress['operation'], 'prepare')
        self.assertEqual(progress['account_id'], self.account['id'])
        results = self.post_events('post_result')
        self.assertEqual([x['request_id'] for x in results], ['d'*32, 'e'*32])
        self.assertEqual([x['result']['state'] for x in results], ['review', 'unknown'])
        self.context.cookies.assert_not_called()
        self.context.storage_state.assert_not_called()

    def test_review_focuses_only_original_binding_method(self):
        self.run_commands(self.command(operation='review'))
        self.publisher.focus_review.assert_called_once()
        self.publisher.prepare.assert_not_called()
        self.publisher.publish.assert_not_called()
        self.assertEqual(self.post_events('post_result')[0]['operation'], 'review')

    def page_command(self, **extra):
        if not hasattr(self,'navigation_page'):
            self.navigation_page=self.accounts.save_page(self.account['id'],'Page A','facebook.com/profile.php?id=123','123')
        page=self.navigation_page
        return dict(dict(action='open_page',data_dir=self.temp.name,account_id=self.account['id'],
                         page_key=page['id'],url=page['url'],request_id='f'*32),**extra)

    def test_open_page_activates_actor_before_navigation_success(self):
        command=self.page_command()
        tab=Mock(url=command['url']);self.context.new_page.return_value=tab
        with patch('teodub.facebook_page_identity.ensure_page_actor',return_value=dict(switched=True)) as actor:
            self.run_commands(command)
        self.assertEqual(actor.call_args.args[0:2],(self.context,tab))
        self.assertEqual(actor.call_args.args[2]['page_id'],'123')
        self.assertEqual(tab.goto.call_count,2)
        events=[value for kind,value in self.messages if kind=='navigation']
        self.assertEqual(events[-1]['state'],'opened')
        self.assertIn('danh tính Page',events[-1]['message'])
        self.publisher.prepare.assert_not_called();self.publisher.publish.assert_not_called()

    def test_actor_failure_is_navigation_error_not_opened_page(self):
        command=self.page_command()
        tab=Mock(url=command['url']);self.context.new_page.return_value=tab
        with patch('teodub.facebook_page_identity.ensure_page_actor',side_effect=ValueError('Không có quyền Page')):
            self.run_commands(command)
        events=[value for kind,value in self.messages if kind=='navigation']
        self.assertEqual(events[-1]['state'],'error')
        self.assertIn('Không có quyền Page',events[-1]['message'])
        self.publisher.publish.assert_not_called()

    def test_initial_page_navigation_uses_same_account_profile_and_reports_actor_failure(self):
        command=self.page_command()
        self.context.new_page.return_value=Mock(url=command['url'])
        with patch('teodub.facebook_page_identity.ensure_page_actor',side_effect=ValueError('Cần đăng nhập')):
            self.assertEqual(self.browse(url=command['url'],page_navigation=command,request_id='f'*32),'finished')
        events=[value for kind,value in self.messages if kind=='navigation']
        self.assertEqual(events[-1]['state'],'error')
        opened=next(message for kind,message in self.messages if kind=='opened')
        self.assertIn('chưa chuyển được Page',opened)
        self.assertIn('Cần đăng nhập',opened)
        self.publisher.publish.assert_not_called()

    def test_wrong_profile_or_changed_url_never_starts_page_identity_switch(self):
        other=self.accounts.add_account('Other')
        with patch('teodub.facebook_page_identity.ensure_page_actor') as actor:
            self.run_commands(self.page_command(account_id=other['id']),self.page_command(url='https://www.facebook.com/456'))
        actor.assert_not_called();self.context.new_page.assert_not_called()
        events=[value for kind,value in self.messages if kind=='navigation']
        self.assertEqual([item['state'] for item in events],['error','error'])

    def test_wrong_account_rejected_before_publisher_or_page_actions(self):
        other = self.accounts.add_account('Other synthetic account')
        self.run_commands(self.command(account_id=other['id']))
        self.factory.assert_not_called()
        self.assertIn('hồ sơ/tài khoản khác', self.post_events('post_error')[0]['message'])
        self.assertEqual(self.page.goto.call_count, 1)  # Only initial browser home.
        self.context.new_page.assert_not_called()

    def test_wrong_data_directory_profile_rejected_even_same_uuid(self):
        with tempfile.TemporaryDirectory() as unrelated:
            other = FacebookAccounts(unrelated)
            other.directory.mkdir(parents=True)
            # Synthetic copy of the registry UUID cannot authorize a different root.
            other.path.write_text(self.accounts.path.read_text(encoding='utf-8'),encoding='utf-8')
            self.run_commands(self.command(data_dir=unrelated))
        self.factory.assert_not_called()
        self.assertIn('hồ sơ/tài khoản khác', self.post_events('post_error')[0]['message'])

    def test_unknown_operation_invalid_ids_and_missing_request_produce_request_errors(self):
        commands = [self.command(operation='auto_publish'), self.command(account_id='../bad'),
                    self.command(page_key='wrong'), self.command(request_id=''), self.command(data_dir='')]
        self.run_commands(*commands)
        self.factory.assert_not_called()
        self.assertEqual(len(self.post_events('post_error')), len(commands))
        self.assertTrue(all(x['message'] for x in self.post_events('post_error')))
        self.assertFalse(self.post_events('post_result'))

    def test_unexpected_dom_failure_has_per_request_error_and_browser_can_navigate_next(self):
        self.publisher.prepare.side_effect = RuntimeError('Fixture unexpected DOM changed')
        tab = Mock(url='about:blank')
        self.context.new_page.return_value = tab
        self.run_commands(self.command(), dict(action='open_url',url=META_APPS,request_id='e'*32))
        self.publisher.prepare.assert_called_once()
        error = self.post_events('post_error')[0]
        self.assertEqual(error['message'], 'Fixture unexpected DOM changed')
        self.assertEqual(error['request_id'], 'd'*32)
        self.assertFalse(self.post_events('post_result'))
        tab.goto.assert_called_once_with(META_APPS, wait_until='domcontentloaded', timeout=45000)
        self.launch.assert_called_once()

    def test_expired_or_cancelled_queued_request_has_no_publisher_or_page_actions(self):
        cancelled = threading.Event()
        cancelled.set()
        self.run_commands(self.command(expires_at=time.monotonic()-1), self.command(cancel_request=cancelled))
        self.factory.assert_not_called()
        errors = self.post_events('post_error')
        self.assertEqual(len(errors), 2)
        self.assertIn('hết hạn', errors[0]['message'])
        self.assertIn('đã bị hủy', errors[1]['message'])
        self.assertEqual(self.page.goto.call_count, 1)
        self.context.new_page.assert_not_called()

    def test_request_cancel_combines_with_worker_cancel_inside_operation(self):
        request_cancel = threading.Event()
        def prepare(context, account_id, page_key, post_id, **kwargs):
            combined = kwargs['cancel']
            self.assertFalse(combined.is_set())
            request_cancel.set()
            self.assertTrue(combined.is_set())
            request_cancel.clear()
            self.cancel.set()
            self.assertTrue(combined.is_set())
            self.cancel.clear()
            return dict(state='review',message='Fixture cancellation check')
        self.publisher.prepare.side_effect = prepare
        self.run_commands(self.command(cancel_request=request_cancel,expires_at=time.monotonic()+60))
        self.publisher.prepare.assert_called_once()


if __name__ == '__main__':
    unittest.main()
