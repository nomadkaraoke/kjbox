# Original-Vocals Guide — Session Handoff (2026-07-07)

Pick-up doc for a fresh Claude session. Full design:
`2026-07-06-original-vocals-guide-design.md`; phase-4 build plan:
`2026-07-07-original-vocals-phase4-playback-plan.md`; agent memory:
`project_kjbox_original_vocals_guide`.

## The goal
During a NOMAD-produced karaoke song, optionally layer the **original singer's
vocals** under playback at an adjustable volume (~30%, default off) as a
sing-along guide for nervous singers. Pitch-shift must apply to both the karaoke
instrumental and the guide. Feature lives in kj-controller (the device).

## Decisions locked (with the user)
- **Guide source = isolated vocals** (not full mix), produced by audio-separator
  model `vocals_mel_band_roformer.ckpt` on the Mac (M3 Max/MPS). Cleaner than the
  full mix (no doubled band). Full-mix stays as a fallback.
- **Sync = measured per track**, not assumed. Video = [silent title-card ~5s] +
  [instrumental aligned to original] + [outro]; intro ~5s but not exact, outro
  era-dependent. Pad the guide by the measured offset (mpv `adelay`) at playback.
- **Control** = 3rd "Original Vocals" slider, default 0%/off, KJ raises to ~30%;
  shown only for mpv + sync-confirmed tracks with a resolvable guide.
- **Autonomy** = full (merge to main, restart device, on-device test) when no
  show; merge phase-4 on spectral verification alone.
- Guide padded on the fly (no pre-padded files).

## What's DONE
1. **Phase 1 — harvest**: classifier picks the original per NOMAD folder →
   `scripts/original_vocals/data/manifest.csv`. **PR #175 merged.**
2. **Fetch — 100%**: all **1,372** originals on the device at
   `/opt/nomad/downloads/NOMAD-audio/`. (Dropbox API lacked `files.content.read`,
   so routed through the Mac's Dropbox client via `NSFileCoordinator` materialize
   — `scripts/original_vocals/local_clone/`.)
3. **Phase 2 — sync verify**: `scripts/original_vocals/verify_sync.py`
   (cross-correlation + silence corroboration). Full run: **1,355/1,372 confirmed
   (98.8%)**, 16 needs-review, 1 error.
4. **Phase 3 — offset table**: `make_offset_table.py` → on the device at
   `/opt/nomad/downloads/NOMAD-vocals/offsets.json` (+ `sync_report.csv`).
5. **Vocals separation pipeline**: `scripts/original_vocals/vocals/` — resumable,
   diagnostics-recording. **PAUSED at 287/1,372** (re-run `vocals/run.sh` to
   continue; ~1.4 days remaining).
6. All tooling on **PR #178** (open, branch `feat/sess-20260707-local-clone-fetch`).

## Current STATUS
- Separation **paused**, 287 vocals on the device (`NOMAD-vocals/*.flac`).
- Sync/offset data complete and on the device.
- Phase 4 (playback feature) **not started** — fully specced + technique proven
  on-device (mpv `lavfi-complex` amix + rubberband pitches both streams).

## KEY FINDING — wrong inputs, concentrated early
The phase-1 name-match heuristic sometimes picked an **already-separated
instrumental** instead of the original full mix (e.g. NOMAD-0100). These separate
to near-silent vocals. Of the first 254 separated:
- 63% good/ok vocals (≥ −18 dB peak) — correct inputs.
- 22% weak (−18 to −30) — quiet mix *or* partly wrong.
- **16% near-silent (< −30 dB) — almost certainly wrong input.**
- The near-silent ones **cluster in NOMAD-0001–0200** (24 in 0001–0100, 13 in
  0101–0200, 3 in 0201–0300, tailing off) — the earliest hand-made era. Later
  eras look clean.

Sync-verify can't catch this (an instrumental input correlates *even better* with
the instrumental video), so vocal-energy is the detector: `flag_weak_vocals.py`.

## WHAT TO REVIEW (to decide next steps)
Run these / open these:
1. **Vocal quality distribution + flags** (needs separation done so far):
   `python3 scripts/original_vocals/vocals/flag_weak_vocals.py` →
   `scripts/original_vocals/vocals/weak_vocals_review.csv` (worst-first). And
   `vocals_diagnostics.csv` (per-track peak dB + size).
2. **Listen to a few** (the ultimate quality check) — pull from the device:
   - a good one: `NOMAD-0002 …` (−6.4 dB) and `NOMAD-0257/0258 …` (−0.9 dB)
   - a flagged one: `NOMAD-0018`, `NOMAD-0024`, `NOMAD-0100`
   `scp nomadpctunnel:"/opt/nomad/downloads/NOMAD-vocals/NOMAD-00xx*.flac" .`
3. **Sync/offset table**: on the device `NOMAD-vocals/offsets.json` +
   `sync_report.csv`. The 17 not-confirmed brands: 0117, 0120, 0222–0226, 0580,
   0666, 0668, 0834, 0923, 0976, 1001, 1048, 1095, 1102.
4. **Phase-1 picks**: `scripts/original_vocals/data/manifest.csv` (what was chosen
   per folder; the `alt_candidates` column often holds the *correct* file for a
   bad pick — e.g. NOMAD-0100's alt `01 Little Discourage.mp3`).

## DECISIONS to make next session
- **Bad early inputs (NOMAD-0001–~0200):** re-pick from the folder (often the
  album-rip `NN Title.mp3` alt is the real original, not the name-match
  `Artist - Title.mp3`)? Improve the classifier for the early era? Or accept a
  partial guide catalog and skip the un-fixable ones?
- **Sequencing:** finish separating all 1,372 first, or re-pick the flagged early
  inputs *then* re-separate just those (avoid separating known-bad inputs)?
- **Phase 4:** build + deploy now (works for the ~1,100 good tracks with full-mix
  fallback) or wait until the early inputs are fixed?
- **The 17 sync needs-review**: re-check those inputs (several are the known
  "(Short Version)" edits 0222–0226 that legitimately don't align 1:1).

## How to run / resume things
```
# resume vocals separation (Mac, caffeinated, skips done)
bash scripts/original_vocals/vocals/run.sh
bash scripts/original_vocals/vocals/status.sh        # progress
python3 scripts/original_vocals/vocals/flag_weak_vocals.py   # weak/wrong-input flags

# re-run classifier / regenerate manifest (from a fresh rclone listing)
python3 scripts/original_vocals/classify.py <listing> --out-dir scripts/original_vocals/data

# re-run sync verification (device; needs the venv) — see phase-4 plan §1
# regenerate offset table
python3 scripts/original_vocals/make_offset_table.py <sync_report.csv> -o offsets.json
```

## Where things live
- **Device** (`ssh nomadpctunnel`): originals `/opt/nomad/downloads/NOMAD-audio/`;
  vocals `/opt/nomad/downloads/NOMAD-vocals/` (+ `offsets.json`, `sync_report.csv`);
  master videos `/opt/nomad/downloads/NOMAD-720p/`. **Live prod device — no pushes/
  restarts during a show.**
- **Repo**: `scripts/original_vocals/` (classify, verify_sync, make_offset_table,
  local_clone/, vocals/). Branch `feat/sess-20260707-local-clone-fetch`, PR #178.
- **Mac**: audio-separator in miniforge env `nomadkaraoke`; ~28 GB materialized in
  the Dropbox folder to reclaim (Finder → "Make Online-Only").

## Gotchas learned
- ffmpeg `silencedetect`/`volumedetect` log at INFO — do NOT pass `-v error`.
- macOS has no `setsid` (use `nohup`), no GNU `xargs -a/-d`.
- kjbox render engine is mpv; VLC has no pitch/mixing. Pitch = runtime
  `af-command rb set-pitch`.
- Merging any `.py` (even scripts) triggers `kj-autodeploy` → restarts kj-controller.
