import json
from pathlib import Path
import tempfile
import unittest

from teodub.campaign_tracking import latest_progress, tracking_row, campaign_notice, processing_time
from teodub.channel_discovery import classify_input, resolved_video_url


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.job = Path(self.temp.name)
        self.item = dict(number=1, title='Đèn', url='https://www.douyin.com/video/123',
                         job=str(self.job), status='running', message='Đang chạy')

    def test_partial_event_and_missing_percent_preserve_last_valid_progress(self):
        events = [dict(stage='recognition', percent=32, message='Nhận dạng'),
                  dict(stage='translation', percent=None, message='Đang dịch')]
        (self.job/'events.jsonl').write_text('\n'.join(json.dumps(x) for x in events)+'\n{"stage":', encoding='utf-8')
        event, percent = latest_progress(self.job)
        self.assertEqual(event['stage'], 'translation')
        self.assertEqual(percent, 32)
        self.assertEqual(tracking_row(self.item)[3:5], ('Đang dịch', '32%'))

    def test_terminal_result_overrides_old_worker_stage(self):
        (self.job/'events.jsonl').write_text(json.dumps(dict(stage='voice', percent=70, message='Giọng'))+'\n', encoding='utf-8')
        for status, label in [('failed', 'Lỗi'), ('cancelled', 'Đã dừng'), ('completed', 'Hoàn tất')]:
            row = tracking_row(dict(self.item, status=status, message='Kết quả'))
            self.assertEqual(row[3], label)
            self.assertEqual(row[-1], 'Kết quả')
            self.assertEqual(row[4], '100%' if status == 'completed' else '70%')
        self.assertEqual(tracking_row(self.item, active=False)[3], 'Gián đoạn · Tiếp tục')

    def test_unreported_percent_is_not_fabricated(self):
        self.assertEqual(tracking_row(self.item)[4], '—')
        (self.job/'events.jsonl').write_text('[]\n'+json.dumps(dict(stage='voice', percent=200))+'\n', encoding='utf-8')
        self.assertEqual(tracking_row(self.item)[4], '—')

    def test_completed_timestamp_column_next_to_percent_uses_vietnam_time(self):
        row = tracking_row(dict(self.item, status='completed', completed_at='2026-10-01T01:02:03+00:00'))
        self.assertEqual(row[4:6], ('100%', '01/10/2026 08:02:03'))
        for status in ('pending', 'failed', 'cancelled', 'running'):
            with self.subTest(status=status):
                self.assertEqual(tracking_row(dict(self.item, status=status, completed_at='2026-10-01T01:02:03Z'))[5], '—')

    def test_old_completed_video_uses_completion_event_only_or_displays_unknown(self):
        item = dict(self.item, status='completed')
        self.assertEqual(tracking_row(item)[5], '—')
        event = dict(stage='voice', percent=90, time='2026-09-30T18:11:05')
        (self.job/'events.jsonl').write_text(json.dumps(event)+'\n', encoding='utf-8')
        self.assertEqual(tracking_row(item)[5], '—')
        event.update(stage='completed', percent=100)
        (self.job/'events.jsonl').write_text(json.dumps(event)+'\n', encoding='utf-8')
        self.assertEqual(tracking_row(item)[5], '30/09/2026 18:11:05')
        self.assertEqual(tracking_row(dict(item, completed_at='invalid'))[5], '30/09/2026 18:11:05')

    def test_pause_error_and_unknown_interruption_notices_are_truthful(self):
        manifest = dict(status='paused', message='Đã hết hạn mức Codex', items=[dict(self.item, status='failed')])
        self.assertIn('Tạm dừng: Đã hết hạn mức Codex', campaign_notice(manifest))
        self.assertIn('Kiểm tra hạn mức', campaign_notice(manifest))
        manifest.update(status='running', message='')
        self.assertIn('chưa xác định nguyên nhân', campaign_notice(manifest, active=False))
        self.assertNotIn('hết hạn mức', campaign_notice(manifest, active=False))
        manifest['items'][0].update(message='Lỗi kết nối mạng')
        self.assertIn('vẫn đang xử lý', campaign_notice(manifest, active=True))
        self.assertIn('Kiểm tra kết nối mạng', campaign_notice(manifest, active=True))
        manifest.update(status='completed', items=[dict(self.item, status='completed')])
        self.assertEqual(campaign_notice(manifest), '')

    def test_duration_rejects_invalid_metadata(self):
        self.assertEqual(processing_time(dict(processing_seconds=3665.9)), '01:01:05')
        for value in (None, -1, True, float('nan'), float('inf'), '60'):
            self.assertEqual(processing_time(dict(processing_seconds=value)), 'Chưa ghi nhận')

    def test_automatic_channel_and_video_classification(self):
        self.assertEqual(classify_input('https://www.douyin.com/user/OWNER')[0], 'channel')
        self.assertEqual(classify_input('[kênh](https://v.douyin.com/ABC/)')[0], 'channel')
        self.assertEqual(classify_input('https://www.douyin.com/video/123')[0], 'videos')
        self.assertEqual(classify_input('https://www.douyin.com/video/123\nhttps://www.douyin.com/video/456')[0], 'videos')
        self.assertEqual(classify_input('https://v.douyin.com/ABC/', 'Danh sách link video')[0], 'videos')
        with self.assertRaises(ValueError):
            classify_input('https://www.douyin.com/user/OWNER\nhttps://www.douyin.com/video/123')

    def test_short_video_redirect_is_validated(self):
        self.assertEqual(resolved_video_url('https://www.douyin.com/jingxuan?modal_id=123'), 'https://www.douyin.com/video/123')
        self.assertEqual(resolved_video_url('https://www.iesdouyin.com/share/video/456/'), 'https://www.douyin.com/video/456')
        self.assertIsNone(resolved_video_url('https://evil.com/video/123'))
        self.assertIsNone(resolved_video_url('https://www.douyin.com/user/OWNER'))


if __name__ == '__main__':
    unittest.main()
