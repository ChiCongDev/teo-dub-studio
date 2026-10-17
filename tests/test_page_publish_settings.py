import json
from pathlib import Path
import tempfile
import threading
import unittest

from teodub.page_publish_settings import result_checks_enabled, result_checks_guard, set_result_checks_enabled


class ResultCheckSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = self.root/'page-publishing'/'publish-settings.json'

    def test_missing_keeps_legacy_choice_and_saved_pause_survives_reopen(self):
        self.assertTrue(result_checks_enabled(self.root))
        set_result_checks_enabled(self.root, False)
        self.assertFalse(result_checks_enabled(str(self.root)))
        self.assertEqual(json.loads(self.settings.read_text(encoding='utf-8')),
                         dict(version=1, result_checks_enabled=False))
        set_result_checks_enabled(self.root, True)
        self.assertTrue(result_checks_enabled(self.root))

    def test_invalid_preference_does_not_start_checks(self):
        self.settings.parent.mkdir()
        for saved in ('not json', 'null', '[]', '{"result_checks_enabled":true}',
                      '{"version":true,"result_checks_enabled":true}',
                      '{"version":1,"result_checks_enabled":"true"}',
                      '{"version":1,"result_checks_enabled":1}'):
            with self.subTest(saved=saved):
                self.settings.write_text(saved, encoding='utf-8')
                self.assertFalse(result_checks_enabled(self.root))

    def test_setter_rejects_non_boolean_without_changing_existing_pause(self):
        set_result_checks_enabled(self.root, False)
        before = self.settings.read_bytes()
        for value in (None, 0, 1, 'false', {}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                set_result_checks_enabled(self.root, value)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_pause_change_waits_for_an_in_progress_final_result_commit(self):
        set_result_checks_enabled(self.root, True)
        started = threading.Event()
        finished = threading.Event()

        def pause():
            started.set()
            set_result_checks_enabled(self.root, False)
            finished.set()

        with result_checks_guard(self.root) as enabled:
            self.assertTrue(enabled)
            worker = threading.Thread(target=pause)
            worker.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(finished.wait(.05))
        self.assertTrue(finished.wait(1))
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertFalse(result_checks_enabled(self.root))
