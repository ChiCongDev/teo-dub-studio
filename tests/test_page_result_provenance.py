"""Restored tabs are candidates, never evidence of which submission made them."""
from contextlib import nullcontext
from types import SimpleNamespace
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, patch

from teodub.facebook_web_post import AUTH_SCRIPT, BrowserPagePublisher


class ResultProvenanceTests(unittest.TestCase):
    reel = 'https://www.facebook.com/reel/123456789'
    insights = 'https://www.facebook.com/content/insights/?content_id=oldContentReference'

    def observe(self, reference, *, kind='video', sources=None):
        clock = SimpleNamespace(value=0.0)
        target = dict(name='Synthetic Page', page_id='100', url='https://www.facebook.com/100')
        snapshot = dict(body='Identical caption', kind=kind,
            composer=dict(video_preview=[dict(src='blob:uploaded', duration=30, width=720, height=1280)]))
        post = dict(id='a'*32, state='processing', kind=kind, body=snapshot['body'], browser_review=snapshot)
        locator = Mock()
        locator.count.return_value = 0
        tab = Mock(url=target['url'])
        tab.is_closed.return_value = False
        tab.get_by_role.return_value = locator
        observed = dict(statuses=[], articles=[dict(text='Identical caption',
            links=[dict(url='https://www.facebook.com/100/posts/123', name='Synthetic Page')])])
        tab.evaluate.side_effect = lambda script: dict(auth=False,blocked=False) if script == AUTH_SCRIPT else observed
        tab.wait_for_timeout.side_effect = lambda milliseconds: setattr(clock,'value',clock.value+milliseconds/1000)
        result_tab = Mock(url=reference)
        result_tab.is_closed.return_value = False
        reel_tab = Mock(url=self.reel)
        reel_tab.is_closed.return_value = False
        context = SimpleNamespace(pages=[tab,result_tab]+([] if reference == self.reel else [reel_tab]),new_page=Mock())
        publisher = object.__new__(BrowserPagePublisher)
        publisher.store = Mock()
        publisher.store.transition.side_effect = lambda *args,**kwargs: dict(post,state=args[3])
        publisher.store.confirm_browser_publication.return_value = dict(post,state='published')
        publisher._result_checks_enabled = Mock(return_value=True)
        publisher._result_checks_guard = Mock(side_effect=lambda: nullcontext(True))
        publisher._cancel = Mock()
        # These old pages deliberately match every content/media field. The
        # missing link to this submission must still prevent confirmation.
        publisher._publication_evidence = Mock(return_value=dict(url=self.reel,kind='success_status'))
        publisher._new_reel_links = Mock(return_value=[])
        publisher._notification_reels = Mock(return_value=[])
        publisher._insights_reel_links = Mock(return_value=[self.reel])
        publisher._reel_evidence = Mock(return_value=dict(url=self.reel,kind='verified_published_reel',
            body=snapshot['body'],target_id='100',video_verified=True,observed_duration=30,
            observed_width=720,observed_height=1280))
        publisher._insights_evidence = Mock(return_value=None)
        publisher._unambiguous_attempt = Mock(return_value=True)
        tracking = dict(submitted_at=990,deadline_at=1001,baseline=dict(statuses=[],articles=[]),
            references=[reference],reference_sources=sources or {},notifications={})
        with patch('teodub.facebook_web_post.wall_time',return_value=1000), \
                patch('teodub.facebook_web_post.time.monotonic',side_effect=lambda: clock.value):
            iterator = publisher._observe_publication_steps(context,tab,'account','page',post['id'],
                post,target,tracking,trusted_origin=False)
            for _ in range(20):
                try:
                    next(iterator)
                except StopIteration as result:
                    return publisher,result.value
            self.fail('Restored result observer did not stop at its bounded deadline')

    def test_old_matching_open_reel_does_not_confirm_new_submission(self):
        publisher,result = self.observe(self.reel)
        self.assertEqual(result['state'],'unknown')
        publisher.store.confirm_browser_publication.assert_not_called()

    def test_old_matching_insights_cannot_bypass_freshness_by_resolving_its_reel(self):
        publisher,result = self.observe(self.insights)
        self.assertEqual(result['state'],'unknown')
        publisher.store.confirm_browser_publication.assert_not_called()

    def test_restored_text_feed_or_generic_toast_does_not_confirm_old_matching_article(self):
        publisher,result = self.observe(self.reel,kind='text')
        self.assertEqual(result['state'],'unknown')
        publisher.store.confirm_browser_publication.assert_not_called()
        publisher._publication_evidence.assert_not_called()

    def test_reference_bound_to_original_submission_can_still_be_verified(self):
        publisher,result = self.observe(self.reel,sources={self.reel:dict(provenance='direct_status')})
        self.assertEqual(result['state'],'published')
        publisher.store.confirm_browser_publication.assert_called_once()

    def test_prior_confirmed_matching_caption_does_not_permanently_block_later_video(self):
        publisher = object.__new__(BrowserPagePublisher)
        publisher.store = Mock()
        review = dict(composer=dict(video_preview=[dict(duration=30,width=720,height=1280)]))
        current = dict(id='new',kind='video',body='Recurring caption',browser_review=review)
        previous = dict(id='old',kind='video',body='Recurring caption',browser_review=review,
            attempt=1,state='published',browser_receipt=dict(checked_at='Only confirmation time is known'))
        publisher.store.post.return_value = dict(current,browser_tracking=dict(submitted_at=1000))
        publisher.store.posts.return_value = [previous]
        stamp = lambda value: datetime.fromtimestamp(value,timezone.utc).isoformat()
        publisher.store.events.return_value = [dict(state='submitting',at=stamp(980)),
            dict(state='processing',at=stamp(981)),dict(state='published',at=stamp(990))]
        self.assertTrue(publisher._unambiguous_attempt('account','page','new',current))
        # An earlier unconfirmed upload remains a real competing possibility.
        previous['state'] = 'processing'
        publisher.store.events.return_value = publisher.store.events.return_value[:-1]
        self.assertFalse(publisher._unambiguous_attempt('account','page','new',current))


if __name__ == '__main__':
    unittest.main()
