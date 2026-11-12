from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from teodub import vc_runtime


class VcRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root/'Teo'
        self.python = self.project/'runtime'/'python'
        self.python.mkdir(parents=True)
        (self.python/'python.exe').write_bytes(b'p')
        self.source = self.root/'vc_redist'
        self.source.mkdir()
        for name in vc_runtime.FILES:
            (self.source/name).write_bytes(b'new '+name.encode())

    def ensure(self, executable=None):
        return vc_runtime.ensure(executable or self.python/'python.exe', self.project, self.source)

    def test_an_old_bundle_gets_the_missing_files_next_to_python(self):
        self.assertEqual(self.ensure(), list(vc_runtime.FILES))
        for name in vc_runtime.FILES:
            self.assertEqual((self.python/name).read_bytes(), b'new '+name.encode())
        self.assertEqual(self.ensure(), [])
        self.assertFalse(list(self.python.glob('*.tmp')))

    def test_a_file_already_there_is_never_replaced(self):
        (self.python/'msvcp140.dll').write_bytes(b'own')
        self.assertEqual(self.ensure(), ['msvcp140_1.dll', 'vcomp140.dll'])
        self.assertEqual((self.python/'msvcp140.dll').read_bytes(), b'own')

    def test_a_developer_venv_or_system_python_is_left_alone(self):
        other = self.root/'.venv'/'Scripts'
        other.mkdir(parents=True)
        self.assertEqual(self.ensure(other/'python.exe'), [])
        self.assertFalse(list(other.glob('*.dll')))

    def test_a_folder_that_cannot_be_written_falls_back_to_this_process_only(self):
        with patch.object(vc_runtime.shutil, 'copyfile', side_effect=PermissionError('read-only')), \
             patch.object(vc_runtime.os, 'add_dll_directory', create=True) as added:
            self.assertEqual(self.ensure(), [])
        added.assert_called_once_with(str(self.source))

    def test_the_real_files_ship_inside_teodub(self):
        for name in vc_runtime.FILES:
            self.assertGreater((vc_runtime.SOURCE/name).stat().st_size, 10000, name)


if __name__ == '__main__':
    unittest.main()
