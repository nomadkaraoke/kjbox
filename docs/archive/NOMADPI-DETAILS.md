# NomadPi - Raspberry Pi Configuration Guide

**Last Updated:** 2026-02-17
**Purpose:** Nomad Karaoke live events - video playback and AV equipment connection
**Location:** Local network at 192.168.8.106 (Ethernet) / 192.168.1.84 (WiFi fallback)

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
- **Ethernet:** Onboard, enabled (MAC: e4:5f:01:b5:5d:c0)

## 🌐 Network Configuration

### Dual-Interface Setup

NomadPi uses both Ethernet and WiFi simultaneously, with **Ethernet preferred** (lower metric). Both interfaces use DHCP. This allows the Pi to work in different network environments without reconfiguration — plug in Ethernet at a venue and it becomes the primary connection; WiFi stays as a fallback.

| Interface | IP | Subnet | Gateway | Metric | Purpose |
|-----------|-----|--------|---------|--------|---------|
| eth0 (Ethernet) | 192.168.8.106 (DHCP, reserved) | 192.168.8.0/24 | 192.168.8.1 | 100 (preferred) | Primary — GL.inet karaoke router |
| wlan0 (WiFi) | Disabled | — | — | 200 (fallback) | Disabled as of 2026-02-17 (see below) |
| tailscale0 | 100.66.53.104 | Tailscale mesh | — | — | Remote access from anywhere |

**Hostname:** `nomadpi` / `NomadPi`
**DNS:** Provided via DHCP

### WiFi — Currently Disabled

As of 2026-02-17, WiFi is disabled to save power and avoid confusion about which interface is active. The Pi connects exclusively via Ethernet.

**What was done:**
- `nmcli radio wifi off` — disables WiFi radio via NetworkManager (persists across reboots)
- `nmcli connection modify "Moominvalley" connection.autoconnect no` — prevents auto-connecting to the saved WiFi network
- `wpa_supplicant` service disabled
- wlan0 commented out in `/etc/network/interfaces`

**Important:** NetworkManager controls wlan0, not ifupdown. Commenting out wlan0 in `/etc/network/interfaces` alone is not sufficient — NM will still bring it up. The `nmcli radio wifi off` command is what actually keeps WiFi disabled.

**To re-enable WiFi:**
```bash
ssh nomadpi 'nmcli radio wifi on && nmcli connection modify "Moominvalley" connection.autoconnect yes'
```
Also uncomment the wlan0 block in `/etc/network/interfaces` if ifupdown integration is needed.

The WiFi credentials (SSID: Moominvalley) are preserved in both NetworkManager's connection profile and `/etc/wpa_supplicant/wpa_supplicant.conf`.

### Network Configuration Files

| File | Purpose |
|------|---------|
| `/etc/network/interfaces` | Interface definitions, DHCP settings, metrics (primary config) |
| `/boot/dietpi.txt` | DietPi network flags (`ETHERNET_ENABLED`, `WIFI_ENABLED`, `USESTATIC`) |
| `/etc/wpa_supplicant/wpa_supplicant.conf` | WiFi SSID and credentials |
| `/boot/dietpi-wifi.txt` | DietPi WiFi setup (used at first boot) |
| `/var/lib/dhcp/dhclient.leases` | DHCP lease history |

### Current `/etc/network/interfaces`

```
# Ethernet (preferred - lower metric = higher priority)
allow-hotplug eth0
iface eth0 inet dhcp
metric 100

# WiFi (fallback - higher metric = lower priority)
allow-hotplug wlan0
iface wlan0 inet dhcp
metric 200
pre-up iw dev wlan0 set power_save off
post-down iw dev wlan0 set power_save on
wpa-conf /etc/wpa_supplicant/wpa_supplicant.conf
```

### DHCP Reservation

The GL.inet karaoke router has a DHCP reservation for the Pi's Ethernet MAC:
- **MAC:** `E4:5F:01:B5:5D:C0`
- **Reserved IP:** `192.168.8.106`
- Configure at: GL.inet admin panel → LAN → Static IP Address Binding

### Tailscale VPN
- **Enabled:** Yes
- **Tailscale IP:** 100.66.53.104
- **IPv6:** fd7a:115c:a1e0::d601:356d
- **Status:** Connected, managed by beveradb@github
- **Interface:** tailscale0

### mDNS / Avahi (`.local` hostname)

The Pi broadcasts `nomadpi.local` via mDNS (multicast DNS), allowing any device on the same LAN to reach it by hostname without any DNS server or configuration.

- **Package:** `avahi-daemon` + `libnss-mdns`
- **Hostname:** `nomadpi.local` (derived from `/etc/hostname`)
- **Config:** `/etc/avahi/avahi-daemon.conf` (restricted to `eth0` via `allow-interfaces=eth0`)
- **Service:** `avahi-daemon.service` (enabled, starts on boot)
- **Works on:** macOS (natively via Bonjour), Linux (with avahi/nss-mdns), Windows (with Bonjour Print Services or iTunes)
- **No internet required** — pure LAN multicast, works even if the router has no internet

**Usage:**
```bash
ssh root@nomadpi.local
curl http://nomadpi.local/status
# Browser: http://nomadpi.local
```

**Troubleshooting:**
```bash
# Check avahi is running
ssh nomadpi 'systemctl status avahi-daemon'

# Check what hostname is being advertised
ssh nomadpi 'avahi-browse -at | head -20'

# Restart avahi
ssh nomadpi 'systemctl restart avahi-daemon'
```

### SSH Access Methods

**Via Ethernet (preferred — GL.inet network):**
```bash
ssh nomadpi               # Using local .ssh/config (points to 192.168.8.106)
ssh root@192.168.8.106    # Direct IP
ssh root@nomadpi.local    # Via mDNS (works on any LAN, no config needed)
```

**Via WiFi (fallback — Ubiquiti network):**
```bash
ssh nomadpihomewifi           # Using local .ssh/config (points to 192.168.1.84)
ssh root@192.168.1.84     # Direct IP
```

**Via Tailscale (from anywhere):**
```bash
ssh root@100.66.53.104
ssh root@nomadpi           # Via Tailscale MagicDNS (if enabled)
```

### Networking Management Commands

```bash
# Check current IP addresses and interface status
ssh nomadpi 'ip -4 addr show eth0; ip -4 addr show wlan0'

# Check routing table (lower metric = preferred)
ssh nomadpi 'ip route show'

# Check which interface is handling default traffic
ssh nomadpi 'ip route get 8.8.8.8'

# Test internet connectivity
ssh nomadpi 'ping -c 3 8.8.8.8'

# View DHCP leases
ssh nomadpi 'cat /var/lib/dhcp/dhclient.leases'

# Renew DHCP lease on eth0 (e.g. after changing router)
ssh nomadpi 'dhclient -r eth0 && dhclient eth0'

# Renew DHCP lease on wlan0
ssh nomadpi 'dhclient -r wlan0 && dhclient wlan0'

# Restart all networking
ssh nomadpi 'systemctl restart networking'

# View current interfaces config
ssh nomadpi 'cat /etc/network/interfaces'

# View DietPi network settings
ssh nomadpi 'grep "NET_" /boot/dietpi.txt'
```

### Changing WiFi Network

```bash
# Edit WiFi credentials
ssh nomadpi 'wpa_passphrase "NewSSID" "password" > /etc/wpa_supplicant/wpa_supplicant.conf'
# Or edit manually:
ssh nomadpi 'nano /etc/wpa_supplicant/wpa_supplicant.conf'

# Restart WiFi
ssh nomadpi 'ifdown wlan0 && ifup wlan0'
```

### Switching to a Different Ethernet Network

The Pi uses DHCP on Ethernet, so simply plugging into a different router should work automatically. If the new router uses a different subnet, the Pi will get a new IP via DHCP. To find it:

```bash
# From a Mac on the same network:
nmap -sn 192.168.X.0/24  # Replace X with the new subnet

# Or check the router's DHCP client list for MAC E4:5F:01:B5:5D:C0
```

If the Pi doesn't get an IP on the new network, SSH via WiFi (if available) or Tailscale and run:
```bash
dhclient -r eth0 && dhclient eth0
```

### Setting a Static IP (if needed)

Edit `/etc/network/interfaces` and change the eth0 block:
```bash
ssh nomadpi 'cat > /etc/network/interfaces << "EOF"
source interfaces.d/*

# Ethernet (static)
allow-hotplug eth0
iface eth0 inet static
address 192.168.X.84
netmask 255.255.255.0
gateway 192.168.X.1
dns-nameservers 8.8.8.8 8.8.4.4
metric 100

# WiFi (fallback)
allow-hotplug wlan0
iface wlan0 inet dhcp
metric 200
pre-up iw dev wlan0 set power_save off
post-down iw dev wlan0 set power_save on
wpa-conf /etc/wpa_supplicant/wpa_supplicant.conf
EOF'
ssh nomadpi 'systemctl restart networking'
```

### Reverting to DHCP (from static)

```bash
ssh nomadpi 'cat > /etc/network/interfaces << "EOF"
source interfaces.d/*

# Ethernet (preferred - lower metric = higher priority)
allow-hotplug eth0
iface eth0 inet dhcp
metric 100

# WiFi (fallback - higher metric = lower priority)
allow-hotplug wlan0
iface wlan0 inet dhcp
metric 200
pre-up iw dev wlan0 set power_save off
post-down iw dev wlan0 set power_save on
wpa-conf /etc/wpa_supplicant/wpa_supplicant.conf
EOF'
ssh nomadpi 'systemctl restart networking'
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

- **Distribution:** DietPi v10.0.1 (Debian 13 Trixie) — upgraded from Debian 12 Bookworm on 2026-02-17
- **Kernel:** 6.12.62+rpt-rpi-v8 #1 SMP PREEMPT
- **Python:** 3.13.5 (system)
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
- **Display Manager:** LightDM (with root autologin) — changed from startx/xinit during Trixie upgrade
- **X Server:** Xorg
- **X Display:** :0 on vt7

### LightDM Configuration
File: `/etc/lightdm/lightdm.conf`
```
[Seat:*]
autologin-user=root
autologin-session=LXDE
user-session=LXDE
```

**PAM autologin:** Trixie's default `/etc/pam.d/lightdm-autologin` blocks root autologin. The line `auth required pam_succeed_if.so user != root quiet_success` must be commented out for root autologin to work.

### LXDE Desktop Components
**Autostart:** `/etc/xdg/lxsession/LXDE/autostart`
```
@lxpanel --profile LXDE
@pcmanfm --desktop --profile LXDE
@xscreensaver -no-splash
@xhost +SI:localuser:dietpi
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
- **Address:** `192.168.8.106` (no port needed for Service Mode)
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
- **Connection:** `192.168.8.106` (port 5900)

#### Virtual Mode
- **Separate virtual desktop** - independent from HDMI
- **Service:** `vncserver.service` (DietPi wrapper)
- **Resolution:** 1024x600x16 (configured in `/boot/dietpi.txt`)
- **Connection:** `192.168.8.106:1` (port 5901)

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
- **avahi-daemon** + **libnss-mdns** - mDNS/DNS-SD stack, broadcasts `nomadpi.local` on LAN (installed 2026-02-17)
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
- Remote control interface at `http://192.168.8.106/` (accessible from any browser on the local network)
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
  "flask_port": 80
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
- Flask app on port 80 (threaded mode)
- Karaoke VLC on port 8080 (HTTP control interface, fullscreen)
- Filler VLC on port 8081 (HTTP control interface, looping)
- Both VLC instances use `--aout alsa --alsa-audio-device <device>` for audio routing
- Audio device switching restarts both VLC instances (~5 seconds)
- Media index (`media_index.json`) caches file metadata; rebuilt on rescan or first startup
- Multi-folder scanning: walks all configured `media_folders` recursively
- Delete restricted to `download_folder` only (prevents deleting shared media)

See [kj-controller/](kj-controller/) for source code

### Rotation Display
A conky-based full-screen overlay that fetches the singer rotation from a public Google Sheet and displays the next 10 singers on the left side of the screen. Uses `desktop/rotation_data.py` (stdlib only, no pip deps) as the data source, called by conky via `${execpi}` (parsed exec — output is interpreted as conky markup). Designed for venue visibility during live karaoke events.

**How it works:** The conky window covers the entire 1920x1080 screen. A cropped version of the desktop wallpaper (`rotation-bg.png`) is drawn as the background image, creating faux transparency — the overlay blends seamlessly with the desktop. Text is positioned in the left ~600px via `${goto}` commands. The data script is called every 30 seconds to refresh.

**Previously:** Built with Python/tkinter, which cannot render transparent backgrounds on X11 (the `-alpha` attribute makes text transparent too). Replaced with conky on 2026-02-16.

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

**Display features:**
- **Header:** "ROTATION" title with stats line: `Started: M/D HH:MM  N singers | N sung | N queued`
- **Queue entries:** Up to 10 rows, each showing `N. Singer Name` + song line below
- **Color-coded badges:**
  - **NOW** (dark green) — currently singing (first non-done entry, or explicit "Now Singing" status)
  - **NEXT** (dark orange) — up next (explicit "Up Next" status)
  - **WIP** (red) — song is being generated (explicit "Being Made" status)
- **Singer names** in gold (`#ffdf6b`) for readability against the dark wallpaper
- **Song text** in light gray (`#e0e6f0`)
- **Numbers** in white

**Configuration files:**
- `desktop/rotation.conkyrc` — conky window and layout (full-screen 1920x1080, font defaults, refresh interval, background image path)
- `desktop/rotation_data.py` — data fetching, filtering, and conky markup formatting
- `desktop/rotation-bg.png` — 1920x1080 background image (generated from `nomad-kjbox-desktop-background-4k.jpg`)

**Key tunables in `rotation_data.py`:**
- `SHEET_ID` / `SHEET_GID` — Google Sheet ID and tab index
- `COL_SINGER`, `COL_SONG_ARTIST`, `COL_STATUS` — column indices (0-indexed)
- `MAX_ENTRIES` — number of queue entries to display (default: `10`)
- `FETCH_TIMEOUT` — HTTP timeout in seconds (default: `10`)
- `COLOR_*` — hex colors for names, badges, text (6 constants)
- `MARGIN` / `SONG_INDENT` — horizontal positioning via `${goto}` (default: `90` / `115`)
- `FONT_NAME` / `FONT_SONG` / `FONT_BADGE` — font family and size strings

**Key tunables in `rotation.conkyrc`:**
- `update_interval` — refresh cycle in seconds (default: `30`)
- `gap_x` / `gap_y` — window position (default: `0` / `0` for full-screen)
- `minimum_width` / `maximum_width` — window dimensions (default: `1920`)
- `minimum_height` — window height (default: `1080`)

**CLI usage (for debugging):**
```bash
# Full conky-formatted rotation (shows markup tags)
ssh nomadpi 'python3 /opt/nomad/kjbox/desktop/rotation_data.py'

# Header stats only
ssh nomadpi 'python3 /opt/nomad/kjbox/desktop/rotation_data.py --stats'
# Output: "Started: 2/12 21:25    26 singers | 57 sung | 10 queued"
```

**Updating the background image:**
When the desktop wallpaper (`desktop/nomad-kjbox-desktop-background-4k.jpg`) changes, `rotation-bg.png` must be regenerated:
```python
# Run locally (requires Pillow)
from PIL import Image
img = Image.open('desktop/nomad-kjbox-desktop-background-4k.jpg')
img.resize((1920, 1080), Image.LANCZOS).save('desktop/rotation-bg.png', 'PNG')
```
Commit both files and deploy. The conky overlay will pick up the new background on next service restart.

**Setup on a new device:**
```bash
# 1. Install conky
apt-get install -y conky-all

# 2. Create systemd service
cat > /etc/systemd/system/rotation-display.service << 'EOF'
[Unit]
Description=Karaoke Rotation Display Overlay
After=graphical.target

[Service]
Type=simple
Environment=DISPLAY=:0
ExecStartPre=/bin/bash -c "xhost +SI:localuser:root"
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
- Expected columns: `Timestamp` (col 0), `Singer` (col 1), `Song & Artist` (col 2), `Status` (col 3)
- Recognized statuses: `Done`, `Now Singing`, `Up Next`, `Being Made (!)`, `Waiting`
- Rows with Status = "Done" are filtered out of the queue (but counted in "sung" stat)
- First non-done entry automatically gets the "NOW" badge
- Earliest timestamp in the sheet is shown as "Started: M/D HH:MM"

**Troubleshooting:**
- **Overlay not visible:** Check `systemctl status rotation-display`. If conky is running but invisible, the window type may be wrong — must be `own_window_type = 'dock'` (not `'override'`) because PCManFM's desktop window in LXDE sits above override-type windows.
- **Text shows raw `${color}` / `${font}` tags:** The conkyrc must use `${execpi}` (not `${execi}`) — the "p" means "parse output for conky variables."
- **Background misaligned:** Ensure `rotation-bg.png` is exactly 1920x1080 and was regenerated from the current wallpaper. The conky window must be full-screen (`gap_x=0, gap_y=0, 1920x1080`).
- **No data / "Offline":** Check network connectivity on the Pi. The script fetches from `docs.google.com` — requires internet access. Verify the Sheet is published to web.
- **Font rendering issues:** The Pi uses `DejaVu Sans` (not Helvetica, which is not installed). Check available fonts with `fc-list | grep -i dejavu`.
- **Stale data after deploy:** The auto-deploy script restarts `rotation-display` on new commits, but the `${execpi 30}` cache means data may take up to 30 seconds to refresh after restart.

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
avahi-daemon.service            - mDNS/DNS-SD (broadcasts nomadpi.local on LAN)
kj-autodeploy.service          - Auto-deploy kj-controller from GitHub (polls every 60s)
kj-controller.service          - KJ Controller (karaoke show management, port 80)
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
- `/etc/lightdm/lightdm.conf` - LightDM config (autologin-user=root, session=LXDE)
- `/etc/pam.d/lightdm-autologin` - PAM autologin policy (root autologin requires commenting out user!=root line)
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
- `/opt/nomad/kjbox/desktop/rotation.conkyrc` - Conky configuration (full-screen layout, background image, refresh interval)
- `/opt/nomad/kjbox/desktop/rotation_data.py` - Data fetcher script (Google Sheet → conky markup, --stats flag)
- `/opt/nomad/kjbox/desktop/rotation-bg.png` - 1920x1080 background image (faux transparency)
- `/opt/nomad/kjbox/desktop/nomad-kjbox-desktop-background-4k.jpg` - 4K source wallpaper (rotation-bg.png is generated from this)
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
