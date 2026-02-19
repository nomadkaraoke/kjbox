# kjbox

Hardware, software, and configuration for Nomad Karaoke live event devices.

## Devices

### NomadPi (Raspberry Pi 4)

The original karaoke rig — a Raspberry Pi 4 running DietPi. Connects to displays/projectors and sound systems.

- **Hardware:** Pi 4 Model B (2GB RAM, 256GB SD card), 7" touchscreen, Yamaha MG-XU mixer
- **OS:** DietPi (Debian 13 Trixie) with LXDE desktop
- **Access:** `ssh nomadpi` (192.168.8.106), `nomadpi.local`, Tailscale
- **Docs:** [docs/archive/NOMADPI-DETAILS.md](docs/archive/NOMADPI-DETAILS.md)

### NomadPC (x86 Mini PC)

A more powerful replacement/companion — Intel N97 mini PC running Linux Mint.

- **Hardware:** Intel N97 (4-core, 3.6GHz), 16GB RAM, 476GB NVMe SSD, 2x HDMI + 2x DP
- **OS:** Linux Mint 22.1 Xia (XFCE desktop), PipeWire audio
- **Access:** `ssh nomadpc` (192.168.8.170), `nomadpc.local`, Cloudflare tunnel (`kjbox.nomadkaraoke.com`)
- **Setup Guide:** [docs/MINIPC-SETUP.md](docs/MINIPC-SETUP.md)

### Software Stack (shared)

| Component | Purpose |
|-----------|---------|
| VLC | Video/audio playback |
| KJ Controller | Web-based karaoke show management (Flask + yt-dlp + VLC) |
| Rotation Display | Singer queue overlay from Google Sheets (Conky + Python) |
| Overlay Engine | Dynamic display overlays (ticker, countdown, QR, etc.) |

## Repository Structure

```
kjbox/
  README.md                    # This file
  CLAUDE.md                    # Claude Code agent instructions
  LICENSE                      # MIT License
  docs/
    ARCHITECTURE.md            # System architecture and API reference
    DEVELOPMENT.md             # Local setup and dev workflow
    TESTING.md                 # Test conventions and coverage
    AUDIO.md                   # Audio config: HDMI/ALSA, device switching, live event routing
    MINIPC-SETUP.md            # Setup guide for deploying to a new x86 mini PC
    TROUBLESHOOTING.md         # Operations runbook: troubleshooting, common tasks
    CHANGELOG.md               # NomadPi system configuration change log
    archive/
      NOMADPI-DETAILS.md       # Device reference: hardware, network, display, services
      NETWORK-CONFIG-BACKUP.md # Tailscale & Cloudflare tunnel backup
  desktop/
    rotation.conkyrc           # Conky config for singer queue overlay (full-screen layout)
    rotation_data.py           # Data fetcher: Google Sheet → conky markup (stdlib only)
    rotation-bg.png            # 1920x1080 wallpaper background (faux transparency)
    nomad-kjbox-desktop-background-4k.jpg  # 4K source wallpaper
  kj-controller/               # KJ Remote Controller web app
    app.py                     # App factory (create_app) + entry point
    config.py                  # Constants, platform detection, config loading
    utils.py                   # Logging and filename utilities
    media.py                   # MediaIndex class (scan, validate, download)
    vlc.py                     # VLCManager class (dual VLC instance control)
    routes.py                  # Flask Blueprint with REST API handlers
    pyproject.toml             # Project metadata and tool config
    requirements.txt           # Production dependencies
    requirements-dev.txt       # Test dependencies
    templates/                 # Web UI templates
    tests/                     # pytest test suite (115 tests, 88% coverage)
```

## KJ Remote Controller

The `kj-controller/` directory contains a web-based karaoke show management app. It provides:

- **One-click playback** of karaoke videos via VLC
- **YouTube downloading** with yt-dlp for building a song library
- **Filler music** with intelligent crossfading between songs
- **VNC screen preview** — live thumbnail of the Pi's HDMI output in the browser (noVNC + websockify)
- **Dynamic overlays** — scrolling tickers, countdown timers, QR codes, and more on the display
- **Remote control** from any device on the local network

See [kj-controller/README.md](kj-controller/README.md) for setup and usage.

## Device Documentation

NomadPi documentation is split by topic:

- **[docs/MINIPC-SETUP.md](docs/MINIPC-SETUP.md)** - Setup guide for deploying to a new x86 mini PC
- **[docs/AUDIO.md](docs/AUDIO.md)** - Audio configuration (HDMI with custom EDID, ALSA, USB mixer, live event routing)
- **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** - Troubleshooting guides and common Pi tasks
- **[docs/CHANGELOG.md](docs/CHANGELOG.md)** - System configuration change log
- **[docs/archive/NOMADPI-DETAILS.md](docs/archive/NOMADPI-DETAILS.md)** - Hardware specs, network, display, boot, VNC, services, config file paths

## Quick Start

### Accessing Devices

```bash
# NomadPi (Raspberry Pi)
ssh nomadpi                    # GL.iNet router (192.168.8.106)
ssh root@100.66.53.104         # Tailscale VPN (from anywhere)

# NomadPC (Mini PC)
ssh nomadpc                    # GL.iNet router (192.168.8.170)
# Via Cloudflare tunnel:       ssh kjbox.nomadkaraoke.com (requires cloudflared access)

# Either device via mDNS (any LAN):
ping nomadpi.local
ping nomadpc.local
```

### Running KJ Controller

KJ Controller runs as a systemd service on both devices. Access from any browser:
```
http://nomadpi.local    # Pi
http://nomadpc.local    # Mini PC
```

### Testing Audio

```bash
# Pi (ALSA, custom HDMI config)
ssh nomadpi 'speaker-test -D hdmiout -c 2 -t sine -f 440 -l 1'

# Mini PC (PipeWire, default HDMI)
ssh nomadpc 'speaker-test -c 2 -t sine -f 440 -l 1'
```

## Development

### Frontend Architecture

The KJ Controller UI is a **single-page vanilla JavaScript app** — no framework, no build step:

- `kj-controller/templates/index.html` — HTML structure (Jinja2 template)
- `kj-controller/static/style.css` — all styles (dark theme, responsive)
- `kj-controller/static/app.js` — all UI logic (`fetch()` REST calls, DOM manipulation)

The UI polls `/status` every 2 seconds for live player state. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full module diagram, API reference, and design decisions.

### Auto-Deploy

The `kj-autodeploy` service on each device polls `origin/main` and pulls changes automatically within ~60 seconds. For static files (HTML, CSS, JS), the change takes effect on the next browser refresh. For Python changes, the service needs a restart:

```bash
ssh nomadpc 'sudo systemctl restart kj-controller'
```

### Quick Iteration

```bash
# Edit locally, push to main, auto-deploys in ~60s
git add <files> && git commit -m "message" && git push

# Or pull manually on device
ssh nomadpc 'cd /opt/nomad/kjbox && git pull origin main'

# Restart service if Python files changed
ssh nomadpc 'sudo systemctl restart kj-controller'

# Tail service logs
ssh nomadpc 'journalctl -u kj-controller -f'
```

### Testing

```bash
cd kj-controller
pytest                              # all tests
pytest --cov --cov-report=term      # with coverage
```

See [docs/TESTING.md](docs/TESTING.md) for conventions, fixtures, and coverage targets. See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for local setup details.

## Key Technical Notes

- **Pi HDMI Audio** requires a custom EDID override and `iec958` ALSA plugin due to the 7" touchscreen's corrupt EDID and kernel 6.12's IEC958 subframe format. See [docs/AUDIO.md](docs/AUDIO.md).

- **Mini PC Audio** uses PipeWire and works out of the box — no custom ALSA config needed.

- **Pi VLC** runs as the `dietpi` user (not root) via a wrapper script. **Mini PC VLC** runs directly as the `nomad` user.

- **Platform detection:** `is_pi()` checks for `/boot/dietpi.txt`. The mini PC uses `"enable_vlc": true` in `config.json` instead.

## License

MIT - see [LICENSE](LICENSE).
