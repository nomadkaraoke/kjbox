"""Tests for MpvKaraokePlayer: IPC, pitch, playback, ensure_released race fix."""

import pytest

from filler import FillerVLC
from mpv_manager import MpvKaraokePlayer, PITCH_MIN, PITCH_MAX


@pytest.fixture
def player(mock_config, mocker):
    """Fresh MpvKaraokePlayer with its FillerVLC (disabled — no real processes)."""
    filler = FillerVLC(mock_config, enabled=False)
    p = MpvKaraokePlayer(mock_config, filler, enabled=False)
    return p


# --- Identity & capabilities ---

def test_name(player):
    assert player.name == 'mpv'


def test_supports_pitch(player):
    assert player.supports_pitch is True


def test_supports_cdg(player):
    assert player.supports_cdg is True


# --- Initial state ---

def test_initial_state(player):
    assert player.current_path is None
    assert player.active is False
    assert player.karaoke_volume == 200
    assert player.pitch_semitones == 0
    assert player.audio_error is False
    assert player.audio_device == 'hdmiout'
    assert player.audio_backend == 'alsa'


def test_karaoke_volume_from_config(mock_config):
    mock_config['karaoke_volume'] = 150
    filler = FillerVLC(mock_config, enabled=False)
    p = MpvKaraokePlayer(mock_config, filler, enabled=False)
    assert p.karaoke_volume == 150


# --- Volume scale conversion ---

def test_vlc_to_mpv_volume_100():
    assert MpvKaraokePlayer._vlc_to_mpv_volume(256) == 100.0


def test_vlc_to_mpv_volume_0():
    assert MpvKaraokePlayer._vlc_to_mpv_volume(0) == 0.0


def test_vlc_to_mpv_volume_200():
    assert MpvKaraokePlayer._vlc_to_mpv_volume(512) == 200.0


def test_vlc_to_mpv_volume_50():
    assert MpvKaraokePlayer._vlc_to_mpv_volume(128) == 50.0


# --- Pitch ---

def test_set_pitch_stores_value(player):
    player.set_pitch(3)
    assert player.pitch_semitones == 3


def test_set_pitch_clamps_max(player):
    player.set_pitch(10)
    assert player.pitch_semitones == PITCH_MAX


def test_set_pitch_clamps_min(player):
    player.set_pitch(-10)
    assert player.pitch_semitones == PITCH_MIN


def test_set_pitch_sends_ipc_only_if_active(player, mocker):
    send = mocker.patch.object(player, '_send_ipc')
    player.active = False
    player.set_pitch(2)
    send.assert_not_called()

    player.active = True
    player.set_pitch(3)
    send.assert_called_once()


# --- ensure_released (mpv race fix) ---

def test_ensure_released_sends_stop_and_polls(player, mocker):
    send = mocker.patch.object(player, '_send_ipc', return_value={'error': 'success'})
    # idle-active becomes true on 3rd poll
    mocker.patch.object(player, '_get_property', side_effect=[False, False, True])
    sleep = mocker.patch('mpv_manager.time.sleep')

    player.ensure_released()

    send.assert_called_with(["stop"])
    # final 150ms buffer sleep
    sleep_args = [c.args[0] for c in sleep.call_args_list]
    assert 0.15 in sleep_args


def test_ensure_released_respects_timeout(player, mocker):
    mocker.patch.object(player, '_send_ipc')
    mocker.patch.object(player, '_get_property', return_value=False)
    fake_time = [100.0]
    mocker.patch('mpv_manager.time.sleep', side_effect=lambda _: fake_time.__setitem__(0, fake_time[0] + 0.02))
    mocker.patch('mpv_manager.time.time', side_effect=lambda: fake_time[0])
    # Should bail out after ~1s even if idle never true
    assert player.ensure_released() is True


# --- pause_resume ---

def test_pause_resume_toggles(player, mocker):
    mocker.patch.object(player, '_get_property', return_value=False)
    set_prop = mocker.patch.object(player, '_set_property')
    result = player.pause_resume()
    assert result is True  # now paused
    set_prop.assert_called_with("pause", True)


def test_pause_resume_returns_none_on_error(player, mocker):
    mocker.patch.object(player, '_get_property', return_value=None)
    assert player.pause_resume() is None


# --- seek ---

def test_seek_updates_timestamp(player, mocker):
    import time as real_time
    mocker.patch.object(player, '_send_ipc')
    before = real_time.time()
    player.seek(42)
    assert player.last_seek_time >= before


# --- stop ---

def test_stop_clears_state(player, mocker):
    send = mocker.patch.object(player, '_send_ipc')
    player.active = True
    player.current_path = '/some/song.mp4'
    player.stop()
    send.assert_called_with(["stop"])
    assert player.active is False
    assert player.current_path is None


# --- play ---

def test_play_missing_file_no_op(player, mocker):
    send = mocker.patch.object(player, '_send_ipc')
    player.enabled = True
    player.play('/does/not/exist.mp4')
    send.assert_not_called()


def test_play_disabled_no_op(player, mocker, tmp_path):
    f = tmp_path / "song.mp4"
    f.write_text("")
    send = mocker.patch.object(player, '_send_ipc')
    player.enabled = False
    player.play(str(f))
    send.assert_not_called()


def test_play_loadfile_failure_rollback(player, mocker, tmp_path):
    f = tmp_path / "song.mp4"
    f.write_text("")
    player.enabled = True
    overlay = mocker.Mock()
    mocker.patch.object(
        player, '_send_ipc', return_value={'error': 'loading failed'},
    )
    player.play(str(f), display_path=str(f), overlay_manager=overlay)
    # Must mark audio_error and clear overlay
    assert player.audio_error is True
    overlay.set_karaoke_playing.assert_any_call(False)


def test_play_without_audio_file_sends_plain_loadfile(player, mocker, tmp_path):
    f = tmp_path / "song.mp4"
    f.write_text("")
    player.enabled = True
    send = mocker.patch.object(player, '_send_ipc', return_value={'error': 'success'})
    player.play(str(f))
    assert send.call_args_list[0].args[0] == ["loadfile", str(f), "replace"]


def test_play_with_audio_file_attaches_external_audio(player, mocker, tmp_path):
    # mpv renders CDG graphics only when handed the .cdg directly; the matching
    # mp3 is attached as an external audio track via a loadfile option. The
    # value uses mpv's length-prefixed escaping so paths with commas/spaces are
    # safe.
    cdg = tmp_path / "song.cdg"
    cdg.write_text("")
    mp3 = tmp_path / "song.mp3"
    mp3.write_text("")
    player.enabled = True
    send = mocker.patch.object(player, '_send_ipc', return_value={'error': 'success'})
    player.play(str(cdg), audio_file=str(mp3))
    cmd = send.call_args_list[0].args[0]
    assert cmd[:3] == ["loadfile", str(cdg), "replace"]
    n = len(str(mp3).encode("utf-8"))
    assert cmd[3] == f"audio-file=%{n}%{mp3}"


# --- _handle_karaoke_ended: race fix wired up ---

def test_handle_karaoke_ended_calls_ensure_released_before_callback(player, mocker):
    player.active = True
    ensure_released = mocker.patch.object(player, 'ensure_released')
    mocker.patch.object(player, '_save_state')
    call_order = []
    ensure_released.side_effect = lambda: call_order.append('ensure_released')
    player.on_karaoke_end = lambda: call_order.append('callback')
    player._handle_karaoke_ended()
    # ensure_released must run BEFORE on_karaoke_end, else filler reclaims ALSA
    # while mpv is still draining
    assert call_order == ['ensure_released', 'callback']


def test_handle_karaoke_ended_noop_when_inactive(player, mocker):
    player.active = False
    ensure = mocker.patch.object(player, 'ensure_released')
    cb = mocker.Mock()
    player.on_karaoke_end = cb
    player._handle_karaoke_ended()
    ensure.assert_not_called()
    cb.assert_not_called()


# --- Audio backend ---

def test_launch_alsa_command(mock_config, mocker):
    filler = FillerVLC(mock_config, enabled=False)
    p = MpvKaraokePlayer(mock_config, filler, enabled=True, audio_backend='alsa')
    p.audio_device = 'hdmiout'
    mocker.patch('os.unlink', side_effect=FileNotFoundError)
    mocker.patch('builtins.open', mocker.mock_open())
    mock_popen = mocker.patch('subprocess.Popen')
    mock_popen.return_value.pid = 12345
    mocker.patch('os.path.exists', return_value=False)
    mocker.patch('mpv_manager.time.sleep')
    p.launch()
    args = mock_popen.call_args[0][0]
    assert '--ao=alsa' in args
    assert '--audio-device=alsa/hdmiout' in args
    assert '--ao=pulse' not in args


def test_launch_pipewire_command(mock_config, mocker):
    filler = FillerVLC(mock_config, enabled=False)
    p = MpvKaraokePlayer(mock_config, filler, enabled=True, audio_backend='pipewire')
    mocker.patch('os.unlink', side_effect=FileNotFoundError)
    mocker.patch('builtins.open', mocker.mock_open())
    mock_popen = mocker.patch('subprocess.Popen')
    mock_popen.return_value.pid = 12345
    mocker.patch('os.path.exists', return_value=False)
    mocker.patch('mpv_manager.time.sleep')
    p.launch()
    args = mock_popen.call_args[0][0]
    assert '--ao=pulse' in args
    assert '--ao=alsa' not in args


# --- shutdown ---

def test_shutdown_sends_quit_and_terminates(player, mocker):
    send = mocker.patch.object(player, '_send_ipc')
    mock_proc = mocker.Mock()
    mock_proc.poll.return_value = None  # running
    player.process = mock_proc
    mocker.patch('mpv_manager.time.sleep')
    mocker.patch('os.unlink', side_effect=FileNotFoundError)
    player.shutdown()
    send.assert_called_with(["quit"])
    mock_proc.terminate.assert_called_once()
    assert player.process is None
    assert player.active is False
    assert player._monitor_stop.is_set()


# --- try_reconnect ---

def test_try_reconnect_disabled(mock_config):
    filler = FillerVLC(mock_config, enabled=False)
    p = MpvKaraokePlayer(mock_config, filler, enabled=False)
    assert p.try_reconnect() is False


def test_try_reconnect_no_socket(mock_config, mocker):
    filler = FillerVLC(mock_config, enabled=True)
    p = MpvKaraokePlayer(mock_config, filler, enabled=True)
    mocker.patch('os.path.exists', return_value=False)
    assert p.try_reconnect() is False


def test_try_reconnect_finds_idle(mock_config, mocker):
    filler = FillerVLC(mock_config, enabled=True)
    p = MpvKaraokePlayer(mock_config, filler, enabled=True)
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch.object(p, '_get_property', return_value=True)  # idle
    assert p.try_reconnect() is True
    assert p.active is False


def test_try_reconnect_finds_playing(mock_config, mocker):
    filler = FillerVLC(mock_config, enabled=True)
    p = MpvKaraokePlayer(mock_config, filler, enabled=True)
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch.object(p, '_get_property', return_value=False)  # not idle
    mocker.patch.object(p, '_load_state', return_value={'current_playing_path': '/s.mp4'})
    assert p.try_reconnect() is True
    assert p.active is True
    assert p.current_path == '/s.mp4'
