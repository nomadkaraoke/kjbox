# kjbox

Hardware, software, and configuration for the **NomadPi** - a Raspberry Pi 4 that powers Nomad Karaoke live events.

## What's in the Box

NomadPi is a portable karaoke rig built on a Raspberry Pi 4 running DietPi. It connects to displays/projectors and sound systems to run karaoke shows at live events.

### Hardware

- **Raspberry Pi 4 Model B** (2GB RAM, 256GB SD card)
- **7" Touchscreen** - KJ control interface (connected via HDMI-2 + USB)
- **External Display/Projector** - Audience-facing output (connects to HDMI-1)
- **Yamaha MG-XU USB Mixer** - Professional audio output
- **Speakers/Soundbar** - Connected via HDMI or mixer

### Software Stack

| Component | Purpose |
|-----------|---------|
| DietPi (Debian 13 Trixie) | Lightweight OS for Raspberry Pi |
| VLC | Video/audio playback with hardware acceleration |
| KJ Controller | Web-based karaoke show management (Flask + yt-dlp + VLC) |
| Rotation Display | Singer queue overlay from Google Sheets (Conky + Python) |
| LXDE | Desktop environment with touchscreen support |
| Tailscale | VPN for remote access in variable network environments |
| Docker | Container runtime (available for future services) |
| RealVNC | Remote desktop access |

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

### Accessing NomadPi

```bash
# Via Ethernet (preferred — GL.inet karaoke router, 192.168.8.x)
ssh nomadpi

# Via WiFi (fallback — Ubiquiti home network, 192.168.1.x)
ssh nomadpihomewifi

# Via Tailscale VPN (from anywhere)
ssh root@100.66.53.104

# VNC (native client — shares physical display)
# Connect to 192.168.8.106:5900

# VNC (browser preview — built into KJ Controller web UI)
# Open https://nomadpi.local and enter VNC password in "Screen Preview"
```

### Playing Karaoke Videos

```bash
# Launch VLC from desktop (via touchscreen or VNC)
# Click the VLC icon in the LXDE menu

# Or launch from SSH
ssh nomadpi '/usr/local/bin/vlc-root-wrapper /path/to/video.mp4'
```

### Testing Audio

```bash
# HDMI audio (default)
ssh nomadpi 'speaker-test -D hdmiout -c 2 -t sine -f 440 -l 1'

# USB mixer
ssh nomadpi 'speaker-test -D usbmixer -c 2 -t sine -f 440 -l 1'
```

### Running KJ Controller

```bash
ssh nomadpi
cd ~/kj-controller
source venv/bin/activate
python3 app.py
# Access from browser: http://nomadpi.local
```

## Key Technical Notes

- **HDMI Audio** requires a custom EDID override (`/lib/firmware/edid/nomadpi-hdmi.bin`) because the 7" touchscreen provides corrupt EDID data. The custom EDID includes the HDMI Vendor Specific Data Block needed for the kernel to enable audio packets. See the Audio Configuration section in NOMADPI-DETAILS.md.

- **VLC runs as the `dietpi` user** (not root) via a wrapper script, since VLC refuses to run as root. The wrapper handles X11 access, GPU permissions, and runtime directory setup.

- **The `iec958` ALSA plugin** is required on kernel 6.12+ because the vc4-hdmi driver only exposes `IEC958_SUBFRAME_LE` format. The ALSA config at `/etc/asound.conf` chains `iec958` + `plug` plugins for format conversion.

## License

MIT - see [LICENSE](LICENSE).
