"""Loại trừ video (owner 04/11/2026): videos of a link marked by their links are never dubbed, do not hold the link back, and come
back with "Bỏ loại trừ". Local fixtures only; no worker process, network, Chrome or AI."""
from teodub import campaign, campaign_append, campaign_batch, campaign_exclude
from teodub.campaign_tracking import tracking_row
from teodub.pipeline import _job_lock
from tests.test_campaign_rotation import Fixture, SETTINGS


def link(number):
    return f'https://www.douyin.com/video/76900000000000{number:05d}'


class ParseTests(Fixture):
    def test_links_ids_and_odd_pieces(self):
        text = f'{link(1)}\n  https://www.douyin.com/user/x?modal_id=7690000000000099999 {link(1)}\n7690000000000088888 https://v.douyin.com/abc/ hello'
        ids, bad = campaign_exclude.parse_links(text)
        self.assertEqual(ids, ['7690000000000000001', '7690000000000099999', '7690000000000088888'])  # in order, no repeats
        self.assertEqual(bad, ['https://v.douyin.com/abc/', 'hello'])

    def test_empty_text_has_nothing(self):
        self.assertEqual(campaign_exclude.parse_links('  \n '), ([], []))


class Base(Fixture):
    def make_link(self, count=4):
        rows = [dict(url=link(number), title=f't{number}') for number in range(1, count+1)]
        return campaign.create_campaign(self.root, 'A', rows, SETTINGS, str(self.root/'originals'), str(self.root/'outputs'))

    def numbers(self, directory):
        return {item['number']: item['status'] for item in campaign.read_json(directory/'campaign.json')['items']}


class ExcludeTests(Base):
    def test_the_plan_says_what_each_link_is_and_changes_nothing(self):
        a = self.make_link()
        campaign._run_campaign_locked(a, runner=self.video, max_videos=1)  # Video 1 done
        before = (a/'campaign.json').read_bytes()
        plan = campaign_exclude.build_plan(a, f'{link(1)} {link(2)} {link(2)} {link(99)} nonsense')
        self.assertEqual([(e['number'], e['kind']) for e in plan['entries']], [(1, 'skip'), (2, 'mark'), (None, 'skip')])
        self.assertEqual(plan['unreadable'], ['nonsense'])
        self.assertEqual(plan['marked'], 1)
        self.assertEqual((a/'campaign.json').read_bytes(), before)

    def test_excluded_videos_are_never_made_and_the_link_completes_without_them(self):
        a = self.make_link()
        out = campaign_exclude.apply(a, [2, 3])
        self.assertEqual(out, dict(mode='now', changed=[2, 3]))
        self.assertEqual(self.numbers(a), {1: 'pending', 2: 'excluded', 3: 'excluded', 4: 'pending'})
        result = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual([number for _, number in self.videos], [1, 4])
        self.assertEqual(result['status'], 'completed')
        manifest = campaign.read_json(a/'campaign.json')
        counts = campaign.totals(manifest)
        self.assertEqual((counts['total'], counts['completed'], counts['excluded']), (2, 2, 2))
        campaign_batch._completed_unchanged(a, manifest)  # A finished link with excluded videos still verifies.
        self.assertEqual(tracking_row(manifest['items'][1])[3], 'Đã loại trừ')

    def test_a_finished_video_is_not_touched_and_a_retry_does_not_bring_an_excluded_one_back(self):
        a = self.make_link(3)
        self.outcomes[('A', 2)] = dict(status='failed', message='lỗi thử', outputs={})
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(campaign_exclude.apply(a, [1, 2])['changed'], [2])  # 1 is finished, 2 failed -> excluded
        self.videos.clear()
        campaign._run_campaign_locked(a, retry_failed=True, runner=self.video)
        self.assertEqual(self.videos, [])
        self.assertEqual(self.numbers(a), {1: 'completed', 2: 'excluded', 3: 'completed'})
        self.assertEqual(campaign.read_json(a/'campaign.json')['status'], 'completed')

    def test_restore_brings_a_video_back_and_reopens_a_completed_link(self):
        a = self.make_link(2)
        campaign_exclude.apply(a, [2])
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(campaign.read_json(a/'campaign.json')['status'], 'completed')
        self.assertEqual(campaign_exclude.apply(a, [2], restore=True)['changed'], [2])
        manifest = campaign.read_json(a/'campaign.json')
        self.assertEqual((manifest['status'], self.numbers(a)[2]), ('paused', 'pending'))
        self.videos.clear()
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos, [('A', 2)])

    def test_asking_while_the_link_runs_leaves_a_request_applied_before_the_next_video(self):
        a = self.make_link(4)
        modes = []
        self.during[('A', 1)] = lambda: modes.append(campaign_exclude.apply(a, [3])['mode'])  # Inside the running worker's lock.
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(modes, ['request'])
        self.assertEqual([number for _, number in self.videos], [1, 2, 4])  # The video being made finished; 3 never started.
        self.assertEqual(self.numbers(a)[3], 'excluded')
        self.assertEqual(campaign_exclude.pending_requests(a), 0)

    def test_a_request_for_the_video_being_made_does_not_stop_it(self):
        a = self.make_link(2)
        self.during[('A', 1)] = lambda: campaign_exclude.apply(a, [1])
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.numbers(a), {1: 'completed', 2: 'completed'})

    def test_appending_does_not_add_an_excluded_video_again(self):
        a = self.make_link(2)
        campaign_exclude.apply(a, [2])
        result = campaign_append.append_videos(a, [dict(url=link(2), title='again'), dict(url=link(3), title='new')])
        self.assertEqual((result['count'], result['skipped']), (1, 1))

    def test_nothing_changes_for_a_damaged_request_or_nothing_to_mark(self):
        a = self.make_link(2)
        (a/'exclude-bad.json').write_text('{not json', encoding='utf-8')
        manifest = campaign.read_json(a/'campaign.json')
        self.assertEqual(campaign.absorb_exclusions(a, manifest), [])
        self.assertFalse((a/'exclude-bad.json').exists())
        with self.assertRaises(ValueError):
            campaign_exclude.apply(a, [])
        with _job_lock(a):
            self.assertEqual(campaign_exclude.apply(a, [1])['mode'], 'request')
