# Vocal-Guide Alignment — design spec (2026-07-08)

## Goal
Align each original-vocals **guide** with its NOMAD karaoke **master video** to
**zero-tolerance** accuracy (a ≤100 ms flam between the guide vocal and the karaoke
music is unacceptable), replacing the current fixed-5 s padding MVP with a
**measured, per-track** offset — and **human-verifying** every track that the
automatic measurement isn't certain about.

## Why (background)
- The shipped feature (PR #181, v0.75.0) mixes a guide under the karaoke via mpv
  `--lavfi-complex` amix; it reads pre-padded guides from device `NOMAD-vocals-padded/`.
- Those guides are currently padded a **flat 5 s lead + 5 s trail** (MVP). That is
  wrong in general: NOMAD videos are `[title-card intro] + [instrumental] + [outro]`,
  the intro is ~5 s but **not exactly uniform** (quiet song intros push it out), and
  the outro is **era-dependent** (early era ~0 s; Frightened Rabbit tracks have an
  intro but **no** outro; web era ~5 s). A duration-only check is insufficient (a
  10 s total overhead split 8+2 still starts the guide 3 s early), and silence
  detection alone is insufficient (some songs have silent/gappy starts).
- A cross-correlation sync engine already exists and is unit-tested:
  `scripts/original_vocals/verify_sync.py`. It was written for M1 but only ever run
  against the pre-correction inputs. This design **reuses and adapts** it.

## Non-goals
- No change to the playback feature code — it already reads `NOMAD-vocals-padded/`;
  we only make those files correctly aligned (and remove guides for excluded tracks).
- karaoke-gen write-path (auto-emitting original+vocals on new renders) — later; but
  this pipeline is designed to be re-runnable so new tracks flow through it.

## Architecture — measure → classify → human-verify → emit

### A. Measurement (adapt `verify_sync.py`)
Per track, cross-correlate the **original full-mix** (`NOMAD-audio/`) against the
**master video audio** (`NOMAD-720p/`) to measure the lead-in **offset** (8 kHz
analysis → sub-ms; normalized correlation peak = confidence). The guide vocals were
stem-separated from that same original, so they **share its timebase** — the offset
measured on the original is the offset to apply to the guide. (We correlate on the
original because it shares the instrumental content with the video and locks sharply;
the guide alone — vocals vs the video's instrumental — would not.)
- Adaptation: `emit_padded` pads the **guide** (not the original) by the measured
  offset, trimmed/padded to the video duration.
- Existing pure math (`best_lag`, `rms_db`, normalized peak) is unchanged + already
  unit-tested.

### B. Classification
Each track → `confirmed` or `needs-review`, using the existing verdict logic:
- `confirmed`: normalized peak ≥ 0.30 **and** offset in a sane 2–15 s range **and**
  corroborated by intro-silence / audio-onset. The sharp correlation makes the offset
  sample-accurate.
- `needs-review`: weak/ambiguous peak, insane offset, or method disagreement. This
  deliberately catches **two** failure modes:
  1. correct source, unusual-but-fixable intro/structure → fix via review clips;
  2. genuinely **different recording** (cover-band instrumental, wrong source) that
     can *never* align → **exclude** (no guide), same as the short-versions.

### C. Human verification via pre-rendered clips
- **Which tracks:** every `needs-review` track **plus a random ~7 % spot-check of
  `confirmed`** (sanity-check the confidence metric without sitting through ~1,400).
- **Clip content:** locate the guide's **first vocal onset** (first sustained energy
  in the isolated-vocals guide); render an mp4 of ~3 s-before → ~12 s-after that
  moment = the master video with the **guide mixed in at ~65 %** (loud enough that a
  ≤100 ms flam is obvious) at the candidate offset. Filenames encode the offset,
  e.g. `NOMAD-0300 - Artist - Title__off=4.980s.mp4`.
- **Variant comb (flagged/fixable tracks):** render a coarse comb first — measured,
  ±100, ±200 ms — then a fine ±25 ms comb around whichever the user picks, converging
  to <100 ms in 1–2 rounds. Spot-check `confirmed` tracks get a single clip at the
  measured offset (just confirming).
- **Delivery + decisions:** clips land in a Dropbox review folder the user can play
  anywhere. The user records outcomes in `align_decisions.csv`, per track:
  `confirm` | `offset_ms=<n>` | `exclude` | `needs-finer`.

### D. Offset store + emit
- Source of truth: `align_offsets.csv` — `brand, offset_s, video_dur, source
  (measured|human), verdict, status (active|excluded)`. Written by the measurement
  run; updated from `align_decisions.csv` review outcomes.
- **Emit:** for every `active` track, render the aligned guide = `silence[offset] +
  guide`, trimmed/padded to `video_dur`, → overwrite device `NOMAD-vocals-padded/`.
  This replaces the fixed-5 s pad. `excluded` tracks get their guide files removed
  (raw + padded, device + Mac) so `has_vocals_track` goes false — clean skip.
- Playback feature: **no code change.**

## Data / files
- Inputs (device): `/opt/nomad/downloads/NOMAD-audio` (originals, correlation ref),
  `/opt/nomad/downloads/NOMAD-720p` (master videos), `/opt/nomad/downloads/NOMAD-vocals` (raw guides).
- Working: `align_offsets.csv`, `align_decisions.csv`, review-clips folder (→ Dropbox).
- Output (device): `/opt/nomad/downloads/NOMAD-vocals-padded` (aligned guides).
- New tooling under `scripts/original_vocals/` (device-run parts scp'd like the M1 tools).

## Scope + ordering
1. Prereq: `pip install numpy` on the device (needed by `verify_sync`).
2. Measure all ~1,467 active tracks → `align_offsets.csv` + confirmed/needs-review split.
3. Render review clips (all flagged + ~7 % confirmed spot-check) → Dropbox review folder.
4. User reviews → `align_decisions.csv`; fine-comb rounds for any `needs-finer`.
5. Emit aligned guides for all `active` tracks → re-pad device; remove `excluded`.
6. Manually-fixed mismatch tracks (e.g. trimmed **0307**) re-enter at step 2 once their
   `Original` is corrected.
7. Current fixed-5 s pads stay live until replaced (feature keeps working meanwhile).

## Edge cases / notes
- **With-Vocals-extracted originals** (73 tracks, M1) are already video-aligned (their
  audio came from the video) → offset ≈ 0; the correlation should confirm this.
- **Cover-band / different-recording** tracks: correlation stays weak at every lag →
  `needs-review` → user excludes.
- Re-runnable + idempotent (skip already-emitted unless offset changed).
- Device audio output during clip rendering is offline (ffmpeg), no playback disruption.

## Testing
- `verify_sync` pure math already unit-tested (`best_lag`, `rms_db`, normalized peak).
- Add unit tests: first-vocal-onset windowing, `align_offsets`/`align_decisions`
  parse+merge, emit filename/trim logic, exclude handling.
- Validation: the ~7 % spot-check is itself the empirical confidence check on the
  auto-`confirmed` set.

## Open risk
- If the video's instrumental for some era was NOT stem-separated from the original
  (e.g. a licensed/cover instrumental), correlation fails → those land in
  `needs-review` and the user excludes — acceptable (they can't align anyway).
