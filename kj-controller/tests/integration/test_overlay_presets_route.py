"""Integration tests for /overlays/presets/<name> and ticker refresh on save."""

import pytest


class TestPresetRoute:
    def test_scan_to_sing_creates_qr_overlay(self, flask_test_client):
        resp = flask_test_client.post('/overlays/presets/scan-to-sing')
        assert resp.status_code == 201
        overlay = resp.get_json()
        assert overlay['type'] == 'qr_code'
        assert overlay['name'] == 'Scan to Sing'
        assert overlay['show_over_video'] is True
        assert overlay['enabled'] is True
        cfg = overlay['config']
        assert cfg['follow_event_url'] is True
        # url must be populated by sync_event_url_overlays
        assert cfg['url']

    def test_scan_to_sing_persists_in_listing(self, flask_test_client):
        resp = flask_test_client.post('/overlays/presets/scan-to-sing')
        created_id = resp.get_json()['id']
        listing = flask_test_client.get('/overlays').get_json()
        assert any(o['id'] == created_id for o in listing)

    def test_unknown_preset_returns_400(self, flask_test_client):
        resp = flask_test_client.post('/overlays/presets/does-not-exist')
        assert resp.status_code == 400

    def test_rotation_list_preset_creates_overlay(self, flask_test_client):
        resp = flask_test_client.post('/overlays/presets/rotation-list')
        assert resp.status_code == 201
        overlay = resp.get_json()
        assert overlay['type'] == 'rotation_list'
        assert overlay['show_over_video'] is False
