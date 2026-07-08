#!/bin/bash
# Run ON kjbox (NomadPC). Pads each raw NOMAD-vocals guide with 5s of leading +
# 5s of trailing silence into NOMAD-vocals-padded, so the guide aligns with the
# NOMAD master's ~5s title-card intro. Fixed 5s (MVP). Resumable + idempotent.
#
#   ssh nomadpctunnel 'bash /opt/nomad/kjbox-scripts/pad_vocals.sh'
set -u
RAW=/opt/nomad/downloads/NOMAD-vocals
OUT=/opt/nomad/downloads/NOMAD-vocals-padded
mkdir -p "$OUT"
shopt -s nullglob
n=0; ok=0; skip=0; fail=0
for f in "$RAW"/*; do
    base=$(basename "$f")
    case "$base" in *.part) continue;; esac
    stem="${base%.*}"
    dest="$OUT/$stem.flac"
    n=$((n+1))
    if [ -f "$dest" ]; then skip=$((skip+1)); continue; fi   # resumable
    # -f flac forces the muxer (the .part extension would otherwise confuse ffmpeg)
    if ffmpeg -y -loglevel error -i "$f" \
        -af "adelay=5000:all=1,apad=pad_dur=5" -c:a flac -f flac "$dest.part" </dev/null; then
        mv "$dest.part" "$dest"; ok=$((ok+1))
    else
        rm -f "$dest.part"; fail=$((fail+1)); echo "FAIL: $base"
    fi
    [ $((n % 100)) -eq 0 ] && echo "  ...processed $n"
done
echo "DONE pad: total=$n padded=$ok skipped=$skip fail=$fail -> $OUT"
