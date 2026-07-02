# Requests → dedicated right-rail section (design)

**Date:** 2026-07-02
**Repo:** kjbox (`kj-controller`)
**Status:** Approved, ready for implementation plan
**Worktree:** `kjbox-requests-section` / `feat/sess-20260702-1705-requests-section`

## Problem

Singer requests (from the public web/QR request form) currently render into
`#pending-requests-panel`, which is nested **inside** the `.rotation-panel`
container — between the Rotation header and the add-form. When a request
arrives (or the pending list grows), the rotation container inflates and the
**Rotation list shifts down under the KJ's cursor**. Mid-show, that can cause a
dangerous mis-click on a rotation control (Done/Next/Delete) right as a singer
submits.

This is especially acute in **Simple Mode**, where the design intent is that
*all* singer requests come through the web UI — so requests are the primary
inbound event, and the jank is constant.

## Goal

Move requests out of the Rotation container into a **permanent, dedicated
Requests section in the right rail**, so:

1. The Rotation section's height is decoupled from request activity and **never
   moves** when a request arrives.
2. Requests are a first-class, always-visible surface (with an on/off status
   indicator), not a panel that only appears when something is pending.
3. Multiple requests **queue** in the section for the KJ to action in order,
   without the section itself pushing surrounding content around.

## Current layout (relevant facts)

- `.main-layout` is a CSS grid: `grid-template-columns: 2fr 1fr`, `align-items:
  start`. The two grid items are `#col1` (left, 2fr) and `#col2` (right, 1fr),
  each a `.column` flex column stacking `.container` cards. Because of
  `align-items: start`, the two columns size **independently** — growth in one
  never moves the other.
- **Advanced mode**
  - `#col1`: Playback Controls, Rotation, Overlays, Screen Preview
    (`#vnc-preview-container`, preceded by the floating `#vnc-max-toolbar`),
    System.
  - `#col2`: Search Karaoke Nerds, Search YouTube, Search Divebar, Upload/
    Download, Available Songs, …
- **Simple mode** (`body.simple-mode`, shipped in #132/#134): `#col2` is hidden;
  `@media (min-width: 769px)` collapses `.main-layout` to a single `1fr` column
  and re-grids `#col1` into `grid-template-areas: "playback preview" / "rotation
  preview"`, carving Screen Preview out as a right rail. This special-case
  exists **only because** Screen Preview lived in `#col1` while `#col2` was
  entirely advanced-only sections that hide in simple mode (leaving dead space).
- **Requests trigger today:** a `Requests` button in the Rotation header
  (`.rotation-requests-btn`) with a `#pending-count-badge`, opening
  `openSingRequestsModal()` (the settings modal: enable toggle, auto-approve,
  accept-make, QR codes, token, SMS).
- **`SingRequests` module** (`app.js`): polls `/rotation/requests?status=pending`
  every 5s → `renderPanel()` (hides the panel when 0 pending, else lists rows) +
  `updateBadge()`. `fetchConfig()` reads `/rotation/requests/config`
  (`config.enabled` = public form on/off) → `applyConfigToModal()` +
  `updateBadge()`. Row rendering (approve / edit / reject / KJ-pick / YouTube
  preview / duet partners) is unchanged by this work.
- **Dot convention:** health dots use base `.yt-health-dot` (grey `#444`) plus a
  state modifier — `.yt-dot-ok` (green `#22c55e`), `.yt-dot-warn` (amber),
  `.yt-dot-error` (red). (A prior bug used non-existent `green/yellow/red`
  classes → the dot never coloured; we reuse the real class names here.)
- **Mobile (`≤768px`):** `.main-layout` becomes a flex column, `#col1`/`#col2`
  use `display: contents`, and each section's stack position is set by an
  explicit `order:` value — so stacking is **independent of DOM parent**.

## Approach: unify `#col2` as the right rail in both modes

Chosen over the alternative of runtime-relocating the Requests node on every
mode switch (more moving parts) and over duplicating the section per mode
(divergence risk). Moving Screen Preview into `#col2` lets a **single static DOM
placement** be correct in both modes, and lets us **delete** the Simple-mode
grid special-case.

### DOM changes (`templates/index.html`)

1. Move `#vnc-max-toolbar` + `#vnc-preview-container` (Screen Preview) to be the
   **first children of `#col2`**, before `.kn-search-section`.
2. Insert the new **Requests** section immediately after Screen Preview (still
   top of `#col2`, second). Resulting `#col2` order in both modes:
   **Screen Preview → Requests → Search Karaoke Nerds → YouTube → Divebar → …**
3. Remove the old `Requests` button + `#pending-count-badge` from the Rotation
   header, and remove the inline `#pending-requests-panel` from inside
   `.rotation-panel`.

New Requests section markup (shape):

```html
<div class="container requests-panel" id="requests-panel">
  <div class="header-row">
    <h2>Requests <span id="requests-status-dot" class="yt-health-dot" title="Public request form status"></span></h2>
    <div class="header-actions">
      <span id="pending-requests-count" class="pending-requests-count">0</span>
      <button onclick="openSingRequestsModal()" title="Request form settings — QR code, kill switch, SMS">Settings</button>
    </div>
  </div>
  <div id="pending-requests-list" class="pending-requests-list"></div>
  <div id="pending-requests-empty" class="pending-requests-empty">No pending requests — singers submit via the QR code.</div>
</div>
```

- Header buttons use the `.header-actions` framework (a classless button
  auto-sizes correctly — do not add bespoke size CSS).
- The status dot sits in the `<h2>` (like `Search Divebar Karaoke`'s
  `#db-health-dot`), NOT as an action button.

### CSS changes (`static/style.css`)

1. **Delete the Simple-mode grid special-case** (the `@media (min-width:769px)`
   block): `body.simple-mode .main-layout { grid-template-columns: 1fr }`,
   `body.simple-mode #col1 { display:grid; grid-template-areas: "playback
   preview" / "rotation preview"; … }`, and the three `grid-area` assignments
   (`.rotation-panel`, `#vnc-preview-container`, `.playback-controls`). With
   Screen Preview now in `#col2`, both modes use the same `2fr 1fr` layout.
   - Keep the mode-independent compact playback bar (`.playback-controls {
     display:grid … }` at `@media (min-width:769px)`) — it is not part of the
     special-case and applies in both modes.
   - `#col2` stays in the `body.simple-mode … { display:none }` hide list?
     **No** — remove `#col2` from that list (we need it visible in simple mode).
     Instead the *individual* advanced-only col2 sections
     (`.kn-search-section`, `.yt-search-section`, `.db-search-section`,
     `.download-section`, `.available-songs`, `.browser-mode-panel`) remain
     hidden in simple mode, which they already are. Net: in simple mode `#col2`
     shows only Screen Preview + Requests.
2. **Requests panel styles:** `#pending-requests-list` capped height (~3–4 rows)
   with `overflow-y: auto` and the existing thin-scrollbar treatment; empty-state
   text muted; count badge styling reused from `.pending-requests-count`.
3. **Status dot:** no new colours — reuse `.yt-health-dot` + `.yt-dot-ok`.
4. **Mobile order:** add an `order:` for `.requests-panel` slotted just after
   `.rotation-panel` (i.e. between order 2 and 3; renumber the tail as needed) so
   a KJ on a phone sees pending requests early. Screen Preview keeps its existing
   `order: 10` (unchanged by the DOM move, since order is explicit).

### Module changes (`static/app.js`, `SingRequests`)

- `renderPanel()`: the section is **permanent** — stop toggling `.hidden` by
  pending count. Always render the header/count/dot; show the rows list when
  `pending.length > 0` and the empty-state element otherwise; update the count.
- New `updateStatusDot()`: set `#requests-status-dot` class to `.yt-dot-ok` when
  `config.enabled`, else base grey (remove the modifier). Call from
  `fetchConfig()`.
- `updateBadge()`: retarget to `#pending-requests-count` (the section's count).
  The old `#pending-count-badge` is gone.
- Poll cadence (5s), row rendering, approve/reject/edit, and the settings modal
  are unchanged.

## Behaviour summary

| Event | Before | After |
|---|---|---|
| Request arrives | Rotation container grows → **Rotation list shifts** | Requests section (right rail) grows within its cap; **Rotation unchanged** |
| Many requests queue | Panel grows unbounded inside Rotation | List caps at ~3–4 rows + scrolls; nothing below shifts; count shows true total |
| Zero pending | Panel hidden | Section visible with empty-state + dot |
| Public form on/off | (only visible inside modal) | Green/grey dot on the section header |
| Open settings | `Requests` button in Rotation header | `Settings` button in Requests header (same modal) |

## Testing

Local **Flask + Playwright mock harness** (the established kjbox pattern:
renders the real `index.html` template + real `static/`, stubs
`/rotation/requests`, `/rotation/requests/config`, `/status`). Assertions:

1. **Rotation invariance (the core guarantee):** measure `.rotation-panel`
   bounding box; inject a pending request via the stubbed endpoint; assert the
   box is **unchanged**. Advanced and simple mode.
2. **Placement:** Requests panel is inside `#col2`, positioned after Screen
   Preview and before Search Karaoke Nerds, in both modes.
3. **Simple-mode layout:** with `body.simple-mode`, left column = Playback +
   Rotation, right rail = Screen Preview + Requests; advanced-only col2 sections
   hidden; no dead space (reproduces the shipped Simple look + Requests below
   preview).
4. **Dot:** `config.enabled: true` → `#requests-status-dot` has `.yt-dot-ok`
   (computed green); `false` → base grey.
5. **Queue cap:** inject > cap requests; list scrolls; count = true total;
   surrounding sections do not move.
6. **Settings:** the header `Settings` button opens the sing-requests modal.

kjbox has **no pytest CI** (security workflow only) — tests are local-only.
Bump `pyproject.toml` version for cache-bust (`app.js?v=` / `style.css?v=` read
`app_version` at startup). Frontend-only change → deploy = `git pull` + a running
process still serves the old `?v=` until restart/hard-refresh.

## Out of scope

- No changes to request row rendering, approve/reject/edit flows, the settings
  modal contents, SMS, QR, or any backend endpoint.
- No change to what "enabled" means (public form kill switch) — only surfacing it
  as a dot.
- Broader Simple-mode redesign beyond the Screen-Preview relocation.

## Risks / watch-items

- **Simple-mode regression:** removing the `#132` grid special-case must
  reproduce the shipped Simple visual (Playback+Rotation left, Preview right).
  Covered by test #3; verify against `docs/archive/2026-07-01-simple-mode-
  fullwidth-layout.md`.
- **Advanced-mode visible change:** Screen Preview moves from left-bottom to
  right-top of the layout. Endorsed by the user (collapsible via its Hide
  control).
- **`#vnc-max-toolbar` / Max mode:** the floating size toolbar and the "Max"
  preview mode must still function from `#col2`; include a manual check.
- **Mobile order renumbering:** inserting `.requests-panel` into the explicit
  `order:` sequence — renumber carefully so nothing collides.
