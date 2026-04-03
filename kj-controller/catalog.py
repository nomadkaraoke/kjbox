"""ExternalCatalog: SQLite FTS5-backed searchable catalog for external media."""

import os
import re
import sqlite3
import unicodedata


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


def _fts5_safe_query(query):
    """Sanitize user input into a safe FTS5 query.

    Strips special chars, quotes each term, prefix-matches last term.
    "bon jovi livin" -> "bon" "jovi" "livin"*
    """
    # Remove FTS5 special characters
    cleaned = re.sub(r'[^\w\s]', ' ', query, flags=re.UNICODE)
    terms = cleaned.split()
    if not terms:
        return ''
    # Quote each term, prefix-match the last one
    quoted = [f'"{t}"' for t in terms[:-1]]
    quoted.append(f'"{terms[-1]}"*')
    return ' '.join(quoted)


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


LATIN_SPECIAL_MAP = {
    'ø': 'o', 'Ø': 'O', 'æ': 'ae', 'Æ': 'AE', 'ß': 'ss',
    'ð': 'd', 'Ð': 'D', 'ł': 'l', 'Ł': 'L', 'ı': 'i',
    'đ': 'd', 'Đ': 'D', 'þ': 'th', 'Þ': 'Th',
}
LATIN_SPECIAL_MAP_RE = re.compile('[' + re.escape(''.join(LATIN_SPECIAL_MAP)) + ']')


def _normalize_for_search(text):
    """Normalize text for search: strip diacritics and map special Latin chars.

    Handles two categories:
    1. NFD-decomposable diacritics (é→e, ï→i, ñ→n, ç→c, etc.)
    2. Non-decomposable Latin chars (ø→o, æ→ae, ß→ss, ð→d, ł→l, ı→i, etc.)
    """
    if not text:
        return text
    s = unicodedata.normalize('NFD', text)
    s = re.sub(r'[\u0300-\u036f]', '', s)
    s = LATIN_SPECIAL_MAP_RE.sub(lambda m: LATIN_SPECIAL_MAP[m.group()], s)
    return s


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

        # Drop INSERT trigger — we populate FTS manually with normalized text
        conn.execute("DROP TRIGGER IF EXISTS media_ai")

        # Clear existing data
        conn.execute("DELETE FROM media")
        conn.execute("DELETE FROM media_fts")

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

        # Recreate INSERT trigger for any future individual inserts
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS media_ai AFTER INSERT ON media BEGIN
                INSERT INTO media_fts(rowid, artist, title, disc_id)
                VALUES (new.id, new.artist, new.title, new.disc_id);
            END
        """)

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
        return self._like_fallback(normalized, limit, offset)

    def _like_fallback(self, query, limit, offset):
        """Fallback search using LIKE with punctuation stripped."""
        terms = re.sub(r'[^\w\s]', ' ', query, flags=re.UNICODE).split()
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
