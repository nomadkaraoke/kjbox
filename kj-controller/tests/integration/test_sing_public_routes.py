"""Integration tests for the public /sing/* blueprint."""

from unittest.mock import MagicMock

import pytest

import sing
from app import create_app


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Each test starts with an empty rate-limit window."""
    sing._rate_limit_state.clear()
    yield
    sing._rate_limit_state.clear()


@pytest.fixture
def sing_app(mock_config):
    # Use a real in-memory rotation + sing_store via create_app
    app = create_app(config=mock_config)
    app.config["TESTING"] = True
    yield app
    app.catalog.close()


@pytest.fixture
def client(sing_app):
    with sing_app.test_client() as c:
        yield c


@pytest.fixture
def token(sing_app):
    return sing_app.sing_store.ensure_token()


class TestLanding:
    def test_landing_no_token_returns_closed_page(self, client):
        resp = client.get("/sing/")
        assert resp.status_code == 403
        assert b"closed" in resp.data.lower() or b"aren't open" in resp.data

    def test_landing_invalid_token(self, client):
        resp = client.get("/sing/?t=nope")
        assert resp.status_code == 403

    def test_landing_valid_token(self, client, token):
        resp = client.get(f"/sing/?t={token}")
        assert resp.status_code == 200
        assert b"sing-root" in resp.data

    def test_landing_when_disabled(self, client, sing_app, token):
        sing_app.sing_store.set_enabled(False)
        resp = client.get(f"/sing/?t={token}")
        assert resp.status_code == 403


class TestSearch:
    def test_missing_token(self, client):
        resp = client.get("/sing/search?q=hello")
        assert resp.status_code == 403

    def test_short_query(self, client, token):
        resp = client.get(f"/sing/search?q=ab&t={token}")
        assert resp.status_code == 400

    def test_search_returns_shape(self, client, token, monkeypatch):
        # Stub out karaoke_nerds to avoid network
        import karaoke_nerds
        monkeypatch.setattr(karaoke_nerds, "search", lambda *a, **kw: [])
        resp = client.get(f"/sing/search?q=hello&t={token}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "local" in data
        assert "karaoke_nerds" in data


class TestSubmit:
    def _body(self, **overrides):
        body = {
            "singer_name": "Andrew",
            "phone": "+61 400 123 456",
            "song_artist": "Queen",
            "song_title": "Bohemian Rhapsody",
            "source_type": "youtube",
            "source_ref": "https://youtu.be/abc",
        }
        body.update(overrides)
        return body

    def test_missing_token(self, client):
        resp = client.post("/sing/submit", json=self._body())
        assert resp.status_code == 403

    def test_happy_path_creates_pending_request(self, client, sing_app, token):
        resp = client.post(f"/sing/submit?t={token}", json=self._body())
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auto_approved"] is False
        req = data["request"]
        assert req["singer_name"] == "Andrew"
        assert req["status"] == "pending"
        # phone is NOT leaked in the public view
        assert "phone" not in req
        # Stored
        assert sing_app.sing_store.count_pending() == 1

    def test_missing_singer_name(self, client, token):
        resp = client.post(f"/sing/submit?t={token}", json=self._body(singer_name=""))
        assert resp.status_code == 400

    def test_missing_phone(self, client, token):
        resp = client.post(f"/sing/submit?t={token}", json=self._body(phone=""))
        assert resp.status_code == 400

    def test_invalid_phone_format(self, client, token):
        resp = client.post(f"/sing/submit?t={token}", json=self._body(phone="abc"))
        assert resp.status_code == 400

    def test_invalid_source_type(self, client, token):
        resp = client.post(f"/sing/submit?t={token}", json=self._body(source_type="bogus"))
        assert resp.status_code == 400

    def test_make_requires_artist_and_title(self, client, token):
        body = self._body(source_type="make", song_artist="", song_title="", source_ref=None)
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 400

    def test_rate_limit_blocks_after_limit(self, client, sing_app, token):
        sing_app.kj_config["sing_rate_limit_per_ip"] = 2
        sing_app.kj_config["sing_rate_limit_window_s"] = 60
        # 1st and 2nd accepted
        for _ in range(2):
            r = client.post(f"/sing/submit?t={token}", json=self._body())
            assert r.status_code == 200
        # 3rd blocked
        r = client.post(f"/sing/submit?t={token}", json=self._body())
        assert r.status_code == 429

    def test_untrusted_peer_cannot_spoof_forwarded_ip(self, sing_app, token):
        """A non-loopback peer must NOT be able to bypass rate limits via CF-Connecting-IP."""
        sing_app.kj_config["sing_rate_limit_per_ip"] = 2
        sing_app.kj_config["sing_rate_limit_window_s"] = 60
        # Simulate an internet origin (not loopback) sending different spoofed
        # CF-Connecting-IP values on each request. The rate limiter must fall
        # back to the real REMOTE_ADDR and still block after the limit.
        client = sing_app.test_client()
        client.environ_base["REMOTE_ADDR"] = "1.2.3.4"
        for _ in range(2):
            r = client.post(
                f"/sing/submit?t={token}",
                json=self._body(),
                headers={"CF-Connecting-IP": "5.6.7.8"},
            )
            assert r.status_code == 200
        r = client.post(
            f"/sing/submit?t={token}",
            json=self._body(),
            headers={"CF-Connecting-IP": "9.9.9.9"},  # try to escape with new spoof
        )
        assert r.status_code == 429

    def test_trusted_loopback_peer_honours_forwarded_ip(self, sing_app, token):
        """cloudflared on localhost gets to pass the real singer IP through."""
        sing_app.kj_config["sing_rate_limit_per_ip"] = 2
        sing_app.kj_config["sing_rate_limit_window_s"] = 60
        client = sing_app.test_client()  # default REMOTE_ADDR=127.0.0.1
        # Two different forwarded IPs from a trusted loopback peer → each
        # gets its own rate-limit bucket.
        for forwarded in ("11.11.11.11", "22.22.22.22"):
            for _ in range(2):
                r = client.post(
                    f"/sing/submit?t={token}",
                    json=self._body(),
                    headers={"CF-Connecting-IP": forwarded},
                )
                assert r.status_code == 200
        # Third request from IP 11.11.11.11 is blocked; IP 22.22.22.22 still ok
        # is ambiguous here because they share the loopback peer — what matters
        # is that the forwarded IP, not the peer, keys the bucket.
        r = client.post(
            f"/sing/submit?t={token}",
            json=self._body(),
            headers={"CF-Connecting-IP": "11.11.11.11"},
        )
        assert r.status_code == 429

    def test_auto_approve_creates_rotation_entry(self, client, sing_app, token):
        sing_app.sing_store.set_auto_approve(True)
        body = self._body(source_type="local", source_ref="/tmp/song.mp4")
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auto_approved"] is True
        assert data["request"]["status"] == "approved"
        # Rotation entry created
        entries = sing_app.rotation.get_rotation()
        assert any(e["singer"] == "Andrew" for e in entries)


class TestStatus:
    def test_status_without_token_rejected(self, client):
        resp = client.get("/sing/status/99999")
        assert resp.status_code == 403

    def test_status_not_found(self, client, token):
        resp = client.get(f"/sing/status/99999?t={token}")
        assert resp.status_code == 404

    def test_status_pending(self, client, sing_app, token):
        resp = client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Andrew", "phone": "+1 555 0000",
            "source_type": "local", "source_ref": "/x.mp4",
        })
        req_id = resp.get_json()["request"]["id"]
        resp = client.get(f"/sing/status/{req_id}?t={token}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["request"]["status"] == "pending"
        assert "position" not in data  # no rotation link yet

    def test_status_after_approval_includes_position(self, client, sing_app, token):
        resp = client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Andrew", "phone": "+1 555 0000",
            "source_type": "local", "source_ref": "/x.mp4",
        })
        req_id = resp.get_json()["request"]["id"]
        # Admin approves
        resp = client.post(f"/rotation/requests/{req_id}/approve")
        assert resp.status_code == 200
        # Singer checks status
        resp = client.get(f"/sing/status/{req_id}?t={token}")
        data = resp.get_json()
        assert data["request"]["status"] == "approved"
        assert data["position"] is not None
        assert "queue" in data

    def test_status_from_previous_event_rejected(self, client, sing_app, token):
        """Requests submitted under an old token must not be readable with a new one."""
        resp = client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Andrew", "phone": "+1 555 0000",
            "source_type": "local", "source_ref": "/x.mp4",
        })
        req_id = resp.get_json()["request"]["id"]
        # KJ starts a new event → new token
        new_token = sing_app.sing_store.regenerate_token()
        # Singer's old tab still has the old token — we verify with the new one
        resp = client.get(f"/sing/status/{req_id}?t={new_token}")
        assert resp.status_code == 404


class TestOverlaySync:
    def test_sync_updates_linked_overlays_only(self, sing_app):
        overlay_manager = sing_app.overlay_manager
        # Overlay linked to event URL
        linked = overlay_manager.create_overlay({
            "type": "qr_code", "name": "Event QR",
            "config": {"url": "old", "follow_event_url": True},
        })
        # Overlay not linked — should NOT change
        unlinked = overlay_manager.create_overlay({
            "type": "qr_code", "name": "Other QR",
            "config": {"url": "leaveme"},
        })
        # Wrong type — should NOT change
        ticker = overlay_manager.create_overlay({
            "type": "ticker", "name": "Ticker",
            "config": {"text": "don't touch"},
        })
        count = sing.sync_event_url_overlays(overlay_manager, "new-url")
        assert count == 1
        assert overlay_manager.get_overlay(linked["id"])["config"]["url"] == "new-url"
        assert overlay_manager.get_overlay(unlinked["id"])["config"]["url"] == "leaveme"
        assert overlay_manager.get_overlay(ticker["id"])["config"]["text"] == "don't touch"
