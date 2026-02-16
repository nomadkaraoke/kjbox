#!/usr/bin/env python3
"""Fetch karaoke rotation data and output conky-formatted text.

Called by conky via ${execi}. Fetches the singer rotation from a public
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
COLOR_NOW = "e63946"       # red — now singing
COLOR_NEXT = "f4a623"      # gold — up next
COLOR_DEFAULT = "8892a4"   # muted gray — queued
COLOR_TEXT = "e0e6f0"      # light gray body text
COLOR_WHITE = "ffffff"     # white for names/header


# ---------------------------------------------------------------------------
# Data fetching (reused from rotation_display.py)
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

def status_color(status):
    """Return hex color based on status text."""
    s = status.lower()
    if "next" in s:
        return COLOR_NEXT
    if "singing" in s or "now" in s:
        return COLOR_NOW
    return COLOR_DEFAULT


def format_conky(entries):
    """Format entries as conky markup text."""
    G = "${goto 70}"  # left margin for all content lines

    if not entries:
        print(f"{G}${{color {COLOR_DEFAULT}}}${{font DejaVu Sans:size=28}}No singers in queue${{font}}${{color}}")
        return

    # Current singer
    now = entries[0]
    status_text = (now["status"] if now["status"] else "Now Singing").upper()
    print(f"{G}${{color {COLOR_NOW}}}${{font DejaVu Sans:bold:size=28}}{status_text}${{font}}${{color}}")
    print(f"{G}${{color {COLOR_WHITE}}}${{font DejaVu Sans:bold:size=52}}{now['singer']}${{font}}${{color}}")
    if now["song_artist"]:
        print(f"{G}${{color {COLOR_TEXT}}}${{font DejaVu Sans:size=28}}{now['song_artist']}${{font}}${{color}}")

    # Queue
    for idx, entry in enumerate(entries[1:], start=2):
        color = status_color(entry["status"])
        # Status badge for non-generic statuses
        status_badge = ""
        if entry["status"] and entry["status"].lower() not in ("queued", "waiting", ""):
            status_badge = f"  ${{color {color}}}${{font DejaVu Sans:size=24}}{entry['status']}${{font}}${{color}}"

        print()
        print(f"{G}${{color {color}}}${{font DejaVu Sans:bold:size=32}}{idx}.${{font}} ${{font DejaVu Sans:bold:size=40}}{entry['singer']}${{font}}${{color}}{status_badge}")
        if entry["song_artist"]:
            print(f"{G}${{color {COLOR_TEXT}}}${{font DejaVu Sans:size=20}}{entry['song_artist']}${{font}}${{color}}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    count_only = "--count-only" in sys.argv

    try:
        entries = fetch_rotation()
    except (URLError, OSError, ValueError, csv.Error):
        print("0" if count_only else f"${{goto 70}}${{color {COLOR_DEFAULT}}}${{font DejaVu Sans:size=28}}Offline${{font}}${{color}}")
        return

    if count_only:
        print(len(entries))
    else:
        format_conky(entries)


if __name__ == "__main__":
    main()
