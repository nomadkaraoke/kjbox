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

## Karaoke Video Crashes on AV1 Files (NomadPC)

**Symptoms:**
- A specific karaoke video (often a recent YouTube download) kills the mpv engine within a few
  seconds of starting; afterwards *every* song fails to load until Fix / a service restart.
- `ssh nomadpc 'coredumpctl list | grep -i mpv'` shows a fresh SIGSEGV.
- `ssh nomadpc 'sudo dmesg -T | grep mpv'` shows `segfault ... in libavcodec.so.60`.

**Cause:** The file is AV1-encoded and NomadPC is running the DFSG **free**
`intel-media-va-driver`, which has broken AV1 hardware decode — it returns "internal decoding
error 23", which ffmpeg turns into a segfault. (mpv defaults to `hwdec=vaapi`.) See
[CHANGELOG.md](CHANGELOG.md) § 2026-07-05.

**Fix — install the non-free Intel driver (no reboot):**
```bash
ssh nomadpc 'sudo apt-get install -y intel-media-va-driver-non-free'
# relaunch the karaoke engine:
ssh nomadpc "curl -s -X POST http://127.0.0.1:5001/fix_audio -H 'Content-Type: application/json' -d '{}'"
# verify: play an AV1 file; mpv's hwdec-current should read "vaapi"
```

**Interim workaround** (if you can't install the driver): force software decode —
`ssh nomadpc 'echo hwdec=no > ~/.config/mpv/mpv.conf'` then relaunch. Stops the crash but costs
~1 CPU core per song. Rollback: delete that file.

Track A auto-recovery (v0.68.0+) auto-restarts the engine and shows the KJ an amber banner if a
file still crashes it, so a crash is a ~2s blip rather than a dead show.

## 4TB USB SSD Drops Offline (Medium Not Present / 0 Capacity)

**Symptoms:**
- Local-media plays fail with `POST /play` → 400 "Invalid or inaccessible file path" while
  YouTube/internal-NVMe files still play fine.
- The drive still shows as mounted, but any file access returns `Input/output error`.
- `ssh nomadpc 'sudo dmesg -T | grep sda'` shows `Sense Key : Not Ready ... Medium not present`
  and EXT4 errors; eventually `Aborting journal on device sda1-8`.
- After a USB reset, `lsblk` shows `sda 0B` — the bridge enumerates but reports zero capacity.

**Cause (2026-08-27 incident):** the SanDisk Extreme Pro's internal ASMedia USB-NVMe bridge
firmware hung. Trigger was v0.94.0's SSD temperature polling (`smartctl -d sntasmedia` NVMe
admin passthrough every ~20s while the Stats panel was open) — that passthrough is known to hang
ASMedia bridge firmware. The polling was removed in v0.95.0; **do not reintroduce smartctl
polling against this drive**.

**Fix — only a physical power cycle recovers it:**
1. Unplug the SSD's USB cable from the NomadPC, wait ~10 seconds, plug it back in.
2. The drive auto-mounts and ext4 journal recovery runs on mount (check
   `sudo dmesg -T | tail` for `recovery complete`). No fsck needed — the errors are clean read
   failures from the hang, not corruption.
3. Verify: `df -h /media/nomad/Nomad4TBOne` shows 3.6T, and a file reads end-to-end.

**What does NOT work** (tried during the incident): `umount` + USB unbind/rebind
(`/sys/bus/usb/drivers/usb/{unbind,bind}`), xHCI PCI unbind/rebind, `uhubctl` (no per-port
power switching on this board), SCSI device delete + rescan. The bridge re-enumerates each time
but keeps reporting 0 blocks because VBUS never drops. Note: if the whole xHCI controller
(`0000:00:0d.0`) vanishes from PCI after an unplug (D3cold), `echo 1 | sudo tee
/sys/bus/pci/rescan` brings it back.

## Singer Submission Shows "Unavailable" or Auto-Swapped Version

A singer submitted a song through the `/sing` UI and it either quietly played a **different
version** than expected, or shows a **red ❌ download-failed** with the singer told "we couldn't
find a playable version."

- **This is expected auto-fallback behaviour (2026-07-09+).** The version the singer/KJ picked was
  an unavailable YouTube video (private, deleted, region-blocked). Rather than dead-ending, the
  download worker automatically tries the next-best candidate version of the same song and rebinds
  the request to whichever one downloads.
- **Auto-swap (no action needed):** the entry ends up linked to a working version and `/my-requests`
  shows it. If the swapped version is a poor match, use the rotation 🔗 link / "Try Another" button
  to pick a different one manually — same as before.
- **Terminal ❌ (KJ action):** every candidate was unavailable (or there were no alternates — e.g. a
  single-version song, or a raw pasted URL). Link a working file manually via the rotation entry.
- **Everything shows unavailable / nothing downloads:** that's a different problem — check YouTube
  health (cookies, yt-dlp version) and whether the `bgutil` PO-token helper at `127.0.0.1:4416` is
  reachable. Persistent transient errors (timeouts/429) exhaust the bounded retries and then surface
  as terminal ❌. `journalctl -u kj-controller` shows `Sing fallback:` lines tracing each decision.

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
