# Original Vocals Guide

Layer the **original singer's isolated vocals** under a NOMAD karaoke master during
playback, at an adjustable **"Original Vocals"** slider (default 0 % / off; the KJ
raises it ~30 %) as a sing-along guide. Pitch shifts both streams together. Shipped
in kj-controller **v0.75.0** (#181); per-track alignment applied 2026-07-08 (#182/#183).

> ⚠️ **DISABLED by default since v0.86.0 (2026-07-17).** The guide mix (and the
> rubberband pitch shift) are gated behind config `audio_processing_enabled`
> (**default `false`**). While off, mpv plays only the raw selected audio track and
> the "Original Vocals" slider + pitch controls are hidden. This was done because the
> live playback path had two bugs — the guide sometimes played *instead of* the
> instrumental, and it desynced when the slider was raised — both from a race in
> `MpvKaraokePlayer.play()` where `_apply_vocals_mix()` runs before the master's
> embedded audio track is demuxed (`instrumental=None`). **The guide files are
> correctly aligned (~1 ms)** — it's a playback bug, not a data problem. Re-enable
> with `"audio_processing_enabled": true` **after fixing the race** (build the mix
> only once both tracks resolve, so the guide is engaged from t≈0). Everything below
> describes the feature as designed, for when it's re-enabled.

## How it enables — automatic, no config

When you play a file, `routes._resolve_vocals_guide` decides whether a guide is available:

1. Is the file a NOMAD master? (`naming._MASTER_RE` → `NOMAD-####`). If not → no guide.
2. Look in the guide dir — `cfg['vocals_guide_dir']`, else the `NOMAD-vocals-padded/`
   sibling of the master's folder (`/opt/nomad/downloads/NOMAD-vocals-padded`).
3. Match a guide by **brand prefix** (`NOMAD-#### - *`), so master/guide filename
   normalisation differences don't matter.

If a guide is found, mpv loads it as a second audio track and the **Original Vocals**
slider appears (default 0). Raising it mixes the guide under the karaoke via mpv
`--lavfi-complex` amix; pitch (rubberband `--af=@rb`) shifts both streams. If no guide
exists (non-NOMAD file, or a brand with no/excluded guide) the slider stays hidden —
a clean skip. `/status` exposes `has_vocals_track` and `original_vocals_volume`.

**So: play a NOMAD master that has a guide in `NOMAD-vocals-padded/` → the slider is
just there.** Nothing to toggle or configure.

## The guide dataset (device: `/opt/nomad/downloads/`)

| Dir | Contents |
|---|---|
| `NOMAD-720p` | Master videos. Structure: `[~5s silent title card] + song + [~5s end]` |
| `NOMAD-audio` | Verified original full-mix audio (M1) — the correlation reference |
| `NOMAD-vocals` | Raw isolated-vocal guides (M2) |
| `NOMAD-vocals-padded` | **Aligned guides the feature reads** = `silence[offset] + guide`, trimmed to the video's duration. ~1,459 guides as of 2026-07-08 |

Only `NOMAD-vocals-padded` is read at playback. Masters have a **fixed ~5 s silent
title card** (karaoke-gen `DEFAULT_INTRO_STYLE.video_duration = 5`, silent `anullsrc`),
so most guides are offset ~5 s; the alignment pipeline catches the ~5 % with longer intros.

## Generating / aligning / excluding guides (`scripts/original_vocals/`)

Run on the device with the **kj-controller venv python**
(`/opt/nomad/kjbox/kj-controller/venv/bin/python` — has numpy; the system
`/usr/bin/python3` does **not**):

1. **`align_measure.py`** — cross-correlate `NOMAD-audio` vs `NOMAD-720p` for a per-track
   lead-in offset → `align_offsets.csv` (verdict `confirmed` / `needs-review`).
2. **`align_clips.py`** — render **first-60 s** review clips (the video from t=0 with the
   guide mixed in via `adelay=offset`, mirroring the emit) + an `align_decisions.csv`
   template. `--only`/`--fine` render a fine variant comb for follow-up rounds.
3. **Human review** — play the clips, fill `decision`:
   `confirm` | `offset_ms=<n>` | `exclude` | `needs-finer`.
4. **`align_apply.py --decisions …`** — emit aligned guides to `NOMAD-vocals-padded`
   (confirmed + human-decided), remove excluded (raw + padded). An **un-decided
   `needs-review` track is skipped**, never shipped with an unverified offset.

Excluded as of 2026-07-08: 8 tracks (parody / live / pitched / unique recordings that
can't correlate to the original) — they simply get no guide.

## Automatic guide distribution (write-path, v0.77.0)

Newly rendered NOMAD tracks now get their guide **automatically** — no re-run of the
`scripts/original_vocals/` pipeline. The loop:

1. **karaoke-gen** (at finalize) has the isolated `mixed_vocals` stem + the job's exact
   intro duration, so it emits `silence[intro] + vocals` (capped to the master) and pushes
   it to `gs://nomadkaraoke-divebar-files/files/Nomad Karaoke/vocals-padded/{brand} - …flac`
   — the same alignment this pipeline produces, but computed for free (no correlation).
2. **This device** — the 5-minute master-sync timer (`scripts/sync_masters.py`) now pulls
   that prefix into `NOMAD-vocals-padded/` alongside the masters, so the guide is present
   before/with the master. Playback then works exactly as above.

**Additive-only:** the vocals sync **never reconcile-deletes**, so the existing ~1,459
retro-fit guides (absent from the initially near-empty GCS prefix) are preserved. Known
limitation: a *recycled* brand's stale guide isn't auto-removed (future: backfill existing
guides to GCS, then enable reconcile). No `/rescan` poke — guides are glob-resolved at play
time, never indexed.

**Activation (device config):** set `"vocals_sync_enabled": true` (default False). Optional
overrides: `vocals_sync_source`, `vocals_sync_dest`, `vocals_sync_delete_removed`.

## Gotchas

- **Clear `--lavfi-complex` BEFORE `loadfile`**, not after — otherwise `af-command rb`
  breaks and the *next* song loses pitch.
- **Stop with `POST /control {"action":"stop"}`** — a raw mpv `stop` is auto-replayed by
  crash-recovery (can blast a song at full volume).
- Emitting flac to a `<name>.part` temp file needs explicit `-f flac` (ffmpeg otherwise
  guesses the container from `.part` and fails). The karaoke-gen write-path uses the same
  `-f flac` trick.
- The write-path handles **new** renders only; a historical gap still needs the
  `scripts/original_vocals/` pipeline (or a future GCS backfill).

## Design / history

`docs/archive/2026-07-0{6,7,8}-original-vocals-*.md` and
`docs/archive/2026-07-08-vocal-guide-alignment-*.md`.
