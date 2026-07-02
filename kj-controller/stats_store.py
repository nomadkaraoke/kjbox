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
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(play_events)")}
            if "artist_norm" not in cols:
                conn.execute("ALTER TABLE play_events ADD COLUMN artist_norm TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_play_events_artist "
                         "ON play_events(artist_norm)")
            conn.commit()
        # Backfill runs OUTSIDE the lock — self._lock() is non-reentrant, never nest it.
        self._backfill_artist_norm()

    def _backfill_artist_norm(self):
        """Populate artist_norm for any rows missing it (post-migration one-time)."""
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                "SELECT id, artist FROM play_events "
                "WHERE artist_norm IS NULL AND artist IS NOT NULL AND artist <> ''"
            ).fetchall()
            for r in rows:
                conn.execute("UPDATE play_events SET artist_norm=? WHERE id=?",
                             (_norm_artist(r["artist"]), r["id"]))
            if rows:
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
                     entry_id, source, artist, artist_norm, title)
                VALUES (?,?,?,?,
                        COALESCE(?, datetime('now')),
                        COALESCE(?, date('now','localtime')),
                        ?,?,?,?,?)
                """,
                (media_id, song_key, singer, _norm_singer(singer),
                 played_at, night_date, entry_id, source, artist,
                 _norm_artist(artist), title))
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

    def get_note(self, media_id):
        conn = self._get_conn()
        with self._lock():
            r = conn.execute(
                "SELECT * FROM version_notes WHERE media_id=?", (media_id,)).fetchone()
        return dict(r) if r else None

    def upsert_note(self, media_id, note, label, artist=None, title=None):
        conn = self._get_conn()
        with self._lock():
            conn.execute(
                """
                INSERT INTO version_notes
                    (media_id, note, label, artist, title, created_at, updated_at)
                VALUES (?,?,?,?,?, datetime('now'), datetime('now'))
                ON CONFLICT(media_id) DO UPDATE SET
                    note=excluded.note,
                    label=excluded.label,
                    artist=COALESCE(version_notes.artist, excluded.artist),
                    title=COALESCE(version_notes.title, excluded.title),
                    updated_at=datetime('now')
                """,
                (media_id, note, label, artist, title))
            conn.commit()
        return self.get_note(media_id)

    def distinct_labels(self):
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                "SELECT DISTINCT label FROM version_notes "
                "WHERE label IS NOT NULL AND label <> '' ORDER BY label").fetchall()
        return [r["label"] for r in rows]

    def top_songs(self, *, singer=None, since=None, limit=10):
        clauses = ["song_key IS NOT NULL AND song_key <> ''"]
        params = []
        if singer:
            clauses.append("singer_norm=?")
            params.append(_norm_singer(singer))
        if since:
            clauses.append("played_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                f"""SELECT song_key, COUNT(*) plays, MAX(artist) artist, MAX(title) title
                    FROM play_events WHERE {where}
                    GROUP BY song_key ORDER BY plays DESC, MAX(played_at) DESC LIMIT ?""",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]

    def top_singers(self, *, since=None, limit=10):
        clauses = ["singer_norm IS NOT NULL AND singer_norm <> ''"]
        params = []
        if since:
            clauses.append("played_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                f"""SELECT MAX(singer) singer, COUNT(*) plays,
                           COUNT(DISTINCT song_key) distinct_songs
                    FROM play_events WHERE {where}
                    GROUP BY singer_norm ORDER BY plays DESC LIMIT ?""",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]

    def overview(self, *, since=None):
        params = []
        where = ""
        if since:
            where = "WHERE played_at >= ?"
            params.append(since)
        conn = self._get_conn()
        with self._lock():
            row = conn.execute(
                f"""SELECT COUNT(*) total_plays,
                           COUNT(DISTINCT song_key) distinct_songs,
                           COUNT(DISTINCT NULLIF(singer_norm,'')) distinct_singers,
                           COUNT(DISTINCT NULLIF(artist_norm,'')) distinct_artists,
                           MIN(played_at) first_played, MAX(played_at) last_played
                    FROM play_events {where}""", params).fetchone()
            last30 = conn.execute(
                "SELECT COUNT(*) c FROM play_events "
                "WHERE played_at >= datetime('now','-30 days')").fetchone()["c"]
        d = dict(row)
        d["plays_last_30d"] = last30
        return d

    def top_artists(self, *, since=None, limit=25):
        clauses = ["artist_norm IS NOT NULL AND artist_norm <> ''"]
        params = []
        if since:
            clauses.append("played_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                f"""SELECT MAX(artist) artist, COUNT(*) plays,
                           COUNT(DISTINCT song_key) distinct_songs
                    FROM play_events WHERE {where}
                    GROUP BY artist_norm ORDER BY plays DESC, MAX(played_at) DESC LIMIT ?""",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]

    def artist_songs(self, artist, *, since=None, limit=100):
        an = _norm_artist(artist)
        if not an:
            return []
        clauses = ["artist_norm=?", "song_key IS NOT NULL AND song_key <> ''"]
        params = [an]
        if since:
            clauses.append("played_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                f"""SELECT song_key, MAX(artist) artist, MAX(title) title,
                           COUNT(*) plays, COUNT(DISTINCT NULLIF(singer_norm,'')) distinct_singers
                    FROM play_events WHERE {where}
                    GROUP BY song_key ORDER BY plays DESC, MAX(played_at) DESC LIMIT ?""",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]

    def singer_songs(self, singer, *, since=None, limit=100):
        sn = _norm_singer(singer)
        if not sn:
            return []
        clauses = ["singer_norm=?", "song_key IS NOT NULL AND song_key <> ''"]
        params = [sn]
        if since:
            clauses.append("played_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                f"""SELECT song_key, MAX(artist) artist, MAX(title) title, COUNT(*) plays,
                           MIN(played_at) first_sung, MAX(played_at) last_sung
                    FROM play_events WHERE {where}
                    GROUP BY song_key ORDER BY plays DESC, MAX(played_at) DESC LIMIT ?""",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]

    def singer_song_history(self, singer, song_key, *, limit=200):
        sn = _norm_singer(singer)
        if not sn or not song_key:
            return []
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                """SELECT played_at, night_date FROM play_events
                   WHERE singer_norm=? AND song_key=?
                   ORDER BY played_at DESC LIMIT ?""",
                (sn, song_key, limit)).fetchall()
        return [dict(r) for r in rows]

    def song_history(self, song_key, *, since=None, limit=200):
        if not song_key:
            return []
        clauses = ["song_key=?"]
        params = [song_key]
        if since:
            clauses.append("played_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                f"""SELECT singer, played_at, night_date, media_id
                    FROM play_events WHERE {where}
                    ORDER BY played_at DESC LIMIT ?""",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]

    def busiest_nights(self, *, limit=20):
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                """SELECT night_date, COUNT(*) plays,
                          COUNT(DISTINCT NULLIF(singer_norm,'')) distinct_singers,
                          COUNT(DISTINCT song_key) distinct_songs
                   FROM play_events WHERE night_date IS NOT NULL AND night_date <> ''
                   GROUP BY night_date ORDER BY plays DESC, night_date DESC LIMIT ?""",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def night_setlist(self, night_date, *, limit=200):
        if not night_date:
            return []
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                """SELECT played_at, singer, artist, title, song_key, media_id
                   FROM play_events WHERE night_date=?
                   ORDER BY played_at ASC LIMIT ?""",
                (night_date, limit)).fetchall()
        return [dict(r) for r in rows]

    def most_repeated(self, *, since=None, limit=10):
        clauses = ["singer_norm IS NOT NULL AND singer_norm <> ''",
                   "song_key IS NOT NULL AND song_key <> ''"]
        params = []
        if since:
            clauses.append("played_at >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        conn = self._get_conn()
        with self._lock():
            rows = conn.execute(
                f"""SELECT MAX(singer) singer, song_key, MAX(artist) artist,
                           MAX(title) title, COUNT(*) plays
                    FROM play_events WHERE {where}
                    GROUP BY singer_norm, song_key
                    HAVING plays > 1
                    ORDER BY plays DESC, MAX(played_at) DESC LIMIT ?""",
                params + [limit]).fetchall()
        return [dict(r) for r in rows]


def _norm_singer(s):
    if s is None:
        return ""
    return " ".join(s.split()).lower()


# Artist normalization is identical to singer: whitespace-collapse + lowercase.
_norm_artist = _norm_singer


class _NullCtx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


_NULLCTX = _NullCtx()
