"""Drive controls in the app/campaign shell: temporary data and no cloud calls."""
from copy import deepcopy
import json
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from teodub.common import atomic_json, hash_file
from teodub.google_drive import normalize_drive_config
from tests import test_campaign_ui as campaign_ui_fixtures


def selected_drive(account_id='a' * 32, folder_id='folder_A'):
    return dict(version=1, enabled=True, account_id=account_id,
                folder_id=folder_id, folder_name='Thành phẩm')


class GoogleDriveAppUITests(unittest.TestCase):
    def setUp(self):
        self.cloud = self.enterContext(patch('teodub.google_drive.DriveClient'))
        self.dialog_cloud = self.enterContext(patch('teodub.google_drive_ui.DriveClient'))
        self.spawn = self.enterContext(patch('teodub.app.subprocess.Popen'))
        self.sound = self.enterContext(patch('teodub.app.winsound.PlaySound'))
        self.open = self.enterContext(patch('teodub.app.os.startfile'))
        self.f = campaign_ui_fixtures.CampaignUITests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.studio, self.tab = self.f.studio, self.f.tab
        self.root = self.f.root
        self.directory = Path(self.f.temp.name)
        self.studio.input.insert('1.0', 'https://www.douyin.com/video/111')
        self.studio.fields['original_dir'].set(str(self.directory/'single originals'))
        self.studio.fields['output_dir'].set(str(self.directory/'single outputs'))
        self.addCleanup(self.assert_no_cloud_activity)

    def assert_no_cloud_activity(self):
        self.cloud.assert_not_called()
        self.dialog_cloud.return_value.connect.assert_not_called()
        self.dialog_cloud.return_value.upload_video.assert_not_called()
        self.dialog_cloud.return_value.folders.assert_not_called()
        self.dialog_cloud.return_value.create_folder.assert_not_called()
        self.dialog_cloud.return_value.disconnect.assert_not_called()
        self.spawn.assert_not_called()
        self.open.assert_not_called()

    def choose(self, value=None):
        self.studio.fill_drive({'drive_upload': value or selected_drive()})

    def owned_job(self, *, drive=None, legacy=False, result=None):
        config = self.studio.config_from_form('full')
        if legacy:
            config.pop('drive_upload', None)
            config.pop('drive_data_dir', None)
        elif drive is not None:
            config['drive_upload'] = deepcopy(drive)
        directory = Path(config['directory'])
        directory.mkdir(parents=True)
        atomic_json(directory/'config.json', config)
        if result is not None:
            atomic_json(directory/'result.json', result)
        self.studio.current = deepcopy(config)
        self.studio.result = deepcopy(result)
        return config

    def manifest(self):
        self.tab.create()
        return json.loads((self.tab.directory/'campaign.json').read_text(encoding='utf-8'))

    def test_new_checkbox_starts_off_and_full_config_binds_exact_data_directory(self):
        self.assertFalse(self.studio.fields['drive_enabled'].get())
        self.assertEqual(self.studio.config_from_form('full')['drive_upload'], normalize_drive_config({}))
        self.choose()
        config = self.studio.config_from_form('full')
        self.assertEqual(config['drive_upload'], selected_drive())
        self.assertEqual(config['drive_data_dir'], str(self.studio.data_dir))
        self.assertIn('Thành phẩm', self.studio.drive_summary.get())
        self.assertEqual(str(self.studio.drive_check['state']), 'normal')

    def test_unconfigured_tick_cannot_create_a_full_job_but_preview_has_no_drive(self):
        self.studio.fields['drive_enabled'].set(True)
        with self.assertRaises(ValueError):
            self.studio.config_from_form('full')
        preview = self.studio.config_from_form('preview')
        self.assertNotIn('drive_upload', preview)
        self.assertNotIn('drive_data_dir', preview)
        self.cloud.assert_not_called()
        self.dialog_cloud.assert_not_called()

    def test_preview_omits_even_an_enabled_valid_account_and_does_not_change_it(self):
        self.choose()
        before = deepcopy(self.studio.drive_options)
        preview = self.studio.config_from_form('preview')
        self.assertNotIn('drive_upload', preview)
        self.assertNotIn('drive_data_dir', preview)
        self.studio.fill_form(preview)
        self.assertEqual(self.studio.drive_selection(), before)
        self.dialog_cloud.assert_not_called()

    def test_saved_preference_restores_for_new_work_without_overwriting_legacy_job(self):
        old = self.owned_job(legacy=True, result={'status': 'completed', 'message': 'Local video done'})
        old_directory = Path(old['directory'])
        old_digest = hash_file(old_directory/'config.json')
        self.choose(selected_drive('b'*32, 'folder_B'))
        self.studio.save_preferences()
        atomic_json(self.studio.data_dir/'last-job.json', dict(directory=old['directory']))
        self.studio.fill_drive({})
        self.studio.current = None
        self.studio.restore()
        self.assertEqual(self.studio.drive_selection(), selected_drive('b'*32, 'folder_B'))
        self.assertEqual(self.studio.current, old)
        self.assertNotIn('drive_upload', self.studio.current)
        self.assertEqual(hash_file(old_directory/'config.json'), old_digest)
        self.dialog_cloud.assert_not_called()

    def test_resume_reloads_frozen_account_and_folder_instead_of_current_form(self):
        self.choose()
        original = self.owned_job(result={'status': 'completed', 'google_drive': {'state': 'failed'}})
        self.choose(selected_drive('b'*32, 'folder_B'))
        with patch.object(self.studio, 'launch') as launch:
            self.studio.resume()
        launch.assert_called_once_with(original, fresh=False)
        self.assertEqual(self.studio.drive_selection(), selected_drive())
        self.assertEqual(json.loads((Path(original['directory'])/'config.json').read_text(encoding='utf-8')), original)

    def test_resume_legacy_job_stays_off_even_when_new_work_preference_is_enabled(self):
        original = self.owned_job(legacy=True, result={'status': 'cancelled'})
        self.choose()
        with patch.object(self.studio, 'launch') as launch:
            self.studio.resume()
        launch.assert_called_once_with(original, fresh=False)
        self.assertFalse(self.studio.fields['drive_enabled'].get())
        self.assertEqual(self.studio.drive_selection(), normalize_drive_config({}))

    def test_completed_failed_unknown_cancelled_delivery_can_continue_but_uploaded_cannot(self):
        self.choose()
        self.owned_job()
        for state in ('failed', 'unknown', 'cancelled', 'needs_auth', 'queued', 'uploading'):
            with self.subTest(state=state):
                self.studio.result = dict(status='completed', google_drive={'state': state})
                self.studio.refresh_buttons()
                self.assertEqual(str(self.studio.continue_button['state']), 'normal')
        self.studio.result = dict(status='completed', google_drive={'state': 'uploaded'})
        self.studio.refresh_buttons()
        self.assertEqual(str(self.studio.continue_button['state']), 'disabled')
        self.studio.current.pop('drive_upload')
        self.studio.result = dict(status='completed')
        self.studio.refresh_buttons()
        self.assertEqual(str(self.studio.continue_button['state']), 'disabled')

    def test_inflight_worker_locks_drive_controls_and_continue(self):
        self.choose()
        self.owned_job()
        self.studio.result = dict(status='completed', google_drive={'state': 'failed'})
        self.studio.process = Mock()
        self.studio.refresh_buttons()
        self.assertEqual(str(self.studio.continue_button['state']), 'disabled')
        self.assertEqual(str(self.studio.drive_check['state']), 'disabled')
        self.assertEqual(str(self.studio.drive_button['state']), 'disabled')
        self.studio.process = None

    def test_advanced_drive_callback_saves_preference_only_and_keeps_job_snapshot(self):
        old = self.owned_job(legacy=True)
        with patch('teodub.google_drive_ui.configure_google_drive') as configure:
            self.studio.choose_drive_api()
        parent, data_dir, initial, save = configure.call_args.args
        self.assertIs(parent, self.studio)
        self.assertEqual(data_dir, self.studio.data_dir)
        self.assertEqual(initial, normalize_drive_config({}))
        save(selected_drive())
        preference = json.loads((self.studio.data_dir/'preferences.json').read_text(encoding='utf-8'))
        self.assertEqual(preference['drive_upload'], selected_drive())
        self.assertEqual(self.studio.current, old)
        self.assertNotIn('drive_upload', old)
        self.dialog_cloud.assert_not_called()

    def test_advanced_drive_dialog_accepts_studio_frame_parent_and_is_passive(self):
        self.dialog_cloud.return_value.accounts.return_value = []
        dialog = self.studio.choose_drive_api()
        dialog.withdraw()
        try:
            self.assertIsInstance(dialog, tk.Toplevel)
            self.assertFalse(dialog.enabled.get())
            self.dialog_cloud.assert_called_once_with(self.studio.data_dir)
            self.assertFalse(dialog.busy)
        finally:
            dialog.close()

    def test_unconfigured_form_opens_setup_without_invalid_enabled_payload(self):
        self.studio.fields['drive_enabled'].set(True)
        with patch('teodub.google_drive_ui.configure_google_drive') as configure:
            self.studio.choose_drive_api()
        self.assertFalse(configure.call_args.args[2]['enabled'])
        self.assertTrue(self.studio.fields['drive_enabled'].get())

    def test_completion_keeps_render_progress_and_shows_separate_cloud_error(self):
        self.choose()
        self.owned_job(result=dict(status='completed', message='Video đã xuất.', outputs={},
            google_drive=dict(state='failed', message='Drive hết dung lượng.')))
        self.studio.complete()
        self.assertEqual(self.studio.progress.get(), 100)
        self.assertIn('Video đã xuất.', self.studio.status.get())
        self.assertIn('Google Drive: Drive hết dung lượng.', self.studio.status.get())
        self.assertEqual(str(self.studio.continue_button['state']), 'normal')

    def test_campaign_capture_freezes_drive_target_and_does_not_modify_saved_campaign(self):
        self.choose()
        self.tab.capture_settings()
        self.assertEqual(self.tab.settings['drive_upload'], selected_drive())
        self.assertEqual(self.tab.settings['drive_data_dir'], str(self.tab.data_dir))
        self.assertIn('Tự lưu Drive', self.tab.summary.get())
        manifest = self.manifest()
        campaign_directory = self.tab.directory
        manifest_digest = hash_file(campaign_directory/'campaign.json')
        job_digest = hash_file(campaign_directory/'jobs/0001/config.json')
        self.choose(selected_drive('b'*32, 'folder_B'))
        self.tab.capture_settings()
        self.assertEqual(self.tab.settings['drive_upload'], selected_drive('b'*32, 'folder_B'))
        self.assertEqual(manifest['settings']['drive_upload'], selected_drive())
        self.assertEqual(hash_file(campaign_directory/'campaign.json'), manifest_digest)
        self.assertEqual(hash_file(campaign_directory/'jobs/0001/config.json'), job_digest)

    def test_campaign_drive_checkbox_shared_for_new_work_only(self):
        self.choose()
        self.tab.capture_settings()
        manifest = self.manifest()
        before = hash_file(self.tab.directory/'campaign.json')
        self.tab.drive_check.invoke()
        self.assertFalse(self.studio.fields['drive_enabled'].get())
        self.assertFalse(self.tab.settings['drive_upload']['enabled'])
        self.assertTrue(manifest['settings']['drive_upload']['enabled'])
        self.assertEqual(hash_file(self.tab.directory/'campaign.json'), before)

    def test_drive_column_is_separate_from_dubbing_and_posting_and_refreshes_passively(self):
        self.choose()
        self.tab.capture_settings()
        manifest = self.manifest()
        columns = tuple(self.tab.tree['columns'])
        self.assertEqual(columns[columns.index('completed_at'):], ('completed_at', 'posting', 'drive', 'message'))
        first = manifest['items'][0]
        job = Path(first['job'])
        atomic_json(job/'drive-upload.json', dict(account_id='a'*32, folder_id='folder_A',
            state='uploading', percent=46, message='Đang tải video lên Drive.'))
        self.tab.display(manifest)
        self.assertEqual(self.tab.tree.set('1', 'drive'), 'Đang tải lên 46%')
        self.assertEqual(self.tab.tree.set('1', 'percent'), '0%')
        self.assertEqual(self.tab.tree.set('1', 'posting'), 'Chưa đăng')
        atomic_json(job/'drive-upload.json', dict(account_id='a'*32, folder_id='folder_A',
            state='failed', percent=0, message='Tải lên bị ngắt; video trên máy được giữ.'))
        self.tab.poll()
        self.assertEqual(self.tab.tree.set('1', 'drive'), 'Tải lên bị lỗi')
        self.assertEqual(self.tab.tree.set('1', 'percent'), '0%')
        self.dialog_cloud.assert_not_called()

    def test_campaign_details_show_cloud_reason_and_preview_clears_old_delivery_state(self):
        self.choose()
        self.tab.capture_settings()
        manifest = self.manifest()
        atomic_json(Path(manifest['items'][0]['job'])/'drive-upload.json',
            dict(account_id='a'*32, folder_id='folder_A', state='needs_auth', percent=0,
                 message='Tài khoản Google cần kết nối lại.'))
        self.tab.display(manifest)
        self.tab.tree.selection_set('1')
        with patch('tkinter.messagebox.showinfo') as dialog:
            self.tab.show_item(detail_only=True)
            text = dialog.call_args.args[1]
        self.assertIn('Google Drive: Cần kết nối lại', text)
        self.assertIn('Tài khoản Google cần kết nối lại.', text)
        self.tab.preview_records([dict(url='https://www.douyin.com/video/333', title='Preview')])
        self.assertEqual(self.tab.tree.set('1', 'drive'), '—')
        self.assertEqual(self.tab.drive_statuses, {})
        self.dialog_cloud.assert_not_called()


if __name__ == '__main__':
    unittest.main()
