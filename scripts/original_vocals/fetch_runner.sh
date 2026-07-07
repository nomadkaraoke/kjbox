#!/usr/bin/env bash
# Resumable parallel fetch of original-mix audio from Dropbox onto the KJ device.
#
# Reads a TSV (brand<TAB>src_remote_path<TAB>dst_filename) produced by classify.py
# and copies each file to $DEST, skipping any that already exist so it can be
# re-run safely. Each download goes to a .part file and is renamed on success, so
# an interrupted run never leaves a truncated file that looks complete.
#
# Usage: fetch_runner.sh <fetch_plan.tsv> <dest_dir> [parallelism]
set -u
PLAN="${1:?usage: fetch_runner.sh <plan.tsv> <dest> [parallelism]}"
DEST="${2:?dest dir required}"
PAR="${3:-8}"
RCLONE="${RCLONE:-$HOME/bin/rclone}"

mkdir -p "$DEST"
export RCLONE DEST

fetch_one() {
  local line="$1" brand src dst out
  IFS=$'\t' read -r brand src dst <<<"$line"
  [ -z "${dst:-}" ] && { echo "BADROW $line"; return 0; }
  out="$DEST/$dst"
  if [ -f "$out" ]; then echo "SKIP $brand"; return 0; fi
  if "$RCLONE" copyto "$src" "$out.part" \
        --low-level-retries 10 --retries 5 --timeout 300s --checkers 1 --transfers 1 \
        >/dev/null 2>>"$DEST/.fetch_errors.log"; then
    mv -f "$out.part" "$out"
    echo "OK $brand"
  else
    rm -f "$out.part"
    echo "FAIL $brand $dst"
  fi
}
export -f fetch_one

echo "=== fetch start $(date -u +%FT%TZ)  rows=$(wc -l <"$PLAN")  par=$PAR  dest=$DEST ==="
# -d '\n' so tabs/spaces/parens in the row are preserved as a single argument
xargs -a "$PLAN" -d '\n' -P "$PAR" -I {} bash -c 'fetch_one "$@"' _ {}
echo "=== fetch done  $(date -u +%FT%TZ) ==="
