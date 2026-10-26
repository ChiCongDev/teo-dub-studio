"""Natural video captions (owner 23/10/2026): the AI's caption rules and dialogue sampled over the whole video.
Synthetic stores and files only; no AI call."""
import json
import unittest

from teodub import page_ai
from teodub.common import atomic_json, hash_file
from teodub.page_ai import CAPTION_STYLE, _dialogue_samples, make_prompt
from tests import test_page_ai as fixtures


def numbered(count):
    return ' '.join(f'Câu số {i} ở chợ đêm.' for i in range(1, count+1))


class DialogueSampleTests(unittest.TestCase):
    def test_short_dialogue_is_kept_whole(self):
        self.assertEqual(_dialogue_samples('  Xin   chào\nmọi người. ', 700), 'Xin chào mọi người.')

    def test_long_dialogue_is_sampled_from_start_middle_and_end(self):
        text = numbered(300)
        sample = _dialogue_samples(text, 1200)
        self.assertLessEqual(len(sample), 1200)
        self.assertTrue(sample.startswith('Câu số 1 ở chợ đêm.'))
        self.assertIn('Câu số 300 ở chợ đêm.', sample)
        self.assertTrue(any(f'Câu số {i} ' in sample for i in range(140, 161)))  # The middle of the video.
        self.assertEqual(sample.count(' … '), page_ai.DIALOGUE_PIECES-1)
        for piece in sample.split(' … '):
            self.assertIn(piece, text)  # Whole words only: every piece is an exact run of the dialogue.

    def test_a_small_limit_uses_fewer_pieces(self):
        sample = _dialogue_samples(numbered(300), 250)
        self.assertLessEqual(len(sample), 250)
        self.assertEqual(sample.count(' … '), 1)


class CaptionContextTests(unittest.TestCase):
    setUp = fixtures.PageAITests.setUp
    saved = fixtures.PageAITests.saved
    task = fixtures.PageAITests.task

    def add_videos(self, count, text):
        """count finished campaign videos (replacing the fixture's one), each with this dialogue."""
        output = self.video.parent
        items = []
        for number in range(1, count+1):
            video, subs = output/f'Video {number}.mp4', output/f'Video {number}.srt'
            video.write_bytes(b'synthetic finished video %d' % number)
            subs.write_text('1\n00:00:00,000 --> 00:30:00,000\n'+text+'\n', encoding='utf-8')
            job = self.root/'campaigns'/self.campaign/'jobs'/f'{number:04}'
            outputs = dict(video=str(video), subtitles=str(subs))
            atomic_json(job/'state.json', dict(rendered=dict(outputs=outputs,
                                                             hashes={k: hash_file(v) for k, v in outputs.items()})))
            items.append(dict(number=number, status='completed', job=str(job), outputs=outputs, title=f'Video {number}'))
        atomic_json(self.root/'campaigns'/self.campaign/'campaign.json',
                    dict(id=self.campaign, name='Câu cá', output_dir=str(output), items=items))

    def test_one_video_gets_up_to_the_larger_sample_from_the_whole_video(self):
        self.add_videos(1, numbered(300))
        task = self.task()
        video = self.store.context(task, '')['videos'][0]
        self.assertTrue(video['content_verified'])
        self.assertGreater(len(video['dialogue_excerpt']), page_ai.DIALOGUE_LIMIT)
        self.assertLessEqual(len(video['dialogue_excerpt']), page_ai.DIALOGUE_MAX)
        self.assertIn('Câu số 300 ở chợ đêm.', video['dialogue_excerpt'])

    def test_many_videos_keep_the_earlier_size_per_video_but_sample_the_whole_video(self):
        self.add_videos(20, numbered(300))
        task = self.task()
        videos = self.store.context(task, '')['videos']
        self.assertEqual(len(videos), 20)
        for video in videos:
            self.assertLessEqual(len(video['dialogue_excerpt']), page_ai.DIALOGUE_LIMIT)  # Never more than before.
            self.assertIn('Câu số 300 ở chợ đêm.', video['dialogue_excerpt'])

    def test_the_prompt_carries_the_caption_rules_and_the_page_goal(self):
        self.add_videos(1, 'Bát phở này giá 120 nghìn mà anh chủ quán còn tặng thêm trứng.')
        task = self.task(goal='Caption vui, ngắn; hợp người trẻ.')
        prompt = make_prompt(task, self.store.context(task, ''))
        self.assertIn(CAPTION_STYLE, prompt)
        for rule in ('8–20 chữ', 'móc câu', 'Không mở đầu bằng "Video ghi lại"', 'Không kêu gọi like/share/comment',
                     'không thêm chi tiết không có trong dữ liệu video', 'goals nêu cách viết',
                     'Đổi kiểu móc câu giữa các bài', 'không chọn chi tiết vặt'):
            with self.subTest(rule=rule):
                self.assertIn(rule, prompt)
        self.assertNotIn('viết nội dung hữu ích', prompt)
        payload = json.loads(prompt.rsplit('\n', 1)[-1])
        self.assertEqual(payload['goals'], 'Caption vui, ngắn; hợp người trẻ.')
        self.assertIn('120 nghìn', payload['videos'][0]['dialogue_excerpt'])
        self.assertIn('plan_id='+task['plan_uuid'], prompt)


if __name__ == '__main__':
    unittest.main()
