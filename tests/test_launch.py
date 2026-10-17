from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import launch


class RuntimeLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='teo runtime ')
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.scripts = self.project/'.venv'/'Scripts'
        self.scripts.mkdir(parents=True)
        (self.scripts/'python.exe').touch()
        (self.scripts/'pythonw.exe').touch()
        self.enterContext(patch.object(launch.sys, 'prefix', str(self.project/'system-python')))

    def test_direct_source_launch_uses_project_environment_and_preserves_args(self):
        args = ['--data-dir', str(self.project/'du lieu tiếng Việt')]
        with patch.object(launch.subprocess, 'Popen') as spawn:
            self.assertEqual(launch.bootstrap_environment(args, self.project), 0)
        self.assertEqual(spawn.call_args.args[0], [str(self.scripts/'pythonw.exe'), '-X', 'utf8', '-B', str(self.project/'launch.py'), *args])
        self.assertNotIn('shell', spawn.call_args.kwargs)

    def test_current_environment_does_not_respawn_or_loop(self):
        with patch.object(launch.sys, 'prefix', str(self.project/'.venv')), patch.object(launch.subprocess, 'Popen') as spawn:
            self.assertIsNone(launch.bootstrap_environment([], self.project))
            spawn.assert_not_called()

    def test_frozen_executable_uses_bundled_runtime(self):
        with patch.object(launch.sys, 'frozen', True, create=True), patch.object(launch.subprocess, 'Popen') as spawn:
            self.assertIsNone(launch.bootstrap_environment([], self.project))
            spawn.assert_not_called()

    def test_worker_redirect_waits_and_preserves_exit_status(self):
        for flag in ('--worker', '--campaign-worker', '--campaign-batch-worker'):
            with self.subTest(flag=flag), patch.object(launch.subprocess, 'run', return_value=Mock(returncode=2)) as run, patch.object(launch.subprocess, 'Popen') as spawn:
                self.assertEqual(launch.bootstrap_environment([flag, '--config', 'request.json'], self.project), 2)
                self.assertEqual(run.call_args.args[0][0], str(self.scripts/'python.exe'))
                self.assertIn(flag, run.call_args.args[0])
                spawn.assert_not_called()

    def test_missing_environment_is_not_silently_created(self):
        (self.scripts/'python.exe').unlink()
        with patch.object(launch.subprocess, 'Popen') as spawn:
            self.assertIsNone(launch.bootstrap_environment([], self.project))
            spawn.assert_not_called()

    def test_redirect_happens_before_loading_ui_or_workers(self):
        with patch.object(launch, 'bootstrap_environment', return_value=7), patch('teodub.app.main') as ui, patch('teodub.pipeline.main') as worker:
            self.assertEqual(launch.main(['--worker', '--config', 'request.json']), 7)
            ui.assert_not_called()
            worker.assert_not_called()

    def test_batch_worker_dispatch_preserves_stop_flag_without_loading_ui(self):
        with patch.object(launch, 'bootstrap_environment', return_value=None), \
             patch('teodub.campaign_batch.main', return_value=2) as worker, \
             patch('teodub.app.main') as ui:
            result = launch.main(['--campaign-batch-worker', '--directory', 'saved queue', '--keep-stop'])
        self.assertEqual(result, 2)
        worker.assert_called_once_with(['--directory', 'saved queue', '--keep-stop'])
        ui.assert_not_called()


if __name__ == '__main__':
    unittest.main()
