"""Tests for SleepManager."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from sleep_mode import SLEEP_FLAG, SLEEP_STATE, SleepManager


@pytest.fixture
def sleep_mgr(tmp_path, monkeypatch):
    """SleepManager with flag/state paths pointed at tmp_path."""
    flag = str(tmp_path / 'kj-sleep-mode')
    state = str(tmp_path / 'kj-sleep-state.json')
    monkeypatch.setattr('sleep_mode.SLEEP_FLAG', flag)
    monkeypatch.setattr('sleep_mode.SLEEP_STATE', state)
    mgr = SleepManager()
    mgr._flag_path = flag
    mgr._state_path = state
    return mgr


@pytest.fixture
def cfg():
    return {'log_file': '/dev/null'}


class TestIsSleeping:
    def test_not_sleeping_by_default(self, sleep_mgr):
        assert sleep_mgr.is_sleeping() is False

    def test_sleeping_when_flag_exists(self, sleep_mgr, tmp_path):
        flag = tmp_path / 'kj-sleep-mode'
        flag.touch()
        assert sleep_mgr.is_sleeping() is True


class TestGetStatus:
    def test_status_when_awake(self, sleep_mgr):
        status = sleep_mgr.get_status()
        assert status['active'] is False
        assert status['entering'] is False
        assert status['exiting'] is False
        assert 'state' not in status

    def test_status_when_sleeping_with_state_file(self, sleep_mgr, tmp_path):
        flag = tmp_path / 'kj-sleep-mode'
        flag.touch()
        state = tmp_path / 'kj-sleep-state.json'
        state_data = {'entered_at': '2026-03-21T14:30:00', 'autodeploy_was_active': True}
        state.write_text(json.dumps(state_data))
        status = sleep_mgr.get_status()
        assert status['active'] is True
        assert status['state']['entered_at'] == '2026-03-21T14:30:00'

    def test_status_when_sleeping_without_state_file(self, sleep_mgr, tmp_path):
        flag = tmp_path / 'kj-sleep-mode'
        flag.touch()
        status = sleep_mgr.get_status()
        assert status['active'] is True
        assert 'state' not in status


class TestEnterSleep:
    def test_already_sleeping_returns_early(self, sleep_mgr, tmp_path, cfg):
        (tmp_path / 'kj-sleep-mode').touch()
        result = sleep_mgr.enter_sleep(cfg)
        assert result['active'] is True
        assert 'Already' in result['message']

    @patch('sleep_mode.subprocess.run')
    def test_enter_sleep_calls_script(self, mock_run, sleep_mgr, tmp_path, cfg):
        # Make the script "succeed" by creating the flag file
        def side_effect(*args, **kwargs):
            if args[0][0].endswith('sleep-enter.sh'):
                (tmp_path / 'kj-sleep-mode').touch()
            result = MagicMock()
            result.returncode = 0
            result.stdout = 'ok'
            result.stderr = ''
            return result
        mock_run.side_effect = side_effect

        result = sleep_mgr.enter_sleep(cfg)
        assert result['active'] is True

        # Verify the shell script was called
        script_calls = [c for c in mock_run.call_args_list
                        if c[0][0][0].endswith('sleep-enter.sh')]
        assert len(script_calls) == 1

    @patch('sleep_mode.subprocess.run')
    def test_enter_sleep_stops_vlc(self, mock_run, sleep_mgr, tmp_path, cfg):
        # Script creates flag as side effect
        def side_effect(*args, **kwargs):
            if args[0][0].endswith('sleep-enter.sh'):
                (tmp_path / 'kj-sleep-mode').touch()
            return MagicMock(returncode=0, stdout='ok', stderr='')
        mock_run.side_effect = side_effect

        vlc = MagicMock()
        vlc.enabled = True
        vlc.processes = {}
        sleep_mgr.enter_sleep(cfg, vlc=vlc)

        vlc.fade_out_filler.assert_called_once()
        vlc.ensure_filler_stopped.assert_called_once()
        vlc.ensure_karaoke_released.assert_called_once()

    @patch('sleep_mode.subprocess.run')
    def test_enter_sleep_script_failure_reports_error(self, mock_run, sleep_mgr, cfg):
        mock_run.return_value = MagicMock(returncode=1, stdout='', stderr='script failed')
        result = sleep_mgr.enter_sleep(cfg)
        assert len(result['errors']) > 0
        assert 'script failed' in result['errors'][0]


class TestExitSleep:
    def test_not_sleeping_returns_early(self, sleep_mgr, cfg):
        result = sleep_mgr.exit_sleep(cfg)
        assert result['active'] is False
        assert 'Not in sleep mode' in result['message']

    @patch('sleep_mode.subprocess.run')
    def test_exit_sleep_calls_script(self, mock_run, sleep_mgr, tmp_path, cfg):
        (tmp_path / 'kj-sleep-mode').touch()

        def side_effect(*args, **kwargs):
            if args[0][0].endswith('sleep-exit.sh'):
                (tmp_path / 'kj-sleep-mode').unlink(missing_ok=True)
            result = MagicMock()
            result.returncode = 0
            result.stdout = 'ok'
            result.stderr = ''
            return result
        mock_run.side_effect = side_effect

        result = sleep_mgr.exit_sleep(cfg)
        assert result['active'] is False

    @patch('sleep_mode.subprocess.run')
    def test_exit_sleep_restarts_vlc(self, mock_run, sleep_mgr, tmp_path, cfg):
        (tmp_path / 'kj-sleep-mode').touch()

        # Script removes flag as side effect
        def side_effect(*args, **kwargs):
            if args[0][0].endswith('sleep-exit.sh'):
                (tmp_path / 'kj-sleep-mode').unlink(missing_ok=True)
            return MagicMock(returncode=0, stdout='ok', stderr='')
        mock_run.side_effect = side_effect

        vlc = MagicMock()
        vlc.enabled = True
        sleep_mgr.exit_sleep(cfg, vlc=vlc)

        vlc.restart_instances.assert_called_once()
