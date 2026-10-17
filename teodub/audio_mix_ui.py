"""Two export-volume controls; does not touch speech generation or playback."""
import tkinter as tk
from tkinter import ttk

from teodub.audio_mix import validate_percent


class AudioMixControls(ttk.LabelFrame):
    def __init__(self, parent, fields):
        super().__init__(parent, text='Âm lượng khi xuất video', padding=(8, 4))
        self.fields = fields
        self.scales, self.entries, self.values, self.traces = {}, {}, {}, []
        self.syncing = False
        for index, (key, label) in enumerate((('original_volume', 'Tiếng video gốc'),
                                               ('voice_volume', 'Giọng lồng tiếng'))):
            cell = ttk.Frame(self)
            cell.grid(row=0, column=index, sticky='ew', padx=(0, 12))
            self.columnconfigure(index, weight=1)
            cell.columnconfigure(1, weight=1)
            ttk.Label(cell, text=label).grid(row=0, column=0, padx=(0, 8))
            value = tk.DoubleVar(self, value=float(fields[key].get()))
            self.values[key] = value
            scale = ttk.Scale(cell, from_=0, to=200, variable=value,
                              command=lambda amount, item=key: self.slide(item, amount))
            scale.grid(row=0, column=1, sticky='ew')
            entry = ttk.Spinbox(cell, from_=0, to=200, increment=5, width=5,
                                textvariable=fields[key])
            entry.grid(row=0, column=2, padx=(8, 2))
            ttk.Label(cell, text='%').grid(row=0, column=3)
            self.scales[key], self.entries[key] = scale, entry
            trace = fields[key].trace_add('write', lambda *_args, item=key: self.sync(item))
            self.traces.append((fields[key], trace))
        self.reset_button = ttk.Button(self, text='Mặc định', command=self.reset)
        self.reset_button.grid(row=0, column=2)
        self.controls = [*self.scales.values(), *self.entries.values(), self.reset_button]
        ttk.Label(self, text='0%: tắt tiếng · 100%: mức gốc · 200%: tăng gấp đôi').grid(
            row=1, column=0, columnspan=3, sticky='w', pady=(3, 0))
        self.bind('<Destroy>', self.cleanup, add='+')

    def slide(self, key, amount):
        if not self.syncing:
            self.fields[key].set(str(round(float(amount))))

    def sync(self, key):
        try:
            value = validate_percent(self.fields[key].get(), 'Âm lượng')*100
        except (ValueError, TypeError):
            return  # Keep incomplete typing visible; the start action validates it.
        self.syncing = True
        try:
            self.values[key].set(value)
        finally:
            self.syncing = False

    def reset(self):
        self.fields['original_volume'].set('10')
        self.fields['voice_volume'].set('100')

    def cleanup(self, event):
        if event.widget is self:
            for variable, trace in self.traces:
                variable.trace_remove('write', trace)
            self.traces.clear()
