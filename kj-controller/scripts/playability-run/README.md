# playability-run

Operational harness for the **full-library playability sweep** — runs on the NomadPC
device to find corrupt / unplayable files across the whole karaoke library so they can be
reviewed and deleted. Built on the playability checker (kjbox v0.40.0, PR #112).

**Full runbook (read this):** [`docs/PLAYABILITY-FULL-LIBRARY-RUN.md`](../../../docs/PLAYABILITY-FULL-LIBRARY-RUN.md)
— what it does, the two phases, ETAs, how to check/pause/resume, how to read results, the
critical "run as `nomad` not root" gotcha, and how to (re)install from scratch.

These scripts are deployed to `/opt/nomad/playability-run/` on the device. Quick reference
(after `ssh nomadpctunnel`):

```bash
/opt/nomad/playability-run/progress.sh   # % + ETA + temp/load
/opt/nomad/playability-run/pause.sh      # pause before a live event
/opt/nomad/playability-run/start.sh      # resume (and how to restart after a reboot)
/opt/nomad/playability-run/report.sh     # CSV/MD of flagged files
```
