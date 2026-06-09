# Overlay Renderer v2 — Compositor-backed GTK overlay, retire conky

**Status:** Design approved (brainstorming) — ready for implementation plan
**Date:** 2026-06-09
**Author:** Andrew + Claude
**Repo:** kjbox (`kj-controller/` + `desktop/`)

## Problem

The venue display has two rendering layers today:

1. **conky** (`desktop/rotation.conkyrc` + `desktop/rotation_data.py`) renders the
   between-songs "home" screen: branded background + ROTATION heading + live
   singer list + rules.
2. **The pygame overlay engine** (`desktop/overlay_engine.py` + `overlay_types.py`,
   run as `overlay-display.service`) renders small over-video overlays (ticker, QR,
   countdown, text).

conky cannot do real transparency — its pseudo-transparency grabs the root pixmap,
so to look right it draws a **full-screen background image** (`rotation-bg.png`)
matching the desktop wallpaper. That full-screen `own_window_type='dock'` window is
the root cause of a recurring production incident: when an overlay window steals
focus, xfwm4 demotes the fullscreen VLC video out of the top layer, and the
full-screen conky **dock** window (pseudo-transparent → paints the wallpaper) ends
up **above** the video, hiding it. Recovery today is a manual `wmctrl -i -a <VLC>`.
See `memory: project_kjbox_overlay_focus_steal_vlc`.

The pygame engine also can't do real per-pixel alpha (SDL2 limitation — it
pre-blends colours with black, see `_make_bg_color`), and its multi-window model
needs a destroy/recreate "restack" hack that caused a separate QR flicker bug
(fixed in PR #99 / v0.34.1, but the hack remains).

## Goal

Replace conky entirely with a single compositor-backed renderer that draws **all**
overlays — including the rotation list — with **real per-pixel transparency** on top
of the desktop wallpaper (between songs) or the live video (during songs), with **no
full-screen opaque window** that can ever hide the video.

The xfwm4 compositor is already enabled (`/general/use_compositing = true`) on the
NomadPC's Intel UHD (Alder Lake-N) GPU, so true ARGB transparency over live video is
achievable. GTK3 + PyGObject + pycairo are already installed on the device; pygame
uses SDL2 (no real window transparency); PyQt is not installed.

## Non-goals

- Redesigning the visual look of the rotation screen. We **preserve the current
  layout, fonts, colours, and behaviour as closely as possible** (see Preservation
  Reference). Visual redesign is a separate future effort.
- Changing the kj-controller overlay config schema, `overlays.json`, the overlay
  CRUD UI data model, or how `/tmp/rotation_cache.json` is produced. Only the
  **renderer** changes (plus a new `rotation_list` overlay type and retiring the
  push-based rotation ticker).
- Changing the static wallpaper / `rotation-bg.png` artwork.

## Chosen approach (Approach A)

A **single** always-on-top, fully-transparent (RGBA), **click-through**,
**non-focus-stealing** GTK3 window covering the monitor. All overlays are drawn into
it with Cairo at real per-pixel alpha. conky and `rotation.conkyrc` are removed.

Why one passive transparent window:

- True transparency: wherever there is no overlay content, the video/wallpaper shows
  through for real (not a wallpaper hack).
- It **cannot hide the video**: it is click-through, never takes focus, and its empty
  regions are transparent. With conky gone, the only remaining full-screen window is
  the desktop (lowest layer), so even a momentarily-demoted VLC still sits above it.
- It **eliminates a whole bug class**: no inter-overlay z-order, no restack hack, no
  flicker, no focus-steal demotion — because there is one passive window instead of
  many focus-grabbing ones.

Rejected alternatives: per-element transparent GTK windows (keeps multi-window
stacking management); staying on pygame (SDL2 cannot do real per-pixel alpha — would
not deliver the transparency goal).

## Architecture

One GTK3 process, still named `overlay_engine.py`, still run by
`overlay-display.service`. The **data/config layer is unchanged**; only the renderer
is swapped.

### The window (created once at startup)

- RGBA visual: `screen.get_rgba_visual()` + `set_app_paintable(True)` → per-pixel alpha
- `set_keep_above(True)`, `set_skip_taskbar_hint(True)`, `set_skip_pager_hint(True)`
- `set_accept_focus(False)` + `set_focus_on_map(False)` → never steals focus (this is
  what was demoting VLC)
- empty input shape: `win.input_shape_combine_region(cairo.Region())` → fully
  click-through; VLC/desktop receive all input
- sized to the monitor geometry; window type hint `NORMAL` or `UTILITY` — **not**
  `DOCK` (the layer conky abused)
- single Cairo `draw` handler that paints all enabled+visible overlays

### Modules (clear boundaries)

| Module | Responsibility | Depends on |
|---|---|---|
| `desktop/overlay_engine.py` | GTK app: window setup, config-mtime polling, `karaoke_playing` visibility, redraw scheduling, compositor safety guard, dispatch to painters | gi/Gtk/Gdk, cairo, overlay_config, overlay_painters |
| `desktop/overlay_painters.py` *(replaces `overlay_types.py`)* | `BasePainter` + `Ticker`, `StaticText`, `Image`, `Countdown`, `QRCode`, **`RotationList`**. Each: `layout()`, `draw(cr)`, `tick(dt)` for animation. Cairo drawing + PIL/qrcode for QR bitmap | cairo, PIL, qrcode, overlay_config |
| `desktop/overlay_config.py` | unchanged: schema, defaults, positions, `hex_to_rgb`; **add** `rotation_list` defaults | — |
| `desktop/rotation_source.py` *(refactor of `rotation_data.py`)* | parse `/tmp/rotation_cache.json` → structured `(queue, stats)` with per-entry fields; pagination helper; rules loader; staleness/offline. **No conky markup.** | — |
| `kj-controller/overlay.py`, `routes.py`, `static/app.js`, `templates/index.html` | add `rotation_list` to `OVERLAY_TYPES`/`TYPE_DEFAULTS`/presets + CRUD form. Retire `rotation_ticker_sync.py` (renderer reads rotation data directly for both list and "up next" ticker) | — |

`rotation_data.py` (conky markup CLI) is removed once conky is gone; its data-loading
logic moves to `rotation_source.py`.

## Data flow

1. kj-controller writes `data/overlays.json` (overlay configs + `karaoke_playing`) and
   `/tmp/rotation_cache.json` (rotation `queue` + `stats`) — **unchanged**.
2. `overlay_engine` polls `overlays.json` mtime → reload overlay set/config.
3. Rotation painters re-read `/tmp/rotation_cache.json` on its cadence; `rotation_source`
   handles parse + staleness (`CACHE_MAX_AGE`, currently 120s → "Offline").
4. `karaoke_playing` drives per-overlay visibility via existing `show_over_video`:
   `rotation_list` defaults `show_over_video=False` (between songs only); ticker/QR
   default `show_over_video=True` (over video). Hidden overlays are simply not drawn.
5. Draw: for each enabled+visible overlay, the matching painter draws into the single
   Cairo context at its configured position and alpha.

### Render cadence

GTK frame-clock / `GLib` timeout loop that is **idle when nothing animates**:
- redraw on `overlays.json` or rotation-cache change
- a 1s tick for the countdown
- ~30–60fps **only while a ticker is actively scrolling**
- rotation list re-paginates on its 10s schedule

1080p full-surface Cairo redraws at 30fps are comfortable on the Intel UHD; partial
damage regions are an optimisation, not a requirement.

## Preservation Reference (current conky behaviour to reproduce)

**Static — already in the desktop wallpaper (`/home/nomad/kjdata/wallpaper.jpg`,
same artwork as `rotation-bg.png`); NOT drawn by the renderer:** neon frame, brick,
mic graphic, "nomadkaraoke.com" (top-right), promo bubbles (find your song →
generate a video → sing anything!), centre **"Scan to sing!" QR + ribbon** (encodes a
stable URL, baked since Apr 30 — distinct from the live per-event QR overlay), bottom
"NOMAD KARAOKE — WHEREVER YOU ARE, SING!". The left area is intentionally clear for
the rotation text.

**Dynamic — `RotationList` painter must reproduce (values from `rotation_data.py`):**

- **Heading** "ROTATION" — DejaVu Sans **bold 40**, white `#ffffff`, at x≈90, top
  (≈voffset 70).
- **Stats** — same row, x≈460, DejaVu Sans size 21, `#8892a4`:
  `Started: {started}    {singers} singers | {sung} sung | {queued} queued`.
- **Singer list** — left column, left margin x≈90, song indent x≈115:
  - Singer line: `{idx}. {singer}` — number white `#ffffff`, name **gold `#ffdf6b`**,
    DejaVu Sans **bold 36**; trailing red **♥** `#e74c3c` if `entry.paid`; then a
    **status badge** (DejaVu Sans **bold 18**, leading space padding) unless status is
    empty/"waiting".
  - Song line: `{song_artist}`, DejaVu Sans size 20, `#e0e6f0`.
  - Status→badge colour map (`_status_color`): contains "singing"/"now singing" →
    `#2d8a4e` (green); contains "next" or =="waiting" → `#d4720a` (orange);
    "being made" → `#cc3333` (red/WIP); "on hold"/"brb" → `#888888`; "skipped" →
    `#3b82f6` (blue); else `#8892a4`.
- **Pagination**: `PAGE_SIZE=10`, `PAGE_DURATION=10s`. When `len(queue) > 10`, cycle
  pages by `int(time()/10) % num_pages`; numbering offset by page start; show page
  **dots** (size 16) under the list — current page white `#ffffff`, others `#8892a4`.
- **Rules panel** — right column x≈1020 (currently also `voffset -900`): "HOW IT WORKS"
  header DejaVu Sans **bold 28** white, then bullet lines DejaVu Sans size 18
  `#8892a4` from `desktop/rotation_rules.txt`.
- **Empty/offline states**: empty queue → "No singers in queue" (size 28, `#8892a4`);
  stale/missing cache → "Offline".
- Cache entry fields: `singer`, `song_artist`, `status`, `paid`. Stats fields:
  `started`, `singers`, `sung`, `queued`.

**Known discrepancy to resolve during implementation:** the rules panel is coded at
x≈1020 / `voffset -900` (top-right), which overlaps the static promo bubbles and is
**not visible** in the current production screenshot. Preserve the content; confirm
final placement with Andrew (options: reposition to a clear area, or omit if
intentionally retired).

**Existing non-rotation overlays to re-implement in Cairo (from live `overlays.json`):**
- Ticker (static + `source='rotation'`): full-width scrolling bar; `font_size`,
  `text_color`, `bg_color`, `bg_opacity`, `padding`, `speed`, `position`
  (top/bottom), `prefix`/`count`/`separator`/`empty_text` for rotation source.
- QR code: `url`, `label`, `size`, `position`, `padding` (+ optional `bg_color`,
  `bg_opacity`, `corner_radius`). The live per-event "Scan to Sing" QR follows the
  event URL (`https://sing.nomadkaraoke.com/?t=<token>`).
- Static text, Image, Countdown: per current `TYPE_DEFAULTS`.

The new renderer should now render bg/opacity with **real alpha** (no black
pre-blend), so rounded translucent panels look correct.

## Safety guards

- **Compositor guard:** at startup and periodically check `Gdk.Screen.is_composited()`
  (and/or the `_NET_WM_CM_S0` selection owner). If **not** composited, do **not** map
  the overlay window (log loudly, leave wallpaper/video visible) — a non-composited
  transparent window would render opaque and could black-screen over the video.
- **Focus/stacking:** click-through + no-focus + `keep_above` so it cannot steal focus
  or block VLC input. Verify on-device that it stacks above fullscreen VLC.
- The "Bring video to front" recovery (`wmctrl -i -a <VLC|mpv>`) remains available as
  an operator safety net regardless (separate small task; can ship first).

## Error handling

- Missing/invalid `overlays.json` → no overlays (current behaviour).
- Missing/stale rotation cache → "Offline" / empty state via `rotation_source`.
- Per-overlay draw wrapped in try/except so one failing painter cannot blank the rest;
  log the offending overlay id.
- `overlay-display.service` keeps `Restart=always`.

## Testing

- **Painter unit tests (headless):** render each painter to an offscreen Cairo
  `ImageSurface` (no X needed) and assert computed sizes, row counts, page-dot counts,
  and sampled pixel colours (e.g. gold name, badge colour).
- **`rotation_source` tests:** parse representative `rotation_cache.json` (incl. paid,
  each status, >10 entries paging, empty, stale) → structured data + pagination.
- **Reuse** `overlay_config` tests (defaults/validation/positions); add `rotation_list`
  defaults.
- **`--render-png` / `--demo` mode:** dump a composite or per-overlay PNG for visual
  review and visual-regression diffing.
- **kj-controller-side:** add `rotation_list` to CRUD + presets tests; remove
  `rotation_ticker_sync` tests; keep overlay route/host-guard tests.
- **Manual on-device (no live show):** stacking above fullscreen VLC, click-through,
  transparency, compositor-off guard.

## Rollout (live hardware — manual deploy; auto-deploy is OFF)

1. Build the GTK renderer + `rotation_source` + `rotation_list` painter alongside the
   existing engine; validate via `--render-png` and unit tests.
2. On-device dry run during a quiet window: run the new engine manually, verify
   stacking/click-through/transparency and the compositor guard.
3. Swap `overlay-display.service` `ExecStart` to the GTK engine; disable the conky
   autostart (`~/.config/autostart/Conky.desktop`) and stop conky; remove
   `rotation.conkyrc` from the launch. Keep conky files in git history for rollback.
4. Update `desktop/overlay-display.service` env if needed (GTK uses X11; drop SDL env).
5. Deploy: `ssh nomadpctunnel 'cd /opt/nomad/kjbox && git pull --ff-only origin main'`
   then restart `overlay-display` (overlay blink only; does **not** interrupt
   playback). Confirm `karaoke_playing=false` / no live show first.
6. Update docs: `docs/CHANGELOG.md` entry; refresh any overlay/conky references in
   `docs/ARCHITECTURE.md`.

## Open questions for implementation plan

1. Rules panel final placement (see discrepancy above).
2. Confirm retiring `rotation_ticker_sync.py` end-to-end (renderer reads rotation cache
   directly for the `source='rotation'` ticker).
3. Whether to ship the "Bring video to front" System-section button as a small
   precursor PR (independent value, lower risk).
