#!/usr/bin/env python3
"""Fetch karaoke rotation data and output conky-formatted text.

Called by conky via ${execpi}. Reads rotation data from a local JSON cache
written by kj-controller (instant updates), falling back to the Google Sheet
CSV endpoint if the cache is missing or stale.

Usage:
    python3 rotation_data.py              # Full conky-formatted rotation
    python3 rotation_data.py --count-only # Just the singer count (integer)
"""

import csv
import io
import json
import os
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SHEET_ID = "1OzNxqJB-pYHhI0VJkkPjJc1Ba242TL6Kadov52GHWl8"
SHEET_GID = "0"
SHEET_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    f"/gviz/tq?tqx=out:csv&gid={SHEET_GID}"
)

COL_SINGER = 1
COL_SONG_ARTIST = 2
COL_STATUS = 3

MAX_ENTRIES = 10
FETCH_TIMEOUT = 10

# Local cache written by kj-controller after every rotation change
CACHE_FILE = "/tmp/rotation_cache.json"
CACHE_MAX_AGE = 120  # seconds before falling back to sheet

# Colors (hex without #)
COLOR_NAME = "ffdf6b"      # gold for all singer names
COLOR_NOW_PILL = "2d8a4e"  # dark green for "Now" badge
COLOR_NEXT_PILL = "d4720a" # darker orange for "Next" badge
COLOR_WIP_PILL = "cc3333"  # red for "WIP" badge
COLOR_DEFAULT = "8892a4"   # muted gray — queued number
COLOR_TEXT = "e0e6f0"      # light gray body text

# Layout
MARGIN = "${goto 90}"       # left margin for all lines
SONG_INDENT = "${goto 115}" # song line indent

# Font shortcuts
FONT_NAME = "DejaVu Sans:bold:size=36"
FONT_SONG = "DejaVu Sans:size=20"
FONT_BADGE = "DejaVu Sans:bold:size=18"


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def read_local_cache():
    """Read rotation data from the local JSON cache file.

    Returns (queue, stats) or None if cache is missing/stale.
    """
    try:
        mtime = os.path.getmtime(CACHE_FILE)
        if time.time() - mtime > CACHE_MAX_AGE:
            return None
        with open(CACHE_FILE) as f:
            data = json.load(f)
        return data["queue"][:MAX_ENTRIES], data["stats"]
    except (OSError, KeyError, json.JSONDecodeError):
        return None


def fetch_from_sheet():
    """Fetch all rows from the Google Sheet CSV. Returns (queue, stats)."""
    with urlopen(SHEET_CSV_URL, timeout=FETCH_TIMEOUT) as response:
        text = response.read().decode("utf-8")
    reader = csv.reader(io.StringIO(text))

    try:
        next(reader)  # skip header
    except StopIteration:
        return [], {}

    all_singers = set()
    done_count = 0
    queue = []
    earliest_ts = None

    for row in reader:
        if len(row) <= COL_STATUS:
            continue
        status = row[COL_STATUS].strip()
        singer = row[COL_SINGER].strip()
        if not singer:
            continue

        # Track earliest timestamp (column 0, format "M/D/YYYY HH:MM:SS")
        ts_raw = row[0].strip() if row[0].strip() else None
        if ts_raw and earliest_ts is None:
            earliest_ts = ts_raw

        all_singers.add(singer)

        if status.lower() == "done":
            done_count += 1
        else:
            if len(queue) < MAX_ENTRIES:
                queue.append({
                    "singer": singer,
                    "song_artist": row[COL_SONG_ARTIST].strip() if COL_SONG_ARTIST < len(row) else "",
                    "status": status,
                })

    # Format earliest timestamp as M/D HH:MM
    started = ""
    if earliest_ts:
        try:
            from datetime import datetime
            dt = datetime.strptime(earliest_ts, "%m/%d/%Y %H:%M:%S")
            started = dt.strftime("%-m/%-d %-H:%M")
        except ValueError:
            started = earliest_ts

    stats = {
        "singers": len(all_singers),
        "sung": done_count,
        "queued": len(queue),
        "started": started,
    }
    return queue, stats


def fetch_all_rows():
    """Get rotation data, preferring local cache over sheet fetch."""
    cached = read_local_cache()
    if cached is not None:
        return cached
    return fetch_from_sheet()


# ---------------------------------------------------------------------------
# Conky output formatting
# ---------------------------------------------------------------------------

def badge(text, bg_color):
    """Render a colored badge with the exact status text."""
    return f"  ${{color {bg_color}}}${{font {FONT_BADGE}}} {text} ${{font}}${{color}}"


def _status_color(status_lower):
    """Return badge color for a status string."""
    if "singing" in status_lower or status_lower == "now singing":
        return COLOR_NOW_PILL
    if "next" in status_lower:
        return COLOR_NEXT_PILL
    if status_lower == "waiting":
        return COLOR_NEXT_PILL
    if "being made" in status_lower:
        return COLOR_WIP_PILL
    if "on hold" in status_lower or "brb" in status_lower:
        return "888888"
    if status_lower == "skipped":
        return "3b82f6"
    return COLOR_DEFAULT


def format_conky(entries):
    """Format entries as conky markup text."""
    if not entries:
        print(f"{MARGIN}${{color {COLOR_DEFAULT}}}${{font DejaVu Sans:size=28}}No singers in queue${{font}}${{color}}")
        return

    for idx, entry in enumerate(entries, start=1):
        status = entry["status"]
        status_lower = status.lower()

        # Show exact status text with color coding
        if status and status_lower != "waiting":
            entry_badge = badge(status, _status_color(status_lower))
        else:
            entry_badge = ""

        # Singer line: single font block so number and name share baseline
        print(f"{MARGIN}${{font {FONT_NAME}}}${{color ffffff}}{idx}. ${{color}}"
              f"${{color {COLOR_NAME}}}{entry['singer']}${{color}}${{font}}{entry_badge}")

        # Song line
        if entry["song_artist"]:
            print(f"{SONG_INDENT}${{color {COLOR_TEXT}}}${{font {FONT_SONG}}}{entry['song_artist']}${{font}}${{color}}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    stats_only = "--stats" in sys.argv

    try:
        queue, stats = fetch_all_rows()
    except (URLError, OSError, ValueError, csv.Error):
        print("--" if stats_only else f"{MARGIN}${{color {COLOR_DEFAULT}}}${{font DejaVu Sans:size=28}}Offline${{font}}${{color}}")
        return

    if stats_only:
        parts = []
        if stats.get("started"):
            parts.append(f"Started: {stats['started']}")
        parts.append(f"{stats['singers']} singers | {stats['sung']} sung | {stats['queued']} queued")
        print("    ".join(parts))
    else:
        format_conky(queue)


if __name__ == "__main__":
    main()
