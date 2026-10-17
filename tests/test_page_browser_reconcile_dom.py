"""Read-only result reconciliation in fresh offline, intercepted Chrome."""
from copy import deepcopy
from datetime import datetime
import html
import json
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_page_browser_dom as browser_fixtures
from tests import test_page_browser_reconcile_store as store_fixtures
from tests.test_page_browser_reconcile_store import stamp
from teodub.facebook_web_post import BrowserPagePublisher, _canonical_inspection_url
from teodub.page_browser_reconcile import preview


class BrowserReconcileDomTests(unittest.TestCase):
    setUpClass = browser_fixtures.BrowserDomTests.__dict__['setUpClass']
    tearDownClass = browser_fixtures.BrowserDomTests.__dict__['tearDownClass']
    video = store_fixtures.BrowserReconcileStoreTests.video
    schedule = store_fixtures.BrowserReconcileStoreTests.schedule
    submit = store_fixtures.BrowserReconcileStoreTests.submit
    attempted = store_fixtures.BrowserReconcileStoreTests.attempted
    make_attempt = store_fixtures.BrowserReconcileStoreTests.make_attempt
    duplicate_attempt = store_fixtures.BrowserReconcileStoreTests.duplicate_attempt
    capture = store_fixtures.BrowserReconcileStoreTests.capture
    view = store_fixtures.BrowserReconcileStoreTests.view

    def setUp(self):
        store_fixtures.BrowserReconcileStoreTests.setUp(self)
        self.publisher = BrowserPagePublisher(self.directory)
        self.context = self.browser.new_context()
        self.context.set_offline(True)
        self.addCleanup(self.context.close)
        self.requests = []
        self.document = ''
        def respond(route):
            self.requests.append(route.request.url)
            route.fulfill(content_type='text/html', body=self.document)
        self.context.route('**/*', respond)
        budget = patch('teodub.facebook_web_post.INSPECT_RESULT_WAIT_SECONDS', .08)
        budget.start()
        self.addCleanup(budget.stop)
        # These must never be involved in inspecting an old submitted result.
        for name in ('prepare', 'publish'):
            guard = patch.object(self.publisher, name, side_effect=AssertionError('Inspection must be read only'))
            guard.start()
            self.addCleanup(guard.stop)
        self.row = self.attempted()
        self.expected = self.view(self.row)
        self.url = 'https://www.facebook.com/reel/123456'
        self.document = self.result_html()

    def result_html(self, *, body=None, owner='100', name='Fixture Page', video_count=1,
                    duration=30, width=640, height=360, video_attributes='',
                    timestamp=None, time_extra='', caption_attributes='', extra='', semantic=True):
        body = self.expected['post']['body'] if body is None else body
        clock = '' if timestamp is None else f'<time datetime="{html.escape(timestamp, quote=True)}">Posted</time>'
        card = (f'<a href="https://www.facebook.com/profile.php?id={owner}">{html.escape(name)}</a>'
                f'<div style="white-space:pre-wrap" {caption_attributes}>{html.escape(body)}</div>'
                + clock + time_extra
                + ''.join(f'<video src="blob:fixture-{index}" style="width:120px;height:90px" {video_attributes}></video>'
                          for index in range(video_count)))
        if semantic:
            card = '<article>'+card+'</article>'
        return ('<!doctype html><meta charset="utf-8"><main>'+card+extra+'</main>'
                '<input type="file" onchange="window.uploads++"><button onclick="window.posts++">Post</button>'
                '<script>window.uploads=0;window.posts=0;for(const video of document.querySelectorAll("video")) {'
                + ''.join(f'Object.defineProperty(video,{json.dumps(key)},{{get:()=>{value}}});'
                          for key,value in [('duration',duration),('videoWidth',width),('videoHeight',height)])
                + '}</script>')

    def tab(self, url=None, document=None):
        if document is not None:
            self.document = document
        tab = self.context.new_page()
        tab.goto(url or self.url)
        return tab

    def inspect(self, *, result_url=None, confirmed=True, expected=None, **kwargs):
        return self.publisher.inspect_result(self.context, self.account, self.page, self.row['post_id'],
            result_url=self.url if result_url is None else result_url,
            expected_reconcile=self.expected if expected is None else expected,
            confirmed_attempt=confirmed, **kwargs)

    def unchanged(self, operation):
        before = self.capture(self.row)
        result = operation()
        self.assertEqual(result['state'], 'unverified', result)
        self.assertTrue(result['message'].startswith('Chưa xác minh:'))
        self.assertEqual((result['id'],result['account_id'],result['page_key'],result['schedule_id']),
                         (self.row['post_id'],self.account,self.page,self.row['id']))
        self.assertEqual(self.capture(self.row), before)
        self.assertEqual(self.calls, [])
        for tab in self.context.pages:
            if not tab.is_closed():
                self.assertEqual(tab.evaluate('window.uploads || 0'), 0)
                self.assertEqual(tab.evaluate('window.posts || 0'), 0)
        return result

    def test_unique_confirmed_direct_link_records_original_post_with_zero_uploads_or_publish(self):
        before = self.capture(self.row)
        progress = []
        result = self.inspect(progress=progress.append)
        self.assertEqual(result['state'], 'published', result)
        self.assertEqual(result['id'], self.row['post_id'])
        self.assertEqual(result['schedule_id'], self.row['id'])
        self.assertEqual(result['external_url'], self.url)
        receipt = result['browser_receipt']
        self.assertEqual(receipt['association_mode'], 'unique_attempt_user_link')
        self.assertEqual(receipt['observed_duration'], 30)
        self.assertEqual(receipt['owner_url'], 'https://www.facebook.com/profile.php?id=100')
        self.assertEqual(len(self.capture(self.row)['posts']), len(before['posts']))
        self.assertEqual(self.context.pages[-1].evaluate('[window.uploads,window.posts]'), [0,0])
        self.assertEqual(len(self.context.pages), 1)
        self.assertEqual(self.calls, [])
        self.assertTrue(progress)

    def test_explicit_existing_result_tab_is_reused_without_navigation(self):
        tab = self.tab()
        before = list(self.requests)
        self.assertEqual(self.inspect()['state'], 'published')
        self.assertEqual(self.requests, before)
        self.assertEqual(self.context.pages, [tab])

    def test_visible_time_disambiguates_confirmed_same_video_caption_attempts(self):
        submitted = datetime.fromisoformat(self.expected['submitted_at']).timestamp()
        self.duplicate_attempt(self.row, submitted=int(submitted)-300)
        self.expected = self.view(self.row)
        self.document = self.result_html(timestamp=stamp(submitted+20))
        self.tab()
        result = self.inspect(result_url='')
        self.assertEqual(result['state'], 'published', result)
        self.assertEqual(result['browser_receipt']['association_mode'], 'visible_time')

    def test_matching_timestamp_cannot_bypass_explicit_attempt_confirmation(self):
        submitted = datetime.fromisoformat(self.expected['submitted_at']).timestamp()
        self.document = self.result_html(timestamp=stamp(submitted+20))
        self.unchanged(lambda:self.inspect(confirmed=False))
        self.assertEqual(self.context.pages, [])

    def test_older_identical_caption_timestamp_cannot_mark_selected_attempt_published(self):
        submitted = datetime.fromisoformat(self.expected['submitted_at']).timestamp()
        self.duplicate_attempt(self.row, submitted=int(submitted)-300)
        self.expected = self.view(self.row)
        self.document = self.result_html(timestamp=stamp(submitted-270))
        self.unchanged(self.inspect)

    def test_missing_date_needs_explicit_confirmation_and_unique_local_attempt(self):
        self.unchanged(lambda:self.inspect(confirmed=False))
        self.unchanged(lambda:self.inspect(result_url=''))
        self.duplicate_attempt(self.row, archived=True)
        self.expected = self.view(self.row)
        self.unchanged(self.inspect)

    def test_wrong_full_caption_owner_or_foreign_recommendation_fail_closed(self):
        for params in [dict(body='Caption'),dict(owner='200'),dict(name='Other Page'),
                       dict(owner='200',semantic=False,extra='<section><a href="https://www.facebook.com/profile.php?id=100">Fixture Page</a></section>'),
                       dict(extra='<a href="https://www.facebook.com/profile.php?id=200">Other Page</a>',semantic=False)]:
            with self.subTest(params=params):
                tab = self.tab(document=self.result_html(**params))
                self.unchanged(self.inspect)
                tab.close()

    def test_video_duration_aspect_decoder_metadata_or_ambiguity_cannot_be_skipped(self):
        for params in [dict(duration=35),dict(duration=0),dict(width=0),dict(width=360,height=640),
                       dict(video_count=0),dict(video_count=2),dict(video_attributes='hidden'),
                       dict(video_attributes='aria-hidden="true"'),dict(video_attributes='inert')]:
            with self.subTest(params=params):
                tab = self.tab(document=self.result_html(**params))
                self.unchanged(self.inspect)
                tab.close()
        tab = self.tab(document=self.result_html())
        tab.locator('video').evaluate("e=>e.style.cssText='position:absolute;top:3000px;width:120px;height:90px'")
        self.assertGreater(tab.locator('video').bounding_box()['y'], 3000-1)
        self.unchanged(self.inspect)
        tab.close()

    def test_matching_recommendation_cannot_borrow_foreign_primary_player_permalink(self):
        recommendation = ('<article><a href="https://www.facebook.com/profile.php?id=100">Fixture Page</a>'
                          '<div>'+html.escape(self.expected['post']['body'])+'</div>'
                          '<video src="blob:recommendation" style="width:120px;height:90px"></video></article>')
        for visibility in ['visible','offscreen','foreign-player-hidden']:
            with self.subTest(visibility=visibility):
                tab = self.tab(document=self.result_html(body='Foreign primary caption',owner='200',extra=recommendation))
                if visibility == 'offscreen':
                    tab.locator('video').last.evaluate("e=>e.style.cssText='position:absolute;top:3000px;width:120px;height:90px'")
                elif visibility == 'foreign-player-hidden':
                    tab.locator('video').first.evaluate('e=>e.hidden=true')
                self.unchanged(self.inspect)
                tab.close()

    def test_comments_editor_hidden_and_foreign_dialog_cannot_supply_caption(self):
        for attributes in ['hidden','aria-hidden="true"','inert','contenteditable="true"',
                           'role="textbox"','data-testid="comment-body"']:
            with self.subTest(attributes=attributes):
                tab = self.tab(document=self.result_html(caption_attributes=attributes))
                self.unchanged(self.inspect)
                tab.close()
        foreign = ('<section role="dialog"><div>'+html.escape(self.expected['post']['body'])+'</div></section>')
        tab = self.tab(document=self.result_html(body='Other caption',extra=foreign))
        self.unchanged(self.inspect)
        tab.close()

    def test_authentication_or_visible_create_post_dialog_never_establishes_success(self):
        for extra in ['<input type="password">','<h1>Confirm your identity</h1>',
                      '<section role="dialog" aria-label="Create post"><div contenteditable="true">Ready</div></section>',
                      '<section role="dialog"><h2>Reel settings</h2></section>']:
            with self.subTest(extra=extra):
                tab = self.tab(document=self.result_html(extra=extra))
                self.unchanged(self.inspect)
                tab.close()

    def test_hidden_missing_naive_future_and_conflicting_time_are_conservative(self):
        submitted = datetime.fromisoformat(self.expected['submitted_at']).timestamp()
        for timestamp,time_extra in [(stamp(submitted-20),''),('2026-10-14T01:02:03',''),
                                     ('invalid',''),(stamp(time.time()+3600),''),
                                     (stamp(submitted+20),f'<time datetime="{stamp(submitted+30)}">Other</time>')]:
            with self.subTest(timestamp=timestamp,time_extra=time_extra):
                tab = self.tab(document=self.result_html(timestamp=timestamp,time_extra=time_extra))
                self.unchanged(self.inspect)
                tab.close()

    def test_timestamp_inside_caption_or_comments_does_not_disambiguate_attempt(self):
        self.duplicate_attempt(self.row)
        self.expected = self.view(self.row)
        extra = f'<aside><time datetime="{self.expected["submitted_at"]}">Posted</time></aside>'
        self.tab(document=self.result_html(extra=extra))
        self.unchanged(self.inspect)

    def test_invalid_urls_are_rejected_before_new_tab_or_network_navigation(self):
        for url in ['http://www.facebook.com/reel/123456','https://user@www.facebook.com/reel/123456',
                    'https://www.facebook.com:444/reel/123456','https://example.com/reel/123456',
                    'https://www.facebook.com/profile.php?id=100',
                    'https://www.facebook.com/content/insights/?content_id=UzpfSTopaque']:
            with self.subTest(url=url):
                self.unchanged(lambda:self.inspect(result_url=url))
        self.assertEqual(self.context.pages, [])
        self.assertEqual(self.requests, [])

    def test_actual_direct_post_url_is_supported_with_same_full_video_evidence(self):
        for url in ['https://www.facebook.com/100/posts/123456',
                    'https://www.facebook.com/100/videos/123456',
                    'https://www.facebook.com/permalink.php?id=100&story_fbid=123456']:
            with self.subTest(url=url):
                self.assertTrue(_canonical_inspection_url(url))
        self.url = 'https://www.facebook.com/100/posts/123456'
        self.assertEqual(self.inspect()['state'], 'published')

    def test_duplicate_result_tabs_and_four_owned_tabs_limit_never_guess(self):
        first,second = self.tab(),self.tab()
        self.unchanged(self.inspect)
        first.close();second.close()
        self.document = self.result_html(body='Not the submitted caption')
        for number in range(4):
            self.unchanged(lambda:self.inspect(result_url=f'https://www.facebook.com/reel/{2000+number}'))
        before = len(self.context.pages),len(self.requests)
        self.unchanged(lambda:self.inspect(result_url='https://www.facebook.com/reel/9000'))
        self.assertEqual((len(self.context.pages),len(self.requests)), before)

    def test_cancel_or_finished_before_read_or_before_commit_leaves_data_untouched(self):
        cancel = threading.Event();cancel.set()
        self.unchanged(lambda:self.inspect(cancel=cancel))
        self.assertEqual(self.context.pages, [])
        finished = threading.Event();finished.set()
        self.unchanged(lambda:self.inspect(finished=finished))
        from teodub.page_browser_reconcile import commit_verified
        def cancel_during_hash(store,capture,evidence,**kwargs):
            cancel.set()
            return commit_verified(store,capture,evidence,**kwargs)
        cancel.clear()
        with patch('teodub.page_browser_reconcile.commit_verified',side_effect=cancel_during_hash):
            self.unchanged(lambda:self.inspect(cancel=cancel))

    def test_stale_capture_media_changes_and_review_only_draft_never_reconcile(self):
        stale = deepcopy(self.expected);stale['post']['body'] = 'Changed preview'
        self.unchanged(lambda:self.inspect(expected=stale))
        with self.store.activity.database() as conn:
            conn.execute("UPDATE posts SET state='review' WHERE id=?",(self.row['post_id'],))
        self.unchanged(self.inspect)
        with self.store.activity.database() as conn:
            conn.execute("UPDATE posts SET state='unknown' WHERE id=?",(self.row['post_id'],))
        from pathlib import Path
        Path(self.row['source']['path']).write_bytes(b'Changed local video after submission')
        self.unchanged(self.inspect)
        self.assertEqual(self.context.pages, [])

    def test_url_or_player_navigation_race_before_atomic_commit_cannot_change_status(self):
        from teodub.page_browser_reconcile import commit_verified
        for mutation in ["history.replaceState({},'', '/reel/9000')",
                         "document.querySelector('video').src='blob:different-result'",
                         "document.querySelector('article a').textContent='Different owner'"]:
            with self.subTest(mutation=mutation):
                tab = self.tab()
                def change_before_commit(store,capture,evidence,**kwargs):
                    tab.evaluate(mutation)
                    return commit_verified(store,capture,evidence,**kwargs)
                with patch('teodub.page_browser_reconcile.commit_verified',side_effect=change_before_commit):
                    self.unchanged(self.inspect)
                tab.close()

    def test_removed_schedule_remains_removed_when_old_result_is_verified(self):
        self.store.update(self.row['id'],state='removed')
        self.expected = self.view(self.row)
        self.assertEqual(self.inspect()['state'], 'published')
        self.assertEqual(self.store.get(self.row['id'])['state'], 'removed')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
