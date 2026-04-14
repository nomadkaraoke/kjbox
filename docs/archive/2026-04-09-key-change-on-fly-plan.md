# Plan: Real-Time Key Change for Karaoke Playback

**Created:** 2026-04-09
**Branch:** feat/sess-20260409-2148-key-change-on-fly
**Status:** Implementation complete, pending device integration test

## Overview

Add the ability to change the musical key (pitch shift) of any karaoke track in real-time during playback on NomadPC. The KJ taps +/- buttons and the key shifts instantly mid-song — no re-processing, no interruption.

**Approach:** Replace VLC with mpv for karaoke playback, using mpv's built-in rubberband audio filter for high-quality pitch shifting controlled via JSON IPC socket. Filler music stays on VLC (unchanged).

### Why mpv + rubberband

| Consideration | VLC scaletempo_pitch | mpv + rubberband |
|---|---|---|
| Audio quality | Basic pitch shifter | Gold standard (used by Audacity, Ardour) |
| Formant preservation | No | Yes — shifted voices sound natural |
| Real-time control | HTTP API doesn't expose pitch variable | `af-command` via IPC — glitch-free changes |
| API richness | Limited HTTP commands | Full JSON IPC with events |
| Already installed | Yes | Yes (mpv 0.37.0, librubberband 3.3.0) |

### What's Already on NomadPC

- mpv 0.37.0 with rubberband filter support
- librubberband 3.3.0
- ALSA `hdmiout` device (same one VLC uses)

## Requirements

- [ ] KJ can shift pitch up/down by semitone increments (-6 to +6) during playback
- [ ] Pitch changes are instant (no audible glitch, no playback interruption)
- [ ] Current key offset is displayed in the UI (e.g., "+2", "-1", "0")
- [ ] Key resets to 0 when a new song starts
- [ ] All existing playback features work identically (play, pause, seek, stop, volume, filler crossfade)
- [ ] Status polling continues to work for the UI (state, time, length, volume)
- [ ] Filler music is unaffected (stays on VLC)
- [ ] ALSA exclusive device handoff between filler VLC and karaoke mpv works reliably
- [ ] NomadPi compatibility is not required for v1 (can be a follow-up)

## Phase 0: Investigation & Validation

**Do this first, on the live device during downtime.** All steps are read-only or use throwaway test files.

### 0.1 Verify mpv + HDMI audio + fullscreen video

```bash
# SSH into NomadPC (use tunnel alias for remote access)
ssh nomadpctunnel

# Find the correct ALSA device name for mpv
mpv --audio-device=help 2>&1 | grep -i hdmi

# Test with a known karaoke file (pick any from the media dir)
# This should show fullscreen video on the TV with audio through HDMI
mpv --fs --ao=alsa --audio-device='alsa/DEVICE_NAME_FROM_ABOVE' \
    --input-ipc-server=/tmp/mpv-test \
    /path/to/any/karaoke/file.mp4

# In a second SSH session, verify IPC works:
echo '{"command": ["get_property", "time-pos"]}' | socat - /tmp/mpv-test
```

**Record:**
- [ ] Correct `--audio-device` string for HDMI output
- [ ] Video displays fullscreen on TV (no window decorations, correct resolution)
- [ ] Audio plays through HDMI to speakers
- [ ] IPC socket responds to commands

### 0.2 Verify rubberband pitch shifting

```bash
# With mpv still running from 0.1, or start fresh with rubberband:
mpv --fs --ao=alsa --audio-device='alsa/DEVICE_FROM_0.1' \
    --af=@rb:rubberband \
    --input-ipc-server=/tmp/mpv-test \
    /path/to/karaoke/file.mp4

# In second SSH session — shift up 2 semitones:
echo '{"command": ["af-command", "rb", "set-pitch", "1.122462"]}' | socat - /tmp/mpv-test

# Shift down 2 semitones:
echo '{"command": ["af-command", "rb", "set-pitch", "0.890899"]}' | socat - /tmp/mpv-test

# Reset to original:
echo '{"command": ["af-command", "rb", "set-pitch", "1.0"]}' | socat - /tmp/mpv-test
```

**Record:**
- [ ] Pitch changes are audible and correct direction
- [ ] No audio glitch or dropout when changing pitch
- [ ] Formant preservation sounds natural (vocals don't sound chipmunk/demonic)
- [ ] CPU usage is acceptable: `top -p $(pgrep mpv)` — should be well under 50%

### 0.3 Verify ALSA device handoff (VLC filler → mpv karaoke)

```bash
# Start filler VLC on its normal port (if not already running from kj-controller)
cvlc --extraintf http --http-port 8081 --http-password filler \
     --aout alsa --alsa-audio-device hdmiout --loop /path/to/filler.mp3 &

# Verify filler audio works
# Then stop filler:
curl -u :filler 'http://localhost:8081/requests/status.json?command=pl_stop'

# Wait 1 second, then start mpv:
sleep 1
mpv --fs --ao=alsa --audio-device='alsa/DEVICE_FROM_0.1' \
    --af=@rb:rubberband \
    --input-ipc-server=/tmp/mpv-test \
    /path/to/karaoke/file.mp4

# Verify karaoke audio works through mpv
# Then stop mpv:
echo '{"command": ["stop"]}' | socat - /tmp/mpv-test

# Restart filler and verify it works again
curl -u :filler 'http://localhost:8081/requests/status.json?command=pl_play'
```

**Record:**
- [ ] VLC filler → stop → mpv karaoke transition works (no "device busy" errors)
- [ ] mpv stop → VLC filler restart works
- [ ] Transition time is acceptable (< 2 seconds of silence)

### 0.4 Verify mpv idle mode + end-file events

```bash
# Start mpv in idle mode (stays alive, waits for commands)
mpv --idle --fs --ao=alsa --audio-device='alsa/DEVICE_FROM_0.1' \
    --af=@rb:rubberband \
    --input-ipc-server=/tmp/mpv-test &

# Load a short file:
echo '{"command": ["loadfile", "/path/to/short/file.mp4", "replace"]}' | socat - /tmp/mpv-test

# Monitor events (in another terminal):
socat - /tmp/mpv-test
# Wait for song to end naturally, look for:
# {"event": "end-file", "reason": "eof", ...}

# Verify mpv stays alive in idle after playback ends:
echo '{"command": ["get_property", "idle-active"]}' | socat - /tmp/mpv-test
# Should return: {"data": true, "error": "success"}
```

**Record:**
- [ ] `end-file` event fires when song finishes naturally
- [ ] mpv stays alive in idle mode after playback ends
- [ ] Can load and play another file after the first one ends
- [ ] `loadfile replace` while already playing switches immediately

### 0.5 Verify CDG+MP3 playback (if you use ZIP karaoke files)

```bash
# The existing ZipPlayback class extracts to a temp dir and plays the .mp3
# mpv should auto-discover the .cdg in the same directory (same as VLC)
echo '{"command": ["loadfile", "/tmp/kj-zip-extract/song.mp3", "replace"]}' | socat - /tmp/mpv-test
```

**Record:**
- [ ] CDG graphics overlay renders (or confirm CDG support not needed for mpv tracks)

### Phase 0 Outcome

If any validation step fails, document the failure and we'll adjust the plan. If all pass, proceed to Phase 1.

---

## Phase 1: MpvManager Backend

Create `kj-controller/mpv_manager.py` — a new class that manages mpv for karaoke playback via JSON IPC socket.

### 1.1 Core MpvManager class

```python
class MpvManager:
    """Manages mpv instance for karaoke playback with real-time pitch control."""

    def __init__(self, config, enabled=None):
        # Same signature as VLCManager.__init__
        # Key additions: self.pitch_semitones = 0, self.ipc_socket_path
        ...
```

**Properties to replicate from VLCManager** (routes.py reads these directly):

| Property | Type | Notes |
|---|---|---|
| `enabled` | bool | Same logic: `is_pi() or config.get('enable_vlc', False)` |
| `karaoke_active` | bool | Set True on play, False on end-file/stop |
| `current_playing_path` | str/None | Display path for UI |
| `current_filler_track` | str/None | Managed by filler VLC, but stored here for status |
| `audio_device` | str | ALSA device string |
| `audio_error` | bool | Set if playback verification fails |
| `karaoke_volume` | int | **Note: VLC uses 0-512 (256=100%). mpv uses 0-100+. Need scale conversion.** |
| `filler_volume` | int | Filler stays on VLC, so this stays in VLC scale |
| `last_seek_time` | float | Used by monitor to avoid false "stopped" detection |
| `last_play_time` | float | Same |
| `config` | dict | Mutable, replaced on `/rescan` |
| `on_karaoke_end` | callable | Callback when song ends |
| `processes` | dict | `{"karaoke": mpv_proc, "filler": vlc_proc}` |

**New properties:**

| Property | Type | Notes |
|---|---|---|
| `pitch_semitones` | int | Current pitch offset, -6 to +6, default 0 |
| `ipc_socket_path` | str | Default `/tmp/mpv-karaoke.sock` |

### 1.2 IPC communication layer

```python
def _send_ipc(self, command, request_id=None):
    """Send a JSON command to mpv's IPC socket. Returns parsed response."""
    # Connect to Unix socket, send JSON + newline, read response
    # Handle connection errors gracefully (mpv not running)
    ...

def _get_property(self, name):
    """Get an mpv property value."""
    return self._send_ipc(["get_property", name])

def _set_property(self, name, value):
    """Set an mpv property."""
    return self._send_ipc(["set_property", name, value])
```

### 1.3 Launch and reconnect

```python
def launch_instance(self, name, ...):
    """Launch mpv (karaoke) or VLC (filler)."""
    if name == "karaoke":
        # mpv --idle --fs --ao=alsa --audio-device=... --af=@rb:rubberband
        # --input-ipc-server=/tmp/mpv-karaoke.sock --really-quiet
        # start_new_session=True (survive kj-controller restart)
        ...
    elif name == "filler":
        # Existing VLC launch logic (unchanged)
        ...

def try_reconnect(self):
    """Check if existing mpv/VLC instances are running."""
    # Karaoke: probe IPC socket with get_property idle-active
    # Filler: probe VLC HTTP (existing logic)
    ...
```

### 1.4 Playback control

```python
def play_video(self, file_path, display_path=None, overlay_manager=None):
    """Play a karaoke file via mpv."""
    # 1. Reset pitch to 0
    # 2. Fade out + stop filler VLC (existing logic)
    # 3. Send loadfile command via IPC
    # 4. Set volume (convert from VLC 0-512 scale to mpv %)
    # 5. Set karaoke_active, save state
    # 6. Verify playback after 3s (existing pattern)
    ...

def set_pitch(self, semitones):
    """Set pitch offset in semitones (-6 to +6). Real-time, no interruption."""
    semitones = max(-6, min(6, semitones))
    pitch_scale = 2 ** (semitones / 12)
    self._send_ipc(["af-command", "rb", "set-pitch", str(pitch_scale)])
    self.pitch_semitones = semitones
```

### 1.5 Filler management

Filler VLC methods are copied from VLCManager unchanged:
- `fade_in_filler()`, `fade_out_filler()`
- `ensure_filler_stopped()`
- `send_command()` — kept for filler VLC only
- `_probe_vlc()` — kept for filler reconnect

### 1.6 Monitor thread (event-driven)

```python
def monitor_karaoke(self):
    """Background thread: listen for mpv end-file events via IPC."""
    # Unlike VLC polling, mpv pushes events
    # Connect to IPC socket, read lines
    # On {"event": "end-file", "reason": "eof"}:
    #   - Set karaoke_active = False
    #   - Call on_karaoke_end callback
    #   - fade_in_filler()
    # Reconnect on socket errors (mpv restart)
    # Fallback: if event listening fails, poll get_property idle-active every 2s
    ...
```

### 1.7 Volume scale conversion

VLC uses 0-512 (256 = 100%). The UI sliders, config values, and all existing code use this scale. Two options:

**Option A (recommended): Convert at the boundary.** Keep VLC scale everywhere (config, UI, API). MpvManager converts internally:
```python
def _vlc_to_mpv_volume(self, vlc_vol):
    """Convert VLC volume (0-512, 256=100%) to mpv volume (0-200, 100=100%)."""
    return (vlc_vol / 256) * 100
```

**Option B:** Change the UI/config to use 0-100 scale. Too much churn for this feature.

### 1.8 Status response

The `/status` route currently calls `vlc.send_command()` to get VLC's raw status. For mpv, add a method:

```python
def get_karaoke_status(self):
    """Get current karaoke playback status for /status endpoint."""
    if not self.karaoke_active:
        return {"state": "stopped", "time": 0, "length": 0}
    return {
        "state": "paused" if self._get_property("pause") else "playing",
        "time": int(self._get_property("time-pos") or 0),
        "length": int(self._get_property("duration") or 0),
    }
```

---

## Phase 2: Route Integration

Modify `routes.py` to use MpvManager instead of raw VLC commands for karaoke operations. Filler routes stay unchanged.

### 2.1 Replace direct send_command calls for karaoke

| Route | Current (VLC) | New (mpv) |
|---|---|---|
| `/seek` | `vlc.send_command(port, pw, f"seek&val={time}")` | `vlc.seek_karaoke(time)` → `_send_ipc(["seek", time, "absolute"])` |
| `/control` pause | `vlc.send_command(port, pw, "pl_pause")` | `vlc.pause_resume_karaoke()` → `_set_property("pause", ...)` |
| `/control` restart | `vlc.send_command(port, pw, "seek&val=0")` | `vlc.seek_karaoke(0)` |
| `/control` stop | `vlc.ensure_karaoke_released()` | `vlc.stop_karaoke()` → `_send_ipc(["stop"])` |
| `/volume` karaoke | `vlc.send_command(port, pw, f"volume&val={level}")` | `vlc.set_karaoke_volume_live(level)` → `_set_property("volume", ...)` |
| `/status` | `vlc.send_command(port, pw, "")` → parse VLC JSON | `vlc.get_karaoke_status()` |

The key insight: instead of routes calling `send_command()` directly with port/password, they call named methods on MpvManager. This is cleaner than the current approach.

### 2.2 New pitch endpoint

```python
@routes_bp.route('/pitch', methods=['POST'])
def handle_pitch():
    """Set karaoke pitch offset in semitones."""
    semitones = request.json.get('semitones')
    vlc = current_app.vlc
    vlc.set_pitch(int(semitones))
    return jsonify({"success": True, "pitch_semitones": vlc.pitch_semitones})
```

### 2.3 Add pitch to /status response

```json
{
  "state": "playing",
  "pitch_semitones": 2,
  ...
}
```

### 2.4 Browser mode interplay

Browser mode routes (`/browser-mode/enable`, `/browser-mode/disable`) currently call `vlc.ensure_karaoke_released()` and `vlc.restart_instances()`. These need to work with the new mpv instance.

---

## Phase 3: Frontend UI

### 3.1 Pitch control in Now Playing bar

Add +/- buttons to the Now Playing section (visible only during playback):

```
[<< -1] [ 0 ] [+1 >>]     ← semitone buttons
```

Or a compact design:
```
Key: [-] 0 [+]
```

- Display shows current offset: "-2", "0", "+3"
- Buttons send `POST /pitch {semitones: current + 1}` or `{semitones: current - 1}`
- Reset button (click the "0" / current value) sends `{semitones: 0}`
- Disable buttons at ±6 bounds
- Hidden when state is "stopped"

### 3.2 Status polling update

`updateStatus()` in app.js already polls `/status` every 2s. Add:
```javascript
if (data.pitch_semitones !== undefined) {
    updatePitchDisplay(data.pitch_semitones);
}
```

### 3.3 Keyboard shortcuts (optional, nice-to-have)

- `+` / `=` → pitch up
- `-` → pitch down
- `0` → reset pitch

---

## Phase 4: Testing

### 4.1 Unit tests for MpvManager

- IPC command formatting (JSON structure)
- Volume scale conversion (VLC ↔ mpv)
- Pitch semitone-to-scale calculation
- State transitions (idle → playing → ended → idle)
- Pitch bounds clamping (-6 to +6)

### 4.2 Unit tests for routes

- `POST /pitch` with valid semitones
- `POST /pitch` with out-of-bounds values (clamped)
- `/status` includes `pitch_semitones`
- Existing playback route tests still pass

### 4.3 Integration test (manual, on device)

- [ ] Play a song → verify video + audio through HDMI
- [ ] Tap +1 three times → verify pitch shifts up naturally
- [ ] Tap -1 six times → verify pitch shifts down, stops at -6
- [ ] Play next song → verify pitch resets to 0
- [ ] Pause → resume → verify pitch is preserved
- [ ] Seek → verify pitch is preserved
- [ ] Stop → filler fades in normally
- [ ] Play from rotation queue → verify the whole flow works
- [ ] Fix Audio button → verify mpv restarts correctly
- [ ] Audio device switch → verify mpv restarts with new device

---

## Implementation Steps (ordered)

1. [ ] **Phase 0**: Run validation on NomadPC (investigation, ~30 min)
2. [ ] **Phase 1.1-1.2**: Create `mpv_manager.py` with core class + IPC layer
3. [ ] **Phase 1.3**: Launch, reconnect, and process management
4. [ ] **Phase 1.4-1.5**: Playback control + filler management
5. [ ] **Phase 1.6-1.8**: Monitor thread, volume conversion, status
6. [ ] **Phase 4.1**: Unit tests for MpvManager
7. [ ] **Phase 2**: Route integration (replace VLC calls, add /pitch)
8. [ ] **Phase 4.2**: Unit tests for routes
9. [ ] **Phase 3**: Frontend UI (pitch controls, status display)
10. [ ] **Phase 4.3**: Manual integration test on NomadPC

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `kj-controller/mpv_manager.py` | Create | MpvManager class (replaces VLCManager for karaoke) |
| `kj-controller/app.py` | Modify | Wire up MpvManager instead of VLCManager |
| `kj-controller/routes.py` | Modify | Replace karaoke send_command calls, add /pitch endpoint, update /status |
| `kj-controller/templates/index.html` | Modify | Add pitch control UI in Now Playing section |
| `kj-controller/static/app.js` | Modify | Pitch buttons, status polling for pitch_semitones |
| `kj-controller/static/style.css` | Modify | Pitch control styling |
| `kj-controller/tests/unit/test_mpv_manager.py` | Create | Unit tests for MpvManager |
| `kj-controller/tests/unit/test_routes_pitch.py` | Create | Unit tests for pitch route |

## Open Questions

- [ ] What is the exact `--audio-device` string for NomadPC HDMI? (Resolved in Phase 0.1)
- [ ] Does mpv's rubberband filter handle CDG+MP3 karaoke files? (Resolved in Phase 0.5)
- [ ] Can mpv use the named ALSA device `hdmiout` directly (like VLC's `--alsa-audio-device hdmiout`), or does it need the full `alsa/hdmi:CARD=...` form? (Resolved in Phase 0.1)
- [ ] Is `af-command rb set-pitch` supported in mpv 0.37.0? (Resolved in Phase 0.2)
- [ ] Should we keep VLCManager.py as-is for rollback, or refactor it? (Recommend: keep it, MpvManager is a parallel class)

## Rollback Plan

MpvManager is a new class alongside VLCManager. If mpv doesn't work out:
1. Revert `app.py` to instantiate VLCManager instead of MpvManager
2. Revert route changes (restore direct send_command calls)
3. Remove pitch UI elements
4. MpvManager file can be deleted — no other code depends on it

The VLCManager class is never modified or deleted, so rollback is a clean revert.

## Pitch Reference Table

| Semitones | Pitch Scale | Musical Interval |
|---|---|---|
| -6 | 0.707107 | Tritone down |
| -5 | 0.749154 | Perfect 4th down |
| -4 | 0.793701 | Major 3rd down |
| -3 | 0.840896 | Minor 3rd down |
| -2 | 0.890899 | Major 2nd down |
| -1 | 0.943874 | Semitone down |
| 0 | 1.000000 | Original key |
| +1 | 1.059463 | Semitone up |
| +2 | 1.122462 | Major 2nd up |
| +3 | 1.189207 | Minor 3rd up |
| +4 | 1.259921 | Major 3rd up |
| +5 | 1.334840 | Perfect 4th up |
| +6 | 1.414214 | Tritone up |

Formula: `pitch_scale = 2 ** (semitones / 12)`
