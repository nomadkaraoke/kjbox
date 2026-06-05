"""ExternalCatalog: SQLite FTS5-backed searchable catalog for external media."""

import os
import re
import sqlite3

from rapidfuzz import fuzz
from text_normalize import (
    normalize as _normalize_for_search,
    fts_match_query as _fts5_safe_query,
    tokens as _query_tokens,
    LATIN_SPECIAL_MAP,  # re-export for any external importers
    NORMALIZER_VERSION,
)


# Fuzzy fallback score cutoff (0-100). Tunable; validated by scripts/search_metrics.py.
FUZZY_SCORE_CUTOFF = 80
# Min fraction of the query's significant tokens (len>=4) that must appear in a
# fuzzy candidate. Precision gate: stops WRatio's partial_ratio from inventing
# matches that share no real words (real-data analysis: 190/254 fuzzy hits were
# zero-overlap garbage at the old WRatio>=80-only setting).
FUZZY_MIN_TOKEN_OVERLAP = 0.5
# When the query has NO significant (len>=4) tokens, the overlap gate can't apply;
# require a near-exact score instead.
FUZZY_SHORT_QUERY_CUTOFF = 95


def parse_karaoke_filename(filename):
    """Parse karaoke filename into (disc_id, artist, title) components.

    Splits on ' - ' delimiter:
    - 3+ parts: disc_id - artist - title (title may contain ' - ')
    - 2 parts: heuristic — if first part has digits, treat as disc_id
    - 1 part: entire stem as title
    """
    stem = os.path.splitext(filename)[0]
    parts = stem.split(' - ', 2)

    if len(parts) >= 3:
        return (parts[0].strip(), parts[1].strip(), parts[2].strip())
    elif len(parts) == 2:
        first, second = parts[0].strip(), parts[1].strip()
        if re.search(r'\d', first):
            return (first, '', second)
        else:
            return ('', first, second)
    else:
        return ('', '', stem.strip())


def _detect_format(filename):
    """Return format string from filename extension."""
    ext = os.path.splitext(filename)[1].lower()
    format_map = {
        '.zip': 'cdg+mp3',
        '.mp4': 'mp4',
        '.mkv': 'mkv',
        '.avi': 'avi',
        '.webm': 'webm',
        '.mov': 'mov',
        '.mp3': 'mp3',
        '.cdg': 'cdg',
    }
    return format_map.get(ext, ext.lstrip('.') if ext else 'unknown')


class ExternalCatalog:
    """SQLite FTS5-backed catalog for external media files."""

    def __init__(self, config, db_path=None):
        self.config = config
        self.db_path = db_path or config.get(
            'external_catalog_db',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'external_media.db')
        )
        self._conn = None

    def _get_conn(self):
        """Lazy SQLite connection with WAL mode and optimized settings."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA cache_size=-8192")  # 8MB
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def is_available(self):
        """Check if database exists and has data."""
        if not os.path.exists(self.db_path):
            return False
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='media'"
            ).fetchone()
            if not row:
                return False
            count = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
            return count > 0
        except sqlite3.Error:
            return False

    def init_schema(self):
        """Create tables, FTS5 virtual table, and sync triggers."""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                folder TEXT NOT NULL,
                disc_id TEXT,
                artist TEXT,
                title TEXT,
                format TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS media_fts USING fts5(
                artist, title, disc_id,
                content='media', content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );

            -- Standalone (NOT content='') trigram index over normalized
            -- "artist title" for substring/fuzzy candidate lookup. Standalone
            -- so plain "DELETE FROM media_trigram" works during rebuild.
            CREATE VIRTUAL TABLE IF NOT EXISTS media_trigram USING fts5(
                norm_text, tokenize='trigram'
            );

            CREATE TABLE IF NOT EXISTS catalog_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            -- WARNING: these triggers feed RAW artist/title to media_fts, but the
            -- index actually stores NORMALIZED tokens (see _flush_batch /
            -- rebuild_fts, which write text_normalize.normalize(...) output and
            -- bypass these triggers by dropping them during build). The triggers
            -- exist only so ad-hoc INSERTs stay roughly indexed. Do NOT add a
            -- production path that does individual DELETE/UPDATE on `media`: the
            -- 'delete' command here passes raw text that won't match the stored
            -- normalized postings, leaving orphans. If individual mutation is ever
            -- needed, normalize inside these triggers first.
            CREATE TRIGGER IF NOT EXISTS media_ai AFTER INSERT ON media BEGIN
                INSERT INTO media_fts(rowid, artist, title, disc_id)
                VALUES (new.id, new.artist, new.title, new.disc_id);
            END;

            CREATE TRIGGER IF NOT EXISTS media_ad AFTER DELETE ON media BEGIN
                INSERT INTO media_fts(media_fts, rowid, artist, title, disc_id)
                VALUES ('delete', old.id, old.artist, old.title, old.disc_id);
            END;

            CREATE TRIGGER IF NOT EXISTS media_au AFTER UPDATE ON media BEGIN
                INSERT INTO media_fts(media_fts, rowid, artist, title, disc_id)
                VALUES ('delete', old.id, old.artist, old.title, old.disc_id);
                INSERT INTO media_fts(rowid, artist, title, disc_id)
                VALUES (new.id, new.artist, new.title, new.disc_id);
            END;
        """)

    def build_from_file_list(self, path, mount_replace=None, callback=None):
        """Parse a text file of paths and batch-insert into the catalog.

        Args:
            path: Path to text file with one file path per line.
            mount_replace: Tuple of (old_prefix, new_prefix) for path rewriting.
                          e.g. ('/Volumes/Nomad4TBOne/', '/mnt/Nomad4TBOne/')
            callback: Optional function called with (count,) after each batch.

        Returns:
            Number of entries inserted.
        """
        conn = self._get_conn()
        self.init_schema()

        # Drop ALL sync triggers during build. We populate media_fts manually
        # with NORMALIZED text, but the triggers feed RAW text. For an
        # external-content FTS5 table the 'delete' command requires the text it
        # is given to tokenize to the SAME terms that were indexed; raw vs.
        # normalized diverge (apostrophes, '&'->'and', case) and corrupt the
        # index ("database disk image is malformed"). So we manage the index
        # ourselves here and recreate the triggers afterwards.
        conn.execute("DROP TRIGGER IF EXISTS media_ai")
        conn.execute("DROP TRIGGER IF EXISTS media_ad")
        conn.execute("DROP TRIGGER IF EXISTS media_au")

        # Clear existing data. media_fts is an external-content table, so
        # "DELETE FROM media_fts" is a silent no-op — use the special
        # 'delete-all' command to actually empty the inverted index.
        conn.execute("DELETE FROM media")
        conn.execute("INSERT INTO media_fts(media_fts) VALUES('delete-all')")
        # media_trigram is a STANDALONE fts5 table, so a plain DELETE works.
        conn.execute("DELETE FROM media_trigram")

        batch = []
        batch_size = 5000
        total = 0

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                file_path = line
                if mount_replace:
                    old_prefix, new_prefix = mount_replace
                    if file_path.startswith(old_prefix):
                        file_path = new_prefix + file_path[len(old_prefix):]

                filename = os.path.basename(file_path)
                if not os.path.splitext(filename)[1]:
                    continue  # Skip directories and extensionless entries
                folder = os.path.dirname(file_path)
                fmt = _detect_format(filename)
                disc_id, artist, title = parse_karaoke_filename(filename)

                batch.append((file_path, filename, folder, disc_id, artist, title, fmt))

                if len(batch) >= batch_size:
                    self._flush_batch(conn, batch)
                    total += len(batch)
                    if callback:
                        callback(total)
                    batch = []

        if batch:
            self._flush_batch(conn, batch)
            total += len(batch)
            if callback:
                callback(total)

        # Recreate sync triggers for any future individual mutations.
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS media_ai AFTER INSERT ON media BEGIN
                INSERT INTO media_fts(rowid, artist, title, disc_id)
                VALUES (new.id, new.artist, new.title, new.disc_id);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS media_ad AFTER DELETE ON media BEGIN
                INSERT INTO media_fts(media_fts, rowid, artist, title, disc_id)
                VALUES ('delete', old.id, old.artist, old.title, old.disc_id);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS media_au AFTER UPDATE ON media BEGIN
                INSERT INTO media_fts(media_fts, rowid, artist, title, disc_id)
                VALUES ('delete', old.id, old.artist, old.title, old.disc_id);
                INSERT INTO media_fts(rowid, artist, title, disc_id)
                VALUES (new.id, new.artist, new.title, new.disc_id);
            END
        """)

        # Stamp the normalizer version the index was built with, so we can
        # later detect a stale index after a NORMALIZER_VERSION bump.
        conn.execute(
            "INSERT OR REPLACE INTO catalog_meta(key, value) VALUES('normalizer_version', ?)",
            (str(NORMALIZER_VERSION),),
        )
        conn.commit()

        return total

    def _flush_batch(self, conn, batch):
        """Insert batch into media table and normalized text into FTS index."""
        conn.executemany(
            "INSERT OR IGNORE INTO media (path, filename, folder, disc_id, artist, title, format) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            batch
        )
        # Get IDs and insert normalized text into FTS
        paths = [row[0] for row in batch]
        placeholders = ','.join('?' * len(paths))
        rows = conn.execute(
            f"SELECT id, artist, title, disc_id FROM media WHERE path IN ({placeholders})",
            paths
        ).fetchall()
        conn.executemany(
            "INSERT INTO media_fts(rowid, artist, title, disc_id) VALUES (?, ?, ?, ?)",
            [(r[0], _normalize_for_search(r[1] or ''), _normalize_for_search(r[2] or ''),
              _normalize_for_search(r[3] or '')) for r in rows]
        )
        # Populate the trigram candidate index over normalized "artist title"
        # so a fresh build is immediately fuzzy/substring-search ready.
        conn.executemany(
            "INSERT INTO media_trigram(rowid, norm_text) VALUES (?, ?)",
            [(r[0], _normalize_for_search(((r[1] or '') + ' ' + (r[2] or '')).strip()))
             for r in rows]
        )
        conn.commit()

    def search(self, query, limit=50, offset=0):
        """Full-text search using FTS5 MATCH with LIKE fallback.

        Returns list of dicts with path, filename, folder, disc_id, artist, title, format.
        FTS5 handles most queries fast; LIKE fallback catches punctuation
        mismatches (e.g. "Sheeps" vs "Sheep's").
        """
        normalized = _normalize_for_search(query)
        fts_query = _fts5_safe_query(normalized)
        if not fts_query:
            return []

        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT m.path, m.filename, m.folder, m.disc_id, m.artist, m.title, m.format "
                "FROM media_fts f "
                "JOIN media m ON f.rowid = m.id "
                "WHERE media_fts MATCH ? "
                "ORDER BY rank "
                "LIMIT ? OFFSET ?",
                (fts_query, limit, offset)
            ).fetchall()
            if rows:
                return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            pass

        # LIKE fallback: strip punctuation from query terms and match against
        # artist/title with punctuation stripped. Handles cases like
        # "Sheeps" matching "Sheep's" where FTS5 tokenization diverges.
        like_rows = self._like_fallback(normalized, limit, offset)
        if like_rows:
            return like_rows
        return self._fuzzy_search(query, limit, offset)

    def _like_fallback(self, query, limit, offset):
        """Fallback search using LIKE with punctuation stripped."""
        terms = _query_tokens(query)
        if not terms:
            return []
        # Build WHERE clause: each term must appear in artist||title with
        # punctuation removed (using SQLite REPLACE for common punctuation)
        strip_expr = "LOWER(REPLACE(REPLACE(REPLACE(COALESCE(artist,'') || ' ' || COALESCE(title,''), '''', ''), '-', ' '), '.', ''))"
        conditions = []
        params = []
        for term in terms:
            conditions.append(f"{strip_expr} LIKE ?")
            params.append(f"%{term.lower()}%")
        params.extend([limit, offset])
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT path, filename, folder, disc_id, artist, title, format "
                "FROM media "
                f"WHERE {' AND '.join(conditions)} "
                "LIMIT ? OFFSET ?",
                params,
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            return []

    def _fuzzy_search(self, query, limit, offset=0):
        """Fuzzy fallback using rapidfuzz WRatio over trigram candidates.

        Only called when both FTS5 MATCH and LIKE fallback return nothing.
        Uses the trigram index to retrieve candidates (typo-tolerant recall),
        then scores each with WRatio and filters by FUZZY_SCORE_CUTOFF.

        Precision gate: a candidate must share at least FUZZY_MIN_TOKEN_OVERLAP
        of the query's significant tokens (len>=4) with the candidate text.
        This prevents WRatio's partial_ratio component from producing false
        positives where no real words are shared (real-data: 190/254 fuzzy hits
        at WRatio>=80 had zero token overlap).

        Ranking: overlap first (real-word agreement), then WRatio score.
        """
        norm_q = _normalize_for_search(query)
        if len(norm_q) < 3:
            return []
        q_sig = {t for t in norm_q.split() if len(t) >= 4}
        candidates = self._trigram_candidates(
            query, limit=max(200, (limit + offset) * 20))
        scored = []
        for c in candidates:
            hay = _normalize_for_search(
                ((c.get("artist") or "") + " " + (c.get("title") or "")).strip()
            )
            score = fuzz.WRatio(norm_q, hay)
            if score < FUZZY_SCORE_CUTOFF:
                continue
            if q_sig:
                hay_tokens = set(hay.split())
                overlap = len(q_sig & hay_tokens) / len(q_sig)
                if overlap < FUZZY_MIN_TOKEN_OVERLAP:
                    continue
            else:
                # No significant tokens to gate on; require near-exact match.
                if score < FUZZY_SHORT_QUERY_CUTOFF:
                    continue
                overlap = 0.0
            scored.append((overlap, score, c))
        # Rank by overlap first (real-word agreement), then fuzzy score.
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return [c for _, _, c in scored[offset:offset + limit]]

    def normalizer_version(self):
        """Return the NORMALIZER_VERSION the index was built with (or None)."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT value FROM catalog_meta WHERE key='normalizer_version'"
            ).fetchone()
            return int(row[0]) if row else None
        except sqlite3.Error:
            return None

    def index_is_stale(self):
        """True when the FTS index was built with a different normalizer."""
        return self.normalizer_version() != NORMALIZER_VERSION

    def rebuild_fts(self, callback=None, batch_size=5000):
        """Rebuild media_fts + media_trigram from the media table using the
        current normalizer, then restamp NORMALIZER_VERSION.

        Returns the number of media rows reindexed.
        """
        conn = self._get_conn()
        # media_fts is external-content: "DELETE FROM media_fts" is a silent
        # no-op, so use the special 'delete-all' command to clear the inverted
        # index. media_trigram is standalone, so a plain DELETE works there.
        conn.execute("INSERT INTO media_fts(media_fts) VALUES('delete-all')")
        conn.execute("DELETE FROM media_trigram")
        conn.commit()
        rows = conn.execute(
            "SELECT id, artist, title, disc_id FROM media"
        ).fetchall()
        total = len(rows)
        for start in range(0, total, batch_size):
            chunk = rows[start:start + batch_size]
            conn.executemany(
                "INSERT INTO media_fts(rowid, artist, title, disc_id) VALUES (?,?,?,?)",
                [(r[0], _normalize_for_search(r[1] or ''),
                  _normalize_for_search(r[2] or ''),
                  _normalize_for_search(r[3] or '')) for r in chunk],
            )
            conn.executemany(
                "INSERT INTO media_trigram(rowid, norm_text) VALUES (?,?)",
                [(r[0], _normalize_for_search(((r[1] or '') + ' ' + (r[2] or '')).strip()))
                 for r in chunk],
            )
            conn.commit()
            if callback:
                callback(min(start + batch_size, total), total)
        conn.execute(
            "INSERT OR REPLACE INTO catalog_meta(key,value) VALUES('normalizer_version', ?)",
            (str(NORMALIZER_VERSION),),
        )
        conn.commit()
        return total

    def _trigram_candidates(self, query, limit=200):
        """Return candidate rows via the trigram index (substring/fuzzy-friendly).

        Builds the query's overlapping 3-grams and OR-matches them against the
        index. OR (rather than an exact phrase MATCH of the whole query) is what
        makes this typo-tolerant: a single transposition/insertion only breaks a
        couple of trigrams, but the rest still hit. This is a *candidate* set
        meant to be re-ranked downstream, so recall matters more than precision.
        """
        norm = _normalize_for_search(query)
        if len(norm) < 3:
            return []
        trigrams = {norm[i:i + 3] for i in range(len(norm) - 2)}
        # Quote each trigram so '"' / special chars can't break the MATCH syntax.
        match_expr = ' OR '.join('"' + t.replace('"', '""') + '"' for t in trigrams)
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT m.path, m.filename, m.folder, m.disc_id, m.artist, m.title, m.format "
                "FROM media_trigram t JOIN media m ON t.rowid = m.id "
                "WHERE t.norm_text MATCH ? LIMIT ?",
                (match_expr, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def count(self):
        """Return total number of entries in the catalog."""
        conn = self._get_conn()
        return conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]

    def stats(self):
        """Return catalog statistics: total count and breakdown by format."""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        rows = conn.execute(
            "SELECT format, COUNT(*) as cnt FROM media GROUP BY format ORDER BY cnt DESC"
        ).fetchall()
        by_format = {row['format']: row['cnt'] for row in rows}
        return {'total': total, 'by_format': by_format}

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
