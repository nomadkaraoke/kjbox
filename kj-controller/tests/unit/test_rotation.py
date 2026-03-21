"""Unit tests for RotationManager (rotation.py) — coordinator layer."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from rotation import ROTATION_CACHE_FILE, RotationManager, _find_header


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mgr(tmp_path):
    """Return a RotationManager backed by a temp SQLite DB, no Sheet configured."""
    return RotationManager(str(tmp_path / "rotation.db"))


@pytest.fixture
def mgr_with_entries(mgr):
    """RotationManager pre-populated with three singers."""
    mgr.add_entry("Alice", "Bohemian Rhapsody - Queen")
    mgr.add_entry("Bob", "Don't Stop Believin - Journey")
    mgr.add_entry("Carol", "Sweet Caroline - Neil Diamond", notes="first timer")
    return mgr


# ---------------------------------------------------------------------------
# Re-export check
# ---------------------------------------------------------------------------

class TestFindHeaderReExport:
    """Ensure _find_header is still importable from rotation for existing callers."""

    def test_finds_header(self):
        rows = [["Timestamp", "Singer", "Song & Artist", "Status", "Notes"]]
        idx, col_map = _find_header(rows)
        assert idx == 1
        assert col_map["singer"] == 1
        assert col_map["status"] == 3

    def test_no_header(self):
        idx, col_map = _find_header([["Foo", "Bar"]])
        assert idx is None


# ---------------------------------------------------------------------------
# TestCoordinatorCRUD
# ---------------------------------------------------------------------------

class TestCoordinatorCRUD:
    def test_get_rotation_empty(self, mgr):
        assert mgr.get_rotation() == []

    def test_get_rotation_returns_entries(self, mgr_with_entries):
        entries = mgr_with_entries.get_rotation()
        assert len(entries) == 3
        assert entries[0]["singer"] == "Alice"

    def test_get_rotation_force_refresh_accepted(self, mgr_with_entries):
        """force_refresh=True is accepted without error (ignored internally)."""
        entries = mgr_with_entries.get_rotation(force_refresh=True)
        assert len(entries) == 3

    def test_add_entry_returns_dict(self, mgr):
        entry = mgr.add_entry("Dave", "Piano Man - Billy Joel")
        assert entry["singer"] == "Dave"
        assert entry["song_artist"] == "Piano Man - Billy Joel"
        assert entry["status"] == "Waiting"
        assert "id" in entry

    def test_add_entry_with_notes(self, mgr):
        entry = mgr.add_entry("Eve", "Total Eclipse", notes="VIP")
        assert entry["notes"] == "VIP"

    def test_update_status_basic(self, mgr_with_entries):
        entries = mgr_with_entries.get_rotation()
        alice_id = entries[0]["id"]
        mgr_with_entries.update_status(alice_id, "Done")
        updated = mgr_with_entries.store.get_entry(alice_id)
        assert updated["status"] == "Done"

    def test_update_status_not_in_queue_after_done(self, mgr_with_entries):
        entries = mgr_with_entries.get_rotation()
        alice_id = entries[0]["id"]
        mgr_with_entries.update_status(alice_id, "Done")
        queue = mgr_with_entries.get_rotation()
        singers = [e["singer"] for e in queue]
        assert "Alice" not in singers

    def test_mark_singing(self, mgr_with_entries):
        entries = mgr_with_entries.get_rotation()
        bob_id = entries[1]["id"]
        mgr_with_entries.mark_singing(bob_id)
        updated = mgr_with_entries.store.get_entry(bob_id)
        assert updated["status"] == "Now Singing"

    def test_mark_singing_clears_others(self, mgr_with_entries):
        entries = mgr_with_entries.get_rotation()
        alice_id = entries[0]["id"]
        bob_id = entries[1]["id"]
        mgr_with_entries.mark_singing(alice_id)
        mgr_with_entries.mark_singing(bob_id)
        # Alice should be back to Waiting
        assert mgr_with_entries.store.get_entry(alice_id)["status"] == "Waiting"
        assert mgr_with_entries.store.get_entry(bob_id)["status"] == "Now Singing"

    def test_mark_up_next(self, mgr_with_entries):
        entries = mgr_with_entries.get_rotation()
        carol_id = entries[2]["id"]
        mgr_with_entries.mark_up_next(carol_id)
        assert mgr_with_entries.store.get_entry(carol_id)["status"] == "Up Next"

    def test_mark_up_next_clears_others(self, mgr_with_entries):
        entries = mgr_with_entries.get_rotation()
        alice_id = entries[0]["id"]
        bob_id = entries[1]["id"]
        mgr_with_entries.mark_up_next(alice_id)
        mgr_with_entries.mark_up_next(bob_id)
        assert mgr_with_entries.store.get_entry(alice_id)["status"] == "Waiting"
        assert mgr_with_entries.store.get_entry(bob_id)["status"] == "Up Next"

    def test_update_entry_singer(self, mgr_with_entries):
        entries = mgr_with_entries.get_rotation()
        alice_id = entries[0]["id"]
        result = mgr_with_entries.update_entry(alice_id, singer="Alicia")
        assert result["singer"] == "Alicia"

    def test_update_entry_song(self, mgr_with_entries):
        entries = mgr_with_entries.get_rotation()
        bob_id = entries[1]["id"]
        result = mgr_with_entries.update_entry(bob_id, song_artist="New Song - Artist")
        assert result["song_artist"] == "New Song - Artist"

    def test_update_entry_both(self, mgr_with_entries):
        entries = mgr_with_entries.get_rotation()
        carol_id = entries[2]["id"]
        result = mgr_with_entries.update_entry(carol_id, singer="Caroline", song_artist="Different Song")
        assert result["singer"] == "Caroline"
        assert result["song_artist"] == "Different Song"

    def test_delete_entry(self, mgr_with_entries):
        entries = mgr_with_entries.get_rotation()
        alice_id = entries[0]["id"]
        mgr_with_entries.delete_entry(alice_id)
        remaining = mgr_with_entries.get_rotation()
        assert len(remaining) == 2
        assert all(e["singer"] != "Alice" for e in remaining)

    def test_move_entry(self, mgr_with_entries):
        entries = mgr_with_entries.get_rotation()
        alice_id = entries[0]["id"]
        # Move Alice from position 1 to position 3
        mgr_with_entries.move_entry(alice_id, 3)
        reordered = mgr_with_entries.get_rotation()
        assert reordered[2]["singer"] == "Alice"

    def test_archive_rotation(self, mgr_with_entries):
        count = mgr_with_entries.archive_rotation()
        assert count == 3
        # After archive, rotation has only the starter entry
        entries = mgr_with_entries.get_rotation()
        assert len(entries) == 1
        assert entries[0]["singer"] == "Andrew"

    def test_get_stats(self, mgr_with_entries):
        stats = mgr_with_entries.get_stats()
        assert stats["singers"] == 3
        assert stats["queued"] == 3
        assert stats["sung"] == 0


# ---------------------------------------------------------------------------
# TestCoordinatorLinking
# ---------------------------------------------------------------------------

class TestCoordinatorLinking:
    def test_link_file_without_media(self, mgr):
        entry = mgr.add_entry("Frank", "My Way - Sinatra")
        mgr.link_file(entry["id"], "/path/to/my-way.cdg")
        updated = mgr.store.get_entry(entry["id"])
        assert updated["file_path"] == "/path/to/my-way.cdg"
        assert updated["duration"] is None  # no media index

    def test_link_file_with_media_index(self, mgr):
        """When self.media is set, duration is looked up from media.index."""
        entry = mgr.add_entry("Frank", "My Way - Sinatra")
        file_path = "/path/to/my-way.cdg"

        media_mock = MagicMock()
        media_mock.index = {file_path: {"duration": 245}}
        mgr.media = media_mock

        mgr.link_file(entry["id"], file_path)
        updated = mgr.store.get_entry(entry["id"])
        assert updated["file_path"] == file_path
        assert updated["duration"] == 245

    def test_link_file_media_missing_from_index(self, mgr):
        """If file not in media index, duration stays None."""
        entry = mgr.add_entry("Frank", "My Way - Sinatra")
        media_mock = MagicMock()
        media_mock.index = {}  # file not indexed
        mgr.media = media_mock

        mgr.link_file(entry["id"], "/path/to/my-way.cdg")
        updated = mgr.store.get_entry(entry["id"])
        assert updated["duration"] is None

    def test_unlink_file(self, mgr):
        entry = mgr.add_entry("Grace", "Dancing Queen")
        mgr.link_file(entry["id"], "/path/to/dancing-queen.zip")
        mgr.unlink_file(entry["id"])
        updated = mgr.store.get_entry(entry["id"])
        assert updated["file_path"] is None
        assert updated["duration"] is None


# ---------------------------------------------------------------------------
# TestSyncStatus
# ---------------------------------------------------------------------------

class TestSyncStatus:
    def test_get_sync_status_no_sync(self, mgr):
        status = mgr.get_sync_status()
        assert status["is_online"] is False
        assert status["last_sync"] is None
        assert status["next_sync_in"] is None

    def test_get_sync_status_delegates_to_sync(self, tmp_path):
        mgr = RotationManager(str(tmp_path / "rotation.db"))
        mock_sync = MagicMock()
        mock_sync.get_status.return_value = {
            "last_sync": "2026-03-21 17:00:00",
            "is_online": True,
            "next_sync_in": 15.0,
        }
        mgr.sync = mock_sync
        status = mgr.get_sync_status()
        assert status["is_online"] is True
        mock_sync.get_status.assert_called_once()

    def test_restore_from_sheet_no_sync_raises(self, mgr):
        with pytest.raises(RuntimeError, match="not configured"):
            mgr.restore_from_sheet()

    def test_restore_from_sheet_delegates_to_sync(self, tmp_path):
        mgr = RotationManager(str(tmp_path / "rotation.db"))
        mock_sync = MagicMock()
        mock_sync.restore_from_sheet.return_value = 5
        mgr.sync = mock_sync
        count = mgr.restore_from_sheet()
        assert count == 5
        mock_sync.restore_from_sheet.assert_called_once()


# ---------------------------------------------------------------------------
# TestDisplayCache
# ---------------------------------------------------------------------------

class TestDisplayCache:
    def test_cache_written_after_add(self, tmp_path):
        cache_file = str(tmp_path / "rotation_cache.json")
        mgr = RotationManager(str(tmp_path / "rotation.db"))
        with patch("rotation.ROTATION_CACHE_FILE", cache_file):
            mgr.add_entry("Alice", "Bohemian Rhapsody")
        assert os.path.exists(cache_file)

    def test_cache_contains_queue(self, tmp_path):
        cache_file = str(tmp_path / "rotation_cache.json")
        mgr = RotationManager(str(tmp_path / "rotation.db"))
        with patch("rotation.ROTATION_CACHE_FILE", cache_file):
            mgr.add_entry("Alice", "Bohemian Rhapsody")
            mgr.add_entry("Bob", "My Way")
        with open(cache_file) as f:
            data = json.load(f)
        assert "queue" in data
        assert "stats" in data
        assert "updated" in data
        assert len(data["queue"]) == 2
        assert data["queue"][0]["singer"] == "Alice"

    def test_cache_queue_fields(self, tmp_path):
        cache_file = str(tmp_path / "rotation_cache.json")
        mgr = RotationManager(str(tmp_path / "rotation.db"))
        with patch("rotation.ROTATION_CACHE_FILE", cache_file):
            mgr.add_entry("Alice", "Bohemian Rhapsody")
        with open(cache_file) as f:
            data = json.load(f)
        entry = data["queue"][0]
        assert set(entry.keys()) == {"singer", "song_artist", "status"}

    def test_cache_updated_after_status_change(self, tmp_path):
        cache_file = str(tmp_path / "rotation_cache.json")
        mgr = RotationManager(str(tmp_path / "rotation.db"))
        with patch("rotation.ROTATION_CACHE_FILE", cache_file):
            entry = mgr.add_entry("Alice", "Bohemian Rhapsody")
            mgr.mark_singing(entry["id"])
        with open(cache_file) as f:
            data = json.load(f)
        assert data["queue"][0]["status"] == "Now Singing"

    def test_cache_atomic_write(self, tmp_path):
        """No .tmp file should linger after the write."""
        cache_file = str(tmp_path / "rotation_cache.json")
        mgr = RotationManager(str(tmp_path / "rotation.db"))
        with patch("rotation.ROTATION_CACHE_FILE", cache_file):
            mgr.add_entry("Alice", "Bohemian Rhapsody")
        assert not os.path.exists(cache_file + ".tmp")
        assert os.path.exists(cache_file)

    def test_cache_survives_errors(self, tmp_path):
        """_write_display_cache silently swallows errors (best-effort)."""
        mgr = RotationManager(str(tmp_path / "rotation.db"))
        # Patch ROTATION_CACHE_FILE to an unwritable path to simulate failure
        with patch("rotation.ROTATION_CACHE_FILE", "/no/such/directory/cache.json"):
            # Should not raise
            mgr._write_display_cache()
