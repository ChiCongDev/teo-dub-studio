"""Page đăng (owner 10/11/2026): a window on a link's tab listing the Pages that use this link's videos; a double click opens that
Page's own "Quản lý Page" window. It only reads (campaign_pages) when it opens and when "Làm mới" is pressed, never on a timer; it
changes no schedule, source or setting."""
import tkinter as tk
from tkinter import ttk

from teodub.campaign_pages import pages_of_campaign

EMPTY = ('Chưa có Page nào dùng video của link này. Muốn thêm: mở Quản lý Page của Page đó, tab Thiết lập, bấm Thêm nguồn… rồi chọn '
         'link này.')
NOTE = ('Chờ đăng: lịch đã lưu hoặc đang chờ giờ. Đã gửi: đã gửi tới Facebook hoặc đã xác nhận đăng. Cần xem: cần kiểm tra hoặc '
        'đã tạm dừng. Các số chỉ đếm bài làm từ video của link này.')


class CampaignPagesDialog(tk.Toplevel):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab, self.studio, self.directory = tab, tab.studio, tab.directory
        self.rows = {}
        self.closed = False
        name = str((tab.manifest or {}).get('name') or self.directory.name[:8])
        self.title('Page đăng')
        self.geometry('900x380')
        self.minsize(640, 260)
        self.transient(tab.winfo_toplevel())
        shell = ttk.Frame(self, padding=(16, 12))
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text=f'Page đăng video của "{name}"', font=('Segoe UI', 14, 'bold')).pack(anchor='w')
        ttk.Label(shell, wraplength=860, foreground='#444444', text=NOTE).pack(anchor='w', pady=(4, 8))
        table = ttk.Frame(shell)
        table.pack(fill='both', expand=True)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('page', 'account', 'via', 'waiting', 'sent', 'attention'), show='headings',
                                 selectmode='browse', height=8)
        for key, label, width, anchor in (('page', 'Page', 200, 'w'), ('account', 'Tài khoản', 150, 'w'), ('via', 'Liên quan', 170, 'w'),
                                          ('waiting', 'Chờ đăng', 80, 'center'), ('sent', 'Đã gửi', 80, 'center'),
                                          ('attention', 'Cần xem', 80, 'center')):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=60, anchor=anchor, stretch=key in ('page', 'via'))
        self.tree.grid(row=0, column=0, sticky='nsew')
        bar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        bar.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.update_buttons())
        self.tree.bind('<Double-1>', lambda _event: self.open_page())
        self.status = tk.StringVar(self)
        ttk.Label(shell, textvariable=self.status, wraplength=860, foreground='#164b78').pack(anchor='w', pady=(8, 4))
        actions = ttk.Frame(shell)
        actions.pack(fill='x')
        self.open_button = ttk.Button(actions, text='Mở Quản lý Page', command=self.open_page, state='disabled')
        self.open_button.pack(side='left')
        ttk.Button(actions, text='Làm mới', command=self.load).pack(side='left', padx=8)
        ttk.Button(actions, text='Đóng', command=self.close).pack(side='right')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.load()

    def load(self):
        try:
            rows = pages_of_campaign(self.studio.data_dir, (self.tab.manifest or {}).get('id') or self.directory.name)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set('Chưa đọc được danh sách Page: '+str(exc))
            return
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        for row in rows:
            ident = f'{row["account_id"]}|{row["page_key"]}'
            self.rows[ident] = row
            self.tree.insert('', 'end', iid=ident, values=(row['page_name'], row['account_name'], row['via'], row['waiting'],
                                                          row['sent'], row['attention']))
        if selected and selected[0] in self.rows:
            self.tree.selection_set(selected[0])
        self.status.set(f'{len(rows)} Page dùng video của link này. Nhấp đôi một Page để mở Quản lý Page của nó.' if rows else EMPTY)
        self.update_buttons()

    def chosen(self):
        selection = self.tree.selection()
        return self.rows.get(selection[0]) if selection else None

    def update_buttons(self):
        row = self.chosen()
        self.open_button.configure(state='normal' if row is not None and row['in_teo'] else 'disabled')

    def open_page(self):
        row = self.chosen()
        if row is None:
            self.status.set('Chọn một Page trước.')
            return
        if not row['in_teo']:
            self.status.set('Page này đã bị xóa khỏi Tèo; không mở được cửa sổ Quản lý Page.')
            return
        tab = getattr(self.studio, 'facebook_tab', None)
        if tab is None:
            self.status.set('Chưa có tab Quản lý Page.')
            return
        try:
            window = tab.manage_page_of(row['account_id'], row['page_key'])
        except (OSError, ValueError) as exc:
            self.status.set('Chưa mở được Quản lý Page: '+str(exc))
            return
        self.status.set(f'Đã mở Quản lý Page — {row["page_name"]}.' if window is not None else
                        'Chưa mở được Quản lý Page; xem dòng trạng thái ở tab Quản lý Page.')

    def close(self):
        self.closed = True
        self.destroy()
