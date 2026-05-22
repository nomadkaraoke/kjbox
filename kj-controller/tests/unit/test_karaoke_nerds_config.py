"""Tests for the two-list /karaoke-nerds/config endpoint."""

import pytest

from app import create_app
from version_priority import COMMUNITY_DEFAULTS, COMMERCIAL_DEFAULTS


def _build_client(mock_config, monkeypatch):
    """Create a Flask test client with save_config_value stubbed out so the
    test never writes to a real config.json."""
    monkeypatch.setattr("routes.save_config_value",
                        lambda key, value: None)
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    return app.test_client()


@pytest.fixture
def kn_config_client(mock_config, monkeypatch):
    return _build_client(mock_config, monkeypatch)


class TestGetConfig:
    def test_returns_defaults_when_unset(self, kn_config_client):
        resp = kn_config_client.get('/karaoke-nerds/config')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["priority_community"] == COMMUNITY_DEFAULTS
        assert data["priority_commercial"] == COMMERCIAL_DEFAULTS
        assert "aliases" in data
        assert "CC" in data["aliases"]
        assert "CCK" in [a.upper() for a in data["aliases"]["CC"]]

    def test_returns_overrides_when_set(self, mock_config, monkeypatch):
        mock_config["kn_priority_community"] = ["LC", "CC"]
        mock_config["kn_priority_commercial"] = ["SC", "KV"]
        client = _build_client(mock_config, monkeypatch)
        resp = client.get('/karaoke-nerds/config')
        data = resp.get_json()
        assert data["priority_community"] == ["LC", "CC"]
        assert data["priority_commercial"] == ["SC", "KV"]

    def test_ignores_legacy_kn_preferred_brands(self, mock_config, monkeypatch):
        # Legacy field present — new endpoint returns defaults, not the
        # legacy list, so existing config doesn't leak through.
        mock_config["kn_preferred_brands"] = ["WHATEVER"]
        client = _build_client(mock_config, monkeypatch)
        resp = client.get('/karaoke-nerds/config')
        data = resp.get_json()
        assert data["priority_community"] == COMMUNITY_DEFAULTS


class TestSetConfig:
    def test_saves_both_lists(self, kn_config_client):
        resp = kn_config_client.post(
            '/karaoke-nerds/config',
            json={"priority_community": ["LC", "CC"],
                  "priority_commercial": ["SC", "KV"]})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["priority_community"] == ["LC", "CC"]
        assert data["priority_commercial"] == ["SC", "KV"]

    def test_rejects_unknown_canonical_code(self, kn_config_client):
        resp = kn_config_client.post(
            '/karaoke-nerds/config',
            json={"priority_community": ["WHATEVER"],
                  "priority_commercial": []})
        assert resp.status_code == 400
        assert "WHATEVER" in resp.get_json()["error"]

    def test_uppercases_and_trims(self, kn_config_client):
        resp = kn_config_client.post(
            '/karaoke-nerds/config',
            json={"priority_community": [" lc ", "cc"],
                  "priority_commercial": ["kv"]})
        assert resp.status_code == 200
        assert resp.get_json()["priority_community"] == ["LC", "CC"]

    def test_rejects_non_list(self, kn_config_client):
        resp = kn_config_client.post(
            '/karaoke-nerds/config',
            json={"priority_community": "not a list",
                  "priority_commercial": []})
        assert resp.status_code == 400
