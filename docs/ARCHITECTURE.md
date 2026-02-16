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
                                       └────────────────────────┘
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
| `routes.py` | ~350 | Flask Blueprint with all 18 route handlers |

### Dependency Flow

```
app.py → config.py, media.py, vlc.py, catalog.py, zip_playback.py, routes.py, utils.py
routes.py → config.py, utils.py (accesses media/vlc/catalog/zip_playback via current_app)
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

## Frontend Architecture

Single-page vanilla JavaScript app (`templates/index.html`). No build step, no framework. Communicates with the backend via `fetch()` REST calls. Polls `/status` for live player state updates.
