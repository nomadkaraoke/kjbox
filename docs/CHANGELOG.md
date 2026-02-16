# Change Log

NomadPi system configuration changes. For current configuration details, see [archive/NOMADPI-DETAILS.md](archive/NOMADPI-DETAILS.md).

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
