"""Parametric protocol-conformance tests: both karaoke players must satisfy
the same contract for read-only behaviours."""

import pytest

from filler import FillerVLC
from karaoke_player import KaraokePlayer, fade_steps
from mpv_manager import MpvKaraokePlayer
from vlc import VlcKaraokePlayer


class _SyncThread:
    """Runs the thread target inline so fadeout ramps complete synchronously."""

    def __init__(self, target, daemon=False, **kwargs):
        self.target = target

    def start(self):
        self.target()


def _count_fade_volume_writes(player, mocker, duration_s):
    """Run a fadeout and count how many volume-set primitives each engine issues."""
    import mpv_manager as _m
    import vlc as _v

    calls = []
    if hasattr(player, '_set_property'):  # mpv
        mocker.patch.object(player, '_send_ipc')
        mocker.patch.object(
            player, '_set_property',
            side_effect=lambda name, val: calls.append(1) if name == 'volume' else None)
    if hasattr(player, '_send'):  # vlc
        mocker.patch.object(
            player, '_send',
            side_effect=lambda cmd='', *a, **k: calls.append(1)
            if str(cmd).startswith('volume&val=') else None)
    mocker.patch.object(player, 'stop')
    mocker.patch.object(_m.time, 'sleep')
    mocker.patch.object(_v.time, 'sleep')
    mocker.patch.object(_m.threading, 'Thread', _SyncThread)
    mocker.patch.object(_v.threading, 'Thread', _SyncThread)

    player.fadeout(duration_s=duration_s)
    return len(calls)


# --- fade_steps helper (shared by both engines) ---

def test_fade_steps_scales_with_duration():
    assert fade_steps(3) < fade_steps(10) < fade_steps(20)


def test_fade_steps_floor_and_cap():
    assert fade_steps(0.5) == 20        # short fades never coarser than 20 steps
    assert fade_steps(1000) == 200      # runaway durations capped at 200 steps


# --- Both engines ramp equally, scaled to the requested duration ---

def test_fadeout_long_fade_ramps_more(player, mocker):
    count = _count_fade_volume_writes(player, mocker, 20)
    assert count >= fade_steps(20)      # at least the scaled step count on both engines


def test_fadeout_short_fade_uses_min_steps(player, mocker):
    count = _count_fade_volume_writes(player, mocker, 3)
    # 3s → fade_steps(3) ramp writes; allow the loop's inclusive end + one restore send
    assert fade_steps(3) <= count <= fade_steps(3) + 3


@pytest.fixture(params=[MpvKaraokePlayer, VlcKaraokePlayer], ids=['mpv', 'vlc'])
def player(request, mock_config):
    filler = FillerVLC(mock_config, enabled=False)
    return request.param(mock_config, filler, enabled=False)


# --- Protocol conformance ---

def test_isinstance_karaoke_player(player):
    assert isinstance(player, KaraokePlayer)


# --- Shared attribute shape ---

def test_has_name(player):
    assert isinstance(player.name, str)
    assert player.name in ('mpv', 'vlc')


def test_has_supports_flags(player):
    assert isinstance(player.supports_pitch, bool)
    assert isinstance(player.supports_cdg, bool)


def test_initial_active_is_false(player):
    assert player.active is False


def test_initial_current_path_is_none(player):
    assert player.current_path is None


def test_initial_pitch_zero(player):
    assert player.pitch_semitones == 0


def test_initial_audio_error_false(player):
    assert player.audio_error is False


# --- Shared method shape (no-op on disabled) ---

def test_stop_clears_active(player, mocker):
    # Allow both players to no-op gracefully without external processes
    if hasattr(player, '_send_ipc'):
        mocker.patch.object(player, '_send_ipc')
    if hasattr(player, '_send'):
        mocker.patch.object(player, '_send')
    mocker.patch.object(player, '_save_state', create=True)
    player.active = True
    player.stop()
    assert player.active is False


def test_get_status_returns_state_dict(player, mocker):
    if hasattr(player, '_send_ipc'):
        mocker.patch.object(player, '_send_ipc', return_value=None)
        mocker.patch.object(player, '_get_property', return_value=None)
    if hasattr(player, '_send'):
        mocker.patch.object(player, '_send', return_value=None)
    status = player.get_status()
    assert 'state' in status
    assert 'time' in status
    assert 'length' in status


def test_seek_updates_last_seek_time(player, mocker):
    import time as real_time
    if hasattr(player, '_send_ipc'):
        mocker.patch.object(player, '_send_ipc')
    if hasattr(player, '_send'):
        mocker.patch.object(player, '_send')
    before = real_time.time()
    player.seek(10)
    assert player.last_seek_time >= before


def test_set_volume_updates_attr(player, mocker):
    if hasattr(player, '_send_ipc'):
        mocker.patch.object(player, '_send_ipc')
        mocker.patch.object(player, '_set_property')
    if hasattr(player, '_send'):
        mocker.patch.object(player, '_send')
    player.set_volume(256)
    assert player.karaoke_volume == 256


def test_ensure_released_returns_bool(player, mocker):
    if hasattr(player, '_send_ipc'):
        mocker.patch.object(player, '_send_ipc')
        mocker.patch.object(player, '_get_property', return_value=True)
    if hasattr(player, '_send'):
        mocker.patch.object(player, '_send', return_value={"state": "stopped"})
    import mpv_manager as _m
    import vlc as _v
    mocker.patch.object(_m.time, 'sleep')
    mocker.patch.object(_v.time, 'sleep')
    result = player.ensure_released()
    assert isinstance(result, bool)
