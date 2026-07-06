"""Lightweight media technical-details probe (ffprobe wrapper).

Powers the "click the format pill → technical details" UI on both the
now-playing song and Library/Catalog rows. Kept deliberately small and split
into a pure parser (`parse_media_info`) plus a thin subprocess shell
(`probe_media_info`) so the normalization logic is unit-testable without ffprobe.

This is intentionally distinct from `playability.py`, which does a much heavier
decode/integrity verdict. Here we just want a human-readable spec sheet
(container, codecs, resolution, audio, bitrate, duration) as fast as possible.
"""
import json
import os
import shutil
import subprocess
import tempfile
import zipfile

# ffprobe is fast for a metadata-only probe, but a pathological file (or a slow
# external drive spinning up) shouldn't hang the request thread.
_FFPROBE_TIMEOUT_S = 20

# Audio members we recognize inside a CDG+MP3 karaoke zip (mirrors
# playability.CDG_AUDIO_EXTS).
_ZIP_AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".mp2"}


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # ffprobe emits "N/A" as a string (handled above) and occasionally NaN.
    return f if f == f else None


def _parse_fps(*rates):
    """Turn an ffprobe frame-rate string like "30000/1001" into a float.

    Tries each candidate (avg_frame_rate then r_frame_rate) and returns the
    first that yields a positive value; "0/0" and malformed values -> None.
    """
    for rate in rates:
        if not rate or not isinstance(rate, str) or "/" not in rate:
            continue
        num, _, den = rate.partition("/")
        n, d = _to_float(num), _to_float(den)
        if n and d:
            return n / d
    return None


def parse_media_info(data, *, size_bytes=None):
    """Normalize a parsed ffprobe JSON dict into a compact spec-sheet dict.

    Args:
        data: dict from ``ffprobe -print_format json -show_format -show_streams``.
        size_bytes: on-disk size (from os.stat) to prefer over ffprobe's, which
            can be absent for some containers.

    Returns a dict with keys: ok, container, format_long, duration, size_bytes,
    bit_rate, video, audio. ``video``/``audio`` are None when absent.
    """
    streams = (data or {}).get("streams") or []
    fmt = (data or {}).get("format") or {}

    video = audio = None
    for s in streams:
        kind = s.get("codec_type")
        if kind == "video" and video is None:
            video = {
                "codec": s.get("codec_name"),
                "profile": s.get("profile"),
                "width": _to_int(s.get("width")),
                "height": _to_int(s.get("height")),
                "pix_fmt": s.get("pix_fmt"),
                "fps": _parse_fps(s.get("avg_frame_rate"), s.get("r_frame_rate")),
                "bit_rate": _to_int(s.get("bit_rate")),
            }
        elif kind == "audio" and audio is None:
            audio = {
                "codec": s.get("codec_name"),
                "sample_rate": _to_int(s.get("sample_rate")),
                "channels": _to_int(s.get("channels")),
                "channel_layout": s.get("channel_layout"),
                "bit_rate": _to_int(s.get("bit_rate")),
            }

    resolved_size = size_bytes if size_bytes is not None else _to_int(fmt.get("size"))

    return {
        "ok": bool(video or audio or fmt.get("format_name")),
        "container": fmt.get("format_name"),
        "format_long": fmt.get("format_long_name"),
        "duration": _to_float(fmt.get("duration")),
        "size_bytes": resolved_size,
        "bit_rate": _to_int(fmt.get("bit_rate")),
        "video": video,
        "audio": audio,
    }


def _size_bytes(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _probe_cdg_zip(path):
    """Describe a CDG+MP3 karaoke ``.zip`` — ffprobe can't read a zip directly.

    Reports it as a CDG graphics + audio bundle: peeks the archive for the .cdg
    and audio members, extracts just the audio member to a temp file and ffprobes
    *that* for the audio codec/duration, and reports the zip's own size.
    """
    size = _size_bytes(path)
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            has_cdg = any(n.lower().endswith(".cdg") for n in names)
            audio_member = next(
                (n for n in names
                 if os.path.splitext(n)[1].lower() in _ZIP_AUDIO_EXTS), None)
            audio_probe = _probe_zip_member(zf, audio_member) if audio_member else None
    except (zipfile.BadZipFile, OSError) as exc:
        return {"ok": False, "error": f"could not read zip: {exc}"}

    audio_ext = os.path.splitext(audio_member)[1].lstrip(".").upper() if audio_member else None
    if has_cdg and audio_ext:
        container = f"CDG + {audio_ext} (zip)"
    elif has_cdg:
        container = "CDG (zip)"
    else:
        container = "ZIP archive"

    return {
        "ok": True,
        "container": container,
        "format_long": "CDG graphics + audio karaoke bundle" if has_cdg else "ZIP archive",
        "note": "CDG graphics track (no video codec)" if has_cdg else None,
        "duration": (audio_probe or {}).get("duration"),
        "size_bytes": size,
        "bit_rate": None,
        "video": None,
        "audio": (audio_probe or {}).get("audio"),
    }


def _probe_zip_member(zf, member):
    """Extract a single zip member to a temp file and ffprobe it. Returns the
    normalized probe dict, or None on any failure (best-effort)."""
    ext = os.path.splitext(member)[1].lower()
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=ext, prefix="kj-mediainfo-")
        with os.fdopen(fd, "wb") as dst, zf.open(member) as src:
            shutil.copyfileobj(src, dst)
        return _probe_with_ffprobe(tmp)
    except (KeyError, OSError, zipfile.BadZipFile):
        return None
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _probe_bare_cdg(path):
    """A standalone ``.cdg`` is graphics-only — ffprobe can't read it and it has
    no audio track. Report that plainly instead of an ffprobe error."""
    return {
        "ok": True,
        "container": "CDG (graphics only)",
        "format_long": "CD+Graphics — no audio track",
        "note": "Graphics-only .cdg — needs a matching audio file to play",
        "duration": None,
        "size_bytes": _size_bytes(path),
        "bit_rate": None,
        "video": None,
        "audio": None,
    }


def probe_media_info(path):
    """Return a normalized spec-sheet dict for ``path``.

    Dispatches on file type: CDG+MP3 zips and bare .cdg files are described
    directly (ffprobe can't read them); everything else goes through ffprobe.
    On any failure returns ``{"ok": False, "error": <message>}``.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".zip":
        return _probe_cdg_zip(path)
    if ext == ".cdg":
        return _probe_bare_cdg(path)
    return _probe_with_ffprobe(path)


def _probe_with_ffprobe(path):
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=_FFPROBE_TIMEOUT_S,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "ffprobe not installed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ffprobe timed out"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        return {"ok": False, "error": err[-1] if err else "ffprobe failed"}

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "could not parse ffprobe output"}

    try:
        size_bytes = os.path.getsize(path)
    except OSError:
        size_bytes = None

    info = parse_media_info(data, size_bytes=size_bytes)
    if not info["ok"]:
        info["error"] = "no media streams found"
    return info
