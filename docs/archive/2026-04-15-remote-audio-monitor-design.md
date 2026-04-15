# Remote Audio Monitor — Design Spec

**Date:** 2026-04-15
**Status:** Approved

## Purpose

Enable remote audio monitoring of NomadPC karaoke playback from a Mac over SSH/LAN. Primarily a dev/testing tool. Streams live audio over HTTP so it can be played with `ffplay` or similar.

## Investigation Results

Tested on NomadPC (2026-04-15):
- PipeWire 1.0.5 HDMI output works with mpv `--ao=pipewire`
- `pw-record --target <monitor>` captures real audio (-9.4 dB mean, not silence)
- PipeWire HDMI and existing ALSA-direct mpv coexist without conflict
- snd-aloop available as fallback but not needed

## Architecture

### Toggle Mode

The audio monitor is a toggle: enabling it switches the audio pipeline from ALSA-direct to PipeWire, and disabling restores the original ALSA mode. State is not persisted — after service restart, monitor is off and audio returns to ALSA default.

**Enable flow:**
1. Switch PipeWire card profile to `output:hdmi-stereo+input:analog-stereo`
2. Restart mpv with `--ao=pipewire` (replaces `--ao=alsa --audio-device=alsa/hdmiout`)
3. Restart VLC filler with `--aout pulse` (replaces `--aout alsa --alsa-audio-device hdmiout`)
4. Start ffmpeg subprocess: capture PipeWire monitor source → encode MP3 → stdout pipe
5. Flask route serves ffmpeg stdout as chunked HTTP response

**Disable flow (also triggered by Reset All):**
1. Kill ffmpeg capture process
2. Restart mpv with `--ao=alsa` (original mode)
3. Restart VLC filler with `--aout alsa` (original mode)
4. Switch PipeWire card profile back to `output:analog-stereo+input:analog-stereo`

### Stream Details

- Format: MP3 128kbps (compatible with ffplay, VLC, browsers)
- Transport: Chunked HTTP on the existing Flask port (5000)
- Endpoint: `GET /audio-monitor/stream`
- Single client only (dev tool). Second connection gets 409.
- A drain thread discards ffmpeg output when no client is connected to prevent pipe backpressure.

### PipeWire Details

- Card: `alsa_card.pci-0000_00_1f.3`
- HDMI profile: `output:hdmi-stereo+input:analog-stereo`
- Analog profile (default): `output:analog-stereo+input:analog-stereo`
- Monitor source name: `alsa_output.pci-0000_00_1f.3.hdmi-stereo.monitor`
- Commands run as user `nomad` with `XDG_RUNTIME_DIR=/run/user/1000`

## Files

### New
- `kj-controller/audio_monitor.py` — AudioMonitor class

### Modified
- `kj-controller/mpv_manager.py` — launch methods accept audio backend param
- `kj-controller/routes.py` — `/audio-monitor/start`, `/audio-monitor/stop`, `/audio-monitor/stream` routes; `/av/reset` stops monitor
- `kj-controller/app.py` — instantiate AudioMonitor
- `kj-controller/templates/index.html` — "Audio Monitor" section in AV Output modal
- `kj-controller/static/app.js` — JS for start/stop, status, stream URL display
- `kj-controller/static/style.css` — minimal styling reusing `.av-section` patterns

## API

### POST /audio-monitor/start
Enables the audio monitor. Switches to PipeWire, restarts players, starts capture.
Response: `{"success": true, "stream_url": "/audio-monitor/stream"}`

### POST /audio-monitor/stop
Disables the audio monitor. Stops capture, restores ALSA mode, restarts players.
Response: `{"success": true}`

### GET /audio-monitor/status
Returns monitor state.
Response: `{"active": bool, "stream_url": "/audio-monitor/stream"}`

### GET /audio-monitor/stream
Chunked HTTP audio stream (audio/mpeg). Returns 404 if monitor not active, 409 if another client is already connected.

## UI

New section in AV Output modal after "Browser Audio Output", using existing `.av-section` styling:

**Inactive state:**
- Status dot (grey) + "Off"
- "Start Monitor" button
- Help text: "Streams audio over HTTP for remote listening. Requires player restart (~3s)."
- Listen command shown greyed out

**Active state:**
- Status dot (green) + "Streaming (PipeWire HDMI)"
- "Stop Monitor" button
- Listen command: `ffplay http://nomadpc.local:5000/audio-monitor/stream`

## Edge Cases

- **No listener connected:** Drain thread discards ffmpeg output to prevent pipe blocking.
- **Service restart while active:** State not persisted. Returns to ALSA/off. Intentional safety for live shows.
- **Reset All:** Stops monitor, restores ALSA, same as disable flow.
- **Playback during toggle:** The ~3s restart interrupts playback (same behavior as existing VLC device switching).
- **PipeWire profile already on HDMI:** Start is idempotent — just ensures correct state.
