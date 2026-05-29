# Overlays: Rotation Ticker + Scan-to-Sing QR — Design

**Date:** 2026-05-28
**Status:** Approved (brainstorm) — moving to implementation plan
**Worktree:** `kjbox-overlays-ticker-qr` · branch `feat/sess-20260522-0515-overlays-ticker-qr`

## Goal

Two improvements to the existing overlay engine, both targeted at making live shows feel more polished without adding a new rendering subsystem:

1. **Rotation ticker** — a configurable ticker bar that always shows the next N singers in the rotation (current performer plus the queue), with an optional prefix, suitable for placing across the top of the screen on top of video playback.
2. **Scan-to-Sing QR** — a one-click preset that places a small QR code in a top corner over video and ticker, auto-syncing to the current event URL. Includes the small QR-rendering improvements needed for it to look good on top of other overlays (semi-transparent bg, rounded corners) and a deterministic Z-order fix so the QR sits above the ticker.

## Non-goals

- New overlay types (we reuse existing `ticker` and `qr_code`).
- New transport layer between Flask and the engine (we keep mtime-polling `overlays.json`).
- Smooth cross-fade when ticker text changes — first version accepts a scroll reset.
- Translation / i18n of overlay text — same scope as the rest of kjbox today.

## Architecture overview

```
┌──────────────────────────────────────────────────────────────────┐
│  Flask process (kj-controller)                                   │
│                                                                  │
│  RotationManager ──_after_mutation()──┐                          │
│           │                           ├─ push_dispatcher         │
│           │                           ├─ _write_display_cache    │
│           │                           └─ rotation_ticker_sync    │
│           │                                       │              │
│           ▼                                       ▼              │
│  /tmp/rotation_cache.json          OverlayManager.update_overlay │
│  (existing, unchanged)                            │              │
│                                                   ▼              │
│                                          data/overlays.json      │
└──────────────────────────────────────────────────────────────────┘
                              ↕  (mtime poll, ~1s)
┌──────────────────────────────────────────────────────────────────┐
│  overlay_engine.py (separate process, systemd)                   │
│                                                                  │
│  TickerOverlay  — renders config.text verbatim (unchanged)       │
│  QRCodeOverlay  — semi-transparent bg, rounded card, restacked   │
│                   above ticker after creation                    │
└──────────────────────────────────────────────────────────────────┘
```

**Design principle:** the engine remains a "dumb renderer" that reads `config.text` and draws it. All composition logic stays in the backend, in a pure, well-tested function. This keeps the engine — the riskiest, hardest-to-debug component on the device — unchanged for the ticker work.

## Data model changes

### Ticker overlay — new `config` fields

All new fields are optional with defaults that match today's behaviour, so existing tickers are untouched.

| Field | Type | Default | Used when | Meaning |
|---|---|---|---|---|
| `source` | string | `"static"` | always | `"static"` (current behaviour) \| `"rotation"` |
| `prefix` | string | `"Up next: "` | `source=="rotation"` | Text prepended once before the dynamic list |
| `count` | int | `5` | `source=="rotation"` | Max singers to include |
| `separator` | string | `"   "` | `source=="rotation"` | Inserted between numbered slots |
| `empty_text` | string | `"Sign up at the booth!"` | `source=="rotation"` | Shown after `prefix` when rotation is empty |

`text` is still the field the engine renders. When `source=="rotation"`, the backend overwrites `text` on every rotation change. The form hides the manual textarea and renders a read-only preview of the composed text instead.

### QR overlay — new visual `config` fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `bg_opacity` | float `[0, 1]` | `1.0` (today's behaviour: opaque) | Lets the QR card become semi-transparent so video shows through padding |
| `corner_radius` | int (px) | `0` (today's behaviour: square) | Rounds the QR card. `0` skips the alpha mask entirely. |

The QR code itself (the black/white modules) must remain fully opaque and high-contrast for scanners — `bg_opacity` only applies to the padding area around the code.

### Scan-to-Sing preset

A named preset, not a new overlay type. Backend constant:

```python
OVERLAY_PRESETS = {
    "scan-to-sing": {
        "type": "qr_code",
        "name": "Scan to Sing",
        "enabled": True,
        "show_over_video": True,
        "config": {
            "url": "",                     # filled by sync_event_url_overlays after creation
            "follow_event_url": True,
            "label": "Scan to sing",
            "size": 110,
            "position": "top-right",
            "padding": 8,
            "bg_color": "#000000",
            "bg_opacity": 0.85,
            "corner_radius": 12,
        },
    },
}
```

## Components

### 1. `kj-controller/rotation_ticker_sync.py` (new, ~80 LOC)

```python
def compose_ticker_text(
    entries: list[dict],   # rotation entries from RotationStore.get_entries()
    prefix: str,
    count: int,
    separator: str,
    empty_text: str,
) -> str:
    """Pure: produces the rendered ticker string."""
```

Algorithm:
1. Filter `entries` to exclude statuses `done` / `left` (rotation_store already does this in `get_entries()` by default; rely on caller).
2. Take the first `count` entries in rotation order.
3. If the filtered list is empty → return `f"{prefix}{empty_text}"`.
4. Else build numbered slots `f"{i}. {singer}"` for `i, singer` in `enumerate(slice, 1)`, joined by `separator`.
5. Return `f"{prefix}{joined}"`.

Notes:
- The first slot ends up being whoever's at the top of rotation — which by convention is the `Now Singing` entry if there is one (the rotation store orders by position, and "now singing" gets moved/stays at the top of the queue when the play route fires). The compose function itself doesn't special-case `Now Singing`; the rotation order is the source of truth.
- Names are not truncated. The KJ controls singer count; the ticker scrolls so length isn't a hard constraint.

```python
class RotationTickerSync:
    def __init__(self, overlay_manager, rotation_store): ...

    def refresh(self) -> int:
        """Recompose text for every overlay where type==ticker AND config.source==rotation.

        Updates the overlay only when the text actually changed (avoid spurious
        file writes that would re-trigger the engine reload every mutation).
        Returns the number of overlays updated. Best-effort: never raises.
        """
```

### 2. `kj-controller/rotation.py` — wire the hook

- Add `rotation_ticker_sync=None` constructor arg, store on `self`.
- Inside `_after_mutation()`, after the push dispatcher block, call `self.rotation_ticker_sync.refresh()` inside a `try/except` that logs and swallows (same pattern as push).

### 3. `kj-controller/overlay.py`

- Add module-level `OVERLAY_PRESETS` dict (as in Data model § Scan-to-Sing preset).
- Add `OverlayManager.create_preset(preset_name)` → looks up template, deep-copies, runs through `create_overlay()` to assign id, returns the created overlay. Raises `ValueError` on unknown preset.

### 4. `kj-controller/routes.py`

- `POST /overlays/presets/<name>` → `create_preset(name)`. After creation, if the preset is `scan-to-sing`, call `sync_event_url_overlays(overlay_manager, current_event_url)` so `url` is populated immediately. Returns the created overlay.
- In existing `POST /overlays` and `PUT /overlays/<id>`: if the resulting overlay has `type=="ticker"` and `config.source=="rotation"`, call `rotation_ticker_sync.refresh()` so the new/edited ticker is populated on the first save without waiting for the next rotation mutation.

### 5. `kj-controller/app.py`

- After `OverlayManager` and `RotationManager` are constructed, instantiate `RotationTickerSync(overlay_manager, rotation_manager.store)` and pass it into `RotationManager` (similar to how `push_dispatcher` is wired today).
- Call `rotation_ticker_sync.refresh()` once during app startup so any rotation tickers already configured come up populated.

### 6. `desktop/overlay_config.py` — defaults

`apply_defaults(overlay)` learns the new fields:

```python
TICKER_DEFAULTS = {
    "source": "static",
    "prefix": "Up next: ",
    "count": 5,
    "separator": "   ",
    "empty_text": "Sign up at the booth!",
    # …existing ticker defaults…
}
QR_DEFAULTS = {
    "bg_opacity": 1.0,
    "corner_radius": 0,
    # …existing QR defaults…
}
```

Existing overlays without `source` resolve to `"static"` and render exactly as before.

### 7. `desktop/overlay_types.py` — QR visual + Z-order

`QRCodeOverlay._setup()`:
- Pre-multiply `bg_color` with `bg_opacity` via the existing `_make_bg_color` helper (already used by the static text and countdown overlays).
- If `corner_radius > 0`: build a rounded-rect alpha mask the size of the window (PIL `ImageDraw.rounded_rectangle`), and in `render()` blit the QR onto a transparent canvas, then onto the masked card.

`QRCodeOverlay.render()`:
- Fill background with the pre-multiplied colour (or use the rounded card when `corner_radius > 0`).
- Blit QR module image (unchanged) and label (unchanged).

**Z-order fix** in `OverlayEngine._reload_config()`:

After the create/update pass, run a single restack step that destroys-and-recreates QR overlay windows so they are mapped last:

```python
def _restack_qr_above_ticker(self):
    qr_ids = [oid for oid, ov in self.overlays.items() if isinstance(ov, QRCodeOverlay) and ov.visible]
    if not qr_ids:
        return
    # Only restack if at least one ticker is currently visible (else no overlap to worry about)
    has_visible_ticker = any(isinstance(ov, TickerOverlay) and ov.visible for ov in self.overlays.values())
    if not has_visible_ticker:
        return
    for oid in qr_ids:
        ov = self.overlays[oid]
        ov.destroy_window()
        ov.create_window()
        ov.render()
```

Called at the end of `_reload_config()` and inside `update_visibility()` whenever a ticker just became visible. Re-creating a window last forces the X11 server to map it on top of any earlier-mapped always-on-top windows; this matches how the existing engine already handles `show()/hide()` via `destroy_window`/`create_window`. The guard avoids unnecessary work in the common case where no ticker is on screen.

### 8. Controller UI

`kj-controller/templates/index.html`:
- Overlay modal: add `<select id="overlay-source">` with options `static` / `rotation`, within the ticker conditional block (`data-types="ticker"`).
- Add four conditional rows for the rotation fields (`data-source="rotation"`).
- Existing `data-types`-based show/hide logic extends to use a combined `data-types`+`data-source` predicate.
- Overlay panel header: new button **Scan to Sing** beside `+ Add` that calls `addScanToSingQR()`.

`kj-controller/static/app.js`:
- `addScanToSingQR()` → `POST /overlays/presets/scan-to-sing`, then refreshes the overlay list and surfaces a toast.
- `onOverlayTypeChange()` (existing in `app.js`): extend to consider the new ticker `Source` select. Either change it to filter rows by `data-types` AND `data-source` together, or split into a second `onOverlayTickerSourceChange()` that runs after the type filter. The rotation-only rows carry `data-source="rotation"` and only become visible when `type=="ticker"` AND the source select is `rotation`. When a ticker overlay opens with `source=="rotation"`, the position default flips from `bottom` to `top` (override the existing default).
- When editing a `ticker` with `source=="rotation"`, hide the textarea for `text` and instead render a small read-only preview "Preview: 1. Alice   2. Bob   …" populated from the existing overlay's current `config.text` (no live recomputation in JS — the backend stays the single source of truth).

## Data flow examples

**KJ marks a singer as "Now Singing":**
1. Route handler in `routes.py` calls `RotationManager.update_status(id, "Now Singing")`.
2. `update_status` mutates SQLite, fires `_after_mutation`.
3. `_after_mutation` calls `_write_display_cache` (unchanged), `push_dispatcher.notify_rotation_changed` (unchanged), then `rotation_ticker_sync.refresh()` (new).
4. `RotationTickerSync.refresh` iterates `OverlayManager.list_overlays()`, picks rotation tickers, composes new text via `compose_ticker_text`, and `update_overlay`s any whose text changed.
5. `update_overlay` saves `overlays.json` atomically.
6. Within ~1 s, `OverlayEngine.check_config` sees the mtime change, calls `_reload_config`, ticker's `update_config` detects a config diff, tears down its surface, and re-renders with the new text.

**KJ clicks "Scan to Sing":**
1. JS posts to `/overlays/presets/scan-to-sing`.
2. Backend creates the QR overlay with `follow_event_url=True`, then calls `sync_event_url_overlays(overlay_manager, current_event_url)` to fill `url`.
3. JSON file is written; engine picks up the new overlay within ~1 s; QR window is created and (because of the Z-order pass) raised above any existing ticker windows.

## Error handling

- All hooks into `_after_mutation` swallow exceptions and log via `logging.getLogger(__name__).exception(...)`. The KJ controller UI must never break because of a ticker bug.
- `RotationTickerSync.refresh()` returns 0 on any internal error.
- `create_preset()` raises `ValueError` only for unknown preset names; routes translate to a 400.
- Engine: malformed `bg_opacity` or `corner_radius` values fall back to defaults via the same `try/except` discipline used elsewhere in `overlay_types.py`.

## Testing

### Unit tests

- `tests/unit/test_compose_ticker_text.py`
  - 0 entries → returns `f"{prefix}{empty_text}"`.
  - 1, N-1, N, N+1 entries → correct slot count and numbering.
  - Custom prefix/separator.
  - Singer name with awkward characters (em-dash, emoji) passes through.

- `tests/unit/test_overlay_presets.py`
  - `create_preset("scan-to-sing")` produces an overlay with the documented defaults and a generated id.
  - Unknown preset raises `ValueError`.

### Integration tests

- `tests/integration/test_rotation_ticker_hook.py`
  - Construct app with `OverlayManager` and `RotationManager`. Add a rotation ticker. Mutate the rotation. Assert `overlay_manager.get_overlay(id)["config"]["text"]` matches what `compose_ticker_text` would produce.
  - When the rotation doesn't change the visible slice (e.g. mutation of an entry beyond position `count`), `update_overlay` is **not** called (idempotency: no file write).

- `tests/integration/test_overlay_presets_route.py`
  - `POST /overlays/presets/scan-to-sing` returns 201 with the preset overlay; subsequent `GET /overlays` shows it; `config.url` is populated from the current event URL.
  - `POST /overlays/presets/unknown` returns 400.

### Engine — visual smoke

Not unit-testable (pygame + X11). Manual checks documented in the design doc and the PR:
- Demo overlays config with one rotation ticker (top) + Scan-to-Sing QR (top-right). Run `python3 overlay_engine.py --demo` variant.
- Verify QR sits on top of the ticker at the overlap point.
- Toggle `corner_radius` between 0 and 12 to confirm the rounded card renders.
- Set `bg_opacity` to 0.5 and confirm the desktop behind the padding is faintly visible (within the limits of pygame's whole-window alpha; per-pixel alpha is approximated by pre-multiplication, which is acceptable for a black/dark padding).

## Out of scope / follow-ups

- Smooth ticker text transitions (cross-fade or queue swap at end of scroll).
- Showing song titles alongside singer names — easy follow-up: extend `compose_ticker_text` with a `format` template string.
- Multi-line / two-row ticker.
- Auto-creating a rotation ticker on first run / via a "Default overlays" preset bundle.
- Per-pixel alpha for QR (would require a different windowing strategy — pygame-ce windows can't host an alpha channel on the system surface).

## Open questions

None at design time. Anything that comes up during implementation (e.g. the precise hook function name in `app.js`) gets resolved with a code read, not a rewrite of the spec.
