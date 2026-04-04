"""Integration tests for rotation routes."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app import create_app


SAMPLE_ENTRIES = [
    {"id": 1, "position": 1, "singer": "Alice", "song_artist": "Bohemian Rhapsody", "status": "Now Singing", "notes": "", "file_path": None, "duration": None},
    {"id": 2, "position": 2, "singer": "Bob", "song_artist": "Don't Stop Believin", "status": "Up Next", "notes": "", "file_path": None, "duration": None},
    {"id": 3, "position": 3, "singer": "Carol", "song_artist": "Sweet Caroline", "status": "", "notes": "", "file_path": None, "duration": None},
]


@pytest.fixture
def mock_rotation():
    """Mock RotationManager."""
    rotation = MagicMock()
    rotation.get_rotation.return_value = list(SAMPLE_ENTRIES)
    rotation.get_sync_status.return_value = {"last_sync": None, "is_online": False, "next_sync_in": None}
    rotation.sync = None
    rotation.media = None
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


@pytest.fixture
def no_rotation_app(mock_config):
    """Flask app with rotation explicitly set to None."""
    app = create_app(config=mock_config)
    app.rotation = None
    app.config['TESTING'] = True
    yield app
    app.catalog.close()


@pytest.fixture
def no_rotation_client(no_rotation_app):
    with no_rotation_app.test_client() as client:
        yield client


class TestGetRotation:
    def test_returns_entries(self, rotation_client, mock_rotation):
        resp = rotation_client.get('/rotation')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['entries']) == 3
        assert data['entries'][0]['singer'] == 'Alice'

    def test_entries_have_estimated_time(self, rotation_client):
        resp = rotation_client.get('/rotation')
        assert resp.status_code == 200
        entries = resp.get_json()['entries']
        assert all('estimated_time' in e for e in entries)

    def test_now_singing_gets_now(self, rotation_client):
        resp = rotation_client.get('/rotation')
        entries = resp.get_json()['entries']
        now_singing = [e for e in entries if e['status'] == 'Now Singing']
        assert all(e['estimated_time'] == 'Now' for e in now_singing)

    def test_not_configured_returns_503(self, no_rotation_client):
        resp = no_rotation_client.get('/rotation')
        assert resp.status_code == 503

    def test_error_returns_500(self, rotation_client, mock_rotation):
        mock_rotation.get_rotation.side_effect = Exception("DB error")
        resp = rotation_client.get('/rotation')
        assert resp.status_code == 500


class TestUpdateRotationStatus:
    def test_update_done(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/status',
            data=json.dumps({"id": 3, "status": "Done"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.update_status.assert_called_once_with(3, "Done")

    def test_update_singing_uses_mark_singing(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/status',
            data=json.dumps({"id": 3, "status": "Now Singing"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.mark_singing.assert_called_once_with(3)

    def test_update_up_next_uses_mark_up_next(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/status',
            data=json.dumps({"id": 4, "status": "Up Next"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.mark_up_next.assert_called_once_with(4)

    def test_invalid_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/status',
            data=json.dumps({"id": "abc", "status": "Done"}),
            content_type='application/json')
        assert resp.status_code == 400
        assert "integer" in resp.get_json()["error"]

    def test_negative_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/status',
            data=json.dumps({"id": -1, "status": "Done"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_missing_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/status',
            data=json.dumps({"status": "Done"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_not_configured_returns_503(self, no_rotation_client):
        resp = no_rotation_client.post('/rotation/status',
            data=json.dumps({"id": 2, "status": "Done"}),
            content_type='application/json')
        assert resp.status_code == 503


class TestEditRotationEntry:
    def test_edit_singer_and_song(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"id": 3, "singer": "Bobby", "song_artist": "New Song"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.update_entry.assert_called_once_with(3, singer="Bobby", song_artist="New Song")

    def test_edit_singer_only(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"id": 3, "singer": "Bobby"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.update_entry.assert_called_once_with(3, singer="Bobby", song_artist=None)

    def test_missing_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"singer": "Bobby"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_invalid_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"id": "abc", "singer": "Bobby"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_negative_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"id": -1, "singer": "Bobby"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_strips_whitespace(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"id": 3, "singer": "  Bobby  ", "song_artist": "  Song  "}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.update_entry.assert_called_once_with(3, singer="Bobby", song_artist="Song")

    def test_error_returns_500(self, rotation_client, mock_rotation):
        mock_rotation.update_entry.side_effect = Exception("DB error")
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"id": 3, "singer": "Bobby"}),
            content_type='application/json')
        assert resp.status_code == 500

    def test_not_configured_returns_503(self, no_rotation_client):
        resp = no_rotation_client.post('/rotation/edit',
            data=json.dumps({"id": 3, "singer": "Bobby"}),
            content_type='application/json')
        assert resp.status_code == 503


class TestDeleteRotationEntry:
    def test_delete_entry(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/delete',
            data=json.dumps({"id": 3}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.delete_entry.assert_called_once_with(3)

    def test_missing_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/delete',
            data=json.dumps({}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_invalid_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/delete',
            data=json.dumps({"id": "abc"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_negative_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/delete',
            data=json.dumps({"id": -1}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_error_returns_500(self, rotation_client, mock_rotation):
        mock_rotation.delete_entry.side_effect = Exception("DB error")
        resp = rotation_client.post('/rotation/delete',
            data=json.dumps({"id": 3}),
            content_type='application/json')
        assert resp.status_code == 500

    def test_not_configured_returns_503(self, no_rotation_client):
        resp = no_rotation_client.post('/rotation/delete',
            data=json.dumps({"id": 3}),
            content_type='application/json')
        assert resp.status_code == 503


class TestAddRotationEntry:
    def test_add_entry(self, rotation_client, mock_rotation):
        mock_rotation.add_entry.return_value = {"id": 4, "singer": "Frank", "song_artist": "My Way"}
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"singer": "Frank", "song_artist": "My Way"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.add_entry.assert_called_once_with("Frank", "My Way", "", file_path=None)

    def test_add_entry_with_notes(self, rotation_client, mock_rotation):
        mock_rotation.add_entry.return_value = {"id": 4, "singer": "Frank", "song_artist": "My Way", "notes": "has mic"}
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"singer": "Frank", "song_artist": "My Way", "notes": "has mic"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.add_entry.assert_called_once_with("Frank", "My Way", "has mic", file_path=None)

    def test_missing_singer_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"song_artist": "My Way"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_response_includes_entry(self, rotation_client, mock_rotation):
        new_entry = {"id": 4, "singer": "Frank", "song_artist": "My Way"}
        mock_rotation.add_entry.return_value = new_entry
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"singer": "Frank"}),
            content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['entry'] == new_entry

    def test_not_configured_returns_503(self, no_rotation_client):
        resp = no_rotation_client.post('/rotation/add',
            data=json.dumps({"singer": "Frank"}),
            content_type='application/json')
        assert resp.status_code == 503

    def test_add_with_file_path(self, rotation_client, mock_rotation):
        mock_rotation.add_entry.return_value = {
            "id": 4, "singer": "Alice", "song_artist": "Song A",
            "file_path": "/media/song.mp4", "duration": None,
        }
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice", "song_artist": "Song A", "file_path": "/media/song.mp4"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.add_entry.assert_called_once_with(
            "Alice", "Song A", "", file_path="/media/song.mp4")

    def test_add_with_url_fallback(self, rotation_client, mock_rotation):
        new_entry = {"id": 4, "singer": "Bob", "song_artist": "Song B",
                     "url_fallback": "https://youtube.com/watch?v=abc"}
        mock_rotation.add_entry.return_value = {"id": 4, "singer": "Bob",
            "song_artist": "Song B", "url_fallback": None}
        mock_rotation.store.get_entry.return_value = new_entry
        mock_rotation.get_rotation.return_value = [new_entry]
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"singer": "Bob", "song_artist": "Song B",
                            "url_fallback": "https://youtube.com/watch?v=abc"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.set_url_fallback.assert_called_once_with(4, "https://youtube.com/watch?v=abc")


class TestMoveRotationEntry:
    def test_move_entry(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/move',
            data=json.dumps({"id": 1, "new_position": 3}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.move_entry.assert_called_once_with(1, 3)

    def test_missing_params_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/move',
            data=json.dumps({"id": 1}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_missing_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/move',
            data=json.dumps({"new_position": 3}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_invalid_params_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/move',
            data=json.dumps({"id": "abc", "new_position": 3}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_negative_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/move',
            data=json.dumps({"id": -1, "new_position": 3}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_negative_position_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/move',
            data=json.dumps({"id": 1, "new_position": -1}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_error_returns_500(self, rotation_client, mock_rotation):
        mock_rotation.move_entry.side_effect = Exception("DB error")
        resp = rotation_client.post('/rotation/move',
            data=json.dumps({"id": 1, "new_position": 3}),
            content_type='application/json')
        assert resp.status_code == 500

    def test_not_configured_returns_503(self, no_rotation_client):
        resp = no_rotation_client.post('/rotation/move',
            data=json.dumps({"id": 1, "new_position": 3}),
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
        mock_rotation.archive_rotation.side_effect = Exception("DB error")
        resp = rotation_client.post('/rotation/archive',
            content_type='application/json')
        assert resp.status_code == 500

    def test_not_configured_returns_503(self, no_rotation_client):
        resp = no_rotation_client.post('/rotation/archive',
            content_type='application/json')
        assert resp.status_code == 503


class TestLinkRotationFile:
    def test_link_file(self, rotation_client, mock_rotation):
        entries = list(SAMPLE_ENTRIES)
        entries[2] = {**entries[2], "file_path": "/media/song.cdg"}
        mock_rotation.get_rotation.return_value = entries
        resp = rotation_client.post('/rotation/link',
            data=json.dumps({"id": 3, "file_path": "/media/song.cdg"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.link_file.assert_called_once_with(3, "/media/song.cdg")

    def test_missing_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/link',
            data=json.dumps({"file_path": "/media/song.cdg"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_missing_file_path_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/link',
            data=json.dumps({"id": 3}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_invalid_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/link',
            data=json.dumps({"id": "abc", "file_path": "/media/song.cdg"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_negative_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/link',
            data=json.dumps({"id": -1, "file_path": "/media/song.cdg"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_error_returns_500(self, rotation_client, mock_rotation):
        mock_rotation.link_file.side_effect = Exception("DB error")
        resp = rotation_client.post('/rotation/link',
            data=json.dumps({"id": 3, "file_path": "/media/song.cdg"}),
            content_type='application/json')
        assert resp.status_code == 500

    def test_not_configured_returns_503(self, no_rotation_client):
        resp = no_rotation_client.post('/rotation/link',
            data=json.dumps({"id": 3, "file_path": "/media/song.cdg"}),
            content_type='application/json')
        assert resp.status_code == 503


class TestUnlinkRotationFile:
    def test_unlink_file(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/unlink',
            data=json.dumps({"id": 3}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.unlink_file.assert_called_once_with(3)

    def test_missing_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/unlink',
            data=json.dumps({}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_invalid_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/unlink',
            data=json.dumps({"id": "abc"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_negative_id_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/unlink',
            data=json.dumps({"id": -1}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_error_returns_500(self, rotation_client, mock_rotation):
        mock_rotation.unlink_file.side_effect = Exception("DB error")
        resp = rotation_client.post('/rotation/unlink',
            data=json.dumps({"id": 3}),
            content_type='application/json')
        assert resp.status_code == 500

    def test_not_configured_returns_503(self, no_rotation_client):
        resp = no_rotation_client.post('/rotation/unlink',
            data=json.dumps({"id": 3}),
            content_type='application/json')
        assert resp.status_code == 503


class TestRotationSyncStatus:
    def test_returns_sync_status(self, rotation_client, mock_rotation):
        mock_rotation.get_sync_status.return_value = {
            "last_sync": 1700000000.0,
            "is_online": True,
            "next_sync_in": 25,
        }
        resp = rotation_client.get('/rotation/sync-status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['is_online'] is True
        assert data['next_sync_in'] == 25

    def test_offline_status_when_no_sync(self, rotation_client, mock_rotation):
        mock_rotation.get_sync_status.return_value = {
            "last_sync": None,
            "is_online": False,
            "next_sync_in": None,
        }
        resp = rotation_client.get('/rotation/sync-status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['is_online'] is False

    def test_error_returns_500(self, rotation_client, mock_rotation):
        mock_rotation.get_sync_status.side_effect = Exception("sync error")
        resp = rotation_client.get('/rotation/sync-status')
        assert resp.status_code == 500

    def test_not_configured_returns_503(self, no_rotation_client):
        resp = no_rotation_client.get('/rotation/sync-status')
        assert resp.status_code == 503


class TestRestoreRotationFromSheet:
    def test_restore_returns_count(self, rotation_client, mock_rotation):
        mock_rotation.restore_from_sheet.return_value = 7
        mock_rotation.get_rotation.return_value = []
        resp = rotation_client.post('/rotation/restore',
            content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['restored'] == 7
        mock_rotation.restore_from_sheet.assert_called_once()

    def test_error_when_no_sync_returns_500(self, rotation_client, mock_rotation):
        mock_rotation.restore_from_sheet.side_effect = RuntimeError("SheetSync is not configured")
        resp = rotation_client.post('/rotation/restore',
            content_type='application/json')
        assert resp.status_code == 500

    def test_not_configured_returns_503(self, no_rotation_client):
        resp = no_rotation_client.post('/rotation/restore',
            content_type='application/json')
        assert resp.status_code == 503


class TestSetPaidRoute:
    def test_set_paid_success(self, rotation_client, mock_rotation):
        mock_rotation.set_paid.return_value = {
            "id": 1, "singer": "Alice", "paid": 1,
        }
        mock_rotation.get_rotation.return_value = [
            {"id": 1, "singer": "Alice", "song_artist": "Song A", "status": "Waiting", "paid": 1},
        ]
        resp = rotation_client.post('/rotation/set-paid',
            data=json.dumps({"id": 1, "paid": True}),
            content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "entries" in data
        mock_rotation.set_paid.assert_called_once_with(1, True)

    def test_set_paid_missing_id(self, rotation_client):
        resp = rotation_client.post('/rotation/set-paid',
            data=json.dumps({"paid": True}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_set_paid_invalid_id(self, rotation_client):
        resp = rotation_client.post('/rotation/set-paid',
            data=json.dumps({"id": "abc", "paid": True}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_set_paid_not_configured(self, no_rotation_client):
        resp = no_rotation_client.post('/rotation/set-paid',
            data=json.dumps({"id": 1, "paid": True}),
            content_type='application/json')
        assert resp.status_code == 503
