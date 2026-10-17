"""Phrase grouping without invented words, lost captions, or unsafe boundaries."""
import copy
import unittest

from teodub.speech_plan import PLAN_VERSION, STYLES, build_speech_plan, validate_style


def cue(text, start=0, end=1000, **extra):
    return dict(start_ms=start, end_ms=end, text=text, **extra)


class SpeechPlanTests(unittest.TestCase):
    def test_actual_unfinished_geographic_clause_is_one_phrase(self):
        source = [cue('Dù bạn ở Vân Nam, Tứ Xuyên, Quý Châu', 39820, 41500),
                  cue('hay ở Trùng Khánh,', 41500, 42360),
                  cue('trời âm u, mưa vẫn không mất điện.', 42360, 43440)]
        plan = build_speech_plan(source)
        self.assertEqual(plan['version'], PLAN_VERSION)
        self.assertEqual(plan['settings'], STYLES['conversation'])
        self.assertEqual(plan['source_cue_count'], 3)
        self.assertEqual(plan['groups'], [dict(start_ms=39820, end_ms=43440,
            text='Dù bạn ở Vân Nam, Tứ Xuyên, Quý Châu hay ở Trùng Khánh, trời âm u, mưa vẫn không mất điện.',
            source_ids=[1, 2, 3])])

    def test_all_source_indices_occur_once_in_order_and_words_are_preserved(self):
        source = [cue('  Giá   hôm nay', 0, 1000), cue('vẫn là 10.500 đồng,', 1000, 2000),
                  cue('rất hợp lý nhé!', 2100, 3000), cue('Mua tại đây.', 3100, 4000),
                  cue('Mọi người\nđều có thể', 4010, 4900), cue('dùng ngay', 4900, 5500)]
        for style in STYLES:
            with self.subTest(style=style):
                plan = build_speech_plan(source, style)
                self.assertEqual([i for group in plan['groups'] for i in group['source_ids']], list(range(1, 7)))
                self.assertEqual(' '.join(group['text'] for group in plan['groups']).split(),
                                 ' '.join(item['text'] for item in source).split())
                self.assertTrue(all(a['end_ms'] <= b['start_ms'] for a, b in zip(plan['groups'], plan['groups'][1:])))

    def test_legacy_preserves_exact_text_and_timing(self):
        source = [cue('  Giữ  nguyên\n văn bản ', 17, 1123), cue('và dấu, câu', 1123, 2300)]
        groups = build_speech_plan(source, 'legacy')['groups']
        self.assertEqual(groups, [dict(item, source_ids=[i]) for i, item in enumerate(source, 1)])

    def test_source_digest_and_plan_are_deterministic_and_input_is_untouched(self):
        source = [cue('Một đoạn', 0, 1000), cue('nói liên tục.', 1100, 2100)]
        before = copy.deepcopy(source)
        first = build_speech_plan(source)
        self.assertEqual(build_speech_plan(source), first)
        self.assertEqual(source, before)
        first['groups'][0]['source_ids'].append(999)
        self.assertEqual(source, before)
        self.assertNotEqual(first, build_speech_plan(source))
        changed = copy.deepcopy(source)
        changed[1]['text'] = 'nói nội dung khác.'
        self.assertNotEqual(first['source_cues_sha256'], build_speech_plan(changed)['source_cues_sha256'])

    def test_speaker_metadata_changes_source_identity(self):
        source = [cue('Xin chào', 0, 1000), cue('mọi người.', 1000, 2000)]
        named = copy.deepcopy(source)
        named[1]['speaker'] = 'Lan'
        self.assertNotEqual(build_speech_plan(source)['source_cues_sha256'],
                            build_speech_plan(named)['source_cues_sha256'])

    def test_sentence_punctuation_including_closing_quotes_is_a_boundary(self):
        for ending in ('.', '!', '?', '…', '...', '。”', '!”', '?"', '！', '？', '.)'):
            with self.subTest(ending=ending):
                source = [cue('Một câu'+ending), cue('Câu tiếp theo', 1000, 2000)]
                self.assertEqual(len(build_speech_plan(source)['groups']), 2)

    def test_explicit_dialogue_list_or_named_speaker_never_joins_either_side(self):
        for text in ('- Xin chào', '– Xin chào', '— Xin chào', '“Xin chào', '"Xin chào',
                     'Lan: Xin chào', 'Nam:', 'Người 2： Xin chào', 'Một dòng\n- Người khác nói', '1. Xin chào'):
            with self.subTest(text=text):
                source = [cue('Câu đầu', 0, 1000), cue(text, 1000, 2000), cue('câu cuối', 2000, 3000)]
                self.assertEqual([g['source_ids'] for g in build_speech_plan(source)['groups']], [[1], [2], [3]])

    def test_explicit_speaker_fields_keep_boundaries(self):
        for info in ({'speaker':'Lan'}, {'speaker_id':0}, {'speaker_id':'A'}, {'dialogue':True}, {'new_speaker':True}):
            with self.subTest(info=info):
                source = [cue('Một người', 0, 1000), cue('người khác', 1000, 2000, **info), cue('câu sau', 2000, 3000)]
                self.assertEqual(len(build_speech_plan(source)['groups']), 3)

    def test_600ms_gap_allowed_but_601ms_is_a_boundary(self):
        for gap, expected in ((600, 1), (601, 2)):
            with self.subTest(gap=gap):
                source = [cue('Đây là'), cue('một câu.', 1000+gap, 2000+gap)]
                self.assertEqual(len(build_speech_plan(source)['groups']), expected)

    def test_merge_has_at_most_three_cues(self):
        source = [cue('từ'+str(i), i*1000, (i+1)*1000) for i in range(5)]
        self.assertEqual([g['source_ids'] for g in build_speech_plan(source)['groups']], [[1, 2, 3], [4, 5]])

    def test_character_limit_includes_joining_space(self):
        for right_length, expected in ((109, 1), (110, 2)):
            with self.subTest(length=right_length):
                source = [cue('a'*110), cue('b'*right_length, 1000, 2000)]
                self.assertEqual(len(build_speech_plan(source)['groups']), expected)

    def test_span_limit_is_twelve_seconds(self):
        for end, expected in ((12000, 1), (12001, 2)):
            with self.subTest(end=end):
                source = [cue('Một câu dài', 0, 6000), cue('tiếp tục.', 6000, end)]
                self.assertEqual(len(build_speech_plan(source)['groups']), expected)

    def test_oversized_original_is_standalone_and_never_truncated(self):
        text = 'Nguyên lời '*50
        source = [cue(text, 0, 15000), cue('câu sau', 15000, 16000)]
        result = build_speech_plan(source)['groups']
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['text'].split(), text.split())
        self.assertEqual(result[0]['source_ids'], [1])
        self.assertEqual(result[0]['end_ms'], 15000)

    def test_invalid_empty_timestamps_order_overlap_and_missing_text_fail(self):
        invalid = [[], None, [cue('')], [cue('   ')], [cue(None)], [cue('x', -1, 1000)],
                   [cue('x', 10, 10)], [cue('x', True, 1000)], [cue('x', 0., 1000)],
                   [cue('x', 0, '1000')], [dict(text='x')], ['text'],
                   [cue('one', 1000, 2000), cue('two', 500, 900)],
                   [cue('one', 0, 1000), cue('two', 999, 2000)]]
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(ValueError):
                build_speech_plan(source)

    def test_unknown_style_fails_and_validated_settings_are_copies(self):
        for style in ('', 'happy', None, 1, {}, True):
            with self.subTest(style=style), self.assertRaises(ValueError):
                build_speech_plan([cue('Đủ lời.')], style)
        settings = validate_style('conversation')
        settings['gap_ms'] = -1
        self.assertEqual(STYLES['conversation']['gap_ms'], 180)

    def test_styles_only_change_declared_settings_not_words_or_grouping(self):
        source = [cue('Một đoạn', 0, 1000), cue('nói liên tục.', 1000, 2000)]
        base = build_speech_plan(source, 'conversation')
        for style in ('story', 'advertising'):
            plan = build_speech_plan(source, style)
            self.assertEqual(plan['groups'], base['groups'])
            self.assertEqual(plan['source_cues_sha256'], base['source_cues_sha256'])
        self.assertEqual(validate_style('legacy'), {'label':'Theo bản cũ', 'speech_speed':1., 'gap_ms':100})


if __name__ == '__main__':
    unittest.main()
