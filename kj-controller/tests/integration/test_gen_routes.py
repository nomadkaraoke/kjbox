"""Tests for gen-related rotation routes."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app import create_app


@pytest.fixture
def gen_app(mock_config):
    mock_config["rotation_db_path"] = ":memory:"
    mock_config["gen_api_url"] = "https://api.example.com"
    mock_config["gen_api_token"] = "test-token"
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    yield app
    app.catalog.close()


@pytest.fixture
def gen_client(gen_app):
    with gen_app.test_client() as client:
        yield client


@pytest.fixture
def no_gen_app(mock_config):
    mock_config["rotation_db_path"] = ":memory:"
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    yield app
    app.catalog.close()


@pytest.fixture
def no_gen_client(no_gen_app):
    with no_gen_app.test_client() as client:
        yield client


class TestMakeRotationEntry:
    def test_make_creates_job(self, gen_client, gen_app):
        # First add a singer
        resp = gen_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice", "song_artist": "Bohemian Rhapsody - Queen"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]

        with patch.object(gen_app.gen_client, 'create_job', return_value={
            "job_id": "gen-abc123", "status": "pending"
        }):
            resp = gen_client.post('/rotation/make',
                data=json.dumps({"id": entry_id, "artist": "Queen", "title": "Bohemian Rhapsody"}),
                content_type='application/json')
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["job_id"] == "gen-abc123"
            assert data["entry"]["gen_job_id"] == "gen-abc123"
            assert data["entry"]["gen_status"] == "processing"

    def test_make_creates_entry_if_singer_provided(self, gen_client, gen_app):
        with patch.object(gen_app.gen_client, 'create_job', return_value={
            "job_id": "gen-abc123", "status": "pending"
        }):
            resp = gen_client.post('/rotation/make',
                data=json.dumps({
                    "artist": "Queen", "title": "Bohemian Rhapsody",
                    "singer": "Alice"
                }),
                content_type='application/json')
            assert resp.status_code == 200
            assert resp.get_json()["entry"]["singer"] == "Alice"

    def test_make_creates_entry_from_singers_array(self, gen_client, gen_app):
        """Regression: the UI sends `singers` (plural array) from the pill
        input when creating a MAKE entry. Must work or the entry is lost."""
        with patch.object(gen_app.gen_client, 'create_job', return_value={
            "job_id": "gen-abc123", "status": "pending"
        }):
            resp = gen_client.post('/rotation/make',
                data=json.dumps({
                    "artist": "Queen", "title": "Bohemian Rhapsody",
                    "singers": ["Phil", "Anya"]
                }),
                content_type='application/json')
            assert resp.status_code == 200
            entry = resp.get_json()["entry"]
            assert entry["singer"] == "Phil & Anya"
            assert entry.get("singers_json") == '["Phil", "Anya"]'
            # song_artist fallback: "{artist} - {title}" when not supplied (P3 flip)
            assert entry["song_artist"] == "Queen - Bohemian Rhapsody"

    def test_make_missing_artist_returns_400(self, gen_client):
        resp = gen_client.post('/rotation/make',
            data=json.dumps({"title": "Song"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_make_missing_title_returns_400(self, gen_client):
        resp = gen_client.post('/rotation/make',
            data=json.dumps({"artist": "Artist"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_make_missing_id_and_singer_returns_400(self, gen_client):
        resp = gen_client.post('/rotation/make',
            data=json.dumps({"artist": "Queen", "title": "Song"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_make_not_configured_returns_503(self, no_gen_client):
        resp = no_gen_client.post('/rotation/make',
            data=json.dumps({"artist": "Queen", "title": "Song", "singer": "Alice"}),
            content_type='application/json')
        assert resp.status_code == 503


class TestGenStatus:
    def test_returns_active_entries(self, gen_client, gen_app):
        # Add and set gen status
        resp = gen_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice", "song_artist": "Song A"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]
        gen_app.rotation.set_gen_status(entry_id, "job-1", "processing")

        resp = gen_client.get('/rotation/gen-status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["gen_entries"]) == 1
        assert data["gen_entries"][0]["gen_job_id"] == "job-1"

    def test_returns_empty_when_no_active(self, gen_client):
        resp = gen_client.get('/rotation/gen-status')
        assert resp.status_code == 200
        assert resp.get_json()["gen_entries"] == []
