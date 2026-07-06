# Original-Vocals Guide — data tooling (phases 1–2)

Tooling to harvest the **original recording** (the original singer) for each
NOMAD-produced track and align it to the released karaoke video, so it can later
be layered under playback as an adjustable-volume sing-along guide.

Design spec: `docs/archive/2026-07-06-original-vocals-guide-design.md`.

## Pipeline

```
list ──▶ classify ──▶ review ──▶ fetch ──▶ verify ──▶ (deploy)
        (phase 1)               (phase 1) (phase 2)
```

### 1. list (metadata only, no downloads)

```bash
rclone lsf -R --files-only --format "sp" --separator "||" \
  "andrewdropboxfull:/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized/" \
  > tracks_listing.txt
```

### 2. classify → manifest + fetch plan

```bash
python3 classify.py tracks_listing.txt --out-dir data/
```

Writes `data/manifest.csv`, `data/manifest.json`, `data/fetch_plan.tsv`. Each
folder is tiered:

| Tier | Meaning | Action |
|------|---------|--------|
| HIGH | clear era marker or `Artist - Title` filename | auto-fetch |
| MED | one plausible audio file after exclusions | auto-fetch |
| LOW | several candidates; best guess picked | auto-fetch, confirm later |
| NO_SOURCE | track was sourced from a pre-made karaoke — no original exists | skip (no guide) |
| GAP | no usable audio | manual sourcing |

Measured coverage of the full 1,505-folder catalog: **82.9% HIGH, 1,372
auto-fetchable, 132 NO_SOURCE, 1 GAP.**

### 3. review

Open `data/manifest.csv`; for MED/LOW rows confirm or override `chosen_path`.
Phase-2 correlation independently rejects wrong picks, so this is a light pass.

### 4. fetch → device staging folder

```bash
# on the KJ device (bytes never touch the Mac):
bash fetch_runner.sh fetch_plan.tsv /opt/nomad/downloads/NOMAD-audio 8
```

Idempotent + resumable: skips files already present, downloads to `.part` and
renames on success. Files land as `NOMAD-#### - Artist - Title.<ext>`.

> **⚠️ Fetch prerequisite (blocker as of 2026-07-06):** the Dropbox app behind the
> `andrewdropboxfull` remote (app id 6174833) can list filenames but lacks the
> `files.content.read` scope, so downloads fail. Enable that scope at
> dropbox.com/developers/apps → your app → Permissions, then
> `rclone config reconnect andrewdropboxfull:`. The classifier only needs listing
> (already works); only the fetch needs the download scope.

### 5. verify → aligned audio (phase 2)

```bash
python3 verify_sync.py \
  --audio-dir /opt/nomad/downloads/NOMAD-audio \
  --video-dir /opt/nomad/downloads/NOMAD-720p \
  --out-dir   /opt/nomad/downloads/NOMAD-audio-synced --emit
```

Cross-correlates each original against its released video to measure the true
title-card offset (not assumed), corroborates with intro-silence + duration
checks, marks each `confirmed`/`needs-review`, and (with `--emit`) writes a padded
copy aligned sample-accurately to the video timeline. Requires `ffmpeg`/`ffprobe`
and `numpy`.

## Tests

```bash
python3 -m pytest scripts/original_vocals/ -q
```

Classifier tests are pure/stdlib; verifier tests generate audio with ffmpeg and
skip if it's unavailable.
