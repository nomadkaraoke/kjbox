"""Integration tests for the host-based route guard.

When `request.host` matches the configured public sing hostname, only
endpoints on the `sing` blueprint should be reachable — all other routes
must return 404. This prevents the public Cloudflare tunnel from leaking
KJ-only admin routes.
"""

import pytest

from app import create_app


PUBLIC_HOST = "sing.nomadkaraoke.com"


@pytest.fixture
def guarded_app(mock_config):
    mock_config["sing_public_host"] = PUBLIC_HOST
    app = create_app(config=mock_config)
    app.config["TESTING"] = True
    yield app
    app.catalog.close()


@pytest.fixture
def guarded_client(guarded_app):
    with guarded_app.test_client() as c:
        yield c


@pytest.fixture
def token(guarded_app):
    return guarded_app.sing_store.ensure_token()


class TestPublicHostBlocks:
    def test_status_blocked(self, guarded_client):
        resp = guarded_client.get("/status", headers={"Host": PUBLIC_HOST})
        assert resp.status_code == 404

    def test_rotation_blocked(self, guarded_client):
        # The admin /rotation endpoint must NOT be reachable from the public
        # host. Since the WSGI rewriter prefixes /sing on the public host,
        # /rotation now resolves to the token-gated singer rotation endpoint
        # (/sing/rotation) — without a token it returns 403, never the admin
        # rotation manager's data.
        resp = guarded_client.get("/rotation", headers={"Host": PUBLIC_HOST})
        assert resp.status_code == 403
        # Defence in depth: confirm the response shape is the singer error
        # JSON, not the admin rotation manager body.
        assert resp.get_json() == {"error": "not_open"}

    def test_admin_requests_blocked(self, guarded_client):
        resp = guarded_client.get(
            "/rotation/requests", headers={"Host": PUBLIC_HOST}
        )
        assert resp.status_code == 404

    def test_flask_static_blocked(self, guarded_client):
        resp = guarded_client.get(
            "/static/app.js", headers={"Host": PUBLIC_HOST}
        )
        assert resp.status_code == 404


class TestPublicHostAllows:
    def test_sing_landing_allowed(self, guarded_client, token):
        resp = guarded_client.get(
            f"/sing/?t={token}", headers={"Host": PUBLIC_HOST}
        )
        assert resp.status_code == 200

    def test_sing_static_allowed(self, guarded_client):
        # sing.css is a real asset in static-sing/; the guard must not block it.
        resp = guarded_client.get(
            "/sing/static/sing.css", headers={"Host": PUBLIC_HOST}
        )
        assert resp.status_code == 200
        assert b"sing-card" in resp.data  # real CSS body, not a guard 404

    def test_sing_submit_allowed(self, guarded_client, token):
        resp = guarded_client.post(
            f"/sing/submit?t={token}",
            headers={"Host": PUBLIC_HOST},
            json={
                "singer_name": "Andrew", "phone": "+1 5551234",
                "source_type": "local", "source_ref": "/x.mp4",
            },
        )
        assert resp.status_code in (200, 429)


class TestPublicHostRootRewrite:
    """On the public host, `/` and `/<path>` map to the sing blueprint without
    needing the visible `/sing/` prefix. This is what singers see in the QR URL."""

    def test_root_serves_sing_landing(self, guarded_client, token):
        resp = guarded_client.get(
            f"/?t={token}", headers={"Host": PUBLIC_HOST}
        )
        assert resp.status_code == 200
        assert b"sing-root" in resp.data

    def test_bare_root_shows_code_entry(self, guarded_client):
        resp = guarded_client.get("/", headers={"Host": PUBLIC_HOST})
        assert resp.status_code == 200
        assert b"sing-enter-code" in resp.data

    def test_submit_at_root_works(self, guarded_client, token):
        resp = guarded_client.post(
            f"/submit?t={token}",
            headers={"Host": PUBLIC_HOST},
            json={
                "singer_name": "Andrew", "phone": "+1 5551234",
                "source_type": "local", "source_ref": "/x.mp4",
            },
        )
        assert resp.status_code in (200, 429)

    def test_static_at_root_works(self, guarded_client):
        resp = guarded_client.get(
            "/static/sing.css", headers={"Host": PUBLIC_HOST}
        )
        assert resp.status_code == 200
        assert b"sing-card" in resp.data

    def test_validate_at_root_works(self, guarded_client, token):
        resp = guarded_client.post(
            "/validate",
            headers={"Host": PUBLIC_HOST},
            json={"t": token},
        )
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}


class TestPrivateHostUnaffected:
    def test_status_reachable_on_kjbox(self, guarded_client):
        resp = guarded_client.get(
            "/status", headers={"Host": "kjbox.nomadkaraoke.com"}
        )
        assert resp.status_code == 200

    def test_rotation_reachable_on_lan(self, guarded_client):
        resp = guarded_client.get(
            "/rotation", headers={"Host": "nomadpc.local"}
        )
        assert resp.status_code == 200

    def test_sing_still_reachable_on_lan(self, guarded_client, token):
        resp = guarded_client.get(
            f"/sing/?t={token}", headers={"Host": "nomadpc.local"}
        )
        assert resp.status_code == 200

    def test_root_on_lan_hits_admin_not_sing(self, guarded_client):
        """On the admin host, `/` must stay pinned to the KJ controller UI —
        the public-host rewrite must not leak to other hosts."""
        resp = guarded_client.get("/", headers={"Host": "nomadpc.local"})
        # Admin index renders HTML; the thing we care about is that it's NOT
        # the sing UI (no sing-root / sing-enter-code in the body).
        assert resp.status_code == 200
        assert b"sing-enter-code" not in resp.data
        assert b"sing-root" not in resp.data


class TestGuardConfigEdgeCases:
    def test_no_public_host_configured_disables_guard(self, mock_config):
        mock_config["sing_public_host"] = ""
        app = create_app(config=mock_config)
        app.config["TESTING"] = True
        try:
            with app.test_client() as c:
                resp = c.get("/status", headers={"Host": "sing.nomadkaraoke.com"})
                assert resp.status_code == 200
        finally:
            app.catalog.close()

    def test_aliases_also_blocked(self, mock_config):
        mock_config["sing_public_host"] = PUBLIC_HOST
        mock_config["sing_public_host_aliases"] = ["sing2.nomadkaraoke.com"]
        app = create_app(config=mock_config)
        app.config["TESTING"] = True
        try:
            with app.test_client() as c:
                resp = c.get("/status", headers={"Host": "sing2.nomadkaraoke.com"})
                assert resp.status_code == 404
        finally:
            app.catalog.close()

    def test_host_match_is_case_insensitive(self, guarded_client):
        resp = guarded_client.get(
            "/status", headers={"Host": "SING.NOMADKARAOKE.COM"}
        )
        assert resp.status_code == 404

    def test_host_match_strips_port(self, guarded_client):
        resp = guarded_client.get(
            "/status", headers={"Host": f"{PUBLIC_HOST}:8443"}
        )
        assert resp.status_code == 404
