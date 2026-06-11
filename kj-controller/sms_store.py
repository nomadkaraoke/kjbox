"""SmsStore: append-only audit log of outbound SMS sends.

Lives in ``rotation.db`` alongside rotation + sing requests so backups and
archives stay unified. Per-thread connection pattern (same as RotationStore
and SingStore after the 2026-05-01 outage fix) — one shared connection
across Flask + background threads caused implicit-transaction lockups.
"""

import sqlite3
import threading


class SmsStore:
    """Append-only audit log of outbound SMS sends."""

    _MEMORY = ":memory:"

    def __init__(self, db_path):
        self.db_path = db_path
        self._local = threading.local()
        self._memory_conn = None
        self._memory_lock = threading.Lock()
        self.init_schema()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _open_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA cache_size=-2048")  # 2 MB — small append-only log
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _get_conn(self):
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
        conn = self._get_conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sms_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                sent_at             TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                rotation_entry_id   INTEGER,
                sing_request_id     INTEGER,
                phone_e164          TEXT NOT NULL,
                body                TEXT NOT NULL,
                status              TEXT NOT NULL,
                telnyx_message_id   TEXT,
                error               TEXT,
                kj_user_agent       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sms_log_entry
                ON sms_log(rotation_entry_id);
            CREATE INDEX IF NOT EXISTS idx_sms_log_sent_at
                ON sms_log(sent_at);
            CREATE INDEX IF NOT EXISTS idx_sms_log_telnyx_id
                ON sms_log(telnyx_message_id);

            -- Opt-out registry. Populated by inbound STOP webhooks; consulted
            -- by the send path so the KJ can't text a singer who replied STOP.
            -- Keyed by E.164 so it survives across events/nights.
            CREATE TABLE IF NOT EXISTS sms_opt_outs (
                phone_e164    TEXT PRIMARY KEY,
                opted_out_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                keyword       TEXT
            );
            """
        )
        conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row):
        return None if row is None else dict(row)

    def record_send(
        self,
        *,
        rotation_entry_id,
        sing_request_id,
        phone_e164,
        body,
        status,
        telnyx_message_id=None,
        error=None,
        kj_user_agent=None,
    ):
        """Insert a log row. ``status`` must be 'sent' or 'failed'."""
        if status not in ("sent", "failed"):
            raise ValueError(f"status must be 'sent' or 'failed', got {status!r}")
        if not phone_e164:
            raise ValueError("phone_e164 is required")
        if not body:
            raise ValueError("body is required")
        conn = self._get_conn()
        cur = conn.execute(
            """
            INSERT INTO sms_log
                (rotation_entry_id, sing_request_id, phone_e164, body,
                 status, telnyx_message_id, error, kj_user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rotation_entry_id,
                sing_request_id,
                phone_e164,
                body,
                status,
                telnyx_message_id,
                error,
                kj_user_agent,
            ),
        )
        conn.commit()
        return self.get_log(cur.lastrowid)

    def update_status_by_telnyx_id(self, telnyx_message_id, status, error=None):
        """Update an outbound row's status from a Telnyx delivery receipt.

        Matches on ``telnyx_message_id`` (the id returned at send time). Returns
        the number of rows updated. A blank/None id matches nothing — failed
        sends store a NULL id and must never be blanket-overwritten by a DLR
        that arrives without one.
        """
        if not telnyx_message_id:
            return 0
        conn = self._get_conn()
        cur = conn.execute(
            "UPDATE sms_log SET status = ?, error = ? "
            "WHERE telnyx_message_id = ?",
            (status, error, telnyx_message_id),
        )
        conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------------
    # Opt-out registry
    # ------------------------------------------------------------------

    def record_opt_out(self, phone_e164, keyword=None):
        """Mark ``phone_e164`` as opted out (idempotent)."""
        if not phone_e164:
            return
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO sms_opt_outs (phone_e164, keyword) VALUES (?, ?) "
            "ON CONFLICT(phone_e164) DO UPDATE SET "
            "opted_out_at = datetime('now', 'localtime'), keyword = excluded.keyword",
            (phone_e164, keyword),
        )
        conn.commit()

    def clear_opt_out(self, phone_e164):
        """Remove an opt-out (inbound START/UNSTOP)."""
        if not phone_e164:
            return
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM sms_opt_outs WHERE phone_e164 = ?", (phone_e164,)
        )
        conn.commit()

    def is_opted_out(self, phone_e164):
        """True iff ``phone_e164`` is in the opt-out registry."""
        if not phone_e164:
            return False
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM sms_opt_outs WHERE phone_e164 = ?", (phone_e164,)
        ).fetchone()
        return row is not None

    def get_log(self, log_id):
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM sms_log WHERE id = ?", (log_id,)
        ).fetchone()
        return self._row_to_dict(row)

    def get_latest_for_entry(self, rotation_entry_id):
        """Return the most recent send (any status) for ``rotation_entry_id``.

        Used by the rotation row renderer to show the ✉ sent marker.
        """
        conn = self._get_conn()
        row = conn.execute(
            """
            SELECT * FROM sms_log
            WHERE rotation_entry_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (rotation_entry_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def get_latest_for_entries(self, rotation_entry_ids):
        """Bulk lookup — one query for the whole rotation list.

        Returns a dict {entry_id: latest_row_dict}. Entries with no sends are
        absent from the dict.
        """
        if not rotation_entry_ids:
            return {}
        conn = self._get_conn()
        placeholders = ",".join("?" * len(rotation_entry_ids))
        rows = conn.execute(
            f"""
            SELECT sl.*
            FROM sms_log sl
            JOIN (
                SELECT rotation_entry_id, MAX(id) AS max_id
                FROM sms_log
                WHERE rotation_entry_id IN ({placeholders})
                GROUP BY rotation_entry_id
            ) latest
              ON latest.max_id = sl.id
            """,
            tuple(rotation_entry_ids),
        ).fetchall()
        return {row["rotation_entry_id"]: dict(row) for row in rows}
