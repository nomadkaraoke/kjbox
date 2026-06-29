"""Tests for PlaybackCoordinator: renderer swap, delegation, rejection rules."""

import pytest

from config import DEFAULT_RENDER_MODE, RENDER_MODE_MPV, RENDER_MODE_VLC
from mpv_manager import MpvKaraokePlayer
from playback import PlaybackCoordinator, RendererSwitchRejected
from vlc import VlcKaraokePlayer


# --- Construction ---

def test_default_renderer_is_mpv(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False)
    assert c.render_mode == RENDER_MODE_MPV
    assert isinstance(c.player, MpvKaraokePlayer)


def test_config_render_mode_honored(mock_config):
    mock_config['render_mode'] = RENDER_MODE_VLC
    c = PlaybackCoordinator(mock_config, enabled=False)
    assert c.render_mode == RENDER_MODE_VLC
    assert isinstance(c.player, VlcKaraokePlayer)


def test_invalid_render_mode_falls_back_to_default(mock_config):
    mock_config['render_mode'] = 'bogus'
    c = PlaybackCoordinator(mock_config, enabled=False)
    assert c.render_mode == DEFAULT_RENDER_MODE


def test_initial_mode_overrides_config(mock_config):
    mock_config['render_mode'] = RENDER_MODE_MPV
    c = PlaybackCoordinator(mock_config, enabled=False, initial_mode=RENDER_MODE_VLC)
    assert c.render_mode == RENDER_MODE_VLC


def test_filler_is_shared_single_instance(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False)
    assert c.filler is not None
    # Filler should be same instance on the player
    assert c.player.filler is c.filler


# --- describe_renderer ---

def test_describe_renderer_mpv(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False)
    desc = c.describe_renderer()
    assert desc == {
        'mode': 'mpv',
        'supports_pitch': True,
        'supports_cdg': True,
        'available_modes': ['mpv', 'vlc'],
    }


def test_describe_renderer_vlc(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False, initial_mode=RENDER_MODE_VLC)
    desc = c.describe_renderer()
    assert desc['mode'] == 'vlc'
    assert desc['supports_pitch'] is False


# --- switch_renderer: happy path ---

def test_switch_renderer_changes_player_class(mock_config, mocker):
    c = PlaybackCoordinator(mock_config, enabled=False)
    assert isinstance(c.player, MpvKaraokePlayer)
    mocker.patch('playback.save_config_value')
    mocker.patch.object(c, '_start_monitor')

    c.switch_renderer(RENDER_MODE_VLC)
    assert c.render_mode == RENDER_MODE_VLC
    assert isinstance(c.player, VlcKaraokePlayer)


def test_switch_renderer_keeps_filler_instance(mock_config, mocker):
    c = PlaybackCoordinator(mock_config, enabled=False)
    filler_id_before = id(c.filler)
    mocker.patch('playback.save_config_value')
    mocker.patch.object(c, '_start_monitor')

    c.switch_renderer(RENDER_MODE_VLC)
    assert id(c.filler) == filler_id_before  # shared across the swap


def test_switch_renderer_preserves_on_karaoke_end_callback(mock_config, mocker):
    c = PlaybackCoordinator(mock_config, enabled=False)
    mocker.patch('playback.save_config_value')
    mocker.patch.object(c, '_start_monitor')
    callback = lambda: None
    c.on_karaoke_end = callback

    c.switch_renderer(RENDER_MODE_VLC)
    assert c.player.on_karaoke_end is callback


def test_switch_renderer_persists_config(mock_config, mocker):
    c = PlaybackCoordinator(mock_config, enabled=False)
    save = mocker.patch('playback.save_config_value')
    mocker.patch.object(c, '_start_monitor')

    c.switch_renderer(RENDER_MODE_VLC)
    save.assert_called_with('render_mode', 'vlc')


def test_switch_renderer_same_mode_is_noop(mock_config, mocker):
    c = PlaybackCoordinator(mock_config, enabled=False)
    player_before = c.player
    result = c.switch_renderer(RENDER_MODE_MPV)  # already mpv
    assert c.player is player_before
    assert result['mode'] == 'mpv'


def test_switch_renderer_invalid_raises(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False)
    with pytest.raises(ValueError):
        c.switch_renderer('bogus')


# --- switch_renderer: rejection during active playback ---

def test_switch_renderer_rejected_while_active(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False)
    c.player.active = True
    with pytest.raises(RendererSwitchRejected) as exc:
        c.switch_renderer(RENDER_MODE_VLC)
    assert exc.value.status_code == 409
    assert c.render_mode == RENDER_MODE_MPV  # unchanged


def test_switch_renderer_does_not_shutdown_when_rejected(mock_config, mocker):
    c = PlaybackCoordinator(mock_config, enabled=False)
    c.player.active = True
    shutdown = mocker.patch.object(c.player, 'shutdown')
    with pytest.raises(RendererSwitchRejected):
        c.switch_renderer(RENDER_MODE_VLC)
    shutdown.assert_not_called()


# --- Delegation: pass-through properties ---

def test_karaoke_active_reads_from_player(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False)
    c.player.active = True
    assert c.karaoke_active is True
    c.player.active = False
    assert c.karaoke_active is False


def test_karaoke_active_setter_updates_player(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False)
    c.karaoke_active = True
    assert c.player.active is True


def test_current_playing_path_passthrough(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False)
    c.player.current_path = '/song.mp4'
    assert c.current_playing_path == '/song.mp4'
    c.current_playing_path = '/new.mp4'
    assert c.player.current_path == '/new.mp4'


def test_current_filler_track_passthrough(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False)
    c.current_filler_track = 'wii.mp3'
    assert c.filler.current_track == 'wii.mp3'


def test_pitch_semitones_reads_from_player(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False)
    assert c.pitch_semitones == 0
    c.player._pitch_semitones = 3
    assert c.pitch_semitones == 3


def test_audio_device_setter_updates_both(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False)
    c.audio_device = 'usbmixer'
    assert c.player.audio_device == 'usbmixer'
    assert c.filler.audio_device == 'usbmixer'


def test_switch_renderer_preserves_audio_device(mock_config, mocker):
    """Non-default audio_device survives a renderer swap (rebuild doesn't
    clobber it via config defaults)."""
    c = PlaybackCoordinator(mock_config, enabled=False)
    c.audio_device = 'hw:0,0'
    mocker.patch('playback.save_config_value')
    mocker.patch.object(c, '_start_monitor')

    c.switch_renderer(RENDER_MODE_VLC)
    assert c.player.audio_device == 'hw:0,0'


def test_restart_instances_preserves_audio_device_override(mock_config, mocker):
    """Runtime audio_device override must persist across restart_instances
    — the player is rebuilt, which would otherwise revert to config default."""
    c = PlaybackCoordinator(mock_config, enabled=True)
    # Simulate an override set via coordinator (e.g. /av/vlc-device)
    c.audio_device = 'hw:0,0'
    assert c.player.audio_device == 'hw:0,0'

    mocker.patch.object(c.player, 'shutdown')
    mocker.patch.object(c.filler, 'shutdown')
    mocker.patch.object(c.filler, 'launch')
    mocker.patch.object(c.filler, 'fade_in')
    # Prevent the rebuilt player from actually launching mpv/vlc in the test
    mocker.patch.object(MpvKaraokePlayer, 'launch')
    mocker.patch.object(VlcKaraokePlayer, 'launch')
    mocker.patch.object(c, '_start_monitor')
    mocker.patch('playback.time.sleep')

    c.restart_instances()
    assert c.player.audio_device == 'hw:0,0'


# --- Delegation: method passthrough ---

def test_set_pitch_goes_to_player(mock_config, mocker):
    c = PlaybackCoordinator(mock_config, enabled=False)
    set_pitch = mocker.patch.object(c.player, 'set_pitch')
    c.set_pitch(4)
    set_pitch.assert_called_once_with(4)


def test_stop_karaoke_goes_to_player(mock_config, mocker):
    c = PlaybackCoordinator(mock_config, enabled=False)
    stop = mocker.patch.object(c.player, 'stop')
    c.stop_karaoke()
    stop.assert_called_once()


def test_fade_in_filler_goes_to_filler(mock_config, mocker):
    c = PlaybackCoordinator(mock_config, enabled=False)
    fade_in = mocker.patch.object(c.filler, 'fade_in')
    c.fade_in_filler()
    fade_in.assert_called_once()


def test_send_command_filler_routes_to_filler(mock_config, mocker):
    c = PlaybackCoordinator(mock_config, enabled=False)
    filler_send = mocker.patch.object(c.filler, 'send')
    c.send_command(8081, 'filler', 'pl_play')
    filler_send.assert_called_once()


def test_send_command_unknown_port_returns_none(mock_config, mocker):
    c = PlaybackCoordinator(mock_config, enabled=False)
    filler_send = mocker.patch.object(c.filler, 'send')
    assert c.send_command(9999, 'x', 'pl_play') is None
    filler_send.assert_not_called()


# --- play_video: stops filler before play ---

def test_play_video_stops_filler_first(mock_config, mocker, tmp_path):
    f = tmp_path / "song.mp4"
    f.write_text("")
    c = PlaybackCoordinator(mock_config, enabled=True)
    mocker.patch.object(c.filler, 'send', return_value={"state": "playing"})
    fade_out = mocker.patch.object(c.filler, 'fade_out')
    ensure = mocker.patch.object(c.filler, 'ensure_stopped')
    play = mocker.patch.object(c.player, 'play')
    mocker.patch('playback.time.sleep')

    c.play_video(str(f))
    fade_out.assert_called_once()
    ensure.assert_called_once()
    play.assert_called_once_with(str(f), display_path=None, overlay_manager=None, audio_file=None)


def test_play_video_threads_audio_file_to_player(mock_config, mocker, tmp_path):
    """An external audio file (CDG zip on mpv) is forwarded to the player."""
    cdg = tmp_path / "song.cdg"
    cdg.write_text("")
    mp3 = tmp_path / "song.mp3"
    mp3.write_text("")
    c = PlaybackCoordinator(mock_config, enabled=True)
    mocker.patch.object(c.filler, 'send', return_value={"state": "stopped"})
    play = mocker.patch.object(c.player, 'play')
    mocker.patch('playback.time.sleep')

    c.play_video(str(cdg), display_path=str(cdg), audio_file=str(mp3))
    play.assert_called_once_with(
        str(cdg), display_path=str(cdg), overlay_manager=None, audio_file=str(mp3)
    )


def test_play_video_skips_fadeout_when_filler_stopped(mock_config, mocker, tmp_path):
    f = tmp_path / "song.mp4"
    f.write_text("")
    c = PlaybackCoordinator(mock_config, enabled=True)
    mocker.patch.object(c.filler, 'send', return_value={"state": "stopped"})
    fade_out = mocker.patch.object(c.filler, 'fade_out')
    mocker.patch.object(c.player, 'play')

    c.play_video(str(f))
    fade_out.assert_not_called()


# --- set_audio_backend ---

def test_set_audio_backend_updates_both(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False)
    c.set_audio_backend('pipewire')
    assert c.audio_backend == 'pipewire'
    assert c.filler.audio_backend == 'pipewire'
    assert c.player.audio_backend == 'pipewire'


# --- try_reconnect ---

def test_try_reconnect_disabled(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False)
    assert c.try_reconnect() == {'karaoke': False, 'filler': False}


def test_try_reconnect_returns_both_flags(mock_config, mocker):
    c = PlaybackCoordinator(mock_config, enabled=True)
    mocker.patch.object(c.player, 'try_reconnect', return_value=True)
    mocker.patch.object(c.filler, 'try_reconnect', return_value=False)
    result = c.try_reconnect()
    assert result == {'karaoke': True, 'filler': False}


# --- processes (sleep_mode compat) ---

def test_processes_exposes_karaoke_and_filler(mock_config):
    c = PlaybackCoordinator(mock_config, enabled=False)
    c.player.process = 'karaoke-proc'
    c.filler.process = 'filler-proc'
    assert c.processes == {'karaoke': 'karaoke-proc', 'filler': 'filler-proc'}
