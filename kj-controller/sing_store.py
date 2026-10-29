"""SingStore: SQLite-backed storage for public singer requests + event token helpers.

Shares the rotation.db file so backups and archives stay unified. Stores
pending-review requests coming in from the public `/sing/` request form and
provides helpers for the short-lived event token that gates that form.
"""

import datetime
import hashlib
import json
import logging
import re
import secrets
import sqlite3
import threading
import time

from history_ids import ENTRY_IDS_UNIQUE_SINCE
from singer_names import fold_name

logger = logging.getLogger(__name__)

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
                device_id       TEXT DEFAULT NULL,
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

            -- Per-singer social-media photo/video consent (2026-09-24). Keyed
            -- on the folded singer name (rotation identity is the name string)
            -- and read NIGHT-SCOPED (updated_at >= night_started_at) so a
            -- same-name walk-in on a later night never inherits someone else's
            -- choice. The singer's device remembers its own choice and re-sends
            -- it with each night's first request. consent: 'yes' | 'no';
            -- source: 'singer' | 'kj'.
            CREATE TABLE IF NOT EXISTS singer_photo_consent (
                name_key     TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                consent      TEXT NOT NULL,
                source       TEXT NOT NULL DEFAULT 'singer',
                updated_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
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
        # Additive migration — `auto_status`/`auto_error` (2026-10-16): with
        # auto-approve on, a request is 'in_progress' while the server adds it
        # (the KJ panel shows "Adding automatically…" and refuses a manual
        # approve that would double-add) and 'failed' + a reason when it
        # couldn't — so anything left in the panel says why it needs the KJ.
        for col in ("auto_status", "auto_error", "auto_started_at"):
            try:
                conn.execute(f"ALTER TABLE sing_requests ADD COLUMN {col} TEXT DEFAULT NULL")
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
        # Additive migration — `sing_push_subscriptions.device_id` (2026-09-25).
        # Phone is optional at signup, so a phone-less singer's subscription is
        # keyed by the device that made their requests instead (their phone
        # column holds '' — treated as absent everywhere). Existing rows get
        # NULL and keep matching by phone exactly as before.
        try:
            conn.execute(
                "ALTER TABLE sing_push_subscriptions ADD COLUMN device_id TEXT DEFAULT NULL"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sing_push_token_device "
            "ON sing_push_subscriptions(token, device_id)"
        )
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
        # Additive migration — `gen_job_id` / `gen_submit_state` (2026-09-24).
        # A singer "make" request starts its karaoke-gen job the moment it is
        # submitted (not when the KJ approves), so the job id has to live on the
        # request until approval attaches it to a rotation entry.
        # gen_submit_state: NULL (no early job) | submitting | submitted | failed.
        for ddl in (
            "ALTER TABLE sing_requests ADD COLUMN gen_job_id TEXT DEFAULT NULL",
            "ALTER TABLE sing_requests ADD COLUMN gen_submit_state TEXT DEFAULT NULL",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    raise
        # Singer make-it flow (2026-09-25): the singer's verified karaoke-gen
        # account, per device. The session token never leaves the box — the
        # singer's browser only ever sees their email.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sing_gen_accounts (
                device_id     TEXT PRIMARY KEY,
                email         TEXT NOT NULL,
                session_token TEXT NOT NULL,
                verified_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
            """
        )
        # Per-song media consent (2026-10-14). A singer can say "not THIS song"
        # without revoking consent for the rest of their night:
        # - sing_requests.media_consent: the requester's own override for the
        #   song that request created ('yes'|'no'|NULL);
        # - singer_photo_consent.instagram: optional handle for tagging;
        # - song_media_consent: KJ overrides, keyed on (entry_id, entry_created_at)
        #   because rotation entry ids are recycled across nights. name_key '*'
        #   covers every performer on the song;
        # - photo_consent_events: append-only log of every consent/handle change,
        #   so a past night resolves the same after singer_photo_consent rows
        #   are overwritten on later nights.
        for ddl in (
            "ALTER TABLE sing_requests ADD COLUMN media_consent TEXT DEFAULT NULL",
            "ALTER TABLE singer_photo_consent ADD COLUMN instagram TEXT DEFAULT NULL",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    raise
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS song_media_consent (
                entry_id         INTEGER NOT NULL,
                entry_created_at TEXT NOT NULL DEFAULT '',
                name_key         TEXT NOT NULL,
                display_name     TEXT NOT NULL,
                consent          TEXT NOT NULL,
                source           TEXT NOT NULL DEFAULT 'kj',
                updated_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                PRIMARY KEY (entry_id, entry_created_at, name_key)
            );
            CREATE TABLE IF NOT EXISTS photo_consent_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                night        TEXT NOT NULL,
                scope        TEXT NOT NULL,
                name_key     TEXT NOT NULL,
                display_name TEXT NOT NULL,
                entry_id     INTEGER,
                request_id   INTEGER,
                consent      TEXT,
                instagram    TEXT,
                source       TEXT NOT NULL,
                created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_photo_consent_events_night
                ON photo_consent_events (night);
            """
        )
        # App-facing singer API storage (2026-10-21). All additive/idempotent.
        # - sing_push_subscriptions.kind/expo_token: Expo push rows share the table
        #   with web-push rows (kind 'webpush' default keeps old rows unchanged).
        for ddl in (
            "ALTER TABLE sing_push_subscriptions ADD COLUMN kind TEXT DEFAULT 'webpush'",
            "ALTER TABLE sing_push_subscriptions ADD COLUMN expo_token TEXT DEFAULT NULL",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    raise
        conn.executescript(
            """
            -- Device -> gen account link, NIGHT-SCOPED (a new night starts empty).
            CREATE TABLE IF NOT EXISTS singer_accounts (
                device_id        TEXT NOT NULL,
                gen_email        TEXT NOT NULL,
                origin           TEXT NOT NULL,
                night_started_at TEXT NOT NULL,
                linked_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                PRIMARY KEY (device_id, night_started_at)
            );
            CREATE INDEX IF NOT EXISTS idx_singer_accounts_email
                ON singer_accounts (gen_email, night_started_at);

            CREATE TABLE IF NOT EXISTS app_idempotency (
                key           TEXT PRIMARY KEY,
                response_json TEXT NOT NULL,
                expires_at    REAL NOT NULL
            );

            -- Requests the app submitted as "song for a friend" (singer_name is
            -- the friend; device_id stays the requester's). Kept out of
            -- sing_requests so admin/web row shapes are unchanged.
            CREATE TABLE IF NOT EXISTS app_friend_requests (
                request_id  INTEGER PRIMARY KEY,
                friend_name TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            -- Pending pushes of sung-song history to decide. One row per
            -- history_id: the latest op (put/delete) wins.
            CREATE TABLE IF NOT EXISTS history_outbox (
                history_id       TEXT PRIMARY KEY,
                op               TEXT NOT NULL,
                payload_json     TEXT NOT NULL,
                state            TEXT NOT NULL DEFAULT 'pending',
                attempts         INTEGER NOT NULL DEFAULT 0,
                next_attempt_at  TEXT NOT NULL,
                last_error       TEXT,
                night_started_at TEXT NOT NULL,
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_history_outbox_due
                ON history_outbox (state, next_attempt_at);

            -- Past-night songs a singer claimed from the app (Phase 4c).
            -- First claimer wins per entry and per singer slot (R31).
            CREATE TABLE IF NOT EXISTS history_claims (
                night       TEXT NOT NULL,      -- Night Log night (YYYY-MM-DD, noon rollover)
                entry_id    INTEGER NOT NULL,   -- exact or inferred rotation entry id
                gen_email   TEXT NOT NULL,
                history_id  TEXT NOT NULL,
                member      TEXT NOT NULL,      -- the matched member's display name (duets)
                claimed_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                PRIMARY KEY (night, entry_id, gen_email)
            );
            -- Songs a singer said were not theirs, keyed by email hash only.
            CREATE TABLE IF NOT EXISTS history_rejections (
                email_hash  TEXT NOT NULL,
                night       TEXT NOT NULL,
                entry_id    INTEGER NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                PRIMARY KEY (email_hash, night, entry_id)
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

    _APP_IDENTITY_SECRET_KEY = "app_identity_secret"

    def app_identity_secret(self):
        """The box's app identity secret (32 random bytes, hex), generated on
        first use and kept in rotation_meta so app device ids survive restarts.
        Never log or expose it."""
        conn = self._get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO rotation_meta (key, value) VALUES (?, ?)",
            (self._APP_IDENTITY_SECRET_KEY, secrets.token_hex(32)),
        )
        conn.commit()
        return self._get_meta(self._APP_IDENTITY_SECRET_KEY)

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
    # Tip settings — KJ-editable from the Public Request Form modal.
    # Stored as one JSON blob; keys absent here fall back to config.json
    # (sing_tip_* keys) and then in-code defaults. See sing._tip_settings.
    # ------------------------------------------------------------------

    TIP_SETTINGS_KEY = "sing_tip_settings"
    # The editable field allowlist (and their expected types for validation).
    TIP_SETTING_FIELDS = {
        "enabled": bool,       # explicit on/off; absent = default on
        "kj_name": str,        # "Andrew" → singer UI says "Tip Andrew"
        "venmo": str,          # username (no @)
        "cashapp": str,        # cashtag (no $)
        "paypal": str,         # paypal.me handle
        "zelle": str,          # phone/email shown with a copy button
        "stripe_url": str,     # https://buy.stripe.com/… payment link
        "threshold": (int, float),  # $ amount that earns the ♥ + bump
    }

    # ------------------------------------------------------------------
    # Footer settings — the KJ's free-text message shown under every singer
    # screen, plus the pre-built venue notices (phone chargers etc.) they've
    # switched on. Same rotation_meta JSON-blob pattern as tip settings.
    # ------------------------------------------------------------------

    FOOTER_SETTINGS_KEY = "sing_footer_settings"
    FOOTER_MESSAGE_MAX = 400
    # Pre-built notices the KJ can toggle. The singer UI owns the (translated)
    # copy + icon per key; the store only validates membership.
    FOOTER_NOTICE_KEYS = (
        "chargers", "lyricsScreen", "duets", "moreSongs",
        "tipsHelp", "wifi", "water", "photos",
    )
    # Social links shown as an icon row in the singer footer. Order here is
    # the render order. "email" is an address (rendered as mailto:); the rest
    # are http(s) URLs.
    FOOTER_SOCIAL_KEYS = (
        "instagram", "facebook", "tiktok", "youtube", "x", "website", "email", "phone",
    )
    # KJ-authored notices ("c-<id>") and edits to the pre-built ones live in
    # ``notice_defs`` as {key: {icon, text}}; ``notices`` stays the ordered
    # list of switched-on keys. A pre-built notice with no text override keeps
    # its translated copy.
    FOOTER_CUSTOM_NOTICE_RE = re.compile(r"^c-[a-z0-9]{1,12}$")
    FOOTER_CUSTOM_NOTICES_MAX = 12
    FOOTER_NOTICE_TEXT_MAX = 200
    FOOTER_NOTICE_ICON_MAX = 16
    _PHONE_RE = re.compile(r"^\+?[0-9 ().-]{7,24}$")
    FOOTER_SOCIAL_MAX = 300
    _EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    @classmethod
    def _clean_social_value(cls, key, value):
        """Normalise one social-link value; '' clears it. Raises ValueError."""
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError(f"social.{key} must be a string")
        value = value.strip()
        if not value:
            return ""
        if len(value) > cls.FOOTER_SOCIAL_MAX:
            raise ValueError(f"social.{key} is too long")
        if key == "phone":
            if value.lower().startswith("tel:"):
                value = value[4:].strip()
            digits = re.sub(r"\D", "", value)
            if not cls._PHONE_RE.match(value) or not 7 <= len(digits) <= 15:
                raise ValueError("social.phone must be a phone number")
            return value
        if key == "email":
            if value.lower().startswith("mailto:"):
                value = value[7:]
            if not cls._EMAIL_RE.match(value):
                raise ValueError("social.email must be an email address")
            return value
        if not re.match(r"^https?://", value, re.IGNORECASE):
            # Accept a bare "instagram.com/foo" — but never another scheme
            # (javascript:, data:, …) since this lands in an href.
            if ":" in value.split("/", 1)[0] or "." not in value:
                raise ValueError(f"social.{key} must be a web address")
            value = "https://" + value
        if any(c.isspace() for c in value):
            raise ValueError(f"social.{key} must be a web address")
        return value

    @classmethod
    def _clean_notice_defs(cls, defs):
        """Validate ``notice_defs``: pre-built keys may override icon/text;
        custom ``c-<id>`` keys need text. Raises ValueError on bad input."""
        if defs is None:
            return {}
        if not isinstance(defs, dict):
            raise ValueError("notice_defs must be an object")
        cleaned = {}
        customs = 0
        for key, d in defs.items():
            custom = isinstance(key, str) and bool(cls.FOOTER_CUSTOM_NOTICE_RE.match(key))
            if not (custom or key in cls.FOOTER_NOTICE_KEYS):
                continue
            if not isinstance(d, dict):
                raise ValueError(f"notice_defs.{key} must be an object")
            icon, text = d.get("icon") or "", d.get("text") or ""
            if not isinstance(icon, str) or not isinstance(text, str):
                raise ValueError(f"notice_defs.{key} icon/text must be strings")
            icon = icon.strip()[: cls.FOOTER_NOTICE_ICON_MAX]
            text = " ".join(text.split())[: cls.FOOTER_NOTICE_TEXT_MAX]
            if custom:
                if not text:
                    continue   # an emptied custom notice is deleted
                customs += 1
                if customs > cls.FOOTER_CUSTOM_NOTICES_MAX:
                    raise ValueError(f"at most {cls.FOOTER_CUSTOM_NOTICES_MAX} custom notices")
            elif not (icon or text):
                continue       # back to the translated default
            cleaned[key] = {"icon": icon, "text": text}
        return cleaned

    def _notice_known(self, key, defs):
        return key in self.FOOTER_NOTICE_KEYS or (
            isinstance(key, str) and self.FOOTER_CUSTOM_NOTICE_RE.match(key) and key in defs)

    def get_footer_settings(self):
        """``{"message", "notices", "notice_defs", "social", "ask_photo_consent"}``
        — always every key. ``social`` holds only the non-empty links."""
        raw = self._get_meta(self.FOOTER_SETTINGS_KEY)
        data = {}
        if raw:
            try:
                parsed = json.loads(raw)
                data = parsed if isinstance(parsed, dict) else {}
            except (ValueError, TypeError):
                data = {}
        message = data.get("message")
        notices = data.get("notices")
        social = data.get("social") if isinstance(data.get("social"), dict) else {}
        try:
            defs = self._clean_notice_defs(data.get("notice_defs"))
        except ValueError:
            defs = {}
        return {
            "message": message if isinstance(message, str) else "",
            "notices": [n for n in (notices if isinstance(notices, list) else [])
                        if self._notice_known(n, defs)],
            "notice_defs": defs,
            "social": {k: social[k] for k in self.FOOTER_SOCIAL_KEYS
                       if isinstance(social.get(k), str) and social[k]},
            "ask_photo_consent": bool(data.get("ask_photo_consent")),
        }

    def set_footer_settings(self, settings):
        """Merge ``settings`` ({message?, notices?, notice_defs?, social?,
        ask_photo_consent?}) into the stored blob.

        ``message`` is trimmed and capped; ``notices`` must be a list of
        known keys (unknown keys are dropped, order preserved); ``social``
        replaces the whole link set (unknown keys dropped, blanks cleared, bad
        URLs/emails rejected). Raises ValueError on bad input.
        """
        if not isinstance(settings, dict):
            raise ValueError("footer settings must be an object")
        current = self.get_footer_settings()
        if "message" in settings:
            msg = settings["message"]
            if msg is None:
                msg = ""
            if not isinstance(msg, str):
                raise ValueError("message must be a string")
            current["message"] = msg.strip()[: self.FOOTER_MESSAGE_MAX]
        if "notice_defs" in settings:
            current["notice_defs"] = self._clean_notice_defs(settings["notice_defs"])
            # A deleted custom notice can't stay switched on.
            current["notices"] = [n for n in current["notices"]
                                  if self._notice_known(n, current["notice_defs"])]
        if "notices" in settings:
            notices = settings["notices"]
            if notices is None:
                notices = []
            if not isinstance(notices, list) or not all(isinstance(n, str) for n in notices):
                raise ValueError("notices must be a list of strings")
            seen = []
            for n in notices:
                if self._notice_known(n, current["notice_defs"]) and n not in seen:
                    seen.append(n)
            current["notices"] = seen
        if "social" in settings:
            social = settings["social"]
            if social is None:
                social = {}
            if not isinstance(social, dict):
                raise ValueError("social must be an object")
            cleaned = {}
            for key in self.FOOTER_SOCIAL_KEYS:
                val = self._clean_social_value(key, social.get(key))
                if val:
                    cleaned[key] = val
            current["social"] = cleaned
        if "ask_photo_consent" in settings:
            current["ask_photo_consent"] = bool(settings["ask_photo_consent"])
        self._set_meta(self.FOOTER_SETTINGS_KEY, json.dumps(current))
        return current

    # ------------------------------------------------------------------
    # Social-media photo/video consent — per singer NAME, night-scoped.
    # ------------------------------------------------------------------

    PHOTO_CONSENT_VALUES = ("yes", "no")

    @staticmethod
    def photo_consent_key(name):
        """Fold a singer name to its consent key (case/whitespace-insensitive)."""
        return " ".join((name or "").split()).casefold()

    _UNSET = object()
    _INSTAGRAM_RE = re.compile(r"^[a-z0-9._]{1,30}$")
    _INSTAGRAM_URL_RE = re.compile(
        r"^(?:https?://)?(?:www\.)?instagram\.com/([^/?#]+)/?(?:[?#].*)?$", re.I)

    @classmethod
    def normalize_instagram(cls, handle):
        """Fold an Instagram handle ("@Name", "instagram.com/name", "name") to
        its bare lowercase form; blank → None. Raises ValueError if invalid."""
        if handle is None:
            return None
        if not isinstance(handle, str):
            raise ValueError("instagram must be a string")
        h = handle.strip()
        m = cls._INSTAGRAM_URL_RE.match(h)
        if m:
            h = m.group(1)
        h = h.lstrip("@").strip().lower()
        if not h:
            return None
        if not cls._INSTAGRAM_RE.match(h):
            raise ValueError("instagram handle must be 1-30 letters, numbers, '.' or '_'")
        return h

    @staticmethod
    def _current_night():
        from night_log import night_of
        return night_of(datetime.datetime.now())

    def _log_consent_event(self, conn, scope, name, consent, source,
                           instagram=None, entry_id=None, request_id=None):
        conn.execute(
            "INSERT INTO photo_consent_events (night, scope, name_key, display_name, "
            "entry_id, request_id, consent, instagram, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self._current_night(), scope,
             "*" if name == "*" else self.photo_consent_key(name),
             "*" if name == "*" else " ".join((name or "").split()),
             entry_id, request_id, consent, instagram, source),
        )

    def set_photo_consent(self, name, consent, source="singer", instagram=_UNSET):
        """Record ``consent`` ('yes'/'no') for ``name``; ``None`` clears it.

        ``instagram`` (optional) sets/clears the singer's handle; omitted keeps
        the stored one. Every change is also appended to photo_consent_events.
        Raises ValueError on an unknown value, blank name or bad handle.
        """
        key = self.photo_consent_key(name)
        if not key:
            raise ValueError("singer name is required")
        if consent is not None and consent not in self.PHOTO_CONSENT_VALUES:
            raise ValueError("consent must be 'yes', 'no' or null")
        handle_given = instagram is not self._UNSET
        handle = self.normalize_instagram(instagram) if handle_given else None
        conn = self._get_conn()
        if consent is None:
            conn.execute("DELETE FROM singer_photo_consent WHERE name_key = ?", (key,))
        else:
            if not handle_given:
                row = conn.execute(
                    "SELECT instagram FROM singer_photo_consent WHERE name_key = ? "
                    "AND updated_at >= ?",
                    (key, self.get_night_started_at() or "9999"),
                ).fetchone()
                handle = row[0] if row else None
            conn.execute(
                "INSERT INTO singer_photo_consent "
                "(name_key, display_name, consent, source, instagram, updated_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime')) "
                "ON CONFLICT(name_key) DO UPDATE SET display_name = excluded.display_name, "
                "consent = excluded.consent, source = excluded.source, "
                "instagram = excluded.instagram, updated_at = excluded.updated_at",
                (key, " ".join(name.split()), consent, source, handle),
            )
        self._log_consent_event(conn, "night", name, consent, source,
                                instagram=handle if consent else None)
        conn.commit()

    def get_photo_consents(self):
        """``{name_key: {"consent", "source", "instagram"}}`` for choices made tonight.

        Fails closed (empty) with no night marker, like phone resolution.
        """
        night_started = self.get_night_started_at()
        if not night_started:
            return {}
        rows = self._get_conn().execute(
            "SELECT name_key, consent, source, instagram FROM singer_photo_consent "
            "WHERE updated_at >= ?",
            (night_started,),
        ).fetchall()
        return {r[0]: {"consent": r[1], "source": r[2], "instagram": r[3]} for r in rows}

    def get_photo_consent(self, name):
        """Tonight's consent for ``name`` ('yes'/'no') or None."""
        rec = self.get_photo_consents().get(self.photo_consent_key(name))
        return rec["consent"] if rec else None

    def carry_photo_consent(self, old_name, new_name):
        """Carry tonight's consent across a rename/merge of ``old_name`` →
        ``new_name``. If both have a choice and they disagree, 'no' wins —
        never widen permission on a guess. The Instagram handle and tonight's
        KJ per-song overrides follow too. Best-effort; returns the result."""
        self._carry_song_overrides(old_name, new_name)
        consents = self.get_photo_consents()
        old = consents.get(self.photo_consent_key(old_name))
        if not old or not self.photo_consent_key(new_name):
            return None
        new = consents.get(self.photo_consent_key(new_name))
        handle = (new or {}).get("instagram") or old.get("instagram")
        if new and new["consent"] == old["consent"] and new.get("instagram") == handle:
            return new["consent"]
        merged = "no" if new and new["consent"] != old["consent"] else old["consent"]
        self.set_photo_consent(new_name, merged, source=(old["source"] if not new else "kj"),
                               instagram=handle)
        return merged

    def _carry_song_overrides(self, old_name, new_name):
        old_key, new_key = self.photo_consent_key(old_name), self.photo_consent_key(new_name)
        night_started = self.get_night_started_at()
        if not old_key or not new_key or old_key == new_key or not night_started:
            return
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT entry_id, entry_created_at, consent FROM song_media_consent "
            "WHERE name_key = ? AND updated_at >= ?", (old_key, night_started)).fetchall()
        for entry_id, created, consent in rows:
            existing = conn.execute(
                "SELECT consent FROM song_media_consent WHERE entry_id = ? AND "
                "entry_created_at = ? AND name_key = ?", (entry_id, created, new_key)).fetchone()
            merged = "no" if "no" in (consent, existing and existing[0]) else consent
            self.set_song_media_consent(entry_id, created, new_name, merged)
            self.set_song_media_consent(entry_id, created, old_name, None)

    # ------------------------------------------------------------------
    # Per-song media consent
    # ------------------------------------------------------------------

    def set_request_media_consent(self, request_id, consent):
        """The requester's own override for the song ``request_id`` created
        ('yes'/'no'; None clears). Returns the updated request or None."""
        if consent is not None and consent not in self.PHOTO_CONSENT_VALUES:
            raise ValueError("consent must be 'yes', 'no' or null")
        req = self.get_request(request_id)
        if req is None:
            return None
        conn = self._get_conn()
        conn.execute("UPDATE sing_requests SET media_consent = ? WHERE id = ?",
                     (consent, request_id))
        self._log_consent_event(conn, "song", req.get("singer_name") or "", consent, "singer",
                                entry_id=req.get("linked_entry_id"), request_id=request_id)
        conn.commit()
        return self.get_request(request_id)

    def set_song_media_consent(self, entry_id, entry_created_at, name, consent, source="kj"):
        """KJ override for one performance. ``name`` None/'*' = every performer.
        ``consent`` None clears it."""
        if consent is not None and consent not in self.PHOTO_CONSENT_VALUES:
            raise ValueError("consent must be 'yes', 'no' or null")
        if not name or name == "*":
            name, key = "*", "*"
        else:
            key = self.photo_consent_key(name)
            if not key:
                raise ValueError("singer name is required")
        created = entry_created_at or ""
        conn = self._get_conn()
        if consent is None:
            conn.execute(
                "DELETE FROM song_media_consent WHERE entry_id = ? AND entry_created_at = ? "
                "AND name_key = ?", (entry_id, created, key))
        else:
            conn.execute(
                "INSERT INTO song_media_consent (entry_id, entry_created_at, name_key, "
                "display_name, consent, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime')) "
                "ON CONFLICT(entry_id, entry_created_at, name_key) DO UPDATE SET "
                "display_name = excluded.display_name, consent = excluded.consent, "
                "source = excluded.source, updated_at = excluded.updated_at",
                (entry_id, created, key, name if key == "*" else " ".join(name.split()),
                 consent, source))
        self._log_consent_event(conn, "song", name, consent, source, entry_id=entry_id)
        conn.commit()

    def get_song_media_consents(self):
        """``{(entry_id, entry_created_at): {name_key: consent}}`` — every KJ
        per-song override (keys are unique across nights)."""
        out = {}
        for entry_id, created, key, consent in self._get_conn().execute(
                "SELECT entry_id, entry_created_at, name_key, consent FROM song_media_consent"):
            out.setdefault((entry_id, created), {})[key] = consent
        return out

    def get_request_media_consents_by_entry(self):
        """Tonight's singer per-song overrides: ``{entry_id: {name_key: consent}}``."""
        night_started = self.get_night_started_at()
        if not night_started:
            return {}
        out = {}
        for entry_id, name, consent in self._get_conn().execute(
                "SELECT linked_entry_id, singer_name, media_consent FROM sing_requests "
                "WHERE media_consent IS NOT NULL AND linked_entry_id IS NOT NULL "
                "AND status = 'approved' AND created_at >= ?", (night_started,)):
            out.setdefault(entry_id, {})[self.photo_consent_key(name)] = consent
        return out

    def get_night_consent_defaults(self, night):
        """Night-default consent per person for a past (or current) night.

        Returns ``(defaults, source)``: ``defaults`` is ``{name_key: {"consent",
        "instagram"}}`` with the LAST choice made that night; ``source`` is
        'events' when the consent event log covers the night, else 'legacy'
        (pre-log nights: singer_photo_consent rows last updated that night).
        """
        from night_log import night_of
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT name_key, consent, instagram FROM photo_consent_events "
            "WHERE night = ? AND scope = 'night' ORDER BY id", (night,)).fetchall()
        has_log = conn.execute(
            "SELECT 1 FROM photo_consent_events WHERE night <= ? LIMIT 1", (night,)).fetchone()
        if has_log:
            out = {}
            for key, consent, handle in rows:
                if consent is None:
                    out.pop(key, None)
                else:
                    out[key] = {"consent": consent, "instagram": handle}
            return out, "events"
        out = {}
        for key, consent, handle, updated in conn.execute(
                "SELECT name_key, consent, instagram, updated_at FROM singer_photo_consent"):
            if night_of(updated) == night:
                out[key] = {"consent": consent, "instagram": handle}
        return out, "legacy"

    def get_tip_settings(self):
        """Return the KJ-saved tip settings dict (may be empty)."""
        raw = self._get_meta(self.TIP_SETTINGS_KEY)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    def set_tip_settings(self, settings):
        """Merge-validate ``settings`` into the stored blob.

        Only allowlisted keys are kept; strings are trimmed; an empty string
        deletes the key (so clearing a field in the modal reverts to the
        config.json / default fallback). Raises ValueError on bad types.
        """
        if not isinstance(settings, dict):
            raise ValueError("tip settings must be an object")
        current = self.get_tip_settings()
        for key, value in settings.items():
            expected = self.TIP_SETTING_FIELDS.get(key)
            if expected is None:
                continue   # unknown key — ignore rather than fail the save
            if value is None:
                current.pop(key, None)
                continue
            if expected is bool:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be a boolean")
                current[key] = value
            elif expected is str:
                if not isinstance(value, str):
                    raise ValueError(f"{key} must be a string")
                trimmed = value.strip()
                if trimmed:
                    current[key] = trimmed
                else:
                    current.pop(key, None)
            else:   # numeric threshold
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"{key} must be a number")
                import math
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"{key} must be a finite number >= 0")
                current[key] = value
        self._set_meta(self.TIP_SETTINGS_KEY, json.dumps(current))
        return current

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

    def device_singer_names(self, device_id, since):
        """Distinct singer names this device has submitted under since ``since``
        (the night marker) — the server's own record of who the phone is."""
        if not device_id or not since:
            return []
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT singer_name FROM sing_requests "
            "WHERE device_id = ? AND created_at >= ? AND singer_name != ''",
            (device_id, since),
        ).fetchall()
        return [r[0] for r in rows]

    def singer_names_with_devices(self, since):
        """Singer names tonight that at least one phone has submitted as — the
        singers who can self-serve from My songs."""
        if not since:
            return []
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT singer_name FROM sing_requests "
            "WHERE device_id IS NOT NULL AND device_id != '' "
            "AND created_at >= ? AND singer_name != ''",
            (since,),
        ).fetchall()
        return [r[0] for r in rows]

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

    def set_request_phone(self, request_id, phone):
        """Update just the contact phone on a request (singer added/changed
        their number after submitting). Returns the updated row."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE sing_requests SET phone = ? WHERE id = ?",
            ((phone or "").strip(), request_id),
        )
        conn.commit()
        return self.get_request(request_id)

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

    def set_auto_status(self, request_id, status, error=None):
        """Record auto-approve progress on a request: 'failed' (with a KJ-facing
        ``error``) or None to clear. Use ``claim_approval`` to start one."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE sing_requests SET auto_status = ?, auto_error = ?, auto_started_at = NULL "
            "WHERE id = ?",
            (status, (error or None) if status == "failed" else None, request_id),
        )
        conn.commit()

    def claim_approval(self, request_id, stale_after_s):
        """Atomically mark a pending request 'in_progress' for one approver
        (auto-approve or the KJ's button). Returns False if it isn't pending or
        another approval started less than ``stale_after_s`` ago — so two paths
        can never both add the same song to the rotation."""
        conn = self._get_conn()
        cur = conn.execute(
            "UPDATE sing_requests "
            "   SET auto_status = 'in_progress', auto_error = NULL, "
            "       auto_started_at = datetime('now', 'localtime') "
            " WHERE id = ? AND status = 'pending' "
            "   AND (auto_status IS NOT 'in_progress' "
            "        OR auto_started_at IS NULL "
            "        OR auto_started_at < datetime('now', 'localtime', ?))",
            (request_id, f"-{int(stale_after_s)} seconds"),
        )
        conn.commit()
        return cur.rowcount > 0

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

    def get_gen_account(self, device_id):
        if not device_id:
            return None
        row = self._get_conn().execute(
            "SELECT device_id, email, session_token, verified_at "
            "FROM sing_gen_accounts WHERE device_id = ?", (device_id,),
        ).fetchone()
        return dict(row) if row else None

    def set_gen_account(self, device_id, email, session_token):
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO sing_gen_accounts (device_id, email, session_token) VALUES (?, ?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET email = excluded.email, "
            "session_token = excluded.session_token, "
            "verified_at = datetime('now', 'localtime')",
            (device_id, email, session_token),
        )
        conn.commit()

    def clear_gen_account(self, device_id):
        conn = self._get_conn()
        conn.execute("DELETE FROM sing_gen_accounts WHERE device_id = ?", (device_id,))
        conn.commit()

    def set_request_gen(self, request_id, gen_job_id, gen_submit_state):
        """Record the early (submit-time) gen job for a make request."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE sing_requests SET gen_job_id = ?, gen_submit_state = ? WHERE id = ?",
            (gen_job_id, gen_submit_state, request_id),
        )
        conn.commit()
        return self.get_request(request_id)

    def get_request_by_gen_job_id(self, gen_job_id):
        """The (newest) singer request that created this gen job, or None."""
        if not gen_job_id:
            return None
        row = self._get_conn().execute(
            "SELECT * FROM sing_requests WHERE gen_job_id = ? ORDER BY id DESC LIMIT 1",
            (gen_job_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def count_make_requests_for_device(self, device_id, exclude_request_id=None):
        """Tonight's make requests from one device (cancelled/rejected excluded)."""
        night_started = self.get_night_started_at()
        if not device_id or not night_started:
            return 0
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) FROM sing_requests "
            "WHERE source_type = 'make' AND device_id = ? AND created_at >= ? "
            "AND status IN ('pending', 'approved') AND id != ?",
            (device_id, night_started, exclude_request_id or -1),
        ).fetchone()
        return row[0]

    def find_make_request(self, device_id, singer_name, song_artist, song_title, source_meta):
        """Tonight's live make request (under the current event token) that this
        exact submission already created: same device, singer, song and audio
        choice (gen search session + picked result, or YouTube link) and gen
        account (``source_meta.gen_account``, set by submit). A repeat
        submit returns it instead of asking gen again: a search session is
        single-use there. Anything that differs is a new submission."""
        meta = source_meta if isinstance(source_meta, dict) else {}
        account = meta.get("gen_account")
        if meta.get("search_session_id"):
            same_audio = lambda m: (m.get("search_session_id") == meta["search_session_id"]
                                    and m.get("selection_index") == meta.get("selection_index"))
        elif meta.get("youtube_url"):
            same_audio = lambda m: m.get("youtube_url") == meta["youtube_url"]
        else:
            return None
        night_started = self.get_night_started_at()
        if not (device_id and night_started):
            return None
        rows = self._get_conn().execute(
            "SELECT * FROM sing_requests WHERE source_type = 'make' AND device_id = ? AND token = ? "
            "AND created_at >= ? AND status IN ('pending', 'approved') "
            "AND singer_name = ? AND song_artist = ? AND song_title = ? ORDER BY id DESC",
            (device_id, self.get_token() or "", night_started, singer_name, song_artist, song_title),
        ).fetchall()
        for row in rows:
            try:
                stored = json.loads(row["source_meta"] or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(stored, dict) and same_audio(stored) and stored.get("gen_account") == account:
                return self._row_to_dict(row)
        return None

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

    def host_alias_tonight(self, device_id, since):
        """True when the host (a KJ rename or merge) set this device's alias
        since ``since`` (the night marker). Aliases outlive the night, so an
        older KJ alias doesn't count."""
        device_id = (device_id or "").strip()
        if not device_id or not since:
            return False
        row = self._get_conn().execute(
            "SELECT origin, updated_at FROM singer_aliases WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        return bool(row and row["origin"] == "kj" and (row["updated_at"] or "") >= since)

    def name_claimed_by_others(self, folded_name, account_devices, night_started_at):
        """True when someone other than the account ``account_devices`` might
        be ``folded_name`` tonight: a device outside the account submitted
        under that (folded) name since ``night_started_at``, or has an alias
        to it set since then. Fails closed (True) without a name or night
        marker. Attribution by name (a host-added entry, a duet) is only safe
        when this is False; the history outbox uses the same rule. An app
        "song for a friend" names someone else, so it isn't a claim by the
        requesting device (the friend may well be the account's singer)."""
        if not folded_name or not night_started_at:
            return True
        devices = {d for d in account_devices or () if d}
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT r.device_id, r.singer_name AS name FROM sing_requests r "
            "LEFT JOIN app_friend_requests f ON f.request_id = r.id "
            "WHERE r.created_at >= ? AND r.device_id IS NOT NULL AND r.device_id != '' "
            "AND f.request_id IS NULL",
            (night_started_at,),
        ).fetchall()
        rows += conn.execute(
            "SELECT device_id, canonical_name AS name FROM singer_aliases "
            "WHERE updated_at >= ?",
            (night_started_at,),
        ).fetchall()
        return any(r["device_id"] not in devices and fold_name(r["name"]) == folded_name
                   for r in rows)

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
        # The renamed singer keeps tonight's photo-consent choice.
        try:
            self.carry_photo_consent(old_name, new_name)
        except Exception:
            pass
        return len(device_ids)

    # ------------------------------------------------------------------
    # Push subscription CRUD (sub-project #4)
    # ------------------------------------------------------------------

    def insert_push_subscription(self, token, phone, singer_name, endpoint,
                                  p256dh, auth, user_agent=None, device_id=None):
        """Insert-or-replace on UNIQUE(token, endpoint).

        ``phone`` may be empty for a singer who signed up without one; the row
        is then matched by ``device_id``. One row per endpoint, so a singer who
        later adds a phone updates the same row (no double pushes).
        If a matching row is disabled, re-enable it via disabled_at=NULL.
        Returns the row id.
        """
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO sing_push_subscriptions "
            "  (token, phone, singer_name, endpoint, p256dh, auth, user_agent, "
            "   device_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime')) "
            "ON CONFLICT(token, endpoint) DO UPDATE SET "
            "  phone=excluded.phone, singer_name=excluded.singer_name, "
            "  p256dh=excluded.p256dh, auth=excluded.auth, "
            "  user_agent=excluded.user_agent, "
            "  device_id=COALESCE(excluded.device_id, device_id), "
            "  updated_at=datetime('now', 'localtime'), "
            "  disabled_at=NULL",
            (token, phone or "", singer_name, endpoint, p256dh, auth, user_agent,
             (device_id or None)),
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
            "WHERE token != ? AND COALESCE(kind, 'webpush') = 'webpush' "
            "  AND updated_at < datetime('now', '-7 days', 'localtime')",
            (current_token,),
        )
        conn.commit()

    def find_subs_by_phone(self, token, phone):
        """Return all non-disabled subs matching (token, phone) as dict list."""
        if not phone:
            return []   # '' = phone-less subscription; never a match key
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM sing_push_subscriptions "
            "WHERE token=? AND phone=? AND disabled_at IS NULL",
            (token, phone),
        ).fetchall()
        return [dict(r) for r in rows]

    def find_subs_by_device(self, token, device_id):
        """Return all non-disabled subs matching (token, device_id) as dict list."""
        if not device_id:
            return []
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM sing_push_subscriptions "
            "WHERE token=? AND device_id=? AND disabled_at IS NULL",
            (token, device_id),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Expo push (mobile app)
    # ------------------------------------------------------------------

    EXPO_TOKEN_KEY = "expo"  # sentinel in the NOT NULL `token` column

    def add_expo_subscription(self, device_id, expo_token, platform):
        """Upsert an Expo push token for a device. Idempotent per token.

        Reuses sing_push_subscriptions (web-push NOT NULL columns get ''); the
        UNIQUE(token, endpoint) key is ('expo', <expo_token>). `platform`
        ('ios'|'android') is kept in user_agent.
        """
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO sing_push_subscriptions "
            "  (token, phone, singer_name, endpoint, p256dh, auth, user_agent, "
            "   device_id, kind, expo_token, updated_at) "
            "VALUES (?, '', '', ?, '', '', ?, ?, 'expo', ?, datetime('now', 'localtime')) "
            "ON CONFLICT(token, endpoint) DO UPDATE SET "
            "  device_id=excluded.device_id, user_agent=excluded.user_agent, "
            "  updated_at=datetime('now', 'localtime'), disabled_at=NULL",
            (self.EXPO_TOKEN_KEY, expo_token, platform, device_id or None, expo_token),
        )
        conn.commit()

    def remove_expo_subscription(self, device_id, expo_token):
        """Delete this device's row for an Expo token. Idempotent.

        Scoped to the caller's device so one app user can't unregister
        another's token. Returns the number of rows removed.
        """
        if not device_id:
            return 0
        conn = self._get_conn()
        cur = conn.execute(
            "DELETE FROM sing_push_subscriptions "
            "WHERE kind='expo' AND device_id=? AND expo_token=?",
            (device_id, expo_token),
        )
        conn.commit()
        return cur.rowcount

    def list_active_expo_subscriptions(self):
        """Non-disabled Expo rows (the web-push queries never return them)."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM sing_push_subscriptions "
            "WHERE kind='expo' AND disabled_at IS NULL ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def find_expo_subs_by_device(self, device_id):
        if not device_id:
            return []
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM sing_push_subscriptions "
            "WHERE kind='expo' AND device_id=? AND disabled_at IS NULL",
            (device_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # App accounts (night-scoped device -> gen email links)
    # ------------------------------------------------------------------

    def link_account(self, device_id, gen_email, origin, night_started_at):
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO singer_accounts "
            "  (device_id, gen_email, origin, night_started_at, linked_at) "
            "VALUES (?, ?, ?, ?, datetime('now', 'localtime')) "
            "ON CONFLICT(device_id, night_started_at) DO UPDATE SET "
            "  linked_at=CASE WHEN singer_accounts.gen_email = excluded.gen_email "
            "    THEN singer_accounts.linked_at ELSE excluded.linked_at END, "
            "  gen_email=excluded.gen_email, origin=excluded.origin",
            (device_id, gen_email, origin, night_started_at),
        )
        conn.commit()

    def unlink_account(self, device_id, night_started_at):
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM singer_accounts WHERE device_id=? AND night_started_at=?",
            (device_id, night_started_at),
        )
        conn.commit()

    def account_devices(self, gen_email, night_started_at):
        rows = self._get_conn().execute(
            "SELECT device_id FROM singer_accounts "
            "WHERE gen_email=? AND night_started_at=? ORDER BY linked_at, device_id",
            (gen_email, night_started_at),
        ).fetchall()
        return [r["device_id"] for r in rows]

    def devices_for_email_any_night(self, gen_email):
        """Every device ``singer_accounts`` ever linked to ``gen_email`` (any
        night, app or claimed web devices), compared case-insensitively."""
        email = (gen_email or "").strip().lower()
        if not email:
            return []
        rows = self._get_conn().execute(
            "SELECT DISTINCT device_id FROM singer_accounts "
            "WHERE lower(gen_email)=? ORDER BY device_id",
            (email,),
        ).fetchall()
        return [r["device_id"] for r in rows]

    def night_accounts(self, night_started_at):
        """{gen_email: [device_id, ...]} for every account linked tonight."""
        out = {}
        for r in self._get_conn().execute(
                "SELECT device_id, gen_email FROM singer_accounts "
                "WHERE night_started_at=? ORDER BY linked_at, device_id",
                (night_started_at,)).fetchall():
            out.setdefault(r["gen_email"], []).append(r["device_id"])
        return out

    def is_app_device(self, device_id):
        """True when ``singer_accounts`` has ever recorded ``device_id`` as an
        app device (origin 'app', any night). Web claims refuse such ids."""
        row = self._get_conn().execute(
            "SELECT 1 FROM singer_accounts WHERE device_id=? AND origin='app' LIMIT 1",
            (device_id,),
        ).fetchone()
        return row is not None

    def account_for_device(self, device_id, night_started_at):
        row = self._get_conn().execute(
            "SELECT gen_email FROM singer_accounts "
            "WHERE device_id=? AND night_started_at=?",
            (device_id, night_started_at),
        ).fetchone()
        return row["gen_email"] if row else None

    # ------------------------------------------------------------------
    # Idempotency cache
    # ------------------------------------------------------------------

    # A claim is a placeholder row written before the work starts, so two
    # simultaneous retries with one key can't both do it. Its short TTL means a
    # claim abandoned by a crash frees the key again.
    _IDEMPOTENT_PENDING = '{"__pending__": true}'

    def idempotent_get(self, key):
        """The stored response for ``key``; None when absent, expired or still
        only claimed (see ``idempotent_pending``)."""
        row = self._get_conn().execute(
            "SELECT response_json FROM app_idempotency "
            "WHERE key=? AND expires_at > ?",
            (key, time.time()),
        ).fetchone()
        if row is None or row["response_json"] == self._IDEMPOTENT_PENDING:
            return None
        return json.loads(row["response_json"])

    def idempotent_pending(self, key):
        """True while ``key`` is claimed and has no response yet."""
        row = self._get_conn().execute(
            "SELECT 1 FROM app_idempotency "
            "WHERE key=? AND expires_at > ? AND response_json=?",
            (key, time.time(), self._IDEMPOTENT_PENDING),
        ).fetchone()
        return row is not None

    def idempotent_claim(self, key, ttl_s=300):
        """Atomically claim ``key``. True when this caller owns it (then call
        ``idempotent_put`` on success or ``idempotent_release`` on failure);
        False when another claim or a stored response already holds it."""
        now = time.time()
        conn = self._get_conn()
        conn.execute("DELETE FROM app_idempotency WHERE expires_at <= ?", (now,))
        cur = conn.execute(
            "INSERT OR IGNORE INTO app_idempotency (key, response_json, expires_at) "
            "VALUES (?, ?, ?)",
            (key, self._IDEMPOTENT_PENDING, now + ttl_s),
        )
        conn.commit()
        return cur.rowcount == 1

    def idempotent_release(self, key):
        """Drop a pending claim (never a stored response) so a retry can run."""
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM app_idempotency WHERE key=? AND response_json=?",
            (key, self._IDEMPOTENT_PENDING),
        )
        conn.commit()

    def idempotent_put(self, key, response, ttl_s=86400):
        now = time.time()
        conn = self._get_conn()
        conn.execute("DELETE FROM app_idempotency WHERE expires_at <= ?", (now,))
        conn.execute(
            "INSERT OR REPLACE INTO app_idempotency (key, response_json, expires_at) "
            "VALUES (?, ?, ?)",
            (key, json.dumps(response), now + ttl_s),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # App "song for a friend" markers
    # ------------------------------------------------------------------

    def mark_friend_request(self, request_id, friend_name):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO app_friend_requests (request_id, friend_name) "
            "VALUES (?, ?)",
            (int(request_id), friend_name or ""),
        )
        conn.commit()

    def friend_request_ids(self, request_ids):
        ids = [int(i) for i in request_ids if i is not None]
        if not ids:
            return set()
        marks = ",".join("?" * len(ids))
        rows = self._get_conn().execute(
            f"SELECT request_id FROM app_friend_requests WHERE request_id IN ({marks})",
            ids,
        ).fetchall()
        return {r[0] for r in rows}

    def account_request_names(self, device_ids, since):
        """Distinct singer names on requests from ``device_ids`` since ``since``
        (the night marker), leaving out "song for a friend" requests and
        cancelled/rejected ones: the names those devices sing under."""
        if not since:
            return []
        return self._request_names(device_ids, since=since)

    def request_names_any_night(self, device_ids):
        """``account_request_names`` across every night, also leaving out the
        reorder/tip meta-requests: every name those devices sang under on this
        box (history claims, R35)."""
        return self._request_names(device_ids, since=None, songs_only=True)

    @staticmethod
    def _night_window_end(marker_dt):
        """Noon after the night a marker belongs to (the Night Log's noon roll;
        ``night_log.marker_night`` says which night that is)."""
        from night_log import marker_night
        night = datetime.date.fromisoformat(marker_night(marker_dt))
        return datetime.datetime.combine(night + datetime.timedelta(days=1),
                                         datetime.time(12, 0))

    def _known_night_markers(self, conn):
        markers = {r[0] for r in conn.execute(
            "SELECT DISTINCT night_started_at FROM singer_accounts "
            "UNION SELECT DISTINCT night_started_at FROM history_outbox "
            "WHERE night_started_at NOT LIKE 'claim:%'") if r[0]}
        current = self.get_night_started_at()
        if current:
            markers.add(current)
        return sorted(markers)

    def linked_request_names(self, gen_email):
        """``request_names_any_night`` for the devices ``singer_accounts``
        linked to ``gen_email``, counting each device's requests only on the
        nights it was linked to this email (R49): a phone that later became
        someone else's never lends their names.

        A linked night runs from its marker to the EARLIER of the next known
        marker and the noon that ends that night. Accepted risk: requests made
        on the same night before the device was linked still count (the link
        happens mid-night, and the person holding the phone is usually the
        same all evening)."""
        email = self._norm_email(gen_email)
        if not email:
            return []
        conn = self._get_conn()
        links = conn.execute(
            "SELECT device_id, night_started_at FROM singer_accounts "
            "WHERE lower(gen_email)=? ORDER BY night_started_at, device_id",
            (email,)).fetchall()
        if not links:
            return []
        markers = self._known_night_markers(conn)
        names, seen = [], set()
        for link in links:
            start = link["night_started_at"]
            t = self._parse_marker(start)
            if t is None:
                continue
            end = self._night_window_end(t).strftime("%Y-%m-%d %H:%M:%S")
            later = [m for m in markers if m > start]
            if later and later[0] < end:
                end = later[0]
            for n in self._request_names([link["device_id"]], since=start,
                                         songs_only=True, until=end):
                if n not in seen:
                    seen.add(n)
                    names.append(n)
        return names

    @staticmethod
    def _parse_marker(marker):
        try:
            return datetime.datetime.strptime((marker or "")[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def _request_names(self, device_ids, since=None, songs_only=False, until=None):
        device_ids = [d for d in (device_ids or []) if d]
        if not device_ids:
            return []
        marks = ",".join("?" * len(device_ids))
        sql = ("SELECT DISTINCT r.singer_name FROM sing_requests r "
               "LEFT JOIN app_friend_requests f ON f.request_id = r.id "
               f"WHERE r.device_id IN ({marks}) "
               "AND r.singer_name != '' AND f.request_id IS NULL "
               "AND r.status NOT IN ('cancelled', 'rejected')")
        params = list(device_ids)
        if since is not None:
            sql += " AND r.created_at >= ?"
            params.append(since)
        if until is not None:
            sql += " AND r.created_at < ?"
            params.append(until)
        if songs_only:
            sql += " AND r.source_type NOT IN ('reorder', 'tip')"
        rows = self._get_conn().execute(sql, params).fetchall()
        return [r[0] for r in rows]

    def account_requests(self, device_ids, since, token):
        """Every request from ``device_ids`` under event code ``token`` since
        ``since`` (the night marker), oldest first: the rows an app account
        owns tonight. Empty without devices, marker or token."""
        device_ids = [d for d in device_ids if d]
        if not device_ids or not since or not token:
            return []
        marks = ",".join("?" * len(device_ids))
        rows = self._get_conn().execute(
            f"SELECT * FROM sing_requests WHERE device_id IN ({marks}) "
            "AND token = ? AND created_at >= ? ORDER BY id",
            (*device_ids, token, since),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def night_requests(self, token, since):
        """Every request under event code ``token`` created since ``since``
        (the night marker), oldest first. The event code is reused across
        nights, so this is the night-scoped read (cost tracks tonight only)."""
        if not token or not since:
            return []
        rows = self._get_conn().execute(
            "SELECT * FROM sing_requests WHERE token = ? AND created_at >= ? ORDER BY id",
            (token, since),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def requests_linked_to_entries(self, entry_ids, since):
        """Full rows of requests created since ``since`` (the night marker) that
        are linked to any of ``entry_ids``, oldest first. Night-scoped so a
        reused entry id never matches a past night's request (A.15)."""
        ids = [int(e) for e in (entry_ids or []) if e is not None]
        if not ids or not since:
            return []
        marks = ",".join("?" * len(ids))
        rows = self._get_conn().execute(
            f"SELECT * FROM sing_requests WHERE linked_entry_id IN ({marks}) "
            "AND created_at >= ? ORDER BY id",
            (*ids, since),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def is_friend_request(self, request_id):
        return bool(self.friend_request_ids([request_id]))

    # ------------------------------------------------------------------
    # History outbox (sung songs -> decide)
    # ------------------------------------------------------------------
    # Timestamps are stored as 'YYYY-MM-DD HH:MM:SS' strings. Aware datetimes
    # are converted to UTC; naive datetimes are used as given, so callers must
    # consistently pass naive-UTC (or all aware) values.

    @staticmethod
    def _ts(dt):
        if dt.tzinfo is not None:
            dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def outbox_enqueue(self, history_id, op, payload, night_started_at, now=None):
        """Queue a put/delete for history_id. Returns True if the row changed.

        A different op than the current row (or a row from another night: a
        reused id, A.15) replaces it, resetting it to pending; the same op is
        a no-op. One atomic upsert, so concurrent scans from several request
        threads can't both write it.
        """
        conn = self._get_conn()
        changed = self._outbox_upsert(conn, history_id, op, payload, night_started_at, now)
        conn.commit()
        return changed

    def _outbox_upsert(self, conn, history_id, op, payload, night_started_at, now=None):
        """``outbox_enqueue``'s upsert on ``conn`` without committing, so a
        caller can make it part of a larger transaction. True if the row changed."""
        now_s = self._ts(now or datetime.datetime.now(datetime.timezone.utc))
        cur = conn.execute(
            "INSERT INTO history_outbox "
            "  (history_id, op, payload_json, state, attempts, next_attempt_at, "
            "   last_error, night_started_at, created_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', 0, ?, NULL, ?, ?, ?) "
            "ON CONFLICT(history_id) DO UPDATE SET "
            "  op=excluded.op, payload_json=excluded.payload_json, state='pending', "
            "  attempts=0, next_attempt_at=excluded.next_attempt_at, last_error=NULL, "
            "  night_started_at=excluded.night_started_at, "
            "  created_at=excluded.created_at, updated_at=excluded.updated_at "
            "WHERE history_outbox.op != excluded.op "
            "   OR history_outbox.night_started_at != excluded.night_started_at",
            (history_id, op, json.dumps(payload), now_s, night_started_at,
             now_s, now_s),
        )
        return cur.rowcount == 1

    def outbox_rows_for_night(self, night_started_at):
        """{history_id: {op, state, payload}} for every outbox row of a night."""
        rows = self._get_conn().execute(
            "SELECT history_id, op, state, payload_json FROM history_outbox "
            "WHERE night_started_at=?",
            (night_started_at,),
        ).fetchall()
        return {r["history_id"]: {"op": r["op"], "state": r["state"],
                                  "payload": json.loads(r["payload_json"])}
                for r in rows}

    def outbox_due(self, now, limit=20):
        rows = self._get_conn().execute(
            "SELECT * FROM history_outbox "
            "WHERE state='pending' AND next_attempt_at <= ? "
            "ORDER BY next_attempt_at, created_at LIMIT ?",
            (self._ts(now), limit),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d.pop("payload_json"))
            out.append(d)
        return out

    def outbox_mark_sent(self, history_id, op=None):
        """Mark delivered. With ``op``, only while the row still holds that op:
        a scan that swapped put<->delete mid-delivery keeps the new op pending."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE history_outbox SET state='sent', last_error=NULL, "
            "updated_at=datetime('now') WHERE history_id=? AND (? IS NULL OR op=?)",
            (history_id, op, op),
        )
        conn.commit()

    def outbox_mark_failed(self, history_id, error, next_attempt_at, permanent, op=None):
        conn = self._get_conn()
        conn.execute(
            "UPDATE history_outbox SET attempts=attempts+1, last_error=?, "
            "state=?, next_attempt_at=?, updated_at=datetime('now') "
            "WHERE history_id=? AND (? IS NULL OR op=?)",
            (error, "failed" if permanent else "pending",
             self._ts(next_attempt_at), history_id, op, op),
        )
        conn.commit()

    def outbox_sent_ids_for_night(self, night_started_at):
        """history_ids whose current op is 'put' and not failed (pending or sent)."""
        rows = self._get_conn().execute(
            "SELECT history_id FROM history_outbox "
            "WHERE night_started_at=? AND op='put' AND state IN ('pending','sent')",
            (night_started_at,),
        ).fetchall()
        return {r["history_id"] for r in rows}

    # ------------------------------------------------------------------
    # History claims (past-night songs a singer says were theirs)
    # ------------------------------------------------------------------

    @staticmethod
    def _norm_email(email):
        return (email or "").strip().lower()

    def claims_for_nights(self, nights):
        """``{(night, entry_id): [gen_email, ...]}`` for every claim on ``nights``."""
        nights = [n for n in (nights or []) if n]
        if not nights:
            return {}
        marks = ",".join("?" * len(nights))
        out = {}
        for r in self._get_conn().execute(
                "SELECT night, entry_id, gen_email FROM history_claims "
                f"WHERE night IN ({marks}) ORDER BY claimed_at, gen_email", nights):
            out.setdefault((r["night"], r["entry_id"]), []).append(r["gen_email"])
        return out

    def rejections_for(self, email_hash):
        """``{(night, entry_id)}`` the account (by email hash) said weren't theirs."""
        if not email_hash:
            return set()
        return {(r["night"], r["entry_id"]) for r in self._get_conn().execute(
            "SELECT night, entry_id FROM history_rejections WHERE email_hash=?",
            (email_hash.lower(),))}

    def add_rejections(self, email_hash, night, entry_ids):
        """Record "not mine" for ``entry_ids`` on ``night`` (INSERT OR IGNORE).
        Entry ids must be numeric (ints or digit strings); ``int()`` raises
        on anything else, so validate client input before calling."""
        ids = [int(e) for e in (entry_ids or []) if e is not None]
        if not email_hash or not night or not ids:
            return
        conn = self._get_conn()
        conn.executemany(
            "INSERT OR IGNORE INTO history_rejections (email_hash, night, entry_id) "
            "VALUES (?, ?, ?)",
            [(email_hash.lower(), night, e) for e in ids])
        conn.commit()

    @staticmethod
    def _outbox_holders(conn, entry_ids):
        ids = sorted({str(int(e)) for e in (entry_ids or []) if e is not None})
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        out = {}
        for r in conn.execute(
                "SELECT json_extract(payload_json, '$.entry_id') AS eid, "
                "       json_extract(payload_json, '$.gen_email') AS email "
                "FROM history_outbox WHERE op='put' AND state IN ('pending','sent') "
                "AND night_started_at NOT LIKE 'claim:%' "
                f"AND CAST(json_extract(payload_json, '$.entry_id') AS TEXT) IN ({marks})",
                ids):
            if not r["email"]:
                continue
            out.setdefault(int(r["eid"]), set()).add(SingStore._norm_email(r["email"]))
        return out

    def outbox_holders(self, entry_ids):
        """``{entry_id: {gen_email}}`` from live-night outbox puts (pending or
        sent). Claim rows (night ``claim:...``) are left out: ``history_claims``
        already counts them. Entry ids are monotonic since 2026-06-04 and the
        outbox began 2026-10-22, so the entry id alone identifies the entry on
        nights from 2026-06-04 on (``try_claim`` ignores these for older ones)."""
        return self._outbox_holders(self._get_conn(), entry_ids)

    def claims_for_email(self, gen_email):
        """``{(night, entry_id): member}`` this email holds."""
        email = self._norm_email(gen_email)
        if not email:
            return {}
        return {(r["night"], r["entry_id"]): r["member"] for r in self._get_conn().execute(
            "SELECT night, entry_id, member FROM history_claims WHERE gen_email=?", (email,))}

    def claim_night_ids(self):
        """``{night_date: night_id}`` the claim records already carry (the most
        common per night), so a night keeps its id once a claim went out."""
        counts = {}
        for r in self._get_conn().execute(
                "SELECT night_started_at, json_extract(payload_json, '$.night_id') AS nid, "
                "COUNT(*) AS n FROM history_outbox WHERE night_started_at LIKE 'claim:%' "
                "GROUP BY night_started_at, nid"):
            if not r["nid"]:
                continue
            night = r["night_started_at"][len("claim:"):]
            best = counts.get(night)
            if best is None or (r["n"], r["nid"]) > best:
                counts[night] = (r["n"], r["nid"])
        return {night: nid for night, (_, nid) in counts.items()}

    def remove_rejections(self, email_hash, night, entry_ids):
        """Forget "not mine" for ``entry_ids`` (the account claimed it after all)."""
        ids = [int(e) for e in (entry_ids or []) if e is not None]
        if not email_hash or not ids:
            return
        conn = self._get_conn()
        conn.executemany(
            "DELETE FROM history_rejections WHERE email_hash=? AND night=? AND entry_id=?",
            [(email_hash.lower(), night, e) for e in ids])
        conn.commit()

    def outbox_has_put(self, history_id):
        """True when ``history_id``'s outbox row is a put that isn't failed."""
        row = self._get_conn().execute(
            "SELECT 1 FROM history_outbox WHERE history_id=? AND op='put' "
            "AND state IN ('pending','sent')", (history_id,)).fetchone()
        return row is not None

    def claim_rows_for_entries(self, entry_ids):
        """Full ``history_claims`` rows for ``entry_ids`` on nights from
        ``ENTRY_IDS_UNIQUE_SINCE`` on (where an entry id names one entry)."""
        ids = sorted({int(e) for e in (entry_ids or []) if e is not None})
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return [dict(r) for r in self._get_conn().execute(
            f"SELECT * FROM history_claims WHERE night >= ? AND entry_id IN ({marks}) "
            "ORDER BY night, entry_id, gen_email", (ENTRY_IDS_UNIQUE_SINCE, *ids))]

    def claim_holders(self, entry_ids):
        """``{entry_id: {gen_email}}`` from ``history_claims`` on nights from
        ``ENTRY_IDS_UNIQUE_SINCE`` on (where an entry id names one entry): the
        live outbox's view of who already claimed tonight's entries (R47)."""
        ids = sorted({int(e) for e in (entry_ids or []) if e is not None})
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        out = {}
        for r in self._get_conn().execute(
                "SELECT entry_id, gen_email FROM history_claims "
                f"WHERE night >= ? AND entry_id IN ({marks})",
                (ENTRY_IDS_UNIQUE_SINCE, *ids)):
            out.setdefault(r["entry_id"], set()).add(self._norm_email(r["gen_email"]))
        return out

    def try_claim(self, night, entry_id, email, history_id, member, capacity):
        """Claim ``(night, entry_id)`` for ``email`` in one ``BEGIN IMMEDIATE``
        transaction: ``"already_mine"`` when this email already holds it (a
        claim or a live outbox record), ``"taken"`` when ``max(1, capacity)``
        accounts hold it, else ``"claimed"``. Live outbox records count as
        holders only from ``ENTRY_IDS_UNIQUE_SINCE`` on (older nights recycled
        entry ids, so today's entry 12 says nothing about theirs)."""
        email = self._norm_email(email)
        entry_id = int(entry_id)
        capacity = max(1, int(capacity or 0))
        conn = self._get_conn()
        if conn.in_transaction:
            # Someone left a write open on this connection: never commit it
            # under a claim's name.
            logger.warning("try_claim: rolling back a stray open transaction")
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        try:
            holders = {self._norm_email(r[0]) for r in conn.execute(
                "SELECT gen_email FROM history_claims WHERE night=? AND entry_id=?",
                (night, entry_id))}
            if night >= ENTRY_IDS_UNIQUE_SINCE:
                holders |= self._outbox_holders(conn, [entry_id]).get(entry_id, set())
            if email in holders:
                result = "already_mine"
            elif len(holders) >= capacity:
                result = "taken"
            else:
                conn.execute(
                    "INSERT INTO history_claims "
                    "  (night, entry_id, gen_email, history_id, member) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (night, entry_id, email, history_id, member or ""))
                result = "claimed"
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return result

    def delete_claim(self, night, entry_id, email=None):
        """Delete the claims on ``(night, entry_id)`` (only ``email``'s when
        given) and return the deleted rows (host undo)."""
        conn = self._get_conn()
        sql = "FROM history_claims WHERE night=? AND entry_id=?"
        params = [night, int(entry_id)]
        if email is not None:
            sql += " AND gen_email=?"
            params.append(self._norm_email(email))
        rows = [dict(r) for r in conn.execute(f"SELECT * {sql}", params)]
        if rows:
            conn.execute(f"DELETE {sql}", params)
            conn.commit()
        return rows

    def release_claims(self, night, entry_id, email=None, now=None):
        """Delete the claims on ``(night, entry_id)`` (only ``email``'s when
        given) and queue each one's retraction, in ONE ``BEGIN IMMEDIATE``
        transaction: either the claim rows go and their deletes are queued, or
        nothing changes. Each delete body is ``{history_id, gen_email,
        entry_id, night_id}``; ``night_id`` is copied from the claim's own
        outbox row (else the night's other claim records), so
        ``claim_night_ids`` keeps the night's id after an undo. Returns the
        deleted claim rows."""
        conn = self._get_conn()
        if conn.in_transaction:
            logger.warning("release_claims: rolling back a stray open transaction")
            conn.rollback()
        sql = "FROM history_claims WHERE night=? AND entry_id=?"
        params = [night, int(entry_id)]
        if email is not None:
            sql += " AND gen_email=?"
            params.append(self._norm_email(email))
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = [dict(r) for r in conn.execute(f"SELECT * {sql}", params)]
            if rows:
                conn.execute(f"DELETE {sql}", params)
                fallback = conn.execute(
                    "SELECT json_extract(payload_json, '$.night_id') AS nid, COUNT(*) AS n "
                    "FROM history_outbox WHERE night_started_at=? AND nid IS NOT NULL "
                    "GROUP BY nid ORDER BY n DESC, nid DESC LIMIT 1",
                    ("claim:" + night,)).fetchone()
                for row in rows:
                    own = conn.execute(
                        "SELECT json_extract(payload_json, '$.night_id') FROM history_outbox "
                        "WHERE history_id=?", (row["history_id"],)).fetchone()
                    nid = (own[0] if own and own[0] else None) or (fallback[0] if fallback else None)
                    gone = {"history_id": row["history_id"], "gen_email": row["gen_email"],
                            "entry_id": str(row["entry_id"])}
                    if nid:
                        gone["night_id"] = nid
                    self._outbox_upsert(conn, row["history_id"], "delete", gone,
                                        "claim:" + row["night"], now)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return rows

    def claim_rows_since(self, since):
        """Full ``history_claims`` rows on nights ``>= since``."""
        return [dict(r) for r in self._get_conn().execute(
            "SELECT * FROM history_claims WHERE night >= ? ORDER BY night, entry_id, gen_email",
            (since,))]

    # ------------------------------------------------------------------
    # Account deletion (gen account removed -> erase this person's links)
    # ------------------------------------------------------------------

    @staticmethod
    def _latest_device_owners(conn):
        """``{device_id: (timestamp, email)}``: each device's MOST RECENT link
        across ``singer_accounts`` (linked_at) and ``sing_gen_accounts``
        (verified_at). A device belongs to that email only."""
        latest = {}
        for r in conn.execute("SELECT device_id, gen_email AS email, linked_at AS ts "
                              "FROM singer_accounts "
                              "UNION ALL SELECT device_id, email, verified_at "
                              "FROM sing_gen_accounts"):
            cand = (r["ts"] or "", r["email"])
            if r["device_id"] not in latest or cand[0] >= latest[r["device_id"]][0]:
                latest[r["device_id"]] = cand
        return latest

    def alias_since_link(self, device_id, gen_email):
        """The device's alias when it was set during ``gen_email``'s current
        run of ownership: at or after the earliest link by this email that
        follows the latest link by any other email (links are
        ``singer_accounts.linked_at`` and ``sing_gen_accounts.verified_at``).
        So re-linking the same phone keeps its alias, while on a phone that
        went A -> B -> A, the alias B set never becomes A's."""
        email = self._norm_email(gen_email)
        if not device_id or not email:
            return None
        conn = self._get_conn()
        row = conn.execute("SELECT canonical_name, updated_at FROM singer_aliases "
                           "WHERE device_id=?", (device_id,)).fetchone()
        if row is None:
            return None
        links = conn.execute(
            "SELECT linked_at AS ts, lower(gen_email) AS email FROM singer_accounts "
            "WHERE device_id=? UNION ALL SELECT verified_at, lower(email) "
            "FROM sing_gen_accounts WHERE device_id=?", (device_id, device_id)).fetchall()
        others = [r["ts"] or "" for r in links if r["email"] != email]
        after = max(others) if others else ""
        mine = [r["ts"] or "" for r in links if r["email"] == email and (r["ts"] or "") > after]
        if not mine:
            return None
        if (row["updated_at"] or "") < min(mine):
            return None
        return row["canonical_name"]

    def latest_owner_emails(self, device_ids):
        """``{device_id: email}`` (lowercased) for the devices' current owners."""
        want = {d for d in (device_ids or []) if d}
        if not want:
            return {}
        return {d: self._norm_email(e)
                for d, (_, e) in self._latest_device_owners(self._get_conn()).items()
                if d in want}

    @staticmethod
    def email_hash(email):
        """sha256 hex of the lowercased email: the id gen uses for a deletion."""
        return hashlib.sha256((email or "").lower().encode("utf-8")).hexdigest()

    def forget_gen_account(self, email_hash):
        """Erase every local link to the gen account whose email hashes to
        ``email_hash``. Idempotent; one transaction (all or nothing).

        Removes the device links (``singer_accounts``, ``sing_gen_accounts``),
        those devices' push subscriptions, aliases and request phone numbers,
        the app's friend-request markers, every ``history_outbox`` row for the
        email (pending, failed and sent; past-night claims included), the account's
        ``history_claims`` and ``history_rejections``, and the photo/song media
        consent rows keyed on the names those devices sang under. ``photo_consent_events`` is an
        append-only log, so its rows stay with the link (``request_id``) and
        Instagram handle cleared. Returns counts per kind (all zero when
        nothing matched).
        """
        want = (email_hash or "").lower()
        counts = {k: 0 for k in (
            "singer_accounts", "sing_gen_accounts", "push_subscriptions",
            "phones_cleared", "aliases", "app_friend_requests",
            "singer_photo_consent", "song_media_consent",
            "photo_consent_events_unlinked", "history_outbox",
            "history_claims", "history_rejections")}
        if not want:
            return counts
        conn = self._get_conn()
        matches = lambda e: self.email_hash(e) == want  # noqa: E731
        # Ownership. sing_gen_accounts is one row per device (device_id PRIMARY
        # KEY, upserted), but singer_accounts is one row per (device, night), so a
        # device that switched person A -> B can still carry an old row for A.
        # A device is A's only if A is its MOST RECENT link across both tables;
        # device-wide data (push, alias, phones, requests) is erased only then.
        # A's own link rows are erased wherever they sit.
        latest = self._latest_device_owners(conn)  # device_id -> (timestamp, email)
        a_rows = [(r["device_id"], r["night_started_at"]) for r in conn.execute(
            "SELECT device_id, gen_email, night_started_at FROM singer_accounts")
            if matches(r["gen_email"])]  # singer_accounts keys that are A's
        gen_dev = [r["device_id"] for r in conn.execute(
            "SELECT device_id, email FROM sing_gen_accounts") if matches(r["email"])]
        devices = {d for d, (_, e) in latest.items() if matches(e)}
        outbox_ids = []
        # Every state, sent included (R50): decide removes its own copy on
        # deletion, and outbox_holders must never return a deleted email.
        for r in conn.execute("SELECT history_id, payload_json FROM history_outbox"):
            try:
                email = (json.loads(r["payload_json"]) or {}).get("gen_email")
            except (TypeError, ValueError):
                continue
            if email and matches(email):
                outbox_ids.append(r["history_id"])
        claim_keys = [(r["night"], r["entry_id"], r["gen_email"]) for r in conn.execute(
            "SELECT night, entry_id, gen_email FROM history_claims") if matches(r["gen_email"])]
        has_rejections = conn.execute(
            "SELECT 1 FROM history_rejections WHERE email_hash=? LIMIT 1", (want,)
        ).fetchone() is not None
        if (not devices and not outbox_ids and not a_rows and not gen_dev
                and not claim_keys and not has_rejections):
            return counts

        def marks(items):
            return ",".join("?" * len(items))

        def run(key, sql, params):
            counts[key] += conn.execute(sql, params).rowcount

        dev = sorted(devices)
        try:
            if dev:
                friend_ids = {r[0] for r in conn.execute(
                    "SELECT request_id FROM app_friend_requests")}
                request_ids, names = [], set()
                for r in conn.execute(
                        f"SELECT id, singer_name FROM sing_requests "
                        f"WHERE device_id IN ({marks(dev)})", dev):
                    request_ids.append(r["id"])
                    if r["id"] not in friend_ids:  # a friend request names someone else
                        names.add(fold_name(r["singer_name"]))
                for r in conn.execute(
                        f"SELECT canonical_name FROM singer_aliases "
                        f"WHERE device_id IN ({marks(dev)})", dev):
                    names.add(fold_name(r["canonical_name"]))
                names.discard("")

                run("push_subscriptions",
                    f"DELETE FROM sing_push_subscriptions WHERE device_id IN ({marks(dev)})", dev)
                run("aliases",
                    f"DELETE FROM singer_aliases WHERE device_id IN ({marks(dev)})", dev)
                run("phones_cleared",
                    f"UPDATE sing_requests SET phone='' WHERE phone != '' "
                    f"AND device_id IN ({marks(dev)})", dev)
                if request_ids:
                    run("app_friend_requests",
                        f"DELETE FROM app_friend_requests WHERE request_id IN ({marks(request_ids)})",
                        request_ids)
                    run("photo_consent_events_unlinked",
                        f"UPDATE photo_consent_events SET request_id=NULL, instagram=NULL "
                        f"WHERE request_id IN ({marks(request_ids)})", request_ids)
                if names:
                    # Keep consent for names another (not-forgotten) device still uses.
                    shared = set()
                    for r in conn.execute("SELECT singer_name FROM sing_requests "
                                          f"WHERE device_id NOT IN ({marks(dev)})", dev):
                        shared.add(fold_name(r["singer_name"]))
                    for r in conn.execute("SELECT canonical_name FROM singer_aliases "
                                          f"WHERE device_id NOT IN ({marks(dev)})", dev):
                        shared.add(fold_name(r["canonical_name"]))
                    keys = sorted(names - shared)
                if names and keys:
                    run("singer_photo_consent",
                        f"DELETE FROM singer_photo_consent WHERE name_key IN ({marks(keys)})", keys)
                    run("song_media_consent",
                        f"DELETE FROM song_media_consent WHERE name_key IN ({marks(keys)})", keys)
            for d, n in a_rows:
                run("singer_accounts",
                    "DELETE FROM singer_accounts WHERE device_id=? AND night_started_at=?",
                    (d, n))
            for d in gen_dev:
                run("sing_gen_accounts", "DELETE FROM sing_gen_accounts WHERE device_id=?", (d,))
            for night, entry_id, email in claim_keys:
                run("history_claims",
                    "DELETE FROM history_claims WHERE night=? AND entry_id=? AND gen_email=?",
                    (night, entry_id, email))
            run("history_rejections",
                "DELETE FROM history_rejections WHERE email_hash=?", (want,))
            if outbox_ids:
                run("history_outbox",
                    f"DELETE FROM history_outbox WHERE history_id IN ({marks(outbox_ids)})",
                    outbox_ids)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return counts

