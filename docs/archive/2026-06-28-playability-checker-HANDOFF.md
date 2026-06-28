# Playability Checker — Session Handoff (2026-06-28)

> ## UPDATE (2026-06-28, later session) — tier-2 + frontend now BUILT
> Handoff items **#1 (tier-2 async render verification)** and **#3 (frontend: surface
> verdict)** are now implemented + tested on the branch (still NOT deployed). New commits:
> - `rotation_store`: `playability_warning` column + `set_playability_warning()` (no
>   `updated_at` bump).
> - `XvfbDisplay`: auto-picks a free display (`pick_free_display`) — no more hard `:99`
>   collision risk between a tier-2 check and a batch sweep. **This closes the §1
>   concurrency decision (chose: single-worker queue serialises tier-2 + dynamic display
>   as belt-and-braces) AND the "Xvfb :99 hard-coded" gotcha below.**
> - `routes.py`: single-worker queue + `_run_tier2_check` (full render check vs
>   `current_app.vlc.render_mode`) enqueued after a successful `/rotation/link`; stamps the
>   warning on fail, clears on pass. Best-effort, skips pure-audio.
> - Frontend: ⚠️ badge on flagged rotation rows + prominent auto-dismiss toast for tier-1
>   422 rejects (link/upload).
> - Version bumped 0.39.0 → **0.40.0**. Full suite green (1 expected Xvfb skip). Frontend
>   syntax-checked only — **not yet visually verified on a running instance.**
>
> **Remaining (unchanged priority):** #2 full-library batch run (on box, Andrew-gated),
> frontend on-device visual check, #4 deploy (Andrew-gated, off-show), #5 tuning.
> **New small follow-up:** only `/rotation/link` enqueues tier-2 — download-auto-link paths
> don't yet (downloads still get tier-1 at download time).


**For the next Claude session.** This documents everything built so far on the kjbox
playability-checker, the on-device validation done, and the remaining work. Read this first,
then the design + plan + findings docs linked below.

## TL;DR

A belt-and-braces playability checker for the kjbox karaoke library that verifies a file
actually **renders video** (not just audio) in VLC and mpv, supports **CDG zips**, runs
**show-safe** (off-screen Xvfb, never the live `:0`/`hw:0,0`). Engine + batch tool + inline
hard-gates are **built, tested, and validated on the real device**. Remaining: the **tier-2
async render verification**, the **full-library batch run**, frontend polish, and deploy.

## Where things live

- **Worktree:** `/Users/andrew/Projects/nomadkaraoke/kjbox-playability-checker`
- **Branch:** `feat/sess-20260627-2107-playability-checker` (pushed to origin)
- **Docs (in worktree `docs/archive/`):**
  - `2026-06-27-playability-checker-design.md` — design spec
  - `2026-06-27-playability-checker-plan.md` — 13-task implementation plan
  - `2026-06-27-playability-confidence-run-findings.md` — on-device validation results
  - `2026-06-28-playability-checker-HANDOFF.md` — this file
- **SDD progress ledger:** `.superpowers/sdd/progress.md` (git-ignored) — per-task commit log
- **Memory:** `project_kjbox_playability_checker.md` in the nomadkaraoke agent memory

## What's DONE (committed on the branch)

### Engine (`kj-controller/`)
- `frame_analysis.py` — pure Pillow frame math (blank/black detection, motion, judge).
- `playability.py` — `PlayabilityResult`, ffprobe integrity parser, ffmpeg decode parser,
  kind classification, **CDG sub-pipeline**, `compute_verdict`, `PlayabilityChecker.check()`.
- `playability_render.py` — `XvfbDisplay` (off-screen X), VLC/mpv frame-capture command
  builders, `render_check()` (+ optional `keep_dir` to save a representative frame).
- `playability_batch.py` — resumable library walker, mtime/size skip-manifest, JSONL stream,
  CSV+Markdown aggregation (VLC-vs-mpv matrix), CLI `main()`.

Key engine facts:
- `check(path, renderers=("vlc","mpv"), depth="deep", short_circuit=False, display=None, frames_dir=None)`.
- **Inline gate mode:** `check(path, renderers=(), depth="quick")` → integrity + ~5s sampled
  decode, **no render, no Xvfb** (~1–3s). Used by the hard-gates.
- **Full mode:** `check(path, renderers=("vlc","mpv"))` → also Xvfb render proof per renderer.
- `res.timings` = `{integrity, decode, cdg, render_vlc, render_mpv, xvfb_start, total}`.
- `res.verdict` = `{overall_ok, reasons[], vlc_playable, mpv_playable}`.
- **CDG verdict:** mpv is recorded but does NOT gate `overall_ok` (mpv can't render CD+G).
- **Xvfb wiring:** `run_batch` opens ONE shared `XvfbDisplay`; `check()` self-starts one only
  when a VLC render is needed and no display is passed. Fixed display `:99` (see gotchas).

### Inline hard-gates (tier-1) — DONE
- **Link** (`routes.py` `/rotation/link`): `_playability_gate(file_path)` before
  `rotation.link_file` → 422 `{error, verdict}` on fail.
- **Upload** (`routes.py` `handle_upload`): after `file.save(dest)`, before `media.scan()` →
  `os.remove(dest)` + 422 on fail.
- **Downloads** (`media.py` `download_video`, `download_from_url`): after file lands, before
  index → delete + log + return `(None, None)` (the callers' failure convention; they swallow
  exceptions). Passing downloads cache `entry["playability"] = verdict`.
- Config accessor: `current_app.kj_config`; active renderer: `current_app.vlc.render_mode`.

### Test status
All unit + integration green (~150+ tests). Real-Xvfb integration test is skip-marked when
Xvfb absent. Run: `cd kj-controller && python3 -m pytest tests/unit tests/integration -q`.

## On-device validation (confidence run) — DONE

Ran on NomadPC (`ssh nomadpctunnel`) in a scratch dir `/tmp/kj-play` (NOT the live
`/opt/nomad/kjbox` tree), using the live venv python (`/opt/nomad/kjbox/kj-controller/venv/bin/python`,
has Pillow). Installed `xvfb` via apt. Validated against 8 + 20-diverse + **all 77 files
played the night of 2026-06-25→26**. Saved frames → labelled contact sheets (sent to Andrew).

Findings (full detail in the findings doc):
- Checker is accurate — contact sheets visually confirm real rendered frames.
- TRUE catches: truncated (`moov atom not found`) + audio-only (`no video stream`).
- **Found + FIXED a false-positive class:** CDG captured the black CD+G intro (`start=0`).
  Fix: `check_cdg` records audio duration; CDG capture now seeks mid-file. 3 discs confirmed.
- **mpv cannot render CD+G** (real limitation) → mpv-primary switch must route CDG to VLC.
- **Timing:** integrity ~0.15s, decode 2–21s (the expensive variable), vlc render ~3.5–7s,
  mpv render 3–24s, xvfb ~0.1s.

The full catalog lives on the SSD at `/media/nomad/Nomad4TBOne/HyperMule/Master Karaoke Folder/…`
(deeply nested); paths are indexed in `/opt/nomad/kjbox/kj-controller/external_media.db`
(table `media`, columns include `path`, `filename`). Local media: `/opt/nomad/YTDownloads`,
`/opt/nomad/MP4-720p`.

## REMAINING WORK (priority order)

### 1. Tier-2: async render verification + entry flagging (the user chose two-tier)
The inline gate (tier-1) hard-blocks on integrity+decode. Tier-2 = after a successful link,
run the FULL check (`check(path, renderers=(active,))` with render) in a **background thread**;
if the active renderer can't render it, **flag the rotation entry** so the KJ sees a warning
before playing. Needs:
- A background runner (daemon thread or the existing download-worker pattern).
- A rotation-entry flag: add a `playability_warning` column to `rotation_store` (SQLite
  migration), a setter, include it in `_decorate_rotation_entries`, and surface a ⚠️ badge in
  the rotation UI (`static/app.js` + `templates/index.html`).
- Decide concurrency: the Xvfb display is fixed `:99` — concurrent render checks collide. For
  tier-2 background use, either serialize render checks (a lock/queue) or give `XvfbDisplay` a
  dynamic free display number.

### 2. Full-library playability run
Run the resumable batch over the entire library to produce the complete VLC-vs-mpv matrix
(informs the mpv-primary decision). The batch tool (`playability_batch.py`) is ready. On the box:
```bash
# scratch dir already has the modules; refresh from the branch if needed.
cd /tmp/kj-play
nohup env PYTHONPATH=/tmp/kj-play nice -n 19 ionice -c3 \
  /opt/nomad/kjbox/kj-controller/venv/bin/python -u playability_batch.py \
  --roots /opt/nomad/YTDownloads /opt/nomad/MP4-720p /media/nomad/Nomad4TBOne \
  --throttle 0.3 --jsonl /tmp/kj-play/library-results.jsonl \
  > /tmp/kj-play/library.log 2>&1 < /dev/null &
```
- Resumable: re-run with the same `--jsonl` to continue (mtime/size manifest skips done files).
- ~400k catalog files → many hours/days; run detached (nohup) — the cloudflared SSH tunnel
  drops occasionally (exit 255), so NEVER run a long job in a foreground ssh.
- Watch: `ssh nomadpctunnel 'tail -f /tmp/kj-play/library.log'`.
- Note: the batch uses `run_batch` which opens ONE shared Xvfb — good. The `--throttle` is
  SSD-friendly. Consider a tiered approach (cheap integrity pass first, expensive render after)
  if the full deep run is too slow — currently it does deep (full decode + both renders) per file.
- After: `playability_batch.py` writes `playability_report.csv` + `.md` with the matrix buckets
  (unplayable / mpv-not-vlc / vlc-not-mpv / cdg-problems). The contact-sheet tooling
  (`scratchpad/contact_sheet.py` from this session — re-create if needed) can visualize frames.

### 3. Frontend: surface the 422 verdict clearly
Currently a blocked link shows `data.error` in the log panel + a 3s red flash; upload shows
inline text. The `verdict` field is ignored. For a hard-block the KJ relies on, make the
rejection prominent (a clear toast/modal naming the reason). Files: `static/app.js`
(`apiCall`/`rotationMutate`/`selectRotSearchResult`, and `uploadFile`).

### 4. Deploy (CAREFUL — production device)
The gates are BACKEND changes → require a `kj-controller` service restart, which **interrupts
active VLC playback**. Per kjbox `CLAUDE.md`: no `git push` to main / no restart without
Andrew's explicit OK. Autodeploy may be OFF. Deploy off-show. Xvfb must be installed on the
box (done on NomadPC; check NomadPi if it's also used). Add `Pillow` to the deployed venv and
`xvfb` as a system dep in the box setup docs/IaC.

### 5. Open tuning items
- The deep batch full-decodes every file on top of two renders — validate runtime on the full
  library before committing to a full sweep; consider the cheap-pass-first tiering.
- Re-confirm thresholds (`BLANK_SPREAD_THRESHOLD = 6.0`) against the full-library results.

## Gotchas learned this session
- **~~Xvfb display `:99` is hard-coded.~~ RESOLVED (later session):** `XvfbDisplay` now
  auto-picks a free display via `pick_free_display` (probes sockets + lock files), so concurrent
  tier-2 and batch checks no longer collide. (Earlier text below describing a fixed `:99` is
  historical.)
- **The cloudflared SSH tunnel (`nomadpctunnel`) drops** intermittently (exit 255). Run long
  jobs detached with `nohup … &` and poll the logfile; don't rely on a foreground ssh.
- **`sqlite3` CLI is not installed** on the box — use the venv python's `sqlite3` module.
  `rotation.db` opened with `mode=ro` showed 0 tables (WAL); the catalog paths are in
  `external_media.db`.
- **The repo's Bash tooling compresses/garbles multi-line output** (an `rtk` proxy). When a
  subagent reads a brief/file via `cat`, it can come back garbled — instruct subagents to use
  the Read tool, and verify transcription against the source. Several review/fix loops this
  session traced to this.
- **mpv can't render CD+G** — `mpv --vo=image` on a `.cdg` exits 2 / no frames. Expected.
- **CD+G is stateful** — seeking mid-file works in VLC (it rebuilds), but capturing `start=0`
  shows the black intro. Always capture CDG mid-file (the fix).

## How to resume on the box
- `ssh nomadpctunnel` (cloudflared tunnel) or `ssh nomadpc` (LAN).
- Scratch dir `/tmp/kj-play` holds the staged modules + scripts from this session (may be
  cleared on reboot — re-stage from the branch: scp the `kj-controller/*.py` modules +
  `zip_playback.py`, run with the live venv python + `PYTHONPATH=/tmp/kj-play`).
- NEVER edit `/opt/nomad/kjbox` (live deploy) directly; never touch `:0` or `hw:0,0`.
