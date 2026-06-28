# Playability Checker — Design Spec

**Date:** 2026-06-27
**Project:** kjbox (`kj-controller`)
**Status:** Design approved — pending implementation plan
**Worktree:** `kjbox-playability-checker` (`feat/sess-20260627-2107-playability-checker`)

## Problem

When the KJ hits *play* on a rotation entry, the file occasionally fails:

1. **Total failure** — nothing renders. Root cause traced (2026-06-25 incident): the
   file was a **truncated/incomplete download** (`moov atom not found`). The KJ-facing
   symptom was a black screen while the controller logged a misleading
   `Playback started`.
2. **Audio-but-no-video** — audio plays full length but the video never appears.
   This is **invisible in logs**: the song plays to completion (audio fine) and logs a
   normal `Karaoke video finished playing`. Only the KJ's eyes catch it.

Two latent gaps make this silent:

- The player logs `Playback started` the instant it issues the play command, without
  confirming a frame rendered. (`vlc.py` has a partial `verify()` that only checks the
  player reached the `playing` state — an audio-level signal, never video — and only
  logs a hidden `WARNING`.)
- VLC runs `-I dummy` with no file-logging, so decode errors go nowhere.

A 2026-06-25 spot check found **4 of 18** `divebar__*.mp4` files on the box were corrupt
with the same `moov atom not found` signature — so this is a class of problem, not a
one-off.

## Goal

Make "click play → it fails" impossible, with a belt-and-braces playability checker that:

1. Validates a file is playable, **verifying actual video render (not just audio)** in
   **both VLC and mpv**, and supports the **CDG (`.zip`)** files prevalent in the library.
2. Runs as a **hard gate** when a file is linked to a rotation entry (reject on fail).
3. Runs after any **upload/download** (reject + delete unplayable files so junk never
   accumulates and the KJ knows to retry/find another source).
4. Runs as a **library-wide batch** (local downloads + the attached 4TB SSD), throttled,
   writing results incrementally and aggregating at the end, so unplayable files can be
   found and triaged.

The check must run **without interrupting live playback** — it must never output to the
live screen (`:0`) or the live audio device (`hw:0,0`).

### Secondary goal — inform the mpv-primary switch

The KJ wants to switch the primary player back to **mpv** (it supports on-demand
pitch-shifting for singers via the rubberband filter). The batch's per-player results are
first-class output: a per-file VLC-vs-mpv matrix that shows exactly which files play in
each player, so the switch can be made with confidence and problem files triaged first.

## Key decisions (from brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Render-proof method | **Xvfb frame-capture** | Only reliable way to catch audio-but-no-video is to drive the *actual* renderer and look at real pixels. A file can decode fine in ffmpeg yet still show black in VLC. |
| Install Xvfb on the box | **Yes** | Small, standard package. Enables off-screen rendering on a virtual display, isolated from the live `:0`/`hw:0,0`. |
| Renderers tested | **Both VLC and mpv, always** | Needed for the mpv-primary switch data; recorded per file. |
| Link-time behaviour on fail | **Hard block, no override** | Justified by validating the checker against the whole library *first* — false positives are eliminated empirically before the gate goes live, so no escape hatch is needed. |
| Rollout order | **Batch-first** | Build engine + batch, run whole library, tune to zero false-positives, *then* wire the hard gates. Confidence before blocking. |
| Link-time depth tier (quick vs deep vs two-tier) | **Deferred** | Decide after measuring real per-file check durations on the box. |

## Environment (verified on the box, 2026-06-27)

- App: `kj-controller`, runs as user `nomad`, `WorkingDirectory=/opt/nomad/kjbox/kj-controller`.
- Config: `render_mode: vlc` (box currently on VLC; mpv is the code default).
  - `download_folder`: `/opt/nomad/YTDownloads`
  - `media_folders`: `/opt/nomad/YTDownloads`, `/opt/nomad/MP4-720p`
  - `media_index_path`: `/opt/nomad/kjbox/kj-controller/media_index.json`
  - `default_audio_device`: `hw:0,0`
- 4TB SSD: `/media/nomad/Nomad4TBOne` (ext4, ~88% full).
- Tools present: `ffmpeg`/`ffprobe` (ffmpeg has a `cdg` demuxer **and** `cdgraphics`
  decoder — it can decode CDG), VLC 3.0.20, mpv 0.37.0.
- Tools **absent**: `mplayer` (the "second player" is really mpv), `Xvfb` (to be installed).
- Display: Xorg live on `:0` (tty7). Checks must avoid `:0`.

**Real launch paths to mirror (decode path identical, output redirected for checks):**

- VLC: `cvlc --extraintf http … --no-video-title-show --aout alsa --alsa-audio-device hw:0,0 --fullscreen` on `:0` (software decode).
- mpv: `mpv --idle --fs --ao=alsa --audio-device=alsa/hw:0,0 --af=@rb:rubberband --input-ipc-server=… --really-quiet --keep-open=no …` (default GPU vo).

## Architecture

### Section 1 — The engine

New module `kj-controller/playability.py`, one class **`PlayabilityChecker`**, used
identically by every caller (link, upload, download, batch). It produces *evidence*, not
policy — callers decide what to do with the verdict.

**API:** `check(path, renderers=('vlc', 'mpv'), depth='deep'|'quick') -> PlayabilityResult`

**`PlayabilityResult`** — a dataclass, JSON-serializable (so the batch can stream it and
the media index can cache it):

```
path, kind (video|audio|cdg_zip), size, mtime, checked_at, elapsed_s
integrity : {ok, has_video, has_audio, vcodec, acodec, container, moov_ok, error}
decode    : {ok, frames_decoded, decode_errors, error}
renderers : { vlc: {ok, reached_playing, frame_captured, frame_nonblack, frame_varies, elapsed_s, error},
              mpv: { …same… } }
cdg       : {ok, zip_ok, has_cdg, has_audio, cdg_decodes, audio_decodes, error}   # zips only
verdict   : { vlc_playable, mpv_playable, overall_ok, reasons[] }
```

**Pipeline** (cheap → expensive; each layer independent and unit-testable):

1. **Integrity** (`ffprobe`): container parses, has a video stream, `moov` present, sane
   duration, codecs. Catches the divebar truncation class instantly.
2. **Decode** (`ffmpeg … -f null`): decode the video stream (sampled or full) — catches
   mid-file corruption and codecs that won't decode at all.
3. **Render proof** (Xvfb + real VLC **and** mpv): see Section 2.
4. **CDG sub-pipeline** for `.zip` files: see Section 2.

The **batch** runs all layers even when an early one fails (full signal — we want to
understand each file). The **live gate** runs short-circuit (stop at first hard failure,
for speed).

**Resource safety:** every subprocess gets a hard timeout and runs at low priority
(`nice`/`ionice`), concurrency 1 by default, and **never** touches `hw:0,0` or `:0`.

### Section 2 — Render-proof layer (Xvfb + VLC + mpv) + CDG

The layer that catches audio-but-no-video: it drives each player and inspects real pixels.

**Off-screen display.** A managed **Xvfb** display (e.g. `:99`), started on demand, reused
across a batch, torn down after. Both players render into that virtual framebuffer — never
the live `:0`. Audio is forced to a null sink (`mpv --ao=null`, `cvlc --aout dummy`), so
`hw:0,0` is never opened. Net: a check can run mid-show without touching screen or sound.

**Per renderer** (same decode path as real playback, output redirected):

- **mpv** → `--ao=null` into `:99`, seek to mid-file, capture 2–3 frames spaced through
  the file via mpv's own screenshot.
- **VLC** → `cvlc --aout dummy` into `:99` with the scene snapshot filter,
  `--play-and-exit`, snapshot at the same spaced timestamps.

**Frame judging** (a pure function, independently testable): a renderer "produces video"
if at least one captured frame is a real, non-uniform image (variance / edge content above
a floor) **and** frames differ across timestamps (motion / lyrics moving). Sampling from
**mid-file** (not 0s) with **lenient** thresholds avoids false-positives on legit black
intros or static title cards. The full-library run calibrates these thresholds before the
checker ever hard-blocks.

**CDG zips** reuse the existing `ZipPlayback` extraction, then: valid zip → has `.cdg` +
audio → ffmpeg `cdgraphics` decodes the `.cdg` + audio decodes → then the VLC/mpv
frame-capture proof on the extracted pair. (mpv's CDG support is per-file uncertain per
the code comment, so its result is recorded, not assumed.)

**Fidelity caveat.** Xvfb has no GPU, so it exercises *software* rendering while the box
plays through the GPU vo. The decode path (where bad files fail) is identical, but to be
safe the confidence run cross-checks the checker's verdicts against the known-good and
known-bad files and we tune until verdicts match reality before trusting the gate.

### Section 3 — The library batch tool

A CLI runner (`scripts/check_library.py`, importing the same `PlayabilityChecker`), run on
the box.

**Scope & SSD-friendliness.** Walks the configured roots — local `/opt/nomad/YTDownloads`
+ `/opt/nomad/MP4-720p` and the 4TB SSD `/media/nomad/Nomad4TBOne` — filtering to media
extensions. Single-threaded at low priority (`ionice -c3`, `nice`) with a configurable
`--throttle` sleep between files so it never hammers the SSD. Show-safe (never touches
`:0`/`hw:0,0`); the big first run is off-show regardless.

**Resumable + incremental.** Each `PlayabilityResult` is appended to a **JSONL** file the
instant it completes — a crash or Ctrl-C loses nothing. A manifest keyed by
`path + mtime + size` lets a re-run skip unchanged already-checked files.
Flags: `--resume`, `--recheck-failed`, `--roots`, `--limit`, `--depth quick|deep`,
`--throttle`.

**Tiered runtime.** The per-renderer frame-capture is the slow part, so the runner can do
a cheap integrity+decode pass over everything first, then the expensive both-renderer
capture — an early "what's outright broken" list, with full render verdicts filling in
after. Exact tiering tuned by the timing measurement.

**Output — built for the mpv-switch decision.** Aggregates the JSONL into:

- a **CSV matrix**: one row per file — `VLC ✓/✗`, `mpv ✓/✗`, kind, vcodec/acodec, reason;
- a **Markdown/HTML summary** with headline buckets: *totally unplayable*, *plays in mpv
  but not VLC*, *plays in VLC but not mpv*, *CDG problems*, plus counts.

The two "plays in one but not the other" buckets are the direct evidence for whether mpv
can become primary and which files need attention first.

### Section 4 — The gates (link / upload / download)

All reuse `PlayabilityChecker`; they differ only in reaction. **Wired last**, after the
library run earns trust.

- **Link** (`link_file` route): before committing, run `check(path,
  renderers=(active_renderer,), depth=…)`. On fail → **hard block**, return a clear UI
  error naming what failed and why (e.g. "VLC could not render video — vcodec hevc, no
  frames captured"). On pass → link proceeds. Both renderers' results are still recorded
  (mpv-switch data keeps accruing) even though only the active one gates.
- **Upload** (`/upload`) and **downloads** (`media.download_video`,
  `download_from_url`, `gen_poller` auto-downloads): check right after the file lands. On
  fail → clear dismissible error, **don't add to the index, and delete the unplayable
  file**.
- **Caching:** results stored in the media index keyed by `path + mtime + size`, so
  re-linking a known-good file is instant (no re-probe).

### Section 5 — Testing

- Pure logic with tiny ffmpeg-generated fixtures: valid mp4, truncated mp4 (moov
  stripped — the divebar class), audio-only mp4 (no video stream), all-black-video mp4,
  valid CDG zip, broken CDG zip (no `.cdg`). Assert per-layer verdicts.
- Frame-judge tested as a pure function on sample PNGs.
- Actual Xvfb/VLC/mpv invocation is skip-marked when those tools are absent (CI), and runs
  for real on the box and the dev machine.
- Batch walker, skip-by-manifest, JSONL append, and aggregation tested against a fake
  checker.

### Section 6 — Rollout (the ~5-day path to next show, ~2026-07-02)

1. Engine + render-proof + CDG.
2. Batch tool.
3. Sanity-check against the known set (4 corrupt divebar files + good samples) — must flag
   the bad, pass the good; measure per-file timing.
4. **Full library run off-show** → analyze report → tune thresholds to zero false-positives
   → pick the link-time depth tier from measured timings.
5. Only then wire the hard gates + index caching.
6. Use the VLC-vs-mpv matrix to plan the mpv-primary switch.

Work happens in this `kjbox` worktree. Nothing deploys to the box without explicit
approval (per kjbox `CLAUDE.md` production-safety rules: no `git push` to main, no service
restart, without permission).

## Out of scope

- Auto-deleting or auto-replacing bad files found by the batch (report-only; KJ decides).
- Fixing/re-downloading the source of bad files (separate concern — divebar download
  pipeline).
- Changing the primary renderer to mpv (this spec *informs* that decision with data; the
  switch itself is separate work).

## Open items deferred to measurement

- Link-time depth tier (quick / deep / two-tier) — decide from measured durations.
- Exact batch tiering and `--throttle` value — tune against SSD throughput.
- Frame-judge thresholds — calibrate against known-good/known-bad during the confidence run.
