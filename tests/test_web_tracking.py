import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
from datetime import datetime
import unittest
from unittest.mock import patch

from teodub import web_tracking as tracking


class WebTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.now = datetime(2026, 10, 6, 12).timestamp()
        with self.db() as db:
            db.executescript('''CREATE TABLE users(id TEXT,username TEXT,name TEXT,enabled INTEGER,password TEXT);
                CREATE TABLE jobs(id TEXT,owner TEXT,title TEXT,input TEXT,spec TEXT,state TEXT,
                                  created REAL,updated REAL,message TEXT);
                CREATE TABLE sessions(token TEXT);
                INSERT INTO users VALUES('a','an','An',1,'private');
                INSERT INTO users VALUES('b','binh','Bình',0,'private');''')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.root/'portal.sqlite3')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def job(self, number, state='queued', owner='a', created=None, spec=None):
        ident = f'{number:032x}'
        created = self.now if created is None else created
        with self.db() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                       (ident, owner, f'Video {number}', 'https://v.douyin.com/example/',
                        json.dumps(spec or dict(save_drive=True, voice='Adam')), state, created, self.now, 'Thông báo đã lưu'))
        return ident

    def read(self, **kwargs):
        return tracking.snapshot(self.root, now=self.now, health=lambda: True, **kwargs)

    def metadata(self, ident, name, value):
        directory = self.root/'jobs'/ident
        directory.mkdir(parents=True, exist_ok=True)
        (directory/name).write_text(json.dumps(value), encoding='utf-8')

    def test_missing_database_is_not_created_or_probed(self):
        missing = self.root/'missing'
        with patch('teodub.web_tracking.server_online') as health:
            result = tracking.snapshot(missing, health=health)
        self.assertFalse(result['exists'])
        self.assertFalse(missing.exists())
        health.assert_not_called()

    def test_reader_is_readonly_and_never_reads_password_or_sessions(self):
        self.job(1)
        before = (self.root/'portal.sqlite3').read_bytes()
        connect = sqlite3.connect

        def authorized(*args, **kwargs):
            self.assertIn('mode=ro', args[0])
            db = connect(*args, **kwargs)
            def check(action, table, column, *_):
                if action == sqlite3.SQLITE_READ and (table == 'sessions' or column == 'password'):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.set_authorizer(check)
            return db

        with patch('teodub.web_tracking.sqlite3.connect', side_effect=authorized):
            result = self.read()
        self.assertEqual(len(result['jobs']), 1)
        self.assertEqual((self.root/'portal.sqlite3').read_bytes(), before)
        self.assertNotIn('password', result['users'][0])

    def test_order_running_first_then_queue_fifo_then_history_newest(self):
        new = self.job(1, created=self.now)
        old = self.job(2, created=self.now-120)
        running = self.job(3, 'running', created=self.now-60)
        complete = self.job(4, 'completed', created=self.now+60)
        self.assertEqual([row['id'] for row in self.read()['jobs']], [running, old, new, complete])

    def test_counts_employee_scope_and_state_filter_only_limits_list(self):
        self.job(1)
        self.job(2, 'completed')
        self.job(3, 'failed', 'b')
        self.job(4, 'cancelled', 'b')
        result = self.read(state='queued')
        self.assertEqual(result['visible_total'], 1)
        self.assertEqual(sum(result['counts'].values()), 4)
        self.assertEqual(len(result['people']), 2)
        result = self.read(owner='b')
        self.assertEqual(result['counts'], dict(failed=1, cancelled=1))
        self.assertEqual([row['id'] for row in result['users']], ['a', 'b'])
        self.assertEqual(self.read(owner="' OR 1=1 --")['jobs'], [])

    def test_today_and_seven_days_use_local_calendar_and_created_time(self):
        midnight = datetime(2026, 10, 6).timestamp()
        today = self.job(1, created=midnight)
        self.job(2, created=midnight-1)
        first = self.job(3, created=datetime(2026, 9, 30).timestamp())
        self.job(4, created=datetime(2026, 9, 30).timestamp()-1)
        self.assertEqual([j['id'] for j in self.read(period='Hôm nay')['jobs']], [today])
        self.assertEqual(self.read(period='7 ngày gần đây')['visible_total'], 3)
        self.assertIn(first, [j['id'] for j in self.read(period='7 ngày gần đây')['jobs']])

    def test_limit_does_not_truncate_statistics(self):
        for number in range(205):
            self.job(number)
        result = self.read()
        self.assertEqual(len(result['jobs']), tracking.ROW_LIMIT)
        self.assertEqual(result['visible_total'], 205)
        self.assertEqual(result['counts']['queued'], 205)

    def test_progress_retains_latest_message_and_earlier_numeric_percent(self):
        ident = self.job(1, 'running')
        directory = self.root/'jobs'/ident
        directory.mkdir(parents=True)
        (directory/'events.jsonl').write_text(
            json.dumps(dict(percent=42, message='Thông báo cũ'))+'\n'
            +json.dumps(dict(percent=None, message='Đang ghép video', time='2026-10-06T12:00:00'))
            +'\n{"partial":', encoding='utf-8')
        job = self.read()['jobs'][0]
        self.assertEqual(job['progress'], '42%')
        self.assertEqual(job['message'], 'Đang ghép video')

    def test_drive_stage_is_not_claimed_uploaded_and_bad_metadata_is_tolerated(self):
        ident = self.job(1, 'completed')
        self.metadata(ident, 'drive-upload.json', dict(state='staged', cloud_confirmed=False))
        self.assertEqual(self.read()['jobs'][0]['drive'], 'Đã chép vào thư mục đồng bộ')
        (self.root/'jobs'/ident/'drive-upload.json').write_text('{partial', encoding='utf-8')
        self.assertEqual(self.read()['jobs'][0]['drive'], 'Chờ thành phẩm')

    def test_job_path_traversal_is_not_read(self):
        self.assertIsNone(tracking.job_directory(self.root, '../outside'))
        self.assertIsNone(tracking.job_directory(self.root, 'A'*32))

    def test_output_open_requires_published_job_bound_video(self):
        ident = self.job(1, 'completed')
        folder = self.root/'outputs'/ident/'output'
        folder.mkdir(parents=True)
        video = folder/'video.mp4'
        video.write_bytes(b'fixture')
        outputs = dict(video=str(video))
        self.metadata(ident, 'result.json', dict(status='completed', outputs=outputs))
        self.metadata(ident, 'state.json', dict(rendered=dict(outputs=outputs)))
        job = self.read()['jobs'][0]
        self.assertEqual(tracking.output_folder(self.root, job), folder)
        outside = self.root/'outside.mp4'
        outside.write_bytes(b'outside')
        bad = dict(video=str(outside))
        self.metadata(ident, 'result.json', dict(status='completed', outputs=bad))
        self.metadata(ident, 'state.json', dict(rendered=dict(outputs=bad)))
        self.assertIsNone(tracking.output_folder(self.root, job))
        self.assertIsNone(tracking.output_folder(self.root, dict(job, state='running')))

    def test_pinned_path_used_even_when_missing_instead_of_second_store(self):
        project = self.root/'project'
        (project/'tools').mkdir(parents=True)
        target = self.root/'pinned'
        (project/'tools'/'web-data-root.txt').write_text(str(target), encoding='utf-8')
        with patch.object(tracking, 'PROJECT', project):
            self.assertEqual(tracking.web_data_root(), target)
        self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
