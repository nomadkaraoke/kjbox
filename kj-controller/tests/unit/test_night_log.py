"""Tests for night_log.NightLog (past-night review) and its /history routes."""

import pytest

from media_library import MediaLibraryStore
from night_log import NightLog, night_of
from rotation_store import RotationStore
from sing_store import SingStore
from stats_store import StatsStore
from tests.unit.night_log_helpers import (
    _exact_archive,
    _legacy_archive,
    _play,
    _request,
    _utc_of_local,
)


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
    assert flask_test_client.get("/history/night?night=2026-13-45").status_code == 400


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


def test_request_time_match_needs_singer_or_full_song(dbs):
    store, _, stats = dbs
    _legacy_archive(store, "Kim", "Love - Lana Del Rey", None, "2026-10-01 21:00:00")
    # Different singer, title alone matches: must NOT link.
    _request(store, "Pat", "Someone Else", "Love", "local", "2026-10-01 21:00:01")
    [e] = _log(store, stats).get_night("2026-10-01")
    assert e["added_by"]["by"] == "kj"
    # Renamed singer but title + artist both match: links.
    _request(store, "Kimberly", "Lana Del Rey", "Love", "youtube", "2026-10-01 21:00:02")
    [e] = _log(store, stats).get_night("2026-10-01")
    assert e["added_by"]["source_type"] == "youtube"


def test_started_at_is_play_start_and_sung_at_is_done_time(dbs):
    # Since the Night Log shipped, sung_at = done_at = when the KJ marked the entry
    # Done (the song's END). started_at carries the play_events start so the
    # night-clips tool can attribute phone clips to the song actually on stage.
    store, _, stats = dbs
    e = store.add_entry("Lulu K", song_artist="Ana Ng - They Might Be Giants")
    _play(stats, "yt-ana", e["id"], "Lulu K", _utc_of_local("2026-10-15 22:13:31"))
    store.update_status(e["id"], "Done")
    conn = store._get_conn()
    conn.execute("UPDATE rotation_entries SET done_at=? WHERE id=?", ("2026-10-15 22:16:57", e["id"]))
    conn.commit()

    [got] = _log(store, stats).get_night("2026-10-15")
    assert got["started_at"] == "2026-10-15 22:13:31"
    assert got["sung_at"] == "2026-10-15 22:16:57"


def test_started_at_none_without_play_event(dbs):
    store, _, stats = dbs
    _legacy_archive(store, "Jude", "Song - Artist", None, "2026-10-08 20:57:51")
    [e] = _log(store, stats).get_night("2026-10-08")
    assert e["started_at"] is None


# --- history-claim fields (Phase 4c) ------------------------------------------

def test_exact_row_link_exact_and_started_at_from_play(dbs):
    store, _, stats = dbs
    _exact_archive(store, 5012, "Wren B", "Starlight - Muse", "2026-10-20 21:40:00",
                   "2026-10-20 22:10:00", file_path="/media/x [lib-star1].mp4")
    _play(stats, "lib-star1", 5012, "Wren B", _utc_of_local("2026-10-20 22:06:00"),
          artist="Muse", title="Starlight")
    [e] = _log(store, stats).get_night("2026-10-20")
    assert e["link"] == "exact"
    assert e["entry_id"] == 5012
    assert e["started_at"] == "2026-10-20 22:06:00"  # play start
    assert e["sung_at"] == "2026-10-20 22:10:00"     # Done = END (#284), unchanged
    assert e["done_at"] == "2026-10-20 22:10:00"
    assert e["members"] == ["Wren B"]


def test_legacy_row_inferred_link_via_play_event(dbs):
    store, ml, stats = dbs
    path = "/media/nomad/SSD/SC1234-01 - Muse - Starlight.zip"
    ml.upsert({"media_id": "lib-muse1", "source": "library", "file_path": path,
               "artist": "Muse", "title": "Starlight"})
    _legacy_archive(store, "Juno & Wren B", "Starlight - Muse", path, "2026-10-08 20:57:51")
    _play(stats, "lib-muse1", 1477, "Juno & Wren B", _utc_of_local("2026-10-08 21:04:32"))
    [e] = _log(store, stats).get_night("2026-10-08")
    assert e["link"] == "inferred"
    assert e["entry_id"] == 1477
    assert e["done_at"] is None
    assert e["started_at"] == "2026-10-08 21:04:32"
    assert e["members"] == ["Juno", "Wren B"]


def test_legacy_unlinked_row_link_none(dbs):
    store, _, stats = dbs
    _legacy_archive(store, "Quill", "Hello - Adele", None, "2026-10-08 20:55:05")
    [e] = _log(store, stats).get_night("2026-10-08")
    assert e["link"] == "none"
    assert e["entry_id"] is None
    assert e["song_identity"] == {"artist": "Adele", "title": "Hello"}


def test_members_from_singers_json(dbs):
    store, _, stats = dbs
    _exact_archive(store, 6001, "Juno", "Hello - Adele", "2026-10-20 21:00:00",
                   "2026-10-20 21:05:00", singers_json='["Juno", "Wren B"]')
    [e] = _log(store, stats).get_night("2026-10-20")
    assert e["members"] == ["Juno", "Wren B"]


def test_song_identity_precedence(dbs):
    store, _, stats = dbs
    # 1. The linked request's artist/title wins over the play and the text.
    _exact_archive(store, 7001, "Wren", "Starlite - Muse (KJ typo)", "2026-10-20 21:00:00",
                   "2026-10-20 21:05:00")
    _play(stats, "lib-a", 7001, "Wren", _utc_of_local("2026-10-20 21:01:00"),
          artist="Muse (Play)", title="Starlight (Play)")
    _request(store, "Wren", "Muse", "Starlight", "local", "2026-10-20 20:59:00",
             linked_entry_id=7001)
    # 2. No request: the play's media identity.
    _exact_archive(store, 7002, "Juno", "Helo - Adele", "2026-10-20 21:10:00",
                   "2026-10-20 21:15:00")
    _play(stats, "lib-b", 7002, "Juno", _utc_of_local("2026-10-20 21:11:00"),
          artist="Adele", title="Hello")
    # 3. Neither: the rotation text, split "Title - Artist".
    _exact_archive(store, 7003, "Quill", "Uprising - Muse", "2026-10-20 21:20:00",
                   "2026-10-20 21:25:00")
    by_id = {e["entry_id"]: e for e in _log(store, stats).get_night("2026-10-20")}
    assert by_id[7001]["song_identity"] == {"artist": "Muse", "title": "Starlight"}
    assert by_id[7002]["song_identity"] == {"artist": "Adele", "title": "Hello"}
    assert by_id[7003]["song_identity"] == {"artist": "Muse", "title": "Uprising"}


def test_build_cached_invalidates_on_new_archive_row(dbs, monkeypatch):
    import night_log
    store, _, stats = dbs
    night_log.clear_build_cache()
    _exact_archive(store, 8001, "Wren", "Hello - Adele", "2026-10-20 21:00:00",
                   "2026-10-20 21:05:00")
    calls = []
    real = NightLog._build

    def counting(self):
        calls.append(1)
        return real(self)

    monkeypatch.setattr(NightLog, "_build", counting)
    first = _log(store, stats).build_cached()
    again = _log(store, stats).build_cached()  # a new NightLog per request, as in routes
    assert len(calls) == 1
    assert [e["entry_id"] for e in again] == [e["entry_id"] for e in first] == [8001]

    _exact_archive(store, 8002, "Juno", "Uprising - Muse", "2026-10-20 21:10:00",
                   "2026-10-20 21:15:00")
    fresh = _log(store, stats).build_cached()
    assert len(calls) == 2
    assert sorted(e["entry_id"] for e in fresh) == [8001, 8002]

    # A rotation rev bump and a new play also invalidate; the TTL expires it.
    store.bump_rev()
    _log(store, stats).build_cached()
    assert len(calls) == 3
    _play(stats, "lib-z", 8002, "Juno", _utc_of_local("2026-10-20 21:11:00"))
    _log(store, stats).build_cached()
    assert len(calls) == 4
    _log(store, stats).build_cached(ttl_s=0)
    assert len(calls) == 5

    # The KJ modal paths stay uncached.
    _log(store, stats).get_night("2026-10-20")
    _log(store, stats).list_nights(min_songs=0)
    assert len(calls) == 7


def test_build_cached_returns_copies(dbs):
    import night_log
    store, _, stats = dbs
    night_log.clear_build_cache()
    _exact_archive(store, 8101, "Wren", "Hello - Adele", "2026-10-20 21:00:00",
                   "2026-10-20 21:05:00")
    first = _log(store, stats).build_cached()
    first[0]["singer"] = "changed by a caller"
    first.append({"entry_id": "junk"})
    again = _log(store, stats).build_cached()
    assert [e["singer"] for e in again] == ["Wren"]


def test_build_cached_keeps_in_memory_stores_apart():
    import night_log
    night_log.clear_build_cache()
    a, b = RotationStore(":memory:"), RotationStore(":memory:")
    # Same state key on both (one archive row each, same rev, no media DB).
    _exact_archive(a, 9001, "Wren", "Hello - Adele", "2026-10-20 21:00:00",
                   "2026-10-20 21:05:00")
    _exact_archive(b, 9002, "Juno", "Uprising - Muse", "2026-10-20 21:00:00",
                   "2026-10-20 21:05:00")
    assert [e["entry_id"] for e in NightLog(a).build_cached()] == [9001]
    assert [e["entry_id"] for e in NightLog(b).build_cached()] == [9002]


# --- claims on the Night Log + host undo (Phase 4c E7) -----------------------------

def test_entries_carry_claim_counts(dbs):
    store, _, stats = dbs
    sing = SingStore(store.db_path)
    _exact_archive(store, 8201, "Juno", "Hello - Adele", "2026-10-20 21:00:00",
                   "2026-10-20 21:05:00", singers_json='["Juno", "Wren B"]')
    _exact_archive(store, 8202, "Wren", "Uprising - Muse", "2026-10-20 21:10:00",
                   "2026-10-20 21:15:00")
    sing.try_claim("2026-10-20", 8201, "e2e-a@example.com", "kj_a", "Juno", 2)
    sing.try_claim("2026-10-20", 8201, "e2e-b@example.com", "kj_b", "Wren B", 2)
    by_id = {e["entry_id"]: e for e in _log(store, stats).get_night("2026-10-20")}
    assert by_id[8201]["claims"] == 2
    assert by_id[8202]["claims"] == 0
    sing.close()


class _FakeHttp:
    def __init__(self):
        self.calls = []

    def put(self, url, json=None, headers=None, timeout=None):
        self.calls.append(("PUT", url, json))
        return type("R", (), {"status_code": 200, "text": ""})()

    def delete(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("DELETE", url, params))
        return type("R", (), {"status_code": 204, "text": ""})()


def _claimed_past_song(flask_app, email="e2e-wren@example.com", eid=8301):
    rs, ss = flask_app.rotation.store, flask_app.sing_store
    _exact_archive(rs, eid, "Wren B", "Hello - Adele", "2026-10-20 21:00:00",
                   "2026-10-20 21:05:00")
    hid = f"kj_test_{email[:8]}_{eid}"
    assert ss.try_claim("2026-10-20", eid, email, hid, "Wren B", 1) == "claimed"
    ss.outbox_enqueue(hid, "put", {"history_id": hid, "gen_email": email,
                                   "entry_id": str(eid)}, "claim:2026-10-20")
    return hid


def test_claims_undo_route_deletes_claim_and_retracts_record(flask_app, flask_test_client):
    import night_log as nl
    from history_claims import suggestions
    ss = flask_app.sing_store
    hid = _claimed_past_song(flask_app)
    ob = flask_app.history_outbox
    http = _FakeHttp()
    ob._put, ob._delete = http.put, http.delete
    ob.cfg.update({"decide_api_url": "https://decide.test", "decide_kjbox_secret": "s"})
    ss.outbox_mark_sent(hid)

    r = flask_test_client.post("/history/claims/undo",
                               json={"night": "2026-10-20", "entry_id": 8301})
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"undone": 1}
    assert ss.claims_for_nights(["2026-10-20"]) == {}
    row = ss._get_conn().execute("SELECT op, state, payload_json FROM history_outbox "
                                 "WHERE history_id=?", (hid,)).fetchone()
    assert (row["op"], row["state"]) == ("delete", "pending")
    import json
    assert json.loads(row["payload_json"]) == {"history_id": hid,
                                               "gen_email": "e2e-wren@example.com",
                                               "entry_id": "8301"}
    assert ob._wake.is_set()
    assert ob.deliver_due() == 1
    assert http.calls == [("DELETE", "https://decide.test/api/partner/kjbox/karaoke-log/"
                           + hid, {"gen_email": "e2e-wren@example.com"})]
    # The song is suggestible again.
    nl.clear_build_cache()
    names = {"words": {"wren"}, "names": []}
    log = nl.NightLog(flask_app.rotation.store, flask_app.stats._get_conn)
    got = suggestions(log, ss, flask_app.kj_config, names, "e2e-other@example.com")
    assert [s["entry_id"] for s in got["nights"][0]["songs"]] == [8301]


def test_claims_undo_route_one_email_and_validation(flask_app, flask_test_client):
    ss = flask_app.sing_store
    _exact_archive(flask_app.rotation.store, 8401, "Juno & Wren B", "Hello - Adele",
                   "2026-10-20 21:00:00", "2026-10-20 21:05:00")
    ss.try_claim("2026-10-20", 8401, "e2e-a@example.com", "kj_a", "Juno", 2)
    ss.try_claim("2026-10-20", 8401, "e2e-b@example.com", "kj_b", "Wren B", 2)
    r = flask_test_client.post("/history/claims/undo", json={
        "night": "2026-10-20", "entry_id": 8401, "gen_email": "E2E-B@example.com"})
    assert r.get_json() == {"undone": 1}
    assert ss.claims_for_nights(["2026-10-20"]) == {("2026-10-20", 8401): ["e2e-a@example.com"]}
    assert flask_test_client.post("/history/claims/undo", json={
        "night": "2026-10-20", "entry_id": 8401, "gen_email": "nobody@example.com"}
    ).get_json() == {"undone": 0}
    for bad in ({}, {"night": "2026-13-45", "entry_id": 1}, {"night": "2026-10-20"},
                {"night": "2026-10-20", "entry_id": "x"}, {"night": "2026-10-20", "entry_id": True},
                {"night": "2026-10-20", "entry_id": 1, "gen_email": 5}):
        assert flask_test_client.post("/history/claims/undo", json=bad).status_code == 400, bad


def test_claims_undo_not_on_public_host(mock_config):
    from app import create_app
    mock_config["sing_public_host"] = "sing.nomadkaraoke.com"
    app = create_app(config=mock_config)
    app.config["TESTING"] = True
    try:
        with app.test_client() as c:
            for path in ("/history/claims/undo", "/sing/history/claims/undo"):
                r = c.post(path, json={"night": "2026-10-20", "entry_id": 1},
                           headers={"Host": "sing.nomadkaraoke.com"})
                assert r.status_code == 404, path
    finally:
        app.catalog.close()


def test_build_cached_is_single_flight(dbs, monkeypatch):
    # Several requests missing the cache at once build it once, not once each.
    import threading
    import time
    import night_log
    store, _, stats = dbs
    night_log.clear_build_cache()
    _exact_archive(store, 8001, "Wren", "Hello - Adele", "2026-10-20 21:00:00",
                   "2026-10-20 21:05:00")
    calls = []
    real = NightLog._build

    def slow(self):
        calls.append(1)
        time.sleep(0.2)
        return real(self)

    monkeypatch.setattr(NightLog, "_build", slow)
    start = threading.Barrier(4)
    got, errors = [], []

    def worker():
        try:
            start.wait()
            got.append([e["entry_id"] for e in _log(store, stats).build_cached()])
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert got == [[8001]] * 4
    assert len(calls) == 1
