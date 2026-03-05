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
    assert vm.last_play_time == 0
    assert vm.audio_error is False
    assert vm.audio_device == "hdmiout"


def test_volumes_loaded_from_config(mock_config):
    """VLCManager reads persisted volume settings from config dict."""
    mock_config['karaoke_volume'] = 180
    mock_config['filler_volume'] = 75
    vm = VLCManager(mock_config, enabled=False)
    assert vm.karaoke_volume == 180
    assert vm.filler_volume == 75


def test_volumes_default_when_not_in_config(mock_config):
    """VLCManager uses defaults when volume keys are absent from config."""
    mock_config.pop('karaoke_volume', None)
    mock_config.pop('filler_volume', None)
    vm = VLCManager(mock_config, enabled=False)
    assert vm.karaoke_volume == 200
    assert vm.filler_volume == 100


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


def test_play_video_skips_filler_fade_when_already_stopped(mock_config, tmp_media_dir, mocker):
    """play_video skips the 3s fade when filler is already stopped (e.g. switching songs)."""
    vm = VLCManager(mock_config, enabled=True)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    # send_command returns "stopped" — filler is already off
    mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    fade_out_mock = mocker.patch.object(vm, 'fade_out_filler')
    ensure_mock = mocker.patch.object(vm, 'ensure_filler_stopped', return_value=True)
    mocker.patch('vlc.time.sleep')
    mocker.patch('vlc.threading.Thread')

    vm.play_video(str(test_file))

    fade_out_mock.assert_not_called()
    ensure_mock.assert_not_called()


def test_play_video_fades_filler_when_playing(mock_config, tmp_media_dir, mocker):
    """play_video does the full 3s fade when filler is currently playing."""
    vm = VLCManager(mock_config, enabled=True)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    call_count = [0]
    def mock_send(*args, **kwargs):
        call_count[0] += 1
        # First call is filler status check — report playing
        if call_count[0] == 1:
            return {"state": "playing"}
        return {"state": "stopped"}

    mocker.patch.object(vm, 'send_command', side_effect=mock_send)
    fade_out_mock = mocker.patch.object(vm, 'fade_out_filler')
    ensure_mock = mocker.patch.object(vm, 'ensure_filler_stopped', return_value=True)
    mocker.patch('vlc.time.sleep')
    mocker.patch('vlc.threading.Thread')

    vm.play_video(str(test_file))

    fade_out_mock.assert_called_once()
    ensure_mock.assert_called_once()


def test_play_video_sends_correct_command_sequence(mock_config, tmp_media_dir, mocker):
    """play_video sends filler check, pl_empty, enqueue, volume, pl_play in order."""
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
    assert commands[0] == ""  # filler status check
    assert commands[1] == "pl_empty"
    assert commands[2].startswith("in_enqueue&input=")
    assert str(test_file) in commands[2]
    assert commands[3] == f"volume&val={vm.karaoke_volume}"
    assert commands[4] == "pl_play"


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
        # First 5 calls are play_video commands (filler check + 4 karaoke),
        # 6th is verify_playback status check
        if call_count[0] <= 5:
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
    mocker.patch.object(vm, 'ensure_karaoke_released')
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
    vm.ensure_karaoke_released.assert_called_once()
    vm.fade_in_filler.assert_called_once()


def test_monitor_karaoke_calls_on_karaoke_end_callback(mock_config, mocker):
    """monitor invokes on_karaoke_end callback when song finishes."""
    vm = VLCManager(mock_config, enabled=True)
    vm.karaoke_active = True
    vm.current_playing_path = "/some/video.mp4"

    callback = mocker.MagicMock()
    vm.on_karaoke_end = callback

    mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    mocker.patch.object(vm, 'ensure_karaoke_released')
    mocker.patch.object(vm, 'fade_in_filler')

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

    callback.assert_called_once()


def test_monitor_karaoke_survives_callback_exception(mock_config, mocker):
    """monitor continues even if on_karaoke_end callback raises."""
    vm = VLCManager(mock_config, enabled=True)
    vm.karaoke_active = True
    vm.current_playing_path = "/some/video.mp4"

    vm.on_karaoke_end = mocker.MagicMock(side_effect=RuntimeError("callback boom"))

    mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    release_mock = mocker.patch.object(vm, 'ensure_karaoke_released')
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

    # Exception didn't prevent the rest of the flow
    release_mock.assert_called_once()
    fade_mock.assert_called_once()


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


# --- fade_in_filler / fade_out_filler with enabled=True ---

def test_fade_in_filler_sends_commands_when_enabled(mock_config, mocker):
    """fade_in_filler sets volume to 0, plays, and starts fade thread."""
    vm = VLCManager(mock_config, enabled=True)
    vm.filler_volume = 100
    send_mock = mocker.patch.object(vm, 'send_command')
    thread_mock = mocker.patch('vlc.threading.Thread')

    vm.fade_in_filler()

    # Should set volume to 0, then play
    calls = send_mock.call_args_list
    assert any("volume&val=0" in str(c) for c in calls)
    assert any("pl_play" in str(c) for c in calls)
    # Should start a fade thread
    thread_mock.assert_called_once()
    thread_mock.return_value.start.assert_called_once()


def test_fade_out_filler_fades_and_stops_when_enabled(mock_config, mocker):
    """fade_out_filler reads actual volume, calls fade_music, then sends pl_stop."""
    vm = VLCManager(mock_config, enabled=True)
    vm.filler_volume = 100
    # send_command returns volume status, then None for pl_stop
    send_mock = mocker.patch.object(vm, 'send_command',
        return_value={"state": "playing", "volume": 80})
    fade_mock = mocker.patch.object(vm, 'fade_music')

    vm.fade_out_filler()

    # Should call fade_music starting from actual volume (80), not filler_volume (100)
    fade_mock.assert_called_once()
    args = fade_mock.call_args[0]
    assert args[2] == 80  # start_vol = actual VLC volume
    assert args[3] == 0   # end_vol = 0
    # Should send pl_stop after fade
    assert any("pl_stop" in str(c) for c in send_mock.call_args_list)


# --- send_command debug mode ---

def test_send_command_debug_logging(mock_config, mocker):
    """send_command with debug=True logs URL and response."""
    vm = VLCManager(mock_config, enabled=True)
    mock_response = mocker.MagicMock()
    mock_response.json.return_value = {"state": "playing"}
    mock_response.status_code = 200
    mock_response.raise_for_status = mocker.MagicMock()

    mock_session = mocker.MagicMock()
    mock_session.get.return_value = mock_response
    mocker.patch('vlc.requests.Session', return_value=mock_session)
    log_mock = mocker.patch('vlc.log_message')

    result = vm.send_command(8080, "karaoke", "pl_play", debug=True)
    assert result == {"state": "playing"}
    # debug=True should have logged the URL and response
    assert log_mock.call_count >= 2
    log_messages = [str(c) for c in log_mock.call_args_list]
    assert any("DEBUG" in m for m in log_messages)


def test_send_command_debug_logs_on_connection_error(mock_config, mocker):
    """send_command with debug=True logs errors."""
    import requests as req
    vm = VLCManager(mock_config, enabled=True)

    mock_session = mocker.MagicMock()
    mock_session.get.side_effect = req.exceptions.ConnectionError("refused")
    mocker.patch('vlc.requests.Session', return_value=mock_session)
    log_mock = mocker.patch('vlc.log_message')

    result = vm.send_command(8080, "karaoke", "pl_play", debug=True)
    assert result is None
    # Should log the debug URL and the error
    assert any("DEBUG" in str(c) for c in log_mock.call_args_list)


# --- ensure_karaoke_released ---

def test_ensure_karaoke_released_succeeds_first_try(mock_config, mocker):
    """Returns True when karaoke VLC reports stopped immediately."""
    vm = VLCManager(mock_config, enabled=True)
    send_mock = mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    mocker.patch('vlc.time.sleep')

    assert vm.ensure_karaoke_released() is True
    commands = [call.args[2] for call in send_mock.call_args_list]
    assert "pl_stop" in commands
    assert "pl_empty" in commands
    assert "" in commands  # status check


def test_ensure_karaoke_released_succeeds_after_retries(mock_config, mocker):
    """Returns True when karaoke stops after a few retries."""
    vm = VLCManager(mock_config, enabled=True)
    responses = [
        None, None,                   # initial pl_stop + pl_empty
        {"state": "playing"},         # attempt 0: status check
        None,                         # attempt 0: retry pl_stop
        {"state": "playing"},         # attempt 1: status check
        None,                         # attempt 1: retry pl_stop
        {"state": "stopped"},         # attempt 2: success
    ]
    mocker.patch.object(vm, 'send_command', side_effect=responses)
    mocker.patch('vlc.time.sleep')

    assert vm.ensure_karaoke_released() is True


def test_ensure_karaoke_released_fails_after_max_attempts(mock_config, mocker):
    """Returns False after 5 failed attempts."""
    vm = VLCManager(mock_config, enabled=True)
    mocker.patch.object(vm, 'send_command', return_value={"state": "playing"})
    mocker.patch('vlc.time.sleep')

    assert vm.ensure_karaoke_released() is False


def test_ensure_karaoke_released_handles_none_status(mock_config, mocker):
    """Returns False when VLC is unreachable (send_command returns None)."""
    vm = VLCManager(mock_config, enabled=True)
    mocker.patch.object(vm, 'send_command', return_value=None)
    mocker.patch('vlc.time.sleep')

    assert vm.ensure_karaoke_released() is False


# --- monitor_karaoke: ensure_karaoke_released ordering ---

def test_monitor_karaoke_releases_karaoke_before_fading_in_filler(mock_config, mocker):
    """monitor calls ensure_karaoke_released before fade_in_filler when song ends."""
    vm = VLCManager(mock_config, enabled=True)
    vm.karaoke_active = True
    vm.current_playing_path = "/some/video.mp4"

    mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})

    call_order = []
    release_mock = mocker.patch.object(vm, 'ensure_karaoke_released',
                                       side_effect=lambda: call_order.append('release'))
    fade_mock = mocker.patch.object(vm, 'fade_in_filler',
                                    side_effect=lambda: call_order.append('fade_in'))

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

    assert call_order == ['release', 'fade_in']


# --- monitor_karaoke: play grace period ---

def test_monitor_karaoke_skips_during_play_grace_period(mock_config, mocker):
    """monitor skips status check within 5s of a play request."""
    vm = VLCManager(mock_config, enabled=True)
    vm.karaoke_active = True
    vm.last_play_time = time.time()  # just started playing

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


# --- play_video: last_play_time and lock ---

def test_play_video_sets_last_play_time(mock_config, tmp_media_dir, mocker):
    """play_video sets last_play_time to current time."""
    vm = VLCManager(mock_config, enabled=True)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    mocker.patch.object(vm, 'fade_out_filler')
    mocker.patch.object(vm, 'ensure_filler_stopped', return_value=True)
    mocker.patch('vlc.time.sleep')
    mocker.patch('vlc.threading.Thread')

    before = time.time()
    vm.play_video(str(test_file))
    assert vm.last_play_time >= before
    assert vm.last_play_time <= time.time()


def test_play_video_serializes_concurrent_calls(mock_config, tmp_media_dir, mocker):
    """play_video uses a lock so concurrent calls are serialized, not interleaved."""
    import threading
    RealThread = threading.Thread  # save before any mocking

    vm = VLCManager(mock_config, enabled=True)
    file_a = tmp_media_dir / "media" / "song_a.mp4"
    file_b = tmp_media_dir / "media" / "song_b.mp4"
    file_a.write_text("fake video a")
    file_b.write_text("fake video b")

    # Track the order of enqueue commands to verify serialization
    enqueue_order = []
    order_lock = threading.Lock()

    def tracking_send(port, pw, cmd, **kwargs):
        if "in_enqueue" in cmd:
            with order_lock:
                enqueue_order.append(cmd)
        return {"state": "stopped"}

    mocker.patch.object(vm, 'send_command', side_effect=tracking_send)
    mocker.patch.object(vm, 'fade_out_filler')
    mocker.patch.object(vm, 'ensure_filler_stopped', return_value=True)
    mocker.patch('vlc.time.sleep')

    # Mock Thread only for verify_playback inside play_video, not for our test threads
    mock_thread_cls = mocker.MagicMock()
    mocker.patch('vlc.threading.Thread', mock_thread_cls)

    # Launch two play_video calls concurrently using real threads
    t1 = RealThread(target=vm.play_video, args=(str(file_a),))
    t2 = RealThread(target=vm.play_video, args=(str(file_b),))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    # Both should have enqueued (lock serializes, doesn't block)
    assert len(enqueue_order) == 2
    # The two enqueues should not be interleaved — each play_video runs atomically
    assert "song_a.mp4" in enqueue_order[0] or "song_b.mp4" in enqueue_order[0]
    assert "song_a.mp4" in enqueue_order[1] or "song_b.mp4" in enqueue_order[1]


def test_rapid_play_requests_last_wins(mock_config, tmp_media_dir, mocker):
    """When multiple play requests arrive rapidly, the last one ends up playing."""
    import threading
    RealThread = threading.Thread

    vm = VLCManager(mock_config, enabled=True)
    files = []
    for i in range(3):
        f = tmp_media_dir / "media" / f"song_{i}.mp4"
        f.write_text(f"fake video {i}")
        files.append(str(f))

    # Track all pl_play commands and what was last enqueued
    last_enqueued = [None]

    def tracking_send(port, pw, cmd, **kwargs):
        if "in_enqueue" in cmd:
            last_enqueued[0] = cmd
        return {"state": "stopped"}

    mocker.patch.object(vm, 'send_command', side_effect=tracking_send)
    mocker.patch.object(vm, 'fade_out_filler')
    mocker.patch.object(vm, 'ensure_filler_stopped', return_value=True)
    mocker.patch('vlc.time.sleep')
    mocker.patch('vlc.threading.Thread', mocker.MagicMock())

    # Fire off 3 rapid play requests using real threads
    threads = [RealThread(target=vm.play_video, args=(f,)) for f in files]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    # All 3 should have completed (lock serializes them)
    assert last_enqueued[0] is not None
    assert vm.karaoke_active is True
    # last_play_time should be recent
    assert time.time() - vm.last_play_time < 2


def test_play_grace_period_prevents_false_song_end_during_transition(mock_config, mocker):
    """Simulates: song A playing, user clicks song B, monitor shouldn't trigger filler."""
    import threading

    vm = VLCManager(mock_config, enabled=True)
    vm.karaoke_active = True
    vm.current_playing_path = "/some/song_a.mp4"
    # Simulate play_video just started (sets last_play_time)
    vm.last_play_time = time.time()

    # Monitor would see "stopped" if it checked, but grace period should prevent it
    mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    release_mock = mocker.patch.object(vm, 'ensure_karaoke_released')
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

    # Monitor should have skipped due to play grace period
    release_mock.assert_not_called()
    fade_mock.assert_not_called()
    assert vm.karaoke_active is True  # not reset


# --- Bug 8: fade cancel prevents concurrent fade-in/fade-out ---

def test_fade_cancel_event_in_init(mock_config):
    """VLCManager initializes with a _fade_cancel Event."""
    vm = VLCManager(mock_config, enabled=True)
    import threading
    assert isinstance(vm._fade_cancel, threading.Event)
    assert not vm._fade_cancel.is_set()


def test_fade_music_stops_when_cancel_set(mock_config, mocker):
    """fade_music exits early when cancel_event is set."""
    vm = VLCManager(mock_config, enabled=True)
    import threading
    cancel = threading.Event()
    cancel.set()  # pre-cancelled

    send_mock = mocker.patch.object(vm, 'send_command')
    mocker.patch('vlc.time.sleep')

    vm.fade_music(8081, "filler", 0, 100, cancel_event=cancel)

    # Should not have sent any volume commands (cancelled before first step)
    send_mock.assert_not_called()


def test_fade_in_cancels_previous_fade(mock_config, mocker):
    """fade_in_filler sets the old _fade_cancel and creates a new one."""
    vm = VLCManager(mock_config, enabled=True)
    old_cancel = vm._fade_cancel

    mocker.patch.object(vm, 'send_command')
    mocker.patch('vlc.threading.Thread')

    vm.fade_in_filler()

    # Old cancel should be set (stopping any in-progress fade)
    assert old_cancel.is_set()
    # New cancel should be a fresh, unset Event
    assert vm._fade_cancel is not old_cancel
    assert not vm._fade_cancel.is_set()


def test_fade_out_cancels_previous_fade(mock_config, mocker):
    """fade_out_filler sets the old _fade_cancel and creates a new one."""
    vm = VLCManager(mock_config, enabled=True)
    old_cancel = vm._fade_cancel

    mocker.patch.object(vm, 'send_command', return_value={"volume": 100})
    mocker.patch.object(vm, 'fade_music')

    vm.fade_out_filler()

    assert old_cancel.is_set()
    assert vm._fade_cancel is not old_cancel


def test_fade_out_reads_actual_volume(mock_config, mocker):
    """fade_out_filler starts from VLC's actual volume, not filler_volume."""
    vm = VLCManager(mock_config, enabled=True)
    vm.filler_volume = 100

    mocker.patch.object(vm, 'send_command', return_value={"state": "playing", "volume": 60})
    fade_mock = mocker.patch.object(vm, 'fade_music')

    vm.fade_out_filler()

    args = fade_mock.call_args[0]
    assert args[2] == 60  # start from actual volume, not 100


def test_fade_out_falls_back_to_filler_volume_on_none(mock_config, mocker):
    """fade_out_filler uses filler_volume when send_command returns None."""
    vm = VLCManager(mock_config, enabled=True)
    vm.filler_volume = 100

    mocker.patch.object(vm, 'send_command', return_value=None)
    fade_mock = mocker.patch.object(vm, 'fade_music')

    vm.fade_out_filler()

    args = fade_mock.call_args[0]
    assert args[2] == 100  # falls back to filler_volume


def test_fade_in_passes_cancel_event_to_thread(mock_config, mocker):
    """fade_in_filler passes cancel_event kwarg to the fade thread."""
    vm = VLCManager(mock_config, enabled=True)
    mocker.patch.object(vm, 'send_command')
    thread_mock = mocker.patch('vlc.threading.Thread')

    vm.fade_in_filler()

    kwargs = thread_mock.call_args[1]
    assert 'cancel_event' in kwargs.get('kwargs', {})
    assert not kwargs['kwargs']['cancel_event'].is_set()


# --- Bug 7: play_video display_path and overlay_manager ---

def test_play_video_sets_display_path_inside_lock(mock_config, tmp_media_dir, mocker):
    """play_video sets current_playing_path from display_path arg."""
    vm = VLCManager(mock_config, enabled=True)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    mocker.patch('vlc.time.sleep')
    mocker.patch('vlc.threading.Thread')

    vm.play_video(str(test_file), display_path="/original/path.zip")
    assert vm.current_playing_path == "/original/path.zip"


def test_play_video_calls_overlay_manager(mock_config, tmp_media_dir, mocker):
    """play_video calls overlay_manager.set_karaoke_playing(True) inside the lock."""
    vm = VLCManager(mock_config, enabled=True)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    mocker.patch('vlc.time.sleep')
    mocker.patch('vlc.threading.Thread')

    mock_overlay = mocker.MagicMock()
    vm.play_video(str(test_file), overlay_manager=mock_overlay)
    mock_overlay.set_karaoke_playing.assert_called_once_with(True)


def test_play_video_without_display_path_leaves_current_playing(mock_config, tmp_media_dir, mocker):
    """play_video without display_path does not change current_playing_path."""
    vm = VLCManager(mock_config, enabled=True)
    vm.current_playing_path = "/old/path.mp4"
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    mocker.patch('vlc.time.sleep')
    mocker.patch('vlc.threading.Thread')

    vm.play_video(str(test_file))
    assert vm.current_playing_path == "/old/path.mp4"
