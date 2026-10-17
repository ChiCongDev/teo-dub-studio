import os
from pathlib import Path
import tempfile
import unittest
from teodub.subtitle_clean_profiles import save_profile, load_profile, set_enabled
from teodub.subtitle_clean import validate_clean, verify_clean_source


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source.mp4'
        self.source.write_bytes(b'original video')
        self.options = dict(region=[.1, .7, .9, .8])

    def test_save_disabled_retains_region_toggle_and_reopen(self):
        saved = save_profile(self.root, self.source, self.options)
        self.assertFalse(saved['enabled'])
        self.assertEqual(load_profile(self.root, self.source), saved)
        self.assertEqual(set_enabled(self.root, self.source, True)['region'], self.options['region'])
        self.assertTrue(load_profile(self.root, self.source)['enabled'])
        self.assertEqual(set_enabled(self.root, self.source, False)['region'], self.options['region'])
        self.assertFalse(load_profile(self.root, self.source)['enabled'])

    def test_source_binding_survives_validation_and_rejects_other_source(self):
        saved = save_profile(self.root, self.source, self.options)
        validated = validate_clean({'subtitle_clean': dict(saved, enabled=True)})
        self.assertEqual(validated['source_sha256'], saved['source_sha256'])
        verify_clean_source(self.source, validated)
        other = self.root/'other.mp4'
        other.write_bytes(b'other video')
        with self.assertRaises(ValueError):
            verify_clean_source(other, validated)
        self.assertEqual(load_profile(self.root, other), dict(enabled=False))

    def test_hash_verification_catches_tampering_even_same_size_and_timestamp(self):
        save_profile(self.root, self.source, self.options)
        stamp = self.source.stat()
        self.source.write_bytes(b'X'*stamp.st_size)
        os.utime(self.source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with self.assertRaises(ValueError):
            set_enabled(self.root, self.source, True)

    def test_missing_saved_region_cannot_be_enabled(self):
        with self.assertRaisesRegex(ValueError, 'lưu vùng'):
            set_enabled(self.root, self.source, True)

    def test_resaving_region_requires_new_tick_and_does_not_edit_old_snapshot(self):
        save_profile(self.root, self.source, self.options)
        old = set_enabled(self.root, self.source, True)
        save_profile(self.root, self.source, dict(region=[.2, .6, .8, .8]))
        fresh = load_profile(self.root, self.source)
        self.assertFalse(fresh['enabled'])
        self.assertTrue(old['enabled'])
        self.assertEqual(old['region'], self.options['region'])
