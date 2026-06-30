# Playability Checker — Deterministic & Reliable Verdict (Design)

**Date:** 2026-06-30
**Status:** Approved (design); implementation pending
**Worktree:** `kjbox-playability-deterministic`
**Branch:** `feat/sess-20260630-0130-playability-deterministic`

## 1. Problem

The full-library playability sweep (kjbox v0.40.0, launched 2026-06-28) flagged ~2,400
files in its first ~6% of progress. A manual review of a **166-file sample** pulled to a
laptop (all 66 flagged internal/video files + a stratified 100 of the flagged SSD CD+G zips)
found that **~90% of the flags are false positives**:

| Set | Reviewed | False positives | Genuinely broken |
|---|---|---|---|
| Internal video | 66 | **58 (88%)** | 8 |
| SSD CD+G | 100 | **91 (91%)** | 9 |
| **Total** | **166** | **149 (90%)** | **17** |

Even whole commercial discs flagged 100% (e.g. the 12-track `CDG-SP374`, the 708-flag
`FLIPN976` Philippine super-disc) play fine. The checker cannot be trusted as a delete list,
and the live link/upload/download gates (which share the CD+G verdict) are currently
false-rejecting playable commercial CD+G discs.

### Evidence method
Each file was re-tested with `ffmpeg`/`ffprobe` on the laptop as a neutral third decoder.
ffmpeg is gold-standard for video and MP3; for CD+G a "lenient" pass (decode to completion,
ignore *recoverable* per-frame warnings, require exit-code 0 + frames produced) was used.
Review artifacts live in `~/playability-review/_meta/` (`manifest.tsv`,
`internal_ffmpeg_triage.tsv`, `ssd_cdg_triage.tsv`, `ssd_lenient.tsv`).

## 2. Root causes (all isolated, with code refs)

| # | Bug | Location | FPs |
|---|---|---|---|
| 1 | Render frame-capture is a **hard gate** — headless Xvfb pixel-proof is environment-fragile | `compute_verdict` `playability.py:336-344` | 51 video |
| 2 | **180 s decode timeout** too short once every cmd is wrapped in `nice -n19 ionice -c3` | `decode_video` `playability.py:149` | 15 video |
| 3 | **`-xerror`** aborts ffmpeg on the *recoverable* `tile is out of range` CD+G warning | `decode_file` `playability.py:152` (used by `check_cdg`) | ~57 CD+G |
| 4 | CD+G audio detection is **`.mp3`-only**, misses `.m4a`/`.wav`/etc. | `check_cdg` `playability.py:169` | 7 CD+G |
| 5 | **Fragile unzip** crashes (`zlib Error -3`) where Python `zipfile` opens fine | `check_cdg` → `ZipPlayback` (`zip_playback.py`) | 6 CD+G |

The genuinely-real signals — `not a valid zip` (Python also fails), `no video/audio stream`,
`moov atom not found`, truly-undecodable streams — all come from the **deterministic
integrity layer**, which is correct and is exactly what the live gates already use
(`renderers=()` ⇒ `overall_ok = bool(base_ok)`, `playability.py:345`).

### mpv ⇄ CD+G correction
A separate finding (memory `project_kjbox_playability_checker`) recorded "mpv CANNOT render
CD+G (exit 2) → route CD+G to VLC." **That conclusion was a test artifact.** The checker
hands mpv a *bare* `.cdg` (`playability.py:266`, `--ao=null`, no audio) — a CD+G stream has
no inherent timeline, so mpv can't seek to the mid-file capture point and aborts. Production
proved mpv *can* render CD+G via `loadfile <cdg>` + `audio-add <mp3>` (`mpv_manager.py:282,297`,
shipped v0.42.0). The checker's mpv test must attach the companion audio or the VLC-vs-mpv
matrix keeps lying about mpv.

## 3. Design

**Principle:** the verdict is derived **only** from deterministic signals — container
integrity (`ffprobe`) + decode-to-completion (`ffmpeg` exit code) — treating *recoverable*
decoder warnings as non-fatal. The headless render frame-capture is **removed from the
verdict** and kept only as a recorded diagnostic.

### 3.1 Verdict — `compute_verdict` (`playability.py:306-345`)
- `overall_ok = bool(base_ok)` for **all** kinds. Stop folding `frame_nonblank` into
  `overall_ok`.
- The `"<renderer>: no video frame rendered"` string (`:338`) becomes a non-gating
  diagnostic recorded on the renderer result, never appended to gating `reasons`.
- `res.renderers` continues to be populated (matrix/diagnostic intact).
- The live gates already pass `renderers=()`, so they are unaffected; this fixes the batch
  and tier-2.

### 3.2 Video decode — `decode_video` (`playability.py:141-150`)
- **Remove `-xerror`.** Base `ok` on `returncode == 0`, not on counting stderr lines that
  merely contain "error". (Keep a small *fatal*-pattern allowlist only if needed for clearly
  unrecoverable demux failures; default is exit-code.)
- **Timeout scaled to duration:** `timeout = max(180, ceil(duration * FACTOR))` (decision (a):
  scale, not uncapped). `FACTOR` accounts for the `nice/ionice` slowdown; tune from the
  measured per-stage timings. Quick-tier (5 s sample) is unaffected.

### 3.3 CD+G — `check_cdg` / `decode_file` (`playability.py:152-213`)
- **Remove `-xerror` from the `.cdg` decode.** `cdg_decodes = (rc == 0 and frames_produced)`;
  `tile is out of range` no longer fatal.
- **Broaden audio detection** to `{.mp3,.m4a,.wav,.ogg,.flac,.opus,.aac,.mp2}`; `has_audio`
  is false only when no audio file of any supported type exists.
- **Robust unzip:** when `ZipPlayback.extract_*` raises a decompress error (`zlib Error -3`),
  fall back to Python `zipfile` extraction (which handles these). Only a true `BadZipFile`
  (Python also fails) maps to `"not a valid zip"`.

### 3.4 mpv CD+G render-diagnostic
- Thread the extracted `.mp3` into the mpv capture path; extend `build_mpv_capture_cmd` to
  accept an optional `audio_file` and emit `--audio-file=<mp3>` (CLI mirror of production's
  `audio-add`). For CD+G, `render_check` for mpv receives both the `.cdg` and the audio.
- Diagnostic-only (never gates). Verify on-device (mpv 0.37) that `--audio-file` attaches and
  a non-blank frame is captured.

### 3.5 Gating posture (decision: block live, never delete)
- **link / upload** (`routes.py` `_playability_gate`): keep hard-block (422) — recoverable.
- **download paths** (`media.py` `download_video` `:276-281`, `download_from_url` `:366-371`):
  change delete-on-fail → **quarantine** (leave the file in place, record a warning/flag);
  never auto-delete on an automated verdict.

### 3.6 Re-scan batch (decision (b): decode-only by default)
- Batch default becomes **decode-only** (no render) — fast (days, not weeks), and the verdict
  no longer needs render. Add an opt-in `--render-matrix` flag to run the VLC-vs-mpv capture
  pass for the eventual mpv-primary evaluation.

## 4. Acceptance criteria (validation fixture)

The 166 reviewed files + their ffmpeg ground-truth become a **regression fixture**:
- A committed manifest of `path → expected_overall_ok` (the lenient ffmpeg verdict).
- A runner that points the *fixed* checker at the local review set
  (`~/playability-review/{internal,ssd}`) and asserts it flags **only** the ~17 known-bad and
  passes the other 149 (zero of the 149 known-good may be flagged; the known-bad must be
  caught).
- A small committed sample (a few representative good + bad files, license permitting) for CI;
  the full 166-file run is a local/manual gate.

**Definition of done:** fix merged; fixture passes; live link/upload/download fix deployed
(CD+G no longer mis-rejected); full-library re-scan re-run advisory/report-only; flag list
reviewed before any cleanup.

## 5. Testing
- Unit: `overall_ok == base_ok` (render never gates); `decode_video` has no `-xerror` and
  passes on recoverable-warning fixtures; timeout scales with duration; `check_cdg` accepts
  non-mp3 audio; unzip falls back to `zipfile` on `zlib` error; `build_mpv_capture_cmd`
  includes `--audio-file` when given audio.
- Integration: the 166-file regression fixture.
- On-device smoke (NomadPC, off-show): a CD+G renders on mpv with audio attached → non-blank
  frame; a known-truncated file still fails; a known-good commercial CD+G passes.

## 6. Out of scope
- The live gates' deterministic logic (already correct).
- Overlay/player runtime, catalog, rotation.
- Re-tuning frame thresholds for gating (render no longer gates).

## 7. Rollout
Pause batch (done) → implement → fixture passes → deploy live-gate + quarantine → re-run
full library advisory → review small flag list → cleanup. kjbox autodeploy is OFF: deploy =
manual `git pull` + (backend) `systemctl restart kj-controller` (interrupts playback — off-show
only). Frontend (none here) would just need a hard-refresh.
