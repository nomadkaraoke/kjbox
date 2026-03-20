# Plan: Chromium Launcher Widget

**Created:** 2026-03-19
**Branch:** feat/sess-20260319-2154-chromium-launcher-widget
**Status:** Implemented

## Overview

Add a "Browser Mode" widget to the KJ Controller UI (right column, below Available Songs) with an explicit mode toggle between VLC (default) and Chromium. When Browser Mode is enabled, playback happens in fullscreen Chromium on the minipc's display at a user-provided URL. When disabled (default), everything works as normal via VLC.

**Why:** If YouTube blocks yt-dlp downloads, the KJ needs a fallback to play YouTube videos directly in a browser on the venue display. This keeps the show running without needing to SSH into the device or switch away from the KJ Controller interface.

## Requirements

- [ ] URL input field with a sensible default (youtube.com)
- [ ] Explicit mode toggle: VLC mode (default) ↔ Browser mode
- [ ] When Browser mode is toggled ON: kills VLC, launches Chromium fullscreen/kiosk at the URL
- [ ] When Browser mode is toggled OFF: kills Chromium, restarts VLC
- [ ] If a karaoke track is played while Browser mode is off (the default), normal VLC behavior — no change
- [ ] Status indicator showing current mode and Chromium running state
- [ ] Status integrates into the existing 2s `/status` poll (no separate polling)
- [ ] Works on NomadPC (primary target); gracefully does nothing on Pi or dev machines
- [ ] Persists last-used URL across service restarts (config.json)
- [ ] Handles orphan Chromium processes from previous runs (cleanup on launch)

## Technical Approach

### Architecture

Follow the existing VLCManager pattern — a lightweight `ChromiumManager` class that owns one subprocess, plus mode coordination logic:

```
UI Widget ──POST──> Flask routes ──> ChromiumManager ──> subprocess.Popen(chromium)
                         │                                    │
                         ├──> VLCManager.stop/restart    coordination
                         │
              /status poll <──────── process.poll() state + mode
```

No new polling interval — piggyback on the existing `/status` endpoint that already polls every 2s.

### Mode Toggle Behavior

| Action | VLC Mode (default) | Browser Mode |
|--------|-------------------|--------------|
| Toggle ON browser | Kill VLC → Launch Chromium at URL | Already in browser mode |
| Toggle OFF browser | Already in VLC mode | Kill Chromium → Restart VLC |
| Play karaoke track | Normal VLC playback | N/A (play buttons hidden/disabled while in browser mode) |

The toggle is deliberate — no auto-switching. This avoids the latency concern of switching mid-show and makes the mode explicit to the KJ.

### Key Decisions

1. **Kiosk mode (`--kiosk`)** rather than just `--start-fullscreen` — kiosk hides the address bar and all browser chrome, which is what you want on a venue display.

2. **Separate user data dir** (`--user-data-dir=/tmp/kj-chromium`) — avoids conflicting with any existing Chromium sessions and ensures a clean state.

3. **Kill strategy**: SIGTERM → wait 3s → SIGKILL, plus `pkill -f` fallback for orphans. Similar to VLC's `_kill_port` but matching on process name/args instead.

4. **`DISPLAY=:0`** environment variable required for the process to render on the physical display (same pattern as VLC on Pi).

5. **New `chromium.py` file** alongside `vlc.py` — keeps the pattern consistent with one manager per external process.

6. **No preset URL buttons** — just a URL input field. Simple and flexible.

## Implementation Steps

### Step 1: Backend — ChromiumManager class
1. [ ] Create `kj-controller/chromium.py` with `ChromiumManager` class
   - `__init__(config)` — store config ref, process handle = None, current_url = None
   - `launch(url)` — kill existing first, then `Popen` chromium with kiosk flags + DISPLAY=:0
   - `kill()` — SIGTERM → wait → SIGKILL, plus orphan cleanup via `pkill`
   - `is_running()` → bool (check `process.poll()`)
   - `get_status()` → dict with `running`, `pid`, `url` fields
   - `_find_chromium_binary()` — check for `chromium-browser`, `chromium`, `google-chrome` in PATH

### Step 2: Backend — Flask routes
2. [ ] Add routes to `kj-controller/routes.py`:
   - `POST /browser-mode/enable` — accepts `{"url": "..."}`, kills VLC, launches Chromium, persists URL, sets mode flag
   - `POST /browser-mode/disable` — kills Chromium, restarts VLC, clears mode flag
   - `GET /browser-mode/status` — returns mode state (also included in main `/status`)
   - Include browser mode + chromium status in existing `GET /status` response

### Step 3: Backend — Wire up in app.py
3. [ ] In `kj-controller/app.py`:
   - Import and instantiate `ChromiumManager`
   - Attach to `app.chromium` (following the `app.vlc` pattern)
   - Add `app.browser_mode = False` flag

### Step 4: Frontend — HTML widget
4. [ ] Add widget HTML in `kj-controller/templates/index.html` after the Available Songs container:
   - Container with "Browser Mode" header
   - URL input field (pre-filled from config or default "https://youtube.com")
   - Mode toggle button: "Enable Browser Mode" / "Disable Browser Mode" (changes label + style)
   - Status line showing current mode + Chromium PID when running

### Step 5: Frontend — JavaScript
5. [ ] Add functions in `kj-controller/static/app.js`:
   - `enableBrowserMode()` — POST to `/browser-mode/enable` with URL from input
   - `disableBrowserMode()` — POST to `/browser-mode/disable`
   - `updateBrowserModeStatus(statusData)` — called from existing `updateStatus()`, updates toggle state + status text
   - When browser mode is active: visually indicate on the widget (e.g. green active state on toggle)

### Step 6: Frontend — CSS
6. [ ] Add minimal CSS in `kj-controller/static/style.css`:
   - URL input styling (matches existing search-box pattern)
   - Toggle button active/inactive states
   - Status indicator styling

### Step 7: Tests
7. [ ] Create `kj-controller/tests/test_chromium.py`:
   - Unit tests for ChromiumManager (mock subprocess)
   - Route tests for `/browser-mode/enable`, `/browser-mode/disable`, status in `/status`
   - Test mode coordination: enable kills VLC + launches Chromium, disable kills Chromium + restarts VLC

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `kj-controller/chromium.py` | **Create** | ChromiumManager class (~70 lines) |
| `kj-controller/routes.py` | Modify | Add `/browser-mode/enable`, `/browser-mode/disable` routes; add to `/status` |
| `kj-controller/app.py` | Modify | Instantiate ChromiumManager, attach to app, add mode flag |
| `kj-controller/templates/index.html` | Modify | Add Browser Mode widget in col2 |
| `kj-controller/static/app.js` | Modify | Add enable/disable/status functions |
| `kj-controller/static/style.css` | Modify | Minimal widget-specific styles |
| `kj-controller/tests/test_chromium.py` | **Create** | Unit + route tests |

## Testing Strategy

- **Unit tests**: ChromiumManager — launch calls Popen with correct args, kill sends correct signals, is_running reflects process state, orphan cleanup
- **Route tests**: `/browser-mode/enable` (kills VLC + launches Chromium), `/browser-mode/disable` (kills Chromium + restarts VLC), status included in `/status`
- **Manual testing**:
  - Toggle ON → verify VLC stops, Chromium appears fullscreen on minipc display
  - Toggle OFF → verify Chromium closes, VLC restarts with filler music
  - Enter custom URL → verify correct page loads
  - Restart kj-controller → verify mode resets to VLC (safe default)

## Rollback Plan

All changes are additive — the widget is self-contained. To roll back:
1. Revert the commit
2. No database/config migration needed (the `chromium_url` config key is simply ignored if unused)
3. Mode always defaults to VLC on restart, so no stuck state possible
