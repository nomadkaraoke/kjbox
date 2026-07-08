"""Tests for the original-vocals guide feature:
- MpvKaraokePlayer vocals state / set_vocals_volume / lavfi-complex mix graph
- routes._resolve_vocals_guide brand-code resolution + sibling derivation
"""
import os
import pytest

from filler import FillerVLC
from mpv_manager import MpvKaraokePlayer


@pytest.fixture
def player(mock_config):
    filler = FillerVLC(mock_config, enabled=False)
    return MpvKaraokePlayer(mock_config, filler, enabled=False)


# --- vocals state ---

def test_initial_vocals_state(player):
    assert player.vocals_volume == 0
    assert player.has_vocals_track is False


def test_set_vocals_volume_stores_and_clamps(player):
    player.set_vocals_volume(77)
    assert player.vocals_volume == 77
    player.set_vocals_volume(999)
    assert player.vocals_volume == 256
    player.set_vocals_volume(-5)
    assert player.vocals_volume == 0


def test_has_vocals_track_reflects_guide(player):
    assert player.has_vocals_track is False
    player._vocals_file = "/x/NOMAD-0001 - A - B.flac"
    assert player.has_vocals_track is True


def test_set_vocals_volume_inactive_no_ipc(player, mocker):
    """No guide loaded → just remember the level, don't touch the filter graph."""
    sp = mocker.patch.object(player, "_set_property")
    player.set_vocals_volume(128)
    sp.assert_not_called()


def test_apply_vocals_mix_builds_graph(player, mocker):
    sp = mocker.patch.object(player, "_set_property")
    player.active = True
    player._vocals_file = "/x/NOMAD-0001 - A - B.flac"
    player.set_vocals_volume(128)  # 128/256 = 0.5 gain
    sp.assert_called_once()
    prop, graph = sp.call_args[0]
    assert prop == "lavfi-complex"
    assert graph == "[aid2]volume=0.5000[gv];[aid1][gv]amix=inputs=2:normalize=0[ao]"
    assert player._lavfi_active is True


def test_apply_vocals_mix_zero_gain_off(player, mocker):
    sp = mocker.patch.object(player, "_set_property")
    player.active = True
    player._vocals_file = "/x/NOMAD-0001 - A - B.flac"
    player.set_vocals_volume(0)
    _, graph = sp.call_args[0]
    assert "volume=0.0000" in graph


def test_apply_vocals_mix_noop_without_guide(player, mocker):
    sp = mocker.patch.object(player, "_set_property")
    player._vocals_file = None
    player._apply_vocals_mix()
    sp.assert_not_called()


# --- routes._resolve_vocals_guide ---

@pytest.fixture
def guide_tree(tmp_path):
    masters = tmp_path / "NOMAD-720p"
    guides = tmp_path / "NOMAD-vocals-padded"
    masters.mkdir()
    guides.mkdir()
    (guides / "NOMAD-0123 - Artist - Song.flac").write_bytes(b"x")
    master = masters / "NOMAD-0123 - Artist - Song.mp4"
    master.write_bytes(b"x")
    return tmp_path, str(master), str(guides)


def test_resolve_guide_explicit_dir(guide_tree):
    from routes import _resolve_vocals_guide
    _, master, guides = guide_tree
    got = _resolve_vocals_guide(master, {"vocals_guide_dir": guides})
    assert got and got.endswith("NOMAD-0123 - Artist - Song.flac")


def test_resolve_guide_sibling_derivation(guide_tree):
    """No configured dir → derive the NOMAD-vocals-padded sibling of the master."""
    from routes import _resolve_vocals_guide
    _, master, _ = guide_tree
    got = _resolve_vocals_guide(master, {})
    assert got and got.endswith("NOMAD-0123 - Artist - Song.flac")


def test_resolve_guide_not_a_master(guide_tree):
    from routes import _resolve_vocals_guide
    tmp, _, guides = guide_tree
    got = _resolve_vocals_guide(str(tmp / "random file.mp4"), {"vocals_guide_dir": guides})
    assert got is None


def test_resolve_guide_no_match(guide_tree):
    from routes import _resolve_vocals_guide
    tmp, _, guides = guide_tree
    fake = str(tmp / "NOMAD-720p" / "NOMAD-9999 - X - Y.mp4")
    assert _resolve_vocals_guide(fake, {"vocals_guide_dir": guides}) is None


def test_resolve_guide_missing_dir(guide_tree):
    from routes import _resolve_vocals_guide
    _, master, _ = guide_tree
    assert _resolve_vocals_guide(master, {"vocals_guide_dir": "/no/such/dir"}) is None


def test_resolve_guide_none_path():
    from routes import _resolve_vocals_guide
    assert _resolve_vocals_guide(None, {"vocals_guide_dir": "/tmp"}) is None
