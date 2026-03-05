"""Google Sheets rotation integration for singer queue management.

Reads and writes to a Google Sheet that tracks the singer rotation.
Uses gspread with a service account for authentication.

Sheet columns: Timestamp | Singer | Song & Artist | Status | Notes
"""

import os
import time
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

# Column indices (0-based)
COL_TIMESTAMP = 0
COL_SINGER = 1
COL_SONG_ARTIST = 2
COL_STATUS = 3
COL_NOTES = 4

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Cache duration in seconds
CACHE_TTL = 10


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

        if len(all_values) <= 1:
            # Only header row or empty
            self._cache = []
            self._cache_time = time.time()
            return self._cache

        entries = []
        for idx, row in enumerate(all_values[1:], start=2):  # row 2 in sheet (1-indexed, skip header)
            if len(row) <= COL_STATUS:
                continue
            singer = row[COL_SINGER].strip()
            status = row[COL_STATUS].strip()
            if not singer:
                continue
            if status.lower() == "done":
                continue

            entries.append({
                "row_index": idx,
                "singer": singer,
                "song_artist": row[COL_SONG_ARTIST].strip() if len(row) > COL_SONG_ARTIST else "",
                "status": status,
                "notes": row[COL_NOTES].strip() if len(row) > COL_NOTES else "",
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
        # Status is column D (index 4 in 1-based)
        sheet.update_cell(row_index, COL_STATUS + 1, new_status)
        self._invalidate_cache()

    def add_entry(self, singer, song_artist, notes=""):
        """Append a new singer entry to the sheet.

        Args:
            singer: Singer name
            song_artist: Song and artist info
            notes: Optional notes
        """
        sheet = self._get_sheet()
        timestamp = datetime.now().strftime("%-m/%-d/%Y %H:%M:%S")
        sheet.append_row(
            [timestamp, singer, song_artist, "", notes],
            value_input_option="USER_ENTERED",
        )
        self._invalidate_cache()

    def mark_singing(self, row_index):
        """Mark a row as 'Singing Now' and clear any other 'Singing Now' statuses."""
        sheet = self._get_sheet()
        all_values = sheet.get_all_values()

        # Batch: clear other "Singing Now" entries and set this one
        batch_updates = []
        for idx, row in enumerate(all_values[1:], start=2):
            if len(row) <= COL_STATUS:
                continue
            status = row[COL_STATUS].strip().lower()
            if idx == row_index:
                batch_updates.append({
                    "range": f"D{idx}",
                    "values": [["Singing Now"]],
                })
            elif status in ("singing now", "singing"):
                batch_updates.append({
                    "range": f"D{idx}",
                    "values": [[""]],
                })

        if batch_updates:
            sheet.batch_update(batch_updates, value_input_option="USER_ENTERED")
        self._invalidate_cache()
