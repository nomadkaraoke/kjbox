#!/bin/bash
# Playability run — periodic system/thermal/progress logger + thermal hard-stop.
# Runs as its own systemd unit (playability-monitor). Lightweight: one sample / 5 min.
set -u
WORK=/opt/nomad/playability-run
LOG="$WORK/monitor.log"
THERM_STOP=92   # package °C: hard-stop the batch above this (safety net; resumable)

while true; do
  TS=$(date '+%F %T')
  PKG=$(sensors 2>/dev/null | grep -m1 'Package id 0' | grep -oE '\+[0-9.]+' | head -1 | tr -d '+')
  LOAD=$(cut -d' ' -f1-3 /proc/loadavg)
  IDONE=$(wc -l < "$WORK/internal_results.jsonl" 2>/dev/null || echo 0)
  SDONE=$(wc -l < "$WORK/ssd_results.jsonl" 2>/dev/null || echo 0)
  if systemctl is-active --quiet playability-batch; then BSTATE=run; else BSTATE=stopped; fi
  echo "$TS temp=${PKG:-?}C load=$LOAD internal=$IDONE ssd=$SDONE batch=$BSTATE" >> "$LOG"

  # Thermal safety net — stop the batch (not the device) if we ever run hot.
  PKGi=${PKG%.*}
  if [ -n "${PKGi:-}" ] && [ "${PKGi:-0}" -ge "$THERM_STOP" ] 2>/dev/null; then
    echo "$TS !! THERMAL STOP: ${PKG}C >= ${THERM_STOP}C — stopping playability-batch" >> "$LOG"
    systemctl stop playability-batch 2>/dev/null
  fi
  sleep 300
done
