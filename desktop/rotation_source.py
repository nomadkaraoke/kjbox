"""Pure data layer for the overlay renderer.

Reads the rotation snapshot that kj-controller writes to /tmp/rotation_cache.json
(see kj-controller/rotation.py::_write_display_cache) and exposes it as structured
data for the painters. Reproduces the display semantics that desktop/rotation_data.py
implemented for conky (status colours, pagination, ticker text) -- but returns data,
not conky markup. No rendering or GTK dependencies.
"""

import json
import os
import time
from dataclasses import dataclass, field

CACHE_FILE = "/tmp/rotation_cache.json"
CACHE_MAX_AGE = 120  # seconds; older than this is treated as offline
PAGE_SIZE = 10
PAGE_DURATION = 10  # seconds per page when cycling

# Status badge colours (hex with leading #). Mirrors rotation_data._status_color.
COLOR_NOW = "#2d8a4e"
COLOR_NEXT = "#d4720a"
COLOR_WIP = "#cc3333"
COLOR_HOLD = "#888888"
COLOR_SKIPPED = "#3b82f6"
COLOR_DEFAULT = "#8892a4"


@dataclass
class Entry:
    singer: str
    song_artist: str
    status: str
    paid: bool


@dataclass
class Snapshot:
    online: bool
    queue: list = field(default_factory=list)   # list[Entry]
    stats: dict = field(default_factory=dict)    # {started, singers, sung, queued}


def load_snapshot(path=CACHE_FILE, max_age=CACHE_MAX_AGE):
    """Load and parse the rotation cache. Returns a Snapshot; online=False when
    the file is missing, stale, or unparseable (callers render an offline state)."""
    try:
        mtime = os.path.getmtime(path)
        if time.time() - mtime > max_age:
            return Snapshot(online=False)
        with open(path) as f:
            data = json.load(f)
        queue = [
            Entry(
                singer=e.get("singer", ""),
                song_artist=e.get("song_artist", ""),
                status=e.get("status", ""),
                paid=bool(e.get("paid", False)),
            )
            for e in data.get("queue", [])
        ]
        return Snapshot(online=True, queue=queue, stats=data.get("stats", {}))
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return Snapshot(online=False)


def status_color(status):
    """Badge colour for a status string (mirrors rotation_data._status_color)."""
    s = (status or "").lower()
    if "singing" in s or s == "now singing":
        return COLOR_NOW
    if "next" in s or s == "waiting":
        return COLOR_NEXT
    if "being made" in s:
        return COLOR_WIP
    if "on hold" in s or "brb" in s:
        return COLOR_HOLD
    if s == "skipped":
        return COLOR_SKIPPED
    return COLOR_DEFAULT


def badge_text(status):
    """Return the badge label for a status, or None when no badge should show.
    Conky showed no badge for empty status or plain 'waiting'."""
    if not status:
        return None
    if status.lower() == "waiting":
        return None
    return status


def paginate(queue, now=None, page_size=PAGE_SIZE, page_duration=PAGE_DURATION):
    """Return (page_entries, start_index, page_num, total_pages) for the current
    time slice. Cycles pages every `page_duration` seconds when queue > page_size."""
    if now is None:
        now = time.time()
    total = len(queue)
    if total <= page_size:
        return queue, 0, 0, 1
    num_pages = (total + page_size - 1) // page_size
    page = int(now / page_duration) % num_pages
    start = page * page_size
    return queue[start:start + page_size], start, page, num_pages


def compose_ticker_text(entries, prefix, count, separator, empty_text):
    """Compose the 'Up next: 1. X  2. Y' ticker string.

    Moved (semantics preserved) from kj-controller/rotation_ticker_sync.compose_ticker_text.
    `entries` items must expose a `.singer` attribute (rotation_source.Entry) OR be
    dicts with a 'singer' key. The cache queue already excludes done/left."""
    if count <= 0:
        return f"{prefix}{empty_text}"
    slice_ = list(entries)[:count]
    if not slice_:
        return f"{prefix}{empty_text}"

    def name(e):
        return e.singer if hasattr(e, "singer") else e["singer"]

    slots = [f"{i}. {name(e)}" for i, e in enumerate(slice_, start=1)]
    return f"{prefix}{separator.join(slots)}"
