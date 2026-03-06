"""Google Sheets rotation integration for singer queue management.

Reads and writes to a Google Sheet that tracks the singer rotation.
Uses gspread with a service account for authentication.

Auto-detects the header row by looking for a row containing "Singer"
and "Status" columns, then maps column positions dynamically.
"""

import json
import os
import time
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Cache duration in seconds
CACHE_TTL = 10

# Local file cache for conky rotation display (avoids sheet polling delay)
ROTATION_CACHE_FILE = "/tmp/rotation_cache.json"

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

    def _get_spreadsheet(self):
        """Get or create the gspread spreadsheet connection."""
        if self._client is None:
            creds = Credentials.from_service_account_file(
                self._credentials_file, scopes=SCOPES
            )
            self._client = gspread.authorize(creds)
        return self._client.open_by_key(self._sheet_id)

    def _get_sheet(self):
        """Get or create the gspread worksheet connection."""
        if self._sheet is not None:
            return self._sheet
        spreadsheet = self._get_spreadsheet()
        self._sheet = spreadsheet.sheet1
        return self._sheet

    def _invalidate_cache(self):
        self._cache = None
        self._cache_time = 0
        self._write_display_cache()

    def _write_display_cache(self):
        """Write current rotation to a local JSON file for the conky display.

        Re-fetches from the sheet (cache was just invalidated) and writes
        the queue + stats so rotation_data.py can read locally instead of
        polling the sheet.
        """
        try:
            entries = self.get_rotation(force_refresh=True)
            queue = [
                {
                    "singer": e["singer"],
                    "song_artist": e["song_artist"],
                    "status": e["status"],
                }
                for e in entries
            ]
            # Build stats from the sheet data
            sheet = self._get_sheet()
            all_values = sheet.get_all_values()
            header_row, col_map = _find_header(all_values)
            if header_row is None:
                return
            col_singer = col_map.get("singer", 1)
            col_status = col_map.get("status", 3)

            all_singers = set()
            done_count = 0
            queued_count = 0
            earliest_ts = None
            col_ts = col_map.get("timestamp")

            for row in all_values[header_row:]:
                if len(row) <= col_status:
                    continue
                singer = row[col_singer].strip()
                if not singer:
                    continue
                all_singers.add(singer)
                status = row[col_status].strip().lower()
                if status == "done":
                    done_count += 1
                else:
                    queued_count += 1
                if col_ts is not None and earliest_ts is None:
                    ts_raw = row[col_ts].strip() if col_ts < len(row) else ""
                    if ts_raw:
                        earliest_ts = ts_raw

            started = ""
            if earliest_ts:
                try:
                    dt = datetime.strptime(earliest_ts, "%m/%d/%Y %H:%M:%S")
                    started = dt.strftime("%-m/%-d %-H:%M")
                except ValueError:
                    started = earliest_ts

            data = {
                "queue": queue,
                "stats": {
                    "singers": len(all_singers),
                    "sung": done_count,
                    "queued": queued_count,
                    "started": started,
                },
                "updated": time.time(),
            }
            tmp = ROTATION_CACHE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, ROTATION_CACHE_FILE)
        except Exception:
            pass  # Best-effort; display will fall back to sheet polling

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

        new_row[col_map.get("status", 3)] = "Waiting"
        sheet.append_row(new_row, value_input_option="USER_ENTERED")
        self._invalidate_cache()

    def update_entry(self, row_index, singer=None, song_artist=None):
        """Update the singer name and/or song for a given row."""
        sheet = self._get_sheet()
        all_values = sheet.get_all_values()
        _, col_map = _find_header(all_values)

        updates = []
        if singer is not None:
            col = col_map.get("singer", 1)
            updates.append({"range": f"{_col_letter(col)}{row_index}", "values": [[singer]]})
        if song_artist is not None:
            col = col_map.get("song_artist", 2)
            updates.append({"range": f"{_col_letter(col)}{row_index}", "values": [[song_artist]]})

        if updates:
            sheet.batch_update(updates, value_input_option="USER_ENTERED")
        self._invalidate_cache()

    def delete_entry(self, row_index):
        """Delete a row from the sheet entirely."""
        sheet = self._get_sheet()
        sheet.delete_rows(row_index)
        self._invalidate_cache()

    def move_entry(self, from_row, to_row):
        """Move a row from one position to another in the sheet.

        Args:
            from_row: 1-based sheet row to move
            to_row: 1-based target sheet row position
        """
        if from_row == to_row:
            return
        sheet = self._get_sheet()
        all_values = sheet.get_all_values()
        if from_row < 1 or from_row > len(all_values):
            raise ValueError(f"from_row {from_row} out of range")
        if to_row < 1 or to_row > len(all_values):
            raise ValueError(f"to_row {to_row} out of range")

        row_data = all_values[from_row - 1]  # 0-based index

        # Delete the source row first
        sheet.delete_rows(from_row)

        # After deletion, target index shifts if it was below the source
        insert_at = to_row if to_row < from_row else to_row - 1

        # insert_row is 1-based, inserts BEFORE the given row
        sheet.insert_row(row_data, insert_at, value_input_option="USER_ENTERED")
        self._invalidate_cache()

    def _set_exclusive_status(self, row_index, new_status, clear_statuses):
        """Set a status on one row and clear it from all others.

        Args:
            row_index: 1-based sheet row to set
            new_status: Status string to apply (e.g. "Now Singing", "Up Next")
            clear_statuses: Set of lowercase status strings to clear back to "Waiting"
        """
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
                    "values": [[new_status]],
                })
            elif status in clear_statuses:
                batch_updates.append({
                    "range": f"{col_letter}{idx}",
                    "values": [["Waiting"]],
                })

        if batch_updates:
            sheet.batch_update(batch_updates, value_input_option="USER_ENTERED")
        self._invalidate_cache()

    def mark_singing(self, row_index):
        """Mark a row as 'Now Singing' and reset any other singing rows to 'Waiting'."""
        self._set_exclusive_status(
            row_index, "Now Singing",
            {"now singing", "singing now", "singing"},
        )

    def mark_up_next(self, row_index):
        """Mark a row as 'Up Next' and reset any other 'Up Next' rows to 'Waiting'."""
        self._set_exclusive_status(
            row_index, "Up Next",
            {"up next", "next"},
        )

    def archive_rotation(self):
        """Move all data rows from the rotation sheet to the 'Past events' sheet.

        Copies every row below the header (including Done entries) to the bottom
        of the 'Past events' sheet with a date separator, then deletes them from
        the rotation sheet, leaving the header intact.

        Returns the number of rows archived.
        """
        sheet = self._get_sheet()
        all_values = sheet.get_all_values()

        header_row, _ = _find_header(all_values)
        if header_row is None:
            return 0

        # All rows below the header (header_row is 1-based, data starts at index header_row)
        data_rows = all_values[header_row:]
        # Filter out completely empty rows
        data_rows = [r for r in data_rows if any(cell.strip() for cell in r)]
        if not data_rows:
            return 0

        # Get or create the "Past events" sheet
        spreadsheet = self._get_spreadsheet()
        try:
            archive_sheet = spreadsheet.worksheet("Past events")
        except gspread.exceptions.WorksheetNotFound:
            archive_sheet = spreadsheet.add_worksheet(
                title="Past events", rows=1000, cols=len(all_values[0]) if all_values else 7
            )

        # Add a date separator row then the data
        dt = datetime.now()
        separator = [f"--- {dt.strftime('%B %d, %Y')} ---"]
        archive_sheet.append_row(separator, value_input_option="USER_ENTERED")
        archive_sheet.append_rows(data_rows, value_input_option="USER_ENTERED")

        # Replace data rows with a single starter entry (Sheets won't allow
        # deleting all non-frozen rows, so we overwrite row 1 then delete the rest)
        col_map = _find_header(all_values)[1]
        dt = datetime.now()
        starter = [""] * len(all_values[0])
        if "timestamp" in col_map:
            starter[col_map["timestamp"]] = f"{dt.month}/{dt.day}/{dt.year} {dt:%H:%M:%S}"
        starter[col_map.get("singer", 1)] = "Andrew"
        starter[col_map.get("song_artist", 2)] = "First Song of the Night"
        starter[col_map.get("status", 3)] = "Waiting"

        first_data_row = header_row + 1
        last_data_row = len(all_values)

        # Write starter into the first data row
        col_letter = _col_letter(len(starter) - 1)
        sheet.update(f"A{first_data_row}:{col_letter}{first_data_row}", [starter],
                     value_input_option="USER_ENTERED")

        # Delete remaining rows (if any)
        if last_data_row > first_data_row:
            sheet.delete_rows(first_data_row + 1, last_data_row)

        self._invalidate_cache()
        return len(data_rows)
