# Playability Checker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a belt-and-braces playability checker for the kjbox karaoke library that verifies a file actually *renders video* (not just audio) in both VLC and mpv, supports CDG zips, runs without touching the live screen/audio, and is wired as a hard gate on link/upload/download plus a resumable library-wide batch.

**Architecture:** A single `PlayabilityChecker` runs a cheap→expensive pipeline (ffprobe integrity → ffmpeg decode → real-renderer Xvfb frame-capture → CDG sub-pipeline) and returns structured per-layer evidence (`PlayabilityResult`). Callers (link/upload/download/batch) decide policy from the verdict. The batch tool streams results to JSONL and aggregates a per-file VLC-vs-mpv matrix.

**Tech Stack:** Python 3 (Flask app, flat modules in `kj-controller/`), `ffprobe`/`ffmpeg`, VLC 3.0.20 (`cvlc`), mpv 0.37.0, Xvfb (to be installed on box), Pillow (frame analysis), pytest + pytest-mock.

## Global Constraints

- **Module location:** all new modules are flat files in `kj-controller/` (match existing layout); tests in `kj-controller/tests/unit/`.
- **Logging helper:** use `from config import ...`-style imports and `log_message(msg, config)` for any operational logging (matches codebase).
- **Never touch live output:** no check may render to display `:0` or open audio device `hw:0,0`. VLC renders into Xvfb `:99` with `--no-audio`; mpv uses `--vo=image` + `--ao=null`.
- **Subprocess safety:** every external process gets a hard `timeout` and runs at low priority (`nice -n 19 ionice -c3 …`); concurrency 1.
- **Production safety (kjbox CLAUDE.md):** NO `git push` to main, NO `systemctl restart kj-controller`, NO `apt install` on the box, and NO test runs on the device without explicit user permission at that moment. Local commits are fine.
- **Renderers tested:** both `vlc` and `mpv`, always recorded per file (feeds the future mpv-primary switch).
- **Rollout order:** engine → batch → confidence run on full library → tune to zero false-positives → only then wire hard gates. Gates are the LAST tasks.
- **Verified box paths:** download_folder `/opt/nomad/YTDownloads`; media_folders `/opt/nomad/YTDownloads`, `/opt/nomad/MP4-720p`; 4TB SSD `/media/nomad/Nomad4TBOne`; `render_mode: vlc`.

---

## File Structure

| File | Responsibility |
|---|---|
| `kj-controller/frame_analysis.py` (new) | Pure Pillow frame math: is a PNG blank/black, do two frames differ, judge a renderer's captured frames. |
| `kj-controller/playability.py` (new) | `PlayabilityResult` dataclass; ffprobe/ffmpeg parsers; kind classification; CDG sub-pipeline; `PlayabilityChecker.check()` orchestration + `compute_verdict`. |
| `kj-controller/playability_render.py` (new) | `XvfbDisplay` context manager; VLC/mpv capture command builders (pure); `capture_frames()` + `render_check()` runners. |
| `kj-controller/playability_batch.py` (new) | Library walker, mtime/size skip-manifest, JSONL streaming writer, report aggregation (CSV + Markdown), `main()` CLI. |
| `kj-controller/routes.py` (modify) | Hard gate in `/rotation/link` (≈line 2669) and `/upload` (≈line 242). |
| `kj-controller/media.py` (modify) | Post-download gate in `download_video`/`download_from_url`; cache results in the media index. |
| `kj-controller/requirements.txt` (modify) | Add `Pillow`. |
| `kj-controller/tests/unit/test_*.py` (new) | One test module per new code module + gate tests. |

---

## Task 1: Frame analysis (pure Pillow functions)

**Files:**
- Create: `kj-controller/frame_analysis.py`
- Modify: `kj-controller/requirements.txt` (add `Pillow`)
- Test: `kj-controller/tests/unit/test_frame_analysis.py`

**Interfaces:**
- Produces: `analyze_frame(path, blank_spread_threshold=6.0) -> FrameStats`; `frames_differ(path_a, path_b, min_mean_abs_diff=2.0) -> bool`; `judge_renderer_frames(frame_paths: list[str]) -> dict` with keys `frame_captured, frame_nonblank, frame_varies, frames`. `FrameStats` has `path, exists, mean_luma, spread, is_blank`.

- [ ] **Step 1: Add Pillow to requirements**

Append to `kj-controller/requirements.txt`:
```
Pillow
```

- [ ] **Step 2: Write the failing tests**

Create `kj-controller/tests/unit/test_frame_analysis.py`:
```python
"""Unit tests for frame_analysis (pure Pillow frame math)."""
import os

import pytest
from PIL import Image

import frame_analysis as fa


def _save(tmp_path, name, img):
    p = os.path.join(str(tmp_path), name)
    img.save(p)
    return p


def test_black_frame_is_blank(tmp_path):
    p = _save(tmp_path, "black.png", Image.new("RGB", (64, 48), (0, 0, 0)))
    s = fa.analyze_frame(p)
    assert s.exists is True
    assert s.is_blank is True
    assert s.spread < 1.0


def test_solid_colour_frame_is_blank(tmp_path):
    p = _save(tmp_path, "solid.png", Image.new("RGB", (64, 48), (40, 120, 200)))
    s = fa.analyze_frame(p)
    assert s.is_blank is True  # uniform => no real picture


def test_detailed_frame_is_not_blank(tmp_path):
    img = Image.new("L", (64, 48))
    img.putdata([(i * 7) % 256 for i in range(64 * 48)])  # high-variance pattern
    p = _save(tmp_path, "noise.png", img)
    s = fa.analyze_frame(p)
    assert s.is_blank is False
    assert s.spread > 6.0


def test_missing_or_empty_file_is_blank_and_absent(tmp_path):
    assert fa.analyze_frame(os.path.join(str(tmp_path), "nope.png")).exists is False
    empty = os.path.join(str(tmp_path), "empty.png")
    open(empty, "wb").close()
    s = fa.analyze_frame(empty)
    assert s.exists is False and s.is_blank is True


def test_frames_differ_true_for_different_images(tmp_path):
    a = _save(tmp_path, "a.png", Image.new("RGB", (64, 48), (0, 0, 0)))
    b = _save(tmp_path, "b.png", Image.new("RGB", (64, 48), (200, 200, 200)))
    assert fa.frames_differ(a, b) is True


def test_frames_differ_false_for_identical(tmp_path):
    a = _save(tmp_path, "a.png", Image.new("RGB", (64, 48), (10, 10, 10)))
    b = _save(tmp_path, "b.png", Image.new("RGB", (64, 48), (10, 10, 10)))
    assert fa.frames_differ(a, b) is False


def test_judge_renderer_frames_real_video(tmp_path):
    img = Image.new("L", (64, 48))
    img.putdata([(i * 7) % 256 for i in range(64 * 48)])
    f1 = _save(tmp_path, "f1.png", img)
    img2 = img.rotate(90)
    f2 = _save(tmp_path, "f2.png", img2)
    v = fa.judge_renderer_frames([f1, f2])
    assert v["frame_captured"] is True
    assert v["frame_nonblank"] is True
    assert v["frame_varies"] is True


def test_judge_renderer_frames_all_black(tmp_path):
    f1 = _save(tmp_path, "b1.png", Image.new("RGB", (64, 48), (0, 0, 0)))
    f2 = _save(tmp_path, "b2.png", Image.new("RGB", (64, 48), (0, 0, 0)))
    v = fa.judge_renderer_frames([f1, f2])
    assert v["frame_captured"] is True
    assert v["frame_nonblank"] is False
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_frame_analysis.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'frame_analysis'`). If Pillow is missing, `pip install Pillow` into the venv first (local dev machine only — NOT the box without permission).

- [ ] **Step 4: Implement `frame_analysis.py`**

Create `kj-controller/frame_analysis.py`:
```python
"""Pure frame-analysis helpers for the playability render check.

Operates on PNG screenshots captured from a video renderer. No video/audio
I/O — just pixel math, so it is fully unit-testable with synthetic PNGs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageStat

# A frame whose luma standard deviation is below this is treated as "no real
# picture" (pure black, solid colour, or a flat title card with no detail).
# Deliberately lenient; calibrated against the real library before gating.
BLANK_SPREAD_THRESHOLD = 6.0


@dataclass
class FrameStats:
    path: str
    exists: bool
    mean_luma: float
    spread: float
    is_blank: bool


def analyze_frame(path: str, blank_spread_threshold: float = BLANK_SPREAD_THRESHOLD) -> FrameStats:
    if not path or not os.path.isfile(path) or os.path.getsize(path) == 0:
        return FrameStats(path=path, exists=False, mean_luma=0.0, spread=0.0, is_blank=True)
    try:
        with Image.open(path) as im:
            gray = im.convert("L")
            stat = ImageStat.Stat(gray)
            mean_luma = float(stat.mean[0])
            spread = float(stat.stddev[0])
    except OSError:
        return FrameStats(path=path, exists=False, mean_luma=0.0, spread=0.0, is_blank=True)
    return FrameStats(
        path=path, exists=True, mean_luma=mean_luma, spread=spread,
        is_blank=spread < blank_spread_threshold,
    )


def frames_differ(path_a: str, path_b: str, min_mean_abs_diff: float = 2.0) -> bool:
    """True if two frames differ enough to indicate the picture is moving."""
    if not (os.path.isfile(path_a) and os.path.isfile(path_b)):
        return False
    try:
        with Image.open(path_a) as a, Image.open(path_b) as b:
            ga, gb = a.convert("L"), b.convert("L")
            if ga.size != gb.size:
                gb = gb.resize(ga.size)
            mad = float(ImageStat.Stat(ImageChops.difference(ga, gb)).mean[0])
    except OSError:
        return False
    return mad >= min_mean_abs_diff


def judge_renderer_frames(frame_paths: list[str]) -> dict:
    """Verdict over the frames captured from one renderer.

    Produces video if at least one captured frame is a real (non-blank)
    image. Records whether frames vary (motion) as a secondary signal.
    """
    stats = [analyze_frame(p) for p in frame_paths]
    existing = [s.path for s in stats if s.exists]
    varies = False
    for i in range(len(existing)):
        for j in range(i + 1, len(existing)):
            if frames_differ(existing[i], existing[j]):
                varies = True
                break
        if varies:
            break
    return {
        "frame_captured": any(s.exists for s in stats),
        "frame_nonblank": any(s.exists and not s.is_blank for s in stats),
        "frame_varies": varies,
        "frames": [s.__dict__ for s in stats],
    }
```

- [ ] **Step 5: Run tests, verify pass, commit**

Run: `cd kj-controller && python -m pytest tests/unit/test_frame_analysis.py -q` → Expected: PASS
```bash
git add kj-controller/frame_analysis.py kj-controller/tests/unit/test_frame_analysis.py kj-controller/requirements.txt
git commit -m "feat(playability): pure Pillow frame-analysis helpers"
```

---

## Task 2: Result schema + integrity parser + kind classification

**Files:**
- Create: `kj-controller/playability.py`
- Test: `kj-controller/tests/unit/test_playability.py`

**Interfaces:**
- Produces: `classify_kind(path) -> str` ('video'|'audio'|'cdg_zip'|'unknown'); `parse_integrity(returncode, stdout, stderr) -> dict` with keys `ok, has_video, has_audio, vcodec, acodec, container, duration, moov_ok, error`; `PlayabilityResult` dataclass with `to_dict()`/`from_dict()` and fields `path, kind, size, mtime, checked_at, elapsed_s, integrity, decode, renderers, cdg, verdict`.

- [ ] **Step 1: Write the failing tests**

Create `kj-controller/tests/unit/test_playability.py`:
```python
"""Unit tests for playability parsers, classification, result schema."""
import json

import playability as pl


def test_classify_kind():
    assert pl.classify_kind("/x/song.mp4") == "video"
    assert pl.classify_kind("/x/song.MKV") == "video"
    assert pl.classify_kind("/x/song.zip") == "cdg_zip"
    assert pl.classify_kind("/x/song.mp3") == "audio"
    assert pl.classify_kind("/x/song.txt") == "unknown"


def test_parse_integrity_valid_video():
    stdout = json.dumps({
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "212.5"},
    })
    r = pl.parse_integrity(0, stdout, "")
    assert r["ok"] is True
    assert r["has_video"] and r["has_audio"]
    assert r["vcodec"] == "h264" and r["acodec"] == "aac"
    assert r["duration"] == 212.5
    assert r["moov_ok"] is True
    assert r["error"] is None


def test_parse_integrity_truncated_moov():
    r = pl.parse_integrity(1, "", "[mov,mp4 @ 0x..] moov atom not found\n")
    assert r["ok"] is False
    assert r["moov_ok"] is False
    assert "moov atom not found" in r["error"]


def test_parse_integrity_no_video_stream():
    stdout = json.dumps({
        "streams": [{"codec_type": "audio", "codec_name": "mp3"}],
        "format": {"format_name": "mp3", "duration": "180.0"},
    })
    r = pl.parse_integrity(0, stdout, "")
    assert r["has_video"] is False and r["has_audio"] is True
    assert r["ok"] is True  # structurally valid; video requirement applied in verdict


def test_result_roundtrip():
    res = pl.PlayabilityResult(path="/x/a.mp4", kind="video", size=10, mtime=1.0)
    res.integrity = {"ok": True}
    d = res.to_dict()
    assert json.loads(json.dumps(d))["integrity"]["ok"] is True
    back = pl.PlayabilityResult.from_dict(d)
    assert back.path == "/x/a.mp4" and back.kind == "video"
```

- [ ] **Step 2: Run, verify fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'playability'`).

- [ ] **Step 3: Implement schema + parsers in `playability.py`**

Create `kj-controller/playability.py`:
```python
"""PlayabilityChecker: multi-layer playability probe for karaoke media.

Produces structured evidence (PlayabilityResult), never policy. Callers
(link / upload / download / batch) decide how to react to the verdict.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".webm", ".mov"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg"}
CDG_ZIP_EXTS = {".zip"}


def classify_kind(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in CDG_ZIP_EXTS:
        return "cdg_zip"
    if ext in AUDIO_EXTS:
        return "audio"
    return "unknown"


def parse_integrity(returncode: int, stdout: str, stderr: str) -> dict:
    stderr = stderr or ""
    moov_ok = "moov atom not found" not in stderr
    has_video = has_audio = False
    vcodec = acodec = container = None
    duration = None
    if stdout:
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            data = {}
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and not has_video:
                has_video, vcodec = True, s.get("codec_name")
            elif s.get("codec_type") == "audio" and not has_audio:
                has_audio, acodec = True, s.get("codec_name")
        fmt = data.get("format", {})
        container = fmt.get("format_name")
        try:
            duration = float(fmt["duration"]) if fmt.get("duration") else None
        except (TypeError, ValueError, KeyError):
            duration = None
    ok = returncode == 0 and moov_ok and (has_video or has_audio)
    error = None
    if not moov_ok:
        error = "moov atom not found (truncated/incomplete file)"
    elif returncode != 0:
        error = (stderr.strip().splitlines() or ["ffprobe failed"])[-1]
    elif not (has_video or has_audio):
        error = "no decodable streams"
    return {
        "ok": ok, "has_video": has_video, "has_audio": has_audio,
        "vcodec": vcodec, "acodec": acodec, "container": container,
        "duration": duration, "moov_ok": moov_ok, "error": error,
    }


@dataclass
class PlayabilityResult:
    path: str
    kind: str
    size: int = 0
    mtime: float = 0.0
    checked_at: float = 0.0
    elapsed_s: float = 0.0
    integrity: dict = field(default_factory=dict)
    decode: dict = field(default_factory=dict)
    renderers: dict = field(default_factory=dict)
    cdg: dict = field(default_factory=dict)
    verdict: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PlayabilityResult":
        known = {k: d[k] for k in d if k in cls.__dataclass_fields__}
        return cls(**known)
```

- [ ] **Step 4: Run, verify pass, commit**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability.py -q` → Expected: PASS
```bash
git add kj-controller/playability.py kj-controller/tests/unit/test_playability.py
git commit -m "feat(playability): result schema, integrity parser, kind classification"
```

---

## Task 3: Decode parser + ffprobe/ffmpeg runner methods

**Files:**
- Modify: `kj-controller/playability.py`
- Test: `kj-controller/tests/unit/test_playability.py`

**Interfaces:**
- Consumes: `parse_integrity` (Task 2).
- Produces: `parse_decode(returncode, stderr) -> dict` with keys `ok, decode_errors, error`. `PlayabilityChecker(config=None)` with `_run(cmd, timeout) -> (rc, stdout, stderr)`, `probe_integrity(path) -> dict`, `decode_video(path, start=None, length=None) -> dict`.

- [ ] **Step 1: Write failing tests (append to `test_playability.py`)**
```python
import playability as pl


def test_parse_decode_clean():
    r = pl.parse_decode(0, "")
    assert r["ok"] is True and r["decode_errors"] == 0


def test_parse_decode_errors():
    stderr = "[h264 @ 0x] error while decoding MB 1 2\n[h264 @ 0x] concealing errors\n"
    r = pl.parse_decode(1, stderr)
    assert r["ok"] is False
    assert r["decode_errors"] >= 1
    assert r["error"]


def test_probe_integrity_invokes_ffprobe(mocker):
    chk = pl.PlayabilityChecker(config={})
    fake = mocker.patch.object(chk, "_run", return_value=(0, '{"streams":[{"codec_type":"video","codec_name":"h264"}],"format":{"duration":"10.0"}}', ""))
    r = chk.probe_integrity("/x/a.mp4")
    assert r["ok"] is True and r["has_video"] is True
    cmd = fake.call_args[0][0]
    assert "ffprobe" in cmd and "/x/a.mp4" in cmd


def test_decode_video_builds_sampled_window(mocker):
    chk = pl.PlayabilityChecker(config={})
    fake = mocker.patch.object(chk, "_run", return_value=(0, "", ""))
    chk.decode_video("/x/a.mp4", start=30.0, length=5.0)
    cmd = fake.call_args[0][0]
    assert "ffmpeg" in cmd
    assert "-ss" in cmd and "30.0" in cmd
    assert "-t" in cmd and "5.0" in cmd
    assert "-f" in cmd and "null" in cmd
```

- [ ] **Step 2: Run, verify fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability.py -q`
Expected: FAIL (`AttributeError`/`parse_decode` undefined).

- [ ] **Step 3: Implement (append to `playability.py`)**
```python
import re
import shutil
import subprocess

_DECODE_ERR_RE = re.compile(r"\berror\b", re.IGNORECASE)


def parse_decode(returncode: int, stderr: str) -> dict:
    stderr = stderr or ""
    decode_errors = len([ln for ln in stderr.splitlines() if _DECODE_ERR_RE.search(ln)])
    ok = returncode == 0 and decode_errors == 0
    error = None
    if not ok:
        error = (stderr.strip().splitlines() or ["decode failed"])[-1]
    return {"ok": ok, "decode_errors": decode_errors, "error": error}


class PlayabilityChecker:
    """Runs the layered playability pipeline and returns PlayabilityResult."""

    def __init__(self, config=None, *, low_priority=True):
        self.config = config or {}
        self.low_priority = low_priority

    def _run(self, cmd, timeout):
        """Run a subprocess at low priority; return (rc, stdout, stderr)."""
        wrapped = cmd
        if self.low_priority and shutil.which("nice") and shutil.which("ionice"):
            wrapped = ["nice", "-n", "19", "ionice", "-c3"] + cmd
        try:
            p = subprocess.run(wrapped, capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"timeout after {timeout}s"
        except FileNotFoundError as e:
            return 127, "", str(e)

    def probe_integrity(self, path):
        cmd = ["ffprobe", "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", path]
        rc, out, err = self._run(cmd, timeout=30)
        return parse_integrity(rc, out, err)

    def decode_video(self, path, start=None, length=None):
        cmd = ["ffmpeg", "-v", "error", "-xerror"]
        if start is not None:
            cmd += ["-ss", str(start)]
        cmd += ["-i", path]
        if length is not None:
            cmd += ["-t", str(length)]
        cmd += ["-map", "0:v:0", "-f", "null", "-"]
        rc, _out, err = self._run(cmd, timeout=180)
        return parse_decode(rc, err)
```

- [ ] **Step 4: Run, verify pass, commit**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability.py -q` → Expected: PASS
```bash
git add kj-controller/playability.py kj-controller/tests/unit/test_playability.py
git commit -m "feat(playability): decode parser + ffprobe/ffmpeg runner methods"
```

---

## Task 4: Render capture command builders (pure)

**Files:**
- Create: `kj-controller/playability_render.py`
- Test: `kj-controller/tests/unit/test_playability_render.py`

**Interfaces:**
- Produces: `build_mpv_capture_cmd(path, out_dir, start_s, frames=3, fps=1) -> list[str]`; `build_vlc_capture_cmd(path, display, out_dir, start_s, window_s=3, scene_ratio=25) -> list[str]`; `capture_start(duration) -> float`.

- [ ] **Step 1: Write failing tests**

Create `kj-controller/tests/unit/test_playability_render.py`:
```python
"""Unit tests for render command builders + start-time selection."""
import playability_render as pr


def test_capture_start_mid_file_for_long():
    assert pr.capture_start(200.0) == 80.0  # 40%


def test_capture_start_zero_for_short_or_unknown():
    assert pr.capture_start(4.0) == 0.0
    assert pr.capture_start(None) == 0.0


def test_build_mpv_capture_cmd():
    cmd = pr.build_mpv_capture_cmd("/x/a.mp4", "/tmp/out", 30.0, frames=3, fps=1)
    assert cmd[0] == "mpv"
    assert "--no-config" in cmd and "--ao=null" in cmd
    assert "--vo=image" in cmd
    assert "--vo-image-outdir=/tmp/out" in cmd
    assert "--start=30.0" in cmd
    assert "--vf=fps=1" in cmd
    assert "--frames=3" in cmd
    assert cmd[-1] == "/x/a.mp4"


def test_build_vlc_capture_cmd_targets_xvfb_and_no_audio():
    cmd = pr.build_vlc_capture_cmd("/x/a.mp4", ":99", "/tmp/out", 30.0, window_s=3, scene_ratio=25)
    assert cmd[0] == "env" and "DISPLAY=:99" in cmd
    assert "cvlc" in cmd
    assert "--no-audio" in cmd
    assert "--video-filter=scene" in cmd
    assert "--scene-path=/tmp/out" in cmd
    assert "--start-time=30.0" in cmd
    assert "--stop-time=33.0" in cmd
    assert "vlc://quit" in cmd
    assert "/x/a.mp4" in cmd
```

- [ ] **Step 2: Run, verify fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability_render.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement builders in `playability_render.py`**

Create `kj-controller/playability_render.py`:
```python
"""Render-proof layer: drive VLC and mpv to capture real frames off-screen.

VLC renders into an Xvfb virtual display (:99) with --no-audio. mpv uses its
headless image vo (--vo=image) + --ao=null. Neither touches the live :0 screen
or hw:0,0 audio device, so checks are safe during a live show.
"""
from __future__ import annotations

import glob
import os
import shutil
import socket
import subprocess
import time

# Capture from mid-file to skip black intros/outros; short/unknown -> start at 0.
MID_FILE_FRACTION = 0.4
MIN_DURATION_FOR_MIDFILE = 6.0


def capture_start(duration) -> float:
    if not duration or duration < MIN_DURATION_FOR_MIDFILE:
        return 0.0
    return round(duration * MID_FILE_FRACTION, 3)


def build_mpv_capture_cmd(path, out_dir, start_s, frames=3, fps=1):
    return [
        "mpv", "--no-config", "--ao=null",
        "--vo=image", "--vo-image-format=png",
        f"--vo-image-outdir={out_dir}",
        f"--start={start_s}", f"--vf=fps={fps}", f"--frames={frames}",
        "--really-quiet", path,
    ]


def build_vlc_capture_cmd(path, display, out_dir, start_s, window_s=3, scene_ratio=25):
    return [
        "env", f"DISPLAY={display}",
        "cvlc", "--no-audio", "--vout", "x11",
        "--no-video-title-show",
        "--video-filter=scene", "--scene-format=png",
        f"--scene-path={out_dir}", f"--scene-ratio={scene_ratio}",
        f"--start-time={start_s}", f"--stop-time={start_s + window_s}",
        "--play-and-exit", path, "vlc://quit",
    ]
```

- [ ] **Step 4: Run, verify pass, commit**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability_render.py -q` → Expected: PASS
```bash
git add kj-controller/playability_render.py kj-controller/tests/unit/test_playability_render.py
git commit -m "feat(playability): render capture command builders"
```

---

## Task 5: Xvfb manager + capture runner + render_check

**Files:**
- Modify: `kj-controller/playability_render.py`
- Test: `kj-controller/tests/unit/test_playability_render.py`

**Interfaces:**
- Consumes: builders (Task 4); `frame_analysis.judge_renderer_frames` (Task 1).
- Produces: `XvfbDisplay(display=':99', resolution='1280x720x24')` context manager; `render_check(checker_run, path, renderer, duration, display, tmp_root) -> dict` (returns `judge_renderer_frames` output plus `error`, `elapsed_s`). `checker_run` is a callable `(cmd, timeout) -> (rc, out, err)` (the checker's `_run`), injected for testability.

- [ ] **Step 1: Write tests (mocked + tool-gated integration)**

Append to `kj-controller/tests/unit/test_playability_render.py`:
```python
import os
import shutil

import pytest

import playability_render as pr


def _tools_present(*names):
    return all(shutil.which(n) for n in names)


def test_render_check_mpv_judges_captured_frames(tmp_path, mocker):
    from PIL import Image

    def fake_run(cmd, timeout):
        # Simulate mpv writing two real (non-blank) frames into out_dir.
        out_dir = [a.split("=", 1)[1] for a in cmd if a.startswith("--vo-image-outdir=")][0]
        img = Image.new("L", (32, 24))
        img.putdata([(i * 9) % 256 for i in range(32 * 24)])
        img.save(os.path.join(out_dir, "00000001.png"))
        img.rotate(90).save(os.path.join(out_dir, "00000002.png"))
        return (0, "", "")

    res = pr.render_check(fake_run, "/x/a.mp4", "mpv", duration=100.0,
                          display=":99", tmp_root=str(tmp_path))
    assert res["frame_captured"] is True
    assert res["frame_nonblank"] is True
    assert res["error"] is None


def test_render_check_reports_no_frame(tmp_path):
    def fake_run(cmd, timeout):
        return (0, "", "")  # writes nothing

    res = pr.render_check(fake_run, "/x/a.mp4", "mpv", duration=100.0,
                          display=":99", tmp_root=str(tmp_path))
    assert res["frame_captured"] is False
    assert res["frame_nonblank"] is False


@pytest.mark.skipif(not _tools_present("Xvfb", "cvlc"), reason="Xvfb/cvlc not installed")
def test_xvfb_starts_and_stops():
    with pr.XvfbDisplay(display=":97") as d:
        assert os.path.exists("/tmp/.X11-unix/X97")
        assert d.display == ":97"
    # after exit the socket is gone
    assert not os.path.exists("/tmp/.X11-unix/X97")
```

- [ ] **Step 2: Run, verify fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability_render.py -q`
Expected: FAIL (`AttributeError: module has no attribute 'render_check'`). The Xvfb test SKIPs unless tools present.

- [ ] **Step 3: Implement Xvfb + render_check (append to `playability_render.py`)**
```python
class XvfbDisplay:
    """On-demand off-screen X display so VLC can render without touching :0."""

    def __init__(self, display=":99", resolution="1280x720x24", ready_timeout=5.0):
        self.display = display
        self.resolution = resolution
        self.ready_timeout = ready_timeout
        self._proc = None

    def __enter__(self):
        num = self.display.lstrip(":")
        sock = f"/tmp/.X11-unix/X{num}"
        self._proc = subprocess.Popen(
            ["Xvfb", self.display, "-screen", "0", self.resolution, "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            if os.path.exists(sock):
                return self
            if self._proc.poll() is not None:
                raise RuntimeError(f"Xvfb {self.display} exited early")
            time.sleep(0.1)
        self.__exit__(None, None, None)
        raise RuntimeError(f"Xvfb {self.display} not ready in {self.ready_timeout}s")

    def __exit__(self, *exc):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None


def render_check(run, path, renderer, duration, display=":99", tmp_root=None,
                 capture_timeout=90):
    """Capture frames from `renderer` and judge them. `run(cmd, timeout)` does
    the actual subprocess call (injected so this is unit-testable)."""
    import tempfile

    from frame_analysis import judge_renderer_frames

    start = capture_start(duration)
    out_dir = tempfile.mkdtemp(prefix=f"kj-cap-{renderer}-", dir=tmp_root)
    error = None
    t0 = time.monotonic()
    try:
        if renderer == "mpv":
            cmd = build_mpv_capture_cmd(path, out_dir, start)
        elif renderer == "vlc":
            cmd = build_vlc_capture_cmd(path, display, out_dir, start)
        else:
            raise ValueError(f"unknown renderer {renderer}")
        rc, _out, err = run(cmd, capture_timeout)
        if rc not in (0, None):
            error = (err or f"{renderer} exited {rc}").strip().splitlines()[-1] if err else f"{renderer} exited {rc}"
        frames = sorted(glob.glob(os.path.join(out_dir, "*.png")))
        verdict = judge_renderer_frames(frames)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
    verdict["error"] = error if not verdict.get("frame_nonblank") else None
    verdict["elapsed_s"] = round(time.monotonic() - t0, 3)
    return verdict
```

- [ ] **Step 4: Run, verify pass, commit**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability_render.py -q` → Expected: PASS (Xvfb test skipped locally)
```bash
git add kj-controller/playability_render.py kj-controller/tests/unit/test_playability_render.py
git commit -m "feat(playability): Xvfb manager + frame-capture render check"
```

---

## Task 6: CDG sub-pipeline

**Files:**
- Modify: `kj-controller/playability.py`
- Test: `kj-controller/tests/unit/test_playability.py`

**Interfaces:**
- Consumes: existing `zip_playback.ZipPlayback`; `parse_decode` (Task 3).
- Produces: `PlayabilityChecker.check_cdg(path) -> dict` with keys `ok, zip_ok, has_cdg, has_audio, cdg_decodes, audio_decodes, error, extracted_audio` (extracted_audio = path to the `.mp3` for the render layer, or None).

- [ ] **Step 1: Write tests (append to `test_playability.py`)**
```python
import os
import zipfile


def _make_cdg_zip(tmp_path, with_cdg=True, with_audio=True):
    zp = os.path.join(str(tmp_path), "song.zip")
    with zipfile.ZipFile(zp, "w") as zf:
        if with_audio:
            zf.writestr("song.mp3", b"ID3fakeaudio")
        if with_cdg:
            zf.writestr("song.cdg", b"\x00" * 96)  # 4 CDG packets (24 bytes each)
    return zp


def test_check_cdg_missing_cdg(tmp_path):
    chk = pl.PlayabilityChecker(config={})
    zp = _make_cdg_zip(tmp_path, with_cdg=False)
    r = chk.check_cdg(zp)
    assert r["zip_ok"] is True
    assert r["has_cdg"] is False
    assert r["ok"] is False
    assert "cdg" in r["error"].lower()


def test_check_cdg_decodes(tmp_path, mocker):
    chk = pl.PlayabilityChecker(config={})
    zp = _make_cdg_zip(tmp_path, with_cdg=True, with_audio=True)
    # Both ffmpeg decode calls succeed.
    mocker.patch.object(chk, "_run", return_value=(0, "", ""))
    r = chk.check_cdg(zp)
    assert r["zip_ok"] and r["has_cdg"] and r["has_audio"]
    assert r["cdg_decodes"] and r["audio_decodes"]
    assert r["ok"] is True
    assert r["extracted_audio"] and r["extracted_audio"].endswith(".mp3")


def test_check_cdg_bad_zip(tmp_path):
    chk = pl.PlayabilityChecker(config={})
    bad = os.path.join(str(tmp_path), "bad.zip")
    with open(bad, "wb") as f:
        f.write(b"not a zip")
    r = chk.check_cdg(bad)
    assert r["zip_ok"] is False and r["ok"] is False
```

- [ ] **Step 2: Run, verify fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability.py -k cdg -q`
Expected: FAIL (`check_cdg` undefined).

- [ ] **Step 3: Implement `check_cdg` (append to `PlayabilityChecker` in `playability.py`)**
```python
    def decode_file(self, path, timeout=120):
        """Decode any A/V file to null (used for cdg + audio sub-checks)."""
        rc, _out, err = self._run(
            ["ffmpeg", "-v", "error", "-xerror", "-i", path, "-f", "null", "-"],
            timeout=timeout,
        )
        return parse_decode(rc, err)

    def check_cdg(self, path):
        from zip_playback import ZipPlayback

        result = {
            "ok": False, "zip_ok": False, "has_cdg": False, "has_audio": False,
            "cdg_decodes": False, "audio_decodes": False, "error": None,
            "extracted_audio": None,
        }
        zp = ZipPlayback(self.config)
        mp3 = zp.extract_and_get_mp3(path)  # extracts into a temp dir, returns .mp3
        try:
            if mp3 is None:
                # Could be a bad zip OR a zip with no mp3. Distinguish by reopening.
                import zipfile
                try:
                    with zipfile.ZipFile(path) as zf:
                        names = zf.namelist()
                    result["zip_ok"] = True
                    result["error"] = "no .mp3 in CDG zip"
                except (zipfile.BadZipFile, OSError):
                    result["error"] = "not a valid zip"
                return result
            result["zip_ok"] = True
            result["has_audio"] = True
            extract_dir = os.path.dirname(mp3)
            cdgs = [f for f in os.listdir(extract_dir) if f.lower().endswith(".cdg")]
            result["has_cdg"] = bool(cdgs)
            if not cdgs:
                result["error"] = "no .cdg graphics in zip"
                return result
            cdg_path = os.path.join(extract_dir, cdgs[0])
            result["audio_decodes"] = self.decode_file(mp3)["ok"]
            result["cdg_decodes"] = self.decode_file(cdg_path)["ok"]
            result["extracted_audio"] = mp3
            result["ok"] = result["audio_decodes"] and result["cdg_decodes"]
            if not result["ok"]:
                bad = []
                if not result["audio_decodes"]:
                    bad.append("audio")
                if not result["cdg_decodes"]:
                    bad.append("cdg graphics")
                result["error"] = " and ".join(bad) + " failed to decode"
            # NOTE: caller (check()) runs the renderer frame-capture on extracted_audio
            # BEFORE this ZipPlayback instance is cleaned up.
            self._last_zip = zp  # keep extraction alive for the render step
        finally:
            # Cleanup deferred to check() via _cleanup_cdg(); see Task 7.
            pass
        return result

    def _cleanup_cdg(self):
        zp = getattr(self, "_last_zip", None)
        if zp is not None:
            zp.cleanup()
            self._last_zip = None
```

- [ ] **Step 4: Run, verify pass, commit**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability.py -k cdg -q` → Expected: PASS
```bash
git add kj-controller/playability.py kj-controller/tests/unit/test_playability.py
git commit -m "feat(playability): CDG zip sub-pipeline (zip+cdg+audio decode)"
```

---

## Task 7: Verdict + `check()` orchestration

**Files:**
- Modify: `kj-controller/playability.py`
- Test: `kj-controller/tests/unit/test_playability.py`

**Interfaces:**
- Consumes: all prior layers; `playability_render.render_check`.
- Produces: `compute_verdict(kind, result, renderers) -> dict` with keys `overall_ok, reasons, vlc_playable, mpv_playable`; `PlayabilityChecker.check(path, renderers=('vlc','mpv'), depth='deep', short_circuit=False) -> PlayabilityResult`.

- [ ] **Step 1: Write tests (append to `test_playability.py`)**
```python
def test_compute_verdict_video_all_pass():
    res = pl.PlayabilityResult(path="/x/a.mp4", kind="video")
    res.integrity = {"ok": True, "has_video": True}
    res.decode = {"ok": True}
    res.renderers = {"vlc": {"frame_nonblank": True}, "mpv": {"frame_nonblank": True}}
    v = pl.compute_verdict("video", res, ("vlc", "mpv"))
    assert v["overall_ok"] is True
    assert v["vlc_playable"] and v["mpv_playable"]
    assert v["reasons"] == []


def test_compute_verdict_video_mpv_only():
    res = pl.PlayabilityResult(path="/x/a.mp4", kind="video")
    res.integrity = {"ok": True, "has_video": True}
    res.decode = {"ok": True}
    res.renderers = {"vlc": {"frame_nonblank": False}, "mpv": {"frame_nonblank": True}}
    v = pl.compute_verdict("video", res, ("vlc", "mpv"))
    assert v["mpv_playable"] is True and v["vlc_playable"] is False
    assert v["overall_ok"] is False
    assert any("vlc" in r for r in v["reasons"])


def test_compute_verdict_truncated_no_render_needed():
    res = pl.PlayabilityResult(path="/x/a.mp4", kind="video")
    res.integrity = {"ok": False, "has_video": False, "error": "moov atom not found (truncated/incomplete file)"}
    v = pl.compute_verdict("video", res, ("vlc", "mpv"))
    assert v["overall_ok"] is False
    assert any("moov" in r for r in v["reasons"])


def test_check_video_short_circuits_on_bad_integrity(mocker):
    chk = pl.PlayabilityChecker(config={})
    mocker.patch.object(chk, "probe_integrity",
                        return_value={"ok": False, "has_video": False, "moov_ok": False,
                                      "error": "moov atom not found (truncated/incomplete file)", "duration": None})
    spy_decode = mocker.patch.object(chk, "decode_video")
    spy_render = mocker.patch("playability_render.render_check")
    res = chk.check("/x/a.mp4", short_circuit=True)
    assert res.verdict["overall_ok"] is False
    spy_decode.assert_not_called()
    spy_render.assert_not_called()


def test_check_video_runs_all_layers_in_batch_mode(mocker, tmp_path):
    f = tmp_path / "a.mp4"; f.write_bytes(b"x")
    chk = pl.PlayabilityChecker(config={})
    mocker.patch.object(chk, "probe_integrity",
                        return_value={"ok": True, "has_video": True, "duration": 100.0, "error": None})
    mocker.patch.object(chk, "decode_video", return_value={"ok": True, "decode_errors": 0, "error": None})
    mocker.patch("playability_render.render_check",
                 return_value={"frame_captured": True, "frame_nonblank": True, "frame_varies": True, "error": None, "elapsed_s": 1.0})
    res = chk.check(str(f), renderers=("vlc", "mpv"), short_circuit=False)
    assert res.verdict["overall_ok"] is True
    assert set(res.renderers) == {"vlc", "mpv"}
```

- [ ] **Step 2: Run, verify fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability.py -k "verdict or check_video" -q`
Expected: FAIL (`compute_verdict`/`check` undefined).

- [ ] **Step 3: Implement `compute_verdict` + `check` (append to `playability.py`)**
```python
def compute_verdict(kind, result, renderers):
    reasons = []
    if kind == "cdg_zip":
        base_ok = (result.cdg or {}).get("ok", False)
        if not base_ok:
            reasons.append((result.cdg or {}).get("error") or "CDG validation failed")
    elif kind == "audio":
        integ = result.integrity or {}
        base_ok = integ.get("ok", False) and integ.get("has_audio", False)
        if not base_ok:
            reasons.append(integ.get("error") or "no playable audio")
    else:  # video / unknown
        integ = result.integrity or {}
        dec = result.decode or {}
        base_ok = integ.get("ok", False) and integ.get("has_video", False) and dec.get("ok", True)
        if not integ.get("ok", False):
            reasons.append(integ.get("error") or "integrity check failed")
        elif not integ.get("has_video", False):
            reasons.append("no video stream")
        elif not dec.get("ok", True):
            reasons.append(dec.get("error") or "decode failed")

    per = {}
    render_needed = kind in ("video", "cdg_zip")
    for r in renderers:
        rr = result.renderers.get(r, {})
        if render_needed:
            playable = bool(base_ok and rr.get("frame_nonblank"))
            if base_ok and not rr.get("frame_nonblank"):
                reasons.append(f"{r}: {rr.get('error') or 'no video frame rendered'}")
        else:
            playable = bool(base_ok)
        per[f"{r}_playable"] = playable

    overall = base_ok and all(per[f"{r}_playable"] for r in renderers)
    return {"overall_ok": overall, "reasons": reasons, **per}


# Extend PlayabilityChecker with check():
def _checker_check(self, path, renderers=("vlc", "mpv"), depth="deep", short_circuit=False):
    import playability_render as render_mod

    t0 = time.monotonic()
    kind = classify_kind(path)
    res = PlayabilityResult(path=path, kind=kind, checked_at=time.time())
    try:
        st = os.stat(path)
        res.size, res.mtime = st.st_size, st.st_mtime
    except OSError:
        pass

    if kind == "cdg_zip":
        res.cdg = self.check_cdg(path)
        if res.cdg.get("ok") or not short_circuit:
            audio = res.cdg.get("extracted_audio")
            if audio:
                for r in renderers:
                    res.renderers[r] = render_mod.render_check(self._run, audio, r, duration=None)
                    if short_circuit and not res.renderers[r].get("frame_nonblank"):
                        break
        self._cleanup_cdg()
    elif kind == "audio":
        res.integrity = self.probe_integrity(path)
        if res.integrity.get("ok") or not short_circuit:
            res.decode = self.decode_file(path)
    else:  # video / unknown
        res.integrity = self.probe_integrity(path)
        dur = res.integrity.get("duration")
        if res.integrity.get("ok") or not short_circuit:
            res.decode = self.decode_video(
                path,
                start=render_mod.capture_start(dur) if depth == "quick" else None,
                length=5.0 if depth == "quick" else None,
            )
            if res.decode.get("ok") or not short_circuit:
                for r in renderers:
                    res.renderers[r] = render_mod.render_check(self._run, path, r, duration=dur)
                    if short_circuit and not res.renderers[r].get("frame_nonblank"):
                        break

    res.verdict = compute_verdict(kind, res, renderers)
    res.elapsed_s = round(time.monotonic() - t0, 3)
    return res


PlayabilityChecker.check = _checker_check
```

- [ ] **Step 4: Run full module, verify pass, commit**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability.py tests/unit/test_playability_render.py tests/unit/test_frame_analysis.py -q` → Expected: PASS
```bash
git add kj-controller/playability.py kj-controller/tests/unit/test_playability.py
git commit -m "feat(playability): verdict computation + layered check() orchestration"
```

---

## Task 8: Batch walker + skip-manifest + JSONL writer

**Files:**
- Create: `kj-controller/playability_batch.py`
- Test: `kj-controller/tests/unit/test_playability_batch.py`

**Interfaces:**
- Consumes: `classify_kind`, `PlayabilityResult`.
- Produces: `iter_media_files(roots, exts) -> Iterator[str]`; `load_manifest(jsonl_path) -> dict[str, dict]` (key `path` → `{mtime, size}`); `is_unchanged(path, manifest) -> bool`; `append_jsonl(jsonl_path, result_dict)`; `run_batch(checker, roots, jsonl_path, throttle=0.0, depth='deep', recheck_failed=False, limit=None, log=print) -> int` (returns count checked).

- [ ] **Step 1: Write tests**

Create `kj-controller/tests/unit/test_playability_batch.py`:
```python
"""Unit tests for the library batch runner (walker, manifest, JSONL)."""
import json
import os

import playability_batch as pb


def test_iter_media_files_filters_extensions(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.zip").write_bytes(b"x")
    (tmp_path / "c.txt").write_bytes(b"x")
    sub = tmp_path / "sub"; sub.mkdir()
    (sub / "d.mkv").write_bytes(b"x")
    found = sorted(os.path.basename(p) for p in pb.iter_media_files([str(tmp_path)], pb.DEFAULT_EXTS))
    assert found == ["a.mp4", "b.zip", "d.mkv"]


def test_manifest_skip_unchanged(tmp_path):
    f = tmp_path / "a.mp4"; f.write_bytes(b"hello")
    jsonl = tmp_path / "results.jsonl"
    pb.append_jsonl(str(jsonl), {"path": str(f), "size": 5, "mtime": os.stat(f).st_mtime})
    manifest = pb.load_manifest(str(jsonl))
    assert pb.is_unchanged(str(f), manifest) is True
    f.write_bytes(b"changed-size")
    manifest2 = pb.load_manifest(str(jsonl))
    assert pb.is_unchanged(str(f), manifest2) is False


def test_run_batch_streams_and_skips(tmp_path, mocker):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"x")
    jsonl = tmp_path / "out.jsonl"

    class FakeChecker:
        def __init__(self): self.calls = 0
        def check(self, path, **kw):
            self.calls += 1
            from playability import PlayabilityResult
            r = PlayabilityResult(path=path, kind="video",
                                  size=os.stat(path).st_size, mtime=os.stat(path).st_mtime)
            r.verdict = {"overall_ok": True}
            return r

    chk = FakeChecker()
    n1 = pb.run_batch(chk, [str(tmp_path)], str(jsonl), throttle=0.0, log=lambda *a: None)
    assert n1 == 2 and chk.calls == 2
    # Second run skips both unchanged files.
    n2 = pb.run_batch(chk, [str(tmp_path)], str(jsonl), throttle=0.0, log=lambda *a: None)
    assert n2 == 0 and chk.calls == 2
    lines = [json.loads(l) for l in open(jsonl)]
    assert len(lines) == 2
```

- [ ] **Step 2: Run, verify fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability_batch.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `playability_batch.py`**

Create `kj-controller/playability_batch.py`:
```python
"""Library-wide playability batch: walk roots, probe each file, stream results
to JSONL (resumable via mtime/size manifest), then aggregate a report."""
from __future__ import annotations

import json
import os
import time

DEFAULT_EXTS = {".mp4", ".mkv", ".avi", ".webm", ".mov", ".zip"}


def iter_media_files(roots, exts):
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in sorted(files):
                if os.path.splitext(name)[1].lower() in exts:
                    yield os.path.join(dirpath, name)


def append_jsonl(jsonl_path, result_dict):
    os.makedirs(os.path.dirname(os.path.abspath(jsonl_path)) or ".", exist_ok=True)
    with open(jsonl_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(result_dict) + "\n")


def load_manifest(jsonl_path):
    manifest = {}
    if not os.path.isfile(jsonl_path):
        return manifest
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("path"):
                manifest[d["path"]] = {
                    "mtime": d.get("mtime"), "size": d.get("size"),
                    "overall_ok": (d.get("verdict") or {}).get("overall_ok"),
                }
    return manifest


def is_unchanged(path, manifest):
    prev = manifest.get(path)
    if not prev:
        return False
    try:
        st = os.stat(path)
    except OSError:
        return False
    return prev.get("size") == st.st_size and prev.get("mtime") == st.st_mtime


def run_batch(checker, roots, jsonl_path, throttle=0.0, depth="deep",
              recheck_failed=False, limit=None, log=print):
    manifest = load_manifest(jsonl_path)
    checked = 0
    for path in iter_media_files(roots, DEFAULT_EXTS):
        if is_unchanged(path, manifest):
            if not (recheck_failed and manifest[path].get("overall_ok") is False):
                continue
        try:
            result = checker.check(path, depth=depth)
            append_jsonl(jsonl_path, result.to_dict())
            ok = result.verdict.get("overall_ok")
            log(f"[{'OK ' if ok else 'BAD'}] {path}")
        except Exception as exc:  # never let one file kill the batch
            append_jsonl(jsonl_path, {"path": path, "kind": "unknown",
                                      "verdict": {"overall_ok": False, "reasons": [f"checker crashed: {exc}"]}})
            log(f"[ERR] {path}: {exc}")
        checked += 1
        if limit and checked >= limit:
            break
        if throttle:
            time.sleep(throttle)
    return checked
```

- [ ] **Step 4: Run, verify pass, commit**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability_batch.py -q` → Expected: PASS
```bash
git add kj-controller/playability_batch.py kj-controller/tests/unit/test_playability_batch.py
git commit -m "feat(playability): batch walker, skip-manifest, JSONL streaming"
```

---

## Task 9: Batch aggregation (CSV matrix + Markdown report)

**Files:**
- Modify: `kj-controller/playability_batch.py`
- Test: `kj-controller/tests/unit/test_playability_batch.py`

**Interfaces:**
- Produces: `aggregate(jsonl_path) -> dict` with keys `total, ok, unplayable, mpv_not_vlc, vlc_not_mpv, cdg_problems` (each a list of paths except counts); `write_reports(jsonl_path, csv_path, md_path) -> dict` (returns the aggregate).

- [ ] **Step 1: Write tests (append to `test_playability_batch.py`)**
```python
def _row(path, vlc, mpv, kind="video", overall=None):
    return {
        "path": path, "kind": kind,
        "integrity": {"vcodec": "h264", "acodec": "aac"},
        "renderers": {"vlc": {"frame_nonblank": vlc}, "mpv": {"frame_nonblank": mpv}},
        "verdict": {"overall_ok": overall if overall is not None else (vlc and mpv),
                    "vlc_playable": vlc, "mpv_playable": mpv, "reasons": []},
    }


def test_aggregate_buckets(tmp_path):
    jsonl = tmp_path / "r.jsonl"
    for r in [_row("/a.mp4", True, True), _row("/b.mp4", False, True),
              _row("/c.mp4", True, False), _row("/d.mp4", False, False)]:
        pb.append_jsonl(str(jsonl), r)
    agg = pb.aggregate(str(jsonl))
    assert agg["total"] == 4
    assert "/a.mp4" in agg["ok"]
    assert "/b.mp4" in agg["mpv_not_vlc"]
    assert "/c.mp4" in agg["vlc_not_mpv"]
    assert "/d.mp4" in agg["unplayable"]


def test_write_reports_emits_files(tmp_path):
    jsonl = tmp_path / "r.jsonl"
    pb.append_jsonl(str(jsonl), _row("/a.mp4", True, False))
    csv_p, md_p = tmp_path / "out.csv", tmp_path / "out.md"
    agg = pb.write_reports(str(jsonl), str(csv_p), str(md_p))
    assert csv_p.exists() and md_p.exists()
    head = open(csv_p).readline()
    assert "path" in head and "vlc" in head and "mpv" in head
    assert "/a.mp4" in open(md_p).read()
    assert agg["total"] == 1
```

- [ ] **Step 2: Run, verify fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability_batch.py -k "aggregate or reports" -q`
Expected: FAIL (`aggregate` undefined).

- [ ] **Step 3: Implement aggregation (append to `playability_batch.py`)**
```python
import csv


def _read_results(jsonl_path):
    out = []
    if not os.path.isfile(jsonl_path):
        return out
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    # de-dup by path, keeping the last occurrence (re-checks supersede)
    by_path = {}
    for d in out:
        if d.get("path"):
            by_path[d["path"]] = d
    return list(by_path.values())


def aggregate(jsonl_path):
    rows = _read_results(jsonl_path)
    agg = {"total": len(rows), "ok": [], "unplayable": [],
           "mpv_not_vlc": [], "vlc_not_mpv": [], "cdg_problems": []}
    for d in rows:
        v = d.get("verdict", {})
        vlc, mpv = v.get("vlc_playable"), v.get("mpv_playable")
        p = d["path"]
        if v.get("overall_ok"):
            agg["ok"].append(p)
        if not vlc and not mpv:
            agg["unplayable"].append(p)
        elif mpv and not vlc:
            agg["mpv_not_vlc"].append(p)
        elif vlc and not mpv:
            agg["vlc_not_mpv"].append(p)
        if d.get("kind") == "cdg_zip" and not (d.get("cdg") or {}).get("ok", True):
            agg["cdg_problems"].append(p)
    return agg


def write_reports(jsonl_path, csv_path, md_path):
    rows = _read_results(jsonl_path)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "kind", "vlc", "mpv", "vcodec", "acodec", "reasons"])
        for d in rows:
            v = d.get("verdict", {})
            integ = d.get("integrity", {})
            w.writerow([
                d.get("path"), d.get("kind"),
                "OK" if v.get("vlc_playable") else "FAIL",
                "OK" if v.get("mpv_playable") else "FAIL",
                integ.get("vcodec", ""), integ.get("acodec", ""),
                "; ".join(v.get("reasons", [])),
            ])
    agg = aggregate(jsonl_path)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("# Playability Report\n\n")
        fh.write(f"- Total: {agg['total']}\n")
        fh.write(f"- Fully OK: {len(agg['ok'])}\n")
        fh.write(f"- Totally unplayable: {len(agg['unplayable'])}\n")
        fh.write(f"- Plays in mpv but NOT VLC: {len(agg['mpv_not_vlc'])}\n")
        fh.write(f"- Plays in VLC but NOT mpv: {len(agg['vlc_not_mpv'])}\n")
        fh.write(f"- CDG problems: {len(agg['cdg_problems'])}\n\n")
        for title, key in [("Totally unplayable", "unplayable"),
                           ("Plays in mpv but NOT VLC", "mpv_not_vlc"),
                           ("Plays in VLC but NOT mpv", "vlc_not_mpv"),
                           ("CDG problems", "cdg_problems")]:
            if agg[key]:
                fh.write(f"## {title}\n\n")
                for p in sorted(agg[key]):
                    fh.write(f"- {p}\n")
                fh.write("\n")
    return agg
```

- [ ] **Step 4: Run, verify pass, commit**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability_batch.py -q` → Expected: PASS
```bash
git add kj-controller/playability_batch.py kj-controller/tests/unit/test_playability_batch.py
git commit -m "feat(playability): batch aggregation (CSV matrix + Markdown report)"
```

---

## Task 10: Batch CLI entrypoint

**Files:**
- Modify: `kj-controller/playability_batch.py`
- Test: `kj-controller/tests/unit/test_playability_batch.py`

**Interfaces:**
- Produces: `build_arg_parser() -> argparse.ArgumentParser`; `main(argv=None) -> int`. Defaults: roots = box media folders + 4TB SSD; jsonl = `playability_results.jsonl` in cwd.

- [ ] **Step 1: Write test (append to `test_playability_batch.py`)**
```python
def test_arg_parser_defaults_and_overrides():
    p = pb.build_arg_parser()
    ns = p.parse_args([])
    assert ns.throttle >= 0.0
    assert ns.depth in ("deep", "quick")
    ns2 = p.parse_args(["--roots", "/x", "/y", "--throttle", "0.5", "--limit", "3", "--depth", "quick"])
    assert ns2.roots == ["/x", "/y"] and ns2.throttle == 0.5 and ns2.limit == 3 and ns2.depth == "quick"
```

- [ ] **Step 2: Run, verify fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability_batch.py -k arg_parser -q`
Expected: FAIL (`build_arg_parser` undefined).

- [ ] **Step 3: Implement CLI (append to `playability_batch.py`)**
```python
import argparse

DEFAULT_ROOTS = ["/opt/nomad/YTDownloads", "/opt/nomad/MP4-720p", "/media/nomad/Nomad4TBOne"]


def build_arg_parser():
    p = argparse.ArgumentParser(description="Check playability of the karaoke library.")
    p.add_argument("--roots", nargs="+", default=list(DEFAULT_ROOTS),
                   help="Directories to scan (default: box media folders + 4TB SSD).")
    p.add_argument("--jsonl", default="playability_results.jsonl",
                   help="Incremental results file (append-only, resumable).")
    p.add_argument("--csv", default="playability_report.csv")
    p.add_argument("--md", default="playability_report.md")
    p.add_argument("--throttle", type=float, default=0.2,
                   help="Seconds to sleep between files (SSD-friendly).")
    p.add_argument("--depth", choices=["deep", "quick"], default="deep")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--recheck-failed", action="store_true")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    from playability import PlayabilityChecker

    checker = PlayabilityChecker(config={})
    n = run_batch(checker, args.roots, args.jsonl, throttle=args.throttle,
                  depth=args.depth, recheck_failed=args.recheck_failed, limit=args.limit)
    agg = write_reports(args.jsonl, args.csv, args.md)
    print(f"Checked {n} new/changed files. Total {agg['total']}: "
          f"{len(agg['ok'])} OK, {len(agg['unplayable'])} unplayable, "
          f"{len(agg['mpv_not_vlc'])} mpv-only, {len(agg['vlc_not_mpv'])} vlc-only.")
    print(f"Reports: {args.csv}, {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run, verify pass, commit**

Run: `cd kj-controller && python -m pytest tests/unit/test_playability_batch.py -q` → Expected: PASS
```bash
git add kj-controller/playability_batch.py kj-controller/tests/unit/test_playability_batch.py
git commit -m "feat(playability): batch CLI entrypoint with SSD throttle"
```

---

## Task 11: Confidence run on the box (operational — REQUIRES USER PERMISSION)

**No code.** This task validates the checker against the real library before any gate is wired. Do NOT run on the box without explicit user go-ahead (production-safety rule). Run off-show.

- [ ] **Step 1: Install Xvfb + Pillow on the box** (ask first)

```bash
ssh nomadpctunnel 'sudo apt-get update && sudo apt-get install -y xvfb'
ssh nomadpctunnel '/opt/nomad/kjbox/kj-controller/venv/bin/pip install Pillow'
```

- [ ] **Step 2: Sanity-check against the known set**

Copy the new modules to the box's checkout (or `git pull` the branch into a scratch clone — NOT the live `/opt/nomad/kjbox` working tree). Run the checker against the 4 known-corrupt files and a handful of known-good ones:
```bash
# Known-bad (should report overall_ok=false, moov/no-frame):
#   /opt/nomad/YTDownloads/divebar__ESK - 3 Doors Down - Kryptonite.mp4
#   /opt/nomad/YTDownloads/divebar__FATBIRD - Vampire Weekend - Capricorn.mp4
#   /opt/nomad/YTDownloads/divebar__KFS - Los Fabulosos Cadillacs - Matador (Latino).mp4
#   /opt/nomad/YTDownloads/divebar__NOMAD - Juanes - Para Tu Amor.mp4
python -c "from playability import PlayabilityChecker, ...; print(...)"
```
Expected: all 4 bad files → `overall_ok=false`; the good samples → `overall_ok=true`. **Measure per-file `elapsed_s`** (this is the data that decides the link-time depth tier — the deferred decision).

- [ ] **Step 3: Full library run, off-show**

```bash
cd /tmp/kj-playability && nice -n 19 ionice -c3 \
  /opt/nomad/kjbox/kj-controller/venv/bin/python playability_batch.py \
  --throttle 0.3 --jsonl /tmp/kj-playability/results.jsonl
```
Resumable — re-run with the same `--jsonl` to continue after a stop.

- [ ] **Step 4: Analyze + tune**

Review `playability_report.md`. For every file flagged unplayable, manually confirm (play a sample) it really is broken. **Tune `BLANK_SPREAD_THRESHOLD` / decode strictness until there are zero false-positives** (good files flagged bad). Record the chosen link-time `depth` based on measured timings. Commit any threshold changes.

- [ ] **Step 5: Capture findings**

Write a short results summary into `docs/archive/2026-06-27-playability-checker-plan.md` (or a sibling notes file): counts per bucket, false-positive rate after tuning, chosen depth tier, and the mpv-vs-VLC matrix takeaways for the primary-renderer switch.

---

## Task 12: Hard gate on `/rotation/link`

**Files:**
- Modify: `kj-controller/routes.py` (≈line 2669, inside `link_rotation_file`)
- Test: `kj-controller/tests/unit/test_link_gate.py`

**Interfaces:**
- Consumes: `PlayabilityChecker.check`, active `render_mode` from config.
- Produces: link returns HTTP 422 with `{error, verdict}` when the file fails the active renderer; otherwise proceeds unchanged.

- [ ] **Step 1: Write the failing test**

Create `kj-controller/tests/unit/test_link_gate.py`:
```python
"""The /rotation/link route hard-blocks unplayable files."""
import json

import pytest


@pytest.fixture
def client(mocker):
    import app as app_module
    application = app_module.create_app()
    application.config["TESTING"] = True
    return application.test_client()


def _ok_verdict():
    return {"overall_ok": True, "vlc_playable": True, "mpv_playable": True, "reasons": []}


def _bad_verdict():
    return {"overall_ok": False, "vlc_playable": False, "mpv_playable": True,
            "reasons": ["vlc: no video frame rendered"]}


def test_link_blocked_when_active_renderer_fails(client, mocker):
    from playability import PlayabilityResult
    res = PlayabilityResult(path="/x/bad.mp4", kind="video")
    res.verdict = _bad_verdict()
    mocker.patch("routes._playability_check_for_link", return_value=res)
    r = client.post("/rotation/link", json={"id": 1, "file_path": "/x/bad.mp4"})
    assert r.status_code == 422
    body = r.get_json()
    assert "vlc" in (body.get("error") or "").lower()
    assert body["verdict"]["vlc_playable"] is False


def test_link_allowed_when_playable(client, mocker):
    from playability import PlayabilityResult
    res = PlayabilityResult(path="/x/good.mp4", kind="video")
    res.verdict = _ok_verdict()
    mocker.patch("routes._playability_check_for_link", return_value=res)
    mocker.patch("routes._resolve_or_create_rotation_entry_id", return_value=(1, None))
    link = mocker.patch.object(client.application.rotation, "link_file")
    mocker.patch.object(client.application.rotation, "get_rotation", return_value=[])
    r = client.post("/rotation/link", json={"id": 1, "file_path": "/x/good.mp4"})
    assert r.status_code == 200
    link.assert_called_once()
```

- [ ] **Step 2: Run, verify fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_link_gate.py -q`
Expected: FAIL (`_playability_check_for_link` undefined).

- [ ] **Step 3: Implement the gate in `routes.py`**

Add a helper near the top of `routes.py` (after imports):
```python
def _playability_check_for_link(file_path):
    """Probe a file against the ACTIVE renderer before linking. Returns a
    PlayabilityResult. Both renderers are recorded; only the active one gates."""
    from playability import PlayabilityChecker
    cfg = current_app.config_data if hasattr(current_app, "config_data") else {}
    active = (cfg.get("render_mode") or "vlc")
    checker = PlayabilityChecker(config=cfg)
    return checker.check(file_path, renderers=(active, "mpv" if active == "vlc" else "vlc"),
                         short_circuit=True)
```
Then in `link_rotation_file`, immediately **before** `rotation.link_file(entry_id, file_path)` (≈line 2669), insert:
```python
        cfg = current_app.config_data if hasattr(current_app, "config_data") else {}
        active = (cfg.get("render_mode") or "vlc")
        pr_result = _playability_check_for_link(file_path)
        active_ok = pr_result.verdict.get(f"{active}_playable", False)
        if not active_ok:
            return jsonify({
                "error": "File failed playability check: " + "; ".join(pr_result.verdict.get("reasons", [])),
                "verdict": pr_result.verdict,
            }), 422
```

> Confirm the exact config accessor used elsewhere in `routes.py` (search for how `render_mode` is read — e.g. `current_app.config_data` vs a module global) and match it. Replace `current_app.config_data` above if the codebase uses a different handle.

- [ ] **Step 4: Run, verify pass, commit**

Run: `cd kj-controller && python -m pytest tests/unit/test_link_gate.py -q` → Expected: PASS
```bash
git add kj-controller/routes.py kj-controller/tests/unit/test_link_gate.py
git commit -m "feat(playability): hard-gate /rotation/link on active-renderer playability"
```

---

## Task 13: Upload + download gates with index caching

**Files:**
- Modify: `kj-controller/routes.py` (`handle_upload`, ≈line 243)
- Modify: `kj-controller/media.py` (`download_video` ≈line 213, `download_from_url` ≈line 299)
- Test: `kj-controller/tests/unit/test_upload_download_gate.py`

**Interfaces:**
- Consumes: `PlayabilityChecker.check`.
- Produces: upload returns 422 + deletes file on fail; `download_video`/`download_from_url` raise/return a clear failure and delete the file on fail; passing results cached in the media index under `playability` keyed by `mtime+size`.

- [ ] **Step 1: Write the failing tests**

Create `kj-controller/tests/unit/test_upload_download_gate.py`:
```python
"""Upload + download gates: unplayable files are rejected and deleted."""
import os

import pytest


def test_download_video_deletes_unplayable(tmp_path, mocker):
    import media
    idx = media.MediaIndex(config={"download_folder": str(tmp_path), "media_folders": [str(tmp_path)],
                                   "media_index_path": str(tmp_path / "idx.json")})
    bad = tmp_path / "dl.mp4"; bad.write_bytes(b"truncated")
    mocker.patch.object(idx, "_run_ytdlp_download", return_value=(str(bad), {"duration": 10}))
    from playability import PlayabilityResult
    res = PlayabilityResult(path=str(bad), kind="video")
    res.verdict = {"overall_ok": False, "reasons": ["moov atom not found (truncated/incomplete file)"]}
    mocker.patch("media._playability_check", return_value=res)
    with pytest.raises(media.UnplayableDownloadError):
        idx.download_video("https://example/x")
    assert not bad.exists()  # unplayable file removed


def test_upload_rejects_unplayable(tmp_path, mocker):
    import app as app_module
    application = app_module.create_app()
    application.config["TESTING"] = True
    client = application.test_client()
    from playability import PlayabilityResult
    res = PlayabilityResult(path="/x/u.mp4", kind="video")
    res.verdict = {"overall_ok": False, "reasons": ["no video stream"]}
    mocker.patch("routes._playability_check_for_link", return_value=res)
    data = {"file": (io_bytes(), "u.mp4")}
    r = client.post("/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 422
    assert "video" in (r.get_json().get("error") or "").lower()


def io_bytes():
    import io
    return io.BytesIO(b"fake mp4 bytes")
```

> The exact name `_run_ytdlp_download` is illustrative — during implementation, factor the actual yt-dlp call in `download_video` into a small helper with that name so the test can mock it without hitting the network. If `download_video` already has a seam, mock that instead and update the test.

- [ ] **Step 2: Run, verify fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_upload_download_gate.py -q`
Expected: FAIL (`UnplayableDownloadError`/`_playability_check` undefined).

- [ ] **Step 3: Implement gates**

In `media.py`, add near the top:
```python
class UnplayableDownloadError(Exception):
    """Raised when a freshly downloaded file fails the playability check."""


def _playability_check(path, config):
    from playability import PlayabilityChecker
    return PlayabilityChecker(config=config).check(path, short_circuit=True)
```
At the end of `download_video` and `download_from_url`, after the file is on disk and before indexing:
```python
        result = _playability_check(file_path, self.config)
        if not result.verdict.get("overall_ok"):
            try:
                os.remove(file_path)
            except OSError:
                pass
            raise UnplayableDownloadError(
                "Downloaded file is not playable: " + "; ".join(result.verdict.get("reasons", []))
            )
        # cache the passing result in the index entry
        entry["playability"] = result.verdict
```
In `routes.py` `handle_upload`, after the file is saved to disk and before responding success, add:
```python
        pr_result = _playability_check_for_link(saved_path)  # reuse the link helper
        if not pr_result.verdict.get("overall_ok"):
            try:
                os.remove(saved_path)
            except OSError:
                pass
            return jsonify({
                "error": "Upload rejected — file is not playable: " + "; ".join(pr_result.verdict.get("reasons", [])),
                "verdict": pr_result.verdict,
            }), 422
```

> Match `saved_path` to the actual variable `handle_upload` uses for the written file. Callers of `download_video`/`download_from_url` (e.g. `gen_poller`, `/rotation/download-and-link`) must catch `UnplayableDownloadError` and surface the message — grep for call sites and wrap each in try/except returning a clear error.

- [ ] **Step 4: Run, verify pass, commit**

Run: `cd kj-controller && python -m pytest tests/unit/test_upload_download_gate.py -q` → Expected: PASS
```bash
git add kj-controller/media.py kj-controller/routes.py kj-controller/tests/unit/test_upload_download_gate.py
git commit -m "feat(playability): gate uploads + downloads, delete unplayable files, cache verdicts"
```

- [ ] **Step 5: Run the full suite**

Run: `cd kj-controller && python -m pytest -q`
Expected: PASS (existing suite + all new tests).

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Engine with per-layer evidence → Tasks 2,3,7. ✓
- Render proof (Xvfb + VLC + mpv, mid-file frames) → Tasks 1,4,5. ✓
- CDG support → Task 6. ✓
- Both renderers always → Tasks 5,7 (check passes both), batch matrix Task 9. ✓
- Show-safe isolation (`:99`, `--no-audio`/`--ao=null`, low priority) → Tasks 3,4,5 + Global Constraints. ✓
- Batch: resumable JSONL, mtime/size skip, throttle, aggregate report → Tasks 8,9,10. ✓
- Confidence-first rollout + tuning + measure timings → Task 11. ✓
- Hard-block link, no override → Task 12. ✓
- Upload/download reject + delete + cache → Task 13. ✓
- Deferred decisions (link depth tier, throttle value, thresholds) → resolved in Task 11. ✓

**Placeholder scan:** Two intentional "match the real variable/seam" notes (Tasks 12,13) flag where the implementer must align with existing `routes.py`/`media.py` internals not visible in this plan — these are verification instructions, not unfinished code. All code blocks are complete.

**Type consistency:** `PlayabilityResult` fields and `verdict` keys (`overall_ok`, `vlc_playable`, `mpv_playable`, `reasons`, `frame_nonblank`) are used consistently across Tasks 7–13. `_run(cmd, timeout) -> (rc, stdout, stderr)` signature consistent across render_check injection and checker methods.

## Notes for the implementer

- Run tests from inside `kj-controller/` (that's where `conftest.py` and the import roots live — modules import bare, e.g. `import playability`).
- Tasks 1–10 are pure local dev (no box). Task 11 is the only one needing the device and needs explicit permission. Tasks 12–13 are local code but DEPLOY only after Task 11 builds confidence.
- Nothing here pushes to `main` or restarts the service. Deployment is a separate, user-authorized step.
