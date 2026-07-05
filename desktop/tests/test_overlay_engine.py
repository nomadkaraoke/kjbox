import json
import os
import sys
import time
import types

import pytest

cairo = pytest.importorskip("cairo")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import overlay_engine as eng


def _cfg(tmp_path, overlays, playing=False):
    p = tmp_path / "overlays.json"
    p.write_text(json.dumps({"karaoke_playing": playing, "overlays": overlays}))
    return str(p)


def test_load_config_applies_defaults(tmp_path):
    path = _cfg(tmp_path, [{"id": "q", "type": "qr_code", "enabled": True,
                            "config": {"url": "https://x"}}])
    playing, overlays = eng.load_config(path)
    assert playing is False
    assert overlays[0]["config"]["size"] == 180  # default applied


def test_load_config_bad_file_returns_empty(tmp_path):
    assert eng.load_config(str(tmp_path / "nope.json")) == (False, [])


def test_visible_overlays_hides_desktop_only_when_playing():
    overlays = [
        {"id": "a", "type": "ticker", "enabled": True, "show_over_video": True},
        {"id": "b", "type": "rotation_list", "enabled": True, "show_over_video": False},
        {"id": "c", "type": "ticker", "enabled": False, "show_over_video": True},
    ]
    playing = eng.visible_overlays(True, overlays)
    assert [o["id"] for o in playing] == ["a"]      # b hidden (desktop-only), c disabled
    home = eng.visible_overlays(False, overlays)
    assert [o["id"] for o in home] == ["a", "b"]    # both visible when not playing


def test_render_to_png_writes_file(tmp_path):
    cache = tmp_path / "rotation_cache.json"
    cache.write_text(json.dumps({
        "queue": [{"singer": "Alice", "song_artist": "S - A", "status": "waiting", "paid": False}],
        "stats": {"started": "t", "singers": 1, "sung": 0, "queued": 1},
        "updated": time.time(),
    }))
    path = _cfg(tmp_path, [
        {"id": "rot", "type": "rotation_list", "enabled": True, "show_over_video": False,
         "config": {"cache_path": str(cache)}},
        {"id": "qr", "type": "qr_code", "enabled": True, "show_over_video": True,
         "config": {"url": "https://sing.nomadkaraoke.com", "size": 100}},
    ])
    out = tmp_path / "out.png"
    eng.render_to_png(str(out), path)
    assert out.exists() and out.stat().st_size > 0
    surf = cairo.ImageSurface.create_from_png(str(out))
    assert surf.get_width() == eng.SCREEN_WIDTH


def test_apply_click_through_sets_empty_input_shape():
    """The overlay must stay click-through: an EMPTY input shape lets every
    pointer event pass to VLC/desktop windows underneath (and, via VNC, lets the
    KJ click desktop dialogs). Regression for the overlay swallowing all clicks
    because GTK's post-realize size-allocate had reset the shape to full-window.
    """
    recorded = []

    class _GdkWin:
        def input_shape_combine_region(self, region, off_x, off_y):
            recorded.append((region, off_x, off_y))

    class _Win:
        def get_window(self):
            return _GdkWin()

    fake = types.SimpleNamespace(win=_Win())
    result = eng.OverlayApp._apply_click_through(fake)

    assert result is False  # event handlers must not swallow the signal
    assert len(recorded) == 1
    region, off_x, off_y = recorded[0]
    assert region.is_empty()          # empty region == fully click-through
    assert (off_x, off_y) == (0, 0)


def test_apply_click_through_before_window_realized_is_noop():
    """Called before the GdkWindow exists (get_window() -> None) it must be a
    safe no-op rather than raising."""
    class _Win:
        def get_window(self):
            return None

    fake = types.SimpleNamespace(win=_Win())
    assert eng.OverlayApp._apply_click_through(fake) is False
