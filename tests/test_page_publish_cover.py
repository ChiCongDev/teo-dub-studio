"""22/11/2026: a Facebook notice bar over the final "Đăng" button is waited for (nothing is clicked meanwhile); still there after the
wait, Tèo stops before clicking and says so in Vietnamese. Playwright's English call log is shown as one short line."""
from pathlib import Path
import threading
from unittest.mock import patch
import unittest

from teodub import facebook_web_post
from teodub.chrome_errors import brief
from teodub.facebook_post_tasks import BrowserPostTasks
from tests import test_page_browser_dom as fixture

BAR = ("const bar=document.createElement('div');bar.id='bar';bar.textContent='Thông báo mới: có người bình luận';"
       "bar.style.cssText='position:fixed;inset:0;z-index:99;background:rgba(0,0,0,.05)';document.body.append(bar);")


class CoveredPublishTests(unittest.TestCase):
    setUpClass = fixture.BrowserDomTests.__dict__['setUpClass']
    tearDownClass = fixture.BrowserDomTests.__dict__['tearDownClass']
    setUp = fixture.BrowserDomTests.setUp
    draft = fixture.BrowserDomTests.draft
    prepare = fixture.BrowserDomTests.prepare
    publish = fixture.BrowserDomTests.publish
    saved = fixture.BrowserDomTests.saved
    composer_tab = fixture.BrowserDomTests.composer_tab
    reel_draft = fixture.BrowserDomTests.reel_draft

    def ready(self):
        post = self.reel_draft()
        self.prepare(post)
        return post, self.composer_tab()

    def events(self, post):
        return self.publisher.store.events(self.account['id'], self.target['id'], post['id'])

    def test_a_bar_that_goes_away_is_waited_for_then_posted_once(self):
        post, tab = self.ready()
        tab.evaluate(BAR+"setTimeout(()=>bar.remove(),1500);")
        progress = []
        self.assertEqual(self.publish(post, progress=progress.append)['state'], 'published')
        self.assertEqual(tab.evaluate('window.posts'), 1)
        self.assertTrue(any('đang bị một ô của Facebook che' in line for line in progress))

    def test_a_bar_that_stays_stops_before_clicking_and_says_so(self):
        post, tab = self.ready()
        tab.evaluate(BAR)
        with patch.object(facebook_web_post, 'PUBLISH_COVER_WAIT_SECONDS', 1):
            with self.assertRaisesRegex(ValueError, 'Chưa bấm Đăng') as caught:
                self.publish(post)
        self.assertIsNone(tab.evaluate('window.posts'))  # Nothing was clicked …
        self.assertNotIn('submitting', [event['state'] for event in self.events(post)])  # … not even tried.
        self.assertIn('Thông báo mới: có người bình luận', str(caught.exception))
        self.assertIn('CHƯA gửi', str(caught.exception))
        self.assertNotIn('Call log', str(caught.exception))
        shots = list((Path(self.temp.name)/'page-publishing'/'post-screenshots').glob('*.png'))
        self.assertEqual(len(shots), 1)
        self.assertIn(shots[0].name, str(caught.exception))

    def test_a_bar_elsewhere_does_not_delay_posting(self):
        post, tab = self.ready()
        tab.evaluate("const bar=document.createElement('div');bar.textContent='x';"
                     "bar.style.cssText='position:fixed;right:0;top:0;width:10px;height:10px;z-index:99';document.body.append(bar);")
        progress = []
        self.assertEqual(self.publish(post, progress=progress.append)['state'], 'published')
        self.assertEqual(tab.evaluate('window.posts'), 1)
        self.assertFalse(any('đang bị một ô' in line for line in progress))

    def test_only_the_last_screenshots_are_kept(self):
        post, tab = self.ready()
        folder = Path(self.temp.name)/'page-publishing'/'post-screenshots'
        folder.mkdir(parents=True)
        for number in range(facebook_web_post.POST_SCREENSHOTS_KEPT+5):
            (folder/('20260101-0000%02d-old.png' % number)).write_bytes(b'x')
        self.assertTrue(self.publisher._save_screenshot(tab, post['id']))
        self.assertEqual(len(list(folder.glob('*.png'))), facebook_web_post.POST_SCREENSHOTS_KEPT)


class BriefTests(unittest.TestCase):
    COVERED = ('Locator.click: Timeout 5000ms exceeded.\nCall log:\n  - waiting for get_by_role("button")\n'
               '    - locator resolved to <div role="button">…</div>\n  - attempting click action\n'
               '    2 × waiting for element to be visible, enabled and stable\n'
               '      - <div class="x">…</div> from <div data-visualcompletion="ignore">…</div> subtree intercepts pointer events\n'
               '    - retrying click action\n      - waiting 500ms')

    def test_a_covered_button_reads_as_one_vietnamese_line(self):
        text = brief(self.COVERED)
        self.assertIn('bị một ô/thanh thông báo của Facebook che', text)
        self.assertNotIn('Call log', text)
        self.assertNotIn('\n', text)

    def test_a_timeout_keeps_teos_own_words_after_the_log(self):
        text = brief('Locator.click: Timeout 5000ms exceeded.\nCall log:\n  - waiting for x\n    - performing click action\n'
                     ' Chưa xác minh kết quả; không gửi lại.')
        self.assertEqual(text, 'Chrome chờ 5 giây mà nút/ô trên Facebook chưa sẵn sàng. Chưa xác minh kết quả; không gửi lại.')

    def test_other_messages_are_unchanged(self):
        for text in ('Nút Đăng không còn sẵn sàng; giữ Chrome để kiểm tra.', '', 'Line one\nline two'):
            self.assertEqual(brief(text), text)

    def test_a_failed_post_step_reaches_the_screen_short(self):
        def steps():
            raise RuntimeError(self.COVERED)
            yield
        events = []
        tasks = BrowserPostTasks()
        tasks.add('r1', steps(), scope=('a', 'p', 'x'), cancel=threading.Event(),
                  event=lambda kind, message, **extra: events.append((kind, message)))
        tasks.step()
        self.assertEqual(events[0][0], 'post_error')
        self.assertNotIn('Call log', events[0][1])
        self.assertIn('che', events[0][1])


if __name__ == '__main__':
    unittest.main()
