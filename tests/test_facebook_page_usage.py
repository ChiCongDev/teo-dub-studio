"""Load Page asks whether a saved Page has work attached before it merges a duplicate row away (owner 11/11/2026). Temp folders only."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from teodub.facebook_page_usage import PageUsage


def make(base, name, sql, rows=()):
    base.mkdir(parents=True, exist_ok=True)
    path = base/name
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.execute(sql)
    for statement, values in rows:
        connection.execute(statement, values)
    connection.commit()
    connection.close()


class PageUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.base = self.data/'page-publishing'
        self.usage = PageUsage(self.data, 'a1')

    def schedules(self, *states):
        make(self.base, 'schedules.sqlite3', 'CREATE TABLE schedules (account_id TEXT, page_key TEXT, state TEXT)',
             [('INSERT INTO schedules VALUES (?,?,?)', ('a1', 'p1', state)) for state in states])

    def test_a_page_with_nothing_saved_is_not_in_use(self):
        self.assertFalse(self.usage.in_use('p1'))

    def test_any_schedule_even_a_finished_one_makes_the_page_in_use(self):
        self.schedules('published')
        self.assertTrue(self.usage.in_use('p1'))
        self.assertFalse(self.usage.in_use('p2'))  # Other pages do not count.

    def test_a_database_that_cannot_be_read_is_the_safe_answer(self):
        self.base.mkdir(parents=True)
        (self.base/'schedules.sqlite3').write_text('not a database', encoding='utf-8')
        self.assertTrue(self.usage.in_use('p1'))

    def test_a_policy_makes_the_page_in_use(self):
        make(self.base, 'ai-planning.sqlite3', 'CREATE TABLE policies (account_id TEXT, page_key TEXT)',
             [('INSERT INTO policies VALUES (?,?)', ('a1', 'p1'))])
        self.assertTrue(self.usage.in_use('p1'))
        self.assertFalse(self.usage.in_use('p9'))


if __name__ == '__main__':
    unittest.main()
