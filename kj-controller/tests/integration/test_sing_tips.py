"""Tests for the tip-for-heart flow.

Singers tip via the KJ's configured payment handles, then file a claim
(``POST /sing/tip-claim`` → a ``source_type="tip"`` row on the requests
queue). The KJ confirming it (normal approve route) hearts the singer's
active entries and, at/above ``sing_tip_priority_threshold``, applies the
same singer-level +1 bump as the rotation view's bump-up button.
"""

import json

import pytest

import sing as sing_mod


@pytest.fixture
def tip_config(sing_app):
    """Enable tipping with a Venmo handle; restore config after."""
    cfg = sing_app.kj_config
    keys = ("sing_tip_venmo", "sing_tip_cashapp", "sing_tip_stripe_url",
            "sing_tip_url", "sing_tip_url_label", "sing_tips_enabled",
            "sing_tip_priority_threshold")
    saved = {k: cfg.get(k) for k in keys}
    cfg["sing_tip_venmo"] = "nomadkaraoke"
    sing_mod._tip_rate_limit_state.clear()
    yield cfg
    for k in keys:
        if saved[k] is None:
            cfg.pop(k, None)
        else:
            cfg[k] = saved[k]
    sing_mod._tip_rate_limit_state.clear()


def _claim(client, token, **overrides):
    body = {"singer_name": "Andrew", "amount": 25, "method": "Venmo"}
    body.update(overrides)
    return client.post(f"/sing/tip-claim?t={token}", json=body)


class TestTipInfo:
    def test_zero_config_falls_back_to_live_tip_page(self, client, token):
        # Tipping is ON out of the box, pointing at the existing
        # nomadkaraoke.com/tip page (Stripe + Cash App + Venmo + PayPal).
        resp = client.get(f"/sing/tip-info?t={token}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["enabled"] is True
        assert [m["key"] for m in data["methods"]] == ["page"]
        assert data["methods"][0]["url"] == "https://nomadkaraoke.com/tip"
        assert data["methods"][0]["amount_style"] == "none"

    def test_enabled_with_handle_and_builds_urls(self, client, token, tip_config):
        tip_config["sing_tip_cashapp"] = "$nomadkj"
        tip_config["sing_tip_stripe_url"] = "https://buy.stripe.com/test123"
        tip_config["sing_tip_url"] = "https://tip.example/kj"
        tip_config["sing_tip_url_label"] = "Card"
        resp = client.get(f"/sing/tip-info?t={token}")
        data = resp.get_json()
        assert data["enabled"] is True
        assert data["threshold"] == 20   # default
        urls = {m["key"]: m["url"] for m in data["methods"]}
        assert urls["venmo"] == "https://venmo.com/nomadkaraoke"
        assert urls["cashapp"] == "https://cash.app/$nomadkj"
        assert urls["stripe"] == "https://buy.stripe.com/test123"
        assert urls["custom"] == "https://tip.example/kj"
        # Direct handles configured → the zero-config page fallback is absent.
        assert "page" not in urls
        styles = {m["key"]: m["amount_style"] for m in data["methods"]}
        assert styles == {"venmo": "venmo", "cashapp": "path",
                          "stripe": "none", "custom": "none"}

    def test_explicit_disable_wins(self, client, token, tip_config):
        tip_config["sing_tips_enabled"] = False
        assert client.get(f"/sing/tip-info?t={token}").get_json()["enabled"] is False

    def test_non_http_custom_url_ignored(self, client, token, tip_config):
        tip_config["sing_tip_url"] = "javascript:alert(1)"
        methods = client.get(f"/sing/tip-info?t={token}").get_json()["methods"]
        assert all(m["key"] != "custom" for m in methods)

    def test_threshold_override(self, client, token, tip_config):
        tip_config["sing_tip_priority_threshold"] = 10
        assert client.get(f"/sing/tip-info?t={token}").get_json()["threshold"] == 10


class TestTipClaim:
    def test_rejected_when_tips_disabled(self, client, sing_app, token):
        sing_app.kj_config["sing_tips_enabled"] = False
        try:
            assert _claim(client, token).status_code == 400
        finally:
            sing_app.kj_config.pop("sing_tips_enabled", None)

    def test_creates_tip_request(self, client, sing_app, token, tip_config):
        resp = _claim(client, token)
        assert resp.status_code == 200
        view = resp.get_json()["request"]
        assert view["source_type"] == "tip"
        assert view["tip_amount"] == 25
        assert view["tip_method"] == "Venmo"
        stored = sing_app.sing_store.get_request(view["id"])
        assert stored["status"] == "pending"
        meta = json.loads(stored["source_meta"])
        assert meta == {"amount": 25, "method": "Venmo"}

    def test_requires_name_and_positive_amount(self, client, token, tip_config):
        assert _claim(client, token, singer_name="").status_code == 400
        assert _claim(client, token, amount=0).status_code == 400
        assert _claim(client, token, amount="lots").status_code == 400
        assert _claim(client, token, amount=9999).status_code == 400

    def test_never_auto_approved(self, client, sing_app, token, tip_config):
        # Even with song auto-approve ON, money needs the KJ's eyes.
        sing_app.sing_store.set_auto_approve(True)
        resp = _claim(client, token)
        assert resp.get_json()["request"]["status"] == "pending"

    def test_rate_limited(self, client, token, tip_config):
        codes = [_claim(client, token).status_code for _ in range(6)]
        assert codes[:5] == [200] * 5
        assert codes[5] == 429


class TestTipConfirm:
    def _admin(self, sing_app):
        return sing_app.test_client()

    def test_confirm_hearts_and_bumps_at_threshold(self, client, sing_app, token, tip_config):
        solo = sing_app.rotation.add_entry("Andrew", "Song A")
        duet = sing_app.rotation.add_entry(
            "Mike", "Song B", singers=["Mike", "Andrew"])
        other = sing_app.rotation.add_entry("Jen", "Song C")
        req_id = _claim(client, token, amount=25).get_json()["request"]["id"]

        ap = self._admin(sing_app).post(f"/rotation/requests/{req_id}/approve", json={})
        assert ap.status_code == 200
        assert ap.get_json()["entry_id"] is None
        assert ap.get_json()["request"]["status"] == "approved"

        by_id = {e["id"]: e for e in sing_app.rotation.get_rotation()}
        assert by_id[solo["id"]]["paid"] == 1
        assert by_id[solo["id"]]["priority_bias"] == 1
        # Duet entry the tipper appears in gets the heart + bump too.
        assert by_id[duet["id"]]["paid"] == 1
        assert by_id[duet["id"]]["priority_bias"] == 1
        # Unrelated singer untouched.
        assert by_id[other["id"]]["paid"] == 0
        assert by_id[other["id"]]["priority_bias"] == 0

    def test_below_threshold_hearts_without_bump(self, client, sing_app, token, tip_config):
        entry = sing_app.rotation.add_entry("Andrew", "Song A")
        req_id = _claim(client, token, amount=5).get_json()["request"]["id"]
        ap = self._admin(sing_app).post(f"/rotation/requests/{req_id}/approve", json={})
        assert ap.status_code == 200
        got = {e["id"]: e for e in sing_app.rotation.get_rotation()}[entry["id"]]
        assert got["paid"] == 1
        assert got["priority_bias"] == 0

    def test_confirm_with_no_entries_is_noop_success(self, client, sing_app, token, tip_config):
        req_id = _claim(client, token, amount=25).get_json()["request"]["id"]
        ap = self._admin(sing_app).post(f"/rotation/requests/{req_id}/approve", json={})
        assert ap.status_code == 200
        assert ap.get_json()["request"]["status"] == "approved"

    def test_dismiss_leaves_rotation_untouched(self, client, sing_app, token, tip_config):
        entry = sing_app.rotation.add_entry("Andrew", "Song A")
        req_id = _claim(client, token, amount=25).get_json()["request"]["id"]
        rj = self._admin(sing_app).post(f"/rotation/requests/{req_id}/reject", json={})
        assert rj.status_code == 200
        got = {e["id"]: e for e in sing_app.rotation.get_rotation()}[entry["id"]]
        assert got["paid"] == 0
        assert got["priority_bias"] == 0


class TestTipInMyRequests:
    def test_my_requests_carries_tip_claim(self, client, sing_app, token, tip_config):
        req_id = _claim(client, token, amount=25).get_json()["request"]["id"]
        resp = client.get(f"/sing/my-requests?ids={req_id}&t={token}")
        assert resp.status_code == 200
        items = resp.get_json()["requests"]
        assert len(items) == 1
        view = items[0]["request"]
        assert view["source_type"] == "tip"
        assert view["tip_amount"] == 25
        assert view["tip_method"] == "Venmo"
