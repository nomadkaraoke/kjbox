"""Song identification endpoint + the persistent search log (docs/SONG-IDENTIFICATION.md)."""
import gzip

import pytest

import routes
from scripts.build_song_id_db import build
from search_log import SearchLog
from song_identify import SongIdentifier


@pytest.fixture
def wired(sing_app, tmp_path, monkeypatch):
    src = tmp_path / "songs.tsv.gz"
    with gzip.open(src, "wt", encoding="utf-8") as f:
        for row in [("The Strokes", "Machu Picchu", 64, 1), ("Rihanna", "Push Up On Me", 47, 0),
                    ("Sabrina Carpenter", "Espresso", 90, 1), ("Leonard Cohen", "Hallelujah", 70, 1),
                    ("Jeff Buckley", "Hallelujah", 72, 1)]:
            f.write("\t".join(map(str, row)) + "\n")
    build(str(src), str(tmp_path / "song_id.db"))
    sing_app.song_identifier = SongIdentifier(str(tmp_path / "song_id.db"))
    sing_app.search_log = SearchLog(str(tmp_path / "search_log.db"))
    monkeypatch.setattr(routes, "unified_search", lambda q, app, **kw: {"songs": []})
    return sing_app


def _identify(client, token, q, sid="s1"):
    return client.get("/sing/search/identify", query_string={"q": q, "t": token, "sid": sid,
                                                              "device_id": "d" * 32}).get_json()


@pytest.mark.parametrize("q,kind", [
    ("rihanna push up on me", "cosmetic"),      # same words → "Tidied to"
    ("Rihanna Push Up On Me", "same"),          # typed exactly → nothing to say
    ("espresso", "completed"),                  # title only → artist added
    ("the stokes max picu", "content"),         # typos → "Corrected to"
])
def test_identify_kinds(client, token, wired, q, kind):
    body = _identify(client, token, q)
    assert body["status"] == "confident" and body["kind"] == kind


def test_identify_candidates_and_none(client, token, wired):
    body = _identify(client, token, "hallelujah")
    assert body["status"] == "candidates"
    assert {c["artist"] for c in body["candidates"][:2]} == {"Jeff Buckley", "Leonard Cohen"}
    assert _identify(client, token, "xqzv blorp wibble")["status"] == "none"


def test_identify_without_index_is_none(client, token, sing_app, tmp_path):
    sing_app.song_identifier = SongIdentifier(str(tmp_path / "missing.db"))
    assert _identify(client, token, "espresso") == {"status": "none", "unavailable": True}


def test_search_identify_and_choice_are_logged_under_one_search_id(client, token, wired):
    client.get("/sing/search", query_string={"q": "the stokes max picu", "t": token, "sid": "abc"})
    _identify(client, token, "the stokes max picu", sid="abc")
    r = client.post(f"/sing/search/event?t={token}", json={
        "sid": "abc", "action": "keep_typed", "q": "the stokes max picu", "data": {"shown": "The Strokes — Machu Picchu"}})
    assert r.status_code == 200
    ev = wired.search_log.events()
    assert [e["type"] for e in ev] == ["search", "identify", "choice"]
    assert {e["search_id"] for e in ev} == {"abc"}
    assert ev[1]["data"]["best"] == {"artist": "The Strokes", "title": "Machu Picchu", "karaoke": True}
    assert ev[2]["data"] == {"action": "keep_typed", "shown": "The Strokes — Machu Picchu"}


def test_unknown_choice_action_rejected(client, token, wired):
    r = client.post(f"/sing/search/event?t={token}", json={"sid": "x", "action": "drop_tables"})
    assert r.status_code == 400
    assert wired.search_log.events() == []


def test_search_log_never_raises(tmp_path):
    bad = SearchLog(str(tmp_path / "no-such-dir" / "log.db"))
    bad.log("search", query="x")          # unwritable path: swallowed
    big = SearchLog(str(tmp_path / "log.db"))
    big.log("choice", data={"blob": "x" * 10000})
    assert big.events()[0]["data"]["truncated"] is True


def test_report_flags_undo_and_missed_identifications(tmp_path, capsys):
    from scripts import search_log_report
    sl = SearchLog(str(tmp_path / "log.db"))
    sl.log("identify", search_id="a", query="the stokes max picu",
           data={"status": "confident", "kind": "content", "best": {"artist": "The Strokes", "title": "Machu Picchu"}})
    sl.log("choice", search_id="a", query="the stokes max picu", data={"action": "keep_typed"})
    sl.log("identify", search_id="b", query="brand new song", data={"status": "none"})
    sl.log("choice", search_id="b", query="brand new song", data={"action": "make_submit", "edited": False})
    sl.log("identify", search_id="c", query="espresso", data={"status": "confident", "kind": "completed"})
    sl.log("choice", search_id="c", query="espresso", data={"action": "request_song"})
    assert search_log_report.main(["--db", str(tmp_path / "log.db")]) == 0
    out = capsys.readouterr().out
    assert "undid-tidy" in out and "make-without-identification" in out
    assert "session c" not in out          # a clean request isn't flagged


def test_client_cannot_override_validated_action(client, token, wired):
    client.post(f"/sing/search/event?t={token}", json={
        "sid": "x", "action": "accept_tidy", "data": {"action": "not_it", "extra": 1}})
    ev = wired.search_log.events()
    assert ev[0]["data"] == {"action": "accept_tidy", "extra": 1}


def test_event_and_identify_are_rate_limited(client, token, wired, monkeypatch):
    import sing
    sing._resolve_rate_state.clear()
    monkeypatch.setattr(sing, "_EVENT_RATE_PER_DEVICE", 2)
    monkeypatch.setattr(sing, "_IDENTIFY_RATE_PER_DEVICE", 1)
    body = {"sid": "x", "action": "keep_typed", "device_id": "d" * 32}
    codes = [client.post(f"/sing/search/event?t={token}", json=body).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert _identify(client, token, "espresso").get("status") == "confident"
    r = client.get("/sing/search/identify", query_string={"q": "espresso", "t": token, "device_id": "d" * 32})
    assert r.status_code == 429


def test_search_log_prunes_old_and_excess_rows(tmp_path, monkeypatch):
    import search_log as sl_mod
    path = str(tmp_path / "log.db")
    sl = SearchLog(path)
    for i in range(5):
        sl.log("search", query=f"q{i}")
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute("UPDATE search_events SET ts = 0 WHERE query = 'q0'")     # ancient
    conn.commit(); conn.close()
    monkeypatch.setattr(sl_mod, "MAX_ROWS", 3)
    assert [e["query"] for e in SearchLog(path).events()] == ["q2", "q3", "q4"]
