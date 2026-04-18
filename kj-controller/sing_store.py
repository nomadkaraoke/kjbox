"""SingStore: SQLite-backed storage for public singer requests + event token helpers.

Shares the rotation.db file so backups and archives stay unified. Stores
pending-review requests coming in from the public `/sing/` request form and
provides helpers for the short-lived event token that gates that form.
"""

import json
import secrets
import sqlite3


# Meta keys stored in rotation_meta (same table RotationStore uses)
TOKEN_KEY = "request_token"
ENABLED_KEY = "request_token_enabled"
AUTO_APPROVE_KEY = "request_auto_approve"

# Token length: ~16 URL-safe chars — short enough for a QR, long enough to resist
# guessing. `secrets.token_urlsafe(12)` yields 16 chars.
TOKEN_BYTES = 12


class SingStore:
    """Local SQLite storage for public sing requests + event token.

    Follows the same connection pattern as RotationStore: WAL mode,
    check_same_thread=False, Row factory, cache tuning.
    """

    def __init__(self, db_path):
        self.db_path = db_path
        self._conn = None
        self.init_schema()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _get_conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA cache_size=-8192")  # 8 MB
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init_schema(self):
        """Create the sing_requests table and ensure rotation_meta exists."""
        conn = self._get_conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sing_requests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                token           TEXT NOT NULL,
                singer_name     TEXT NOT NULL,
                phone           TEXT NOT NULL,
                song_artist     TEXT NOT NULL DEFAULT '',
                song_title      TEXT NOT NULL DEFAULT '',
                source_type     TEXT NOT NULL,
                source_ref      TEXT,
                source_meta     TEXT,
                notes           TEXT NOT NULL DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'pending',
                rejected_reason TEXT,
                reviewed_at     TEXT,
                linked_entry_id INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_sing_requests_status
                ON sing_requests (status);
            CREATE INDEX IF NOT EXISTS idx_sing_requests_token
                ON sing_requests (token);

            CREATE TABLE IF NOT EXISTS rotation_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row):
        if row is None:
            return None
        return dict(row)

    def _get_meta(self, key, default=None):
        conn = self._get_conn()
        row = conn.execute(
            "SELECT value FROM rotation_meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else default

    def _set_meta(self, key, value):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO rotation_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------

    def get_token(self):
        """Return the current event token, or None if unset."""
        return self._get_meta(TOKEN_KEY)

    def regenerate_token(self):
        """Generate a fresh token, store it, return it."""
        new_token = secrets.token_urlsafe(TOKEN_BYTES)
        self._set_meta(TOKEN_KEY, new_token)
        return new_token

    def ensure_token(self):
        """Return the current token, generating one if absent."""
        tok = self.get_token()
        if tok:
            return tok
        return self.regenerate_token()

    def is_enabled(self):
        """Return True if public requests are currently enabled."""
        return self._get_meta(ENABLED_KEY, "1") == "1"

    def set_enabled(self, enabled):
        self._set_meta(ENABLED_KEY, "1" if enabled else "0")

    def is_auto_approve(self):
        """Return True if incoming requests skip the review queue."""
        return self._get_meta(AUTO_APPROVE_KEY, "0") == "1"

    def set_auto_approve(self, enabled):
        self._set_meta(AUTO_APPROVE_KEY, "1" if enabled else "0")

    # ------------------------------------------------------------------
    # Request CRUD
    # ------------------------------------------------------------------

    def create_request(
        self,
        singer_name,
        phone,
        song_artist="",
        song_title="",
        source_type="local",
        source_ref=None,
        source_meta=None,
        notes="",
        token=None,
    ):
        """Insert a new pending request and return the created row as a dict."""
        if not singer_name:
            raise ValueError("singer_name is required")
        if not phone:
            raise ValueError("phone is required")
        if not source_type:
            raise ValueError("source_type is required")

        request_token = token if token is not None else (self.get_token() or "")
        meta_json = json.dumps(source_meta) if source_meta is not None else None
        conn = self._get_conn()
        cur = conn.execute(
            """
            INSERT INTO sing_requests
                (token, singer_name, phone, song_artist, song_title,
                 source_type, source_ref, source_meta, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_token,
                singer_name.strip(),
                phone.strip(),
                song_artist.strip(),
                song_title.strip(),
                source_type,
                source_ref,
                meta_json,
                notes.strip(),
            ),
        )
        conn.commit()
        return self.get_request(cur.lastrowid)

    def get_request(self, request_id):
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM sing_requests WHERE id = ?", (request_id,)
        ).fetchone()
        return self._row_to_dict(row)

    def list_requests(self, status=None, token=None, limit=None):
        """List requests ordered newest first. Optionally filter by status/token."""
        conn = self._get_conn()
        clauses = []
        params = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if token is not None:
            clauses.append("token = ?")
            params.append(token)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_clause = f"LIMIT {int(limit)}" if limit else ""
        rows = conn.execute(
            f"SELECT * FROM sing_requests {where} "
            f"ORDER BY id DESC {limit_clause}",
            params,
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count_by_status(self):
        """Return a dict {status: count} for all statuses present."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM sing_requests GROUP BY status"
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def count_pending(self):
        return self.count_by_status().get("pending", 0)

    def update_request(
        self,
        request_id,
        singer_name=None,
        song_artist=None,
        song_title=None,
        source_type=None,
        source_ref=None,
        source_meta=None,
        notes=None,
    ):
        """Edit mutable fields on a request. Returns the updated row."""
        existing = self.get_request(request_id)
        if existing is None:
            raise ValueError(f"Request {request_id} not found")

        new_vals = {
            "singer_name": singer_name.strip() if singer_name is not None else existing["singer_name"],
            "song_artist": song_artist.strip() if song_artist is not None else existing["song_artist"],
            "song_title": song_title.strip() if song_title is not None else existing["song_title"],
            "source_type": source_type if source_type is not None else existing["source_type"],
            "source_ref": source_ref if source_ref is not None else existing["source_ref"],
            "source_meta": (
                json.dumps(source_meta) if source_meta is not None
                else existing["source_meta"]
            ),
            "notes": notes.strip() if notes is not None else existing["notes"],
        }
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE sing_requests
               SET singer_name = ?, song_artist = ?, song_title = ?,
                   source_type = ?, source_ref = ?, source_meta = ?, notes = ?
             WHERE id = ?
            """,
            (
                new_vals["singer_name"], new_vals["song_artist"], new_vals["song_title"],
                new_vals["source_type"], new_vals["source_ref"], new_vals["source_meta"],
                new_vals["notes"], request_id,
            ),
        )
        conn.commit()
        return self.get_request(request_id)

    def mark_approved(self, request_id, linked_entry_id=None):
        """Set status=approved, reviewed_at=now, linked_entry_id if provided."""
        if self.get_request(request_id) is None:
            raise ValueError(f"Request {request_id} not found")
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE sing_requests
               SET status = 'approved',
                   reviewed_at = datetime('now', 'localtime'),
                   linked_entry_id = ?
             WHERE id = ?
            """,
            (linked_entry_id, request_id),
        )
        conn.commit()
        return self.get_request(request_id)

    def mark_rejected(self, request_id, reason=None):
        if self.get_request(request_id) is None:
            raise ValueError(f"Request {request_id} not found")
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE sing_requests
               SET status = 'rejected',
                   reviewed_at = datetime('now', 'localtime'),
                   rejected_reason = ?
             WHERE id = ?
            """,
            (reason, request_id),
        )
        conn.commit()
        return self.get_request(request_id)

    def set_linked_entry(self, request_id, linked_entry_id):
        """Update the linked rotation entry id without changing status."""
        if self.get_request(request_id) is None:
            raise ValueError(f"Request {request_id} not found")
        conn = self._get_conn()
        conn.execute(
            "UPDATE sing_requests SET linked_entry_id = ? WHERE id = ?",
            (linked_entry_id, request_id),
        )
        conn.commit()
        return self.get_request(request_id)
