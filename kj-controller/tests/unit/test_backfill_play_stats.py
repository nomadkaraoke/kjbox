import sqlite3
import importlib.util
import os

HERE = os.path.dirname(__file__)
SPEC = importlib.util.spec_from_file_location(
    "backfill_play_stats",
    os.path.join(HERE, "..", "..", "scripts", "backfill_play_stats.py"))


def _load():
    mod = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(mod)
    return mod


def _make_archive(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE rotation_archive (
        night_date TEXT, singer TEXT, song_artist TEXT, status TEXT,
        notes TEXT, position INTEGER, file_path TEXT, duration REAL, created_at TEXT)""")
    conn.executemany(
        "INSERT INTO rotation_archive (night_date, singer, song_artist, status, file_path)"
        " VALUES (?,?,?,?,?)",
        [("2026-03-27", "Celeste", "ABBA - SOS", "Done", "/opt/nomad/downloads/x.mp4"),
         ("2026-03-27", "Dan", "Queen - Bohemian Rhapsody", "Done", None),        # no path
         ("2026-03-27", "Amy", "ABBA - SOS", "Waiting", "/opt/nomad/downloads/x.mp4")])  # not Done
    conn.commit(); conn.close()


def test_backfill_attributes_done_rows(tmp_path):
    mod = _load()
    from media_library import MediaLibraryStore
    rot = str(tmp_path / "rotation.db"); mldb = str(tmp_path / "media_library.db")
    _make_archive(rot)
    ml = MediaLibraryStore(mldb)
    ml.upsert({"media_id": "yt-x", "source": "youtube", "artist": "ABBA",
               "title": "SOS", "file_path": "/opt/nomad/downloads/x.mp4"})
    res = mod.backfill(rot, mldb, execute=True)
    assert res["attributed"] == 1        # only the Done row with a resolvable path
    assert res["skipped"] == 1           # Done row with no path (Waiting row ignored entirely)

    from stats_store import StatsStore
    s = StatsStore(mldb)
    assert s.stats_for(["yt-x"])["yt-x"]["plays"] == 1


def test_backfill_idempotent(tmp_path):
    mod = _load()
    from media_library import MediaLibraryStore
    from stats_store import StatsStore
    rot = str(tmp_path / "rotation.db"); mldb = str(tmp_path / "media_library.db")
    _make_archive(rot)
    MediaLibraryStore(mldb).upsert({"media_id": "yt-x", "source": "youtube",
        "artist": "ABBA", "title": "SOS", "file_path": "/opt/nomad/downloads/x.mp4"})
    mod.backfill(rot, mldb, execute=True)
    mod.backfill(rot, mldb, execute=True)     # re-run
    assert StatsStore(mldb).stats_for(["yt-x"])["yt-x"]["plays"] == 1
