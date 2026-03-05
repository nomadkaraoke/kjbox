"""Google Sheets rotation integration for singer queue management.

Reads and writes to a Google Sheet that tracks the singer rotation.
Uses gspread with a service account for authentication.

Auto-detects the header row by looking for a row containing "Singer"
and "Status" columns, then maps column positions dynamically.
"""

import os
import time
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Cache duration in seconds
CACHE_TTL = 10

# Column letter lookup (0-based index to A, B, C, ...)
def _col_letter(idx):
    return chr(ord('A') + idx)


def _find_header(all_values):
    """Find the header row and return (header_row_index, column_map).

    Scans for a row containing "Singer" and "Status" (case-insensitive).
    Returns 1-based row index and a dict mapping canonical names to 0-based
    column indices.
    """
    for i, row in enumerate(all_values):
        lower_cells = [c.strip().lower() for c in row]
        if "singer" in lower_cells and "status" in lower_cells:
            col_map = {}
            for j, cell in enumerate(lower_cells):
                if cell == "singer":
                    col_map["singer"] = j
                elif cell in ("song & artist", "song", "song and artist"):
                    col_map["song_artist"] = j
                elif cell == "status":
                    col_map["status"] = j
                elif cell == "notes":
                    col_map["notes"] = j
                elif cell == "timestamp":
                    col_map["timestamp"] = j
            return i + 1, col_map  # 1-based row index
    return None, {}


class RotationManager:
    """Manages singer rotation via Google Sheets API."""

    def __init__(self, sheet_id, credentials_file):
        self._sheet_id = sheet_id
        self._credentials_file = os.path.expanduser(credentials_file)
        self._client = None
        self._sheet = None
        self._cache = None
        self._cache_time = 0

    def _get_sheet(self):
        """Get or create the gspread worksheet connection."""
        if self._sheet is not None:
            return self._sheet
        creds = Credentials.from_service_account_file(
            self._credentials_file, scopes=SCOPES
        )
        self._client = gspread.authorize(creds)
        spreadsheet = self._client.open_by_key(self._sheet_id)
        self._sheet = spreadsheet.sheet1
        return self._sheet

    def _invalidate_cache(self):
        self._cache = None
        self._cache_time = 0

    def get_rotation(self, force_refresh=False):
        """Fetch the current rotation queue from the sheet.

        Returns a list of dicts: {row_index, singer, song_artist, status, notes}
        Only includes non-done entries. Results are cached for CACHE_TTL seconds.
        """
        now = time.time()
        if not force_refresh and self._cache is not None and (now - self._cache_time) < CACHE_TTL:
            return self._cache

        sheet = self._get_sheet()
        all_values = sheet.get_all_values()

        header_row, col_map = _find_header(all_values)
        if header_row is None:
            self._cache = []
            self._cache_time = time.time()
            return self._cache

        col_singer = col_map.get("singer", 1)
        col_song = col_map.get("song_artist", 2)
        col_status = col_map.get("status", 3)
        col_notes = col_map.get("notes")

        entries = []
        for idx, row in enumerate(all_values[header_row:], start=header_row + 1):
            if len(row) <= col_status:
                continue
            singer = row[col_singer].strip()
            status = row[col_status].strip()
            if not singer:
                continue
            if status.lower() == "done":
                continue

            entries.append({
                "row_index": idx,
                "singer": singer,
                "song_artist": row[col_song].strip() if col_song < len(row) else "",
                "status": status,
                "notes": row[col_notes].strip() if col_notes is not None and col_notes < len(row) else "",
            })

        self._cache = entries
        self._cache_time = time.time()
        return entries

    def update_status(self, row_index, new_status):
        """Update the status cell for a given row.

        Args:
            row_index: 1-based sheet row number
            new_status: New status string (e.g. "Singing Now", "Done", "Next")
        """
        sheet = self._get_sheet()
        all_values = sheet.get_all_values()
        _, col_map = _find_header(all_values)
        col_status = col_map.get("status", 3)
        sheet.update_cell(row_index, col_status + 1, new_status)
        self._invalidate_cache()

    def add_entry(self, singer, song_artist, notes=""):
        """Append a new singer entry to the sheet.

        Args:
            singer: Singer name
            song_artist: Song and artist info
            notes: Optional notes
        """
        sheet = self._get_sheet()
        all_values = sheet.get_all_values()
        _, col_map = _find_header(all_values)

        # Build a row matching the sheet's column layout
        max_col = max(col_map.values()) + 1 if col_map else 5
        new_row = [""] * max_col
        if "timestamp" in col_map:
            dt = datetime.now()
            new_row[col_map["timestamp"]] = f"{dt.month}/{dt.day}/{dt.year} {dt:%H:%M:%S}"
        new_row[col_map.get("singer", 1)] = singer
        new_row[col_map.get("song_artist", 2)] = song_artist
        if notes and "notes" in col_map:
            new_row[col_map["notes"]] = notes

        sheet.append_row(new_row, value_input_option="USER_ENTERED")
        self._invalidate_cache()

    def mark_singing(self, row_index):
        """Mark a row as 'Singing Now' and clear any other 'Singing Now' statuses."""
        sheet = self._get_sheet()
        all_values = sheet.get_all_values()

        header_row, col_map = _find_header(all_values)
        if header_row is None:
            return
        col_status = col_map.get("status", 3)
        col_letter = _col_letter(col_status)

        batch_updates = []
        for idx, row in enumerate(all_values[header_row:], start=header_row + 1):
            if len(row) <= col_status:
                continue
            status = row[col_status].strip().lower()
            if idx == row_index:
                batch_updates.append({
                    "range": f"{col_letter}{idx}",
                    "values": [["Singing Now"]],
                })
            elif status in ("singing now", "singing"):
                batch_updates.append({
                    "range": f"{col_letter}{idx}",
                    "values": [[""]],
                })

        if batch_updates:
            sheet.batch_update(batch_updates, value_input_option="USER_ENTERED")
        self._invalidate_cache()
