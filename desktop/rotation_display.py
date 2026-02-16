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
import threading
import tkinter as tk
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
MARGIN_TOP = 60
MARGIN_BOTTOM = 105
MARGIN_LEFT = 70

# Window width (independent of margins)
WINDOW_WIDTH = 600

# Internal padding (pixels) — space between window edge and content
PAD_X = 10

# How many queue entries to show (including the current singer)
MAX_ENTRIES = 10

# Refresh interval in milliseconds
REFRESH_MS = 30_000

# HTTP fetch timeout in seconds
FETCH_TIMEOUT = 10

# Colors
TEXT_COLOR = "#e0e6f0"         # light gray text
HEADER_COLOR = "#ffffff"       # white header
ACCENT_NOW = "#e63946"         # red — now singing
ACCENT_NEXT = "#f4a623"        # gold — up next
ACCENT_DEFAULT = "#8892a4"     # muted gray — queued
LOADING_COLOR = "#5a9bf5"      # blue for loading indicator

# Font scale — multiply all font sizes by this factor
FONT_SCALE = 2

# Base font sizes (multiplied by FONT_SCALE)
FONT_HEADER = ("Helvetica", 20 * FONT_SCALE, "bold")
FONT_NOW_LABEL = ("Helvetica", 14 * FONT_SCALE, "bold")
FONT_NOW_NAME = ("Helvetica", 26 * FONT_SCALE, "bold")
FONT_NOW_SONG = ("Helvetica", 14 * FONT_SCALE)
FONT_QUEUE_NUM = ("Helvetica", 16 * FONT_SCALE, "bold")
FONT_QUEUE_NAME = ("Helvetica", 20 * FONT_SCALE, "bold")
FONT_QUEUE_SONG = ("Helvetica", 10 * FONT_SCALE)
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
    """Flicker-free rotation overlay using in-place label updates."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Rotation")

        # Calculate window geometry with margins
        win_height = SCREEN_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
        self.root.geometry(
            f"{WINDOW_WIDTH}x{win_height}+{MARGIN_LEFT}+{MARGIN_TOP}"
        )
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        self.cached_entries = []
        self.is_offline = False

        # Pre-build all widgets once — updates change text/color in place
        self._create_widgets()
        self._apply_entries([])
        self._refresh()

    def _create_widgets(self):
        """Create all widgets once. They are reused across refreshes."""
        content_width = WINDOW_WIDTH - PAD_X * 2

        # --- Header row ---
        header_frame = tk.Frame(self.root)
        header_frame.pack(fill=tk.X, padx=PAD_X, pady=(12, 0))

        self.header_label = tk.Label(
            header_frame, text="ROTATION", font=FONT_HEADER,
            fg=HEADER_COLOR, anchor="w",
        )
        self.header_label.pack(side=tk.LEFT)

        self.count_label = tk.Label(
            header_frame, text="", font=FONT_STATUS_BAR,
            fg=ACCENT_DEFAULT, anchor="e",
        )
        self.count_label.pack(side=tk.RIGHT)

        # --- "Now singing" section ---
        self.now_frame = tk.Frame(self.root)
        self.now_frame.pack(fill=tk.X, padx=PAD_X)

        self.now_status_label = tk.Label(
            self.now_frame, text="", font=FONT_NOW_LABEL,
            fg=ACCENT_NOW, anchor="w",
        )
        self.now_status_label.pack(fill=tk.X)

        self.now_name_label = tk.Label(
            self.now_frame, text="", font=FONT_NOW_NAME,
            fg=HEADER_COLOR, anchor="w",
            wraplength=content_width,
        )
        self.now_name_label.pack(fill=tk.X)

        self.now_song_label = tk.Label(
            self.now_frame, text="", font=FONT_NOW_SONG,
            fg=TEXT_COLOR, anchor="w",
            wraplength=content_width,
        )
        self.now_song_label.pack(fill=tk.X)

        # Divider after now singing
        self.now_divider = tk.Frame(self.root, height=1)
        self.now_divider.pack(fill=tk.X, padx=PAD_X, pady=(10, 6))

        # --- Queue slots (pre-create MAX_ENTRIES-1 rows) ---
        self.queue_rows = []
        for _ in range(MAX_ENTRIES - 1):
            row_frame = tk.Frame(self.root)
            row_frame.pack(fill=tk.X, padx=PAD_X, pady=(0, 2))

            top_line = tk.Frame(row_frame)
            top_line.pack(fill=tk.X)

            num_label = tk.Label(
                top_line, text="", font=FONT_QUEUE_NUM,
                fg=ACCENT_DEFAULT, anchor="w",
            )
            num_label.pack(side=tk.LEFT)

            name_label = tk.Label(
                top_line, text="", font=FONT_QUEUE_NAME,
                fg=HEADER_COLOR, anchor="w",
            )
            name_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

            status_label = tk.Label(
                top_line, text="", font=FONT_QUEUE_STATUS,
                fg=ACCENT_DEFAULT, anchor="e",
            )
            status_label.pack(side=tk.RIGHT)

            song_label = tk.Label(
                row_frame, text="", font=FONT_QUEUE_SONG,
                fg=TEXT_COLOR, anchor="w",
                wraplength=content_width - 20,
            )
            song_label.pack(fill=tk.X, padx=(4, 0))

            self.queue_rows.append({
                "frame": row_frame,
                "num": num_label,
                "name": name_label,
                "status": status_label,
                "song": song_label,
            })

        # --- Empty queue message (hidden by default) ---
        self.empty_label = tk.Label(
            self.root, text="No singers in queue",
            font=FONT_EMPTY, fg=ACCENT_DEFAULT,
        )

    def _apply_entries(self, entries):
        """Update all widget text/colors in place — no widget destruction."""
        has_entries = len(entries) > 0

        # Header count / loading indicator
        if has_entries:
            count_text = f"{len(entries)} singer{'s' if len(entries) != 1 else ''}"
            self.count_label.configure(text=count_text, fg=ACCENT_DEFAULT)
        else:
            self.count_label.configure(text="")

        # Now singing section
        if has_entries:
            now = entries[0]
            status_text = now["status"] if now["status"] else "Now Singing"
            self.now_status_label.configure(text=status_text.upper())
            self.now_name_label.configure(text=now["singer"])
            self.now_song_label.configure(text=now["song_artist"])
            self.now_frame.pack(fill=tk.X, padx=PAD_X)
            self.now_divider.pack(fill=tk.X, padx=PAD_X, pady=(10, 6))
            self.empty_label.pack_forget()
        else:
            self.now_frame.pack_forget()
            self.now_divider.pack_forget()
            self.empty_label.pack(pady=60)

        # Queue rows
        queue = entries[1:] if has_entries else []
        for idx, row_widgets in enumerate(self.queue_rows):
            if idx < len(queue):
                entry = queue[idx]
                num = idx + 2

                # Status color
                status_lower = entry["status"].lower()
                if "next" in status_lower:
                    color = ACCENT_NEXT
                elif "singing" in status_lower or "now" in status_lower:
                    color = ACCENT_NOW
                else:
                    color = ACCENT_DEFAULT

                row_widgets["num"].configure(text=f"{num}. ", fg=color)
                row_widgets["name"].configure(text=entry["singer"])

                # Status badge
                if entry["status"] and entry["status"].lower() not in ("queued", "waiting", ""):
                    row_widgets["status"].configure(text=entry["status"], fg=color)
                else:
                    row_widgets["status"].configure(text="")

                row_widgets["song"].configure(text=entry["song_artist"])
                row_widgets["frame"].pack(fill=tk.X, padx=PAD_X, pady=(0, 2))
            else:
                row_widgets["frame"].pack_forget()

    def _show_loading(self):
        """Show loading indicator in header."""
        self.count_label.configure(text="\u21bb", fg=LOADING_COLOR)

    def _refresh(self):
        """Kick off a background fetch, then update the UI when done."""
        self._show_loading()
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
        self._apply_entries(entries)
        self.root.after(REFRESH_MS, self._refresh)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    RotationDisplay().run()
