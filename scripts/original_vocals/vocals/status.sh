#!/usr/bin/env bash
# Show vocals-separation progress + the current weak-vocals flag count.
HOST="${HOST:-nomadpctunnel}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
done=$(ssh "$HOST" "ls -1 /opt/nomad/downloads/NOMAD-vocals 2>/dev/null | wc -l" 2>/dev/null | tr -d ' ')
total=$(ssh "$HOST" "ls -1 /opt/nomad/downloads/NOMAD-audio 2>/dev/null | wc -l" 2>/dev/null | tr -d ' ')
echo "vocals separated: ${done:-?} / ${total:-?}"
if [ -f "$here/vocals_diagnostics.csv" ]; then
  echo "diagnostics rows: $(( $(wc -l < "$here/vocals_diagnostics.csv") - 1 ))"
fi
running=$(pgrep -f separate_vocals.sh | wc -l | tr -d ' ')
echo "separation running: $([ "$running" -gt 0 ] && echo yes || echo 'no (run.sh to resume)')"
