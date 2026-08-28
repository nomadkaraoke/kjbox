"""SingStore: SQLite-backed storage for public singer requests + event token helpers.

Shares the rotation.db file so backups and archives stay unified. Stores
pending-review requests coming in from the public `/sing/` request form and
provides helpers for the short-lived event token that gates that form.
"""

import json
import secrets
import sqlite3
import threading


# Meta keys stored in rotation_meta (same table RotationStore uses)
TOKEN_KEY = "request_token"
ENABLED_KEY = "request_token_enabled"
AUTO_APPROVE_KEY = "request_auto_approve"
ACCEPT_MAKE_REQUESTS_KEY = "sing_accept_make_requests"
# When on, the KJ controller auto-texts the up-next singer 20s after the KJ
# starts the top-of-rotation song (see the frontend arming logic + the
# /rotation/sms/auto-send route). Default OFF — opt-in per event.
AUTO_SMS_NEXT_KEY = "sms_auto_next_singer"
# When on, the rotation is automatically re-run through Auto Order every time a new
# entry is added (e.g. a singer request is approved). Default OFF — opt-in per event.
AUTO_REORDER_KEY = "rotation_auto_reorder"
SIMPLE_MODE_KEY = "kj_simple_mode"
SMS_TEMPLATE_KEY = "sms_template"
SMS_DEFAULT_REGION_KEY = "sms_default_region"
# Written by RotationStore.archive() when a New Rotation starts. Used to scope
# phone resolution (SMS + push) to requests created during the current night so
# a rotation_entry_id recycled by a New Rotation can't phantom-match a prior
# night's sing_request.
NIGHT_STARTED_KEY = "night_started_at"

DEFAULT_SMS_REGION = "US"

# Token is a 4-digit numeric code. 10 000 combinations — small enough to read off
# the venue screen and type on a phone numpad, large enough that the rate-limited
# /validate endpoint (see sing.py) makes brute-force impractical in one sitting.
TOKEN_DIGITS = 4


class SingStore:
    """Local SQLite storage for public sing requests + event token.

    Per-thread connections via ``threading.local`` — see RotationStore for
    the rationale (2026-05-01 outage caused by sharing one sqlite3
    connection across Flask + background threads).
    """

    _MEMORY = ":memory:"
    _SENTINEL = object()

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
        conn.execute("PRAGMA cache_size=-8192")  # 8 MB
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

            CREATE TABLE IF NOT EXISTS sing_push_subscriptions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                token           TEXT NOT NULL,
                phone           TEXT NOT NULL,
                singer_name     TEXT NOT NULL,
                endpoint        TEXT NOT NULL,
                p256dh          TEXT NOT NULL,
                auth            TEXT NOT NULL,
                user_agent      TEXT,
                last_sent_state TEXT,
                last_seen_at    TEXT,
                disabled_at     TEXT,
                UNIQUE(token, endpoint)
            );
            CREATE INDEX IF NOT EXISTS idx_sing_push_token_phone
                ON sing_push_subscriptions(token, phone);

            -- Device → canonical-name mapping (2026-08-27). A singer's display
            -- name is free text they type on their phone (localStorage), so a KJ
            -- or self-service rename would otherwise be lost the next time that
            -- device submits a song. An alias row makes a rename stick: at
            -- /sing/submit the typed name is overridden by the device's canonical
            -- name (if any). Keyed on the opaque device_id the client generates
            -- once and stores in localStorage. Persists across nights on purpose
            -- (a regular keeps their chosen name) — device_id is stable per
            -- browser, so there's no cross-night id-reuse hazard here.
            -- `origin` (2026-08-28) records who established the alias: 'kj' for a
            -- KJ rename/merge (a deliberate "these are one person" assertion) vs
            -- 'self' for a singer's own /sing/rename. Only 'kj' aliases mark a
            -- CANONICAL identity that a later self-rename may propagate across the
            -- whole name-group — a singer self-renaming their own songs must never
            -- gain the power to rename a coincidental same-name walk-in.
            CREATE TABLE IF NOT EXISTS singer_aliases (
                device_id      TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                origin         TEXT NOT NULL DEFAULT 'self',
                updated_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            """
        )
        # Additive migration — `additional_singers` was added 2026-05-15 for
        # duet-partner support. Existing rows get NULL (= solo request).
        try:
            conn.execute(
                "ALTER TABLE sing_requests "
                "ADD COLUMN additional_singers TEXT DEFAULT NULL"
            )
        except sqlite3.OperationalError as e:
            # Column already exists on this DB; safe to ignore.
            if "duplicate column name" not in str(e).lower():
                raise
        # Additive migration — `edit_token` (2026-07-09) is a per-request secret
        # proving device ownership for singer self-service (cancel/edit).
        try:
            conn.execute(
                "ALTER TABLE sing_requests ADD COLUMN edit_token TEXT DEFAULT NULL"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
        # Additive migration — `supersedes_request_id` (2026-07-09) links an
        # edit's new pending request to the approved original it replaces.
        try:
            conn.execute(
                "ALTER TABLE sing_requests ADD COLUMN supersedes_request_id INTEGER DEFAULT NULL"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
        # Additive migration — `user_agent` (2026-08-13) records the submitting
        # device's User-Agent so the KJ can tell a real singer-UI session apart
        # from a duet-partner label or a KJ-hand-added entry, and eyeball the
        # phone/browser. Mirrors sing_push_subscriptions.user_agent. Existing
        # rows get NULL (unknown device).
        try:
            conn.execute(
                "ALTER TABLE sing_requests ADD COLUMN user_agent TEXT DEFAULT NULL"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
        # Additive migration — `device_id` (2026-08-27) is the opaque, stable
        # per-browser identifier the singer UI generates once and stores in
        # localStorage. Lets a rename (KJ-side or singer self-service) persist to
        # future submissions from the same device via the singer_aliases table.
        # Existing rows get NULL (unknown device — never alias-matched).
        try:
            conn.execute(
                "ALTER TABLE sing_requests ADD COLUMN device_id TEXT DEFAULT NULL"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
        # Additive migration — `singer_aliases.origin` (2026-08-28). Distinguishes
        # KJ-established identities ('kj', from a rename/merge) from a singer's own
        # self-rename ('self'). Only 'kj' unlocks a whole-group rename. Pre-upgrade
        # rows can't be told apart, so they default to 'self' — the SAFE choice:
        # they behave exactly as before (edit_token-scoped renames) and never gain
        # the power to sweep up a coincidental same-name singer. A merge done after
        # the upgrade writes fresh 'kj' aliases, so the fix applies going forward.
        try:
            conn.execute(
                "ALTER TABLE singer_aliases ADD COLUMN origin TEXT NOT NULL DEFAULT 'self'"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
        conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row):
        if row is None:
            return None
        d = dict(row)
        # additional_singers is stored as JSON; deserialise for callers.
        # NULL → None (solo). Empty array → [] (cleared list).
        raw = d.get("additional_singers")
        if raw is not None:
            try:
                d["additional_singers"] = json.loads(raw)
            except (TypeError, ValueError):
                d["additional_singers"] = None
        return d

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

    def get_night_started_at(self):
        """Timestamp the current night began ('YYYY-MM-DD HH:MM:SS'), or None.

        Set by RotationStore.archive() on a New Rotation. Phone resolution
        scopes to ``created_at >= night_started_at`` so a recycled
        rotation_entry_id can't resolve to a prior night's request (the
        cross-night id-reuse bug that texted/pushed the wrong singer).
        """
        return self._get_meta(NIGHT_STARTED_KEY)

    def ensure_night_started(self):
        """Set night_started_at to now on first boot if it's never been set.

        Phone resolution (SMS + push) is night-scoped and fails CLOSED when the
        marker is missing, so guaranteeing it's always present keeps SMS/push
        working on a device that hasn't run a New Rotation yet. A device that
        has archived already keeps its existing (newer) value — this never
        overwrites. Returns the effective value.
        """
        existing = self._get_meta(NIGHT_STARTED_KEY)
        if existing:
            return existing
        conn = self._get_conn()
        now = conn.execute("SELECT datetime('now', 'localtime')").fetchone()[0]
        self._set_meta(NIGHT_STARTED_KEY, now)
        return now

    def get_token(self):
        """Return the current event token, or None if unset."""
        return self._get_meta(TOKEN_KEY)

    def regenerate_token(self):
        """Generate a fresh 4-digit token, store it, return it.

        Never returns the same token twice in a row — a KJ rotating the token
        mid-event expects "rotate" to actually invalidate the old one.
        """
        previous = self.get_token()
        upper = 10 ** TOKEN_DIGITS
        while True:
            new_token = str(secrets.randbelow(upper)).zfill(TOKEN_DIGITS)
            if new_token != previous:
                break
        self._set_meta(TOKEN_KEY, new_token)
        return new_token

    def set_token(self, token):
        """Set a specific 4-digit token (e.g. KJ pinning a memorable code).

        Raises ValueError on malformed input — the route translates to HTTP 400.
        Same-token writes are allowed (idempotent) so the UI can re-pin without
        a special path.
        """
        if not isinstance(token, str):
            raise ValueError("token must be a string")
        if len(token) != TOKEN_DIGITS or not token.isdigit():
            raise ValueError(f"token must be exactly {TOKEN_DIGITS} digits")
        self._set_meta(TOKEN_KEY, token)
        return token

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

    def is_accepting_make_requests(self):
        """Return True if singers can submit ``source_type="make"`` requests.

        Default on. The KJ flips this off when they're too busy to do same-
        night lyrics reviews. When off, the empty-state triage in the singer
        UI hides the "ask the KJ to make it" card AND ``/sing/submit`` rejects
        any ``make`` payloads (defence-in-depth for stale clients).
        """
        return self._get_meta(ACCEPT_MAKE_REQUESTS_KEY, "1") == "1"

    def set_accepting_make_requests(self, enabled):
        self._set_meta(ACCEPT_MAKE_REQUESTS_KEY, "1" if enabled else "0")

    def is_auto_sms_next(self):
        """Return True if the KJ has enabled auto-texting the next singer.

        Default OFF. When on, the frontend arms a 20s timer whenever the KJ
        starts the top-of-rotation song and — if that song is still playing —
        auto-sends the "you're up next" SMS to the up-next singer (position 2).
        See /rotation/sms/auto-send for the server-side eligibility guards.
        """
        return self._get_meta(AUTO_SMS_NEXT_KEY, "0") == "1"

    def set_auto_sms_next(self, enabled):
        self._set_meta(AUTO_SMS_NEXT_KEY, "1" if enabled else "0")

    def is_auto_reorder(self):
        """Return True if Auto Order should re-run automatically on new entries.

        Default OFF. When on, every time a new rotation entry is added (a singer
        request approved, or a KJ manual add), the rotation is re-run through
        ``compute_auto_order`` so a stand-in KJ never has to reorder by hand. The
        on-demand "Auto Order" header button works regardless of this flag.
        """
        return self._get_meta(AUTO_REORDER_KEY, "0") == "1"

    def set_auto_reorder(self, enabled):
        self._set_meta(AUTO_REORDER_KEY, "1" if enabled else "0")

    def is_simple_mode(self):
        """Return True if the KJ controller is in stand-in / simple-operator mode.

        When on, the singer UI restricts the source allowlist to local/
        divebar/kn (no YouTube paste, make, or kj_pick deferral), and the KJ
        UI hides search panels, manual rotation entry, and most System
        controls. Persistent across rotation archives; toggled from the KJ
        controller's System → Mode subsection.
        """
        return self._get_meta(SIMPLE_MODE_KEY, "0") == "1"

    def set_simple_mode(self, enabled):
        self._set_meta(SIMPLE_MODE_KEY, "1" if enabled else "0")

    # ------------------------------------------------------------------
    # SMS settings — per-event template + default region for normalization
    # ------------------------------------------------------------------

    def get_sms_template(self):
        """Return the per-event SMS template, or None if unset (caller falls
        back to the in-code DEFAULT_TEMPLATE)."""
        return self._get_meta(SMS_TEMPLATE_KEY)

    def set_sms_template(self, template):
        """Persist a custom template. ``None`` clears the override so the
        in-code default takes over again."""
        if template is None:
            conn = self._get_conn()
            conn.execute("DELETE FROM rotation_meta WHERE key = ?", (SMS_TEMPLATE_KEY,))
            conn.commit()
            return
        if not isinstance(template, str):
            raise ValueError("template must be a string")
        template = template.strip()
        if not template:
            raise ValueError("template must not be empty")
        self._set_meta(SMS_TEMPLATE_KEY, template)

    def get_sms_default_region(self):
        """ISO 3166-1 alpha-2 region used to normalize bare local-format
        phones. Defaults to US for our most common venue."""
        return self._get_meta(SMS_DEFAULT_REGION_KEY, DEFAULT_SMS_REGION)

    def set_sms_default_region(self, region):
        if not isinstance(region, str) or len(region) != 2 or not region.isalpha():
            raise ValueError("region must be an ISO 3166-1 alpha-2 code (e.g. 'US')")
        self._set_meta(SMS_DEFAULT_REGION_KEY, region.upper())

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
        additional_singers=None,
        supersedes_request_id=None,
        user_agent=None,
        device_id=None,
    ):
        """Insert a new pending request and return the created row as a dict."""
        if not singer_name:
            raise ValueError("singer_name is required")
        if not source_type:
            raise ValueError("source_type is required")
        # Phone is optional — KJs text singers when they're up, but the
        # public form lets singers opt out. Stored as empty string so the
        # NOT NULL column constraint still holds.
        phone = phone or ""

        request_token = token if token is not None else (self.get_token() or "")
        meta_json = json.dumps(source_meta) if source_meta is not None else None
        partners_json = (
            json.dumps(additional_singers) if additional_singers is not None else None
        )
        edit_token = secrets.token_urlsafe(16)
        conn = self._get_conn()
        cur = conn.execute(
            """
            INSERT INTO sing_requests
                (token, singer_name, phone, song_artist, song_title,
                 source_type, source_ref, source_meta, notes,
                 additional_singers, edit_token, supersedes_request_id,
                 user_agent, device_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                partners_json,
                edit_token,
                supersedes_request_id,
                (user_agent or None),
                (device_id or None),
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

    def get_requests_for_entries(self, entry_ids, night_started=None):
        """Return linked sing_requests for the given rotation-entry ids.

        Used to attribute rotation entries to the device session that submitted
        them (singer session provenance). Night-scoped like the SMS phone lookup
        (``_add_sms_status``): a New Rotation recycles rotation_entry ids, so a
        recycled id could otherwise phantom-match a prior night's request. Fails
        CLOSED (returns []) when ``night_started`` is None — callers pass the
        current ``night_started_at`` which ``ensure_night_started`` guarantees.

        Returns a list of dicts (newest first) with the columns needed for
        classification: id, linked_entry_id, singer_name, phone, user_agent,
        created_at, source_type, additional_singers (deserialised).
        """
        ids = [int(e) for e in (entry_ids or []) if e is not None]
        if not ids or not night_started:
            return []
        conn = self._get_conn()
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""
            SELECT id, linked_entry_id, singer_name, phone, user_agent,
                   created_at, source_type, additional_singers
            FROM sing_requests
            WHERE linked_entry_id IN ({placeholders})
              AND created_at >= ?
            ORDER BY id DESC
            """,
            tuple(ids + [night_started]),
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
        additional_singers=_SENTINEL,
    ):
        """Edit mutable fields on a request. Returns the updated row.

        For `additional_singers`: pass a list (incl. empty) to overwrite, or
        leave default to preserve the existing value. There is no "set to
        NULL" path through update_request — create with None instead.
        """
        existing = self.get_request(request_id)
        if existing is None:
            raise ValueError(f"Request {request_id} not found")

        if additional_singers is self._SENTINEL:
            partners_json = (
                json.dumps(existing["additional_singers"])
                if existing.get("additional_singers") is not None
                else None
            )
        else:
            partners_json = json.dumps(additional_singers)

        new_vals = {
            "singer_name": singer_name.strip() if singer_name is not None else existing["singer_name"],
            "song_artist": song_artist.strip() if song_artist is not None else existing["song_artist"],
            "song_title": song_title.strip() if song_title is not None else existing["song_title"],
            "source_type": source_type if source_type is not None else existing["source_type"],
            "source_ref": source_ref if source_ref is not None else existing["source_ref"],
            "source_meta": (
                json.dumps(source_meta) if source_meta is not None
                else (
                    json.dumps(existing["source_meta"])
                    if isinstance(existing.get("source_meta"), (dict, list))
                    else existing["source_meta"]
                )
            ),
            "notes": notes.strip() if notes is not None else existing["notes"],
        }
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE sing_requests
               SET singer_name = ?, song_artist = ?, song_title = ?,
                   source_type = ?, source_ref = ?, source_meta = ?,
                   notes = ?, additional_singers = ?
             WHERE id = ?
            """,
            (
                new_vals["singer_name"], new_vals["song_artist"], new_vals["song_title"],
                new_vals["source_type"], new_vals["source_ref"], new_vals["source_meta"],
                new_vals["notes"], partners_json, request_id,
            ),
        )
        conn.commit()
        return self.get_request(request_id)

    def update_request_source(self, request_id, source_type, source_ref, source_meta):
        """Rewrite only the source_* fields on a request.

        Used when a ``kj_pick`` request gets bound to a concrete version at
        approval time — we want the post-approval row to reflect the picked
        version (so downstream audit trails, "now playing" history, and any
        future retry logic see ``local`` / ``divebar`` / ``youtube`` instead
        of the transient ``kj_pick`` placeholder).

        ``source_meta`` is stored verbatim as JSON; pass ``None`` to clear it.
        """
        if self.get_request(request_id) is None:
            raise ValueError(f"Request {request_id} not found")
        meta_json = json.dumps(source_meta) if source_meta is not None else None
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE sing_requests
               SET source_type = ?, source_ref = ?, source_meta = ?
             WHERE id = ?
            """,
            (source_type, source_ref, meta_json, request_id),
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

    def mark_cancelled(self, request_id):
        """Set status=cancelled, reviewed_at=now. Raises ValueError if not found."""
        if self.get_request(request_id) is None:
            raise ValueError(f"Request {request_id} not found")
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE sing_requests
               SET status = 'cancelled',
                   reviewed_at = datetime('now', 'localtime')
             WHERE id = ?
            """,
            (request_id,),
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

    # ------------------------------------------------------------------
    # Singer aliases — device_id → canonical display name
    # ------------------------------------------------------------------

    def get_alias(self, device_id):
        """Return the canonical name a device is mapped to, or None.

        Called on every /sing/submit to override the free-text name a device
        types (localStorage) with the name a KJ or the singer themselves chose.
        """
        device_id = (device_id or "").strip()
        if not device_id:
            return None
        conn = self._get_conn()
        row = conn.execute(
            "SELECT canonical_name FROM singer_aliases WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        return row[0] if row else None

    def set_alias(self, device_id, canonical_name, origin="self"):
        """Upsert a device → canonical-name mapping. No-op on blank input.

        ``origin`` is 'kj' when a KJ rename/merge established the identity, else
        'self' (a singer's own /sing/rename). The stored origin always reflects
        the LAST writer: a KJ action stamps 'kj'; a self-rename stamps 'self'.
        Crucially, KJ authority does NOT travel with a device onto a name the
        SINGER later chose — a self-rename that changes the name resets origin to
        'self', so a singer can never launder a past merge into whole-group power
        over a coincidental same-name walk-in. Only 'kj' aliases satisfy
        is_canonical_identity; a genuinely KJ-merged multi-device identity stays
        canonical via the sibling devices the KJ/merge path re-stamps 'kj'.
        """
        device_id = (device_id or "").strip()
        canonical_name = (canonical_name or "").strip()
        origin = "kj" if origin == "kj" else "self"
        if not device_id or not canonical_name:
            return
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO singer_aliases (device_id, canonical_name, origin, updated_at) "
            "VALUES (?, ?, ?, datetime('now', 'localtime')) "
            "ON CONFLICT(device_id) DO UPDATE SET "
            "  canonical_name = excluded.canonical_name, "
            "  origin = excluded.origin, "
            "  updated_at = datetime('now', 'localtime')",
            (device_id, canonical_name, origin),
        )
        conn.commit()

    def clear_alias(self, device_id):
        """Drop a device's alias (used when a device declares a new identity)."""
        device_id = (device_id or "").strip()
        if not device_id:
            return
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM singer_aliases WHERE device_id = ?", (device_id,)
        )
        conn.commit()

    def is_canonical_identity(self, name):
        """True if ``name`` is a KJ-established singer identity.

        An identity is "established" only when at least one device carries a
        KJ-origin alias to ``name`` — i.e. a KJ renamed/merged someone into it.
        This is the trust anchor that lets a self-service rename safely carry the
        WHOLE name-group rather than only the calling device's own songs.

        A singer's OWN /sing/rename records a 'self'-origin alias, which does NOT
        count here — otherwise a singer who self-renamed their song to "Mike"
        could, on a second rename, sweep up a coincidental second "Mike" walk-in
        whose edit_token they never held. Only the KJ's explicit assertion that a
        name is one managed identity unlocks whole-group renames.
        """
        name = (name or "").strip()
        if not name:
            return False
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM singer_aliases "
            "WHERE LOWER(canonical_name) = LOWER(?) AND origin = 'kj' LIMIT 1",
            (name,),
        ).fetchone()
        return row is not None

    def remap_aliases(self, old_name, new_name):
        """Re-point every device aliased to ``old_name`` at ``new_name``.

        Used when a merged identity is renamed: all the devices the KJ merged
        into ``old_name`` must follow the singer to ``new_name`` so none of them
        re-splits under the stale name on a future submission. Cross-night like
        the aliases themselves (device_id is stable per browser). No-op on blank
        input or a no-op rename.
        """
        old_name = (old_name or "").strip()
        new_name = (new_name or "").strip()
        if not old_name or not new_name or old_name.lower() == new_name.lower():
            return
        conn = self._get_conn()
        conn.execute(
            "UPDATE singer_aliases SET canonical_name = ?, "
            "  updated_at = datetime('now', 'localtime') "
            "WHERE LOWER(canonical_name) = LOWER(?)",
            (new_name, old_name),
        )
        conn.commit()

    def mark_identity(self, name, night_started=None):
        """Alias every device that submitted under ``name`` tonight → ``name``.

        Records the devices behind a name as one established identity (a
        KJ-origin alias). Called on the KEEP side of a KJ merge so the merged
        singer is a recognised identity even from a device that never had to be
        renamed — which is what makes a later self-service rename carry the group.
        Best-effort; returns the number of devices marked.

        Night-scoped like the rest of request/phone resolution. The marker is
        resolved internally when not passed; with no night at all we fail closed
        (return 0) rather than mark every historical device under ``name``.
        """
        name = (name or "").strip()
        if not name:
            return 0
        night_started = night_started or self.get_night_started_at()
        if not night_started:
            return 0
        conn = self._get_conn()
        params = [name, night_started]
        night_clause = " AND created_at >= ?"
        rows = conn.execute(
            "SELECT DISTINCT device_id FROM sing_requests "
            "WHERE LOWER(singer_name) = LOWER(?)"
            "  AND device_id IS NOT NULL AND device_id != ''" + night_clause,
            tuple(params),
        ).fetchall()
        device_ids = [r[0] for r in rows]
        for did in device_ids:
            self.set_alias(did, name, origin="kj")
        return len(device_ids)

    def persist_rename(self, old_name, new_name, night_started=None):
        """Make a KJ/merge rename of ``old_name`` → ``new_name`` sticky.

        1. Aliases every device that submitted a request under ``old_name`` (as
           the primary singer) tonight so their FUTURE submissions resolve to
           ``new_name`` at /sing/submit time.
        2. Rewrites those requests' stored ``singer_name`` to ``new_name`` so
           singer-session provenance (which matches request.singer_name against
           the current rotation-entry name) keeps working after the rename.

        Night-scoped (``created_at >= night_started``) like the rest of the
        request/phone resolution to avoid touching prior nights' history. The
        marker is resolved internally when not passed; with no night at all we
        fail closed (return 0) rather than rewrite every historical request under
        ``old_name``. Returns the number of distinct devices aliased. Best-effort;
        safe to call for a name that has no portal submissions (returns 0).
        """
        old_name = (old_name or "").strip()
        new_name = (new_name or "").strip()
        if not old_name or not new_name or old_name.lower() == new_name.lower():
            return 0
        night_started = night_started or self.get_night_started_at()
        if not night_started:
            return 0
        conn = self._get_conn()
        params = [old_name, night_started]
        night_clause = " AND created_at >= ?"
        rows = conn.execute(
            "SELECT DISTINCT device_id FROM sing_requests "
            "WHERE LOWER(singer_name) = LOWER(?)"
            "  AND device_id IS NOT NULL AND device_id != ''" + night_clause,
            tuple(params),
        ).fetchall()
        device_ids = [r[0] for r in rows]
        for did in device_ids:
            # A KJ/merge rename establishes a managed identity (origin='kj').
            self.set_alias(did, new_name, origin="kj")
        # Rewrite the tonight requests' primary singer_name for provenance.
        conn.execute(
            "UPDATE sing_requests SET singer_name = ? "
            "WHERE LOWER(singer_name) = LOWER(?)" + night_clause,
            tuple([new_name] + params),
        )
        conn.commit()
        return len(device_ids)

    # ------------------------------------------------------------------
    # Push subscription CRUD (sub-project #4)
    # ------------------------------------------------------------------

    def insert_push_subscription(self, token, phone, singer_name, endpoint,
                                  p256dh, auth, user_agent=None):
        """Insert-or-replace on UNIQUE(token, endpoint).

        If a matching row is disabled, re-enable it via disabled_at=NULL.
        Returns the row id.
        """
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO sing_push_subscriptions "
            "  (token, phone, singer_name, endpoint, p256dh, auth, user_agent, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime')) "
            "ON CONFLICT(token, endpoint) DO UPDATE SET "
            "  phone=excluded.phone, singer_name=excluded.singer_name, "
            "  p256dh=excluded.p256dh, auth=excluded.auth, "
            "  user_agent=excluded.user_agent, "
            "  updated_at=datetime('now', 'localtime'), "
            "  disabled_at=NULL",
            (token, phone, singer_name, endpoint, p256dh, auth, user_agent),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM sing_push_subscriptions WHERE token=? AND endpoint=?",
            (token, endpoint),
        ).fetchone()
        return row["id"] if row else None

    def list_active_push_subscriptions(self, token):
        """Return all non-disabled subs for `token` as a list of dict rows."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM sing_push_subscriptions "
            "WHERE token=? AND disabled_at IS NULL "
            "ORDER BY id",
            (token,),
        ).fetchall()
        return [dict(r) for r in rows]

    def disable_push_subscription(self, sub_id):
        conn = self._get_conn()
        conn.execute(
            "UPDATE sing_push_subscriptions "
            "SET disabled_at=datetime('now', 'localtime') "
            "WHERE id=?",
            (sub_id,),
        )
        conn.commit()

    def disable_push_subscription_by_endpoint(self, token, endpoint):
        """Soft-disable a subscription by (token, endpoint).

        Returns True if a row was disabled, False if no matching row existed.
        Silent no-op on unknown endpoint — callers treat it as idempotent.
        """
        conn = self._get_conn()
        cur = conn.execute(
            "UPDATE sing_push_subscriptions "
            "SET disabled_at=datetime('now', 'localtime') "
            "WHERE token=? AND endpoint=? AND disabled_at IS NULL",
            (token, endpoint),
        )
        conn.commit()
        return cur.rowcount > 0

    def update_push_sent_state(self, sub_id, state_dict):
        import json as _json
        conn = self._get_conn()
        conn.execute(
            "UPDATE sing_push_subscriptions "
            "SET last_sent_state=?, updated_at=datetime('now', 'localtime') "
            "WHERE id=?",
            (_json.dumps(state_dict), sub_id),
        )
        conn.commit()

    def cleanup_stale_push_subscriptions(self, current_token):
        """Delete subs on tokens other than the current, not refreshed in >7 days.

        Uses updated_at (bumped on every upsert) rather than created_at so a
        singer who's been actively re-subscribing on a different token keeps
        their row.
        """
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM sing_push_subscriptions "
            "WHERE token != ? "
            "  AND updated_at < datetime('now', '-7 days', 'localtime')",
            (current_token,),
        )
        conn.commit()

    def find_subs_by_phone(self, token, phone):
        """Return all non-disabled subs matching (token, phone) as dict list."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM sing_push_subscriptions "
            "WHERE token=? AND phone=? AND disabled_at IS NULL",
            (token, phone),
        ).fetchall()
        return [dict(r) for r in rows]
