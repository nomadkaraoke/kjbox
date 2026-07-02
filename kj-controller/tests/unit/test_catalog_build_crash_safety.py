# kj-controller/tests/unit/test_catalog_build_crash_safety.py
"""A catalog (re)build must be all-or-nothing.

The live catalog sat at exactly 240,000/398,446 rows for months because the
in-app POST /catalog/build was killed mid-ingest by an auto-deploy service
restart, and per-batch commits made the truncation durable AND invisible
(count was a clean batch multiple). The build must run as one transaction:
an interrupted/failed build leaves the PREVIOUS catalog fully intact, and a
concurrent reader mid-build still sees the old data (WAL snapshot).
"""
import sqlite3

import pytest

from catalog import ExternalCatalog


def _write_list(tmp_path, names, fname="list.txt"):
    p = tmp_path / fname
    p.write_text("".join(f"/media/x/Discs/{n}\n" for n in names), encoding="utf-8")
    return str(p)


def _cat(tmp_path):
    return ExternalCatalog({"external_catalog_db": str(tmp_path / "cat.db")})


OLD_NAMES = [f"OLD{i:02d} - Old Artist - Old Song {i}.zip" for i in range(3)]


def test_interrupted_build_preserves_previous_catalog(tmp_path, monkeypatch):
    cat = _cat(tmp_path)
    cat.build_from_file_list(_write_list(tmp_path, OLD_NAMES, "old.txt"))
    assert cat.count() == 3

    def boom(conn, batch):
        raise sqlite3.OperationalError("simulated death mid-batch")

    monkeypatch.setattr(cat, "_flush_batch", boom)
    with pytest.raises(sqlite3.OperationalError):
        cat.build_from_file_list(
            _write_list(tmp_path, ["NEW01 - New Artist - New Song.zip"], "new.txt"))

    # Previous catalog fully intact — rows, FTS index, and triggers — and
    # nothing from the failed build persisted. (Plain search("New Artist")
    # would fuzzy-match the old rows via the shared "artist" token, so check
    # the filenames instead.)
    assert cat.count() == 3
    assert len(cat.search("Old Artist Old Song")) == 3
    assert not any("NEW01" in r["filename"] for r in cat.search("New Artist"))

    # The sync triggers were DROPped inside the failed build's transaction —
    # the rollback must restore them, so a direct INSERT still reaches the
    # FTS index via media_ai.
    direct = sqlite3.connect(str(tmp_path / "cat.db"))
    direct.execute(
        "INSERT INTO media (path, filename, folder, disc_id, artist, title, format) "
        "VALUES ('/media/x/Discs/T.zip', 'T.zip', '/media/x/Discs', 'T01', "
        "'Triggercheck', 'Stillwired', 'zip')")
    direct.commit()
    direct.close()
    assert len(cat.search("Triggercheck Stillwired")) == 1


def test_failed_build_leaves_connection_usable_for_next_build(tmp_path, monkeypatch):
    cat = _cat(tmp_path)
    cat.build_from_file_list(_write_list(tmp_path, OLD_NAMES, "old.txt"))

    real = ExternalCatalog._flush_batch
    calls = {"n": 0}

    def flaky(conn, batch):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("simulated death")
        return real(cat, conn, batch)

    monkeypatch.setattr(cat, "_flush_batch", flaky)
    with pytest.raises(sqlite3.OperationalError):
        cat.build_from_file_list(
            _write_list(tmp_path, ["NEW01 - New Artist - New Song.zip"], "new.txt"))

    # A retry on the same instance must succeed (no dangling transaction).
    total = cat.build_from_file_list(
        _write_list(tmp_path, ["NEW01 - New Artist - New Song.zip"], "new.txt"))
    assert total == 1
    assert cat.count() == 1
    assert len(cat.search("New Artist")) == 1


def test_reader_mid_build_sees_old_catalog(tmp_path):
    """WAL snapshot isolation: while the (uncommitted) rebuild is in flight, a
    separate connection — e.g. a live search request — sees the old rows."""
    cat = _cat(tmp_path)
    cat.build_from_file_list(_write_list(tmp_path, OLD_NAMES, "old.txt"))

    big = [f"B{i:05d} - Bulk Artist - Bulk Song {i}.zip" for i in range(5001)]
    seen = {}

    def peek(total):
        if total == 5000 and "mid" not in seen:
            other = sqlite3.connect(str(tmp_path / "cat.db"))
            seen["mid"] = other.execute("SELECT COUNT(*) FROM media").fetchone()[0]
            other.close()

    total = cat.build_from_file_list(
        _write_list(tmp_path, big, "big.txt"), callback=peek)
    assert total == 5001
    assert seen["mid"] == 3          # mid-build reader saw the OLD catalog
    assert cat.count() == 5001       # committed result replaced it atomically


def test_interrupted_rebuild_fts_preserves_search(tmp_path):
    cat = _cat(tmp_path)
    cat.build_from_file_list(_write_list(tmp_path, OLD_NAMES, "old.txt"))

    def boom(done, total):
        raise RuntimeError("simulated death mid-reindex")

    with pytest.raises(RuntimeError):
        cat.rebuild_fts(callback=boom, batch_size=1)

    # FTS index intact — search still works on the previous state.
    assert len(cat.search("Old Artist Old Song")) == 3
    # And a clean retry completes.
    assert cat.rebuild_fts() == 3
    assert len(cat.search("Old Artist Old Song")) == 3
