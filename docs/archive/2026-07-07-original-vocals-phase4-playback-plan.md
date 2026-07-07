# Phase 4 — Guide-Vocal Playback: Implementation Plan

Concrete build plan for layering the original-vocal guide into KJ playback.
Prereqs done: fetch complete (1,372 originals on device), sync verification →
offset table, vocals separation running. Design + decisions:
`2026-07-06-original-vocals-guide-design.md` and memory `project_kjbox_original_vocals_guide`.

## Decisions locked
- **Source**: `NOMAD-vocals/<brand>…flac` (isolated vocals) preferred; fall back to
  `NOMAD-audio/<brand>…` (full mix) when a vocals stem isn't ready. Resolve by brand.
- **Control**: 3rd "Original Vocals" slider in `.pc-volumes`, default **0% / off**,
  KJ raises to ~30%. Shown only when: renderer is mpv AND current song is a
  sync-**confirmed** NOMAD master with a resolvable guide file. Mirrors pitch-gating.
- **Sync**: pad guide on the fly by the **measured offset** (from the sync report),
  via mpv `adelay` — no pre-padded files.
- **Pitch**: already shared across both streams (proven). Off by default.
- **Deploy**: full autonomy when no show; merge on spectral verify.

## Step 0 — VERIFY the mpv architecture (do first, ~15 min, device must be free)
Proven already (encode-to-file, mpv 0.37): `--external-file=V --lavfi-complex=
"[aid1][aid2]amix=inputs=2:normalize=0[m];[m]rubberband=pitch=P[ao]"` mixes both and
pitches both (200+600Hz → 300+900 at pitch 1.5). **Open question**: can the existing
smooth `--af=@rb` af-command pitch coexist with a lavfi-complex amix (i.e. does the
af chain run on the lavfi-complex `[ao]`)? Test by encoding with lavfi-complex amix +
`--af=@rb:rubberband`, set pitch via IPC `af-command rb set-pitch`, check output pitched.
- **If yes** → Architecture B: amix+adelay+per-branch `volume` in lavfi-complex; keep
  pitch on the existing `@rb` af-command (smooth). Only rebuild lavfi-complex on
  vocals-volume change.
- **If no** → Architecture A (proven): everything in lavfi-complex; rebuild the
  `lavfi-complex` property on pitch OR volume change (brief glitch, acceptable given
  discrete pitch buttons + occasional volume). Default to A if B is unverified.

## Step 1 — Offset table on device (phase 3)
- From the merged sync report (`/tmp/ov_full_report.csv`): build
  `original_vocals_offsets.json` = `{ "NOMAD-0001": {"offset_s": 5.02, "verdict": "confirmed"}, … }`
  for confirmed rows only. Place at `/opt/nomad/downloads/NOMAD-vocals/offsets.json`
  (or `~/kjdata/`). Regenerate as the report updates.
- Add a small loader in the app (config.py / a new `guide_vocals.py`) that reads it
  once + on rescan.

## Step 2 — Resolver (`guide_vocals.py`, new module)
- `resolve_guide(video_path) -> {path, offset_s} | None`:
  1. brand = `^NOMAD-(\d+)` from basename; else None.
  2. offset = offsets[brand] if verdict==confirmed else None → None (no guide).
  3. path = first existing of `NOMAD-vocals/<base>.flac`, else `NOMAD-audio/<base>.*`.
  4. return {path, offset_s} or None.
- Unit-test with a tmp dir of fake files + offsets json.

## Step 3 — Playback wiring
- `routes.py handle_play` (~793): after resolving the master video, call
  `resolve_guide`; pass `guide_file` + `guide_offset_s` through to
  `vlc.play_video(...)` (new kwargs), alongside the existing `audio_file` channel.
- `playback.py PlaybackCoordinator.play_video` (~408): forward kwargs to
  `self.player.play(...)`.
- `mpv_manager.py MpvKaraokePlayer.play` (~303): if `guide_file` and mpv, load with
  the mixing filtergraph (Step 0 arch) — external-file + lavfi-complex amix with
  `adelay=guide_offset_s*1000` on the guide branch + a labeled `volume` filter
  (`@ov`) at initial gain 0. VLC path: ignore guide (no-op).
- New setter `set_guide_volume(vlc_scale)`: Arch B → `af-command @ov set volume <g>`;
  Arch A → rebuild lavfi-complex with new amix weight. Reset guide vol to 0 on each
  new song (like pitch).

## Step 4 — API + status
- `/volume` (routes.py ~938): accept `target: 'vocals'` → coordinator `set_guide_volume`.
- `/status` (~1255): add `original_vocals_volume` + `has_guide_track` (bool: current
  song resolved a guide). Coordinator passthrough properties (playback.py ~356).

## Step 5 — Frontend (vanilla JS, bump pyproject version for cache-bust)
- `templates/index.html` `.pc-volumes` (~79): add a `.pc-slider-row` "Original Vocals"
  range `#vocals-volume` (min 0 max 256, default 0), `oninput=updateVocalsVolume(this.value)`.
- `static/app.js`: `updateVocalsVolume` → `debouncedSetVolume('vocals', v)`; in
  `updateNowPlaying` show/hide the row on `renderer.supports_pitch && data.has_guide_track`.
- `static/style.css`: reuse `.pc-slider-row` styling.

## Step 6 — Verify + ship
- Unit tests: resolver, offset-json loader, mpv command construction (assert the
  filtergraph string for a guide track).
- **Spectral e2e** on device (no show): pick a sync-confirmed track that has a guide
  file, play with guide at ~50%, capture mpv output (or `--o` a few seconds), FFT to
  confirm the guide's vocal energy is present AND time-aligned (cross-correlate the
  captured mix's residual against the guide). Confirm pitch shifts both. No playback
  errors in logs.
- Bump `pyproject.toml` version (frontend cache-bust). PR (+`@coderabbitai ignore`),
  merge, autodeploy restarts kj-controller (verify /status 200 + renderer mpv after).

## Safety
- Inert for every song without a resolved confirmed guide (slider hidden, no filtergraph
  change) → cannot affect normal playback. VLC engine unaffected.
- Weak-vocals-flagged tracks: guide resolves to full-mix or near-silent vocals — harmless
  (just a poor guide) until the input is re-selected.
