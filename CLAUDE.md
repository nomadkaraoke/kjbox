# Claude Code Instructions for kjbox

**Read [README.md](README.md) first** for device info, SSH access, repo structure, and development workflow.

## What This Is

KJ Controller (`kj-controller/`) is a Flask + vanilla JS web app for managing live karaoke shows. It runs on physical devices (NomadPi, NomadPC) controlling dual VLC instances, YouTube downloading, song catalog search (~415K songs), display overlays, and VNC screen preview.

## Key Files

**Frontend** (vanilla JS — no framework, no build step):
- `kj-controller/templates/index.html` — single-page HTML (Jinja2 template)
- `kj-controller/static/style.css` — dark theme, Nomad branding, responsive
- `kj-controller/static/app.js` — all UI logic, fetch() REST calls, 2s status polling

**Backend** (Flask):
- `kj-controller/routes.py` — 32 REST API endpoints
- `kj-controller/vlc.py` — VLCManager (dual VLC process control)
- `kj-controller/media.py` — MediaIndex (scan, validate, download, delete)
- `kj-controller/overlay.py` — OverlayManager (CRUD, JSON persistence)
- `kj-controller/catalog.py` — ExternalCatalog (SQLite FTS5 search)
- `kj-controller/karaoke_nerds.py` — Karaoke Nerds web scraper (search, parse, YouTube URLs)
- `kj-controller/youtube_search.py` — YouTube search via yt-dlp (fast metadata-only)
- `kj-controller/app.py` — app factory + entry point
- `kj-controller/config.py` — constants, platform detection, config loading

**Detailed architecture**: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (module diagram, full API reference, design decisions)

## Deployment

Push to `main` → auto-deployed to devices within ~60s (no build step).

```bash
ssh nomadpc                          # Mini PC (primary device)
ssh nomadpi                          # Raspberry Pi
ssh nomadpc 'sudo systemctl restart kj-controller'   # restart after Python changes
ssh nomadpc 'journalctl -u kj-controller -f'         # tail logs
```

Web UI: `http://nomadpc.local` (LAN) or `https://kjbox.nomadkaraoke.com` (tunnel)

## Testing

```bash
cd kj-controller && pytest                         # all tests
cd kj-controller && pytest --cov --cov-report=term # with coverage (target: 70%+)
```

See [docs/TESTING.md](docs/TESTING.md) for conventions and fixtures.

## Documentation Maintenance

When making **system/device changes** (not just code), update docs:

| Change Type | File to Update |
|---|---|
| Audio / ALSA / VLC audio | `docs/AUDIO.md` |
| Troubleshooting / common tasks | `docs/TROUBLESHOOTING.md` |
| Hardware, network, display, services | `docs/archive/NOMADPI-DETAILS.md` |
| Mini PC setup or config | `docs/MINIPC-SETUP.md` |
| **Any system change** | Add dated entry to `docs/CHANGELOG.md` |

These are physical devices — configuration knowledge must be documented or it's lost between sessions.
