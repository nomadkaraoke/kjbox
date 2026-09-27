#!/usr/bin/env python3
"""Build the on-device song-identification index (``song_id.db``).

Input: the TSV(.gz) export of "every song people might mean" — columns
``artist, title, popularity, karaoke`` (popularity blank for karaoke-only rows).
See docs/SONG-IDENTIFICATION.md §3 for where it comes from.

Output SQLite:
  songs(rowid, artist, title, na, nt, pop, karaoke)      display + normalized text
  songs_fts  — FTS5 word index over (na, nt)             candidate retrieval
  artists(na, artist, pop, songs) + artists_fts          artist-first path (word index over names)
  vocab(word, freq) + vocab_tri — FTS5 trigram index     typo-tolerant word lookup

Duplicates are merged on the space-less normalized artist+title ("u s a" == "usa"),
keeping the most popular display spelling and OR-ing the karaoke flag.

Usage: python scripts/build_song_id_db.py songs.tsv.gz song_id.db
"""
import csv
import gzip
import os
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from song_identify import song_norm  # noqa: E402

SCHEMA_VERSION = 1


def _open(path):
    return gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")


def build(src, dst):
    merged = {}
    with _open(src) as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) < 4:
                continue
            artist, title, pop, karaoke = row[0].strip(), row[1].strip(), row[2], row[3]
            na, nt = song_norm(artist), song_norm(title)
            if not na or not nt:
                continue
            p = int(pop) if pop.strip() else None
            k = 1 if karaoke.strip() in ("1", "true", "True") else 0
            key = (na.replace(" ", ""), nt.replace(" ", ""))
            cur = merged.get(key)
            if cur is None:
                merged[key] = [artist, title, p, k]
            else:
                if p is not None and (cur[2] is None or p > cur[2]):
                    cur[0], cur[1], cur[2] = artist, title, p
                cur[3] = cur[3] or k

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
        CREATE TABLE vocab(word TEXT PRIMARY KEY, freq INTEGER) WITHOUT ROWID;
        CREATE VIRTUAL TABLE vocab_tri USING fts5(word, tokenize='trigram');
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
    db.executemany("INSERT INTO vocab VALUES (?,?)", vocab.items())
    db.executemany("INSERT INTO vocab_tri(word) VALUES (?)", [(w,) for w in vocab if len(w) >= 3])
    db.execute("INSERT INTO songs_fts(songs_fts) VALUES ('optimize')")
    db.execute("INSERT INTO vocab_tri(vocab_tri) VALUES ('optimize')")
    db.executemany("INSERT INTO meta VALUES (?,?)", [
        ("schema_version", str(SCHEMA_VERSION)), ("songs", str(len(rows))), ("words", str(len(vocab)))])
    db.commit()
    db.execute("VACUUM")
    db.close()
    os.replace(tmp, dst)
    return len(rows), len(vocab)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    n, w = build(sys.argv[1], sys.argv[2])
    print(f"{n} songs, {w} words → {sys.argv[2]} ({os.path.getsize(sys.argv[2]) / 1e6:.0f} MB)")
