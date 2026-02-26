"""Tests for AV output routes: /av/status, /av/reset, /av/vlc-device."""

import json
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch


# --- /av/status ---

def test_av_status_returns_structure(flask_test_client, mocker):
    """GET /av/status returns the expected top-level structure."""
    mocker.patch('routes.subprocess.run', return_value=MagicMock(stdout='', stderr='', returncode=0))
    mocker.patch('routes.glob.glob', return_value=[])
    mocker.patch('builtins.open', side_effect=FileNotFoundError)

    response = flask_test_client.get('/av/status')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'video' in data
    assert 'audio' in data
    assert 'health' in data


def test_av_status_video_section_has_required_fields(flask_test_client, mocker):
    """GET /av/status video section contains connectors and active_output."""
    xrandr_output = (
        'Screen 0: minimum 320 x 200, current 1920 x 1080\n'
        'HDMI-1 connected primary 1920x1080+0+0 (normal left inverted right) 510mm x 290mm\n'
        '   1920x1080     60.00*+  50.00  \n'
        '   1280x720      60.00  \n'
        'HDMI-2 disconnected (normal left inverted right)\n'
        'DP-1 disconnected (normal left inverted right)\n'
    )
    mocker.patch(
        'routes.subprocess.run',
        return_value=MagicMock(stdout=xrandr_output, stderr='', returncode=0),
    )
    mocker.patch('routes.glob.glob', return_value=[])
    mocker.patch('builtins.open', side_effect=FileNotFoundError)

    response = flask_test_client.get('/av/status')
    assert response.status_code == 200
    data = json.loads(response.data)

    video = data['video']
    assert 'connectors' in video
    assert 'active_output' in video
    assert 'HDMI-1' in video['connectors']
    assert video['connectors']['HDMI-1']['connected'] is True
    assert video['connectors']['HDMI-1']['current_resolution'] == '1920x1080'
    assert '1920x1080' in video['connectors']['HDMI-1']['available_modes']
    assert video['active_output'] == 'HDMI-1'
    assert 'HDMI-2' in video['connectors']
    assert video['connectors']['HDMI-2']['connected'] is False


def test_av_status_audio_section_has_required_fields(flask_test_client, mocker):
    """GET /av/status audio section contains HDMI PCM info and VLC device."""
    aplay_output = (
        'card 0: PCH [HDA Intel PCH], device 3: HDMI 0 [HDMI 0]\n'
        '  Subdevices: 1/1\n'
        'card 0: PCH [HDA Intel PCH], device 7: HDMI 1 [HDMI 1]\n'
        '  Subdevices: 1/1\n'
    )
    amixer_output = (
        "numid=24,iface=MIXER,name='IEC958 Playback Switch',index=0\n"
        "  ; type=BOOLEAN,access=rw------,values=1\n"
        "  : values=on\n"
        "numid=30,iface=MIXER,name='IEC958 Playback Switch',index=1\n"
        "  ; type=BOOLEAN,access=rw------,values=1\n"
        "  : values=off\n"
        "numid=42,iface=MIXER,name='HDMI/DP,pcm=3 Jack',index=0\n"
        "  ; type=BOOLEAN,access=r-------,values=1\n"
        "  : values=on\n"
        "numid=48,iface=MIXER,name='HDMI/DP,pcm=7 Jack',index=0\n"
        "  ; type=BOOLEAN,access=r-------,values=1\n"
        "  : values=off\n"
    )

    call_count = [0]

    def mock_run(cmd, **kwargs):
        call_count[0] += 1
        if 'aplay' in cmd:
            return MagicMock(stdout=aplay_output, stderr='', returncode=0)
        if 'amixer' in cmd:
            return MagicMock(stdout=amixer_output, stderr='', returncode=0)
        if 'pactl' in cmd:
            return MagicMock(stdout='Active Profile: output:analog-stereo+input:analog-stereo', returncode=0)
        return MagicMock(stdout='', stderr='', returncode=0)

    mocker.patch('routes.subprocess.run', side_effect=mock_run)
    mocker.patch('routes.glob.glob', return_value=[])
    mocker.patch('builtins.open', side_effect=FileNotFoundError)

    response = flask_test_client.get('/av/status')
    assert response.status_code == 200
    data = json.loads(response.data)

    audio = data['audio']
    assert 'vlc_device' in audio
    assert 'asound_hw' in audio
    assert 'hdmi_pcms' in audio
    assert 'pipewire_profile' in audio
    assert 'hw:0,3' in audio['hdmi_pcms']
    assert audio['hdmi_pcms']['hw:0,3']['connected'] is True
    assert 'hw:0,7' in audio['hdmi_pcms']
    assert audio['hdmi_pcms']['hw:0,7']['connected'] is False


def test_av_status_health_section(flask_test_client, mocker):
    """GET /av/status health section contains all expected keys."""
    mocker.patch('routes.subprocess.run', return_value=MagicMock(stdout='', stderr='', returncode=0))
    mocker.patch('routes.glob.glob', return_value=[])
    mocker.patch('builtins.open', side_effect=FileNotFoundError)

    response = flask_test_client.get('/av/status')
    assert response.status_code == 200
    data = json.loads(response.data)

    health = data['health']
    assert 'video_ok' in health
    assert 'audio_ok' in health
    assert 'asound_matches_active_jack' in health
    assert 'pipewire_profile_ok' in health
    assert 'iec958_ok' in health


# --- /av/reset ---

def test_av_reset_runs_script_and_restarts_vlc(flask_test_client, flask_app, mocker):
    """POST /av/reset runs fix-hdmi-audio.sh and queues a VLC restart."""
    mock_run = mocker.patch(
        'routes.subprocess.run',
        return_value=MagicMock(stdout='fix-hdmi-audio: done', stderr='', returncode=0),
    )
    mock_restart = mocker.patch.object(flask_app.vlc, 'restart_instances')
    mock_thread = mocker.patch('routes.threading.Thread')

    response = flask_test_client.post('/av/reset')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['success'] is True

    # Script was called
    script_calls = [c for c in mock_run.call_args_list if 'fix-hdmi-audio' in str(c)]
    assert len(script_calls) == 1

    # VLC restart was threaded
    mock_thread.assert_called_once()
    # VLC audio device reset to hdmiout
    assert flask_app.vlc.audio_device == 'hdmiout'


def test_av_reset_script_failure_returns_500(flask_test_client, mocker):
    """POST /av/reset returns 500 if fix-hdmi-audio.sh exits non-zero."""
    mocker.patch(
        'routes.subprocess.run',
        return_value=MagicMock(stdout='', stderr='permission denied', returncode=1),
    )

    response = flask_test_client.post('/av/reset')
    assert response.status_code == 500
    data = json.loads(response.data)
    assert 'error' in data


def test_av_reset_script_exception_returns_500(flask_test_client, mocker):
    """POST /av/reset returns 500 if running the script raises an exception."""
    mocker.patch('routes.subprocess.run', side_effect=FileNotFoundError('bash not found'))

    response = flask_test_client.post('/av/reset')
    assert response.status_code == 500
    data = json.loads(response.data)
    assert 'error' in data


def test_av_reset_sets_vlc_device_to_hdmiout(flask_test_client, flask_app, mocker):
    """POST /av/reset always resets VLC audio device to 'hdmiout'."""
    flask_app.vlc.audio_device = 'usbmixer'
    mocker.patch(
        'routes.subprocess.run',
        return_value=MagicMock(stdout='ok', stderr='', returncode=0),
    )
    mocker.patch('routes.threading.Thread')

    flask_test_client.post('/av/reset')
    assert flask_app.vlc.audio_device == 'hdmiout'


# --- /av/vlc-device ---

def test_av_vlc_device_switches_hw_device(flask_test_client, flask_app, mocker):
    """POST /av/vlc-device accepts hw:X,Y format and restarts VLC."""
    flask_app.vlc.audio_device = 'hdmiout'
    mocker.patch.object(flask_app.vlc, 'restart_instances')
    mocker.patch('routes.threading.Thread')

    response = flask_test_client.post('/av/vlc-device',
        data=json.dumps({'device': 'hw:0,7'}),
        content_type='application/json')

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['success'] is True
    assert flask_app.vlc.audio_device == 'hw:0,7'


def test_av_vlc_device_accepts_configured_named_device(flask_test_client, flask_app, mocker):
    """POST /av/vlc-device accepts named devices from audio_devices config."""
    flask_app.kj_config['audio_devices'] = {'hdmiout': 'HDMI Output', 'usbmixer': 'USB Mixer'}
    flask_app.vlc.audio_device = 'hdmiout'
    mocker.patch('routes.threading.Thread')

    response = flask_test_client.post('/av/vlc-device',
        data=json.dumps({'device': 'usbmixer'}),
        content_type='application/json')

    assert response.status_code == 200
    assert flask_app.vlc.audio_device == 'usbmixer'


def test_av_vlc_device_rejects_unknown_named_device(flask_test_client):
    """POST /av/vlc-device with unknown named device returns 400."""
    response = flask_test_client.post('/av/vlc-device',
        data=json.dumps({'device': 'nosuchdevice'}),
        content_type='application/json')
    assert response.status_code == 400


def test_av_vlc_device_requires_device(flask_test_client):
    """POST /av/vlc-device without device returns 400."""
    response = flask_test_client.post('/av/vlc-device',
        data=json.dumps({}),
        content_type='application/json')
    assert response.status_code == 400


def test_av_vlc_device_already_active(flask_test_client, flask_app):
    """POST /av/vlc-device with current device returns success without restart."""
    flask_app.vlc.audio_device = 'hdmiout'

    response = flask_test_client.post('/av/vlc-device',
        data=json.dumps({'device': 'hdmiout'}),
        content_type='application/json')

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['success'] is True


# --- config.json no longer updated by audio/display routes ---

def test_audio_device_switch_no_longer_saves_config(flask_test_client, flask_app, mocker):
    """POST /audio_device no longer calls save_config_value."""
    flask_app.kj_config['audio_devices'] = {'hdmiout': 'HDMI', 'usbmixer': 'USB'}
    flask_app.vlc.audio_device = 'hdmiout'
    mock_save = mocker.patch('routes.save_config_value')
    mocker.patch('routes.threading.Thread')

    flask_test_client.post('/audio_device',
        data=json.dumps({'device': 'usbmixer'}),
        content_type='application/json')

    mock_save.assert_not_called()


def test_display_resolution_no_longer_saves_config(flask_test_client, mocker):
    """POST /display/resolution no longer calls save_config_value."""
    xrandr_output = (
        'HDMI-1 connected 1920x1080+0+0\n'
        '   1920x1080     60.00*+\n'
        '   1280x720      60.00\n'
    )
    mock_run = mocker.patch(
        'routes.subprocess.run',
        return_value=MagicMock(stdout=xrandr_output, stderr='', returncode=0),
    )
    mock_save = mocker.patch('routes.save_config_value')

    flask_test_client.post('/display/resolution',
        data=json.dumps({'resolution': '1280x720'}),
        content_type='application/json')

    mock_save.assert_not_called()


def test_switch_hdmi_no_longer_saves_config(flask_test_client, flask_app, mocker):
    """POST /audio/switch-hdmi no longer calls save_config_value."""
    mocker.patch(
        'routes.subprocess.run',
        return_value=MagicMock(stdout='', stderr='', returncode=0),
    )
    mock_save = mocker.patch('routes.save_config_value')
    mocker.patch('routes.threading.Thread')

    flask_test_client.post('/audio/switch-hdmi',
        data=json.dumps({'device': 'hw:0,7'}),
        content_type='application/json')

    mock_save.assert_not_called()
