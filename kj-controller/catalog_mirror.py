"""CatalogMirror: local SQLite mirror of the three remote song catalogs.

Mirrors KaraokeNerds community, KaraokeNerds full, and the Divebar Drive
index into ONE local FTS5-backed database so live-show searches never pay
BigQuery latency (~1.5s per Cloud Function call) and keep working when the
venue Wi-Fi dies. The sources are nightly GCS exports:

  * gs://nomadkaraoke-kn-data/community/community-data-latest.json.gz
    (gzipped JSON ``{"Items": [{Id, Artist, Title, Brand, Watch, ...}]}``)
  * gs://nomadkaraoke-kn-data/full/full-data-latest.json.gz
    (gzipped JSON ``{"Items": [{Id, Artist, Title, Brands}]}``)
  * gs://nomadkaraoke-divebar-files/exports/divebar-catalog-latest.json.gz
    (gzipped NDJSON, one Divebar catalog row per line — produced by the
    divebar-mirror Cloud Function after each index build)

``scripts/sync_catalogs.py`` downloads these and calls ``build_mirror_db``,
which builds a fresh database beside the live one and atomically replaces
it (never writes into a live SQLite file).

Search uses the SAME shared engine as the external catalog and the
media-index scan (text_normalize + FTS5 unicode61 remove_diacritics +
trigram candidates + fuzzy_match full-coverage gate), and the public search
methods return the SAME shapes as the remote Cloud Function actions they
replace (``divebar.kn_search`` / CF ``search``), so callers are drop-in.
"""

import gzip
import json
import logging
import os
import sqlite3
import time

import fuzzy_match
from text_normalize import (
    normalize as _normalize,
    fts_match_query as _fts5_safe_query,
    tokens as _query_tokens,
    NORMALIZER_VERSION,
)

logger = logging.getLogger(__name__)

SOURCE_KN_COMMUNITY = "kn_community"
SOURCE_KN_FULL = "kn_full"
SOURCE_DIVEBAR = "divebar"

# A mirror older than this falls back to the live Cloud Function path: stale
# results are worse than slow ones once the export pipeline has been broken
# for over a week (new tracks/mirrors would be invisible).
DEFAULT_MAX_AGE_DAYS = 8


def _default_db_path(config):
    return config.get("catalog_mirror_db") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "catalog_mirror.db")


class CatalogMirror:
    """Read-side handle on the local catalog-mirror database."""

    def __init__(self, config, db_path=None):
        self.config = config or {}
        self.db_path = db_path or _default_db_path(self.config)
        self._conn = None

    # ------------------------------------------------------------------ conn

    def _get_conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA query_only=ON")
            self._conn.execute("PRAGMA cache_size=-8192")
        return self._conn

    def reload(self):
        """Drop the connection so the next query reopens the (replaced) file."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def close(self):
        self.reload()

    # ------------------------------------------------------------- freshness

    def _meta(self, key):
        try:
            row = self._get_conn().execute(
                "SELECT value FROM mirror_meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    def built_at(self):
        """Unix timestamp the mirror was built, or None."""
        val = self._meta("built_at")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    def is_usable(self, max_age_days=None):
        """True when the mirror exists, is fresh, and was built with the
        current normalizer — the gate for serving searches locally instead of
        via the Cloud Function."""
        if not self.config.get("catalog_mirror_enabled", True):
            return False
        if not os.path.exists(self.db_path):
            return False
        built = self.built_at()
        if built is None:
            return False
        if max_age_days is None:
            max_age_days = self.config.get(
                "catalog_mirror_max_age_days", DEFAULT_MAX_AGE_DAYS)
        if (time.time() - built) > max_age_days * 86400:
            return False
        if self._meta("normalizer_version") != str(NORMALIZER_VERSION):
            return False
        return True

    def stats(self):
        """Freshness + per-source row counts for /system/stats and logs."""
        out = {
            "db_path": self.db_path,
            "exists": os.path.exists(self.db_path),
            "usable": False,
            "built_at": None,
            "age_hours": None,
            "sources": {},
        }
        if not out["exists"]:
            return out
        try:
            built = self.built_at()
            out["built_at"] = built
            if built:
                out["age_hours"] = round((time.time() - built) / 3600, 1)
            rows = self._get_conn().execute(
                "SELECT source, COUNT(*) AS cnt FROM entries GROUP BY source").fetchall()
            out["sources"] = {r["source"]: r["cnt"] for r in rows}
            out["usable"] = self.is_usable()
        except sqlite3.Error as e:
            out["error"] = str(e)
        return out

    # ---------------------------------------------------------------- search

    def kn_search(self, query, limit=50):
        """Local replacement for ``divebar.kn_search`` — same return shape:
        ``{"community": [{artist,title,brand,watch}],
           "full": [{artist,title,brands}]}`` with a per-source limit."""
        return {
            "community": self._search_source(SOURCE_KN_COMMUNITY, query, limit),
            "full": self._search_source(SOURCE_KN_FULL, query, limit),
        }

    def kn_community_search(self, query, limit=50):
        """Local replacement for ``divebar.kn_community_search`` (flat rows)."""
        return self._search_source(SOURCE_KN_COMMUNITY, query, limit)

    def divebar_search(self, query, limit=100):
        """Local replacement for the CF ``search`` action: FLAT catalog rows
        (callers group them with ``divebar.group_results``, exactly like the
        remote path)."""
        return self._search_source(SOURCE_DIVEBAR, query, limit)

    def _search_source(self, source, query, limit):
        """The shared search ladder, filtered to one source:
        FTS5 MATCH → normalized-LIKE fallback → trigram + fuzzy_match gate.
        Same ladder as ExternalCatalog.search / the media-index scan, over the
        same normalized text space."""
        normalized = _normalize(query or "")
        fts_query = _fts5_safe_query(normalized)
        if not fts_query:
            return []
        conn = self._get_conn()

        try:
            # Source filter INSIDE the MATCH (column filter) — see schema note.
            # `source` values tokenize to a phrase ("kn_full" -> "kn full").
            rows = conn.execute(
                "SELECT e.payload FROM entries_fts f "
                "JOIN entries e ON f.rowid = e.id "
                "WHERE entries_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (f'({fts_query}) AND source: "{source}"', limit),
            ).fetchall()
            if rows:
                return [json.loads(r["payload"]) for r in rows]
        except sqlite3.Error:
            return []

        like_rows = self._like_fallback(conn, source, normalized, limit)
        if like_rows:
            return like_rows
        return self._fuzzy_search(conn, source, query, limit)

    def _like_fallback(self, conn, source, normalized, limit):
        """Token-AND substring match over the pre-normalized text column.
        Catches tokenization divergence (e.g. partial-word queries)."""
        terms = _query_tokens(normalized)
        if not terms:
            return []
        conditions = ["e.norm_text LIKE ?" for _ in terms]
        params = [f"%{t}%" for t in terms] + [source, limit]
        try:
            rows = conn.execute(
                "SELECT e.payload FROM entries e "
                f"WHERE {' AND '.join(conditions)} AND e.source = ? LIMIT ?",
                params,
            ).fetchall()
            return [json.loads(r["payload"]) for r in rows]
        except sqlite3.Error:
            return []

    def _fuzzy_search(self, conn, source, query, limit):
        """Typo-tolerant fallback: trigram candidates re-ranked through the
        shared fuzzy_match full-coverage gate (identical semantics to
        ExternalCatalog._fuzzy_search)."""
        norm_q = _normalize(query or "")
        if len(norm_q) < 3:
            return []
        trigrams = {norm_q[i:i + 3] for i in range(len(norm_q) - 2)}
        match_expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in trigrams)
        try:
            # Column filter keeps the plan FTS-driven (see schema note); the
            # trigram tokenizer phrase-matches the source value consistently.
            candidates = conn.execute(
                "SELECT e.payload, e.norm_text FROM entries_trigram t "
                "JOIN entries e ON t.rowid = e.id "
                "WHERE entries_trigram MATCH ? "
                "ORDER BY rank LIMIT ?",
                (f'({match_expr}) AND source: "{source}"',
                 max(200, limit * 20)),
            ).fetchall()
        except sqlite3.Error:
            return []
        q_sig = fuzzy_match.significant_tokens(norm_q)
        scored = []
        for c in candidates:
            res = fuzzy_match.score(norm_q, c["norm_text"], q_sig=q_sig)
            if res is None:
                continue
            overlap, wratio = res
            scored.append((overlap, wratio, c["payload"]))
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return [json.loads(p) for _, _, p in scored[:limit]]


# ============================================================== build side

_SCHEMA = """
    CREATE TABLE entries (
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,
        artist TEXT,
        title TEXT,
        payload TEXT NOT NULL,
        norm_text TEXT NOT NULL
    );
    CREATE INDEX entries_source ON entries(source);
    -- `source` is an FTS column (not just an entries column) so the
    -- per-source filter happens INSIDE the MATCH expression. Filtering via
    -- "JOIN entries ... WHERE e.source=?" instead lets SQLite drive the plan
    -- from the 360k-row entries table and probe FTS per row — measured 32s
    -- for a query whose bare MATCH takes 3ms.
    CREATE VIRTUAL TABLE entries_fts USING fts5(
        norm_text, source,
        content='entries', content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    CREATE VIRTUAL TABLE entries_trigram USING fts5(
        norm_text, source, tokenize='trigram'
    );
    CREATE TABLE mirror_meta (
        key TEXT PRIMARY KEY,
        value TEXT
    );
"""


def _iter_kn_export(path):
    """Yield rows from a KN export.

    The community export is gzipped JSON ``{"Items": [...]}``; the full
    export is a gzipped bare JSON list (verified against the live bucket
    2026-09-22 — the data-access doc's claim that both use Items is wrong
    for full). Accept either shape.
    """
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("Items") or []
    for item in data:
        yield item


def _iter_ndjson_export(path):
    """Yield rows from a gzipped NDJSON export (divebar catalog)."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _entry_rows(source, rows_iter):
    """Map raw export rows to (source, artist, title, payload, norm_text).

    Payloads use the exact key sets the remote APIs return, so search results
    are drop-in for the CF-backed paths.
    """
    for raw in rows_iter:
        if source == SOURCE_KN_COMMUNITY:
            artist = (raw.get("Artist") or "").strip()
            title = (raw.get("Title") or "").strip()
            payload = {"artist": artist, "title": title,
                       "brand": raw.get("Brand"), "watch": raw.get("Watch")}
        elif source == SOURCE_KN_FULL:
            artist = (raw.get("Artist") or "").strip()
            title = (raw.get("Title") or "").strip()
            payload = {"artist": artist, "title": title,
                       "brands": raw.get("Brands")}
        else:  # divebar — export rows already use the CF search column set
            artist = (raw.get("artist") or "").strip()
            title = (raw.get("title") or "").strip()
            payload = raw
        if not artist and not title:
            continue
        norm_text = _normalize((artist + " " + title).strip())
        if not norm_text:
            continue
        yield (source, artist, title, json.dumps(payload), norm_text)


def build_mirror_db(dest_path, community_path, full_path, divebar_path,
                    source_hashes=None):
    """Build a fresh mirror DB beside ``dest_path`` and atomically replace it.

    Never writes into the live file: the build happens in ``<dest>.new`` and
    lands via ``os.replace`` (readers keep the old file's inode until they
    reload). Returns per-source row counts.
    """
    tmp_path = dest_path + ".new"
    for stale in (tmp_path, tmp_path + "-wal", tmp_path + "-shm"):
        if os.path.exists(stale):
            os.unlink(stale)

    conn = sqlite3.connect(tmp_path)
    try:
        conn.executescript(_SCHEMA)
        counts = {}
        for source, path, reader in (
            (SOURCE_KN_COMMUNITY, community_path, _iter_kn_export),
            (SOURCE_KN_FULL, full_path, _iter_kn_export),
            (SOURCE_DIVEBAR, divebar_path, _iter_ndjson_export),
        ):
            batch = []
            count = 0
            for row in _entry_rows(source, reader(path)):
                batch.append(row)
                if len(batch) >= 5000:
                    _flush(conn, batch)
                    count += len(batch)
                    batch = []
            if batch:
                _flush(conn, batch)
                count += len(batch)
            counts[source] = count

        meta = {
            "built_at": str(time.time()),
            "normalizer_version": str(NORMALIZER_VERSION),
            "counts": json.dumps(counts),
        }
        if source_hashes:
            meta["source_hashes"] = json.dumps(source_hashes)
        conn.executemany(
            "INSERT OR REPLACE INTO mirror_meta(key, value) VALUES (?, ?)",
            list(meta.items()))
        conn.commit()
    except BaseException:
        conn.close()
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    conn.close()
    os.replace(tmp_path, dest_path)
    logger.info("Catalog mirror built at %s: %s", dest_path, counts)
    return counts


def _flush(conn, batch):
    cur = conn.executemany(
        "INSERT INTO entries(source, artist, title, payload, norm_text) "
        "VALUES (?, ?, ?, ?, ?)", batch)
    # Backfill both indexes for the rows just inserted (single transaction,
    # ids are contiguous because nothing else writes to this fresh file).
    first_id = conn.execute("SELECT MAX(id) FROM entries").fetchone()[0] - len(batch) + 1
    rows = conn.execute(
        "SELECT id, norm_text, source FROM entries WHERE id >= ?",
        (first_id,)).fetchall()
    conn.executemany(
        "INSERT INTO entries_fts(rowid, norm_text, source) VALUES (?, ?, ?)", rows)
    conn.executemany(
        "INSERT INTO entries_trigram(rowid, norm_text, source) VALUES (?, ?, ?)",
        rows)
    del cur


def stored_source_hashes(db_path):
    """Source-file hashes recorded at build time (for the sync's skip check)."""
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT value FROM mirror_meta WHERE key='source_hashes'").fetchone()
            return json.loads(row[0]) if row else None
        finally:
            conn.close()
    except (sqlite3.Error, ValueError):
        return None
