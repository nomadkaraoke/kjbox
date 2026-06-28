"""Tests for the playability hard-gate on the /upload route."""

import io
from unittest.mock import MagicMock, patch

import pytest

from app import create_app


@pytest.fixture
def upload_app(mock_config):
    """Flask app wired for upload tests."""
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    # Attach a mock MediaIndex so scan() can be inspected.
    app.media = MagicMock()
    yield app
    app.catalog.close()


@pytest.fixture
def upload_client(upload_app):
    with upload_app.test_client() as client:
        yield client


def _multipart(filename='song.mp4', content=b'fake video data'):
    """Build a minimal multipart form payload."""
    return {
        'file': (io.BytesIO(content), filename),
    }


class TestUploadGateReject:
    """Gate rejects unplayable files: 422, file deleted, scan NOT called."""

    def test_returns_422(self, upload_app, upload_client, tmp_path):
        bad_verdict = {'overall_ok': False, 'reasons': ['truncated file']}
        mock_result = MagicMock()
        mock_result.verdict = bad_verdict

        with patch('routes._playability_gate', return_value=mock_result):
            resp = upload_client.post(
                '/upload',
                data=_multipart(),
                content_type='multipart/form-data',
            )

        assert resp.status_code == 422

    def test_error_message_contains_reason(self, upload_app, upload_client):
        bad_verdict = {'overall_ok': False, 'reasons': ['audio stream only']}
        mock_result = MagicMock()
        mock_result.verdict = bad_verdict

        with patch('routes._playability_gate', return_value=mock_result):
            resp = upload_client.post(
                '/upload',
                data=_multipart(),
                content_type='multipart/form-data',
            )

        data = resp.get_json()
        assert 'error' in data
        assert 'audio stream only' in data['error']

    def test_verdict_included_in_response(self, upload_app, upload_client):
        bad_verdict = {'overall_ok': False, 'reasons': ['truncated file']}
        mock_result = MagicMock()
        mock_result.verdict = bad_verdict

        with patch('routes._playability_gate', return_value=mock_result):
            resp = upload_client.post(
                '/upload',
                data=_multipart(),
                content_type='multipart/form-data',
            )

        data = resp.get_json()
        assert data['verdict'] == bad_verdict

    def test_saved_file_deleted(self, upload_app, upload_client):
        """Rejected file must be removed from disk."""
        import os

        bad_verdict = {'overall_ok': False, 'reasons': ['truncated file']}
        mock_result = MagicMock()
        mock_result.verdict = bad_verdict

        deleted_paths = []
        original_remove = os.remove

        def spy_remove(path):
            deleted_paths.append(path)
            try:
                original_remove(path)
            except FileNotFoundError:
                pass

        with patch('routes._playability_gate', return_value=mock_result), \
             patch('routes.os.remove', side_effect=spy_remove):
            upload_client.post(
                '/upload',
                data=_multipart(),
                content_type='multipart/form-data',
            )

        assert len(deleted_paths) == 1
        assert deleted_paths[0].endswith('song.mp4')

    def test_media_scan_not_called(self, upload_app, upload_client):
        """On rejection, the media index must NOT be updated."""
        bad_verdict = {'overall_ok': False, 'reasons': ['truncated file']}
        mock_result = MagicMock()
        mock_result.verdict = bad_verdict

        with patch('routes._playability_gate', return_value=mock_result):
            upload_client.post(
                '/upload',
                data=_multipart(),
                content_type='multipart/form-data',
            )

        upload_app.media.scan.assert_not_called()

    def test_missing_reasons_uses_fallback_message(self, upload_app, upload_client):
        """When reasons list is empty, a generic fallback appears in the error."""
        bad_verdict = {'overall_ok': False, 'reasons': []}
        mock_result = MagicMock()
        mock_result.verdict = bad_verdict

        with patch('routes._playability_gate', return_value=mock_result):
            resp = upload_client.post(
                '/upload',
                data=_multipart(),
                content_type='multipart/form-data',
            )

        data = resp.get_json()
        assert 'not playable' in data['error']


class TestUploadGatePass:
    """Gate allows good files: 200, scan called, filename returned."""

    def test_returns_200(self, upload_app, upload_client):
        good_verdict = {'overall_ok': True, 'reasons': []}
        mock_result = MagicMock()
        mock_result.verdict = good_verdict

        with patch('routes._playability_gate', return_value=mock_result):
            resp = upload_client.post(
                '/upload',
                data=_multipart(),
                content_type='multipart/form-data',
            )

        assert resp.status_code == 200

    def test_success_body(self, upload_app, upload_client):
        good_verdict = {'overall_ok': True, 'reasons': []}
        mock_result = MagicMock()
        mock_result.verdict = good_verdict

        with patch('routes._playability_gate', return_value=mock_result):
            resp = upload_client.post(
                '/upload',
                data=_multipart(),
                content_type='multipart/form-data',
            )

        data = resp.get_json()
        assert data['success'] is True
        assert 'filename' in data

    def test_media_scan_called(self, upload_app, upload_client):
        """On success, media.scan() must be called to update the index."""
        good_verdict = {'overall_ok': True, 'reasons': []}
        mock_result = MagicMock()
        mock_result.verdict = good_verdict

        with patch('routes._playability_gate', return_value=mock_result):
            upload_client.post(
                '/upload',
                data=_multipart(),
                content_type='multipart/form-data',
            )

        upload_app.media.scan.assert_called_once()
