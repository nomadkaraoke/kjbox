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
- `kj-controller/routes.py` — REST API endpoints
- `kj-controller/mpv_manager.py` — MpvManager (mpv karaoke with rubberband pitch + VLC filler)
- `kj-controller/vlc.py` — VLCManager (legacy, kept for rollback)
- `kj-controller/media.py` — MediaIndex (scan, validate, download, delete)
- `kj-controller/overlay.py` — OverlayManager (CRUD, JSON persistence)
- `kj-controller/rotation.py` — RotationManager (coordinator: delegates to rotation_store + rotation_sync)
- `kj-controller/rotation_store.py` — RotationStore (SQLite CRUD, position management, archive)
- `kj-controller/rotation_sync.py` — SheetSync (optional background push to Google Sheets)
- `kj-controller/catalog.py` — ExternalCatalog (SQLite FTS5 search)
- `kj-controller/karaoke_nerds.py` — Karaoke Nerds web scraper (search, parse, YouTube URLs)
- `kj-controller/youtube_search.py` — YouTube search via yt-dlp (fast metadata-only)
- `kj-controller/youtube_health.py` — YouTube health checks, cookie validation, EJS/Deno detection
- `kj-controller/sing.py` — Public `/sing/*` blueprint, token gate, host-based route guard, PWA manifest + service worker route. Also installs a WSGI rewrite that mounts the blueprint at the ROOT of the public host (`sing.nomadkaraoke.com/`) so singers never see the `/sing/` segment.
- `kj-controller/sing_store.py` — SingStore (SQLite CRUD for sing_requests + sing_push_subscriptions + event-token helpers)
- `kj-controller/push_dispatcher.py` — PushDispatcher (VAPID, subscription scan, ladder decision, dedup, thread-pool webpush sends — plugged into RotationManager._after_mutation)
- `kj-controller/wait_estimate.py` — Pure `compute_estimate(entries, target_id, cfg)` for singer-facing wait times (position + honest range from tonight's variance)
- `kj-controller/static-sing/sw.js` — Service worker, scope `/sing/`. Handles Web Push + notificationclick + shell cache
- `kj-controller/app.py` — app factory + entry point (also bootstraps VAPID keypair on first boot)
- `kj-controller/config.py` — constants, platform detection, config loading

**Hooks**:
- `.githooks/pre-commit` — JS syntax validation hook (activate with `git config core.hooksPath .githooks`)

**Detailed architecture**: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (module diagram, full API reference, design decisions)

## Deployment — PRODUCTION SAFETY

**NomadPC is a live production device.** It may be running a karaoke show with singers actively performing. Treat every deployment action as a production deploy.

### NEVER do these without explicit user permission:
- `git push` to `main` (triggers auto-deploy to devices within ~60s)
- `ssh nomadpc 'sudo systemctl restart kj-controller'` (kills active VLC playback mid-song)
- Any SSH command that modifies state on the device

### Safe actions (no permission needed):
- `ssh nomadpc 'journalctl -u kj-controller -f'` (read-only log tailing)
- `ssh nomadpc 'cat ...'` or other read-only SSH commands
- Running tests locally
- Committing locally (without push)

### Frontend-only changes (JS/CSS/HTML):
- Auto-deploy pulls the code but does NOT restart the service
- Changes take effect on next browser refresh — **no service interruption**
- Still requires permission to push since auto-deploy runs `git pull`

### Backend changes (Python):
- Requires service restart to take effect — **will interrupt active playback**
- Always ask user before pushing AND before restarting

```bash
ssh nomadpc                          # Mini PC (primary device)
ssh nomadpi                          # Raspberry Pi
ssh nomadpc 'journalctl -u kj-controller -f'         # tail logs (safe)
# REQUIRES PERMISSION: ssh nomadpc 'sudo systemctl restart kj-controller'
```

Web UI: `http://nomadpc.local` (LAN) or `https://kjbox.nomadkaraoke.com` (tunnel)

### Reaching the tunnel URL past Cloudflare Access

`https://kjbox.nomadkaraoke.com` is gated by Cloudflare Access (team `beveradb.cloudflareaccess.com`). A **service token** is provisioned so automation/curl can get through without the email login (the human email-login policy is untouched and still applies to browsers).

`WebFetch` can't send custom headers, so use `curl` with the service-token headers, sourced from env vars that direnv loads from the workspace `.envrc` (gitignored — secrets are **not** in this repo):

```bash
curl -s -H "CF-Access-Client-Id: $KJBOX_CF_ACCESS_CLIENT_ID" \
        -H "CF-Access-Client-Secret: $KJBOX_CF_ACCESS_CLIENT_SECRET" \
        https://kjbox.nomadkaraoke.com/
```

For a browser (e.g. Playwright), set those same two values as extra HTTP headers on the context before navigating. To bypass Cloudflare entirely, read-only `ssh nomadpc 'curl -s http://localhost/...'` hits the Flask app directly.

## Testing

```bash
cd kj-controller && pytest                         # all tests
cd kj-controller && pytest --cov --cov-report=term # with coverage (target: 70%+)
```

See [docs/TESTING.md](docs/TESTING.md) for conventions and fixtures.

## HDMI Troubleshooting

If the user reports HDMI video or audio issues on NomadPC, **read [docs/HDMI.md](docs/HDMI.md) first**. It contains:
- Full signal chain diagram (NomadPC → OREI splitter → venue displays)
- How HDMI, EDID, and Linux audio routing work
- EDID captures and test results for every HDMI device owned
- Known issues and fixes (especially the IEC958 mute switch)

**Quick diagnostic:** `ssh nomadpc '/opt/nomad/kjbox/kj-controller/hdmi-diag.sh'`

Key scripts:
- `kj-controller/hdmi-diag.sh` — pass/fail diagnostic with inline fix commands
- `kj-controller/fix-hdmi-audio.sh` — runs at boot (ExecStartPre), auto-detects HDMI audio device, enables IEC958 switches

## Documentation Maintenance

When making **system/device changes** (not just code), update docs:

| Change Type | File to Update |
|---|---|
| HDMI / display / video output | `docs/HDMI.md` |
| Audio / ALSA / VLC audio | `docs/AUDIO.md` |
| Troubleshooting / common tasks | `docs/TROUBLESHOOTING.md` |
| Hardware, network, display, services | `docs/archive/NOMADPI-DETAILS.md` |
| Mini PC setup or config | `docs/MINIPC-SETUP.md` |
| **Any system change** | Add dated entry to `docs/CHANGELOG.md` |

These are physical devices — configuration knowledge must be documented or it's lost between sessions.
