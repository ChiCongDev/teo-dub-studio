"""A video made again with a new voice (campaign_redo): its Page schedules that were never sent follow the new file.

The worker only records, on the finished video, which output the new one replaced (`redone`). Here a Page schedule that still
points at that old output, and has never been attempted, is moved to the new one (`PageScheduleStore.replace_video`): same Page,
same time, same caption. A schedule that was sent or touched is never changed. Nothing here sends anything to Facebook."""
import os
from pathlib import Path

from teodub.campaign import read_json
from teodub.page_schedule import campaign_video_source

CHECK_SECONDS = 20


def _same_path(a, b):
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def follow_new_files(store, *, only=None, limit=None):
    """Move every eligible schedule (or just `only`, a schedule id) to its video's new file. Returns (moved ids, [(id, error)])."""
    moved, errors, manifests = [], [], {}
    for row in store.list():
        source = row['source']
        if only is not None and row['id'] != only:
            continue
        if (source.get('kind') == 'text' or not source.get('campaign') or row['attempted_at'] or row['request_id']
                or row['finished_at'] or row['state'] not in store.REPLACEABLE):
            continue
        directory = Path(source['campaign'])
        if directory not in manifests:
            try:
                manifests[directory] = read_json(directory/'campaign.json')
            except (OSError, ValueError):
                manifests[directory] = None
        manifest = manifests[directory]
        item = next((x for x in (manifest or {}).get('items', []) if x.get('number') == source.get('number')), None)
        redone = (item or {}).get('redone') or {}
        previous = (redone.get('previous_outputs') or {}).get('video')
        if (not item or item.get('status') != 'completed' or item.get('redo') or item.get('no_post')
                or not previous or not _same_path(previous, source['path'])):
            continue
        try:
            fresh = campaign_video_source(store.data_dir, directory, source['number'])
            if fresh['sha256'] == source['sha256'] and _same_path(fresh['path'], source['path']):
                continue
            store.replace_video(row['id'], row, fresh)
            moved.append(row['id'])
        except (OSError, ValueError, KeyError) as exc:
            errors.append((row['id'], str(exc)))
        if limit is not None and len(moved) >= limit:
            break
    return moved, errors
