"""PlayabilityChecker: multi-layer playability probe for karaoke media.

Produces structured evidence (PlayabilityResult), never policy. Callers
(link / upload / download / batch) decide how to react to the verdict.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
        raw_dur = fmt.get("duration")
        if raw_dur is not None:
            try:
                duration = float(raw_dur)
            except (TypeError, ValueError):
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
        "ok": ok,
        "has_video": has_video,
        "has_audio": has_audio,
        "vcodec": vcodec,
        "acodec": acodec,
        "container": container,
        "duration": duration,
        "moov_ok": moov_ok,
        "error": error,
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


_DECODE_ERR_RE = re.compile(r"error while decoding", re.IGNORECASE)


def parse_decode(returncode: int, stderr: str) -> dict:
    stderr = stderr or ""
    matches = _DECODE_ERR_RE.findall(stderr)
    decode_errors = len(matches)
    ok = returncode == 0 and decode_errors == 0
    error = None
    if not ok:
        lines = [ln for ln in stderr.splitlines() if ln.strip()]
        error = lines[-1] if lines else f"ffmpeg exit {returncode}"
    return {"ok": ok, "decode_errors": decode_errors, "error": error}


class PlayabilityChecker:
    """Runs layered playability pipeline and returns PlayabilityResult."""

    def __init__(self, config=None, *, low_priority=True):
        self.config = config or {}
        self.low_priority = low_priority

    def _run(self, cmd, timeout):
        """Run subprocess at low priority; return (rc, stdout, stderr)."""
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
