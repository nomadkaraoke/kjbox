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


class TestTickerRefreshOnSave:
    def test_create_rotation_ticker_populates_text(self, flask_test_client, flask_app):
        flask_app.rotation.add_entry('Alice')
        resp = flask_test_client.post('/overlays', json={
            'type': 'ticker',
            'name': 'Rotation Bar',
            'enabled': True,
            'show_over_video': True,
            'config': {
                'source': 'rotation',
                'prefix': 'Up next: ',
                'count': 5,
                'separator': '   ',
                'empty_text': 'Sign up at the booth!',
                'position': 'top',
            },
        })
        assert resp.status_code == 201
        overlay = resp.get_json()
        # The POST response itself is read after the refresh hook so it reflects
        # the populated text.
        assert overlay['config']['text'] == 'Up next: 1. Alice'

    def test_update_to_rotation_source_populates_text(self, flask_test_client, flask_app):
        flask_app.rotation.add_entry('Bob')
        create = flask_test_client.post('/overlays', json={
            'type': 'ticker',
            'name': 'Static',
            'config': {'text': 'placeholder', 'source': 'static'},
        })
        oid = create.get_json()['id']

        update = flask_test_client.put(f'/overlays/{oid}', json={
            'config': {
                'source': 'rotation',
                'prefix': 'Now: ',
                'count': 5,
                'separator': ' | ',
                'empty_text': '',
                'text': 'placeholder',
            },
        })
        assert update.status_code == 200
        assert update.get_json()['config']['text'] == 'Now: 1. Bob'
