# Mini PC Setup Guide

Step-by-step guide to set up the Nomad Karaoke mini PC to run the same karaoke software stack as NomadPi. This guide assumes you're working from a Mac with SSH access to the mini PC.

**Target state:** The mini PC boots straight into a desktop with KJ Controller and the rotation display running automatically — identical behavior to the Pi.

## Device Specs (Verified 2026-02-18)

| Spec | Value |
|------|-------|
| **CPU** | Intel N97 (4 cores, up to 3.6GHz, x86_64) |
| **RAM** | 16GB |
| **Storage** | 476GB NVMe SSD (ext4), 411GB free |
| **GPU** | Intel Alder Lake-N UHD Graphics |
| **OS** | Linux Mint 22.1 (Xia), based on Ubuntu 24.04 Noble |
| **Kernel** | 6.8.0-71-generic |
| **Desktop** | XFCE (via LightDM) |
| **Audio** | HDA Intel PCH via PipeWire (HDMI stereo output) |
| **Hostname** | `nomadpc` |
| **User** | `nomad` (UID 1000, shell: zsh, autologin enabled) |
| **Display outputs** | HDMI-1, HDMI-2, DP-1, DP-2 |
| **Ethernet** | `enp2s0` (MAC: `84:47:09:5a:1d:13`) |
| **WiFi** | `wlp1s0` (MAC: `9c:12:21:3f:39:43`) |

### Pre-Installed Software

| Package | Version | Notes |
|---------|---------|-------|
| Python | 3.12.3 | System python |
| VLC | 3.0.20 | Already installed |
| conky | 1.19.6 | Already installed |
| yt-dlp | 2025.07.21 | Already installed |
| git | 2.43.0 | Already installed |
| avahi-daemon | — | Already running, broadcasting `nomadpc.local` |
| cloudflared | — | Already running, tunnel to `kjbox.nomadkaraoke.com` (web), `kjssh.nomadkaraoke.com` (SSH) |
| Tailscale | — | Already installed, IP `100.82.90.111` |
| PipeWire | 1.0.5 | Audio server (replaces PulseAudio/raw ALSA) |

### Not Installed (Need to Set Up)

- Docker (not needed unless future services require it)
- KJ Controller app stack (the repo clone, venv, services)

---

## Phase 1: OS & Base System

### 1.1 Review Current OS (DONE)

The mini PC runs Linux Mint 22.1 (Xia) with XFCE desktop. No reinstall needed.

```bash
ssh nomadlocalkinodirect  # 192.168.8.170 via GL.iNet router
```

### 1.2 Update to Latest

```bash
sudo apt update && sudo apt upgrade -y
sudo reboot
```

### 1.3 Set Hostname

Currently `nomad-karaoke`, needs to be changed to `nomadpc`:

```bash
sudo hostnamectl set-hostname nomadpc
sudo sed -i 's/127\.0\.1\.1.*/127.0.1.1\tnomadpc/' /etc/hosts
sudo systemctl restart avahi-daemon
# Avahi will now broadcast nomadpc.local
```

### 1.4 User (DONE)

User `nomad` (UID 1000) exists with autologin enabled. KJ Controller runs as this user — no root wrapper needed (unlike the Pi).

### 1.5 SSH Key Access (DONE)

SSH key `andrew@beveridge.uk` is already authorized in `/home/nomad/.ssh/authorized_keys`.

Password authentication is still enabled (the `#PasswordAuthentication yes` line is commented out, meaning default=yes). To harden:
```bash
ssh nomadlocalkinodirect 'sudo sed -i "s/#PasswordAuthentication yes/PasswordAuthentication no/" /etc/ssh/sshd_config && sudo systemctl restart ssh'
```

### 1.6 Disable Sleep/Screensaver/Power Management

**NOT YET DONE.** Currently: screensaver lock is ON, idle timeout is 900s (15min).

```bash
# XFCE power management — disable all sleep/blank
xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-enabled -s false
xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/blank-on-ac -s 0
xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-on-ac-sleep -s 0
xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-on-ac-off -s 0

# XFCE screensaver — disable lock
xfconf-query -c xfce4-screensaver -p /lock/enabled -s false
xfconf-query -c xfce4-screensaver -p /saver/enabled -s false

# Disable systemd sleep targets
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

# Disable DPMS at X11 level — add to autostart
echo 'xset s off -dpms' >> ~/.xprofile
```

### 1.7 Graceful Power Loss Handling (CONFIGURED)

The mini PC gets unplugged at the end of gigs without a clean shutdown. All five layers of power-loss hardening are applied:

**1. BIOS: Auto-power-on after power loss**
```bash
# BIOS setting (access during boot, usually DEL or F2):
# Advanced > Power Management > AC Power Loss > "Power On"
```

**2. Filesystems: ext4 with journaling**
```bash
# Root (NVMe)
mount | grep ' / '
# /dev/nvme0n1p2 on / type ext4 (rw,relatime,errors=remount-ro)

# SSD (USB) — reformatted from exFAT to ext4 (2026-02-24)
mount | grep Nomad4TBOne
# /dev/sda1 on /media/nomad/Nomad4TBOne type ext4 (rw,noatime,stripe=512)
```
The SSD uses `noatime` to reduce unnecessary metadata writes.

**3. Kernel: Auto-reboot on panic**
```bash
# /etc/sysctl.d/99-power-loss.conf
kernel.panic = 10
# Reboots automatically 10s after kernel panic (default 0 = hang forever)
```

**4. Journal size cap**
```bash
# /etc/systemd/journald.conf.d/size-limit.conf
[Journal]
SystemMaxUse=200M
# Caps journal at 200M — faster flush after unclean shutdown
```

**5. Atomic writes in application code**

Config and media index files use atomic writes (temp file + fsync + rename) so power loss mid-write cannot corrupt them. See `config.py:save_config_value()` and `media.py:MediaIndex.save()`.

### 1.8 Auto-Login to Desktop (DONE)

Already configured in `/etc/lightdm/lightdm.conf`:
```
autologin-guest=false
autologin-user=nomad
autologin-user-timeout=0
```

Desktop session is XFCE (`/usr/share/xsessions/xfce.desktop`).

---

## Phase 2: Network

### 2.1 Current Network State

The mini PC has both Ethernet and WiFi connected:

| Interface | IP | Subnet | Gateway | Metric | Purpose |
|-----------|-----|--------|---------|--------|---------|
| `enp2s0` (Ethernet) | `192.168.8.170` (DHCP) | 192.168.8.0/24 | 192.168.8.1 | 100 (preferred) | GL.iNet karaoke router |
| `wlp1s0` (WiFi) | `192.168.1.87` (DHCP) | 192.168.1.0/24 | 192.168.1.1 | 600 (fallback) | Home WiFi |

Ethernet already has the lower metric and carries the default route. Good.

### 2.2 Disable WiFi (at venue)

WiFi is useful at home for fallback access. At the venue, disable it:

```bash
# Disable WiFi radio (persists across reboots)
nmcli radio wifi off

# Verify
nmcli general status
# Should show wifi: disabled

# To re-enable:
nmcli radio wifi on
```

### 2.3 Set DHCP Reservation on GL.iNet Router

1. Log into GL.iNet admin panel (http://192.168.8.1)
2. Go to LAN → Static IP Address Binding
3. Bind MAC `84:47:09:5a:1d:13` to a fixed IP (e.g., `192.168.8.170`)

### 2.4 Avahi / mDNS (DONE)

Already installed and running. Broadcasting `nomadpc.local`.

**Recommended:** Restrict Avahi to the ethernet interface only:
```bash
# Edit /etc/avahi/avahi-daemon.conf, add under [server]:
# allow-interfaces=enp2s0

sudo systemctl restart avahi-daemon
```

Verify from Mac:
```bash
ping nomadpc.local
```

### 2.5 Install Tailscale (DONE)

Tailscale is installed and running. NomadPC's Tailscale IP: **`100.82.90.111`**

```bash
# If reinstalling:
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# Follow the auth URL to add to your tailnet
```

### 2.6 Cloudflare Tunnel (CONFIGURED)

A Cloudflare tunnel exposes the KJ Controller web UI, VNC websocket, and SSH remotely:
- **Tunnel:** `1e86a7f5-04e7-4527-b624-49447450443e` (name: `kjbox`)
- **Config:** `/etc/cloudflared/config.yml`
- **Credentials:** `/etc/cloudflared/1e86a7f5-04e7-4527-b624-49447450443e.json`

**Hostnames:**
| Hostname | Service | Purpose |
|----------|---------|---------|
| `kjbox.nomadkaraoke.com` | `https://localhost:443` | KJ Controller web UI |
| `kjvnc.nomadkaraoke.com` | `http://localhost:6080` | Websockify (VNC preview WebSocket) |
| `kjssh.nomadkaraoke.com` | `ssh://localhost:22` | SSH remote access |

**Current config (`/etc/cloudflared/config.yml`):**
```yaml
tunnel: 1e86a7f5-04e7-4527-b624-49447450443e
credentials-file: /etc/cloudflared/1e86a7f5-04e7-4527-b624-49447450443e.json

ingress:
  - hostname: kjssh.nomadkaraoke.com
    service: ssh://localhost:22
  - hostname: kjvnc.nomadkaraoke.com
    service: http://localhost:6080
  - hostname: kjbox.nomadkaraoke.com
    service: https://localhost:443
    originRequest:
      noTLSVerify: true
  - service: http_status:404
```

**Notes:**
- `noTLSVerify: true` is needed because the origin uses a mkcert certificate (not publicly trusted)
- The websockify hostname (`kjvnc`) is on a separate ingress because Cloudflare tunnels don't support path-based routing on the same hostname
- DNS CNAMEs created with: `sudo cloudflared tunnel route dns <tunnel-id> <hostname>`

### 2.7 Cloudflare Access (Zero Trust Auth)

Cloudflare Access protects the tunnel endpoints so only authorized users can access the controller remotely. This is configured in the Cloudflare Zero Trust dashboard (not on the device).

**Setup:** https://one.dash.cloudflare.com/ → Access → Applications
- **Application:** Self-hosted, covers both `kjbox.nomadkaraoke.com` and `kjvnc.nomadkaraoke.com`
- **Auth method:** Email OTP — user enters email, receives one-time code
- **Policy:** Allow list of authorized email addresses
- **Session duration:** 24 hours

When someone visits `https://kjbox.nomadkaraoke.com`, Cloudflare shows a login page before any traffic reaches the device.

### 2.8 Update SSH Config on Mac

Add to `~/.ssh/config` (all three entries for LAN, Tailscale, and Cloudflare tunnel):
```
# LAN (mDNS — works when on same network)
Host nomadpc
    HostName nomadpc.local
    User nomad
    Port 22

# Tailscale (works from anywhere when Mac Tailscale is running)
Host nomadpcts
    HostName 100.82.90.111
    User nomad
    Port 22

# Cloudflare tunnel (works from anywhere, no Tailscale needed)
# Requires: brew install cloudflare/cloudflare/cloudflared
Host nomadpctunnel
    HostName kjssh.nomadkaraoke.com
    User nomad
    Port 22
    ProxyCommand cloudflared access ssh --hostname %h
```

**Which to use:**
- On the same LAN: `ssh nomadpc`
- Remote with Tailscale running: `ssh nomadpcts` (start Tailscale app on Mac first)
- Remote without Tailscale: `ssh nomadpctunnel` (opens browser auth on first use)

---

## Phase 3: Software Stack

### 3.1 Install System Dependencies

Most are already installed. Install any missing ones:

```bash
sudo apt install -y \
    python3-venv \
    python3-pip \
    fonts-dejavu \
    curl
```

**Already present:** git, vlc, conky-all, yt-dlp, avahi-daemon.

### 3.2 Clone the Repository

```bash
sudo mkdir -p /opt/nomad
sudo chown nomad:nomad /opt/nomad
git clone https://github.com/nomadkaraoke/kjbox.git /opt/nomad/kjbox
```

### 3.3 Set Up KJ Controller

```bash
cd /opt/nomad/kjbox/kj-controller
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3.4 Create config.json

```bash
cat > /opt/nomad/kjbox/kj-controller/config.json << 'EOF'
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
  "flask_port": 80,
  "enable_vlc": true,
  "audio_devices": {
    "hdmiout": "HDMI Output (TV)"
  },
  "default_audio_device": "hdmiout",
  "tls_cert": "/opt/nomad/kjbox/kj-controller/certs/cert.pem",
  "tls_key": "/opt/nomad/kjbox/kj-controller/certs/key.pem",
  "external_file_list": "/media/nomad/Nomad4TBOne/HyperMule/all-karaoke-files-2025.02.28.txt",
  "external_media_mount": "/media/nomad/Nomad4TBOne",
  "websockify_host": "kjvnc.nomadkaraoke.com"
}
EOF
```

**Config notes:**
- `media_folders` — scanned into a local JSON index (`media_index.json`). Good for small collections like YTDownloads. **Do NOT add large external drives here** — use the external catalog instead.
- `external_file_list` / `external_media_mount` — used by the SQLite FTS5 external catalog system for large collections like HyperMule (~415K files). See [Phase 6: USB External Drive](#phase-6-usb-external-drive) for details.
- `tls_cert` / `tls_key` — when present, Flask serves HTTPS on port 443 instead of HTTP on port 80. See [Phase 5.6](#56-tls-certificates-configured).
- `websockify_host` — tunnel hostname for VNC WebSocket. When accessing via tunnel (e.g., `kjbox.nomadkaraoke.com`), the noVNC client connects to this hostname. When accessing locally (`.local`, `localhost`, or IP), it connects directly to `hostname:6080`. Leave empty if not using a tunnel.

**Audio note:** This mini PC has PipeWire installed, but VLC bypasses it and uses ALSA directly for HDMI audio. PipeWire's HDMI routing doesn't reliably produce sound on this hardware. The `hdmiout` ALSA device (defined in `/etc/asound.conf`) maps directly to `hw:0,7`. See [AUDIO.md](AUDIO.md) for the full NomadPC audio setup and troubleshooting.

### 3.5 Create Data Directories

```bash
mkdir -p /opt/nomad/YTDownloads
mkdir -p /opt/nomad/FillerMusic
# If you have existing media, copy/mount it:
# mkdir -p /opt/nomad/Tracks-PublicShare
```

### 3.6 Platform Detection (DONE — in repo)

The app uses `is_pi()` to decide whether to enable VLC. On the mini PC, this returns `False` because there's no `/boot/dietpi.txt`. The `enable_vlc` config flag was added to support non-Pi devices.

Add `"enable_vlc": true` to `config.json` (already included in 3.4 above).

The code changes are already committed to the repo:
- **`vlc.py`**: `self.enabled` checks `config.get('enable_vlc', False)` alongside `is_pi()`
- **`app.py`**: Platform setup restructured — Pi-specific setup (xhost, dietpi user) is separate from shared device setup (websockify). Websockify starts on any device with `enable_vlc: true`.

---

## Phase 4: Systemd Services

### 4.1 KJ Controller Service

```bash
sudo tee /etc/systemd/system/kj-controller.service << 'EOF'
[Unit]
Description=KJ Controller - Karaoke Show Management
After=network.target graphical.target
Wants=graphical.target

[Service]
Type=simple
User=nomad
WorkingDirectory=/opt/nomad/kjbox/kj-controller
Environment=DISPLAY=:0
Environment=HOME=/home/nomad
Environment=XDG_RUNTIME_DIR=/run/user/1000
# Port 80 requires this capability (or run as root)
AmbientCapabilities=CAP_NET_BIND_SERVICE
ExecStart=/opt/nomad/kjbox/kj-controller/venv/bin/python /opt/nomad/kjbox/kj-controller/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
EOF
```

**Note:** `XDG_RUNTIME_DIR` is needed for PipeWire audio access (PipeWire runs as the `nomad` user and its socket is at `/run/user/1000/pipewire-0`). If `CAP_NET_BIND_SERVICE` doesn't work for port 80, use port 8080 instead.

### 4.2 Auto-Deploy Service

```bash
sudo tee /etc/systemd/system/kj-autodeploy.service << 'EOF'
[Unit]
Description=KJ Controller Auto-Deploy from GitHub
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=nomad
ExecStart=/opt/nomad/kjbox/kj-controller/auto-deploy.sh
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Make sure auto-deploy.sh is executable
chmod +x /opt/nomad/kjbox/kj-controller/auto-deploy.sh
```

### 4.3 Rotation Display Service

```bash
sudo tee /etc/systemd/system/rotation-display.service << 'EOF'
[Unit]
Description=Karaoke Rotation Display Overlay
After=graphical.target

[Service]
Type=simple
User=nomad
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/nomad/.Xauthority
ExecStart=/usr/bin/conky -c /opt/nomad/kjbox/desktop/rotation.conkyrc
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
EOF
```

**Note:** XFCE uses `own_window_type = 'desktop'` or `'dock'` for conky. The LXDE-specific `'dock'` workaround for PCManFM may not be needed — test and adjust `rotation.conkyrc` if the overlay doesn't appear correctly.

### 4.4 Overlay Display Service

```bash
sudo tee /etc/systemd/system/overlay-display.service << 'EOF'
[Unit]
Description=Karaoke Overlay Display Engine
After=graphical.target

[Service]
Type=simple
User=nomad
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/nomad/.Xauthority
ExecStart=/opt/nomad/kjbox/kj-controller/venv/bin/python /opt/nomad/kjbox/desktop/overlay_engine.py
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
EOF
```

### 4.5 Enable All Services

```bash
sudo systemctl daemon-reload
sudo systemctl enable kj-controller kj-autodeploy rotation-display overlay-display
sudo systemctl start kj-controller kj-autodeploy rotation-display overlay-display
```

---

## Phase 5: Display & Audio

### 5.1 HDMI Audio (VERIFIED WORKING)

Audio uses direct ALSA to HDMI, bypassing PipeWire. PipeWire's HDMI routing does not reliably produce sound on this hardware (audio flows through PipeWire's pipeline but nothing comes out of the TV).

**Step 1: Create `/etc/asound.conf`:**
```bash
sudo tee /etc/asound.conf << 'EOF'
# HDMI audio output via Intel HDA HDMI 1 (connected to TV)
pcm.hdmiout {
    type plug
    slave {
        pcm "hw:0,7"
    }
}

ctl.hdmiout {
    type hw
    card 0
}
EOF
```

**Step 2: Keep PipeWire on analog profile** (so it doesn't lock the HDMI device):
```bash
sudo -u nomad XDG_RUNTIME_DIR=/run/user/1000 pactl set-card-profile alsa_card.pci-0000_00_1f.3 "output:analog-stereo+input:analog-stereo"
```

**Step 3: Test audio:**
```bash
speaker-test -D hdmiout -c 2 -t sine -f 440 -l 1
```

**VLC audio:** VLC uses `--aout alsa --alsa-audio-device hdmiout` (configured via `default_audio_device` in config.json). See [AUDIO.md](AUDIO.md) for full details and troubleshooting.

### 5.2 Display (VERIFIED)

Currently connected: HDMI-1 at 1920x1080@60Hz.

Available outputs: HDMI-1, HDMI-2, DP-1, DP-2.

```bash
DISPLAY=:0 xrandr
# HDMI-1 connected 1920x1080+0+0
# HDMI-2 disconnected
# DP-1 disconnected
# DP-2 disconnected
```

For multi-display (e.g., HDMI splitter or separate outputs):
```bash
# Mirror HDMI-1 to HDMI-2
xrandr --output HDMI-2 --same-as HDMI-1 --auto

# Or extend desktop
xrandr --output HDMI-2 --right-of HDMI-1 --auto
```

### 5.3 Rotation Display Background

The conky overlay uses a 1920x1080 background image for faux transparency:

```bash
# Set the XFCE desktop wallpaper to the Nomad background
xfconf-query -c xfce4-desktop -p /backdrop/screen0/monitorHDMI-1/workspace0/last-image \
  -s /opt/nomad/kjbox/desktop/nomad-kjbox-desktop-background-4k.jpg

# Regenerate rotation-bg.png if needed (requires Pillow)
cd /opt/nomad/kjbox/desktop
python3 -c "
from PIL import Image
img = Image.open('nomad-kjbox-desktop-background-4k.jpg')
img.resize((1920, 1080), Image.LANCZOS).save('rotation-bg.png', 'PNG')
"
```

### 5.4 Conky Font Check

```bash
# Verify DejaVu Sans is available (used by rotation display)
fc-list | grep -i "dejavu sans"
# If not installed:
sudo apt install -y fonts-dejavu
```

### 5.5 VNC Screen Preview

For the browser-based VNC preview in KJ Controller, you'll need a VNC server:

```bash
# Install x11vnc (lightweight, shares physical display like RealVNC on the Pi)
sudo apt install -y x11vnc

# Set a VNC password
x11vnc -storepasswd

# Create a systemd service
sudo tee /etc/systemd/system/x11vnc.service << 'EOF'
[Unit]
Description=x11vnc VNC Server
After=display-manager.service

[Service]
Type=simple
User=nomad
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/nomad/.Xauthority
ExecStart=/usr/bin/x11vnc -display :0 -auth /home/nomad/.Xauthority -forever -shared -rfbport 5900 -rfbauth /home/nomad/.vnc/passwd
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now x11vnc
```

**Important flags:**
- `-shared` — allows multiple simultaneous VNC connections. Without this, a new connection kicks the existing one.
- `After=display-manager.service` + `WantedBy=multi-user.target` — avoids ordering cycle with `graphical.target` during shutdown.

**Note:** The Pi uses RealVNC (proprietary, RA2ne auth). The mini PC uses x11vnc (open source, standard VNC auth). The websockify + noVNC browser preview should work with either — the key difference is the auth type. x11vnc uses standard VNC password auth which noVNC handles natively (no `serververification` workaround needed).

### 5.6 TLS Certificates (CONFIGURED)

TLS is required for HTTPS (the VNC preview uses `crypto.subtle` which needs a secure context).

```bash
# On Mac (one-time CA install):
brew install mkcert && mkcert -install

# Generate cert for all access methods:
mkcert nomadpc.local nomadpc 192.168.8.170 localhost 127.0.0.1

# Deploy to device:
ssh nomadpc 'mkdir -p /opt/nomad/kjbox/kj-controller/certs'
scp nomadpc.local+4.pem nomadpc:/opt/nomad/kjbox/kj-controller/certs/cert.pem
scp nomadpc.local+4-key.pem nomadpc:/opt/nomad/kjbox/kj-controller/certs/key.pem
```

Add to `config.json`:
```json
{
  "tls_cert": "/opt/nomad/kjbox/kj-controller/certs/cert.pem",
  "tls_key": "/opt/nomad/kjbox/kj-controller/certs/key.pem"
}
```

When TLS certs are present, Flask auto-switches from port 80 to **port 443** (HTTPS). Websockify also uses the certs for WSS on port 6080.

**Trusting the cert on other devices:**
- **Mac:** `mkcert -install` (adds CA to macOS keychain, one-time)
- **Android:** Copy `$(mkcert -CAROOT)/rootCA.pem` to the phone, install via Settings → Security → Install CA certificate
- **Via tunnel:** Not needed — `kjbox.nomadkaraoke.com` uses Cloudflare's own trusted cert

---

## Phase 6: USB External Drive

### 6.1 Mount the Drive

The 4TB SanDisk Extreme Pro SSD contains the full karaoke catalog (HyperMule). It uses **ext4** (reformatted from exFAT on 2026-02-24 for power-loss safety — exFAT has no journal).

```bash
# Check the drive is detected
lsblk -o NAME,SIZE,FSTYPE,LABEL
# Should show: sda1  3.6T  ext4  Nomad4TBOne

# Create mount point and mount
sudo mkdir -p /media/nomad/Nomad4TBOne
sudo mount /dev/sda1 /media/nomad/Nomad4TBOne
sudo chown nomad:nomad /media/nomad/Nomad4TBOne

# Verify
ls /media/nomad/Nomad4TBOne/HyperMule/
```

### 6.2 Persist in fstab

Add an fstab entry so the drive auto-mounts on boot:

```bash
# Get the UUID
sudo blkid /dev/sda1
# UUID="b5ec3a27-4477-467e-a002-fd7ab8b3b755" (will vary per drive)

# Add to fstab — nofail prevents boot hang if drive is unplugged, noatime reduces metadata writes
echo 'UUID=b5ec3a27-4477-467e-a002-fd7ab8b3b755 /media/nomad/Nomad4TBOne ext4 defaults,noatime,nofail 0 2' | sudo tee -a /etc/fstab
```

**Notes:**
- `nofail` — system boots normally even if the drive isn't plugged in
- `noatime` — skips access-time metadata updates on reads (reduces writes, extends SSD life)
- `0 2` — enables fsck on boot (after root filesystem)
- Replace the UUID with the actual value from `blkid`

### 6.3 Filler Music

Copy filler music tracks to `/opt/nomad/FillerMusic/`. These play between karaoke songs.

```bash
# Example: copy from legacy KJ software data folder
cp /home/nomad/kjdata/filler/*.mp3 /opt/nomad/FillerMusic/
# Or from an external source
```

### 6.4 Migrate Legacy YouTube Downloads

If the device has videos from a previous KJ software (e.g., in `/home/nomad/kjdata/videos/`), they need to be renamed to match our naming convention: `{youtube_id}__{channel}__{title}.mp4`.

The legacy format uses random 8-char IDs with JSON sidecar files containing metadata:
```
# Legacy: 03rwuq20.mp4 + 03rwuq20.json
# JSON:   {"id": "03rwuq20", "title": "...", "original_url": "https://youtube.com/watch?v=..."}
#
# New:    gkBGrVCd4uc__Unknown__Megan Moroney - 6 Months Later (Karaoke Version).mp4
```

A migration script extracts the YouTube ID from `original_url` and the title from each JSON file. The channel is set to "Unknown" since the legacy format doesn't store it. The script is idempotent (skips files that already exist in the destination).

See `scripts/migrate_legacy_videos.py` in this repo (or write one using the pattern in `kj-controller/utils.py:sanitize_filename_part`).

### 6.5 Build External Catalog (HyperMule)

The KJ Controller has **two separate indexing systems** — understanding this is critical:

| System | Storage | Scan Method | Best For |
|--------|---------|-------------|----------|
| **Local Media Index** | JSON file (`media_index.json`) | Directory walk via `POST /rescan` | Small collections (YTDownloads, <10K files) |
| **External Catalog** | SQLite + FTS5 (`external_media.db`) | Manifest file via `POST /catalog/build` | Large catalogs (HyperMule, 400K+ files) |

**Do NOT add large external drives to `media_folders`** — the directory walk is slow and the JSON index will be huge. Use the external catalog instead.

The external catalog reads from a text manifest file (one file path per line). HyperMule ships with one:

```bash
ls /media/nomad/Nomad4TBOne/HyperMule/all-karaoke-files-2025.02.28.txt
# ~415K lines
```

The manifest was generated on macOS, so paths start with `/Volumes/Nomad4TBOne/`. The catalog builder auto-rewrites these to the Linux mount point using `external_media_mount` from config.json (`/Volumes/Nomad4TBOne/` → `/media/nomad/Nomad4TBOne/`).

**Build the catalog** (one-time, takes ~10 seconds for 415K entries):

```bash
# Restart controller to pick up config changes first
sudo systemctl restart kj-controller
sleep 5

# Trigger the build
curl -s -X POST http://localhost/catalog/build
# {"success": true, "count": 414933}

# Verify search works
curl -s 'http://localhost/search?q=bohemian+rhapsody' | python3 -m json.tool | head -20
```

The catalog is also rebuilt automatically by `auto-deploy.sh` after each code deploy (15-second delay).

**To regenerate the manifest** (if files change on the drive):
```bash
# From Mac (where the drive is /Volumes/Nomad4TBOne):
find /Volumes/Nomad4TBOne/HyperMule -type f > /Volumes/Nomad4TBOne/HyperMule/all-karaoke-files-$(date +%Y.%m.%d).txt

# Then rebuild the catalog on the device:
curl -s -X POST http://localhost/catalog/build
```

**How search works in the UI:**
- The search box searches both systems simultaneously
- Local results: filtered in-browser from the JSON index
- Catalog results: FTS5 full-text search with diacritics normalization (é→e, ø→o, etc.)
- Results shown in unified list with "Your Library" vs "Catalog" sections

---

## Phase 7: Verification

### 7.1 Service Status

```bash
systemctl status kj-controller
systemctl status kj-autodeploy
systemctl status rotation-display
systemctl status overlay-display
systemctl status avahi-daemon
systemctl status cloudflared
```

### 7.2 KJ Controller Web UI

From your Mac:
```bash
curl -sk https://nomadpc.local/status
# Should return JSON with player state

# Open in browser:
# https://nomadpc.local      (local network)
# https://kjbox.nomadkaraoke.com  (remote, via Cloudflare tunnel + Access)
```

### 7.3 VLC Playback

Test from the web UI — download a test video and play it. Verify:
- Video displays fullscreen on the HDMI output
- Audio comes through HDMI
- Filler music fades out before karaoke and fades back in after

### 7.4 Rotation Display

Check if the conky overlay is visible on the desktop. If not:
```bash
journalctl -u rotation-display --no-pager -l | tail -20
# Common issues:
# - Wrong DISPLAY variable
# - Font not found
# - Background image path incorrect
# - XFCE may need different own_window_type than LXDE
```

### 7.5 Auto-Deploy

```bash
journalctl -u kj-autodeploy --no-pager | tail -10
# Should show "Auto-deploy started (polling every 60s)"
```

### 7.6 mDNS

```bash
# From Mac:
ping nomadpc.local
ssh nomadpc  # via your .ssh/config
```

### 7.7 Reboot Test

The most important test — verify everything comes back after a cold boot:

```bash
sudo reboot
# Wait for it to come back up (15-30 seconds — SSD is much faster than Pi's SD card)
# Then verify all services are running:
ssh nomadpc 'systemctl is-active kj-controller kj-autodeploy rotation-display overlay-display avahi-daemon cloudflared'
curl -sk https://nomadpc.local/status
```

---

## Differences from Pi Setup

| Aspect | NomadPi (Raspberry Pi 4) | Mini PC (`nomadpc`) |
|--------|--------------------------|---------------------------|
| **CPU** | 4-core ARM (BCM2711) | Intel N97 (4-core x86_64, 3.6GHz) |
| **RAM** | 2GB | 16GB |
| **Storage** | 256GB SD card | 476GB NVMe SSD |
| **OS** | DietPi (Debian 13 Trixie) | Linux Mint 22.1 Xia (Ubuntu Noble) |
| **Desktop** | LXDE (via LightDM) | XFCE (via LightDM) |
| **User** | root (VLC wrapped as dietpi) | `nomad` (VLC runs directly) |
| **Audio** | Raw ALSA + custom EDID + iec958 plugin | Direct ALSA `hw:0,7` (bypasses PipeWire) |
| **VLC launch** | `sudo -u dietpi env DISPLAY=:0 cvlc` | `cvlc` directly |
| **VNC** | RealVNC (proprietary, RA2ne auth) | x11vnc (open source, standard VNC auth) |
| **Platform detection** | `is_pi()` = True | Needs `enable_vlc: true` in config |
| **Remote access** | Tailscale + Cloudflare tunnel | Tailscale + Cloudflare tunnel (`kjbox.nomadkaraoke.com`) |
| **Boot time** | ~45 seconds | ~15 seconds |
| **HDMI ports** | 2 (micro-HDMI) | 2 HDMI + 2 DisplayPort |

---

## Setup Checklist

### Completed (2026-02-18/19)

- [x] Set hostname to `nomadpc` (Phase 1.3)
- [x] Disable sleep/screensaver (Phase 1.6)
- [x] Restrict Avahi to `enp2s0` only (Phase 2.4)
- [x] Install Tailscale (Phase 2.5)
- [x] Clone repo and set up KJ Controller venv (Phase 3.2-3.3)
- [x] Create `config.json` with `enable_vlc` flag (Phase 3.4)
- [x] Apply platform detection patches to `app.py` and `vlc.py` (Phase 3.6)
- [x] Create and enable systemd services (Phase 4)
- [x] Set desktop wallpaper to Nomad background (Phase 5.3)
- [x] Install and configure x11vnc for VNC preview (Phase 5.5)
- [x] Set HDMI output to 1920x1080 with boot persistence
- [x] Verify HDMI audio works via direct ALSA `hdmiout` device (speaker-test + VLC)
- [x] Mount USB SSD and add to fstab (Phase 6.1-6.2)
- [x] Copy filler music (Phase 6.3)
- [x] Migrate 697 legacy YouTube downloads to new naming convention (Phase 6.4)
- [x] Build external catalog — 414,933 entries indexed (Phase 6.5)
- [x] Reboot test — all services survive (Phase 7.7)
- [x] Add NOPASSWD sudo for `nomad` user
- [x] Generate and deploy TLS certs with mkcert (Phase 5.6)
- [x] Configure Cloudflare tunnel for web UI + VNC (Phase 2.6)
- [x] Configure Cloudflare Access (Zero Trust auth) (Phase 2.7)
- [x] Commit platform detection patches to repo (Phase 3.6)
- [x] Fix auto-deploy sudo for systemctl (auto-deploy.sh)
- [x] Smart websockify routing — LAN direct, tunnel via hostname (templates/index.html)

### Completed (2026-02-24)

- [x] Reformat USB SSD from exFAT to ext4 for power-loss safety (Phase 6.1)
- [x] Restore 413,670 files (3.44 TB) from HDD backup to reformatted SSD
- [x] Power-loss hardening (Phase 1.7): kernel.panic=10, journal cap 200M, SSD noatime, atomic JSON writes

### Remaining

- [ ] Run `sudo apt update && sudo apt upgrade -y` (341 packages pending, mirror hash issue)
- [ ] Check BIOS for "Power On After Power Loss" setting (Phase 1.7) — requires physical access
- [ ] Set DHCP reservation on GL.iNet router for `84:47:09:5a:1d:13` (Phase 2.3)
- [ ] Copy `youtube_cookies.txt` from Pi if needed
- [ ] Test with HDMI splitter + projector at venue
- [ ] Harden SSH: disable password authentication (Phase 1.5)
