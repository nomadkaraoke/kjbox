# Architecture

## System Overview

KJ Controller is a web-based karaoke show management application. A Flask backend orchestrates a runtime-swappable karaoke player (mpv **or** VLC) plus a shared VLC filler-music process, and serves a browser-based remote control UI.

```
┌─────────────────┐     HTTP/REST      ┌──────────────────────────────────┐
│  Browser (KJ)   │◄──────────────────►│         Flask Backend            │
│  templates/      │                    │                                  │
│  index.html      │                    │  app.py (factory + entry point)  │
└─────────────────┘                    │  routes.py (Blueprint)           │
                                       │  playback.py (Coordinator)       │
                                       │   ├── filler.py (FillerVLC)      │
                                       │   └── one of:                    │
                                       │       ├ mpv_manager.py (Mpv…)    │
                                       │       └ vlc.py (Vlc…)            │
                                       │  overlay.py (OverlayManager)     │
                                       │  config.py / utils.py            │
                                       │                                  │
                                       │  ┌────────────┐ ┌────────────┐  │
                                       │  │  Karaoke   │ │ Filler VLC │  │
                                       │  │ mpv (IPC)  │ │   :8081    │  │
                                       │  │  or VLC    │ │  (shared)  │  │
                                       │  │   :8080    │ │            │  │
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

                  ┌─────────────────────┐
                  │  Rotation Display    │  Separate process (systemd)
                  │  desktop/            │  Conky, reads local cache
                  │  rotation_data.py    │  /tmp/rotation_cache.json
                  └─────────────────────┘
```

## Module Structure

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `app.py` | ~70 | `create_app()` factory, `start_app()` entry point |
| `config.py` | ~70 | Constants, `is_pi()`, `load_config()`, `save_config_value()`, render mode constants |
| `utils.py` | ~40 | `log_message()`, `sanitize_filename_part()`, `parse_youtube_filename()` |
| `media.py` | ~310 | `MediaIndex` class: scan, validate, download, delete, list |
| `playback.py` | ~290 | `PlaybackCoordinator`: owns filler + one karaoke player, runtime `switch_renderer()`, facade for routes.py |
| `karaoke_player.py` | ~90 | `KaraokePlayer` Protocol: contract both renderer backends implement |
| `filler.py` | ~290 | `FillerVLC`: shared filler-music VLC instance, fade, auto-heal on broken aout |
| `mpv_manager.py` | ~420 | `MpvKaraokePlayer`: mpv karaoke backend (IPC + rubberband pitch + ALSA-release race fix) |
| `vlc.py` | ~340 | `VlcKaraokePlayer`: dual-VLC karaoke backend (CDG-compatible) |
| `chromium.py` | ~160 | `ChromiumManager` class: launch/kill fullscreen Chromium for Browser Mode, PipeWire audio routing |
| `catalog.py` | ~230 | `ExternalCatalog` class: SQLite FTS5 search over external media |
| `zip_playback.py` | ~50 | `ZipPlayback` class: CDG+MP3 ZIP extraction for VLC |
| `overlay.py` | ~100 | `OverlayManager` class: CRUD, toggle, karaoke_playing state, JSON persistence |
| `karaoke_nerds.py` | ~140 | Karaoke Nerds web scraper: search, parse HTML results, extract YouTube URLs |
| `youtube_search.py` | ~80 | YouTube search via yt-dlp: ytsearch with extract_flat for fast metadata |
| `youtube_health.py` | ~170 | YouTube health checks: yt-dlp/EJS/Deno version detection, cookie validation, PyPI version check (24h cache), pip upgrade |
| `divebar.py` | ~150 | Divebar catalog client: search, download URL generation via Cloud Function API |
| `rotation.py` | ~180 | `RotationManager` coordinator: delegates to `RotationStore` (SQLite) + `SheetSync` (optional), writes display cache, download/gen tracking |
| `rotation_store.py` | ~310 | `RotationStore` class: SQLite CRUD for rotation entries, position management, file linking, download/gen tracking, archive |
| `rotation_sync.py` | ~230 | `SheetSync` class: background thread pushing SQLite state to Google Sheets (optional backup) |
| `gen_client.py` | ~100 | `GenClient` HTTP client for gen API: job creation, status polling, download URL retrieval |
| `gen_poller.py` | ~90 | `GenPoller` background thread: polls gen API for active jobs, auto-downloads completed videos |
| `sleep_mode.py` | ~100 | `SleepManager` class: enter/exit low-power sleep mode, stop services, unmount SSD |
| `routes.py` | ~800 | Flask Blueprint with all route handlers |

### Dependency Flow

```
app.py → config.py, media.py, playback.py, chromium.py, catalog.py, zip_playback.py, overlay.py, sleep_mode.py, routes.py, utils.py
playback.py → config.py, filler.py, karaoke_player.py, mpv_manager.py, vlc.py, utils.py
filler.py → config.py, utils.py (requests)
karaoke_player.py → (stdlib typing)
mpv_manager.py → config.py, filler.py, utils.py (socket for IPC)
routes.py → config.py, utils.py, divebar, karaoke_nerds, youtube_search, youtube_health, psutil (accesses media/vlc/catalog/zip_playback/overlay_manager via current_app)
divebar.py → config.py (requests)
sleep_mode.py → config.py, utils.py (subprocess for shell scripts)
karaoke_nerds.py → config.py, utils.py (requests, beautifulsoup4)
youtube_search.py → media._ytdlp_base_opts, config.py, utils.py (yt_dlp)
youtube_health.py → (yt_dlp, importlib.metadata, shutil, subprocess)
overlay.py → (stdlib only: json, os, uuid, tempfile)
media.py → config.py, utils.py
vlc.py → config.py, utils.py
chromium.py → config.py, utils.py
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
| Player processes | `PlaybackCoordinator.processes` (proxies filler + active player) | `current_app.vlc` |
| Active renderer | `PlaybackCoordinator.render_mode` (persisted to config.json) | `current_app.vlc` |
| Playback state | `KaraokePlayer` attributes on the active player | `current_app.vlc` |
| Audio device | Set on both `FillerVLC` and active player | `current_app.vlc.audio_device` |
| Pitch offset | `MpvKaraokePlayer.pitch_semitones` (0 on VLC) | `current_app.vlc.pitch_semitones` |
| Filler track | `FillerVLC.current_track` | `current_app.vlc.current_filler_track` |
| External catalog | `ExternalCatalog` (SQLite DB) | `current_app.catalog` |
| ZIP extraction | `ZipPlayback._temp_dir` | `current_app.zip_playback` |
| Overlay configs | `OverlayManager` (overlays.json) | `current_app.overlay_manager` |
| Karaoke playing flag | `OverlayManager.karaoke_playing` | `current_app.overlay_manager` |
| Download state | `app.download_state` dict | `current_app.download_state` |
| Rotation queue | `RotationManager` (SQLite primary + optional Sheet backup) | `current_app.rotation` |
| Chromium process | `ChromiumManager` (subprocess + PipeWire) | `current_app.chromium` |
| Browser mode flag | `_browser_mode` bool in routes.py | Module-level (resets on restart) |
| Sleep mode | `SleepManager` (`/tmp/kj-sleep-mode` flag) | `current_app.sleep_manager` |

## REST API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serve remote control UI |
| POST | `/download` | Start async YouTube download via yt-dlp (returns immediately) |
| POST | `/download/ack` | Acknowledge download completion (resets state to idle) |
| POST | `/play` | Play a media file (path validated) |
| POST | `/pitch` | Set karaoke pitch offset in semitones (-6 to +6, mpv only) |
| GET | `/renderer` | Get active karaoke renderer and capabilities |
| POST | `/renderer` | Switch karaoke renderer (`{mode: "mpv" \| "vlc"}`); 409 while karaoke is active |
| POST | `/seek` | Seek to position in karaoke video |
| POST | `/control` | Playback control (pause_resume, restart, stop, fadeout) |
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
| POST | `/divebar/search` | Search Divebar community karaoke catalog (48K+ tracks from 62 brands) |
| POST | `/divebar/kn-lookup` | Cross-reference KN song IDs with Divebar catalog |
| POST | `/divebar/download` | Queue download of a Divebar track from Google Drive |
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
| GET | `/system/sleep-mode` | Sleep mode status (active, entering, exiting, state details) |
| POST | `/system/sleep-mode` | Enter or exit sleep mode (stops services, unmounts SSD, power-saver) |
| GET | `/system/stats` | System metrics: CPU %, memory, disk usage (requires psutil) |
| GET | `/rotation` | Get singer rotation queue (non-done entries, with estimated times) |
| POST | `/rotation/status` | Update a rotation entry's status (`{id, status}`) |
| POST | `/rotation/edit` | Edit a rotation entry's singer name and/or song (`{id, singer?, song_artist?}`) |
| POST | `/rotation/delete` | Delete a rotation entry (`{id}`) |
| POST | `/rotation/add` | Add a new singer (`{singer, song_artist?, notes?, file_path?, url_fallback?}`, default status: Waiting) |
| POST | `/rotation/move` | Reorder a rotation entry (`{id, new_position}`) |
| POST | `/rotation/archive` | Archive all entries to local archive + Sheet, start new rotation |
| POST | `/rotation/link` | Link a media file to a rotation entry (`{id, file_path}`) |
| POST | `/rotation/unlink` | Remove file link from a rotation entry (`{id}`) |
| POST | `/rotation/set-paid` | Toggle paid priority flag on a rotation entry (`{id, paid}`) |
| GET | `/rotation/search` | Unified search: local catalog + Karaoke Nerds + Divebar cross-ref (`?q=query`, min 3 chars) |
| POST | `/rotation/download-and-link` | Queue download and link to rotation entry (`{id?, singer?, source, file_id/youtube_url}`) |
| POST | `/rotation/make` | Create gen job and link to rotation entry (`{id?, singer?, artist, title}`) |
| GET | `/rotation/gen-status` | Get active gen job statuses for rotation entries |
| GET | `/rotation/sync-status` | Get Sheet sync status (`{last_sync, is_online, next_sync_in}`) |
| POST | `/rotation/restore` | Restore rotation from snapshot (`{entries}` for undo/redo) or from Google Sheet backup (no body) |
| POST | `/upload` | Upload a media file to the download folder (validates extension, sanitizes filename, triggers rescan) |
| POST | `/browser-mode/enable` | Enable Browser Mode: stop VLC, launch fullscreen Chromium at URL |
| POST | `/browser-mode/disable` | Disable Browser Mode: kill Chromium, restart VLC |

## Key Design Decisions

### App Factory Pattern
`create_app(config=None)` creates a fresh Flask app with injected services. Tests use `create_app(config=test_config)` for isolation without `importlib.reload` hacks. This matches the pattern used by karaoke-decide.

### Swappable Karaoke Renderer + Shared Filler
Karaoke playback happens on one of two backends, swappable at runtime from the AV Output modal:
- **`MpvKaraokePlayer`** (default) — mpv + rubberband audio filter for real-time pitch shifting (±6 semitones).
- **`VlcKaraokePlayer`** — dedicated VLC on port 8080, native CDG rendering, no pitch shift.

Filler music is always a single VLC process on port 8081, owned by `FillerVLC` and shared across renderer swaps so filler keeps playing when the KJ toggles engines. The exclusive-HDMI handoff is the same either way: filler `pl_stop` → karaoke plays → karaoke player `ensure_released()` → filler fade-in.

`PlaybackCoordinator` is the facade `routes.py` talks to; it holds the filler + one karaoke player and rejects `switch_renderer()` with HTTP 409 while karaoke is active. Render mode persists to `config.json` (`render_mode: mpv | vlc`).

### mpv IPC Control
mpv is controlled via a JSON IPC socket (`/tmp/mpv-karaoke.sock`). Commands are sent as newline-terminated JSON objects. mpv runs in `--idle` mode (stays alive between songs) and pushes `end-file` events when a song finishes. The rubberband filter is pre-loaded as a labeled filter (`--af=@rb:rubberband`) so pitch can be changed mid-song via `af-command rb set-pitch <scale>` without reinserting the filter chain.

### Real-Time Pitch Shifting
Pitch is shifted in semitone increments (-6 to +6) using the formula `pitch_scale = 2^(semitones/12)`. The rubberband library provides formant preservation, so shifted vocals sound natural rather than chipmunk/demonic. Pitch resets to 0 when a new song starts. The `af-command` approach is glitch-free — no audible pop or dropout when changing pitch during playback.

### VLC Filler HTTP API
The filler VLC instance is controlled via its built-in HTTP interface (`--extraintf http`), not via python-vlc bindings. This avoids native library dependencies and works cleanly with the `sudo -u dietpi` process isolation on the Pi.

### MediaIndex Class
A stateful class holding the index dict, with methods for scan, validate, download, and delete. The persistent JSON index (`media_index.json`) avoids rescanning the filesystem on every request. YouTube metadata (duration, upload_date) is preserved across rescans.

### PlaybackCoordinator + KaraokePlayer Protocol
Three classes collaborate:

- **`PlaybackCoordinator`** is what `current_app.vlc` points at. It owns a `FillerVLC` and exactly one `KaraokePlayer`. Most of the old `VLCManager` surface (play_video, seek_karaoke, set_pitch, restart_instances, …) is preserved as passthrough so `routes.py` was barely touched.
- **`KaraokePlayer` protocol** (`karaoke_player.py`) formalises what every renderer backend must provide: `play`, `stop`, `seek`, `pause_resume`, `set_volume`, `set_pitch`, `fadeout`, `ensure_released`, `get_status`, `monitor`, `try_reconnect`, `shutdown`, plus `name`, `supports_pitch`, `supports_cdg`, `active`, and related state.
- **`FillerVLC`** (`filler.py`) owns the shared filler-music process. Its `fade_in()` spawns an auto-heal thread that detects the "aout dead" failure mode (decoder running, no audio reaching the device — see `docs/AUDIO.md § Filler Audio Handoff`) and relaunches the VLC process to recover.

Both player processes are launched with `start_new_session=True` and survive kj-controller restarts. `try_reconnect()` on each probes the mpv IPC socket / VLC HTTP port — if an existing instance responds, launch is skipped and playback state recovered. The systemd unit uses `KillMode=process` so only the Python process is killed on restart, not the player children.

**Volume scale:** UI and config use VLC's 0-512 scale (256 = 100%). `MpvKaraokePlayer` converts to mpv's 0-100+ scale at the boundary so neither the UI nor filler VLC has to care.

**ALSA release race fix:** `MpvKaraokePlayer.ensure_released()` sends `stop` over IPC and polls `idle-active` before returning — this closes a race where mpv's `end-file` event fires ~350ms before the ALSA device is actually released, which would starve the filler VLC on re-open. Documented in `docs/AUDIO.md`.

### Platform Detection
`is_pi()` checks for `/boot/dietpi.txt`. On non-Pi platforms, VLC is disabled and the app runs in dev mode (web UI + media scanning only).

### Path Validation
`MediaIndex.validate_path()` resolves symlinks and verifies files are within configured media folders, preventing directory traversal. `is_in_download_folder()` restricts deletion to downloaded files only. The `/play` route also accepts paths under `external_media_mount` for external catalog files.

### External Catalog (SQLite FTS5)
`ExternalCatalog` provides instant full-text search over ~415K external karaoke files without keeping them in memory. The SQLite database lives on the SD card (`external_media.db`), indexed from a file list (`all-karaoke-files-*.txt`). FTS5 tokenizes artist, title, and disc_id fields. Queries are sanitized to prevent FTS5 syntax errors. If FTS5 returns no results, a LIKE fallback searches with punctuation stripped from both query and data (e.g. "Sheeps" matches "Sheep's"). The catalog is built once via `POST /catalog/build` and persists across restarts.

### CDG+MP3 ZIP Playback
`ZipPlayback` extracts CDG+MP3 ZIP files to a temp directory. VLC is given the `.mp3` path and auto-discovers the matching `.cdg` in the same directory for lyrics/graphics overlay. ZIP entries are validated against path traversal (`..` or absolute paths). Extracted files are chmod'd world-readable so VLC (running as `dietpi` user) can access them. The temp dir is cleaned up before each new extraction.

### Dynamic Overlay System
The overlay system uses a three-component architecture: (1) the KJ Controller web UI for configuration, (2) the Flask backend (`overlay.py`) for CRUD and state management, and (3) a standalone overlay engine (`desktop/overlay_engine.py`) for rendering. The engine runs as a separate systemd service (`overlay-display.service`) with a 30fps pygame-ce render loop, creating one borderless always-on-top X11 window per enabled overlay. Communication between the Flask backend and the engine is via a shared JSON file (`data/overlays.json`) polled by mtime every ~1 second. This avoids coupling the render loop to Flask's request-response model and matches the existing pattern of the rotation-display service. Five overlay types are supported: `ticker` (scrolling text bar), `static_text`, `image`, `countdown`, and `qr_code`. Each overlay has an independent `show_over_video` flag — when false, it auto-hides during karaoke playback and auto-shows when playback stops. The `karaoke_playing` state is set by the play/control routes and a `MpvManager.on_karaoke_end` callback.

### Singer Rotation System

The rotation system manages the singer queue during live karaoke shows, with an offline-first architecture:

```
┌─────────────────┐                     ┌──────────────────┐
│  KJ Controller  │  background push    │  Google Sheet     │
│  rotation.py    │────────────────────►│  (backup mirror)  │
│  rotation_store │                     │  (optional)       │
│  .py (SQLite)   │◄── emergency pull ──│                   │
│                 │                     └──────────────────┘
│  After mutation:│
│  writes cache   │
│        │        │
│        ▼        │
│  /tmp/rotation  │     reads every 3s  ┌──────────────────┐
│  _cache.json    │◄───────────────────│  Conky Display    │
│                 │                     │  rotation_data.py │
└─────────────────┘                     │  rotation.conkyrc │
                                        └──────────────────┘
                                                │
                                        ┌───────▼──────────┐
                                        │  HDMI Output      │
                                        │  (singer queue    │
                                        │   on screen)      │
                                        └──────────────────┘
```

**Data flow:** SQLite is the source of truth (`~/kjdata/rotation.db`). `RotationManager` delegates all CRUD to `RotationStore` (SQLite) and optionally syncs to Google Sheets via `SheetSync` (background thread, every 30s). After every mutation, the manager writes a local JSON cache to `/tmp/rotation_cache.json`. The conky display reads this cache every 3 seconds. The system works fully offline — Sheet sync is optional and gracefully handles network failures.

**UI features:** The KJ Controller web UI shows the rotation queue with status badges, preparation badges (READY/DOWNLOADING/URL/UNLINKED), action buttons (Singing, Done, Next, plus more status options), drag-and-drop reordering via drag handles, inline editing (Shift+click), and one-click deletion (Ctrl/Cmd+click). The "Add Singer" form includes a search-as-you-type dropdown that queries local catalog, Karaoke Nerds, and Divebar — selecting a result adds the singer with the file linked or download queued in one action. Divebar cross-referencing works by searching the Divebar catalog with the same query, then matching results to KN tracks locally by (artist, title, brand_code). The media index search also strips punctuation for fuzzy matching. The search dropdown renders identically to the KN panel with community/preferred badges. The Play button auto-advances rotation status: sets the current entry to "Now Singing" and the next entry to "Up Next". Edit mode (inline editing) is isolated from global keyboard/click handlers and polling — the 10-second rotation poll skips re-render while an entry is being edited.

**Conky display:** A full-screen 1920x1080 conky window (`rotation.conkyrc`) renders the queue with gold singer names, colored status badges matching the exact sheet status text, and song info. Uses faux transparency via a wallpaper background image (`rotation-bg.png`). Runs as the `rotation-display` systemd service.

**Reordering:** Drag-and-drop in the UI calls `POST /rotation/move` with `{id, new_position}`. The store atomically shifts positions — no delete+insert, so entries can't be lost mid-operation.

**File linking:** Rotation entries can be linked to media files from the catalog (`POST /rotation/link`). Duration is looked up from MediaIndex and stored in the entry. This enables estimated sing times (shown in the UI) and one-click playback from the rotation view. The unified search dropdown can also link files at add time, and `POST /rotation/download-and-link` queues a download (Divebar or YouTube) that auto-links to the rotation entry on completion. Each entry tracks `download_source`, `download_status`, `download_id`, and `url_fallback` for preparation status.

**Configuration:** `rotation_db_path` (default: `~/kjdata/rotation.db`) is always used. `rotation_sheet_id` + `rotation_credentials_file` in `config.json` are optional — if present, Sheet sync is enabled. `rotation_sync_interval` (default: 30s) controls push frequency.

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
