#!/bin/bash
# Playability run — on-demand progress + ETA report. Safe to run anytime.
set -u
WORK=/opt/nomad/playability-run
IT=$(cat "$WORK/totals.internal" 2>/dev/null || echo 0)
ST=$(cat "$WORK/totals.ssd" 2>/dev/null || echo 0)
lines(){ if [ -f "$1" ]; then wc -l < "$1"; else echo 0; fi; }
flagged(){ if [ -f "$1" ]; then c=$(grep -c '"overall_ok": false' "$1" 2>/dev/null); echo "${c:-0}"; else echo 0; fi; }
ID=$(lines "$WORK/internal_results.jsonl")
SD=$(lines "$WORK/ssd_results.jsonl")
TOT=$((IT + ST)); DONE=$((ID + SD)); REMAIN=$((TOT - DONE))
pct(){ awk -v d="$1" -v t="$2" 'BEGIN{if(t>0)printf "%.1f%%", d*100/t; else printf "?"}'; }

# Current throughput (files/sec) from the last ~30 min of monitor samples.
RATE=$(tail -7 "$WORK/monitor.log" 2>/dev/null | awk '
  {d=0; for(i=1;i<=NF;i++){if($i ~ /^internal=/ || $i ~ /^ssd=/){split($i,x,"="); d+=x[2]}} a[NR]=d; n=NR}
  END{if(n>=3){dd=a[n]-a[1]; ss=(n-1)*300; if(ss>0 && dd>0) printf "%.6f", dd/ss; else print 0} else print 0}')

if systemctl is-active --quiet playability-batch; then BSTATE="RUNNING"; else BSTATE="PAUSED/STOPPED"; fi

echo "=== Playability run @ $(date '+%F %T')   [batch: $BSTATE] ==="
echo "Phase A internal : $ID / $IT   ($(pct $ID $IT))"
echo "Phase B 4TB SSD  : $SD / $ST   ($(pct $SD $ST))"
echo "Overall          : $DONE / $TOT   ($(pct $DONE $TOT))"
FB_I=$(flagged "$WORK/internal_results.jsonl"); FB_S=$(flagged "$WORK/ssd_results.jsonl")
BAD=$((FB_I + FB_S))
echo "Flagged so far   : $BAD  (failed = corrupt / unplayable, to review & delete)"

if awk "BEGIN{exit !($RATE > 0)}" 2>/dev/null; then
  RPM=$(awk -v r="$RATE" 'BEGIN{printf "%.1f", r*60}')
  ETA=$(awk -v rem="$REMAIN" -v r="$RATE" 'BEGIN{s=rem/r; d=int(s/86400); h=int((s%86400)/3600); m=int((s%3600)/60); printf "%dd %dh %dm", d, h, m}')
  echo "Throughput       : ${RPM} files/min    ETA for remaining: ~$ETA (at current rate)"
else
  echo "Throughput       : (idle/paused — no recent progress to extrapolate)"
fi
echo "--- recent system samples (temp / load / counts) ---"
tail -3 "$WORK/monitor.log" 2>/dev/null || echo "(no samples yet)"
