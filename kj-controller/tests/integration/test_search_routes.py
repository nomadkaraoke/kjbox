"""Integration tests for search, catalog, and ZIP playback routes."""

import json
import os
import zipfile

import pytest


class TestSearchRoute:
    def test_search_catalog_not_available(self, flask_test_client):
        """GET /search returns 503 when catalog is not built."""
        response = flask_test_client.get('/search?q=test')
        assert response.status_code == 503

    def test_search_empty_query(self, flask_test_client, flask_app, sample_file_list):
        """GET /search with empty query returns empty list."""
        flask_app.catalog.build_from_file_list(sample_file_list)
        response = flask_test_client.get('/search?q=')
        assert response.status_code == 200
        assert json.loads(response.data) == []

    def test_search_returns_results(self, flask_test_client, flask_app, sample_file_list):
        """GET /search returns matching results."""
        flask_app.catalog.build_from_file_list(sample_file_list)
        response = flask_test_client.get('/search?q=Bon+Jovi')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) >= 1
        assert any('Bon Jovi' in r.get('artist', '') for r in data)

    def test_search_with_limit(self, flask_test_client, flask_app, sample_file_list):
        """GET /search respects limit parameter."""
        flask_app.catalog.build_from_file_list(sample_file_list)
        response = flask_test_client.get('/search?q=a&limit=2')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) <= 2

    def test_search_with_offset(self, flask_test_client, flask_app, sample_file_list):
        """GET /search respects offset parameter."""
        flask_app.catalog.build_from_file_list(sample_file_list)
        response = flask_test_client.get('/search?q=a&limit=50&offset=0')
        assert response.status_code == 200

    def test_search_limit_clamped(self, flask_test_client, flask_app, sample_file_list):
        """GET /search clamps limit to 200."""
        flask_app.catalog.build_from_file_list(sample_file_list)
        response = flask_test_client.get('/search?q=a&limit=999')
        assert response.status_code == 200

    def test_search_invalid_limit(self, flask_test_client, flask_app, sample_file_list):
        """GET /search handles non-numeric limit gracefully."""
        flask_app.catalog.build_from_file_list(sample_file_list)
        response = flask_test_client.get('/search?q=bon&limit=abc')
        assert response.status_code == 200

    def test_search_invalid_offset(self, flask_test_client, flask_app, sample_file_list):
        """GET /search handles non-numeric offset gracefully."""
        flask_app.catalog.build_from_file_list(sample_file_list)
        response = flask_test_client.get('/search?q=bon&offset=xyz')
        assert response.status_code == 200


class TestCatalogStatsRoute:
    def test_stats_not_available(self, flask_test_client):
        """GET /catalog/stats when catalog not built."""
        response = flask_test_client.get('/catalog/stats')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['available'] is False
        assert data['total'] == 0

    def test_stats_available(self, flask_test_client, flask_app, sample_file_list):
        """GET /catalog/stats after build returns stats."""
        flask_app.catalog.build_from_file_list(sample_file_list)
        response = flask_test_client.get('/catalog/stats')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['available'] is True
        assert data['total'] == 5
        assert 'by_format' in data


class TestCatalogBuildRoute:
    def test_build_no_path(self, flask_test_client):
        """POST /catalog/build without file_list_path returns 400."""
        response = flask_test_client.post('/catalog/build',
            data=json.dumps({}),
            content_type='application/json')
        assert response.status_code == 400

    def test_build_file_not_found(self, flask_test_client):
        """POST /catalog/build with nonexistent file returns 404."""
        response = flask_test_client.post('/catalog/build',
            data=json.dumps({"file_list_path": "/nonexistent/file.txt"}),
            content_type='application/json')
        assert response.status_code == 404

    def test_build_success(self, flask_test_client, flask_app, sample_file_list):
        """POST /catalog/build creates catalog from file list."""
        response = flask_test_client.post('/catalog/build',
            data=json.dumps({"file_list_path": sample_file_list}),
            content_type='application/json')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True
        assert data['count'] == 5

    def test_build_uses_config_path(self, flask_test_client, flask_app, sample_file_list):
        """POST /catalog/build falls back to config external_file_list."""
        flask_app.kj_config['external_file_list'] = sample_file_list
        response = flask_test_client.post('/catalog/build',
            data=json.dumps({}),
            content_type='application/json')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True

    def test_build_with_mount_replace(self, flask_test_client, flask_app, sample_file_list):
        """POST /catalog/build rewrites paths when external_media_mount is set."""
        flask_app.kj_config['external_media_mount'] = '/mnt/Nomad4TBOne'
        response = flask_test_client.post('/catalog/build',
            data=json.dumps({"file_list_path": sample_file_list}),
            content_type='application/json')
        assert response.status_code == 200
        # Verify paths were rewritten
        results = flask_app.catalog.search("Bon Jovi")
        assert len(results) >= 1
        assert results[0]['path'].startswith('/mnt/Nomad4TBOne/')


class TestPlayWithZip:
    def test_play_zip_no_vlc(self, flask_test_client, flask_app, tmp_media_dir):
        """POST /play with ZIP returns 503 when VLC disabled (after path validation)."""
        # Create ZIP in media folder
        media_dir = tmp_media_dir / "media"
        zip_path = media_dir / "song.zip"
        with zipfile.ZipFile(str(zip_path), 'w') as zf:
            zf.writestr("song.cdg", b"fake cdg")
            zf.writestr("song.mp3", b"fake mp3")

        response = flask_test_client.post('/play',
            data=json.dumps({"file_path": str(zip_path)}),
            content_type='application/json')
        assert response.status_code == 503

    def test_play_zip_with_vlc(self, flask_test_client, flask_app, tmp_media_dir, mocker):
        """POST /play with ZIP extracts CDG and plays it."""
        media_dir = tmp_media_dir / "media"
        zip_path = media_dir / "song.zip"
        with zipfile.ZipFile(str(zip_path), 'w') as zf:
            zf.writestr("song.cdg", b"fake cdg")
            zf.writestr("song.mp3", b"fake mp3")

        flask_app.vlc.enabled = True
        mocker.patch.object(flask_app.vlc, 'play_video')

        response = flask_test_client.post('/play',
            data=json.dumps({"file_path": str(zip_path)}),
            content_type='application/json')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True

    def test_play_zip_no_mp3(self, flask_test_client, flask_app, tmp_media_dir):
        """POST /play with ZIP missing .mp3 returns 400."""
        media_dir = tmp_media_dir / "media"
        zip_path = media_dir / "nomp3.zip"
        with zipfile.ZipFile(str(zip_path), 'w') as zf:
            zf.writestr("song.cdg", b"fake cdg")

        flask_app.vlc.enabled = True

        response = flask_test_client.post('/play',
            data=json.dumps({"file_path": str(zip_path)}),
            content_type='application/json')
        assert response.status_code == 400

    def test_play_external_path(self, flask_test_client, flask_app, tmp_path, mocker):
        """POST /play validates external media mount paths."""
        ext_dir = tmp_path / "external"
        ext_dir.mkdir()
        test_file = ext_dir / "song.mp4"
        test_file.write_text("fake video")

        flask_app.kj_config['external_media_mount'] = str(ext_dir)
        flask_app.vlc.enabled = True
        mocker.patch.object(flask_app.vlc, 'play_video')

        response = flask_test_client.post('/play',
            data=json.dumps({"file_path": str(test_file)}),
            content_type='application/json')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True

    def test_play_external_path_not_in_mount(self, flask_test_client, flask_app, tmp_path):
        """POST /play rejects paths outside both media folders and external mount."""
        flask_app.kj_config['external_media_mount'] = '/some/other/mount'
        response = flask_test_client.post('/play',
            data=json.dumps({"file_path": "/tmp/not-allowed/song.mp4"}),
            content_type='application/json')
        assert response.status_code == 400


class TestPlaybackAlternates:
    """GET /playback/alternates — the "Try Another" version picker. Returns
    other LOCAL versions of the currently-playing song, current file excluded."""

    def _seed_two_versions(self, flask_app):
        # Two local files for the same song, discovered via the media index scan
        # (no external catalog needed — catalog stays unbuilt / unavailable).
        flask_app.media.index = {
            "/media/livin-a.mp4": {
                "filename": "Livin On A Prayer - Bon Jovi [A].mp4",
                "display_name": "Livin On A Prayer - Bon Jovi", "duration": 200},
            "/media/livin-b.cdg": {
                "filename": "Livin On A Prayer - Bon Jovi [B].cdg",
                "display_name": "Livin On A Prayer - Bon Jovi", "duration": 200},
        }
        entry = flask_app.rotation.add_entry(
            "Alice", "Livin On A Prayer - Bon Jovi", file_path="/media/livin-a.mp4")
        return entry

    def test_lists_other_versions_excluding_current(self, flask_test_client, flask_app):
        entry = self._seed_two_versions(flask_app)
        flask_app.vlc.current_playing_path = "/media/livin-a.mp4"

        resp = flask_test_client.get('/playback/alternates')
        assert resp.status_code == 200
        d = resp.get_json()
        assert d["playing"] is True
        assert d["entry_id"] == entry["id"]
        assert d["current_path"] == "/media/livin-a.mp4"
        paths = [a["path"] for a in d["alternates"]]
        assert "/media/livin-b.cdg" in paths        # the other version surfaced
        assert "/media/livin-a.mp4" not in paths     # current version filtered out

    def test_nothing_playing_returns_empty(self, flask_test_client, flask_app):
        flask_app.vlc.current_playing_path = None
        resp = flask_test_client.get('/playback/alternates')
        assert resp.status_code == 200
        d = resp.get_json()
        assert d["playing"] is False
        assert d["alternates"] == []

    def test_resolves_current_entry_by_now_singing_status(self, flask_test_client, flask_app):
        # When the player path doesn't match any entry's file_path (e.g. a ZIP
        # was unpacked to a temp file), fall back to the "Now Singing" entry.
        self._seed_two_versions(flask_app)
        entry = flask_app.rotation.add_entry(
            "Bob", "Livin On A Prayer - Bon Jovi", file_path="/media/livin-b.cdg")
        flask_app.rotation.update_status(entry["id"], "Now Singing")
        flask_app.vlc.current_playing_path = "/tmp/unpacked/livin.cdg"

        resp = flask_test_client.get('/playback/alternates')
        d = resp.get_json()
        assert d["playing"] is True
        assert d["entry_id"] == entry["id"]
