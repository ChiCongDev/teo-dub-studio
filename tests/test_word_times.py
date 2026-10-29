"""29/10/2026: a recognized line starts when its first word is spoken, and a silence of more than two seconds between
two spoken words ends the line. Fake Whisper; no real inference or ffmpeg."""
from types import SimpleNamespace
import unittest

from teodub import asr
from teodub.subtitles import read_srt
from tests.test_listen_gaps import RecheckTests


def word(text, start, end):
    return SimpleNamespace(word=text, start=start, end=end)


def segment(start, end, text, words):
    return SimpleNamespace(start=start, end=end, text=text, words=words, no_speech_prob=.1, avg_logprob=-.4,
                           compression_ratio=1.2)


class PieceTests(unittest.TestCase):
    def test_a_cry_glued_to_a_sentence_twelve_seconds_later_becomes_two_lines(self):
        # The owner's câu_ca video 1: "哇" at 1:16.5, "这个好" at 1:28.7 in one recognized segment.
        glued = segment(76.5, 91.9, '哇 这个好 这个好', [word('哇', 76.5, 76.9), word(' 这个', 88.7, 88.9), word('好', 88.9, 89.2),
                                                           word(' 这个', 90.7, 91.0), word('好', 91.0, 91.9)])
        self.assertEqual(asr._spoken_pieces(glued), [(76.5, 76.9, '哇'), (88.7, 91.9, ' 这个好 这个好')])

    def test_a_gap_up_to_two_seconds_stays_one_line_and_more_than_two_splits(self):
        for gap, count in ((1.9, 1), (2.0, 1), (2.1, 2)):
            with self.subTest(gap=gap):
                parts = [word('甲', 10., 10.5), word('乙', 10.5+gap, 11.+gap)]
                self.assertEqual(len(asr._spoken_pieces(segment(9., 12., '甲乙', parts))), count)

    def test_the_line_runs_from_its_first_word_to_its_last_not_the_segments_own_edges(self):
        pieces = asr._spoken_pieces(segment(70., 80., '你好吗', [word('你好', 71.2, 71.8), word('吗', 71.8, 72.4)]))
        self.assertEqual(pieces, [(71.2, 72.4, '你好吗')])

    def test_without_word_times_the_segment_is_used_as_before(self):
        for words in (None, []):
            with self.subTest(words=words):
                self.assertEqual(asr._spoken_pieces(segment(3., 5., '你好', words)), [(3., 5., '你好')])
        self.assertEqual(asr._spoken_pieces(SimpleNamespace(start=3., end=5., text='你好')), [(3., 5., '你好')])

    def test_words_without_usable_times_are_ignored(self):
        broken = [word('甲', None, 1.), word('乙', 5., 4.), word('丙', 6., 6.5)]
        self.assertEqual(asr._spoken_pieces(segment(0., 7., '甲乙丙', broken)), [(6., 6.5, '丙')])


class TranscribeWordTimeTests(unittest.TestCase):
    setUp = RecheckTests.setUp

    def run_with(self, segments, duration=120.):
        self.small.transcribe.return_value = (iter(segments), SimpleNamespace(duration=duration))
        return asr.transcribe(self.source, self.output, asr_model='small')  # A chosen model: no second listen.

    def lines(self):
        return [(cue['start_ms'], cue['end_ms'], cue['text']) for cue in read_srt(self.output)]

    def test_the_first_pass_asks_for_word_times(self):
        self.run_with([segment(0., 2., '你好', [word('你好', 0., 2.)])])
        self.assertIs(self.small.transcribe.call_args.kwargs['word_timestamps'], True)
        self.assertIs(self.small.transcribe.call_args.kwargs['vad_filter'], True)

    def test_the_owners_case_the_sentence_starts_when_it_is_spoken(self):
        self.run_with([segment(76.5, 91.9, '哇 这个好 这个好', [word('哇', 76.5, 76.9), word(' 这个', 88.7, 88.9),
                                                              word('好', 88.9, 89.2), word(' 这个', 90.7, 91.0), word('好', 91.0, 91.9)])])
        self.assertEqual(self.lines(), [(76500, 76900, '哇'), (88700, 91900, '这个好 这个好')])

    def test_following_segments_keep_their_order_and_never_overlap(self):
        self.run_with([segment(1., 4., '你好吗', [word('你好', 1., 2.), word('吗', 5., 6.)]),
                       segment(5.5, 8., '很好', [word('很好', 5.5, 8.)])])
        lines = self.lines()
        self.assertEqual([text for _, _, text in lines], ['你好', '吗', '很好'])
        self.assertTrue(all(a_end <= b_start for (_, a_end, _), (b_start, _, _) in zip(lines, lines[1:])))

    def test_a_split_word_with_no_length_is_kept_for_a_moment_not_dropped(self):
        self.run_with([segment(1., 9., '甲乙', [word('甲', 1., 2.), word('乙', 8., 8.)])])
        self.assertEqual(self.lines(), [(1000, 2000, '甲'), (8000, 8200, '乙')])

    def test_a_single_line_with_no_length_still_stops_the_video_as_before(self):
        with self.assertRaisesRegex(ValueError, 'thời gian không hợp lệ'):
            self.run_with([segment(5., 5., '甲', [word('甲', 5., 5.)])])

    def test_segments_without_word_times_give_the_same_lines_as_before(self):
        self.run_with([SimpleNamespace(start=1., end=3., text=' 你好', no_speech_prob=.1, avg_logprob=-.4, compression_ratio=1.2)])
        self.assertEqual(self.lines(), [(1000, 3000, '你好')])


if __name__ == '__main__':
    unittest.main()
