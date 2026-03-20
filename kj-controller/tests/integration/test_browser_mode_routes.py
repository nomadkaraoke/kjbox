"""Tests for browser mode routes: /browser-mode/enable, /browser-mode/disable, /status."""

import json
from unittest.mock import MagicMock, patch


def test_status_includes_browser_mode(flask_test_client):
    """GET /status includes browser_mode in response."""
    response = flask_test_client.get('/status')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'browser_mode' in data
    bm = data['browser_mode']
    assert 'enabled' in bm
    assert 'running' in bm
    assert bm['enabled'] is False
    assert bm['running'] is False


def test_enable_browser_mode(flask_test_client, flask_app, mocker):
    """POST /browser-mode/enable launches Chromium and sets mode flag."""
    mocker.patch.object(flask_app.chromium, 'launch', return_value=True)
    mocker.patch('routes.save_config_value')

    response = flask_test_client.post(
        '/browser-mode/enable',
        data=json.dumps({'url': 'https://youtube.com'}),
        content_type='application/json',
    )

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['success'] is True
    assert data['url'] == 'https://youtube.com'
    flask_app.chromium.launch.assert_called_once()


def test_enable_browser_mode_default_url(flask_test_client, flask_app, mocker):
    """POST /browser-mode/enable defaults to youtube.com when no URL provided."""
    mocker.patch.object(flask_app.chromium, 'launch', return_value=True)
    mocker.patch('routes.save_config_value')

    response = flask_test_client.post(
        '/browser-mode/enable',
        data=json.dumps({}),
        content_type='application/json',
    )

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['url'] == 'https://youtube.com'


def test_enable_browser_mode_stops_vlc(flask_test_client, flask_app, mocker):
    """Enabling browser mode stops VLC filler and karaoke."""
    flask_app.vlc.enabled = True
    mocker.patch.object(flask_app.vlc, 'fade_out_filler')
    mocker.patch.object(flask_app.vlc, 'ensure_filler_stopped')
    mocker.patch.object(flask_app.vlc, 'ensure_karaoke_released')
    mocker.patch.object(flask_app.chromium, 'launch', return_value=True)
    mocker.patch('routes.save_config_value')

    response = flask_test_client.post(
        '/browser-mode/enable',
        data=json.dumps({'url': 'https://youtube.com'}),
        content_type='application/json',
    )

    assert response.status_code == 200
    flask_app.vlc.fade_out_filler.assert_called_once()
    flask_app.vlc.ensure_filler_stopped.assert_called_once()
    flask_app.vlc.ensure_karaoke_released.assert_called_once()


def test_enable_browser_mode_launch_failure(flask_test_client, flask_app, mocker):
    """Returns 500 if Chromium fails to launch."""
    mocker.patch.object(flask_app.chromium, 'launch', return_value=False)

    response = flask_test_client.post(
        '/browser-mode/enable',
        data=json.dumps({'url': 'https://youtube.com'}),
        content_type='application/json',
    )

    assert response.status_code == 500


def test_disable_browser_mode(flask_test_client, flask_app, mocker):
    """POST /browser-mode/disable kills Chromium and restarts VLC."""
    flask_app.vlc.enabled = True
    mocker.patch.object(flask_app.chromium, 'kill')
    mocker.patch.object(flask_app.vlc, 'restart_instances')

    response = flask_test_client.post('/browser-mode/disable')

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['success'] is True
    flask_app.chromium.kill.assert_called_once()
    flask_app.vlc.restart_instances.assert_called_once()


def test_enable_passes_audio_device_to_chromium(flask_test_client, flask_app, mocker):
    """Contract: enable route passes VLC's audio_device to chromium.launch()."""
    flask_app.vlc.enabled = True
    flask_app.vlc.audio_device = 'usbmixer'
    mocker.patch.object(flask_app.vlc, 'fade_out_filler')
    mocker.patch.object(flask_app.vlc, 'ensure_filler_stopped')
    mocker.patch.object(flask_app.vlc, 'ensure_karaoke_released')
    mock_launch = mocker.patch.object(flask_app.chromium, 'launch', return_value=True)
    mocker.patch('routes.save_config_value')

    flask_test_client.post(
        '/browser-mode/enable',
        data=json.dumps({'url': 'https://youtube.com'}),
        content_type='application/json',
    )

    # Verify audio_device was passed through
    mock_launch.assert_called_once_with('https://youtube.com', audio_device='usbmixer')


def test_enable_uses_config_default_when_vlc_disabled(flask_test_client, flask_app, mocker):
    """Contract: when VLC is disabled, audio_device comes from config default."""
    flask_app.vlc.enabled = False
    flask_app.kj_config['default_audio_device'] = 'hdmiout'
    mock_launch = mocker.patch.object(flask_app.chromium, 'launch', return_value=True)
    mocker.patch('routes.save_config_value')

    flask_test_client.post(
        '/browser-mode/enable',
        data=json.dumps({'url': 'https://youtube.com'}),
        content_type='application/json',
    )

    mock_launch.assert_called_once_with('https://youtube.com', audio_device='hdmiout')


def test_status_auto_clears_browser_mode_on_chromium_crash(flask_test_client, flask_app, mocker):
    """Status auto-clears browser_mode when Chromium dies on its own."""
    mocker.patch.object(flask_app.chromium, 'launch', return_value=True)
    mocker.patch('routes.save_config_value')

    # Enable browser mode
    flask_test_client.post(
        '/browser-mode/enable',
        data=json.dumps({'url': 'https://youtube.com'}),
        content_type='application/json',
    )

    # Simulate Chromium crashing (is_running returns False)
    mocker.patch.object(flask_app.chromium, 'get_status', return_value={
        'running': False, 'pid': None, 'url': None,
    })

    # Status should auto-clear browser_mode
    response = flask_test_client.get('/status')
    data = json.loads(response.data)
    assert data['browser_mode']['enabled'] is False


def test_status_reflects_enabled_after_toggle(flask_test_client, flask_app, mocker):
    """Status shows browser_mode.enabled=True after enabling."""
    mocker.patch.object(flask_app.chromium, 'launch', return_value=True)
    mocker.patch.object(flask_app.chromium, 'get_status', return_value={
        'running': True, 'pid': 123, 'url': 'https://youtube.com',
    })
    mocker.patch('routes.save_config_value')

    # Enable
    flask_test_client.post(
        '/browser-mode/enable',
        data=json.dumps({'url': 'https://youtube.com'}),
        content_type='application/json',
    )

    # Check status
    response = flask_test_client.get('/status')
    data = json.loads(response.data)
    assert data['browser_mode']['enabled'] is True
    assert data['browser_mode']['running'] is True
