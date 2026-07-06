# Rotation "Link song" search-result row layout cleanup

Date: 2026-07-06 · Worktree: `kjbox-rotation-link-buttons`

## Problem (from live "Bastille - Pompeii" baseline)

The rotation link-search result rows are visually noisy and misaligned:

- **Preview ▶ jumps horizontally**: x≈808 on "Link" rows vs x≈732 on "DL & Link" rows —
  the `YouTube` source pill (present only on download rows) shoves it left.
- **Action button differs**: `Link` (w=64) vs `DL & Link` (w=78), left edge 847 vs 833.
- **Format column drifts**: variable-width stats (`👁1✎`=33px vs `✎`=9px) push the whole
  `.rs-tags` (format+brand) block sideways, so the format pill never lands in one column.
- **Inconsistent chip styling**: format/brand/best pills, stat indicators (▶N, 👁N) and the
  ✎ note-edit button have differing heights, radii, and borders (some none).

Reference for "good/consistent" = the actual rotation list rows (`.rotation-btn` = radius 5px,
1px border, colour-coded; `.rotation-badge` = radius 4px bg-tint pill).

## Design

New row anatomy (left → right):

```
[Best/★] brand-name  Title…            [stats…][BRAND]   [fmt][▶][Link/Download]
└──────── .rs-main (flex:1) ───────────┘└ .rs-extras ─┘  └──── .rs-fixed ────┘
                                         sometimes, var    always, fixed, pinned right
```

- **`.rs-fixed`** — always-shown, fixed child widths → every element at the same x on every
  row: `.rs-fmt` (58px, pill right-aligned) · preview ▶ (30px) · action button (88px).
- **`.rs-extras`** — sometimes-shown, variable, right-aligned against the fixed cluster so its
  presence/width never shifts the fixed columns: stats chips + ✎ edit + brand pill.
- **Main** flex:1 absorbs all slack, so `.rs-fixed` (last, fixed) is pinned to the right edge.

### Req 1 — Download button with source icon, no source pill
- Remove the `YouTube` / `GCS` / `DRIVE` source pills (`rotSourceBadge`) from the rows.
- `DL & Link` → `<icon> Download`, icon inlined SVG (~13px, offline-safe):
  - YouTube source → YouTube icon + **red** outline.
  - Community mirror (GCS/Drive) → Google Drive icon + **green** outline (like `Link`).

### Req 2 — fixed-width, right-aligned always cluster
- `Link` and `Download` share one fixed width (88px). Preview fixed (30px). Format slot 58px.
- Sometimes-shown (stats, brand) live in `.rs-extras`, left of the fixed cluster.

### Req 3 — unified chip/button language
- All buttons (preview, Link, Download): height 22–24px, radius 5px, 1px border, colour-coded.
- All pills (format, brand, best, stat indicators): height ~20px, radius 5px, bg-tint, no border.
- ✎ note-edit: small bordered icon button (matches the action-button border language).

## Files
- `kj-controller/static/app.js` — `renderRotRowHtml`, `renderRotKnRow`, `renderRotDivebarRow`,
  remove `rotSourceBadge`/`rotTagsHtml`, add `rotDownloadBtn` + inline icons.
- `kj-controller/static/style.css` — `.rs-row`/`.rs-main`/`.rs-extras`/`.rs-fixed`/`.rs-fmt`,
  scoped button + pill styles.
- `kj-controller/pyproject.toml` — version bump (cache-bust).

Verify live against real catalog (Playwright, "Bastille - Pompeii"); measure column alignment.

## Round 2 (review feedback)

1. **Row is inert** — removed the `.rs-row` `onclick` (and the `rs-clickable` class + pointer
   cursor). Linking is now *only* an explicit click on a row's Link / Download button (each
   carries the `selectRotSearchResult` onclick). Selecting the title text can't trigger a link.
2. **Enter never links** — the dropdown no longer default-selects the top row, and the Enter
   handler no longer calls `selectRotSearchResult`. In link mode Enter just re-runs the search
   (skips the debounce); in add mode it adds the singer unlinked. Removed arrow-key row nav and
   the now-dead `rotMoveSelection`/`rotVisibleIndices`/`highlightRotSearchResult`. Hint updated.
3. **Format pills** — `formatPillLabel()`: `cdg+mp3`→`CDG`, `mp4`→`MP4`, else `.toUpperCase()`.
   Applied to the search dropdown *and* the Library "YOUR LIBRARY" + "CATALOG" pills. Fixed
   width: dropdown 44px (fills `.rs-fmt`, centred), Library base `.format-badge` min-width 40px
   centred.
4. **Uniform height 22px** — buttons dropped 24→22px, pills raised 20→22px (was the mismatch:
   preview 24 vs format 20).
5. **Left padding** — `.rs-row` padding-left 28→14px.

Verified live: row-body click and Enter both leave the entry UNLINKED; format pills CDG/MP4 at
44px (dropdown) / 40px (Library); all buttons+pills 22px; padding-left 14px.

## Round 3 (review feedback, 11 items)

Rotation dropdown + Library polish:
1. Format pills narrower — `.rs-fmt` 44→40px; Library base `.format-badge` min-width 40→34px.
2. Library format pill moved out of the title row into the `.media-actions` cluster.
3. Path prefix `Nomad4TBOne/HyperMule/Master Karaoke Folder/` stripped in `prettyFolder()` (Library)
   and the rotation local-row subline.
4. Rotation gaps 6→3px (`.rs-fixed`, `.rs-extras`, `.rs-stats`).
5. Rotation: click track name → `copyRotationText` (copy); click format pill → `openRotTechDetails`
   → tech-details modal (only rows with a local file: on-disk / already-downloaded).
6. Link button gets an inline chain icon (`RS_ICON_LINK`, currentColor) like the Download icon.
7. Rotation preview button widened to a play-glyph + "Preview" label (`previewButtonHtml` in preview.js).
8. Rotation note-edit `.rs-note-edit` recoloured grey→yellow.
9. Library: delete → small `🗑` icon that arms to "Confirm?" on click (`armButtonConfirm`); green Play
   button gains "▶ Play" text and moves to right-most (away from the pink preview).
10. Library edit Cancel/Save fixed — they called `updateMediaList()`, which early-returns while a
    search is active, so nothing happened in the search view. Now they restore the row in place via
    `li.replaceWith(createMediaItemLi(item))` (Save also updates the item's artist/title/display_name).
11. Version Note modal restyled — themed full-width Label/Note fields stacked vertically (the default
    textarea rendered white and overlapped its label).

Verified live (proxy): preview="▶ Preview"; Link has chain icon (88px == Download 88px); note-edit
yellow; fmt pills 40px; Library actions order = MP4 · ▶ · ✎ · 🗑 · ▶ Play; paths stripped; edit→Cancel
restores; trash arms to "Confirm? (3s)"; format-pill click opens tech-details; Version Note modal clean.

Note: dev proxy now also serves `preview.js` locally (added to the override map).
