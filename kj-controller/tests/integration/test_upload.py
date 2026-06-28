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

    def test_upload_valid_media_file(self, upload_client, upload_app):
        """Upload an .mp4 file returns 200 with success, filename, path, and file on disk."""
        resp = upload_client.post(
            '/upload',
            data=_make_file_data('test_song.mp4'),
            content_type='multipart/form-data',
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['filename'] == 'test_song.mp4'
        assert data['path'].endswith('test_song.mp4')
        assert os.path.exists(data['path'])
        with open(data['path'], 'rb') as f:
            assert f.read() == b"fake video data"

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

    def test_upload_sanitizes_filename(self, upload_client, upload_app):
        """Special characters in filename are stripped."""
        resp = upload_client.post(
            '/upload',
            data=_make_file_data('my $ong @#!.mp4'),
            content_type='multipart/form-data',
        )
        assert resp.status_code == 200
        data = resp.get_json()
        # Special chars ($, @, #, !) should be removed
        assert '$' not in data['filename']
        assert '@' not in data['filename']
        assert '#' not in data['filename']
        assert '!' not in data['filename']
        assert data['filename'].endswith('.mp4')
        assert os.path.exists(data['path'])

    def test_upload_avoids_overwrite(self, upload_client, upload_app):
        """Uploading the same filename twice gives the second a timestamp suffix."""
        # First upload
        resp1 = upload_client.post(
            '/upload',
            data=_make_file_data('duplicate.mp4', content=b"first"),
            content_type='multipart/form-data',
        )
        assert resp1.status_code == 200
        name1 = resp1.get_json()['filename']

        # Second upload with same filename
        resp2 = upload_client.post(
            '/upload',
            data=_make_file_data('duplicate.mp4', content=b"second"),
            content_type='multipart/form-data',
        )
        assert resp2.status_code == 200
        name2 = resp2.get_json()['filename']

        assert name1 != name2
        assert name2.startswith('duplicate_')
        assert name2.endswith('.mp4')
        # Both files exist
        assert os.path.exists(resp1.get_json()['path'])
        assert os.path.exists(resp2.get_json()['path'])

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
