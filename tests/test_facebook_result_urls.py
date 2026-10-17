"""Stable IDs for verified browser results, without changing manual URL policy."""
import unittest

from teodub.facebook_accounts import facebook_post_url, facebook_result_url


class FacebookResultURLTests(unittest.TestCase):
    def test_verified_insights_keeps_opaque_id_and_removes_other_query_data(self):
        value = 'https://m.facebook.com:443/content/insights?content_id=UzpfSTYx%2Babc%3D&ref=notification&token=discard#fragment'
        self.assertEqual(facebook_result_url(value),
            'https://www.facebook.com/content/insights/?content_id=UzpfSTYx%2Babc%3D')

    def test_distinct_content_ids_do_not_collapse_into_one_url(self):
        one = facebook_result_url('https://www.facebook.com/content/insights/?content_id=opaqueA')
        two = facebook_result_url('https://www.facebook.com/content/insights/?content_id=opaqueB')
        self.assertNotEqual(one, two)
        self.assertEqual(facebook_result_url(one), one)

    def test_insights_requires_one_nonempty_safe_id_and_allowed_https_origin(self):
        invalid = [
            'https://www.facebook.com/content/insights/',
            'https://www.facebook.com/content/insights/?content_id=',
            'https://www.facebook.com/content/insights/?content_id=A&content_id=B',
            'https://www.facebook.com/content/insights/?content_id=A&content_id=',
            'https://www.facebook.com/content/insights/?content_id=with%20space',
            'https://www.facebook.com/content/insights/?content_id=bad%0Aline',
            'https://www.facebook.com/content/insights/?content_id=bad%7Fid',
            'https://www.facebook.com/content/insights/?content_id='+'x'*2049,
            'http://www.facebook.com/content/insights/?content_id=A',
            'https://business.facebook.com/content/insights/?content_id=A',
            'https://www.facebook.com.evil.example/content/insights/?content_id=A',
            'https://user:password@www.facebook.com/content/insights/?content_id=A',
            'https://www.facebook.com:444/content/insights/?content_id=A',
            'https://www.facebook.com:bad/content/insights/?content_id=A',
            'https://www.facebook.com/content/insights/other?content_id=A',
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    facebook_result_url(value)

    def test_normal_public_post_addresses_keep_existing_normalization(self):
        for value in ('https://m.facebook.com/reel/123/?ref=tracking',
                      'https://www.facebook.com/story.php?story_fbid=12&id=100&ref=x',
                      'https://www.facebook.com/watch/?v=123&ref=x'):
            with self.subTest(value=value):
                self.assertEqual(facebook_result_url(value), facebook_post_url(value))
        # Existing manual URL handling deliberately does not acquire the new
        # permission to store private management references as verified proof.
        insights = 'https://www.facebook.com/content/insights/?content_id=opaqueA'
        self.assertEqual(facebook_post_url(insights), 'https://www.facebook.com/content/insights')

    def test_opening_verified_insights_keeps_its_exact_content_identity(self):
        from teodub.facebook_accounts import facebook_browser_url
        source = 'https://www.facebook.com/content/insights/?content_id=opaque%2FA%3D&ref=tracking'
        self.assertEqual(facebook_browser_url(source), facebook_result_url(source))
        with self.assertRaises(ValueError):
            facebook_browser_url('https://www.facebook.com/content/insights/')


if __name__ == '__main__':
    unittest.main()
