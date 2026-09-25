# kjbox Functionality Map

What a KJ (host) can do in the KJ UI, what a singer can do in the Singer UI (`/sing`, sing.nomadkaraoke.com), and what the system does on its own during a night. The map is built to support:
(a) recording real karaoke nights as test fixtures, and
(b) a future end-to-end night-simulation test suite.

Every row points to code (`file:function:line`, relative to `kj-controller/` unless stated otherwise). Line numbers are a snapshot from 2026-09-24 (app v0.112.x). Several `app.py` citations include an uncommitted action-recorder hook that was in progress on this branch when the map was written.

**Contents**
1. Overview: actors, external systems, persistent state
2. Core night flows (critical path) + recording hooks
3. KJ UI capability catalogue (3A rotation/singers/media/requests/SMS; 3B header/playback/overlays/settings/system/preview/stats)
4. Singer UI capability catalogue
5. Background / automatic behaviours
6. Endpoint inventory (generated; all 176 routes)
7. Test coverage snapshot
8. Risk notes (+ 8.x defects found while mapping)

**Regenerating §6:** the endpoint table was produced by parsing the route decorators in `routes.py`/`sing.py` and grepping `tests/` for each path literal. Re-run that process after adding routes (see the §6 preamble), and diff the result against this table.

## 1. Overview

All paths are relative to `kj-controller/` unless noted. The app has two entry points that build the
same object graph: `create_app()` (tests/dev, `app.py:237`) and `start_app()` (device, `app.py:378`).
The main difference: `start_app()` also starts the background threads (PerfSampler, GenPoller) and
launches the renderer processes (`vlc.init_playback()`, `app.py:582`). `create_app(config)` passes
`enabled=False` to `PlaybackCoordinator`, so no mpv/VLC processes are spawned (`app.py:260-263`).

> Line numbers are against the working tree as of 2026-09-24. `app.py` had an uncommitted +14-line
> change when this was written (the `_install_action_recorder` hook), and the numbers below include it.

### 1.1 Actors

| Actor | Entry surface | Auth / identity | Notes |
|---|---|---|---|
| **KJ (host)** | `routes_bp` (`routes.py`, ~6.8k lines) served at `/` on the LAN (`nomadpc.local`, `kjbox.nomadkaraoke.com`). UI is `templates/` + `static/app.js`. | **None.** The app assumes the LAN/tunnel is trusted. Browser prefs live in KJ-browser `localStorage` (`kj-folder-state`, `kj-media-filter`, `kj-vnc-*`, `kj-overlays-hidden`, `kjbox.songStatsCollapsed`, `ytKaraokePrefix`, and others). | The "auto-SMS next singer" 20s arming timer runs **in the KJ browser** (`sing_store.py:20-23`). The server only exposes `/rotation/sms/auto-send` (`routes.py:4311`). |
| **Singer (public)** | `sing_bp` (`sing.py`), mounted at `/sing/`. On the public host (`sing_public_host`, default `sing.nomadkaraoke.com`) a WSGI rewriter prefixes `/sing` so the SPA is served at the root (`sing.py:334-367`). The host guard 404s every non-`sing.*` endpoint on the public host (`sing.py:309-331`). | **4-digit event token** (`rotation_meta.request_token`), passed as `?t=` or `session["sing_token"]` (`sing.py:166-242`). `require_token` also checks `request_token_enabled`. Device identity is `sing_device_id` in `localStorage` (`static-sing/sing.js:162`). | Other client-side state in `localStorage`: `sing_name`, `sing_phone`, `sing_photo_consent`, `sing_my_request_ids` (the ids plus per-request `edit_token`), `sing_lang`, `sing_rules_commercial_community_seen`. Rate limits are per IP and per device (see 1.4). |
| **Background jobs (in-process)** | Daemon threads: `SheetSync` (`rotation_sync.py:52`, every `rotation_sync_interval` s), `GenPoller` (`gen_poller.py:12`, every `gen_poll_interval` s), `PerfSampler` 1 Hz (`perf_sampler.py:345`), player monitor thread (`playback.py:283`), download worker (`routes.py:596`), Tier-2 playability worker (`routes.py:3953`), master-sync "Sync now" worker (`routes.py:1364`), PushDispatcher 0.5s debounce and 2-thread pool (`push_dispatcher.py:108-133`), volume-save 2s timer (`routes.py:510-526`). | n/a | Replay needs these stubbed or driven deterministically. |
| **Background jobs (out-of-process)** | systemd timers: `nomad-master-sync` runs `scripts.sync_masters` (`deploy/nomad-master-sync.service:15`). `nomad-catalog-sync` runs `scripts.sync_catalogs` daily at 12:15 UTC (`deploy/nomad-catalog-sync.timer:7`). `kj-autodeploy` runs `auto-deploy.sh`: polls `origin/main` every 60s, `git reset --hard`, restarts `kj-controller` only if `*.py`/requirements changed (`auto-deploy.sh:15-55`). It is toggled by `/system/autodeploy` (`routes.py:2870-2891`). | Timers poke the app over loopback: `POST /rescan` and `POST /catalog-mirror/reload` on `127.0.0.1:<app_bind_port>` (`scripts/sync_masters.py:176`, `scripts/sync_catalogs.py:134`). | `auto-deploy.sh` itself has **no mid-song guard**. A restart mid-night is recovered by the `/tmp/kj-*-state.json` files (1.3). |
| **Desktop display processes** | `desktop/overlay_engine.py` (the `overlay-display` service) and `rotation-display` (conky). | They read files only: `data/overlays.json` and `/tmp/rotation_cache.json`. They write `/tmp/kj-overlay-perf.json`. | Separate processes. They are **not** in Flask, and the controller reaches them only through those files. |

### 1.2 External systems

| System | Module(s) | How it is called | Config / env keys | Failure mode if unavailable |
|---|---|---|---|---|
| **mpv karaoke renderer** (default) | `mpv_manager.py` (`MpvKaraokePlayer`), owned by `playback.py:PlaybackCoordinator` | Subprocess plus JSON IPC on the unix socket `/tmp/mpv-karaoke.sock` (`mpv_manager.py:29`, `:145`). `launch()` at `:262`, `play()` at `:499`. | `render_mode` (`mpv`\|`vlc`; persisted by `switch_renderer`, `playback.py:235`), `enable_vlc` (gates process spawn off-Pi), `default_audio_device`, `karaoke_volume`, `audio_processing_enabled` (rubberband pitch + vocals guide, default False), `vocals_guide_dir`, `video_top_margin_px`, `screen_width/height` | The monitor detects process death and fires `on_engine_died`, which auto-restarts with a crash-history guard (`playback.py:73-74`). An unverified start (time-pos stuck for 10s) sets `audio_error` (`mpv_manager.py:38-39`). With `enabled=False` all calls are no-ops. |
| **VLC karaoke renderer** (alt) | `vlc.py` (`VlcKaraokePlayer`) | `cvlc` with the HTTP interface on `karaoke_vlc_port` (8080), polling `/requests/status.json` (`vlc.py:86`, `:96`). The window is positioned with `wmctrl`. | `karaoke_vlc_port`, `karaoke_vlc_password` | Same death/restart path as mpv. Switching renderer is rejected with 409 while karaoke is active (`playback.py:200`). |
| **VLC filler** | `filler.py` (`FillerVLC`) | `cvlc --extraintf http --http-port <filler_vlc_port>` (8081) (`filler.py:137-166`). Fades are done by stepping the volume over HTTP. | `filler_vlc_port`, `filler_vlc_password`, `filler_music_dir`, `default_filler_track`, `filler_volume` | Best-effort HTTP (timeout 2s). The filler is silent but karaoke is unaffected. `on_karaoke_end` calls `fade_in_filler` (`app.py:171-190`, `playback.py:487`). |
| **Audio stack (ALSA/PipeWire)** | `audio_monitor.py`, `chromium.py`, `routes.py` audio-device routes | `pactl`/`ffmpeg` via `sudo -u nomad XDG_RUNTIME_DIR=/run/user/1000` (`audio_monitor.py:15`). The browser audio monitor streams through ffmpeg. | `audio_devices`, `default_audio_device`, `browser_audio_device` (saved to config.json, `routes.py:2704`, `:2775`) | The monitor stops. Playback continues. |
| **VNC preview** | `app.py:428-450` | `websockify <websockify_port> <vnc_target>` subprocess (6080 → 5900) | `websockify_enabled`, `websockify_port`, `vnc_target`, `tls_cert/key` | The VNC pane is unavailable. Nothing else is affected. |
| **Chromium browser mode** | `chromium.py` (`ChromiumManager`) | Launches Chromium with profile dir `/tmp/kj-chromium` and CDP on port 9222 (`chromium.py:21-24`). Sets the PipeWire card profile with `pactl`. | `browser_mode_url` (saved to config.json, `routes.py:5590`, `:5628`), `browser_audio_device` | Browser mode fails. Karaoke is unaffected. The `_browser_mode` flag is in-memory (`routes.py:43`). |
| **Telnyx SMS, outbound** | `sms.py` (`send`, `sms.py:220`), `sms_store.py`, routes `/rotation/sms/*` (`routes.py:~4205-4330`) | `POST https://api.telnyx.com/v2/messages` with a Bearer key (`sms.py:208-240`). Every attempt is logged to `sms_log`. Numbers in `sms_opt_outs` are refused (`sms_store.py:197`). | env `TELNYX_API_KEY`, `TELNYX_FROM_NUMBER` (`app.py:293-299`). Template and region come from `rotation_meta.sms_template`/`sms_default_region`. | Missing creds: the SMS button hides and the modal shows "Not configured". HTTP/network error raises `TelnyxError`, which is logged with `status` = error and shown to the KJ. |
| **Telnyx SMS, inbound webhook** | `sing.py:1136` `/sing/telnyx/webhook` (it lives on the public host) | Ed25519 signature verified with `TELNYX_PUBLIC_KEY`. A DLR updates `sms_log.status` by `telnyx_message_id`. Inbound STOP/START updates `sms_opt_outs`. | env `TELNYX_PUBLIC_KEY` | Key unset: fails **closed** with 401. Recognised events always get 200. |
| **Web Push (VAPID)** | `push_dispatcher.py` (`PushDispatcher`), `sing.py:1093-1134` subscribe/unsubscribe | `pywebpush.webpush()` on a ThreadPool (`push_dispatcher.py:212`). Triggered by `RotationManager._after_mutation` → `notify_rotation_changed` (debounced 0.5s) and by `notify_request_decision`. The dedup state is `sing_push_subscriptions.last_sent_state`. | `vapid_public_key`, `vapid_private_key`, `vapid_subject`. These are auto-generated and persisted to config.json on first boot (`app.py:106-144`). | A 404/410 from the push service disables the subscription (`push_dispatcher.py:229`). Other errors are logged and swallowed. If key persistence fails, the keys are in-memory only and subscriptions break on restart. |
| **Google Sheets** (rotation backup) | `rotation_sync.py` (`SheetSync`, gspread + service account) | A daemon thread pushes all of SQLite `rotation_entries` to the Sheet every N seconds. `restore_from_sheet()` is the emergency pull (`rotation.py:280`). | `rotation_sheet_id`, `rotation_credentials_file`, `rotation_sync_interval` (`app.py:33-39`) | Optional: absent config means `rotation.sync = None`. SQLite is the primary store, so sync errors only log. |
| **karaoke-gen API** | `gen_client.py`, `gen_poller.py` | REST calls with `X-Admin-Token`: `POST /api/audio-search/search` (create job), `GET /api/jobs/{id}`, `GET /api/jobs/{id}/download-urls`, `POST /api/parse-karaoke-titles` (LLM title refine for downloads). The poller maps status to `rotation_entries.gen_status`. On COMPLETE it calls `media.download_from_url` and links the file (`gen_poller.py:23-76`). | `gen_api_url`, `gen_api_token`, `gen_poll_interval` | Unconfigured: `gen_client`/`gen_poller` are `None` (`app.py:363`, `:555`). The "Make" (gen) request path and LLM refine are then unavailable. Per-poll errors are logged. |
| **Divebar Cloud Function** (Divebar/NOMAD catalog + KN BigQuery copies) | `divebar.py` | `POST divebar_api_url` with JSON `action` ∈ {`search` (`:109`), `kn_community_search` (`:154`), `kn_search` (`:200`), `lookup` (`:239`), `stats` (`:273`), `refresh` (`:314`), `download_url` (`:342`, returns a signed GCS URL)}. 10s timeout. In-process 300s search cache (`divebar.py:28-31`). | `divebar_api_url`, `divebar_refresh_token` | Search returns `[]` and logs a warning. Divebar downloads fail. |
| **GCS: divebar file downloads** | `media.py:701` `download_from_url`, `:785` `download_cdg_pair`, `:878` `_http_download`; `preview.py:122` | HTTP GET of the signed URL returned by `divebar.download_url`. Files land in `download_folder`. | `download_folder` | The download item goes to `error`. The sing request falls back or fails (see `sing_resolve.py`). |
| **GCS: master sync** | `scripts/sync_masters.py` (timer plus the in-app "Sync Masters" button, `routes.py:1356`) | `gcloud storage rsync` of `master_sync_source` → `master_sync_dest` (default `{download_folder}/NOMAD-720p`). Second pass: vocals-padded guides (additive). flock at `/tmp/nomad-master-sync.lock`. Pokes `/rescan` on change. | `master_sync_enabled`, `master_sync_source/dest`, `master_sync_credentials_file`, `master_sync_delete_removed`, `master_sync_max_deletes`, `master_sync_delete_dry_run`, `master_sync_gcloud_bin`, `master_sync_rescan_url`, `vocals_sync_*` | If disabled or failing, masters lag (by about 24h before gen→GCS push). The app is otherwise unaffected. |
| **GCS: catalog mirror** | `scripts/sync_catalogs.py` → `catalog_mirror.build_mirror_db` (`catalog_mirror.py:351`); read side `CatalogMirror` | Downloads the Divebar export (`storage.googleapis.com/.../exports/divebar-catalog-latest.json.gz`) and KN community/full exports (`gs://nomadkaraoke-kn-data/...`). It builds a temp DB, then `os.replace`s it into `catalog_mirror.db`. flock at `/tmp/nomad-catalog-sync.lock`. | `catalog_mirror_enabled`, `catalog_mirror_db`, `catalog_mirror_max_age_days` (8), `catalog_mirror_reload_url`, `master_sync_credentials_file` | Mirror missing, or older than max age (`is_usable()`, `catalog_mirror.py:112`): search falls back to the live Divebar Cloud Function (`karaoke_nerds.py:58`, `routes.py:5081`). |
| **KaraokeNerds data** | `karaoke_nerds.py` (search), `version_priority.py` (brand ranking), `local_grouping.py` | **We never contact karaokenerds.com** (`karaoke_nerds.py:1-9`). The local `catalog_mirror.db` is tried first (sources `kn_community`, `kn_full`). Fallback is `divebar.kn_search` (BigQuery copies refreshed by the `kn-data-sync` job). Community rows carry a YouTube URL. | `kn_preferred_brands`, `kn_priority_community`, `kn_priority_commercial` (the last two are saved from the UI, `routes.py:2109-2112`) | Returns an empty/partial result set. |
| **YouTube / yt-dlp** | `media.py:575` `download_video` (base opts `media.py:~100`), `youtube_search.py` (ytsearch flat), `youtube_health.py` (yt-dlp/EJS/Deno/cookie health, PyPI latest-version check at `:169`), `preview.py` (YouTube preview mode = embed) | In-process `yt_dlp.YoutubeDL`. Cookies are uploaded via `/youtube/cookies` (`routes.py:2145-2176`) and written atomically with mode 0600 (`youtube_health.py:126-147`). | `youtube_cookies_file`, `download_folder` | Bot-check or age-gate: the download fails, `_last_error` is set, and the sing-request fallback worker tries the next candidate (`routes.py:~5940-5968`). Search returns `[]`. |
| **External 4TB catalog** | `catalog.py` (`ExternalCatalog`) | Local SQLite FTS built from a text file list (`build_from_file_list`, `catalog.py:159`) by `POST /catalog/build`. | `external_catalog_db`, `external_file_list`, `external_media_mount` | Not built: `is_available()` is False (`catalog.py:83`), so there are no disc-library results. A stale normalizer is logged at boot (`app.py:268-274`). |
| **Tips** | `sing.py:771-960` (`/sing/tip-info`, `/sing/tip-claim`) | **No Stripe API.** The app only renders payment links: Venmo, Cash App, PayPal, Zelle, a Stripe URL, or the default `https://nomadkaraoke.com/tip` (`sing.py:771`). A claim is self-reported and recorded as a `sing_requests` row with `source_type='tip'` and `source_meta={amount,method}` (`sing.py:937-956`). | `sing_tips_enabled`, `sing_tip_*` (overridden by `rotation_meta.sing_tip_settings`), `sing_tip_url`, `sing_tip_url_label` | n/a (no external call). |
| **System (systemd/X)** | `routes.py` `/system/*`, `sleep_mode.py` (`sleep-enter.sh`/`sleep-exit.sh`), `perf_sampler.py` (GPU clock helper `set-gpu-clock.sh`) | `sudo systemctl`, `xhost`, `wmctrl`, DBus | `auto_pin_gpu_during_playback` | Best-effort. Errors are returned to the UI. |
| **Caddy reverse proxy** | `deploy/Caddyfile`; `app.py:588-592` | Terminates TLS on :443. Flask listens on `app_bind_host:app_bind_port` (127.0.0.1:5001). Trusted proxies for `X-Forwarded-For` are `127.0.0.1`/`::1` (`sing.py:68`). | `behind_proxy`, `app_bind_host`, `app_bind_port`, `flask_port`, `tls_cert`, `tls_key` | With no proxy, Flask serves TLS itself and runs an HTTP→HTTPS redirector on :80 (`app.py:54-88`). |

### 1.3 Persistent state stores

Three SQLite files hold almost everything: `rotation.db` (shared by three store classes), `media_library.db`
(shared by two), and the two catalog DBs. All read-write stores use per-thread connections with WAL
and `busy_timeout` (`rotation_store.py:107-133`, `sing_store.py:67-90`).

| Store | Path / config key | Tables / keys (key columns) | Owner module (file:line) | Night-scoped? |
|---|---|---|---|---|
| **rotation.db: rotation** | `rotation_db_path`, default `~/kjdata/rotation.db` (`app.py:35`). Set explicitly in the device config.json. | **`rotation_entries`**: `id` AUTOINCREMENT (**never reset across nights**, `rotation_store.py:1273-1279`), `singer`, `singers_json` (duet names), `song_artist`, `status` (Waiting/Up Next/Now Singing/Done/…), `notes`, `position`, `priority_bias`, `paid`, `file_path`, `duration`, `download_source/status/id`, `url_fallback`, `gen_job_id`, `gen_status`, `playability_warning`, `created_at`, `updated_at`, `done_at` (`rotation_store.py:156-175`, migrations `:229-250`). **`rotation_archive`**: `night_date` + a subset of columns (singer, song_artist, status, notes, position, file_path, duration, created_at). It does **not** keep singers_json/paid/bias/done_at/download/gen fields (`:182-193`, `:1255-1261`). **`rotation_history`**: undo/redo stacks (`stack`, `seq`, `label`, `rev`, `entries_json` full snapshot), undo capped at `MAX_HISTORY=30` (`:81`, `:204-212`). **`rotation_meta`** keys: `rotation_rev` (monotonic revision, `:1380-1398`), `night_started_at`, `left_singers_json` (`:1182-1210`). | `rotation_store.py:61` `RotationStore`, wrapped by `rotation.py:20` `RotationManager` | `rotation_entries`, `rotation_history`, and `left_singers_json` are the **current night** (cleared on archive). `rotation_archive` is persistent. |
| **rotation.db: sing** (same file) | same `rotation_db_path` (`app.py:280`) | **`sing_requests`**: `id`, `created_at` (the night-scoping key), `token`, `singer_name`, `phone`, `song_artist`, `song_title`, `source_type` ∈ {local, divebar, kn, youtube, make, kj_pick, tip}, `source_ref`, `source_meta` JSON, `additional_singers` JSON, `notes`, `status` (pending/approved/rejected/cancelled…), `rejected_reason`, `reviewed_at`, `linked_entry_id` (→ rotation_entries.id), `edit_token`, `supersedes_request_id`, `user_agent`, `device_id` (`sing_store.py:109-125`, migrations `:199-257`). **`sing_push_subscriptions`**: `token`, `phone`, `singer_name`, `endpoint`/`p256dh`/`auth`, `last_sent_state`, `disabled_at`, UNIQUE(token, endpoint) (`:137-153`). **`singer_aliases`**: `device_id` PK, `canonical_name`, `origin` ('kj'\|'self') (`:171-176`). **`singer_photo_consent`**: `name_key` PK, `display_name`, `consent` yes/no, `source` singer/kj, `updated_at` (`:185-191`). **`rotation_meta`** keys (`sing_store.py:16-34`): `request_token`, `request_token_enabled`, `request_auto_approve`, `sing_accept_make_requests`, `sms_auto_next_singer`, `rotation_auto_reorder`, `kj_simple_mode`, `sms_template`, `sms_default_region`, `night_started_at`, `sing_tip_settings` JSON (`:441`), `sing_footer_settings` JSON (`:460`). | `sing_store.py:37` `SingStore` | `sing_requests` rows are **never deleted**; they are scoped at read time by `created_at >= night_started_at` (`sing.py:189-202`, `app.py:306-330`, `routes.py:3240`, `:3432`, `:4075`). Photo consent is read night-scoped by `updated_at`. Push subs are scoped by **token**, not night. Aliases and meta toggles persist across nights. |
| **rotation.db: SMS** (same file) | same `rotation_db_path` (`app.py:287`) | **`sms_log`**: `sent_at`, `rotation_entry_id`, `sing_request_id`, `phone_e164`, `body`, `status`, `telnyx_message_id`, `error`, `kj_user_agent` (`sms_store.py:71-82`). **`sms_opt_outs`**: `phone_e164` PK, `opted_out_at`, `keyword` (`:93-97`). | `sms_store.py` `SmsStore` | The log is append-only across nights and scoped via entry id / `sent_at`. Opt-outs persist. |
| **media_library.db: library** | `media_db_path`, default `<app>/media_library.db` (`config.py:50`) | **`media_library`**: `media_id` PK (source-prefixed stable id), `source`, `source_ref`, `artist`, `title`, `artist_norm`, `title_norm`, `confidence`, `parse_method`, `needs_review`, `raw_original_name`, `file_path`, `ext` (`media_library.py:53-69`) | `media_library.py` `MediaLibraryStore` (`app.py:250`) | Persistent |
| **media_library.db: stats** (same file) | same `media_db_path` (`app.py:251`: `StatsStore(cfg.get('media_db_path'))`) | **`play_events`**: `media_id`, `song_key`, `singer`, `singer_norm`, `artist_norm`, `played_at`, `night_date` (default `date('now','localtime')`), `entry_id` (partial UNIQUE for live plays, so there is one play per entry), `source` live/backfill, `artist`, `title` (`stats_store.py:51-67`, `:115-149`). **`preview_events`** (`:70-78`). **`version_notes`**: `media_id` PK, `note`, `label` (`:81-89`). | `stats_store.py` `StatsStore`; written by `routes.py:86-124` `_record_play_stat` on `/play` (`routes.py:1069`) | Persistent. Rows are tagged with `night_date`; the `/stats` night setlist is at `routes.py:6782`. |
| **external_media.db** | `external_catalog_db`, default `<app>/external_media.db` (`config.py:47`) | `media` (path, filename, folder, disc_id, artist, title, format), `media_fts` (FTS5), `media_trigram`, `catalog_meta` (`normalizer_version`) (`catalog.py:103-130`) | `catalog.py:62` `ExternalCatalog` | Persistent; rebuilt on demand. |
| **catalog_mirror.db** | `catalog_mirror_db`, default `<app>/catalog_mirror.db` (`catalog_mirror.py:59-61`) | `entries` (source ∈ kn_community/kn_full/divebar, artist, title, `payload` JSON, norm_text), `entries_fts`, `entries_trigram`, `mirror_meta` (built-at, `counts`, `source_hashes`) (`catalog_mirror.py:267-294`) | Read by `catalog_mirror.py:64` `CatalogMirror` (`PRAGMA query_only`). Written only by `scripts/sync_catalogs.py` (atomic `os.replace`, `:356-403`). | Persistent, replaced daily. |
| **config.json** | `CONFIG_FILE` = `<app>/config.json` (`config.py:9`); template `config.example.json` | All config keys. Runtime writers call `save_config_value` (atomic, `config.py:180-209`) for: `karaoke_volume`, `filler_volume` (`routes.py:516-517`), `render_mode` (`playback.py:235`), `default_audio_device` (`routes.py:2775`), `browser_audio_device` (`:2704`), `browser_mode_url` (`:5590`), `kn_priority_community/commercial` (`:2111-2112`), `vapid_*` (`app.py:135-137`) | `config.py` | Persistent |
| **media_index.json** | `media_index_path`, default `<app>/media_index.json` | `{realpath: entry}` index of all playable files under `media_folders`. Excludes `non_library_dirnames` and `_playability_quarantine/` (`media.py:28`, `:288`). | `media.py:143` `MediaIndex.save/_load_file` (`media.py:392-425`) | Persistent. Rescanned by `/rescan`. |
| **Media files** | `download_folder` (device: `/opt/nomad/YTDownloads`), `media_folders`, `{download_folder}/NOMAD-720p` (masters), `NOMAD-vocals-padded/` (guides), `_playability_quarantine/<file>` + `.reason.txt` (`media.py:47-90`) | yt-dlp sidecars (`.info.json`, thumbnails) are relocated alongside (`media.py:114`) | `media.py` | Persistent |
| **Preview cache** | `preview_cache_dir`, default a sibling `preview-cache/` of `download_folder` (`config.py:151-174`). Capped by `preview_cache_max_bytes` (8 GiB LRU). | `transcode/<content-sig>/…` + `.done` marker, `cdg/<key>/`, `gcsblob/<hash>/<name>` (`preview_cache.py:43-88`) | `preview_cache.py`, `preview.py:140` | Persistent (LRU) |
| **overlays.json** | `overlays_path` (only honoured in `create_app` with a config). Default `<repo>/data/overlays.json` (`overlay.py:11-14`). | `{karaoke_playing: bool, video_top_margin, overlays: [{id, type ∈ ticker/static_text/image/countdown/qr_code/rotation_list, enabled, show_over_video, config}]}`. A QR overlay with `follow_event_url` is rewritten when the token changes (`sing.py:271-294`). | `overlay.py:48` `OverlayManager` (atomic `_save`, `:67-80`). Read by `desktop/overlay_engine.py` (1s mtime poll). | Persistent. `karaoke_playing` is live playback state that is persisted. |
| **Wallpaper** | `~/kjdata/wallpaper.jpg` (original), `~/kjdata/rotation-bg.png` (backup), `<repo>/desktop/rotation-bg.png` (conky copy) | image files | `routes.py:1918-1990`; restored at boot by `app.py:42-52` | Persistent |
| **Flask secret** | `flask_secret_key_path`, default `~/kjdata/flask_secret` (`app.py:147-170`) | 32 random bytes, mode 0600. It keeps the singer `session["sing_token"]` cookie valid across restarts. | `app.py` | Persistent |
| **YouTube cookies** | `youtube_cookies_file`, default `~/kjdata/youtube_cookies.txt` | Netscape cookie jar | `youtube_health.py:126`; used by `media.py:103-105` | Persistent |
| **Perf recordings** | `perf_recordings_dir`, default `~/kjdata/perf_recordings` (`perf_sampler.py:362`) | `<YYYYmmdd-HHMMSS>-<label>.jsonl` session files (1 Hz samples) | `perf_recorder.py` | Persistent, on demand |
| **Playability results** | The result is stored as `rotation_entries.playability_warning` (`rotation_store.py:792`). A failed download goes to the quarantine dir. Batch runs (`playability_batch.py`) write JSONL/CSV/MD reports to a CLI-chosen path. | — | `playability.py`, `routes.py:3873` `_playability_gate` | Warning is night-scoped (on the entry) |
| **Renderer reconnect state** | `/tmp/kj-mpv-state.json` `{current_playing_path, audio_file}` (`mpv_manager.py:28`, `:238-251`); `/tmp/kj-vlc-state.json` `{current_playing_path}` (`vlc.py:23`); `/tmp/kj-filler-state.json` `{current_track}` (`filler.py:23`) | Lets a restarted controller re-attach to a still-running player mid-song | `mpv_manager.py`, `vlc.py`, `filler.py` | Lost on reboot (`/tmp`) |
| **Rotation display cache** | `/tmp/rotation_cache.json` (`rotation.py:15`) | `{queue:[{singer, song_artist, status, paid, priority_bias}], stats, updated}` | `rotation.py:425-453` `_write_display_cache` (on every mutation and every `get_rotation`) | Derived |
| **Sleep mode** | `/tmp/kj-sleep-mode` flag + `/tmp/kj-sleep-state.json` (`sleep_mode.py:10-11`) | flag presence = sleeping | `sleep_mode.py:16` `SleepManager` | Lost on reboot |
| **Overlay perf** | `/tmp/kj-overlay-perf.json` (`perf_sampler.py:32`) | written by the overlay engine, read by the sampler | `desktop/overlay_engine.py` | Ephemeral |
| **Sync locks** | `/tmp/nomad-master-sync.lock`, `/tmp/nomad-catalog-sync.lock` | flock | `scripts/sync_masters.py:31`, `scripts/sync_catalogs.py:38` | Ephemeral |
| **ZIP extraction / Chromium profile** | `tempfile.mkdtemp('kj-zip-extract-')` (`zip_playback.py:70`); `/tmp/kj-chromium` (`chromium.py:21`) | temp files | `zip_playback.py`, `chromium.py` | Ephemeral |
| **Action log (WIP, uncommitted)** | `action_log_dir`, default `~/kjdata/action-logs`; gated by `action_log_enabled` (on by default on the device) (`app.py:226-234`) | per-night JSONL of KJ/singer actions (`action_recorder.py`, `scripts/night_capture.py`, in progress) | `action_recorder.install_action_recorder` | Night-scoped files |
| **Log** | `log_file`, default `~/kj-controller.log` (`utils.py`) | text log | `utils.log_message` | Persistent (append) |

Undo/redo history is **persisted** in `rotation_history`, not held in memory. Auto-deploy has no
flag file: its on/off state is the systemd unit state of `kj-autodeploy`.

### 1.4 In-memory runtime state (cannot be captured from disk)

| State | Location | Night relevance |
|---|---|---|
| Active renderer object, `render_mode`, `health_events` (deque 30, acked by id), `crash_history` (deque 30) | `playback.py:49-80` | Crash/restart notices shown to the KJ |
| Player live state: `current_path`, `active`, `last_play_time`, `last_seek_time`, `audio_error`, `_last_file_path` (crash Retry), `_pitch_semitones`, `_vocals_file`/`_vocals_volume`, CDG audio-length cache | `mpv_manager.py:49-90`, `vlc.py:34-54` | Everything "now playing". Only the path (+ `audio_file`) survives a restart, via `/tmp/kj-*-state.json`. |
| Filler `current_track`, `volume`, fade cancel event | `filler.py:26-35` | |
| Download queue `{items:[{id, url, status queued/downloading/done/error, title, error, file_path, candidate_index, transient_attempts, added_at, completed_at}], worker_running}` guarded by `app._download_lock` (max 5 active) | `app.py:341`, `:534`, `routes.py:560-600`, `:5427-5471`, `:5940-5968`, `:6120` | Download progress for rotation entries. The durable mirror is `rotation_entries.download_status`. |
| Master-sync run state `{running, result}` | `routes.py:1344-1352` | |
| `_browser_mode` flag, `_volume_save_timer` | `routes.py:43`, `:510` | |
| Browser-preview tokens `{token → entry}`, TTL 3600s; the single active transcode | `preview.py:38`, `:147`; `preview_transcode.py:30` | `/sing/preview/*` and KJ preview URLs die on restart |
| Singer rate-limit deques: submit per-IP/per-device (`sing_rate_limit_*`, defaults device 8 / IP 60), `/validate`, preview (`sing_preview_rate_*`), tip (5 per 600s) | `sing.py:47-49`, `:110-111`, `:623`, `:775` | Replay must reset these, or they will 429 |
| Push debounce timer + executor | `push_dispatcher.py:130-132` | The dedup state itself is persisted (`last_sent_state`) |
| Divebar search cache (300s, 256 entries) | `divebar.py:28-31` | Makes search results time-dependent |
| yt-dlp latest-version cache (24h) | `youtube_health.py:13-14` | |
| PerfSampler ring buffer + GPU-pin edge state | `perf_sampler.py:345-370` | |
| AudioMonitor ffmpeg proc / client flag | `audio_monitor.py:26-33` | |
| MediaIndex `index` dict (loaded from `media_index.json`) and `_last_error` | `media.py:143-150` | |
| ZipPlayback `_temp_dir` | `zip_playback.py:19` | |
| SleepManager `_entering/_exiting` | `sleep_mode.py:24-26` | |
| KJ browser: auto-SMS-next 20s arming timer, UI prefs; Singer browser: `localStorage` identity (see 1.1) | client-side | Must be simulated by the test driver |

### 1.5 Night identity & lifecycle of state

- **There is no night ID or event table.** A night is identified by two things:
  - **`rotation_meta.night_started_at`**: a local datetime string. `RotationStore.archive()` writes it (`rotation_store.py:1282-1285`). `SingStore.ensure_night_started()` (`sing_store.py:311`) sets it on first boot only, via `app.py:286`/`:481`.
  - **`night_date`**: `date('now','localtime')`, stamped on `rotation_archive` rows at archive time and on `play_events` rows at play time (`stats_store.py:143`).

  These two can disagree for a night that crosses midnight. `play_events.night_date` is the calendar date of each play; the archive `night_date` is the date of the **New Rotation** press.
- **New Rotation** is `POST /rotation/archive` (`routes.py:3848`) → `RotationManager.archive_rotation()` (`rotation.py:267`). It does these steps in order:
  1. Copies `rotation_entries` into `rotation_archive` (partial column set).
  2. `DELETE FROM rotation_entries` (the AUTOINCREMENT is **kept**, so ids are globally monotonic).
  3. Clears `left_singers_json`.
  4. Sets `night_started_at = now`.
  5. Adds the starter entry "Andrew / First Song of the Night".
  6. `clear_history()` empties undo/redo.
  7. `_after_mutation` bumps `rotation_rev`, triggers push, and rewrites the display cache.
  8. Re-enables public requests (`request_token_enabled=1`).

  The **event token is NOT regenerated** (`routes.py:3852-3857`). It changes only through the explicit regenerate/set routes (`sing_store.py:332`).
- **Night-scoped by read-time filter, not deletion:** `sing_requests` (`created_at >= night_started_at`), `singer_photo_consent` (`updated_at >= night_started_at`), phone→entry resolution for SMS and push (`app.py:306-330`, `routes.py:3424-3442`, `:4068-4083`), `_belongs_to_current_night` for singer `/sing/status`/`my-requests` (`sing.py:189-202`), and rename/identity propagation (`sing_store.py:1140-1200`). All of them **fail closed** if the marker is NULL.
- **Night-scoped by deletion on archive:** `rotation_entries`, `rotation_history`, `rotation_meta.left_singers_json`.
- **Persistent across nights:** `rotation_archive`, all of `media_library.db` (library, `play_events`, `preview_events`, `version_notes`), `singer_aliases` (by design, `sing_store.py:156-168`), `sms_opt_outs`, `sms_log`, `sing_push_subscriptions` (valid while the token is unchanged), every `rotation_meta` toggle/setting (token, auto-approve, accept-make, auto-SMS, auto-reorder, simple mode, SMS template/region, tip and footer settings), config.json, overlays.json, the catalogs, the media files, and the preview cache.
- **Fixture implications:**
  - Capturing a night needs a snapshot of `rotation.db` (all three stores) plus `media_library.db` (for `play_events` with that `night_date`), `overlays.json`, and the relevant `config.json` keys.
  - Replay must set `night_started_at` and freeze `datetime('now','localtime')` or inject timestamps: SQL defaults and the `>=` scoping use SQLite's local clock, not Python's.
  - Replay must also reset the in-memory rate-limit, preview-token, and divebar-cache state listed in 1.4.
  - `dev_server.py --fetch-real` / `restore_archived_night` (`dev_server.py:41-70`) already copies the live `rotation.db` and rebuilds a mid-night snapshot from `rotation_archive`. That snapshot is lossy, because the archive drops `singers_json`, `paid`, `priority_bias`, and the download/gen fields.

## 2. Core night flows (critical path)

All paths are relative to `kj-controller/`. `R` = `routes.py`, `S` = `sing.py`, `RM` = `rotation.py` (RotationManager), `RS` = `rotation_store.py`, `SS` = `sing_store.py`, `PB` = `playback.py` (PlaybackCoordinator), `MPV` = `mpv_manager.py` (MpvKaraokePlayer), `JS` = `static/app.js` (KJ UI), `SJS` = `static-sing/sing.js` (singer SPA).

Cross-cutting mechanics that every step relies on:

- **One SQLite file, three stores.** `rotation.db` holds `rotation_entries`, `rotation_meta`, `rotation_archive`, `rotation_history` (RS:152), plus `sing_requests`, `sing_push_subscriptions`, `singer_aliases`, `singer_photo_consent` (SS:104) and `sms_log`/opt-outs (`sms_store.py`). `media_library.db` holds the media library plus `play_events`, `preview_events`, `version_notes` (`stats_store.py:46`).
- **Every rotation mutation** goes through `RM._before_mutation(label)` (RM:390; writes an undo snapshot into `rotation_history` via `RS.checkpoint` RS:1431) and then `RM._after_mutation()` (RM:402). `_after_mutation` bumps `rotation_meta.rotation_rev`, atomically rewrites the conky display cache (`RM._write_display_cache` RM:424, `ROTATION_CACHE_FILE`), and calls `PushDispatcher.notify_rotation_changed()`. That call is debounced by 0.5s (`push_dispatcher.py:135`) and then scans the Web Push ladder (`_dispatch_now` :171). Background mutations (`set_download_status`, `complete_download`, `set_gen_status`) skip the checkpoint, so they are not undoable.
- **The rotation list definition.** `RS.get_entries()` (RS:293) excludes only `done` and `left`. `Cancelled`, `On Hold (BRB)`, `Skipped` and `Being Made (!)` all stay in the "active" list that feeds the push ladder, `wait_estimate.compute_estimate` and auto-order. The singer-facing `/sing/now` is the exception: it also drops `cancelled` (S:2043).
- **Night identity has three independent definitions:**
  - `rotation_meta.night_started_at` (localtime, second resolution). Set by `RS.archive` (RS:1234) or on first boot by `SS.ensure_night_started` (SS:311). Used to night-scope phone lookups (SMS/push), `/sing/status`, `/sing/my-requests`, cancel/change/reorder, rename and photo consent.
  - `play_events.night_date` = `date('now','localtime')`, the calendar date of the play (`stats_store.py:115`).
  - WIP `action_recorder.night_date()` uses a noon cutoff.
  - These three do **not** agree for nights that cross midnight.
- **The KJ UI drives much of the "business logic" client-side.** Examples: the Play button = `/play` + a batch `/rotation/status`; the 20s auto-text timer; the 4s auto-delete of singer-cancelled rows; the 2s `/status` and `/rotation` polls. A simulation must replay these as HTTP calls; they do not happen in the backend on their own.

### Step 0 — Box boot / app start
- **Actor:** systemd (`kj-controller.service`), or a KJ pressing Restart App (`POST /system/restart-app` R:2824 → `sudo systemctl restart`).
- **Backend:** `app.start_app` (app.py:365). It is a separate code path from `create_app` (app.py:225), which is used only by tests and dev.
  - `load_config`, then `_bootstrap_vapid_keys` (app.py:105; generates and persists a P-256 key pair on first boot).
  - `PlaybackCoordinator(cfg)` (PB:52). Picks the default filler track (first audio file in `filler_music_dir`). Wires `vlc.on_karaoke_end = _make_on_karaoke_end(...)` (app.py:170). **`create_app` never wires `on_karaoke_end`**, so no test app has song-end behaviour.
  - `MediaIndex.load()`, `StatsStore`, `RotationManager` (starts the SheetSync thread if `rotation_sheet_id` is set), `rotation._write_display_cache()`.
  - `SingStore.ensure_token()` (SS:361; random 4-digit token via `secrets.randbelow` if absent) and `ensure_night_started()`.
  - `SmsStore`, `sms_config` from the env vars `TELNYX_API_KEY`, `TELNYX_FROM_NUMBER` and `TELNYX_PUBLIC_KEY`.
  - `PushDispatcher`, wired as `rotation.push_dispatcher` with the night-scoped `_phone_for_rotation_entry` (app.py:294 / :488).
  - `SleepManager`. The in-memory `download_queue` starts empty.
  - `GenClient` + `GenPoller.start()` (60s poll) if `gen_api_url`/`gen_api_token` are set.
  - `install_host_guard` (S:309) and `install_public_host_rewriter` (S:334).
  - `vlc.init_playback()` (PB:255): `filler.try_reconnect`, then `player.try_reconnect`. A non-idle mpv that survived the restart is kept (a song keeps playing); an idle one is respawned. Missing players are launched, filler fades in if karaoke is inactive, and the monitor thread starts (`MPV.monitor` :956).
- **State mutated:**
  - `rotation_meta`: token, night_started_at (first boot only), rotation_rev init.
  - `config.json` (VAPID keys, first boot).
  - `~/kjdata/flask_secret`.
  - `/tmp/kj-mpv-state.json` (read).
- **Side effects:** mpv and filler-VLC processes, websockify, and an HTTP→HTTPS redirect thread (or a Caddy proxy when `behind_proxy`).
- **Lost on restart (in-memory only):** `download_queue` (queued downloads are dropped; the UI shows those rows as `failed` via the JS:5462 fallback; there is no server resume), tier-2 queue, crash history and `player_alert`, rate-limit buckets, `routes._browser_mode`, and the push debounce timer.

### Step 1 — Night setup ("New Rotation" / open requests)
- **Actor:** the KJ.
- **UI action:** the "New Rotation" button (`archiveRotation` JS:8970, which asks for confirmation) and the Requests settings modal (token, enabled, auto-approve, auto-SMS, auto-reorder, simple mode, accept make, SMS template, tips, footer).
- **Endpoints:**
  - `POST /rotation/archive` (R:3849).
  - `GET/POST /rotation/requests/config` (R:6213 / R:6255).
  - `GET /rotation/requests/qr.svg` (R:6358).
- **Backend:**
  - `RM.archive_rotation` (RM:267), which runs `RS.archive(starter_singer="Andrew", starter_song="First Song of the Night")` (RS:1234):
    1. copies every row into `rotation_archive` (night_date = localtime date);
    2. `DELETE FROM rotation_entries`;
    3. clears `left_singers_json`;
    4. sets `night_started_at = now`;
    5. adds the starter entry;
    6. deliberately does **not** reset `sqlite_sequence`, so ids stay monotonic.
  - Then `RS.clear_history()` and `_after_mutation`. The route also calls `sing_store.set_enabled(True)`.
  - The token is **not** regenerated. Only `regenerate` or `token` in the config POST changes it. That goes through `_on_token_changed`: `sync_event_url_overlays` rewrites the QR/URL overlays and `cleanup_stale_push_subscriptions` runs.
- **State mutated:** `rotation_entries`, `rotation_archive`, `rotation_meta` (night_started_at, left_singers_json, rev), `rotation_history` (cleared), `sing_meta` flags (inside `rotation_meta`).
- **What does NOT reset:**
  - pending `sing_requests` (`/rotation/requests` lists **all** pending ever: `SS.list_requests` SS:803 has no night filter);
  - push subscriptions (token-scoped, and the token is reused);
  - `play_events`;
  - the media library;
  - SMS opt-outs;
  - singer aliases (their reads are night-scoped).

### Step 2 — Singer arrives: QR, landing page, code check
- **Actor:** a singer's phone, reaching the box via `sing.nomadkaraoke.com` (Cloudflare tunnel) or the LAN host.
- **Endpoints:**
  - `GET /sing/` (landing, S:433). On the public host, the WSGI rewriter maps `/` to `/sing/`.
  - `POST /sing/validate` (S:483; per-IP limit of 10 per 300s, `_validate_rate_limit_state`).
  - `GET /sing/event-info` (S:886), `GET /sing/tip-info` (S:873), `/sing/manifest.json`, `/sing/sw.js`.
- **Backend:**
  - `_extract_token` (S:166; order: `?t=` → JSON `t` → form → session cookie) and `_is_token_valid` (enabled AND equal to the current token).
  - The landing page has three states: closed (403), code_entry (200/400), or the SPA. The SPA receives `vapid_public_key`, `make_requests_enabled`, `simple_mode`, `sms_region` and `kj_name`.
  - On success `session["sing_token"]` is set.
  - The host guard (S:313) 404s every non-`sing.*` endpoint on the public host.
- **State mutated:** Flask session cookie only.
- **Side effects:** none.

### Step 3 — Singer identifies and searches
- **Endpoints:**
  - `GET /sing/singers` (S:1385; known names for the duet chips).
  - `GET /sing/my-stats` (S:1394).
  - `GET /sing/search?q=` (S:570, token-gated, query of 3+ characters).
  - `POST /sing/media-info` and `/sing/preview/*` (preview of a version).
- **Backend:** `R.unified_search(query, app, grouped=True, catalog_limit=60)` (R:5089). It fans out to:
  - the local media index and FTS catalog (`catalog.py`);
  - the on-box catalog mirror (`catalog_mirror.py`, the KN/divebar mirror);
  - `divebar.search` (a Cloud Function, cached);
  - `karaoke_nerds.search` (mirror-first).
  - Results are grouped with `_group_search_results` (R:350) using `version_priority` ranking. The response carries `make_requests_enabled` and `simple_mode`.
- **State mutated:** none, apart from the divebar in-process cache and the preview cache/transcodes.
- **External:** divebar Cloud Function; `yt-dlp` only for YouTube search via the KJ panel, not the singer flow.

### Step 4 — Singer submits a request
- **Endpoint:** `POST /sing/submit` (S:964).
- **Validation order:**
  1. rate limit: `_singer_rate_limited`, per device/IP, using `sing_rate_limit_per_ip`/`_window_s`;
  2. `photo_consent` must be yes/no;
  3. `additional_singers` shape;
  4. a `device_id` alias overrides the typed name (`SS.get_alias`);
  5. phone regex;
  6. `source_type` must be in `{local, divebar, kn, youtube, make, kj_pick}`;
  7. simple mode allows only `{local, divebar, kn}`;
  8. `source_ref` is required for local/divebar/kn/youtube;
  9. `make` requires `accept_make_requests` plus artist and title;
  10. `kj_pick` requires a valid `versions` snapshot (max 50).
- **Duet partners** are canonicalised against tonight's known names (`_canonicalize_partners` S:1370 / `match_known_singer` S:1312).
- **Backend:** `SS.create_request` (SS:734) stores a pending row with token, a `secrets.token_urlsafe(16)` edit_token, the user agent and the device_id. Then `SS.set_photo_consent` (best-effort).
- **Auto-approve branch** (when `SS.is_auto_approve()`):
  - `kj_pick` requests are first bound by `R.resolve_kj_pick_best` (R:5771).
  - `R.approve_sing_request(app, req)` (R:5994) runs, then `SS.mark_approved(id, linked_entry_id)` and `R.maybe_auto_reorder` (R:3787).
  - Any exception leaves the request pending (logged).
  - This branch sends **no approval push**. Only the admin route sends one.
- **Response:** `{request: public view + edit_token (returned only here), auto_approved}`.
- **State mutated:** `sing_requests`, `singer_photo_consent`. With auto-approve, also `rotation_entries` and the download queue (see Step 6).

### Step 5 — Request lands in the KJ right rail
- **KJ UI:** polls `GET /rotation/requests` (R:6192; returns all statuses, `edit_token` stripped, plus `counts`) and `GET /rotation/requests/config` (`pending_count`).
- **KJ actions:**
  - edit a request (`POST /rotation/requests/<id>/edit` R:6500);
  - reject (`POST .../reject` R:6520): `SS.mark_rejected(reason)` then `notify_request_decision("rejected")` (the push is skipped for `tip`);
  - approve (Step 6).

### Step 6 — KJ approves (or auto-approve); the media gets linked
- **Endpoint:** `POST /rotation/requests/<id>/approve` (R:6382). Returns 409 unless the request is `pending`. The status check and the approval are not atomic, so two tabs or a double-click can approve twice.
- **Meta-request types:**
  - `tip`: `apply_confirmed_tip` (R:5803) hearts the singer's active entries via `RM.set_paid`. If the amount is at least `sing_tip_priority_threshold`, it also calls `RM.set_singer_priority_bias(name, 1)` and `run_auto_order`.
  - `reorder`: `apply_reorder_request` (R:5855) moves the singer's own entries within the slots they already hold.
- **kj_pick:** the body must include `version_index`. `_pick_version_from_kj_pick` (R:5671) and `SS.update_request_source` rewrite the request to a concrete source first.
- **`approve_sing_request`** (R:5994), by source:
  - **local:** `RM.add_entry(singer, "Artist - Title", file_path=source_ref, singers=[...])`. No playability gate.
  - **divebar / youtube / kn:**
    - With `skip_download`, an unlinked entry is created.
    - Otherwise, dedup first: `_existing_media_for(_prospective_media_id(...))` links an existing file and sets status `complete`.
    - Divebar: `_resolve_divebar_spec` (R:753) runs **before** the entry is created (it pairs a loose CDG with its mp3 and resolves a GCS/Drive URL via `divebar.get_download_url`).
    - Then `add_entry`, `set_download_status(queued, download_id=uuid4)`, the item is appended to `app.download_queue`, and `_download_worker` (R:816) is spawned if idle.
    - A YouTube item carries a ranked `candidates` fallback list (`_build_sing_fallback_candidates` R:5885).
  - **make:** `add_entry`, then `gen_client.create_job(artist, title)`.
    - On failure the entry gets status `Being Made (!)` and stays unlinked (approval still succeeds).
    - On success, `set_gen_status(job_id, mapped)`.
- **After approval:**
  - `SS.mark_approved(req_id, linked_entry_id)`.
  - **Supersede:** if the request supersedes an earlier one (a singer "change"), the old entry is deleted if still active (not done/left/now singing), the new entry moves into its position, and the original request is marked cancelled.
  - `notify_request_decision("approved")`: an immediate push to every subscription whose phone matches.
  - `maybe_auto_reorder`.
- **Download worker** (R:816; one thread, sequential):
  - Downloads via `media.download_video` (yt-dlp), `download_from_url` (GCS/Drive divebar) or `download_cdg_pair`.
  - On failure with candidates: `_attempt_sing_fallback` (R:5926) retries a transient error up to `sing_resolve.MAX_TRANSIENT_RETRIES`, or moves to the next candidate (and `update_request_source`).
  - On success: `RM.complete_download(download_id, path, title)` (RM:195) links the file and duration, fills in the title if blank, and sets `download_status=complete`.
  - On terminal failure: `_sync_rotation_download` marks the entry failed.
  - Then `_notify_sing_outcome` (R:5981), which is intended to push `resolved_alt` or `unavailable`.
- **KJ manual paths** (from the rotation row or the add form):
  - `POST /rotation/link` (R:3960): `_playability_gate` (tier-1 integrity + sampled decode; 422 if not OK), then `RM.link_file` (undoable), then `_enqueue_tier2` (background render check, which sets `playability_warning`).
  - `POST /rotation/unlink`.
  - `POST /rotation/download-and-link` (R:5352; divebar/youtube; queue capped at 5 → 409; dedup).
  - `POST /rotation/make` (R:5483; gen job).
  - `POST /divebar/download` (R:2279), `/download` (R:552), `/upload` (R:602).
- **Gen completion:** the `GenPoller` thread (`gen_poller.py:23`, every 60s) calls `get_job_status` then `set_gen_status`. On `complete` it calls `get_download_url`, `media.download_from_url` and `RM.complete_gen_job` (links the file).
- **State mutated:** `sing_requests` (status, linked_entry_id, reviewed_at, source rewrite), `rotation_entries` (+ download/gen fields), `rotation_history`, `rotation_meta.rev`, `media_library` rows, and files under `download_folder`.

### Step 7 — Rotation ordering
- **Manual move:** `POST /rotation/move {id,new_position}` (R:3730) → `RM.move_entry` → `RS.move_entry` (RS:425; shifts the rows in between).
- **Status dropdown:** `POST /rotation/status` (R:3589).
- **Edit / delete / add:**
  - `/rotation/edit` (R:3639).
  - `/rotation/delete` (R:3676).
  - `/rotation/add` (R:3702): `add_entry` + optional `url_fallback`, then `maybe_auto_reorder`.
- **Auto Order:**
  - Triggered on demand by `POST /rotation/auto-order` (R:3834), or automatically after a new entry when `auto_reorder` is on (`maybe_auto_reorder` R:3787, called from add, admin approve and sing auto-approve).
  - `run_auto_order` (R:3757): decorates the entries (`_decorate_rotation_entries` R:3542: time estimates, songs_sung, wait pills, sms, media_meta, photo consent), then `auto_order.build_entry_views` (:169) and `compute_auto_order` (:232; rows 1–3 locked; rows 4–5 frozen except for duplicates; `Being Made` pinned to the bottom; greedy fairness/spacing/wait weave), then `RM.reorder_by_ids` (no-op with no checkpoint if unchanged).
  - **Depends on wall-clock time** (`wait_minutes` and `last_sang` come from SQLite `datetime('now','localtime')`).
- **Priority bias:**
  - Entry level: `POST /rotation/set-priority` (R:4458) → `RM.set_priority_bias` (RM:240).
  - Singer level: `POST /rotation/singer/priority` (R:4736) → `set_singer_priority_bias`.
  - Both use a single checkpoint and then `_auto_order_response(checkpoint=False)`.
- **Paid:** `POST /rotation/set-paid` (R:4415) → `RM.set_paid` (a heart; does not reorder by itself).
- **Singer self-reorder:** `POST /sing/requests/reorder` (S:1708) creates a pending `reorder` request that the KJ approves (Step 6). It is applied immediately if auto-approve is on.

### Step 8 — KJ plays the next singer
- **Actor:** the KJ presses ▶ on a rotation row: `playAndAdvanceRotation(entry, idx, entries)` (JS:7515). This sends three independent client requests:
  1. `POST /play {file_path, entry_id}` (R:977):
     - `_check_sleep_mode` (409 while sleeping).
     - `media.validate_path`, or a path under the external mount (NFC/NFD).
     - 503 if playback is disabled.
     - ZIP files: `zip_playback.extract_and_get_mp3`; under mpv, plays the `.cdg` with the mp3 as `audio_file`. A bare `.cdg` needs a sibling audio file.
     - Kills Chromium if browser mode is on.
     - Resolves the vocals guide (`_resolve_vocals_guide` R:949; NOMAD masters on mpv with audio processing on).
     - Starts a thread for `PB.play_video` (PB:408): fade out and stop the filler (VLC HTTP), then `MPV.play` (MPV:499).
       - Under `_play_lock`: reset pitch and vocals, `overlay.set_karaoke_playing(True)`, clear `lavfi-complex` if a guide was active.
       - IPC `loadfile replace`, `audio-add` for CDG (abort if that fails), or attach the guide + `_apply_vocals_mix`.
       - Set the volume, `active=True`, `_save_state`.
       - A `_verify_playback_progress` thread sets `audio_error` if `time-pos` does not advance within the timeout.
     - Synchronously, `_record_play_stat` (R:86): resolves the media_id (the library row, the filename's media id, or a library hash computed off-thread), then `stats.record_play(media_id, entry_id, singer, artist, title, song_key)`. There is one live row per `entry_id` (partial UNIQUE index), so a Retry does not double-count. The stat is recorded before playback is confirmed.
  2. `advanceRotationStatus` → `POST /rotation/status {updates:[{id,'Now Singing'},{next,'Up Next'}]}`. `RM.update_statuses` takes one checkpoint ("Advance rotation"). `RS.update_status` (RS:376) enforces exclusivity: **any other `Now Singing` entry reverts to `Waiting`, not `Done`**.
  3. If `idx===0` and auto-SMS is enabled, `armAutoTextNextSinger` sets a 20s JS timer, then `POST /rotation/sms/auto-send {entry_id: slot2, playing_entry_id: slot1}` (R:4312).
     - The server re-checks: the flag is on; slot 2 is the target; slot 1 is the playing entry; `vlc.current_playing_path == slot1.file_path`; there is no earlier `sms_log` row; there is a night-scoped phone (`_resolve_sms_target` R:4044).
     - Then `_perform_sms_send` (R:4200): opt-out check, `sms.send` (Telnyx `requests.post`), `sms_store.record_send`.
- **Push:** the status change triggers `_after_mutation`, then a 0.5s debounce, then `_dispatch_now`: for each active subscription on the current token, `next_entry_for_phone` → `decide_ladder_step` (`now_singing` / `up_next` for positions ≤2 / `up_in_2` for position 3), dedupe on `last_sent_state`, then `webpush` on the executor. A 404/410 disables the subscription.
- **Manual SMS:** `POST /rotation/sms/preview` then `/rotation/sms/send`.
- **Status as seen by clients:**
  - `GET /status` (R:1465, polled every 2s): `state` / `time` / `length` from `MPV.get_status` (IPC `pause`, `time-pos`, `duration`), `current_playing_path`, `rotation_downloads`, `player_alert`, `simple_mode`.
  - The singer SPA polls `/sing/now`, `/sing/rotation`, `/sing/my-requests` and `/sing/status/<id>`.

### Step 9 — Mid-song controls
Each control below maps directly to a single route:
- **Pause/resume:** `POST /control {action:'pause_resume'}` (R:1088) → `MPV.pause_resume`. Sets `karaoke_active` and the overlay's playing flag. The filler is deliberately not started while paused.
- **Restart:** `{action:'restart'}` → `seek_karaoke(0)`.
- **Seek:** `POST /seek {time}` (R:1074).
- **Stop:** `{action:'stop'}` → `stop_karaoke` (`MPV.stop` MPV:649: IPC stop, clears `active`/`current_path`), then `ensure_karaoke_released` (waits for `idle-active`), overlay off, `fade_in_filler`.
- **Fade out:** `{action:'fadeout', duration_s}` (clamped to 0.5–60s) → `PB.fadeout` (PB:467), a thread running player fadeout, sleep, `ensure_released`, overlay off, filler fade-in.
- **Pitch:** `POST /pitch {semitones}` (R:1166; −6..+6) → the rubberband `af-command`. No-op when `audio_processing_enabled` is false. Resets for each song.
- **Volume:** `POST /volume {target: karaoke|filler|vocals, level}` (R:1133). Volumes are persisted with a debounce; the vocals guide level is not persisted.
- **Renderer swap / fix audio:** `/renderer` POST (R:1189), `POST /fix_audio` (R:1547) → `restart_instances` (kills the current song).

### Step 10 — Song end
- **Automatic (natural EOF):** the mpv monitor (`MPV._monitor_via_events` :974) sees `end-file reason=eof`, then `_handle_karaoke_ended` (MPV:1034): `active=False`, clears the path and pitch, `_save_state`, `ensure_released`, then `on_karaoke_end` (app.py:170), which turns the overlay off and fades in the filler. When polling instead (`_monitor_via_polling`), the trigger is `idle-active` true, with 5s play/seek grace periods.
- **What does NOT happen automatically:** the rotation entry is not marked Done, the next singer is not started, and no status changes. The KJ must:
  - press **Done** on the row (`updateRotationStatus(id,'Done')` JS:6087 → `POST /rotation/status` → `RS.update_status` stamps `done_at`, which drives songs_sung, last-sang and auto-order fairness); **or**
  - press ▶ on the next row. That silently reverts the previous `Now Singing` entry to `Waiting` (RS:392), so a forgotten Done puts the singer back into the queue as not-sung.
- `/status` then reports `state: stopped` and `current_playing: null`, and the KJ UI shows idle.

### Step 11 — Singer cancels, changes or reorders (self-service, edit_token-gated)
- **Cancel:** `POST /sing/requests/<id>/cancel` (S:1568). Checks: rate limit, token valid, request token matches and belongs to the current night, edit_token (constant-time compare), not already cancelled/rejected.
  - If approved and linked: `RM.update_status(entry,'Cancelled')`. Returns 409 `already_sung` if the entry is done/left.
  - Then `SS.mark_cancelled`.
  - The **KJ browser** then auto-deletes Cancelled rows after 4s (`maybeAutoRemoveCancelled` JS:5565 → `/rotation/delete`, an undoable "Remove X"). With no KJ tab open, Cancelled rows remain and still count in the push ladder, wait estimates and auto-order.
- **Change:** `POST /sing/requests/<id>/change` (S:1625).
  - A pending request is updated in place (`update_request` + `update_request_source`).
  - An approved request creates a **new pending** request with `supersedes_request_id`. That request is **never auto-approved**, even when auto-approve is on; the KJ must approve the swap (Step 6 supersede).
- **Reorder:** covered in Step 7.
- **Other singer self-service:**
  - `POST /sing/update-phone` (S:1783): writes the phone onto every request the device owns.
  - `/sing/rename` (S:1884): alias + `persist_rename`.
  - `/sing/photo-consent` (S:1833), `/sing/forget` (S:2021), `/sing/tip-claim` (S:909; creates a `tip` meta-request).
  - `/sing/push/subscribe` (S:1095), `/sing/push/unsubscribe` (S:1124).

### Step 12 — Duets
- The singer adds partners on the confirm screen: `additional_singers` → `_canonicalize_partners` → stored as JSON.
- On approval, `singers_list = [primary] + partners` → `RS.add_entry(singers=...)` → `singers_json`. The display name is `"A & B"`.
- The KJ can create duets through `/rotation/add` or `/rotation/link` with `singers: [...]`, or `/rotation/edit singers`.
- Name identity is by exact string (case-folded) across `singers_json` members for:
  - `songs_sung` (the minimum across members);
  - wait pills (the maximum);
  - BRB/remove/bias (`RS.set_singer_status` RS:1149 matches `singers_json`);
  - auto-order spacing (per member).
- Phone/push resolve only via the **primary** request's phone.

### Step 13 — KJ skip, remove, BRB
- **Skip:** the status dropdown sets `Skipped` (`/rotation/status`). The entry stays in the active list.
- **BRB:** `POST /rotation/singer/brb {name, brb}` (R:4718) → `RM.set_singer_status(name, "On Hold (BRB)"|"Waiting")`. This affects **every** non-Done entry for that name, including a `Now Singing` one.
- **Remove singer:** `POST /rotation/singer/remove` (R:4763) → status `Left` + `mark_singer_left` (a `rotation_meta` set that is **not** part of the undo snapshot).
- **Restore:** `/rotation/singer/restore` (R:4780) sets every non-done entry to `Waiting` (this also revives Cancelled and BRB entries).
- **Delete one entry:** `/rotation/delete`.
- **Rename / merge / split / photo consent:** `/rotation/singer/rename` (R:4651), `/merge` (R:4690), `/split` (R:4797), `/photo-consent` (R:4669).

### Step 14 — Crashes and recovery
- **mpv crash:**
  - The monitor's `_notify_if_dead` (MPV:903) sees Popen `poll()` return an exit code, or two consecutive dead-socket checks for a reconnected process. It sets `active=False` and calls `on_engine_died({engine, returncode, song, file_path})`.
  - `PB._handle_engine_died` (PB:99) → `_record_crash` (PB:111): records a health event and escalates if there are 3 or more crashes within 60s (`CRASH_GUARD_*`). Otherwise `_safe_restart` → `restart_instances` (PB:287) on a thread, which relaunches mpv and the filler and fades the filler in.
  - `/status.player_alert` drives the KJ banner. The KJ can Retry (the frontend re-POSTs `/play`) and dismiss with `POST /player-crash/ack {id}` (R:1558).
- **Audio stuck:** `POST /fix_audio` → `restart_instances`. `_verify_playback_progress` sets `audio_error`, which shows in `/status.audio_error`.
- **App restart:** `/system/restart-app`, auto-deploy on a `.py` diff, or a crash. mpv keeps playing (reconnect). Rotation and undo history persist in SQLite. The download queue, tier-2 queue and crash history are lost (see Step 0).
- **Undo/redo:** `POST /rotation/undo|redo`. Without `confirm` it returns a preview diff; with `{confirm:true, expected_rev}` it applies, and a stale rev is rejected (R:4548).
  - `RS._apply_from` (RS:1480) → `restore_entries(snapshot, preserve_tracking=True)` (RS:1301). This keeps the live download and file-link fields, but it runs **`DELETE FROM sqlite_sequence`**.
  - Consequence: after undoing an add or approve, the **next new entry reuses the undone entry's id** (verified: add A(1), checkpoint, add B(2), undo, add C → id 2).
  - A prior sing request with `linked_entry_id` equal to that id, created tonight, will then phantom-match for SMS/push/`my-requests`. The night-scope guard does not help within a night.
- **Sheet restore:** `POST /rotation/restore` (R:4509) → `RM.restore_from_sheet` (undoable).

### Step 15 — Close the night
- There is no explicit "close". The next night's `POST /rotation/archive` (Step 1) is the close.
- Optionally, `POST /rotation/requests/config {enabled:false}` stops requests, and `/system/sleep-mode` (R:2911) runs `SleepManager.enter_sleep` (blocks `/play` with 409).
- **What persists:**
  - `rotation_archive`: singer, song_artist, status, notes, position, file_path, duration, created_at. It does **not** keep done_at, singers_json, paid, bias or the ids.
  - `play_events` (the stats night setlist is `GET /stats/night-setlist?night_date=` R:6778 → `StatsStore.night_setlist`, keyed by the **calendar** date).
  - `sing_requests` (all nights), `sms_log`, push subscriptions, aliases, photo consents, the media library, and the rotation id sequence (monotonic).
- **Sheet sync:** if configured, the background `rotation_sync.SheetSync` pushes every `sync_interval` (30s).

### 2.x Recording hooks (what a night-recorder must capture to replay each step deterministically)

WIP in this worktree (uncommitted):
- `action_recorder.py` + `install_action_recorder`: a per-request JSONL with body, response and client headers. It skips `GET /status`, `/rotation`, `/rotation/requests`, `/system/stats`, `/perf/stream` and static files.
- `scripts/night_capture.py`: a sidecar with row-level DB diffs via `PRAGMA data_version`, `/status` snapshots, the journal, and periodic SQLite backups.

The table maps each step to what these cover and what is still missing.

| Step | Must capture | Nondeterminism / stubs needed |
|---|---|---|
| 0 Boot | config.json subset (render_mode, audio_processing_enabled, sing_*/estimate knobs, gen/KN/divebar config, behind_proxy); starting DB snapshot (rotation.db + media_library.db); media index / library listing; `/tmp/kj-mpv-state.json`; env flags (Telnyx configured y/n) | VAPID keys, flask secret, token (`secrets.randbelow`) → seed or inject; mpv/filler process reconnect → fake player |
| 1 Setup | archive call + response; resulting `night_started_at`; config POST bodies | `datetime('now','localtime')` in SQLite (night_started_at, starter entry created_at) → **SQLite clock must be injectable** (today it is not; only SQL `now`) |
| 2 Landing / validate | request host (public vs LAN), `?t=`, IP (rate limit), response code | rate-limit uses `time.time()`; session cookie |
| 3 Search | query → full response (songs/versions) so replay doesn't need the catalog/mirror/divebar CF | divebar CF HTTP, KN mirror, catalog DB state → stub `unified_search` or record its output per query |
| 4 Submit | full body (incl. `source_meta.versions` snapshot, device_id, photo_consent, additional_singers), UA, IP, response (request id, **edit_token**, auto_approved) | `edit_token` (`secrets.token_urlsafe`), request id (autoincrement), `created_at` (SQLite now) → replay must re-map ids/tokens from the response |
| 5 Rail | `/rotation/requests` snapshots (skipped by recorder → sidecar DB diffs cover `sing_requests`) | — |
| 6 Approve / link / download / make | approve body (`version_index`, `skip_download`); response `entry_id`; download_queue item lifecycle (**in-memory only** — visible via `/status.download_queue` + `rotation_downloads`); final `file_path`; `_last_error` reasons for fallback; gen `job_id` + poll status sequence | `uuid4` download_id; yt-dlp (`media.download_video`), divebar `get_download_url`/`download_from_url`/`download_cdg_pair`, gen `create_job`/`get_job_status`/`get_download_url` (60s poll); ffprobe/playability gate (`_playability_gate`, tier-2 render); dedup lookups against media_library → stub with recorded results + fake files |
| 7 Ordering | every move/auto-order/bias/paid body + response `entries` order; auto-order `changed` | auto-order depends on `wait_minutes`/`last_sang` (SQLite now) + durations → capture decorated entries or freeze clock; `maybe_auto_reorder` is implicit after add/approve (record `auto_reorder` flag) |
| 8 Play | `/play` body (file_path, entry_id); batch `/rotation/status` body; `/status` timeline (state/time/length/current_playing_path) ; `play_events` row; auto-send request + skip reason; `sms_log`; push sends (endpoint, step) | mpv IPC (loadfile/audio-add/time-pos), filler VLC HTTP, `_verify_playback_progress` timeout, Telnyx `requests.post` (message id), `pywebpush.webpush`, 0.5s push debounce Timer, 20s client timer, stats `datetime('now')` (UTC) + `date('now','localtime')` |
| 9 Mid-song | `/control`, `/seek`, `/pitch`, `/volume` bodies + wall-clock; `/status` time-pos at action | mpv IPC; fadeout thread sleeps `duration_s+0.5` |
| 10 Song end | **not an HTTP event**: must be inferred from `/status` (playing→stopped with no `/control`), journal "Karaoke video finished playing.", and the following Done/Play request; song length | EOF event from mpv monitor → fake player must emit `end-file eof` / call `_handle_karaoke_ended` at a scripted time |
| 11 Cancel/change/reorder | bodies incl. edit_token (raw — recorder keeps it); KJ-tab auto-delete `/rotation/delete` 4s later (client-driven) | edit_token must be re-mapped on replay; 4s JS timer |
| 12 Duet | `additional_singers` raw + canonicalized (response) ; `singers_json` | `_canonicalize_partners` depends on tonight's known names (state) |
| 13 Skip/BRB/remove | bodies + response entries; `left_singers_json` meta | — |
| 14 Crash/recovery | journal "mpv engine died (exit N)", `/status.player_alert`, `/player-crash/ack`, `/fix_audio`, restart timestamps (process restarts → in-memory loss), undo preview/confirm bodies with `expected_rev`, `rotation_rev` | crash injection in fake player; `time.time()` crash window; `restart_instances` sleeps (1s+3s); undo relies on `rotation_history` rows (sidecar diffs) |
| 15 Close | archive response; `rotation_archive` rows; `play_events` for the night; final DB snapshot | calendar vs noon-cutoff night_date mismatch |

Recording gaps to close:
1. The recorder skips `GET /rotation` and `/rotation/requests`, and the sidecar records only `/status`. The KJ UI's view of `entries` (decorated wait pills, sms blocks) is therefore not captured. It can be rebuilt from DB diffs plus a clock.
2. `/sing/now`, `/sing/rotation` and `/sing/my-requests` are not in the skip list. With many phones polling every ~15s this will dominate the JSONL (a volume issue, but also useful as load data).
3. Outbound effects (Telnyx sends, webpush sends, gen/yt-dlp/divebar calls, mpv IPC) are **not** HTTP requests into Flask. They are visible only via DB diffs (`sms_log`, `sing_push_subscriptions.last_sent_state`, rotation download/gen fields) and journal lines. For a faithful replay, add explicit outbound-call logging, or wrap `sms.send`, `PushDispatcher._send`, `GenClient`, `media.download_*` and `MPV._send_ipc`.
4. For deterministic replay, the SQLite `datetime('now','localtime')` defaults need an injectable clock (e.g. pass a timestamp from Python), as do `time.time()` in the rate limiters, crash guard, auto-order and time estimates, and `uuid4`/`secrets`.

---


## 3. KJ UI capability catalogue

The KJ UI is `templates/index.html` + `static/app.js` (with `static/preview.js` and `static/cdg.js`), served at `/` on the LAN/admin host. The KJ routes have no auth; they are 404 on the public singer host (sing.py:309). Each row shows: user action → app.js function → HTTP call → backend handler → state mutated → side effects.

### 3A. Rotation, singers, media linking, requests, SMS

All paths are relative to `kj-controller/`. Line numbers are from the worktree at the time of writing.

**Legend (used in the tables below)**
- **R** = `routes.py`, **RM** = `rotation.py` (`RotationManager`), **RS** = `rotation_store.py` (`RotationStore`), **SS** = `sing_store.py`, **SMS** = `sms_store.py`, **PD** = `push_dispatcher.py`.
- **CK(label)** = an undo checkpoint: `RM._before_mutation` (rotation.py:390) → `RS.checkpoint` (rotation_store.py:1431). This pushes a full JSON snapshot of `rotation_entries` onto `rotation_history` (stack `undo`), **wipes the redo stack**, and prunes the undo stack to `MAX_HISTORY=30`.
- **AM** = `RM._after_mutation` (rotation.py:402). It bumps `rotation_meta.rotation_rev`, rewrites `/tmp/rotation_cache.json` (the conky overlay cache), and calls `PD.notify_rotation_changed()`. That call is debounced by 0.5s, then scans every active push subscription for the current token and sends a ladder push (`up_in_2` / `up_next` / `now_singing`) to any singer whose ladder step changed. It dedups against `sing_push_subscriptions.last_sent_state`.
- **Sheets**: no mutation triggers a Google Sheets write directly. `SheetSync._run` (rotation_sync.py:120) pushes `get_all_entries()` (including Done rows) every `rotation_sync_interval` seconds (default 30, app.py:38). The comment in `AM` confirms it is fire-and-forget. So **every** rotation mutation reaches Sheets within ≤30s, and nothing else does.
- **DEC** = `_decorate_rotation_entries` (R:3542). It adds `estimated_time`, `songs_sung`, `last_sang_minutes`/`wait_minutes`, `sms{}`, `media_meta`, and `photo_consent{}`, and it is read-only. Every mutation response returns `{success, entries}` decorated by DEC. The frontend replaces `rotationData` wholesale from any response carrying `entries`.
- Almost every route below touches the single DB `~/kjdata/rotation.db`, which holds `rotation_entries`, `rotation_meta`, `rotation_archive`, `rotation_history`, `sing_requests`, `singer_aliases`, `singer_photo_consent`, and `sing_push_subscriptions`. `sms_log` and `sms_opt_outs` live in the SMS store.

**Client polling that drives the KJ UI (important for replay):**
- `fetchRotation()` (app.js:5493) → `GET /rotation` every 10s (app.js:4828). It also runs after most actions.
- `updateStatus()` → `GET /status` every 2s (app.js:4827). It carries `download_queue` and `rotation_downloads`. When `rotation_downloads` changes, the client calls `fetchRotation()` (app.js:1714-1719).
- Every 15th status tick (about 30s), the client calls `pollGenStatuses()` → `GET /rotation/gen-status` (app.js:1738-1727).
- `SingRequests.fetchPending()` → `GET /rotation/requests?status=pending` every 5s (app.js:9814).
- `fetchSyncStatus()` → `GET /rotation/sync-status` every 30s (app.js:8123).
- **Auto-delete of cancelled rows:** any open KJ browser automatically `POST /rotation/delete`s a row whose status is `Cancelled` about 4.45s after first rendering it. The chain is `maybeAutoRemoveCancelled` → `beginCancelledLeave` → `autoRemoveCancelledEntry`, app.js:5565-5620. This is a **client-originated** mutation, and it creates a CK("Remove X"). A replay needs either a headless client or an equivalent server-side step, because otherwise Cancelled rows never disappear.

---

#### 3A.1 Rotation list: read, row status actions, add, edit, delete, reorder

| Capability (trigger) | app.js function (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| Load/refresh rotation (10s poll, **Refresh** button index.html:116, after most actions) | `fetchRotation` (5493) | GET `/rotation` | R:`get_rotation`:3566 | none. `RM.get_rotation` rewrites `/tmp/rotation_cache.json` on every read (rotation.py:52) | Response carries `entries` (all non-Done: Waiting/Now Singing/Up Next/Being Made (!)/On Hold (BRB)/Skipped/Left/Cancelled) plus `singer_stats` (+ `last_sang_minutes`, `session` provenance from `_add_singer_session_info`:3218), `rev`, and `history` (undo/redo counts+labels). Rendering is in `renderRotation` (5660) and `renderSingerStats` (6367) |
| Pills on each row: ×N sung, wait time, ⚠ not-sung-yet, ♥ paid, bump ⬆/⬇ badge, 📷 consent, ~ETA, NOW/NEXT/WAITING/MAKING/BRB/SKIP/CANCELLED badge, READY/DOWNLOADING/FAILED/URL/MAKING/NEEDS REVIEW/RENDERING/UNLINKED prep badge, ⚠ playability warning, SMS ✉ state | `renderRotation` (5660-6318) | (from GET `/rotation`) | DEC helpers: `_add_songs_sung`:3136, `_add_wait_pills`:3159, `_add_sms_status`:3372, `_add_media_meta`:3483, `_add_photo_consent`:3508, `_add_time_estimates`:3468 | none | songs_sung is a **min** across duet members, and wait is a **max**. Last-sang uses `done_at` (set only on the transition to Done, RS.update_status:376). The NEEDS REVIEW badge opens `gen.nomadkaraoke.com/app/jobs#/<gen_job_id>/review` (client only) |
| **▶ Play** on a linked row | `playAndAdvanceRotation` (7515) → `playMedia` (411) + `advanceRotationStatus` (7598) + `armAutoTextNextSinger` (7539) if idx==0 | POST `/play` `{file_path, entry_id}` then POST `/rotation/status` `{updates:[{id,'Now Singing'},{next.id,'Up Next'}]}` | R:`handle_play`:976 (playback is covered in another section). R:`update_rotation_status`:3588 batch path → RM.update_statuses:103 | `rotation_entries.status` for 2 rows. The exclusivity rule resets other Now Singing / Up Next rows to Waiting (RS.update_status:376) | **One** CK("Advance rotation") and one AM (a single undo step). `/play` → mpv/VLC load plus `_record_play_stat` (R:86 → stats_store `record_play` with entry_id + singer). Arms a 20s client timer for auto-SMS (see 3A.6) |
| ▶ Play for a URL-fallback (browser-mode) row | inline (6024) → `enableBrowserMode` + `advanceRotationStatus` | POST `/browser-mode/enable`, POST `/rotation/status` (batch) | R:`browser_mode_enable`:5552, R:`update_rotation_status` | same as Play | Stops the player and launches Chromium (browser mode is covered in another section). No auto-SMS arm |
| **Singing** button | `updateRotationStatus` (8003) via row btn (6078) | POST `/rotation/status` `{id,status:'Now Singing'}` | R:`update_rotation_status`:3588 → RM.mark_singing → update_status:97 | status. Other singing rows → Waiting | CK("Set status: Now Singing") + AM (push ladder `now_singing`) |
| **Done** button | `updateRotationStatus` (6087) | POST `/rotation/status` `{status:'Done'}` | same → RM.update_status | status='Done', `done_at`=now | CK + AM. The row leaves GET /rotation. It feeds songs_sung and last-sang |
| **Next** button | `updateRotationStatus` (6102) | POST `/rotation/status` `{status:'Up Next'}` | same → RM.mark_up_next | status. Other up-next rows → Waiting | CK + AM |
| **…** menu → any of Now Singing / Up Next / Waiting / Done / Being Made (!) / On Hold (BRB) / Skipped | `updateRotationStatus` (6125) | POST `/rotation/status` | same (free-text statuses go to RM.update_status) | status | CK("Set status: X") + AM. Setting Waiting on a Cancelled row within the ~4.45s window is the "restore" path. "Being Made (!)" pins the row to the bottom in Auto Order (auto_order.py:201) |
| **…** → Unlink Song (only if file_path or url_fallback) | inline fetch (6141) | POST `/rotation/unlink` `{id}` | R:`unlink_rotation_file`:4013 → RM.unlink_file:177 | clears file_path, duration, download_status/id/source, url_fallback (RS.unlink_file:735). Leaves gen_status | CK("Unlink file") + AM |
| **…** → Mark as Paid ♥ / Remove Paid ♥ | inline fetch (6166) | POST `/rotation/set-paid` `{id, paid:bool}` | R:`set_rotation_paid`:4414 → RM.set_paid:227 | `paid` | CK("Toggle paid") + AM. `paid` is a DIFF field. Non-bool `paid` → 400 |
| **…** → Bump Up ⬆ / Normal / Bump Down ⬇ (per entry) | `setRotationPriority` (8055) via (6197) | POST `/rotation/set-priority` `{id, bias:-1/0/1}` | R:`set_rotation_priority`:4457 → RM.set_priority_bias:240, then `_auto_order_response(checkpoint=False)`:3805 → `run_auto_order`:3757 → RM.reorder_by_ids:138 | `priority_bias` (clamped to {-1,0,1}), positions | **One** CK("Bump up"/"Bump down"/"Reset priority") covers both the bias change and the re-weave. AM ×2 (bias, then reorder if changed). The response includes `history` and `rev`. An unknown id → 404 **without** a checkpoint (validated first) |
| **Reorder** header button (Auto Order) | `autoOrderRotation` (8026) | POST `/rotation/auto-order` `{}` | R:`auto_order_rotation`:3833 → `_auto_order_response`:3805 → `run_auto_order` → `auto_order.compute_auto_order` → RM.reorder_by_ids | positions only | CK("Auto Order") + AM **only if the order changed**. A no-op leaves the undo stack untouched. Response `changed` bool. Rows 1-3 are never moved, rows 4-5 are frozen except for duplicate bumps, and "Being Made" rows sink to the bottom (auto_order.py docstring) |
| Drag ⠇ handle and drop onto another row | row `drop` handler → `moveRotationEntry` (8081) via (5763) | POST `/rotation/move` `{id, new_position}` (target row's absolute `position`) | R:`move_rotation_entry`:3729 → RM.move_entry:132 | positions (shifting neighbours, RS.move_entry:425) | CK("Reorder queue") + AM. `new_position` is the absolute position including Done rows (positions aren't compacted when a row goes Done) |
| **+ Add** (header, index.html:118), then type singer pills + song, then **Add** button or Enter (no dropdown open, or add mode) | `toggleRotationAddForm` (8177), `addRotationEntry` (8190) | POST `/rotation/add` `{singers:[..], song_artist}` | R:`add_rotation_entry`:3701 → RM.add_entry:81, then `maybe_auto_reorder`:3787 | new `rotation_entries` row at max(position)+1. `singers_json` set when a list is passed (display = " & ".join) | CK("Add X") + AM, then an optional CK("Auto Order (new entry)") + AM if `sing_store.is_auto_reorder()` and the order changed. The API also accepts `notes`, `file_path` (duration looked up), and `url_fallback` (→ set_url_fallback, an AM without CK), but the UI never sends them |
| Edit an entry: ✎ button (6218) or Shift+click row/song/singer (5727). Save = button or Enter in song field | `enterRotationEditMode` (7272) → `saveRotationEdit` (7422) via (7354) | POST `/rotation/edit` `{id, singers:[..], song_artist}` | R:`edit_rotation_entry`:3638 → RM.update_entry:90 | singer, singers_json, song_artist | CK("Edit entry") + AM. Tab / `,` / `&` create singer pills |
| Delete entry (Delete button inside edit mode, with confirm) | `deleteRotationEntry` (7447) via (7374) | POST `/rotation/delete` `{id}` | R:`delete_rotation_entry`:3675 → RM.delete_entry:124 | row deleted, positions above it compacted | CK("Remove X") + AM |
| Auto-remove a singer-Cancelled row (automatic, see polling notes) | `autoRemoveCancelledEntry` (5594) | POST `/rotation/delete` | same | same | same (CK per open client that fires; later ones 500 with "Entry not found", which is harmless) |
| Copy singer/song text (plain click) | `copyRotationText` (7473) | none | none | none | clipboard only |
| 📁 Paths toggle, singer-stats Hide toggle | `toggleRotationFilePaths` (6318), `toggleSingerStats` (6352) | none | none | none | UI only |

#### 3A.2 Singer operations (singer-stats panel rows, `buildSingerActions` app.js:6529)

All of these go through `singerAction(action, data)` (app.js:6920) → POST `/rotation/singer/<action>`. The response comes from `_singer_action_response` (R:3302): `{entries, singer_stats}` with no `history` (except `priority`).

| Capability (trigger) | app.js (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| **Songs** modal (queued and sung list) | `openSingerSongsModal` (6654) | none (uses `singer.entries` from singer_stats) | none | none | read-only |
| Device/provenance modal | `openSingerDeviceModal` (7020) | none | data from `_add_singer_session_info` (R:3218) | none | shows singer_ui / duet_partner / kj_added origin, phone, and parsed UA |
| **Edit** (rename): Save or Enter | `enterSingerEditMode` (6943) → `singerAction('rename')` (6971) | POST `/rotation/singer/rename` `{old_name,new_name}` | R:`rename_singer_route`:4650 → RM.rename_singer:344 (RS:951) + `_persist_singer_rename`:4632 → SS.persist_rename:1173 | `rotation_entries.singer/singers_json` on **all rows including Done** (exact-case match). `rotation_meta.left_singers_json` migrated. `singer_aliases` (origin='kj') for tonight's devices. `sing_requests.singer_name` rewritten (case-insensitive, night-scoped). `singer_photo_consent` carried over (SS.carry_photo_consent:631, 'no' wins on conflict) | CK("Rename A → B") + AM. Future `/sing/submit` from those devices resolves to the new name |
| **Merge** (modal, pick the keeper) | `openMergeModal` (7093) / `renderMergeModal` (7168) → `singerAction('merge')` (7268) | POST `/rotation/singer/merge` `{source_name,target_name}` | R:`merge_singers_route`:4689 → RM.merge_singers:356 (RS:1041) + `_persist_singer_rename` + SS.mark_identity:1140 | entries (exact-case; dedups duet arrays), left-meta for the source removed, aliases for both sides' devices (origin='kj'), request names, consent | CK("Merge A → B") + AM |
| **Split** (modal: choose entries plus a new name) | `openSplitModal` (6773) → `singerAction('split')` (6905) | POST `/rotation/singer/split` `{source_name,new_name,entry_ids:[..]}` | R:`split_singer_route`:4796 → RM.split_singer:380 (RS:1085) | chosen entries only (case-insensitive). A duet that collapses to one name → `singers_json=NULL` | CK("Split A") + AM. No alias/request rewrite. Unknown id → 400 (ValueError), **after** the CK was already taken |
| **BRB / Back** | `singerAction('brb', {name, brb})` (6577) | POST `/rotation/singer/brb` | R:`singer_brb_route`:4717 → RM.set_singer_status:362 | every non-Done entry for that singer → "On Hold (BRB)" or "Waiting" (exact-case name match) | CK("Set NAME: status") + AM. **Back sets Waiting**, which silently clears Now Singing / Up Next / Skipped / Being Made on that singer's rows |
| **Priority** → Bump Up / Normal / Bump Down (singer-level) | `singerAction('priority', {name,bias})` (6602) | POST `/rotation/singer/priority` | R:`singer_priority_route`:4735 → RM.set_singer_priority_bias:251 + `_auto_order_response(checkpoint=False)` | `priority_bias` on all non-Done entries (exact-case), positions | one CK("Bump up: NAME") + AM(s). The response includes `history`, and the client refreshes the undo buttons |
| **Left** (remove singer) | `singerAction('remove')` (6617) | POST `/rotation/singer/remove` | R:`remove_singer_route`:4762 → RM.set_singer_status(name,"Left") + RM.mark_singer_left:368 | non-Done entries → "Left", and `rotation_meta.left_singers_json` += lowercased name | **Two** CKs ("Set NAME: Left", "NAME left") + two AMs. One Undo only reverts the meta step. The ladder push skips Left rows (PD.decide_ladder_step) |
| **Restore** (for a singer with status left) | `singerAction('restore')` (6542) | POST `/rotation/singer/restore` | R:`restore_singer_route`:4779 → set_singer_status("Waiting") + unmark_singer_left:374 | entries → Waiting, meta name removed | **Two** CKs + AMs |
| 📷 photo-consent marker next to each name on a rotation row (click cycles unknown→yes, yes→no, no→yes) | `photoConsentMarker` (5624) → `setSingerPhotoConsent` (5644) | POST `/rotation/singer/photo-consent` `{singer, consent:'yes'/'no'/null}` | R:`singer_photo_consent_route`:4668 → SS.set_photo_consent:586 (source='kj') | `singer_photo_consent` upsert keyed by casefolded name. `null` deletes the row | **No CK, no AM, no rev bump, no push.** Other KJ devices see it at their next 10s poll. Consent is night-scoped via `updated_at >= night_started_at`. Unknown is displayed as NO |

#### 3A.3 Undo/redo, New Rotation (archive), sheet restore, sync status

| Capability (trigger) | app.js (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| **↩ Undo** / **↪ Redo** (index.html:112-113). Buttons enabled from `history` counts. There is no keyboard shortcut | `rotationHistory.undo/redo` → `_run` (app.js:22) | Phase 1: POST `/rotation/undo` (or `/redo`) `{}` returns a preview `{label, diff{removed,added,changed}, rev}`. Then a `confirm()` dialog. Phase 2: POST same `{confirm:true, expected_rev}` | R:`_undo_or_redo`:4548 (routes `undo_rotation`:4620 / `redo_rotation`:4626) → RM.preview_undo / RM.undo:303 → RS._apply_from:1480 → RS.restore_entries(preserve_tracking=True):1301 | Replaces **all** `rotation_entries` with the snapshot. The current state is pushed to the opposite stack | AM on success (rev bump plus push ladder). A stale `expected_rev` → `{success:false, reason:'stale'}`, and the client re-fetches and re-previews. An empty stack → `{success:false, reason:'empty'}` |
| **New Rotation** (index.html:119, with confirm) | `archiveRotation` (8966) | POST `/rotation/archive` | R:`archive_rotation`:3848 → RM.archive_rotation:267 → RS.archive:1234 + RS.clear_history:1511 | Copies all rows → `rotation_archive` (night_date=today), deletes all `rotation_entries`, clears `left_singers_json`, sets `rotation_meta.night_started_at`=now, adds a starter entry "Andrew / First Song of the Night". Clears undo+redo. `SS.set_enabled(True)` | AM (no CK: **not undoable**). The event token is deliberately NOT regenerated. The night boundary re-scopes SMS/push phone resolution, photo consent, and alias/rename. Entry ids stay monotonic (sqlite_sequence is not reset) |
| **Restore** from Google Sheet (index.html:114, with confirm) | `restoreFromSheet` (8125) | POST `/rotation/restore` with no body | R:`restore_rotation_from_sheet`:4508 (no-body branch) → RM.restore_from_sheet:280 → SheetSync.restore_from_sheet (rotation_sync.py:195) | Deletes all entries, **resets sqlite_sequence**, re-inserts singer/song/status/notes from the sheet with fresh ids 1..n (no file links or tracking) | CK("Restore from sheet") (undoable) + AM. A RuntimeError → 500 when Sheets isn't configured |
| Snapshot restore (legacy API, no UI caller) | none | POST `/rotation/restore` `{entries:[..]}` | same handler (JSON branch) → RM.restore_entries:294 | full replace (preserve_tracking=False) | AM, **no CK** |
| Sheet sync dot | `fetchSyncStatus` (8103), every 30s | GET `/rotation/sync-status` | R:`rotation_sync_status`:4494 → RM.get_sync_status | none | `{last_sync,is_online,next_sync_in}` |

#### 3A.4 Linking media to rotation entries (add/link search dropdown, Try Another, MAKE, downloads)

The "+ Add" form doubles as **link mode**. The 🔗 row button calls `openLinkSearch(entryId, song)` (8137). That sets `form.dataset.linkTargetId`, pre-fills the song, and fires the search immediately. Results render in `renderRotSearchDropdown` (8355). Only an explicit click on a row's **Link** (`rotLinkBtn` 8625), **Download** (`rotDownloadBtn` 8611), or the **MAKE** row calls `selectRotSearchResult(result)` (8844). Enter never links (8274-8290). All of these go through `rotationMutate` (246) → `apiCall` (214), and failures are only logged. `buildCall` maps result.type → endpoint.

| Capability (trigger) | app.js (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| Type-ahead search in the Add/Link song box (700ms debounce, ≥3 chars). **Search** button or Enter in link mode runs it immediately | `initRotationSearch` (8242) → `doRotationSearch` (8306), `triggerRotationSearchNow` (8335) | GET `/rotation/search?q=` | R:`rotation_search`:5273 → `unified_search`:5089 (local catalog + media index, `karaoke_nerds.search` via local `catalog_mirror` → Divebar Cloud Function, `_divebar_search_local_first`:5074) + `_enrich_search_stats`:241 | none | Parallel KN + Divebar lookups (ThreadPoolExecutor). A `karaoke_nerds_timeout` flag is set on KN failure. Stale responses are dropped client-side through the `rotSearchGen` counter. Stats are read-only |
| Link a **local** result (in link mode) | `selectRotSearchResult` (8844) | first POST `/rotation/edit` `{id, song_artist}` (if there's text), then POST `/rotation/link` `{id, file_path}` | R:`edit_rotation_entry`, then R:`link_rotation_file`:3959 → `_playability_gate`:3873 (quick integrity/decode) → RM.link_file:164 | song_artist, then file_path + duration (from the media index) | **Two** CKs ("Edit entry", "Link file") + 2 AMs. A gate failure → 422 plus `verdict` (a toast via `showPlayabilityToast`). After a successful link: an async `library_media.ensure_library_row_for_app` (materialises the `media_library` row for SSD files) and `_enqueue_tier2`:3938 (a background single-thread deep render check against the active renderer → `RS.set_playability_warning`, which writes directly with **no AM/rev bump and no updated_at**) |
| Add a new singer **with** a local result (add mode) | `selectRotSearchResult` | POST `/rotation/link` `{singers, song_artist, file_path}` | R:`link_rotation_file` → `_resolve_or_create_rotation_entry_id`:3342 → RM.add_entry, then the gate, then link_file | new row, then the link | CK("Add X") + CK("Link file"). **The entry is created before the playability gate**, so a 422 leaves an unlinked orphan row. `maybe_auto_reorder` is **not** called on this path |
| Download & link (YouTube / KN community track) | `selectRotSearchResult` | POST `/rotation/download-and-link` `{id | singers+song_artist, source:'youtube', youtube_url, filename}` | R:`download_and_link_rotation`:5351 | Dedup hit (`_existing_media_for`:737 matches the youtube media_id on disk): (add +) link_file + set_download_status('complete'). Otherwise: a queue-capacity check (max 5 active → 409), then (add), then `set_download_status(entry, 'youtube','queued', uuid)`, then append to the in-memory `app.download_queue['items']` | The add path takes a CK. **set_download_status has no CK** (AM only), so a DL&Link on an existing row is undoable only via the preceding Edit CK. Starts the `_download_worker` thread (R:816) if idle. The worker mirrors queue status → entry (`_sync_rotation_download`:680, guarded by a download_id match) and on completion calls `RM.complete_download`:195 (links the file, fills an empty song_artist from the title, sets status complete, AM with no CK) |
| Download & link (Divebar / community mirror) | `selectRotSearchResult` | POST `/rotation/download-and-link` `{..., source:'divebar', file_id, artist, title, brand_code, format}` | same handler → `_resolve_divebar_spec`:753 (gets the Drive/GCS URL; a loose `.cdg` is paired with sibling audio via `divebar.find_sibling_audio` → a queued cdg+mp3 zip, or 422 if there's no audio) | same as the YouTube row | The spec is resolved **before** entry creation (no orphan on failure). The worker uses `media.download_cdg_pair` / `download_from_url` (source='community', ref `<brand>-<fileid>`) |
| **MAKE** result row (generate with karaoke-gen) | `selectRotSearchResult` (type 'make', parses "Title - Artist") | POST `/rotation/make` `{id | singers+song_artist, artist, title}` | R:`make_rotation_entry`:5482 → `gen_client.create_job` → RM.set_gen_status:221 | (new row) + `gen_job_id`, `gen_status` (mapped via `map_gen_status`) | An outbound HTTP call creates a gen job. set_gen_status is AM-only (no CK). `GenPoller.poll_once` (gen_poller.py, every `gen_poll_interval`=60s) updates gen_status. On COMPLETE, it downloads `GEN-<job8> - artist - title.mp4` and calls `RM.complete_gen_job` (links the file, AM) |
| Gen status poll (every ~30s) | `pollGenStatuses` (1738) | GET `/rotation/gen-status` | R:`rotation_gen_status`:5526 → RS.get_active_gen_entries | none | If any are active, the client calls `fetchRotation()` |
| ✕ Cancel a queued rotation download (row button, shown only when effective status is queued and a download_id exists) | inline (6046) → `apiCall('/download/cancel')` then `fetchRotation` | POST `/download/cancel` `{id: download_id}` | R:`cancel_download`:898 → `_clear_rotation_download_for_item`:706 | removes the queue item. The entry's download_source/status/id → NULL (only if download_id still matches) | AM (no CK). Returns 409 if already `downloading` |
| **Try Another** (Playback Controls, index.html:63): swap the currently playing song to another local version | `tryAnother` (607) → `_renderTryAnother` (634) → **Play this →** `swapToVersion` (717) | GET `/playback/alternates`, then POST `/rotation/link` `{id, file_path}`, then POST `/play` `{file_path, entry_id}` | R:`playback_alternates`:5292 (resolves the entry from `vlc.current_playing_path` plus the Now Singing row, then `unified_search(local_only=True)`), R:`link_rotation_file`, R:`handle_play` | the entry's file_path | CK("Link file") + AM, tier-2 enqueue, then live player hot-swap plus a play stat. The link happens first, so a gate reject leaves the current song playing |

**Stand-alone search panels** (right side of the page; not linked to rotation):

| Capability (trigger) | app.js (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| Library panel filter (search box) | `catalogSearch` (2530) | GET `/library/search?q=&limit=50` (AbortController drops superseded keystrokes) | R:`library_search`:1599 → `unified_search(local_only=True, catalog_limit)` | none | Media-index rows are kept in preference to catalog overflow. Clicking a result plays or previews it (covered in another section) |
| Catalog FTS (API only; no app.js caller) | none | GET `/search?q=&limit=&offset=` | R:`search_catalog`:1575 → `catalog.search` | none | 503 if the catalog hasn't been built |
| KaraokeNerds panel: **Search** (index.html:444) | `searchKaraokeNerds` (3519) → `renderKNResults` (3562) | POST `/karaoke-nerds/search` `{query}` | R:`kn_search`:2013 → `unified_search(grouped=True, include_disc_only=True, catalog_limit=sing_search_catalog_limit or 60)` | none | Song-grouped result shape, the same as singer search. **Never scrapes karaokenerds.com** (karaoke_nerds.py docstring) |
| KN panel → Download (YouTube track) | `downloadKNTrack` (3816) | POST `/download` `{url}` | R:`handle_download`:551 | in-memory `download_queue` item (no rotation link) | Dedup-skip if already on disk (`{deduped:true, file_path}`). 409 on a duplicate URL or a full queue (5). Starts the worker. Blocked in sleep mode (`_check_sleep_mode`) |
| KN panel → Download (mirror / Divebar version) | `makeDivebarDownloadBtn` (3775) → `downloadDivebarTrack` (4185) | POST `/divebar/download` `{file_id, artist, title, brand_code, format}` | R:`divebar_download`:2278 → `_resolve_divebar_spec` | queue item | same dedup/capacity behaviour. The worker downloads into community/ |
| YouTube panel: **Search** (index.html:457) and Download | `searchYouTube` (3934), `downloadYTTrack` (4011) | POST `/youtube/search` `{query}`; POST `/download` | R:`yt_search`:2125 → `youtube_search.search`; R:`handle_download` | none / queue item | yt-dlp search (the "karaoke" prefix toggle is client-side, 4017) |
| Divebar panel: **Search** (index.html:479) and Download | `searchDivebar` (4029), `downloadDivebarTrack` (4185) | POST `/divebar/search` `{query}`; POST `/divebar/download` | R:`divebar_search`:2209 → `divebar.search` (Cloud Function); R:`divebar_download` | none / queue item | 503 if `divebar_api_url` is unset |
| Divebar KN-id cross-ref (API only; no app.js caller) | none | POST `/divebar/kn-lookup` `{kn_ids:[..]}` | R:`divebar_kn_lookup`:2226 → `divebar.lookup_kn_ids` | none | returns `{}` if unconfigured |
| Paste-URL **Download** (index.html:497) | `downloadSong` (276) | POST `/download` `{url}` | R:`handle_download`:551 | queue item | as above |
| **Upload** a media file (hidden `#upload-file` input, index.html:501) | `uploadFile` (290) | POST `/upload` (multipart `file`) | R:`handle_upload`:601 → `_playability_gate` → `media.import_upload` | file moved to `<download_folder>/upload/<slug [up-<hash>]>` plus a `media_library` row. The index is rescanned | A gate reject → 422 and the staging file is removed. Extension whitelist `MEDIA_EXTENSIONS` |
| Download-queue Cancel / Dismiss, and auto-dismiss of completed items 3s later | `cancelQueueItem` (395), `ackQueueItem` (399), `handleDownloadQueue` (370) | POST `/download/cancel` `{id}`; POST `/download/ack` `{id}` (no id = all finished) | R:`cancel_download`:898, R:`ack_download`:918 | queue list. For an **errored** rotation-linked item, the entry's download fields are cleared | AM on rotation-linked clears. After a completed download the client calls `refreshMediaData()` and refreshes the library |

#### 3A.5 Requests right rail (singer requests from `/sing`) and Request-form settings

`SingRequests` IIFE (app.js:9099-9880). The pending list re-polls every 5s. Row types come from `renderRow` (9204): **tip** (Confirm/Dismiss), **reorder** (Approve/Reject), **kj_pick** (a version picker: `renderKjPickPicker` 9353, the ⭐ BEST hero card `renderHeroCard` 9419 and `renderVersionCard` 9443, each "Approve →" sends `version_index`), **youtube** (Approve plus "Approve, link later" = `skip_download`), and everything else (Approve/Edit/Reject). A `supersedes_request_id` row is shown with ✎ (a change request).

| Capability (trigger) | app.js (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| Pending list and count badge (5s poll) | `fetchPending` (9104) | GET `/rotation/requests?status=pending` | R:`list_sing_requests`:6191 | none | `edit_token` is stripped from the response. `counts` by status |
| **Approve**: local source | `approve(id)` (9505) | POST `/rotation/requests/<id>/approve` (no body) | R:`approve_sing_request_route`:6381 → `approve_sing_request`:5994 | `rotation_entries` add (with `file_path`=source_ref, singers_json for duet partners). `sing_requests.status='approved'`, `reviewed_at`, `linked_entry_id` (SS.mark_approved:968) | CK("Add X") + AM (ladder push). Then **immediate** `PD.notify_request_decision(...'approved')` web push to the singer's subs (by phone plus token). Then `maybe_auto_reorder` (optional CK "Auto Order (new entry)"). The client then calls `fetchRotation()` |
| **Approve**: youtube / kn / divebar | `approve(id)`; "Approve, link later" → `approve(id,{skipDownload:true})` (9280) | POST `.../approve` `{skip_download?:true}` | same → download branch of `approve_sing_request` | add entry. Dedup hit → link_file (CK) + set_download_status complete. Otherwise → set_download_status queued plus a queue item. **YouTube items carry `request_id` + ranked `candidates`** | Worker auto-fallback (`_attempt_sing_fallback`:5926): a transient error → retry up to `sing_resolve.MAX_TRANSIENT_RETRIES`. Unavailable → advance to the next candidate and `SS.update_request_source`. Terminal outcomes push `resolved_alt` / `unavailable` via `_notify_sing_outcome`:5981 |
| **Approve**: kj_pick with a chosen version | hero or version card → `approve(id,{versionIndex})` | POST `.../approve` `{version_index}` | same → `_pick_version_from_kj_pick`:5671 + `_preserve_versions_meta`, then `SS.update_request_source`, then `approve_sing_request` | the request row's source_type/ref/meta are **rewritten before** approval, then as above | A missing or out-of-range index → 400. (The auto-approve path in sing.py:1061-1078 uses `resolve_kj_pick_best`:5771 instead) |
| **Approve**: make | `approve(id)` | POST `.../approve` | `approve_sing_request` make branch | add entry, then try `gen_client.create_job`. On success: set_gen_status. On **any** gen failure: status "Being Made (!)" and left unlinked. Always marked approved | Outbound gen job. See the idempotency notes |
| **Approve**: change request (`supersedes_request_id`) | `approve(id)` | same | route supersede block (6443-6475) | new entry takes the old entry's slot: RM.delete_entry(old) + RM.move_entry(new, old_pos), and the original request → `cancelled`. Skipped if the old entry is Done / Left / Now Singing | 2 extra CKs + AMs. Best-effort (exceptions are logged and never fail the approval) |
| **Confirm** tip claim | `approve(id)` (tip row) | POST `.../approve` | route tip branch → `apply_confirmed_tip`:5803 | `paid=1` on every active entry for that singer (exact name). At or above `sing_tip_priority_threshold` (effective tip settings, default $20): singer bias +1. Request approved with `linked_entry_id=NULL` | **One CK per hearted entry** ("Toggle paid") + a CK for the bump, then `run_auto_order(checkpoint=True)` (another CK if the order changed). **No** decision push |
| Approve a **reorder** request | `approve(id)` (reorder row) | POST `.../approve` | route reorder branch → `apply_reorder_request`:5855 | the singer's own entries are moved into the requested order within the slots they already occupy | **One CK per moved entry** ("Reorder queue"). No decision push. (Also auto-applied from sing.py:1761 when auto-approve is on) |
| **Reject** / tip **Dismiss** (with confirm) | `reject(id,msg)` (9527) | POST `.../reject` `{}` | R:`reject_sing_request_route`:6519 → SS.mark_rejected:986 | `status='rejected'`, `reviewed_at`, `rejected_reason` | `PD.notify_request_decision('rejected')` push, **except** for tips. No rotation change. Rejecting a non-pending request is allowed (no 409) |
| **Edit** request, then **Save & Approve** | `editInline` (9544) | POST `/rotation/requests/<id>/edit` `{singer_name, song_artist, song_title}`, then `approve(id)` | R:`edit_sing_request_route`:6499 → SS.update_request:877 | request fields (the API also allows source_type/ref/meta/notes) | then the approve flow above |
| Settings modal: open (loads config plus QR images) | `openModal` (9793) → `fetchConfig` (9115) → `applyConfigToModal` (9573) | GET `/rotation/requests/config`; GET `/rotation/requests/qr.svg?scope=public|local&cb=` | R:`get_sing_config`:6212 (`SS.ensure_token` may **create** a token on first call), R:`sing_qr_svg`:6357 | token created if missing | QR generated in-process with `qrcode` |
| Toggles: accepting requests (kill switch), auto-approve, accept MAKE requests, auto-SMS next singer, auto-reorder | `toggleEnabled` (9699), `toggleAutoApprove` (9710), `toggleAcceptMake` (9719), `toggleAutoSmsNext` (9728), `toggleAutoReorder` (9737) → `postConfig` (9676) | POST `/rotation/requests/config` `{enabled|auto_approve|accept_make_requests|auto_sms_next|auto_reorder: bool}` | R:`update_sing_config`:6254 → SS setters | `rotation_meta` keys (SS._set_meta) | `auto_sms_next` and `auto_reorder` must be real JSON booleans (otherwise 400). No rotation or undo effect |
| Simple / Advanced KJ mode switch | `setKjMode` (3313) | POST `/rotation/requests/config` `{simple_mode:true, enabled:true, accept_make_requests:false}` or `{simple_mode:false}` | same | meta | UI mode (covered in another section) |
| Regenerate token / Set custom 4-digit code | `regenerate` (9749), `setCustom` (9754) | POST `.../config` `{regenerate:true}` / `{token:"1234"}` | same → SS.regenerate_token / set_token plus `_on_token_changed` | token meta | `sync_event_url_overlays` rewrites the QR/URL overlays. `cleanup_stale_push_subscriptions(current_token)` disables old-token push subs |
| SMS template / region save and reset; tip settings; footer settings | `saveSmsTemplate` (9817), `resetSmsTemplate` (9825), `toggleTipsEnabled` (9830), `saveTipSettings` (9839), `saveFooterSettings` (9862) | POST `.../config` `{sms_template, sms_default_region}` / `{sms_template:null}` / `{tip_settings}` / `{footer_settings}` | same | meta | validation errors → 400 |
| Copy public/local URL | `copyUrl` (9783) | none | none | none | clipboard |

#### 3A.6 Rotation SMS ("you're up" texts via Telnyx)

The ✉ SMS button is rendered on every row when `sms.configured` is true, and disabled when `sms.available` is false (no night-scoped linked request with a phone). A sent row shows a delivery state (`smsDeliveryState` 7630: delivered / failed / pending).

| Capability (trigger) | app.js (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| ✉ SMS on a row not yet texted: preview panel | `openSmsPreview` (7868) | POST `/rotation/sms/preview` `{entry_id}` | R:`sms_preview`:4125 → `_resolve_sms_target`:4044 + `sms.render_template` | none | returns `{phone_e164, first_name, song, artist, body, length, segments}`. 503 if SMS isn't configured, 400 if there's no phone or it's invalid |
| Preview **Send** (button or Ctrl/Cmd+Enter 7996) | `doSend` in `openSmsPreview` (~7955) | POST `/rotation/sms/send` `{entry_id, body}` (KJ-edited body sent verbatim) | R:`sms_send`:4275 → `_perform_sms_send`:4200 → `sms.send` (Telnyx) | `sms_log` row (`SMS.record_send`:110): status sent/failed, telnyx_message_id, error, kj_user_agent | **Real outbound SMS.** Opted-out numbers (`sms_opt_outs`) → 403 and a failed log row. Telnyx error → 502. Body length limit `sms.MAX_BODY_LEN`. No rotation mutation (no CK/AM). The client calls `fetchRotation()` so the marker updates. The delivery-receipt webhook `/sing/telnyx/webhook` later overwrites status to delivered / delivery_failed (covered in the /sing section) |
| ✉ on an already-sent row: details modal, then **Resend** / **Retry send** (Ctrl/Cmd+Enter 7859) | `openSmsDetails` (7672), `doResend` (~7813) | POST `/rotation/sms/detail` `{entry_id}`; then POST `/rotation/sms/send` | R:`sms_detail`:4166 (`SMS.get_latest_for_entry`:214, 404 if none), then as above | new `sms_log` row | as above |
| **Auto-text next singer** (opt-in `auto_sms_next`). Armed when ▶ Play is pressed on **slot 1** (idx 0). Fires after 20s | `armAutoTextNextSinger` (7539) → `maybeAutoTextNextSinger` (7550) | POST `/rotation/sms/auto-send` `{entry_id: slot2.id, playing_entry_id: slot1.id}` | R:`sms_auto_send`:4311 → `_perform_sms_send` with the stored template | `sms_log` row | Guards are checked on the client and re-checked on the server: the setting is on, `entry_id` is currently slot 2, `playing_entry_id` is slot 1, `vlc.current_playing_path == slot1.file_path`, there's no prior `sms_log` row for the entry (any attempt blocks), and a phone is on file. Skips return 200 `{sent:false, skipped:<reason>}`. Playing any row other than slot 1 cancels the timer. Timer state is client-side, so replay requires the same Play-on-slot-1 plus 20s still-playing sequence |

---

#### Non-obvious invariants and gotchas (for fixtures and simulation)

1. **Undo checkpoint granularity isn't one per user action.** Several compound actions take multiple CKs:
   - Singer Left/Restore (2).
   - Link-mode select (Edit + Link = 2).
   - Add-with-local-result (Add + Link).
   - Supersede approval (Add + Delete + Move, plus optional Auto Order).
   - Tip confirm (one per hearted entry, plus the bump and Auto Order).
   - Reorder-request approve (one per moved entry).

   Other actions take no CK at all:
   - `set_download_status`, `complete_download`, `set_gen_status`, `complete_gen_job`, `set_url_fallback`, archive, and snapshot restore.
   - Photo consent (it touches no rotation state at all).

   The only actions that deliberately collapse to a single CK are the batch status advance (Play) and the priority bumps (`checkpoint=False` on the re-weave). Each CK also **clears the redo stack**.
2. **Undo does not undo file links.** `RS._apply_from` restores with `preserve_tracking=True`, which keeps the *live* `_TRACKING_FIELDS` for any id still present: file_path, duration, download_*, url_fallback, gen_*, playability_warning. So "Undo Link file" / "Undo Unlink file" restores human fields only, and the file link stays as it is now. `priority_bias` and `position` are restored from the snapshot but are **not** in `_DIFF_FIELDS` (rotation_store.py:15). The undo preview therefore can't show bias/reorder changes, and a bump-only undo previews as an empty diff labelled "Bump up".
3. **Two-phase undo with a rev guard.** The preview is side-effect-free. Confirm with a stale `expected_rev` → `reason:'stale'`. The rev is bumped by *every* AM, including background download/gen transitions and singer self-submits. So on a busy night confirm may loop through re-previews.
4. **Entry id reuse.** The fix for the cross-night "Connie" bug removed the sqlite_sequence reset from `archive()`, so ids stay monotonic across New Rotation. However, `RS.restore_entries` (used by **every undo/redo**) still runs `DELETE FROM sqlite_sequence WHERE name='rotation_entries'` (rotation_store.py:1328) and re-inserts explicit ids. `SheetSync.restore_from_sheet` does the same and renumbers from 1.
   - After undoing an "Add" of the highest id N, the next add reuses N **within the same night**.
   - `_add_sms_status` / `_resolve_sms_target` / the push phone lookup match `sing_requests.linked_entry_id` with only night scoping. So a KJ-added row reusing N could resolve to the undone request's phone.
   - `sms_detail` / `auto-send already_sent` key on entry id too. Stats `record_play(entry_id=…)` is also affected.
   - This is inferred from the code, not observed, and should be verified with a test. Fixtures should record raw ids, not assume they are monotonic.
5. **Name matching is inconsistent.** `rename_singer`, `merge_singers`, `set_singer_status` (brb/left/restore) and `set_singer_priority_bias` use **exact-case** membership. `split_singer`, `rename_singer_in_entries`, `persist_rename`, left-meta and photo consent are case-insensitive (photo consent is also whitespace-folded). `apply_confirmed_tip` is exact-case too.
6. **Approve isn't atomic.** The route checks `status=='pending'` (409 otherwise), then runs `approve_sing_request`, then `mark_approved`. The client `approve()` has no in-flight guard, so a double click during a slow branch (gen `create_job`, Divebar URL resolution) can create two entries or two gen jobs.
   - The MAKE branch was hardened so that approval *always* succeeds and a gen failure leaves the entry as "Being Made (!)". That prevents the "stuck pending → re-click → duplicate gen job" loop, but not a true concurrent double-submit.
   - `POST /rotation/make` is itself non-idempotent: each call creates a new gen job and overwrites `gen_job_id`, which orphans the prior job.
   - kj_pick approval rewrites the request's source *before* `approve_sing_request`. If that then fails (500), the request stays pending but is no longer `kj_pick`.
7. **Orphan-entry asymmetry.** `/rotation/download-and-link` and approval resolve Divebar specs *before* creating an entry. `/rotation/link` in add mode creates the entry *before* the playability gate, so a 422 leaves an unlinked row.
8. **Download ↔ rotation sync is guarded by download_id.** `_sync_rotation_download` and `_clear_rotation_download_for_item` only touch the entry if `entry.download_id` still equals the queue item id, so a stale worker can't clobber a retry. The client `effectiveDownloadStatus` (5465) treats a queued/downloading row whose queue item is gone as `failed`. `rotationMutate` pre-seeds `lastRotationDownloads` to avoid a "failed" flash. The download queue is **in-memory only** and is lost on restart; entries would be left `queued`/`downloading` and render as FAILED.
9. **Auto-reorder hooks.** `maybe_auto_reorder` runs after `/rotation/add` and request approval (the route and the sing auto-approve path), but **not** after `/rotation/link`, `/download-and-link` or `/make` add-mode creates. Auto Order is deterministic given the decorated entries, but it depends on `wait_minutes` (wall-clock `now`). Simulations must freeze time for reproducible orderings.
10. **Status strings are free text.** The canonical set is Waiting (default), Now Singing, Up Next, Done, Being Made (!), On Hold (BRB), Skipped, Left, Cancelled (Cancelled is set by the singer UI). Exclusivity applies only to the singing variants (`now singing`/`singing now`/`singing`) and up-next variants (`up next`/`next`). Only `done` is excluded from GET /rotation. Left/Cancelled rows still come back. Push ladder and wait logic ignore done/left.
11. **Photo consent, SMS sends and request approve/reject decisions don't bump `rotation_rev`.** Other devices only see them on the 10s poll. Push for approve/reject is *immediate* (`notify_request_decision`), while ladder pushes are debounced 0.5s after any AM and deduped per subscription by `(entry_id, ladder_step)`.
12. **Sheets restore can resurrect stale rows (inferred).** `SheetSync.sync_now` writes only rows 1..n with `sheet.update` and never clears trailing rows. After a rotation shrinks (e.g. New Rotation leaves one starter row), older rows remain in the sheet, and "Restore" would import them. It also drops file links and tracking and renumbers ids.
13. **Night boundary.** `night_started_at` (set by archive, or by `ensure_night_started` on first boot) scopes SMS target resolution, the `sms.available` flag, photo consent, `persist_rename`/`mark_identity`, and singer-session provenance. A fixture must capture it alongside the rotation DB.

### 3B. Header, playback, overlays, settings, system, preview, stats

Scope: everything in the KJ UI (`templates/index.html` + `static/app.js` + `static/preview.js`) that isn't rotation, requests, SMS, or media-linking. Line numbers are from the current worktree. Route lines point at the `@routes_bp.route` decorator in `kj-controller/routes.py`, and the handler `def` is on the next line. "coord" means `current_app.vlc`, which is the `PlaybackCoordinator` (`playback.py:49`). It owns `.player` (a `MpvKaraokePlayer` in `mpv_manager.py:42` or a `VlcKaraokePlayer` in `vlc.py:26`) and `.filler` (a `FillerVLC` in `filler.py:26`, which uses the VLC HTTP interface on `filler_vlc_port`, default 8081). `apiCall()` (`app.js:214`) always sends `POST` with a JSON body. On a non-2xx response it logs the error and returns `null`, and a 422 with `verdict` shows the playability toast.

Sleep gate: `_check_sleep_mode()` (`routes.py:52`) returns **409** when `SleepManager.is_sleeping()` is true, meaning the `SLEEP_FLAG` file exists. Only `/play`, `POST /filler_music`, `/browser-mode/enable` and `/browser-mode/navigate` call it. Every other route here still runs while the box is asleep.

Dev/test gate: `/play` returns **503** when `coord.enabled` is false. That is the default off-Pi unless `enable_vlc` is set. When `coord.enabled` is false, most other player calls do nothing and return 200.

---

#### 3B.1 Polling loops (what the UI polls, and how often)

| Loop | Trigger / cadence | Endpoint | Renders |
|---|---|---|---|
| `updateStatus()` app.js:1631 | Runs once on DOMContentLoaded (app.js:4781), then `setInterval(updateStatus, 2000)` (app.js:4827). Errors are swallowed silently. | `GET /status` | See 3B.2. It also drives the VNC auto-pause (`perfMaybeAutoPauseVnc`, app.js:5186) and calls `pollGenStatuses()` → `GET /rotation/gen-status` on every 15th tick (about every 30s, app.js:1718-1723). |
| `fetchSystemStats()` app.js:4844 | Runs once at load, then `setInterval(…, 5000)` (app.js:4949) | `GET /system/stats` | CPU/MEM/DISK bars and sparklines (30 samples), plus the AMBIENT temperature row, which is hidden when `ambient_temp_c` is missing |
| `fetchPerfStream()` app.js:5209 | Every 1s, but only while the Performance panel is expanded (`perfStartPolling`, app.js:5385). The open state lives in localStorage `kj-perf-open`. | `GET /perf/stream` | Perf panel |
| `waitForRestart(max)` app.js:2361 | After a restart, update, reboot or yt-dlp upgrade: waits 2s, then polls every 1s with a 2s timeout until it gets an OK | `GET /status` | none |
| `syncMasters` → `poll` app.js:483 | First poll after 1.5s, then every 2s while `running` | `GET /master-sync/status` | Button label and log line, then `refreshMediaData()` |
| Page-load one-shots | DOMContentLoaded (app.js:4715-4792) and top-level code | `GET /media`, `GET /filler_music`, `GET /overlays`, `GET /catalog/stats`, `GET /system/autodeploy`, `GET /system/sleep-mode`, `GET /youtube/status` (app.js:2375, health dot) | none |

Other agents cover the other intervals: `fetchRotation` every 10s (app.js:4828), `fetchSyncStatus` every 30s (app.js:8123), and SingRequests `fetchPending` every 5s (app.js:9814).

#### 3B.2 `GET /status` response shape (`get_status`, routes.py:1464). Record this in fixtures.

```text
state                 "playing"|"paused"|"stopped"   ← coord.get_karaoke_status() → player.get_status()
current_playing       display_name from media.index[cpp], else basename(cpp), else null
current_playing_path  coord.current_playing_path (=player.current_path; the *display* path, e.g. the .zip not the extracted .cdg)
current_filler_track  filler.current_track (filename in filler_music_dir) or null
time, length          int seconds (mpv: time-pos/duration; CDG uses probed mp3 length if longer)
audio_device          coord.audio_device (e.g. "hdmiout" or "hw:0,3")
vlc_enabled           coord.enabled
audio_error           bool (set on mpv loadfile/audio-add failure; cleared by /fix_audio, play, stop)
download_queue        app.download_queue['items'] (list of {id,url?,title?,source,source_detail?,status,progress?,error,file_path?,rotation_entry_id?,…})
karaoke_volume, filler_volume   int 0-256 (VLC scale)
browser_mode          {running, pid, url, enabled}   (Chromium.get_status + module _browser_mode)
rotation_downloads    { "<rotation_entry_id>": {status, progress, file_path, source, source_detail} }
pitch_semitones       int -6..+6
original_vocals_volume int 0-256 (mpv guide level; resets to 0 each song)
has_vocals_track      bool = mpv AND supports_pitch AND _resolve_vocals_guide(cpp) is not None
renderer              {mode:"mpv"|"vlc", supports_pitch, supports_cdg, available_modes:[...]}
player_alert          latest un-acked crash event or null: {id, ts, engine, song, file_path, outcome:"restarting"|"escalated"}
player_health_events  list (≤30) of the same event dicts, oldest first
simple_mode           bool (sing_store.is_simple_mode(); False on error)
```

Things to know when recording or replaying:

- `GET /status` is **not read-only**. It changes the module-level `_browser_mode` global to match whether Chromium is actually running (routes.py:1484-1488).
- The VLC renderer reports `playing` in two cases where VLC itself says otherwise: during a 5s window after a play or seek while VLC reports `stopped`, and during HTTP blips while a song is loaded (vlc.py:529-559).
- `time` and `ts` differ on every call, so normalize them in fixtures.

How `updateStatus()` renders the response:

- It toggles `body.simple-mode` (`applySimpleMode`, app.js:3369).
- It shows the `#audio-warning` banner when `audio_error` is set, and the crash banner via `updatePlayerCrashBanner(player_alert)` (app.js:531).
- It syncs `#filler-selector`, sets the `currentPlayingPath` global (used to protect the playing file from deletion and to gate buttons), and moves the seek slider unless the KJ is dragging it (`isSeeking`).
- `updateNowPlaying` (app.js:1520) fills in the title, filename, file-type pill, renderer badge, time and pitch, and hides the pitch group when `!renderer.supports_pitch`.
- `updatePlaybackButtons` (app.js:1601) enables Restart, Stop, Try Another and the Fade buttons only when `currentPlayingPath` is set (not when `state` is set).
- It moves the volume and vocals sliders unless they have focus, and shows the vocals row only when `has_vocals_track`.
- `handleDownloadQueue` (app.js:370) runs next.
- If `rotation_downloads` differs from the previous poll, it calls `fetchRotation()` and re-renders.
- Finally it calls `updateBrowserModeUI(browser_mode)` (app.js:9050).

---

#### 3B.3 Header actions and Simple Mode

These are the `.header-actions` clusters that belong to this section. The rotation header (undo, redo, Restore, Paths, Refresh, Auto-Order, + Add, New Rotation) and the Requests Settings button are covered in 3A.

| Capability (trigger) | app.js fn (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| **Simple / Advanced toggle**: segmented buttons `#mode-seg-simple` / `#mode-seg-advanced` in the Playback Controls header (index.html:31-36) | `setKjMode(mode)` 3313 (applies to the UI immediately; a sequence counter guards against races) → `applyStandinOverlays()` 3348 → `loadOverlays()` | `POST /rotation/requests/config` with body `{simple_mode:true, enabled:true, accept_make_requests:false}` or `{simple_mode:false}`. When switching to Simple it then sends `GET /overlays` and a `PUT /overlays/<id>` with `{enabled:true}` for each disabled overlay named "scan to sing", "rotation ticker" or "rotation list" | `update_sing_config` routes.py:6254 (the simple_mode branch is at :6320) | sing_store meta `SIMPLE_MODE_KEY` (`set_simple_mode`, sing_store.py:432). Switching to Simple also turns public requests on and turns "make it" requests off. The stand-in overlays get enabled in `data/overlays.json`. Switching back to Advanced does **not** undo these. | CSS `body.simple-mode` (style.css:4276-4305) hides KN, YT, Divebar, Download, Library, Browser Mode, Song Stats, Overlays, the whole System section, the rotation New/Restore/Paths/Undo/Redo buttons, and the VNC connection controls. The next `/status` poll re-syncs the mode from the server. |
| Library header: filter cycle, Needs-review, **Rescan Media**, **Sync Masters**, **Rebuild catalog** (index.html:512-517) | `cycleMediaFilter` / `toggleReviewFilter` (localStorage only, no HTTP), `rescanMedia` 438, `syncMasters` 462, `rebuildCatalog` 3475 | see 3B.9 | | | |
| Overlays header: Hide, Wallpaper, Backup, Restore, Scan to Sing, + Add (index.html:151-158) | see 3B.7 | | | | |
| Song Stats header: click the header (or press Enter/Space on it) to collapse or expand; **Refresh** button (index.html:533-539) | `toggleSongStats` 4372, `refreshSongStats` 4337 | see 3B.12 | | localStorage `kjbox.songStatsCollapsed` | |
| Screen Preview header: Hide / 200px / 400px / Fit / Max (index.html:357-362) | `hideVncPreview`, `setVncSize` | none (noVNC talks to websockify directly, not Flask) | | localStorage `kj-vnc-size` / `kj-vnc-hidden` | |
| KN header: **Prefs**; Divebar header: **Status**; Upload/Download header: **Settings** (YouTube) | `toggleKNPrefs` 3854, `openDbStatusModal`, `openYtModal` 2172 | see 3B.8 | | | |

---

#### 3B.4 Playback controls (Playback Controls panel, index.html:27-100)

| Capability (trigger) | app.js fn (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| **Play** a file. Triggers: Library row "▶ Play" (app.js:1193-1202), the KN-panel local version's "Play" (`knPlayBtn` 3664), and the rotation row ▶ (`playAndAdvanceRotation` 7515, which also sends `/rotation/status` and may send `/rotation/sms/auto-send`, both covered in 3A) | `playMedia(filePath, entryId?)` 411 | `POST /play` `{file_path, entry_id?}` | `handle_play` routes.py:976 | See the ordered steps after this table. `player.current_path` = the display path, `player.active=True`, `_pitch_semitones=0`, `_vocals_volume=0`, `audio_error=False`. `overlays.json` `karaoke_playing=true`. The state file `/tmp/kj-mpv-state.json` (or `kj-vlc-state.json`) is written. | Filler: `send("")`; if the filler isn't stopped, `fade_out()` (20-step VLC `volume&val=` ramp over 1.5s, then `pl_stop`) and `ensure_stopped()` (polls up to 5×). **mpv IPC**: `set_property lavfi-complex ""` (only if a guide was active), `loadfile <path> replace`, then either `audio-add <mp3> select` (CDG; on failure it sends `stop` and sets `audio_error`) or `audio-add <guide> auto` followed by `set_property lavfi-complex "[aidG]volume=g[gv];[aidI][gv]amix=inputs=2:normalize=0[ao]"`, then `set_property volume <mpv-scaled>`. A thread verifies that playback progresses. **VLC HTTP**: `pl_empty`, `in_enqueue&input=`, `volume&val=`, `pl_play`. Chromium is killed if it is running. A **stats** row is written (see step 5). Returns 409 when asleep, 400 for a bad path, a bare .cdg with no audio sibling, or a zip without an mp3, and 503 when the player is disabled. |
| **Pause / Resume** (`#btn-pause`) | `controlPlayback('pause_resume')` 590 | `POST /control` `{action:"pause_resume"}` | `handle_control` routes.py:1087 | `player.active` is set to the opposite of the paused result, and `overlays.json` `karaoke_playing` follows. Filler is **not** started while paused. | mpv: `get_property pause` then `set_property pause <!cur>`. VLC: `pl_pause`, then a status read after 0.5s. If mpv can't be reached, the result is `None`, which the handler treats as "resumed". |
| **Restart** (`#btn-restart`) | `controlPlayback('restart')` | `POST /control` `{action:"restart"}` | same handler | none | `seek_karaoke(0)`: mpv `seek 0 absolute`, VLC `seek&val=0` |
| **Stop** (`#btn-stop`) | `controlPlayback('stop')` | `POST /control` `{action:"stop"}` | same handler | `player.active=False`, `current_path=None`, vocals and audio-length state reset, state file written, `karaoke_playing=false` | mpv: `stop` (under `_play_lock`) and then `ensure_released()` (another `stop`). VLC: `pl_stop` and `pl_empty`. Then `filler.fade_in()`: `volume&val=0`, `pl_play`, a 20-step ramp up to the filler volume, and after 4s an "aout dead" check that relaunches the filler if needed. |
| **Fade Out** presets 3s / 6s / 10s / 20s (`.fade-preset`, index.html:69-72) | `fadeOut(seconds, btn)` 772. The client-side `_fadingOut` lock is released after `seconds*1000+800` ms. | `POST /control` `{action:"fadeout", duration_s}` | same handler. `duration_s` is clamped to 0.5–60, with a default of 3. | After the fade, the same end state as Stop. `karaoke_volume` goes back to its saved value. | `coord.fadeout` (playback.py:467) starts a thread that calls `player.fadeout`, which ramps the volume over `fade_steps(d)` steps (mpv `set_property volume`, VLC `volume&val=`) and then calls `stop()`. mpv restores `karaoke_volume` internally; VLC also sends `volume&val=<saved>`. After `d+0.5` s the coordinator calls `ensure_released()`, sets `karaoke_playing=false`, and calls `filler.fade_in()`. |
| Unknown `action` | n/a | `POST /control` | same handler | nothing | Returns **200** `{"success":true}`. Only a missing action gets a 400. |
| **Seek** slider `#seek-slider`: drag it (oninput updates the label, mousedown/touchstart set `isSeeking`), release to seek (onchange) | `seekVideo(pos)` 841, `updateSeekLabel` 834 | `POST /seek` `{time: pos*length}` | `handle_seek` routes.py:1073, which truncates the time to `int` | `player.last_seek_time` | mpv `seek <s> absolute`. VLC `seek&val=<s>`. There is no sleep gate and no enabled check. |
| **Karaoke volume** slider `#karaoke-volume`, 0–256 | `updateKaraokeVolume` 811 → `debouncedSetVolume` 806 (150ms) → `setVolume` 826 | `POST /volume` `{target:"karaoke", level}` | `handle_volume` routes.py:1132 | `player.karaoke_volume`. A 2s-debounced write of `karaoke_volume` and `filler_volume` to config.json (`_debounced_save_volumes`, routes.py:520) | mpv `set_property volume` (VLC→mpv scaling). VLC `volume&val=` |
| **Filler volume** slider `#filler-volume` | `updateFillerVolume` 816 → same path | `POST /volume` `{target:"filler", level}` | same handler | `coord.filler_volume` and config.json (debounced) | Filler HTTP `volume&val=<level>` |
| **Original-vocals guide** slider `#vocals-volume`. It is shown only when `/status.has_vocals_track` is true. | `updateVocalsVolume` 821 → same path | `POST /volume` `{target:"vocals", level}` | same handler | mpv `_vocals_volume` (**not** persisted, and reset to 0 on each play). The debounced config write still runs. | mpv rebuilds `lavfi-complex` with the new guide gain (`_apply_vocals_mix`, mpv_manager.py:705), resolving the track IDs live from `track-list`. On VLC, or when audio processing is off, nothing happens. An invalid target returns 400. |
| **Pitch** −, reset (click the number), + (`#np-pitch-*`, index.html:50-52; hidden if `!supports_pitch`) | `changePitch(delta)` 738 (clamped ±6) | `POST /pitch` `{semitones}` | `handle_pitch` routes.py:1165. Returns `{pitch_semitones}`. | mpv `_pitch_semitones` (reset on each play) | mpv `af-command rb set-pitch <2^(n/12)>`, only while `active` and audio processing is on. VLC just logs that the call was ignored. |
| **Try Another**: swap to another local version mid-song (`#btn-try-another`) | `tryAnother()` 607 → modal → `swapToVersion(entryId, path)` 717 | `GET /playback/alternates`, then `POST /rotation/link` `{id, file_path}` (3A), then `POST /play` `{file_path, entry_id}` | `playback_alternates` routes.py:5292 (a local-only `unified_search` on the current entry's `song_artist`, excluding the current path) | The rotation link (3A), then the same as Play | Pressing Escape or clicking the backdrop closes the modal. |
| **Now-playing file-type pill**: click it for technical details | `openMediaInfoModal(path, name)` 1432 | `POST /media/info` `{file_path}` | `media_info` routes.py:1263 | none | ffprobe (`mediainfo.probe_media_info`) |
| **Fix Audio**: button in the `#audio-warning` banner (index.html:16) | `fixAudio()` 520 | `POST /fix_audio` | `fix_audio` routes.py:1546 | `audio_error=False`. The player object is rebuilt, and `audio_device` and `on_karaoke_end` are kept. | `coord.restart_instances()` runs **synchronously**: player `shutdown` (mpv IPC `quit`), filler `shutdown`, 1s sleep, player `launch`, filler `launch(loop)`, 3s sleep, `filler.fade_in()`, and a new monitor thread. This interrupts a song that is playing. |
| **Player-crash banner** (`#player-crash-banner`), driven by `/status.player_alert`. Buttons: **Retry song**, **Switch engine**, **Dismiss** | `retryCrashedSong` 576 / `openAvModal` 1754 / `dismissPlayerCrash` 572 → `ackPlayerCrash` 565 | Retry: `POST /play` `{file_path}` (no entry_id), then ack on success. Dismiss: `POST /player-crash/ack` `{id}` | `player_crash_ack` routes.py:1557 → `coord.ack_player_alerts` (playback.py:169) | `_last_acked_id = max(prev, id)` | none. Crash events come from `_handle_engine_died` (playback.py:99): an auto `restart_instances()` on a daemon thread, or `outcome:"escalated"` with no restart once there are 3 or more crashes in 60s (`CRASH_GUARD_*`, playback.py:45-46). |
| **Natural end of song** (no UI action) | n/a | n/a | the mpv/VLC monitor thread → `_handle_karaoke_ended` (mpv_manager.py:1034) → `on_karaoke_end` (`_make_on_karaoke_end`, app.py:171) | `active=False`, `current_path=None`, pitch reset, state file written | `ensure_released()`, `karaoke_playing=false`, `filler.fade_in()` |

**`POST /play` steps, in order** (`handle_play`, routes.py:976):

1. Check the sleep gate.
2. Validate the path, either through `media.validate_path` or as inside `external_media_mount`, trying NFC and NFD forms.
3. For a `.zip`, `zip_playback.extract_and_get_mp3`. On mpv, play the `.cdg` with the mp3 as `audio_file`. On VLC, play the mp3.
4. For a bare `.cdg`, a same-stem audio sibling is required. On mpv, play the cdg with the sibling as `audio_file`. On VLC, play the sibling.
5. If Chromium is running or `_browser_mode` is set, kill Chromium and clear `_browser_mode`.
6. On mpv with `supports_pitch`, resolve the vocals guide (`_resolve_vocals_guide`, routes.py:948), looking for `NOMAD-####` in `vocals_guide_dir` or the `NOMAD-vocals-padded` sibling folder.
7. Start `coord.play_video` on a background thread.
8. Call `_record_play_stat(validated, entry_id)` (routes.py:86). This does `stats.record_play` → `INSERT OR IGNORE play_events` (stats_store.py:115) with media_id, song_key, singer (from the rotation entry), artist, title and night_date. Deduplication: one row per `entry_id` via a partial UNIQUE index, or a 120s same-media window when there is no entry_id. SSD files with no media_library row are hashed off-thread (`_record_library_play`).
9. Return `{"success":true}` straight away, before playback has actually started.

`/play` does **not** change the rotation status. The UI sends `/rotation/status` separately.

---

#### 3B.5 Renderer swap (mpv ⇄ VLC) and AV Output modal

The modal opens from the System "AV Output" button (index.html:174) or from the crash banner's "Switch engine". It closes with × (`closeAvModal`), a backdrop click, or Escape (app.js:4740-4747).

| Capability (trigger) | app.js fn (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| Open or refresh the modal (**Refresh** button, index.html:932). It also auto-refreshes after each action. | `openAvModal` 1754 → `avRefresh` 1763 → `renderAvModal` | `GET /av/status` and `GET /renderer`, in parallel | `av_status` routes.py:2644, `get_renderer` routes.py:1182 | none | `xrandr` (DISPLAY=:0), EDID from sysfs, ELD, `amixer`/`aplay` probes, the PipeWire profile via pactl, and `/etc/asound.conf`. The response is `{video, audio{…, browser_audio{setting,resolved_profile,available_profiles}}, health{video_ok,audio_ok,asound_matches_active_jack,pipewire_profile_ok,iec958_ok}, audio_monitor}` |
| **Renderer radio** mpv / VLC (index.html:854-858) | `avSetRenderer(mode)` 1810 | `POST /renderer` `{mode}` | `set_renderer` routes.py:1188 → `coord.switch_renderer` (playback.py:200) | `coord.render_mode`, persisted as config `render_mode`. A new player object is built with the same `audio_device` and `on_karaoke_end`. | Old player `shutdown`, new player `launch` (mpv with `--af=@rb:rubberband` when audio processing is on), and a new monitor thread. Filler is untouched. Returns **409** `karaoke_active` while `player.active` (the UI shows an alert and reverts the radio) and 400 for an invalid mode. |
| **Display resolution** select | `avSetResolution` 2073 (then refreshes after 1s) | `POST /display/resolution` `{resolution}` | `set_display_resolution` routes.py:2390 | none (not persisted) | `xrandr --output <o> --mode <res>`. Returns 503 without xrandr and 400 for an unknown mode. `GET /display/resolution` (routes.py:2377) exists, but the UI never calls it; the modal reads modes from `/av/status.video`. |
| **HDMI PCM (hdmiout alias)** select | `avSwitchHdmiPcm` 2083 (refreshes after 3.5s) | `POST /audio/switch-hdmi` `{device:"hw:X,Y"}` | `switch_hdmi_audio` routes.py:1760 | `/etc/asound.conf` is rewritten (via `sudo tee`). `coord.audio_device='hdmiout'` (runtime only). | `restart_instances()` on a thread. This interrupts playback. |
| **Playback audio device** select | `avSetVlcDevice` 2093 (refreshes after 3.5s) | `POST /av/vlc-device` `{device}` (either `hw:X,Y` or a named `audio_devices` key) | `av_set_vlc_device` routes.py:2750 | config `default_audio_device` (**persisted**) and `coord.audio_device` | `restart_instances()` on a thread |
| **Browser audio** select ("same", or a PipeWire profile) | `avSetBrowserAudio` 2063 | `POST /av/browser-audio` `{device}` | `av_set_browser_audio` routes.py:2687 | config `browser_audio_device` (persisted; a short key is mapped to the full profile string) | none until the next browser-mode launch |
| **Reset All to Known-Good State** | `avReset` 2103 (refreshes after 4.5s) | `POST /av/reset` | `av_reset` routes.py:2710 | `coord.audio_device='hdmiout'` (not persisted) | Stops the audio monitor if it is active, runs `sudo fix-hdmi-audio.sh` (ALSA alias, IEC958, PipeWire profile, display; 30s timeout), then `restart_instances()` on a thread |
| **Audio monitor** Start / Stop (`#av-monitor-btn`). While it runs, the modal shows a `ffplay …/audio-monitor/stream` hint. | `toggleAudioMonitor(start)` 2154, `renderAvMonitorSection` 2119 | `POST /audio-monitor/start` / `POST /audio-monitor/stop` | `audio_monitor_start` routes.py:2790 / `audio_monitor_stop` :2800, both running the work on a thread | `AudioMonitor.active`. `coord.audio_backend` goes to `pipewire` on start and back to `alsa` on stop. | Start: `set_audio_backend('pipewire')`, `restart_instances()`, `pactl set-card-profile <HDMI>`, then a `parec \| ffmpeg` MP3 pipeline. Stop reverses it. |
| Listen to the monitor (external `ffplay`, not in the UI) | n/a | `GET /audio-monitor/stream` | `audio_monitor_stream` routes.py:2810 | `_client_connected` | Chunked `audio/mpeg`. Returns 404 when not active and 409 if a second client connects. |
| (not used by the UI) monitor status | n/a | `GET /audio-monitor/status` | routes.py:2784 | none | `{active, stream_url?}` |
| (not used by the UI) legacy device switch and HDMI scan | n/a | `GET`/`POST /audio_device`, `POST /audio/scan` | routes.py:1566/1678/1698 | POST `/audio_device`: `coord.audio_device` (not persisted) | POST: `restart_instances()` on a thread. `/audio/scan` runs `aplay -l` and `amixer -c 0 contents` and reads `/etc/asound.conf` (no tests). |

---

#### 3B.6 Filler music

| Capability (trigger) | app.js fn (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| Populate the **Filler Music** select (System → Media & Output, index.html:171) at page load | `updateFillerMusicList` 857 | `GET /filler_music` | `list_filler_music` routes.py:1394 | none | Lists the `.mp3/.wav/.ogg/.flac` files in `filler_music_dir`, or `[]` |
| Choose a track (select `onchange`) | `setFillerMusic(name)` 849 | `POST /filler_music` `{track_name}` | `set_filler_music` routes.py:1410 | `filler.current_track`, saved to `/tmp/kj-filler-state.json` | Filler HTTP: `pl_stop`, `pl_empty`, `in_enqueue&input=<path>`. If karaoke is **not** active it then sends `pl_play`, waits 0.5s, reads status, seeks to a random `seek&val=` position, and sends `volume&val=<filler_volume>`. If karaoke is active, the track is only queued. Returns 409 when asleep and 404 for an unknown track. |

`/status.current_filler_track` also keeps the select and the now-playing "filler" row (app.js:1536-1546) in sync.

---

#### 3B.7 Overlays, ticker and wallpaper

Overlays are saved in `data/overlays.json` by `OverlayManager` (overlay.py:48) using an atomic temp-file-plus-rename write. They are drawn by a **separate process**, `desktop/overlay_engine.py` (the `overlay-display.service`), which reads that file. It also reads `karaoke_playing` (flipped by play, stop and fade) and `video_top_margin_px`. Types are `ticker`, `static_text`, `image`, `countdown`, `qr_code` and `rotation_list` (overlay.py:16). The "ticker" is the `ticker` overlay type, whose `config.source` can be static or rotation-fed.

| Capability (trigger) | app.js fn (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| List overlays (on page load and after every mutation) | `loadOverlays` 2692 → `renderOverlayList` 2702 | `GET /overlays` | `list_overlays` routes.py:1803 | none | none |
| **+ Add** or **Edit** → the modal (type, source, position and max-width fields) → **Save** | `showOverlayForm` 2814 / `editOverlay` 2871 → `saveOverlay` 2939 (uses `buildOverlayConfig`) | Create: `POST /overlays` `{type,name,enabled,show_over_video,config}` → 201. Edit: `PUT /overlays/<id>` with the same body. | `create_overlay` routes.py:1822 (a bad type returns 400), `update_overlay` :1875 (404 if missing) | overlays.json | The overlay engine picks up the change |
| **Enable toggle** on a row | `toggleOverlayEnabled(id)` 2994 | `POST /overlays/<id>/toggle` | `toggle_overlay` routes.py:1897 | `enabled` flips | same |
| **Delete** (×, with a `confirm()`) | `deleteOverlay(id,name)` 2981 | `DELETE /overlays/<id>` | `delete_overlay` routes.py:1889 | overlays.json | same |
| **Scan to Sing** (header) | `addScanToSingQR` 2628 | `POST /overlays/presets/scan-to-sing` → 201 | `create_overlay_preset` routes.py:1837 | A new `qr_code` overlay (top-right, show_over_video) | `sync_event_url_overlays(mgr, public sing URL)` fills in `config.url` from `sing_store` token. The `rotation-list` preset exists too, but the UI has no button for it. |
| **Backup** (header) | `backupOverlays` 2642 | `GET /overlays` | same as list | none | The browser downloads `overlays-backup.json` |
| **Restore** (header → file picker → `confirm()`) | `restoreOverlays` 2659 → `handleOverlayRestore` 2663 | `POST /overlays/import` (a JSON array) | `import_overlays` routes.py:1809 | All overlays are **replaced** | none |
| **Hide / Show** the overlays list (header) | `toggleOverlaysPanel` 5421 | none | | localStorage | |
| (not used by the UI) get one overlay, toggle show-over-video | n/a | `GET /overlays/<id>`, `POST /overlays/<id>/toggle-video` | routes.py:1866, :1906 | `show_over_video` flips | none |
| **Wallpaper** (header → modal → file input) | `showWallpaperModal` 3001 (`<img src=/wallpaper?t=>`), `uploadWallpaper` 3014 | `GET /wallpaper`, and `POST /wallpaper` (multipart `file`, .jpg/.jpeg/.png/.webp) | `get_wallpaper` routes.py:1917, `upload_wallpaper` :1934 | Writes `~/kjdata/wallpaper.jpg`, `~/kjdata/rotation-bg.png` and `desktop/rotation-bg.png` (a 1920×1080 PIL resize) | `_set_xfce_wallpaper` runs `sudo -u nomad xfconf-query … last-image` for every monitor, best-effort. **Neither wallpaper route has tests.** |

---

#### 3B.8 Settings: YouTube, KaraokeNerds priorities, catalog

| Capability (trigger) | app.js fn (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| YouTube health dot (at page load) and the **Settings** modal (Upload/Download header), with its **Refresh** | `updateYtHealthDot` 2269, `openYtModal` 2172 → `ytSettingsRefresh` 2181 | `GET /youtube/status` | `youtube_status` routes.py:2137 → `youtube_health.get_youtube_status` | none | Probes yt-dlp (version and latest), EJS, Deno and the cookies file |
| **Upload Cookies** (paste Netscape-format cookies into the textarea) | `uploadYtCookies` 2286 | `POST /youtube/cookies` `{content}` | `youtube_upload_cookies` routes.py:2145 | Writes the `youtube_cookies_file` | The format is validated first. Returns 500 if the cookies path isn't configured. |
| **Delete Cookies** (with a `confirm()`) | `deleteYtCookies` 2309 | `DELETE /youtube/cookies` | routes.py:2170 | Removes the cookies file | none |
| **Update** yt-dlp (button shown only when the installed version is outdated, with a `confirm()`) | `upgradeYtdlp(btn)` 2325 → `waitForRestart` | `POST /youtube/upgrade-ytdlp` | routes.py:2186 | The installed yt-dlp package | `python -m pip install --upgrade yt-dlp`, then 1s later `sudo systemctl restart kj-controller` |
| **KN Prefs** panel (KN header "Prefs") | `toggleKNPrefs` 3854 → `fetchKnPrefs` 3827 | `GET /karaoke-nerds/config` | `kn_get_config` routes.py:2048 | none | Returns `{priority_community, priority_commercial, aliases}`, falling back to `version_priority` defaults |
| **Save** brand priorities | `saveKNPrefs` 3868 (reruns the KN search if results are showing) | `POST /karaoke-nerds/config` `{priority_community:[…], priority_commercial:[…]}` | `kn_set_config` routes.py:2077 | config `kn_priority_community` / `kn_priority_commercial` (persisted and live) | Unknown brand codes return 400 |
| **Reset to defaults** (with a `confirm()`) | `resetKNPrefs` 3895 | `POST` with empty lists, then `GET`, then `POST` with the defaults | same handlers | The config is rewritten with the defaults | none |
| Catalog availability, shown in the search placeholder (at page load) | `checkCatalogAvailability` 2600 | `GET /catalog/stats` | `catalog_stats` routes.py:1635 | none | `{available, total, by_format}` |
| **Rebuild catalog** (Library header) | `rebuildCatalog` 3475 | `POST /catalog/build` `{}` | `catalog_build` routes.py:1646 | The external catalog SQLite is rebuilt from `external_file_list` | Reads the file list and rewrites the Mac `/Volumes/X/` prefix to the Pi mount. Returns 400 or 404 when the list is missing. |
| (not used by the UI) catalog-mirror reload, called by `scripts/sync_catalogs.py` after the DB swap | n/a | `POST /catalog-mirror/reload` | routes.py:1311 | Closes the mirror connection so the next query reopens it | 404 if not configured. The mirror stats show up in `/system/stats.catalog_mirror`. |

---

#### 3B.9 Library / media maintenance (not linking)

| Capability (trigger) | app.js fn (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| Load the library list (page load, after a rescan, delete or master sync) | `refreshMediaData` 1386 / `updateMediaList` 1391 | `GET /media` | `list_media` routes.py:1208 → `media.list_items()` | none | Sorted newest (mtime) first |
| **Rescan Media** | `rescanMedia` 438 (reruns the current search) | `POST /rescan` `{}` | `handle_rescan` routes.py:1325 | Reloads config.json into `current_app.kj_config`, `media.config` and `coord.config`. The media index is rebuilt and saved. | Walks every `media_folders` directory. Returns `{count}`. |
| **Sync Masters** | `syncMasters` 462 + the `/master-sync/status` poll | `POST /master-sync/run` → `{started,running}`, then `GET /master-sync/status` → `{running,result}` | `master_sync_run` routes.py:1355, `master_sync_status` :1386 | `app._master_sync_state` | A daemon thread runs `scripts.sync_masters.run_master_sync_now` (a gcloud rsync of the masters from GCS, sharing the timer's flock). Afterwards it POSTs `http://127.0.0.1:<app_bind_port>/rescan`. `result` is `{changed,copied,deleted,rescanned,error?}`, where error can be `busy` or `disabled`. |
| **Edit Artist/Title** (✎ on a library row; Enter saves, Escape cancels) | `editMediaMetadata` 994 → `doSave` 1020 | `POST /media/metadata` `{media_id, artist, title}` | `set_media_metadata` routes.py:1214 | The media_library row: `parse_method='manual'`, `needs_review=0`, `*_norm` recomputed | none |
| **Delete** a downloaded file (🗑, click twice via `armButtonConfirm` 3225 with a 3s countdown; refused if it's the currently playing file) | `deleteMedia` 422 | `POST /delete` `{file_path}` | `delete_media` routes.py:1283 | The file, its same-basename sidecars and its index entry are removed | Only allowed inside `download_folder` (403 otherwise) |
| **Technical details** (click a format pill on the library, catalog or now-playing row) | `openMediaInfoModal` 1432 / `closeMediaInfoModal` 1465 | `POST /media/info` `{file_path}` | `media_info` routes.py:1263 | none | ffprobe. Returns 404 for a path outside the allowed folders. |
| **Version note** (✎ or the note badge in rotation-search result rows) → modal → Save | `openNoteModal(mediaId)` 4243, `saveNote` 4261 (then reruns `doRotationSearch`) | `GET /media/note-labels`, `POST /media/note` `{media_id, note, label}` | `media_note_labels` routes.py:6654, `media_note` :6639 | `stats.db` `version_notes` upsert (stats_store.py:212) | none |

---

#### 3B.10 System panel (System section; the whole section is hidden in Simple Mode)

| Capability (trigger) | app.js fn (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| **Update (Safe)** (with a `confirm()`) | `updateApp` 3496 → `waitForRestart` → `location.reload()` | `POST /system/update` | `system_update` routes.py:2837 | The repo working tree | `git pull origin main` runs synchronously in the repo root (500 on failure), then 1s later `sudo systemctl restart kj-controller`. mpv, VLC and the filler keep running, and `try_reconnect` picks them up on the next start. |
| **Auto-Deploy** switch | `fetchAutoDeployStatus` 3272 (at page load), `toggleAutoDeploy` 3280 | `GET /system/autodeploy`, `POST /system/autodeploy` `{active}` | `autodeploy_status` routes.py:2870, `autodeploy_toggle` :2881 | The systemd unit `kj-autodeploy` enabled/started state | `sudo systemctl enable --now` or `disable --now kj-autodeploy`, then `systemctl is-active` |
| **Sleep Mode** switch (entering asks for a `confirm()`) | `fetchSleepModeStatus` 3385 (at page load), `toggleSleepMode` 3422, `updateSleepModeUI` 3396 | `GET /system/sleep-mode` → `{active, entering, exiting, state?}`, `POST /system/sleep-mode` `{active}` → `{active, message, errors[]}` | `sleep_mode_status` routes.py:2903, `sleep_mode_toggle` :2910 → `SleepManager.enter_sleep`/`exit_sleep` (sleep_mode.py:48/117) | Entering: **public requests turned off** (`sing_store.set_enabled(False)`; waking does not turn them back on), the `SLEEP_FLAG` file created, and the pre-sleep state saved as JSON | Entering: filler `fade_out`, `ensure_filler_stopped`, `ensure_karaoke_released`, the player and filler processes terminated, `pkill -f websockify`, then `sleep-enter.sh` (`systemctl stop` for services, x11vnc and kj-autodeploy; `umount` the SSD and power down its USB port; `powerprofilesctl set power-saver`). Waking runs `sleep-exit.sh` and relaunches the players. While asleep, `/play`, `POST /filler_music` and `/browser-mode/enable` and `/navigate` return 409. |
| **Restart App** (with a `confirm()`) | `restartApp` 3483 | `POST /system/restart-app` | `restart_app` routes.py:2823 | none | 1s later, `sudo systemctl restart kj-controller` |
| **Reboot** / **Shut Down** (click twice, with a 3s countdown) | `dangerousAction(btn, action, …)` 3192 → `executeDangerousAction` 3255 (reboot waits up to 60s for the box to come back) | `POST /system/reboot` / `POST /system/shutdown` | routes.py:2935 / :2949 | none | 1s later, `sudo reboot` or `sudo shutdown -h now` |
| **Stats** tiles (CPU, MEM, DISK, AMBIENT) | `fetchSystemStats` 4844 (every 5s) | `GET /system/stats` | `system_stats` routes.py:2998 | none | psutil. Returns `{cpu_percent, mem_percent, mem_used_gb, mem_total_gb, disk_percent, disk_used_gb, disk_total_gb, ambient_temp_c?, catalog_mirror?}`, or 501 without psutil |
| **Performance** panel: click the header, or press Enter/Space on it, to expand | `togglePerfPanel` 5407 → `perfSetOpen` 5396 | `GET /perf/stream` every 1s while open, and `GET /perf/record/list` on open | `perf_stream` routes.py:3039 → `perf_sampler.snapshot()` | localStorage `kj-perf-open` | 501 if there is no sampler |
| Perf A/B toggles **Overlay** / **Compositor** / **GPU max-clock** | `perfToggle(control, btn)` 5230 | `POST /perf/toggle/<overlay\|compositor\|gpu-clock>` `{on}` | `perf_toggle` routes.py:3051 → `perf_sampler.apply_toggle` (perf_sampler.py:312) | System state | overlay: `sudo -n systemctl start/stop overlay-display`. compositor: `xfconf-query -c xfwm4 -p /general/use_compositing -s true/false`. gpu-clock: `sudo -n set-gpu-clock.sh pin/unpin`. An unknown control returns 400. |
| Perf **VNC preview** toggle and **auto-pause VNC during playback** checkbox | `perfToggleVnc` 5255, `perfSetAutoPause` 5269, `perfMaybeAutoPauseVnc` 5186 (called from every `/status` poll) | none (client-side noVNC) | | localStorage `kj-vnc-autopause` (on by default) | Disconnects VNC while `state==='playing'` and reconnects afterwards |
| Perf **● Record** / Stop, the saved-recordings list, summary and download | `perfToggleRecord` 5280, `perfRefreshRecordings` 5296, `perfShowSummary` 5357; download is an `<a href>` | `POST /perf/record/start` `{label}`, `POST /perf/record/stop`, `GET /perf/record/list`, `GET /perf/record/<id>/summary`, `GET /perf/record/<id>/download` | routes.py:3074/3084/3093/3102/3114 → `PerfRecorder` | Session files `<dir>/<YYYYmmdd-HHMMSS>-<label>.jsonl`, one sample appended per second | Download returns ndjson as an attachment |

---

#### 3B.11 Browser Mode and Preview

| Capability (trigger) | app.js fn (line) | HTTP | Backend handler | State mutated | Side effects |
|---|---|---|---|---|---|
| **Enable Browser Mode** (`#browser-mode-toggle` when inactive; the URL defaults to https://youtube.com) | `toggleBrowserMode` 8994 → `enableBrowserMode(url?)` 9002 | `POST /browser-mode/enable` `{url}` | `browser_mode_enable` routes.py:5552 | `_browser_mode=True`, config `browser_mode_url` persisted, `coord.karaoke_active=False`, `current_playing_path=None` | Filler `fade_out` and `ensure_filler_stopped`, then `ensure_karaoke_released`. The audio device is `browser_audio_device`, or the VLC device when that is "same". `chromium.launch(url, audio_device)` runs pactl `set-card-profile` and starts Chromium with `--remote-debugging-port`. Returns 409 when asleep, 503 if there is no Chromium manager, and 500 if the launch fails. |
| **Navigate**: the Go button, or Enter in `#browser-mode-url` while active | `browserModeNavigate` 9035 | `POST /browser-mode/navigate` `{url}` | `browser_mode_navigate` routes.py:5596 | config `browser_mode_url` | Navigates over CDP (`chromium.navigate`) and falls back to a full relaunch. Returns 409 if browser mode isn't active. **No tests.** |
| **Disable Browser Mode** | `disableBrowserMode` 9020 | `POST /browser-mode/disable` | `browser_mode_disable` routes.py:5632 | `_browser_mode=False` | `chromium.kill()` (which resets PipeWire), then `restart_instances()` **synchronously**, unless the box is asleep |
| Browser mode is also turned off automatically by `/play` (3B.4 step 5), and `/status` re-syncs the flag | n/a | | | | |
| **Preview** (pink ▶ on library rows at app.js:1140-1151 and on rotation rows at app.js:6009; `previewButtonHtml()` buttons in KN, local-match, divebar and YouTube result rows at app.js:3802, 8740, 8794, 8829). It plays in the browser only and never touches the device player. | `openPreview(descriptor)` in preview.js:70 (also `openPreviewEnc`) | `POST /preview/resolve`. The descriptor is `{source:"local",file_path}`, `{source:"divebar",file_id,format}` or `{source:"youtube",youtube_url}`, plus optional `{title, prefer_transcode, link_idx, link_label}`. | `preview_resolve` routes.py:6549 → `PreviewService.resolve` (preview.py:150) | An in-memory token (`_tokens`, with a TTL). The preview cache (default 8 GB LRU) holds the CDG extraction, the HLS transcode and divebar blob downloads. **stats**: `_record_preview_stat` → `preview_events` insert (60s dedup; local and youtube sources only). | The mode is `native_video`, `native_audio`, `cdg`, `hls`, `youtube` or `unavailable`. Video runs through ffprobe. Anything that isn't natively playable goes through an ffmpeg HLS transcode (`TranscodeBusy` returns `unavailable`). Divebar downloads the file from a signed GCS URL. |
| Preview media fetches (made by the `<video>`/`<audio>` elements, cdg.js and hls.js) | `_mountVideo`, `_mountAudio`, `_mountCdg` (fetches `/graphics` into a `CDGPlayer` canvas), `_mountHls` (native HLS or a lazily loaded hls.js), `_mountYouTube` (an iframe embed, with no server call) | `GET /preview/stream/<token>` (Range/206/416), `GET /preview/cdg/<token>/<audio\|graphics>`, `GET /preview/hls/<token>/<index.m3u8\|seg>` | routes.py:6582 / :6621 (no tests) / :6630 | none | Files are served from the token's path |
| **Close preview**: ×, a backdrop click, Escape (preview.js:273), or opening another preview | `closePreview` preview.js:260 → `_postClose` | `POST /preview/close` `{token}` | `preview_close` routes.py:6559 → `PreviewService.close` | The token is forgotten. `token=None` would drop every token and kill the transcode, but the UI always sends a token. | none |
| "Use this version" in the preview footer (only when opened from a rotation search result that has `link_idx`) | `_renderFooter` → `selectRotSearchResult` (3A linking) | see 3A | | | |

The singer UI reuses preview.js against `/sing/preview/*` through `window.__PREVIEW_URL`. That path is not part of this section.

---

#### 3B.12 Song Stats section (`#song-stats`; collapsed by default and hidden in Simple Mode)

All of these are read-only `GET`s against `StatsStore` (`stats.db`, stats_store.py). They go through `statsFetch` (app.js:4299), which returns `null` on errors, and `statsQS` (4290), which adds `since` when a range is chosen. Stats are first loaded lazily by `maybeLoadSongStats` 4354, when the section is expanded and in view (IntersectionObserver), or by Refresh.

| Capability (trigger) | app.js fn (line) | HTTP (params → response key) | Backend handler | Store method |
|---|---|---|---|---|
| Overview cards (Plays, Songs, Singers, Artists, Span, Last 30d) | `renderStatsOverview` 4311 | `GET /stats/overview?since=` → `overview` | `stats_overview` routes.py:6687 | `overview()` :275 |
| **Top Songs** view, plus the singer filter input `#statsSingerFilter` | `renderTopSongs` 4428 (cached per range and singer) | `GET /stats/top-songs?singer=&since=&limit=25` (1–100) → `songs` | `stats_top_songs` :6660 | `top_songs()` :239 |
| **Top Singers** view, with the "Most repeated" banner | `renderTopSingers` 4447 | `GET /stats/singers?limit=50` (1–200) → `singers`, and `GET /stats/most-repeated?limit=1` (1–50) → `repeated` | `stats_singers` :6674, `stats_most_repeated` :6790 | `top_singers()` :258, `most_repeated()` :409 |
| **Top Artists** view | `renderTopArtists` 4478 | `GET /stats/top-artists?limit=50` (1–100) → `artists` | `stats_top_artists` :6696 | `top_artists()` :297 |
| **Nights** view (all-time, ignores `since`) | `renderNights` 4496 | `GET /stats/nights?limit=50` (1–100) → `nights` | `stats_nights` :6765 | `busiest_nights()` :385 |
| Singer-filter autocomplete datalist | `populateSingerDatalist` 4533 | `GET /stats/singers?limit=200` | same | same |
| Drill into a song row | `toggleDrill` 4380 (`data-drill="song"`) | `GET /stats/song-history?song_key=&since=&limit=200` → `history` | `stats_song_history` :6751 | `song_history()` :367 |
| Drill into a singer row, then into one of their songs | `toggleDrill` (`singer`, then `singersong`) | `GET /stats/singer-songs?singer=&since=&limit=100` → `songs`, then `GET /stats/singer-song-history?singer=&song_key=` → `history` | `stats_singer_songs` :6723, `stats_singer_song_history` :6737 | `singer_songs()` :334, `singer_song_history()` :354 |
| Drill into an artist row | `toggleDrill` (`artist`) | `GET /stats/artist-songs?artist=&since=&limit=100` → `songs` | `stats_artist_songs` :6709 | `artist_songs()` :314 |
| Drill into a night row | `toggleDrill` (`night`) | `GET /stats/night-setlist?night_date=&limit=200` → `setlist` | `stats_night_setlist` :6777 | `night_setlist()` :397 |
| Range buttons All time / This year / Last 30 days / Custom (a date input) | `applyStatsRange` 4516 and the `#statsSince` change listener | Re-fetches the overview and the current view with `since=YYYY-MM-DD` | | |
| View switch buttons | `switchStatsView` 4326 | as per the view | | |

What writes to `stats.db`:

- `POST /play` inserts into `play_events`.
- `POST /preview/resolve` inserts into `preview_events`.
- `POST /media/note` upserts into `version_notes`.
- Per-version stats (`stats.stats_for`) also appear in rotation search results (routes.py:260, covered in 3A).

---

#### 3B.13 Keyboard shortcuts and gestures (app.js)

| Key | Where | Effect |
|---|---|---|
| `/` | global, unless the focus is in an INPUT, TEXTAREA or SELECT (app.js:4734-4738) | Focuses the library search `#catalog-search` |
| `Escape` | global (app.js:4740-4747) | Closes the Overlay modal and the AV modal |
| `Escape` | global (preview.js:273) | Closes the Preview modal, which sends `/preview/close` |
| `Escape` | Try Another modal (app.js:600) | Closes it |
| `Escape` | in `#catalog-search` (app.js:4729) | `clearSearch()` |
| `Enter` / `Escape` | inline library Artist/Title edit (app.js:1052-1055) | Save / cancel (`POST /media/metadata`) |
| `Enter` | `#browser-mode-url` while browser mode is active (index.html:569) | `browserModeNavigate()` |
| `Enter` / `Space` | the focused Song Stats header (app.js:4556) or Performance header (index.html:255) | Collapse or expand |
| `Shift` held while hovering a rotation row | global keydown/keyup (app.js:9079-9094) | Highlights the row as editable (3A) |

Other keys, listed here for completeness and covered in 3A: the singer-pill input (Tab, `,`, `&`, Backspace, Enter), the rotation song input (Enter, Escape, Tab), Escape in the SMS, singer-songs, device and merge modals, and Cmd/Ctrl+Enter to send an SMS.

Other gestures:

- Destructive buttons use two-click arming with a 3s countdown: Reboot and Shut Down (`dangerousAction` 3192) and library Delete (`armButtonConfirm` 3225).
- Sliders only send `/volume` 150ms after the drag stops. `/seek` fires when the slider is released (`onchange`).
- Fade presets are locked on the client for the length of the fade.

#### 3B.14 Coverage gaps in this section (from the §6 generated inventory)

These routes have **no tests**: `POST /audio/scan`, `GET /wallpaper`, `POST /wallpaper`, `POST /browser-mode/navigate`, `GET /preview/cdg/<token>/<part>`.

These routes are never called by the UI, only by scripts or curl: `GET/POST /audio_device`, `POST /audio/scan`, `GET /display/resolution`, `GET /overlays/<id>`, `POST /overlays/<id>/toggle-video`, `GET /audio-monitor/status`, `GET /audio-monitor/stream` (ffplay), and `POST /catalog-mirror/reload` (`sync_catalogs.py`).

Behaviours a simulation should model:

- `/play` returns before playback actually starts, because it runs on a thread.
- `/control fadeout` completes about `d+0.5`s later, on a thread.
- `/fix_audio` and `/browser-mode/disable` restart the players synchronously, which takes about 4s or more.
- An unknown `/control` action still returns 200.
- `GET /status` changes `_browser_mode`.

## 4. Singer UI capability catalogue

The public singer SPA. Backend: `kj-controller/sing.py` (blueprint `sing_bp`, prefix `/sing`, static at `/sing/static/` from `static-sing/`). Frontend: `kj-controller/templates/sing.html` (one Jinja template, three modes: `closed` / `code_entry` / SPA), `static-sing/sing.js` (ES module, 3828 lines), `static-sing/i18n.js`, `static-sing/sw.js`, `static-sing/messages/<locale>.json` (33 files). Supporting modules: `sing_store.py` (SQLite, same DB file as the rotation: `rotation_db_path`, default `~/kjdata/rotation.db`, wired at `app.py:268`), `wait_estimate.py`, `push_dispatcher.py`, `sms.py`, `sing_resolve.py`, `version_priority.py`.

Notation: `sing.js:N` is a line in `static-sing/sing.js`. `sing.py:fn:N` is handler plus decorator line. "RL-dev" is the shared singer-mutation budget `_singer_rate_limited` (`sing.py:114`): per `device_id` `sing_rate_limit_per_device` (code default 8) plus per-IP `sing_rate_limit_per_ip`, window `sing_rate_limit_window_s` (300 s). Both budgets are checked before either slot is consumed. "Tok" is the `require_token` decorator (`sing.py:226`). It takes the token from `?t=`, then the JSON `t`, then the form `t`, then the Flask session `sing_token`. It returns 403 `{"error":"not_open"}` if the token is invalid or the store is disabled, and on success stores the token in the session. "Night" is `_belongs_to_current_night` (`sing.py:189`): `req.created_at >= rotation_meta.night_started_at`, and it fails closed. "Own" means the per-request `edit_token` in the body matches `sing_requests.edit_token` (constant-time compare), where the token was minted by `create_request` (`sing_store.py:734`).

### 4.0 Host guard, mounting, and public reachability

| Mechanism | Code | Behaviour |
|---|---|---|
| Public host set | `sing.py:_public_hosts:297` | `sing_public_host` (default `sing.nomadkaraoke.com`, `config.py:117`) + `sing_public_host_aliases[]`. Empty set disables guard + rewrite. |
| Root mount (WSGI rewrite) | `sing.py:install_public_host_rewriter:334`, installed `app.py:361` (and `:550`) | On a public host, any `PATH_INFO` not starting `/sing` gets `/sing` prepended, so `sing.nomadkaraoke.com/?t=1234` → `/sing/?t=1234`. `/sing/...` paths pass unchanged (SW notification URLs, static assets). |
| Host guard | `sing.py:install_host_guard:309`, installed `app.py:360` | `before_request`: on a public host, every endpoint not named `sing.*` → `abort(404)`. So KJ routes (`/rotation/*`, `/media/*`, `/preview/*` etc.) are unreachable publicly; `sing.static` IS reachable. |
| Client base detection | `sing.js:15` | `BASE = "/sing"` if pathname starts `/sing/`, else `""`. Every fetch uses `${BASE}/...`. Static assets/i18n files always resolve from `import.meta.url` (`/sing/static/...`) — see note `sing.py:329`. |
| Event URL / QR target | `sing.py:get_event_url:245` | public scope `<sing_public_url_base>/?t=TOK`; local scope `<sing_local_url_base or request host>/sing/?t=TOK`. `sync_event_url_overlays:271` rewrites `qr_code` overlays with `follow_event_url`. |

Publicly reachable routes (all `sing_bp`). Most are token-gated. The following gate themselves or are open:
- Open, no token: `GET /sing/` (landing, renders code entry), `POST /sing/validate`, `GET /sing/sw.js`, `GET /sing/static/*`, `POST /sing/forget` (**no token check at all**, `sing.py:2020`), `POST /sing/telnyx/webhook` (Ed25519 signature instead).
- Inline token check, not the decorator: `GET /sing/status/<id>`, and `POST /sing/requests/<id>/cancel|change`, `/sing/requests/reorder`, `/sing/update-phone`, `/sing/photo-consent`, `/sing/rename`. These run the rate limit **before** the token check.

### 4.1 Entry, event-code gate, and landing modes

| Capability (trigger) | sing.js function (line) | HTTP | Backend handler | State mutated | Side effects | Validation / limits |
|---|---|---|---|---|---|---|
| Scan QR / open URL with `?t=` | server render; bootstrap `sing.js:3741-3808` | GET `/sing/` (`/` on public host) | `sing.py:landing:432` | Flask session `sing_token` | none | Store missing → 503 closed. `!store.is_enabled()` → 403 `closed=True`. Bad or missing token → `code_entry=True` (400 if a token was supplied, else 200). A valid token renders the SPA with `data-token`, `data-request-id` (`?r=`), `data-make-requests-enabled`, `data-simple-mode`, `data-sms-region`, `data-kj-name`, and the `vapid-public-key` meta tag (`templates/sing.html:18,64-70`). |
| Enter 4-digit code | `initCodeEntry` `sing.js:3669`; `submitCode` `:3682` (auto-submits at 4 digits `:3711`) | POST `/sing/validate` `{t}` | `sing.py:validate_code:482` | in-memory `_validate_rate_limit_state` | On 200, the client redirects to `${BASE}/?t=CODE` (`:3698`) | Per-IP 10 per 300 s → 429 (`:495`). Client needs exactly 4 digits (`:3684`). Invalid code → 400 `{ok:false}`. Error copy: `code.badCode` / `code.tooMany` / `code.offline`. |
| Requests closed page | `sing.js:3805-3807` (adds language pill) | GET `/sing/` | `landing:447` (403) | — | — | No `sing_closed.html` exists, so `sing.html` renders with `closed=True`. |
| Legacy/deep link `?r=<id>[:edit_token][,…]` | bootstrap `sing.js:3744-3763` → `rememberRequestId` `:86` | — | (landing passes `request_id`) | localStorage `sing_my_request_ids` | Opens My songs if there is no hash. `id:edit_token` grants full self-service. | This is the push-notification click target (`sw.js` notificationclick). |
| Hash routing / Back | `STEP_HASH` `sing.js:422`, `_stepFromHash` `:440`, `_sanitizeStep` `:450`, `_syncHash` `:465`, `popstate` `:478`, `render` `:488` | — | — | `history` | — | Steps and hashes: identity=`#name`, search=`#search`, confirm=`#confirm`, done=`#mysongs`, rotation=`#rotation`, tip=`#tip`. `#confirm` with no selection falls back to search. Search or confirm with no name falls back to identity. A stale `#name` on a device that already has an identity is dropped. `_navReplace` makes the step use `replaceState` (used after submit). |
| Boot step | `_bootStep` `sing.js:435` | — | — | — | — | Has a name and a valid (or empty) phone → `search`, else `identity`. The old landing screen is gone. |
| Smart restore to My songs | `bootRestore` `sing.js:3810` → `refreshMySongs` `:3040` | GET `/sing/my-requests` | see 4.6 | prunes localStorage ids | Moves to `done` if there are live songs and the user is still on an untouched boot screen | Retries at 2 s, 4 s, 6 s (3 attempts) on network or 5xx failure (`:3825`). |
| Bottom tabs | `updateTabsBar` `sing.js:3312`, `_activeTabForStep` `:3297`, `_goRequestTab` `:3305` | — | — | — | — | Tabs: 🎵 Request, 🎤 My songs (badge = live song count), 📋 Rotation, 💜 Tip. The Tip tab only shows when `state.tipInfo.enabled`. |
| Shell header (brand, language pill, status tiles) | `renderShellTopbar` `:3179`, `brandHeader` `:3127`, `statusTile` `:3191`, `_stageTile` `:3214`, `_youTile` `:3243`, `updateMySongsBar` `:3271` | — | — | — | — | Lives outside `#sing-root` (`sing.html:33-35`) so it survives re-renders. The "you" tile shows on My songs and Rotation, or on any tab once the singer is urgent (position ≤3 or now singing). The stage tile shows on Rotation only. |

### 4.2 Device identity, name entry, rename, switch/forget

Client identity is stored in localStorage:
- `sing_device_id`: 128-bit hex, `getDeviceId` `sing.js:174`, rotated by `rotateDeviceId` `:186`.
- `sing_name` and `sing_phone`.
- `sing_photo_consent`.
- `sing_my_request_ids`: `{token, ids[], tokens{id:edit_token}}` (`:69-154`).
- `sing_lang` and `sing_rules_commercial_community_seen`.

Every mutation sends `device_id`. Server side, the `singer_aliases` table (`device_id` → `canonical_name`, `origin` `self`|`kj`, `sing_store.py:171`) overrides the typed name on `/submit` and `/tip-claim`.

| Capability | sing.js function (line) | HTTP | Backend handler | State mutated | Side effects | Validation / limits |
|---|---|---|---|---|---|---|
| First-time name + optional phone | `renderIdentity` `sing.js:1134`, `onSubmit` `:1155` | none (local only) | — | localStorage `sing_name`, `sing_phone` | Focuses the search box | Name required. Phone must match `PHONE_RE` `^\+?[0-9 \-()]{7,20}$` (`:191`) when present. The placeholder is `PHONE_EXAMPLES[SMS_REGION]` (`:53`). |
| Edit my name, persistent rename ("· edit name" / "Not you?") | `enterEditName` `:548`, `editNameLink` `:558`, `renameMe` `:317` | POST `/sing/rename` `{new_name, device_id, items:[{id,edit_token}]}` | `sing.py:rename_me:1883` | For each Own+Night request: `sing_requests.singer_name`. Rotation rename: if the old name is a KJ-established identity (`is_canonical_identity`) and there is a night marker, it runs the whole-group `rotation.rename_singer` + `persist_rename` + `remap_aliases`. Otherwise only the owned approved entries are renamed via `rename_singer_in_entries`. Also `carry_photo_consent`, then `set_alias(device_id,new_name)` with origin `self`. | The KJ rotation shows the new name on the singer's entries. | RL-dev. `new_name` must be non-empty and ≤100 chars. `device_id` is required (400). Items that fail Own or Night checks are silently skipped. An empty items list is valid (sets the alias only). 429 shows `common.tooManyChanges`. |
| "Different person?" switch | `switchIdentity` `:1113` → `forgetIdentity` `:334` | POST `/sing/forget` `{device_id}` | `sing.py:forget_me:2020` | `singer_aliases` row deleted (`clear_alias`) | none | No token or rate limit. Always returns 204. The client rotates `device_id` synchronously and clears name, phone, and consent. |

### 4.3 Search, results, versions, preview

| Capability | sing.js function (line) | HTTP | Backend handler | State mutated | Side effects | Validation / limits |
|---|---|---|---|---|---|---|
| Type a query | `renderSearch` `:1653`, `doSearch` `:1677` (700 ms debounce `:1682`, generation guard `searchGen` `:1676`), `search` `:285` | GET `/sing/search?q=` | `sing.py:search:568` → `routes.unified_search(grouped=True, catalog_limit=sing_search_catalog_limit or 60)` (`routes.py:5089`) | none | Response also carries `make_requests_enabled` and `simple_mode`, which the client mirrors into state (`:1696-1701`) so KJ toggles apply on the next search. | Tok. `q` must be ≥3 chars (400; the client skips shorter queries). Sources: local media library FTS, the local catalog mirror (`app.catalog_mirror`, `routes.py:5176`; KN community + KN full + Divebar index, falls back to the Divebar Cloud Function when stale), and KaraokeNerds (`karaoke_nerds_timeout` flag). Grouped one-per-song with `versions[]` best-first (`version_priority.annotate_versions`, `routes.py:481`). Groups are sorted by `_group_relevance` (`routes.py:319`): title match, then availability, then version count. |
| Anti-mis-tap arming | `armMs`/`armed` `:1667-1670`, set in `renderResults` `:2124` | — | — | — | — | Rows are inert for 300 ms after a re-render. Tests override with `window.__SING_ARM_MS`. |
| Result row + "Request this song" | `renderResults` `:2122` | — | — | `state.selected` | → confirm step | Single-version: `pickSingleVersion` `:1794` → `pickSingleLocal` `:1714` (`local`, `source_ref=path`) or `pickSingleKN` `:1724` (`divebar` with `file_id` + meta `{brand_code, disc_id, format}` if there is a Divebar mirror, else `kn` with the YouTube URL). Multi-version: `pickKjChoice` `:1750` (`kj_pick`, `source_ref=null`, `source_meta={group_key, version_count, versions}`), shown as "auto-picks best" (`search.autoBest`). In simple mode, multi-version rows have no CTA and the expander is forced open (`:2152,:2164`). |
| Expand versions / pick a specific version | `toggleExpanded` `:1851`, `renderVersionsExpander` `:1946`, `pickSpecificVersion` `:1805` | — | — | `state.selected` | Direct concrete source (not `kj_pick`), so auto-approve binds exactly this version | Sections: library, cloud (divebar), online, community (`_versionSection` `:1263`). The "online" section collapses behind `online-collapse-toggle` when a good option exists (`:1992`). versions[0] gets the BEST badge, and a ⭐ marks a stated brand priority. |
| Availability tier line | `renderVersionRow` `:1862` (tiers `:1881-1892`) | — | — | — | — | `local` → `sure` (on the box). Divebar `file_id` → `high` (cloud mirror, with size). Otherwise → `likely` (YouTube download). CSS `sing-avail-{sure,high,likely}`. |
| Community/Commercial pill → explainer | `openClassExplainer` `:1303`; one-time CC explainer `dismissCcExplainer` `:1845` | — | — | localStorage `sing_rules_commercial_community_seen` | — | The class comes from `version.priority_class` (`version_priority.rank_version` / `annotate_versions` `version_priority.py:307,341`). |
| Brand name → brand info modal | `openBrandInfo` `:1323` | — | — | — | — | Local i18n copy only. |
| Format pill → technical details | `openFormatDetails` `:1423` | POST `/sing/media-info` `{file_path}` (local only) | `sing.py:media_info:641` → `routes._resolve_media_path` + `mediainfo.probe_media_info` | none | — | Tok. `file_path` required (400); a path outside the media roots → 404. The server strips `path` and returns only `filename`. Divebar and YouTube versions render client-side from snapshot metadata. |
| ▶ Preview a search version | `openVersionPreview` `:1532` → `ensurePreviewLibs` `:1490`, `_previewDescriptor` `:1519`, `window.__PREVIEW_URL` `:1471` (rewrites the KJ `preview.js` endpoints onto `/sing/...?t=`) | GET `/sing/lib/{cdg.js,preview.js,hls.min.js}`; POST `/sing/preview/resolve`; GET `/sing/preview/stream/<tok>`, `/sing/preview/cdg/<tok>/<part>`, `/sing/preview/hls/<tok>/<name>`; POST `/sing/preview/close` | `sing.py:lib_file:630` (whitelist `_LIB_FILES` `:615`), `preview_resolve:698` → `app.preview.resolve(descriptor)`, `preview_stream:737` / `preview_cdg:744` / `preview_hls:751` / `preview_close:730` delegate to `routes.preview_*` | Preview transcode cache (`preview_cache_dir`, 8 GiB LRU) | Never records KJ preview stats (`:724`). Nothing reaches the device A/V output. | Tok. Preview RL is per-IP `sing_preview_rate_limit` 12 per `sing_preview_rate_window_s` 60 s → 429 `{mode:"unavailable"}`. Allowed sources are `local`, `divebar`, `youtube`, plus `entry` (below). Anything else → 400. |
| ▶ Preview a song already on the rotation | `openEntryPreview` `:1546` (from Rotation rows `:701-708` and My songs cards `:2706`) | POST `/sing/preview/resolve` `{source:"entry", entry_id}` | `preview_resolve:714` → `_entry_preview_descriptor:679` | same | — | Only entries with `file_path` present on disk (`_entry_previewable:666`), else 404 "Not ready". The file path never leaves the box. |
| Song ideas (my history + crowd favourites) | `renderInspiration` `:2193` (lazy on `<details>` open) | GET `/sing/my-stats?name=` | `sing.py:my_stats:1392` → `app.stats.singer_songs(name, 50)`, `top_songs(10)` | none | Tapping a row runs a search | Tok. Returns artist, title, and plays only (plus `last_sung` for "mine"). |
| Empty-state triage | `renderEmptyStateTriage` `:2012` | — | — | `state.selected` | — | Simple mode shows only "ask the KJ". Otherwise: (1) paste a YouTube URL → `pickYouTube` `:1777` (`youtube`); (2) "ask KJ to make it" → `pickMake` `:1766` (`make`, needs artist and title plus a `confirm()`), shown only when `makeRequestsEnabled`; (3) DIY link to gen.nomadkaraoke.com. |

### 4.4 Confirm and submit a request (incl. duet partners, photo consent)

| Capability | sing.js function (line) | HTTP | Backend handler | State mutated | Side effects (KJ-visible) | Validation / limits |
|---|---|---|---|---|---|---|
| Confirm screen | `renderConfirm` `:2295`, `_confirmSourceLine` `:2591` | — | — | — | — | Shows the song, the source line, "you searched X", and name/phone. |
| Add partner, existing singer (the "Existing/New singer picker") | `openExistingPicker` `:2433`, `loadKnownSingers` `:2409` (60 s cache `KNOWN_SINGERS_TTL_MS` `:236`), `addPartner` `:2423` | GET `/sing/singers` | `sing.py:known_singers:1383` → `_known_singer_names:1242` | none | — | Tok. The list includes every rotation entry (any active status, including `singers_json` members and `&`/`+` split duets via `_split_duet_name:1296`) plus tonight's (Night) pending and approved requests and their partners. Names are deduped by `_fold_name:1303`. The picker has a folded filter and A–Z headers above 12 names; the singer's own name is excluded; "add as new" is the fallback. This picker is on the **confirm screen for duet partners**, not the identity screen. |
| Add partner, new singer | `renderPartnersSection` `:2516` (`add-singer`) | — | — | `state.additional[]` | — | Max 3 partners (`MAX_PARTNERS` `:235`, server `_MAX_ADDITIONAL_SINGERS` `sing.py:395`). The per-partner phone is checked against `PHONE_RE` client-side (`:2345`). |
| Photo/video consent (first ask) | `photoConsentPicker` `:3609`, `setPhotoConsentLocal` `:3587`; shown when `askPhotoConsent()` `:3583` (`eventInfo.ask_photo_consent`) and no stored choice | rides on `/sing/submit` as `photo_consent` | `submit:1050` → `store.set_photo_consent(name, v, source="singer")` (`sing_store.py:586`) | `singer_photo_consent` (`name_key` = folded name) | The KJ rotation shows 📷, or a crossed-out 📷 when consent is no | Must be `yes`, `no`, empty, or null, else 400. Best-effort: never fails the submit. |
| **Submit request** | `send` `:2306` → `submit` `:290` | POST `/sing/submit` body `{singer_name, device_id, phone, song_artist, song_title, source_type, source_ref, source_meta, additional_singers?, photo_consent?}`. The server also accepts `notes`, which the UI never sends. | `sing.py:submit:962` | `sing_requests` INSERT (`status='pending'`, `token`=current, fresh `edit_token`, `user_agent`, `device_id`, `additional_singers` JSON). Client: `rememberRequestId(TOKEN,id,edit_token)`. | The KJ Requests right-rail shows it as pending. **If `is_auto_approve()`** (`sing.py:1061`): for `kj_pick`, `routes.resolve_kj_pick_best:5771` first binds the best resolvable version (`update_request_source`). Then `routes.approve_sing_request:5994` creates the rotation entry: `local` → linked file; `divebar`/`kn`/`youtube` → entry plus a download-queue item (dedup-links an existing file); `make` → Gen job, or leaves the entry as "Being Made (!)". Then `mark_approved(linked_entry_id)`, then `maybe_auto_reorder` (`routes.py:3787`). The rotation mutation fires `push_dispatcher.notify_rotation_changed` (ladder pushes). An auto-approve does **not** send the "You're in!" decision push. On failure the request stays pending. | RL-dev → 429 `confirm.tooMany`. `singer_name` required (it is replaced by the device alias if one exists). The phone must match `_PHONE_RE` when given. `source_type` must be one of `local`, `divebar`, `kn`, `youtube`, `make`, `kj_pick`. Simple mode allows only `local`, `divebar`, `kn` (`simple_mode_disabled_source`). `source_ref` is required for local, divebar, kn, and youtube. `make` needs `is_accepting_make_requests` plus artist and title. `kj_pick` needs 1–50 `source_meta.versions` plus artist and title. Partner names are canonicalized to tonight's spelling (`_canonicalize_partners:1370` / `match_known_singer:1312`: exact fold, then unique first name, then a Damerau-Levenshtein typo match within budget). The response includes `edit_token` exactly once. |
| Change-song submit | `send` `:2317` → `changeSong` `:299` | POST `/sing/requests/<id>/change` | see 4.5 | | | |

### 4.5 My songs (status, cancel, change, reorder, dismiss)

| Capability | sing.js function (line) | HTTP | Backend handler | State mutated | Side effects | Validation / limits |
|---|---|---|---|---|---|---|
| View my songs tonight | `renderDone` `:2902`, `pollMyRequests` `:2947` (cached first paint `:2995`), `fetchMyRequests` `:2629`, `_splitAndSortSongs` `:3090`, `_activeSortKey` `:3103`, `_renderSongCard` `:2681`, `_statusLine` `:2650`, `_renderSungSection` `:3116` | GET `/sing/my-requests?ids=a,b,…` (the last 20 stored ids) | `sing.py:my_requests:1484` | none | — | Tok. More than 20 ids → 400. Non-int ids → 400. Unknown, foreign-token, and non-Night ids are dropped. Each item has `request` (the `_public_request_view` at `:2084`: no phone, no `edit_token`, plus tip amount/method), and one of: `estimate` (if the linked entry is active), `previewable`, `removed` (the linked entry is Cancelled, or is gone and not done/left), or `performed` (the linked entry is Done or Left). The top-level `now_playing` is included. The client prunes ids that were queried but not returned (`pruneRequestIds` `:134`). |
| Status line wording | `_statusLine` `:2650`, `_mySongsPillSummary` `:3137` | — | — | — | — | Order: performed → rejected → cancelled → removed → pending → no estimate ("queued") → now singing → position 1 "next" → position 2 ("after this" if someone is on stage, else "one to go") → position ≥3 "#N, low–high". |
| Cancel a song | `_renderSongCard` cancel button `:2761` (uses `confirm()`) | POST `/sing/requests/<id>/cancel` `{t, edit_token, device_id}` | `sing.py:cancel_request:1567` | `sing_requests.status='cancelled'`, `reviewed_at` (`mark_cancelled` `sing_store.py:1003`). If it was approved and linked: rotation entry status → `Cancelled` (`rotation.update_status`). | The KJ rotation row shows Cancelled; the KJ can dismiss it or restore it to Waiting. Push ladder re-scan. | RL-dev (checked before the token). Invalid token → 403. Not Night or foreign → 404. Not Own → 403 `forbidden`. Already cancelled or rejected → 409. Linked entry Done or Left → 409 `already_sung`. Client-side: no buttons are shown when the song is performed or now singing (`:2717-2720`). |
| Change song (supersede) | change button `:2746` sets `state.changeRequestId/EditToken/SongLabel` → search → confirm → `changeSong` `:299`; banner `:2250`, "keep song" `:2255` | POST `/sing/requests/<id>/change` `{edit_token, device_id, song_artist, song_title, source_type, source_ref, source_meta}` | `sing.py:change_request:1624` | **Pending original**: updated in place (`update_request` + `update_request_source`), still pending. **Approved original**: a NEW pending request with `supersedes_request_id=<id>`, the same token, partners, and device; the original is untouched until the KJ approves. | The KJ sees a pending "replaces X" request. When the KJ approves it (`routes.py:6454-6481`), the new entry takes the old slot (the old entry is deleted and the new one moved to `old_pos`) unless the old entry is Done, Left, or Now Singing. The original request is marked cancelled. **Change requests are never auto-approved.** | RL-dev, token 403, Night 404, Own 403. Status must be pending or approved (409). A `reorder` meta-request → 400. Linked entry done or left → 409. Same source validation as submit, except `kj_pick` does not require artist and title. Partners cannot be edited. |
| Reorder my songs | `renderReorderView` `:2859`, `_reorderableSongs` `:2800` (approved + Own + has a position), `_enableReorderDrag` `:2819` (pointer drag on the ⠿ handle), `reorderSongs` `:306`; the toggle appears when there are ≥2 reorderable songs (`:2969`) | POST `/sing/requests/reorder` `{items:[{id,edit_token}], device_id}` | `sing.py:reorder_requests:1707` | `sing_requests` INSERT `source_type='reorder'`, `source_meta={ordered_entry_ids}`. If auto-approve is on: `routes.apply_reorder_request:5855` moves the singer's own entries within their existing slots, then `mark_approved`. | The KJ Requests panel shows a reorder meta-request, or the rotation reorders immediately under auto-approve. No push. | RL-dev, token 403. Needs ≥2 items. Duplicate ids, non-int ids, or non-object items → 400. Each item must be Own (else 403), Night (else 404), and approved and linked (else 409). Notice lasts 15 s (`REORDER_NOTICE_MS` `:2850`). |
| Dismiss a rejected or removed song | dismiss button `:2723` → `forgetRequestId` `:106` | none | — | localStorage only | — | Local-only. The id stops being queried. |
| Request another | `request-another` `:2918` | — | — | clears selection and partners | → search | — |

### 4.6 Rotation tab, now playing, wait estimates

| Capability | sing.js function (line) | HTTP | Backend handler | State mutated | Side effects | Validation / limits |
|---|---|---|---|---|---|---|
| Rotation list | `renderRotation` `:813`, `attachRotationLive` `:751`, `fetchRotation` `:600`, `_renderRotationBody` `:671`, `_waitText` `:611`, `_fmtWaitRange` `:629` | GET `/sing/rotation` | `sing.py:rotation:1198` → `_build_now_playing:2043` + `wait_estimate.compute_all_estimates` (`wait_estimate.py:84`) | none | — | Tok. Per active (non done/left/cancelled) entry it returns: position, `first_name`, `display_name` (every first name in a duet), `song_artist`, status, `entry_id`, `previewable`, `now_singing`, `expected_s`, and `range_low_s`/`range_high_s`. No phone, no full surname. Stale-while-revalidate cache is `state.rotationCache` with a 30 s TTL (`:598`). There is a manual ↻ refresh (`:655`). 403 → the "closed" copy. |
| Stage tile ("who's on stage") | `startStagePoll` `:577`, `_stageTile` `:3214` | GET `/sing/now` | `sing.py:now_playing:1187` → `_build_now_playing` | none | — | Tok. Returns `{now_singing, up_next, queued_count}`, excluding done, left, and cancelled. |
| House rules footer (Rotation tab only) | `renderRulesFooter` `:3644`, `updateRulesFooterVisibility` `:3638` | (uses tip-info threshold) | — | — | — | Rule 6 quotes the tip threshold (default 20). |
| Single-request status (legacy) | **not called by sing.js** | GET `/sing/status/<id>` | `sing.py:status:1438` | none | — | Inline token check → 403. Foreign token or not Night → 404. Returns `request`, `now_playing`, and, if linked, `estimate` and `queue` (`_public_queue_view`, first names only). |
| Wait estimate math | — | — | `wait_estimate.compute_estimate:14`, `compute_all_estimates:84`, `_baseline:63` | — | — | Expected time = the sum of the durations of the active entries ahead (entry `duration`, else the baseline) + `sing_estimate_transition_s` (30) per transition. The baseline is the mean of ≥3 Done durations ("tonight"), else `sing_estimate_default_song_s` (240, "fallback"). Spread = max(`sing_estimate_min_spread_s` 120, pstdev or 180). |

### 4.7 Tip tab (tip claim → heart + priority bias)

| Capability | sing.js function (line) | HTTP | Backend handler | State mutated | Side effects | Validation / limits |
|---|---|---|---|---|---|---|
| Load tip config (boot) | bootstrap `:3781` | GET `/sing/tip-info` | `sing.py:tip_info:871` → `_tip_settings:778` (the KJ modal `rotation_meta` settings take precedence over config.json and defaults), `_tip_methods:817` | none | Shows the 💜 tab when enabled | Tok. Default-on. When no handles are configured, the only method is the `https://nomadkaraoke.com/tip` page. Methods: cashapp or paypal (`path` amount), venmo (`venmo` pay intent), zelle (`copy`), stripe or custom (`none`). A failed fetch is treated as disabled (`:3788`). |
| Pick an amount and open a payment app | `renderTip` `:873`, `methodUrl` `:904`, `setAmount` `:976`, presets 3/5/10/20 `:987`, Zelle copy `:926` | external links | — | — | — | Preset buttons at or above the threshold get a ♥. |
| File a tip claim | submit `:1023` | POST `/sing/tip-claim` `{singer_name, device_id, phone, amount, method}` | `sing.py:tip_claim:907` | `sing_requests` INSERT `source_type='tip'`, `source_meta={amount,method}`, `notes="Tip claim: $X via M"`. Client `rememberRequestId`. | The KJ Requests panel shows a Confirm card. It is **never auto-approved**. When the KJ confirms (`routes.py:6395` → `apply_confirmed_tip:5803`), every non-done entry of that singer gets `paid=1` (♥). If the amount is ≥ the threshold, `set_singer_priority_bias(name, 1)` runs and then `run_auto_order`. A KJ reject skips the push. | Tok. Tips disabled → 400. **Per-IP** 5 per 600 s → 429. `singer_name` required (replaced by the device alias). The amount must satisfy 0 < amount ≤ 500. A malformed phone is silently dropped. |
| Claim status list | `claimsSection` `:1063`, `_tipStatusLine` `:838` | GET `/sing/my-requests` (15 s poll `:1097`) | `my_requests` | — | — | pending → "waiting", approved → "confirmed", rejected → "not confirmed". Tip claims are excluded from the song lists (`_liveSongs` `:3071`, `_splitAndSortSongs` `:3090`). |

### 4.8 Notifications: Web Push, SMS phone, photo consent (My songs section)

| Capability | sing.js function (line) | HTTP | Backend handler | State mutated | Side effects | Validation / limits |
|---|---|---|---|---|---|---|
| Register the service worker | `registerServiceWorker` `:3347` (only if `serviceWorker` and `PushManager` exist) | GET `/sing/sw.js?t=` (scope `${BASE}/`) | `sing.py:service_worker:546` (injects `APP_VERSION` into the cache name, `Cache-Control: no-cache`) | browser SW registration | — | Not token-gated. |
| Enable browser notifications | `maybeShowPushPrompt` `:3493`, `requestPushPermission` `:3409`, `ensurePushSubscription` `:3375` | POST `/sing/push/subscribe?t=` `{phone, singer_name, subscription:{endpoint, keys:{p256dh,auth}}}` | `sing.py:push_subscribe:1093` → `insert_push_subscription` (`sing_store.py:1228`, upsert on `(token,endpoint)`, clears `disabled_at`) | `sing_push_subscriptions` | Later pushes come from `PushDispatcher`. **Ladder** (`push_dispatcher.py:29`): after each rotation mutation, debounced 0.5 s, it scans active subs for the current token. Each sub's phone maps to the first active entry linked via `sing_requests.phone` (Night-scoped, `app.py:295`). Steps: `up_in_2` (position 3), `up_next` (positions 1–2), `now_singing`. Deduped via `last_sent_state`. **Decision** pushes: approve or reject from KJ routes (`routes.py:6487,6535`), and auto-resolve `resolved_alt` / `unavailable` (`routes.py:890`, `_notify_sing_outcome:5981`). Copy is at `push_dispatcher.py:67`. A 404 or 410 from the push service disables the sub. | Tok. **Phone is required** (400 if missing), and so are the name, endpoint, and keys. The phone must match `_PHONE_RE`. iOS outside standalone PWA mode gets an "Add to Home Screen" hint instead (`:3423-3425,:3525`). |
| Unsubscribe | **not called by sing.js** | POST `/sing/push/unsubscribe` `{endpoint}` | `sing.py:push_unsubscribe:1122` | `disabled_at` set | — | Tok. `endpoint` required. |
| Push display and click | `sw.js` `push` / `notificationclick` handlers | — | — | — | Opens `/sing/?t=TOK&r=<request_id>`, or focuses an existing `/sing/` window and posts `push-focus` | — |
| Add or change SMS number | `_phoneEditor` `:3461`, `_savePhoneNumber` `:3441` | POST `/sing/update-phone` `{phone, device_id, items:[{id,edit_token}]}` | `sing.py:update_phone:1782` → `set_request_phone` | `sing_requests.phone` on each Own+Night request (max 20) | The KJ SMS button and auto-SMS target resolve the phone from these rows (`routes._resolve_sms_target:4044`, auto-send `routes.py:4311` when `is_auto_sms_next`). The client re-syncs the push sub's phone. | RL-dev, token 403, phone required and valid (400). Items that fail Own or Night checks are skipped. Returns `updated` count. The Own check here uses a plain `!=`, not `compare_digest` (`:1824`). |
| Change photo consent later | `maybeShowPushPrompt` `:3562`, `savePhotoConsent` `:3592` | POST `/sing/photo-consent` `{consent, items}` | `sing.py:photo_consent:1832` | `singer_photo_consent` for each distinct Own+Night singer name | KJ rotation 📷 marker | RL-dev, token 403, consent must be `yes` or `no`. Zero owned requests → `updated: 0` (the choice goes out with the next submit instead). The UI rolls back on failure. |
| Inbound SMS (STOP/START) and delivery receipts | — (Telnyx → server) | POST `/sing/telnyx/webhook` | `sing.py:telnyx_webhook:1136` → `sms.verify_webhook_signature:289`, `parse_webhook_event:350`, `classify_inbound_keyword:333` | `sms_store`: DLR → `update_status_by_telnyx_id`; STOP-family (`STOP`, `STOPALL`, `UNSUBSCRIBE`, `CANCEL`, `QUIT`, `END`, `OPTOUT`; first word only) → `record_opt_out`; `START`, `UNSTOP`, `YES`, `OPTIN` → `clear_opt_out` | The KJ SMS send path refuses opted-out numbers | Ed25519 signature against `TELNYX_PUBLIC_KEY`, failing closed → 401. Recognised events always get 200. A malformed body gets 200. |

### 4.9 Venue footer, social links, i18n, PWA, offline

| Capability | sing.js function (line) | HTTP | Backend handler | State mutated | Side effects | Validation / limits |
|---|---|---|---|---|---|---|
| Footer: KJ message, notices, social links | `loadEventInfo` `:1562` (once at boot), `renderEventFooter` `:1627`, `renderSocialLinks` `:1600` (order `SOCIAL_ORDER` `:1584`) | GET `/sing/event-info` | `sing.py:event_info:884` → `store.get_footer_settings` (`sing_store.py:505`) + `_tip_settings` | none | — | Tok. Returns `{kj_name, footer_message, notices[], social{}, ask_photo_consent}`. Notices are i18n keys (`notices.<key>`). Social values must be `http(s)` URLs (email → `mailto:`), validated by both the server (`_clean_social_value` `sing_store.py:478`) and the client. |
| Language switch (33 locales) | `langPill` `:376`, `openLanguageModal` `:385`, `rerenderForLocale` `:408`; `i18n.js`: `detectLocale:79` (checks `?lang=`, then localStorage `sing_lang`, then `navigator.languages`, then `en`), `initI18n:119`, `_activate:136` (sequence-guarded, falls back to en), `setLocale:168`, `t/tn` (Intl plural), `applyStaticStrings:213` (`data-i18n*` attributes) | GET `/sing/static/messages/<locale>.json?v=APP_VERSION` | `sing.static` | localStorage `sing_lang` | Sets `<html lang dir>`. RTL for `ar` and `he`. | Re-renders in place (no reload). Aliases: `no`/`nn`→`nb`, `fil`→`tl`, `iw`→`he`, `in`→`id`, `zh-hans`/`zh-hant`→`zh`. |
| PWA manifest / install | `sing.html:10-18` | GET `/sing/manifest.json?t=` | `sing.py:manifest:505` | — | — | Tok. `start_url` is `<base>?t=TOK` (base is `/` on the public host, `/sing/` otherwise), `display: standalone`. `beforeinstallprompt` is captured but never surfaced (`sing.js:3430`). |
| Offline banner | `setOfflineBanner` `:243`, `onPollSuccess/Failure` `:250-258`, window `online`/`offline` events `:260` | — | — | — | — | The banner shows after 2 consecutive poll failures (`OFFLINE_FAIL_THRESHOLD` `:241`) or on the browser `offline` event. Stale lists and the stage tile stay painted. |
| Offline shell | `sw.js` install and fetch handlers | — | — | Cache Storage `nomad-sing-shell-<APP_VERSION>` | — | Network-first for shell paths (css, js, i18n.js, en.json, icons), falling back to the precache while ignoring the query string. Only when the SW is registered, which requires PushManager. |
| Cached-first paint | My songs `pollMyRequests` `:2995`; Rotation SWR `attachRotationLive.load` `:761`; stage tile from `state.nowPlaying` | — | — | in-memory `state.mySongs`, `state.rotationCache`, `state.nowPlaying` | — | Switching tabs paints the last-known data immediately. Notices are time-boxed rather than lasting "until the next paint". |
| Test bridge | `sing.js:3725-3735` | — | — | — | — | `window.__sing_state`, `__sing_render`, `__sing_pruneRequestIds`, `__sing_readMyRequestIds`, `__sing_t`, `__sing_setLocale`, `__sing_getLocale`, `__SING_ARM_MS`, `__PREVIEW_URL`. |

### 4.10 Request lifecycle state machine (`sing_requests.status`)

Statuses: `pending` (the column default, `sing_store.py:121`), `approved`, `rejected`, `cancelled`. `performed` and `removed` are **derived** by `/my-requests` from the linked rotation entry. They are not stored.

| From → To | Trigger | Who | Code |
|---|---|---|---|
| ∅ → pending | `/submit`, `/tip-claim`, `/requests/reorder`, `/requests/<id>/change` (approved original → a new superseding row) | singer | `sing.py:1033, 945, 1749, 1694` → `create_request` `sing_store.py:734` |
| pending → pending (song edited in place) | `/requests/<id>/change` on a pending row | singer | `sing.py:1680-1690` |
| pending → pending (source bound) | `kj_pick` auto-bind before auto-approve | system | `routes.resolve_kj_pick_best:5771` |
| pending → approved | auto-approve on submit (song) or reorder | system (`is_auto_approve`) | `sing.py:1061-1080`, `1761-1770` → `mark_approved` `sing_store.py:968` |
| pending → approved | KJ Approve (song → rotation entry + `linked_entry_id`; `kj_pick` requires `version_index`; tip → heart/bias; reorder → apply) | KJ | `routes.approve_sing_request_route:6381` (409 unless pending) |
| pending → rejected | KJ Reject (with a push, except for tips) | KJ | `routes.reject_sing_request_route:6519` → `mark_rejected` `sing_store.py:986`. **The route has no status guard**, so it can also reject an approved request, and that leaves the rotation entry in place. |
| pending/approved → cancelled | singer Cancel (an approved one also sets the rotation entry to `Cancelled`) | singer | `sing.py:cancel_request:1567` |
| approved → cancelled | the KJ approves the superseding change request; the original is replaced | KJ-triggered system | `routes.py:6476` |
| (derived) approved → performed | the linked entry reaches Done or Left | KJ/playback | `sing.py:1548-1555` |
| (derived) approved → removed | the linked entry is `Cancelled` (singer or host), or missing / not done-or-left | KJ | `sing.py:1540-1560` |

Related rotation-entry side: the singer can only move an entry to `Cancelled` (via cancel). All other entry statuses (Waiting, Now Singing, Done, Left, "Being Made (!)") are set by the KJ or the system. `make` requests that Gen rejects stay unlinked as "Being Made (!)" (`routes.py` in `approve_sing_request`'s make branch). YouTube download failures auto-advance to the next candidate via `sing_resolve` (`MAX_CANDIDATES=3`, `MAX_TRANSIENT_RETRIES=2`) and push `resolved_alt` or `unavailable` to the singer.

### 4.11 Client polling and timing cadences

| What | Interval | Active when | Code |
|---|---|---|---|
| `/sing/my-requests` (My songs) | immediately, then every 15 s | step `done` (cleared in `render` `:493`) | `sing.js:3030-3031` |
| `/sing/my-requests` (status-bar poll) | 20 s | not on `done` and live songs > 0 | `startBarPoll` `:3167` |
| `/sing/my-requests` (Tip claims) | 15 s | step `tip` | `:1097-1106` |
| `/sing/my-requests` (boot restore) | at 0 s, then retries after 2 s, 4 s, 6 s | boot, untouched landing | `bootRestore` `:3810` |
| `/sing/now` | immediately, then every 15 s | step `rotation` | `startStagePoll` `:577-593` |
| `/sing/rotation` | 30 s auto-refresh; 30 s cache TTL; age label re-computed every 5 s | step `rotation` | `:743-805` |
| `/sing/singers` | cached 60 s | partner picker opened | `:236, :2409` |
| `/sing/search` | 700 ms debounce; 300 ms row arming | typing | `:1682, :1668` |
| `/sing/event-info`, `/sing/tip-info` | once at boot | SPA mode | `:3779-3781` |
| Server push debounce | 0.5 s after each rotation mutation | always | `push_dispatcher.py:109` |

### 4.12 Route → test coverage (from the §6 generated inventory)

| Route | Tests |
|---|---|
| GET `/sing/` | test_host_guard, test_sing_public_routes, test_sing_make_request_disable_e2e, test_sing_simple_mode_e2e |
| POST `/sing/validate`, GET `/sing/manifest.json`, GET `/sing/sw.js` | test_sing_public_routes |
| GET `/sing/search` | test_sing_public_routes, test_kn_panel_grouped_search, test_sing_make_request_disable_e2e, test_sing_simple_mode_e2e |
| `/sing/lib`, `/sing/media-info`, `/sing/preview/{stream,cdg,hls}` | test_sing_media_preview |
| POST `/sing/preview/resolve` | test_sing_media_preview, test_sing_ux_i18n |
| POST `/sing/preview/close` | **none** |
| `/sing/tip-info`, `/sing/tip-claim` | test_sing_tips |
| `/sing/event-info` | test_footer_social_photo_consent, test_sing_ux_i18n |
| POST `/sing/submit` | 13 files (test_sing_kj_pick(_e2e), test_rotation_e2e, test_host_guard, test_sing_admin_routes, …) |
| `/sing/push/subscribe`, `/sing/push/unsubscribe` | test_sing_push_routes |
| POST `/sing/telnyx/webhook` | test_sms_routes |
| `/sing/now` | test_sing_now_and_status |
| `/sing/rotation` | test_sing_rotation_route, test_sing_ux_i18n |
| `/sing/singers` | test_sing_partner_match |
| `/sing/my-stats` | test_sing_my_stats |
| `/sing/status/<id>` | test_sing_now_and_status, test_sing_public_routes |
| `/sing/my-requests` | test_sing_public_routes, test_sing_tips |
| cancel / change | test_sing_public_routes |
| reorder | test_sing_public_routes, test_rotation_e2e |
| `/sing/update-phone` | test_sing_update_phone |
| `/sing/photo-consent` | test_footer_social_photo_consent |
| `/sing/rename`, `/sing/forget` | test_sing_rename |

### 4.13 Discrepancies found while mapping (for the fixture/simulation work)

1. **Per-IP singer rate limit is 5, not 60.** `config.py:119` sets `sing_rate_limit_per_ip: 5`. Config defaults are merged into `kj_config`, so they override the code default `_IP_RATE_DEFAULT=60` (`sing.py:111`). On shared venue wifi the whole crowd therefore gets 5 mutations per 5 min, unless the device's config.json overrides it. `docs/CHANGELOG.md:178` claims "default now 60". The test conftest also uses 5. A simulation with many phones behind one IP will hit 429s.
2. **`/sing/rotation` misaligns estimates when a `Cancelled` entry exists.** `sing.py:1211-1215` zips `active` (excludes done, left, and cancelled) with `compute_all_estimates(entries)` (excludes only done and left). Every row after a cancelled entry gets the wrong position, wait, and `now_singing` flag, and the last estimate is dropped. `compute_estimate` (`/my-requests`, `/status`) and `push_dispatcher.decide_ladder_step` also count `Cancelled` entries as ahead in line. No test combines Cancelled with `/sing/rotation`.
3. **The "tonight's variance" estimate never activates.** `rotation.get_rotation()` returns only non-done/left entries (`rotation_store.get_entries:293`), so `wait_estimate._baseline` never sees Done durations. `spread_source` is always `fallback`, the baseline is always 240 s, and the spread is always 180 s. Only entries with a stored `duration` deviate from this.
4. **Push opt-in silently fails without a phone.** `/push/subscribe` requires a phone (400), but `ensurePushSubscription` (`sing.js:3392`) ignores the response status. The UI shows "browser notifications on" while no subscription is stored. Push delivery is keyed by phone in any case.
5. Shared-IP budgets beyond #1: `/validate` (10 per 5 min), `/tip-claim` (5 per 10 min), and `/preview/resolve` (12 per min) are **per-IP only**, so the whole venue shares one budget.
6. `sw.js` notificationclick only focuses windows whose URL contains `/sing/`. On the public root host that never matches, so it always opens a new window. The `push-focus` postMessage has no listener in `sing.js`.
7. `/sing/forget` has no token or rate-limit check. It needs an unguessable `device_id`, so the risk is low.
8. Unused endpoints: `sing.js` never calls `/sing/status/<id>` or `/sing/push/unsubscribe`. The submit field `notes` is accepted but never sent by the UI.
9. The auto-approve path on `/submit` does not send the "You're in!" decision push, which the KJ manual approve does (`routes.py:6487`). Only ladder pushes fire.
10. The KJ reject route (`routes.py:6519`) has no pending-only guard. Contrast with approve, which returns 409.

## 5. Background / automatic behaviours

All paths are relative to `kj-controller/` unless prefixed with `desktop/`, `docs/` or `deploy/`. `app.py` line numbers are from committed HEAD (5cd00eb). The uncommitted `action_recorder.py` / `_install_action_recorder` work in this worktree is **not** mapped here.

**Two app factories, wired differently. This matters for the simulation harness:**
- `start_app()` (`app.py:365`) is production. It starts every thread below and wires `on_karaoke_end` (`app.py:401`).
- `create_app(config)` (`app.py:225`) is what tests use. It **does not** call `gen_poller.start()` (the poller object is built at `app.py:345` but never started), **does not** wire `on_karaoke_end`, **does not** call `vlc.init_playback()` (so there is no player monitor thread), and only starts the PerfSampler when `config is None` (`app.py:356`).
- `SheetSync.start()` fires inside `RotationManager.__init__` (`rotation.py:39-47`) under **both** factories whenever `rotation_sheet_id` + `rotation_credentials_file` are set.
- A simulation that wants song-end → filler, gen polling, or crash recovery has to wire these itself.

### 5.1 In-process threads, timers and hooks (kj-controller Python)

| Behaviour | Trigger + cadence | Code | Reads | Mutates | External calls | Failure mode / live-night impact | Kill switch / config |
|---|---|---|---|---|---|---|---|
| **Player monitor: song-end (EOF) detection, mpv** | Daemon thread started by `PlaybackCoordinator._start_monitor` (`playback.py:280-285`) from `init_playback` (`playback.py:255`, called at `app.py:568`), `switch_renderer` (`:231`) and `restart_instances` (`:320`). Event-driven over the mpv IPC socket. Falls back to polling `idle-active` every 2s; the poll ignores the first 5s after a play or seek. | `mpv_manager.py:monitor:956`, `_monitor_via_events:974` (fires on `end-file` with `reason=="eof"`, `:1009`), `_monitor_via_polling:1017`, `_handle_karaoke_ended:1034` | mpv IPC socket | `player.active=False`, `current_path=None`, pitch reset, `/tmp/kj-mpv-state.json` (`_save_state:239`). Calls `ensure_released()` (`:884`), then `on_karaoke_end()` | mpv IPC | A KJ stop or fadeout sends `stop`, which produces `end-file` with reason `stop`, **not** `eof`, so `on_karaoke_end` does not fire. Those routes fade filler in themselves. If the monitor thread has exited (see engine crash), song end is never detected: the overlay stays in "karaoke playing" and filler does not return. | `render_mode` (`mpv`/`vlc`) |
| **Player monitor: song-end detection, VLC** | Same thread. Polls VLC HTTP every 2s (5s grace after play or seek). Song end = `state=='stopped'`. | `vlc.py:monitor:656-687` | VLC HTTP :8080-ish | Same as the mpv row; `/tmp/kj-vlc-state.json` | VLC HTTP | A transient `stopped` state is a false positive, damped only by the 5s grace window. | `render_mode=vlc` |
| **on_karaoke_end callback → overlay off + filler fade-in** | Called by the monitor on natural EOF | `app.py:_make_on_karaoke_end:170-187`, wired at `app.py:401` (start_app only); passthrough property at `playback.py:399-405`, preserved across renderer swap and restart (`:220,226,303,313`) | none | `overlays.json` `karaoke_playing=False` (`overlay.py:182`), filler `fade_in()` | filler VLC HTTP | Exceptions are swallowed. **Does NOT touch rotation, stats, push or SMS.** | none |
| **Filler fade-in + "aout dead" auto-heal** | `fade_in()` spawns a fade thread (20 steps over 1.5s) plus a verify thread. Verify sleeps 4s; if `playedabuffers==0 && decodedaudio>=100` it relaunches the filler VLC. | `filler.py:fade_in:247-268`, `_verify_playing:272-298`, `_relaunch:300-309`, `_fade:236` | filler VLC status/stats | filler VLC process (kill + relaunch), volume | `cvlc` subprocess, VLC HTTP :8081 | The relaunch plays at target volume without re-verifying, so a hard failure does not loop. The relaunch costs 2.5s+. | `filler_music_dir`, `default_filler_track`, `filler_volume` |
| **Filler fade-out before karaoke** | Synchronous inside `play_video`, which runs on a per-`/play` thread (`routes.py:1064`) | `playback.py:play_video:408-433` → `filler.fade_out:311`, `ensure_stopped:324` (5 tries, 0.5s apart) | filler status | filler stopped | VLC HTTP | If filler cannot be confirmed stopped, only a warning is logged. The karaoke engine may then fail to open ALSA, which shows as `audio_error`. | none |
| **Playback-progress verify** | One-shot daemon thread per play. mpv: poll `time-pos` every 0.5s for up to 10s. VLC: a single check after 3s. | `mpv_manager.py:_verify_playback_progress:621` (spawned `:595`), `vlc.py:verify:466-479` | engine `time-pos`/state | `player.audio_error` (drives the "audio device issue" banner through `/status`) | IPC/HTTP | False banners on a cold 4K decode (mpv mitigated by the 10s window). | `PLAYBACK_VERIFY_TIMEOUT` const `mpv_manager.py:38` |
| **CDG audio-length probe** | Daemon thread per CDG play (`_begin_audio_probe:435`, thread at `:448`) | `mpv_manager.py:_probe_audio_duration_async:597` | ffprobe on the .mp3 | `_audio_duration`, guarded by a generation counter | ffprobe | A slow probe only means the song length shown is short. | none |
| **VLC window positioning** | Daemon thread per VLC play when `video_top_margin_px>0` | `vlc.py:457-465` (`_position_window`) | wmctrl | X window geometry | wmctrl | Cosmetic only | `video_top_margin_px` |
| **Engine-crash detection → auto-restart / escalate** | Each monitor loop iteration calls `_notify_if_dead` (process poll, or socket liveness with a 2-strike debounce). The callback then spawns an `engine-recovery` thread. | `mpv_manager.py:_notify_if_dead:903-954`, `vlc.py:~620-654`, `playback.py:_handle_engine_died:99`, `_record_crash:111`, `_safe_restart:140` → `restart_instances:287` | process state | `_health_events`/`_crash_history` deques (served as `player_alert` in `/status`); **filler and karaoke both killed and relaunched** | subprocess | 3 or more crashes within 60s (`CRASH_GUARD_MAX/WINDOW`, `playback.py:45-46`) means escalate with no restart. **The monitor thread has already returned, so there is no song-end detection until the KJ restarts or switches engine.** The restart takes about 4s+ of dead air. | constants only |
| **Coordinator fadeout** | KJ `/control fadeout` → daemon thread: player fade (`mpv_manager.py:fadeout:750` / `vlc.py:fadeout:~505`), sleep `duration+0.5`, `ensure_released`, overlay off, filler fade-in | `playback.py:fadeout:467-481`; route `routes.py:1115-1127` (duration clamped 0.5-60s) | none | same as on_karaoke_end | IPC | Behaviour is timer-based, so if the engine stalls the filler still comes in. | none |
| **Per-song temp CDG extraction** | Synchronous in `/play`. The previous temp dir is cleaned on the next extract. | `zip_playback.py:extract_and_get_mp3:22`, `cleanup:173` | zip | temp dir | unzip | none significant | none |
| **Play-stat recording** | **At play START**, synchronously in `/play` (not at song end). SSD/library files with no media row are hashed off-thread (`library_media.run_async:91`). | `routes.py:_record_play_stat:86-130`, `_record_library_play:173` | `media_library`, `rotation_entries` (singer) | `play_events` (`stats_store.py:51`); may insert a `media_library` row | none | Swallowed on error. Replays and restarts count as extra plays. | none |
| **Rotation `_after_mutation` hook** | Every RotationManager mutation (add, edit, status, move, reorder, link, download, gen status, paid, bias, archive, undo/redo, renames…) | `rotation.py:_after_mutation:402-422`; `_before_mutation:390` (undo checkpoint) | none | `rotation_meta.rev` (`rotation_store.py:bump_rev:1388`); `rotation_history` (checkpoint `:1431`); `/tmp/rotation_cache.json` (`_write_display_cache:424`, atomic) | none | All best-effort. **SheetSync is NOT poked here** (`:417-422` is a comment-only no-op); it relies on its own 30s loop. | none |
| **Display cache refresh on read** | Every `get_rotation()`: KJ poll every 10s, singer polls, push dispatch, and the boot write at `app.py:459` | `rotation.py:get_rotation:53-62` | `rotation_entries` | `/tmp/rotation_cache.json` | none | The overlay treats a cache older than 120s as **offline** (`desktop/rotation_source.py:16,44-50`). With no browser open and no mutations, the TV rotation list goes offline after 2 minutes. | none |
| **Web Push ladder dispatch** | `notify_rotation_changed()` from `_after_mutation`, debounced by a 0.5s `threading.Timer`; sends go to a 2-worker `ThreadPoolExecutor` | `push_dispatcher.py:notify_rotation_changed:135-144`, `_dispatch_now:171-208`, `decide_ladder_step:29-53` (Now Singing → `now_singing`; active pos 1-2 → `up_next`; pos 3 → `up_in_2`), `next_entry_for_phone:56`, `_send:210-238`; phone lookup wired at `app.py:294-326` (night-scoped via `night_started_at`) | `sing_push_subscriptions`, `sing_requests.linked_entry_id/phone`, rotation | `sing_push_subscriptions.last_sent_state` (reserved **before** send; dedups on entry_id+step); disables subscriptions on 404/410 | pywebpush → browser push services | A failed send is not retried; the next ladder step is picked up on a later mutation. No push is sent without an event token. | VAPID keys (`app.py:_bootstrap_vapid_keys:105`); singer opt-in |
| **Immediate decision push** | KJ approve (`routes.py:6484-6489`), reject (`routes.py:6532-6535`) | `push_dispatcher.py:notify_request_decision:146-165` | subscriptions by phone | none (no dedup state) | webpush | none | same |
| **Sing-fallback outcome push (`resolved_alt`/`unavailable`)**: **LATENT BUG** | Download worker on success-after-fallback or on exhaustion | `routes.py:_notify_sing_outcome:5981-5991` | none | none | none | Uses `getattr(app, "push_dispatcher")`, but the dispatcher lives on `app.rotation.push_dispatcher` (`app.py:320/509`). **These pushes never fire.** Untested (`tests/integration/test_sing_fallback.py` has no push assertions). | none |
| **Auto Order on new entry** | Synchronous after KJ manual add (`routes.py:3721`), KJ approve (`routes.py:6491`), and auto-approved singer submit (`sing.py:1077-1078`). Confirmed tip always re-weaves (`routes.py:6404-6409`). | `routes.py:maybe_auto_reorder:3787`, `run_auto_order:3757` → `auto_order.compute_auto_order:232` → `rotation.reorder_by_ids:138` | rotation + decorations (songs_sung, wait, duration) | `rotation_entries.position`, undo checkpoint "Auto Order (new entry)", `_after_mutation` (so pushes follow) | none | Best-effort: errors are logged and the add/approve still succeeds. There is **no periodic reorder**. It runs only on these events. | `rotation_meta.rotation_auto_reorder` (`sing_store.py:26,408,418`), toggled via Requests settings `routes.py:6312-6318` |
| **Auto-approve singer submissions** | Synchronous in the `/sing` submit handlers | `sing.py:1061-1080` (and `:1760-1768`) → `routes.approve_sing_request:5994`, `resolve_kj_pick_best` | `sing_requests`, catalog | new `rotation_entries` row, `sing_requests.status/linked_entry_id`; may enqueue a download | download sources | On failure the request stays pending. | `sing_store.is_auto_approve()` (Requests settings) |
| **Download worker (queue drain)** | Daemon thread started on demand when items are queued and no worker is running: `/download` `routes.py:596`, divebar `:2335`, download-and-link `:5472`, sing approve `:6123`. Processes items serially until empty. | `routes.py:_download_worker:816-925`, `_sync_rotation_download:680`, `_attempt_sing_fallback:5926` | `app.download_queue` (in-memory) | media files, media index, `rotation_entries.download_status/file_path` (`rotation.complete_download:195`), `sing_requests.source*` | yt-dlp / HTTP / Divebar / GCS | The queue is **in-memory**, so a service restart loses queued downloads and leaves entries stuck in `downloading`. A failed singer pick advances to the next candidate. Entries are auto-linked on success. | none |
| **Tier-2 render verification** | Single lazy daemon worker on a `queue.Queue`, fed after `/rotation/link` (`routes.py:4004`) | `routes.py:_enqueue_tier2:3938`, `_tier2_worker:3929`, `_run_tier2_check:3895` → `playability.PlayabilityChecker.check(depth="deep")` | the file | `rotation_entries.playability_warning` (`rotation_store.py:set_playability_warning:792`). **No rev bump or `_after_mutation`**, so the warning appears on the next poll. | Xvfb (random display, `playability_render.py:71`) + mpv/VLC | Spawns an off-screen Xvfb + engine **during the show** (CPU/GPU contention). Only covers `/rotation/link`, not download or gen auto-links. | none |
| **GenPoller (MAKE requests)** | Daemon thread with `Event.wait(gen_poll_interval=60s)`; **start_app only** (`app.py:539`) | `gen_poller.py:_run:94`, `poll_once:23`, `_handle_complete:47` | `rotation_entries WHERE gen_job_id NOT NULL AND gen_status NOT IN (complete,failed)` (`rotation_store.py:839`) | `gen_status` (`rotation.set_gen_status:221`), downloaded file + `link_file` + status `complete` (`rotation.complete_gen_job:257`); both fire `_after_mutation` | gen API (`gen_client`), HTTP download | The download blocks the loop, so other jobs wait. **A failed download is never retried**: `gen_status` is set to `complete` *before* `_handle_complete` runs (`:37-42`), and `get_active_gen_entries` then excludes the entry. It is left `complete` with no file, and the KJ has to link it manually. | `gen_api_url` + `gen_api_token` (absent means no poller), `gen_poll_interval` |
| **Google Sheets backup (one-way push)** | Daemon thread: `sync_now()` then `wait(rotation_sync_interval=30s)`, **unconditionally** (not change-driven) | `rotation_sync.py:start:105`, `_run:120`, `sync_now:130-193` | `rotation_store.get_all_entries()` (incl. done) | Google Sheet rows (overwritten in place; **never cleared**, so rows beyond the current count go stale after an archive); `rotation_meta.last_sheet_sync` | Google Sheets API | Offline means `is_online=False` and the connection is reset; no impact on the rotation. The reverse direction is manual only (`restore_from_sheet:195`). | `rotation_sheet_id`, `rotation_credentials_file`, `rotation_sync_interval` |
| **PerfSampler + GPU auto-pin** | Daemon thread at 1 Hz (`SAMPLE_INTERVAL_S`, ring of 300) | `perf_sampler.py:start:372`, `_run:395`, `_maybe_autopin:412`, `apply_toggle:311`; before/after_request `/status` latency hook `app.py:204-216` | engine perf, `/sys` GPU, `/proc`, `/tmp/kj-overlay-perf.json`, VNC connections, temperature | ring buffer; `~/kjdata/perf_recordings/*.jsonl` when a recording session is active (`perf_recorder.py:176`); **GPU clock pinned on the play edge, unpinned on the stop edge** | `sudo set-gpu-clock.sh pin/unpin` | Errors are swallowed per tick. | `auto_pin_gpu_during_playback` (default True), `perf_recordings_dir` |
| **Debounced volume persistence** | `threading.Timer(2.0)` reset on each `/volume` | `routes.py:_debounced_save_volumes:520-528`, `_do_save_volumes:514` | none | `config.json` `karaoke_volume`/`filler_volume` | none | none | none |
| **Master-sync "Sync Masters" button** | KJ-initiated daemon thread; shares the flock with the timer | `routes.py:master_sync_run:1356-1382` → `scripts/sync_masters.py:run_master_sync_now:225` | GCS | see the systemd table | gcloud | returns `busy` if the timer holds the lock | `master_sync_enabled` |
| **Media rescan (poked by syncs)** | Synchronous `POST /rescan`, called by the master-sync timer on **any** change | `routes.py:handle_rescan:1324-1335` → `media.scan:264` | all `media_folders` (full `os.walk`) | media index JSON + `media_library`; **also replaces `app.kj_config` with a fresh `load_config()`** (other components keep their old cfg dict) | none | A full walk in a request thread during a show (IO load). A config reload mid-show can diverge from the component configs. | `master_sync_rescan_url` |
| **Catalog-mirror reload** | `POST /catalog-mirror/reload` from the catalog sync script | `routes.py:1311-1322` → `catalog_mirror.reload:82` | new sqlite DB | connection swap | none | Local search is used only while `is_usable()` (`catalog_mirror.py:112`: exists, fresh ≤8 days, normalizer matches); otherwise it falls back to the Cloud Function. | `catalog_mirror_enabled`, `catalog_mirror_max_age_days` |
| **Preview HLS transcode + cache eviction** | On demand (KJ or singer preview). One active ffmpeg (`nice 19 ionice -c3`); a watcher thread marks `.done`. LRU eviction runs after each resolve. Token GC uses a TTL. | `preview_transcode.py:ensure_hls:71-135` (`_watch` thread `:97-115`), `preview_cache.py:evict_if_needed:132`, `preview.py:207,273,284,_gc:339` | the source file | preview-cache dir | ffmpeg; Divebar/GCS downloads | A new preview kills the in-flight transcode. CPU during the show at low priority. | `preview_transcode_height/preset`, `preview_cache_dir`, cache max bytes |
| **In-process TTL caches** | Lazy | `divebar.py:_SEARCH_CACHE_TTL=300:28`; `youtube_health.py:_get_ytdlp_latest:162` (24h PyPI check) | none | memory | CF / PyPI | none | none |
| **Browser-mode auto-kill + state sync** | `/play` kills Chromium if it is running (`routes.py:1044-1052`); `/status` resyncs the `_browser_mode` flag from the process (`routes.py:1484-1491`) | `chromium.py:kill:460`; YouTube auto-setup thread `chromium.py:_youtube_auto_setup:382-458` (CDP, retries) | process table | global `_browser_mode` | CDP | none | none |
| **Audio monitor (HDMI capture stream)** | KJ-initiated (`routes.py:2791-2806`, spawns a thread); stream drain thread (`audio_monitor.py:124`) | `audio_monitor.py:start:57` → **`coordinator.restart_instances()`** (`:71`) | PipeWire | engine audio backend, PipeWire card profile | pactl, parec, ffmpeg | **Starting or stopping it kills and relaunches both players, which cuts any playing song.** | manual only |
| **HTTP→HTTPS redirect listener** | Daemon thread; start_app only, when TLS is configured and not `behind_proxy` | `app.py:_start_http_redirect_thread:53-87` (`:590`) | none | none | none | Logged if port 80 is in use (Caddy owns :80 when behind the proxy). | `behind_proxy`, `tls_cert/key` |
| **websockify (VNC bridge)** | `Popen` at boot | `app.py:414-435` | none | child process | none | Not supervised: if it dies, there is no VNC preview until restart. Killed by sleep mode. | `websockify_enabled`, `websockify_port`, `vnc_target` |
| **Public-host guard + root rewrite** | `before_request` hook + WSGI middleware on every request | `sing.py:install_host_guard:309-326` (404 for non-`sing.*` endpoints on public hosts), `install_public_host_rewriter:334-359` (prefix `/sing`) | `kj_config` public hosts | none | none | none | public host list in config |
| **Sleep-mode gate** | `_check_sleep_mode()` on `/play` and other control routes (`routes.py:52,554,604,979,1413,5555,5599`) | `sleep_mode.py:enter_sleep:48`, `exit_sleep:117`; `sleep-enter.sh` / `sleep-exit.sh` | `/tmp/kj-sleep-mode`, `/tmp/kj-sleep-state.json` | stops players and websockify; the script stops `overlay-display`, `rotation-display`, `x11vnc`, cups, bluetooth, ModemManager, avahi, dropbox, **`kj-autodeploy`**; DPMS off; unmounts SSD `/media/nomad/Nomad4TBOne`; power-saver. Exit restores from the state file. | systemctl, umount, xset, powerprofilesctl | In-process threads (GenPoller, SheetSync, PerfSampler) and the systemd timers **keep running** during sleep. | manual (`/sleep/*` routes) |
| **Operator-initiated async system actions** | Threads after response: restart app `routes.py:2824-2833`, `/system/update` (git pull + restart) `:2838-2862`, reboot `:2945`, shutdown `:2959`, yt-dlp upgrade + restart `:2187-2203`, audio device and AV reset → `restart_instances` `:1694,1797,2744,2778` | none | none | service or processes | systemctl, git | Each of these interrupts playback. | none |

### 5.2 systemd units, timers, host processes and deploy loop

| Unit | Type / cadence | Runs | Effect | Live-night impact | Kill switch |
|---|---|---|---|---|---|
| `kj-controller.service` (`docs/MINIPC-SETUP.md:503-527`) | simple, `Restart=always` (5s) | `ExecStartPre=+fix-hdmi-audio.sh` (root), then `venv/bin/python app.py` (`start_app`) | the whole app | `KillMode=process`: mpv and VLC children **survive** restarts and are re-attached by `try_reconnect` (`playback.py:261-262`, `mpv_manager.py:344`). The download queue, push debounce and tier-2 queue are lost. | `systemctl stop` |
| `kj-autodeploy.service` (`docs/MINIPC-SETUP.md:546-563`) | simple, `Restart=always` (30s) | `auto-deploy.sh`: `while true` loop, `git fetch`, compare HEAD with `origin/main`, **sleep 60s** | `git reset --hard origin/main`. If `requirements.txt` changed, `pip install`. **If any `*.py` diff: `sudo systemctl restart kj-controller`.** Always restarts `rotation-display` and `overlay-display` if they are active. If `catalog.py` changed: `POST /catalog/build` after 15s. | **There is NO automatic live-show detection in the script** (`auto-deploy.sh:15-59`). A backend merge restarts the service mid-show (players survive, but see above). Operational practice is to stop the unit manually before a show; sleep-enter also stops it. | `/system/autodeploy` POST (`routes.py:2882-2895`, `systemctl enable/disable --now`); GET status `:2871` |
| `nomad-master-sync.timer` → `.service` (`deploy/`) | `OnBootSec=1min`, `OnUnitActiveSec=60s`, `Persistent`; oneshot, `Nice=10`, IO idle, flock `/tmp/nomad-master-sync.lock` | `python -m scripts.sync_masters` (`scripts/sync_masters.py:main:256`) | `gcloud storage rsync` GCS NOMAD-720p to `{download_folder}/NOMAD-720p`; guarded reconcile-deletes (`_reconcile_deletions:57`, max-deletes cap); **on change, `POST /rescan`** (`:171-183`). Then an additive vocals-guide sync (`_vocals_config_view:187`, no rescan). | Every minute during the show: rsync IO, plus a full media rescan whenever new masters land. | `master_sync_enabled` (default False), `master_sync_delete_removed`, `master_sync_delete_dry_run`, `master_sync_max_deletes`, `vocals_sync_enabled` |
| `nomad-catalog-sync.timer` → `.service` (`deploy/`) | `OnCalendar=12:15 UTC` daily, **`OnBootSec=10min`**, `Persistent`, random delay 300s; `Nice=10`, IO idle, flock `/tmp/nomad-catalog-sync.lock` | `python -m scripts.sync_catalogs` (`scripts/sync_catalogs.py:main:148`, `run_sync:84`) | downloads Divebar (HTTPS) plus KN community and full (`gcloud cp`); skips if the sha256 hashes are unchanged; else `build_mirror_db` (atomic swap), then `POST /catalog-mirror/reload` | **The boot run lands about 10 minutes after power-on, which is typically early in a show**: a rebuild of about 415K rows competes for CPU and IO. | `catalog_mirror_enabled` |
| `overlay-display.service` (`desktop/overlay-display.service`) | simple, `Restart=always` (5s) | `desktop/overlay_engine.py`, GTK/Cairo at 30 FPS | polls `overlays.json` mtime every 1s (`overlay_engine.py:34,206,296`); reads `/tmp/rotation_cache.json` (`rotation_source.py`); writes `/tmp/kj-overlay-perf.json` every 1s (`:301`) | Hides or shows overlays depending on `karaoke_playing` (set by `/play`, stop, fadeout and on_karaoke_end). | PerfSampler toggle `apply_toggle('overlay')` (`perf_sampler.py:318-321`) |
| `rotation-display.service` (`docs/MINIPC-SETUP.md:572-590`) | conky | `desktop/rotation.conkyrc` (**no longer in the repo**, so legacy and likely inactive) | none | none | none |
| `caddy` (`deploy/Caddyfile`, `deploy/install-caddy.sh`) | always on | TLS on :443 → `127.0.0.1:5001`; serves `/static/*` directly; :80 redirects | none | Static assets are served by Caddy, not Flask. | none |
| `cloudflared`, `x11vnc`, `avahi-daemon` (`docs/MINIPC-SETUP.md:35,723-742`) | always on | tunnel, VNC, mDNS | none | none | none |
| `playability-batch` / `playability-monitor` (transient `systemd-run`, `scripts/playability-run/start.sh`) | manual | full-library playability batch (`playability_batch.py`), Nice 19, CPU quota 200%, own Xvfb; monitor samples every 5 minutes | JSONL manifest | The operator must pause it before a show (`pause.sh`). | manual |
| No cron jobs | none | none | none | none | none |

### 5.3 Client-side automatic behaviours (browser timers; they only run while a page is open)

These are genuinely "automatic" server mutations, but **they are driven by the KJ's browser**. A simulation must either run the JS or reproduce these calls.

| Behaviour | Trigger / cadence | Code | Server effect |
|---|---|---|---|
| Status poll | `setInterval(updateStatus, 2000)` `static/app.js:4827` | `updateStatus:1631` | `GET /status`. Every 15th tick (30s), `GET /rotation/gen-status` (`:1725`, read-only) and a refetch if gen entries are active. VNC auto-pause on the play edge (`perfMaybeAutoPauseVnc:5186`). |
| Rotation poll | `setInterval(fetchRotation, 10000)` `:4828` | `fetchRotation:5493` | `GET /rotation`, which rewrites `/tmp/rotation_cache.json` |
| System stats / sheet-sync status / pending requests / perf stream | 5s `:4949`, 30s `:8123`, 5s `:9814`, 1s while the perf panel is open `:5389` | none | reads only |
| **Auto-text next singer** | KJ presses Play on **slot 1** (`playAndAdvanceRotation:7515`), arming a **20s** `setTimeout` (`AUTO_TEXT_NEXT_DELAY_MS:7532`, `armAutoTextNextSinger:7539`). Any other play cancels it. On fire: client-side guards (`maybeAutoTextNextSinger:7551`), then `POST /rotation/sms/auto-send` | server `routes.py:sms_auto_send:4311-4411`: re-checks the toggle, that the target is slot 2, that slot 1 is `playing_entry_id`, that `current_playing_path == slot1.file_path`, that nothing was sent before (`sms_store.get_latest_for_entry`), the phone (`_resolve_sms_target:4044`) and opt-out, then `_perform_sms_send:4200` | `sms_log` row; Telnyx API. Toggle: `auto_sms_next` (`routes.py:6304-6310`); requires `TELNYX_API_KEY` + `TELNYX_FROM_NUMBER` (`app.py:281-287`). |
| **Cancelled-entry auto-remove** | When a singer cancels, the row gets status `Cancelled` (`sing.py:1603-1617`). The KJ page then pulses for 4s plus a 450ms leave animation (`app.js:5558-5559`, `maybeAutoRemoveCancelled:5565`) and calls `POST /rotation/delete` (`autoRemoveCancelledEntry:5594`) | none | The entry is deleted, with an undo checkpoint and a push. Each open KJ tab fires its own delete. |
| Download auto-ack | 3s after a completed download is seen (`app.js:386`) | none | `POST /download/ack` |
| Singer page polls (`static-sing/sing.js`) | `/now` 15s (`:593`); rotation tab 30s (`:743,794`); my-requests 15s (`:3031`); My-songs bar 20s (`:3169`); tip claims 15s (`:1097`) | none | reads (and `get_rotation` cache writes) |

### 5.x Song-end / automatic advance sequence

**A. The current song ends naturally (EOF).**
1. The monitor thread detects it: mpv `end-file`/`eof` (`mpv_manager.py:1009`) or VLC `stopped` on the 2s poll (`vlc.py:676`).
2. `_handle_karaoke_ended` (`mpv_manager.py:1034`) sets `active=False` and `current_path=None`, resets pitch to 0, and writes the state file.
3. `ensure_released()` blocks up to about 1.15s until mpv is idle, which frees ALSA.
4. `on_karaoke_end` (`app.py:178`) sets overlay `karaoke_playing=False`, which rewrites `overlays.json`. The overlay engine picks that up within about 1s.
5. `coordinator.fade_in_filler()` → `filler.fade_in` fades filler up over 1.5s. The auto-heal verify runs 4s later.
6. On the next 2s `/status` poll the browser sees `state:stopped` and `current_playing_path:null`. That makes any pending auto-text guard fail, and the VNC preview resumes if it was auto-paused.
7. PerfSampler sees the playing→idle edge and **unpins the GPU clock**.

**Nothing else happens automatically at song end.**
- The rotation is **not** changed: the entry stays `Now Singing`, with no `Done` and no `done_at`.
- No stats are recorded.
- No push, SMS or Sheets write happens, apart from the unconditional 30s Sheets tick.
- There is no auto-advance or auto-play.

The KJ has to press **Done** (`app.js:6087` → `POST /rotation/status` with `Done`). That stamps `done_at` (`rotation_store.py:411-418`), which drives last-sang and wait estimates, and fires `_after_mutation`: rev bump, display cache, and a debounced push, which moves the ladder so new pos 1-2 get `up_next` and pos 3 gets `up_in_2`.

**B. The KJ plays the next singer: Play on the row at `idx` (`app.js:playAndAdvanceRotation:7515`).**
1. `POST /play {file_path, entry_id}` (`routes.py:975`). This blocks in sleep mode, validates the path, handles zip/cdg extraction, auto-kills Chromium, and resolves the vocals guide. It then spawns a `play_video` thread (filler fade-out → `ensure_stopped` → loadfile → `active=True` → overlay `karaoke_playing=True` → progress verify for 10s) and **records the play stat at start** (`play_events`).
2. At the same time, `POST /rotation/status {updates:[{entry→Now Singing},{entries[idx+1]→Up Next}]}` runs as one undo step (`rotation.update_statuses:103`). Exclusivity rules reset any *other* `Now Singing`/`Up Next` rows to **`Waiting`**, not Done (`rotation_store.py:392-405`). So if the KJ skipped Done, the previous singer silently goes back into the queue.
3. `_after_mutation` then bumps the rev, writes the display cache, and 0.5s later the push dispatch sends the `now_singing` push to the singer's subscriptions and `up_next` / `up_in_2` to others. This depends on dedup against `last_sent_state`.
4. If `idx==0`, the 20s auto-text timer is armed and then `POST /rotation/sms/auto-send` texts slot 2 (server-validated). If `idx!=0`, any armed timer is cancelled.
5. PerfSampler sees the idle→playing edge and pins the GPU. The browser auto-pauses the VNC preview.
6. Separately, if auto-reorder is on, it runs **only** when a new entry is added or approved. Status changes never trigger a reorder.

**C. KJ Stop or Fade Out.** Stop runs `stop` + `ensure_released` + overlay off + filler fade-in synchronously (`routes.py:1110-1114`). Fade Out does the same after a timed fade on a thread (`playback.py:467`). Neither fires `on_karaoke_end`, and neither touches rotation, stats, push or SMS.

### 5.y Event hooks (callback registrations)

| Source event | Hook | Registered at | Downstream |
|---|---|---|---|
| Natural EOF (monitor) | `player.on_karaoke_end` | `app.py:401` (start_app only); preserved `playback.py:220-226,303-313` | overlay off, filler fade-in |
| Engine process died | `player.on_engine_died = coordinator._handle_engine_died` | `playback.py:95` (every `_build_player`) | crash record → `engine-recovery` thread `restart_instances`, or escalate. `player_alert` in `/status`; ack via route (tested in `test_routes_player_health.py`) |
| Any RotationManager mutation | `_before_mutation` / `_after_mutation` | `rotation.py:390,402` | undo checkpoint; rev bump; `/tmp/rotation_cache.json`; `push_dispatcher.notify_rotation_changed` (0.5s debounce) |
| `rotation.push_dispatcher` injection | attribute set | `app.py:320` / `app.py:509` | Web Push ladder |
| Sing request approve / reject | `notify_request_decision` | `routes.py:6484,6532` | immediate push |
| Download-worker fallback outcome | `_notify_sing_outcome` | `routes.py:5981` (broken lookup, see 5.1) | intended `resolved_alt` / `unavailable` push, never sent |
| New entry (KJ add, KJ approve, auto-approve) | `maybe_auto_reorder` | `routes.py:3721,6491`, `sing.py:1077` | Auto Order → `reorder_by_ids` → `_after_mutation` |
| Tip confirmed with bump | `run_auto_order` | `routes.py:6404-6409` | re-weave |
| `/rotation/link` success | `_enqueue_tier2` | `routes.py:4004` | background render check → `playability_warning` |
| Gen job status change / complete | GenPoller → `rotation.set_gen_status` / `complete_gen_job` | `gen_poller.py:38,71` | `_after_mutation` (push, cache) |
| Flask request lifecycle | `before_request` `_perf_mark_start`, `after_request` `/status` latency | `app.py:204-216` | PerfSampler |
| Flask request lifecycle | `before_request` `_sing_host_guard`; WSGI rewriter | `sing.py:312`, `sing.py:346-359` | public-host routing |
| Master-sync or catalog-sync completion | HTTP poke `POST /rescan`, `POST /catalog-mirror/reload` | `scripts/sync_masters.py:176-181`, `scripts/sync_catalogs.py:131-140` | media rescan + config reload; mirror reopen |
| Inbound Telnyx webhook | `POST /sing/telnyx/webhook` (Ed25519-verified) | `sing.py:1136` | `sms_log` delivery status, `sms_opt_outs` |
| Singer cancel | `rotation.update_status(...,'Cancelled')` | `sing.py:1603-1617` | the KJ browser auto-deletes after about 4.5s (5.3) |
| Filler fade-in | `_verify_playing` thread | `filler.py:268` | auto-relaunch |
| Sheets (not a hook) | no hook: a 30s loop | `rotation.py:417-422` (explicit no-op) | none |

### 5.z Test coverage of background behaviours (`kj-controller/tests/`)

| Behaviour | Tests |
|---|---|
| GenPoller | `unit/test_gen_poller.py` (poll_once/complete; the thread loop is not exercised live) |
| SheetSync | `unit/test_rotation_sync.py`, `integration/test_rotation_routes.py` |
| Push dispatcher + rotation hook | `unit/test_push_dispatcher.py`, `integration/test_rotation_push_hook.py`, `e2e/test_sing_push_e2e.py` |
| Auto Order / auto-reorder | `unit/test_auto_order.py`, `integration/test_auto_order_routes.py`, `unit/test_sing_store.py` (flag); sims in `scripts/auto_order_sim.py`, `scripts/auto_order_review.py` |
| Auto-text next singer (server) | `integration/test_sms_routes.py:539+` (auto-send guards), `integration/test_sing_admin_routes.py` (toggle). **The client 20s timer is untested** (no e2e). |
| Song-end callback / EOF | `unit/test_app_callbacks.py` (`_make_on_karaoke_end`), `unit/test_mpv_karaoke_player.py` (`end-file`/`idle-active`, `_notify_if_dead`), `unit/test_vlc_karaoke_player.py` (monitor), `unit/test_playback_coordinator.py` |
| Crash guard / recovery | `unit/test_playback_coordinator.py`, `unit/test_routes_player_health.py` |
| Filler auto-heal | `unit/test_filler.py` |
| Play stats | `unit/test_routes_stats.py`, `unit/test_stats_store.py`, `integration/test_sing_my_stats.py` |
| Display cache | `unit/test_rotation.py` |
| Cancelled auto-remove | `e2e/test_rotation_e2e.py:326` (`test_cancelled_entry_pulses_then_auto_removes`) |
| Download worker + sing fallback | `integration/test_sing_fallback.py`, `unit/test_download_gate.py`, `integration/test_download_link_routes.py` (**outcome pushes untested**) |
| Tier-2 | `unit/test_playability_tier2.py`, `unit/test_link_gate.py` |
| Master sync | `unit/test_sync_masters.py`, `unit/test_routes_master_sync.py` |
| Catalog mirror sync | `test_catalog_mirror.py` (incl. `run_sync`), `unit/test_catalog_build_crash_safety.py` |
| PerfSampler / autopin | `unit/test_perf_sampler.py`, `unit/test_perf_recorder.py`, `integration/test_perf_routes.py` |
| Preview transcode / cache | `unit/test_preview_transcode.py`, `unit/test_preview_cache.py`, `unit/test_preview_service.py`, `integration/test_preview_routes.py` |
| Sleep mode | `unit/test_sleep_mode.py`, `integration/test_sleep_mode_routes.py` |
| Audio monitor | `unit/test_audio_monitor.py` |
| Host guard | `integration/test_host_guard.py` |
| Autodeploy route | `integration/test_routes.py:918` (`/system/autodeploy` status only). **`auto-deploy.sh` is untested.** |
| Overlay engine | `desktop/tests/test_overlay_engine.py`, `test_rotation_source.py`, `test_overlay_painters.py` |
| YouTube health / Chromium / zip | `unit/test_youtube_health.py`, `unit/test_chromium.py`, `unit/test_zip_playback.py` |
| **No tests** | volume debounce timer; HTTP redirect thread; websockify spawn; the `sync_catalogs.py` systemd timing; the client auto-text timer; `_notify_sing_outcome` |

## 6. Endpoint inventory

Generated programmatically by parsing every `@routes_bp.route` / `@sing_bp.route` decorator in `kj-controller/routes.py` and `kj-controller/sing.py` (the sing blueprint has `url_prefix="/sing"`; on the public host `install_public_host_rewriter` (sing.py:334) also mounts it at `/`). `app.py` defines no routes of its own, only `before_request`/`after_request` hooks (host guard sing.py:309; perf latency app.py:204).

**Totals: 176 routes — 144 KJ/admin (routes.py), 32 singer (sing.py; 1 of which, `/sing/telnyx/webhook`, is a system callback). Mutating: 95 change persisted, device, or external state ("yes"); 6 only touch in-memory state; 3 GETs have side effects; the rest are read-only.** Nine POSTs are read-only (searches, SMS preview/detail, media-info, validate), so "POST" does not mean "mutating".

Public-host rule: on `sing_public_host` (config), every non-`sing.*` endpoint returns 404 (sing.py:309). On the LAN/admin host, both blueprints are reachable, with no auth on KJ routes.

Tests column: test files whose source contains the literal path (f-string ids included). `test_host_guard` is excluded because it only checks reachability. "none" means no test references the path at all. A reference does not mean the behaviour is covered in depth; see §7.

| Method | Path | Handler | Actor | Mutating | Category | Tests referencing |
|---|---|---|---|---|---|---|
| GET | `/` | `routes.py:index` (L531) | KJ | no | UI shell | test_frontend, test_routes, test_catalog (+2) |
| POST | `/download` | `routes.py:handle_download` (L551) | KJ | yes | Downloads | test_routes, test_sleep_mode_routes, test_dedup_skip |
| POST | `/upload` | `routes.py:handle_upload` (L601) | KJ | yes | Downloads | test_upload, test_upload_gate |
| POST | `/download/cancel` | `routes.py:cancel_download` (L898) | KJ | yes | Downloads | test_download_link_routes, test_routes |
| POST | `/download/ack` | `routes.py:ack_download` (L918) | KJ | yes | Downloads | test_download_link_routes, test_routes |
| POST | `/play` | `routes.py:handle_play` (L976) | KJ | yes | Playback | test_browser_mode_routes, test_routes, test_search_routes (+1) |
| POST | `/seek` | `routes.py:handle_seek` (L1073) | KJ | yes | Playback | test_routes |
| POST | `/control` | `routes.py:handle_control` (L1087) | KJ | yes | Playback | test_frontend, test_routes |
| POST | `/volume` | `routes.py:handle_volume` (L1132) | KJ | yes | Playback | test_routes |
| POST | `/pitch` | `routes.py:handle_pitch` (L1165) | KJ | yes | Playback | test_pitch_routes |
| GET | `/renderer` | `routes.py:get_renderer` (L1182) | KJ | no | Playback | test_renderer_routes |
| POST | `/renderer` | `routes.py:set_renderer` (L1188) | KJ | yes | Playback | test_renderer_routes |
| GET | `/media` | `routes.py:list_media` (L1208) | KJ | no | Library | test_routes |
| POST | `/media/metadata` | `routes.py:set_media_metadata` (L1214) | KJ | yes | Library | test_media_metadata |
| POST | `/media/info` | `routes.py:media_info` (L1263) | KJ | no | Library | test_media_info_route |
| POST | `/delete` | `routes.py:delete_media` (L1283) | KJ | yes | Library | test_routes |
| POST | `/catalog-mirror/reload` | `routes.py:catalog_mirror_reload` (L1311) | KJ | yes | Catalog | test_catalog_mirror |
| POST | `/rescan` | `routes.py:handle_rescan` (L1325) | KJ | yes | Library | test_routes |
| POST | `/master-sync/run` | `routes.py:master_sync_run` (L1355) | KJ | yes | Catalog | test_routes_master_sync |
| GET | `/master-sync/status` | `routes.py:master_sync_status` (L1386) | KJ | no | Catalog | test_routes_master_sync |
| GET | `/filler_music` | `routes.py:list_filler_music` (L1394) | KJ | no | Filler | test_routes, test_sleep_mode_routes |
| POST | `/filler_music` | `routes.py:set_filler_music` (L1410) | KJ | yes | Filler | test_routes, test_sleep_mode_routes |
| GET | `/status` | `routes.py:get_status` (L1464) | KJ | no | Status | test_browser_mode_routes, test_pitch_routes, test_renderer_routes (+2) |
| POST | `/fix_audio` | `routes.py:fix_audio` (L1546) | KJ | yes | AV | test_routes |
| POST | `/player-crash/ack` | `routes.py:player_crash_ack` (L1557) | KJ | yes | Playback | test_routes_player_health |
| GET | `/audio_device` | `routes.py:get_audio_device` (L1566) | KJ | no | AV | test_av_routes, test_routes |
| GET | `/search` | `routes.py:search_catalog` (L1575) | KJ | no | Search | test_search_routes |
| GET | `/library/search` | `routes.py:library_search` (L1599) | KJ | no | Search | test_search_endpoints_unified |
| GET | `/catalog/stats` | `routes.py:catalog_stats` (L1635) | KJ | no | Catalog | test_search_routes |
| POST | `/catalog/build` | `routes.py:catalog_build` (L1646) | KJ | yes | Catalog | test_search_routes |
| POST | `/audio_device` | `routes.py:set_audio_device` (L1678) | KJ | yes | AV | test_av_routes, test_routes |
| POST | `/audio/scan` | `routes.py:scan_hdmi_audio` (L1698) | KJ | yes | AV | **none** |
| POST | `/audio/switch-hdmi` | `routes.py:switch_hdmi_audio` (L1760) | KJ | yes | AV | test_av_routes |
| GET | `/overlays` | `routes.py:list_overlays` (L1803) | KJ | no | Overlays | test_overlay_presets_route, test_overlay_routes |
| POST | `/overlays/import` | `routes.py:import_overlays` (L1809) | KJ | yes | Overlays | test_overlay_routes |
| POST | `/overlays` | `routes.py:create_overlay` (L1822) | KJ | yes | Overlays | test_overlay_presets_route, test_overlay_routes |
| POST | `/overlays/presets/<preset_name>` | `routes.py:create_overlay_preset` (L1837) | KJ | yes | Overlays | test_overlay_presets_route |
| GET | `/overlays/<overlay_id>` | `routes.py:get_overlay` (L1866) | KJ | no | Overlays | test_overlay_presets_route, test_overlay_routes |
| PUT | `/overlays/<overlay_id>` | `routes.py:update_overlay` (L1875) | KJ | yes | Overlays | test_overlay_presets_route, test_overlay_routes |
| DELETE | `/overlays/<overlay_id>` | `routes.py:delete_overlay` (L1889) | KJ | yes | Overlays | test_overlay_presets_route, test_overlay_routes |
| POST | `/overlays/<overlay_id>/toggle` | `routes.py:toggle_overlay` (L1897) | KJ | yes | Overlays | test_overlay_routes |
| POST | `/overlays/<overlay_id>/toggle-video` | `routes.py:toggle_overlay_video` (L1906) | KJ | yes | Overlays | test_overlay_routes |
| GET | `/wallpaper` | `routes.py:get_wallpaper` (L1917) | KJ | no | Overlays | **none** |
| POST | `/wallpaper` | `routes.py:upload_wallpaper` (L1934) | KJ | yes | Overlays | **none** |
| POST | `/karaoke-nerds/search` | `routes.py:kn_search` (L2013) | KJ | no | KN | test_kn_panel_grouped_search, test_routes, test_search_endpoints_unified |
| GET | `/karaoke-nerds/config` | `routes.py:kn_get_config` (L2048) | KJ | no | KN | test_routes, test_karaoke_nerds_config |
| POST | `/karaoke-nerds/config` | `routes.py:kn_set_config` (L2077) | KJ | yes | KN | test_routes, test_karaoke_nerds_config |
| POST | `/youtube/search` | `routes.py:yt_search` (L2125) | KJ | no | YouTube | test_routes |
| GET | `/youtube/status` | `routes.py:youtube_status` (L2137) | KJ | no | YouTube | test_youtube_routes |
| POST | `/youtube/cookies` | `routes.py:youtube_upload_cookies` (L2145) | KJ | yes | YouTube | test_youtube_routes |
| DELETE | `/youtube/cookies` | `routes.py:youtube_delete_cookies` (L2170) | KJ | yes | YouTube | test_youtube_routes |
| POST | `/youtube/upgrade-ytdlp` | `routes.py:youtube_upgrade_ytdlp` (L2186) | KJ | yes | YouTube | test_youtube_routes |
| POST | `/divebar/search` | `routes.py:divebar_search` (L2209) | KJ | no | Divebar | **none** |
| POST | `/divebar/kn-lookup` | `routes.py:divebar_kn_lookup` (L2226) | KJ | no | Divebar | **none** |
| GET | `/divebar/status` | `routes.py:divebar_status` (L2242) | KJ | no | Divebar | **none** |
| POST | `/divebar/refresh` | `routes.py:divebar_refresh` (L2256) | KJ | yes | Divebar | test_routes |
| POST | `/divebar/download` | `routes.py:divebar_download` (L2278) | KJ | yes | Divebar | test_routes |
| GET | `/display/resolution` | `routes.py:get_display_resolution` (L2377) | KJ | no | AV | test_av_routes |
| POST | `/display/resolution` | `routes.py:set_display_resolution` (L2390) | KJ | yes | AV | test_av_routes |
| GET | `/av/status` | `routes.py:av_status` (L2644) | KJ | no | AV | test_av_routes, test_audio_monitor |
| POST | `/av/browser-audio` | `routes.py:av_set_browser_audio` (L2687) | KJ | yes | AV | test_av_routes |
| POST | `/av/reset` | `routes.py:av_reset` (L2710) | KJ | yes | AV | test_av_routes, test_audio_monitor |
| POST | `/av/vlc-device` | `routes.py:av_set_vlc_device` (L2750) | KJ | yes | AV | test_av_routes |
| GET | `/audio-monitor/status` | `routes.py:audio_monitor_status` (L2784) | KJ | no | AV | test_audio_monitor |
| POST | `/audio-monitor/start` | `routes.py:audio_monitor_start` (L2790) | KJ | in-mem | AV | test_audio_monitor |
| POST | `/audio-monitor/stop` | `routes.py:audio_monitor_stop` (L2800) | KJ | in-mem | AV | test_audio_monitor |
| GET | `/audio-monitor/stream` | `routes.py:audio_monitor_stream` (L2810) | KJ | no | AV | test_audio_monitor |
| POST | `/system/restart-app` | `routes.py:restart_app` (L2823) | KJ | yes | System | test_routes |
| POST | `/system/update` | `routes.py:system_update` (L2837) | KJ | yes | System | test_routes |
| GET | `/system/autodeploy` | `routes.py:autodeploy_status` (L2870) | KJ | no | System | test_routes |
| POST | `/system/autodeploy` | `routes.py:autodeploy_toggle` (L2881) | KJ | yes | System | test_routes |
| GET | `/system/sleep-mode` | `routes.py:sleep_mode_status` (L2903) | KJ | no | System | test_sing_admin_routes, test_sleep_mode_routes |
| POST | `/system/sleep-mode` | `routes.py:sleep_mode_toggle` (L2910) | KJ | yes | System | test_sing_admin_routes, test_sleep_mode_routes |
| POST | `/system/reboot` | `routes.py:system_reboot` (L2935) | KJ | yes | System | test_routes |
| POST | `/system/shutdown` | `routes.py:system_shutdown` (L2949) | KJ | yes | System | test_routes |
| GET | `/system/stats` | `routes.py:system_stats` (L2998) | KJ | no | System | test_routes_system_stats |
| GET | `/perf/stream` | `routes.py:perf_stream` (L3039) | KJ | no | Perf | test_perf_routes |
| POST | `/perf/toggle/<control>` | `routes.py:perf_toggle` (L3051) | KJ | in-mem | Perf | test_perf_routes |
| POST | `/perf/record/start` | `routes.py:perf_record_start` (L3074) | KJ | yes | Perf | test_perf_routes |
| POST | `/perf/record/stop` | `routes.py:perf_record_stop` (L3084) | KJ | yes | Perf | test_perf_routes |
| GET | `/perf/record/list` | `routes.py:perf_record_list` (L3093) | KJ | no | Perf | test_perf_routes |
| GET | `/perf/record/<session_id>/summary` | `routes.py:perf_record_summary` (L3102) | KJ | no | Perf | test_perf_routes |
| GET | `/perf/record/<session_id>/download` | `routes.py:perf_record_download` (L3114) | KJ | no | Perf | test_perf_routes |
| GET | `/rotation` | `routes.py:get_rotation` (L3566) | KJ | no | Rotation | test_rotation_e2e, test_rotation_routes, test_rotation_undo_routes (+2) |
| POST | `/rotation/status` | `routes.py:update_rotation_status` (L3588) | KJ | yes | Rotation | test_rotation_e2e, test_rotation_routes, test_rotation_undo_routes (+1) |
| POST | `/rotation/edit` | `routes.py:edit_rotation_entry` (L3638) | KJ | yes | Rotation | test_rotation_routes, test_sms_routes |
| POST | `/rotation/delete` | `routes.py:delete_rotation_entry` (L3675) | KJ | yes | Rotation | test_rotation_routes |
| POST | `/rotation/add` | `routes.py:add_rotation_entry` (L3701) | KJ | yes | Rotation | test_auto_order_routes, test_download_link_routes, test_gen_routes (+2) |
| POST | `/rotation/move` | `routes.py:move_rotation_entry` (L3729) | KJ | yes | Rotation | test_rotation_routes, test_sms_routes |
| POST | `/rotation/auto-order` | `routes.py:auto_order_rotation` (L3833) | KJ | yes | Rotation | test_auto_order_routes |
| POST | `/rotation/archive` | `routes.py:archive_rotation` (L3848) | KJ | yes | Night lifecycle | test_rotation_routes, test_sing_admin_routes |
| POST | `/rotation/link` | `routes.py:link_rotation_file` (L3959) | KJ | yes | Media linking | test_rotation_routes, test_link_gate |
| POST | `/rotation/unlink` | `routes.py:unlink_rotation_file` (L4013) | KJ | yes | Media linking | test_rotation_routes, test_sms_routes |
| POST | `/rotation/sms/preview` | `routes.py:sms_preview` (L4125) | KJ | no | SMS | test_sms_routes |
| POST | `/rotation/sms/detail` | `routes.py:sms_detail` (L4166) | KJ | no | SMS | test_sms_routes |
| POST | `/rotation/sms/send` | `routes.py:sms_send` (L4275) | KJ | yes | SMS | test_sms_routes |
| POST | `/rotation/sms/auto-send` | `routes.py:sms_auto_send` (L4311) | KJ | yes | SMS | test_sms_routes |
| POST | `/rotation/set-paid` | `routes.py:set_rotation_paid` (L4414) | KJ | yes | Rotation | test_rotation_routes, test_sms_routes |
| POST | `/rotation/set-priority` | `routes.py:set_rotation_priority` (L4457) | KJ | yes | Rotation | test_auto_order_routes |
| GET | `/rotation/sync-status` | `routes.py:rotation_sync_status` (L4494) | KJ | no | Night lifecycle | test_rotation_routes |
| POST | `/rotation/restore` | `routes.py:restore_rotation_from_sheet` (L4508) | KJ | yes | Night lifecycle | test_rotation_routes |
| POST | `/rotation/undo` | `routes.py:undo_rotation` (L4620) | KJ | yes | Undo | test_rotation_undo_routes |
| POST | `/rotation/redo` | `routes.py:redo_rotation` (L4626) | KJ | yes | Undo | test_rotation_undo_routes |
| POST | `/rotation/singer/rename` | `routes.py:rename_singer_route` (L4650) | KJ | yes | Singers | test_rotation_routes, test_sing_rename, test_footer_social_photo_consent |
| POST | `/rotation/singer/photo-consent` | `routes.py:singer_photo_consent_route` (L4668) | KJ | yes | Singers | test_footer_social_photo_consent |
| POST | `/rotation/singer/merge` | `routes.py:merge_singers_route` (L4689) | KJ | yes | Singers | test_rotation_routes, test_sing_rename |
| POST | `/rotation/singer/brb` | `routes.py:singer_brb_route` (L4717) | KJ | yes | Singers | test_rotation_routes |
| POST | `/rotation/singer/priority` | `routes.py:singer_priority_route` (L4735) | KJ | yes | Singers | test_auto_order_routes |
| POST | `/rotation/singer/remove` | `routes.py:remove_singer_route` (L4762) | KJ | yes | Singers | test_rotation_routes |
| POST | `/rotation/singer/restore` | `routes.py:restore_singer_route` (L4779) | KJ | yes | Singers | test_rotation_routes |
| POST | `/rotation/singer/split` | `routes.py:split_singer_route` (L4796) | KJ | yes | Singers | test_rotation_routes |
| GET | `/rotation/search` | `routes.py:rotation_search` (L5273) | KJ | no | Media linking | test_rotation_e2e, test_rotation_search, test_routes_stats |
| GET | `/playback/alternates` | `routes.py:playback_alternates` (L5292) | KJ | no | Media linking | test_search_routes |
| POST | `/rotation/download-and-link` | `routes.py:download_and_link_rotation` (L5351) | KJ | yes | Media linking | test_download_link_routes |
| POST | `/rotation/make` | `routes.py:make_rotation_entry` (L5482) | KJ | yes | Gen/MAKE | test_gen_routes |
| GET | `/rotation/gen-status` | `routes.py:rotation_gen_status` (L5526) | KJ | no | Gen/MAKE | test_gen_routes |
| POST | `/browser-mode/enable` | `routes.py:browser_mode_enable` (L5552) | KJ | yes | Browser mode | test_browser_mode_routes |
| POST | `/browser-mode/navigate` | `routes.py:browser_mode_navigate` (L5596) | KJ | yes | Browser mode | **none** |
| POST | `/browser-mode/disable` | `routes.py:browser_mode_disable` (L5632) | KJ | yes | Browser mode | test_browser_mode_routes |
| GET | `/rotation/requests` | `routes.py:list_sing_requests` (L6191) | KJ | no | Requests (KJ) | test_sing_admin_routes, test_sing_kj_pick_e2e |
| GET | `/rotation/requests/config` | `routes.py:get_sing_config` (L6212) | KJ | no | Requests (KJ) | test_rotation_e2e, test_auto_order_routes, test_sing_admin_routes (+6) |
| POST | `/rotation/requests/config` | `routes.py:update_sing_config` (L6254) | KJ | yes | Requests (KJ) | test_rotation_e2e, test_auto_order_routes, test_sing_admin_routes (+6) |
| GET | `/rotation/requests/qr.svg` | `routes.py:sing_qr_svg` (L6357) | KJ | no | Requests (KJ) | test_sing_admin_routes |
| POST | `/rotation/requests/<int:req_id>/approve` | `routes.py:approve_sing_request_route` (L6381) | KJ | yes | Requests (KJ) | test_sing_admin_routes, test_sing_kj_pick_e2e, test_sing_public_routes (+1) |
| POST | `/rotation/requests/<int:req_id>/edit` | `routes.py:edit_sing_request_route` (L6499) | KJ | yes | Requests (KJ) | test_sing_admin_routes |
| POST | `/rotation/requests/<int:req_id>/reject` | `routes.py:reject_sing_request_route` (L6519) | KJ | yes | Requests (KJ) | test_sing_admin_routes, test_sing_kj_pick_e2e, test_sing_tips |
| POST | `/preview/resolve` | `routes.py:preview_resolve` (L6549) | KJ | yes | Preview | test_preview_routes |
| POST | `/preview/close` | `routes.py:preview_close` (L6559) | KJ | in-mem | Preview | test_preview_routes |
| GET | `/preview/stream/<token>` | `routes.py:preview_stream` (L6582) | KJ | side (transcode/cache fill) | Preview | test_preview_routes |
| GET | `/preview/cdg/<token>/<part>` | `routes.py:preview_cdg` (L6621) | KJ | no | Preview | **none** |
| GET | `/preview/hls/<token>/<path:name>` | `routes.py:preview_hls` (L6630) | KJ | no | Preview | test_preview_routes |
| POST | `/media/note` | `routes.py:media_note` (L6639) | KJ | yes | Library | test_routes_stats |
| GET | `/media/note-labels` | `routes.py:media_note_labels` (L6654) | KJ | no | Library | test_routes_stats |
| GET | `/stats/top-songs` | `routes.py:stats_top_songs` (L6660) | KJ | no | Stats | test_routes_stats |
| GET | `/stats/singers` | `routes.py:stats_singers` (L6674) | KJ | no | Stats | test_routes_stats |
| GET | `/stats/overview` | `routes.py:stats_overview` (L6687) | KJ | no | Stats | test_routes_stats |
| GET | `/stats/top-artists` | `routes.py:stats_top_artists` (L6696) | KJ | no | Stats | test_routes_stats |
| GET | `/stats/artist-songs` | `routes.py:stats_artist_songs` (L6709) | KJ | no | Stats | test_routes_stats |
| GET | `/stats/singer-songs` | `routes.py:stats_singer_songs` (L6723) | KJ | no | Stats | test_routes_stats |
| GET | `/stats/singer-song-history` | `routes.py:stats_singer_song_history` (L6737) | KJ | no | Stats | test_routes_stats |
| GET | `/stats/song-history` | `routes.py:stats_song_history` (L6751) | KJ | no | Stats | test_routes_stats |
| GET | `/stats/nights` | `routes.py:stats_nights` (L6765) | KJ | no | Stats | test_routes_stats |
| GET | `/stats/night-setlist` | `routes.py:stats_night_setlist` (L6777) | KJ | no | Stats | test_routes_stats |
| GET | `/stats/most-repeated` | `routes.py:stats_most_repeated` (L6790) | KJ | no | Stats | test_routes_stats |
| GET | `/sing/` | `sing.py:landing` (L432) | singer | side (session cookie (sing_token)) | Access | test_sing_make_request_disable_e2e, test_sing_public_routes, test_sing_simple_mode_e2e |
| POST | `/sing/validate` | `sing.py:validate_code` (L482) | singer | no | Access | test_sing_public_routes |
| GET | `/sing/manifest.json` | `sing.py:manifest` (L505) | singer (browser) | no | PWA | test_sing_public_routes |
| GET | `/sing/sw.js` | `sing.py:service_worker` (L546) | singer (browser SW) | no | PWA | test_sing_public_routes |
| GET | `/sing/search` | `sing.py:search` (L568) | singer | no | Search | test_kn_panel_grouped_search, test_sing_make_request_disable_e2e, test_sing_public_routes (+1) |
| GET | `/sing/lib/<name>` | `sing.py:lib_file` (L630) | singer | no | Preview | test_sing_media_preview |
| POST | `/sing/media-info` | `sing.py:media_info` (L641) | singer | no | Preview | test_sing_media_preview |
| POST | `/sing/preview/resolve` | `sing.py:preview_resolve` (L698) | singer | in-mem | Preview | test_sing_media_preview, test_sing_ux_i18n |
| POST | `/sing/preview/close` | `sing.py:preview_close` (L730) | singer | in-mem | Preview | **none** |
| GET | `/sing/preview/stream/<tok>` | `sing.py:preview_stream` (L737) | singer | side (transcode/cache fill) | Preview | test_sing_media_preview |
| GET | `/sing/preview/cdg/<tok>/<part>` | `sing.py:preview_cdg` (L744) | singer | no | Preview | test_sing_media_preview |
| GET | `/sing/preview/hls/<tok>/<path:name>` | `sing.py:preview_hls` (L751) | singer | no | Preview | test_sing_media_preview |
| GET | `/sing/tip-info` | `sing.py:tip_info` (L871) | singer | no | Tips | test_sing_tips |
| GET | `/sing/event-info` | `sing.py:event_info` (L884) | singer | no | Event | test_footer_social_photo_consent, test_sing_ux_i18n |
| POST | `/sing/tip-claim` | `sing.py:tip_claim` (L907) | singer | yes | Tips | test_sing_tips |
| POST | `/sing/submit` | `sing.py:submit` (L962) | singer | yes | Requests | test_rotation_e2e, test_sing_admin_routes, test_sing_kj_pick (+9) |
| POST | `/sing/push/subscribe` | `sing.py:push_subscribe` (L1093) | singer | yes | Push | test_sing_push_routes |
| POST | `/sing/push/unsubscribe` | `sing.py:push_unsubscribe` (L1122) | singer | yes | Push | test_sing_push_routes |
| POST | `/sing/telnyx/webhook` | `sing.py:telnyx_webhook` (L1136) | system (Telnyx) | yes | SMS | test_sms_routes |
| GET | `/sing/now` | `sing.py:now_playing` (L1187) | singer | no | Rotation view | test_sing_now_and_status |
| GET | `/sing/rotation` | `sing.py:rotation` (L1198) | singer | no | Rotation view | test_sing_rotation_route, test_sing_ux_i18n |
| GET | `/sing/singers` | `sing.py:known_singers` (L1383) | singer | no | Identity | test_sing_partner_match |
| GET | `/sing/my-stats` | `sing.py:my_stats` (L1392) | singer | no | Stats | test_sing_my_stats |
| GET | `/sing/status/<int:request_id>` | `sing.py:status` (L1438) | singer | no | Requests | test_sing_now_and_status, test_sing_public_routes |
| GET | `/sing/my-requests` | `sing.py:my_requests` (L1484) | singer | no | Requests | test_sing_public_routes, test_sing_tips |
| POST | `/sing/requests/<int:req_id>/cancel` | `sing.py:cancel_request` (L1567) | singer | yes | Requests | test_sing_public_routes |
| POST | `/sing/requests/<int:req_id>/change` | `sing.py:change_request` (L1624) | singer | yes | Requests | test_sing_public_routes |
| POST | `/sing/requests/reorder` | `sing.py:reorder_requests` (L1707) | singer | yes | Requests | test_rotation_e2e, test_sing_public_routes |
| POST | `/sing/update-phone` | `sing.py:update_phone` (L1782) | singer | yes | Identity | test_sing_update_phone |
| POST | `/sing/photo-consent` | `sing.py:photo_consent` (L1832) | singer | yes | Identity | test_footer_social_photo_consent |
| POST | `/sing/rename` | `sing.py:rename_me` (L1883) | singer | yes | Identity | test_sing_rename |
| POST | `/sing/forget` | `sing.py:forget_me` (L2020) | singer | yes | Identity | test_sing_rename |

## 7. Test coverage snapshot

**How tests run.**
- `cd kj-controller && pytest`. `pyproject.toml [tool.pytest.ini_options]` sets `testpaths=["tests"]` and `addopts="-ra -q"`.
- The `slow` and `integration` markers are declared but never applied.
- Dev dependencies (`requirements-dev.txt`): pytest, pytest-cov, pytest-mock, pytest-playwright.
- `tests/e2e/` needs Playwright browsers installed (`playwright install`). It starts a real Flask server on :5099 in a thread (`e2e/conftest.py:live_server`) with playback disabled and a file-backed `rotation.db`. The e2e tests are collected by a plain `pytest` run; there is no skip guard.
- About 3,176 test functions: ~1,900 unit, ~900 integration, ~245 e2e/Playwright, plus 5 top-level files.

**CI: confirmed, there is no pytest CI.**
- `.github/workflows/` contains only `i18n.yml` (translation key parity plus `check-i18n-keys.py` on singer-UI changes) and `security.yml` (gitleaks).
- `.githooks/pre-commit` runs gitleaks and i18n auto-translate, **not pytest**. CLAUDE.md's claim that it does JS syntax validation is stale.
- Tests run only when a developer runs them locally. Auto-deploy (polls `main` every 60s) ships untested code to the live box.

**`docs/TESTING.md` is stale.** It describes VLCManager/`VLCManager(enabled=False)` and `vlc.requests.Session` mocking. The app now uses `PlaybackCoordinator`, `MpvKaraokePlayer` and `FillerVLC`. The push-notification section is a manual runbook, not automated tests.

**Stubbing strategy in practice:**
- **Playback:** `create_app(config=...)` builds `PlaybackCoordinator(enabled=False)`, so `/play` returns **503** in both integration and e2e tests. Tests that exercise `/play` flip `flask_app.vlc.enabled=True` and mock `play_video` (`integration/test_routes.py:747`, `:1245–1393`). mpv logic is unit-tested with a mocked `_send_ipc` and socket (`unit/test_mpv_karaoke_player.py`, 69 tests), the coordinator with mocked players (`unit/test_playback_coordinator.py`, 44 tests), and the filler with mocked HTTP (`unit/test_filler.py`).
- **Song end:** `_handle_karaoke_ended` and `_notify_if_dead` are unit-tested at the player level. `_make_on_karaoke_end` is tested with MagicMocks (`unit/test_app_callbacks.py`, 3 tests). `create_app` never wires it, so no app-level song-end test exists.
- **Telnyx:** `@patch("sms.requests.post")` (`integration/test_sms_routes.py`, 61 tests). Credentials are injected via `app.sms_config`.
- **Web Push:** `patch("push_dispatcher.webpush")`. `e2e/test_sing_push_e2e.py` (2 tests; in-process, **not** a browser test despite the folder) drains the dispatcher directly. `integration/test_rotation_push_hook.py` swaps in a MagicMock dispatcher.
- **Gen:** `patch.object(app.gen_client, 'create_job')` (`integration/test_gen_routes.py`). `GenPoller` is tested with mocks (`unit/test_gen_poller.py`).
- **Downloads:** `patch('routes._download_worker')` or direct calls with `media.download_*` mocked. `divebar.get_download_url` and `find_sibling_audio` are patched.
- **Playwright:** browser tests intercept singer endpoints with `page.route(...)` (e.g. `e2e/test_footer_social_photo_consent.py`).

**Fixtures:**
- `tests/conftest.py`: `mock_config` (tmp paths, `rotation_db_path=":memory:"`, `audio_processing_enabled=True`), `flask_app`/`flask_test_client`, `sing_app`/`client`/`token`, and autouse resets of the rate limiter and divebar cache.
- `tests/fixtures.py`: catalog filename corpora (ASCII / Unicode).
- `tests/search_corpus.py` + `fixtures/real_rotation_samples.json` (5 curated queries) and `fixtures/real_rotation_raw.json` (975 raw `"Artist - Title"` strings from real rotations). These are **search** corpora only, not night timelines. They are used by `integration/test_search_corpus.py`.
- `fixtures/playability_regression_manifest.tsv` (playability).
- **There are no recorded-night or rotation-timeline fixtures.** Related non-test tools that replay real data: `scripts/auto_order_sim.py` / `auto_order_review.py` (repo root, offline Auto Order on archived nights) and `dev_server.py --fetch-real` (runs against a copy of the live DB).
- WIP (uncommitted): `integration/test_action_recorder.py`, 8 tests.

### Coverage by core-night step

| Step | Tests | Type | Depth |
|---|---|---|---|
| 0 Boot | `unit/test_vapid_bootstrap.py`, `unit/test_config*.py`, `unit/test_app_media_*` | unit | `start_app` is **untested** (pragma no cover); `init_playback`, reconnect-on-restart and the lost download queue are **untested** at app level |
| 1 Night setup / archive | `unit/test_rotation_store.py` (13 archive refs, incl. `test_archive_does_not_recycle_entry_ids` :754), `unit/test_rotation_undo.py::test_archive_clears_history`, `integration/test_rotation_routes.py` (archive route), `integration/test_sing_admin_routes.py` (config/token/regenerate/enabled/simple mode, 62 tests), `integration/test_sms_routes.py::TestCrossNightPhantomMatch`, `integration/test_sing_now_and_status.py::test_prior_night_request_not_readable_via_reused_token` | unit + integration | Real SQLite behaviour; good for cross-night id reuse. **Not covered:** prior-night pending requests still listed and approvable; `rotation_archive` column loss |
| 2 Landing / validate | `integration/test_sing_public_routes.py` (73), `integration/test_host_guard.py` (20), `e2e/test_sing_frontend.py` (73) | integration + Playwright | Real |
| 3 Search | `integration/test_search_*`, `test_unified_search_*`, `test_kn_panel_grouped_search.py`, `unit/test_search_grouping.py`, `test_version_priority.py` (80), `test_catalog*.py`, `test_divebar.py`, `test_catalog_mirror.py` | unit + integration | Real logic; divebar CF / KN mocked |
| 4 Submit (+ auto-approve) | `test_sing_public_routes.py`, `test_sing_kj_pick.py` (33), `test_sing_partner_match.py` (20), `test_sing_simple_mode_e2e.py`, `test_sing_make_request_disable_e2e.py`, `test_footer_social_photo_consent.py` | integration (the "e2e"-named files here are integration) | Real routes plus SQLite; downloads and gen mocked |
| 5 Rail | `e2e/test_requests_panel.py` (9) | Playwright | Layout, badge, "rotation unmoved when request arrives" |
| 6 Approve / link / download / make | `test_sing_admin_routes.py`, `test_sing_kj_pick_e2e.py` (3; approve → download **queued**), `test_sing_fallback.py` (7; worker fallback with mocked media), `test_download_link_routes.py` (26), `unit/test_link_gate.py`, `unit/test_playability_tier2.py`, `unit/test_dedup_skip.py`, `test_gen_routes.py` (9), `unit/test_gen_poller.py`, `unit/test_rotation.py` (`complete_download`, `complete_gen_job`) | unit + integration | Each hop tested in isolation. **No test chains approve → worker completes → entry linked → /play.** `_notify_sing_outcome` push is untested (and broken, see §8) |
| 7 Ordering | `unit/test_auto_order.py` (39), `integration/test_auto_order_routes.py` (19; incl. auto-reorder on add, bias single-undo), `test_rotation_routes.py` (106; move/add/edit/paid/brb), `unit/test_rotation_store.py` (196) | unit + integration | Real. Auto-order tests use static timestamps; no clock-controlled "night progresses" test |
| 8 Play | `integration/test_routes.py` (/play validation, zip/cdg routing, mocked `play_video`), `unit/test_playback_coordinator.py`, `unit/test_mpv_karaoke_player.py`, `unit/test_routes_stats.py` (`_record_play_stat`), `unit/test_stats_store.py`, `test_rotation_undo_routes.py::test_batch_status_is_single_undo_step`, `test_sms_routes.py::TestAutoSend`, `unit/test_push_dispatcher.py` (28), `e2e/test_sing_push_e2e.py` | unit + integration | Pieces are real; `/play` always mocks the engine. **No test does play → batch status → auto-send → push as one flow** |
| 9 Mid-song | `integration/test_routes.py` (control/seek/volume/fadeout), `test_pitch_routes.py` (9), `unit/test_vocals_guide.py` (21), `e2e/test_frontend.py` (fade/stop button state) | unit, integration, Playwright (UI state only) | Mocked engine |
| 10 Song end | `unit/test_mpv_karaoke_player.py` (`_handle_karaoke_ended`), `unit/test_vlc_karaoke_player.py`, `unit/test_app_callbacks.py` | unit | **Untested end-to-end.** No test of EOF → filler → KJ Done → next; no test that a forgotten Done reverts the singer to Waiting |
| 11 Cancel/change/reorder | `test_sing_public_routes.py` (cancel/change/reorder, already_sung guards), `test_sing_admin_routes.py` (supersede takeover), `e2e/test_rotation_e2e.py::test_cancelled_entry_pulses_then_auto_removes`, `e2e/test_sing_frontend.py` | integration + Playwright | Real. Change-while-auto-approve not tested |
| 12 Duet | `test_sing_partner_match.py`, `unit/test_rotation_store.py` (singers_json), `e2e/test_rotation_e2e.py` (pills), `unit/test_singer_session_info.py` | unit, integration, Playwright | Real |
| 13 Skip/BRB/remove | `test_rotation_routes.py`, `e2e/test_singer_stats_e2e.py` (8) | integration + Playwright | Real. BRB on a Now Singing entry not tested; undo of remove not checked for `left_singers_json` |
| 14 Crash/recovery | `unit/test_playback_coordinator.py` (crash guard, restart), `unit/test_routes_player_health.py` (4), `unit/test_rotation_undo.py` (28), `integration/test_rotation_undo_routes.py` (9) | unit + integration | Real for undo. **No test of id reuse after undo** (bug confirmed). No app-restart / reconnect / lost-download-queue test |
| 15 Close / stats | `unit/test_routes_stats.py` (night-setlist), `unit/test_stats_store.py`, `integration/test_sing_my_stats.py`, `unit/test_backfill_play_stats.py` | unit + integration | Real. Midnight-crossing nights not tested |

### Coverage by capability area

| Area | Main test files | Type | Depth / notes |
|---|---|---|---|
| Rotation CRUD | `unit/test_rotation_store.py` (196), `unit/test_rotation.py` (52), `integration/test_rotation_routes.py` (106) | unit + integration | Strong, real SQLite |
| Singer ops (rename / merge / split / brb / remove / photo consent) | `integration/test_rotation_routes.py`, `integration/test_sing_rename.py` (19), `e2e/test_singer_stats_e2e.py`, `test_footer_social_photo_consent.py` (24) + e2e (7) | all three | Strong |
| Undo/redo | `unit/test_rotation_undo.py`, `integration/test_rotation_undo_routes.py` | unit + integration | Good except id reuse after undo, and multi-client interleaving beyond the stale-rev check |
| Auto-order | `unit/test_auto_order.py`, `integration/test_auto_order_routes.py` | unit + integration | Good (pure algorithm); offline harness under `scripts/` |
| Requests approve / reject / kj_pick | `integration/test_sing_admin_routes.py`, `test_sing_kj_pick*.py`, `test_sing_tips.py` (21) | integration | Good per call; no double-approve race test |
| Sing submit / cancel / change / reorder | `integration/test_sing_public_routes.py` (73) + e2e | integration + Playwright | Strong |
| Push | `unit/test_push_dispatcher.py`, `integration/test_sing_push_routes.py` (11), `test_rotation_push_hook.py`, `e2e/test_sing_push_e2e.py` | unit + integration | `webpush` mocked; real delivery is manual only (TESTING.md runbook) |
| SMS | `unit/test_sms.py` (55), `unit/test_sms_store.py` (20), `integration/test_sms_routes.py` (61) | unit + integration | Strong; Telnyx HTTP mocked |
| Playback play/control | `unit/test_playback_coordinator.py`, `test_mpv_karaoke_player.py`, `test_vlc_karaoke_player.py`, `test_karaoke_player_protocol.py`, `integration/test_routes.py`, `test_renderer_routes.py` | unit + integration | Heavily mocked IPC; no real-mpv test |
| Song-end handling | `unit/test_mpv_karaoke_player.py`, `unit/test_app_callbacks.py` | unit | Thin; not wired in `create_app` |
| Filler | `unit/test_filler.py` (36), `integration/test_routes.py` (filler_music) | unit + integration | Mocked VLC HTTP |
| Stats recording | `unit/test_stats_store.py` (40), `unit/test_routes_stats.py` (23) | unit | Real SQLite |
| Archive night | see Step 1 | unit + integration | Good for the id-monotonic guard |
| Sheets sync | `unit/test_rotation_sync.py` (23), `integration/test_rotation_routes.py` (restore) | unit + integration | gspread mocked |
| Gen / MAKE | `unit/test_gen_client*.py`, `unit/test_gen_poller.py`, `integration/test_gen_routes.py`, `test_sing_admin_routes.py` (Being Made) | unit + integration | Mocked HTTP |
| Downloads / link | `integration/test_download_link_routes.py`, `unit/test_download_*`, `test_dedup_skip.py`, `test_link_gate.py`, `test_upload_gate.py`, `integration/test_upload.py`, `test_youtube_routes.py`, `unit/test_youtube_*` | unit + integration | yt-dlp / GCS mocked |
| Divebar | `tests/test_divebar.py` (42), `unit/test_preview_divebar.py`, `integration/test_search_endpoints_unified.py` | unit + integration | Cloud Function / GCS mocked |
| KN | `unit/test_karaoke_nerds*.py`, `integration/test_kn_panel_grouped_search.py`, `tests/test_catalog_mirror.py` | unit + integration | Mirror-first; mocked |
| Preview | `unit/test_preview_*` (6 files), `integration/test_preview_routes.py`, `test_sing_media_preview.py` (17), `unit/test_preview_js.py` (node) | unit + integration | Transcode mocked |
| Overlays | `unit/test_overlay*.py`, `integration/test_overlay_routes.py` (21), `test_overlay_presets_route.py` | unit + integration | Real JSON persistence |
| Sleep mode | `unit/test_sleep_mode.py` (17), `integration/test_sleep_mode_routes.py` (6) | unit + integration | Scripts mocked |
| Host guard | `integration/test_host_guard.py` (20) | integration | Real |
| i18n | `tests/test_sing_ux_i18n.py` (21), `e2e/test_sing_i18n_footer.py` (15), CI `i18n.yml` | unit + Playwright + CI | Only area with CI |

### Untested or thin multi-step core-night flows (explicit)
1. **Full night lifecycle.** No test runs archive → N submits/approves → play/done cycles → archive again → verify the next night's state (night-scoped reads, push subscriptions, pending requests, stats setlist).
2. **Play → song end → next.** No test combines EOF (`_handle_karaoke_ended`) + `on_karaoke_end` + KJ Done + next Play + push ladder + auto-send. `create_app` doesn't even wire `on_karaoke_end`.
3. **Approve → download → link → play.** The worker is either never run or run with mocks in isolation. `complete_download` is tested only on the manager. Nothing asserts that the playable file ends up on the entry and that `/play` accepts it.
4. **Archive → new night id reuse.** Covered for archive. **Not covered for undo**, and undo does reset `sqlite_sequence`.
5. **Concurrent singer + KJ edits.** Only store-level thread-safety tests exist (`test_concurrent_writes_from_many_threads` in both stores) plus the undo stale-rev guard. Untested:
   - singer cancel/change racing a KJ move/approve/undo;
   - double approve;
   - auto-reorder firing during a KJ drag;
   - push debounce under bursts.
6. **App restart mid-night.** No coverage of: mpv reconnect, the lost download queue and its UI "failed" state, undo history surviving a restart, and rate-limit reset.
7. **Crash → auto-restart → Retry.** Only unit-level coverage (mocked players).
8. **Midnight crossing.** Stats `night_date`, the recorder `night_date` and `night_started_at` diverge; there is no test.
9. **Cancelled / BRB / Skipped rows in estimates and push.** No test that singer-facing positions ignore them. `/sing/now` does, while `compute_estimate`, the push ladder and auto-order do not.
10. **Sheet sync under live load** (only mocked unit tests), and **auto-deploy restart during a show** (untestable here).

---

## 8. Risk notes (top flows most likely to break a night if regressed)

1. **Undo resets the id sequence, so a later entry reuses an id and texts/pushes the wrong singer.**
   - **Why:** `RS.restore_entries` runs `DELETE FROM sqlite_sequence` (RS:1328) on every undo/redo. The next `add_entry` gets `max(id)+1`, which equals the id of an undone approved entry whose `sing_requests.linked_entry_id` still points at it, created tonight. `_phone_for_rotation_entry` (app.py:294), `_resolve_sms_target` (R:4044) and `_add_sms_status` then match the wrong phone. This is the same class as the "Connie" bug that archive already fixed.
   - **Coverage:** none (verified by a manual repro).
   - **Test:** a store/integration test: approve a request (entry N), undo, KJ-add a new entry, and assert its id ≠ N and that the SMS target / push phone for the new entry is None. Fix by removing the sequence delete, or re-seeding `sqlite_sequence` to `max(prev, snapshot max)`.
2. **Forgotten Done: pressing ▶ on the next row reverts the previous singer to Waiting.**
   - **Why:** `RS.update_status` sets other `Now Singing` rows to `Waiting` (RS:392), and song end doesn't mark Done (MPV:1034, app.py:170). The singer reappears as un-sung, `songs_sung` and `done_at` are not stamped, and auto-order fairness and wait pills are wrong.
   - **Coverage:** exclusivity is unit-tested, but not as night behaviour.
   - **Test:** a simulation with fake player EOF → next Play without Done. Assert the intended policy (auto-Done on advance?) and pin it with a fixture from a real night.
3. **The fallback-outcome push is dead code.**
   - **Why:** `_notify_sing_outcome` reads `getattr(app, "push_dispatcher")` (R:5983), but the dispatcher is only attached to `app.rotation.push_dispatcher` (app.py:320/509). The `resolved_alt` and `unavailable` pushes never fire.
   - **Coverage:** `test_sing_fallback.py` doesn't assert the push.
   - **Test:** an integration test with a mocked `webpush`: a failed candidate then a successful alternate asserts one `resolved_alt` push.
4. **Approve → download → auto-link chain.**
   - **Why:** several hand-offs across threads: `approve_sing_request` (R:5994), `_download_worker` (R:816), `complete_download` (RM:195) keyed by `download_id`, `_sync_rotation_download`. An undo or delete in between, or a restart (in-memory queue), silently orphans the entry.
   - **Coverage:** each hop is tested in isolation only.
   - **Test:** an integration test that runs the real worker with a stubbed `media.download_video` returning a temp file, then asserts the entry's `file_path`, `download_status=complete`, and that `/play` (engine stubbed) accepts it. Add variants with undo before completion, entry deleted, and fallback.
5. **The Play button's three client calls are not transactional.**
   - **Why:** `/play` (R:977), the batch `/rotation/status` and the 20s `/rotation/sms/auto-send` (R:4312) are independent. `/play` records stats before confirming playback (R:1069). Auto-send correctness relies on `current_playing_path`, slot order and `sms_log` idempotency.
   - **Coverage:** each piece is tested, but no sequence is.
   - **Test:** a scripted sequence against a fake player: Play slot 1, advance, 20s later auto-send, with variants (KJ stops early, KJ reorders, manual SMS first). Assert exactly-once SMS and correct `play_events`.
6. **The song-end path isn't wired in the test app factory.**
   - **Why:** `create_app` never sets `vlc.on_karaoke_end` (only `start_app` does, app.py:401). A regression in filler resume or overlay clearing at EOF, or a divergence between the two factories, is invisible to all tests.
   - **Coverage:** a mocked unit test of the callback only.
   - **Test:** make `create_app` share the wiring, add a fake `KaraokePlayer` that emits EOF, and assert that the filler fades in and the overlay flag clears.
7. **Auto-approve path differs from the admin approve path.**
   - **Why:** `sing.submit` auto-approve (S:1057–1080) sends no `approved` push and does no supersede handling. `change_request` on an approved request always creates a pending request, even under auto-approve (S:1699). There is a double-approve race: the pending check and `mark_approved` are not atomic (R:6389 → R:6451).
   - **Coverage:** per-path integration tests only.
   - **Test:** a parity test running the same request through both paths and comparing rotation, push and request state; a concurrent double-approve test that asserts one entry.
8. **Singer-cancelled rows linger when no KJ tab is open.**
   - **Why:** removal is client-side (`maybeAutoRemoveCancelled` JS:5565). Backend estimates (`compute_estimate`, `wait_estimate.py:21`), the push ladder (`decide_ladder_step`, `push_dispatcher.py:29`) and auto-order all count Cancelled/BRB/Skipped as queue slots. The result is wrong "you're up next" pushes and positions. `/sing/rotation` also zips `active` (which excludes cancelled) with estimates (which include it) (S:1215).
   - **Coverage:** a Playwright test for the pulse and removal only.
   - **Test:** backend-only simulation (no browser): cancel mid-queue, then assert the push ladder and `my-requests` positions skip it.
9. **Crash, auto-restart and app restart mid-song.**
   - **Why:** `_notify_if_dead` (MPV:903), `_record_crash` escalation (PB:111), `restart_instances` (PB:287), and the reconnect/respawn-idle logic in `init_playback`. A restart loses the download queue, crash history and tier-2 queue.
   - **Coverage:** unit tests with mocks only.
   - **Test:** a fake-mpv harness (an IPC socket stub) that kills the process mid-song. Assert `player_alert`, the restart, and that Retry `/play` doesn't double-count stats. Also a restart-persistence test (new app on the same DB) asserting the rotation, undo stack and rev survive.
10. **Night boundary and archive.**
    - **Why:** three night definitions (`night_started_at`, stats `night_date` from the calendar date at `stats_store.py:142`, and the recorder's noon cutoff). `/rotation/requests` lists prior-night pending requests (SS:803), and approving one yields an entry whose phone lookup fails (its `created_at` is before `night_started`). `rotation_archive` drops done_at, singers_json, paid and bias (RS:1257).
    - **Coverage:** the id-monotonic and night-scoped reads are covered; nothing else is.
    - **Test:** a clock-controlled two-night simulation crossing midnight; assert the setlist grouping and the pending-request handling.
11. **Auto-order / auto-reorder moving rows under the KJ.**
    - **Why:** `maybe_auto_reorder` runs implicitly on every add and approve (R:3787). It is clock-dependent (wait minutes) and can reorder during a KJ drag or undo preview; the stale-rev check covers undo only.
    - **Coverage:** algorithm plus route toggles.
    - **Test:** replay a recorded night's add/approve/move sequence with a frozen clock and golden-file the order after each step (extend `scripts/auto_order_sim.py` into a pytest fixture).
12. **Push ladder dedup and phone identity.**
    - **Why:** `next_entry_for_phone` matches by phone across all token-scoped subscriptions, including prior nights with the same token. Dedup is per subscription on `(entry_id, step)` (`push_dispatcher.py:171`), and the reservation is written before send, so a failed send is never retried. Duets resolve only the primary's phone. `decide_ladder_step` matches only the literal `now singing`.
    - **Coverage:** unit tests plus 2 in-process ladder tests.
    - **Test:** a fixture-driven ladder simulation over a recorded night's status timeline, asserting the exact push sequence per phone. Include a duet, two devices for one phone, and a returning singer from a prior night.

Also worth one line each:
- **No pytest CI.** A regression in any of the above ships to the live box via auto-deploy with no gate. Adding a GitHub Actions `pytest tests/unit tests/integration` job is the cheapest risk reduction.
- **`docs/TESTING.md` is stale.** It documents VLCManager mocking.

### 8.x Defects found while mapping

These came up while building the map. The first three were re-verified directly against the code. None has a test today. Each is a candidate for a failing test first.

| # | Defect | Evidence | Night impact |
|---|---|---|---|
| D1 | Undo/redo and "Restore from sheet" reset the `rotation_entries` AUTOINCREMENT, so ids can be reused inside one night | `rotation_store.py:1328` and `rotation_sync.py:238` run `DELETE FROM sqlite_sequence WHERE name='rotation_entries'`, but `dev_server.py:60` says to never reset it | After undoing an Add or approve, the next entry reuses the id. Tonight's request / phone / push lookups keyed by entry id can target the wrong singer (same class as the cross-night id-reuse bug) |
| D2 | Fallback-outcome pushes ("alternate version queued" / "couldn't find it") never fire | `routes.py:5983` `_notify_sing_outcome` reads `app.push_dispatcher`, but the dispatcher is only set on `app.rotation.push_dispatcher` (`app.py:332`, `app.py:522`) | The singer is never told their request failed or changed |
| D3 | `auto-deploy.sh` has no live-show guard | `auto-deploy.sh:29-41` restarts `kj-controller` on any `.py` diff | A merge mid-show restarts the app. Playback and the in-memory download queue are lost |
| D4 | Singer per-IP rate limit is 5 per 5 min, not 60 | `config.py:119-120` default overrides the code/CHANGELOG value | Many phones behind one venue NAT get 429s |
| D5 | `/sing/rotation` positions and waits are off after a cancelled entry | `sing.py:1211-1215` pairs the filtered entry list with an unfiltered estimate list | Wrong "you're #N / ~M min" for everyone after a cancellation |
| D6 | Song end never marks the entry Done. Playing the next row reverts an un-Done previous entry to Waiting | `app.py:170-187` end hook only turns off the overlay and starts filler; `rotation_store.py:392-405` | Singer looks un-sung, which skews fairness, pills and auto-order |
| D7 | A failed gen download is never retried | gen job marked complete before download (see §5, gen poller row) | A MAKE request is stuck unlinked |
| D8 | Approve has no double-click guard; `/rotation/make` creates a new gen job on every call | §3A invariants | Duplicate entries or gen jobs |
| D9 | Push opt-in with no phone fails silently | server rejects, client shows opted-in (§4.13) | Singer thinks they will be notified but won't be |
| D10 | Tonight's-variance wait estimate is dead code | rotation list excludes Done entries, so the fixed 240 s/180 s defaults are always used (§4.13) | Inaccurate wait estimates |
