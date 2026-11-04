"""Add the videos of a channel that no campaign has yet to an existing campaign (owner 30/10/2026).

A link was made from the first N videos of a channel; later the owner reads the channel again (logged in, higher
limit) and wants only the videos Tèo does not have, appended to that same link as Video N+1, N+2 ... Nothing here
reads a channel, starts a video or calls AI: it only compares links and writes the new videos' local records.

Who writes: while a rotation runs, the worker is the only writer of campaign.json and queue.json, so the desktop only
leaves an `append-*.json` request in the queue folder (`request_append`) and the worker applies it between two turns
(`campaign_batch._absorb_appends`). When nothing runs, the desktop applies it itself (`append_videos`). Videos already
in the campaign keep their number, status, outputs and files: the new ones are only added after them.
"""
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
import uuid

from teodub.campaign import CONFIG_KEYS, MAX_CAMPAIGN_VIDEOS, likes_first, likes_of, read_json
from teodub.common import atomic_json, hash_file
from teodub.downloaders.video_download import share_url
from teodub.pipeline import _job_lock

APPEND_REQUESTS = 'append-*.json'


def _n(value):
    return f'{value:,}'.replace(',', '.')


def _normal(url):
    try:
        return share_url(str(url))
    except ValueError:
        return None


def _records_of(path):
    try:
        value = read_json(path)
    except (OSError, ValueError):
        return []
    return value.get('records', []) if isinstance(value, dict) and isinstance(value.get('records'), list) else []


def known_urls(data_dir):
    """Every video link Tèo already has: all saved campaigns (archived ones too) and appends still waiting for the
    worker. Read only."""
    data_dir = Path(data_dir)
    known = set()
    for path in (data_dir/'campaigns').glob('*/campaign.json'):
        try:
            items = read_json(path).get('items', [])
        except (OSError, ValueError, AttributeError):
            continue
        known.update(url for url in (_normal(item.get('url', '')) for item in items if isinstance(item, dict)) if url)
    for path in (data_dir/'campaign-batches').glob('*/'+APPEND_REQUESTS):
        known.update(url for url in (_normal(row.get('url', '')) for row in _records_of(path)
                                     if isinstance(row, dict)) if url)
    return known


def split_known(records, known):
    """(new, already): the records whose video no campaign has, in the channel's order and without repeats, and how
    many were skipped because Tèo has them (or they repeat inside the list)."""
    fresh, seen, already = [], set(known), 0
    for row in records:
        url = _normal(row.get('url', ''))
        if url is None:
            continue
        if url in seen:
            already += 1
            continue
        seen.add(url)
        fresh.append(dict(url=url, title=str(row.get('title', ''))[:300]))
        if likes_of(row) is not None:
            fresh[-1]['likes'] = likes_of(row)
    return fresh, already


def append_videos(directory, records):
    """Add `records` after the last video of the campaign. Returns dict(first, count, skipped).

    Refuses while the campaign runs (its worker rewrites campaign.json) and never changes an existing video. A video
    the campaign already has is skipped, so applying one request twice adds nothing twice. Job files are written
    first and campaign.json last: a crash before it leaves only unused folders.
    """
    from teodub.campaign_batch import _identity
    directory = Path(directory).resolve()
    if not isinstance(records, list) or not records:
        raise ValueError('Không có video mới để thêm.')
    held = ExitStack()
    try:
        held.enter_context(_job_lock(directory))
    except (OSError, ValueError) as exc:  # Only taking the lock: the campaign's worker holds it while it works.
        raise ValueError('Link này đang làm video; đợi xong hoặc bấm Dừng link này rồi thêm video.') from exc
    with held:
        manifest = read_json(directory/'campaign.json')
        _identity(manifest, directory)  # Archived or damaged data is refused before anything is written.
        items = manifest['items']
        have = {url for url in (_normal(item.get('url', '')) for item in items) if url}
        fresh, skipped = split_known(records, have)
        fresh = likes_first(fresh)  # 03/11/2026 (owner): the new videos go in most liked first (no change without counts)
        if not fresh:
            return dict(first=None, count=0, skipped=skipped)
        if len(items)+len(fresh) > MAX_CAMPAIGN_VIDEOS:
            raise ValueError(f'Mỗi chiến dịch tối đa {_n(MAX_CAMPAIGN_VIDEOS)} video; link này đang có {_n(len(items))} '
                             f'video, thêm {_n(len(fresh))} video nữa sẽ vượt giới hạn.')
        snapshot = {key: manifest['settings'][key] for key in CONFIG_KEYS if key in manifest['settings']}
        date = datetime.now().strftime('%d-%m-%Y')
        first = len(items)+1
        added = []
        for number, row in enumerate(fresh, first):
            job = directory/'jobs'/f'{number:04}'
            config = dict(snapshot, mode='full', directory=str(job), stop_file=str(job/'stop.request'),
                          input=row['url'], subtitles=None, original_dir=manifest['original_dir'],
                          output_dir=manifest['output_dir'], campaign_basename=f'Video {number}_{date}')
            atomic_json(job/'config.json', config)
            added.append(dict(number=number, title=row['title'], url=row['url'], config_sha256=hash_file(job/'config.json'),
                              job=str(job), status='pending', message='Chờ xử lý', outputs={}))
            if 'likes' in row:
                added[-1]['likes'] = row['likes']
        manifest['items'] = items+added
        manifest.setdefault('appended', []).append(
            dict(at=datetime.now().astimezone().isoformat(timespec='seconds'), first=first, count=len(added)))
        if manifest.get('status') == 'completed':  # Not complete any more: the new videos wait for Chạy.
            manifest['status'] = 'paused'
            manifest['message'] = f'Đã thêm {len(added)} video mới; bấm Chạy để làm tiếp.'
        atomic_json(directory/'campaign.json', manifest)
        with (directory/'links.txt').open('a', encoding='utf-8') as links:
            links.write(''.join(row['url']+'\n' for row in fresh))
        return dict(first=first, count=len(added), skipped=skipped)


def request_append(queue_directory, campaign_directory, records):
    """Leave the records for a running rotation's worker to add between two turns; returns the request's path."""
    queue_directory = Path(queue_directory)
    if not isinstance(records, list) or not records:
        raise ValueError('Không có video mới để thêm.')
    path = queue_directory/f'append-{uuid.uuid4().hex}.json'
    atomic_json(path, dict(directory=str(Path(campaign_directory).resolve()),
                           records=[dict(url=str(row['url']), title=str(row.get('title', ''))[:300]) for row in records]))
    return path
