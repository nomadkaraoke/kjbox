#!/usr/bin/env bash
# sleep-exit.sh — Wake NomadPC from sleep mode.
#
# Usage: sleep-exit.sh <state-file>
#
# Reads pre-sleep state from <state-file> (JSON) and restores only
# the services that were running before sleep.
#
# Designed to be called by kj-controller's SleepManager, but can also
# be run manually via SSH for debugging.

set -euo pipefail

STATE_FILE="${1:?Usage: sleep-exit.sh <state-file>}"
FLAG_FILE="/tmp/kj-sleep-mode"
SSD_MOUNT="/media/nomad/Nomad4TBOne"
USB_POWER_PATH="/sys/bus/usb/devices/2-1/power/control"

log() { echo "[sleep-exit] $(date '+%H:%M:%S') $*"; }

# --- Read pre-sleep state ---
if [ ! -f "$STATE_FILE" ]; then
    log "WARNING: No state file at $STATE_FILE — restoring defaults"
    # Fallback: assume everything was running
    autodeploy_was_active="true"
    overlay_was_active="true"
    rotation_was_active="true"
    x11vnc_was_active="true"
    cups_was_active="true"
    bluetooth_was_active="true"
    avahi_was_active="true"
    dropbox_was_running="true"
    ssd_was_mounted="true"
    previous_power_profile="balanced"
else
    # Parse JSON with python (available on the system)
    eval "$(python3 -c "
import json, sys
with open('$STATE_FILE') as f:
    d = json.load(f)
for k, v in d.items():
    if isinstance(v, bool):
        print(f'{k}={str(v).lower()}')
    else:
        print(f'{k}=\"{v}\"')
")"
fi

log "Waking from sleep (entered at: ${entered_at:-unknown})..."

# --- Remove sleep flag ---
rm -f "$FLAG_FILE"
log "Sleep flag removed"

# --- Restore power profile ---
profile="${previous_power_profile:-balanced}"
log "Restoring power profile to $profile..."
sudo powerprofilesctl set "$profile" 2>/dev/null || log "powerprofilesctl failed"

# --- Re-enable auto-deploy ---
if [ "${autodeploy_was_active:-false}" = "true" ]; then
    log "Starting kj-autodeploy..."
    sudo systemctl start kj-autodeploy
fi

# --- Power-up and mount USB SSD ---
if [ -f "$USB_POWER_PATH" ]; then
    log "Setting USB power to 'on'..."
    echo "on" | sudo tee "$USB_POWER_PATH" > /dev/null
fi

if [ "${ssd_was_mounted:-false}" = "true" ]; then
    log "Waiting for SSD to wake..."
    sleep 2
    if ! mountpoint -q "$SSD_MOUNT" 2>/dev/null; then
        log "Mounting SSD ($SSD_MOUNT)..."
        sudo mount "$SSD_MOUNT" 2>/dev/null || log "WARNING: SSD mount failed"
    else
        log "SSD already mounted"
    fi
fi

# --- Restore display ---
log "Unblanking display..."
xset -display :0 dpms force on 2>/dev/null || log "DPMS unblank failed (no display?)"

if [ "${x11vnc_was_active:-false}" = "true" ]; then
    log "Starting x11vnc..."
    sudo systemctl start x11vnc
fi

# --- Start Dropbox ---
if [ "${dropbox_was_running:-false}" = "true" ]; then
    log "Starting Dropbox..."
    # Run as the nomad user, detached
    sudo -u nomad bash -c 'nohup /home/nomad/.dropbox-dist/dropboxd > /dev/null 2>&1 &' || log "Dropbox start failed"
fi

# --- Restart system services ---
for svc in cups bluetooth avahi-daemon; do
    var="${svc//-/_}_was_active"
    # Normalize service name for variable lookup
    case "$svc" in
        avahi-daemon) was_active="${avahi_was_active:-false}" ;;
        cups)         was_active="${cups_was_active:-false}" ;;
        bluetooth)    was_active="${bluetooth_was_active:-false}" ;;
        *)            was_active="false" ;;
    esac
    if [ "$was_active" = "true" ]; then
        log "Starting $svc..."
        sudo systemctl start "$svc"
    fi
done

# --- Start karaoke services ---
# Note: VLC is restarted by SleepManager (Python) after this script completes.

if [ "${overlay_was_active:-false}" = "true" ]; then
    log "Starting overlay-display..."
    sudo systemctl start overlay-display
fi

if [ "${rotation_was_active:-false}" = "true" ]; then
    log "Starting rotation-display..."
    sudo systemctl start rotation-display
fi

# --- Clean up state file ---
rm -f "$STATE_FILE"

log "Wake complete. All services restored."
