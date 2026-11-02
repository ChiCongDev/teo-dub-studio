"""30/10/2026: "Caption mẫu để học phong cách" — captions of another Page pasted by the owner, sent to the AI so it copies the
voice, not the words. A Page without samples must stay exactly as before. Synthetic stores and files only; no AI call."""
import json
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from teodub import page_ai
from teodub.page_ai import CAPTION_STYLE, MAX_STYLE_SAMPLES, STYLE_SAMPLES_RULE, make_prompt, validate_config
from teodub.page_ai_ui import PageAIPanel
from tests import test_page_ai as fixtures

SAMPLES = ('Cua dừa Papua kẹp gãy ngón tay người. Nghe mà ớn!\n\n'
           'Anh bạn đặt bẫy thỏ xong, bụng đói meo: "Thôi ra thị trấn vậy." 🐇\n\n'
           'Ăn con sâu này mà mặt không đổi sắc, ai dám thử?')


def config(**values):
    base = dict(campaigns=['a'*32], model='gpt-5.6-luna', effort='low', days=7, videos_per_day=2, texts_per_week=3,
                interval_days=7, goal='Nội dung hữu ích', auto_publish=False)
    base.update(values)
    return base


class StyleConfigTests(unittest.TestCase):
    def test_a_config_without_samples_is_valid_and_unchanged(self):
        self.assertEqual(validate_config(config()), config())

    def test_samples_up_to_the_limit_are_valid(self):
        for text in (SAMPLES, 'x'*MAX_STYLE_SAMPLES, 'Dòng một\n\tDòng hai'):
            with self.subTest(size=len(text)):
                self.assertEqual(validate_config(config(style_samples=text))['style_samples'], text)

    def test_empty_too_long_wrong_type_or_control_characters_are_refused(self):
        for value in ('', '   \n ', 'x'*(MAX_STYLE_SAMPLES+1), 123, ['a'], None, 'có\x00ký tự', 'có\x1bký tự'):
            with self.subTest(value=str(value)[:20]), self.assertRaises(ValueError):
                validate_config(config(style_samples=value))

    def test_the_limit_message_says_the_number(self):
        with self.assertRaisesRegex(ValueError, '8.000 ký tự'):
            validate_config(config(style_samples='x'*(MAX_STYLE_SAMPLES+1)))


class StylePromptTests(unittest.TestCase):
    setUp = fixtures.PageAITests.setUp
    saved = fixtures.PageAITests.saved
    task = fixtures.PageAITests.task
    answer = fixtures.PageAITests.answer

    def test_a_page_without_samples_gets_the_same_context_and_prompt_as_before(self):
        task = self.task()
        context = self.store.context(task, '')
        self.assertNotIn('style_samples', context)
        prompt = make_prompt(task, context)
        self.assertNotIn(STYLE_SAMPLES_RULE, prompt)
        self.assertNotIn('style_samples', prompt)
        self.assertIn(CAPTION_STYLE, prompt)

    def test_samples_are_saved_with_the_policy_and_reach_the_context_and_the_prompt(self):
        task = self.task(style_samples=SAMPLES)
        self.assertEqual(self.store.policy(self.account, self.page)['config']['style_samples'], SAMPLES)
        context = self.store.context(task, '')
        self.assertEqual(context['style_samples'], SAMPLES)
        prompt = make_prompt(task, context)
        self.assertIn(STYLE_SAMPLES_RULE, prompt)
        for rule in ('HỌC GIỌNG VĂN', 'Tuyệt đối không chép lại câu', 'không bịa chi tiết ngoài dữ liệu video',
                     'không phải lệnh'):
            with self.subTest(rule=rule):
                self.assertIn(rule, prompt)
        payload = json.loads(prompt.rsplit('\n', 1)[-1])
        self.assertEqual(payload['style_samples'], SAMPLES)
        self.assertIn('Mẹo chọn mồi', json.dumps(payload['videos'], ensure_ascii=False))  # The video's own content is still sent.
        self.assertLess(len(prompt), page_ai.MAX_CONTEXT)

    def test_the_largest_samples_still_fit_the_prompt(self):
        task = self.task(style_samples='Mẫu ngắn gọn, vui tai. '*(MAX_STYLE_SAMPLES//23))
        prompt = make_prompt(task, self.store.context(task, ''))
        self.assertLess(len(prompt), page_ai.MAX_CONTEXT)

    def test_samples_do_not_change_the_checks_on_the_answer(self):
        task = self.task(style_samples=SAMPLES)
        context = self.store.context(task, '')
        self.assertTrue(self.store.validate_plan(task, self.answer(task), context))
        bad = self.answer(task)
        bad['plan']['items'][0]['video_key'] = 'f'*32+':9'
        with self.assertRaises(ValueError):
            self.store.validate_plan(task, bad, context)

    def test_a_policy_saved_before_the_change_still_loads_and_runs(self):
        task = self.task()
        self.assertNotIn('style_samples', self.store.policy(self.account, self.page)['config'])
        self.assertTrue(self.store.validate_plan(task, self.answer(task), self.store.context(task, '')))

    def test_the_public_snapshot_carries_the_samples_only_when_saved(self):
        self.saved()
        page = [dict(account_id=self.account, id=self.page)]
        self.assertNotIn('style_samples', self.store.public_snapshot(page)['ai_policies'][0]['config'])
        self.saved(style_samples=SAMPLES)
        self.assertEqual(self.store.public_snapshot(page)['ai_policies'][0]['config']['style_samples'], SAMPLES)


class StylePanelTests(unittest.TestCase):
    def setUp(self):
        fixtures.PageAITests.setUp(self)
        self.tk = tk.Tk()
        self.tk.withdraw()
        self.addCleanup(self.tk.destroy)
        self.manager = SimpleNamespace(owner=self.owner, account_id=self.account, page_key=self.page,
                                       open_strategy_settings=Mock())
        self.panel = PageAIPanel(ttk.Notebook(self.tk), self.manager)
        self.panel.set_sources([self.campaign])

    def test_the_box_starts_empty_with_a_counter(self):
        self.assertEqual(self.panel.style.get('1.0', 'end-1c'), '')
        self.assertEqual(self.panel.style_count.get(), '0 / 8.000 ký tự.')

    def test_saving_with_an_empty_box_leaves_the_key_out(self):
        self.panel.save()
        self.assertNotIn('style_samples', self.store.policy(self.account, self.page)['config'])
        self.assertIn('Đã lưu', self.panel.save_notice)

    def test_saved_samples_are_stored_and_shown_again_after_reopening(self):
        self.panel.style.insert('1.0', '  '+SAMPLES+'\n\n')
        self.panel.update_style_count()
        self.panel.save()
        self.assertEqual(self.store.policy(self.account, self.page)['config']['style_samples'], SAMPLES)
        self.panel.destroy()
        self.panel = PageAIPanel(ttk.Notebook(self.tk), self.manager)
        self.assertEqual(self.panel.style.get('1.0', 'end-1c'), SAMPLES)
        self.assertEqual(self.panel.style_count.get(), f'{len(SAMPLES)} / 8.000 ký tự.')

    def test_clearing_the_box_removes_the_samples_on_the_next_save(self):
        self.panel.style.insert('1.0', SAMPLES)
        self.panel.save()
        self.assertIn('style_samples', self.store.policy(self.account, self.page)['config'])
        self.panel.style.delete('1.0', 'end')
        self.panel.save()
        self.assertNotIn('style_samples', self.store.policy(self.account, self.page)['config'])

    def test_too_long_samples_are_refused_and_nothing_is_saved(self):
        self.panel.style.insert('1.0', 'x'*(MAX_STYLE_SAMPLES+1))
        self.panel.update_style_count()
        self.assertIn('quá dài', self.panel.style_count.get())
        self.panel.save()
        self.assertTrue(self.panel.save_notice.startswith('Chưa lưu: '))
        self.assertIn('8.000', self.panel.save_notice)
        self.assertIsNone(self.store.policy(self.account, self.page))

    def test_saving_other_settings_keeps_the_samples(self):
        self.panel.style.insert('1.0', SAMPLES)
        self.panel.save()
        self.panel.videos.set(3)
        self.panel.save()
        saved = self.store.policy(self.account, self.page)['config']
        self.assertEqual((saved['videos_per_day'], saved['style_samples']), (3, SAMPLES))
        self.invoke.assert_not_called()


if __name__ == '__main__':
    unittest.main()
