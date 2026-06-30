#!/bin/bash
# Full-library playability run — Phase A (internal, deep render) then Phase B (4TB
# SSD archive, gentle integrity-only). Both resumable via their JSONL manifests, so
# a stop/reboot/thermal-pause just picks up where it left off on the next start.
set -u
WORK=/opt/nomad/playability-run
PY=/opt/nomad/kjbox/kj-controller/venv/bin/python
CODE=/opt/nomad/kjbox/kj-controller
cd "$CODE" || exit 1
ts(){ date '+%F %T'; }

echo "[$(ts)] PHASE A: internal storage — deep render (catches corrupt + black-screen)"
"$PY" playability_batch.py \
  --roots /opt/nomad/YTDownloads /opt/nomad/MP4-720p \
  --jsonl "$WORK/internal_results.jsonl" \
  --csv "$WORK/internal_report.csv" --md "$WORK/internal_report.md" \
  --depth deep --throttle 0.3 >> "$WORK/batch.log" 2>&1
echo "[$(ts)] PHASE A complete (report: $WORK/internal_report.md)"

echo "[$(ts)] PHASE B: 4TB SSD archive — integrity sweep, no render (gentle on the SSD)"
"$PY" "$WORK/ssd_runner.py" \
  /media/nomad/Nomad4TBOne/HyperMule \
  /media/nomad/Nomad4TBOne/NomadKaraoke-2024.11.02 \
  >> "$WORK/batch.log" 2>&1
echo "[$(ts)] PHASE B complete — FULL LIBRARY DONE."
