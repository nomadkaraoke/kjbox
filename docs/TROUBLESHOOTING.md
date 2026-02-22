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

## VNC Not Working (Native Client)

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

## VNC Browser Preview Not Working

The web UI includes a VNC screen preview (noVNC → websockify → RealVNC). If it's not connecting:

**Check websockify is running:**
```bash
ssh nomadpi 'ss -tlnp | grep 6080'
# Should show websockify listening on :6080
```

**If websockify isn't running**, check the kj-controller logs:
```bash
ssh nomadpi 'journalctl -u kj-controller --no-pager | grep -i websock'
# Look for "websockify not found" or startup errors
```

**Test websockify directly** (should return HTTP 405 — that's correct, it expects WebSocket upgrades):
```bash
curl -k https://nomadpi.local:6080/
```

**"Connecting..." hangs indefinitely:**
- This usually means RA2ne server verification isn't being handled. Ensure the `serververification` event handler is present in the noVNC script (auto-approves the server's RSA key).
- Check browser console for errors. The noVNC module requires HTTPS (`crypto.subtle` is only available in secure contexts).

**TLS certificate issues:**
```bash
# Check certs exist on Pi
ssh nomadpi 'ls -la /opt/nomad/kjbox/kj-controller/certs/'
# Should have cert.pem and key.pem

# If certs are missing or expired, regenerate with mkcert on your Mac:
mkcert nomadpi.local nomadpi 192.168.8.106 localhost 127.0.0.1
scp cert.pem key.pem nomadpi:/opt/nomad/kjbox/kj-controller/certs/
ssh nomadpi 'systemctl restart kj-controller'
```

**RealVNC encryption mismatch:**
```bash
# Ensure Encryption=PreferOff is set
ssh nomadpi 'grep Encryption /root/.vnc/config.d/vncserver-x11'
# If not set:
ssh nomadpi 'echo "Encryption=PreferOff" >> /root/.vnc/config.d/vncserver-x11'
ssh nomadpi 'systemctl restart vncserver-x11-serviced'
```

**Authentication failed:**
- Click "Forget Password" in the VNC preview UI, then re-enter the VNC password
- Verify the VNC password is correct: `ssh nomadpi 'vncpasswd -print'`

## Desktop Not Starting

**Check LightDM (display manager):**
```bash
# Check if LightDM is running
ssh nomadpi 'systemctl status lightdm'

# Check LightDM logs for auth failures
ssh nomadpi 'cat /var/log/lightdm/lightdm.log | tail -30'

# Check xsession errors
ssh nomadpi 'tail -20 /root/.xsession-errors'

# Restart LightDM
ssh nomadpi 'systemctl restart lightdm'
```

**Common causes after OS upgrade:**
- LightDM autologin not configured: check `/etc/lightdm/lightdm.conf` has `autologin-user=root` and `autologin-session=LXDE`
- PAM blocks root autologin (Trixie default): check `/etc/pam.d/lightdm-autologin` — the line `auth required pam_succeed_if.so user != root quiet_success` must be commented out
- LXDE not installed: `apt-get install -y lxde`
- xsession-errors says "no session managers, no window managers": LXDE packages missing

**Check DietPi autostart mode:**
```bash
# Check autostart mode
ssh nomadpi 'cat /boot/dietpi/.dietpi-autostart_index'

# Should be 2 for Desktop autologin
ssh nomadpi '/boot/dietpi/dietpi-autostart 2'

# Restart getty to apply changes
ssh nomadpi 'systemctl restart getty@tty1'
```

## Services Not Starting After OS Upgrade

**Python venv broken (ModuleNotFoundError):**
Major Python version upgrades (e.g., 3.11 → 3.13) invalidate the virtual environment. Rebuild it:
```bash
ssh nomadpi 'apt-get install -y python3-venv && cd /opt/nomad/kjbox/kj-controller && rm -rf venv && python3 -m venv venv && venv/bin/pip install -r requirements.txt'
ssh nomadpi 'systemctl restart kj-controller'
```

**auto-deploy.sh permission denied:**
Git doesn't preserve execute bits unless explicitly set. After a `git pull`:
```bash
ssh nomadpi 'chmod +x /opt/nomad/kjbox/kj-controller/auto-deploy.sh && systemctl restart kj-autodeploy'
```

**kj-controller not auto-starting at boot (no journal entries):**
Check `WantedBy=` in the service file. Services with `After=graphical.target` should use `WantedBy=graphical.target` (not `multi-user.target`), otherwise systemd may not start them correctly.

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
1. Try mDNS: `ping nomadpi.local` (works on any LAN via Avahi/Bonjour, no config needed)
2. Check the router's DHCP client list for MAC `E4:5F:01:B5:5D:C0` (Ethernet) or `E4:5F:01:B5:5D:C1` (WiFi)
3. Scan the subnet: `nmap -sn 192.168.X.0/24`
4. Try Tailscale: `ssh root@100.66.53.104`
5. If the Pi is on a different subnet, add a temporary IP alias on your Mac to reach it (see Changelog 2026-02-17)

**Can't reach NomadPC remotely (not on same LAN)?**

| Method | Command | Works remotely? | Requirement |
|--------|---------|-----------------|-------------|
| LAN/mDNS | `ssh nomadpc` | ❌ No — mDNS doesn't cross networks | Same LAN only |
| Tailscale | `ssh nomadpcts` | ✅ Yes | Tailscale app running on Mac |
| Cloudflare tunnel | `ssh nomadpctunnel` | ✅ Yes | `cloudflared` installed on Mac; browser auth on first use |

Verified 2026-02-22: both `nomadpcts` and `nomadpctunnel` confirmed working from a different network.

- Install cloudflared if needed: `brew install cloudflare/cloudflare/cloudflared`
- Start Tailscale: open the Tailscale menu bar app

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
