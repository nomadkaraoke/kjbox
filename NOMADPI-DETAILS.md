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

### HDMI Audio Setup
HDMI audio requires a custom EDID file because the 7" touchscreen provides corrupt EDID data (bad checksum), which prevents the kernel from detecting HDMI audio capabilities.

**Custom EDID:** `/lib/firmware/edid/nomadpi-hdmi.bin`
- 256-byte EDID (128-byte base + 128-byte CEA extension)
- Declares 1920x1080@60 preferred timing, monitor name "NomadPi"
- **Critical:** Includes HDMI Vendor Specific Data Block (VSDB) with IEEE OUI 0x000C03
  - Without VSDB, kernel treats output as DVI (no audio support)
  - With VSDB, kernel sets `VC4_HDMI_RAM_PACKET_ENABLE` bit for HDMI audio
- Audio: LPCM 2ch (32/44.1/48kHz, 16/20/24bit), Speaker Allocation FL/FR
- Loaded via kernel parameter: `drm.edid_firmware=HDMI-A-2:edid/nomadpi-hdmi.bin`

### ALSA Configuration
File: `/etc/asound.conf`
```
# HDMI audio output via vc4-hdmi-1 (HDMI-A-2 port)
pcm.hdmiout_raw {
    type iec958
    slave {
        pcm "hw:vc4hdmi1,0"
        format IEC958_SUBFRAME_LE
    }
    status [ 0x04 0x00 0x00 0x01 ]
}

## Shared HDMI output via dmix (allows multiple simultaneous streams)
pcm.hdmiout_dmix {
    type dmix
    ipc_key 2048
    slave {
        pcm hdmiout_raw
        rate 48000
        channels 2
        format S16_LE
    }
}

pcm.hdmiout {
    type plug
    slave {
        pcm hdmiout_dmix
    }
}

# Yamaha MG-XU USB mixer
pcm.usbmixer {
    type plug
    slave {
        pcm "hw:MGXU,0"
    }
}

# Default: HDMI output
pcm.!default {
    type plug
    slave {
        pcm hdmiout_dmix
    }
}

ctl.!default {
    type hw
    card vc4hdmi1
}
```

**Why the iec958 plugin is needed:** On kernel 6.12+, the vc4-hdmi MAI PCM device only exposes `IEC958_SUBFRAME_LE` format (raw HDMI audio frames). The `iec958` ALSA plugin handles encoding standard PCM audio (S16_LE etc.) into IEC958 subframes. The `plug` plugin on top handles sample rate and format conversion.

**Why dmix is needed:** The KJ Controller runs two VLC instances simultaneously (karaoke + filler music), both outputting to HDMI. Without `dmix`, only one process can open the ALSA device at a time - the second gets "Device or resource busy". The `dmix` plugin mixes multiple audio streams in software before passing them to the hardware device.

### Audio Devices
| Card | Name | Type | ALSA Device |
|------|------|------|-------------|
| 0 | MG-XU | Yamaha USB mixer | `usbmixer` or `hw:MGXU,0` |
| 1 | vc4-hdmi-0 | HDMI port 1 (disconnected) | `hw:vc4hdmi0,0` |
| 2 | vc4-hdmi-1 | HDMI port 2 (touchscreen) | `hdmiout` (default) |

### Switching Audio Output

**Per-app (VLC command line):**
```bash
# Play through HDMI (default)
ssh nomadpi '/usr/local/bin/vlc-root-wrapper /path/to/video.mp4'

# Play through HDMI (explicit)
ssh nomadpi '/usr/local/bin/vlc-root-wrapper --aout alsa --alsa-audio-device hdmiout /path/to/video.mp4'

# Play through Yamaha USB mixer
ssh nomadpi '/usr/local/bin/vlc-root-wrapper --aout alsa --alsa-audio-device usbmixer /path/to/video.mp4'
```

**VLC GUI:** Audio → Audio Device menu lets you switch output while playing.

**System-wide default** (changes what all apps use when no device is specified):
Edit `/etc/asound.conf` and change the `pcm.!default` slave from `hdmiout_raw` to `"hw:MGXU,0"` (or vice versa).

### Testing Audio
```bash
# Test HDMI audio
ssh nomadpi 'speaker-test -D hdmiout -c 2 -t sine -f 440 -l 1'

# Test USB mixer audio
ssh nomadpi 'speaker-test -D usbmixer -c 2 -t sine -f 440 -l 1'

# Test default output (HDMI)
ssh nomadpi 'speaker-test -c 2 -t sine -f 440 -l 1'
```

## 🎤 Live Event Audio Routing

### Current Setup (as of 2026-02-16)

The karaoke event setup uses two separate audio paths to avoid latency issues:

**Path 1 - Instrumental Playback (Pi → AV Unit → Stereo Speakers):**
- Pi outputs karaoke video + audio via **HDMI** (`hdmiout` ALSA device)
- Single HDMI cable runs to the AV unit (receiver/TV)
- AV unit drives full-size stereo speakers for instrumental/backing tracks
- Filler music between songs also goes through this path

**Path 2 - Vocal/Mic Audio (Mics → Mixer → Monitor Speaker):**
- **Shure SLX-D** wireless microphones connect to **Yamaha MG-XU** USB mixer
- Mixer's main output hard-wired to **Bose S1 Pro** powered monitor speaker
- Provides amplified singer vocals with zero additional latency
- Mixer handles all mic levels, EQ, and effects independently of the Pi

**Why two separate paths:** Karaoke is extremely latency-sensitive for vocals. Singers hearing even slight delay of their own voice causes confusion and poor performance. Keeping the mic audio path fully analog (mixer → speaker) guarantees zero digital latency.

### Equipment List
| Equipment | Role | Connection |
|-----------|------|------------|
| Raspberry Pi 4 | Video + instrumental audio playback | HDMI to AV unit |
| Shure SLX-D (x2) | Wireless microphones | Analog to mixer inputs |
| Yamaha MG-XU | USB mixer for mic audio | USB to Pi (available but unused), analog out to Bose |
| Bose S1 Pro | Amplified monitor speaker for vocals | Analog from mixer main out |
| AV unit + stereo speakers | Instrumental/video playback | HDMI from Pi |
| 7" Touchscreen | KJ Controller UI | HDMI-2 + USB to Pi |

### Abandoned: USB Mixer → HDMI Audio Mirroring

**Goal:** Eliminate the Bose S1 Pro by routing the mixer's mixed output (instrumentals + vocals) back through the Pi to HDMI, so the AV unit/stereo speakers would carry everything.

**What worked:**
- The Yamaha MG-XU sends its full stereo mix back to the Pi via USB capture (`hw:MGXU,0` capture device, S32_LE format, 48kHz)
- `alsaloop` successfully captured the mixer return and played it to HDMI output
- Required `dmix` ALSA plugin for the USB mixer so both VLC instances (karaoke + filler) could share the USB playback device simultaneously
- Audio was audible on the TV via HDMI

**Technical implementation that was working:**
```bash
# alsaloop command (ran as systemd service "audio-mirror")
alsaloop -C hw:MGXU,0 -P hdmiout -t 200000 -f S32_LE -r 48000 -c 2 --sync=0 -T -1 -A 5 -S 3

# dmix ALSA config was needed for USB mixer sharing:
# pcm.usbmixer_dmix { type dmix; ipc_key 1024; slave { pcm "hw:MGXU,0"; rate 48000; channels 2 } }
# pcm.usbmixer { type plug; slave { pcm usbmixer_dmix } }
```

**Why it was abandoned:**
1. **Latency:** ~200ms minimum buffer needed to avoid underruns. Singers would hear their own voice delayed through the stereo speakers, causing confusion/echo effect
2. **Audio dropouts:** With smaller buffers (`-t 50000`), `alsaloop` consumed 12% CPU and produced frequent `underrun for playback hdmiout` errors. The HDMI output path involves CPU-intensive format conversion (S32_LE → IEC958 subframes via the `iec958` ALSA plugin)
3. **Complexity:** Required dmix layer, alsaloop service management, and coordination with audio device switching in the app
4. **Reliability:** Even with tuned 200ms buffers and zero underruns, the additional latency and processing made it unsuitable for live vocal monitoring

**If revisiting in future:**
- A hardware solution (mixer aux send → AV unit analog input) would avoid all digital latency
- A more powerful Pi (or x86 device) might handle the format conversion without underruns at lower latency
- PulseAudio/PipeWire might handle the routing more gracefully than raw ALSA, but adds its own latency
- The fundamental issue is that the HDMI output requires IEC958 subframe encoding in software, which is CPU-intensive on the Pi 4

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

### Desktop Crashes / Multiple X Sessions
**Symptoms:**
- Physical screen shows different content than VNC
- Multiple X servers running (`:0`, `:1`, etc.)
- Desktop environment appears to crash

**Solution:**
```bash
# Kill all X servers and restart cleanly
ssh nomadpi 'killall Xorg; sleep 2; systemctl restart getty@tty1'

# Verify only one X server is running
ssh nomadpi 'ps aux | grep Xorg | grep -v grep'
```

**Prevention:** The `/usr/local/bin/startx-single` wrapper prevents multiple X sessions automatically.

### Display Outputs Not Synchronized
**Symptoms:**
- Physical 7" screen shows different content than external HDMI monitor
- VNC shows different desktop than physical displays
- Screenshots don't match what's on screen

**Cause:** Multiple X sessions running on different displays (`:0` vs `:1`)

**Solution:**
```bash
# Kill all X servers and restart cleanly
ssh nomadpi 'killall Xorg; sleep 2; systemctl restart getty@tty1'

# Or reboot for cleanest solution
ssh nomadpi 'reboot'

# Verify single display after restart
ssh nomadpi 'DISPLAY=:0 xrandr --listmonitors'
```

**Prevention:** The startx-single wrapper automatically prevents this issue.

### Taking Screenshots Remotely
**Tool:** `scrot` (installed as of 2026-02-15)

```bash
# Take screenshot
ssh nomadpi 'DISPLAY=:0 scrot /tmp/screenshot.png'

# Copy to local machine
scp nomadpi:/tmp/screenshot.png ~/Desktop/

# Take screenshot and copy in one command
ssh nomadpi 'DISPLAY=:0 scrot /tmp/screen.png' && scp nomadpi:/tmp/screen.png ~/Desktop/
```

### VNC Not Working
```bash
# Check VNC service status
ssh nomadpi 'systemctl status vncserver-x11-serviced'

# Check if X server is running
ssh nomadpi 'ps aux | grep X'

# Check VNC logs
ssh nomadpi 'journalctl -u vncserver-x11-serviced -f'

# Restart VNC service
ssh nomadpi 'systemctl restart vncserver-x11-serviced'
```

### Desktop Not Starting
```bash
# Check autostart mode
ssh nomadpi 'cat /boot/dietpi/.dietpi-autostart_index'

# Should be 2 for Desktop autologin
ssh nomadpi '/boot/dietpi/dietpi-autostart 2'

# Restart getty to apply changes
ssh nomadpi 'systemctl restart getty@tty1'
```

### Docker Containers Not Running
```bash
# Check Docker service
ssh nomadpi 'systemctl status docker'

# Check container status
ssh nomadpi 'docker ps -a'

# Restart containers
ssh nomadpi 'docker compose restart'

# View container logs
ssh nomadpi 'docker logs --tail 100 <container-name>'
```

**Note:** As of 2026-02-15, no Docker containers are running. Docker is available for future services.

### Network Issues
```bash
# Check network status
ssh nomadpi 'ip addr show'
ssh nomadpi 'ping -c 3 8.8.8.8'

# Restart NetworkManager
ssh nomadpi 'systemctl restart NetworkManager'

# Check WiFi status
ssh nomadpi 'iwconfig wlan0'
```


### SSH Connection Issues
```bash
# Check SSH service
ssh nomadpi 'systemctl status ssh'

# Check authorized keys
ssh nomadpi 'cat ~/.ssh/authorized_keys'

# View SSH logs
ssh nomadpi 'journalctl -u ssh -f'
```

### Bluetooth Not Working or Not Discoverable
```bash
# Check Bluetooth service status
ssh nomadpi 'systemctl status bluetooth'

# Check if controller is up
ssh nomadpi 'hciconfig -a'

# Verify configuration
ssh nomadpi 'bluetoothctl show'

# Make discoverable if needed
ssh nomadpi 'echo "discoverable on" | bluetoothctl'

# Restart Bluetooth service
ssh nomadpi 'systemctl restart bluetooth'

# Check Bluetooth logs
ssh nomadpi 'journalctl -u bluetooth -f'
```

**If pairing fails:**
- Ensure AlwaysPairable is enabled in `/etc/bluetooth/main.conf`
- Check that device is both discoverable and pairable
- Remove old pairing and try again: `echo "remove <MAC>" | bluetoothctl`

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

### Change VNC Password
```bash
ssh nomadpi
sudo vncpasswd
sudo systemctl restart vncserver-x11-serviced
```

### Add New SSH Key
```bash
# On your local machine
cat ~/.ssh/id_ed25519.pub

# On NomadPi
ssh nomadpi
echo "your-public-key-here" >> ~/.ssh/authorized_keys
```


### Change Screen Resolution
Edit `/boot/dietpi.txt`:
```bash
ssh nomadpi 'nano /boot/dietpi.txt'
# Modify:
SOFTWARE_VNCSERVER_WIDTH=1920
SOFTWARE_VNCSERVER_HEIGHT=1080
SOFTWARE_CHROMIUM_RES_X=1920
SOFTWARE_CHROMIUM_RES_Y=1080
```

### Access Logs
```bash
# System logs
ssh nomadpi 'journalctl -f'

# Docker container logs (if any containers running)
ssh nomadpi 'docker logs -f <container-name>'

# DietPi logs
ssh nomadpi 'ls /var/tmp/dietpi/logs/'
```

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

### 2026-02-16 - Directory Restructure
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

### 2026-02-15 - HDMI Audio Configuration
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

### 2026-02-15 - VLC Media Player Configuration
**Issue:** VLC launcher icon wasn't working when clicked from desktop.

**Root Cause:** VLC refuses to run as root user for security reasons. Desktop environment runs as root on NomadPi.

**Solution Implemented:**
1. Created wrapper script at `/usr/local/bin/vlc-root-wrapper` that runs VLC as `dietpi` user
2. Added `dietpi` user to `video`, `audio`, and `render` groups
3. Used `xhost +SI:localuser:dietpi` for X11 access (added to LXDE autostart)
4. Modified `/usr/share/applications/vlc.desktop` launcher to use wrapper
5. Wrapper uses `sg render` for GPU access and creates `/run/user/1000` for XDG runtime

**Result:** VLC now launches successfully from desktop icon with video (hardware-accelerated) and audio.

### 2026-02-15 - Device Repurposed for Nomad Karaoke
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
   - Cleaned up sticker printing and kiosk-specific content

5. **Retained Configuration**
   - All hardware specifications remain unchanged
   - Bluetooth, VNC, desktop environment configuration preserved
   - DietPi system configuration unchanged
   - /opt/nomad directory (19GB) untouched - contains NomadBranding and Tracks-PublicShare

**Current State:**
- Clean system with no running Docker containers
- Ready for Nomad Karaoke application installation
- All remote access methods working (Tailscale at 100.66.53.104, local at 192.168.1.84)

### 2026-02-15 - Auto-Deploy from GitHub
**Changes Made:**
1. **Created auto-deploy script** at `/opt/nomad/kjbox/kj-controller/auto-deploy.sh`
   - Polls `origin/main` every 60 seconds via `git fetch`
   - Compares local HEAD to remote; on difference: `git pull` + restart kj-controller
   - Auto-installs new pip dependencies if requirements.txt changes
2. **Created systemd service** `kj-autodeploy.service` (enabled, starts on boot)

**Workflow:** Edit code on Mac → `git push` → Pi auto-deploys within ~60 seconds

### 2026-02-15 - Multi-Folder Media Scanning & Descriptive Downloads
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

### 2026-02-15 - KJ Controller Deployed
**Changes Made:**
1. **Deployed KJ Controller** to `/opt/kj-controller/`
   - Simplified app.py: removed SocketIO/external screen sync (no longer needed)
   - Added audio device switching (HDMI ↔ USB mixer) via dropdown in web UI
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

### 2026-02-15 - Bluetooth Configuration
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

### 2026-02-15 - Display Management & Watchdog Fix
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

**Note:** This documentation was initially generated on 2026-02-15 and is updated as the system configuration changes.
