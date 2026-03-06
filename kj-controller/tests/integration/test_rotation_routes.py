"""Integration tests for rotation routes."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app import create_app


SAMPLE_ENTRIES = [
    {"row_index": 2, "singer": "Alice", "song_artist": "Bohemian Rhapsody", "status": "Now Singing", "notes": ""},
    {"row_index": 3, "singer": "Bob", "song_artist": "Don't Stop Believin", "status": "Up Next", "notes": ""},
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
            data=json.dumps({"row_index": 3, "status": "Now Singing"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.mark_singing.assert_called_once_with(3)

    def test_update_up_next_uses_mark_up_next(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/status',
            data=json.dumps({"row_index": 4, "status": "Up Next"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.mark_up_next.assert_called_once_with(4)

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


class TestEditRotationEntry:
    def test_edit_singer_and_song(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"row_index": 3, "singer": "Bobby", "song_artist": "New Song"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.update_entry.assert_called_once_with(3, singer="Bobby", song_artist="New Song")

    def test_edit_singer_only(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"row_index": 3, "singer": "Bobby"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.update_entry.assert_called_once_with(3, singer="Bobby", song_artist=None)

    def test_missing_row_index_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"singer": "Bobby"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_invalid_row_index_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"row_index": "abc", "singer": "Bobby"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_negative_row_index_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"row_index": -1, "singer": "Bobby"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_strips_whitespace(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"row_index": 3, "singer": "  Bobby  ", "song_artist": "  Song  "}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.update_entry.assert_called_once_with(3, singer="Bobby", song_artist="Song")

    def test_error_returns_500(self, rotation_client, mock_rotation):
        mock_rotation.update_entry.side_effect = Exception("API error")
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"row_index": 3, "singer": "Bobby"}),
            content_type='application/json')
        assert resp.status_code == 500

    def test_not_configured_returns_503(self, flask_test_client):
        resp = flask_test_client.post('/rotation/edit',
            data=json.dumps({"row_index": 3, "singer": "Bobby"}),
            content_type='application/json')
        assert resp.status_code == 503


class TestDeleteRotationEntry:
    def test_delete_entry(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/delete',
            data=json.dumps({"row_index": 3}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.delete_entry.assert_called_once_with(3)

    def test_missing_row_index_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/delete',
            data=json.dumps({}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_invalid_row_index_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/delete',
            data=json.dumps({"row_index": "abc"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_negative_row_index_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/delete',
            data=json.dumps({"row_index": -1}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_error_returns_500(self, rotation_client, mock_rotation):
        mock_rotation.delete_entry.side_effect = Exception("API error")
        resp = rotation_client.post('/rotation/delete',
            data=json.dumps({"row_index": 3}),
            content_type='application/json')
        assert resp.status_code == 500

    def test_not_configured_returns_503(self, flask_test_client):
        resp = flask_test_client.post('/rotation/delete',
            data=json.dumps({"row_index": 3}),
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


class TestMoveRotationEntry:
    def test_move_entry(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/move',
            data=json.dumps({"from_row": 2, "to_row": 4}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.move_entry.assert_called_once_with(2, 4)

    def test_missing_params_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/move',
            data=json.dumps({"from_row": 2}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_invalid_params_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/move',
            data=json.dumps({"from_row": "abc", "to_row": 3}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_negative_row_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/move',
            data=json.dumps({"from_row": -1, "to_row": 3}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_error_returns_500(self, rotation_client, mock_rotation):
        mock_rotation.move_entry.side_effect = Exception("API error")
        resp = rotation_client.post('/rotation/move',
            data=json.dumps({"from_row": 2, "to_row": 4}),
            content_type='application/json')
        assert resp.status_code == 500

    def test_not_configured_returns_503(self, flask_test_client):
        resp = flask_test_client.post('/rotation/move',
            data=json.dumps({"from_row": 2, "to_row": 4}),
            content_type='application/json')
        assert resp.status_code == 503


class TestArchiveRotation:
    def test_archive_returns_count(self, rotation_client, mock_rotation):
        mock_rotation.archive_rotation.return_value = 5
        mock_rotation.get_rotation.return_value = []
        resp = rotation_client.post('/rotation/archive',
            content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['archived'] == 5
        assert data['entries'] == []
        mock_rotation.archive_rotation.assert_called_once()

    def test_error_returns_500(self, rotation_client, mock_rotation):
        mock_rotation.archive_rotation.side_effect = Exception("Sheet error")
        resp = rotation_client.post('/rotation/archive',
            content_type='application/json')
        assert resp.status_code == 500

    def test_not_configured_returns_503(self, flask_test_client):
        resp = flask_test_client.post('/rotation/archive',
            content_type='application/json')
        assert resp.status_code == 503
