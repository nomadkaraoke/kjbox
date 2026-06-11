# Overlay Renderer v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace conky with a single compositor-backed, transparent, click-through GTK3 window that draws all overlays (including the rotation list) with real per-pixel alpha over the wallpaper/video — eliminating the full-screen window that hid the karaoke video.

**Architecture:** A pure-Python data layer (`rotation_source.py`) feeds a set of pycairo **painters** (`overlay_painters.py`), which are composited by a thin GTK engine (`overlay_engine.py`) into one always-on-top, RGBA, click-through, non-focus-stealing window. The kj-controller config layer (`overlays.json`, CRUD UI, rotation cache) is reused; a new `rotation_list` overlay type is added; the push-based `rotation_ticker_sync.py` and all conky assets are retired.

**Tech Stack:** Python 3.12, pycairo (painters — pip-installable, no GTK needed for tests), PyGObject/GTK3 + Gdk (engine window only), PIL + `qrcode` (QR bitmap), pytest. Design spec: `docs/archive/2026-06-09-overlay-renderer-v2-design.md`.

**Testability boundary (important):** `rotation_source.py` imports only the stdlib. `overlay_painters.py` imports only `cairo` (pycairo), `PIL`, `qrcode`. **Only `overlay_engine.py` imports `gi`/Gtk/Gdk.** This lets painter and data tests run anywhere pycairo is installed, without a display or GTK.

**Reference — current conky behaviour to preserve** (from `desktop/rotation_data.py`, captured in the design doc): left-column singer list (gold names `#ffdf6b` bold 36, song lines `#e0e6f0` size 20, indent), "ROTATION" heading (white bold 40), stats line (`#8892a4` size 21), colour-coded status badges, red `♥` paid marker, 10-per-page cycling every 10s with page dots, "Offline"/"No singers in queue" states. The **rules panel is removed**. Static branding (frame, promo bubbles, centre QR, strapline) stays in the **desktop wallpaper** and is not drawn by the renderer.

---

## File structure

**Create:**
- `desktop/rotation_source.py` — pure data layer: load `/tmp/rotation_cache.json`, paginate, status→colour, compose ticker text.
- `desktop/overlay_painters.py` — `cairo_helpers` + `BasePainter` + `TickerPainter`, `StaticTextPainter`, `CountdownPainter`, `ImagePainter`, `QRCodePainter`, `RotationListPainter`, `make_painter()` factory.
- `desktop/tests/` — `test_rotation_source.py`, `test_overlay_painters.py` (pytest; pycairo-gated).

**Modify:**
- `desktop/overlay_engine.py` — rewrite as a GTK3 app (single transparent click-through window, config polling, visibility, render loop, compositor guard, `--render-png`).
- `desktop/overlay_config.py` — add `rotation_list` to `OVERLAY_TYPES` + `TYPE_DEFAULTS`.
- `desktop/overlay-display.service` — `ExecStart` env (drop SDL, GTK uses X11).
- `kj-controller/overlay.py` — add `rotation_list` to `OVERLAY_TYPES` + a preset.
- `kj-controller/rotation.py` — remove the `rotation_ticker_sync` hook (keep `_write_display_cache`).
- `kj-controller/templates/index.html`, `kj-controller/static/app.js` — add `rotation_list` to the overlay CRUD form + type labels.

**Delete:**
- `desktop/overlay_types.py` (replaced by `overlay_painters.py`)
- `desktop/rotation_data.py`, `desktop/rotation.conkyrc`, `desktop/rotation_rules.txt`
- `kj-controller/rotation_ticker_sync.py` and its tests
- `kj-controller/tests/unit/test_overlay_types.py`, `test_qr_overlay_visual.py`, `test_engine_restack.py` (pygame-era; replaced by painter tests)

---

## Phase 1 — Data layer (`rotation_source.py`)

Pure Python, no rendering deps. This is where the conky data semantics are reproduced.

### Task 1: rotation_source module

**Files:**
- Create: `desktop/rotation_source.py`
- Create: `desktop/tests/__init__.py` (empty)
- Test: `desktop/tests/test_rotation_source.py`

- [ ] **Step 1: Write the failing tests**

```python
# desktop/tests/test_rotation_source.py
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import rotation_source as rs


def _write_cache(tmp_path, queue, stats, updated=None):
    p = tmp_path / "rotation_cache.json"
    p.write_text(json.dumps({
        "queue": queue, "stats": stats,
        "updated": updated if updated is not None else time.time(),
    }))
    return str(p)


def test_load_returns_queue_and_stats(tmp_path):
    path = _write_cache(tmp_path,
        [{"singer": "Alice", "song_artist": "Song - Artist", "status": "Now Singing", "paid": True}],
        {"started": "2026-06-04 20:54:55", "singers": 24, "sung": 40, "queued": 13})
    snap = rs.load_snapshot(path)
    assert snap.online is True
    assert snap.stats["singers"] == 24
    assert snap.queue[0].singer == "Alice"
    assert snap.queue[0].paid is True


def test_stale_cache_is_offline(tmp_path):
    path = _write_cache(tmp_path, [], {}, updated=time.time() - 9999)
    snap = rs.load_snapshot(path, max_age=120)
    assert snap.online is False


def test_missing_cache_is_offline(tmp_path):
    snap = rs.load_snapshot(str(tmp_path / "nope.json"))
    assert snap.online is False
    assert snap.queue == []


def test_status_color_mapping():
    assert rs.status_color("Now Singing") == "#2d8a4e"
    assert rs.status_color("Up Next") == "#d4720a"
    assert rs.status_color("waiting") == "#d4720a"
    assert rs.status_color("Being Made") == "#cc3333"
    assert rs.status_color("On Hold") == "#888888"
    assert rs.status_color("BRB") == "#888888"
    assert rs.status_color("Skipped") == "#3b82f6"
    assert rs.status_color("anything else") == "#8892a4"


def test_badge_text_hidden_for_waiting_and_empty():
    assert rs.badge_text("waiting") is None
    assert rs.badge_text("") is None
    assert rs.badge_text("Now Singing") == "Now Singing"


def test_paginate_single_page():
    q = list(range(5))
    page, start, page_num, total = rs.paginate(q, now=0.0)
    assert (page, start, page_num, total) == ([0, 1, 2, 3, 4], 0, 0, 1)


def test_paginate_cycles_every_10s():
    q = list(range(25))  # 3 pages of 10/10/5
    p0 = rs.paginate(q, now=0.0)
    p1 = rs.paginate(q, now=10.0)
    p2 = rs.paginate(q, now=20.0)
    p_wrap = rs.paginate(q, now=30.0)
    assert p0[2] == 0 and p0[1] == 0 and p0[0] == list(range(0, 10))
    assert p1[2] == 1 and p1[1] == 10 and p1[0] == list(range(10, 20))
    assert p2[2] == 2 and p2[1] == 20 and p2[0] == list(range(20, 25))
    assert p_wrap[2] == 0  # wraps back to page 0
    assert p0[3] == 3      # total_pages


def test_compose_ticker_text_basic():
    entries = [type("E", (), {"singer": "Alice"})(), type("E", (), {"singer": "Bob"})()]
    out = rs.compose_ticker_text(entries, prefix="Up next: ", count=5,
                                 separator="   ", empty_text="none")
    assert out == "Up next: 1. Alice   2. Bob"


def test_compose_ticker_text_empty():
    assert rs.compose_ticker_text([], "Up next: ", 5, "   ", "Scan the QR!") == "Up next: Scan the QR!"
    assert rs.compose_ticker_text([1, 2], "P:", 0, " ", "x") == "P:x"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd desktop && python3 -m pytest tests/test_rotation_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rotation_source'`

- [ ] **Step 3: Write the implementation**

```python
# desktop/rotation_source.py
"""Pure data layer for the overlay renderer.

Reads the rotation snapshot that kj-controller writes to /tmp/rotation_cache.json
(see kj-controller/rotation.py::_write_display_cache) and exposes it as structured
data for the painters. Reproduces the display semantics that desktop/rotation_data.py
implemented for conky (status colours, pagination, ticker text) — but returns data,
not conky markup. No rendering or GTK dependencies.
"""

import json
import os
import time
from dataclasses import dataclass, field

CACHE_FILE = "/tmp/rotation_cache.json"
CACHE_MAX_AGE = 120  # seconds; older than this is treated as offline
PAGE_SIZE = 10
PAGE_DURATION = 10  # seconds per page when cycling

# Status badge colours (hex with leading #). Mirrors rotation_data._status_color.
COLOR_NOW = "#2d8a4e"
COLOR_NEXT = "#d4720a"
COLOR_WIP = "#cc3333"
COLOR_HOLD = "#888888"
COLOR_SKIPPED = "#3b82f6"
COLOR_DEFAULT = "#8892a4"


@dataclass
class Entry:
    singer: str
    song_artist: str
    status: str
    paid: bool


@dataclass
class Snapshot:
    online: bool
    queue: list = field(default_factory=list)   # list[Entry]
    stats: dict = field(default_factory=dict)    # {started, singers, sung, queued}


def load_snapshot(path=CACHE_FILE, max_age=CACHE_MAX_AGE):
    """Load and parse the rotation cache. Returns a Snapshot; online=False when
    the file is missing, stale, or unparseable (callers render an offline state)."""
    try:
        mtime = os.path.getmtime(path)
        if time.time() - mtime > max_age:
            return Snapshot(online=False)
        with open(path) as f:
            data = json.load(f)
        queue = [
            Entry(
                singer=e.get("singer", ""),
                song_artist=e.get("song_artist", ""),
                status=e.get("status", ""),
                paid=bool(e.get("paid", False)),
            )
            for e in data.get("queue", [])
        ]
        return Snapshot(online=True, queue=queue, stats=data.get("stats", {}))
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return Snapshot(online=False)


def status_color(status):
    """Badge colour for a status string (mirrors rotation_data._status_color)."""
    s = (status or "").lower()
    if "singing" in s or s == "now singing":
        return COLOR_NOW
    if "next" in s or s == "waiting":
        return COLOR_NEXT
    if "being made" in s:
        return COLOR_WIP
    if "on hold" in s or "brb" in s:
        return COLOR_HOLD
    if s == "skipped":
        return COLOR_SKIPPED
    return COLOR_DEFAULT


def badge_text(status):
    """Return the badge label for a status, or None when no badge should show.
    Conky showed no badge for empty status or plain 'waiting'."""
    if not status:
        return None
    if status.lower() == "waiting":
        return None
    return status


def paginate(queue, now=None, page_size=PAGE_SIZE, page_duration=PAGE_DURATION):
    """Return (page_entries, start_index, page_num, total_pages) for the current
    time slice. Cycles pages every `page_duration` seconds when queue > page_size."""
    if now is None:
        now = time.time()
    total = len(queue)
    if total <= page_size:
        return queue, 0, 0, 1
    num_pages = (total + page_size - 1) // page_size
    page = int(now / page_duration) % num_pages
    start = page * page_size
    return queue[start:start + page_size], start, page, num_pages


def compose_ticker_text(entries, prefix, count, separator, empty_text):
    """Compose the 'Up next: 1. X  2. Y' ticker string.

    Moved verbatim (semantics) from kj-controller/rotation_ticker_sync.compose_ticker_text.
    `entries` items must expose a `.singer` attribute (rotation_source.Entry) OR be
    dicts with a 'singer' key. Caller passes queue[:count]-eligible entries already
    filtered (the cache queue already excludes done/left)."""
    if count <= 0:
        return f"{prefix}{empty_text}"
    slice_ = list(entries)[:count]
    if not slice_:
        return f"{prefix}{empty_text}"

    def name(e):
        return e.singer if hasattr(e, "singer") else e["singer"]

    slots = [f"{i}. {name(e)}" for i, e in enumerate(slice_, start=1)]
    return f"{prefix}{separator.join(slots)}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd desktop && python3 -m pytest tests/test_rotation_source.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add desktop/rotation_source.py desktop/tests/__init__.py desktop/tests/test_rotation_source.py
git commit -m "feat(overlay): add pure rotation_source data layer (replaces rotation_data conky markup)"
```

---

## Phase 2 — Painters (`overlay_painters.py`)

pycairo only (no GTK). Each painter computes its own size/position and draws onto a
Cairo context the engine provides (the full-screen surface). Real per-pixel alpha:
`bg_opacity` is a true alpha channel now (no black pre-blend).

### Task 2: Cairo helpers + BasePainter

**Files:**
- Create: `desktop/overlay_painters.py`
- Test: `desktop/tests/test_overlay_painters.py`

- [ ] **Step 1: Write the failing tests**

```python
# desktop/tests/test_overlay_painters.py
import os
import sys

import pytest

cairo = pytest.importorskip("cairo")  # skip the whole module if pycairo absent

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import overlay_painters as op


def _surface(w=1920, h=1080):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    return surf, cairo.Context(surf)


def _pixel(surf, x, y):
    """Return (r, g, b, a) 0-255 for a pixel on an ARGB32 surface."""
    surf.flush()
    data = bytes(surf.get_data())
    stride = surf.get_stride()
    off = y * stride + x * 4
    b, g, r, a = data[off], data[off + 1], data[off + 2], data[off + 3]
    return (r, g, b, a)


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
    # Something white was painted somewhere in the box
    assert any(_pixel(surf, x, y)[3] > 0 for x in range(5, 60) for y in range(5, 45))


def test_rounded_rect_fills_translucent():
    surf, cr = _surface(40, 40)
    op.rounded_rect(cr, 0, 0, 40, 40, 8)
    op.set_hex(cr, "#000000", 0.5)
    cr.fill()
    r, g, b, a = _pixel(surf, 20, 20)
    assert a == pytest.approx(128, abs=2)  # 0.5 alpha


def test_base_painter_position_top_right():
    p = op.BasePainter("id1", {"position": "top-right"}, show_over_video=True)
    p._w, p._h = 100, 50
    p._apply_position()
    assert p._x == 1920 - 100 - 20  # SCREEN_WIDTH - w - MARGIN
    assert p._y == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd desktop && python3 -m pytest tests/test_overlay_painters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'overlay_painters'` (or skip if pycairo missing — install with `pip install pycairo`)

- [ ] **Step 3: Write the helpers + BasePainter**

```python
# desktop/overlay_painters.py
"""Cairo painters for the overlay renderer.

Pure pycairo (NO gi/Gtk) so painters are testable headless. Each painter computes
its own size/position via overlay_config.calculate_position and draws onto a Cairo
context the engine supplies (the single full-screen ARGB surface).
"""

import io
import os

import cairo

try:
    import qrcode
except ImportError:  # pragma: no cover
    qrcode = None

try:
    from PIL import Image
    _pil = True
except ImportError:  # pragma: no cover
    _pil = False

from overlay_config import calculate_position, hex_to_rgb

FONT_FAMILY = "DejaVu Sans"

# Module-level scratch context for text measurement (no display needed).
_scratch_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8)
_scratch = cairo.Context(_scratch_surface)


def _select_font(cr, size, bold=False, italic=False):
    slant = cairo.FONT_SLANT_ITALIC if italic else cairo.FONT_SLANT_NORMAL
    weight = cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL
    cr.select_font_face(FONT_FAMILY, slant, weight)
    cr.set_font_size(size)


def set_hex(cr, hex_color, alpha=1.0):
    """Set the Cairo source to a hex colour with real alpha."""
    r, g, b = hex_to_rgb(hex_color)
    cr.set_source_rgba(r / 255.0, g / 255.0, b / 255.0, alpha)


def text_width(text, size, bold=False, italic=False):
    """Measure the advance width of `text` in px (0 for empty)."""
    if not text:
        return 0.0
    _select_font(_scratch, size, bold, italic)
    return _scratch.text_extents(text).x_advance


def font_height(size, bold=False):
    """Return (ascent, descent, line_height) for a font size."""
    _select_font(_scratch, size, bold)
    ascent, descent, height, _, _ = _scratch.font_extents()
    return ascent, descent, height


def draw_text(cr, x, y_top, text, size, hex_color, bold=False, italic=False, alpha=1.0):
    """Draw `text` with its top-left at (x, y_top). Returns the advance width."""
    if not text:
        return 0.0
    _select_font(cr, size, bold, italic)
    ascent, _, _, _, _ = cr.font_extents()
    set_hex(cr, hex_color, alpha)
    cr.move_to(x, y_top + ascent)
    cr.show_text(text)
    return cr.text_extents(text).x_advance


def rounded_rect(cr, x, y, w, h, r):
    """Append a rounded-rectangle path (caller fills/strokes). r<=0 => plain rect."""
    if r <= 0:
        cr.rectangle(x, y, w, h)
        return
    r = min(r, w / 2, h / 2)
    import math
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class BasePainter:
    """Base class: holds config + computed geometry. Subclasses override layout/draw."""

    def __init__(self, overlay_id, config, show_over_video=False):
        self.overlay_id = overlay_id
        self.config = config or {}
        self.show_over_video = show_over_video
        self._x = self._y = 0
        self._w = self._h = 0
        self.layout()

    # ---- geometry ----
    def _apply_position(self):
        """Set self._x/_y from config position + current self._w/_h."""
        self._x, self._y = calculate_position(
            self.config.get("position", "top-left"),
            self._w, self._h,
            self.config.get("custom_x"), self.config.get("custom_y"),
        )

    def layout(self):
        """Compute self._w/_h and call self._apply_position(). Override."""
        self._apply_position()

    def tick(self, dt):
        """Advance animation by dt seconds. Override if animated. Returns True if
        a redraw is needed."""
        return False

    def draw(self, cr):
        """Draw onto cr at self._x/_y. Override."""

    def update_config(self, config, show_over_video):
        self.config = config or {}
        self.show_over_video = show_over_video
        self.layout()

    def cleanup(self):
        """Release any resources. Override if needed."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd desktop && python3 -m pytest tests/test_overlay_painters.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add desktop/overlay_painters.py desktop/tests/test_overlay_painters.py
git commit -m "feat(overlay): Cairo helpers + BasePainter (real per-pixel alpha)"
```

### Task 3: StaticTextPainter + TickerPainter

**Files:**
- Modify: `desktop/overlay_painters.py` (append classes)
- Test: `desktop/tests/test_overlay_painters.py` (append tests)

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_static_text_layout_sizes_to_content():
    p = op.StaticTextPainter("t", {
        "text": "Hello", "font_size": 30, "bold": True, "padding": 12,
        "text_color": "#ffffff", "bg_color": "#000000", "bg_opacity": 0.7,
        "position": "top-left",
    }, True)
    assert p._w > 24 and p._h > 24       # content + 2*padding
    surf, cr = _surface()
    p.draw(cr)                            # must not raise


def test_ticker_layout_full_width_and_scrolls():
    p = op.TickerPainter("k", {
        "text": "Up next: Alice", "font_size": 28, "position": "bottom",
        "speed": 2, "text_color": "#ffffff", "bg_color": "#000000",
        "bg_opacity": 0.85, "padding": 10,
    }, True)
    assert p._w == 1920                   # full-width bar
    assert p._y == 1080 - p._h            # bottom
    x0 = p._scroll_x
    p.tick(0.5)
    assert p._scroll_x < x0               # scrolled left


def test_ticker_resets_after_scrolling_off():
    p = op.TickerPainter("k", {"text": "X", "speed": 100, "position": "top"}, True)
    p._scroll_x = -p._text_w - 5
    p.tick(0.01)
    assert p._scroll_x >= 1920 - 1        # wrapped back to the right edge
```

- [ ] **Step 2: Run to verify fail**

Run: `cd desktop && python3 -m pytest tests/test_overlay_painters.py -k "static_text or ticker" -v`
Expected: FAIL (`AttributeError: module 'overlay_painters' has no attribute 'StaticTextPainter'`)

- [ ] **Step 3: Append the painters**

```python
# --- append to desktop/overlay_painters.py ---

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080


class StaticTextPainter(BasePainter):
    """Static text block with a rounded translucent background."""

    def layout(self):
        cfg = self.config
        self._size = cfg.get("font_size", 36)
        self._bold = cfg.get("bold", True)
        self._italic = cfg.get("italic", False)
        self._pad = cfg.get("padding", 12)
        self._lines = (cfg.get("text", "") or "").split("\n")
        _, _, line_h = font_height(self._size, self._bold)
        self._line_h = line_h
        text_w = max((text_width(ln, self._size, self._bold, self._italic) for ln in self._lines), default=0)
        self._w = int(text_w + self._pad * 2)
        self._h = int(line_h * len(self._lines) + self._pad * 2)
        self._apply_position()

    def draw(self, cr):
        cfg = self.config
        radius = cfg.get("corner_radius", 0)
        rounded_rect(cr, self._x, self._y, self._w, self._h, radius)
        set_hex(cr, cfg.get("bg_color", "#000000"), cfg.get("bg_opacity", 0.7))
        cr.fill()
        y = self._y + self._pad
        for ln in self._lines:
            draw_text(cr, self._x + self._pad, y, ln, self._size,
                      cfg.get("text_color", "#ffffff"), self._bold, self._italic)
            y += self._line_h


class TickerPainter(BasePainter):
    """Full-width scrolling text bar (top or bottom)."""

    def layout(self):
        cfg = self.config
        self._size = cfg.get("font_size", 28)
        self._pad = cfg.get("padding", 10)
        self._text = cfg.get("text", "") or ""
        self._text_w = text_width(self._text, self._size)
        self._w = SCREEN_WIDTH
        self._h = int(self._size + self._pad * 2)
        position = cfg.get("position", "bottom")
        self._x = 0
        self._y = 0 if position == "top" else SCREEN_HEIGHT - self._h
        if not hasattr(self, "_scroll_x"):
            self._scroll_x = SCREEN_WIDTH

    def tick(self, dt):
        speed = self.config.get("speed", 2)
        self._scroll_x -= speed * 100 * dt   # speed=1 => 100px/s
        if self._scroll_x < -self._text_w:
            self._scroll_x = SCREEN_WIDTH
        return True

    def draw(self, cr):
        cfg = self.config
        cr.rectangle(self._x, self._y, self._w, self._h)
        set_hex(cr, cfg.get("bg_color", "#000000"), cfg.get("bg_opacity", 0.85))
        cr.fill()
        draw_text(cr, int(self._scroll_x), self._y + self._pad, self._text,
                  self._size, cfg.get("text_color", "#ffffff"))
```

- [ ] **Step 4: Run to verify pass**

Run: `cd desktop && python3 -m pytest tests/test_overlay_painters.py -k "static_text or ticker" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add desktop/overlay_painters.py desktop/tests/test_overlay_painters.py
git commit -m "feat(overlay): static text + scrolling ticker painters"
```

### Task 4: CountdownPainter

**Files:**
- Modify: `desktop/overlay_painters.py`
- Test: `desktop/tests/test_overlay_painters.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_countdown_formats_remaining(monkeypatch):
    import overlay_painters as op2
    # 65 seconds in the future
    p = op2.CountdownPainter("c", {
        "target_time": "", "label": "Last call", "expired_text": "TIME!",
        "font_size": 40, "padding": 15, "position": "top-center",
        "text_color": "#ff4444", "bg_color": "#000000", "bg_opacity": 0.85,
    }, False)
    assert p._format_remaining(now_ts=0, target_ts=65) == "01:05"
    assert p._format_remaining(now_ts=0, target_ts=3725) == "1:02:05"
    assert p._format_remaining(now_ts=10, target_ts=0) == "TIME!"
    surf, cr = _surface()
    p.draw(cr)  # must not raise
```

- [ ] **Step 2: Run to verify fail**

Run: `cd desktop && python3 -m pytest tests/test_overlay_painters.py -k countdown -v`
Expected: FAIL (`AttributeError ... CountdownPainter`)

- [ ] **Step 3: Append the painter**

```python
# --- append to desktop/overlay_painters.py ---
from datetime import datetime


class CountdownPainter(BasePainter):
    """Live countdown to a target time (ISO 8601 in config['target_time'])."""

    def layout(self):
        cfg = self.config
        self._size = cfg.get("font_size", 40)
        self._label_size = max(14, self._size // 2)
        self._pad = cfg.get("padding", 15)
        self._label = cfg.get("label", "")
        try:
            self._target_ts = datetime.fromisoformat(cfg.get("target_time", "")).timestamp()
        except (ValueError, TypeError):
            self._target_ts = None
        # Size from a sample so the box doesn't jitter as digits change.
        sample_w = max(text_width("00:00:00", self._size, True),
                       text_width(self._label, self._label_size),
                       text_width(cfg.get("expired_text", "TIME!"), self._size, True))
        _, _, big_h = font_height(self._size, True)
        _, _, small_h = font_height(self._label_size)
        self._w = int(sample_w + self._pad * 2)
        self._h = int(big_h + small_h + 4 + self._pad * 2)
        self._apply_position()

    def _format_remaining(self, now_ts=None, target_ts=None):
        import time as _t
        now_ts = _t.time() if now_ts is None else now_ts
        target_ts = self._target_ts if target_ts is None else target_ts
        if target_ts is None:
            return self.config.get("expired_text", "TIME!")
        total = int(target_ts - now_ts)
        if total <= 0:
            return self.config.get("expired_text", "TIME!")
        h, m, s = total // 3600, (total % 3600) // 60, total % 60
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def tick(self, dt):
        return True  # always repaint (1s cadence handled by engine)

    def draw(self, cr):
        cfg = self.config
        rounded_rect(cr, self._x, self._y, self._w, self._h, cfg.get("corner_radius", 0))
        set_hex(cr, cfg.get("bg_color", "#000000"), cfg.get("bg_opacity", 0.85))
        cr.fill()
        color = cfg.get("text_color", "#ff4444")
        _, _, small_h = font_height(self._label_size)
        lw = text_width(self._label, self._label_size)
        draw_text(cr, self._x + (self._w - lw) / 2, self._y + self._pad, self._label,
                  self._label_size, color)
        txt = self._format_remaining()
        tw = text_width(txt, self._size, True)
        draw_text(cr, self._x + (self._w - tw) / 2, self._y + self._pad + small_h + 4,
                  txt, self._size, color, bold=True)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd desktop && python3 -m pytest tests/test_overlay_painters.py -k countdown -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add desktop/overlay_painters.py desktop/tests/test_overlay_painters.py
git commit -m "feat(overlay): countdown painter"
```

---
