"""Page đăng (owner 10/11/2026): which Pages use the videos of one link (campaign), read from what Tèo already keeps. Read only: nothing is
written, no lock is taken and no schedule, source or setting changes.

A Page is listed when either
  * the link is one of its "Nguồn video" (the Page's AI plan names the campaign as a source), or
  * the Page already has schedules made from this link's videos (one that is neither removed nor cancelled).
The counts are the schedules made from THIS link's videos only."""
import json
from pathlib import Path
import sqlite3

from teodub.facebook_accounts import FacebookAccounts

SOURCE = 'Nguồn video'
SCHEDULED = 'Lịch đăng'
WAITING_STATES = ('scheduled', 'armed', 'preparing')
SENT_STATES = ('submitting', 'verifying', 'submitted', 'published')
ATTENTION_STATES = ('needs_attention', 'paused')


def _query(path, sql, params=()):
    """The rows of a read-only query, or [] when the database does not exist yet or cannot be read. The connection is always closed."""
    path = Path(path)
    if not path.is_file():
        return []
    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
    finally:
        if connection is not None:
            connection.close()


def _like_prefix(text):
    """`text` as a LIKE pattern that matches it literally (escape character: backslash)."""
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def source_pages(data_dir, campaign_id):
    """{(account_id, page_key)} of the Pages that name this campaign as a source of video."""
    found = set()
    rows = _query(Path(data_dir)/'page-publishing'/'ai-planning.sqlite3', 'SELECT account_id,page_key,config FROM policies')
    for row in rows:
        try:
            if campaign_id in (json.loads(row['config']).get('campaigns') or []):
                found.add((row['account_id'], row['page_key']))
        except (ValueError, AttributeError, TypeError):
            continue  # One damaged policy never hides the others.
    return found


def schedule_counts(data_dir, campaign_id):
    """{(account_id, page_key): dict(waiting, sent, attention)} of the schedules made from this campaign's videos."""
    counts = {}
    rows = _query(Path(data_dir)/'page-publishing'/'schedules.sqlite3',
                  'SELECT account_id,page_key,state,COUNT(*) AS number FROM schedules WHERE source_key LIKE ? ESCAPE ? '
                  'GROUP BY account_id,page_key,state',
                  (_like_prefix(campaign_id)+':%', '\\'))
    for row in rows:
        entry = counts.setdefault((row['account_id'], row['page_key']), dict(waiting=0, sent=0, attention=0))
        for key, states in (('waiting', WAITING_STATES), ('sent', SENT_STATES), ('attention', ATTENTION_STATES)):
            if row['state'] in states:
                entry[key] += row['number']
    return {key: value for key, value in counts.items() if value['waiting'] or value['sent'] or value['attention']}


def pages_of_campaign(data_dir, campaign_id):
    """The Pages that use this link's videos, by Page name. Each row: account_id, account_name, page_key, page_name, in_teo (the Page is
    still saved in Tèo, so its window can be opened), via ('Nguồn video · Lịch đăng'), waiting, sent, attention."""
    campaign_id = str(campaign_id)
    sources = source_pages(data_dir, campaign_id)
    scheduled = schedule_counts(data_dir, campaign_id)
    try:
        registry = FacebookAccounts(str(data_dir)).load()
    except (OSError, ValueError, KeyError, TypeError):
        registry = dict(accounts=[], pages=[])
    accounts = {row['id']: row.get('name', '') for row in registry.get('accounts', [])}
    pages = {(row['account_id'], row['id']): row for row in registry.get('pages', [])}
    rows = []
    for key in sorted(sources | set(scheduled)):
        account_id, page_key = key
        page = pages.get(key)
        counts = scheduled.get(key, dict(waiting=0, sent=0, attention=0))
        via = [name for name, hit in ((SOURCE, key in sources), (SCHEDULED, key in scheduled)) if hit]
        rows.append(dict(account_id=account_id, account_name=accounts.get(account_id, '(không còn tài khoản)'), page_key=page_key,
                         page_name=page['name'] if page else '(Page đã xóa khỏi Tèo)', in_teo=page is not None,
                         via=' · '.join(via), **counts))
    return sorted(rows, key=lambda row: (row['page_name'].casefold(), row['account_name'].casefold()))
