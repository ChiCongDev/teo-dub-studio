"""Channel sources with real Page/AI/schedule stores; synthetic scans, campaigns and batches.

No browser, AI inference, video processing or Facebook action happens in these tests.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from teodub import channel_autopilot as autopilot
from teodub.campaign import read_json
from teodub.common import atomic_json
from teodub.facebook_accounts import FacebookAccounts
from teodub.page_ai import PageAIController, PageAIStore
from teodub.page_schedule import LOCAL_TIME, PageScheduleStore

CHANNEL = 'https://www.douyin.com/user/MS4wLjABAAAAsyntheticChannel01'
OTHER_CHANNEL = 'https://www.douyin.com/user/MS4wLjABAAAAsyntheticChannel02'
SETTINGS = dict(voice='synthetic-voice', speed=1.0)


def video(number):
    return dict(url=f'https://www.douyin.com/video/{7000000000000000000+number}', title=f'Video {number}')


class FakeBatch:
    def __init__(self, root):
        self.root, self.busy, self.directory, self.starts = root, False, None, []

    def start(self, directories):
        if self.busy:
            raise ValueError('Đợi công việc đang chạy hoàn tất hoặc bấm Dừng trước.')
        self.directory = self.root/'campaign-batches'/uuid.uuid4().hex
        atomic_json(self.directory/'queue.json', dict(status='running', message='Đang chạy', campaigns=[]))
        self.starts.append([Path(d) for d in directories])
        self.busy = True

    def finish(self, status='completed', message='Đã hoàn tất'):
        queue = read_json(self.directory/'queue.json')
        queue.update(status=status, message=message)
        atomic_json(self.directory/'queue.json', queue)
        self.busy = False


class ChannelAutopilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        accounts = FacebookAccounts(self.root)
        self.account = accounts.add_account('Synthetic FB')['id']
        self.page_a = accounts.save_page(self.account, 'Page A', 'facebook.com/profile.php?id=100', '100')['id']
        self.page_b = accounts.save_page(self.account, 'Page B', 'facebook.com/profile.php?id=200', '200')['id']
        self.now = time.time()
        self.scans, self.created, self.create_error, self.channel_items = [], [], None, {}
        self.scan_result = dict(channel=CHANNEL, items=[video(n) for n in range(1, 6)], complete=False,
                                reason='no_new_posts', message='')
        self.studio = SimpleNamespace(data_dir=self.root, process=None, campaign_busy=False, login_pending=False,
            voice_learning_busy=False, closing=False, closed=False, restart_pending=False, restart_draining=False)
        self.campaign_tab = SimpleNamespace(scanning=False)
        self.batch = FakeBatch(self.root)
        self.ai = PageAIStore(self.root)
        self.pilot = self.make_pilot()

    def make_pilot(self):
        pilot = autopilot.ChannelAutopilot(self.studio, self.campaign_tab, self.batch, scan=self.scan,
            create=self.create, clock=lambda: self.now, spawn=lambda work: work())
        self.addCleanup(pilot.close)
        return pilot

    def scan(self, text, profile_dir, **options):
        self.scans.append(dict(text=text, profile=Path(profile_dir), **options))
        if isinstance(self.scan_result, Exception):
            raise self.scan_result
        result = deepcopy(self.scan_result)
        if text in self.channel_items:
            result['items'] = [video(n) for n in self.channel_items[text]]
        return result

    def create(self, data_dir, name, records, settings, original_dir, output_dir, discovery=None, **options):
        if self.create_error:
            error, self.create_error = self.create_error, None
            raise ValueError(error)
        ident = uuid.uuid4().hex
        directory = Path(data_dir)/'campaigns'/ident
        items = [dict(number=n, title=r['title'], url=r['url'], status='pending', outputs={})
                 for n, r in enumerate(records, 1)]
        atomic_json(directory/'campaign.json', dict(id=ident, name=name, status='ready', items=items,
            output_dir=str(output_dir), settings=settings, discovery=discovery))
        self.created.append(dict(directory=directory, name=name, urls=[r['url'] for r in records],
                                 settings=settings, original_dir=original_dir, output_dir=output_dir))
        return directory

    def add(self, *pages, channel=CHANNEL, shared=False, per_scan=3):
        targets = [dict(account_id=self.account, page_key=page) for page in pages or (self.page_a,)]
        return self.pilot.add_source(channel, targets, shared=shared, per_scan=per_scan, settings=SETTINGS,
            original_dir=str(self.root/'goc'), output_dir=str(self.root/'long-tieng'), browser='chrome')

    def cycle(self, count=2):
        for _ in range(count):
            self.pilot.tick()

    def complete(self, directory):
        manifest = read_json(directory/'campaign.json')
        for item in manifest['items']:
            item['status'] = 'completed'
        manifest['status'] = 'completed'
        atomic_json(directory/'campaign.json', manifest)

    def morning_after(self, clock):
        local = datetime.fromtimestamp(clock, LOCAL_TIME)
        target = local.replace(hour=6, minute=30, second=0, microsecond=0)
        return (target if target.timestamp() > clock+600 else target+timedelta(days=1)).timestamp()

    def test_first_scan_splits_new_videos_one_page_each_and_never_takes_them_again(self):
        source = self.add(self.page_a, self.page_b)
        self.cycle()
        self.assertEqual(len(self.scans), 1)
        self.assertEqual(self.scans[0]['text'], CHANNEL)
        self.assertEqual(self.scans[0]['visible'], False)
        self.assertEqual(self.scans[0]['profile'], (self.root/'douyin-channel-browser'/'chrome').resolve())
        self.assertEqual([c['urls'] for c in self.created],
                         [[video(1)['url'], video(3)['url']], [video(2)['url']]])
        self.assertTrue(all(c['settings'] == SETTINGS for c in self.created))
        self.assertIn('Page A', self.created[0]['name'])
        self.assertIn('Page B', self.created[1]['name'])
        self.assertEqual(self.ai.policy(self.account, self.page_a)['config']['campaigns'], [self.created[0]['directory'].name])
        self.assertEqual(self.ai.policy(self.account, self.page_b)['config']['campaigns'], [self.created[1]['directory'].name])
        self.pilot.scan_now(source['id'])
        self.cycle()
        self.assertEqual([c['urls'] for c in self.created[2:]], [[video(4)['url']], [video(5)['url']]])
        self.pilot.scan_now(source['id'])
        self.cycle()
        self.assertEqual(len(self.created), 4)
        self.assertEqual(self.pilot.store.get(source['id'])['message'], 'Không có video mới.')

    def test_next_scan_waits_a_day_and_new_source_scans_immediately(self):
        source = self.add(per_scan=2)
        self.assertEqual(source['next_scan_at'], self.now)
        self.cycle()
        saved = self.pilot.store.get(source['id'])
        self.assertEqual(saved['next_scan_at'], self.now+autopilot.SCAN_INTERVAL)
        self.now += autopilot.SCAN_INTERVAL-60
        self.cycle()
        self.assertEqual(len(self.scans), 1)
        self.now += 120
        self.cycle()
        self.assertEqual(len(self.scans), 2)

    def test_days_without_tèo_cause_one_scan_not_a_catch_up_burst(self):
        source = self.add(per_scan=1)
        self.cycle()
        self.now += autopilot.SCAN_INTERVAL*5
        self.cycle(6)
        self.assertEqual(len(self.scans), 2)
        self.assertEqual(self.pilot.store.get(source['id'])['next_scan_at'], self.now+autopilot.SCAN_INTERVAL)

    def test_shared_source_links_one_campaign_to_every_page(self):
        self.add(self.page_a, self.page_b, shared=True)
        self.cycle()
        self.assertEqual(len(self.created), 1)
        ident = self.created[0]['directory'].name
        for page in (self.page_a, self.page_b):
            self.assertEqual(self.ai.policy(self.account, page)['config']['campaigns'], [ident])

    def test_daily_limit_counts_every_source(self):
        self.add(per_scan=3)
        self.add(channel=OTHER_CHANNEL, per_scan=3)
        self.channel_items = {CHANNEL: [1, 2, 3], OTHER_CHANNEL: [11, 12, 13]}
        with patch.object(autopilot, 'DAILY_LIMIT', 4):
            self.cycle(4)
        self.assertEqual([c['urls'] for c in self.created], [[video(n)['url'] for n in (1, 2, 3)], [video(11)['url']]])
        messages = [s['message'] for s in self.pilot.store.sources()]
        self.assertTrue(any('hôm nay' in m for m in messages), messages)

    def test_login_wall_without_videos_needs_attention_and_stops_scanning(self):
        source = self.add()
        self.scan_result = dict(channel=CHANNEL, items=[], complete=False, reason='login_required',
                                message='Douyin yêu cầu đăng nhập để xem thêm video.')
        self.cycle()
        saved = self.pilot.store.get(source['id'])
        self.assertEqual(saved['state'], 'needs_attention')
        self.assertIn('đăng nhập', saved['message'])
        self.now += autopilot.SCAN_INTERVAL*2
        self.cycle()
        self.assertEqual(len(self.scans), 1)
        self.assertEqual(self.created, [])

    def test_partial_list_behind_login_still_uses_visible_new_videos(self):
        self.add()
        self.scan_result.update(reason='login_required', message='Douyin yêu cầu đăng nhập để xem thêm video.')
        self.cycle()
        self.assertEqual(len(self.created), 1)

    def test_scan_error_needs_attention_and_busy_profile_retries_later(self):
        source = self.add()
        self.scan_result = ValueError('Một cửa sổ Tèo khác đang dùng cùng hồ sơ trình duyệt để đăng nhập hoặc đọc kênh.')
        self.cycle()
        saved = self.pilot.store.get(source['id'])
        self.assertEqual(saved['state'], 'active')
        self.assertEqual(saved['next_scan_at'], self.now+autopilot.BUSY_RETRY)
        self.scan_result = RuntimeError('Trình duyệt không mở được')
        self.now += autopilot.BUSY_RETRY
        self.cycle()
        saved = self.pilot.store.get(source['id'])
        self.assertEqual(saved['state'], 'needs_attention')
        self.assertIn('Trình duyệt không mở được', saved['message'])

    def test_new_page_gets_review_only_ai_policy_for_next_morning_without_strategy_chat(self):
        self.add()
        self.cycle()
        policy = self.ai.policy(self.account, self.page_a)
        config = policy['config']
        self.assertIs(config['auto_publish'], False)
        self.assertEqual((config['days'], config['interval_days'], config['videos_per_day'], config['texts_per_week']), (1, 1, 2, 0))
        self.assertEqual(config['thread_id'], '')
        self.assertIn('8–20 từ', config['goal'])
        self.assertTrue(policy['enabled'])
        self.assertEqual(policy['next_at'], self.morning_after(self.now))

    def test_existing_policy_gains_campaign_without_moving_its_appointment(self):
        existing = 'f'*32
        atomic_json(self.root/'campaigns'/existing/'campaign.json', dict(id=existing, name='Cũ', items=[]))
        config = dict(campaigns=[existing], model='gpt-5.6-luna', effort='low', days=7, videos_per_day=2,
                      texts_per_week=3, interval_days=7, goal='Mục tiêu cũ', auto_publish=False)
        before = self.ai.save(self.account, self.page_a, config, time.time()+7200, False)
        self.add()
        self.cycle()
        after = self.ai.policy(self.account, self.page_a)
        self.assertEqual(after['config']['campaigns'], [existing, self.created[0]['directory'].name])
        self.assertEqual((after['next_at'], after['enabled'], after['config']['goal']), (before['next_at'], 0, 'Mục tiêu cũ'))
        self.assertIn('AI của Page A đang tắt', self.pilot.store.sources()[0]['message'])

    def test_auto_publishing_page_is_refused_and_never_fed(self):
        existing = 'f'*32
        atomic_json(self.root/'campaigns'/existing/'campaign.json', dict(id=existing, name='Cũ', items=[]))
        config = dict(campaigns=[existing], model='gpt-5.6-luna', effort='low', days=7, videos_per_day=2,
                      texts_per_week=0, interval_days=7, goal='', auto_publish=True)
        self.ai.save(self.account, self.page_a, config, time.time()+7200, True)
        source = self.add()
        self.cycle(3)
        self.assertEqual(self.ai.policy(self.account, self.page_a)['config']['campaigns'], [existing])
        saved = self.pilot.store.get(source['id'])
        self.assertEqual(saved['state'], 'needs_attention')
        self.assertIn('Tự bật lịch đăng', saved['message'])
        self.assertEqual(len(self.scans), 1)

    def test_running_ai_turn_defers_linking_until_it_finishes(self):
        existing = 'f'*32
        atomic_json(self.root/'campaigns'/existing/'campaign.json', dict(id=existing, name='Cũ', items=[]))
        config = dict(campaigns=[existing], model='gpt-5.6-luna', effort='low', days=1, videos_per_day=2,
                      texts_per_week=0, interval_days=1, goal='', auto_publish=False)
        row = self.ai.save(self.account, self.page_a, config, time.time()+30, True)
        task = self.ai.claim(row, row['next_at']+1)
        self.add()
        self.cycle(3)
        self.assertEqual(self.ai.policy(self.account, self.page_a)['config']['campaigns'], [existing])
        self.ai.record(task['id'], state='done')
        self.cycle()
        self.assertEqual(self.ai.policy(self.account, self.page_a)['config']['campaigns'],
                         [existing, self.created[0]['directory'].name])

    def test_full_flow_never_approves_plans_or_arms_schedules(self):
        self.add(self.page_a, self.page_b)
        self.cycle(4)
        self.assertEqual(PageScheduleStore(self.root).list(), [])
        for page in (self.page_a, self.page_b):
            self.assertEqual(self.ai.plans.plans(self.account, page), [])
            self.assertIs(self.ai.policy(self.account, page)['config']['auto_publish'], False)

    def test_processing_starts_only_when_tèo_is_idle(self):
        self.studio.process = object()
        self.add()
        self.cycle()
        self.assertEqual(self.batch.starts, [])
        self.studio.process = None
        self.campaign_tab.scanning = True
        self.cycle()
        self.assertEqual(self.batch.starts, [])
        self.campaign_tab.scanning = False
        self.cycle(1)
        self.assertEqual(self.batch.starts, [[self.created[0]['directory']]])
        self.cycle()
        self.assertEqual(len(self.batch.starts), 1)

    def test_paused_or_stalled_batch_blocks_auto_processing_until_resumed(self):
        source = self.add()
        self.cycle()
        self.batch.finish('paused', 'Hết hạn mức Codex')
        self.cycle()
        self.assertEqual(len(self.batch.starts), 1)
        self.assertIn('Hết hạn mức Codex', self.pilot.processing_blocked())
        self.pilot.resume_processing()
        self.cycle(1)
        self.assertEqual(len(self.batch.starts), 2)
        self.batch.finish('completed')
        self.cycle()
        self.assertIn('không tiến triển', self.pilot.processing_blocked())
        self.assertEqual(len(self.batch.starts), 2)
        self.pilot.resume_processing()
        self.complete(self.created[0]['directory'])
        self.cycle()
        self.assertEqual(self.pilot.processing_blocked(), '')
        self.assertEqual(self.pilot.store.get(source['id'])['state'], 'active')

    def test_worker_launch_failure_blocks_instead_of_retrying_every_tick(self):
        self.batch.start = Mock(side_effect=OSError('pythonw.exe không tồn tại'))
        self.add()
        self.cycle(4)
        self.batch.start.assert_called_once()
        self.assertIn('pythonw.exe không tồn tại', self.pilot.processing_blocked())

    def test_completed_batch_lets_next_campaigns_start(self):
        source = self.add(per_scan=1)
        self.cycle()
        self.complete(self.created[0]['directory'])
        self.batch.finish('completed')
        self.pilot.scan_now(source['id'])
        self.cycle(3)
        self.assertEqual(self.batch.starts, [[self.created[0]['directory']], [self.created[1]['directory']]])

    def test_paused_source_neither_scans_nor_processes(self):
        source = self.add()
        self.studio.process = object()
        self.cycle()
        self.pilot.pause(source['id'])
        self.studio.process = None
        self.pilot.scan_now(source['id'])
        self.cycle()
        self.assertEqual((len(self.scans), self.batch.starts), (1, []))
        self.pilot.resume(source['id'])
        self.cycle(1)
        self.assertEqual(len(self.batch.starts), 1)

    def test_failed_campaign_creation_releases_its_videos_for_the_next_scan(self):
        source = self.add(self.page_a, self.page_b)
        self.create_error = 'Thiếu tệp giọng mẫu'
        self.cycle()
        saved = self.pilot.store.get(source['id'])
        self.assertEqual(saved['state'], 'needs_attention')
        self.assertIn('Thiếu tệp giọng mẫu', saved['message'])
        self.assertEqual(self.pilot.store.seen_urls(), set())
        self.pilot.resume(source['id'])
        self.cycle()
        self.assertEqual(sorted(u for c in self.created for u in c['urls']),
                         sorted(video(n)['url'] for n in (1, 2, 3)))

    def test_interrupted_reservation_is_reported_and_never_fetched_again(self):
        source = self.add()
        self.pilot.store.reserve(source['id'], [video(1)['url']], self.now)
        self.pilot.close()
        self.pilot = self.make_pilot()
        self.cycle()
        saved = self.pilot.store.get(source['id'])
        self.assertEqual(saved['state'], 'needs_attention')
        self.assertIn('gián đoạn', saved['message'])
        self.pilot.resume(source['id'])
        self.cycle()
        self.assertNotIn(video(1)['url'], [u for c in self.created for u in c['urls']])

    def test_restart_or_closing_starts_no_scan_or_batch(self):
        self.add()
        for flag in ('restart_pending', 'restart_draining', 'closing'):
            with self.subTest(flag=flag):
                setattr(self.studio, flag, True)
                self.cycle()
                self.assertEqual((self.scans, self.batch.starts), ([], []))
                setattr(self.studio, flag, False)

    def test_duplicate_channel_unknown_page_and_named_page_are_refused(self):
        self.add()
        with self.assertRaisesRegex(ValueError, 'đã có'):
            self.add(self.page_b, channel=CHANNEL+'?from_tab_name=main')
        with self.assertRaises(ValueError):
            self.add('0'*32, channel=OTHER_CHANNEL)
        accounts = FacebookAccounts(self.root)
        unnamed = accounts.save_page(self.account, 'Không ID', 'https://www.facebook.com/khongid')['id']
        with self.assertRaisesRegex(ValueError, 'ID'):
            self.add(unnamed, channel=OTHER_CHANNEL)
        with self.assertRaises(ValueError):
            self.add(channel='https://www.douyin.com/video/7000000000000000001')

    def test_removed_source_frees_its_channel_and_keeps_history(self):
        source = self.add()
        self.cycle()
        self.pilot.remove(source['id'])
        self.assertEqual(self.pilot.store.sources(), [])
        self.assertEqual(self.pilot.store.get(source['id'])['state'], 'removed')
        again = self.add()
        self.cycle()
        self.assertNotEqual(again['id'], source['id'])
        self.assertEqual(len(self.created), 2)
        self.assertFalse(set(self.created[0]['urls']) & set(self.created[1]['urls']))


class PolicyWithoutStrategyChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        accounts = FacebookAccounts(self.root)
        self.account = accounts.add_account('Synthetic FB')['id']
        self.page = accounts.save_page(self.account, 'Page A', 'facebook.com/profile.php?id=100', '100')['id']
        self.campaign = 'a'*32
        atomic_json(self.root/'campaigns'/self.campaign/'campaign.json', dict(id=self.campaign, name='Kênh', items=[]))
        self.store = PageAIStore(self.root)

    def test_ai_turn_without_linked_chat_never_reads_a_chat(self):
        config = dict(autopilot.review_policy([self.campaign]), model='gpt-5.6-luna')
        row = self.store.save(self.account, self.page, config, time.time()+30, True)
        task = self.store.claim(row, row['next_at']+1)
        task.update(cancel=threading.Event(), plan_uuid=str(uuid.uuid4()))
        owner = SimpleNamespace(studio=SimpleNamespace(data_dir=self.root), status=Mock())
        bridge = SimpleNamespace(owner=owner, active=None, content_receiver=SimpleNamespace(pending=None))
        reader = Mock()
        answer = dict(analysis='Chưa có video hoàn tất.', plan=dict(format='teo-page-plan', version=1,
            plan_id=task['plan_uuid'], title='Kế hoạch', page_id='100', time_zone='Asia/Bangkok', items=[]))
        invoke = Mock(return_value=(answer, dict(usage={})))
        controller = PageAIController(bridge, invoke=invoke, reader=reader)
        self.addCleanup(controller.close)
        controller.pending = task
        controller.work(task)
        controller.observe()
        reader.assert_not_called()
        invoke.assert_called_once()
        self.assertEqual(self.store.runs(self.account, self.page)[0]['state'], 'done')

    def test_add_campaigns_refuses_unknown_campaign_and_missing_policy(self):
        with self.assertRaises(ValueError):
            self.store.add_campaigns(self.account, self.page, [self.campaign])
        self.store.save(self.account, self.page, autopilot.review_policy([self.campaign]), time.time()+3600, True)
        with self.assertRaises(ValueError):
            self.store.add_campaigns(self.account, self.page, ['b'*32])


if __name__ == '__main__':
    unittest.main()
