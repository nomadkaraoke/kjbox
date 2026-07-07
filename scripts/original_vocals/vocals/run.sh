#!/usr/bin/env bash
# One command to run OR resume the vocals separation, keeping the Mac awake while
# it works. Ctrl-C to pause; just run this again to resume where it left off
# (progress = vocals files already on the device, so nothing is ever lost).
#
#   bash scripts/original_vocals/vocals/run.sh
#
# Runs ~90-120s per track (~1.7 days for all 1,372). If the Mac sleeps it pauses;
# re-run to continue. Watch progress in another terminal with: bash status.sh
cd "$(dirname "${BASH_SOURCE[0]}")"
exec caffeinate -i -s bash separate_vocals.sh
