import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from teodub import version


class VersionTests(unittest.TestCase):
    def read_with(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'version.json'
            if text is not None:
                path.write_text(text, encoding='utf-8')
            with patch.object(version, 'VERSION_FILE', path):
                return version.current()

    def test_reads_version_and_date(self):
        self.assertEqual(self.read_with(json.dumps(dict(version='1.2.3', released='2026-11-03'))),
                         dict(version='1.2.3', released='2026-11-03'))

    def test_missing_or_damaged_file_is_zero_not_a_crash(self):
        for text in (None, 'not json', '{"version": "1.2"}', '{"version": "a.b.c"}', '[]'):
            with self.subTest(text=text):
                self.assertEqual(self.read_with(text)['version'], '0.0.0')

    def test_shipped_file_is_valid(self):
        self.assertNotEqual(version.current()['version'], '0.0.0')


if __name__ == '__main__':
    unittest.main()
