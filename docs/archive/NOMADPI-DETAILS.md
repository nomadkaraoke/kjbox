# NomadPi - Raspberry Pi Configuration Guide

**Last Updated:** 2026-02-16
**Purpose:** Nomad Karaoke live events - video playback and AV equipment connection
**Location:** Local network at 192.168.1.84

---

## 🖥️ Hardware Specifications

- **Model:** Raspberry Pi 4 Model B (aarch64)
- **Manufacturer:** Sony UK
- **PCB Revision:** 5 (Hardware Revision: b03115)
- **CPU:** 4 cores, ARM aarch64
- **RAM:** 2048 MB (2 GB)
- **Storage:** 256 GB SD Card (`/dev/mmcblk0p2`)
  - **Root:** 230 GB total, 9.0 GB used, 212 GB available (5% used)
  - **Boot:** 127 MB partition at `/boot/firmware`
- **WiFi:** Onboard WiFi enabled (MAC: e4:5f:01:b5:5d:c1)
- **Ethernet:** Onboard, currently disabled (MAC: e4:5f:01:b5:5d:c0)

## 🌐 Network Configuration

### Primary Network Access
- **Hostname:** `nomadpi` / `NomadPi`
- **Local IP:** 192.168.1.84/24 (via WiFi)
- **Gateway:** 192.168.1.1
- **DNS:** Provided via DHCP
- **WiFi SSID:** Configured via `/boot/dietpi-wifi.txt`

### Tailscale VPN
- **Enabled:** Yes
- **Tailscale IP:** 100.66.53.104
- **IPv6:** fd7a:115c:a1e0::d601:356d
- **Status:** Connected, managed by beveradb@github
- **Interface:** tailscale0
- **Other devices on network:**
  - andrewbeveridgembpm3 (macOS) - offline

### SSH Access Methods

**Local Network:**
```bash
ssh nomadpi               # Using local .ssh/config
ssh root@192.168.1.84     # Direct IP
```

**Via Tailscale:**
```bash
ssh root@100.66.53.104
```

**Authorized SSH Key:**
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKlZs39JlgHhMNmr730g5F9ASz5e6JhbOA3Vp+O1+P89 andrew@beveridge.uk
```

**SSH Configuration:**
- **Server:** OpenSSH
- **Password authentication:** Disabled (key-only)
- **X11 Forwarding:** Enabled
- **Port:** 22 (default)

### Docker Networks
- **docker0:** 172.17.0.1/16 (default bridge)
- **br-329f6cd3288e:** 172.18.0.1/16 (custom bridge)
- **br-b0474949f557:** 172.19.0.1/16 (custom bridge)

## 🐧 Operating System

- **Distribution:** DietPi (Debian 12 Bookworm)
- **Kernel:** 6.12.62+rpt-rpi-v8 #1 SMP PREEMPT
- **Architecture:** aarch64 (64-bit ARM)
- **Init System:** systemd

## 📡 Bluetooth Configuration

### Status
- **Enabled:** Yes
- **Controller:** hci0 (Cypress Semiconductor)
- **Address:** E4:5F:01:B5:5D:C3
- **Name:** NomadPi
- **Powered:** Yes
- **Discoverable:** Yes (always)
- **Pairable:** Yes (always)

### Configuration
File: `/etc/bluetooth/main.conf`
```
AlwaysPairable = true
DiscoverableTimeout = 0
```

**Key Settings:**
- **AlwaysPairable:** Enabled - device always accepts pairing requests
- **DiscoverableTimeout:** 0 - device stays discoverable indefinitely (not just 3 minutes)

### Hardware Details
- **Type:** Primary
- **Bus:** UART
- **HCI Version:** 5.0 (Bluetooth 5.0)
- **Manufacturer:** Cypress Semiconductor (305)
- **Supported Services:**
  - Generic Attribute Profile (GATT)
  - Generic Access Profile (GAP)
  - PnP Information
  - A/V Remote Control Target
  - A/V Remote Control
  - Device Information

### Pairing with NomadPi

**From any Bluetooth device (phone, laptop, etc.):**
1. Open Bluetooth settings on your device
2. Look for "NomadPi" in available devices list
3. Tap/click to pair
4. Confirm pairing code if prompted (on both devices)

**The device is permanently discoverable** - you can pair with it at any time without needing to enable discovery mode first.

### Bluetooth Management Commands

**Check Status:**
```bash
ssh nomadpi 'bluetoothctl show'
ssh nomadpi 'hciconfig -a'
```

**List Paired Devices:**
```bash
ssh nomadpi 'echo "devices" | bluetoothctl'
```

**Scan for Nearby Devices:**
```bash
ssh nomadpi 'echo "scan on" | timeout 10 bluetoothctl'
```

**Enable/Disable Discovery:**
```bash
ssh nomadpi 'echo "discoverable on" | bluetoothctl'
ssh nomadpi 'echo "discoverable off" | bluetoothctl'
```

**Power Control:**
```bash
ssh nomadpi 'echo "power on" | bluetoothctl'
ssh nomadpi 'echo "power off" | bluetoothctl'
```

**Disconnect a Device:**
```bash
ssh nomadpi 'echo "disconnect <MAC_ADDRESS>" | bluetoothctl'
```

**Remove/Unpair a Device:**
```bash
ssh nomadpi 'echo "remove <MAC_ADDRESS>" | bluetoothctl'
```

**Interactive Bluetooth Shell:**
```bash
ssh nomadpi 'bluetoothctl'
# Then use commands: scan on, devices, pair <MAC>, connect <MAC>, etc.
```

### Service Management
```bash
# Check Bluetooth service status
ssh nomadpi 'systemctl status bluetooth'

# Restart Bluetooth service
ssh nomadpi 'systemctl restart bluetooth'

# View Bluetooth logs
ssh nomadpi 'journalctl -u bluetooth -f'
```

### User Accounts
- **root:** UID 0, shell: /bin/bash (primary user, autologin on tty1)
- **dietpi:** UID 1000, shell: /bin/bash (secondary admin user)

### DietPi Version & Configuration Files
- **Config:** `/boot/dietpi.txt` (main configuration)
- **Autostart Index:** `/boot/dietpi/.dietpi-autostart_index`
- **Installed Software:** `/boot/dietpi/.installed`
- **DietPi Scripts:** `/boot/dietpi/dietpi-*`

## 🚀 Boot & Autostart Configuration

### Current Autostart Mode
**Mode 2: Desktop Autologin**
- Automatically logs in as `root` on tty1
- Starts LXDE desktop environment
- Configured in `/boot/dietpi/.dietpi-autostart_index`

### Autostart Modes Available
```
0  = Console (manual login)
7  = Console autologin
1  = Kodi
2  = Desktop autologin (CURRENT)
16 = Desktop (manual login)
11 = Chromium kiosk
14 = Custom script (background)
17 = Custom script (foreground)
```

### Changing Autostart Mode
```bash
# Via DietPi tool (recommended)
ssh nomadpi '/boot/dietpi/dietpi-autostart <mode_number>'

# Example: Switch to console autologin
ssh nomadpi '/boot/dietpi/dietpi-autostart 7'

# Verify change
ssh nomadpi 'cat /boot/dietpi/.dietpi-autostart_index'
```

### Getty Autologin Configuration
File: `/etc/systemd/system/getty@tty1.service.d/dietpi-autologin.conf`
```
[Service]
ExecStart=
ExecStart=-/sbin/agetty -a root -J %I $TERM
```

## 🖼️ Display & Graphics Configuration

### Display Manager
- **Window Manager:** LXDE (Lightweight X11 Desktop Environment)
- **Display Manager:** None (direct X session via xinit/startx)
- **X Server:** Xorg
- **X Display:** :0 on vt1

### LXDE Desktop Components
**Autostart:** `/etc/xdg/lxsession/LXDE/autostart`
```
@lxpanel --profile LXDE
@pcmanfm --desktop --profile LXDE
@xscreensaver -no-splash
xhost +SI:localuser:dietpi
```
**Note:** The `xhost` line grants the `dietpi` user X11 display access, required for VLC (which runs as `dietpi` via the root-user workaround).

### Boot Configuration
File: `/boot/config.txt` (active settings, comments removed)
```
hdmi_blanking=1           # Enable screen blanking (standby after 10 min)
disable_overscan=1        # No overscan borders
gpu_mem_256=76            # GPU memory allocation
gpu_mem_512=76
gpu_mem_1024=76
disable_splash=1          # No boot splash screen
dtparam=audio=on          # Enable audio
enable_uart=0             # UART disabled (saves power, avoids WiFi freq conflict)
dtparam=sd_poll_once      # Reduce SD card polling (less CPU usage)
temp_limit=75             # Thermal throttle at 75C
initial_turbo=20          # 20 second turbo at boot
arm_64bit=1               # 64-bit kernel
dtoverlay=vc4-kms-v3d     # KMS video driver
```

File: `/boot/cmdline.txt` (appended parameters)
```
drm.edid_firmware=HDMI-A-2:edid/nomadpi-hdmi.bin  # Custom EDID for HDMI audio
```

**Note:** The firmware prepends its own parameters to cmdline.txt. Our custom parameters are appended after the firmware defaults.

### X11 Session
- **Started via:** xinit/startx (via wrapper script)
- **Configuration:** `/etc/X11/xinit/xinitrc`
- **Session script:** `/etc/X11/Xsession`
- **Log:** `/var/log/Xorg.0.log`

### Single X Session Enforcement
**Purpose:** Ensures only one X server runs at a time, preventing display inconsistencies between physical screen, HDMI, and VNC.

**Implementation:** Custom wrapper script at `/usr/local/bin/startx-single`
```bash
#!/bin/bash
# Wrapper to ensure only one X session runs at a time

# Kill any existing X servers
for pid in $(pgrep -x Xorg); do
    echo "Killing existing X server (PID: $pid)"
    kill $pid
    sleep 1
    # Force kill if still running
    kill -9 $pid 2>/dev/null
done

# Wait for X to fully terminate
sleep 1

# Start X normally
exec /usr/bin/startx "$@"
```

**Integration:**
- Modified `/boot/dietpi/dietpi-login` to call `/usr/local/bin/startx-single` instead of `startx`
- Automatically cleans up stale X processes before starting new sessions
- Ensures physical display, HDMI output, and VNC always show the same content

**Display Synchronization:**
- All video outputs (HDMI-2, external monitors) show identical content
- VNC Service Mode mirrors the physical display exactly
- No separate X sessions or virtual desktops

## 🖥️ VNC Remote Access

### Current Configuration: Service Mode
**Service Mode** shares the physical HDMI display - what you see in VNC is identical to the physical screen.

### Connection Details
- **Address:** `192.168.1.84` (no port needed for Service Mode)
- **Password:** Set via `vncpasswd` (stored encrypted in VNC config)
- **Service:** `vncserver-x11-serviced.service`
- **Status:** Enabled and running

### VNC Configuration Files
- **Service Mode Config:** `/root/.vnc/config.d/vncserver-x11`
- **Virtual Mode Config:** `/root/.vnc/config.d/Xvnc` (not used)
- **Password File:** `/root/.vnc/config.d/vncserver-x11`

### VNC Modes

#### Service Mode (Current)
- **Shares physical display** - same desktop on HDMI and VNC
- **Service:** `vncserver-x11-serviced.service`
- **Requires:** Desktop running on physical display (autostart mode 2 or 16)
- **Connection:** `192.168.1.84` (port 5900)

#### Virtual Mode
- **Separate virtual desktop** - independent from HDMI
- **Service:** `vncserver.service` (DietPi wrapper)
- **Resolution:** 1024x600x16 (configured in `/boot/dietpi.txt`)
- **Connection:** `192.168.1.84:1` (port 5901)

### Switching VNC Modes

**Switch to Service Mode (shares HDMI):**
```bash
ssh nomadpi 'systemctl stop vncserver && systemctl disable vncserver'
ssh nomadpi 'systemctl enable vncserver-x11-serviced && systemctl start vncserver-x11-serviced'
ssh nomadpi '/boot/dietpi/dietpi-autostart 2'  # Enable desktop on HDMI
ssh nomadpi 'systemctl restart getty@tty1'
```

**Switch to Virtual Mode (separate desktop):**
```bash
ssh nomadpi 'systemctl stop vncserver-x11-serviced && systemctl disable vncserver-x11-serviced'
ssh nomadpi 'systemctl enable vncserver && systemctl start vncserver'
ssh nomadpi '/boot/dietpi/dietpi-autostart 7'  # Optional: disable HDMI desktop
```

### VNC Configuration in dietpi.txt
```
SOFTWARE_VNCSERVER_WIDTH=1024
SOFTWARE_VNCSERVER_HEIGHT=600
SOFTWARE_VNCSERVER_DEPTH=16
SOFTWARE_VNCSERVER_DISPLAY_INDEX=1
SOFTWARE_VNCSERVER_SHARE_DESKTOP=0
```

## 🎬 VLC Media Player Configuration

### Running as Root Workaround
VLC refuses to run as root user for security reasons. On NomadPi (which runs desktop as root), a wrapper script allows VLC to run as the `dietpi` user with proper X11 access.

**Wrapper Location:** `/usr/local/bin/vlc-root-wrapper`
```bash
#!/bin/bash
# Wrapper to run VLC as dietpi user from root desktop
export DISPLAY=:0
xhost +SI:localuser:dietpi 2>/dev/null || true
if [ ! -d /run/user/1000 ]; then
    mkdir -p /run/user/1000
    chown dietpi:dietpi /run/user/1000
    chmod 700 /run/user/1000
fi
exec sg render -c "sudo -u dietpi XDG_RUNTIME_DIR=/run/user/1000 DISPLAY=:0 /usr/bin/vlc \"$@\""
```

**Desktop Launcher:** `/usr/share/applications/vlc.desktop` is configured to use the wrapper.

**Manual Launch:**
```bash
# From desktop/terminal as root
ssh nomadpi '/usr/local/bin/vlc-root-wrapper'

# Direct as dietpi user (need xhost grant first)
ssh nomadpi 'DISPLAY=:0 xhost +SI:localuser:dietpi && sg render -c "sudo -u dietpi XDG_RUNTIME_DIR=/run/user/1000 DISPLAY=:0 vlc"'
```

**User Configuration:**
- VLC runs as: `dietpi` user
- Groups: `dietpi`, `video`, `audio`, `render`
- X11 access via `xhost +SI:localuser:dietpi` (granted at LXDE autostart)
- XDG_RUNTIME_DIR: `/run/user/1000` (created by wrapper if missing)

## 🔊 Audio Configuration

> **See [../AUDIO.md](../AUDIO.md)** for full audio documentation: HDMI/ALSA setup, device switching, live event routing, and the abandoned USB-HDMI mirroring experiment.

## 📦 Installed DietPi Software

Software installed via `dietpi-software`:

| ID  | Software | State |
|-----|----------|-------|
| 0   | File Server Samba Client | Installed |
| 5   | ? | Installed |
| 6   | ? | Installed |
| 17  | ? | Installed |
| 23  | LXDE Desktop | Installed |
| 103 | ? | Installed |
| 104 | Dropbear SSH | Not Installed |
| 105 | OpenSSH Server | Installed |
| 113 | Git | Installed |
| 120 | RealVNC Server | Installed |
| 134 | Docker | Installed |
| 162 | Tailscale | Installed |

### Additional Installed Packages (via apt)
- **scrot** - Screenshot utility for X11 (installed 2026-02-15)
- **vlc** - VLC media player 3.0.23 (installed via DietPi, runs as dietpi user)

### Installing Additional Software
```bash
# Browse software catalog
ssh nomadpi '/boot/dietpi/dietpi-software'

# Install software by ID
ssh nomadpi '/boot/dietpi/dietpi-software install <ID>'

# Example: Install TigerVNC (ID 28)
ssh nomadpi '/boot/dietpi/dietpi-software install 28'

# List all available software
ssh nomadpi '/boot/dietpi/dietpi-software list'
```

## 🐳 Docker Configuration

### Docker Status
- **Version:** Latest (installed via DietPi)
- **Status:** Running and enabled on boot
- **Socket:** `/var/run/docker.sock`
- **Data Root:** `/mnt/dietpi_userdata/docker-data`
- **Service:** `docker.service` with DietPi customizations

### Docker Management
```bash
# View running containers
ssh nomadpi 'docker ps'

# View all containers (including stopped)
ssh nomadpi 'docker ps -a'

# View logs for a container
ssh nomadpi 'docker logs <container-name>'

# Restart containers
ssh nomadpi 'docker compose restart'
```

## 🎵 Nomad Karaoke Configuration

### Purpose
NomadPi is configured for Nomad Karaoke live events:
- Video playback for karaoke performances
- AV equipment connection (HDMI output to displays/projectors)
- Portable event setup with touchscreen control

### Hardware
- **7" Touchscreen Display:**
  - **Touch Input:** WingCool Inc. TouchScreen (USB HID device, ID 27c6:0818)
  - **Video Output:** Connected via HDMI-2 (micro-HDMI port) at 1920x1080 (via custom EDID)
  - **Built-in speakers:** Yes (small, via HDMI audio)
  - **Note:** This is a USB touchscreen with HDMI video, not a DSI ribbon cable display
  - **Note:** The touchscreen has corrupt EDID data; custom EDID override is required (see Audio Configuration)
- **External Display/Projector:** Connects to HDMI-1 (main full-size HDMI) for audience-facing output via AV unit
- **Yamaha MG-XU USB Mixer:** For microphone audio mixing (connected via USB but VLC outputs to HDMI, not mixer)
- **Shure SLX-D Wireless Mics (x2):** Vocal microphones, analog input to Yamaha mixer
- **Bose S1 Pro:** Powered monitor speaker for amplified vocal audio (from mixer main out)
- **USB Hub:** VIA Labs Hub (for peripherals)

### KJ Controller
A web-based karaoke show management app. Runs directly from the git clone at `/opt/nomad/kjbox/kj-controller/`. It provides:
- Remote control interface at `http://192.168.1.84:5000/` (accessible from any browser on the local network)
- YouTube video downloading via yt-dlp
- Dual VLC instance management (karaoke player + filler music with crossfading)
- Audio output switching between HDMI and USB mixer (restarts VLC instances)
- VLC runs as `dietpi` user (wrapped with `sudo -u dietpi`) since VLC refuses root

**Service:** `kj-controller.service` (enabled, starts on boot)
```bash
# Check service status
ssh nomadpi 'systemctl status kj-controller'

# View logs
ssh nomadpi 'journalctl -u kj-controller -f'

# Restart service
ssh nomadpi 'systemctl restart kj-controller'

# View app log
ssh nomadpi 'tail -f /opt/nomad/kjbox/kj-controller/kj-controller.log'
```

**Configuration:**
- `/opt/nomad/kjbox/kj-controller/config.json` - All app settings (media folders, ports, audio devices, etc.)
- `/opt/nomad/kjbox/kj-controller/media_index.json` - Central index of all scanned media files

**Data Directories:**
- `/opt/nomad/YTDownloads/` - YouTube downloads (named `{youtube_id}__{channel}__{title}.mp4`)
- `/opt/nomad/Tracks-PublicShare/` - Karaoke video tracks (scanned, not deletable from UI)
- `/opt/nomad/FillerMusic/` - Filler music files (mp3, wav, ogg, flac)

**config.json:**
```json
{
  "download_folder": "/opt/nomad/YTDownloads",
  "media_folders": [
    "/opt/nomad/YTDownloads",
    "/opt/nomad/Tracks-PublicShare/MP4"
  ],
  "filler_music_dir": "/opt/nomad/FillerMusic",
  "media_index_path": "/opt/nomad/kjbox/kj-controller/media_index.json",
  "log_file": "/opt/nomad/kjbox/kj-controller/kj-controller.log",
  "youtube_cookies_file": "/opt/nomad/kjbox/kj-controller/youtube_cookies.txt",
  "flask_port": 5000
}
```

**Auto-Deploy:** `kj-autodeploy.service` (enabled, starts on boot)
- Polls GitHub every 60 seconds for new commits on `main`
- On change: `git pull` and restart kj-controller (app runs from git clone, no file copying)
- **Workflow:** edit on Mac → `git push` → deployed to Pi within ~60 seconds
```bash
# View auto-deploy logs
ssh nomadpi 'journalctl -u kj-autodeploy -f'

# Disable auto-deploy (stops polling, won't start on boot)
ssh nomadpi 'systemctl disable --now kj-autodeploy'

# Re-enable auto-deploy
ssh nomadpi 'systemctl enable --now kj-autodeploy'

# Manual deploy (if auto-deploy is disabled)
ssh nomadpi 'cd /opt/nomad/kjbox && git pull && systemctl restart kj-controller'
```

**Architecture:**
- Flask app on port 5000 (threaded mode)
- Karaoke VLC on port 8080 (HTTP control interface, fullscreen)
- Filler VLC on port 8081 (HTTP control interface, looping)
- Both VLC instances use `--aout alsa --alsa-audio-device <device>` for audio routing
- Audio device switching restarts both VLC instances (~5 seconds)
- Media index (`media_index.json`) caches file metadata; rebuilt on rescan or first startup
- Multi-folder scanning: walks all configured `media_folders` recursively
- Delete restricted to `download_folder` only (prevents deleting shared media)

See [kj-controller/](kj-controller/) for source code

### Rotation Display
A conky-based overlay that fetches the singer rotation from a public Google Sheet and displays the next 10 singers on the left side of the screen. Uses `desktop/rotation_data.py` (stdlib only, no pip deps) as the data source, called by conky via `${execi}`. Designed for venue visibility during live karaoke events.

**Key advantage:** Conky supports true ARGB transparent backgrounds on X11, so text is fully opaque while the background is see-through — wallpaper/video visible behind the overlay.

**Service:** `rotation-display.service` (enabled, starts on boot)
```bash
# Check service status
ssh nomadpi 'systemctl status rotation-display'

# View logs
ssh nomadpi 'journalctl -u rotation-display -f'

# Restart service
ssh nomadpi 'systemctl restart rotation-display'

# Stop overlay (e.g. when not running karaoke)
ssh nomadpi 'systemctl stop rotation-display'
```

**Configuration:**
- `desktop/rotation.conkyrc` — conky layout settings (margins, width, refresh interval, fonts)
- `desktop/rotation_data.py` — data fetcher settings (Sheet ID, column mapping, max entries, colors)

Key tunables in `rotation_data.py`:
- `SHEET_ID` — Google Sheet ID (must be published to web)
- `SHEET_GID` — Tab index (default: `0`)
- `COL_*` — Column indices for singer, song/artist, status
- `MAX_ENTRIES` — Number of queue entries to show (default: `10`)

Key tunables in `rotation.conkyrc`:
- `gap_x` / `gap_y` — Margins from screen edge (default: `70` / `60`)
- `minimum_width` / `maximum_width` — Window width (default: `600`)
- `update_interval` — Refresh interval in seconds (default: `30`)

**Setup on a new device:**
```bash
# 1. Install conky and compositor
apt-get install -y conky-all xcompmgr

# 2. Create systemd service
cat > /etc/systemd/system/rotation-display.service << 'EOF'
[Unit]
Description=Karaoke Rotation Display Overlay
After=graphical.target

[Service]
Type=simple
Environment=DISPLAY=:0
ExecStartPre=/bin/bash -c "xhost +SI:localuser:root"
ExecStartPre=/bin/bash -c "pgrep xcompmgr || xcompmgr &"
ExecStart=/usr/bin/conky -c /opt/nomad/kjbox/desktop/rotation.conkyrc
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
EOF

# 3. Enable and start
systemctl daemon-reload && systemctl enable --now rotation-display
```

**Google Sheet requirements:**
- Sheet must be published to web (File > Share > Publish to web)
- Expected columns (0-indexed): `#`, `Singer`, `Song & Artist`, `Status`
- Rows with Status = "Done" are filtered out
- First non-done entry is highlighted as "Now Singing"

### Chromium Kiosk Watchdog (DISABLED)
**Status:** **DISABLED** as of 2026-02-15

**Note:** A Chromium kiosk watchdog previously existed but has been disabled. System now runs in Desktop mode (autostart 2) for full desktop environment access via VNC.

## 🔧 System Services

### Running Services
```
containerd.service             - Container runtime
cron.service                   - Background tasks
dbus.service                   - System message bus
docker.service                 - Docker engine
getty@tty1.service             - Console on tty1
kj-autodeploy.service          - Auto-deploy kj-controller from GitHub (polls every 60s)
kj-controller.service          - KJ Controller (karaoke show management, port 5000)
rotation-display.service       - Singer rotation overlay (Google Sheets → conky)
NetworkManager.service         - Network management
ssh.service                    - SSH server
systemd-journald.service       - System logging
systemd-logind.service         - Login management
systemd-udevd.service          - Device management
tailscaled.service             - Tailscale VPN
vncserver-x11-serviced.service - VNC Server (Service Mode)
wpa_supplicant.service         - WiFi authentication
```

### Service Management
```bash
# Check service status
ssh nomadpi 'systemctl status <service-name>'

# Start/stop service
ssh nomadpi 'systemctl start <service-name>'
ssh nomadpi 'systemctl stop <service-name>'

# Enable/disable on boot
ssh nomadpi 'systemctl enable <service-name>'
ssh nomadpi 'systemctl disable <service-name>'

# View logs
ssh nomadpi 'journalctl -u <service-name> -f'
```

## 💾 Storage & Memory

### Memory
- **Total RAM:** 1.8 GB
- **Used:** 368 MB
- **Free:** 912 MB
- **Buff/Cache:** 609 MB
- **Available:** 1.4 GB
- **Swap:** 946 MB (zram-based)

### Disk Usage
- **Root (/):** 230 GB total, 9.0 GB used (5%)
- **Boot (/boot/firmware):** 127 MB total, 36 MB used
- **Logs (/var/log):** 50 MB tmpfs (RAMlog)
- **Temp (/tmp):** 1.4 GB tmpfs

### DietPi RAMlog
DietPi uses RAMlog to reduce SD card wear by keeping logs in RAM.
- **Max size:** 50 MB
- **Hourly clear:** Enabled
- **Store location:** `/var/tmp/dietpi/logs/dietpi-ramlog_store`

## 🔄 System Maintenance

### Updates

**DietPi Update:**
```bash
ssh nomadpi 'dietpi-update'
```

**System Updates:**
```bash
ssh nomadpi 'apt update && apt upgrade -y'
```

**Docker Container Updates:**
```bash
ssh nomadpi 'docker compose pull && docker compose up -d'
```

### Backup & Restore
```bash
# Backup DietPi configuration
ssh nomadpi 'dietpi-backup 1'  # Create backup

# Restore from backup
ssh nomadpi 'dietpi-backup 2'  # Restore from backup
```

### Reboot & Shutdown
```bash
# Reboot
ssh nomadpi 'reboot'

# Shutdown
ssh nomadpi 'shutdown -h now'
```

### Cleaning Up
```bash
# DietPi cleaner (removes temp files, logs, APT cache)
ssh nomadpi 'dietpi-cleaner'

# Docker cleanup
ssh nomadpi 'docker system prune -a'
```

## 🛠️ Troubleshooting

> **See [../TROUBLESHOOTING.md](../TROUBLESHOOTING.md)** for troubleshooting guides and common tasks (display issues, VNC, network, Bluetooth, screenshots, etc.).

## 📝 Configuration File Locations

### DietPi
- `/boot/dietpi.txt` - Main configuration
- `/boot/dietpi/.dietpi-autostart_index` - Autostart mode
- `/boot/dietpi/.installed` - Installed software
- `/boot/dietpi/.hw_model` - Hardware model info
- `/boot/dietpi-wifi.txt` - WiFi credentials

### System
- `/boot/config.txt` - Raspberry Pi boot config
- `/etc/ssh/sshd_config` - SSH server config
- `/etc/systemd/system/getty@tty1.service.d/dietpi-autologin.conf` - Autologin
- `/etc/bluetooth/main.conf` - Bluetooth configuration

### Display & Desktop
- `/etc/X11/xinit/xinitrc` - X session startup
- `/etc/xdg/lxsession/LXDE/autostart` - LXDE autostart (includes xhost grant for dietpi)
- `/var/log/Xorg.0.log` - X server log
- `/usr/local/bin/startx-single` - Custom X startup wrapper (prevents multiple sessions)
- `/boot/dietpi/dietpi-login` - DietPi login script (modified to use startx-single)

### Audio & Video
- `/etc/asound.conf` - ALSA configuration (HDMI default, iec958 plugin chain)
- `/lib/firmware/edid/nomadpi-hdmi.bin` - Custom EDID with HDMI VSDB for audio support
- `/usr/local/bin/vlc-root-wrapper` - VLC launcher wrapper (runs VLC as dietpi user)
- `/usr/share/applications/vlc.desktop` - VLC desktop launcher (uses wrapper)

### VNC
- `/root/.vnc/config.d/vncserver-x11` - Service Mode config
- `/root/.vnc/config.d/Xvnc` - Virtual Mode config
- `/lib/systemd/system/vncserver-x11-serviced.service` - Service Mode systemd unit
- `/etc/systemd/system/vncserver.service` - Virtual Mode systemd unit (DietPi wrapper)

### Rotation Display
- `/opt/nomad/kjbox/desktop/rotation.conkyrc` - Conky configuration (layout, margins, refresh)
- `/opt/nomad/kjbox/desktop/rotation_data.py` - Data fetcher script (Google Sheet → conky markup)
- `/etc/systemd/system/rotation-display.service` - systemd service unit (ExecStart=conky)

### KJ Controller
- `/opt/nomad/kjbox/` - Git clone of kjbox repo (app runs directly from here)
- `/opt/nomad/kjbox/kj-controller/app.py` - Main Flask application
- `/opt/nomad/kjbox/kj-controller/templates/index.html` - Web UI template
- `/opt/nomad/kjbox/kj-controller/requirements.txt` - Python dependencies
- `/opt/nomad/kjbox/kj-controller/config.json` - App configuration (gitignored)
- `/opt/nomad/kjbox/kj-controller/media_index.json` - Central media file index (gitignored)
- `/opt/nomad/kjbox/kj-controller/venv/` - Python virtual environment
- `/opt/nomad/kjbox/kj-controller/auto-deploy.sh` - Auto-deploy script (polls GitHub)
- `/etc/systemd/system/kj-controller.service` - systemd service unit
- `/etc/systemd/system/kj-autodeploy.service` - Auto-deploy systemd unit
- `/opt/nomad/YTDownloads/` - YouTube downloads
- `/opt/nomad/FillerMusic/` - Filler music files

### Docker
- `/var/run/docker.sock` - Docker socket
- `/mnt/dietpi_userdata/docker-data` - Docker data root
- `/etc/systemd/system/docker.service.d/` - Docker service overrides

### Data & Content
- `/opt/nomad/` - Nomad Karaoke root directory
  - `kjbox/` - Git clone (app + docs)
  - `YTDownloads/` - YouTube downloads
  - `Tracks-PublicShare/` - Karaoke video tracks (MP4-720p)
  - `FillerMusic/` - Filler music files
  - `NomadBranding/` - Branding assets

## 🎓 Common Tasks

> **See [../TROUBLESHOOTING.md](../TROUBLESHOOTING.md)** for common tasks (VNC password, SSH keys, screen resolution, logs, etc.).

## 🔐 Security Notes

1. **SSH:** Password authentication disabled, key-only
2. **Root access:** Direct root login enabled (typical for embedded systems)
3. **Firewall:** Not explicitly configured (relies on Docker and Cloudflare)
4. **VNC:** Password protected, Service Mode only shares HDMI display
5. **Docker:** Containers run with host networking (backend needs NetworkManager)
6. **Tailscale:** Provides secure VPN access

## 📚 Additional Resources

- **DietPi Documentation:** https://dietpi.com/docs/
- **DietPi Forums:** https://dietpi.com/forum/
- **RealVNC Documentation:** https://help.realvnc.com/
- **Nomad Karaoke:** https://nomadkaraoke.com

---

## 📋 Change Log

> **See [../CHANGELOG.md](../CHANGELOG.md)** for the full change log of NomadPi system configuration changes.
