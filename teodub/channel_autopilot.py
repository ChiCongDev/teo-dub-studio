"""Douyin channel sources that feed dubbed videos into each Page's AI plan.

A source scans its channel, turns unseen videos into campaigns, queues them
while Tèo is idle and adds them to the Page AI policy in review mode. It never
approves a plan, arms a schedule or publishes: the owner reviews every caption.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import uuid

from teodub.campaign import create_campaign, is_running, read_json, should_process
from teodub.channel_browser import BROWSER_LABELS, profile_directory
from teodub.channel_discovery import channel_url, scan_channel
from teodub.downloaders.video_download import share_url
from teodub.facebook_accounts import FacebookAccounts
from teodub.page_activity import now
from teodub.page_ai import PageAIStore
from teodub.page_schedule import LOCAL_TIME

PER_SCAN = 10
DAILY_LIMIT = 20
SCAN_INTERVAL = 86400
BUSY_RETRY = 300
SCAN_READ_LIMIT = 60
AI_MORNING = (6, 30)
STATES = {'active', 'paused', 'needs_attention', 'removed'}
CAPTION_STYLE = ('Caption giải trí, ngắn gọn và lôi cuốn: nêu một tình huống cụ thể trong video kèm một nét vui, '
    'khoảng 8–20 từ; xen kẽ câu kể và lời thoại trích từ video; ít emoji, 1–2 hashtag liên quan; '
    'chỉ đặt câu hỏi khi hợp tình huống. Không bịa chi tiết video không có. '
    'Ưu tiên khung giờ đông người xem (trưa 11:30–12:30, tối 19:00–21:30); đây là giả thuyết cần kiểm chứng.')


def review_policy(campaigns):
    """Daily review-mode AI plan for a Page fed by channel sources: never auto-arms."""
    return dict(campaigns=list(campaigns), model='gpt-5.6-terra', effort='low', days=1, videos_per_day=2,
                texts_per_week=0, interval_days=1, goal=CAPTION_STYLE, auto_publish=False)


def next_morning(clock):
    local = datetime.fromtimestamp(clock, LOCAL_TIME)
    target = local.replace(hour=AI_MORNING[0], minute=AI_MORNING[1], second=0, microsecond=0)
    if target.timestamp() <= clock+600:
        target += timedelta(days=1)
    return target.timestamp()


def _day_start(clock):
    return datetime.fromtimestamp(clock, LOCAL_TIME).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _local_text(clock):
    return datetime.fromtimestamp(clock, LOCAL_TIME).strftime('%H:%M %d/%m')


class ChannelSourceStore:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.path = self.data_dir/'channel-autopilot'/'sources.sqlite3'

    @contextmanager
    def database(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,channel TEXT NOT NULL,label TEXT NOT NULL,
                targets TEXT NOT NULL,shared INTEGER NOT NULL,per_scan INTEGER NOT NULL,settings TEXT NOT NULL,
                original_dir TEXT NOT NULL,output_dir TEXT NOT NULL,browser TEXT NOT NULL,state TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,last_scan_at TEXT NOT NULL DEFAULT '',
                next_scan_at REAL NOT NULL);
              CREATE UNIQUE INDEX IF NOT EXISTS live_channel ON sources(channel) WHERE state<>'removed';
              CREATE TABLE IF NOT EXISTS seen(url TEXT PRIMARY KEY,source_id TEXT NOT NULL,at REAL NOT NULL,
                campaign_id TEXT NOT NULL DEFAULT '');
              CREATE TABLE IF NOT EXISTS links(campaign_id TEXT NOT NULL,source_id TEXT NOT NULL,
                account_id TEXT NOT NULL,page_key TEXT NOT NULL,linked INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(campaign_id,account_id,page_key));
              CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY,source_id TEXT NOT NULL,at TEXT NOT NULL,
                action TEXT NOT NULL,detail TEXT NOT NULL);
            ''')
            with db:
                yield db
        except sqlite3.IntegrityError as exc:
            if 'sources.channel' in str(exc):
                raise ValueError('Kênh này đã có trong Nguồn kênh; dùng nguồn đang có.') from None
            raise ValueError('Dữ liệu Nguồn kênh bị trùng; chưa lưu. '+str(exc)) from None
        except sqlite3.Error as exc:
            raise ValueError('Chưa đọc/lưu được Nguồn kênh. '+str(exc)) from None
        finally:
            db.close()

    @staticmethod
    def _decode(row):
        result = dict(row)
        result['targets'] = json.loads(result['targets'])
        result['settings'] = json.loads(result['settings'])
        result['shared'] = bool(result['shared'])
        return result

    def add(self, channel, label, targets, *, shared, per_scan, settings, original_dir, output_dir, browser, clock):
        ident = uuid.uuid4().hex
        with self.database() as db:
            db.execute('INSERT INTO sources(id,channel,label,targets,shared,per_scan,settings,original_dir,output_dir,'
                       'browser,state,message,created_at,next_scan_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (ident, channel, label, json.dumps(targets), int(shared), per_scan,
                        json.dumps(settings, ensure_ascii=False), original_dir, output_dir, browser, 'active',
                        'Chờ quét lần đầu.', now(), clock))
        self.journal(ident, 'Thêm nguồn kênh', json.dumps(dict(channel=channel, targets=targets, shared=shared,
                                                                per_scan=per_scan), ensure_ascii=False))
        return self.get(ident)

    def get(self, ident):
        with self.database() as db:
            row = db.execute('SELECT * FROM sources WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise ValueError('Không còn Nguồn kênh này.')
        return self._decode(row)

    def sources(self, *, include_removed=False):
        with self.database() as db:
            rows = db.execute('SELECT * FROM sources'+('' if include_removed else " WHERE state<>'removed'")
                              +' ORDER BY created_at').fetchall()
        return [self._decode(row) for row in rows]

    def update(self, ident, **values):
        if not set(values) <= {'state', 'message', 'last_scan_at', 'next_scan_at'}:
            raise ValueError('Trường Nguồn kênh không hợp lệ.')
        if values.get('state', 'active') not in STATES:
            raise ValueError('Trạng thái Nguồn kênh không hợp lệ.')
        with self.database() as db:
            db.execute('UPDATE sources SET '+','.join(k+'=?' for k in values)+' WHERE id=?', (*values.values(), ident))

    def attention(self, ident, message):
        self.update(ident, state='needs_attention', message=message)
        self.journal(ident, 'Cần kiểm tra', message)

    def journal(self, ident, action, detail=''):
        with self.database() as db:
            db.execute('INSERT INTO journal(source_id,at,action,detail) VALUES (?,?,?,?)', (ident, now(), action, str(detail)[:20000]))

    def history(self, ident):
        with self.database() as db:
            return [dict(r) for r in db.execute('SELECT * FROM journal WHERE source_id=? ORDER BY id DESC LIMIT 200', (ident,))]

    def seen_urls(self):
        with self.database() as db:
            return {row['url'] for row in db.execute('SELECT url FROM seen')}

    def accepted_since(self, clock):
        with self.database() as db:
            return db.execute('SELECT count(*) FROM seen WHERE at>=?', (clock,)).fetchone()[0]

    def taken(self, ident):
        with self.database() as db:
            return db.execute("SELECT count(*) FROM seen WHERE source_id=? AND campaign_id NOT IN ('','interrupted')",
                              (ident,)).fetchone()[0]

    def reserve(self, ident, urls, clock):
        """Claim videos before creating campaigns; an interrupted claim is never fetched again."""
        with self.database() as db:
            db.executemany('INSERT INTO seen(url,source_id,at) VALUES (?,?,?)', [(url, ident, clock) for url in urls])

    def release(self, urls):
        with self.database() as db:
            db.executemany("DELETE FROM seen WHERE url=? AND campaign_id=''", [(url,) for url in urls])

    def attach(self, ident, campaign_id, urls, targets):
        with self.database() as db:
            db.executemany("UPDATE seen SET campaign_id=? WHERE url=? AND source_id=? AND campaign_id=''",
                           [(campaign_id, url, ident) for url in urls])
            db.executemany('INSERT INTO links(campaign_id,source_id,account_id,page_key) VALUES (?,?,?,?)',
                           [(campaign_id, ident, t['account_id'], t['page_key']) for t in targets])

    def unlinked(self):
        with self.database() as db:
            return [dict(r) for r in db.execute('SELECT * FROM links WHERE linked=0 ORDER BY rowid')]

    def mark_linked(self, link):
        with self.database() as db:
            db.execute('UPDATE links SET linked=1 WHERE campaign_id=? AND account_id=? AND page_key=?',
                       (link['campaign_id'], link['account_id'], link['page_key']))

    def campaigns(self, states=('active',)):
        marks = ','.join('?'*len(states))
        with self.database() as db:
            return [r['campaign_id'] for r in db.execute(
                'SELECT DISTINCT links.campaign_id FROM links JOIN sources ON sources.id=links.source_id '
                f'WHERE sources.state IN ({marks}) ORDER BY links.rowid', states)]

    def meta(self, key):
        with self.database() as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row['value'] if row else ''

    def set_meta(self, key, value):
        with self.database() as db:
            db.execute('INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))

    def recover(self):
        """Report claims left by a crash; keep them reserved so no video can be processed twice."""
        with self.database() as db:
            rows = db.execute("SELECT source_id,count(*) AS n FROM seen WHERE campaign_id='' GROUP BY source_id").fetchall()
            db.execute("UPDATE seen SET campaign_id='interrupted' WHERE campaign_id=''")
        for row in rows:
            self.attention(row['source_id'], f'Lượt quét trước bị gián đoạn: {row["n"]} video đã giữ chỗ nhưng chưa rõ '
                           'đã tạo chiến dịch chưa. Tèo không lấy lại các video này để tránh trùng; '
                           'kiểm tra Danh sách chiến dịch rồi bấm Tiếp tục.')


class ChannelAutopilot:
    """Owned by the desktop UI thread; scans run on a worker thread, everything else on tick()."""

    def __init__(self, studio, campaign_tab, batch_controller, *, scan=scan_channel, create=create_campaign,
                 clock=time.time, spawn=None):
        self.studio, self.campaign_tab, self.batch = studio, campaign_tab, batch_controller
        self.data_dir = Path(studio.data_dir).resolve()
        self.store = ChannelSourceStore(self.data_dir)
        self.ai = PageAIStore(self.data_dir)
        self.scan, self.create, self.clock = scan, create, clock
        self.spawn = spawn or (lambda work: threading.Thread(target=work, daemon=True, name='teo-channel-scan').start())
        self.results = queue.Queue()
        self.cancel = threading.Event()
        self.scanning = None
        self.closed = False
        self.recovered = False

    # Owner actions -----------------------------------------------------------------
    def add_source(self, text, targets, *, shared, per_scan, settings, original_dir, output_dir, browser):
        channel = channel_url(text)
        if type(per_scan) is not int or not 1 <= per_scan <= 20:
            raise ValueError('Số video mới mỗi lượt quét từ 1 đến 20.')
        if type(shared) is not bool or browser not in BROWSER_LABELS or not isinstance(settings, dict) or not settings:
            raise ValueError('Cấu hình Nguồn kênh chưa hợp lệ.')
        for value in (original_dir, output_dir):
            if not value or not Path(value).is_absolute():
                raise ValueError('Chọn thư mục video gốc và video lồng tiếng ở tab Chiến dịch hàng loạt trước.')
        targets = self._targets(targets)
        label = 'Douyin · '+channel.rstrip('/').rsplit('/', 1)[-1][-8:]
        return self.store.add(channel, label, targets, shared=shared, per_scan=per_scan, settings=settings,
                              original_dir=str(original_dir), output_dir=str(output_dir), browser=browser,
                              clock=self.clock())

    def scan_now(self, ident):
        source = self.store.get(ident)
        if source['state'] == 'removed':
            raise ValueError('Nguồn kênh đã bỏ.')
        self.store.update(ident, next_scan_at=self.clock())

    def pause(self, ident):
        self.store.update(ident, state='paused', message='Đã tạm dừng: không quét kênh và không tự xử lý video của nguồn này.')
        self.store.journal(ident, 'Tạm dừng')

    def resume(self, ident):
        if self.store.get(ident)['state'] == 'removed':
            raise ValueError('Nguồn kênh đã bỏ.')
        self.store.update(ident, state='active', message='Đã tiếp tục; quét lại ngay.', next_scan_at=self.clock())
        self.store.journal(ident, 'Tiếp tục')

    def remove(self, ident):
        self.store.update(ident, state='removed', message='Đã bỏ nguồn. Chiến dịch, video và lịch sử được giữ.')
        self.store.journal(ident, 'Bỏ nguồn')

    def processing_blocked(self):
        return self.store.meta('processing_blocked')

    def resume_processing(self):
        self.store.set_meta('processing_blocked', '')
        self.store.set_meta('last_pending', '')

    def close(self):
        self.closed = True
        self.cancel.set()

    # Desktop timer -----------------------------------------------------------------
    def tick(self):
        if self.closed:
            return
        if not self.recovered:
            self.store.recover()
            self.recovered = True
        self._drain()
        if self._halted():
            return
        self._link()
        self._process()
        self._scan_due()

    def _halted(self):
        return any(getattr(self.studio, flag, False) is True
                   for flag in ('closing', 'closed', 'restart_pending', 'restart_draining'))

    def _idle(self):
        return (self.studio.process is None and not self.studio.campaign_busy and not self.studio.login_pending
                and getattr(self.studio, 'voice_learning_busy', False) is not True
                and not self.campaign_tab.scanning and not self.batch.busy)

    def _targets(self, targets):
        if not isinstance(targets, list) or not 1 <= len(targets) <= 20:
            raise ValueError('Chọn từ 1 đến 20 Page đích.')
        pages = {(p['account_id'], p['id']): p for p in FacebookAccounts(self.data_dir).load()['pages']}
        result = []
        for target in targets:
            key = (target.get('account_id'), target.get('page_key')) if isinstance(target, dict) else None
            page = pages.get(key)
            if page is None:
                raise ValueError('Page đích không còn trong danh sách tài khoản.')
            if not re.fullmatch(r'\d+', str(page.get('page_id', ''))):
                raise ValueError(f'Page {page["name"]} chưa có Page ID bằng số; sửa Page trong Quản lý Page trước.')
            if dict(account_id=key[0], page_key=key[1]) not in result:
                result.append(dict(account_id=key[0], page_key=key[1]))
        return result

    def _page_name(self, target):
        try:
            return self.ai.plans._page(target['account_id'], target['page_key'])['name']
        except (ValueError, KeyError, OSError):
            return 'Page'

    # Scanning ----------------------------------------------------------------------
    def _scan_due(self):
        if self.scanning or self.campaign_tab.scanning:
            return
        clock = self.clock()
        due = [s for s in self.store.sources() if s['state'] == 'active' and s['next_scan_at'] <= clock]
        if not due:
            return
        source = due[0]
        self.scanning = source['id']
        profile = profile_directory(self.data_dir/'douyin-channel-browser', source['browser'])

        def work():
            try:
                result = self.scan(source['channel'], profile, limit=SCAN_READ_LIMIT, visible=False,
                                   cancel=self.cancel, browser=source['browser'])
                self.results.put((source['id'], result, ''))
            except Exception as exc:
                self.results.put((source['id'], None, str(exc) or type(exc).__name__))
        self.spawn(work)

    def _drain(self):
        while True:
            try:
                ident, result, error = self.results.get_nowait()
            except queue.Empty:
                return
            self.scanning = None
            if not self.closed:
                self._apply_scan(ident, result, error)

    def _apply_scan(self, ident, result, error):
        source = self.store.get(ident)
        if source['state'] != 'active':
            return  # Paused or removed while scanning: discard the list.
        clock = self.clock()
        if error:
            if 'đang dùng cùng hồ sơ trình duyệt' in error:
                self.store.update(ident, next_scan_at=clock+BUSY_RETRY,
                                  message='Hồ sơ đọc kênh đang bận; tự thử lại sau 5 phút.')
            else:
                self.store.attention(ident, 'Chưa đọc được kênh: '+error)
            return
        items = result.get('items') or []
        if not items:
            self.store.attention(ident, result.get('message') or 'Kênh không trả video nào; kiểm tra link hoặc đăng nhập Douyin.')
            return
        seen, fresh = self.store.seen_urls(), []
        for item in items:
            try:
                url = share_url(item['url'])
            except (ValueError, KeyError, TypeError):
                continue
            if url not in seen and all(url != row['url'] for row in fresh):
                fresh.append(dict(url=url, title=str(item.get('title', ''))[:300]))
        room = max(0, DAILY_LIMIT-self.store.accepted_since(_day_start(clock)))
        take = fresh[:min(source['per_scan'], room)]
        if not take:
            note = ('Không có video mới.' if not fresh else
                    f'Đã đủ {DAILY_LIMIT} video hôm nay; {len(fresh)} video mới chờ lượt quét sau.')
            self._scanned(source, clock, note)
            return
        targets = source['targets']
        groups = ([(targets, take)] if source['shared'] or len(targets) == 1 else
                  [([t], take[i::len(targets)]) for i, t in enumerate(targets) if take[i::len(targets)]])
        self.store.reserve(ident, [row['url'] for row in take], clock)
        stamp = datetime.fromtimestamp(clock, LOCAL_TIME).strftime('%d/%m %H:%M')
        for index, (pages, records) in enumerate(groups):
            name = (source['label']+' → '+(self._page_name(pages[0]) if len(pages) == 1 else f'{len(pages)} Page')
                    +' · '+stamp)
            discovery = dict(channel=source['channel'], source_id=ident, complete=bool(result.get('complete')),
                             reason=str(result.get('reason', '')), message=str(result.get('message', '')), items=records)
            try:
                directory = Path(self.create(self.data_dir, name, records, source['settings'], source['original_dir'],
                                             source['output_dir'], discovery))
            except Exception as exc:
                self.store.release([row['url'] for _, rows in groups[index:] for row in rows])
                self.store.attention(ident, 'Chưa tạo được chiến dịch: '+(str(exc) or type(exc).__name__))
                return
            self.store.attach(ident, directory.name, [row['url'] for row in records], pages)
            self.store.journal(ident, 'Tạo chiến dịch', f'{directory.name} · {len(records)} video · {name}')
        note = f'Đã lấy {len(take)} video mới → {len(groups)} chiến dịch.'
        if len(fresh) > len(take):
            limit = (f'đã đủ {DAILY_LIMIT} video hôm nay' if room < source['per_scan']
                     else f'mỗi lượt lấy {source["per_scan"]} video')
            note += f' Còn {len(fresh)-len(take)} video mới chờ lượt sau ({limit}).'
        self._scanned(source, clock, note)

    def _scanned(self, source, clock, message):
        self.store.update(source['id'], state='active', message=message, last_scan_at=now(),
                          next_scan_at=clock+SCAN_INTERVAL)

    # Page AI -----------------------------------------------------------------------
    def _link(self):
        notes = {}
        for link in self.store.unlinked():
            try:
                source = self.store.get(link['source_id'])
                if source['state'] == 'removed':
                    continue
                note = self._link_one(link)
            except ValueError as exc:
                self.store.attention(link['source_id'], str(exc))
                continue
            if note:
                notes.setdefault(link['source_id'], []).append(note)
        for ident, items in notes.items():
            source = self.store.get(ident)
            if source['state'] != 'needs_attention':
                self.store.update(ident, message=source['message']+' '+' '.join(items))

    def _link_one(self, link):
        account, page, campaign = link['account_id'], link['page_key'], link['campaign_id']
        name = self._page_name(link)
        policy = self.ai.policy(account, page)
        if policy is None:
            due = next_morning(self.clock())
            self.ai.save(account, page, review_policy([campaign]), due, True)
            self.ai.journal(account, page, 'Nguồn kênh', 'Tạo AI chế độ duyệt',
                            'AI soạn bài mỗi ngày; chỉ bật lịch sau khi bạn duyệt.')
            self.store.mark_linked(link)
            return f'AI của {name} sẽ soạn bài lúc {_local_text(due)}; duyệt trước khi đăng.'
        try:
            updated = self.ai.add_campaigns(account, page, [campaign])
        except ValueError as exc:
            raise ValueError(f'{name}: {exc}') from None
        if updated is None:
            return None  # An AI turn is running for this Page; retry on a later tick.
        self.store.mark_linked(link)
        if not updated['enabled']:
            return f'AI của {name} đang tắt; bật ở Quản lý Page → Đăng theo kế hoạch AI.'
        return ''

    # Processing --------------------------------------------------------------------
    def _process(self):
        if self.store.meta('processing_blocked'):
            return
        batch = self.store.meta('batch_dir')
        if batch:
            state, message = self._batch_state(Path(batch))
            if state == 'running':
                return
            self.store.set_meta('batch_dir', '')
            if state != 'done':
                self.store.set_meta('processing_blocked', 'Hàng đợi xử lý của Nguồn kênh đã dừng: '+message)
                return
        pending = []
        for campaign in self.store.campaigns():
            try:
                manifest = read_json(self.data_dir/'campaigns'/campaign/'campaign.json')
            except (OSError, ValueError):
                continue
            remaining = sum(should_process(item) for item in manifest.get('items', []))
            if remaining and not manifest.get('archived_at'):
                pending.append((campaign, remaining))
        if not pending:
            self.store.set_meta('last_pending', '')
            return
        signature = json.dumps(pending)
        if signature == self.store.meta('last_pending'):
            self.store.set_meta('processing_blocked', 'Hàng đợi xử lý không tiến triển sau lượt chạy trước; '
                                'xem Danh sách chiến dịch rồi bấm Tiếp tục xử lý tự động.')
            return
        if not self._idle():
            return
        try:
            self.batch.start([self.data_dir/'campaigns'/campaign for campaign, _ in pending])
        except ValueError:
            return  # Another job started first; try again later.
        except OSError as exc:
            self.store.set_meta('processing_blocked', 'Chưa khởi động được xử lý video của Nguồn kênh: '+str(exc))
            return
        self.store.set_meta('batch_dir', str(self.batch.directory))
        self.store.set_meta('last_pending', signature)

    def _batch_state(self, directory):
        if is_running(directory) or (self.batch.directory == directory and self.batch.busy):
            return 'running', ''
        try:
            queue_data = read_json(directory/'queue.json')
        except (OSError, ValueError):
            return 'stopped', 'không đọc được hàng đợi.'
        if queue_data.get('status') in ('completed', 'needs_attention'):
            return 'done', ''
        return 'stopped', str(queue_data.get('message') or 'hàng đợi bị gián đoạn.')

    # Display -----------------------------------------------------------------------
    def rows(self):
        result = []
        for source in self.store.sources():
            pages = ', '.join(self._page_name(t) for t in source['targets'])
            result.append(dict(source, pages=pages, taken=self.store.taken(source['id']),
                               next_text=_local_text(source['next_scan_at']) if source['state'] == 'active' else '—'))
        return result
