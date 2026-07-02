# kj-controller/tests/unit/test_import_rotation_ssd_tracks.py
import os
import sqlite3

from catalog import ExternalCatalog, parse_karaoke_filename
from media_library import MediaLibraryStore
from scripts.import_rotation_ssd_tracks import rotation_ssd_paths, run


def _rotation_db(tmp_path, active_paths, archive_paths):
    db = str(tmp_path / "rotation.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE rotation_entries (id INTEGER PRIMARY KEY, file_path TEXT)")
    conn.execute("CREATE TABLE rotation_archive (id INTEGER PRIMARY KEY, file_path TEXT)")
    conn.executemany("INSERT INTO rotation_entries (file_path) VALUES (?)",
                     [(p,) for p in active_paths])
    conn.executemany("INSERT INTO rotation_archive (file_path) VALUES (?)",
                     [(p,) for p in archive_paths])
    conn.commit()
    conn.close()
    return db


def _catalog_db(tmp_path, paths):
    db = str(tmp_path / "cat.db")
    cat = ExternalCatalog({"external_catalog_db": db})
    cat.init_schema()
    conn = cat._get_conn()
    for p in paths:
        fname = os.path.basename(p)
        disc, artist, title = parse_karaoke_filename(fname)
        conn.execute(
            "INSERT INTO media (path, filename, folder, disc_id, artist, title, format) "
            "VALUES (?,?,?,?,?,?,?)",
            (p, fname, os.path.dirname(p), disc, artist, title, "zip"))
    conn.commit()
    return db


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(path.encode())  # unique content per file -> unique lib- ids


def test_rotation_ssd_paths_unions_and_filters(tmp_path):
    mount = str(tmp_path / "ssd")
    a = f"{mount}/Discs/A.zip"
    b = f"{mount}/Discs/B.zip"
    db = _rotation_db(tmp_path, [a, "/opt/nomad/downloads/x.mp4", None],
                      [b, a])  # duplicate across tables + non-SSD + NULL
    assert rotation_ssd_paths(db, mount) == sorted([a, b])


def test_dry_run_reports_without_writing_or_hashing(tmp_path):
    mount = str(tmp_path / "ssd")
    a = f"{mount}/Discs/SC1 - ABBA - SOS.zip"
    gone = f"{mount}/Discs/GONE.zip"
    _touch(a)
    rot = _rotation_db(tmp_path, [a], [gone])
    cat = _catalog_db(tmp_path, [a])
    media_db = str(tmp_path / "ml.db")
    counts, missing, _ = run(rot, media_db, cat, mount, execute=False)
    assert counts["imported"] == 1 and counts["missing"] == 1
    assert missing == [gone]
    assert MediaLibraryStore(media_db).list_records(source="library") == []


def test_execute_imports_idempotently(tmp_path):
    mount = str(tmp_path / "ssd")
    a = f"{mount}/Discs/SC1 - ABBA - SOS.zip"        # in catalog
    b = f"{mount}/Loose/weird~name.zip"               # NOT in catalog
    _touch(a)
    _touch(b)
    rot = _rotation_db(tmp_path, [a], [b])
    cat = _catalog_db(tmp_path, [a])
    media_db = str(tmp_path / "ml.db")

    counts, _, catalog_misses = run(rot, media_db, cat, mount, execute=True)
    assert counts["imported"] == 2 and counts["already"] == 0
    assert catalog_misses == [b]
    ml = MediaLibraryStore(media_db)
    row_a = ml.get_by_path(a)
    assert row_a["media_id"].startswith("lib-") and row_a["artist"] == "ABBA"
    assert row_a["needs_review"] == 0 and row_a["parse_method"] == "catalog"
    row_b = ml.get_by_path(b)
    assert row_b["needs_review"] == 1 and row_b["parse_method"] == "deterministic"

    counts2, _, _ = run(rot, media_db, cat, mount, execute=True)
    assert counts2["already"] == 2 and counts2["imported"] == 0

    # The unchanged play-stats backfill can now resolve these paths.
    from scripts.backfill_play_stats import _resolve_media_id
    mid, artist, _title = _resolve_media_id(ml, a)
    assert mid == row_a["media_id"] and artist == "ABBA"
