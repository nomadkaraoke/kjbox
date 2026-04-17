# Change Log

Device configuration changes. For Pi details, see [archive/NOMADPI-DETAILS.md](archive/NOMADPI-DETAILS.md). For mini PC setup, see [MINIPC-SETUP.md](MINIPC-SETUP.md).

## 2026-04-16 - Fix: Filler music silent after every karaoke track (NomadPC)

Root-caused and fixed a race where VLC's filler music would go silent after every karaoke track played via the new mpv karaoke pipeline. See [AUDIO.md § Filler Audio Handoff](AUDIO.md#filler-audio-handoff-mpv--vlc) for the full story.

- **Race condition:** mpv emits its `end-file` IPC event ~350ms before it actually releases the ALSA device. The old handler called `fade_in_filler` → `pl_play` within 0.6ms — VLC's `snd_pcm_open` hit "Device or resource busy" and its `aout` module entered a permanent broken state (`state=playing` but `playedabuffers=0`, audio decoded into the void).
- **Fix:** Added `_wait_for_mpv_idle()` that sends mpv `stop` and polls `idle-active` before yielding to VLC. Eliminates the race.
- **Safety net:** Added `_verify_filler_playing()` auto-heal thread that samples VLC stats 4s after every fade-in; if `played=0` while `decoded>100`, calls `_relaunch_filler()` to restart only the VLC process (mpv untouched).
- **Version:** `kj-controller` bumped 0.19.0 → 0.19.1

## 2026-04-15 - Remote Audio Monitor

- **Audio Monitor:** Added remote audio monitoring via AV Output modal. Streams live audio over HTTP for dev/testing. Uses PipeWire HDMI capture + ffmpeg MP3 encoding. See [AUDIO.md](AUDIO.md#remote-audio-monitor).

## 2026-03-26 - Streamlined Rotation Link UX & Auto-Advance

Major improvements to the rotation song-linking workflow, search results, playback auto-advance, and edit mode isolation.

- **Link mode UI:** Visual "Linking song for: Singer — Song" banner with Cancel button replaces old prompt()-based flow
- **Singer name hidden in link mode:** Not needed when linking to an existing entry
- **Song_artist auto-updated:** When linking a search result, the rotation entry's song_artist is updated to match the selected result
- **Unlink Song option:** Added to rotation entry overflow menu for removing file links
- **Downloaded videos in search:** Local downloaded videos now included in rotation search results alongside catalog/KN/Divebar
- **Search result sorting:** Community tracks first, then karafun/preferred brands, then others
- **KN-style dropdown rendering:** Rotation search dropdown now renders identically to the Karaoke Nerds panel with community/preferred badges
- **Auto-advance on Play:** Play button sets the current entry to "Now Singing" and the next entry to "Up Next" automatically
- **File upload:** `POST /upload` route for uploading media files directly to the download folder (validates extension, sanitizes filename, triggers rescan)
- **Conky fix:** Removed `own_window_hints below` so rotation display stays visible after VLC exits fullscreen
- **Cache write on startup:** Prevents conky showing "Offline" status immediately after kj-controller restart
- **Pre-commit hook:** `.githooks/pre-commit` validates JS syntax via `node --check` before commits
- **"Update (Safe)" button:** Renamed with tooltip explaining VLC keeps playing during restart
- **Edit mode isolation:** Global keyboard shortcuts (Space, arrow keys) and click handlers disabled during inline editing
- **Polling protection:** 10-second rotation poll skips re-render when edit mode is active
- **Layout improvements:** 2:1 left/right column ratio, reduced page padding for better space utilization
- **Upload/Download widget:** Renamed with file upload picker for direct media uploads

## 2026-03-21 - SQLite-Primary Offline-First Rotation

Major architecture change: rotation system now uses local SQLite as source of truth instead of Google Sheets.

- **SQLite primary:** All rotation data stored in `~/kjdata/rotation.db` (WAL mode, busy_timeout)
- **Offline-first:** System works without internet; Google Sheets is an optional background backup
- **Stable IDs:** Entries use auto-increment SQLite IDs instead of fragile sheet row indices
- **Atomic moves:** Position updates use SQL UPDATE (not delete+insert), preventing data loss
- **File linking:** Rotation entries can link to catalog files (`POST /rotation/link`) with duration lookup
- **Time estimates:** Each entry shows estimated sing time based on cumulative durations
- **Sync indicator:** UI shows green/yellow/gray dot for Sheet sync status
- **Restore button:** Emergency restore from Sheet backup if local DB is corrupted
- **New endpoints:** `/rotation/link`, `/rotation/unlink`, `/rotation/sync-status`, `/rotation/restore`
- **API change:** All rotation endpoints now use `id` instead of `row_index`, move uses `{id, new_position}`
- **Conky simplified:** Display script reads local cache only (Sheet CSV fallback removed)
- **Modules:** `rotation_store.py` (SQLite CRUD), `rotation_sync.py` (Sheet backup), `rotation.py` (coordinator)

## 2026-03-21 - Browser Mode Orphan Detection

Fixed browser mode state getting out of sync after auto-deploy restarts. Chromium processes now survive service restarts as orphans but the server lost track of them — mode showed "VLC" while browser was visible, toggle didn't work, and playing a video left the browser on screen.

- `ChromiumManager` now detects orphan Chromium via `pgrep` (not just managed `self.process`)
- Orphans killed on startup; adopted by status endpoint if still running
- `/play` checks actual process state, not just `_browser_mode` flag
- PipeWire reset triggered for orphan cleanup (not just managed process)

## 2026-03-21 - Sleep Mode

Added Sleep Mode toggle to the System section of the KJ Controller UI for low-power state between weekly karaoke nights.

- Toggle in System panel stops VLC, overlays, rotation display, VNC, Dropbox, and unnecessary services
- Unmounts and spins down USB SSD, enables USB auto-suspend
- Blanks display via DPMS, switches to power-saver power profile
- Pre-sleep state captured to restore only previously-running services on wake
- Playback routes return 409 while sleeping; web UI and SSH remain accessible
- Reboot during sleep auto-clears flag — system boots normally
- Shell scripts (`sleep-enter.sh`, `sleep-exit.sh`) can also be run manually via SSH
- Installed `uhubctl` on NomadPC (USB hubs don't support per-port power, but available for future hardware)

## 2026-03-06 - Drag-and-Drop Rotation Reordering

Added drag handles (≡) to rotation entries in the KJ Controller UI for reordering singers.

- Drag handle on the left of each entry, HTML5 drag-and-drop with visual feedback
- Backend `POST /rotation/move` endpoint: deletes source row and re-inserts at target position
- `RotationManager.move_entry()` handles row index shifting after deletion

## 2026-03-05 - Exact Status Text on Rotation Display

Conky rotation display now shows the exact status text from the Google Sheet (e.g. "Now Singing", "Being Made (!)") instead of abbreviated badge labels ("NOW", "MAKING"). Color coding preserved, "Waiting" status hidden (no badge).

## 2026-03-05 - Instant Rotation Display Updates

Rotation display (conky) now updates within ~3 seconds of changes made in KJ Controller, down from up to 60 seconds.

- KJ Controller writes `/tmp/rotation_cache.json` after every rotation mutation (add, update, delete, mark singing, etc.)
- `rotation_data.py` reads from local cache first, falls back to Google Sheet CSV if cache is missing or >120s old
- Conky polling interval reduced from 30s to 3s (reading a local file is effectively free)

## 2026-03-05 - Faster Filler Music Fade

Reduced filler music fade time from 3s to 1.5s, and post-fade buffer from 0.5s to 0.3s. Karaoke playback now starts ~1.8s after pressing Play (was ~3.5s).

## 2026-03-05 - Consistent Button Styling, System Stats & Format Filter

**UI overhaul:** Unified all ~33 button types to consistent outlined style (was: 3 conflicting paradigms).

- Base buttons: dark outlined (`#222` bg, `#444` border, `#ccc` text) with hover glow
- New `.btn-primary` class: pink-hinted outline for primary actions (Play, Download, Search, Save)
- All secondary buttons (filler, overlay, rotation, system) updated to match
- Delete buttons: outlined red instead of solid red fill

**System section reorganized** into logical subsections:
- Media & Output (AV Output, Filler Music, Audio Device)
- Maintenance (Auto-Deploy, Update, Restart, Reboot)
- Stats (CPU, Memory, Disk — live sparkline graphs with hover tooltips)

**System stats** (`GET /system/stats`):
- CPU %, memory (used/total GB), disk (used/total GB) via `psutil`
- Frontend: bar charts + 30-point sparkline history, polled every 5s
- Hover tooltips show value and time offset (e.g., "45% (30s ago)")

**3-state format filter** replaces MP4 Only toggle:
- Cycles: All → MP4 Only → CDG+ZIP Only
- Persisted in localStorage with migration from old boolean setting

**Dependency:** Added `psutil` to `requirements.txt`

## 2026-03-05 - VLC Process Lifecycle Decoupling

**Zero-downtime deploys:** VLC playback now survives kj-controller restarts.

- VLC launched in its own process group (`start_new_session=True`) so it's not killed when the Python process exits
- On startup, kj-controller probes VLC HTTP ports and reconnects to existing instances
- Playback state (current song, filler track) persisted to `/tmp/kj-vlc-state.json` and recovered on restart
- `restart_instances` handles orphan VLC processes (kills by port when we don't own the PID)
- Auto-deploy skips service restart for frontend-only changes (JS/CSS/HTML)
- systemd unit updated: `KillMode=process` so only Python is killed, not VLC children

## 2026-03-05 - UI Polish & System Management

**Rotation improvements:**
- "New Rotation" button: archives all entries to "Past events" sheet, clears rotation for a fresh night
- Edit and delete rotation entries: pencil button, Shift+click to edit inline, Ctrl/Cmd+click to delete
- Ctrl/Cmd+hover shows red strikethrough delete preview, Shift+hover shows purple edit preview
- Exclusive statuses: only one "Now Singing" and one "Up Next" at a time (others reset to "Waiting")
- New entries default to "Waiting" status
- `POST /rotation/archive`, `/rotation/edit`, `/rotation/delete` endpoints
- Local dev server (`dev_server.py`) with mock rotation data for UI iteration
- Replaced "Updating..."/"Updated!" text with spinner/tick indicator in section header
- Compact row layout (smaller fonts, tighter padding) to show more singers at once
- Advanced status dropdown ("...") button with all 7 Google Sheet statuses
- Click-to-copy on singer names and song text

**System section:**
- Auto-deploy toggle (start/stop + enable/disable for reboot persistence)
- Graceful restart/reboot/update: spinner overlay, polls backend, shows success when back online
- Update button always restarts service; auto-reloads browser when backend recovers

**YouTube settings:**
- yt-dlp version check against PyPI (cached 24h), yellow dot + "Update" button when outdated
- One-click yt-dlp upgrade from UI with graceful restart polling
- Normalized version comparison (2026.03.03 vs 2026.3.3)

**Screen Preview:**
- Hide button disconnects VNC and collapses section; clicking a size button reconnects
- Hidden state persists via localStorage; VNC doesn't auto-connect when hidden

**Overlay management:**
- Backup/Restore buttons: download config as JSON, restore from file
- `POST /overlays/import` endpoint for bulk import
- Untracked `data/overlays.json` from git to prevent auto-deploy wiping device config

---

## 2026-03-05 - Rotation Sheet Integration & System Update Button

**Motivation:** The singer rotation was managed entirely via a Google Sheet, which meant the KJ had to alt-tab to the browser to update singer status. This led to forgetting to update the rotation until several songs late.

**New module: `rotation.py`**
- `RotationManager` class using `gspread` + GCP service account for Google Sheets API
- Auto-detects header row and column positions (handles metadata rows, extra columns)
- Read: fetches non-done entries with 10s cache to avoid API hammering
- Write: update status, mark singing (auto-clears other "Singing Now"), add new entries
- Portable timestamp formatting (no platform-specific strftime)

**New UI: Rotation panel (below Playback Controls)**
- Shows all non-done rotation entries with singer name, song, and status badges (NOW/NEXT/WIP)
- Per-row action buttons: Singing, Done, Next
- Inline "Add Singer" form
- Auto-refreshes every 10s, manual Refresh button
- Gracefully hidden when rotation is not configured (no service account set up)

**New UI: System Update button**
- `POST /system/update` runs `git pull origin main` on the device
- If Python files changed: auto-restarts the service and reloads the page after 5s
- If only static files changed: prompts to refresh the browser
- Reports git pull output and errors in the UI

**Config additions** (both optional):
- `rotation_sheet_id` — Google Sheet ID for singer rotation
- `rotation_credentials_file` — path to GCP service account JSON key

**Setup on NomadPC:**
- Created GCP service account `kjbox-138@nomadkaraoke.iam.gserviceaccount.com`
- Shared rotation Google Sheet with service account (Editor access)
- SA key deployed to `~/kjdata/rotation-sa-key.json`
- Config updated, service restarted, verified working

**Dependencies added:** `gspread`, `google-auth` (already installed in venv)

**Version:** 0.8.3 → 0.9.0

## 2026-02-26 - YouTube Download Resilience & Async Downloads

**Motivation:** YouTube downloads via yt-dlp would fail after several tracks in quick succession during a live karaoke show (YouTube rate limiting/bot detection). Also, refreshing the page during a download lost the completion notification since the download was synchronous (blocking 25+ seconds).

**New module: `youtube_health.py`**
- Health checks: yt-dlp version, EJS solver (yt-dlp-ejs) installed/version, Deno runtime available/version, cookie file status and validation
- Cookie management: Netscape-format validation, atomic file writes with 0o600 permissions

**Anti-detection settings in `media.py`**
- New `_ytdlp_base_opts(config)` helper used by both `download_video()` and `youtube_search.py`
- Sleep intervals between requests (`sleep_interval: 1`, `max_sleep_interval: 5`, `sleep_interval_requests: 1`) — main mitigation for rapid successive downloads
- Retries (`retries: 3`, `fragment_retries: 3`, `extractor_retries: 3`)
- Cookie support via `youtube_cookies_file` config key

**Async downloads**
- `POST /download` now starts download in a background thread, returns immediately
- Server-side `download_state` tracking: `idle → downloading → completed/error`
- Frontend detects completion via existing 2-second `/status` poll — survives page refresh
- `POST /download/ack` resets state after frontend handles notification
- Concurrent download protection (409 if already downloading)

**YouTube Settings modal (web UI)**
- Health dot next to "Download Song" header (green/yellow/red)
- Modal shows yt-dlp version, EJS solver status, Deno runtime status, cookie status
- Paste Netscape-format cookies from browser extension → Upload → validates and saves
- Delete cookies button with confirmation

**New API endpoints:** `GET /youtube/status`, `POST /youtube/cookies`, `DELETE /youtube/cookies`, `POST /download/ack`

**Dependencies:** Changed `yt-dlp` → `yt-dlp[default]` in requirements.txt (includes yt-dlp-ejs solver plugin). Deno runtime installed on NomadPC for EJS solver.

**NomadPC setup:** Installed yt-dlp-ejs 0.5.0, Deno 2.7.1 (symlinked to `/usr/local/bin/deno`), verified via `/youtube/status` endpoint.

## 2026-02-26 - Fix: AV Reset button failed with Permission denied on /etc/asound.conf

**Root cause:** `POST /av/reset` calls `fix-hdmi-audio.sh` from within the Flask process (running as user `nomad`). The script needs to write `/etc/asound.conf` and call `amixer`, both requiring root. The `ExecStartPre=+` mechanism that gives root at service start doesn't apply to this in-process call.

**Fix:**
- `routes.py`: Changed subprocess call from `['/bin/bash', script_path]` to `['sudo', script_path]`
- Added `/etc/sudoers.d/kj-fix-hdmi` on NomadPC: `nomad ALL=(root) NOPASSWD: /opt/nomad/kjbox/kj-controller/fix-hdmi-audio.sh`
- Updated `docs/MINIPC-SETUP.md` to document this sudoers requirement (needed for fresh installs)

## 2026-02-25 - AV Output Modal (replaces Audio/Display dropdowns)

**Motivation:** The old Audio Output and Display Resolution dropdowns in the System section were a liability — changing them from their known-good values could undo the hard-won HDMI/audio configuration, and changes persisted to `config.json` without a clear "reset to safe state" path.

**Changes:**
- Replaced Audio Output dropdown + Display Resolution dropdown + Scan HDMI button with a single **AV Output** button (opens a modal)
- Modal shows full AV status: video connectors (connected/resolution/EDID name), HDMI PCM devices (jack state, IEC958 switch, ELD monitor name), PipeWire profile, VLC device, ALSA alias, and overall health indicators
- **Reset All** button runs `POST /av/reset` → `fix-hdmi-audio.sh` → restores the full known-good AV state (ALSA, IEC958, PipeWire, display 1920x1080) then restarts VLC
- AV settings no longer persisted to `config.json` — `fix-hdmi-audio.sh` is the single source of truth for the known-good state
- `fix-hdmi-audio.sh` extended: now also resets PipeWire to `output:analog-stereo+input:analog-stereo` and xrandr to 1920x1080 after fixing ALSA/IEC958. This means service restart always fully restores AV state.
- New routes: `GET /av/status`, `POST /av/reset`, `POST /av/vlc-device`

## 2026-02-25 - Fix: HDMI audio silent at boot (missing ExecStartPre)

**Root cause:** `fix-hdmi-audio.sh` was never being called at service start. The `ExecStartPre` line was documented in `HDMI.md` but was missing from `MINIPC-SETUP.md` (the actual setup guide used to configure the device). As a result, every boot left the IEC958 Playback Switch `off` (its hardware default), silencing all HDMI audio.

**What the IEC958 switch does:** It's the digital audio enable/disable at the HDA codec level. When `off`, the ALSA PCM stream runs normally (state: RUNNING, data flowing) but no audio packets are transmitted over HDMI. This is an HDA hardware default that must be explicitly enabled — it doesn't survive reboots.

**Fix:**
- Added `ExecStartPre=+/opt/nomad/kjbox/kj-controller/fix-hdmi-audio.sh` to `/etc/systemd/system/kj-controller.service` on NomadPC
- The `+` prefix makes it run as root (needed to write `/etc/asound.conf`), even though the main service runs as `nomad`
- Updated `MINIPC-SETUP.md` to include `ExecStartPre` so future reinstalls are correct

**Also confirmed at first venue test (2026-02-25):**
- OREI splitter in STD mode presenting "HDMI Splitter" EDID to NomadPC — correct 1920x1080
- Denon AVR on splitter Out 1 (50ft cable) — video and audio working after IEC958 fix
- 7" touchscreen on another output — working simultaneously

## 2026-02-24 - Power-Loss Hardening (SSD reformat + system config)

NomadPC gets unplugged at venues without clean shutdown. Applied four layers of hardening so power loss never prevents healthy startup.

**1. SSD reformatted: exFAT → ext4**
- exFAT has no journal — power loss can silently corrupt data
- Reformatted `/dev/sda1` (4TB SanDisk SSD) to ext4 with journaling
- Restored 413,670 files (3.44 TB) from verified HDD backup via rsync
- Updated fstab: `UUID=b5ec3a27-4477-467e-a002-fd7ab8b3b755`, `ext4`, `noatime,nofail`
- `noatime` reduces unnecessary metadata writes (fewer things to journal)

**2. Atomic JSON writes (code change)**
- `config.py:save_config_value()` and `media.py:MediaIndex.save()` previously used `open(path, 'w')` + `json.dump()` — power loss mid-write would truncate/empty the file
- Now use atomic write pattern: write to temp file → fsync → `os.replace()` (rename is atomic on ext4)
- `overlay.py` already used this pattern — no change needed
- Added 10 tests in `tests/test_atomic_writes.py` verifying crash safety

**3. Kernel panic auto-reboot**
- `/etc/sysctl.d/99-power-loss.conf`: `kernel.panic = 10`
- Default was 0 (hang forever on panic) — now reboots after 10 seconds

**4. Journal size cap**
- `/etc/systemd/journald.conf.d/size-limit.conf`: `SystemMaxUse=200M`
- Was unbounded (890MB and growing) — faster flush after unclean shutdown

**Docs updated:** `MINIPC-SETUP.md` (Phase 1.7 rewritten, Phase 6.1-6.2 updated for ext4, checklist updated)

## 2026-02-24 - Fix: 5 VLC Management Bugs (v0.8.2)

Follow-up to the ALSA contention fix (v0.8.1). Comprehensive audit found 5 additional bugs in VLC state management where the code didn't properly account for ALSA device exclusivity or made incorrect assumptions about state.

**Bug fixes:**

1. **Fade cancel (concurrent fade-in/fade-out race)** — If `fade_in_filler` and `fade_out_filler` ran close together, both fade threads would compete to set volume simultaneously. Added `_fade_cancel` event that each fade operation sets before starting, causing any in-progress fade to abort. `fade_out_filler` now also reads actual VLC volume instead of assuming `filler_volume`.

2. **Pause no longer starts filler** — Pausing karaoke was calling `fade_in_filler()`, but the paused karaoke VLC still holds the exclusive ALSA device. Filler would get "Device or resource busy". Removed filler fade-in/out from pause_resume — the KJ should just hear silence during pause.

3. **Stop uses ensure_karaoke_released** — The stop action was sending raw `pl_stop`/`pl_empty` without verifying the device was actually released before starting filler. Replaced with `ensure_karaoke_released()` which retries up to 5 times with status verification.

4. **Filler music change during karaoke** — `set_filler_music` unconditionally called `pl_play`, which would fail with "Device or resource busy" when karaoke was active. Now enqueues the track but skips playback — the track will start when karaoke ends and `fade_in_filler()` is called.

5. **Play state set atomically** — `current_playing_path` and overlay state were set in the route handler *before* `play_video` acquired the lock, creating a race where status could show song B's name while song A was still playing. Moved state-setting inside `play_video`'s `_play_lock` via new `display_path` and `overlay_manager` parameters.

## 2026-02-24 - Fix: Filler Music Audio Device Contention

Filler music stopped playing after karaoke songs ended. Root cause: the karaoke VLC held the exclusive ALSA device (`hw:0,3` via `hdmiout`) even after reaching "stopped" state, so the filler VLC got "Device or resource busy" when trying to resume.

**Fix:**
- Added `ensure_karaoke_released()` — explicitly sends `pl_stop` + `pl_empty` to karaoke VLC before filler resumes, forcing ALSA device release
- Added `_play_lock` to serialize concurrent `play_video()` calls (prevents race conditions when rapidly clicking different tracks)
- Added `last_play_time` grace period to prevent the monitor thread from falsely detecting "stopped" during song transitions
- Skipped the 3s filler fade-out when switching between karaoke songs (filler is already stopped), making song switches near-instant

## 2026-02-22 - NomadPC: Remote SSH Access (Tailscale + Cloudflare Tunnel)

Enabled SSH access to NomadPC from outside the LAN via two paths.

**What was done:**
- Confirmed Tailscale is already installed and running on NomadPC — IP `100.82.90.111`
- Added SSH ingress to the Cloudflare tunnel config (`/etc/cloudflared/config.yml`): `kjssh.nomadkaraoke.com → ssh://localhost:22`
- Created DNS CNAME for `kjssh.nomadkaraoke.com` in Cloudflare
- Restarted cloudflared service to pick up the new config
- Added `nomadpcts` (Tailscale) and `nomadpctunnel` (Cloudflare) SSH aliases to `~/.ssh/config` on Mac

**How to SSH remotely:**
- `ssh nomadpcts` — via Tailscale (Mac Tailscale must be running)
- `ssh nomadpctunnel` — via Cloudflare tunnel (browser auth on first use, `cloudflared` must be installed)

**Docs updated:** `MINIPC-SETUP.md` (sections 2.5, 2.6, 2.8), `TROUBLESHOOTING.md`

## 2026-02-19 - NomadPC: Fix HDMI Audio After Reboot

HDMI audio stopped working after reboot. Root cause: VLC was configured to use ALSA device `default`, which PipeWire redirects to the analog stereo output — not HDMI.

**Investigation:**
- PipeWire 1.0.5 defaults to analog stereo profile on boot
- Even after switching PipeWire to the HDMI profile, audio flowed through PipeWire (confirmed via `pw-top`) but produced no sound at the TV
- Direct ALSA access to `hw:0,7` (bypassing PipeWire) worked reliably

**Fix applied:**
- Created `/etc/asound.conf` defining `hdmiout` as `plug` → `hw:0,7` (direct ALSA, bypasses PipeWire)
- Updated `config.json`: `default_audio_device: "hdmiout"`, `audio_devices: {"hdmiout": "HDMI Output (TV)"}`
- PipeWire left on analog profile so it doesn't lock the HDMI device
- VLC now launches with `--alsa-audio-device hdmiout`

**Docs updated:** `AUDIO.md` (added NomadPC section), `MINIPC-SETUP.md` (corrected audio config and instructions).

## 2026-02-19 - NomadPC: TLS, Cloudflare Tunnel, Remote Access & Code Fixes

Set up HTTPS, Cloudflare tunnel for remote access, Zero Trust authentication, and committed several code fixes to the repo.

**TLS/HTTPS:**
- Generated mkcert certificates for `nomadpc.local`, `nomadpc`, `192.168.8.170`, `localhost`, `127.0.0.1`
- Deployed to `/opt/nomad/kjbox/kj-controller/certs/` on device
- Flask auto-switches to port 443 (HTTPS) when certs are present
- Websockify also uses certs for WSS on port 6080
- Config keys added: `tls_cert`, `tls_key`

**Cloudflare tunnel reconfigured:**
- Changed from SSH-only tunnel to web UI + VNC WebSocket
- `kjbox.nomadkaraoke.com` → `https://localhost:443` (KJ Controller web UI, `noTLSVerify: true` for mkcert)
- `kjvnc.nomadkaraoke.com` → `http://localhost:6080` (websockify for VNC preview)
- Two hostnames needed because Cloudflare tunnels don't support path-based routing

**Cloudflare Access (Zero Trust):**
- Configured via Cloudflare Zero Trust dashboard
- Email OTP authentication on both `kjbox` and `kjvnc` hostnames
- 24-hour session duration

**Code committed to repo:**
- **`vlc.py`**: Platform detection — `self.enabled` checks `config.get('enable_vlc', False)` alongside `is_pi()`
- **`app.py`**: Restructured platform setup — Pi-specific (xhost, dietpi) separate from shared (websockify). Websockify starts on any device with `enable_vlc: true`, with configurable host/port and TLS support
- **`auto-deploy.sh`**: Added `sudo` prefix to all `systemctl restart` commands (non-root `nomad` user needs it). Fixed catalog rebuild URL to try HTTPS first
- **`templates/index.html`**: Smart websockify routing — detects LAN vs tunnel from `location.hostname`. `.local`/`localhost`/IP → direct `hostname:6080`; anything else → `websockify_host` config value (`kjvnc.nomadkaraoke.com`)
- **`routes.py`**: Pass `config` dict to template for websockify settings

**Config keys added:** `websockify_host` (tunnel hostname for VNC WebSocket)

## 2026-02-19 - NomadPC: Media Setup & External Catalog

Set up the USB external drive, migrated legacy video files, and built the full karaoke catalog.

**USB SSD mounted:**
- SanDisk Extreme Pro 4TB (`/dev/sda1`, exFAT, label "Nomad4TBOne")
- Mounted at `/media/nomad/Nomad4TBOne` with fstab persistence (`nofail,uid=1000,gid=1000`)
- Contains HyperMule karaoke catalog (~415K files)

**Legacy video migration:**
- Migrated 697 MP4 files from `/home/nomad/kjdata/videos/` (old KJ software) to `/opt/nomad/YTDownloads/`
- Renamed from legacy format (`{random_id}.mp4` + JSON sidecar) to new convention (`{youtube_id}__Unknown__{safe_title}.mp4`)
- YouTube IDs extracted from `original_url` in JSON metadata; channel set to "Unknown" (not in legacy data)
- 6 files skipped (no YouTube ID), 1 error (malformed JSON)

**External catalog built:**
- 414,933 entries indexed into SQLite FTS5 database (`external_media.db`)
- Manifest file: `/media/nomad/Nomad4TBOne/HyperMule/all-karaoke-files-2025.02.28.txt`
- Path rewriting: `/Volumes/Nomad4TBOne/` → `/media/nomad/Nomad4TBOne/` (macOS → Linux)
- Search verified working (e.g., "bohemian rhapsody" returns Queen results from multiple publishers)
- Config keys added: `external_file_list`, `external_media_mount`

**Filler music:** Copied from legacy KJ data folder, playing successfully through HDMI.

**HDMI audio verified:** `speaker-test` and VLC both produce sound via HDMI. Later found to require direct ALSA config (see 2026-02-19 audio fix entry).

**HDMI display:** Set to 1920x1080@60Hz via `xrandr`, persisted with XFCE autostart entry.

**x11vnc fixes:**
- Added `-shared` flag (without it, new connections kick existing ones)
- Changed to `After=display-manager.service` + `WantedBy=multi-user.target` (avoids ordering cycle on shutdown)

**Documentation:** Updated MINIPC-SETUP.md with new Phase 6 (USB drive, filler music, legacy migration, external catalog), corrected x11vnc service definition, and updated checklist.

## 2026-02-18 - NomadPC: Initial Hardware Audit

Performed initial audit of the x86 mini PC that will serve as a more powerful replacement/companion for the Raspberry Pi at live events.

**Hardware discovered:**
- **CPU:** Intel N97 (4 cores, up to 3.6GHz, x86_64)
- **RAM:** 16GB
- **Storage:** 476GB NVMe SSD (ext4, 411GB free)
- **GPU:** Intel Alder Lake-N UHD Graphics
- **Display outputs:** 2x HDMI + 2x DisplayPort (HDMI-1 connected at 1920x1080)
- **Audio:** HDA Intel PCH, HDMI stereo via PipeWire (verified working)
- **Ethernet:** `enp2s0` (MAC: `84:47:09:5a:1d:13`)
- **WiFi:** `wlp1s0` (MAC: `9c:12:21:3f:39:43`)

**Software state:**
- **OS:** Linux Mint 22.1 Xia (Ubuntu 24.04 Noble), kernel 6.8.0-71-generic
- **Desktop:** XFCE via LightDM (autologin as `nomad` user)
- **Audio:** PipeWire 1.0.5 (not raw ALSA like the Pi)
- **Pre-installed:** VLC 3.0.20, conky 1.19.6, yt-dlp 2025.07.21, git 2.43.0, avahi-daemon
- **Cloudflared:** Running with SSH tunnel to `kjbox.nomadkaraoke.com`
- **Hostname:** `nomad-karaoke` (to be renamed to `nomadpc`)
- **User:** `nomad` (UID 1000, zsh shell)
- **SSH key:** `andrew@beveridge.uk` already authorized

**Not yet done:**
- Hostname rename to `nomadpc`
- Sleep/screensaver not disabled (lock=true, idle=900s)
- Tailscale not installed
- KJ Controller not deployed (/opt is empty)
- Avahi not restricted to ethernet interface
- BIOS "Power On After Power Loss" not verified

**Key differences from Pi that affect setup:**
1. PipeWire audio (not raw ALSA) — VLC needs `XDG_RUNTIME_DIR` for PipeWire socket access
2. XFCE desktop (not LXDE) — screensaver/power commands differ, conky `own_window_type` may need adjustment
3. VLC runs directly as `nomad` user — no root wrapper or `sudo -u dietpi` needed
4. `enable_vlc: true` config flag needed (no `/boot/dietpi.txt` sentinel)
5. x11vnc instead of RealVNC for VNC preview

Updated `docs/MINIPC-SETUP.md` with all verified details. Updated `README.md` and `CLAUDE.md` to reference both devices.

## 2026-02-17 - KJ Controller: VNC Screen Preview

Added a live VNC screen preview thumbnail to the KJ Controller web UI. The KJ can now see what's on the Pi's HDMI output directly in the browser without a direct line of sight to the display.

**Architecture:**
- **websockify** (Python package) runs on the Pi as a WebSocket-to-TCP proxy, listening on port 6080 and forwarding to RealVNC on port 5900
- **noVNC** v1.6.0 (vendored ES6 library) runs in the browser, connecting via WebSocket to render the VNC framebuffer into a canvas element
- The thumbnail is 200px wide, view-only, positioned in the left column of the web UI

**Changes Made:**
1. **websockify subprocess** — started during app startup on Pi only (`is_pi()` = true); resolves the binary from the venv's bin directory (`sys.executable` parent), falling back to system PATH
2. **noVNC vendored** — ~56 ES6 module files in `static/novnc/` (core library + pako compression vendor)
3. **VNC preview UI** — password input (stored in `localStorage`), connect/disconnect controls, auto-reconnect on disconnect (5-second delay)
4. **TLS/HTTPS support** — Flask and websockify serve over HTTPS/WSS when TLS certs are present. Required because RealVNC's RA2ne authentication uses `crypto.subtle` which is only available in secure contexts (HTTPS). Certs generated via `mkcert` (locally-trusted CA). When certs are present, Flask auto-switches from port 80 to 443.
5. **RA2ne auth handling** — noVNC's `serververification` event is auto-approved (similar to SSH host key acceptance) since this is a trusted local Pi. Without this handler, the RA2ne handshake hangs indefinitely.
6. **New config keys** — `websockify_port` (default: 6080), `vnc_target` (default: `localhost:5900`), `websockify_enabled` (default: true), `tls_cert` (default: `certs/cert.pem`), `tls_key` (default: `certs/key.pem`)
7. **New dependency** — `websockify` added to `requirements.txt`

**TLS certificate setup (one-time per dev machine):**
```bash
brew install mkcert && mkcert -install  # install local CA
mkcert nomadpi.local nomadpi 192.168.8.106 localhost 127.0.0.1
# Copy cert.pem and key.pem to kj-controller/certs/ on the Pi
```

**RealVNC device configuration applied** (`/root/.vnc/config.d/vncserver-x11`):
- Added `Encryption=PreferOff` — allows unencrypted connections (websockify handles TLS termination)
- Restarted `vncserver-x11-serviced` to apply changes

## 2026-02-17 - KJ Controller: Dynamic Overlay System

Added a configurable overlay system for the NomadPi display, managed entirely from the KJ Controller web UI.

**New Components:**
1. **Overlay Engine** (`desktop/overlay_engine.py`, `overlay_types.py`, `overlay_config.py`) — standalone pygame-ce process that renders overlays as borderless always-on-top X11 windows at 30fps. Supports 5 overlay types: scrolling ticker, static text, image/logo, countdown timer, and QR code.
2. **Overlay Manager** (`kj-controller/overlay.py`) — CRUD operations and state persistence for overlay configurations via `data/overlays.json`.
3. **Overlay REST API** — 7 new routes (`GET/POST /overlays`, `GET/PUT/DELETE /overlays/<id>`, `POST /overlays/<id>/toggle`, `POST /overlays/<id>/toggle-video`)
4. **Web UI panel** — "Overlays" panel in the KJ Controller interface with add/edit/delete forms, toggle switches, type-specific config fields
5. **Systemd service** (`desktop/overlay-display.service`) — runs the overlay engine as `overlay-display.service`

**Architecture:**
- KJ Controller backend writes overlay configuration to `data/overlays.json`
- Overlay engine polls the JSON file (mtime check every ~1s) and syncs overlay windows
- Each overlay has an independent `show_over_video` toggle: when off, overlays auto-hide during karaoke video playback
- `karaoke_playing` state is set by the play/control/stop routes and the `VLCManager.on_karaoke_end` callback

**Dependencies:** `pygame-ce`, `qrcode` (pip, overlay engine only)

## 2026-02-17 - KJ Controller: UI Redesign with Nomad Branding

Redesigned the KJ Controller web interface with Nomad brand identity, responsive layout, and modular file structure.

**Changes Made:**
1. **Brand Identity** — Applied Nomad color palette: pink (#ff5bb8/#ff7acc), gold (#ffdf6b), purple (#8b5cf6) on dark backgrounds (#0f0f0f/#1a1a1a) with ambient radial gradients
2. **Static Asset Extraction** — Separated inline CSS/JS into `static/style.css` and `static/app.js`; Jinja2 variable bridged via `window.KJ_CONFIG`
3. **Favicons** — Added favicon.ico, 16x16, 32x32, apple-touch-icon (from karaoke-gen)
4. **Responsive Design** — Three breakpoints: 1024px (tablet), 768px (single-column mobile), 480px (compact mobile)
5. **Title** — Changed from "KJ Remote" to "Nomad KJ Control"
6. **E2E Tests** — 44 Playwright browser automation tests covering layout, controls, interactions, responsive behavior, and brand colors

## 2026-02-17 - KJ Controller: Port 5000 → 80

Changed the KJ Controller Flask server default port from 5000 to 80 so it's accessible at `http://nomadpi.local` without specifying a port number.

**Changes Made:**
1. Updated default `flask_port` from 5000 → 80 in `config.py` and `app.py`
2. Updated Pi's `config.json` (which had an explicit `flask_port: 5000` override)
3. Restarted `kj-controller` service — confirmed responding on port 80

**Why:** With mDNS now broadcasting `nomadpi.local`, using the standard HTTP port means `http://nomadpi.local` just works — no need to remember `:5000`.

**Note:** The service runs as root, so binding to port 80 (privileged port) works without extra configuration.

## 2026-02-17 - mDNS / Avahi: `nomadpi.local` Hostname

Installed `avahi-daemon` and `libnss-mdns` so the Pi broadcasts its hostname via mDNS (multicast DNS). Any device on the same LAN can now reach the Pi at `nomadpi.local` without any DNS configuration — works automatically via Bonjour on macOS.

**Changes Made:**
1. `apt-get install -y avahi-daemon libnss-mdns`
2. Restricted Avahi to `eth0` only (`allow-interfaces=eth0` in `/etc/avahi/avahi-daemon.conf`) to avoid advertising Docker bridge IPs
3. Service enabled and starts on boot automatically

**Usage:** `ssh root@nomadpi.local`, `http://nomadpi.local`, `ping nomadpi.local`

**Why:** Provides reliable hostname-based access that survives IP changes — no need to know the current DHCP IP. Works without internet (pure LAN multicast). Complements static DHCP reservation (Layer 1) and Tailscale (Layer 3) as the middle layer of the connectivity strategy.

## 2026-02-17 - DietPi Upgrade: Debian 12 (Bookworm) → Debian 13 (Trixie)

Upgraded DietPi to the latest Debian release following the [official upgrade guide](https://dietpi.com/blog/?p=4014). The upgrade completed successfully but required several post-upgrade fixes.

**Upgrade process:**
- Ran `dietpi-update` which handled the Bookworm → Trixie transition
- Hit a dependency blocker: `chromium` and other GTK3 packages depended on `libgtk-3-0` which was renamed to `libgtk-3-0t64` in Trixie (64-bit time_t transition)
- Resolved by removing blocking packages (`apt-get remove -y lxde chromium chromium-browser galculator libgspell-1-2 libgtksourceview-4-0 libmousepad0 libvte-2.91-0 lxterminal mousepad xarchiver zenity zenoty chromium-common`) then retrying the upgrade
- Upgrade completed, system rebooted, ran `apt autopurge`

**Post-upgrade fixes required:**

1. **Reinstalled LXDE** — the desktop meta-package and all its components were removed during the GTK3 dependency cleanup
   ```bash
   apt-get install -y lxde
   ```

2. **LightDM autologin** — the upgrade switched from startx-based autologin to LightDM, but autologin wasn't configured
   ```bash
   # /etc/lightdm/lightdm.conf
   [Seat:*]
   autologin-user=root
   autologin-session=LXDE
   user-session=LXDE
   ```

3. **PAM root autologin** — Trixie's default `/etc/pam.d/lightdm-autologin` blocks root from auto-login. Commented out the blocking line:
   ```
   # Was: auth required pam_succeed_if.so user != root quiet_success
   ```

4. **LXDE autostart** — re-added `@xhost +SI:localuser:dietpi` (lost during LXDE reinstall)

5. **Python venv rebuilt** — Python upgraded from 3.11 to 3.13, breaking the kj-controller venv
   ```bash
   apt-get install -y python3.13-venv
   cd /opt/nomad/kjbox/kj-controller && rm -rf venv && python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   ```

6. **auto-deploy.sh execute permission** — git didn't preserve the execute bit (was `100644`, fixed to `100755` in git index)

7. **kj-controller service boot ordering** — changed `WantedBy=multi-user.target` to `WantedBy=graphical.target` in `/etc/systemd/system/kj-controller.service`. The service has `After=graphical.target` ordering, so it needs to be pulled in by `graphical.target` (not `multi-user.target` which starts earlier). This matches `rotation-display.service` which already used `WantedBy=graphical.target`.

**Post-upgrade state:**
- OS: Debian 13 (Trixie), DietPi v10.0.1
- Kernel: 6.12.62+rpt-rpi-v8
- Python: 3.13.5
- Display manager: LightDM (was startx/xinit)
- All services boot correctly: kj-controller, rotation-display, kj-autodeploy

**Known issue — USB touchscreen flapping:**
The Goodix touchscreen controller (WingCool Inc., VID `27c6` PID `0818`) on USB port 1-1.4 disconnects and reconnects every ~5 seconds under kernel 6.12. This was initially mistaken for an HDMI issue (the screen would blank during USB reconnect events). Likely a `hid-multitouch` driver regression or power management change in the new kernel. Workaround: use USB power-only cable (no data) for the touchscreen display. Touch input is not currently needed.

**Power supply issue:**
The Pi was also experiencing instability (crashes, unreachable via SSH) due to the power supply being shared with too many peripherals. Resolved by adding dedicated power supplies for peripherals.

## 2026-02-17 - WiFi Disabled

Disabled WiFi to save power and avoid confusion about which network interface is active. The Pi now connects exclusively via Ethernet.

**What was done:**
- `nmcli radio wifi off` — disables WiFi radio via NetworkManager (persists across reboots)
- `nmcli connection modify "Moominvalley" connection.autoconnect no` — prevents auto-connecting
- `systemctl disable wpa_supplicant` — prevents wpa_supplicant starting on boot
- Commented out wlan0 in `/etc/network/interfaces`

**Note:** NetworkManager manages wlan0 on this system, not ifupdown. Commenting out wlan0 in `/etc/network/interfaces` and disabling `wpa_supplicant` alone was NOT sufficient — NM brought WiFi back up on every reboot. The key command is `nmcli radio wifi off`.

**To re-enable:**
```bash
ssh nomadpi 'nmcli radio wifi on && nmcli connection modify "Moominvalley" connection.autoconnect yes'
```

## 2026-02-17 - Network Reconfiguration: Dual-Interface with Ethernet Priority

**Problem:** Pi was configured with Ethernet disabled (`AUTO_SETUP_NET_ETHERNET_ENABLED=0` in dietpi.txt) and WiFi as the sole network interface. The `/etc/network/interfaces` file had contradictory config — `iface eth0 inet dhcp` with stale static IP lines (`address 192.168.0.100`, `gateway 192.168.0.1`) that overrode DHCP. When connecting the Pi via Ethernet to a new GL.inet karaoke router (192.168.8.0/24), it couldn't obtain a DHCP lease on the new subnet.

**Root Cause:**
1. Ethernet was disabled in DietPi config (`AUTO_SETUP_NET_ETHERNET_ENABLED=0`)
2. `/etc/network/interfaces` had orphaned static IP lines under the `dhcp` stanza (leftover from original FoxTag device config), which interfered with DHCP
3. No metric was configured, so there was no defined priority between interfaces

**Changes Made:**
1. **Enabled Ethernet** — set `AUTO_SETUP_NET_ETHERNET_ENABLED=1` in `/boot/dietpi.txt`
2. **Cleaned `/etc/network/interfaces`** — removed stale static IP/gateway/netmask lines from both eth0 and wlan0 stanzas, leaving pure DHCP
3. **Added routing metrics** — eth0 gets metric 100 (preferred), wlan0 gets metric 200 (fallback)
4. **DHCP reservation** — configured GL.inet router to reserve `192.168.8.106` for Pi's Ethernet MAC (`E4:5F:01:B5:5D:C0`)

**Current Network State:**
- **eth0:** 192.168.8.106/24 via DHCP (GL.inet router, metric 100 — preferred)
- **wlan0:** 192.168.1.84/24 via DHCP (Ubiquiti home network, SSID: Moominvalley, metric 200 — fallback)
- **Tailscale:** 100.66.53.104 (reconnects automatically once Pi has internet)

**SSH config updated:** `nomadpi` alias now points to `192.168.8.106`, `nomadpihomewifi` alias points to `192.168.1.84`.

**Troubleshooting technique learned:** When a Pi has a static/stale IP on a different subnet, you can add a temporary IP alias on your Mac to reach it across the same physical switch:
```bash
sudo ifconfig en10 alias 192.168.1.100 netmask 255.255.255.0  # Add alias
# ... SSH in and fix config ...
sudo ifconfig en10 -alias 192.168.1.100                        # Remove alias
```

## 2026-02-16 - Rotation Display Rewrite: tkinter → Conky

**Problem:** tkinter cannot render a transparent background on X11 — every widget has a solid fill. The `-alpha` attribute makes the entire window (text included) uniformly transparent, washing out text readability.

**Solution:** Rewrote the rotation display using **conky** with a faux-transparency approach — a full-screen window with a scaled copy of the desktop wallpaper as the background image, so the overlay blends seamlessly with the desktop.

**Changes Made:**
1. **Created `desktop/rotation_data.py`** — standalone data-fetching script (extracted from old tkinter app). Called by conky via `${execpi}` (parsed exec), outputs conky markup to stdout. Supports `--stats` flag for header stats.
2. **Created `desktop/rotation.conkyrc`** — full-screen conky window (1920x1080) with wallpaper background image, XFT anti-aliased fonts, 30-second refresh.
3. **Created `desktop/rotation-bg.png`** — 1920x1080 background image generated from the 4K wallpaper source.
4. **Deleted `desktop/rotation_display.py`** — old tkinter app fully replaced.

**Display features:**
- Header stats: `Started: M/D HH:MM  N singers | N sung | N queued`
- Up to 10 queue entries with gold singer names and light gray song text
- Color-coded badges: NOW (green), NEXT (orange), WIP (red)
- Faux-transparent background using cropped wallpaper (full ARGB transparency doesn't work reliably on the Pi's physical display)

**Key decisions / lessons learned:**
- `${execpi}` not `${execi}` — the "p" variant parses conky `${color}`/`${font}` tags in script output
- `own_window_type = 'dock'` not `'override'` — PCManFM's desktop window in LXDE sits above override-type windows
- `DejaVu Sans` not `Helvetica` — Helvetica is not installed on DietPi
- ARGB transparency (`own_window_argb_visual`) doesn't work on the Pi's physical display even with xcompmgr compositor — faux transparency with a wallpaper background image is more reliable
- Full-screen window (1920x1080 at gap 0,0) avoids background alignment issues vs. a smaller positioned window

**Dependencies changed:**
- Added: `conky-all` (apt)
- Removed: `python3-tk` (no longer needed)

**Deployment:** See [archive/NOMADPI-DETAILS.md](archive/NOMADPI-DETAILS.md) § Rotation Display for full setup and troubleshooting.

## 2026-02-16 - Karaoke Rotation Display Overlay (initial)

**Changes Made:**
1. **Created rotation display** — fetches singer rotation from a public Google Sheet and displays the next 10 singers as a persistent overlay on the left side of the screen. No pip dependencies (stdlib only).
2. **Auto-deploy restart** — `kj-controller/auto-deploy.sh` now restarts the `rotation-display` systemd service on deploy (no-op if service isn't set up yet).

**Features:**
- Fetches Google Sheet data as CSV via `gviz/tq?tqx=out:csv` endpoint
- Filters out "Done" entries, shows current singer + next 9 in queue
- Color-coded status: red (Now Singing), gold (Up Next), gray (queued)
- 30-second auto-refresh with offline fallback (shows cached data)

**Setup on a new device:** See [archive/NOMADPI-DETAILS.md](archive/NOMADPI-DETAILS.md) § Rotation Display for full setup instructions.

## 2026-02-16 - Search UI: Full Filename & Folder Path

**Problem:** Catalog search results showed only parsed `Artist - Title` which was identical for popular songs with versions from many producers (e.g., 15+ "Queen - Killer Queen" entries).

**Fix:** Search results now show the full filename (preserving disc ID prefix like `SC8231-07`) and an abbreviated folder path below each result. Mount prefix (`/mnt/...`) is stripped for brevity; full path available on hover.

## 2026-02-16 - ZIP Playback Fix (MP3 + Permissions)

**Problem:** CDG+MP3 ZIP playback failed with two issues:
1. **Permission denied** — VLC runs as `dietpi` user but temp extraction dir was created by root with restrictive permissions
2. **Played wrong file** — VLC was given the `.cdg` file (no audio, instant "finish") instead of the `.mp3`

**Fix:**
1. Extracted files are now chmod'd world-readable (`S_IROTH | S_IXOTH | S_IRGRP | S_IXGRP` on dirs, `S_IROTH | S_IRGRP` on files)
2. `extract_and_get_mp3()` now returns the `.mp3` path — VLC plays it and auto-discovers the matching `.cdg` for lyrics overlay

## 2026-02-16 - External Media Catalog & Search

**Changes Made:**
1. **SQLite FTS5 catalog** (`catalog.py`) — indexes ~415K karaoke files from a text file list into a searchable SQLite database on the SD card. Full-text search across artist, title, and disc_id fields with prefix matching.
2. **CDG+MP3 ZIP playback** (`zip_playback.py`) — extracts CDG+MP3 ZIP files to a temp directory for VLC playback. Validates against path traversal attacks.
3. **Search UI** — added search input to the web UI with 300ms debounce, result rendering with artist (purple) + title + format badge (zip=yellow, mp4=blue), and click-to-play.
4. **New routes** — `GET /search`, `GET /catalog/stats`, `POST /catalog/build`
5. **Extended `/play`** — now accepts external media mount paths and handles ZIP file extraction

**New config keys** (in `config.json`):
- `external_file_list` — path to text file listing external media
- `external_media_mount` — mount point for external media drive

**Catalog build (one-time):**
```bash
curl -X POST http://localhost:5000/catalog/build \
  -H 'Content-Type: application/json' \
  -d '{"file_list_path": "/mnt/Nomad4TBOne/HyperMule/all-karaoke-files-2025.02.28.txt"}'
```

## 2026-02-16 - Directory Restructure

**Changes Made:**
1. **Consolidated to single directory** - App now runs directly from git clone at `/opt/nomad/kjbox/kj-controller/`
2. **Eliminated file-copying deploy** - Auto-deploy now just does `git pull` + restart (no separate deploy dir)
3. **Moved git clone** from `/opt/kjbox/` to `/opt/nomad/kjbox/` (everything under `/opt/nomad/`)
4. **Moved venv + config** into git clone directory (venv and config.json are gitignored)
5. **Removed old directories** - `/opt/nomad/KJController/` and `/opt/kjbox/` deleted
6. **Updated systemd services** - Both `kj-controller.service` and `kj-autodeploy.service` point to new paths
7. **Updated config.json paths** - media_index_path, log_file, youtube_cookies_file now under `/opt/nomad/kjbox/kj-controller/`

**Directory structure:**
```
/opt/nomad/
├── kjbox/                    # Git clone (app runs from here)
│   └── kj-controller/
│       ├── app.py
│       ├── templates/
│       ├── config.json       # gitignored
│       ├── media_index.json  # gitignored
│       ├── venv/             # gitignored
│       └── auto-deploy.sh
├── YTDownloads/
├── Tracks-PublicShare/
├── FillerMusic/
└── NomadBranding/
```

## 2026-02-15 - HDMI Audio Configuration

**Issue:** VLC and all ALSA apps could not play audio via HDMI. Error: `cannot open ALSA device "default": Unknown error 524` (-ENOTSUPP).

**Root Cause (multi-layered):**
1. The 7" touchscreen provides corrupt EDID data (invalid checksum), so the kernel couldn't detect HDMI audio capabilities
2. Created custom EDID override, but initial version was missing the HDMI Vendor Specific Data Block (VSDB)
3. Without VSDB, kernel treated the output as DVI mode (no audio), even though ELD data was populated
4. The `VC4_HDMI_RAM_PACKET_ENABLE` bit (bit 16 of `HDMI_RAM_PACKET_CONFIG`) was not set in DVI mode
5. On kernel 6.12+, the vc4-hdmi PCM device only exposes `IEC958_SUBFRAME_LE` format, requiring the `iec958` ALSA plugin

**Solution Implemented:**
1. Generated custom EDID at `/lib/firmware/edid/nomadpi-hdmi.bin` with HDMI VSDB (IEEE OUI 0x000C03)
2. Added `drm.edid_firmware=HDMI-A-2:edid/nomadpi-hdmi.bin` to `/boot/cmdline.txt`
3. Configured `/etc/asound.conf` with `iec958` plugin chain for HDMI audio
4. Set HDMI audio as default ALSA output

**Result:** HDMI audio works. VLC plays karaoke videos with audio via HDMI. USB mixer (Yamaha MG-XU) also available as `usbmixer` device.

## 2026-02-15 - VLC Media Player Configuration

**Issue:** VLC launcher icon wasn't working when clicked from desktop.

**Root Cause:** VLC refuses to run as root user for security reasons. Desktop environment runs as root on NomadPi.

**Solution Implemented:**
1. Created wrapper script at `/usr/local/bin/vlc-root-wrapper` that runs VLC as `dietpi` user
2. Added `dietpi` user to `video`, `audio`, and `render` groups
3. Used `xhost +SI:localuser:dietpi` for X11 access (added to LXDE autostart)
4. Modified `/usr/share/applications/vlc.desktop` launcher to use wrapper
5. Wrapper uses `sg render` for GPU access and creates `/run/user/1000` for XDG runtime

**Result:** VLC now launches successfully from desktop icon with video (hardware-accelerated) and audio.

## 2026-02-15 - Device Repurposed for Nomad Karaoke

**Changes Made:**

1. **System Configuration**
   - Changed hostname from `FoxTag1` to `nomadpi` (in /etc/hostname and /etc/hosts)
   - Updated Bluetooth device name from "FoxTag1" to "NomadPi" (via /etc/machine-info)
   - Updated device purpose from FoxTag sticker printing kiosk to Nomad Karaoke live events
   - Device now used for video playback and AV equipment connection at karaoke events

2. **FoxTag Application Removal**
   - Stopped and removed all FoxTag Docker containers (backend, frontend, cloudflared, watchtower)
   - Removed all FoxTag Docker volumes
   - Deleted /opt/foxtag directory (736KB)
   - Removed auto-cd to /opt/foxtag from ~/.bashrc
   - Removed disabled watchdog cron file

3. **Network Configuration Preserved**
   - Tailscale: Continues running (system-level, unaffected by cleanup)
   - Cloudflare Tunnel: Token saved in NETWORK-CONFIG-BACKUP.md for potential reuse
   - WiFi and local network access maintained

4. **Documentation Updated**
   - Created NETWORK-CONFIG-BACKUP.md with Tailscale and Cloudflare tunnel information
   - Renamed FOXTAG1-DETAILS.md to NOMADPI-DETAILS.md
   - Updated CLAUDE.md with new device name and purpose
   - Removed all FoxTag application-specific sections from documentation

5. **Retained Configuration**
   - All hardware specifications remain unchanged
   - Bluetooth, VNC, desktop environment configuration preserved
   - DietPi system configuration unchanged
   - /opt/nomad directory (19GB) untouched - contains NomadBranding and Tracks-PublicShare

**Current State:**
- Clean system with no running Docker containers
- Ready for Nomad Karaoke application installation
- All remote access methods working (Tailscale at 100.66.53.104, local at 192.168.1.84)

## 2026-02-15 - Auto-Deploy from GitHub

**Changes Made:**
1. **Created auto-deploy script** at `/opt/nomad/kjbox/kj-controller/auto-deploy.sh`
   - Polls `origin/main` every 60 seconds via `git fetch`
   - Compares local HEAD to remote; on difference: `git pull` + restart kj-controller
   - Auto-installs new pip dependencies if requirements.txt changes
2. **Created systemd service** `kj-autodeploy.service` (enabled, starts on boot)

**Workflow:** Edit code on Mac > `git push` > Pi auto-deploys within ~60 seconds

## 2026-02-15 - Multi-Folder Media Scanning & Descriptive Downloads

**Changes Made:**
1. **New YouTube download naming** - Files now saved as `{youtube_id}__{channel}__{title}.mp4` instead of random 8-char IDs
2. **Central media index** - Single `media_index.json` replaces per-video `.json` sidecar files
3. **Config file** - `config.json` defines download folder and media folders to scan
4. **Multi-folder recursive scanning** - Scans `/opt/nomad/YTDownloads/`, `/opt/nomad/Tracks-PublicShare/`, and `/root/kjdata/videos/`
5. **Path-based playback** - Play/delete by file path instead of opaque video ID
6. **Rescan button** - UI button to rescan all media folders
7. **Delete restrictions** - Only files in download folder can be deleted from UI
8. **Folder grouping** - Media list shows folder headers when files come from multiple folders
9. **New download location** - YouTube downloads now go to `/opt/nomad/YTDownloads/`

**Media count after initial scan:** 2,434 files from Tracks-PublicShare

## 2026-02-15 - KJ Controller Deployed

**Changes Made:**
1. **Deployed KJ Controller** to `/opt/kj-controller/`
   - Simplified app.py: removed SocketIO/external screen sync (no longer needed)
   - Added audio device switching (HDMI <> USB mixer) via dropdown in web UI
   - VLC instances run as `dietpi` user via `sudo -u dietpi env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000`
   - Flask server on port 5000, karaoke VLC on 8080, filler VLC on 8081

2. **Created systemd service** (`kj-controller.service`)
   - Runs as root, VLC subprocesses as dietpi
   - ExecStartPre grants X11 access (`xhost +SI:localuser:dietpi`) and creates `/run/user/1000`
   - After=graphical.target ensures X11 display is available
   - Restart=always with 5-second delay

3. **Installed dependencies**
   - Installed `python3.11-venv` package (was missing)
   - Created venv at `/opt/kj-controller/venv/`
   - Installed Flask, requests, yt-dlp

4. **Set file permissions**
   - Made `/root/kjdata/` readable by dietpi user (VLC needs access to video/music files)

**Verification:**
- Service running: `systemctl status kj-controller` shows active
- Two VLC processes running as dietpi user
- Flask API responding at `http://192.168.1.84:5000/`
- Web UI accessible from browser on local network

## 2026-02-15 - Bluetooth Configuration

**Changes Made:**
1. **Enabled Bluetooth Pairing**
   - Set `AlwaysPairable = true` in `/etc/bluetooth/main.conf`
   - Device now accepts pairing requests at all times

2. **Permanent Discovery Mode**
   - Set `DiscoverableTimeout = 0` in `/etc/bluetooth/main.conf`
   - Device stays discoverable indefinitely (no 3-minute timeout)
   - "NomadPi" is always visible to nearby Bluetooth devices

3. **Enabled Bluetooth Services**
   - Bluetooth service running and enabled on boot
   - Controller hci0 (Cypress Semiconductor) configured and active

**Current Bluetooth Configuration:**
- Name: NomadPi (updated 2026-02-15)
- Address: E4:5F:01:B5:5D:C3
- Always discoverable and pairable
- Bluetooth 5.0 support

## 2026-02-15 - Display Management & Watchdog Fix

**Issues Resolved:**
- Desktop environment was crashing every 60 seconds
- Multiple X sessions causing display inconsistencies between physical screen, HDMI, and VNC
- Chromium kiosk watchdog running inappropriately in desktop mode

**Changes Made:**
1. **Disabled Chromium Kiosk Watchdog**
   - Removed the kiosk watchdog cron file (previously at `/etc/cron.d/foxtag-watchdog`)
   - Watchdog was designed for kiosk mode only; incompatible with desktop mode

2. **Implemented Single X Session Enforcement**
   - Created `/usr/local/bin/startx-single` wrapper script
   - Modified `/boot/dietpi/dietpi-login` to use wrapper
   - Automatically prevents multiple X servers from running
   - Ensures all displays (physical, HDMI, VNC) stay synchronized

3. **Installed Additional Tools**
   - Installed `scrot` for remote screenshot capability

**Current Configuration:**
- Autostart Mode: 2 (Desktop autologin with LXDE)
- Single X server on `:0`
- All video outputs mirrored/synchronized
- VNC Service Mode shares physical HDMI display
- 7" touchscreen connected via HDMI-2 at 1920x1080 (via custom EDID override)

---

**Note:** This change log was initially generated on 2026-02-15 and is updated as the system configuration changes.
