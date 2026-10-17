"""Manual subtitle rectangle and a short, cancellable GPU preview."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, ttk
import uuid

from teodub.subtitle_clean import clean_video, validate_clean


class SubtitleCleanDialog(tk.Toplevel):
    def __init__(self, studio):
        super().__init__(studio)
        self.studio = studio
        self.title('Xóa chữ gốc bằng GPU — thử nghiệm')
        self.geometry('780x880')
        self.transient(studio.winfo_toplevel())
        self.grab_set()
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.running = False
        self.preview_path = None
        self.region = studio.clean_options.get('region')
        self.enabled = tk.BooleanVar(self, value=studio.clean_options.get('enabled', False))
        self.source = tk.StringVar(self)
        self.second = tk.StringVar(self, value='30')
        self.message = tk.StringVar(self, value='Chọn video trên máy, tìm khung hình có chữ rồi kéo chuột để khoanh vùng.')
        text = studio.input.get('1.0', 'end').strip()
        if Path(text).is_file():
            self.source.set(text)
        elif studio.current and text == studio.current.get('input'):
            try:
                state = json.loads((Path(studio.current['directory'])/'state.json').read_text(encoding='utf-8'))
                self.source.set(state['source']['path'])
            except (OSError, ValueError, KeyError):
                pass
        box = ttk.Frame(self, padding=12)
        box.pack(fill='both', expand=True)
        ttk.Label(box, text='Lưu vùng xóa riêng cho video đã tải. Sau khi lưu, tick áp dụng ở màn hình Video đơn.').pack(anchor='w')
        ttk.Label(box, wraplength=740, text='STTN chạy trên GPU NVIDIA, miễn phí cục bộ. Chỉ xử lý vùng bạn khoanh ở mọi khung hình; '
                  'chưa tự nhận diện tiếng Trung. Tránh khoanh mặt người, logo hoặc vùng quá lớn. '
                  'Nền chuyển động có thể bị nhòe; hãy xem thử trước. Sub Việt được chèn sau.').pack(anchor='w', pady=6)
        row = ttk.Frame(box)
        row.pack(fill='x')
        ttk.Entry(row, textvariable=self.source, state='readonly').pack(side='left', fill='x', expand=True)
        self.choose_button = ttk.Button(row, text='Chọn video…', command=self.choose)
        # The source is the prepared video from the main form, not a second independent selection.
        row = ttk.Frame(box)
        row.pack(fill='x', pady=5)
        ttk.Label(row, text='Giây xem mẫu:').pack(side='left')
        ttk.Entry(row, textvariable=self.second, width=8).pack(side='left', padx=6)
        self.frame_button = ttk.Button(row, text='Xem hình gốc', command=self.show_original)
        self.frame_button.pack(side='left')
        self.preview_button = ttk.Button(row, text='Thử GPU 3 giây', command=self.preview)
        self.preview_button.pack(side='left', padx=6)
        self.play_button = ttk.Button(row, text='Mở clip thử', command=self.play, state='disabled')
        self.play_button.pack(side='left')
        self.canvas = tk.Canvas(box, width=720, height=510, background='#202020', highlightthickness=0)
        self.canvas.pack(pady=5)
        self.canvas.bind('<ButtonPress-1>', self.begin)
        self.canvas.bind('<B1-Motion>', self.drag)
        ttk.Label(box, textvariable=self.message, wraplength=735).pack(fill='x', pady=4)
        ttk.Label(box, wraplength=735, text='Cấu hình chỉ dành cho video đang chọn. Lưu vùng không tự bật xử lý: '
                  'hãy tick ô Xóa Sub gốc trên màn hình chính, rồi bấm Lồng tiếng và xuất video. '
                  'Không tự dùng vùng này cho video hoặc chiến dịch khác.').pack(fill='x')
        row = ttk.Frame(box)
        row.pack(fill='x', pady=8)
        self.save_button = ttk.Button(row, text='Lưu vùng cho video này', command=self.save)
        self.save_button.pack(side='left')
        self.apply_button = ttk.Button(row, text='Lưu và bật xóa Sub', command=lambda: self.save(enabled=True))
        self.apply_button.pack(side='left', padx=6)
        ttk.Button(row, text='Đóng / Dừng thử', command=self.close).pack(side='right')
        self.controls = [self.frame_button, self.preview_button, self.save_button, self.apply_button]
        self.image = None
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.timer = self.after(150, self.poll)
        if self.source.get():
            self.show_original()

    def choose(self):
        path = filedialog.askopenfilename(parent=self, title='Video gốc trên máy')
        if path:
            self.source.set(path)
            self.preview_path = None
            self.play_button.configure(state='disabled')
            self.show_original()

    def selected_source(self):
        path = Path(self.source.get()).resolve(strict=True)
        if not path.is_file():
            raise ValueError('Chọn video đã tải trên máy trước.')
        second = float(self.second.get().replace(',', '.'))
        if not 0 <= second < 24*3600:
            raise ValueError('Mốc xem phải là số giây từ 0 đến 86400.')
        return path, second

    def display(self, path, second=0):
        import av
        from PIL import Image, ImageTk
        with av.open(str(path)) as video:
            if second:
                video.seek(round(second*av.time_base))
            frame = next(video.decode(video=0))
            if second:
                for candidate in video.decode(video=0):
                    frame = candidate
                    if float(frame.time or 0) >= second:
                        break
            picture = frame.to_image()
        picture.thumbnail((720, 510), Image.Resampling.LANCZOS)
        self.image = ImageTk.PhotoImage(picture, master=self)
        self.w, self.h = picture.size
        self.ox, self.oy = (720-self.w)//2, (510-self.h)//2
        self.canvas.delete('all')
        self.canvas.create_image(self.ox, self.oy, image=self.image, anchor='nw')
        self.draw()

    def show_original(self):
        try:
            self.display(*self.selected_source())
            self.message.set('Hình gốc. Kéo chuột khoanh sát chữ, gồm cả viền chữ; rồi bấm Thử GPU 3 giây.')
        except Exception as exc:
            self.message.set('Chưa xem được video: '+str(exc))

    def point(self, event):
        return (max(0, min(1, (event.x-self.ox)/self.w)), max(0, min(1, (event.y-self.oy)/self.h)))

    def begin(self, event):
        if self.image and not self.running:
            self.anchor = self.point(event)

    def drag(self, event):
        if self.image and not self.running and hasattr(self, 'anchor'):
            x, y = self.point(event)
            ax, ay = self.anchor
            self.region = [min(ax, x), min(ay, y), max(ax, x), max(ay, y)]
            self.draw()

    def draw(self):
        self.canvas.delete('region')
        if self.region and self.image:
            x1, y1, x2, y2 = self.region
            self.canvas.create_rectangle(self.ox+x1*self.w, self.oy+y1*self.h,
                self.ox+x2*self.w, self.oy+y2*self.h, outline='#00ffff', width=2, tags='region')

    def settings(self, force=False):
        return validate_clean({'subtitle_clean': dict(enabled=force or self.enabled.get(), region=self.region)})

    def preview(self):
        if self.running:
            return
        try:
            source, second = self.selected_source()
            options = self.settings(force=True)
        except Exception as exc:
            self.message.set(str(exc))
            return
        self.running = True
        self.preview_path = None
        self.cancel.clear()
        for control in self.controls:
            control.configure(state='disabled')
        self.play_button.configure(state='disabled')
        self.message.set('Đang thử GPU. Lần đầu cần nạp mô hình; có thể bấm Đóng / Dừng thử.')

        def work():
            try:
                import imageio_ffmpeg
                from teodub.pipeline import _job_lock
                from teodub.common import check_cancel
                lock = self.studio.data_dir/'execution'
                lock.mkdir(parents=True, exist_ok=True)
                with _job_lock(lock):
                    directory = self.studio.data_dir/'inpaint-previews'/uuid.uuid4().hex
                    directory.mkdir(parents=True)
                    sample = directory/'original.mkv'
                    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                        '-ss', str(second), '-i', str(source), '-t', '3', '-an', '-sn', '-c:v', 'ffv1', str(sample)]
                    subprocess.run(command, check=True, capture_output=True, timeout=60,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    check_cancel(self.cancel)
                    result = clean_video(sample, {'subtitle_clean': options}, directory/'clean', cancel=self.cancel,
                        progress=lambda text, percent=None: self.events.put(('message', text)))
                    clip = directory/'preview.mp4'
                    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                        '-i', result['path'], '-an', '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', str(clip)],
                        check=True, capture_output=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    check_cancel(self.cancel)
                    self.events.put(('done', str(clip)))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        while not self.events.empty():
            event, value = self.events.get_nowait()
            self.message.set(value)
            if event in ('done', 'error'):
                self.running = False
                for control in self.controls:
                    control.configure(state='normal')
                if event == 'done':
                    self.preview_path = value
                    self.play_button.configure(state='normal')
                    self.display(value)
                    self.message.set('Đây là hình sau khi xóa. Mở clip thử để kiểm tra chuyển động; xem hình gốc để so sánh.')
        self.timer = self.after(150, self.poll)

    def play(self):
        if self.preview_path:
            os.startfile(self.preview_path)

    def save(self, *, enabled=False):
        try:
            from teodub.subtitle_clean_profiles import save_profile, set_enabled
            source, _second = self.selected_source()
            if self.studio.clean_source() != source:
                raise ValueError('Video đang chọn đã thay đổi. Đóng và mở lại cửa sổ vùng xóa.')
            options = self.settings(force=True)
            self.studio.clean_options = save_profile(self.studio.data_dir, source, options)
            if enabled:
                self.studio.clean_options = set_enabled(self.studio.data_dir, source, True)
            self.studio.sync_clean_source(force=True)
            self.studio.save_preferences()
            self.studio.refresh_buttons()
            self.studio.status.set('Đã lưu và BẬT xóa Sub gốc bằng GPU. Bấm Lồng tiếng và xuất video.' if enabled else
                                  'Đã lưu vùng nhưng CHƯA BẬT. Tick Xóa Sub gốc, rồi bấm Lồng tiếng và xuất video.')
            self.close()
        except Exception as exc:
            self.message.set(str(exc))

    def close(self):
        self.cancel.set()
        self.after_cancel(self.timer)
        self.destroy()
