"""StatsStore: SQLite store for karaoke play and preview events.

Per-thread connections (threading.local) + WAL, mirroring RotationStore and
MediaLibraryStore — a shared connection across Flask + background threads
caused prior outages.
"""

import sqlite3
import threading


class StatsStore:
    _MEMORY = ":memory:"

    def __init__(self, db_path):
        self.db_path = db_path
        self._local = threading.local()
        self._memory_conn = None
        self._memory_lock = threading.Lock()
        self.init_schema()

    def _open_conn(self):
        conn = sqlite3.connect(
            self.db_path, timeout=10, check_same_thread=(self.db_path != self._MEMORY)
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _get_conn(self):
        if self.db_path == self._MEMORY:
            if self._memory_conn is None:
                self._memory_conn = self._open_conn()
            return self._memory_conn
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open_conn()
            self._local.conn = conn
        return conn

    def _lock(self):
        return self._memory_lock if self.db_path == self._MEMORY else _NULLCTX

    def init_schema(self):
        conn = self._get_conn()
        with self._lock():
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS play_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_id TEXT NOT NULL,
                    song_key TEXT,
                    singer TEXT,
                    singer_norm TEXT,
                    played_at TEXT NOT NULL,
                    night_date TEXT,
                    entry_id INTEGER,
                    source TEXT NOT NULL DEFAULT 'live',
                    artist TEXT,
                    title TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_play_events_media ON play_events(media_id);
                CREATE INDEX IF NOT EXISTS idx_play_events_song ON play_events(song_key);
                CREATE INDEX IF NOT EXISTS idx_play_events_singer ON play_events(singer_norm);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_play_events_entry
                ON play_events(entry_id) WHERE entry_id IS NOT NULL AND source='live';

                CREATE TABLE IF NOT EXISTS preview_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_id TEXT NOT NULL,
                    song_key TEXT,
                    previewed_at TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'live',
                    artist TEXT,
                    title TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_preview_events_media ON preview_events(media_id);

                CREATE TABLE IF NOT EXISTS version_notes (
                    media_id TEXT PRIMARY KEY,
                    note TEXT,
                    label TEXT,
                    artist TEXT,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def record_play(self, media_id, *, entry_id=None, singer=None, artist=None,
                    title=None, song_key=None, played_at=None, night_date=None,
                    source="live"):
        """Insert a play event. Return True iff a row was inserted.

        Dedup: one live play per entry_id (partial UNIQUE index). When entry_id
        is None and source='live', a 120s same-media_id window guard. Empty
        media_id is a no-op. Never nests `with self._lock()`.
        """
        if not media_id:
            return False
        conn = self._get_conn()
        with self._lock():
            if entry_id is None and source == "live":
                dup = conn.execute(
                    "SELECT 1 FROM play_events WHERE media_id=? AND entry_id IS NULL "
                    "AND source='live' AND played_at >= datetime('now','-120 seconds') "
                    "LIMIT 1", (media_id,)).fetchone()
                if dup:
                    return False
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO play_events
                    (media_id, song_key, singer, singer_norm, played_at, night_date,
                     entry_id, source, artist, title)
                VALUES (?,?,?,?,
                        COALESCE(?, datetime('now')),
                        COALESCE(?, date('now','localtime')),
                        ?,?,?,?)
                """,
                (media_id, song_key, singer, _norm_singer(singer),
                 played_at, night_date, entry_id, source, artist, title))
            conn.commit()
            return cur.rowcount > 0

    def record_preview(self, media_id, *, artist=None, title=None,
                       song_key=None, source="live"):
        if not media_id:
            return False
        conn = self._get_conn()
        with self._lock():
            if source == "live":
                dup = conn.execute(
                    "SELECT 1 FROM preview_events WHERE media_id=? AND source='live' "
                    "AND previewed_at >= datetime('now','-60 seconds') LIMIT 1",
                    (media_id,)).fetchone()
                if dup:
                    return False
            cur = conn.execute(
                """
                INSERT INTO preview_events (media_id, song_key, previewed_at, source, artist, title)
                VALUES (?,?, datetime('now'), ?,?,?)
                """,
                (media_id, song_key, source, artist, title))
            conn.commit()
            return cur.rowcount > 0

    def stats_for(self, media_ids):
        ids = list({m for m in media_ids if m})
        if not ids:
            return {}
        ph = ",".join("?" * len(ids))
        out = {m: {"plays": 0, "previews": 0, "last_played": None} for m in ids}
        conn = self._get_conn()
        with self._lock():
            for r in conn.execute(
                f"SELECT media_id, COUNT(*) c, MAX(played_at) lp FROM play_events "
                f"WHERE media_id IN ({ph}) GROUP BY media_id", ids):
                out[r["media_id"]]["plays"] = r["c"]
                out[r["media_id"]]["last_played"] = r["lp"]
            for r in conn.execute(
                f"SELECT media_id, COUNT(*) c FROM preview_events "
                f"WHERE media_id IN ({ph}) GROUP BY media_id", ids):
                out[r["media_id"]]["previews"] = r["c"]
        return out

    def usual_media_id(self, media_ids):
        ids = list({m for m in media_ids if m})
        if not ids:
            return None
        ph = ",".join("?" * len(ids))
        conn = self._get_conn()
        with self._lock():
            r = conn.execute(
                f"SELECT media_id, COUNT(*) c FROM play_events WHERE media_id IN ({ph}) "
                f"GROUP BY media_id ORDER BY c DESC, MAX(played_at) DESC LIMIT 1",
                ids).fetchone()
        return r["media_id"] if r and r["c"] > 0 else None


def _norm_singer(s):
    if s is None:
        return ""
    return " ".join(s.split()).lower()


class _NullCtx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


_NULLCTX = _NullCtx()
