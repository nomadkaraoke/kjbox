# Rotation SQLite Offline-First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Google Sheets as the rotation source of truth with a local SQLite database, enabling fully offline karaoke shows with optional Sheet backup.

**Architecture:** Three-layer design — `RotationStore` (pure SQLite CRUD), `SheetSync` (background push/restore), and `RotationManager` (thin coordinator). Frontend switches from fragile sheet `row_index` to stable SQLite `id`. New `file_path`/`duration` fields enable catalog linking and time estimates.

**Tech Stack:** Python 3, SQLite (WAL mode), Flask, gspread (optional sync), vanilla JS

**Spec:** `docs/superpowers/specs/2026-03-21-rotation-sqlite-offline-first-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `kj-controller/rotation_store.py` | SQLite CRUD, schema init, position management, archive |
| Create | `kj-controller/rotation_sync.py` | Background thread pushing SQLite state to Google Sheet, restore |
| Modify | `kj-controller/rotation.py` | Rewrite as thin coordinator delegating to store + sync |
| Modify | `kj-controller/app.py:23-29` | Update `_init_rotation` to pass db_path and optional sheet config |
| Modify | `kj-controller/routes.py:1402-1562` | `row_index` → `id`, add `/rotation/link`, `/rotation/unlink`, `/rotation/sync-status`, `/rotation/restore` |
| Modify | `kj-controller/static/app.js:2320-2822` | `row_index` → `id`, add link/play/duration/time-estimate UI, sync indicator |
| Modify | `kj-controller/templates/index.html:55-71` | Add sync indicator dot, restore button in rotation header |
| Modify | `kj-controller/static/style.css` | Duration badge, time estimate, sync indicator, link/play button styles |
| Modify | `desktop/rotation_data.py` | Remove Sheet CSV fallback, keep local cache reader only |
| Create | `kj-controller/tests/unit/test_rotation_store.py` | Store unit tests (in-memory SQLite) |
| Create | `kj-controller/tests/unit/test_rotation_sync.py` | Sync unit tests (mocked gspread) |
| Modify | `kj-controller/tests/unit/test_rotation.py` | Rewrite for new coordinator interface |
| Modify | `kj-controller/tests/integration/test_rotation_routes.py` | `row_index` → `id`, add new endpoint tests |
| Modify | `kj-controller/tests/conftest.py` | Add `rotation_db_path` to `mock_config` |

---

## Task 1: RotationStore — Schema and Connection

**Files:**
- Create: `kj-controller/rotation_store.py`
- Create: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write schema and connection tests**

```python
# kj-controller/tests/unit/test_rotation_store.py
"""Unit tests for RotationStore (SQLite rotation storage)."""

import sqlite3
import pytest
from rotation_store import RotationStore


@pytest.fixture
def store():
    """In-memory RotationStore for testing."""
    return RotationStore(":memory:")


class TestSchemaInit:
    def test_creates_tables(self, store):
        conn = store._get_conn()
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        assert "rotation_entries" in tables
        assert "rotation_meta" in tables
        assert "rotation_archive" in tables

    def test_wal_mode_enabled(self, store):
        conn = store._get_conn()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_creates_position_index(self, store):
        conn = store._get_conn()
        indexes = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()]
        assert "idx_rotation_position" in indexes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py::TestSchemaInit -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rotation_store'`

- [ ] **Step 3: Implement RotationStore skeleton with schema init**

```python
# kj-controller/rotation_store.py
"""SQLite-backed rotation storage. Pure local storage — no network calls."""

import os
import sqlite3
from datetime import datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rotation_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    singer TEXT NOT NULL,
    song_artist TEXT DEFAULT '',
    status TEXT DEFAULT 'Waiting',
    notes TEXT DEFAULT '',
    position INTEGER NOT NULL,
    file_path TEXT DEFAULT NULL,
    duration INTEGER DEFAULT NULL,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_rotation_position ON rotation_entries(position);
CREATE INDEX IF NOT EXISTS idx_rotation_status ON rotation_entries(status);

CREATE TABLE IF NOT EXISTS rotation_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS rotation_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    night_date TEXT NOT NULL,
    singer TEXT NOT NULL,
    song_artist TEXT DEFAULT '',
    status TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    position INTEGER,
    file_path TEXT DEFAULT NULL,
    duration INTEGER DEFAULT NULL,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_rotation_archive_night ON rotation_archive(night_date);
"""


class RotationStore:
    """SQLite rotation storage. All operations are local and atomic."""

    def __init__(self, db_path):
        self._db_path = os.path.expanduser(db_path) if db_path != ":memory:" else db_path
        self._conn = None
        # Ensure schema exists on init
        self._get_conn()

    def _get_conn(self):
        """Lazy SQLite connection with WAL mode."""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA cache_size=-8192")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
        return self._conn
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py::TestSchemaInit -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat(rotation): add RotationStore with SQLite schema init"
```

---

## Task 2: RotationStore — Add and Get Entries

**Files:**
- Modify: `kj-controller/rotation_store.py`
- Modify: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write add/get tests**

```python
# Append to test_rotation_store.py

class TestAddAndGetEntries:
    def test_add_entry_returns_dict_with_id(self, store):
        entry = store.add_entry("Alice", "Bohemian Rhapsody - Queen")
        assert entry["id"] is not None
        assert entry["singer"] == "Alice"
        assert entry["song_artist"] == "Bohemian Rhapsody - Queen"
        assert entry["status"] == "Waiting"
        assert entry["position"] == 1

    def test_add_multiple_entries_increments_position(self, store):
        store.add_entry("Alice", "Song A")
        store.add_entry("Bob", "Song B")
        entry3 = store.add_entry("Carol", "Song C")
        assert entry3["position"] == 3

    def test_add_entry_with_notes(self, store):
        entry = store.add_entry("Alice", "Song A", notes="first timer")
        assert entry["notes"] == "first timer"

    def test_get_entries_excludes_done(self, store):
        store.add_entry("Alice", "Song A")
        store.add_entry("Bob", "Song B")
        # Manually mark one as Done
        conn = store._get_conn()
        conn.execute("UPDATE rotation_entries SET status='Done' WHERE singer='Alice'")
        conn.commit()
        entries = store.get_entries()
        assert len(entries) == 1
        assert entries[0]["singer"] == "Bob"

    def test_get_entries_include_done(self, store):
        store.add_entry("Alice", "Song A")
        conn = store._get_conn()
        conn.execute("UPDATE rotation_entries SET status='Done' WHERE singer='Alice'")
        conn.commit()
        entries = store.get_entries(include_done=True)
        assert len(entries) == 1

    def test_get_entries_ordered_by_position(self, store):
        store.add_entry("Alice", "Song A")
        store.add_entry("Bob", "Song B")
        store.add_entry("Carol", "Song C")
        entries = store.get_entries()
        assert [e["singer"] for e in entries] == ["Alice", "Bob", "Carol"]

    def test_get_entry_by_id(self, store):
        added = store.add_entry("Alice", "Song A")
        entry = store.get_entry(added["id"])
        assert entry["singer"] == "Alice"

    def test_get_entry_missing_returns_none(self, store):
        assert store.get_entry(999) is None

    def test_get_entries_empty_rotation(self, store):
        assert store.get_entries() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py::TestAddAndGetEntries -v`
Expected: FAIL — `AttributeError: 'RotationStore' object has no attribute 'add_entry'`

- [ ] **Step 3: Implement add_entry, get_entries, get_entry**

Add to `RotationStore` class in `rotation_store.py`:

```python
    def _row_to_dict(self, row):
        """Convert a sqlite3.Row to a plain dict."""
        return dict(row) if row else None

    def add_entry(self, singer, song_artist='', notes=''):
        """Insert a new entry at the end of the rotation. Returns the new entry dict."""
        conn = self._get_conn()
        # Next position = max + 1, or 1 if empty
        row = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM rotation_entries").fetchone()
        position = row[0]
        cursor = conn.execute(
            """INSERT INTO rotation_entries (singer, song_artist, notes, position)
               VALUES (?, ?, ?, ?)""",
            (singer, song_artist, notes, position),
        )
        conn.commit()
        return self._row_to_dict(conn.execute(
            "SELECT * FROM rotation_entries WHERE id = ?", (cursor.lastrowid,)
        ).fetchone())

    def get_entries(self, include_done=False):
        """All entries ordered by position. Excludes Done by default."""
        conn = self._get_conn()
        if include_done:
            rows = conn.execute("SELECT * FROM rotation_entries ORDER BY position").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM rotation_entries WHERE LOWER(status) != 'done' ORDER BY position"
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_entry(self, entry_id):
        """Single entry by ID, or None."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM rotation_entries WHERE id = ?", (entry_id,)).fetchone()
        return self._row_to_dict(row)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py::TestAddAndGetEntries -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat(rotation): add/get entries in RotationStore"
```

---

## Task 3: RotationStore — Update, Delete, Exclusive Statuses

**Files:**
- Modify: `kj-controller/rotation_store.py`
- Modify: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write update/delete/status tests**

```python
# Append to test_rotation_store.py

class TestUpdateEntry:
    def test_update_singer(self, store):
        added = store.add_entry("Alice", "Song A")
        updated = store.update_entry(added["id"], singer="Alicia")
        assert updated["singer"] == "Alicia"
        assert updated["song_artist"] == "Song A"  # unchanged

    def test_update_song_artist(self, store):
        added = store.add_entry("Alice", "Song A")
        updated = store.update_entry(added["id"], song_artist="Song B")
        assert updated["song_artist"] == "Song B"
        assert updated["singer"] == "Alice"  # unchanged

    def test_update_both(self, store):
        added = store.add_entry("Alice", "Song A")
        updated = store.update_entry(added["id"], singer="Bob", song_artist="Song B")
        assert updated["singer"] == "Bob"
        assert updated["song_artist"] == "Song B"

    def test_update_nonexistent_raises(self, store):
        with pytest.raises(ValueError):
            store.update_entry(999, singer="Ghost")


class TestDeleteEntry:
    def test_delete_removes_entry(self, store):
        a = store.add_entry("Alice", "Song A")
        store.add_entry("Bob", "Song B")
        store.delete_entry(a["id"])
        entries = store.get_entries()
        assert len(entries) == 1
        assert entries[0]["singer"] == "Bob"

    def test_delete_recompacts_positions(self, store):
        store.add_entry("Alice", "Song A")  # pos 1
        b = store.add_entry("Bob", "Song B")  # pos 2
        store.add_entry("Carol", "Song C")  # pos 3
        store.delete_entry(b["id"])
        entries = store.get_entries()
        positions = [e["position"] for e in entries]
        assert positions == [1, 2]

    def test_delete_nonexistent_raises(self, store):
        with pytest.raises(ValueError):
            store.delete_entry(999)


class TestUpdateStatus:
    def test_update_regular_status(self, store):
        added = store.add_entry("Alice", "Song A")
        updated = store.update_status(added["id"], "Done")
        assert updated["status"] == "Done"

    def test_now_singing_clears_other_singing(self, store):
        a = store.add_entry("Alice", "Song A")
        b = store.add_entry("Bob", "Song B")
        store.update_status(a["id"], "Now Singing")
        store.update_status(b["id"], "Now Singing")
        entries = store.get_entries(include_done=True)
        alice = next(e for e in entries if e["singer"] == "Alice")
        bob = next(e for e in entries if e["singer"] == "Bob")
        assert alice["status"] == "Waiting"
        assert bob["status"] == "Now Singing"

    def test_up_next_clears_other_up_next(self, store):
        a = store.add_entry("Alice", "Song A")
        b = store.add_entry("Bob", "Song B")
        store.update_status(a["id"], "Up Next")
        store.update_status(b["id"], "Up Next")
        entries = store.get_entries(include_done=True)
        alice = next(e for e in entries if e["singer"] == "Alice")
        bob = next(e for e in entries if e["singer"] == "Bob")
        assert alice["status"] == "Waiting"
        assert bob["status"] == "Up Next"

    def test_update_status_nonexistent_raises(self, store):
        with pytest.raises(ValueError):
            store.update_status(999, "Done")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py::TestUpdateEntry tests/unit/test_rotation_store.py::TestDeleteEntry tests/unit/test_rotation_store.py::TestUpdateStatus -v`
Expected: FAIL

- [ ] **Step 3: Implement update_entry, delete_entry, update_status**

Add to `RotationStore` class:

```python
    def update_entry(self, entry_id, singer=None, song_artist=None):
        """Edit singer name and/or song. Returns updated entry."""
        conn = self._get_conn()
        existing = self.get_entry(entry_id)
        if existing is None:
            raise ValueError(f"Entry {entry_id} not found")
        updates = []
        params = []
        if singer is not None:
            updates.append("singer = ?")
            params.append(singer)
        if song_artist is not None:
            updates.append("song_artist = ?")
            params.append(song_artist)
        if updates:
            updates.append("updated_at = datetime('now', 'localtime')")
            params.append(entry_id)
            conn.execute(
                f"UPDATE rotation_entries SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()
        return self.get_entry(entry_id)

    def delete_entry(self, entry_id):
        """Delete entry and recompact positions."""
        conn = self._get_conn()
        existing = self.get_entry(entry_id)
        if existing is None:
            raise ValueError(f"Entry {entry_id} not found")
        deleted_pos = existing["position"]
        conn.execute("DELETE FROM rotation_entries WHERE id = ?", (entry_id,))
        conn.execute(
            "UPDATE rotation_entries SET position = position - 1 WHERE position > ?",
            (deleted_pos,),
        )
        conn.commit()

    def update_status(self, entry_id, new_status):
        """Set status. Clears exclusive statuses (Now Singing, Up Next) from others."""
        conn = self._get_conn()
        existing = self.get_entry(entry_id)
        if existing is None:
            raise ValueError(f"Entry {entry_id} not found")

        # Exclusive statuses: clear from all other entries first
        exclusive_map = {
            "Now Singing": {"now singing", "singing now", "singing"},
            "Up Next": {"up next", "next"},
        }
        clear_set = exclusive_map.get(new_status)
        if clear_set:
            # Build WHERE clause for all aliases
            placeholders = ", ".join("?" for _ in clear_set)
            conn.execute(
                f"""UPDATE rotation_entries
                    SET status = 'Waiting', updated_at = datetime('now', 'localtime')
                    WHERE id != ? AND LOWER(status) IN ({placeholders})""",
                (entry_id, *clear_set),
            )

        conn.execute(
            "UPDATE rotation_entries SET status = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (new_status, entry_id),
        )
        conn.commit()
        return self.get_entry(entry_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py::TestUpdateEntry tests/unit/test_rotation_store.py::TestDeleteEntry tests/unit/test_rotation_store.py::TestUpdateStatus -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat(rotation): update, delete, exclusive statuses in RotationStore"
```

---

## Task 4: RotationStore — Move, Stats, File Linking

**Files:**
- Modify: `kj-controller/rotation_store.py`
- Modify: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write move/stats/link tests**

```python
# Append to test_rotation_store.py

class TestMoveEntry:
    def test_move_down(self, store):
        a = store.add_entry("Alice", "Song A")  # pos 1
        store.add_entry("Bob", "Song B")          # pos 2
        store.add_entry("Carol", "Song C")        # pos 3
        store.move_entry(a["id"], 3)
        entries = store.get_entries()
        assert [e["singer"] for e in entries] == ["Bob", "Carol", "Alice"]
        assert [e["position"] for e in entries] == [1, 2, 3]

    def test_move_up(self, store):
        store.add_entry("Alice", "Song A")        # pos 1
        store.add_entry("Bob", "Song B")          # pos 2
        c = store.add_entry("Carol", "Song C")    # pos 3
        store.move_entry(c["id"], 1)
        entries = store.get_entries()
        assert [e["singer"] for e in entries] == ["Carol", "Alice", "Bob"]
        assert [e["position"] for e in entries] == [1, 2, 3]

    def test_move_same_position_noop(self, store):
        a = store.add_entry("Alice", "Song A")
        store.move_entry(a["id"], 1)
        entries = store.get_entries()
        assert entries[0]["singer"] == "Alice"

    def test_move_nonexistent_raises(self, store):
        with pytest.raises(ValueError):
            store.move_entry(999, 1)


class TestGetStats:
    def test_stats_empty(self, store):
        stats = store.get_stats()
        assert stats["singers"] == 0
        assert stats["sung"] == 0
        assert stats["queued"] == 0

    def test_stats_with_entries(self, store):
        store.add_entry("Alice", "Song A")
        store.add_entry("Bob", "Song B")
        store.add_entry("Alice", "Song C")  # same singer, different song
        conn = store._get_conn()
        conn.execute("UPDATE rotation_entries SET status='Done' WHERE singer='Bob'")
        conn.commit()
        stats = store.get_stats()
        assert stats["singers"] == 2  # unique singers
        assert stats["sung"] == 1
        assert stats["queued"] == 2


class TestFileLink:
    def test_link_file(self, store):
        added = store.add_entry("Alice", "Song A")
        linked = store.link_file(added["id"], "/mnt/media/song.mp4", duration=213)
        assert linked["file_path"] == "/mnt/media/song.mp4"
        assert linked["duration"] == 213

    def test_link_file_without_duration(self, store):
        added = store.add_entry("Alice", "Song A")
        linked = store.link_file(added["id"], "/mnt/media/song.mp4")
        assert linked["file_path"] == "/mnt/media/song.mp4"
        assert linked["duration"] is None

    def test_unlink_file(self, store):
        added = store.add_entry("Alice", "Song A")
        store.link_file(added["id"], "/mnt/media/song.mp4", duration=213)
        unlinked = store.unlink_file(added["id"])
        assert unlinked["file_path"] is None
        assert unlinked["duration"] is None

    def test_link_nonexistent_raises(self, store):
        with pytest.raises(ValueError):
            store.link_file(999, "/mnt/media/song.mp4")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py::TestMoveEntry tests/unit/test_rotation_store.py::TestGetStats tests/unit/test_rotation_store.py::TestFileLink -v`
Expected: FAIL

- [ ] **Step 3: Implement move_entry, get_stats, link_file, unlink_file**

Add to `RotationStore` class:

```python
    def move_entry(self, entry_id, new_position):
        """Move entry to new_position. Shifts other entries atomically."""
        conn = self._get_conn()
        existing = self.get_entry(entry_id)
        if existing is None:
            raise ValueError(f"Entry {entry_id} not found")
        old_position = existing["position"]
        if old_position == new_position:
            return

        if new_position < old_position:
            # Moving up: shift entries in [new, old) down by 1
            conn.execute(
                """UPDATE rotation_entries
                   SET position = position + 1
                   WHERE position >= ? AND position < ? AND id != ?""",
                (new_position, old_position, entry_id),
            )
        else:
            # Moving down: shift entries in (old, new] up by 1
            conn.execute(
                """UPDATE rotation_entries
                   SET position = position - 1
                   WHERE position > ? AND position <= ? AND id != ?""",
                (old_position, new_position, entry_id),
            )
        conn.execute(
            "UPDATE rotation_entries SET position = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (new_position, entry_id),
        )
        conn.commit()

    def get_stats(self):
        """Returns {singers, sung, queued, started}."""
        conn = self._get_conn()
        row = conn.execute("""
            SELECT
                COUNT(DISTINCT singer) as singers,
                SUM(CASE WHEN LOWER(status) = 'done' THEN 1 ELSE 0 END) as sung,
                SUM(CASE WHEN LOWER(status) != 'done' THEN 1 ELSE 0 END) as queued
            FROM rotation_entries
        """).fetchone()
        started = ""
        meta_row = conn.execute(
            "SELECT value FROM rotation_meta WHERE key = 'night_started_at'"
        ).fetchone()
        if meta_row:
            started = meta_row[0]
        return {
            "singers": row["singers"] or 0,
            "sung": row["sung"] or 0,
            "queued": row["queued"] or 0,
            "started": started,
        }

    def link_file(self, entry_id, file_path, duration=None):
        """Link a media file to a rotation entry."""
        conn = self._get_conn()
        existing = self.get_entry(entry_id)
        if existing is None:
            raise ValueError(f"Entry {entry_id} not found")
        conn.execute(
            """UPDATE rotation_entries
               SET file_path = ?, duration = ?, updated_at = datetime('now', 'localtime')
               WHERE id = ?""",
            (file_path, duration, entry_id),
        )
        conn.commit()
        return self.get_entry(entry_id)

    def unlink_file(self, entry_id):
        """Remove file link from a rotation entry."""
        conn = self._get_conn()
        existing = self.get_entry(entry_id)
        if existing is None:
            raise ValueError(f"Entry {entry_id} not found")
        conn.execute(
            """UPDATE rotation_entries
               SET file_path = NULL, duration = NULL, updated_at = datetime('now', 'localtime')
               WHERE id = ?""",
            (entry_id,),
        )
        conn.commit()
        return self.get_entry(entry_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py::TestMoveEntry tests/unit/test_rotation_store.py::TestGetStats tests/unit/test_rotation_store.py::TestFileLink -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat(rotation): move, stats, file linking in RotationStore"
```

---

## Task 5: RotationStore — Archive and get_all_entries

**Files:**
- Modify: `kj-controller/rotation_store.py`
- Modify: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write archive tests**

```python
# Append to test_rotation_store.py

class TestArchive:
    def test_archive_moves_to_archive_table(self, store):
        store.add_entry("Alice", "Song A")
        store.add_entry("Bob", "Song B")
        count = store.archive()
        assert count == 2
        # Rotation should have just the starter entry
        entries = store.get_entries()
        assert len(entries) == 1
        assert entries[0]["singer"] == "Andrew"
        # Archive table should have the archived entries
        conn = store._get_conn()
        archived = conn.execute("SELECT * FROM rotation_archive ORDER BY position").fetchall()
        assert len(archived) == 2
        assert archived[0]["singer"] == "Alice"
        assert archived[1]["singer"] == "Bob"

    def test_archive_empty_rotation(self, store):
        count = store.archive()
        assert count == 0
        # Should still create starter entry
        entries = store.get_entries()
        assert len(entries) == 1
        assert entries[0]["singer"] == "Andrew"

    def test_archive_sets_night_started_at(self, store):
        store.add_entry("Alice", "Song A")
        store.archive()
        conn = store._get_conn()
        row = conn.execute(
            "SELECT value FROM rotation_meta WHERE key = 'night_started_at'"
        ).fetchone()
        assert row is not None

    def test_archive_includes_done_entries(self, store):
        store.add_entry("Alice", "Song A")
        b = store.add_entry("Bob", "Song B")
        store.update_status(b["id"], "Done")
        count = store.archive()
        assert count == 2  # includes Done entry


class TestGetAllEntries:
    def test_includes_done(self, store):
        a = store.add_entry("Alice", "Song A")
        store.add_entry("Bob", "Song B")
        store.update_status(a["id"], "Done")
        all_entries = store.get_all_entries()
        assert len(all_entries) == 2

    def test_ordered_by_position(self, store):
        store.add_entry("Alice", "Song A")
        store.add_entry("Bob", "Song B")
        all_entries = store.get_all_entries()
        assert all_entries[0]["singer"] == "Alice"
        assert all_entries[1]["singer"] == "Bob"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py::TestArchive tests/unit/test_rotation_store.py::TestGetAllEntries -v`
Expected: FAIL

- [ ] **Step 3: Implement archive and get_all_entries**

Add to `RotationStore` class:

```python
    def archive(self, starter_singer="Andrew", starter_song="First Song of the Night"):
        """Move all entries to rotation_archive. Creates a starter entry. Returns count archived."""
        conn = self._get_conn()
        entries = conn.execute("SELECT * FROM rotation_entries ORDER BY position").fetchall()
        count = len(entries)

        night_date = datetime.now().strftime("%Y-%m-%d")

        if entries:
            conn.executemany(
                """INSERT INTO rotation_archive
                   (night_date, singer, song_artist, status, notes, position, file_path, duration, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(night_date, e["singer"], e["song_artist"], e["status"], e["notes"],
                  e["position"], e["file_path"], e["duration"], e["created_at"])
                 for e in entries],
            )

        # Clear rotation and create starter entry
        conn.execute("DELETE FROM rotation_entries")
        conn.execute(
            """INSERT INTO rotation_entries (singer, song_artist, status, position)
               VALUES (?, ?, 'Waiting', 1)""",
            (starter_singer, starter_song),
        )
        # Set night_started_at
        conn.execute(
            "INSERT OR REPLACE INTO rotation_meta (key, value) VALUES ('night_started_at', ?)",
            (datetime.now().isoformat(),),
        )
        conn.commit()
        return count

    def get_all_entries(self):
        """All entries including Done, ordered by position. Used for Sheet sync."""
        return self.get_entries(include_done=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py::TestArchive tests/unit/test_rotation_store.py::TestGetAllEntries -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat(rotation): archive and get_all_entries in RotationStore"
```

---

## Task 6: SheetSync — Background Push and Restore

**Files:**
- Create: `kj-controller/rotation_sync.py`
- Create: `kj-controller/tests/unit/test_rotation_sync.py`

- [ ] **Step 1: Write sync tests**

```python
# kj-controller/tests/unit/test_rotation_sync.py
"""Unit tests for SheetSync (Google Sheets background sync)."""

import time
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from rotation_store import RotationStore
from rotation_sync import SheetSync


@pytest.fixture
def store():
    """In-memory RotationStore for testing."""
    s = RotationStore(":memory:")
    s.add_entry("Alice", "Song A")
    s.add_entry("Bob", "Song B")
    return s


@pytest.fixture
def mock_gspread():
    """Mock gspread client and sheet."""
    with patch("rotation_sync.gspread") as mock_gs, \
         patch("rotation_sync.Credentials") as mock_creds:
        mock_client = MagicMock()
        mock_gs.authorize.return_value = mock_client
        mock_spreadsheet = MagicMock()
        mock_client.open_by_key.return_value = mock_spreadsheet
        mock_sheet = MagicMock()
        mock_spreadsheet.sheet1 = mock_sheet
        mock_sheet.get_all_values.return_value = [
            ["Timestamp", "Singer", "Song & Artist", "Status", "Notes"],
        ]
        yield {
            "gspread": mock_gs,
            "creds": mock_creds,
            "client": mock_client,
            "spreadsheet": mock_spreadsheet,
            "sheet": mock_sheet,
        }


class TestSyncNow:
    def test_pushes_entries_to_sheet(self, store, mock_gspread):
        sync = SheetSync(store, "sheet-id", "/tmp/fake-creds.json", sync_interval=9999)
        result = sync.sync_now()
        assert result is True
        # Should have called batch_update with entry data
        mock_gspread["sheet"].update.assert_called_once()

    def test_handles_network_error(self, store, mock_gspread):
        mock_gspread["sheet"].batch_update.side_effect = Exception("Network error")
        sync = SheetSync(store, "sheet-id", "/tmp/fake-creds.json", sync_interval=9999)
        result = sync.sync_now()
        assert result is False

    def test_updates_last_sync_meta(self, store, mock_gspread):
        sync = SheetSync(store, "sheet-id", "/tmp/fake-creds.json", sync_interval=9999)
        sync.sync_now()
        conn = store._get_conn()
        row = conn.execute("SELECT value FROM rotation_meta WHERE key='last_sheet_sync'").fetchone()
        assert row is not None


class TestRestoreFromSheet:
    def test_imports_sheet_data(self, store, mock_gspread):
        mock_gspread["sheet"].get_all_values.return_value = [
            ["Timestamp", "Singer", "Song & Artist", "Status", "Notes"],
            ["3/5/2026 20:00:00", "Carol", "Song C", "Waiting", ""],
            ["3/5/2026 20:05:00", "Dave", "Song D", "Up Next", ""],
        ]
        sync = SheetSync(store, "sheet-id", "/tmp/fake-creds.json", sync_interval=9999)
        count = sync.restore_from_sheet()
        assert count == 2
        entries = store.get_entries(include_done=True)
        assert len(entries) == 2
        assert entries[0]["singer"] == "Carol"
        assert entries[1]["singer"] == "Dave"

    def test_clears_existing_entries(self, store, mock_gspread):
        """Existing entries should be replaced, not appended to."""
        mock_gspread["sheet"].get_all_values.return_value = [
            ["Timestamp", "Singer", "Song & Artist", "Status", "Notes"],
            ["3/5/2026 20:00:00", "Carol", "Song C", "Waiting", ""],
        ]
        sync = SheetSync(store, "sheet-id", "/tmp/fake-creds.json", sync_interval=9999)
        count = sync.restore_from_sheet()
        assert count == 1
        entries = store.get_entries(include_done=True)
        assert len(entries) == 1  # Alice and Bob replaced


class TestGetStatus:
    def test_status_without_sync(self, store, mock_gspread):
        sync = SheetSync(store, "sheet-id", "/tmp/fake-creds.json", sync_interval=30)
        status = sync.get_status()
        assert "last_sync" in status
        assert "is_online" in status

    def test_status_after_successful_sync(self, store, mock_gspread):
        sync = SheetSync(store, "sheet-id", "/tmp/fake-creds.json", sync_interval=30)
        sync.sync_now()
        status = sync.get_status()
        assert status["is_online"] is True
        assert status["last_sync"] is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_sync.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rotation_sync'`

- [ ] **Step 3: Implement SheetSync**

```python
# kj-controller/rotation_sync.py
"""Background sync of rotation data to Google Sheets.

Pushes SQLite rotation state to the Sheet on a timer. The Sheet is a
read-only backup mirror — SQLite is always authoritative. Provides an
emergency restore_from_sheet() for pulling data back.
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
    """0-based column index to letter (A, B, C, ...)."""
    return chr(ord("A") + idx)


def _find_header(all_values):
    """Find header row. Returns (1-based row index, column_map) or (None, {})."""
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
            return i + 1, col_map
    return None, {}


class SheetSync:
    """Background sync of rotation data to Google Sheets."""

    def __init__(self, store, sheet_id, credentials_file, sync_interval=30):
        self._store = store
        self._sheet_id = sheet_id
        self._credentials_file = credentials_file
        self._sync_interval = sync_interval
        self._client = None
        self._sheet = None
        self._thread = None
        self._stop_event = threading.Event()
        self._is_online = False
        self._last_error = None

    def _get_sheet(self):
        """Get or create gspread worksheet connection."""
        if self._client is None:
            creds = Credentials.from_service_account_file(
                self._credentials_file, scopes=SCOPES
            )
            self._client = gspread.authorize(creds)
        if self._sheet is None:
            spreadsheet = self._client.open_by_key(self._sheet_id)
            self._sheet = spreadsheet.sheet1
        return self._sheet

    def start(self):
        """Start background sync thread."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop background sync thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _run_loop(self):
        """Background loop: sync every interval."""
        while not self._stop_event.is_set():
            self.sync_now()
            self._stop_event.wait(self._sync_interval)

    def sync_now(self):
        """Push current rotation state to the Sheet. Returns True on success."""
        try:
            sheet = self._get_sheet()
            all_values = sheet.get_all_values()
            header_row, col_map = _find_header(all_values)
            if header_row is None:
                logger.warning("Sheet has no header row, skipping sync")
                return False

            entries = self._store.get_all_entries()

            # Build rows matching Sheet column layout
            max_col = max(col_map.values()) + 1 if col_map else 5
            rows = []
            for entry in entries:
                row = [""] * max_col
                if "timestamp" in col_map:
                    row[col_map["timestamp"]] = entry.get("created_at", "")
                row[col_map.get("singer", 1)] = entry["singer"]
                row[col_map.get("song_artist", 2)] = entry["song_artist"]
                row[col_map.get("status", 3)] = entry["status"]
                if "notes" in col_map:
                    row[col_map["notes"]] = entry.get("notes", "")
                rows.append(row)

            # Overwrite data area (below header) in-place
            first_data_row = header_row + 1
            last_existing_row = len(all_values)
            last_col = _col_letter(max_col - 1)

            if rows:
                # Write new data
                data_range = f"A{first_data_row}:{last_col}{first_data_row + len(rows) - 1}"
                sheet.update(data_range, rows, value_input_option="USER_ENTERED")
                # Delete excess rows if old data was longer
                if last_existing_row > first_data_row + len(rows) - 1:
                    sheet.delete_rows(first_data_row + len(rows), last_existing_row)
            elif last_existing_row >= first_data_row:
                # No entries, clear all data rows
                sheet.delete_rows(first_data_row, last_existing_row)

            # Update last sync timestamp
            self._store._get_conn().execute(
                "INSERT OR REPLACE INTO rotation_meta (key, value) VALUES ('last_sheet_sync', ?)",
                (datetime.now().isoformat(),),
            )
            self._store._get_conn().commit()

            self._is_online = True
            self._last_error = None
            return True

        except Exception as e:
            logger.warning(f"Sheet sync failed: {e}")
            self._is_online = False
            self._last_error = str(e)
            # Reset connection on failure so next attempt re-authenticates
            self._client = None
            self._sheet = None
            return False

    def restore_from_sheet(self):
        """Pull Sheet data into SQLite (emergency restore). Returns entry count."""
        sheet = self._get_sheet()
        all_values = sheet.get_all_values()
        header_row, col_map = _find_header(all_values)
        if header_row is None:
            return 0

        col_singer = col_map.get("singer", 1)
        col_song = col_map.get("song_artist", 2)
        col_status = col_map.get("status", 3)
        col_notes = col_map.get("notes")
        col_ts = col_map.get("timestamp")

        conn = self._store._get_conn()
        conn.execute("DELETE FROM rotation_entries")

        count = 0
        for idx, row in enumerate(all_values[header_row:], start=1):
            if len(row) <= col_singer:
                continue
            singer = row[col_singer].strip()
            if not singer:
                continue
            song = row[col_song].strip() if col_song < len(row) else ""
            status = row[col_status].strip() if col_status < len(row) else "Waiting"
            notes = row[col_notes].strip() if col_notes is not None and col_notes < len(row) else ""

            conn.execute(
                """INSERT INTO rotation_entries (singer, song_artist, status, notes, position)
                   VALUES (?, ?, ?, ?, ?)""",
                (singer, song, status, notes, idx),
            )
            count += 1

        conn.commit()
        return count

    def get_status(self):
        """Returns sync status dict."""
        conn = self._store._get_conn()
        row = conn.execute(
            "SELECT value FROM rotation_meta WHERE key = 'last_sheet_sync'"
        ).fetchone()
        last_sync = row[0] if row else None
        return {
            "last_sync": last_sync,
            "is_online": self._is_online,
            "next_sync_in": self._sync_interval,  # static interval (could compute countdown)
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_sync.py -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_sync.py kj-controller/tests/unit/test_rotation_sync.py
git commit -m "feat(rotation): add SheetSync for background Google Sheets backup"
```

---

## Task 7: Rewrite RotationManager as Coordinator

**Files:**
- Modify: `kj-controller/rotation.py` (full rewrite)
- Modify: `kj-controller/tests/unit/test_rotation.py` (full rewrite)

- [ ] **Step 1: Write coordinator tests**

```python
# kj-controller/tests/unit/test_rotation.py (full rewrite)
"""Unit tests for RotationManager (coordinator)."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from rotation import RotationManager, ROTATION_CACHE_FILE


@pytest.fixture
def manager(tmp_path):
    """RotationManager backed by in-memory SQLite, no sheet sync."""
    mgr = RotationManager(db_path=":memory:")
    mgr._write_display_cache = lambda: None  # skip file writes in tests
    return mgr


@pytest.fixture
def manager_with_media(tmp_path):
    """RotationManager with a mock MediaIndex for link_file testing."""
    mgr = RotationManager(db_path=":memory:")
    mgr._write_display_cache = lambda: None
    mgr.media = MagicMock()
    mgr.media.index = {
        "/mnt/media/song.mp4": {"duration": 213},
    }
    return mgr


class TestCoordinatorCRUD:
    def test_get_rotation(self, manager):
        manager.add_entry("Alice", "Song A")
        entries = manager.get_rotation()
        assert len(entries) == 1
        assert entries[0]["singer"] == "Alice"

    def test_add_entry(self, manager):
        entry = manager.add_entry("Alice", "Song A")
        assert entry["id"] is not None

    def test_update_status(self, manager):
        entry = manager.add_entry("Alice", "Song A")
        manager.update_status(entry["id"], "Done")
        entries = manager.get_rotation()
        assert len(entries) == 0  # Done entries excluded

    def test_mark_singing(self, manager):
        a = manager.add_entry("Alice", "Song A")
        b = manager.add_entry("Bob", "Song B")
        manager.mark_singing(a["id"])
        entries = manager.get_rotation()
        alice = next(e for e in entries if e["singer"] == "Alice")
        assert alice["status"] == "Now Singing"

    def test_mark_up_next(self, manager):
        a = manager.add_entry("Alice", "Song A")
        manager.mark_up_next(a["id"])
        entries = manager.get_rotation()
        assert entries[0]["status"] == "Up Next"

    def test_update_entry(self, manager):
        a = manager.add_entry("Alice", "Song A")
        manager.update_entry(a["id"], singer="Alicia")
        entries = manager.get_rotation()
        assert entries[0]["singer"] == "Alicia"

    def test_delete_entry(self, manager):
        a = manager.add_entry("Alice", "Song A")
        manager.delete_entry(a["id"])
        assert manager.get_rotation() == []

    def test_move_entry(self, manager):
        manager.add_entry("Alice", "Song A")
        b = manager.add_entry("Bob", "Song B")
        manager.move_entry(b["id"], 1)
        entries = manager.get_rotation()
        assert entries[0]["singer"] == "Bob"

    def test_archive_rotation(self, manager):
        manager.add_entry("Alice", "Song A")
        count = manager.archive_rotation()
        assert count == 1
        entries = manager.get_rotation()
        assert entries[0]["singer"] == "Andrew"


class TestCoordinatorLinking:
    def test_link_file_with_media_index(self, manager_with_media):
        a = manager_with_media.add_entry("Alice", "Song A")
        linked = manager_with_media.link_file(a["id"], "/mnt/media/song.mp4")
        assert linked["file_path"] == "/mnt/media/song.mp4"
        assert linked["duration"] == 213  # looked up from MediaIndex

    def test_link_file_without_media_index(self, manager):
        a = manager.add_entry("Alice", "Song A")
        linked = manager.link_file(a["id"], "/mnt/media/song.mp4")
        assert linked["file_path"] == "/mnt/media/song.mp4"
        assert linked["duration"] is None  # no media index to look up

    def test_unlink_file(self, manager):
        a = manager.add_entry("Alice", "Song A")
        manager.link_file(a["id"], "/mnt/media/song.mp4")
        unlinked = manager.unlink_file(a["id"])
        assert unlinked["file_path"] is None


class TestDisplayCache:
    def test_write_display_cache(self, tmp_path, monkeypatch):
        cache_file = str(tmp_path / "rotation_cache.json")
        monkeypatch.setattr("rotation.ROTATION_CACHE_FILE", cache_file)
        mgr = RotationManager(db_path=":memory:")
        mgr.add_entry("Alice", "Song A")
        # Cache should have been written
        assert os.path.exists(cache_file)
        with open(cache_file) as f:
            data = json.load(f)
        assert len(data["queue"]) == 1
        assert data["queue"][0]["singer"] == "Alice"
        assert "stats" in data
        assert "updated" in data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation.py -v`
Expected: FAIL — old RotationManager doesn't match new interface

- [ ] **Step 3: Rewrite rotation.py as coordinator**

```python
# kj-controller/rotation.py (full rewrite)
"""Rotation coordinator — delegates to RotationStore (SQLite) and SheetSync.

This module is a thin wrapper. All data lives in SQLite via RotationStore.
Google Sheets sync is optional and runs in the background via SheetSync.
"""

import json
import os
import time

from rotation_store import RotationStore

ROTATION_CACHE_FILE = "/tmp/rotation_cache.json"


class RotationManager:
    """Coordinates rotation storage and optional Sheet sync."""

    def __init__(self, db_path, sheet_id=None, credentials_file=None, sync_interval=30):
        self.store = RotationStore(db_path)
        self.sync = None
        self.media = None  # Set by app.py if MediaIndex is available

        if sheet_id and credentials_file:
            from rotation_sync import SheetSync
            self.sync = SheetSync(
                self.store, sheet_id,
                os.path.expanduser(credentials_file),
                sync_interval,
            )
            self.sync.start()

    def _kick_sync(self):
        """Notify sync that data changed (non-blocking)."""
        # Sync runs on its own timer; this is a hint for immediate push
        pass

    def _write_display_cache(self):
        """Write rotation data to local JSON cache for conky display."""
        try:
            entries = self.store.get_entries()
            queue = [
                {
                    "singer": e["singer"],
                    "song_artist": e["song_artist"],
                    "status": e["status"],
                }
                for e in entries
            ]
            stats = self.store.get_stats()
            data = {
                "queue": queue,
                "stats": stats,
                "updated": time.time(),
            }
            tmp = ROTATION_CACHE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, ROTATION_CACHE_FILE)
        except Exception:
            pass  # Best-effort

    def _after_mutation(self):
        """Called after every write operation."""
        self._write_display_cache()
        self._kick_sync()

    # --- Read ---

    def get_rotation(self, force_refresh=False):
        """Get non-done entries. force_refresh is accepted but ignored (SQLite is always fresh)."""
        return self.store.get_entries()

    def get_stats(self):
        return self.store.get_stats()

    # --- Write ---

    def add_entry(self, singer, song_artist='', notes=''):
        entry = self.store.add_entry(singer, song_artist, notes)
        self._after_mutation()
        return entry

    def update_entry(self, entry_id, singer=None, song_artist=None):
        entry = self.store.update_entry(entry_id, singer, song_artist)
        self._after_mutation()
        return entry

    def update_status(self, entry_id, new_status):
        entry = self.store.update_status(entry_id, new_status)
        self._after_mutation()
        return entry

    def mark_singing(self, entry_id):
        return self.update_status(entry_id, "Now Singing")

    def mark_up_next(self, entry_id):
        return self.update_status(entry_id, "Up Next")

    def delete_entry(self, entry_id):
        self.store.delete_entry(entry_id)
        self._after_mutation()

    def move_entry(self, entry_id, new_position):
        self.store.move_entry(entry_id, new_position)
        self._after_mutation()

    # --- File linking ---

    def link_file(self, entry_id, file_path):
        """Link a media file. Looks up duration from self.media if available."""
        duration = None
        if self.media and hasattr(self.media, 'index'):
            media_entry = self.media.index.get(file_path)
            if media_entry:
                duration = media_entry.get("duration")
        entry = self.store.link_file(entry_id, file_path, duration)
        self._after_mutation()
        return entry

    def unlink_file(self, entry_id):
        entry = self.store.unlink_file(entry_id)
        self._after_mutation()
        return entry

    # --- Archive ---

    def archive_rotation(self):
        count = self.store.archive()
        self._after_mutation()
        return count

    # --- Sync ---

    def get_sync_status(self):
        if self.sync is None:
            return {"last_sync": None, "is_online": False, "next_sync_in": None}
        return self.sync.get_status()

    def restore_from_sheet(self):
        if self.sync is None:
            raise RuntimeError("Sheet sync not configured")
        count = self.sync.restore_from_sheet()
        self._after_mutation()
        return count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation.py -v`
Expected: All passed

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation.py kj-controller/tests/unit/test_rotation.py
git commit -m "feat(rotation): rewrite RotationManager as SQLite coordinator"
```

---

## Task 8: Update app.py and conftest — Initialization

> **Note:** After Task 7, the `flask_app` fixture in conftest.py will break because `_init_rotation` still uses the old `RotationManager(sheet_id, creds_file)` constructor. This task fixes that. Run only rotation-specific tests between Tasks 7 and 8.

**Files:**
- Modify: `kj-controller/app.py:23-29, 124-126`
- Modify: `kj-controller/tests/conftest.py`

- [ ] **Step 1: Update `_init_rotation` in app.py**

Replace lines 23-29 of `kj-controller/app.py`:

```python
def _init_rotation(cfg):
    """Create a RotationManager with SQLite DB and optional Sheet sync."""
    db_path = cfg.get('rotation_db_path', os.path.expanduser('~/kjdata/rotation.db'))
    sheet_id = cfg.get('rotation_sheet_id')
    creds_file = cfg.get('rotation_credentials_file')
    sync_interval = cfg.get('rotation_sync_interval', 30)
    return RotationManager(db_path, sheet_id, creds_file, sync_interval)
```

Also update the `create_app` function (line 43) and `start_app` (line 124-126) to pass media reference:

After `flask_app.rotation = _init_rotation(cfg)` add:
```python
    flask_app.rotation.media = flask_app.media
```

Update the start_app section similarly — after `flask_app.rotation = _init_rotation(cfg)`:
```python
    if flask_app.rotation:
        flask_app.rotation.media = flask_app.media
        log_message("Rotation enabled (SQLite primary).", cfg)
        if flask_app.rotation.sync:
            log_message("Sheet sync enabled.", cfg)
```

- [ ] **Step 2: Update conftest.py to include rotation_db_path**

Add to `mock_config` fixture in `kj-controller/tests/conftest.py`:
```python
        "rotation_db_path": ":memory:",
```

- [ ] **Step 3: Run existing tests to verify nothing breaks**

Run: `cd kj-controller && python -m pytest tests/ -v --timeout=30`
Expected: All pass (or skip as before)

- [ ] **Step 4: Commit**

```bash
git add kj-controller/app.py kj-controller/tests/conftest.py
git commit -m "feat(rotation): update app init for SQLite-backed rotation"
```

---

## Task 9: Update Routes — row_index to id + New Endpoints

**Files:**
- Modify: `kj-controller/routes.py:1402-1562`
- Modify: `kj-controller/tests/integration/test_rotation_routes.py`

- [ ] **Step 1: Rewrite rotation routes**

Replace `kj-controller/routes.py` lines 1402-1562 with:

```python
# --- Rotation (SQLite primary, optional Sheet backup) ---

@routes_bp.route('/rotation', methods=['GET'])
def get_rotation():
    """Returns the current singer rotation queue (non-done entries)."""
    rotation = current_app.rotation
    if rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    try:
        entries = rotation.get_rotation()
        return jsonify({"entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/status', methods=['POST'])
def update_rotation_status():
    """Update a rotation entry's status."""
    rotation = current_app.rotation
    if rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    raw_id = data.get('id')
    status = data.get('status', '')
    if raw_id is None:
        return jsonify({"error": "id is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "id must be an integer"}), 400
    if entry_id < 1:
        return jsonify({"error": "id must be >= 1"}), 400

    try:
        if status.lower() in ('now singing', 'singing now', 'singing'):
            rotation.mark_singing(entry_id)
        elif status.lower() in ('up next', 'next'):
            rotation.mark_up_next(entry_id)
        else:
            rotation.update_status(entry_id, status)
        entries = rotation.get_rotation()
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/edit', methods=['POST'])
def edit_rotation_entry():
    """Edit a rotation entry's singer name and/or song."""
    rotation = current_app.rotation
    if rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    raw_id = data.get('id')
    if raw_id is None:
        return jsonify({"error": "id is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "id must be an integer"}), 400

    singer = data.get('singer')
    song_artist = data.get('song_artist')
    if singer is not None:
        singer = singer.strip()
    if song_artist is not None:
        song_artist = song_artist.strip()

    try:
        rotation.update_entry(entry_id, singer=singer, song_artist=song_artist)
        entries = rotation.get_rotation()
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/delete', methods=['POST'])
def delete_rotation_entry():
    """Delete a rotation entry entirely."""
    rotation = current_app.rotation
    if rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    raw_id = data.get('id')
    if raw_id is None:
        return jsonify({"error": "id is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "id must be an integer"}), 400
    if entry_id < 1:
        return jsonify({"error": "id must be >= 1"}), 400

    try:
        rotation.delete_entry(entry_id)
        entries = rotation.get_rotation()
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/add', methods=['POST'])
def add_rotation_entry():
    """Add a new singer to the rotation."""
    rotation = current_app.rotation
    if rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    singer = data.get('singer', '').strip()
    song_artist = data.get('song_artist', '').strip()
    notes = data.get('notes', '').strip()
    if not singer:
        return jsonify({"error": "singer is required"}), 400

    try:
        rotation.add_entry(singer, song_artist, notes)
        entries = rotation.get_rotation()
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/move', methods=['POST'])
def move_rotation_entry():
    """Move a rotation entry to a new position."""
    rotation = current_app.rotation
    if rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    try:
        entry_id = int(data.get('id'))
        new_position = int(data.get('new_position'))
    except (TypeError, ValueError):
        return jsonify({"error": "id and new_position must be integers"}), 400

    try:
        rotation.move_entry(entry_id, new_position)
        entries = rotation.get_rotation()
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/archive', methods=['POST'])
def archive_rotation():
    """Archive all rotation entries and start fresh."""
    rotation = current_app.rotation
    if rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503

    try:
        count = rotation.archive_rotation()
        entries = rotation.get_rotation()
        return jsonify({"success": True, "archived": count, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/link', methods=['POST'])
def link_rotation_file():
    """Link a media file to a rotation entry."""
    rotation = current_app.rotation
    if rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    raw_id = data.get('id')
    file_path = data.get('file_path', '').strip()
    if raw_id is None:
        return jsonify({"error": "id is required"}), 400
    if not file_path:
        return jsonify({"error": "file_path is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "id must be an integer"}), 400
    if entry_id < 1:
        return jsonify({"error": "id must be >= 1"}), 400

    try:
        entry = rotation.link_file(entry_id, file_path)
        entries = rotation.get_rotation()
        return jsonify({"success": True, "entry": entry, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/unlink', methods=['POST'])
def unlink_rotation_file():
    """Remove file link from a rotation entry."""
    rotation = current_app.rotation
    if rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    raw_id = data.get('id')
    if raw_id is None:
        return jsonify({"error": "id is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "id must be an integer"}), 400
    if entry_id < 1:
        return jsonify({"error": "id must be >= 1"}), 400

    try:
        entry = rotation.unlink_file(entry_id)
        entries = rotation.get_rotation()
        return jsonify({"success": True, "entry": entry, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/sync-status', methods=['GET'])
def rotation_sync_status():
    """Returns the current sync status."""
    rotation = current_app.rotation
    if rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    try:
        return jsonify(rotation.get_sync_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/restore', methods=['POST'])
def restore_rotation_from_sheet():
    """Emergency restore rotation data from Google Sheet."""
    rotation = current_app.rotation
    if rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    try:
        count = rotation.restore_from_sheet()
        entries = rotation.get_rotation()
        return jsonify({"success": True, "restored": count, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 2: Rewrite route tests**

Full rewrite of `kj-controller/tests/integration/test_rotation_routes.py` — change all `row_index` to `id`, update SAMPLE_ENTRIES to include `id` and `position` fields, add tests for new endpoints (`/rotation/link`, `/rotation/unlink`, `/rotation/sync-status`, `/rotation/restore`). The `mock_rotation` fixture should mock the new `RotationManager` interface (no `mark_singing`/`mark_up_next` — those are called internally by `update_status` route logic, but the route tests should verify the right coordinator method is called).

Key changes to SAMPLE_ENTRIES:
```python
SAMPLE_ENTRIES = [
    {"id": 1, "singer": "Alice", "song_artist": "Bohemian Rhapsody", "status": "Now Singing",
     "notes": "", "position": 1, "file_path": None, "duration": None,
     "created_at": "2026-03-21 20:00:00", "updated_at": "2026-03-21 20:00:00"},
    {"id": 2, "singer": "Bob", "song_artist": "Don't Stop Believin", "status": "Up Next",
     "notes": "", "position": 2, "file_path": None, "duration": None,
     "created_at": "2026-03-21 20:05:00", "updated_at": "2026-03-21 20:05:00"},
    {"id": 3, "singer": "Carol", "song_artist": "Sweet Caroline", "status": "Waiting",
     "notes": "", "position": 3, "file_path": None, "duration": None,
     "created_at": "2026-03-21 20:10:00", "updated_at": "2026-03-21 20:10:00"},
]
```

- [ ] **Step 3: Run all rotation tests**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation.py tests/integration/test_rotation_routes.py -v`
Expected: All pass

- [ ] **Step 4: Run full test suite to check for regressions**

Run: `cd kj-controller && python -m pytest tests/ -v --timeout=30`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_rotation_routes.py
git commit -m "feat(rotation): update routes for SQLite IDs, add link/sync/restore endpoints"
```

---

## Task 10: Frontend — row_index to id + Move Semantics

**Files:**
- Modify: `kj-controller/static/app.js:2320-2822`

- [ ] **Step 1: Update all rotation JS functions**

Replace all `row_index` references with `id` throughout the rotation section of `app.js` (lines 2320-2822). Key changes:

1. **`renderRotation`** (line 2387): `entry.row_index` → `entry.id`
2. **Drag-and-drop drop handler** (line 2422): Change from `moveRotationEntry(entries[fromIdx].row_index, entries[toIdx].row_index)` to `moveRotationEntry(entries[fromIdx].id, entries[toIdx].position)`
3. **`enterRotationEditMode`** (line 2582): `entry.row_index` → `entry.id`
4. **`saveRotationEdit`** (line 2629): `row_index: rowIndex` → `id: entryId`, rename param
5. **`deleteRotationEntry`** (line 2653): `row_index: rowIndex` → `id: entryId`, rename param
6. **`updateRotationStatus`** (line 2699): `row_index: rowIndex` → `id: entryId`, rename param
7. **`moveRotationEntry`** (line 2722): Change from `{from_row: fromRow, to_row: toRow}` to `{id: entryId, new_position: newPosition}`, rename function params
8. **All button onclick handlers** (lines 2480, 2487, 2494, 2516): `entry.row_index` → `entry.id`

Also update the comment on line 2320 from `// --- Rotation (Google Sheet integration) ---` to `// --- Rotation (SQLite primary) ---`

- [ ] **Step 2: Test manually in browser**

Verify via the dev server (`npx next dev` is not applicable here — this is a Flask app):
```bash
cd kj-controller && python app.py
```
Open `http://localhost:80` and test:
- Add a singer
- Change status (Singing, Done, Next)
- Edit singer/song
- Drag to reorder
- Delete an entry
- Archive rotation

- [ ] **Step 3: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat(rotation): frontend switches from row_index to SQLite id"
```

---

## Task 11: Frontend — File Linking, Duration, Time Estimates, Sync Indicator

**Files:**
- Modify: `kj-controller/static/app.js`
- Modify: `kj-controller/templates/index.html:55-71`
- Modify: `kj-controller/static/style.css`

- [ ] **Step 1: Add sync indicator to HTML**

In `kj-controller/templates/index.html`, add a sync indicator dot in the rotation header (inside `rotation-header-btns` div):

```html
<span id="rotation-sync-dot" class="rotation-sync-dot" title="Sync status"></span>
```

Add a restore button in the header area (near "New Rotation"):
```html
<button class="rotation-restore-btn" onclick="restoreFromSheet()" title="Restore from Google Sheet backup">Restore</button>
```

- [ ] **Step 2: Add file linking UI, duration badges, and time estimates to app.js**

Add to the rotation section of `app.js`:

**Sync indicator polling (every 30s):**
```javascript
async function fetchSyncStatus() {
    try {
        const resp = await fetch('/rotation/sync-status');
        if (!resp.ok) return;
        const data = await resp.json();
        const dot = document.getElementById('rotation-sync-dot');
        if (!dot) return;
        dot.className = 'rotation-sync-dot';
        if (data.is_online) {
            dot.classList.add('sync-online');
            dot.title = 'Synced: ' + (data.last_sync || 'unknown');
        } else if (data.next_sync_in) {
            dot.classList.add('sync-offline');
            dot.title = 'Offline — sync will resume when connected';
        } else {
            dot.classList.add('sync-disabled');
            dot.title = 'Sheet sync not configured';
        }
    } catch (e) { /* ignore */ }
}
setInterval(fetchSyncStatus, 30000);
```

**Restore function:**
```javascript
async function restoreFromSheet() {
    if (!confirm('Restore rotation from Google Sheet backup?\n\nThis will replace the current rotation with the last synced state.')) return;
    showRotationIndicator('spin');
    try {
        const resp = await fetch('/rotation/restore', { method: 'POST', headers: {'Content-Type': 'application/json'} });
        const data = await resp.json();
        if (!resp.ok) { showRotationIndicator('error'); alert('Restore failed: ' + (data.error || 'Unknown')); return; }
        if (data.entries) { rotationData = data.entries; renderRotation(rotationData); }
        showRotationIndicator('success');
    } catch (e) { showRotationIndicator('error'); }
}
```

**In `renderRotation`, add to each entry row:**

Duration badge (after song text):
```javascript
if (entry.duration) {
    const dur = document.createElement('span');
    dur.className = 'rotation-duration';
    const mins = Math.floor(entry.duration / 60);
    const secs = entry.duration % 60;
    dur.textContent = mins + ':' + String(secs).padStart(2, '0');
    info.appendChild(dur);
}
```

Time estimate (right side):
```javascript
if (entry.estimated_time) {
    const est = document.createElement('span');
    est.className = 'rotation-estimate';
    est.textContent = '~' + entry.estimated_time;
    est.title = 'Estimated sing time';
    info.appendChild(est);
}
```

Link/Play buttons (in actions area):
```javascript
if (entry.file_path) {
    const playBtn = document.createElement('button');
    playBtn.className = 'rotation-btn rotation-btn-play';
    playBtn.textContent = '\u25B6';  // ▶
    playBtn.title = 'Play this song';
    playBtn.onclick = () => playMedia(entry.file_path);
    actions.insertBefore(playBtn, actions.firstChild);
} else {
    const linkBtn = document.createElement('button');
    linkBtn.className = 'rotation-btn rotation-btn-link';
    linkBtn.textContent = '\uD83D\uDD17';  // 🔗
    linkBtn.title = 'Link a song file';
    linkBtn.onclick = () => openLinkSearch(entry.id);
    actions.insertBefore(linkBtn, actions.firstChild);
}
```

**Link search function** (opens inline catalog search):
```javascript
async function openLinkSearch(entryId) {
    const query = prompt('Search for song to link:');
    if (!query) return;
    try {
        const resp = await fetch('/search?q=' + encodeURIComponent(query));
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.results || !data.results.length) { alert('No results found'); return; }
        // Show first 5 results as choices
        const choices = data.results.slice(0, 5);
        const msg = choices.map((r, i) => `${i+1}. ${r.artist} - ${r.title} (${r.format})`).join('\n');
        const choice = prompt('Select a result (1-' + choices.length + '):\n\n' + msg);
        if (!choice) return;
        const idx = parseInt(choice, 10) - 1;
        if (idx < 0 || idx >= choices.length) return;
        const selected = choices[idx];
        showRotationIndicator('spin');
        const linkResp = await fetch('/rotation/link', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id: entryId, file_path: selected.path }),
        });
        const linkData = await linkResp.json();
        if (linkData.entries) { rotationData = linkData.entries; renderRotation(rotationData); }
        showRotationIndicator(linkResp.ok ? 'success' : 'error');
    } catch (e) { showRotationIndicator('error'); }
}
```

- [ ] **Step 3: Add CSS styles**

Add to `kj-controller/static/style.css`:

```css
/* Rotation sync indicator */
.rotation-sync-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 8px;
    background: #555;
}
.rotation-sync-dot.sync-online { background: #2d8a4e; }
.rotation-sync-dot.sync-offline { background: #d4720a; }
.rotation-sync-dot.sync-disabled { background: #555; }

/* Duration badge */
.rotation-duration {
    font-size: 0.75em;
    color: #8892a4;
    margin-left: 8px;
    font-variant-numeric: tabular-nums;
}

/* Time estimate */
.rotation-estimate {
    font-size: 0.75em;
    color: #8892a4;
    margin-left: auto;
    padding-left: 8px;
    white-space: nowrap;
}

/* Link/Play buttons */
.rotation-btn-play {
    background: #2d8a4e;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 2px 6px;
    cursor: pointer;
}
.rotation-btn-link {
    background: transparent;
    color: #8892a4;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 2px 6px;
    cursor: pointer;
}
.rotation-btn-link:hover { border-color: #8892a4; }

/* Restore button */
.rotation-restore-btn {
    font-size: 0.75em;
    background: transparent;
    color: #8892a4;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 2px 8px;
    cursor: pointer;
}
```

- [ ] **Step 4: Add estimated_time computation to routes.py**

In the `get_rotation` route, compute `estimated_time` before returning entries. Add this helper at the top of the rotation routes section:

```python
def _add_time_estimates(entries):
    """Add estimated_time field to each entry based on cumulative durations."""
    from datetime import datetime, timedelta
    default_duration = 240  # 4 minutes
    now = datetime.now()
    cumulative = 0
    # Skip entries that are "Now Singing" for the base time
    for entry in entries:
        if entry.get("status", "").lower() in ("now singing", "singing now"):
            entry["estimated_time"] = "Now"
            continue
        est = now + timedelta(seconds=cumulative)
        entry["estimated_time"] = est.strftime("%-I:%M %p").lower()
        cumulative += entry.get("duration") or default_duration
```

Call `_add_time_estimates(entries)` before `return jsonify({"entries": entries})` in the `get_rotation` route.

- [ ] **Step 5: Test manually in browser**

Verify all new UI elements work:
- Sync indicator dot shows correct color
- Duration badges display for linked entries
- Time estimates show for each entry
- Link button opens search prompt
- Play button triggers playback
- Restore button works

- [ ] **Step 6: Commit**

```bash
git add kj-controller/static/app.js kj-controller/templates/index.html kj-controller/static/style.css kj-controller/routes.py
git commit -m "feat(rotation): file linking UI, duration, time estimates, sync indicator"
```

---

## Task 12: Conky Display — Remove Sheet Fallback

**Files:**
- Modify: `desktop/rotation_data.py`

- [ ] **Step 1: Simplify rotation_data.py**

Remove from `desktop/rotation_data.py`:
- `import csv` (line 13)
- `import io` (line 14)
- `from urllib.error import URLError` (line 19)
- `from urllib.request import urlopen` (line 20)
- `SHEET_ID`, `SHEET_GID`, `SHEET_CSV_URL` constants (lines 26-31)
- `FETCH_TIMEOUT` constant (line 38)
- `fetch_from_sheet()` function (lines 82-139)
- `fetch_all_rows()` function (lines 142-147)

Update `main()` to use `read_local_cache()` directly:

```python
def main():
    stats_only = "--stats" in sys.argv

    cached = read_local_cache()
    if cached is None:
        print("--" if stats_only else f"{MARGIN}${{color {COLOR_DEFAULT}}}${{font DejaVu Sans:size=28}}Offline${{font}}${{color}}")
        return

    queue, stats = cached

    if stats_only:
        parts = []
        if stats.get("started"):
            parts.append(f"Started: {stats['started']}")
        parts.append(f"{stats['singers']} singers | {stats['sung']} sung | {stats['queued']} queued")
        print("    ".join(parts))
    else:
        format_conky(queue)
```

- [ ] **Step 2: Run rotation_data.py test**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_data.py -v`
Expected: Tests pass (or update if they test the removed functions)

- [ ] **Step 3: Commit**

```bash
git add desktop/rotation_data.py
git commit -m "refactor(rotation): remove Sheet CSV fallback from conky display"
```

---

## Task 13: Final Integration Test and Cleanup

**Files:**
- All modified files

- [ ] **Step 1: Run full test suite**

Run: `cd kj-controller && python -m pytest tests/ -v --timeout=30`
Expected: All pass

- [ ] **Step 2: Run with coverage**

Run: `cd kj-controller && python -m pytest tests/ --cov --cov-report=term --timeout=30`
Expected: rotation_store.py, rotation_sync.py, rotation.py all above 70%

- [ ] **Step 3: Manual smoke test**

Start the app locally and verify the full rotation workflow:
```bash
cd kj-controller && python app.py
```

Test:
1. Add 3 singers
2. Mark one as Now Singing, one as Up Next
3. Drag to reorder
4. Edit a singer name
5. Link a file from catalog search
6. Verify duration badge and time estimates appear
7. Mark as Done
8. Archive rotation
9. Check sync indicator

- [ ] **Step 4: Commit any final fixes**

```bash
git add -A
git commit -m "test(rotation): final integration fixes"
```
