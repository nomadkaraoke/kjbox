# Implementation plan: Remaining A — ticker off video into reserved top strip

**Date:** 2026-07-07
**Spec:** [`docs/superpowers/specs/2026-07-07-perf-layout-fix-handoff.md`](../superpowers/specs/2026-07-07-perf-layout-fix-handoff.md) § 4
**Goal:** Remove the over-video overlay compositing cost that drops 4K frames on NomadPC,
by (a) reserving a top strip for the scrolling ticker and shrinking the video below it, and
(b) making the overlay engine damage only each animated overlay's own bounding box.

## Two-process architecture (why coordination is subtle)

- **kj-controller** (Flask; `kj-controller/config.py` → `config.json`): owns `mpv_manager.py`
  and `vlc.py`. These size the *video window*.
- **overlay engine** (separate systemd `overlay-display`; `desktop/`): reads `data/overlays.json`
  (polled by mtime) + `/tmp/rotation_cache.json`. Owns the *transparent overlay window*.
  Hardcodes `SCREEN_WIDTH=1920`/`SCREEN_HEIGHT=1080` in `overlay_config.py`.

## Key design decision — NO cross-process margin value

> **Superseded (v0.74.1):** this was the pre-implementation stance. It shipped as v0.74.0 but
> left a visible wallpaper gap between the natural-height ticker and the video. v0.74.1 now
> *does* share the strip height cross-process (persisted in `overlays.json`, not runtime IPC) so
> the top ticker fills the strip. See the v0.74.1 CHANGELOG entry and ARCHITECTURE.md. The
> per-overlay damage below is unchanged and remains margin-agnostic.

The perf-critical engine change (per-overlay damage) is **margin-agnostic**. The ticker sits at
`position:'top'` (existing config) with a bar height (~font+2·padding ≈ 48px) that fits inside the
80px reserved strip. Because the video window is lowered to `y=margin` (=80), the ticker's damage
rect `(0,0,W,~48)` never overlaps the video window `(0,80,W,H-80)` → the compositor recomposites
only the strip (over static wallpaper), and the video window page-flips freely.

So we do **not** pipe `video_top_margin_px` into the overlay engine. Convention only:
`margin ≥ ticker_bar_height`. Documented; both are ~80px static device config. (Tighter coupling
is an easy follow-up if drift ever bites.)

## Build steps

1. **`kj-controller/config.py`** — defaults: `video_top_margin_px: 80`, `screen_width: 1920`,
   `screen_height: 1080`. `margin=0` → old fullscreen behaviour (clean rollback).

2. **`kj-controller/mpv_manager.py`** — extract `_video_window_args(margin, w, h)`:
   - `margin>0` → `['--no-border', f'--geometry={w}x{h-margin}+0+{margin}']`
   - else → `['--fs']`
   Splice into BOTH launch variants (pulse + alsa) in place of `--fs`. Unit-test the helper +
   that `launch()` emits the right args for margin 0 and 80.

3. **`desktop/overlay_painters.py`** — add `BasePainter.bbox() -> (x,y,w,h)` (uses `_x/_y/_w/_h`).
   Ticker at `position:'top'` already yields `(0,0,1920,h)`. Unit-test bbox for ticker/qr/countdown.

4. **`desktop/overlay_engine.py`** — replace whole-window `queue_draw()` in `_on_frame` with a
   per-overlay `queue_draw_area(*bbox)` for each painter whose `tick()` returned True. Extract a
   pure `_collect_dirty(dt) -> list[bbox]` method (testable with real painters, no GTK), mirroring
   how `_apply_click_through` is unit-tested via a `SimpleNamespace` fake. Fall back to full
   `queue_draw()` if a painter has no usable bbox. `_on_draw` is unchanged (Cairo clips paint+draws
   to the damaged region automatically).

5. **`kj-controller/vlc.py`** — `margin>0` → drop `--fullscreen`, reposition after launch with
   `wmctrl -r <win> -e 0,0,margin,w,h-margin` + `-b remove,fullscreen`. **FIDDLY — validate on
   device first** (VLC window title, and the filler VLC is a second "VLC media player" window →
   ambiguous `wmctrl -r`). Documented fallback: margin affects mpv only, VLC stays fullscreen.
   mpv is the default engine and the only viable 4K engine, so this is lower-stakes.

6. **Measure before/after** with `/perf/record/*` on a 4K file, per engine, ticker on:
   compare `render_fps`, real drops, mpv CPU, GPU busy. Deploy only with Andrew's permission
   (live production box — see CLAUDE.md).

## Test plan
- `config.py`: default present.
- `mpv_manager.py`: `_video_window_args` (both branches) + `launch()` arg assertions (margin 0/80).
- `overlay_painters.py`: `bbox()` for ticker(top)/qr/countdown.
- `overlay_engine.py`: `_collect_dirty` returns the ticking painters' bboxes; empty when nothing ticks.
- `vlc.py`: `--fullscreen` present at margin 0, absent at margin>0 (wmctrl geometry = device-validated).
