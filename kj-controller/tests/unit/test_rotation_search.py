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
        """KN results are enriched with Divebar availability via search."""
        with patch.object(search_app.catalog, 'search', return_value=[]), \
             patch('routes.karaoke_nerds.search', return_value=[
                 {"title": "Bohemian Rhapsody", "artist": "Queen", "tracks": [
                     {"brand_name": "KFN", "brand_code": "KFN-1234",
                      "youtube_url": "https://youtube.com/watch?v=abc", "is_community": True}
                 ]}
             ]), \
             patch('routes.divebar.search', return_value=[
                 {"artist": "Queen", "title": "Bohemian Rhapsody", "tracks": [
                     {"file_id": "abc123", "brand_code": "KFN-1234", "format": "mp4"}
                 ]}
             ]):
            resp = search_client.get('/rotation/search?q=bohemian')
            data = resp.get_json()
            track = data["karaoke_nerds"][0]["tracks"][0]
            assert "divebar" in track
            assert track["divebar"]["file_id"] == "abc123"

    def test_rotation_search_includes_downloaded_media(self, search_client, search_app):
        """Files in media.index but not in catalog appear in local results."""
        search_app.media.index = {
            "/downloads/Queen - Bohemian Rhapsody.mp4": {
                "filename": "Queen - Bohemian Rhapsody.mp4",
                "display_name": "Queen - Bohemian Rhapsody",
                "duration": 355,
            }
        }
        with patch.object(search_app.catalog, 'is_available', return_value=True), \
             patch.object(search_app.catalog, 'search', return_value=[]), \
             patch('routes.karaoke_nerds.search', return_value=[]):
            resp = search_client.get('/rotation/search?q=bohemian')
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data["local"]) == 1
            assert data["local"][0]["path"] == "/downloads/Queen - Bohemian Rhapsody.mp4"

    def test_rotation_search_downloaded_media_fields(self, search_client, search_app):
        """Downloaded media results contain path, filename, artist, title, disc_id, format."""
        search_app.media.index = {
            "/downloads/SC1234 - Artist Name - Song Title.mp4": {
                "filename": "SC1234 - Artist Name - Song Title.mp4",
                "display_name": "SC1234 - Artist Name - Song Title",
                "duration": 240,
            }
        }
        with patch.object(search_app.catalog, 'is_available', return_value=True), \
             patch.object(search_app.catalog, 'search', return_value=[]), \
             patch('routes.karaoke_nerds.search', return_value=[]):
            resp = search_client.get('/rotation/search?q=artist song')
            data = resp.get_json()
            assert len(data["local"]) == 1
            result = data["local"][0]
            assert result["path"] == "/downloads/SC1234 - Artist Name - Song Title.mp4"
            assert result["filename"] == "SC1234 - Artist Name - Song Title.mp4"
            assert result["artist"] == "Artist Name"
            assert result["title"] == "Song Title"
            assert result["disc_id"] == "SC1234"
            assert result["format"] == "mp4"

    def test_rotation_search_all_terms_must_match(self, search_client, search_app):
        """'queen bohemian' only matches files containing BOTH terms."""
        search_app.media.index = {
            "/downloads/Queen - Bohemian Rhapsody.mp4": {
                "filename": "Queen - Bohemian Rhapsody.mp4",
                "display_name": "Queen - Bohemian Rhapsody",
            },
            "/downloads/Queen - We Will Rock You.mp4": {
                "filename": "Queen - We Will Rock You.mp4",
                "display_name": "Queen - We Will Rock You",
            },
        }
        with patch.object(search_app.catalog, 'is_available', return_value=True), \
             patch.object(search_app.catalog, 'search', return_value=[]), \
             patch('routes.karaoke_nerds.search', return_value=[]):
            resp = search_client.get('/rotation/search?q=queen bohemian')
            data = resp.get_json()
            assert len(data["local"]) == 1
            assert "Bohemian" in data["local"][0]["filename"]

    def test_rotation_search_media_index_punctuation(self, search_client, search_app):
        """Punctuation in query or filename doesn't prevent media index matches."""
        search_app.media.index = {
            "/downloads/Set It Off - Wolf In Sheep's Clothing.mp4": {
                "filename": "Set It Off - Wolf In Sheep's Clothing.mp4",
                "display_name": "Set It Off - Wolf In Sheep's Clothing",
            },
        }
        with patch.object(search_app.catalog, 'is_available', return_value=True), \
             patch.object(search_app.catalog, 'search', return_value=[]), \
             patch('routes.karaoke_nerds.search', return_value=[]):
            # Query without apostrophe matches filename with apostrophe
            resp = search_client.get('/rotation/search?q=wolf sheeps clothing')
            data = resp.get_json()
            assert len(data["local"]) == 1
            assert "Sheep" in data["local"][0]["filename"]

    def test_rotation_search_no_duplicates(self, search_client, search_app):
        """A file in both catalog and media.index only appears once."""
        catalog_result = {
            "path": "/downloads/Queen - Bohemian Rhapsody.mp4",
            "artist": "Queen",
            "title": "Bohemian Rhapsody",
            "format": "mp4",
            "disc_id": "",
        }
        search_app.media.index = {
            "/downloads/Queen - Bohemian Rhapsody.mp4": {
                "filename": "Queen - Bohemian Rhapsody.mp4",
                "display_name": "Queen - Bohemian Rhapsody",
            }
        }
        with patch.object(search_app.catalog, 'is_available', return_value=True), \
             patch.object(search_app.catalog, 'search', return_value=[catalog_result]), \
             patch('routes.karaoke_nerds.search', return_value=[]):
            resp = search_client.get('/rotation/search?q=bohemian')
            data = resp.get_json()
            paths = [r["path"] for r in data["local"]]
            assert paths.count("/downloads/Queen - Bohemian Rhapsody.mp4") == 1

    def test_rotation_search_divebar_exception_graceful(self, search_client, search_app):
        """If divebar.search raises, rotation search still returns results."""
        with patch.object(search_app.catalog, 'is_available', return_value=True), \
             patch.object(search_app.catalog, 'search', return_value=[
                 {"path": "/media/song.zip", "artist": "Queen", "title": "Bohemian Rhapsody",
                  "format": "cdg+mp3", "disc_id": "ASK-002204"}
             ]), \
             patch('routes.karaoke_nerds.search', return_value=[
                 {"title": "Bohemian Rhapsody", "artist": "Queen", "tracks": [
                     {"brand_name": "KFN", "brand_code": "KFN-1234",
                      "youtube_url": "https://youtube.com/watch?v=abc", "is_community": True}
                 ]}
             ]), \
             patch('routes.divebar.search', side_effect=Exception("connection refused")):
            resp = search_client.get('/rotation/search?q=bohemian')
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data["local"]) >= 1
            assert len(data["karaoke_nerds"]) >= 1


class TestPriorityAnnotation:
    def test_local_results_annotated(self, search_client, search_app):
        with patch.object(search_app.catalog, 'is_available', return_value=True), \
             patch.object(search_app.catalog, 'search', return_value=[
                 {"path": "/media/song.zip", "artist": "Queen",
                  "title": "Bohemian Rhapsody", "format": "cdg+mp3",
                  "disc_id": "KVD-22524",
                  "filename": "KVD-22524 - Queen - Bohemian Rhapsody.zip"}
             ]), \
             patch('routes.karaoke_nerds.search', return_value=[]):
            resp = search_client.get('/rotation/search?q=bohemian')
            data = resp.get_json()
            assert data["local"][0]["priority_brand"] == "KV"
            assert data["local"][0]["priority_class"] == "commercial"
            assert "priority_rank" in data["local"][0]

    def test_kn_tracks_annotated(self, search_client, search_app):
        with patch.object(search_app.catalog, 'is_available', return_value=True), \
             patch.object(search_app.catalog, 'search', return_value=[]), \
             patch('routes.karaoke_nerds.search', return_value=[
                 {"title": "Bohemian Rhapsody", "artist": "Queen", "tracks": [
                     {"brand_name": "Lemmy Caution", "brand_code": "LC",
                      "youtube_url": "https://youtube.com/watch?v=abc",
                      "is_community": True}
                 ]}
             ]):
            resp = search_client.get('/rotation/search?q=bohemian')
            data = resp.get_json()
            track = data["karaoke_nerds"][0]["tracks"][0]
            assert track["priority_brand"] == "LC"
            assert track["priority_class"] == "community"
            assert "priority_rank" in track
