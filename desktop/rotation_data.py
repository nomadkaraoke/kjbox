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

PAGE_SIZE = 10
PAGE_DURATION = 10  # seconds per page when cycling

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
COLOR_PAID_HEART = "e74c3c"  # red for paid heart

# Layout
MARGIN = "${goto 90}"       # left margin for all lines
SONG_INDENT = "${goto 115}" # song line indent

# Font shortcuts
FONT_NAME = "DejaVu Sans:bold:size=36"
FONT_SONG = "DejaVu Sans:size=20"
FONT_BADGE = "DejaVu Sans:bold:size=18"

# Rules panel
RULES_FILE = "/opt/nomad/kjbox/desktop/rotation_rules.txt"
RULES_MARGIN = "${goto 1020}"  # right column start
FONT_RULES_HEADER = "DejaVu Sans:bold:size=28"
FONT_RULES_BODY = "DejaVu Sans:size=18"
COLOR_RULES_HEADER = "ffffff"
COLOR_RULES_BODY = "8892a4"


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
        return data["queue"], data["stats"]
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


def paginate(queue):
    """Return (page_entries, start_index, page_num, total_pages) for the current time slice."""
    total = len(queue)
    if total <= PAGE_SIZE:
        return queue, 0, 0, 1
    num_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = int(time.time() / PAGE_DURATION) % num_pages
    start = page * PAGE_SIZE
    return queue[start:start + PAGE_SIZE], start, page, num_pages


def format_conky(entries, start_index=0, page_info=None):
    """Format entries as conky markup text.

    Args:
        entries: List of rotation entries to display.
        start_index: Queue position offset (0-based) for numbering.
        page_info: Tuple of (page_num, total_pages) if paginated, else None.
    """
    if not entries:
        print(f"{MARGIN}${{color {COLOR_DEFAULT}}}${{font DejaVu Sans:size=28}}No singers in queue${{font}}${{color}}")
        return

    for idx, entry in enumerate(entries, start=start_index + 1):
        status = entry["status"]
        status_lower = status.lower()

        # Show exact status text with color coding
        if status and status_lower != "waiting":
            entry_badge = badge(status, _status_color(status_lower))
        else:
            entry_badge = ""

        # Paid heart indicator
        paid_mark = f" ${{color {COLOR_PAID_HEART}}}♥${{color}}" if entry.get("paid") else ""

        # Singer line: single font block so number and name share baseline
        print(f"{MARGIN}${{font {FONT_NAME}}}${{color ffffff}}{idx}. ${{color}}"
              f"${{color {COLOR_NAME}}}{entry['singer']}${{color}}{paid_mark}${{font}}{entry_badge}")

        # Song line
        if entry["song_artist"]:
            print(f"{SONG_INDENT}${{color {COLOR_TEXT}}}${{font {FONT_SONG}}}{entry['song_artist']}${{font}}${{color}}")

    # Page indicator when cycling
    if page_info and page_info[1] > 1:
        page_num, total_pages = page_info
        dots = "  ".join(
            f"${{color ffffff}}●${{color}}" if i == page_num else f"${{color {COLOR_DEFAULT}}}●${{color}}"
            for i in range(total_pages)
        )
        print(f"\n{MARGIN}${{font DejaVu Sans:size=16}}{dots}${{font}}")


def format_rules():
    """Output conky markup for the rules panel on the right side of the screen."""
    try:
        with open(RULES_FILE) as f:
            lines = [line.strip() for line in f if line.strip()]
    except OSError:
        return

    # Header
    print(f"{RULES_MARGIN}${{voffset -30}}${{font {FONT_RULES_HEADER}}}${{color {COLOR_RULES_HEADER}}}HOW IT WORKS${{color}}${{font}}")
    print()

    # Bullet points
    for line in lines:
        print(f"{RULES_MARGIN}${{font {FONT_RULES_BODY}}}${{color {COLOR_RULES_BODY}}}• {line}${{color}}${{font}}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    stats_only = "--stats" in sys.argv
    rules_only = "--rules" in sys.argv

    if rules_only:
        format_rules()
        return

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
        page_entries, start_idx, page_num, total_pages = paginate(queue)
        page_info = (page_num, total_pages) if total_pages > 1 else None
        format_conky(page_entries, start_index=start_idx, page_info=page_info)


if __name__ == "__main__":
    main()
