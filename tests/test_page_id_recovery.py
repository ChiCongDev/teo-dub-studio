"""Missing username Page IDs must fail before inference, never be guessed."""
import unittest
from unittest.mock import patch

import test_page_ai as planning
import test_facebook_page_discovery as discovery
import test_facebook_page_cards_js as cards
from teodub.facebook_accounts import FacebookAccounts
from teodub.page_ai import require_page_id


class PlanningIdTests(unittest.TestCase):
    setUp = planning.PageAITests.setUp
    saved = planning.PageAITests.saved
    task = planning.PageAITests.task

    def test_missing_id_refuses_save_without_overwriting_old_policy(self):
        original = self.saved()
        FacebookAccounts(self.root).save_page(self.account, 'Synthetic Page',
            'https://www.facebook.com/a.cau.ngao', '', record_id=self.page)
        with self.assertRaisesRegex(ValueError, 'Page chưa có Page ID'):
            self.saved()
        self.assertEqual(self.store.policy(self.account, self.page), original)
        self.invoke.assert_not_called()

    def test_old_frozen_empty_id_stops_before_api_or_ai_and_disables_auto(self):
        task = self.task(auto_campaign=True, auto_publish=True, texts_per_week=0,
                         slot_start='06:00', slot_gap=780)
        task['auto'] = True
        task['config']['target']['page_id'] = ''
        self.controller.pending = task
        with patch.object(self.store.plans.activity, 'connection') as api:
            self.controller.work(task)
            self.controller.observe()
        api.assert_not_called()
        self.invoke.assert_not_called()
        self.assertFalse(self.store.policy(self.account, self.page)['config']['auto_campaign'])
        run = self.store.runs(self.account, self.page)[0]
        self.assertEqual(run['state'], 'needs_attention')
        self.assertIn('Page chưa có Page ID', run['message'])
        self.assertEqual(self.scheduler.store.list(self.account, self.page), [])

    def test_resaving_after_numeric_id_recovery_freezes_new_target(self):
        self.saved()
        accounts = FacebookAccounts(self.root)
        accounts.save_page(self.account, 'Synthetic Page', 'https://www.facebook.com/a.cau.ngao',
                           '', record_id=self.page)
        accounts.merge_discovered_pages(self.account, [dict(name='Synthetic Page',
            url='https://www.facebook.com/a.cau.ngao', page_id='61512345678901')])
        result = self.saved()
        self.assertEqual(result['config']['target']['page_id'], '61512345678901')
        self.invoke.assert_not_called()

    def test_id_type_and_ascii_digits_match_plan_contract(self):
        for number in ('', None, 123, 'a.cau.ngao', '１２３', '123 ', '1'*33):
            with self.subTest(number=number), self.assertRaises(ValueError):
                require_page_id(dict(page_id=number))
        self.assertEqual(require_page_id(dict(page_id='123'))['page_id'], '123')


class CardHintTests(unittest.TestCase):
    setUp = discovery.FacebookDiscoveryTests.setUp
    advance = discovery.FacebookDiscoveryTests.advance
    scan = discovery.FacebookDiscoveryTests.scan
    probe_landing = discovery.FacebookDiscoveryTests.probe_landing

    def test_new_username_card_id_requires_observed_matching_redirect(self):
        self.snapshot['items'] = [dict(name='A Cẩu Ngáo', url='https://www.facebook.com/a.cau.ngao',
                                      id_candidates=['61512345678901'])]
        probe = self.probe_landing(['https://www.facebook.com/a.cau.ngao/'])
        self.assertEqual(self.scan()['items'][0]['page_id'], '61512345678901')
        probe.close.assert_called_once()

    def test_hint_landing_on_other_page_is_not_used(self):
        self.snapshot['items'] = [dict(name='A Cẩu Ngáo', url='https://www.facebook.com/a.cau.ngao',
                                      id_candidates=['61512345678901'])]
        self.probe_landing(['https://www.facebook.com/another.page'])
        self.assertEqual(self.scan()['items'][0]['page_id'], '')

    def test_two_ids_for_same_username_stay_unresolved(self):
        self.snapshot['items'] = [dict(name='A Cẩu Ngáo', url='https://www.facebook.com/a.cau.ngao',
                                      id_candidates=['61512345678901', '61512345678902'])]
        self.probe_landing(['https://www.facebook.com/a.cau.ngao']*2)
        self.assertEqual(self.scan()['items'][0]['page_id'], '')


@unittest.skipIf(cards.sync_playwright is None, 'playwright is not installed')
class CardDomTests(unittest.TestCase):
    setUpClass = classmethod(cards.PageCardTests.setUpClass.__func__)
    tearDownClass = classmethod(cards.PageCardTests.tearDownClass.__func__)
    read = cards.PageCardTests.read

    def test_numeric_create_link_is_only_a_probe_hint(self):
        snapshot = self.read(cards.page(cards.card('A Cẩu Ngáo',
            'https://www.facebook.com/a.cau.ngao', create_href='https://www.facebook.com/61512345678901')))
        self.assertEqual(len(snapshot['items']), 1)
        self.assertEqual(snapshot['items'][0]['page_id'], '')
        self.assertEqual(snapshot['items'][0]['id_candidates'], ['61512345678901'])

    def test_neighboring_card_hints_are_not_borrowed(self):
        snapshot = self.read(cards.page(
            cards.card('A Cẩu Ngáo', 'https://www.facebook.com/a.cau.ngao'),
            cards.card('Other', 'https://www.facebook.com/other.page',
                       create_href='https://www.facebook.com/61512345678902')))
        rows = {r['name']: r for r in snapshot['items']}
        self.assertEqual(rows['A Cẩu Ngáo'].get('id_candidates', []), [])
        self.assertEqual(rows['Other']['id_candidates'], ['61512345678902'])
