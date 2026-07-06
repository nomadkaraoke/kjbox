# Plan: Video-player crash detection, notification & auto-recovery (+ media-stack upgrade)

**Created:** 2026-07-05
**Branch:** feat/sess-20260705-1704-player-crash-recovery
**Status:** DONE — Track A shipped (v0.68.0 #165 / v0.68.1 #166); Track B resolved (see below)
**Background:** [2026-07-05-mpv-av1-crash-findings.md](2026-07-05-mpv-av1-crash-findings.md)

> **Track B outcome (2026-07-05):** The root cause was **not** the mpv/ffmpeg/dav1d versions —
> it was the DFSG **free** `intel-media-va-driver` having broken AV1 decode. Fix = swap to
> `intel-media-va-driver-non-free` (one package, no reboot); AV1 now **hardware**-decodes with
> no crash. The self-contained mpv build and the full-system/kernel upgrade in the steps below
> proved **unnecessary** for the crash (the upgrade is deferred as hygiene, pending physical
> access to the box). See the RESOLUTION section of the findings doc, CHANGELOG 2026-07-05, and
> MINIPC-SETUP § 3.7.

## Overview

An AV1 video reliably crashes the mpv karaoke engine (SIGSEGV in libavcodec 6.1.1), and today
nothing detects or recovers from it — mpv becomes a zombie and **every subsequent song fails**
until the KJ manually hits Fix. This was the real cause of the 2026-07-02 on-stage failure.
~25% of recent downloads are AV1, so it will keep happening.

Two independent tracks:

- **Track A — Robustness (code):** Detect when the active engine (mpv *or* vlc) dies, **auto-restart
  it**, and **tell the KJ in the web UI** what happened (with Retry / Switch-engine actions) so a
  crash becomes a ~2s blip + an informative notification instead of a dead show. Valuable for
  *any* future crash, not just AV1.
- **Track B — Root cause (device ops):** Upgrade the media stack on NomadPC (mpv 0.41, latest
  ffmpeg, dav1d 1.5.3) so AV1 decodes without crashing; evaluate `--hwdec=no` as an immediate
  mitigation. Verify by playing the AV1 files through the app.

Track A is the safety net and ships first via the normal code flow. Track B is the cure for AV1
specifically and is a carefully-staged device change (production hardware).

## Requirements

**Track A**
- [ ] Detect genuine engine *death* (process exited), distinct from a normal song end (EOF) and
      from transient IPC/HTTP blips — for both mpv and vlc.
- [ ] Detect death whether a song was playing **or the engine died while idle**.
- [ ] Auto-restart the dead engine (reuse `PlaybackCoordinator.restart_instances()`), off the
      monitor thread (it blocks ~4–6s).
- [ ] Restart-loop guard: if the engine dies again within a short window / N times, stop
      auto-restarting and escalate the notification ("keeps crashing — try the other engine").
      Never auto-switch engines silently (KJ stays in control).
- [ ] Remember what was playing at crash time (for the notification + Retry).
- [ ] `/status` exposes `player_health` (engine, alive, restart_count, last_event) + a bounded
      `player_health_events` list.
- [ ] KJ web UI: an amber, **acknowledge-driven** crash banner (distinct from the red audio-error
      banner) with **Retry**, **Switch engine**, **Dismiss**; plus `log()` history entries.
- [ ] `POST /player-crash/ack` to dismiss (server-side, so it persists across refresh / multiple
      screens).
- [ ] No i18n (kjbox is English-only). Tests first (TDD). No regressions to the v0.66.1 fix.

**Track B**
- [ ] mpv 0.41.0, latest ffmpeg, dav1d 1.5.3 available to kj-controller on NomadPC.
- [ ] The 4 known AV1 files play **through the app** to completion with **no SIGSEGV** (STAT stays
      `SLsl`, no new coredump).
- [ ] Reversible (rollback to current versions documented).
- [ ] Documented in `docs/MINIPC-SETUP.md` + `docs/CHANGELOG.md`.

## Track A — Technical approach

Engine-agnostic death detection feeding a coordinator-level recovery + notification path. Hook
points confirmed by codebase exploration:

**1. Death detection in each engine** (`mpv_manager.py`, `vlc.py`)
- Both engines already hold a Popen handle (`self.process`). The strongest, unambiguous "it
  crashed" signal is **`self.process.poll() is not None`** (child exited) — use it to confirm
  death before acting, so transient IPC/HTTP blips don't trigger a false restart.
- Add `self.on_engine_died` callback attribute to both (mirror the existing `on_karaoke_end`
  passthrough).
- **mpv** (`_monitor_via_events`, ~mpv_manager.py:476-513): the `except OSError` (connect refused)
  and `if not chunk` (recv EOF) branches are where death surfaces. On either, if
  `self.process.poll() is not None` → fire `on_engine_died` (once; don't spin). Distinguish from
  normal end: normal end is the `end-file`/`reason=eof` *event on a live socket*, not a transport
  failure.
- **vlc** (`monitor`, ~vlc.py:391-421): where the HTTP probe returns `None`, confirm with
  `self.process.poll() is not None` (+ optionally N consecutive failures) → fire `on_engine_died`.
  Normal end is a live `state=='stopped'` HTTP response.
- Run liveness **independent of `self.active`** so a death-while-idle is caught (add a lightweight
  `process.poll()` check to the monitor loop that runs even when idle).

**2. Recovery + events in the coordinator** (`playback.py`)
- In `_build_player()` (playback.py:67-75), wire `player.on_engine_died` → new
  `PlaybackCoordinator._handle_engine_died(info)` (alongside where `on_karaoke_end` is set).
- `_handle_engine_died`: append a health event (bounded `deque`, e.g. maxlen 20:
  `{ts, engine, event:'crash'|'recovered', song, action}`), then decide via the **restart-loop
  guard** (e.g. track timestamps of recent auto-restarts): under the limit → spawn a thread that
  calls `self.restart_instances()` and appends a `recovered` event; over the limit → append an
  `escalated` event and do **not** auto-restart (banner tells KJ to switch engines / retry).
- Expose `player_health` + `player_health_events` via coordinator properties (mirror the existing
  `audio_error` property passthrough).

**3. Status + ack** (`routes.py`)
- Add `player_health` and `player_health_events` to the `/status` payload (~routes.py:1178-1200).
- New `POST /player-crash/ack {id}` (mirror `/fix_audio` at routes.py:1203) → coordinator marks
  events up to `id` acknowledged so `/status` stops flagging the banner.
- New `POST /player-crash/retry` (optional) → re-play the remembered crashed song (or reuse
  `/play`).

**4. Web UI** (`templates/index.html`, `static/app.js`, `static/style.css`)
- Add `#player-crash-banner` sibling after the `#audio-warning` div (index.html:14-17), reuse that
  CSS block as a template but **amber** (`#fbbf24`), with buttons **Retry** / **Switch engine**
  (calls existing `openAvModal()` / `avSetRenderer`) / **Dismiss**.
- In `updateStatus()` (app.js:1231, next to the `audio_error` handling at :1242): read
  `data.player_health_events`; if there's an unacknowledged crash, show the banner (song + time +
  restart outcome); on Dismiss, `apiCall('/player-crash/ack', {id})`.
- On each new event, `log('Video player (<engine>) crashed HH:MM on "<song>" — auto-restarted',
  'error')` for persistent history (app.js:180).

## Track A — Implementation steps (TDD)

1. [ ] **Test+build death detection (mpv):** unit tests — `on_engine_died` fires when socket
       refused/EOF **and** `process.poll()` returns an int; does **not** fire on live-socket EOF
       event (normal end) or when `process.poll()` is `None` (transient blip). Then implement.
2. [ ] **Test+build death detection (vlc):** analogous with HTTP-probe-None + `process.poll()`.
3. [ ] **Test+build `_handle_engine_died` + restart guard:** event recorded; `restart_instances`
       called (mocked) under the limit; over the limit → escalate, no restart. Records crashed
       song.
4. [ ] **Test+build `/status` fields + `/player-crash/ack`:** payload includes health + events;
       ack marks them acknowledged.
5. [ ] **Frontend banner + `updateStatus()` wiring + `log()` history** (JS syntax hook; manual/e2e).
6. [ ] **(Optional) `--hwdec=no` config toggle** in the mpv launch command, off a config flag, as
       an immediate AV1 mitigation — *only if* on-device testing (Track B step 0) shows it stops
       the crash. Default value chosen after that test.
7. [ ] **Verify end-to-end on device** by killing mpv (or playing an AV1 file pre-upgrade) and
       watching auto-restart + banner.

## Track B — Implementation steps (device ops, off-show)

0. [ ] **Quick mitigation test:** on NomadPC, play an AV1 file through the app with the mpv launch
       command temporarily including `--hwdec=no`; confirm whether it stops the SIGSEGV (software
       libdav1d decode was crash-free in testing). If yes → cheap interim fix via Track A step 6.
1. [ ] **Snapshot + rollback prep:** record current versions/packages; note how to reinstall.
   Current: mpv 0.37.0, ffmpeg 6.1.1-3ubuntu5, libdav1d7 1.4.1, libavcodec 60.31.102.
2. [ ] **Choose packaging** (Open Question below): system apt/PPA upgrade vs. a **self-contained
       static mpv** (bundles its own ffmpeg+dav1d) used only by kj-controller. Lean static — it
       won't disturb the system ffmpeg that yt-dlp and other tooling rely on, and rolls back by
       swapping one binary.
3. [ ] Install mpv 0.41.0 (+ ffmpeg/dav1d as needed by the chosen packaging).
4. [ ] **Verify:** play all 4 known AV1 files (My Hero, Olivia Rodrigo – Drop Dead, Afroman –
       Because I Got High, Justin Bieber – Baby) **through the app**; confirm no crash (STAT
       `SLsl`, no new `coredumpctl`/`dmesg` mpv SIGSEGV), plays to completion, A/V OK. Re-run an
       H.264 control (ABBA) to confirm no regression.
5. [ ] Confirm rubberband pitch-shift + HDMI audio still work with the new mpv.
6. [ ] Document in `docs/MINIPC-SETUP.md` (install/upgrade + rollback) and `docs/CHANGELOG.md`.

## Files to create/modify

| File | Action | Description |
|------|--------|-------------|
| `kj-controller/mpv_manager.py` | Modify | `on_engine_died` attr; confirm death via `process.poll()`; fire from monitor; (opt) `--hwdec` flag |
| `kj-controller/vlc.py` | Modify | `on_engine_died` attr; confirm death via `process.poll()` in monitor |
| `kj-controller/playback.py` | Modify | Wire callback in `_build_player`; `_handle_engine_died`; restart-loop guard; health event deque + properties |
| `kj-controller/routes.py` | Modify | `player_health`/`player_health_events` in `/status`; `POST /player-crash/ack` (+ optional `/retry`) |
| `kj-controller/templates/index.html` | Modify | `#player-crash-banner` (amber) with Retry/Switch/Dismiss |
| `kj-controller/static/app.js` | Modify | banner wiring in `updateStatus()`; ack via `apiCall`; `log()` history |
| `kj-controller/static/style.css` | Modify | amber banner styles (model on `#audio-warning`) |
| `kj-controller/tests/unit/test_mpv_karaoke_player.py` | Modify | mpv death-detection tests |
| `kj-controller/tests/unit/test_vlc*.py` / `test_playback*.py` | Modify/Create | vlc death + coordinator recovery/guard tests |
| `kj-controller/tests/unit/test_routes*.py` | Modify/Create | `/status` health fields + ack endpoint |
| `docs/MINIPC-SETUP.md` | Modify | Track B: media-stack upgrade + rollback |
| `docs/CHANGELOG.md` (+ `kj-controller/docs/CHANGELOG.md`) | Modify | dated entries (system + app) |

## Testing strategy

- **Unit (TDD, primary):** death-vs-normal-end discrimination (mock `process.poll()` /
  socket / HTTP); callback fires exactly once on confirmed death; restart-loop guard math;
  `/status` payload; ack endpoint. Mirror existing patterns in `test_mpv_karaoke_player.py`.
- **Frontend:** JS syntax pre-commit hook; optional Playwright e2e (needs live server) that feeds
  a synthetic crash event into `/status` and asserts the banner + actions.
- **On-device (Track B):** the app-level AV1 repro/verify from the findings doc — the only true
  test of the crash fix.

## Open questions

**Proceeding on these defaults (chosen 2026-07-05 while user was away — confirm before/at implement):**

- **Repeat-crash behavior → Notify + suggest, KJ decides** (default). After the guard trips, show
  the amber banner urging Retry / Switch-engine; do **not** auto-switch engines. Matches the KJ's
  stated workflow of staying in control. *(Auto-failover-to-VLC can be added later as a toggle if
  wanted.)*
- **Track B packaging → Self-contained mpv** (default). A static/bundled mpv (own ffmpeg+dav1d)
  used only by kj-controller; leaves system ffmpeg (yt-dlp etc.) untouched; rollback = swap the
  binary. Smallest blast radius on the production device.
- **Restart-guard threshold → ≥3 engine crashes within 60s → stop + escalate** (default).
  *Implementation note (2026-07-05, per CodeRabbit):* the guard counts crashes **globally**,
  not per-song, so it also catches a hot restart loop or a run of un-playable files; the song is
  kept only for the notification. Each health event carries a monotonic `id`, and
  `/player-crash/ack` acknowledges up to that id (not deque position). The UI hides **Retry** on
  an escalated alert (retrying the same engine is futile → switch engine). **Open behaviour
  question for review:** on escalation the engine is currently left un-restarted (dead) to avoid
  a hot loop — an alternative is to always restart to a clean *idle* state (never leave it dead)
  and treat escalation as message-only. Flagged for the user.
- **Retry offered only for crashes, not normal `audio_error`** — banners stay separate (different
  failure classes).

Still genuinely open (need user):
- [ ] **Ship `--hwdec=no` interim mitigation?** Decide after Track B step 0 confirms it stops the
      crash on-device.
- [ ] Confirm the four defaults above (especially auto-failover vs. suggest, and static-mpv vs.
      system upgrade — the latter touches production hardware).

## Rollback plan

- **Track A:** pure code behind the normal PR/deploy flow — revert the PR. Auto-restart reuses the
  existing, proven `restart_instances()`; the new code only *triggers* it. A restart-loop guard
  prevents runaway restarts. Feature can hide behind a config flag if desired.
- **Track B:** keep the current mpv/ffmpeg/dav1d packages/binaries; rollback = reinstall them (or,
  with a static mpv, swap the binary back). Do the upgrade **off-show** with the device idle, and
  verify before relying on it. If AV1 still crashes post-upgrade, Track A + `--hwdec=no` still keep
  the show alive.

## Sequencing

1. Track A (safety net) — TDD, PR, ship via normal flow. Protects against *all* engine crashes.
2. Track B step 0 (`--hwdec=no` test) — cheap; if it works, ship as an interim AV1 mitigation.
3. Track B upgrade — off-show device change, verified against the AV1 files, documented.
