# Verified Original Audio Dataset — Design Spec (Milestone 1)

**Status:** Design approved in brainstorming 2026-07-07; spec pending user review.
**Worktree:** `kjbox-original-vocals-guide` · **Branch:** `feat/sess-20260707-local-clone-fetch` (PR #178)
**Supersedes** the Phase-1 identification approach in `2026-07-06-original-vocals-guide-design.md` (the `name-match` heuristic there is the bug this milestone fixes).

## Why this milestone exists

The original-vocals guide feature depends on having, for each NOMAD release, the
**original recording's audio** (the karaoke-gen *input*). Phase 1 identified those
files from Dropbox by filename heuristic. That heuristic is **wrong for the early
hand-made era**, and we now know exactly why.

### Root cause (confirmed with data + user)

In the early era, the file named cleanly `Artist - Title.mp3` is **not** the
original — it is the **CDG+MP3 backfill instrumental**. The user later built CDG
generation software and retroactively produced instrumental+CDG packages for old
releases, naming the instrumental `Artist - Title.mp3`. The classifier's
`name_matches_title` rule scored those `+80` → tier HIGH → picked the instrumental.
The true original kept its messy download/rip name (`NN Track Title.flac`,
`leningrad-alkash.mp3`, `Y2Mate.is - Title.mp3`, …).

Evidence (287 early tracks separated so far, mel-band):
- By **mean** vocal level (not peak): **140/287 dead (<−45 dB), ~65% wrong inputs**;
  only 92 (32%) plausibly real vocals.
- **151 of 170 dead picks used `name-match`**; **152/170 have the correct original
  already sitting in `alt_candidates`** (album-rip / source-tagged names).
- The old flag metric (`vocals_max_db`, peak) **masked** wrong picks — 69 tracks
  looked "good" by peak but are silent by mean (e.g. NOMAD-0002: peak −6.4 dB from a
  single transient, mean −41.5 dB).

Filenames in that era are unreliable, so we stop trusting them and **measure**:
separate each candidate and pick the one that actually contains vocals.

## Goal & scope

**Deliverable:** a verified, consistently-named **original full-mix per NOMAD
release**, assembled in Dropbox as the source of truth.

**In scope (Milestone 1):**
- Correctly identify + verify the original input audio for **all** releases.
- Assemble them into `Tracks-Audio/Original/` with consistent naming.
- Fix the weak-vocals flag metric (peak → mean).

**Out of scope (later milestones, captured but not designed here):**
- **Milestone 2 — Vocals dataset:** source guide vocals by finding existing
  `(Vocals)`-named stems already present in each track folder and materializing
  them (they were produced from the *correct* original when the karaoke track was
  made), rather than re-separating from scratch. Fall back to fresh mel-band
  separation only for gaps. Caveats to revisit: stem quality varies by era/model;
  coverage < 100%.
- **Device distribution:** once both `Tracks-Audio/Original` and
  `Tracks-Audio/Vocals` exist, copy the whole `Tracks-Audio/` folder to the device
  `NOMAD-audio/`.
- **Sync/offset measurement + Phase-4 playback** (the guide feature itself).
- **karaoke-gen write-path:** teach the generator to write original + vocals to
  these Dropbox paths on every new render.

## Dataset layout (source of truth = Dropbox)

```
~/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/
  Tracks-Organized/                 # messy source, 1505 NOMAD-#### folders (unchanged)
  Tracks-Audio/
    Original/                       # M1 output — NOMAD-#### - Artist - Title.<origext>
    Vocals/                         # M2 output
```

- Naming: `NOMAD-#### - Artist - Title.<ext>` (mirrors master videos so the
  `^NOMAD-(\d+)` brand regex resolves it; original bytes preserved, no transcode).
- Both `Original/` and `Vocals/` already exist (empty). Files land in `Original/`;
  Dropbox sync makes them the backed-up source of truth.

## Approach

### 1 — Candidate enumeration (per folder, from the local Dropbox clone)
For each in-scope folder under `Tracks-Organized/`, list all audio files
(`flac/wav/mp3/m4a/opus/aac/ogg/wma/webm`). Exclude only **unambiguous derived
artifacts**: existing separation stems (`_(Vocals)_`, `_(Instrumental)_`,
model-tagged `2_HP-UVR`/`mel_band`/`roformer`/`mdx`/`uvr`/`demucs`), `.cdg`/`.zip`,
and `(Karaoke)`/`(Title)`/"with vocals" renders. **Do not name-exclude the clean
`Artist - Title` files** — the oracle self-eliminates them (an instrumental
separates to dead vocals). Keep name as a *tiebreak hint only* (prefer lossless
album-rip / source-tagged over a clean-named mp3 when two candidates both have
vocals).

### 2 — Oracle pass (on the Mac, MPS)
For each candidate: materialize it from the Dropbox clone (NSFileCoordinator
coordinated read — `materialize.swift` from the local_clone tooling), then separate
with **`2_HP-UVR.pth`** (~25–30 s/track in the `nomadkaraoke` conda env), and
measure the **vocals stem's mean RMS** via ffmpeg `astats` (`Overall.RMS_level`) —
**mean, not peak**. Per folder, choose the candidate with the highest vocal energy.
Record: winner path, winner dB, runner-up dB, and the **margin**.

### 3 — Zone selection (data-driven)
- **Full-oracle zone:** all `name-match` (213) + `leftover*` (124) = **337 folders,
  NOMAD-0001–0448** — the entire pre-marker era where filenames are unreliable.
  Every candidate separated & measured.
- **Marker-era audit:** everything ≥ NOMAD-0458 is marker-based
  (`original`/`local`/`youtube`/`flacfetch`/`uploaded`) and trusted, but **verified
  by a stratified audit** — a random sample **per marker label** so every era the
  user described is covered: ~7 each from `youtube`/`local`/`original`/`flacfetch`,
  all 13 `uploaded`, plus ~8 `karaoke-sourced` (NO_SOURCE) to confirm no original
  was missed (≈ 40 tracks). If the audit surfaces a bad pick in an era, **escalate**
  that whole era into the full-oracle zone.

### 4 — Confidence + human review
- **Auto-confirm** clear winners: margin between top-2 large **and** winner above a
  calibrated floor (floor calibrated on known-good `13 Eileen` / NOMAD-0002 album-rip
  vs known-dead NOMAD-0018).
- **Flag low-confidence** first: small top-2 margin (e.g. an original *and* a live
  version both have vocals — energy can't tell studio from live), winner barely
  above floor, or single-candidate folders.
- **Review artifact:** a report (CSV + `soundscope` waveform thumbnails,
  lowest-confidence first) listing chosen input, winner/runner-up dB, margin, tier.
- **User verifies ≥ 20**, weighted to low-confidence plus a few random
  high-confidence. If a folder's *best* candidate is still dead → reclassify
  NO_SOURCE/GAP (no original exists).

### 5 — Assemble `Tracks-Audio/Original/`
For every confirmed release (early: oracle winner; marker: manifest pick,
audit-backed), copy the verified original — renamed
`NOMAD-#### - Artist - Title.<ext>` — into `Tracks-Audio/Original/`. Dropbox uploads
it (source of truth). No device interaction in this milestone.

### 6 — Metric fix
`flag_weak_vocals.py` switches its primary signal from `vocals_max_db` to
mean/RMS; byte-ratio stays as corroboration. Prevents the peak-masking that hid
~65% of the early wrong picks.

## Authoritative record (manifest)
Extend `data/manifest.csv` (or a sibling `verified_originals.csv`) with, per brand:
`verified` (bool), `verify_method` (`oracle` | `marker+audit`), `winner_vocal_db`,
`runnerup_vocal_db`, `margin_db`, `confidence` (`high`|`low`), `chosen_source_path`
(in Tracks-Organized), `dest_path` (in Tracks-Audio/Original), `human_checked`
(bool). This is the durable record that future initiatives (and Milestone 2) key off.

## Operational constraints
- **Mac disk:** ~40 GB free; the Original set is ~37 GB. Build incrementally;
  after a file is copied into `Tracks-Audio/Original/` and Dropbox has uploaded it,
  both the source (in `Tracks-Organized`) and the destination copy can be made
  **online-only** to reclaim space (legacy Smart Sync has no CLI evict — user does
  Finder → "Make Online Only" / Dropbox "Free Up Space" periodically). The oracle
  and copy proceed folder-by-folder so the working set stays small.
- **Resumability:** every stage skips folders already done (winner already in
  `Tracks-Audio/Original/` and recorded verified). Safe to stop/resume across the
  week.
- **Live device untouched** in M1.

## Verification & testing
- Unit tests: candidate enumeration + exclusions; oracle winner selection (highest
  mean RMS, tiebreak by format/size); manifest round-trip; the metric-fix in
  `flag_weak_vocals.py`.
- Oracle sanity: on a fixture folder with a known instrumental + known original,
  assert the original wins.
- Human gate: ≥20 user-verified picks (low-confidence-weighted) before the dataset
  is declared complete.
- Coverage report: counts per zone/era, confirmed vs low-confidence vs
  NO_SOURCE/GAP, and any era escalated by the audit.

## Risks
- **Studio vs live/alt version** both have vocals → oracle can't distinguish →
  handled by the low-confidence flag + human check.
- **Folder with only the instrumental** (original deleted) → best candidate dead →
  correctly reclassified NO_SOURCE; needs manual sourcing later (18 such early-era
  folders seen so far).
- **Audit misses a systematic marker-era error** → mitigated by stratified
  per-label sampling and era-escalation rule.
- **Disk pressure on the Mac** → mitigated by incremental build + online-only
  eviction; not a correctness risk.
