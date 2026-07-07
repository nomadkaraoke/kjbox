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


# --- Engine death detection (crash → auto-recovery) ---

class _FakeProc:
    """Popen stand-in: poll() is None while alive, else the exit code."""
    def __init__(self, code):
        self._code = code

    def poll(self):
        return self._code

    @property
    def returncode(self):
        return self._code


def test_notify_if_dead_fires_callback_on_crash(player):
    fired = []
    player.on_engine_died = lambda info: fired.append(info)
    player.process = _FakeProc(-11)
    assert player._notify_if_dead() is True
    assert len(fired) == 1
    assert fired[0]['engine'] == 'vlc'
    assert fired[0]['returncode'] == -11


def test_notify_if_dead_noop_when_alive(player):
    fired = []
    player.on_engine_died = lambda info: fired.append(info)
    player.process = _FakeProc(None)
    assert player._notify_if_dead() is False
    assert fired == []


def test_notify_if_dead_noop_during_intentional_shutdown(player):
    fired = []
    player.on_engine_died = lambda info: fired.append(info)
    player.process = _FakeProc(-15)
    player._monitor_stop.set()
    assert player._notify_if_dead() is False
    assert fired == []


def test_notify_if_dead_fires_only_once(player):
    fired = []
    player.on_engine_died = lambda info: fired.append(info)
    player.process = _FakeProc(-11)
    assert player._notify_if_dead() is True
    assert player._notify_if_dead() is False
    assert len(fired) == 1


def test_notify_if_dead_reconnected_fires_when_probe_dead(player, mocker):
    # Reconnected VLC (no Popen handle): death via HTTP probe, debounced.
    fired = []
    player.on_engine_died = lambda info: fired.append(info)
    player.process = None
    mocker.patch.object(player, '_probe', return_value=None)
    assert player._notify_if_dead() is False   # 1st failure — debounce
    assert player._notify_if_dead() is True     # 2nd consecutive — dead
    assert len(fired) == 1
    assert fired[0]['engine'] == 'vlc'


def test_notify_if_dead_reconnected_noop_when_probe_alive(player, mocker):
    fired = []
    player.on_engine_died = lambda info: fired.append(info)
    player.process = None
    mocker.patch.object(player, '_probe', return_value={'state': 'stopped'})
    assert player._notify_if_dead() is False
    assert player._notify_if_dead() is False
    assert fired == []


# --- Reserved-strip video geometry (gated behind video_strip_vlc) ---

def test_video_window_args_fullscreen_by_default():
    # margin>0 but VLC strip disabled -> fullscreen (documented fallback).
    assert VlcKaraokePlayer._video_window_args(80, strip_vlc=False) == ['--fullscreen']


def test_video_window_args_fullscreen_when_no_margin():
    assert VlcKaraokePlayer._video_window_args(0, strip_vlc=True) == ['--fullscreen']


def test_video_window_args_windowed_when_enabled_and_margin():
    assert VlcKaraokePlayer._video_window_args(80, strip_vlc=True) == []


def _patch_vlc_launch(mocker):
    mocker.patch('builtins.open', mocker.mock_open())
    mock_popen = mocker.patch('subprocess.Popen')
    mock_popen.return_value.pid = 4321
    mock_popen.return_value.poll.return_value = None
    mocker.patch('vlc.time.sleep')
    return mock_popen


def test_launch_fullscreen_by_default(mock_config, mocker):
    filler = FillerVLC(mock_config, enabled=False)
    p = VlcKaraokePlayer(mock_config, filler, enabled=True)
    mock_popen = _patch_vlc_launch(mocker)
    reposition = mocker.patch.object(p, '_reposition_window')
    p.launch()
    args = mock_popen.call_args[0][0]
    assert '--fullscreen' in args
    reposition.assert_not_called()


def test_launch_windowed_repositions_when_strip_enabled(mock_config, mocker):
    mock_config['video_top_margin_px'] = 80
    mock_config['video_strip_vlc'] = True
    filler = FillerVLC(mock_config, enabled=False)
    p = VlcKaraokePlayer(mock_config, filler, enabled=True)
    mock_popen = _patch_vlc_launch(mocker)
    reposition = mocker.patch.object(p, '_reposition_window')
    p.launch()
    args = mock_popen.call_args[0][0]
    assert '--fullscreen' not in args
    reposition.assert_called_once_with(80, 1920, 1080)


def test_reposition_window_runs_wmctrl_geometry(mock_config, mocker):
    filler = FillerVLC(mock_config, enabled=False)
    p = VlcKaraokePlayer(mock_config, filler, enabled=True)
    run = mocker.patch('vlc.subprocess.run',
                       return_value=mocker.Mock(returncode=0, stderr=b''))
    p._reposition_window(80, 1920, 1080)
    cmds = [c.args[0] for c in run.call_args_list]
    # Remove any fullscreen state, then place the window below the 80px strip.
    assert any('remove,fullscreen' in c for c in cmds)
    assert any('0,0,80,1920,1000' in c for c in cmds)


def test_reposition_window_stops_on_wmctrl_nonzero_exit(mock_config, mocker):
    # A window-title miss (wmctrl exit 1) must not log the success line.
    filler = FillerVLC(mock_config, enabled=False)
    p = VlcKaraokePlayer(mock_config, filler, enabled=True)
    mocker.patch('vlc.subprocess.run',
                 return_value=mocker.Mock(returncode=1, stderr=b'Cannot find window'))
    logs = mocker.patch('vlc.log_message')
    p._reposition_window(80, 1920, 1080)
    msgs = ' '.join(str(c.args[0]) for c in logs.call_args_list)
    assert 'non-zero exit' in msgs
    assert 'Positioned karaoke VLC' not in msgs


def test_reposition_window_swallows_wmctrl_errors(mock_config, mocker):
    # wmctrl absent / failing must never crash launch — VLC just stays put.
    filler = FillerVLC(mock_config, enabled=False)
    p = VlcKaraokePlayer(mock_config, filler, enabled=True)
    mocker.patch('vlc.subprocess.run', side_effect=FileNotFoundError)
    mocker.patch('vlc.log_message')
    p._reposition_window(80, 1920, 1080)  # must not raise
