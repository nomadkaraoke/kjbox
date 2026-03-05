"""Tests for VLC process lifecycle decoupling: reconnection, state persistence."""

import json
import os

import pytest

from vlc import STATE_FILE, VLCManager


# --- State persistence ---

class TestStatePersistence:
    """Test _save_state / _load_state round-trip."""

    def test_save_and_load_state(self, mock_config, tmp_path, monkeypatch):
        state_file = str(tmp_path / "state.json")
        monkeypatch.setattr('vlc.STATE_FILE', state_file)

        vm = VLCManager(mock_config, enabled=True)
        vm.current_playing_path = "/media/Song - Artist.mp4"
        vm.current_filler_track = "filler.mp3"
        vm._save_state()

        loaded = vm._load_state()
        assert loaded['current_playing_path'] == "/media/Song - Artist.mp4"
        assert loaded['current_filler_track'] == "filler.mp3"

    def test_load_state_missing_file(self, mock_config, tmp_path, monkeypatch):
        monkeypatch.setattr('vlc.STATE_FILE', str(tmp_path / "nonexistent.json"))
        vm = VLCManager(mock_config, enabled=True)
        assert vm._load_state() == {}

    def test_load_state_corrupt_json(self, mock_config, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        state_file.write_text("{bad json")
        monkeypatch.setattr('vlc.STATE_FILE', str(state_file))
        vm = VLCManager(mock_config, enabled=True)
        assert vm._load_state() == {}

    def test_save_state_overwrites(self, mock_config, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr('vlc.STATE_FILE', str(state_file))

        vm = VLCManager(mock_config, enabled=True)
        vm.current_playing_path = "/media/test.mp4"
        vm._save_state()
        vm.current_playing_path = "/media/test2.mp4"
        vm._save_state()

        data = json.loads(state_file.read_text())
        assert data['current_playing_path'] == "/media/test2.mp4"


# --- Reconnection ---

class TestReconnect:
    """Test try_reconnect with mocked VLC HTTP responses."""

    def test_reconnect_disabled(self, mock_config):
        vm = VLCManager(mock_config, enabled=False)
        result = vm.try_reconnect()
        assert result == {'karaoke': False, 'filler': False}

    def test_reconnect_no_vlc_running(self, mock_config, mocker):
        vm = VLCManager(mock_config, enabled=True)
        mocker.patch.object(vm, '_probe_vlc', return_value=None)
        mocker.patch.object(vm, '_load_state', return_value={})

        result = vm.try_reconnect()
        assert result == {'karaoke': False, 'filler': False}
        assert vm.karaoke_active is False

    def test_reconnect_karaoke_playing(self, mock_config, mocker):
        vm = VLCManager(mock_config, enabled=True)
        karaoke_status = {'state': 'playing', 'volume': 200}
        filler_status = {'state': 'stopped', 'volume': 0}
        saved_state = {
            'current_playing_path': '/media/Song.mp4',
            'current_filler_track': 'filler.mp3',
        }

        mocker.patch.object(vm, '_probe_vlc', side_effect=[karaoke_status, filler_status])
        mocker.patch.object(vm, '_load_state', return_value=saved_state)

        result = vm.try_reconnect()
        assert result == {'karaoke': True, 'filler': True}
        assert vm.karaoke_active is True
        assert vm.current_playing_path == '/media/Song.mp4'
        assert vm.current_filler_track == 'filler.mp3'

    def test_reconnect_karaoke_paused(self, mock_config, mocker):
        vm = VLCManager(mock_config, enabled=True)
        mocker.patch.object(vm, '_probe_vlc', side_effect=[
            {'state': 'paused'}, None
        ])
        mocker.patch.object(vm, '_load_state', return_value={
            'current_playing_path': '/media/Song.mp4',
        })

        result = vm.try_reconnect()
        assert result == {'karaoke': True, 'filler': False}
        assert vm.karaoke_active is True

    def test_reconnect_karaoke_stopped(self, mock_config, mocker):
        vm = VLCManager(mock_config, enabled=True)
        mocker.patch.object(vm, '_probe_vlc', side_effect=[
            {'state': 'stopped'}, None
        ])
        mocker.patch.object(vm, '_load_state', return_value={})

        result = vm.try_reconnect()
        assert result == {'karaoke': True, 'filler': False}
        assert vm.karaoke_active is False
        assert vm.current_playing_path is None

    def test_reconnect_without_state_file(self, mock_config, mocker):
        """VLC playing but no state file — karaoke_active is set but no path."""
        vm = VLCManager(mock_config, enabled=True)
        mocker.patch.object(vm, '_probe_vlc', side_effect=[
            {'state': 'playing'}, None
        ])
        mocker.patch.object(vm, '_load_state', return_value={})

        result = vm.try_reconnect()
        assert vm.karaoke_active is True
        assert vm.current_playing_path is None

    def test_reconnect_filler_only(self, mock_config, mocker):
        vm = VLCManager(mock_config, enabled=True)
        mocker.patch.object(vm, '_probe_vlc', side_effect=[
            None, {'state': 'playing', 'volume': 100}
        ])
        mocker.patch.object(vm, '_load_state', return_value={
            'current_filler_track': 'background.mp3',
        })

        result = vm.try_reconnect()
        assert result == {'karaoke': False, 'filler': True}
        assert vm.karaoke_active is False
        assert vm.current_filler_track == 'background.mp3'


# --- Launch instance with start_new_session ---

def test_launch_instance_uses_start_new_session(mock_config, mocker):
    """Verify VLC is launched in its own session (process group)."""
    mocker.patch('vlc.is_pi', return_value=False)
    mock_popen = mocker.patch('vlc.subprocess.Popen')
    mocker.patch('vlc.time.sleep')
    mock_log = mocker.mock_open()
    mocker.patch('builtins.open', mock_log)

    vm = VLCManager(mock_config, enabled=True)
    vm.launch_instance("karaoke", 8080, "karaoke")

    mock_popen.assert_called_once()
    call_kwargs = mock_popen.call_args[1]
    assert call_kwargs.get('start_new_session') is True


# --- restart_instances with orphan VLC ---

def test_restart_kills_orphan_by_port(mock_config, mocker):
    """When processes dict has None (orphan), _kill_port is called."""
    mocker.patch('vlc.is_pi', return_value=False)
    mocker.patch('vlc.time.sleep')
    mocker.patch('vlc.subprocess.Popen')
    mocker.patch('builtins.open', mocker.mock_open())

    vm = VLCManager(mock_config, enabled=True)
    vm.processes = {"karaoke": None, "filler": None}

    mock_kill_port = mocker.patch.object(vm, '_kill_port')
    mocker.patch.object(vm, 'send_command', return_value=None)
    mocker.patch.object(vm, 'fade_in_filler')
    mocker.patch.object(vm, '_save_state')

    vm.restart_instances()

    assert mock_kill_port.call_count == 2
    mock_kill_port.assert_any_call(8080)
    mock_kill_port.assert_any_call(8081)


# --- play_video saves state ---

def test_play_video_saves_state(mock_config, tmp_media_dir, mocker):
    """play_video calls _save_state after setting playback state."""
    vm = VLCManager(mock_config, enabled=True)
    test_file = tmp_media_dir / "media" / "song.mp4"
    test_file.write_text("fake video")

    mocker.patch.object(vm, 'send_command', return_value={"state": "stopped"})
    mocker.patch('vlc.time.sleep')
    mocker.patch('vlc.threading.Thread')
    mock_save = mocker.patch.object(vm, '_save_state')

    vm.play_video(str(test_file), display_path="/media/song.mp4")
    mock_save.assert_called_once()
