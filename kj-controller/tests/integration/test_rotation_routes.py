"""Integration tests for rotation routes."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app import create_app


SAMPLE_ENTRIES = [
    {"row_index": 2, "singer": "Alice", "song_artist": "Bohemian Rhapsody", "status": "Singing Now", "notes": ""},
    {"row_index": 3, "singer": "Bob", "song_artist": "Don't Stop Believin", "status": "Next", "notes": ""},
    {"row_index": 4, "singer": "Carol", "song_artist": "Sweet Caroline", "status": "", "notes": ""},
]


@pytest.fixture
def mock_rotation():
    """Mock RotationManager."""
    rotation = MagicMock()
    rotation.get_rotation.return_value = SAMPLE_ENTRIES
    return rotation


@pytest.fixture
def rotation_app(mock_config, mock_rotation):
    """Flask app with rotation configured."""
    app = create_app(config=mock_config)
    app.rotation = mock_rotation
    app.config['TESTING'] = True
    yield app
    app.catalog.close()


@pytest.fixture
def rotation_client(rotation_app):
    with rotation_app.test_client() as client:
        yield client


class TestGetRotation:
    def test_returns_entries(self, rotation_client, mock_rotation):
        resp = rotation_client.get('/rotation')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['entries']) == 3
        assert data['entries'][0]['singer'] == 'Alice'

    def test_force_refresh(self, rotation_client, mock_rotation):
        rotation_client.get('/rotation?refresh=1')
        mock_rotation.get_rotation.assert_called_with(force_refresh=True)

    def test_not_configured_returns_503(self, flask_test_client):
        resp = flask_test_client.get('/rotation')
        assert resp.status_code == 503

    def test_error_returns_500(self, rotation_client, mock_rotation):
        mock_rotation.get_rotation.side_effect = Exception("Sheet error")
        resp = rotation_client.get('/rotation')
        assert resp.status_code == 500


class TestUpdateRotationStatus:
    def test_update_done(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/status',
            data=json.dumps({"row_index": 3, "status": "Done"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.update_status.assert_called_once_with(3, "Done")

    def test_update_singing_uses_mark_singing(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/status',
            data=json.dumps({"row_index": 3, "status": "Singing Now"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.mark_singing.assert_called_once_with(3)

    def test_invalid_row_index_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/status',
            data=json.dumps({"row_index": "abc", "status": "Done"}),
            content_type='application/json')
        assert resp.status_code == 400
        assert "integer" in resp.get_json()["error"]

    def test_negative_row_index_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/status',
            data=json.dumps({"row_index": -1, "status": "Done"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_missing_row_index_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/status',
            data=json.dumps({"status": "Done"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_not_configured_returns_503(self, flask_test_client):
        resp = flask_test_client.post('/rotation/status',
            data=json.dumps({"row_index": 2, "status": "Done"}),
            content_type='application/json')
        assert resp.status_code == 503


class TestAddRotationEntry:
    def test_add_entry(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"singer": "Frank", "song_artist": "My Way"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.add_entry.assert_called_once_with("Frank", "My Way")

    def test_missing_singer_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"song_artist": "My Way"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_not_configured_returns_503(self, flask_test_client):
        resp = flask_test_client.post('/rotation/add',
            data=json.dumps({"singer": "Frank"}),
            content_type='application/json')
        assert resp.status_code == 503
