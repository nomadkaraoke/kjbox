"""Integration tests for /sing/push/* routes."""

import pytest


VALID_SUB = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/abcdefg",
    "keys": {"p256dh": "p256dh-value", "auth": "auth-value"},
}


class TestSubscribe:
    def test_requires_token(self, client):
        resp = client.post("/sing/push/subscribe", json={
            "phone": "+61400000001",
            "singer_name": "Alice",
            "subscription": VALID_SUB,
        })
        assert resp.status_code == 403

    def test_persists_subscription(self, client, sing_app, token):
        sing_app.sing_store.set_enabled(True)
        resp = client.post(f"/sing/push/subscribe?t={token}", json={
            "phone": "+61400000001",
            "singer_name": "Alice",
            "subscription": VALID_SUB,
        })
        assert resp.status_code == 204
        subs = sing_app.sing_store.list_active_push_subscriptions(token)
        assert len(subs) == 1
        assert subs[0]["phone"] == "+61400000001"
        assert subs[0]["singer_name"] == "Alice"
        assert subs[0]["endpoint"] == VALID_SUB["endpoint"]
        assert subs[0]["p256dh"] == "p256dh-value"
        assert subs[0]["auth"] == "auth-value"

    def test_rejects_malformed_payload(self, client, sing_app, token):
        sing_app.sing_store.set_enabled(True)
        resp = client.post(f"/sing/push/subscribe?t={token}", json={
            "phone": "+61400000001",
            # missing singer_name and subscription
        })
        assert resp.status_code == 400

    def test_rejects_missing_endpoint(self, client, sing_app, token):
        sing_app.sing_store.set_enabled(True)
        resp = client.post(f"/sing/push/subscribe?t={token}", json={
            "phone": "+61400000001",
            "singer_name": "Alice",
            "subscription": {"keys": {"p256dh": "p", "auth": "a"}},  # no endpoint
        })
        assert resp.status_code == 400

    def test_rejects_missing_keys(self, client, sing_app, token):
        sing_app.sing_store.set_enabled(True)
        resp = client.post(f"/sing/push/subscribe?t={token}", json={
            "phone": "+61400000001",
            "singer_name": "Alice",
            "subscription": {"endpoint": "ep"},  # no keys
        })
        assert resp.status_code == 400

    def test_rejects_invalid_phone_format(self, client, sing_app, token):
        sing_app.sing_store.set_enabled(True)
        resp = client.post(f"/sing/push/subscribe?t={token}", json={
            "phone": "not-a-phone",
            "singer_name": "Alice",
            "subscription": VALID_SUB,
        })
        assert resp.status_code == 400

    def test_repeat_subscribe_is_idempotent(self, client, sing_app, token):
        """Same endpoint + updated keys should upsert, not error."""
        sing_app.sing_store.set_enabled(True)
        for keys in [{"p256dh": "p1", "auth": "a1"}, {"p256dh": "p2", "auth": "a2"}]:
            sub = {"endpoint": VALID_SUB["endpoint"], "keys": keys}
            resp = client.post(f"/sing/push/subscribe?t={token}", json={
                "phone": "+61400000001",
                "singer_name": "Alice",
                "subscription": sub,
            })
            assert resp.status_code == 204
        subs = sing_app.sing_store.list_active_push_subscriptions(token)
        assert len(subs) == 1
        assert subs[0]["p256dh"] == "p2"


class TestUnsubscribe:
    def test_requires_token(self, client):
        resp = client.post("/sing/push/unsubscribe", json={"endpoint": "ep"})
        assert resp.status_code == 403

    def test_disables_by_endpoint(self, client, sing_app, token):
        sing_app.sing_store.set_enabled(True)
        client.post(f"/sing/push/subscribe?t={token}", json={
            "phone": "+61400000001", "singer_name": "Alice",
            "subscription": VALID_SUB,
        })
        resp = client.post(f"/sing/push/unsubscribe?t={token}", json={
            "endpoint": VALID_SUB["endpoint"],
        })
        assert resp.status_code == 204
        subs = sing_app.sing_store.list_active_push_subscriptions(token)
        assert subs == []

    def test_unsubscribe_missing_endpoint(self, client, sing_app, token):
        sing_app.sing_store.set_enabled(True)
        resp = client.post(f"/sing/push/unsubscribe?t={token}", json={})
        assert resp.status_code == 400

    def test_unsubscribe_unknown_endpoint_is_ok(self, client, sing_app, token):
        """Unsubscribing a never-subscribed endpoint should silently succeed."""
        sing_app.sing_store.set_enabled(True)
        resp = client.post(f"/sing/push/unsubscribe?t={token}", json={
            "endpoint": "never-seen-this",
        })
        assert resp.status_code == 204
