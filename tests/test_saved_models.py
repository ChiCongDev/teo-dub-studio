"""Models already saved on this machine load without the Hugging Face Hub; temporary caches, no network."""
from pathlib import Path
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

import httpx
from huggingface_hub import constants, hf_hub_download

import test_speech
from teodub import asr, models, tts

COMMIT = '0123456789abcdef0123456789abcdef01234567'
RESET = '[WinError 10054] An existing connection was forcibly closed by the remote host'


class SavedFirstTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='teodub-saved-models-test-')
        self.addCleanup(temporary.cleanup)
        self.cache = Path(temporary.name).resolve()
        repo = self.cache/'models--teo--voice'
        (repo/'refs').mkdir(parents=True)
        (repo/'refs'/'main').write_text(COMMIT, encoding='utf-8')
        saved = repo/'snapshots'/COMMIT/'voice.onnx'
        saved.parent.mkdir(parents=True)
        saved.write_bytes(b'saved model weights')
        self.offline = constants.HF_HUB_OFFLINE
        # 23/10 test's video 15: the Hub reset the connection while VieNeu checked a file it already had.
        self.hub = self.enterContext(patch.object(httpx.HTTPTransport, 'handle_request',
                                                  side_effect=httpx.ReadError(RESET)))

    def download(self, name='voice.onnx'):
        return hf_hub_download('teo/voice', name, cache_dir=str(self.cache))

    def test_a_hub_reset_stopped_the_load_of_a_saved_model_before(self):
        with self.assertRaisesRegex(httpx.ReadError, '10054'):
            self.download()
        self.assertTrue(self.hub.called)

    def test_a_saved_model_loads_without_asking_the_hub(self):
        self.assertEqual(Path(models.saved_first(self.download)).read_bytes(), b'saved model weights')
        self.hub.assert_not_called()
        self.assertEqual(constants.HF_HUB_OFFLINE, self.offline)

    def test_a_model_with_a_missing_file_is_loaded_online_as_before(self):
        with self.assertRaisesRegex(httpx.ReadError, '10054'):
            models.saved_first(lambda: self.download('new-voice.onnx'))
        self.assertTrue(self.hub.called)
        self.assertEqual(constants.HF_HUB_OFFLINE, self.offline)

    def test_other_load_errors_are_reported_without_a_second_load(self):
        load = Mock(side_effect=RuntimeError('ONNX graph is damaged'))
        with self.assertRaisesRegex(RuntimeError, 'damaged'):
            models.saved_first(load)
        load.assert_called_once()
        self.assertEqual(constants.HF_HUB_OFFLINE, self.offline)


class ModelLoadTests(unittest.TestCase):
    def test_voice_model_is_built_from_the_saved_files(self):
        seen = []
        vieneu = ModuleType('vieneu')
        vieneu.Vieneu = lambda **options: seen.append((constants.HF_HUB_OFFLINE, options)) or 'voice engine'
        with patch.dict('sys.modules', {'vieneu': vieneu}), patch.object(tts, 'configure_models'), \
                patch.object(tts, '_model', None), patch.object(tts, '_model_name', None):
            self.assertEqual(tts.get_model(), 'voice engine')
        self.assertEqual(seen, [(True, dict(mode='v3turbo', backend='onnx', precision='fp32', threads=4))])

    def test_whisper_is_built_from_the_saved_files(self):
        speech = test_speech.AsrTests()
        speech.setUp()
        self.addCleanup(speech.doCleanups)
        seen, offline = [], constants.HF_HUB_OFFLINE
        with speech.model() as (factory, engine):
            factory.side_effect = lambda *args, **kwargs: seen.append(constants.HF_HUB_OFFLINE) or engine
            asr.transcribe(speech.source, speech.output)
        self.assertEqual(seen, [True])
        self.assertEqual(constants.HF_HUB_OFFLINE, offline)


if __name__ == '__main__':
    unittest.main()
