# Mini PC Setup Guide

Step-by-step guide to set up a new mini PC (x86_64, Linux Mint or similar) to run the same karaoke software stack as NomadPi. This guide assumes you're working from a Mac with SSH access to the mini PC.

**Target state:** The mini PC boots straight into a desktop with KJ Controller and the rotation display running automatically — identical behavior to the Pi.

**Hardware assumptions:**
- x86_64 mini PC (e.g., Intel N95, 16GB RAM, 512GB SSD)
- Single HDMI output (split to 7" monitor, projector, and singer screen)
- Ethernet connection to GL.iNet router (no WiFi needed)
- No USB mixer connection (mics are mixed separately to PA)

---

## Phase 1: OS & Base System

### 1.1 Review Current OS

If the mini PC already has Linux Mint (or similar Ubuntu/Debian-based distro), keep it. No need to reinstall.

```bash
# SSH in (you may need to find the IP from the router's DHCP client list first)
ssh user@<minipc-ip>

# Check what's installed
cat /etc/os-release
uname -a
free -h
df -h
```

### 1.2 Update to Latest

```bash
sudo apt update && sudo apt upgrade -y
sudo reboot
```

### 1.3 Set Hostname

```bash
sudo hostnamectl set-hostname nomadpc
# Also update /etc/hosts — replace any old hostname with nomadpc
sudo sed -i 's/127\.0\.1\.1.*/127.0.1.1\tnomadpc/' /etc/hosts
```

### 1.4 Create a Dedicated User (if needed)

On Linux Mint, you likely already have a regular user. The KJ Controller can run as that user (unlike the Pi where root was the primary user and VLC needed a wrapper). Note which user you'll use:

```bash
whoami  # e.g., "nomad" or whatever was set up at install
```

### 1.5 SSH Key Access

```bash
# From your Mac:
ssh-copy-id user@<minipc-ip>

# On the mini PC, optionally disable password auth for security:
sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

### 1.6 Disable Sleep/Screensaver/Power Management

Critical for a live event machine — it must never sleep or lock.

```bash
# Disable screen blanking and sleep
gsettings set org.cinnamon.desktop.screensaver lock-enabled false 2>/dev/null || true
gsettings set org.cinnamon.desktop.session idle-delay 0 2>/dev/null || true

# For MATE/XFCE, adapt the gsettings keys as needed

# Disable systemd sleep targets
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

# Disable DPMS (display power management) — add to autostart
echo 'xset s off -dpms' >> ~/.xprofile
```

### 1.7 Graceful Power Loss Handling

Configure the system to boot automatically after power loss and handle unclean shutdowns:

```bash
# Set BIOS to "Power On After Power Loss" (AC Recovery = Power On)
# This is a BIOS setting — access it during boot (usually DEL or F2)
# Look for: Advanced > Power Management > AC Power Loss > "Power On"

# Ensure filesystem is resilient (ext4 journaling is default — just verify)
mount | grep ' / '
# Should show ext4 (or similar journaling FS)
```

### 1.8 Auto-Login to Desktop

Linux Mint typically has auto-login configured during install. Verify:

```bash
# For LightDM (Mint default)
cat /etc/lightdm/lightdm.conf | grep -i autologin
# Should show: autologin-user=<your-username>

# If not set:
sudo tee -a /etc/lightdm/lightdm.conf.d/50-autologin.conf << 'EOF'
[Seat:*]
autologin-user=nomad
autologin-session=cinnamon
EOF
```

Replace `nomad` with your actual username, and `cinnamon` with your desktop session (e.g., `mate`, `xfce`).

---

## Phase 2: Network

### 2.1 Disable WiFi

```bash
# Turn off WiFi radio
nmcli radio wifi off

# Verify
nmcli general status
# Should show wifi: disabled
```

### 2.2 Configure Ethernet

Ethernet via DHCP should work automatically. Set up a DHCP reservation on the GL.iNet router:

1. Log into GL.iNet admin panel (http://192.168.8.1)
2. Go to LAN → Static IP Address Binding
3. Find the mini PC's MAC address: `ip link show eth0 | grep ether`
4. Bind it to a fixed IP (e.g., `192.168.8.120`)

### 2.3 Install Avahi (mDNS)

This lets you access the mini PC as `nomadpc.local` on any LAN.

```bash
sudo apt install -y avahi-daemon libnss-mdns
```

Restrict to ethernet only to avoid advertising Docker/VPN IPs:

```bash
sudo sed -i 's/^#allow-interfaces=eth0/allow-interfaces=eth0/' /etc/avahi/avahi-daemon.conf
# If the line doesn't exist, add it under [server]:
# allow-interfaces=eth0

# Check your interface name (might be enp1s0, eno1, etc. instead of eth0)
ip link show | grep 'state UP'
# Use whatever interface name is shown

sudo systemctl restart avahi-daemon
```

Verify from your Mac:
```bash
ping nomadpc.local
```

### 2.4 Install Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# Follow the auth URL to add to your tailnet
```

### 2.5 Update SSH Config on Mac

Add to `~/.ssh/config`:
```
Host nomadpc
    HostName 192.168.8.120
    User nomad
```

---

## Phase 3: Software Stack

### 3.1 Install System Dependencies

```bash
sudo apt install -y \
    git \
    python3-venv \
    python3-pip \
    vlc \
    conky-all \
    yt-dlp \
    curl
```

### 3.2 Clone the Repository

```bash
sudo mkdir -p /opt/nomad
sudo chown $(whoami):$(whoami) /opt/nomad
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
  "audio_devices": {
    "default": "Default HDMI"
  },
  "default_audio_device": "default"
}
EOF
```

**Note:** Audio device names will differ from the Pi. Run `aplay -L` to list available devices and update `audio_devices` accordingly. On x86 with HDMI, the default ALSA device usually works without the custom `iec958` plugin chain the Pi needs.

### 3.5 Create Data Directories

```bash
mkdir -p /opt/nomad/YTDownloads
mkdir -p /opt/nomad/FillerMusic
# If you have existing media, copy/mount it:
# mkdir -p /opt/nomad/Tracks-PublicShare
```

### 3.6 Platform Detection Fix

The app uses `is_pi()` to decide whether to enable VLC. On the mini PC, this returns `False` because there's no `/boot/dietpi.txt`. You have two options:

**Option A: Touch the sentinel file (quick hack)**
```bash
sudo touch /boot/dietpi.txt
```
This makes `is_pi()` return `True`. The Pi-specific VLC wrapper (`sudo -u dietpi`) will fail, so you also need Option B.

**Option B: Modify the code (proper fix)**

The `is_pi()` check gates two things:
1. Whether VLC is enabled (`vlc.py` line 18)
2. Whether to use the `sudo -u dietpi` wrapper (`vlc.py` line 59, `app.py` line 59)

The cleanest approach: create a config flag. In `config.json`, add:
```json
"enable_vlc": true
```

Then update `config.py`:
```python
def is_pi():
    """Detect if running on NomadPi (DietPi on Linux ARM)."""
    return os.path.exists('/boot/dietpi.txt')

def is_karaoke_device():
    """Detect if this is a karaoke playback device (Pi or mini PC)."""
    return is_pi() or os.path.exists('/opt/nomad/kjbox/kj-controller/config.json')
```

And update `vlc.py` to use `is_karaoke_device()` for the `enabled` check but keep `is_pi()` for the `sudo -u dietpi` wrapper. **This is a code change that should be planned and tested properly** — see the note at the end of this guide.

**For now, the simplest path:**
On the mini PC, VLC can run directly as your user (no root issue, no wrapper needed). The `is_pi()` = `False` path just needs to not disable VLC. A minimal patch to `vlc.py`:

In `VLCManager.__init__`:
```python
# Change from:
self.enabled = enabled if enabled is not None else is_pi()
# To:
self.enabled = enabled if enabled is not None else (is_pi() or config.get('enable_vlc', False))
```

In `VLCManager.launch_instance`, the `is_pi()` block wraps with `sudo -u dietpi`. The `else` branch already runs `cvlc` directly, which is what we want on the mini PC.

In `app.py` `start_app()`, the `is_pi()` block does `xhost` and `/run/user/1000` setup. The mini PC doesn't need this since VLC runs as the logged-in user.

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
# Port 80 requires this capability (or run as root)
AmbientCapabilities=CAP_NET_BIND_SERVICE
ExecStart=/opt/nomad/kjbox/kj-controller/venv/bin/python /opt/nomad/kjbox/kj-controller/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
EOF
```

**Note:** Replace `nomad` with your actual username. If `CAP_NET_BIND_SERVICE` doesn't work for port 80, you can either:
- Run the service as root (remove the `User=` line)
- Use port 8080 instead and update `config.json`

### 4.2 Auto-Deploy Service

```bash
sudo tee /etc/systemd/system/kj-autodeploy.service << 'EOF'
[Unit]
Description=KJ Controller Auto-Deploy from GitHub
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
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
ExecStart=/usr/bin/conky -c /opt/nomad/kjbox/desktop/rotation.conkyrc
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
EOF
```

### 4.4 Enable All Services

```bash
sudo systemctl daemon-reload
sudo systemctl enable kj-controller kj-autodeploy rotation-display
sudo systemctl start kj-controller kj-autodeploy rotation-display
```

---

## Phase 5: Display & Audio

### 5.1 HDMI Audio

On x86, HDMI audio typically works out of the box without the custom EDID and `iec958` plugin the Pi needs.

```bash
# List audio devices
aplay -L

# Test HDMI audio (find the right device name from aplay -L)
speaker-test -c 2 -t sine -f 440 -l 1

# If HDMI audio doesn't work with the default device, check:
aplay -l
# Note the card/device numbers and update config.json audio_devices accordingly
```

### 5.2 ALSA Configuration

If the default ALSA output isn't HDMI, create `/etc/asound.conf`:

```bash
# Only needed if HDMI isn't the default audio output
# Find the card number from: aplay -l
sudo tee /etc/asound.conf << 'EOF'
pcm.!default {
    type plug
    slave {
        pcm "hw:0,0"
    }
}

ctl.!default {
    type hw
    card 0
}
EOF
```

Replace `hw:0,0` with the correct card/device for your HDMI output.

### 5.3 Display Resolution

The HDMI splitter should handle resolution negotiation. Verify:

```bash
xrandr
# Should show 1920x1080 (or your target resolution)
```

### 5.4 Rotation Display Background

The conky overlay uses a 1920x1080 background image for faux transparency. If the desktop wallpaper differs from the Pi:

```bash
# Set the desktop wallpaper to match the rotation background
# Or regenerate rotation-bg.png from the 4K source:
cd /opt/nomad/kjbox/desktop
python3 -c "
from PIL import Image
img = Image.open('nomad-kjbox-desktop-background-4k.jpg')
img.resize((1920, 1080), Image.LANCZOS).save('rotation-bg.png', 'PNG')
"
# You may need: pip3 install Pillow
```

Set the desktop wallpaper to `nomad-kjbox-desktop-background-4k.jpg` via the desktop environment's settings.

### 5.5 Conky Font Check

```bash
# Verify DejaVu Sans is available (used by rotation display)
fc-list | grep -i "dejavu sans"
# If not installed:
sudo apt install -y fonts-dejavu
```

---

## Phase 6: Verification

### 6.1 Service Status

```bash
systemctl status kj-controller
systemctl status kj-autodeploy
systemctl status rotation-display
systemctl status avahi-daemon
```

### 6.2 KJ Controller Web UI

From your Mac:
```bash
curl -s http://nomadpc.local/status
# Should return JSON with player state

# Open in browser:
# http://nomadpc.local
```

### 6.3 VLC Playback

Test from the web UI — download a test video and play it. Verify:
- Video displays fullscreen on the HDMI output
- Audio comes through HDMI
- Filler music fades out before karaoke and fades back in after

### 6.4 Rotation Display

Check if the conky overlay is visible on the desktop. If not:
```bash
journalctl -u rotation-display --no-pager -l | tail -20
# Common issues:
# - Wrong DISPLAY variable
# - Font not found
# - Background image path incorrect
```

### 6.5 Auto-Deploy

```bash
journalctl -u kj-autodeploy --no-pager | tail -10
# Should show "Auto-deploy started (polling every 60s)"
```

### 6.6 mDNS

```bash
# From Mac:
ping nomadpc.local
ssh nomadpc  # via your .ssh/config
```

### 6.7 Reboot Test

The most important test — verify everything comes back after a cold boot:

```bash
sudo reboot
# Wait for it to come back up (30-60 seconds)
# Then verify all services are running:
ssh nomadpc 'systemctl is-active kj-controller kj-autodeploy rotation-display avahi-daemon'
curl http://nomadpc.local/status
```

---

## Differences from Pi Setup

| Aspect | NomadPi (Raspberry Pi 4) | Mini PC (x86_64) |
|--------|--------------------------|-------------------|
| **OS** | DietPi (Debian Trixie) | Linux Mint (Ubuntu-based) |
| **User** | root (VLC wrapped as dietpi) | Regular user (VLC runs directly) |
| **Display manager** | LightDM + LXDE | LightDM + Cinnamon/MATE |
| **HDMI audio** | Custom EDID + iec958 ALSA plugin | Works out of the box |
| **VLC launch** | `sudo -u dietpi env DISPLAY=:0 cvlc` | `cvlc` directly |
| **Platform detection** | `is_pi()` = True | Needs `enable_vlc: true` in config |
| **Performance** | 2GB RAM, SD card, ARM | 16GB RAM, SSD, x86_64 |
| **Power** | SD card corruption risk | SSD is more resilient |

---

## TODO After Initial Setup

- [ ] Copy media files (YTDownloads, Tracks-PublicShare, FillerMusic) from Pi or external drive
- [ ] Build external catalog if using external media: `curl -X POST http://nomadpc.local/catalog/build -H 'Content-Type: application/json' -d '{"file_list_path": "/path/to/file-list.txt"}'`
- [ ] Copy `youtube_cookies.txt` from Pi if needed for yt-dlp authentication
- [ ] Test with actual HDMI splitter + projector + 7" screen at venue
- [ ] Implement proper `enable_vlc` config flag (see Phase 3.6) as a code change in the repo
