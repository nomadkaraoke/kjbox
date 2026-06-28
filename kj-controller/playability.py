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
