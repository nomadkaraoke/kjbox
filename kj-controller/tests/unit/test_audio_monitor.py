"""Tests for AudioMonitor: PipeWire switching, pw-cat+ffmpeg capture, stream serving."""

import subprocess
import threading

import pytest

from audio_monitor import (
    AudioMonitor,
    PW_ANALOG_PROFILE,
    PW_CARD,
    PW_HDMI_PROFILE,
    PW_MONITOR_SOURCE_PREFIX,
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


MOCK_MONITOR_SOURCE = PW_MONITOR_SOURCE_PREFIX + '.3.monitor'


def _mock_popen(mocker):
    """Mock subprocess.Popen for the shell pipeline (single call)."""
    mock_proc = mocker.Mock()
    mock_proc.stdout = mocker.Mock()
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen', return_value=mock_proc)
    return mock_popen, mock_proc


@pytest.fixture
def monitor(mock_mpv, mocker):
    """AudioMonitor with subprocess.run mocked (pactl calls) and source discovery mocked."""
    mocker.patch('audio_monitor.subprocess.run')
    mocker.patch.object(AudioMonitor, '_find_monitor_source', return_value=MOCK_MONITOR_SOURCE)
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
    _mock_popen(mocker)
    monitor.start()

    import audio_monitor
    audio_monitor.subprocess.run.assert_called_once_with(
        PACTL_ENV_PREFIX + ['pactl', 'set-card-profile', PW_CARD, PW_HDMI_PROFILE],
        check=False,
    )


def test_start_sets_audio_backend_to_pipewire(monitor, mocker):
    _mock_popen(mocker)
    monitor.start()
    assert monitor.mpv.audio_backend == 'pipewire'


def test_start_calls_restart_instances(monitor, mocker):
    _mock_popen(mocker)
    monitor.start()
    monitor.mpv.restart_instances.assert_called_once()


def test_start_launches_shell_pipeline(monitor, mocker):
    mock_popen, mock_proc = _mock_popen(mocker)
    monitor.start()

    mock_popen.assert_called_once()
    call_kwargs = mock_popen.call_args[1]
    assert call_kwargs['shell'] is True
    assert call_kwargs['stdout'] == subprocess.PIPE
    shell_cmd = mock_popen.call_args[0][0]
    assert 'parec' in shell_cmd
    assert f'--device={MOCK_MONITOR_SOURCE}' in shell_cmd
    assert 'ffmpeg' in shell_cmd
    assert 'libmp3lame' in shell_cmd


def test_start_sets_active_flag(monitor, mocker):
    _mock_popen(mocker)
    monitor.start()
    assert monitor.active is True


def test_start_starts_drain_thread(monitor, mocker):
    _mock_popen(mocker)
    monitor.start()
    assert monitor._drain_thread is not None
    assert monitor._drain_thread.daemon is True


def test_start_idempotent(monitor, mocker):
    """Calling start() when already active should be a no-op."""
    mock_popen, _ = _mock_popen(mocker)
    monitor.start()
    monitor.mpv.restart_instances.reset_mock()
    mock_popen.reset_mock()

    monitor.start()

    monitor.mpv.restart_instances.assert_not_called()
    mock_popen.assert_not_called()


# --- stop() ---

def test_stop_kills_process(monitor, mocker):
    _, mock_proc = _mock_popen(mocker)
    monitor.start()
    monitor.stop()

    mock_proc.terminate.assert_called_once()


def test_stop_sets_active_false(monitor, mocker):
    _mock_popen(mocker)
    monitor.start()
    assert monitor.active is True
    monitor.stop()
    assert monitor.active is False


def test_stop_restores_alsa_backend(monitor, mocker):
    _mock_popen(mocker)
    monitor.start()
    assert monitor.mpv.audio_backend == 'pipewire'
    monitor.stop()
    assert monitor.mpv.audio_backend == 'alsa'


def test_stop_calls_restart_instances(monitor, mocker):
    _mock_popen(mocker)
    monitor.start()
    monitor.mpv.restart_instances.reset_mock()
    monitor.stop()
    monitor.mpv.restart_instances.assert_called_once()


def test_stop_switches_pipewire_to_analog(monitor, mocker):
    _mock_popen(mocker)
    monitor.start()
    import audio_monitor
    audio_monitor.subprocess.run.reset_mock()
    monitor.stop()
    audio_monitor.subprocess.run.assert_called_once_with(
        PACTL_ENV_PREFIX + ['pactl', 'set-card-profile', PW_CARD, PW_ANALOG_PROFILE],
        check=False,
    )


def test_stop_when_not_active_is_noop(monitor, mocker):
    mock_popen = mocker.patch('audio_monitor.subprocess.Popen')
    monitor.stop()
    mock_popen.assert_not_called()
    monitor.mpv.restart_instances.assert_not_called()
    assert monitor.active is False


def test_stop_clears_proc(monitor, mocker):
    _mock_popen(mocker)
    monitor.start()
    assert monitor._ffmpeg_proc is not None
    monitor.stop()
    assert monitor._ffmpeg_proc is None


# --- status() ---

def test_status_when_inactive(monitor):
    assert monitor.status() == {'active': False}


def test_status_when_active(monitor, mocker):
    _mock_popen(mocker)
    monitor.start()
    status = monitor.status()
    assert status == {'active': True, 'stream_url': '/audio-monitor/stream'}


# --- stream_generator() ---

def test_stream_generator_yields_chunks(monitor, mocker):
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
    mock_stdout = mocker.Mock()
    mock_stdout.read.side_effect = [b'\x00' * 100, b'']

    mock_proc = mocker.Mock()
    mock_proc.stdout = mock_stdout
    monitor._ffmpeg_proc = mock_proc
    monitor.active = True
    assert monitor._client_connected is False

    list(monitor.stream_generator())
    assert monitor._client_connected is False


def test_stream_generator_no_proc_returns_empty(monitor):
    chunks = list(monitor.stream_generator())
    assert chunks == []


def test_stream_generator_no_stdout_returns_empty(monitor, mocker):
    _mock_popen(mocker)
    # Override ffmpeg stdout to None after start
    monitor.start()
    monitor._ffmpeg_proc.stdout = None
    chunks = list(monitor.stream_generator())
    assert chunks == []


# --- _drain_loop() ---

def test_drain_loop_exits_on_no_proc(monitor):
    monitor._ffmpeg_proc = None
    monitor._drain_loop()


def test_drain_loop_exits_on_eof(monitor, mocker):
    mock_proc = mocker.Mock()
    mock_stdout = mocker.Mock()
    mock_stdout.read.return_value = b''
    mock_proc.stdout = mock_stdout
    monitor._ffmpeg_proc = mock_proc
    monitor._client_connected = False
    monitor._drain_loop()
    mock_stdout.read.assert_called_once_with(STREAM_CHUNK_SIZE)


def test_drain_loop_sleeps_when_client_connected(monitor, mocker):
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
            monitor._client_connected = False
            mock_stdout.read.return_value = b''

    sleep_mock.side_effect = limited_sleep
    monitor._drain_loop()

    assert sleep_mock.call_count >= 1
    sleep_mock.assert_any_call(0.1)


def test_drain_loop_drains_when_no_client(monitor, mocker):
    mock_proc = mocker.Mock()
    mock_stdout = mocker.Mock()
    mock_stdout.read.side_effect = [b'\x00' * 4096, b'']
    mock_proc.stdout = mock_stdout
    monitor._ffmpeg_proc = mock_proc
    monitor._client_connected = False
    monitor._drain_loop()
    assert mock_stdout.read.call_count == 2


# --- Route tests ---

@pytest.fixture
def app_with_monitor(mock_config):
    from app import create_app
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    yield app
    app.catalog.close()


@pytest.fixture
def client(app_with_monitor):
    with app_with_monitor.test_client() as c:
        yield c


def test_status_route_inactive(client):
    resp = client.get('/audio-monitor/status')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['active'] is False


def test_start_route(client, mocker):
    mocker.patch('audio_monitor.subprocess.run')
    mocker.patch('audio_monitor.subprocess.Popen')
    resp = client.post('/audio-monitor/start', json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert 'stream_url' in data


def test_stop_route(client, mocker):
    mocker.patch('audio_monitor.subprocess.run')
    mocker.patch('audio_monitor.subprocess.Popen')
    client.post('/audio-monitor/start', json={})
    resp = client.post('/audio-monitor/stop', json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True


def test_stream_route_when_inactive(client):
    resp = client.get('/audio-monitor/stream')
    assert resp.status_code == 404


def test_av_status_includes_audio_monitor(client):
    resp = client.get('/av/status')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'audio_monitor' in data
    assert data['audio_monitor']['active'] is False


def test_av_reset_stops_active_monitor(client, mocker):
    mocker.patch('audio_monitor.subprocess.run')
    mocker.patch('audio_monitor.subprocess.Popen')
    mock_subprocess_run = mocker.patch('routes.subprocess.run')
    mock_subprocess_run.return_value = mocker.Mock(returncode=0, stdout='OK', stderr='')

    client.post('/audio-monitor/start', json={})
    import time
    time.sleep(0.2)

    resp = client.post('/av/reset', json={})
    assert resp.status_code == 200
    time.sleep(0.2)
    assert client.application.audio_monitor.active is False
