"""Actual DOM navigation/posting fixtures; no user Chrome profile or Facebook network."""
import html
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from teodub.common import Cancelled
from teodub.facebook_accounts import FacebookAccounts
from teodub.facebook_page_identity import ensure_page_actor, verify_page_actor
from teodub.facebook_web_post import FINISHED_TAB_SECONDS, BrowserPagePublisher


def fixture(*, duplicate=False, missing=False, wrong_id=False, switch_works=True, linked_author=False):
    other = '<button aria-label="Page Demo">Page Demo</button>' if duplicate else ''
    target = '' if missing else '<button id="page-choice" '+('href="https://www.facebook.com/profile.php?id=456"' if wrong_id else '')+' onclick="choose()">Page Demo</button>'
    author = '<a href="https://www.facebook.com/profile.php?id=123">Page Demo</a>' if linked_author else '<span>Page Demo</span>'
    choice_script = "localStorage.actor='Page Demo';" if switch_works else ''
    return f'''<!doctype html><meta charset="utf-8">
    <button aria-label="Trang cá nhân của bạn" onclick="toggle()">Profile</button>
    <div role="dialog" aria-label="Trang cá nhân của bạn" id="profile" hidden>
      <div id="quick"><a href="/me/">Người dùng</a><button aria-label="Chuyển sang Page Demo">Page Demo</button>
      <button onclick="showProfiles()">Xem tất cả trang cá nhân</button></div>
      <div id="choices" hidden><h2>Chọn trang cá nhân</h2>
      <button id="personal">Người dùng</button>{target}{other}<button>Tạo Trang</button></div></div>
    <main role="main"><h1>Page Demo</h1>
      <button onclick="document.getElementById('composer').hidden=false">Demo ơi, bạn đang nghĩ gì thế?</button>
    </main>
    <div role="dialog" id="composer" aria-label="Tạo bài viết" hidden><h2>Tạo bài viết</h2>{author}
      <div role="textbox" contenteditable="true"></div><button onclick="publish()">Đăng</button></div>
    <script>
    function toggle(){{const p=document.getElementById('profile');p.hidden=!p.hidden;
      document.getElementById('quick').hidden=false;document.getElementById('choices').hidden=true;}}
    function showProfiles(){{document.getElementById('quick').hidden=true;document.getElementById('choices').hidden=false;
      const active=localStorage.actor||'Người dùng';document.getElementById('personal').setAttribute('aria-label','Người dùng'+(active==='Người dùng'?', đang chọn':''));
      const target=document.getElementById('page-choice');if(target)target.setAttribute('aria-label','Page Demo'+(active==='Page Demo'?', đang chọn':', 2 thông báo'));}}
    function choose(){{localStorage.switches=String(Number(localStorage.switches||0)+1);{choice_script}document.getElementById('profile').hidden=true;}}
    function publish(){{localStorage.posts=String(Number(localStorage.posts||0)+1);document.getElementById('composer').hidden=true;
      const status=document.createElement('div');status.setAttribute('role','status');status.innerHTML='Your post was published. <a href="https://www.facebook.com/123/posts/999">View post</a>';document.body.append(status);}}
    </script>'''


def fixture_24_10():
    """The English layout read from Tèo's Chrome on 24/10/2026: the account button 'Account, your profile' (a menu
    button), the dialog 'Your profile', 'See all profiles', 'Select profile' with ', currently selected' and
    ', N notifications', an opener "What's on your mind?" and a composer whose author is plain text."""
    return '''<!doctype html><meta charset="utf-8">
    <div role="button" tabindex="0" aria-label="Account, your profile" aria-haspopup="menu" onclick="toggle()"><img alt="Your profile"></div>
    <div role="dialog" aria-label="Your profile" id="profile" hidden>
      <div id="quick"><a href="/profile.php?id=777">Phú thọ</a><div role="button">Switch to Minh Châu</div>
      <div role="button" onclick="showProfiles()">See all profiles</div><div role="button">Log out</div></div>
      <div id="choices" hidden><div role="button" onclick="toggle();toggle()">Back to previous menu</div><h2>Select profile</h2>
      <div role="button" id="current">Phú thọ</div><div role="button" id="page-choice" onclick="choose()">Page Demo</div>
      <div role="button">Demo 23-10</div><a href="/pages/">See all Pages</a></div></div>
    <main role="main"><h1>Page Demo</h1>
      <div role="button" onclick="document.getElementById('composer').hidden=false">What's on your mind?</div></main>
    <div role="dialog" id="composer" aria-label="Create post" hidden><h2>Create post</h2><span>Page Demo</span>
      <div role="textbox" contenteditable="true" aria-label="What's on your mind?"></div>
      <div role="button" onclick="publish()">Post</div></div>
    <script>
    function toggle(){const p=document.getElementById('profile');p.hidden=!p.hidden;
      document.getElementById('quick').hidden=false;document.getElementById('choices').hidden=true;}
    function showProfiles(){document.getElementById('quick').hidden=true;document.getElementById('choices').hidden=false;
      const active=localStorage.actor||'Phú thọ';
      document.getElementById('current').setAttribute('aria-label','Phú thọ'+(active==='Phú thọ'?', currently selected':', 25 notifications'));
      document.getElementById('page-choice').setAttribute('aria-label','Page Demo'+(active==='Page Demo'?', currently selected':', 3 notifications'));}
    function choose(){localStorage.switches=String(Number(localStorage.switches||0)+1);localStorage.actor='Page Demo';
      document.getElementById('profile').hidden=true;}
    function publish(){localStorage.posts=String(Number(localStorage.posts||0)+1);document.getElementById('composer').hidden=true;
      const status=document.createElement('div');status.setAttribute('role','status');status.innerHTML='Your post was published. <a href="https://www.facebook.com/123/posts/999">View post</a>';document.body.append(status);}
    </script>'''


def reloading_fixture(delay_ms):
    """fixture_24_10, but choosing a profile reloads the whole page after `delay_ms` (26/10/2026: Facebook replaces
    the document after the choice; a read during that reload failed Phú thọ's 10:30 post)."""
    page = fixture_24_10()
    choice = "document.getElementById('profile').hidden=true;}"
    assert page.count(choice) == 1
    return page.replace(choice, "document.getElementById('profile').hidden=true;"
                                f"setTimeout(()=>location.reload(),{delay_ms});}}")


class IdentityDomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(channel='chrome',headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = FacebookAccounts(self.temp.name)
        self.account = self.registry.add_account('Test account')
        self.target = self.registry.save_page(self.account['id'],'Page Demo','facebook.com/profile.php?id=123','123')
        self.publisher = BrowserPagePublisher(self.temp.name)
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.html = fixture()
        self.context.route('https://www.facebook.com/**',lambda route: route.fulfill(body=self.html,content_type='text/html'))
        self.tab = self.context.new_page()
        self.tab.goto(self.target['url'])

    def actor(self):
        return ensure_page_actor(self.context,self.tab,self.target)

    def reload(self):
        self.tab.reload()

    def draft(self):
        return self.publisher.store.save_draft(self.account['id'],self.target['id'],'Nội dung đã duyệt',delivery_mode='browser',browser_finish_mode='tool')

    def prepare(self,post):
        return self.publisher.prepare(self.context,self.account['id'],self.target['id'],post['id'])

    def publish(self,post):
        return self.publisher.publish(self.context,self.account['id'],self.target['id'],post['id'])

    def test_switch_from_personal_and_verify_selected_profile(self):
        result = self.actor()
        self.assertTrue(result['switched'])
        self.assertEqual(self.tab.evaluate('localStorage.actor'),'Page Demo')
        self.assertEqual(self.tab.evaluate('localStorage.switches'),'1')
        self.assertTrue(self.tab.get_by_role('dialog',name='Trang cá nhân của bạn').is_hidden())

    def test_current_page_not_switched_again_and_notification_suffix_supported(self):
        self.actor()
        result = self.actor()
        self.assertFalse(result['switched'])
        self.assertEqual(self.tab.evaluate('localStorage.switches'),'1')

    def test_missing_duplicate_wrong_id_or_unsuccessful_switch_never_edits_composer(self):
        for option,message in [('missing','duy nhất'),('duplicate','duy nhất'),('wrong_id','Page ID'),('switch_works','chưa chuyển')]:
            with self.subTest(option=option):
                self.tab.evaluate('localStorage.clear()')
                self.html=fixture(**{option:False if option=='switch_works' else True})
                self.reload()
                with self.assertRaisesRegex(ValueError,message):
                    self.actor()
                self.assertEqual(self.tab.locator('[role=textbox]').inner_text(),'')
                self.assertIsNone(self.tab.evaluate('localStorage.posts'))

    def test_unfinished_editor_in_another_tab_blocks_actor_switch(self):
        other=self.context.new_page();other.goto(self.target['url'])
        other.get_by_role('button',name='Demo ơi, bạn đang nghĩ gì thế?').click()
        other.get_by_role('textbox').fill('Không được mất bản nháp')
        with self.assertRaisesRegex(ValueError,'chưa đóng'):
            self.actor()
        self.assertEqual(other.get_by_role('textbox').inner_text(),'Không được mất bản nháp')
        self.assertIsNone(self.tab.evaluate('localStorage.switches'))

    def test_cancellation_before_switch_keeps_personal_actor(self):
        stop=threading.Event();stop.set()
        with self.assertRaises(Cancelled):
            ensure_page_actor(self.context,self.tab,self.target,cancel=stop)
        self.assertIsNone(self.tab.evaluate('localStorage.switches'))

    def test_read_only_actor_check_never_switches_and_closes_only_its_scratch_tab(self):
        before=len(self.context.pages)
        with self.assertRaisesRegex(ValueError,'đã đổi'):
            verify_page_actor(self.context,self.target)
        self.assertEqual(len(self.context.pages),before)
        self.assertIsNone(self.tab.evaluate('localStorage.switches'))
        self.actor()
        self.assertTrue(verify_page_actor(self.context,self.target))
        self.assertEqual(len(self.context.pages),before)

    def test_plain_author_prepare_publish_end_to_end_one_click(self):
        post=self.draft();prepared=self.prepare(post)
        self.assertEqual(prepared['state'],'review')
        self.assertEqual(prepared['browser_review']['identity']['source'],'visible_profile_selector_and_composer')
        self.assertIsNone(self.tab.evaluate('localStorage.posts'))
        result=self.publish(post)
        self.assertEqual(result['state'],'published')
        self.assertEqual(result['external_url'],'https://www.facebook.com/123/posts/999')
        self.assertEqual(self.tab.evaluate('localStorage.posts'),'1')
        with self.assertRaises(ValueError):self.publish(post)
        self.assertEqual(self.tab.evaluate('localStorage.posts'),'1')

    def test_separate_header_and_feed_mains_use_verified_selected_actor(self):
        self.html = self.html.replace('<main role="main"><h1>Page Demo</h1>',
            '<main role="main"><h1>Page Demo</h1></main><main role="main">')
        self.reload()
        post = self.draft()
        self.assertEqual(self.prepare(post)['state'], 'review')
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertEqual(self.tab.evaluate('localStorage.posts'), '1')

    def test_separate_feed_mains_with_multiple_openers_do_not_choose_first(self):
        self.html = self.html.replace('<main role="main"><h1>Page Demo</h1>',
            '<main role="main"><h1>Page Demo</h1></main><main role="main">')
        self.html += '<main role="main"><button onclick="window.wrong=1">Tạo bài viết</button></main>'
        self.reload()
        with self.assertRaisesRegex(ValueError, 'nút Tạo bài viết.*2 mục'):
            self.prepare(self.draft())
        self.assertIsNone(self.tab.evaluate('localStorage.posts'))
        self.assertIsNone(self.tab.evaluate('window.wrong'))

    def test_actor_changed_after_review_blocks_publish_without_switching_back(self):
        post=self.draft();self.prepare(post)
        self.tab.evaluate("localStorage.actor='Người dùng'")
        with self.assertRaisesRegex(ValueError,'đã đổi'):
            self.publish(post)
        self.assertIsNone(self.tab.evaluate('localStorage.posts'))
        self.assertEqual(self.tab.evaluate('localStorage.actor'),'Người dùng')

    def test_24_10_account_button_switches_from_another_page_and_verifies_it(self):
        # 24/10: "Account, your profile" was not recognized, so du lịch toàn cầu and Phú thọ stopped before Publish.
        self.html = fixture_24_10()
        self.reload()
        result = self.actor()
        self.assertTrue(result['switched'])
        self.assertEqual(self.tab.evaluate('localStorage.actor'), 'Page Demo')
        self.assertEqual(self.tab.evaluate('localStorage.switches'), '1')
        self.assertTrue(self.tab.get_by_role('dialog', name='Your profile').is_hidden())
        self.assertFalse(self.actor()['switched'])  # Already the selected Page: not switched again.
        self.assertEqual(self.tab.evaluate('localStorage.switches'), '1')

    def test_24_10_layout_prepares_with_the_plain_author_and_publishes_once(self):
        self.html = fixture_24_10()
        self.reload()
        post = self.draft()
        prepared = self.prepare(post)
        self.assertEqual(prepared['state'], 'review')
        self.assertEqual(prepared['browser_review']['identity']['source'], 'visible_profile_selector_and_composer')
        self.assertIsNone(self.tab.evaluate('localStorage.posts'))
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertEqual(self.tab.evaluate('localStorage.posts'), '1')
        with self.assertRaises(ValueError):
            self.publish(post)
        self.assertEqual(self.tab.evaluate('localStorage.posts'), '1')

    def test_26_10_reload_after_choosing_a_profile_is_waited_for_not_a_failure(self):
        # 26/10 10:30: Facebook reloaded the page after the switch to Phú thọ and a read during the reload failed
        # the post ("Execution context was destroyed"). The reload is waited for; the profile is chosen once.
        for delay in (0, 150, 400):
            with self.subTest(delay=delay):
                self.tab.evaluate('localStorage.clear()')
                self.html = reloading_fixture(delay)
                self.reload()
                result = self.actor()
                self.assertTrue(result['switched'])
                self.assertEqual(self.tab.evaluate('localStorage.actor'), 'Page Demo')
                self.assertEqual(self.tab.evaluate('localStorage.switches'), '1')
                self.assertTrue(self.tab.get_by_role('dialog', name='Your profile').is_hidden())

    def test_26_10_reload_after_switch_prepares_and_publishes_once(self):
        self.html = reloading_fixture(150)
        self.reload()
        post = self.draft()
        self.assertEqual(self.prepare(post)['state'], 'review')
        self.assertEqual(self.tab.evaluate('localStorage.switches'), '1')
        self.assertIsNone(self.tab.evaluate('localStorage.posts'))
        self.assertEqual(self.publish(post)['state'], 'published')
        self.assertEqual(self.tab.evaluate('localStorage.posts'), '1')

    def test_26_10_tab_that_does_not_answer_stops_the_switch_within_seconds(self):
        # 26/10: one of 19 old tabs never answered; the switch check waited 20 minutes and held the account.
        self.html = fixture_24_10()
        self.reload()
        other = self.context.new_page()
        other.goto(self.target['url'])
        other.evaluate('setTimeout(() => { const end = Date.now()+10000; while (Date.now() < end) {} }, 0)')
        time.sleep(.3)
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, 'không phản hồi'):
            self.actor()
        self.assertLess(time.monotonic()-started, 8)
        self.assertIsNone(self.tab.evaluate('localStorage.switches'))

    def test_26_10_crashed_tab_does_not_block_the_switch(self):
        self.html = fixture_24_10()
        self.reload()
        crashed = self.context.new_page()
        crashed.goto(self.target['url'])
        with self.assertRaises(Exception):
            crashed.goto('chrome://crash', timeout=5000)
        time.sleep(.5)
        self.assertTrue(self.actor()['switched'])
        self.assertEqual(self.tab.evaluate('localStorage.switches'), '1')

    def attempt_tab(self, post):
        key = self.publisher._key(self.account['id'], self.target['id'], post['id'])
        tabs = [entry['tab'] for entry in self.publisher._post_tabs(self.context) if entry['key'] == key]
        self.assertEqual(len(tabs), 1)
        return tabs[0]

    def test_26_10_finished_post_tabs_close_when_the_next_post_starts(self):
        # 26/10: every attempt left its own tab open (19 tabs, 7.8 GB) until one crashed.
        self.html = fixture_24_10()
        self.reload()
        published = self.draft()
        self.prepare(published)
        self.assertEqual(self.publish(published)['state'], 'published')
        published_tab = self.attempt_tab(published)
        self.html = fixture_24_10().replace("What's on your mind?</div></main>", '</div></main>')  # No opener.
        failed = self.draft()
        with self.assertRaises(ValueError):
            self.prepare(failed)
        self.assertEqual(self.publisher.store.post(self.account['id'], self.target['id'], failed['id'])['state'], 'failed')
        failed_tab = self.attempt_tab(failed)
        self.html = fixture_24_10()
        later = time.time()+FINISHED_TAB_SECONDS+20
        following = self.draft()
        with patch('teodub.facebook_web_post.wall_time', return_value=later):
            self.assertEqual(self.prepare(following)['state'], 'review')
        self.assertTrue(published_tab.is_closed())
        self.assertTrue(failed_tab.is_closed())
        self.assertFalse(self.tab.is_closed())  # A tab Tèo did not open for a post is never closed.
        self.assertFalse(self.attempt_tab(following).is_closed())
        self.assertEqual(self.tab.evaluate('localStorage.posts'), '1')  # Closing tabs never posts anything.

    def test_26_10_recent_or_editing_post_tabs_stay_open(self):
        self.html = fixture_24_10()
        self.reload()
        published = self.draft()
        self.prepare(published)
        self.publish(published)
        published_tab = self.attempt_tab(published)
        reviewed = self.draft()
        self.prepare(reviewed)  # The first Publish click is seconds old: Facebook may still be busy there.
        self.assertFalse(published_tab.is_closed())
        published_tab.get_by_role('button', name="What's on your mind?").click()
        published_tab.get_by_role('textbox').fill('Ai đó đang viết ở đây')
        with patch('teodub.facebook_web_post.wall_time', return_value=time.time()+FINISHED_TAB_SECONDS+20):
            self.assertEqual(self.prepare(self.draft())['state'], 'review')
        self.assertFalse(published_tab.is_closed())  # An open editor is never closed.
        self.assertEqual(published_tab.get_by_role('textbox').inner_text(), 'Ai đó đang viết ở đây')
        self.assertFalse(self.attempt_tab(reviewed).is_closed())  # A post waiting in review keeps its tab.

    def test_plain_author_without_selected_profile_evidence_never_accepted(self):
        tab=self.context.new_page();tab.goto(self.target['url'])
        tab.get_by_role('button',name='Demo ơi, bạn đang nghĩ gì thế?').click()
        with self.assertRaisesRegex(ValueError,'Chưa xác minh'):
            self.publisher._observe(tab,self.target)


if __name__=='__main__': unittest.main()
