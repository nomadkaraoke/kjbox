"""Tests for FillerVLC: the shared filler-music process."""

import pytest

from filler import FillerVLC


# --- Enabled / disabled state ---

def test_disabled_by_default_non_pi(mock_config, mocker):
    mocker.patch('filler.is_pi', return_value=False)
    mock_config.pop('enable_vlc', None)
    f = FillerVLC(mock_config)
    assert f.enabled is False


def test_enabled_on_pi(mock_config, mocker):
    mocker.patch('filler.is_pi', return_value=True)
    f = FillerVLC(mock_config)
    assert f.enabled is True


def test_enabled_via_config(mock_config, mocker):
    mocker.patch('filler.is_pi', return_value=False)
    mock_config['enable_vlc'] = True
    f = FillerVLC(mock_config)
    assert f.enabled is True


def test_explicit_enabled_override(mock_config):
    f = FillerVLC(mock_config, enabled=False)
    assert f.enabled is False


def test_initial_state(mock_config):
    f = FillerVLC(mock_config, enabled=False)
    assert f.process is None
    assert f.current_track is None
    assert f.volume == 100
    assert f.audio_device == 'hdmiout'
    assert f.audio_backend == 'alsa'


def test_volume_from_config(mock_config):
    mock_config['filler_volume'] = 75
    f = FillerVLC(mock_config, enabled=False)
    assert f.volume == 75


def test_port_and_password_properties(mock_config):
    f = FillerVLC(mock_config, enabled=False)
    assert f.port == 8081
    assert f.password == 'filler'


# --- send() disabled guard ---

def test_send_disabled_returns_none(mock_config):
    f = FillerVLC(mock_config, enabled=False)
    assert f.send("pl_play") is None


def test_send_success(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    mock_resp = mocker.Mock()
    mock_resp.json.return_value = {"state": "playing"}
    mock_resp.raise_for_status = mocker.Mock()
    mock_session = mocker.Mock()
    mock_session.get.return_value = mock_resp
    mocker.patch('filler.requests.Session', return_value=mock_session)
    assert f.send("pl_play") == {"state": "playing"}


def test_send_returns_none_on_http_error(mock_config, mocker):
    import requests as req
    f = FillerVLC(mock_config, enabled=True)
    mocker.patch(
        'filler.requests.Session.get', side_effect=req.exceptions.ConnectionError,
    )
    assert f.send("pl_play") is None


# --- Probe ---

def test_probe_returns_none_on_unreachable(mock_config, mocker):
    import requests as req
    f = FillerVLC(mock_config, enabled=True)
    mocker.patch(
        'filler.requests.Session.get', side_effect=req.exceptions.ConnectionError,
    )
    assert f.probe() is None


def test_probe_returns_status(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    mock_resp = mocker.Mock()
    mock_resp.json.return_value = {"state": "playing", "volume": 100}
    mock_resp.raise_for_status = mocker.Mock()
    mock_session = mocker.Mock()
    mock_session.get.return_value = mock_resp
    mocker.patch('filler.requests.Session', return_value=mock_session)
    assert f.probe() == {"state": "playing", "volume": 100}


# --- Fade disabled guard ---

def test_fade_in_disabled_noop(mock_config):
    f = FillerVLC(mock_config, enabled=False)
    f.fade_in()  # should not raise


def test_fade_out_disabled_noop(mock_config):
    f = FillerVLC(mock_config, enabled=False)
    f.fade_out()  # should not raise


# --- ensure_stopped ---

def test_ensure_stopped_success_first_try(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    mocker.patch.object(f, 'send', return_value={"state": "stopped"})
    assert f.ensure_stopped() is True


def test_ensure_stopped_retries_until_stopped(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    mocker.patch.object(f, 'send', side_effect=[
        {"state": "playing"}, None,
        {"state": "playing"}, None,
        {"state": "stopped"},
    ])
    mocker.patch('filler.time.sleep')
    assert f.ensure_stopped() is True


def test_ensure_stopped_fails_after_5_attempts(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    mocker.patch.object(f, 'send', return_value={"state": "playing"})
    mocker.patch('filler.time.sleep')
    assert f.ensure_stopped() is False


# --- launch / shutdown ---

def test_launch_disabled_noop(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=False)
    mock_popen = mocker.patch('subprocess.Popen')
    f.launch()
    mock_popen.assert_not_called()


def test_launch_skips_if_already_running(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    mock_proc = mocker.Mock()
    mock_proc.poll.return_value = None  # still running
    f.process = mock_proc
    mock_popen = mocker.patch('subprocess.Popen')
    f.launch()
    mock_popen.assert_not_called()


def test_launch_alsa_command(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    f.audio_backend = 'alsa'
    f.audio_device = 'hdmiout'
    mocker.patch('builtins.open', mocker.mock_open())
    mock_popen = mocker.patch('subprocess.Popen')
    mock_popen.return_value.pid = 12345
    mocker.patch('filler.is_pi', return_value=False)
    mocker.patch('filler.time.sleep')
    f.launch()
    args = mock_popen.call_args[0][0]
    assert '--aout' in args
    assert args[args.index('--aout') + 1] == 'alsa'
    assert '--alsa-audio-device' in args
    assert 'hdmiout' in args


def test_launch_pipewire_command(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    f.audio_backend = 'pipewire'
    mocker.patch('builtins.open', mocker.mock_open())
    mock_popen = mocker.patch('subprocess.Popen')
    mock_popen.return_value.pid = 12345
    mocker.patch('filler.is_pi', return_value=False)
    mocker.patch('filler.time.sleep')
    f.launch()
    args = mock_popen.call_args[0][0]
    assert '--aout' in args
    assert args[args.index('--aout') + 1] == 'pulse'
    assert '--alsa-audio-device' not in args


def test_shutdown_terminates_process(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    mock_proc = mocker.Mock()
    mock_proc.poll.return_value = None  # running
    f.process = mock_proc
    mocker.patch.object(f, '_kill_port')
    f.shutdown()
    mock_proc.terminate.assert_called_once()
    assert f.process is None


def test_shutdown_kills_orphan_if_no_owned_process(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    f.process = None
    kill_port = mocker.patch.object(f, '_kill_port')
    f.shutdown()
    kill_port.assert_called_once()


# --- try_reconnect ---

def test_try_reconnect_disabled(mock_config):
    f = FillerVLC(mock_config, enabled=False)
    assert f.try_reconnect() is False


def test_try_reconnect_finds_running(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    mocker.patch.object(f, 'probe', return_value={"state": "playing"})
    mocker.patch.object(f, '_load_state', return_value={'current_track': 'song.mp3'})
    assert f.try_reconnect() is True
    assert f.current_track == 'song.mp3'


def test_try_reconnect_no_running_instance(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    mocker.patch.object(f, 'probe', return_value=None)
    assert f.try_reconnect() is False


# --- set_volume ---

def test_set_volume_updates_attr_and_sends(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    send = mocker.patch.object(f, 'send')
    f.set_volume(180)
    assert f.volume == 180
    send.assert_called_once_with("volume&val=180")


# --- current_media_path ---

def test_current_media_path_joins_dir_and_track(mock_config):
    f = FillerVLC(mock_config, enabled=False)
    f.current_track = 'wii.mp3'
    path = f.current_media_path()
    assert path.endswith('wii.mp3')
    assert mock_config['filler_music_dir'] in path


def test_current_media_path_empty_when_no_track(mock_config):
    f = FillerVLC(mock_config, enabled=False)
    f.current_track = None
    assert f.current_media_path() == ''


# --- Auto-heal (_verify_playing / _relaunch) ---

def test_verify_playing_healthy_noop(mock_config, mocker):
    """playedabuffers > 0 → VLC outputting audio, no action."""
    f = FillerVLC(mock_config, enabled=True)
    mocker.patch('filler.time.sleep')
    mocker.patch.object(f, 'probe', return_value={
        "state": "playing",
        "stats": {"playedabuffers": 500, "decodedaudio": 1000},
    })
    relaunch = mocker.patch.object(f, '_relaunch')
    f._verify_playing()
    relaunch.assert_not_called()


def test_verify_playing_dead_aout_triggers_relaunch(mock_config, mocker):
    """played=0 but decoded>100 → aout broken, relaunch."""
    f = FillerVLC(mock_config, enabled=True)
    mocker.patch('filler.time.sleep')
    mocker.patch.object(f, 'probe', return_value={
        "state": "playing",
        "stats": {"playedabuffers": 0, "decodedaudio": 5000},
    })
    relaunch = mocker.patch.object(f, '_relaunch')
    f._verify_playing()
    relaunch.assert_called_once()


def test_verify_playing_skips_if_not_playing(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    mocker.patch('filler.time.sleep')
    mocker.patch.object(f, 'probe', return_value={
        "state": "stopped", "stats": {"playedabuffers": 0, "decodedaudio": 0},
    })
    relaunch = mocker.patch.object(f, '_relaunch')
    f._verify_playing()
    relaunch.assert_not_called()


def test_verify_playing_skips_if_probe_fails(mock_config, mocker):
    f = FillerVLC(mock_config, enabled=True)
    mocker.patch('filler.time.sleep')
    mocker.patch.object(f, 'probe', return_value=None)
    relaunch = mocker.patch.object(f, '_relaunch')
    f._verify_playing()
    relaunch.assert_not_called()


def test_verify_playing_skips_if_decoder_cold(mock_config, mocker):
    """decoded<100 → too early to judge, don't relaunch."""
    f = FillerVLC(mock_config, enabled=True)
    mocker.patch('filler.time.sleep')
    mocker.patch.object(f, 'probe', return_value={
        "state": "playing", "stats": {"playedabuffers": 0, "decodedaudio": 10},
    })
    relaunch = mocker.patch.object(f, '_relaunch')
    f._verify_playing()
    relaunch.assert_not_called()


def test_relaunch_does_not_call_fade_in(mock_config, mocker):
    """Guard against infinite loop: _relaunch must not trigger another fade_in
    (which would retrigger the auto-heal thread)."""
    f = FillerVLC(mock_config, enabled=True)
    f.current_track = 'wii.mp3'
    mocker.patch('filler.time.sleep')
    mocker.patch.object(f, 'shutdown')
    mocker.patch.object(f, 'launch')
    mocker.patch.object(f, 'send')
    fade_in = mocker.patch.object(f, 'fade_in')
    f._relaunch()
    fade_in.assert_not_called()


def test_fade_in_spawns_verify_thread(mock_config, mocker):
    """fade_in must kick off the auto-heal verifier."""
    f = FillerVLC(mock_config, enabled=True)
    mocker.patch.object(f, 'send')
    mock_thread = mocker.patch('filler.threading.Thread')
    f.fade_in()
    targets = [call.kwargs.get('target') for call in mock_thread.call_args_list]
    assert f._verify_playing in targets
