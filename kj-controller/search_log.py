"""Persistent log of singer searches and what singers chose afterwards.

Andrew (2026-09-28): "let's make sure we persistently collect metadata/logs about
what options singers actually choose after the search/auto-correction results
land, so after a few live events we can review the results and identify any
issues or edge cases worth addressing". Review with scripts/search_log_report.py.

One append-only SQLite table (its own file, ``search_log.db`` beside the app, so
it never touches the request/rotation databases). Every row carries a
client-generated ``search_id`` that ties a search to the identification shown
and to whatever the singer did next:

  server-side  search      what karaoke search returned for the query
               identify    what song identification said (on-device matcher)
               resolve     what the Gemini fallback said (gen)
  client-side  choice      the singer's action: kept/undid a tidy, picked a
                           "Which one?" candidate, requested a song/version,
                           sent a make-it (and whether they edited the
                           pre-filled artist/title), pasted a YouTube link …

Logging must never break a search: every write swallows its own errors.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time

log = logging.getLogger(__name__)

# Client-side "choice" actions the endpoint accepts (anything else is rejected).
CHOICE_ACTIONS = {
    "accept_tidy",          # left the "Tidied/Corrected to …" applied and went on
    "keep_typed",           # tapped "keep what I typed" / Undo
    "reapply_tidy",         # tapped "use tidied version" / "use the correction" again
    "pick_candidate",       # chose a song from the "Which one?" list
    "not_it",               # opened "not it?" on the identified song
    "request_song",         # requested a karaoke song/version from the results
    "make_submit",          # sent a Generate-on-demand request
    "youtube_submit",       # pasted a YouTube link
    "describe_open",        # opened "Can't remember the name? Describe it"
    "describe_submit",      # sent a description to the Gemini fallback
}
MAX_DATA_BYTES = 4000
# Retention: a year of shows is plenty for review; the row cap bounds the file
# (~2 KB/row worst case → ~1 GB) even if something spams the endpoint.
RETENTION_DAYS = 365
MAX_ROWS = 500_000
PRUNE_EVERY = 1000        # writes between prunes (also pruned on first open)


def default_db_path(config):
    return (config or {}).get("search_log_db") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "search_log.db")


class SearchLog:
    def __init__(self, db_path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._ready = False
        self._writes = 0

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        if not self._ready:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS search_events (
                    id INTEGER PRIMARY KEY,
                    ts REAL NOT NULL,
                    type TEXT NOT NULL,
                    search_id TEXT,
                    device_id TEXT,
                    query TEXT,
                    data TEXT
                );
                CREATE INDEX IF NOT EXISTS search_events_sid ON search_events(search_id);
                CREATE INDEX IF NOT EXISTS search_events_ts ON search_events(ts);
            """)
            self._prune(conn)
            self._ready = True
        return conn

    @staticmethod
    def _prune(conn):
        """Drop rows past retention, then the oldest beyond MAX_ROWS (on first open)."""
        conn.execute("DELETE FROM search_events WHERE ts < ?", (time.time() - RETENTION_DAYS * 86400,))
        conn.execute("DELETE FROM search_events WHERE id <= (SELECT MAX(id) FROM search_events) - ?",
                     (MAX_ROWS,))
        conn.commit()

    def log(self, type_, *, search_id=None, device_id=None, query=None, data=None):
        """Append one event; never raises."""
        try:
            blob = json.dumps(data or {}, ensure_ascii=False, default=str)
            if len(blob.encode()) > MAX_DATA_BYTES:
                # Fixed-size marker (a huge key must not sneak the size back in).
                keys = sorted(str(k)[:40] for k in (data or {}).keys())[:20]
                blob = json.dumps({"truncated": True, "keys": keys})
            with self._lock:
                conn = self._conn()
                try:
                    conn.execute(
                        "INSERT INTO search_events(ts, type, search_id, device_id, query, data) "
                        "VALUES (?,?,?,?,?,?)",
                        (time.time(), type_, (search_id or "")[:64] or None,
                         (device_id or "")[:64] or None, (query or "")[:300] or None, blob))
                    conn.commit()
                    self._writes += 1
                    if self._writes % PRUNE_EVERY == 0:    # long-running app: keep both limits
                        self._prune(conn)
                finally:
                    conn.close()
        except Exception:  # noqa: BLE001 — logging must never break a search
            log.warning("search log write failed", exc_info=True)

    def events(self, since=None, limit=None):
        """Rows as dicts, oldest first (for the review script and tests)."""
        with self._lock:
            conn = self._conn()
            try:
                conn.row_factory = sqlite3.Row
                sql = "SELECT * FROM search_events"
                args = []
                if since is not None:
                    sql += " WHERE ts >= ?"
                    args.append(since)
                sql += " ORDER BY id"
                if limit:
                    sql += " LIMIT ?"
                    args.append(limit)
                rows = conn.execute(sql, args).fetchall()
            finally:
                conn.close()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["data"] = json.loads(d["data"] or "{}")
            except ValueError:
                d["data"] = {}
            out.append(d)
        return out
