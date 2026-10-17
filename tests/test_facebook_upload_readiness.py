"""Upload readiness regressions in a fresh, fully intercepted offline Chrome.

No user browser profile, live Facebook response or publishing action is used.
The fixture models the English 100% badge and safe-to-publish Reel message.
"""
from copy import deepcopy
import html
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_page_browser_dom as fixtures
from teodub.facebook_web_post import COMPOSER_SCRIPT


SAFE_REEL = '<div role="status">Your reel is safe to publish!</div>'
BADGE_100 = '<div id="upload-badge"><span>100%</span></div>'
PROGRESS_100 = '<div role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="100">100%</div>'


def composer_fixture(*, indicators=PROGRESS_100, caption='Approved caption',
                     controls='<button>Next</button>', videos=1, removals=1,
                     extra='', attributes=''):
    """A scoped composer; completion words in its caption are untrusted."""
    preview = ''.join('<video width="120" height="80" src="blob:offline-video"></video>'
                      for _ in range(videos))
    remove = '<button aria-label="Remove attachment from post">Remove</button>' * removals
    return ('<!doctype html><meta charset="utf-8">'
            f'<section id="composer" role="dialog" aria-label="Create post" {attributes}>'
            '<h2>Create post</h2><a href="https://www.facebook.com/profile.php?id=123">Page Demo</a>'
            f'<div id="body" role="textbox" contenteditable="true">{caption}</div>'
            f'{preview}{remove}{indicators}{controls}{extra}</section>')


def english_reel_fixture(*, release='immediate'):
    """Create post -> Edit reel -> Reel settings, with released file input."""
    source = '''<!doctype html><meta charset="utf-8">
    <button aria-label="Your profile" onclick="profile.hidden=!profile.hidden">Profile</button>
    <div id="profile" role="dialog" aria-label="Your profile" hidden>
      <h2>Choose a profile</h2><button aria-label="Page Demo, selected">Page Demo</button></div>
    <main><h1>Page Demo</h1><button onclick="composer.hidden=false">Create post</button></main>
    <section id="composer" role="dialog" aria-label="Create post" hidden>
      <h2>Create post</h2><span>Page Demo</span>
      <div id="body" role="textbox" contenteditable="true" style="white-space:pre-wrap"></div>
      <input id="uploadInput" type="file" accept="video/*"><div id="preview"></div>
      <button onclick="toEdit()">Next</button></section>
    <section id="edit" role="dialog" aria-label="Create post" hidden>
      <h2>Edit reel</h2><input aria-label="Add tags"><div id="editPreview"></div>
      <button onclick="toSettings()">Next</button></section>
    <section id="settings" role="dialog" aria-label="Create post" hidden>
      <h2>Reel settings</h2><span>Page Demo</span>
      <div id="caption" role="textbox" contenteditable="true" style="white-space:pre-wrap"></div>
      <div id="finalPreview"></div>
      <button onclick="window.posts=(window.posts||0)+1">Publish</button></section>
    <script>
      let selectedVideo;
      document.querySelector('input[type=file]').onchange=e=>{
        window.uploads=(window.uploads||0)+1;
        window.selectedFile={name:e.target.files[0].name,size:e.target.files[0].size};
        preview.innerHTML='<div id="uploadBadge"><span>100%</span></div>'+
          '<div role="status">Your reel is safe to publish!</div>';
        const remove=document.createElement('button');
        remove.setAttribute('aria-label','Remove attachment from post');preview.append(remove);
        selectedVideo=document.createElement('video');selectedVideo.width=120;selectedVideo.height=80;
        Object.defineProperties(selectedVideo,{duration:{value:30,configurable:true},videoWidth:{value:720,configurable:true},videoHeight:{value:1280,configurable:true}});
        selectedVideo.src=URL.createObjectURL(e.target.files[0]);preview.append(selectedVideo);
        // React releases its picker after upload; retain the actual observed File.
        e.target.value='';
      };
      function toEdit(){window.nexts=(window.nexts||0)+1;composer.hidden=true;edit.hidden=false;
        editPreview.append(selectedVideo);}
      function toSettings(){window.nexts=(window.nexts||0)+1;edit.hidden=true;settings.hidden=false;
        caption.innerText=body.innerText;finalPreview.append(selectedVideo);}
    </script>'''
    if release in {'async', 'pending'}:
        source = source.replace(
            "preview.innerHTML='<div id=\"uploadBadge\"><span>100%</span></div>'+\n"
            "          '<div role=\"status\">Your reel is safe to publish!</div>';",
            "preview.innerHTML='<div role=\"progressbar\" aria-valuemin=\"0\" aria-valuemax=\"100\" aria-valuenow=\"10\">10%</div>';\n"
            "        composer.querySelector(':scope > button').disabled=true;")
        source = source.replace("e.target.value='';", "const picker=e.target;")
        if release == 'async':
            source = source.replace("const picker=e.target;", """const picker=e.target;
        setTimeout(()=>{
          picker.value='';
          const progress=preview.querySelector('[role=progressbar]');
          progress.setAttribute('aria-valuenow','100');progress.textContent='100%';
          preview.insertAdjacentHTML('beforeend','<div role="status">Your reel is safe to publish!</div>');
          composer.querySelector(':scope > button').disabled=false;
        },1000);""")
    return source


class FacebookUploadReadinessTests(unittest.TestCase):
    # Reuse isolation helpers, not the existing test methods or test inheritance.
    setUpClass = fixtures.BrowserDomTests.__dict__['setUpClass']
    tearDownClass = fixtures.BrowserDomTests.__dict__['tearDownClass']
    setUp = fixtures.BrowserDomTests.setUp
    draft = fixtures.BrowserDomTests.draft
    prepare = fixtures.BrowserDomTests.prepare
    saved = fixtures.BrowserDomTests.saved
    composer_tab = fixtures.BrowserDomTests.composer_tab

    def observe(self, **kwargs):
        tab = self.context.new_page()
        tab.set_content(composer_fixture(**kwargs))
        return tab.locator('#composer').evaluate(COMPOSER_SCRIPT)

    def assert_not_complete(self, **kwargs):
        data = self.observe(**kwargs)
        self.assertFalse(data['upload_complete'], data)
        return data

    def test_completed_progressbar_is_idle_with_unique_sourced_removable_video_and_next(self):
        for controls in ('<button>Next</button>', '<button>Post</button>', '<button>Publish</button>'):
            with self.subTest(controls=controls):
                data = self.observe(controls=controls)
                self.assertTrue(data['upload_complete'], data)
                self.assertFalse(data['busy'], data)
                self.assertEqual(data['preview_count'], 1)
                self.assertEqual(len(data['videos']), 1)

    def test_screenshot_badge_and_safe_reel_message_complete_without_legacy_marker(self):
        data = self.observe(indicators=BADGE_100 + SAFE_REEL)
        self.assertTrue(data['upload_complete'], data)
        self.assertFalse(data['busy'], data)

    def test_safe_reel_alert_and_unrelated_information_alert_do_not_block_completion(self):
        for indicators, extra in (
            (BADGE_100 + '<div role="alert">Your reel is safe to publish!</div>', ''),
            (BADGE_100 + SAFE_REEL, '<div role="alert">Tip: add a caption</div>'),
        ):
            with self.subTest(indicators=indicators, extra=extra):
                data = self.observe(indicators=indicators, extra=extra)
                self.assertTrue(data['upload_complete'], data)
                self.assertFalse(data['upload_error'], data)
                self.assertFalse(data['busy'], data)

    def test_completion_requires_badge_and_safe_reel_pair(self):
        for indicators in (BADGE_100, SAFE_REEL, '<span>100%</span>',
                           '<span>100% finished</span>' + SAFE_REEL,
                           '<span>99%</span>' + SAFE_REEL):
            with self.subTest(indicators=indicators):
                self.assert_not_complete(indicators=indicators)

    def test_completion_text_in_caption_cannot_substitute_for_upload_evidence(self):
        cases = (
            dict(indicators='', caption='<span>100%</span>' + SAFE_REEL),
            dict(indicators=SAFE_REEL, caption='<span>100%</span>'),
            dict(indicators=BADGE_100, caption=SAFE_REEL),
            dict(indicators='', caption='100% Your reel is safe to publish!'),
        )
        for case in cases:
            with self.subTest(case=case):
                self.assert_not_complete(**case)

    def test_caption_wrapper_cannot_supply_badge_or_safe_reel_status(self):
        for caption, indicators in (
            ('100%', SAFE_REEL),
            ('Your reel is safe to publish!', BADGE_100),
        ):
            with self.subTest(caption=caption):
                tab = self.context.new_page()
                source = composer_fixture(caption=caption, indicators=indicators)
                source = source.replace('<div id="body"', '<div id="caption-wrapper"><div id="body"')
                source = source.replace('</div><video', '</div></div><video', 1)
                tab.set_content(source)
                data = tab.locator('#composer').evaluate(COMPOSER_SCRIPT)
                self.assertFalse(data['upload_complete'], data)

    def test_hidden_and_inert_completion_indicators_are_not_evidence(self):
        for indicators in (
            '<div hidden>' + PROGRESS_100 + '</div>',
            '<div style="display:none">' + BADGE_100 + SAFE_REEL + '</div>',
            '<div style="visibility:hidden">' + BADGE_100 + SAFE_REEL + '</div>',
            '<div aria-hidden="true">' + BADGE_100 + SAFE_REEL + '</div>',
            '<div inert>' + BADGE_100 + SAFE_REEL + '</div>',
        ):
            with self.subTest(indicators=indicators):
                self.assert_not_complete(indicators=indicators)

    def test_badges_in_other_or_hidden_composers_do_not_complete_current_upload(self):
        tab = self.context.new_page()
        tab.set_content(composer_fixture(indicators='') +
                        '<section role="dialog">' + BADGE_100 + SAFE_REEL + '</section>' +
                        '<section role="dialog" hidden>' + PROGRESS_100 + '</section>')
        data = tab.locator('#composer').evaluate(COMPOSER_SCRIPT)
        self.assertFalse(data['upload_complete'], data)

    def test_incomplete_or_indeterminate_progress_vetoes_complete_badge(self):
        for progress in (
            '<div role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="99">99%</div>',
            '<div role="progressbar">Uploading</div>',
            '<div role="progressbar" aria-valuemax="100">100%</div>',
            '<div role="progressbar" aria-valuemax="100" aria-valuenow="NaN">100%</div>',
            '<div role="progressbar" aria-valuemax="100" aria-valuenow="101">100%</div>',
            '<div role="progressbar" aria-valuemax="100" aria-valuenow="99.9">100%</div>',
            '<div role="progressbar" aria-valuemax="100" aria-valuenow="">100%</div>',
            '<div role="progressbar" aria-valuemax="100" aria-valuenow="Infinity">100%</div>',
            '<div role="progressbar" aria-valuemin="100" aria-valuemax="100" aria-valuenow="100">100%</div>',
            '<div role="progressbar" aria-valuemax="NaN" aria-valuenow="100">100%</div>',
        ):
            with self.subTest(progress=progress):
                data = self.assert_not_complete(indicators=progress + BADGE_100 + SAFE_REEL)
                self.assertTrue(data['busy'], data)

    def test_completed_progress_cannot_override_another_incomplete_indicator(self):
        incomplete = '<div role="progressbar" aria-valuemax="100" aria-valuenow="20">20%</div>'
        data = self.assert_not_complete(indicators=PROGRESS_100 + incomplete)
        self.assertTrue(data['busy'], data)

    def test_aria_busy_on_composer_or_descendant_vetoes_completion(self):
        for case in (dict(attributes='aria-busy="true"'),
                     dict(extra='<div aria-busy="true">Processing video</div>')):
            with self.subTest(case=case):
                data = self.assert_not_complete(**case)
                self.assertTrue(data['busy'], data)

    def test_visible_upload_error_vetoes_complete_progress_and_badge(self):
        for message in ('Upload failed', 'Video upload failed', 'Error uploading video',
                        'Could not upload video', 'Video processing failed'):
            with self.subTest(message=message):
                self.assert_not_complete(indicators=PROGRESS_100 + BADGE_100 + SAFE_REEL,
                                         extra=f'<div role="alert">{message}</div>')

    def test_completion_does_not_guess_missing_duplicate_or_disabled_forward_controls(self):
        for controls in ('', '<button disabled>Next</button>', '<button aria-disabled="true">Next</button>',
                         '<button>Next</button><button>Next</button>', '<button>Next</button><button>Post</button>'):
            with self.subTest(controls=controls):
                self.assert_not_complete(controls=controls)

    def test_completion_does_not_guess_media_without_unique_sourced_removable_preview(self):
        for case in (dict(videos=0), dict(videos=2), dict(removals=0), dict(removals=2)):
            with self.subTest(case=case):
                self.assert_not_complete(**case)
        tab = self.context.new_page()
        tab.set_content(composer_fixture())
        tab.locator('video').evaluate('e => e.removeAttribute("src")')
        self.assertFalse(tab.locator('#composer').evaluate(COMPOSER_SCRIPT)['upload_complete'])

    def test_legacy_marker_path_still_completes_without_new_badge_shape(self):
        for marker in ('Upload complete', 'Uploaded', 'Đã tải lên xong'):
            with self.subTest(marker=marker):
                data = self.observe(indicators=f'<img alt="{html.escape(marker)}" width="16" height="16">',
                                    controls='<button>Next</button>')
                self.assertTrue(data['upload_complete'], data)
                self.assertFalse(data['busy'], data)

    def test_english_reel_released_picker_prepares_once_without_publishing(self):
        self.html = english_reel_fixture()
        path = Path(self.temp.name).resolve() / 'offline reel.mp4'
        path.write_bytes(b'Offline video fixture; never sent outside the intercepted context.')
        caption = 'Nội dung đã duyệt.\n\n#MinhChau'
        post = self.draft(caption, paths=[path])
        result = self.prepare(post)
        tab = self.composer_tab()
        self.assertEqual(result['state'], 'review')
        self.assertEqual(result['browser_review']['body'], caption)
        self.assertEqual(result['browser_review']['composer']['layout'], 'reel_settings')
        self.assertEqual(result['browser_review']['identity']['page_id'], '123')
        self.assertEqual(result['browser_review']['observed_files'][0]['name'], path.name)
        self.assertEqual(result['browser_review']['observed_files'][0]['size'], path.stat().st_size)
        self.assertEqual(tab.evaluate('uploadInput.files.length'), 0)
        self.assertEqual(tab.evaluate('window.uploads'), 1)
        self.assertEqual(tab.evaluate('window.nexts'), 2)
        self.assertIsNone(tab.evaluate('window.posts'))
        self.assertEqual(self.unrelated.locator('textarea').input_value(), 'keep this form')
        count = len(self.context.pages)
        again = self.prepare(post)
        self.assertEqual(again['state'], 'review')
        self.assertEqual(len(self.context.pages), count)
        self.assertEqual(tab.evaluate('window.uploads'), 1)
        self.assertEqual(tab.evaluate('window.nexts'), 2)
        self.assertIsNone(tab.evaluate('window.posts'))
        self.assertTrue(self.requests)
        self.assertTrue(all(url == self.target['url'] for url in self.requests), self.requests)

    def test_released_picker_retention_requires_same_body_actor_and_video(self):
        self.html = composer_fixture(indicators=BADGE_100 + SAFE_REEL)
        tab = self.context.new_page()
        tab.goto(self.target['url'])
        _, observed = self.publisher._observe(tab, self.target)
        retained = deepcopy(observed)
        retained['files'] = [dict(name='retained.mp4', size=12, type='video/mp4', last_modified=123)]
        _, accepted = self.publisher._observe_upload_stage(tab, self.target, retained)
        self.assertEqual(accepted['files'], retained['files'])
        self.assertTrue(accepted['input_released_after_upload'])
        mutations = (
            lambda data: data.update(body='Changed approved caption'),
            lambda data: data['identity'].update(page_id='456'),
            lambda data: data['videos'][0].update(src='blob:different-video'),
            lambda data: data.update(preview_count=2),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                changed = deepcopy(retained)
                mutate(changed)
                _, actual = self.publisher._observe_upload_stage(tab, self.target, changed)
                self.assertEqual(actual['files'], [])
                self.assertNotIn('input_released_after_upload', actual)

    def test_async_released_picker_completes_with_retained_final_progressbar(self):
        self.html = english_reel_fixture(release='async')
        path = Path(self.temp.name).resolve() / 'async reel.mp4'
        path.write_bytes(b'Offline asynchronous picker fixture.')
        post = self.draft('Giữ nguyên lời đã duyệt.', paths=[path])
        observations = []
        original = self.publisher._observe_upload_stage
        def observed(*args, **kwargs):
            dialog, data = original(*args, **kwargs)
            observations.append(deepcopy(data))
            return dialog, data
        with patch.object(self.publisher, '_observe_upload_stage', side_effect=observed), \
                patch('teodub.facebook_web_post.UPLOAD_WAIT_SECONDS', 3):
            result = self.prepare(post)
        self.assertEqual(result['state'], 'review')
        self.assertTrue(any(data['busy'] and data['files'] for data in observations))
        self.assertTrue(any(data.get('input_released_after_upload') for data in observations))
        self.assertEqual(result['browser_review']['observed_files'][0]['name'], path.name)
        tab = self.composer_tab()
        self.assertEqual(tab.evaluate('uploadInput.files.length'), 0)
        self.assertEqual(tab.locator('#preview [role=progressbar]').get_attribute('aria-valuenow'), '100')
        self.assertEqual(tab.evaluate('window.uploads'), 1)
        self.assertEqual(tab.evaluate('window.nexts'), 2)
        self.assertIsNone(tab.evaluate('window.posts'))

    def test_replaced_preview_after_picker_release_cannot_rebase_selected_file(self):
        self.html = english_reel_fixture(release='pending')
        path = Path(self.temp.name).resolve() / 'original reel.mp4'
        path.write_bytes(b'Original offline selected file identity.')
        post = self.draft('Approved caption is unchanged.', paths=[path])
        original = self.publisher._observe_upload_stage
        observations = []
        def replaced(tab, *args, **kwargs):
            dialog, data = original(tab, *args, **kwargs)
            observations.append(deepcopy(data))
            if len(observations) == 1:
                # The first real observation saw the selected original File/video.
                # Simulate a React preview replacement between later observations.
                tab.evaluate('''() => {
                    uploadInput.value='';
                    preview.querySelector('video').src='blob:replacement-video';
                    const progress=preview.querySelector('[role=progressbar]');
                    progress.setAttribute('aria-valuenow','100');progress.textContent='100%';
                    preview.insertAdjacentHTML('beforeend','<div role="status">Your reel is safe to publish!</div>');
                    composer.querySelector(':scope > button').disabled=false;
                }''')
            return dialog, data
        with patch.object(self.publisher, '_observe_upload_stage', side_effect=replaced), \
                patch('teodub.facebook_web_post.UPLOAD_WAIT_SECONDS', 1):
            with self.assertRaisesRegex(ValueError, 'chưa đủ bằng chứng'):
                self.prepare(post)
        self.assertGreaterEqual(len(observations), 3)
        self.assertTrue(observations[0]['files'])
        self.assertTrue(all(not data['files'] for data in observations[1:]))
        self.assertEqual(self.saved(post)['state'], 'unknown')
        tab = self.composer_tab()
        self.assertEqual(tab.evaluate('window.uploads'), 1)
        self.assertIsNone(tab.evaluate('window.nexts'))
        self.assertIsNone(tab.evaluate('window.posts'))
        count = len(self.context.pages)
        with self.assertRaises(ValueError):
            self.prepare(post)
        self.assertEqual(len(self.context.pages), count)
        self.assertEqual(tab.evaluate('window.uploads'), 1)


if __name__ == '__main__':
    unittest.main()
