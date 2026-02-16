#!/usr/bin/env python3
"""Karaoke rotation display overlay.

Fetches the singer rotation from a public Google Sheet and displays the next 10
singers as a persistent overlay on the left third of the screen. Designed for
venue visibility during live karaoke events.

Requires: python3-tk (apt-get install -y python3-tk)
No pip dependencies — stdlib only.
"""

import csv
import io
import tkinter as tk
from datetime import datetime
from urllib.error import URLError
from urllib.request import urlopen

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Google Sheet published URL — replace SHEET_ID with your sheet's ID.
# The sheet must be published to the web (File > Share > Publish to web).
SHEET_ID = "1sNR3KTYaxRj0MwqpSPyS42MBCz2VIQQdye-HLHZ8hTY"
SHEET_GID = "0"  # first tab
SHEET_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    f"/gviz/tq?tqx=out:csv&gid={SHEET_GID}"
)

# Expected column layout (0-indexed). Adjust if your sheet differs.
COL_POSITION = 0   # "#" or position number
COL_SINGER = 1     # Singer name
COL_SONG = 2       # Song title
COL_ARTIST = 3     # Artist
COL_STATUS = 4     # Status text (e.g. "Done", "Now Singing", "Up Next")

# Display geometry — left 1/3 of a 1920×1080 screen
WINDOW_WIDTH = 640
WINDOW_HEIGHT = 1080
WINDOW_X = 0
WINDOW_Y = 0

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

# Fonts (family, size, weight)
FONT_HEADER = ("Helvetica", 28, "bold")
FONT_NOW_LABEL = ("Helvetica", 14, "bold")
FONT_NOW_NAME = ("Helvetica", 32, "bold")
FONT_NOW_SONG = ("Helvetica", 18)
FONT_QUEUE_NUM = ("Helvetica", 16, "bold")
FONT_QUEUE_NAME = ("Helvetica", 20, "bold")
FONT_QUEUE_SONG = ("Helvetica", 14)
FONT_QUEUE_STATUS = ("Helvetica", 12)
FONT_STATUS_BAR = ("Helvetica", 11)
FONT_EMPTY = ("Helvetica", 20)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_rotation():
    """Fetch the rotation from the Google Sheet CSV endpoint.

    Returns a list of dicts with keys: singer, song, artist, status.
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
        if len(row) <= max(COL_SINGER, COL_STATUS):
            continue
        status = row[COL_STATUS].strip() if COL_STATUS < len(row) else ""
        if status.lower() == "done":
            continue
        entries.append({
            "singer": row[COL_SINGER].strip() if COL_SINGER < len(row) else "",
            "song": row[COL_SONG].strip() if COL_SONG < len(row) else "",
            "artist": row[COL_ARTIST].strip() if COL_ARTIST < len(row) else "",
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
        self.root.geometry(
            f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{WINDOW_X}+{WINDOW_Y}"
        )
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        self.cached_entries = []
        self.is_offline = False

        # Scrollable content area
        self.canvas = tk.Canvas(
            self.root, bg=BG_COLOR, highlightthickness=0,
            width=WINDOW_WIDTH, height=WINDOW_HEIGHT,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.content = tk.Frame(self.canvas, bg=BG_COLOR)
        self.canvas.create_window(
            (0, 0), window=self.content, anchor="nw", width=WINDOW_WIDTH,
        )

        self._build_ui([])
        self._refresh()

    def _build_ui(self, entries):
        """Rebuild the entire UI with the given entries."""
        for widget in self.content.winfo_children():
            widget.destroy()

        pad_x = 24

        # --- Header ---
        header_frame = tk.Frame(self.content, bg=BG_COLOR)
        header_frame.pack(fill=tk.X, padx=pad_x, pady=(24, 4))

        tk.Label(
            header_frame, text="ROTATION", font=FONT_HEADER,
            fg=HEADER_COLOR, bg=BG_COLOR, anchor="w",
        ).pack(side=tk.LEFT)

        count_text = f"{len(entries)} singer{'s' if len(entries) != 1 else ''}"
        tk.Label(
            header_frame, text=count_text, font=FONT_STATUS_BAR,
            fg=ACCENT_DEFAULT, bg=BG_COLOR, anchor="e",
        ).pack(side=tk.RIGHT)

        # Divider
        tk.Frame(
            self.content, bg=DIVIDER_COLOR, height=2,
        ).pack(fill=tk.X, padx=pad_x, pady=(8, 16))

        if not entries:
            tk.Label(
                self.content, text="No singers in queue",
                font=FONT_EMPTY, fg=ACCENT_DEFAULT, bg=BG_COLOR,
            ).pack(pady=60)
            self._build_status_bar(pad_x)
            return

        # --- Now Singing (first entry) ---
        now = entries[0]
        now_frame = tk.Frame(self.content, bg=BG_COLOR)
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

        song_line = now["song"]
        if now["artist"]:
            song_line += f"  —  {now['artist']}"
        tk.Label(
            now_frame, text=song_line,
            font=FONT_NOW_SONG, fg=TEXT_COLOR, bg=BG_COLOR, anchor="w",
            wraplength=WINDOW_WIDTH - pad_x * 2,
        ).pack(fill=tk.X)

        # Divider after now singing
        tk.Frame(
            self.content, bg=DIVIDER_COLOR, height=1,
        ).pack(fill=tk.X, padx=pad_x, pady=(16, 12))

        # --- Queue (entries 2–N) ---
        for i, entry in enumerate(entries[1:], start=2):
            row = tk.Frame(self.content, bg=BG_COLOR)
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
            song_line = entry["song"]
            if entry["artist"]:
                song_line += f"  —  {entry['artist']}"
            if song_line:
                tk.Label(
                    row, text=f"      {song_line}",
                    font=FONT_QUEUE_SONG, fg=TEXT_COLOR, bg=BG_COLOR,
                    anchor="w", wraplength=WINDOW_WIDTH - pad_x * 2 - 40,
                ).pack(fill=tk.X)

        self._build_status_bar(pad_x)

    def _build_status_bar(self, pad_x):
        """Add the status/update time bar at the bottom."""
        # Spacer to push status bar down
        spacer = tk.Frame(self.content, bg=BG_COLOR)
        spacer.pack(fill=tk.BOTH, expand=True)

        # Divider
        tk.Frame(
            self.content, bg=DIVIDER_COLOR, height=1,
        ).pack(fill=tk.X, padx=pad_x, pady=(8, 4))

        if self.is_offline:
            status_text = "Offline — showing cached data"
            status_color = OFFLINE_COLOR
        else:
            now = datetime.now().strftime("%-I:%M %p")
            status_text = f"Updated {now}"
            status_color = ACCENT_DEFAULT

        tk.Label(
            self.content, text=status_text, font=FONT_STATUS_BAR,
            fg=status_color, bg=BG_COLOR, anchor="w",
        ).pack(fill=tk.X, padx=pad_x, pady=(0, 12))

    def _refresh(self):
        """Fetch new data and rebuild the UI, then schedule the next refresh."""
        try:
            entries = fetch_rotation()
            self.cached_entries = entries
            self.is_offline = False
        except (URLError, OSError, ValueError):
            entries = self.cached_entries
            self.is_offline = True

        self._build_ui(entries)
        self.root.after(REFRESH_MS, self._refresh)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    RotationDisplay().run()
