"""RotationStore: SQLite-backed storage for karaoke singer rotation entries."""

import json
import sqlite3
import threading


# Human-editable fields that define a "meaningful" difference between two
# rotation states (position is deliberately excluded — reordering is low-stakes
# and would make every diff noisy).
_DIFF_FIELDS = ("singer", "song_artist", "status", "notes", "paid", "priority_bias")


def _human_view(entry):
    """A compact, display-friendly projection of an entry for diff output."""
    return {
        "id": entry.get("id"),
        "singer": entry.get("singer", ""),
        "song_artist": entry.get("song_artist", ""),
        "status": entry.get("status", ""),
    }


def diff_entries(current, target):
    """Compare two rotation snapshots by entry id.

    Returns ``{"removed": [...], "added": [...], "changed": [...]}`` where:
      - removed: present in ``current`` but not ``target`` (an undo would drop them)
      - added:   present in ``target`` but not ``current`` (an undo would re-add them)
      - changed: present in both but a human field differs (each item carries a
                 ``before`` projection alongside the after-view)

    Pure function — used both for the undo/redo preview and for tests.
    """
    cur = {e["id"]: e for e in current if e.get("id") is not None}
    tgt = {e["id"]: e for e in target if e.get("id") is not None}

    removed = [_human_view(cur[i]) for i in cur if i not in tgt]
    added = [_human_view(tgt[i]) for i in tgt if i not in cur]
    changed = []
    for i in cur:
        if i not in tgt:
            continue
        before = tuple(cur[i].get(f) for f in _DIFF_FIELDS)
        after = tuple(tgt[i].get(f) for f in _DIFF_FIELDS)
        if before != after:
            view = _human_view(tgt[i])
            view["before"] = _human_view(cur[i])
            changed.append(view)

    removed.sort(key=lambda e: e["id"])
    added.sort(key=lambda e: e["id"])
    changed.sort(key=lambda e: e["id"])
    return {"removed": removed, "added": added, "changed": changed}


class RotationStore:
    """Pure local SQLite storage for rotation entries.

    Connections are per-thread via ``threading.local``. Sharing a single
    sqlite3 connection across Flask request threads + the SheetSync background
    thread caused a 2026-05-01 outage: an implicit BEGIN in one thread
    pinned the write lock for every other thread, AND concurrent cursor
    use on the shared connection corrupted the API state. Per-thread
    connections give each thread its own implicit-transaction scope; the
    file-level WAL lock is the only thing that serialises writers, and a
    10s busy_timeout is plenty for that.
    """

    # In-memory databases (":memory:") are tied to a single connection.
    # Tests use them and need to share the connection across threads, so
    # treat them specially — share one connection with a process-wide lock.
    _MEMORY = ":memory:"

    # Server-side undo/redo history depth (per stack). Snapshots are small JSON
    # blobs, so this is a generous bound that still caps unbounded growth.
    MAX_HISTORY = 30

    # Tracking fields restored separately from human-edited fields, so an undo
    # can preserve the *live* values (e.g. a download that completed in the
    # background) instead of reverting them and breaking playback.
    _TRACKING_FIELDS = (
        "file_path", "duration", "download_source", "download_status",
        "download_id", "url_fallback", "gen_job_id", "gen_status",
        # Tier-2 render-verification flag: tied to the live linked file, so a
        # restore must keep the live value (not revert to the snapshot) and
        # never drop it entirely.
        "playability_warning",
    )

    def __init__(self, db_path):
        self.db_path = db_path
        self._local = threading.local()
        # Memory-db fallback: one shared connection, lock-serialised.
        self._memory_conn = None
        self._memory_lock = threading.Lock()
        self.init_schema()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _open_conn(self):
        """Open and configure a fresh sqlite3 connection."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA cache_size=-8192")   # 8 MB
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _get_conn(self):
        """Return this thread's sqlite3 connection (lazily created).

        For ``:memory:`` databases the connection is process-shared because
        each connect() opens a different in-memory db.
        """
        if self.db_path == self._MEMORY:
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(
                    self.db_path, check_same_thread=False, timeout=10,
                )
                self._memory_conn.row_factory = sqlite3.Row
            return self._memory_conn
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open_conn()
            self._local.conn = conn
        return conn

    def close(self):
        """Close this thread's connection (or the shared memory conn)."""
        if self.db_path == self._MEMORY:
            if self._memory_conn is not None:
                self._memory_conn.close()
                self._memory_conn = None
            return
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init_schema(self):
        """Create tables and indexes if they do not already exist."""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS rotation_entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                singer      TEXT NOT NULL,
                song_artist TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'Waiting',
                notes       TEXT NOT NULL DEFAULT '',
                position    INTEGER NOT NULL DEFAULT 0,
                priority_bias INTEGER NOT NULL DEFAULT 0,
                file_path   TEXT,
                duration    INTEGER,
                download_source TEXT DEFAULT NULL,
                download_status TEXT DEFAULT NULL,
                download_id TEXT DEFAULT NULL,
                url_fallback TEXT DEFAULT NULL,
                gen_job_id  TEXT DEFAULT NULL,
                gen_status  TEXT DEFAULT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                done_at     TEXT DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS rotation_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS rotation_archive (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                night_date  TEXT NOT NULL,
                singer      TEXT NOT NULL,
                song_artist TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT '',
                notes       TEXT NOT NULL DEFAULT '',
                position    INTEGER,
                file_path   TEXT,
                duration    INTEGER,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_rotation_position
                ON rotation_entries (position);

            CREATE INDEX IF NOT EXISTS idx_rotation_status
                ON rotation_entries (status);

            CREATE INDEX IF NOT EXISTS idx_rotation_archive_night
                ON rotation_archive (night_date);

            CREATE TABLE IF NOT EXISTS rotation_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                stack        TEXT NOT NULL,          -- 'undo' | 'redo'
                seq          INTEGER NOT NULL,       -- pop the highest seq per stack
                label        TEXT,
                rev          INTEGER,
                entries_json TEXT NOT NULL,
                created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_rotation_history_stack_seq
                ON rotation_history (stack, seq);
        """)

        # Initialise the monotonic revision counter if absent.
        conn.execute(
            "INSERT OR IGNORE INTO rotation_meta (key, value) VALUES ('rotation_rev', '0')"
        )

        # Migrate existing databases: add columns that may be missing
        existing_cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(rotation_entries)"
        ).fetchall()}
        migrations = [
            ("download_source", "TEXT DEFAULT NULL"),
            ("download_status", "TEXT DEFAULT NULL"),
            ("download_id", "TEXT DEFAULT NULL"),
            ("url_fallback", "TEXT DEFAULT NULL"),
            ("gen_job_id", "TEXT DEFAULT NULL"),
            ("gen_status", "TEXT DEFAULT NULL"),
            ("paid", "INTEGER NOT NULL DEFAULT 0"),
            ("priority_bias", "INTEGER NOT NULL DEFAULT 0"),
            ("singers_json", "TEXT DEFAULT NULL"),
            ("done_at", "TEXT DEFAULT NULL"),
            ("playability_warning", "TEXT DEFAULT NULL"),
        ]
        added_cols = []
        for col_name, col_type in migrations:
            if col_name not in existing_cols:
                conn.execute(
                    f"ALTER TABLE rotation_entries ADD COLUMN {col_name} {col_type}"
                )
                added_cols.append(col_name)
        # Best-effort backfill of done_at for entries marked done before this
        # column existed, so "time since last sang" survives the upgrade. We use
        # updated_at as an approximation (it was the previous source).
        if "done_at" in added_cols:
            conn.execute(
                "UPDATE rotation_entries SET done_at = updated_at "
                "WHERE LOWER(status) = 'done' AND done_at IS NULL"
            )
        conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row):
        """Convert a sqlite3.Row to a plain dict."""
        if row is None:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # Task 2: Add and Get Entries
    # ------------------------------------------------------------------

    def add_entry(self, singer, song_artist='', notes='', file_path=None, duration=None, singers=None):
        """Insert a new entry at max(position)+1 and return the new entry dict."""
        singers_json = None
        if singers is not None:
            singers = [s.strip() for s in singers]
            singer = " & ".join(singers)
            singers_json = json.dumps(singers)

        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO rotation_entries (singer, song_artist, notes, position, file_path, duration, singers_json) "
            "VALUES (?, ?, ?, (SELECT COALESCE(MAX(position), 0) + 1 FROM rotation_entries), ?, ?, ?)",
            (singer, song_artist, notes, file_path, duration, singers_json),
        )
        conn.commit()
        return self._row_to_dict(
            conn.execute(
                "SELECT * FROM rotation_entries WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        )

    def get_entries(self, include_done=False):
        """Return all entries ordered by position.

        By default, excludes entries whose status (case-insensitive) is 'done'.
        """
        conn = self._get_conn()
        if include_done:
            rows = conn.execute(
                "SELECT * FROM rotation_entries ORDER BY position"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM rotation_entries "
                "WHERE LOWER(status) NOT IN ('done', 'left') "
                "ORDER BY position"
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_entry(self, entry_id):
        """Return a single entry by ID, or None if not found."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM rotation_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return self._row_to_dict(row)

    # ------------------------------------------------------------------
    # Task 3: Update, Delete, Exclusive Statuses
    # ------------------------------------------------------------------

    def update_entry(self, entry_id, singer=None, song_artist=None, singers=None):
        """Edit singer and/or song_artist fields.

        When singers is provided, overrides both singer and singers_json.
        When singers is None, preserves existing singers_json.

        Raises ValueError if entry_id not found.
        Returns updated entry dict.
        """
        existing = self.get_entry(entry_id)
        if existing is None:
            raise ValueError(f"Entry {entry_id} not found")

        new_singers_json = existing.get("singers_json")
        if singers is not None:
            singers = [s.strip() for s in singers]
            singer = " & ".join(singers)
            new_singers_json = json.dumps(singers)

        new_singer = singer if singer is not None else existing["singer"]
        new_song_artist = song_artist if song_artist is not None else existing["song_artist"]

        conn = self._get_conn()
        conn.execute(
            "UPDATE rotation_entries "
            "SET singer = ?, song_artist = ?, singers_json = ?, updated_at = datetime('now', 'localtime') "
            "WHERE id = ?",
            (new_singer, new_song_artist, new_singers_json, entry_id),
        )
        conn.commit()
        return self.get_entry(entry_id)

    def delete_entry(self, entry_id):
        """Delete an entry and recompact positions.

        Raises ValueError if entry_id not found.
        """
        existing = self.get_entry(entry_id)
        if existing is None:
            raise ValueError(f"Entry {entry_id} not found")

        deleted_pos = existing["position"]
        conn = self._get_conn()
        conn.execute("DELETE FROM rotation_entries WHERE id = ?", (entry_id,))
        # Shift all entries above the deleted position down by 1
        conn.execute(
            "UPDATE rotation_entries "
            "SET position = position - 1, updated_at = datetime('now', 'localtime') "
            "WHERE position > ?",
            (deleted_pos,),
        )
        conn.commit()

    def update_status(self, entry_id, new_status):
        """Set entry status, enforcing exclusivity rules.

        For 'Now Singing': clears all other entries whose status is in
            {'now singing', 'singing now', 'singing'} back to 'Waiting'.
        For 'Up Next': clears all other entries whose status is in
            {'up next', 'next'} back to 'Waiting'.

        Raises ValueError if entry_id not found.
        """
        if self.get_entry(entry_id) is None:
            raise ValueError(f"Entry {entry_id} not found")

        conn = self._get_conn()
        status_lower = new_status.lower()

        if status_lower in {"now singing", "singing now", "singing"}:
            conn.execute(
                "UPDATE rotation_entries "
                "SET status = 'Waiting', updated_at = datetime('now', 'localtime') "
                "WHERE id != ? AND LOWER(status) IN ('now singing', 'singing now', 'singing')",
                (entry_id,),
            )
        elif status_lower in {"up next", "next"}:
            conn.execute(
                "UPDATE rotation_entries "
                "SET status = 'Waiting', updated_at = datetime('now', 'localtime') "
                "WHERE id != ? AND LOWER(status) IN ('up next', 'next')",
                (entry_id,),
            )

        # Stamp done_at when (and only when) an entry transitions to Done. This
        # is a dedicated "last sang" timestamp: unlike updated_at it is NOT
        # touched by reorders/edits/paid toggles, so get_last_sang_times stays
        # accurate after the rotation is shuffled.
        conn.execute(
            "UPDATE rotation_entries "
            "SET status = ?, updated_at = datetime('now', 'localtime'), "
            "    done_at = CASE WHEN LOWER(?) = 'done' "
            "                   THEN datetime('now', 'localtime') ELSE done_at END "
            "WHERE id = ?",
            (new_status, new_status, entry_id),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Task 4: Move, Stats, File Linking
    # ------------------------------------------------------------------

    def move_entry(self, entry_id, new_position):
        """Move entry to new_position, shifting other entries atomically.

        No-op if already at new_position.
        Raises ValueError if entry_id not found.
        """
        existing = self.get_entry(entry_id)
        if existing is None:
            raise ValueError(f"Entry {entry_id} not found")

        old_pos = existing["position"]
        if old_pos == new_position:
            return

        conn = self._get_conn()

        if new_position > old_pos:
            # Moving down: shift entries in [old_pos+1, new_position] up by 1
            conn.execute(
                "UPDATE rotation_entries "
                "SET position = position - 1, updated_at = datetime('now', 'localtime') "
                "WHERE position > ? AND position <= ?",
                (old_pos, new_position),
            )
        else:
            # Moving up: shift entries in [new_position, old_pos-1] down by 1
            conn.execute(
                "UPDATE rotation_entries "
                "SET position = position + 1, updated_at = datetime('now', 'localtime') "
                "WHERE position >= ? AND position < ?",
                (new_position, old_pos),
            )

        conn.execute(
            "UPDATE rotation_entries "
            "SET position = ?, updated_at = datetime('now', 'localtime') "
            "WHERE id = ?",
            (new_position, entry_id),
        )
        conn.commit()

    def reorder_by_ids(self, ordered_ids):
        """Reassign positions so the given entries take the new order in ``ordered_ids``.

        The entries keep the exact set of position slots they currently occupy (just
        shuffled among themselves), so any rows NOT in ``ordered_ids`` — e.g. Done
        entries interleaved in the position sequence — stay exactly where they are.
        Used by Auto Order to apply a computed reordering of the visible queue in one
        atomic write. Unknown ids are ignored.

        Returns True if any position actually changed.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, position FROM rotation_entries"
        ).fetchall()
        pos_by_id = {r["id"]: r["position"] for r in rows}
        ids = [i for i in ordered_ids if i in pos_by_id]
        if not ids:
            return False
        slots = sorted(pos_by_id[i] for i in ids)
        changed = False
        try:
            for new_pos, eid in zip(slots, ids):
                if pos_by_id[eid] != new_pos:
                    conn.execute(
                        "UPDATE rotation_entries "
                        "SET position = ?, updated_at = datetime('now', 'localtime') "
                        "WHERE id = ?",
                        (new_pos, eid),
                    )
                    changed = True
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return changed

    def get_songs_sung_counts(self):
        """Return a dict mapping singer name → count of 'done' entries tonight.

        Case-insensitive matching on singer name (lowered keys).
        Only counts entries in the current rotation_entries table (not archive).
        When singers_json is set, credits each individual singer separately.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT singer, singers_json FROM rotation_entries WHERE LOWER(status) = 'done'"
        ).fetchall()
        counts = {}
        for row in rows:
            if row["singers_json"] is not None:
                names = json.loads(row["singers_json"])
            else:
                names = [row["singer"]]
            for name in names:
                key = name.lower()
                counts[key] = counts.get(key, 0) + 1
        return counts

    def get_last_sang_times(self):
        """Return a dict mapping singer name → minutes since they last sang.

        "Last sang" = the most recent 'done' entry for that singer tonight,
        using its ``done_at`` (stamped only when the entry was marked done — and
        never touched by reorders/edits, so it stays accurate after the rotation
        is shuffled). The value is whole minutes elapsed from now, floored at 0.

        Case-insensitive matching on singer name (lowered keys), mirroring
        ``get_songs_sung_counts``. When ``singers_json`` is set, each individual
        singer is credited separately. Only entries in the current rotation
        (not the archive) are considered. Singers with no 'done' entry (or whose
        done entry predates the ``done_at`` column) are absent from the result.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT singer, singers_json, "
            "       CAST((julianday('now', 'localtime') "
            "             - julianday(done_at)) * 1440 AS INTEGER) AS mins_ago "
            "FROM rotation_entries "
            "WHERE LOWER(status) = 'done' AND done_at IS NOT NULL"
        ).fetchall()
        result = {}
        for row in rows:
            mins = row["mins_ago"]
            if mins is None:
                continue
            if mins < 0:
                mins = 0
            if row["singers_json"] is not None:
                try:
                    names = json.loads(row["singers_json"])
                except (ValueError, TypeError):
                    names = [row["singer"]]
            else:
                names = [row["singer"]]
            for name in names:
                key = name.lower()
                # Keep the smallest elapsed time = the singer's MOST RECENT song.
                if key not in result or mins < result[key]:
                    result[key] = mins
        return result

    def get_first_entered_times(self):
        """Return a dict mapping singer name → minutes since they FIRST entered
        the rotation tonight (their earliest ``created_at`` across all their
        entries, whether or not they've sung).

        Used to show a "waiting" duration for singers who have not sung yet —
        they've been waiting since their first song was added — and as the
        wait-time fallback in general. The value is whole minutes elapsed from
        now, floored at 0. Case-insensitive matching on singer name (lowered
        keys), mirroring ``get_last_sang_times``; each singer in a group entry
        is credited separately. Only entries in the current rotation (not the
        archive) are considered.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT singer, singers_json, "
            "       CAST((julianday('now', 'localtime') "
            "             - julianday(created_at)) * 1440 AS INTEGER) AS mins_ago "
            "FROM rotation_entries"
        ).fetchall()
        result = {}
        for row in rows:
            mins = row["mins_ago"]
            if mins is None:
                continue
            if mins < 0:
                mins = 0
            if row["singers_json"] is not None:
                try:
                    names = json.loads(row["singers_json"])
                except (ValueError, TypeError):
                    names = [row["singer"]]
            else:
                names = [row["singer"]]
            for name in names:
                key = name.lower()
                # Keep the LARGEST elapsed time = the singer's EARLIEST entry,
                # i.e. when they first joined the rotation tonight.
                if key not in result or mins > result[key]:
                    result[key] = mins
        return result

    def get_singer_stats(self):
        """Return per-singer aggregate stats from all entries (including done/left).

        Returns a list of dicts sorted by first_added (earliest first).
        """
        import json as _json
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM rotation_entries ORDER BY created_at"
        ).fetchall()

        singers = {}
        for row in rows:
            entry = self._row_to_dict(row)
            if entry.get("singers_json"):
                try:
                    names = _json.loads(entry["singers_json"])
                except (ValueError, TypeError):
                    names = [entry["singer"]]
            else:
                names = [entry["singer"]]

            for name in names:
                key = name.strip().lower()
                if key not in singers:
                    singers[key] = {"display_name": name.strip(), "entries": []}
                singers[key]["entries"].append(entry)

        left_set = self.get_left_singer_names()

        result = []
        for key, data in singers.items():
            entries = data["entries"]
            statuses = [e["status"].lower() for e in entries]
            non_done = [s for s in statuses if s != "done"]

            entries_sung = sum(1 for s in statuses if s == "done")
            entries_left = sum(1 for s in statuses if s == "left")
            entries_waiting = len(entries) - entries_sung - entries_left

            if not non_done:
                status = "done"
            elif all(s == "left" for s in non_done):
                status = "left"
            elif all(s in ("on hold (brb)", "on hold") for s in non_done):
                status = "brb"
            else:
                status = "active"

            if key in left_set:
                status = "left"

            trimmed_entries = [
                {
                    "id": e["id"],
                    "song_artist": e["song_artist"],
                    "status": e["status"],
                    "position": e["position"],
                    "created_at": e["created_at"],
                    # done_at is the dedicated "actually sung / marked done"
                    # timestamp; the Songs modal shows it for done entries.
                    "done_at": e.get("done_at"),
                    "updated_at": e.get("updated_at"),
                }
                for e in entries
            ]

            result.append({
                "name": data["display_name"],
                "entries_total": len(entries),
                "entries_sung": entries_sung,
                "entries_waiting": entries_waiting,
                "entries_left": entries_left,
                "first_added": entries[0]["created_at"],
                "has_tipped": any(e.get("paid") for e in entries),
                "status": status,
                "entries": trimmed_entries,
            })

        result.sort(key=lambda s: s["first_added"])
        return result

    def get_stats(self):
        """Return rotation statistics dict.

        Keys: singers (distinct), sung (done count), queued (non-done count),
              started (rotation_meta 'night_started_at' or None).
        """
        conn = self._get_conn()
        singers = conn.execute(
            "SELECT COUNT(DISTINCT singer) FROM rotation_entries"
        ).fetchone()[0]
        sung = conn.execute(
            "SELECT COUNT(*) FROM rotation_entries WHERE LOWER(status) = 'done'"
        ).fetchone()[0]
        queued = conn.execute(
            "SELECT COUNT(*) FROM rotation_entries WHERE LOWER(status) != 'done'"
        ).fetchone()[0]
        meta_row = conn.execute(
            "SELECT value FROM rotation_meta WHERE key = 'night_started_at'"
        ).fetchone()
        started = meta_row[0] if meta_row else None
        return {
            "singers": singers,
            "sung": sung,
            "queued": queued,
            "started": started,
        }

    def link_file(self, entry_id, file_path, duration=None):
        """Set file_path (and optionally duration) on an entry.

        Raises ValueError if entry_id not found.
        """
        if self.get_entry(entry_id) is None:
            raise ValueError(f"Entry {entry_id} not found")
        conn = self._get_conn()
        conn.execute(
            "UPDATE rotation_entries "
            "SET file_path = ?, duration = ?, updated_at = datetime('now', 'localtime') "
            "WHERE id = ?",
            (file_path, duration, entry_id),
        )
        conn.commit()

    def unlink_file(self, entry_id):
        """Reset an entry to a fully-unlinked state.

        Clears file_path/duration AND the download tracking fields
        (download_status/download_id/download_source/url_fallback).
        Without clearing download_status, a stale 'complete' from a
        prior successful download hides the UI's link button after
        unlink. Leaves singer/song_artist/status/gen_status alone.

        Raises ValueError if entry_id not found.
        """
        if self.get_entry(entry_id) is None:
            raise ValueError(f"Entry {entry_id} not found")
        conn = self._get_conn()
        conn.execute(
            "UPDATE rotation_entries "
            "SET file_path = NULL, duration = NULL, "
            "    download_status = NULL, download_id = NULL, "
            "    download_source = NULL, url_fallback = NULL, "
            "    updated_at = datetime('now', 'localtime') "
            "WHERE id = ?",
            (entry_id,),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Download/Prep Tracking
    # ------------------------------------------------------------------

    def set_download_status(self, entry_id, source, status, download_id=None):
        """Set download tracking fields on a rotation entry."""
        if self.get_entry(entry_id) is None:
            raise ValueError(f"Entry {entry_id} not found")
        conn = self._get_conn()
        conn.execute(
            """UPDATE rotation_entries
               SET download_source = ?, download_status = ?, download_id = ?,
                   updated_at = datetime('now', 'localtime')
               WHERE id = ?""",
            (source, status, download_id, entry_id),
        )
        conn.commit()
        return self.get_entry(entry_id)

    def set_url_fallback(self, entry_id, url):
        """Set a URL fallback for browser mode playback."""
        if self.get_entry(entry_id) is None:
            raise ValueError(f"Entry {entry_id} not found")
        conn = self._get_conn()
        conn.execute(
            """UPDATE rotation_entries SET url_fallback = ?, updated_at = datetime('now', 'localtime')
               WHERE id = ?""",
            (url, entry_id),
        )
        conn.commit()
        return self.get_entry(entry_id)

    def set_playability_warning(self, entry_id, warning):
        """Stamp (or clear, with ``None``) the tier-2 playability warning.

        Written by the background render-verification worker after a file is
        linked: if the active renderer cannot actually render video for the
        file, ``warning`` describes why so the KJ sees a ⚠️ before playing it.

        Deliberately does NOT bump ``updated_at`` — that timestamp feeds the
        "time since last sang" pill, and a background stamp must not reset it.
        """
        if self.get_entry(entry_id) is None:
            raise ValueError(f"Entry {entry_id} not found")
        conn = self._get_conn()
        conn.execute(
            "UPDATE rotation_entries SET playability_warning = ? WHERE id = ?",
            (warning, entry_id),
        )
        conn.commit()
        return self.get_entry(entry_id)

    def get_entry_by_download_id(self, download_id):
        """Find a rotation entry by its download queue correlation ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM rotation_entries WHERE download_id = ?", (download_id,)
        ).fetchone()
        return self._row_to_dict(row)

    # ------------------------------------------------------------------
    # Gen Job Tracking
    # ------------------------------------------------------------------

    def set_gen_status(self, entry_id, job_id, status):
        """Set gen job tracking fields on a rotation entry."""
        if self.get_entry(entry_id) is None:
            raise ValueError(f"Entry {entry_id} not found")
        conn = self._get_conn()
        conn.execute(
            """UPDATE rotation_entries
               SET gen_job_id = ?, gen_status = ?,
                   updated_at = datetime('now', 'localtime')
               WHERE id = ?""",
            (job_id, status, entry_id),
        )
        conn.commit()
        return self.get_entry(entry_id)

    def get_active_gen_entries(self):
        """Return entries with active (non-terminal) gen jobs."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM rotation_entries "
            "WHERE gen_job_id IS NOT NULL AND gen_status NOT IN ('complete', 'failed') "
            "ORDER BY position"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_entry_by_gen_job_id(self, job_id):
        """Find a rotation entry by its gen job ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM rotation_entries WHERE gen_job_id = ?", (job_id,)
        ).fetchone()
        return self._row_to_dict(row)

    # ------------------------------------------------------------------
    # Paid flag
    # ------------------------------------------------------------------

    def set_paid(self, entry_id, paid):
        """Set paid priority flag on a rotation entry.

        Raises ValueError if entry_id not found.
        """
        if self.get_entry(entry_id) is None:
            raise ValueError(f"Entry {entry_id} not found")
        conn = self._get_conn()
        conn.execute(
            "UPDATE rotation_entries SET paid = ?, updated_at = datetime('now', 'localtime') "
            "WHERE id = ?",
            (int(bool(paid)), entry_id),
        )
        conn.commit()
        return self.get_entry(entry_id)

    # ------------------------------------------------------------------
    # Priority bias (Auto Order bump up / down)
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_bias(bias):
        """Clamp an arbitrary value to the tri-state {-1, 0, +1}.

        The column is a plain INTEGER (kept extensible), but the only values the
        UI produces — and the only ones Auto Order reasons about — are bump-up
        (+1), normal (0), and bump-down (-1). Anything positive/negative is
        clamped to +1/-1 so a stray value can never over/under-weight the weave.
        """
        try:
            b = int(bias)
        except (TypeError, ValueError):
            return 0
        if b > 0:
            return 1
        if b < 0:
            return -1
        return 0

    def set_priority_bias(self, entry_id, bias):
        """Set the Auto Order priority bias on a single entry.

        ``bias`` is coerced to {-1, 0, +1} (bump down / normal / bump up).
        Raises ValueError if entry_id not found.
        """
        if self.get_entry(entry_id) is None:
            raise ValueError(f"Entry {entry_id} not found")
        conn = self._get_conn()
        conn.execute(
            "UPDATE rotation_entries SET priority_bias = ?, updated_at = datetime('now', 'localtime') "
            "WHERE id = ?",
            (self._coerce_bias(bias), entry_id),
        )
        conn.commit()
        return self.get_entry(entry_id)

    def set_singer_priority_bias(self, singer_name, bias):
        """Set the priority bias for every non-done entry a singer appears in.

        Mirrors ``set_singer_status``: checks both the ``singer`` field and the
        individual names in ``singers_json``; skips entries whose current status
        is 'Done'. ``bias`` is coerced to {-1, 0, +1}.
        """
        b = self._coerce_bias(bias)
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM rotation_entries").fetchall()
        for row in rows:
            entry = self._row_to_dict(row)
            if entry["status"].lower() == "done":
                continue
            if entry.get("singers_json"):
                try:
                    names = json.loads(entry["singers_json"])
                except (ValueError, TypeError):
                    names = [entry["singer"]]
            else:
                names = [entry["singer"]]
            if singer_name in names:
                conn.execute(
                    "UPDATE rotation_entries "
                    "SET priority_bias = ?, updated_at = datetime('now', 'localtime') "
                    "WHERE id = ?",
                    (b, entry["id"]),
                )
        conn.commit()

    # ------------------------------------------------------------------
    # Singer action methods
    # ------------------------------------------------------------------

    def rename_singer(self, old_name, new_name):
        """Rename all entries belonging to old_name to new_name.

        For multi-singer entries (singers_json), replaces old_name within the
        JSON array and regenerates the display singer string.
        For legacy single-singer entries, updates singer directly if it matches.
        """
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM rotation_entries").fetchall()
        for row in rows:
            entry = self._row_to_dict(row)
            if entry.get("singers_json"):
                try:
                    names = json.loads(entry["singers_json"])
                except (ValueError, TypeError):
                    names = [entry["singer"]]
                if old_name in names:
                    names = [new_name if n == old_name else n for n in names]
                    new_display = " & ".join(names)
                    conn.execute(
                        "UPDATE rotation_entries "
                        "SET singer = ?, singers_json = ?, updated_at = datetime('now', 'localtime') "
                        "WHERE id = ?",
                        (new_display, json.dumps(names), entry["id"]),
                    )
            else:
                if entry["singer"] == old_name:
                    conn.execute(
                        "UPDATE rotation_entries "
                        "SET singer = ?, updated_at = datetime('now', 'localtime') "
                        "WHERE id = ?",
                        (new_name, entry["id"]),
                    )
        conn.commit()

        # Migrate left-singer meta if applicable
        left = self.get_left_singer_names()
        old_key = old_name.strip().lower()
        if old_key in left:
            left.discard(old_key)
            left.add(new_name.strip().lower())
            self._set_left_singer_names(left)

    def rename_singer_in_entries(self, old_name, new_name, entry_ids):
        """Rename ``old_name`` → ``new_name`` within a SPECIFIC set of entries.

        Like ``rename_singer`` but scoped to ``entry_ids`` (and tolerant of ids
        that no longer exist — silently skipped rather than raising). Used by the
        singer self-service rename (`/sing/rename`), where the device may only
        rewrite the entries it proved ownership of via edit_token — never the
        whole rotation. Case-insensitive match on ``old_name``; multi-singer
        (singers_json) entries have only the matching name replaced, preserving
        duet partners.
        """
        old_key = (old_name or "").strip().lower()
        new_name = (new_name or "").strip()
        if not old_key or not new_name or not entry_ids:
            return
        conn = self._get_conn()
        for entry_id in entry_ids:
            entry = self.get_entry(entry_id)
            if entry is None:
                continue
            if entry.get("singers_json"):
                try:
                    names = json.loads(entry["singers_json"])
                except (ValueError, TypeError):
                    names = [entry["singer"]]
                if not any((n or "").strip().lower() == old_key for n in names):
                    continue
                new_names = [
                    new_name if (n or "").strip().lower() == old_key else n
                    for n in names
                ]
                new_display = " & ".join(new_names)
                conn.execute(
                    "UPDATE rotation_entries "
                    "SET singer = ?, singers_json = ?, updated_at = datetime('now', 'localtime') "
                    "WHERE id = ?",
                    (new_display, json.dumps(new_names), entry_id),
                )
            elif (entry["singer"] or "").strip().lower() == old_key:
                conn.execute(
                    "UPDATE rotation_entries "
                    "SET singer = ?, updated_at = datetime('now', 'localtime') "
                    "WHERE id = ?",
                    (new_name, entry_id),
                )
        conn.commit()

    def merge_singers(self, source_name, target_name):
        """Merge source_name into target_name across all entries.

        Same as rename_singer but also deduplicates: if replacing source with
        target would create a duplicate in singers_json, removes the duplicate.
        """
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM rotation_entries").fetchall()
        for row in rows:
            entry = self._row_to_dict(row)
            if entry.get("singers_json"):
                try:
                    names = json.loads(entry["singers_json"])
                except (ValueError, TypeError):
                    names = [entry["singer"]]
                if source_name in names:
                    # Replace source with target and deduplicate
                    new_names = []
                    seen = set()
                    for n in names:
                        effective = target_name if n == source_name else n
                        if effective not in seen:
                            new_names.append(effective)
                            seen.add(effective)
                    new_display = " & ".join(new_names)
                    conn.execute(
                        "UPDATE rotation_entries "
                        "SET singer = ?, singers_json = ?, updated_at = datetime('now', 'localtime') "
                        "WHERE id = ?",
                        (new_display, json.dumps(new_names), entry["id"]),
                    )
            else:
                if entry["singer"] == source_name:
                    conn.execute(
                        "UPDATE rotation_entries "
                        "SET singer = ?, updated_at = datetime('now', 'localtime') "
                        "WHERE id = ?",
                        (target_name, entry["id"]),
                    )
        conn.commit()

        # Drop source from left-singer meta; target's state is untouched
        self.unmark_singer_left(source_name)

    def split_singer(self, source_name, new_name, entry_ids):
        """Reassign specific entries from source_name to new_name.

        For multi-singer entries (singers_json set), replace source_name with
        new_name in the array (case-insensitive match on source_name), preserving
        other names. If the resulting array has a single name, collapse to the
        legacy single-singer shape (singers_json = NULL).

        For legacy single-singer entries whose `singer` matches source_name
        case-insensitively, overwrite singer = new_name.

        Entries whose content doesn't include source_name are silently skipped.

        Raises ValueError if any entry_id is not found.
        """
        if not entry_ids:
            return
        source_key = source_name.strip().lower()
        new_name = new_name.strip()
        conn = self._get_conn()

        for entry_id in entry_ids:
            existing = self.get_entry(entry_id)
            if existing is None:
                raise ValueError(f"Entry {entry_id} not found")

            singers_json_raw = existing.get("singers_json")
            if singers_json_raw:
                try:
                    names = json.loads(singers_json_raw)
                except (ValueError, TypeError):
                    names = [existing["singer"]]
                # Case-insensitive replacement of source with new_name
                if not any(n.strip().lower() == source_key for n in names):
                    continue  # source not present; skip
                new_names = [new_name if n.strip().lower() == source_key else n for n in names]
                if len(new_names) == 1:
                    conn.execute(
                        "UPDATE rotation_entries "
                        "SET singer = ?, singers_json = NULL, "
                        "    updated_at = datetime('now', 'localtime') "
                        "WHERE id = ?",
                        (new_names[0], entry_id),
                    )
                else:
                    conn.execute(
                        "UPDATE rotation_entries "
                        "SET singer = ?, singers_json = ?, "
                        "    updated_at = datetime('now', 'localtime') "
                        "WHERE id = ?",
                        (" & ".join(new_names), json.dumps(new_names), entry_id),
                    )
            else:
                if existing["singer"].strip().lower() != source_key:
                    continue  # legacy single-singer but name doesn't match
                conn.execute(
                    "UPDATE rotation_entries "
                    "SET singer = ?, updated_at = datetime('now', 'localtime') "
                    "WHERE id = ?",
                    (new_name, entry_id),
                )

        conn.commit()

    def set_singer_status(self, singer_name, new_status):
        """Set status for all non-done entries where the singer appears.

        Checks both the singer field and individual names in singers_json.
        Skips entries whose current status is 'Done'.
        """
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM rotation_entries").fetchall()
        for row in rows:
            entry = self._row_to_dict(row)
            if entry["status"].lower() == "done":
                continue
            # Determine if this singer appears in the entry
            if entry.get("singers_json"):
                try:
                    names = json.loads(entry["singers_json"])
                except (ValueError, TypeError):
                    names = [entry["singer"]]
            else:
                names = [entry["singer"]]
            if singer_name in names:
                conn.execute(
                    "UPDATE rotation_entries "
                    "SET status = ?, updated_at = datetime('now', 'localtime') "
                    "WHERE id = ?",
                    (new_status, entry["id"]),
                )
        conn.commit()

    # ------------------------------------------------------------------
    # Left-singers meta (session-scoped list of names who have left)
    # ------------------------------------------------------------------

    _LEFT_META_KEY = "left_singers_json"

    def get_left_singer_names(self):
        """Return the set of lowercased singer names marked as 'left'.

        Backed by rotation_meta.left_singers_json. Returns an empty set if
        the key is unset or unparseable (malformed JSON is treated as empty).
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT value FROM rotation_meta WHERE key = ?",
            (self._LEFT_META_KEY,),
        ).fetchone()
        if row is None or row[0] is None:
            return set()
        try:
            names = json.loads(row[0])
        except (ValueError, TypeError):
            return set()
        return set(names) if isinstance(names, list) else set()

    def _set_left_singer_names(self, names):
        """Internal: overwrite the left-singers meta list."""
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO rotation_meta (key, value) VALUES (?, ?)",
            (self._LEFT_META_KEY, json.dumps(sorted(names))),
        )
        conn.commit()

    def mark_singer_left(self, name):
        """Add a singer name to the left set (case-insensitive, idempotent)."""
        key = name.strip().lower()
        if not key:
            return
        names = self.get_left_singer_names()
        names.add(key)
        self._set_left_singer_names(names)

    def unmark_singer_left(self, name):
        """Remove a singer name from the left set (case-insensitive, idempotent)."""
        key = name.strip().lower()
        if not key:
            return
        names = self.get_left_singer_names()
        names.discard(key)
        self._set_left_singer_names(names)

    # ------------------------------------------------------------------
    # Task 5: Archive and get_all_entries
    # ------------------------------------------------------------------

    def archive(self, starter_singer="Andrew", starter_song="First Song of the Night"):
        """Move all current entries to rotation_archive, reset rotation.

        Steps:
        1. Copy all rotation_entries into rotation_archive with tonight's date.
        2. Delete all rotation_entries.
        3. Create a starter entry (starter_singer / starter_song).
        4. Record night_started_at in rotation_meta.

        Returns the number of entries archived (excluding the new starter).
        """
        conn = self._get_conn()
        night_date = conn.execute(
            "SELECT date('now', 'localtime')"
        ).fetchone()[0]

        # Count entries before archiving
        count = conn.execute(
            "SELECT COUNT(*) FROM rotation_entries"
        ).fetchone()[0]

        # Copy to archive
        conn.execute(
            "INSERT INTO rotation_archive "
            "    (night_date, singer, song_artist, status, notes, position, file_path, duration, created_at) "
            "SELECT ?, singer, song_artist, status, notes, position, file_path, duration, created_at "
            "FROM rotation_entries",
            (night_date,),
        )

        # Clear rotation
        conn.execute("DELETE FROM rotation_entries")

        # Clear left-singers meta — it's session-scoped
        conn.execute(
            "DELETE FROM rotation_meta WHERE key = ?",
            (self._LEFT_META_KEY,),
        )

        # NOTE: deliberately do NOT reset the AUTOINCREMENT counter here.
        # rotation_entry ids must stay globally monotonic across nights:
        # sing_requests (in the same DB) keep their linked_entry_id, and
        # recycling ids back to 1 made a fresh entry phantom-match a PRIOR
        # night's request — attaching the wrong singer's phone to SMS/push.
        # Phone resolution is also night-scoped as defense in depth, but not
        # reusing ids removes the collision at its source.

        # Record night start time
        conn.execute(
            "INSERT OR REPLACE INTO rotation_meta (key, value) "
            "VALUES ('night_started_at', datetime('now', 'localtime'))",
        )

        conn.commit()

        # Add starter entry (calls add_entry which does its own commit)
        self.add_entry(starter_singer, song_artist=starter_song)

        return count

    def get_all_entries(self):
        """Return all entries regardless of status (alias for get_entries(include_done=True)).

        Used for Sheet sync to push the complete rotation state.
        """
        return self.get_entries(include_done=True)

    def restore_entries(self, entries, preserve_tracking=False):
        """Atomically replace all rotation entries with the given snapshot.

        Used by the undo/redo system. Preserves original entry IDs.

        ``created_at`` is carried through from the snapshot when present (falling
        back to the table default only when absent) so a restore never rewrites
        the rotation's timeline — the bug that stamped every row with the restore
        time on 2026-05-28.

        When ``preserve_tracking`` is True, the *live* download/file-link fields
        (see ``_TRACKING_FIELDS``) are kept for any entry whose id still exists,
        instead of being reverted to the snapshot's values. This keeps an undo
        focused on the human-edited columns and stops it breaking a download or
        file link that completed in the background after the snapshot was taken.
        """
        conn = self._get_conn()

        live = {}
        if preserve_tracking:
            for row in conn.execute("SELECT * FROM rotation_entries").fetchall():
                d = dict(row)
                live[d["id"]] = d

        try:
            conn.execute("DELETE FROM rotation_entries")
            conn.execute(
                "DELETE FROM sqlite_sequence WHERE name = 'rotation_entries'"
            )
            for e in entries:
                track = {f: e.get(f) for f in self._TRACKING_FIELDS}
                if preserve_tracking and e.get("id") in live:
                    src = live[e["id"]]
                    for f in self._TRACKING_FIELDS:
                        track[f] = src.get(f)

                cols = [
                    "id", "singer", "song_artist", "status", "notes", "position",
                    "file_path", "duration", "download_source", "download_status",
                    "download_id", "url_fallback", "gen_job_id", "gen_status",
                    "playability_warning", "singers_json", "paid",
                    "priority_bias",
                ]
                vals = [
                    e["id"], e["singer"], e["song_artist"], e["status"],
                    e.get("notes", ""), e["position"],
                    track["file_path"], track["duration"],
                    track["download_source"], track["download_status"],
                    track["download_id"], track["url_fallback"],
                    track["gen_job_id"], track["gen_status"],
                    track["playability_warning"],
                    e.get("singers_json"), int(bool(e.get("paid", 0))),
                    self._coerce_bias(e.get("priority_bias", 0)),
                ]
                # Preserve created_at when the snapshot carries it.
                if e.get("created_at"):
                    cols.append("created_at")
                    vals.append(e["created_at"])
                # Preserve done_at (the last-sang timestamp) when present, so an
                # undo/redo doesn't wipe a restored Done entry's last-sang time.
                if e.get("done_at"):
                    cols.append("done_at")
                    vals.append(e["done_at"])

                placeholders = ", ".join(["?"] * len(vals))
                conn.execute(
                    f"INSERT INTO rotation_entries ({', '.join(cols)}, updated_at) "
                    f"VALUES ({placeholders}, datetime('now', 'localtime'))",
                    vals,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Revision counter + server-side undo/redo history
    # ------------------------------------------------------------------

    def get_rev(self):
        """Return the monotonic rotation revision counter (0 if unset)."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT value FROM rotation_meta WHERE key = 'rotation_rev'"
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def bump_rev(self):
        """Increment and return the rotation revision counter."""
        conn = self._get_conn()
        new_rev = self.get_rev() + 1
        conn.execute(
            "INSERT OR REPLACE INTO rotation_meta (key, value) "
            "VALUES ('rotation_rev', ?)",
            (str(new_rev),),
        )
        conn.commit()
        return new_rev

    def _snapshot_json(self, conn):
        """Serialise the full current rotation (all statuses) to JSON."""
        rows = conn.execute(
            "SELECT * FROM rotation_entries ORDER BY position"
        ).fetchall()
        return json.dumps([dict(r) for r in rows])

    def _stack_count(self, conn, stack):
        return conn.execute(
            "SELECT COUNT(*) FROM rotation_history WHERE stack = ?", (stack,)
        ).fetchone()[0]

    def _push_history(self, conn, stack, label, entries_json):
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM rotation_history WHERE stack = ?",
            (stack,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO rotation_history (stack, seq, label, rev, entries_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (stack, seq, label, self.get_rev(), entries_json),
        )

    def _prune_stack(self, conn, stack, max_size):
        ids = conn.execute(
            "SELECT id FROM rotation_history WHERE stack = ? ORDER BY seq DESC",
            (stack,),
        ).fetchall()
        for extra in ids[max_size:]:
            conn.execute("DELETE FROM rotation_history WHERE id = ?", (extra[0],))

    def checkpoint(self, label=None):
        """Snapshot the current rotation onto the undo stack before a mutation.

        Clears the redo stack (a new action invalidates any redo history) and
        prunes the undo stack to ``MAX_HISTORY``.
        """
        conn = self._get_conn()
        self._push_history(conn, "undo", label, self._snapshot_json(conn))
        conn.execute("DELETE FROM rotation_history WHERE stack = 'redo'")
        self._prune_stack(conn, "undo", self.MAX_HISTORY)
        conn.commit()

    def history_counts(self):
        """Return undo/redo depth and the label of each stack's top entry."""
        conn = self._get_conn()
        top_undo = conn.execute(
            "SELECT label FROM rotation_history WHERE stack = 'undo' "
            "ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        top_redo = conn.execute(
            "SELECT label FROM rotation_history WHERE stack = 'redo' "
            "ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return {
            "undo": self._stack_count(conn, "undo"),
            "redo": self._stack_count(conn, "redo"),
            "undo_label": top_undo["label"] if top_undo else None,
            "redo_label": top_redo["label"] if top_redo else None,
        }

    def _peek(self, stack):
        conn = self._get_conn()
        row = conn.execute(
            "SELECT label, entries_json FROM rotation_history WHERE stack = ? "
            "ORDER BY seq DESC LIMIT 1",
            (stack,),
        ).fetchone()
        if row is None:
            return None
        return {"label": row["label"], "entries": json.loads(row["entries_json"])}

    def peek_undo(self):
        """Return the snapshot that an undo would restore, without applying it."""
        return self._peek("undo")

    def peek_redo(self):
        """Return the snapshot that a redo would restore, without applying it."""
        return self._peek("redo")

    def _apply_from(self, from_stack, to_stack):
        """Pop ``from_stack``, push current state to ``to_stack``, restore the pop."""
        conn = self._get_conn()
        top = conn.execute(
            "SELECT id, label, entries_json FROM rotation_history "
            "WHERE stack = ? ORDER BY seq DESC LIMIT 1",
            (from_stack,),
        ).fetchone()
        if top is None:
            return {"ok": False, "reason": "empty"}

        current_json = self._snapshot_json(conn)
        self.restore_entries(json.loads(top["entries_json"]), preserve_tracking=True)
        conn.execute("DELETE FROM rotation_history WHERE id = ?", (top["id"],))
        self._push_history(conn, to_stack, top["label"], current_json)
        conn.commit()
        return {
            "ok": True,
            "label": top["label"],
            "undo": self._stack_count(conn, "undo"),
            "redo": self._stack_count(conn, "redo"),
        }

    def undo(self):
        """Restore the most recent undo snapshot; current state goes to redo."""
        return self._apply_from("undo", "redo")

    def redo(self):
        """Re-apply the most recent redo snapshot; current state goes to undo."""
        return self._apply_from("redo", "undo")

    def clear_history(self):
        """Drop all undo/redo history (called when a night is archived/reset)."""
        conn = self._get_conn()
        conn.execute("DELETE FROM rotation_history")
        conn.commit()
