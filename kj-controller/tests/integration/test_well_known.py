"""App-link association files served on the public sing host."""

import json

import pytest

from app import create_app

PUBLIC_HOST = "sing.nomadkaraoke.com"
FP = "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99"


def _client(mock_config, **extra):
    mock_config["sing_public_host"] = PUBLIC_HOST
    mock_config.update(extra)
    app = create_app(config=mock_config)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def public(mock_config):
    app = _client(mock_config, app_android_sha256_fingerprints=[FP])
    with app.test_client() as c:
        yield c
    app.catalog.close()


def test_assetlinks_json_on_public_host(public):
    resp = public.get("/.well-known/assetlinks.json", headers={"Host": PUBLIC_HOST})
    assert resp.status_code == 200
    assert resp.mimetype == "application/json"
    assert resp.headers["Cache-Control"] == "max-age=3600"
    assert resp.get_json() == [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {"namespace": "android_app", "package_name": "com.nomadkaraoke.mobile",
                   "sha256_cert_fingerprints": [FP]},
    }]


def test_assetlinks_empty_without_fingerprints(mock_config):
    app = _client(mock_config)
    with app.test_client() as c:
        resp = c.get("/.well-known/assetlinks.json", headers={"Host": PUBLIC_HOST})
    app.catalog.close()
    assert resp.status_code == 200 and resp.get_json() == []


def test_aasa_paths(public):
    resp = public.get("/.well-known/apple-app-site-association", headers={"Host": PUBLIC_HOST})
    assert resp.status_code == 200
    assert resp.mimetype == "application/json"
    assert resp.headers["Cache-Control"] == "max-age=3600"
    assert json.loads(resp.data) == {"applinks": {"details": [{
        "appIDs": ["55B76HN73D.com.nomadkaraoke.mobile"],
        "components": [{"/": "/", "?": {"t": "?*"}}, {"/": "/*"}],
    }]}}


def test_internal_prefix_also_reachable(public):
    resp = public.get("/sing/.well-known/assetlinks.json", headers={"Host": PUBLIC_HOST})
    assert resp.status_code == 200


def test_well_known_404_on_kj_host_unaffected(public):
    # The un-prefixed path only exists on the public host (the rewriter adds
    # /sing there); the KJ host serves nothing at /.well-known, and unknown
    # well-known names stay 404 on the public host.
    kj = {"Host": "nomadpc.local"}
    assert public.get("/.well-known/assetlinks.json", headers=kj).status_code == 404
    assert public.get("/.well-known/apple-app-site-association", headers=kj).status_code == 404
    assert public.get("/.well-known/other", headers={"Host": PUBLIC_HOST}).status_code == 404
