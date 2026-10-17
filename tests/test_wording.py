"""Literal Vietnamese wording rules and immutable subtitle provenance."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unicodedata
import unittest
from unittest.mock import patch

from teodub import wording
from teodub.common import Cancelled, hash_file
from teodub.srt import write_srt
from teodub.subtitles import read_srt


VERSION = 'vi-wording-v1'


def rule(source, replacement):
    return dict(source=source, replacement=replacement)


def config(*rules, enabled=True):
    return dict(wording=dict(version=VERSION, enabled=enabled, rules=list(rules)))


def payload(report):
    return {key: value for key, value in report.items() if key not in ('report', 'report_sha256')}


class WordingRulesTests(unittest.TestCase):
    def test_missing_settings_keep_legacy_disabled_configuration(self):
        self.assertEqual(wording.validate_wording({}),
                         dict(version=VERSION, enabled=False, rules=[]))

    def test_valid_configuration_is_canonical_and_does_not_mutate_input(self):
        selected = config(rule('bán kính', 'phạm vi'), rule('bán', 'chào bán'))
        before = copy.deepcopy(selected)
        result = wording.validate_wording(selected)
        self.assertEqual(result, selected['wording'])
        self.assertEqual(selected, before)
        result['rules'][0]['replacement'] = 'changed'
        self.assertEqual(selected, before)

    def test_at_most_fifty_rules_are_accepted(self):
        rules = [rule(f'từ số {i}', f'lời số {i}') for i in range(50)]
        self.assertEqual(len(wording.validate_wording(config(*rules))['rules']), 50)
        with self.assertRaises(ValueError):
            wording.validate_wording(config(*rules, rule('thêm', 'nữa')))

    def test_duplicate_sources_ignore_case_and_unicode_composition(self):
        for duplicate in ('BÁN KÍNH', unicodedata.normalize('NFD', 'bán kính')):
            with self.subTest(duplicate=duplicate), self.assertRaises(ValueError):
                wording.validate_wording(config(rule('bán kính', 'phạm vi'), rule(duplicate, 'cự ly')))

    def test_empty_sources_and_replacements_are_rejected(self):
        for entry in (rule('', 'từ'), rule('   ', 'từ'), rule('từ', ''), rule('từ', '  ')):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                wording.validate_wording(config(entry))

    def test_enabled_without_rules_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'chưa có quy tắc'):
            wording.validate_wording(config())

    def test_terms_are_normalized_and_limited_to_safe_vietnamese_text(self):
        selected = config(rule(unicodedata.normalize('NFD', '  bán   kính  '), '  phạm   vi  '))
        self.assertEqual(wording.validate_wording(selected)['rules'], [rule('bán kính', 'phạm vi')])
        for source, replacement in (('a'*101, 'từ'), ('từ', 'a'*101), ('từ\x00', 'lời'),
                                    ('từ', 'lời\x01'), ('中文', 'lời'), ('từ', '中文')):
            with self.subTest(source=source, replacement=replacement), self.assertRaises(ValueError):
                wording.validate_wording(config(rule(source, replacement)))

    def test_invalid_configuration_types_and_versions_are_rejected(self):
        invalid = [dict(wording=None), dict(wording=[]), dict(wording='bán => mua'),
                   dict(wording=dict(version='future-version', enabled=True, rules=[])),
                   dict(wording=dict(version=VERSION, enabled='true', rules=[])),
                   dict(wording=dict(version=VERSION, enabled=1, rules=[])),
                   dict(wording=dict(version=VERSION, enabled=True, rules='bán => mua')),
                   config('not a rule'), config(dict(source='bán')),
                   config(rule(1, 'mua')), config(rule('bán', None))]
        for selected in invalid:
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                wording.validate_wording(selected)

    def test_parse_ascii_and_unicode_arrows_with_blank_lines(self):
        rules = [rule('bán kính', 'phạm vi'), rule('bán', 'chào bán')]
        self.assertEqual(wording.parse_rule_text('\n bán kính => phạm vi \n\n bán → chào bán \n'), rules)
        self.assertEqual(wording.parse_rule_text(' \n\n'), [])

    def test_formatted_rules_round_trip_without_losing_accents(self):
        rules = [rule('bán kính', 'phạm vi'), rule('Vân Nam', 'miền núi')]
        formatted = wording.format_rule_text(rules)
        self.assertIsInstance(formatted, str)
        self.assertEqual(wording.parse_rule_text(formatted), rules)

    def test_parser_rejects_missing_arrow_empty_values_and_duplicates(self):
        for text in ('bán kính', '=> phạm vi', 'bán kính =>', 'bán => mua\nBÁN → mua khác'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                wording.parse_rule_text(text)

    def test_rule_sources_cannot_contain_delimiters_that_break_round_trip(self):
        for entry in (rule('bán => mua', 'lời'), rule('bán → mua', 'lời')):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                wording.validate_wording(config(entry))

    def test_replacement_delimiters_round_trip_as_literal_replacement_text(self):
        rules = [rule('bán', 'mua => thuê'), rule('giá', 'mức → số')]
        self.assertEqual(wording.parse_rule_text(wording.format_rule_text(rules)), rules)

    def test_invisible_unicode_control_characters_are_rejected(self):
        for character in ('\u200b', '\u202e', '\u2066', '\ufeff'):
            with self.subTest(character=character), self.assertRaises(ValueError):
                wording.validate_wording(config(rule('bán'+character, 'mua')))

    def test_whole_words_keep_other_vietnamese_accents_and_longer_tokens(self):
        source = 'bán, bản, bán kính, bánkính; x_bán bán2 2bán, (bán).'
        output, changes = wording.rewrite_text(source, [rule('bán', 'mua')])
        self.assertEqual(output, 'mua, bản, bán kính, bánkính; x_bán bán2 2bán, (mua).')
        self.assertEqual(len(changes), 2)

    def test_generic_terms_do_not_change_protected_compounds(self):
        source = 'bán kính, bán cầu, bán đảo, bán dẫn, bán tự động, bán; giá trị, giá đỡ, giá thể, giá.'
        output, changes = wording.rewrite_text(source, [rule('bán', 'báng'), rule('giá', 'chi phí')])
        self.assertEqual(output, 'bán kính, bán cầu, bán đảo, bán dẫn, bán tự động, báng; giá trị, giá đỡ, giá thể, chi phí.')
        self.assertEqual(len(changes), 2)

    def test_explicit_phrase_rule_can_change_a_protected_compound(self):
        output, changes = wording.rewrite_text('bán kính và giá trị.',
            [rule('bán', 'báng'), rule('giá', 'chi phí'), rule('bán kính', 'phạm vi'), rule('giá trị', 'trị giá')])
        self.assertEqual(output, 'phạm vi và trị giá.')
        self.assertEqual(len(changes), 2)

    def test_longest_phrase_wins_regardless_of_rule_order(self):
        entries = [rule('bán', 'mua'), rule('bán kính', 'phạm vi')]
        for rules in (entries, entries[::-1]):
            with self.subTest(rules=rules):
                output, changes = wording.rewrite_text('bán kính và bán.', rules)
                self.assertEqual(output, 'phạm vi và mua.')
                self.assertEqual(len(changes), 2)

    def test_replacements_are_simultaneous_and_never_cascade(self):
        output, changes = wording.rewrite_text('bán mua bán.', [rule('bán', 'mua'), rule('mua', 'thuê')])
        self.assertEqual(output, 'mua thuê mua.')
        self.assertEqual(len(changes), 3)

    def test_preserves_lower_sentence_title_and_upper_case(self):
        source = 'bán kính. Bán kính. Bán Kính. BÁN KÍNH.'
        output, changes = wording.rewrite_text(source, [rule('bán kính', 'phạm vi')])
        self.assertEqual(output, 'phạm vi. Phạm vi. Phạm Vi. PHẠM VI.')
        self.assertEqual(len(changes), 4)

    def test_decomposed_accents_match_canonical_phrase(self):
        decomposed = unicodedata.normalize('NFD', 'Bán kính và bản vẽ.')
        output, changes = wording.rewrite_text(decomposed, [rule('bán kính', 'phạm vi')])
        self.assertEqual(output, 'Phạm vi và bản vẽ.')
        self.assertEqual(len(changes), 1)

    def test_rule_text_with_regex_characters_is_literal(self):
        output, changes = wording.rewrite_text('giá 1.000 và giá 1x000.', [rule('1.000', 'một nghìn')])
        self.assertEqual(output, 'giá một nghìn và giá 1x000.')
        self.assertEqual(len(changes), 1)

    def test_unmatched_text_and_rules_remain_unmodified(self):
        rules = [rule('bán kính', 'phạm vi')]
        before = copy.deepcopy(rules)
        output, changes = wording.rewrite_text('Bản vẽ giữ nguyên.', rules)
        self.assertEqual((output, changes), ('Bản vẽ giữ nguyên.', []))
        self.assertEqual(rules, before)


class WordingSubtitleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='teodub-wording-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.raw = self.root/'bản dịch gốc.srt'
        self.output = self.root/'lời đọc.srt'
        self.cues = [dict(start_ms=50, end_ms=450, text='Bán kính là mười mét.'),
                     dict(start_ms=500, end_ms=900, text='Bản vẽ giữ nguyên.'),
                     dict(start_ms=1050, end_ms=1800, text='BÁN KÍNH và bán.')]
        write_srt(self.raw, self.cues)
        self.raw_bytes = self.raw.read_bytes()
        self.config = config(rule('bán kính', 'phạm vi'), rule('bán', 'chào bán'))

    def adapt(self, **kwargs):
        return wording.adapt_subtitles(self.raw, self.output, self.config, **kwargs)

    def report_path(self):
        paths = list(self.root.glob('*.wording.json'))
        self.assertEqual(len(paths), 1)
        return paths[0]

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_disabled_rules_do_not_create_files_or_read_the_source(self):
        for selected in ({}, config(enabled=False), config(rule('bán', 'mua'), enabled=False)):
            with self.subTest(selected=selected):
                result = wording.adapt_subtitles(self.root/'does-not-exist.srt', self.output, selected)
                self.assertIsNone(result)
                self.assertFalse(self.output.exists())
                self.assertEqual(list(self.root.glob('*.wording.json')), [])

    def test_independent_adaptation_preserves_original_bytes_timing_and_unchanged_cues(self):
        report = self.adapt()
        expected = [dict(self.cues[0], text='Phạm vi là mười mét.'), self.cues[1],
                    dict(self.cues[2], text='PHẠM VI và chào bán.')]
        self.assertEqual(read_srt(self.output), expected)
        self.assertEqual(self.raw.read_bytes(), self.raw_bytes)
        self.assertNotEqual(self.raw.resolve(), self.output.resolve())
        self.assertEqual(wording.verify_wording(self.output, self.raw, self.config), report)
        self.assertTrue(self.report_path().is_file())

    def test_report_binds_raw_output_rules_and_each_changed_cue(self):
        report = self.adapt()
        self.assertEqual(report['version'], 1)
        self.assertEqual(report['engine'], VERSION)
        self.assertEqual(report['raw_path'], str(self.raw.resolve()))
        self.assertEqual(report['raw_sha256'], hash_file(self.raw))
        self.assertEqual(report['output'], str(self.output.resolve()))
        self.assertEqual(report['output_sha256'], hash_file(self.output))
        self.assertEqual(report['settings'], wording.validate_wording(self.config))
        self.assertEqual(len(report['settings_sha256']), 64)
        self.assertEqual(report['report_sha256'], hash_file(self.report_path()))
        self.assertEqual(report['cue_count'], len(self.cues))
        self.assertEqual([row['id'] for row in report['changes']], [1, 3])
        for row in report['changes']:
            raw = self.cues[row['id']-1]['text']
            adapted, changes = wording.rewrite_text(raw, self.config['wording']['rules'])
            self.assertEqual(row, dict(id=row['id'], before=raw, after=adapted, replacements=changes))
            self.assertTrue(row['replacements'])
        saved = json.loads(self.report_path().read_text(encoding='utf-8'))
        self.assertEqual(saved, payload(report))

    def test_no_matches_still_records_an_independent_verified_output(self):
        self.config = config(rule('không có', 'từ thay'))
        report = self.adapt()
        self.assertEqual(read_srt(self.output), self.cues)
        self.assertEqual(wording.verify_wording(self.output), report)
        self.assertEqual(self.raw.read_bytes(), self.raw_bytes)

    def test_unchanged_reuse_is_read_only(self):
        first = self.adapt()
        before = self.snapshot()
        mtimes = {p: p.stat().st_mtime_ns for p in (self.raw, self.output, self.report_path())}
        second = self.adapt()
        self.assertEqual(second, first)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual({p: p.stat().st_mtime_ns for p in mtimes}, mtimes)

    def test_raw_path_cannot_be_the_adapted_output(self):
        with self.assertRaises(ValueError):
            wording.adapt_subtitles(self.raw, self.raw, self.config)
        self.assertEqual(self.raw.read_bytes(), self.raw_bytes)
        self.assertEqual(list(self.root.glob('*.wording.json')), [])

    def test_unrelated_existing_output_is_never_overwritten(self):
        self.output.write_text('human-edited evidence', encoding='utf-8')
        before = self.snapshot()
        with self.assertRaises((ValueError, FileExistsError)):
            self.adapt()
        self.assertEqual(self.snapshot(), before)

    def test_output_tamper_fails_verification_and_reuse_without_repair(self):
        self.adapt()
        self.output.write_text(self.output.read_text(encoding='utf-8').replace('Phạm vi', 'Lời khác'), encoding='utf-8')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            wording.verify_wording(self.output, self.raw, self.config)
        with self.assertRaises(ValueError):
            self.adapt()
        self.assertEqual(self.snapshot(), before)

    def test_raw_tamper_fails_verification_and_reuse_without_repair(self):
        self.adapt()
        self.raw.write_text(self.raw.read_text(encoding='utf-8').replace('Bản vẽ', 'Bản khác'), encoding='utf-8')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            wording.verify_wording(self.output)
        with self.assertRaises(ValueError):
            self.adapt()
        self.assertEqual(self.snapshot(), before)

    def test_changed_rule_table_cannot_reuse_or_overwrite_existing_artifacts(self):
        self.adapt()
        before = self.snapshot()
        changed = config(rule('bán kính', 'cự ly'), rule('bán', 'chào bán'))
        with self.assertRaises(ValueError):
            wording.verify_wording(self.output, self.raw, changed)
        with self.assertRaises(ValueError):
            wording.adapt_subtitles(self.raw, self.output, changed)
        self.assertEqual(self.snapshot(), before)

    def test_another_raw_path_with_identical_bytes_is_not_the_bound_source(self):
        self.adapt()
        another = self.root/'source-copy.srt'
        another.write_bytes(self.raw_bytes)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            wording.verify_wording(self.output, another, self.config)
        with self.assertRaises(ValueError):
            wording.adapt_subtitles(another, self.output, self.config)
        self.assertEqual(self.snapshot(), before)

    def test_report_is_required_and_malformed_report_is_never_replaced(self):
        self.adapt()
        report = self.report_path()
        report.write_text('{broken', encoding='utf-8')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            wording.verify_wording(self.output)
        with self.assertRaises(ValueError):
            self.adapt()
        self.assertEqual(self.snapshot(), before)

    def test_forged_output_digest_still_fails_recomputed_text_and_timing_checks(self):
        for mutation in ('text', 'timing'):
            with self.subTest(mutation=mutation):
                report = self.adapt()
                original_output = self.output.read_bytes()
                original_report = self.report_path().read_bytes()
                value = self.output.read_text(encoding='utf-8')
                value = value.replace('Phạm vi', 'Lời khác') if mutation == 'text' else value.replace('00:00:00,050', '00:00:00,060')
                self.output.write_text(value, encoding='utf-8')
                report = payload(report)
                report['output_sha256'] = hash_file(self.output)
                self.report_path().write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    wording.verify_wording(self.output, self.raw, self.config)
                with self.assertRaises(ValueError):
                    self.adapt()
                self.assertEqual(self.snapshot(), before)
                self.output.write_bytes(original_output)
                self.report_path().write_bytes(original_report)

    def test_forged_raw_digest_still_fails_recomputed_subtitle_comparison(self):
        report = self.adapt()
        self.raw.write_text(self.raw.read_text(encoding='utf-8').replace('Bản vẽ', 'Bản khác'), encoding='utf-8')
        report = payload(report)
        report['raw_sha256'] = hash_file(self.raw)
        self.report_path().write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            wording.verify_wording(self.output)
        with self.assertRaises(ValueError):
            self.adapt()
        self.assertEqual(self.snapshot(), before)

    def test_report_rule_or_change_tamper_is_not_accepted_with_valid_file_digests(self):
        for mutation in ('rules', 'changes', 'engine', 'cue_count'):
            with self.subTest(mutation=mutation):
                report = self.adapt()
                original = self.report_path().read_bytes()
                report = payload(report)
                if mutation == 'rules':
                    report['settings']['rules'][0]['replacement'] = 'cự ly'
                elif mutation == 'changes':
                    report['changes'][0]['id'] = 2
                elif mutation == 'engine':
                    report['engine'] = 'unsupported'
                else:
                    report['cue_count'] += 1
                self.report_path().write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    wording.verify_wording(self.output)
                with self.assertRaises(ValueError):
                    self.adapt()
                self.assertEqual(self.snapshot(), before)
                self.report_path().write_bytes(original)

    def test_report_change_during_verification_is_rejected(self):
        self.adapt()
        report_path = self.report_path()
        original_verify = wording._verify
        def changed_report(*args, **kwargs):
            result = original_verify(*args, **kwargs)
            with report_path.open('a', encoding='utf-8') as file:
                file.write('\n')
            return result
        before = self.raw.read_bytes(), self.output.read_bytes()
        with patch.object(wording, '_verify', side_effect=changed_report):
            with self.assertRaises(ValueError):
                wording.verify_wording(self.output, self.raw, self.config)
        self.assertEqual((self.raw.read_bytes(), self.output.read_bytes()), before)

    def test_missing_report_does_not_make_an_existing_output_safe_to_overwrite(self):
        self.adapt()
        self.report_path().unlink()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            wording.verify_wording(self.output)
        with self.assertRaises((ValueError, FileExistsError)):
            self.adapt()
        self.assertEqual(self.snapshot(), before)

    def test_cancel_before_start_does_not_publish_or_modify_any_evidence(self):
        cancel = threading.Event()
        cancel.set()
        before = self.snapshot()
        with self.assertRaises(Cancelled):
            self.adapt(cancel=cancel)
        self.assertEqual(self.snapshot(), before)
        cancel.clear()
        self.adapt(cancel=cancel)
        self.assertEqual(self.raw.read_bytes(), self.raw_bytes)
        wording.verify_wording(self.output, self.raw, self.config)

    def test_cancel_after_rewriting_cue_does_not_publish_partial_subtitles(self):
        cancel = threading.Event()
        real_rewrite = wording.rewrite_text
        def stop_after_rewrite(*args, **kwargs):
            result = real_rewrite(*args, **kwargs)
            cancel.set()
            return result
        with patch.object(wording, 'rewrite_text', side_effect=stop_after_rewrite):
            with self.assertRaises(Cancelled):
                self.adapt(cancel=cancel)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.raw.read_bytes(), self.raw_bytes)
        cancel.clear()
        self.adapt(cancel=cancel)
        wording.verify_wording(self.output, self.raw, self.config)

    def test_publication_gap_before_srt_recovers_from_pending_evidence(self):
        original_publish = wording._publish
        def fail_output(source, target):
            if Path(target) == self.output:
                raise OSError('synthetic SRT publication failure')
            return original_publish(source, target)
        with patch.object(wording, '_publish', side_effect=fail_output):
            with self.assertRaisesRegex(OSError, 'publication failure'):
                self.adapt()
        self.assertFalse(self.output.exists())
        pending = self.output.with_suffix('.wording.pending.json')
        self.assertTrue(pending.is_file())
        before = pending.read_bytes()
        report = self.adapt()
        self.assertFalse(pending.exists())
        self.assertEqual(json.loads(before), payload(report))
        self.assertEqual(self.raw.read_bytes(), self.raw_bytes)
        wording.verify_wording(self.output, self.raw, self.config)

    def test_publication_gap_after_srt_recovers_without_overwriting_the_srt(self):
        original_json = wording._create_json
        report_path = self.output.with_suffix('.wording.json')
        def fail_final_report(path, value):
            if Path(path) == report_path:
                raise OSError('synthetic report publication failure')
            return original_json(path, value)
        with patch.object(wording, '_create_json', side_effect=fail_final_report):
            with self.assertRaisesRegex(OSError, 'publication failure'):
                self.adapt()
        self.assertTrue(self.output.is_file())
        self.assertFalse(report_path.exists())
        before = (self.output.read_bytes(), self.output.stat().st_mtime_ns)
        self.adapt()
        self.assertEqual((self.output.read_bytes(), self.output.stat().st_mtime_ns), before)
        self.assertFalse(self.output.with_suffix('.wording.pending.json').exists())
        wording.verify_wording(self.output, self.raw, self.config)

    def test_pending_publication_with_modified_srt_is_not_repaired(self):
        original_json = wording._create_json
        report_path = self.output.with_suffix('.wording.json')
        def fail_final_report(path, value):
            if Path(path) == report_path:
                raise OSError('synthetic report publication failure')
            return original_json(path, value)
        with patch.object(wording, '_create_json', side_effect=fail_final_report):
            with self.assertRaises(OSError):
                self.adapt()
        self.output.write_text(self.output.read_text(encoding='utf-8').replace('Phạm vi', 'Lời khác'), encoding='utf-8')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.adapt()
        self.assertEqual(self.snapshot(), before)

    def test_cancel_after_srt_publication_keeps_recoverable_evidence_and_never_overwrites(self):
        cancel = threading.Event()
        original_publish = wording._publish
        def publish_then_cancel(source, target):
            result = original_publish(source, target)
            if Path(target) == self.output:
                cancel.set()
            return result
        with patch.object(wording, '_publish', side_effect=publish_then_cancel):
            with self.assertRaises(Cancelled):
                self.adapt(cancel=cancel)
        self.assertTrue(self.output.exists())
        self.assertTrue(self.output.with_suffix('.wording.pending.json').exists())
        before = (self.output.read_bytes(), self.output.stat().st_mtime_ns)
        cancel.clear()
        self.adapt(cancel=cancel)
        self.assertEqual((self.output.read_bytes(), self.output.stat().st_mtime_ns), before)
        self.assertEqual(self.raw.read_bytes(), self.raw_bytes)
        wording.verify_wording(self.output, self.raw, self.config)


if __name__ == '__main__':
    unittest.main()
