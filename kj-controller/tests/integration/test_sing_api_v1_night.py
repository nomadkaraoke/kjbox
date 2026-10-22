"""App-facing singer API v1: auth, night scoping, GET /night, POST /join.

Every response is checked against docs/api/singer-v1.openapi.json.
"""

import hashlib
import json
import re

import pytest

from app_identity import app_device_id
from tests.integration.v1_contract import (
    V1, assert_matches_contract, assert_v1_error, bearer, validate_body,
)

PUBLIC_HOST = "sing.nomadkaraoke.com"


def _night(client, tok, t, **params):
    return client.get(f"{V1}/night", query_string={"t": t, **params}, headers=bearer(tok))


# --- auth ordering -------------------------------------------------------

def test_v1_requires_bearer(client, token):
    resp = client.get(f"{V1}/night", query_string={"t": token})
    assert resp.status_code == 401
    body = assert_matches_contract(resp, "getNight")
    assert body["error"] == "unauthenticated"
    assert body["api_version"] == "1.0"

    resp = client.post(f"{V1}/join", json={"t": token, "name": "Sam"},
                       headers={"Authorization": "Basic abc"})
    assert resp.status_code == 401
    assert assert_matches_contract(resp, "joinNight")["error"] == "unauthenticated"


def test_v1_rejects_unknown_gen_token(client, token, gen_stub):
    resp = _night(client, "not-a-real-session", token)
    assert resp.status_code == 401
    assert assert_matches_contract(resp, "getNight")["error"] == "unauthenticated"


def test_v1_gen_down_503(client, token, app_token, gen_stub):
    tok = app_token()
    gen_stub.down = True
    resp = _night(client, tok, token)
    assert resp.status_code == 503
    body = assert_matches_contract(resp, "getNight")
    assert body["error"] == "gen_unavailable"


def test_v1_gen_not_configured_503(client, token, sing_app):
    sing_app.gen_verifier = None
    resp = _night(client, "anything", token)
    assert resp.status_code == 503
    assert assert_matches_contract(resp, "getNight")["error"] == "gen_unavailable"


def test_v1_bad_event_code_403_not_open(client, token, app_token, gen_stub):
    tok = app_token()
    resp = client.post(f"{V1}/join", json={"t": "0000", "name": "Sam"}, headers=bearer(tok))
    assert resp.status_code == 403
    assert assert_matches_contract(resp, "joinNight")["error"] == "not_open"
    # The code is checked before gen is asked about the token.
    assert gen_stub.calls == []

    # GET /night shows the closed state instead of an error.
    resp = _night(client, tok, "0000")
    assert resp.status_code == 200
    body = assert_matches_contract(resp, "getNight")
    assert body["open"] is False
    assert body["error"] == "not_open"
    assert body["reason"] == "bad_code"


def test_v1_night_closed_shows_host_message(client, token, app_token, sing_app):
    sing_app.sing_store.set_footer_settings({"message": "Back next Thursday!"})
    sing_app.sing_store.set_tip_settings({"kj_name": "Andrew"})
    sing_app.sing_store.set_enabled(False)
    tok = app_token()
    resp = _night(client, tok, token)
    assert resp.status_code == 200
    body = assert_matches_contract(resp, "getNight")
    assert body["open"] is False
    assert body["reason"] == "closed"
    assert body["footer_message"] == "Back next Thursday!"
    assert body["kj_name"] == "Andrew"

    resp = client.post(f"{V1}/join", json={"t": token, "name": "Sam"}, headers=bearer(tok))
    assert resp.status_code == 403
    assert assert_matches_contract(resp, "joinNight")["error"] == "not_open"


def test_v1_event_code_from_body_not_session_cookie(client, token, app_token):
    tok = app_token()
    resp = client.post(f"{V1}/join", json={"t": token, "name": "Sam"}, headers=bearer(tok))
    assert resp.status_code == 200
    # v1 never sets the Flask session cookie...
    assert "Set-Cookie" not in resp.headers
    # ...and doesn't fall back to one: a later call without t is not open.
    client.get(f"/sing/?t={token}")  # web page sets session["sing_token"]
    resp = client.post(f"{V1}/join", json={"name": "Sam"}, headers=bearer(tok))
    assert resp.status_code == 403


def test_v1_fails_closed_without_night_marker(client, token, app_token, sing_app):
    tok = app_token()
    conn = sing_app.sing_store._get_conn()
    conn.execute("DELETE FROM rotation_meta WHERE key = 'night_started_at'")
    conn.commit()
    assert sing_app.sing_store.get_night_started_at() is None
    resp = client.post(f"{V1}/join", json={"t": token, "name": "Sam"}, headers=bearer(tok))
    assert resp.status_code == 403
    assert assert_matches_contract(resp, "joinNight")["error"] == "not_open"
    resp = _night(client, tok, token)
    assert resp.status_code == 200
    assert assert_matches_contract(resp, "getNight")["open"] is False


# --- GET /night ----------------------------------------------------------

def test_v1_night_payload_matches_contract(client, token, app_token, sing_app):
    store = sing_app.sing_store
    store.set_footer_settings({
        "message": "Tip jar by the bar",
        "notices": ["chargers"],
        "social": {"instagram": "https://instagram.com/nomad", "email": "hi@example.com"},
        "ask_photo_consent": True,
    })
    tok = app_token()
    resp = _night(client, tok, token)
    assert resp.status_code == 200
    body = assert_matches_contract(resp, "getNight")
    assert body["api_version"] == "1.0"
    assert body["open"] is True
    night = store.get_night_started_at()
    expected = hashlib.sha256(f"nomadpc:{night}".encode()).hexdigest()[:16]
    assert body["night_id"] == expected
    assert body["footer_message"] == "Tip jar by the bar"
    assert body["notices"] == ["chargers"]
    assert body["social"] == {"instagram": "https://instagram.com/nomad"}
    assert body["contact"] == {"email": "hi@example.com"}
    assert body["features"]["ask_photo_consent"] is True
    assert set(body["features"]) == {"make", "simple_mode", "ask_photo_consent",
                                     "quick_version", "tips"}
    assert isinstance(body["rev"], int)
    assert resp.headers.get("Cache-Control") == "no-store"


def test_v1_night_id_uses_box_id(client, token, app_token, sing_app):
    sing_app.kj_config["box_id"] = "nomadpi"
    body = _night(client, app_token(), token).get_json()
    night = sing_app.sing_store.get_night_started_at()
    assert body["night_id"] == hashlib.sha256(f"nomadpi:{night}".encode()).hexdigest()[:16]


def test_v1_night_changed_returns_409(client, token, app_token, sing_app):
    tok = app_token()
    current = _night(client, tok, token).get_json()["night_id"]
    # Matching night_id passes (query and JSON body).
    assert _night(client, tok, token, night_id=current).status_code == 200
    # The box starts a new night with the same event code.
    conn = sing_app.sing_store._get_conn()
    conn.execute("UPDATE rotation_meta SET value = '2099-01-01 18:00:00' "
                 "WHERE key = 'night_started_at'")
    conn.commit()
    resp = _night(client, tok, token, night_id=current)
    assert resp.status_code == 409
    body = assert_matches_contract(resp, "getNight")
    assert body["error"] == "night_changed"
    assert body["night_id"] != current
    assert body["night_id"] == _night(client, tok, token).get_json()["night_id"]

    resp = client.post(f"{V1}/join", json={"t": token, "name": "Sam", "night_id": current},
                       headers=bearer(tok))
    assert resp.status_code == 409
    assert assert_matches_contract(resp, "joinNight")["error"] == "night_changed"
    # Nothing was written for the stale night.
    assert sing_app.sing_store.get_alias(app_device_id("sam.singer@example.com")) is None


# --- POST /join / needs_name --------------------------------------------

def test_needs_name_until_join(client, token, app_token):
    tok = app_token(display_name="Samantha Jones")
    body = _night(client, tok, token).get_json()
    assert body["needs_name"] is True
    assert body["suggested_name"] == "Samantha"
    assert "me" not in body

    resp = client.post(f"{V1}/join", json={"t": token, "name": "Sam J"}, headers=bearer(tok))
    assert resp.status_code == 200
    assert assert_matches_contract(resp, "joinNight")["me"] == {"name": "Sam J"}

    resp = _night(client, tok, token)
    body = assert_matches_contract(resp, "getNight")
    assert body["needs_name"] is False
    assert body["me"] == {"name": "Sam J"}
    assert "suggested_name" not in body


def test_needs_name_without_suggestion(client, token, app_token, monkeypatch):
    import sing_api_v1
    monkeypatch.setattr(sing_api_v1, "suggested_name", lambda user: "")
    body = _night(client, app_token(display_name=None), token).get_json()
    assert body["needs_name"] is True
    assert body["suggested_name"] is None


def test_join_sets_alias_and_links_account(client, token, app_token, sing_app):
    store = sing_app.sing_store
    tok = app_token(email="Sam.Singer@Example.com")
    resp = client.post(f"{V1}/join", json={"t": token, "name": "  Sam  "}, headers=bearer(tok))
    assert resp.status_code == 200
    body = assert_matches_contract(resp, "joinNight")
    assert body == {"api_version": "1.0", "me": {"name": "Sam"}}
    dev = app_device_id("sam.singer@example.com")
    assert store.get_alias(dev) == "Sam"
    row = store._get_conn().execute(
        "SELECT origin FROM singer_aliases WHERE device_id = ?", (dev,)).fetchone()
    assert row[0] == "self"
    night = store.get_night_started_at()
    assert store.account_for_device(dev, night) == "sam.singer@example.com"
    assert store.account_devices("sam.singer@example.com", night) == [dev]
    rec = store._get_conn().execute(
        "SELECT origin FROM singer_accounts WHERE device_id = ?", (dev,)).fetchone()
    assert rec[0] == "app"


@pytest.mark.parametrize("name", ["", "   ", "x" * 101, 42])
def test_join_rejects_bad_name(client, token, app_token, name):
    resp = client.post(f"{V1}/join", json={"t": token, "name": name},
                       headers=bearer(app_token()))
    assert resp.status_code == 400
    assert assert_matches_contract(resp, "joinNight")["error"] == "invalid"


def test_any_v1_call_links_account_once(client, token, app_token, sing_app, monkeypatch):
    store = sing_app.sing_store
    calls = []
    real = store.link_account
    monkeypatch.setattr(store, "link_account",
                        lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    tok = app_token()
    _night(client, tok, token)
    _night(client, tok, token)
    assert len(calls) == 1
    dev = app_device_id("sam.singer@example.com")
    assert store.account_for_device(dev, store.get_night_started_at()) == "sam.singer@example.com"


def test_app_auth_sets_request_context(sing_app, token, app_token):
    """The decorator's g contract that later endpoints rely on."""
    from flask import g, jsonify

    import sing_api_v1

    seen = {}

    @sing_api_v1.app_auth
    def probe():
        seen.update(
            user=g.app_user, night=g.night_started_at, night_id=g.night_id,
            devices=list(g.account_devices), gen=dict(g.app_gen_account),
        )
        return jsonify({})

    tok = app_token()
    with sing_app.test_request_context(
            f"{V1}/night?t={token}", headers=bearer(tok)):
        probe()
    store = sing_app.sing_store
    dev = app_device_id("sam.singer@example.com")
    assert seen["user"].device_id == dev
    assert seen["night"] == store.get_night_started_at()
    assert seen["night_id"] == sing_api_v1.night_id_for(store, sing_app.kj_config)
    assert seen["devices"] == [dev]
    assert seen["gen"] == {"email": "sam.singer@example.com", "session_token": tok}


def test_night_id_for_none_without_marker(sing_app):
    import sing_api_v1
    conn = sing_app.sing_store._get_conn()
    conn.execute("DELETE FROM rotation_meta WHERE key = 'night_started_at'")
    conn.commit()
    assert sing_api_v1.night_id_for(sing_app.sing_store, sing_app.kj_config) is None


# --- public host + ActionRecorder ---------------------------------------

@pytest.fixture
def public_app(mock_config):
    from app import create_app
    mock_config["sing_public_host"] = PUBLIC_HOST
    app = create_app(config=mock_config)
    app.config["TESTING"] = True
    yield app
    app.catalog.close()


def test_v1_reachable_on_public_host(public_app, monkeypatch):
    import app_identity

    class _Gen:
        def get_me(self, session_token):
            return {"has_session": True,
                    "user": {"email": "pub@example.com", "display_name": "Pub", "tenant_id": None}}

    public_app.gen_verifier = app_identity.GenTokenVerifier(_Gen())
    token = public_app.sing_store.ensure_token()
    with public_app.test_client() as c:
        resp = c.get(f"/api/v1/night?t={token}", headers={"Host": PUBLIC_HOST, **bearer("x")})
        assert resp.status_code == 200
        assert assert_matches_contract(resp, "getNight")["open"] is True
        # Also reachable on the internal /sing prefix via the public host.
        resp = c.get(f"/sing/api/v1/night?t={token}",
                     headers={"Host": PUBLIC_HOST, **bearer("x")})
        assert resp.status_code == 200
        # KJ-only routes stay blocked.
        assert c.get("/rotation/requests", headers={"Host": PUBLIC_HOST}).status_code == 404


@pytest.fixture
def rec_app(mock_config, tmp_path):
    from app import create_app
    mock_config["action_log_dir"] = str(tmp_path / "action-logs")
    mock_config["action_log_enabled"] = True
    app = create_app(config=mock_config)
    app.config["TESTING"] = True
    yield app
    app.catalog.close()


def test_authorization_header_redacted_in_action_log(rec_app):
    import app_identity

    class _Gen:
        def get_me(self, session_token):
            return {"has_session": True,
                    "user": {"email": "rec@example.com", "display_name": "Rec", "tenant_id": None}}

    rec_app.gen_verifier = app_identity.GenTokenVerifier(_Gen())
    token = rec_app.sing_store.ensure_token()
    secret = "SUPER-SECRET-GEN-SESSION-abc123"
    with rec_app.test_client() as c:
        resp = c.post(f"{V1}/join", json={"t": token, "name": "Rec"},
                      headers={**bearer(secret), "User-Agent": "NomadApp/1.0"})
        assert resp.status_code == 200
        # A token pasted into the body or query is redacted too.
        c.post(f"{V1}/join?session_token={secret}",
               json={"t": token, "name": "Rec", "authorization": f"Bearer {secret}"},
               headers=bearer(secret))
    path = rec_app.action_recorder.path_for()
    with open(path) as fh:
        raw = fh.read()
    assert secret not in raw
    recs = [json.loads(line) for line in raw.splitlines()]
    joins = [r for r in recs if r["path"] == f"{V1}/join"]
    assert len(joins) == 2
    assert joins[0]["actor"] == "app"
    assert joins[0]["client"]["Authorization"] == "[redacted]"
    assert joins[0]["client"]["User-Agent"] == "NomadApp/1.0"
    assert joins[1]["body"]["authorization"] == "[redacted]"
    assert joins[1]["query"]["session_token"] == "[redacted]"


# --- the contract file itself -------------------------------------------

V1_ERROR_CODES = [
    "unauthenticated", "gen_unavailable", "not_open", "night_changed", "needs_name",
    "not_found", "not_owner", "already_sung", "on_stage", "rate_limited", "invalid",
    "kj_pick_too_large", "make_disabled", "youtube_blocked", "gen_rejected",
    "already_generating", "busy_retry", "conflict", "internal",
]


def test_contract_is_well_formed():
    import jsonschema

    from tests.integration.v1_contract import load_contract
    doc = load_contract()
    assert doc["openapi"].startswith("3.1")
    assert doc["components"]["schemas"]["ErrorCode"]["enum"] == V1_ERROR_CODES
    op_ids = [op["operationId"] for item in doc["paths"].values()
              for op in item.values() if isinstance(op, dict)]
    assert len(op_ids) == len(set(op_ids)) and all(op_ids)
    for name, schema in doc["components"]["schemas"].items():
        jsonschema.Draft202012Validator.check_schema(schema)


def test_every_v1_route_is_in_the_contract(sing_app):
    from tests.integration.v1_contract import load_contract
    paths = load_contract()["paths"]
    for rule in sing_app.url_map.iter_rules():
        if not rule.endpoint.startswith("app_api."):
            continue
        # Flask "<int:id>" -> OpenAPI "{id}"
        rel = re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", rule.rule[len(V1):])
        assert rel in paths, f"{rule.rule} missing from the contract"
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            assert method.lower() in paths[rel], f"{method} {rule.rule} missing"



def test_contract_harness_is_strict():
    import jsonschema
    ok = {"api_version": "1.0", "me": {"name": "Sam"}}
    validate_body(ok, {"$ref": "#/components/schemas/JoinResponse"})
    for bad in ({**ok, "surprise": 1}, {**ok, "me": {"name": "Sam", "surprise": 1}}):
        with pytest.raises(jsonschema.ValidationError):
            validate_body(bad, {"$ref": "#/components/schemas/JoinResponse"})
    # Error declares night_id; NightChangedError requires it.
    validate_body({"api_version": "1.0", "error": "night_changed", "night_id": "ab"},
                  {"$ref": "#/components/schemas/NightChangedError"})
    with pytest.raises(jsonschema.ValidationError):
        validate_body({"api_version": "1.0", "error": "night_changed"},
                      {"$ref": "#/components/schemas/NightChangedError"})


def test_missing_bearer_with_bad_code_is_401(client, token):
    resp = client.get(f"{V1}/night", query_string={"t": "0000"})
    assert resp.status_code == 401
    assert assert_matches_contract(resp, "getNight")["error"] == "unauthenticated"


def test_gen_stub_leaves_gen_client_alone(sing_app, gen_stub):
    assert sing_app.gen_client is None
    assert sing_app.gen_verifier is not None


# --- JSON errors on v1 paths ---------------------------------------------

def test_v1_unknown_path_404_json(client):
    assert_v1_error(client.get(f"{V1}/no-such-thing"), "not_found", 404)
    # Other paths keep Flask's HTML 404.
    resp = client.get("/no-such-thing")
    assert resp.status_code == 404 and resp.mimetype != "application/json"


def test_v1_wrong_method_405_json(client):
    assert_v1_error(client.get(f"{V1}/join"), "invalid", 405)
    assert_v1_error(client.delete(f"{V1}/night"), "invalid", 405)


def test_v1_unknown_path_on_public_host_is_json(public_app):
    with public_app.test_client() as c:
        assert_v1_error(c.get("/api/v1/nope", headers={"Host": PUBLIC_HOST}), "not_found", 404)
        assert_v1_error(c.get("/sing/api/v1/nope", headers={"Host": PUBLIC_HOST}),
                        "not_found", 404)
        # Non-v1 guard 404s on the public host are unchanged.
        resp = c.get("/rotation/requests", headers={"Host": PUBLIC_HOST})
        assert resp.status_code == 404 and resp.mimetype != "application/json"


def test_v1_unhandled_exception_500_internal(client, token, app_token, monkeypatch, caplog):
    import logging

    import sing_api_v1

    def boom(user):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(sing_api_v1, "suggested_name", boom)
    tok = app_token()
    with caplog.at_level(logging.ERROR, logger="sing_api_v1"):
        resp = _night(client, tok, token)
    assert resp.status_code == 500
    body = assert_matches_contract(resp, "getNight")
    assert body["error"] == "internal"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and errors[0].exc_info and "kaboom" in caplog.text
    assert tok not in caplog.text
    assert "Authorization" not in caplog.text


# --- per-IP limits (A3-R5) ----------------------------------------------

def _from(ip):
    return {"environ_base": {"REMOTE_ADDR": ip}}


def test_failed_event_codes_rate_limited_per_ip(client, token, app_token):
    tok = app_token()
    for _ in range(10):
        resp = client.post(f"{V1}/join", json={"t": "0000", "name": "Sam"},
                           headers=bearer(tok), **_from("198.51.100.7"))
        assert resp.status_code == 403
    # Over the limit even the right code is refused (no oracle), on any endpoint.
    resp = client.post(f"{V1}/join", json={"t": token, "name": "Sam"},
                       headers=bearer(tok), **_from("198.51.100.7"))
    assert resp.status_code == 429
    body = assert_matches_contract(resp, "joinNight")
    assert body["error"] == "rate_limited"
    assert 1 <= body["retry_after"] <= 300
    assert resp.headers["Retry-After"] == str(body["retry_after"])
    resp = client.get(f"{V1}/night", query_string={"t": token}, headers=bearer(tok),
                      **_from("198.51.100.7"))
    assert resp.status_code == 429
    assert assert_matches_contract(resp, "getNight")["error"] == "rate_limited"
    # Another IP is unaffected; successful codes never count.
    for _ in range(12):
        resp = client.get(f"{V1}/night", query_string={"t": token}, headers=bearer(tok),
                          **_from("198.51.100.8"))
        assert resp.status_code == 200


def test_gen_verification_misses_rate_limited_per_ip(client, token, app_token, gen_stub):
    good = app_token()
    ip = _from("198.51.100.9")
    assert _night(client, good, token, **{}).status_code == 200  # other IP; warms cache
    for i in range(30):
        resp = client.get(f"{V1}/night", query_string={"t": token},
                          headers=bearer(f"junk-{i}"), **ip)
        assert resp.status_code == 401
    calls = len(gen_stub.calls)
    resp = client.get(f"{V1}/night", query_string={"t": token},
                      headers=bearer("junk-new"), **ip)
    assert resp.status_code == 429
    body = assert_matches_contract(resp, "getNight")
    assert body["error"] == "rate_limited" and 1 <= body["retry_after"] <= 60
    assert len(gen_stub.calls) == calls  # gen wasn't asked
    # A token already in the verifier cache still works from that IP.
    resp = client.get(f"{V1}/night", query_string={"t": token}, headers=bearer(good), **ip)
    assert resp.status_code == 200


def test_sliding_window_expires():
    import sing_api_v1
    now = [100.0]
    w = sing_api_v1.SlidingWindow(2, 60, clock=lambda: now[0])
    w.hit("a")
    w.hit("a")
    assert w.retry_after("a") == 60
    assert w.retry_after("b") is None
    now[0] += 59.5
    assert w.retry_after("a") == 1
    now[0] += 0.5
    assert w.retry_after("a") is None


def test_malformed_v1_body_recorded_as_length_only(rec_app):
    token = rec_app.sing_store.ensure_token()
    secret = "SUPER-SECRET-IN-BROKEN-JSON"
    raw = '{"t": "%s", "session_token": "%s"' % (token, secret)  # no closing brace
    with rec_app.test_client() as c:
        c.post(f"{V1}/join", data=raw, content_type="application/json",
               headers=bearer("x"))
    with open(rec_app.action_recorder.path_for()) as fh:
        text = fh.read()
    assert secret not in text
    (rec,) = [json.loads(line) for line in text.splitlines()
              if json.loads(line)["path"] == f"{V1}/join"]
    assert rec["body"] == {"__unparsed_bytes__": len(raw)}
