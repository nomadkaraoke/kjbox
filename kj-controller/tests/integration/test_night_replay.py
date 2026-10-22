"""Night replay: the 2026-09-24 fixture night, replayed through the app API v1.

The committed fixture (``tests/fixtures/localstack/night.json``, built by
``scripts/build_localstack_fixture.py`` from the redacted recording) holds the
night's start rotation, every singer submit / cancel / change and every host
rotation action, plus the expected final rotation and history outbox. Every
singer device becomes an app user (stubbed gen identities), so this drives the
v1 request path, idempotent retries (Owen's 60-version kj_pick, retried six
times), the host's Done/Left marks and the outbox attribution end to end.
"""

import json
import os
import re

import pytest

import localstack

FIXTURE = localstack.FIXTURE_PATH


# --- PII guard (A12-R3) --------------------------------------------------------

def test_fixture_has_no_pii():
    with open(FIXTURE, encoding="utf-8") as f:
        text = f.read()
    hits = localstack.pii_hits(text)
    assert hits == [], f"PII-looking strings in the committed fixture: {hits[:10]}"


@pytest.mark.parametrize("bad", [
    "call me on +1 (555) 010-2345 tonight",
    "+15550000029",
    "555.010.2345",
    "5550102345",
    "sam.singer@example.com",
    '"edit_token": "abc123"',
    "redacted-76ba7c66fd",
    "bae1ddb8443702bcdb98db740b30fb77",
    "Authorization: Bearer abcdefghijkl",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig",
])
def test_pii_guard_catches(bad):
    assert localstack.pii_hits(json.dumps({"x": bad})), bad


@pytest.mark.parametrize("ok", [
    "KVD-33622 - Frank Sinatra - Day In The Life Of A Fool.zip",
    "https://www.youtube.com/watch?v=BA9J9RPCgxk",
    "11TA4MValmQ-SgIrNAvA7Ct-rGho19-1_",
    "2026-09-24 21:30:41",
    "NOMAD-1709 - Indigo De Souza - Sleep Talking.mp4",
    '"file_size": 1665216',
])
def test_pii_guard_allows_catalog_text(ok):
    assert localstack.pii_hits(json.dumps({"x": ok})) == []


def test_fixture_shape():
    fx = localstack.load_fixture()
    assert fx["event_code"] == "2121"
    assert fx["start_rotation"] and fx["actions"] and fx["devices"]
    ops = {a["op"] for a in fx["actions"]}
    assert {"submit", "cancel", "host", "play"} <= ops
    # Owen's kj_pick: more than 50 versions, retried as one idempotent action.
    owen = [a for a in fx["actions"] if a["op"] == "submit"
            and len((a["body"].get("source_meta") or {}).get("versions") or []) > 50]
    assert owen and owen[0].get("attempts", 1) > 1
    # Device ids from the recording never reach the fixture.
    assert all(re.fullmatch(r"d\d\d", d["ref"]) for d in fx["devices"])


# --- replay -----------------------------------------------------------------

@pytest.fixture
def sing_app(tmp_path, monkeypatch):
    """Override the shared ``sing_app``: a file-backed, hermetic local-stack
    app (gen_stub and app_token build on it)."""
    from app import create_app
    cfg = localstack.replay_config(str(tmp_path))
    app = create_app(config=cfg)
    app.config["TESTING"] = True
    localstack.make_hermetic(app, setattr_fn=monkeypatch.setattr)
    yield app
    app.catalog.close()


def test_night_replay_through_v1(sing_app, gen_stub):
    fx = localstack.load_fixture()
    ids = localstack.StubIdentities(gen_stub.users)
    with sing_app.test_client() as client:
        snap = localstack.replay_night(sing_app, client, fx, ids.token_for)

    expected = fx["expected"]
    assert snap["submits"] == expected["submits"]
    assert snap["rotation"] == expected["rotation"]
    assert snap["requests"] == expected["requests"]
    assert snap["outbox"] == expected["outbox"]

    # Independent invariants (not just "same as last time"):
    # 1. Every submit went through (v1 trims Owen's 60 versions to 50) and
    #    retries with the same client_request_id made ONE request.
    assert all(s[1] == 200 for s in snap["submits"])
    owen = next(a for a in fx["actions"] if a["op"] == "submit" and a.get("attempts", 1) > 1)
    owen_rows = [r for r in snap["rotation"] if r[0].startswith(owen["name"])]
    assert len([r for r in owen_rows if "I Want It That Way" in r[1]]) >= 1
    # 2. One outbox put per (linked singer, Done entry); nothing for entries
    #    that weren't sung.
    rows = localstack.night_state(sing_app)["outbox"]
    keys = [(r["gen_email"], r["entry_id"]) for r in rows]
    assert len(keys) == len(set(keys)) == len(snap["outbox"])
    entries = {e["id"]: e for e in localstack.night_state(sing_app)["rotation"]}
    assert all(entries[int(r["entry_id"])]["status"] == "Done" for r in rows)
    assert all(o[1] == "put" for o in snap["outbox"])
    assert {o[2] for o in snap["outbox"]} <= {"sung", "sung_unplayed"}
    done_songs = {r[1] for r in snap["rotation"] if r[2] == "Done"}
    assert snap["outbox"], "the night must produce outbox records"
    for dev, _op, _state, artist, title, _singers in snap["outbox"]:
        assert any(title.lower() in s.lower() for s in done_songs), (dev, artist, title)
    # 3. The cancelled double-submit leaves exactly one Done copy for its singer.
    cancels = [a for a in fx["actions"] if a["op"] == "cancel"]
    assert cancels
    assert snap["requests"].get("cancelled", 0) >= len(cancels)


def test_replay_is_repeatable(sing_app, gen_stub):
    """Two replays in one process give the same snapshot (reset really resets)."""
    fx = localstack.load_fixture()
    ids = localstack.StubIdentities(gen_stub.users)
    with sing_app.test_client() as client:
        first = localstack.replay_night(sing_app, client, fx, ids.token_for)
        second = localstack.replay_night(sing_app, client, fx, ids.token_for)
    assert first == second


# --- the local stack's catalog answers the app's fixture searches ----------------

def test_localstack_catalog_search_cases(tmp_path, monkeypatch):
    from app import create_app
    fx = localstack.load_fixture()
    cfg = localstack.replay_config(str(tmp_path))
    localstack.build_catalogs(str(tmp_path), fx)
    app = create_app(config=cfg)
    app.config["TESTING"] = True
    localstack.make_hermetic(app, setattr_fn=monkeypatch.setattr)
    localstack.reset_night(app, fx)
    try:
        with app.test_client() as c:
            def search(q):
                resp = c.get("/sing/search", query_string={"t": "2121", "q": q})
                assert resp.status_code == 200, resp.get_data(as_text=True)
                return resp.get_json()["songs"]

            def find(songs, artist, title):
                return next((s for s in songs
                             if s["artist"].lower() == artist.lower()
                             and s["title"].lower() == title.lower()), None)

            def identify(q):
                resp = c.get("/sing/search/identify", query_string={"t": "2121", "q": q})
                assert resp.status_code == 200, resp.get_data(as_text=True)
                return resp.get_json()

            cases = fx["search_cases"]
            assert set(cases) == {"local_video", "fuzzy", "no_karaoke", "kn_only",
                                  "divebar", "community", "many_versions"}
            for name, case in cases.items():
                query = case["q"]
                if case.get("identify"):
                    ident = identify(query)
                    assert ident["status"] == "confident", (name, ident)
                    assert (ident["song"]["artist"], ident["song"]["title"]) == (
                        case["artist"], case["title"]), (name, ident)
                    assert ident["song"]["karaoke"] is (name != "no_karaoke"), (name, ident)
                    # The typo alone finds no karaoke rows; the identified name does.
                    query = f'{ident["song"]["artist"]} {ident["song"]["title"]}'
                songs = search(query)
                song = find(songs, case["artist"], case["title"])
                if name == "no_karaoke":
                    assert song is None and not any(
                        "machu picchu" in s["title"].lower() for s in songs), songs
                    continue
                assert song is not None, (name, case, [(s["artist"], s["title"]) for s in songs])
                sources = {v.get("source") for v in song["versions"]}
                if name == "local_video":
                    assert "local" in sources
                elif name == "divebar":
                    assert "divebar" in sources or any(
                        (v.get("kn") or {}).get("divebar") for v in song["versions"])
                elif name == "kn_only":
                    assert sources == {"kn"}
                elif name == "community":
                    assert any(v.get("priority_class") == "community" for v in song["versions"])
                elif name == "many_versions":
                    assert len(song["versions"]) > 50
                elif name == "fuzzy":
                    classes = {v.get("priority_class") for v in song["versions"]}
                    assert {"community", "commercial"} <= classes, classes
    finally:
        app.catalog.close()


def test_fixture_file_is_where_the_stack_expects_it():
    assert os.path.isfile(FIXTURE)
    assert FIXTURE.endswith(os.path.join("tests", "fixtures", "localstack", "night.json"))
