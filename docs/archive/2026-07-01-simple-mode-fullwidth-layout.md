# Simple Mode — Full-Width "Run the Show" Layout

**Date:** 2026-07-01
**Branch:** `feat/sess-20260701-2251-simple-mode-kj-ux`
**Status:** Layout implemented (CSS-only), verified live on NomadPC via tunnel. Awaiting review before deploy. Optional polish deferred (see below).

## Motivation (Andrew's brief)

> "Review the 'simple mode' functionality and web UI layout… refine the UX/UI for the KJ controller view (use full window width, there's currently a pointless empty space to the side when in simple mode) and make sure it's polished and genuinely easy to use for a newbie KJ."

## Root cause of the "pointless empty space"

The advanced layout is a CSS grid on `.main-layout` with `grid-template-columns: 2fr 1fr`
(`#col1` | `#col2`). Simple Mode hides `#col2` with `display: none`, **but the grid's
second (`1fr`) track stays reserved and empty** — that leftover track is the dead space on
the right (~1/3 of the window).

The prior simple-mode CSS made it worse by then capping `#col1` at `max-width: 720px` and
centering it, so on a wide screen the working UI was a narrow centered column with void on
*both* sides, and rotation song titles collapsed to ~13px (`text-overflow: ellipsis`) because
the crushed column left no room.

## Solution — command-center layout

Collapse `.main-layout` to a single column in simple mode so `#col1` gets the **full width**,
then re-organise `#col1`'s own panels into a run-the-show command center:

```
┌────────────────────────────────────────────────┐
│  Playback Controls (full-width horizontal bar)  │
├─────────────────────────────┬──────────────────┤
│  Rotation (hero, ~2fr)      │  Screen Preview   │
│                             ├──────────────────┤
│                             │  System · Mode    │
└─────────────────────────────┴──────────────────┘
```

- `.main-layout { grid-template-columns: 1fr }` → `#col1` fills the window (kills the void).
- `#col1` becomes a grid: `minmax(0,2fr) minmax(0,1fr)` with named areas
  `playback playback / rotation preview / rotation system`.
- Playback Controls become a compact horizontal top bar (buttons + seek on row 1, the two
  volume sliders on row 2).
- Rotation is the wide hero on the left → song titles now render in full (measured 151px vs
  the old 2px), no per-row hack needed.
- Screen Preview + System (Mode toggle) sit in a slim right rail.

**Scoped to `@media (min-width: 769px)`** so the existing mobile stack
(`#col1 { display: contents }` at ≤768px) is completely untouched — simple mode just stacks
vertically on phones/small tablets, exactly as before.

### Files touched

- `kj-controller/static/style.css` — replaced the ~8-line simple-mode layout block
  (`.main-layout { justify-content: center }` + `#col1 { max-width: 720px }`) with the
  command-center grid described above; removed the now-unused `.simple-mode-banner` rule.
- `kj-controller/static/app.js` — `applySimpleMode()` no longer renders the "Simple Mode is
  ON …" guidance banner. In the new full-width layout the System → "Simple Mode (for stand-in
  KJ)" toggle is a prominent, always-visible on-indicator, so the banner was redundant clutter
  across the top of the rotation hero. The function now just toggles `body.simple-mode` and
  keeps the switch in sync with `/status`.

No backend/template changes.

## Verification

- Verified live on NomadPC (via `nomadpctunnel` → local Flask :5001) by injecting the exact
  final rules (no `!important`, real cascade) with Simple Mode toggled on. Screenshots in
  `screenshots/`:
  - `02-current-simple.png` — before (void + truncated titles)
  - `06-final-B-fullwidth.png` / `09-implemented-verify.png` — after (full width, full titles)
  - `07-final-B-1120.png` — holds at laptop width
  - `08-final-B-mobile760.png` — mobile stack untouched (<769px)
- CSS brace balance validated (965/965).
- Device restored to `simple_mode = false` after testing (baseline as found).

## Deferred (needs Andrew's call — functionality-removing, not shipped)

Held back deliberately: these remove or hide features on a production device, so they want a
yes/no rather than a best-guess.

1. **Trim per-row rotation buttons** in simple mode (hide `…` more-menu / `✎` edit / `✉ SMS`)
   for a calmer newbie row. Counter-point: SMS ("you're up next") and edit (fix a name typo)
   are arguably useful even for a stand-in. Options: trim all three / trim only `…` / leave.
2. **Hide singer power-user actions** (`Merge` / `Split`). These share a generic
   `.singer-stats-btn` class, so hiding just those two needs a markup change (add classes or
   data-attrs), not CSS alone.
3. **Screen Preview newbie state** — currently shows "Disconnected" + a `VNC password` box,
   which is jargon for a stand-in. Could auto-connect or show a friendlier placeholder; a
   slightly deeper change (needs to source/store the VNC password).

## Deploy notes

- Frontend-only (CSS). Auto-deploy `git pull`s but does **not** restart the service; change
  takes effect on next browser refresh — **no playback interruption**. `git push` still needs
  Andrew's explicit permission (auto-deploy trigger).
- Bump `pyproject.toml` version in the same PR so `app.js?v=` cache-busts (per kjbox
  frontend-deploy convention).
