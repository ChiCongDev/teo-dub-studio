"""Closing from a Page dialog targets its account, not the outer account picker."""
import unittest

from tests import test_facebook_ui as fixtures


class FacebookSessionCloseScopeTests(unittest.TestCase):
    setUp = fixtures.FacebookUITests.setUp
    account = fixtures.FacebookUITests.account

    def test_explicit_account_close_leaves_selected_other_account_and_session_intact(self):
        first = self.account('Account A')
        self.tab.open_facebook()
        first_session = self.tab.sessions[first]
        second = self.account('Account B')
        self.tab.open_facebook()
        second_session = self.tab.sessions[second]
        before = self.tab.store.load()
        self.assertEqual(self.tab.account_id, second)

        self.assertTrue(self.tab.finish_browser(account_id=first))

        self.assertTrue(first_session.finished.is_set())
        self.assertFalse(second_session.finished.is_set())
        self.assertFalse(second_session.cancel.is_set())
        self.assertEqual(self.tab.account_id, second)
        self.assertEqual(self.tab.store.load(), before)
        self.assertTrue(first_session.pending)
        self.assertTrue(second_session.pending)
        self.browser.assert_not_called()

    def test_missing_explicit_account_never_falls_back_to_selected_account(self):
        account = self.account()
        self.tab.open_facebook()
        session = self.tab.sessions[account]
        before = self.tab.store.load()
        self.assertFalse(self.tab.finish_browser(account_id='missing-account'))
        self.assertFalse(session.finished.is_set())
        self.assertFalse(session.cancel.is_set())
        self.assertEqual(self.tab.store.load(), before)

    def test_closed_explicit_session_is_passive_and_default_still_closes_current(self):
        first = self.account('Account A')
        self.tab.open_facebook()
        first_session = self.tab.sessions[first]
        first_session.pending = first_session.opened = False
        second = self.account('Account B')
        self.tab.open_facebook()
        second_session = self.tab.sessions[second]
        self.assertFalse(self.tab.finish_browser(account_id=first))
        self.assertFalse(first_session.finished.is_set())
        self.assertFalse(second_session.finished.is_set())
        self.assertTrue(self.tab.finish_browser())
        self.assertTrue(second_session.finished.is_set())
        self.assertFalse(first_session.finished.is_set())


if __name__ == '__main__':
    unittest.main()
