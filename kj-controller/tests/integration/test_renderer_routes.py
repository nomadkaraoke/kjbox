"""Integration tests for /renderer endpoints."""

import json


def test_get_renderer_returns_current_mode(flask_test_client):
    """GET /renderer returns current renderer state and capabilities."""
    response = flask_test_client.get('/renderer')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['mode'] == 'mpv'  # default
    assert data['supports_pitch'] is True
    assert data['supports_cdg'] is True
    assert 'mpv' in data['available_modes']
    assert 'vlc' in data['available_modes']


def test_post_renderer_switches_mode(flask_test_client, flask_app, mocker):
    """POST /renderer switches karaoke backend."""
    mocker.patch('playback.save_config_value')
    mocker.patch.object(flask_app.vlc, '_start_monitor')

    response = flask_test_client.post('/renderer',
        data=json.dumps({"mode": "vlc"}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['success'] is True
    assert data['mode'] == 'vlc'
    assert data['supports_pitch'] is False
    assert flask_app.vlc.render_mode == 'vlc'


def test_post_renderer_same_mode_is_noop(flask_test_client, flask_app):
    """POST /renderer with current mode is idempotent."""
    response = flask_test_client.post('/renderer',
        data=json.dumps({"mode": "mpv"}),
        content_type='application/json')
    assert response.status_code == 200
    assert flask_app.vlc.render_mode == 'mpv'


def test_post_renderer_invalid_mode_returns_400(flask_test_client):
    """POST /renderer with unknown mode rejects."""
    response = flask_test_client.post('/renderer',
        data=json.dumps({"mode": "bogus"}),
        content_type='application/json')
    assert response.status_code == 400
    data = json.loads(response.data)
    assert data['error'] == 'invalid_mode'


def test_post_renderer_rejected_while_karaoke_active(flask_test_client, flask_app):
    """POST /renderer returns 409 while karaoke is playing."""
    flask_app.vlc.karaoke_active = True
    response = flask_test_client.post('/renderer',
        data=json.dumps({"mode": "vlc"}),
        content_type='application/json')
    assert response.status_code == 409
    data = json.loads(response.data)
    assert data['error'] == 'karaoke_active'
    # Mode unchanged
    assert flask_app.vlc.render_mode == 'mpv'


def test_status_exposes_renderer_block(flask_test_client):
    """GET /status includes the renderer metadata block for UI."""
    response = flask_test_client.get('/status')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'renderer' in data
    assert data['renderer']['mode'] in ('mpv', 'vlc')
