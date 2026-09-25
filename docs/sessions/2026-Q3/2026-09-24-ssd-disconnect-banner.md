# SSD-disconnect banner — 2026-09-24

**Project:** kjbox (kj-controller)   **Branch/commit:** `feat/sess-20260924-2215-ssd-disconnect-banner` → squash-merged to `main` as `a7ec838` (PR #236, v0.114.0)   **Status:** done — shipped and verified live on NomadPC

## Summary

Andrew reported `[10:11:53 PM] API Error: Invalid or inaccessible file path` on NomadPC.
Investigation found this was a **recurrence** of the 2026-08-27 4TB SanDisk SSD
ASMedia-bridge-hang incident (see `docs/TROUBLESHOOTING.md`), this time with no
smartctl polling running — so the bridge can wedge on its own, not only via the
previously-identified trigger. Andrew physically replugged the drive and it recovered
cleanly (no fsck needed). He then asked for an in-app feature so this is never silent
again: detect the condition and show a clear banner in the KJ UI.

Built, reviewed (3 CodeRabbit cycles), shipped, merged, and verified live — all in this
session.

## What changed

**Investigation** (read-only, via `ssh nomadpc`):
- `dmesg` showed `Sense Key: Not Ready` / `Medium not present` starting at 22:10:04,
  ~2 min before the app-level error — same signature as the 08-27 incident.
- Confirmed no smartctl process was running (ruled out the known regression trigger).
- After Andrew's physical replug: `EXT4-fs (sda1): recovery complete`, verified with a
  real file read at 535 MB/s.

**Feature implementation** (worktree `kjbox-ssd-disconnect-banner`):
- New `kj-controller/external_media_monitor.py` — `ExternalMediaMonitor` polls
  `external_media_mount` every 5s. Deliberately uses only `os.path.ismount()` +
  `os.listdir()` — never smartctl/NVMe passthrough, which is what hung the bridge
  originally. After `FAILURE_THRESHOLD=2` consecutive failures, raises an alert;
  clears automatically once the mount responds again.
- Wired into `/status` as `external_media_alert`, alongside the existing
  `player_alert` pattern (`app.py`, `routes.py`).
- Frontend: sticky, pulsing red banner (`role="alert"`) at the very top of the KJ UI —
  "⚠ SSD disconnected — unplug and replug it now! ⚠" (`templates/index.html`,
  `static/style.css`, `static/app.js`). Not dismissable — it reflects a live
  condition, not a one-off event.
- Docs: `docs/TROUBLESHOOTING.md` recurrence note, `kj-controller/docs/CHANGELOG.md`
  entry, version bump to v0.114.0.
- Tests: `tests/unit/test_external_media_monitor.py` +
  `tests/unit/test_routes_external_media_alert.py` — 16 tests, 99% coverage.

**Review (CodeRabbit CLI v0.7.6, 3 cycles, `--committed --base origin/main`)** — all
fixed:
1. Test paths using the real NomadPC device path (hermeticity risk) → switched to
   `tmp_path`-based paths.
2. `_probe()` missing `os.path.ismount()` check → a *cleanly* unmounted drive (mount
   point reverts to an ordinary directory) would falsely read as healthy via
   `os.listdir()` alone. Re-added the check (had been dropped earlier to fix a test
   that used a non-mounted `tmp_path` as a stand-in for "healthy").
3. Banner missing screen-reader semantics → added `role="alert"`, only update
   `textContent` when the message actually changes (avoid re-announcing every 2s
   poll).
4. `stop()`/`start()` race: if `start()` ran while the old loop was still mid-`check_once()`
   after `stop()`, it would no-op and never restart. Switched `_stop` bool → `threading.Event`
   (wakes the sleep immediately) and made `start()` wait (bounded) for the old thread
   to actually exit before spawning a replacement.
5. Unbounded probe-thread accumulation: each `check_once()` spawned a fresh probe
   thread; if a future failure mode blocks instead of erroring fast, a prolonged
   outage (the exact scenario this monitor targets) would leak one thread per 5s
   poll forever. Added `PROBE_TIMEOUT_SECONDS=3` bound (probe runs on an isolated
   daemon thread, `_probe_with_timeout()` treats a still-running probe as unhealthy)
   and capped at one in-flight probe thread at a time.

**Ship:** PR #236 → CI green (gitleaks pass, CodeRabbit skipped via
`@coderabbitai ignore`) → squash-merged → NomadPC auto-deploy picked it up within
seconds (`kj-controller` restarted 23:16:16) → verified `/status` live with
`external_media_alert: null` (healthy) and **playback continued uninterrupted through
the restart** (`state: playing`, same song still loaded) → worktree cleaned up, local
`main` fast-forwarded.

## Decisions & rationale

- **No smartctl/NVMe passthrough in the health check** — that's literally what hung
  the bridge in the first incident. Plain `os.listdir()`/`os.path.ismount()` are
  ordinary syscalls that failed fast (immediate `OSError`) in both observed
  incidents.
- **No manual dismiss button on the banner** — unlike the video-player-crash banner
  (a one-off event, ack'd via `/player-crash/ack`), this reflects a *live* condition.
  Dismissing it would let the KJ hide an unresolved problem; it should only go away
  when the drive is actually fixed.
- **FAILURE_THRESHOLD=2** rather than 1 — avoids flapping the banner on a single
  transient hiccup.

## Learnings / gotchas

- **Production-safety update (confirmed by Andrew this session):** a
  `systemctl restart kj-controller` on NomadPC **no longer interrupts active
  playback**. mpv is spawned with `start_new_session=True` and survives the
  restart — the coordinator reconnects via IPC on boot. This was already
  half-documented in memory (`mpv-survives-restart-reconnect.md`, about launch-arg
  changes needing a renderer bounce) but the broader "restart kills playback" line
  in `MEMORY.md`'s PRODUCTION SAFETY section was stale (true for the legacy VLC
  renderer, not the current mpv default) — corrected there. Still ask before
  pushing/restarting; just don't assume it'll kill the song.
- **Local `main` branch ref can be stale vs. actual `origin/main`** in this
  multi-worktree setup — `git fetch origin main` updates `FETCH_HEAD` and the
  `origin/main` remote-tracking ref, but a worktree's local `main` branch only
  moves on an explicit `pull`/`merge`/reset. Mid-session, local `main` was one PR
  behind real `origin/main`; always diff CodeRabbit / PR bases against
  `origin/main`, not the possibly-stale local `main`.
- **CodeRabbit CLI v0.7.6 flag set differs from the `/coderabbit` skill's
  documented invocation** — no `--plain`/`--type`; use `--committed`/`--uncommitted`
  + `--base <branch>`. The skill doc needs updating (not done this session — noted
  here for whoever hits it next). Also occasionally fails with `WebSocket closed`
  (transient) — a same-command retry succeeded both times it happened.
- `gh pr merge --squash --delete-branch` can fail on the *local* git step
  (`fatal: 'main' is already used by worktree at ...`) when another worktree has
  `main` checked out — the merge itself still goes through on GitHub; just delete
  the remote branch manually afterward (`git push <remote> --delete <branch>`).

## Open threads & next steps

- None outstanding for this feature — fully shipped, deployed, and verified healthy
  in production.
- Minor, not worth its own session: the `/coderabbit` skill doc's CLI invocation
  examples are out of date for CLI v0.7.6 (see gotcha above) — could be fixed
  next time `/coderabbit` is touched.
- Watch `docs/TROUBLESHOOTING.md`'s "4TB USB SSD Drops Offline" section — if the
  bridge wedges again, the KJ should now see the banner immediately; confirm on the
  next real occurrence that it actually fired (this session verified the code path
  and the healthy-state banner-absence in prod, but there was no live SSD failure to
  confirm the banner rendering itself during this session).

## Related docs

- `docs/TROUBLESHOOTING.md` — "4TB USB SSD Drops Offline" section (recurrence note
  added this session)
- `kj-controller/docs/CHANGELOG.md` — v0.114.0 entry
- PR: https://github.com/nomadkaraoke/kjbox/pull/236
