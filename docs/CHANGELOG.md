# Change Log

Device configuration changes. For Pi details, see [archive/NOMADPI-DETAILS.md](archive/NOMADPI-DETAILS.md). For mini PC setup, see [MINIPC-SETUP.md](MINIPC-SETUP.md).

## 2026-07-02 - Download-naming Phase 3: canonical Artist - Title everywhere (v0.53.0)

**Why:** P1/P2 built the canonical media-identity store + download renaming; P3 surfaces it in
the UI and flips the rotation display order to a consistent `Artist - Title`.

**What (frontend — takes effect on browser refresh; backend bits need a restart):**
- **Rotation `Artist - Title` flip (req C):** rotation-search row builders (KN, local, divebar) now
  write `song_artist` as `Artist - Title` (was `Title - Artist`). Backend consumers flipped to match:
  `_resolve_sms_target` fallback split and the `song_artist_fallback` in download-and-link. Existing
  rows' `song_artist` display is verbatim (cosmetic order only); the SMS fallback is rarely hit
  (structured request fields win).
- **Available Songs canonical names + review/edit:** `list_items` now joins `media_library` so rows
  show canonical `Artist - Title` (not the raw filename) + `source`/`needs_review`. New inline ✎ editor
  (Artist + Title) posts to **`POST /media/metadata`** (marks the row user-confirmed:
  `parse_method='manual'`, `needs_review=0`, recomputes `*_norm`). A **"Needs review"** filter button
  shows only auto-parsed rows awaiting confirmation; an amber `review` tag marks them.

## 2026-07-02 - Divebar status dot fix + unified header toolbar buttons (v0.52.1)

**Why:** The "Search Divebar Karaoke" status dot stayed grey even when the catalog
modal reported "Fully synced". Separately, the Rotation and Overlays header toolbar
buttons had drifted apart visually (each button class was authored in a different PR).

**What (frontend — takes effect on browser refresh after `git pull`; no restart):**
- **Status dot fix:** `updateDbHealthDot()` set the dot's class to `green`/`yellow`/`red`,
  but the CSS only defines `.yt-dot-ok`/`.yt-dot-warn`/`.yt-dot-error`. Those tokens matched
  no rule, so the dot always fell back to the base `#444` grey — it had never shown a colour
  since it was added. Fixed the class names to match the CSS (the sibling YouTube health dot
  was already correct). The dot now goes green at ≥95% GCS-mirror sync.
- **Toolbar unification:** the Rotation buttons (Restore, Paths, Refresh, +Add, Requests,
  New Rotation) and Overlays buttons (Wallpaper, Backup, Restore, Scan to Sing, +Add) now share
  one font-size (0.75em), weight (500), padding (4px 10px), border-radius (6px), and neutral
  colour (#222 / #444 / #ccc) via a shared rule. Preserved as intentional/functional: the
  **New Rotation** red accent (it archives the whole night) and the **Paths** toggled-on blue
  active state.

## 2026-07-02 - Download-naming Phase 2: LLM parse + download renaming + dedup-skip (v0.52.0)

**Why:** Phase 1 stood up the canonical media-identity store; downloads still landed with messy,
inconsistent names and re-downloaded songs we already had. Phase 2 makes every *new* download
land with a canonical `Artist - Title [media_id]` name in a per-source folder, and skips
re-downloading files already on disk.

**What (backend — needs a service restart to take effect):**
- New karaoke-gen endpoint `POST /api/parse-karaoke-titles` (separate gen PR) turns messy
  filenames into `{artist, title, confidence}` via Vertex Gemini (fixes artist/title order, e.g.
  KaraFun-reversed). kjbox calls it via `GenClient.parse_titles`; **degrades gracefully offline**.
- Downloads (`download_video` / `download_from_url` / `download_cdg_pair`) now stage → gate →
  `_finalize_download_identity`: deterministic parse → best-effort LLM refine → write
  `Artist - Title [media_id].ext` into `downloads/{youtube,community,gen,uploads}/` → upsert
  `media_library`. Fixes the old gen `divebar__` mislabel (gen → `source=gen`).
- **Dedup-skip:** the four enqueue sites (`/download`, `/divebar/download`,
  `/rotation/download-and-link`, sing-approve) link an already-downloaded file instead of
  re-downloading when the prospective `media_id` is already present on disk.
- **`scripts/refine_titles.py`** batch-refines the existing `needs_review` backlog (DB-only,
  dry-run by default, offline-tolerant). Does NOT rename files (that is the Phase-4 migration).
- New config `parse_confidence_threshold` (0.75). Slug embeds `[media_id]` so `scan()` round-trips
  identity; YouTube dedup catches the existing backlog, community dedup is forward-looking.

## 2026-06-30 - Search-row dedup + unified renderer + redundant-download cleanup (v0.50.0)

**Why:** Searching the rotation "Link song" box for a song held in multiple forms (e.g.
*Maxïmo Park – Books from Boxes*) showed three rows that all play the same Nomad video, and one
row rendered with different fonts/pills/alignment. Root causes: a downloaded file surfaced both
as a `local` row and as a Karaoke Nerds "✓ Downloaded" row (no `local`↔`karaoke_nerds` dedup),
and the rotation search rendered KN vs local rows through two divergent templates.

**What (frontend — takes effect on browser refresh after `git pull`; no restart):**
- **Dedup:** `renderRotSearchDropdown` drops the redundant local row when a downloaded KN row
  already claims that exact on-disk path (`extractYouTubeId` → `downloadedIdToPath`), so a
  downloaded file shows once.
- **Unified rows:** new `renderRotRowHtml` skeleton (`.rs-row`/`.rs-main`/`.rs-title`/`.rs-sub`/
  `.rs-actions`); `renderRotLocalRow`/`renderRotKnRow`/`renderRotDivebarRow` delegate to it, so
  every row shares font size, title colour, and a fixed 170px action column (fmt/brand pills +
  buttons line up). The shared `.kn-track*`/`.kn-local-match` classes (also used by the main KN
  browse view) were left untouched.
- **Removed** the inline "Community" and "✓ Downloaded" pills — community shows via the section
  header + green left-accent; "Link" vs "DL & Link" already signals on-disk vs download.

**What (ops tooling — no deploy):**
- New `scripts/cleanup_redundant_downloads.py` (dry-run by default; `--execute` deletes verified
  litter and **quarantines** YT re-downloads of NOMAD masters; `--relink-twins` re-points
  rotation rows off a referenced twin onto its master first). Per-file safety: litter deleted
  only with a same-video-id completed playable; quarantine goes to `/opt/nomad/_redundant_quarantine`
  (a sibling of the download folder, outside `media_folders`, so rescan can't re-index it).
- **Executed on NomadPC 2026-06-30 (no show running):** 393 litter deleted, 95 twins
  quarantined (reversible), 14 rotation rows re-linked to masters; YTDownloads 1575 → 1087; 0
  broken links caused; media index clean. Backups: `rotation.db` / `media_index.json`
  `.bak-20260630-cleanup`. (Separately discovered 9 pre-existing broken `divebar__` rotation
  links, unrelated to this cleanup — see archive doc.)

See [archive/2026-06-30-search-dedup-row-unification-cleanup.md](archive/2026-06-30-search-dedup-row-unification-cleanup.md).

## 2026-06-30 - Feature: "Last sang" time in Singers list (v0.49.0)

**Code (v0.49.0 — frontend takes effect on browser refresh after `git pull`; backend change needs deploy + restart):**
- The Singers list already showed "Joined: Xh Xm ago" and a "Sung: ×N" pill per singer.
  It now also shows "Last sang: Xh Xm ago" immediately after the Sung pill, using the same
  compact elapsed-time format as the rotation count pill (e.g. "30m ago", "1h15m ago").
- Backend: new `_add_last_sang_to_singer_stats()` helper in `routes.py` enriches
  `singer_stats` dicts with `last_sang_minutes` at all three response sites
  (`_singer_action_response`, GET `/rotation`, redo/undo endpoint). Mirrors
  `_add_last_sang()` for rotation entries.
- Frontend: `buildSingerRow()` in `app.js` renders the label when
  `last_sang_minutes` is present; hidden when the singer has not yet sung tonight.

## 2026-06-30 - Preview cache relocated out of the download folder + content-addressed (v0.47.0)

**Why:** The "Available Downloads" (YTDOWNLOADS) list showed phantom `graphics` / `audio`
rows. They were browser-preview cache artifacts (extracted CDG halves): the preview cache
defaulted to `<download_folder>/.preview-cache`, *inside* the indexed download folder, so
`MediaIndex.scan()` indexed them as downloads. Separately, local-file cache keys were
`realpath|size|mtime`, so renaming/moving a source file orphaned its cached preview.

**What:**
- Preview cache now resolves to a **sibling** of the download folder
  (`/opt/nomad/preview-cache` on device) via `config.resolve_preview_cache_dir()` — outside
  every indexed path, so artifacts can never be indexed again.
- Local-file cache keys are **content-addressed**: `sha1(size + sha1(head 1MiB) + sha1(tail
  1MiB))` (`preview_cache.content_signature`), surviving renames/moves at ~2 MiB read cost.
  `PARAMS_VERSION` bumped `1`→`2`. GCS/divebar keying (by `file_id`) unchanged.
- `MediaIndex.scan()` also prunes the resolved preview-cache dir defensively.
- **Deploy:** one-time `rm -rf /opt/nomad/YTDownloads/.preview-cache` (old cache, 1 day old,
  regenerable); push; `systemctl restart kj-controller`. See
  [archive/2026-06-30-preview-cache-relocation-design.md](archive/2026-06-30-preview-cache-relocation-design.md).

## 2026-06-29 - Divebar GCS-mirror downloads land + CDG renders on mpv (v0.42.0)

**Why:** Selecting a GCS community-mirror (Divebar) version to download/link/play failed
for CDG zips with a "Download failed" toast, and even once downloaded a CDG zip played
audio-only (no graphics) on the default mpv renderer. The GCS mirror is mostly `mp4` +
`zip` (CDG+MP3), so both bugs hit the common case.

**What:**
- **Download extension (`utils.divebar_ext`)** — the on-disk extension was hardcoded `.mp4`
  at all three divebar enqueue sites (`divebar_download`, `download_and_link_rotation`,
  `approve_sing_request`), so a CDG zip landed as `…​.mp4`, was classified `video`, failed
  the ffprobe gate, and was deleted. Now derived server-side from the resolved download URL
  path (the GCS mirror always carries the real extension), falling back to the frontend-
  threaded catalog `format`, then `.mp4`. Frontend now threads `format` through the divebar
  download payloads (`app.js`, `static-sing/sing.js`). No `_gate_playable` change needed —
  a correctly-named `.zip` already validates via the existing `cdg_zip` path.
- **CDG graphics on mpv** — `/play` now feeds each renderer correctly: VLC gets the `.mp3`
  (auto-discovers the sibling `.cdg`); **mpv gets the `.cdg`** (graphics) with the `.mp3`
  attached as an external audio track via `audio-add <mp3> select` (after `loadfile <cdg>`).
  `audio-add` is used rather than a `loadfile` option because the `loadfile` options-arg
  position changed between mpv 0.37 (device) and 0.38+; `audio-add` is version-stable. mpv
  playback **aborts** if the audio fails to attach (a bare `.cdg` is silent). New
  `ZipPlayback.current_cdg_path()`; `audio_file` threaded through the `KaraokePlayer`
  protocol (`PlaybackCoordinator.play_video` → `play`), accepted+ignored by VLC.
- **Verified live on NomadPC** (mpv 0.37): repro CKK CDG zip downloads as `.zip`
  (`overall_ok: True`), and plays graphics + synced audio on **both** renderers
  (track-list `video=cdgraphics` + external `audio=mp3`, time-pos advancing, HDMI screenshot).
- Builds on the search-surfacing work in #114/#115 (GCS mirror rows in rotation search).
  Out of scope: bare `.cdg` (no audio) / bare `.mp3` (no video) catalog entries.

**Deploy steps**
- Backend change ⇒ **service restart required** (`git pull` + `sudo systemctl restart
  kj-controller`; interrupts active playback). kj-autodeploy is OFF. Already deployed to
  NomadPC (commit `9c656ac`, v0.42.0) and reverted to the persisted `render_mode = vlc`.

## 2026-06-28 - Ops: Full-library playability run launched on device + harness committed

**Why:** With the playability checker shipped+deployed (v0.40.0), sweep the *entire*
karaoke library once to find corrupt / unplayable files to review and delete. Internal
storage first (smaller, more diverse, higher corruption risk — fresh YouTube pulls + the
period divebar downloads were truncating), then the 4TB SSD archive. Must be monitorable,
resumable, gentle on the SSD, thermally safe, and pausable around live events.

**What:**
- **`kj-controller/scripts/playability-run/`** — committed the operational harness
  (`start.sh`, `pause.sh`, `run_all.sh`, `ssd_runner.py`, `monitor.sh`, `progress.sh`,
  `report.sh`), deployed to `/opt/nomad/playability-run/` on the device.
- **Phase A** (internal: YTDownloads + MP4-720p, ~2,485 mp4) — deep render in **both VLC
  and mpv** (the matrix), ~16–18 h. **Phase B** (4TB SSD: HyperMule ~398k CDG zips +
  NomadKaraoke ~1,982 mp4) — integrity-only, no render, gentle, ~20 days active, pausable.
- Resumable JSONL manifests (skip checked files by mtime/size, durable append-per-file);
  transient systemd units running the batch as **`nomad`** with `Nice=19` +
  `IOSchedulingClass=idle` + `CPUQuota=200%` + `MemoryMax=2G` + 0.3 s/file throttle;
  `monitor.sh` logs temp/load every 5 min and hard-stops at 92 °C.
- **⚠️ Gotcha baked into `start.sh`:** the batch MUST run as `nomad` — VLC refuses to run
  as root ("cannot be run by non-trusted users") and would falsely flag every video.
- **Runbook:** [PLAYABILITY-FULL-LIBRARY-RUN.md](PLAYABILITY-FULL-LIBRARY-RUN.md) — full
  check / pause / resume / read-results / reinstall instructions for future sessions.

## 2026-06-28 - Feature: Playability tier-2 async render verification + frontend — shipped + deployed (v0.40.0, PR #112)

**Why:** Tier-1 (the inline gate) hard-blocks on integrity+decode but skips the expensive
render proof. A file can pass the gate yet still fail to render video in the *active* renderer
(e.g. an odd codec under mpv). Tier-2 catches that off the request path so the KJ sees a warning
before hitting play.

**What:**
- **`rotation_store.py`** — new `playability_warning` column + `set_playability_warning(id, reason)`
  setter (deliberately does NOT bump `updated_at` — that feeds the "last sang" pill).
- **`routes.py`** — single-worker queue + daemon worker. After a file passes the tier-1 gate and
  is linked (`/rotation/link`), a background `check(path, renderers=(active,), depth="deep")` runs
  against `current_app.vlc.render_mode`; on failure it stamps the entry's `playability_warning`,
  on success clears any stale one. Best-effort — never affects the already-successful link;
  pure-audio files are skipped. One worker ⇒ one off-screen Xvfb render at a time.
- **`playability_render.py`** — `XvfbDisplay` now auto-picks a *free* display (`pick_free_display`
  probes sockets + lock files) instead of a hard-coded `:99`, so a tier-2 check and a manual batch
  sweep can't collide. Explicit displays still honoured.
- **Frontend (`static/app.js` + `style.css`)** — rotation rows with a `playability_warning` show a
  ⚠️ next to the song (reason on hover; click for a full toast). A tier-1 422 reject on
  link/upload now pops a prominent, auto-dismissing toast naming the reason, instead of only a
  fleeting log line.

**Tests:** `test_playability_tier2.py` (worker logic + enqueue + plumbing), `test_link_gate.py`
(enqueue-on-link / not-on-block), `test_playability_render.py` (free-display picker),
`test_rotation_store.py::TestPlayabilityWarning`. Full unit+integration suite green (1 expected
Xvfb skip). Frontend syntax-checked; visual verification deferred to on-device.

**Deploy notes (NOT yet deployed):** backend change → needs `kj-controller` restart (interrupts
playback); same `xvfb`+`Pillow` device deps as tier-1. Still-open follow-ups in the handoff:
tier-2 background concurrency is serialized (single worker) — if it's ever parallelised, the
free-display picker already covers Xvfb collisions; download-auto-link paths don't yet enqueue
tier-2 (only `/rotation/link` does).

## 2026-06-28 - Feature: Playability checker (verify render before play) — built + validated, NOT yet deployed

**Why:** On 2026-06-25 a queued file (`Mirah - Gone Sugaring`) "played" but showed a black
screen — a truncated download (`moov atom not found`). Audio-but-no-video failures are also
invisible in logs (the song finishes normally). New checker verifies a file actually *renders
video* before it can be linked/uploaded/downloaded.

**What (new modules in `kj-controller/`):** `frame_analysis.py`, `playability.py`,
`playability_render.py`, `playability_batch.py` (see ARCHITECTURE.md § Module Structure).
`PlayabilityChecker.check()` runs ffprobe integrity → ffmpeg decode → **off-screen Xvfb render
proof in VLC + mpv** (never touches the live `:0` display or `hw:0,0` audio) → verdict.
Supports CDG zips. A resumable batch tool produces a VLC-vs-mpv playability matrix.

**Gates (inline, tier-1):** `/rotation/link`, `/upload`, and `media.py` downloads now run a
fast integrity+decode check and **hard-block** unplayable files (422 / reject+delete). Tier-2
(async full render verification flagging linked entries) is now built — see the tier-2 entry above.

**Validated on NomadPC** against all 77 files from the 2026-06-25 show: caught the truncated +
an audio-only file; found and fixed a CDG false-positive (was capturing the black CD+G intro —
now seeks mid-file); confirmed **mpv can't render CD+G** (mpv-primary switch must keep CDG on
VLC). Timing: integrity ~0.15s, decode 2–21s, VLC render ~3.5–7s.

**Deploy notes (NOT yet deployed):** backend change → needs `kj-controller` restart (interrupts
playback). Requires **`xvfb`** (system pkg) + **`Pillow`** (venv) on the device — installed on
NomadPC, verify on NomadPi. Design/plan/findings/handoff in `docs/archive/2026-06-2*-playability-*`.

## 2026-06-25 - Feature: "time since last sang" on the rotation count pill (v0.39.0)

**Code (v0.39.0 — frontend takes effect on browser refresh after `git pull`; backend field needs deploy + restart to populate):**
- The rotation count pill showed how many songs each singer has sung tonight
  (`×4` / `NEW`) but not *how recently* — which matters as much when reordering
  to keep things fair. A `×4` who just sang is very different from a `×4` who
  hasn't had the mic in over an hour.
- The pill now appends a compact, dimmer "· <elapsed>" (e.g. `×4 · 1h15m`,
  `×1 · 22m`). `NEW` singers stay clean (nothing to show yet).
- Backend: a new `done_at` column is stamped only when an entry transitions to
  Done (and never touched by reorders/edits, unlike `updated_at`, so the time
  stays accurate after the rotation is shuffled). `rotation_store.get_last_sang_times()`
  returns minutes since each singer's most recent `done_at`, mirroring
  `get_songs_sung_counts` (done-only, case-insensitive, credits each member of
  a duet). `routes._add_last_sang()` attaches `last_sang_minutes` per entry via
  the single `_decorate_rotation_entries` helper. Multi-singer entries surface
  the longest wait. Elapsed time is computed server-side (device localtime) so
  there's no device-vs-browser timezone drift.
- **Until the service is restarted**, the backend serves the old code without
  `last_sang_minutes`; the frontend degrades gracefully (shows the count pill
  only, no time).

## 2026-06-12 - Fix: MAKE-request approval stuck pending + duplicate gen jobs (v0.38.1)

**Code (v0.38.1 — needs deploy + restart to take effect):**
- Bug: approving a "made for you" (MAKE) sing-request submitted a karaoke-gen job
  but left the request stuck `pending`, and re-clicking Approve spawned a
  **duplicate** gen job. Root cause: gen's `/api/audio-search/search?auto_download`
  creates its job *before* the flaky audio search; on a transient search failure
  (`404 no_results`, expired Dropbox creds) it returned 4xx/5xx, so kjbox's
  `approve_sing_request` make-branch raised *after* `add_entry` but *before*
  `mark_approved` → request stayed pending, orphan entry, no dedup.
- Fix: the make-branch now **always succeeds** — the singer is queued; if gen
  can't start the job the entry is left UNLINKED with a `"Being Made (!)"`
  status (KJ starts generation later via the rotation row's make button). The
  request is always marked approved, so re-clicking returns 409 — **no duplicate
  gen jobs**. (`kj-controller/routes.py`)

**Deploy steps (manual; kj-autodeploy is OFF):**
- `git pull` on NomadPC, then `sudo systemctl restart kj-controller` (backend
  change — interrupts active playback).

## 2026-06-11 - SMS: Telnyx 10DLC campaign + inbound webhook (delivery receipts + STOP)

**Telnyx account config (done via API, no deploy needed):**
- Created a `LOW_VOLUME` 10DLC campaign (`campaignId 4b30019e-b816-36a3-5727-0074e6af09bb`)
  against the verified "Nomad Karaoke" brand and set a $5/day messaging-profile
  spend cap. Number `+18038053750` will be assigned once the campaign is
  carrier-approved (~1-3 weeks). See `archive/2026-05-01-sms-notifications-design.md`.
- **Carrier hard-rejects unregistered sends** (`40010`) until then — no US delivery
  is possible, even self-tests.

**Code (v0.37.0 — needs deploy + restart to take effect):**
- New inbound webhook `POST /sing/telnyx/webhook` (`sing.py`), Ed25519
  signature-verified against `TELNYX_PUBLIC_KEY`. Handles delivery receipts
  (updates `sms_log.status` → delivered/delivery_failed) and inbound STOP/START
  (opt-out registry in `sms_store`). The KJ send path now refuses opted-out
  numbers (403).
- New dep: `cryptography` (already present transitively via pywebpush).

**Deploy steps (manual; kj-autodeploy is OFF):**
- `git pull` on NomadPC, then `sudo systemctl restart kj-controller` (interrupts playback).
- Point Telnyx at the webhook (only after deploy):
  `curl -X PATCH -H "Authorization: Bearer $TELNYX_API_KEY" -H "Content-Type: application/json" \`
  `-d '{"webhook_url":"https://sing.nomadkaraoke.com/telnyx/webhook"}' \`
  `https://api.telnyx.com/v2/messaging_profiles/40019e4e-d369-4bd2-b3bf-2ec80e0825f2`
- End-to-end webhook verification isn't possible until the 10DLC campaign is
  approved and real messages flow.

## 2026-06-11 - Overlay renderer v2: retire conky, single GTK transparent overlay

Replaced conky and the pygame-ce overlay engine with a single compositor-backed
**GTK3 + Cairo** overlay window (`desktop/overlay_engine.py` + `overlay_painters.py`
+ `rotation_source.py`). The window is RGBA (real per-pixel transparency),
always-on-top, click-through, and never takes focus — so it cannot hide or
de-stack the fullscreen VLC video (root cause of the recurring "video
backgrounded" incident, where the full-screen conky `dock` window rose above the
demoted fullscreen VLC). The rotation home screen is now the `rotation_list`
overlay type drawn over the desktop wallpaper; the `source='rotation'` ticker
composes its text directly from `/tmp/rotation_cache.json` (the push-based
`rotation_ticker_sync` is retired). A compositor guard refuses to map the window
if no compositor is running.

**Device deploy steps (manual; kj-autodeploy is OFF):**
- `git pull` on NomadPC
- disable the conky autostart (`~/.config/autostart/Conky.desktop`) and kill conky
- install the updated `overlay-display.service` (now GTK/X11; daemon-reload), restart it
- the desktop wallpaper (`/home/nomad/kjdata/wallpaper.jpg`) is unchanged and remains
  the between-songs background

kj-controller 0.35.1 -> 0.36.0.

## 2026-06-04 - Fix Scan-to-Sing QR overlay flicker

The Scan-to-Sing QR overlay flickered instead of displaying solidly whenever a
ticker overlay was also visible over video. The overlay engine's render loop
(`desktop/overlay_engine.py`) called `_restack_qr_above_ticker()` from
`update_visibility()` every frame (30 FPS); that helper destroys and recreates
each QR window to fix Z-order, so running it per-frame tore down and rebuilt the
QR window 30×/second. Fixed by only restacking when an overlay's visibility
actually changes that frame (config-change restacks are still handled by
`_reload_config()`). kj-controller 0.34.0 → 0.34.1.

## 2026-06-04 - Robust server-side rotation undo/redo

Fixes the 2026-05-28 incident where a KJ clicked **Undo** mid-show and lost
rotation history. The old undo was a per-browser snapshot stack that POSTed a
whole-rotation overwrite to `/rotation/restore`; it was blind to singer
self-submissions and other devices, survived a mid-show restart while going
stale, and reset every `created_at`. One click silently clobbered concurrent
changes. See `docs/archive/2026-06-04-server-side-undo-design.md`.

Undo/redo is now **server-side and shared across all KJ devices**:

- New `rotation_history` table + a monotonic `rotation_meta.rotation_rev`
  counter (bumped on every mutation). `RotationManager` checkpoints before each
  *meaningful* edit (add/edit/delete/move/status/paid/singer ops/link); noisy
  background tracking (download/gen status) is excluded.
- New `POST /rotation/undo` and `/rotation/redo` are **two-phase**: without
  `confirm` they return a preview diff (removed / added / changed) and apply
  nothing; with `confirm: true` they apply. The apply is **revision-guarded**
  (`expected_rev`) so a change between preview and confirm is rejected as
  `stale` and the KJ re-previews the real diff instead of applying a stale one.
- `restore_entries` now **preserves `created_at`** (no more timeline corruption)
  and, on undo, **preserves live file-link/download fields** so an unrelated
  undo never breaks a download that completed in the background.
- Archiving a night clears the undo history (session-scoped).
- `GET /rotation` now returns `rev` + `history` so the undo/redo buttons reflect
  the shared server state on every 2s poll. The legacy `/rotation/restore`
  snapshot path is retained only for Google-Sheet emergency recovery.

## 2026-06-02 - Fix: legacy Deflate64 CDG zips failed to play

Some older karaoke discs (e.g. "MP3+G Toolz .NET" authored zips like the `KST` Spanish collection) compress their `.cdg`/`.mp3` with **Deflate64** (compression method 9). Python's stdlib `zipfile` only supports STORED/DEFLATE, so `ZipPlayback.extract_and_get_mp3` raised an uncaught `NotImplementedError` and the `/play` request 500'd — the song silently failed to play while standard-deflate zips worked fine.

`zip_playback.py` now catches `NotImplementedError` from `extractall` and falls back to the system `unzip` binary (which handles Deflate64). Path-traversal validation still runs first via `zipfile.namelist()`; the unzip fallback returns `None` gracefully (with a logged error) if `unzip` is missing or exits non-zero. Bumped kj-controller to `0.33.1`.

## 2026-05-28 - Overlay system: rotation ticker + Scan-to-Sing preset

- Ticker overlays gained a `source: rotation` mode whose text is composed by the backend on every rotation mutation. New `prefix`, `count`, `separator`, `empty_text` config fields. The engine stays a dumb renderer of `config.text`.
- New `POST /overlays/presets/scan-to-sing` plus a "Scan to Sing" button in the overlay panel creates a small QR overlay (top-right, `follow_event_url=True`, `show_over_video=True`) ready for singers to scan.
- QR overlays gained `bg_opacity` (semi-transparent padding) and `corner_radius` (rounded card) for better appearance over video and ticker.
- Overlay engine restacks QR windows above any visible ticker by destroying+recreating the QR window after every reload / visibility change, so the QR reliably sits on top of an always-on-top ticker bar.
- Design + implementation plan: `docs/archive/2026-05-28-overlays-ticker-qr-design.md` and `docs/archive/2026-05-28-overlays-ticker-qr-plan.md`.

## 2026-05-28 - Feature: Simple KJ Mode (stand-in operator UI)

A toggle in the KJ controller's System section that shrinks both the singer SPA and the KJ UI to a "good enough" surface area for a novice stand-in. The singer side restricts requests to `local` / `divebar` / `kn` sources (no YouTube paste, no make-on-demand, no defer-to-KJ). The KJ side hides the right column (search panels, downloads, library browser), manual rotation entry, the overlay panel, and every System subsection except the Mode toggle itself. Designed for QR-only nights where the operator just approves → plays → marks done → announces.

**Persistent flag:** `kj_simple_mode` in `sing_meta` (default off). New `SingStore.is_simple_mode()` / `set_simple_mode()` mirror the existing `is_accepting_make_requests` pattern.

**API surface (no new routes):**
- `GET /rotation/requests/config` now returns `simple_mode`.
- `POST /rotation/requests/config` accepts `simple_mode`.
- `GET /status` carries `simple_mode` so the 2s heartbeat poll keeps the body class in lockstep with the server.
- `GET /sing/` template context includes `simple_mode` (forwarded as `data-simple-mode` on `#sing-root`).
- `GET /sing/search` response includes `simple_mode` so the singer SPA stays in sync mid-session.
- `POST /sing/submit` narrows the source allowlist to `{local, divebar, kn}` when the flag is on (returns 400 `simple_mode_disabled_source` otherwise) — defence-in-depth for stale singer PWAs.

**KJ UI (CSS-driven):** `app.js` sets `body.simple-mode` from each `/status` poll. A single CSS block in `style.css` hides panels, the rotation manual-add controls, the now-playing pitch buttons, and every `system-subsection:not(#kj-mode-section)`. A guidance banner above the rotation list reads "Simple Mode is ON · Approve incoming requests → tap a row to play → mark done → announce next singer."

**Singer SPA (vanilla JS):** Empty-state triage cards (paste YT / ask KJ / DIY-via-gen) are suppressed in favour of a single instructional message. Multi-version songs auto-expand to show the versions list directly (no `kj_pick` shortcut). Confirm-screen subtitle adapts. The submit-error handler shows a "Refresh the page" hint if a stale PWA hits the server's `simple_mode_disabled_source` 400.

**Tests:** 11 new automated tests (3 unit, 4 integration, 4 E2E). Manual smoke runbook added to `docs/TESTING.md` covering KJ-side toggle behaviour, singer SPA changes, server-side allowlist via curl, stale-PWA recovery copy, toggle isolation, and pre-existing pending requests.

**Spec:** [docs/archive/2026-05-22-simple-kj-mode-design.md](archive/2026-05-22-simple-kj-mode-design.md)
**Plan:** [docs/archive/2026-05-28-simple-kj-mode-plan.md](archive/2026-05-28-simple-kj-mode-plan.md)

## 2026-05-22 - Fix: Divebar GCS-mirror downloads use human-readable filenames

GCS-mirror divebar downloads were landing on disk as `divebar__divebar-{file_id}.mp4` — three enqueue paths dropped the artist/title metadata they already had. The smoking gun was `approve_sing_request` (sing-request approval), which hardcoded `title = f"divebar-{source_ref}.mp4"` despite `req.song_title` / `req.song_artist` being available on the request row.

**Backend changes:**
- `utils.build_divebar_filename(brand_code, artist, title)` — new pure helper. Returns `"WTF - Queen - Bohemian Rhapsody.mp4"` (or `"DB - …"` when brand_code is missing). Returns `None` when neither artist nor title is present, so callers can fall back to the old `divebar-{file_id}.mp4`.
- All three divebar enqueue sites in `routes.py` now call the helper with structured fields the call site already has:
  - `/divebar/download` (panel button) — accepts `{file_id, artist, title, brand_code}`.
  - `/rotation/download-and-link` (divebar branch) — same payload shape, replacing the previous client-supplied `filename`. Queue item also now carries `divebar_file_id`. YouTube branch unchanged.
  - `approve_sing_request` (divebar branch) — uses `req.song_artist` / `req.song_title`, and `brand_code` from `source_meta` when available (kj_pick path).

**UI changes:**
- `static/app.js` — Divebar search panel "Download" button (`downloadDivebarTrack`) and rotation-search divebar result now send `{file_id, artist, title, brand_code}` instead of a pre-built `filename`. Server is the single source of truth.

**One-off cleanup (live device):** Five existing `divebar__divebar-*.mp4` files on nomadpc renamed to `divebar__DB - <artist> - <title>.mp4`, with matching updates to `rotation_entries.file_path` and `rotation_archive.file_path`. Rotation DB backed up to `~/kjdata/rotation.db.bak-divebar-rename-20260522`.

**Test changes:**
- 8 unit tests for `build_divebar_filename` (`tests/unit/test_utils.py::test_build_divebar_filename_*`).
- 3 integration tests for `/divebar/download` (`tests/integration/test_routes.py::TestDivebarDownloadFilename`).
- 3 integration tests for `/rotation/download-and-link` divebar branch (`tests/integration/test_download_link_routes.py::TestDownloadAndLinkDivebarFilename`).
- 2 integration tests for sing-request approval (`tests/integration/test_sing_admin_routes.py::TestApprove::test_approve_divebar_*`).

**Spec / plan:** `docs/archive/2026-05-22-divebar-filename-design.md`, `docs/archive/2026-05-22-divebar-filename-plan.md`.

## 2026-05-22 - Feature: Brand-priority ranking for kj_pick approval + rotation search

Surfaces the KJ's in-head version-quality rules ("community always beats commercial; CC > LC > FBK …") in the two places where version selection happens. Backend now annotates every karaoke version with a `priority_rank` integer; frontend reads it to drive the new UX. Data-informed: alias map and unlisted-brand defaults seeded from analysis of 104 approved requests + 10 diverse song searches on the live show 2026-05-22.

**New module `kj-controller/version_priority.py`:**
- Canonical brand registry with two ordered lists (community / commercial), each entry has `(canonical_code, aliases, display_name)`. Defaults include the KJ-stated top brands (CC, LC, FBK, BELLY, NOMAD, FAKEY, PMK, OBSK for community; KV, SC, SBI, SF, CB, ZM for commercial) plus high-frequency unlisted brands (SDK, DBK; VS, SK, MR, PT, EK) appended at the end so they outrank truly unknown brands.
- Aliases collapse cross-source naming inconsistencies: `LEMMY`/`LC`/`Lemmy Caution` → LC; `KVD`/`KCD`/`Karaoke Cloud Digitrax`/`Karafun` → KV; `ZOOM` → ZM; `CCK`/`CCX`/`CC Karaoke X` → CC.
- `resolve_brand(...)` accepts disc_id, filename, brand_code, brand_name, is_community and returns `(canonical, classification)`. Local disc_id parsed by alpha-prefix regex; YouTube-download filename pattern `VIDEOID__BrandName__rest.ext` recognized so OBSK/SK YouTube rips classify correctly.
- `rank_version(version, cfg)` returns a sortable int with tier-based gaps (community 0–999, unrecognized community 1000s, commercial 2000s, unrecognized commercial 3000s, unknown 4000s); source tiebreaker `local < divebar < youtube` within the same brand slot.
- Config overrides via two new keys: `kn_priority_community` + `kn_priority_commercial` (lists of canonical codes). Legacy single `kn_preferred_brands` field is ignored (left untouched for rollback safety).

**Backend wiring (`routes.py`):**
- `_group_search_results` annotates + sorts versions in the kj_pick snapshot.
- `unified_search` flat-return path annotates local results + KN tracks.
- `/karaoke-nerds/search` annotates + sorts per-song tracks.
- `/karaoke-nerds/config` rewritten: GET returns `{priority_community, priority_commercial, aliases}`, POST validates canonicals and rejects unknown codes with a 400 listing valid options.

**Frontend (`static/app.js` + `templates/index.html` + `static/style.css`):**
- kj_pick approval card renders a hero card for the best version with prominent green CTA + collapsed `<details>` for alternates (auto-open if every option is tier-unknown). Legacy 4-bucket sort retained as fallback for pre-deploy snapshots.
- Rotation Add/Link dropdown does a global priority sort across local + KN results, emits section headers when class changes (⭐ Best — CC (community), ── Community ──, ── Commercial ──, ── Unknown ──), highlights top community rows with a gold left-border + ⭐ icon + "Best" pill on row 0. Default-selects row 0 so Enter picks the best.
- KN search panel reads `priority_rank` from backend; legacy `sortKNTracks` and `knPreferredBrands` removed.
- KN prefs settings panel grows from one input to two textareas (community + commercial) with alias hints + Reset to defaults button.
- New CSS for `.pr-version-hero`, `.pr-picker-alternates`, `.rs-best-pill`, `.rs-top-star`, `.kn-section-header`, `.kn-prefs-textarea`.

**Tests:** 53 new unit tests in `test_version_priority.py` covering alias resolution, disc_id parsing, YT filename parsing, tier ranges, priority order, source tiebreaker, config override, is_community semantics. New `test_karaoke_nerds_config.py` covers the two-list endpoint. `test_search_grouping.py` and `test_rotation_search.py` extended with annotation assertions. Existing integration test for `/karaoke-nerds/config` updated for the new shape. Total: 1673 backend tests pass.

**Out of scope (separate PRs):**
- Tracking `original_source_type` on `sing_requests` so kj_pick approval history is preserved (currently the source_type gets overwritten on approve).
- Auto-approving kj_pick on the singer side when confidence is high.
- Brand badges on the rotation table itself.

Design spec: `docs/archive/2026-05-22-choose-best-version-design.md`. Implementation plan: `docs/archive/2026-05-22-choose-best-version-plan.md`.

## 2026-05-21 - Feature: Active-download source visibility (GCS / Drive / YouTube)

The "DOWNLOADING" prep badge previously only hinted at source via colour (orange=youtube, green=other), so the KJ couldn't tell whether an active divebar download was hitting the fast GCS community mirror or falling back to slow Google Drive. Both surfaces now show the source explicitly.

**Backend changes:**
- `divebar.classify_download_url(url)` — new helper. Returns `'gcs'` for `storage.googleapis.com` (or `*.storage.googleapis.com`), `'drive'` for `drive.google.com` / `drive.usercontent.google.com` / `*.googleusercontent.com`, else `None`.
- All three divebar enqueue sites stamp `source_detail` on the queue item: `/download` (divebar), `/download-and-link` (divebar), `approve_sing_request` (divebar branch).
- `/status` `rotation_downloads` map now includes `source` + `source_detail` per active download (alongside the existing `status` / `progress` / `file_path`). The UI uses the live in-flight snapshot rather than only the persisted `download_source` field.

**UI changes:**
- `static/app.js` — `downloadSourceBadge(item)` maps (source, source_detail) to a label + class. `renderDownloadQueue` prepends a coloured pill before the spinner: `GCS` (green), `DRIVE` (amber), `YT` (red), `DIVEBAR` (blue, fallback for old/unknown). Rotation prep-badge reads `lastRotationDownloads[id].source_detail` and renders `GCS DL` / `DRIVE DL` / `YT DL` instead of plain `DOWNLOADING`. Pre-classification entries fall back to the original label.
- `static/style.css` — new `.dl-source-*` and `.prep-downloading-{gcs,drive}` rules.
- Inline fix: `escapeHtml` on URL + label in the download-queue HTML template (singers' pasted YouTube URLs reach this code path).

**Test changes:**
- 8 unit tests in `tests/test_divebar.py::TestClassifyDownloadUrl` covering every host variant (path-style GCS, virtual-host GCS, Drive, usercontent, googleusercontent, unknown, empty, malformed).
- 2 integration tests in `test_download_link_routes.py` round-trip the `/rotation/download-and-link` divebar path with mocked GCS and Drive URLs, asserting the queue item's `source_detail`.
- 1 integration test in `test_routes.py` asserts the `/status` `rotation_downloads` payload carries `source` + `source_detail` for in-flight items.

## 2026-05-21 - Feature: YouTube-request preview + approve-link-later + rotation file-paths toggle

Three additions that close the loop on singer-pasted YouTube URLs not always being karaoke versions. (1) The KJ admin pending-request card now shows a thumbnail + clickable "▶ Watch on YouTube" link (singer-pasted URL is sanitised — `http(s)` only; `javascript:`/`data:` get a manual-review warning). (2) For `source_type=youtube` only, a second "Approve, link later" button creates the rotation entry **without** queuing a download — the KJ then uses the existing rotation 🔗 button to attach a proper file. (3) New "📁 Paths" toggle in the Rotation header reveals each entry's filename (full path on hover); unlinked entries flagged in amber so the KJ can scan for ones that still need relinking. Toggle state persists in `localStorage`.

**Backend changes:**
- `approve_sing_request(app, req, skip_download=False)` — new kwarg. When `True` for `youtube|kn|divebar`, creates the rotation entry via `rotation.add_entry(...)` with no `file_path` and skips the download queue. Default `False` preserves existing behaviour (auto-approve callsite unchanged).
- `POST /rotation/requests/<id>/approve` — accepts `{skip_download: true}` in JSON body and threads it through. Unknown body fields ignored.

**UI changes:**
- `static/app.js` — `SingRequests.renderYouTubePreview(url)` renders the thumbnail (`i.ytimg.com/vi/{id}/default.jpg`) + link block for `source_type=youtube` rows; `extractYouTubeId` reused from existing helper. `approve(id, opts)` signature now takes an options object (`{versionIndex, skipDownload}`) to keep the signature extensible. YouTube rows get a two-button approve area; other source types unchanged. New `toggleRotationFilePaths()` + persisted state, with file-path elements always rendered (CSS-toggled via `.show-file-paths` class on `#rotation-list`).
- `static/style.css` — `.pr-youtube-preview` (full-width wrap inside the flex pending-req-row), `.pr-actions .btn-approve-skip`, `.rotation-file-path` (hidden by default; flex-basis 100% when shown), `.rotation-paths-btn` + active state.
- `templates/index.html` — new "📁 Paths" button in `.rotation-header-btns` between Refresh and `+ Add`.

**Test changes:**
- `test_youtube_request_exposes_url_for_kj_preview` — regression guard that `/rotation/requests` returns `source_ref` for `source_type=youtube` requests (the UI's data dependency for the preview block).
- `test_approve_youtube_skip_download_creates_unlinked_entry` — verifies `skip_download=true` on a youtube request: download worker never invoked, queue stays empty, rotation entry has `file_path is None` and `download_status is None` (so the existing 🔗 link button auto-appears).

## 2026-05-15 - Feature: Singer duet partners + multi-song done screen

Two papercuts in the singer UI fixed. (1) Singers can attach up to 3 duet partners (name + optional phone) on the confirm screen — partners surface in the KJ admin approval card with `sms:` links, then ride through `approve_sing_request` into `rotation_entries.singers_json` (joined `"Alice & Sarah & Mike"` in the legacy `singer` column). (2) The post-submit "done" screen lists every song the singer has submitted this event with live status, and a "+ Request another song" button that preserves identity. See [design](archive/2026-05-15-singer-duets-and-multi-song-design.md) and [plan](archive/2026-05-15-singer-duets-and-multi-song-plan.md).

**Backend changes:**
- `sing_requests.additional_singers TEXT NULL` — new column, additive idempotent ALTER, JSON array of `{name, phone}`.
- `POST /sing/submit` — validates `additional_singers` (max 3, name required, phone optional).
- `approve_sing_request` — passes `singers=[primary, …partner_names]` to `rotation.add_entry(...)` in all source-type branches.
- `GET /sing/my-requests?ids=…` — new endpoint, multi-id status feed (max 20 ids), returns `{now_playing, requests:[{request, estimate?}]}`. Filters foreign-token rows for cross-event safety.

**UI changes:**
- `static-sing/sing.js` — partners section on `renderConfirm`, new multi-song `renderDone` + `pollMyRequests` (15s tick), "+ Request another song" button, rules-footer copy, localStorage `sing_my_request_ids` scoped per token, test bridge globals (`window.__sing_state` / `__sing_render`).
- `static/app.js` — duet-partners block on the admin approval card, partner phones rendered as `sms:` links.

**Test changes:**
- 5 new unit tests covering `additional_singers` round-trip + sentinel-preserve behaviour on `update_request`.
- 14 new integration tests across `/sing/submit` validation, `/sing/my-requests`, and `approve_sing_request` (local + youtube duet paths, solo-unchanged, all-blank-names defensive, auto-approve+duet, admin list endpoint exposes partners).
- 6 new e2e tests (Playwright) covering the confirm-screen partners section, partner-row removal, multi-song done screen, and "Request another song" button.

## 2026-04-27 - Feature: Singer full-rotation view on landing page

Singers visiting `sing.nomadkaraoke.com/?t=<token>` now see a "See full rotation (N singers)" expander between the now-playing widget and the "Request a song" CTA. On expand, the body shows every active rotation entry with position, first name, song/artist, and a rough wait estimate (`on now` / `up next` / `~low–high min`), preceded by a caveat that order can change (new singers get bumped, paid spots jump, times are rough).

Decision-useful for the QR-scanning visitor's first question — *"how long is the line?"* — without forcing them to submit a request to find out. See [design](archive/2026-04-27-singer-rotation-view-design.md) and [plan](archive/2026-04-27-singer-rotation-view-plan.md).

**Backend changes:**
- `wait_estimate.compute_all_estimates(entries, cfg)` — new helper that computes cumulative estimates for every active entry in one pass, asserting parity with `compute_estimate` for any single target.
- `GET /sing/rotation` — new token-gated route returning `{entries: [...], spread_source}`. Each entry has `position`, `first_name`, `song_artist`, `status`, `now_singing`, `expected_s`, `range_low_s`, `range_high_s`. Done/left entries filtered out.

**UI changes:**
- `static-sing/sing.js` — `renderRotationExpander()` rendered inside `renderLanding()`. Lazy-fetches on first expand, caches for 30s on `state.rotationCache` so back-from-search renders instantly. No live polling — singers wanting live updates submit and get the done-screen status flow.
- `static-sing/sing.css` — `.rotation-expander` + grid-row layout with mobile breakpoint at 380px.

**Test changes:**
- `tests/unit/test_wait_estimate.py` — 10 new cases for `compute_all_estimates` including a parity test against `compute_estimate`.
- `tests/integration/test_sing_rotation_route.py` — new file, 7 cases for the new endpoint.
- `tests/integration/test_host_guard.py::test_rotation_blocked` — updated to expect 403 (token-gated singer endpoint) instead of 404 on the public host. Same security guarantee — admin data does not leak.

**Modified files:**
- `kj-controller/wait_estimate.py`, `kj-controller/sing.py`
- `kj-controller/static-sing/sing.js`, `kj-controller/static-sing/sing.css`
- `kj-controller/pyproject.toml` — `0.27.0` → `0.28.0`.

## 2026-04-23 - Feature: Song selection UX — empty-state triage for punks (Phase C)

Final phase of the song-selection-ux overhaul (see [master plan](archive/2026-04-23-song-selection-ux-master-plan.md), [phase C design](archive/2026-04-23-song-selection-phase-c-design.md)).

When a punk searches for their niche local band's song and gets zero hits, the singer UI used to show two tiny `<details>` fallbacks buried at the bottom. Phase C replaces that with a deliberate three-card triage:

1. **Paste a YouTube link** — fastest, quality varies.
2. **Ask the KJ to make it tonight** — free, 20 min to 1 hour, may be declined. *Only shown when the KJ has enabled the toggle.*
3. **Make it yourself on gen.nomadkaraoke.com** — ~5 min if you do the lyrics review. Has an inline "How it works" explainer with the full 6-step recipe.

Order is ascending singer-effort. The singer picks based on how much work they're willing to do; the KJ loses no agency (they still review + approve).

**New KJ toggle: "Accept 'make it' requests tonight"** — stored as `sing_accept_make_requests` in `rotation_meta` (default on). KJ flips it off when they're too busy for same-night lyrics reviews.

**Backend changes:**
- `SingStore.is_accepting_make_requests()` / `.set_accepting_make_requests()`.
- `GET /rotation/requests/config` exposes `accept_make_requests` (bool).
- `POST /rotation/requests/config` accepts `accept_make_requests` updates (partial-POST preserved; other flags untouched).
- `GET /sing/search` response carries `make_requests_enabled` alongside `songs[]` so the singer UI can show/hide card 2 without a second round-trip.
- `GET /sing/` landing template forwards the flag on `#sing-root` dataset so the UI has it before the first search.
- `POST /sing/submit` with `source_type=make` returns 400 `make_requests_disabled` when the flag is off — defence-in-depth against stale clients that cached `sing.js` from before the toggle flipped.

**UI changes:**
- Singer side (`static-sing/sing.js`): new `renderEmptyStateTriage()` renders the three cards when `songs.length === 0` and query length ≥ 3. Card 2 conditionally rendered from `state.makeRequestsEnabled` (updated on every `/sing/search` response). The always-visible bottom `<details>` fallbacks for "Paste a YouTube link" / "Ask the KJ to make this one" are retired — their functionality moves into the triage cards.
- Card 2 submit goes through a `confirm()` with the time-cost caveat ("this can take 20 min to 1 hour, or may not be possible tonight. Sure?").
- Card 3's "Open gen.nomadkaraoke.com" is a standard `<a target="_blank" rel="noopener">` — no backend, no tracking.
- Admin side (`static/app.js`, `templates/index.html`): Requests settings modal gains a "Accept 'make it' requests tonight" checkbox in the same style as the existing auto-approve toggle. `toggleSingAcceptMake` wires it to `POST /rotation/requests/config`.

**Codebase audit confirmed:** a gen-published YouTube URL pasted into card 1 flows through the existing `source_type=youtube` path with no special handling — yt-dlp treats it like any other YouTube URL. Zero backend changes needed for the DIY path.

**Modified files:**
- `kj-controller/sing_store.py`, `kj-controller/sing.py`, `kj-controller/routes.py`
- `kj-controller/static-sing/sing.js`, `kj-controller/static-sing/sing.css`
- `kj-controller/static/app.js`, `kj-controller/templates/index.html`, `kj-controller/templates/sing.html`
- Tests: `tests/unit/test_sing_store.py` (3 new cases), `tests/integration/test_sing_admin_routes.py` (3 new cases in TestConfig), `tests/integration/test_sing_public_routes.py` (3 new cases across search + submit), `tests/integration/test_sing_make_request_disable_e2e.py` (new, 2 cases).
- `kj-controller/pyproject.toml` — `0.26.0` → `0.27.0`.

**Manual ops steps required (post-deploy):** none. Frontend + backend changes land together; restart kj-controller as normal. The default for `sing_accept_make_requests` is `"1"` (on) so existing events behave identically to before until the KJ flips the new toggle.

## 2026-04-23 - Feature: Song selection UX — per-version expander for nerds (Phase B)

Builds on Phase A's one-tile-per-song grouping. A nerd who wants to pick a specific version now taps "N versions available →" on any multi-version tile and gets an inline expander listing every candidate categorized by source, with the metadata the codebase actually has — brand, format, quality (where known), filepath for local/divebar. A first-time expanded singer sees a one-time "Commercial vs Community" explainer that dismisses permanently.

**Phase B design:** [archive/2026-04-23-song-selection-phase-b-design.md](archive/2026-04-23-song-selection-phase-b-design.md).

**UX:**
- Grouped-result card now uses a column layout: title + artist + "In our library" badge + big primary "Let the KJ pick the best version →" CTA, then a secondary "N versions available →" toggle. Tapping the toggle expands inline (no modal, no navigation).
- Expander sections in fixed order, empties omitted:
  - **In our library** — `source: "local"` → brand+format, filename, expandable full path.
  - **Community karaoke (in our library)** — `source: "kn"` with divebar.file_id → brand + divebar format/quality/size, expandable drive_path.
  - **Online only (download needed)** — `source: "kn"`, non-community, no divebar → brand + "Commercial · YouTube".
  - **Community (AI vocal removal)** — `source: "kn"`, community, no divebar → brand + "Community · YouTube".
- Each version card has its own "Pick this version →" button. Submission is **not** `kj_pick`; it's a direct `local` / `divebar` / `kn` request with the chosen version baked in, so auto-approve still works and the admin sees a normal one-tap Approve.
- Long filepaths (local.path, divebar.drive_path) live inside a `<details>` block so they're hidden by default ("show full path" chevron) and expand in a mono-font line-break block when the singer taps.
- Primary "Let the KJ pick" CTA continues to work after interacting with the expander. Re-tapping the toggle re-collapses.

**Commercial vs Community explainer:**
- Soft info callout above the version list on first expand. Two bullets (commercial = pro track, cover band, classic; community = original recording, AI vocal removal).
- Dismissed with "Got it" — `localStorage['sing_rules_commercial_community_seen'] = "1"` persists across tabs and reloads. Private browsing (where localStorage throws) silently falls back to "reshow every visit".

**Architectural notes:**
- All changes are client-side — backend contract is unchanged since Phase A already snapshots `versions[]` into `/sing/search`.
- Pure-rendering helpers added inside `renderSearch` closure (not extracted to a module — sing.js is 1108 LOC vs the "split at 1000 LOC" guidance, but the closure state coupling argues for keeping it inline this PR; extraction is a follow-up).
- `_humanFileSize`, `_versionSection`, `_ccExplainerSeen`, `_markCcExplainerSeen` hoisted to module scope as pure utilities.

**Modified files:**
- `kj-controller/static-sing/sing.js` — `renderVersionsExpander`, `renderVersionRow`, `pickSpecificVersion`, `toggleExpanded`, `dismissCcExplainer`. renderResults now emits a div-not-button with primary CTA + toggle + optional expander.
- `kj-controller/static-sing/sing.css` — `.sing-versions-toggle`, `.sing-version-expander`, `.sing-version-section`, `.sing-version-card`, `.sing-version-path`, `.sing-cc-explainer` styles. Rewrote `.result-row.grouped` to flex-column so the expander flows below.

**Version:** `0.25.0` → `0.26.0` (minor — new visible singer UX, no API break).

**Manual ops steps required (post-deploy):** none. Frontend-only; auto-deploy picks it up on next browser refresh.

**Not yet shipped (final phase):**
- Phase C: empty-state triage when search returns nothing (YouTube link, ask KJ to make, DIY-via-gen.nomadkaraoke.com). Design: [phase-c-design.md](archive/2026-04-23-song-selection-phase-c-design.md).

## 2026-04-23 - Feature: Song selection UX — grouped search + KJ-picks-version (Phase A)

The "Pick your song" page now shows **one tile per unique song** instead of N tiles per N versions. The common-case singer — who wants "Bohemian Rhapsody" and doesn't care whether it's SoundChoice SC6523 or Karaoke Version's cover — taps once and submits a `kj_pick` request. The KJ sees the full candidate snapshot on the admin side and picks the best-quality version in one tap. Phase A of the three-phase song-selection-ux overhaul (see [master plan](archive/2026-04-23-song-selection-ux-master-plan.md)).

**Data shape changes:**
- `GET /sing/search` response now returns `{songs: [{key, artist, title, version_count, in_library, has_community_only, versions: [...]}]}`. The old flat `{local, karaoke_nerds}` keys are gone. `unified_search()` in `routes.py` gained a `grouped` kwarg; admin-side search callers still get the flat shape (kwarg default).
- Grouping is exact-match on a normalized `(artist, title)` key — `_normalize_song_key` strips "feat." / "ft.", parenthetical suffixes, apostrophes, punctuation, and collapses whitespace. No fuzzy matching; "Don't Stop Believin'" and "Dont Stop Believin" will still render as two tiles until we revisit.
- Each group's `versions[]` carries the full unchanged local / KN track objects (for KN: `{source:"kn", kn:{...}}`) so the admin picker can render richly from the snapshot — no re-query needed at approval time.

**New source_type: `kj_pick`:**
- `_ALLOWED_SOURCES` in `sing.py` gains `"kj_pick"`. A `kj_pick` submit stores the `versions[]` snapshot in `source_meta` verbatim (JSON TEXT column). `_validate_kj_pick_payload` rejects empty arrays + snapshots >50 versions.
- **Auto-approve is skipped for `kj_pick`** — the whole point is to defer binding to the KJ, so bypassing review would leave the rotation entry with no file attached. All other source types continue to auto-approve as before.

**Approval flow:**
- `POST /rotation/requests/<id>/approve` now accepts `{version_index: N}` in the JSON body. For `kj_pick` requests it's required — `_pick_version_from_kj_pick` in `routes.py` translates the picked version into concrete `source_type` / `source_ref` / `source_meta` fields (local path / divebar file_id / YouTube URL), writes them back via new `SingStore.update_request_source`, then hands off to the existing `approve_sing_request` dispatch.
- Post-approval the `sing_requests` row reflects what actually played (local / divebar / youtube) — the transient `kj_pick` placeholder is gone. Important for audit trails and the "now playing" history view.
- Missing / out-of-range index returns 400 and leaves the row pending so the admin can retry. On non-`kj_pick` requests `version_index` is ignored (backwards-compat).

**UI:**
- Singer side (`static-sing/sing.js`): `renderSearch()` rewritten to consume `results.songs` and produce one card per group with a "Let the KJ pick the best version →" CTA. Single-version groups short-circuit to today's per-version flow ("Add to queue" CTA, no `kj_pick` submission). "N versions available →" hint is present but inert — Phase B wires the real expander to it.
- Admin side (`static/app.js`): pending `kj_pick` rows grow a picker below the singer summary, one mini-card per candidate (`📁 Local file` / `💿 Divebar mirror` / `🎤 Community` / `📺 YouTube`). Candidates ranked locals first, then KN+divebar, then KN community, then KN YouTube-only. Each card has its own "Approve with this →" button that passes `version_index`.

**Modified files:**
- `kj-controller/routes.py` — `_normalize_song_key`, `_group_search_results`, `unified_search(grouped=True)` path, `_pick_version_from_kj_pick`, `approve_sing_request_route` kj_pick branch. Top-level `import json`.
- `kj-controller/sing.py` — `kj_pick` in `_ALLOWED_SOURCES`, `_validate_kj_pick_payload`, `/sing/submit` validation + auto-approve skip.
- `kj-controller/sing_store.py` — new `update_request_source()` method.
- `kj-controller/static-sing/sing.js` — grouped rendering + `pickKjChoice` + `pickSingleVersion`.
- `kj-controller/static-sing/sing.css` — `.result-row.grouped` grid layout.
- `kj-controller/static/app.js` — inline version picker for `kj_pick` rows + `approve(id, versionIndex)`.
- `kj-controller/static/style.css` — `.pr-picker` + `.pr-version` styles, amber `.pr-badge.pr-kj_pick`.
- Tests: `tests/unit/test_search_grouping.py` (29 cases), `tests/integration/test_sing_public_routes.py::TestSearch` (5 new), `tests/integration/test_sing_kj_pick.py` (23 cases), `tests/unit/test_sing_store.py::TestUpdateAndStatus` (3 new), `tests/integration/test_sing_admin_routes.py::TestApproveKjPick` (6 cases), `tests/integration/test_sing_kj_pick_e2e.py` (3 cases).

**Version:** `0.24.0` → `0.25.0` (minor — new response shape, new source_type).

**Manual ops steps required (post-deploy):** none. Frontend + backend changes land together; restart kj-controller as normal. No schema migration needed — `source_meta` JSON column already exists from sub-project #1.

**Not yet shipped (follow-ups):**
- Phase B: per-group version expander on the singer side, showing metadata (brand, format, filepath) + commercial-vs-community explainer. Design: [phase-b-design.md](archive/2026-04-23-song-selection-phase-b-design.md).
- Phase C: empty-state triage (YouTube / make-request / DIY-via-gen.nomadkaraoke.com). Design: [phase-c-design.md](archive/2026-04-23-song-selection-phase-c-design.md).

## 2026-04-22 - UX tweaks: 4-digit event codes, `/sing/` path hidden, rules inlined

Follow-up iteration on the singer UI that shipped earlier today. Three visible changes, one structural one.

**Event token is now a 4-digit numeric code.** Previously a 16-char `secrets.token_urlsafe` string — too long to read off the venue screen and awkward to type. Now a zero-padded `0000–9999` code generated via `secrets.randbelow`. `regenerate_token()` refuses to return the same code twice in a row. The 10 000-combo space is protected by a new per-IP rate limit (10 attempts per 5 min) on the code-validation endpoint.

**Public host serves the singer UI at the ROOT** — no more `/sing/` segment for singers to type. `sing.nomadkaraoke.com/?t=4321` now works. A tiny WSGI middleware (`install_public_host_rewriter` in `sing.py`) prepends `/sing` to the PATH_INFO on the public host so the blueprint stays mounted at `/sing/` internally while the admin host (`nomadpc.local`, `kjbox.nomadkaraoke.com`) keeps the KJ controller UI at `/`. Installed from both `create_app()` and `start_app()` — the duplication code-smell is still there, tracked as a follow-up.

**No-token visitors get a code-entry form.** Typing `sing.nomadkaraoke.com` into a browser now shows a 4-digit numeric input (autocomplete="one-time-code" so iOS / Android keyboards surface the code-entry UX) that auto-submits on the 4th digit, validates via new `POST /sing/validate`, and redirects to `/?t=XXXX`. The old "Requests aren't open right now" page is reserved for when the KJ has actually paused requests (`is_enabled() is False`).

**House rules are inlined on every page.** The separate `/sing/rules` route and `templates/sing_rules.html` are gone. Instead, `renderRulesFooter()` in `sing.js` populates a persistent `<div id="sing-rules-footer">` present in every template branch (closed / code_entry / main SPA). Short bullets are always visible; a `<details>` "Read the full rules" expands the long copy.

**Breaking changes** (all shipped an hour ago — no deployed clients yet):
- `sing_store.regenerate_token()` now emits 4 digits, not 16 URL-safe chars. `TOKEN_BYTES` constant renamed to `TOKEN_DIGITS`.
- `get_event_url(cfg, token, scope="public")` returns `{base}/?t=TOK` (no `/sing/`). `scope="local"` still returns `{base}/sing/?t=TOK` because the admin device serves its KJ controller UI at `/`.
- `GET /sing/` with no/invalid token no longer returns 403 — it returns 200 (missing token) or 400 (bad token) with a code-entry form.
- `/sing/rules` route removed; `sing_rules.html` deleted.
- Manifest `start_url` is host-aware: `/` on the public host, `/sing/` on the admin host.
- Service worker registration reads `window.location.pathname` to decide between `/sw.js` (scope `/`) and `/sing/sw.js` (scope `/sing/`).

**New endpoint:**
- `POST /sing/validate` — rate-limited (10/IP/5min) token-verify endpoint backing the code-entry form. Returns `{ok: true}` on match, 400 on miss, 429 when throttled.

**Modified files:**
- `kj-controller/sing_store.py` — token generator
- `kj-controller/sing.py` — new `/validate`, rewritten `landing()`, `get_event_url`, `manifest()`, `install_public_host_rewriter`; split validate rate-limit bucket
- `kj-controller/app.py` — wires `install_public_host_rewriter` in both `create_app()` and `start_app()`
- `kj-controller/templates/sing.html` — three-way branch (closed / code_entry / main)
- `kj-controller/static-sing/sing.js` — `BASE` detection, `initCodeEntry()`, `renderRulesFooter()`, removed inline-rules `<details>` and identity-screen house-rules link
- `kj-controller/static-sing/sing.css` — `.sing-enter-code` + `.rules-footer` styles, removed `.rules-inline` + `.sing-rules` + `.sing-footer-links`
- Tests: `test_sing_store.py` asserts 4-digit format; `test_sing_public_routes.py` new `TestValidateCode` class + rewritten `TestLanding`; `test_host_guard.py` new `TestPublicHostRootRewrite` class

**Manual ops steps required (post-deploy):** none. Frontend-only + Python route changes; restart kj-controller as normal. The in-flight event token (if any) is no longer in the expected 4-digit format but will keep working until the KJ rotates it via the admin UI — there's no live event right now and nobody has used yesterday's deployment yet, so no singer impact.

## 2026-04-22 - Feature: Singer expectations UI (push + wait estimates + rules page, sub-project 4/4)

Singers who scan the QR now get honest wait-time estimates, a rules page, a "what's playing now" widget, an offline banner, and — on Android + desktop Chrome + iOS-installed-as-PWA — Web Push notifications when they're "up in 2", "up next", and "now singing". Approve/reject decisions also push. The held-tab polling flow shipped in sub-project #1 remains the universal fallback — push is additive, not a replacement.

See the design + plan docs:
- [2026-04-22-singer-expectations-design.md](archive/2026-04-22-singer-expectations-design.md)
- [2026-04-22-singer-expectations-plan.md](archive/2026-04-22-singer-expectations-plan.md)

**New modules:**
- `kj-controller/push_dispatcher.py` — `PushDispatcher` class (subscription scan, ladder-step decision, dedup via `last_sent_state`, 500ms debounce, 2-worker send pool) + pure helpers `decide_ladder_step`, `next_entry_for_phone`, `render_payload`.
- `kj-controller/wait_estimate.py` — pure function `compute_estimate(entries, target_id, cfg)` returning position + honest range derived from tonight's sung-entry mean ± stdev (with a configurable fallback spread when <3 done entries).
- `kj-controller/static-sing/sw.js` — service worker handling `push` + `notificationclick`. Shell cache for offline render. `skipWaiting` + `clients.claim` for fast rollout.
- `kj-controller/templates/sing_rules.html` — standalone rules page template, public (no token gate), reuses the 5 rules from `desktop/rotation_rules_printable.html`.

**New endpoints:**
- Public: `GET /sing/now`, `GET /sing/rules`, `GET /sing/manifest.json` (dynamic — token-aware `start_url`), `GET /sing/sw.js` (served from `/sing/` for correct SW scope).
- Push: `POST /sing/push/subscribe`, `POST /sing/push/unsubscribe` (token-gated).

**Response shape change:**
- `GET /sing/status/<id>` now returns `estimate` and `now_playing` sub-objects. Legacy top-level `position`, `estimated_wait_s`, `queue` keys kept for the client rollout window.

**New DB table:**
- `sing_push_subscriptions` in `rotation.db` — (token, endpoint) unique, carries `last_sent_state` JSON for dedup, soft-disabled by 404/410 response from FCM.

**New config keys** (all defaulted in `config.py`):
- `sing_estimate_transition_s` (30), `sing_estimate_default_song_s` (240), `sing_estimate_min_spread_s` (120)
- `vapid_public_key`, `vapid_private_key`, `vapid_subject` — auto-generated on first boot by `_bootstrap_vapid_keys` in `app.py` and persisted to `config.json` (gitignored).

**Integration hooks:**
- `RotationManager._after_mutation()` now notifies `self.push_dispatcher` (debounced 500ms). No-op when the dispatcher isn't wired.
- `/rotation/requests/<id>/approve` and `/reject` fire `notify_request_decision(...)` for immediate singer feedback.
- `/rotation/requests/config` token regeneration calls `cleanup_stale_push_subscriptions` to garbage-collect subs on other tokens older than 7 days.

**Frontend additions (sing.js):**
- "What's playing now" widget polls `/sing/now` every 15s on landing; confirmation page consumes the `now_playing` sub-object from `/sing/status` polls.
- Offline banner: two-signal detection (`navigator.onLine` events + 2-poll-failure threshold) so captive-portal networks still surface it.
- Push opt-in button on confirmation page, 2s delay after "you're in!". Graceful degrade on iOS Safari non-standalone → instructional card explaining Share → Add to Home Screen.
- Wait-estimate rendering reads from `data.estimate.*` with 4 distinct UI states (now_singing, pos 1 "up next", pos 2 "1 song to go", pos 3+ honest minute range).

**New dependency:** `pywebpush>=2.0.0` (and its transitive `cryptography`).

**Test coverage:** 60+ new tests across `test_wait_estimate.py`, `test_sing_now_and_status.py`, `test_sing_public_routes.py` (rules/manifest/sw), `test_sing_push_routes.py`, `test_sing_store.py` (push subscriptions), `test_vapid_bootstrap.py`, `test_push_dispatcher.py` (27 tests), `test_rotation_push_hook.py`, `test_sing_admin_routes.py` (push hooks), and `test_sing_push_e2e.py` (2 full end-to-end flows). Full suite 1411+ tests pass.

**Manual ops steps required (post-deploy):**
- First service restart on NomadPC will generate VAPID keys into `config.json` (gitignored). No manual key management.
- Web Push on iOS requires the singer to Add-to-Home-Screen first — the confirmation page shows an inline instructional card explaining this when it detects iOS Safari outside a standalone PWA.
- No DNS / tunnel / Cloudflare changes needed (sing.nomadkaraoke.com from sub-project #1 is reused).

## 2026-04-18 - Feature: Public singer request form (MVP, sub-project 1/4)

Singers can now submit song requests from their phones by scanning a QR code, instead of handing the KJ a paper slip. The form is reachable over the internet via a new Cloudflare tunnel hostname (`sing.nomadkaraoke.com`, no Access rule) and over the venue wifi via the NomadPC's LAN IP when no internet is available. Requests land in a pending review queue on the KJ UI by default; an auto-approve toggle lets the KJ bypass review when they trust the crowd.

See the design + plan docs:
- [2026-04-18-public-request-form-design.md](archive/2026-04-18-public-request-form-design.md)
- [2026-04-18-public-request-form-plan.md](archive/2026-04-18-public-request-form-plan.md)

**New modules:**
- `kj-controller/sing.py` — public `/sing/*` blueprint, token gate, rate limiter (5/5min/IP), host-based route guard, QR-overlay auto-sync helper
- `kj-controller/sing_store.py` — SQLite CRUD for `sing_requests` (lives alongside `rotation_entries`) + event-token helpers on `rotation_meta`
- `kj-controller/templates/sing.html` + `static-sing/` — singer-facing mobile SPA

**New endpoints:**
- Public: `GET /sing/`, `GET /sing/search`, `POST /sing/submit`, `GET /sing/status/<id>`
- Admin: `GET /rotation/requests`, `GET|POST /rotation/requests/config`, `GET /rotation/requests/qr.svg`, `POST /rotation/requests/<id>/approve|edit|reject`

**Integration hooks:**
- `POST /rotation/archive` regenerates the event token + re-enables requests + syncs any `qr_code` overlay with `config.follow_event_url=True`
- `POST /system/sleep-mode` on entry disables requests; exit does **not** auto-re-enable (KJ flips back manually)

**Manual ops steps required (post-deploy):**
1. Add DNS CNAME `sing.nomadkaraoke.com` → `<tunnel-id>.cfargotunnel.com`
2. Add new `ingress` entry in `/etc/cloudflared/config.yml` on NomadPC (see MINIPC-SETUP § 2.6)
3. Confirm no Cloudflare Access policy attached to the new hostname
4. `sudo systemctl restart cloudflared` + `sudo systemctl restart kj-controller`

**New dependency:** `qrcode` (pure-Python SVG QR generation; no Pillow).

**Test coverage:** 1327 unit+integration tests pass (1232 prior + 95 new). Overall coverage 81%; `sing_store.py` 99%, `sing.py` 87%, `routes.py` 85%. Coverage includes sing_store CRUD + token helpers, public blueprint (landing, search, submit, rate limit, status), admin endpoints (all approval paths, edit, reject, config, QR), host-based route guard (blocks admin + static on public host; private hosts unaffected), and archive / sleep integration hooks.

## 2026-04-17 - Feature: Runtime-swappable karaoke renderer (mpv / VLC)

The KJ can now switch karaoke engines at runtime from the AV Output modal. mpv is the default (pitch-shift supported); VLC is the fallback if a specific file or quirk doesn't render on the current engine. Switching is rejected during active karaoke playback (HTTP 409). Filler music stays uninterrupted across the swap. See [AUDIO.md § Karaoke Renderer Toggle](AUDIO.md#karaoke-renderer-toggle).

**Architecture refactor** ([plan](archive/2026-04-17-renderer-toggle-plan.md)):
- Extracted ~110 lines of duplicated filler-VLC logic into a single `FillerVLC` class shared across renderer swaps — single owner of port-8081 VLC, its audio backend, and the auto-heal on broken aout.
- Added `KaraokePlayer` Protocol formalising the contract both backends satisfy.
- Added `PlaybackCoordinator` facade that owns the filler + one karaoke player at a time. `switch_renderer()` tears down the old player and builds the new; filler untouched.
- `audio_monitor.py` now talks to the coordinator instead of poking mpv internals directly.
- Fixed a latent crash in `routes.py`'s fadeout action that called mpv-only methods on what would be a `VLCManager` after the previous rollback.

**New endpoints:** `GET /renderer`, `POST /renderer {mode}`. `GET /status` now includes a `renderer` block with capability flags.

**UI:** renderer radios in the AV Output modal; small engine badge ("MPV"/"VLC") in the Now Playing bar; pitch controls auto-hide when the active engine doesn't support pitch.

**Version:** `kj-controller` bumped 0.19.3 → 0.20.0 (minor — new feature + facing API surface).

**Test coverage:** 1185 tests pass (874 unit + 311 integration). New modules: `test_filler.py` (36), `test_mpv_karaoke_player.py` (25), `test_vlc_karaoke_player.py` (20), `test_playback_coordinator.py` (30), `test_karaoke_player_protocol.py` (10 parametric), `test_renderer_routes.py` (6). Obsolete `test_vlc.py` / `test_mpv_manager.py` / `test_vlc_reconnect.py` removed; their value migrated to the new files.

## 2026-04-17 - Fix: Rotation search race when saving before results render

Fixed a UX bug where pressing Enter (or clicking Add) in the rotation add form before the search-as-you-type dropdown appeared would: save the entry successfully, then pop up a stale dropdown whose Link / DL & Link buttons silently did nothing. Cause: the pending `/rotation/search` fetch resolved after the form was reset, rendering a dropdown with no valid singer context; `selectRotSearchResult()` then returned early because `singerPillInput` was empty.

Fix: added a `rotSearchGen` generation counter bumped by `hideRotSearchDropdown()` (and called from `addRotationEntry()` after validation passes). In-flight fetches capture the generation on dispatch and discard their response if it changed.

- **Version:** `kj-controller` bumped 0.19.2 → 0.19.3

## 2026-04-16 - Fix: Filler music silent after every karaoke track (NomadPC)

Root-caused and fixed a race where VLC's filler music would go silent after every karaoke track played via the new mpv karaoke pipeline. See [AUDIO.md § Filler Audio Handoff](AUDIO.md#filler-audio-handoff-mpv--vlc) for the full story.

- **Race condition:** mpv emits its `end-file` IPC event ~350ms before it actually releases the ALSA device. The old handler called `fade_in_filler` → `pl_play` within 0.6ms — VLC's `snd_pcm_open` hit "Device or resource busy" and its `aout` module entered a permanent broken state (`state=playing` but `playedabuffers=0`, audio decoded into the void).
- **Fix:** Added `_wait_for_mpv_idle()` that sends mpv `stop` and polls `idle-active` before yielding to VLC. Eliminates the race.
- **Safety net:** Added `_verify_filler_playing()` auto-heal thread that samples VLC stats 4s after every fade-in; if `played=0` while `decoded>100`, calls `_relaunch_filler()` to restart only the VLC process (mpv untouched).
- **Version:** `kj-controller` bumped 0.19.0 → 0.19.1

## 2026-04-15 - Remote Audio Monitor

- **Audio Monitor:** Added remote audio monitoring via AV Output modal. Streams live audio over HTTP for dev/testing. Uses PipeWire HDMI capture + ffmpeg MP3 encoding. See [AUDIO.md](AUDIO.md#remote-audio-monitor).

## 2026-03-26 - Streamlined Rotation Link UX & Auto-Advance

Major improvements to the rotation song-linking workflow, search results, playback auto-advance, and edit mode isolation.

- **Link mode UI:** Visual "Linking song for: Singer — Song" banner with Cancel button replaces old prompt()-based flow
- **Singer name hidden in link mode:** Not needed when linking to an existing entry
- **Song_artist auto-updated:** When linking a search result, the rotation entry's song_artist is updated to match the selected result
- **Unlink Song option:** Added to rotation entry overflow menu for removing file links
- **Downloaded videos in search:** Local downloaded videos now included in rotation search results alongside catalog/KN/Divebar
- **Search result sorting:** Community tracks first, then karafun/preferred brands, then others
- **KN-style dropdown rendering:** Rotation search dropdown now renders identically to the Karaoke Nerds panel with community/preferred badges
- **Auto-advance on Play:** Play button sets the current entry to "Now Singing" and the next entry to "Up Next" automatically
- **File upload:** `POST /upload` route for uploading media files directly to the download folder (validates extension, sanitizes filename, triggers rescan)
- **Conky fix:** Removed `own_window_hints below` so rotation display stays visible after VLC exits fullscreen
- **Cache write on startup:** Prevents conky showing "Offline" status immediately after kj-controller restart
- **Pre-commit hook:** `.githooks/pre-commit` validates JS syntax via `node --check` before commits
- **"Update (Safe)" button:** Renamed with tooltip explaining VLC keeps playing during restart
- **Edit mode isolation:** Global keyboard shortcuts (Space, arrow keys) and click handlers disabled during inline editing
- **Polling protection:** 10-second rotation poll skips re-render when edit mode is active
- **Layout improvements:** 2:1 left/right column ratio, reduced page padding for better space utilization
- **Upload/Download widget:** Renamed with file upload picker for direct media uploads

## 2026-03-21 - SQLite-Primary Offline-First Rotation

Major architecture change: rotation system now uses local SQLite as source of truth instead of Google Sheets.

- **SQLite primary:** All rotation data stored in `~/kjdata/rotation.db` (WAL mode, busy_timeout)
- **Offline-first:** System works without internet; Google Sheets is an optional background backup
- **Stable IDs:** Entries use auto-increment SQLite IDs instead of fragile sheet row indices
- **Atomic moves:** Position updates use SQL UPDATE (not delete+insert), preventing data loss
- **File linking:** Rotation entries can link to catalog files (`POST /rotation/link`) with duration lookup
- **Time estimates:** Each entry shows estimated sing time based on cumulative durations
- **Sync indicator:** UI shows green/yellow/gray dot for Sheet sync status
- **Restore button:** Emergency restore from Sheet backup if local DB is corrupted
- **New endpoints:** `/rotation/link`, `/rotation/unlink`, `/rotation/sync-status`, `/rotation/restore`
- **API change:** All rotation endpoints now use `id` instead of `row_index`, move uses `{id, new_position}`
- **Conky simplified:** Display script reads local cache only (Sheet CSV fallback removed)
- **Modules:** `rotation_store.py` (SQLite CRUD), `rotation_sync.py` (Sheet backup), `rotation.py` (coordinator)

## 2026-03-21 - Browser Mode Orphan Detection

Fixed browser mode state getting out of sync after auto-deploy restarts. Chromium processes now survive service restarts as orphans but the server lost track of them — mode showed "VLC" while browser was visible, toggle didn't work, and playing a video left the browser on screen.

- `ChromiumManager` now detects orphan Chromium via `pgrep` (not just managed `self.process`)
- Orphans killed on startup; adopted by status endpoint if still running
- `/play` checks actual process state, not just `_browser_mode` flag
- PipeWire reset triggered for orphan cleanup (not just managed process)

## 2026-03-21 - Sleep Mode

Added Sleep Mode toggle to the System section of the KJ Controller UI for low-power state between weekly karaoke nights.

- Toggle in System panel stops VLC, overlays, rotation display, VNC, Dropbox, and unnecessary services
- Unmounts and spins down USB SSD, enables USB auto-suspend
- Blanks display via DPMS, switches to power-saver power profile
- Pre-sleep state captured to restore only previously-running services on wake
- Playback routes return 409 while sleeping; web UI and SSH remain accessible
- Reboot during sleep auto-clears flag — system boots normally
- Shell scripts (`sleep-enter.sh`, `sleep-exit.sh`) can also be run manually via SSH
- Installed `uhubctl` on NomadPC (USB hubs don't support per-port power, but available for future hardware)

## 2026-03-06 - Drag-and-Drop Rotation Reordering

Added drag handles (≡) to rotation entries in the KJ Controller UI for reordering singers.

- Drag handle on the left of each entry, HTML5 drag-and-drop with visual feedback
- Backend `POST /rotation/move` endpoint: deletes source row and re-inserts at target position
- `RotationManager.move_entry()` handles row index shifting after deletion

## 2026-03-05 - Exact Status Text on Rotation Display

Conky rotation display now shows the exact status text from the Google Sheet (e.g. "Now Singing", "Being Made (!)") instead of abbreviated badge labels ("NOW", "MAKING"). Color coding preserved, "Waiting" status hidden (no badge).

## 2026-03-05 - Instant Rotation Display Updates

Rotation display (conky) now updates within ~3 seconds of changes made in KJ Controller, down from up to 60 seconds.

- KJ Controller writes `/tmp/rotation_cache.json` after every rotation mutation (add, update, delete, mark singing, etc.)
- `rotation_data.py` reads from local cache first, falls back to Google Sheet CSV if cache is missing or >120s old
- Conky polling interval reduced from 30s to 3s (reading a local file is effectively free)

## 2026-03-05 - Faster Filler Music Fade

Reduced filler music fade time from 3s to 1.5s, and post-fade buffer from 0.5s to 0.3s. Karaoke playback now starts ~1.8s after pressing Play (was ~3.5s).

## 2026-03-05 - Consistent Button Styling, System Stats & Format Filter

**UI overhaul:** Unified all ~33 button types to consistent outlined style (was: 3 conflicting paradigms).

- Base buttons: dark outlined (`#222` bg, `#444` border, `#ccc` text) with hover glow
- New `.btn-primary` class: pink-hinted outline for primary actions (Play, Download, Search, Save)
- All secondary buttons (filler, overlay, rotation, system) updated to match
- Delete buttons: outlined red instead of solid red fill

**System section reorganized** into logical subsections:
- Media & Output (AV Output, Filler Music, Audio Device)
- Maintenance (Auto-Deploy, Update, Restart, Reboot)
- Stats (CPU, Memory, Disk — live sparkline graphs with hover tooltips)

**System stats** (`GET /system/stats`):
- CPU %, memory (used/total GB), disk (used/total GB) via `psutil`
- Frontend: bar charts + 30-point sparkline history, polled every 5s
- Hover tooltips show value and time offset (e.g., "45% (30s ago)")

**3-state format filter** replaces MP4 Only toggle:
- Cycles: All → MP4 Only → CDG+ZIP Only
- Persisted in localStorage with migration from old boolean setting

**Dependency:** Added `psutil` to `requirements.txt`

## 2026-03-05 - VLC Process Lifecycle Decoupling

**Zero-downtime deploys:** VLC playback now survives kj-controller restarts.

- VLC launched in its own process group (`start_new_session=True`) so it's not killed when the Python process exits
- On startup, kj-controller probes VLC HTTP ports and reconnects to existing instances
- Playback state (current song, filler track) persisted to `/tmp/kj-vlc-state.json` and recovered on restart
- `restart_instances` handles orphan VLC processes (kills by port when we don't own the PID)
- Auto-deploy skips service restart for frontend-only changes (JS/CSS/HTML)
- systemd unit updated: `KillMode=process` so only Python is killed, not VLC children

## 2026-03-05 - UI Polish & System Management

**Rotation improvements:**
- "New Rotation" button: archives all entries to "Past events" sheet, clears rotation for a fresh night
- Edit and delete rotation entries: pencil button, Shift+click to edit inline, Ctrl/Cmd+click to delete
- Ctrl/Cmd+hover shows red strikethrough delete preview, Shift+hover shows purple edit preview
- Exclusive statuses: only one "Now Singing" and one "Up Next" at a time (others reset to "Waiting")
- New entries default to "Waiting" status
- `POST /rotation/archive`, `/rotation/edit`, `/rotation/delete` endpoints
- Local dev server (`dev_server.py`) with mock rotation data for UI iteration
- Replaced "Updating..."/"Updated!" text with spinner/tick indicator in section header
- Compact row layout (smaller fonts, tighter padding) to show more singers at once
- Advanced status dropdown ("...") button with all 7 Google Sheet statuses
- Click-to-copy on singer names and song text

**System section:**
- Auto-deploy toggle (start/stop + enable/disable for reboot persistence)
- Graceful restart/reboot/update: spinner overlay, polls backend, shows success when back online
- Update button always restarts service; auto-reloads browser when backend recovers

**YouTube settings:**
- yt-dlp version check against PyPI (cached 24h), yellow dot + "Update" button when outdated
- One-click yt-dlp upgrade from UI with graceful restart polling
- Normalized version comparison (2026.03.03 vs 2026.3.3)

**Screen Preview:**
- Hide button disconnects VNC and collapses section; clicking a size button reconnects
- Hidden state persists via localStorage; VNC doesn't auto-connect when hidden

**Overlay management:**
- Backup/Restore buttons: download config as JSON, restore from file
- `POST /overlays/import` endpoint for bulk import
- Untracked `data/overlays.json` from git to prevent auto-deploy wiping device config

---

## 2026-03-05 - Rotation Sheet Integration & System Update Button

**Motivation:** The singer rotation was managed entirely via a Google Sheet, which meant the KJ had to alt-tab to the browser to update singer status. This led to forgetting to update the rotation until several songs late.

**New module: `rotation.py`**
- `RotationManager` class using `gspread` + GCP service account for Google Sheets API
- Auto-detects header row and column positions (handles metadata rows, extra columns)
- Read: fetches non-done entries with 10s cache to avoid API hammering
- Write: update status, mark singing (auto-clears other "Singing Now"), add new entries
- Portable timestamp formatting (no platform-specific strftime)

**New UI: Rotation panel (below Playback Controls)**
- Shows all non-done rotation entries with singer name, song, and status badges (NOW/NEXT/WIP)
- Per-row action buttons: Singing, Done, Next
- Inline "Add Singer" form
- Auto-refreshes every 10s, manual Refresh button
- Gracefully hidden when rotation is not configured (no service account set up)

**New UI: System Update button**
- `POST /system/update` runs `git pull origin main` on the device
- If Python files changed: auto-restarts the service and reloads the page after 5s
- If only static files changed: prompts to refresh the browser
- Reports git pull output and errors in the UI

**Config additions** (both optional):
- `rotation_sheet_id` — Google Sheet ID for singer rotation
- `rotation_credentials_file` — path to GCP service account JSON key

**Setup on NomadPC:**
- Created GCP service account `kjbox-138@nomadkaraoke.iam.gserviceaccount.com`
- Shared rotation Google Sheet with service account (Editor access)
- SA key deployed to `~/kjdata/rotation-sa-key.json`
- Config updated, service restarted, verified working

**Dependencies added:** `gspread`, `google-auth` (already installed in venv)

**Version:** 0.8.3 → 0.9.0

## 2026-02-26 - YouTube Download Resilience & Async Downloads

**Motivation:** YouTube downloads via yt-dlp would fail after several tracks in quick succession during a live karaoke show (YouTube rate limiting/bot detection). Also, refreshing the page during a download lost the completion notification since the download was synchronous (blocking 25+ seconds).

**New module: `youtube_health.py`**
- Health checks: yt-dlp version, EJS solver (yt-dlp-ejs) installed/version, Deno runtime available/version, cookie file status and validation
- Cookie management: Netscape-format validation, atomic file writes with 0o600 permissions

**Anti-detection settings in `media.py`**
- New `_ytdlp_base_opts(config)` helper used by both `download_video()` and `youtube_search.py`
- Sleep intervals between requests (`sleep_interval: 1`, `max_sleep_interval: 5`, `sleep_interval_requests: 1`) — main mitigation for rapid successive downloads
- Retries (`retries: 3`, `fragment_retries: 3`, `extractor_retries: 3`)
- Cookie support via `youtube_cookies_file` config key

**Async downloads**
- `POST /download` now starts download in a background thread, returns immediately
- Server-side `download_state` tracking: `idle → downloading → completed/error`
- Frontend detects completion via existing 2-second `/status` poll — survives page refresh
- `POST /download/ack` resets state after frontend handles notification
- Concurrent download protection (409 if already downloading)

**YouTube Settings modal (web UI)**
- Health dot next to "Download Song" header (green/yellow/red)
- Modal shows yt-dlp version, EJS solver status, Deno runtime status, cookie status
- Paste Netscape-format cookies from browser extension → Upload → validates and saves
- Delete cookies button with confirmation

**New API endpoints:** `GET /youtube/status`, `POST /youtube/cookies`, `DELETE /youtube/cookies`, `POST /download/ack`

**Dependencies:** Changed `yt-dlp` → `yt-dlp[default]` in requirements.txt (includes yt-dlp-ejs solver plugin). Deno runtime installed on NomadPC for EJS solver.

**NomadPC setup:** Installed yt-dlp-ejs 0.5.0, Deno 2.7.1 (symlinked to `/usr/local/bin/deno`), verified via `/youtube/status` endpoint.

## 2026-02-26 - Fix: AV Reset button failed with Permission denied on /etc/asound.conf

**Root cause:** `POST /av/reset` calls `fix-hdmi-audio.sh` from within the Flask process (running as user `nomad`). The script needs to write `/etc/asound.conf` and call `amixer`, both requiring root. The `ExecStartPre=+` mechanism that gives root at service start doesn't apply to this in-process call.

**Fix:**
- `routes.py`: Changed subprocess call from `['/bin/bash', script_path]` to `['sudo', script_path]`
- Added `/etc/sudoers.d/kj-fix-hdmi` on NomadPC: `nomad ALL=(root) NOPASSWD: /opt/nomad/kjbox/kj-controller/fix-hdmi-audio.sh`
- Updated `docs/MINIPC-SETUP.md` to document this sudoers requirement (needed for fresh installs)

## 2026-02-25 - AV Output Modal (replaces Audio/Display dropdowns)

**Motivation:** The old Audio Output and Display Resolution dropdowns in the System section were a liability — changing them from their known-good values could undo the hard-won HDMI/audio configuration, and changes persisted to `config.json` without a clear "reset to safe state" path.

**Changes:**
- Replaced Audio Output dropdown + Display Resolution dropdown + Scan HDMI button with a single **AV Output** button (opens a modal)
- Modal shows full AV status: video connectors (connected/resolution/EDID name), HDMI PCM devices (jack state, IEC958 switch, ELD monitor name), PipeWire profile, VLC device, ALSA alias, and overall health indicators
- **Reset All** button runs `POST /av/reset` → `fix-hdmi-audio.sh` → restores the full known-good AV state (ALSA, IEC958, PipeWire, display 1920x1080) then restarts VLC
- AV settings no longer persisted to `config.json` — `fix-hdmi-audio.sh` is the single source of truth for the known-good state
- `fix-hdmi-audio.sh` extended: now also resets PipeWire to `output:analog-stereo+input:analog-stereo` and xrandr to 1920x1080 after fixing ALSA/IEC958. This means service restart always fully restores AV state.
- New routes: `GET /av/status`, `POST /av/reset`, `POST /av/vlc-device`

## 2026-02-25 - Fix: HDMI audio silent at boot (missing ExecStartPre)

**Root cause:** `fix-hdmi-audio.sh` was never being called at service start. The `ExecStartPre` line was documented in `HDMI.md` but was missing from `MINIPC-SETUP.md` (the actual setup guide used to configure the device). As a result, every boot left the IEC958 Playback Switch `off` (its hardware default), silencing all HDMI audio.

**What the IEC958 switch does:** It's the digital audio enable/disable at the HDA codec level. When `off`, the ALSA PCM stream runs normally (state: RUNNING, data flowing) but no audio packets are transmitted over HDMI. This is an HDA hardware default that must be explicitly enabled — it doesn't survive reboots.

**Fix:**
- Added `ExecStartPre=+/opt/nomad/kjbox/kj-controller/fix-hdmi-audio.sh` to `/etc/systemd/system/kj-controller.service` on NomadPC
- The `+` prefix makes it run as root (needed to write `/etc/asound.conf`), even though the main service runs as `nomad`
- Updated `MINIPC-SETUP.md` to include `ExecStartPre` so future reinstalls are correct

**Also confirmed at first venue test (2026-02-25):**
- OREI splitter in STD mode presenting "HDMI Splitter" EDID to NomadPC — correct 1920x1080
- Denon AVR on splitter Out 1 (50ft cable) — video and audio working after IEC958 fix
- 7" touchscreen on another output — working simultaneously

## 2026-02-24 - Power-Loss Hardening (SSD reformat + system config)

NomadPC gets unplugged at venues without clean shutdown. Applied four layers of hardening so power loss never prevents healthy startup.

**1. SSD reformatted: exFAT → ext4**
- exFAT has no journal — power loss can silently corrupt data
- Reformatted `/dev/sda1` (4TB SanDisk SSD) to ext4 with journaling
- Restored 413,670 files (3.44 TB) from verified HDD backup via rsync
- Updated fstab: `UUID=b5ec3a27-4477-467e-a002-fd7ab8b3b755`, `ext4`, `noatime,nofail`
- `noatime` reduces unnecessary metadata writes (fewer things to journal)

**2. Atomic JSON writes (code change)**
- `config.py:save_config_value()` and `media.py:MediaIndex.save()` previously used `open(path, 'w')` + `json.dump()` — power loss mid-write would truncate/empty the file
- Now use atomic write pattern: write to temp file → fsync → `os.replace()` (rename is atomic on ext4)
- `overlay.py` already used this pattern — no change needed
- Added 10 tests in `tests/test_atomic_writes.py` verifying crash safety

**3. Kernel panic auto-reboot**
- `/etc/sysctl.d/99-power-loss.conf`: `kernel.panic = 10`
- Default was 0 (hang forever on panic) — now reboots after 10 seconds

**4. Journal size cap**
- `/etc/systemd/journald.conf.d/size-limit.conf`: `SystemMaxUse=200M`
- Was unbounded (890MB and growing) — faster flush after unclean shutdown

**Docs updated:** `MINIPC-SETUP.md` (Phase 1.7 rewritten, Phase 6.1-6.2 updated for ext4, checklist updated)

## 2026-02-24 - Fix: 5 VLC Management Bugs (v0.8.2)

Follow-up to the ALSA contention fix (v0.8.1). Comprehensive audit found 5 additional bugs in VLC state management where the code didn't properly account for ALSA device exclusivity or made incorrect assumptions about state.

**Bug fixes:**

1. **Fade cancel (concurrent fade-in/fade-out race)** — If `fade_in_filler` and `fade_out_filler` ran close together, both fade threads would compete to set volume simultaneously. Added `_fade_cancel` event that each fade operation sets before starting, causing any in-progress fade to abort. `fade_out_filler` now also reads actual VLC volume instead of assuming `filler_volume`.

2. **Pause no longer starts filler** — Pausing karaoke was calling `fade_in_filler()`, but the paused karaoke VLC still holds the exclusive ALSA device. Filler would get "Device or resource busy". Removed filler fade-in/out from pause_resume — the KJ should just hear silence during pause.

3. **Stop uses ensure_karaoke_released** — The stop action was sending raw `pl_stop`/`pl_empty` without verifying the device was actually released before starting filler. Replaced with `ensure_karaoke_released()` which retries up to 5 times with status verification.

4. **Filler music change during karaoke** — `set_filler_music` unconditionally called `pl_play`, which would fail with "Device or resource busy" when karaoke was active. Now enqueues the track but skips playback — the track will start when karaoke ends and `fade_in_filler()` is called.

5. **Play state set atomically** — `current_playing_path` and overlay state were set in the route handler *before* `play_video` acquired the lock, creating a race where status could show song B's name while song A was still playing. Moved state-setting inside `play_video`'s `_play_lock` via new `display_path` and `overlay_manager` parameters.

## 2026-02-24 - Fix: Filler Music Audio Device Contention

Filler music stopped playing after karaoke songs ended. Root cause: the karaoke VLC held the exclusive ALSA device (`hw:0,3` via `hdmiout`) even after reaching "stopped" state, so the filler VLC got "Device or resource busy" when trying to resume.

**Fix:**
- Added `ensure_karaoke_released()` — explicitly sends `pl_stop` + `pl_empty` to karaoke VLC before filler resumes, forcing ALSA device release
- Added `_play_lock` to serialize concurrent `play_video()` calls (prevents race conditions when rapidly clicking different tracks)
- Added `last_play_time` grace period to prevent the monitor thread from falsely detecting "stopped" during song transitions
- Skipped the 3s filler fade-out when switching between karaoke songs (filler is already stopped), making song switches near-instant

## 2026-02-22 - NomadPC: Remote SSH Access (Tailscale + Cloudflare Tunnel)

Enabled SSH access to NomadPC from outside the LAN via two paths.

**What was done:**
- Confirmed Tailscale is already installed and running on NomadPC — IP `100.82.90.111`
- Added SSH ingress to the Cloudflare tunnel config (`/etc/cloudflared/config.yml`): `kjssh.nomadkaraoke.com → ssh://localhost:22`
- Created DNS CNAME for `kjssh.nomadkaraoke.com` in Cloudflare
- Restarted cloudflared service to pick up the new config
- Added `nomadpcts` (Tailscale) and `nomadpctunnel` (Cloudflare) SSH aliases to `~/.ssh/config` on Mac

**How to SSH remotely:**
- `ssh nomadpcts` — via Tailscale (Mac Tailscale must be running)
- `ssh nomadpctunnel` — via Cloudflare tunnel (browser auth on first use, `cloudflared` must be installed)

**Docs updated:** `MINIPC-SETUP.md` (sections 2.5, 2.6, 2.8), `TROUBLESHOOTING.md`

## 2026-02-19 - NomadPC: Fix HDMI Audio After Reboot

HDMI audio stopped working after reboot. Root cause: VLC was configured to use ALSA device `default`, which PipeWire redirects to the analog stereo output — not HDMI.

**Investigation:**
- PipeWire 1.0.5 defaults to analog stereo profile on boot
- Even after switching PipeWire to the HDMI profile, audio flowed through PipeWire (confirmed via `pw-top`) but produced no sound at the TV
- Direct ALSA access to `hw:0,7` (bypassing PipeWire) worked reliably

**Fix applied:**
- Created `/etc/asound.conf` defining `hdmiout` as `plug` → `hw:0,7` (direct ALSA, bypasses PipeWire)
- Updated `config.json`: `default_audio_device: "hdmiout"`, `audio_devices: {"hdmiout": "HDMI Output (TV)"}`
- PipeWire left on analog profile so it doesn't lock the HDMI device
- VLC now launches with `--alsa-audio-device hdmiout`

**Docs updated:** `AUDIO.md` (added NomadPC section), `MINIPC-SETUP.md` (corrected audio config and instructions).

## 2026-02-19 - NomadPC: TLS, Cloudflare Tunnel, Remote Access & Code Fixes

Set up HTTPS, Cloudflare tunnel for remote access, Zero Trust authentication, and committed several code fixes to the repo.

**TLS/HTTPS:**
- Generated mkcert certificates for `nomadpc.local`, `nomadpc`, `192.168.8.170`, `localhost`, `127.0.0.1`
- Deployed to `/opt/nomad/kjbox/kj-controller/certs/` on device
- Flask auto-switches to port 443 (HTTPS) when certs are present
- Websockify also uses certs for WSS on port 6080
- Config keys added: `tls_cert`, `tls_key`

**Cloudflare tunnel reconfigured:**
- Changed from SSH-only tunnel to web UI + VNC WebSocket
- `kjbox.nomadkaraoke.com` → `https://localhost:443` (KJ Controller web UI, `noTLSVerify: true` for mkcert)
- `kjvnc.nomadkaraoke.com` → `http://localhost:6080` (websockify for VNC preview)
- Two hostnames needed because Cloudflare tunnels don't support path-based routing

**Cloudflare Access (Zero Trust):**
- Configured via Cloudflare Zero Trust dashboard
- Email OTP authentication on both `kjbox` and `kjvnc` hostnames
- 24-hour session duration

**Code committed to repo:**
- **`vlc.py`**: Platform detection — `self.enabled` checks `config.get('enable_vlc', False)` alongside `is_pi()`
- **`app.py`**: Restructured platform setup — Pi-specific (xhost, dietpi) separate from shared (websockify). Websockify starts on any device with `enable_vlc: true`, with configurable host/port and TLS support
- **`auto-deploy.sh`**: Added `sudo` prefix to all `systemctl restart` commands (non-root `nomad` user needs it). Fixed catalog rebuild URL to try HTTPS first
- **`templates/index.html`**: Smart websockify routing — detects LAN vs tunnel from `location.hostname`. `.local`/`localhost`/IP → direct `hostname:6080`; anything else → `websockify_host` config value (`kjvnc.nomadkaraoke.com`)
- **`routes.py`**: Pass `config` dict to template for websockify settings

**Config keys added:** `websockify_host` (tunnel hostname for VNC WebSocket)

## 2026-02-19 - NomadPC: Media Setup & External Catalog

Set up the USB external drive, migrated legacy video files, and built the full karaoke catalog.

**USB SSD mounted:**
- SanDisk Extreme Pro 4TB (`/dev/sda1`, exFAT, label "Nomad4TBOne")
- Mounted at `/media/nomad/Nomad4TBOne` with fstab persistence (`nofail,uid=1000,gid=1000`)
- Contains HyperMule karaoke catalog (~415K files)

**Legacy video migration:**
- Migrated 697 MP4 files from `/home/nomad/kjdata/videos/` (old KJ software) to `/opt/nomad/YTDownloads/`
- Renamed from legacy format (`{random_id}.mp4` + JSON sidecar) to new convention (`{youtube_id}__Unknown__{safe_title}.mp4`)
- YouTube IDs extracted from `original_url` in JSON metadata; channel set to "Unknown" (not in legacy data)
- 6 files skipped (no YouTube ID), 1 error (malformed JSON)

**External catalog built:**
- 414,933 entries indexed into SQLite FTS5 database (`external_media.db`)
- Manifest file: `/media/nomad/Nomad4TBOne/HyperMule/all-karaoke-files-2025.02.28.txt`
- Path rewriting: `/Volumes/Nomad4TBOne/` → `/media/nomad/Nomad4TBOne/` (macOS → Linux)
- Search verified working (e.g., "bohemian rhapsody" returns Queen results from multiple publishers)
- Config keys added: `external_file_list`, `external_media_mount`

**Filler music:** Copied from legacy KJ data folder, playing successfully through HDMI.

**HDMI audio verified:** `speaker-test` and VLC both produce sound via HDMI. Later found to require direct ALSA config (see 2026-02-19 audio fix entry).

**HDMI display:** Set to 1920x1080@60Hz via `xrandr`, persisted with XFCE autostart entry.

**x11vnc fixes:**
- Added `-shared` flag (without it, new connections kick existing ones)
- Changed to `After=display-manager.service` + `WantedBy=multi-user.target` (avoids ordering cycle on shutdown)

**Documentation:** Updated MINIPC-SETUP.md with new Phase 6 (USB drive, filler music, legacy migration, external catalog), corrected x11vnc service definition, and updated checklist.

## 2026-02-18 - NomadPC: Initial Hardware Audit

Performed initial audit of the x86 mini PC that will serve as a more powerful replacement/companion for the Raspberry Pi at live events.

**Hardware discovered:**
- **CPU:** Intel N97 (4 cores, up to 3.6GHz, x86_64)
- **RAM:** 16GB
- **Storage:** 476GB NVMe SSD (ext4, 411GB free)
- **GPU:** Intel Alder Lake-N UHD Graphics
- **Display outputs:** 2x HDMI + 2x DisplayPort (HDMI-1 connected at 1920x1080)
- **Audio:** HDA Intel PCH, HDMI stereo via PipeWire (verified working)
- **Ethernet:** `enp2s0` (MAC: `84:47:09:5a:1d:13`)
- **WiFi:** `wlp1s0` (MAC: `9c:12:21:3f:39:43`)

**Software state:**
- **OS:** Linux Mint 22.1 Xia (Ubuntu 24.04 Noble), kernel 6.8.0-71-generic
- **Desktop:** XFCE via LightDM (autologin as `nomad` user)
- **Audio:** PipeWire 1.0.5 (not raw ALSA like the Pi)
- **Pre-installed:** VLC 3.0.20, conky 1.19.6, yt-dlp 2025.07.21, git 2.43.0, avahi-daemon
- **Cloudflared:** Running with SSH tunnel to `kjbox.nomadkaraoke.com`
- **Hostname:** `nomad-karaoke` (to be renamed to `nomadpc`)
- **User:** `nomad` (UID 1000, zsh shell)
- **SSH key:** `andrew@beveridge.uk` already authorized

**Not yet done:**
- Hostname rename to `nomadpc`
- Sleep/screensaver not disabled (lock=true, idle=900s)
- Tailscale not installed
- KJ Controller not deployed (/opt is empty)
- Avahi not restricted to ethernet interface
- BIOS "Power On After Power Loss" not verified

**Key differences from Pi that affect setup:**
1. PipeWire audio (not raw ALSA) — VLC needs `XDG_RUNTIME_DIR` for PipeWire socket access
2. XFCE desktop (not LXDE) — screensaver/power commands differ, conky `own_window_type` may need adjustment
3. VLC runs directly as `nomad` user — no root wrapper or `sudo -u dietpi` needed
4. `enable_vlc: true` config flag needed (no `/boot/dietpi.txt` sentinel)
5. x11vnc instead of RealVNC for VNC preview

Updated `docs/MINIPC-SETUP.md` with all verified details. Updated `README.md` and `CLAUDE.md` to reference both devices.

## 2026-02-17 - KJ Controller: VNC Screen Preview

Added a live VNC screen preview thumbnail to the KJ Controller web UI. The KJ can now see what's on the Pi's HDMI output directly in the browser without a direct line of sight to the display.

**Architecture:**
- **websockify** (Python package) runs on the Pi as a WebSocket-to-TCP proxy, listening on port 6080 and forwarding to RealVNC on port 5900
- **noVNC** v1.6.0 (vendored ES6 library) runs in the browser, connecting via WebSocket to render the VNC framebuffer into a canvas element
- The thumbnail is 200px wide, view-only, positioned in the left column of the web UI

**Changes Made:**
1. **websockify subprocess** — started during app startup on Pi only (`is_pi()` = true); resolves the binary from the venv's bin directory (`sys.executable` parent), falling back to system PATH
2. **noVNC vendored** — ~56 ES6 module files in `static/novnc/` (core library + pako compression vendor)
3. **VNC preview UI** — password input (stored in `localStorage`), connect/disconnect controls, auto-reconnect on disconnect (5-second delay)
4. **TLS/HTTPS support** — Flask and websockify serve over HTTPS/WSS when TLS certs are present. Required because RealVNC's RA2ne authentication uses `crypto.subtle` which is only available in secure contexts (HTTPS). Certs generated via `mkcert` (locally-trusted CA). When certs are present, Flask auto-switches from port 80 to 443.
5. **RA2ne auth handling** — noVNC's `serververification` event is auto-approved (similar to SSH host key acceptance) since this is a trusted local Pi. Without this handler, the RA2ne handshake hangs indefinitely.
6. **New config keys** — `websockify_port` (default: 6080), `vnc_target` (default: `localhost:5900`), `websockify_enabled` (default: true), `tls_cert` (default: `certs/cert.pem`), `tls_key` (default: `certs/key.pem`)
7. **New dependency** — `websockify` added to `requirements.txt`

**TLS certificate setup (one-time per dev machine):**
```bash
brew install mkcert && mkcert -install  # install local CA
mkcert nomadpi.local nomadpi 192.168.8.106 localhost 127.0.0.1
# Copy cert.pem and key.pem to kj-controller/certs/ on the Pi
```

**RealVNC device configuration applied** (`/root/.vnc/config.d/vncserver-x11`):
- Added `Encryption=PreferOff` — allows unencrypted connections (websockify handles TLS termination)
- Restarted `vncserver-x11-serviced` to apply changes

## 2026-02-17 - KJ Controller: Dynamic Overlay System

Added a configurable overlay system for the NomadPi display, managed entirely from the KJ Controller web UI.

**New Components:**
1. **Overlay Engine** (`desktop/overlay_engine.py`, `overlay_types.py`, `overlay_config.py`) — standalone pygame-ce process that renders overlays as borderless always-on-top X11 windows at 30fps. Supports 5 overlay types: scrolling ticker, static text, image/logo, countdown timer, and QR code.
2. **Overlay Manager** (`kj-controller/overlay.py`) — CRUD operations and state persistence for overlay configurations via `data/overlays.json`.
3. **Overlay REST API** — 7 new routes (`GET/POST /overlays`, `GET/PUT/DELETE /overlays/<id>`, `POST /overlays/<id>/toggle`, `POST /overlays/<id>/toggle-video`)
4. **Web UI panel** — "Overlays" panel in the KJ Controller interface with add/edit/delete forms, toggle switches, type-specific config fields
5. **Systemd service** (`desktop/overlay-display.service`) — runs the overlay engine as `overlay-display.service`

**Architecture:**
- KJ Controller backend writes overlay configuration to `data/overlays.json`
- Overlay engine polls the JSON file (mtime check every ~1s) and syncs overlay windows
- Each overlay has an independent `show_over_video` toggle: when off, overlays auto-hide during karaoke video playback
- `karaoke_playing` state is set by the play/control/stop routes and the `VLCManager.on_karaoke_end` callback

**Dependencies:** `pygame-ce`, `qrcode` (pip, overlay engine only)

## 2026-02-17 - KJ Controller: UI Redesign with Nomad Branding

Redesigned the KJ Controller web interface with Nomad brand identity, responsive layout, and modular file structure.

**Changes Made:**
1. **Brand Identity** — Applied Nomad color palette: pink (#ff5bb8/#ff7acc), gold (#ffdf6b), purple (#8b5cf6) on dark backgrounds (#0f0f0f/#1a1a1a) with ambient radial gradients
2. **Static Asset Extraction** — Separated inline CSS/JS into `static/style.css` and `static/app.js`; Jinja2 variable bridged via `window.KJ_CONFIG`
3. **Favicons** — Added favicon.ico, 16x16, 32x32, apple-touch-icon (from karaoke-gen)
4. **Responsive Design** — Three breakpoints: 1024px (tablet), 768px (single-column mobile), 480px (compact mobile)
5. **Title** — Changed from "KJ Remote" to "Nomad KJ Control"
6. **E2E Tests** — 44 Playwright browser automation tests covering layout, controls, interactions, responsive behavior, and brand colors

## 2026-02-17 - KJ Controller: Port 5000 → 80

Changed the KJ Controller Flask server default port from 5000 to 80 so it's accessible at `http://nomadpi.local` without specifying a port number.

**Changes Made:**
1. Updated default `flask_port` from 5000 → 80 in `config.py` and `app.py`
2. Updated Pi's `config.json` (which had an explicit `flask_port: 5000` override)
3. Restarted `kj-controller` service — confirmed responding on port 80

**Why:** With mDNS now broadcasting `nomadpi.local`, using the standard HTTP port means `http://nomadpi.local` just works — no need to remember `:5000`.

**Note:** The service runs as root, so binding to port 80 (privileged port) works without extra configuration.

## 2026-02-17 - mDNS / Avahi: `nomadpi.local` Hostname

Installed `avahi-daemon` and `libnss-mdns` so the Pi broadcasts its hostname via mDNS (multicast DNS). Any device on the same LAN can now reach the Pi at `nomadpi.local` without any DNS configuration — works automatically via Bonjour on macOS.

**Changes Made:**
1. `apt-get install -y avahi-daemon libnss-mdns`
2. Restricted Avahi to `eth0` only (`allow-interfaces=eth0` in `/etc/avahi/avahi-daemon.conf`) to avoid advertising Docker bridge IPs
3. Service enabled and starts on boot automatically

**Usage:** `ssh root@nomadpi.local`, `http://nomadpi.local`, `ping nomadpi.local`

**Why:** Provides reliable hostname-based access that survives IP changes — no need to know the current DHCP IP. Works without internet (pure LAN multicast). Complements static DHCP reservation (Layer 1) and Tailscale (Layer 3) as the middle layer of the connectivity strategy.

## 2026-02-17 - DietPi Upgrade: Debian 12 (Bookworm) → Debian 13 (Trixie)

Upgraded DietPi to the latest Debian release following the [official upgrade guide](https://dietpi.com/blog/?p=4014). The upgrade completed successfully but required several post-upgrade fixes.

**Upgrade process:**
- Ran `dietpi-update` which handled the Bookworm → Trixie transition
- Hit a dependency blocker: `chromium` and other GTK3 packages depended on `libgtk-3-0` which was renamed to `libgtk-3-0t64` in Trixie (64-bit time_t transition)
- Resolved by removing blocking packages (`apt-get remove -y lxde chromium chromium-browser galculator libgspell-1-2 libgtksourceview-4-0 libmousepad0 libvte-2.91-0 lxterminal mousepad xarchiver zenity zenoty chromium-common`) then retrying the upgrade
- Upgrade completed, system rebooted, ran `apt autopurge`

**Post-upgrade fixes required:**

1. **Reinstalled LXDE** — the desktop meta-package and all its components were removed during the GTK3 dependency cleanup
   ```bash
   apt-get install -y lxde
   ```

2. **LightDM autologin** — the upgrade switched from startx-based autologin to LightDM, but autologin wasn't configured
   ```bash
   # /etc/lightdm/lightdm.conf
   [Seat:*]
   autologin-user=root
   autologin-session=LXDE
   user-session=LXDE
   ```

3. **PAM root autologin** — Trixie's default `/etc/pam.d/lightdm-autologin` blocks root from auto-login. Commented out the blocking line:
   ```
   # Was: auth required pam_succeed_if.so user != root quiet_success
   ```

4. **LXDE autostart** — re-added `@xhost +SI:localuser:dietpi` (lost during LXDE reinstall)

5. **Python venv rebuilt** — Python upgraded from 3.11 to 3.13, breaking the kj-controller venv
   ```bash
   apt-get install -y python3.13-venv
   cd /opt/nomad/kjbox/kj-controller && rm -rf venv && python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   ```

6. **auto-deploy.sh execute permission** — git didn't preserve the execute bit (was `100644`, fixed to `100755` in git index)

7. **kj-controller service boot ordering** — changed `WantedBy=multi-user.target` to `WantedBy=graphical.target` in `/etc/systemd/system/kj-controller.service`. The service has `After=graphical.target` ordering, so it needs to be pulled in by `graphical.target` (not `multi-user.target` which starts earlier). This matches `rotation-display.service` which already used `WantedBy=graphical.target`.

**Post-upgrade state:**
- OS: Debian 13 (Trixie), DietPi v10.0.1
- Kernel: 6.12.62+rpt-rpi-v8
- Python: 3.13.5
- Display manager: LightDM (was startx/xinit)
- All services boot correctly: kj-controller, rotation-display, kj-autodeploy

**Known issue — USB touchscreen flapping:**
The Goodix touchscreen controller (WingCool Inc., VID `27c6` PID `0818`) on USB port 1-1.4 disconnects and reconnects every ~5 seconds under kernel 6.12. This was initially mistaken for an HDMI issue (the screen would blank during USB reconnect events). Likely a `hid-multitouch` driver regression or power management change in the new kernel. Workaround: use USB power-only cable (no data) for the touchscreen display. Touch input is not currently needed.

**Power supply issue:**
The Pi was also experiencing instability (crashes, unreachable via SSH) due to the power supply being shared with too many peripherals. Resolved by adding dedicated power supplies for peripherals.

## 2026-02-17 - WiFi Disabled

Disabled WiFi to save power and avoid confusion about which network interface is active. The Pi now connects exclusively via Ethernet.

**What was done:**
- `nmcli radio wifi off` — disables WiFi radio via NetworkManager (persists across reboots)
- `nmcli connection modify "Moominvalley" connection.autoconnect no` — prevents auto-connecting
- `systemctl disable wpa_supplicant` — prevents wpa_supplicant starting on boot
- Commented out wlan0 in `/etc/network/interfaces`

**Note:** NetworkManager manages wlan0 on this system, not ifupdown. Commenting out wlan0 in `/etc/network/interfaces` and disabling `wpa_supplicant` alone was NOT sufficient — NM brought WiFi back up on every reboot. The key command is `nmcli radio wifi off`.

**To re-enable:**
```bash
ssh nomadpi 'nmcli radio wifi on && nmcli connection modify "Moominvalley" connection.autoconnect yes'
```

## 2026-02-17 - Network Reconfiguration: Dual-Interface with Ethernet Priority

**Problem:** Pi was configured with Ethernet disabled (`AUTO_SETUP_NET_ETHERNET_ENABLED=0` in dietpi.txt) and WiFi as the sole network interface. The `/etc/network/interfaces` file had contradictory config — `iface eth0 inet dhcp` with stale static IP lines (`address 192.168.0.100`, `gateway 192.168.0.1`) that overrode DHCP. When connecting the Pi via Ethernet to a new GL.inet karaoke router (192.168.8.0/24), it couldn't obtain a DHCP lease on the new subnet.

**Root Cause:**
1. Ethernet was disabled in DietPi config (`AUTO_SETUP_NET_ETHERNET_ENABLED=0`)
2. `/etc/network/interfaces` had orphaned static IP lines under the `dhcp` stanza (leftover from original FoxTag device config), which interfered with DHCP
3. No metric was configured, so there was no defined priority between interfaces

**Changes Made:**
1. **Enabled Ethernet** — set `AUTO_SETUP_NET_ETHERNET_ENABLED=1` in `/boot/dietpi.txt`
2. **Cleaned `/etc/network/interfaces`** — removed stale static IP/gateway/netmask lines from both eth0 and wlan0 stanzas, leaving pure DHCP
3. **Added routing metrics** — eth0 gets metric 100 (preferred), wlan0 gets metric 200 (fallback)
4. **DHCP reservation** — configured GL.inet router to reserve `192.168.8.106` for Pi's Ethernet MAC (`E4:5F:01:B5:5D:C0`)

**Current Network State:**
- **eth0:** 192.168.8.106/24 via DHCP (GL.inet router, metric 100 — preferred)
- **wlan0:** 192.168.1.84/24 via DHCP (Ubiquiti home network, SSID: Moominvalley, metric 200 — fallback)
- **Tailscale:** 100.66.53.104 (reconnects automatically once Pi has internet)

**SSH config updated:** `nomadpi` alias now points to `192.168.8.106`, `nomadpihomewifi` alias points to `192.168.1.84`.

**Troubleshooting technique learned:** When a Pi has a static/stale IP on a different subnet, you can add a temporary IP alias on your Mac to reach it across the same physical switch:
```bash
sudo ifconfig en10 alias 192.168.1.100 netmask 255.255.255.0  # Add alias
# ... SSH in and fix config ...
sudo ifconfig en10 -alias 192.168.1.100                        # Remove alias
```

## 2026-02-16 - Rotation Display Rewrite: tkinter → Conky

**Problem:** tkinter cannot render a transparent background on X11 — every widget has a solid fill. The `-alpha` attribute makes the entire window (text included) uniformly transparent, washing out text readability.

**Solution:** Rewrote the rotation display using **conky** with a faux-transparency approach — a full-screen window with a scaled copy of the desktop wallpaper as the background image, so the overlay blends seamlessly with the desktop.

**Changes Made:**
1. **Created `desktop/rotation_data.py`** — standalone data-fetching script (extracted from old tkinter app). Called by conky via `${execpi}` (parsed exec), outputs conky markup to stdout. Supports `--stats` flag for header stats.
2. **Created `desktop/rotation.conkyrc`** — full-screen conky window (1920x1080) with wallpaper background image, XFT anti-aliased fonts, 30-second refresh.
3. **Created `desktop/rotation-bg.png`** — 1920x1080 background image generated from the 4K wallpaper source.
4. **Deleted `desktop/rotation_display.py`** — old tkinter app fully replaced.

**Display features:**
- Header stats: `Started: M/D HH:MM  N singers | N sung | N queued`
- Up to 10 queue entries with gold singer names and light gray song text
- Color-coded badges: NOW (green), NEXT (orange), WIP (red)
- Faux-transparent background using cropped wallpaper (full ARGB transparency doesn't work reliably on the Pi's physical display)

**Key decisions / lessons learned:**
- `${execpi}` not `${execi}` — the "p" variant parses conky `${color}`/`${font}` tags in script output
- `own_window_type = 'dock'` not `'override'` — PCManFM's desktop window in LXDE sits above override-type windows
- `DejaVu Sans` not `Helvetica` — Helvetica is not installed on DietPi
- ARGB transparency (`own_window_argb_visual`) doesn't work on the Pi's physical display even with xcompmgr compositor — faux transparency with a wallpaper background image is more reliable
- Full-screen window (1920x1080 at gap 0,0) avoids background alignment issues vs. a smaller positioned window

**Dependencies changed:**
- Added: `conky-all` (apt)
- Removed: `python3-tk` (no longer needed)

**Deployment:** See [archive/NOMADPI-DETAILS.md](archive/NOMADPI-DETAILS.md) § Rotation Display for full setup and troubleshooting.

## 2026-02-16 - Karaoke Rotation Display Overlay (initial)

**Changes Made:**
1. **Created rotation display** — fetches singer rotation from a public Google Sheet and displays the next 10 singers as a persistent overlay on the left side of the screen. No pip dependencies (stdlib only).
2. **Auto-deploy restart** — `kj-controller/auto-deploy.sh` now restarts the `rotation-display` systemd service on deploy (no-op if service isn't set up yet).

**Features:**
- Fetches Google Sheet data as CSV via `gviz/tq?tqx=out:csv` endpoint
- Filters out "Done" entries, shows current singer + next 9 in queue
- Color-coded status: red (Now Singing), gold (Up Next), gray (queued)
- 30-second auto-refresh with offline fallback (shows cached data)

**Setup on a new device:** See [archive/NOMADPI-DETAILS.md](archive/NOMADPI-DETAILS.md) § Rotation Display for full setup instructions.

## 2026-02-16 - Search UI: Full Filename & Folder Path

**Problem:** Catalog search results showed only parsed `Artist - Title` which was identical for popular songs with versions from many producers (e.g., 15+ "Queen - Killer Queen" entries).

**Fix:** Search results now show the full filename (preserving disc ID prefix like `SC8231-07`) and an abbreviated folder path below each result. Mount prefix (`/mnt/...`) is stripped for brevity; full path available on hover.

## 2026-02-16 - ZIP Playback Fix (MP3 + Permissions)

**Problem:** CDG+MP3 ZIP playback failed with two issues:
1. **Permission denied** — VLC runs as `dietpi` user but temp extraction dir was created by root with restrictive permissions
2. **Played wrong file** — VLC was given the `.cdg` file (no audio, instant "finish") instead of the `.mp3`

**Fix:**
1. Extracted files are now chmod'd world-readable (`S_IROTH | S_IXOTH | S_IRGRP | S_IXGRP` on dirs, `S_IROTH | S_IRGRP` on files)
2. `extract_and_get_mp3()` now returns the `.mp3` path — VLC plays it and auto-discovers the matching `.cdg` for lyrics overlay

## 2026-02-16 - External Media Catalog & Search

**Changes Made:**
1. **SQLite FTS5 catalog** (`catalog.py`) — indexes ~415K karaoke files from a text file list into a searchable SQLite database on the SD card. Full-text search across artist, title, and disc_id fields with prefix matching.
2. **CDG+MP3 ZIP playback** (`zip_playback.py`) — extracts CDG+MP3 ZIP files to a temp directory for VLC playback. Validates against path traversal attacks.
3. **Search UI** — added search input to the web UI with 300ms debounce, result rendering with artist (purple) + title + format badge (zip=yellow, mp4=blue), and click-to-play.
4. **New routes** — `GET /search`, `GET /catalog/stats`, `POST /catalog/build`
5. **Extended `/play`** — now accepts external media mount paths and handles ZIP file extraction

**New config keys** (in `config.json`):
- `external_file_list` — path to text file listing external media
- `external_media_mount` — mount point for external media drive

**Catalog build (one-time):**
```bash
curl -X POST http://localhost:5000/catalog/build \
  -H 'Content-Type: application/json' \
  -d '{"file_list_path": "/mnt/Nomad4TBOne/HyperMule/all-karaoke-files-2025.02.28.txt"}'
```

## 2026-02-16 - Directory Restructure

**Changes Made:**
1. **Consolidated to single directory** - App now runs directly from git clone at `/opt/nomad/kjbox/kj-controller/`
2. **Eliminated file-copying deploy** - Auto-deploy now just does `git pull` + restart (no separate deploy dir)
3. **Moved git clone** from `/opt/kjbox/` to `/opt/nomad/kjbox/` (everything under `/opt/nomad/`)
4. **Moved venv + config** into git clone directory (venv and config.json are gitignored)
5. **Removed old directories** - `/opt/nomad/KJController/` and `/opt/kjbox/` deleted
6. **Updated systemd services** - Both `kj-controller.service` and `kj-autodeploy.service` point to new paths
7. **Updated config.json paths** - media_index_path, log_file, youtube_cookies_file now under `/opt/nomad/kjbox/kj-controller/`

**Directory structure:**
```
/opt/nomad/
├── kjbox/                    # Git clone (app runs from here)
│   └── kj-controller/
│       ├── app.py
│       ├── templates/
│       ├── config.json       # gitignored
│       ├── media_index.json  # gitignored
│       ├── venv/             # gitignored
│       └── auto-deploy.sh
├── YTDownloads/
├── Tracks-PublicShare/
├── FillerMusic/
└── NomadBranding/
```

## 2026-02-15 - HDMI Audio Configuration

**Issue:** VLC and all ALSA apps could not play audio via HDMI. Error: `cannot open ALSA device "default": Unknown error 524` (-ENOTSUPP).

**Root Cause (multi-layered):**
1. The 7" touchscreen provides corrupt EDID data (invalid checksum), so the kernel couldn't detect HDMI audio capabilities
2. Created custom EDID override, but initial version was missing the HDMI Vendor Specific Data Block (VSDB)
3. Without VSDB, kernel treated the output as DVI mode (no audio), even though ELD data was populated
4. The `VC4_HDMI_RAM_PACKET_ENABLE` bit (bit 16 of `HDMI_RAM_PACKET_CONFIG`) was not set in DVI mode
5. On kernel 6.12+, the vc4-hdmi PCM device only exposes `IEC958_SUBFRAME_LE` format, requiring the `iec958` ALSA plugin

**Solution Implemented:**
1. Generated custom EDID at `/lib/firmware/edid/nomadpi-hdmi.bin` with HDMI VSDB (IEEE OUI 0x000C03)
2. Added `drm.edid_firmware=HDMI-A-2:edid/nomadpi-hdmi.bin` to `/boot/cmdline.txt`
3. Configured `/etc/asound.conf` with `iec958` plugin chain for HDMI audio
4. Set HDMI audio as default ALSA output

**Result:** HDMI audio works. VLC plays karaoke videos with audio via HDMI. USB mixer (Yamaha MG-XU) also available as `usbmixer` device.

## 2026-02-15 - VLC Media Player Configuration

**Issue:** VLC launcher icon wasn't working when clicked from desktop.

**Root Cause:** VLC refuses to run as root user for security reasons. Desktop environment runs as root on NomadPi.

**Solution Implemented:**
1. Created wrapper script at `/usr/local/bin/vlc-root-wrapper` that runs VLC as `dietpi` user
2. Added `dietpi` user to `video`, `audio`, and `render` groups
3. Used `xhost +SI:localuser:dietpi` for X11 access (added to LXDE autostart)
4. Modified `/usr/share/applications/vlc.desktop` launcher to use wrapper
5. Wrapper uses `sg render` for GPU access and creates `/run/user/1000` for XDG runtime

**Result:** VLC now launches successfully from desktop icon with video (hardware-accelerated) and audio.

## 2026-02-15 - Device Repurposed for Nomad Karaoke

**Changes Made:**

1. **System Configuration**
   - Changed hostname from `FoxTag1` to `nomadpi` (in /etc/hostname and /etc/hosts)
   - Updated Bluetooth device name from "FoxTag1" to "NomadPi" (via /etc/machine-info)
   - Updated device purpose from FoxTag sticker printing kiosk to Nomad Karaoke live events
   - Device now used for video playback and AV equipment connection at karaoke events

2. **FoxTag Application Removal**
   - Stopped and removed all FoxTag Docker containers (backend, frontend, cloudflared, watchtower)
   - Removed all FoxTag Docker volumes
   - Deleted /opt/foxtag directory (736KB)
   - Removed auto-cd to /opt/foxtag from ~/.bashrc
   - Removed disabled watchdog cron file

3. **Network Configuration Preserved**
   - Tailscale: Continues running (system-level, unaffected by cleanup)
   - Cloudflare Tunnel: Token saved in NETWORK-CONFIG-BACKUP.md for potential reuse
   - WiFi and local network access maintained

4. **Documentation Updated**
   - Created NETWORK-CONFIG-BACKUP.md with Tailscale and Cloudflare tunnel information
   - Renamed FOXTAG1-DETAILS.md to NOMADPI-DETAILS.md
   - Updated CLAUDE.md with new device name and purpose
   - Removed all FoxTag application-specific sections from documentation

5. **Retained Configuration**
   - All hardware specifications remain unchanged
   - Bluetooth, VNC, desktop environment configuration preserved
   - DietPi system configuration unchanged
   - /opt/nomad directory (19GB) untouched - contains NomadBranding and Tracks-PublicShare

**Current State:**
- Clean system with no running Docker containers
- Ready for Nomad Karaoke application installation
- All remote access methods working (Tailscale at 100.66.53.104, local at 192.168.1.84)

## 2026-02-15 - Auto-Deploy from GitHub

**Changes Made:**
1. **Created auto-deploy script** at `/opt/nomad/kjbox/kj-controller/auto-deploy.sh`
   - Polls `origin/main` every 60 seconds via `git fetch`
   - Compares local HEAD to remote; on difference: `git pull` + restart kj-controller
   - Auto-installs new pip dependencies if requirements.txt changes
2. **Created systemd service** `kj-autodeploy.service` (enabled, starts on boot)

**Workflow:** Edit code on Mac > `git push` > Pi auto-deploys within ~60 seconds

## 2026-02-15 - Multi-Folder Media Scanning & Descriptive Downloads

**Changes Made:**
1. **New YouTube download naming** - Files now saved as `{youtube_id}__{channel}__{title}.mp4` instead of random 8-char IDs
2. **Central media index** - Single `media_index.json` replaces per-video `.json` sidecar files
3. **Config file** - `config.json` defines download folder and media folders to scan
4. **Multi-folder recursive scanning** - Scans `/opt/nomad/YTDownloads/`, `/opt/nomad/Tracks-PublicShare/`, and `/root/kjdata/videos/`
5. **Path-based playback** - Play/delete by file path instead of opaque video ID
6. **Rescan button** - UI button to rescan all media folders
7. **Delete restrictions** - Only files in download folder can be deleted from UI
8. **Folder grouping** - Media list shows folder headers when files come from multiple folders
9. **New download location** - YouTube downloads now go to `/opt/nomad/YTDownloads/`

**Media count after initial scan:** 2,434 files from Tracks-PublicShare

## 2026-02-15 - KJ Controller Deployed

**Changes Made:**
1. **Deployed KJ Controller** to `/opt/kj-controller/`
   - Simplified app.py: removed SocketIO/external screen sync (no longer needed)
   - Added audio device switching (HDMI <> USB mixer) via dropdown in web UI
   - VLC instances run as `dietpi` user via `sudo -u dietpi env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000`
   - Flask server on port 5000, karaoke VLC on 8080, filler VLC on 8081

2. **Created systemd service** (`kj-controller.service`)
   - Runs as root, VLC subprocesses as dietpi
   - ExecStartPre grants X11 access (`xhost +SI:localuser:dietpi`) and creates `/run/user/1000`
   - After=graphical.target ensures X11 display is available
   - Restart=always with 5-second delay

3. **Installed dependencies**
   - Installed `python3.11-venv` package (was missing)
   - Created venv at `/opt/kj-controller/venv/`
   - Installed Flask, requests, yt-dlp

4. **Set file permissions**
   - Made `/root/kjdata/` readable by dietpi user (VLC needs access to video/music files)

**Verification:**
- Service running: `systemctl status kj-controller` shows active
- Two VLC processes running as dietpi user
- Flask API responding at `http://192.168.1.84:5000/`
- Web UI accessible from browser on local network

## 2026-02-15 - Bluetooth Configuration

**Changes Made:**
1. **Enabled Bluetooth Pairing**
   - Set `AlwaysPairable = true` in `/etc/bluetooth/main.conf`
   - Device now accepts pairing requests at all times

2. **Permanent Discovery Mode**
   - Set `DiscoverableTimeout = 0` in `/etc/bluetooth/main.conf`
   - Device stays discoverable indefinitely (no 3-minute timeout)
   - "NomadPi" is always visible to nearby Bluetooth devices

3. **Enabled Bluetooth Services**
   - Bluetooth service running and enabled on boot
   - Controller hci0 (Cypress Semiconductor) configured and active

**Current Bluetooth Configuration:**
- Name: NomadPi (updated 2026-02-15)
- Address: E4:5F:01:B5:5D:C3
- Always discoverable and pairable
- Bluetooth 5.0 support

## 2026-02-15 - Display Management & Watchdog Fix

**Issues Resolved:**
- Desktop environment was crashing every 60 seconds
- Multiple X sessions causing display inconsistencies between physical screen, HDMI, and VNC
- Chromium kiosk watchdog running inappropriately in desktop mode

**Changes Made:**
1. **Disabled Chromium Kiosk Watchdog**
   - Removed the kiosk watchdog cron file (previously at `/etc/cron.d/foxtag-watchdog`)
   - Watchdog was designed for kiosk mode only; incompatible with desktop mode

2. **Implemented Single X Session Enforcement**
   - Created `/usr/local/bin/startx-single` wrapper script
   - Modified `/boot/dietpi/dietpi-login` to use wrapper
   - Automatically prevents multiple X servers from running
   - Ensures all displays (physical, HDMI, VNC) stay synchronized

3. **Installed Additional Tools**
   - Installed `scrot` for remote screenshot capability

**Current Configuration:**
- Autostart Mode: 2 (Desktop autologin with LXDE)
- Single X server on `:0`
- All video outputs mirrored/synchronized
- VNC Service Mode shares physical HDMI display
- 7" touchscreen connected via HDMI-2 at 1920x1080 (via custom EDID override)

---

**Note:** This change log was initially generated on 2026-02-15 and is updated as the system configuration changes.
