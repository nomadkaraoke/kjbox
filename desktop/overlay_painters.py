"""Cairo painters for the overlay renderer.

Pure pycairo (NO gi/Gtk) so painters are testable headless. Each painter computes
its own size/position via overlay_config.calculate_position and draws onto a Cairo
context the engine supplies (the single full-screen ARGB surface).

Real per-pixel alpha: bg_opacity is a true alpha channel now (no black pre-blend).
"""

import io
import math
import os
from datetime import datetime

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

import rotation_source as rs
from overlay_config import SCREEN_HEIGHT, SCREEN_WIDTH, calculate_position, hex_to_rgb

FONT_FAMILY = "DejaVu Sans"

# Module-level scratch context for text measurement (no display needed).
_scratch_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8)
_scratch = cairo.Context(_scratch_surface)


# ----------------------------------------------------------------------------
# Cairo helpers
# ----------------------------------------------------------------------------
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
    ascent = cr.font_extents()[0]
    set_hex(cr, hex_color, alpha)
    cr.move_to(x, y_top + ascent)
    cr.show_text(text)
    return cr.text_extents(text).x_advance


def draw_text_baseline(cr, x, baseline_y, text, size, hex_color, bold=False, alpha=1.0):
    """Draw text anchored to a shared baseline (used to align mixed font sizes).
    Returns advance width."""
    if not text:
        return 0.0
    _select_font(cr, size, bold)
    set_hex(cr, hex_color, alpha)
    cr.move_to(x, baseline_y)
    cr.show_text(text)
    return cr.text_extents(text).x_advance


def rounded_rect(cr, x, y, w, h, r):
    """Append a rounded-rectangle path (caller fills/strokes). r<=0 => plain rect."""
    if r <= 0:
        cr.rectangle(x, y, w, h)
        return
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


# ----------------------------------------------------------------------------
# Base painter
# ----------------------------------------------------------------------------
class BasePainter:
    """Base class: holds config + computed geometry. Subclasses override layout/draw."""

    def __init__(self, overlay_id, config, show_over_video=False):
        self.overlay_id = overlay_id
        self.config = config or {}
        self.show_over_video = show_over_video
        self._x = self._y = 0
        self._w = self._h = 0
        self.layout()

    def _apply_position(self):
        self._x, self._y = calculate_position(
            self.config.get("position", "top-left"),
            self._w, self._h,
            self.config.get("custom_x"), self.config.get("custom_y"),
        )

    def layout(self):
        """Compute self._w/_h and call self._apply_position(). Override."""
        self._apply_position()

    def tick(self, dt):
        """Advance animation by dt seconds. Return True if a redraw is needed."""
        return False

    def draw(self, cr):
        """Draw onto cr at self._x/_y. Override."""

    def update_config(self, config, show_over_video):
        self.config = config or {}
        self.show_over_video = show_over_video
        self.layout()

    def cleanup(self):
        pass


# ----------------------------------------------------------------------------
# Text painters
# ----------------------------------------------------------------------------
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
        rounded_rect(cr, self._x, self._y, self._w, self._h, cfg.get("corner_radius", 0))
        set_hex(cr, cfg.get("bg_color", "#000000"), cfg.get("bg_opacity", 0.7))
        cr.fill()
        y = self._y + self._pad
        for ln in self._lines:
            draw_text(cr, self._x + self._pad, y, ln, self._size,
                      cfg.get("text_color", "#ffffff"), self._bold, self._italic)
            y += self._line_h


class TickerPainter(BasePainter):
    """Full-width scrolling text bar (top or bottom).

    For source=='rotation' the text is composed live from the rotation cache
    (replacing the old kj-controller push-based rotation_ticker_sync); otherwise
    the static config['text'] is used.
    """

    _ROTATION_TTL = 1.0  # seconds between cache re-reads for rotation source

    def layout(self):
        cfg = self.config
        self._size = cfg.get("font_size", 28)
        self._pad = cfg.get("padding", 10)
        self._source = cfg.get("source", "static")
        self._cache_path = cfg.get("cache_path", rs.CACHE_FILE)
        self._compose_at = 0.0
        self._text = self._resolve_text()
        self._text_w = text_width(self._text, self._size)
        self._w = SCREEN_WIDTH
        self._h = int(self._size + self._pad * 2)
        position = cfg.get("position", "bottom")
        self._x = 0
        self._y = 0 if position == "top" else SCREEN_HEIGHT - self._h
        if not hasattr(self, "_scroll_x"):
            self._scroll_x = SCREEN_WIDTH

    def _resolve_text(self):
        cfg = self.config
        if self._source != "rotation":
            return cfg.get("text", "") or ""
        snap = rs.load_snapshot(self._cache_path)
        return rs.compose_ticker_text(
            snap.queue if snap.online else [],
            prefix=cfg.get("prefix", "Up next: "),
            count=int(cfg.get("count", 5) or 0),
            separator=cfg.get("separator", "   "),
            empty_text=cfg.get("empty_text", ""),
        )

    def _maybe_recompose(self):
        import time as _t
        now = _t.time()
        if self._source == "rotation" and (now - self._compose_at) >= self._ROTATION_TTL:
            self._compose_at = now
            new = self._resolve_text()
            if new != self._text:
                self._text = new
                self._text_w = text_width(self._text, self._size)

    def tick(self, dt):
        self._maybe_recompose()
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
        sample_w = max(text_width("00:00:00", self._size, True),
                       text_width(self._label, self._label_size),
                       text_width(cfg.get("expired_text", "TIME!"), self._size, True))
        _, _, big_h = font_height(self._size, True)
        _, _, small_h = font_height(self._label_size)
        self._big_h, self._small_h = big_h, small_h
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
        return True

    def draw(self, cr):
        cfg = self.config
        rounded_rect(cr, self._x, self._y, self._w, self._h, cfg.get("corner_radius", 0))
        set_hex(cr, cfg.get("bg_color", "#000000"), cfg.get("bg_opacity", 0.85))
        cr.fill()
        color = cfg.get("text_color", "#ff4444")
        lw = text_width(self._label, self._label_size)
        draw_text(cr, self._x + (self._w - lw) / 2, self._y + self._pad, self._label,
                  self._label_size, color)
        txt = self._format_remaining()
        tw = text_width(txt, self._size, True)
        draw_text(cr, self._x + (self._w - tw) / 2, self._y + self._pad + self._small_h + 4,
                  txt, self._size, color, bold=True)


# ----------------------------------------------------------------------------
# Image + QR painters
# ----------------------------------------------------------------------------
def _pil_to_surface(img):
    """Convert a PIL image to a Cairo ImageSurface via an in-memory PNG."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return cairo.ImageSurface.create_from_png(buf)


class ImagePainter(BasePainter):
    """Display a PNG/JPG at a configurable position, scaled to target width."""

    def layout(self):
        cfg = self.config
        target_w = cfg.get("width", 150)
        self._surface = None
        path = cfg.get("image_path", "")
        if _pil and path and os.path.exists(path):
            img = Image.open(path).convert("RGBA")
            ow, oh = img.size
            scale = target_w / ow if ow else 1
            self._surface = _pil_to_surface(img.resize((target_w, max(1, int(oh * scale)))))
            self._w, self._h = self._surface.get_width(), self._surface.get_height()
        else:
            self._w = self._h = target_w
        self._apply_position()

    def draw(self, cr):
        if not self._surface:
            return
        cr.set_source_surface(self._surface, self._x, self._y)
        cr.paint()


class QRCodePainter(BasePainter):
    """QR code from a URL with an optional label, on a rounded translucent card."""

    def layout(self):
        cfg = self.config
        self._size = cfg.get("size", 180)
        self._pad = cfg.get("padding", 10)
        self._label = cfg.get("label", "")
        self._qr_surface = self._make_qr(cfg.get("url", ""), self._size)
        self._label_size = max(12, self._size // 10)
        self._label_h = (font_height(self._label_size)[2] + 4) if self._label else 0
        self._w = int(self._size + self._pad * 2)
        self._h = int(self._size + self._label_h + self._pad * 2)
        self._apply_position()

    def _make_qr(self, url, size):
        if not qrcode or not url or not _pil:
            return None
        qr = qrcode.QRCode(box_size=1, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
        img = img.resize((size, size), Image.NEAREST)
        return _pil_to_surface(img)

    def draw(self, cr):
        cfg = self.config
        rounded_rect(cr, self._x, self._y, self._w, self._h, cfg.get("corner_radius", 0))
        set_hex(cr, cfg.get("bg_color", "#000000"), cfg.get("bg_opacity", 1.0))
        cr.fill()
        if self._qr_surface:
            cr.set_source_surface(self._qr_surface, self._x + self._pad, self._y + self._pad)
            cr.paint()
        if self._label:
            lw = text_width(self._label, self._label_size)
            draw_text(cr, self._x + (self._w - lw) / 2, self._y + self._pad + self._size + 4,
                      self._label, self._label_size, cfg.get("text_color", "#ffffff"))


# ----------------------------------------------------------------------------
# Rotation list painter (replaces the conky between-songs screen)
# ----------------------------------------------------------------------------
class RotationListPainter(BasePainter):
    """Renders the rotation home screen: heading, stats, singer list, page dots.
    Reads /tmp/rotation_cache.json via rotation_source on each draw. The static
    branding (frame, promo bubbles, centre QR) lives in the desktop wallpaper."""

    # Layout constants (absolute px, matching the old conky rotation.conkyrc).
    LEFT_X = 90
    SONG_X = 115
    HEADING_Y = 60
    STATS_X = 460
    LIST_TOP = 135
    NAME_SIZE = 36
    SONG_SIZE = 20
    HEADING_SIZE = 40
    STATS_SIZE = 21
    BADGE_SIZE = 18
    ENTRY_GAP = 6

    COLOR_NAME = "#ffdf6b"
    COLOR_NUMBER = "#ffffff"
    COLOR_SONG = "#e0e6f0"
    COLOR_STATS = "#8892a4"
    COLOR_PAID = "#e74c3c"

    _CACHE_TTL = 1.0  # seconds between cache re-reads

    def layout(self):
        # Occupies the screen; bounds are the full display (used only for
        # show_over_video gating, not clipping).
        self._w, self._h = SCREEN_WIDTH, SCREEN_HEIGHT
        self._x = self._y = 0
        self._cache_path = self.config.get("cache_path", rs.CACHE_FILE)
        self._snap = None
        self._snap_at = 0.0

    def tick(self, dt):
        # Page cycling + stats are time-based; ask for periodic repaint.
        return True

    def _snapshot(self):
        import time as _t
        now = _t.time()
        if self._snap is None or (now - self._snap_at) >= self._CACHE_TTL:
            self._snap = rs.load_snapshot(self._cache_path)
            self._snap_at = now
        return self._snap

    def draw(self, cr):
        snap = self._snapshot()
        # Heading
        draw_text(cr, self.LEFT_X, self.HEADING_Y, "ROTATION", self.HEADING_SIZE,
                  self.COLOR_NUMBER, bold=True)
        if not snap.online:
            draw_text(cr, self.LEFT_X, self.LIST_TOP, "Offline", 28, self.COLOR_STATS)
            return
        # Stats
        st = snap.stats or {}
        parts = []
        if st.get("started"):
            parts.append(f"Started: {st['started']}")
        parts.append(f"{st.get('singers', 0)} singers | {st.get('sung', 0)} sung | "
                     f"{st.get('queued', 0)} queued")
        draw_text(cr, self.STATS_X, self.HEADING_Y + 14, "    ".join(parts),
                  self.STATS_SIZE, self.COLOR_STATS)
        # List
        if not snap.queue:
            draw_text(cr, self.LEFT_X, self.LIST_TOP, "No singers in queue", 28, self.COLOR_STATS)
            return
        page, start, page_num, total_pages = rs.paginate(snap.queue)
        name_asc, _, name_h = font_height(self.NAME_SIZE, True)
        _, _, song_h = font_height(self.SONG_SIZE)
        y = self.LIST_TOP
        for i, entry in enumerate(page, start=start + 1):
            baseline = y + name_asc
            x = self.LEFT_X
            x += draw_text_baseline(cr, x, baseline, f"{i}. ", self.NAME_SIZE,
                                    self.COLOR_NUMBER, bold=True)
            x += draw_text_baseline(cr, x, baseline, entry.singer, self.NAME_SIZE,
                                    self.COLOR_NAME, bold=True)
            if entry.paid:
                x += draw_text_baseline(cr, x, baseline, " ♥", self.NAME_SIZE, self.COLOR_PAID, bold=True)
            badge = rs.badge_text(entry.status)
            if badge:
                draw_text_baseline(cr, x + 12, baseline, badge, self.BADGE_SIZE,
                                   rs.status_color(entry.status), bold=True)
            y += name_h
            if entry.song_artist:
                draw_text(cr, self.SONG_X, y, entry.song_artist, self.SONG_SIZE, self.COLOR_SONG)
                y += song_h
            y += self.ENTRY_GAP
        # Page dots
        if total_pages > 1:
            dot_x = self.LEFT_X
            for p in range(total_pages):
                color = self.COLOR_NUMBER if p == page_num else self.COLOR_STATS
                dot_x += draw_text(cr, dot_x, y, "●  ", 16, color) or 0


# ----------------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------------
PAINTERS = {
    "ticker": TickerPainter,
    "static_text": StaticTextPainter,
    "image": ImagePainter,
    "countdown": CountdownPainter,
    "qr_code": QRCodePainter,
    "rotation_list": RotationListPainter,
}


def make_painter(overlay):
    """Build a painter from an overlay dict, or None for an unknown type."""
    cls = PAINTERS.get(overlay.get("type"))
    if not cls:
        return None
    return cls(overlay.get("id", ""), overlay.get("config", {}),
               overlay.get("show_over_video", False))
