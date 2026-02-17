# Plan: Dynamic Overlay System

**Created:** 2026-02-17
**Branch:** TBD (needs `/start`)
**Status:** Implemented

## Overview

Add a configurable overlay system to NomadPi that renders text, images, QR codes, and countdowns on the live display — either between songs (desktop only) or always visible including during video playback. All overlays are managed from the KJ Controller web UI.

The existing conky rotation display remains separate and untouched.

## Requirements

- [ ] Scrolling ticker overlay (like a TV news ticker bar) at configurable screen edge
- [ ] Static text overlay at configurable position, size, and color
- [ ] Image/logo overlay (PNG) at configurable position and size
- [ ] Countdown timer overlay with target time and label
- [ ] QR code overlay generated from a URL
- [ ] Each overlay independently togglable: enabled/disabled, "show over video" on/off
- [ ] All configuration done from the KJ Controller web UI (no SSH/file editing)
- [ ] Solid background bars for all overlays (ARGB transparency not available on Pi)
- [ ] Overlays persist across service restarts (saved to disk)
- [ ] "Desktop only" overlays auto-hide when karaoke video plays, auto-show when it stops

## Architecture

### Three Components

```
┌─────────────────────────────────┐
│   KJ Controller Web UI          │  Browser (phone/laptop)
│   (overlay config panel)        │
└──────────────┬──────────────────┘
               │ REST API
               ▼
┌─────────────────────────────────┐
│   KJ Controller Backend         │  Flask (port 80)
│   overlay.py + routes           │
│   Writes overlays.json          │
│   Updates karaoke_playing state │
└──────────────┬──────────────────┘
               │ Shared JSON file
               ▼
┌─────────────────────────────────┐
│   Overlay Engine                │  Separate process (systemd)
│   pygame-ce render loop         │  DISPLAY=:0
│   One X11 window per overlay    │
│   Polls overlays.json for       │
│   config changes                │
└─────────────────────────────────┘
```

### Why Separate Process

The overlay engine needs a 30fps render loop (for smooth ticker scrolling) which doesn't fit Flask's request-response model. It also needs direct X11 access on `:0`. Keeping it separate means:
- Independent restarts (overlay crash doesn't kill KJ Controller)
- Clean separation of concerns
- Same pattern as the existing rotation-display service

### Shared State: overlays.json

```
/opt/nomad/kjbox/data/overlays.json
```

Written by KJ Controller backend, read by overlay engine. Polled every ~1 second via mtime check.

```json
{
  "karaoke_playing": false,
  "overlays": [
    {
      "id": "uuid-here",
      "type": "ticker",
      "name": "Welcome Message",
      "enabled": true,
      "show_over_video": false,
      "config": {
        "text": "Welcome to Karaoke Night! Sign up at the DJ booth!",
        "speed": 2,
        "position": "bottom",
        "font_size": 28,
        "text_color": "#FFFFFF",
        "bg_color": "#1a1a2e",
        "bg_opacity": 0.85,
        "padding": 10
      }
    },
    {
      "id": "uuid-here",
      "type": "static_text",
      "name": "Birthday Message",
      "enabled": true,
      "show_over_video": true,
      "config": {
        "text": "Happy Birthday Sarah!",
        "position": "top-right",
        "custom_x": null,
        "custom_y": null,
        "font_size": 36,
        "text_color": "#FFD700",
        "bg_color": "#000000",
        "bg_opacity": 0.7,
        "padding": 12
      }
    },
    {
      "id": "uuid-here",
      "type": "image",
      "name": "Venue Logo",
      "enabled": false,
      "show_over_video": true,
      "config": {
        "image_path": "/opt/nomad/assets/venue-logo.png",
        "position": "top-right",
        "custom_x": null,
        "custom_y": null,
        "width": 150
      }
    },
    {
      "id": "uuid-here",
      "type": "countdown",
      "name": "Last Call Timer",
      "enabled": false,
      "show_over_video": false,
      "config": {
        "target_time": "2026-02-17T23:00:00",
        "label": "Last call in",
        "expired_text": "LAST CALL!",
        "position": "top-center",
        "custom_x": null,
        "custom_y": null,
        "font_size": 40,
        "text_color": "#FF4444",
        "bg_color": "#000000",
        "bg_opacity": 0.85,
        "padding": 15
      }
    },
    {
      "id": "uuid-here",
      "type": "qr_code",
      "name": "Song Request QR",
      "enabled": false,
      "show_over_video": false,
      "config": {
        "url": "https://nomadkaraoke.com/request",
        "label": "Scan to request a song",
        "position": "bottom-right",
        "custom_x": null,
        "custom_y": null,
        "size": 180,
        "padding": 10
      }
    }
  ]
}
```

### Position System

Overlays use **preset positions** with optional custom x,y override:

| Preset | Coordinates |
|--------|------------|
| `top-left` | (20, 20) |
| `top-center` | (centered, 20) |
| `top-right` | (right-aligned - 20, 20) |
| `center` | (centered, centered) |
| `bottom-left` | (20, bottom - height - 20) |
| `bottom-center` | (centered, bottom - height - 20) |
| `bottom-right` | (right - width - 20, bottom - height - 20) |
| `bottom` | (0, bottom - height) — full-width bar |
| `top` | (0, 0) — full-width bar |
| `custom` | (custom_x, custom_y) |

Ticker overlays always use `bottom` or `top` (full-width bars).

### X11 Window Strategy

Each overlay = one pygame-ce `Window` object:
- **Borderless** (no decorations)
- **Always on top** via `SDL_WINDOW_ALWAYS_ON_TOP` flag or X11 `_NET_WM_STATE_ABOVE` hint
- **Sized to content** (not fullscreen — avoids transparency issues)
- **Solid background** with configurable color and opacity (rendered as blended color)

For "desktop only" overlays: window is destroyed/hidden when `karaoke_playing` becomes true, recreated/shown when false.

For "always visible" overlays: window stays up regardless of playback state.

### Pygame-ce Multi-Window

pygame-ce 2.2+ supports `pygame.Window` + `pygame.Renderer` for multiple independent windows. If this proves unstable on Pi, fallback plan is python-xlib for window management + PIL for rendering (same concept, more manual).

## Technical Approach

### Dependencies (Pi)

```
pip3 install pygame-ce qrcode pillow
```

- `pygame-ce` — Rendering engine, window management, text/image display
- `qrcode` — QR code generation
- `pillow` — Image loading, QR code image generation

### Overlay Engine Render Loop

```
Initialize pygame
Load overlays.json
Create windows for enabled overlays

Main loop (30 fps):
  1. Check overlays.json mtime — reload if changed
  2. Process pygame events (window close, etc.)
  3. For each overlay:
     - If desktop-only and karaoke_playing: hide window
     - If desktop-only and not karaoke_playing: show window
     - Update state (scroll position, countdown remaining)
     - Render to window surface
  4. Clock tick (30fps)
```

### KJ Controller Integration

The backend writes `karaoke_playing` state to overlays.json:
- Set `true` in `play_video()` (when karaoke starts)
- Set `false` in `monitor_karaoke()` (when song ends) and `stop` control action

This is lightweight — just a JSON write alongside the existing VLC commands.

## Implementation Steps

### Phase 1: Overlay Engine Core (desktop/overlay_engine.py)

1. [ ] Create `desktop/overlay_engine.py` — main entry point
   - pygame initialization, main render loop
   - Config file watching (mtime-based polling)
   - Window creation/destruction based on config changes
   - Graceful shutdown on SIGTERM

2. [ ] Create `desktop/overlay_types.py` — overlay type implementations
   - `BaseOverlay` — shared window management, position calculation, show/hide
   - `TickerOverlay` — horizontal scrolling text on solid bar
   - `StaticTextOverlay` — positioned text block with background
   - `ImageOverlay` — PNG image display at position
   - `CountdownOverlay` — live countdown to target time
   - `QRCodeOverlay` — QR code generated from URL with optional label

3. [ ] Create `desktop/overlay_config.py` — config schema, validation, defaults
   - Parse overlays.json
   - Validate overlay configs
   - Provide sensible defaults for missing fields

4. [ ] Test overlay engine locally (Mac with X11/XQuartz or headless pygame)

### Phase 2: Systemd Service

5. [ ] Create `desktop/overlay-display.service` systemd unit
   - After=graphical.target (same as rotation-display)
   - Environment: DISPLAY=:0, SDL_VIDEODRIVER=x11
   - ExecStart: python3 /opt/nomad/kjbox/desktop/overlay_engine.py
   - Restart=always

6. [ ] Create `data/overlays.json` with empty default: `{"karaoke_playing": false, "overlays": []}`

### Phase 3: Backend API (kj-controller/overlay.py + routes)

7. [ ] Create `kj-controller/overlay.py` — OverlayManager class
   - Load/save overlays.json
   - CRUD operations on overlay configs
   - Generate UUIDs for new overlays
   - Validate overlay configs before saving
   - Update `karaoke_playing` state

8. [ ] Add overlay API endpoints to routes.py:
   - `GET /overlays` — list all overlays
   - `POST /overlays` — create new overlay
   - `PUT /overlays/<id>` — update overlay config
   - `DELETE /overlays/<id>` — delete overlay
   - `POST /overlays/<id>/toggle` — toggle enabled state
   - `POST /overlays/<id>/toggle-video` — toggle show_over_video

9. [ ] Wire karaoke_playing state updates:
   - In `play_video()` flow: set `karaoke_playing: true`
   - In `monitor_karaoke()` song-end: set `karaoke_playing: false`
   - In `stop` control action: set `karaoke_playing: false`

10. [ ] Initialize OverlayManager in app.py (alongside existing VLC, media, catalog managers)

### Phase 4: Frontend UI

11. [ ] Add "Overlays" section to index.html
   - Collapsible section (like media folders)
   - List of overlays: name, type badge, enabled toggle, show-over-video toggle, edit/delete
   - "Add Overlay" button

12. [ ] Build overlay editor form (inline or modal)
   - Type selector (dropdown): ticker, static_text, image, countdown, qr_code
   - Dynamic fields based on type selection
   - Common fields: name, enabled, show_over_video
   - Color pickers using `<input type="color">`
   - Position preset dropdown + optional custom x,y
   - Font size slider/input
   - Text input (textarea for ticker, single line for static)
   - Countdown: datetime-local picker for target_time
   - QR: URL input field
   - Image: file path input (or upload — stretch goal)

13. [ ] Add overlay JavaScript functions
   - `loadOverlays()` — fetch and render overlay list
   - `createOverlay(data)` — POST to create
   - `updateOverlay(id, data)` — PUT to update
   - `deleteOverlay(id)` — DELETE with confirmation
   - `toggleOverlay(id)` / `toggleOverlayVideo(id)` — quick toggles

### Phase 5: Deploy & Test

14. [ ] Install dependencies on Pi: `pip3 install pygame-ce qrcode pillow`
15. [ ] Deploy overlay engine + service to Pi
16. [ ] Test each overlay type on physical display
17. [ ] Test "desktop only" vs "always visible" with VLC playback
18. [ ] Verify overlay windows stack correctly above VLC fullscreen
19. [ ] Test web UI overlay management from phone/laptop
20. [ ] Update autodeploy service to also restart overlay-display on code changes

### Phase 6: Documentation

21. [ ] Add overlay system section to docs/ARCHITECTURE.md
22. [ ] Add overlay service to docs/archive/NOMADPI-DETAILS.md
23. [ ] Add dated changelog entry to docs/CHANGELOG.md
24. [ ] Update CLAUDE.md memory with overlay system notes

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `desktop/overlay_engine.py` | Create | Main overlay rendering process (pygame-ce) |
| `desktop/overlay_types.py` | Create | Overlay type classes (Ticker, StaticText, Image, Countdown, QRCode) |
| `desktop/overlay_config.py` | Create | Config loading, validation, defaults |
| `desktop/overlay-display.service` | Create | Systemd service unit for overlay engine |
| `data/overlays.json` | Create | Default empty overlay config |
| `kj-controller/overlay.py` | Create | OverlayManager class (CRUD, state) |
| `kj-controller/routes.py` | Modify | Add overlay API endpoints |
| `kj-controller/app.py` | Modify | Initialize OverlayManager |
| `kj-controller/vlc.py` | Modify | Write karaoke_playing state on play/stop |
| `kj-controller/templates/index.html` | Modify | Add Overlays UI section |
| `docs/ARCHITECTURE.md` | Modify | Document overlay system |
| `docs/archive/NOMADPI-DETAILS.md` | Modify | Add overlay service details |
| `docs/CHANGELOG.md` | Modify | Add dated entry |

## Testing Strategy

### Unit Tests
- Overlay config validation (schema, defaults, edge cases)
- OverlayManager CRUD operations (create, read, update, delete)
- Position calculation (presets → pixel coords)
- Overlay type config defaults

### Integration Tests
- Flask routes for overlay API (GET/POST/PUT/DELETE)
- karaoke_playing state updates via play/stop actions
- Config file reading/writing

### Manual Testing (on Pi)
- Each overlay type renders correctly on physical display
- Ticker scrolls smoothly at 30fps
- Overlays appear above VLC fullscreen video
- "Desktop only" overlays hide during video, reappear after
- "Always visible" overlays persist during video
- Web UI creates/edits/deletes overlays successfully
- Overlay engine handles config changes without restart
- Service restart preserves overlay state
- Multiple simultaneous overlays render without conflict

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| pygame-ce multi-window unstable on Pi | Fallback to python-xlib + PIL for window management |
| Overlay windows not stacking above VLC | Use `_NET_WM_STATE_ABOVE` hint or SDL always-on-top flag |
| Ticker scrolling jerky at 30fps | Reduce to 24fps, use pixel-level smooth scrolling, profile on Pi |
| Config file race condition (read during write) | Write to temp file then atomic rename |
| Large QR code generation slow | Cache generated QR surface, only regenerate on URL change |

## Rollback Plan

1. Stop and disable overlay-display service: `systemctl stop overlay-display && systemctl disable overlay-display`
2. The rotation display and KJ Controller continue working independently
3. Remove overlay routes from Flask (revert routes.py changes)
4. All changes are additive — no existing functionality is modified destructively

## Open Questions

- [x] ~~Overlay technology~~ → pygame-ce (user chose)
- [x] ~~Video overlay style~~ → solid background bar (user chose)
- [x] ~~Rotation display integration~~ → keep separate (user chose)
- [x] ~~Overlay types~~ → all five types (user chose)
- [ ] Should the overlay engine have its own log file or use journalctl? (Suggest: journalctl via systemd, same as other services)
- [ ] Maximum number of simultaneous overlays? (Suggest: no hard limit, but document that 5+ may impact performance)
- [ ] Should image upload be supported from the web UI, or just file paths? (Suggest: file paths for V1, upload in V2)
