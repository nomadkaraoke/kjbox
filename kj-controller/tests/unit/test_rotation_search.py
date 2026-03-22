"""Tests for the unified rotation search endpoint."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app import create_app


@pytest.fixture
def search_app(mock_config):
    mock_config["rotation_db_path"] = ":memory:"
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    yield app
    app.catalog.close()


@pytest.fixture
def search_client(search_app):
    with search_app.test_client() as client:
        yield client


class TestUnifiedSearch:
    def test_returns_local_results(self, search_client, search_app):
        """Local catalog results returned under 'local' key."""
        with patch.object(search_app.catalog, 'is_available', return_value=True), \
             patch.object(search_app.catalog, 'search', return_value=[
            {"path": "/media/song.zip", "artist": "Queen", "title": "Bohemian Rhapsody",
             "format": "cdg+mp3", "disc_id": "ASK-002204"}
        ]):
            resp = search_client.get('/rotation/search?q=bohemian')
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data["local"]) == 1
            assert data["local"][0]["artist"] == "Queen"

    def test_returns_kn_results(self, search_client, search_app):
        """Karaoke Nerds results returned under 'karaoke_nerds' key."""
        with patch.object(search_app.catalog, 'search', return_value=[]), \
             patch('routes.karaoke_nerds.search', return_value=[
                 {"title": "Bohemian Rhapsody", "artist": "Queen", "tracks": [
                     {"brand_name": "KFN", "brand_code": "KFN-1234",
                      "youtube_url": "https://youtube.com/watch?v=abc", "is_community": True}
                 ]}
             ]):
            resp = search_client.get('/rotation/search?q=bohemian')
            data = resp.get_json()
            assert len(data["karaoke_nerds"]) == 1

    def test_empty_query_returns_400(self, search_client):
        resp = search_client.get('/rotation/search?q=')
        assert resp.status_code == 400

    def test_short_query_returns_400(self, search_client):
        resp = search_client.get('/rotation/search?q=bo')
        assert resp.status_code == 400

    def test_missing_query_returns_400(self, search_client):
        resp = search_client.get('/rotation/search')
        assert resp.status_code == 400

    def test_kn_timeout_returns_local_only(self, search_client, search_app):
        """If KN search times out, returns local results with timeout flag."""
        with patch.object(search_app.catalog, 'is_available', return_value=True), \
             patch.object(search_app.catalog, 'search', return_value=[
            {"path": "/media/song.zip", "artist": "Queen", "title": "Bohemian Rhapsody",
             "format": "cdg+mp3", "disc_id": "ASK-002204"}
        ]), patch('routes.karaoke_nerds.search', side_effect=Exception("timeout")):
            resp = search_client.get('/rotation/search?q=bohemian')
            data = resp.get_json()
            assert len(data["local"]) == 1
            assert data.get("karaoke_nerds_timeout") is True

    def test_divebar_cross_reference(self, search_client, search_app):
        """KN results are enriched with Divebar availability."""
        with patch.object(search_app.catalog, 'search', return_value=[]), \
             patch('routes.karaoke_nerds.search', return_value=[
                 {"title": "Bohemian Rhapsody", "artist": "Queen", "tracks": [
                     {"brand_name": "KFN", "brand_code": "KFN-1234",
                      "youtube_url": "https://youtube.com/watch?v=abc", "is_community": True}
                 ]}
             ]), \
             patch('routes.divebar.lookup_kn_ids', return_value={
                 "KFN-1234": [{"file_id": "abc123", "format": "mp4"}]
             }):
            resp = search_client.get('/rotation/search?q=bohemian')
            data = resp.get_json()
            track = data["karaoke_nerds"][0]["tracks"][0]
            assert "divebar" in track
            assert track["divebar"]["file_id"] == "abc123"
