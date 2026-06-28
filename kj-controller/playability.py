"""PlayabilityChecker: multi-layer playability probe for karaoke media.

Produces structured evidence (PlayabilityResult), never policy. Callers
(link / upload / download / batch) decide how to react to the verdict.
"""
from __future__ import annotations

import contextlib
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
    timings: dict = field(default_factory=dict)

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
            "extracted_audio": None, "extracted_cdg": None, "duration": None,
        }
        zp = ZipPlayback(self.config)
        mp3 = zp.extract_and_get_mp3(path)  # extracts into a temp dir, returns .mp3
        if mp3 is None:
            # Could be a bad zip OR a zip with no mp3. Distinguish by reopening.
            import zipfile
            try:
                with zipfile.ZipFile(path) as zf:
                    zf.namelist()
                result["zip_ok"] = True
                result["error"] = "no .mp3 in CDG zip"
            except (zipfile.BadZipFile, OSError):
                result["error"] = "not a valid zip"
            return result
        # Extraction produced a temp dir; register it NOW so check()'s
        # _cleanup_cdg() removes it on EVERY post-extraction return path
        # (e.g. the no-.cdg early return below would otherwise leak it).
        self._last_zip = zp
        result["zip_ok"] = True
        result["has_audio"] = True
        extract_dir = os.path.dirname(mp3)
        cdgs = [f for f in os.listdir(extract_dir) if f.lower().endswith(".cdg")]
        result["has_cdg"] = bool(cdgs)
        if not cdgs:
            result["error"] = "no .cdg graphics in zip"
            return result
        cdg_path = os.path.join(extract_dir, cdgs[0])
        result["extracted_cdg"] = cdg_path
        result["audio_decodes"] = self.decode_file(mp3)["ok"]
        result["cdg_decodes"] = self.decode_file(cdg_path)["ok"]
        result["extracted_audio"] = mp3
        # CD+G builds its image incrementally from the START, and the first
        # seconds are black before any graphics are drawn. Record the audio
        # duration so the renderer captures mid-file (where graphics exist)
        # instead of the black intro — otherwise valid CDGs read as "no video".
        result["duration"] = self.probe_integrity(mp3).get("duration")
        result["ok"] = result["audio_decodes"] and result["cdg_decodes"]
        if not result["ok"]:
            bad = []
            if not result["audio_decodes"]:
                bad.append("audio")
            if not result["cdg_decodes"]:
                bad.append("cdg graphics")
            result["error"] = " and ".join(bad) + " failed to decode"
        # NOTE: caller (check()) runs the renderer frame-capture on the extracted
        # files BEFORE this ZipPlayback instance is cleaned up via _cleanup_cdg().
        return result

    def _cleanup_cdg(self):
        zp = getattr(self, "_last_zip", None)
        if zp is not None:
            zp.cleanup()
            self._last_zip = None

    def check(self, path, renderers=("vlc", "mpv"), depth="deep", short_circuit=False,
              display=None, frames_dir=None):
        import playability_render as render_mod

        t0 = time.monotonic()
        timings = {}

        def _timed(name, fn):
            s = time.monotonic()
            try:
                return fn()
            finally:
                timings[name] = round(time.monotonic() - s, 3)

        kind = classify_kind(path)
        res = PlayabilityResult(
            path=path,
            kind=kind,
            size=os.path.getsize(path) if os.path.exists(path) else 0,
            mtime=os.path.getmtime(path) if os.path.exists(path) else 0.0,
            checked_at=time.time(),
        )

        # VLC renders against an X display; mpv uses --vo=image (no display).
        # Only start an off-screen Xvfb if a VLC render will actually run here
        # AND the caller (e.g. the batch) hasn't already supplied a shared one.
        need_vlc = ("vlc" in renderers) and kind in ("video", "cdg_zip")
        with contextlib.ExitStack() as stack:
            if need_vlc and display is None:
                xs = time.monotonic()
                xvfb = stack.enter_context(render_mod.XvfbDisplay())
                display = xvfb.display
                timings["xvfb_start"] = round(time.monotonic() - xs, 3)

            if kind == "cdg_zip":
                res.cdg = _timed("cdg", lambda: self.check_cdg(path))
                audio = res.cdg.get("extracted_audio")
                extracted_cdg = res.cdg.get("extracted_cdg")
                cdg_dur = res.cdg.get("duration")
                if audio and (res.cdg.get("ok") or not short_circuit):
                    for r in renderers:
                        # VLC auto-discovers the sibling .cdg from the .mp3; mpv
                        # must be handed the .cdg directly or it renders no video.
                        src = extracted_cdg if r == "mpv" else audio
                        if src is None:
                            continue
                        # Pass the audio duration so capture seeks mid-file: a
                        # CD+G screen is black at the start (graphics accrue over
                        # the first seconds), so capturing start=0 reads as blank.
                        res.renderers[r] = render_mod.render_check(
                            self._run, src, r, duration=cdg_dur, display=display,
                            keep_dir=frames_dir)
                        timings[f"render_{r}"] = res.renderers[r].get("elapsed_s")
                        if short_circuit and not res.renderers[r].get("frame_nonblank"):
                            break
                self._cleanup_cdg()
            elif kind == "audio":
                res.integrity = _timed("integrity", lambda: self.probe_integrity(path))
            else:  # video / unknown
                res.integrity = _timed("integrity", lambda: self.probe_integrity(path))
                dur = res.integrity.get("duration")
                if res.integrity.get("ok") or not short_circuit:
                    res.decode = _timed("decode", lambda: self.decode_video(
                        path,
                        start=render_mod.capture_start(dur) if depth == "quick" else None,
                        length=5.0 if depth == "quick" else None,
                    ))
                    if res.decode.get("ok") or not short_circuit:
                        for r in renderers:
                            res.renderers[r] = render_mod.render_check(
                                self._run, path, r, duration=dur, display=display,
                                keep_dir=frames_dir)
                            timings[f"render_{r}"] = res.renderers[r].get("elapsed_s")
                            if short_circuit and not res.renderers[r].get("frame_nonblank"):
                                break

            res.verdict = compute_verdict(kind, res, renderers)
            res.elapsed_s = round(time.monotonic() - t0, 3)
            timings["total"] = res.elapsed_s
            res.timings = timings
        return res


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
    # mpv cannot render CD+G graphics; for CDG its result is recorded (for the
    # mpv-vs-VLC matrix) but does NOT gate overall_ok or add a failure reason.
    gating = [r for r in renderers if not (kind == "cdg_zip" and r == "mpv")]
    for r in renderers:
        rr = result.renderers.get(r, {})
        if render_needed:
            playable = bool(base_ok and rr.get("frame_nonblank"))
            if base_ok and not rr.get("frame_nonblank") and r in gating:
                reasons.append(f"{r}: {rr.get('error') or 'no video frame rendered'}")
        else:
            playable = bool(base_ok)
        per[r + "_playable"] = playable

    overall_ok = bool(
        base_ok and all(per.get(r + "_playable", False) for r in gating)
    ) if gating else bool(base_ok)
    return {"overall_ok": overall_ok, "reasons": reasons, **per}
