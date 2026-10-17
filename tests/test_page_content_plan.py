"""Temporary plans, files and existing scheduler; no real Codex or Facebook."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import os
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from teodub.common import atomic_json, hash_file
from teodub.facebook_accounts import FacebookAccounts
from teodub.page_content_plan import ContentPlanStore, extract_plan
from teodub.page_schedule import PageScheduleController, PageScheduleStore


class ContentPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = FacebookAccounts(self.root)
        self.account = self.registry.add_account('Synthetic account')['id']
        self.page = self.registry.save_page(self.account, 'Synthetic Page', 'facebook.com/profile.php?id=100', '100')['id']
        self.thread = str(uuid.uuid4())
        self.store = ContentPlanStore(self.root)
        self.store.save_connection(self.account, self.page, self.thread, 'Phát triển page câu cá')
        self.scheduler = PageScheduleStore(self.root)
        owner = SimpleNamespace(studio=SimpleNamespace(data_dir=self.root), sessions={}, status=Mock(),
            submit_page_browser=Mock(return_value=None))
        self.controller = PageScheduleController(owner)
        self.addCleanup(self.controller.close)

    def item(self, ident='bai-1', *, kind='text', video_key=''):
        date = datetime.now(timezone(timedelta(hours=7)))+timedelta(days=1)
        return dict(id=ident, kind=kind, due_at=date.isoformat(timespec='seconds'),
            body='Một buổi câu cá vui vẻ.\n#cauca', video_key=video_key, goal='Chia sẻ kinh nghiệm hữu ích')

    def document(self, items=None):
        return dict(format='teo-page-plan', version=1, plan_id=str(uuid.uuid4()), title='Tuần câu cá',
            page_id='100', time_zone='Asia/Bangkok', items=items or [self.item()])

    def snapshot(self, document, **extra):
        return dict(id=self.thread, messages=[dict(turn_id='turn-1', phase='final_answer', turn_status='completed',
            text='Đề xuất:\n```teo-page-plan\n'+json.dumps(document, ensure_ascii=False)+'\n```')], **extra)

    def imported(self, document=None):
        answer = self.store.import_messages(self.account, self.page, self.snapshot(document or self.document()))
        self.assertFalse(answer['error'], answer['status'])
        return answer['plan']

    def video(self, number=1, campaign_id='a'*32, *, status='completed'):
        folder = self.root/'campaigns'/campaign_id
        job = folder/'jobs'/f'{number:04}'
        outputs = self.root/'outputs'/campaign_id
        outputs.mkdir(parents=True, exist_ok=True)
        video = outputs/f'Video {number}.mp4'
        video.write_bytes(b'synthetic video '+str(number).encode())
        result = dict(video=str(video))
        atomic_json(job/'state.json', dict(rendered=dict(outputs=result, hashes=dict(video=hash_file(video)))))
        atomic_json(folder/'campaign.json', dict(id=campaign_id, name='Synthetic campaign', output_dir=str(outputs),
            items=[dict(number=number, status=status, job=str(job), outputs=result, title='Kinh nghiệm câu cá')]))
        return campaign_id+':'+str(number), video

    def many_videos(self, campaign_id, name, count):
        folder = self.root/'campaigns'/campaign_id
        output_dir = self.root/'outputs'/campaign_id
        output_dir.mkdir(parents=True, exist_ok=True)
        items = []
        for number in range(1, count+1):
            job = folder/'jobs'/f'{number:04}'
            output = output_dir/f'Video {number}.mp4'
            output.write_bytes(b'synthetic video '+str(number).encode())
            outputs = dict(video=str(output))
            atomic_json(job/'state.json', dict(rendered=dict(outputs=outputs, hashes=dict(video=hash_file(output)))))
            items.append(dict(number=number, status='completed', job=str(job), outputs=outputs,
                title=name+' — nội dung '+str(number)))
        atomic_json(folder/'campaign.json', dict(id=campaign_id, name=name, output_dir=str(output_dir), items=items))
        return folder

    def test_connection_uuid_scope_bool_and_disconnect_keeps_schedules(self):
        connection = self.store.get_connection(self.account, self.page)
        self.assertEqual(connection['thread_id'], self.thread)
        self.assertFalse(connection['auto_sync'])
        for args in (('bad', 'X', False), (self.thread, 'X', 1), (self.thread, '', False)):
            with self.assertRaises(ValueError):
                self.store.save_connection(self.account, self.page, *args)
        other = self.registry.add_account('Other')['id']
        with self.assertRaises(ValueError):
            self.store.save_connection(other, self.page, self.thread, 'X')
        plan = self.imported()
        self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.store.disconnect(self.account, self.page)
        self.assertEqual(self.store.get_connection(self.account, self.page)['thread_id'], '')
        self.assertEqual(len(self.scheduler.list()), 1)

    def test_only_final_completed_fence_and_latest_block_counts(self):
        old, latest = self.document(), self.document()
        messages = self.snapshot(old)['messages']+self.snapshot(latest)['messages']
        messages.append(dict(phase='commentary', text=self.snapshot(self.document())['messages'][0]['text']))
        self.assertEqual(extract_plan(messages)['plan_id'], latest['plan_id'])
        for change in ({'phase':'analysis'}, {'role':'user'}, {'turn_status':'inProgress'}, {'turn_status':'failed'}):
            with self.assertRaisesRegex(ValueError, 'Chưa có kế hoạch'):
                extract_plan([dict(messages[0], **change)])
        with self.assertRaises(ValueError):
            extract_plan([dict(phase='final_answer', text=json.dumps(old))])
        bad = dict(phase='final_answer', text='```teo-page-plan\n{"bad":true}\n```')
        with self.assertRaises(ValueError):
            extract_plan(messages+[bad])

    def test_schema_is_strict_and_rejects_whole_plan(self):
        base = self.document()
        mutations = [lambda p:p.update(version=True), lambda p:p.update(page_id=100),
            lambda p:p.update(time_zone='UTC'), lambda p:p.update(path='C:/unsafe.mp4'),
            lambda p:p.update(items=[]), lambda p:p.update(plan_id='not UUID'),
            lambda p:p['items'][0].update(kind='tool'), lambda p:p['items'][0].update(kind={}),
            lambda p:p['items'][0].update(path='unsafe'), lambda p:p['items'][0].update(body=''),
            lambda p:p['items'][0].update(body='x'*6001), lambda p:p['items'][0].update(id='bad id'),
            lambda p:p['items'][0].update(video_key='a'*32+':1'),
            lambda p:p['items'][0].update(due_at='2026-01-01T20:00:00Z'),
            lambda p:p['items'][0].update(due_at='2026-01-01T20:00:00+07:00')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                data = deepcopy(base)
                mutate(data)
                with self.assertRaises(ValueError):
                    extract_plan(self.snapshot(data)['messages'])
        data = deepcopy(base)
        data['items'].append(deepcopy(data['items'][0]))
        with self.assertRaises(ValueError):
            extract_plan(self.snapshot(data)['messages'])
        raw = json.dumps(base).replace('"version": 1', '"version": 1, "version": 1')
        with self.assertRaisesRegex(ValueError, 'trùng'):
            extract_plan([dict(phase='final_answer', text='```teo-page-plan\n'+raw+'\n```')])

    def test_inventory_skips_unfinished_or_archived_and_prompt_has_no_path_or_srt(self):
        key, video = self.video()
        self.video(campaign_id='b'*32, status='review_required')
        self.video(campaign_id='c'*32)
        manifest = self.root/'campaigns'/('c'*32)/'campaign.json'
        data = json.loads(manifest.read_text(encoding='utf-8'))
        data['archived_at'] = 'synthetic'
        atomic_json(manifest, data)
        self.assertEqual([item['key'] for item in self.store.videos()], [key])
        self.store.activity.save_details(self.account, self.page, 'Câu cá', 'Giọng gần gũi')
        prompt = self.store.request_context(self.account, self.page)
        self.assertIn(key, prompt)
        self.assertIn('Giọng gần gũi', prompt)
        self.assertNotIn(str(self.root), prompt)
        self.assertIn('```teo-page-plan', prompt)

    def test_import_passive_idempotent_and_wrong_page_thread_no_writes(self):
        document = self.document()
        plan = self.imported(document)
        self.assertEqual(plan['state'], 'proposed')
        self.assertFalse(plan['items'][0]['schedule_id'])
        self.assertEqual(self.scheduler.list(), [])
        self.assertEqual(self.store.activity.posts(self.account, self.page), [])
        repeated = self.store.import_messages(self.account, self.page, self.snapshot(document))
        self.assertFalse(repeated['imported'])
        self.assertEqual(repeated['plan']['digest'], plan['digest'])
        wrong = self.snapshot(self.document())
        wrong['id'] = str(uuid.uuid4())
        self.assertTrue(self.store.import_messages(self.account, self.page, wrong)['error'])
        wrong = self.document()
        wrong['page_id'] = '999'
        self.assertTrue(self.store.import_messages(self.account, self.page, self.snapshot(wrong))['error'])
        self.assertEqual(len(self.store.plans(self.account, self.page)), 1)

    def test_changed_same_plan_id_keeps_old_immutable(self):
        document = self.document()
        plan = self.imported(document)
        document['items'][0]['body'] = 'Changed'
        answer = self.store.import_messages(self.account, self.page, self.snapshot(document))
        self.assertTrue(answer['error'])
        self.assertIn('plan_id mới', answer['status'])
        self.assertEqual(self.store.get_plan(plan['plan_id'], self.account, self.page)['digest'], plan['digest'])
        self.assertIn('thay đổi', self.store.get_connection(self.account, self.page)['last_error'])

    def test_repeated_old_plan_after_due_is_still_idempotent(self):
        document = self.document()
        self.imported(document)
        future = time.time()+3*86400
        with patch('teodub.page_content_plan.time.time', return_value=future):
            answer = self.store.import_messages(self.account, self.page, self.snapshot(document))
        self.assertFalse(answer['error'])
        self.assertFalse(answer['imported'])

    def test_missing_plan_status_keeps_previous_plans(self):
        self.imported()
        answer = self.store.import_messages(self.account, self.page,
            dict(id=self.thread, messages=[dict(phase='final_answer', text='Chưa lên kế hoạch')]))
        self.assertTrue(answer['error'])
        self.assertIn('Chưa có kế hoạch', answer['status'])
        self.assertEqual(len(self.store.plans(self.account, self.page)), 1)

    def test_video_unknown_or_repeated_key_rejects(self):
        document = self.document([self.item(kind='video', video_key='f'*32+':1')])
        self.assertTrue(self.store.import_messages(self.account, self.page, self.snapshot(document))['error'])
        key, _ = self.video()
        document = self.document([self.item(kind='video', video_key=key), self.item('bai-2', kind='video', video_key=key)])
        with self.assertRaises(ValueError):
            extract_plan(self.snapshot(document)['messages'])
        self.assertEqual(self.store.plans(self.account, self.page), [])

    def test_stage_creates_passive_mixed_schedules_then_approval_arms_once(self):
        key, video = self.video()
        plan = self.imported(self.document([self.item(), self.item('bai-2', kind='video', video_key=key)]))
        staged = self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.assertEqual(staged['state'], 'staged')
        self.assertEqual({row['state'] for row in self.scheduler.list()}, {'scheduled'})
        self.assertEqual(staged['items'][1]['source']['path'], str(video))
        self.assertFalse(self.controller.active)
        with patch('teodub.page_content_plan.hash_file', side_effect=AssertionError('No second full hash')):
            approved = self.store.approve(plan['plan_id'], self.account, self.page, self.controller, plan['digest'])
        self.assertEqual({item['state'] for item in approved['items']}, {'armed'})
        self.assertEqual(len(self.controller.active), 2)
        self.assertEqual(self.controller.owner.submit_page_browser.call_count, 0)
        self.controller.pause(approved['items'][0]['schedule_id'])
        again = self.store.approve(plan['plan_id'], self.account, self.page, self.controller, plan['digest'])
        self.assertEqual(again['items'][0]['state'], 'paused')
        self.assertEqual(len(self.scheduler.list()), 2)

    def test_generated_video_caption_keeps_fixed_page_template(self):
        key, _ = self.video()
        self.scheduler.save_template(self.account, self.page, 'Mẫu của tôi', '#fixed')
        plan = self.imported(self.document([self.item(kind='video', video_key=key)]))
        self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.assertEqual(self.scheduler.template(self.account, self.page), dict(caption='Mẫu của tôi', hashtags='#fixed'))

    def test_wrong_digest_changed_page_or_thread_no_drafts_or_schedules(self):
        plan = self.imported()
        with self.assertRaises(ValueError):
            self.store.stage(plan['plan_id'], self.account, self.page, 'f'*64)
        self.registry.save_page(self.account, 'Changed Page', 'facebook.com/profile.php?id=100', '100', record_id=self.page)
        with self.assertRaisesRegex(ValueError, 'Page'):
            self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.assertEqual(self.scheduler.list(), [])
        self.assertEqual(self.store.activity.posts(self.account, self.page), [])

    def test_changed_video_before_stage_rejects_whole_plan_even_text_first(self):
        key, video = self.video()
        plan = self.imported(self.document([self.item(), self.item('bai-2', kind='video', video_key=key)]))
        video.write_bytes(b'changed bytes')
        with self.assertRaisesRegex(ValueError, 'khớp thành phẩm'):
            self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.assertEqual(self.scheduler.list(), [])
        self.assertEqual(self.store.activity.posts(self.account, self.page), [])

    def test_existing_unrelated_video_schedule_blocks_plan(self):
        key, _ = self.video()
        plan = self.imported(self.document([self.item(kind='video', video_key=key)]))
        source = self.store.videos()[0]['source']
        self.scheduler.create(self.account, self.page, source, 'Other caption', '', time.time()+3600)
        with self.assertRaisesRegex(ValueError, 'đã có lịch'):
            self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.assertEqual(len(self.scheduler.list()), 1)
        self.assertFalse(self.controller.active)

    def test_changed_staged_post_prevents_approval(self):
        plan = self.imported()
        staged = self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.store.activity.save_draft(self.account, self.page, 'Manual changed body', post_id=staged['items'][0]['post_id'])
        with self.assertRaisesRegex(ValueError, 'Bản nháp đã đổi'):
            self.store.approve(plan['plan_id'], self.account, self.page, self.controller, plan['digest'])
        self.assertFalse(self.controller.active)

    def test_failed_creation_retains_passive_work_and_retry_has_no_duplicates(self):
        key, _ = self.video()
        plan = self.imported(self.document([self.item(), self.item('bai-2', kind='video', video_key=key)]))
        original = PageScheduleStore.create
        with patch.object(PageScheduleStore, 'create', side_effect=ValueError('Synthetic failure')):
            with self.assertRaisesRegex(ValueError, 'Synthetic failure'):
                self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.assertEqual(len(self.scheduler.list()), 1)
        self.assertEqual(self.scheduler.list()[0]['state'], 'scheduled')
        retry = self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.assertTrue(all(item['schedule_id'] for item in retry['items']))
        self.assertEqual(len(self.scheduler.list()), 2)

    def test_crash_window_after_video_schedule_recovers_exact_intent(self):
        key, _ = self.video()
        plan = self.imported(self.document([self.item(kind='video', video_key=key)]))
        original = self.store._entry
        def lost_reply(record, item, **values):
            if values.get('schedule_id'):
                raise OSError('Synthetic lost update')
            return original(record, item, **values)
        with patch.object(self.store, '_entry', side_effect=lost_reply):
            with self.assertRaises(ValueError):
                self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.assertEqual(len(self.scheduler.list()), 1)
        recovered = self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.assertTrue(recovered['items'][0]['schedule_id'])
        self.assertEqual(len(self.scheduler.list()), 1)
        self.assertFalse(self.controller.active)

    def test_deterministic_text_draft_recovers_save_mapping_gap(self):
        plan = self.imported()
        original = self.store._entry
        def lost_reply(record, item, **values):
            if values.get('post_id') and not values.get('schedule_id'):
                raise OSError('Synthetic draft mapping loss')
            return original(record, item, **values)
        with patch.object(self.store, '_entry', side_effect=lost_reply):
            with self.assertRaises(ValueError):
                self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.assertEqual(len(self.store.activity.posts(self.account, self.page)), 1)
        self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.assertEqual(len(self.store.activity.posts(self.account, self.page)), 1)
        self.assertEqual(len(self.scheduler.list()), 1)

    def test_arm_failure_pauses_earlier_items_and_never_rearms_on_repeat(self):
        plan = self.imported(self.document([self.item(), self.item('bai-2')]))
        self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        start = self.controller.start
        calls = []
        def fail_second(ident):
            calls.append(ident)
            if len(calls) == 2:
                raise ValueError('Synthetic arm failure')
            return start(ident)
        with patch.object(self.controller, 'start', side_effect=fail_second):
            with self.assertRaises(ValueError):
                self.store.approve(plan['plan_id'], self.account, self.page, self.controller, plan['digest'])
        self.assertFalse(self.controller.active)
        again = self.store.approve(plan['plan_id'], self.account, self.page, self.controller, plan['digest'])
        self.assertEqual(again['state'], 'needs_attention')
        self.assertEqual(len(calls), 2)

    def test_sync_delegates_exact_saved_thread_and_keeps_failure_notice(self):
        reader = Mock(return_value=self.snapshot(self.document()))
        answer = self.store.sync(self.account, self.page, reader)
        reader.assert_called_once_with(self.thread)
        self.assertFalse(answer['error'])
        reader.side_effect = OSError('Synthetic offline')
        self.assertTrue(self.store.sync(self.account, self.page, reader)['error'])
        self.assertIn('offline', self.store.get_connection(self.account, self.page)['last_error'])

    def test_cancelled_schedule_mapping_is_never_recreated_or_rearmed(self):
        document = self.document()
        plan = self.imported(document)
        staged = self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        ident = staged['items'][0]['schedule_id']
        self.controller.cancel(ident)
        synced = self.store.import_messages(self.account, self.page, self.snapshot(document))
        self.assertFalse(synced['imported'])
        self.assertEqual(synced['plan']['items'][0]['state'], 'cancelled')
        with self.assertRaises(ValueError):
            self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        with self.assertRaises(ValueError):
            self.store.approve(plan['plan_id'], self.account, self.page, self.controller, plan['digest'])
        self.assertEqual(len(self.scheduler.list()), 1)
        self.assertTrue(self.store.activity.post(self.account, self.page, staged['items'][0]['post_id'])['archived'])
        self.assertFalse(self.controller.active)

    def test_same_uuid_different_linked_threads_are_distinct_and_cannot_cross_approve(self):
        document = self.document()
        first_thread = self.thread
        first = self.imported(document)
        self.thread = str(uuid.uuid4())
        self.store.save_connection(self.account, self.page, self.thread, 'Another strategy chat')
        changed = deepcopy(document)
        changed['items'][0]['body'] = 'Nội dung từ cuộc trò chuyện khác'
        second = self.imported(changed)
        self.assertEqual(first['plan_id'], second['plan_id'])
        self.assertNotEqual(first['record_id'], second['record_id'])
        self.assertEqual(self.store.get_plan(first['plan_id'], self.account, self.page)['digest'], second['digest'])
        with self.assertRaises(ValueError):
            self.store.stage(first['plan_id'], self.account, self.page, first['digest'])
        self.assertEqual(self.scheduler.list(), [])
        self.store.save_connection(self.account, self.page, first_thread, 'Phát triển page câu cá')
        restored = self.store.get_plan(first['plan_id'], self.account, self.page)
        self.assertEqual(restored['digest'], first['digest'])
        self.assertEqual(len(self.store.plans(self.account, self.page)), 2)

    def test_link_changes_during_import_do_not_write_plan_or_new_link_error(self):
        old_thread = self.thread
        new_thread = str(uuid.uuid4())
        original = self.store.get_connection
        changed = []
        def connection_with_race(account, page):
            current = original(account, page)
            if not changed:
                changed.append(True)
                with self.store.database() as db:
                    db.execute('UPDATE connections SET thread_id=?,last_error=? WHERE account_id=? AND page_key=?',
                        (new_thread, 'New connection notice', account, page))
            return current
        with patch.object(self.store, 'get_connection', side_effect=connection_with_race):
            answer = self.store.import_messages(self.account, self.page, self.snapshot(self.document()))
        self.assertTrue(answer['error'])
        self.assertEqual(self.store.plans(self.account, self.page), [])
        self.assertEqual(self.store.get_connection(self.account, self.page)['thread_id'], new_thread)
        self.assertEqual(self.store.get_connection(self.account, self.page)['last_error'], 'New connection notice')

    def test_recovered_video_intent_rejects_edited_post_before_other_entries_save(self):
        key, _ = self.video()
        plan = self.imported(self.document([self.item(kind='video', video_key=key), self.item('bai-2')]))
        original = self.store._entry
        def lost_reply(record, item, **values):
            if values.get('schedule_id'):
                raise OSError('Synthetic lost mapping')
            return original(record, item, **values)
        with patch.object(self.store, '_entry', side_effect=lost_reply):
            with self.assertRaises(ValueError):
                self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        schedule = self.scheduler.list()[0]
        self.store.activity.save_draft(self.account, self.page, 'Manual body changed', post_id=schedule['post_id'])
        with self.assertRaisesRegex(ValueError, 'Bản nháp đã đổi'):
            self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.assertEqual(len(self.store.activity.posts(self.account, self.page)), 1)
        self.assertEqual(len(self.scheduler.list()), 1)

    def test_new_large_campaign_cannot_starve_older_fishing_campaign_or_import(self):
        fishing_id, newer_id = 'd'*32, 'e'*32
        older = self.many_videos(fishing_id, 'Kênh Câu Cá', 1)
        self.many_videos(newer_id, 'Mẹ và Bé', 260)
        stamp = time.time()-300
        os.utime(older, (stamp, stamp))
        inventory = self.store.videos()
        self.assertEqual(len(inventory), 250)
        self.assertIn(fishing_id+':1', [video['key'] for video in inventory])
        self.assertEqual(inventory[1]['campaign_name'], 'Kênh Câu Cá')
        document = self.document([self.item(kind='video', video_key=fishing_id+':1')])
        # Authorization must not depend on any presentation inventory limit.
        with patch.object(self.store, 'videos', return_value=[]):
            plan = self.imported(document)
            staged = self.store.stage(plan['plan_id'], self.account, self.page, plan['digest'])
        self.assertEqual(staged['items'][0]['source']['key'], fishing_id+':1')
        self.assertEqual(len(self.scheduler.list()), 1)
        self.assertFalse(self.controller.active)
        prompt = self.store.request_context(self.account, self.page)
        self.assertIn('Kênh Câu Cá', prompt)
        self.assertLessEqual(prompt.count('"campaign_name"'), 30)
        self.assertIn('Danh sách mẫu', prompt)

    def test_compact_context_has_at_most_thirty_diverse_titles(self):
        for number in range(12):
            self.many_videos(f'{number+1:032x}', 'Campaign '+str(number), 5)
        prompt = self.store.request_context(self.account, self.page)
        self.assertEqual(prompt.count('"campaign_name"'), 30)
        for number in range(12):
            self.assertIn('Campaign '+str(number), prompt)
        self.assertNotIn(str(self.root), prompt)


if __name__ == '__main__':
    unittest.main()
