#!/bin/bash
# Refresh the CSV+MD reports (incl. the lists of unplayable/corrupt files to review
# and delete) from whatever each JSONL holds so far. Safe to run anytime, mid-run.
set -u
WORK=/opt/nomad/playability-run
PY=/opt/nomad/kjbox/kj-controller/venv/bin/python
cd /opt/nomad/kjbox/kj-controller || exit 1
"$PY" - "$WORK" <<'PY'
import os, sys
sys.path.insert(0, ".")
from playability_batch import write_reports
work = sys.argv[1]
for tag in ("internal", "ssd"):
    j = os.path.join(work, tag + "_results.jsonl")
    if os.path.isfile(j):
        agg = write_reports(j, os.path.join(work, tag + "_report.csv"),
                            os.path.join(work, tag + "_report.md"))
        print(f"{tag}: total {agg['total']} | OK {len(agg['ok'])} | "
              f"unplayable {len(agg['unplayable'])} | "
              f"vlc-not-mpv {len(agg['vlc_not_mpv'])} | cdg-problems {len(agg['cdg_problems'])}")
PY
echo "Reports: $WORK/{internal,ssd}_report.{csv,md}"
