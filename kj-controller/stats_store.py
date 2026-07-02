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
