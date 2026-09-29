#!/usr/bin/env python3
"""Build the on-device song-identification index (``song_id.db``).

Input: the TSV(.gz) export of "every song people might mean" — columns
``artist, title, popularity, karaoke`` (popularity blank for karaoke-only rows).
See docs/SONG-IDENTIFICATION.md §3 for where it comes from.

Output SQLite:
  songs(rowid, artist, title, na, nt, pop, karaoke)      display + normalized text
  songs_fts  — FTS5 word index over (na, nt)             candidate retrieval
  artists(na, artist, pop, songs) + artists_fts          artist-first path (word index over names)
  vocab(word, freq, first, len)                          typo lookup: same first letter, similar length

Duplicates are merged on the space-less normalized artist+title ("u s a" == "usa"),
keeping the most popular display spelling and OR-ing the karaoke flag. Then spelling
variants of the same artist's title are folded together ("Breaking Dishes" ==
"Breakin' Dishes", "Get Your Freak On" == "Get Ur Freak On"), showing the spelling
that has a karaoke version (the name singers will find), else the more popular one.

Usage: python scripts/build_song_id_db.py songs.tsv.gz [more.tsv.gz ...] song_id.db
"""
import csv
import gzip
import os
import re
import sqlite3
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from song_identify import song_norm  # noqa: E402
from text_normalize import NORMALIZER_VERSION  # noqa: E402

SCHEMA_VERSION = 3


_JUNK_CREDIT_RE = re.compile(r"\b(?:cover|covers|covered|tribute|karaoke|in the style of|made famous)\b", re.IGNORECASE)


# Title spelling folds: dropped g ("walkin" = "walking") and text-speak ("ur", "u").
_WORD_FOLDS = {"ur": "your", "u": "you"}


def _fold_title(nt):
    """Space-less title with spelling variants folded (merge key only, never displayed)."""
    return "".join(_WORD_FOLDS.get(w, w[:-1] if len(w) >= 5 and w.endswith("ing") else w)
                   for w in nt.split())


def _open(path):
    return gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")


def _rows(srcs):
    for src in srcs:
        with _open(src) as f:
            yield from csv.reader(f, delimiter="\t")


def build(srcs, dst, meta=None):
    """Build ``dst`` from one or more TSV(.gz) shards; atomic replace."""
    if isinstance(srcs, str):
        srcs = [srcs]
    merged = {}
    folds = {}      # exact key → spelling-folded key, only where they differ
    for row in _rows(srcs):
        if len(row) < 4:
            continue
        artist, title, pop, karaoke = row[0].strip(), row[1].strip(), row[2], row[3]
        na, nt = song_norm(artist), song_norm(title)
        if not na or not nt:
            continue
        p = int(pop) if pop.strip() else None
        k = 1 if karaoke.strip() in ("1", "true", "True") else 0
        if not k and _JUNK_CREDIT_RE.search(artist):
            continue    # MusicBrainz cover/tribute uploads ("George Benson (Hscc Cover Ft …)")
        if na.replace(" ", "") == nt.replace(" ", "") and not k and (p or 0) < 40:
            continue    # MusicBrainz "self-titled" junk ("Future — Future"); real ones have karaoke/popularity
        key = (na.replace(" ", ""), nt.replace(" ", ""))
        cur = merged.get(key)
        if cur is None:
            merged[key] = [artist, title, p, k]
            folded = _fold_title(nt)
            if folded != key[1]:
                folds[key] = (key[0], folded)
        else:
            if p is not None and (cur[2] is None or p > cur[2]):
                cur[0], cur[1], cur[2] = artist, title, p
            cur[3] = cur[3] or k
    # Fold spelling variants into one song. Folding is idempotent, so a folded key is
    # either an unfolded song's own key or a new group started by the first variant.
    for key, fkey in folds.items():
        cur = merged.pop(key)
        target = merged.get(fkey)
        if target is None:
            merged[fkey] = cur
        else:
            # Between spellings: the one with a karaoke version, then the more popular
            # (both "Get Your Freak On" and "Get Ur Freak On" are on KaraokeNerds).
            if (cur[3], cur[2] or -1) > (target[3], target[2] or -1):
                target[0], target[1] = cur[0], cur[1]
            if cur[2] is not None and (target[2] is None or cur[2] > target[2]):
                target[2] = cur[2]
            target[3] = target[3] or cur[3]
    del folds

    tmp = dst + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE songs(rowid INTEGER PRIMARY KEY, artist TEXT, title TEXT, na TEXT, nt TEXT,
                           pop INTEGER, karaoke INTEGER);
        CREATE VIRTUAL TABLE songs_fts USING fts5(na, nt, content='songs', content_rowid='rowid',
                                                  tokenize='unicode61');
        CREATE TABLE artists(rowid INTEGER PRIMARY KEY, na TEXT UNIQUE, artist TEXT, pop INTEGER, songs INTEGER);
        CREATE VIRTUAL TABLE artists_fts USING fts5(na, content='artists', content_rowid='rowid', tokenize='unicode61');
        CREATE TABLE vocab(word TEXT PRIMARY KEY, freq INTEGER, first TEXT, len INTEGER) WITHOUT ROWID;
    """)
    vocab = Counter()
    rows = []
    artists = {}
    for artist, title, p, k in merged.values():
        na, nt = song_norm(artist), song_norm(title)
        rows.append((artist, title, na, nt, p, k))
        vocab.update(set(na.split()) | set(nt.split()))
        a = artists.setdefault(na, [artist, -1, 0])
        a[2] += 1
        if (p if p is not None else 30) > a[1]:
            a[0], a[1] = artist, (p if p is not None else 30)
    db.executemany("INSERT INTO songs(artist, title, na, nt, pop, karaoke) VALUES (?,?,?,?,?,?)", rows)
    db.execute("CREATE INDEX songs_na ON songs(na)")
    db.execute("INSERT INTO songs_fts(songs_fts) VALUES ('rebuild')")
    db.executemany("INSERT INTO artists(na, artist, pop, songs) VALUES (?,?,?,?)",
                   [(na, a, p, n) for na, (a, p, n) in artists.items()])
    db.execute("INSERT INTO artists_fts(artists_fts) VALUES ('rebuild')")
    db.executemany("INSERT INTO vocab VALUES (?,?,?,?)", [(w, f, w[0], len(w)) for w, f in vocab.items()])
    db.execute("CREATE INDEX vocab_first_len ON vocab(first, len)")
    db.execute("INSERT INTO songs_fts(songs_fts) VALUES ('optimize')")
    db.executemany("INSERT INTO meta VALUES (?,?)", [
        ("schema_version", str(SCHEMA_VERSION)), ("songs", str(len(rows))), ("words", str(len(vocab))),
        ("normalizer_version", str(NORMALIZER_VERSION)), ("built_at", str(int(time.time())))]
        + [(k, str(v)) for k, v in (meta or {}).items()])
    db.commit()
    db.execute("VACUUM")
    db.close()
    os.replace(tmp, dst)
    return len(rows), len(vocab)


def stored_meta(db_path):
    """meta table of an existing index ({} if missing/unreadable)."""
    if not os.path.exists(db_path):
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return dict(conn.execute("SELECT key, value FROM meta").fetchall())
        finally:
            conn.close()
    except sqlite3.Error:
        return {}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    n, w = build(sys.argv[1:-1], sys.argv[-1])
    print(f"{n} songs, {w} words → {sys.argv[-1]} ({os.path.getsize(sys.argv[-1]) / 1e6:.0f} MB)")
