"""App-facing singer API v1: make a song on the spot with the app's gen account.

The app's own gen session (the v1 bearer) is forwarded to gen per call, so the
job lands in the app user's gen account (the app's Make tab and native review
see it), while kjbox still applies the show credit, the "Being Made (!)"
rotation entry and the quick version. The bearer is never stored: no
``sing_gen_accounts`` row is ever written for an app device.
Every response is checked against docs/api/singer-v1.openapi.json.
"""

import time
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import sing_make
from app_identity import app_device_id
from gen_client import CREATE_JOB_TIMEOUT, GenApiError
from tests.integration.v1_contract import V1, assert_matches_contract, bearer

EMAIL = "sam.singer@example.com"
OTHER = "olive.other@example.com"
YT_BOT = ("We're temporarily unable to access YouTube to download this video — "
          "YouTube is asking our server to verify it's not a bot.")


@pytest.fixture
def gen(sing_app):
    client = MagicMock()
    client.singer_flow_configured.return_value = True
    client.match_judge.return_value = {"kind": "cosmetic", "confident": True,
                                       "canonical_artist": "Radiohead",
                                       "canonical_title": "Creep"}
    client.search_audio.return_value = {"search_session_id": "ss-1", "results": [
        {"index": 0, "provider": "RED", "title": "Pablo Honey", "is_lossless": True,
         "seeders": 80}]}
    client.create_job_from_search.return_value = {"job_id": "job-sam", "status": "pending"}
    client.create_job_from_url.return_value = {"job_id": "job-url", "status": "pending"}
    client.validate_url.return_value = {"supported": True, "platform": "youtube"}
    client.grant_show_credit.return_value = {"granted": True}
    sing_app.gen_client = client
    return client


def _join(client, tok, t, name):
    resp = client.post(f"{V1}/join", json={"t": t, "name": name}, headers=bearer(tok))
    assert resp.status_code == 200, resp.get_json()


@pytest.fixture
def sam(client, token, app_token, gen):
    tok = app_token(EMAIL, "Sam Singer")
    _join(client, tok, token, "Sam S")
    return tok


@pytest.fixture
def olive(client, token, app_token, gen):
    tok = app_token(OTHER, "Olive Other")
    _join(client, tok, token, "Olive")
    return tok


def _expect(resp, op, status, error=None):
    assert resp.status_code == status, resp.get_json()
    body = assert_matches_contract(resp, op)
    if error is not None:
        assert body["error"] == error, body
    return body


def _make(client, tok, t, path, **body):
    return client.post(f"{V1}/make/{path}", json={"t": t, **body}, headers=bearer(tok))


def _submit_make(client, tok, t, cid=None, **o):
    body = {"t": t, "client_request_id": cid or str(uuid.uuid4()), "source_type": "make",
            "song_artist": "Radiohead", "song_title": "Creep",
            "source_meta": {"search_session_id": "ss-1", "selection_index": 0}}
    body.update(o)
    return client.post(f"{V1}/requests", json=body, headers=bearer(tok))


def _gen_account_rows(sing_app):
    conn = sing_app.sing_store._get_conn()
    return conn.execute("SELECT COUNT(*) FROM sing_gen_accounts").fetchone()[0]


# --- the bearer is forwarded, never stored -------------------------------

def test_make_search_forwards_bearer_not_stored(client, token, sam, gen, sing_app):
    body = _expect(_make(client, sam, token, "search", artist="Radiohead", title="Creep"),
                   "makeSearch", 200)
    assert body["search_session_id"] == "ss-1"
    assert body["results"][0]["provider"] == "RED"
    gen.search_audio.assert_called_once_with(sam, "Radiohead", "Creep")
    # The show-credit top-up (A.67) runs as the app user too.
    grant = gen.grant_show_credit.call_args
    assert grant.args[0] == sam and grant.kwargs["only_if_empty"] is True

    check = _expect(_make(client, sam, token, "check", artist="radiohed", title="creep",
                          stage="full", tier=3), "checkMake", 200)
    assert check["canonical_artist"] == "Radiohead"
    gen.match_judge.assert_called_once_with(sam, "radiohed", "creep", stage="full",
                                            audio_confidence_tier=3)

    url = _expect(_make(client, sam, token, "validate-url", url="https://youtu.be/abc"),
                  "validateMakeUrl", 200)
    assert url["supported"] is True
    gen.validate_url.assert_called_once_with(sam, "https://youtu.be/abc")

    assert sing_app.sing_store.get_gen_account(app_device_id(EMAIL)) is None
    assert _gen_account_rows(sing_app) == 0


def test_make_needs_artist_and_title(client, token, sam):
    _expect(_make(client, sam, token, "search", artist="Radiohead"), "makeSearch", 400, "invalid")
    _expect(_make(client, sam, token, "check", title="x"), "checkMake", 400, "invalid")


def test_gen_account_prefers_bearer_only_for_the_app_device(sing_app):
    store = sing_app.sing_store
    app_dev = app_device_id(EMAIL)
    web_dev = "0123456789abcdef" * 2
    store.set_gen_account(web_dev, "mary@example.com", "sess-mary")
    with sing_app.test_request_context("/sing/api/v1/make/search"):
        from flask import g
        # No app user on this request: the stored account (web behaviour).
        assert sing_make._gen_account(store, web_dev)["session_token"] == "sess-mary"
        assert sing_make._gen_account(store, app_dev) is None
        g.app_user = SimpleNamespace(device_id=app_dev, email=EMAIL)
        g.app_gen_account = {"email": EMAIL, "session_token": "bearer-tok"}
        assert sing_make._gen_account(store, app_dev) == {"email": EMAIL,
                                                          "session_token": "bearer-tok"}
        # Any other device still uses its own stored sign-in.
        assert sing_make._gen_account(store, web_dev)["session_token"] == "sess-mary"
    assert sing_make._gen_account(store, web_dev)["session_token"] == "sess-mary"
    assert sing_make._gen_account(store, "") is None


# --- submit: Being Made entry, idempotency, show credit -------------------

def test_make_submit_creates_being_made_entry(client, token, sam, gen, sing_app):
    _expect(_make(client, sam, token, "search", artist="Radiohead", title="Creep"),
            "makeSearch", 200)
    search_key = gen.grant_show_credit.call_args.args[1]
    cid = str(uuid.uuid4())
    body = _expect(_submit_make(client, sam, token, cid=cid), "createRequest", 200)
    req = body["request"]
    assert body["auto_approved"] is True
    assert req["singer_name"] == "Sam S" and req["source_type"] == "make"
    gen.create_job_from_search.assert_called_once_with(sam, "ss-1", 0, "Radiohead", "Creep")
    gen.create_job.assert_not_called()
    # Submit-time top-up uses the search's key: one credit for this song at most.
    last = gen.grant_show_credit.call_args
    assert last.args[0] == sam and last.args[1] == search_key
    assert last.kwargs.get("only_if_empty", False) is False

    entry = sing_app.rotation.store.get_entry(req["linked_entry_id"])
    assert entry["status"] == "Being Made (!)"
    assert entry["gen_job_id"] == "job-sam"
    row = sing_app.sing_store.get_request(req["id"])
    assert row["gen_job_id"] == "job-sam"
    assert row["device_id"] == app_device_id(EMAIL)

    # A retry after a timeout: same request, no second gen job or entry.
    again = _expect(_submit_make(client, sam, token, cid=cid), "createRequest", 200)
    assert again["request"]["id"] == req["id"]
    assert gen.create_job_from_search.call_count == 1
    assert [e.get("gen_job_id") for e in sing_app.rotation.get_rotation()].count("job-sam") == 1
    assert _gen_account_rows(sing_app) == 0


def test_make_submit_claim_outlives_gen_create_timeout(client, token, sam, gen, sing_app):
    """A7-R2: the idempotency claim must not expire while gen is still creating
    the job, or a retry would create a second job."""
    store = sing_app.sing_store
    cid = str(uuid.uuid4())
    key = f"{app_device_id(EMAIL)}:{cid}"
    seen = {}

    def create(*_a, **_k):
        row = store._get_conn().execute(
            "SELECT expires_at FROM app_idempotency WHERE key=?", (key,)).fetchone()
        seen["ttl"] = row["expires_at"] - time.time()
        return {"job_id": "job-sam", "status": "pending"}

    gen.create_job_from_search.side_effect = create
    _expect(_submit_make(client, sam, token, cid=cid), "createRequest", 200)
    assert seen["ttl"] >= sing_make.MAKE_SUBMIT_BUDGET_S + 59
    assert sing_make.MAKE_SUBMIT_BUDGET_S >= CREATE_JOB_TIMEOUT
    assert seen["ttl"] > 300

    # Other submits keep the default claim TTL.
    store.idempotent_claim = MagicMock(wraps=store.idempotent_claim)
    resp = client.post(f"{V1}/requests", headers=bearer(sam), json={
        "t": token, "client_request_id": str(uuid.uuid4()), "song_artist": "Queen",
        "song_title": "Bo Rhap", "source_type": "local", "source_ref": "/b.mp4"})
    _expect(resp, "createRequest", 200)
    assert store.idempotent_claim.call_args.kwargs.get("ttl_s", 300) == 300


def test_idempotent_claim_ttl_parameter(sing_app):
    store = sing_app.sing_store
    assert store.idempotent_claim("k-ttl", ttl_s=500)
    row = store._get_conn().execute(
        "SELECT expires_at FROM app_idempotency WHERE key='k-ttl'").fetchone()
    assert 498 < row["expires_at"] - time.time() <= 500


def test_show_credit_cap_applies_to_app_users(client, token, sam, gen, sing_app):
    sing_app.kj_config["sing_make_max_per_device"] = 1   # 2 distinct searched songs
    for title in ("Creep", "Karma Police"):
        _expect(_make(client, sam, token, "search", artist="Radiohead", title=title),
                "makeSearch", 200)
    _expect(_make(client, sam, token, "search", artist="Radiohead", title="No Surprises"),
            "makeSearch", 400, "make_disabled")


# --- hidden / disabled ----------------------------------------------------

def _night(client, tok, t):
    resp = client.get(f"{V1}/night?t={t}", headers=bearer(tok))
    return _expect(resp, "getNight", 200)


def test_make_hidden_when_disabled_or_simple_mode(client, token, sam, gen, sing_app):
    store = sing_app.sing_store
    assert _night(client, sam, token)["features"]["make"] is True

    store.set_accepting_make_requests(False)
    assert _night(client, sam, token)["features"]["make"] is False
    _expect(_make(client, sam, token, "search", artist="Radiohead", title="Creep"),
            "makeSearch", 400, "make_disabled")
    _expect(_make(client, sam, token, "check", artist="Radiohead", title="Creep"),
            "checkMake", 400, "make_disabled")
    _expect(_submit_make(client, sam, token), "createRequest", 400, "make_disabled")
    store.set_accepting_make_requests(True)

    store.set_simple_mode(True)
    night = _night(client, sam, token)
    assert night["features"]["make"] is False and night["features"]["simple_mode"] is True
    _expect(_make(client, sam, token, "validate-url", url="https://youtu.be/abc"),
            "validateMakeUrl", 400, "make_disabled")
    _expect(_submit_make(client, sam, token), "createRequest", 400, "make_disabled")
    store.set_simple_mode(False)

    gen.singer_flow_configured.return_value = False   # no partner secret
    assert _night(client, sam, token)["features"]["make"] is False
    _expect(_make(client, sam, token, "search", artist="Radiohead", title="Creep"),
            "makeSearch", 400, "make_disabled")
    gen.search_audio.assert_not_called()
    assert sing_app.sing_store.list_requests() == []


# --- gen errors -----------------------------------------------------------

def test_youtube_blocked_maps(client, token, sam, gen, sing_app):
    gen.create_job_from_url.side_effect = GenApiError(400, YT_BOT)
    _expect(_submit_make(client, sam, token, source_meta={"youtube_url": "https://youtu.be/a"}),
            "createRequest", 400, "youtube_blocked")
    gen.create_job_from_url.side_effect = GenApiError(400, "Video unavailable")
    _expect(_submit_make(client, sam, token, source_meta={"youtube_url": "https://youtu.be/a"}),
            "createRequest", 400, "gen_rejected")
    gen.search_audio.side_effect = GenApiError(400, YT_BOT)
    _expect(_make(client, sam, token, "search", artist="Radiohead", title="Creep"),
            "makeSearch", 400, "youtube_blocked")
    gen.search_audio.side_effect = GenApiError(402, {"message": "no credits"})
    _expect(_make(client, sam, token, "search", artist="Radiohead", title="Creep"),
            "makeSearch", 400, "gen_rejected")
    assert sing_app.sing_store.list_requests() == []


def test_failed_make_submit_can_be_retried(client, token, sam, gen, sing_app):
    cid = str(uuid.uuid4())
    gen.create_job_from_search.side_effect = GenApiError(400, "nope")
    _expect(_submit_make(client, sam, token, cid=cid), "createRequest", 400, "gen_rejected")
    gen.create_job_from_search.side_effect = None
    _expect(_submit_make(client, sam, token, cid=cid), "createRequest", 200)
    assert len(sing_app.sing_store.list_requests()) == 1


def test_stale_search_is_conflict(client, token, sam, gen):
    gen.create_job_from_search.side_effect = GenApiError(404, "Search expired")
    body = _expect(_submit_make(client, sam, token), "createRequest", 409, "conflict")
    assert body["message"] == "search_expired"


def test_gen_401_is_unauthenticated(client, token, sam, gen, sing_app):
    """A7-R3: gen refusing the app's token during make → v1 401 unauthenticated
    (the app's own session check decides whether to sign out)."""
    gen.search_audio.side_effect = GenApiError(401, "Invalid session")
    _expect(_make(client, sam, token, "search", artist="Radiohead", title="Creep"),
            "makeSearch", 401, "unauthenticated")
    gen.match_judge.side_effect = GenApiError(401, "Invalid session")
    _expect(_make(client, sam, token, "check", artist="a", title="b"),
            "checkMake", 401, "unauthenticated")
    gen.validate_url.side_effect = GenApiError(401, "Invalid session")
    _expect(_make(client, sam, token, "validate-url", url="https://youtu.be/a"),
            "validateMakeUrl", 401, "unauthenticated")
    gen.create_job_from_search.side_effect = GenApiError(401, "Invalid session")
    _expect(_submit_make(client, sam, token), "createRequest", 401, "unauthenticated")
    assert sing_app.sing_store.list_requests() == []
    assert _gen_account_rows(sing_app) == 0


def test_gen_unreachable_is_gen_unavailable(client, token, sam, gen):
    gen.search_audio.side_effect = GenApiError(0, "connection refused")
    _expect(_make(client, sam, token, "search", artist="Radiohead", title="Creep"),
            "makeSearch", 503, "gen_unavailable")


def test_make_rate_limited_per_device(client, token, sam, olive, gen, sing_app):
    """A7-R4: the web's make bucket, per device (40 calls per 10 minutes)."""
    rate = sing_make._state(sing_app)["rate"]
    now = time.monotonic()
    rate[f"make:dev:{app_device_id(EMAIL)}"].extend(
        [now] * (sing_make._MAKE_RATE_PER_DEVICE - 2))
    for _ in range(2):
        _expect(_make(client, sam, token, "check", artist="a", title="b"), "checkMake", 200)
    resp = _make(client, sam, token, "search", artist="Radiohead", title="Creep")
    body = _expect(resp, "makeSearch", 429, "rate_limited")
    assert 1 <= body["retry_after"] <= sing_make._MAKE_RATE_WINDOW_S
    assert resp.headers["Retry-After"] == str(body["retry_after"])
    _expect(_make(client, sam, token, "validate-url", url="https://youtu.be/a"),
            "validateMakeUrl", 429, "rate_limited")
    # Another account (another app device) has its own budget.
    _expect(_make(client, olive, token, "check", artist="a", title="b"), "checkMake", 200)


# --- quick version ("Sing it now") ---------------------------------------

def _made(client, sam, token):
    return _expect(_submit_make(client, sam, token), "createRequest", 200)["request"]


def _use_quick(client, tok, t, rid):
    return client.post(f"{V1}/make/use-quick/{rid}", json={"t": t}, headers=bearer(tok))


def test_use_quick_requires_ownership(client, token, sam, olive, gen, sing_app, tmp_path):
    req = _made(client, sam, token)
    _expect(_use_quick(client, olive, token, req["id"]), "useQuickVersion", 403, "not_owner")
    _expect(_use_quick(client, sam, token, 9999), "useQuickVersion", 404, "not_found")
    # No quick version yet.
    body = _expect(_use_quick(client, sam, token, req["id"]), "useQuickVersion", 404,
                   "not_found")
    assert body["message"] == "quick_not_ready"

    path = tmp_path / "quick.mp4"
    path.write_bytes(b"\x00" * 64)
    sing_app.rotation.store.upsert_quick_version("job-sam", status="ready",
                                                 file_path=str(path))
    body = _expect(_use_quick(client, sam, token, req["id"]), "useQuickVersion", 200)
    assert body["ok"] is True
    entry = sing_app.rotation.store.get_entry(req["linked_entry_id"])
    assert entry["file_path"] == str(path) and entry["status"] == "Waiting"
    # Not a make request.
    other = client.post(f"{V1}/requests", headers=bearer(sam), json={
        "t": token, "client_request_id": str(uuid.uuid4()), "song_artist": "Queen",
        "song_title": "Bo Rhap", "source_type": "local", "source_ref": "/b.mp4"})
    oid = _expect(other, "createRequest", 200)["request"]["id"]
    _expect(_use_quick(client, sam, token, oid), "useQuickVersion", 404, "not_found")


def test_use_quick_disabled(client, token, sam, gen, sing_app):
    req = _made(client, sam, token)
    sing_app.kj_config["make_quick_version_enabled"] = False
    _expect(_use_quick(client, sam, token, req["id"]), "useQuickVersion", 400, "make_disabled")


def test_make_endpoints_need_auth(client, token, gen):
    for path in ("check", "search", "validate-url", "use-quick/1"):
        resp = client.post(f"{V1}/make/{path}", json={"t": token})
        assert resp.status_code == 401
