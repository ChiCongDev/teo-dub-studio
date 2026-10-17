"""Durable observation data cannot become proof or belong to another attempt."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import unittest
from unittest.mock import Mock, patch

from teodub.page_activity import PageActivity
from teodub.facebook_web_post import BrowserPagePublisher
from tests import test_page_browser_store as store_fixtures


class BrowserPostTrackingTests(unittest.TestCase):
    def setUp(self):
        store_fixtures.BrowserStoreTests.setUp(self)
        self.snapshot['body'] = 'Bài thử ngoại tuyến'
    draft = store_fixtures.BrowserStoreTests.draft
    state = store_fixtures.BrowserStoreTests.state
    review = store_fixtures.BrowserStoreTests.review

    def submitted(self):
        row = self.review(browser_finish_mode='tool')
        self.state(row, 'submitting')
        return self.state(row, 'processing')

    def tracking(self, row, **changes):
        return dict(dict(version=1, account_id=self.account, page_key=self.page, post_id=row['id'],
                         submitted_at=1700000000.0, deadline_at=1700000300.0,
                         review=deepcopy(row['browser_review']), baseline=dict(statuses=[], articles=[]),
                         tab_url='https://www.facebook.com/syntheticpage', candidates=[]), **changes)

    def save(self, row, tracking):
        self.store.save_browser_tracking(self.account, self.page, row['id'], tracking)
        return self.store.post(self.account, self.page, row['id'])

    def receipt(self, row, url):
        self.store.record_browser_receipt(self.account, self.page, row['id'],
            dict(source='visible_dom', kind='verified_published_reel', url=url, body=row['body'], checked_at='fixture'))

    def test_two_attempts_have_independent_durable_tracking_without_becoming_receipts(self):
        first, second = self.submitted(), self.submitted()
        a = self.tracking(first)
        b = self.tracking(second, submitted_at=1700000010.0, deadline_at=1700000310.0)
        self.save(first, a)
        self.save(second, b)
        reopened = PageActivity(self.directory)
        for row, expected in ((first, a), (second, b)):
            actual = reopened.post(self.account, self.page, row['id'])
            self.assertEqual(actual['browser_tracking'], expected)
            self.assertEqual((actual['state'], actual['browser_receipt'], actual['external_url'], actual['published_at']),
                             ('processing', {}, '', ''))
        a['review']['body'] = 'Changed caller data'
        self.assertNotEqual(reopened.post(self.account, self.page, first['id'])['browser_tracking']['review'], a['review'])

    def test_tracking_rejects_other_scope_changed_review_and_wrong_post_atomically(self):
        row = self.submitted()
        for changed in (dict(account_id=self.other), dict(page_key=self.other_page), dict(post_id='f'*32),
                        dict(review=dict(body='Unreviewed replacement')), dict(version=99)):
            with self.subTest(changed=changed):
                before = self.store.post(self.account, self.page, row['id'])
                with self.assertRaises(ValueError):
                    self.save(row, self.tracking(row, **changed))
                self.assertEqual(self.store.post(self.account, self.page, row['id']), before)
        with self.assertRaises(ValueError):
            self.store.save_browser_tracking(self.other, self.other_page, row['id'], self.tracking(row))
        self.assertEqual(self.store.post(self.account, self.page, row['id']), row)

    def test_saved_submission_identity_time_and_baseline_cannot_be_replaced(self):
        row = self.submitted()
        original = self.tracking(row)
        saved = self.save(row, original)
        for changed in (dict(submitted_at=1700000001.0), dict(deadline_at=1700000301.0),
                        dict(baseline=dict(statuses=[dict(text='Changed historical baseline')], articles=[]))):
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    self.save(row, dict(original, **changed))
                self.assertEqual(self.store.post(self.account, self.page, row['id']), saved)
        progress = dict(original, candidates=['https://www.facebook.com/reel/567'], last_checked_at=1700000010.0)
        updated = self.save(row, progress)
        self.assertEqual(updated['browser_tracking'], progress)
        self.assertEqual(updated['browser_receipt'], {})

    def test_tracking_requires_submitted_unarchived_browser_attempt(self):
        for state in ('draft', 'preparing', 'review', 'failed', 'published'):
            with self.subTest(state=state):
                row = self.review(browser_finish_mode='tool')
                if state != 'review':
                    row = self.state(row, state)
                before = self.store.post(self.account, self.page, row['id'])
                with self.assertRaises(ValueError):
                    self.save(row, self.tracking(row))
                self.assertEqual(self.store.post(self.account, self.page, row['id']), before)
        row = self.submitted()
        with self.store.database() as conn:
            conn.execute('UPDATE posts SET archived=1 WHERE id=?', (row['id'],))
        before = self.store.post(self.account, self.page, row['id'])
        with self.assertRaises(ValueError):
            self.save(row, self.tracking(row))
        self.assertEqual(self.store.post(self.account, self.page, row['id']), before)

    def test_corrupt_initial_descriptor_cannot_enter_recovery_tracking(self):
        row = self.submitted()
        invalid = [dict(version=True), dict(submitted_at=None), dict(submitted_at='1700000000'),
                   dict(submitted_at=float('nan')), dict(submitted_at=float('inf')), dict(submitted_at=-1),
                   dict(deadline_at=None), dict(deadline_at=float('inf')), dict(deadline_at=True),
                   dict(deadline_at=1699999999.0), dict(baseline=[]), dict(baseline=None),
                   dict(review={}), dict(references={})]
        for changed in invalid:
            with self.subTest(changed=changed):
                before = self.store.post(self.account, self.page, row['id'])
                with self.assertRaises(ValueError):
                    self.save(row, self.tracking(row, **changed))
                self.assertEqual(self.store.post(self.account, self.page, row['id']), before)

    def test_submitted_tracking_cannot_be_erased_by_relabeling_post_failed_and_editing(self):
        row = self.submitted()
        self.save(row, self.tracking(row))
        failed = self.state(row, 'failed')
        with self.assertRaises(ValueError):
            self.store.save_draft(self.account, self.page, 'A new reviewed attempt', post_id=row['id'])
        self.assertEqual(self.store.post(self.account, self.page, row['id']), failed)

    def test_serialization_failure_preserves_previous_descriptor_and_database_remains_usable(self):
        row = self.submitted()
        tracking = self.tracking(row)
        saved = self.save(row, tracking)
        with self.assertRaises(TypeError):
            self.save(row, dict(tracking, candidates={'not JSON serializable'}))
        self.assertEqual(self.store.post(self.account, self.page, row['id']), saved)
        self.save(row, dict(tracking, candidates=[]))

    def test_same_actual_receipt_cannot_confirm_two_pending_videos(self):
        first, second = self.submitted(), self.submitted()
        self.receipt(first, 'https://www.facebook.com/reel/567')
        before = self.store.post(self.account, self.page, second['id'])
        with self.assertRaisesRegex(ValueError, 'lượt đăng khác'):
            self.receipt(second, 'https://www.facebook.com/reel/567')
        self.assertEqual(self.store.post(self.account, self.page, second['id']), before)
        self.assertEqual(self.store.post(self.account, self.page, first['id'])['browser_receipt']['url'],
                         'https://www.facebook.com/reel/567')

    def test_equivalent_mobile_and_tracked_urls_are_one_durable_receipt_claim(self):
        first, second = self.submitted(), self.submitted()
        self.receipt(first, 'https://m.facebook.com/reel/567/?ref=notification#tracking')
        self.assertEqual(self.store.post(self.account, self.page, first['id'])['browser_receipt']['url'],
                         'https://www.facebook.com/reel/567')
        before = self.store.post(self.account, self.page, second['id'])
        with self.assertRaises(ValueError):
            self.receipt(second, 'https://www.facebook.com/reel/567')
        self.assertEqual(self.store.post(self.account, self.page, second['id']), before)

    def test_concurrent_receipt_claims_have_only_one_winner(self):
        rows = [self.submitted(), self.submitted()]
        barrier = threading.Barrier(2)

        def claim(row):
            store = PageActivity(self.directory)
            barrier.wait(timeout=3)
            try:
                store.record_browser_receipt(self.account, self.page, row['id'],
                    dict(source='visible_dom', url='https://www.facebook.com/reel/900'))
            except ValueError:
                return False
            return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = list(pool.map(claim, rows))
        self.assertEqual(sorted(winners), [False, True])
        receipts = [self.store.post(self.account, self.page, row['id'])['browser_receipt'] for row in rows]
        self.assertEqual(sum(bool(receipt) for receipt in receipts), 1)

    def test_distinct_insights_reference_ids_survive_receipt_and_published_status(self):
        rows = [self.submitted(), self.submitted()]
        for row, content_id in zip(rows, ('opaqueReferenceA', 'opaqueReferenceB')):
            url = 'https://www.facebook.com/content/insights/?content_id='+content_id
            self.store.record_browser_receipt(self.account, self.page, row['id'],
                dict(source='visible_dom', kind='published_insights', url=url, verification_url=url,
                     public_url='', visible_video=True, video_verified=True))
            result = self.store.transition(self.account, self.page, row['id'], 'published',
                'Verified actual content in Insights', expected={'processing'}, external_url=url)
            self.assertEqual(result['browser_receipt']['url'], url)
            self.assertEqual(result['external_url'], url)
            self.assertEqual(result['browser_receipt']['public_url'], '')
        self.assertNotEqual(self.store.post(self.account, self.page, rows[0]['id'])['external_url'],
                            self.store.post(self.account, self.page, rows[1]['id'])['external_url'])

    def confirm(self, row, receipt=None, **kwargs):
        if receipt is None:
            receipt = dict(source='visible_dom', url='https://www.facebook.com/reel/444', visible_video=True)
        return self.store.confirm_browser_publication(self.account, self.page, row['id'], receipt,
            expected_review=kwargs.pop('expected_review', row['browser_review']), **kwargs)

    def test_atomic_confirmation_records_receipt_state_event_without_inventing_publication_time(self):
        row = self.submitted()
        events_before = self.store.events(self.account, self.page, row['id'])
        result = self.confirm(row)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['external_url'], result['browser_receipt']['url'])
        self.assertEqual(result['published_at'], '')
        events = self.store.events(self.account, self.page, row['id'])
        self.assertEqual(events[:-1], events_before)
        self.assertEqual(events[-1]['state'], 'published')
        self.assertIn(result['external_url'], events[-1]['message'])
        self.assertEqual(PageActivity(self.directory).post(self.account, self.page, row['id']), result)

    def test_atomic_confirmation_preserves_observed_publication_time(self):
        row = self.submitted()
        actual = '2026-10-15T01:48:09+00:00'
        result = self.confirm(row, dict(source='visible_dom', url='https://www.facebook.com/reel/555',
                                        published_at=actual))
        self.assertEqual(result['published_at'], actual)

    def test_atomic_confirmation_requires_frozen_review_and_visible_dom_receipt(self):
        row = self.submitted()
        events = self.store.events(self.account, self.page, row['id'])
        for review in ({}, dict(row['browser_review'], body='Changed after review')):
            with self.subTest(review=review):
                with self.assertRaises(ValueError):
                    self.confirm(row, expected_review=review)
                self.assertEqual(self.store.post(self.account, self.page, row['id']), row)
        with self.assertRaises(ValueError):
            self.confirm(row, dict(source='user_report', url='https://www.facebook.com/reel/444'))
        self.assertEqual(self.store.post(self.account, self.page, row['id']), row)
        self.assertEqual(self.store.events(self.account, self.page, row['id']), events)

    def test_atomic_confirmation_failed_event_insert_rolls_back_state_receipt_and_url(self):
        row = self.submitted()
        events_before = self.store.events(self.account, self.page, row['id'])
        with patch.object(self.store, '_event', side_effect=RuntimeError('Synthetic disk/write failure')):
            with self.assertRaises(RuntimeError):
                self.confirm(row)
        self.assertEqual(self.store.post(self.account, self.page, row['id']), row)
        self.assertEqual(self.store.events(self.account, self.page, row['id']), events_before)
        self.assertEqual(self.confirm(row)['state'], 'published')

    def test_atomic_confirmation_cannot_claim_already_confirmed_result_for_second_video(self):
        first, second = self.submitted(), self.submitted()
        self.confirm(first)
        before_events = self.store.events(self.account, self.page, second['id'])
        with self.assertRaises(ValueError):
            self.confirm(second)
        self.assertEqual(self.store.post(self.account, self.page, second['id']), second)
        self.assertEqual(self.store.events(self.account, self.page, second['id']), before_events)

    def test_atomic_confirmation_rejects_page_changed_since_original_review(self):
        row = self.submitted()
        self.accounts.save_page(self.account, 'Renamed Page', 'facebook.com/syntheticpage', '100', record_id=self.page)
        with self.assertRaises(ValueError):
            self.confirm(row)
        self.assertEqual(self.store.post(self.account, self.page, row['id']), row)

    def test_expired_descriptor_can_be_rechecked_with_fresh_read_only_budget(self):
        row = self.submitted()
        descriptor = self.tracking(row, references=[], notifications={})
        self.save(row, descriptor)
        publisher = BrowserPagePublisher(self.directory)
        context = Mock(pages=[])
        tab = context.new_page.return_value
        captured = {}

        def observe(context, tab, account, page, post_id, post, target, tracking, **kwargs):
            captured.update(tracking=deepcopy(tracking), deadline=kwargs.get('observation_deadline'))
            yield dict(phase='verifying')
            return publisher.store.post(account, page, post_id)

        with patch('teodub.facebook_web_post.wall_time', return_value=1700001000.0), \
                patch.object(publisher, '_files', return_value=row['browser_review']['media']), \
                patch.object(publisher, '_observe_publication_steps', side_effect=observe), \
                patch.object(publisher, 'prepare_steps') as prepare, \
                patch.object(publisher, 'publish_steps') as publish:
            steps = publisher.verify_steps(context, self.account, self.page, row['id'])
            self.assertEqual(next(steps)['phase'], 'verifying')
            with self.assertRaises(StopIteration):
                next(steps)
        self.assertEqual(captured['deadline'], 1700001090.0)
        self.assertEqual(captured['tracking']['submitted_at'], descriptor['submitted_at'])
        self.assertEqual(captured['tracking']['deadline_at'], descriptor['deadline_at'])
        self.assertEqual(self.store.post(self.account, self.page, row['id'])['browser_tracking'], descriptor)
        prepare.assert_not_called()
        publish.assert_not_called()
        tab.get_by_role.assert_not_called()
        tab.click.assert_not_called()
        tab.set_input_files.assert_not_called()


if __name__ == '__main__':
    unittest.main()
