from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from teodub import desktop_browser


class DesktopCoccocOpenTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='teo desktop browser fixture ')
        self.addCleanup(self.temporary.cleanup)
        self.executable = Path(self.temporary.name)/'Cốc Cốc'/'Application'/'browser.exe'
        find_patch = patch.object(desktop_browser, 'find_coccoc', return_value=self.executable)
        self.find = find_patch.start()
        self.addCleanup(find_patch.stop)
        spawn_patch = patch.object(desktop_browser.subprocess, 'Popen', return_value=Mock())
        self.spawn = spawn_patch.start()
        self.addCleanup(spawn_patch.stop)

    def assert_ordinary_launch(self, *expected_arguments):
        self.spawn.assert_called_once()
        positional, options = self.spawn.call_args
        self.assertEqual(positional, ([str(self.executable), *expected_arguments],))
        self.assertIs(options.get('shell'), False)
        self.assertIs(options.get('close_fds'), True)
        self.assertEqual(Path(options['cwd']), self.executable.parent)
        for stream in ('stdin', 'stdout', 'stderr'):
            self.assertEqual(options[stream], subprocess.DEVNULL)
        command = positional[0]
        self.assertFalse(any(argument.startswith('--') for argument in command[1:]), command)
        self.assertNotIn('env', options)

    def test_blank_input_opens_browser_without_profile_or_navigation_arguments(self):
        desktop_browser.open_desktop_coccoc()
        self.assert_ordinary_launch()

    def test_whitespace_input_also_opens_browser_without_a_url(self):
        desktop_browser.open_desktop_coccoc(' \n\t ')
        self.assert_ordinary_launch()

    def test_full_channel_url_is_canonicalized_before_opening(self):
        desktop_browser.open_desktop_coccoc('https://www.douyin.com/user/Owner_abc-123?from_tab_name=main&vid=789')
        self.assert_ordinary_launch('https://www.douyin.com/user/Owner_abc-123')

    def test_shared_channel_message_extracts_only_validated_share_url(self):
        desktop_browser.open_desktop_coccoc('复制打开抖音，看看这个频道 https://v.douyin.com/7gbKbOUa1G4/ 09/30')
        self.assert_ordinary_launch('https://v.douyin.com/7gbKbOUa1G4/')

    def test_markdown_share_url_is_supported(self):
        desktop_browser.open_desktop_coccoc('[Kênh](https://v.douyin.com/7gbKbOUa1G4/)')
        self.assert_ordinary_launch('https://v.douyin.com/7gbKbOUa1G4/')

    def test_invalid_or_nonchannel_input_never_starts_a_process(self):
        invalid = (
            'https://example.com/user/owner',
            'https://www.douyin.com/video/123',
            'https://username:secret@www.douyin.com/user/owner',
            'https://www.douyin.com:9222/user/owner',
            'https://www.douyin.com/user/one https://www.douyin.com/user/two',
            'file:///C:/private.html',
            '--remote-debugging-port=9222',
        )
        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    desktop_browser.open_desktop_coccoc(text)
                self.spawn.assert_not_called()

    def test_missing_installation_reports_coccoc_without_spawning(self):
        self.find.return_value = None
        with self.assertRaisesRegex(ValueError, 'Cốc Cốc'):
            desktop_browser.open_desktop_coccoc()
        self.spawn.assert_not_called()

    def test_process_start_failure_becomes_a_friendly_coccoc_error(self):
        self.spawn.side_effect = OSError('synthetic fixture denied')
        with self.assertRaisesRegex(ValueError, 'Cốc Cốc'):
            desktop_browser.open_desktop_coccoc()
        self.spawn.assert_called_once()


if __name__ == '__main__':
    unittest.main()
