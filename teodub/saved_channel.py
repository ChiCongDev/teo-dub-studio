"""Import the public post links from a channel HTML file saved by the user."""
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

from teodub.channel_discovery import MAX_SCAN_VIDEOS, channel_url, resolved_video_url

MAX_SIZE = 32*1024*1024
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


class SavedChannelParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.channels = []
        self.items = {}
        self.anchor = None
        self.container_found = False

    def add_channel(self, value):
        try:
            normalized = channel_url(value)
        except ValueError:
            return
        if '/user/' in normalized and normalized not in self.channels:
            self.channels.append(normalized)

    def handle_comment(self, text):
        if 'saved from url=' in text.lower():
            match = re.search(r'https?://\S+', text)
            if match:
                self.add_channel(match[0])

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'link' and 'canonical' in str(attrs.get('rel', '')).split():
            self.add_channel(attrs.get('href') or '')
        if tag == 'meta' and attrs.get('property') == 'og:url':
            self.add_channel(attrs.get('content') or '')
        scoped = bool(self.stack and self.stack[-1][1]) or attrs.get('data-e2e') == 'user-post-list'
        self.container_found |= attrs.get('data-e2e') == 'user-post-list'
        if tag not in VOID:
            self.stack.append((tag, scoped))
        if tag == 'a' and scoped:
            href = urljoin('https://www.douyin.com', attrs.get('href') or '')
            try:
                parsed = urlsplit(href)
                is_post = (parsed.scheme in ('http', 'https') and not parsed.username and not parsed.password
                           and parsed.port in (None, 80, 443)
                           and re.fullmatch(r'/(?:share/)?video/\d+/?', parsed.path))
                url = resolved_video_url(href) if is_post else None
            except ValueError:
                url = None
            self.anchor = dict(url=url, title=attrs.get('title') or attrs.get('aria-label') or '', text=[])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, text):
        if self.anchor and not any(tag in ('script', 'style') for tag, _ in self.stack):
            self.anchor['text'].append(text)

    def handle_endtag(self, tag):
        if tag == 'a' and self.anchor:
            url = self.anchor['url']
            title = ' '.join((' '.join(self.anchor['text']) or self.anchor['title']).split())[:300]
            if url:
                self.items.setdefault(url, dict(url=url, title=title))
            self.anchor = None
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def import_saved_channel(path, *, limit=100, expected_channel=None):
    if type(limit) is not int or not 1 <= limit <= MAX_SCAN_VIDEOS:
        raise ValueError('Chọn từ 1 đến 10.000 video.')
    path = Path(path)
    if path.suffix.lower() not in ('.html', '.htm'):
        raise ValueError('Chọn tệp trang kênh .html hoặc .htm do trình duyệt lưu.')
    if path.stat().st_size > MAX_SIZE:
        raise ValueError('Trang đã lưu quá lớn (tối đa 32 MB).')
    parser = SavedChannelParser()
    parser.feed(path.read_bytes().decode('utf-8-sig', errors='replace'))
    parser.close()
    if len(parser.channels) > 1:
        raise ValueError('Trang đã lưu chứa nhiều địa chỉ kênh; chọn đúng trang cá nhân Douyin.')
    expected = channel_url(expected_channel) if expected_channel else None
    if expected and '/user/' not in expected:
        expected = None
    channel = parser.channels[0] if parser.channels else expected
    if not channel:
        raise ValueError('Chưa xác định được kênh trong trang đã lưu. Dán link kênh đầy đủ /user/… rồi nhập lại.')
    if expected and channel != expected:
        raise ValueError('Trang đã lưu thuộc kênh khác với link đang nhập. Chọn đúng trang hoặc sửa link kênh.')
    if not parser.container_found or not parser.items:
        raise ValueError('Trang đã lưu chưa có danh sách video của kênh. Mở tab Tác phẩm, cuộn để tải video rồi lưu lại trang bằng Ctrl+S.')
    items = list(parser.items.values())[:limit]
    return dict(channel=channel, items=items, complete=False, reason='saved_page',
                message=f'Đã nhập {len(items)} video từ trang bạn lưu. Chỉ gồm video đã tải trên trang; chưa khẳng định hết kênh.',
                browser='saved_page', requested_browser='saved_page', file_name=path.name, skipped_image_posts=0)
