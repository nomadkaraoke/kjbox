# Section-Header Action-Button Framework — Design

**Date:** 2026-07-02
**Repo:** kjbox / `kj-controller` (Flask + vanilla JS, no build step)
**Files:** `templates/index.html`, `static/style.css`, `docs/DEVELOPMENT.md`, `pyproject.toml`, optionally `.githooks/pre-commit`
**Status:** Design approved (mechanism + size); awaiting spec review before writing the implementation plan.

---

## 1. Problem

Every main panel in the KJ UI has a `.header-row` (`<h2>` on the left, action buttons flushed right). Conceptually these are all the same thing — "small secondary action buttons in a section header." In practice each was authored in a different PR with its own class and its own numbers, so the family has drifted repeatedly. Adding/altering a header button re-triggers the drift; this is at least the second unification attempt (PR #135 unified only Rotation + Overlays).

Three distinct symptoms, one root cause (no shared, structural styling contract):

### 1a. Inconsistent sizing (desktop)
Measured live (device v0.56.0). Five different sizing systems for one concept:

| Section | Button(s) | Class | font-size | padding | weight | radius |
|---|---|---|---|---|---|---|
| Rotation | **Song Stats** | `.rotation-stats-btn` | **13.6px** | **10×18** | **600** | 6px |
| Rotation | Restore/Paths/Refresh/+Add/Requests/New | unified (`style.css:2938`) | 12px | 4×10 | 500 | 6px |
| Rotation | undo / redo | `.rotation-undo/redo-btn` | 13.6px | 2×8 | 600 | 4px |
| Overlays | Wallpaper/Backup/Restore/Scan/+Add | unified | 12px | 4×10 | 500 | 6px |
| Screen Preview | Hide/200/400/Fit/Max | `.vnc-size-btn` | 10.4px | 3×8 | 600 | 5px |
| Playback Controls | Simple / Advanced | `.mode-seg` pill | 10.4px | 3×10 | 600 | 5px |
| Karaoke Nerds | Prefs | `.kn-prefs-btn` | 11.2px | 4×10 | 600 | 6px |
| Divebar / Download | Status / Settings | `.rescan-btn` | 11.2px | 5×10 | 600 | 6px |
| Available Songs | All Formats / Needs review / Rescan Media / Rebuild Catalog | `.rescan-btn` | 11.2px | 5×10 | 600 | 6px |

**Song Stats root cause:** `.rotation-stats-btn` (`index.html:85`) has **zero CSS** — it falls through to the global `button {}` (`style.css:206`: `0.85em / 10px 18px / weight 600`). The unified rule at `style.css:2938-2950` lists 8 sibling classes but was never given `.rotation-stats-btn`. One missing selector = the whole bug.

### 1b. Overflow / clipping (769–1280px "two-column squeeze")
Header buttons only wrap at `≤768px`. Above 768px the layout is two columns (col1 ≈ 2/3, col2 ≈ 1/3), so each header is far narrower than the viewport. In the gap between "two columns active" and "wrap active," buttons sit on a `nowrap` row that can't fit → they overflow the container and are **clipped with no scroll and no wrap** (unreachable). Measured onset (local harness, worktree CSS):

- **Available Songs** (col2, 4 buttons): clips from **≤1280px**; up to 169px cut off at 800px (loses *Needs review, Rescan Media, Rebuild Catalog*).
- **Rotation** (col1): clips from **≤1024px**; up to 157px cut off at 800px (loses *Requests, Song Stats, New Rotation*).

1280px is a common laptop / non-maximized-window width, so this is hit constantly. Below 768px it's fine (single column + wrap).

### 1c. Mobile touch targets (≤768px)
Wrapping works, but header buttons are 23–26px tall (font 10.4–12px) — below the ~44px iOS / 48px Material guidance. The `@media (max-width:480px) button { padding:10px 12px; font-size:0.8em }` rule meant to enlarge them is **defeated by class specificity** (every header button has a class rule that outranks bare `button`). The only header button that *does* grow on mobile is Song Stats — because it's classless. So the "fix" is silently dead for every classed button.

---

## 2. Goals

1. All section-header action buttons share **one** consistent size and shape.
2. Header buttons **wrap at every width** — never clip / overflow / become unreachable.
3. Adequate **touch targets** on mobile.
4. Recurrence is **structurally prevented** and **clearly documented**, so the next PR that adds a header button can't reintroduce the drift.

---

## 3. Design

### 3.1 Mechanism — structural, not class-based
A single wrapper class `.header-actions` holds each section's button group, and the base **sizing** rule is keyed to structure (`.header-actions button`), not to per-button classes. Any `<button>` placed in a header — even with zero classes — is automatically correct. This is what makes the Song Stats bug (a classless button) impossible to reintroduce, which directly serves Goal 4.

### 3.2 Size tokens (approved: "match Screen Preview")
Single source of truth via CSS custom properties:

```css
:root {
  --hdr-btn-font-size: 0.65em;   /* ~10.4px — matches Screen Preview */
  --hdr-btn-font-weight: 500;
  --hdr-btn-pad-y: 3px;
  --hdr-btn-pad-x: 8px;
  --hdr-btn-radius: 6px;         /* standardized (was 4–6px across families) */
  --hdr-btn-min-h: 26px;         /* touch-target floor (desktop) */
  --hdr-btn-gap: 6px;
}
```

### 3.3 The wrapper
```css
.header-actions {
  display: flex;
  flex-wrap: wrap;               /* wrap at EVERY width → removes 769–1280px clipping */
  justify-content: flex-end;
  align-items: center;
  gap: var(--hdr-btn-gap);
  min-width: 0;
}
```
`.header-row` keeps `flex-wrap: wrap` so the whole `.header-actions` group can drop below the `<h2>` when the row is tight. `<h2>` gets `min-width: 0` so it shrinks rather than forcing overflow.

### 3.4 Structural base sizing (safety net)
```css
.header-actions button {         /* specificity (0,1,1) beats bare `button` (0,0,1) and any single legacy class (0,1,0) */
  font-size: var(--hdr-btn-font-size);
  font-weight: var(--hdr-btn-font-weight);
  padding: var(--hdr-btn-pad-y) var(--hdr-btn-pad-x);
  border-radius: var(--hdr-btn-radius);
  min-height: var(--hdr-btn-min-h);
  /* neutral fill inherited from global button {}: #222 / #444 / #ccc */
}
```
The ~12 scattered legacy **sizing** blocks are deleted; only color/accent rules remain (see 3.5). Behaviour-bearing classes/ids (see 3.7) are kept.

### 3.5 Accents — the only surviving per-button rules (color only, never size)
- **Danger:** New Rotation red → `.header-actions .is-danger` (or keep `.rotation-new-btn` but strip its size).
- **Active/toggled:** Paths-active blue (`.rotation-paths-btn-active`), Available-Songs filter `.rescan-btn.active` pink, Screen-Preview `.vnc-size-active` pink → keep as-is but ensure they set only color/background/border, not size.

### 3.6 Intentional exceptions (documented as exceptions)
Both stay inside `.header-actions` (so they wrap with the group and share `min-height` for touch) but opt out of the neutral button fill via their own classes:
- **Simple/Advanced segmented pill** (`.mode-segmented` / `.mode-seg`): a segmented control, not a button group. Keeps its pill look; its size is derived from the same tokens so it stays in proportion.
- **Undo/redo icon buttons** (`↩ ↪`): icon-only, muted, transparent. Keep distinct; share `min-height`.

### 3.7 JS-safety — classes/ids to preserve
JS references (must keep; only remove their *sizing* CSS): `rescan-btn`, `vnc-size-btn`, `vnc-size-active`, `rotation-paths-btn-active`, ids `#media-filter-btn`, `#media-review-btn`, `#rescan-btn`, `#kn-prefs-toggle`, `#overlay-add-btn`, `#rotation-undo-btn`, `#rotation-redo-btn`, `#mode-seg-simple/advanced`. Zero-JS-ref wrapper classes safe to rename to `.header-actions`: `rotation-header-btns`, `overlay-header-btns` (and the bare/`vnc-size-buttons` groups).

### 3.8 Responsive / touch
- Wrapping at all widths removes the clipping dead-zone (Goal 2).
- `<h2>` goes full-width at **≤768px** (currently ≤480px) so actions cleanly stack across the whole tablet range.
- `@media (max-width:768px)` bumps `--hdr-btn-min-h` to ~38px and padding for comfortable touch. Because sizing is now structural, the bump actually applies (fixes 1c).

---

## 4. Scope of change

**`templates/index.html`** — introduce `.header-actions` on 8 headers; wrap the currently-bare buttons (Karaoke Nerds Prefs, Divebar Status, Download Settings, Available Songs ×4); move undo/redo + mode-pill inside the wrapper; keep behaviour classes/ids; add an HTML comment pointing to the docs.

**`static/style.css`** — add tokens + `.header-actions` + `.header-actions button` framework + a prominent doc comment block; delete the scattered redundant sizing blocks (`style.css:256`, `574-577`, `677-684`, `863`, `1762-1782`, `1995-2003`, `2451-2457`, `2921-2950`, and the mode-compact/undo-redo size bits — exact lines confirmed during implementation); consolidate the responsive rules into token bumps.

**`docs/DEVELOPMENT.md`** — add a "Section header buttons" section with the rule ("put it in `.header-actions`, don't add bespoke size CSS; accents via modifiers; exceptions are the pill + icon buttons") and a copy-paste snippet.

**`pyproject.toml`** — version bump (frontend-only change → git-pull deploy, no service restart; will require a browser hard-refresh to bust `app.js?v=`/css cache).

**Optional — `.githooks/pre-commit`** — grep guard that fails if a `<button>` sits directly in a `.header-row` outside `.header-actions`, or if a new per-button class in the header family declares `font-size`/`padding`. (kjbox has no pytest CI — a hook is the realistic enforcement.)

---

## 5. Testing

Local Playwright harness (`kj-controller/_toolbar_harness.html`, served over `python -m http.server`, using the **worktree** `style.css`) asserts, across a 320–1920px sweep:
1. Every `.header-actions button` shares identical computed `font-size` / `font-weight` / `padding` / `border-radius` / `min-height`.
2. Zero overflow / clipping in every header at every width (no button's right edge exceeds its container).
3. Mobile (`≤768px`) `min-height` ≥ touch target.

The harness will either be committed as a small local visual/computed-style check under `tests/` or removed before the final commit (decided in the plan). It must not ship in the Flask template dir.

---

## 6. Out of scope
- Buttons **not** in section headers (in-row `btn-primary` search buttons, rotation-entry action buttons, modal buttons, System panel buttons).
- Any functional/behavioural change to what the buttons do.
- The 10 pre-existing broken `divebar__` rotation links (tracked elsewhere).

---

## 7. Risks
- **Cache:** frontend-only; running service keeps serving the old `?v=` until hard-refresh or next off-show restart. Bump version in the same PR; note in handoff.
- **Specificity regressions:** deleting legacy size rules could expose a spot where a class did more than sizing — mitigated by keeping accent rules and by the harness computed-style assertions.
- **Exceptions drift:** the pill/icon exceptions must be explicitly documented as intentional so a future author doesn't "fix" them into the neutral family.
