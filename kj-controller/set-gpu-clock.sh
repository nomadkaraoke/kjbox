#!/usr/bin/env bash
# set-gpu-clock.sh pin|unpin
#
# Pin the Intel iGPU (i915) at its hardware max clock, or restore the default
# floor. Used by the KJ Controller Performance monitor's experimental "GPU max
# clock" A/B control to test whether sustained-load frame drops are caused by the
# GPU declining to boost to its ceiling (observed: act stuck at 1000 MHz while
# max is 1200 MHz under stacked load).
#
# Requires root (sysfs write) — invoked via a NOPASSWD sudoers entry:
#   nomad ALL=(root) NOPASSWD: /opt/nomad/kjbox/kj-controller/set-gpu-clock.sh
#
# NOT persisted across reboot by design — an experiment must not silently become
# permanent config.
set -euo pipefail

GT=$(ls -d /sys/class/drm/card*/gt/gt0 2>/dev/null | head -1)
if [ -z "$GT" ]; then
    echo "no i915 GT sysfs found" >&2
    exit 1
fi

MAX_HW=$(cat "$GT/rps_max_freq_mhz" 2>/dev/null || echo "")
FLOOR=$(cat "$GT/rps_RPn_freq_mhz" 2>/dev/null || echo "")

case "${1:-}" in
    pin)
        [ -n "$MAX_HW" ] || { echo "cannot read max freq (rps_max_freq_mhz)" >&2; exit 1; }
        echo "$MAX_HW" > "$GT/rps_min_freq_mhz"
        echo "pinned: rps_min_freq_mhz -> ${MAX_HW} MHz"
        ;;
    unpin)
        # Don't guess the floor — restoring the wrong frequency is worse than failing.
        [ -n "$FLOOR" ] || { echo "cannot read hardware floor (rps_RPn_freq_mhz)" >&2; exit 1; }
        echo "$FLOOR" > "$GT/rps_min_freq_mhz"
        echo "unpinned: rps_min_freq_mhz -> ${FLOOR} MHz"
        ;;
    *)
        echo "usage: $0 pin|unpin" >&2
        exit 2
        ;;
esac
