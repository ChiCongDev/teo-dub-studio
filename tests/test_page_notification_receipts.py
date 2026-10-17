"""Fresh notification receipts in isolated, intercepted Chrome; no live profiles."""
import html
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from playwright.sync_api import Locator
from tests import test_page_browser_dom as fixtures
from teodub.common import Cancelled
from teodub.facebook_web_post import (
    BrowserPagePublisher, RESULT_SCRIPT, _canonical_insights_reference,
)


OPAQUE_ID = 'UzpfSTYxNTk1MjU4OTE1ODM0OjEyMjEwMDQ0OTUwNTUwODYzMDoxMjIxMDA0NDk1MDU1MDg2MzA='


class NotificationReceiptTests(unittest.TestCase):
    setUpClass = fixtures.BrowserDomTests.__dict__['setUpClass']
    tearDownClass = fixtures.BrowserDomTests.__dict__['tearDownClass']
    draft = fixtures.BrowserDomTests.draft
    prepare = fixtures.BrowserDomTests.prepare
    publish = fixtures.BrowserDomTests.publish
    saved = fixtures.BrowserDomTests.saved
    composer_tab = fixtures.BrowserDomTests.composer_tab

    def setUp(self):
        fixtures.BrowserDomTests.setUp(self)
        # A fulfilled HTTP redirect can bypass Playwright request routing for
        # its follow-up request. Offline mode guarantees no fixture can fall
        # through to Facebook; modeled redirects use intercepted JS navigation.
        self.context.set_offline(True)
        budget = patch('teodub.facebook_web_post.PUBLISH_RESULT_SECONDS', 3)
        budget.start()
        self.addCleanup(budget.stop)

    def notice_url(self, *, path='/content/insights/', stamp=None, kind='fb_shorts_video_processed',
                   content_id=OPAQUE_ID, prefix='https://www.facebook.com'):
        query = dict(notif_t=kind, notif_id=str(int(time.time()*1e6) if stamp is None else stamp))
        if content_id is not None:
            query['content_id'] = content_id
        return prefix + path + '?' + urlencode(query)

    def notice_panel(self, url, text='Your reel is published', *, attributes='', link_attributes=''):
        return (f'<section role="dialog" aria-label="Notifications" {attributes}>'
                f'<a href="{html.escape(url, quote=True)}" {link_attributes}>{text}</a></section>')

    def notice_tab(self, source):
        tab = self.context.new_page()
        tab.goto(self.target['url'])
        tab.set_content('<meta charset="utf-8">' + source)
        return tab

    def insights_html(self, body, *, owner='123', caption=None, links=None, container='article'):
        caption = body if caption is None else caption
        links = ['https://www.facebook.com/reel/999'] if links is None else links
        return ('<meta charset="utf-8"><main><' + container + '>'
                f'<a href="https://www.facebook.com/profile.php?id={owner}">Page Demo</a>'
                '<div style="white-space:pre-wrap">' + html.escape(caption) + '</div>'
                '<video src="blob:posted" style="width:120px;height:120px"></video>'
                + ''.join(f'<a href="{html.escape(url, quote=True)}">View reel</a>' for url in links)
                + '</' + container + '></main>')

    def generic_insights_html(self, body, *, owner='123', permalink='https://www.facebook.com/123/videos/999',
                              metadata=(30, 720, 1280), timestamp='', actions=True):
        """Screenshot-shaped Insights: generic left card, statistics on right."""
        source = ('<meta charset="utf-8"><main role="main"><h1>Post insights</h1>'
            '<div style="display:grid;grid-template-columns:400px 600px"><div id="published-card">'
            f'<div><a href="https://www.facebook.com/profile.php?id={owner}">Page Demo</a>'
            + (f'<a href="{html.escape(permalink, quote=True)}">1m</a>' if permalink else '<span>1m</span>')
            + (f'<time datetime="{html.escape(timestamp, quote=True)}">Posted</time>' if timestamp else '')
            + '</div><div><p style="white-space:pre-wrap">' + html.escape(body) + '</p></div>'
            '<div><video src="blob:published-video" style="width:200px;height:300px"></video></div>'
            + ('<div><button>Like</button><button>Comment</button><button>Share</button></div>' if actions else '')
            + '</div><div><h2>Views over time</h2><p>Insights not available</p>'
            '<h2>Audience retention</h2></div></div></main>')
        if metadata is not None:
            duration, width, height = metadata
            source += '<script>Object.defineProperties(document.querySelector("video"),'
            source += json.dumps({key:dict(value=value) for key,value in
                zip(('duration', 'videoWidth', 'videoHeight'), (duration, width, height))}) + ');</script>'
        return source

    def reel_html(self, body, *, owner='123', semantic=False, recommendation=False, source='blob:posted'):
        card = (f'<a href="https://www.facebook.com/profile.php?id={owner}">Page Demo</a>'
                '<div style="white-space:pre-wrap">' + html.escape(body) + '</div>'
                f'<video src="{source}" style="width:120px;height:120px"></video>')
        if semantic:
            card = '<article>' + card + '</article>'
        extra = '<section><a href="https://www.facebook.com/profile.php?id=123">Page Demo</a></section>' if recommendation else ''
        return ('<meta charset="utf-8"><main role="main">' + card + extra + '</main>'
            '<script>document.querySelectorAll("video").forEach(video=>Object.defineProperties(video,'
            '{duration:{value:30},videoWidth:{value:720},videoHeight:{value:1280}}));</script>')

    def video_snapshot(self, body):
        return dict(kind='video', body=body, composer=dict(video_preview=[dict(duration=30,width=720,height=1280)]))

    def outside_main_insights_html(self, body):
        source = self.generic_insights_html(body, permalink='')
        card = source.split('<div id="published-card">',1)[1].split('</div><div><h2>Views over time</h2>',1)[0]
        metadata = source.split('</main>',1)[1]
        return ('<meta charset="utf-8"><h1>Post insights</h1><div id="application">'
            '<aside role="complementary"><div id="published-card">'+card+'</div></aside>'
            '<main><h2>Views over time</h2></main></div>'+metadata)

    def real_nested_insights_html(self, body):
        """Observed depth9 media-only wrapper, depth10 published post card."""
        source = self.outside_main_insights_html(body).replace('<h1>Post insights</h1>','')
        source = source.replace('<aside role="complementary">','<div role="navigation"><h1>Post insights</h1>')
        source = source.replace('</aside>','</div>')
        source = source.replace('<div><p style="white-space:pre-wrap">','<div id="media-only"><div><p style="white-space:pre-wrap">')
        source = source.replace('<div><button>Like</button>',
            '<div><button>Play</button><button>Mute</button><button>Settings</button>'
            '<button>Fullscreen</button><button>Picture in picture</button></div></div><div><button>Like</button>')
        return source

    def load_document(self, url, source):
        self.context.route(url, lambda route: route.fulfill(content_type='text/html', body=source))
        tab = self.context.new_page()
        tab.goto(url)
        return tab

    def post_fixture(self, paths=None):
        paths = paths or ['/content/insights/']
        bases = []
        for path in paths:
            query = dict(notif_t='fb_shorts_video_processed')
            if path == '/content/insights/':
                query['content_id'] = OPAQUE_ID
            bases.append('https://www.facebook.com' + path + '?' + urlencode(query) + '&notif_id=')
        notices = '''const panel=document.createElement('section');panel.setAttribute('role','dialog');
            panel.setAttribute('aria-label','Notifications');
            for(const base of BASES){const link=document.createElement('a');
              link.href=base+String(Date.now()*1000);link.textContent='Your reel is published';panel.append(link);}
            document.body.append(panel);'''.replace('BASES', json.dumps(bases))
        self.html = fixtures.reel_fixture().replace('document.body.append(done);', notices)
        path = Path(self.temp.name).resolve() / 'receipt fixture.mp4'
        path.write_bytes(b'Offline notification receipt fixture; never sent outside intercepted Chrome.')
        return self.draft('Exact caption.\n\n#MinhChau', paths=[path])

    def route_receipt(self, pattern, source, seen):
        def respond(route):
            seen.append(route.request.url)
            route.fulfill(content_type='text/html', body=source)
        self.context.route(pattern, respond)

    def test_actual_opaque_insights_is_published_is_a_reference_not_a_receipt(self):
        started = time.time()
        url = self.notice_url(stamp=int(started*1e6))
        tab = self.notice_tab(self.notice_panel(url))
        candidates = self.publisher._notification_reels(tab, started)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['kind'], 'insights_reference')
        self.assertEqual(candidates[0]['url'], _canonical_insights_reference(url))
        self.assertIn('UzpfST', candidates[0]['url'])
        self.assertNotIn('/reel/', candidates[0]['url'])

    def test_direct_reel_publication_wording_and_relative_links(self):
        started = time.time()
        for text in ('Your reel is published', 'Your reel was published', 'Your reel has been published',
                     'Reel published', 'Đã đăng thước phim'):
            with self.subTest(text=text):
                tab = self.notice_tab(self.notice_panel(self.notice_url(path='/reel/999', stamp=int(started*1e6)), text))
                self.assertEqual(self.publisher._notification_reels(tab, started)[0]['url'],
                                 'https://www.facebook.com/reel/999')
        tab = self.notice_tab(self.notice_panel(self.notice_url(prefix='', stamp=int(started*1e6))))
        self.assertEqual(len(self.publisher._notification_reels(tab, started)), 1)

    def test_old_missing_future_wrong_type_and_non_publication_notices_are_ignored(self):
        started = time.time()
        for url, text in (
            (self.notice_url(stamp=int((started-3600)*1e6)), 'Your reel is published'),
            (self.notice_url(stamp=''), 'Your reel is published'),
            (self.notice_url(stamp=int((started+60)*1e6)), 'Your reel is published'),
            (self.notice_url(kind='other'), 'Your reel is published'),
            (self.notice_url(), 'Your reel is not published'),
            (self.notice_url(), 'Your video is partially blocked'),
            (self.notice_url(), 'Your reel is uploading'),
        ):
            with self.subTest(url=url, text=text):
                self.assertEqual(self.publisher._notification_reels(self.notice_tab(self.notice_panel(url, text)), started), [])

    def test_notification_host_scheme_credentials_port_path_and_identifier_are_strict(self):
        started = time.time()
        good = self.notice_url(stamp=int(started*1e6))
        for bad in (good.replace('https:', 'http:'), good.replace('www.facebook.com', 'facebook.com.evil.example'),
                    good.replace('www.facebook.com', 'user:password@www.facebook.com'),
                    good.replace('www.facebook.com', 'www.facebook.com:444'),
                    good.replace('/content/insights/', '/login/'),
                    self.notice_url(content_id=None), self.notice_url(content_id=''),
                    self.notice_url(content_id='opaque with space'), good + '&content_id=another',
                    good + '&notif_id=123', good + '&notif_t=other'):
            with self.subTest(bad=bad):
                self.assertEqual(self.publisher._notification_reels(self.notice_tab(self.notice_panel(bad)), started), [])

    def test_hidden_multiple_foreign_dialog_and_duplicate_notices(self):
        started = time.time()
        panel = self.notice_panel(self.notice_url(stamp=int(started*1e6)))
        for source in (self.notice_panel(self.notice_url(), attributes='hidden'), panel + panel,
                       self.notice_panel(self.notice_url(), link_attributes='aria-hidden="true"'),
                       '<section role="dialog" aria-label="Notifications"><section role="dialog" aria-label="Other">'
                       '<a href="' + html.escape(self.notice_url(), quote=True) + '">Your reel is published</a></section></section>'):
            with self.subTest(source=source):
                self.assertEqual(self.publisher._notification_reels(self.notice_tab(source), started), [])
        duplicate = panel.replace('</section>', panel.split('>', 1)[1].rsplit('</section>', 1)[0] + '</section>')
        self.assertEqual(len(self.publisher._notification_reels(self.notice_tab(duplicate), started)), 1)

    def test_atomic_snapshot_has_no_per_link_locator_wait_and_survives_detachment(self):
        started = time.time()
        tab = self.notice_tab(self.notice_panel(self.notice_url(stamp=int(started*1e6))))
        original = Locator.evaluate_all
        def detached(locator, expression, *args, **kwargs):
            rows = original(locator, expression, *args, **kwargs)
            tab.evaluate('document.querySelectorAll("[role=dialog]").forEach(e=>e.remove())')
            return rows
        with patch.object(Locator, 'inner_text', side_effect=AssertionError('No per-link wait')), \
                patch.object(Locator, 'get_attribute', side_effect=AssertionError('No per-link wait')), \
                patch.object(Locator, 'evaluate_all', detached):
            self.assertEqual(len(self.publisher._notification_reels(tab, started)), 1)
        self.assertEqual(self.publisher._notification_reels(tab, started), [])

    def test_insights_resolves_only_visible_link_beside_exact_caption_and_page_owner(self):
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Full caption\n\n#Tag')
        for container in ('article', 'section role="group"'):
            with self.subTest(container=container):
                # Semantic content cards must include their actual visible player.
                source = self.insights_html(snapshot['body']).replace('<article>', '<' + container + '>').replace('</article>', '</section>' if 'section' in container else '</article>')
                tab = self.load_document(reference, source)
                self.assertEqual(self.publisher._insights_reel_links(tab, reference, self.target, snapshot),
                                 ['https://www.facebook.com/reel/999'])

    def test_insights_missing_ambiguous_hidden_wrong_owner_caption_and_auth_fail_closed(self):
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact caption')
        base = self.insights_html(snapshot['body'])
        variants = (
            self.insights_html(snapshot['body'], owner='999'),
            self.insights_html(snapshot['body'], caption='Truncated…'),
            self.insights_html(snapshot['body'], links=[]),
            self.insights_html(snapshot['body'], links=['https://www.facebook.com/reel/999', 'https://www.facebook.com/reel/888']),
            base.replace('>View reel</a>', ' aria-hidden="true">View reel</a>'),
            base.replace('<article>', '<article hidden>'),
            base.replace('<article>', '').replace('</article>', ''),
            base.replace('<video ', '<video hidden '),
            base + '<input type="password">',
            base.replace('<div style=', '<div aria-label="Comment" style='),
        )
        for source in variants:
            with self.subTest(source=source):
                tab = self.load_document(reference, source)
                self.assertEqual(self.publisher._insights_reel_links(tab, reference, self.target, snapshot), [])

    def test_generic_insights_card_resolves_observed_real_permalink_formats(self):
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact full caption\n\n#MinhChau')
        for permalink in ('https://www.facebook.com/reel/999?tracking=1',
                          'https://www.facebook.com/123/videos/999?tracking=1',
                          'https://www.facebook.com/123/posts/999/',
                          'https://www.facebook.com/story.php?story_fbid=999&id=123&tracking=1',
                          'https://www.facebook.com/permalink.php?id=123&story_fbid=999'):
            with self.subTest(permalink=permalink):
                tab = self.load_document(reference, self.generic_insights_html(snapshot['body'], permalink=permalink))
                resolved = self.publisher._insights_reel_links(tab, reference, self.target, snapshot)
                self.assertEqual(len(resolved), 1)
                self.assertNotIn('tracking', resolved[0])
                direct = self.load_document(resolved[0], self.reel_html(snapshot['body']))
                self.assertIsNotNone(self.publisher._reel_evidence(direct, resolved[0], self.target, snapshot))

    def test_generic_insights_never_borrows_sibling_video_owner_or_permalink(self):
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact caption')
        base = self.generic_insights_html(snapshot['body'])
        for source in (base.replace('<video ', '<video hidden '),
                       base.replace('id=123', 'id=999'),
                       base.replace('<div id="published-card">', '<div id="published-card" aria-hidden="true">'),
                       base.replace('<a href="https://www.facebook.com/123/videos/999">1m</a>', ''),
                       base.replace('</main>', '<div><video src="blob:recommended" width="120" height="120"></video></div></main>'),
                       base.replace('</main>', '<section role="group"><a href="https://www.facebook.com/reel/777">Suggested</a></section></main>')
                           .replace('<a href="https://www.facebook.com/123/videos/999">1m</a>', '')):
            with self.subTest(source=source):
                tab = self.load_document(reference, source)
                self.assertEqual(self.publisher._insights_reel_links(tab, reference, self.target, snapshot), [])

    def test_direct_video_receipt_rejects_conflicting_numeric_permalink_owner(self):
        snapshot = self.video_snapshot('Exact caption')
        for url in ('https://www.facebook.com/999/videos/888', 'https://www.facebook.com/999/posts/888',
                    'https://www.facebook.com/story.php?id=999&story_fbid=888'):
            with self.subTest(url=url):
                tab = self.load_document(url, self.reel_html(snapshot['body']))
                self.assertIsNone(self.publisher._reel_evidence(tab, url, self.target, snapshot))

    def test_loaded_video_duration_and_aspect_must_match_frozen_review(self):
        snapshot = dict(kind='video', body='Exact caption', composer=dict(video_preview=[
            dict(duration=30, width=720, height=1280)]))
        url = 'https://www.facebook.com/123/videos/999'
        for metadata, accepted in (((30.2, 360, 640), True), ((40, 720, 1280), False),
                                   ((30, 1280, 720), False), (None, False)):
            with self.subTest(metadata=metadata):
                source = self.generic_insights_html(snapshot['body'], metadata=metadata)
                tab = self.load_document(url, source)
                evidence = self.publisher._reel_evidence(tab, url, self.target, snapshot)
                self.assertEqual(evidence is not None, accepted)
                if accepted:
                    self.assertTrue(evidence['video_verified'])
                    self.assertEqual(evidence['observed_duration'], metadata[0])
        tab = self.load_document(url, self.generic_insights_html(snapshot['body']))
        for frozen in ([], [dict(duration=0,width=720,height=1280)],
                       [dict(duration=True,width=720,height=1280)],
                       [dict(duration=float('nan'),width=720,height=1280)]):
            with self.subTest(frozen=frozen):
                invalid = dict(snapshot, composer=dict(video_preview=frozen))
                self.assertIsNone(self.publisher._reel_evidence(tab, url, self.target, invalid))
        self.assertIsNone(self.publisher._reel_evidence(tab, url, self.target,
            dict(kind='video',body=snapshot['body'])))

    def test_published_insights_without_permalink_requires_loaded_identity_and_fresh_notice(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = dict(kind='video', body='Exact caption', composer=dict(video_preview=[
            dict(duration=30, width=720, height=1280)]))
        source = self.generic_insights_html(snapshot['body'], permalink='')
        tab = self.load_document(reference, source)
        self.assertEqual(self.publisher._insights_reel_links(tab, reference, self.target, snapshot), [])
        evidence = self.publisher._insights_evidence(tab, reference, self.target, snapshot,
            submitted_at=submitted, notified_at=submitted+5)
        self.assertEqual(evidence['kind'], 'published_insights')
        self.assertEqual(evidence['url'], reference)
        self.assertEqual(evidence['verification_url'], reference)
        self.assertEqual(evidence['public_url'], '')
        self.assertEqual(evidence['published_at'], '')
        self.assertEqual(evidence['association_mode'], 'fresh_notification')
        for notified in (None, submitted-1, time.time()+60, float('nan'), True):
            with self.subTest(notified=notified):
                self.assertIsNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
                    submitted_at=submitted, notified_at=notified))

    def test_published_insights_rejects_missing_metadata_actions_draft_auth_and_ambiguity(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = dict(kind='video', body='Exact caption', composer=dict(video_preview=[
            dict(duration=30, width=720, height=1280)]))
        source = self.generic_insights_html(snapshot['body'], permalink='')
        for document in (self.generic_insights_html(snapshot['body'], metadata=None),
                         self.generic_insights_html(snapshot['body'], metadata=(60, 720, 1280)),
                         self.generic_insights_html(snapshot['body'], metadata=(30, 1280, 720)),
                         self.generic_insights_html(snapshot['body'], owner='999'),
                         self.generic_insights_html(snapshot['body'], actions=False),
                         source.replace('<h1>Post insights</h1>', '<h1>Draft</h1>'),
                         source + '<input type="password">',
                         source + '<section role="dialog" aria-label="Create post"><textarea></textarea></section>',
                         source.replace('</main>', '<video src="blob:another" width="120" height="120"></video></main>')):
            with self.subTest(document=document):
                tab = self.load_document(reference, document)
                self.assertIsNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
                    submitted_at=submitted, notified_at=submitted+5))
        tab = self.load_document(reference, source)
        for invalid_snapshot in (dict(kind='video', body=snapshot['body']),
                                 dict(snapshot, body='Caption…'), dict(snapshot, kind='text')):
            self.assertIsNone(self.publisher._insights_evidence(tab, reference, self.target, invalid_snapshot,
                submitted_at=submitted, notified_at=submitted+5))

    def test_insights_action_footer_can_be_a_sibling_without_borrowing_other_content(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = dict(kind='video', body='Exact caption', composer=dict(video_preview=[
            dict(duration=30, width=720, height=1280)]))
        source = self.generic_insights_html(snapshot['body'], permalink='')
        # The content wrapper ends before the footer, as on generic div layouts.
        source = source.replace('<div id="published-card">', '<div id="published-card"><div>')
        source = source.replace('<div><button>Like</button>', '</div><div><button>Like</button>')
        tab = self.load_document(reference, source)
        self.assertIsNotNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
            submitted_at=submitted, notified_at=submitted+5))
        borrowed = source.replace('</div><div><button>Like</button>',
            '</div><a href="https://www.facebook.com/reel/888">Another post</a><div><button>Like</button>')
        tab = self.load_document(reference, borrowed)
        self.assertIsNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
            submitted_at=submitted, notified_at=submitted+5))

    def test_insights_real_action_labels_and_verbose_share_label_are_supported(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact caption')
        for comment_label in ('Comment', 'Leave a comment', 'Bình luận'):
            with self.subTest(comment_label=comment_label):
                source = self.generic_insights_html(snapshot['body'], permalink='')
                source = source.replace('<button>Like</button>', '<button aria-label="Like this post">Like</button>')
                source = source.replace('<button>Comment</button>',
                    '<button aria-label="'+comment_label+'">Comment</button>')
                source = source.replace('<button>Share</button>',
                    '<button aria-label="Send this to friends or post it on your timeline">Share</button>')
                tab = self.load_document(reference, source)
                self.assertIsNotNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
                    submitted_at=submitted, notified_at=submitted+5))

    def test_recognized_insights_preview_sidebar_outside_main_keeps_full_evidence(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact caption')
        source = self.outside_main_insights_html(snapshot['body'])
        for heading in ('Post insights','Thông tin chi tiết bài viết'):
            with self.subTest(heading=heading):
                tab = self.load_document(reference, source.replace('Post insights',heading))
                self.assertIsNotNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
                    submitted_at=submitted, notified_at=submitted+5))
        for document in (source.replace('<h1>Post insights</h1>',''),
                         source+'<h1>Post insights</h1>',
                         source.replace('<h1>Post insights</h1>','<h1>Another page</h1>'),
                         source.replace('</aside>', '</aside><video src="blob:other" width="120" height="120"></video>'),
                         source.replace('id=123','id=999'),
                         source.replace('<aside role="complementary">','<aside role="complementary" aria-label="Comments">')):
            with self.subTest(document=document):
                tab = self.load_document(reference, document)
                self.assertIsNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
                    submitted_at=submitted, notified_at=submitted+5))

    def test_insights_deep_tiny_caption_and_player_groups_do_not_truncate_post_card(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact caption')
        for outside in (False,True):
            with self.subTest(outside=outside):
                source = (self.outside_main_insights_html(snapshot['body']) if outside else
                    self.generic_insights_html(snapshot['body'],permalink=''))
                caption = '<p style="white-space:pre-wrap">'+snapshot['body']+'</p>'
                source = source.replace(caption,'<div role="group">'+'<div>'*14+caption+'</div>'*14+'</div>')
                source = source.replace('<div><video','<div role="group"><video')
                source = source.replace('<div><a href=','<div role="group"><a href=')
                source = source.replace('<div><button>Like</button>','<div role="group"><button>Like</button>')
                tab = self.load_document(reference,source)
                self.assertIsNotNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
                    submitted_at=submitted, notified_at=submitted+5))
                extra = source.replace('</main>', '<div role="group"><video src="blob:recommended" width="120" height="120"></video></div></main>')
                tab = self.load_document(reference,extra)
                self.assertIsNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
                    submitted_at=submitted, notified_at=submitted+5))

    def test_recognized_insights_heading_and_local_preview_inside_navigation_are_supported(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact caption')
        source = self.outside_main_insights_html(snapshot['body']).replace('<h1>Post insights</h1>','')
        source = source.replace('<aside role="complementary">','<div role="navigation"><h1>Post insights</h1>')
        source = source.replace('</aside>','</div>')
        tab = self.load_document(reference, source)
        self.assertIsNotNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
            submitted_at=submitted, notified_at=submitted+5))
        for document in (source.replace('<h1>Post insights</h1>',''), source+'<h1>Post insights</h1>',
                         source.replace('id=123','id=999')):
            with self.subTest(document=document):
                tab = self.load_document(reference,document)
                self.assertIsNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
                    submitted_at=submitted, notified_at=submitted+5))

    def test_actual_media_only_wrapper_can_reach_its_published_header_and_action_card(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact caption')
        source = self.real_nested_insights_html(snapshot['body'])
        tab = self.load_document(reference,source)
        wrapper = tab.locator('#media-only')
        self.assertEqual(wrapper.locator('video').count(),1)
        self.assertEqual(wrapper.locator('a[href*="profile.php"]').count(),0)
        self.assertEqual(wrapper.get_by_role('button',name='Like',exact=True).count(),0)
        self.assertIsNotNone(self.publisher._insights_evidence(tab,reference,self.target,snapshot,
            submitted_at=submitted,notified_at=submitted+5))
        # A complete local unowned post card is a stopping boundary: no owner
        # can be donated by its neighboring header/card at a broader ancestor.
        unowned = source.replace('<a href="https://www.facebook.com/profile.php?id=123">Page Demo</a>','')
        unowned = unowned.replace('</main>',
            '</main><div><a href="https://www.facebook.com/profile.php?id=123">Page Demo</a></div>')
        tab = self.load_document(reference,unowned)
        self.assertIsNone(self.publisher._insights_evidence(tab,reference,self.target,snapshot,
            submitted_at=submitted,notified_at=submitted+5))

    def test_actual_media_only_wrapper_expands_long_caption_inside_same_complete_card(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        body = 'The full approved video caption contains all these words. #MinhChau'
        snapshot = self.video_snapshot(body)
        source = self.real_nested_insights_html(body)
        source = source.replace('<p style="white-space:pre-wrap">'+html.escape(body)+'</p>',
            '<p id="full-caption" style="white-space:pre-wrap">The full approved video caption… '
            '<button onclick="expandInsights()">See more</button></p>')
        source += '<script>function expandInsights(){window.captionExpansions=(window.captionExpansions||0)+1;'
        source += 'document.getElementById("full-caption").innerHTML='+json.dumps(html.escape(body)+'<button>See less</button>')+';}</script>'
        tab = self.load_document(reference,source)
        self.assertIsNotNone(self.publisher._insights_evidence(tab,reference,self.target,snapshot,
            submitted_at=submitted,notified_at=submitted+5))
        self.assertEqual(tab.evaluate('window.captionExpansions'),1)

    def test_insights_failure_diagnostics_follow_real_gates_without_media_source_or_caption(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact private caption sentinel')
        source = self.generic_insights_html(snapshot['body'],permalink='')
        for document,reason in ((source.replace('id=123','id=999'),'author_missing_or_conflicting_owner'),
                                (source.replace('<h1>Post insights</h1>','').replace('<button>Share</button>',''),
                                    'publication_actions_missing'),
                                (self.generic_insights_html(snapshot['body'],metadata=(60,720,1280)),
                                    'video_metadata_or_identity_mismatch')):
            with self.subTest(reason=reason):
                tab = self.load_document(reference,document)
                diagnostic = {}
                self.assertIsNone(self.publisher._insights_evidence(tab,reference,self.target,snapshot,
                    submitted_at=submitted,notified_at=submitted+5,diagnostics=diagnostic))
                self.assertEqual(diagnostic['reason'],'no_verified_panel')
                self.assertEqual(diagnostic['panels'][0]['reason'],reason)
                self.assertTrue(diagnostic['fresh_notice'])
                self.assertFalse(diagnostic['verified'])
                encoded = json.dumps(diagnostic)
                self.assertNotIn(snapshot['body'],encoded)
                self.assertNotIn('blob:',encoded)
                self.assertNotIn('published-video',encoded)
        tab = self.load_document(reference,source)
        diagnostic = {}
        self.assertIsNotNone(self.publisher._insights_evidence(tab,reference,self.target,snapshot,
            submitted_at=submitted,notified_at=submitted+5,diagnostics=diagnostic))
        self.assertEqual(diagnostic['reason'],'verified')
        self.assertTrue(diagnostic['verified'])
        self.assertEqual(diagnostic['panels'][0]['owners'],[dict(
            path='/profile.php',page_id='123',name='Page Demo',local=True)])
        self.assertEqual(diagnostic['panels'][0]['video'],dict(has_src=True,duration=30,width=720,height=1280))
        diagnostic = {}
        self.assertIsNone(self.publisher._insights_evidence(tab,reference,self.target,snapshot,
            submitted_at=submitted,notified_at=submitted-1,diagnostics=diagnostic))
        self.assertEqual(diagnostic['reason'],'notification_not_fresh')
        self.assertEqual(diagnostic['panels'],[])

    def test_insights_failure_diagnostics_bound_owner_observations(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact caption')
        source = self.generic_insights_html(snapshot['body'],permalink='').replace('</main>',
            ''.join('<a href="https://www.facebook.com/'+str(number)+'">Other Page</a>'
                    for number in range(900,940))+'</main>')
        tab = self.load_document(reference,source)
        diagnostic = {}
        self.assertIsNone(self.publisher._insights_evidence(tab,reference,self.target,snapshot,
            submitted_at=submitted,notified_at=submitted+5,diagnostics=diagnostic))
        panel = diagnostic['panels'][0]
        self.assertEqual(panel['reason'],'author_missing_or_conflicting_owner')
        self.assertEqual(len(panel['owners']),12)
        self.assertEqual(panel['owner_count'],41)
        self.assertTrue(panel['conflicting_owner'])

    def test_insights_unowned_card_cannot_borrow_owner_from_sibling_sidebar_or_generic_card(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact caption')
        unowned = ('<div id="unowned-post"><div>Exact caption</div>'
            '<video src="blob:published-video" width="200" height="300"></video>'
            '<div><button>Like</button><button>Comment</button><button>Share</button></div></div>')
        owner = '<a href="https://www.facebook.com/profile.php?id=123">Page Demo</a>'
        for layout in ('<aside role="complementary">'+unowned+'</aside><aside>'+owner+'</aside>',
                       '<aside role="complementary"><section>'+unowned+'<div id="other-card">'+owner+'</div></section></aside>',
                       '<div role="navigation"><section>'+unowned+'<div id="other-card">'+owner+'</div></section></div>'):
            with self.subTest(layout=layout):
                source = ('<meta charset="utf-8"><h1>Post insights</h1><div id="application">'+layout+
                    '<main><h2>Views over time</h2></main></div><script>Object.defineProperties(document.querySelector("video"),'
                    '{duration:{value:30},videoWidth:{value:720},videoHeight:{value:1280}});</script>')
                tab = self.load_document(reference, source)
                self.assertIsNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
                    submitted_at=submitted, notified_at=submitted+5))

    def test_insights_second_offscreen_primary_player_is_ambiguous(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact caption')
        source = self.outside_main_insights_html(snapshot['body']).replace('</main>',
            '<video src="blob:other-primary" style="width:200px;height:300px;position:absolute;top:20000px"></video></main>')
        tab = self.load_document(reference,source)
        self.assertIsNone(self.publisher._insights_evidence(tab, reference, self.target,snapshot,
            submitted_at=submitted,notified_at=submitted+5))

    def test_insights_comment_threads_cannot_supply_post_publication_actions(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact caption')
        source = self.generic_insights_html(snapshot['body'], permalink='')
        for marker in ('aria-label="Comments"', 'aria-label="Bình luận"', 'data-testid="comment-thread"'):
            with self.subTest(marker=marker):
                comments = source.replace('<div><button>Like</button>', '<div '+marker+'><button>Like</button>')
                tab = self.load_document(reference, comments)
                self.assertIsNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
                    submitted_at=submitted, notified_at=submitted+5))

    def test_insights_long_caption_expands_its_own_card_once_and_ignores_comment_expansion(self):
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        body = 'The full approved video caption contains all these words.\n\n#MinhChau'
        snapshot = self.video_snapshot(body)
        source = self.generic_insights_html(body, permalink='')
        prefix = 'The full approved video caption'
        source = source.replace('<p style="white-space:pre-wrap">'+html.escape(body)+'</p>',
            '<p id="full-caption" style="white-space:pre-wrap">'+prefix+'… '
            '<button onclick="expandInsights()">See more</button></p>')
        source = source.replace('</main>', '<div aria-label="Comments"><p>'+prefix+'… '
            '<button onclick="window.commentExpansions=(window.commentExpansions||0)+1">See more</button></p></div></main>')
        source += '<script>function expandInsights(){window.captionExpansions=(window.captionExpansions||0)+1;'
        source += 'document.getElementById("full-caption").innerHTML='+json.dumps(html.escape(body)+'<button>See less</button>')+';}</script>'
        tab = self.load_document(reference, source)
        self.assertEqual(self.publisher._insights_reel_links(tab, reference, self.target, snapshot), [])
        evidence = self.publisher._insights_evidence(tab, reference, self.target, snapshot,
            submitted_at=submitted, notified_at=submitted+5)
        self.assertIsNotNone(evidence)
        self.assertEqual(tab.evaluate('window.captionExpansions'), 1)
        self.assertEqual(tab.evaluate('window.commentExpansions || 0'), 0)
        self.assertEqual(tab.get_by_role('button', name='See more', exact=True).count(), 1)
        self.assertIsNotNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
            submitted_at=submitted, notified_at=submitted+5))
        self.assertEqual(tab.evaluate('window.captionExpansions'), 1)

    def test_insights_explicit_publication_time_is_real_and_not_notification_time(self):
        from datetime import datetime, timezone
        submitted = time.time()-10
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = dict(kind='video', body='Exact caption', composer=dict(video_preview=[
            dict(duration=30, width=720, height=1280)]))
        stamp = datetime.fromtimestamp(submitted+2, timezone.utc).isoformat()
        tab = self.load_document(reference, self.generic_insights_html(snapshot['body'], permalink='', timestamp=stamp))
        evidence = self.publisher._insights_evidence(tab, reference, self.target, snapshot, submitted_at=submitted)
        self.assertEqual(evidence['published_at'], stamp)
        self.assertEqual(evidence['association_mode'], 'visible_time')
        self.assertNotIn('notification_at', evidence)
        for stamp in (datetime.fromtimestamp(submitted-60, timezone.utc).isoformat(),
                      datetime.fromtimestamp(time.time()+60, timezone.utc).isoformat(), 'not-a-date'):
            with self.subTest(stamp=stamp):
                tab = self.load_document(reference, self.generic_insights_html(snapshot['body'], timestamp=stamp))
                self.assertIsNone(self.publisher._insights_evidence(tab, reference, self.target, snapshot,
                    submitted_at=submitted, notified_at=submitted+5))

    def test_insights_does_not_borrow_owner_or_links_from_navigation_or_other_cards(self):
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact caption')
        source = self.insights_html(snapshot['body'], owner='999').replace('</main>',
            '<aside><a href="https://www.facebook.com/profile.php?id=123">Page Demo</a></aside></main>')
        tab = self.load_document(reference, source)
        self.assertEqual(self.publisher._insights_reel_links(tab, reference, self.target, snapshot), [])
        source = self.insights_html(snapshot['body'], links=[]).replace('</main>',
            '<nav><a href="https://www.facebook.com/reel/999">View reel</a></nav></main>')
        tab = self.load_document(reference, source)
        self.assertEqual(self.publisher._insights_reel_links(tab, reference, self.target, snapshot), [])

    def test_reference_identity_must_not_change_and_duplicate_permalink_tracking_is_deduplicated(self):
        reference = _canonical_insights_reference(self.notice_url())
        snapshot = self.video_snapshot('Exact caption')
        tab = self.load_document(reference, self.insights_html(snapshot['body'], links=[
            'https://www.facebook.com/reel/999?old=1', 'https://m.facebook.com/reel/999/?new=2']))
        self.assertEqual(self.publisher._insights_reel_links(tab, reference, self.target, snapshot), ['https://www.facebook.com/reel/999'])
        different = _canonical_insights_reference(self.notice_url(content_id='another-opaque-id'))
        self.assertEqual(self.publisher._insights_reel_links(tab, different, self.target, snapshot), [])

    def test_foreign_primary_player_cannot_be_attributed_to_recommended_target_page(self):
        snapshot = self.video_snapshot('Same exact caption')
        for semantic in (False, True):
            with self.subTest(semantic=semantic):
                tab = self.load_document('https://www.facebook.com/reel/999',
                    self.reel_html(snapshot['body'], owner='999', semantic=semantic, recommendation=True))
                self.assertIsNone(self.publisher._reel_evidence(tab, tab.url, self.target, snapshot))
        tab = self.load_document('https://www.facebook.com/reel/999', self.reel_html(snapshot['body']))
        self.assertIsNotNone(self.publisher._reel_evidence(tab, tab.url, self.target, snapshot))

    def test_recommended_own_player_cannot_label_an_offscreen_foreign_permalink(self):
        snapshot = self.video_snapshot('Same exact caption')
        recommended = self.reel_html(snapshot['body'], semantic=True).split('<main role="main">')[1].rsplit('</main>', 1)[0]
        for semantic in (False, True):
            with self.subTest(semantic=semantic):
                source = self.reel_html('Unrelated foreign caption', owner='999', semantic=semantic)
                source = source.replace('style="width:120px;height:120px"',
                    'style="position:absolute;top:20000px;width:120px;height:120px"')
                source = source.replace('</main>', recommended + '</main>')
                tab = self.load_document('https://www.facebook.com/reel/999', source)
                self.assertIsNone(self.publisher._reel_evidence(tab, tab.url, self.target, snapshot))

    def test_primary_card_ignores_foreign_recommendation_but_two_matching_players_are_ambiguous(self):
        snapshot = self.video_snapshot('Same exact caption')
        source = self.reel_html(snapshot['body'], semantic=True).replace('</main>',
            '<aside><a href="https://www.facebook.com/profile.php?id=999">Other Page</a></aside></main>')
        tab = self.load_document('https://www.facebook.com/reel/999', source)
        self.assertIsNotNone(self.publisher._reel_evidence(tab, tab.url, self.target, snapshot))
        second = self.reel_html(snapshot['body'], semantic=True, source='blob:another').split('<main role="main">')[1].rsplit('</main>', 1)[0]
        tab = self.load_document('https://www.facebook.com/reel/999', source.replace('</main>', second + '</main>'))
        self.assertIsNone(self.publisher._reel_evidence(tab, tab.url, self.target, snapshot))

    def test_actual_reel_url_must_remain_https_without_credentials_or_other_port(self):
        snapshot = self.video_snapshot('Exact caption')
        for actual in ('http://www.facebook.com/reel/999', 'https://user:pass@www.facebook.com/reel/999',
                       'https://www.facebook.com:444/reel/999'):
            with self.subTest(actual=actual):
                tab = self.load_document('https://www.facebook.com/reel/999', self.reel_html(snapshot['body']))
                class FakeUrl:
                    url = actual
                    def __getattr__(self, name):
                        return getattr(tab, name)
                self.assertIsNone(self.publisher._reel_evidence(FakeUrl(), 'https://www.facebook.com/reel/999', self.target, snapshot))

    def test_full_insights_permalink_verifies_real_reel_with_single_publish(self):
        post = self.post_fixture()
        seen = []
        self.route_receipt('**/content/insights/**', self.insights_html(post['body']), seen)
        self.route_receipt('**/reel/999', self.reel_html(post['body']), seen)
        self.prepare(post)
        original = self.composer_tab()
        result = self.publish(post)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['external_url'], 'https://www.facebook.com/reel/999')
        self.assertEqual(result['browser_receipt']['kind'], 'verified_published_reel')
        self.assertIn('/content/insights/', result['browser_receipt']['notification_reference'])
        self.assertEqual((original.evaluate('window.uploads'), original.evaluate('window.posts')), (1, 1))
        with self.assertRaises(ValueError):
            self.publish(post)
        self.assertEqual(original.evaluate('window.posts'), 1)
        self.assertEqual(len(seen), 2)

    def test_full_generic_insights_without_permalink_keeps_precise_reference_and_one_publish(self):
        post = self.post_fixture()
        self.html = self.html.replace('preview.append(selectedVideo);', 'preview.append(selectedVideo);'
            'Object.defineProperties(selectedVideo,{duration:{value:30},videoWidth:{value:720},videoHeight:{value:1280}});')
        seen = []
        self.route_receipt('**/content/insights/**', self.generic_insights_html(post['body'], permalink=''), seen)
        self.prepare(post)
        original = self.composer_tab()
        result = self.publish(post)
        self.assertEqual(result['state'], 'published')
        reference = _canonical_insights_reference(seen[0])
        self.assertEqual(result['external_url'], reference)
        self.assertEqual(result['browser_receipt']['url'], reference)
        self.assertEqual(result['browser_receipt']['verification_url'], reference)
        self.assertEqual(result['browser_receipt']['public_url'], '')
        self.assertEqual(result['browser_receipt']['published_at'], '')
        self.assertEqual(result['browser_receipt']['kind'], 'published_insights')
        self.assertEqual((original.evaluate('window.uploads'), original.evaluate('window.posts')), (1, 1))
        self.assertEqual(len(seen), 1)
        with self.assertRaises(ValueError):
            self.publish(post)
        self.assertEqual(original.evaluate('window.posts'), 1)

    def test_actual_redirect_from_insights_is_observed_then_verified(self):
        post = self.post_fixture()
        self.context.route('**/content/insights/**', lambda route: route.fulfill(content_type='text/html',
            body="<script>location.href='https://www.facebook.com/reel/999';</script>"))
        self.context.route('**/reel/999', lambda route: route.fulfill(content_type='text/html', body=self.reel_html(post['body'])))
        self.prepare(post)
        original = self.composer_tab()
        result = self.publish(post)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['external_url'], 'https://www.facebook.com/reel/999')
        self.assertEqual(original.evaluate('window.posts'), 1)

    def test_reference_or_toast_without_real_permalink_never_guesses_a_reel_url(self):
        post = self.post_fixture()
        seen = []
        self.route_receipt('**/content/insights/**', self.insights_html(post['body'], links=[]), seen)
        self.prepare(post)
        original = self.composer_tab()
        with patch('teodub.facebook_web_post.PUBLISH_RESULT_SECONDS', .5):
            result = self.publish(post)
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual((result['external_url'], result['browser_receipt']), ('', {}))
        self.assertEqual(original.evaluate('window.posts'), 1)
        self.assertEqual(len(seen), 1)
        self.assertNotIn('/reel/', seen[0])

    def test_result_tabs_are_capped_and_cached_without_another_publish_click(self):
        post = self.post_fixture(paths=[f'/reel/{number}' for number in range(100, 116)])
        seen = []
        self.route_receipt('**/reel/*', self.reel_html(post['body'], owner='999'), seen)
        self.prepare(post)
        original = self.composer_tab()
        with patch('teodub.facebook_web_post.PUBLISH_RESULT_SECONDS', 2):
            result = self.publish(post)
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(len(seen), 8)
        self.assertEqual(len(set(seen)), 8)
        self.assertEqual(original.evaluate('window.posts'), 1)

    def test_deadline_and_cancellation_do_not_open_more_receipt_tabs_or_republish(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                post = self.post_fixture(paths=['/reel/999'])
                seen = []
                self.route_receipt('**/reel/999', self.reel_html(post['body']), seen)
                self.prepare(post)
                original = self.composer_tab()
                if cancelled:
                    event = threading.Event()
                    evaluate = original.evaluate
                    def cancel_after_submit(script, *args, **kwargs):
                        result = evaluate(script, *args, **kwargs)
                        if script == RESULT_SCRIPT and evaluate('window.posts || 0') == 1:
                            event.set()
                        return result
                    with patch.object(original, 'evaluate', cancel_after_submit), self.assertRaises(Cancelled):
                        self.publish(post, cancel=event)
                    self.assertEqual(self.saved(post)['state'], 'unknown')
                else:
                    with patch('teodub.facebook_web_post.PUBLISH_RESULT_SECONDS', 0):
                        self.assertEqual(self.publish(post)['state'], 'unknown')
                self.assertEqual(original.evaluate('window.posts'), 1)
                self.assertEqual(seen, [])


if __name__ == '__main__':
    unittest.main()
