"""28/10/2026: empty stretches are listened to again with a better model (auto mode only); no real inference or ffmpeg."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub import asr, asr_runtime
from teodub.common import Cancelled, hash_file
from teodub.subtitles import read_srt


def backend(count=1):
    return SimpleNamespace(get_cuda_device_count=Mock(return_value=count),
        get_supported_compute_types=Mock(side_effect=lambda device: {'int8', 'float32', 'int8_float32'}))


def seg(start, end, text, no_speech=.1, logprob=-.4, compression=1.2):
    return SimpleNamespace(start=start, end=end, text=text, no_speech_prob=no_speech, avg_logprob=logprob,
                           compression_ratio=compression)


class EmptyStretchTests(unittest.TestCase):
    def test_only_stretches_of_four_seconds_or_more_are_found_including_both_ends(self):
        cues = [dict(start_ms=5000, end_ms=8000, text='a'), dict(start_ms=9000, end_ms=11000, text='b'),
                dict(start_ms=16000, end_ms=17000, text='c')]
        self.assertEqual(asr.empty_stretches(cues, 25.), [(0, 5000), (11000, 16000), (17000, 25000)])
        self.assertEqual(asr.empty_stretches(cues, 19.), [(0, 5000), (11000, 16000)])

    def test_overlapping_or_unsorted_lines_do_not_invent_a_gap(self):
        cues = [dict(start_ms=1000, end_ms=9000, text='a'), dict(start_ms=0, end_ms=2000, text='b')]
        self.assertEqual(asr.empty_stretches(cues, 9.5), [])


class KeepRecoveredTests(unittest.TestCase):
    @staticmethod
    def item(text, no_speech=.1, logprob=-.4, compression=1.2):
        return dict(text=text, start_ms=0, end_ms=1000, no_speech=no_speech, logprob=logprob, compression=compression)

    def kept(self, *items):
        return [item['text'] for item in asr._keep_recovered(list(items))]

    def test_real_lines_and_short_cries_are_kept(self):
        self.assertEqual(self.kept(self.item('这是鲨鱼'), self.item('哇'), self.item('来了')), ['这是鲨鱼', '哇', '来了'])

    def test_lines_the_model_doubts_are_dropped(self):
        self.assertEqual(self.kept(self.item('甲', no_speech=.7), self.item('乙', logprob=-1.4),
                                   self.item('丙', compression=3.), self.item('丁')), ['丁'])
        self.assertEqual(self.kept(self.item('嚇死我了', logprob=-1.06)), ['嚇死我了'])  # A real short cry is kept.

    def test_a_line_without_any_chinese_character_or_with_an_english_word_is_noise(self):
        self.assertEqual(self.kept(self.item('YES SIR'), self.item('...'), self.item('二撒 tones'), self.item('哇 ok')),
                         ['哇 ok'])

    def test_repetition_is_dropped_whether_inside_one_line_or_across_lines(self):
        self.assertEqual(self.kept(self.item('哎呦哎呦哎呦哎呦')), [])
        self.assertEqual(self.kept(self.item('完蛋了'), self.item('完蛋了'), self.item('完蛋了'), self.item('完蛋了')), [])
        self.assertEqual(self.kept(self.item('完蛋了'), self.item('完蛋了')), [])  # Twice in a row is the model looping.
        spread = [dict(self.item('哎呦'), start_ms=1000), dict(self.item('真的'), start_ms=3000),
                  dict(self.item('哎呦'), start_ms=9000)]
        self.assertEqual([item['text'] for item in asr._keep_recovered(spread)], ['哎呦', '真的', '哎呦'])  # Apart: both real.


class RecheckTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root/'source.mp4'
        self.source.write_bytes(b'checked source')
        self.output = self.root/'chinese.srt'
        # First pass: lines at 0-2 s and 12-14 s of a 20 s video; empty stretches 2-12 s and 14-20 s.
        self.small = Mock()
        self.small.transcribe.return_value = (iter([seg(0, 2, '你好'), seg(12, 14, '再见')]), SimpleNamespace(duration=20.))
        self.medium = Mock()
        self.medium.transcribe.side_effect = lambda *args, **kwargs: (iter(self.heard.pop(0)), SimpleNamespace(duration=1))
        self.heard = [[seg(1., 3., '今天我们带了两条'), seg(3.5, 4.5, '哇')], [seg(0.5, 2.5, '这是鲨鱼')]]
        self.models = {'small': self.small, 'medium': self.medium}
        self.factory = Mock(side_effect=lambda name, **kwargs: self.models[name])
        self.enterContext(patch.dict('sys.modules', {'ctranslate2': backend(),
                                                     'faster_whisper': SimpleNamespace(WhisperModel=self.factory)}))
        self.enterContext(patch.object(asr, 'configure_models', return_value=self.root))
        self.enterContext(patch('teodub.models.configure_models', return_value=self.root))
        self.enterContext(patch.object(asr_runtime, 'gpu_memory_mib', return_value=2048))
        self.ffmpeg = self.enterContext(patch('teodub.asr.subprocess.run'))
        self.enterContext(patch('imageio_ffmpeg.get_ffmpeg_exe', return_value='ffmpeg'))

    def texts(self):
        return [(cue['start_ms'], cue['end_ms'], cue['text']) for cue in read_srt(self.output)]

    def test_lines_found_in_the_empty_stretches_join_the_recognition_in_time_order(self):
        result = asr.transcribe(self.source, self.output)
        self.assertEqual(self.texts(), [(0, 2000, '你好'), (3000, 5000, '今天我们带了两条'), (5500, 6500, '哇'),
                                        (12000, 14000, '再见'), (14500, 16500, '这是鲨鱼')])
        self.assertEqual([call.args[0] for call in self.factory.call_args_list], ['small', 'medium'])
        self.assertEqual(result['cue_count'], 5)
        self.assertEqual(result['model'], 'small')  # The recorded main model is unchanged.
        self.assertEqual(result['gap_recheck']['added'], 3)
        self.assertEqual((result['gap_recheck']['stretches'], result['gap_recheck']['listened']), (2, 2))
        self.assertEqual(result['sha256'], hash_file(self.output))
        self.factory.reset_mock()
        self.assertEqual(asr.transcribe(self.source, self.output), result)  # The saved result is reused: no model.
        self.factory.assert_not_called()

    def test_the_second_listen_does_not_use_voice_detection_that_hid_real_speech(self):
        asr.transcribe(self.source, self.output)
        for call in self.medium.transcribe.call_args_list:
            self.assertIs(call.kwargs['vad_filter'], False)
            self.assertEqual(call.kwargs['language'], 'zh')
        self.assertIs(self.small.transcribe.call_args.kwargs['vad_filter'], True)  # The first pass is unchanged.

    def test_each_window_is_cut_from_the_source_at_its_own_time(self):
        asr.transcribe(self.source, self.output)
        starts = [call.args[0][call.args[0].index('-ss')+1] for call in self.ffmpeg.call_args_list]
        lengths = [call.args[0][call.args[0].index('-t')+1] for call in self.ffmpeg.call_args_list]
        self.assertEqual((starts, lengths), (['2.000', '14.000'], ['10.000', '6.000']))

    def test_a_line_outside_its_stretch_is_clipped_to_it(self):
        self.heard = [[seg(0., 99., '很长很长的一句话')], []]
        asr.transcribe(self.source, self.output)
        self.assertEqual(self.texts()[1], (2000, 12000, '很长很长的一句话'))

    def test_a_chosen_model_is_respected_and_nothing_is_listened_to_again(self):
        self.models['small'].transcribe.return_value = (iter([seg(0, 2, '你好'), seg(12, 14, '再见')]),
                                                        SimpleNamespace(duration=20.))
        result = asr.transcribe(self.source, self.output, asr_model='small')
        self.assertEqual([call.args[0] for call in self.factory.call_args_list], ['small'])
        self.assertNotIn('gap_recheck', result)
        self.assertEqual(len(self.texts()), 2)

    def test_no_long_empty_stretch_means_no_second_model(self):
        self.small.transcribe.return_value = (iter([seg(0, 5, '甲'), seg(6, 11, '乙')]), SimpleNamespace(duration=12.))
        result = asr.transcribe(self.source, self.output)
        self.assertEqual([call.args[0] for call in self.factory.call_args_list], ['small'])
        self.assertEqual(result['gap_recheck']['status'], 'no_empty_stretch')
        self.ffmpeg.assert_not_called()

    def test_a_failed_second_listen_keeps_the_first_recognition_and_the_video_goes_on(self):
        def factory(name, **kwargs):
            if name != 'small':
                raise RuntimeError('không nạp được mô hình lớn')
            return self.small
        self.factory.side_effect = factory
        result = asr.transcribe(self.source, self.output)
        self.assertEqual(self.texts(), [(0, 2000, '你好'), (12000, 14000, '再见')])
        self.assertEqual(result['gap_recheck']['status'], 'failed')
        self.assertIn('không nạp được', result['gap_recheck']['message'])
        self.assertEqual(result['cue_count'], 2)

    def test_a_cut_audio_failure_is_also_only_a_note(self):
        self.ffmpeg.side_effect = RuntimeError('ffmpeg trục trặc')
        result = asr.transcribe(self.source, self.output)
        self.assertEqual(len(self.texts()), 2)
        self.assertEqual(result['gap_recheck']['status'], 'failed')

    def test_stopping_during_the_second_listen_stops_the_video(self):
        stop = threading.Event()
        self.ffmpeg.side_effect = lambda *args, **kwargs: stop.set()  # The owner presses Dừng while it listens.
        with self.assertRaises(Cancelled):
            asr.transcribe(self.source, self.output, cancel=stop)
        self.assertFalse(self.output.exists())

    def test_dubious_recoveries_are_counted_and_left_out(self):
        self.heard = [[seg(1., 3., '甲', no_speech=.9), seg(3., 4., '哎呦哎呦哎呦哎呦'), seg(4., 5., '真话')], []]
        result = asr.transcribe(self.source, self.output)
        self.assertEqual([text for _, _, text in self.texts()], ['你好', '真话', '再见'])
        self.assertEqual((result['gap_recheck']['added'], result['gap_recheck']['dropped']), (1, 2))


if __name__ == '__main__':
    unittest.main()
