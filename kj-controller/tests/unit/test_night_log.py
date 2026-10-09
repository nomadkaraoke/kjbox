"""Tests for night_log.NightLog (past-night review) and its /history routes."""

import pytest

from media_library import MediaLibraryStore
from night_log import NightLog, night_of
from rotation_store import RotationStore
from sing_store import SingStore
from stats_store import StatsStore


@pytest.fixture
def dbs(tmp_path):
    rot_path = str(tmp_path / "rotation.db")
    media_path = str(tmp_path / "media.db")
    store = RotationStore(rot_path)
    SingStore(rot_path)  # creates sing_requests in the same DB, as on the device
    ml = MediaLibraryStore(media_path)
    stats = StatsStore(media_path)
    yield store, ml, stats
    store.close()


def _log(store, stats):
    return NightLog(store, stats._get_conn)


def _legacy_archive(store, singer, song, file_path, created_at, status="Done", night_date="2026-10-09"):
    """Insert an archive row the way pre-night-log archives looked (no entry id)."""
    conn = store._get_conn()
    conn.execute(
        "INSERT INTO rotation_archive (night_date, singer, song_artist, status, notes, "
        "position, file_path, duration, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (night_date, singer, song, status, "", 0, file_path, None, created_at))
    conn.commit()


def _request(store, singer, artist, title, source_type, reviewed_at, linked_entry_id=None,
             status="approved"):
    conn = store._get_conn()
    conn.execute(
        "INSERT INTO sing_requests (created_at, token, singer_name, phone, song_artist, "
        "song_title, source_type, status, reviewed_at, linked_entry_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (reviewed_at, "tok", singer, "+15550000000", artist, title, source_type, status,
         reviewed_at, linked_entry_id))
    conn.commit()


def _play(stats, media_id, entry_id, singer, played_utc):
    conn = stats._get_conn()
    conn.execute(
        "INSERT INTO play_events (media_id, singer, singer_norm, played_at, night_date, "
        "entry_id, source) VALUES (?,?,?,?,?,?, 'live')",
        (media_id, singer, (singer or "").lower(), played_utc, played_utc[:10], entry_id))
    conn.commit()


def _utc_of_local(local):
    """Convert a local 'YYYY-MM-DD HH:MM:SS' to the UTC string play_events stores."""
    import datetime as dt
    t = dt.datetime.strptime(local, "%Y-%m-%d %H:%M:%S")
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------

def test_night_of_rolls_at_noon():
    assert night_of("2026-10-08 21:00:00") == "2026-10-08"
    assert night_of("2026-10-09 01:30:00") == "2026-10-08"
    assert night_of("2026-10-09 11:59:59") == "2026-10-08"
    assert night_of("2026-10-09 12:00:00") == "2026-10-09"
    assert night_of(None) is None
    assert night_of("garbage") is None


def test_archive_keeps_entry_id_done_at_and_sources(dbs):
    store, _, _ = dbs
    e = store.add_entry("Alice", song_artist="ABBA - SOS")
    store.set_download_status(e["id"], "youtube", "complete", "dl-1")
    store.set_gen_status(e["id"], "job-9", "complete")
    store.update_status(e["id"], "Done")
    store.archive()
    row = dict(store._get_conn().execute(
        "SELECT entry_id, done_at, download_source, gen_job_id FROM rotation_archive").fetchone())
    assert row["entry_id"] == e["id"]
    assert row["done_at"]
    assert row["download_source"] == "youtube"
    assert row["gen_job_id"] == "job-9"


def test_archive_migration_adds_columns_to_old_db(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE rotation_archive (id INTEGER PRIMARY KEY AUTOINCREMENT, night_date TEXT NOT NULL, "
        "singer TEXT NOT NULL, song_artist TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', "
        "notes TEXT NOT NULL DEFAULT '', position INTEGER, file_path TEXT, duration INTEGER, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))")
    conn.execute("INSERT INTO rotation_archive (night_date, singer) VALUES ('2026-01-01', 'Old')")
    conn.commit()
    conn.close()
    store = RotationStore(path)
    cols = {r[1] for r in store._get_conn().execute("PRAGMA table_info(rotation_archive)")}
    assert {"entry_id", "done_at", "download_source", "gen_job_id", "singers_json"} <= cols
    assert store._get_conn().execute("SELECT COUNT(*) FROM rotation_archive").fetchone()[0] == 1
    store.close()


def test_new_archive_links_request_exactly(dbs):
    store, _, stats = dbs
    kj = store.add_entry("Andrew KJ", song_artist="Warrant - Cherry Pie")
    sung = store.add_entry("Bailey", song_artist="Cameo - Word Up",
                           file_path="/opt/nomad/downloads/youtube/Cameo - Word Up [yt-abc].mp4")
    store.set_download_status(sung["id"], "youtube", "complete", "dl-1")
    _request(store, "Bailey", "Cameo", "Word Up", "youtube", "2026-10-08 21:00:00",
             linked_entry_id=sung["id"])
    store.update_status(kj["id"], "Done")
    store.update_status(sung["id"], "Done")
    store.archive()

    log = _log(store, stats)
    night = night_of(store._get_conn().execute(
        "SELECT done_at FROM rotation_archive WHERE entry_id=?", (sung["id"],)).fetchone()[0])
    entries = {e["singer"]: e for e in log.get_night(night)}
    b = entries["Bailey"]
    assert b["added_by"]["by"] == "singer"
    assert b["added_by"]["source_label"] == "YouTube"
    assert b["added_by"]["requested_as"] == "Cameo - Word Up"
    assert b["media"]["kind"] == "youtube"
    assert b["media"]["fetched_tonight"] is True
    assert b["media"]["file"] == "Cameo - Word Up [yt-abc].mp4"
    assert b["sung"] and b["sung_at"]
    k = entries["Andrew KJ"]
    assert k["added_by"] == {"by": "kj"}
    assert k["media"]["kind"] == "none"


def test_legacy_rows_link_through_play_events(dbs):
    store, ml, stats = dbs
    path = "/media/nomad/SSD/SC8722-06 - Rod Stewart - Do Ya Think I'm Sexy.zip"
    ml.upsert({"media_id": "lib-abc123", "source": "library", "file_path": path,
               "artist": "Rod Stewart", "title": "Do Ya Think I'm Sexy"})
    _legacy_archive(store, "Jude", "Do Ya Think I'm Sexy - Rod Stewart", path, "2026-10-08 20:57:51")
    # The original entry (id 1477) was sung at 21:04 local and linked to a request.
    _play(stats, "lib-abc123", 1477, "Jude", _utc_of_local("2026-10-08 21:04:32"))
    _request(store, "Jude", "Rod Stewart", "Do Ya Think I'm Sexy", "local",
             "2026-10-08 20:57:40", linked_entry_id=1477)

    [e] = _log(store, stats).get_night("2026-10-08")
    assert e["sung_at"] == "2026-10-08 21:04:32"
    assert e["entry_id"] == 1477
    assert e["added_by"]["by"] == "singer"
    assert e["added_by"]["source_label"] == "Library search"
    assert e["media"]["kind"] == "library"
    assert e["media"]["label"] == "Library"
    assert e["media"]["fetched_tonight"] is False


def test_legacy_rows_fall_back_to_request_time_match(dbs):
    store, _, stats = dbs
    _legacy_archive(store, "Mija", "Adele - Make You Feel My Love",
                    "/opt/nomad/downloads/community/x [db-1234].zip", "2026-10-09 01:37:09")
    _request(store, "Mija", "Adele", "Make You Feel My Love", "divebar", "2026-10-09 01:37:08",
             linked_entry_id=999)
    # A request for someone else at the same moment must not be taken.
    _request(store, "Other", "X", "Y", "local", "2026-10-09 01:37:09", linked_entry_id=1000)
    [e] = _log(store, stats).get_night("2026-10-08")
    assert e["added_by"]["by"] == "singer"
    assert e["added_by"]["source_type"] == "divebar"
    assert e["media"]["kind"] == "community"
    assert e["media"]["label"] == "Divebar"


def test_legacy_unlinked_row_is_kj_inferred(dbs):
    store, _, stats = dbs
    _legacy_archive(store, "Andrew KJ", "Billy Talent - Saint Veronika",
                    "/opt/nomad/downloads/NOMAD-720p/NOMAD-1288 - Billy Talent - Saint Veronika.mp4",
                    "2026-10-08 20:55:05")
    [e] = _log(store, stats).get_night("2026-10-08")
    assert e["added_by"] == {"by": "kj", "inferred": True}
    assert e["media"]["kind"] == "master"
    assert e["sung_at"] is None  # no play event to time it


def test_list_nights_filters_by_min_songs_and_counts(dbs):
    store, _, stats = dbs
    for i in range(12):
        _legacy_archive(store, f"S{i % 4}", f"Song {i}", None, f"2026-10-01 21:{i:02d}:00")
    _legacy_archive(store, "Left", "Never sung", None, "2026-10-01 21:30:00", status="Left")
    for i in range(3):  # a test session
        _legacy_archive(store, "Andrew", f"Test {i}", None, f"2026-10-05 14:0{i}:00")
    log = _log(store, stats)

    nights = log.list_nights(min_songs=10)
    assert [n["night"] for n in nights] == ["2026-10-01"]
    n = nights[0]
    assert n["sung"] == 12 and n["entries"] == 13 and n["singers"] == 4
    assert n["first_at"] == "2026-10-01 21:00:00"

    assert [n["night"] for n in log.list_nights(min_songs=0)] == ["2026-10-05", "2026-10-01"]


def test_get_night_orders_by_sung_time_then_unsung(dbs):
    store, _, stats = dbs
    _legacy_archive(store, "A", "third", None, "2026-10-01 21:10:00")
    _legacy_archive(store, "B", "first", None, "2026-10-01 21:00:00")
    _legacy_archive(store, "C", "left", None, "2026-10-01 21:05:00", status="Left")
    entries = _log(store, stats).get_night("2026-10-01")
    # No play timestamps here, so everything falls back to when it was added.
    assert [e["singer"] for e in entries] == ["B", "C", "A"]
    # Unsung entries carry their status and sung=False.
    c = next(e for e in entries if e["singer"] == "C")
    assert c["sung"] is False and c["status"] == "Left"


def test_live_unsung_entries_belong_to_the_coming_night(dbs):
    store, _, stats = dbs
    conn = store._get_conn()
    store.add_entry("Andrew", song_artist="First Song of the Night")
    conn.execute("UPDATE rotation_entries SET created_at='2026-10-09 11:37:44'")
    conn.execute("INSERT OR REPLACE INTO rotation_meta (key, value) "
                 "VALUES ('night_started_at', '2026-10-09 11:37:44')")
    conn.commit()
    [e] = _log(store, stats).get_night("2026-10-09")
    assert e["live"] is True and e["sung"] is False


def test_works_without_media_db(dbs):
    store, _, _ = dbs
    _legacy_archive(store, "A", "x", "/opt/nomad/downloads/youtube/x [yt-q].mp4", "2026-10-01 21:00:00")
    [e] = NightLog(store, None).get_night("2026-10-01")
    assert e["media"]["kind"] == "youtube"


# --- routes ---------------------------------------------------------------

def test_history_routes(flask_test_client, flask_app):
    store = flask_app.rotation.store
    for i in range(11):
        _legacy_archive(store, "S", f"Song {i}", None, f"2026-10-01 21:{i:02d}:00")
    r = flask_test_client.get("/history/nights")
    assert r.status_code == 200
    body = r.get_json()
    assert body["min_songs"] == 10
    assert [n["night"] for n in body["nights"]] == ["2026-10-01"]

    assert flask_test_client.get("/history/nights?min_songs=50").get_json()["nights"] == []
    assert flask_test_client.get("/history/nights?min_songs=abc").get_json()["min_songs"] == 10

    r = flask_test_client.get("/history/night?night=2026-10-01")
    assert r.status_code == 200
    assert len(r.get_json()["entries"]) == 11

    assert flask_test_client.get("/history/night?night=nope").status_code == 400


def test_get_night_orders_by_when_sung_not_when_added(dbs):
    store, _, stats = dbs
    _legacy_archive(store, "X", "x", "/d/youtube/x [yt-x].mp4", "2026-10-01 21:00:00")
    _legacy_archive(store, "Y", "y", "/d/youtube/y [yt-y].mp4", "2026-10-01 21:10:00")
    _play(stats, "yt-x", 1, "X", _utc_of_local("2026-10-01 21:30:00"))
    _play(stats, "yt-y", 2, "Y", _utc_of_local("2026-10-01 21:15:00"))
    assert [e["singer"] for e in _log(store, stats).get_night("2026-10-01")] == ["Y", "X"]


def test_unsung_duplicate_does_not_steal_the_play(dbs):
    store, _, stats = dbs
    path = "/d/youtube/x [yt-x].mp4"
    _legacy_archive(store, "Jude", "x", path, "2026-10-08 20:48:03", status="Waiting")
    _legacy_archive(store, "Jude", "x", path, "2026-10-08 20:57:51")
    _play(stats, "yt-x", 7, "Jude", _utc_of_local("2026-10-08 21:04:32"))
    entries = _log(store, stats).get_night("2026-10-08")
    sung = [e for e in entries if e["sung"]]
    assert len(sung) == 1 and sung[0]["sung_at"] == "2026-10-08 21:04:32"
    assert sung[0]["added_at"] == "2026-10-08 20:57:51"
    unsung = [e for e in entries if not e["sung"]]
    assert unsung[0]["sung_at"] is None
