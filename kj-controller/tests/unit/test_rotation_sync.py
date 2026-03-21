"""Unit tests for SheetSync (rotation_sync.py)."""

import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest

from rotation_store import RotationStore
from rotation_sync import SheetSync, _find_header


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path):
    """Return a fresh RotationStore backed by a temp SQLite file."""
    return RotationStore(str(tmp_path / "rotation.db"))


def _sheet_values(entries):
    """Build a minimal get_all_values() response for the given entry dicts."""
    header = ["Timestamp", "Singer", "Song & Artist", "Status", "Notes"]
    rows = [header]
    for e in entries:
        rows.append([
            e.get("created_at", ""),
            e.get("singer", ""),
            e.get("song_artist", ""),
            e.get("status", "Waiting"),
            e.get("notes", ""),
        ])
    return rows


def _make_sync(store, mock_sheet):
    """Return a SheetSync wired to *mock_sheet* (bypasses gspread auth)."""
    sync = SheetSync(store, "fake-sheet-id", "/tmp/fake-creds.json", sync_interval=30)
    sync._sheet = mock_sheet  # inject mock directly
    return sync


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    s = _make_store(tmp_path)
    s.add_entry("Alice", "Bohemian Rhapsody - Queen")
    s.add_entry("Bob", "Don't Stop Believin - Journey")
    return s


@pytest.fixture
def mock_sheet(store):
    sheet = MagicMock()
    all_entries = store.get_all_entries()
    sheet.get_all_values.return_value = _sheet_values(all_entries)
    return sheet


@pytest.fixture
def sync(store, mock_sheet):
    return _make_sync(store, mock_sheet)


# ---------------------------------------------------------------------------
# _find_header tests (imported into rotation_sync)
# ---------------------------------------------------------------------------

class TestFindHeader:
    def test_finds_standard_header(self):
        rows = [
            ["Timestamp", "Singer", "Song & Artist", "Status", "Notes"],
        ]
        idx, col_map = _find_header(rows)
        assert idx == 1
        assert col_map["singer"] == 1
        assert col_map["song_artist"] == 2
        assert col_map["status"] == 3

    def test_finds_header_in_second_row(self):
        rows = [
            ["", "", "", "", ""],
            ["Timestamp", "Singer", "Song & Artist", "Status", "Notes"],
        ]
        idx, col_map = _find_header(rows)
        assert idx == 2

    def test_no_header_returns_none(self):
        rows = [["Foo", "Bar", "Baz"]]
        idx, col_map = _find_header(rows)
        assert idx is None
        assert col_map == {}


# ---------------------------------------------------------------------------
# TestSyncNow
# ---------------------------------------------------------------------------

class TestSyncNow:
    def test_pushes_entries_to_sheet(self, sync, mock_sheet):
        result = sync.sync_now()
        assert result is True
        mock_sheet.update.assert_called_once()

    def test_update_range_covers_entries(self, sync, mock_sheet):
        sync.sync_now()
        call_args = mock_sheet.update.call_args
        range_str = call_args[0][0]
        rows = call_args[0][1]
        # Header is row 1, data starts at row 2
        assert range_str.startswith("A2:")
        assert len(rows) == 2  # Alice + Bob

    def test_rows_contain_correct_data(self, sync, mock_sheet):
        sync.sync_now()
        rows = mock_sheet.update.call_args[0][1]
        # Singer col is index 1
        singers = [r[1] for r in rows]
        assert "Alice" in singers
        assert "Bob" in singers

    def test_updates_last_sheet_sync_meta(self, sync, mock_sheet, store):
        sync.sync_now()
        conn = store._get_conn()
        row = conn.execute(
            "SELECT value FROM rotation_meta WHERE key = 'last_sheet_sync'"
        ).fetchone()
        assert row is not None
        assert row[0]  # non-empty timestamp

    def test_sets_is_online_true_on_success(self, sync, mock_sheet):
        sync.sync_now()
        assert sync._is_online is True

    def test_handles_network_error_gracefully(self, sync, mock_sheet):
        mock_sheet.update.side_effect = Exception("network error")
        result = sync.sync_now()
        assert result is False
        assert sync._is_online is False

    def test_resets_connection_on_error(self, sync, mock_sheet):
        mock_sheet.update.side_effect = Exception("timeout")
        sync.sync_now()
        # Connection should be reset so next call re-authenticates
        assert sync._sheet is None
        assert sync._client is None

    def test_returns_false_when_no_header(self, sync, mock_sheet):
        mock_sheet.get_all_values.return_value = [["Foo", "Bar"]]
        result = sync.sync_now()
        assert result is False

    def test_empty_store_no_update_called(self, tmp_path):
        empty_store = _make_store(tmp_path)
        fresh_sheet = MagicMock()
        fresh_sheet.get_all_values.return_value = _sheet_values([])
        empty_sync = _make_sync(empty_store, fresh_sheet)
        empty_sync.sync_now()
        # No rows to push, update should not be called
        fresh_sheet.update.assert_not_called()


# ---------------------------------------------------------------------------
# TestRestoreFromSheet
# ---------------------------------------------------------------------------

class TestRestoreFromSheet:
    def test_imports_entries_from_sheet(self, tmp_path, mock_sheet):
        """Restore clears the store and imports from sheet data."""
        fresh_store = _make_store(tmp_path)
        # Sheet has Alice and Bob
        sheet_data = _sheet_values([
            {"singer": "Alice", "song_artist": "Bohemian Rhapsody", "status": "Waiting"},
            {"singer": "Bob", "song_artist": "My Way", "status": "Up Next"},
        ])
        mock_sheet.get_all_values.return_value = sheet_data
        sync = _make_sync(fresh_store, mock_sheet)

        count = sync.restore_from_sheet()

        assert count == 2
        entries = fresh_store.get_all_entries()
        assert len(entries) == 2
        assert entries[0]["singer"] == "Alice"
        assert entries[1]["singer"] == "Bob"

    def test_clears_existing_entries(self, store, mock_sheet):
        """Existing rotation is wiped before importing."""
        # store already has Alice + Bob; sheet has Carol only
        sheet_data = _sheet_values([
            {"singer": "Carol", "song_artist": "Sweet Caroline", "status": "Waiting"},
        ])
        mock_sheet.get_all_values.return_value = sheet_data
        sync = _make_sync(store, mock_sheet)

        count = sync.restore_from_sheet()

        assert count == 1
        entries = store.get_all_entries()
        assert len(entries) == 1
        assert entries[0]["singer"] == "Carol"

    def test_preserves_status(self, tmp_path, mock_sheet):
        fresh_store = _make_store(tmp_path)
        sheet_data = _sheet_values([
            {"singer": "Alice", "song_artist": "", "status": "Now Singing"},
            {"singer": "Bob", "song_artist": "", "status": "Done"},
        ])
        mock_sheet.get_all_values.return_value = sheet_data
        sync = _make_sync(fresh_store, mock_sheet)

        sync.restore_from_sheet()

        entries = fresh_store.get_all_entries()
        # get_all_entries includes Done entries
        statuses = {e["singer"]: e["status"] for e in entries}
        assert statuses["Alice"] == "Now Singing"
        assert statuses["Bob"] == "Done"

    def test_assigns_positions_in_order(self, tmp_path, mock_sheet):
        fresh_store = _make_store(tmp_path)
        sheet_data = _sheet_values([
            {"singer": "A", "song_artist": "", "status": "Waiting"},
            {"singer": "B", "song_artist": "", "status": "Waiting"},
            {"singer": "C", "song_artist": "", "status": "Waiting"},
        ])
        mock_sheet.get_all_values.return_value = sheet_data
        sync = _make_sync(fresh_store, mock_sheet)

        sync.restore_from_sheet()

        entries = fresh_store.get_all_entries()
        positions = [e["position"] for e in entries]
        assert positions == [1, 2, 3]

    def test_skips_empty_singer_rows(self, tmp_path, mock_sheet):
        fresh_store = _make_store(tmp_path)
        sheet_data = _sheet_values([
            {"singer": "", "song_artist": "ignored", "status": "Waiting"},
            {"singer": "Bob", "song_artist": "A Song", "status": "Waiting"},
        ])
        mock_sheet.get_all_values.return_value = sheet_data
        sync = _make_sync(fresh_store, mock_sheet)

        count = sync.restore_from_sheet()

        assert count == 1
        entries = fresh_store.get_all_entries()
        assert entries[0]["singer"] == "Bob"

    def test_returns_zero_when_no_header(self, store, mock_sheet):
        mock_sheet.get_all_values.return_value = [["Foo", "Bar"]]
        sync = _make_sync(store, mock_sheet)
        count = sync.restore_from_sheet()
        assert count == 0


# ---------------------------------------------------------------------------
# TestGetStatus
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_initial_status_is_offline(self, sync):
        status = sync.get_status()
        assert status["is_online"] is False
        assert status["last_sync"] is None
        assert status["next_sync_in"] is None

    def test_status_after_successful_sync(self, sync, mock_sheet):
        sync.sync_now()
        status = sync.get_status()
        assert status["is_online"] is True
        assert status["last_sync"] is not None
        assert status["next_sync_in"] is not None
        assert status["next_sync_in"] >= 0

    def test_status_after_failed_sync(self, sync, mock_sheet):
        mock_sheet.update.side_effect = Exception("fail")
        sync.sync_now()
        status = sync.get_status()
        assert status["is_online"] is False

    def test_next_sync_in_decreases_over_time(self, sync, mock_sheet):
        sync.sync_now()
        status1 = sync.get_status()
        time.sleep(0.05)
        status2 = sync.get_status()
        assert status2["next_sync_in"] < status1["next_sync_in"]

    def test_last_sync_is_formatted_string(self, sync, mock_sheet):
        sync.sync_now()
        status = sync.get_status()
        # Should be parseable as datetime
        from datetime import datetime
        datetime.strptime(status["last_sync"], "%Y-%m-%d %H:%M:%S")
