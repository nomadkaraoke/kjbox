"""Tests for the download-and-link rotation endpoint."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app import create_app


@pytest.fixture
def dl_app(mock_config):
    mock_config["rotation_db_path"] = ":memory:"
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    yield app
    app.catalog.close()


@pytest.fixture
def dl_client(dl_app):
    with dl_app.test_client() as client:
        yield client


class TestDownloadAndLink:
    def test_divebar_download(self, dl_client, dl_app):
        """Divebar download sets download_source and queues download."""
        # First add a singer
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice", "song_artist": "Bohemian Rhapsody"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]

        with patch('routes.divebar.get_download_url', return_value="https://storage.googleapis.com/test"):
            resp = dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "id": entry_id,
                    "source": "divebar",
                    "file_id": "abc123",
                    "filename": "KFN-1234 - Queen - Bohemian Rhapsody.mp4"
                }),
                content_type='application/json')
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["entry"]["download_source"] == "divebar"
            assert data["entry"]["download_status"] == "queued"

    def test_youtube_download(self, dl_client, dl_app):
        """YouTube download sets download_source and queues download."""
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Bob", "song_artist": "Song B"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]

        resp = dl_client.post('/rotation/download-and-link',
            data=json.dumps({
                "id": entry_id,
                "source": "youtube",
                "youtube_url": "https://youtube.com/watch?v=abc",
                "filename": "KV-5678 - Queen - Bohemian Rhapsody.mp4"
            }),
            content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["entry"]["download_source"] == "youtube"

    def test_missing_source_returns_400(self, dl_client, dl_app):
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]

        resp = dl_client.post('/rotation/download-and-link',
            data=json.dumps({"id": entry_id}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_missing_id_and_singer_returns_400(self, dl_client):
        resp = dl_client.post('/rotation/download-and-link',
            data=json.dumps({"source": "divebar", "file_id": "abc"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_creates_entry_if_singer_provided(self, dl_client, dl_app):
        """If id is omitted but singer provided, creates entry first."""
        with patch('routes.divebar.get_download_url', return_value="https://storage.googleapis.com/test"):
            resp = dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "source": "divebar",
                    "file_id": "abc123",
                    "filename": "KFN-1234 - Queen - Bohemian Rhapsody.mp4",
                    "singer": "Alice",
                    "song_artist": "Bohemian Rhapsody - Queen"
                }),
                content_type='application/json')
            assert resp.status_code == 200
            assert resp.get_json()["entry"]["singer"] == "Alice"

    def test_unknown_source_returns_400(self, dl_client, dl_app):
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]

        resp = dl_client.post('/rotation/download-and-link',
            data=json.dumps({"id": entry_id, "source": "invalid"}),
            content_type='application/json')
        assert resp.status_code == 400
