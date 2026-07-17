# KJ Controller — Changelog

Dated entries, newest first. Each entry notes any required deploy steps.

---

## 2026-07-16 - Auto-text the next singer (v0.84.0)

**Deploy:** backend change (`routes.py`, `sing_store.py`) → **requires `systemctl restart kj-controller`** (interrupts playback — deploy in a maintenance window). Also frontend (`app.js`, `index.html`). No DB migration (reuses the `rotation_meta` key/value table).

- New opt-in toggle in the **Requests settings** modal: **"Auto-text the next singer"** (default off). Automates the KJ's manual habit of texting the up-next singer once the current song is safely underway.
- Behaviour: when the KJ presses **play on the top rotation entry (slot 1)**, a 20s timer is armed. If that same song is **still playing** when the timer fires, the "you're up next" SMS is auto-sent to the **slot-2** singer — but only if they have a mobile number on file and haven't already been texted for that entry. If the KJ stops the slot-1 song early (e.g. a no-show singer), nothing is sent, so no misleading text goes out.
- New route `POST /rotation/sms/auto-send` resolves the up-next singer, renders the default SMS template server-side, and re-validates the slot relationship + active playback server-side (target must be current slot 2, the played entry must still be slot 1 and its file still loaded/on screen) plus every eligibility guard (enabled, has-phone, not-already-sent, not-opted-out) as defence-in-depth. Manual `POST /rotation/sms/send` and the new auto-send now share a `_perform_sms_send` helper.
- New config field `auto_sms_next` on `GET/POST /rotation/requests/config`.

## 2026-07-16 - Auto-approve covers everything + "Try Another" version swap (v0.83.0)

**Deploy:** backend change (`sing.py`, `routes.py`) → **requires `systemctl restart kj-controller`** (interrupts playback — deploy between songs). Also frontend (`app.js`, `style.css`, `templates/index.html`) which takes effect on browser refresh.

- **Auto-approve now actually approves everything a guest KJ would want.** Previously it silently skipped multi-version (`kj_pick`) requests and never touched reorders, so an away-KJ still had to hand-approve most of the queue.
  - **Multi-version requests:** auto-approve now binds the request to a playable version automatically — walking the candidate snapshot best-first (the same order the admin picker marks ⭐ BEST) and taking the first that resolves. Even a song with no "good"/branded version still auto-binds to *something* playable; it only stays pending if literally nothing in the snapshot resolves.
  - **Reorders:** a singer's self-service reorder now applies immediately under auto-approve (it only shuffles that singer's own songs within slots they already hold, so there's nothing to vet).
- **New "Try Another" button** in Playback Controls (next to Pause/Restart/Stop). When a singer says the version that just started is a bad one, the KJ taps it to swap to a different **local** version of the same song instantly. Alternates come from a fast local-only search (`GET /playback/alternates`, new `local_only` path on `unified_search` — no ~8s Karaoke Nerds scrape); picking one relinks the rotation entry (playability-gated) then hot-swaps playback (mpv `loadfile replace`). Online-only versions still go through the 🔗 Link button.

## 2026-07-16 - Singer UI: your songs survive a refresh (v0.82.0)

**Deploy:** frontend-only (`static-sing/sing.js`, `static-sing/sing.css`, `templates/sing.html`) → auto-deploy pulls on the next browser refresh; **no service restart** and no playback interruption.

- A singer who refreshes their phone no longer loses sight of their submitted songs. The request ids + edit tokens were already persisted in `localStorage`; the app just never restored to them on reload. Now, on boot, if the device has songs for tonight, the singer lands straight on their **"Your songs tonight"** list (with the existing cancel / change / reorder controls).
- Added an always-visible **"🎤 My songs (N)" bar** on every other screen (landing, search, confirm) showing status at a glance (🎤 You're up! / 🎤 You're next / `#4 · ~10–15 min` / Waiting for KJ…). One tap opens the list. Hidden on the done screen (which is the list) and when the device owns no songs.
- **Stale-night pruning:** the event token is reused across nights and `localStorage` isn't cleared, so `/my-requests` night-scopes old ids out server-side; the client now prunes any id the server no longer returns so the count never shows phantom songs from a previous night. A fresh night cleanly shows the normal landing screen.

## 2026-07-09 - Singer self-service: change song + reorder your own (v0.79.0)

**Deploy:** backend change (`sing.py`, `sing_store.py`, `routes.py`) → **requires `systemctl restart kj-controller`** (interrupts playback — maintenance window). Also frontend (`sing.js`, `app.js`, CSS). Additive DB migration (`sing_requests.supersedes_request_id`) — safe on existing DBs. Builds on the v0.78.0 `edit_token` ownership.

- Singers can **change the song** of their own request from the "your songs tonight" screen. A not-yet-approved request updates in place (stays pending). A request already in the rotation creates a *change* the KJ approves — on approval the new song takes over the original's queue slot (and downloads if needed) and the original is removed.
- Singers with 2+ queued songs can **reorder their own songs** (Up/Down). This becomes a *reorder* the KJ approves; it only moves that singer's songs within the slots they already occupy — it never jumps other singers.
- Both route through the existing KJ requests panel: a change shows as "✎ Change …", a reorder as "↕ Reorder — <singer>'s songs", each with the normal Approve/Reject.

## 2026-07-09 - Singer self-service: cancel your own song (v0.78.0)

**Deploy:** backend change (`sing.py`, `sing_store.py`) → **requires `systemctl restart kj-controller`** (interrupts playback — deploy in a maintenance window). Also frontend (`sing.js`, `app.js`, CSS). Additive DB migration (`sing_requests.edit_token`) — safe on existing DBs.

- Singers can cancel their own request from their phone ("your songs tonight" screen). A per-request secret (`edit_token`, minted at submit and stored on the device) proves ownership, so a singer can only cancel their own songs.
- A not-yet-approved request cancels instantly. A request already in the rotation is **soft-cancelled**: it stays visible to the KJ marked "Cancelled by singer" (struck-through + CANCELLED badge) with a **Dismiss** button to remove it, or set it back to **Waiting** to restore.
- Edit-song and reorder-your-own are coming in a follow-up; cancel ships first.

## 2026-07-08 - Singer UI: reliable song search + hardened confirm (v0.76.0)

**Deploy:** frontend-only (`static-sing/sing.js`, `static-sing/sing.css`) → auto-deploy pulls on the next browser refresh; **no service restart** and no playback interruption.

- Fixes the likely cause of "this isn't the song I picked": the singer search
  now discards a slow earlier query's response so it can't overwrite a newer
  one (ports the KJ link-search generation guard + latest-owner rule; debounce
  raised 300 → 700ms to match the shared live-scraping backend).
- The "Searching…" hint shows the instant you type, before the debounce fires.
- Freshly-rendered result/version buttons are briefly inert (~300ms) so a tap
  aimed at the previous layout can't select a row that just appeared.
- Confirm screen redesigned for mis-tap safety: song title/artist dominant, an
  explicit source line, a "you searched" breadcrumb, and clear "Yes — send to
  the KJ" / "← Pick a different song" actions.
- Version list marks the Best version and KJ-trusted brands, and collapses
  noisy commercial downloads behind a toggle when a good option is available.

## 2026-07-06 - Stop keeping yt-dlp thumbnail litter next to downloads (v0.72.3)

**Deploy:** backend change (`media.py`) → requires `systemctl restart kj-controller`.

- YouTube downloads no longer fetch or keep a `.webp` thumbnail. `download_video`
  had `writethumbnail: True` and the post-download step moved the thumbnail next
  to the mp4 — but nothing in the app uses it, so every download left a stray
  image beside the video.
- The download tidy step (`relocate_download_sidecars`) now **deletes** image
  sidecars (`.webp/.jpg/.jpeg/.png/.gif`) and only moves non-image sidecars
  (e.g. `.info.json`) next to the final video — defense-in-depth in case a
  thumbnail is ever written anyway.
- Existing stray `.webp` files under `downloads/youtube/` were removed on-device.

## 2026-07-06 - Match downloaded YouTube songs to their Karaoke Nerds row again (v0.72.2)

**Deploy:** backend change (`media.py` `list_items`) → requires
`systemctl restart kj-controller`. No frontend edit — both search surfaces read the
repaired field from `/media`, so they fix on the next browser refresh after restart.

**Bug:** In rotation-entry linking (and the standalone Karaoke Nerds panel), an
already-downloaded YouTube song no longer matched its Karaoke Nerds row. It surfaced
a redundant **"From YouTube — unverified"** row for the local file *and* offered a
pointless **"DL & Link"** re-download on the matching community row (e.g. Bastille –
Pompeii `yt-3QhmiCHjRHE`, downloaded, still offered as a fresh YouTube download).

**Root cause:** The frontend keys its Karaoke-Nerds→local dedup/match on each media
item's `youtube_id`. `list_items()` only set `youtube_id` from the **legacy**
`{11id}__{channel}__{title}` filename parser. Since the v0.54.1 canonical-slug rename,
downloads are named `Artist - Title [yt-<vid>].ext` and carry their id in the
canonical `media_id` (`yt-<vid>`) — the legacy parser no longer matches, so
`youtube_id` was **null on every current download** (verified on-device: 1003 `yt-`
downloads, 0 with `youtube_id`). The dedup map was therefore empty for all of them.
This is the community-download twin of the NOMAD-master case fixed in v0.71.0 (#169).

**Fix:** `list_items()` now derives `youtube_id` from the canonical `media_id` (the
11-char id after the `yt-` prefix) whenever the legacy parse didn't already set it.
One source-of-truth change repairs both the KJ linking modal and the standalone KN
panel. Verified against live `/media`: all 1003 downloads gain the correct id, zero
edge cases. 3 new unit tests (canonical-slug derivation, legacy-parse precedence,
non-YouTube ids untouched); full suite green.

## 2026-07-06 - Tech-details modal follow-ups: real folder path, full filepath, CDG zips (v0.72.1)

**Deploy:** backend change (`mediainfo.py` CDG/zip handling) → requires
`systemctl restart kj-controller`. Frontend changes take effect on browser refresh.

Follow-ups to v0.72.0 from device testing:
- **Folder line now shows each file's actual containing directory** (dirname of the
  file path) instead of the media-root. Two copies of the same song in different
  subdirs (e.g. `youtube/` vs `NOMAD-720p/`) were both showing `/opt/nomad/downloads`;
  now they're distinguishable.
- **Technical-details modal shows the full file path** under the title, so you can tell
  which copy of a song you're inspecting.
- **CDG+MP3 zips and bare `.cdg` files are now described** instead of showing
  "ffprobe failed": a zip is reported as `CDG + MP3 (zip)` with the extracted audio
  track's codec/duration; a bare `.cdg` as graphics-only. The modal only shows Video/
  Audio rows that actually exist (no more "Video: none").

## 2026-07-06 - Fade cleanup, tech-details modal, catalog row unify, upload organizing (v0.72.0)

**Deploy:** backend change (new `/media/info` route + `MediaIndex.import_upload`) →
requires `systemctl restart kj-controller`. Frontend changes take effect on browser refresh.

**Playback + Library UX:**
- Removed the custom-duration fade entry box + big **Fade** button from Playback Controls —
  the presets (3/6/10/20s) are enough.
- Click a **format pill** (the now-playing `MP4` pill, or a Library/Catalog row's pill) to
  open a **technical-details modal**: container, video codec + resolution + fps, audio codec
  + sample rate + channels, overall bitrate, duration, file size. Backed by a new
  `POST /media/info` ffprobe probe (`mediainfo.py`), path-validated for both internal media
  folders and the external catalog mount.
- **Catalog (4TB SSD) results now render identically to internal-storage rows** — same
  clickable name (copies), format pill, Preview + Play buttons — via the shared
  `createMediaItemLi` renderer. No Edit/Delete for catalog rows (they have no
  media_id/is_download). Deleted the bespoke inline catalog rendering. A folder/path line is
  now shown under every row (internal + catalog).

**Upload flow fix (loose-file bug):**
- Browser `POST /upload` was saving files **loose in the download-folder root** with a
  sanitized name and no `[up-<id>]` token — unlike every other ingestion path. It now routes
  through the identity pipeline (`MediaIndex.import_upload` → `_finalize_download_identity`),
  landing the file in `downloads/upload/<Artist - Title [up-<hash>]>` with a media_library
  row. Content-addressed → idempotent (identical bytes resolve to the same identity instead
  of piling up timestamped duplicates).
- **Cleanup note:** one pre-existing loose file
  (`downloads/Drop Nineteens - Angel (Final Karaoke Lossy 4k).mp4`) predates the fix and is
  still loose; relocating it is a one-off (its media_library row already has id
  `up-0b58c1be0330`).

## 2026-07-06 - Recognise NOMAD masters; stop suggesting YouTube for them (v0.71.0)

**Deploy:** backend change → requires `systemctl restart kj-controller`.

**Why:** a song we already hold in the deliberate NOMAD master mirror
(`downloads/NOMAD-720p/`, e.g. `NOMAD-1272 - Maximo Park - By the Monument.mp4`)
was shown in rotation search under **"From YouTube — unverified"**, and
KaraokeNerds independently offered a **YouTube "DL & Link"** download for the
exact release we play locally. Root cause: `unified_search` discarded the
master's `disc_id` when it took the clean media_library artist/title, so brand
resolution never saw the `NOMAD-####` prefix → `priority_class="unknown"` →
routed to the YouTube-unverified bucket. And nothing cross-referenced KN NOMAD
tracks against the local masters.

**What:**
- **Recognise the master** — `unified_search` now preserves the parsed
  `disc_id` for master-source rows, so `resolve_brand` sees `NOMAD-####` →
  `('NOMAD', 'community')`. The master leads as **Best — NOMAD (community)**
  with a **Play/Link** button (local file, no download). Gated on
  `source == master` so non-master curated rows can't gain a false brand from a
  hyphenated title.
- **Suppress the redundant KN row** — new `_suppress_mastered_kn_tracks` drops a
  KN track whose canonical brand is NOMAD when a local master already covers the
  same (normalized artist, title). Other brands on the same song (commercial /
  other community) are left as genuine alternatives. Runs before both the KJ
  (flat) and singer (grouped) branches, so both search surfaces are fixed.

Scope: NOMAD masters only. +8 tests (helper unit tests + route integration).

---

## 2026-07-05 - Crash detection also works after a reconnect (v0.68.1)

**Deploy:** backend change → requires `systemctl restart kj-controller`.

**Why:** v0.68.0 detected engine death via the Popen exit code (`process.poll()`),
but when kj-controller restarts while mpv/VLC *survives* (they're spawned with
`start_new_session`), the coordinator **reconnects** to the existing process and has
no Popen handle — so `process.poll()` couldn't see a later crash. Verified live: after
a deploy, an AV1 crash was **not** auto-recovered. **What:** when there's no Popen
handle (reconnected), `_notify_if_dead` falls back to a **debounced liveness probe**
(mpv IPC `idle-active` / VLC HTTP status; 2 consecutive failures required, so a
transient blip during a restart isn't mistaken for a crash). The spawned path is
unchanged. +5 unit tests.

## 2026-07-05 - Video-player crash detection, auto-recovery & operator notification (v0.68.0)

**Deploy:** backend change → requires `systemctl restart kj-controller` to take
effect. Frontend (banner/CSS) applies on browser refresh.

**Why:** An AV1 video reliably SIGSEGVs the mpv engine (in libavcodec 6.1.1). Until
now mpv just became a zombie and *every* subsequent song failed to load until the KJ
manually hit Fix — the real cause of the 2026-07-02 on-stage failure. ~25% of recent
downloads are AV1. (Fixing AV1 at the source — upgrading mpv/ffmpeg/dav1d — is separate
Track B device work; see `docs/archive/2026-07-05-mpv-av1-crash-findings.md`.)

**What (engine-agnostic — mpv *and* vlc):**
- **Detection:** each engine fires a new `on_engine_died` callback, once, only when
  `process.poll()` confirms the process actually exited **and** it isn't an intentional
  shutdown — so a normal song-end or a transient IPC/HTTP blip never trips it. Runs at the
  top of the monitor loop, so a death-while-idle is caught too.
- **Auto-recovery:** the coordinator restarts the dead engine (reusing
  `restart_instances()`), on a thread, with a **restart-loop guard**: ≥3 crashes of the
  *same* song within 60s stops auto-restarting and escalates instead of thrashing.
- **Operator notification:** `/status` gains `player_alert` + `player_health_events`;
  `POST /player-crash/ack` dismisses. The KJ UI shows an amber, acknowledge-driven banner
  (distinct from the red audio-error one) with **Retry song / Switch engine / Dismiss**,
  plus one System-log line per crash for history.

**Files:** `mpv_manager.py`, `vlc.py`, `playback.py`, `routes.py`, `templates/index.html`,
`static/app.js`, `static/style.css`, `pyproject.toml`. 24 new unit tests.

## 2026-07-03 - SMS status pill in-button + details modal (v0.67.0)

**Why:** The delivery marker added in v0.65.0 sat *after* the SMS button as a
separate span ("✓ delivered 2:16 AM"), which widened sent rows and knocked the
action-button columns out of alignment.

**What:** The status now lives *inside* the SMS button so it stays a fixed,
minimal width and every row's buttons line up:
- Not sent → `✉ SMS` (pulses if the singer is up-next). Sent → `✓ 13:07`
  (delivered, green) / `✗ 13:07` (failed, red) / `• 13:07` (pending, amber),
  24-hour time. Fixed `min-width` sized to the widest state with `tabular-nums`
  so times never jitter; the separate marker span is gone.
- Clicking a **sent** button opens a details modal: recipient, the **exact
  message body**, send time, delivery status, an up-front failure reason when it
  bounced, and an expandable **Technical info** section (Telnyx message ID, raw
  status, error, ISO timestamp, sending device). A **Resend** button hands off to
  the normal compose/send panel. A not-yet-sent button still opens compose
  directly.
- New backend endpoint `POST /rotation/sms/detail` returns the last send's full
  detail for a row (keyed on the globally-unique rotation_entry_id).

**Deploy:** frontend + the new `routes.py` endpoint. The compact button/modal are
frontend — visible on next browser load once `static/…?v=` busts (version read at
startup → new query param after a restart, or a hard refresh). The details modal
needs the `/rotation/sms/detail` endpoint, which requires the next service
**restart** to exist; until then the button still renders and clicking a sent one
shows a graceful "could not load" until the restart. `kj-autodeploy.service` is
inactive → manual `git pull` on the device; do the restart **between shows**.

---

## 2026-07-03 - Split singer count/wait into two happiness-coloured pills (v0.66.2)

**Why:** The rotation row packed two facts into one pill (`×4 · 33m`, or a bare
`NEW` badge) whose colour meant "KJ, prioritise this singer" — 0-sung was green,
5+ was red. That's backwards from how a KJ actually reads the room: the colour
you want at a glance is *how happy is this singer likely feeling*. And new
singers showed only `NEW` with no wait time, even though they've been waiting
since their first song was entered — exactly the person you don't want to lose
track of.

**What:**
- Two separate compact pills per row: (1) **sing count** `×N`, always shown
  including `×0` (the `NEW` badge is retired); (2) **wait time**.
- Colours now consistently mean **green = happy / red = unhappy** from the
  singer's perspective:
  - Count: `<2` red · `2–4` yellow · `≥5` green.
  - Wait: `≤20m` green · `21–45m` yellow · `>45m` or unknown (`∞`) red.
- **New singers now get a wait pill too**, measured from when their first song
  entered the rotation — `rotation_store.get_first_entered_times()` (earliest
  `created_at` per singer). `routes._add_last_sang` → `_add_wait_pills` sets
  `last_sang_minutes` (tooltip wording) plus `wait_minutes`
  (= last-sang, else first-entered; group entries surface the longest wait).
- The Song Stats section's Sung pill uses the same convention so a given count
  reads identically everywhere.
- Tests: store unit (`TestGetFirstEnteredTimes`), route integration
  (`wait_minutes`), and an e2e tier regression (`TestSingerHappinessPills`)
  that renders every colour band in a real browser.

**Deploy:** touches `routes.py` + `rotation_store.py`, so a service **restart**
is required for the new `wait_minutes` field. `kj-autodeploy.service` is
currently inactive → deploy is a manual `git pull` on the device; **do the
restart between shows, never mid-song.** Until restart the frontend degrades
(old combined pill served from the old `app.js`).

**Note:** this flips the established rotation-row colour meaning (green used to
say "give this singer a turn"). Intentional, per the happiness framing above.

---

## 2026-07-03 - Fix false "audio device issue" banner in mpv mode (v0.66.1)

**Deploy:** backend change → requires `systemctl restart kj-controller` to take
effect (interrupts active playback — restart between songs).

**Why:** During a live show the mpv backend raised the red *"Audio device issue
detected"* banner on song after song while audio was actually playing fine — the
journal showed zero ALSA errors, flagged songs played to completion, and the same
files played instantly under VLC. The KJ had to fall back to the VLC backend
mid-show. Two bugs conspired, neither an actual audio fault:

1. **`_send_ipc` could return `None` spuriously.** mpv broadcasts async event lines
   (playback-restart, property changes, …) on *every* client connection, interleaved
   with command replies. The old code read only until the *first* newline, so an
   event arriving before the reply was returned instead — and
   `_get_property("time-pos")` then saw `None`. Worst right as a video starts (peak
   event chatter), i.e. exactly when the progress check runs.
2. **The progress check took a single 3-second sample.** A cold 4K file (freshly
   downloaded YouTube mp4) can sit at `time-pos == 0` for a few seconds while it
   starts, so one sample misread a slow start as a stalled/silent player and set the
   banner. Clicking **Fix** restarted mpv — interrupting playback that was actually
   fine — and the fresh instance could false-positive again, producing the on-stage
   loop.

**What:**
- **`_send_ipc` tags each command with a unique `request_id` and reads whole lines
  until the reply with that id arrives**, skipping async events (bounded by the 5s
  socket timeout).
- **`_verify_playback_progress` (extracted from the nested `verify()`) polls instead
  of single-sampling**: succeeds the instant `time-pos` advances, flags only if still
  stuck after `PLAYBACK_VERIFY_TIMEOUT` (10s). Genuine stalls are still caught.
- Banner wording softened to *"Playback may not be progressing…"* — accurate for what
  the check measures.

**Files:** `mpv_manager.py`, `templates/index.html`, `pyproject.toml`. Tests: 6 new
unit tests in `tests/unit/test_mpv_karaoke_player.py`.

## 2026-07-03 - Playback Controls: volumes stacked left, Seek long on the right (v0.66.0)

**Why:** The Playback Controls sliders had drifted into a confusing layout: Seek
sat in the left column with Karaoke Volume beside it and Filler Volume below.
Root cause — #154's `.fade-controls` auto-placed into column 2 of the
`.playback-controls` `auto 1fr` grid (next to the button group), displacing Seek
into the sliders' first cell. `TestPlaybackControlsUnified` had been red on `main`
because of this (no pytest CI to catch it).

**What:** Wrapped the three sliders in `.pc-sliders` — a dedicated 2-column grid
isolated from the button/fade rows above:
- **Left column** (`.pc-volumes`): Karaoke Volume over Filler Volume, stacked at
  equal width.
- **Right column**: Seek, the long bar, vertically centred against the volume
  stack.
- Each `.pc-slider-row` keeps the label-in-col-1 / slider-in-col-2 pairing.
- Scoped to ≥769px; below that everything stacks full-width as before.
- `TestPlaybackControlsUnified` rewritten to assert the new split layout (passes
  in both simple and advanced modes).

**Deploy:** frontend-only (HTML + CSS). Appears on next browser load once
`static/…?v=` busts (version read at startup → new query param after a restart,
or a hard refresh). `kj-autodeploy.service` is inactive → manual `git pull` on
the device; no restart strictly required for CSS/HTML, but the `?v=` bump only
updates after a restart (else hard-refresh the KJ browser).

---

## 2026-07-03 - SMS delivery status on rotation rows + up-next nudge (v0.65.0)

**Why:** A live-show SMS outage (the sending number was never linked to the
approved 10DLC campaign → every send hard-rejected with carrier error `40010`)
went unnoticed for ~2 weeks because the rotation row showed an optimistic
"sent 11:39 PM" marker whenever an SMS was *accepted* by Telnyx (HTTP-2xx =
queued). The real outcome arrives later via the Telnyx delivery-receipt (DLR)
webhook, which writes `delivered` / `delivery_failed` to `sms_log.status` — but
the frontend only styled the send-time `failed` string, never the DLR
`delivery_failed`, so carrier rejections rendered as a plain neutral "sent".

**What:**
- `routes.py` (`_add_sms_status`): add `last_error` to each row's `sms` block so
  the failure tooltip can explain *why* (e.g. `40010 Not 10DLC registered`,
  opted-out, carrier reject). Docstring updated for the DLR statuses.
- `static/app.js`: `smsDeliveryState()` collapses the raw status into
  delivered / failed / pending (the failure set now includes the DLR statuses).
  Marker renders ✓ delivered (green) / ✗ failed (red) / ⋯ sent (amber); the SMS
  button becomes a red **Retry** on failure.
- Up-next nudge: when the up-next singer has a phone but no SMS has been sent
  yet, the SMS button pulses (subtle brand-pink glow; respects
  `prefers-reduced-motion`).
- Tests: `/rotation` surfaces `last_status` + `last_error` for delivered and
  `delivery_failed` DLRs.

**Deploy:** frontend + a tiny additive `routes.py` field. The ✓/✗/⋯ colours are
frontend — they appear on the next browser load once `app.js?v=` busts (version
read at startup → new query param after a restart, or a hard refresh). The
`last_error` tooltip text needs the next service **restart** to take effect.
`kj-autodeploy.service` is currently inactive → deploy is a manual `git pull` on
the device; **do the restart between shows, never mid-song.**

---

## 2026-07-03 - Version renumber: 0.64.2 (avoid collision with concurrent #154)

**Why:** PR #155 (the source-id token fix below) and PR #154 (fade-out controls on
one compact line — CSS only, no changelog entry) both merged as `v0.64.1` from
concurrent branches. Renumber HEAD to `0.64.2` so the deployed version is unique and
strictly later. No code change — version string only. The `v0.64.1` label now refers
to the #154 CSS tweak; the token fix ships as `0.64.2`.

---

## 2026-07-03 - Fix: source-id token leaking into rotation song names (v0.64.2, was v0.64.1)

**Why:** During a live show, some rotation entries displayed the raw media-id slug
token in the song name, e.g. `Vienna [yt-I8wu3lLbB0k] - Billy Joel` and
`Sabrina Carpenter - Go Go Juice [yt-q0rHs_xFuSk]`. Root cause: the P1–P4
download-naming rename (v0.54.1) renamed downloaded files to the canonical slug
`Artist - Title [media_id].ext`, but `unified_search()` still derived a search
result's artist/title by calling `parse_karaoke_filename()` on the raw on-disk
filename. That parser splits on ` - ` and never strips the trailing `[media_id]`
token, so the token stuck to the **title**. The polluted title then flowed into
both the singer-facing pick (`_format_song_text` → `Title - Artist`) and the
KJ-side add/link (`Artist - Title`), which is why the same bug appeared in two
orderings. Pre-rename YouTube files (`VIDEOID__…`) never hit this, and SSD-library
results (pre-parsed via external catalog) were unaffected. Note the `media_library`
rows themselves were always clean — this was purely a display-build defect.

**What:**
- `unified_search()` downloaded-media branch now prefers the clean, curated
  `media_library` identity (looked up by path) for a file's artist/title. The scan
  already resolves the real artist/title into `media_library`, so search reuses it.
- When no `media_library` row exists, it falls back to
  `parse_karaoke_filename(naming.strip_media_id_token(filename))` so the token is
  stripped before the deterministic parse. The `display_name` fallback is likewise
  token-stripped.
- Fixes the source that feeds both the singer `/sing/search` and KJ
  `/rotation/search` result sets, so no polluted names reach either consumer.

**Deploy:** Backend change (`routes.py`) — requires a `kj-controller` restart to take
effect. Safe to deploy between singers / after the show. Existing polluted rotation
rows (`song_artist` free-text) are display-only and can be corrected in-app or left to
age out; this fix prevents new ones.

---

## 2026-07-02 - Reliable Fade Out on both engines + selectable durations (v0.64.0)

**Why:** The Fade Out button was "only sometimes clickable." It was enabled only when
`/status.state` was exactly `playing`/`paused`, but that live state flickers to
`stopped`: on the VLC renderer, VLC's HTTP status reports `stopped` transiently for ~5s
after each play/seek (the end-of-song monitor guards this, but `get_status()` — read by
the button — did not), and a status HTTP blip (`_send` timeout → `None`) also reads as
`stopped`. mpv is steadier but not immune. Separately, the fade length was hardcoded to
3s; the KJ wants to choose it per song.

**What:**
- **Availability (renderer-agnostic):** Fade / Restart / Stop now gate on whether a song
  is *loaded* (`current_playing_path`, a stable signal set on play and cleared only by a
  real stop/fadeout or the guarded monitor) instead of the flaky live state.
- **VLC state steadied at the source:** `VlcKaraokePlayer.get_status()` now mirrors the
  monitor's 5s post-play/seek guard and treats an HTTP blip while a song is loaded as
  last-known `playing`, so its reported state matches mpv's reliability (also stops the
  now-playing pill flickering "Stopped").
- **Selectable durations:** preset buttons 3 / 6 / 10 / 20s + a custom 1–60s field.
  `POST /control` accepts an optional `duration_s` (default 3.0, clamped `[0.5, 60]`),
  forwarded through the polymorphic `coordinator.fadeout` to both engines. A shared
  `fade_steps()` (`karaoke_player.py`) scales the volume ramp with duration so long fades
  are smooth on both renderers. The frontend's post-fade reset now scales with the chosen
  length (was a fixed 3.5s) so buttons don't re-enable mid-fade.

**Deploy:** Backend change (`routes.py`, `vlc.py`, `mpv_manager.py`, `karaoke_player.py`)
→ requires `sudo systemctl restart kj-controller` (interrupts active playback). Ship
**off-show**. (The frontend gating half alone is a no-restart change, but this ships as
one unit.)

---

## 2026-06-30 - Pair a loose CDG with its sibling MP3 at download (v0.48.0)

**Why:** v0.46.0 (#124) *blocks* a silent bare `.cdg`, but for brands that store a
CDG's graphics and audio as two separate Drive files (e.g. Sandell Karaoke), the
divebar index exposes them as independent track rows — so clicking the `cdg` row
downloads a graphics-only file that is then rejected, leaving the song unobtainable.
~2,665 such loose CDG+MP3 pairs are mirrored in GCS.

**What:**
- When a divebar track's `format` is `cdg`, resolve its sibling audio via the divebar
  API (`divebar.find_sibling_audio` — re-search the song, match the same brand and the
  same `drive_path` basename with the extension swapped), download **both** the `.cdg`
  and the `.mp3`, and package them into a single `divebar__*.zip`
  (`media.download_cdg_pair`). The zip passes the existing `cdg_zip` gate and plays
  exactly like any other CDG — no `/play` changes needed.
- A shared resolver (`routes._resolve_divebar_spec`) centralises the single-vs-paired
  decision across all three divebar enqueue sites (`/divebar/download`,
  `/rotation/download-and-link`, `approve_sing_request`); the download worker dispatches
  `pair` items to `download_cdg_pair`. If no sibling audio exists in the mirror, the
  request **fails fast** (422 / clear error) and no file is created.

**Deploy:** Backend change → requires `sudo systemctl restart kj-controller` (interrupts
active playback). Autodeploy is OFF — pull + restart manually, off-show.

---

## 2026-06-30 - Block silent bare .cdg + show file type/extension (v0.46.0)

**Why:** A bare `.cdg` (graphics-only, e.g. `divebar__SDK - ABBA - Dancing Queen.cdg`)
is a first-class indexed media type, so it showed in Available Songs and was
link/play-eligible — but playing it on the main screen is **silent** (embarrassing
mid-show). The old gate even allowed it (ffmpeg's `cdgraphics` demuxer presents a
`.cdg` as a video stream, so the verdict's video-only check passed), and `/play`
didn't gate at all. The UI also showed no file type/extension, so a KJ couldn't tell
a `cdg-zip` from a bare `.cdg` from an `.mp4` before clicking.

**What:**
- `classify_kind` now returns `cdg_bare` for a standalone `.cdg`. A bare `.cdg` is
  playable/linkable **only** when a same-stem audio file sits beside it (`X.cdg` +
  `X.mp3` — `playability.sibling_cdg_audio`). The playability verdict folds `cdg_bare`
  into the `cdg_zip` branch, so `/rotation/link` **and** downloads reject an audioless
  `.cdg` automatically. `/play` gains the matching guard (400 + clear message; when a
  sibling exists, mpv plays the `.cdg`+sibling, VLC plays the sibling and auto-finds
  the `.cdg`).
- Available Songs rows now show a `kind · .ext` badge (`cdg-zip · .zip`, `mp4 · .mp4`,
  …) via `utils.media_type_label`; a no-audio `.cdg` gets a red "no audio" tag, is
  dimmed, and its click no longer attempts a silent play.
- The preview modal header shows the file type + extension; a bare `.cdg` with a
  sibling previews as CDG, without one shows "Graphics-only .cdg — no audio track."

**Deploy steps:** Backend change → requires a service restart (interrupts active
playback); deploy off-show: `git pull` + `sudo systemctl restart kj-controller`, then
hard-refresh the KJ browser. No new dependencies.

---

## 2026-06-30 - Playability checker made deterministic (v0.45.0)

**Why:** A manual review of 166 files flagged by the full-library playability sweep
found **~90% were false positives** (88% of flagged video, 91% of flagged CD+G). Whole
commercial discs were flagged 100% yet played fine. The checker's verdict leaned on
signals that are environment-fragile rather than deterministic, so it could not be
trusted as a delete list — and because the CD+G verdict also feeds the live
link/upload/download gates, the box was false-rejecting playable commercial CD+G discs.

**Root causes (all fixed):**
- **Render frame-capture was a hard gate.** Headless-Xvfb pixel-proof is too
  environment-sensitive (51 video false positives). Now a **diagnostic only** — it never
  gates `overall_ok`; per-renderer "playable" flags are still recorded for the VLC-vs-mpv
  matrix.
- **`ffmpeg -xerror`** aborted on the *recoverable* cdgraphics `tile is out of range`
  warning, failing valid discs (~57 CD+G false positives). Removed; decode `ok` now means
  ffmpeg ran to completion (rc == 0). Recoverable warnings are recorded, never gate.
- **180 s decode timeout** was too short under the `nice -n19 ionice -c3` throttle for long
  files (15 false positives). Deep-decode timeout now scales with duration.
- **CD+G audio detection was `.mp3`-only**, missing `.m4a`/`.wav`/etc. (7 false positives).
  Now accepts all common audio formats.
- **Fragile unzip** crashed on a single corrupt member (`zlib Error -3`) where the good
  `.cdg`/`.mp3` extract fine (6 false positives). Now falls back to system `unzip`, which
  skips the bad member.

**Also:**
- **mpv CD+G render fixed.** The mpv diagnostic now attaches the companion audio
  (`--audio-file=`, mirroring production `loadfile` + `audio-add`) so mpv has a timeline to
  seek into — the earlier "mpv can't render CD+G" finding was a test artifact of handing it
  a bare, timeline-less `.cdg`.
- **Download gate quarantines, never deletes.** A rejected download (and its sidecars) is
  moved to a `_playability_quarantine/` subdir (skipped by `scan()`) instead of being
  deleted — an automated verdict can be wrong, so it must never irreversibly destroy a file.
  Link/upload still hard-block (recoverable 422).
- **Batch is decode-only by default** (fast, no Xvfb). The VLC-vs-mpv render matrix is now
  opt-in via `--render-matrix`. The report's "unplayable" list keys on the deterministic
  verdict.

Validated against the 166-file review set: the fixed checker flags only the genuinely-broken
files (truncated downloads, no-audio/no-stream, corrupt zips) and passes the rest.

**Deploy:** backend change — requires `git pull` + `sudo systemctl restart kj-controller`
(interrupts playback; do off-show). The pending full-library batch should be re-run with the
fixed, decode-only checker.

---

## 2026-06-30 - In-browser file preview playback (v0.44.0)

**Why:** When several versions of a song surface in the rotation "Link song"
search (local downloads, KaraokeNerds/YouTube, divebar GCS mirror), there was no
way to audition them before linking — the only way to hear/see a file was to play
it on the device's HDMI/PA, impossible mid-show.

**What:** A `▶` preview button on every link-search row and Available Songs row
opens a modal that plays the file **in the browser** (small video render + audio,
with seek), without ever touching the primary player or device A/V output — safe to
use while a singer is performing. Delivery is chosen per file:

- H.264/AAC mp4 + webm (local or GCS) → HTTP byte-range to `<video>` (zero CPU).
- CDG zips → inner `.mp3` + `.cdg` rendered in a `<canvas>` synced to `<audio>`
  (zero CPU, perfect seek) via the new dependency-free `static/cdg.js`.
- audio → byte-range `<audio>`.
- mkv/avi/odd-codec mp4 → ffmpeg→HLS transcode (≈480p, niced, single-job),
  **cached on disk** so any file transcodes at most once, ever.
- YouTube candidates → IFrame embed; divebar GCS files → fetched once into the
  cache then handled like a local file.

New modules: `preview.py` (`PreviewService` + `parse_range`), `preview_cache.py`,
`preview_transcode.py`; frontend `static/preview.js`, `static/cdg.js`,
`static/vendor/hls.min.js`. New routes under `/preview/*`. New config:
`preview_cache_dir`, `preview_cache_max_bytes` (8 GiB LRU), `preview_transcode_height`,
`preview_transcode_preset`.

**Deploy steps:** Backend change → requires a service restart (interrupts active
playback); deploy off-show: `git pull` + `sudo systemctl restart kj-controller`.
Frontend assets are served versioned (`?v={{ app_version }}`); hard-refresh the KJ
browser if it had the old page cached. Requires `ffmpeg`/`ffprobe` on the device
(already present). The preview cache directory is created on first use under
`<download_folder>/.preview-cache` unless `preview_cache_dir` is set; point it at the
roomiest mount (e.g. the 4TB SSD) if desired.

---

## 2026-06-29 - Rotation "Link song" search row polish (v0.43.2)

**Why:** Follow-up polish to the v0.43.0 search dropdown. The format pills
(e.g. `cdg+mp3`) and brand codes (e.g. `KV`) didn't line up into clean columns —
local rows sat 8px out of step with Karaoke Nerds / Divebar rows because each row
type used different inter-column spacing. One "Link" button also rendered smaller
than the rest (its font-size wasn't rem-anchored), and the ⭐ trusted-brand marker
had no explanation on hover.

**What:**
- **Aligned tag columns.** Format and brand pills now occupy fixed-width, right-aligned
  slots (`rotTagsHtml`) so they form consistent columns across every row type and group.
  Normalized the row flex spacing in the dropdown (`.kn-track` / `.kn-local-match` share
  one `gap`; the actions column's stray `margin-left` is dropped) so the slots land at the
  same x regardless of source.
- **Uniform controls.** Unified `.kn-play-btn` / `.kn-download-btn` sizing
  (rem-anchored font-size, shared padding/min-width) — all Link / DL & Link buttons now
  match. Fixed the one-off smaller button.
- **Tooltips.** ⭐ now has a "Reliably high-quality brand (KJ-trusted)" title and the
  **Best** pill a "Best available version for this song" title.
- Fixed a stale `test_css_loaded` e2e assertion left by the v0.43.1 cache-bust (the
  stylesheet `href` now carries a `?v=` query string).

**Deploy:** Frontend-only (JS/CSS) + version bump. `git pull` + restart busts the
cache via the new `?v=0.43.2`; takes effect on next browser refresh.

---

## 2026-06-29 - Rotation "Link song" search UX overhaul (v0.43.0)

**Why:** Linking a song to a rotation entry was slow and noisy during live shows:
the initial search didn't fire until you typed a throwaway character; there was no
"searching" feedback so you'd mash space and waste scrapes; clicking anywhere off the
results list wiped them (forcing a multi-second re-scrape); file type and download
source were shown inconsistently; popular tracks buried the good versions under 50+
low-quality commercial cover-band downloads; and the "Unknown" section lumped the
trusted 4TB-SSD library together with messy YouTube downloads.

**What (all in the rotation Link/Add search dropdown):**
- **Instant initial search.** Opening Link now fires the search immediately. Root cause
  of the old lag was a global "close on outside click" handler that fired on the *same*
  click that opened link mode and cleared the pending search timer — that handler is
  removed (#1, #4). The list now persists until Cancel / Esc / a completed link.
- **Dedicated Search button + "Searching…" indicator** beside the song input, so you can
  re-trigger on demand and always see when a search is in flight (#2).
- **File-type badge on every row** (mp4 / cdg+mp3 / …) and a **source badge** (GCS / Drive
  / YouTube) beside every "DL & Link" button so you know what you're downloading (#3).
- **Collapse low-quality commercial noise.** Non-"KJ-stated" commercial *download* options
  fold under a "▸ N more commercial versions to download" expander whenever a good option
  (community, or a stated-commercial brand) is already visible. The trusted set is the
  single source of truth already in `version_priority.py` (`COMMERCIAL_STATED` /
  `COMMUNITY_STATED`); stars now mark stated brands (#5A).
- **Meaningful groups instead of "Unknown."** Unknown-brand local files are split by the
  backend (`local_grouping.py`) into "Library — Karaoke - Digital/Active|Dead|…" (by SSD
  folder) and "From YouTube — community brand" vs "From YouTube — unverified" (by filename
  brand / our GCS-mirror naming / cross-reference against community brands in the same
  search) (#5B). Recognized-brand local files still sort into Community/Commercial.

**Deploy:** kj-autodeploy is OFF — deploy manually (`git pull` + service restart). This
release includes backend changes (`routes.py`, `version_priority.py`, new
`local_grouping.py`), so a **service restart is required** and will interrupt active
playback — restart between singers.

---

## 2026-06-04 — Fix stale results in rotation link search (v0.35.1)

**What changed**
- `doRotationSearch` (`static/app.js`) now bumps `rotSearchGen` at the start of every
  query, so an earlier, slower in-flight `/rotation/search` request supersedes itself
  and is discarded instead of rendered. Fixes the bug where the link autocomplete would
  show results for an *earlier* partial query (e.g. `frank`) after you'd finished typing
  a fuller one (e.g. `frank might`) — caused by out-of-order responses, since the live
  Karaoke Nerds scrape has highly variable latency (broad queries return more, slower).
  The latest query now always wins regardless of which response lands last.
- Input debounce raised 300ms → 700ms. `/rotation/search` does a live Karaoke Nerds
  scrape, so a longer debounce avoids firing intermediate queries on every keystroke
  (fewer wasted scrapes). Correctness is guaranteed by `rotSearchGen`, not the debounce.
- New e2e regression test `TestRotationSearchRace` reproduces the race in a real browser
  (shims `window.fetch` so a broad query resolves after a narrow one) — fails without the
  guard, passes with it.

**Deploy steps**
- Frontend + version-bump only (no Python behavior change). Auto-deploy pulls the code;
  the version bump (0.35.0 → 0.35.1) busts the `app.js?v=` cache so browsers load the fix
  on next refresh. No service restart required.

## 2026-06-04 — Unified song-text normalization + fuzzy matching

**What changed**
- New `text_normalize.py` (+ `static/text_normalize.js` twin, node-parity-tested) is
  the single source of truth for search normalization: `&`↔`and`, numbers↔words,
  diacritics/Unicode, `feat.`, abbreviations (pt/vs), apostrophes/punctuation. Every
  search call site (catalog FTS index+query, the `media_trigram` index,
  `routes.unified_search` local filter, `routes._normalize_song_key` grouping, and the
  frontend `app.js`) now routes through it. Fixes the "Simon and Garfunkel" vs
  "Simon & Garfunkel" link bug.
- rapidfuzz fuzzy fallback for typos (token-overlap gated, bm25-ranked `media_trigram`
  candidates). **New dependency: `rapidfuzz` (in `requirements.txt`).**
- `NORMALIZER_VERSION` stamp + `index_is_stale()` startup warning; `scripts/reindex_catalog.py`;
  recall metrics harness (`scripts/search_metrics.py`); 975 real rotation-history queries
  captured for regression testing. Decision: bracketed qualifiers like `(Live)` are NOT
  stripped, so version grouping is more granular than before.

**DEPLOY PROCEDURE (required for any normalizer change — order matters)**
Auto-deploy only `git pull`s; it does NOT install deps, reindex, or restart. On each device:
1. `git pull` (auto-deploy handles this).
2. **Install new deps in the venv** — `catalog.py` imports `rapidfuzz` at module load,
   so the service will NOT restart without it:
   `/opt/nomad/kjbox/kj-controller/venv/bin/pip install -r requirements.txt`
3. **Reindex** (full rebuild of FTS + trigram over ~398K rows; `reindex_catalog.py` calls
   `init_schema()` first, so it creates `media_trigram`/`catalog_meta` on a pre-existing DB):
   `/opt/nomad/kjbox/kj-controller/venv/bin/python scripts/reindex_catalog.py`
4. **Restart** (interrupts playback): `sudo systemctl restart kj-controller`
   (backend change — must restart to take effect; service runs on 127.0.0.1:5001).
Until reindexed, the query normalizer and the index disagree → degraded search + a stale-index
log warning.

**Deployment status**
- NomadPC: DEPLOYED + verified live 2026-06-04 (398,446 rows reindexed; `/search` for
  "Sound Of Silence - Simon and Garfunkel" returns the "& Garfunkel" rows).
- NomadPi: NOT yet deployed — run the procedure above if it's used for shows.
