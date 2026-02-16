#!/usr/bin/env python3
"""Fetch karaoke rotation data and output conky-formatted text.

Called by conky via ${execpi}. Fetches the singer rotation from a public
Google Sheet CSV endpoint, filters out completed entries, and prints
conky markup to stdout.

Usage:
    python3 rotation_data.py              # Full conky-formatted rotation
    python3 rotation_data.py --count-only # Just the singer count (integer)
"""

import csv
import io
import sys
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

def fetch_all_rows():
    """Fetch all rows from the Google Sheet CSV. Returns (all_rows, queue, stats)."""
    with urlopen(SHEET_CSV_URL, timeout=FETCH_TIMEOUT) as response:
        text = response.read().decode("utf-8")
    reader = csv.reader(io.StringIO(text))

    try:
        next(reader)  # skip header
    except StopIteration:
        return [], {}, {}

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


# ---------------------------------------------------------------------------
# Conky output formatting
# ---------------------------------------------------------------------------

def badge(text, bg_color):
    """Render a colored badge."""
    return f"  ${{color {bg_color}}}${{font {FONT_BADGE}}} {text} ${{font}}${{color}}"


def format_conky(entries):
    """Format entries as conky markup text."""
    if not entries:
        print(f"{MARGIN}${{color {COLOR_DEFAULT}}}${{font DejaVu Sans:size=28}}No singers in queue${{font}}${{color}}")
        return

    for idx, entry in enumerate(entries, start=1):
        status_lower = entry["status"].lower()

        # Determine badge
        if idx == 1 or "singing" in status_lower or "now" in status_lower:
            entry_badge = badge("NOW", COLOR_NOW_PILL)
        elif "next" in status_lower:
            entry_badge = badge("NEXT", COLOR_NEXT_PILL)
        elif "being made" in status_lower or "wip" in status_lower:
            entry_badge = badge("WIP", COLOR_WIP_PILL)
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
        if stats["started"]:
            parts.append(f"Started: {stats['started']}")
        parts.append(f"{stats['singers']} singers | {stats['sung']} sung | {stats['queued']} queued")
        print("    ".join(parts))
    else:
        format_conky(queue)


if __name__ == "__main__":
    main()
