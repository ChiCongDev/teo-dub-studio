"""The result-check switch never changes the one-click publishing boundary."""
from unittest.mock import Mock, patch
import unittest

from teodub.page_publish_settings import set_result_checks_enabled
from tests import test_page_browser_dom as fixture


class PausedPublisherTests(unittest.TestCase):
    setUpClass = fixture.BrowserDomTests.__dict__['setUpClass']
    tearDownClass = fixture.BrowserDomTests.__dict__['tearDownClass']
    setUp = fixture.BrowserDomTests.setUp
    draft = fixture.BrowserDomTests.draft
    prepare = fixture.BrowserDomTests.prepare
    saved = fixture.BrowserDomTests.saved
    composer_tab = fixture.BrowserDomTests.composer_tab

    def pause_checks(self):
        set_result_checks_enabled(self.temp.name, False)

    def test_paused_before_publish_clicks_once_and_does_not_open_result_pages(self):
        post = self.draft('Publish once without result observation')
        self.prepare(post)
        tab = self.composer_tab()
        pages_before = list(self.context.pages)
        self.pause_checks()

        result = self.publisher.publish(
            self.context, self.account['id'], self.target['id'], post['id'])

        self.assertTrue(result['verification_paused'])
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(result['external_url'], '')
        self.assertEqual(result['browser_receipt'], {})
        self.assertEqual(tab.evaluate('window.posts'), 1)
        self.assertEqual(self.context.pages, pages_before)
        self.assertIn('Đã gửi — chưa xác minh', result['message'])
        with self.assertRaises(ValueError):
            self.publisher.publish(
                self.context, self.account['id'], self.target['id'], post['id'])
        self.assertEqual(tab.evaluate('window.posts'), 1)

    def test_turning_off_a_live_observer_stops_before_result_dom_or_receipt(self):
        post = self.draft('Stop a live result observer')
        self.prepare(post)
        tab = self.composer_tab()
        iterator = self.publisher.publish_steps(
            self.context, self.account['id'], self.target['id'], post['id'])

        self.assertEqual(next(iterator), {'phase': 'verifying'})
        self.assertEqual(tab.evaluate('window.posts'), 1)
        self.assertEqual(self.saved(post)['state'], 'processing')
        self.pause_checks()

        with self.assertRaises(StopIteration) as stopped:
            next(iterator)
        result = stopped.exception.value
        self.assertTrue(result['verification_paused'])
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(result['external_url'], '')
        self.assertEqual(result['browser_receipt'], {})
        self.assertEqual(tab.evaluate('window.posts'), 1)

    def test_disabled_verify_and_inspect_do_not_touch_browser_context(self):
        self.pause_checks()
        context = Mock()

        with self.assertRaisesRegex(ValueError, 'tạm tắt'):
            self.publisher.verify(
                context, self.account['id'], self.target['id'], 'f' * 32)
        inspected = self.publisher.inspect_result(
            context, self.account['id'], self.target['id'], 'f' * 32,
            result_url='https://www.facebook.com/reel/123',
            expected_reconcile={}, confirmed_attempt=True)

        self.assertTrue(inspected['verification_paused'])
        self.assertEqual(inspected['state'], 'unverified')
        self.assertEqual(context.mock_calls, [])

    def test_pause_at_an_expired_observer_boundary_returns_paused_marker(self):
        from teodub.facebook_web_post import wall_time

        post = self.draft('Pause exactly when the result deadline expires')
        self.prepare(post)
        scope = (self.account['id'], self.target['id'], post['id'])
        self.publisher.store.transition(*scope, 'submitting', 'Synthetic click boundary', expected={'review'})
        self.publisher.store.transition(*scope, 'processing', 'Synthetic click completed', expected={'submitting'})
        post = self.saved(post)
        tracking = dict(submitted_at=wall_time()-10, deadline_at=wall_time()-1,
            baseline=dict(statuses=[], articles=[]), references=[], notifications={})
        iterator = self.publisher._observe_publication_steps(
            self.context, self.composer_tab(), *scope, post,
            self.target, tracking)

        self.assertEqual(next(iterator), {'phase': 'verifying'})
        self.pause_checks()
        with self.assertRaises(StopIteration) as stopped:
            next(iterator)

        result = stopped.exception.value
        self.assertTrue(result['verification_paused'])
        self.assertEqual(result['state'], 'unknown')
        self.assertIn('Đã gửi — chưa xác minh', result['message'])

    def test_pause_after_verify_entry_stops_before_hash_or_browser_access(self):
        post = self.draft('Pause after a verify request entered')
        self.prepare(post)
        scope = (self.account['id'], self.target['id'], post['id'])
        self.publisher.store.transition(*scope, 'submitting', 'Synthetic click boundary', expected={'review'})
        self.publisher.store.transition(*scope, 'processing', 'Synthetic click completed', expected={'submitting'})
        context = Mock()

        with patch.object(self.publisher, '_result_checks_enabled', side_effect=[True, False]), \
                patch.object(self.publisher, '_files') as files:
            result = self.publisher.verify(context, *scope)

        self.assertTrue(result['verification_paused'])
        self.assertEqual(result['state'], 'unknown')
        files.assert_not_called()
        self.assertEqual(context.mock_calls, [])


if __name__ == '__main__':
    unittest.main()
