# Original-Vocals Guide — Design Spec

**Status:** Design approved (brainstorming). Phase 1 tooling + Phase 4 feature implemented autonomously 2026-07-06; fetch of the actual audio blocked on a Dropbox app scope (see Phase 1 §Blocker).
**Worktree:** `kjbox-original-vocals-guide` · **Branch:** `feat/sess-20260706-1849-original-vocals-guide`

## Goal

During a NOMAD-produced karaoke song, optionally layer the **original recording's
audio** (the original singer) underneath the karaoke video at an adjustable
volume (e.g. 30%), as a sing-along guide for singers who aren't confident. When
the KJ pitch-shifts the song (to suit a singer's range), the shift must apply to
**both** the karaoke instrumental and the layered original, so they stay in tune
with each other.

This is especially useful with pitch-shift: a nervous singer can lean on the
original vocal as a reference while it's transposed to their key.

## Non-goals (this iteration)

- Isolated-vocal-only guide (we layer the **full original mix**; if the doubled
  instrumental sounds muddy, the lead vocal can be separated later with the
  existing `python-audio-separator` tool and swapped in — no design change needed).
- A guide for tracks that were themselves sourced from a pre-made karaoke/
  instrumental (no original vocals ever existed — they simply won't offer the
  slider).

## Architecture decomposition (build in order)

| Phase | Deliverable | Interface out |
|------:|-------------|---------------|
| **1 — Harvest** | Identify + fetch one original full-mix file per NOMAD brand code from Dropbox into a clean, consistently-named folder on the device. | `/opt/nomad/downloads/NOMAD-audio/NOMAD-#### - Artist - Title.<ext>` + a manifest. |
| **2 — Sync & verify** | Prove each original aligns with its released video; produce a padded, timeline-aligned copy; mark confirmed vs needs-review. | Padded audio aligned to the karaoke video timeline. |
| **3 — Deploy & register** | Make the aligned audio resolvable by brand code at play time. | `karaoke_video → original_audio` lookup. |
| **4 — Playback** | Layer the original as a 2nd audio stream inside mpv, with an independent volume slider; shared pitch. | KJ UI feature. |

Each phase is independently useful and testable. Phase 4 is inert until audio
files exist, so it is safe to ship ahead of the data.

---

## Phase 1 — Harvest & consolidate

### Source of truth

Dropbox `andrewdropboxfull:/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized/`
— 1,505 `NOMAD-####` folders (0001–1507), 46,033 files. Two years of
inconsistent naming across three production eras (hand-made → early CLI → web
platform), so the original-input file must be identified heuristically.

### Pipeline (manifest-first, not fetch-first)

1. **list** — `rclone lsf -R --files-only --format "sp" --separator "||"` → a
   `size||path` text listing (metadata only, no downloads).
2. **classify** — `scripts/original_vocals/classify.py` scores each folder's
   audio files and assigns a confidence tier + picks the single original mix.
3. **review** — HIGH auto-accepted; MED/LOW/edge surfaced in `manifest.csv` for a
   human to confirm/override. Phase-2 sync verification is a second safety net
   that rejects a wrong pick automatically.
4. **fetch** — `scripts/original_vocals/fetch_runner.sh` copies each accepted row
   to the staging folder, renamed `NOMAD-#### - Artist - Title.<ext>` (mirrors the
   master-video naming so the `^NOMAD-(\d+)` master regex resolves it). No
   transcoding — original bytes preserved.

### Classifier heuristic

For each folder, collect audio files (`flac/wav/mp3/m4a/opus/aac/ogg/wma/webm`), then:

- **Exclude** derived/output artifacts by filename substring: `karaoke`,
  `instrumental`/`instr.`, `vocals`/`backing`/`+bv`, `final`/`title`/`with vocals`,
  separation-model tags (`mdx`, `uvr`, `roformer`, `model_bs_`, `mel_band`,
  `demucs`), `(filtered)`, `acapella`, stems, `(click)`, `(guide track)`.
- **Score** survivors by era marker — `(Original)`/`flacfetch`/`(uploaded)` (100),
  `(Youtube …)`/`(Local)`/streaming-source (90) — plus filename==`Artist - Title`
  (80, quote-normalised, track-number-stripped). Format rank (flac>wav>mp3>webm)
  and size are tiebreaks only.
- **Tier:** HIGH (any strong marker), MED (one leftover after exclusions), LOW
  (several leftovers → best guess), NO_SOURCE (only karaoke/instrumental present →
  original never existed), GAP (no usable audio).

### Measured coverage (full catalog)

| Tier | Count | % |
|------|------:|---:|
| HIGH (turnkey) | 1,248 | 82.9% |
| MED (one leftover) | 58 | 3.9% |
| LOW (needs a pick) | 66 | 4.4% |
| NO_SOURCE (karaoke-sourced; no guide possible) | 132 | 8.8% |
| GAP (genuinely missing) | 1 | 0.1% |

**Auto-fetchable (HIGH+MED+LOW): 1,372.** Of the ~1,373 tracks that *can* have a
guide, ~91% are turnkey; ~124 need a light human confirm. Only 2 numbering gaps
(532, 1247) and 1 true content gap (NOMAD-1253, stems-only — could reconstruct
from its lead-vocal stem later).

### Staging location

`/opt/nomad/downloads/NOMAD-audio/` on the device (root nvme, 358 GB free; beside
the master videos at `/opt/nomad/downloads`). This is the eventual home, keeping
Phase 3 trivial, and the released videos are already on the device for Phase 2.

### ⚠️ Blocker (fetch)

The Dropbox app behind `andrewdropboxfull` (app id 6174833) has **list**
permission but not `files.content.read`, so it can enumerate filenames (the whole
classifier is built on that) but cannot **download** content. The other rclone
remotes don't help (`dropbox`/`vocalstar` are sandboxed to karaoke-gen's job
folders; `googledrive` doesn't hold these). GCS holds only output videos.

**Fix (~2 min, requires Dropbox login):** enable `files.content.read` on app
6174833 at dropbox.com/developers/apps → Permissions, then
`rclone config reconnect andrewdropboxfull:` to refresh the token. The fetch is
built and resumable: on the device, `~/bin/rclone` + `/tmp/fetch_plan.tsv` +
`/tmp/fetch_runner.sh` → `bash /tmp/fetch_runner.sh /tmp/fetch_plan.tsv /opt/nomad/downloads/NOMAD-audio 8`.

---

## Phase 2 — Sync & verify

The released karaoke video = a title-card intro (nominally 5 s of silence) +
instrumental + a tail. To layer the original perfectly we must know the exact
offset per track. We do **not** assume 5 s.

**Method (`scripts/original_vocals/verify_sync.py`):**

1. **Extract** the video's audio (the karaoke instrumental) and load the original.
2. **Cross-correlate** the original against the video audio to measure the true
   lead-in offset in milliseconds. The instrumental is stem-separated from the
   original, so their non-vocal content aligns and correlates sharply.
3. **Corroborate** with two cheap checks: (a) the first N seconds of the video are
   near-silent; (b) `video_duration ≈ original_duration + intro + tail`.
4. **Emit** a padded original (`silence[offset] + original`) trimmed/extended to
   the video length, plus a verdict: `confirmed` (correlation peak sharp and
   consistent with the silence/duration checks) or `needs-review`.

A wrong Phase-1 pick (LOW tier, or a mislabelled file) fails to correlate → auto
`needs-review`, so Phase 2 doubles as a Phase-1 validator. Output feeds a small
human spot-check across eras before trusting the batch.

Padded output alignment is what guarantees "never out of sync" — it's measured,
not assumed.

---

## Phase 3 — Deploy & register

The staged/padded audio already lives on the device. Resolution at play time:
a NOMAD master video `NOMAD-#### …` maps to `NOMAD-audio/NOMAD-#### ….<ext>` by
brand code (`^NOMAD-(\d+)`). Implemented as a resolver helper (sibling-file
convention, mirroring the existing CDG sibling-audio resolution) rather than a new
DB column, so it needs no schema migration and works the moment files appear.
Optionally mirror the padded audio to GCS parallel to `MP4-720p/` and extend
`sync_masters.py` for offsite backup + auto-distribution to future devices.

---

## Phase 4 — Playback (dual-stream + slider + shared pitch)

### Constraints (from code audit)

- **mpv-only.** Pitch is an mpv rubberband filter (`--af=@rb:rubberband`, set via
  `af-command rb set-pitch`); VLC has no pitch. The device runs mpv. Gate the
  feature on `supports_pitch` + a "has vocals track" flag.
- **Single-process ALSA.** The HDMI output is exclusive to one process — a second
  player is impossible. The original must be a **second audio input inside the one
  mpv process**, mixed with `--lavfi-complex`/`amix`, feeding the existing `@rb`
  filter. Consequently the existing `set-pitch` command pitches **both** streams
  for free.

### Design

- **Resolve** the original-audio sibling for a NOMAD master in `handle_play`
  (`routes.py`), pass its path via a new `vocals_file` kwarg alongside the existing
  `audio_file` channel → `PlaybackCoordinator.play_video` → `MpvKaraokePlayer.play`.
- **Mix** in mpv: load the video, then add the original as a second audio track and
  route both through a filtergraph
  `[vid_audio][orig_audio]amix=inputs=2:weights=...` → `@rb` (rubberband) → output.
  The original branch gets its own `volume` filter (labelled, e.g. `@ov`) so its
  gain is independent.
- **Volume:** a new `/volume` target `vocals` → coordinator/player setter →
  `af-command @ov set volume <gain>` (mirrors the pitch `af-command` pattern).
- **Pitch:** unchanged — `af-command rb set-pitch` now affects the mixed bus.
- **Status/UI:** `/status` gains `original_vocals_volume` + `has_vocals_track`; a
  third slider "Original Vocals" in `.pc-volumes` (`templates/index.html`),
  defaulting to 0% (off) and shown only when mpv + a vocals track exists.

### Default behaviour

Off by default (0%). The KJ raises it per singer request. Because it's gated on a
resolvable vocals file, the feature is inert for every song until Phase 1–3 land
the audio — safe to ship ahead of the data.

---

## Testing & verification

- **Phase 1:** unit tests for the classifier (`test_classify.py`) covering
  exclusions, markers, name-match, each tier, NO_SOURCE, format preference,
  listing parse, fetch-plan filtering. Coverage measured against the real 46k-file
  listing.
- **Phase 2:** unit tests using synthetic audio (ffmpeg-generated tone + silence
  pad) to validate offset detection, silence check, and duration math without the
  real library.
- **Phase 4:** unit tests for the resolver and the mpv command construction; a
  standalone local mpv proof that `amix → rubberband → per-branch volume` behaves
  (two files, live pitch, independent gain). Full on-device end-to-end verification
  waits for real audio + a maintenance window (no live show).

## Key decisions & risks

- **Full mix over isolated stem** — chosen for universal identifiability; muddiness
  mitigated later via the separator tool.
- **Manifest-first** — decouples the hard identification problem from byte movement;
  lets the human review before 40 GB moves.
- **Sibling-file resolver over DB column** — zero migration, activates on file
  presence.
- **Risk:** wrong Phase-1 picks (LOW tier) — mitigated by Phase-2 correlation
  rejecting them.
- **Risk:** live-device audio regression — mitigated by gating (inert without
  files) and a local mpv proof before on-device testing.
