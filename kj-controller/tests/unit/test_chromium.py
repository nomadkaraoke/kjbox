"""Tests for ChromiumManager: process lifecycle, binary detection, PipeWire audio routing."""

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from chromium import CHROMIUM_DATA_DIR, ChromiumManager


@pytest.fixture
def mgr(mock_config):
    """ChromiumManager with test config."""
    return ChromiumManager(mock_config)


# --- Binary detection ---

def test_find_binary_returns_first_found(mgr, mocker):
    """Returns the first available Chromium binary."""
    mocker.patch('chromium.shutil.which', side_effect=lambda name: '/usr/bin/chromium' if name == 'chromium' else None)
    assert mgr._find_binary() == '/usr/bin/chromium'


def test_find_binary_returns_none_when_missing(mgr, mocker):
    """Returns None when no Chromium binary is found."""
    mocker.patch('chromium.shutil.which', return_value=None)
    assert mgr._find_binary() is None


# --- Initial state ---

def test_initial_state(mgr):
    """Manager starts with no process and no URL."""
    assert mgr.process is None
    assert mgr.current_url is None
    assert mgr.is_running() is False


def test_get_status_when_stopped(mgr):
    """get_status returns stopped state when no process."""
    status = mgr.get_status()
    assert status == {'running': False, 'pid': None, 'url': None}


# --- Launch ---

def test_launch_success(mgr, mocker):
    """launch() starts Chromium with kiosk flags and returns True."""
    mocker.patch('chromium.shutil.which', return_value='/usr/bin/chromium')
    mocker.patch('chromium.is_pi', return_value=False)
    mock_popen = mocker.patch('chromium.subprocess.Popen')
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.poll.return_value = None
    mock_popen.return_value = mock_proc

    # Mock pactl and pkill (called during kill() cleanup)
    mocker.patch.object(mgr, '_pactl', return_value=(True, ''))
    mocker.patch('chromium.subprocess.run')

    result = mgr.launch('https://youtube.com')

    assert result is True
    assert mgr.current_url == 'https://youtube.com'
    assert mgr.is_running() is True

    # Verify kiosk mode and user data dir in command
    popen_args = mock_popen.call_args
    cmd = popen_args[0][0]
    assert '--kiosk' in cmd
    assert f'--user-data-dir={CHROMIUM_DATA_DIR}' in cmd
    assert 'https://youtube.com' in cmd


def test_launch_default_url(mgr, mocker):
    """launch() defaults to youtube.com when no URL provided."""
    mocker.patch('chromium.shutil.which', return_value='/usr/bin/chromium')
    mocker.patch('chromium.is_pi', return_value=False)
    mock_popen = mocker.patch('chromium.subprocess.Popen')
    mock_proc = MagicMock()
    mock_proc.pid = 1
    mock_proc.poll.return_value = None
    mock_popen.return_value = mock_proc
    mocker.patch.object(mgr, '_pactl', return_value=(True, ''))
    mocker.patch('chromium.subprocess.run')

    mgr.launch('')

    cmd = mock_popen.call_args[0][0]
    assert 'https://youtube.com' in cmd


def test_launch_fails_no_binary(mgr, mocker):
    """launch() returns False when no Chromium binary is found."""
    mocker.patch('chromium.shutil.which', return_value=None)

    result = mgr.launch('https://youtube.com')

    assert result is False
    assert mgr.process is None


def test_launch_sets_display_env(mgr, mocker):
    """launch() sets DISPLAY=:0 in the subprocess environment."""
    mocker.patch('chromium.shutil.which', return_value='/usr/bin/chromium')
    mocker.patch('chromium.is_pi', return_value=False)
    mock_popen = mocker.patch('chromium.subprocess.Popen')
    mock_proc = MagicMock()
    mock_proc.pid = 1
    mock_proc.poll.return_value = None
    mock_popen.return_value = mock_proc
    mocker.patch.object(mgr, '_pactl', return_value=(True, ''))
    mocker.patch('chromium.subprocess.run')

    mgr.launch('https://example.com')

    env = mock_popen.call_args[1]['env']
    assert env['DISPLAY'] == ':0'


# --- Kill ---

def test_kill_terminates_running_process(mgr, mocker):
    """kill() sends SIGTERM to a running process."""
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None  # Process is running
    mock_proc.pid = 999
    mock_proc.wait.return_value = 0
    mgr.process = mock_proc
    mgr.current_url = 'https://youtube.com'

    mocker.patch('chromium.subprocess.run')  # pkill
    mocker.patch.object(mgr, '_reset_pipewire')

    mgr.kill()

    mock_proc.terminate.assert_called_once()
    assert mgr.process is None
    assert mgr.current_url is None


def test_kill_force_kills_on_timeout(mgr, mocker):
    """kill() sends SIGKILL if SIGTERM times out."""
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.pid = 999
    mock_proc.wait.side_effect = [subprocess.TimeoutExpired('chromium', 3), 0]
    mgr.process = mock_proc
    mgr.current_url = 'https://youtube.com'

    mocker.patch('chromium.subprocess.run')
    mocker.patch.object(mgr, '_reset_pipewire')

    mgr.kill()

    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()


def test_kill_noop_when_not_running(mgr, mocker):
    """kill() is safe to call when no process is running."""
    mocker.patch('chromium.subprocess.run')
    mgr.kill()  # Should not raise
    assert mgr.process is None


def test_kill_resets_pipewire(mgr, mocker):
    """kill() resets PipeWire to analog when a process was running."""
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.pid = 999
    mock_proc.wait.return_value = 0
    mgr.process = mock_proc

    mocker.patch('chromium.subprocess.run')
    mock_reset = mocker.patch.object(mgr, '_reset_pipewire')

    mgr.kill()

    mock_reset.assert_called_once()


# --- PipeWire audio routing ---

def test_set_pipewire_profile_hdmi(mgr, mocker):
    """Sets HDMI PipeWire profile for hdmiout audio device."""
    mocker.patch('chromium.is_pi', return_value=False)
    mock_pactl = mocker.patch.object(mgr, '_pactl', return_value=(True, ''))

    mgr._set_pipewire_profile('hdmiout')

    mock_pactl.assert_called_once()
    args = mock_pactl.call_args[0]
    assert 'set-card-profile' in args
    assert 'hdmi' in args[2].lower()


def test_set_pipewire_profile_analog_fallback(mgr, mocker):
    """Falls back to analog profile for unknown audio device names."""
    mocker.patch('chromium.is_pi', return_value=False)
    mock_pactl = mocker.patch.object(mgr, '_pactl', return_value=(True, ''))

    mgr._set_pipewire_profile('usbmixer')

    args = mock_pactl.call_args[0]
    assert 'analog' in args[2].lower()


def test_set_pipewire_skipped_on_pi(mgr, mocker):
    """PipeWire operations are skipped on Pi (no PipeWire)."""
    mocker.patch('chromium.is_pi', return_value=True)
    mock_pactl = mocker.patch.object(mgr, '_pactl')

    mgr._set_pipewire_profile('hdmiout')

    mock_pactl.assert_not_called()


# --- get_status ---

def test_get_status_when_running(mgr):
    """get_status returns running state with PID and URL."""
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.pid = 42
    mgr.process = mock_proc
    mgr.current_url = 'https://youtube.com'

    status = mgr.get_status()

    assert status == {'running': True, 'pid': 42, 'url': 'https://youtube.com'}


# --- Launch with audio device ---

def test_launch_sets_pipewire_for_audio_device(mgr, mocker):
    """launch() calls _set_pipewire_profile with the provided audio device."""
    mocker.patch('chromium.shutil.which', return_value='/usr/bin/chromium')
    mocker.patch('chromium.is_pi', return_value=False)
    mock_popen = mocker.patch('chromium.subprocess.Popen')
    mock_proc = MagicMock()
    mock_proc.pid = 1
    mock_proc.poll.return_value = None
    mock_popen.return_value = mock_proc
    mock_set = mocker.patch.object(mgr, '_set_pipewire_profile')
    mocker.patch('chromium.subprocess.run')

    mgr.launch('https://youtube.com', audio_device='hdmiout')

    mock_set.assert_called_once_with('hdmiout')


# --- _pactl subprocess integration ---

def test_pactl_runs_as_nomad_user(mgr, mocker):
    """_pactl runs pactl via sudo -u nomad with XDG_RUNTIME_DIR."""
    mock_run = mocker.patch('chromium.subprocess.run')
    mock_run.return_value = MagicMock(returncode=0, stdout='ok')

    ok, output = mgr._pactl('set-card-profile', 'card', 'profile')

    assert ok is True
    cmd = mock_run.call_args[0][0]
    assert cmd[:4] == ['sudo', '-u', 'nomad', 'env']
    assert 'XDG_RUNTIME_DIR=/run/user/1000' in cmd
    assert 'pactl' in cmd


def test_pactl_returns_false_on_failure(mgr, mocker):
    """_pactl returns (False, '') when pactl fails."""
    mock_run = mocker.patch('chromium.subprocess.run')
    mock_run.return_value = MagicMock(returncode=1, stdout='')

    ok, output = mgr._pactl('set-card-profile', 'card', 'profile')

    assert ok is False


def test_pactl_handles_missing_binary(mgr, mocker):
    """_pactl returns (False, '') when pactl is not installed."""
    mocker.patch('chromium.subprocess.run', side_effect=FileNotFoundError)

    ok, output = mgr._pactl('list')

    assert ok is False
    assert output == ''


# --- Launch failure resets PipeWire ---

def test_launch_resets_pipewire_on_popen_failure(mgr, mocker):
    """If Popen raises, PipeWire is reset to analog."""
    mocker.patch('chromium.shutil.which', return_value='/usr/bin/chromium')
    mocker.patch('chromium.is_pi', return_value=False)
    mocker.patch('chromium.subprocess.Popen', side_effect=OSError('no display'))
    mocker.patch('chromium.subprocess.run')  # pkill
    mock_reset = mocker.patch.object(mgr, '_reset_pipewire')
    mock_set = mocker.patch.object(mgr, '_set_pipewire_profile')

    result = mgr.launch('https://youtube.com', audio_device='hdmiout')

    assert result is False
    mock_reset.assert_called_once()


# --- Orphan cleanup ---

def test_kill_orphans_handles_pkill_not_found(mgr, mocker):
    """_kill_orphans doesn't raise when pkill is missing."""
    mocker.patch('chromium.subprocess.run', side_effect=FileNotFoundError)
    mgr._kill_orphans()  # Should not raise


# --- URL scheme validation ---

def test_launch_rejects_file_url(mgr, mocker):
    """launch() rejects file:// URLs for security."""
    result = mgr.launch('file:///etc/passwd')
    assert result is False
    assert mgr.process is None


def test_launch_rejects_data_url(mgr, mocker):
    """launch() rejects data: URLs for security."""
    result = mgr.launch('data:text/html,<h1>hi</h1>')
    assert result is False


def test_launch_accepts_https_url(mgr, mocker):
    """launch() accepts https:// URLs."""
    mocker.patch('chromium.shutil.which', return_value='/usr/bin/chromium')
    mocker.patch('chromium.is_pi', return_value=False)
    mock_popen = mocker.patch('chromium.subprocess.Popen')
    mock_proc = MagicMock()
    mock_proc.pid = 1
    mock_proc.poll.return_value = None
    mock_popen.return_value = mock_proc
    mocker.patch.object(mgr, '_pactl', return_value=(True, ''))
    mocker.patch('chromium.subprocess.run')

    result = mgr.launch('https://youtube.com')
    assert result is True


def test_launch_accepts_http_url(mgr, mocker):
    """launch() accepts http:// URLs."""
    mocker.patch('chromium.shutil.which', return_value='/usr/bin/chromium')
    mocker.patch('chromium.is_pi', return_value=False)
    mock_popen = mocker.patch('chromium.subprocess.Popen')
    mock_proc = MagicMock()
    mock_proc.pid = 1
    mock_proc.poll.return_value = None
    mock_popen.return_value = mock_proc
    mocker.patch.object(mgr, '_pactl', return_value=(True, ''))
    mocker.patch('chromium.subprocess.run')

    result = mgr.launch('http://localhost:8080')
    assert result is True
