"""Add lyrics to a quick (draft) version that gen rendered without any.

gen's quick version (see GenPoller) looks lyrics up on LRCLIB by the job's exact
artist/title. When that misses — e.g. "My Body Isn't Ready (Orchestral Version
Live From Sound City)" — gen still renders the video, with a single
"(lyrics unavailable)" line, and the box records ``lyrics_tier = "none"``. The
singer then gets backing audio with nothing on screen.

This module lets the KJ fix that from the KJ UI in seconds:

* ``search_lrclib`` — a looser LRCLIB search (cleaned title, free-text query).
* ``parse_lyrics`` — pasted text, auto-detecting LRC ``[mm:ss.xx]`` timing.
* ``rerender_quick_video`` — re-renders ONLY the video track (the scrolling
  crawl, same look as gen's) and stream-copies the existing audio, so the
  instrumental + vocal-guide mix is untouched and no separation is needed. The
  file is replaced in place (same path, same media id), so the rotation entry,
  QUICK label and ``gen_quick_versions`` row stay linked.

The crawl layout/scroll code is fastgen's (``../fastgen/fastgen.py``, the POC
gen's quick-version renderer was ported from), loaded by path so there's one
copy of it on the box.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading

import requests

logger = logging.getLogger(__name__)

LRCLIB_BASE = "https://lrclib.net/api"
USER_AGENT = "nomadkaraoke-kjbox/1.0 (https://nomadkaraoke.com)"
SEARCH_TIMEOUT = 10
MAX_RESULTS = 15
MAX_LYRICS_CHARS = 20000

# Same look as gen's quick version (backend/services/quick_version/renderer.py).
HEIGHT = 480
FPS = 24
WRAP = 34
READING_FRAC = 0.42
GAP_THRESHOLD = 5.0
FFMPEG_TIMEOUT = 300

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",       # NomadPC / Pi
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",           # macOS dev
)

_FASTGEN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "fastgen", "fastgen.py")
_fastgen = None
_render_lock = threading.Lock()

# Trailing "(...)" / "[...]" groups and " - Live at ..." style suffixes that
# stop LRCLIB matching the studio lyrics.
_TRAILING_GROUP_RE = re.compile(r"\s*[\(\[][^\(\)\[\]]*[\)\]]\s*$")
_DASH_SUFFIX_RE = re.compile(
    r"\s+-\s+.*\b(live|version|remaster(ed)?|mix|edit|acoustic|orchestral|session|demo|mono|stereo)\b.*$",
    re.IGNORECASE)


class RenderBusy(RuntimeError):
    """Another re-render is already running on the box."""


def fastgen():
    """The fastgen module (loaded once, by path)."""
    global _fastgen
    if _fastgen is None:
        spec = importlib.util.spec_from_file_location("kjbox_fastgen", _FASTGEN_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod  # @dataclass looks its module up here
        spec.loader.exec_module(mod)
        _fastgen = mod
    return _fastgen


def clean_title(title):
    """Strip version/live/remaster decorations: 'Song (Live From X) [HD]' → 'Song'."""
    t = (title or "").strip()
    while True:
        stripped = _TRAILING_GROUP_RE.sub("", t)
        if stripped == t or not stripped.strip():
            break
        t = stripped
    t = _DASH_SUFFIX_RE.sub("", t)
    return t.strip() or (title or "").strip()


def artist_title_from_path(path):
    """('Sombr', "My Body Isn't Ready (...)") from 'Sombr - My Body ... [up-x].mp4'.

    Quick versions are imported as "<gen artist> - <gen title>", so the file
    name is the most reliable source (rotation entries hold one free-text song
    field)."""
    from naming import strip_media_id_token
    stem = os.path.splitext(strip_media_id_token(path))[0]
    stem = re.sub(r"\s*\(Quick Version\)$", "", stem, flags=re.IGNORECASE).strip()
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", stem


def default_query(artist, title):
    return " ".join(p for p in ((artist or "").strip(), clean_title(title)) if p)


def _hit(h):
    synced = (h.get("syncedLyrics") or "").strip()
    plain = (h.get("plainLyrics") or "").strip()
    if not synced and not plain:
        return None
    return {
        "id": h.get("id"),
        "artist": h.get("artistName") or "",
        "title": h.get("trackName") or "",
        "album": h.get("albumName") or "",
        "duration": h.get("duration"),
        "synced": bool(synced),
        "lyrics": synced or plain,
        "preview": " / ".join([ln for ln in (plain or fastgen()._strip_lrc_timestamps(synced)).splitlines()
                               if ln.strip()][:2]),
    }


def search_lrclib(query, session=None, timeout=SEARCH_TIMEOUT):
    """LRCLIB free-text search → result dicts (synced first, deduped, with lyrics only).

    Raises ``requests.RequestException`` if LRCLIB can't be reached."""
    query = (query or "").strip()
    if not query:
        return []
    s = session or requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    r = s.get(f"{LRCLIB_BASE}/search", params={"q": query}, timeout=timeout)
    r.raise_for_status()
    out, seen = [], set()
    for h in r.json() or []:
        hit = _hit(h) if isinstance(h, dict) else None
        if not hit:
            continue
        key = hit["id"] if hit["id"] is not None else (hit["artist"], hit["title"], hit["lyrics"][:200])
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    out.sort(key=lambda x: 0 if x["synced"] else 1)  # stable: keeps LRCLIB's ranking within each group
    return out[:MAX_RESULTS]


def parse_lyrics(text):
    """Pasted/selected lyrics → fastgen ``Lyrics``; LRC timestamps → "synced"."""
    fg = fastgen()
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        raise ValueError("Lyrics are empty")
    if len(text) > MAX_LYRICS_CHARS:
        raise ValueError("Lyrics are too long")
    if fg._LRC_RE.search(text):
        timed = fg._parse_synced(text)
        plain = fg._strip_lrc_timestamps(text)
        if any(t.strip() for _, t in timed):
            return fg.Lyrics("synced", plain, timed)
        text = plain
    if not text.strip():
        raise ValueError("Lyrics are empty")
    return fg.Lyrics("plain", text)


def has_timing(text):
    return bool(fastgen()._LRC_RE.search(text or ""))


def probe_duration(path):
    return fastgen().probe_duration(path)


def _font_path():
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise RuntimeError("No bold TrueType font found for the lyrics crawl")


def _ffmpeg_cmd(src, crawl_png, y_expr, out_path, width, duration):
    dur = f"{max(duration, 0.1):.3f}"
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{HEIGHT}:r={FPS}:d={dur}",
        "-loop", "1", "-framerate", str(FPS), "-t", dur, "-i", crawl_png,
        "-i", src,
        "-filter_complex", f"[0:v][1:v]overlay=x=(W-w)/2:y={y_expr}[v]",
        "-map", "[v]", "-map", "2:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-t", dur,
        out_path,
    ]


def rerender_quick_video(path, lyrics, artist, title, use_timing=True):
    """Re-render ``path`` in place with ``lyrics``; returns {lyrics_tier, line_count, duration}.

    Only one render runs at a time (raises ``RenderBusy``). The new file is
    written next to the old one and swapped in atomically, so a player that has
    the old file open keeps playing it undisturbed."""
    if not _render_lock.acquire(blocking=False):
        raise RenderBusy("Another lyrics re-render is already running")
    try:
        return _rerender_locked(path, lyrics, artist, title, use_timing)
    finally:
        _render_lock.release()


def _rerender_locked(path, lyrics, artist, title, use_timing):
    fg = fastgen()
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    duration = probe_duration(path)
    if not use_timing and lyrics.kind == "synced":
        lyrics = fg.Lyrics("plain", lyrics.plain)
    timed_lines, mode = fg.resolve_timed_lines(lyrics, False, None, "", None)
    tier = "synced" if mode == "synced" else "constant"
    if tier == "synced":
        timed_lines = fg.insert_instrumental_gaps(timed_lines, GAP_THRESHOLD, 1)
    line_count = sum(1 for t, _ in timed_lines if t.strip())
    if not line_count:
        raise ValueError("Lyrics are empty")

    width = round(HEIGHT * 16 / 9)
    width += width % 2
    fontsize = max(18, round(HEIGHT * 0.07))
    folder = os.path.dirname(path) or "."
    with tempfile.TemporaryDirectory(prefix=".quick_lyrics_") as workdir:
        lines = fg.build_visual_lines(artist or "", title or "", timed_lines, WRAP)
        crawl_png, img_h, centers = fg.render_crawl_png(lines, width, fontsize, _font_path(), workdir)
        y_expr, _ = fg.build_scroll_y_expr(lines, centers, duration, HEIGHT, img_h, READING_FRAC)
        fd, staging = tempfile.mkstemp(prefix=".quick_lyrics_", suffix=".mp4", dir=folder)
        os.close(fd)
        try:
            proc = subprocess.run(_ffmpeg_cmd(path, crawl_png, y_expr, staging, width, duration),
                                  capture_output=True, text=True, timeout=FFMPEG_TIMEOUT)
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-1500:]}")
            os.chmod(staging, os.stat(path).st_mode & 0o777)
            os.replace(staging, path)
        except BaseException:
            if os.path.exists(staging):
                os.remove(staging)
            raise
    logger.info("Quick version re-rendered with %s lyrics (%d lines): %s", tier, line_count, path)
    return {"lyrics_tier": tier, "line_count": line_count, "duration": round(duration, 1)}
