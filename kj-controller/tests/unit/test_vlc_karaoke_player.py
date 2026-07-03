"""Tests for VlcKaraokePlayer: dual-VLC karaoke backend."""

import pytest

from filler import FillerVLC
from vlc import VlcKaraokePlayer


@pytest.fixture
def player(mock_config, mocker):
    """Fresh VlcKaraokePlayer with its FillerVLC (disabled — no real processes)."""
    filler = FillerVLC(mock_config, enabled=False)
    return VlcKaraokePlayer(mock_config, filler, enabled=False)


# --- Identity & capabilities ---

def test_name(player):
    assert player.name == 'vlc'


def test_does_not_support_pitch(player):
    assert player.supports_pitch is False


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


# --- set_pitch: no-op ---

def test_set_pitch_is_noop(player, mocker):
    log = mocker.patch('vlc.log_message')
    player.set_pitch(5)
    assert player.pitch_semitones == 0  # always 0 regardless of input
    log.assert_called()  # but logs the attempt


# --- Playback delegation ---

def test_stop_clears_state(player, mocker):
    player.active = True
    player.current_path = '/song.mp4'
    mocker.patch.object(player, 'ensure_released')
    mocker.patch.object(player, '_save_state')
    player.stop()
    assert player.active is False
    assert player.current_path is None


def test_seek_updates_timestamp(player, mocker):
    import time as real_time
    mocker.patch.object(player, '_send')
    before = real_time.time()
    player.seek(42)
    assert player.last_seek_time >= before


def test_set_volume_updates_attr_and_sends(player, mocker):
    send = mocker.patch.object(player, '_send')
    player.set_volume(300)
    assert player.karaoke_volume == 300
    send.assert_called_once_with("volume&val=300")


def test_pause_resume_returns_paused_bool(player, mocker):
    mocker.patch.object(player, '_send', side_effect=[
        None,  # pl_pause response
        {"state": "paused"},  # status query
    ])
    mocker.patch('vlc.time.sleep')
    assert player.pause_resume() is True


def test_pause_resume_returns_none_on_error(player, mocker):
    mocker.patch.object(player, '_send', side_effect=[None, None])
    mocker.patch('vlc.time.sleep')
    assert player.pause_resume() is None


# --- get_status ---

def test_get_status_default_when_unreachable(player, mocker):
    mocker.patch.object(player, '_send', return_value=None)
    assert player.get_status() == {"state": "stopped", "time": 0, "length": 0}


def test_get_status_returns_state_time_length(player, mocker):
    mocker.patch.object(player, '_send', return_value={
        "state": "playing", "time": 42, "length": 240,
    })
    assert player.get_status() == {"state": "playing", "time": 42, "length": 240}


# --- get_status: transient-'stopped' guard (mirrors monitor(), keeps UI steady) ---

def test_get_status_transient_stopped_is_playing_when_loaded(player, mocker):
    """VLC reports 'stopped' for a few seconds after play; while a song is loaded and
    within the post-play guard window, get_status smooths it to 'playing' so the fade
    button / now-playing pill don't flicker."""
    import time as _t
    player.active = True
    player.current_path = '/songs/song.mp4'
    player.last_play_time = _t.time()
    mocker.patch.object(player, '_send', return_value={
        "state": "stopped", "time": 0, "length": 0,
    })
    assert player.get_status()["state"] == "playing"


def test_get_status_transient_stopped_after_seek_is_playing(player, mocker):
    import time as _t
    player.active = True
    player.current_path = '/songs/song.mp4'
    player.last_seek_time = _t.time()
    mocker.patch.object(player, '_send', return_value={
        "state": "stopped", "time": 30, "length": 240,
    })
    assert player.get_status()["state"] == "playing"


def test_get_status_stopped_past_guard_window_passes_through(player, mocker):
    """A genuine 'stopped' well after play/seek is honored (song really ended)."""
    player.active = True
    player.current_path = '/songs/song.mp4'
    player.last_play_time = 0
    player.last_seek_time = 0
    mocker.patch.object(player, '_send', return_value={
        "state": "stopped", "time": 5, "length": 240,
    })
    assert player.get_status()["state"] == "stopped"


def test_get_status_blip_is_playing_when_loaded(player, mocker):
    """A None from _send (HTTP timeout) mustn't flap the UI to 'stopped' mid-song."""
    player.active = True
    player.current_path = '/songs/song.mp4'
    mocker.patch.object(player, '_send', return_value=None)
    assert player.get_status() == {"state": "playing", "time": 0, "length": 0}


def test_get_status_blip_when_idle_is_stopped(player, mocker):
    """No song loaded + blip → genuinely stopped (original behavior preserved)."""
    mocker.patch.object(player, '_send', return_value=None)
    assert player.get_status() == {"state": "stopped", "time": 0, "length": 0}


def test_get_status_paused_passes_through(player, mocker):
    player.active = True
    player.current_path = '/songs/song.mp4'
    mocker.patch.object(player, '_send', return_value={
        "state": "paused", "time": 10, "length": 240,
    })
    assert player.get_status()["state"] == "paused"


# --- ensure_released ---

def test_ensure_released_success_first_try(player, mocker):
    mocker.patch.object(player, '_send', return_value={"state": "stopped"})
    assert player.ensure_released() is True


def test_ensure_released_retries(player, mocker):
    mocker.patch.object(player, '_send', side_effect=[
        None, None,  # pl_stop + pl_empty
        {"state": "playing"}, None,  # retry 1
        {"state": "stopped"},  # success
    ])
    mocker.patch('vlc.time.sleep')
    assert player.ensure_released() is True


def test_ensure_released_fails_after_5_attempts(player, mocker):
    mocker.patch.object(player, '_send', return_value={"state": "playing"})
    mocker.patch('vlc.time.sleep')
    assert player.ensure_released() is False


# --- play ---

def test_play_missing_file_no_op(player, mocker):
    send = mocker.patch.object(player, '_send')
    player.enabled = True
    player.play('/does/not/exist.mp4')
    send.assert_not_called()


def test_play_disabled_no_op(player, mocker, tmp_path):
    f = tmp_path / "song.mp4"
    f.write_text("")
    send = mocker.patch.object(player, '_send')
    player.enabled = False
    player.play(str(f))
    send.assert_not_called()


def test_play_sets_display_path_and_overlay(player, mocker, tmp_path):
    f = tmp_path / "song.mp4"
    f.write_text("")
    player.enabled = True
    overlay = mocker.Mock()
    mocker.patch.object(player, '_send')
    mocker.patch('vlc.time.sleep')
    player.play(str(f), display_path='/display/song.mp4', overlay_manager=overlay)
    assert player.current_path == '/display/song.mp4'
    assert player.active is True
    overlay.set_karaoke_playing.assert_called_with(True)


# --- fadeout ---

def test_fadeout_restores_configured_volume(player, mocker):
    player.karaoke_volume = 300
    mocker.patch.object(player, '_send')
    mocker.patch.object(player, 'stop')
    mocker.patch('vlc.time.sleep')

    # Prevent thread spawn by invoking the target inline
    class SyncThread:
        def __init__(self, target, daemon=False, **kwargs):
            self.target = target
        def start(self):
            self.target()
    mocker.patch('vlc.threading.Thread', SyncThread)

    player.fadeout(duration_s=0.01)
    assert player.karaoke_volume == 300  # restored after fadeout


# --- try_reconnect ---

def test_try_reconnect_disabled(mock_config):
    filler = FillerVLC(mock_config, enabled=False)
    p = VlcKaraokePlayer(mock_config, filler, enabled=False)
    assert p.try_reconnect() is False


def test_try_reconnect_no_instance(player, mocker):
    mocker.patch.object(player, '_probe', return_value=None)
    player.enabled = True
    assert player.try_reconnect() is False


def test_try_reconnect_finds_stopped(player, mocker):
    mocker.patch.object(player, '_probe', return_value={"state": "stopped"})
    player.enabled = True
    assert player.try_reconnect() is True
    assert player.active is False


def test_try_reconnect_finds_playing(player, mocker):
    mocker.patch.object(player, '_probe', return_value={"state": "playing"})
    mocker.patch.object(player, '_load_state', return_value={'current_playing_path': '/s.mp4'})
    player.enabled = True
    assert player.try_reconnect() is True
    assert player.active is True
    assert player.current_path == '/s.mp4'


# --- shutdown ---

def test_shutdown_terminates(player, mocker):
    player.enabled = True
    mock_proc = mocker.Mock()
    mock_proc.poll.return_value = None
    player.process = mock_proc
    mocker.patch.object(player, 'ensure_released')
    player.shutdown()
    mock_proc.terminate.assert_called_once()
    assert player.process is None
    assert player.active is False


def test_shutdown_kills_orphan_when_no_process(player, mocker):
    player.process = None
    mocker.patch.object(player, 'ensure_released')
    kill = mocker.patch.object(VlcKaraokePlayer, '_kill_port')
    player.shutdown()
    kill.assert_called_once()
