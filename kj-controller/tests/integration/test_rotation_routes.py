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
    rotation.store.get_songs_sung_counts.return_value = {}
    rotation.store.get_last_sang_times.return_value = {}
    rotation.get_singer_stats.return_value = []
    # Server-side undo/redo surface (GET /rotation includes these).
    rotation.store.get_rev.return_value = 0
    rotation.history_status.return_value = {
        "undo": 0, "redo": 0, "undo_label": None, "redo_label": None,
    }
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

    def test_entries_have_songs_sung(self, rotation_client):
        resp = rotation_client.get('/rotation')
        entries = resp.get_json()['entries']
        assert all('songs_sung' in e for e in entries)
        assert all(e['songs_sung'] == 0 for e in entries)

    def test_songs_sung_reflects_done_count(self, rotation_client, mock_rotation):
        mock_rotation.store.get_songs_sung_counts.return_value = {"alice": 2, "bob": 1}
        resp = rotation_client.get('/rotation')
        entries = resp.get_json()['entries']
        alice = next(e for e in entries if e['singer'] == 'Alice')
        bob = next(e for e in entries if e['singer'] == 'Bob')
        carol = next(e for e in entries if e['singer'] == 'Carol')
        assert alice['songs_sung'] == 2
        assert bob['songs_sung'] == 1
        assert carol['songs_sung'] == 0

    def test_entries_have_last_sang_minutes(self, rotation_client):
        resp = rotation_client.get('/rotation')
        entries = resp.get_json()['entries']
        assert all('last_sang_minutes' in e for e in entries)
        assert all(e['last_sang_minutes'] is None for e in entries)

    def test_last_sang_minutes_reflects_store(self, rotation_client, mock_rotation):
        mock_rotation.store.get_last_sang_times.return_value = {"alice": 22, "bob": 0}
        resp = rotation_client.get('/rotation')
        entries = resp.get_json()['entries']
        alice = next(e for e in entries if e['singer'] == 'Alice')
        bob = next(e for e in entries if e['singer'] == 'Bob')
        carol = next(e for e in entries if e['singer'] == 'Carol')
        assert alice['last_sang_minutes'] == 22
        assert bob['last_sang_minutes'] == 0
        assert carol['last_sang_minutes'] is None

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
        mock_rotation.update_entry.assert_called_once_with(3, singer="Bobby", song_artist="New Song", singers=None)

    def test_edit_singer_only(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"id": 3, "singer": "Bobby"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.update_entry.assert_called_once_with(3, singer="Bobby", song_artist=None, singers=None)

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
        mock_rotation.update_entry.assert_called_once_with(3, singer="Bobby", song_artist="Song", singers=None)

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
        mock_rotation.add_entry.assert_called_once_with("Frank", "My Way", "", file_path=None, singers=None)

    def test_add_entry_with_notes(self, rotation_client, mock_rotation):
        mock_rotation.add_entry.return_value = {"id": 4, "singer": "Frank", "song_artist": "My Way", "notes": "has mic"}
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"singer": "Frank", "song_artist": "My Way", "notes": "has mic"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.add_entry.assert_called_once_with("Frank", "My Way", "has mic", file_path=None, singers=None)

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
            "Alice", "Song A", "", file_path="/media/song.mp4", singers=None)

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
    @pytest.fixture(autouse=True)
    def _pass_playability_gate(self):
        # These tests exercise link behaviour, not the playability gate (which
        # has its own tests in test_link_gate.py). Stub the gate to pass so the
        # fake paths used here aren't hard-blocked.
        from types import SimpleNamespace
        with patch('routes._playability_gate',
                   return_value=SimpleNamespace(verdict={"overall_ok": True, "reasons": []})):
            yield

    def test_link_file(self, rotation_client, mock_rotation):
        entries = list(SAMPLE_ENTRIES)
        entries[2] = {**entries[2], "file_path": "/media/song.cdg"}
        mock_rotation.get_rotation.return_value = entries
        resp = rotation_client.post('/rotation/link',
            data=json.dumps({"id": 3, "file_path": "/media/song.cdg"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.link_file.assert_called_once_with(3, "/media/song.cdg")

    def test_missing_id_and_singer_returns_400(self, rotation_client):
        # Without either an id or a singer, there's no entry to link to
        # and nothing to create — this is a malformed body.
        resp = rotation_client.post('/rotation/link',
            data=json.dumps({"file_path": "/media/song.cdg"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_link_creates_entry_when_no_id(self, rotation_client, mock_rotation):
        # Add-mode: the search dropdown's "Link" button on a local result
        # while no entry exists yet. /rotation/link must create the entry
        # AND link the file in one call (not 400).
        mock_rotation.add_entry.return_value = {"id": 99}
        resp = rotation_client.post(
            '/rotation/link',
            data=json.dumps({
                "singers": ["Andrew"],
                "song_artist": "Let Down - Radiohead",
                "file_path": "/media/letdown.mp4",
            }),
            content_type='application/json',
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        mock_rotation.add_entry.assert_called_once()
        mock_rotation.link_file.assert_called_once_with(99, "/media/letdown.mp4")

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


class TestRestoreRoute:
    """Tests for POST /rotation/restore."""

    def test_restore_success(self, rotation_client, mock_rotation):
        """Restore endpoint calls restore_entries and returns updated entries."""
        mock_rotation.restore_entries.return_value = None
        mock_rotation.get_rotation.return_value = SAMPLE_ENTRIES

        resp = rotation_client.post('/rotation/restore', json={
            'entries': SAMPLE_ENTRIES,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert 'entries' in data
        mock_rotation.restore_entries.assert_called_once()

    def test_restore_missing_entries_field(self, rotation_client):
        """Missing entries field returns 400."""
        resp = rotation_client.post('/rotation/restore', json={})
        assert resp.status_code == 400
        assert 'entries' in resp.get_json()['error'].lower()

    def test_restore_entries_not_list(self, rotation_client):
        """Non-list entries field returns 400."""
        resp = rotation_client.post('/rotation/restore', json={'entries': 'not a list'})
        assert resp.status_code == 400

    def test_restore_no_rotation(self, no_rotation_client):
        """Returns 503 when rotation is not configured."""
        resp = no_rotation_client.post('/rotation/restore', json={'entries': []})
        assert resp.status_code == 503


class TestMultiSingerAdd:
    def test_add_with_singers_array(self, rotation_client, mock_rotation):
        mock_rotation.add_entry.return_value = {
            "id": 10, "singer": "Phil & Anya", "singers_json": '["Phil", "Anya"]',
            "position": 4, "status": "Waiting",
        }
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"singers": ["Phil", "Anya"], "song_artist": "Duet Song"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.add_entry.assert_called_once()
        call_kwargs = mock_rotation.add_entry.call_args
        assert call_kwargs[1].get("singers") == ["Phil", "Anya"]

    def test_add_without_singers_backward_compat(self, rotation_client, mock_rotation):
        mock_rotation.add_entry.return_value = {
            "id": 10, "singer": "Sarah", "singers_json": None,
            "position": 4, "status": "Waiting",
        }
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"singer": "Sarah", "song_artist": "My Song"}),
            content_type='application/json')
        assert resp.status_code == 200
        call_kwargs = mock_rotation.add_entry.call_args
        assert call_kwargs[1].get("singers") is None


class TestMultiSingerEdit:
    def test_edit_with_singers_array(self, rotation_client, mock_rotation):
        mock_rotation.update_entry.return_value = {
            "id": 1, "singer": "Phil & Anya", "singers_json": '["Phil", "Anya"]',
        }
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"id": 1, "singers": ["Phil", "Anya"]}),
            content_type='application/json')
        assert resp.status_code == 200
        call_kwargs = mock_rotation.update_entry.call_args
        assert call_kwargs[1].get("singers") == ["Phil", "Anya"]


class TestMultiSingerSongsSung:
    def test_songs_sung_min_for_multi_singer(self, rotation_client, mock_rotation):
        mock_rotation.store.get_songs_sung_counts.return_value = {"phil": 3, "anya": 1}
        mock_rotation.get_rotation.return_value = [
            {"id": 1, "position": 1, "singer": "Phil & Anya",
             "singers_json": '["Phil", "Anya"]', "status": "Waiting",
             "song_artist": "Duet", "notes": "", "file_path": None, "duration": None},
        ]
        resp = rotation_client.get('/rotation')
        entries = resp.get_json()['entries']
        assert entries[0]['songs_sung'] == 1  # min(phil=3, anya=1)

    def test_songs_sung_legacy_entry_unchanged(self, rotation_client, mock_rotation):
        mock_rotation.store.get_songs_sung_counts.return_value = {"sarah": 2}
        mock_rotation.get_rotation.return_value = [
            {"id": 1, "position": 1, "singer": "Sarah",
             "singers_json": None, "status": "Waiting",
             "song_artist": "Song", "notes": "", "file_path": None, "duration": None},
        ]
        resp = rotation_client.get('/rotation')
        entries = resp.get_json()['entries']
        assert entries[0]['songs_sung'] == 2


class TestMultiSingerLastSang:
    def test_last_sang_max_for_multi_singer(self, rotation_client, mock_rotation):
        # The pill surfaces the LONGEST wait across the group, mirroring how
        # songs_sung surfaces the least-served member.
        mock_rotation.store.get_last_sang_times.return_value = {"phil": 5, "anya": 90}
        mock_rotation.get_rotation.return_value = [
            {"id": 1, "position": 1, "singer": "Phil & Anya",
             "singers_json": '["Phil", "Anya"]', "status": "Waiting",
             "song_artist": "Duet", "notes": "", "file_path": None, "duration": None},
        ]
        resp = rotation_client.get('/rotation')
        entries = resp.get_json()['entries']
        assert entries[0]['last_sang_minutes'] == 90  # max(phil=5, anya=90)

    def test_last_sang_none_when_a_member_never_sang(self, rotation_client, mock_rotation):
        # Only members who have sung are in the dict; if none has, it's None.
        mock_rotation.store.get_last_sang_times.return_value = {}
        mock_rotation.get_rotation.return_value = [
            {"id": 1, "position": 1, "singer": "Phil & Anya",
             "singers_json": '["Phil", "Anya"]', "status": "Waiting",
             "song_artist": "Duet", "notes": "", "file_path": None, "duration": None},
        ]
        resp = rotation_client.get('/rotation')
        entries = resp.get_json()['entries']
        assert entries[0]['last_sang_minutes'] is None


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

    def test_set_paid_string_value_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/set-paid',
            data=json.dumps({"id": 1, "paid": "true"}),
            content_type='application/json')
        assert resp.status_code == 400
        assert "boolean" in resp.get_json()["error"]

    def test_set_paid_not_configured(self, no_rotation_client):
        resp = no_rotation_client.post('/rotation/set-paid',
            data=json.dumps({"id": 1, "paid": True}),
            content_type='application/json')
        assert resp.status_code == 503


class TestSingerStats:
    def test_rotation_includes_singer_stats(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = [
            {"name": "Alice", "entries_total": 2, "entries_sung": 1,
             "entries_waiting": 1, "entries_left": 0, "first_added": "2026-04-14 20:00:00",
             "has_tipped": False, "status": "active"},
        ]
        resp = rotation_client.get('/rotation')
        data = resp.get_json()
        assert 'singer_stats' in data
        assert len(data['singer_stats']) == 1
        assert data['singer_stats'][0]['name'] == 'Alice'


class TestSingerRenameRoute:
    def test_rename_singer(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/rename',
            data=json.dumps({"old_name": "Phill", "new_name": "Phil"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.rename_singer.assert_called_once_with("Phill", "Phil")

    def test_rename_missing_params(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/singer/rename',
            data=json.dumps({"old_name": "Phil"}),
            content_type='application/json')
        assert resp.status_code == 400


class TestSingerMergeRoute:
    def test_merge_singers(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/merge',
            data=json.dumps({"source_name": "Phill", "target_name": "Phil"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.merge_singers.assert_called_once_with("Phill", "Phil")


class TestSingerBrbRoute:
    def test_brb_toggle(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/brb',
            data=json.dumps({"name": "Alice", "brb": True}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.set_singer_status.assert_called_once_with("Alice", "On Hold (BRB)")

    def test_brb_restore(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/brb',
            data=json.dumps({"name": "Alice", "brb": False}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.set_singer_status.assert_called_once_with("Alice", "Waiting")


class TestSingerRemoveRoute:
    def test_remove_singer(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/remove',
            data=json.dumps({"name": "Alice"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.set_singer_status.assert_called_once_with("Alice", "Left")
        mock_rotation.mark_singer_left.assert_called_once_with("Alice")


class TestSingerRestoreRoute:
    def test_restore_singer(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/restore',
            data=json.dumps({"name": "Alice"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.set_singer_status.assert_called_once_with("Alice", "Waiting")
        mock_rotation.unmark_singer_left.assert_called_once_with("Alice")


class TestSingerSplitRoute:
    def test_split_success(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/split',
            data=json.dumps({
                "source_name": "Kai",
                "new_name": "Kai P",
                "entry_ids": [1, 2],
            }),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.split_singer.assert_called_once_with("Kai", "Kai P", [1, 2])

    def test_split_missing_source_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/singer/split',
            data=json.dumps({"new_name": "X", "entry_ids": [1]}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_split_missing_new_name_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/singer/split',
            data=json.dumps({"source_name": "X", "entry_ids": [1]}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_split_missing_entry_ids_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/singer/split',
            data=json.dumps({"source_name": "X", "new_name": "Y"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_split_empty_entry_ids_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/singer/split',
            data=json.dumps({
                "source_name": "X", "new_name": "Y", "entry_ids": [],
            }),
            content_type='application/json')
        assert resp.status_code == 400

    def test_split_same_name_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/singer/split',
            data=json.dumps({
                "source_name": "Kai", "new_name": "  kai  ", "entry_ids": [1],
            }),
            content_type='application/json')
        assert resp.status_code == 400
