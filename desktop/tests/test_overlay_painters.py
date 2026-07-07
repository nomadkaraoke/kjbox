import json
import os
import sys

import pytest

cairo = pytest.importorskip("cairo")  # skip the module if pycairo absent

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import overlay_painters as op


def _surface(w=1920, h=1080):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    return surf, cairo.Context(surf)


def _pixel(surf, x, y):
    surf.flush()
    data = bytes(surf.get_data())
    stride = surf.get_stride()
    off = y * stride + x * 4
    b, g, r, a = data[off], data[off + 1], data[off + 2], data[off + 3]
    return (r, g, b, a)


def _any_painted(surf, x0, y0, x1, y1):
    return any(_pixel(surf, x, y)[3] > 0 for x in range(x0, x1) for y in range(y0, y1))


# ---- helpers ----
def test_set_hex_sets_color():
    surf, cr = _surface(4, 4)
    op.set_hex(cr, "#ff0000", 1.0)
    cr.rectangle(0, 0, 4, 4)
    cr.fill()
    assert _pixel(surf, 1, 1) == (255, 0, 0, 255)


def test_text_width_positive():
    assert op.text_width("Hello", 20, bold=True) > 0
    assert op.text_width("", 20) == 0


def test_draw_text_returns_advance_and_paints():
    surf, cr = _surface(200, 50)
    adv = op.draw_text(cr, 5, 5, "Hi", 24, "#ffffff", bold=True)
    assert adv > 0
    assert _any_painted(surf, 5, 5, 60, 45)


def test_rounded_rect_fills_translucent():
    surf, cr = _surface(40, 40)
    op.rounded_rect(cr, 0, 0, 40, 40, 8)
    op.set_hex(cr, "#000000", 0.5)
    cr.fill()
    r, g, b, a = _pixel(surf, 20, 20)
    assert a == pytest.approx(128, abs=2)


def test_base_painter_position_top_right():
    p = op.BasePainter("id1", {"position": "top-right"}, show_over_video=True)
    p._w, p._h = 100, 50
    p._apply_position()
    assert p._x == 1920 - 100 - 20
    assert p._y == 20


# ---- text painters ----
def test_static_text_layout_sizes_to_content():
    p = op.StaticTextPainter("t", {
        "text": "Hello", "font_size": 30, "bold": True, "padding": 12,
        "text_color": "#ffffff", "bg_color": "#000000", "bg_opacity": 0.7,
        "position": "top-left",
    }, True)
    assert p._w > 24 and p._h > 24
    surf, cr = _surface()
    p.draw(cr)
    assert _any_painted(surf, 0, 0, 200, 80)


def test_ticker_layout_full_width_and_scrolls():
    p = op.TickerPainter("k", {
        "text": "Up next: Alice", "font_size": 28, "position": "bottom",
        "speed": 2, "text_color": "#ffffff", "bg_color": "#000000",
        "bg_opacity": 0.85, "padding": 10,
    }, True)
    assert p._w == 1920
    assert p._y == 1080 - p._h
    x0 = p._scroll_x
    p.tick(0.5)
    assert p._scroll_x < x0


def test_ticker_rotation_source_composes_from_cache(tmp_path):
    cache = tmp_path / "rotation_cache.json"
    cache.write_text(json.dumps({
        "queue": [{"singer": "Shylo R.", "song_artist": "", "status": "waiting", "paid": False},
                  {"singer": "Donte", "song_artist": "", "status": "waiting", "paid": False}],
        "stats": {}, "updated": __import__("time").time(),
    }))
    p = op.TickerPainter("k", {
        "source": "rotation", "cache_path": str(cache), "prefix": "Up next: ",
        "count": 5, "separator": "   ", "empty_text": "Scan the QR!",
        "font_size": 28, "position": "top",
    }, True)
    assert p._text == "Up next: 1. Shylo R.   2. Donte"


def test_ticker_rotation_source_empty_when_offline(tmp_path):
    p = op.TickerPainter("k", {
        "source": "rotation", "cache_path": str(tmp_path / "missing.json"),
        "prefix": "Up next: ", "count": 5, "empty_text": "Scan the QR!",
    }, True)
    assert p._text == "Up next: Scan the QR!"


def test_ticker_resets_after_scrolling_off():
    p = op.TickerPainter("k", {"text": "X", "speed": 100, "position": "top"}, True)
    p._scroll_x = -p._text_w - 5
    p.tick(0.01)
    assert p._scroll_x >= 1920 - 1


# ---- reserved-strip fill ----
def test_ticker_top_fills_reserved_strip():
    # A strip taller than the natural bar => the top ticker fills it (no wallpaper
    # gap to the video below), text centred, damage rect == the strip.
    p = op.TickerPainter("k", {"text": "hi", "position": "top",
                               "font_size": 35, "padding": 12, "_strip_h": 80}, True)
    assert p._h == 80
    assert p._y == 0
    assert p.bbox() == (0, 0, 1920, 80)
    assert 0 < p._text_y < 80  # text sits inside the bar


def test_ticker_top_natural_height_without_strip():
    p = op.TickerPainter("k", {"text": "hi", "position": "top",
                               "font_size": 35, "padding": 12}, True)
    assert p._h == 35 + 2 * 12       # natural, unchanged when no strip
    assert p._text_y == p._y + 12


def test_ticker_bottom_ignores_strip():
    # _strip_h only lifts a TOP ticker; a bottom ticker keeps its natural height.
    p = op.TickerPainter("k", {"text": "hi", "position": "bottom",
                               "font_size": 28, "padding": 10, "_strip_h": 80}, True)
    assert p._h == 28 + 2 * 10
    assert p._y == 1080 - p._h


# ---- bbox (partial-redraw damage regions) ----
def test_ticker_bbox_full_width_at_top():
    # A top-strip ticker damages the full-width strip (0,0,W,bar_h) — never the
    # video window lowered beneath it.
    p = op.TickerPainter("k", {"text": "hi", "position": "top",
                               "font_size": 28, "padding": 10}, True)
    x, y, w, h = p.bbox()
    assert (x, y, w) == (0, 0, 1920)
    assert h == p._h and h > 0


def test_qr_bbox_matches_card():
    p = op.QRCodePainter("q", {"url": "https://x", "size": 110, "padding": 10,
                               "position": "top-right"}, True)
    assert p.bbox() == (p._x, p._y, p._w, p._h)


def test_countdown_bbox_matches_card():
    p = op.CountdownPainter("c", {"target_time": "", "position": "top-center",
                                  "font_size": 40, "padding": 15}, False)
    x, y, w, h = p.bbox()
    assert (x, y, w, h) == (p._x, p._y, p._w, p._h)
    assert w > 0 and h > 0


def test_countdown_formats_remaining():
    p = op.CountdownPainter("c", {
        "target_time": "", "label": "Last call", "expired_text": "TIME!",
        "font_size": 40, "padding": 15, "position": "top-center",
        "text_color": "#ff4444", "bg_color": "#000000", "bg_opacity": 0.85,
    }, False)
    assert p._format_remaining(now_ts=0, target_ts=65) == "01:05"
    assert p._format_remaining(now_ts=0, target_ts=3725) == "1:02:05"
    assert p._format_remaining(now_ts=10, target_ts=0) == "TIME!"
    surf, cr = _surface()
    p.draw(cr)


# ---- qr + rotation + factory ----
def test_qr_painter_renders_card_and_qr():
    p = op.QRCodePainter("q", {
        "url": "https://sing.nomadkaraoke.com/?t=2121", "label": "Scan to sing",
        "size": 110, "padding": 10, "position": "top-right",
        "bg_color": "#000000", "bg_opacity": 0.85, "corner_radius": 12,
    }, True)
    assert p._w == 110 + 20
    surf, cr = _surface()
    p.draw(cr)
    # QR area should contain white (the QR modules) somewhere
    assert any(_pixel(surf, x, y)[0] > 200 for x in range(p._x, p._x + p._w)
               for y in range(p._y, p._y + p._h))


def test_rotation_list_draws_names(tmp_path):
    cache = tmp_path / "rotation_cache.json"
    cache.write_text(json.dumps({
        "queue": [
            {"singer": "Miguel", "song_artist": "Rooster - Alice in Chains",
             "status": "waiting", "paid": False},
            {"singer": "Aeris & Tara", "song_artist": "The Hand - Annabelle Dinda",
             "status": "Now Singing", "paid": True},
        ],
        "stats": {"started": "2026-06-04 20:54:55", "singers": 24, "sung": 40, "queued": 13},
        "updated": __import__("time").time(),
    }))
    p = op.RotationListPainter("r", {"cache_path": str(cache)}, False)
    surf, cr = _surface()
    p.draw(cr)
    # Heading + list region painted
    assert _any_painted(surf, op.RotationListPainter.LEFT_X, 50,
                        op.RotationListPainter.LEFT_X + 300, 250)


def test_rotation_list_offline_state(tmp_path):
    p = op.RotationListPainter("r", {"cache_path": str(tmp_path / "missing.json")}, False)
    surf, cr = _surface()
    p.draw(cr)  # must not raise; draws "Offline"
    assert _any_painted(surf, op.RotationListPainter.LEFT_X, 120,
                        op.RotationListPainter.LEFT_X + 200, 180)


def test_make_painter_factory():
    assert isinstance(op.make_painter({"type": "ticker", "id": "1", "config": {"text": "x"}}), op.TickerPainter)
    assert isinstance(op.make_painter({"type": "rotation_list", "id": "2", "config": {}}), op.RotationListPainter)
    assert op.make_painter({"type": "unknown", "id": "3"}) is None
