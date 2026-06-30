"""Unit tests for RotationStore — SQLite-backed rotation storage."""

import sqlite3

import pytest

from rotation_store import RotationStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store():
    """In-memory RotationStore for each test."""
    s = RotationStore(":memory:")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Task 1: Schema and Connection
# ---------------------------------------------------------------------------

class TestSchemaInit:
    def test_tables_created(self, store):
        conn = store._get_conn()
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "rotation_entries" in tables
        assert "rotation_meta" in tables
        assert "rotation_archive" in tables

    def test_rotation_entries_columns(self, store):
        conn = store._get_conn()
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(rotation_entries)"
        ).fetchall()}
        expected = {
            "id", "singer", "song_artist", "status", "notes",
            "position", "file_path", "duration",
            "download_source", "download_status", "download_id", "url_fallback",
            "gen_job_id", "gen_status", "paid",
            "created_at", "updated_at",
        }
        assert expected <= cols

    def test_rotation_meta_columns(self, store):
        conn = store._get_conn()
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(rotation_meta)"
        ).fetchall()}
        assert {"key", "value"} <= cols

    def test_rotation_archive_columns(self, store):
        conn = store._get_conn()
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(rotation_archive)"
        ).fetchall()}
        expected = {
            "id", "night_date", "singer", "song_artist", "status", "notes",
            "position", "file_path", "duration", "created_at",
        }
        assert expected <= cols

    def test_indexes_created(self, store):
        conn = store._get_conn()
        indexes = {row[1] for row in conn.execute(
            "SELECT type, name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        assert "idx_rotation_position" in indexes
        assert "idx_rotation_status" in indexes
        assert "idx_rotation_archive_night" in indexes

    def test_wal_mode(self, tmp_path):
        """WAL mode is set for on-disk databases (in-memory always returns 'memory')."""
        db_path = str(tmp_path / "rotation.db")
        s = RotationStore(db_path)
        mode = s._get_conn().execute("PRAGMA journal_mode").fetchone()[0]
        s.close()
        assert mode == "wal"

    def test_schema_idempotent(self, store):
        """Calling init_schema again should not raise."""
        store.init_schema()
        store.init_schema()

    def test_second_instance_same_db(self, tmp_path):
        """Two RotationStore instances on the same file both see schema."""
        db_path = str(tmp_path / "rotation.db")
        s1 = RotationStore(db_path)
        s2 = RotationStore(db_path)
        conn = s2._get_conn()
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "rotation_entries" in tables
        s1.close()
        s2.close()

    def test_concurrent_writes_from_many_threads(self, tmp_path):
        """Regression: 2026-05-01 outage — sharing one sqlite3 connection
        across threads with default isolation_level let an implicit BEGIN
        in one thread block every other writer for the busy_timeout window.
        Per-thread connections must isolate transactions so concurrent
        writers each succeed within busy_timeout.
        """
        import threading
        import time

        db_path = str(tmp_path / "rotation.db")
        s = RotationStore(db_path)
        try:
            errors = []

            def worker(idx):
                try:
                    s.add_entry(f"singer_{idx}", f"song_{idx}")
                except sqlite3.OperationalError as exc:
                    errors.append((idx, str(exc)))

            threads = [threading.Thread(target=worker, args=(i,))
                       for i in range(20)]
            t0 = time.time()
            for t in threads: t.start()
            for t in threads: t.join(timeout=15)
            elapsed = time.time() - t0
            assert not errors, (
                f"{len(errors)} concurrent writes raised, e.g. {errors[0]}")
            # 20 sequential writes against WAL should comfortably finish
            # well inside busy_timeout (10s) even on a slow disk.
            assert elapsed < 10, f"writes took {elapsed:.1f}s — likely lock contention"

            entries = s.get_all_entries()
            assert len(entries) == 20
        finally:
            s.close()


# ---------------------------------------------------------------------------
# Task 2: Add and Get Entries
# ---------------------------------------------------------------------------

class TestAddAndGetEntries:
    def test_add_returns_dict(self, store):
        entry = store.add_entry("Alice")
        assert isinstance(entry, dict)

    def test_add_has_id(self, store):
        entry = store.add_entry("Alice")
        assert "id" in entry
        assert entry["id"] is not None

    def test_add_sets_singer(self, store):
        entry = store.add_entry("Bob")
        assert entry["singer"] == "Bob"

    def test_add_sets_song_artist(self, store):
        entry = store.add_entry("Bob", song_artist="The Beatles")
        assert entry["song_artist"] == "The Beatles"

    def test_add_sets_notes(self, store):
        entry = store.add_entry("Carol", notes="birthday!")
        assert entry["notes"] == "birthday!"

    def test_position_starts_at_1(self, store):
        entry = store.add_entry("Alice")
        assert entry["position"] == 1

    def test_position_increments(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Bob")
        e3 = store.add_entry("Carol")
        assert e1["position"] == 1
        assert e2["position"] == 2
        assert e3["position"] == 3

    def test_get_entries_ordered_by_position(self, store):
        store.add_entry("Alice")
        store.add_entry("Bob")
        store.add_entry("Carol")
        entries = store.get_entries()
        assert [e["singer"] for e in entries] == ["Alice", "Bob", "Carol"]

    def test_get_entries_excludes_done_by_default(self, store):
        store.add_entry("Alice")
        done = store.add_entry("Bob")
        store.update_status(done["id"], "Done")
        entries = store.get_entries()
        singers = [e["singer"] for e in entries]
        assert "Alice" in singers
        assert "Bob" not in singers

    def test_get_entries_include_done(self, store):
        store.add_entry("Alice")
        done = store.add_entry("Bob")
        store.update_status(done["id"], "Done")
        entries = store.get_entries(include_done=True)
        singers = [e["singer"] for e in entries]
        assert "Alice" in singers
        assert "Bob" in singers

    def test_get_entry_by_id(self, store):
        added = store.add_entry("Dave")
        fetched = store.get_entry(added["id"])
        assert fetched is not None
        assert fetched["singer"] == "Dave"
        assert fetched["id"] == added["id"]

    def test_get_entry_missing_returns_none(self, store):
        assert store.get_entry(9999) is None

    def test_empty_rotation(self, store):
        assert store.get_entries() == []

    def test_default_status_is_waiting(self, store):
        entry = store.add_entry("Eve")
        assert entry["status"].lower() in ("waiting", "")

    def test_done_case_insensitive(self, store):
        e = store.add_entry("Frank")
        store.update_status(e["id"], "DONE")
        entries = store.get_entries()
        assert all(en["singer"] != "Frank" for en in entries)


# ---------------------------------------------------------------------------
# Task 3: Update, Delete, Exclusive Statuses
# ---------------------------------------------------------------------------

class TestUpdateEntry:
    def test_update_singer(self, store):
        e = store.add_entry("Alice")
        store.update_entry(e["id"], singer="Alicia")
        fetched = store.get_entry(e["id"])
        assert fetched["singer"] == "Alicia"

    def test_update_song_artist(self, store):
        e = store.add_entry("Alice", song_artist="Adele")
        store.update_entry(e["id"], song_artist="Beyoncé")
        fetched = store.get_entry(e["id"])
        assert fetched["song_artist"] == "Beyoncé"

    def test_update_partial(self, store):
        """Updating one field does not clobber the other."""
        e = store.add_entry("Alice", song_artist="Adele")
        store.update_entry(e["id"], singer="Alicia")
        fetched = store.get_entry(e["id"])
        assert fetched["song_artist"] == "Adele"

    def test_update_not_found_raises(self, store):
        with pytest.raises(ValueError):
            store.update_entry(9999, singer="Ghost")

    def test_update_returns_updated_dict(self, store):
        e = store.add_entry("Alice")
        result = store.update_entry(e["id"], singer="Alicia")
        assert isinstance(result, dict)
        assert result["singer"] == "Alicia"


class TestDeleteEntry:
    def test_delete_removes_entry(self, store):
        e = store.add_entry("Alice")
        store.delete_entry(e["id"])
        assert store.get_entry(e["id"]) is None

    def test_delete_recompacts_positions(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Bob")
        e3 = store.add_entry("Carol")
        store.delete_entry(e2["id"])
        entries = store.get_entries()
        positions = [en["position"] for en in entries]
        assert positions == [1, 2]

    def test_delete_not_found_raises(self, store):
        with pytest.raises(ValueError):
            store.delete_entry(9999)

    def test_delete_first_recompacts(self, store):
        e1 = store.add_entry("Alice")
        store.add_entry("Bob")
        store.add_entry("Carol")
        store.delete_entry(e1["id"])
        entries = store.get_entries()
        positions = [en["position"] for en in entries]
        assert positions == [1, 2]

    def test_delete_last_no_shift_needed(self, store):
        store.add_entry("Alice")
        store.add_entry("Bob")
        e3 = store.add_entry("Carol")
        store.delete_entry(e3["id"])
        entries = store.get_entries()
        assert len(entries) == 2
        assert [e["position"] for e in entries] == [1, 2]


class TestUpdateStatus:
    def test_set_waiting(self, store):
        e = store.add_entry("Alice")
        store.update_status(e["id"], "Waiting")
        assert store.get_entry(e["id"])["status"] == "Waiting"

    def test_set_done(self, store):
        e = store.add_entry("Alice")
        store.update_status(e["id"], "Done")
        assert store.get_entry(e["id"])["status"] == "Done"

    def test_now_singing_clears_others(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Bob")
        e3 = store.add_entry("Carol")
        store.update_status(e1["id"], "Now Singing")
        store.update_status(e2["id"], "Now Singing")
        # e1 should now be back to Waiting
        assert store.get_entry(e1["id"])["status"] == "Waiting"
        assert store.get_entry(e2["id"])["status"] == "Now Singing"

    def test_now_singing_clears_singing_variants(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Bob")
        store.update_status(e1["id"], "singing now")
        store.update_status(e2["id"], "Now Singing")
        assert store.get_entry(e1["id"])["status"] == "Waiting"

    def test_up_next_clears_others(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Bob")
        store.update_status(e1["id"], "Up Next")
        store.update_status(e2["id"], "Up Next")
        assert store.get_entry(e1["id"])["status"] == "Waiting"
        assert store.get_entry(e2["id"])["status"] == "Up Next"

    def test_up_next_clears_next_variant(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Bob")
        store.update_status(e1["id"], "next")
        store.update_status(e2["id"], "Up Next")
        assert store.get_entry(e1["id"])["status"] == "Waiting"

    def test_status_not_found_raises(self, store):
        with pytest.raises(ValueError):
            store.update_status(9999, "Waiting")

    def test_now_singing_does_not_clear_up_next(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Bob")
        store.update_status(e1["id"], "Up Next")
        store.update_status(e2["id"], "Now Singing")
        # Up Next on e1 should still be intact
        assert store.get_entry(e1["id"])["status"] == "Up Next"


# ---------------------------------------------------------------------------
# Task 4: Move, Stats, File Linking
# ---------------------------------------------------------------------------

class TestMoveEntry:
    def test_move_down(self, store):
        e1 = store.add_entry("Alice")   # pos 1
        e2 = store.add_entry("Bob")     # pos 2
        e3 = store.add_entry("Carol")   # pos 3
        store.move_entry(e1["id"], 3)
        entries = store.get_entries(include_done=True)
        order = [e["singer"] for e in entries]
        assert order == ["Bob", "Carol", "Alice"]

    def test_move_up(self, store):
        e1 = store.add_entry("Alice")   # pos 1
        e2 = store.add_entry("Bob")     # pos 2
        e3 = store.add_entry("Carol")   # pos 3
        store.move_entry(e3["id"], 1)
        entries = store.get_entries(include_done=True)
        order = [e["singer"] for e in entries]
        assert order == ["Carol", "Alice", "Bob"]

    def test_move_same_position_noop(self, store):
        e1 = store.add_entry("Alice")
        store.add_entry("Bob")
        store.move_entry(e1["id"], 1)
        entries = store.get_entries()
        assert entries[0]["singer"] == "Alice"

    def test_move_nonexistent_raises(self, store):
        with pytest.raises(ValueError):
            store.move_entry(9999, 1)

    def test_move_preserves_all_positions_contiguous(self, store):
        for name in ["Alice", "Bob", "Carol", "Dave"]:
            store.add_entry(name)
        entries = store.get_entries()
        e2 = next(e for e in entries if e["singer"] == "Bob")
        store.move_entry(e2["id"], 4)
        entries = store.get_entries()
        positions = sorted(e["position"] for e in entries)
        assert positions == [1, 2, 3, 4]


class TestGetStats:
    def test_empty_stats(self, store):
        stats = store.get_stats()
        assert stats["singers"] == 0
        assert stats["sung"] == 0
        assert stats["queued"] == 0

    def test_stats_with_entries(self, store):
        store.add_entry("Alice")
        store.add_entry("Alice")   # same singer, still 1 distinct
        e3 = store.add_entry("Bob")
        store.update_status(e3["id"], "Done")
        stats = store.get_stats()
        assert stats["singers"] == 2
        assert stats["sung"] == 1
        assert stats["queued"] == 2   # Alice×2 (not done)

    def test_stats_started_none_by_default(self, store):
        stats = store.get_stats()
        assert stats["started"] is None

    def test_stats_started_after_archive(self, store):
        store.archive()
        stats = store.get_stats()
        assert stats["started"] is not None


class TestGetSongsSungCounts:
    def test_empty(self, store):
        assert store.get_songs_sung_counts() == {}

    def test_no_done_entries(self, store):
        store.add_entry("Alice")
        store.add_entry("Bob")
        assert store.get_songs_sung_counts() == {}

    def test_counts_done_entries(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Bob")
        e3 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        store.update_status(e3["id"], "Done")
        store.update_status(e2["id"], "Done")
        counts = store.get_songs_sung_counts()
        assert counts["alice"] == 2
        assert counts["bob"] == 1

    def test_case_insensitive(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("alice")
        store.update_status(e1["id"], "Done")
        store.update_status(e2["id"], "Done")
        counts = store.get_songs_sung_counts()
        assert counts["alice"] == 2

    def test_excludes_non_done(self, store):
        e1 = store.add_entry("Alice")
        store.add_entry("Alice")  # still waiting
        store.update_status(e1["id"], "Done")
        counts = store.get_songs_sung_counts()
        assert counts["alice"] == 1


class TestGetLastSangTimes:
    def _backdate(self, store, entry_id, minutes):
        """Force an entry's done_at to ``minutes`` ago (device localtime)."""
        conn = store._get_conn()
        conn.execute(
            "UPDATE rotation_entries "
            "SET done_at = datetime('now', 'localtime', ?) WHERE id = ?",
            ("-{} minutes".format(minutes), entry_id),
        )
        conn.commit()

    def test_empty(self, store):
        assert store.get_last_sang_times() == {}

    def test_no_done_entries(self, store):
        store.add_entry("Alice")
        store.add_entry("Bob")
        assert store.get_last_sang_times() == {}

    def test_recent_done_is_near_zero(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        times = store.get_last_sang_times()
        assert "alice" in times
        assert 0 <= times["alice"] <= 1

    def test_reports_minutes_since_done(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        self._backdate(store, e1["id"], 45)
        assert store.get_last_sang_times()["alice"] == 45

    def test_uses_most_recent_done(self, store):
        # Two songs sung: one 90m ago, one 10m ago — report the recent one.
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        store.update_status(e2["id"], "Done")
        self._backdate(store, e1["id"], 90)
        self._backdate(store, e2["id"], 10)
        assert store.get_last_sang_times()["alice"] == 10

    def test_case_insensitive(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        self._backdate(store, e1["id"], 20)
        e2 = store.add_entry("alice")
        store.update_status(e2["id"], "Done")
        self._backdate(store, e2["id"], 5)
        assert store.get_last_sang_times()["alice"] == 5

    def test_excludes_non_done(self, store):
        e1 = store.add_entry("Alice")
        store.add_entry("Alice")  # still waiting
        store.update_status(e1["id"], "Done")
        self._backdate(store, e1["id"], 30)
        assert store.get_last_sang_times()["alice"] == 30

    def test_credits_each_singer_in_group(self, store):
        e1 = store.add_entry(None, singers=["Alice", "Bob"])
        store.update_status(e1["id"], "Done")
        self._backdate(store, e1["id"], 15)
        times = store.get_last_sang_times()
        assert times["alice"] == 15
        assert times["bob"] == 15

    def test_done_at_survives_updated_at_bump(self, store):
        # Regression: reorders/edits bump updated_at even on done rows. The
        # "last sang" time must read done_at, so it stays put after a shuffle.
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        self._backdate(store, e1["id"], 40)
        # Simulate an unrelated action touching the row (e.g. a reorder).
        conn = store._get_conn()
        conn.execute(
            "UPDATE rotation_entries "
            "SET updated_at = datetime('now', 'localtime') WHERE id = ?",
            (e1["id"],),
        )
        conn.commit()
        assert store.get_last_sang_times()["alice"] == 40

    def test_done_at_refreshes_on_resing(self, store):
        # If a singer is re-marked Done (sang again), done_at moves to now.
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        self._backdate(store, e1["id"], 60)
        store.update_status(e1["id"], "Done")  # sang again
        assert store.get_last_sang_times()["alice"] <= 1


class TestFileLink:
    def test_link_file(self, store):
        e = store.add_entry("Alice")
        store.link_file(e["id"], "/path/to/file.cdg")
        fetched = store.get_entry(e["id"])
        assert fetched["file_path"] == "/path/to/file.cdg"

    def test_link_file_with_duration(self, store):
        e = store.add_entry("Alice")
        store.link_file(e["id"], "/path/to/file.cdg", duration=210)
        fetched = store.get_entry(e["id"])
        assert fetched["duration"] == 210

    def test_link_file_without_duration(self, store):
        e = store.add_entry("Alice")
        store.link_file(e["id"], "/path/to/file.cdg")
        fetched = store.get_entry(e["id"])
        assert fetched["duration"] is None

    def test_link_file_nonexistent_raises(self, store):
        with pytest.raises(ValueError):
            store.link_file(9999, "/path/to/file.cdg")

    def test_unlink_file(self, store):
        e = store.add_entry("Alice")
        store.link_file(e["id"], "/path/to/file.cdg", duration=210)
        store.unlink_file(e["id"])
        fetched = store.get_entry(e["id"])
        assert fetched["file_path"] is None
        assert fetched["duration"] is None

    def test_unlink_file_clears_download_tracking(self, store):
        # Regression: a previously-completed download leaves
        # download_status='complete' on the entry. Without clearing it,
        # the UI hides the link button after unlink (entry shows
        # "UNLINKED" but offers no way to attach a new song).
        e = store.add_entry("Alice")
        store.set_download_status(e["id"], "youtube", "queued", download_id="dl-1")
        store.set_url_fallback(e["id"], "https://youtu.be/abc")
        store.link_file(e["id"], "/path/to/file.cdg", duration=210)
        store.set_download_status(e["id"], "youtube", "complete", download_id="dl-1")

        store.unlink_file(e["id"])

        fetched = store.get_entry(e["id"])
        assert fetched["file_path"] is None
        assert fetched["duration"] is None
        assert fetched["download_status"] is None
        assert fetched["download_id"] is None
        assert fetched["download_source"] is None
        assert fetched["url_fallback"] is None

    def test_unlink_file_nonexistent_raises(self, store):
        with pytest.raises(ValueError):
            store.unlink_file(9999)


# ---------------------------------------------------------------------------
# Task 5: Archive and get_all_entries
# ---------------------------------------------------------------------------

class TestArchive:
    def test_archive_moves_to_archive_table(self, store):
        store.add_entry("Alice")
        store.add_entry("Bob")
        count = store.archive()
        assert count == 2
        conn = store._get_conn()
        archived = conn.execute("SELECT COUNT(*) FROM rotation_archive").fetchone()[0]
        assert archived == 2

    def test_archive_clears_rotation_entries(self, store):
        store.add_entry("Alice")
        store.add_entry("Bob")
        store.archive()
        entries = store.get_all_entries()
        # Only starter entry remains
        assert len(entries) == 1

    def test_archive_creates_starter_entry(self, store):
        store.add_entry("Alice")
        store.archive(starter_singer="KJ", starter_song="Welcome Song")
        entries = store.get_all_entries()
        assert len(entries) == 1
        assert entries[0]["singer"] == "KJ"
        assert entries[0]["song_artist"] == "Welcome Song"

    def test_archive_default_starter(self, store):
        store.add_entry("Alice")
        store.archive()
        entries = store.get_all_entries()
        assert entries[0]["singer"] == "Andrew"
        assert entries[0]["song_artist"] == "First Song of the Night"

    def test_archive_empty_rotation(self, store):
        count = store.archive()
        assert count == 0
        entries = store.get_all_entries()
        # Starter entry still created
        assert len(entries) == 1

    def test_archive_sets_night_started_at(self, store):
        store.add_entry("Alice")
        store.archive()
        stats = store.get_stats()
        assert stats["started"] is not None

    def test_archive_includes_done_entries(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        store.add_entry("Bob")
        count = store.archive()
        assert count == 2

    def test_archive_night_date_set(self, store):
        store.add_entry("Alice")
        store.archive()
        conn = store._get_conn()
        row = conn.execute(
            "SELECT night_date FROM rotation_archive LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] is not None

    def test_archive_multiple_nights(self, store):
        store.add_entry("Alice")
        store.archive()
        # After first archive: rotation has [Andrew starter]
        store.add_entry("Bob")
        # Rotation now has [Andrew starter, Bob]
        count = store.archive()
        assert count == 2   # Andrew starter + Bob both archived
        conn = store._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM rotation_archive").fetchone()[0]
        # Alice from night 1, Andrew starter from night 1, Andrew starter + Bob from night 2
        assert total >= 3

    def test_archive_does_not_recycle_entry_ids(self, store):
        """Entry ids must stay globally monotonic across nights.

        Regression: archive() used to `DELETE FROM sqlite_sequence`, restarting
        ids at 1. sing_requests (same DB) keep their linked_entry_id, so a
        recycled id phantom-matched a prior night's request and attached the
        wrong singer's phone to SMS/push. Not reusing ids removes the collision
        at its source.
        """
        first = store.add_entry("Alice")
        store.archive()
        # The new night's starter entry must NOT reuse the prior id.
        after = store.get_all_entries()
        assert all(e["id"] > first["id"] for e in after)
    def test_get_all_includes_done(self, store):
        store.add_entry("Alice")
        e2 = store.add_entry("Bob")
        store.update_status(e2["id"], "Done")
        all_entries = store.get_all_entries()
        singers = [e["singer"] for e in all_entries]
        assert "Alice" in singers
        assert "Bob" in singers

    def test_get_all_ordered_by_position(self, store):
        store.add_entry("Alice")
        store.add_entry("Bob")
        store.add_entry("Carol")
        entries = store.get_all_entries()
        assert [e["singer"] for e in entries] == ["Alice", "Bob", "Carol"]

    def test_get_all_empty(self, store):
        assert store.get_all_entries() == []


# ---------------------------------------------------------------------------
# Download/Prep Tracking Columns
# ---------------------------------------------------------------------------

class TestDownloadColumns:
    def test_add_entry_has_download_fields(self, store):
        entry = store.add_entry("Alice", "Song A")
        assert entry["download_source"] is None
        assert entry["download_status"] is None
        assert entry["download_id"] is None
        assert entry["url_fallback"] is None

    def test_add_entry_with_file_path(self, store):
        """add_entry accepts optional file_path and duration for single-action add+link."""
        entry = store.add_entry("Alice", "Song A", file_path="/media/song.mp4", duration=213)
        assert entry["file_path"] == "/media/song.mp4"
        assert entry["duration"] == 213

    def test_set_download_status(self, store):
        entry = store.add_entry("Alice", "Song A")
        updated = store.set_download_status(entry["id"], source="divebar", status="queued", download_id="uuid-123")
        assert updated["download_source"] == "divebar"
        assert updated["download_status"] == "queued"
        assert updated["download_id"] == "uuid-123"

    def test_set_url_fallback(self, store):
        entry = store.add_entry("Alice", "Song A")
        updated = store.set_url_fallback(entry["id"], "https://youtube.com/watch?v=abc")
        assert updated["url_fallback"] == "https://youtube.com/watch?v=abc"

    def test_get_entry_by_download_id(self, store):
        e1 = store.add_entry("Alice", "Song A")
        store.set_download_status(e1["id"], source="divebar", status="queued", download_id="uuid-123")
        found = store.get_entry_by_download_id("uuid-123")
        assert found is not None
        assert found["singer"] == "Alice"

    def test_get_entry_by_download_id_missing(self, store):
        assert store.get_entry_by_download_id("nonexistent") is None


# ---------------------------------------------------------------------------
# Gen Job Tracking Columns
# ---------------------------------------------------------------------------

class TestGenColumns:
    def test_add_entry_has_gen_fields(self, store):
        entry = store.add_entry("Alice", "Song A")
        assert entry["gen_job_id"] is None
        assert entry["gen_status"] is None

    def test_set_gen_status(self, store):
        entry = store.add_entry("Alice", "Song A")
        updated = store.set_gen_status(entry["id"], job_id="job-123", status="processing")
        assert updated["gen_job_id"] == "job-123"
        assert updated["gen_status"] == "processing"

    def test_update_gen_status(self, store):
        entry = store.add_entry("Alice", "Song A")
        store.set_gen_status(entry["id"], job_id="job-123", status="processing")
        updated = store.set_gen_status(entry["id"], job_id="job-123", status="awaiting_review")
        assert updated["gen_status"] == "awaiting_review"

    def test_set_gen_status_not_found_raises(self, store):
        with pytest.raises(ValueError):
            store.set_gen_status(9999, job_id="job-123", status="processing")

    def test_get_active_gen_entries(self, store):
        e1 = store.add_entry("Alice", "Song A")
        e2 = store.add_entry("Bob", "Song B")
        e3 = store.add_entry("Carol", "Song C")
        store.set_gen_status(e1["id"], "job-1", "processing")
        store.set_gen_status(e2["id"], "job-2", "complete")  # terminal
        store.set_gen_status(e3["id"], "job-3", "awaiting_review")
        active = store.get_active_gen_entries()
        assert len(active) == 2
        job_ids = {e["gen_job_id"] for e in active}
        assert job_ids == {"job-1", "job-3"}

    def test_get_active_gen_entries_empty(self, store):
        store.add_entry("Alice", "Song A")
        assert store.get_active_gen_entries() == []

    def test_get_entry_by_gen_job_id(self, store):
        e = store.add_entry("Alice", "Song A")
        store.set_gen_status(e["id"], "job-123", "processing")
        found = store.get_entry_by_gen_job_id("job-123")
        assert found is not None
        assert found["singer"] == "Alice"

    def test_get_entry_by_gen_job_id_missing(self, store):
        assert store.get_entry_by_gen_job_id("nonexistent") is None


class TestRestoreEntries:
    """Tests for restore_entries() — atomic snapshot restore."""

    def test_restore_replaces_all_entries(self, store):
        """Restoring a snapshot replaces all current entries."""
        store.add_entry("Alice", "Song A")
        store.add_entry("Bob", "Song B")

        snapshot = [
            {"id": 10, "singer": "Xavier", "song_artist": "Song X", "status": "Waiting",
             "notes": "", "position": 1, "file_path": None, "duration": None,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None},
            {"id": 11, "singer": "Yolanda", "song_artist": "Song Y", "status": "Now Singing",
             "notes": "", "position": 2, "file_path": "/path/y.mp4", "duration": 180,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None},
        ]
        store.restore_entries(snapshot)

        entries = store.get_entries(include_done=True)
        assert len(entries) == 2
        assert entries[0]["id"] == 10
        assert entries[0]["singer"] == "Xavier"
        assert entries[1]["id"] == 11
        assert entries[1]["singer"] == "Yolanda"
        assert entries[1]["status"] == "Now Singing"
        assert entries[1]["file_path"] == "/path/y.mp4"
        assert entries[1]["duration"] == 180

    def test_restore_preserves_original_ids(self, store):
        """Restored entries keep their original IDs, not new autoincrement values."""
        snapshot = [
            {"id": 42, "singer": "Zara", "song_artist": "Song Z", "status": "Waiting",
             "notes": "test", "position": 1, "file_path": None, "duration": None,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None},
        ]
        store.restore_entries(snapshot)

        entry = store.get_entry(42)
        assert entry is not None
        assert entry["singer"] == "Zara"
        assert entry["notes"] == "test"

    def test_restore_preserves_done_at(self, store):
        """Restored Done entries keep done_at so last-sang survives undo/redo."""
        snapshot = [
            {"id": 7, "singer": "Alice", "song_artist": "Song A", "status": "Done",
             "notes": "", "position": 1, "file_path": None, "duration": None,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None,
             "done_at": "2026-06-25 21:00:00"},
        ]
        store.restore_entries(snapshot)
        entry = store.get_entry(7)
        assert entry["done_at"] == "2026-06-25 21:00:00"

    def test_restore_to_empty(self, store):
        """Restoring an empty snapshot clears the rotation."""
        store.add_entry("Alice", "Song A")
        store.add_entry("Bob", "Song B")

        store.restore_entries([])

        entries = store.get_entries(include_done=True)
        assert len(entries) == 0

    def test_restore_fewer_entries(self, store):
        """Restoring fewer entries than current removes the extras."""
        store.add_entry("Alice", "Song A")
        store.add_entry("Bob", "Song B")
        store.add_entry("Carol", "Song C")

        snapshot = [
            {"id": 1, "singer": "Alice", "song_artist": "Song A", "status": "Done",
             "notes": "", "position": 1, "file_path": None, "duration": None,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None},
        ]
        store.restore_entries(snapshot)

        entries = store.get_entries(include_done=True)
        assert len(entries) == 1
        assert entries[0]["singer"] == "Alice"
        assert entries[0]["status"] == "Done"

    def test_restore_more_entries(self, store):
        """Restoring more entries than current adds the new ones."""
        store.add_entry("Alice", "Song A")

        snapshot = [
            {"id": 1, "singer": "Alice", "song_artist": "Song A", "status": "Waiting",
             "notes": "", "position": 1, "file_path": None, "duration": None,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None},
            {"id": 2, "singer": "Bob", "song_artist": "Song B", "status": "Up Next",
             "notes": "", "position": 2, "file_path": None, "duration": None,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None},
            {"id": 3, "singer": "Carol", "song_artist": "Song C", "status": "Waiting",
             "notes": "", "position": 3, "file_path": None, "duration": None,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None},
        ]
        store.restore_entries(snapshot)

        entries = store.get_entries(include_done=True)
        assert len(entries) == 3

    def test_restore_preserves_download_and_gen_fields(self, store):
        """All download/gen tracking fields survive a restore."""
        snapshot = [
            {"id": 5, "singer": "Dan", "song_artist": "Song D", "status": "Waiting",
             "notes": "", "position": 1, "file_path": "/path/d.mp4", "duration": 200,
             "download_source": "youtube", "download_status": "complete",
             "download_id": "dl-123", "url_fallback": "https://example.com/d",
             "gen_job_id": "gen-456", "gen_status": "complete"},
        ]
        store.restore_entries(snapshot)

        entry = store.get_entry(5)
        assert entry["download_source"] == "youtube"
        assert entry["download_status"] == "complete"
        assert entry["download_id"] == "dl-123"
        assert entry["url_fallback"] == "https://example.com/d"
        assert entry["gen_job_id"] == "gen-456"
        assert entry["gen_status"] == "complete"


# ---------------------------------------------------------------------------
# Paid flag
# ---------------------------------------------------------------------------

class TestSetPaid:
    def test_set_paid_true(self, store):
        entry = store.add_entry("Alice", "Song A")
        store.set_paid(entry["id"], True)
        updated = store.get_entry(entry["id"])
        assert updated["paid"] == 1

    def test_set_paid_false(self, store):
        entry = store.add_entry("Alice", "Song A")
        store.set_paid(entry["id"], True)
        store.set_paid(entry["id"], False)
        updated = store.get_entry(entry["id"])
        assert updated["paid"] == 0

    def test_set_paid_default_is_zero(self, store):
        entry = store.add_entry("Alice", "Song A")
        assert entry["paid"] == 0


# ---------------------------------------------------------------------------
# Task 1 (new): Left status exclusion + get_singer_stats
# ---------------------------------------------------------------------------

class TestLeftStatus:
    def test_get_entries_excludes_left(self, store):
        store.add_entry("Alice")
        e2 = store.add_entry("Bob")
        store.update_status(e2["id"], "Left")
        entries = store.get_entries()
        assert len(entries) == 1
        assert entries[0]["singer"] == "Alice"

    def test_get_entries_include_done_includes_left(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Bob")
        store.update_status(e1["id"], "Done")
        store.update_status(e2["id"], "Left")
        entries = store.get_entries(include_done=True)
        assert len(entries) == 2


class TestGetSingerStats:
    def test_basic_stats(self, store):
        store.add_entry("Alice", song_artist="Song 1")
        store.add_entry("Alice", song_artist="Song 2")
        store.add_entry("Bob", song_artist="Song 3")
        stats = store.get_singer_stats()
        assert len(stats) == 2
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["entries_total"] == 2
        assert alice["entries_waiting"] == 2
        assert alice["entries_sung"] == 0
        assert alice["status"] == "active"

    def test_multi_singer_entry_credits_both(self, store):
        store.add_entry("ignored", singers=["Phil", "Anya"])
        stats = store.get_singer_stats()
        names = [s["name"].lower() for s in stats]
        assert "phil" in names
        assert "anya" in names

    def test_done_entries_counted(self, store):
        e1 = store.add_entry("Alice", song_artist="Song 1")
        store.add_entry("Alice", song_artist="Song 2")
        store.update_status(e1["id"], "Done")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["entries_sung"] == 1
        assert alice["entries_waiting"] == 1
        assert alice["entries_total"] == 2

    def test_left_singer_status(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Left")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "left"
        assert alice["entries_left"] == 1

    def test_brb_singer_status(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "On Hold (BRB)")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "brb"

    def test_sorted_by_first_added(self, store):
        store.add_entry("Bob")
        store.add_entry("Alice")
        stats = store.get_singer_stats()
        assert stats[0]["name"].lower() == "bob"

    def test_has_tipped(self, store):
        e1 = store.add_entry("Alice")
        store.set_paid(e1["id"], True)
        store.add_entry("Alice")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["has_tipped"] is True

    def test_all_done_status(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "done"

    def test_done_singer_forced_left_by_meta(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        store.mark_singer_left("Alice")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "left"

    def test_active_singer_forced_left_by_meta(self, store):
        store.add_entry("Alice")
        store.mark_singer_left("Alice")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "left"

    def test_unmarked_done_singer_stays_done(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        store.mark_singer_left("Alice")
        store.unmark_singer_left("Alice")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "done"

    def test_left_meta_case_insensitive_match(self, store):
        store.add_entry("Kai")
        store.mark_singer_left("kai")  # lowercase mark
        stats = store.get_singer_stats()
        kai = next(s for s in stats if s["name"].lower() == "kai")
        assert kai["status"] == "left"

    def test_entries_projection_included(self, store):
        e1 = store.add_entry("Alice", song_artist="Song A")
        e2 = store.add_entry("Alice", song_artist="Song B")
        store.update_status(e1["id"], "Done")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert "entries" in alice
        assert len(alice["entries"]) == 2
        fields = set(alice["entries"][0].keys())
        assert fields == {"id", "song_artist", "status", "position", "created_at"}
        # Ordered by created_at (earliest first, same as stat ordering)
        assert alice["entries"][0]["song_artist"] == "Song A"
        assert alice["entries"][0]["status"] == "Done"
        assert alice["entries"][1]["song_artist"] == "Song B"
        assert alice["entries"][1]["status"] == "Waiting"

    def test_entries_projection_excludes_heavy_fields(self, store):
        e1 = store.add_entry("Alice", song_artist="Song A", file_path="/some/path.mp3")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        entry = alice["entries"][0]
        assert "file_path" not in entry
        assert "download_source" not in entry
        assert "singers_json" not in entry

    def test_multi_singer_entry_appears_under_each_singer(self, store):
        e1 = store.add_entry("ignored", singers=["Phil", "Anya"], song_artist="Duet")
        stats = store.get_singer_stats()
        phil = next(s for s in stats if s["name"].lower() == "phil")
        anya = next(s for s in stats if s["name"].lower() == "anya")
        assert phil["entries"][0]["song_artist"] == "Duet"
        assert anya["entries"][0]["song_artist"] == "Duet"
        # Same underlying entry id
        assert phil["entries"][0]["id"] == anya["entries"][0]["id"]


# ---------------------------------------------------------------------------
# Task 2 (new): Singer action methods
# ---------------------------------------------------------------------------

class TestSingerActions:
    def test_rename_singer(self, store):
        store.add_entry("Phill", song_artist="Song 1")
        store.add_entry("Phill", song_artist="Song 2")
        store.add_entry("Bob", song_artist="Song 3")
        store.rename_singer("Phill", "Phil")
        entries = store.get_entries(include_done=True)
        phill_entries = [e for e in entries if "Phill" in e["singer"]]
        phil_entries = [e for e in entries if "Phil" in e["singer"] and "Phill" not in e["singer"]]
        assert len(phill_entries) == 0
        assert len(phil_entries) == 2

    def test_rename_singer_in_multi_singer_entry(self, store):
        store.add_entry("ignored", singers=["Phill", "Anya"])
        store.rename_singer("Phill", "Phil")
        entry = store.get_entries(include_done=True)[0]
        import json
        names = json.loads(entry["singers_json"])
        assert "Phil" in names
        assert "Phill" not in names
        assert entry["singer"] == "Phil & Anya"

    def test_merge_singers(self, store):
        store.add_entry("Phill")
        store.add_entry("Phil")
        store.merge_singers("Phill", "Phil")
        entries = store.get_entries(include_done=True)
        assert all(e["singer"] == "Phil" for e in entries)

    def test_merge_deduplicates_in_multi_singer(self, store):
        store.add_entry("ignored", singers=["Phill", "Phil"])
        store.merge_singers("Phill", "Phil")
        entry = store.get_entries(include_done=True)[0]
        import json
        names = json.loads(entry["singers_json"])
        assert names == ["Phil"]
        assert entry["singer"] == "Phil"

    def test_set_singer_status_brb(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Alice")
        e3 = store.add_entry("Bob")
        store.set_singer_status("Alice", "On Hold (BRB)")
        alice_entries = [e for e in store.get_entries(include_done=True) if e["singer"] == "Alice"]
        assert all(e["status"] == "On Hold (BRB)" for e in alice_entries)
        bob = store.get_entry(e3["id"])
        assert bob["status"] == "Waiting"

    def test_set_singer_status_left(self, store):
        e1 = store.add_entry("Alice")
        store.set_singer_status("Alice", "Left")
        entry = store.get_entry(e1["id"])
        assert entry["status"] == "Left"

    def test_set_singer_status_skips_done(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        store.set_singer_status("Alice", "Left")
        done_entry = store.get_entry(e1["id"])
        left_entry = store.get_entry(e2["id"])
        assert done_entry["status"] == "Done"
        assert left_entry["status"] == "Left"

    def test_set_singer_status_restores_left_to_waiting(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Left")
        store.set_singer_status("Alice", "Waiting")
        entry = store.get_entry(e1["id"])
        assert entry["status"] == "Waiting"

    def test_set_paid_nonexistent_entry(self, store):
        with pytest.raises(ValueError, match="not found"):
            store.set_paid(9999, True)


class TestSingersJson:
    def test_schema_has_singers_json_column(self, store):
        conn = store._get_conn()
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(rotation_entries)"
        ).fetchall()}
        assert "singers_json" in cols

    def test_add_entry_with_singers_list(self, store):
        entry = store.add_entry("ignored", singers=["Phil", "Anya"])
        assert entry["singer"] == "Phil & Anya"
        assert entry["singers_json"] == '["Phil", "Anya"]'

    def test_add_entry_with_single_singer_list(self, store):
        entry = store.add_entry("ignored", singers=["Sarah"])
        assert entry["singer"] == "Sarah"
        assert entry["singers_json"] == '["Sarah"]'

    def test_add_entry_without_singers_has_null_singers_json(self, store):
        entry = store.add_entry("Sarah")
        assert entry["singer"] == "Sarah"
        assert entry["singers_json"] is None

    def test_add_entry_singers_trims_whitespace(self, store):
        entry = store.add_entry("ignored", singers=["  Phil  ", " Anya "])
        assert entry["singer"] == "Phil & Anya"
        import json
        assert json.loads(entry["singers_json"]) == ["Phil", "Anya"]

    def test_update_entry_with_singers(self, store):
        entry = store.add_entry("Phil", singers=["Phil"])
        updated = store.update_entry(entry["id"], singers=["Phil", "Anya"])
        assert updated["singer"] == "Phil & Anya"
        assert updated["singers_json"] == '["Phil", "Anya"]'

    def test_update_entry_without_singers_preserves_singers_json(self, store):
        entry = store.add_entry("ignored", singers=["Phil", "Anya"])
        updated = store.update_entry(entry["id"], song_artist="New Song")
        assert updated["singers_json"] == '["Phil", "Anya"]'
        assert updated["singer"] == "Phil & Anya"

    def test_get_songs_sung_counts_unpacks_json(self, store):
        e = store.add_entry("ignored", song_artist="Duet Song", singers=["Phil", "Anya"])
        store.update_status(e["id"], "Done")
        counts = store.get_songs_sung_counts()
        assert counts["phil"] == 1
        assert counts["anya"] == 1

    def test_get_songs_sung_counts_mixed_legacy_and_json(self, store):
        e1 = store.add_entry("Phil")
        store.update_status(e1["id"], "Done")
        e2 = store.add_entry("ignored", singers=["Phil", "Anya"])
        store.update_status(e2["id"], "Done")
        counts = store.get_songs_sung_counts()
        assert counts["phil"] == 2
        assert counts["anya"] == 1

    def test_get_songs_sung_counts_ignores_non_done(self, store):
        store.add_entry("ignored", singers=["Phil", "Anya"])
        e2 = store.add_entry("ignored", singers=["Phil"])
        store.update_status(e2["id"], "Done")
        counts = store.get_songs_sung_counts()
        assert counts["phil"] == 1
        assert "anya" not in counts

    def test_restore_entries_preserves_singers_json(self, store):
        e = store.add_entry("ignored", singers=["Phil", "Anya"])
        snapshot = store.get_all_entries()
        store.add_entry("Extra")
        store.restore_entries(snapshot)
        entries = store.get_all_entries()
        assert len(entries) == 1
        assert entries[0]["singers_json"] == '["Phil", "Anya"]'
        assert entries[0]["singer"] == "Phil & Anya"


class TestLeftSingersMeta:
    def test_empty_by_default(self, store):
        assert store.get_left_singer_names() == set()

    def test_mark_adds_lowercased(self, store):
        store.mark_singer_left("Kai")
        assert store.get_left_singer_names() == {"kai"}

    def test_mark_strips_whitespace(self, store):
        store.mark_singer_left("  Kai  ")
        assert store.get_left_singer_names() == {"kai"}

    def test_mark_is_idempotent(self, store):
        store.mark_singer_left("Kai")
        store.mark_singer_left("kai")
        store.mark_singer_left("KAI")
        assert store.get_left_singer_names() == {"kai"}

    def test_mark_multiple_names(self, store):
        store.mark_singer_left("Kai")
        store.mark_singer_left("Anya")
        assert store.get_left_singer_names() == {"kai", "anya"}

    def test_unmark_removes(self, store):
        store.mark_singer_left("Kai")
        store.mark_singer_left("Anya")
        store.unmark_singer_left("Kai")
        assert store.get_left_singer_names() == {"anya"}

    def test_unmark_is_idempotent(self, store):
        store.unmark_singer_left("Kai")  # no-op
        assert store.get_left_singer_names() == set()
        store.mark_singer_left("Kai")
        store.unmark_singer_left("Kai")
        store.unmark_singer_left("Kai")  # second unmark no-op
        assert store.get_left_singer_names() == set()

    def test_unmark_case_insensitive(self, store):
        store.mark_singer_left("Kai")
        store.unmark_singer_left("KAI")
        assert store.get_left_singer_names() == set()

    def test_persisted_across_get(self, store):
        store.mark_singer_left("Kai")
        # Second call reads from DB, not cached state
        assert store.get_left_singer_names() == {"kai"}
        assert store.get_left_singer_names() == {"kai"}

    def test_rename_migrates_left_set(self, store):
        store.add_entry("Kai")
        store.mark_singer_left("Kai")
        store.rename_singer("Kai", "Kai P")
        assert store.get_left_singer_names() == {"kai p"}

    def test_rename_unlisted_singer_no_effect(self, store):
        store.add_entry("Kai")
        store.add_entry("Anya")
        store.mark_singer_left("Anya")
        store.rename_singer("Kai", "Kai P")
        assert store.get_left_singer_names() == {"anya"}

    def test_merge_drops_source_from_left_set(self, store):
        store.add_entry("Kai")
        store.add_entry("Kai P")
        store.mark_singer_left("Kai")
        store.merge_singers("Kai", "Kai P")
        assert store.get_left_singer_names() == set()

    def test_merge_preserves_target_in_left_set(self, store):
        store.add_entry("Kai")
        store.add_entry("Kai P")
        store.mark_singer_left("Kai P")
        store.merge_singers("Kai", "Kai P")
        assert store.get_left_singer_names() == {"kai p"}

    def test_archive_clears_left_set(self, store):
        store.add_entry("Kai")
        store.mark_singer_left("Kai")
        store.archive()
        assert store.get_left_singer_names() == set()


class TestSplitSinger:
    def test_split_single_singer_entry(self, store):
        e1 = store.add_entry("Kai", song_artist="Song A")
        e2 = store.add_entry("Kai", song_artist="Song B")
        store.split_singer("Kai", "Kai P", [e1["id"]])
        updated = store.get_entry(e1["id"])
        assert updated["singer"] == "Kai P"
        assert updated["singers_json"] is None
        unchanged = store.get_entry(e2["id"])
        assert unchanged["singer"] == "Kai"

    def test_split_multi_singer_replaces_within_array(self, store):
        e1 = store.add_entry("ignored", singers=["Kai", "Anya"])
        store.split_singer("Kai", "Kai P", [e1["id"]])
        updated = store.get_entry(e1["id"])
        import json as _j
        names = _j.loads(updated["singers_json"])
        assert names == ["Kai P", "Anya"]
        assert updated["singer"] == "Kai P & Anya"

    def test_split_multi_to_single_clears_singers_json(self, store):
        # Entry with just one name in singers_json should collapse to legacy shape
        e1 = store.add_entry("Kai", singers=["Kai"])
        store.split_singer("Kai", "Kai P", [e1["id"]])
        updated = store.get_entry(e1["id"])
        assert updated["singer"] == "Kai P"
        assert updated["singers_json"] is None

    def test_split_case_insensitive_source_match(self, store):
        e1 = store.add_entry("Kai", song_artist="Song A")
        store.split_singer("kai", "Kai P", [e1["id"]])
        updated = store.get_entry(e1["id"])
        assert updated["singer"] == "Kai P"

    def test_split_skips_entries_without_source(self, store):
        e1 = store.add_entry("Kai", song_artist="Song A")
        e2 = store.add_entry("Anya", song_artist="Song B")
        store.split_singer("Kai", "Kai P", [e1["id"], e2["id"]])
        assert store.get_entry(e1["id"])["singer"] == "Kai P"
        # e2 untouched because source name wasn't present
        assert store.get_entry(e2["id"])["singer"] == "Anya"

    def test_split_nonexistent_entry_raises(self, store):
        with pytest.raises(ValueError, match="not found"):
            store.split_singer("Kai", "Kai P", [9999])

    def test_split_empty_entry_ids_noop(self, store):
        e1 = store.add_entry("Kai")
        store.split_singer("Kai", "Kai P", [])
        assert store.get_entry(e1["id"])["singer"] == "Kai"

    def test_split_updates_updated_at(self, store):
        e1 = store.add_entry("Kai")
        original_updated = store.get_entry(e1["id"])["updated_at"]
        # Bump time deterministically by forcing a second write
        import time as _t
        _t.sleep(1.01)
        store.split_singer("Kai", "Kai P", [e1["id"]])
        new_updated = store.get_entry(e1["id"])["updated_at"]
        assert new_updated != original_updated

    def test_split_preserves_other_entry_fields(self, store):
        e1 = store.add_entry("Kai", song_artist="Song A", notes="vip")
        store.update_status(e1["id"], "Done")
        store.split_singer("Kai", "Kai P", [e1["id"]])
        updated = store.get_entry(e1["id"])
        assert updated["song_artist"] == "Song A"
        assert updated["notes"] == "vip"
        assert updated["status"] == "Done"


# ---------------------------------------------------------------------------
# Playability Warning Column (tier-2 async render verification)
# ---------------------------------------------------------------------------

class TestPlayabilityWarning:
    def test_add_entry_has_no_warning_by_default(self, store):
        entry = store.add_entry("Alice", "Song A")
        assert entry["playability_warning"] is None

    def test_set_playability_warning(self, store):
        entry = store.add_entry("Alice", "Song A")
        updated = store.set_playability_warning(
            entry["id"], "mpv: no video frame rendered"
        )
        assert updated["playability_warning"] == "mpv: no video frame rendered"
        # Survives a re-read.
        assert store.get_entry(entry["id"])["playability_warning"] == \
            "mpv: no video frame rendered"

    def test_clear_playability_warning(self, store):
        entry = store.add_entry("Alice", "Song A")
        store.set_playability_warning(entry["id"], "transient")
        cleared = store.set_playability_warning(entry["id"], None)
        assert cleared["playability_warning"] is None

    def test_set_playability_warning_not_found_raises(self, store):
        with pytest.raises(ValueError):
            store.set_playability_warning(9999, "boom")

    def test_warning_surfaces_through_get_rotation(self, store):
        """The column flows through the list path every endpoint uses, so the
        frontend ⚠️ badge sees it without any extra decoration."""
        entry = store.add_entry("Alice", "Song A")
        store.set_playability_warning(entry["id"], "mpv: no video frame rendered")
        rows = store.get_entries()
        match = next(r for r in rows if r["id"] == entry["id"])
        assert match["playability_warning"] == "mpv: no video frame rendered"

    def test_warning_survives_restore_entries(self, store):
        """Undo/redo (restore_entries) must not drop the tier-2 warning — it is
        tied to the linked file, so it has to round-trip through a restore."""
        entry = store.add_entry("Alice", "Song A")
        store.set_playability_warning(entry["id"], "mpv: no video frame rendered")
        snapshot = store.get_entries()  # snapshot carries the warning
        store.set_playability_warning(entry["id"], None)  # diverge live state
        store.restore_entries(snapshot)  # full restore → snapshot value
        assert store.get_entry(entry["id"])["playability_warning"] == \
            "mpv: no video frame rendered"

    def test_warning_preserved_live_through_undo(self, store):
        """With preserve_tracking=True (an undo of human edits), the *live*
        warning is kept — it tracks the live file_path, not the snapshot."""
        entry = store.add_entry("Alice", "Song A")
        snapshot = store.get_entries()  # no warning yet
        store.set_playability_warning(entry["id"], "live warning")
        store.restore_entries(snapshot, preserve_tracking=True)
        assert store.get_entry(entry["id"])["playability_warning"] == "live warning"

    def test_set_playability_warning_does_not_bump_updated_at(self, store):
        """A background tier-2 stamp must NOT touch updated_at — otherwise a
        done singer's "time since last sang" (derived from done_at/updated_at)
        would reset every time the warning is written."""
        entry = store.add_entry("Alice", "Song A")
        # Force a known, far-past updated_at sentinel.
        conn = store._get_conn()
        conn.execute(
            "UPDATE rotation_entries SET updated_at = ? WHERE id = ?",
            ("2000-01-01 00:00:00", entry["id"]),
        )
        conn.commit()
        store.set_playability_warning(entry["id"], "mpv: no video frame rendered")
        assert store.get_entry(entry["id"])["updated_at"] == "2000-01-01 00:00:00"
