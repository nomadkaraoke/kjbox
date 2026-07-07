# Verified Original Audio Dataset (M1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correctly identify the original full-mix input for every NOMAD release by *measuring* separated vocal energy (not trusting filenames), then assemble the verified originals into the Dropbox source-of-truth folder with consistent naming.

**Architecture:** A local (Mac) pipeline of small pure-logic Python modules + a driver. For each in-scope folder we enumerate candidate audio files, materialize each from the Dropbox clone, run a cheap `2_HP-UVR.pth` separation, measure the vocals stem's mean volume, and pick the candidate with real vocals. Filenames are a tiebreak only. Early era (NOMAD-0001–0448) gets the full oracle; marker eras get a stratified audit. A human verifies ≥20 low-confidence picks, then an assembler copies winners into `Tracks-Audio/Original/`.

**Tech Stack:** Python 3.13 (conda env `nomadkaraoke`), pytest 9.0.2, `audio-separator` 0.44.1 (VR model `2_HP-UVR.pth`), ffmpeg 8 (`volumedetect`), a Swift `materialize` helper for Dropbox online-only files, `soundscope` for review waveforms.

## Global Constraints

- **Python/pytest:** run everything with the env interpreter: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python`. Run tests from `scripts/original_vocals/`: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest <file> -v`.
- **Modules are flat** in `scripts/original_vocals/` with an `oracle_` prefix and `from classify import …` (same-dir import), matching the existing `classify.py` / `verify_sync.py` convention. No package, no `tests/` subdir.
- **Reuse from `classify.py`** (do not reimplement): `AudioFile`, `is_excluded`, `AUDIO_EXT`, `FMT_RANK`, `safe_dst_name`, `_brand_num`, `_norm_quotes`.
- **Separator:** `audio-separator` at `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/audio-separator`; model `2_HP-UVR.pth`; requires env `AUDIO_SEPARATOR_MODEL_DIR=/Volumes/AndrewMacSD/python-audio-separator-models-repo`. Output format flac; vocals stem filename contains `(Vocals)` (capital V) — match case-insensitively.
- **Vocal-energy metric = `volumedetect` `mean_volume`** (mean level, dBFS). **Never** use `max_volume`/peak — peak masked ~65% of wrong picks (NOMAD-0002 peak −6.4 dB but mean −41.5 dB).
- **materialize:** build once with `swiftc -O local_clone/materialize.swift -o local_clone/materialize`; invoke as `local_clone/materialize <abs_path>` (blocks until the online-only file is fully local).
- **Dropbox paths:**
  - Source (messy): `/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized/`
  - Dest (source of truth): `/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Audio/Original/`
- **Naming:** `NOMAD-#### - Artist - Title.<origext>` via `classify.safe_dst_name(brand, f"{artist} - {title}", ext)`; original bytes preserved (no transcode).
- **Zones:** full-oracle = manifest `method` in {`name-match`, `leftover-only`, `leftover-ambiguous`} (337 folders, NOMAD-0001–0448). Marker eras (≥0458) = stratified audit.
- **No live device interaction in M1.** Everything runs on the Mac against Dropbox. Disk: build incrementally; the 2_HP-UVR stems are disposable (temp workdir), only measurements are kept.
- **Manifest:** `data/manifest.csv` columns `brand_code,artist,title,tier,method,chosen_ext,n_audio,n_candidates,note,chosen_path,alt_candidates`.

---

### Task 1: Candidate enumeration

**Files:**
- Create: `scripts/original_vocals/oracle_candidates.py`
- Test: `scripts/original_vocals/test_oracle_candidates.py`

**Interfaces:**
- Consumes: `classify.AudioFile`, `classify.is_excluded`, `classify.AUDIO_EXT`.
- Produces: `filter_candidates(files: list[AudioFile]) -> list[AudioFile]`; `enumerate_candidates(folder: str) -> list[AudioFile]` (recursively lists a Tracks-Organized folder, returns non-excluded audio files with absolute `path`).

- [ ] **Step 1: Write the failing test**

```python
# test_oracle_candidates.py
import os
from classify import AudioFile
from oracle_candidates import filter_candidates, enumerate_candidates


def af(name, ext, size=1000, path=None):
    return AudioFile(size=size, path=path or f"F/{name}", name=name, ext=ext)


def test_filter_drops_existing_stems_and_renders_keeps_originals():
    files = [
        af("Idlewild - Little Discourage.mp3", "mp3"),        # CDG-backfill instrumental (KEEP: oracle judges)
        af("01 Little Discourage.flac", "flac"),               # album rip original (KEEP)
        af("01 Little Discourage_(Vocals)_2_HP-UVR.flac", "flac"),  # existing stem (DROP)
        af("01 Little Discourage_(Instrumental)_mel_band.flac", "flac"),  # existing stem (DROP)
        af("Idlewild - Little Discourage (Karaoke) [abc].webm", "webm"),  # render (DROP)
    ]
    kept = {f.name for f in filter_candidates(files)}
    assert kept == {"Idlewild - Little Discourage.mp3", "01 Little Discourage.flac"}


def test_enumerate_reads_folder_recursively(tmp_path):
    d = tmp_path / "NOMAD-0100 - Idlewild - Little Discourage"
    (d / "sub").mkdir(parents=True)
    (d / "01 Little Discourage.flac").write_bytes(b"x" * 10)
    (d / "sub" / "Idlewild - Little Discourage.mp3").write_bytes(b"y" * 20)
    (d / "cover.jpg").write_bytes(b"z")            # non-audio ignored
    (d / "song_(Vocals)_2_HP-UVR.flac").write_bytes(b"v")  # stem dropped
    got = {os.path.basename(c.path) for c in enumerate_candidates(str(d))}
    assert got == {"01 Little Discourage.flac", "Idlewild - Little Discourage.mp3"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_oracle_candidates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'oracle_candidates'`

- [ ] **Step 3: Write minimal implementation**

```python
# oracle_candidates.py
"""Enumerate the viable original-input candidates in a Tracks-Organized folder.

Unlike the phase-1 classifier we do NOT try to pick here — we return every
plausible candidate so the separation oracle can measure each. We only drop
UNAMBIGUOUS derived artifacts (existing vocals/instrumental stems, karaoke
renders, model-tagged files) via classify.is_excluded. Crucially the clean
"Artist - Title.mp3" CDG-backfill instrumentals are KEPT — the oracle
self-eliminates them by measuring near-silent vocals.
"""
from __future__ import annotations
import os
from classify import AudioFile, is_excluded, AUDIO_EXT


def filter_candidates(files: list[AudioFile]) -> list[AudioFile]:
    return [f for f in files if f.ext in AUDIO_EXT and not is_excluded(f.name.lower())]


def enumerate_candidates(folder: str) -> list[AudioFile]:
    out: list[AudioFile] = []
    for root, _dirs, names in os.walk(folder):
        for name in names:
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in AUDIO_EXT:
                continue
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            out.append(AudioFile(size=size, path=path, name=name, ext=ext))
    return filter_candidates(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_oracle_candidates.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/original_vocals/oracle_candidates.py scripts/original_vocals/test_oracle_candidates.py
git commit -m "feat(oracle): candidate enumeration (keep clean-named files for the oracle to judge)"
```

---

### Task 2: Vocal-energy measurement

**Files:**
- Create: `scripts/original_vocals/oracle_measure.py`
- Test: `scripts/original_vocals/test_oracle_measure.py`

**Interfaces:**
- Consumes: nothing internal.
- Produces: `parse_mean_volume(stderr_text: str) -> float | None` (pure parser of ffmpeg `volumedetect` output); `separate_and_measure(audio_path: str, workdir: str, sep_bin: str, model: str) -> float | None` (materialize is done by the driver; this separates + measures, returns vocals `mean_volume` dB or None on failure).

- [ ] **Step 1: Write the failing test**

```python
# test_oracle_measure.py
from oracle_measure import parse_mean_volume


def test_parse_mean_volume_reads_mean_not_max():
    stderr = (
        "[Parsed_volumedetect_0 @ 0x0] n_samples: 1000\n"
        "[Parsed_volumedetect_0 @ 0x0] mean_volume: -41.5 dB\n"
        "[Parsed_volumedetect_0 @ 0x0] max_volume: -6.4 dB\n"
    )
    assert parse_mean_volume(stderr) == -41.5


def test_parse_mean_volume_missing_returns_none():
    assert parse_mean_volume("no volume info here") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_oracle_measure.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'oracle_measure'`

- [ ] **Step 3: Write minimal implementation**

```python
# oracle_measure.py
"""Separate one audio file with a cheap VR model and measure its vocals stem's
MEAN volume (dBFS). Mean, not peak: peak is fooled by transient spikes and
masked ~65% of the early wrong-input picks.
"""
from __future__ import annotations
import glob
import os
import re
import shutil
import subprocess

_MEAN_RE = re.compile(r"mean_volume:\s*([-0-9.]+)\s*dB")


def parse_mean_volume(stderr_text: str) -> float | None:
    m = _MEAN_RE.search(stderr_text or "")
    return float(m.group(1)) if m else None


def _measure_file_mean_db(path: str) -> float | None:
    # volumedetect logs at INFO on stderr — do NOT pass -v error.
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-af", "volumedetect", "-f", "null", "/dev/null"],
        capture_output=True, text=True,
    )
    return parse_mean_volume(proc.stderr)


def separate_and_measure(audio_path: str, workdir: str, sep_bin: str, model: str) -> float | None:
    out = os.path.join(workdir, "out")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("AUDIO_SEPARATOR_MODEL_DIR",
                   "/Volumes/AndrewMacSD/python-audio-separator-models-repo")
    r = subprocess.run(
        [sep_bin, audio_path, "--model_filename", model,
         "--output_dir", out, "--output_format", "flac"],
        capture_output=True, text=True, env=env,
    )
    if r.returncode != 0:
        return None
    voc = None
    for f in glob.glob(os.path.join(out, "*.flac")):
        if "(vocals)" in os.path.basename(f).lower():
            voc = f
            break
    if not voc:
        return None
    db = _measure_file_mean_db(voc)
    shutil.rmtree(out, ignore_errors=True)   # stems are disposable
    return db
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_oracle_measure.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Smoke-test the real separation path (manual, one file)**

Run (from `scripts/original_vocals/`):
```bash
/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -c "
from oracle_measure import separate_and_measure
db = separate_and_measure('/Users/andrew/Projects/nomadkaraoke/test-kjbox-vocal-sep/13 Eileen.mp3',
    '/tmp/ov_oracle_smoke', '/Users/andrew/miniforge3/envs/nomadkaraoke/bin/audio-separator', '2_HP-UVR.pth')
print('mean_db=', db)"
```
Expected: prints a `mean_db=` value well above −40 (real vocals; `13 Eileen` is a confirmed original). If the file isn't present, substitute any known-good original mp3.

- [ ] **Step 6: Commit**

```bash
git add scripts/original_vocals/oracle_measure.py scripts/original_vocals/test_oracle_measure.py
git commit -m "feat(oracle): separate-and-measure vocal mean volume (2_HP-UVR + volumedetect)"
```

---

### Task 3: Oracle pick logic (the heart)

**Files:**
- Create: `scripts/original_vocals/oracle_pick.py`
- Test: `scripts/original_vocals/test_oracle_pick.py`

**Interfaces:**
- Consumes: `classify.FMT_RANK`.
- Produces: `@dataclass Candidate(path:str, name:str, ext:str, size:int, mean_db: float | None)`; `@dataclass PickResult(winner: Candidate | None, winner_db: float | None, runnerup_db: float | None, margin_db: float | None, confidence: str, verdict: str)`; `pick_winner(cands: list[Candidate], floor_db: float = -40.0, margin_db: float = 6.0) -> PickResult`. `verdict ∈ {confirmed, no_source}`; `confidence ∈ {high, low, none}`.

- [ ] **Step 1: Write the failing test**

```python
# test_oracle_pick.py
from oracle_pick import Candidate, pick_winner


def c(name, ext, db, size=1000):
    return Candidate(path=f"F/{name}", name=name, ext=ext, size=size, mean_db=db)


def test_picks_loud_original_over_silent_instrumental():
    r = pick_winner([
        c("Idlewild - Little Discourage.mp3", "mp3", -53.2),   # CDG instrumental (silent)
        c("01 Little Discourage.flac", "flac", -22.0),          # real original
    ])
    assert r.winner.name == "01 Little Discourage.flac"
    assert r.verdict == "confirmed"
    assert r.confidence == "high"          # 31 dB margin
    assert round(r.margin_db, 1) == 31.2


def test_all_dead_is_no_source():
    r = pick_winner([c("a.mp3", "mp3", -60.0), c("b.mp3", "mp3", -55.0)])
    assert r.verdict == "no_source"
    assert r.confidence == "none"
    assert r.winner is None


def test_close_margin_is_low_confidence():
    r = pick_winner([c("studio.flac", "flac", -20.0), c("live.mp3", "mp3", -23.0)])
    assert r.winner.name == "studio.flac"
    assert r.confidence == "low"           # 3 dB margin < 6
    assert r.verdict == "confirmed"


def test_tie_breaks_on_format_then_size():
    # equal loudness within tie epsilon -> prefer flac over mp3
    r = pick_winner([c("x.mp3", "mp3", -20.0, size=9000), c("y.flac", "flac", -20.2, size=1000)])
    assert r.winner.name == "y.flac"
    assert r.confidence == "low"           # near-equal loudness


def test_single_candidate_above_floor_is_low_confidence():
    r = pick_winner([c("only.flac", "flac", -25.0)])
    assert r.winner.name == "only.flac"
    assert r.verdict == "confirmed"
    assert r.confidence == "low"           # nothing to compare against
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_oracle_pick.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'oracle_pick'`

- [ ] **Step 3: Write minimal implementation**

```python
# oracle_pick.py
"""Choose the original input from measured candidates.

Primary signal: highest MEAN vocal volume (a real full mix separates to loud
vocals; an instrumental separates to near-silence). Filename is only a
tiebreak (format rank, then size) when two candidates are near-equally loud.

verdict:  confirmed = at least one candidate has real vocals (>= floor_db)
          no_source = every candidate is dead (no original exists in the folder)
confidence: high = clear winner (margin >= margin_db over the runner-up)
            low  = single candidate, or runner-up within margin_db (e.g. an
                   original AND a live/alt version both have vocals) -> human check
            none = no_source
"""
from __future__ import annotations
from dataclasses import dataclass
from classify import FMT_RANK

_TIE_EPS_DB = 1.5   # within this, treat loudness as a tie and use format/size


@dataclass
class Candidate:
    path: str
    name: str
    ext: str
    size: int
    mean_db: float | None


@dataclass
class PickResult:
    winner: Candidate | None
    winner_db: float | None
    runnerup_db: float | None
    margin_db: float | None
    confidence: str
    verdict: str


def _sort_key(c: Candidate):
    db = c.mean_db if c.mean_db is not None else -999.0
    return (db, FMT_RANK.get(c.ext, 0), c.size)


def pick_winner(cands: list[Candidate], floor_db: float = -40.0,
                margin_db: float = 6.0) -> PickResult:
    live = [c for c in cands if c.mean_db is not None and c.mean_db >= floor_db]
    if not live:
        return PickResult(None, None, None, None, "none", "no_source")

    ordered = sorted(live, key=_sort_key, reverse=True)
    winner = ordered[0]
    if len(ordered) == 1:
        return PickResult(winner, winner.mean_db, None, None, "low", "confirmed")

    runnerup = ordered[1]
    margin = winner.mean_db - runnerup.mean_db
    confidence = "high" if margin >= margin_db else "low"
    return PickResult(winner, winner.mean_db, runnerup.mean_db, margin,
                      confidence, "confirmed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_oracle_pick.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/original_vocals/oracle_pick.py scripts/original_vocals/test_oracle_pick.py
git commit -m "feat(oracle): winner selection by mean vocal volume, margin-based confidence"
```

---

### Task 4: Zone + stratified audit selection

**Files:**
- Create: `scripts/original_vocals/oracle_zones.py`
- Test: `scripts/original_vocals/test_oracle_zones.py`

**Interfaces:**
- Consumes: manifest rows (`list[dict]` with `brand_code`, `method`).
- Produces: `full_oracle_brands(rows) -> list[str]`; `audit_sample(rows, per_label:int=7, uploaded_all:bool=True, no_source_n:int=8, seed:int=1729) -> list[str]`. Both return sorted brand-code lists. Marker label is read from `method` (a `+`-joined label string like `original`, `local`, `youtube`, `flacfetch`, `uploaded`; `karaoke-sourced` = NO_SOURCE).

- [ ] **Step 1: Write the failing test**

```python
# test_oracle_zones.py
from oracle_zones import full_oracle_brands, audit_sample


def rows():
    r = []
    for i in range(1, 449):
        meth = "name-match" if i <= 200 else "leftover-only"
        r.append({"brand_code": f"NOMAD-{i:04d}", "method": meth})
    for i in range(458, 900):
        r.append({"brand_code": f"NOMAD-{i:04d}", "method": "original"})
    for i in range(475, 758):
        r.append({"brand_code": f"NOMAD-{i:04d}", "method": "local"})
    for i in range(1263, 1276):
        r.append({"brand_code": f"NOMAD-{i:04d}", "method": "uploaded"})
    for i in range(1200, 1240):
        r.append({"brand_code": f"NOMAD-{i:04d}", "method": "karaoke-sourced"})
    return r


def test_full_oracle_is_namematch_and_leftover_only():
    got = full_oracle_brands(rows())
    assert got[0] == "NOMAD-0001" and got[-1] == "NOMAD-0448"
    assert len(got) == 448
    assert "NOMAD-0458" not in got            # marker era excluded


def test_audit_is_stratified_and_deterministic():
    a = audit_sample(rows(), per_label=7, seed=1729)
    b = audit_sample(rows(), per_label=7, seed=1729)
    assert a == b                              # deterministic
    methods = {"original", "local", "uploaded", "karaoke-sourced"}
    # every marker label present in input is represented
    seen = set()
    lut = {r["brand_code"]: r["method"] for r in rows()}
    for br in a:
        seen.add(lut[br])
    assert methods.issubset(seen)
    assert all(lut[br] != "name-match" for br in a)   # never audits the full-oracle zone


def test_audit_takes_all_uploaded_when_flagged():
    a = audit_sample(rows(), per_label=7, uploaded_all=True, seed=1729)
    lut = {r["brand_code"]: r["method"] for r in rows()}
    assert sum(1 for br in a if lut[br] == "uploaded") == 13
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_oracle_zones.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'oracle_zones'`

- [ ] **Step 3: Write minimal implementation**

```python
# oracle_zones.py
"""Decide which folders get the full oracle vs a stratified audit.

full-oracle = the pre-marker messy era (method name-match / leftover*) where
filenames are unreliable. audit = a random, stratified sample of the trusted
marker eras so every era the user described is spot-checked; escalate a whole
era into the full oracle if the audit finds a bad pick there.
"""
from __future__ import annotations
import random

_FULL_METHODS = {"name-match", "leftover-only", "leftover-ambiguous"}


def full_oracle_brands(rows: list[dict]) -> list[str]:
    return sorted(r["brand_code"] for r in rows if r["method"] in _FULL_METHODS)


def _label(method: str) -> str:
    # method is a '+'-joined marker label list; first token is enough to bucket.
    return (method or "").split("+")[0]


def audit_sample(rows: list[dict], per_label: int = 7, uploaded_all: bool = True,
                 no_source_n: int = 8, seed: int = 1729) -> list[str]:
    rng = random.Random(seed)
    by_label: dict[str, list[str]] = {}
    for r in rows:
        if r["method"] in _FULL_METHODS:
            continue                         # never audit the full-oracle zone
        by_label.setdefault(_label(r["method"]), []).append(r["brand_code"])

    picked: list[str] = []
    for label, brands in by_label.items():
        brands = sorted(brands)
        if label == "uploaded" and uploaded_all:
            n = len(brands)
        elif label == "karaoke-sourced":
            n = min(no_source_n, len(brands))
        else:
            n = min(per_label, len(brands))
        picked.extend(rng.sample(brands, n))
    return sorted(picked)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_oracle_zones.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/original_vocals/oracle_zones.py scripts/original_vocals/test_oracle_zones.py
git commit -m "feat(oracle): full-oracle zone + stratified per-era audit selection"
```

---

### Task 5: Oracle driver (orchestration + resumable results)

**Files:**
- Create: `scripts/original_vocals/oracle_run.py`
- Build artifact: `scripts/original_vocals/local_clone/materialize` (from existing `materialize.swift`)
- Output: `scripts/original_vocals/data/oracle_results.csv`

**Interfaces:**
- Consumes: `oracle_candidates.enumerate_candidates`, `oracle_measure.separate_and_measure`, `oracle_pick.pick_winner` + `Candidate`, `oracle_zones.full_oracle_brands`/`audit_sample`, `classify._brand_num`.
- Produces: `data/oracle_results.csv` with columns `brand,folder,verdict,confidence,winner_name,winner_ext,winner_rel,winner_db,runnerup_db,margin_db,n_candidates,approved`. `winner_rel` = path relative to Tracks-Organized; `approved` starts empty (human fills `y`).

- [ ] **Step 1: Build the materialize helper**

Run (from `scripts/original_vocals/`):
```bash
swiftc -O local_clone/materialize.swift -o local_clone/materialize && ls -la local_clone/materialize
```
Expected: an executable at `local_clone/materialize`.

- [ ] **Step 2: Write the failing test (folder→brand resolution + resume)**

```python
# test_oracle_run.py
import os
from oracle_run import folder_for_brand, load_done_brands


def test_folder_for_brand_globs_tracks_organized(tmp_path):
    root = tmp_path / "Tracks-Organized"
    (root / "NOMAD-0100 - Idlewild - Little Discourage").mkdir(parents=True)
    assert os.path.basename(folder_for_brand(str(root), "NOMAD-0100")) == \
        "NOMAD-0100 - Idlewild - Little Discourage"
    assert folder_for_brand(str(root), "NOMAD-9999") is None


def test_load_done_brands_reads_results_csv(tmp_path):
    p = tmp_path / "oracle_results.csv"
    p.write_text("brand,folder,verdict,confidence,winner_name,winner_ext,winner_rel,"
                 "winner_db,runnerup_db,margin_db,n_candidates,approved\n"
                 "NOMAD-0001,f,confirmed,high,w.flac,flac,f/w.flac,-20,-50,30,2,\n")
    assert load_done_brands(str(p)) == {"NOMAD-0001"}
    assert load_done_brands(str(tmp_path / "missing.csv")) == set()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_oracle_run.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'oracle_run'`

- [ ] **Step 4: Write the driver**

```python
# oracle_run.py
"""Run the separation oracle over in-scope folders and record verified picks.

Per folder: enumerate candidates -> materialize + separate + measure each ->
pick the one with real vocals -> append a row to data/oracle_results.csv.
RESUMABLE: brands already in the results CSV are skipped.

Usage (from scripts/original_vocals/):
  python oracle_run.py --zone full            # NOMAD-0001..0448
  python oracle_run.py --zone audit           # stratified marker-era sample
  python oracle_run.py --brands NOMAD-0002,NOMAD-0018,NOMAD-0100   # calibration
  python oracle_run.py --zone full --limit 20 # first 20 only
"""
from __future__ import annotations
import argparse
import csv
import glob
import os
import subprocess
import sys

from classify import _brand_num
from oracle_candidates import enumerate_candidates
from oracle_measure import separate_and_measure
from oracle_pick import Candidate, pick_winner
from oracle_zones import full_oracle_brands, audit_sample

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKS_ORG = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized"
SEP_BIN = "/Users/andrew/miniforge3/envs/nomadkaraoke/bin/audio-separator"
MODEL = "2_HP-UVR.pth"
MATERIALIZE = os.path.join(HERE, "local_clone", "materialize")
RESULTS = os.path.join(HERE, "data", "oracle_results.csv")
WORKDIR = "/tmp/ov_oracle_work"
FIELDS = ["brand", "folder", "verdict", "confidence", "winner_name", "winner_ext",
          "winner_rel", "winner_db", "runnerup_db", "margin_db", "n_candidates", "approved"]


def folder_for_brand(tracks_org: str, brand: str) -> str | None:
    hits = sorted(glob.glob(os.path.join(tracks_org, f"{brand} *")) +
                  glob.glob(os.path.join(tracks_org, brand)))
    return hits[0] if hits else None


def load_done_brands(results_csv: str) -> set[str]:
    if not os.path.exists(results_csv):
        return set()
    with open(results_csv, newline="") as f:
        return {r["brand"] for r in csv.DictReader(f)}


def _load_manifest_rows() -> list[dict]:
    with open(os.path.join(HERE, "data", "manifest.csv"), newline="") as f:
        return list(csv.DictReader(f))


def _brands_for_zone(zone: str) -> list[str]:
    rows = _load_manifest_rows()
    if zone == "full":
        return full_oracle_brands(rows)
    if zone == "audit":
        return audit_sample(rows)
    if zone == "all":
        return sorted({r["brand_code"] for r in rows}, key=_brand_num)
    raise SystemExit(f"unknown zone {zone!r}")


def _rel(path: str) -> str:
    return os.path.relpath(path, TRACKS_ORG)


def process_brand(brand: str) -> dict | None:
    folder = folder_for_brand(TRACKS_ORG, brand)
    if not folder:
        print(f"  {brand}: NO FOLDER", file=sys.stderr)
        return None
    cands_af = enumerate_candidates(folder)
    measured: list[Candidate] = []
    for af in cands_af:
        subprocess.run([MATERIALIZE, af.path], capture_output=True)
        db = separate_and_measure(af.path, WORKDIR, SEP_BIN, MODEL)
        measured.append(Candidate(path=af.path, name=af.name, ext=af.ext,
                                  size=af.size, mean_db=db))
        print(f"    {af.name}: {db} dB")
    res = pick_winner(measured)
    w = res.winner
    return {
        "brand": brand, "folder": os.path.basename(folder),
        "verdict": res.verdict, "confidence": res.confidence,
        "winner_name": w.name if w else "", "winner_ext": w.ext if w else "",
        "winner_rel": _rel(w.path) if w else "",
        "winner_db": f"{res.winner_db:.1f}" if res.winner_db is not None else "",
        "runnerup_db": f"{res.runnerup_db:.1f}" if res.runnerup_db is not None else "",
        "margin_db": f"{res.margin_db:.1f}" if res.margin_db is not None else "",
        "n_candidates": len(measured), "approved": "",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zone", choices=["full", "audit", "all"])
    ap.add_argument("--brands", help="comma-separated brand codes (overrides --zone)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    if args.brands:
        brands = [b.strip() for b in args.brands.split(",") if b.strip()]
    elif args.zone:
        brands = _brands_for_zone(args.zone)
    else:
        raise SystemExit("pass --zone or --brands")

    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    os.makedirs(WORKDIR, exist_ok=True)
    done = load_done_brands(RESULTS)
    todo = [b for b in brands if b not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"oracle: {len(todo)} folders to process ({len(done)} already done)")

    new = not os.path.exists(RESULTS)
    with open(RESULTS, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for i, brand in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {brand}")
            row = process_brand(brand)
            if row:
                w.writerow(row)
                f.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_oracle_run.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Calibrate on the 3 known folders + set the floor**

Run (from `scripts/original_vocals/`):
```bash
rm -f data/oracle_results.csv
/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python oracle_run.py \
  --brands NOMAD-0002,NOMAD-0018,NOMAD-0100
column -s, -t < data/oracle_results.csv
```
Expected: NOMAD-0002 and NOMAD-0100 winners should be a messy-named album-rip/source-tagged file (NOT the clean `Artist - Title.mp3`), with the clean file measuring much lower; NOMAD-0018 may come back `no_source` if its folder truly has only the instrumental. Confirm the winner mean-dB of real originals sits clearly above −40 and the CDG instrumentals sit clearly below. If the gap warrants it, adjust `floor_db`/`margin_db` defaults in `oracle_pick.py` and note the calibrated values in a comment. Then `rm data/oracle_results.csv` before the full run.

- [ ] **Step 7: Commit**

```bash
git add scripts/original_vocals/oracle_run.py scripts/original_vocals/test_oracle_run.py scripts/original_vocals/local_clone/materialize
git commit -m "feat(oracle): resumable driver (materialize+separate+measure+pick -> results csv)"
```

*(If `local_clone/materialize` is gitignored as a build artifact, commit only the .py/.test files and note the build command in the run instructions.)*

---

### Task 6: Review report (low-confidence-first + waveforms)

**Files:**
- Create: `scripts/original_vocals/oracle_report.py`
- Test: `scripts/original_vocals/test_oracle_report.py`
- Output: `data/review_picks.csv`, `data/review/<brand>.png`, `data/review/index.html`

**Interfaces:**
- Consumes: `data/oracle_results.csv`.
- Produces: `sort_for_review(rows: list[dict]) -> list[dict]` (no_source + low-confidence first, then ascending margin) and a CLI that writes the review CSV + a waveform PNG (ffmpeg `showwavespic` over the winner's materialized original) + an `index.html` grid for the flagged subset.

- [ ] **Step 1: Write the failing test**

```python
# test_oracle_report.py
from oracle_report import sort_for_review


def test_review_order_puts_uncertain_first():
    rows = [
        {"brand": "A", "verdict": "confirmed", "confidence": "high", "margin_db": "30"},
        {"brand": "B", "verdict": "confirmed", "confidence": "low", "margin_db": "3"},
        {"brand": "C", "verdict": "no_source", "confidence": "none", "margin_db": ""},
        {"brand": "D", "verdict": "confirmed", "confidence": "low", "margin_db": "5"},
    ]
    order = [r["brand"] for r in sort_for_review(rows)]
    assert order[0] == "C"                 # no_source first
    assert order[1:3] == ["B", "D"]        # low-confidence by ascending margin
    assert order[-1] == "A"                # high-confidence last
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_oracle_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'oracle_report'`

- [ ] **Step 3: Write minimal implementation**

```python
# oracle_report.py
"""Produce a human-review artifact from oracle_results.csv, uncertain-first.

Writes data/review_picks.csv (sorted for review) and, for the flagged subset
(no_source + low-confidence), a waveform PNG per winner plus an index.html grid
so the user can eyeball + audition the ~20 they want to verify.
"""
from __future__ import annotations
import argparse
import csv
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "data", "oracle_results.csv")
REVIEW_CSV = os.path.join(HERE, "data", "review_picks.csv")
REVIEW_DIR = os.path.join(HERE, "data", "review")
TRACKS_ORG = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized"


def _rank(r: dict) -> tuple:
    order = {"no_source": 0, "low": 1, "high": 2}
    key = "no_source" if r["verdict"] == "no_source" else r["confidence"]
    try:
        margin = float(r.get("margin_db") or 1e9)
    except ValueError:
        margin = 1e9
    return (order.get(key, 3), margin)


def sort_for_review(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=_rank)


def _waveform_png(src: str, dst: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src,
                    "-filter_complex", "showwavespic=s=640x120", "-frames:v", "1", dst],
                   capture_output=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", default=RESULTS)
    args = ap.parse_args(argv)
    with open(args.results, newline="") as f:
        rows = sort_for_review(list(csv.DictReader(f)))

    with open(REVIEW_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    os.makedirs(REVIEW_DIR, exist_ok=True)
    flagged = [r for r in rows if r["verdict"] == "no_source" or r["confidence"] == "low"]
    cards = []
    for r in flagged:
        png = os.path.join(REVIEW_DIR, f"{r['brand']}.png")
        if r["winner_rel"]:
            _waveform_png(os.path.join(TRACKS_ORG, r["winner_rel"]), png)
        cards.append(
            f"<div style='margin:8px;font-family:sans-serif'>"
            f"<b>{r['brand']}</b> [{r['verdict']}/{r['confidence']}] "
            f"win={r['winner_db']}dB runnerup={r['runnerup_db']}dB margin={r['margin_db']}<br>"
            f"<code>{r['winner_name']}</code><br>"
            f"<img src='{r['brand']}.png' width='640'></div>")
    with open(os.path.join(REVIEW_DIR, "index.html"), "w") as f:
        f.write("<html><body>" + "\n".join(cards) + "</body></html>")

    print(f"review: {len(rows)} picks -> {REVIEW_CSV}; {len(flagged)} flagged -> {REVIEW_DIR}/index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_oracle_report.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/original_vocals/oracle_report.py scripts/original_vocals/test_oracle_report.py
git commit -m "feat(oracle): uncertain-first review report with waveform grid"
```

---

### Task 7: Assembler → `Tracks-Audio/Original/`

**Files:**
- Create: `scripts/original_vocals/oracle_assemble.py`
- Test: `scripts/original_vocals/test_oracle_assemble.py`

**Interfaces:**
- Consumes: `data/oracle_results.csv`, `data/manifest.csv` (for `artist`/`title`), `classify.safe_dst_name`.
- Produces: `plan_copies(results_rows: list[dict], manifest_map: dict[str, dict], dest_dir: str) -> list[tuple[str, str]]` returning `(abs_src, abs_dst)` for every assemble-eligible pick; a CLI that materializes each src + copies to `Tracks-Audio/Original/`, resumable (skips existing dest). Eligible = `verdict==confirmed` AND (`confidence==high` OR `approved` truthy).

- [ ] **Step 1: Write the failing test**

```python
# test_oracle_assemble.py
from oracle_assemble import plan_copies

TORG = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized"


def test_plan_copies_only_eligible_and_names_consistently():
    results = [
        {"brand": "NOMAD-0100", "verdict": "confirmed", "confidence": "high",
         "winner_rel": "NOMAD-0100 - Idlewild - Little Discourage/01 Little Discourage.flac",
         "winner_ext": "flac", "approved": ""},
        {"brand": "NOMAD-0018", "verdict": "no_source", "confidence": "none",
         "winner_rel": "", "winner_ext": "", "approved": ""},          # skip
        {"brand": "NOMAD-0007", "verdict": "confirmed", "confidence": "low",
         "winner_rel": "NOMAD-0007 - The Hush Sound - A Dark Congregation/02 A Dark Congregation.mp3",
         "winner_ext": "mp3", "approved": ""},                          # low + unapproved -> skip
        {"brand": "NOMAD-0008", "verdict": "confirmed", "confidence": "low",
         "winner_rel": "NOMAD-0008 - The Hush Sound - As You Cry/06 As You Cry.mp3",
         "winner_ext": "mp3", "approved": "y"},                         # low + approved -> include
    ]
    manifest = {
        "NOMAD-0100": {"artist": "Idlewild", "title": "Little Discourage"},
        "NOMAD-0008": {"artist": "The Hush Sound", "title": "As You Cry"},
    }
    plan = plan_copies(results, manifest, "/DEST")
    dsts = {src.split("/")[-1]: dst for src, dst in plan}
    assert plan == [
        (f"{TORG}/NOMAD-0100 - Idlewild - Little Discourage/01 Little Discourage.flac",
         "/DEST/NOMAD-0100 - Idlewild - Little Discourage.flac"),
        (f"{TORG}/NOMAD-0008 - The Hush Sound - As You Cry/06 As You Cry.mp3",
         "/DEST/NOMAD-0008 - The Hush Sound - As You Cry.mp3"),
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_oracle_assemble.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'oracle_assemble'`

- [ ] **Step 3: Write minimal implementation**

```python
# oracle_assemble.py
"""Copy verified originals into the Dropbox source-of-truth folder, renamed
NOMAD-#### - Artist - Title.<ext>. Eligible = confirmed AND (high-confidence OR
human-approved in the results CSV `approved` column). Resumable: skips dests
that already exist. Materializes each source first (Dropbox online-only).

Usage (from scripts/original_vocals/):
  python oracle_assemble.py            # copy eligible picks
  python oracle_assemble.py --dry-run  # print the copy plan only
"""
from __future__ import annotations
import argparse
import csv
import os
import shutil
import subprocess

from classify import safe_dst_name

HERE = os.path.dirname(os.path.abspath(__file__))
TRACKS_ORG = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Organized"
DEST_DIR = "/Users/andrew/AB Dropbox/Andrew Beveridge/MediaUnsynced/Karaoke/Tracks-Audio/Original"
MATERIALIZE = os.path.join(HERE, "local_clone", "materialize")


def _eligible(r: dict) -> bool:
    return r["verdict"] == "confirmed" and bool(r["winner_rel"]) and (
        r["confidence"] == "high" or (r.get("approved") or "").strip().lower() in ("y", "yes", "1"))


def plan_copies(results_rows: list[dict], manifest_map: dict[str, dict],
                dest_dir: str) -> list[tuple[str, str]]:
    plan: list[tuple[str, str]] = []
    for r in results_rows:
        if not _eligible(r):
            continue
        m = manifest_map.get(r["brand"])
        if not m:
            continue
        dst_name = safe_dst_name(r["brand"], f"{m['artist']} - {m['title']}", r["winner_ext"])
        plan.append((os.path.join(TRACKS_ORG, r["winner_rel"]),
                     os.path.join(dest_dir, dst_name)))
    return plan


def _load(path: str) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=os.path.join(HERE, "data", "oracle_results.csv"))
    ap.add_argument("--manifest", default=os.path.join(HERE, "data", "manifest.csv"))
    ap.add_argument("--dest", default=DEST_DIR)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    manifest_map = {r["brand_code"]: r for r in _load(args.manifest)}
    plan = plan_copies(_load(args.results), manifest_map, args.dest)
    os.makedirs(args.dest, exist_ok=True)
    copied = skipped = 0
    for src, dst in plan:
        if os.path.exists(dst):
            skipped += 1
            continue
        print(("DRY " if args.dry_run else "COPY ") + f"{os.path.basename(dst)}")
        if not args.dry_run:
            subprocess.run([MATERIALIZE, src], capture_output=True)
            shutil.copy2(src, dst)
            copied += 1
    print(f"assemble: {copied} copied, {skipped} already present, {len(plan)} eligible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_oracle_assemble.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/original_vocals/oracle_assemble.py scripts/original_vocals/test_oracle_assemble.py
git commit -m "feat(oracle): assemble verified originals into Dropbox Tracks-Audio/Original"
```

---

### Task 8: Fix the weak-vocals flag metric (peak → mean)

**Files:**
- Modify: `scripts/original_vocals/vocals/flag_weak_vocals.py`
- Test: `scripts/original_vocals/vocals/test_flag_weak_vocals.py`

**Interfaces:**
- Consumes: `vocals_diagnostics.csv` (has `vocals_max_db` AND `vocals_mean_db`).
- Produces: flagging keyed on `vocals_mean_db` (mean) instead of `vocals_max_db` (peak). CLI unchanged except the default threshold flag becomes `--mean-db` (default −35.0).

- [ ] **Step 1: Write the failing test**

```python
# vocals/test_flag_weak_vocals.py
import csv, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _write(diag, rows):
    with open(diag, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["brand", "dest", "input_bytes", "vocals_bytes",
                    "vocals_max_db", "vocals_mean_db", "dur_s", "seconds"])
        w.writerows(rows)


def test_flags_by_mean_not_peak(tmp_path):
    diag = tmp_path / "d.csv"
    out = tmp_path / "review.csv"
    # NOMAD-0002: peak loud (-6.4) but mean silent (-41.5) -> MUST be flagged now
    _write(diag, [
        ["NOMAD-0002", "a.flac", 6000000, 214341, "-6.4", "-41.5", "169", "90"],
        ["NOMAD-9999", "b.flac", 6000000, 900000, "-4.0", "-22.0", "180", "90"],  # real, not flagged
    ])
    subprocess.run([sys.executable, os.path.join(HERE, "flag_weak_vocals.py"),
                    "--diag", str(diag), "--out", str(out)], check=True)
    flagged = {r["brand"] for r in csv.DictReader(open(out))}
    assert "NOMAD-0002" in flagged
    assert "NOMAD-9999" not in flagged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest vocals/test_flag_weak_vocals.py -v`
Expected: FAIL — current code flags on `vocals_max_db` so NOMAD-0002 (peak −6.4) is NOT flagged.

- [ ] **Step 3: Edit `flag_weak_vocals.py` to key on mean**

Change the argument and the flagging signal. Replace the `--max-db` arg and the `vmax`/`low_peak` logic:

```python
    ap.add_argument("--mean-db", type=float, default=-35.0,
                    help="flag if vocals MEAN volume is quieter than this (default -35 dB). "
                         "Mean, not peak: peak is fooled by transient spikes.")
    ap.add_argument("--bps", type=float, default=1500.0,
                    help="flag if vocals flac bytes/sec is below this (default 1500)")
    ap.add_argument("--out", default=os.path.join(here, "weak_vocals_review.csv"))
    args = ap.parse_args(argv)
```

```python
    rows = list(csv.DictReader(open(args.diag)))
    flagged = []
    for r in rows:
        vmean = fnum(r.get("vocals_mean_db"))
        vbytes = fnum(r.get("vocals_bytes")) or 0
        dur = fnum(r.get("dur_s")) or 0
        bps = (vbytes / dur) if dur else 0
        low_mean = vmean is not None and vmean < args.mean_db
        tiny = dur and bps < args.bps
        if low_mean or tiny:
            reasons = []
            if low_mean:
                reasons.append(f"mean {vmean:.1f}dB")
            if tiny:
                reasons.append(f"{bps:.0f} B/s")
            sev = (args.mean_db - (vmean if vmean is not None else args.mean_db)) + \
                  max(0, (args.bps - bps) / args.bps) * 10
            flagged.append((sev, r["brand"], r.get("dest", ""), vmean, bps, "; ".join(reasons)))
```

Also update the output header cell `"vocals_max_db"` → `"vocals_mean_db"` and the two `vmax` references in the writer/print loop to `vmean`.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest vocals/test_flag_weak_vocals.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Full test sweep + commit**

```bash
cd scripts/original_vocals
/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest -q
git add vocals/flag_weak_vocals.py vocals/test_flag_weak_vocals.py
git commit -m "fix(oracle): flag weak vocals by mean volume not peak (peak masked ~65% of wrong picks)"
```

---

## Execution / operating notes (after the code lands)

These are run steps, not code tasks (the human drives them across the week):

1. **Full-oracle run:** `python oracle_run.py --zone full` (resumable; ~337 folders × candidates × ~30 s). Reclaim Mac disk between waves via Finder → "Make Online-Only" on completed `Tracks-Organized` subfolders.
2. **Audit run:** `python oracle_run.py --zone audit`. Inspect the marker-era picks; if any era shows a bad pick, re-run that era through the full oracle (`--brands …`).
3. **Review:** `python oracle_report.py`, open `data/review/index.html`, verify **≥20** (all `no_source` + lowest-margin `low` first). Mark good low-confidence picks `approved=y` in `data/oracle_results.csv`; fix any wrong winner by editing `winner_rel`/`winner_ext` (or re-running that brand).
4. **Assemble:** `python oracle_assemble.py --dry-run` then `python oracle_assemble.py` → populates `Tracks-Audio/Original/`.
5. **Coverage check:** counts of confirmed / low / no_source vs the 1,373 fetchable brands; list any `no_source` early-era folders for manual sourcing.

Milestone 1 is done when `Tracks-Audio/Original/` holds a verified, consistently-named original for every non-NO_SOURCE release and the human has signed off ≥20 low-confidence picks. **Milestone 2 (deferred): source `(Vocals)` stems into `Tracks-Audio/Vocals/`, then copy `Tracks-Audio/` → device.**

## Self-review notes
- **Spec coverage:** §1 candidate enum → T1; §2 oracle/measure → T2/T5; §3 zones+audit → T4; §4 confidence+review → T3/T6; §5 assemble → T7; §6 metric fix → T8; manifest record → results CSV (T5) + review CSV (T6). Sync/offset/phase-4/M2 correctly absent (out of scope).
- **Types consistent:** `Candidate`/`PickResult` (T3) consumed unchanged by T5; `oracle_results.csv` columns defined in T5 consumed by T6 (`sort_for_review`) and T7 (`plan_copies`); `safe_dst_name` signature matches `classify.py`.
- **No placeholders:** every code/test step has complete content; thresholds have concrete defaults calibrated in T5 Step 6.
