# Original-Vocals Guide — FULL session handoff (2026-07-08)

Pick-up doc for a fresh Claude session (this session ran long). Captures everything
done + learned, the file/folder map, how to reach kjbox, all gotchas, and the next
work (vocal-guide alignment).

## Read first, in order
1. This doc.
2. Auto-memory `project_kjbox_original_vocals_guide` (loads automatically — has the arc + gotchas).
3. **Alignment spec:** `docs/archive/2026-07-08-vocal-guide-alignment-design.md`
4. **Alignment plan (execute this next):** `docs/archive/2026-07-08-vocal-guide-alignment-plan.md`
5. Prior: `2026-07-07-verified-original-audio-dataset-{design,plan}.md` (M1), `2026-07-06-original-vocals-guide-design.md`.

## The feature (goal)
During a NOMAD karaoke song, optionally layer the **original singer's isolated
vocals** under playback at an adjustable "Original Vocals" slider (default 0 %/off;
KJ raises ~30 %) as a sing-along guide. Pitch shifts both streams together. Lives in
kj-controller (the device).

Milestones: **M1 verified-originals dataset ✅ → M2 vocals dataset ✅ → padding+device ✅ →
playback feature ✅ SHIPPED → per-track ALIGNMENT ⬅ NEXT → (later) karaoke-gen write-path.**

---

## ✅ DONE this session (all shipped + verified)

### Playback feature — SHIPPED (PR #181, kj-controller v0.75.0, on `main` = `39e65da`)
- 3rd "Original Vocals" volume slider (hidden until the current master has a guide).
- mpv mixes the guide UNDER karaoke via `--lavfi-complex` amix; pitch shared.
- On-device smoke test **9/9 pass**; deployed to the (idle) device via autodeploy.
- Feature files (all on `main`): `kj-controller/{mpv_manager.py, playback.py, routes.py,
  config.py, static/app.js, templates/index.html, pyproject.toml}` + tests
  `tests/unit/test_vocals_guide.py`.

### M2 vocals dataset — built + on device
- **1,488 guides built** (1,116 reused in-folder full-vocals stems, gated on
  duration-match; 371 separated fresh with `2_HP-UVR.pth`). Router: `build_vocals.py`.
- Synced to device; padded on-device (fixed 5 s lead+trail — an **MVP that the
  alignment work replaces**). NOMAD-audio refreshed to the M1 originals.

### Short-Version purge (2026-07-08, user-directed)
- **37 tracks named "(Short Version)"** — third-party edits of cover-band instrumentals;
  no real original can exist. Deleted their original+vocals everywhere (21 had files):
  Mac `Tracks-Audio/{Original,Vocals}` (42 files) + device `NOMAD-audio`/`NOMAD-vocals`/
  `NOMAD-vocals-padded` (63 files). Device dirs now **1,467** each.
- ⚠️ **Still pending:** their in-folder mirror copies inside each
  `Tracks-Organized/<folder>/` were NOT deleted (offered; awaiting user go-ahead).

### What the video-vs-vocals check revealed (why alignment is the next job)
- Duration verify (vocals vs originals) = 0 mismatches, BUT comparing vs the **master
  videos** (NOMAD-720p) showed the fixed-5 s pad is only a rough approximation:
  median video−song overhead ≈ 10.1 s but ranges widely; FR tracks have a 5 s intro but
  **no** outro; ~15 non-short tracks had video < song (source/edit mismatches, e.g.
  `0307` had a hidden-track tail the user has since trimmed).
- User's requirement: **zero-tolerance (≤100 ms)** alignment, measured per-track, with
  human verification of anything uncertain. → the alignment spec/plan below.

---

## ⬅ NEXT: per-track alignment (spec + plan written, ready to execute)
- **Branch:** `feat/sess-20260708-vocal-guide-alignment` (off `main` `39e65da`; has the
  spec `e99c337` + plan `ea1f123`).
- **Approach:** reuse `verify_sync.py` (cross-correlate original↔video → offset, apply
  to guide) → classify confirmed/needs-review → render pre-rendered A/V review clips
  (variant combs; ~7 % spot-check of confirmed) → user fills `align_decisions.csv` →
  emit aligned guides to `NOMAD-vocals-padded`, remove excluded. Playback code unchanged.
- **Execute** the plan via `superpowers:subagent-driven-development` (recommended) or
  `executing-plans`. Tasks 1–4 are TDD (pure logic on Mac), Task 5 is device validation.
- **Prereq:** `pip3 install --user numpy` on the device (verify_sync needs it).
- Mismatch tracks the user fixes manually (e.g. `0307`'s trimmed
  `Tracks-Organized/NOMAD-0307 …/NOMAD-0307 - Frightened Rabbit - Square 9 (Original).flac`)
  re-enter at the measurement step once their `Original` is corrected.

---

## How to reach kjbox (the device)
- **SSH:** `ssh nomadpctunnel` (Cloudflare tunnel; works unattended from this Mac).
  LAN `nomadpc.local` does NOT resolve from this Mac — always use the tunnel.
- **App:** Flask on `http://localhost:5001` (device-local). `:80` = nginx public.
  Read status: `ssh nomadpctunnel 'curl -s http://localhost:5001/status'`.
- **Public URL:** `https://kjbox.nomadkaraoke.com` (Cloudflare Access; use the
  `$KJBOX_CF_ACCESS_CLIENT_ID` / `_SECRET` service-token headers from the workspace
  `.envrc` for curl; or `ssh nomadpctunnel 'curl localhost'` to bypass).
- **STOP karaoke:** `POST /control {"action":"stop"}` — do NOT send a raw mpv `stop`
  IPC; the coordinator's crash-recovery **auto-replays** it (blasted a song at full
  volume during the smoke test).
- **Control endpoints:** `/play {file_path}`, `/volume {target:karaoke|filler|vocals, level:0-256}`,
  `/pitch {semitones:-6..6}`, `/control {action:pause|restart|stop|fadeout}`, `/status`.
- **mpv IPC socket:** `/tmp/mpv-karaoke.sock` (JSON commands; e.g. `get_property lavfi-complex`).
- **Autodeploy:** `kj-autodeploy.service` is ACTIVE → merging to `main` pulls + restarts
  kj-controller **if the diff has any `.py`**. Restart interrupts playback →
  **device is LIVE PROD; always check `/status` `state`/`current_playing` first** and
  never restart during a show. It was idle overnight.
- **Device dirs:** `/opt/nomad/downloads/` → `NOMAD-720p` (master videos, 1506),
  `NOMAD-audio` (originals, 1467), `NOMAD-vocals` (raw guides, 1467),
  `NOMAD-vocals-padded` (aligned guides the feature reads, 1467).
- **Device tools:** `/usr/bin/ffmpeg` + `ffprobe`; python3 **without numpy** (install it).

## Mac / Dropbox / dataset
- **Dataset root:** `/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/`
  - `Tracks-Audio/Original/` — M1 verified originals (~1,467).
  - `Tracks-Audio/Vocals/` — M2 guides (~1,467).
  - `Tracks-Organized/<NOMAD-#### - Artist - Title>/` — per-track archive: master videos,
    stems (`(Vocals …)`, `(Instrumental …)`), title cards, in-folder original mirror, etc.
- **conda python (numpy, audio-separator 0.44.1):** `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python`
- **ffmpeg/ffprobe:** `/opt/homebrew/bin` (NOT the conda env — env lacks ffprobe).
- **Separation models:** `/Volumes/AndrewMacSD/python-audio-separator-models-repo` (`2_HP-UVR.pth`, roformers…).
- **Materialize a Dropbox online-only file:** `scripts/original_vocals/local_clone/materialize <path>`
  (passive `cat`/`cp`/`ffprobe` do NOT materialize; they see 0 bytes).

## Tooling map (`scripts/original_vocals/`, all on `main`)
- **M1 (dataset):** `oracle_*.py`, `assemble_originals.py`, `withvocals_extract.py`,
  `oracle_vocals_only_sweep.py`, `classify.py`, `verify_sync.py`, `make_offset_table.py`,
  `local_clone/*`. Data (gitignored) under `data/` — incl. `review_decisions.tsv` (the
  human record of truth), `manifest.csv`.
- **M2 (vocals):** `build_vocals.py` (+ `test_build_vocals.py`), `device/pad_vocals.sh`,
  `device/verify_durations.py`.
- **Alignment (to be created per the plan):** `align_core.py`, `align_measure.py`,
  `align_clips.py`, `align_apply.py` (+ tests).
- **verify_sync.py** is the alignment engine: `best_lag` (FFT cross-correlation, unit-tested),
  `verify_pair` (offset + confidence verdict), `emit_padded`. Correlates original↔video.

## Gotchas learned (save yourself the pain)
- **RTK shell hook** mangles some Bash: `&&`/`||` in compound one-liners → `zsh parse
  error near &`; `grep --include=*.py` glob expansion; `tail -N`. → run commands on
  separate lines; launch `nohup … &` inside a `bash script.sh`, not inline.
- **Long detached jobs:** `nohup caffeinate -s -i <cmd> </dev/null >log 2>&1 &` (via a
  launcher script). The harness `run_in_background` gets **reaped ~24 min** — don't use
  it for hour-long jobs. Use the **Monitor** tool (tail -f log | grep) for completion signals.
- **Dropbox disk reclaim:** no scriptable evict — `brctl evict` FAILS ("not a CloudDocs
  library"); no `dropbox` CLI. Only the user's Finder "Make Online Only" / "Free Up
  Space" frees space. (This session the user evicted `Tracks-Organized` → freed ~97 GB.)
- **Mac has openrsync** (BSD): no `--info`/`--size-only`. `rsync -a --delete` re-copies by
  mtime. Tunnel throughput ≈ **6 MB/s**.
- **rsync races:** killing a loop's bash doesn't kill its child `rsync` → concurrent
  rsyncs corrupt temp files (`.OnULfA` errors). `pkill -f rsync` all of them.
- **ffmpeg `.part` output** confuses the muxer ("Invalid argument") → pass `-f flac`.
- **mpv (0.37 on device):** guide via `audio-add <file> auto` (→ aid2, not selected);
  mix graph `[aid2]volume=G[gv];[aid1][gv]amix=inputs=2:normalize=0[ao]`, G=level/256;
  rubberband stays in `--af=@rb` (pitches the mix, pitch code unchanged). **MUST clear
  `lavfi-complex` BEFORE the next `loadfile`** (tracked by `_lavfi_active`) — clearing
  after breaks `af-command rb`. Verified via isolated `--ao=null` mpv tests.
- **audio-separator:** `-m 2_HP-UVR.pth --single_stem Vocals --output_format FLAC
  --model_file_dir /Volumes/AndrewMacSD/… --output_dir <scratch>` (~30 s/track, M3 MPS).
- **verify_sync** needs numpy (device lacks it). `silencedetect` logs at INFO — don't `-v error`.

## Git state
- `main` = `39e65da` (feature #181 merged: kj-controller v0.75.0 + all M1/M2 scripts).
- Working branch `feat/sess-20260708-vocal-guide-alignment` = `main` + alignment spec
  (`e99c337`) + plan (`ea1f123`). Execute the plan on this branch.
- Workflow: `/coderabbit` before `/pr`; PR body gets `@coderabbitai ignore`; squash-merge;
  autodeploy restarts the (idle) device.

## Open items
- Execute the alignment plan (numpy on device first).
- Delete short-version in-folder mirror copies (awaiting user OK).
- Fold in user-fixed mismatch tracks (0307 trimmed; others as the user fixes them).
- Later: karaoke-gen write-path (emit original+vocals to the Dropbox paths on each render).
- Consider committing `data/review_decisions.tsv` (the human record) — currently gitignored.
