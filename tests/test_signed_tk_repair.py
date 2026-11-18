"""Transactional, narrowly scoped repair using disposable runtime fixtures."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import repair_tk
import signed_tk


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root/'Teo'
        self.runtime = self.project/'runtime/python'
        self.runtime.mkdir(parents=True)
        (self.project/'launch.py').write_text('# fixture')
        (self.runtime/'python.exe').write_bytes(b'original interpreter')
        (self.runtime/'Lib/site-packages/keep').mkdir(parents=True)
        (self.runtime/'Lib/site-packages/keep/data').write_bytes(b'dependency')
        self.package = self.root/'package'
        for unit in repair_tk.UNITS:
            old = self.runtime/unit
            new = self.package/'payload'/unit
            if unit in ('tcl', 'Lib/tkinter'):
                old, new = old/'init.py', new/'init.py'
            for p, value in ((old, b'old'), (new, b'new')):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(value)
        self.manifest()
        self.before = self.snapshot()
        self.options = dict(verify=Mock(), idle=Mock(), runtime_check=Mock(), gui_test=Mock(return_value='TK_OK'))

    def manifest(self):
        payload = self.package/'payload'
        data = dict(format='teo-signed-tk-v1', units=list(repair_tk.UNITS),
            files={p.relative_to(payload).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in payload.rglob('*') if p.is_file()})
        (self.package/'manifest.json').write_text(json.dumps(data))
        return data

    def snapshot(self):
        return {p.relative_to(self.runtime).as_posix():p.read_bytes()
                for p in self.runtime.rglob('*') if p.is_file()}

    def apply(self, **kwargs):
        return repair_tk.apply(self.project, self.package, **dict(self.options, **kwargs))

    def test_applies_only_tk_and_keeps_interpreter_dependencies_and_code(self):
        result = self.apply()
        self.assertEqual(result['status'], 'repaired')
        self.assertEqual((self.runtime/'python.exe').read_bytes(), b'original interpreter')
        self.assertEqual((self.runtime/'Lib/site-packages/keep/data').read_bytes(), b'dependency')
        self.assertEqual((self.project/'launch.py').read_text(), '# fixture')
        self.assertEqual((Path(result['backup'])/'original/DLLs/_tkinter.pyd').read_bytes(), b'old')
        self.assertFalse(result['user_data_changed'])
        self.assertFalse(result['policies_changed'])

    def test_check_only_does_not_modify_runtime(self):
        self.assertEqual(self.apply(check_only=True)['status'], 'checked')
        self.assertEqual(self.snapshot(), self.before)

    def test_bad_checksum_stops_before_replacement(self):
        (self.package/'payload/DLLs/_tkinter.pyd').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'Damaged payload'):
            self.apply()
        self.assertEqual(self.snapshot(), self.before)

    def test_extra_file_is_not_accepted(self):
        (self.package/'payload/evil.dll').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            self.apply()
        self.assertEqual(self.snapshot(), self.before)

    def test_path_outside_whitelist_is_not_accepted_even_with_manifest_hash(self):
        (self.package/'payload/evil.dll').write_bytes(b'extra')
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'Unsafe payload'):
            self.apply()
        self.assertEqual(self.snapshot(), self.before)

    def test_unsigned_payload_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'signature'):
            self.apply(verify=Mock(side_effect=ValueError('signature invalid')))
        self.assertEqual(self.snapshot(), self.before)

    def test_busy_runtime_is_not_touched(self):
        with self.assertRaisesRegex(ValueError, 'busy'):
            self.apply(idle=Mock(side_effect=ValueError('busy')))
        self.assertEqual(self.snapshot(), self.before)

    def test_wrong_python_is_not_touched(self):
        with self.assertRaisesRegex(ValueError, 'version'):
            self.apply(runtime_check=Mock(side_effect=ValueError('version')))
        self.assertEqual(self.snapshot(), self.before)

    def test_gui_failure_restores_all_units(self):
        with self.assertRaisesRegex(ValueError, 'GUI failed'):
            self.apply(gui_test=Mock(side_effect=ValueError('GUI failed')))
        self.assertEqual(self.snapshot(), self.before)

    def test_mid_copy_failure_restores_previously_replaced_units(self):
        original = repair_tk.os.replace
        def fail_once(src, dst):
            if 'staged' in str(src) and str(src).endswith('tk86t.dll'):
                raise OSError('synthetic copy failure')
            return original(src, dst)
        with patch.object(repair_tk.os, 'replace', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'synthetic'):
                self.apply()
        self.assertEqual(self.snapshot(), self.before)

    def test_repeating_repair_keeps_backups_and_does_not_change_other_files(self):
        first = self.apply()
        second = self.apply()
        self.assertNotEqual(first['backup'], second['backup'])
        self.assertEqual((self.runtime/'python.exe').read_bytes(), b'original interpreter')

    def test_official_msi_checksum_mismatch_refuses_before_extraction(self):
        cache = self.root/'build/signed-tk-3.13'
        cache.mkdir(parents=True)
        (cache/'tcltk.msi').write_bytes(b'damaged')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            signed_tk.prepare(self.root/'build')
