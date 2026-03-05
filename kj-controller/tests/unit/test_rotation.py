"""Unit tests for RotationManager."""

import time
from unittest.mock import MagicMock, patch

import pytest

from rotation import RotationManager


@pytest.fixture
def mock_sheet():
    """Create a mock gspread worksheet."""
    sheet = MagicMock()
    sheet.get_all_values.return_value = [
        ["Timestamp", "Singer", "Song & Artist", "Status", "Notes"],
        ["3/5/2026 20:00:00", "Alice", "Bohemian Rhapsody - Queen", "Singing Now", ""],
        ["3/5/2026 20:05:00", "Bob", "Don't Stop Believin - Journey", "Next", ""],
        ["3/5/2026 20:10:00", "Carol", "Sweet Caroline - Neil Diamond", "", "first timer"],
        ["3/5/2026 19:30:00", "Dave", "Piano Man - Billy Joel", "Done", ""],
        ["3/5/2026 20:15:00", "Eve", "Total Eclipse - Bonnie Tyler", "", ""],
    ]
    return sheet


@pytest.fixture
def manager(mock_sheet):
    """Create a RotationManager with a mocked sheet."""
    mgr = RotationManager("fake-sheet-id", "/tmp/fake-creds.json")
    mgr._sheet = mock_sheet
    return mgr


class TestGetRotation:
    def test_returns_non_done_entries(self, manager, mock_sheet):
        entries = manager.get_rotation()
        assert len(entries) == 4  # Alice, Bob, Carol, Eve (not Dave who is Done)
        assert entries[0]["singer"] == "Alice"
        assert entries[0]["status"] == "Singing Now"
        assert entries[0]["row_index"] == 2

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

    def test_empty_sheet(self, manager, mock_sheet):
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
        manager.update_status(3, "Done")
        mock_sheet.update_cell.assert_called_once_with(3, 4, "Done")

    def test_invalidates_cache(self, manager, mock_sheet):
        manager.get_rotation()  # populate cache
        manager.update_status(3, "Done")
        assert manager._cache is None


class TestMarkSinging:
    def test_sets_singing_and_clears_others(self, manager, mock_sheet):
        manager.mark_singing(3)
        mock_sheet.batch_update.assert_called_once()
        updates = mock_sheet.batch_update.call_args[0][0]
        # Should clear Alice (row 2, currently "Singing Now") and set Bob (row 3)
        ranges = {u["range"]: u["values"][0][0] for u in updates}
        assert ranges["D2"] == ""  # clear Alice's singing
        assert ranges["D3"] == "Singing Now"  # set Bob as singing

    def test_invalidates_cache(self, manager, mock_sheet):
        manager.get_rotation()
        manager.mark_singing(3)
        assert manager._cache is None


class TestAddEntry:
    def test_appends_row(self, manager, mock_sheet):
        manager.add_entry("Frank", "My Way - Sinatra")
        mock_sheet.append_row.assert_called_once()
        row = mock_sheet.append_row.call_args[0][0]
        assert row[1] == "Frank"
        assert row[2] == "My Way - Sinatra"
        assert row[3] == ""  # status empty
        assert row[0]  # has timestamp

    def test_invalidates_cache(self, manager, mock_sheet):
        manager.get_rotation()
        manager.add_entry("Frank", "My Way")
        assert manager._cache is None
