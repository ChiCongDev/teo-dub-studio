# CLAUDE.md — Tèo Dub Studio

## Project
Windows desktop app (Python/Tkinter) + staff web (Flask, teo.pipideal.com via tunnel).
Douyin/TikTok/YouTube/Facebook video → Whisper ASR → Codex translation → Vietnamese TTS dub
(VieNeu/CapCut) → GPU subtitle inpaint → render → Google Drive → scheduled Facebook Page posting
through per-account Chrome automation (Playwright), optionally planned by AI (Codex).

The app is in daily production use by the owner. Treat every change as a change to a live system.

## Where things are
- `teodub/` desktop app + workers. `teodub_web/` Flask web; it only sends commands to the desktop
  (`page_command_bridge.py`), it never drives a browser itself.
- User data lives OUTSIDE the repo: `%LOCALAPPDATA%\TeoDubStudio` (`app.default_data_dir()`).
  Page publishing state: `page-publishing/` (`schedules.sqlite3`, `activity.sqlite3`,
  `accounts.json`, `publish-settings.json`, `browser-profiles/<account>/chrome`, `credentials/`).
- `work/<YYYYMMDD>-<slug>/` per-change evidence: source backups, test logs, `verification.json`,
  before/after data snapshots. Gitignored. Create one for every non-trivial change, and copy every file
  you will modify into `work/<change>/before/` BEFORE the first edit (there is no git to recover from).
- `README.md` — user-facing changelog/guide, **Vietnamese**, newest section on top.
- `HANDOFF.md` — agent-to-agent log, English, newest dated section on top. ~350 KB: read only the
  top 2–3 sections, grep for the rest. Add a new top section after each deployed change.

## Run and test
```
.venv\Scripts\pythonw.exe -X utf8 -B launch.py        # desktop ("Mo Teo Dub Studio.cmd")
launch.py --worker / --campaign-worker / --campaign-batch-worker   # subprocess entry points
tools\start_web.ps1                                     # web ("Mo Teo Web.cmd")
```
Tests are `unittest` (pytest is not installed). Run from the project root with the `.venv` python;
`tests/` is not a package, and `test_web_*` need `.venv-web\Lib\site-packages` on `sys.path`
(Flask) — see `work/20261015-verification-pause/run_final_checks.py` for the working pattern.
Full related suite takes ~6–9 min and launches offline Chrome; prefer focused module lists.
Set `tempfile.tempdir = str(Path(tempfile.gettempdir()).resolve())` in the runner: the shell TEMP is the 8.3
short path (`CHUNGP~1`) and several existing tests fail on it otherwise (not a code defect).
Other venvs: `.venv-inpaint` (STTN GPU inpaint), `.venv-web` (Flask).

## Module map (flat package, grouped by prefix)
| Area | Modules |
|---|---|
| Dub pipeline | `pipeline` `download` `downloaders/` `asr` `asr_runtime` `codex_translation` `tts` `capcut_tts` `adam_voice` `speech_*` `srt` `subtitles` `subtitle_*` `audio_mix` `render` `inpaint_worker` `wording` |
| Campaigns (batch) | `campaign*` |
| Voice/content learning | `voice_learning*` `voice_profiles` `video_content*` `learning_download` |
| Drive | `google_drive*` `drive_*` |
| Facebook accounts/browser | `facebook_*` `desktop_browser` `channel_*` |
| Page publishing | `page_schedule*` (store, recovery, removal, new attempt), `facebook_web_post` (publisher), `page_browser_reconcile`, `page_publish_settings`, `page_activity`, `page_status` |
| AI planning | `page_ai*` `page_content_*` `codex_chat` |
| Channel autopilot | `channel_autopilot*` — Douyin channel → campaigns → Page AI sources (review mode only); see `SPEC-channel-autopilot.md` |
| Fixed posting times | `page_ai.fixed_slots` + panel fields: owner picks first time/gap, AI writes captions only (validated) |
| Caption review | `plan_review_ui` + review API in `page_content_plan` (`reviews` table, revision plans) |
| Meta Graph API (stalled) | `meta_page_api` |
| Runtime | `app` `restart` `page_command_bridge` `web_*` `common` `models` `ai_models` |

`*_ui.py` (plus `adam_voice`, `dialogue_editor`) are Tk. Non-UI modules must not import Tk or UI modules.

## Ubiquitous Language
| Term (code) | UI (Vietnamese) | Meaning |
|---|---|---|
| Account | Tài khoản | One Facebook login = one isolated Chrome profile. One input slot at a time. |
| Page / Page ID | Page | Always identified by the **numeric** Page ID, never by name or link. |
| Schedule | Lịch | One video/text bound to one Page at a due time. Armed (`Bật`) or saved-unarmed. |
| Post | Bài / bản nháp | Frozen body + media (SHA-256) a schedule publishes. |
| Attempt | Lượt | One prepare→upload→Publish run. Has append-only events. |
| Receipt | Bằng chứng | Evidence that a submission is really published (owner, full caption, video). |
| Submitted, unverified | Đã gửi — chưa xác minh | Publish clicked once; publication NOT confirmed. |
| needs_attention / unknown | Cần kiểm tra | Outcome unclear. Human checks Facebook. Never auto-resolved. |
| Safe restart / transfer | Khởi động lại, giữ công việc | The only way to load new code into the running desktop. |
| Campaign / Source | Chiến dịch / Nguồn | Batch of source videos producing finished (dubbed) videos. |
| AI policy / Plan | Đăng theo kế hoạch AI / Kế hoạch | Per-Page AI config; `teo-page-plan` block from Codex. |
| Channel source | Nguồn kênh | Douyin channel + target Pages + frozen dub settings; feeds Page AI, never arms or posts. |
| Seen video | Video đã lấy | Channel video already claimed into a campaign; never fetched again (global). |
| Review / revision plan | Duyệt caption | Per-item edit/decision on an immutable proposal; approval creates a revision plan with only the chosen items. |

## Safety invariants — the one-way doors (never break, never "simplify" away)
1. **One Publish click per attempt.** Never upload or click Publish again for an attempt that was
   submitted or whose result is unknown. No automatic retry, replay, or catch-up of missed
   schedules after restart. Re-posting only through the explicit user-confirmed flows
   (recovery / recreate / new attempt), which keep the old attempt and evidence intact.
2. **Never claim "published" without a receipt**: numeric Page owner + full caption + matching
   video (duration/aspect), associated with that submission. Not from a button click, a closed
   form, a toast, or a Reel URL inferred from `content_id`. Ambiguity fails closed → unverified.
3. **Persisted history is append-only.** Old events, attempts, receipts and source reservations are
   never rewritten or deleted. Schedule/post changes commit in one SQLite transaction
   (DELETE journal, not WAL), lock order schedule → post, re-validate before commit.
4. **Deploy only via the built-in safe restart.** Never kill the desktop or Chrome. If restart is
   blocked by an open Facebook composer, leave the composer alone and tell the user.
5. **No live side effects without explicit user approval in this session**: no real Facebook
   action, AI (Codex) call, Meta API call, schedule arming, or user-data edit. Tests use temp stores
   and offline Playwright fixtures; never point a test at the production data path.
6. **Before/after preservation check** around any deploy: settings, schedule/post/event tables and
   campaign file hashes unchanged (pattern: `work/*/audit_data.py`, `before-data.json`).
7. **No secrets handling**: never read cookies, passwords or SMS codes; never copy Chrome profiles;
   Page tokens stay DPAPI-encrypted under `credentials/`.
8. **AI calls**: Codex CLI on the user's ChatGPT login (no paid API). No automatic model fallback or
   retry; at most one AI run per Page per day. Whisper: no silent GPU→CPU fallback mid-job.

## Reporting rules
- Say "offline-tested" vs "live-verified" explicitly. Offline success never proves a real post.
- Overlapping test runs are not summed. Report the exact command/module list and pass count.
- Facebook automation can get accounts restricted (Meta ToS). Never promise a safe posting rate.

## Boundaries (shipright) — for new and touched code
1. UI (`*_ui.py`) → controller/store; stores never import UI. Web → desktop only via the bridge.
2. `facebook_web_post.py` is the adapter for Facebook's DOM: raw DOM/text stays inside it; the rest
   of the app sees typed outcomes (submitted / receipt / needs_attention).
3. One owner per SQLite store; other modules go through its functions (see debt below).
4. Every external call (Facebook, Codex CLI, Meta, downloads, Drive) has a deadline and a defined
   result on timeout — usually "stop and mark for checking", never "try again".

## Tech debt log
| Debt | Trigger to fix |
|---|---|
| No git repo — rollback relies on `work/` backups | Now: `git init` + baseline commit (user approval pending) |
| `facebook_web_post.py` ~2,300 lines, most fixes land here as Facebook's UI changes | Next publisher fix: extract DOM readers/result parsing behind the adapter, test via fixtures |
| `page_browser_reconcile`, `page_schedule_recovery`, `page_schedule_new_attempt` open the schedule DB directly | When a 4th module needs it: move into one schedule-store API |
| `schedules.finished_at` means both "published" and "sent once, unverified" (`submitted`); guards that read it alone misfire (16/10 new-attempt bug) | Next guard touching `finished_at`: read the state/removal record, not the timestamp |
| Meta Graph API path stalled at Meta phone verification | User finishes verification → prefer API over browser for posting |
| README/HANDOFF too large to load | Keep CLAUDE.md as the entry point; archive HANDOFF sections older than 14 days |

## Ask before doing
- Anything touching invariants 1–8, the publisher's submit path, or the safe restart flow.
- Adding a dependency to any venv; changing a persisted state key or status API field.
- Deleting or rewriting anything under the data root or `work/`.

## Shipright state
Level: L3 (production, operating) — HARDEN for Page publishing
Active: Channel autopilot phases 1–3 + caption review done and tested (804/804), NOT deployed (work/20261016-channel-autopilot, work/20261016-plan-review). Also NOT deployed: new-attempt fix (389/389). Result checks paused
Next: user safe restart, then a first live trial (1 Page, 3 videos/scan) and review in Duyệt caption; Codex owns phase 0 (post verification). After the restart run `work/20261016-new-attempt-submitted/audit_data.py after` (expect unchanged); user checks Minh châu sneaker for duplicates (16 Publish clicks, 0 confirmed) before any new attempt; `git init` still awaiting approval (.gitignore must add .venv-inpaint 7.3 GB, tools/node-runtime). Other agent on this repo is Codex → consider AGENTS.md
Last updated: 2026-10-16
Skills active: none installed; bundled: /security-review, /code-review (user-run), /run
Registry: desktop app + browser automation + AI (Codex CLI)
