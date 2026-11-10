"""Bấm tiêu đề cột để sắp xếp bảng (owner 10/11/2026). Click a column heading: ascending, again: descending, a third time: back to the
table's own order. The arrow ▲/▼ on the heading shows which column is used, and the choice is saved in a small file so it is still
there after Tèo is closed and opened again.

Only the ROW ORDER OF THE TABLE changes. Nothing the worker, the schedules or the Page posting read is touched; rows keep their ids,
so selecting, double-clicking and the context menus still act on the row that was clicked. A refresh puts the rows in the chosen order
again, so the table never jumps back."""
import json
import re
from pathlib import Path
from tkinter import TclError

from teodub.common import atomic_json

UP, DOWN = ' ▲', ' ▼'
BLANK = {'', '—', '-', '…', 'None'}
_DAY_FIRST = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?')
_TOKEN = re.compile(r'(\d+)')


def _dates_as_digits(text):
    """24/10/2026 14:36:52 -> 20261024143652, so dates in either order of writing sort by time."""
    def swap(match):
        day, month, year, hour, minute, second = match.groups()
        return f'{year}{int(month):02}{int(day):02}{int(hour or 0):02}{minute or "00"}{second or "00"}'
    return _DAY_FIRST.sub(swap, text)


def natural(text):
    """Video 2 before Video 10, 7% before 40%, a date by its time; text without letter case. Every token is (kind, value), so any two
    cells of one column can be compared."""
    text = _dates_as_digits(str(text)).strip().casefold()
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in _TOKEN.split(text) if part != '')


def cell_key(text, prefixes=()):
    """(rank of the first matching prefix, natural order): a status column groups its labels as the prefixes are listed."""
    shown = str(text).strip()
    rank = next((index for index, prefix in enumerate(prefixes) if shown.casefold().startswith(prefix.casefold())), len(prefixes))
    return rank, natural(shown)


def sorted_ids(rows, descending, prefixes=()):
    """rows: [(id, cell text)] in the table's own order. Blank cells stay last in either direction; equal cells keep their own order."""
    full = [(ident, text) for ident, text in rows if str(text).strip() not in BLANK]
    blank = [ident for ident, text in rows if str(text).strip() in BLANK]
    full.sort(key=lambda row: cell_key(row[1], prefixes), reverse=descending)
    return [ident for ident, _text in full]+blank


class TableSort:
    """Makes the headings of a ttk.Treeview clickable. `path` is where the choice is kept (None: not kept). `priority` maps a column
    to the label prefixes of its groups, first group first (for the status columns)."""

    def __init__(self, tree, *, path=None, priority=None):
        self.tree = tree
        self.path = Path(path) if path else None
        self.priority = dict(priority or {})
        self.columns = list(tree['columns'])
        self.labels = {column: str(tree.heading(column, 'text')) for column in self.columns}
        self.default = []
        self.column, self.descending = self.load()
        for column in self.columns:
            tree.heading(column, command=lambda name=column: self.click(name))
        self.show()

    def load(self):
        if self.path is None:
            return None, False
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None, False
        column = saved.get('column') if isinstance(saved, dict) else None
        return (column, saved.get('descending') is True) if column in self.columns else (None, False)

    def save(self):
        if self.path is None:
            return
        try:
            atomic_json(self.path, dict(version=1, column=self.column, descending=self.descending))
        except OSError:
            pass  # Only the memory of the choice is lost; the table still sorts.

    def click(self, column):
        if self.column != column:
            self.column, self.descending = column, False
        elif not self.descending:
            self.descending = True
        else:
            self.column, self.descending = None, False
        self.save()
        self.show()
        self.apply()

    def show(self):
        for column in self.columns:
            arrow = (DOWN if self.descending else UP) if column == self.column else ''
            try:
                self.tree.heading(column, text=self.labels[column]+arrow)
            except TclError:
                return  # The table is gone.

    def apply(self, default=None):
        """Put the rows in the chosen order. `default` is the table's own order of row ids (the order of an unsorted table); it is
        remembered for the click that clears the sort."""
        try:
            present = list(self.tree.get_children())
            if default is not None:
                self.default = list(default)
            own = [ident for ident in self.default if self.tree.exists(ident)]
            known = set(own)
            own += [ident for ident in present if ident not in known]
            if self.column is None:
                wanted = own
            else:
                index = self.columns.index(self.column)
                cells = {ident: self.tree.item(ident, 'values') for ident in own}
                rows = [(ident, str(cells[ident][index]) if len(cells[ident]) > index else '') for ident in own]
                wanted = sorted_ids(rows, self.descending, self.priority.get(self.column, ()))
            if wanted != present:
                for position, ident in enumerate(wanted):
                    self.tree.move(ident, '', position)
        except TclError:
            return  # The table is gone.
