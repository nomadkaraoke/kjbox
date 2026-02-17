"""Integration tests for overlay API routes."""

import json


class TestOverlayRoutes:
    """Test overlay CRUD API endpoints."""

    def test_list_overlays_empty(self, flask_test_client):
        response = flask_test_client.get('/overlays')
        assert response.status_code == 200
        assert response.json == []

    def test_create_overlay(self, flask_test_client):
        response = flask_test_client.post('/overlays', json={
            'type': 'ticker',
            'name': 'Test Ticker',
            'enabled': True,
            'config': {'text': 'Hello', 'speed': 2},
        })
        assert response.status_code == 201
        data = response.json
        assert data['id']
        assert data['type'] == 'ticker'
        assert data['name'] == 'Test Ticker'

    def test_create_overlay_invalid_type(self, flask_test_client):
        response = flask_test_client.post('/overlays', json={
            'type': 'invalid',
            'config': {},
        })
        assert response.status_code == 400
        assert 'error' in response.json

    def test_create_overlay_missing_type(self, flask_test_client):
        response = flask_test_client.post('/overlays', json={
            'name': 'No type',
        })
        assert response.status_code == 400

    def test_get_overlay(self, flask_test_client):
        # Create first
        create_resp = flask_test_client.post('/overlays', json={
            'type': 'static_text',
            'name': 'Find Me',
            'config': {'text': 'test'},
        })
        overlay_id = create_resp.json['id']

        # Get it
        response = flask_test_client.get(f'/overlays/{overlay_id}')
        assert response.status_code == 200
        assert response.json['name'] == 'Find Me'

    def test_get_overlay_not_found(self, flask_test_client):
        response = flask_test_client.get('/overlays/nonexistent')
        assert response.status_code == 404

    def test_update_overlay(self, flask_test_client):
        # Create
        create_resp = flask_test_client.post('/overlays', json={
            'type': 'ticker',
            'name': 'Before',
            'config': {'text': 'old'},
        })
        overlay_id = create_resp.json['id']

        # Update
        response = flask_test_client.put(f'/overlays/{overlay_id}', json={
            'name': 'After',
            'config': {'text': 'new'},
        })
        assert response.status_code == 200
        assert response.json['name'] == 'After'
        assert response.json['config']['text'] == 'new'

    def test_update_overlay_not_found(self, flask_test_client):
        response = flask_test_client.put('/overlays/nonexistent', json={
            'name': 'X',
        })
        assert response.status_code == 404

    def test_update_overlay_no_body(self, flask_test_client):
        response = flask_test_client.put('/overlays/some-id',
                                         content_type='application/json')
        assert response.status_code == 400

    def test_delete_overlay(self, flask_test_client):
        # Create
        create_resp = flask_test_client.post('/overlays', json={
            'type': 'qr_code',
            'name': 'Delete Me',
            'config': {'url': 'https://example.com'},
        })
        overlay_id = create_resp.json['id']

        # Delete
        response = flask_test_client.delete(f'/overlays/{overlay_id}')
        assert response.status_code == 200
        assert response.json['success'] is True

        # Verify gone
        response = flask_test_client.get('/overlays')
        assert len(response.json) == 0

    def test_delete_overlay_not_found(self, flask_test_client):
        response = flask_test_client.delete('/overlays/nonexistent')
        assert response.status_code == 404

    def test_toggle_enabled(self, flask_test_client):
        # Create disabled
        create_resp = flask_test_client.post('/overlays', json={
            'type': 'ticker',
            'enabled': False,
            'config': {},
        })
        overlay_id = create_resp.json['id']

        # Toggle on
        response = flask_test_client.post(f'/overlays/{overlay_id}/toggle')
        assert response.status_code == 200
        assert response.json['enabled'] is True

        # Toggle off
        response = flask_test_client.post(f'/overlays/{overlay_id}/toggle')
        assert response.status_code == 200
        assert response.json['enabled'] is False

    def test_toggle_enabled_not_found(self, flask_test_client):
        response = flask_test_client.post('/overlays/nonexistent/toggle')
        assert response.status_code == 404

    def test_toggle_video(self, flask_test_client):
        create_resp = flask_test_client.post('/overlays', json={
            'type': 'static_text',
            'show_over_video': False,
            'config': {},
        })
        overlay_id = create_resp.json['id']

        response = flask_test_client.post(f'/overlays/{overlay_id}/toggle-video')
        assert response.status_code == 200
        assert response.json['show_over_video'] is True

    def test_toggle_video_not_found(self, flask_test_client):
        response = flask_test_client.post('/overlays/nonexistent/toggle-video')
        assert response.status_code == 404

    def test_full_crud_lifecycle(self, flask_test_client):
        """Create, list, update, toggle, delete — full lifecycle."""
        # Create
        resp = flask_test_client.post('/overlays', json={
            'type': 'countdown',
            'name': 'Last Call',
            'enabled': True,
            'config': {'target_time': '2026-12-31T23:00:00', 'label': 'Countdown'},
        })
        assert resp.status_code == 201
        oid = resp.json['id']

        # List
        resp = flask_test_client.get('/overlays')
        assert len(resp.json) == 1

        # Update
        resp = flask_test_client.put(f'/overlays/{oid}', json={
            'name': 'Updated Name',
        })
        assert resp.json['name'] == 'Updated Name'

        # Toggle
        resp = flask_test_client.post(f'/overlays/{oid}/toggle')
        assert resp.json['enabled'] is False

        # Delete
        resp = flask_test_client.delete(f'/overlays/{oid}')
        assert resp.json['success'] is True

        # Confirm empty
        resp = flask_test_client.get('/overlays')
        assert resp.json == []
