"""RotationStore: SQLite-backed storage for karaoke singer rotation entries."""

import json
import sqlite3


class RotationStore:
    """Pure local SQLite storage for rotation entries.

    Follows the same connection pattern as ExternalCatalog in catalog.py:
    WAL mode, check_same_thread=False, Row factory, cache tuning.
    """

    def __init__(self, db_path):
        self.db_path = db_path
        self._conn = None
        self.init_schema()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _get_conn(self):
        """Lazy SQLite connection with WAL mode and optimised settings."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA cache_size=-8192")   # 8 MB
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

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
                file_path   TEXT,
                duration    INTEGER,
                download_source TEXT DEFAULT NULL,
                download_status TEXT DEFAULT NULL,
                download_id TEXT DEFAULT NULL,
                url_fallback TEXT DEFAULT NULL,
                gen_job_id  TEXT DEFAULT NULL,
                gen_status  TEXT DEFAULT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
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
        """)

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
            ("singers_json", "TEXT DEFAULT NULL"),
        ]
        for col_name, col_type in migrations:
            if col_name not in existing_cols:
                conn.execute(
                    f"ALTER TABLE rotation_entries ADD COLUMN {col_name} {col_type}"
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

        conn.execute(
            "UPDATE rotation_entries "
            "SET status = ?, updated_at = datetime('now', 'localtime') "
            "WHERE id = ?",
            (new_status, entry_id),
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

            result.append({
                "name": data["display_name"],
                "entries_total": len(entries),
                "entries_sung": entries_sung,
                "entries_waiting": entries_waiting,
                "entries_left": entries_left,
                "first_added": entries[0]["created_at"],
                "has_tipped": any(e.get("paid") for e in entries),
                "status": status,
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
        """Set file_path and duration to NULL on an entry.

        Raises ValueError if entry_id not found.
        """
        if self.get_entry(entry_id) is None:
            raise ValueError(f"Entry {entry_id} not found")
        conn = self._get_conn()
        conn.execute(
            "UPDATE rotation_entries "
            "SET file_path = NULL, duration = NULL, "
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

        # Reset the AUTOINCREMENT counter so IDs restart cleanly (optional but tidy)
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name = 'rotation_entries'"
        )

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

    def restore_entries(self, entries):
        """Atomically replace all rotation entries with the given snapshot.

        Used by the undo/redo system. Preserves original entry IDs.
        Each entry dict must have: id, singer, song_artist, status, notes,
        position, file_path, duration, download_source, download_status,
        download_id, url_fallback, gen_job_id, gen_status.
        """
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM rotation_entries")
            conn.execute(
                "DELETE FROM sqlite_sequence WHERE name = 'rotation_entries'"
            )
            for e in entries:
                conn.execute(
                    "INSERT INTO rotation_entries "
                    "(id, singer, song_artist, status, notes, position, "
                    " file_path, duration, download_source, download_status, "
                    " download_id, url_fallback, gen_job_id, gen_status, "
                    " singers_json, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "        datetime('now', 'localtime'))",
                    (
                        e["id"], e["singer"], e["song_artist"], e["status"],
                        e.get("notes", ""), e["position"],
                        e.get("file_path"), e.get("duration"),
                        e.get("download_source"), e.get("download_status"),
                        e.get("download_id"), e.get("url_fallback"),
                        e.get("gen_job_id"), e.get("gen_status"),
                        e.get("singers_json"),
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
