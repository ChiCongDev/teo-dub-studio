"""The release number of this copy of Tèo, read from version.json next to launch.py."""
import json
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parents[1]/'version.json'


def current():
    """Return {'version': 'x.y.z', 'released': 'YYYY-MM-DD'}; '0.0.0' when the file is missing or damaged."""
    try:
        data = json.loads(VERSION_FILE.read_text(encoding='utf-8'))
        parts = str(data['version']).split('.')
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            return dict(version='.'.join(parts), released=str(data.get('released', '')))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return dict(version='0.0.0', released='')


def label():
    return 'Phiên bản ' + current()['version']
