# NomadPi - Raspberry Pi Configuration Guide

**Last Updated:** 2026-02-15
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
```

### Boot Configuration
File: `/boot/config.txt`
```
hdmi_drive=2              # Use HDMI audio
hdmi_blanking=1           # Enable screen blanking
disable_overscan=1        # Disable overscan
gpu_mem_256=76            # GPU memory allocation
gpu_mem_512=76
gpu_mem_1024=76
disable_splash=1          # No splash screen
dtparam=audio=on          # Enable audio
enable_uart=0             # UART disabled
arm_64bit=1               # 64-bit kernel
dtoverlay=vc4-kms-v3d     # KMS video driver
temp_limit=75             # Temperature limit
initial_turbo=20          # Initial turbo boost
```

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
export XAUTHORITY=/home/dietpi/.Xauthority
exec sudo -u dietpi /usr/bin/vlc "$@"
```

**Desktop Launcher:** `/usr/share/applications/vlc.desktop` is configured to use the wrapper.

**Manual Launch:**
```bash
# From desktop/terminal as root
ssh nomadpi '/usr/local/bin/vlc-root-wrapper'

# Direct as dietpi user
ssh nomadpi 'sudo -u dietpi DISPLAY=:0 XAUTHORITY=/home/dietpi/.Xauthority vlc'
```

**User Configuration:**
- VLC runs as: `dietpi` user
- Groups: `dietpi`, `video`, `audio`
- X Authority: `/home/dietpi/.Xauthority` (copied from root)

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
  - **Video Output:** Connected via HDMI-2 at 1024x768
  - **Note:** This is a USB touchscreen, not a DSI ribbon cable display
- **USB Hub:** VIA Labs Hub (for peripherals)

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
Automatic via Watchtower (checks every 60 seconds)

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
scp foxtag1:/tmp/screenshot.png ~/Desktop/

# Take screenshot and copy in one command
ssh nomadpi 'DISPLAY=:0 scrot /tmp/screen.png' && scp foxtag1:/tmp/screen.png ~/Desktop/
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
ssh nomadpi 'cd /opt/foxtag && docker compose restart'

# View container logs
ssh nomadpi 'docker logs --tail 100 foxtag-backend'
```

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
- `/etc/xdg/lxsession/LXDE/autostart` - LXDE autostart
- `/var/log/Xorg.0.log` - X server log
- `/usr/local/bin/startx-single` - Custom X startup wrapper (prevents multiple sessions)
- `/boot/dietpi/dietpi-login` - DietPi login script (modified to use startx-single)

### VNC
- `/root/.vnc/config.d/vncserver-x11` - Service Mode config
- `/root/.vnc/config.d/Xvnc` - Virtual Mode config
- `/lib/systemd/system/vncserver-x11-serviced.service` - Service Mode systemd unit
- `/etc/systemd/system/vncserver.service` - Virtual Mode systemd unit (DietPi wrapper)


### Docker
- `/var/run/docker.sock` - Docker socket
- `/mnt/dietpi_userdata/docker-data` - Docker data root
- `/etc/systemd/system/docker.service.d/` - Docker service overrides

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

# On FoxTag1
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

# Docker container logs
ssh nomadpi 'docker logs -f foxtag-backend'

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

### 2026-02-15 - VLC Media Player Configuration
**Issue:** VLC launcher icon wasn't working when clicked from desktop.

**Root Cause:** VLC refuses to run as root user for security reasons. Desktop environment runs as root on NomadPi.

**Solution Implemented:**
1. Created wrapper script at `/usr/local/bin/vlc-root-wrapper` that runs VLC as `dietpi` user
2. Added `dietpi` user to `video` and `audio` groups
3. Copied X authority file to `/home/dietpi/.Xauthority` for X11 access
4. Modified `/usr/share/applications/vlc.desktop` launcher to use wrapper
5. Restarted desktop environment to apply changes

**Result:** VLC now launches successfully from desktop icon.

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

**Next Steps:**
- Configure video playback software for karaoke
- Set up AV output configuration
- Install Nomad Karaoke-specific applications
- Optionally configure new Cloudflare tunnel for remote access

### 2026-02-15 - Bluetooth Configuration
**Changes Made:**
1. **Enabled Bluetooth Pairing**
   - Set `AlwaysPairable = true` in `/etc/bluetooth/main.conf`
   - Device now accepts pairing requests at all times

2. **Permanent Discovery Mode**
   - Set `DiscoverableTimeout = 0` in `/etc/bluetooth/main.conf`
   - Device stays discoverable indefinitely (no 3-minute timeout)
   - "FoxTag1" is always visible to nearby Bluetooth devices

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
   - Renamed `/etc/cron.d/foxtag-watchdog` → `/etc/cron.d/foxtag-watchdog.disabled`
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
- 7" touchscreen connected via HDMI-2 at 1024x768

---

**Note:** This documentation was initially generated on 2026-02-15 and is updated as the system configuration changes.
