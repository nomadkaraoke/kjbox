"""The local-stack test API (/sing/api/test/*) used by the mobile Maestro flows.

It must only exist when KJBOX_TEST_API=1 AND the server binds a loopback host
(A12-R2); everywhere else every path under it is a 404.
"""

import json

import pytest

import localstack
import sing_test_api

TEST = "/sing/api/test"


def _app(mock_config):
    from app import create_app
    app = create_app(config=mock_config)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def test_api_app(mock_config, monkeypatch):
    monkeypatch.setenv("KJBOX_TEST_API", "1")
    app = _app(mock_config)
    localstack.make_hermetic(app, setattr_fn=monkeypatch.setattr)
    assert sing_test_api.install_test_api(app, "127.0.0.1") is True
    yield app
    app.catalog.close()


@pytest.fixture
def tc(test_api_app):
    with test_api_app.test_client() as c:
        yield c


# --- mounting rules (A12-R2) ---------------------------------------------------

def test_test_api_absent_without_env(mock_config, monkeypatch):
    monkeypatch.delenv("KJBOX_TEST_API", raising=False)
    app = _app(mock_config)
    assert sing_test_api.install_test_api(app, "127.0.0.1") is False
    with app.test_client() as c:
        assert c.get(f"{TEST}/state").status_code == 404
        assert c.post(f"{TEST}/reset", json={}).status_code == 404
    app.catalog.close()

    # Any value other than "1" is off too.
    monkeypatch.setenv("KJBOX_TEST_API", "true")
    app = _app(mock_config)
    assert sing_test_api.install_test_api(app, "127.0.0.1") is False
    with app.test_client() as c:
        assert c.get(f"{TEST}/state").status_code == 404
    app.catalog.close()


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.8.10", "10.0.2.2",
                                  "nomadpc.local", "", None])
def test_test_api_refuses_non_loopback(mock_config, monkeypatch, host):
    monkeypatch.setenv("KJBOX_TEST_API", "1")
    app = _app(mock_config)
    assert sing_test_api.install_test_api(app, host) is False
    with app.test_client() as c:
        assert c.get(f"{TEST}/state").status_code == 404
        assert c.post(f"{TEST}/advance", json={}).status_code == 404
    app.catalog.close()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_test_api_mounts_on_loopback(mock_config, monkeypatch, host):
    monkeypatch.setenv("KJBOX_TEST_API", "1")
    app = _app(mock_config)
    assert sing_test_api.install_test_api(app, host) is True
    with app.test_client() as c:
        assert c.get(f"{TEST}/state").status_code == 200
    app.catalog.close()


@pytest.mark.parametrize("header", ["X-Forwarded-For", "CF-Connecting-IP"])
def test_test_api_refuses_proxied_requests(tc, header):
    assert tc.get(f"{TEST}/state").status_code == 200
    resp = tc.get(f"{TEST}/state", headers={header: "203.0.113.9"})
    assert resp.status_code == 404
    assert tc.post(f"{TEST}/reset", json={}, headers={header: "127.0.0.1"}).status_code == 404


def test_reset_reloads_the_servers_fixture(tc, test_api_app, tmp_path):
    fx = localstack.load_fixture()
    fx["start_rotation"] = fx["start_rotation"][:2]
    path = tmp_path / "night.json"
    path.write_text(json.dumps(fx))
    test_api_app.localstack_fixture_path = str(path)
    state = tc.post(f"{TEST}/reset", json={}).get_json()["state"]
    assert [e["singer"] for e in state["rotation"]] == [
        e["singer"] for e in fx["start_rotation"]]


def test_test_api_never_answers_the_public_host(tc, test_api_app):
    test_api_app.kj_config["sing_public_host"] = "sing.nomadkaraoke.com"
    assert tc.get(f"{TEST}/state").status_code == 200
    resp = tc.get(f"{TEST}/state", headers={"Host": "sing.nomadkaraoke.com"})
    assert resp.status_code == 404


# --- endpoints -------------------------------------------------------------

def test_reset_default_loads_fixture_night(tc, test_api_app):
    fixture = localstack.load_fixture()
    body = tc.post(f"{TEST}/reset", json={}).get_json()
    assert body["ok"] is True
    state = body["state"]
    assert state["event_code"] == "2121"
    assert test_api_app.sing_store.get_token() == "2121"
    assert [e["singer"] for e in state["rotation"]] == [
        e["singer"] for e in fixture["start_rotation"]]
    assert state["requests"] == [] and state["outbox"] == []
    night1 = state["night_id"]

    # A second reset starts a NEW night (the app must see night_changed).
    state2 = tc.post(f"{TEST}/reset", json={"fixture": "empty"}).get_json()["state"]
    assert state2["rotation"] == []
    assert state2["night_id"] != night1


def test_reset_rejects_unknown_fixture(tc):
    resp = tc.post(f"{TEST}/reset", json={"fixture": "nope"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid"


def test_seed_advance_done_undone(tc, test_api_app):
    tc.post(f"{TEST}/reset", json={"fixture": "empty"})
    seeded = tc.post(f"{TEST}/seed", json={"singers": [
        {"name": "Sam", "email": "sam.singer@example.com",
         "songs": [{"artist": "Adele", "title": "Hello", "source_type": "local"}]},
        {"name": "Kim", "songs": [{"artist": "Muse", "title": "Starlight"}]},
        {"name": "Lee", "device_id": "web-device-lee",
         "songs": [{"artist": "Queen", "title": "Bohemian Rhapsody", "source_type": "kn"}]},
    ]}).get_json()
    sam, kim, lee = seeded["singers"]
    secret = test_api_app.app_identity_secret
    from app_identity import app_device_id
    assert sam["device_id"] == app_device_id("sam.singer@example.com", secret)
    assert kim["device_id"] is None and kim["entries"][0]["request_id"] is None
    assert lee["device_id"] == "web-device-lee"
    store = test_api_app.sing_store
    req = store.get_request(sam["entries"][0]["request_id"])
    assert req["status"] == "approved" and req["device_id"] == sam["device_id"]
    assert req["linked_entry_id"] == sam["entries"][0]["entry_id"]
    assert store.get_alias(sam["device_id"]) == "Sam"
    night = store.get_night_started_at()
    assert store.account_for_device(sam["device_id"], night) == "sam.singer@example.com"

    adv = tc.post(f"{TEST}/advance", json={}).get_json()
    assert adv["done"] is None
    assert adv["now_singing"]["id"] == sam["entries"][0]["entry_id"]
    assert adv["up_next"]["id"] == kim["entries"][0]["entry_id"]
    # A play event is recorded, so the outbox gets a real sung_at.
    assert test_api_app.stats.first_play_started_at(adv["now_singing"]["id"]) is not None

    adv2 = tc.post(f"{TEST}/advance", json={}).get_json()
    assert adv2["done"]["id"] == sam["entries"][0]["entry_id"]
    assert adv2["now_singing"]["id"] == kim["entries"][0]["entry_id"]

    state = tc.get(f"{TEST}/state").get_json()
    statuses = {e["id"]: e["status"] for e in state["rotation"]}
    assert statuses[sam["entries"][0]["entry_id"]] == "Done"
    assert statuses[kim["entries"][0]["entry_id"]] == "Now Singing"
    assert statuses[lee["entries"][0]["entry_id"]] == "Up Next"

    done = tc.post(f"{TEST}/done", json={}).get_json()
    assert done["entry"]["id"] == kim["entries"][0]["entry_id"]
    assert done["entry"]["status"] == "Done"

    undone = tc.post(f"{TEST}/undone",
                     json={"entry_id": kim["entries"][0]["entry_id"]}).get_json()
    assert undone["entry"]["status"] == "Waiting"

    assert tc.post(f"{TEST}/undone", json={}).status_code == 400
    assert tc.post(f"{TEST}/undone", json={"entry_id": 999999}).status_code == 404


def test_done_with_nothing_on_stage_is_404(tc):
    tc.post(f"{TEST}/reset", json={"fixture": "empty"})
    resp = tc.post(f"{TEST}/done", json={})
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


def test_outbox_drain_sends_the_sung_record(tc, test_api_app, monkeypatch):
    sent = []

    class _Resp:
        status_code = 200

    def fake_put(url, json=None, headers=None, timeout=None):
        sent.append((url, json, headers))
        return _Resp()

    test_api_app.kj_config["decide_api_url"] = "http://127.0.0.1:8030"
    test_api_app.kj_config["decide_kjbox_secret"] = "local-stack-secret"
    monkeypatch.setattr(test_api_app.history_outbox, "_put", fake_put)

    tc.post(f"{TEST}/reset", json={"fixture": "empty"})
    tc.post(f"{TEST}/seed", json={"singers": [
        {"name": "Sam", "email": "sam.singer@example.com",
         "songs": [{"artist": "Adele", "title": "Hello"}]}]})
    tc.post(f"{TEST}/advance", json={})
    tc.post(f"{TEST}/done", json={})
    drained = tc.post(f"{TEST}/outbox/drain", json={}).get_json()
    assert drained["sent"] == 1
    url, payload, headers = sent[0]
    assert url.startswith("http://127.0.0.1:8030/api/partner/kjbox/karaoke-log/kj_")
    assert payload["gen_email"] == "sam.singer@example.com"
    assert (payload["artist"], payload["title"]) == ("Adele", "Hello")
    assert payload["state"] == "sung" and payload["sung_at"]
    assert headers["X-Kjbox-Secret"] == "local-stack-secret"
    state = tc.get(f"{TEST}/state").get_json()
    assert [(r["op"], r["state"]) for r in state["outbox"]] == [("put", "sent")]
    # Nothing left to send.
    assert tc.post(f"{TEST}/outbox/drain", json={}).get_json()["sent"] == 0


def test_flags(tc, test_api_app):
    store = test_api_app.sing_store
    body = tc.post(f"{TEST}/flags", json={
        "make": False, "simple_mode": True, "ask_photo_consent": True,
        "open": False, "auto_approve": False}).get_json()
    assert body["flags"] == {"make": False, "simple_mode": True,
                             "ask_photo_consent": True, "open": False,
                             "auto_approve": False, "auto_reorder": False}
    assert store.is_accepting_make_requests() is False
    assert store.is_simple_mode() is True
    assert store.get_footer_settings()["ask_photo_consent"] is True
    assert store.is_enabled() is False
    assert store.is_auto_approve() is False
    body = tc.post(f"{TEST}/flags", json={"open": True}).get_json()
    assert body["flags"]["open"] is True and body["flags"]["make"] is False
    assert tc.post(f"{TEST}/flags", json={"open": "yes"}).status_code == 400
    assert tc.post(f"{TEST}/flags", json={"bogus": True}).status_code == 400


def test_host_add_and_remove(tc, test_api_app):
    tc.post(f"{TEST}/reset", json={"fixture": "empty"})
    added = tc.post(f"{TEST}/host-add", json={
        "name": "Sam", "artist": "Adele", "title": "Hello"}).get_json()["entry"]
    assert added["singer"] == "Sam" and added["song_artist"] == "Hello - Adele"
    removed = tc.post(f"{TEST}/host-remove",
                      json={"entry_id": added["id"]}).get_json()["entry"]
    assert removed["status"] == "Cancelled"
    deleted = tc.post(f"{TEST}/host-remove",
                      json={"entry_id": added["id"], "delete": True}).get_json()
    assert deleted["deleted"] is True
    assert test_api_app.rotation.store.get_entry(added["id"]) is None
    assert tc.post(f"{TEST}/host-add", json={"name": "Sam"}).status_code == 400


def test_rate_limits(tc, test_api_app):
    import sing
    sing._rate_limit_state["x"] = [1.0]
    body = tc.post(f"{TEST}/rate-limits",
                   json={"per_device": 2, "per_ip": 3}).get_json()
    assert body["rate_limits"] == {"per_device": 2, "per_ip": 3}
    assert test_api_app.kj_config["sing_rate_limit_per_device"] == 2
    assert test_api_app.kj_config["sing_rate_limit_per_ip"] == 3
    assert not sing._rate_limit_state
    assert tc.post(f"{TEST}/rate-limits", json={"per_device": 0}).status_code == 400


def test_state_shape(tc):
    tc.post(f"{TEST}/reset", json={})
    state = tc.get(f"{TEST}/state").get_json()
    assert set(state) >= {"event_code", "night_id", "night_started_at", "rev",
                          "flags", "rotation", "requests", "outbox", "pushes"}
    entry = state["rotation"][0]
    assert set(entry) >= {"id", "singer", "singers", "song_artist", "status",
                          "position"}


def test_push_sends_are_captured_never_sent(test_api_app):
    disp = test_api_app.rotation.push_dispatcher
    disp._send_expo({"id": 1}, {"to": "ExponentPushToken[x]"})
    disp._send({"id": 2, "endpoint": "https://push.example/x"}, {"a": 1}, {})
    assert [p["kind"] for p in test_api_app.localstack_pushes] == ["expo", "web"]
    assert test_api_app.sms_config["api_key"] is None


def test_push_outbox_get_and_delete(test_api_app, tc):
    disp = test_api_app.rotation.push_dispatcher
    assert tc.get(f"{TEST}/push-outbox").get_json() == {"pushes": []}
    disp._send_expo({"id": 1}, {"to": "ExponentPushToken[x]", "data": {"step": "up_next"}})
    pushes = tc.get(f"{TEST}/push-outbox").get_json()["pushes"]
    assert [p["kind"] for p in pushes] == ["expo"]
    assert pushes[0]["message"]["data"]["step"] == "up_next"
    assert tc.delete(f"{TEST}/push-outbox").get_json() == {"ok": True}
    assert tc.get(f"{TEST}/push-outbox").get_json() == {"pushes": []}


def test_push_outbox_absent_without_test_api(mock_config, monkeypatch):
    monkeypatch.delenv("KJBOX_TEST_API", raising=False)
    app = _app(mock_config)
    sing_test_api.install_test_api(app, "127.0.0.1")
    with app.test_client() as c:
        assert c.get(f"{TEST}/push-outbox").status_code == 404
        assert c.delete(f"{TEST}/push-outbox").status_code == 404
    app.catalog.close()


# --- past nights for the claim flows (Phase 4c E8) ---------------------------------

HIST_WREN = "e2e-me-13-1@example.com"
HIST_JUNO = "e2e-me-13-2@example.com"


def _history_verifier(app, monkeypatch, users):
    from app_identity import GenTokenVerifier

    class _Gen:
        def get_me(self, session_token):
            from gen_client import GenApiError
            if session_token not in users:
                raise GenApiError(401, "Invalid session")
            return {"user": dict(users[session_token]), "has_session": True}

    monkeypatch.setattr(app, "gen_verifier",
                        GenTokenVerifier(_Gen(), secret=app.app_identity_secret), raising=False)


def _seed_body():
    return {
        "nights": [
            {"date": "2026-10-08", "entries": [
                {"ref": "w1", "singer": "Wren B", "artist": "Muse", "title": "Starlight",
                 "link": "exact", "played": True},
                {"ref": "w2", "singer": "Wren", "artist": "Adele", "title": "Hello",
                 "link": "inferred"},
                {"ref": "w3", "singer": "Wren B", "artist": "Muse", "title": "Uprising",
                 "link": "none"},
                {"ref": "j1", "singer": "Juno", "artist": "Adele", "title": "Hello",
                 "link": "exact", "claimed_by": HIST_JUNO},
                {"ref": "d1", "singer": "Juno & Wren B", "singers": ["Juno", "Wren B"],
                 "artist": "Muse", "title": "Madness", "link": "exact"},
                {"ref": "x1", "singer": "Wren", "artist": "Muse", "title": "Hysteria",
                 "link": "exact", "status": "Left"},
            ]},
            {"date": "2026-05-14", "entries": [
                {"ref": "w4", "singer": "Wren", "artist": "Adele", "title": "Skyfall",
                 "link": "exact"},
            ]},
        ],
        "identity": {"email": HIST_WREN, "names": ["Wren B"], "date": "2026-10-08"},
    }


def test_history_seed_suggest_claim_state_reset(tc, test_api_app, monkeypatch):
    from tests.integration.v1_contract import V1, assert_matches_contract, bearer
    _history_verifier(test_api_app, monkeypatch,
                      {"tok-wren": {"email": HIST_WREN, "display_name": None,
                                    "tenant_id": None}})
    tc.post(f"{TEST}/reset", json={"fixture": "empty"})
    seeded = tc.post(f"{TEST}/history/seed", json=_seed_body())
    assert seeded.status_code == 200, seeded.get_json()
    nights = {n["date"]: n for n in seeded.get_json()["nights"]}
    assert set(nights) == {"2026-10-08", "2026-05-14"}
    refs = nights["2026-10-08"]["entries"]
    assert set(refs) == {"w1", "w2", "w3", "j1", "d1", "x1"}
    # Ids sit above the live rotation's, and the live rotation continues above them.
    live = test_api_app.rotation.add_entry("Live Singer", "Song - Band")
    assert live["id"] > max(refs.values())

    resp = tc.get(f"{V1}/history/suggestions", headers=bearer("tok-wren"))
    body = assert_matches_contract(resp, "getHistorySuggestions")
    assert resp.status_code == 200, body
    got = {n["date"]: n for n in body["nights"]}
    oct8 = got["2026-10-08"]
    assert oct8["night_id"] == nights["2026-10-08"]["night_id"]
    assert [s["entry_id"] for s in oct8["songs"]] == [refs["w1"], refs["w2"], refs["d1"]]
    assert oct8["unmatched"] == 1                       # w3: no entry id
    assert oct8["songs"][0]["state"] == "sung" and oct8["songs"][0]["started_at"]
    assert (oct8["songs"][0]["artist"], oct8["songs"][0]["title"]) == ("Muse", "Starlight")
    assert [s["entry_id"] for s in got["2026-05-14"]["songs"]] == [
        nights["2026-05-14"]["entries"]["w4"]]

    resp = tc.post(f"{V1}/history/claim", headers=bearer("tok-wren"), json={
        "night_id": oct8["night_id"], "mine": [refs["w1"]], "not_mine": [refs["w2"]]})
    assert assert_matches_contract(resp, "claimHistory")["saved"] == 1

    state = tc.get(f"{TEST}/history/state", query_string={"email": HIST_WREN}).get_json()
    assert [(c["night"], c["entry_id"]) for c in state["claims"]] == [("2026-10-08", refs["w1"])]
    assert state["rejections"] == [{"night": "2026-10-08", "entry_id": refs["w2"]}]
    [row] = state["outbox"]
    assert (row["op"], row["state"], row["entry_id"], row["gen_email"]) == (
        "put", "pending", str(refs["w1"]), HIST_WREN)
    everyone = tc.get(f"{TEST}/history/state").get_json()
    assert {c["gen_email"] for c in everyone["claims"]} == {HIST_WREN, HIST_JUNO}
    juno = tc.get(f"{TEST}/history/state", query_string={"email": HIST_JUNO}).get_json()
    assert juno["rejections"] == [] and juno["outbox"] == []

    tc.post(f"{TEST}/reset", json={"fixture": "empty"})
    assert tc.get(f"{TEST}/history/state").get_json() == {
        "claims": [], "rejections": [], "outbox": []}
    resp = tc.get(f"{V1}/history/suggestions", headers=bearer("tok-wren"))
    assert resp.get_json()["nights"] == []


@pytest.mark.parametrize("body", [
    {},
    {"nights": []},
    {"nights": [{"date": "2026-13-45", "entries": [
        {"ref": "a", "singer": "Wren", "title": "Hello", "link": "exact"}]}]},
    {"nights": [{"date": "2026-10-08", "entries": []}]},
    {"nights": [{"date": "2026-10-08", "entries": [
        {"ref": "a", "singer": "Wren", "title": "Hello", "link": "sideways"}]}]},
    {"nights": [{"date": "2026-10-08", "entries": [
        {"ref": "a", "singer": "", "title": "Hello", "link": "exact"}]}]},
    {"nights": [{"date": "2026-10-08", "entries": [
        {"ref": "a", "singer": "Wren", "title": "Hello"},
        {"ref": "a", "singer": "Wren", "title": "Hello"}]}]},
    {"nights": [{"date": "2026-10-08", "entries": [
        {"ref": "a", "singer": "Wren", "title": "Hello"}]}],
     "identity": {"email": "", "names": ["Wren"], "date": "2026-10-08"}},
])
def test_history_seed_validation(tc, body):
    resp = tc.post(f"{TEST}/history/seed", json=body)
    assert resp.status_code == 400, resp.get_json()
    assert resp.get_json()["error"] == "invalid"
