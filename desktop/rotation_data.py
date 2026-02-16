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

def fetch_rotation():
    """Fetch rotation from Google Sheet CSV. Returns list of dicts."""
    with urlopen(SHEET_CSV_URL, timeout=FETCH_TIMEOUT) as response:
        text = response.read().decode("utf-8")
    reader = csv.reader(io.StringIO(text))

    try:
        next(reader)  # skip header
    except StopIteration:
        return []

    entries = []
    for row in reader:
        if len(row) <= COL_STATUS:
            continue
        status = row[COL_STATUS].strip()
        singer = row[COL_SINGER].strip()
        if status.lower() == "done" or not singer:
            continue
        entries.append({
            "singer": singer,
            "song_artist": row[COL_SONG_ARTIST].strip() if COL_SONG_ARTIST < len(row) else "",
            "status": status,
        })
        if len(entries) >= MAX_ENTRIES:
            break

    return entries


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
            entry_badge = badge("Now", COLOR_NOW_PILL)
        elif "next" in status_lower:
            entry_badge = badge("Next", COLOR_NEXT_PILL)
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
    count_only = "--count-only" in sys.argv

    try:
        entries = fetch_rotation()
    except (URLError, OSError, ValueError, csv.Error):
        print("0" if count_only else f"{MARGIN}${{color {COLOR_DEFAULT}}}${{font DejaVu Sans:size=28}}Offline${{font}}${{color}}")
        return

    if count_only:
        print(len(entries))
    else:
        format_conky(entries)


if __name__ == "__main__":
    main()
