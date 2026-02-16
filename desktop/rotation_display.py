#!/usr/bin/env python3
"""Karaoke rotation display overlay.

Fetches the singer rotation from a public Google Sheet and displays the next 10
singers as a persistent overlay on the left third of the screen. Designed for
venue visibility during live karaoke events.

Requires: python3-tk (apt-get install -y python3-tk)
For transparency: xcompmgr (apt-get install -y xcompmgr)
No pip dependencies — stdlib only.
"""

import csv
import io
import threading
import tkinter as tk
from datetime import datetime
from urllib.error import URLError
from urllib.request import urlopen

# ---------------------------------------------------------------------------
# Configuration — edit these values and restart the service to apply
#   ssh nomadpi 'nano /opt/nomad/kjbox/desktop/rotation_display.py'
#   ssh nomadpi 'systemctl restart rotation-display'
# ---------------------------------------------------------------------------

# Google Sheet published URL — replace SHEET_ID with your sheet's ID.
# The sheet must be published to the web (File > Share > Publish to web).
SHEET_ID = "1OzNxqJB-pYHhI0VJkkPjJc1Ba242TL6Kadov52GHWl8"
SHEET_GID = "0"  # first tab
SHEET_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    f"/gviz/tq?tqx=out:csv&gid={SHEET_GID}"
)

# Expected column layout (0-indexed). Adjust if your sheet differs.
# Columns: Timestamp | Singer | Song & Artist | Status | Round | Link Notes | URL
COL_SINGER = 1       # Singer name
COL_SONG_ARTIST = 2  # Combined "Artist - Song" field
COL_STATUS = 3       # Status text (e.g. "Done", "Now Singing", "Up Next", "Waiting")

# Screen dimensions (used to calculate window size with margins)
SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080

# Margins — space between window and screen edges (pixels)
MARGIN_TOP = 100
MARGIN_BOTTOM = 100
MARGIN_LEFT = 100

# Window width (independent of margins)
WINDOW_WIDTH = 640

# Background opacity (0.0 = fully transparent, 1.0 = fully opaque)
# Requires a compositor (xcompmgr) for transparency to work.
BG_OPACITY = 0.5

# How many queue entries to show (including the current singer)
MAX_ENTRIES = 10

# Refresh interval in milliseconds
REFRESH_MS = 30_000

# HTTP fetch timeout in seconds
FETCH_TIMEOUT = 10

# Colors
BG_COLOR = "#0a1628"           # dark navy background
TEXT_COLOR = "#e0e6f0"         # light gray text
HEADER_COLOR = "#ffffff"       # white header
ACCENT_NOW = "#e63946"         # red — now singing
ACCENT_NEXT = "#f4a623"        # gold — up next
ACCENT_DEFAULT = "#8892a4"     # muted gray — queued
DIVIDER_COLOR = "#1e2d4a"      # subtle divider
OFFLINE_COLOR = "#e6893a"      # orange for offline indicator
LOADING_COLOR = "#5a9bf5"      # blue for loading indicator

# Font scale — multiply all font sizes by this factor
FONT_SCALE = 2

# Base font sizes (multiplied by FONT_SCALE)
FONT_HEADER = ("Helvetica", 28 * FONT_SCALE, "bold")
FONT_NOW_LABEL = ("Helvetica", 14 * FONT_SCALE, "bold")
FONT_NOW_NAME = ("Helvetica", 32 * FONT_SCALE, "bold")
FONT_NOW_SONG = ("Helvetica", 18 * FONT_SCALE)
FONT_QUEUE_NUM = ("Helvetica", 16 * FONT_SCALE, "bold")
FONT_QUEUE_NAME = ("Helvetica", 20 * FONT_SCALE, "bold")
FONT_QUEUE_SONG = ("Helvetica", 14 * FONT_SCALE)
FONT_QUEUE_STATUS = ("Helvetica", 12 * FONT_SCALE)
FONT_STATUS_BAR = ("Helvetica", 11 * FONT_SCALE)
FONT_EMPTY = ("Helvetica", 20 * FONT_SCALE)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_rotation():
    """Fetch the rotation from the Google Sheet CSV endpoint.

    Returns a list of dicts with keys: singer, song_artist, status.
    Filters out rows where status is "Done". Returns at most MAX_ENTRIES.
    Raises on network/parse errors (caller handles).
    """
    with urlopen(SHEET_CSV_URL, timeout=FETCH_TIMEOUT) as response:
        text = response.read().decode("utf-8")
    reader = csv.reader(io.StringIO(text))

    # Skip header row
    try:
        next(reader)
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
# Display
# ---------------------------------------------------------------------------

class RotationDisplay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Rotation")
        self.root.configure(bg=BG_COLOR)

        # Calculate window geometry with margins
        win_height = SCREEN_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
        self.root.geometry(
            f"{WINDOW_WIDTH}x{win_height}+{MARGIN_LEFT}+{MARGIN_TOP}"
        )
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", BG_OPACITY)

        self.cached_entries = []
        self.is_offline = False
        self.is_loading = False

        # Content frame
        self.content = tk.Frame(self.root, bg=BG_COLOR)
        self.content.pack(fill=tk.BOTH, expand=True)

        self._build_ui([])
        self._refresh()

    def _build_ui(self, entries):
        """Rebuild the UI by swapping in a new content frame (no flicker)."""
        new_content = tk.Frame(self.root, bg=BG_COLOR)
        self._populate(new_content, entries)

        # Atomic swap — new frame appears before old is destroyed
        new_content.pack(fill=tk.BOTH, expand=True)
        self.content.destroy()
        self.content = new_content

    def _populate(self, parent, entries):
        """Populate a frame with the rotation display widgets."""
        pad_x = 24

        # --- Header ---
        header_frame = tk.Frame(parent, bg=BG_COLOR)
        header_frame.pack(fill=tk.X, padx=pad_x, pady=(24, 4))

        tk.Label(
            header_frame, text="ROTATION", font=FONT_HEADER,
            fg=HEADER_COLOR, bg=BG_COLOR, anchor="w",
        ).pack(side=tk.LEFT)

        # Loading indicator or singer count on the right
        if self.is_loading:
            self.loading_label = tk.Label(
                header_frame, text="\u21bb", font=FONT_STATUS_BAR,
                fg=LOADING_COLOR, bg=BG_COLOR, anchor="e",
            )
            self.loading_label.pack(side=tk.RIGHT)
        else:
            count_text = f"{len(entries)} singer{'s' if len(entries) != 1 else ''}"
            tk.Label(
                header_frame, text=count_text, font=FONT_STATUS_BAR,
                fg=ACCENT_DEFAULT, bg=BG_COLOR, anchor="e",
            ).pack(side=tk.RIGHT)

        # Divider
        tk.Frame(
            parent, bg=DIVIDER_COLOR, height=2,
        ).pack(fill=tk.X, padx=pad_x, pady=(8, 16))

        if not entries:
            tk.Label(
                parent, text="No singers in queue",
                font=FONT_EMPTY, fg=ACCENT_DEFAULT, bg=BG_COLOR,
            ).pack(pady=60)
            self._build_status_bar(parent, pad_x)
            return

        # --- Now Singing (first entry) ---
        now = entries[0]
        now_frame = tk.Frame(parent, bg=BG_COLOR)
        now_frame.pack(fill=tk.X, padx=pad_x, pady=(0, 8))

        status_text = now["status"] if now["status"] else "Now Singing"
        tk.Label(
            now_frame, text=f"  {status_text.upper()}",
            font=FONT_NOW_LABEL, fg=ACCENT_NOW, bg=BG_COLOR, anchor="w",
        ).pack(fill=tk.X)

        tk.Label(
            now_frame, text=now["singer"],
            font=FONT_NOW_NAME, fg=HEADER_COLOR, bg=BG_COLOR, anchor="w",
            wraplength=WINDOW_WIDTH - pad_x * 2,
        ).pack(fill=tk.X)

        tk.Label(
            now_frame, text=now["song_artist"],
            font=FONT_NOW_SONG, fg=TEXT_COLOR, bg=BG_COLOR, anchor="w",
            wraplength=WINDOW_WIDTH - pad_x * 2,
        ).pack(fill=tk.X)

        # Divider after now singing
        tk.Frame(
            parent, bg=DIVIDER_COLOR, height=1,
        ).pack(fill=tk.X, padx=pad_x, pady=(16, 12))

        # --- Queue (entries 2-N) ---
        for i, entry in enumerate(entries[1:], start=2):
            row = tk.Frame(parent, bg=BG_COLOR)
            row.pack(fill=tk.X, padx=pad_x, pady=(0, 10))

            # Number + name on same line
            top_line = tk.Frame(row, bg=BG_COLOR)
            top_line.pack(fill=tk.X)

            # Determine status color
            status_lower = entry["status"].lower()
            if "next" in status_lower:
                num_color = ACCENT_NEXT
            elif "singing" in status_lower or "now" in status_lower:
                num_color = ACCENT_NOW
            else:
                num_color = ACCENT_DEFAULT

            tk.Label(
                top_line, text=f"{i}.", font=FONT_QUEUE_NUM,
                fg=num_color, bg=BG_COLOR, width=3, anchor="e",
            ).pack(side=tk.LEFT)

            tk.Label(
                top_line, text=f"  {entry['singer']}",
                font=FONT_QUEUE_NAME, fg=HEADER_COLOR, bg=BG_COLOR,
                anchor="w",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

            # Status badge (if present and not generic)
            if entry["status"] and entry["status"].lower() not in ("queued", ""):
                tk.Label(
                    top_line, text=f" {entry['status']} ",
                    font=FONT_QUEUE_STATUS, fg=num_color, bg=BG_COLOR,
                    anchor="e",
                ).pack(side=tk.RIGHT)

            # Song + artist line
            if entry["song_artist"]:
                tk.Label(
                    row, text=f"      {entry['song_artist']}",
                    font=FONT_QUEUE_SONG, fg=TEXT_COLOR, bg=BG_COLOR,
                    anchor="w", wraplength=WINDOW_WIDTH - pad_x * 2 - 40,
                ).pack(fill=tk.X)

        self._build_status_bar(parent, pad_x)

    def _build_status_bar(self, parent, pad_x):
        """Add the status/update time bar at the bottom."""
        # Spacer to push status bar down
        spacer = tk.Frame(parent, bg=BG_COLOR)
        spacer.pack(fill=tk.BOTH, expand=True)

        # Divider
        tk.Frame(
            parent, bg=DIVIDER_COLOR, height=1,
        ).pack(fill=tk.X, padx=pad_x, pady=(8, 4))

        if self.is_offline:
            status_text = "Offline \u2014 showing cached data"
            status_color = OFFLINE_COLOR
        else:
            now = datetime.now().strftime("%I:%M %p").lstrip("0")
            status_text = f"Updated {now}"
            status_color = ACCENT_DEFAULT

        tk.Label(
            parent, text=status_text, font=FONT_STATUS_BAR,
            fg=status_color, bg=BG_COLOR, anchor="w",
        ).pack(fill=tk.X, padx=pad_x, pady=(0, 12))

    def _refresh(self):
        """Kick off a background fetch, then update the UI when done."""
        self.is_loading = True
        self._build_ui(self.cached_entries)

        thread = threading.Thread(target=self._fetch_and_update, daemon=True)
        thread.start()

    def _fetch_and_update(self):
        """Fetch data in a background thread, then schedule UI update."""
        try:
            entries = fetch_rotation()
            self.root.after(0, self._apply_data, entries, False)
        except (URLError, OSError, ValueError, csv.Error):
            self.root.after(0, self._apply_data, self.cached_entries, True)

    def _apply_data(self, entries, offline):
        """Apply fetched data to the UI (called on the main thread)."""
        self.cached_entries = entries
        self.is_offline = offline
        self.is_loading = False
        self._build_ui(entries)
        self.root.after(REFRESH_MS, self._refresh)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    RotationDisplay().run()
