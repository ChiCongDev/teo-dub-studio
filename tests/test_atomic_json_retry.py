import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from teodub.common import atomic_json


class AtomicJsonRetryTests(unittest.TestCase):
    def test_windows_brief_reader_lock_retries_without_publishing_partial_json(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'status.json';atomic_json(path,{'status':'running'})
            import os
            replace=os.replace
            denied=PermissionError('brief reader lock');denied.winerror=5
            calls=[]
            def replacing(source,target):
                calls.append((source,target))
                self.assertEqual(json.loads(path.read_text()),{'status':'running'})
                if len(calls)<3:raise denied
                replace(source,target)
            with patch('teodub.common.os.replace',side_effect=replacing),patch('teodub.common.os.name','nt'),patch('teodub.common.time.sleep') as sleep:
                atomic_json(path,{'status':'completed'})
            self.assertEqual(json.loads(path.read_text()),{'status':'completed'})
            self.assertEqual(sleep.call_count,2)
            self.assertEqual(list(Path(root).glob('*.tmp')),[])

    def test_persistent_denial_is_bounded_and_keeps_previous_document(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'status.json';atomic_json(path,{'status':'running'})
            denied=PermissionError('persistent lock');denied.winerror=32
            with patch('teodub.common.os.replace',side_effect=denied) as replacing,patch('teodub.common.os.name','nt'),patch('teodub.common.time.sleep'):
                with self.assertRaises(PermissionError):atomic_json(path,{'status':'completed'})
            self.assertEqual(replacing.call_count,7)
            self.assertEqual(json.loads(path.read_text()),{'status':'running'})
            self.assertEqual(list(Path(root).glob('*.tmp')),[])

    def test_unrelated_filesystem_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'status.json'
            with patch('teodub.common.os.replace',side_effect=OSError('disk error')) as replacing,patch('teodub.common.time.sleep') as sleep:
                with self.assertRaises(OSError):atomic_json(path,{'status':'completed'})
            replacing.assert_called_once();sleep.assert_not_called()
