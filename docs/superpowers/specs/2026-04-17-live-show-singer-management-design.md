# Live-show singer management improvements

**Date:** 2026-04-17
**Status:** Draft — pending user review
**Context:** Pain points surfaced during a live karaoke night (4 hours in). The Singers panel was overflowing with done/left singers the KJ had no way to hide, no way to audit a singer's history, and no way to correct a name-collision that had been silently merging stats for two different people ("Kai" and "Kai").

## Problem

At scale (30+ singers across a long night) the current Singers panel stops being usable:

1. Singers whose songs are all sung (`status='done'`) have **no action buttons** at all. The KJ can't mark them as having left; they accumulate visually.
2. Singers marked as Left are shown inline (dimmed) instead of collapsed away. With 6+ left singers, they're still eating vertical space.
3. There's no way to see **which songs a singer has actually sung tonight**. When the KJ suspects two different people share a name (the "Kai problem"), there's no tool to disambiguate.
4. There's no way to **retroactively split** a singer once a collision is noticed — all the merged stats and queued songs stay mixed under one name.

## Goals

- Reduce the Singers panel to what the KJ actually needs to act on right now (active queue), with everything else hidden behind a disclosure.
- Give the KJ a single consistent verb for "this person has physically left", whether their songs are all sung or still queued.
- Provide inline visibility into a singer's song history without leaving the Singers panel.
- Support correcting name-collision mistakes by reassigning specific entries to a new name.

## Non-goals

- **No schema changes to `rotation_entries`.** "Left" is a session-scoped signal about the person, not per-entry data.
- **No cross-night history.** Everything here is scoped to the current rotation session. Archive resets it.
- **No rewrite of the existing merge/rename flow.** Split is additive.
- **No backend schema for "favourite singers"** or other orthogonal features. Stay focused on the reported pain.

## Design

### A. Singer sectioning in the UI

Replace the single flat Singers list with three sections. Section headers show counts and toggle collapse state (persisted in localStorage).

| Section | Population rule | Default | Per-row actions |
|---|---|---|---|
| **Active** | `status ∈ {active, brb}` | expanded | Songs · Edit · Merge · Split · BRB/Back · Left |
| **Done** | `status = done` AND not marked left | **collapsed**, shows count | Songs · Edit · Merge · Split · Left |
| **Gone** | `status = left` (marked left by KJ) | **collapsed**, shows count | Songs · Restore |

Rationale:
- "Active" is the KJ's critical path — it's the only list they need to act on between songs.
- "Done" and "Gone" are separate because a done singer may still be in the room (about to add another song); a gone singer has physically left. Keeping them distinct lets the KJ confirm departure explicitly rather than conflating "finished singing" with "went home".
- localStorage key naming follows the existing `kj-singer-stats-hidden` pattern: `kj-singers-done-collapsed`, `kj-singers-gone-collapsed`.

### B. Backend: marking done singers as Left

The existing `set_singer_status(name, "Left")` iterates non-done entries and sets their status to `Left`. For a singer whose entries are all `Done`, this is a no-op — and that's why the frontend's `status='done'` branch currently shows no buttons.

Rather than pollute the `rotation_entries` table with "left"-overwriting-done semantics (which would break the invariant that `Done` is terminal per-song), track the "left" signal at the **rotation session** level:

- New `rotation_meta` key: `left_singers_json` — JSON array of lowercased singer names.
- New `RotationStore` methods:
  - `mark_singer_left(name)` — adds lowercased name to the meta list (idempotent).
  - `unmark_singer_left(name)` — removes it.
  - `get_left_singer_names()` — returns the set (internal helper).
- `get_singer_stats()` consults the set: if `name.strip().lower()` is in it, force `status = "left"` regardless of what the entries say.
- `rename_singer(old, new)` — if `old` is in the left set, replace with `new`.
- `merge_singers(source, target)` — if `source` is in the left set, remove it (the target's own left-status is preserved if it was already set).
- `archive()` — clears `left_singers_json` along with the rest of the night state.

### C. Route/manager changes

- `RotationManager.mark_singer_left(name)` / `unmark_singer_left(name)` — wrap the store methods and call `_after_mutation`.
- Update `POST /rotation/singer/remove`: existing route keeps working, but its handler now does **both**: call `set_singer_status(name, "Left")` (for waiting/brb entries) AND `mark_singer_left(name)` (covers the done-only case). Single call from the frontend.
- Update `POST /rotation/singer/restore`: does both `set_singer_status(name, "Waiting")` and `unmark_singer_left(name)`.
- Label change only on the frontend: active-singer button renamed from **Remove** → **Left** (behavior identical; endpoint unchanged).

### D. Show songs (inline expand)

New `Songs` button available on every singer row in all three sections.

- Extend the `singer_stats` payload so each singer dict includes `entries: [{song_artist, status, created_at, position, id}, ...]` (trimmed projection — no file paths, no download fields).
- Clicking `Songs` toggles an inline expanded panel under the singer row: a compact table of their entries, status pill (same classes as rotation list), created_at shown as short relative time.
- Pure frontend state — no new endpoint.
- Useful for: spotting the Kai-vs-Kai case; confirming a singer has "actually" sung N songs; seeing what's queued under an active singer without scanning the rotation list.

### E. Split singer

New primary action. Click `Split` on a singer (available in Active and Done) → modal:

```
┌─── Split "Kai" into a new singer ────────────────────┐
│                                                      │
│  Select entries to reassign:                         │
│   ☐ Oneida - Tyler Childers        [WAITING]         │
│   ☑ Wet - Dazey and the Scouts     [WAITING]         │
│   ☑ [Kai's 3rd done song]          [DONE]            │
│   ...                                                │
│                                                      │
│  Reassign to:  ( ) New name: [___________]           │
│                ( ) Existing: [dropdown of singers ▾] │
│                                                      │
│              [Cancel]   [Split]                      │
└──────────────────────────────────────────────────────┘
```

Backend:
- New `RotationStore.split_singer(source_name, new_name, entry_ids)`:
  - For each `entry_id` in the list:
    - If entry has `singers_json`: replace `source_name` with `new_name` in the array (case-insensitive match on `source_name`; preserve other names); rebuild display `singer = " & ".join(names)`.
    - If legacy single-singer (`singers_json` is NULL): overwrite `singer = new_name`, leave `singers_json` NULL.
  - If a previously multi-singer entry ends up with exactly one name after substitution, set `singers_json = NULL` and `singer = <that one name>` to match the legacy shape (keeps storage consistent).
  - Update `updated_at`.
- New `RotationManager.split_singer(...)` wrapper calling `_after_mutation`.
- New route `POST /rotation/singer/split` with body `{source_name, new_name, entry_ids}`.

Frontend:
- `singerAction('split', ...)` reuses the existing rotation-history push/spin/reply-render pattern.
- If `new_name` matches an existing singer (case-insensitive), the split functions as a targeted partial-reassignment (not a full merge). The existing singer's left/brb/etc. status is not disturbed.
- Modal closes on success and re-renders from the result payload.

### F. Minor consistency tweaks

- Active-singer **Remove** → **Left** (label + tooltip).
- Tooltip wording: *"Mark this singer as having left — hides them from the active list (can be restored)."*
- The existing `.singer-left` CSS class keeps its dim styling for use inside the collapsed "Gone" section (still visually distinct when expanded).

## Data flow

```
KJ clicks "Left" on a Done singer
  → POST /rotation/singer/remove { name }
    → RotationManager.set_singer_status(name, "Left")      (no-op: no non-done entries)
    → RotationManager.mark_singer_left(name)               (adds to rotation_meta list)
    → _after_mutation() writes display cache
  → response includes updated singer_stats
    → singer now has status='left'
    → frontend moves row from "Done" section to "Gone" section
```

```
KJ clicks "Split" on Kai
  → opens modal, picks 2 entries + types "Kai P"
  → POST /rotation/singer/split { source_name: "Kai", new_name: "Kai P", entry_ids: [17, 34] }
    → store.split_singer() rewrites those 2 entries
    → _after_mutation
  → response includes updated entries + singer_stats
    → frontend sees a new singer "Kai P" with 2 entries; original Kai has fewer
```

## Isolation / module boundaries

The change fits cleanly into existing files:

- `rotation_store.py` — new methods (`mark_singer_left`, `unmark_singer_left`, `split_singer`); `get_singer_stats` gains a left-set consultation; `rename_singer`/`merge_singers`/`archive` add a few lines for consistency. No new files.
- `rotation.py` — thin pass-throughs for the new store methods (matches the existing coordinator pattern).
- `routes.py` — new route for split; `remove`/`restore` get a second call. No new blueprints.
- `app.js` — new section rendering in `renderSingerStats`, new inline `Songs` expand, new split modal. Frontend file is already large but the change is local to the singer-stats block (~lines 3658–3900). No new JS files.
- `style.css` — minor additions for section headers and split modal.
- `templates/index.html` — unchanged (sections built in JS under the existing `#singer-stats-list` container).

## Testing

- **Unit (`tests/unit/test_rotation_store.py`):**
  - `mark_singer_left` / `unmark_singer_left` round-trip via meta.
  - `get_singer_stats` forces `status='left'` when name is in the set, regardless of entries.
  - `rename_singer` / `merge_singers` migrate the set correctly.
  - `archive` clears the set.
  - `split_singer` — single-singer entries, multi-singer entries, entry not found, new name already present.
- **Integration (`tests/integration/test_rotation_routes.py`):**
  - `POST /rotation/singer/remove` on a done-only singer results in `status='left'` in the follow-up payload.
  - `POST /rotation/singer/split` with mixed waiting/done entries.
- **E2E (`tests/e2e/test_singer_stats_e2e.py`):**
  - Add a scenario exercising the full done→left→restore transition.
  - Split-singer end-to-end.

Coverage target: matches existing module coverage (70%+).

## Rollback

All changes are additive at the schema level (no destructive migrations). If the feature needs to be disabled live, reverting the Python changes restores prior behavior; the `left_singers_json` meta key is simply ignored by the old code. No data loss.

## Out of scope / follow-ups

- Multi-select reassign **from the rotation list** (select N rows, bulk-reassign) — powerful but adds complexity; keep Split as the primary path for now. Revisit if the singer-page UX isn't fast enough in practice.
- "Favourite" / "regular" singer tagging.
- Cross-night singer history. Would require querying `rotation_archive`.
- Analytics on how often singers leave vs. finish. Nice-to-have, not needed tonight.
