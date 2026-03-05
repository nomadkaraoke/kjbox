# Plan: Decouple VLC Process Lifecycle from kj-controller

**Created:** 2026-03-05
**Branch:** TBD (needs `/start`)
**Status:** Draft

## Overview

When kj-controller restarts (via auto-deploy or manual `systemctl restart`), the managed VLC child processes die with it, interrupting any active karaoke playback. Since all VLC control already happens via HTTP API (ports 8080/8081), we can decouple the process lifecycle so VLC survives restarts and kj-controller reconnects on startup.

## Requirements

- [ ] VLC processes survive kj-controller restarts
- [ ] On startup, kj-controller detects and reconnects to existing VLC instances
- [ ] Playback state (what's playing, position, active/stopped) is recovered from VLC HTTP API
- [ ] Auto-deploy (`auto-deploy.sh`) no longer interrupts active songs
- [ ] `restart_instances` API still works (explicit VLC restart when user wants it)
- [ ] Cold boot still launches VLC from scratch
- [ ] No behavior change when VLC is not running (first boot, crashed, etc.)

## Technical Approach

Three coordinated changes:

1. **Process isolation**: Launch VLC with `start_new_session=True` so it's not in kj-controller's process group
2. **systemd config**: `KillMode=process` so only the Python process is killed on restart
3. **Startup reconnection**: Probe VLC HTTP ports on startup; skip launch if already responding; recover state

### State Recovery Strategy

VLC HTTP API (`/requests/status.json`) returns:
- `state`: "playing", "paused", "stopped"
- `information.category.meta.filename`: current file basename
- `time`: current position in seconds
- `length`: total duration
- `volume`: current volume level

What we can recover automatically:
- `karaoke_active` → `state == "playing"` or `state == "paused"`
- `current_playing_path` → resolve filename from VLC status against media index
- `karaoke_volume` / `filler_volume` → read from VLC status
- `current_filler_track` → resolve from filler VLC status

What we persist to a small state file (`/tmp/kj-vlc-state.json`):
- `current_playing_path` (full path — VLC only returns basename, and media index may have duplicates)
- `current_filler_track` (track name)
- Written on play/stop/filler-change, read on startup

## Implementation Steps

### 1. Add `start_new_session=True` to VLC launch
- [ ] In `VLCManager.launch_instance()`, add `start_new_session=True` to the `subprocess.Popen` call
- [ ] This puts VLC in its own process group so `SIGTERM` to kj-controller doesn't cascade

### 2. Add state persistence
- [ ] Add `_save_state()` method that writes `current_playing_path` and `current_filler_track` to `/tmp/kj-vlc-state.json`
- [ ] Call `_save_state()` in: `play_video()`, `monitor_karaoke()` (on song end), filler track change, `restart_instances()`
- [ ] Add `_load_state()` method that reads the file if it exists

### 3. Add startup reconnection logic
- [ ] Add `try_reconnect()` method to VLCManager:
  - Probe karaoke port (8080) and filler port (8081) via HTTP
  - If responding, skip `launch_instance()` for that VLC
  - Query status to recover `karaoke_active`, volume levels
  - Load persisted state file for `current_playing_path`, `current_filler_track`
  - If VLC is playing but state file is missing, still set `karaoke_active=True` (degrade gracefully — UI shows "playing" but without song name)
  - Store `None` for the `processes` dict entries (we don't own these PIDs) — handle this in `restart_instances`

### 4. Update `restart_instances()` for orphan VLC
- [ ] When `self.processes[name]` is None (reconnected, not spawned), find VLC by port and kill it
  - Use the HTTP API: there's no clean "quit" command in VLC HTTP, but `pl_stop` + sending a SIGTERM via `pkill` or `lsof -ti :PORT` works
  - Or: VLC HTTP has an undocumented `?command=pl_stop` then we can just launch a new one on the same port (the old one will fail to bind and exit)
  - Simplest: `subprocess.run(['fuser', '-k', '{port}/tcp'])` to kill whatever is on that port
- [ ] Then launch fresh instances as before

### 5. Update `app.py` startup sequence
- [ ] In `start_app()`, after creating VLCManager, call `vlc.try_reconnect()` before `launch_instance()`
- [ ] If reconnect found active karaoke, don't fade in filler
- [ ] If reconnect found filler playing, don't relaunch it
- [ ] Start monitor thread regardless (it just polls HTTP)

### 6. Update systemd unit
- [ ] Add `KillMode=process` to `[Service]` section in docs/MINIPC-SETUP.md
- [ ] Add `ExecStopPost` to clean up VLC on intentional `systemctl stop` (not restart):
  - Actually, on `stop` we probably DO want VLC to keep running (device might restart the service)
  - Only kill VLC on explicit user action via the `restart_instances` API
  - So: just `KillMode=process`, no ExecStopPost

### 7. Update auto-deploy.sh
- [ ] Change the deploy script to be smarter about restarts:
  - Check if only frontend files changed (JS/CSS/HTML) — if so, skip restart entirely (already works this way by design, but the script always restarts)
  - For Python changes, restart is still needed, but VLC survives thanks to steps 1-2
- [ ] This is an enhancement, not strictly required for the core feature

### 8. Tests
- [ ] Test `try_reconnect()` with mocked HTTP responses (VLC running, VLC not running)
- [ ] Test `_save_state()` / `_load_state()` round-trip
- [ ] Test `launch_instance()` skips launch when VLC already responding
- [ ] Test `restart_instances()` handles both owned processes and orphan VLC
- [ ] Test state recovery: correct `karaoke_active`, volume, playing path from mock VLC status

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `kj-controller/vlc.py` | Modify | `start_new_session`, `try_reconnect()`, `_save_state()`, `_load_state()`, update `restart_instances()` |
| `kj-controller/app.py` | Modify | Call `try_reconnect()` before `launch_instance()` in startup |
| `kj-controller/auto-deploy.sh` | Modify | (Optional) Skip restart for frontend-only changes |
| `docs/MINIPC-SETUP.md` | Modify | Add `KillMode=process` to systemd unit |
| `kj-controller/tests/test_vlc_reconnect.py` | Create | Tests for reconnection and state persistence |

## Testing Strategy

- **Unit tests**: Mock HTTP responses to test `try_reconnect()`, state save/load, `restart_instances()` with orphan processes
- **Manual testing on NomadPC**:
  1. Start a karaoke song playing
  2. `sudo systemctl restart kj-controller`
  3. Verify song continues uninterrupted
  4. Verify UI reconnects and shows correct playing state
  5. Test cold boot (no VLC running) still works
  6. Test `restart_instances` API still kills and relaunches VLC
  7. Test auto-deploy with Python changes doesn't interrupt playback

## Open Questions

- [ ] Should we also persist overlay state (karaoke_playing) so the overlay engine doesn't flash on restart? (Overlay is a separate service, so probably fine as-is)
- [ ] `/tmp/kj-vlc-state.json` gets cleared on reboot — that's fine since VLC also dies on reboot. But should we use a more durable path like `/var/run/kj-controller/` instead?

## Rollback Plan

- Revert the Python changes (vlc.py, app.py)
- Remove `KillMode=process` from systemd unit
- Everything goes back to current behavior (VLC dies with kj-controller)
- No data migration needed — state file is optional/ephemeral
