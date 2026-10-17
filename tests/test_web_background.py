import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from teodub_web import background


class BackgroundLaunchTests(unittest.TestCase):
    def test_windows_launch_errors_are_private_and_do_not_fall_back(self):
        for result in (Mock(returncode=0, stdout='{"code":2,"pid":0}'),
                       Mock(returncode=1, stdout='provider echoed private-secret'),
                       Mock(returncode=0, stdout='{"code":0,"pid":true}')):
            with (self.subTest(result=result),
                  patch.object(background.subprocess, 'run', return_value=result),
                  self.assertRaises(ValueError) as error):
                background.detached_process(['private-secret'], background.PROJECT)
            self.assertNotIn('private-secret', str(error.exception))

    def test_success_requires_our_fresh_server_not_another_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'server-instance.json').write_text(json.dumps(dict(time=124, instance='ours', pid=123)))
            response = Mock(status=200)
            response.read.return_value = b'{"status":"ok","instance":"other"}'
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            opener = Mock()
            opener.open.return_value = response
            with (patch.object(background, 'detached_process'),
                  patch.object(background, 'build_opener', return_value=opener),
                  patch.object(background.time, 'time', return_value=123),
                  patch.object(background.time, 'monotonic', side_effect=[0, 1, 13]),
                  patch.object(background.time, 'sleep'),
                  self.assertRaisesRegex(ValueError, 'chưa sẵn sàng')):
                background.start_background(root)
            response.read.return_value = b'{"status":"ok","instance":"ours"}'
            with (patch.object(background, 'detached_process'),
                  patch.object(background, 'build_opener', return_value=opener),
                  patch.object(background.time, 'time', return_value=123)):
                self.assertEqual(background.start_background(root), 123)

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Object lifecycle')
    def test_background_survives_caller_job_closing_while_inherited_child_stops(self):
        import win32api
        import win32con
        import win32event
        import pywintypes
        with tempfile.TemporaryDirectory(prefix='Teo nền thử ') as directory:
            root = Path(directory)
            child = root/'child.py'
            parent = root/'parent.py'
            child.write_text('''import json,os,sys,time
from pathlib import Path
receipt,stop=map(Path,sys.argv[1:])
receipt.write_text(json.dumps(dict(pid=os.getpid())))
deadline=time.monotonic()+30
while not stop.exists() and time.monotonic()<deadline:
    time.sleep(.05)
''', encoding='utf-8')
            parent.write_text('''import json,subprocess,sys,time
from pathlib import Path
from teodub.common import protect_worker
from teodub_web.background import detached_process,PROJECT
root=Path(sys.argv[1])
protect_worker()
old=subprocess.Popen([sys.executable,str(root/'child.py'),str(root/'old.json'),str(root/'old.stop')],creationflags=subprocess.CREATE_NO_WINDOW)
detached_process([sys.executable,str(root/'child.py'),str(root/'new.json'),str(root/'new.stop')],PROJECT)
deadline=time.monotonic()+8
while not all((root/f).exists() for f in ('old.json','new.json')) and time.monotonic()<deadline:
    time.sleep(.05)
if not all((root/f).exists() for f in ('old.json','new.json')):
    raise RuntimeError('Fixture chưa mở được')
''', encoding='utf-8')
            env = os.environ.copy()
            env['PYTHONPATH'] = str(background.PROJECT)
            try:
                completed = subprocess.run([sys.executable, str(parent), str(root)], cwd=background.PROJECT,
                    env=env, capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(completed.returncode, 0, completed.stderr.decode('utf-8', errors='replace'))
                new_pid = json.loads((root/'new.json').read_text())['pid']
                new_handle = win32api.OpenProcess(win32con.SYNCHRONIZE, False, new_pid)
                try:
                    self.assertEqual(win32event.WaitForSingleObject(new_handle, 1000), win32con.WAIT_TIMEOUT)
                    old_pid = json.loads((root/'old.json').read_text())['pid']
                    try:
                        old_handle = win32api.OpenProcess(win32con.SYNCHRONIZE, False, old_pid)
                    except pywintypes.error as error:
                        if error.winerror != 87:
                            raise
                        old_handle = None  # Exited PID has already been removed.
                    if old_handle:
                        try:
                            self.assertEqual(win32event.WaitForSingleObject(old_handle, 3000), win32con.WAIT_OBJECT_0)
                        finally:
                            old_handle.Close()
                    (root/'new.stop').touch()
                    self.assertEqual(win32event.WaitForSingleObject(new_handle, 3000), win32con.WAIT_OBJECT_0)
                finally:
                    new_handle.Close()
            finally:
                (root/'old.stop').touch()
                (root/'new.stop').touch()
                # Let any failed fixture release its receipt files before cleanup.
                time.sleep(.2)


if __name__ == '__main__':
    unittest.main()
