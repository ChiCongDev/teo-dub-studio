"""Real Playwright DOM fixtures in an isolated offline browser, never a user profile."""
import html
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from teodub.facebook_accounts import FacebookAccounts
from teodub.facebook_web_post import BrowserPagePublisher, COMPOSER_SCRIPT, RESULT_SCRIPT


def fixture(*, author='Page Demo', author_id='123', extra_post='', publish='success', login=False):
    if login:
        return '<main><h1>Page Demo</h1><input type="password"></main>'
    receipt = """const s=document.createElement('div');s.setAttribute('role','status');
      s.innerHTML='Your post was published. <a href="https://www.facebook.com/123/posts/999">View post</a>';
      document.body.append(s); document.getElementById('composer').hidden=true;""" if publish == 'success' else ''
    return f'''<!doctype html><meta charset="utf-8"><main role="main">
      <h1>Page Demo</h1><button onclick="document.getElementById('composer').hidden=false">Create post</button>
      <article role="article">old feed <a href="https://www.facebook.com/123/posts/111">old</a></article>
      </main><button onclick="window.outside=(window.outside||0)+1">Post</button>
      <section role="dialog" id="composer" aria-label="Create post" hidden>
      <h2>Create post</h2><a href="https://www.facebook.com/profile.php?id={author_id}">{author}</a>
      <div contenteditable="true" role="textbox" aria-label="Post body"></div>
      <input type="file" accept="image/*,video/*" multiple>
      <div id="previews"></div><button onclick="window.posts=(window.posts||0)+1;{html.escape(receipt, quote=True)}">Post</button>{extra_post}
      </section><script>
      document.querySelector('input[type=file]').onchange=e=>{{
        const host=document.getElementById('previews');host.innerHTML='';
        for(const f of e.target.files) {{ const b=document.createElement('button');
          b.textContent=f.type.startsWith('video')?'Remove video':'Remove photo';host.append(b);
          if(f.type.startsWith('video')){{const v=document.createElement('video');v.src=URL.createObjectURL(f);
            v.width=100;v.height=100;Object.defineProperties(v,{{duration:{{value:30}},videoWidth:{{value:720}},videoHeight:{{value:1280}}}});host.append(v);}} }}
      }};
      </script>'''


def reel_fixture():
    """Observed Create post -> Edit reel -> Reel settings, fully offline."""
    return '''<!doctype html><meta charset="utf-8">
    <button aria-label="Trang cá nhân của bạn" onclick="profile.hidden=!profile.hidden">Profile</button>
    <div id="profile" role="dialog" aria-label="Trang cá nhân của bạn" hidden>
      <h2>Chọn trang cá nhân</h2><button aria-label="Page Demo, đang chọn">Page Demo</button></div>
    <main><button>Page Demo</button>
      <button onclick="composer.hidden=false">Create post</button>
      <section><h2>Page Demo</h2>old feed author</section>
      <section><h2>Page Demo</h2>another old feed author</section>
    </main>
    <section id="composer" role="dialog" aria-label="Tạo bài viết" hidden>
      <div role="dialog" aria-label="Tạo bài viết"><h2>Tạo bài viết</h2></div>
      <span>Page Demo</span><div id="body" role="textbox" contenteditable="true" style="white-space:pre-wrap"></div>
      <input type="file" accept="video/*"><div id="preview"></div>
      <button onclick="toEdit()">Tiếp</button></section>
    <section id="edit" role="dialog" aria-label="Tạo bài viết" hidden>
      <h2>Chỉnh sửa thước phim</h2><input aria-label="Thêm thẻ"><div id="editPreview"></div>
      <button onclick="toSettings()">Tiếp</button></section>
    <section id="settings" role="dialog" aria-label="Tạo bài viết" hidden>
      <h2>Cài đặt thước phim</h2><div id="caption" role="textbox" contenteditable="true" style="white-space:pre-wrap" aria-label="Mô tả thước phim của bạn..."></div>
      <div id="finalPreview"></div><button onclick="publish()">Đăng</button></section>
    <script>
      let selectedVideo;
      document.querySelector('input[type=file]').onchange=e=>{
        window.uploads=(window.uploads||0)+1;preview.innerHTML='';
        const done=document.createElement('img');done.alt='Đã tải lên xong';done.width=16;done.height=16;preview.append(done);
        const remove=document.createElement('button');remove.setAttribute('aria-label','Gỡ file đính kèm trong bài viết');preview.append(remove);
        selectedVideo=document.createElement('video');selectedVideo.width=100;selectedVideo.height=100;
        Object.defineProperties(selectedVideo,{duration:{value:30,configurable:true},videoWidth:{value:720,configurable:true},videoHeight:{value:1280,configurable:true}});
        selectedVideo.src=URL.createObjectURL(e.target.files[0]);preview.append(selectedVideo);
      };
      function toEdit(){window.nexts=(window.nexts||0)+1;composer.hidden=true;edit.hidden=false;editPreview.append(selectedVideo);}
      function toSettings(){window.nexts=(window.nexts||0)+1;edit.hidden=true;settings.hidden=false;
        caption.innerText=body.innerText;finalPreview.append(selectedVideo);}
      function publish(){window.posts=(window.posts||0)+1;settings.hidden=true;
        const done=document.createElement('div');done.setAttribute('role','status');
        done.innerHTML='Your post was published. <a href="https://www.facebook.com/123/videos/999">View video</a>';document.body.append(done);}
    </script>'''


class BrowserDomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = sync_playwright().start()
        # A fresh ephemeral test browser. No launch_persistent_context/profile access.
        cls.browser = cls.runtime.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = FacebookAccounts(self.temp.name)
        self.account = self.registry.add_account('Offline account')
        self.target = self.registry.save_page(self.account['id'], 'Page Demo', 'https://www.facebook.com/profile.php?id=123', '123')
        self.publisher = BrowserPagePublisher(self.temp.name)
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.html = fixture()
        self.requests = []
        def route(request_route):
            self.requests.append(request_route.request.url)
            if re.fullmatch(r'/123/(?:posts|videos)/[0-9]+/?', urlsplit(request_route.request.url).path):
                # A public permalink is a separate player page, never the old
                # composer or a success toast. Only this disposable fixture's
                # single processing post supplies its full submitted caption.
                pending = [post for post in self.publisher.store.posts(self.account['id'], self.target['id'])
                           if post['state'] == 'processing']
                if len(pending) == 1:
                    result = ('<meta charset="utf-8"><main><a href="https://www.facebook.com/profile.php?id=123">Page Demo</a>'
                        '<div style="white-space:pre-wrap">' + html.escape(pending[0]['body']) + '</div>'
                        '<video src="blob:published" width="120" height="120"></video></main>'
                        '<script>Object.defineProperties(document.querySelector("video"),'
                        '{duration:{value:30},videoWidth:{value:720},videoHeight:{value:1280}});</script>')
                    request_route.fulfill(status=200, content_type='text/html', body=result)
                    return
            request_route.fulfill(status=200, content_type='text/html', body=self.html)
        self.context.route('**/*', route)
        self.unrelated = self.context.new_page()
        self.unrelated.set_content('<textarea id="unfinished">keep this form</textarea>')
        self.progress = []

    def draft(self, body='Hello world', paths=(), finish='tool'):
        return self.publisher.store.save_draft(self.account['id'], self.target['id'], body, paths,
            delivery_mode='browser', browser_finish_mode=finish)

    def prepare(self, post):
        return self.publisher.prepare(self.context, self.account['id'], self.target['id'], post['id'], progress=self.progress.append)

    def publish(self, post, **kwargs):
        return self.publisher.publish(self.context, self.account['id'], self.target['id'], post['id'], **kwargs)

    def saved(self, post):
        return self.publisher.store.post(self.account['id'], self.target['id'], post['id'])

    def composer_tab(self):
        # Publication verification opens independent result tabs; return the
        # actual disposable composer rather than whichever tab opened last.
        for tab in reversed(self.context.pages):
            if not tab.is_closed() and tab.locator('input[type="file"]').count():
                return tab
        return self.context.pages[-1]

    def test_text_prepare_freezes_actual_content_identity_and_preserves_other_form(self):
        post = self.draft()
        result = self.prepare(post)
        self.assertEqual(result['state'], 'review')
        preview = self.publisher.review(self.account['id'], self.target['id'], post['id'])
        self.assertEqual(preview['identity'], dict(page_id='123', name='Page Demo', url=self.target['url']))
        self.assertEqual(preview['body'], 'Hello world')
        self.assertEqual(preview['account_name'], 'Offline account')
        self.assertEqual(self.unrelated.locator('textarea').input_value(), 'keep this form')
        self.assertIsNone(self.composer_tab().evaluate('window.posts'))
        self.assertIsNone(self.composer_tab().evaluate('window.outside'))
        preview['body'] = 'mutated copy'
        self.assertEqual(self.publisher.review(self.account['id'], self.target['id'], post['id'])['body'], 'Hello world')
        before = len(self.context.pages)
        self.prepare(post)  # Idempotent focus only, no new tab or text fill.
        self.assertEqual(len(self.context.pages), before)

    def test_publish_clicks_exactly_once_only_inside_dialog_and_requires_new_link_evidence(self):
        post = self.draft()
        self.prepare(post)
        result = self.publish(post)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['external_url'], 'https://www.facebook.com/123/posts/999')
        self.assertEqual(result['browser_receipt']['kind'], 'success_status')
        self.assertEqual(self.composer_tab().evaluate('window.posts'), 1)
        self.assertIsNone(self.composer_tab().evaluate('window.outside'))
        with self.assertRaises(ValueError):
            self.publish(post)
        self.assertEqual(self.composer_tab().evaluate('window.posts'), 1)

    def test_extra_main_does_not_prevent_posting_or_click_its_other_composer(self):
        self.html += '<main role="main"><h2>Another area</h2><button onclick="window.wrong=(window.wrong||0)+1">Create post</button></main>'
        post = self.draft()
        prepared = self.prepare(post)
        self.assertEqual(prepared['state'], 'review')
        self.assertEqual(self.composer_tab().get_by_role('main').count(), 2)
        self.assertIsNone(self.composer_tab().evaluate('window.wrong'))
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertEqual(self.composer_tab().evaluate('window.posts'), 1)

    def test_nested_header_dialog_resolves_full_editable_composer(self):
        self.html = self.html.replace('<h2>Create post</h2>',
            '<div role="dialog" aria-label="Create post"><h2>Create post</h2></div>')
        path = Path(self.temp.name)/'fixture.mp4'
        path.write_bytes(b'Offline video fixture')
        post = self.draft(paths=[str(path)])
        self.assertEqual(self.prepare(post)['state'], 'review')
        self.assertEqual(self.composer_tab().get_by_role('dialog', name='Create post').count(), 2)
        self.assertEqual(Path(self.saved(post)['browser_review']['media'][0]['path']).name, 'fixture.mp4')
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertEqual(self.composer_tab().evaluate('window.posts'), 1)

    def test_nested_full_composer_wrappers_resolve_one_editor(self):
        self.html = self.html.replace('<section role="dialog" id="composer"',
            '<div role="dialog" aria-label="Create post"><section role="dialog" id="composer"')
        self.html = self.html.replace('</section><script>', '</section></div><script>')
        post = self.draft()
        self.assertEqual(self.prepare(post)['state'], 'review')
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertEqual(self.composer_tab().evaluate('window.posts'), 1)

    def test_two_separate_editable_composers_still_refused(self):
        tab = self.context.new_page()
        tab.set_content('<section role="dialog" aria-label="Create post"><h2>Create post</h2>'
            '<div role="textbox" contenteditable="true"></div></section>'*2)
        with self.assertRaisesRegex(ValueError, 'hộp Tạo bài viết.*2 mục'):
            self.publisher._dialog(tab)

    def test_recovered_schedule_prepares_and_publishes_once_with_two_mains(self):
        import time
        from types import SimpleNamespace
        from unittest.mock import Mock
        from teodub.page_schedule import PageScheduleController
        post = self.draft()
        owner = SimpleNamespace(studio=SimpleNamespace(data_dir=Path(self.temp.name)), sessions={}, status=Mock())
        controller = PageScheduleController(owner)
        self.addCleanup(controller.close)
        row = controller.store.create_post(self.account['id'], self.target['id'], post['id'], time.time()+3600)
        failure = 'Không xác định duy nhất vùng Page (2 mục). Chrome giữ nguyên để kiểm tra; chưa bấm Đăng.'
        self.publisher.store.transition(self.account['id'], self.target['id'], post['id'], 'failed', failure)
        row = controller.store.update(row['id'], state='needs_attention', phase='prepare',
            attempted_at='old pre-input attempt', message=failure)
        self.html += '<main role="main"><h2>Other content area</h2></main>'
        calls = []
        def submit(account, page, post_id, operation, *, request_id):
            calls.append(operation)
            result = getattr(self.publisher, operation)(self.context, account, page, post_id)
            session = owner.sessions.setdefault(account, SimpleNamespace(loading_pages=False, post_actions={}))
            session.post_actions[request_id] = dict(state='done', result=result)
            return request_id
        owner.submit_page_browser = submit
        controller.retry_preparation(self.account['id'], self.target['id'], row['id'], time.time()+600)
        controller.start(row['id'])
        controller.store.update(row['id'], due_at=time.time()-1)
        # Synchronous fixture validation, real composer and scheduler logic.
        def thread(*, target, args, daemon):
            return SimpleNamespace(start=lambda: target(*args))
        with patch('teodub.page_schedule.threading.Thread', side_effect=thread):
            for _ in range(3):
                controller.last_tick = 0
                controller.tick()
        self.assertEqual(controller.store.get(row['id'])['state'], 'published')
        self.assertEqual(calls, ['prepare', 'publish'])
        self.assertEqual(self.composer_tab().evaluate('window.posts'), 1)
        controller.last_tick = 0
        controller.tick()
        self.assertEqual(calls, ['prepare', 'publish'])
        with self.assertRaises(ValueError):
            controller.retry_preparation(self.account['id'], self.target['id'], row['id'], time.time()+600)

    def test_nested_mains_select_same_heading_once(self):
        self.html = self.html.replace('<main role="main">', '<main role="main"><main role="main">', 1)
        self.html = self.html.replace('</main>', '</main></main>', 1)
        post = self.draft()
        self.assertEqual(self.prepare(post)['state'], 'review')
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertEqual(self.composer_tab().evaluate('window.posts'), 1)

    def test_nested_header_uses_parent_main_composer_and_excludes_article_controls(self):
        self.html = self.html.replace('<h1>Page Demo</h1>', '<main role="main"><h1>Page Demo</h1></main>')
        self.html = self.html.replace('old feed', 'old feed <button onclick="window.wrong=1">Create post</button>')
        post = self.draft()
        self.assertEqual(self.prepare(post)['state'], 'review')
        self.assertIsNone(self.composer_tab().evaluate('window.wrong'))

    def test_page_name_saved_in_other_letter_case_than_facebook_shows_is_still_found(self):
        self.html = self.html.replace('<h1>Page Demo</h1>', '<h1>PAGE demo</h1>')
        post = self.draft()
        self.assertEqual(self.prepare(post)['state'], 'review')
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertEqual(self.composer_tab().evaluate('window.posts'), 1)

    def test_a_different_page_name_is_still_refused_whatever_the_letter_case(self):
        from teodub.facebook_web_post import _page_main
        self.html = self.html.replace('<h1>Page Demo</h1>', '<h1>Page Demo Two</h1>')
        tab = self.context.new_page()
        tab.goto(self.target['url'])
        with self.assertRaisesRegex(ValueError, 'tên Page'):
            _page_main(tab, self.target)

    def test_hidden_duplicate_heading_is_not_another_page(self):
        self.html += '<main role="main" hidden><h1>Page Demo</h1><button>Create post</button></main>'
        self.assertEqual(self.prepare(self.draft())['state'], 'review')

    def test_two_page_headings_still_block_before_editing_or_posting(self):
        from teodub.facebook_web_post import _page_main
        self.html += '<main role="main"><h1>Page Demo</h1><button onclick="window.wrong=1">Create post</button></main>'
        tab = self.context.new_page()
        tab.goto(self.target['url'])
        with self.assertRaisesRegex(ValueError, 'tên Page'):
            _page_main(tab, self.target)
        self.assertTrue(tab.get_by_role('dialog', name='Create post').is_hidden())
        self.assertIsNone(tab.evaluate('window.wrong'))

    def test_two_composers_in_page_main_still_block_without_guessing(self):
        self.html = self.html.replace('<article role="article">', '<button onclick="window.wrong=1">Create post</button><article role="article">')
        post = self.draft()
        with self.assertRaisesRegex(ValueError, 'nút Tạo bài viết.*2 mục'):
            self.prepare(post)
        self.assertEqual(self.saved(post)['state'], 'failed')
        self.assertEqual(self.composer_tab().locator('[role=textbox]').inner_text(), '')
        self.assertIsNone(self.composer_tab().evaluate('window.wrong'))
        self.assertIsNone(self.composer_tab().evaluate('window.posts'))
        self.assertIsNone(self.composer_tab().evaluate('window.outside'))
        with self.assertRaises(ValueError):
            self.publish(post)
        self.assertIsNone(self.composer_tab().evaluate('window.posts'))

    def test_manual_default_refuses_tool_publish_and_user_url_does_not_verify(self):
        post = self.draft(finish='manual')
        self.prepare(post)
        with self.assertRaisesRegex(ValueError, 'tự bấm'):
            self.publish(post)
        recorded = self.publisher.record_manual_result(self.account['id'], self.target['id'], post['id'],
                                                     'https://www.facebook.com/123/posts/888?tracking=x')
        self.assertEqual(recorded['state'], 'review')
        self.assertEqual(recorded['external_url'], '')
        self.assertEqual(recorded['manual_url'], 'https://www.facebook.com/123/posts/888')
        self.assertIsNone(self.composer_tab().evaluate('window.posts'))
        with self.assertRaisesRegex(ValueError, 'link Page'):
            self.publisher.record_manual_result(self.account['id'], self.target['id'], post['id'], self.target['url'])

    def test_images_and_single_video_file_metadata_and_visible_previews(self):
        for paths in ([Path(self.temp.name)/'a.png', Path(self.temp.name)/'b.jpg'], [Path(self.temp.name)/'one.mp4']):
            for index, path in enumerate(paths):
                path.write_bytes(b'offline fixture media '+str(index).encode())
            post = self.draft(paths=paths)
            result = self.prepare(post)
            preview = result['browser_review']
            self.assertEqual(preview['composer']['preview_count'], len(paths))
            self.assertEqual([x['name'] for x in preview['observed_files']], [x.name for x in paths])
            self.assertTrue(all(x['sha256'] for x in preview['media']))
            self.assertIsNone(self.composer_tab().evaluate('window.posts'))

    def reel_draft(self):
        self.html = reel_fixture()
        path = Path(self.temp.name)/'reel.mp4'
        path.write_bytes(b'Local fixture video only')
        return self.draft(body='Đã duyệt lời #CauCa', paths=[path])

    def test_observed_reel_wizard_preserves_upload_caption_actor_and_publishes_once(self):
        post = self.reel_draft()
        prepared = self.prepare(post)
        tab = self.composer_tab()
        self.assertEqual(prepared['state'], 'review')
        self.assertEqual(prepared['browser_review']['composer']['layout'], 'reel_settings')
        self.assertEqual(prepared['browser_review']['body'], post['body'])
        self.assertEqual(tab.evaluate('window.uploads'), 1)
        self.assertEqual(tab.evaluate('window.nexts'), 2)
        self.assertIsNone(tab.evaluate('window.posts'))
        self.assertEqual(len(prepared['browser_review']['composer']['video_preview']), 1)
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertEqual(tab.evaluate('window.posts'), 1)
        with self.assertRaises(ValueError):
            self.publish(post)
        self.assertEqual(tab.evaluate('window.uploads'), 1)

    def test_changed_reel_caption_or_preview_cannot_be_published_or_uploaded_again(self):
        for js in ("caption.innerText='different'", "document.querySelector('#settings video').src='blob:changed'"):
            with self.subTest(js=js):
                post = self.reel_draft()
                self.prepare(post)
                tab = self.composer_tab()
                tab.evaluate(js)
                with self.assertRaises(ValueError):
                    self.publish(post)
                self.assertEqual(self.saved(post)['state'], 'unknown')
                self.assertIsNone(tab.evaluate('window.posts'))
                self.assertEqual(tab.evaluate('window.uploads'), 1)
                with self.assertRaises(ValueError):
                    self.prepare(post)

    def test_reel_upload_complete_marker_is_required_before_next(self):
        post = self.reel_draft()
        self.html = self.html.replace("done.alt='Đã tải lên xong'", "done.alt='Đang tải'")
        with self.assertRaisesRegex(ValueError, 'tải xong'):
            self.prepare(post)
        self.assertEqual(self.saved(post)['state'], 'unknown')
        self.assertEqual(self.composer_tab().evaluate('window.uploads'), 1)
        self.assertIsNone(self.composer_tab().evaluate('window.nexts'))
        self.assertIsNone(self.composer_tab().evaluate('window.posts'))

    def test_duplicate_reel_next_buttons_block_without_guessing(self):
        post = self.reel_draft()
        self.html = self.html.replace('<button onclick="toEdit()">Tiếp</button>', '<button onclick="toEdit()">Tiếp</button><button>Tiếp</button>')
        with self.assertRaisesRegex(ValueError, 'nút Tiếp.*2 mục'):
            self.prepare(post)
        self.assertIsNone(self.composer_tab().evaluate('window.nexts'))
        self.assertIsNone(self.composer_tab().evaluate('window.posts'))

    def test_changed_reel_actor_binding_blocks_final_publish(self):
        post = self.reel_draft()
        self.prepare(post)
        tab = self.composer_tab()
        tab._teodub_page_actor['page_id'] = '456'
        with self.assertRaisesRegex(ValueError, 'danh tính Page'):
            self.publish(post)
        self.assertIsNone(tab.evaluate('window.posts'))
        self.assertEqual(self.saved(post)['state'], 'unknown')

    def test_vietnamese_uppercase_post_is_observed_as_final_button(self):
        self.html = fixture().replace('>Post</button>', '>Đăng</button>')
        post = self.draft()
        prepared = self.prepare(post)
        data = self.publisher._dialog(self.composer_tab()).evaluate(COMPOSER_SCRIPT)
        self.assertEqual(data['publish'], 1)
        self.assertNotIn('layout', prepared['browser_review']['composer'])
        self.assertEqual(self.publish(post)['state'], 'published')

    def test_duplicate_profile_header_buttons_are_not_guessed(self):
        from teodub.facebook_web_post import _page_main
        self.html = reel_fixture().replace('<main><button>Page Demo</button>', '<main><button>Page Demo</button><button>Page Demo</button>')
        tab = self.context.new_page()
        tab.goto(self.target['url'])
        with self.assertRaisesRegex(ValueError, 'đầu trang'):
            _page_main(tab, self.target)
        self.assertTrue(tab.locator('#composer').is_hidden())

    def test_changed_body_identity_files_or_duplicate_final_controls_block_publish(self):
        modifications = [
            "document.querySelector('[role=textbox]').innerText='edited';",
            "document.querySelector('[role=dialog] a').href='https://www.facebook.com/profile.php?id=456';",
            "const b=document.createElement('button');b.innerText='Post';document.querySelector('[role=dialog]').append(b);",
        ]
        for js in modifications:
            post = self.draft()
            self.prepare(post)
            self.composer_tab().evaluate(js)
            with self.assertRaises(ValueError):
                self.publish(post)
            self.assertEqual(self.saved(post)['state'], 'unknown')
            self.assertIsNone(self.composer_tab().evaluate('window.posts'))

    def test_restart_or_closed_tab_cannot_recreate_review_or_send(self):
        for drop_binding in (False, True):
            post = self.draft()
            self.prepare(post)
            before = len(self.context.pages)
            if drop_binding:
                self.context._teodub_page_post_tabs = {}
            else:
                self.composer_tab().close()
                before -= 1
            with self.assertRaisesRegex(ValueError, 'khởi động lại'):
                self.prepare(post)
            self.assertEqual(self.saved(post)['state'], 'unknown')
            self.assertEqual(len(self.context.pages), before)

    def test_no_success_from_click_or_disappearing_dialog_or_existing_permalink(self):
        self.html = fixture(publish='silent')
        post = self.draft()
        self.prepare(post)
        tick = iter([0, 1, 1000])  # Cross the bounded result wait, including navigation grace.
        from types import SimpleNamespace
        with patch('teodub.facebook_web_post.time', SimpleNamespace(monotonic=lambda: next(tick))):
            result = self.publish(post)
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(self.composer_tab().evaluate('window.posts'), 1)
        with self.assertRaises(ValueError):
            self.publish(post)
        self.assertEqual(self.composer_tab().evaluate('window.posts'), 1)

    def test_upload_interrupted_persists_unknown_and_does_not_auto_upload_again(self):
        path = Path(self.temp.name)/'a.png'
        path.write_bytes(b'fixture')
        post = self.draft(paths=[path])
        original_check = self.publisher._check_files
        def interrupted(data, files):
            if data['files']:
                raise RuntimeError('fixture interruption after file selection')
            return original_check(data, files)
        with patch.object(self.publisher, '_check_files', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'interruption'):
                self.prepare(post)
        self.assertEqual(self.saved(post)['state'], 'unknown')
        tab_count = len(self.context.pages)
        with self.assertRaises(ValueError):
            self.prepare(post)
        self.assertEqual(len(self.context.pages), tab_count)

    def test_login_and_wrong_page_identity_stop_before_editor_changes(self):
        self.html = fixture(login=True)
        post = self.draft()
        with self.assertRaisesRegex(ValueError, 'đăng nhập'):
            self.prepare(post)
        self.assertEqual(self.saved(post)['state'], 'failed')
        self.assertFalse(self.composer_tab().locator('[role=dialog]').count())
        self.html = fixture(author_id='456')
        post = self.draft()
        # Avoid waiting through the whole compositor-render grace for a known wrong ID.
        with patch.object(self.publisher, '_wait_composer', side_effect=lambda tab, target, **_: self.publisher._observe(tab, target)):
            with self.assertRaisesRegex(ValueError, 'Page ID khác'):
                self.prepare(post)
        self.assertEqual(self.composer_tab().locator('[role=textbox]').inner_text(), '')

    def test_success_receipt_rejects_page_link_wrong_owner_and_preexisting_status(self):
        post = self.draft()
        snapshot = dict(kind='text', body='Hello world')
        evidence = self.publisher._publication_evidence
        baseline = dict(statuses=[], articles=[])
        for url in (self.target['url'], 'https://www.facebook.com/456/posts/999', 'https://evil.example/123/posts/999'):
            observed = dict(statuses=[dict(text='Your post was published.', links=[dict(url=url,name='View')])], articles=[])
            self.assertIsNone(evidence(observed, baseline, self.target, snapshot))
        good = dict(statuses=[dict(text='Your post was published.', links=[dict(url='https://www.facebook.com/123/posts/999',name='View')])], articles=[])
        self.assertIsNone(evidence(good, good, self.target, snapshot))
        self.assertIsNotNone(evidence(good, baseline, self.target, snapshot))

    def test_switch_now_only_on_verified_numeric_page_then_rechecks_composer_author(self):
        original = '<button onclick="document.getElementById(\'composer\').hidden=false">Create post</button>'
        switch = '<button onclick="window.switches=(window.switches||0)+1;this.innerText=\'Create post\';this.onclick=()=>document.getElementById(\'composer\').hidden=false">Switch now</button>'
        self.html = fixture().replace(original, switch)
        post = self.draft()
        self.prepare(post)
        self.assertEqual(self.composer_tab().evaluate('window.switches'), 1)
        self.assertIsNone(self.composer_tab().evaluate('window.posts'))
        self.assertEqual(self.saved(post)['browser_review']['identity']['page_id'], '123')

    def test_switch_menu_or_ambiguous_switch_is_never_operated(self):
        original = '<button onclick="document.getElementById(\'composer\').hidden=false">Create post</button>'
        self.html = fixture().replace(original, '<button>Switch now</button><button>Switch now</button>')
        post = self.draft()
        with self.assertRaisesRegex(ValueError, 'Chuyển ngay'):
            self.prepare(post)
        self.assertIsNone(self.composer_tab().evaluate('window.switches'))
        self.assertTrue(self.composer_tab().locator('[role=dialog]').is_hidden())


if __name__ == '__main__':
    unittest.main()
