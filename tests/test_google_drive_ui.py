"""Hidden Tk Drive setup tests; never contact Google, open a browser or upload."""
from copy import deepcopy
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from teodub.google_drive import DriveError, normalize_drive_config
from teodub.google_drive_ui import GoogleDriveDialog, configure_google_drive, drive_selection_summary


ACCOUNT_A = 'a' * 32
ACCOUNT_B = 'b' * 32
FOLDER_A = 'folder_A'
FOLDER_B = 'folder_B'


class ImmediateThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


class DriveDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.client = Mock()
        self.rows = [dict(id=ACCOUNT_A, name='A', email='a@example.invalid', connected=True),
                     dict(id=ACCOUNT_B, name='B', email='b@example.invalid', connected=True)]
        self.client.accounts.side_effect = lambda: deepcopy(self.rows)
        self.client.folders.return_value = [dict(id=FOLDER_A, name='Same name'), dict(id=FOLDER_B, name='Same name')]
        self.save = Mock()
        self.dialogs = []
        self.enterContext(patch('teodub.google_drive_ui.threading.Thread', ImmediateThread))
        self.addCleanup(self.cleanup_dialogs)

    def cleanup_dialogs(self):
        for dialog in self.dialogs:
            dialog.close()

    def dialog(self, initial=None):
        dialog = configure_google_drive(self.root, self.directory, initial or {}, self.save, client=self.client)
        dialog.withdraw()
        self.dialogs.append(dialog)
        return dialog

    def config(self, **kwargs):
        return normalize_drive_config(dict(version=1, enabled=True, account_id=ACCOUNT_A,
                                           folder_id=FOLDER_A, folder_name='Saved folder', **kwargs))

    def select_account(self, dialog, index=0):
        dialog.account_picker.current(index)
        dialog.select_account()

    def finish(self, dialog):
        dialog.poll()

    def test_open_is_passive_does_not_connect_list_folders_or_save(self):
        dialog = self.dialog()
        self.assertFalse(dialog.enabled.get())
        self.assertEqual(dialog.account_id, '')
        self.client.accounts.assert_called_once()
        self.client.connect.assert_not_called()
        self.client.folders.assert_not_called()
        self.client.create_folder.assert_not_called()
        self.client.disconnect.assert_not_called()
        self.save.assert_not_called()

    def test_existing_choice_is_restored_without_network(self):
        initial = self.config()
        dialog = self.dialog(initial)
        initial['folder_name'] = 'Caller mutation'
        self.assertTrue(dialog.enabled.get())
        self.assertEqual(dialog.account_id, ACCOUNT_A)
        self.assertEqual(dialog.folder_id, FOLDER_A)
        self.assertEqual(dialog.folder.get(), 'Saved folder')
        self.client.folders.assert_not_called()
        dialog.save()
        self.assertTrue(dialog.closed)
        self.assertEqual(self.save.call_args.args[0], self.config())

    def test_missing_saved_account_needs_reconnect_and_does_not_fake_success(self):
        self.rows = []
        dialog = self.dialog(self.config())
        self.assertEqual(dialog.account_id, '')
        self.assertEqual(dialog.folder_id, '')
        dialog.save()
        self.assertFalse(dialog.closed)
        self.save.assert_not_called()
        self.assertIn('Kết nối Google', dialog.status.get())

    def test_toggle_off_saves_without_account_or_folder(self):
        dialog = self.dialog()
        dialog.save()
        self.assertEqual(self.save.call_args.args[0], normalize_drive_config({}))
        self.assertTrue(dialog.closed)
        self.client.connect.assert_not_called()

    def test_toggle_on_requires_account_and_folder(self):
        dialog = self.dialog()
        dialog.enabled.set(True)
        dialog.save()
        self.select_account(dialog)
        dialog.save()
        self.save.assert_not_called()
        self.assertFalse(dialog.closed)

    def test_changed_account_clears_folder_does_not_select_another_target_implicitly(self):
        dialog = self.dialog(self.config())
        self.select_account(dialog, 1)
        self.assertEqual(dialog.account_id, ACCOUNT_B)
        self.assertEqual(dialog.folder_id, '')
        self.assertEqual(dialog.folders, [])
        self.client.folders.assert_not_called()

    def test_load_folders_is_scoped_and_does_not_default_to_first_folder(self):
        dialog = self.dialog()
        self.select_account(dialog, 1)
        dialog.load_folders()
        self.assertTrue(dialog.busy)
        self.assertEqual(str(dialog.account_picker['state']), 'disabled')
        self.client.folders.assert_called_once_with(ACCOUNT_B)
        self.finish(dialog)
        self.assertFalse(dialog.busy)
        self.assertEqual(dialog.folder_id, '')
        self.assertEqual(str(dialog.account_picker['state']), 'readonly')
        dialog.folder_picker.current(1)
        dialog.select_folder()
        dialog.enabled.set(True)
        dialog.save()
        self.assertEqual(self.save.call_args.args[0]['account_id'], ACCOUNT_B)
        self.assertEqual(self.save.call_args.args[0]['folder_id'], FOLDER_B)
        self.assertEqual(self.save.call_args.args[0]['folder_name'], 'Same name')

    def test_folder_refresh_restores_id_despite_duplicate_names(self):
        dialog = self.dialog(self.config())
        dialog.load_folders()
        self.finish(dialog)
        self.assertEqual(dialog.folder_id, FOLDER_A)
        self.assertEqual(dialog.folder_picker.current(), 0)
        self.client.folders.return_value.reverse()
        dialog.load_folders()
        self.finish(dialog)
        self.assertEqual(dialog.folder_id, FOLDER_A)
        self.assertEqual(dialog.folder_picker.current(), 1)

    def test_folder_list_without_saved_target_requires_new_selection(self):
        dialog = self.dialog(self.config())
        self.client.folders.return_value = []
        dialog.load_folders()
        self.finish(dialog)
        self.assertEqual(dialog.folder_id, '')
        self.assertIn('Tạo thư mục mới', dialog.status.get())
        dialog.save()
        self.save.assert_not_called()

    def test_create_folder_requires_explicit_name_and_keeps_selected_account(self):
        dialog = self.dialog()
        self.select_account(dialog, 1)
        self.client.create_folder.return_value = dict(id=FOLDER_B, name='Output B')
        with patch('teodub.google_drive_ui.simpledialog.askstring', return_value='  Output B  '):
            dialog.create_folder()
        self.client.create_folder.assert_called_once_with(ACCOUNT_B, 'Output B')
        self.finish(dialog)
        self.assertEqual(dialog.folder_id, FOLDER_B)
        self.assertEqual(dialog.account_id, ACCOUNT_B)
        dialog.reload_accounts()
        self.assertEqual(dialog.folder_id, FOLDER_B)
        self.save.assert_not_called()

    def test_cancelled_or_empty_folder_dialog_has_no_external_mutation(self):
        dialog = self.dialog()
        self.select_account(dialog)
        for name in (None, '', '  '):
            with self.subTest(name=name), patch('teodub.google_drive_ui.simpledialog.askstring', return_value=name):
                dialog.create_folder()
        self.client.create_folder.assert_not_called()

    def test_connect_requires_local_json_and_duplicate_click_is_ignored(self):
        dialog = self.dialog()
        dialog.connect()
        dialog.client_path.set(str(self.directory/'missing.json'))
        dialog.connect()
        self.client.connect.assert_not_called()
        path = self.directory/'client.json'
        path.write_text('{"installed": {"client_id": "synthetic"}}', encoding='utf-8')
        self.client.connect.return_value = deepcopy(self.rows[0])
        dialog.client_path.set(str(path))
        dialog.connect()
        dialog.connect()
        self.client.connect.assert_called_once()
        args, kwargs = self.client.connect.call_args
        self.assertEqual(args, (str(path),))
        self.assertFalse(kwargs['cancel'].is_set())
        self.assertTrue(callable(kwargs['progress']))
        self.assertTrue(dialog.busy)
        self.finish(dialog)
        self.assertEqual(dialog.account_id, ACCOUNT_A)
        self.assertFalse(dialog.busy)
        self.assertIn('Đã kết nối Google', dialog.status.get())
        self.save.assert_not_called()

    def test_file_picker_cancel_preserves_previous_path(self):
        dialog = self.dialog()
        dialog.client_path.set('Existing choice')
        with patch('teodub.google_drive_ui.filedialog.askopenfilename', return_value=''):
            dialog.choose_client()
        self.assertEqual(dialog.client_path.get(), 'Existing choice')
        self.client.connect.assert_not_called()

    def test_worker_error_is_visible_and_releases_controls_without_exposing_unknown_exception(self):
        dialog = self.dialog()
        self.select_account(dialog)
        self.client.folders.side_effect = RuntimeError('secret_access_token=not-for-ui')
        dialog.load_folders()
        self.finish(dialog)
        self.assertFalse(dialog.busy)
        self.assertIn('Không hoàn tất', dialog.status.get())
        self.assertNotIn('secret_access_token', dialog.status.get())
        self.assertEqual(str(dialog.save_button['state']), 'normal')
        self.save.assert_not_called()

    def test_safe_backend_error_is_shown_without_retry(self):
        dialog = self.dialog()
        self.select_account(dialog)
        self.client.folders.side_effect = DriveError('Tài khoản cần kết nối lại.')
        dialog.load_folders()
        self.finish(dialog)
        self.assertEqual(dialog.status.get(), 'Tài khoản cần kết nối lại.')
        self.client.folders.assert_called_once()

    def test_progress_replaces_one_line_and_does_not_save(self):
        dialog = self.dialog()
        self.select_account(dialog)
        def folders(_account):
            dialog.messages.put((dialog.task_id, 'progress', {'message': 'Đang nhận thư mục…'}))
            return []
        self.client.folders.side_effect = folders
        dialog.load_folders()
        self.finish(dialog)
        self.assertIn('Chưa có thư mục', dialog.status.get())
        self.save.assert_not_called()

    def test_thread_start_failure_keeps_dialog_responsive(self):
        dialog = self.dialog()
        self.select_account(dialog)
        with patch('teodub.google_drive_ui.threading.Thread') as thread:
            thread.return_value.start.side_effect = RuntimeError('synthetic')
            dialog.load_folders()
        self.assertFalse(dialog.busy)
        self.assertIn('Chưa bắt đầu', dialog.status.get())
        self.client.folders.assert_not_called()

    def test_disconnect_cancel_has_no_effect_and_confirm_is_account_scoped(self):
        dialog = self.dialog(self.config())
        with patch('teodub.google_drive_ui.messagebox.askyesno', return_value=False):
            dialog.disconnect()
        self.client.disconnect.assert_not_called()
        def disconnected(ident):
            self.rows = [row for row in self.rows if row['id'] != ident]
        self.client.disconnect.side_effect = disconnected
        with patch('teodub.google_drive_ui.messagebox.askyesno', return_value=True):
            dialog.disconnect()
        self.client.disconnect.assert_called_once_with(ACCOUNT_A)
        self.finish(dialog)
        self.assertFalse(dialog.enabled.get())
        self.assertEqual(dialog.account_id, '')
        self.assertEqual(dialog.folder_id, '')
        self.save.assert_not_called()

    def test_save_rechecks_connection_and_keeps_fields_on_failure(self):
        dialog = self.dialog(self.config())
        self.rows = []
        dialog.save()
        self.save.assert_not_called()
        self.assertFalse(dialog.closed)
        self.assertIn('kết nối lại', dialog.status.get().lower())
        self.assertEqual(dialog.folder_id, FOLDER_A)

    def test_parent_save_failure_does_not_discard_choice(self):
        dialog = self.dialog(self.config())
        self.save.side_effect = OSError('synthetic')
        dialog.save()
        self.assertFalse(dialog.closed)
        self.assertEqual(dialog.folder_id, FOLDER_A)
        self.assertIn('Chưa lưu được', dialog.status.get())

    def test_close_cancels_current_login_and_drops_late_results(self):
        dialog = self.dialog()
        success = Mock()
        dialog.run_task('Synthetic task', lambda _cancel, _report: {'id': ACCOUNT_A}, success)
        cancel = dialog.cancel
        dialog.close()
        self.assertTrue(cancel.is_set())
        self.assertTrue(dialog.closed)
        self.assertIsNone(dialog.timer)
        dialog.poll()
        success.assert_not_called()
        self.save.assert_not_called()

    def test_stale_task_result_never_changes_selected_account(self):
        dialog = self.dialog(self.config())
        dialog.task_id = 5
        dialog.messages.put((4, 'done', (dialog.connected, deepcopy(self.rows[1]))))
        dialog.messages.put((4, 'error', 'Stale error'))
        self.finish(dialog)
        self.assertEqual(dialog.account_id, ACCOUNT_A)
        self.assertNotEqual(dialog.status.get(), 'Stale error')

    def test_parent_window_destroy_also_cancels_login(self):
        parent = tk.Toplevel(self.root)
        dialog = GoogleDriveDialog(parent, self.directory, {}, self.save, client=self.client)
        dialog.withdraw()
        self.dialogs.append(dialog)
        success = Mock()
        dialog.run_task('Synthetic task', lambda _cancel, _report: {'id': ACCOUNT_A}, success)
        cancel = dialog.cancel
        parent.destroy()
        self.assertTrue(dialog.closed)
        self.assertTrue(cancel.is_set())
        self.assertIsNone(dialog.timer)
        success.assert_not_called()

    def test_public_summary_describes_preference_not_upload_success(self):
        self.assertEqual(drive_selection_summary({}), 'Google Drive: Tắt')
        self.assertEqual(drive_selection_summary(self.config()), 'Google Drive: Saved folder')
        self.assertNotIn('thành công', drive_selection_summary(self.config()))


if __name__ == '__main__':
    unittest.main()
