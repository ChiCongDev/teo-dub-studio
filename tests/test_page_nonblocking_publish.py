"""Receipt observation stays active while the next approved job uploads."""
import threading
from types import SimpleNamespace
import unittest

from teodub.facebook_post_tasks import BrowserPostTasks
from tests import test_page_browser_worker as worker_fixtures


class BrowserPostTaskTests(unittest.TestCase):
    def setUp(self):
        self.tasks = BrowserPostTasks()
        self.events = []
        self.thread = threading.get_ident()
        self.addCleanup(self.tasks.close)

    def add(self, request, iterator, *, post=None, cancel=None):
        self.tasks.add(request, iterator, scope=('account', 'page', post or request), cancel=cancel,
            event=lambda kind, message, **extra: self.events.append((request, kind, message, extra)))

    def test_second_upload_finishes_while_first_receipt_is_pending_and_observed(self):
        trace = []
        ready = False

        def publish():
            trace.append('A clicked once')
            try:
                while not ready:
                    self.assertEqual(threading.get_ident(), self.thread)
                    trace.append('A receipt observed')
                    yield dict(phase='verifying', message='A awaiting receipt')
                return dict(state='published', message='A confirmed')
            finally:
                trace.append('A lock released')

        def prepare():
            trace.append('B upload started')
            for _ in range(2):
                self.assertEqual(threading.get_ident(), self.thread)
                yield dict(phase='preparing', message='B uploading')
            trace.append('B upload complete')
            return dict(state='review', message='B ready')

        self.add('A', publish())
        self.assertTrue(self.tasks.busy)
        self.tasks.step()
        self.assertFalse(self.tasks.busy)
        self.add('B', prepare())
        for _ in range(7):
            self.tasks.step()
        self.assertIn('B upload complete', trace)
        self.assertIn('A', self.tasks.tasks)
        self.assertNotIn('B', self.tasks.tasks)
        self.assertEqual(trace.count('A clicked once'), 1)
        start, end = trace.index('B upload started'), trace.index('B upload complete')
        self.assertIn('A receipt observed', trace[start:end])
        self.assertFalse(self.tasks.busy)
        ready = True
        self.tasks.step()
        self.assertFalse(self.tasks.tasks)
        self.assertEqual(trace.count('A lock released'), 1)
        done = [(request, extra['result']['state']) for request, kind, _, extra in self.events
                if kind == 'post_result']
        self.assertEqual(done, [('B', 'review'), ('A', 'published')])

    def test_cancelled_observation_releases_its_lock_and_other_job_completes(self):
        stop = threading.Event()
        closed = []

        def waiting():
            try:
                while True:
                    yield dict(phase='verifying')
            finally:
                closed.append('A')

        def other():
            yield dict(phase='preparing')
            return dict(state='review')

        self.add('A', waiting(), cancel=stop)
        self.tasks.step()
        self.add('B', other())
        stop.set()
        for _ in range(3):
            self.tasks.step()
        self.assertEqual(closed, ['A'])
        self.assertFalse(self.tasks.tasks)
        self.assertTrue(any(request == 'A' and kind == 'post_error' for request, kind, _, _ in self.events))
        self.assertTrue(any(request == 'B' and kind == 'post_result' for request, kind, _, _ in self.events))

    def test_same_post_cannot_have_second_action_while_receipt_pending(self):
        def waiting():
            while True:
                yield dict(phase='verifying')

        self.add('A', waiting(), post='one-post')
        self.tasks.step()
        with self.assertRaises(ValueError):
            self.add('B', waiting(), post='one-post')
        self.assertEqual(list(self.tasks.tasks), ['A'])

    def test_close_runs_all_finalizers_once_without_receipt_success(self):
        closed = []

        def waiting(name):
            try:
                while True:
                    yield dict(phase='verifying')
            finally:
                closed.append(name)

        self.add('A', waiting('A'))
        self.add('B', waiting('B'))
        self.tasks.step()
        self.tasks.step()
        self.tasks.close()
        self.tasks.close()
        self.assertEqual(closed, ['A', 'B'])
        self.assertEqual(sum(kind == 'post_error' for _, kind, _, _ in self.events), 2)
        self.assertFalse(any(kind == 'post_result' for _, kind, _, _ in self.events))


class CooperativeBrowserWorkerTests(unittest.TestCase):
    setUp = worker_fixtures.PagePostWorkerTests.setUp
    command = worker_fixtures.PagePostWorkerTests.command
    post_events = worker_fixtures.PagePostWorkerTests.post_events
    browse = worker_fixtures.PagePostWorkerTests.browse
    close_under_lock = worker_fixtures.PagePostWorkerTests.close_under_lock

    def test_verify_steps_only_resumes_observation_without_prepare_or_publish(self):
        read_count = 0

        def verify(context, account_id, page_key, post_id, **kwargs):
            nonlocal read_count
            self.assertIs(context, self.context)
            read_count += 1
            yield dict(phase='verifying', message='Existing submission under observation')
            read_count += 1
            return dict(state='published', message='Existing publication verified')

        # Intentionally no verify(), prepare() or publish() method: resuming a
        # receipt must work without any path that opens a composer or submits.
        self.factory.return_value = SimpleNamespace(verify_steps=verify)
        self.commands.put(self.command(operation='verify'))
        loops = 0

        def advance(_):
            nonlocal loops
            loops += 1
            if self.post_events('post_result') or loops >= 5:
                self.finished.set()

        self.page.wait_for_timeout.side_effect = advance
        self.assertEqual(self.browse(), 'finished')
        self.assertEqual(read_count, 2)
        self.assertEqual(self.post_events('post_verifying')[0]['operation'], 'verify')
        self.assertEqual(self.post_events('post_result')[0]['result']['state'], 'published')
        self.assertFalse(self.post_events('post_error'))

    def test_worker_runs_next_prepare_before_previous_receipt_with_same_browser_thread(self):
        trace = []
        thread = threading.get_ident()
        b_finished = False

        def publishing(context, account_id, page_key, post_id, **kwargs):
            self.assertIs(context, self.context)
            trace.append('A clicked')
            while not b_finished:
                self.assertEqual(threading.get_ident(), thread)
                trace.append('A checking')
                yield dict(phase='verifying', message='A receipt pending')
            return dict(state='published', message='A confirmed')

        def preparing(context, account_id, page_key, post_id, **kwargs):
            nonlocal b_finished
            self.assertIs(context, self.context)
            trace.append('B started')
            for _ in range(2):
                self.assertEqual(threading.get_ident(), thread)
                yield dict(phase='preparing', message='B upload pending')
            b_finished = True
            trace.append('B finished')
            return dict(state='review', message='B ready')

        self.publisher.publish_steps = publishing
        self.publisher.prepare_steps = preparing
        self.commands.put(self.command(operation='publish'))
        self.commands.put(self.command(operation='prepare', request_id='e'*32, post_id='f'*32))
        loops = 0

        def advance(_):
            nonlocal loops
            loops += 1
            if len(self.post_events('post_result')) == 2 or loops >= 20:
                self.finished.set()

        self.page.wait_for_timeout.side_effect = advance
        self.assertEqual(self.browse(), 'finished')
        self.assertLess(loops, 20)
        self.assertEqual(trace.count('A clicked'), 1)
        self.assertIn('A checking', trace[trace.index('B started'):trace.index('B finished')])
        self.assertEqual([event['request_id'] for event in self.post_events('post_result')], ['e'*32, 'd'*32])
        self.assertEqual(self.post_events('post_verifying')[0]['request_id'], 'd'*32)
        self.publisher.publish.assert_not_called()
        self.publisher.prepare.assert_not_called()
        self.launch.assert_called_once()


if __name__ == '__main__':
    unittest.main()
