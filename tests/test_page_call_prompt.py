"""Facebook's "Speak With People Directly" prompt appears after Publish and blocks the post until dismissed.

Offline fixtures only. Dismissing the prompt must never click Post, Add Button or a second Publish.
"""
import unittest

from teodub.page_publish_settings import set_result_checks_enabled
from tests import test_page_browser_dom as fixture

PROMPT = '''
<section id="prompt" role="dialog" aria-label="{name}" hidden>
  <h2>{name}</h2><p>Make it easier for people to speak with you by adding a {call} button to your post.</p>
  <button onclick="window.promptDismissed=(window.promptDismissed||0)+1;document.getElementById('prompt').hidden=true">Not now</button>
  <button onclick="window.promptAdded=(window.promptAdded||0)+1">Add Button</button>
</section>
<script>function openPrompt(){{setTimeout(()=>{{document.getElementById('prompt').hidden=false}},700)}}</script>'''


class CallPromptTests(unittest.TestCase):
    setUpClass = fixture.BrowserDomTests.__dict__['setUpClass']
    tearDownClass = fixture.BrowserDomTests.__dict__['tearDownClass']
    setUp = fixture.BrowserDomTests.setUp
    draft = fixture.BrowserDomTests.draft
    prepare = fixture.BrowserDomTests.prepare
    saved = fixture.BrowserDomTests.saved
    composer_tab = fixture.BrowserDomTests.composer_tab

    def page_with_prompt(self, name='Speak With People Directly', call='“Call Now”'):
        html = fixture.fixture(publish='none')
        marker = 'onclick="window.posts=(window.posts||0)+1;"'
        assert html.count(marker) == 1
        self.html = html.replace(marker, 'onclick="window.posts=(window.posts||0)+1;openPrompt()"')+PROMPT.format(name=name, call=call)

    def static_prompt(self, name, call='“Call Now”', *, copies=1):
        tab = self.context.new_page()
        tab.set_content(''.join(PROMPT.format(name=name, call=call).replace('hidden>', '>').replace('id="prompt"', f'id="p{i}"')
                                for i in range(copies)))
        return tab

    def test_paused_publish_dismisses_the_blocking_prompt_without_extra_clicks(self):
        self.page_with_prompt()
        post = self.draft('Prompt after one publish')
        self.prepare(post)
        tab = self.composer_tab()
        set_result_checks_enabled(self.temp.name, False)
        pages_before = list(self.context.pages)

        result = self.publisher.publish(self.context, self.account['id'], self.target['id'], post['id'])

        self.assertTrue(result['verification_paused'])
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(tab.evaluate('window.posts'), 1)
        self.assertEqual(tab.evaluate('window.promptDismissed'), 1)
        self.assertIsNone(tab.evaluate('window.promptAdded'))
        self.assertEqual(self.context.pages, pages_before)
        self.assertEqual(result['external_url'], '')

    def test_prompt_is_dismissed_with_result_checks_on_as_well(self):
        self.page_with_prompt()
        post = self.draft('Prompt with checks on')
        self.prepare(post)
        tab = self.composer_tab()
        iterator = self.publisher.publish_steps(self.context, self.account['id'], self.target['id'], post['id'])
        self.assertEqual(next(iterator), {'phase': 'verifying'})
        for _ in range(80):
            if tab.evaluate('window.promptDismissed'):
                break
            try:
                next(iterator)
            except StopIteration:
                break
            tab.wait_for_timeout(100)
        self.assertEqual(tab.evaluate('window.promptDismissed'), 1)
        self.assertEqual(tab.evaluate('window.posts'), 1)
        self.assertIsNone(tab.evaluate('window.promptAdded'))
        iterator.close()

    def test_a_publish_without_any_prompt_finishes_promptly_and_unchanged(self):
        post = self.draft('No prompt appears')
        self.prepare(post)
        tab = self.composer_tab()
        set_result_checks_enabled(self.temp.name, False)
        result = self.publisher.publish(self.context, self.account['id'], self.target['id'], post['id'])
        self.assertTrue(result['verification_paused'])
        self.assertEqual(tab.evaluate('window.posts'), 1)

    def test_every_known_title_and_call_wording_is_recognized(self):
        for name in ('Speak With People Directly', 'speak with people directly', 'Talk directly to people',
                     'Trò chuyện trực tiếp với mọi người'):
            for call in ('“Call Now”', 'Call now', 'Gọi ngay'):
                with self.subTest(name=name, call=call):
                    tab = self.static_prompt(name, call)
                    self.assertTrue(self.publisher._dismiss_call_prompt(tab, None, None, None, seconds=0))
                    self.assertEqual(tab.evaluate('window.promptDismissed'), 1)
                    self.assertIsNone(tab.evaluate('window.promptAdded'))
                    tab.close()

    def test_unrelated_dialogs_and_ambiguous_prompts_are_left_alone(self):
        other = self.static_prompt('Create post')
        self.assertFalse(self.publisher._dismiss_call_prompt(other, None, None, None, seconds=0))
        self.assertIsNone(other.evaluate('window.promptDismissed'))
        wrong_text = self.static_prompt('Speak With People Directly', 'phone')
        wrong_text.evaluate("document.querySelector('p').textContent='Something else entirely.'")
        self.assertFalse(self.publisher._dismiss_call_prompt(wrong_text, None, None, None, seconds=0))
        self.assertIsNone(wrong_text.evaluate('window.promptDismissed'))
        twice = self.static_prompt('Speak With People Directly', copies=2)
        self.assertFalse(self.publisher._dismiss_call_prompt(twice, None, None, None, seconds=0))
        self.assertIsNone(twice.evaluate('window.promptDismissed'))

    def test_waiting_is_bounded_and_returns_false_when_nothing_appears(self):
        tab = self.context.new_page()
        tab.set_content('<main>nothing here</main>')
        self.assertFalse(self.publisher._dismiss_call_prompt(tab, None, None, None, seconds=1))


if __name__ == '__main__':
    unittest.main()
