"""Tests for AudioMonitor: PipeWire switching, ffmpeg capture, stream serving."""

import subprocess
import threading

import pytest

from audio_monitor import (
    AudioMonitor,
    PW_ANALOG_PROFILE,
    PW_CARD,
    PW_HDMI_PROFILE,
    PW_MONITOR_SOURCE,
    PACTL_ENV_PREFIX,
    STREAM_CHUNK_SIZE,
)
from mpv_manager import MpvManager


@pytest.fixture
def mock_mpv(mock_config, mocker):
    """MpvManager with restart_instances mocked out."""
    m = MpvManager(mock_config, enabled=False)
    m.restart_instances = mocker.Mock()
    return m


@pytest.fixture
def monitor(mock_mpv, mocker):
    """AudioMonitor with subprocess.run mocked (pactl calls)."""
    mocker.patch('audio_monitor.subprocess.run')
    return AudioMonitor(mock_mpv)


# --- Initial state ---

def test_initial_state(monitor):
    assert monitor.active is False
    assert monitor._ffmpeg_proc is None
    assert monitor._drain_thread is None
    assert monitor._client_connected is False


def test_constructor_default_config(mock_mpv, mocker):
    mocker.patch('audio_monitor.subprocess.run')
    m = AudioMonitor(mock_mpv)
    assert m.config == {}


def test_constructor_custom_config(mock_mpv, mocker):
    mocker.patch('audio_monitor.subprocess.run')
    cfg = {'log_file': '/tmp/test.log'}
    m = AudioMonitor(mock_mpv, config=cfg)
    assert m.config == cfg


# --- start() ---

def test_start_switches_pipewire_to_hdmi(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = None
    mock_popen.return_value.poll.return_value = None

    monitor.start()

    # Check pactl was called with HDMI profile
    import audio_monitor
    audio_monitor.subprocess.run.assert_called_once_with(
        PACTL_ENV_PREFIX + ['pactl', 'set-card-profile', PW_CARD, PW_HDMI_PROFILE],
        check=False,
    )


def test_start_sets_audio_backend_to_pipewire(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = None
    mock_popen.return_value.poll.return_value = None

    monitor.start()

    assert monitor.mpv.audio_backend == 'pipewire'


def test_start_calls_restart_instances(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = None
    mock_popen.return_value.poll.return_value = None

    monitor.start()

    monitor.mpv.restart_instances.assert_called_once()


def test_start_launches_ffmpeg(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = None
    mock_popen.return_value.poll.return_value = None

    monitor.start()

    mock_popen.assert_called_once()
    call_args = mock_popen.call_args
    cmd = call_args[0][0]
    # Verify ffmpeg command structure
    assert 'ffmpeg' in cmd
    assert '-f' in cmd
    assert 'pulse' in cmd
    assert '-i' in cmd
    assert PW_MONITOR_SOURCE in cmd
    assert 'libmp3lame' in cmd
    assert 'pipe:1' in cmd
    # Verify stdout=PIPE, stderr=DEVNULL
    assert call_args[1]['stdout'] == subprocess.PIPE
    assert call_args[1]['stderr'] == subprocess.DEVNULL


def test_start_sets_active_flag(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = None
    mock_popen.return_value.poll.return_value = None

    monitor.start()

    assert monitor.active is True


def test_start_starts_drain_thread(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = None
    mock_popen.return_value.poll.return_value = None

    monitor.start()

    assert monitor._drain_thread is not None
    assert monitor._drain_thread.daemon is True


def test_start_idempotent(monitor, mocker):
    """Calling start() when already active should be a no-op."""
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = None
    mock_popen.return_value.poll.return_value = None

    monitor.start()
    monitor.mpv.restart_instances.reset_mock()
    mock_popen.reset_mock()

    monitor.start()

    # Nothing should be called again
    monitor.mpv.restart_instances.assert_not_called()
    mock_popen.assert_not_called()


# --- stop() ---

def test_stop_kills_ffmpeg(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_proc = mocker.Mock()
    mock_popen.return_value = mock_proc
    mock_proc.stdout = None

    monitor.start()
    monitor.stop()

    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called()


def test_stop_kills_ffmpeg_force_on_timeout(monitor, mocker):
    """If ffmpeg doesn't terminate gracefully, kill it forcefully."""
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_proc = mocker.Mock()
    mock_proc.stdout = None
    # First wait() raises timeout, second succeeds
    mock_proc.wait.side_effect = [subprocess.TimeoutExpired('ffmpeg', 5), None]
    mock_popen.return_value = mock_proc

    monitor.start()
    monitor.stop()

    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()


def test_stop_sets_active_false(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = None
    mock_popen.return_value.poll.return_value = None

    monitor.start()
    assert monitor.active is True

    monitor.stop()
    assert monitor.active is False


def test_stop_restores_alsa_backend(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = None
    mock_popen.return_value.poll.return_value = None

    monitor.start()
    assert monitor.mpv.audio_backend == 'pipewire'

    monitor.stop()
    assert monitor.mpv.audio_backend == 'alsa'


def test_stop_calls_restart_instances(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = None
    mock_popen.return_value.poll.return_value = None

    monitor.start()
    monitor.mpv.restart_instances.reset_mock()

    monitor.stop()
    monitor.mpv.restart_instances.assert_called_once()


def test_stop_switches_pipewire_to_analog(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = None
    mock_popen.return_value.poll.return_value = None

    monitor.start()
    import audio_monitor
    audio_monitor.subprocess.run.reset_mock()

    monitor.stop()

    audio_monitor.subprocess.run.assert_called_once_with(
        PACTL_ENV_PREFIX + ['pactl', 'set-card-profile', PW_CARD, PW_ANALOG_PROFILE],
        check=False,
    )


def test_stop_when_not_active_is_noop(monitor, mocker):
    """Calling stop() when not active should do nothing."""
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')

    monitor.stop()

    mock_popen.assert_not_called()
    monitor.mpv.restart_instances.assert_not_called()
    assert monitor.active is False


def test_stop_clears_ffmpeg_proc(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = None

    monitor.start()
    assert monitor._ffmpeg_proc is not None

    monitor.stop()
    assert monitor._ffmpeg_proc is None


# --- status() ---

def test_status_when_inactive(monitor):
    assert monitor.status() == {'active': False}


def test_status_when_active(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = None
    mock_popen.return_value.poll.return_value = None

    monitor.start()

    status = monitor.status()
    assert status == {'active': True, 'stream_url': '/audio-monitor/stream'}


# --- stream_generator() ---

def test_stream_generator_yields_chunks(monitor, mocker):
    """stream_generator should yield STREAM_CHUNK_SIZE byte chunks from ffmpeg stdout."""
    # Set up the monitor in active state directly, bypassing start()
    # to avoid the drain thread consuming mock reads
    mock_stdout = mocker.Mock()
    chunk1 = b'\xff' * STREAM_CHUNK_SIZE
    chunk2 = b'\xaa' * 100
    mock_stdout.read.side_effect = [chunk1, chunk2, b'']

    mock_proc = mocker.Mock()
    mock_proc.stdout = mock_stdout
    monitor._ffmpeg_proc = mock_proc
    monitor.active = True

    chunks = list(monitor.stream_generator())
    assert len(chunks) == 2
    assert chunks[0] == chunk1
    assert chunks[1] == chunk2


def test_stream_generator_sets_client_connected(monitor, mocker):
    """stream_generator should set _client_connected while active, clear on exit."""
    mock_stdout = mocker.Mock()
    mock_stdout.read.side_effect = [b'\x00' * 100, b'']

    mock_proc = mocker.Mock()
    mock_proc.stdout = mock_stdout
    monitor._ffmpeg_proc = mock_proc
    monitor.active = True
    assert monitor._client_connected is False

    # Consume the generator
    list(monitor.stream_generator())

    # After generator exhausted, client_connected should be False
    assert monitor._client_connected is False


def test_stream_generator_no_proc_returns_empty(monitor):
    """stream_generator should return immediately if no ffmpeg process."""
    chunks = list(monitor.stream_generator())
    assert chunks == []


def test_stream_generator_no_stdout_returns_empty(monitor, mocker):
    """stream_generator should return immediately if ffmpeg has no stdout."""
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    mock_popen.return_value.stdout = None
    mock_popen.return_value.poll.return_value = None

    monitor.start()

    chunks = list(monitor.stream_generator())
    assert chunks == []


# --- _drain_loop() ---

def test_drain_loop_exits_on_no_proc(monitor):
    """drain loop should exit when _ffmpeg_proc is None."""
    monitor._ffmpeg_proc = None
    # Should return immediately (not hang)
    monitor._drain_loop()


def test_drain_loop_exits_on_eof(monitor, mocker):
    """drain loop should exit when stdout returns empty bytes (EOF)."""
    mock_proc = mocker.Mock()
    mock_stdout = mocker.Mock()
    mock_stdout.read.return_value = b''  # EOF
    mock_proc.stdout = mock_stdout
    monitor._ffmpeg_proc = mock_proc
    monitor._client_connected = False

    monitor._drain_loop()

    mock_stdout.read.assert_called_once_with(STREAM_CHUNK_SIZE)


def test_drain_loop_sleeps_when_client_connected(monitor, mocker):
    """drain loop should sleep (not read) when a client is connected."""
    mock_proc = mocker.Mock()
    mock_stdout = mocker.Mock()
    mock_proc.stdout = mock_stdout
    monitor._ffmpeg_proc = mock_proc
    monitor._client_connected = True

    sleep_mock = mocker.patch('audio_monitor.time.sleep')
    call_count = [0]

    def limited_sleep(t):
        call_count[0] += 1
        if call_count[0] >= 2:
            # Disconnect client to let drain read EOF and exit
            monitor._client_connected = False
            mock_stdout.read.return_value = b''

    sleep_mock.side_effect = limited_sleep

    monitor._drain_loop()

    # Should have slept at least once
    assert sleep_mock.call_count >= 1
    sleep_mock.assert_any_call(0.1)


def test_drain_loop_drains_when_no_client(monitor, mocker):
    """drain loop should read and discard data when no client is connected."""
    mock_proc = mocker.Mock()
    mock_stdout = mocker.Mock()
    # Return data once, then EOF
    mock_stdout.read.side_effect = [b'\x00' * 4096, b'']
    mock_proc.stdout = mock_stdout
    monitor._ffmpeg_proc = mock_proc
    monitor._client_connected = False

    monitor._drain_loop()

    assert mock_stdout.read.call_count == 2
