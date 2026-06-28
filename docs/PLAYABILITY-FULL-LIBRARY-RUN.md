# Full-Library Playability Run (operational runbook)

> **For a future Claude session:** the karaoke library is being swept end-to-end by the
> playability checker to find corrupt / unplayable files so they can be reviewed and
> deleted. The run executes **on the physical NomadPC device**, not in CI or the cloud.
> This doc is everything you need to check on it, pause/resume it, read the results, or
> rebuild it from scratch. Start at **§1 Quick status & commands**.

Launched: **2026-06-28**. Engine: the playability checker shipped in kjbox **v0.40.0**
(PR #112) — see [ARCHITECTURE.md](ARCHITECTURE.md) (§ playability) and the modules
`kj-controller/playability.py`, `playability_render.py`, `playability_batch.py`.

The run harness lives in the repo at **`kj-controller/scripts/playability-run/`** and is
deployed (copied) to **`/opt/nomad/playability-run/`** on the device.

---

## 1. Quick status & commands

SSH to the device first: `ssh nomadpctunnel`. Then:

```bash
# Where is it? (% per phase, overall %, throughput, ETA, recent temp/load)
/opt/nomad/playability-run/progress.sh

# Pause it BEFORE a live karaoke event (graceful; loses nothing)
/opt/nomad/playability-run/pause.sh

# Resume after the event — also how you restart it after any reboot
/opt/nomad/playability-run/start.sh

# Generate CSV + Markdown reports of the flagged (corrupt/unplayable) files
/opt/nomad/playability-run/report.sh

# Watch it live
journalctl -u playability-batch -f
```

`progress.sh` is read-only and safe to run anytime. Example output:

```
=== Playability run @ 2026-06-28 02:52:27   [batch: RUNNING] ===
Phase A internal : 5 / 2485   (0.2%)
Phase B 4TB SSD  : 0 / 400128   (0.0%)
Overall          : 5 / 402613   (0.0%)
Flagged so far   : 1  (failed = corrupt / unplayable, to review & delete)
Throughput       : 1.3 files/min    ETA for remaining: ~1d 7h (at current rate)
--- recent system samples (temp / load / counts) ---
2026-06-28 03:09:43 temp=57.0C load=2.85 3.15 2.65 internal=24 ssd=0 batch=run
```

> The ETA shows "warming up" until ~3 monitor samples accumulate (≈15 min) and stays
> a little pessimistic for the first ~30 min while the cold-start samples age out of the
> window. For a from-scratch estimate use the table in §4.

---

## 2. ⚠️ The one critical gotcha: run as `nomad`, never root

VLC **refuses to run as root** ("cannot be run by non-trusted users"). If the batch runs
as root, *every video* is falsely flagged `overall_ok: false` on the VLC side (mpv is
unaffected). The fix is baked into `start.sh`: it launches the batch unit with
`-p User=nomad -p Group=nomad` and mirrors the live service's env
(`HOME=/home/nomad`, `XDG_RUNTIME_DIR=/run/user/1000`, copied from
`kj-controller.service`).

Corollaries:
- **Do NOT** pass `DISPLAY=:0`. The render engine spawns its own off-screen Xvfb on a
  free display (see `playability_render.py::pick_free_display`); `:0` is the live show.
- If a bad (root) launch ever leaves root-owned files in `/opt/nomad/playability-run/`,
  the `nomad` batch can't append to or truncate them — `sudo rm` the stale
  `internal_results.jsonl` / `batch.log` and relaunch (the manifest makes this safe).

---

## 3. What it does — two phases

`run_all.sh` runs **Phase A to completion, then Phase B**. Internal storage is processed
first because it's a smaller, more diverse, higher-corruption-risk set (fresh YouTube
pulls + the period when divebar downloads were truncating).

### Phase A — internal storage (deep render, the VLC-vs-mpv matrix)
- Roots: `/opt/nomad/YTDownloads`, `/opt/nomad/MP4-720p`
- ~2,485 `.mp4` files
- `playability_batch.py --depth deep` → integrity + decode + **off-screen render in BOTH
  VLC and mpv**. This is the matrix that tells us whether we can switch to mpv-primary.
- Results: `/opt/nomad/playability-run/internal_results.jsonl`

### Phase B — 4TB SSD archive (integrity-only, gentle)
- Roots: `/media/nomad/Nomad4TBOne/HyperMule` (≈398,146 CDG `.zip` files — the bulk),
  `/media/nomad/Nomad4TBOne/NomadKaraoke-2024.11.02` (≈1,982 `.mp4`)
- ≈400,128 files total
- `ssd_runner.py` → integrity + decode only, **no render** (`renderers=()`, so no Xvfb,
  minimal CPU/IO). Catches bad zips, missing `.cdg`, undecodable audio/video — the goal
  for the archive without hammering the SSD.
- Results: `/opt/nomad/playability-run/ssd_results.jsonl`

---

## 4. ETAs (from measured per-file rates)

| Phase | Files | Method | Observed rate | Est. **active** time |
|---|---|---|---|---|
| A — internal | 2,485 | deep, VLC+mpv | ~46 s/file | **~30 h** (a bit over a day) |
| B — 4TB SSD | 400,128 | integrity-only | ~4–5 s/file | **~20–22 days** active |

These are the rates **under the gentle resource caps** (`CPUQuota=200%`, `Nice=19`,
`IOSchedulingClass=idle`, 0.3 s throttle) on a device also running the live KJ stack —
roughly 2× slower than an unthrottled benchmark, which is the intended trade-off. The
**live `progress.sh` ETA is authoritative**; it self-corrects to the true rate after the
first ~30 min (once the cold-start samples age out of its averaging window).

Phase B is intentionally allowed to take weeks — pause it around live events and resume
afterward. Finishing the whole library inside any particular window is **not** required.

---

## 5. How it satisfies the run requirements

- **Monitorable / progress %** — `progress.sh` (per-phase %, overall %, throughput, ETA);
  `monitor.sh` logs a temp/load/counts sample every 5 min to `monitor.log`.
- **Resumable / no data loss** — both phases use a **resumable JSONL manifest**: each
  file's verdict is appended durably the moment it completes, and already-checked files
  are skipped on restart by `(mtime, size)`. A stop / reboot / thermal-pause loses at most
  the single in-flight file (which is simply re-checked on resume).
- **Gentle on SSD & CPU** — the batch unit runs with `Nice=19`,
  `IOSchedulingClass=idle` (yields the disk to everything else), `CPUWeight=20`,
  `CPUQuota=200%` (≤ 2 cores), `MemoryMax=2G`, plus a 0.3 s/file throttle.
- **Thermal safety** — `monitor.sh` reads package temp every 5 min and **hard-stops the
  batch (not the device) at 92 °C** (CPU crit is 105 °C). Typical observed temp under
  load: ~54–56 °C.

---

## 6. Reading the results

Each JSONL line is one file's full verdict. The headline field is `verdict.overall_ok`
(`true` = plays video fine in the tested player(s); `false` = flagged). A row also records
per-renderer detail (`renderers.vlc` / `renderers.mpv` with `frame_captured`,
`frame_nonblank`, `error`), integrity, decode, and timings.

To turn the raw JSONL into human-readable lists of files to review/delete:

```bash
/opt/nomad/playability-run/report.sh
# writes, for each phase:
#   {internal,ssd}_report.csv   — every file + verdict
#   {internal,ssd}_report.md    — summary + lists: unplayable, vlc-not-mpv, cdg-problems
```

`report.sh` is safe to run mid-run; it reports whatever the JSONLs hold so far.

Quick ad-hoc counts:
```bash
grep -c '"overall_ok": false' /opt/nomad/playability-run/internal_results.jsonl
grep -c '"overall_ok": false' /opt/nomad/playability-run/ssd_results.jsonl
```

---

## 7. Reboot behavior (important)

The run uses **transient** systemd units (`systemd-run --collect`), so it does **not**
auto-start on boot. This is deliberate — it can never surprise-start during a live show
after a power blip or update. **After any reboot, re-run `start.sh`** to resume; it picks
up exactly where the manifest left off. `start.sh` is idempotent (it no-ops if the batch
is already running).

Units involved:
- `playability-batch.service` — the work (Phase A then B), runs as `nomad`.
- `playability-monitor.service` — the 5-min logger + thermal hard-stop, runs as root.

---

## 8. Install / reinstall from scratch

The repo copy under `kj-controller/scripts/playability-run/` is the source of truth.
To (re)deploy to the device:

```bash
# 1. Copy the harness to the device
scp kj-controller/scripts/playability-run/* nomadpctunnel:/opt/nomad/playability-run/
ssh nomadpctunnel 'chmod +x /opt/nomad/playability-run/*.sh /opt/nomad/playability-run/*.py'

# 2. Seed the totals used for the progress % (recompute if the library changed)
ssh nomadpctunnel 'mkdir -p /opt/nomad/playability-run && cd /opt/nomad/playability-run \
  && echo 2485 > totals.internal && echo 400128 > totals.ssd'

# 3. Launch (or resume)
ssh nomadpctunnel /opt/nomad/playability-run/start.sh
```

Recompute totals if needed (counts of the scanned media extensions per root):
```bash
# internal (mp4):
ssh nomadpctunnel 'find /opt/nomad/YTDownloads /opt/nomad/MP4-720p -type f -iname "*.mp4" | wc -l'
# ssd (CDG zips + mp4):
ssh nomadpctunnel 'find /media/nomad/Nomad4TBOne/HyperMule /media/nomad/Nomad4TBOne/NomadKaraoke-2024.11.02 -type f \( -iname "*.zip" -o -iname "*.mp4" \) | wc -l'
```

The scripts hardcode device paths (`/opt/nomad/playability-run` for working files,
`/opt/nomad/kjbox/kj-controller` for the engine + venv) because they are device-specific
operational tooling. Edit the `WORK` / `CODE` / `PY` vars at the top of each script if the
device layout ever changes.

---

## 9. File reference (`kj-controller/scripts/playability-run/`)

| File | What it does |
|---|---|
| `start.sh` | Launch (or resume) the batch + monitor as transient systemd units. **Runs the batch as `nomad`** with the gentle resource limits. Idempotent. |
| `pause.sh` | `systemctl stop playability-batch` — graceful pause for a live event. |
| `run_all.sh` | The actual work: Phase A (`playability_batch.py`, deep) then Phase B (`ssd_runner.py`, integrity-only). |
| `ssd_runner.py` | Phase B sweeper — integrity-only, reuses the deployed `playability_batch` helpers + the same resumable JSONL-manifest format. |
| `monitor.sh` | 5-min temp/load/progress logger to `monitor.log`; thermal hard-stop at 92 °C. |
| `progress.sh` | On-demand progress + ETA report (read-only). |
| `report.sh` | Render the JSONLs into CSV + Markdown lists of flagged files. |

Working files written on the device (not in the repo):
`internal_results.jsonl`, `ssd_results.jsonl`, `batch.log`, `monitor.log`,
`{internal,ssd}_report.{csv,md}`, `totals.internal`, `totals.ssd`.
