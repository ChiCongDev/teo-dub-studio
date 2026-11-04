"""Video nhiều lượt thích làm trước (owner 03/11/2026): the like count is read from Douyin's answer, a new campaign can be
numbered most liked first, and a saved link can be ordered by likes without renumbering anything. Local fixtures only."""
from teodub import campaign, campaign_append, campaign_likes
from teodub.campaign_preview import next_video_config
from teodub.channel_discovery import ChannelPosts, post_likes
from teodub.pipeline import _job_lock
from tests.test_campaign_rotation import Fixture, SETTINGS

OWNER = 'MS4wOwner'


def link(number):
    return f'https://www.douyin.com/video/{number}'


def counted(likes):
    """Channel records for videos 100.. with the given like counts (None = Douyin gave no count)."""
    return [dict(url=link(100+offset), title=f't{offset}', **({} if count is None else dict(likes=count)))
            for offset, count in enumerate(likes)]


class ReadingTests(Fixture):
    def post(self, ident, likes):
        statistics = {} if likes is None else dict(digg_count=likes, play_count=0)
        return dict(aweme_id=str(ident), desc='d', author=dict(sec_uid=OWNER), video=dict(play_addr='x'), statistics=statistics)

    def test_the_like_count_is_read_and_a_missing_or_odd_one_is_left_out(self):
        self.assertEqual(post_likes(self.post(1, 7)), 7)
        self.assertEqual(post_likes(self.post(1, 0)), 0)
        self.assertIsNone(post_likes(self.post(1, None)))
        self.assertIsNone(post_likes(dict(statistics=dict(digg_count='12'))))
        self.assertIsNone(post_likes(dict(statistics=dict(digg_count=True))))
        self.assertIsNone(post_likes(dict()))

    def test_the_answer_gives_each_video_its_likes_even_if_the_page_links_were_read_first(self):
        posts = ChannelPosts(OWNER, 10)
        posts.items['2'] = dict(url=link(2), title='from the page')
        url = 'https://www.douyin.com/aweme/v1/web/aweme/post?sec_user_id='+OWNER
        posts.consume(url, dict(status_code=0, has_more=1, aweme_list=[self.post(1, 5), self.post(2, 9), self.post(3, None)]))
        self.assertEqual(posts.items['1']['likes'], 5)
        self.assertEqual(posts.items['2']['likes'], 9)
        self.assertNotIn('likes', posts.items['3'])


class OrderTests(Fixture):
    def test_most_liked_first_unknown_after_and_ties_keep_their_order(self):
        rows = [dict(n=1, likes=5), dict(n=2), dict(n=3, likes=50), dict(n=4, likes=5), dict(n=5, likes=0)]
        self.assertEqual([row['n'] for row in campaign.likes_first(rows)], [3, 1, 4, 5, 2])

    def test_a_new_campaign_is_numbered_most_liked_first_only_when_asked(self):
        records = counted([3, 30, 10])
        plain = campaign.create_campaign(self.root, 'P', records, SETTINGS, str(self.root/'o'), str(self.root/'u'))
        liked = campaign.create_campaign(self.root, 'L', records, SETTINGS, str(self.root/'o'), str(self.root/'u'), order='likes')
        self.assertEqual([i['url'][-3:] for i in campaign.read_json(plain/'campaign.json')['items']], ['100', '101', '102'])
        items = campaign.read_json(liked/'campaign.json')['items']
        self.assertEqual([(i['number'], i['url'][-3:], i['likes']) for i in items], [(1, '101', 30), (2, '102', 10), (3, '100', 3)])

    def test_the_new_videos_of_an_append_go_in_most_liked_first(self):
        a = self.make('A', 1)
        campaign_append.append_videos(a, [dict(url=link(500), title='a', likes=1), dict(url=link(501), title='b', likes=9)])
        items = campaign.read_json(a/'campaign.json')['items']
        self.assertEqual([(i['number'], i['url'][-3:]) for i in items[1:]], [(2, '501'), (3, '500')])


class ApplyTests(Fixture):
    def test_a_saved_link_is_picked_by_likes_without_renumbering_and_done_videos_stay(self):
        a = self.make('A', 5)
        self.assertEqual(campaign._run_campaign_locked(a, runner=self.video, max_videos=1)['status'], 'paused')  # Video 1 done
        before = campaign.read_json(a/'campaign.json')
        result = campaign_likes.apply_likes(a, counted([None, 5, 50, None, 20]))
        self.assertEqual(result, dict(matched=3, total=5))  # Only a video with a count in the reading gets one
        after = campaign.read_json(a/'campaign.json')
        self.assertEqual([i['number'] for i in after['items']], [1, 2, 3, 4, 5])
        self.assertEqual(after['items'][0]['status'], 'completed')
        self.assertEqual(after['items'][0]['outputs'], before['items'][0]['outputs'])
        self.assertEqual([i['job'] for i in after['items']], [i['job'] for i in before['items']])
        self.assertEqual(after['order'], 'likes')
        self.videos.clear()
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual([number for _, number in self.videos], [3, 5, 2, 4])  # 50, 20, 5, then the one without a count

    def test_the_next_video_shown_follows_the_new_order(self):
        a = self.make('A', 3)
        campaign_likes.apply_likes(a, counted([1, 2, 3]))
        manifest = campaign.read_json(a/'campaign.json')
        self.assertEqual(next_video_config(a, manifest)['number'], 3)

    def test_the_preview_changes_nothing_and_says_what_comes_next(self):
        a = self.make('A', 3)
        before = (a/'campaign.json').read_bytes()
        plan = campaign_likes.preview(a, counted([1, 2, 3]))
        self.assertEqual((plan['matched'], plan['pending'], plan['unknown'], plan['changed']), (3, 3, 0, True))
        self.assertEqual([row['number'] for row in plan['first']], [3, 2, 1])
        self.assertEqual((a/'campaign.json').read_bytes(), before)

    def test_nothing_changes_without_counts_or_without_a_matching_video_or_while_the_campaign_runs(self):
        a = self.make('A', 2)
        before = (a/'campaign.json').read_bytes()
        with self.assertRaises(ValueError):
            campaign_likes.apply_likes(a, counted([None, None]))
        with self.assertRaises(ValueError):
            campaign_likes.apply_likes(a, [dict(url=link(900), title='x', likes=4)])
        with _job_lock(a), self.assertRaises(ValueError):
            campaign_likes.apply_likes(a, counted([1, 2]))
        self.assertEqual((a/'campaign.json').read_bytes(), before)
