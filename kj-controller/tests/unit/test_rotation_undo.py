"""Unit tests for server-side rotation undo/redo, revision counter, and diff.

Covers:
- RotationStore: rotation_rev counter, rotation_history stack (checkpoint/undo/
  redo/prune/clear), restore_entries created_at preservation + preserve_tracking,
  and the pure diff_entries helper.
- RotationManager: meaningful-mutation checkpoints, background mutations skipped,
  rev bumped on every mutation, undo/redo round-trips, archive clears history,
  and undo preserving a file link added after the checkpoint.
"""

import pytest

from rotation_store import RotationStore, diff_entries
from rotation import RotationManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    """File-backed RotationStore (so per-thread WAL behaviour matches prod)."""
    return RotationStore(str(tmp_path / "rotation.db"))


@pytest.fixture
def mgr(tmp_path):
    """RotationManager with no Sheet sync."""
    return RotationManager(str(tmp_path / "rotation.db"))


# ---------------------------------------------------------------------------
# Revision counter
# ---------------------------------------------------------------------------

class TestRevisionCounter:
    def test_rev_starts_at_zero(self, store):
        assert store.get_rev() == 0

    def test_bump_rev_increments_and_returns(self, store):
        assert store.bump_rev() == 1
        assert store.bump_rev() == 2
        assert store.get_rev() == 2


# ---------------------------------------------------------------------------
# History stack — store level
# ---------------------------------------------------------------------------

class TestHistoryStack:
    def test_checkpoint_pushes_undo(self, store):
        store.add_entry("Alice", "Song A")
        store.checkpoint("Add Bob")
        counts = store.history_counts()
        assert counts["undo"] == 1
        assert counts["redo"] == 0
        assert counts["undo_label"] == "Add Bob"

    def test_checkpoint_clears_redo(self, store):
        # Build an undo then redo, then a new checkpoint must clear redo.
        store.add_entry("Alice", "Song A")
        store.checkpoint("c1")
        store.add_entry("Bob", "Song B")
        store.undo()                      # now there is a redo entry
        assert store.history_counts()["redo"] == 1
        store.checkpoint("c2")            # new action invalidates redo
        assert store.history_counts()["redo"] == 0

    def test_undo_restores_previous_state(self, store):
        store.add_entry("Alice", "Song A")
        store.checkpoint("Add Bob")
        store.add_entry("Bob", "Song B")
        assert len(store.get_entries(include_done=True)) == 2

        result = store.undo()
        assert result["ok"] is True
        entries = store.get_entries(include_done=True)
        assert len(entries) == 1
        assert entries[0]["singer"] == "Alice"

    def test_undo_then_redo_round_trips(self, store):
        store.add_entry("Alice", "Song A")
        store.checkpoint("Add Bob")
        store.add_entry("Bob", "Song B")

        store.undo()
        assert len(store.get_entries(include_done=True)) == 1
        result = store.redo()
        assert result["ok"] is True
        entries = store.get_entries(include_done=True)
        assert len(entries) == 2
        assert {e["singer"] for e in entries} == {"Alice", "Bob"}

    def test_undo_on_empty_stack_is_noop(self, store):
        result = store.undo()
        assert result["ok"] is False
        assert result["reason"] == "empty"

    def test_redo_on_empty_stack_is_noop(self, store):
        result = store.redo()
        assert result["ok"] is False
        assert result["reason"] == "empty"

    def test_history_pruned_to_max(self, store):
        for i in range(RotationStore.MAX_HISTORY + 5):
            store.checkpoint(f"c{i}")
        assert store.history_counts()["undo"] == RotationStore.MAX_HISTORY

    def test_clear_history_empties_both_stacks(self, store):
        store.add_entry("Alice", "Song A")
        store.checkpoint("c1")
        store.add_entry("Bob", "Song B")
        store.undo()
        store.clear_history()
        counts = store.history_counts()
        assert counts["undo"] == 0
        assert counts["redo"] == 0


# ---------------------------------------------------------------------------
# restore_entries hardening
# ---------------------------------------------------------------------------

class TestRestoreEntriesHardening:
    def test_restore_preserves_created_at(self, store):
        snapshot = [
            {"id": 1, "singer": "Alice", "song_artist": "Song A", "status": "Done",
             "notes": "", "position": 1, "file_path": None, "duration": None,
             "created_at": "2026-05-28 21:51:20"},
        ]
        store.restore_entries(snapshot)
        assert store.get_entry(1)["created_at"] == "2026-05-28 21:51:20"

    def test_restore_defaults_created_at_when_missing(self, store):
        snapshot = [
            {"id": 1, "singer": "Alice", "song_artist": "Song A", "status": "Waiting",
             "notes": "", "position": 1},
        ]
        store.restore_entries(snapshot)
        assert store.get_entry(1)["created_at"]  # non-empty default

    def test_preserve_tracking_keeps_live_file_link(self, store):
        # Entry exists now with a file link; snapshot is an older version w/o it.
        store.add_entry("Alice", "Song A")            # id 1
        store.link_file(1, "/media/a.mp4", duration=200)

        snapshot = [
            {"id": 1, "singer": "Alice", "song_artist": "Song A NEW TITLE",
             "status": "Waiting", "notes": "", "position": 1,
             "file_path": None, "duration": None, "download_status": None},
        ]
        store.restore_entries(snapshot, preserve_tracking=True)

        entry = store.get_entry(1)
        assert entry["song_artist"] == "Song A NEW TITLE"   # human field restored
        assert entry["file_path"] == "/media/a.mp4"          # live link preserved
        assert entry["duration"] == 200

    def test_preserve_tracking_uses_snapshot_for_new_entries(self, store):
        # Entry not currently present → snapshot's tracking fields are used.
        snapshot = [
            {"id": 9, "singer": "Zed", "song_artist": "Song Z", "status": "Waiting",
             "notes": "", "position": 1, "file_path": "/media/z.mp4", "duration": 123},
        ]
        store.restore_entries(snapshot, preserve_tracking=True)
        assert store.get_entry(9)["file_path"] == "/media/z.mp4"


# ---------------------------------------------------------------------------
# diff_entries pure helper
# ---------------------------------------------------------------------------

class TestDiffEntries:
    def _e(self, id, singer, song="", status="Waiting"):
        return {"id": id, "singer": singer, "song_artist": song, "status": status,
                "notes": "", "position": id, "paid": 0}

    def test_detects_removed(self):
        current = [self._e(1, "Alice"), self._e(2, "Bob")]
        target = [self._e(1, "Alice")]
        diff = diff_entries(current, target)
        assert [e["id"] for e in diff["removed"]] == [2]
        assert diff["added"] == []

    def test_detects_added(self):
        current = [self._e(1, "Alice")]
        target = [self._e(1, "Alice"), self._e(3, "Carol")]
        diff = diff_entries(current, target)
        assert [e["id"] for e in diff["added"]] == [3]
        assert diff["removed"] == []

    def test_detects_status_change(self):
        current = [self._e(1, "Alice", status="Done")]
        target = [self._e(1, "Alice", status="Waiting")]
        diff = diff_entries(current, target)
        assert len(diff["changed"]) == 1
        assert diff["changed"][0]["id"] == 1

    def test_position_only_change_not_reported(self):
        current = [{"id": 1, "singer": "A", "song_artist": "", "status": "Waiting",
                    "notes": "", "position": 1, "paid": 0}]
        target = [{"id": 1, "singer": "A", "song_artist": "", "status": "Waiting",
                   "notes": "", "position": 5, "paid": 0}]
        diff = diff_entries(current, target)
        assert diff["changed"] == []


# ---------------------------------------------------------------------------
# Manager-level behaviour
# ---------------------------------------------------------------------------

class TestManagerUndo:
    def test_meaningful_mutation_is_undoable(self, mgr):
        mgr.add_entry("Alice", "Song A")
        mgr.add_entry("Bob", "Song B")
        assert len(mgr.store.get_entries(include_done=True)) == 2

        result = mgr.undo()
        assert result["ok"] is True
        entries = mgr.store.get_entries(include_done=True)
        assert len(entries) == 1
        assert entries[0]["singer"] == "Alice"

    def test_background_mutation_not_undoable(self, mgr):
        mgr.add_entry("Alice", "Song A")          # checkpoint #1 (empty before)
        # A background tracking update should NOT add a checkpoint.
        before = mgr.store.history_counts()["undo"]
        mgr.set_download_status(1, "youtube", "downloading", "dl-1")
        after = mgr.store.history_counts()["undo"]
        assert after == before

    def test_every_mutation_bumps_rev(self, mgr):
        r0 = mgr.store.get_rev()
        mgr.add_entry("Alice", "Song A")
        r1 = mgr.store.get_rev()
        assert r1 > r0
        mgr.set_paid(1, True)
        assert mgr.store.get_rev() > r1

    def test_undo_redo_round_trip_via_manager(self, mgr):
        mgr.add_entry("Alice", "Song A")
        mgr.add_entry("Bob", "Song B")
        mgr.undo()
        assert len(mgr.store.get_entries(include_done=True)) == 1
        mgr.redo()
        assert len(mgr.store.get_entries(include_done=True)) == 2

    def test_archive_clears_history(self, mgr):
        mgr.add_entry("Alice", "Song A")
        mgr.add_entry("Bob", "Song B")
        assert mgr.store.history_counts()["undo"] > 0
        mgr.archive_rotation()
        assert mgr.store.history_counts()["undo"] == 0
        assert mgr.store.history_counts()["redo"] == 0

    def test_undo_preserves_file_link_added_after_checkpoint(self, mgr):
        # KJ adds Alice; THEN a download completes and links a file; THEN KJ does
        # an unrelated action and undoes it — the file link must survive.
        mgr.add_entry("Alice", "Song A")              # id 1
        mgr.add_entry("Bob", "Song B")                # id 2, checkpoint captures [Alice]
        # background link on Alice after the checkpoint:
        mgr.link_file(1, "/media/a.mp4")
        # undo the "add Bob" action
        # (link_file is meaningful, so undo first reverts the link, then add)
        mgr.undo()                                     # reverts link_file
        mgr.undo()                                     # reverts add Bob
        entry = mgr.store.get_entry(1)
        assert entry is not None
        assert entry["singer"] == "Alice"
        # The live file link must survive undo (preserve_tracking) so playback
        # never breaks because of an unrelated undo.
        assert entry["file_path"] == "/media/a.mp4"

    def test_history_status_shape(self, mgr):
        mgr.add_entry("Alice", "Song A")
        status = mgr.history_status()
        assert "undo" in status and "redo" in status
        assert status["undo"] >= 1

    def test_update_statuses_is_one_checkpoint(self, mgr):
        # "Play" advances two entries at once — it must be a single undo step,
        # not two (the regression CodeRabbit flagged).
        mgr.add_entry("Alice", "Song A")   # id 1
        mgr.add_entry("Bob", "Song B")     # id 2
        before = mgr.store.history_counts()["undo"]
        mgr.update_statuses([(1, "Now Singing"), (2, "Up Next")])
        after = mgr.store.history_counts()["undo"]
        assert after == before + 1
        assert mgr.store.get_entry(1)["status"] == "Now Singing"
        assert mgr.store.get_entry(2)["status"] == "Up Next"

    def test_undo_after_advance_reverts_both(self, mgr):
        mgr.add_entry("Alice", "Song A")
        mgr.add_entry("Bob", "Song B")
        mgr.update_statuses([(1, "Now Singing"), (2, "Up Next")])
        mgr.undo()
        assert mgr.store.get_entry(1)["status"] != "Now Singing"
        assert mgr.store.get_entry(2)["status"] != "Up Next"

    def test_preview_undo_does_not_apply(self, mgr):
        mgr.add_entry("Alice", "Song A")
        mgr.add_entry("Bob", "Song B")
        preview = mgr.preview_undo()
        assert preview["ok"] is True
        assert "diff" in preview
        # Still two entries — preview applied nothing.
        assert len(mgr.store.get_entries(include_done=True)) == 2
