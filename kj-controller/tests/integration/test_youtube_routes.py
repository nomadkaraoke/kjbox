"""Integration tests for YouTube settings routes."""

import json
import os

VALID_COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc123\n"
    ".google.com\tTRUE\t/\tTRUE\t1893456000\tNID\txyz789\n"
)


def test_youtube_status(flask_test_client):
    """GET /youtube/status returns health info."""
    resp = flask_test_client.get('/youtube/status')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert 'ytdlp_version' in data
    assert 'ejs_installed' in data
    assert 'deno_available' in data
    assert 'cookies_present' in data
    assert 'cookies_valid' in data


def test_youtube_upload_cookies(flask_test_client, flask_app):
    """POST /youtube/cookies validates and saves cookies."""
    resp = flask_test_client.post('/youtube/cookies',
        data=json.dumps({'content': VALID_COOKIES}),
        content_type='application/json')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['success'] is True

    # Verify file was written
    cookies_path = flask_app.kj_config.get('youtube_cookies_file', '')
    assert os.path.exists(cookies_path)


def test_youtube_upload_cookies_invalid_format(flask_test_client):
    """POST /youtube/cookies rejects invalid cookie format."""
    resp = flask_test_client.post('/youtube/cookies',
        data=json.dumps({'content': 'not a cookie file'}),
        content_type='application/json')
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert 'error' in data


def test_youtube_upload_cookies_empty(flask_test_client):
    """POST /youtube/cookies rejects empty content."""
    resp = flask_test_client.post('/youtube/cookies',
        data=json.dumps({'content': ''}),
        content_type='application/json')
    assert resp.status_code == 400


def test_youtube_upload_cookies_no_body(flask_test_client):
    """POST /youtube/cookies rejects missing content field."""
    resp = flask_test_client.post('/youtube/cookies',
        data=json.dumps({}),
        content_type='application/json')
    assert resp.status_code == 400


def test_youtube_delete_cookies_when_none(flask_test_client):
    """DELETE /youtube/cookies succeeds even when no file exists."""
    resp = flask_test_client.delete('/youtube/cookies')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['success'] is True


def test_youtube_delete_cookies_after_upload(flask_test_client, flask_app):
    """DELETE /youtube/cookies removes previously uploaded cookies."""
    # Upload first
    flask_test_client.post('/youtube/cookies',
        data=json.dumps({'content': VALID_COOKIES}),
        content_type='application/json')

    cookies_path = flask_app.kj_config.get('youtube_cookies_file', '')
    assert os.path.exists(cookies_path)

    # Delete
    resp = flask_test_client.delete('/youtube/cookies')
    assert resp.status_code == 200
    assert not os.path.exists(cookies_path)


def test_youtube_status_includes_outdated_field(flask_test_client):
    """GET /youtube/status includes ytdlp_outdated boolean."""
    resp = flask_test_client.get('/youtube/status')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert 'ytdlp_outdated' in data
    assert isinstance(data['ytdlp_outdated'], bool)
    assert 'ytdlp_latest' in data


def test_youtube_upgrade_ytdlp(flask_test_client, mocker):
    """POST /youtube/upgrade-ytdlp upgrades yt-dlp and triggers restart."""
    mocker.patch('youtube_health.upgrade_ytdlp', return_value=(True, 'yt-dlp upgraded to 2026.3.3'))
    mock_thread = mocker.patch('routes.threading.Thread')
    resp = flask_test_client.post('/youtube/upgrade-ytdlp')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['success'] is True
    assert data['restarting'] is True
    mock_thread.assert_called_once()


def test_youtube_upgrade_ytdlp_failure(flask_test_client, mocker):
    """POST /youtube/upgrade-ytdlp returns 500 on failure."""
    mocker.patch('youtube_health.upgrade_ytdlp', return_value=(False, 'pip upgrade failed'))
    resp = flask_test_client.post('/youtube/upgrade-ytdlp')
    assert resp.status_code == 500
    data = json.loads(resp.data)
    assert 'error' in data


def test_youtube_status_reflects_cookies(flask_test_client, flask_app):
    """GET /youtube/status shows cookies after upload."""
    # Before upload
    resp = flask_test_client.get('/youtube/status')
    data = json.loads(resp.data)
    assert data['cookies_present'] is False

    # Upload
    flask_test_client.post('/youtube/cookies',
        data=json.dumps({'content': VALID_COOKIES}),
        content_type='application/json')

    # After upload
    resp = flask_test_client.get('/youtube/status')
    data = json.loads(resp.data)
    assert data['cookies_present'] is True
    assert data['cookies_valid'] is True
