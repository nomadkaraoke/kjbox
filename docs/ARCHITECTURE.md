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
| `media.py` | ~430 | `MediaIndex` class: scan, validate, download, delete, list. Downloads land in per-source subfolders (`downloads/{youtube,community,gen,upload}/`) as `Artist - Title [media_id].ext` via `_finalize_download_identity()` (deterministic parse → best-effort LLM refine via gen → slug + `media_library` upsert; offline-tolerant). `scan()` resolves a stable `media_id` per file and calls `media_library.upsert_scanned()` — **preserves curated (llm/manual) rows, only refreshing file_path/ext** so a rescan never wipes refinements. `list_items()` joins `media_library` for canonical `Artist - Title` + `needs_review` |
| `naming.py` | ~230 | Pure helpers: `classify_source()`, `media_id_for()`, `parse_identity()` (deterministic best-effort artist/title), `merge_llm_result()` (confidence-gated fold of an LLM result, preserving deterministic fields), `strip_karaoke_noise()`, `extract_media_id()`, `strip_media_id_token()`, `youtube_id_from_url()`, `build_slug_filename()`, `content_hash()`. Source-prefixed `media_id` scheme (`yt-/db-/gen-/nomad-/up-`). No network/LLM |
| `media_library.py` | ~230 | `MediaLibraryStore` class: SQLite store of canonical media identity keyed by `media_id` (artist/title + `*_norm`, `confidence`, `needs_review`, `parse_method`, `file_path`). `upsert()`, `upsert_scanned()` (curation-preserving), `set_metadata()` (manual edit), `apply_parse()` (LLM refine), `update_path()` (migration). Per-thread connections mirroring `RotationStore` |
| `playback.py` | ~290 | `PlaybackCoordinator`: owns filler + one karaoke player, runtime `switch_renderer()`, facade for routes.py |
| `karaoke_player.py` | ~90 | `KaraokePlayer` Protocol: contract both renderer backends implement |
| `filler.py` | ~290 | `FillerVLC`: shared filler-music VLC instance, fade, auto-heal on broken aout |
| `mpv_manager.py` | ~420 | `MpvKaraokePlayer`: mpv karaoke backend (IPC + rubberband pitch + ALSA-release race fix) |
| `vlc.py` | ~340 | `VlcKaraokePlayer`: dual-VLC karaoke backend (CDG-compatible) |
| `chromium.py` | ~160 | `ChromiumManager` class: launch/kill fullscreen Chromium for Browser Mode, PipeWire audio routing |
| `catalog.py` | ~230 | `ExternalCatalog` class: SQLite FTS5 search over external media |
| `zip_playback.py` | ~50 | `ZipPlayback` class: CDG+MP3 ZIP extraction for VLC |
| `frame_analysis.py` | ~80 | Pure Pillow frame math: blank/black detection, frame-diff (motion), `judge_renderer_frames` |
| `playability.py` | ~360 | `PlayabilityChecker`: ffprobe integrity + ffmpeg decode + CDG sub-pipeline + render verdict + `check()`; per-stage `timings`. Inline gate helper used by routes/media |
| `playability_render.py` | ~210 | `XvfbDisplay` (off-screen X, never `:0`; auto-picks a free display via `pick_free_display` so concurrent/batch checks don't collide) + VLC/mpv frame-capture command builders + `render_check()` (optional saved frame) |
| `playability_batch.py` | ~200 | Resumable library walker (mtime/size skip-manifest), JSONL stream, CSV+Markdown VLC-vs-mpv matrix report, CLI `main()`. The full-library sweep harness built on this lives in `scripts/playability-run/` — see the runbook [docs/PLAYABILITY-FULL-LIBRARY-RUN.md](PLAYABILITY-FULL-LIBRARY-RUN.md) (check/pause/resume the device run) |
| `overlay.py` | ~100 | `OverlayManager` class: CRUD, toggle, karaoke_playing state, JSON persistence |
| `karaoke_nerds.py` | ~140 | Karaoke Nerds web scraper: search, parse HTML results, extract YouTube URLs |
| `youtube_search.py` | ~80 | YouTube search via yt-dlp: ytsearch with extract_flat for fast metadata |
| `youtube_health.py` | ~170 | YouTube health checks: yt-dlp/EJS/Deno version detection, cookie validation, PyPI version check (24h cache), pip upgrade |
| `divebar.py` | ~150 | Divebar catalog client: search, download URL generation via Cloud Function API |
| `rotation.py` | ~180 | `RotationManager` coordinator: delegates to `RotationStore` (SQLite) + `SheetSync` (optional), writes display cache, download/gen tracking, undo/redo + revision bump |
| `rotation_store.py` | ~330 | `RotationStore` class: SQLite CRUD for rotation entries, position management, file linking, download/gen tracking, archive, server-side undo/redo history (`rotation_history` table + `rotation_rev` counter, `diff_entries` helper), and the `playability_warning` column + `set_playability_warning()` (tier-2 render-verification flag; setter does not bump `updated_at`) |
| `rotation_sync.py` | ~230 | `SheetSync` class: background thread pushing SQLite state to Google Sheets (optional backup) |
| `gen_client.py` | ~120 | `GenClient` HTTP client for gen API (`X-Admin-Token`): job creation, status polling, download URL retrieval, and `parse_titles(items)` → batch messy-filename→`{artist,title,confidence}` via gen's `POST /api/parse-karaoke-titles` (Vertex Gemini; returns `None` on any failure so callers degrade to deterministic) |
| `gen_poller.py` | ~90 | `GenPoller` background thread: polls gen API for active jobs, auto-downloads completed videos (as `source=gen` → `downloads/gen/`) |
| `scripts/refine_titles.py` | ~90 | Batch-refine `needs_review` `media_library` rows via `GenClient.parse_titles` (DB-only, dry-run default, offline-tolerant). Run with `--batch-size 10` — 100-item batches exceed gen's 20s parse timeout |
| `scripts/normalize_download_library.py` | ~220 | One-off backlog migration: dry-run CSV/MD report → hand-correct → `--from-csv <f> --execute` moves+renames existing downloads into `downloads/<source>/` slug scheme, repoints `media_library` + rotation refs (`relink_references`), backs up DBs first. Masters exempt; collision-guard + move-rollback + CSV-injection-sanitize |
| `sleep_mode.py` | ~100 | `SleepManager` class: enter/exit low-power sleep mode, stop services, unmount SSD |
| `push_dispatcher.py` | ~200 | `PushDispatcher` class: VAPID config, subscription scan, ladder decision (`now_singing`/`up_next`/`up_in_2`), dedup via `last_sent_state`, 500ms debounce, `ThreadPoolExecutor` send pool. Pure helpers at module level (`decide_ladder_step`, `next_entry_for_phone`, `render_payload`). |
| `sing.py` | ~280 | Public `/sing/*` blueprint (landing, search, submit, status, rules, now, manifest, sw, push subscribe/unsubscribe, rename, forget) + token-gate decorator + per-IP rate limiter + host-based route guard + QR-overlay auto-sync helper |
| `sing_store.py` | ~260 | `SingStore` class: SQLite CRUD for `sing_requests` (incl. `user_agent` device capture + `device_id` + `get_requests_for_entries` night-scoped session lookup) + `singer_aliases` (device→canonical-name, `persist_rename`) + `sing_push_subscriptions` + event-token helpers (regenerate / enable / auto-approve) on `rotation_meta` |
| `ua_parse.py` | ~130 | Pure best-effort User-Agent parser (`parse_user_agent`, `summarize`) → friendly browser/OS/device labels; no dependency |
| `routes.py` | ~1000 | Flask Blueprint with all route handlers (includes `/rotation/requests/*` admin endpoints for the public request form). Hosts the playability gates: tier-1 inline `_playability_gate` (link/upload/download hard-block) + tier-2 async render verification (`_enqueue_tier2` → single-worker queue → `_run_tier2_check` against the active renderer, stamps `playability_warning`) |
| `wait_estimate.py` | ~80 | Pure function `compute_estimate(entries, target_id, cfg)` producing `{position, expected_s, range_low_s, range_high_s, spread_source, close_to_front, now_singing}`. Uses tonight's sung-entry variance for the range; falls back to a configurable minimum spread. |
| `sing_resolve.py` | ~120 | Pure decision logic for singer-submission download fallback: `classify_error` (unavailable → advance to next candidate vs transient → retry same) and `next_candidate_index` (bounded by `MAX_CANDIDATES`). No yt-dlp/network/Flask deps → exhaustively unit-tested. |

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
media.py → config.py, utils.py, naming.py, media_library.py
naming.py → utils.py, catalog.py
media_library.py → text_normalize.py (stdlib: sqlite3, threading)
scripts/sync_masters.py → config.py (subprocess `gcloud storage rsync`, requests; run by the nomad-master-sync systemd timer)
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
| GET | `/media` | List all indexed media files (joined with `media_library`: canonical `artist`/`title`/`display_name`, `source`, `media_id`, `needs_review`) |
| POST | `/media/metadata` | Set canonical `{media_id, artist, title}` for a media_library row (Available Songs inline ✎ edit; marks it manual, clears `needs_review`, recomputes `*_norm`; a blank field is preserved, not wiped) |
| POST | `/delete` | Delete a downloaded media file |
| POST | `/rescan` | Reload config and rescan media folders (curation-preserving upsert — refined/edited names survive) |
| GET | `/filler_music` | List available filler music files |
| POST | `/filler_music` | Change active filler music track |
| GET | `/status` | Get player state, current track, timing. Also surfaces the `download_queue` (all queue items, with `source`/`source_detail`) and per-rotation `rotation_downloads` map (`status`/`progress`/`file_path`/`source`/`source_detail`) so the UI can show GCS-vs-Drive-vs-YouTube on download badges in real time. Also carries `simple_mode` so the KJ UI applies the `body.simple-mode` CSS class on every 2s poll. |
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
| GET | `/rotation` | Get singer rotation queue (non-done entries, with estimated times). Also returns `rev` (monotonic revision) + `history` (`{undo, redo, undo_label, redo_label}`) for the undo/redo buttons |
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
| POST | `/rotation/undo` | Server-side undo. No `confirm` → preview diff (`{removed, added, changed}`) + `rev`, applies nothing. `{confirm: true, expected_rev}` → apply (rejected as `stale` if `expected_rev` is out of date) |
| POST | `/rotation/redo` | Server-side redo — same two-phase preview/confirm + `expected_rev` guard as `/rotation/undo` |
| POST | `/rotation/restore` | Restore rotation from Google Sheet backup (no body). Legacy `{entries}` snapshot path retained for compatibility; KJ undo/redo now uses `/rotation/undo` `/redo` |
| GET  | `/rotation/requests` | List public sing requests (filter by `?status=pending\|approved\|rejected`) + counts |
| GET  | `/rotation/requests/config` | Current event token, enabled flag, auto-approve flag, accept-make-requests flag, simple-mode flag, public/local URLs, pending count |
| POST | `/rotation/requests/config` | Regenerate token / toggle enabled / toggle auto-approve / toggle accept-make-requests / toggle simple-mode |
| GET  | `/rotation/requests/qr.svg` | SVG QR code for event URL (`?scope=public\|local`) |
| POST | `/rotation/requests/<id>/approve` | Approve request → create rotation entry via source-specific dispatch. Optional body `{skip_download: true}` for `youtube`/`kn`/`divebar` creates an unlinked entry (KJ uses the rotation 🔗 button to attach a file manually) |
| POST | `/rotation/requests/<id>/edit` | Edit singer name / artist / title on a pending request |
| POST | `/rotation/requests/<id>/reject` | Mark request rejected (silent to singer) |
| GET  | `/sing/` | **PUBLIC** — singer-facing landing page (requires `?t=<token>`) |
| GET  | `/sing/search` | **PUBLIC** — search local + Karaoke Nerds catalog (requires token) |
| POST | `/sing/submit` | **PUBLIC** — create a pending request (rate-limited per IP). When `simple_mode` is on, the source allowlist is narrowed to `local`/`divebar`/`kn` and other types return 400 `simple_mode_disabled_source`. |
| GET  | `/sing/status/<id>` | **PUBLIC** — singer's own request status + rotation position |
| GET  | `/sing/my-requests` | **PUBLIC** — multi-id status feed (`?ids=1,2,3`, max 20) for the multi-song done screen |
| GET  | `/sing/now` | **PUBLIC** — lightweight now-singing / up-next / queued-count for the landing widget |
| GET  | `/sing/rotation` | **PUBLIC** — full active rotation with cumulative wait estimates for the landing-page expander |
| POST | `/sing/rename` | **PUBLIC** — singer renames themselves persistently: rewrites the entries/requests they own (edit_token-gated) + records a device alias so future submissions keep the new name |
| POST | `/sing/forget` | **PUBLIC** — drops this device's name alias (used when a new person takes over the same phone via the landing "switch" link) |
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

### Original Vocals Guide
When a NOMAD master with a matching guide in `NOMAD-vocals-padded/` plays, an "Original Vocals" slider mixes the original singer's isolated vocals under the karaoke via mpv `--lavfi-complex` amix (shared rubberband pitch). Auto-enables by brand match — no config. Full operational model, guide dataset, and the alignment pipeline: [ORIGINAL-VOCALS.md](ORIGINAL-VOCALS.md).

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
`ZipPlayback` extracts CDG+MP3 ZIP files to a temp directory. ZIP entries are validated against path traversal (`..` or absolute paths) before extraction; legacy Deflate64 discs that Python's `zipfile` can't read fall back to the system `unzip`. Extracted files are chmod'd world-readable so the player (running as `dietpi`/`nomad`) can access them. The temp dir is cleaned up before each new extraction.

**How the two renderers consume a CDG zip differs — this matters, because handing the wrong file to mpv plays audio with no graphics:**

- **VLC** is given the `.mp3` path (`extract_and_get_mp3`) and auto-discovers the matching `.cdg` sibling in the same directory for the lyrics/graphics overlay. This is VLC's native CDG behaviour.
- **mpv** renders **no** CDG graphics from the `.mp3`, and a bare `.cdg` has **no** audio. So mpv is instead handed the `.cdg` directly (graphics) with the `.mp3` attached as an external audio track. `ZipPlayback.current_cdg_path()` returns the `.cdg` paired with the chosen `.mp3` (same dir, preferring the shared stem). mpv's renderer attaches the audio via the IPC command `["audio-add", <mp3>, "select"]` **after** `loadfile <cdg>` — deliberately *not* a `loadfile` option, because the `loadfile` options-argument position changed between mpv 0.37 (device) and 0.38+, whereas `audio-add` is stable across both and needs no value escaping. If the audio attach fails, playback is **aborted** (`stop` + `audio_error`) rather than starting a silent video the singer can't hear.

`/play` (`routes.py`) picks the source by `render_mode`: VLC → the `.mp3`; mpv → the `.cdg` with the `.mp3` threaded through `PlaybackCoordinator.play_video(..., audio_file=…)` → `KaraokePlayer.play(..., audio_file=…)`. The `audio_file` parameter is part of the `KaraokePlayer` protocol; `VlcKaraokePlayer` accepts and ignores it (it discovers the sibling itself). Verified live on the device (mpv 0.37): track-list shows `video=cdgraphics` + external `audio=mp3`, time-pos advancing, graphics on the HDMI display. See `docs/AUDIO.md § Karaoke Renderer Toggle`.

### Dynamic Overlay System
The overlay system uses a three-component architecture: (1) the KJ Controller web UI for configuration, (2) the Flask backend (`overlay.py`) for CRUD and state management, and (3) a standalone overlay engine (`desktop/overlay_engine.py`) for rendering. The engine runs as a separate systemd service (`overlay-display.service`).

**Renderer (v2, 2026-06):** a single **GTK3 + Cairo** window — RGBA (true per-pixel alpha), always-on-top, **click-through** (empty input shape) and **non-focus-stealing** (`accept_focus=False`/`focus_on_map=False`) — composites all overlays onto one transparent surface over the desktop wallpaper / live video. Because empty regions are genuinely transparent and the window never takes focus, it cannot hide or steal focus from the fullscreen VLC video. This replaced the previous conky "home screen" (a full-screen `dock` window whose pseudo-transparency painted the wallpaper, which could be re-stacked above the video on focus changes) and the earlier multi-window pygame-ce engine (SDL2 cannot do real per-pixel alpha). A startup/periodic **compositor guard** (`Gdk.Screen.is_composited()`) refuses to map the window if no compositor is running, so it can never render opaque over the video.

**Layers:** `desktop/rotation_source.py` (pure stdlib) parses `/tmp/rotation_cache.json` into structured data; `desktop/overlay_painters.py` (pure pycairo, no GTK — headless-testable) holds one painter per overlay type; `desktop/overlay_engine.py` (the only `gi`/GTK module) owns the window + render loop and a gi-free `--render-png` mode for headless/on-device visual checks. Communication with the Flask backend is via `data/overlays.json` polled by mtime every ~1 second. Six overlay types are supported: `rotation_list` (the between-songs home screen — heading, stats, singer list with status badges/paid hearts, page cycling), `ticker` (scrolling bar; `source='rotation'` composes the "up next" text directly from the rotation cache; loops seamlessly — a configurable `loop_separator` glyph, default `♪`, is appended to form a repeating unit that tiles back-to-back so there's no blank gap after the last singer), `static_text`, `image`, `countdown`, and `qr_code`. Each overlay has an independent `show_over_video` flag — when false it is hidden during karaoke playback (e.g. the rotation list) and shown when playback stops. The `karaoke_playing` state is set by the play/control routes and a `MpvManager.on_karaoke_end` callback.

**Partial redraw + reserved top strip (4K frame-drop fix, 2026-07):** the render loop invalidates only each **animated overlay's own bounding box** (`queue_draw_area(*painter.bbox())`), not the whole window. Previously every ticker frame called `queue_draw()`, forcing the compositor to re-blend the entire screen — including the 4K video region — 30×/s, which cost measurable frame drops on the N97 iGPU. Complementing this, the karaoke video is rendered **below a reserved top strip** (`video_top_margin_px`, default 80): `mpv_manager` launches mpv borderless at `--geometry=<W>x<H-margin>+0+<margin>` (was `--fs`; margin 0 restores fullscreen for rollback), so a top-strip ticker's damage rect never overlaps the video window and the compositor stops re-blending video pixels for the ticker entirely. This is a **persisted cross-process contract, not runtime IPC**: kj-controller writes `video_top_margin_px` into `overlays.json` (`OverlayManager.set_video_top_margin`, at startup) and sizes the video from the same `config.py` value; the overlay engine reads that strip height back out (`load_config` injects it into each ticker's config as `_strip_h`) and a `position:'top'` ticker sizes its bar to **fill** the strip — so there's no wallpaper gap between the ticker and the video, and the ticker's damage rect is exactly the reserved strip. Both sides independently read one persisted setting; `video_top_margin_px` is the single source of truth for both the video geometry and the ticker height, so they can't drift. VLC honours the same strip (device-validated 2026-07): it launches windowed and — because it maps its video window only once a song plays and ignores its own geometry CLI flags — `VlcKaraokePlayer._position_window` places the window **per-play** with `wmctrl`, matched by the unambiguous `VLC media player` title (the filler VLC is audio-only with no window; VLC leaves `_NET_WM_PID` unset, so the title is the only key). It drives a short **closed loop** (request → settle → measure → adjust the next request by the observed error) until the window lands within 2px of target — needed because xfwm4 offsets wmctrl moves by a fixed frame-extent amount *and* VLC nudges/resizes its own window while a 4K file loads, so a single measure-and-correct can act on a stale position. `margin_px <= 0` restores fullscreen for both engines (clean rollback). mpv remains the default and the only engine that can *hardware-decode* 4K on this box — VLC 3.0's VAAPI decoder never engages, so VLC always software-decodes (fine at 1080p, glitchy at 4K).

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

**Data flow:** SQLite is the source of truth (`~/kjdata/rotation.db`). `RotationManager` delegates all CRUD to `RotationStore` (SQLite) and optionally syncs to Google Sheets via `SheetSync` (background thread, every 30s). After every mutation, the manager bumps the `rotation_rev` counter and writes a local JSON cache to `/tmp/rotation_cache.json`. The conky display reads this cache every 3 seconds. The system works fully offline — Sheet sync is optional and gracefully handles network failures.

**Undo/redo:** History is server-side and shared across all KJ devices, so it survives a service restart and reflects every writer (singer self-submissions, downloads, other tabs) — unlike the old per-browser snapshot stack that silently overwrote concurrent changes. Before each *meaningful* mutation `RotationManager` checkpoints the full rotation onto the `rotation_history` undo stack (capped at `MAX_HISTORY`, redo stack cleared); background tracking updates are not checkpointed. `/rotation/undo` and `/rotation/redo` preview a `diff_entries` summary before applying, and the apply is guarded by `expected_rev` so a change between preview and confirm is rejected as `stale`. Restores preserve `created_at` and live file-link fields; archiving a night clears the history.

**UI features:** The KJ Controller web UI shows the rotation queue with status badges, preparation badges (READY/DOWNLOADING/URL/UNLINKED), action buttons (Singing, Done, Next, plus more status options), drag-and-drop reordering via drag handles, and inline editing (Shift+click — shift-clicking the song text lands the cursor in the song field, a singer name in the singer field; deletion is done from the Delete button in edit mode, there is no click-to-delete gesture). The "Add Singer" form includes a search-as-you-type dropdown that queries local catalog, Karaoke Nerds, and Divebar — selecting a result adds the singer with the file linked or download queued in one action. Divebar cross-referencing works by searching the Divebar catalog with the same query, then matching results to KN tracks locally by (artist, title, brand_code). The media index search also strips punctuation for fuzzy matching. The search dropdown renders identically to the KN panel with community/preferred badges. The Play button auto-advances rotation status: sets the current entry to "Now Singing" and the next entry to "Up Next". Edit mode (inline editing) is isolated from global keyboard/click handlers and polling — the 10-second rotation poll skips re-render while an entry is being edited.

**Conky display:** A full-screen 1920x1080 conky window (`rotation.conkyrc`) renders the queue with gold singer names, colored status badges matching the exact sheet status text, and song info. Uses faux transparency via a wallpaper background image (`rotation-bg.png`). Runs as the `rotation-display` systemd service.

**Reordering:** Drag-and-drop in the UI calls `POST /rotation/move` with `{id, new_position}`. The store atomically shifts positions — no delete+insert, so entries can't be lost mid-operation.

**File linking:** Rotation entries can be linked to media files from the catalog (`POST /rotation/link`). Duration is looked up from MediaIndex and stored in the entry. This enables estimated sing times (shown in the UI) and one-click playback from the rotation view. The unified search dropdown can also link files at add time, and `POST /rotation/download-and-link` queues a download (Divebar or YouTube) that auto-links to the rotation entry on completion. Each entry tracks `download_source`, `download_status`, `download_id`, and `url_fallback` for preparation status.

**Download source classification:** Divebar serves either a GCS community-mirror URL (fast) or a Google Drive URL (slower, original storage) per file. `divebar.classify_download_url(url)` inspects the host and returns `'gcs'` / `'drive'` / `None`; every divebar enqueue site stamps `source_detail` on the queue item, and `/status` surfaces it on both the `download_queue` items and the per-entry `rotation_downloads` map. The KJ admin UI uses this to render distinct badges (`GCS DL` / `DRIVE DL` / `YT DL`) on the rotation prep-badge and a coloured source pill in the download-queue panel.

**On-disk extension (`utils.divebar_ext`):** the format of a Divebar file is not always `.mp4` — the GCS mirror is mostly `mp4` and `zip` (CDG+MP3), plus some bare `cdg`/`mp3`. The download worker derives the on-disk extension from the **resolved download URL path**, which the GCS mirror always carries URL-encoded (e.g. `…/CKK%20-%20Incubus%20-%20Admiration.zip`); when the URL has no usable extension (Drive URLs don't) it falls back to the catalog `format` the frontend threads through, then to `.mp4`. `divebar_ext` only ever returns a known media extension (`config.MEDIA_EXTENSIONS`). This matters because `playability.classify_kind` keys purely off the extension: a CDG zip written as `…​.mp4` is classified `video`, fails the ffprobe integrity gate, and is deleted — surfacing as "Download failed". With the correct `.zip` extension it validates via the existing `cdg_zip` path (extract + CDG + audio decode) and lands cleanly. All three enqueue sites (`divebar_download`, `download_and_link_rotation`, `approve_sing_request`) call `divebar_ext` and pass it to `build_divebar_filename(..., ext=…)`.

**Configuration:** `rotation_db_path` (default: `~/kjdata/rotation.db`) is always used. `rotation_sheet_id` + `rotation_credentials_file` in `config.json` are optional — if present, Sheet sync is enabled. `rotation_sync_interval` (default: 30s) controls push frequency.

### Public Singer Request Form

Singers submit song requests from their own phones via a QR code instead of handing the KJ a paper slip. See [archive/2026-04-18-public-request-form-design.md](archive/2026-04-18-public-request-form-design.md) for the full design spec.

```
   Singer's phone          Cloudflare tunnel           kj-controller (Flask)
 ┌──────────────────┐   ┌──────────────────────┐    ┌─────────────────────┐
 │ Scan QR → /sing/ │──▶│ sing.nomadkaraoke... │───▶│  sing_bp  /sing/*   │
 │   (token in URL) │   │   no Access policy   │    │  (token gate, rate  │
 └──────────────────┘   └──────────────────────┘    │   limit per IP)     │
                                                    │                     │
       OR venue wifi                                │  routes_bp          │
 ┌──────────────────┐       travel router           │  /rotation/requests │
 │ http://<ip>/sing │──▶ DHCP-reserved LAN IP ─────▶│  (KJ admin, gated   │
 └──────────────────┘                               │   behind Access)    │
                                                    └──────┬──────────────┘
                                                           │
                              ┌────────────────────────────┼───────────────────┐
                              │  host guard: sing.* host → sing_bp only        │
                              └────────────────────────────┬───────────────────┘
                                                           ▼
                                                 ~/kjdata/rotation.db
                                                  (sing_requests table +
                                                   rotation_meta token rows)
```

**Public routes** (`sing_bp`): `GET /sing/`, `GET /sing/search`, `POST /sing/submit`, `POST /sing/validate`, `GET /sing/status/<id>`, `GET /sing/now`, `GET /sing/rotation`. All except `/` and `/validate` require a valid, enabled event token — provided as `?t=<token>` in the QR URL and kept in a session cookie. Rate limiting: 5 submits / 5 min per IP on `/submit`, 10 attempts / 5 min per IP on `/validate` (separate bucket so legit submissions aren't blocked by a brute-force scanner).

**Public host mounts `sing_bp` at root:** on `sing.nomadkaraoke.com` the QR target is `https://sing.nomadkaraoke.com/?t=XXXX` — no `/sing/` segment for singers to read off the screen. `install_public_host_rewriter` (in `sing.py`) is a small WSGI middleware that prepends `/sing` to `PATH_INFO` when `Host` matches the configured public-host set. The blueprint stays mounted at `/sing/` internally so the admin host (`nomadpc.local`, `kjbox.nomadkaraoke.com`) — where `/` is the KJ controller UI — keeps working unchanged. Both `create_app()` and `start_app()` install it; the duplication is tracked as a follow-up consolidation.

**Event token:** a 4-digit numeric code (`0000–9999`). Short enough to read off the venue screen and type on a phone numpad; brute-force-protected by the `/validate` rate limiter. `regenerate_token()` never returns the same code twice in a row.

**No-token visitor flow:** `GET /sing/` (or `/` on the public host) when the store is enabled but no valid token is present renders a code-entry form instead of the old "closed" message. The form auto-submits on the 4th digit via `POST /sing/validate` and redirects to `/?t=XXXX` on match. The "closed" template branch is now only used when the KJ has actually paused requests (`store.is_enabled() is False`).

**Admin routes** (on `routes_bp`): `/rotation/requests/*` — list, approve, edit, reject, config (token + flags), and `qr.svg` QR generator. Never mounted under `/sing/*`, so they cannot leak via the public tunnel.

**Host-based route guard:** a Flask `before_request` hook on the app reads `sing_public_host` (+ aliases) from `config.json`. If the incoming `Host` header matches, only endpoints registered on `sing_bp` are allowed; everything else returns 404 — defence-in-depth in case an endpoint is accidentally added elsewhere.

**Event token lifecycle:** stored in `rotation_meta` as `request_token` + `request_token_enabled` + `request_auto_approve`. Regenerated automatically when the KJ archives a rotation, and manually from the Requests settings modal. Sleep-mode entry disables requests; exit does **not** auto-re-enable (prevents surprise re-opening).

**Approval dispatch:** `approve_sing_request(app, req, skip_download=False)` in `routes.py` creates a rotation entry based on `source_type` — `local` uses `rotation.add_entry(..., file_path=...)`, `divebar|youtube|kn` adds an entry + queues a download on the existing worker, `make` creates a gen-API job via the existing `GenClient`. `skip_download=True` (route reads it from JSON body) suppresses the download for the `divebar|youtube|kn` branch and creates an unlinked entry — used when the KJ has previewed the pasted YouTube URL and wants to attach a different file via the rotation 🔗 button. Auto-approve on submission calls the same helper inline (always `skip_download=False`).

**Grouped search + `kj_pick` (Phase A, 2026-04-23):** `GET /sing/search` returns `{songs: [{artist, title, version_count, in_library, versions: [...]}]}` — one group per unique `(artist, title)` after normalization (feat./paren/apos/punct/WS stripping). `_group_search_results` in `routes.py` does the collapsing; admin-side `unified_search()` callers still receive the flat shape via the kwarg default. The singer's "Let the KJ pick" CTA submits `source_type="kj_pick"` with the full `versions[]` snapshot in `source_meta`. The admin approval route (`POST /rotation/requests/<id>/approve`) then accepts `{version_index}` — `_pick_version_from_kj_pick` translates the picked version into concrete `source_type/ref/meta` fields, `SingStore.update_request_source` writes them back, and `approve_sing_request` dispatches normally. Auto-approve is explicitly skipped for `kj_pick` (otherwise the rotation entry would have no file attached).

**Per-version expander (Phase B, 2026-04-23):** `static-sing/sing.js` `renderSearch` gained an inline expander that splits the `versions[]` snapshot into 4 fixed-order sections (library → divebar → online → community). Purely client-side: the backend contract is unchanged from Phase A. A "Pick this version →" button on each candidate submits a direct `local` / `divebar` / `kn` request (not `kj_pick`), so auto-approve continues to work and the admin sees a standard one-tap Approve. Long paths (local.path, divebar.drive_path) render inside a `<details>` block with `word-break: break-all` monospace. A one-time "Commercial vs Community" explainer appears on the first expand and dismisses via localStorage key `sing_rules_commercial_community_seen`.

**Empty-state triage + make-request toggle (Phase C, 2026-04-23):** When `/sing/search` returns `songs: []` and the query ≥ 3 chars, `renderEmptyStateTriage()` shows a three-card layout: paste YouTube link / ask the KJ to make it / make-it-yourself on gen.nomadkaraoke.com. The KJ controls card 2's visibility via a new `sing_accept_make_requests` meta flag (`rotation_meta` table, default on) exposed on `GET/POST /rotation/requests/config` as `accept_make_requests`. The same flag rides on `GET /sing/search` responses and the landing template dataset so the UI has it before the first search. `POST /sing/submit` enforces the flag server-side (400 `make_requests_disabled`) as defence-in-depth against stale clients. The DIY-via-gen card is just an `<a href="https://gen.nomadkaraoke.com" target="_blank">` — no backend involvement; a gen-published YouTube URL pasted back into card 1 flows through the existing `source_type=youtube` dispatch unchanged.

**Simple KJ Mode (2026-05-28):** Persistent `kj_simple_mode` flag in `sing_meta` (default off) shrinks both the singer SPA and the KJ UI to the bare minimum needed for a stand-in operator running a QR-only show. Server-side, `POST /sing/submit` narrows the source allowlist to `{local, divebar, kn}` (400 `simple_mode_disabled_source` on `youtube`/`make`/`kj_pick`) — defence-in-depth against stale singer PWAs. The flag rides on `GET /status` (2s heartbeat poll) so the KJ UI applies `<body class="simple-mode">` automatically; the same flag ships on `GET /sing/` template context (`data-simple-mode` on `#sing-root`) and `GET /sing/search` responses for the singer SPA. KJ-side CSS hides the right column (KN/YT/Divebar/Upload/Songs/Browser), the rotation manual-add controls, the overlay panel, and every System subsection except the Mode toggle itself. Singer SPA suppresses the empty-state triage cards (paste YouTube / ask KJ / DIY-via-gen) — replaced by a single "We don't have that one. Talk to the KJ." message — and removes the `kj_pick` deferral on multi-version songs (the singer must pick a specific version). Toggle UI is a switch in `templates/index.html`'s System → Mode subsection, wired to `toggleSimpleMode()` in `app.js` which POSTs to the existing `/rotation/requests/config` endpoint.

**QR-overlay auto-sync:** `qr_code` overlays with `config.follow_event_url=True` are automatically updated to point at the current event URL whenever the token regenerates. KJs opt in by ticking the checkbox on the overlay editor.

**Singer UI** lives under `static-sing/` (separate tree from the KJ UI's `static/`) and is served from `sing_bp`'s `/sing/static/*` URL path. Minimal vanilla-JS SPA with four steps: landing → identity (name + phone, persisted to `localStorage`) → search → confirm → confirmation that polls `/sing/status/<id>` every 15s.

Additional `static-sing/` assets added in sub-project #4:
- `static-sing/sw.js` — service worker. Handles `push` + `notificationclick`. Shell cache for offline page render. Served dynamically at `/sw.js` (rewritten internally to `/sing/sw.js`) on the public host with scope `/`, and at `/sing/sw.js` with scope `/sing/` on the admin host. `sing.js` detects the base path at runtime from `window.location.pathname` and registers accordingly.
- `static-sing/manifest.json` — served dynamically by `GET /sing/manifest.json` (or `/manifest.json` on the public host) with the current token injected into `start_url`. `start_url` / `scope` are host-aware: `/` on the public host, `/sing/` on the admin host.

**Response shape change (sub-project #4):** `GET /sing/status/<id>` now includes `estimate` and `now_playing` sub-objects. Legacy top-level `position`, `estimated_wait_s`, `queue` keys are kept for the client rollout window.

**Duet partners + multi-song done screen (2026-05-15):** Singers can attach up to 3 duet partners (name + optional phone) on the confirm screen via a new `additional_singers TEXT NULL` column on `sing_requests` (JSON array). `POST /sing/submit` validates the field (max 3, name required, phone format optional). `approve_sing_request` builds a `singers=[primary, …partner_names]` list and passes it to `rotation.add_entry(...)` — the existing `singers_json` plumbing on `rotation_entries` joins names with ` & ` for the legacy `singer` text column and persists the structured list. KJ admin approval card renders a duet block with `sms:` links for partner phones. The singer's done screen now lists all their submitted requests via a new `GET /sing/my-requests?ids=…` endpoint (max 20 ids per call, returns `{now_playing, requests:[{request, estimate?, performed}]}`) and includes a "+ Request another song" button that resets song-picking state while preserving identity. Request ids are tracked in `localStorage` (`sing_my_request_ids`) scoped per token so yesterday's ids don't leak into a new event. Partner phones are display-only (no push subscriptions for partners) — the KJ texts them manually from the admin card.

**Singer session provenance + smarter Merge (2026-08-13):** Motivated by the "two Chailas" incident (a self-registered singer vs a duet-partner label typed into someone else's request looked like two people). Every `/sing/submit` and `/sing/requests/<id>/change` now stores the submitting device's `User-Agent` on a new `sing_requests.user_agent` column. `routes._add_singer_session_info(singer_stats)` (called alongside `_add_last_sang_to_singer_stats` in `GET /rotation` and every singer action response) attaches a `session` block to each singer by matching their rotation-entry ids to linked requests via `SingStore.get_requests_for_entries(entry_ids, night_started)` (night-scoped, fails closed like the SMS phone lookup): `origin` is `singer_ui` (owns a linked request whose `singer_name` is theirs → `has_device`, carries `device`/`phone`/`request_count`/`sources`), `duet_partner` (only appears in another request's `additional_singers`), or `kj_added` (no linked request). The frontend shows a 📱 icon only on `singer_ui` singers (click → device-details popup) and replaces the inline Merge dropdown with a modal: searchable singer list (device-linked first), then a confirm step spelling out KEEP vs REMOVE, combined sung/queued totals, and a real-device warning + **Swap** that defaults the keeper to the phone-linked singer. `ua_parse.py` turns the raw UA into a friendly summary (Android UAs also yield the model, e.g. SM-S911B; iOS exposes only "iPhone").

**Done-screen ordering + sung-song clearing (2026-07-17, v0.88.0):** The "Your songs tonight" list previously rendered in submission order (the client passes its `localStorage` id list to `/my-requests`, which echoes them back in that order) and never dropped sung songs (a `sing_request` stays status `approved` after its rotation entry is sung, and `get_rotation()` returns the ACTIVE queue only, so the sung entry got a `position=None` estimate and lingered as "Added to the queue" all night). Fix: `/my-requests` now sets a per-item `performed` boolean — for a linked entry not in the active queue it looks up `rotation.store.get_entry(...)` and marks `performed=True` when the entry is Done/Left (no estimate attached). The client (`sing.js`) sorts the active list into sung order (now singing → queue position → awaiting-KJ) and moves performed songs into a collapsed **"✓ Already sung tonight (N)"** section that's read-only (no cancel/change/reorder). `performed` songs are excluded from `_liveSongs`, so the persistent "🎤 My songs (N)" bar count and boot smart-restore ignore them (a singer who has sung everything lands on the request screen, not a stale list).

**Persistent singer rename via device aliases (2026-08-27, v0.97.0):** A singer's display name is free text they type on their phone and cache in `localStorage`, re-sent verbatim on every submission — so a rename (KJ-side or the singer's own) used to be lost the next time that device added a song, and the singer re-split under their old name. Fixed with a stable device identity + an alias mapping. The singer SPA generates an opaque `device_id` once (crypto-random, `localStorage: sing_device_id`) and sends it with every `/sing/submit`; it's stored on a new `sing_requests.device_id` column. A new `singer_aliases` table (`device_id` PK → `canonical_name`) is consulted at submit time — if the device has an alias, it overrides the typed name. Aliases persist across nights on purpose (a regular keeps their chosen name; `device_id` is stable per browser, so there's no cross-night id-reuse hazard). **Two write paths:** (1) **Singer self-service** — `POST /sing/rename` takes the device's own `{id, edit_token}` list, rewrites the rotation entries + requests it proves ownership of (`RotationStore.rename_singer_in_entries`, tolerant + duet-aware), and upserts the alias; the SPA exposes it as an "edit name" affordance on the landing, search, and done screens (the landing "switch" link now also `POST /sing/forget`s the alias, since it means a *different* person on the same phone). (2) **KJ-side** — `rename_singer_route`/`merge_singers_route` call `SingStore.persist_rename(old, new, night_started)`, which aliases every device that submitted under the old name tonight and rewrites those requests' `singer_name` so session provenance keeps matching. Both paths are best-effort on the alias write — a failure there never fails the rename itself.

### Singer Web Push (sub-project #4)

- `push_dispatcher.py` hooks into `RotationManager._after_mutation()`. Every rotation mutation triggers a 500ms-debounced dispatch that scans active subscriptions in `sing_push_subscriptions` (scoped to the current event token) and sends `up_in_2` / `up_next` / `now_singing` pushes via `pywebpush` on a 2-worker thread pool.
- Dedup is via a `last_sent_state` JSON column on each subscription — the same `(entry_id, ladder_step)` pair never fires twice. When the target entry changes (previous Done'd, next-closest becomes the new target), the ladder resets cleanly.
- Approve/reject from admin routes bypass the rotation scan and call `notify_request_decision(...)` for immediate singer feedback.
- VAPID keypair auto-generates on first boot in `_bootstrap_vapid_keys` (app.py) and persists to `config.json` (gitignored). Public key is exposed to the singer page via `<meta name="vapid-public-key">`.
- Client registers the SW at a runtime-detected base (`/sw.js?t=<token>` with scope `/` on the public host; `/sing/sw.js?t=<token>` with scope `/sing/` on the admin host) so the SW has the token for constructing `notificationclick` URLs. Dynamic manifest also carries the token in `start_url`.
- Offline events (no internet): singer's page surfaces an honest banner via `navigator.onLine` + consecutive-poll-failure detection. Push itself can't work without internet (FCM/APNS round-trip); the held-tab polling flow remains the fallback.
- iOS Safari requires Add-to-Home-Screen (installed PWA) before push works. The confirmation page detects iOS non-standalone and renders an instructional card explaining the install flow.
- Housekeeping: on event-token regeneration, `cleanup_stale_push_subscriptions` deletes subs on other tokens older than 7 days. Keeps the table bounded.

### Singer submission download fallback

When an approved singer submission's YouTube download fails, the download worker auto-heals instead of surfacing a dead ❌ the KJ must fix by hand (motivated by the 2026-07-09 live incident where a picked "Say My Name" version was a *private video*).

- **The download attempt is the probe.** `media.download_video` swallows yt-dlp errors and returns `(None, None)`, so it now records the reason on `media._last_error`. On failure the single-threaded `_download_worker` reads it and calls `sing_resolve.classify_error`.
- **Advance vs retry.** `unavailable` (private/removed/blocked) → advance to the next ranked candidate version; `transient` (timeout/429/`bgutil`/network, and any *unknown* error) → retry the same candidate up to `MAX_TRANSIENT_RETRIES`, then advance. Bounded by `MAX_CANDIDATES` (3). Because the worker is sequential, retries re-queue (back of line) rather than sleep-blocking.
- **Candidate list.** `approve_sing_request` attaches a ranked YouTube candidate list to the queue item, built from the `versions[]` snapshot via the existing `_pick_version_from_kj_pick` translator + `_ranked_version_indices`. Since binding a `kj_pick` version rewrites `source_meta`, `_preserve_versions_meta` re-attaches the snapshot at both binding sites so the list survives. v1 falls back across YouTube-type candidates only (cross-source local/Divebar is a documented follow-up).
- **Outcome.** On a successful fallback the request source is rebound (`update_request_source`) so `/my-requests` reflects the version that landed, and the singer gets a `resolved_alt` push. When every candidate is exhausted the entry surfaces the normal terminal ❌ for the KJ plus an `unavailable` push. Non-sing downloads (KJ manual, Divebar) are untouched.

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
