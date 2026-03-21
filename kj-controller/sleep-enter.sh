#!/usr/bin/env bash
# sleep-enter.sh — Put NomadPC into low-power sleep mode.
#
# Usage: sleep-enter.sh <state-file>
#
# Captures pre-sleep state to <state-file> (JSON), then stops services,
# blanks display, unmounts SSD, and switches to power-saver profile.
#
# Designed to be called by kj-controller's SleepManager, but can also
# be run manually via SSH for debugging.

set -euo pipefail

STATE_FILE="${1:?Usage: sleep-enter.sh <state-file>}"
FLAG_FILE="/tmp/kj-sleep-mode"
SSD_MOUNT="/media/nomad/Nomad4TBOne"
SSD_DEVICE="/dev/sda"
USB_POWER_PATH="/sys/bus/usb/devices/2-1/power/control"

log() { echo "[sleep-enter] $(date '+%H:%M:%S') $*"; }

# --- Capture pre-sleep state ---
log "Capturing pre-sleep state..."

is_active() {
    systemctl is-active --quiet "$1" 2>/dev/null && echo "true" || echo "false"
}

autodeploy_active=$(is_active kj-autodeploy)
overlay_active=$(is_active overlay-display)
rotation_active=$(is_active rotation-display)
x11vnc_active=$(is_active x11vnc)
cups_active=$(is_active cups)
bluetooth_active=$(is_active bluetooth)
modemmanager_active=$(is_active ModemManager)
avahi_active=$(is_active avahi-daemon)
dropbox_running="false"
pgrep -f "dropbox" > /dev/null 2>&1 && dropbox_running="true"
ssd_mounted="false"
mountpoint -q "$SSD_MOUNT" 2>/dev/null && ssd_mounted="true"
power_profile=$(powerprofilesctl get 2>/dev/null || echo "balanced")

cat > "$STATE_FILE" <<ENDJSON
{
  "entered_at": "$(date -Iseconds)",
  "autodeploy_was_active": $autodeploy_active,
  "overlay_was_active": $overlay_active,
  "rotation_was_active": $rotation_active,
  "x11vnc_was_active": $x11vnc_active,
  "cups_was_active": $cups_active,
  "bluetooth_was_active": $bluetooth_active,
  "modemmanager_was_active": $modemmanager_active,
  "avahi_was_active": $avahi_active,
  "dropbox_was_running": $dropbox_running,
  "ssd_was_mounted": $ssd_mounted,
  "previous_power_profile": "$power_profile"
}
ENDJSON

log "State saved to $STATE_FILE"

# --- Stop karaoke services ---
# Note: VLC is stopped by SleepManager (Python) before this script runs.
# Websockify is also stopped by SleepManager.

for svc in overlay-display rotation-display; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        log "Stopping $svc..."
        sudo systemctl stop "$svc"
    fi
done

# --- Reduce display stack ---
if systemctl is-active --quiet x11vnc 2>/dev/null; then
    log "Stopping x11vnc..."
    sudo systemctl stop x11vnc
fi

log "Blanking display via DPMS..."
xset -display :0 dpms force off 2>/dev/null || log "DPMS blank failed (no display?)"

# --- Stop unnecessary system services ---
for svc in cups cups-browsed bluetooth ModemManager avahi-daemon; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        log "Stopping $svc..."
        sudo systemctl stop "$svc"
    fi
done

# --- Stop Dropbox ---
if pgrep -f "dropbox" > /dev/null 2>&1; then
    log "Stopping Dropbox..."
    pkill -f "dropbox" || true
fi

# --- Unmount and power-down USB SSD ---
if mountpoint -q "$SSD_MOUNT" 2>/dev/null; then
    log "Unmounting SSD ($SSD_MOUNT)..."
    sudo umount "$SSD_MOUNT"
fi

if [ -b "$SSD_DEVICE" ]; then
    log "Spinning down SSD ($SSD_DEVICE)..."
    sudo hdparm -Y "$SSD_DEVICE" 2>/dev/null || log "hdparm spindown failed"
fi

if [ -f "$USB_POWER_PATH" ]; then
    log "Setting USB auto-suspend..."
    echo "auto" | sudo tee "$USB_POWER_PATH" > /dev/null
fi

# --- Reduce power profile ---
log "Switching to power-saver profile..."
sudo powerprofilesctl set power-saver 2>/dev/null || log "powerprofilesctl failed"

# --- Disable auto-deploy ---
if systemctl is-active --quiet kj-autodeploy 2>/dev/null; then
    log "Stopping kj-autodeploy..."
    sudo systemctl stop kj-autodeploy
fi

# --- Write sleep flag ---
touch "$FLAG_FILE"
log "Sleep mode active. Flag written to $FLAG_FILE"
