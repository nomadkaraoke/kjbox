"""Find a night's phone clips in Dropbox Camera Uploads, download, probe and thumbnail them.

Camera Uploads filenames are the phone's local (America/New_York) recording start
time, e.g. ``2026-10-08 22.13.56.mp4`` (``-1``, ``-2``... when two share a second).
kjbox ``started_at``/``sung_at`` are the same local wall-clock time, so no timezone maths is needed.

The folder has ~100k files and almost all are Dropbox online-only placeholders
(0 bytes until downloaded), so we only ``scandir`` names (never ``stat`` every
file) and only materialise the night's window.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

CAMERA_UPLOADS = Path.home() / "AB Dropbox" / "Andrew Beveridge" / "Camera Uploads"
WINDOW_PAD = dt.timedelta(minutes=10)
THUMB_AT_S = 3.0

FILENAME_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2})(?:-(?P<n>\d+))?\.(?P<ext>mp4|mov)$",
    re.IGNORECASE,
)
TS_FMT = "%Y-%m-%d %H.%M.%S"

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass
class Clip:
    id: str
    filename: str
    path: str
    start: dt.datetime
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    local: bool = False  # materialised (real bytes on disk)
    thumb: str | None = None
    error: str | None = None
    extra: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.strftime("%Y-%m-%d %H:%M:%S")
        return d


def parse_clip_time(filename: str) -> dt.datetime | None:
    m = FILENAME_RE.match(filename)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group("ts"), TS_FMT)
    except ValueError:
        return None


def clip_id(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    return re.sub(r"[^0-9A-Za-z.-]+", "_", stem) + "-" + filename.rsplit(".", 1)[-1].lower()


def parse_ts(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def night_window(songs: list[dict], pad: dt.timedelta = WINDOW_PAD) -> tuple[dt.datetime, dt.datetime] | None:
    times = sorted(parse_ts(s[k]) for s in songs for k in ("started_at", "sung_at") if s.get(k))
    if not times:
        return None
    return times[0] - pad, times[-1] + pad


def scan_clips(directory: Path, start: dt.datetime, end: dt.datetime) -> list[Clip]:
    """Clips whose filename timestamp is in [start, end], sorted by start time."""
    days = set()
    d = start.date()
    while d <= end.date():
        days.add(d.isoformat())
        d += dt.timedelta(days=1)
    clips = []
    with os.scandir(directory) as it:
        for entry in it:
            name = entry.name
            if name[:10] not in days:  # cheap prefix filter before the regex
                continue
            t = parse_clip_time(name)
            if t is None or not (start <= t <= end):
                continue
            clips.append(Clip(id=clip_id(name), filename=name, path=entry.path, start=t))
    clips.sort(key=lambda c: (c.start, c.filename))
    return clips


def is_local(path: str) -> bool:
    try:
        return os.stat(path).st_size > 0
    except OSError:
        return False


def materialize(paths: list[str], runner: Runner = subprocess.run, jobs: int = 8) -> dict[str, str]:
    """Download Dropbox placeholders. Returns {path: reason} for files that are still not local."""
    pending = [p for p in paths if not is_local(p)]
    failures: dict[str, str] = {}
    if pending:
        exe = shutil.which("dbx-materialize") or "dbx-materialize"
        try:
            proc = runner([exe, "-j", str(jobs), "-q", *pending], capture_output=True, text=True)
            output = (proc.stdout or "") + (proc.stderr or "")
        except OSError as e:
            output = f"FAIL dbx-materialize not runnable: {e}"
        for line in output.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0] in ("FAIL", "FLOOR"):
                for p in pending:
                    if p in parts[1] or os.path.basename(p) in parts[1]:
                        failures[p] = line.strip()
    for p in paths:
        if p not in failures and not is_local(p):
            failures[p] = "still online-only after dbx-materialize"
    return failures


def parse_probe(data: dict) -> dict:
    """Pull duration + display dimensions (rotation-aware) out of ffprobe JSON."""
    out: dict = {"duration": None, "width": None, "height": None}
    fmt = data.get("format") or {}
    try:
        out["duration"] = round(float(fmt.get("duration")), 2)
    except (TypeError, ValueError):
        pass
    for s in data.get("streams") or []:
        if s.get("codec_type") != "video":
            continue
        w, h = s.get("width"), s.get("height")
        rot = 0
        for sd in s.get("side_data_list") or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        if not rot and (s.get("tags") or {}).get("rotate"):
            rot = int(s["tags"]["rotate"])
        if rot % 180:
            w, h = h, w
        out["width"], out["height"] = w, h
        if out["duration"] is None and s.get("duration"):
            out["duration"] = round(float(s["duration"]), 2)
        break
    return out


def probe(path: str, runner: Runner = subprocess.run) -> dict:
    proc = runner(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {(proc.stderr or '').strip()[:200]}")
    return parse_probe(json.loads(proc.stdout or "{}"))


def thumbnail(path: str, out: Path, duration: float | None, runner: Runner = subprocess.run) -> Path:
    if out.exists() and out.stat().st_size > 0:
        return out
    at = THUMB_AT_S if not duration or duration > THUMB_AT_S * 2 else max(duration / 2, 0)
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = runner(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", path,
         "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", str(out)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg thumbnail failed: {(proc.stderr or '').strip()[:200]}")
    return out


def prepare(clips: list[Clip], work_dir: Path, workers: int = 4) -> None:
    """Probe + thumbnail every local clip in place (errors recorded on the clip)."""
    thumbs = work_dir / "thumbs"

    def one(c: Clip) -> None:
        c.local = is_local(c.path)
        if not c.local:
            c.error = c.error or "not downloaded (online-only)"
            return
        try:
            info = probe(c.path)
            c.duration, c.width, c.height = info["duration"], info["width"], info["height"]
            c.thumb = str(thumbnail(c.path, thumbs / f"{c.id}.jpg", c.duration))
        except Exception as e:  # noqa: BLE001 — record and carry on with the rest
            c.error = str(e)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, clips))
