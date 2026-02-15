# NomadPi Network Configuration Backup

**Date:** 2026-02-15
**Source:** Extracted from FoxTag1 configuration before cleanup

---

## 🌐 Tailscale VPN

### Current Status
- **Installed:** System-wide (DietPi package, not Docker)
- **Service:** `tailscaled.service` (already running)
- **Current IP:** `[Private Tailscale IP]`
- **Account:** beveradb@github
- **Status:** ✅ Active and will persist after FoxTag cleanup

### Configuration
Tailscale is installed at the system level via DietPi and is **not** affected by removing the FoxTag Docker containers. No action needed - it will continue working.

**Note:** The hostname in Tailscale will automatically update once we change the system hostname from "foxtag1" to "nomadpi".

### Useful Commands
```bash
# Check Tailscale status
ssh nomadpi 'tailscale status'

# Get Tailscale IP
ssh nomadpi 'tailscale ip'

# Check service status
ssh nomadpi 'systemctl status tailscaled'
```

---

## ☁️ Cloudflare Tunnel

### Current Configuration (FoxTag)
- **Tunnel ID:** `[REDACTED - stored securely]`
- **Domain:** kiosk-1.foxtag.us
- **Service:** Forwarding to http://127.0.0.1:3000

### Tunnel Token
```
CLOUDFLARE_TUNNEL_TOKEN=[REDACTED - stored securely, not in git]
```

**Note:** The actual tunnel token is stored securely outside of version control.

### How It Was Running
The Cloudflare tunnel was running as a Docker container with this configuration:

```yaml
cloudflared:
  image: cloudflare/cloudflared:latest
  container_name: foxtag-cloudflared
  restart: unless-stopped
  command: tunnel --no-autoupdate run --token ${CLOUDFLARE_TUNNEL_TOKEN}
  network_mode: host
```

### Reusing for NomadPi

**Option 1: Reuse Existing Tunnel (Update Configuration)**
1. Log into Cloudflare Zero Trust dashboard
2. Find your tunnel by ID (stored securely outside git)
3. Update the public hostname and/or service URL to point to your new Nomad Karaoke service

**Option 2: Create New Tunnel for NomadPi**
1. Create new tunnel at: https://one.dash.cloudflare.com/
2. Set up new domain (e.g., nomadkaraoke.yourdomain.com)
3. Get new tunnel token
4. Run with Docker:
```bash
docker run -d \
  --name nomadpi-cloudflared \
  --restart unless-stopped \
  --network host \
  cloudflare/cloudflared:latest \
  tunnel --no-autoupdate run --token YOUR_NEW_TOKEN
```

**Option 3: Run as System Service (Instead of Docker)**
```bash
# Install cloudflared
ssh nomadpi 'curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb'
ssh nomadpi 'dpkg -i cloudflared.deb'

# Configure with your token
ssh nomadpi 'cloudflared tunnel install YOUR_TOKEN'

# Start service
ssh nomadpi 'systemctl enable --now cloudflared'
```

---

## 🔑 Important Notes

1. **Tailscale is safe** - It's installed system-wide and will continue working after we remove FoxTag containers.

2. **Cloudflare Tunnel will stop** when we remove the `foxtag-cloudflared` container. You can:
   - Reuse the same token/tunnel by updating the Cloudflare dashboard configuration
   - Or create a new tunnel specifically for Nomad Karaoke

3. **Tunnel Configuration Lives in Cloudflare** - The actual routing rules (which domain points where) are configured in the Cloudflare Zero Trust dashboard, not on the Pi. The Pi just needs the token to connect.

---

## 📋 Next Steps

When you're ready to set up remote access for Nomad Karaoke:

1. Decide what service/port you want to expose (e.g., a web interface for karaoke control)
2. Either update the existing Cloudflare tunnel or create a new one
3. Run cloudflared (via Docker or as a system service) with the appropriate token
4. Test access via both Tailscale (private) and Cloudflare (public) if needed

---

## 🔗 Useful Links

- **Cloudflare Zero Trust Dashboard:** https://one.dash.cloudflare.com/
- **Cloudflare Tunnel Docs:** https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
- **Tailscale Admin:** https://login.tailscale.com/admin/machines
