"""23/11/2026 (owner): the 'Đăng hôm nay' column counts by what the schedule table shows. ✘ only for "Cần kiểm tra" and
"Dừng trước khi bấm Đăng — Facebook chưa nhận bài"; "Chưa xác minh đã đăng" is ✔; "Đã tạm dừng" is not counted.
Disposable stores only; no browser, no Facebook."""
import time
from types import SimpleNamespace
import unittest

from teodub.facebook_ui import posts_today_text
from teodub.page_schedule_recovery import STOPPED_BEFORE_PUBLISH, UNVERIFIED, attempt_label
from teodub.page_schedule_ui import PageSchedulePanel
from tests import test_page_wait_longer as waitlonger  # Not imported by name: its own tests would run here again.


def row(ident, state, due=100.0):
    return dict(id=ident, state=state, due_at=due)


class MarkRuleTests(unittest.TestCase):
    def count(self, rows, labels):
        return posts_today_text(rows, 0, 1000, lambda r: labels.get(r['id']))

    def test_only_cần_kiểm_tra_and_stopped_before_publish_are_failures(self):
        rows = [row('a', 'needs_attention'), row('b', 'needs_attention'), row('c', 'needs_attention'), row('d', 'paused'),
                row('e', 'paused'), row('f', 'submitted'), row('g', 'armed'), row('h', 'scheduled'), row('i', 'cancelled')]
        labels = dict(b=UNVERIFIED, c=STOPPED_BEFORE_PUBLISH, e=STOPPED_BEFORE_PUBLISH)
        # ✘: a (Cần kiểm tra), c and e (stopped before publish). ✔: b (unverified) and f. ⏳: g. Not counted: d, h, i.
        self.assertEqual(self.count(rows, labels), '✔ 2 · ✘ 3 · ⏳ 1')

    def test_a_paused_schedule_and_an_unverified_one_are_not_failures(self):
        self.assertEqual(self.count([row('d', 'paused')], {}), '—')
        self.assertEqual(self.count([row('b', 'needs_attention')], dict(b=UNVERIFIED)), '✔ 1')

    def test_without_labels_it_counts_as_before(self):
        rows = [row('a', 'needs_attention'), row('f', 'published'), row('g', 'preparing'), row('d', 'paused')]
        self.assertEqual(posts_today_text(rows, 0, 1000), '✔ 1 · ✘ 1 · ⏳ 1')


class SharedLabelTests(waitlonger.LateStopTests):
    def run(self, result=None):  # Only the tests below, not the inherited ones.
        if self._testMethodName.startswith('test_shared_'):
            return super().run(result)
        return unittest.TestResult() if result is None else result

    def test_shared_the_column_counts_what_the_table_shows(self):
        late = self.stopped('a'*32)
        clicked = self.stopped('b'*32, up_to='processing')
        plain = self.store.update(self.schedule('c'*32)['id'], state='needs_attention')
        paused = self.store.update(self.schedule('d'*32)['id'], state='paused')
        panel = SimpleNamespace(store=self.controller.store)
        labels = [PageSchedulePanel.row_state_label(panel, r) for r in (late, clicked, plain, paused)]
        self.assertEqual(labels, [STOPPED_BEFORE_PUBLISH, UNVERIFIED, 'Cần kiểm tra', 'Đã tạm dừng'])
        self.assertEqual([attempt_label(self.store, r) for r in (late, clicked, plain, paused)],
                         [STOPPED_BEFORE_PUBLISH, UNVERIFIED, None, None])
        now = time.time()
        text = posts_today_text([late, clicked, plain, paused], now-86400, now+86400, lambda r: attempt_label(self.store, r))
        self.assertEqual(text, '✔ 1 · ✘ 2')  # ✔ unverified; ✘ stopped before publish + Cần kiểm tra; paused not counted.

    def test_shared_a_missing_post_gives_no_special_label(self):
        late = self.stopped('a'*32)
        self.assertIsNone(attempt_label(self.store, dict(late, post_id='f'*32)))


if __name__ == '__main__':
    unittest.main()
