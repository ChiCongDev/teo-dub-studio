"""Per-Page opt-in AI planning, selected campaigns and an inspectable journal."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk

from teodub.ai_models import TRANSLATION_MODELS, REASONING_LEVELS
from teodub.campaign import read_json
from teodub.page_ai import PageAIStore, LABELS
from teodub.page_schedule import LOCAL_TIME, PageScheduleStore, parse_time, display_time


# Owner request (19/10/2026): temporarily hide the AI schedule/text/auto controls. The variables, stored values
# and code stay; set to False to show them again.
SOURCE_REFRESH_POLLS = 5  # The source counts are re-read every 5 polls (about 10 seconds).
HIDE_AI_SCHEDULE_CONTROLS = True


class PageAIPanel(ttk.Frame):
    def __init__(self, parent, manager, *, history_parent=None):
        super().__init__(parent, padding=10)
        self.manager=manager
        self.history_parent=history_parent
        self.account,self.page=manager.account_id,manager.page_key
        self.store=PageAIStore(manager.owner.studio.data_dir)
        self.closed=False
        self.timer=None
        self.enabled=tk.BooleanVar(self,False)
        self.auto=tk.BooleanVar(self,False)
        self.model=tk.StringVar(self,TRANSLATION_MODELS['gpt-5.6-terra'])
        self.effort=tk.StringVar(self,REASONING_LEVELS['low'])
        self.days=tk.IntVar(self,7)
        self.videos=tk.IntVar(self,2)
        self.texts=tk.IntVar(self,0 if HIDE_AI_SCHEDULE_CONTROLS else 3)
        self.interval=tk.IntVar(self,7)
        self.slots_on=tk.BooleanVar(self,False)
        self.auto_campaign=tk.BooleanVar(self,False)
        self.loaded_auto=False
        self.slot_start=tk.StringVar(self,'12:00')
        self.slot_gap=tk.StringVar(self,'450')
        tomorrow=(datetime.now(LOCAL_TIME)+timedelta(days=1)).replace(hour=6,minute=0)
        self.due=tk.StringVar(self,tomorrow.strftime('%Y-%m-%d %H:%M'))
        self.status=tk.StringVar(self,'Chưa bật AI. Chọn nguồn và lưu cấu hình để hẹn giờ.')
        self.strategy_status=tk.StringVar(self)
        self.source_status=tk.StringVar(self)
        self.plan_summary=tk.StringVar(self)
        self.slots_summary=tk.StringVar(self)
        self.publish_summary=tk.StringVar(self)
        self.advanced_visible=False
        self.source_rows={}
        self.selected_campaigns=[]
        self.source_picker_window=None
        self.source_picker=None
        self.wrap_labels=[]
        self.rows={}
        self.signature=None
        self.polls=0
        self.build()
        self.load()
        self.update_summaries()
        for variable in (self.days,self.videos,self.texts,self.interval,self.auto):
            variable.trace_add('write',self.update_summaries)
        for variable in (self.slots_on,self.slot_start,self.slot_gap,self.auto_campaign):
            variable.trace_add('write',self.update_summaries)
        self.refresh()
        self.bind('<Destroy>',self.destroyed,add='+')
        self.timer=self.after(2000,self.poll)

    def build(self):
        self.config_tabs=None
        if self.history_parent is None:
            self.config_tabs=ttk.Notebook(self)
            self.config_tabs.pack(fill='both',expand=True)
            self.config_frame=ttk.Frame(self.config_tabs,padding=8)
            self.history_frame=ttk.Frame(self.config_tabs,padding=8)
            self.config_tabs.add(self.config_frame,text='Thiết lập')
            self.config_tabs.add(self.history_frame,text='Lịch sử AI')
        else:
            self.config_frame=ttk.Frame(self)
            self.config_frame.pack(fill='both',expand=True)
            self.history_frame=ttk.Frame(self.history_parent,padding=8)
            self.history_frame.pack(fill='both',expand=True)

        # Keep the action/status footer reachable even at the minimum window size.
        footer=ttk.Frame(self.config_frame)
        footer.pack(side='bottom',fill='x',pady=(8,0))
        actions=ttk.Frame(footer)
        actions.pack(fill='x')
        self.save_button=ttk.Button(actions,text='Lưu thiết lập & hẹn giờ',command=self.save)
        self.save_button.pack(side='left')
        ttk.Button(actions,text='Tạm dừng AI',command=self.pause).pack(side='left',padx=6)
        self.note(footer,textvariable=self.status).pack(anchor='w',fill='x',pady=(5,0))

        scroller=ttk.Frame(self.config_frame)
        scroller.pack(fill='both',expand=True)
        scroller.columnconfigure(0,weight=1)
        scroller.rowconfigure(0,weight=1)
        self.config_canvas=tk.Canvas(scroller,highlightthickness=0,borderwidth=0)
        self.config_canvas.grid(row=0,column=0,sticky='nsew')
        scrollbar=ttk.Scrollbar(scroller,command=self.config_canvas.yview)
        scrollbar.grid(row=0,column=1,sticky='ns')
        self.config_canvas.configure(yscrollcommand=scrollbar.set)
        config=ttk.Frame(self.config_canvas,padding=(0,0,8,0))
        self.config_body=config
        self.config_window=self.config_canvas.create_window((0,0),window=config,anchor='nw')
        config.bind('<Configure>',self.update_scroll_region)
        self.config_canvas.bind('<Configure>',self.resize_config)

        goal_card=ttk.LabelFrame(config,text='Mục tiêu phát triển',padding=10)
        goal_card.pack(fill='x',pady=(0,8))
        self.note(goal_card,text='Bạn muốn phục vụ ai và ưu tiên nội dung gì?').pack(anchor='w',fill='x')
        self.goal=tk.Text(goal_card,height=2,wrap='word',width=40)
        self.goal.pack(fill='x',pady=(5,7))
        self.goal.insert('1.0','Phát triển nội dung hữu ích, caption tự nhiên; phân tích kết quả và điều chỉnh lịch để thử nghiệm.')
        strategy_row=ttk.Frame(goal_card)
        strategy_row.pack(fill='x')
        self.strategy_button=ttk.Button(strategy_row,text='Chat chiến lược…',command=self.open_strategy_settings)
        self.strategy_button.pack(side='right',padx=(8,0))
        self.note(strategy_row,textvariable=self.strategy_status).pack(side='left',fill='x',expand=True)

        source_card=ttk.LabelFrame(config,text='Nguồn video',padding=10)
        source_card.pack(fill='x',pady=(0,8))
        self.note(source_card,text='Chỉ hiển thị nguồn đã chọn. Bấm Thêm nguồn để chọn thêm chiến dịch.').pack(anchor='w',fill='x',pady=(0,5))
        source_table=ttk.Frame(source_card)
        source_table.pack(fill='x')
        source_table.columnconfigure(0,weight=1)
        self.sources=ttk.Treeview(source_table,columns=('name','count','remaining'),show='headings',height=3,selectmode='extended')
        self.sources.heading('name',text='Chiến dịch nguồn video')
        self.sources.heading('count',text='Tệp còn trên máy')
        self.sources.heading('remaining',text='Chưa xếp lịch')
        self.sources.column('name',width=440,minwidth=160)
        self.sources.column('count',width=140,minwidth=100,stretch=False,anchor='center')
        self.sources.column('remaining',width=130,minwidth=100,stretch=False,anchor='center')
        self.sources.grid(row=0,column=0,sticky='ew')
        source_scroll=ttk.Scrollbar(source_table,command=self.sources.yview)
        source_scroll.grid(row=0,column=1,sticky='ns')
        self.sources.configure(yscrollcommand=source_scroll.set)
        self.sources.bind('<<TreeviewSelect>>',self.update_summaries)
        self.load_sources()
        source_actions=ttk.Frame(source_card)
        source_actions.pack(fill='x',pady=(5,0))
        ttk.Button(source_actions,text='Làm mới nguồn',command=self.load_sources).pack(side='right')
        self.source_add_button=ttk.Button(source_actions,text='Thêm nguồn…',command=self.open_source_picker)
        self.source_add_button.pack(side='right',padx=6)
        self.source_remove_button=ttk.Button(source_actions,text='Bỏ nguồn',command=self.remove_sources,state='disabled')
        self.source_remove_button.pack(side='right')
        self.note(source_actions,textvariable=self.source_status).pack(side='left',fill='x',expand=True)

        cadence_card=ttk.LabelFrame(config,text='Nhịp nội dung & giờ gọi AI',padding=10)
        cadence_card.pack(fill='x',pady=(0,8))
        line=ttk.Frame(cadence_card)
        line.pack(fill='x')
        for label,var,low,high in [('Video mỗi ngày',self.videos,0,1000),('Bài chữ mỗi tuần',self.texts,0,7)]:
            if HIDE_AI_SCHEDULE_CONTROLS and var is self.texts:
                continue
            ttk.Label(line,text=label).pack(side='left',padx=(0,4))
            ttk.Spinbox(line,textvariable=var,from_=low,to=high,width=4).pack(side='left',padx=(0,15))
        if not HIDE_AI_SCHEDULE_CONTROLS:
            line=ttk.Frame(cadence_card)
            line.pack(fill='x',pady=(7,3))
            ttk.Label(line,text='Gọi AI tiếp theo (UTC+7)').pack(side='left')
            ttk.Entry(line,textvariable=self.due,width=19).pack(side='left',padx=6)
            ttk.Label(line,text='Lặp sau').pack(side='left')
            ttk.Spinbox(line,textvariable=self.interval,from_=0,to=30,width=4).pack(side='left',padx=4)
            ttk.Label(line,text='ngày · 0 = một lần').pack(side='left')
            self.note(cadence_card,text='Nhập giờ theo dạng YYYY-MM-DD HH:MM.').pack(anchor='w',fill='x')
            self.note(cadence_card,textvariable=self.plan_summary).pack(anchor='w',fill='x',pady=(3,5))
            ttk.Checkbutton(cadence_card,text='Tôi tự đặt giờ đăng, AI chỉ viết caption',variable=self.slots_on).pack(anchor='w')
        line=ttk.Frame(cadence_card)
        line.pack(fill='x',pady=(3,0))
        ttk.Label(line,text='Giờ bài đầu mỗi ngày (HH:MM)').pack(side='left')
        ttk.Entry(line,textvariable=self.slot_start,width=7).pack(side='left',padx=6)
        ttk.Label(line,text='Cách nhau (phút)').pack(side='left')
        ttk.Entry(line,textvariable=self.slot_gap,width=6).pack(side='left',padx=6)
        self.note(cadence_card,textvariable=self.slots_summary).pack(anchor='w',fill='x',pady=(3,5))
        ttk.Checkbutton(cadence_card,text='Tự động từ chiến dịch: video lồng tiếng xong → AI viết caption → tự duyệt → tự đăng',
                        variable=self.auto_campaign).pack(anchor='w')
        self.note(cadence_card,text='Dùng Video mỗi ngày (không giới hạn), Giờ bài đầu và Cách nhau ở trên. Video nào xong thì được '
                  'xếp vào giờ trống kế tiếp, không cần bạn duyệt. Giữ Tèo mở; lỗi AI hoặc giờ sai sẽ tự dừng chế độ này.').pack(anchor='w',fill='x',pady=(0,5))
        if not HIDE_AI_SCHEDULE_CONTROLS:
            ttk.Checkbutton(cadence_card,text='Bật lịch gọi AI',variable=self.enabled).pack(anchor='w')
            self.note(cadence_card,text='Đến giờ hẹn mới dùng hạn mức AI để phân tích và lập kế hoạch.').pack(anchor='w',fill='x',pady=(0,5))
            ttk.Checkbutton(cadence_card,text='Tự bật lịch đăng sau khi Tèo kiểm tra kế hoạch',variable=self.auto).pack(anchor='w')
            self.note(cadence_card,textvariable=self.publish_summary).pack(anchor='w',fill='x')

        self.advanced_button=ttk.Button(config,text='▸ Tùy chọn AI',command=self.toggle_advanced)
        self.advanced_button.pack(anchor='w',pady=(0,5))
        self.advanced_frame=ttk.LabelFrame(config,text='Tùy chọn AI',padding=10)
        line=ttk.Frame(self.advanced_frame)
        line.pack(fill='x',pady=(0,5))
        ttk.Label(line,text='Mô hình').pack(side='left')
        self.model_picker=ttk.Combobox(line,textvariable=self.model,values=list(TRANSLATION_MODELS.values()),state='readonly',width=42)
        self.model_picker.pack(side='left',padx=6)
        self.effort_picker=ttk.Combobox(line,textvariable=self.effort,values=list(REASONING_LEVELS.values()),state='readonly',width=22)
        self.effort_picker.pack(side='left')
        line=ttk.Frame(self.advanced_frame)
        line.pack(fill='x',pady=(0,5))
        ttk.Label(line,text='Số ngày trong mỗi kế hoạch').pack(side='left')
        self.days_picker=ttk.Spinbox(line,textvariable=self.days,from_=1,to=7,width=4)
        self.days_picker.pack(side='left',padx=6)
        ttk.Label(line,text='1–7 ngày; chu kỳ lặp phải dài ít nhất bằng kế hoạch.').pack(side='left')
        self.note(self.advanced_frame,text='Mỗi lượt AI phân tích độc lập, dùng chat liên kết làm tham khảo; không viết vào chat. '
            'Tối đa 1 lượt AI/Page/ngày. Tiếp tục đợt 6 ngày: đặt số ngày và chu kỳ đều là 6, chọn giờ sau đợt hiện tại.').pack(anchor='w',fill='x')
        self.note(self.advanced_frame,text='Giữ Tèo mở, máy có mạng và không ngủ. Dừng AI không hủy lịch đăng đã bật. '
            'Số liệu thiếu được ghi rõ; luồng video/Reels nhiều màn hình có thể dừng để kiểm tra.').pack(anchor='w',fill='x',pady=(5,0))
        for widget in self.descendants(config)+[self.config_canvas]:
            if not isinstance(widget,(tk.Text,ttk.Treeview,ttk.Scrollbar)):
                widget.bind('<MouseWheel>',self.scroll_config,add='+')
        self.build_history()

    def note(self,parent,**kwargs):
        label=ttk.Label(parent,wraplength=780,justify='left',**kwargs)
        label.bind('<Configure>',self.wrap_note)
        self.wrap_labels.append(label)
        return label

    def wrap_note(self,event):
        width=max(120,event.width)
        if int(event.widget.cget('wraplength'))!=width:
            event.widget.configure(wraplength=width)

    @staticmethod
    def descendants(widget):
        children=widget.winfo_children()
        return children+[descendant for child in children for descendant in PageAIPanel.descendants(child)]

    def update_scroll_region(self,_=None):
        self.config_canvas.configure(scrollregion=self.config_canvas.bbox('all'))

    def resize_config(self,event):
        self.config_canvas.itemconfigure(self.config_window,width=event.width)
        self.update_scroll_region()

    def scroll_config(self,event):
        if self.config_body.winfo_reqheight()>self.config_canvas.winfo_height():
            self.config_canvas.yview_scroll(-int(event.delta/120),'units')
            return 'break'

    def toggle_advanced(self):
        self.advanced_visible=not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.pack(fill='x',pady=(0,5))
        else:
            self.advanced_frame.pack_forget()
        self.advanced_button.configure(text=('▾' if self.advanced_visible else '▸')+' Tùy chọn AI')
        self.update_scroll_region()

    def open_strategy_settings(self):
        callback=getattr(self.manager,'open_strategy_settings',None)
        if callable(callback):
            callback()
        else:
            self.status.set('Liên kết chat chiến lược trong phần Kế hoạch của Page này.')

    def refresh_strategy_connection(self):
        connection=self.store.plans.get_connection(self.account,self.page)
        if connection.get('thread_id'):
            self.strategy_status.set('Chat chiến lược: '+(connection.get('title') or connection['thread_id']))
        else:
            self.strategy_status.set('Chưa liên kết chat chiến lược. Cần liên kết trước khi lưu lịch AI.')

    def update_summaries(self,*_):
        selected=self.selected_campaigns
        total=sum(self.source_rows.get(key,{}).get('remaining',0) for key in selected)
        self.source_status.set(f'Đã chọn {len(selected)} nguồn · {total} video chưa xếp lịch cho Page này.' if selected else
            'Chưa chọn nguồn. Bấm Thêm nguồn để chọn chiến dịch.')
        remove_button=getattr(self,'source_remove_button',None)
        if remove_button is not None:
            remove_button.configure(state='normal' if self.sources.selection() else 'disabled')
        try:
            days,interval=self.days.get(),self.interval.get()
            self.plan_summary.set(f'Mỗi lượt lập kế hoạch {days} ngày · '+
                (f'lặp sau {interval} ngày.' if interval else 'chỉ gọi một lần.'))
        except tk.TclError:
            self.plan_summary.set('Mỗi kế hoạch từ 1–7 ngày; chỉnh chi tiết trong Tùy chọn AI.')
        self.slots_summary.set(self.describe_slots())
        self.publish_summary.set('Bật: kế hoạch hợp lệ được bật lịch đăng cho đúng Page này.' if self.auto.get() else
            'Tắt: kế hoạch được lưu để bạn xem và duyệt trước khi bật lịch đăng.')

    def next_run(self):
        """The AI's next run. While the schedule controls are hidden the saved time is kept as is."""
        if not HIDE_AI_SCHEDULE_CONTROLS:
            return parse_time(self.due.get())
        saved=self.store.policy(self.account,self.page)
        if saved and saved['next_at']>time.time()+60:
            return saved['next_at']
        return time.time()+86400

    def slot_values(self):
        """Owner-chosen times as (HH:MM, minutes), or None when the AI picks the times."""
        if not (self.slots_on.get() or self.auto_campaign.get()):
            return None
        try:
            gap=int(self.slot_gap.get().strip())
        except ValueError:
            raise ValueError('Khoảng cách giữa các bài là số phút (chế độ tự động từ 1, chế độ giờ cố định từ 30; tối đa 1440).') from None
        return self.slot_start.get().strip(),gap

    def describe_slots(self):
        try:
            values=self.slot_values()
            if values is None:
                return 'AI tự chọn giờ đăng. Bật ô trên nếu bạn muốn tự đặt giờ.'
            hour,minute=map(int,values[0].split(':'))
            count=max(self.videos.get(),1)
            auto=self.auto_campaign.get()
            times=[(hour*60+minute+index*values[1]) for index in range(count)]
            if not (1 if auto else 30)<=values[1]<=1440 or not 0<=hour<24 or not 0<=minute<60 or (not auto and not all(value<1440 for value in times)):
                raise ValueError
            text=', '.join(f'{value//60%24:02d}:{value%60:02d}' for value in times[:8])+(f' … ({count} bài/ngày)' if count>8 else '')
            if auto:
                return (f'Tự động: mỗi ngày tối đa {count} bài, lúc {text}. Video xong được xếp vào giờ trống kế tiếp; '
                        f'đã qua các giờ này mà hôm nay chưa đủ {count} bài thì đăng bù theo nhịp {values[1]} phút '
                        'tới hết ngày, không đăng trước giờ bài đầu.')
            return f'Mỗi ngày đăng lúc: {text}. AI chỉ viết caption; bài ngoài các giờ này bị từ chối.'
        except (ValueError, tk.TclError):
            return 'Giờ chưa hợp lệ: nhập HH:MM, khoảng cách bất kỳ từ 1 đến 1440 phút (chế độ giờ cố định từ 30 phút và các bài phải nằm trong cùng một ngày).'

    def build_history(self):
        history=self.history_frame
        history.rowconfigure(1,weight=1)
        history.rowconfigure(2,weight=1)
        history.columnconfigure(0,weight=1)
        ttk.Button(history,text='Làm mới lịch sử',command=self.refresh).grid(row=0,column=0,sticky='w')
        frame=ttk.Frame(history)
        frame.grid(row=1,column=0,sticky='nsew',pady=6)
        frame.columnconfigure(0,weight=1)
        frame.rowconfigure(0,weight=1)
        self.history=ttk.Treeview(frame,columns=('time','actor','action'),show='headings',height=8)
        for key,title,width in [('time','Thời gian',180),('actor','Ai thực hiện',110),('action','Thao tác / kết quả',480)]:
            self.history.heading(key,text=title)
            self.history.column(key,width=width)
        self.history.grid(row=0,column=0,sticky='nsew')
        scroll=ttk.Scrollbar(frame,command=self.history.yview)
        scroll.grid(row=0,column=1,sticky='ns')
        self.history.configure(yscrollcommand=scroll.set)
        self.history.bind('<<TreeviewSelect>>',self.show_history)
        self.detail=tk.Text(history,height=8,wrap='word',state='disabled')
        self.detail.grid(row=2,column=0,sticky='nsew')

    def load_sources(self):
        self.source_rows={}
        used={row['source_key'] for row in PageScheduleStore(self.store.data_dir).list(self.account,self.page)}
        for plan in self.store.plans.plans(self.account,self.page):
            used.update(item['video_key'] for item in plan['items'] if item.get('video_key'))
        for row in self.store.campaigns():
            available=remaining=0
            try:
                data=read_json(self.store.data_dir/'campaigns'/row['id']/'campaign.json')
                destination=Path(data['output_dir']).resolve()
                for item in data.get('items',[])[:1000]:
                    if not isinstance(item,dict) or item.get('status')!='completed':
                        continue
                    key=row['id']+':'+str(item.get('number'))
                    try:
                        # Count saved finished files without hashing large media
                        # or repeatedly reading the same campaign manifest.
                        video=Path(item['outputs']['video']).resolve()
                        if (not video.is_relative_to(destination) or video.suffix.lower() not in {'.mp4','.mov'}
                                or not video.is_file() or not video.stat().st_size):
                            continue
                    except (ValueError,OSError,TypeError,KeyError):
                        continue
                    available+=1
                    remaining+=key not in used
            except (ValueError,OSError,TypeError,KeyError):
                pass
            self.source_rows[row['id']]=dict(name=row['name'],available=available,remaining=remaining)
        self.render_sources()

    def set_sources(self, campaigns):
        self.selected_campaigns=list(dict.fromkeys(campaigns))
        self.render_sources()

    def render_sources(self):
        highlighted=set(self.sources.selection())
        self.sources.delete(*self.sources.get_children())
        for key in self.selected_campaigns:
            row=self.source_rows.get(key)
            values=(row['name'],row['available'],row['remaining']) if row else ('Nguồn không còn trong danh sách','—','—')
            self.sources.insert('','end',iid=key,values=values)
        self.sources.selection_set([key for key in self.selected_campaigns if key in highlighted])
        self.update_summaries()

    def remove_sources(self):
        remove=set(self.sources.selection())
        self.set_sources([key for key in self.selected_campaigns if key not in remove])

    def open_source_picker(self):
        if self.source_picker_window is not None:
            self.source_picker_window.lift()
            return
        try:
            self.load_sources()
        except (OSError,ValueError) as exc:
            self.status.set('Chưa lấy được danh sách nguồn: '+str(exc))
            return
        window=self.source_picker_window=tk.Toplevel(self)
        window.title('Thêm nguồn video cho Page')
        window.geometry('820x480')
        window.minsize(700,400)
        window.transient(self.winfo_toplevel())
        window.protocol('WM_DELETE_WINDOW',self.close_source_picker)
        shell=ttk.Frame(window,padding=12)
        shell.pack(fill='both',expand=True)
        ttk.Label(shell,text='Chọn chiến dịch để thêm. Giữ Ctrl / Shift để chọn nhiều nguồn.',wraplength=660).pack(anchor='w',pady=(0,8))
        table=ttk.Frame(shell)
        table.pack(fill='both',expand=True)
        self.source_picker=ttk.Treeview(table,columns=('name','available','remaining','chosen'),show='headings',selectmode='extended')
        for key,title,width in [('name','Chiến dịch nguồn video',350),('available','Tệp trên máy',120),
                                ('remaining','Chưa xếp lịch',120),('chosen','Đã chọn',90)]:
            self.source_picker.heading(key,text=title)
            self.source_picker.column(key,width=width,minwidth=70,stretch=key=='name')
        self.source_picker.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(table,command=self.source_picker.yview)
        scroll.pack(side='right',fill='y')
        self.source_picker.configure(yscrollcommand=scroll.set)
        for key,row in self.source_rows.items():
            self.source_picker.insert('','end',iid=key,values=(row['name'],row['available'],row['remaining'],
                'Có' if key in self.selected_campaigns else ''))
        controls=ttk.Frame(shell)
        controls.pack(fill='x',pady=(10,0))
        self.source_apply_button=ttk.Button(controls,text='Thêm nguồn đã chọn',command=self.apply_picker_sources)
        self.source_apply_button.pack(side='left')
        source_callback=getattr(self.manager,'open_video_sources',None)
        if callable(source_callback):
            ttk.Button(controls,text='Tạo nguồn Douyin mới…',command=self.create_source).pack(side='left',padx=6)
        ttk.Button(controls,text='Đóng',command=self.close_source_picker).pack(side='right')

    def apply_picker_sources(self):
        if self.source_picker is not None:
            added=[key for key in self.source_picker.selection() if key in self.source_rows]
            self.set_sources(self.selected_campaigns+added)
            self.close_source_picker()

    def create_source(self):
        self.close_source_picker()
        self.manager.open_video_sources()

    def close_source_picker(self):
        if self.source_picker_window is not None:
            self.source_picker_window.destroy()
            self.source_picker_window=None
            self.source_picker=None

    def load(self):
        saved=self.store.policy(self.account,self.page)
        if not saved:
            return
        config=saved['config']
        self.enabled.set(bool(saved['enabled']))
        self.auto.set(config['auto_publish'])
        self.model.set(TRANSLATION_MODELS[config['model']])
        self.effort.set(REASONING_LEVELS[config['effort']])
        for var,key in [(self.days,'days'),(self.videos,'videos_per_day'),(self.texts,'texts_per_week'),(self.interval,'interval_days')]:
            var.set(config[key])
        self.auto_campaign.set(bool(config.get('auto_campaign')))
        self.loaded_auto=bool(config.get('auto_campaign'))
        self.slots_on.set('slot_start' in config)
        if 'slot_start' in config:
            self.slot_start.set(config['slot_start'])
            self.slot_gap.set(str(config['slot_gap']))
        self.due.set(display_time(saved['next_at']))
        self.set_sources(config['campaigns'])
        self.goal.delete('1.0','end')
        self.goal.insert('1.0',config['goal'])

    def save(self):
        try:
            model=next(k for k,v in TRANSLATION_MODELS.items() if v==self.model.get())
            effort=next(k for k,v in REASONING_LEVELS.items() if v==self.effort.get())
            config=dict(campaigns=list(self.selected_campaigns),model=model,effort=effort,days=self.days.get(),
                videos_per_day=self.videos.get(),texts_per_week=self.texts.get(),interval_days=self.interval.get(),
                goal=self.goal.get('1.0','end-1c').strip(),auto_publish=self.auto.get())
            slots=self.slot_values()
            if slots is not None:
                config.update(slot_start=slots[0],slot_gap=slots[1])
            if self.auto_campaign.get():
                config.update(auto_campaign=True,auto_publish=True,texts_per_week=0)
            elif self.loaded_auto:
                config.update(auto_publish=False)
            self.store.save(self.account,self.page,config,self.next_run(),self.enabled.get())
            self.status.set('Đã lưu. '+('Đến giờ mới gửi dữ liệu tới AI.' if self.enabled.get() else 'Lịch AI đang tắt.'))
            self.refresh()
        except (ValueError, StopIteration, tk.TclError) as exc:
            self.status.set(str(exc) or 'Chọn lại model và cấu hình.')

    def pause(self):
        self.store.pause(self.account,self.page)
        self.enabled.set(False)
        self.status.set('Đã tạm dừng AI. Lịch đăng bài đã bật cần dừng riêng ở tab Lịch đăng.')
        self.refresh()

    def refresh(self):
        self.refresh_strategy_connection()
        rows=self.store.history(self.account,self.page)
        signature=tuple(row['id'] for row in rows)
        if signature!=self.signature:
            self.signature=signature
            selection=self.history.selection()
            self.rows={str(row['id']):row for row in rows}
            self.history.delete(*self.history.get_children())
            for ident,row in self.rows.items():
                self.history.insert('','end',iid=ident,values=(row['at'],row['actor'],row['action']))
            if selection and selection[0] in self.rows:
                self.history.selection_set(selection)
        saved=self.store.policy(self.account,self.page)
        runs=self.store.runs(self.account,self.page)
        message='Chưa có lượt AI. Chọn nguồn và lưu cấu hình để hẹn giờ.'
        if runs:
            run=runs[0]
            message=LABELS.get(run['state'],run['state'])+' · '+run['message']
        if saved:
            if saved['enabled']:
                message+='\nGọi AI tiếp theo: '+display_time(saved['next_at'])
            else:
                message+='\nLịch gọi AI đang tắt.'
        self.status.set(message)

    def show_history(self,_=None):
        selected=self.history.selection()
        if not selected:
            return
        row=self.rows.get(selected[0])
        if not row:
            return
        text=row['detail']
        try:
            text=json.dumps(json.loads(text),ensure_ascii=False,indent=2)
        except ValueError:
            pass
        self.detail.configure(state='normal')
        self.detail.delete('1.0','end')
        self.detail.insert('1.0',row['at']+' · '+row['actor']+' · '+row['action']+'\n\n'+text)
        self.detail.configure(state='disabled')

    def poll(self):
        self.timer=None
        if not self.closed:
            try:
                self.polls+=1
                if self.polls%SOURCE_REFRESH_POLLS==0:
                    self.load_sources()  # Videos finish while this window stays open.
                self.refresh()
            except (ValueError,OSError) as exc:
                self.status.set(str(exc))
            self.timer=self.after(2000,self.poll)

    def destroyed(self,event):
        if event.widget is self:
            self.closed=True
            self.close_source_picker()
            if self.timer:
                self.after_cancel(self.timer)
