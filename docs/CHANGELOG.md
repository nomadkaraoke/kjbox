# Change Log

NomadPi system configuration changes. For current configuration details, see [archive/NOMADPI-DETAILS.md](archive/NOMADPI-DETAILS.md).

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
