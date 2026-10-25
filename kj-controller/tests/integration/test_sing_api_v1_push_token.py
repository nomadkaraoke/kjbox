"""POST/DELETE /sing/api/v1/push-token — contract-checked."""

import pytest

from app_identity import app_device_id
from tests.integration.v1_contract import V1, assert_matches_contract, bearer

EMAIL = "sam.singer@example.com"
TOK = "ExponentPushToken[xyz789]"


@pytest.fixture
def sam(app_token):
    return app_token(EMAIL, "Sam Singer")


def _post(client, token, tok, body, status=200):
    resp = client.post(f"{V1}/push-token", json={"t": token, **body}, headers=bearer(tok))
    assert resp.status_code == status, resp.get_json()
    return assert_matches_contract(resp, "registerPushToken")


def test_push_token_endpoint_contract(client, token, sam, sing_app):
    body = _post(client, token, sam, {"expo_push_token": TOK, "platform": "ios"})
    assert body == {"api_version": "1.0", "ok": True}
    rows = sing_app.sing_store.list_active_expo_subscriptions()
    assert len(rows) == 1
    assert rows[0]["expo_token"] == TOK and rows[0]["user_agent"] == "ios"
    assert rows[0]["device_id"] == app_device_id(EMAIL, sing_app.app_identity_secret)


def test_push_token_is_idempotent_and_reenables(client, token, sam, sing_app):
    store = sing_app.sing_store
    _post(client, token, sam, {"expo_push_token": TOK, "platform": "ios"})
    store.disable_push_subscription(store.list_active_expo_subscriptions()[0]["id"])
    assert store.list_active_expo_subscriptions() == []
    _post(client, token, sam, {"expo_push_token": TOK, "platform": "android"})
    rows = store.list_active_expo_subscriptions()
    assert len(rows) == 1 and rows[0]["user_agent"] == "android"


@pytest.mark.parametrize("body", [
    {"expo_push_token": "not-a-token", "platform": "ios"},
    {"expo_push_token": TOK, "platform": "windows"},
    {"platform": "ios"},
    {"expo_push_token": 5, "platform": "ios"},
])
def test_push_token_rejects_bad_input(client, token, sam, body):
    err = _post(client, token, sam, body, status=400)
    assert err["error"] == "invalid"


def test_push_token_needs_auth(client, token):
    resp = client.post(f"{V1}/push-token", json={"t": token, "expo_push_token": TOK, "platform": "ios"})
    assert resp.status_code == 401


def _delete(client, token, tok, body, status=200):
    resp = client.delete(f"{V1}/push-token", json={"t": token, **body}, headers=bearer(tok))
    assert resp.status_code == status, resp.get_json()
    return assert_matches_contract(resp, "unregisterPushToken")


def test_delete_push_token(client, token, sam, sing_app):
    store = sing_app.sing_store
    _post(client, token, sam, {"expo_push_token": TOK, "platform": "ios"})
    body = _delete(client, token, sam, {"expo_push_token": TOK})
    assert body == {"api_version": "1.0", "ok": True}
    assert store.list_active_expo_subscriptions() == []
    _delete(client, token, sam, {"expo_push_token": TOK})   # idempotent


def test_delete_push_token_only_touches_own_device(client, token, sam, app_token, sing_app):
    store = sing_app.sing_store
    _post(client, token, sam, {"expo_push_token": TOK, "platform": "ios"})
    other = app_token("alex.other@example.com", "Alex Other")
    _delete(client, token, other, {"expo_push_token": TOK})
    assert len(store.list_active_expo_subscriptions()) == 1


def test_delete_push_token_rejects_bad_input(client, token, sam):
    assert _delete(client, token, sam, {"expo_push_token": "nope"}, status=400)["error"] == "invalid"
    assert _delete(client, token, sam, {}, status=400)["error"] == "invalid"


def test_delete_push_token_needs_auth(client, token):
    resp = client.delete(f"{V1}/push-token", json={"t": token, "expo_push_token": TOK})
    assert resp.status_code == 401
