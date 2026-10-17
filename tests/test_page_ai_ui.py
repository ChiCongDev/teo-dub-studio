import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from tests.test_page_ai import PageAITests
from teodub.page_ai_ui import PageAIPanel
from teodub.ai_models import REASONING_LEVELS,TRANSLATION_MODELS


class PageAIUITests(unittest.TestCase):
    def setUp(self):
        PageAITests.setUp(self)
        self.tk=tk.Tk()
        self.tk.withdraw()
        self.addCleanup(self.tk.destroy)
        self.manager=SimpleNamespace(owner=self.owner,account_id=self.account,page_key=self.page,open_strategy_settings=Mock())
        self.panel=PageAIPanel(ttk.Notebook(self.tk),self.manager)

    def test_open_panel_is_passive_and_correct_page_scoped(self):
        self.assertFalse(self.panel.enabled.get())
        self.assertFalse(self.panel.auto.get())
        self.assertIsNone(self.store.policy(self.account,self.page))
        self.invoke.assert_not_called()
        self.assertEqual(self.panel.sources.get_children(),())
        self.assertIn(self.campaign,self.panel.source_rows)

    def test_save_uses_actual_selected_model_and_campaign(self):
        self.panel.set_sources([self.campaign])
        self.panel.model.set(TRANSLATION_MODELS['gpt-5.6-luna'])
        self.panel.enabled.set(True)
        self.panel.save()
        row=self.store.policy(self.account,self.page)
        self.assertTrue(row['enabled'])
        self.assertEqual(row['config']['model'],'gpt-5.6-luna')
        self.assertEqual(row['config']['campaigns'],[self.campaign])
        self.invoke.assert_not_called()

    def test_collapsed_options_preserve_saved_policy_values_and_only_save_on_request(self):
        import time
        config=dict(self.config,days=6,interval_days=6,videos_per_day=1,texts_per_week=2,
                    model='gpt-6-astra',effort='high',goal='Chủ đề đã lưu',auto_publish=True)
        original=self.store.save(self.account,self.page,config,time.time()+86400,True)
        self.panel.destroy()
        self.panel=PageAIPanel(ttk.Notebook(self.tk),self.manager)
        self.assertFalse(self.panel.advanced_visible)
        self.assertEqual(self.panel.advanced_frame.winfo_manager(),'')
        self.assertEqual(self.panel.model.get(),TRANSLATION_MODELS['gpt-6-astra'])
        self.assertEqual(self.panel.effort.get(),REASONING_LEVELS['high'])
        self.assertEqual(self.panel.days.get(),6)
        self.assertEqual(self.panel.interval.get(),6)
        self.assertIn('6 ngày',self.panel.plan_summary.get())
        self.assertEqual(self.store.policy(self.account,self.page),original)
        self.panel.toggle_advanced()
        self.assertEqual(self.panel.advanced_frame.winfo_manager(),'pack')
        self.panel.toggle_advanced()
        self.assertEqual(self.panel.advanced_frame.winfo_manager(),'')
        self.assertEqual(self.store.policy(self.account,self.page),original)
        self.panel.save()
        saved=self.store.policy(self.account,self.page)
        for key,value in config.items():
            self.assertEqual(saved['config'][key],value,key)
        self.assertTrue(saved['enabled'])
        self.assertEqual(saved['config']['target'],original['config']['target'])
        self.assertEqual(saved['config']['thread_id'],self.thread)
        self.invoke.assert_not_called()

    def test_sources_count_existing_outputs_and_page_reservations_without_hashing(self):
        import time
        self.panel.set_sources([self.campaign])
        self.assertEqual(self.panel.sources.item(self.campaign,'values'),('Câu cá','1','1'))
        source=self.store.plans._video_source(self.campaign+':1')
        self.scheduler.store.create(self.account,self.page,source,'Caption','',time.time()+86400)
        self.panel.sources.selection_set(self.campaign)
        with patch('teodub.page_ai_ui.PageAIStore.context',side_effect=AssertionError('No AI context on open')), \
                patch('teodub.page_schedule.hash_file',side_effect=AssertionError('No large file hashing for counts')):
            self.panel.load_sources()
        self.assertEqual(self.panel.sources.selection(),(self.campaign,))
        self.assertEqual(self.panel.sources.item(self.campaign,'values'),('Câu cá','1','0'))
        self.assertIn('0 video chưa xếp lịch',self.panel.source_status.get())
        self.video.unlink()
        self.panel.load_sources()
        self.assertEqual(self.panel.sources.item(self.campaign,'values'),('Câu cá','0','0'))
        self.invoke.assert_not_called()

    def test_strategy_prerequisite_is_page_scoped_and_opens_manager_navigation(self):
        self.assertIn('Phát triển page câu cá',self.panel.strategy_status.get())
        self.panel.strategy_button.invoke()
        self.manager.open_strategy_settings.assert_called_once_with()
        self.store.plans.disconnect(self.account,self.page)
        self.panel.refresh()
        self.assertIn('Chưa liên kết chat chiến lược',self.panel.strategy_status.get())
        self.assertIn('Cần liên kết trước',self.panel.strategy_status.get())
        self.assertIsNone(self.store.policy(self.account,self.page))
        self.invoke.assert_not_called()

    def test_external_history_parent_keeps_one_passive_controller_and_full_journal(self):
        self.panel.destroy()
        history_parent=ttk.Frame(self.tk)
        self.panel=PageAIPanel(ttk.Frame(self.tk),self.manager,history_parent=history_parent)
        self.assertIsNone(self.panel.config_tabs)
        self.assertIs(self.panel.config_frame.master,self.panel)
        self.assertIs(self.panel.history_frame.master,history_parent)
        self.assertIs(self.panel.history.master.master,self.panel.history_frame)
        self.store.journal(self.account,self.page,'AI → Tèo','Analysis','Exact external journal')
        self.panel.refresh()
        self.panel.history.selection_set(self.panel.history.get_children()[0])
        self.panel.show_history()
        self.assertIn('Exact external journal',self.panel.detail.get('1.0','end'))
        self.assertIsNone(self.store.policy(self.account,self.page))
        self.invoke.assert_not_called()

    def test_pause_keeps_existing_schedule_separate(self):
        self.panel.set_sources([self.campaign])
        self.panel.enabled.set(True)
        self.panel.save()
        self.panel.pause()
        self.assertFalse(self.store.policy(self.account,self.page)['enabled'])

    def test_history_does_not_grow_status_on_poll_and_displays_exact_data(self):
        self.store.journal(self.account,self.page,'AI → Tèo','Analysis','Exact synthetic content')
        self.panel.refresh()
        first=self.panel.status.get()
        self.panel.refresh()
        self.assertEqual(self.panel.status.get(),first)
        first_row=self.panel.history.get_children()[0]
        self.panel.history.selection_set(first_row)
        self.panel.show_history()
        self.assertIn('Exact synthetic content',self.panel.detail.get('1.0','end'))

    def second_campaign(self):
        from teodub.common import atomic_json
        key='b'*32
        atomic_json(self.root/'campaigns'/key/'campaign.json',dict(id=key,name='Nguồn khác',output_dir=str(self.video.parent),
            items=[dict(number=1,status='completed',outputs=dict(video=str(self.video)))]))
        return key

    def test_only_saved_sources_show_and_picker_cancel_does_not_change_policy(self):
        import time
        other=self.second_campaign()
        original=self.store.save(self.account,self.page,self.config,time.time()+86400,True)
        self.panel.destroy()
        self.panel=PageAIPanel(ttk.Notebook(self.tk),self.manager)
        self.assertEqual(self.panel.sources.get_children(),(self.campaign,))
        self.panel.source_add_button.invoke()
        self.assertEqual(set(self.panel.source_picker.get_children()),{self.campaign,other})
        self.panel.source_picker.selection_set(other)
        self.panel.close_source_picker()
        self.assertEqual(self.panel.sources.get_children(),(self.campaign,))
        self.assertEqual(self.store.policy(self.account,self.page),original)
        self.invoke.assert_not_called()

    def test_adding_sources_is_unique_and_highlight_does_not_drop_other_sources_on_save(self):
        other=self.second_campaign()
        self.panel.set_sources([self.campaign])
        self.panel.source_add_button.invoke()
        self.panel.source_picker.selection_set([self.campaign,other])
        self.panel.source_apply_button.invoke()
        self.assertIsNone(self.panel.source_picker_window)
        self.assertEqual(set(self.panel.sources.get_children()),{self.campaign,other})
        self.assertIsNone(self.store.policy(self.account,self.page))
        self.panel.sources.selection_set(other)
        self.panel.update_summaries()
        self.assertIn('Đã chọn 2 nguồn',self.panel.source_status.get())
        self.panel.load_sources()
        self.panel.save()
        saved=self.store.policy(self.account,self.page)
        self.assertEqual(set(saved['config']['campaigns']),{self.campaign,other})
        self.assertFalse(saved['enabled'])
        self.assertEqual(saved['config']['model'],'gpt-5.6-terra')
        self.panel.load()
        self.assertEqual(set(self.panel.sources.get_children()),{self.campaign,other})
        self.invoke.assert_not_called()

    def test_remove_highlighted_source_keeps_other_source_and_is_unsaved_until_save(self):
        other=self.second_campaign()
        self.panel.load_sources()
        self.panel.set_sources([self.campaign,other])
        self.panel.save()
        original=self.store.policy(self.account,self.page)
        self.panel.sources.selection_set(other)
        self.panel.update_summaries()
        self.panel.source_remove_button.invoke()
        self.assertEqual(self.panel.sources.get_children(),(self.campaign,))
        self.panel.load_sources()
        self.assertEqual(self.panel.sources.get_children(),(self.campaign,))
        self.assertEqual(self.store.policy(self.account,self.page),original)
        self.invoke.assert_not_called()

    def test_missing_selected_source_is_visible_until_explicitly_removed(self):
        from unittest.mock import patch
        self.panel.set_sources([self.campaign])
        with patch.object(self.panel.store,'campaigns',return_value=[]):
            self.panel.load_sources()
        self.assertEqual(self.panel.sources.get_children(),(self.campaign,))
        self.assertIn('không còn',self.panel.sources.item(self.campaign,'values')[0])
        self.assertEqual(self.panel.selected_campaigns,[self.campaign])
        self.invoke.assert_not_called()


if __name__=='__main__':
    unittest.main()
