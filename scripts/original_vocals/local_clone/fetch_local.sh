#!/usr/bin/env bash
# Fetch original-mix audio to the KJ device via the Mac's *local Dropbox clone*,
# for when the rclone/API path is unavailable (the andrewdropboxfull app lacks
# the files.content.read scope). Per file:
#
#   materialize (force Dropbox to download the online-only placeholder) ->
#   ffprobe-validate -> scp to the device.
#
# Why "materialize": Dropbox online-only files are 0-byte placeholders
# (com.dropbox.placeholder xattr). A plain cat/cp does NOT fault them in — only an
# NSFileCoordinator coordinated read does. `materialize` (compiled from
# materialize.swift) performs that read and blocks until the file is fully local.
#
# Disk note: Dropbox legacy Smart Sync cannot be programmatically evicted to
# reclaim space (see evict.swift), so this stops when free space on / drops below
# FLOOR_GB. To pull the whole catalog you either free space between runs (Finder
# -> "Make Online-Only") or use the diskless rclone path once the Dropbox scope is
# enabled.
#
# Speed: an SSH ControlMaster is reused so each scp rides one Cloudflare tunnel
# instead of re-running `cloudflared access ssh` per file, and up to PAR files are
# processed concurrently.
#
# Usage: fetch_local.sh <plan.tsv> [parallelism]
#   plan.tsv rows: <brand>\t<local_dropbox_path>\t<dest_filename>
#   (generate with make_local_plan.py from the classifier manifest)
set -u
PLAN="${1:?usage: fetch_local.sh <plan.tsv> [parallelism]}"
PAR="${2:-5}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DEST_HOST="${DEST_HOST:-nomadpctunnel}"
DEST_DIR="${DEST_DIR:-/opt/nomad/downloads/NOMAD-audio}"
MAT="${MATERIALIZE_BIN:-$SCRIPT_DIR/materialize}"
CTL="${SSH_CTL:-/tmp/ov_ssh.sock}"
LOG="${FETCH_LOG:-/tmp/ov_fetch.log}"
FLOOR_KB=$(( ${FLOOR_GB:-5} * 1024 * 1024 ))
ONDEV="/tmp/ov_on_device.txt"
export DEST_HOST DEST_DIR MAT CTL FLOOR_KB ONDEV

[ -x "$MAT" ] || { echo "ERROR: materialize binary not found at $MAT (build: swiftc -O materialize.swift -o materialize)"; exit 1; }

# One persistent multiplexed SSH connection reused by every scp.
if ! ssh -S "$CTL" -O check "$DEST_HOST" >/dev/null 2>&1; then
  ssh -M -S "$CTL" -o ControlPersist=30m -o ServerAliveInterval=30 -fN "$DEST_HOST"
fi
ssh -S "$CTL" "$DEST_HOST" "ls -1 '$DEST_DIR' 2>/dev/null" > "$ONDEV" 2>/dev/null

worker() {
  IFS=$'\t' read -r brand src dst <<<"$1"
  [ -z "${dst:-}" ] && return 0
  grep -Fxq "$dst" "$ONDEV" 2>/dev/null && { echo "SKIP $brand"; return 0; }
  [ "$(df -k / | tail -1 | awk '{print $4}')" -lt "$FLOOR_KB" ] && { echo "FLOOR $brand"; return 0; }
  [ -e "$src" ] || { echo "MISSING $brand :: $src"; return 0; }
  "$MAT" "$src" >/dev/null 2>&1
  local dur; dur=$(ffprobe -v error -show_entries format=duration -of default=nokey=1:noprint_wrappers=1 "$src" 2>/dev/null)
  if [ -z "$dur" ] || ! awk "BEGIN{exit !($dur>0)}" </dev/null 2>/dev/null; then
    echo "INVALID $brand dur='$dur'"; return 0
  fi
  if scp -q -o ControlPath="$CTL" "$src" "$DEST_HOST:$DEST_DIR/$dst" 2>/dev/null; then
    echo "OK $brand"
  else
    echo "FAIL $brand :: $dst"
  fi
}
export -f worker

echo "=== local fetch start $(date -u +%FT%TZ) par=$PAR floor=${FLOOR_GB:-5}GB ===" | tee -a "$LOG"
running=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  { worker "$line" | tee -a "$LOG"; } &
  running=$((running+1))
  if [ "$running" -ge "$PAR" ]; then wait; running=0; fi
done < "$PLAN"
wait
echo "=== done $(date -u +%FT%TZ) ok=$(grep -c '^OK' "$LOG") ===" | tee -a "$LOG"
