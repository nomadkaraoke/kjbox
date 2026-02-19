# Claude Code Instructions for kjbox

## Overview

This repository (`kjbox`) contains documentation and software for Nomad Karaoke live event devices:

- **NomadPi** — Raspberry Pi 4 running DietPi (the original device)
- **NomadPC** — Intel N97 mini PC running Linux Mint 22.1 (more powerful replacement)

Both run the same KJ Controller software stack for video playback, AV equipment connection, and karaoke show management.

### Repository Contents

```
docs/
  ARCHITECTURE.md              # KJ Controller system architecture, API reference, design decisions
  DEVELOPMENT.md               # Local setup, dev workflow, running tests
  TESTING.md                   # Test conventions, coverage targets, fixtures
  AUDIO.md                     # Audio configuration: HDMI/ALSA, device switching, live event routing
  MINIPC-SETUP.md              # Setup guide for deploying to the x86 mini PC (NomadPC)
  TROUBLESHOOTING.md           # Operations runbook: troubleshooting guides, common tasks
  CHANGELOG.md                 # Device configuration change log (dated entries)
  archive/
    NOMADPI-DETAILS.md         # Pi-specific device reference: hardware, network, display, boot, services
    NETWORK-CONFIG-BACKUP.md   # Tailscale VPN and Cloudflare tunnel configuration backup
    2026-02-15-phase2-*.md     # Completed plan files (historical)
kj-controller/                 # KJ Remote Controller web app for managing karaoke playback
```

### Documentation Structure

Documentation is split by audience and purpose:

| File | Audience | Content |
|------|----------|---------|
| `docs/MINIPC-SETUP.md` | System admins | Step-by-step setup guide for the mini PC (NomadPC) |
| `docs/AUDIO.md` | Developers + system admins | ALSA config, HDMI audio, device switching, live event routing |
| `docs/TROUBLESHOOTING.md` | System admins | Troubleshooting guides, common tasks |
| `docs/CHANGELOG.md` | Everyone | Dated log of all device configuration changes |
| `docs/archive/NOMADPI-DETAILS.md` | System admins | Pi-specific hardware specs, network, display, boot, VNC, services |

### Documentation Maintenance - CRITICAL

**IMPORTANT:** When working with either device (NomadPi or NomadPC), you MUST maintain comprehensive documentation:

1. **Update the right file** based on what changed:
   - Audio/ALSA/VLC audio changes → `docs/AUDIO.md`
   - Troubleshooting steps or common tasks → `docs/TROUBLESHOOTING.md`
   - Hardware, network, display, boot, services, or config file paths → `docs/archive/NOMADPI-DETAILS.md`
   - **Always** add a dated entry to `docs/CHANGELOG.md` for any system change

2. **What to document:**
   - Exact configuration changes made (with file paths)
   - Commands used to make changes
   - Before/after states
   - Troubleshooting steps that worked
   - Any gotchas or important notes discovered

3. **Documentation format:**
   - Add new sections for new features/services in the appropriate file
   - Update existing sections when changing configurations
   - Include complete command examples with SSH prefix
   - Document both the "what" and the "why"
   - Add dated entries to the Change Log

4. **Why this matters:**
   - These devices are physical hardware setups that are difficult to replicate
   - Configuration knowledge would be lost between sessions without documentation
   - Future troubleshooting depends on understanding current state
   - The documentation serves as the single source of truth for device configuration

### Archive Convention

- Files in `docs/archive/` are either **living reference docs** (e.g., `NOMADPI-DETAILS.md`) or **historical artifacts** with date prefixes (e.g., `2026-02-15-phase2-solid-refactor.plan.md`)
- Date prefixes (`YYYY-MM-DD-`) are for completed/point-in-time documents only, not for actively maintained references

---

**Remember:** If you configure it, document it. If you learn it, document it. If you fix it, document it.
