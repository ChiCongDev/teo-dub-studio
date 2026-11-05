"""Âm lượng and "Nghe thử 20 giây" (owner 05/11/2026): the two export levels are saved for one link only, a finished video made again
with only new levels reuses its voice files (no new voice) when they are exactly right and makes the voice again when they are not,
and the 20-second listening test is a real export of a finished video with the tried levels. Local fixtures, real FFmpeg; no AI."""
import json
import os
from pathlib import Path
import time
from unittest.mock import Mock, patch

import numpy as np

from teodub import audio_preview, campaign, tts
from teodub.campaign_settings import (next_settings, prepare_next_job, reuse_voice_cache, save_next_link_settings,
                                      save_next_voice_settings)
from teodub.common import atomic_json, hash_file
from teodub.subtitles import read_vietnamese_srt
from tests.test_campaign_exclude import Base
from tests.test_campaign_redo import NEW_VOICE, Redo
from tests.test_render import source_video, tone_clip

SRT = ('1\n00:00:00,000 --> 00:00:01,500\nXin chào các bạn.\n\n'
       '2\n00:00:01,600 --> 00:00:03,000\nHôm nay trời đẹp lắm.\n')


class LevelSettingsTests(Base):
    def test_levels_are_saved_for_this_link_and_the_voice_is_kept(self):
        a = self.make_link(2)
        other = self.make_link(1)
        record = save_next_link_settings(self.root, a, original_gain=.3, voice_gain=1.5)
        self.assertEqual((record['settings']['original_gain'], record['settings']['voice_gain']), (.3, 1.5))
        self.assertEqual(record['settings']['voice'], 'Mai Anh')
        self.assertIsNone(next_settings(other))
        only_one = save_next_link_settings(self.root, a, original_gain=0)  # 0 mutes the original sound on purpose.
        self.assertEqual((only_one['settings']['original_gain'], only_one['settings']['voice_gain']), (0.0, 1.5))

    def test_a_level_outside_zero_to_two_hundred_percent_is_refused_and_nothing_is_saved(self):
        a = self.make_link(1)
        for bad in (-.01, 2.01, float('nan'), True, '1'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                save_next_link_settings(self.root, a, voice_gain=bad)
        self.assertIsNone(next_settings(a))

    def test_the_voice_only_call_is_unchanged(self):
        a = self.make_link(1)
        self.assertEqual(save_next_voice_settings(self.root, a, NEW_VOICE)['settings']['voice'], 'Trúc Ly')
        with self.assertRaises(ValueError):
            save_next_voice_settings(self.root, a, dict(NEW_VOICE, extra=1))
        with self.assertRaises(ValueError):
            save_next_voice_settings(self.root, a, None)


class ReuseVoiceTests(Redo):
    """A finished video with a real voice cache, then new levels (or a new voice) for it."""

    def finished_with_voice(self):
        a = self.finished(1)
        job = Path(self.item(a, 1)['job'])
        source = self.root/'goc.mp4'
        source.write_bytes(b'source video')
        captions = job/'captions'
        captions.mkdir()
        (captions/'vietnamese.srt').write_text(SRT, encoding='utf-8')
        state = campaign.read_json(job/'state.json')
        state.update(source=dict(path=str(source), sha256=hash_file(source)),
                     captions=dict(path=str(captions/'vietnamese.srt'), sha256=hash_file(captions/'vietnamese.srt'), count=2))
        atomic_json(job/'state.json', state)
        self.cues = read_vietnamese_srt(captions/'vietnamese.srt')
        engine = Mock()
        engine.infer.return_value = (.1*np.sin(2*np.pi*220*np.arange(24000)/48000)).astype('float32')
        with patch.object(tts, 'get_model', return_value=engine):
            tts.synthesize(self.cues, 'Mai Anh', job/'voice', reading_style='legacy')
        self.assertEqual(engine.infer.call_count, 2)
        return a, job

    def synthesize_again(self, job):
        engine = Mock(side_effect=AssertionError('a new voice was made'))
        messages = []
        with patch.object(tts, 'get_model', return_value=Mock(infer=engine)):
            clips = tts.synthesize(self.cues, 'Mai Anh', job/'voice', reading_style='legacy',
                                   progress=lambda message, percent=None: messages.append(message))
        return clips, messages

    def test_new_levels_reuse_the_voice_files_and_the_copy_is_accepted_by_the_voice_step(self):
        a, job = self.finished_with_voice()
        old_files = sorted(p.name for p in (job/'voice').iterdir())
        save_next_link_settings(self.root, a, original_gain=.3, voice_gain=1.4, allow_finished=True)
        target = prepare_next_job(a, self.item(a, 1))
        self.assertNotEqual(target, job)
        self.assertTrue((target/'voice'/'manifest.json').is_file())
        clips, messages = self.synthesize_again(target)  # The engine is not allowed to speak: every line is reused.
        self.assertEqual(len(clips), 2)
        self.assertEqual(sum(m.startswith('Dùng lại giọng câu') for m in messages), 2)
        self.assertTrue(all(Path(c['path']).parent == target/'voice' for c in clips))
        self.assertEqual(sorted(p.name for p in (target/'voice').glob('*.wav')), ['00001.wav', '00002.wav'])
        self.assertEqual(sorted(p.name for p in (job/'voice').iterdir()), old_files)  # The old job is untouched.

    def test_a_new_voice_makes_the_voice_again(self):
        a, job = self.finished_with_voice()
        save_next_link_settings(self.root, a, selection=NEW_VOICE, original_gain=.3, allow_finished=True)
        target = prepare_next_job(a, self.item(a, 1))
        self.assertFalse((target/'voice').exists())

    def test_lines_that_changed_since_the_voice_was_made_are_not_reused(self):
        a, job = self.finished_with_voice()
        request = campaign.read_json(job/'voice'/'request.json')
        request['cues'][0]['end_ms'] += 40  # Not the lines the new job will read.
        atomic_json(job/'voice'/'request.json', request)
        save_next_link_settings(self.root, a, voice_gain=1.2, allow_finished=True)
        self.assertFalse((prepare_next_job(a, self.item(a, 1))/'voice').exists())

    def test_an_incomplete_cache_or_an_existing_target_is_left_alone(self):
        a, job = self.finished_with_voice()
        srt = job/'captions'/'vietnamese.srt'
        (job/'voice'/'manifest.json').rename(job/'voice'/'manifest.gone')
        self.assertFalse(reuse_voice_cache(job, self.root/'x', srt, 'legacy'))
        (job/'voice'/'manifest.gone').rename(job/'voice'/'manifest.json')
        (self.root/'y'/'voice').mkdir(parents=True)
        self.assertFalse(reuse_voice_cache(job, self.root/'y', srt, 'legacy'))
        self.assertTrue(reuse_voice_cache(job, self.root/'z', srt, 'legacy'))
        self.assertFalse((self.root/'z'/'voice.copying').exists())


class PreviewTests(Redo):
    def material(self):
        directory = self.root/'listen'
        directory.mkdir()
        source = directory/'nguồn.mp4'
        source_video(source, duration=1.5)
        clips = [tone_clip(directory/f'giọng {i}.wav', i) for i in range(1, 4)]
        cues = [dict(start_ms=0, end_ms=300, text='Xin chào mọi người.'), dict(start_ms=200, end_ms=700, text='Chúng ta thử tiếng Việt.'),
                dict(start_ms=600, end_ms=1400, text='Đọc đủ cả ba câu nhé.')]
        return directory, source, clips, cues

    def test_the_preview_is_a_real_export_with_the_tried_levels(self):
        directory, source, clips, cues = self.material()
        video = audio_preview.render_preview(source, cues, clips, directory/'out', original_gain=.3, voice_gain=1.4)
        self.assertTrue(video.is_file() and video.stat().st_size > 0)
        plan = json.loads((video.parent/'render-plan.json').read_text(encoding='utf-8'))
        self.assertEqual((plan['original_gain'], plan['voice_gain']), (.3, 1.4))
        self.assertNotEqual(plan['subtitle_mode'], 'burned_ass')  # No picture work for a listening test.
        with self.assertRaises(ValueError):
            audio_preview.render_preview(source, cues, clips, directory/'bad', original_gain=2.5, voice_gain=1)

    def test_only_whole_lines_inside_the_first_seconds_are_used(self):
        directory, source, clips, cues = self.material()
        video = audio_preview.render_preview(source, cues, clips, directory/'short', original_gain=.2, voice_gain=1, seconds=1)
        plan = json.loads((video.parent/'render-plan.json').read_text(encoding='utf-8'))
        self.assertEqual([cue['text'] for cue in plan['cues']], ['Xin chào mọi người.', 'Chúng ta thử tiếng Việt.'])  # 1400 ms > 1 s

    def test_the_material_comes_from_a_finished_video_and_the_link_is_left_unchanged(self):
        a = self.finished(2)
        directory, source, clips, cues = self.material()
        item = self.item(a, 2)
        plan_path = directory/'render-plan.json'
        atomic_json(plan_path, dict(source=str(source), cues=cues, gap_ms=100, requested_speed=1.0))
        (Path(item['job'])/'voice').mkdir()
        atomic_json(Path(item['job'])/'voice'/'manifest.json', dict(clips=clips))
        manifest = campaign.read_json(a/'campaign.json')
        manifest['items'][1]['outputs']['plan'] = str(plan_path)  # Video 1 has no record: it is passed over.
        atomic_json(a/'campaign.json', manifest)
        before = (a/'campaign.json').read_bytes()
        self.assertEqual(audio_preview.finished_material(a)[5], 2)
        video = audio_preview.make_preview(self.root, a, .25, 1.3)
        self.assertTrue(video.is_file())
        self.assertEqual(video.parent.parent.parent, audio_preview.preview_root(self.root))
        self.assertEqual((a/'campaign.json').read_bytes(), before)
        self.assertIsNone(next_settings(a))

    def test_no_usable_finished_video_is_said_clearly(self):
        a = self.finished(1)
        with self.assertRaisesRegex(ValueError, 'Chưa có video đã xong nào'):
            audio_preview.finished_material(a)

    def test_old_previews_are_removed_and_recent_ones_kept(self):
        root = audio_preview.preview_root(self.root)
        (root/'old').mkdir(parents=True)
        (root/'new').mkdir()
        old = time.time()-audio_preview.KEEP_SECONDS-60
        os.utime(root/'old', (old, old))
        audio_preview.cleanup_old(self.root)
        self.assertEqual(sorted(p.name for p in root.iterdir()), ['new'])
