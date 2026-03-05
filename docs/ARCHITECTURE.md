# Architecture

## System Overview

KJ Controller is a web-based karaoke show management application. A Flask backend orchestrates dual VLC media player instances and serves a browser-based remote control UI.

```
┌─────────────────┐     HTTP/REST      ┌──────────────────────────────────┐
│  Browser (KJ)   │◄──────────────────►│         Flask Backend            │
│  templates/      │                    │                                  │
│  index.html      │                    │  app.py (factory + entry point)  │
└─────────────────┘                    │  routes.py (Blueprint)           │
                                       │  media.py (MediaIndex)           │
                                       │  vlc.py (VLCManager)             │
                                       │  overlay.py (OverlayManager)     │
                                       │  config.py / utils.py            │
                                       │                                  │
                                       │  ┌────────────┐ ┌────────────┐  │
                                       │  │ Karaoke VLC│ │ Filler VLC │  │
                                       │  │  :8080     │ │  :8081     │  │
                                       │  └─────┬──────┘ └─────┬──────┘  │
                                       └────────┼──────────────┼──────────┘
                                                │              │
                                       ┌────────▼──────────────▼──────────┐
                                       │    ALSA Audio → HDMI / USB Mixer │
                                       └─────────────────────────────────┘
                                                │
                                       ┌────────▼──────────────┐
                                       │  Display (Fullscreen)  │
                                       └───────────────┬────────┘
                                                       │
                           ┌───────────────────────────┘
                           │  overlays.json
                           ▼
                  ┌─────────────────────┐
                  │  Overlay Engine      │  Separate process (systemd)
                  │  desktop/            │  pygame-ce, 30fps render loop
                  │  overlay_engine.py   │  One X11 window per overlay
                  └─────────────────────┘
```

## Module Structure

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `app.py` | ~70 | `create_app()` factory, `start_app()` entry point |
| `config.py` | ~70 | Constants, `is_pi()`, `load_config()`, `save_config_value()` |
| `utils.py` | ~40 | `log_message()`, `sanitize_filename_part()`, `parse_youtube_filename()` |
| `media.py` | ~260 | `MediaIndex` class: scan, validate, download, delete, list |
| `vlc.py` | ~260 | `VLCManager` class: launch, command, fade, play, restart, monitor |
| `catalog.py` | ~230 | `ExternalCatalog` class: SQLite FTS5 search over external media |
| `zip_playback.py` | ~50 | `ZipPlayback` class: CDG+MP3 ZIP extraction for VLC |
| `overlay.py` | ~100 | `OverlayManager` class: CRUD, toggle, karaoke_playing state, JSON persistence |
| `karaoke_nerds.py` | ~140 | Karaoke Nerds web scraper: search, parse HTML results, extract YouTube URLs |
| `youtube_search.py` | ~80 | YouTube search via yt-dlp: ytsearch with extract_flat for fast metadata |
| `youtube_health.py` | ~170 | YouTube health checks: yt-dlp/EJS/Deno version detection, cookie validation, PyPI version check (24h cache), pip upgrade |
| `rotation.py` | ~180 | `RotationManager` class: Google Sheets singer rotation read/write via gspread |
| `routes.py` | ~720 | Flask Blueprint with all route handlers |

### Dependency Flow

```
app.py → config.py, media.py, vlc.py, catalog.py, zip_playback.py, overlay.py, routes.py, utils.py
routes.py → config.py, utils.py, karaoke_nerds, youtube_search, youtube_health (accesses media/vlc/catalog/zip_playback/overlay_manager via current_app)
karaoke_nerds.py → config.py, utils.py (requests, beautifulsoup4)
youtube_search.py → media._ytdlp_base_opts, config.py, utils.py (yt_dlp)
youtube_health.py → (yt_dlp, importlib.metadata, shutil, subprocess)
overlay.py → (stdlib only: json, os, uuid, tempfile)
media.py → config.py, utils.py
vlc.py → config.py, utils.py
catalog.py → (stdlib only: sqlite3, os, re)
zip_playback.py → (stdlib only: zipfile, tempfile, shutil)
config.py → (stdlib only)
utils.py → (stdlib only)
```

### State Ownership

| State | Owner | Access in Routes |
|-------|-------|-----------------|
| Config dict | `app.kj_config` | `current_app.kj_config` |
| Media index | `MediaIndex.index` | `current_app.media` |
| VLC processes | `VLCManager.processes` | `current_app.vlc` |
| Playback state | `VLCManager` attributes | `current_app.vlc` |
| Audio device | `VLCManager.audio_device` | `current_app.vlc` |
| External catalog | `ExternalCatalog` (SQLite DB) | `current_app.catalog` |
| ZIP extraction | `ZipPlayback._temp_dir` | `current_app.zip_playback` |
| Overlay configs | `OverlayManager` (overlays.json) | `current_app.overlay_manager` |
| Karaoke playing flag | `OverlayManager.karaoke_playing` | `current_app.overlay_manager` |
| Download state | `app.download_state` dict | `current_app.download_state` |

## REST API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serve remote control UI |
| POST | `/download` | Start async YouTube download via yt-dlp (returns immediately) |
| POST | `/download/ack` | Acknowledge download completion (resets state to idle) |
| POST | `/play` | Play a media file (path validated) |
| POST | `/seek` | Seek to position in karaoke video |
| POST | `/control` | Playback control (pause_resume, restart, stop) |
| POST | `/volume` | Set volume for karaoke or filler |
| GET | `/media` | List all indexed media files |
| POST | `/delete` | Delete a downloaded media file |
| POST | `/rescan` | Reload config and rescan media folders |
| GET | `/filler_music` | List available filler music files |
| POST | `/filler_music` | Change active filler music track |
| GET | `/status` | Get player state, current track, timing |
| POST | `/fix_audio` | Emergency: restart VLC instances |
| GET | `/audio_device` | Get current and available audio devices |
| POST | `/audio_device` | Switch audio output device (temporary, not persisted) |
| GET | `/av/status` | Full AV output status: video connectors, HDMI PCMs, IEC958, ELD, PipeWire |
| POST | `/av/reset` | Run fix-hdmi-audio.sh to restore known-good AV state, restart VLC |
| POST | `/av/vlc-device` | Temporarily switch VLC audio device (hw:X,Y or named device) |
| GET | `/search` | FTS5 full-text search over external catalog |
| GET | `/catalog/stats` | Catalog availability, total count, format breakdown |
| POST | `/catalog/build` | Build/rebuild catalog from file list |
| GET | `/overlays` | List all configured overlays |
| POST | `/overlays` | Create a new overlay |
| GET | `/overlays/<id>` | Get a single overlay by ID |
| PUT | `/overlays/<id>` | Update an existing overlay |
| DELETE | `/overlays/<id>` | Delete an overlay |
| POST | `/overlays/<id>/toggle` | Toggle overlay enabled state |
| POST | `/overlays/<id>/toggle-video` | Toggle overlay show-over-video state |
| POST | `/karaoke-nerds/search` | Search karaokenerds.com for web-only tracks |
| GET | `/karaoke-nerds/config` | Get preferred brand codes for KN result sorting |
| POST | `/karaoke-nerds/config` | Set preferred brand codes for KN result sorting |
| POST | `/youtube/search` | Search YouTube via yt-dlp (extract_flat metadata) |
| GET | `/youtube/status` | YouTube health: yt-dlp/EJS/Deno versions, cookie status |
| POST | `/youtube/cookies` | Upload Netscape-format cookies for authenticated downloads |
| DELETE | `/youtube/cookies` | Remove YouTube cookies file |
| POST | `/youtube/upgrade-ytdlp` | Upgrade yt-dlp via pip and restart service |
| POST | `/overlays/import` | Bulk import overlays (replaces all, assigns new IDs) |
| GET | `/system/autodeploy` | Check if kj-autodeploy service is active |
| POST | `/system/autodeploy` | Enable/disable kj-autodeploy (persists across reboots) |
| GET | `/rotation` | Get singer rotation queue (non-done entries from Google Sheet) |
| POST | `/rotation/status` | Update a rotation entry's status (any status from sheet) |
| POST | `/rotation/add` | Add a new singer to the rotation |

## Key Design Decisions

### App Factory Pattern
`create_app(config=None)` creates a fresh Flask app with injected services. Tests use `create_app(config=test_config)` for isolation without `importlib.reload` hacks. This matches the pattern used by karaoke-decide.

### Dual VLC Instances
Two headless VLC processes (karaoke on :8080, filler on :8081) enable independent volume control and smooth crossfading. The filler instance is stopped entirely during karaoke playback to release exclusive HDMI audio access.

### HTTP API Control
VLC instances are controlled via their built-in HTTP interface (`--extraintf http`), not via python-vlc bindings. This avoids native library dependencies and works cleanly with the `sudo -u dietpi` process isolation on the Pi.

### MediaIndex Class
A stateful class holding the index dict, with methods for scan, validate, download, and delete. The persistent JSON index (`media_index.json`) avoids rescanning the filesystem on every request. YouTube metadata (duration, upload_date) is preserved across rescans.

### VLCManager Class
A stateful class holding process handles, volume levels, and playback state. All VLC operations are no-ops when `enabled=False` (non-Pi environments), enabling safe dev mode.

### Platform Detection
`is_pi()` checks for `/boot/dietpi.txt`. On non-Pi platforms, VLC is disabled and the app runs in dev mode (web UI + media scanning only).

### Path Validation
`MediaIndex.validate_path()` resolves symlinks and verifies files are within configured media folders, preventing directory traversal. `is_in_download_folder()` restricts deletion to downloaded files only. The `/play` route also accepts paths under `external_media_mount` for external catalog files.

### External Catalog (SQLite FTS5)
`ExternalCatalog` provides instant full-text search over ~415K external karaoke files without keeping them in memory. The SQLite database lives on the SD card (`external_media.db`), indexed from a file list (`all-karaoke-files-*.txt`). FTS5 tokenizes artist, title, and disc_id fields. Queries are sanitized to prevent FTS5 syntax errors. The catalog is built once via `POST /catalog/build` and persists across restarts.

### CDG+MP3 ZIP Playback
`ZipPlayback` extracts CDG+MP3 ZIP files to a temp directory. VLC is given the `.mp3` path and auto-discovers the matching `.cdg` in the same directory for lyrics/graphics overlay. ZIP entries are validated against path traversal (`..` or absolute paths). Extracted files are chmod'd world-readable so VLC (running as `dietpi` user) can access them. The temp dir is cleaned up before each new extraction.

### Dynamic Overlay System
The overlay system uses a three-component architecture: (1) the KJ Controller web UI for configuration, (2) the Flask backend (`overlay.py`) for CRUD and state management, and (3) a standalone overlay engine (`desktop/overlay_engine.py`) for rendering. The engine runs as a separate systemd service (`overlay-display.service`) with a 30fps pygame-ce render loop, creating one borderless always-on-top X11 window per enabled overlay. Communication between the Flask backend and the engine is via a shared JSON file (`data/overlays.json`) polled by mtime every ~1 second. This avoids coupling the render loop to Flask's request-response model and matches the existing pattern of the rotation-display service. Five overlay types are supported: `ticker` (scrolling text bar), `static_text`, `image`, `countdown`, and `qr_code`. Each overlay has an independent `show_over_video` flag — when false, it auto-hides during karaoke playback and auto-shows when playback stops. The `karaoke_playing` state is set by the play/control routes and a `VLCManager.on_karaoke_end` callback.

## VNC Screen Preview

The KJ Controller web UI includes a live thumbnail of the Pi's screen via an embedded VNC viewer. This lets the KJ see what's on the HDMI output without a direct line of sight to the display.

```
┌──────────────┐   WebSocket   ┌─────────────┐   TCP    ┌──────────────┐
│  Browser     │◄─────────────►│ websockify  │◄────────►│  RealVNC     │
│  (noVNC)     │   :6080       │ (Python)    │          │  Server      │
│              │               │ Pi-only     │          │  :5900       │
└──────────────┘               └─────────────┘          └──────────────┘
```

### How It Works

1. **websockify** (Python package, added to `requirements.txt`) runs on the Pi as a WebSocket-to-TCP proxy. It listens on port 6080 and forwards traffic to RealVNC on port 5900.
2. **noVNC** v1.6.0 (vendored ES6 library in `static/novnc/`) runs in the browser as an `<script type="module">` import. It connects to websockify via WebSocket and renders the VNC framebuffer into a `<canvas>` element.
3. The thumbnail is **200px wide**, **view-only**, and positioned in the left column of the web UI.
4. The VNC password is entered once and stored in `localStorage` for subsequent sessions.
5. On disconnect, the client auto-reconnects after a 5-second delay.

### Configuration

| Config Key | Default | Description |
|------------|---------|-------------|
| `websockify_port` | `6080` | Port websockify listens on (WebSocket) |
| `vnc_target` | `localhost:5900` | RealVNC host:port to proxy to |
| `websockify_enabled` | `true` | Set `false` to disable websockify |
| `tls_cert` | `certs/cert.pem` | TLS certificate path (enables HTTPS + WSS) |
| `tls_key` | `certs/key.pem` | TLS private key path |

### TLS / HTTPS

When `tls_cert` and `tls_key` point to valid files, both Flask and websockify serve over TLS:
- Flask auto-switches from port 80 to 443 (HTTPS)
- websockify gets `--cert` and `--key` flags (WSS on port 6080)
- The browser uses `wss://` instead of `ws://` (auto-detected from `location.protocol`)

TLS is required because RealVNC's RA2ne authentication uses `crypto.subtle`, which browsers only expose in secure contexts (HTTPS). Certificates are generated via `mkcert` for locally-trusted development/LAN use.

### Platform Behavior

- **Pi (`is_pi()` = true):** websockify is started as a subprocess during app startup. If the `websockify` binary is not found, a warning is logged and the VNC preview is unavailable.
- **Dev mode (`is_pi()` = false):** websockify is not started. The VNC preview section appears in the UI but cannot connect.

### RealVNC Compatibility

- `Encryption=PreferOff` must be set in RealVNC config (`/root/.vnc/config.d/vncserver-x11`) — websockify handles TLS termination, so the VNC-level connection between websockify and RealVNC is unencrypted localhost traffic.
- RealVNC uses RA2ne (RSA-AES, auth type 6) even with `Authentication=VncAuth` set — the setting doesn't remove RA2ne from the offered auth types. The noVNC client handles this via a `serververification` event handler that auto-approves the server's RSA key (similar to SSH host key acceptance on a trusted LAN).

## Frontend Architecture

Single-page vanilla JavaScript app (`templates/index.html`). No build step, no framework. Communicates with the backend via `fetch()` REST calls. Polls `/status` for live player state updates. The VNC screen preview uses noVNC (ES6 module imported from `static/novnc/core/rfb.js`) to render a live thumbnail of the Pi's display.
