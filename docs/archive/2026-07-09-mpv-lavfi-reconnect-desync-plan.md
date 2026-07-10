# Plan: Robust fix for mpv playback failure from stale lavfi-complex/@rb state across restarts

**Created:** 2026-07-09
**Branch:** feat/sess-20260709-1948-fix-mpv-lavfi-desync
**Status:** Implemented & shipped (v0.80.0). This is a historical planning snapshot; see `docs/CHANGELOG.md` 2026-07-09 for the shipped record.

## Overview

On NomadPC, a normal song (`NOMAD-1508 - Atomic Kitten - Eternal Flame.mp4`)
refused to play — the UI showed "Playback may not be progressing", `time 0:00`,
`audio_error: true`. The file is perfectly healthy (plays fine in a fresh mpv).

**Root cause (empirically confirmed on device):** the long-lived karaoke mpv
process (PID survives kj-controller service restarts via `try_reconnect()`) still
carried a leftover `lavfi-complex` audio graph from an earlier original-vocals
*guide* song:

```
[aid2]volume=0.0000[gv];[aid1][gv]amix=inputs=2:normalize=0[ao]
```

A normal song has only one audio track (`aid1`); the stale graph references a
non-existent `[aid2]`, so `loadfile replace` builds an invalid filter graph,
mpv sets `playback-abort=true` and drops back to **idle** — while kj-controller
still believes `state: playing`.

**Why the graph survives:** `MpvKaraokePlayer.try_reconnect()` reattaches to the
running mpv on service restart but the fresh Python object resets
`_lavfi_active=False`, desyncing from mpv's real state. `play()`'s guard
`if self._lavfi_active:` then never clears the stale graph.

**Why we can't just clear it:** verified on the device — clearing `lavfi-complex`
while mpv is idle (no `aid2` loaded) orphans the persistent `@rb` rubberband
filter. Result: video plays but audio never selects (`aid=False`,
`audio-bitrate=None`) and `af-command rb set-pitch` errors — and this is
**unrecoverable via IPC** (rebuilding `af`, forcing `aid 1` all fail). mpv 0.37's
`af`/`lavfi-complex` state can only be reset by a fresh process.

## The class of failure

mpv's actual runtime audio-graph state (lavfi-complex, @rb) desyncs from
kj-controller's in-memory assumptions whenever the app reconnects to a
long-lived mpv it did not itself spawn. The stale `lavfi-complex` is the
instance that causes a hard playback failure.

## Requirements

- [ ] A normal (single-audio-track) song plays with **video + audio** after a
      kj-controller service restart, regardless of what the previous song was.
- [ ] A guide song genuinely playing when the service restarts is **not**
      interrupted (preserve the reconnect-survives-restart feature).
- [ ] After such a mid-guide restart, the *next* song still loads correctly.
- [ ] No two mpv instances ever coexist (socket/audio-device contention).
- [ ] Fix is renderer-local (mpv module); VLC path untouched.

## Technical Approach

**Only preserve a mpv instance that is actively *playing*; respawn a fresh one
when it is idle.** An idle reconnect then behaves exactly like a cold boot —
a fresh mpv launched with `--af=@rb:rubberband` and no `lavfi-complex` — which
is a known-good path. This eliminates *all* inherited state (stale lavfi,
orphaned @rb, stale pitch/volume), not just this symptom.

For the actively-playing reconnect (mid-song restart), reconcile
`_lavfi_active` from mpv's real `lavfi-complex` so that when the current guide
song ends, the next song's `play()` clears the graph correctly (at that point
the guide file — and its `aid2` — is still loaded, the safe clear path per the
existing code comment).

Idle-reconnect respawn costs nothing (nothing is playing) and only happens on
service restarts, which occur between songs unless a song is mid-play.

## Implementation Steps

1. [ ] `mpv_manager.py` `try_reconnect()`: keep the existing disabled / no-socket
       / IPC-unreachable early returns. When mpv is **playing**, reconnect as
       today **plus** `self._lavfi_active = bool(self._get_property("lavfi-complex"))`.
       When mpv is **idle**, log, tear the stale instance down, and return
       `False` so the coordinator launches fresh.
2. [ ] Add `_teardown_reconnected_idle()`: IPC `quit` → `pkill -TERM -f
       --input-ipc-server=<socket>` fallback (never leave two mpvs) → unlink
       socket → `self.process = None`. Deliberately does **not** set
       `_monitor_stop` (no monitor runs yet at reconnect; `launch()` +
       `_start_monitor()` bring one up on the new process).
3. [ ] Update `test_try_reconnect_finds_idle` for the new respawn behavior and
       add tests: idle→teardown+False, playing→reconcile `_lavfi_active` True
       when a graph is present, playing→False graph → `_lavfi_active` False.
4. [ ] Bump version in `kj-controller/pyproject.toml` (0.79.0 → 0.80.0).
5. [ ] Docs: `docs/CHANGELOG.md` dated entry; `docs/AUDIO.md` note on the
       reconnect audio-graph reset.
6. [ ] Ship + deploy to NomadPC, full reboot, verify song plays with audio.

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| kj-controller/mpv_manager.py | Modify | `try_reconnect()` respawn-on-idle + reconcile; add `_teardown_reconnected_idle()` |
| kj-controller/tests/unit/test_mpv_karaoke_player.py | Modify | Update/add reconnect tests |
| kj-controller/pyproject.toml | Modify | Version bump |
| docs/CHANGELOG.md | Modify | Dated entry |
| docs/AUDIO.md | Modify | Reconnect audio-graph reset note |

## Testing Strategy

- **Unit:** reconnect idle→False+teardown; playing→True+reconcile (graph
  present/absent); no-socket/disabled unchanged. Mock `_get_property`,
  `_send_ipc`, `subprocess.run`, `os.unlink`.
- **Manual (device, no live show):** deploy, full reboot, wait 3 min, confirm a
  fresh mpv, then play `NOMAD-1508 - Atomic Kitten - Eternal Flame.mp4` and
  verify video + audio + time advancing via `/status` and mpv IPC
  (`aid`, `time-pos`, `audio-bitrate`).

## Open Questions

- [ ] Does clearing `lavfi-complex` mid-idle also affect an in-process
      guide→normal transition (2nd song after a guide) on a *healthy* instance?
      Out of scope for the reported bug (that path uses the same process where
      `_lavfi_active` is accurate); will spot-check during device verification
      and note if a follow-up is warranted.

## Rollback Plan

Revert the `mpv_manager.py` change (single-file, self-contained). The prior
behavior (reconnect-to-idle) returns. On the device, a `git pull` of the revert
+ full reboot restores the previous binary state. The renderer bounce
(mpv→vlc→mpv) remains available as a manual unblock either way.
