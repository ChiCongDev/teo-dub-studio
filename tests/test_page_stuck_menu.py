"""12/11/2026: Facebook opened the account menu but never filled it (a grey skeleton, no names), and the 11:00 post of a Page
stopped. A menu that stays an empty skeleton is closed and opened again, then the page is reloaded once, and only then the post stops.
Real Chromium over fixture pages (no Facebook, no user profile); it never clicks Publish."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from playwright.sync_api import sync_playwright

from tests.test_facebook_page_identity import fixture
from teodub import facebook_page_identity as identity
from teodub.facebook_accounts import FacebookAccounts
from teodub.facebook_page_identity import ensure_page_actor, verify_page_actor

TOGGLE = "function toggle(){"
ESCAPE = "document.addEventListener('keydown',e=>{if(e.key==='Escape')document.getElementById('profile').hidden=true;});"


def skeleton_fixture(*, broken, delay_ms=0):
    """fixture() whose account menu opens as an empty skeleton. `broken`: 'first' (only the first opening is a skeleton),
    'reload' (every opening until the page is reloaded), 'always', or 'slow' (empty for `delay_ms`, then filled)."""
    page = fixture().replace('<div id="quick">', '<div id="skel" hidden style="height:40px;background:#ccc"></div><div id="quick">', 1)
    assert page.count(TOGGLE) == 1 and 'id="skel"' in page
    state = {'first': 'let opens=0;', 'reload': "let broken=!localStorage.reloaded;localStorage.reloaded='1';",
             'always': 'let broken=true;', 'slow': ''}[broken]
    check = {'first': 'opens++===0', 'reload': 'broken', 'always': 'broken', 'slow': 'false'}[broken]
    body = ("const p=document.getElementById('profile');p.hidden=!p.hidden;"
            "document.getElementById('quick').hidden=false;document.getElementById('choices').hidden=true;"
            f"const q=document.getElementById('quick');const bad=!p.hidden&&({check});q.hidden=bad;"
            "document.getElementById('skel').hidden=!bad;")
    if broken == 'slow':
        body = ("const p=document.getElementById('profile');p.hidden=!p.hidden;const q=document.getElementById('quick');"
                f"document.getElementById('choices').hidden=true;document.getElementById('skel').hidden=p.hidden;if(!p.hidden){{q.hidden=true;"
                f"setTimeout(()=>{{q.hidden=false;document.getElementById('skel').hidden=true;}},{delay_ms});}}")
    page = page.replace(TOGGLE, state+ESCAPE+TOGGLE+body+"}function unused(){", 1)
    return page


class StuckMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        registry = FacebookAccounts(self.temp.name)
        account = registry.add_account('Test account')
        self.target = registry.save_page(account['id'], 'Page Demo', 'facebook.com/profile.php?id=123', '123')
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.loads = []
        self.html = skeleton_fixture(broken='first')
        def serve(route):
            self.loads.append(route.request.url)
            route.fulfill(body=self.html, content_type='text/html')
        self.context.route('https://www.facebook.com/**', serve)
        for patcher in (patch.object(identity, 'STUCK_MENU_SECONDS', 1), patch.object(identity, '_save_picture')):
            setattr(self, 'p_'+patcher.attribute, patcher.start())
            self.addCleanup(patcher.stop)
        self.tab = self.context.new_page()

    def open(self, html):
        self.html = html
        self.loads.clear()
        self.tab.goto(self.target['url'])

    def actor(self):
        return ensure_page_actor(self.context, self.tab, self.target)

    def test_a_skeleton_menu_is_closed_and_opened_again(self):
        self.open(skeleton_fixture(broken='first'))
        result = self.actor()
        self.assertTrue(result['switched'])
        self.assertEqual(self.tab.evaluate('localStorage.actor'), 'Page Demo')
        self.assertEqual(self.tab.evaluate('localStorage.switches'), '1')
        self.assertEqual(len(self.loads), 1)  # The page was not reloaded.
        self.p__save_picture.assert_not_called()

    def test_a_menu_that_stays_a_skeleton_is_fixed_by_one_reload(self):
        self.open(skeleton_fixture(broken='reload'))
        result = self.actor()
        self.assertTrue(result['switched'])
        self.assertEqual(self.tab.evaluate('localStorage.switches'), '1')  # The Page was chosen exactly once.
        self.assertEqual(len(self.loads), 2)  # The first load and exactly one reload.

    def test_a_menu_that_never_fills_stops_the_post_with_a_picture_after_one_reload(self):
        self.open(skeleton_fixture(broken='always'))
        with self.assertRaisesRegex(ValueError, 'Menu tài khoản Facebook bị kẹt khi tải'):
            self.actor()
        self.assertEqual(len(self.loads), 2)
        self.p__save_picture.assert_called_once()
        self.assertIsNone(self.tab.evaluate('localStorage.switches'))  # No Page chosen.
        self.assertIsNone(self.tab.evaluate('localStorage.posts'))  # Nothing published.
        self.assertTrue(self.tab.evaluate("document.getElementById('composer').hidden"))  # Composer never opened.

    def test_a_tab_that_holds_a_composer_is_never_closed_or_reloaded(self):
        self.open(skeleton_fixture(broken='always'))
        self.tab.evaluate("document.getElementById('composer').hidden=false")
        with self.assertRaisesRegex(ValueError, 'hộp soạn bài'):
            self.actor()
        self.assertEqual(len(self.loads), 1)
        self.assertFalse(self.tab.evaluate("document.getElementById('composer').hidden"))
        self.p__save_picture.assert_not_called()

    def test_a_slow_menu_that_fills_in_time_is_not_disturbed(self):
        self.open(skeleton_fixture(broken='slow', delay_ms=600))
        with patch.object(identity, 'STUCK_MENU_SECONDS', 5):
            result = self.actor()
        self.assertTrue(result['switched'])
        self.assertEqual(len(self.loads), 1)

    def test_the_check_before_publish_recovers_in_its_own_scratch_tab(self):
        self.open(skeleton_fixture(broken='first'))
        self.actor()
        self.html = skeleton_fixture(broken='reload')
        self.tab.evaluate("localStorage.removeItem('reloaded')")
        before = len(self.context.pages)
        self.assertTrue(verify_page_actor(self.context, self.target))
        self.assertEqual(len(self.context.pages), before)  # The scratch tab is closed again.
        self.assertIsNone(self.tab.evaluate('localStorage.posts'))

    def test_without_recovery_the_menu_just_waits_as_before(self):
        self.open(skeleton_fixture(broken='always'))
        with patch.object(identity, 'SELECTOR_SECONDS', 2), self.assertRaisesRegex(ValueError, 'Chưa đọc được danh tính'):
            identity._selector(self.tab, timeout=2)
        self.assertEqual(len(self.loads), 1)


class PictureTests(unittest.TestCase):
    def test_the_five_newest_pictures_are_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            tab = Mock()
            tab.screenshot.side_effect = lambda path, timeout: Path(path).write_bytes(b'png')
            for day in range(1, 5):
                Path(folder, f'menu-ket-2026110{day}-000000.png').write_bytes(b'old')
            Path(folder, 'menu-ket-20261102-000001.png').write_bytes(b'old')
            Path(folder, 'menu-ket-20261102-000002.png').write_bytes(b'old')
            with patch.object(identity, '_diagnostics_dir', return_value=Path(folder)):
                identity._save_picture(tab)
            kept = sorted(path.name for path in Path(folder).glob('menu-ket-*.png'))
            self.assertEqual(len(kept), 5)
            self.assertNotIn('menu-ket-20261101-000000.png', kept)
            self.assertTrue(kept[-1].startswith('menu-ket-'+time.strftime('%Y%m%d')))

    def test_a_failing_picture_never_hides_the_real_error(self):
        tab = Mock()
        tab.screenshot.side_effect = RuntimeError('closed')
        with tempfile.TemporaryDirectory() as folder, patch.object(identity, '_diagnostics_dir', return_value=Path(folder)):
            identity._save_picture(tab)  # Must not raise.

    def test_the_picture_folder_is_inside_the_page_publishing_data(self):
        with patch.dict('os.environ', {'LOCALAPPDATA': r'C:\Data'}):
            self.assertEqual(identity._diagnostics_dir(), Path(r'C:\Data')/'TeoDubStudio'/'page-publishing'/'diagnostics')


if __name__ == '__main__':
    unittest.main()
