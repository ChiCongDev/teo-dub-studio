from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from teodub import channel_browser


class CoccocLocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='teo browser tests ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def installed_browser(self, root):
        executable = root/'CocCoc'/'Browser'/'Application'/'browser.exe'
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.touch()
        return executable

    def test_finds_each_supported_installation_root(self):
        for variable in ('LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMW6432', 'PROGRAMFILES(X86)'):
            with self.subTest(variable=variable):
                root = self.root/variable
                executable = self.installed_browser(root)
                self.assertEqual(channel_browser.find_coccoc({variable: str(root)}), executable)

    def test_local_installation_takes_priority_over_machine_installation(self):
        local = self.installed_browser(self.root/'local')
        self.installed_browser(self.root/'machine')
        environ = {'LOCALAPPDATA': str(self.root/'local'),
                   'PROGRAMFILES': str(self.root/'machine')}
        self.assertEqual(channel_browser.find_coccoc(environ), local)

    def test_absent_executable_returns_none(self):
        environ = {variable: str(self.root/variable)
                   for variable in ('LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)')}
        self.assertIsNone(channel_browser.find_coccoc(environ))

    def test_directory_named_browser_exe_is_not_an_installation(self):
        root = self.root/'local'
        (root/'CocCoc'/'Browser'/'Application'/'browser.exe').mkdir(parents=True)
        self.assertIsNone(channel_browser.find_coccoc({'LOCALAPPDATA': str(root)}))

    def test_explicit_environment_does_not_fall_back_to_ambient_environment(self):
        self.installed_browser(self.root/'ambient')
        with patch.dict(channel_browser.os.environ,
                        {'LOCALAPPDATA': str(self.root/'ambient')}, clear=True):
            self.assertIsNone(channel_browser.find_coccoc({}))

    def test_default_environment_is_injectable_without_reading_personal_paths(self):
        executable = self.installed_browser(self.root/'local')
        with patch.dict(channel_browser.os.environ,
                        {'LOCALAPPDATA': str(self.root/'local')}, clear=True):
            self.assertEqual(channel_browser.find_coccoc(), executable)


class ChannelBrowserLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='teo isolated profile ')
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)/'app-only-profile'
        self.runtime = Mock()
        self.context = Mock(name='browser context')
        self.launch = self.runtime.chromium.launch_persistent_context
        self.launch.return_value = self.context

    def expected_call(self, *, channel=None, executable_path=None, visible=False):
        kwargs = dict(headless=not visible, viewport={'width': 1280, 'height': 850},
                      args=['--mute-audio'], timeout=30000)
        if channel is not None:
            kwargs['channel'] = channel
        if executable_path is not None:
            kwargs['executable_path'] = executable_path
        return call(str(self.profile.resolve()), **kwargs)

    def test_browser_labels_have_stable_settings_ids(self):
        self.assertEqual(channel_browser.BROWSER_LABELS, {
            'auto': 'Tự chọn (Edge / Chrome)',
            'coccoc': 'Cốc Cốc (thử nghiệm)',
            'msedge': 'Microsoft Edge',
            'chrome': 'Google Chrome',
        })

    def test_default_auto_uses_edge_and_returns_selected_browser(self):
        with patch.object(channel_browser, 'find_coccoc') as find:
            context, selected = channel_browser.launch_channel_browser(self.runtime, self.profile)
        self.assertIs(context, self.context)
        self.assertEqual(selected, 'msedge')
        self.assertEqual(self.launch.call_args_list, [self.expected_call(channel='msedge')])
        find.assert_not_called()

    def test_auto_falls_back_from_edge_to_chrome_only(self):
        self.launch.side_effect = [RuntimeError('Edge fixture unavailable'), self.context]
        with patch.object(channel_browser, 'find_coccoc') as find:
            context, selected = channel_browser.launch_channel_browser(
                self.runtime, self.profile, browser='auto')
        self.assertIs(context, self.context)
        self.assertEqual(selected, 'chrome')
        self.assertEqual(self.launch.call_args_list, [
            self.expected_call(channel='msedge'), self.expected_call(channel='chrome')])
        find.assert_not_called()

    def test_auto_reports_both_failures_after_one_attempt_per_browser(self):
        self.launch.side_effect = [RuntimeError('Edge fixture unavailable'),
                                   RuntimeError('Chrome fixture unavailable')]
        with self.assertRaises(ValueError) as error:
            channel_browser.launch_channel_browser(self.runtime, self.profile, browser='auto')
        message = str(error.exception)
        self.assertIn('Edge', message)
        self.assertIn('Chrome', message)
        self.assertEqual(self.launch.call_args_list, [
            self.expected_call(channel='msedge'), self.expected_call(channel='chrome')])

    def test_explicit_edge_and_chrome_use_only_requested_browser(self):
        for browser in ('msedge', 'chrome'):
            with self.subTest(browser=browser):
                self.launch.reset_mock()
                with patch.object(channel_browser, 'find_coccoc') as find:
                    context, selected = channel_browser.launch_channel_browser(
                        self.runtime, self.profile, browser=browser)
                self.assertIs(context, self.context)
                self.assertEqual(selected, browser)
                self.assertEqual(self.launch.call_args_list, [self.expected_call(channel=browser)])
                find.assert_not_called()

    def test_explicit_edge_or_chrome_failure_never_silently_falls_back(self):
        for browser in ('msedge', 'chrome'):
            with self.subTest(browser=browser):
                self.launch.reset_mock()
                self.launch.side_effect = RuntimeError('requested browser fixture unavailable')
                with patch.object(channel_browser, 'find_coccoc') as find:
                    with self.assertRaises((RuntimeError, ValueError)):
                        channel_browser.launch_channel_browser(
                            self.runtime, self.profile, browser=browser)
                self.assertEqual(self.launch.call_args_list, [self.expected_call(channel=browser)])
                find.assert_not_called()

    def test_coccoc_uses_discovered_executable_without_playwright_channel(self):
        executable = Path(self.temp.name)/'Cốc Cốc fixture'/'browser.exe'
        with patch.object(channel_browser, 'find_coccoc', return_value=executable) as find:
            context, selected = channel_browser.launch_channel_browser(
                self.runtime, self.profile, browser='coccoc', visible=True)
        find.assert_called_once_with()
        self.assertIs(context, self.context)
        self.assertEqual(selected, 'coccoc')
        self.assertEqual(self.launch.call_args_list, [self.expected_call(
            executable_path=str(executable), visible=True)])
        self.assertNotIn('channel', self.launch.call_args.kwargs)

    def test_missing_coccoc_fails_before_any_browser_launch(self):
        with patch.object(channel_browser, 'find_coccoc', return_value=None):
            with self.assertRaises(ValueError) as error:
                channel_browser.launch_channel_browser(
                    self.runtime, self.profile, browser='coccoc')
        self.assertIn('Cốc Cốc', str(error.exception))
        self.launch.assert_not_called()

    def test_coccoc_launch_failure_never_silently_falls_back(self):
        executable = Path(self.temp.name)/'coccoc fixture'/'browser.exe'
        self.launch.side_effect = RuntimeError('CocCoc fixture unavailable')
        with patch.object(channel_browser, 'find_coccoc', return_value=executable):
            with self.assertRaises((RuntimeError, ValueError)):
                channel_browser.launch_channel_browser(
                    self.runtime, self.profile, browser='coccoc')
        self.assertEqual(self.launch.call_args_list, [self.expected_call(
            executable_path=str(executable))])

    def test_invalid_browser_is_rejected_before_discovery_or_launch(self):
        for browser in ('firefox', '', 'Cốc Cốc'):
            with self.subTest(browser=browser):
                with patch.object(channel_browser, 'find_coccoc') as find:
                    with self.assertRaises(ValueError):
                        channel_browser.launch_channel_browser(
                            self.runtime, self.profile, browser=browser)
                find.assert_not_called()
                self.launch.assert_not_called()

    def test_visible_mode_disables_headless(self):
        channel_browser.launch_channel_browser(
            self.runtime, self.profile, browser='chrome', visible=True)
        self.assertEqual(self.launch.call_args_list, [
            self.expected_call(channel='chrome', visible=True)])

    def test_profile_path_is_resolved_and_preserved_without_browser_subfolder(self):
        profile = self.profile/'nested'/'..'
        channel_browser.launch_channel_browser(self.runtime, profile, browser='chrome')
        self.assertEqual(self.launch.call_args.args, (str(self.profile.resolve()),))

    def test_auto_profile_preserves_existing_application_profile(self):
        self.assertEqual(channel_browser.profile_directory(self.profile, 'auto'),
                         self.profile.resolve())

    def test_explicit_profiles_are_separate_from_legacy_and_each_other(self):
        profiles = []
        for browser in ('coccoc', 'msedge', 'chrome'):
            with self.subTest(browser=browser):
                profile = channel_browser.profile_directory(self.profile, browser)
                self.assertEqual(profile, self.profile.resolve()/browser)
                profiles.append(profile)
        self.assertEqual(len(set(profiles)), 3)
        self.assertNotIn(self.profile.resolve(), profiles)

    def test_invalid_browser_cannot_select_a_profile(self):
        with self.assertRaises(ValueError):
            channel_browser.profile_directory(self.profile, '../personal-browser-profile')


if __name__ == '__main__':
    unittest.main()
