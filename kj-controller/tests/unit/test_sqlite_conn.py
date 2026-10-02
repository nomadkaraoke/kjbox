"""ThreadLocalConnection: one SQLite connection per thread (shared ones raced live)."""

import sqlite3
import threading

from catalog import ExternalCatalog
from catalog_mirror import CatalogMirror
from sqlite_conn import ThreadLocalConnection


def _db(tmp_path):
    path = str(tmp_path / "t.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE media (id INTEGER PRIMARY KEY)")
    conn.executemany("INSERT INTO media VALUES (?)", [(i,) for i in range(50)])
    conn.commit()
    conn.close()
    return path


def _in_thread(fn):
    out = []
    t = threading.Thread(target=lambda: out.append(fn()))
    t.start()
    t.join()
    return out[0]


def test_each_thread_gets_its_own_connection(tmp_path):
    conns = ThreadLocalConnection(_db(tmp_path))
    main = conns.get()
    assert conns.get() is main
    assert _in_thread(conns.get) is not main


def test_pragmas_and_row_factory_applied(tmp_path):
    conns = ThreadLocalConnection(_db(tmp_path), pragmas=("query_only=ON",))
    conn = conns.get()
    assert conn.row_factory is sqlite3.Row
    assert conn.execute("PRAGMA query_only").fetchone()[0] == 1


def test_reset_closes_own_and_others_reopen_lazily(tmp_path):
    conns = ThreadLocalConnection(_db(tmp_path))
    main = conns.get()
    other = _in_thread(conns.get)
    conns.reset()
    assert conns.current() is None
    try:
        main.execute("SELECT 1")
        raise AssertionError("caller's connection should be closed")
    except sqlite3.ProgrammingError:
        pass
    # Another thread's connection is left for that thread to close (it may be
    # mid-query), so it still works until that thread's next get().
    assert other.execute("SELECT 1").fetchone()[0] == 1
    fresh = conns.get()
    assert fresh is not main
    assert fresh.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 50


def test_get_after_reset_closes_stale_thread_connection(tmp_path):
    conns = ThreadLocalConnection(_db(tmp_path))
    holder = {}
    ready, go, done = threading.Event(), threading.Event(), threading.Event()

    def worker():
        holder["old"] = conns.get()
        ready.set()
        go.wait(5)
        holder["new"] = conns.get()
        done.set()

    t = threading.Thread(target=worker)
    t.start()
    assert ready.wait(5)
    conns.reset()
    go.set()
    assert done.wait(5)
    t.join()
    assert holder["new"] is not holder["old"]
    try:
        holder["old"].execute("SELECT 1")
        raise AssertionError("stale connection should be closed by its thread")
    except sqlite3.ProgrammingError:
        pass


def test_reset_during_open_does_not_register_stale_connection(tmp_path, monkeypatch):
    conns = ThreadLocalConnection(_db(tmp_path))
    real_connect = sqlite3.connect
    calls = []

    def racing_connect(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            conns.reset()  # file replaced while this thread was opening
        return real_connect(*a, **kw)

    monkeypatch.setattr(sqlite3, "connect", racing_connect)
    conn = conns.get()
    assert len(calls) == 2
    assert conns.current() is conn


def test_catalog_is_available_under_concurrency(mock_config, tmp_path):
    # Regression: concurrent /sing/search requests made the shared connection's
    # COUNT(*) return None -> TypeError in is_available().
    catalog = ExternalCatalog(mock_config, db_path=_db(tmp_path))
    errors = []

    def hammer():
        try:
            for _ in range(200):
                assert catalog.is_available() is True
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    catalog.close()
    assert catalog._conn is None


def test_mirror_reload_drops_connection(tmp_path):
    mirror = CatalogMirror({}, db_path=_db(tmp_path))
    first = mirror._get_conn()
    assert mirror._conn is first
    mirror.reload()
    assert mirror._conn is None
    assert mirror._get_conn() is not first
