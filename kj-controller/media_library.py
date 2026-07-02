# kj-controller/media_library.py
"""MediaLibraryStore: SQLite store of canonical media identity keyed by media_id.

Per-thread connections (threading.local) + WAL, mirroring RotationStore — a
shared connection across Flask + background threads caused a prior outage.
"""

import sqlite3
import threading

from text_normalize import normalize as _normalize


class MediaLibraryStore:
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media_library (
                    media_id          TEXT PRIMARY KEY,
                    source            TEXT NOT NULL DEFAULT '',
                    source_ref        TEXT,
                    artist            TEXT NOT NULL DEFAULT '',
                    title             TEXT NOT NULL DEFAULT '',
                    artist_norm       TEXT NOT NULL DEFAULT '',
                    title_norm        TEXT NOT NULL DEFAULT '',
                    confidence        REAL,
                    parse_method      TEXT,
                    needs_review      INTEGER NOT NULL DEFAULT 0,
                    raw_original_name TEXT,
                    file_path         TEXT,
                    ext               TEXT,
                    created_at        TEXT DEFAULT (datetime('now')),
                    updated_at        TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_media_source ON media_library(source)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_needs_review ON media_library(needs_review)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_media_path ON media_library(file_path)")
            conn.commit()

    def upsert(self, record):
        artist = record.get("artist", "") or ""
        title = record.get("title", "") or ""
        row = {
            "media_id": record["media_id"],
            "source": record.get("source") or "",
            "source_ref": record.get("source_ref"),
            "artist": artist,
            "title": title,
            "artist_norm": _normalize(artist),
            "title_norm": _normalize(title),
            "confidence": record.get("confidence"),
            "parse_method": record.get("parse_method"),
            "needs_review": int(record.get("needs_review") or 0),
            "raw_original_name": record.get("raw_original_name"),
            "file_path": record.get("file_path"),
            "ext": record.get("ext"),
        }
        conn = self._get_conn()
        with self._lock():
            conn.execute(
                """
                INSERT INTO media_library
                    (media_id, source, source_ref, artist, title, artist_norm, title_norm,
                     confidence, parse_method, needs_review, raw_original_name, file_path, ext)
                VALUES
                    (:media_id, :source, :source_ref, :artist, :title, :artist_norm, :title_norm,
                     :confidence, :parse_method, :needs_review, :raw_original_name, :file_path, :ext)
                ON CONFLICT(media_id) DO UPDATE SET
                    source=excluded.source, source_ref=excluded.source_ref,
                    artist=excluded.artist, title=excluded.title,
                    artist_norm=excluded.artist_norm, title_norm=excluded.title_norm,
                    confidence=excluded.confidence, parse_method=excluded.parse_method,
                    needs_review=excluded.needs_review,
                    raw_original_name=COALESCE(media_library.raw_original_name, excluded.raw_original_name),
                    file_path=excluded.file_path, ext=excluded.ext,
                    updated_at=datetime('now')
                """,
                row,
            )
            conn.commit()

    def get(self, media_id):
        conn = self._get_conn()
        with self._lock():
            cur = conn.execute(
                "SELECT * FROM media_library WHERE media_id=?", (media_id,)
            )
            r = cur.fetchone()
            return dict(r) if r else None

    def get_by_path(self, file_path):
        conn = self._get_conn()
        with self._lock():
            cur = conn.execute(
                "SELECT * FROM media_library WHERE file_path=?", (file_path,)
            )
            r = cur.fetchone()
            return dict(r) if r else None

    def set_metadata(self, media_id, artist, title):
        conn = self._get_conn()
        with self._lock():
            cur = conn.execute(
                """
                UPDATE media_library
                SET artist=?, title=?, artist_norm=?, title_norm=?,
                    parse_method='manual', confidence=NULL, needs_review=0,
                    updated_at=datetime('now')
                WHERE media_id=?
                """,
                (artist, title, _normalize(artist), _normalize(title), media_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def apply_parse(self, media_id, artist, title, confidence, threshold):
        """Apply an LLM parse result (parse_method='llm'); gate needs_review on
        the confidence threshold. Returns True if a row was updated."""
        artist = artist or ""
        title = title or ""
        needs_review = 0 if (confidence is not None and confidence >= threshold) else 1
        conn = self._get_conn()
        with self._lock():
            cur = conn.execute(
                """
                UPDATE media_library
                SET artist=?, title=?, artist_norm=?, title_norm=?,
                    confidence=?, parse_method='llm', needs_review=?,
                    updated_at=datetime('now')
                WHERE media_id=?
                """,
                (artist, title, _normalize(artist), _normalize(title),
                 confidence, needs_review, media_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def list_records(self, source=None, needs_review=None):
        clauses, params = [], []
        if source is not None:
            clauses.append("source=?")
            params.append(source)
        if needs_review is not None:
            clauses.append("needs_review=?")
            params.append(int(needs_review))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self._get_conn()
        with self._lock():
            cur = conn.execute(
                f"SELECT * FROM media_library{where} ORDER BY updated_at DESC", params
            )
            return [dict(r) for r in cur.fetchall()]

    def delete(self, media_id):
        conn = self._get_conn()
        with self._lock():
            conn.execute("DELETE FROM media_library WHERE media_id=?", (media_id,))
            conn.commit()


class _NullCtx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


_NULLCTX = _NullCtx()
