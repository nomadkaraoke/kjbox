import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import render  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
THANKSGIVING = HERE / "events" / "2026-11-26-thanksgiving"


def _event(tmp_path: Path, **over) -> Path:
    ev = {
        "title": "Karaoke", "venue": "Babylon Kino - Five Points", "date": "Thu Dec 31", "time": "9pm-3am",
        "features": [{"icon": "1f3a4", "text": "Sing anything"}],
    }
    ev.update(over)
    d = tmp_path / "ev"
    d.mkdir(exist_ok=True)
    (d / "event.json").write_text(json.dumps(ev))
    return d


# ---------- emoji_code ----------

@pytest.mark.parametrize("value,code", [
    ("🦃", "1f983"),
    ("1f983", "1f983"),
    ("U+1F983", "1f983"),
    ("❤️", "2764"),                       # FE0F dropped, like Noto's filenames
    ("1f468_200d_1f3a4", "1f468_200d_1f3a4"),
    ("👨‍🎤", "1f468_200d_1f3a4"),
    ("1️⃣", "0031_20e3"),                 # keycaps: Noto pads codepoints to 4 hex digits
    ("0031_20e3", "0031_20e3"),
    ("31_20e3", "0031_20e3"),
])
def test_emoji_code(value, code):
    assert render.emoji_code(value) == code


def test_emoji_code_rejects_empty():
    with pytest.raises(render.EventError):
        render.emoji_code("️")


# ---------- ensure_emoji ----------

def test_ensure_emoji_uses_cache_without_fetching():
    def boom(url):
        raise AssertionError("should not download a cached emoji")
    assert render.ensure_emoji("🦃", fetch=boom).name == "1f983.svg"


def test_ensure_emoji_downloads_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "EMOJI_DIR", tmp_path)
    urls = []
    p = render.ensure_emoji("1f600", fetch=lambda u: urls.append(u) or b"<svg></svg>")
    assert p.read_bytes() == b"<svg></svg>"
    assert urls == [render.NOTO_URL.format(code="1f600")]


def test_ensure_emoji_rejects_non_svg(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "EMOJI_DIR", tmp_path)
    with pytest.raises(render.EventError, match="no SVG"):
        render.ensure_emoji("1f600", fetch=lambda u: b"404: Not Found")
    assert not (tmp_path / "1f600.svg").exists()


# ---------- load_event ----------

def test_load_event_fills_defaults_and_resolves_assets(tmp_path):
    ev = render.load_event(_event(tmp_path, decorations=[{"emoji": "🍁", "left": 0, "top": 0, "size": 50}]))
    assert ev["slug"] == "ev"
    assert ev["theme"]["title_font"] == "Shrikhand"
    assert ev["partner_logo"].startswith("file://") and ev["partner_logo"].endswith("babylon-kino-logo.png")
    assert ev["features"][0]["icon_src"].endswith("1f3a4.svg")
    assert ev["decorations"][0]["src"].endswith("1f341.svg")


def test_load_event_theme_override_keeps_other_defaults(tmp_path):
    ev = render.load_event(_event(tmp_path, theme={"accent": "#00ff00"}))
    assert ev["theme"]["accent"] == "#00ff00"
    assert ev["theme"]["date_color"] == render.DEFAULT_THEME["date_color"]


def test_load_event_missing_fields(tmp_path):
    with pytest.raises(render.EventError, match="venue"):
        render.load_event(_event(tmp_path, venue=""))


def test_load_event_feature_needs_icon_and_text(tmp_path):
    with pytest.raises(render.EventError, match="icon \\+ text"):
        render.load_event(_event(tmp_path, features=[{"text": "no icon"}]))


def test_load_event_missing_background(tmp_path):
    with pytest.raises(render.EventError, match="background_image"):
        render.load_event(_event(tmp_path, theme={"background_image": "nope.jpg"}))


def test_load_event_missing_file(tmp_path):
    with pytest.raises(render.EventError, match="not found"):
        render.load_event(tmp_path)


def test_thanksgiving_event_is_valid():
    ev = render.load_event(THANKSGIVING)
    assert ev["slug"] == "2026-thanksgiving-karaoke"
    assert ev["caption"]


# ---------- html ----------

def test_fonts_href_dedupes_families():
    href = render.fonts_href({**render.DEFAULT_THEME, "title_font": "Poppins"})
    assert href.count("family=Poppins") == 1
    assert "family=Bangers" in href


def test_build_html_injects_everything_and_escapes_script_end(tmp_path):
    ev = render.load_event(_event(tmp_path, quip="</script><b>hi"))
    html = render.build_html(ev, "square")
    for marker in ("__FONTS_HREF__", "__NOMAD_LOGO__", "__VARIANT__", "__EVENT_JSON__"):
        assert marker not in html
    assert '<body class="square">' in html
    assert "</script><b>hi" not in html


# ---------- real render (needs Google Chrome) ----------

def _has_chrome() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.launch(channel="chrome").close()
        return True
    except Exception:  # noqa: BLE001
        return False


chrome = pytest.mark.skipif(not _has_chrome(), reason="Google Chrome + playwright not available")


@chrome
def test_render_writes_both_sizes(tmp_path):
    from PIL import Image  # noqa: F401  (pillow optional)
    outs = render.render(_event(tmp_path), copy_to=tmp_path / "dl")
    sizes = sorted(Image.open(o).size for o in outs)
    assert sizes == [(1080, 1080), (1080, 1350)]
    assert sorted(p.name for p in (tmp_path / "dl").iterdir()) == ["ev-portrait.png", "ev-square.png"]
    assert not list((tmp_path / "ev").glob(".*.html"))  # temp html cleaned up


@chrome
def test_render_shrinks_long_copy_to_fit(tmp_path, capsys):
    feats = [{"icon": "1f3a4", "text": "A fairly long feature line that needs some room"}] * 6
    render.render(_event(tmp_path, features=feats))
    assert "scaled to" in capsys.readouterr().out


@chrome
def test_render_refuses_copy_that_cannot_fit(tmp_path):
    feats = [{"icon": "1f3a4", "text": "word " * 60}]
    with pytest.raises(render.EventError, match="overflows"):
        render.render(_event(tmp_path, features=feats))


@chrome
def test_render_without_curtain_swag(tmp_path):
    outs = render.render(_event(tmp_path, theme={"swag": None}))
    assert len(outs) == 2


@chrome
def test_render_refuses_sticker_wider_than_canvas(tmp_path):
    with pytest.raises(render.EventError, match="overflows"):
        render.render(_event(tmp_path, sticker=["GOBBLE" * 12]))
