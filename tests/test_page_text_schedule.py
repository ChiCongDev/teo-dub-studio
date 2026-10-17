"""Text schedules use frozen drafts and synthetic ACKs, no real Facebook."""
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub.facebook_accounts import FacebookAccounts
from teodub.page_command_bridge import public_post
from teodub.page_schedule import PageScheduleController, PageScheduleStore


class TextScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.registry = FacebookAccounts(self.directory)
        self.account = self.registry.add_account('Synthetic A')['id']
        self.page = self.registry.save_page(self.account, 'Synthetic Page', 'facebook.com/synthetic', '100')['id']
        self.store = PageScheduleStore(self.directory)
        self.post = self.store.activity.save_draft(self.account, self.page, 'Caption nguyên văn\n#ca #viet',
                                                 delivery_mode='browser', browser_finish_mode='tool')
        self.calls = []
        self.owner = SimpleNamespace(studio=SimpleNamespace(data_dir=self.directory), sessions={}, status=Mock())
        self.owner.submit_page_browser = self.submit
        self.controller = PageScheduleController(self.owner)
        self.addCleanup(self.controller.close)

    def submit(self, account, page, post_id, operation, *, request_id=None):
        self.calls.append((account, page, post_id, operation))
        session = self.owner.sessions.setdefault(account, SimpleNamespace(loading_pages=False, post_actions={}))
        session.post_actions[request_id] = dict(state='queued', result=None, message='Synthetic', operation=operation)
        return request_id

    def create(self):
        target = self.store.activity.page(self.account, self.page)
        return self.store.create_post(self.account, self.page, self.post['id'], time.time()+3600,
            expected_target={key:target[key] for key in ('name', 'url', 'page_id')},
            expected_post_key=public_post(self.post)['post_key'])

    def tick(self):
        self.controller.last_tick = 0
        self.controller.tick()

    def prepare(self, row):
        self.controller.start(row['id'])
        self.store.update(row['id'], due_at=time.time()-1)
        with patch('teodub.page_schedule.threading.Thread') as thread:
            thread.return_value.start.side_effect = lambda: self.controller.validate_video(self.store.get(row['id']))
            self.tick()
        self.tick()
        return self.store.get(row['id'])

    def test_create_is_passive_binds_exact_text_and_keeps_same_post(self):
        row = self.create()
        self.assertEqual(row['state'], 'scheduled')
        self.assertEqual(row['post_id'], self.post['id'])
        self.assertEqual(row['source']['kind'], 'text')
        self.assertEqual(row['source']['key'], 'post:'+self.post['id'])
        self.assertNotIn('path', row['source'])
        self.assertEqual(row['body'], self.post['body'])
        self.assertEqual(row['snapshot']['kind'], 'text')
        self.assertEqual(self.store.activity.post(self.account, self.page, self.post['id'])['state'], 'draft')
        self.assertEqual(self.calls, [])
        with self.assertRaisesRegex(ValueError, 'đã có lịch'):
            self.create()
        self.assertEqual(len(self.store.list()), 1)

    def test_future_page_post_key_wrong_account_and_nonbrowser_rejected(self):
        for kwargs in (dict(due_at=time.time()-1), dict(due_at=time.time()+3600, expected_post_key='f'*64),
                       dict(due_at=time.time()+3600, expected_target={'name':'Other', 'url':'https://www.facebook.com/other', 'page_id':'999'})):
            with self.assertRaises(ValueError):
                self.store.create_post(self.account, self.page, self.post['id'], **kwargs)
        account_b = self.registry.add_account('Synthetic B')['id']
        with self.assertRaises(ValueError):
            self.store.create_post(account_b, self.page, self.post['id'], time.time()+3600)
        self.store.activity.save_draft(self.account, self.page, 'API draft', post_id=self.post['id'], delivery_mode='api')
        with self.assertRaisesRegex(ValueError, 'Trình duyệt web'):
            self.store.create_post(self.account, self.page, self.post['id'], time.time()+3600)
        self.assertEqual(self.store.list(), [])

    def test_media_manual_finish_or_previous_review_cannot_be_text_schedule(self):
        path = self.directory/'synthetic.mp4'
        path.write_bytes(b'synthetic')
        video = self.store.activity.save_draft(self.account, self.page, 'Video', [path], delivery_mode='browser', browser_finish_mode='tool')
        with self.assertRaisesRegex(ValueError, 'không có ảnh/video'):
            self.store.create_post(self.account, self.page, video['id'], time.time()+3600)
        manual = self.store.activity.save_draft(self.account, self.page, 'Manual', delivery_mode='browser', browser_finish_mode='manual')
        with self.assertRaises(ValueError):
            self.store.create_post(self.account, self.page, manual['id'], time.time()+3600)
        self.store.activity.transition(self.account, self.page, self.post['id'], 'preparing', 'Synthetic', expected={'draft'})
        self.store.activity.save_browser_review(self.account, self.page, self.post['id'], dict(body='Synthetic'))
        with self.assertRaisesRegex(ValueError, 'lượt chuẩn bị'):
            self.create()

    def test_changed_text_is_blocked_before_browser_prepare(self):
        row = self.create()
        self.store.activity.save_draft(self.account, self.page, 'Changed after review', post_id=self.post['id'])
        row = self.prepare(row)
        self.assertEqual(row['state'], 'needs_attention')
        self.assertIn('khác lịch', row['message'])
        self.assertEqual(self.calls, [])

    def test_text_schedule_prepares_publishes_once_after_matching_durable_review(self):
        row = self.prepare(self.create())
        self.assertEqual([call[-1] for call in self.calls], ['prepare'])
        self.store.activity.transition(self.account, self.page, self.post['id'], 'preparing', 'Synthetic', expected={'draft'})
        review = dict(account_id=self.account, page_key=self.page, post_id=self.post['id'],
            **{key:self.post[key] for key in ('target_name','target_url','target_id','body','browser_finish_mode')}, media=[])
        post = self.store.activity.save_browser_review(self.account, self.page, self.post['id'], review)
        self.owner.sessions[self.account].post_actions[row['request_id']].update(state='done', result=post)
        self.tick()
        row = self.store.get(row['id'])
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish'])
        self.store.activity.transition(self.account, self.page, self.post['id'], 'submitting', 'Synthetic', expected={'review'})
        url = 'https://www.facebook.com/synthetic/posts/123'
        self.store.activity.record_browser_receipt(self.account, self.page, self.post['id'], dict(source='visible_dom', url=url))
        post = self.store.activity.transition(self.account, self.page, self.post['id'], 'published', 'Synthetic observed',
                                             expected={'submitting'}, external_url=url)
        self.owner.sessions[self.account].post_actions[row['request_id']].update(state='done', result=post)
        self.tick()
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'], 'published')
        self.assertEqual(len(self.calls), 2)

    def test_pause_cancel_and_reopen_remain_passive(self):
        row = self.create()
        self.controller.start(row['id'])
        self.controller.pause(row['id'])
        self.assertEqual(self.store.get(row['id'])['state'], 'paused')
        other = PageScheduleController(self.owner)
        self.addCleanup(other.close)
        self.assertEqual(other.active, set())
        self.controller.cancel(row['id'])
        self.assertEqual(self.store.get(row['id'])['state'], 'cancelled')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
