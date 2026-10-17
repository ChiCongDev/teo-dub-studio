"""Observed attachment names in isolated, fully intercepted Playwright fixtures.

These regressions use a fresh browser and temporary activity stores. They never
access the user's browser profile or transmit video to Facebook.
"""
import html
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_page_browser_dom as fixtures
from tests.test_facebook_upload_readiness import (
    BADGE_100, SAFE_REEL, composer_fixture, english_reel_fixture,
)
from teodub.facebook_web_post import COMPOSER_SCRIPT


REMOVE_NAMES = ('Remove post attachment', 'Remove attachment from post')


def named_button(name='Remove post attachment', *, attributes='', text='X'):
    return f'<button aria-label="{html.escape(name, quote=True)}" {attributes}>{text}</button>'


def upload_fixture(*, name_source='aria-label', release='immediate', receipt=False):
    """Same upload/wizard fixture with the observed attachment naming variants."""
    source = english_reel_fixture(release=release)
    assignment = "remove.setAttribute('aria-label','Remove attachment from post');"
    if name_source == 'aria-label':
        replacement = "remove.setAttribute('aria-label','Remove post attachment');"
    elif name_source == 'innerText':
        replacement = "remove.textContent='Remove post attachment';"
    elif name_source == 'aria-labelledby':
        replacement = """remove.setAttribute('aria-labelledby','removal-name');
          preview.insertAdjacentHTML('beforeend','<span id="removal-name" hidden>Remove post attachment</span>');"""
    elif name_source == 'split-labelledby':
        replacement = """remove.setAttribute('aria-labelledby','removal-action removal-object');
          preview.insertAdjacentHTML('beforeend','<span id="removal-action" hidden>Remove</span><span id="removal-object" hidden>post attachment</span>');"""
    else:
        raise ValueError(name_source)
    assert assignment in source
    source = source.replace(assignment, replacement)
    if receipt:
        source = source.replace(
            '<button onclick="window.posts=(window.posts||0)+1">Publish</button>',
            '<button onclick="publish()">Publish</button>')
        source = source.replace('</script>', """
          function publish(){
            window.posts=(window.posts||0)+1;settings.hidden=true;
            const status=document.createElement('div');status.setAttribute('role','status');
            status.innerHTML='Your post was published. <a href="https://www.facebook.com/123/videos/999">View video</a>';
            document.body.append(status);
          }
        </script>""")
    return source


class FacebookAttachmentNamesTests(unittest.TestCase):
    setUpClass = fixtures.BrowserDomTests.__dict__['setUpClass']
    tearDownClass = fixtures.BrowserDomTests.__dict__['tearDownClass']
    setUp = fixtures.BrowserDomTests.setUp
    draft = fixtures.BrowserDomTests.draft
    prepare = fixtures.BrowserDomTests.prepare
    publish = fixtures.BrowserDomTests.publish
    saved = fixtures.BrowserDomTests.saved
    composer_tab = fixtures.BrowserDomTests.composer_tab

    def observe(self, removal, *, indicators=BADGE_100 + SAFE_REEL,
                caption='Approved caption', controls='<button>Next</button>', extra=''):
        tab = self.context.new_page()
        source = composer_fixture(indicators=indicators, caption=caption,
                                  controls=controls, removals=0, extra=extra)
        source = source.replace('<video ', removal + '<video ', 1)
        tab.set_content(source)
        return tab, tab.locator('#composer').evaluate(COMPOSER_SCRIPT)

    def assert_ready(self, data):
        self.assertEqual(data['preview_count'], 1, data)
        self.assertTrue(data['upload_complete'], data)
        self.assertFalse(data['busy'], data)

    def assert_missing(self, data):
        self.assertEqual(data['preview_count'], 0, data)
        self.assertFalse(data['upload_complete'], data)

    def make_video(self, suffix=''):
        path = Path(self.temp.name).resolve() / f'offline attachment{suffix}.mp4'
        path.write_bytes(b'Offline attachment regression; never uploaded to a live website.')
        return path

    def test_observed_name_from_direct_aria_label_and_visible_inner_text(self):
        for removal in (named_button(), '<button>Remove post attachment</button>'):
            with self.subTest(removal=removal):
                tab, data = self.observe(removal)
                self.assertEqual(tab.get_by_role('button', name=REMOVE_NAMES[0], exact=True).count(), 1)
                self.assert_ready(data)

    def test_direct_aria_label_allows_a_decorative_hidden_icon(self):
        for decoration in ('<span aria-hidden="true">X</span>',
                           '<svg aria-hidden="true" width="12" height="12"><path d="M0 0 L12 12"/></svg>'):
            with self.subTest(decoration=decoration):
                tab, data = self.observe(named_button(text=decoration))
                self.assertEqual(tab.get_by_role('button', name=REMOVE_NAMES[0], exact=True).count(), 1)
                self.assert_ready(data)

    def test_existing_attachment_names_remain_supported(self):
        for name in ('Remove attachment from post', 'Remove video', 'Delete video',
                     'Remove photo', 'Delete image', 'Remove video 1',
                     'Gỡ file đính kèm trong bài viết', 'Gỡ video', 'Xóa ảnh 1'):
            with self.subTest(name=name):
                _, data = self.observe(named_button(name))
                self.assert_ready(data)

    def test_title_name_is_used_only_when_other_name_sources_are_empty(self):
        tab, data = self.observe('<button title="Remove post attachment"></button>')
        self.assertEqual(tab.get_by_role('button', name=REMOVE_NAMES[0], exact=True).count(), 1)
        self.assert_ready(data)
        for removal in (
            '<button title="Remove post attachment" aria-label="Close"></button>',
            '<button title="Remove post attachment">Close</button>',
            '<button title="Remove post attachment" aria-labelledby="missing"></button>',
        ):
            with self.subTest(removal=removal):
                _, data = self.observe(removal)
                self.assert_missing(data)

    def test_aria_labelledby_single_and_split_reference_names(self):
        for labels, refs in (
            ('<span id="remove-name">Remove post attachment</span>', 'remove-name'),
            ('<span id="action">Remove</span><span id="object">post attachment</span>', 'action object'),
            ('<span id="action"><b>Remove</b></span><span id="object">post <i>attachment</i></span>',
             'action object'),
            ('<span id="action">Remove</span><span id="object">post attachment</span>', '  action  object  '),
        ):
            with self.subTest(labels=labels, refs=refs):
                tab, data = self.observe(labels + f'<button aria-labelledby="{refs}">X</button>')
                self.assertEqual(tab.get_by_role('button', name=REMOVE_NAMES[0], exact=True).count(), 1)
                self.assert_ready(data)

    def test_hidden_referenced_labels_can_supply_actual_accessible_name(self):
        for attributes in ('hidden', 'style="display:none"', 'style="visibility:hidden"'):
            for content in ('Remove post attachment', 'Remove post<span hidden> attachment</span>'):
                with self.subTest(attributes=attributes, content=content):
                    tab, data = self.observe(f'<span id="remove-name" {attributes}>{content}</span>'
                                             '<button aria-labelledby="remove-name">X</button>')
                    self.assertEqual(tab.get_by_role('button', name=REMOVE_NAMES[0], exact=True).count(), 1)
                    self.assert_ready(data)

    def test_labelledby_precedence_does_not_accept_a_conflicting_supported_aria_label(self):
        _, data = self.observe('<span id="button-name">Close unrelated panel</span>'
                               '<button aria-labelledby="button-name" aria-label="Remove post attachment">X</button>')
        self.assert_missing(data)
        _, data = self.observe('<span id="button-name">Remove post attachment</span>'
                               '<button aria-labelledby="button-name" aria-label="Close unrelated panel">X</button>')
        self.assert_ready(data)

    def test_invalid_labelledby_never_falls_back_to_aria_label_or_visible_text(self):
        for refs, labels in (
            ('does-not-exist', ''),
            ('remove-name does-not-exist', '<span id="remove-name">Remove post attachment</span>'),
            ('', ''),
            ('  ', ''),
        ):
            with self.subTest(refs=refs):
                _, data = self.observe(labels + f'<button aria-labelledby="{refs}" '
                                       'aria-label="Remove post attachment">Remove post attachment</button>')
                self.assert_missing(data)

    def test_reference_order_and_complete_name_must_match_exactly(self):
        labels = '<span id="action">Remove</span><span id="object">post attachment</span>'
        for refs in ('object action', 'action', 'object', 'action object action'):
            with self.subTest(refs=refs):
                _, data = self.observe(labels + f'<button aria-labelledby="{refs}">X</button>')
                self.assert_missing(data)

    def test_caption_cannot_supply_a_referenced_attachment_name(self):
        _, data = self.observe('<button aria-labelledby="caption-remove">X</button>',
                               caption='<span id="caption-remove">Remove post attachment</span>')
        self.assert_missing(data)
        _, data = self.observe('<div id="mixed-remove">Remove post attachment'
                               '<div role="textbox" contenteditable="true">editor</div></div>'
                               '<button aria-labelledby="mixed-remove">X</button>')
        self.assert_missing(data)

    def test_labels_from_other_dialogs_or_document_regions_are_not_attachment_evidence(self):
        for extra in (
            '<section role="dialog" aria-label="Another dialog"><span id="foreign-name">Remove post attachment</span></section>',
            '<span id="foreign-name">Remove post attachment</span>',
        ):
            with self.subTest(extra=extra):
                tab, _ = self.observe('<button aria-labelledby="foreign-name">X</button>')
                tab.locator('body').evaluate('(body, extra) => body.insertAdjacentHTML("beforeend", extra)', extra)
                self.assert_missing(tab.locator('#composer').evaluate(COMPOSER_SCRIPT))

    def test_nested_foreign_dialog_labels_and_controls_are_not_current_preview(self):
        for removal in (
            '<section role="dialog" aria-label="Other composer">' + named_button() + '</section>',
            '<section role="dialog" aria-label="Other composer"><span id="nested-name">Remove post attachment</span></section>'
            '<button aria-labelledby="nested-name">X</button>',
        ):
            with self.subTest(removal=removal):
                _, data = self.observe(removal)
                self.assert_missing(data)

    def test_aria_hidden_and_inert_referenced_names_are_excluded(self):
        for wrapper in (
            '<span id="remove-name" aria-hidden="true">Remove post attachment</span>',
            '<span id="remove-name" inert>Remove post attachment</span>',
            '<div aria-hidden="true"><span id="remove-name">Remove post attachment</span></div>',
            '<div inert><span id="remove-name">Remove post attachment</span></div>',
        ):
            with self.subTest(wrapper=wrapper):
                _, data = self.observe(wrapper + '<button aria-labelledby="remove-name">X</button>')
                self.assert_missing(data)

    def test_referenced_name_cannot_import_hidden_inert_or_foreign_dialog_descendants(self):
        for label in (
            '<span id="remove-name">Remove post<span aria-hidden="true"> attachment</span></span>',
            '<span id="remove-name">Remove post<span inert> attachment</span></span>',
            '<div id="remove-name"><section role="dialog" aria-label="Other composer">'
            '<span>Remove post attachment</span></section></div>',
        ):
            with self.subTest(label=label):
                _, data = self.observe(label + '<button aria-labelledby="remove-name">X</button>')
                self.assert_missing(data)
        # A visible referenced label cannot borrow words from visually hidden
        # children. Chromium names this button "Remove post", not the accepted
        # complete attachment action; the script must also decline it.
        for attributes in ('hidden', 'style="display:none"', 'style="visibility:hidden"'):
            with self.subTest(hidden_child_attributes=attributes):
                tab, data = self.observe('<span id="remove-name">Remove post'
                                         f'<span {attributes}> attachment</span></span>'
                                         '<button aria-labelledby="remove-name">X</button>')
                self.assertEqual(tab.get_by_role('button', name=REMOVE_NAMES[0], exact=True).count(), 0)
                self.assertEqual(tab.get_by_role('button', name='Remove post', exact=True).count(), 1)
                self.assert_missing(data)

    def test_missing_hidden_and_inert_attachment_controls_are_not_previews(self):
        for removal in ('', named_button(attributes='hidden'),
                        named_button(attributes='style="display:none"'),
                        named_button(attributes='style="visibility:hidden"'),
                        named_button(attributes='aria-hidden="true"'),
                        named_button(attributes='inert'),
                        '<div aria-hidden="true">' + named_button() + '</div>',
                        '<div inert>' + named_button() + '</div>'):
            with self.subTest(removal=removal):
                _, data = self.observe(removal)
                self.assert_missing(data)

    def test_attachment_controls_inside_caption_or_containing_an_editor_are_excluded(self):
        _, data = self.observe('', caption=named_button())
        self.assert_missing(data)
        _, data = self.observe('<div role="button" aria-label="Remove post attachment">'
                               '<span role="textbox" contenteditable="true">caption</span></div>')
        self.assert_missing(data)

    def test_duplicate_accepted_controls_do_not_prove_a_single_upload(self):
        for removal in (named_button() + named_button(),
                        named_button() + named_button('Remove attachment from post'),
                        named_button() + '<span id="remove-name">Remove post attachment</span>'
                        '<button aria-labelledby="remove-name">X</button>'):
            with self.subTest(removal=removal):
                _, data = self.observe(removal)
                self.assertEqual(data['preview_count'], 2, data)
                self.assertFalse(data['upload_complete'], data)

    def test_unknown_or_partial_attachment_names_are_not_guessed(self):
        for name in ('Remove', 'Remove attachment', 'Remove media', 'Remove post attachments',
                     'Remove post attachment now', 'Do not Remove post attachment', 'Close'):
            with self.subTest(name=name):
                _, data = self.observe(named_button(name))
                self.assert_missing(data)

    def test_supported_name_still_requires_unique_enabled_next_and_complete_indicators(self):
        for kwargs in (
            dict(controls=''), dict(controls='<button disabled>Next</button>'),
            dict(controls='<button aria-disabled="true">Next</button>'),
            dict(controls='<button>Next</button><button>Next</button>'),
            dict(indicators=BADGE_100), dict(indicators=SAFE_REEL),
            dict(indicators=BADGE_100 + SAFE_REEL + '<div role="progressbar" aria-valuenow="99">99%</div>'),
            dict(extra='<div aria-busy="true">Processing</div>'),
            dict(extra='<div role="alert">Video upload failed</div>'),
        ):
            with self.subTest(kwargs=kwargs):
                _, data = self.observe(named_button(), **kwargs)
                self.assertEqual(data['preview_count'], 1, data)
                self.assertFalse(data['upload_complete'], data)

    def test_immediate_released_picker_prepares_exact_names_once_and_is_idempotent(self):
        for name_source in ('aria-label', 'innerText', 'aria-labelledby', 'split-labelledby'):
            with self.subTest(name_source=name_source):
                self.html = upload_fixture(name_source=name_source)
                caption = 'Nội dung đã duyệt.\n\n#MinhChau'
                path = self.make_video(name_source)
                post = self.draft(caption, paths=[path])
                result = self.prepare(post)
                tab = self.composer_tab()
                self.assertEqual(result['state'], 'review')
                self.assertEqual(result['browser_review']['body'], caption)
                self.assertEqual(result['browser_review']['identity']['page_id'], '123')
                self.assertEqual(result['browser_review']['composer']['preview_count'], 1)
                self.assertEqual(result['browser_review']['observed_files'][0]['name'], path.name)
                self.assertEqual(tab.evaluate('uploadInput.files.length'), 0)
                self.assertEqual(tab.evaluate('window.uploads'), 1)
                self.assertEqual(tab.evaluate('window.nexts'), 2)
                self.assertIsNone(tab.evaluate('window.posts'))
                count = len(self.context.pages)
                self.assertEqual(self.prepare(post)['state'], 'review')
                self.assertEqual(len(self.context.pages), count)
                self.assertEqual(tab.evaluate('window.uploads'), 1)
                self.assertEqual(tab.evaluate('window.nexts'), 2)
                self.assertIsNone(tab.evaluate('window.posts'))
                self.assertEqual(self.unrelated.locator('textarea').input_value(), 'keep this form')
        self.assertTrue(all(url == self.target['url'] for url in self.requests), self.requests)

    def test_async_released_picker_preserves_initial_file_and_single_upload(self):
        for name_source in ('aria-label', 'aria-labelledby', 'split-labelledby'):
            with self.subTest(name_source=name_source):
                self.html = upload_fixture(name_source=name_source, release='async')
                path = self.make_video('async-' + name_source)
                post = self.draft('Approved async caption', paths=[path])
                result = self.prepare(post)
                tab = self.composer_tab()
                self.assertEqual(result['state'], 'review')
                self.assertEqual(result['browser_review']['media'][0]['path'], str(path))
                self.assertEqual(result['browser_review']['observed_files'][0]['size'], path.stat().st_size)
                self.assertEqual(tab.evaluate('uploadInput.files.length'), 0)
                self.assertEqual(tab.evaluate('window.uploads'), 1)
                self.assertEqual(tab.evaluate('window.nexts'), 2)
                self.assertIsNone(tab.evaluate('window.posts'))
                self.assertEqual(self.prepare(post)['state'], 'review')
                self.assertEqual(tab.evaluate('window.uploads'), 1)
        self.assertTrue(all(url == self.target['url'] for url in self.requests), self.requests)

    def test_full_fixture_publish_has_one_upload_one_publication_and_no_repeat_click(self):
        verified_urls = set()
        for index, name_source in enumerate(('aria-label', 'split-labelledby')):
            with self.subTest(name_source=name_source):
                url = 'https://www.facebook.com/123/videos/'+str(999+index)
                verified_urls.add(url)
                self.html = upload_fixture(name_source=name_source, receipt=True).replace(
                    'https://www.facebook.com/123/videos/999', url)
                post = self.draft('Approved complete publication '+name_source,
                    paths=[self.make_video('publish-' + name_source)])
                self.prepare(post)
                tab = self.composer_tab()
                self.assertIsNone(tab.evaluate('window.posts'))
                with patch('teodub.facebook_web_post.PUBLISH_RESULT_SECONDS', 3):
                    published = self.publish(post)
                self.assertEqual(published['state'], 'published')
                self.assertEqual(published['external_url'], url)
                self.assertEqual(tab.evaluate('window.uploads'), 1)
                self.assertEqual(tab.evaluate('window.nexts'), 2)
                self.assertEqual(tab.evaluate('window.posts'), 1)
                with self.assertRaises(ValueError):
                    self.publish(post)
                self.assertEqual(tab.evaluate('window.posts'), 1)
                self.assertEqual(self.unrelated.locator('textarea').input_value(), 'keep this form')
        # Publication independently re-verifies the Page actor and reads each
        # actual result player; every destination is intercepted locally.
        self.assertTrue(verified_urls.issubset(set(self.requests)), self.requests)
        self.assertTrue(all(url in {self.target['url'], 'https://www.facebook.com/'} | verified_urls
                            for url in self.requests), self.requests)


if __name__ == '__main__':
    unittest.main()
