"""App-facing singer API v1: submit, cancel, change, reorder, per-song consent.

Ownership is server-side: a request belongs to the caller when its device_id
is one of the account's devices tonight (the app device, or a web device
linked to the same gen email). The app never sees or sends an edit_token.
Every response is checked against docs/api/singer-v1.openapi.json.
"""

import json
import threading
import uuid

import pytest

import sing
from app_identity import app_device_id
from tests.integration.v1_contract import V1, assert_matches_contract, bearer

EMAIL = "sam.singer@example.com"
OTHER = "olive.other@example.com"


def _join(client, tok, t, name="Sam S"):
    resp = client.post(f"{V1}/join", json={"t": t, "name": name}, headers=bearer(tok))
    assert resp.status_code == 200, resp.get_json()
    assert_matches_contract(resp, "joinNight")


def _song(title="Bo Rhap", **o):
    body = {"song_artist": "Queen", "song_title": title, "source_type": "local",
            "source_ref": f"/{title}.mp4"}
    body.update(o)
    return body


def _submit(client, tok, t, cid=None, **body):
    payload = {"t": t, "client_request_id": cid or str(uuid.uuid4()), **(body or _song())}
    return client.post(f"{V1}/requests", json=payload, headers=bearer(tok))


def _expect(resp, op, status, error=None):
    """Status + strict contract shape (+ the v1 error code) of a response."""
    assert resp.status_code == status, resp.get_json()
    body = assert_matches_contract(resp, op)
    if error is not None:
        assert body["error"] == error
    return body


def _ok_submit(client, tok, t, **body):
    resp = _submit(client, tok, t, **body)
    assert resp.status_code == 200, resp.get_json()
    return assert_matches_contract(resp, "createRequest")["request"]


def _cancel(client, tok, t, rid):
    return client.post(f"{V1}/requests/{rid}/cancel", json={"t": t}, headers=bearer(tok))


def _web_submit(client, t, device_id, singer="Sam S", **o):
    body = {"singer_name": singer, "phone": "", "device_id": device_id, **_song(**o)}
    resp = client.post(f"/sing/submit?t={t}", json=body)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["request"]


@pytest.fixture
def sam(client, token, app_token):
    tok = app_token(EMAIL, "Sam Singer")
    _join(client, tok, token)
    return tok


# --- submit ----------------------------------------------------------------

def test_submit_uses_alias_and_app_device(client, token, sam, sing_app):
    resp = _submit(client, sam, token, **_song(), singer_name="Imposter", device_id="spoof")
    assert resp.status_code == 200
    body = assert_matches_contract(resp, "createRequest")
    req = body["request"]
    assert "edit_token" not in req
    assert req["singer_name"] == "Sam S"
    assert req["for_friend"] is False
    assert body["auto_approved"] is False
    row = sing_app.sing_store.get_request(req["id"])
    assert row["singer_name"] == "Sam S"
    assert row["device_id"] == app_device_id(EMAIL)
    assert "Set-Cookie" not in resp.headers


def test_submit_needs_name_before_join(client, token, app_token, sing_app):
    tok = app_token("new.person@example.com", "New Person")
    resp = _submit(client, tok, token, **_song())
    assert resp.status_code == 400
    assert assert_matches_contract(resp, "createRequest")["error"] == "needs_name"
    assert sing_app.sing_store.list_requests() == []


def test_submit_requires_client_request_id(client, token, sam):
    resp = client.post(f"{V1}/requests", json={"t": token, **_song()}, headers=bearer(sam))
    assert resp.status_code == 400
    assert assert_matches_contract(resp, "createRequest")["error"] == "invalid"
    _expect(_submit(client, sam, token, cid="not-a-uuid", **_song()),
            "createRequest", 400, "invalid")


def test_submit_idempotent_on_client_request_id(client, token, sam, sing_app):
    cid = str(uuid.uuid4())
    first = _submit(client, sam, token, cid=cid, **_song())
    _expect(first, "createRequest", 200)
    again = _submit(client, sam, token, cid=cid, **_song())
    _expect(again, "createRequest", 200)
    assert again.get_json()["request"]["id"] == first.get_json()["request"]["id"]
    # A restart clears the in-memory rate-limit state; the key lives in SQLite.
    sing._rate_limit_state.clear()
    third = _submit(client, sam, token, cid=cid, **_song())
    _expect(third, "createRequest", 200)
    assert third.get_json()["request"]["id"] == first.get_json()["request"]["id"]
    assert len(sing_app.sing_store.list_requests()) == 1
    # Exactly one rotation entry too, with auto-approve on.
    sing_app.sing_store.set_auto_approve(True)
    cid2 = str(uuid.uuid4())
    a = _submit(client, sam, token, cid=cid2, **_song("Other"))
    b = _submit(client, sam, token, cid=cid2, **_song("Other"))
    _expect(a, "createRequest", 200)
    _expect(b, "createRequest", 200)
    assert a.get_json() == b.get_json()
    assert [e["song_artist"] for e in sing_app.rotation.get_rotation()].count("Other - Queen") == 1
    # A new key is a new request.
    assert _submit(client, sam, token, **_song()).get_json()["request"]["id"] != \
        first.get_json()["request"]["id"]


def test_submit_concurrent_retry_gets_busy_retry(client, token, sam, sing_app):
    cid = str(uuid.uuid4())
    key = f"{app_device_id(EMAIL)}:{cid}"
    assert sing_app.sing_store.idempotent_claim(key)  # the first attempt is in flight
    resp = _submit(client, sam, token, cid=cid, **_song())
    assert resp.status_code == 409
    body = assert_matches_contract(resp, "createRequest")
    assert body["error"] == "busy_retry" and body["retry_after"] == 2
    assert sing_app.sing_store.list_requests() == []


def test_submit_simultaneous_retries_create_one_request(mock_config, tmp_path, monkeypatch):
    """Real threads against a file-backed DB (per-thread connections, as in
    production): retries racing each other make one request between them."""
    from app import create_app
    mock_config["rotation_db_path"] = str(tmp_path / "rotation.db")
    app = create_app(config=mock_config)
    app.config["TESTING"] = True
    try:
        _install_gen_stub(app, monkeypatch, "tok")
        t = app.sing_store.ensure_token()
        with app.test_client() as c:
            _join(c, "tok", t)
        cid = str(uuid.uuid4())
        results = []
        start = threading.Barrier(4)

        def go():
            with app.test_client() as c:
                start.wait()
                results.append(_submit(c, "tok", t, cid=cid, **_song()))

        threads = [threading.Thread(target=go) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert len(results) == 4
        assert all(r.status_code in (200, 409) for r in results), \
            [(r.status_code, r.get_json()) for r in results]
        for r in results:
            body = assert_matches_contract(r, "createRequest")
            if r.status_code == 409:
                assert body["error"] == "busy_retry"
        ids = {r.get_json()["request"]["id"] for r in results if r.status_code == 200}
        assert len(ids) == 1
        assert len(app.sing_store.list_requests()) == 1
    finally:
        app.catalog.close()


def test_failed_submit_releases_the_key(client, token, sam, sing_app):
    cid = str(uuid.uuid4())
    bad = _submit(client, sam, token, cid=cid, **_song(source_type="nope"))
    assert bad.status_code == 400
    assert assert_matches_contract(bad, "createRequest")["error"] == "invalid"
    _expect(_submit(client, sam, token, cid=cid, **_song()), "createRequest", 200)
    assert len(sing_app.sing_store.list_requests()) == 1


def test_400_refunds_device_slot(client, token, sam, sing_app):
    sing_app.kj_config["sing_rate_limit_per_device"] = 3
    sing_app.kj_config["sing_rate_limit_per_ip"] = 100  # the IP slot is kept (flood backstop)
    for _ in range(6):
        _expect(_submit(client, sam, token, **_song(source_type="nope")),
                "createRequest", 400, "invalid")
    _expect(_submit(client, sam, token, **_song()), "createRequest", 200)


def test_rate_limited_maps_with_retry_after(client, token, sam, sing_app):
    sing_app.kj_config["sing_rate_limit_per_device"] = 1
    _expect(_submit(client, sam, token, **_song()), "createRequest", 200)
    resp = _submit(client, sam, token, **_song("Two"))
    assert resp.status_code == 429
    body = assert_matches_contract(resp, "createRequest")
    assert body["error"] == "rate_limited"
    assert 1 <= body["retry_after"] <= 300
    assert resp.headers["Retry-After"] == str(body["retry_after"])


def test_kj_pick_over_50_trimmed(client, token, sam, sing_app):
    versions = [{"source": "local", "local": {"path": f"/v{i}.mp4"}} for i in range(60)]
    req = _ok_submit(client, sam, token, **_song(
        source_type="kj_pick", source_ref=None, source_meta={"versions": versions}))
    stored = json.loads(sing_app.sing_store.get_request(req["id"])["source_meta"])
    assert len(stored["versions"]) == 50

    huge = [{"source": "local", "local": {"path": f"/v{i}.mp4"}} for i in range(1001)]
    resp = _submit(client, sam, token, **_song(
        source_type="kj_pick", source_ref=None, source_meta={"versions": huge}))
    assert resp.status_code == 400
    assert assert_matches_contract(resp, "createRequest")["error"] == "kj_pick_too_large"


def test_submit_make_disabled_maps(client, token, sam, sing_app):
    sing_app.sing_store.set_accepting_make_requests(False)
    resp = _submit(client, sam, token, **_song(source_type="make", source_ref=None))
    assert resp.status_code == 400
    assert assert_matches_contract(resp, "createRequest")["error"] == "make_disabled"


def test_friend_song_keeps_owner_device_and_friend_name(client, token, sam, sing_app):
    req = _ok_submit(client, sam, token, **_song(), for_friend=True, friend_name="  Nats  ")
    assert req["singer_name"] == "Nats"
    assert req["for_friend"] is True
    row = sing_app.sing_store.get_request(req["id"])
    assert row["device_id"] == app_device_id(EMAIL)
    assert sing_app.sing_store.get_alias(app_device_id(EMAIL)) == "Sam S"
    assert sing_app.sing_store.is_friend_request(req["id"])
    # The owner manages it like their own song.
    _expect(_cancel(client, sam, token, req["id"]), "cancelRequest", 200)

    resp = _submit(client, sam, token, **_song(), for_friend=True)
    assert resp.status_code == 400
    assert assert_matches_contract(resp, "createRequest")["error"] == "invalid"
    _expect(_submit(client, sam, token, **_song(), for_friend=True, friend_name="x" * 101),
            "createRequest", 400, "invalid")


def test_friend_song_ignores_photo_consent(client, token, sam, sing_app):
    _ok_submit(client, sam, token, **_song(), for_friend=True, friend_name="Nats",
               photo_consent="yes")
    assert sing_app.sing_store.get_photo_consent("Nats") is None


# --- cancel ----------------------------------------------------------------

def test_cancel_own_request(client, token, sam, sing_app):
    req = _ok_submit(client, sam, token, **_song())
    resp = _cancel(client, sam, token, req["id"])
    assert resp.status_code == 200
    body = assert_matches_contract(resp, "cancelRequest")
    assert body["request"]["status"] == "cancelled"
    assert "edit_token" not in body["request"]
    # A retried cancel (timeout) is not an error.
    again = _cancel(client, sam, token, req["id"])
    assert again.status_code == 200
    assert assert_matches_contract(again, "cancelRequest")["request"]["status"] == "cancelled"


def test_cancel_other_device_403_not_owner(client, token, sam, app_token, sing_app):
    olive = app_token(OTHER, "Olive Other")
    _join(client, olive, token, "Olive")
    theirs = _ok_submit(client, olive, token, **_song())
    resp = _cancel(client, sam, token, theirs["id"])
    assert resp.status_code == 403
    assert assert_matches_contract(resp, "cancelRequest")["error"] == "not_owner"
    assert sing_app.sing_store.get_request(theirs["id"])["status"] == "pending"
    # A web singer's request on an unlinked device is not Sam's either.
    web = _web_submit(client, token, "web-dev-x", singer="Sam S")
    _expect(_cancel(client, sam, token, web["id"]), "cancelRequest", 403, "not_owner")


def test_cancel_missing_or_previous_night_404(client, token, sam, sing_app):
    resp = _cancel(client, sam, token, 999999)
    assert resp.status_code == 404
    assert assert_matches_contract(resp, "cancelRequest")["error"] == "not_found"
    req = _ok_submit(client, sam, token, **_song())
    conn = sing_app.sing_store._get_conn()
    conn.execute("UPDATE sing_requests SET created_at='2000-01-01 00:00:00' WHERE id=?",
                 (req["id"],))
    conn.commit()
    _expect(_cancel(client, sam, token, req["id"]), "cancelRequest", 404, "not_found")


def test_cancel_linked_web_device_request_allowed(client, token, sam, sing_app):
    web = _web_submit(client, token, "web-dev-1", singer="Sam S")
    night = sing_app.sing_store.get_night_started_at()
    sing_app.sing_store.link_account("web-dev-1", EMAIL, "web", night)
    resp = _cancel(client, sam, token, web["id"])
    assert resp.status_code == 200
    assert assert_matches_contract(resp, "cancelRequest")["request"]["status"] == "cancelled"


def test_cancel_already_sung_409(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    req = _ok_submit(client, sam, token, **_song())
    entry_id = sing_app.sing_store.get_request(req["id"])["linked_entry_id"]
    sing_app.rotation.update_status(entry_id, "Done")
    resp = _cancel(client, sam, token, req["id"])
    assert resp.status_code == 409
    assert assert_matches_contract(resp, "cancelRequest")["error"] == "already_sung"
    assert sing_app.rotation.store.get_entry(entry_id)["status"] == "Done"


def test_cancel_or_change_on_stage_409(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    req = _ok_submit(client, sam, token, **_song())
    entry_id = sing_app.sing_store.get_request(req["id"])["linked_entry_id"]
    sing_app.rotation.update_status(entry_id, "Now Singing")
    resp = _cancel(client, sam, token, req["id"])
    assert resp.status_code == 409
    assert assert_matches_contract(resp, "cancelRequest")["error"] == "on_stage"
    resp = client.post(f"{V1}/requests/{req['id']}/change",
                       json={"t": token, **_song("New")}, headers=bearer(sam))
    assert resp.status_code == 409
    assert assert_matches_contract(resp, "changeRequest")["error"] == "on_stage"
    assert sing_app.rotation.store.get_entry(entry_id)["status"] == "Now Singing"


# --- change ----------------------------------------------------------------

def test_change_keeps_slot(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    mine = _ok_submit(client, sam, token, **_song())
    _web_submit(client, token, "web-bob", singer="Bob", title="Bob Song")
    old_entry = sing_app.sing_store.get_request(mine["id"])["linked_entry_id"]
    old_pos = sing_app.rotation.store.get_entry(old_entry)["position"]
    resp = client.post(f"{V1}/requests/{mine['id']}/change",
                       json={"t": token, **_song("SOS", song_artist="ABBA")},
                       headers=bearer(sam))
    assert resp.status_code == 200
    body = assert_matches_contract(resp, "changeRequest")
    assert body["auto_approved"] is True
    new = body["request"]
    assert new["id"] != mine["id"] and new["supersedes_request_id"] == mine["id"]
    assert "edit_token" not in new
    new_row = sing_app.sing_store.get_request(new["id"])
    assert new_row["device_id"] == app_device_id(EMAIL)
    entry = sing_app.rotation.store.get_entry(new_row["linked_entry_id"])
    assert entry["position"] == old_pos and entry["file_path"] == "/SOS.mp4"
    assert sing_app.sing_store.get_request(mine["id"])["status"] == "cancelled"


def test_change_friend_song_stays_friend(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    req = _ok_submit(client, sam, token, **_song(), for_friend=True, friend_name="Nats")
    resp = client.post(f"{V1}/requests/{req['id']}/change",
                       json={"t": token, **_song("SOS")}, headers=bearer(sam))
    new = assert_matches_contract(resp, "changeRequest")["request"]
    assert new["for_friend"] is True and new["singer_name"] == "Nats"


def test_change_into_make_rejected(client, token, sam, sing_app):
    req = _ok_submit(client, sam, token, **_song())
    resp = client.post(f"{V1}/requests/{req['id']}/change",
                       json={"t": token, **_song("New", source_type="make", source_ref=None)},
                       headers=bearer(sam))
    assert resp.status_code == 400
    assert assert_matches_contract(resp, "changeRequest")["error"] == "make_disabled"
    assert sing_app.sing_store.get_request(req["id"])["song_title"] == "Bo Rhap"


def test_change_other_device_403(client, token, sam, sing_app):
    web = _web_submit(client, token, "web-dev-x")
    resp = client.post(f"{V1}/requests/{web['id']}/change",
                       json={"t": token, **_song("New")}, headers=bearer(sam))
    assert resp.status_code == 403
    assert assert_matches_contract(resp, "changeRequest")["error"] == "not_owner"


# --- reorder ---------------------------------------------------------------

def test_reorder_own_and_identity_entries(client, token, app_token, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    tok = app_token(EMAIL, "Ashlee A")
    _join(client, tok, token, "Ashlee A")
    own = _ok_submit(client, tok, token, **_song("Mine"))
    host = sing_app.rotation.add_entry("Ashlee A & Chaila R", "Presents - S",
                                       singers=["Ashlee A", "Chaila R"])
    own_entry = sing_app.sing_store.get_request(own["id"])["linked_entry_id"]
    order = [e["id"] for e in sing_app.rotation.get_rotation()]
    assert order.index(own_entry) < order.index(host["id"])

    resp = client.post(f"{V1}/requests/reorder",
                       json={"t": token, "items": [{"entry_id": host["id"]}, {"id": own["id"]}]},
                       headers=bearer(tok))
    assert resp.status_code == 200, resp.get_json()
    body = assert_matches_contract(resp, "reorderRequests")
    assert body["auto_approved"] is True
    assert body["request"]["source_type"] == "reorder"
    order = [e["id"] for e in sing_app.rotation.get_rotation()]
    assert order.index(host["id"]) < order.index(own_entry)


def test_reorder_rejects_unowned(client, token, sam, sing_app):
    sing_app.sing_store.set_auto_approve(True)
    own = _ok_submit(client, sam, token, **_song())
    web = _web_submit(client, token, "web-dev-x", singer="Bob", title="Bob")
    resp = client.post(f"{V1}/requests/reorder",
                       json={"t": token, "items": [{"id": web["id"]}, {"id": own["id"]}]},
                       headers=bearer(sam))
    assert resp.status_code == 403
    assert assert_matches_contract(resp, "reorderRequests")["error"] == "not_owner"
    stranger = sing_app.rotation.add_entry("Someone Else", "X - Y")
    resp = client.post(f"{V1}/requests/reorder",
                       json={"t": token, "items": [{"entry_id": stranger["id"]}, {"id": own["id"]}]},
                       headers=bearer(sam))
    assert resp.status_code == 403
    assert assert_matches_contract(resp, "reorderRequests")["error"] == "not_owner"
    resp = client.post(f"{V1}/requests/reorder",
                       json={"t": token, "items": [{"id": own["id"]}]}, headers=bearer(sam))
    assert resp.status_code == 400
    assert assert_matches_contract(resp, "reorderRequests")["error"] == "invalid"


def test_reorder_friend_name_grants_no_rights(client, token, sam, sing_app):
    """A song for "Bob" (even cancelled) mustn't let Sam move Bob's songs."""
    sing_app.sing_store.set_auto_approve(True)
    own = _ok_submit(client, sam, token, **_song())
    friend = _ok_submit(client, sam, token, **_song("For Bob"), for_friend=True, friend_name="Bob")
    _expect(_cancel(client, sam, token, friend["id"]), "cancelRequest", 200)
    bobs = sing_app.rotation.add_entry("Bob", "Bob Song - B")
    order_before = [e["id"] for e in sing_app.rotation.get_rotation()]
    resp = client.post(f"{V1}/requests/reorder",
                       json={"t": token, "items": [{"entry_id": bobs["id"]}, {"id": own["id"]}]},
                       headers=bearer(sam))
    _expect(resp, "reorderRequests", 403, "not_owner")
    assert [e["id"] for e in sing_app.rotation.get_rotation()] == order_before
    # Not cancelled either: a live friend song grants nothing.
    _ok_submit(client, sam, token, **_song("For Bob 2"), for_friend=True, friend_name="Bob")
    resp = client.post(f"{V1}/requests/reorder",
                       json={"t": token, "items": [{"entry_id": bobs["id"]}, {"id": own["id"]}]},
                       headers=bearer(sam))
    _expect(resp, "reorderRequests", 403, "not_owner")


def test_submit_survives_post_processing_failure(client, token, sam, sing_app, monkeypatch):
    """Once the web made the request, a failure storing the response or the
    friend marker must not release the claim (a retry would duplicate it)."""
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(sing_app.sing_store, "idempotent_put", boom)
    monkeypatch.setattr(sing_app.sing_store, "mark_friend_request", boom)
    cid = str(uuid.uuid4())
    _expect(_submit(client, sam, token, cid=cid, **_song(), for_friend=True, friend_name="Nats"),
            "createRequest", 200)
    _expect(_submit(client, sam, token, cid=cid, **_song(), for_friend=True, friend_name="Nats"),
            "createRequest", 409, "busy_retry")
    assert len(sing_app.sing_store.list_requests()) == 1


def test_signin_required_is_not_unauthenticated(client, token, sam):
    import sing_api_v1
    with client.application.test_request_context("/sing/api/v1/requests"):
        resp = sing_api_v1.map_web_error({"error": "signin_required"}, 401)
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid"
        assert resp.get_json()["message"] == "signin_required"


# --- per-song consent ------------------------------------------------------

def test_song_consent_set_and_clear(client, token, sam, sing_app):
    req = _ok_submit(client, sam, token, **_song())
    resp = client.put(f"{V1}/requests/{req['id']}/consent",
                      json={"t": token, "consent": "no"}, headers=bearer(sam))
    assert resp.status_code == 200
    assert assert_matches_contract(resp, "setRequestConsent")["request"]["media_consent"] == "no"
    resp = client.put(f"{V1}/requests/{req['id']}/consent",
                      json={"t": token, "consent": None}, headers=bearer(sam))
    assert assert_matches_contract(resp, "setRequestConsent")["request"]["media_consent"] is None
    resp = client.put(f"{V1}/requests/{req['id']}/consent",
                      json={"t": token, "consent": "maybe"}, headers=bearer(sam))
    assert resp.status_code == 400
    assert assert_matches_contract(resp, "setRequestConsent")["error"] == "invalid"


def test_song_consent_not_owner_and_friend(client, token, sam, sing_app):
    web = _web_submit(client, token, "web-dev-x")
    resp = client.put(f"{V1}/requests/{web['id']}/consent",
                      json={"t": token, "consent": "yes"}, headers=bearer(sam))
    assert resp.status_code == 403
    assert assert_matches_contract(resp, "setRequestConsent")["error"] == "not_owner"
    # The friend decides about their own song, not the phone's owner.
    friend = _ok_submit(client, sam, token, **_song(), for_friend=True, friend_name="Nats")
    resp = client.put(f"{V1}/requests/{friend['id']}/consent",
                      json={"t": token, "consent": "yes"}, headers=bearer(sam))
    assert resp.status_code == 400
    assert assert_matches_contract(resp, "setRequestConsent")["error"] == "invalid"


# --- delegation plumbing ---------------------------------------------------

@pytest.fixture
def rec_app(mock_config, tmp_path):
    from app import create_app
    mock_config["action_log_dir"] = str(tmp_path / "action-logs")
    mock_config["action_log_enabled"] = True
    app = create_app(config=mock_config)
    app.config["TESTING"] = True
    yield app
    app.catalog.close()


def _records(app):
    with open(app.action_recorder.path_for()) as fh:
        return [json.loads(line) for line in fh]


def _install_gen_stub(app, monkeypatch, tok):
    from app_identity import GenTokenVerifier

    class _Stub:
        def get_me(self, session_token):
            return {"user": {"email": EMAIL, "display_name": "Sam", "tenant_id": None},
                    "has_session": True}

    monkeypatch.setattr(app, "gen_verifier", GenTokenVerifier(_Stub()), raising=False)


def test_delegated_submit_recorded_once(rec_app, monkeypatch):
    t = rec_app.sing_store.ensure_token()
    _install_gen_stub(rec_app, monkeypatch, "tok")
    with rec_app.test_client() as c:
        _join(c, "tok", t)
        resp = _submit(c, "tok", t, **_song())
        _expect(resp, "createRequest", 200)
        assert "Set-Cookie" not in resp.headers
    paths = [r["path"] for r in _records(rec_app)]
    assert paths.count(f"{V1}/requests") == 1
    assert "/sing/submit" not in paths


def test_delegated_exception_recorded_once_on_v1_path(rec_app, monkeypatch):
    t = rec_app.sing_store.ensure_token()
    _install_gen_stub(rec_app, monkeypatch, "tok")

    def boom(**kw):
        raise RuntimeError("db gone")
    with rec_app.test_client() as c:
        _join(c, "tok", t)
        monkeypatch.setattr(rec_app.sing_store, "create_request", boom)
        resp = _submit(c, "tok", t, **_song())
        _expect(resp, "createRequest", 500, "internal")
    recs = [r for r in _records(rec_app) if r["path"] in (f"{V1}/requests", "/sing/submit")]
    assert [(r["path"], r["status"]) for r in recs] == [(f"{V1}/requests", 500)]


def test_delegate_carries_client_ip_and_language(client, token, sam, sing_app, monkeypatch):
    seen = {}
    real = sing._client_ip

    def spy(req):
        seen["ip"] = real(req)
        seen["lang"] = req.headers.get("Accept-Language")
        return seen["ip"]
    monkeypatch.setattr(sing, "_client_ip", spy)
    resp = client.post(f"{V1}/requests",
                       json={"t": token, "client_request_id": str(uuid.uuid4()), **_song()},
                       headers={**bearer(sam), "CF-Connecting-IP": "203.0.113.7",
                                "Accept-Language": "es-MX"},
                       environ_base={"REMOTE_ADDR": "127.0.0.1"})
    _expect(resp, "createRequest", 200)
    assert seen == {"ip": "203.0.113.7", "lang": "es-MX"}
