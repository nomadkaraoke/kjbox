"""Unit tests for RotationManager."""

import time
from unittest.mock import MagicMock, patch

import pytest

from rotation import RotationManager, _find_header


@pytest.fixture
def mock_sheet():
    """Create a mock gspread worksheet with header in row 2 (like the real sheet)."""
    sheet = MagicMock()
    sheet.get_all_values.return_value = [
        ["", "", "", "", "", ".", "URL!"],  # metadata row
        ["Timestamp", "Singer", "Song & Artist", "Status", "Round", "Notes", "Column 1"],
        ["3/5/2026 20:00:00", "Alice", "Bohemian Rhapsody - Queen", "Now Singing", "1", "", ""],
        ["3/5/2026 20:05:00", "Bob", "Don't Stop Believin - Journey", "Up Next", "1", "", ""],
        ["3/5/2026 20:10:00", "Carol", "Sweet Caroline - Neil Diamond", "", "1", "first timer", ""],
        ["3/5/2026 19:30:00", "Dave", "Piano Man - Billy Joel", "Done", "1", "", ""],
        ["3/5/2026 20:15:00", "Eve", "Total Eclipse - Bonnie Tyler", "", "1", "", ""],
    ]
    return sheet


@pytest.fixture
def manager(mock_sheet):
    """Create a RotationManager with a mocked sheet."""
    mgr = RotationManager("fake-sheet-id", "/tmp/fake-creds.json")
    mgr._sheet = mock_sheet
    return mgr


class TestFindHeader:
    def test_finds_header_in_row_2(self):
        rows = [
            ["", "", "", "", "", ".", "URL!"],
            ["Timestamp", "Singer", "Song & Artist", "Status", "Round", "Notes", "Column 1"],
        ]
        row_idx, col_map = _find_header(rows)
        assert row_idx == 2
        assert col_map["singer"] == 1
        assert col_map["song_artist"] == 2
        assert col_map["status"] == 3
        assert col_map["notes"] == 5

    def test_finds_header_in_row_1(self):
        rows = [["Timestamp", "Singer", "Song & Artist", "Status", "Notes"]]
        row_idx, col_map = _find_header(rows)
        assert row_idx == 1
        assert col_map["notes"] == 4

    def test_no_header_found(self):
        rows = [["Foo", "Bar", "Baz"]]
        row_idx, col_map = _find_header(rows)
        assert row_idx is None


class TestGetRotation:
    def test_returns_non_done_entries(self, manager, mock_sheet):
        entries = manager.get_rotation()
        assert len(entries) == 4  # Alice, Bob, Carol, Eve (not Dave who is Done)
        assert entries[0]["singer"] == "Alice"
        assert entries[0]["status"] == "Now Singing"
        assert entries[0]["row_index"] == 3  # header is row 2, data starts row 3

    def test_includes_song_artist(self, manager):
        entries = manager.get_rotation()
        assert entries[1]["song_artist"] == "Don't Stop Believin - Journey"

    def test_includes_notes(self, manager):
        entries = manager.get_rotation()
        assert entries[2]["notes"] == "first timer"

    def test_caches_results(self, manager, mock_sheet):
        manager.get_rotation()
        manager.get_rotation()
        assert mock_sheet.get_all_values.call_count == 1

    def test_force_refresh_bypasses_cache(self, manager, mock_sheet):
        manager.get_rotation()
        manager.get_rotation(force_refresh=True)
        assert mock_sheet.get_all_values.call_count == 2

    def test_cache_expires(self, manager, mock_sheet):
        manager.get_rotation()
        manager._cache_time = time.time() - 20  # expired
        manager.get_rotation()
        assert mock_sheet.get_all_values.call_count == 2

    def test_empty_sheet_no_header(self, manager, mock_sheet):
        mock_sheet.get_all_values.return_value = [["Foo", "Bar"]]
        entries = manager.get_rotation()
        assert entries == []

    def test_header_only(self, manager, mock_sheet):
        mock_sheet.get_all_values.return_value = [
            ["Timestamp", "Singer", "Song & Artist", "Status", "Notes"],
        ]
        entries = manager.get_rotation()
        assert entries == []

    def test_skips_empty_singer(self, manager, mock_sheet):
        mock_sheet.get_all_values.return_value = [
            ["Timestamp", "Singer", "Song & Artist", "Status", "Notes"],
            ["3/5/2026 20:00:00", "", "Some Song", "", ""],
            ["3/5/2026 20:05:00", "Bob", "A Song", "", ""],
        ]
        entries = manager.get_rotation()
        assert len(entries) == 1
        assert entries[0]["singer"] == "Bob"


class TestUpdateStatus:
    def test_updates_cell(self, manager, mock_sheet):
        manager.update_status(4, "Done")
        # Status column is D (index 3, so 1-based = 4)
        mock_sheet.update_cell.assert_called_once_with(4, 4, "Done")

    def test_invalidates_cache(self, manager, mock_sheet):
        manager.get_rotation()  # populate cache
        manager.update_status(4, "Done")
        assert manager._cache is None


class TestMarkSinging:
    def test_sets_singing_and_clears_others(self, manager, mock_sheet):
        manager.mark_singing(4)  # Bob is row 4
        mock_sheet.batch_update.assert_called_once()
        updates = mock_sheet.batch_update.call_args[0][0]
        # Should clear Alice (row 3, currently "Now Singing") and set Bob (row 4)
        ranges = {u["range"]: u["values"][0][0] for u in updates}
        assert ranges["D3"] == "Waiting"  # clear Alice's singing
        assert ranges["D4"] == "Now Singing"  # set Bob as singing

    def test_invalidates_cache(self, manager, mock_sheet):
        manager.get_rotation()
        manager.mark_singing(4)
        assert manager._cache is None


class TestMarkUpNext:
    def test_sets_up_next_and_clears_others(self, manager, mock_sheet):
        manager.mark_up_next(5)  # Carol is row 5
        mock_sheet.batch_update.assert_called_once()
        updates = mock_sheet.batch_update.call_args[0][0]
        ranges = {u["range"]: u["values"][0][0] for u in updates}
        assert ranges["D4"] == "Waiting"  # clear Bob's "Up Next"
        assert ranges["D5"] == "Up Next"  # set Carol

    def test_invalidates_cache(self, manager, mock_sheet):
        manager.get_rotation()
        manager.mark_up_next(5)
        assert manager._cache is None


class TestUpdateEntry:
    def test_updates_singer_and_song(self, manager, mock_sheet):
        manager.update_entry(4, singer="Bobby", song_artist="New Song - Artist")
        mock_sheet.batch_update.assert_called_once()
        updates = mock_sheet.batch_update.call_args[0][0]
        ranges = {u["range"]: u["values"][0][0] for u in updates}
        assert ranges["B4"] == "Bobby"
        assert ranges["C4"] == "New Song - Artist"

    def test_updates_singer_only(self, manager, mock_sheet):
        manager.update_entry(4, singer="Bobby")
        updates = mock_sheet.batch_update.call_args[0][0]
        assert len(updates) == 1
        assert updates[0]["range"] == "B4"

    def test_updates_song_only(self, manager, mock_sheet):
        manager.update_entry(4, song_artist="New Song")
        updates = mock_sheet.batch_update.call_args[0][0]
        assert len(updates) == 1
        assert updates[0]["range"] == "C4"

    def test_no_updates_no_batch(self, manager, mock_sheet):
        manager.update_entry(4)
        mock_sheet.batch_update.assert_not_called()

    def test_invalidates_cache(self, manager, mock_sheet):
        manager.get_rotation()
        manager.update_entry(4, singer="Bobby")
        assert manager._cache is None


class TestDeleteEntry:
    def test_deletes_row(self, manager, mock_sheet):
        manager.delete_entry(4)
        mock_sheet.delete_rows.assert_called_once_with(4)

    def test_invalidates_cache(self, manager, mock_sheet):
        manager.get_rotation()
        manager.delete_entry(4)
        assert manager._cache is None


class TestAddEntry:
    def test_appends_row(self, manager, mock_sheet):
        manager.add_entry("Frank", "My Way - Sinatra")
        mock_sheet.append_row.assert_called_once()
        row = mock_sheet.append_row.call_args[0][0]
        assert row[1] == "Frank"
        assert row[2] == "My Way - Sinatra"
        assert row[3] == "Waiting"  # default status
        assert row[0]  # has timestamp

    def test_invalidates_cache(self, manager, mock_sheet):
        manager.get_rotation()
        manager.add_entry("Frank", "My Way")
        assert manager._cache is None


class TestArchiveRotation:
    def test_archives_data_rows_to_past_events(self, manager, mock_sheet):
        archive_sheet = MagicMock()
        spreadsheet = MagicMock()
        spreadsheet.worksheet.return_value = archive_sheet
        manager._get_spreadsheet = lambda: spreadsheet

        count = manager.archive_rotation()
        # 5 data rows (Alice, Bob, Carol, Dave, Eve)
        assert count == 5
        # Should append separator + data rows
        archive_sheet.append_row.assert_called_once()
        sep = archive_sheet.append_row.call_args[0][0][0]
        assert sep.startswith("---") and "2026" in sep
        archive_sheet.append_rows.assert_called_once()
        rows = archive_sheet.append_rows.call_args[0][0]
        assert len(rows) == 5
        # Should delete data rows from rotation sheet (rows 3-7)
        mock_sheet.delete_rows.assert_called_once_with(3, 7)

    def test_creates_past_events_sheet_if_missing(self, manager, mock_sheet):
        spreadsheet = MagicMock()
        spreadsheet.worksheet.side_effect = __import__('gspread').exceptions.WorksheetNotFound
        new_sheet = MagicMock()
        spreadsheet.add_worksheet.return_value = new_sheet
        manager._get_spreadsheet = lambda: spreadsheet

        count = manager.archive_rotation()
        assert count == 5
        spreadsheet.add_worksheet.assert_called_once()
        new_sheet.append_rows.assert_called_once()

    def test_returns_zero_when_no_data(self, manager, mock_sheet):
        mock_sheet.get_all_values.return_value = [
            ["Timestamp", "Singer", "Song & Artist", "Status", "Notes"],
        ]
        count = manager.archive_rotation()
        assert count == 0

    def test_invalidates_cache(self, manager, mock_sheet):
        archive_sheet = MagicMock()
        spreadsheet = MagicMock()
        spreadsheet.worksheet.return_value = archive_sheet
        manager._get_spreadsheet = lambda: spreadsheet

        manager.get_rotation()
        manager.archive_rotation()
        assert manager._cache is None
