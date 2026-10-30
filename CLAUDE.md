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
A mirror (copy of the root files + staged code, `work/<change>/mirror`) needs empty `.venv/Scripts/python.exe` and
`pythonw.exe` placeholders, or `app.worker_command` raises and ~10 campaign UI/rotation/controller tests fail.
Other venvs: `.venv-inpaint` (STTN GPU inpaint), `.venv-web` (Flask).

## Module map (flat package, grouped by prefix)
| Area | Modules |
|---|---|
| Dub pipeline | `pipeline` `download` `downloaders/` `asr` `asr_runtime` `codex_translation` `tts` `capcut_tts` `adam_voice` `speech_*` `srt` `subtitles` `subtitle_*` `audio_mix` `render` `inpaint_worker` `wording` |
| Download sources | `downloaders/`: order `douyin_direct` (Douyin's own page, no login/cookies) → savetik → vienphanmem → socialvideo → anysave; `provider_health` remembers paused sources ("Chờ nguồn tải" + campaign auto-resume) |
| Listen-again / caption time (28/10) | `asr._recheck_gaps` re-listens to empty stretches ≥ 4 s with Whisper medium when `asr_model == 'auto'` and the first model was small (filters in `_keep_recovered`; failure keeps the first pass); `render._display_ends` lengthens only the burned caption layer (speaker's end, next line, 6 s cap), never the voice, `final.srt` or the plan |
| Caption cover | `caption_cover` finds the video's own burned-in captions per cue: `_mask` = light strokes darker on BOTH sides within `STROKE_REACH` (not step edges of tables/plates), still for `PAIR_S`, learned caption rows; a line's region keeps only rows on those rows (+ dense runs within `ROW_TOLERANCE` or a whole second line); wide `subtitle_band` band only as fallback |
| Burned caption box | `caption_layout` (owner 22/10): one rectangle per line, square corners, text centred (`\an5\pos`), box always centred horizontally (widens evenly to reach the own caption), vertically max(text, own caption) at the caption rows, `PADDING` 1/6 line, Vietnamese size per video = own caption height (`matched_size`, >= chosen, <= 1.5x); `render._caption_boxes` writes one `captions.ass`; `subtitle_style.SUBTITLE_BOXES` colours; option absent = none |
| Sleep-safe local limits | `common.AwakeTime` (poll gap > `SLEEP_GAP_S` 60 s = sleep): render ffmpeg limit, inpaint stall, voice tempo; `render._work_directory` cleanup never hides the render error; `render.clear_leftover_work` before a re-render |
| Translation line repair | `codex_translation._rejected_ids` + `_repair_lines`: ≤ `REPAIR_LIMIT` unusable lines → one follow-up (`REPAIR_NOTE`); batch record `repair: {ids, metadata}`; review note `REPAIR_REASON`. `_usable_notes`: invalid review notes in a fresh answer are left out (`notes_dropped`), saved results stay strict |
| Translation prompt versions | `codex_translation.PROMPT_INSTRUCTIONS`: never edit a version (jobs pin theirs in `request.json`). v1, v2 (`STYLE_PROMPT_VERSION`) frozen; v3 (23/10) = v2 + `_DUBBING_LENGTH` (~4 syllables/s) and per-cue `seconds` (`_spoken_seconds`: until the next cue, ≤ `SPOKEN_TAIL_MS` after the end) for `TIMED_PROMPT_VERSIONS` |
| Dub timing + ducking | `render` (owner 23/10): a line starts at its picture moment; longer than its room (next line's moment − gap, or the picture end) → `_stretch_clip` (in-process PyAV atempo, ≤ `MAX_FIT_TEMPO` 1.25; 24/10: the line + `STRETCH_PAD_S` .1 s silence, cut to exactly `round(frames/tempo)`, because atempo keeps back up to ~32 ms at the end); behind → `CATCH_UP_GAP_MS` 80; picture keeps the chosen speed, overrun = last frame held (`last_frame_held_s`). Ducking: `_duck_envelope` gain curve (`DUCK_GAIN` .12 under the voice, fades .15/.3 s, `DUCK_HOLD_S` .6) via `amultiply` in BOTH the export and `_expected_audio_mix` (`_original_audio`); only if original_gain > .12, voice_gain > 0 and the source has audio |
| Recognized last line, saved models | owner 24/10: `pipeline._end_with_picture` — ASR/Codex cues from the first one ending > 200 ms past the picture are mapped in proportion onto [that cue's start (an earlier one's if < 1 ms per line), picture end]; one such cue = it just ends with the picture; > `MAX_LATE_LINES` 5 cues starting after the picture, or no room → unchanged (the SRT check stops it); supplied SRTs unchanged; also after wording. `models.saved_first`: VieNeu (`tts.get_model`) and Whisper (`asr.transcribe`) load from the saved Hugging Face cache with `huggingface_hub.constants.HF_HUB_OFFLINE` set for the load; only `LocalEntryNotFoundError`/`OfflineModeIsEnabled` (a file not saved) → the old online load |
| Campaigns (batch) | `campaign*` |
| Campaign link tabs | `campaign_tabs_ui` (owner 23/10): `CampaignArea` = top tab "Chiến dịch hàng loạt" holding a sub-notebook: '＋ Thêm link' = the unchanged `CampaignTab`, then one `CampaignLinkTab` per opened campaign (`campaign-tabs.json`, closing only hides). Link actions go through `CampaignBatchController.run_link` / `stop_link` / `link_state` (no lock probes while polling); per-link stop = `leave-*.json` → row `stopped` (`campaign_batch.LINK_STOPPED`), the other links keep their turns; a link's Chạy = join (rejoins a stopped row). A link's Thử lại video lỗi (owner 24/10) = `retry_link`: `campaign.request_retry` writes `retry-*.json` (the failed numbers), the worker marks those items `retry_once` at the campaign's next run (`requested_retries`, files removed after the manifest save), `should_process` honours the mark, it goes when the video starts (one try), then `run_link`; enabled while links run; the table shows `campaign_tracking.RETRYING`. While the rotation runs, `link_state` also reads the link's last join/leave request the worker has not taken yet (`_waiting_request`): join → 'waiting' (Chạy disabled at once), leave → 'stopped'; the active link stays 'running'. Library "Mở chiến dịch" and Page manager "Tạo nguồn Douyin mới…" select the sub-tabs |
| `/people/<name>/<id>/` Page address (30/10) | `facebook_web_post._target_url` / `_page_id` / `_safe_page` (+`_where`), `facebook_page_identity._matching_url`, `facebook_accounts.save_page_link`, `facebook_page_discovery`: Facebook's address for a Page without a username; the numeric ID must still equal the saved Page ID. |
| Video limit 10.000 (29/10) | `channel_discovery.MAX_SCAN_VIDEOS` + `scan_timeout` (0.6 s/video, min 180 s; login wait stays 180 s), `campaign.MAX_CAMPAIGN_VIDEOS` (+ `progress` while saving), `campaign_ui.display_big_table` for campaigns > `BIG_TABLE_ITEMS` (1.000): rows read in slices (`BIG_TABLE_SECONDS`, `BIG_TABLE_REFRESH_S`), table written only where it differs; ≤ 1.000 items = the old path. `campaign_batch.MAX_CAMPAIGNS` and the autopilot limit unchanged. |
| Caption size / word times (29/10) | `caption_layout.matched_size` = `min(max(MIN_SUBTITLE_SIZE, round(own letters)), 1.5 x chosen, MAX)` (may be below the chosen size; no own captions = chosen); `asr._spoken_pieces`: first-pass `word_timestamps=True`, a line = first to last spoken word, split where two words are > `WORD_SPLIT_GAP_S` (2 s) apart, no word times = the segment as before |
| Source wait in turns (28/10) | `campaign_batch._run_rotation`: a campaign that pauses with `campaign.WAITING_SOURCE` + `resume_after` (`_source_wait`) rests alone — row `pending` + `wait_until = max(resume_after, now+SOURCE_WAIT_MIN)`, `_next_turn` skips it, the other links go on; all remaining links waiting → earliest row `paused`, queue paused (desktop auto-resume as before); a fresh pass with nothing selectable only because of waits clears them (Chạy/Tiếp tục = try now); own Chạy pops `wait_until`. Douyin `filter_reason == status_deleted` → reason `video_gone` → `browser_download._gone_ending` = red failure of that video (message must not start with `WAITING_SOURCE` nor hold pause keywords), never a wait |
| Campaigns in turns | owner 23/10: queue `order: rotate` (`campaign_batch.ROTATE`, `next_index`, `_run_rotation`): one video per campaign per turn via `campaign._run_campaign_locked(max_videos=1, verify_done=first turn only)` → result `turn_over`, manifest paused + `ROTATION_WAIT`; joins while running = `join-*.json` (`add_to_rotation`, worker is the only queue writer). A campaign running on its own hands over between videos when `ROTATION_REQUEST` exists (ignored under a supervisor); `CampaignBatchController.join` / `complete_handoff` (queue `handoff_from`, starts once that campaign's video is done). Serial queues have no `order` key and are unchanged |
| Voice/content learning | `voice_learning*` `voice_profiles` `video_content*` `learning_download` |
| Drive | `google_drive*` `drive_*` |
| Facebook accounts/browser | `facebook_*` `desktop_browser` `channel_*` |
| Input notices (28/10) | `page_ai_ui.PageAIPanel.clock_text` (9:30 → 09:30 before `page_ai._check_slots`), `save_notice` prefixed to the panel status by `refresh()`; `campaign_ui.discover_notice` on the red `notice` line, re-applied by `display()`. Refusals of an owner's input must stay visible: a periodic refresh must not overwrite them |
| Page publishing | `page_schedule*` (store, recovery, removal, new attempt), `facebook_web_post` (publisher), `page_browser_reconcile`, `page_publish_settings`, `page_activity`, `page_status`. The actor switch is `facebook_page_identity.ensure_page_actor` through the account menu (`PROFILE_NAMES`; 24/10: the button reads "Account, your profile", its dialog "Your profile", then "See all profiles" → "Select profile", ", currently selected"); without it a plain-text composer author is never accepted. 26/10: reads around the switch survive Facebook's reload (`_steady`, `performance.timeOrigin`, `SWITCH_RELOAD_SECONDS`), `_can_switch` gives each tab `TAB_CHECK_SECONDS` via `open_editor` (crashed skipped, no answer refuses), the publisher closes only its own finished attempt tabs at the next prepare (`_post_tabs`, `_close_finished_tabs`, `FINISHED_TAB_SECONDS`), and `facebook_ui` shows `STALLED_MESSAGE` when a started request outlived the watchdog |
| AI planning | `page_ai*` `page_content_*` `codex_chat` |
| Channel autopilot | `channel_autopilot*` — Douyin channel → campaigns → Page AI sources (review mode only); see `SPEC-channel-autopilot.md` |
| Fixed posting times | `page_ai.fixed_slots` + panel fields: owner picks first time/gap, AI writes captions only (validated) |
| Auto mode from campaigns | `page_ai.auto_slots` + `PageAIController.auto_tick`; policy key `auto_campaign` (owner opt-in per Page: caption + auto-approve + auto-post) |
| Per-video posting times (28/10) | optional `slot_times` (panel checkbox "Đặt giờ riêng cho từng video"), mutually exclusive with `slot_start`/`slot_gap`; `fixed_slots`/`_owner_times` use the list directly, `auto_slots` dispatches to `_auto_slots_custom`: a time already passed today is owed and filled `AUTO_LEAD_SECONDS` after the video is ready (whole seconds — `math.ceil`, never a sub-second stamp, or the `isoformat(timespec='seconds')` round-trip in `context`/`validate_plan` refuses the plan), owed times spaced `AUTO_DEBT_GAP_SECONDS` (900s) apart |
| Page AI panel flags | `page_ai_ui.HIDE_AI_SCHEDULE_CONTROLS` (19/10) and `HIDE_STRATEGY_LINK` (23/10, strategy chat row under the goal) hide owner-unused controls; code, stored values and the Kế hoạch tab link stay |
| Video caption style | `page_ai.CAPTION_STYLE` (owner 23/10: 8–20 words, hook first, natural voice, no engagement bait, goals win) in `make_prompt`; `_dialogue_samples` spreads each video's dialogue over start/middle/end (`DIALOGUE_*`) |
| Reel caption editor | `facebook_web_post._caption_editor`: same visibility as `COMPOSER_SCRIPT` (ignores aria-hidden/inert layers) so the filled editor is the verified one; stops list candidates via `EDITOR_DETAIL` |
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
   (Owner 26/10: the publisher may close tabs it opened itself for a finished attempt; never a tab
   showing an editor, one that does not answer, or one Tèo did not open.)
5. **No live side effects without explicit user approval in this session**: no real Facebook
   action, AI (Codex) call, Meta API call, schedule arming, or user-data edit. Tests use temp stores
   and offline Playwright fixtures; never point a test at the production data path.
6. **Before/after preservation check** around any deploy: settings, schedule/post/event tables and
   campaign file hashes unchanged (pattern: `work/*/audit_data.py`, `before-data.json`).
7. **No secrets handling**: never read cookies, passwords or SMS codes; never copy Chrome profiles;
   Page tokens stay DPAPI-encrypted under `credentials/`.
8. **AI calls**: Codex CLI on the user's ChatGPT login (no paid API). No automatic model fallback or
   retry; at most one AI run per Page per day. One owner-approved exception (22/10): when at most
   `codex_translation.REPAIR_LIMIT` (3) lines of a translation batch come back empty/too long/Chinese,
   one follow-up request for only those lines; still unusable → stop as before. Whisper: no silent
   GPU→CPU fallback mid-job.

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
| `facebook_web_post.py` ~2,440 lines, most fixes land here as Facebook's UI changes (26/10 fix stayed in scope, trigger still open) | Next publisher fix: extract DOM readers/result parsing behind the adapter, test via fixtures |
| `page_browser_reconcile`, `page_schedule_recovery`, `page_schedule_new_attempt` open the schedule DB directly | When a 4th module needs it: move into one schedule-store API |
| `schedules.finished_at` means both "published" and "sent once, unverified" (`submitted`); guards that read it alone misfire (16/10 new-attempt bug) | Next guard touching `finished_at`: read the state/removal record, not the timestamp |
| Meta Graph API path stalled at Meta phone verification | User finishes verification → prefer API over browser for posting |
| README/HANDOFF too large to load | Keep CLAUDE.md as the entry point; archive HANDOFF sections older than 14 days |
| `campaign_batch._requests` orders join/leave files by `(st_mtime_ns, name)`: two written in the same clock tick tie and `join-` sorts before `leave-`, so an earlier stop wins over a later run; `test_the_later_of_stop_and_run_wins` fails ~50% (found 24/10; harmless by hand) | Next `campaign_batch` change: give each request a sequence number and order by it |

## Ask before doing
- **Never edit `teodub/` while a job or campaign runs** (check the campaign status / `execution/worker.lock`).
  Workers import modules lazily from disk, so one process can mix old and new code: on 21/10 an edit made
  video 7 fail mid-render (`cannot import name 'SUBTITLE_BOXES'`). Ask the owner to press Dừng first, or wait.
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
