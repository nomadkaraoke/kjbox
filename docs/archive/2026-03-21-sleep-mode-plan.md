# Plan: Sleep Mode for NomadPC

**Created:** 2026-03-21
**Branch:** feat/sess-20260321-1143-sleep-mode
**Status:** Draft

## Overview

Add a "Sleep Mode" toggle to the System section of the KJ Controller web UI. When enabled, sleep mode puts NomadPC into a low-power, low-wear state for the ~6 days between weekly karaoke nights. The minipc stays running and remotely accessible, but everything non-essential is stopped to minimize power draw, SSD wear, and component stress.

When the KJ returns a week later, they toggle sleep mode off and everything comes back up ready for a show.

## Current State (Research Findings)

### What's running when idle
| Component | Resource Impact | Notes |
|-----------|----------------|-------|
| kj-controller (Flask) | Low CPU, ~50MB RAM | Listens on 80/443 |
| VLC karaoke (port 8080) | Idle, minimal | Waiting for playback |
| VLC filler (port 8081) | ~3.4% CPU | Looping `wii.mp3` continuously |
| overlay-display (pygame) | ~8.3% CPU, 113MB RAM | 30fps render loop, always running |
| rotation-display (conky) | Low CPU | Refreshes every 3s |
| x11vnc | Minimal | VNC server on :5900 |
| websockify | Minimal | WebSocket proxy on :6080 |
| Xorg + xfwm4 | ~16% CPU combined | Display server + window manager |
| lightdm | Minimal | Display manager |
| Dropbox | ~322MB RAM | LAN sync, file watching |
| cloudflared | Minimal | Tunnel for remote access |
| tailscaled | Minimal | VPN |
| ssh | Minimal | Remote shell |
| cups, bluetooth, ModemManager, avahi | Minimal each | Unnecessary services |

### USB SSD (3.6TB SanDisk Extreme Pro)
- Mounted at `/media/nomad/Nomad4TBOne` via fstab (`nofail`)
- Currently: power/control = "on" (no auto-suspend)
- Already enters standby naturally when not accessed (confirmed via `hdparm`)
- `uhubctl` not installed — needed for USB port power control
- USB path: `/sys/bus/usb/devices/2-1`

### Power Management
- `powerprofilesctl` available with profiles: `performance`, `balanced`, `power-saver`
- Currently on `balanced`
- No cron jobs configured

## Requirements

- [ ] Toggle in System UI to enter/exit sleep mode
- [ ] Stop all karaoke-related services (VLC, overlay, rotation, VNC)
- [ ] Unmount USB SSD and put it in standby
- [ ] Attempt USB port power-off if uhubctl is available
- [ ] Switch to power-saver power profile
- [ ] Stop unnecessary system services (cups, bluetooth, ModemManager, Dropbox)
- [ ] Keep SSH, cloudflared, tailscaled running (remote access)
- [ ] Keep kj-controller itself running (so the UI can wake everything back up)
- [ ] All changes fully reversible — wake restores the exact pre-sleep state
- [ ] Persist sleep state across kj-controller restarts (but NOT across reboots — reboot = full wake)
- [ ] UI clearly shows current sleep/wake state
- [ ] Confirmation dialog before entering sleep mode (it's a significant action)

## Technical Approach

### Architecture

Sleep mode is a **kj-controller feature**, not a systemd service. The Flask app orchestrates entering/exiting sleep mode by running systemctl commands and filesystem operations, similar to how autodeploy toggle and reboot already work.

**State persistence:** A simple flag file (`/tmp/kj-sleep-mode`) indicates sleep mode is active. This uses `/tmp` so a reboot automatically clears it (full wake on reboot). The kj-controller reads this on startup to know if it was in sleep mode.

**Sleep mode script approach:** Rather than embedding all the shell commands in Python, we'll create two shell scripts (`sleep-enter.sh` and `sleep-exit.sh`) in `kj-controller/` that handle the system-level operations. This keeps the logic testable, inspectable, and runnable manually via SSH if needed. The Flask route calls these scripts via subprocess.

### What Sleep Mode Does

#### Enter Sleep Mode (in order)
1. **Stop karaoke services:**
   - Stop VLC instances (via VLCManager.stop_all or kill)
   - Stop overlay-display service
   - Stop rotation-display service
   - Stop websockify (if running as subprocess)
2. **Reduce display stack** (can't stop lightdm — kj-controller depends on graphical.target):
   - Stop x11vnc service (prevents memory leaks from 6-day idle VNC)
   - `xset -display :0 dpms force off` (blank connected monitors)
3. **Stop unnecessary system services:**
   - `sudo systemctl stop cups cups-browsed bluetooth ModemManager avahi-daemon`
4. **Stop Dropbox:**
   - `pkill -f dropbox` or `dropbox stop` if available
5. **Unmount and power-down USB SSD:**
   - `sudo umount /media/nomad/Nomad4TBOne` (unmount)
   - `sudo hdparm -Y /dev/sda` (spin down / sleep the drive)
   - Set `/sys/bus/usb/devices/2-1/power/control` to `auto` (kernel auto-suspend)
   - Note: uhubctl installed but minipc USB hubs don't support per-port power switching
6. **Reduce power profile:**
   - `powerprofilesctl set power-saver`
7. **Disable auto-deploy** (prevent git pulls during sleep):
   - `sudo systemctl stop kj-autodeploy` (if running)
8. **Write sleep flag:** `/tmp/kj-sleep-mode`

#### Exit Sleep Mode (reverse order)
1. **Remove sleep flag**
2. **Restore power profile:**
   - `powerprofilesctl set balanced`
3. **Re-enable auto-deploy** (if it was enabled before sleep):
   - `sudo systemctl start kj-autodeploy`
4. **Power-up and mount USB SSD:**
   - Set `/sys/bus/usb/devices/2-1/power/control` to `on` (disable auto-suspend)
   - Wait briefly for device to wake
   - `sudo mount /media/nomad/Nomad4TBOne` (remount per fstab)
5. **Restore display:**
   - `xset -display :0 dpms force on` (unblank monitors)
   - Start x11vnc service
6. **Start Dropbox:**
   - `nohup dropbox start &` or `systemctl --user start dropbox` (whichever applies)
7. **Restart system services:**
   - `sudo systemctl start cups bluetooth avahi-daemon` (skip ModemManager, cups-browsed)
8. **Start karaoke services:**
   - Start overlay-display service
   - Start rotation-display service
   - VLC instances will be started by kj-controller on next status poll or can be explicitly restarted
9. **Rescan media** (in case SSD content changed)

### Pre-sleep State Capture

Before entering sleep, save which services were actually running so we only restart what was active. Store in `/tmp/kj-sleep-state.json`:

```json
{
  "entered_at": "2026-03-21T14:30:00",
  "autodeploy_was_active": true,
  "dropbox_was_running": true,
  "services_stopped": ["overlay-display", "rotation-display", "x11vnc", "cups", ...],
  "ssd_was_mounted": true,
  "uhubctl_available": false,
  "previous_power_profile": "balanced"
}
```

This avoids starting services that weren't running before sleep.

### UI Design

The sleep mode toggle goes in a **new subsection** in the System panel, between Maintenance and Power:

```
┌─────────────────────────────────┐
│ System                          │
├─────────────────────────────────┤
│ Media & Output                  │
│   [Filler dropdown] [AV Output] │
├─────────────────────────────────┤
│ Maintenance                     │
│   [Check for Update] Auto-Deploy│
├─────────────────────────────────┤
│ Sleep Mode                      │  ← NEW
│   ○ Sleep Mode   [toggle]       │
│   "SSD unmounted, 5 services    │  ← status text when active
│    stopped, power-saver mode"   │
├─────────────────────────────────┤
│ Power                           │
│   [Restart App] [Reboot] [Off]  │
├─────────────────────────────────┤
│ Stats                           │
│   CPU ▓▓░░ MEM ▓░░░ DISK ▓░░░  │
└─────────────────────────────────┘
```

**When sleep mode is OFF:** Simple toggle, small "(inactive)" label.

**When sleep mode is ON:** Toggle is pink/active, status text shows what's been stopped. The stats widget should show reduced resource usage. A subtle banner or background tint could reinforce "sleep mode active" state.

**Entering sleep mode:** Uses the `dangerousAction()` two-click confirmation pattern (like Reboot/Shutdown), since it stops services and unmounts the SSD.

**Exiting sleep mode:** Single click is fine (it only starts things). Show a brief progress indicator while services come back up.

### Edge Cases

1. **SSD already unmounted:** Skip unmount, note in state
2. **uhubctl not installed:** Skip USB power control, use kernel auto-suspend as fallback
3. **Service already stopped:** Skip, don't error
4. **kj-controller restarts during sleep:** Read `/tmp/kj-sleep-mode` flag on startup, remain in sleep mode, don't re-enter (services are already stopped)
5. **System reboots during sleep:** `/tmp` is cleared, so all services start normally via systemd — automatic full wake
6. **SSD mount fails on wake:** Log error, continue waking other services, show warning in UI
7. **User tries to play a song while in sleep mode:** Show a clear message that sleep mode must be disabled first
8. **VLC connections during sleep:** VLCManager should handle gracefully since VLC processes won't be running

## Implementation Steps

### Phase 1: Backend Scripts & API (Python + Shell)

1. [ ] **Create `kj-controller/sleep-enter.sh`** — Shell script that stops services, unmounts SSD, sets power profile. Accepts a JSON state file path as argument, writes pre-sleep state to it. Runs with sudo where needed.

2. [ ] **Create `kj-controller/sleep-exit.sh`** — Shell script that reads state file, restores services, mounts SSD, restores power profile.

3. [ ] **Create `kj-controller/sleep_mode.py`** — Python module:
   - `SleepManager` class (follows existing pattern of manager classes)
   - `is_sleeping()` → bool (checks flag file)
   - `enter_sleep(config)` → dict (calls script, returns status)
   - `exit_sleep(config)` → dict (calls script, returns status)
   - `get_status()` → dict (sleep state, what's stopped, timestamps)
   - Handles VLC shutdown via VLCManager before calling shell script
   - Handles websockify shutdown

4. [ ] **Add routes to `routes.py`:**
   - `GET /system/sleep-mode` → `{"active": bool, "status": {...}}`
   - `POST /system/sleep-mode` → `{"active": bool}` — toggle sleep mode
   - Guard playback routes (`/play`, `/filler_music POST`, `/browser-mode/enable`) with sleep mode check → return 409 with message

5. [ ] **Wire into `app.py`:**
   - Create `SleepManager` instance, attach to `current_app`
   - On startup, check if sleep mode was active (read flag file)

### Phase 2: Frontend UI

6. [ ] **Add Sleep Mode subsection to `index.html`:**
   - New `.system-subsection` between Maintenance and Power
   - Toggle switch (reuse `.overlay-toggle` component)
   - Status text area for sleep state details
   - Optional: small icon/indicator

7. [ ] **Add JavaScript to `app.js`:**
   - `fetchSleepModeStatus()` — called on page load and in status poll
   - `toggleSleepMode(active)` — confirmation dialog for enter, direct for exit
   - Update status text with details (services stopped count, SSD status, etc.)
   - Disable play/download buttons when in sleep mode
   - Use `dangerousAction()` pattern or similar confirmation for entering sleep

8. [ ] **Add CSS to `style.css`:**
   - `.sleep-mode-toggle` container styles
   - `.sleep-mode-status` text styles
   - Optional: `.sleep-mode-active` class for visual state indication
   - Sleep mode banner/indicator styling

### Phase 3: Testing & Polish

9. [ ] **Unit tests** — Test SleepManager with mocked subprocess calls
10. [ ] **Manual testing via SSH** — Run scripts directly on the minipc
11. [ ] **End-to-end test** — Toggle via UI, verify services stop/start, SSD unmounts/mounts
12. [ ] **Edge case testing** — Reboot during sleep, restart app during sleep, missing uhubctl

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `kj-controller/sleep_mode.py` | Create | SleepManager class — orchestrates enter/exit |
| `kj-controller/sleep-enter.sh` | Create | Shell script for system-level sleep operations |
| `kj-controller/sleep-exit.sh` | Create | Shell script for system-level wake operations |
| `kj-controller/routes.py` | Modify | Add GET/POST `/system/sleep-mode`, guard playback routes |
| `kj-controller/app.py` | Modify | Create SleepManager, attach to app, check state on startup |
| `kj-controller/templates/index.html` | Modify | Add Sleep Mode subsection to System panel |
| `kj-controller/static/app.js` | Modify | Add fetch/toggle functions, disable controls in sleep |
| `kj-controller/static/style.css` | Modify | Sleep mode toggle and status styles |
| `kj-controller/tests/test_sleep_mode.py` | Create | Unit tests for SleepManager |
| `docs/CHANGELOG.md` | Modify | Add dated entry for sleep mode feature |

## Testing Strategy

**Unit tests:**
- SleepManager.is_sleeping() with/without flag file
- SleepManager.enter_sleep() with mocked subprocess (verify correct commands called)
- SleepManager.exit_sleep() with mocked subprocess and state file
- State file serialization/deserialization
- Edge cases: already sleeping, already awake, missing state file

**Manual testing (on device):**
- Run `sleep-enter.sh` via SSH, verify services stop, SSD unmounts
- Run `sleep-exit.sh` via SSH, verify everything comes back
- Toggle via web UI, verify round-trip
- Reboot during sleep mode, verify clean startup
- Check power consumption difference (if measurable)

**Integration tests:**
- Flask test client: POST /system/sleep-mode with mocked subprocess
- Verify playback routes return 409 during sleep mode
- Verify GET /system/sleep-mode returns correct state

## Resolved Questions

- **Dropbox management:** It's a process (PID 1533), not a systemd service. `pkill -f dropbox` to stop, `dropbox start` to restart.

- **Display blanking:** Yes — use `xset dpms force off` on sleep to turn off connected monitors. On wake, `xset dpms force on`. This saves monitor power without stopping X11.

- **uhubctl:** User approved installation. Install via `sudo apt install uhubctl`. Scripts gracefully handle its absence.

- **lightdm / X11 / VNC:** Cannot stop lightdm because kj-controller's systemd unit depends on `graphical.target` (which lightdm provides) — stopping lightdm would kill kj-controller. Instead: stop x11vnc (addresses memory leak concern for 6-day idle), blank display with DPMS (`xset dpms force off`), and let Xorg/xfwm4 idle at ~5% CPU. The big savings come from stopping overlay-display, rotation, VLC, and Dropbox.

- **uhubctl:** Installed but minipc's USB hubs don't support per-port power switching ("No compatible devices detected"). Fallback: `hdparm -Y` spindown + kernel `auto` power control. Both verified working on device.

- **Scheduled wake:** Not in v1. Manual toggle only.

## Rollback Plan

Sleep mode is fully reversible by design:
1. **From UI:** Toggle sleep mode off
2. **From SSH:** Run `sleep-exit.sh` manually, or delete `/tmp/kj-sleep-mode` and reboot
3. **Nuclear option:** Reboot clears `/tmp`, all services start normally via systemd
4. **Code rollback:** Revert the branch; existing services are unmodified and will continue working as before

## Future Enhancements (not in scope)

- Scheduled wake/sleep (systemd timer or cron)
- Deep sleep (stop display stack entirely)
- Power consumption monitoring/reporting
- Auto-sleep after N hours of inactivity
- Wake-on-LAN integration
