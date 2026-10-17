"""Offline regression for the Oct 12 text wizard and post-submit navigation.

No user profile, Facebook requests, real posts or live credentials are used.
"""
import html
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import Error

from tests import test_page_browser_dom as fixtures
from teodub.facebook_web_post import AUTH_SCRIPT, RESULT_SCRIPT
from teodub.page_schedule import PageScheduleController


def text_wizard():
    return '''<!doctype html><meta charset="utf-8">
    <button aria-label="Trang cá nhân của bạn" onclick="profile.hidden=!profile.hidden">Profile</button>
    <div id="profile" role="dialog" aria-label="Trang cá nhân của bạn" hidden>
      <h2>Chọn trang cá nhân</h2><button aria-label="Page Demo, đang chọn">Page Demo</button></div>
    <main><button>Page Demo</button><button onclick="composer.hidden=false">Create post</button></main>
    <section id="composer" role="dialog" aria-label="Tạo bài viết" hidden>
      <h2>Tạo bài viết</h2><span>Page Demo</span>
      <div id="body" role="textbox" contenteditable="true" style="white-space:pre-wrap"></div>
      <button onclick="window.nexts=(window.nexts||0)+1;caption.innerText=document.getElementById('body').innerText;composer.hidden=true;settings.hidden=false">Tiếp</button>
    </section>
    <section id="settings" role="dialog" aria-label="Tạo bài viết" hidden>
      <h2>Cài đặt bài viết</h2><span>Page Demo</span><div id="caption"></div>
      <button onclick="window.posts=(window.posts||0)+1;settings.hidden=true;result.hidden=false">Đăng</button>
    </section>
    <div id="result" role="status" hidden>Your post was published.
      <a href="https://www.facebook.com/123/posts/999">View post</a></div>'''


class PublishNavigationTests(unittest.TestCase):
    setUpClass = fixtures.BrowserDomTests.__dict__['setUpClass']
    tearDownClass = fixtures.BrowserDomTests.__dict__['tearDownClass']
    setUp = fixtures.BrowserDomTests.setUp
    draft = fixtures.BrowserDomTests.draft
    prepare = fixtures.BrowserDomTests.prepare
    publish = fixtures.BrowserDomTests.publish
    saved = fixtures.BrowserDomTests.saved
    composer_tab = fixtures.BrowserDomTests.composer_tab

    def test_text_next_settings_publishes_once_without_user_input(self):
        self.html = text_wizard()
        post = self.draft('Bài chữ nhiều dòng.\n\n#CauCa')
        result = self.prepare(post)
        self.assertEqual(result['browser_review']['composer']['layout'], 'post_settings')
        tab = self.composer_tab()
        self.assertEqual(tab.evaluate('window.nexts'), 1)
        self.assertIsNone(tab.evaluate('window.posts'))
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertEqual(tab.evaluate('window.posts'), 1)
        with self.assertRaises(ValueError):
            self.publish(post)

    def test_text_edited_after_next_rejects_before_post(self):
        self.html = text_wizard().replace('<div id="caption">', '<div id="caption" role="textbox" contenteditable="true">')
        post = self.draft()
        self.prepare(post)
        tab = self.composer_tab()
        tab.locator('#caption').fill('changed after preparation')
        with self.assertRaisesRegex(ValueError, 'Nội dung bài chữ đã đổi'):
            self.publish(post)
        self.assertIsNone(tab.evaluate('window.posts'))

    def test_observed_nested_settings_header_and_readonly_full_preview(self):
        self.html = text_wizard().replace('<h2>Cài đặt bài viết</h2>',
            '<div role="dialog" aria-label="Cài đặt bài viết"><h2>Cài đặt bài viết</h2><button>Quay lại</button></div>')
        post = self.draft('ĐIỂM CÂU MỚI\n\nMột checklist.\n• Câu hỏi?\n\n#CauCa')
        self.prepare(post)
        self.assertEqual(self.publish(post)['state'], 'published')

    def test_changed_readonly_preview_is_rejected(self):
        self.html = text_wizard()
        post = self.draft()
        self.prepare(post)
        tab = self.composer_tab()
        tab.locator('#caption').evaluate('e=>e.innerText="different body"')
        with self.assertRaisesRegex(ValueError, 'Bản xem trước bài chữ'):
            self.publish(post)
        self.assertIsNone(tab.evaluate('window.posts'))

    def test_two_next_buttons_fail_without_clicking(self):
        self.html = text_wizard().replace('>Tiếp</button>', '>Tiếp</button><button>Tiếp</button>')
        post = self.draft()
        with self.assertRaisesRegex(ValueError, 'nút Tiếp.*2 mục'):
            self.prepare(post)
        self.assertIsNone(self.composer_tab().evaluate('window.nexts'))

    def test_changed_page_in_settings_blocks(self):
        self.html = text_wizard().replace('<span>Page Demo</span><div id="caption">',
            '<a href="https://www.facebook.com/profile.php?id=999">Page Demo</a><div id="caption">')
        # Direct settings observation avoids the bounded transition wait here.
        tab = self.context.new_page()
        tab.goto(self.target['url'])
        tab._teodub_page_actor = dict(page_id='123', name='Page Demo')
        tab.locator('#settings').evaluate('e=>e.hidden=false')
        tab.locator('#caption').evaluate('e=>e.innerText="Hello"')
        with self.assertRaisesRegex(ValueError, 'Danh tính Page'):
            self.publisher._observe_post_settings(tab, self.target,
                dict(kind='text', body='Hello', identity={}))

    def run_scheduled(self, post, *, immediate=False):
        owner = SimpleNamespace(studio=SimpleNamespace(data_dir=Path(self.temp.name)), sessions={}, status=Mock())
        controller = PageScheduleController(owner)
        self.addCleanup(controller.close)
        row = controller.store.create_post(self.account['id'], self.target['id'], post['id'], time.time()+60)
        calls = []
        def submit(account, page, post_id, operation, *, request_id):
            calls.append(operation)
            result = getattr(self.publisher, operation)(self.context, account, page, post_id)
            session = owner.sessions.setdefault(account, SimpleNamespace(loading_pages=False, post_actions={}))
            session.post_actions[request_id] = dict(state='done', result=result)
            return request_id
        owner.submit_page_browser = submit
        def thread(*, target, args, daemon):
            return SimpleNamespace(start=lambda: target(*args))
        with patch('teodub.page_schedule.threading.Thread', side_effect=thread):
            if immediate:
                controller.start_now(self.account['id'], self.target['id'], row['id'], expected_schedule=row)
            else:
                controller.start(row['id'])
                controller.tick()
                self.assertEqual(calls, [])  # Future due time does not submit.
                controller.store.update(row['id'], due_at=time.time()-1)
            for _ in range(4):
                controller.last_tick = 0
                controller.tick()
        self.assertEqual(controller.store.get(row['id'])['state'], 'published')
        self.assertEqual(calls, ['prepare', 'publish'])
        self.assertEqual(self.composer_tab().evaluate('window.posts'), 1)
        return controller, row

    def test_clock_driven_text_wizard_runs_same_pipeline_as_post_now(self):
        self.html = text_wizard()
        controller, row = self.run_scheduled(self.draft())
        with controller.store.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM immediate_requests').fetchone()[0], 0)

    def test_post_now_text_wizard_runs_same_pipeline(self):
        self.html = text_wizard()
        self.run_scheduled(self.draft(), immediate=True)

    def test_context_destroyed_after_submit_retries_observation_only(self):
        post = self.draft()
        self.prepare(post)
        tab = self.composer_tab()
        evaluate = tab.evaluate
        interruptions = []
        def observe(script, *args, **kwargs):
            if script == AUTH_SCRIPT and evaluate('window.posts') == 1 and not interruptions:
                interruptions.append(True)
                raise Error('Page.evaluate: Execution context was destroyed, most likely because of a navigation')
            return evaluate(script, *args, **kwargs)
        with patch.object(tab, 'evaluate', side_effect=observe):
            result = self.publish(post)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(interruptions, [True])
        self.assertEqual(evaluate('window.posts'), 1)

    def test_navigation_error_returned_by_final_click_does_not_repeat_click(self):
        from playwright.sync_api import Locator
        post = self.draft()
        self.prepare(post)
        original_click = Locator.click
        def click(locator, **kwargs):
            original_click(locator, **kwargs)
            raise Error('Execution context was destroyed, most likely because of a navigation')
        with patch.object(Locator, 'click', click):
            result = self.publish(post)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(self.composer_tab().evaluate('window.posts'), 1)

    def test_non_navigation_failure_is_not_swallowed(self):
        post = self.draft()
        self.prepare(post)
        tab = self.composer_tab()
        evaluate = tab.evaluate
        def observe(script, *args, **kwargs):
            if script == AUTH_SCRIPT and evaluate('window.posts') == 1:
                raise Error('Page has been closed')
            return evaluate(script, *args, **kwargs)
        with patch.object(tab, 'evaluate', side_effect=observe), self.assertRaisesRegex(Error, 'closed'):
            self.publish(post)
        self.assertEqual(self.saved(post)['state'], 'unknown')
        self.assertEqual(evaluate('window.posts'), 1)

    def test_video_navigation_loses_toast_but_new_notification_verifies_result(self):
        self.html = fixtures.reel_fixture()
        path = Path(self.temp.name)/'video.mp4'
        path.write_bytes(b'fixture video')
        post = self.draft('Video caption\n\n#CauCa', [path])
        after = '''<meta charset="utf-8"><main><h1>Page Demo</h1></main>
        <button aria-label="Thông báo" onclick="notices.hidden=false">Thông báo</button>
        <div id="notices" role="dialog" aria-label="Thông báo" hidden>
        <a id="new" href="#">Đã đăng thước phim</a></div>
        <script>document.getElementById('new').href='https://www.facebook.com/reel/999?notif_t=fb_shorts_video_processed&notif_id='+sessionStorage.getItem('stamp');</script>'''
        self.html += '''<script>publish=()=>{sessionStorage.setItem('posts',String(Number(sessionStorage.getItem('posts')||0)+1));
          sessionStorage.setItem('stamp',String(Date.now()*1000));location.href='/profile.php?id=123&posted=1';}</script>'''
        self.context.route('**/*posted=1', lambda r: r.fulfill(content_type='text/html', body=after))
        receipt = ('<main><a href="https://www.facebook.com/profile.php?id=123">Page Demo</a>'
            '<div style="white-space:pre-wrap">'+html.escape(post['body'])+'</div>'
            '<video src="blob:published" style="width:100px;height:100px"></video></main>'
            '<script>const player=document.querySelector("video");'
            'Object.defineProperty(player,"duration",{value:30});'
            'Object.defineProperty(player,"videoWidth",{value:720});'
            'Object.defineProperty(player,"videoHeight",{value:1280});</script>')
        self.context.route('**/reel/999', lambda r: r.fulfill(content_type='text/html', body=receipt))
        self.prepare(post)
        original = self.composer_tab()
        with patch('teodub.facebook_web_post.PUBLISH_RESULT_SECONDS', 5):
            result = self.publish(post)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['external_url'], 'https://www.facebook.com/reel/999')
        self.assertEqual(original.evaluate("sessionStorage.getItem('posts')"), '1')
        self.assertEqual(original.evaluate(RESULT_SCRIPT)['statuses'], [])


if __name__ == '__main__':
    unittest.main()
