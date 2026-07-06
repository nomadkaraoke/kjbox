"""Integration tests for POST /upload route."""

import io
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from app import create_app


@pytest.fixture
def upload_app(mock_config):
    """Create a Flask app for upload tests."""
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    yield app
    app.catalog.close()


@pytest.fixture
def upload_client(upload_app):
    """Flask test client for upload tests."""
    with upload_app.test_client() as client:
        yield client


def _make_file_data(filename, content=b"fake video data", field='file'):
    """Helper to create multipart file upload data."""
    return {field: (io.BytesIO(content), filename)}


class TestUpload:
    @pytest.fixture(autouse=True)
    def _pass_playability_gate(self):
        # These tests exercise upload behaviour, not the playability gate (which
        # has its own tests in test_upload_gate.py). The tiny fake files used here
        # aren't real media, so stub the gate to pass.
        from types import SimpleNamespace
        with patch('routes._playability_gate',
                   return_value=SimpleNamespace(verdict={"overall_ok": True, "reasons": []})):
            yield

    def test_upload_organizes_into_upload_subdir_with_token(self, upload_client, upload_app):
        """Upload an .mp4 → organized into upload/ with an [up-<hash>] token, a
        media_library row, and the bytes preserved (not left loose in the root)."""
        resp = upload_client.post(
            '/upload',
            data=_make_file_data('test_song.mp4'),
            content_type='multipart/form-data',
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        # Lives in the upload/ subdir, carries a content-addressed token.
        assert os.path.basename(os.path.dirname(data['path'])) == 'upload'
        assert '[up-' in data['filename']
        assert data['media_id'].startswith('up-')
        assert os.path.exists(data['path'])
        with open(data['path'], 'rb') as f:
            assert f.read() == b"fake video data"
        # A media_library row was upserted for it, pointing at the moved file.
        row = upload_app.media_library.get(data['media_id'])
        assert row is not None
        assert row['file_path'] == os.path.realpath(data['path'])

    def test_upload_leaves_nothing_loose_in_download_root(self, upload_client, upload_app):
        """Regression for the loose-file bug: after upload, the download-folder
        root holds no media files — the upload lives under upload/."""
        download_folder = upload_app.media.config['download_folder']
        resp = upload_client.post(
            '/upload',
            data=_make_file_data('loose_check.mp4'),
            content_type='multipart/form-data',
        )
        assert resp.status_code == 200
        loose = [f for f in os.listdir(download_folder)
                 if os.path.isfile(os.path.join(download_folder, f))
                 and f.lower().endswith('.mp4')]
        assert loose == [], f"upload left loose file(s) in root: {loose}"

    def test_upload_missing_file_returns_400(self, upload_client):
        """POST with no file part returns 400."""
        resp = upload_client.post(
            '/upload',
            data={},
            content_type='multipart/form-data',
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'error' in data

    def test_upload_empty_filename_returns_400(self, upload_client):
        """POST with file but empty filename returns 400."""
        resp = upload_client.post(
            '/upload',
            data={'file': (io.BytesIO(b"data"), '')},
            content_type='multipart/form-data',
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'error' in data

    def test_upload_unsupported_format_returns_400(self, upload_client):
        """Upload a .txt file returns 400 with 'Unsupported format'."""
        resp = upload_client.post(
            '/upload',
            data=_make_file_data('notes.txt'),
            content_type='multipart/form-data',
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'Unsupported format' in data['error']

    def test_upload_messy_name_still_organizes_safely(self, upload_client, upload_app):
        """A messy upload name is organized into upload/ with a token as a single
        filesystem-safe filename (no path separators), consistent with how every
        other source names files."""
        resp = upload_client.post(
            '/upload',
            data=_make_file_data('my song/with slash.mp4'),
            content_type='multipart/form-data',
        )
        assert resp.status_code == 200
        data = resp.get_json()
        # A single filename — the slash must not have created a subdirectory.
        assert '/' not in data['filename']
        assert '[up-' in data['filename']
        assert data['filename'].endswith('.mp4')
        assert os.path.basename(os.path.dirname(data['path'])) == 'upload'
        assert os.path.exists(data['path'])

    def test_upload_distinct_content_kept_separately(self, upload_client, upload_app):
        """Two uploads of the same name but DIFFERENT content are content-addressed
        to distinct [up-<hash>] identities and both persist (no clobber)."""
        resp1 = upload_client.post(
            '/upload',
            data=_make_file_data('duplicate.mp4', content=b"first"),
            content_type='multipart/form-data',
        )
        resp2 = upload_client.post(
            '/upload',
            data=_make_file_data('duplicate.mp4', content=b"second"),
            content_type='multipart/form-data',
        )
        assert resp1.status_code == 200 and resp2.status_code == 200
        d1, d2 = resp1.get_json(), resp2.get_json()
        assert d1['media_id'] != d2['media_id']
        assert '[up-' in d1['filename'] and '[up-' in d2['filename']
        assert os.path.exists(d1['path'])
        assert os.path.exists(d2['path'])

    def test_upload_same_content_is_idempotent(self, upload_client, upload_app):
        """Re-uploading identical bytes resolves to the same [up-<hash>] identity
        (content-addressed) rather than piling up timestamped duplicates."""
        payload = b"identical bytes"
        r1 = upload_client.post('/upload', data=_make_file_data('a.mp4', content=payload),
                                content_type='multipart/form-data')
        r2 = upload_client.post('/upload', data=_make_file_data('b.mp4', content=payload),
                                content_type='multipart/form-data')
        assert r1.get_json()['media_id'] == r2.get_json()['media_id']

    def test_upload_triggers_media_scan(self, upload_client, upload_app):
        """media.scan() is called after successful upload."""
        with patch.object(upload_app.media, 'scan') as mock_scan:
            resp = upload_client.post(
                '/upload',
                data=_make_file_data('scanned.mp4'),
                content_type='multipart/form-data',
            )
            assert resp.status_code == 200
            mock_scan.assert_called_once()

    def test_upload_sleep_mode_blocks(self, upload_client, upload_app):
        """Upload returns 409 when sleep mode is active."""
        sleep_mgr = MagicMock()
        sleep_mgr.is_sleeping.return_value = True
        upload_app.sleep_manager = sleep_mgr

        resp = upload_client.post(
            '/upload',
            data=_make_file_data('blocked.mp4'),
            content_type='multipart/form-data',
        )
        assert resp.status_code == 409
        data = resp.get_json()
        assert 'Sleep mode' in data['error']
