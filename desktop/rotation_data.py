#!/usr/bin/env python3
"""Fetch karaoke rotation data and output conky-formatted text.

Called by conky via ${execpi}. Reads rotation data from the local JSON cache
written by kj-controller after every rotation change.

Usage:
    python3 rotation_data.py              # Full conky-formatted rotation
    python3 rotation_data.py --count-only # Just the singer count (integer)
"""

import json
import os
import sys
import time

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COL_SINGER = 1
COL_SONG_ARTIST = 2
COL_STATUS = 3

MAX_ENTRIES = 10

# Local cache written by kj-controller after every rotation change
CACHE_FILE = "/tmp/rotation_cache.json"
CACHE_MAX_AGE = 120  # seconds; treat as offline if cache is older than this

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

    cached = read_local_cache()
    if cached is None:
        print("--" if stats_only else f"{MARGIN}${{color {COLOR_DEFAULT}}}${{font DejaVu Sans:size=28}}Offline${{font}}${{color}}")
        return

    queue, stats = cached

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
