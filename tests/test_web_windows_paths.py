from pathlib import Path
import unittest
from unittest.mock import patch

from teodub.pipeline import _bounded_output_name


class WindowsOutputPaths(unittest.TestCase):
    def test_redirected_appdata_unicode_title_shortens_consistently(self):
        root=Path(r'C:\Users\chungpc\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\TeoDubWeb\outputs\34710c84d03c4011b4e129a5a045f54b')
        directory=Path('34710c84d03c4011b4e129a5a045f54b')
        title='Teo_'+'视频标题'*16+'_06-10-2026_34710c84'
        result=_bounded_output_name(root,title,directory)
        self.assertEqual(result,'Teo_34710c84d03c')
        self.assertLessEqual(len(str(root/result/(result+'.mp4'))),230)
        self.assertEqual(result,_bounded_output_name(root,title,directory))

    def test_short_existing_names_retained(self):
        self.assertEqual(_bounded_output_name(Path(r'C:\Videos'),'Video 1_06-10-2026',Path('job')), 'Video 1_06-10-2026')

    def test_extreme_root_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError,'thư mục lưu ngắn hơn'):
            _bounded_output_name(Path('C:/'+('a'*210)),'video',Path('job'))


if __name__=='__main__':unittest.main()
