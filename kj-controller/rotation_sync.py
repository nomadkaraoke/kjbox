"""SheetSync: Background Google Sheets backup for rotation data.

Pushes SQLite state to a Google Sheet every N seconds in a daemon thread.
Provides restore_from_sheet() for emergency pull.
Gracefully handles network errors (log warning, retry next cycle).
"""

import logging
import threading
import time
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _col_letter(idx):
    """Return column letter for 0-based column index (A, B, C, ...)."""
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


class SheetSync:
    """Background sync of rotation data to Google Sheets.

    Runs a daemon thread that pushes SQLite state to the Sheet every
    sync_interval seconds. Provides restore_from_sheet() for emergency pull.
    """

    def __init__(self, store, sheet_id, credentials_file, sync_interval=30):
        """Initialise SheetSync.

        Args:
            store: RotationStore instance.
            sheet_id: Google Sheets spreadsheet ID.
            credentials_file: Path to service account JSON key file.
            sync_interval: Seconds between background sync cycles.
        """
        self._store = store
        self._sheet_id = sheet_id
        self._credentials_file = credentials_file
        self._sync_interval = sync_interval

        self._client = None
        self._sheet = None
        self._is_online = False
        self._last_sync = None
        self._stop_event = threading.Event()
        self._thread = None

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _get_sheet(self):
        """Return (or lazily create) the gspread worksheet connection."""
        if self._sheet is not None:
            return self._sheet
        creds = Credentials.from_service_account_file(
            self._credentials_file, scopes=SCOPES
        )
        self._client = gspread.authorize(creds)
        spreadsheet = self._client.open_by_key(self._sheet_id)
        self._sheet = spreadsheet.sheet1
        return self._sheet

    def _reset_connection(self):
        """Drop client/sheet references so next call re-authenticates."""
        self._client = None
        self._sheet = None

    # ------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------

    def start(self):
        """Start the background sync daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Signal the background thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self):
        """Background sync loop."""
        while not self._stop_event.is_set():
            self.sync_now()
            self._stop_event.wait(timeout=self._sync_interval)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync_now(self):
        """Push current rotation to the Sheet.

        Uses sheet.update() to overwrite data rows in-place (NOT clear-then-write).
        Updates last_sheet_sync in rotation_meta on success.

        Returns:
            True on success, False on error.
        """
        try:
            sheet = self._get_sheet()
            all_values = sheet.get_all_values()
            header_row, col_map = _find_header(all_values)
            if header_row is None:
                logger.warning("SheetSync: no header row found in sheet")
                return False

            entries = self._store.get_all_entries()

            col_singer = col_map.get("singer", 1)
            col_song = col_map.get("song_artist", 2)
            col_status = col_map.get("status", 3)
            col_notes = col_map.get("notes")
            col_ts = col_map.get("timestamp")

            num_cols = max(col_map.values()) + 1 if col_map else 5

            rows = []
            for entry in entries:
                row = [""] * num_cols
                row[col_singer] = entry.get("singer", "")
                row[col_song] = entry.get("song_artist", "")
                row[col_status] = entry.get("status", "Waiting")
                if col_notes is not None:
                    row[col_notes] = entry.get("notes", "")
                if col_ts is not None:
                    row[col_ts] = entry.get("created_at", "")
                rows.append(row)

            if rows:
                first_data_row = header_row + 1
                last_data_row = first_data_row + len(rows) - 1
                col_end = _col_letter(num_cols - 1)
                range_str = f"A{first_data_row}:{col_end}{last_data_row}"
                sheet.update(range_str, rows)

            # Record sync time in meta
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = self._store._get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO rotation_meta (key, value) VALUES ('last_sheet_sync', ?)",
                (now_str,),
            )
            conn.commit()

            self._is_online = True
            self._last_sync = time.time()
            return True

        except Exception as exc:
            logger.warning("SheetSync: sync failed: %s", exc)
            self._is_online = False
            self._reset_connection()
            return False

    def restore_from_sheet(self):
        """Read the Sheet and repopulate rotation_entries.

        Clears all existing rotation_entries and re-inserts from the Sheet
        with auto-assigned positions.

        Returns:
            Number of entries imported.
        """
        sheet = self._get_sheet()
        all_values = sheet.get_all_values()
        header_row, col_map = _find_header(all_values)
        if header_row is None:
            return 0

        col_singer = col_map.get("singer", 1)
        col_song = col_map.get("song_artist", 2)
        col_status = col_map.get("status", 3)
        col_notes = col_map.get("notes")

        # Collect non-empty rows from sheet
        sheet_entries = []
        for row in all_values[header_row:]:
            if len(row) <= col_status:
                continue
            singer = row[col_singer].strip() if col_singer < len(row) else ""
            if not singer:
                continue
            status = row[col_status].strip() if col_status < len(row) else "Waiting"
            song_artist = row[col_song].strip() if col_song < len(row) else ""
            notes = ""
            if col_notes is not None and col_notes < len(row):
                notes = row[col_notes].strip()
            sheet_entries.append({
                "singer": singer,
                "song_artist": song_artist,
                "status": status,
                "notes": notes,
            })

        # Clear existing rotation and re-insert
        conn = self._store._get_conn()
        conn.execute("DELETE FROM rotation_entries")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'rotation_entries'")
        conn.commit()

        for i, entry in enumerate(sheet_entries, start=1):
            conn.execute(
                "INSERT INTO rotation_entries (singer, song_artist, status, notes, position) "
                "VALUES (?, ?, ?, ?, ?)",
                (entry["singer"], entry["song_artist"], entry["status"], entry["notes"], i),
            )
        conn.commit()

        return len(sheet_entries)

    def get_status(self):
        """Return sync status dict.

        Keys:
            last_sync: ISO timestamp of last successful sync or None.
            is_online: Whether last sync succeeded.
            next_sync_in: Seconds until next scheduled sync (approximate).
        """
        next_sync_in = None
        if self._last_sync is not None:
            elapsed = time.time() - self._last_sync
            next_sync_in = max(0, self._sync_interval - elapsed)

        return {
            "last_sync": (
                datetime.fromtimestamp(self._last_sync).strftime("%Y-%m-%d %H:%M:%S")
                if self._last_sync is not None
                else None
            ),
            "is_online": self._is_online,
            "next_sync_in": next_sync_in,
        }
