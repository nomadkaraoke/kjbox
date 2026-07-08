# Vocal-Guide Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed-5 s guide padding with a measured, human-verified, per-track offset so each original-vocals guide aligns to its karaoke video within ≤100 ms.

**Architecture:** Reuse the existing cross-correlation engine (`verify_sync.py`) to measure each track's lead-in offset (original↔video, applied to the guide) and classify confirmed vs needs-review. New pure-logic module (`align_core.py`) + three device-run drivers: `align_measure.py` (→ `align_offsets.csv`), `align_clips.py` (render review clips + decisions template), `align_apply.py` (merge human decisions → emit aligned guides to `NOMAD-vocals-padded`, remove excluded). The playback feature is unchanged — it already reads `NOMAD-vocals-padded/`.

**Tech Stack:** Python 3.11 (conda env `nomadkaraoke`), numpy, ffmpeg/ffprobe, existing `verify_sync.py`. Device = kjbox (`ssh nomadpctunnel`), Debian, `/usr/bin/ffmpeg`.

## Global Constraints
- Alignment tolerance: **≤100 ms** (a guide-vs-music flam beyond this is a failure).
- Measurement analysis rate: **8000 Hz** (matches `verify_sync.ANALYSIS_SR`; sub-ms lag).
- Offset is measured on the **original** (`NOMAD-audio`) vs **video** (`NOMAD-720p`) and **applied to the guide** (`NOMAD-vocals`) — they share a timebase.
- Confidence: `verify_sync` verdict — `confirmed` (peak ≥ 0.30, offset 2–15 s, corroborated) vs `needs-review`.
- Human review: pre-rendered A/V clips; all `needs-review` + a deterministic **~7 %** spot-check of `confirmed`; decisions in `align_decisions.csv` (`confirm` | `offset_ms=<n>` | `exclude` | `needs-finer`).
- Variant comb: coarse `[0, ±100, ±200] ms`, then fine `[0, ±25, ±50] ms` around the picked offset.
- Clip window: guide first-vocal-onset − 3 s → + 12 s; guide mixed at gain 0.65.
- Tests run on the Mac (`/Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest`); device runs need `pip install numpy` first.
- All new code under `scripts/original_vocals/`; branch `feat/sess-20260708-vocal-guide-alignment`.
- Device dirs: `/opt/nomad/downloads/{NOMAD-audio,NOMAD-720p,NOMAD-vocals,NOMAD-vocals-padded}`.

---

### Task 1: `align_core.py` — pure alignment logic

**Files:**
- Create: `scripts/original_vocals/align_core.py`
- Test: `scripts/original_vocals/test_align_core.py`

**Interfaces:**
- Produces:
  - `first_vocal_onset(samples: np.ndarray, sr: int, thresh_db=-45.0, win_s=0.05, min_run_s=0.15) -> float`
  - `variant_offsets(measured_s: float, steps_ms=(0,-100,100,-200,200)) -> list[float]`
  - `clip_cut(measured_s: float, onset_s: float, candidate_s: float, before=3.0, after=12.0) -> tuple[float,float,float]` returns `(video_start, guide_start, dur)`
  - `emit_af(offset_s: float, target_dur: float) -> str` (ffmpeg `-af` string)
  - `@dataclass OffsetRow(brand, offset_s, peak, verdict, video_dur, audio_dur, onset_s, source, status)` + `read_offsets(path)->dict[str,OffsetRow]` / `write_offsets(path, rows)`
  - `parse_decision(value: str) -> tuple[str, float|None]` (kind ∈ confirm|exclude|needs-finer|offset; offset seconds or None)
  - `apply_decision(row: OffsetRow, kind: str, off_s: float|None) -> OffsetRow`

- [ ] **Step 1: Write failing tests**

```python
# scripts/original_vocals/test_align_core.py
import numpy as np
from align_core import (first_vocal_onset, variant_offsets, clip_cut, emit_af,
                        OffsetRow, parse_decision, apply_decision, read_offsets, write_offsets)

def test_first_vocal_onset_after_silence():
    sr=8000; sig=np.concatenate([np.zeros(sr*2), 0.5*np.sin(2*np.pi*220*np.arange(sr*3)/sr)]).astype('f4')
    assert abs(first_vocal_onset(sig, sr) - 2.0) < 0.1

def test_first_vocal_onset_immediate():
    sr=8000; sig=(0.5*np.sin(2*np.pi*220*np.arange(sr*2)/sr)).astype('f4')
    assert first_vocal_onset(sig, sr) < 0.2

def test_variant_offsets_default():
    assert variant_offsets(5.0) == [5.0, 4.9, 5.1, 4.8, 5.2]

def test_variant_offsets_clamps_negative():
    assert all(o >= 0 for o in variant_offsets(0.05))

def test_clip_cut_correct_candidate_matches_measured():
    vstart, gstart, dur = clip_cut(measured_s=5.0, onset_s=10.0, candidate_s=5.0)
    assert abs(vstart - 12.0) < 1e-6      # 5 + 10 - 3
    assert abs(gstart - 7.0) < 1e-6       # onset - before  (candidate==measured)
    assert abs(dur - 15.0) < 1e-6

def test_clip_cut_wrong_candidate_shifts_guide():
    # candidate 0.1s later than measured -> guide cut 0.1s earlier so it plays late in-clip
    _, gstart, _ = clip_cut(measured_s=5.0, onset_s=10.0, candidate_s=5.1)
    assert abs(gstart - 6.9) < 1e-6

def test_emit_af_shape():
    af = emit_af(4.98, 200.0)
    assert "adelay=4980:all=1" in af and "atrim=0:200.000" in af

def test_parse_decision():
    assert parse_decision("confirm") == ("confirm", None)
    assert parse_decision("exclude") == ("exclude", None)
    assert parse_decision("needs-finer") == ("needs-finer", None)
    assert parse_decision("offset_ms=4870") == ("offset", 4.870)

def test_apply_decision_offset_sets_human_source():
    r = OffsetRow("NOMAD-0300",5.0,0.9,"confirmed",200,190,10.0,"measured","active")
    r2 = apply_decision(r, "offset", 4.87)
    assert r2.offset_s == 4.87 and r2.source == "human" and r2.status == "active"

def test_apply_decision_exclude():
    r = OffsetRow("NOMAD-0300",5.0,0.1,"needs-review",200,190,10.0,"measured","active")
    assert apply_decision(r, "exclude", None).status == "excluded"

def test_offsets_roundtrip(tmp_path):
    rows={"NOMAD-0300":OffsetRow("NOMAD-0300",5.0,0.9,"confirmed",200,190,10.0,"measured","active")}
    p=tmp_path/"o.csv"; write_offsets(str(p), rows)
    assert read_offsets(str(p))["NOMAD-0300"].offset_s == 5.0
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd scripts/original_vocals && /Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_align_core.py -q`
Expected: FAIL (ImportError: align_core).

- [ ] **Step 3: Implement `align_core.py`**

```python
"""Pure alignment logic (no device/ffmpeg I/O) — unit-tested on the Mac."""
import csv
from dataclasses import dataclass, asdict, fields
import numpy as np


def first_vocal_onset(samples, sr, thresh_db=-45.0, win_s=0.05, min_run_s=0.15):
    """First time (s) the isolated-vocals guide has sustained energy >= thresh_db.
    0.0 if it starts immediately / no clear onset."""
    if samples.size == 0:
        return 0.0
    w = max(1, int(win_s * sr))
    need = max(1, int(round(min_run_s / win_s)))
    run = 0
    for i in range(0, samples.size - w, w):
        seg = samples[i:i + w].astype(np.float64)
        rms = float(np.sqrt(np.mean(seg * seg))) if seg.size else 0.0
        db = 20 * np.log10(rms) if rms > 1e-9 else -120.0
        if db >= thresh_db:
            run += 1
            if run >= need:
                return max(0.0, (i - (need - 1) * w) / sr)
        else:
            run = 0
    return 0.0


def variant_offsets(measured_s, steps_ms=(0, -100, 100, -200, 200)):
    out = []
    for s in steps_ms:
        v = round(measured_s + s / 1000.0, 3)
        if v >= 0:
            out.append(v)
    return out


def clip_cut(measured_s, onset_s, candidate_s, before=3.0, after=12.0):
    """Video window is fixed on the first-vocal moment (using measured offset);
    the guide cut point shifts by (measured - candidate) so a wrong candidate
    plays audibly off. Returns (video_start, guide_start, dur)."""
    dur = before + after
    video_start = max(0.0, measured_s + onset_s - before)
    guide_start = max(0.0, (video_start - measured_s) + (measured_s - candidate_s))
    return round(video_start, 3), round(guide_start, 3), round(dur, 3)


def emit_af(offset_s, target_dur):
    delay_ms = int(round(offset_s * 1000))
    return (f"adelay={delay_ms}:all=1,apad,atrim=0:{target_dur:.3f},asetpts=N/SR/TB")


@dataclass
class OffsetRow:
    brand: str
    offset_s: float
    peak: float
    verdict: str
    video_dur: float
    audio_dur: float
    onset_s: float
    source: str      # measured | human
    status: str      # active | excluded


def parse_decision(value):
    v = (value or "").strip()
    if v.startswith("offset_ms="):
        return "offset", round(int(v.split("=", 1)[1]) / 1000.0, 3)
    if v in ("confirm", "exclude", "needs-finer"):
        return v, None
    return v, None


def apply_decision(row, kind, off_s):
    d = asdict(row)
    if kind == "exclude":
        d["status"] = "excluded"
    elif kind == "offset" and off_s is not None:
        d["offset_s"] = off_s
        d["source"] = "human"
        d["status"] = "active"
    elif kind == "confirm":
        d["status"] = "active"
    return OffsetRow(**d)


def write_offsets(path, rows):
    cols = [f.name for f in fields(OffsetRow)]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows.values():
            w.writerow(asdict(r))


def read_offsets(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for d in csv.DictReader(f):
            out[d["brand"]] = OffsetRow(
                d["brand"], float(d["offset_s"]), float(d["peak"]), d["verdict"],
                float(d["video_dur"]), float(d["audio_dur"]), float(d["onset_s"]),
                d["source"], d["status"])
    return out
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd scripts/original_vocals && /Users/andrew/miniforge3/envs/nomadkaraoke/bin/python -m pytest test_align_core.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add scripts/original_vocals/align_core.py scripts/original_vocals/test_align_core.py
git commit -m "feat(align): pure alignment core (onset, variants, clip-cut, offset store, decisions)"
```

---

### Task 2: `align_measure.py` — build the offset store

**Files:**
- Create: `scripts/original_vocals/align_measure.py`
- Test: `scripts/original_vocals/test_align_measure.py`

**Interfaces:**
- Consumes: `verify_sync.verify_pair`, `verify_sync._index_by_brand`, `verify_sync.decode_mono`; `align_core.{OffsetRow, first_vocal_onset, write_offsets}`.
- Produces: `build_offset_row(sync_result, onset_s) -> OffsetRow` (status `active` for confirmed, `active` for needs-review too — status is about exclusion, verdict drives review); CLI writing `align_offsets.csv`.

- [ ] **Step 1: Write failing test** (pure row-builder; verify_pair is exercised by its own tests)

```python
# test_align_measure.py
from types import SimpleNamespace
from align_measure import build_offset_row
def test_build_offset_row_maps_fields():
    sr = SimpleNamespace(brand_code="NOMAD-0300", offset_s=4.98, peak=0.91,
                         verdict="confirmed", video_dur=200.0, audio_dur=190.0, onset_s=-1.0)
    row = build_offset_row(sr, onset_s=10.2)
    assert row.brand=="NOMAD-0300" and row.offset_s==4.98 and row.verdict=="confirmed"
    assert row.onset_s==10.2 and row.source=="measured" and row.status=="active"
```

- [ ] **Step 2: Run, verify fail** — `pytest test_align_measure.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement `align_measure.py`**

```python
"""Measure per-track offset (via verify_sync) + guide first-vocal-onset -> align_offsets.csv.
Run ON the device (numpy required)."""
import argparse, glob, os, sys
import verify_sync as vs
from align_core import OffsetRow, first_vocal_onset, write_offsets


def build_offset_row(sr, onset_s):
    return OffsetRow(sr.brand_code, round(sr.offset_s, 3), round(sr.peak, 3), sr.verdict,
                     round(sr.video_dur, 3), round(sr.audio_dur, 3), round(onset_s, 3),
                     "measured", "active")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio-dir", required=True)
    ap.add_argument("--video-dir", required=True)
    ap.add_argument("--guide-dir", required=True)          # NOMAD-vocals (raw)
    ap.add_argument("--out", default="align_offsets.csv")
    ap.add_argument("--only", default=None)
    a = ap.parse_args(argv)
    aidx = vs._index_by_brand(glob.glob(os.path.join(a.audio_dir, "*")))
    vidx = vs._index_by_brand(glob.glob(os.path.join(a.video_dir, "*")))
    gidx = vs._index_by_brand(glob.glob(os.path.join(a.guide_dir, "*")))
    brands = sorted(set(aidx) & set(vidx) & set(gidx))
    if a.only:
        brands = [b for b in brands if b.upper() == a.only.upper()]
    rows = {}
    for b in brands:
        try:
            sr = vs.verify_pair(vidx[b], aidx[b])
            g = vs.decode_mono(gidx[b], dur=90)
            onset = first_vocal_onset(g, vs.ANALYSIS_SR)
            rows[b] = build_offset_row(sr, onset)
            print(f"{b}: {sr.verdict:12s} off={sr.offset_s:.3f}s peak={sr.peak:.2f} onset={onset:.2f}s")
        except Exception as e:
            print(f"{b}: ERROR {e}", file=sys.stderr)
    write_offsets(a.out, rows)
    conf = sum(1 for r in rows.values() if r.verdict == "confirmed")
    print(f"\n{conf}/{len(rows)} confirmed -> {a.out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, verify pass** — `pytest test_align_measure.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/original_vocals/align_measure.py scripts/original_vocals/test_align_measure.py
git commit -m "feat(align): align_measure driver -> align_offsets.csv (offset+onset+verdict)"
```

---

### Task 3: `align_clips.py` — render review clips + decisions template

**Files:**
- Create: `scripts/original_vocals/align_clips.py`
- Test: `scripts/original_vocals/test_align_clips.py`

**Interfaces:**
- Consumes: `align_core.{read_offsets, variant_offsets, clip_cut, first_vocal_onset}`, `verify_sync.decode_mono`.
- Produces:
  - `select_review(rows: dict, spot_frac=0.07, seed=1) -> tuple[list[str], list[str]]` → (flagged brands, spot-check brands) — deterministic sampling.
  - `clip_name(brand, artist_title, candidate_s) -> str`
  - `ffmpeg_clip_cmd(video, guide, video_start, guide_start, dur, out, gain=0.65) -> list[str]`
  - CLI: render clips for selected brands (variant comb for flagged, single for spot-check) into `--out-dir`; write `align_decisions.csv` template (one row per reviewed brand, blank `decision`).

- [ ] **Step 1: Write failing tests** (pure parts)

```python
# test_align_clips.py
from align_core import OffsetRow
from align_clips import select_review, clip_name, ffmpeg_clip_cmd

def _rows(n, verdict):
    return {f"NOMAD-{i:04d}": OffsetRow(f"NOMAD-{i:04d}",5.0,0.9 if verdict=="confirmed" else 0.1,
            verdict,200,190,10.0,"measured","active") for i in range(n)}

def test_select_review_all_flagged_plus_spotcheck():
    rows = {**_rows(100,"confirmed")}
    rows.update({f"NOMAD-9{i:03d}": OffsetRow(f"NOMAD-9{i:03d}",5.0,0.1,"needs-review",200,190,10.0,"measured","active") for i in range(10)})
    flagged, spot = select_review(rows, spot_frac=0.07, seed=1)
    assert len(flagged)==10
    assert 5 <= len(spot) <= 9          # ~7% of 100
    assert set(spot).isdisjoint(flagged)

def test_select_review_deterministic():
    rows=_rows(100,"confirmed")
    assert select_review(rows,seed=1)[1] == select_review(rows,seed=1)[1]

def test_clip_name_encodes_offset():
    assert clip_name("NOMAD-0300","Frightened Rabbit - Square 9",4.98).endswith("__off=4.980s.mp4")

def test_ffmpeg_clip_cmd_uses_two_seeked_inputs_and_amix():
    cmd = ffmpeg_clip_cmd("v.mp4","g.flac",12.0,7.0,15.0,"out.mp4")
    s=" ".join(cmd)
    assert "-ss 12.000" in s and "-ss 7.000" in s and "amix=inputs=2" in s and s.endswith("out.mp4")
```

- [ ] **Step 2: Run, verify fail** — `pytest test_align_clips.py -q` → FAIL.

- [ ] **Step 3: Implement `align_clips.py`**

```python
"""Render pre-rendered A/V review clips (guide mixed into video at candidate offsets)
+ a decisions template. Run ON the device (ffmpeg). Pure selection/命名/cmd are unit-tested."""
import argparse, csv, hashlib, os, subprocess
from align_core import read_offsets, variant_offsets, clip_cut

FFMPEG = "ffmpeg"


def _stable_rank(brand, seed):
    return int(hashlib.sha1(f"{seed}:{brand}".encode()).hexdigest(), 16)


def select_review(rows, spot_frac=0.07, seed=1):
    flagged = [b for b, r in rows.items() if r.status == "active" and r.verdict == "needs-review"]
    confirmed = [b for b, r in rows.items() if r.status == "active" and r.verdict == "confirmed"]
    k = max(1, int(round(len(confirmed) * spot_frac))) if confirmed else 0
    spot = sorted(confirmed, key=lambda b: _stable_rank(b, seed))[:k]
    return sorted(flagged), sorted(spot)


def clip_name(brand, artist_title, candidate_s):
    safe = artist_title.replace("/", "_")
    return f"{brand} - {safe}__off={candidate_s:.3f}s.mp4"


def ffmpeg_clip_cmd(video, guide, video_start, guide_start, dur, out, gain=0.65):
    return [FFMPEG, "-y", "-v", "error",
            "-ss", f"{video_start:.3f}", "-t", f"{dur:.3f}", "-i", video,
            "-ss", f"{guide_start:.3f}", "-t", f"{dur:.3f}", "-i", guide,
            "-filter_complex",
            f"[1:a]volume={gain}[g];[0:a][g]amix=inputs=2:normalize=0[a]",
            "-map", "0:v", "-map", "[a]", "-shortest",
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", out]


def _title_from(name):
    base = os.path.splitext(os.path.basename(name))[0]
    return base.split(" - ", 1)[1] if " - " in base else base


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offsets", default="align_offsets.csv")
    ap.add_argument("--video-dir", required=True)
    ap.add_argument("--guide-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--fine", action="store_true", help="fine comb [0,±25,±50]ms (for needs-finer round)")
    ap.add_argument("--only", nargs="*", help="restrict to these brands (fine round)")
    a = ap.parse_args(argv)
    import glob
    import verify_sync as vs
    rows = read_offsets(a.offsets)
    vidx = vs._index_by_brand(glob.glob(os.path.join(a.video_dir, "*")))
    gidx = vs._index_by_brand(glob.glob(os.path.join(a.guide_dir, "*")))
    os.makedirs(a.out_dir, exist_ok=True)
    steps = (0, -25, 25, -50, 50) if a.fine else (0, -100, 100, -200, 200)
    flagged, spot = select_review(rows)
    review = (a.only if a.only else flagged + spot)
    dec_rows = []
    for b in review:
        r = rows.get(b)
        if not r or b not in vidx or b not in gidx:
            continue
        cands = variant_offsets(r.offset_s, steps) if (r.verdict == "needs-review" or a.fine) else [r.offset_s]
        for cand in cands:
            vstart, gstart, dur = clip_cut(r.offset_s, r.onset_s, cand)
            out = os.path.join(a.out_dir, clip_name(b, _title_from(vidx[b]), cand))
            subprocess.run(ffmpeg_clip_cmd(vidx[b], gidx[b], vstart, gstart, dur, out),
                           check=False)
        dec_rows.append({"brand": b, "verdict": r.verdict, "measured_offset_s": r.offset_s,
                         "decision": ""})
        print(f"{b}: {len(cands)} clip(s)")
    with open(os.path.join(a.out_dir, "align_decisions.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["brand", "verdict", "measured_offset_s", "decision"])
        w.writeheader(); w.writerows(dec_rows)
    print(f"\n{len(dec_rows)} tracks to review -> {a.out_dir} (fill 'decision': confirm|offset_ms=<n>|exclude|needs-finer)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, verify pass** — `pytest test_align_clips.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/original_vocals/align_clips.py scripts/original_vocals/test_align_clips.py
git commit -m "feat(align): align_clips renders review clips (variant comb) + decisions template"
```

---

### Task 4: `align_apply.py` — merge decisions + emit aligned guides

**Files:**
- Create: `scripts/original_vocals/align_apply.py`
- Test: `scripts/original_vocals/test_align_apply.py`

**Interfaces:**
- Consumes: `align_core.{read_offsets, write_offsets, parse_decision, apply_decision, emit_af, OffsetRow}`.
- Produces:
  - `merge_decisions(rows: dict, decisions: list[dict]) -> tuple[dict, list[str]]` → (updated rows, `needs_finer` brands).
  - `emit_cmd(guide, out, offset_s, video_dur) -> list[str]` (ffmpeg, flac).
  - CLI: apply `align_decisions.csv` → update `align_offsets.csv`; for `active` emit aligned guide to `--padded-dir`; for `excluded` delete guide from `--padded-dir` + `--raw-dir`; print the list of excluded brands (so Mac-side copies can be removed too).

- [ ] **Step 1: Write failing tests**

```python
# test_align_apply.py
from align_core import OffsetRow
from align_apply import merge_decisions, emit_cmd

def _row(b,verdict="needs-review"): return OffsetRow(b,5.0,0.1,verdict,200,190,10.0,"measured","active")

def test_merge_offset_and_exclude_and_finer():
    rows={"NOMAD-1":_row("NOMAD-1"),"NOMAD-2":_row("NOMAD-2"),"NOMAD-3":_row("NOMAD-3")}
    decs=[{"brand":"NOMAD-1","decision":"offset_ms=4870"},
          {"brand":"NOMAD-2","decision":"exclude"},
          {"brand":"NOMAD-3","decision":"needs-finer"}]
    updated, finer = merge_decisions(rows, decs)
    assert updated["NOMAD-1"].offset_s==4.87 and updated["NOMAD-1"].source=="human"
    assert updated["NOMAD-2"].status=="excluded"
    assert finer==["NOMAD-3"]

def test_emit_cmd_pads_and_trims_to_video():
    cmd=emit_cmd("g.flac","out.flac",4.98,200.0)
    s=" ".join(cmd)
    assert "adelay=4980:all=1" in s and "atrim=0:200.000" in s and s.endswith("out.flac")
```

- [ ] **Step 2: Run, verify fail** — `pytest test_align_apply.py -q` → FAIL.

- [ ] **Step 3: Implement `align_apply.py`**

```python
"""Merge human decisions into align_offsets.csv, then emit aligned guides to the
padded dir (active) and remove guides for excluded tracks. Run ON the device (ffmpeg)."""
import argparse, csv, glob, os, subprocess
from align_core import read_offsets, write_offsets, parse_decision, apply_decision, emit_af

FFMPEG = "ffmpeg"


def merge_decisions(rows, decisions):
    finer = []
    for d in decisions:
        b = d["brand"]; val = d.get("decision", "").strip()
        if not val or b not in rows:
            continue
        kind, off = parse_decision(val)
        if kind == "needs-finer":
            finer.append(b); continue
        rows[b] = apply_decision(rows[b], kind, off)
    return rows, finer


def emit_cmd(guide, out, offset_s, video_dur):
    return [FFMPEG, "-v", "error", "-y", "-i", guide,
            "-af", emit_af(offset_s, video_dur), "-c:a", "flac", out]


def _index(d):
    import re
    idx = {}
    for p in glob.glob(os.path.join(d, "*")):
        m = re.match(r"(NOMAD-\d{4})", os.path.basename(p))
        if m:
            idx.setdefault(m.group(1), p)
    return idx


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offsets", default="align_offsets.csv")
    ap.add_argument("--decisions", default=None)
    ap.add_argument("--raw-dir", required=True)       # NOMAD-vocals
    ap.add_argument("--padded-dir", required=True)    # NOMAD-vocals-padded
    a = ap.parse_args(argv)
    rows = read_offsets(a.offsets)
    if a.decisions and os.path.exists(a.decisions):
        with open(a.decisions, encoding="utf-8") as f:
            rows, finer = merge_decisions(rows, list(csv.DictReader(f)))
        write_offsets(a.offsets, rows)
        if finer:
            print("needs-finer:", " ".join(finer))
    raw = _index(a.raw_dir); pad = _index(a.padded_dir)
    os.makedirs(a.padded_dir, exist_ok=True)
    emitted = excluded = 0
    for b, r in rows.items():
        if r.status == "excluded":
            for idx in (raw, pad):
                if b in idx:
                    os.remove(idx[b]); 
            excluded += 1
            print("EXCLUDE", b)
            continue
        if b not in raw:
            continue
        base = os.path.splitext(os.path.basename(raw[b]))[0]
        out = os.path.join(a.padded_dir, base + ".flac")
        subprocess.run(emit_cmd(raw[b], out + ".part", r.offset_s, r.video_dur), check=True)
        os.replace(out + ".part", out)
        emitted += 1
    print(f"emitted {emitted} aligned guides; excluded {excluded}. (Remove excluded from Mac copies too.)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, verify pass** — `pytest test_align_apply.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/original_vocals/align_apply.py scripts/original_vocals/test_align_apply.py
git commit -m "feat(align): align_apply merges decisions + emits aligned guides / removes excluded"
```

---

### Task 5: Device validation + operational runbook (one confirmed + one flagged track)

**Files:**
- Modify: `docs/archive/2026-07-08-vocal-guide-alignment-plan.md` (append a "run log" section as you go) — optional.

**Interfaces:** Consumes all of Tasks 1–4. No new code (integration/validation only).

- [ ] **Step 1: numpy on device (already in the kj-controller venv — no install)**

The device `/usr/bin/python3` has **no pip/numpy**; the kj-controller venv already ships numpy, so run the align tools with that interpreter (verified 2026-07-08):

Run: `ssh nomadpctunnel '/opt/nomad/kjbox/kj-controller/venv/bin/python -c "import numpy; print(numpy.__version__)"'`
Expected: prints a numpy version (2.4.2 as of 2026-07-08).

- [ ] **Step 2: scp the align tools + verify_sync to device**

```bash
scp scripts/original_vocals/{verify_sync.py,align_core.py,align_measure.py,align_clips.py,align_apply.py} nomadpctunnel:/tmp/
```

- [ ] **Step 3: Measure two tracks (one normal, one FR no-outro) → offsets**

Run:
```bash
ssh nomadpctunnel 'cd /tmp && python3 align_measure.py --audio-dir /opt/nomad/downloads/NOMAD-audio --video-dir /opt/nomad/downloads/NOMAD-720p --guide-dir /opt/nomad/downloads/NOMAD-vocals --only NOMAD-0900 --out /tmp/ao_0900.csv && cat /tmp/ao_0900.csv'
```
Expected: a row with a plausible offset (~5 s) and `verdict=confirmed`, `onset_s` > 0.

- [ ] **Step 4: Render a variant-comb clip set for one flagged track + eyeball**

Run `align_clips.py --only <a needs-review brand>` against `/tmp/ao_*.csv`; scp one clip back; confirm it plays and the guide is audibly mixed over the video around the first vocal.

- [ ] **Step 5: Emit one aligned guide + verify duration == video**

Run `align_apply.py` (no decisions) for one brand into a temp padded dir; `ffprobe` the output; assert its duration equals the video duration (±0.1 s).

- [ ] **Step 6: Commit run log (if edited)**

```bash
git add -A && git commit -m "chore(align): device validation run log"
```

---

## Full-run sequence (after Tasks 1–5 pass — this is the actual data run, not code)
1. `align_measure.py` over all active tracks → `align_offsets.csv` (note confirmed/needs-review split).
2. `align_clips.py` → render clips for flagged + ~7 % spot-check → device folder → `rsync` to a Dropbox review folder.
3. User reviews, fills `align_decisions.csv` in the review folder → `rsync` back to device.
4. `align_apply.py --decisions align_decisions.csv` → updates offsets, emits aligned guides to `NOMAD-vocals-padded`, removes excluded (also delete excluded from Mac `Tracks-Audio/{Original,Vocals}`).
5. For any `needs-finer`: `align_clips.py --fine --only <brands>` → review → apply. Repeat until none.
6. Spot-check the emitted set by ear on-device via the live feature (`/control` play; raise Original Vocals slider).

## Self-review notes
- Spec coverage: A→Task2/verify_sync; B→verify_sync verdict surfaced in Task2; C→Task3 (clips, comb, spot-check, decisions, exclude outcome); D→Task4 (offset store, emit, exclude); scope/ordering→Task5 + full-run sequence. ✓
- No placeholders; every code step has full code. ✓
- Type consistency: `OffsetRow` fields + `parse_decision`/`apply_decision`/`emit_af` signatures identical across Tasks 1/2/4. ✓


## Device validation run log (Task 5 — 2026-07-08, device idle, non-disruptive)
Ran with the kj-controller venv python (`/opt/nomad/kjbox/kj-controller/venv/bin/python`, numpy 2.4.2 — no install needed; `/usr/bin/python3` has no pip/numpy). Tools scp'd to `/tmp`, cleaned up after.
- **measure** `NOMAD-0900` → `confirmed`, offset **5.063s**, peak 0.95, onset 5.35s, video 273.088s / audio 262.613s. (Measured ≠ old flat 5.0s pad.)
- **clips** → valid `.mp4`, video+audio streams, **15.000s** (before 3 + after 12); decisions template written.
- **emit** (confirmed) → valid `.flac`, duration **273.088005s** == video_dur ✓. `-f flac` + `.part`→`os.replace` flow worked on device ffmpeg.
- **gate** (synthetic needs-review + measured row) → `SKIP`, emitted 0 / skipped 1, output dir empty ✓ (unverified offset never ships).
Device left idle (state=stopped), /tmp pristine.
