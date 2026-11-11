"""Cột "Cấu hình áp dụng" (owner 10/11/2026): the name of the saved config (config_presets) each video of a link uses. Display only:
it reads the job configs and the saved presets, writes nothing and changes no setting.

A video "uses" a preset when every setting the preset keeps is equal in the video's config (Google Drive is never part of a preset).
Several presets can match; the one saved last wins. No match: NO_PRESET."""
from pathlib import Path
import json
import time

NO_PRESET = 'Không theo mẫu'
NONE_YET = '—'
ARROW = ' → '


def preset_of(config, presets):
    """The name of the preset whose settings are all found in `config`, or None."""
    if not isinstance(config, dict):
        return None
    for preset in reversed(presets):
        if all(config.get(key) == value for key, value in preset['settings'].items()):
            return preset['name']
    return None


class PresetColumn:
    """Reads the cell of every video of one link. Keeps what it parsed (per file, by size and time) so a long link costs one stat per
    video after the first read."""

    def __init__(self):
        self.cache = {}

    def config(self, job):
        path = Path(job)/'config.json'
        try:
            stat = path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
            hit = self.cache.get(str(path))
            if hit is not None and hit[0] == key:
                return hit[1]
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError):
            return None
        if time.time()-stat.st_mtime > 2:  # A file written a moment ago may be written again within the same timestamp tick.
            self.cache[str(path)] = (key, value)
        return value

    def cells(self, data_dir, directory, manifest):
        """{video number: text} for every item of the manifest."""
        from teodub.campaign_settings import effective_job, next_settings
        from teodub.config_presets import read_presets
        presets = read_presets(data_dir)
        try:
            pending = next_settings(directory)
        except (OSError, ValueError, TypeError, KeyError):
            pending = None
        pending_name = None if pending is None else (preset_of(pending['settings'], presets) or NO_PRESET)
        cells = {}
        for item in manifest['items']:
            try:
                cells[item['number']] = self.cell(item, directory, presets, pending_name)
            except (OSError, ValueError, TypeError, KeyError):
                cells[item['number']] = 'Cần kiểm tra'
        return cells

    def name_of(self, job, presets):
        config = self.config(job)
        return None if config is None else (preset_of(config, presets) or NO_PRESET)

    def cell(self, item, directory, presets, pending_name):
        from teodub.campaign_settings import effective_job
        status = item['status']
        if status == 'excluded':
            return NONE_YET
        redo = item.get('redo')
        if redo and status == 'completed':
            previous = (redo.get('previous') or {}).get('execution_job') or item['job']
            before = self.name_of(previous, presets) or 'Cần kiểm tra'
            if item.get('execution_job') and item['execution_job'] != (redo.get('previous') or {}).get('execution_job'):
                after = self.name_of(effective_job(directory, item), presets)  # The new version is already being made.
            else:
                after = pending_name
            return before if after is None else before+ARROW+after
        if status in ('completed', 'review_required', 'running'):
            return self.name_of(effective_job(directory, item), presets) or 'Cần kiểm tra'
        # Not made yet (or to be tried again): what the link will use for it.
        return pending_name or self.name_of(effective_job(directory, item), presets) or 'Cần kiểm tra'
