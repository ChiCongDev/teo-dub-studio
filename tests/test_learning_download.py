"""Actual MP4 validation/ffmpeg merge, synthetic platform transport only."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from teodub import learning_download as module
from teodub.common import Cancelled, hash_file

YT = 'https://www.youtube.com/watch?v=BaW_jenozKc'
FB = 'https://www.facebook.com/reel/123456789'


class LearningURLTests(unittest.TestCase):
    def test_youtube_single_video_forms_canonicalize_to_same_identity(self):
        for url in ('https://youtu.be/BaW_jenozKc?si=tracking',
                    'https://m.youtube.com/watch?v=BaW_jenozKc&list=playlist&t=10',
                    'https://www.youtube.com/shorts/BaW_jenozKc',
                    'https://www.youtube-nocookie.com/embed/BaW_jenozKc',
                    f'Video mẫu [{"Tên"}]({YT})'):
            self.assertEqual(module.learning_url(url), YT)

    def test_facebook_reel_watch_video_and_share_forms(self):
        for url in ('https://m.facebook.com/reel/123456789/?mibextid=xyz',
                    'https://www.facebook.com/reel/123456789'):
            self.assertEqual(module.learning_url(url), FB)
        cases = {
            'https://www.facebook.com/video.php?v=123456789&ref=watch': 'https://www.facebook.com/watch/?v=123456789',
            'https://facebook.com/Page/videos/12345?mibextid=xyz': 'https://www.facebook.com/Page/videos/12345/',
            'https://www.facebook.com/Page/videos/title/12345/': 'https://www.facebook.com/Page/videos/title/12345/',
            'https://www.facebook.com/share/r/Abcd123/?mibextid=xyz': 'https://www.facebook.com/share/r/Abcd123/',
            'https://fb.watch/Abcd123/': 'https://fb.watch/Abcd123/',
        }
        for url, result in cases.items():
            self.assertEqual(module.learning_url(url), result)

    def test_channel_home_playlist_foreign_file_and_credentials_rejected(self):
        for url in ('https://youtube.com/@page', 'https://youtube.com/playlist?list=abc',
                    'https://facebook.com/Page', 'https://facebook.com/profile.php?id=123',
                    'https://facebook.com.evil.test/reel/123', 'https://youtube.com.evil.test/watch?v=BaW_jenozKc',
                    'https://user:pass@youtube.com/watch?v=BaW_jenozKc', 'file:///C:/private.mp4',
                    'https://youtube.com/watch?v=BaW_jenozKc&v=xxxxxxxxxxx', YT+' '+FB):
            with self.subTest(url=url), self.assertRaises(ValueError):
                module.learning_url(url)


class LearningDownloadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.job = self.root/'analysis'/'link-01'
        self.stop = threading.Event()
        self.events = []
        self.info = dict(title='Video học từ link', duration=5)
        import imageio_ffmpeg
        self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self.media = self.root/'fixture.mp4'
        subprocess.run([self.ffmpeg, '-nostdin', '-v', 'error', '-f','lavfi',
            '-i','color=c=blue:s=160x120:r=10:d=5', '-f','lavfi', '-i','sine=frequency=440:duration=5',
            '-c:v','libx264','-c:a','aac','-shortest',str(self.media)], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        test = self
        class FakeYDL:
            def __init__(self, opts): test.opts = self.opts = opts
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def extract_info(self, url, download=False):
                test.extracted = url
                return test.info
            def process_info(self, info):
                test.processed = True
                target = Path(self.opts['outtmpl'].replace('%(ext)s','mp4'))
                shutil.copyfile(test.media,target)
                for hook in self.opts['progress_hooks']:
                    hook(dict(status='finished',filename=str(target),downloaded_bytes=target.stat().st_size))
        self.factory = FakeYDL
        self.processed = False

    def run_download(self, url=FB):
        return module.download_learning_video(url,self.job,cancel=self.stop,
            progress=lambda message,percent=None:self.events.append(message),ydl_factory=self.factory)

    def test_real_media_verified_result_and_no_credentials_or_remote_runtime_scripts(self):
        result = self.run_download()
        self.assertEqual(result['platform'],'Facebook')
        self.assertEqual(result['metadata']['sha256'],hash_file(self.media))
        self.assertTrue(result['metadata']['has_audio'])
        self.assertEqual(result['metadata']['decoded_frames'],50)
        self.assertEqual(json.loads((self.job/'result.json').read_text(encoding='utf-8')),result)
        self.assertEqual(self.opts['merge_output_format'],'mp4')
        self.assertNotIn('cookiesfrombrowser',self.opts)
        self.assertNotIn('cookiefile',self.opts)
        self.assertEqual(self.opts['remote_components'],set())
        self.assertEqual(self.opts['ffmpeg_location'],self.ffmpeg)

    def test_youtube_config_uses_explicit_runtime_and_split_audio_video_format(self):
        with patch.object(module,'_node_runtime',return_value='C:/teo/node.exe'):
            result = self.run_download(YT)
        self.assertEqual(result['platform'],'YouTube')
        self.assertEqual(self.opts['js_runtimes'],{'node':{'path':'C:/teo/node.exe'}})
        self.assertIn('+ba',self.opts['format'])

    def test_playlist_live_and_large_video_stop_before_downloading(self):
        for info in (dict(_type='playlist',entries=[]),dict(is_live=True),dict(filesize=module.MAX_BYTES+1)):
            with self.subTest(info=info):
                self.job = self.root/'analysis'/str(len(list(self.root.rglob('result.json'))))
                self.info=info; self.processed=False
                with self.assertRaises(ValueError): self.run_download()
                self.assertFalse(self.processed)
                self.assertEqual(json.loads((self.job/'result.json').read_text(encoding='utf-8'))['status'],'failed')

    def test_cancellation_never_publishes_success_or_replays_existing_job(self):
        self.stop.set()
        with self.assertRaises(Cancelled): self.run_download()
        self.assertFalse(self.job.exists())
        self.stop.clear(); self.run_download()
        self.processed=False
        with self.assertRaises(FileExistsError): self.run_download()
        self.assertFalse(self.processed)

    def test_cancel_between_audio_video_fragments_preserves_cancel_reason(self):
        test=self
        class CancelYDL(self.factory):
            def process_info(self,info):
                test.stop.set()
                self.opts['progress_hooks'][0](dict(downloaded_bytes=100))
        self.factory=CancelYDL
        with self.assertRaises(Cancelled): self.run_download()
        self.assertEqual(json.loads((self.job/'result.json').read_text(encoding='utf-8'))['status'],'cancelled')

    def test_auth_error_gives_actionable_message_without_echoing_raw_details(self):
        class LoginYDL(self.factory):
            def extract_info(self,*args,**kwargs):
                raise RuntimeError('Login required cookie_value=secret')
        self.factory=LoginYDL
        with self.assertRaisesRegex(ValueError,'đăng nhập') as failure: self.run_download()
        self.assertNotIn('secret',str(failure.exception))
        self.assertNotIn('secret',(self.job/'result.json').read_text(encoding='utf-8'))

    def test_actual_ytdlp_merger_works_with_installed_bundled_ffmpeg(self):
        from yt_dlp import YoutubeDL
        from yt_dlp.postprocessor.ffmpeg import FFmpegMergerPP
        video=self.root/'v.mp4'; audio=self.root/'a.m4a'; output=self.root/'merged.mp4'
        for target,options in ((video,['-map','0:v:0','-an']), (audio,['-map','0:a:0','-vn'])):
            subprocess.run([self.ffmpeg,'-nostdin','-v','error','-i',str(self.media),*options,'-c','copy',str(target)],
                check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        with YoutubeDL(dict(ffmpeg_location=self.ffmpeg,quiet=True)) as ydl:
            _,info=FFmpegMergerPP(ydl).run(dict(filepath=str(output),ext='mp4',
                requested_formats=[dict(vcodec='h264',acodec='none',protocol='https'),
                                   dict(vcodec='none',acodec='aac',protocol='https')],
                __files_to_merge=[str(video),str(audio)]))
        from teodub.downloaders.video_download import validate_downloaded_video
        self.assertTrue(validate_downloaded_video(output,lambda:None)['has_audio'])


if __name__=='__main__': unittest.main()
