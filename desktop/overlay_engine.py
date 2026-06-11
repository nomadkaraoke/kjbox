#!/usr/bin/env python3
"""Overlay Engine v2 -- compositor-backed GTK overlay renderer.

Renders all overlays (ticker, QR, countdown, text, and the rotation list) into a
SINGLE always-on-top, transparent (RGBA), click-through, non-focus-stealing GTK3
window on top of the desktop wallpaper / live video. Replaces conky and the old
pygame engine.

Why one passive transparent window: with true compositor alpha, empty regions show
the video/wallpaper through; being click-through + never-focused, it cannot steal
focus from VLC or cover the video. See docs/archive/2026-06-09-overlay-renderer-v2-design.md.

Usage:
    python3 overlay_engine.py [--config PATH]
    python3 overlay_engine.py --render-png OUT.png [--config PATH] [--bg WALLPAPER] [--playing]
        # headless render of the current overlays to a PNG (no display / GTK needed)
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cairo  # gi-free; safe everywhere

from overlay_config import SCREEN_HEIGHT, SCREEN_WIDTH, apply_defaults, get_overlays_path
from overlay_painters import PAINTERS, make_painter

FPS = 30
CONFIG_POLL_INTERVAL = 1.0  # seconds between overlays.json mtime checks


def _log(msg):
    print(f"overlay_engine: {msg}", file=sys.stderr, flush=True)


def load_config(path):
    """Return (karaoke_playing, [overlay dicts with defaults applied])."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        _log(f"config load error: {e}")
        return False, []
    overlays = data.get("overlays", [])
    for o in overlays:
        apply_defaults(o)
    return bool(data.get("karaoke_playing", False)), overlays


def visible_overlays(karaoke_playing, overlays):
    """Overlays that should currently be drawn (enabled + visibility rule)."""
    out = []
    for o in overlays:
        if not o.get("enabled", False):
            continue
        if karaoke_playing and not o.get("show_over_video", False):
            continue  # desktop-only overlay hidden during video
        out.append(o)
    return out


# ----------------------------------------------------------------------------
# Headless PNG render (no GTK) -- for tests and on-device visual verification
# ----------------------------------------------------------------------------
def render_to_png(out_path, config_path, bg_path=None,
                  width=SCREEN_WIDTH, height=SCREEN_HEIGHT, karaoke_playing=None):
    """Render the currently-visible overlays to a PNG. gi-free.

    If bg_path is given it is drawn underneath (e.g. the wallpaper) for a realistic
    preview; otherwise the background is transparent.
    """
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    if bg_path and os.path.exists(bg_path):
        try:
            import io
            from PIL import Image
            img = Image.open(bg_path).convert("RGBA").resize((width, height))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            bg = cairo.ImageSurface.create_from_png(buf)
            cr.set_source_surface(bg, 0, 0)
            cr.paint()
        except Exception as e:  # pragma: no cover
            _log(f"bg load failed: {e}")
    playing, overlays = load_config(config_path)
    if karaoke_playing is not None:
        playing = karaoke_playing
    for o in visible_overlays(playing, overlays):
        p = make_painter(o)
        if p:
            try:
                p.draw(cr)
            except Exception as e:  # pragma: no cover
                _log(f"painter {o.get('id')} draw failed: {e}")
    surface.write_to_png(out_path)
    _log(f"rendered {out_path}")


# ----------------------------------------------------------------------------
# GTK application (imports gi lazily so --render-png works without GTK)
# ----------------------------------------------------------------------------
class OverlayApp:
    """Single transparent, click-through, non-focus-stealing GTK overlay window."""

    def __init__(self, config_path):
        self.config_path = config_path
        self.painters = {}            # overlay_id -> painter
        self.playing = False
        self._overlays = []
        self._config_mtime = -1
        self._last_frame = time.monotonic()

        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, GLib, Gtk
        self.Gdk, self.GLib, self.Gtk = Gdk, GLib, Gtk

        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_title("kjbox-overlay")
        screen = self.win.get_screen()
        visual = screen.get_rgba_visual()
        if visual is None:
            _log("FATAL: no RGBA visual; a compositor is required.")
            raise SystemExit(1)
        self.win.set_visual(visual)
        self.win.set_app_paintable(True)
        self.win.set_decorated(False)
        self.win.set_resizable(False)
        self.win.set_keep_above(True)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_skip_pager_hint(True)
        self.win.set_accept_focus(False)
        self.win.set_focus_on_map(False)
        self.win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.win.stick()  # show on all workspaces

        w = screen.get_width() or SCREEN_WIDTH
        h = screen.get_height() or SCREEN_HEIGHT
        self.win.move(0, 0)
        self.win.set_default_size(w, h)
        self.win.resize(w, h)

        self.win.connect("draw", self._on_draw)
        self.win.connect("realize", self._on_realize)
        self.win.connect("destroy", lambda *_: self.Gtk.main_quit())

    def _on_realize(self, *_):
        # Click-through: empty input shape means all clicks pass to VLC/desktop.
        gdk_win = self.win.get_window()
        if gdk_win is not None:
            gdk_win.input_shape_combine_region(cairo.Region(), 0, 0)

    def _compositor_ok(self):
        return bool(self.win.get_screen().is_composited())

    def _reload_config(self):
        self.playing, overlays = load_config(self.config_path)
        wanted = {o["id"]: o for o in overlays if o.get("id")}
        for oid in list(self.painters):
            if oid not in wanted:
                self.painters.pop(oid).cleanup()
        for oid, o in wanted.items():
            cls = PAINTERS.get(o.get("type"))
            if oid in self.painters and self.painters[oid].__class__ is cls:
                self.painters[oid].update_config(o.get("config", {}), o.get("show_over_video", False))
            else:
                if oid in self.painters:
                    self.painters.pop(oid).cleanup()
                p = make_painter(o)
                if p:
                    self.painters[oid] = p
        self._overlays = overlays

    def _poll_config(self):
        try:
            mtime = os.path.getmtime(self.config_path)
        except OSError:
            return True
        if mtime != self._config_mtime:
            self._config_mtime = mtime
            self._reload_config()
            self.win.queue_draw()
        return True  # keep the timeout

    def _on_frame(self):
        now = time.monotonic()
        dt = now - self._last_frame
        self._last_frame = now
        needs = False
        for o in visible_overlays(self.playing, self._overlays):
            p = self.painters.get(o["id"])
            if p and p.tick(dt):
                needs = True
        if needs:
            self.win.queue_draw()
        return True

    def _on_draw(self, _widget, cr):
        # Clear to fully transparent.
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        for o in visible_overlays(self.playing, self._overlays):
            p = self.painters.get(o["id"])
            if not p:
                continue
            try:
                cr.save()
                p.draw(cr)
                cr.restore()
            except Exception as e:
                cr.restore()
                _log(f"painter {o.get('id')} draw failed: {e}")
        return False

    def run(self):
        if not self._compositor_ok():
            _log("WARNING: screen is not composited; overlays would be opaque. "
                 "Waiting for a compositor before mapping the window.")
            self.GLib.timeout_add(2000, self._wait_for_compositor)
        else:
            self._start()
        self.Gtk.main()

    def _wait_for_compositor(self):
        if self._compositor_ok():
            _log("compositor available; starting overlay.")
            self._start()
            return False
        return True

    def _start(self):
        self._poll_config()
        self.win.show_all()
        self.GLib.timeout_add(int(CONFIG_POLL_INTERVAL * 1000), self._poll_config)
        self.GLib.timeout_add(int(1000 / FPS), self._on_frame)
        _log(f"started; config={self.config_path}")


def main():
    ap = argparse.ArgumentParser(description="kjbox overlay engine v2")
    ap.add_argument("--config", help="Path to overlays.json")
    ap.add_argument("--render-png", metavar="OUT", help="Headless: render overlays to a PNG and exit")
    ap.add_argument("--bg", help="Background image to composite under --render-png output")
    ap.add_argument("--playing", action="store_true", help="Render the over-video (karaoke playing) state")
    args = ap.parse_args()

    config_path = args.config or get_overlays_path()

    if args.render_png:
        render_to_png(args.render_png, config_path, bg_path=args.bg,
                      karaoke_playing=True if args.playing else None)
        return

    app = OverlayApp(config_path)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: app.Gtk.main_quit())
    signal.signal(signal.SIGINT, lambda *_: app.Gtk.main_quit())
    app.run()


if __name__ == "__main__":
    main()
