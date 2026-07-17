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


@pytest.fixture
def disabled_player(mock_config):
    """A player with audio processing turned OFF (the production default)."""
    cfg = dict(mock_config)
    cfg["audio_processing_enabled"] = False
    filler = FillerVLC(cfg, enabled=False)
    return MpvKaraokePlayer(cfg, filler, enabled=False)


# --- audio processing disabled: nothing but the raw selected track plays ---

def test_disabled_reports_no_pitch(disabled_player):
    """supports_pitch=False when disabled → frontend hides the pitch controls."""
    assert disabled_player.supports_pitch is False


def test_disabled_launch_omits_rubberband(disabled_player, mocker):
    """mpv is launched WITHOUT the rubberband filter, so no audio processing
    is even possible."""
    disabled_player.enabled = True
    captured = {}

    def fake_popen(command, *a, **k):
        captured["command"] = command
        raise RuntimeError("stop after capturing args")

    mocker.patch("mpv_manager.subprocess.Popen", side_effect=fake_popen)
    mocker.patch("mpv_manager.open", mocker.mock_open())
    try:
        disabled_player.launch()
    except RuntimeError:
        pass
    assert not any("rubberband" in str(arg) for arg in captured["command"])


def test_enabled_launch_includes_rubberband(player, mocker):
    player.enabled = True
    captured = {}

    def fake_popen(command, *a, **k):
        captured["command"] = command
        raise RuntimeError("stop after capturing args")

    mocker.patch("mpv_manager.subprocess.Popen", side_effect=fake_popen)
    mocker.patch("mpv_manager.open", mocker.mock_open())
    try:
        player.launch()
    except RuntimeError:
        pass
    assert "--af=@rb:rubberband" in captured["command"]


def test_disabled_set_pitch_noop(disabled_player, mocker):
    ipc = mocker.patch.object(disabled_player, "_send_ipc")
    disabled_player.active = True
    disabled_player.set_pitch(4)
    assert disabled_player.pitch_semitones == 0
    ipc.assert_not_called()


def test_disabled_set_vocals_volume_noop(disabled_player, mocker):
    """Even with a guide path set + active, the graph is never built when off."""
    sp = mocker.patch.object(disabled_player, "_set_property")
    disabled_player.active = True
    disabled_player._vocals_file = "/x/NOMAD-0001 - A - B.flac"
    disabled_player.set_vocals_volume(128)
    assert disabled_player.vocals_volume == 0
    sp.assert_not_called()


def test_disabled_apply_vocals_mix_noop(disabled_player, mocker):
    sp = mocker.patch.object(disabled_player, "_set_property")
    disabled_player.active = True
    disabled_player._vocals_file = "/x/NOMAD-0001 - A - B.flac"
    disabled_player._apply_vocals_mix()
    sp.assert_not_called()


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


def _track_list(*, guide_id, instrumental_id):
    """Build a mpv track-list with an external guide + embedded instrumental."""
    return [
        {"type": "audio", "id": guide_id, "external": True,
         "codec": "flac", "title": "NOMAD-0001 - A - B.flac"},
        {"type": "audio", "id": instrumental_id, "external": False,
         "codec": "aac", "title": None},
        {"type": "video", "id": 1, "external": False},
    ]


def test_apply_vocals_mix_builds_graph(player, mocker):
    # Normal ordering: embedded instrumental=aid1, external guide=aid2.
    mocker.patch.object(player, "_get_property",
                        return_value=_track_list(guide_id=2, instrumental_id=1))
    sp = mocker.patch.object(player, "_set_property")
    player.active = True
    player._vocals_file = "/x/NOMAD-0001 - A - B.flac"
    player.set_vocals_volume(128)  # 128/256 = 0.5 gain
    sp.assert_called_once()
    prop, graph = sp.call_args[0]
    assert prop == "lavfi-complex"
    assert graph == "[aid2]volume=0.5000[gv];[aid1][gv]amix=inputs=2:normalize=0[ao]"
    assert player._lavfi_active is True


def test_apply_vocals_mix_inverted_ids(player, mocker):
    """Regression: mpv assigned the EXTERNAL guide aid1 and the embedded
    instrumental aid2 (observed live on a replace loadfile). The graph must
    scale the GUIDE (aid1) and pass the INSTRUMENTAL (aid2) at full — the old
    hardcoded [aid2]=guide/[aid1]=instrumental muted the instrumental, so only
    vocals were audible."""
    mocker.patch.object(player, "_get_property",
                        return_value=_track_list(guide_id=1, instrumental_id=2))
    sp = mocker.patch.object(player, "_set_property")
    player.active = True
    player._vocals_file = "/x/NOMAD-0001 - A - B.flac"
    player.set_vocals_volume(128)  # 0.5 gain on the GUIDE
    prop, graph = sp.call_args[0]
    assert prop == "lavfi-complex"
    # Guide (aid1) is the scaled input; instrumental (aid2) passes at full.
    assert graph == "[aid1]volume=0.5000[gv];[aid2][gv]amix=inputs=2:normalize=0[ao]"


def test_apply_vocals_mix_zero_gain_off(player, mocker):
    mocker.patch.object(player, "_get_property",
                        return_value=_track_list(guide_id=2, instrumental_id=1))
    sp = mocker.patch.object(player, "_set_property")
    player.active = True
    player._vocals_file = "/x/NOMAD-0001 - A - B.flac"
    player.set_vocals_volume(0)
    _, graph = sp.call_args[0]
    assert "volume=0.0000" in graph


def test_apply_vocals_mix_missing_track_clears(player, mocker):
    """Only one audio track present (guide attach lost) → never leave the
    instrumental muted. Clear any stale graph and fall back to normal audio."""
    mocker.patch.object(
        player, "_get_property",
        return_value=[{"type": "audio", "id": 1, "external": False, "codec": "aac"}])
    sp = mocker.patch.object(player, "_set_property")
    player.active = True
    player._lavfi_active = True
    player._vocals_file = "/x/NOMAD-0001 - A - B.flac"
    player._apply_vocals_mix()
    sp.assert_called_once_with("lavfi-complex", "")
    assert player._lavfi_active is False


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
