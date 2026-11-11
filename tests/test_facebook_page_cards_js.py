"""Which cards of "Trang bạn quản lý" Load Page takes (owner 11/11/2026). The page script runs in a real Chrome on small local pages shaped like
what Facebook showed on the other machine (its Load Page log): in the Page identity the "Tạo bài viết" button is a link to a numeric address and
a notification count carries a dot. Skipped when Chrome cannot be started. No Facebook, no network."""
from pathlib import Path
import tempfile
import unittest

from teodub.facebook_page_discovery import SNAPSHOT_SCRIPT

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


def card(name, href, *, create_href=None, notice='<span>Thông báo</span>', notice_link=None, extra=''):
    create = (f'<a href="{create_href}"><span>Tạo bài viết</span></a>' if create_href else '<div role="button" tabindex="0">Tạo bài viết</div>')
    notice_html = f'<a href="{notice_link}">{notice}</a>' if notice_link else notice
    return (f'<div class="card"><a href="{href}" aria-label="{name}"><img alt="" width="40" height="40"></a><div><a href="{href}"><span>{name}</span></a>'
            f'<div>{notice_html}<span>Tin nhắn</span></div></div>{extra}<div>{create}<div role="button" tabindex="0">Quảng cáo</div>'
            f'<div role="button">...</div></div></div>')


def page(*cards, heading='Trang mà Tuấn Khánh quản lý'):
    return f'<!doctype html><meta charset="utf-8"><body><div role="main"><h2>{heading}</h2>' + ''.join(cards) + '</div></body>'


@unittest.skipIf(sync_playwright is None, 'playwright is not installed')
class PageCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(channel='chrome', headless=True)
        except Exception as exc:  # No Chrome on this machine.
            cls.playwright.stop()
            raise unittest.SkipTest('Chrome cannot be started: '+str(exc)[:80])
        cls.temp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.temp.cleanup()

    def read(self, html):
        file = Path(self.temp.name)/'list.html'
        file.write_text(html, encoding='utf-8')
        tab = self.browser.new_page()
        try:
            tab.goto(file.as_uri())
            return tab.evaluate(SNAPSHOT_SCRIPT)
        finally:
            tab.close()

    def names(self, html):
        return sorted(row['name'] for row in self.read(html)['items'])

    def test_plain_cards_are_taken_with_their_number_or_text_link(self):
        found = self.read(page(card('Sài Thật Nói Thật', 'https://www.facebook.com/reviewsaithatnoithat'),
                               card('Nhất Tâm Review', 'https://www.facebook.com/profile.php?id=61593699103304')))
        self.assertEqual({row['name']: row['page_id'] for row in found['items']},
                         {'Sài Thật Nói Thật': '', 'Nhất Tâm Review': '61593699103304'})
        self.assertEqual(found['heading'], 'Trang mà Tuấn Khánh quản lý')

    def test_the_log_of_the_other_machine_a_button_that_is_a_numeric_link_and_a_dot_after_the_notification_count(self):
        html = page(
            card('Sài Thật Nói Thật', 'https://www.facebook.com/reviewsaithatnoithat', create_href='https://www.facebook.com/1503669952820505',
                 notice='<span>1 thông báo</span><span>•</span>', notice_link='https://www.facebook.com/notifications/'),
            card('Kỹ Năng Câu Cá', 'https://www.facebook.com/profile.php?id=61594903914921',
                 notice='<span>1 thông báo</span><span>•</span>', notice_link='https://www.facebook.com/notifications/'),
            card('Nhất Tâm Review', 'https://www.facebook.com/profile.php?id=61593699103304'))
        self.assertEqual(self.names(html), ['Kỹ Năng Câu Cá', 'Nhất Tâm Review', 'Sài Thật Nói Thật'])

    def test_the_numeric_address_of_a_button_never_becomes_a_page(self):
        found = self.read(page(card('Sài Thật Nói Thật', 'https://www.facebook.com/reviewsaithatnoithat',
                                    create_href='https://www.facebook.com/1503669952820505')))
        self.assertEqual([row['url'] for row in found['items']], ['https://www.facebook.com/reviewsaithatnoithat'])

    def test_several_pages_in_one_block_are_not_taken(self):
        html = ('<!doctype html><meta charset="utf-8"><body><div role="main"><h2>Trang bạn quản lý</h2><div>'
                '<a href="https://www.facebook.com/profile.php?id=111"><span>Một</span></a><div role="button">Tạo bài viết</div><div role="button">Quảng cáo</div>'
                '<a href="https://www.facebook.com/profile.php?id=222"><span>Hai</span></a><div role="button">Tạo bài viết</div><div role="button">Quảng cáo</div>'
                '</div></div></body>')
        found = self.read(html)
        self.assertEqual(found['items'], [])
        self.assertTrue(found['debug'] and all('thẻ bị từ chối' in entry['outcome'] for entry in found['debug']))  # The log says so.

    def test_a_card_that_holds_two_different_pages_is_not_taken(self):
        html = page(card('Một', 'https://www.facebook.com/profile.php?id=111',
                         extra='<a href="https://www.facebook.com/profile.php?id=222"><span>Hai</span></a>'))
        self.assertEqual(self.names(html), [])


if __name__ == '__main__':
    unittest.main()
