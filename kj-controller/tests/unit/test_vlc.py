"""Tests for VLCManager (all with VLC disabled)."""

from vlc import VLCManager


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
    # Should not raise
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
