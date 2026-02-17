# Troubleshooting

> Operations runbook for NomadPi. For hardware specs and system config, see [archive/NOMADPI-DETAILS.md](archive/NOMADPI-DETAILS.md). For audio-specific issues, see [AUDIO.md](AUDIO.md).

## Desktop Crashes / Multiple X Sessions

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

## Display Outputs Not Synchronized

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

## Taking Screenshots Remotely

**Tool:** `scrot` (installed as of 2026-02-15)

```bash
# Take screenshot
ssh nomadpi 'DISPLAY=:0 scrot /tmp/screenshot.png'

# Copy to local machine
scp nomadpi:/tmp/screenshot.png ~/Desktop/

# Take screenshot and copy in one command
ssh nomadpi 'DISPLAY=:0 scrot /tmp/screen.png' && scp nomadpi:/tmp/screen.png ~/Desktop/
```

## VNC Not Working

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

## Desktop Not Starting

```bash
# Check autostart mode
ssh nomadpi 'cat /boot/dietpi/.dietpi-autostart_index'

# Should be 2 for Desktop autologin
ssh nomadpi '/boot/dietpi/dietpi-autostart 2'

# Restart getty to apply changes
ssh nomadpi 'systemctl restart getty@tty1'
```

## Docker Containers Not Running

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

## Network Issues

NomadPi uses dual-interface networking: Ethernet (preferred, metric 100) and WiFi (fallback, metric 200). Both use DHCP. See [archive/NOMADPI-DETAILS.md](archive/NOMADPI-DETAILS.md) § Network Configuration for full details and management commands.

```bash
# Check IP addresses on both interfaces
ssh nomadpi 'ip -4 addr show eth0; ip -4 addr show wlan0'

# Check routing table (lower metric = preferred route)
ssh nomadpi 'ip route show'

# Test internet connectivity
ssh nomadpi 'ping -c 3 8.8.8.8'

# Renew DHCP lease (e.g. after switching routers)
ssh nomadpi 'dhclient -r eth0 && dhclient eth0'

# Restart all networking
ssh nomadpi 'systemctl restart networking'

# Check WiFi status
ssh nomadpi 'iwconfig wlan0'
```

**Can't find the Pi on the network?**
1. Check the router's DHCP client list for MAC `E4:5F:01:B5:5D:C0` (Ethernet) or `E4:5F:01:B5:5D:C1` (WiFi)
2. Scan the subnet: `nmap -sn 192.168.X.0/24`
3. Try Tailscale: `ssh root@100.66.53.104`
4. If the Pi is on a different subnet, add a temporary IP alias on your Mac to reach it (see Changelog 2026-02-17)

## SSH Connection Issues

```bash
# Check SSH service
ssh nomadpi 'systemctl status ssh'

# Check authorized keys
ssh nomadpi 'cat ~/.ssh/authorized_keys'

# View SSH logs
ssh nomadpi 'journalctl -u ssh -f'
```

## Bluetooth Not Working or Not Discoverable

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

## Common Tasks

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
