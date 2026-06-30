#!/bin/bash
# Pause the run for a live event. Graceful: the in-flight file simply re-checks on
# resume, and every completed file is already durably in the JSONL — no data lost.
sudo systemctl stop playability-batch
echo "PAUSED — playability-batch stopped. Nothing lost; resume after the event with:"
echo "  /opt/nomad/playability-run/start.sh"
