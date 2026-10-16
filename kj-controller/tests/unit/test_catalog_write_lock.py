"""Whole-index catalog writers wait for each other instead of failing.

2026-10-16: the deploy hook's POST /catalog/build hit "database is locked"
because start_app's background reindex held the write transaction.
"""
import threading
import time

from catalog import ExternalCatalog

PATHS = [f"/m/NOMAD-{i:04d} - Artist {i} - (Over)Thinking {i}.mp4" for i in range(3000)]


def _catalog(tmp_path):
    lst = tmp_path / "l.txt"
    lst.write_text("\n".join(PATHS) + "\n")
    cat = ExternalCatalog({}, db_path=str(tmp_path / "c.db"))
    cat.init_schema()
    cat.build_from_file_list(str(lst))
    return cat, str(lst)


def test_concurrent_build_and_reindex_both_succeed(tmp_path, monkeypatch):
    cat, lst = _catalog(tmp_path)
    # On the device the reindex holds its transaction ~40s, far past sqlite's
    # default 5s busy wait. Shrink the busy wait so this test reproduces that
    # without taking 40s: without the lock, the build fails "database is locked".
    import sqlite_conn
    real_connect = sqlite_conn.sqlite3.connect
    monkeypatch.setattr(sqlite_conn.sqlite3, "connect",
                        lambda *a, **k: real_connect(*a, **{**k, "timeout": 0.05}))
    cat.close()
    errors, results = [], {}
    reindex_started = threading.Event()

    def slow_progress(done, total):
        if not reindex_started.is_set():
            reindex_started.set()
            time.sleep(0.5)  # build arrives while the reindex transaction is open

    def run(name, fn):
        try:
            results[name] = fn()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    reindex = threading.Thread(target=run, args=(
        "reindex", lambda: cat.rebuild_fts(callback=slow_progress, batch_size=200)))
    build = threading.Thread(target=run, args=(
        "build", lambda: cat.build_from_file_list(lst)))
    reindex.start()
    assert reindex_started.wait(timeout=30)
    build.start()
    for t in (reindex, build):
        t.join(timeout=60)
    assert errors == []
    assert results == {"reindex": len(PATHS), "build": len(PATHS)}
    assert cat.index_is_stale() is False
    assert len(cat.search("overthinking 42", limit=5)) >= 1


def test_only_if_stale_skips_current_index(tmp_path):
    cat, _ = _catalog(tmp_path)
    assert cat.rebuild_fts(only_if_stale=True) is None


def test_only_if_stale_rebuilds_stale_index(tmp_path):
    cat, _ = _catalog(tmp_path)
    conn = cat._get_conn()
    conn.execute("UPDATE catalog_meta SET value='3' WHERE key='normalizer_version'")
    conn.commit()
    assert cat.rebuild_fts(only_if_stale=True) == len(PATHS)
    assert cat.index_is_stale() is False
