#!/bin/bash
# Start OR RESUME the playability run. Idempotent — resumes from the JSONL manifest,
# so it's safe to re-run after a pause, a reboot, or a thermal stop.
set -u
WORK=/opt/nomad/playability-run
sudo systemctl reset-failed playability-batch playability-monitor 2>/dev/null

# Batch unit: gentle by design — idle I/O class (yields the SSD to everything else),
# low CPU weight + 2-core quota (stays cool, and Nice 19 yields to live VLC during a
# show even if you forget to pause), memory-bounded. KillMode=control-group (default)
# means `systemctl stop` cleanly SIGTERMs the whole phase chain.
#
# MUST run as User=nomad (NOT root): VLC refuses to run as root ("cannot be run by
# non-trusted users"), which would falsely flag every video. Mirror kj-controller's
# env (HOME + XDG_RUNTIME_DIR) so VLC/mpv behave the same as the live service.
# Deliberately do NOT set DISPLAY=:0 — the render engine spawns its own off-screen
# Xvfb on a free display, so nothing ever touches the live show display.
if systemctl is-active --quiet playability-batch; then
  echo "batch already running."
else
  sudo systemd-run --unit=playability-batch --collect \
    -p User=nomad -p Group=nomad \
    -p Environment=HOME=/home/nomad \
    -p Environment=XDG_RUNTIME_DIR=/run/user/1000 \
    -p Nice=19 -p IOSchedulingClass=idle -p IOSchedulingPriority=7 \
    -p CPUWeight=20 -p CPUQuota=200% -p MemoryMax=2G \
    /opt/nomad/playability-run/run_all.sh
  echo "batch started as nomad (resumes from manifest if interrupted)."
fi

# Monitor unit: always-on lightweight logger, independent of pause/resume.
if ! systemctl is-active --quiet playability-monitor; then
  sudo systemd-run --unit=playability-monitor --collect -p Nice=19 \
    /opt/nomad/playability-run/monitor.sh
  echo "monitor started."
fi

echo "progress: $WORK/progress.sh   pause: $WORK/pause.sh   live log: journalctl -u playability-batch -f"
