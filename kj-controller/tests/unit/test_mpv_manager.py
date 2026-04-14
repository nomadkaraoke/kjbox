"""Tests for MpvManager: disabled-mode guards, IPC, pitch, volume conversion, state machine."""

import json
import os
import socket
import threading

import pytest

from mpv_manager import MpvManager, PITCH_MIN, PITCH_MAX


# --- Disabled-mode guard tests ---

def test_disabled_by_default_non_pi(mock_config, mocker):
    mocker.patch('mpv_manager.is_pi', return_value=False)
    mock_config.pop('enable_vlc', None)
    m = MpvManager(mock_config)
    assert m.enabled is False


def test_enabled_on_pi(mock_config, mocker):
    mocker.patch('mpv_manager.is_pi', return_value=True)
    m = MpvManager(mock_config)
    assert m.enabled is True


def test_enabled_via_config(mock_config, mocker):
    mocker.patch('mpv_manager.is_pi', return_value=False)
    mock_config['enable_vlc'] = True
    m = MpvManager(mock_config)
    assert m.enabled is True


def test_explicit_enabled_override(mock_config):
    m = MpvManager(mock_config, enabled=False)
    assert m.enabled is False


# --- Initial state ---

def test_initial_state(mock_config):
    m = MpvManager(mock_config, enabled=False)
    assert m.current_playing_path is None
    assert m.current_filler_track is None
    assert m.filler_volume == 100
    assert m.karaoke_volume == 200
    assert m.karaoke_active is False
    assert m.last_seek_time == 0
    assert m.last_play_time == 0
    assert m.audio_error is False
    assert m.audio_device == "hdmiout"
    assert m.pitch_semitones == 0


def test_volumes_from_config(mock_config):
    mock_config['karaoke_volume'] = 150
    mock_config['filler_volume'] = 80
    m = MpvManager(mock_config, enabled=False)
    assert m.karaoke_volume == 150
    assert m.filler_volume == 80


# --- Volume scale conversion ---

def test_vlc_to_mpv_volume_100_percent():
    assert MpvManager._vlc_to_mpv_volume(256) == 100.0


def test_vlc_to_mpv_volume_zero():
    assert MpvManager._vlc_to_mpv_volume(0) == 0.0


def test_vlc_to_mpv_volume_200_percent():
    assert MpvManager._vlc_to_mpv_volume(512) == 200.0


def test_vlc_to_mpv_volume_50_percent():
    assert MpvManager._vlc_to_mpv_volume(128) == 50.0


# --- Pitch control ---

def test_set_pitch_stores_value(mock_config):
    m = MpvManager(mock_config, enabled=False)
    m.set_pitch(3)
    assert m.pitch_semitones == 3


def test_set_pitch_clamps_max(mock_config):
    m = MpvManager(mock_config, enabled=False)
    m.set_pitch(10)
    assert m.pitch_semitones == PITCH_MAX


def test_set_pitch_clamps_min(mock_config):
    m = MpvManager(mock_config, enabled=False)
    m.set_pitch(-10)
    assert m.pitch_semitones == PITCH_MIN


def test_set_pitch_negative(mock_config):
    m = MpvManager(mock_config, enabled=False)
    m.set_pitch(-3)
    assert m.pitch_semitones == -3


def test_set_pitch_zero(mock_config):
    m = MpvManager(mock_config, enabled=False)
    m.pitch_semitones = 4
    m.set_pitch(0)
    assert m.pitch_semitones == 0


def test_set_pitch_sends_ipc_when_active(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    m.karaoke_active = True
    mock_ipc = mocker.patch.object(m, '_send_ipc')
    m.set_pitch(2)
    mock_ipc.assert_called_once()
    args = mock_ipc.call_args[0][0]
    assert args[0] == "af-command"
    assert args[1] == "rb"
    assert args[2] == "set-pitch"
    # 2 semitones up: 2^(2/12) ≈ 1.122462
    assert abs(float(args[3]) - 1.122462) < 0.001


def test_set_pitch_no_ipc_when_inactive(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    m.karaoke_active = False
    mock_ipc = mocker.patch.object(m, '_send_ipc')
    m.set_pitch(2)
    mock_ipc.assert_not_called()
    assert m.pitch_semitones == 2


def test_apply_pitch_formula(mock_config, mocker):
    """Verify the pitch scale formula for various semitone values."""
    m = MpvManager(mock_config, enabled=False)
    m.karaoke_active = True
    mock_ipc = mocker.patch.object(m, '_send_ipc')

    # Test a few known values
    test_cases = [
        (0, 1.0),
        (1, 1.059463),
        (-1, 0.943874),
        (6, 1.414214),
        (-6, 0.707107),
    ]
    for semitones, expected_scale in test_cases:
        mock_ipc.reset_mock()
        m.set_pitch(semitones)
        args = mock_ipc.call_args[0][0]
        actual = float(args[3])
        assert abs(actual - expected_scale) < 0.001, f"semitones={semitones}: expected {expected_scale}, got {actual}"


# --- Karaoke status ---

def test_get_karaoke_status_stopped(mock_config):
    m = MpvManager(mock_config, enabled=False)
    m.karaoke_active = False
    status = m.get_karaoke_status()
    assert status == {"state": "stopped", "time": 0, "length": 0}


def test_get_karaoke_status_playing(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    m.karaoke_active = True
    mocker.patch.object(m, '_get_property', side_effect=lambda name: {
        "pause": False, "time-pos": 42.5, "duration": 210.0
    }[name])
    status = m.get_karaoke_status()
    assert status == {"state": "playing", "time": 42, "length": 210}


def test_get_karaoke_status_paused(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    m.karaoke_active = True
    mocker.patch.object(m, '_get_property', side_effect=lambda name: {
        "pause": True, "time-pos": 10.0, "duration": 120.0
    }[name])
    status = m.get_karaoke_status()
    assert status == {"state": "paused", "time": 10, "length": 120}


def test_get_karaoke_status_mpv_unresponsive(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    m.karaoke_active = True
    mocker.patch.object(m, '_get_property', return_value=None)
    status = m.get_karaoke_status()
    assert status == {"state": "stopped", "time": 0, "length": 0}


# --- Karaoke control methods ---

def test_seek_karaoke(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    mock_ipc = mocker.patch.object(m, '_send_ipc')
    m.seek_karaoke(90)
    mock_ipc.assert_called_once_with(["seek", 90, "absolute"])
    assert m.last_seek_time > 0


def test_pause_resume_karaoke(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    mocker.patch.object(m, '_get_property', return_value=False)
    mock_set = mocker.patch.object(m, '_set_property', return_value=True)
    result = m.pause_resume_karaoke()
    assert result is True  # Now paused
    mock_set.assert_called_once_with("pause", True)


def test_set_karaoke_volume_live(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    mock_set = mocker.patch.object(m, '_set_property')
    m.set_karaoke_volume_live(256)  # 100% in VLC scale
    assert m.karaoke_volume == 256
    mock_set.assert_called_once_with("volume", 100.0)


def test_stop_karaoke(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    m.karaoke_active = True
    m.current_playing_path = "/some/file.mp4"
    m.audio_error = True
    mock_ipc = mocker.patch.object(m, '_send_ipc')
    mocker.patch.object(m, '_save_state')
    m.stop_karaoke()
    mock_ipc.assert_called_once_with(["stop"])
    assert m.karaoke_active is False
    assert m.current_playing_path is None
    assert m.audio_error is False


# --- State persistence ---

def test_save_and_load_state(mock_config, tmp_path, mocker):
    import mpv_manager
    state_file = str(tmp_path / "state.json")
    mocker.patch.object(mpv_manager, 'STATE_FILE', state_file)
    m = MpvManager(mock_config, enabled=False)
    m.current_playing_path = "/test/song.mp4"
    m.current_filler_track = "filler.mp3"
    m._save_state()

    loaded = m._load_state()
    assert loaded['current_playing_path'] == "/test/song.mp4"
    assert loaded['current_filler_track'] == "filler.mp3"


# --- Handle karaoke ended ---

def test_handle_karaoke_ended(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    m.karaoke_active = True
    m.current_playing_path = "/test/song.mp4"
    m.pitch_semitones = 3
    callback = mocker.Mock()
    m.on_karaoke_end = callback
    mocker.patch.object(m, '_save_state')
    mocker.patch.object(m, 'fade_in_filler')

    m._handle_karaoke_ended()

    assert m.karaoke_active is False
    assert m.current_playing_path is None
    assert m.pitch_semitones == 0
    callback.assert_called_once()
    m.fade_in_filler.assert_called_once()


def test_handle_karaoke_ended_noop_when_inactive(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    m.karaoke_active = False
    callback = mocker.Mock()
    m.on_karaoke_end = callback
    m._handle_karaoke_ended()
    callback.assert_not_called()


# --- IPC communication ---

def _short_sock_path(tmp_path):
    """Return a short Unix socket path (macOS limits to ~104 chars)."""
    import tempfile
    d = tempfile.mkdtemp()
    return os.path.join(d, "s")


def _make_fake_ipc_server(socket_path, responses):
    """Create a fake mpv IPC server that returns canned responses."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(5)
    server.settimeout(5)

    def serve():
        for resp in responses:
            try:
                conn, _ = server.accept()
                conn.recv(4096)  # Read the command
                conn.sendall((json.dumps(resp) + "\n").encode())
                conn.close()
            except socket.timeout:
                break
        server.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return t


def test_send_ipc_success(mock_config, tmp_path):
    sock_path = _short_sock_path(tmp_path)
    m = MpvManager(mock_config, enabled=False)
    m.ipc_socket_path = sock_path

    response = {"data": True, "request_id": 0, "error": "success"}
    _make_fake_ipc_server(sock_path, [response])

    result = m._send_ipc(["get_property", "idle-active"])
    assert result is not None
    assert result["data"] is True


def test_send_ipc_connection_error(mock_config, tmp_path):
    m = MpvManager(mock_config, enabled=False)
    m.ipc_socket_path = str(tmp_path / "nonexistent.sock")
    result = m._send_ipc(["get_property", "idle-active"])
    assert result is None


def test_get_property(mock_config, tmp_path):
    sock_path = _short_sock_path(tmp_path)
    m = MpvManager(mock_config, enabled=False)
    m.ipc_socket_path = sock_path

    _make_fake_ipc_server(sock_path, [
        {"data": 42.5, "request_id": 0, "error": "success"}
    ])

    result = m._get_property("time-pos")
    assert result == 42.5


def test_get_property_error(mock_config, tmp_path):
    sock_path = _short_sock_path(tmp_path)
    m = MpvManager(mock_config, enabled=False)
    m.ipc_socket_path = sock_path

    _make_fake_ipc_server(sock_path, [
        {"request_id": 0, "error": "property not found"}
    ])

    result = m._get_property("nonexistent")
    assert result is None


# --- play_video ---

def test_play_video_file_not_found(mock_config, mocker):
    m = MpvManager(mock_config, enabled=True)
    mock_ipc = mocker.patch.object(m, '_send_ipc')
    m.play_video("/nonexistent/file.mp4")
    mock_ipc.assert_not_called()
    assert m.karaoke_active is False


def test_play_video_disabled(mock_config, tmp_media_dir, mocker):
    m = MpvManager(mock_config, enabled=False)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake")
    mock_ipc = mocker.patch.object(m, '_send_ipc')
    m.play_video(str(test_file))
    mock_ipc.assert_not_called()
    assert m.karaoke_active is False


def test_play_video_success(mock_config, tmp_media_dir, mocker):
    m = MpvManager(mock_config, enabled=True)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    # Mock all external calls
    mocker.patch.object(m, 'send_command', return_value={"state": "stopped"})
    mocker.patch.object(m, 'fade_out_filler')
    mocker.patch.object(m, 'ensure_filler_stopped')
    mocker.patch.object(m, '_send_ipc', return_value={"data": {"playlist_entry_id": 1}, "error": "success"})
    mocker.patch.object(m, '_set_property')
    mocker.patch.object(m, '_save_state')

    m.play_video(str(test_file), display_path="/display/song.mp4")

    assert m.karaoke_active is True
    assert m.current_playing_path == "/display/song.mp4"
    assert m.pitch_semitones == 0  # Reset on new song
    assert m.audio_error is False


def test_play_video_resets_pitch(mock_config, tmp_media_dir, mocker):
    m = MpvManager(mock_config, enabled=True)
    m.pitch_semitones = 4
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    mocker.patch.object(m, 'send_command', return_value={"state": "stopped"})
    mocker.patch.object(m, 'fade_out_filler')
    mocker.patch.object(m, 'ensure_filler_stopped')
    mocker.patch.object(m, '_send_ipc', return_value={"error": "success", "data": {"playlist_entry_id": 1}})
    mocker.patch.object(m, '_set_property')
    mocker.patch.object(m, '_save_state')

    m.play_video(str(test_file))
    assert m.pitch_semitones == 0


def test_play_video_sets_overlay(mock_config, tmp_media_dir, mocker):
    m = MpvManager(mock_config, enabled=True)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    mocker.patch.object(m, 'send_command', return_value={"state": "stopped"})
    mocker.patch.object(m, 'fade_out_filler')
    mocker.patch.object(m, 'ensure_filler_stopped')
    mocker.patch.object(m, '_send_ipc', return_value={"error": "success", "data": {"playlist_entry_id": 1}})
    mocker.patch.object(m, '_set_property')
    mocker.patch.object(m, '_save_state')

    overlay = mocker.Mock()
    m.play_video(str(test_file), overlay_manager=overlay)
    overlay.set_karaoke_playing.assert_called_once_with(True)


def test_play_video_skips_filler_fade_when_already_stopped(mock_config, tmp_media_dir, mocker):
    m = MpvManager(mock_config, enabled=True)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    mocker.patch.object(m, 'send_command', return_value={"state": "stopped"})
    fade_mock = mocker.patch.object(m, 'fade_out_filler')
    mocker.patch.object(m, '_send_ipc', return_value={"error": "success", "data": {"playlist_entry_id": 1}})
    mocker.patch.object(m, '_set_property')
    mocker.patch.object(m, '_save_state')

    m.play_video(str(test_file))
    fade_mock.assert_not_called()


def test_play_video_loadfile_failure(mock_config, tmp_media_dir, mocker):
    m = MpvManager(mock_config, enabled=True)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    mocker.patch.object(m, 'send_command', return_value={"state": "stopped"})
    mocker.patch.object(m, '_send_ipc', return_value=None)
    mocker.patch.object(m, '_set_property')
    mocker.patch.object(m, '_save_state')

    m.play_video(str(test_file))
    assert m.audio_error is True


# --- ensure_karaoke_released ---

def test_ensure_karaoke_released_success(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    mocker.patch.object(m, '_send_ipc')
    mocker.patch.object(m, '_get_property', return_value=True)
    assert m.ensure_karaoke_released() is True


def test_ensure_karaoke_released_retries(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    mocker.patch.object(m, '_send_ipc')
    mocker.patch.object(m, '_get_property', side_effect=[False, False, True])
    mocker.patch('mpv_manager.time.sleep')
    assert m.ensure_karaoke_released() is True


# --- mpv_is_running ---

def test_mpv_is_running_true(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    mocker.patch.object(m, '_get_property', return_value=True)
    assert m._mpv_is_running() is True


def test_mpv_is_running_false(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    mocker.patch.object(m, '_get_property', return_value=None)
    assert m._mpv_is_running() is False


# --- try_reconnect ---

def test_try_reconnect_disabled(mock_config):
    m = MpvManager(mock_config, enabled=False)
    result = m.try_reconnect()
    assert result == {'karaoke': False, 'filler': False}


def test_try_reconnect_finds_idle_mpv(mock_config, mocker):
    m = MpvManager(mock_config, enabled=True)
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch.object(m, '_get_property', return_value=True)  # idle=True
    mocker.patch.object(m, '_probe_vlc', return_value=None)
    mocker.patch.object(m, '_load_state', return_value={})

    result = m.try_reconnect()
    assert result['karaoke'] is True
    assert m.karaoke_active is False


def test_try_reconnect_finds_playing_mpv(mock_config, mocker):
    m = MpvManager(mock_config, enabled=True)
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch.object(m, '_get_property', return_value=False)  # idle=False (playing)
    mocker.patch.object(m, '_probe_vlc', return_value=None)
    mocker.patch.object(m, '_load_state', return_value={'current_playing_path': '/test.mp4'})

    result = m.try_reconnect()
    assert result['karaoke'] is True
    assert m.karaoke_active is True
    assert m.current_playing_path == '/test.mp4'


def test_try_reconnect_finds_filler(mock_config, mocker):
    m = MpvManager(mock_config, enabled=True)
    mocker.patch('os.path.exists', return_value=False)  # no mpv socket
    mocker.patch.object(m, '_probe_vlc', return_value={"state": "playing"})
    mocker.patch.object(m, '_load_state', return_value={'current_filler_track': 'song.mp3'})

    result = m.try_reconnect()
    assert result['karaoke'] is False
    assert result['filler'] is True
    assert m.current_filler_track == 'song.mp3'


# --- Filler VLC methods ---

def test_fade_in_filler_disabled(mock_config):
    m = MpvManager(mock_config, enabled=False)
    m.fade_in_filler()  # Should not raise


def test_fade_out_filler_disabled(mock_config):
    m = MpvManager(mock_config, enabled=False)
    m.fade_out_filler()  # Should not raise


def test_ensure_filler_stopped_success(mock_config, mocker):
    m = MpvManager(mock_config, enabled=True)
    mocker.patch.object(m, 'send_command', return_value={"state": "stopped"})
    assert m.ensure_filler_stopped() is True


def test_ensure_filler_stopped_retries(mock_config, mocker):
    m = MpvManager(mock_config, enabled=True)
    mocker.patch.object(m, 'send_command', side_effect=[
        {"state": "playing"}, None,
        {"state": "playing"}, None,
        {"state": "stopped"},
    ])
    mocker.patch('mpv_manager.time.sleep')
    assert m.ensure_filler_stopped() is True


# --- send_command (filler VLC) ---

def test_send_command_disabled(mock_config):
    m = MpvManager(mock_config, enabled=False)
    assert m.send_command(8081, "filler", "pl_play") is None


def test_send_command_success(mock_config, mocker):
    m = MpvManager(mock_config, enabled=True)
    mock_resp = mocker.Mock()
    mock_resp.json.return_value = {"state": "playing"}
    mock_resp.raise_for_status = mocker.Mock()
    mock_session = mocker.Mock()
    mock_session.get.return_value = mock_resp
    mocker.patch('mpv_manager.requests.Session', return_value=mock_session)
    result = m.send_command(8081, "filler", "pl_play")
    assert result == {"state": "playing"}


# --- restart_instances ---

def test_restart_instances_disabled(mock_config):
    m = MpvManager(mock_config, enabled=False)
    m.restart_instances()  # Should not raise


def test_restart_instances_kills_and_relaunches(mock_config, mocker):
    m = MpvManager(mock_config, enabled=True)
    mock_karaoke_proc = mocker.Mock()
    mock_karaoke_proc.poll.return_value = None  # running
    mock_filler_proc = mocker.Mock()
    mock_filler_proc.poll.return_value = None  # running
    m.processes = {"karaoke": mock_karaoke_proc, "filler": mock_filler_proc}
    m.karaoke_active = True
    m.pitch_semitones = 3

    mocker.patch.object(m, '_launch_mpv_karaoke')
    mocker.patch.object(m, '_launch_vlc_filler')
    mocker.patch.object(m, 'fade_in_filler')
    mocker.patch.object(m, '_save_state')
    mocker.patch('mpv_manager.time.sleep')
    mocker.patch('os.unlink')

    m.restart_instances()

    mock_karaoke_proc.terminate.assert_called_once()
    mock_filler_proc.terminate.assert_called_once()
    assert m.karaoke_active is False
    assert m.pitch_semitones == 0
    m._launch_mpv_karaoke.assert_called_once()
    m._launch_vlc_filler.assert_called_once()
    m.fade_in_filler.assert_called_once()


# --- launch_instance dispatch ---

def test_launch_instance_disabled(mock_config, mocker):
    m = MpvManager(mock_config, enabled=False)
    mocker.patch.object(m, '_launch_mpv_karaoke')
    m.launch_instance("karaoke")
    m._launch_mpv_karaoke.assert_not_called()


def test_launch_instance_karaoke(mock_config, mocker):
    m = MpvManager(mock_config, enabled=True)
    mock_launch = mocker.patch.object(m, '_launch_mpv_karaoke')
    m.launch_instance("karaoke")
    mock_launch.assert_called_once()


def test_launch_instance_filler(mock_config, mocker):
    m = MpvManager(mock_config, enabled=True)
    mock_launch = mocker.patch.object(m, '_launch_vlc_filler')
    m.launch_instance("filler", 8081, "filler", "/music/song.mp3", True)
    mock_launch.assert_called_once_with(8081, "filler", "/music/song.mp3", True)


# --- _launch_mpv_karaoke ---

def test_launch_mpv_karaoke_already_running(mock_config, mocker):
    m = MpvManager(mock_config, enabled=True)
    mock_proc = mocker.Mock()
    mock_proc.poll.return_value = None  # still running
    m.processes["karaoke"] = mock_proc
    mocker.patch('subprocess.Popen')
    m._launch_mpv_karaoke()
    # Should not launch a second instance
    import subprocess
    subprocess.Popen.assert_not_called()


def test_launch_mpv_karaoke_mpv_not_found(mock_config, mocker):
    m = MpvManager(mock_config, enabled=True)
    m.processes["karaoke"] = None
    mocker.patch('os.unlink', side_effect=FileNotFoundError)
    mocker.patch('builtins.open', mocker.mock_open())
    mocker.patch('subprocess.Popen', side_effect=FileNotFoundError)
    m._launch_mpv_karaoke()
    assert m.processes["karaoke"] is None


# --- _launch_vlc_filler ---

def test_launch_vlc_filler_already_running(mock_config, mocker):
    m = MpvManager(mock_config, enabled=True)
    mock_proc = mocker.Mock()
    mock_proc.poll.return_value = None
    m.processes["filler"] = mock_proc
    mocker.patch('subprocess.Popen')
    m._launch_vlc_filler(8081, "filler")
    import subprocess
    subprocess.Popen.assert_not_called()


# --- _monitor_via_polling ---

def test_monitor_polling_detects_end(mock_config, mocker):
    """Polling fallback detects idle state and triggers karaoke ended."""
    m = MpvManager(mock_config, enabled=False)
    m.karaoke_active = True
    m.last_seek_time = 0
    m.last_play_time = 0

    mocker.patch.object(m, '_get_property', return_value=True)  # idle-active=True
    handle_mock = mocker.patch.object(m, '_handle_karaoke_ended')
    sleep_mock = mocker.patch('mpv_manager.time.sleep')
    # Make the loop run once then stop
    call_count = [0]
    original_sleep = sleep_mock.side_effect

    def limited_sleep(t):
        call_count[0] += 1
        if call_count[0] > 2:
            raise StopIteration  # Break the loop

    sleep_mock.side_effect = limited_sleep

    try:
        m._monitor_via_polling()
    except StopIteration:
        pass

    handle_mock.assert_called()


def test_monitor_polling_skips_during_seek(mock_config, mocker):
    """Polling fallback skips check briefly after seek."""
    import time as real_time
    m = MpvManager(mock_config, enabled=False)
    m.karaoke_active = True
    m.last_seek_time = real_time.time()  # Just seeked
    m.last_play_time = 0

    get_mock = mocker.patch.object(m, '_get_property')
    sleep_mock = mocker.patch('mpv_manager.time.sleep')
    call_count = [0]

    def limited_sleep(t):
        call_count[0] += 1
        if call_count[0] > 1:
            raise StopIteration

    sleep_mock.side_effect = limited_sleep

    try:
        m._monitor_via_polling()
    except StopIteration:
        pass

    get_mock.assert_not_called()  # Should skip due to recent seek


# --- Volume conversion edge cases ---

def test_vlc_to_mpv_volume_low():
    assert MpvManager._vlc_to_mpv_volume(31) == pytest.approx(12.109, abs=0.1)


def test_vlc_to_mpv_volume_filler_default():
    assert MpvManager._vlc_to_mpv_volume(110) == pytest.approx(42.97, abs=0.1)
