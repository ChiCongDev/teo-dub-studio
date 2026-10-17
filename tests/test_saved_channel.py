from pathlib import Path
import tempfile
import unittest

from teodub.saved_channel import import_saved_channel


class SavedChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='teo saved channel tests ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.channel = 'https://www.douyin.com/user/ownerABC'

    def saved_page(self, body, metadata=None, suffix='.html'):
        if metadata is None:
            metadata = '<link rel="canonical" href="'+self.channel+'">'
        path = self.root/('channel'+suffix)
        path.write_text('<!doctype html><html><head>'+metadata+'</head><body>'
                        +body+'</body></html>', encoding='utf-8')
        return path

    def listing(self, anchors):
        return '<div data-e2e="user-post-list">'+anchors+'</div>'

    def test_collects_only_channel_list_links_and_normalizes_titles(self):
        body = ('<aside><a href="/video/999">Recommendation before</a></aside>'
                +self.listing('<article><a href="/video/123?from=profile">'
                              '  Đoạn   clip\n<span>A &amp; B</span> </a></article>'
                              '<a href="https://www.douyin.com/video/456">Second</a>')
                +'<a href="/video/888">Recommendation after</a>')
        result = import_saved_channel(self.saved_page(body))
        self.assertEqual(result['channel'], self.channel)
        self.assertEqual(result['items'], [
            {'url': 'https://www.douyin.com/video/123', 'title': 'Đoạn clip A & B'},
            {'url': 'https://www.douyin.com/video/456', 'title': 'Second'},
        ])
        self.assertIs(result['complete'], False)
        self.assertEqual(result['reason'], 'saved_page')
        self.assertEqual(result['browser'], 'saved_page')
        self.assertEqual(result['requested_browser'], 'saved_page')
        self.assertTrue(result['message'])

    def test_deduplicates_in_document_order_and_preserves_first_title(self):
        anchors = ('<a href="/video/456">First</a>'
                   '<a href="/video/123">Second</a>'
                   '<a href="https://www.douyin.com/video/456?x=1">Duplicate</a>'
                   '<a href="/video/789">Third</a>')
        result = import_saved_channel(self.saved_page(self.listing(anchors)))
        self.assertEqual(result['items'], [
            {'url': 'https://www.douyin.com/video/456', 'title': 'First'},
            {'url': 'https://www.douyin.com/video/123', 'title': 'Second'},
            {'url': 'https://www.douyin.com/video/789', 'title': 'Third'},
        ])

    def test_excludes_notes_foreign_hosts_and_non_video_links(self):
        anchors = ('<a href="/note/111">Photo note</a>'
                   '<a href="https://example.com/video/222">Foreign</a>'
                   '<a href="https://www.douyin.com.evil.example/video/333">False host</a>'
                   '<a href="javascript:location.href=\'/video/444\'">Script</a>'
                   '<a href="/user/other">Other profile</a>'
                   '<a href="/video/not-a-number">Invalid ID</a>'
                   '<a href="/video/123">Valid</a>')
        result = import_saved_channel(self.saved_page(self.listing(anchors)))
        self.assertEqual(result['items'], [
            {'url': 'https://www.douyin.com/video/123', 'title': 'Valid'},
        ])

    def test_limit_applies_after_deduplication(self):
        anchors = ('<a href="/video/123">First</a>'
                   '<a href="/video/123">Duplicate</a>'
                   '<a href="/video/456">Second</a>'
                   '<a href="/video/789">Third</a>')
        result = import_saved_channel(self.saved_page(self.listing(anchors)), limit=2)
        self.assertEqual([row['url'] for row in result['items']], [
            'https://www.douyin.com/video/123',
            'https://www.douyin.com/video/456',
        ])
        self.assertIs(result['complete'], False)

    def test_modal_id_on_non_video_links_does_not_count_as_a_post(self):
        anchors = ('<a href="/user/ownerABC?modal_id=999">Profile modal</a>'
                   '<a href="/?modal_id=888">Home modal</a>'
                   '<a href="/video/123">Valid</a>')
        result = import_saved_channel(self.saved_page(self.listing(anchors)))
        self.assertEqual(result['items'], [
            {'url': 'https://www.douyin.com/video/123', 'title': 'Valid'},
        ])

    def test_og_url_identifies_the_channel(self):
        metadata = '<meta property="og:url" content="'+self.channel+'?source=share">'
        result = import_saved_channel(self.saved_page(
            self.listing('<a href="/video/123">Clip</a>'), metadata=metadata))
        self.assertEqual(result['channel'], self.channel)

    def test_saved_from_comment_identifies_the_channel(self):
        metadata = '<!-- saved from url=(0043)'+self.channel+' -->'
        result = import_saved_channel(self.saved_page(
            self.listing('<a href="/video/123">Clip</a>'), metadata=metadata))
        self.assertEqual(result['channel'], self.channel)

    def test_expected_channel_fills_missing_metadata(self):
        result = import_saved_channel(self.saved_page(
            self.listing('<a href="/video/123">Clip</a>'), metadata=''),
            expected_channel=self.channel)
        self.assertEqual(result['channel'], self.channel)
        self.assertEqual(len(result['items']), 1)

    def test_missing_metadata_requires_expected_channel(self):
        path = self.saved_page(self.listing('<a href="/video/123">Clip</a>'), metadata='')
        with self.assertRaises(ValueError):
            import_saved_channel(path)

    def test_mismatched_channel_is_rejected(self):
        path = self.saved_page(self.listing('<a href="/video/123">Clip</a>'))
        with self.assertRaises(ValueError):
            import_saved_channel(path, expected_channel='https://www.douyin.com/user/other')

    def test_matching_normalized_expected_channel_is_accepted(self):
        path = self.saved_page(self.listing('<a href="/video/123">Clip</a>'))
        result = import_saved_channel(path,
            expected_channel='https://m.douyin.com/share/user/ownerABC?source=share')
        self.assertEqual(result['channel'], self.channel)

    def test_short_expected_link_cannot_supply_a_missing_owner(self):
        path = self.saved_page(self.listing('<a href="/video/123">Clip</a>'), metadata='')
        with self.assertRaises(ValueError):
            import_saved_channel(path, expected_channel='https://v.douyin.com/ABC/')

    def test_recommendation_links_cannot_supply_missing_channel_posts(self):
        body = '<aside><a href="/video/123">Recommendation</a></aside>'
        with self.assertRaises(ValueError):
            import_saved_channel(self.saved_page(body))

    def test_empty_post_list_is_rejected(self):
        with self.assertRaises(ValueError):
            import_saved_channel(self.saved_page(self.listing('')))

    def test_accepts_htm_and_uppercase_html_extensions(self):
        for suffix in ('.htm', '.HTML'):
            with self.subTest(suffix=suffix):
                path = self.saved_page(self.listing('<a href="/video/123">Clip</a>'),
                                       suffix=suffix)
                self.assertEqual(len(import_saved_channel(path)['items']), 1)

    def test_rejects_non_html_files(self):
        path = self.saved_page(self.listing('<a href="/video/123">Clip</a>'), suffix='.txt')
        with self.assertRaises(ValueError):
            import_saved_channel(path)

    def test_rejects_file_larger_than_32_mib(self):
        path = self.root/'large.html'
        with path.open('wb') as stream:
            stream.seek(32*1024*1024)
            stream.write(b' ')
        with self.assertRaises(ValueError):
            import_saved_channel(path, expected_channel=self.channel)

    def test_rejects_invalid_limits(self):
        path = self.saved_page(self.listing('<a href="/video/123">Clip</a>'))
        for limit in (0, 1001, -1, True, 1.5, '10'):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    import_saved_channel(path, limit=limit)


if __name__ == '__main__':
    unittest.main()
