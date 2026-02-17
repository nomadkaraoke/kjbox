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
| `routes.py` | ~420 | Flask Blueprint with all 25 route handlers |

### Dependency Flow

```
app.py → config.py, media.py, vlc.py, catalog.py, zip_playback.py, overlay.py, routes.py, utils.py
routes.py → config.py, utils.py (accesses media/vlc/catalog/zip_playback/overlay_manager via current_app)
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

## REST API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serve remote control UI |
| POST | `/download` | Download YouTube video via yt-dlp |
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
| POST | `/audio_device` | Switch audio output device |
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

## Frontend Architecture

Single-page vanilla JavaScript app (`templates/index.html`). No build step, no framework. Communicates with the backend via `fetch()` REST calls. Polls `/status` for live player state updates.
