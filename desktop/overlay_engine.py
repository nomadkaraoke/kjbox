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
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cairo  # gi-free; safe everywhere

from overlay_config import SCREEN_HEIGHT, SCREEN_WIDTH, apply_defaults, get_overlays_path
from overlay_painters import PAINTERS, make_painter

FPS = 30
CONFIG_POLL_INTERVAL = 1.0  # seconds between overlays.json mtime checks
# Self-reported perf for the KJ Controller monitor. The engine is a separate
# process, so it publishes its real frame rate + per-frame raster cost to a small
# file the Flask app's PerfSampler reads (see kj-controller/perf_sampler.py).
PERF_FILE = "/tmp/kj-overlay-perf.json"
PERF_WRITE_INTERVAL = 1.0


def _log(msg):
    print(f"overlay_engine: {msg}", file=sys.stderr, flush=True)


def load_config(path):
    """Return (karaoke_playing, [overlay dicts with defaults applied]).

    Injects the reserved-strip height (top-level `video_top_margin_px`, written by
    kj-controller) into each top ticker's config as `_strip_h`, so the ticker
    fills the strip and no wallpaper band shows between it and the video below.
    One source of truth: kj-controller's `video_top_margin_px` drives both the
    video geometry and the ticker height.
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        _log(f"config load error: {e}")
        return False, []
    overlays = data.get("overlays", [])
    strip_h = int(data.get("video_top_margin_px", 0) or 0)
    for o in overlays:
        apply_defaults(o)
        if o.get("type") == "ticker":
            o["config"]["_strip_h"] = strip_h
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
        gi.require_version("Gdk", "3.0")  # pin before import; system default Gdk is 4.0
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
        # Click-through must be (re)applied after realize AND after every
        # map/size-allocate. GTK resets the input shape to the full window on
        # the size-allocate that follows realize (and on any later resize, e.g.
        # an HDMI mode change), silently clobbering a realize-only application —
        # the overlay then swallows every click on the screen, so nothing behind
        # it (VLC, desktop dialogs) is reachable, including via VNC.
        self.win.connect("realize", self._apply_click_through)
        self.win.connect("map-event", self._apply_click_through)
        self.win.connect("size-allocate", self._apply_click_through)
        self.win.connect("destroy", lambda *_: self.Gtk.main_quit())

    def _apply_click_through(self, *_):
        # Empty input shape => all pointer events pass through to VLC/desktop.
        gdk_win = self.win.get_window()
        if gdk_win is not None:
            gdk_win.input_shape_combine_region(cairo.Region(), 0, 0)
        return False

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

    def _collect_dirty(self, dt):
        """Advance animations; return the damage rects of painters that changed.

        Returns a list of ``(x, y, w, h)`` — one per currently-visible painter
        whose ``tick()`` asked for a redraw. Empty when nothing animates (the
        frame callback then invalidates nothing). This is the crux of the 4K
        frame-drop fix: instead of invalidating the whole transparent window
        (which forced the compositor to re-blend the entire screen — including
        the video region — every ticker frame), each animated overlay damages
        only its own bounding box. A top-strip ticker's rect never overlaps the
        lowered video window, so the video keeps its cheap page-flip path.
        """
        dirty = []
        for o in visible_overlays(self.playing, self._overlays):
            p = self.painters.get(o["id"])
            if p and p.tick(dt):
                dirty.append(p.bbox())
        return dirty

    def _on_frame(self):
        now = time.monotonic()
        dt = now - self._last_frame
        self._last_frame = now
        self._perf_frames += 1  # actual callback rate; < FPS ⇒ engine is starved
        for x, y, w, h in self._collect_dirty(dt):
            self.win.queue_draw_area(int(x), int(y), int(w), int(h))
        return True

    def _on_draw(self, _widget, cr):
        _t0 = time.perf_counter()
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
        raster_ms = (time.perf_counter() - _t0) * 1000.0
        # EMA so the reported cost is stable rather than per-frame noisy.
        self._perf_raster_ms = raster_ms if self._perf_raster_ms is None \
            else (0.7 * self._perf_raster_ms + 0.3 * raster_ms)
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
        # Perf self-reporting state (see PERF_FILE).
        self._perf_frames = 0
        self._perf_raster_ms = None
        self._perf_last = time.monotonic()
        # Reset here (not just in __init__) so the first frame's dt measures time
        # since startup finished, not since construction — otherwise the compositor
        # wait inflates the first dt and the ticker jumps on frame 1.
        self._last_frame = time.monotonic()
        self._poll_config()
        self.win.show_all()
        self.GLib.timeout_add(int(CONFIG_POLL_INTERVAL * 1000), self._poll_config)
        self.GLib.timeout_add(int(1000 / FPS), self._on_frame)
        self.GLib.timeout_add(int(PERF_WRITE_INTERVAL * 1000), self._write_perf)
        _log(f"started; config={self.config_path}")

    def _write_perf(self):
        """Publish real overlay FPS + raster cost to PERF_FILE once per second.

        `fps` is the actual _on_frame callback rate — it sits at ~FPS when the
        engine keeps up and drops when the process is starved. Best-effort; a
        failed write is silently skipped and retried next tick.
        """
        now = time.monotonic()
        elapsed = now - self._perf_last
        fps = round(self._perf_frames / elapsed, 1) if elapsed > 0 else 0.0
        self._perf_frames = 0
        self._perf_last = now
        payload = {
            "fps": fps,
            "raster_ms": round(self._perf_raster_ms, 2)
            if self._perf_raster_ms is not None else None,
            "ts": time.time(),
        }
        # Atomic write via a randomly-named temp file in the target dir (avoids a
        # predictable /tmp path that could be symlink-attacked). Mirrors
        # overlay.py's _save().
        try:
            fd, tmp = tempfile.mkstemp(
                dir=os.path.dirname(PERF_FILE) or ".",
                prefix=".kj-overlay-perf-", suffix=".json")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(payload, f)
                os.replace(tmp, PERF_FILE)
            except OSError:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        except OSError:
            pass
        return True  # keep the GLib timeout alive


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
