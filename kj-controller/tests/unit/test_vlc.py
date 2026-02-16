"""Tests for VLCManager: disabled-mode guards, HTTP communication, and state machine logic."""

import time

from vlc import VLCManager


# --- Disabled-mode guard tests ---

def test_vlc_disabled_by_default_non_pi(mock_config, mocker):
    """Non-Pi environment gets enabled=False."""
    mocker.patch('vlc.is_pi', return_value=False)
    vm = VLCManager(mock_config)
    assert vm.enabled is False


def test_vlc_enabled_on_pi(mock_config, mocker):
    """Pi environment gets enabled=True."""
    mocker.patch('vlc.is_pi', return_value=True)
    vm = VLCManager(mock_config)
    assert vm.enabled is True


def test_vlc_explicit_enabled_override(mock_config):
    """Explicit enabled=False overrides platform detection."""
    vm = VLCManager(mock_config, enabled=False)
    assert vm.enabled is False


def test_send_command_noop_when_disabled(mock_config):
    """send_command returns None when VLC is disabled."""
    vm = VLCManager(mock_config, enabled=False)
    result = vm.send_command(8080, "karaoke", "pl_play")
    assert result is None


def test_play_video_noop_when_disabled(mock_config, tmp_media_dir):
    """play_video logs but doesn't crash when VLC is disabled."""
    vm = VLCManager(mock_config, enabled=False)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")
    vm.play_video(str(test_file))
    assert vm.karaoke_active is False


def test_initial_state(mock_config):
    """Verify default attribute values."""
    vm = VLCManager(mock_config, enabled=False)
    assert vm.current_playing_path is None
    assert vm.current_filler_track is None
    assert vm.filler_volume == 100
    assert vm.karaoke_volume == 200
    assert vm.karaoke_active is False
    assert vm.last_seek_time == 0
    assert vm.audio_error is False
    assert vm.audio_device == "hdmiout"


def test_fade_in_filler_noop_when_disabled(mock_config):
    """fade_in_filler is a no-op when disabled."""
    vm = VLCManager(mock_config, enabled=False)
    vm.fade_in_filler()  # Should not raise


def test_fade_out_filler_noop_when_disabled(mock_config):
    """fade_out_filler is a no-op when disabled."""
    vm = VLCManager(mock_config, enabled=False)
    vm.fade_out_filler()  # Should not raise


def test_restart_instances_noop_when_disabled(mock_config):
    """restart_instances is a no-op when disabled."""
    vm = VLCManager(mock_config, enabled=False)
    vm.restart_instances()  # Should not raise


# --- send_command URL construction (mocked HTTP) ---

def test_send_command_simple_command_url(mock_config, mocker):
    """Simple command (no &) uses input-encoding URL path."""
    vm = VLCManager(mock_config, enabled=True)
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = {"state": "playing"}
    mock_response.raise_for_status = mocker.MagicMock()

    mock_session = mocker.MagicMock()
    mock_session.get.return_value = mock_response
    mocker.patch('vlc.requests.Session', return_value=mock_session)

    result = vm.send_command(8080, "karaoke", "pl_play")
    assert result == {"state": "playing"}
    url = mock_session.get.call_args[0][0]
    assert "command=pl_play" in url
    assert "input=" in url  # empty input gets appended


def test_send_command_ampersand_command_url(mock_config, mocker):
    """Command with & (like volume&val=100) uses direct URL construction."""
    vm = VLCManager(mock_config, enabled=True)
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = {"state": "playing"}
    mock_response.raise_for_status = mocker.MagicMock()

    mock_session = mocker.MagicMock()
    mock_session.get.return_value = mock_response
    mocker.patch('vlc.requests.Session', return_value=mock_session)

    vm.send_command(8080, "karaoke", "volume&val=150")
    url = mock_session.get.call_args[0][0]
    assert url == "http://localhost:8080/requests/status.json?command=volume&val=150"


def test_send_command_path_input_url_encoded(mock_config, mocker):
    """is_path=True causes input to be URL-encoded (spaces, special chars)."""
    vm = VLCManager(mock_config, enabled=True)
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = {}
    mock_response.raise_for_status = mocker.MagicMock()

    mock_session = mocker.MagicMock()
    mock_session.get.return_value = mock_response
    mocker.patch('vlc.requests.Session', return_value=mock_session)

    vm.send_command(8080, "karaoke", "in_enqueue&input=/path/to/my song.mp4", is_path=True)
    url = mock_session.get.call_args[0][0]
    assert "command=in_enqueue" in url
    # requests.utils.quote encodes spaces but preserves slashes by default
    assert "my%20song.mp4" in url
    assert "/path/to/" in url


def test_send_command_empty_command_for_status(mock_config, mocker):
    """Empty command string fetches current status."""
    vm = VLCManager(mock_config, enabled=True)
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = {"state": "stopped", "time": 0, "length": 0}
    mock_response.raise_for_status = mocker.MagicMock()

    mock_session = mocker.MagicMock()
    mock_session.get.return_value = mock_response
    mocker.patch('vlc.requests.Session', return_value=mock_session)

    result = vm.send_command(8080, "karaoke", "")
    assert result["state"] == "stopped"


def test_send_command_connection_error_returns_none(mock_config, mocker):
    """Connection refused (VLC not running) returns None."""
    import requests as req
    vm = VLCManager(mock_config, enabled=True)

    mock_session = mocker.MagicMock()
    mock_session.get.side_effect = req.exceptions.ConnectionError("Connection refused")
    mocker.patch('vlc.requests.Session', return_value=mock_session)

    result = vm.send_command(8080, "karaoke", "pl_play")
    assert result is None


def test_send_command_http_error_returns_none(mock_config, mocker):
    """HTTP 500 from VLC returns None."""
    import requests as req
    vm = VLCManager(mock_config, enabled=True)

    mock_response = mocker.MagicMock()
    mock_response.raise_for_status.side_effect = req.exceptions.HTTPError("500 Server Error")
    mock_session = mocker.MagicMock()
    mock_session.get.return_value = mock_response
    mocker.patch('vlc.requests.Session', return_value=mock_session)

    result = vm.send_command(8080, "karaoke", "pl_play")
    assert result is None


def test_send_command_json_decode_error_returns_none(mock_config, mocker):
    """Malformed JSON response returns None."""
    vm = VLCManager(mock_config, enabled=True)

    mock_response = mocker.MagicMock()
    mock_response.raise_for_status = mocker.MagicMock()
    mock_response.json.side_effect = ValueError("No JSON")
    mock_session = mocker.MagicMock()
    mock_session.get.return_value = mock_response
    mocker.patch('vlc.requests.Session', return_value=mock_session)

    result = vm.send_command(8080, "karaoke", "pl_play")
    assert result is None


# --- ensure_filler_stopped retry logic ---

def test_ensure_filler_stopped_succeeds_first_try(mock_config, mocker):
    """Returns True when filler reports stopped immediately."""
    vm = VLCManager(mock_config, enabled=True)
    mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    mocker.patch('vlc.time.sleep')

    assert vm.ensure_filler_stopped() is True


def test_ensure_filler_stopped_succeeds_after_retries(mock_config, mocker):
    """Returns True when filler stops after a few retries."""
    vm = VLCManager(mock_config, enabled=True)
    responses = [
        {"state": "playing"},  # attempt 0: status check
        None,                  # attempt 0: pl_stop
        {"state": "playing"},  # attempt 1: status check
        None,                  # attempt 1: pl_stop
        {"state": "stopped"},  # attempt 2: status check → success
    ]
    mocker.patch.object(vm, 'send_command', side_effect=responses)
    mocker.patch('vlc.time.sleep')

    assert vm.ensure_filler_stopped() is True


def test_ensure_filler_stopped_fails_after_max_attempts(mock_config, mocker):
    """Returns False after 5 failed attempts."""
    vm = VLCManager(mock_config, enabled=True)
    mocker.patch.object(vm, 'send_command', return_value={"state": "playing"})
    mocker.patch('vlc.time.sleep')

    assert vm.ensure_filler_stopped() is False


# --- play_video state transitions (mock send_command + time) ---

def test_play_video_sets_karaoke_active(mock_config, tmp_media_dir, mocker):
    """play_video sets karaoke_active=True and clears audio_error."""
    vm = VLCManager(mock_config, enabled=True)
    vm.audio_error = True

    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    mocker.patch.object(vm, 'fade_out_filler')
    mocker.patch.object(vm, 'ensure_filler_stopped', return_value=True)
    mocker.patch('vlc.time.sleep')
    mocker.patch('vlc.threading.Thread')  # prevent verify_playback thread

    vm.play_video(str(test_file))
    assert vm.karaoke_active is True
    assert vm.audio_error is False


def test_play_video_sends_correct_command_sequence(mock_config, tmp_media_dir, mocker):
    """play_video sends pl_empty, enqueue, volume, pl_play in order."""
    vm = VLCManager(mock_config, enabled=True)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    send_mock = mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    mocker.patch.object(vm, 'fade_out_filler')
    mocker.patch.object(vm, 'ensure_filler_stopped', return_value=True)
    mocker.patch('vlc.time.sleep')
    mocker.patch('vlc.threading.Thread')

    vm.play_video(str(test_file))

    commands = [call.args[2] for call in send_mock.call_args_list]
    assert commands[0] == "pl_empty"
    assert commands[1].startswith("in_enqueue&input=")
    assert str(test_file) in commands[1]
    assert commands[2] == f"volume&val={vm.karaoke_volume}"
    assert commands[3] == "pl_play"


def test_play_video_nonexistent_file_aborts(mock_config, mocker):
    """play_video returns early for a nonexistent file."""
    vm = VLCManager(mock_config, enabled=True)
    send_mock = mocker.patch.object(vm, 'send_command')

    vm.play_video("/nonexistent/video.mp4")
    assert vm.karaoke_active is False
    send_mock.assert_not_called()


def test_play_video_verify_playback_detects_audio_error(mock_config, tmp_media_dir, mocker):
    """verify_playback sets audio_error=True when VLC isn't playing."""
    vm = VLCManager(mock_config, enabled=True)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    # send_command returns stopped (simulating audio device conflict)
    mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    mocker.patch.object(vm, 'fade_out_filler')
    mocker.patch.object(vm, 'ensure_filler_stopped', return_value=True)
    mocker.patch('vlc.time.sleep')

    # Capture the Thread target instead of running it
    thread_calls = []
    original_thread = mocker.MagicMock
    def capture_thread(**kwargs):
        mock_thread = mocker.MagicMock()
        if 'target' in kwargs:
            thread_calls.append(kwargs['target'])
        return mock_thread
    mocker.patch('vlc.threading.Thread', side_effect=capture_thread)

    vm.play_video(str(test_file))
    assert vm.karaoke_active is True

    # Run the captured verify_playback callback
    assert len(thread_calls) == 1
    thread_calls[0]()  # execute verify_playback
    assert vm.audio_error is True


def test_play_video_verify_playback_clears_on_success(mock_config, tmp_media_dir, mocker):
    """verify_playback keeps audio_error=False when VLC is playing."""
    vm = VLCManager(mock_config, enabled=True)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    call_count = [0]
    def mock_send(*args, **kwargs):
        call_count[0] += 1
        # First 4 calls are play_video commands, 5th is verify_playback status check
        if call_count[0] <= 4:
            return {"state": "stopped"}
        return {"state": "playing"}

    mocker.patch.object(vm, 'send_command', side_effect=mock_send)
    mocker.patch.object(vm, 'fade_out_filler')
    mocker.patch.object(vm, 'ensure_filler_stopped', return_value=True)
    mocker.patch('vlc.time.sleep')

    # Capture the Thread target instead of running it
    thread_calls = []
    def capture_thread(**kwargs):
        mock_thread = mocker.MagicMock()
        if 'target' in kwargs:
            thread_calls.append(kwargs['target'])
        return mock_thread
    mocker.patch('vlc.threading.Thread', side_effect=capture_thread)

    vm.play_video(str(test_file))
    thread_calls[0]()  # execute verify_playback
    assert vm.audio_error is False


# --- monitor_karaoke state machine ---

def test_monitor_karaoke_detects_song_end(mock_config, mocker):
    """monitor detects stopped state and resets karaoke_active."""
    vm = VLCManager(mock_config, enabled=True)
    vm.karaoke_active = True
    vm.current_playing_path = "/some/video.mp4"

    mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    mocker.patch.object(vm, 'fade_in_filler')

    # Patch time.sleep to break the loop after one iteration
    call_count = [0]
    def mock_sleep(seconds):
        call_count[0] += 1
        if call_count[0] > 1:
            raise StopIteration("break loop")
    mocker.patch('vlc.time.sleep', side_effect=mock_sleep)

    try:
        vm.monitor_karaoke()
    except StopIteration:
        pass

    assert vm.karaoke_active is False
    assert vm.current_playing_path is None
    vm.fade_in_filler.assert_called_once()


def test_monitor_karaoke_skips_during_seek_grace_period(mock_config, mocker):
    """monitor skips status check within 5s of a seek."""
    vm = VLCManager(mock_config, enabled=True)
    vm.karaoke_active = True
    vm.last_seek_time = time.time()  # just seeked

    send_mock = mocker.patch.object(vm, 'send_command')

    call_count = [0]
    def mock_sleep(seconds):
        call_count[0] += 1
        if call_count[0] > 1:
            raise StopIteration("break loop")
    mocker.patch('vlc.time.sleep', side_effect=mock_sleep)

    try:
        vm.monitor_karaoke()
    except StopIteration:
        pass

    # send_command should not have been called (skipped due to seek)
    send_mock.assert_not_called()


def test_monitor_karaoke_ignores_when_inactive(mock_config, mocker):
    """monitor does nothing when karaoke_active is False."""
    vm = VLCManager(mock_config, enabled=True)
    vm.karaoke_active = False

    send_mock = mocker.patch.object(vm, 'send_command')

    call_count = [0]
    def mock_sleep(seconds):
        call_count[0] += 1
        if call_count[0] > 1:
            raise StopIteration("break loop")
    mocker.patch('vlc.time.sleep', side_effect=mock_sleep)

    try:
        vm.monitor_karaoke()
    except StopIteration:
        pass

    send_mock.assert_not_called()


def test_monitor_karaoke_continues_when_playing(mock_config, mocker):
    """monitor doesn't reset state when VLC reports playing."""
    vm = VLCManager(mock_config, enabled=True)
    vm.karaoke_active = True
    vm.current_playing_path = "/some/video.mp4"

    mocker.patch.object(vm, 'send_command', return_value={"state": "playing"})
    fade_mock = mocker.patch.object(vm, 'fade_in_filler')

    call_count = [0]
    def mock_sleep(seconds):
        call_count[0] += 1
        if call_count[0] > 1:
            raise StopIteration("break loop")
    mocker.patch('vlc.time.sleep', side_effect=mock_sleep)

    try:
        vm.monitor_karaoke()
    except StopIteration:
        pass

    assert vm.karaoke_active is True
    assert vm.current_playing_path == "/some/video.mp4"
    fade_mock.assert_not_called()


def test_monitor_karaoke_handles_null_status(mock_config, mocker):
    """monitor continues when send_command returns None (VLC unreachable)."""
    vm = VLCManager(mock_config, enabled=True)
    vm.karaoke_active = True

    mocker.patch.object(vm, 'send_command', return_value=None)
    fade_mock = mocker.patch.object(vm, 'fade_in_filler')

    call_count = [0]
    def mock_sleep(seconds):
        call_count[0] += 1
        if call_count[0] > 1:
            raise StopIteration("break loop")
    mocker.patch('vlc.time.sleep', side_effect=mock_sleep)

    try:
        vm.monitor_karaoke()
    except StopIteration:
        pass

    assert vm.karaoke_active is True
    fade_mock.assert_not_called()


# --- fade_music volume interpolation ---

def test_fade_music_volume_sequence(mock_config, mocker):
    """fade_music sends correct volume steps from start to end."""
    vm = VLCManager(mock_config, enabled=True)
    volumes_sent = []

    def capture_send(port, pw, cmd, **kwargs):
        if "volume&val=" in cmd:
            vol = int(cmd.split("=")[1])
            volumes_sent.append(vol)

    mocker.patch.object(vm, 'send_command', side_effect=capture_send)
    mocker.patch('vlc.time.sleep')

    vm.fade_music(8081, "filler", 0, 100, duration_s=3)

    # 21 steps (0 through 20 inclusive)
    assert len(volumes_sent) == 21
    assert volumes_sent[0] == 0
    assert volumes_sent[-1] == 100
    # Monotonically increasing
    for i in range(1, len(volumes_sent)):
        assert volumes_sent[i] >= volumes_sent[i - 1]


def test_fade_music_fade_out(mock_config, mocker):
    """fade_music handles reverse direction (fade out)."""
    vm = VLCManager(mock_config, enabled=True)
    volumes_sent = []

    def capture_send(port, pw, cmd, **kwargs):
        if "volume&val=" in cmd:
            volumes_sent.append(int(cmd.split("=")[1]))

    mocker.patch.object(vm, 'send_command', side_effect=capture_send)
    mocker.patch('vlc.time.sleep')

    vm.fade_music(8081, "filler", 100, 0, duration_s=3)

    assert volumes_sent[0] == 100
    assert volumes_sent[-1] == 0
    # Monotonically decreasing
    for i in range(1, len(volumes_sent)):
        assert volumes_sent[i] <= volumes_sent[i - 1]


# --- restart_instances process management ---

def test_restart_instances_terminates_running_processes(mock_config, mocker):
    """restart_instances terminates existing processes before relaunching."""
    vm = VLCManager(mock_config, enabled=True)

    mock_proc = mocker.MagicMock()
    mock_proc.poll.return_value = None  # process is running
    vm.processes = {"karaoke": mock_proc, "filler": mock_proc}

    mocker.patch.object(vm, 'launch_instance')
    mocker.patch.object(vm, 'fade_in_filler')
    mocker.patch('vlc.time.sleep')

    vm.restart_instances()

    mock_proc.terminate.assert_called()
    assert vm.karaoke_active is False
    assert vm.current_playing_path is None
    vm.launch_instance.assert_called()


def test_restart_instances_kills_on_timeout(mock_config, mocker):
    """restart_instances force-kills processes that don't exit in time."""
    import subprocess
    vm = VLCManager(mock_config, enabled=True)

    mock_proc = mocker.MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="cvlc", timeout=5)
    vm.processes = {"karaoke": mock_proc, "filler": None}

    mocker.patch.object(vm, 'launch_instance')
    mocker.patch.object(vm, 'fade_in_filler')
    mocker.patch('vlc.time.sleep')

    vm.restart_instances()

    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()
