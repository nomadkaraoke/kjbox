# KJ Controller — Architecture

## Overview

KJ Controller is a Flask + vanilla JS web app for managing live karaoke shows. It runs on physical devices (NomadPi, NomadPC) and controls dual VLC/mpv instances, YouTube downloading, song catalog search (~415K songs), display overlays, and VNC screen preview.

---

## Module Map

| Module | Responsibility |
|---|---|
| `app.py` | App factory (`create_app`) and entry point (`start_app`). Bootstraps all services, mounts blueprints. |
| `routes.py` | REST API endpoints (search, queue, playback, catalog, rotation, etc.) |
| `catalog.py` | `ExternalCatalog` — SQLite FTS5 song search with rapidfuzz fuzzy fallback |
| `text_normalize.py` | Single source of truth for song-text normalization (Python); mirrored by `static/text_normalize.js` |
| `media.py` | `MediaIndex` — scans local folders, validates files, tracks download state |
| `rotation.py` | `RotationManager` — coordinator: delegates to `rotation_store` + `rotation_sync` |
| `rotation_store.py` | SQLite CRUD for rotation entries, position management, archive |
| `rotation_sync.py` | Optional background push to Google Sheets |
| `mpv_manager.py` | `MpvManager` — mpv karaoke with rubberband pitch and VLC filler |
| `vlc.py` | `VLCManager` — legacy player, kept for rollback |
| `playback.py` | `PlaybackCoordinator` — owns filler + one karaoke player; swappable at runtime |
| `overlay.py` | `OverlayManager` — CRUD for display overlays, JSON persistence |
| `sing.py` | Public `/sing/*` blueprint, token gate, host-based route guard, PWA manifest + service worker route |
| `sing_store.py` | `SingStore` — SQLite CRUD for sing requests, push subscriptions, event tokens |
| `push_dispatcher.py` | `PushDispatcher` — VAPID, Web Push, subscription fan-out, ladder decision, dedup |
| `wait_estimate.py` | Pure `compute_estimate()` for singer-facing wait times |
| `audio_monitor.py` | `AudioMonitor` — monitors audio device health |
| `chromium.py` | `ChromiumManager` — manages Chromium for display kiosk mode |
| `sleep_mode.py` | `SleepManager` — persistent sleep state across restarts |
| `karaoke_nerds.py` | Karaoke Nerds web scraper (search, parse, YouTube URLs) |
| `youtube_search.py` | YouTube search via yt-dlp (fast metadata-only) |
| `youtube_health.py` | YouTube health checks, cookie validation, EJS/Deno detection |
| `config.py` | Constants, platform detection (`is_pi()`), config loading |
| `utils.py` | `log_message` helper |
| `version_priority.py` | Version selection logic for multi-format catalogs |
| `zip_playback.py` | `ZipPlayback` — plays zipped CDG+MP3 karaoke packs |
| `preview.py` | `PreviewService` — resolve a file/candidate to a browser-preview delivery mode + opaque serving tokens; `parse_range` |
| `preview_cache.py` | `PreviewCache` — content-addressed, `.done`-gated, LRU-evicted on-disk cache for transcodes / GCS blobs / CDG extracts |
| `preview_transcode.py` | `TranscodeManager` — single-job, niced ffmpeg→HLS transcode for exotic video previews |
| `static/preview.js` | Preview modal + per-mode player dispatch + `previewButtonHtml` factory (frontend) |
| `static/cdg.js` | Dependency-free CD+G canvas renderer driven by `<audio>` currentTime (frontend) |

---

## Catalog & Song Search

### Song-text normalization

`text_normalize.py` is the **single source of truth** for all song-text normalization in Python. A JavaScript twin, `static/text_normalize.js`, mirrors it exactly. Lockstep is enforced by a node-driven parity test that runs both implementations on the same corpus and fails if they diverge.

**Pipeline order** (order is significant):

1. **Unicode NFD + diacritic strip + Latin fold** — decompose accented chars, drop U+0300–U+036F combining marks, fold non-decomposable Latin chars (ø→o, æ→ae, ß→ss, þ→th, …).
2. **Lowercase**
3. **Strip `feat.` / `ft.` / `featuring` qualifiers** — removed before conjunction expansion so "feat. Simon & Garfunkel" doesn't become "feat. Simon and Garfunkel".
4. **`&` / `+` → ` and `** — fixes the "Simon and Garfunkel" vs "Simon & Garfunkel" link bug.
5. **Drop apostrophes; punctuation → space** — `rock'n'roll` → `rocknroll`; remaining non-word chars become spaces.
6. **Token-level abbreviation expansion** — `pt`→`part`, `pts`→`parts`, `vs`→`versus`.
7. **Number / roman-numeral canonicalization** — word numbers and unambiguous roman numerals become digits; tens+ones adjacency (`twenty one`→`21`).

**Call sites** — every search path routes through `text_normalize.normalize()`:

- `catalog.py` — index-time: `_normalize_for_search()` wraps `normalize()` when building `media_fts` and `media_trigram`.
- `catalog.py` — query-time: `search()` normalizes the query before building the FTS5 MATCH expression.
- `routes.py` — `unified_search`: normalizes user input before filtering local-media results.
- `routes.py` — `_normalize_song_key`: normalizes artist+title for grouping duplicate results.
- `static/app.js` — frontend normalizes the search box value before sending queries, matching the backend canonical space.

**Fuzzy fallback** — `catalog.search()` operates in three tiers:

1. FTS5 MATCH on `media_fts` (fast, prefix-aware).
2. LIKE fallback for queries that produce no FTS5 hits (catches punctuation mismatches).
3. rapidfuzz `WRatio` over `media_trigram` candidates (catches typos). Scores below `FUZZY_SCORE_CUTOFF` (default 80) are discarded.

### Versioning and reindex

`NORMALIZER_VERSION` in `text_normalize.py` is an integer stamp. It is written into `catalog_meta` whenever `rebuild_fts()` runs. `ExternalCatalog.index_is_stale()` returns `True` when the stored version does not match the current `NORMALIZER_VERSION` constant — indicating the FTS index was built with an older pipeline.

**After any normalizer change**, run:

```bash
python scripts/reindex_catalog.py
```

The service logs a `WARNING` at startup when it detects a stale index (see `app.py`, `start_app` / `create_app`).

### Test corpus and metrics

- `tests/fixtures/real_rotation_raw.json` — real rotation-history queries used as a regression corpus.
- `scripts/search_metrics.py` — computes recall@K over the corpus to give confidence that normalization changes do not regress search quality.
- `tests/unit/test_text_normalize.py` — unit tests for the normalizer pipeline.
- `tests/unit/test_catalog.py` — unit tests for `ExternalCatalog` search, fuzzy fallback, and index-staleness detection.

---

## Data Flow

```
Singer browser
    └── static/app.js (normalize query via text_normalize.js)
            │
            └── GET /search?q=...
                    │
                    ├── catalog.search()
                    │       ├── FTS5 MATCH on media_fts
                    │       ├── LIKE fallback
                    │       └── rapidfuzz over media_trigram
                    │
                    └── routes.unified_search()
                            └── normalize + filter local media index
```

---

## Configuration

Configuration is loaded from `config.json` (repo-local) by `config.py`. Key fields:

- `download_folder` — where downloaded karaoke files are stored
- `rotation_db_path` — SQLite DB for rotation + sing requests (default `~/kjdata/rotation.db`)
- `catalog_db_path` — SQLite DB for the song catalog (default `~/kjdata/catalog.db`)
- `gen_api_url` / `gen_api_token` — optional Nomad Gen integration
- `tls_cert` / `tls_key` — optional TLS for direct HTTPS (without reverse proxy)
- `behind_proxy` — set `true` when Caddy or another proxy terminates TLS

---

## Audio

See [AUDIO.md](AUDIO.md) for the ALSA → VLC/mpv signal chain and filler handoff protocol.

## HDMI & Display

See [HDMI.md](HDMI.md) for the full signal chain, EDID captures, and known issues.

## Deployment & Ops

See [CHANGELOG.md](CHANGELOG.md) for deploy steps associated with each release.
See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common operational issues.

## Browser Preview Playback

Audition any supported file **in the KJ's browser** (small video render + audio,
with seek) from every link/play surface — the rotation "Link song" search rows and
the Available Songs list. The preview renders entirely in the browser and **never
touches the device's primary player or HDMI/PA output**, so it is safe to use while
a singer is performing. The only shared resource is CPU, and only the exotic-video
case uses any (capped, niced, and cached so it is paid at most once per file).

Delivery is chosen per candidate by `PreviewService.resolve(descriptor)`:

| Candidate | Mode | Device CPU | Seek |
|-----------|------|-----------|------|
| H.264/AAC mp4, webm (local or GCS) | `native_video` — HTTP byte-range → `<video>` | none | native |
| CDG zip (local or GCS) | `cdg` — inner `.mp3` + `.cdg` → `cdg.js` canvas synced to `<audio>` | none | native |
| audio (mp3/wav/flac/…) | `native_audio` — byte-range → `<audio>` | none | native |
| mkv/avi/mov/odd-codec mp4 | `hls` — ffmpeg→HLS (≈480p, cached) → hls.js | capped, once | coarse live / full cached |
| bare `.cdg` + same-stem sibling audio | `cdg` — raw `.cdg` + sibling → `cdg.js` canvas | none | native |
| bare `.cdg` with no sibling audio | `unavailable` — "Graphics-only .cdg — no audio track" | — | — |
| YouTube candidate | `youtube` — IFrame embed | none | native |

Every resolve response also carries `format` + `ext` (via `utils.media_type_label`) for
the modal header. A standalone `.cdg` is graphics-only: `classify_kind` returns
`cdg_bare`, and it is only playable/linkable/previewable when a same-stem audio file
sits beside it (`playability.sibling_cdg_audio`). This is enforced at the playability
verdict (so `/rotation/link` + downloads reject an audioless `.cdg`) and with an explicit
`/play` guard; the Available Songs list dims such rows with a "no audio" tag.

Descriptors come in three shapes: `{source:'local', file_path}`,
`{source:'divebar', file_id, format}`, `{source:'youtube', youtube_url}`. Divebar
candidates are downloaded once into the cache and then handled exactly like a local
file. Local paths are validated with `MediaIndex.validate_path` (+ `external_media_mount`);
the browser only ever holds an opaque `token`, never a raw filesystem path.

Routes: `POST /preview/resolve`, `GET /preview/stream/<token>` (206 range),
`GET /preview/cdg/<token>/{audio,graphics}`, `GET /preview/hls/<token>/<name>`,
`POST /preview/close`. The transcode/blob/CDG cache lives under `preview_cache_dir`
(default `<download_folder>/.preview-cache`), size-capped by `preview_cache_max_bytes`.
A transcode is only served if its dir has a `.done` sentinel (written after ffmpeg
exits 0), so a truncated transcode is never replayed.
