"""Web claim (spec 5.6): a web singer signed in with the make-it email code taps
"Save tonight's songs to your Nomad Karaoke account" and kjbox links that
device to their gen account for tonight, so the history outbox sends their
sung songs to decide. Linking is an explicit tap: verify-code alone never
links, and the link ends with the night."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app_identity import app_device_id

DEVICE = "0123456789abcdef" * 2      # the singer UI's random 32-hex device id
OTHER = "fedcba9876543210" * 2
EMAIL = "mary@example.com"


def _local(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _set_night(store, marker):
    conn = store._get_conn()
    conn.execute("INSERT OR REPLACE INTO rotation_meta (key, value) "
                 "VALUES ('night_started_at', ?)", (marker,))
    conn.commit()


@pytest.fixture
def night(sing_app):
    marker = _local(datetime.now() - timedelta(hours=1))
    _set_night(sing_app.sing_store, marker)
    return marker


@pytest.fixture
def gen(sing_app):
    client = MagicMock()
    client.singer_flow_configured.return_value = True
    client.verify_login_code.return_value = {"session_token": "sess-mary", "user": {}}
    sing_app.gen_client = client
    return client


@pytest.fixture
def signed_in(sing_app, gen):
    sing_app.sing_store.set_gen_account(DEVICE, EMAIL, "sess-mary")


def _link(client, token, device=DEVICE, path="link"):
    return client.post(f"/sing/account/{path}?t={token}", json={"device_id": device})


def _account(client, token, device=DEVICE):
    return client.get(f"/sing/make/account?device_id={device}&t={token}").get_json()


def _sung(sing_app, device, name="Mary", song="Golden Hour - Nova Lane"):
    """A request from ``device`` that the host has marked Done tonight."""
    entry = sing_app.rotation.store.add_entry(name, song)
    req = sing_app.sing_store.create_request(
        name, "", song_artist="Nova Lane", song_title="Golden Hour",
        source_type="local", device_id=device)
    sing_app.sing_store.mark_approved(req["id"], linked_entry_id=entry["id"])
    sing_app.rotation.update_status(entry["id"], "Done")
    return entry


def _puts_for(sing_app, night, email):
    rows = sing_app.sing_store.outbox_rows_for_night(night)
    return [r for r in rows.values()
            if r["op"] == "put" and (r["payload"] or {}).get("gen_email") == email]


def test_link_requires_signed_in_device(client, sing_app, token, gen, night):
    resp = _link(client, token)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "signin_required"
    assert sing_app.sing_store.account_for_device(DEVICE, night) is None
    # Unlink needs the stored sign-in too.
    assert _link(client, token, path="unlink").get_json()["error"] == "signin_required"


def test_link_requires_event_code(client, sing_app, signed_in, night):
    resp = client.post("/sing/account/link", json={"device_id": DEVICE})
    assert resp.status_code in (401, 403)
    assert sing_app.sing_store.account_for_device(DEVICE, night) is None


def test_link_links_device_for_tonight(client, sing_app, token, signed_in, night):
    assert _account(client, token)["linked"] is False
    resp = _link(client, token)
    assert resp.status_code == 200
    assert resp.get_json() == {"linked": True, "email": EMAIL}
    assert sing_app.sing_store.account_for_device(DEVICE, night) == EMAIL
    acct = _account(client, token)
    assert acct == {"ready": True, "email": EMAIL, "linked": True}


def test_link_enqueues_already_sung_entries(client, sing_app, token, signed_in, night):
    entry = _sung(sing_app, DEVICE)
    _sung(sing_app, OTHER, name="Bob", song="Paper Moons - The Quiet Hours")
    assert _puts_for(sing_app, night, EMAIL) == []     # not linked yet: nothing queued

    assert _link(client, token).status_code == 200
    puts = _puts_for(sing_app, night, EMAIL)
    assert len(puts) == 1
    assert puts[0]["payload"]["entry_id"] == str(entry["id"])
    assert puts[0]["payload"]["source"] == "kjbox"


def test_make_sign_in_alone_does_not_link(client, sing_app, token, gen, night):
    _sung(sing_app, DEVICE)
    body = {"device_id": DEVICE, "email": EMAIL}
    assert client.post(f"/sing/make/send-code?t={token}", json=body).status_code == 200
    ok = client.post(f"/sing/make/verify-code?t={token}", json={**body, "code": "123456"})
    assert ok.status_code == 200
    assert ok.get_json() == {"email": EMAIL}          # byte-identical, no link fields
    assert sing_app.sing_store.account_for_device(DEVICE, night) is None
    assert _puts_for(sing_app, night, EMAIL) == []
    assert _account(client, token) == {"ready": True, "email": EMAIL, "linked": False}


def test_unlink_stops_future_records(client, sing_app, token, signed_in, night):
    first = _sung(sing_app, DEVICE)
    assert _link(client, token).status_code == 200
    assert len(_puts_for(sing_app, night, EMAIL)) == 1

    resp = _link(client, token, path="unlink")
    assert resp.status_code == 200
    assert resp.get_json() == {"linked": False}
    assert sing_app.sing_store.account_for_device(DEVICE, night) is None
    assert _account(client, token)["linked"] is False

    _sung(sing_app, DEVICE, song="Paper Moons - The Quiet Hours")
    sing_app.history_outbox.scan(force=True)
    puts = _puts_for(sing_app, night, EMAIL)
    # The song already saved stays saved; the new one isn't sent.
    assert [p["payload"]["entry_id"] for p in puts] == [str(first["id"])]


def test_link_is_night_scoped(client, sing_app, token, signed_in, night):
    assert _link(client, token).status_code == 200
    _set_night(sing_app.sing_store, _local(datetime.now()))   # New Rotation
    assert _account(client, token)["linked"] is False


def test_refuses_an_app_device_id(client, sing_app, token, gen, night):
    store = sing_app.sing_store
    app_dev = app_device_id(EMAIL, store.app_identity_secret())
    store.link_account(app_dev, EMAIL, "app", night)
    store.set_gen_account(app_dev, EMAIL, "sess-mary")
    resp = _link(client, token, device=app_dev)
    assert (resp.status_code, resp.get_json()["error"]) == (400, "invalid")
    assert store.account_for_device(app_dev, night) == EMAIL   # the app link is untouched
    assert _link(client, token, device=app_dev, path="unlink").get_json()["error"] == "invalid"
    assert store.account_for_device(app_dev, night) == EMAIL


def test_refuses_a_malformed_device_id(client, token, gen, night):
    resp = client.post(f"/sing/account/link?t={token}", json={"device_id": "abc"})
    assert (resp.status_code, resp.get_json()["error"]) == (400, "invalid")


def test_link_is_rate_limited_on_its_own_budget(client, sing_app, token, signed_in, night,
                                                monkeypatch):
    import sing_make
    monkeypatch.setattr(sing_make, "_CLAIM_RATE_PER_DEVICE", 2)
    sing_app.kj_config["sing_rate_limit_per_device"] = 1
    codes = [_link(client, token).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert _link(client, token).get_json()["error"] == "rate_limited"
    # The song-request budget is untouched by Save/Stop taps.
    sub = client.post(f"/sing/submit?t={token}", json={
        "singer_name": "Mary", "device_id": DEVICE, "song_artist": "Nova Lane",
        "song_title": "Golden Hour", "source_type": "local"})
    assert sub.status_code != 429


@pytest.mark.parametrize("night_off", ["make_off", "simple_mode"])
def test_save_sign_in_works_when_make_it_is_off(client, sing_app, token, gen, night, night_off):
    store = sing_app.sing_store
    if night_off == "make_off":
        store.set_accepting_make_requests(False)
    else:
        store.set_simple_mode(True)
    body = {"device_id": DEVICE, "email": EMAIL}
    # The make-it wizard's sign-in stays closed...
    assert client.post(f"/sing/make/send-code?t={token}", json=body).status_code == 400
    # ...but the save sign-in works: link → code → linked.
    assert _account(client, token) == {"ready": True, "email": None, "linked": False}
    save = {**body, "purpose": "save"}
    assert client.post(f"/sing/make/send-code?t={token}", json=save).status_code == 200
    ok = client.post(f"/sing/make/verify-code?t={token}", json={**save, "code": "123456"})
    assert ok.get_json() == {"email": EMAIL}
    assert _link(client, token).get_json() == {"linked": True, "email": EMAIL}
    assert store.account_for_device(DEVICE, night) == EMAIL


def test_save_sign_in_needs_the_partner_secret(client, sing_app, token, gen, night):
    gen.singer_flow_configured.return_value = False
    body = {"device_id": DEVICE, "email": EMAIL, "purpose": "save"}
    resp = client.post(f"/sing/make/send-code?t={token}", json=body)
    assert (resp.status_code, resp.get_json()["error"]) == (400, "make_requests_disabled")
    assert _account(client, token)["ready"] is False


def test_unlink_without_a_night_is_a_no_op(client, sing_app, token, signed_in):
    store = sing_app.sing_store
    conn = store._get_conn()
    conn.execute("DELETE FROM rotation_meta WHERE key='night_started_at'")
    conn.commit()
    resp = _link(client, token, path="unlink")
    assert resp.status_code == 204
    assert store.get_night_started_at() is None


def test_sign_out_and_new_email_end_the_link(client, sing_app, token, gen, signed_in, night):
    store = sing_app.sing_store
    assert _link(client, token).status_code == 200
    # Verifying the same email again keeps the link.
    client.post(f"/sing/make/verify-code?t={token}",
                json={"device_id": DEVICE, "email": EMAIL, "code": "123456"})
    assert store.account_for_device(DEVICE, night) == EMAIL
    # Signing in as someone else on this phone never saves to the old account.
    client.post(f"/sing/make/verify-code?t={token}",
                json={"device_id": DEVICE, "email": "bob@example.com", "code": "123456"})
    assert store.account_for_device(DEVICE, night) is None
    assert _link(client, token).get_json() == {"linked": True, "email": "bob@example.com"}
    client.post(f"/sing/make/sign-out?t={token}", json={"device_id": DEVICE})
    assert store.account_for_device(DEVICE, night) is None
