# Claude Code Instructions for kjbox

## Overview

This repository (`kjbox`) contains documentation and software for **NomadPi**, a Raspberry Pi 4 running DietPi configured for Nomad Karaoke live events (video playback, AV equipment connection, and karaoke show management).

### Repository Contents
- **docs/ARCHITECTURE.md** - System architecture, API reference, design decisions
- **docs/DEVELOPMENT.md** - Local setup, dev workflow, running tests
- **docs/TESTING.md** - Test conventions, coverage targets, fixtures
- **docs/archive/NOMADPI-DETAILS.md** - Complete device configuration reference (hardware, network, audio, display, services)
- **docs/archive/NETWORK-CONFIG-BACKUP.md** - Tailscale VPN and Cloudflare tunnel configuration backup
- **kj-controller/** - KJ Remote Controller web app for managing karaoke playback

### 📋 Documentation Maintenance - CRITICAL

**IMPORTANT:** When working with the NomadPi device, you MUST maintain comprehensive documentation:

1. **Always update docs/archive/NOMADPI-DETAILS.md** whenever you:
   - Configure or change system settings
   - Install new software or services
   - Troubleshoot and resolve issues
   - Learn new information about how the device works
   - Modify configuration files
   - Enable/disable services
   - Change network or hardware settings

2. **What to document:**
   - Exact configuration changes made (with file paths)
   - Commands used to make changes
   - Before/after states
   - Troubleshooting steps that worked
   - Any gotchas or important notes discovered
   - Add entries to the Change Log section with dates

3. **Documentation format:**
   - Add new configuration sections for new features/services
   - Update existing sections when changing configurations
   - Add troubleshooting entries for issues encountered
   - Include complete command examples
   - Update the Configuration File Locations section
   - Add dated entries to the Change Log

4. **Why this matters:**
   - This device is a physical hardware setup that's difficult to replicate
   - Configuration knowledge would be lost between sessions without documentation
   - Future troubleshooting depends on understanding current state
   - The documentation serves as the single source of truth for device configuration

### 🎯 Goal

The `docs/archive/NOMADPI-DETAILS.md` file should be a **complete, comprehensive reference** that contains everything needed to:
- Understand the current device configuration
- Troubleshoot any issues
- Replicate the setup if needed
- Make informed changes to the system

### 📝 Documentation Standards

- Use clear section headers
- Include complete command examples with SSH prefix
- Document both the "what" and the "why"
- Add dates to the Change Log for all modifications
- Keep the "Last Updated" date current
- Use consistent formatting with existing sections

---

**Remember:** If you configure it, document it. If you learn it, document it. If you fix it, document it.
