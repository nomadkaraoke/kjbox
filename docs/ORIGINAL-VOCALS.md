# Original Vocals Guide

Layer the **original singer's isolated vocals** under a NOMAD karaoke master during
playback, at an adjustable **"Original Vocals"** slider (default 0 % / off; the KJ
raises it ~30 %) as a sing-along guide. Pitch shifts both streams together. Shipped
in kj-controller **v0.75.0** (#181); per-track alignment applied 2026-07-08 (#182/#183).

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

## Gotchas

- **Clear `--lavfi-complex` BEFORE `loadfile`**, not after — otherwise `af-command rb`
  breaks and the *next* song loses pitch.
- **Stop with `POST /control {"action":"stop"}`** — a raw mpv `stop` is auto-replayed by
  crash-recovery (can blast a song at full volume).
- Emitting flac to a `<name>.part` temp file needs explicit `-f flac` (ffmpeg otherwise
  guesses the container from `.part` and fails).
- **No karaoke-gen write-path yet** — newly rendered NOMAD tracks won't get a guide until
  the pipeline above is re-run over them.

## Design / history

`docs/archive/2026-07-0{6,7,8}-original-vocals-*.md` and
`docs/archive/2026-07-08-vocal-guide-alignment-*.md`.
