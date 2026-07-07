#!/usr/bin/env bash
# Generate the isolated-vocals guide dataset for all NOMAD tracks on this Mac
# (Apple Silicon / MPS) and sync the results to the KJ device.
#
# For each input in the device's NOMAD-audio that doesn't yet have a vocals file:
#   pull input -> separate (single vocal model) -> measure vocal energy ->
#   push the (vocals) stem to the device's NOMAD-vocals/ -> record diagnostics.
#
# RESUMABLE: skips any track whose vocals already exist on the device, so you can
# Ctrl-C anytime and just re-run this to continue. Progress is the presence of
# files on the device — nothing to corrupt.
#
# DIAGNOSTICS: appends per-track vocal loudness + size to vocals_diagnostics.csv,
# which flag_weak_vocals.py then uses to flag tracks whose "vocals" are nearly
# silent — a sign the wrong input file (an already-separated instrumental) was
# picked and needs re-selection.
#
# Usage:  bash separate_vocals.sh            # run / resume
#         (wrap with `caffeinate -i` to keep the Mac awake for an unattended run)
set -u

SEP="${SEP:-/Users/andrew/miniforge3/envs/nomadkaraoke/bin/audio-separator}"
MODEL="${MODEL:-vocals_mel_band_roformer.ckpt}"
HOST="${HOST:-nomadpctunnel}"
SRC="${SRC:-/opt/nomad/downloads/NOMAD-audio}"
DST="${DST:-/opt/nomad/downloads/NOMAD-vocals}"
WORK="${WORK:-/tmp/ov_sep_work}"
CTL="${CTL:-/tmp/ov_sep_ssh.sock}"
LOG="${LOG:-$HOME/nomad-vocals-separation.log}"
DIAG="${DIAG:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vocals_diagnostics.csv}"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

mkdir -p "$WORK"
# one reused SSH connection for fast pulls/pushes
if ! ssh -S "$CTL" -O check "$HOST" >/dev/null 2>&1; then
  ssh -M -S "$CTL" -o ControlPersist=2h -o ServerAliveInterval=30 -fN "$HOST"
fi
SSH=(ssh -S "$CTL"); SCP=(scp -o ControlPath="$CTL")
"${SSH[@]}" "$HOST" "mkdir -p '$DST'"

[ -f "$DIAG" ] || echo "brand,dest,input_bytes,vocals_bytes,vocals_max_db,vocals_mean_db,dur_s,seconds" > "$DIAG"

"${SSH[@]}" "$HOST" "ls -1 '$SRC'" | sort > "$WORK/src.txt"
"${SSH[@]}" "$HOST" "ls -1 '$DST' 2>/dev/null" | sort > "$WORK/done.txt"
total=$(wc -l < "$WORK/src.txt" | tr -d ' ')
already=$(wc -l < "$WORK/done.txt" | tr -d ' ')
log "=== separation run start: $total inputs, $already already have vocals ==="
MAXN="${MAXN:-0}"   # >0 = stop after this many (for testing)

meandb() { grep -iE "$2" "$1" 2>/dev/null | grep -oE "[-0-9.]+ dB" | head -1 | grep -oE "[-0-9.]+"; }

n=0
while IFS= read -r fn; do
  [ -z "$fn" ] && continue
  base="${fn%.*}"                 # "NOMAD-#### - Artist - Title"
  brand="${base%% *}"            # "NOMAD-####"
  out="$base.flac"               # vocals dest filename
  grep -Fxq "$out" "$WORK/done.txt" 2>/dev/null && continue
  [ "$MAXN" -gt 0 ] && [ "$n" -ge "$MAXN" ] && { log "MAXN=$MAXN reached, stopping"; break; }

  t0=$(date +%s)
  log "separating $base"
  rm -rf "$WORK/in" "$WORK/out"; mkdir -p "$WORK/in" "$WORK/out"
  if ! "${SCP[@]}" "$HOST:$SRC/$fn" "$WORK/in/$fn" >>"$LOG" 2>&1; then log "PULL FAIL $fn"; continue; fi
  in_bytes=$(stat -f%z "$WORK/in/$fn" 2>/dev/null || echo 0)

  "$SEP" "$WORK/in/$fn" --model_filename "$MODEL" --output_dir "$WORK/out" --output_format flac >>"$LOG" 2>&1
  voc=$(ls "$WORK/out/"*"(vocals)"*.flac 2>/dev/null | head -1)
  if [ -z "$voc" ]; then log "NOVOX $fn (separation produced no vocals stem)"; continue; fi

  # diagnostics: vocal loudness (volumedetect logs at INFO — do NOT use -v error) + size
  ffmpeg -hide_banner -i "$voc" -af volumedetect -f null /dev/null 2>"$WORK/vd.txt"
  vmax=$(meandb "$WORK/vd.txt" "max_volume"); vmean=$(meandb "$WORK/vd.txt" "mean_volume")
  vbytes=$(stat -f%z "$voc" 2>/dev/null || echo 0)
  dur=$(ffprobe -v error -show_entries format=duration -of default=nokey=1:noprint_wrappers=1 "$voc" 2>/dev/null)

  if "${SCP[@]}" "$voc" "$HOST:$DST/$out" >>"$LOG" 2>&1; then
    secs=$(( $(date +%s) - t0 )); n=$((n+1))
    echo "$brand,\"$out\",$in_bytes,$vbytes,${vmax:-},${vmean:-},${dur:-},$secs" >> "$DIAG"
    log "OK $out  ($n this run)  vocals_max=${vmax:-?}dB size=$((vbytes/1024))KB  ${secs}s"
  else
    log "PUSH FAIL $out"
  fi
done < "$WORK/src.txt"

rm -rf "$WORK/in" "$WORK/out"
done_ct=$("${SSH[@]}" "$HOST" "ls -1 '$DST' 2>/dev/null | wc -l" | tr -d ' ')
log "=== run complete: $n separated this pass; $done_ct / $total total on device ==="
