"""Offline browser/scheduler exercises for one-click publishing and Reel receipts."""
import html
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from tests import test_page_browser_dom as dom_fixture
from tests import test_page_post_now as ui_fixture
from teodub.facebook_web_post import RESULT_SCRIPT


class AutonomousDomTests(unittest.TestCase):
    setUpClass = dom_fixture.BrowserDomTests.__dict__['setUpClass']
    tearDownClass = dom_fixture.BrowserDomTests.__dict__['tearDownClass']
    setUp = dom_fixture.BrowserDomTests.setUp
    draft = dom_fixture.BrowserDomTests.draft
    prepare = dom_fixture.BrowserDomTests.prepare
    publish = dom_fixture.BrowserDomTests.publish
    saved = dom_fixture.BrowserDomTests.saved
    composer_tab = dom_fixture.BrowserDomTests.composer_tab

    def reel(self):
        self.html = dom_fixture.reel_fixture().replace(
            'Your post was published. <a href="https://www.facebook.com/123/videos/999">View video</a>',
            'Đã đăng thước phim. <a href="https://www.facebook.com/reel/999">Xem thước phim</a>')
        path = Path(self.temp.name)/'reel.mp4'
        path.write_bytes(b'Offline uploaded media')
        return self.draft('Caption đầy đủ.\n\n#CauCa #DoCau', [path])

    def receipt_html(self, post, *, owner='123', body=None, video=True, comment=False):
        text = html.escape(post['body'] if body is None else body)
        caption = '<div style="white-space:pre-wrap">'+text+'</div>'
        if comment:
            caption = '<div aria-label="Bình luận của người khác">'+caption+'</div>'
        return ('<meta charset="utf-8"><main role="main"><a href="https://www.facebook.com/profile.php?id='+owner+'">Page Demo</a>'+
            caption+('<video src="blob:published-video" style="width:120px;height:120px"></video>'
            '<script>Object.defineProperties(document.querySelector("video"),{duration:{value:30},videoWidth:{value:720},videoHeight:{value:1280}});</script>' if video else '')+'</main>')

    def video_snapshot(self, body):
        return dict(kind='video', body=body, composer=dict(video_preview=[dict(duration=30,width=720,height=1280)]))

    def route_reel(self, text):
        self.context.route('**/reel/999', lambda route: route.fulfill(status=200, content_type='text/html', body=text))

    def test_real_reel_wizard_and_new_vietnamese_toast_verify_owner_caption_and_receipt(self):
        post = self.reel()
        self.route_reel(self.receipt_html(post))
        prepared = self.prepare(post)
        original = self.composer_tab()
        self.assertEqual(prepared['state'], 'review')
        self.assertEqual(prepared['browser_review']['body'], post['body'])
        self.assertEqual(original.evaluate('window.nexts'), 2)
        result = self.publish(post, progress=self.progress.append)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['external_url'], 'https://www.facebook.com/reel/999')
        receipt = result['browser_receipt']
        self.assertEqual(receipt['kind'], 'verified_published_reel')
        self.assertEqual(receipt['target_id'], '123')
        self.assertEqual(receipt['body'], post['body'])
        self.assertTrue(receipt['visible_video'])
        self.assertEqual(original.evaluate('window.uploads'), 1)
        self.assertEqual(original.evaluate('window.posts'), 1)
        self.assertIn('Facebook', ' '.join(self.progress))
        with self.assertRaises(ValueError):
            self.publish(post)
        self.assertEqual(original.evaluate('window.posts'), 1)

    def test_wrong_owner_truncated_comment_only_and_missing_video_cannot_be_success(self):
        post = self.reel()
        snapshot = self.video_snapshot(post['body'])
        tab = self.context.new_page()
        for args in (dict(owner='999'), dict(body='Caption…'), dict(video=False), dict(comment=True)):
            with self.subTest(args=args):
                self.route_reel(self.receipt_html(post, **args))
                tab.goto('https://www.facebook.com/reel/999')
                self.assertIsNone(self.publisher._reel_evidence(tab, tab.url, self.target, snapshot))
        self.route_reel(self.receipt_html(post))
        tab.goto('https://www.facebook.com/reel/999')
        self.assertIsNotNone(self.publisher._reel_evidence(tab, tab.url, self.target, snapshot))

    def test_caption_expands_itself_without_human_input(self):
        post = self.reel()
        page = self.receipt_html(post).replace(html.escape(post['body']), 'Caption đầy đủ.')
        page = page.replace('</main>', '<section><div>Caption đầy đủ.</div><button id="more" '
            'onclick="document.getElementById(\'full\').innerText='+html.escape(repr(post['body']), quote=True)+
            ';this.remove()">Xem thêm</button></section></main>')
        # One caption node becomes the full post; the short title remains unrelated.
        page = page.replace('<div style="white-space:pre-wrap">', '<div id="full" style="white-space:pre-wrap">')
        self.route_reel(page)
        tab = self.context.new_page(); tab.goto('https://www.facebook.com/reel/999')
        snapshot = self.video_snapshot(post['body'])
        result = self.publisher._reel_evidence(tab, tab.url, self.target, snapshot)
        self.assertIsNotNone(result)
        self.assertEqual(tab.get_by_role('button', name='Xem thêm').count(), 0)

    def test_new_reel_candidates_never_reuse_old_links_with_different_tracking(self):
        old = dict(statuses=[dict(text='Đã đăng thước phim', links=[dict(url='https://www.facebook.com/reel/999/?old=1')])], articles=[])
        new = dict(statuses=[dict(text='Đã đăng thước phim!', links=[dict(url='https://www.facebook.com/reel/999?new=2')])], articles=[])
        self.assertEqual(self.publisher._new_reel_links(new, old), [])
        foreign = dict(statuses=[dict(text='Đã đăng thước phim', links=[dict(url='https://evil.example/reel/999')])], articles=[])
        self.assertEqual(self.publisher._new_reel_links(foreign, dict(statuses=[], articles=[])), [])

    def test_result_script_observes_reel_success_but_not_upload_or_unrelated_notice(self):
        tab = self.context.new_page()
        for text, expected in (('Đã đăng thước phim', 1), ('Your reel has been published', 1),
                ('Đang tải thước phim', 0), ('Bạn có thông báo mới', 0)):
            tab.set_content('<div role="status">'+text+' <a href="https://www.facebook.com/reel/999">View</a></div>')
            self.assertEqual(len(tab.evaluate(RESULT_SCRIPT)['statuses']), expected)

    def test_upload_advances_to_edit_reel_before_next_poll_without_another_upload(self):
        post = self.reel()
        self.route_reel(self.receipt_html(post))
        self.html = self.html.replace('preview.append(selectedVideo);', '''preview.append(selectedVideo);
            composer.setAttribute('aria-busy','true');
            setTimeout(()=>{composer.removeAttribute('aria-busy');toEdit();},200);''')
        self.prepare(post)
        tab = self.composer_tab()
        self.assertEqual(tab.evaluate('window.uploads'), 1)
        self.assertEqual(tab.evaluate('window.nexts'), 2)
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertEqual(tab.evaluate('window.posts'), 1)

    def test_completed_upload_clears_input_but_same_video_continues_to_publication(self):
        post = self.reel()
        self.route_reel(self.receipt_html(post))
        self.html = self.html.replace('preview.append(selectedVideo);', '''preview.append(selectedVideo);
            composer.setAttribute('aria-busy','true');
            setTimeout(()=>{e.target.value='';composer.removeAttribute('aria-busy');},200);''')
        result = self.prepare(post)
        self.assertEqual(len(result['browser_review']['observed_files']), 1)
        tab = self.composer_tab()
        self.assertEqual(tab.locator('input[type=file]').input_value(), '')
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertEqual(tab.evaluate('window.uploads'), 1)
        self.assertEqual(tab.evaluate('window.posts'), 1)

    def test_temporary_composer_remount_while_uploading_is_not_a_terminal_failure(self):
        post = self.reel()
        self.route_reel(self.receipt_html(post))
        self.html = self.html.replace('preview.append(selectedVideo);', '''preview.append(selectedVideo);
            composer.setAttribute('aria-busy','true');
            setTimeout(()=>{composer.hidden=true;},150);
            setTimeout(()=>{composer.hidden=false;composer.removeAttribute('aria-busy');},550);''')
        self.prepare(post)
        tab = self.composer_tab()
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertEqual(tab.evaluate('window.uploads'), 1)

    def test_react_clears_picker_in_change_handler_but_input_event_preserves_file_evidence(self):
        post = self.reel()
        self.route_reel(self.receipt_html(post))
        self.html = self.html.replace('preview.append(selectedVideo);', '''preview.append(selectedVideo);
            e.target.value='';composer.setAttribute('aria-busy','true');
            setTimeout(()=>{composer.removeAttribute('aria-busy');},200);''')
        self.prepare(post)
        tab = self.composer_tab()
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertEqual(tab.evaluate('window.uploads'), 1)

    def test_optional_call_now_prompt_is_skipped_and_real_receipt_still_required(self):
        post = self.reel()
        self.route_reel(self.receipt_html(post))
        self.html = self.html.replace('document.body.append(done);}', '''
            const prompt=document.createElement('section');prompt.setAttribute('role','dialog');
            prompt.setAttribute('aria-label','Trò chuyện trực tiếp với mọi người');
            prompt.innerHTML='<p>Hãy thêm nút "Gọi ngay" vào bài viết.</p><button id="add">Thêm nút</button><button id="later">Lúc khác</button>';
            document.body.append(prompt);
            prompt.querySelector('#add').onclick=()=>{window.wrongAdd=true;};
            prompt.querySelector('#later').onclick=()=>{window.dismissed=true;prompt.remove();document.body.append(done);};
        }''')
        self.prepare(post)
        tab = self.composer_tab()
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertTrue(tab.evaluate('window.dismissed'))
        self.assertIsNone(tab.evaluate('window.wrongAdd'))
        self.assertEqual(tab.evaluate('window.posts'), 1)

    def test_automatically_advanced_changed_video_is_rejected_before_final_post(self):
        post = self.reel()
        self.html = self.html.replace('preview.append(selectedVideo);', '''preview.append(selectedVideo);
            composer.setAttribute('aria-busy','true');
            setTimeout(()=>{composer.removeAttribute('aria-busy');toEdit();selectedVideo.src='blob:wrong';
              Object.defineProperty(selectedVideo,'duration',{value:45});},200);''')
        from types import SimpleNamespace
        ticks = iter(range(0,100000,5))
        with patch('teodub.facebook_web_post.time', SimpleNamespace(monotonic=lambda:next(ticks))):
            with self.assertRaisesRegex(ValueError, 'Video xem trước'):
                self.prepare(post)
        self.assertEqual(self.composer_tab().evaluate('window.uploads'), 1)
        self.assertIsNone(self.composer_tab().evaluate('window.posts'))

    def test_poster_changes_when_facebook_decodes_video_are_not_a_different_video(self):
        post = self.reel()
        self.route_reel(self.receipt_html(post))
        self.html = self.html.replace("window.nexts=(window.nexts||0)+1;", "window.nexts=(window.nexts||0)+1;selectedVideo.poster='https://www.facebook.com/decoded-thumb.jpg';")
        self.prepare(post)
        self.assertEqual(self.publish(post)['state'], 'published')

    def test_new_blob_requires_matching_loaded_media_only_during_original_transition(self):
        from teodub.facebook_web_post import _same_video_preview
        original = [dict(src='blob:one',poster='first',width=720,height=1280,duration=123.5)]
        current = [dict(src='blob:two',poster='second',width=720,height=1280,duration=123.5)]
        self.assertFalse(_same_video_preview(current, original))
        self.assertTrue(_same_video_preview(current, original, transition=True))
        for key, value in [('duration',0),('duration',125),('width',1080),('src','https://foreign.example/video')]:
            changed=[dict(current[0], **{key:value})]
            self.assertFalse(_same_video_preview(changed, original, transition=True))

    def test_canvas_reel_player_retains_upload_and_completes_wizard(self):
        post = self.reel()
        self.route_reel(self.receipt_html(post))
        self.html = self.html.replace('editPreview.append(selectedVideo);', '''
            selectedVideo.hidden=true;
            const pane=document.createElement('aside');pane.setAttribute('role','complementary');pane.setAttribute('aria-label','Xem trước');
            pane.innerHTML='<canvas width="120" height="160"></canvas><button aria-label="Tạm dừng video"></button><input type="range" aria-label="Change Position">';
            editPreview.append(pane);window.canvasPane=pane;
        ''').replace('finalPreview.append(selectedVideo);','finalPreview.append(window.canvasPane);')
        prepared = self.prepare(post)
        self.assertEqual(prepared['browser_review']['composer']['preview_observation'], 'canvas_player_retained_upload')
        self.assertEqual(self.publish(post)['state'], 'published')

    def test_static_canvas_without_playback_controls_cannot_be_the_uploaded_video(self):
        post = self.reel()
        self.prepare(post)
        tab = self.composer_tab()
        preview = self.saved(post)['browser_review']['composer']['video_preview']
        tab.evaluate('''() => {
            document.querySelectorAll('video').forEach(v=>v.hidden=true);
            const pane=document.createElement('aside');pane.setAttribute('role','complementary');pane.setAttribute('aria-label','Xem trước');
            pane.innerHTML='<canvas width="120" height="160"></canvas>';settings.append(pane);
        }''')
        from teodub.facebook_web_post import REEL_SETTINGS_NAMES
        with self.assertRaisesRegex(ValueError, 'Video xem trước'):
            self.publisher._observe_reel(tab, self.target, REEL_SETTINGS_NAMES, preview)

    def test_shared_alert_uses_fresh_processed_notification_and_verifies_actual_reel(self):
        post = self.reel()
        self.route_reel(self.receipt_html(post))
        self.html = self.html.replace('document.body.append(done);', '''
            done.innerHTML='Đã chia sẻ bài viết của bạn với EVERYONE';
            document.body.append(done);
            const bell=document.createElement('button');bell.innerText='Thông báo, 1 chưa đọc';document.body.append(bell);
            bell.onclick=()=>{
                const panel=document.createElement('div');panel.setAttribute('role','dialog');panel.setAttribute('aria-label','Thông báo');
                panel.innerHTML='<a href="https://www.facebook.com/reel/999?notif_t=fb_shorts_video_processed&notif_id='+Date.now()*1000+'">Đã đăng thước phim. Bạn cũng có thể chia sẻ lên Tin.</a>';
                document.body.append(panel);
            };
        ''')
        self.prepare(post)
        tab = self.composer_tab()
        result = self.publish(post)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['external_url'], 'https://www.facebook.com/reel/999')
        self.assertEqual(tab.evaluate('window.posts'), 1)

    def test_processed_notifications_ignore_old_missing_and_unrelated_links(self):
        import time
        started = time.time()
        tab = self.context.new_page()
        for stamp, kind, expected in ((int(started*1e6), 'fb_shorts_video_processed', 1),
                (int((started-3600)*1e6), 'fb_shorts_video_processed', 0),
                ('', 'fb_shorts_video_processed', 0), (int(started*1e6), 'other', 0)):
            tab.set_content('<div role="dialog" aria-label="Thông báo"><a href="https://www.facebook.com/reel/999?notif_t='+kind+'&notif_id='+str(stamp)+'">Đã đăng thước phim</a></div>')
            self.assertEqual(len(self.publisher._notification_reels(tab, started)), expected)

    def test_reel_duplicated_caption_hide_control_owner_suffix_and_offscreen_next_video(self):
        post = self.reel()
        page = self.receipt_html(post).replace('>Page Demo</a>', '>Page Demo, see owner profile</a>')
        page = page.replace(html.escape(post['body']), html.escape(post['body'])+'<button>Ẩn bớt</button>')
        page = page.replace('</main>', '<video src="blob:next" style="position:absolute;top:20000px;width:120px;height:120px"></video></main>')
        page = page.replace('</main>', '<div>'+html.escape(post['body'])+'</div></main>')
        self.route_reel(page)
        tab = self.context.new_page(); tab.goto('https://www.facebook.com/reel/999')
        self.assertIsNotNone(self.publisher._reel_evidence(tab, tab.url, self.target, self.video_snapshot(post['body'])))

    def test_scheduler_completes_multiline_video_without_any_manual_review_action(self):
        import queue
        import time
        from types import SimpleNamespace
        from tests import test_page_schedule as schedule_fixture
        from teodub.page_schedule import PageScheduleStore, PageScheduleController, campaign_video_source
        self.directory = Path(self.temp.name)
        source = campaign_video_source(self.directory, *schedule_fixture.ScheduleTests.video(self))
        store = PageScheduleStore(self.directory)
        row = store.create(self.account['id'], self.target['id'], source, 'Caption nhiều đoạn.\n\nCâu hỏi cho người xem?', '#CauCa', time.time()+3600)
        post = store.activity.post(row['account_id'], row['page_key'], row['post_id'])
        self.html = dom_fixture.reel_fixture().replace(
            'Your post was published. <a href="https://www.facebook.com/123/videos/999">View video</a>',
            'Đã đăng thước phim <a href="https://www.facebook.com/reel/999">Xem</a>')
        self.route_reel(self.receipt_html(post))
        session = SimpleNamespace(loading_pages=False, post_actions={})
        operations = []
        def submit(account, page, post_id, operation, *, request_id):
            operations.append(operation)
            result = getattr(self.publisher, operation)(self.context, account, page, post_id)
            session.post_actions[request_id] = dict(state='done', result=result, message=result['message'])
            return request_id
        owner = SimpleNamespace(studio=SimpleNamespace(data_dir=self.directory), sessions={row['account_id']:session},
            status=Mock(), submit_page_browser=submit)
        controller = PageScheduleController(owner)
        self.addCleanup(controller.close)
        with patch('teodub.page_schedule.threading.Thread') as thread:
            thread.return_value.start.side_effect = lambda: controller.validate_video(controller.store.get(row['id']))
            controller.start_now(row['account_id'], row['page_key'], row['id'], expected_schedule=row)
        for _ in range(4):
            controller.last_tick = 0; controller.tick()
        saved = controller.store.get(row['id'])
        self.assertEqual(saved['state'], 'published')
        self.assertEqual(operations, ['prepare', 'publish'])
        self.assertEqual(saved['body'], row['body'])
        self.assertEqual(saved['post_id'], row['post_id'])
        self.assertTrue(saved['finished_at'])
        self.assertEqual(len(controller.store.list()), 1)
        self.assertEqual(len(controller.store.immediate_history(row['id'])), 1)


class OneClickUITests(unittest.TestCase):
    setUp = ui_fixture.PostNowUITests.setUp
    schedule = ui_fixture.PostNowUITests.schedule
    panel = ui_fixture.PostNowUITests.panel
    choose = ui_fixture.PostNowUITests.choose
    start_schedule = ui_fixture.PostNowUITests.start_schedule
    pause_schedule = ui_fixture.PostNowUITests.pause_schedule
    cancel_schedule = ui_fixture.PostNowUITests.cancel_schedule

    def test_primary_button_runs_captured_appointment_once_without_confirmation_window(self):
        row = self.schedule(); panel = self.panel(); self.choose(panel, row)
        def launch(account, page, ident, *, expected_schedule):
            self.assertEqual((account, page, ident), (self.a, self.p, row['id']))
            self.assertEqual(expected_schedule, row)
            return self.store.update(ident, state='preparing', attempted_at='One explicit click')
        self.controller.start_now = Mock(side_effect=launch)
        with patch.object(panel, 'preview_now') as preview:
            panel.now_button.invoke()
            panel.now_button.invoke()
            panel.post_immediately()
        preview.assert_not_called()
        self.controller.start_now.assert_called_once()
        self.assertIsNone(panel.now_window)
        self.assertEqual(self.store.get(row['id'])['state'], 'preparing')


if __name__ == '__main__':
    unittest.main()
