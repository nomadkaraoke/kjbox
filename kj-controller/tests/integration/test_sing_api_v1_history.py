"""App-facing singer API v1: GET /history/suggestions and POST /history/claim
(Phase 4c E6). These need only the gen bearer (R29): no event code, no open
night, and they never link the device to tonight. Every response is checked
against docs/api/singer-v1.openapi.json. Fixture singers and emails are
invented."""

import logging

import pytest

import night_log
import sing_api_v1
from history_ids import history_id_for
from tests.integration.v1_contract import V1, assert_matches_contract, bearer
from tests.unit.night_log_helpers import _exact_archive, _play, _utc_of_local

WREN = "e2e-history-wren@example.com"
JUNO = "e2e-history-juno@example.com"
NIGHT = "2026-10-20"


def _expect(resp, op, status, error=None):
    assert resp.status_code == status, resp.get_json()
    body = assert_matches_contract(resp, op)
    if error is not None:
        assert body["error"] == error
    return body


@pytest.fixture(autouse=True)
def _fresh_history_limits():
    for lim in (sing_api_v1.history_read_limit, sing_api_v1.history_claim_limit):
        lim.clear()
    night_log.clear_build_cache()
    yield
    for lim in (sing_api_v1.history_read_limit, sing_api_v1.history_claim_limit):
        lim.clear()


@pytest.fixture
def past_night(sing_app):
    rs = sing_app.rotation.store
    _exact_archive(rs, 5012, "Wren B", "Starlight - Muse", f"{NIGHT} 21:40:00",
                   f"{NIGHT} 22:10:00", file_path="/m/x [lib-star1].mp4")
    _play(sing_app.stats, "lib-star1", 5012, "Wren B", _utc_of_local(f"{NIGHT} 22:06:00"),
          artist="Muse", title="Starlight")
    _exact_archive(rs, 5013, "Wren", "Hello - Adele", f"{NIGHT} 22:20:00",
                   f"{NIGHT} 22:30:00")
    _exact_archive(rs, 5014, "Juno", "Uprising - Muse", f"{NIGHT} 22:40:00",
                   f"{NIGHT} 22:45:00")
    return NIGHT


def _suggest(client, tok):
    return _expect(client.get(f"{V1}/history/suggestions", headers=bearer(tok)),
                   "getHistorySuggestions", 200)


def _claim(client, tok, **body):
    return client.post(f"{V1}/history/claim", json=body, headers=bearer(tok))


# --- GET /history/suggestions ----------------------------------------------------

def test_suggestions_requires_bearer_401(client, gen_stub, past_night):
    _expect(client.get(f"{V1}/history/suggestions"), "getHistorySuggestions", 401,
            "unauthenticated")
    _expect(client.get(f"{V1}/history/suggestions", headers=bearer("nope")),
            "getHistorySuggestions", 401, "unauthenticated")


def test_suggestions_gen_down_503(client, gen_stub, app_token, past_night):
    tok = app_token(WREN, "Wren B")
    gen_stub.down = True
    _expect(client.get(f"{V1}/history/suggestions", headers=bearer(tok)),
            "getHistorySuggestions", 503, "gen_unavailable")


def test_suggestions_works_with_requests_closed_and_no_code(sing_app, client, app_token,
                                                             past_night):
    sing_app.sing_store.set_enabled(False)
    tok = app_token(WREN, "Wren B")
    body = _suggest(client, tok)
    [night] = body["nights"]
    assert night["date"] == NIGHT
    assert [s["entry_id"] for s in night["songs"]] == [5012, 5013]
    assert night["songs"][0]["started_at"] == (
        _utc_of_local(f"{NIGHT} 22:06:00").replace(" ", "T") + "Z")
    assert body["more_nights"] == 0


def test_suggestions_without_names_is_empty(client, app_token, past_night):
    tok = app_token(WREN, None)  # no gen display name, no box history
    assert _suggest(client, tok) == {"api_version": sing_api_v1.API_VERSION,
                                     "nights": [], "more_nights": 0}


def test_suggestions_never_links_device_or_touches_tonight(sing_app, client, app_token,
                                                           past_night):
    store = sing_app.sing_store
    conn = store._get_conn()
    marker = store.get_night_started_at()
    before = conn.execute("SELECT COUNT(*) FROM singer_accounts").fetchone()[0]
    tok = app_token(WREN, "Wren B")
    _suggest(client, tok)
    nid = _suggest(client, tok)["nights"][0]["night_id"]
    _expect(_claim(client, tok, night_id=nid, mine=[5012]), "claimHistory", 200)
    assert conn.execute("SELECT COUNT(*) FROM singer_accounts").fetchone()[0] == before
    assert store.get_night_started_at() == marker


def test_suggestions_503_without_history_outbox(sing_app, client, app_token, past_night,
                                                monkeypatch):
    monkeypatch.setattr(sing_app, "history_outbox", None)
    tok = app_token(WREN, "Wren B")
    _expect(client.get(f"{V1}/history/suggestions", headers=bearer(tok)),
            "getHistorySuggestions", 503, "gen_unavailable")
    _expect(_claim(client, tok, night_id="x", mine=[1]), "claimHistory", 503,
            "gen_unavailable")


# --- POST /history/claim -----------------------------------------------------------

def test_claim_roundtrip_contract_valid(sing_app, client, app_token, past_night):
    tok = app_token(WREN, "Wren B")
    nid = _suggest(client, tok)["nights"][0]["night_id"]
    body = _expect(_claim(client, tok, night_id=nid, mine=[5012], not_mine=[5013]),
                   "claimHistory", 200)
    assert body == {"api_version": sing_api_v1.API_VERSION, "saved": 1,
                    "already_saved": 0, "taken": [], "rejected": 1}
    again = _expect(_claim(client, tok, night_id=nid, mine=[5012]), "claimHistory", 200)
    assert again["already_saved"] == 1 and again["saved"] == 0
    hid = history_id_for(sing_app.history_outbox.box_id, 5012, WREN)
    rows = sing_app.sing_store._get_conn().execute(
        "SELECT history_id, night_started_at FROM history_outbox").fetchall()
    assert [tuple(r) for r in rows] == [(hid, "claim:" + NIGHT)]
    # Both songs are settled, so the night is gone.
    assert _suggest(client, tok)["nights"] == []


def test_claim_taken_reported(client, app_token, past_night):
    juno = app_token(JUNO, "Wren")  # another account also going by "Wren"
    wren = app_token(WREN, "Wren B")
    nid = _suggest(client, juno)["nights"][0]["night_id"]
    _expect(_claim(client, juno, night_id=nid, mine=[5012]), "claimHistory", 200)
    body = _expect(_claim(client, wren, night_id=nid, mine=[5012, 5013]), "claimHistory", 200)
    assert body["taken"] == [5012] and body["saved"] == 1


@pytest.mark.parametrize("body", [
    {},
    {"night_id": "", "mine": [5012]},
    {"night_id": 7, "mine": [5012]},
    {"mine": [5012]},
    {"night_id": "NID", "mine": [], "not_mine": []},
    {"night_id": "NID"},
    {"night_id": "NID", "mine": "5012"},
    {"night_id": "NID", "mine": [5012, "5013"]},
    {"night_id": "NID", "mine": [True]},
    {"night_id": "NID", "mine": [5012], "not_mine": [5012]},
    {"night_id": "NID", "mine": [5014]},                 # Juno's song, not this account's
    {"night_id": "NID", "mine": list(range(201))},       # over the 200 cap
])
def test_claim_validation_errors(sing_app, client, app_token, past_night, body):
    tok = app_token(WREN, "Wren B")
    nid = _suggest(client, tok)["nights"][0]["night_id"]
    if body.get("night_id") == "NID":
        body = {**body, "night_id": nid}
    _expect(_claim(client, tok, **body), "claimHistory", 400, "invalid")
    conn = sing_app.sing_store._get_conn()
    assert conn.execute("SELECT COUNT(*) FROM history_claims").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM history_rejections").fetchone()[0] == 0


def test_claim_non_json_body_invalid(client, app_token, past_night):
    tok = app_token(WREN, "Wren B")
    resp = client.post(f"{V1}/history/claim", data="mine=1", headers=bearer(tok))
    _expect(resp, "claimHistory", 400, "invalid")


def test_claim_unknown_night_404(client, app_token, past_night):
    tok = app_token(WREN, "Wren B")
    _expect(_claim(client, tok, night_id="0000000000000000", mine=[5012]),
            "claimHistory", 404, "not_found")


def test_claim_requires_bearer_401(client, gen_stub, past_night):
    _expect(client.post(f"{V1}/history/claim", json={"night_id": "x", "mine": [1]}),
            "claimHistory", 401, "unauthenticated")


def test_rate_limited_429_retry_after(client, app_token, past_night):
    tok = app_token(WREN, "Wren B")
    for _ in range(30):
        _suggest(client, tok)
    resp = client.get(f"{V1}/history/suggestions", headers=bearer(tok))
    body = _expect(resp, "getHistorySuggestions", 429, "rate_limited")
    assert body["retry_after"] >= 1
    assert resp.headers["Retry-After"] == str(body["retry_after"])
    # Claims have their own budget (20 / 10 min).
    for _ in range(20):
        _claim(client, tok, night_id="0000000000000000", mine=[5012])
    body = _expect(_claim(client, tok, night_id="0000000000000000", mine=[5012]),
                   "claimHistory", 429, "rate_limited")
    assert body["retry_after"] >= 1
    # Per device: another account is unaffected.
    other = app_token(JUNO, "Juno")
    _suggest(client, other)


def test_token_never_logged(client, app_token, past_night, caplog):
    tok = app_token(WREN, "Wren B")
    with caplog.at_level(logging.DEBUG):
        nid = _suggest(client, tok)["nights"][0]["night_id"]
        _claim(client, tok, night_id=nid, mine=[5012])
        _claim(client, tok, night_id="0000000000000000", mine=[5012])
    assert all(tok not in r.getMessage() for r in caplog.records)
    assert tok not in caplog.text


# --- public host ----------------------------------------------------------------------

def test_public_host_allows_history_paths(mock_config, monkeypatch):
    from app import create_app
    from app_identity import GenTokenVerifier

    mock_config["sing_public_host"] = "sing.nomadkaraoke.com"
    app = create_app(config=mock_config)
    app.config["TESTING"] = True

    class _Gen:
        def get_me(self, session_token):
            return {"user": {"email": WREN, "display_name": "Wren B", "tenant_id": None},
                    "has_session": True}

    monkeypatch.setattr(app, "gen_verifier",
                        GenTokenVerifier(_Gen(), secret=app.app_identity_secret),
                        raising=False)
    host = {"Host": "sing.nomadkaraoke.com", **bearer("tok-public")}
    try:
        with app.test_client() as c:
            resp = c.get("/api/v1/history/suggestions", headers=host)
            _expect(resp, "getHistorySuggestions", 200)
            resp = c.post("/api/v1/history/claim", headers=host,
                          json={"night_id": "0000000000000000", "mine": [1]})
            _expect(resp, "claimHistory", 404, "not_found")
    finally:
        app.catalog.close()
