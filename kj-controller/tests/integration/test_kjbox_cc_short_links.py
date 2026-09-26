"""kjbox.cc short links for QR codes.

* Public: ``kjbox.cc/<token>`` — a Cloudflare edge redirect to
  ``sing.nomadkaraoke.com/?t=<token>``; the box only has to emit it.
* Local: ``l.kjbox.cc`` — resolves to the box's LAN IP on the KJ wifi and serves
  the singer UI at the root with NO event code (LAN reachability is the proof).
"""

import json

import pytest
import qrcode

from app import create_app
from sing import display_url, get_event_url, qr_data, sync_event_url_overlays

LOCAL_HOST = "l.kjbox.cc"
LAN_PHONE = {"REMOTE_ADDR": "127.0.0.1"}  # Caddy on loopback forwards the phone


def _lan(ip="192.168.8.50", **extra):
    return {"Host": LOCAL_HOST, "X-Forwarded-For": ip, **extra}


@pytest.fixture
def short_config(mock_config):
    mock_config.update({
        "sing_public_url_base": "https://sing.nomadkaraoke.com",
        "sing_public_host": "sing.nomadkaraoke.com",
        "sing_short_url_base": "http://kjbox.cc",
        "sing_local_short_host": LOCAL_HOST,
    })
    return mock_config


@pytest.fixture
def app(short_config):
    a = create_app(config=short_config)
    a.config["TESTING"] = True
    yield a
    a.catalog.close()


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture
def token(app):
    return app.sing_store.ensure_token()


# --- URL builders ----------------------------------------------------------

class TestGetEventUrl:
    def test_public_uses_short_link(self):
        cfg = {"sing_short_url_base": "http://kjbox.cc/"}
        assert get_event_url(cfg, "2121") == "http://kjbox.cc/2121"
        assert get_event_url(cfg, "") == "http://kjbox.cc/"

    def test_public_short_link_off_falls_back_to_full_url(self):
        cfg = {"sing_short_url_base": "", "sing_public_url_base": "https://sing.nomadkaraoke.com"}
        assert get_event_url(cfg, "2121") == "https://sing.nomadkaraoke.com/?t=2121"

    def test_local_short_host_has_no_token(self):
        cfg = {"sing_local_short_host": "L.kjbox.cc"}
        assert get_event_url(cfg, "2121", scope="local") == "http://l.kjbox.cc/"

    def test_local_without_short_host_keeps_lan_url(self):
        cfg = {"sing_local_url_base": "http://192.168.8.170"}
        assert get_event_url(cfg, "2121", scope="local") == "http://192.168.8.170/sing/?t=2121"


class TestDisplayUrl:
    @pytest.mark.parametrize("url,expected", [
        ("http://kjbox.cc/2121", "kjbox.cc/2121"),
        ("HTTPS://kjbox.cc/", "kjbox.cc"),
        ("http://l.kjbox.cc/", "l.kjbox.cc"),
        ("https://sing.nomadkaraoke.com/?t=2121", "sing.nomadkaraoke.com/?t=2121"),
        ("", ""),
    ])
    def test_strips_scheme_and_trailing_slash(self, url, expected):
        assert display_url(url) == expected


class TestQrData:
    def test_short_link_uppercased_for_alphanumeric_mode(self):
        assert qr_data("http://kjbox.cc/2121") == "HTTP://KJBOX.CC/2121"
        assert qr_data("http://l.kjbox.cc/") == "HTTP://L.KJBOX.CC/"

    def test_short_link_fits_smallest_qr(self):
        """The whole point: 21x21 (version 1) instead of 29x29 for the long URL."""
        def version(data):
            q = qrcode.QRCode()
            q.add_data(data)
            q.make(fit=True)
            return q.version
        assert version(qr_data("http://kjbox.cc/2121")) == 1
        assert version("https://sing.nomadkaraoke.com/?t=2121") == 3

    @pytest.mark.parametrize("url", [
        "https://sing.nomadkaraoke.com/?t=2121",  # '?', '=' and letters after host
        "https://example.com/Path",               # case-sensitive path
        "https://example.com/a_b",
        "not a url",
        "",
    ])
    def test_leaves_case_sensitive_or_non_alnum_urls_alone(self, url):
        assert qr_data(url) == url


# --- Overlay sync ------------------------------------------------------------

class TestOverlaySync:
    def test_sync_sets_display_url_and_follow_overlays(self, app):
        om = app.overlay_manager
        follow = om.create_overlay({"type": "qr_code", "config": {"url": "", "follow_event_url": True}})
        fixed = om.create_overlay({"type": "qr_code", "config": {"url": "https://x.test/"}})
        assert sync_event_url_overlays(om, "http://kjbox.cc/4321") == 1
        assert om.get_overlay(follow["id"])["config"]["url"] == "http://kjbox.cc/4321"
        assert om.get_overlay(fixed["id"])["config"]["url"] == "https://x.test/"
        with open(om.config_path) as f:
            assert json.load(f)["event_url"] == "kjbox.cc/4321"

    def test_sync_skips_unchanged(self, app):
        om = app.overlay_manager
        om.create_overlay({"type": "qr_code", "config": {"url": "http://kjbox.cc/4321",
                                                         "follow_event_url": True}})
        om.set_event_url("kjbox.cc/4321")
        assert sync_event_url_overlays(om, "http://kjbox.cc/4321") == 0

    def test_startup_migrates_follow_overlay_to_short_link(self, short_config, tmp_path):
        """A box upgraded from the long URL rewrites its QR on boot, not on the
        next token change."""
        with open(short_config["overlays_path"], "w") as f:
            json.dump({"overlays": [{"id": "q1", "type": "qr_code", "config": {
                "url": "https://sing.nomadkaraoke.com/?t=9999", "follow_event_url": True}}]}, f)
        a = create_app(config=short_config)
        try:
            tok = a.sing_store.get_token()
            assert a.overlay_manager.get_overlay("q1")["config"]["url"] == f"http://kjbox.cc/{tok}"
        finally:
            a.catalog.close()

    def test_update_route_fills_url_when_follow_ticked(self, client, app, token):
        ov = app.overlay_manager.create_overlay({"type": "qr_code", "config": {"url": ""}})
        resp = client.put(f"/overlays/{ov['id']}", json={
            "config": {"url": "", "follow_event_url": True, "label": "{url}"}})
        assert resp.status_code == 200
        assert resp.get_json()["config"]["url"] == f"http://kjbox.cc/{token}"

    def test_create_route_fills_url_when_follow_ticked(self, client, token):
        resp = client.post("/overlays", json={
            "type": "qr_code", "config": {"url": "", "follow_event_url": True}})
        assert resp.status_code == 201
        assert resp.get_json()["config"]["url"] == f"http://kjbox.cc/{token}"

    def test_scan_to_sing_preset_labels_with_url(self, client, token):
        resp = client.post("/overlays/presets/scan-to-sing")
        cfg = resp.get_json()["config"]
        assert cfg["url"] == f"http://kjbox.cc/{token}"
        assert cfg["label"] == "{url}"


# --- Admin surfaces ------------------------------------------------------------

class TestAdminUrls:
    def test_config_reports_short_urls(self, client, token):
        data = client.get("/rotation/requests/config").get_json()
        assert data["public_url"] == f"http://kjbox.cc/{token}"
        assert data["local_url"] == "http://l.kjbox.cc/"

    def test_qr_svg_served(self, client, token):
        for scope in ("public", "local"):
            resp = client.get(f"/rotation/requests/qr.svg?scope={scope}")
            assert resp.status_code == 200
            assert resp.mimetype == "image/svg+xml"


# --- Tokenless LAN host ----------------------------------------------------------

class TestTokenlessLanHost:
    def test_root_serves_spa_without_code(self, client, token):
        resp = client.get("/", headers=_lan(), environ_base=LAN_PHONE)
        assert resp.status_code == 200
        assert b"sing-root" in resp.data
        assert b"sing-enter-code" not in resp.data

    def test_api_works_without_code(self, client, token):
        resp = client.get("/rotation", headers=_lan(), environ_base=LAN_PHONE)
        assert resp.status_code == 200

    def test_admin_routes_still_blocked(self, client, token):
        resp = client.get("/status", headers=_lan(), environ_base=LAN_PHONE)
        assert resp.status_code == 404

    def test_closed_when_requests_disabled(self, client, app, token):
        app.sing_store.set_enabled(False)
        resp = client.get("/", headers=_lan(), environ_base=LAN_PHONE)
        assert resp.status_code == 403

    def test_tunnel_borne_request_needs_code(self, client, token):
        resp = client.get("/", headers=_lan(**{"CF-Connecting-IP": "8.8.8.8"}),
                          environ_base=LAN_PHONE)
        assert b"sing-enter-code" in resp.data

    def test_public_client_ip_needs_code(self, client, token):
        resp = client.get("/", headers=_lan(ip="8.8.8.8"), environ_base=LAN_PHONE)
        assert b"sing-enter-code" in resp.data

    def test_other_hosts_still_need_code(self, client, token):
        resp = client.get("/", headers={"Host": "sing.nomadkaraoke.com",
                                        "X-Forwarded-For": "192.168.8.50"},
                          environ_base=LAN_PHONE)
        assert b"sing-enter-code" in resp.data

    def test_off_when_not_configured(self, mock_config):
        a = create_app(config=mock_config)
        a.config["TESTING"] = True
        try:
            a.sing_store.ensure_token()
            with a.test_client() as c:
                resp = c.get("/sing/", headers=_lan(), environ_base=LAN_PHONE)
                assert b"sing-enter-code" in resp.data
        finally:
            a.catalog.close()
