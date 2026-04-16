# Singer Stats Panel — Design Spec

**Date:** 2026-04-14
**Phase:** 2 of 2 (Phase 1: Multi-Singer Data Model — shipped as PR #56, #57)
**Scope:** Singer-focused stats panel with actions, shared undo/redo

## Summary

Add a collapsible "Singers" panel below the rotation queue that shows per-singer aggregate information and provides bulk actions. Helps the KJ manage a busy night by seeing at a glance who's been waiting longest, who's had plenty of turns, and quickly managing singers (rename typos, merge duplicates, mark someone as BRB or gone).

## Data Model

### No new tables or columns

All data derives from existing `rotation_entries`. The backend computes singer stats by grouping entries by individual singer name (unpacking `singers_json` for multi-singer entries, falling back to `singer` string for legacy entries).

### New status: "Left"

When a singer leaves, their non-done entries get status `"Left"`. The main rotation queue (which filters `status != 'done'`) already excludes statuses other than Waiting/Now Singing/Up Next/BRB/Skipped. "Left" entries need to be excluded from the main queue the same way "Done" entries are — by adding `LOWER(status) != 'left'` to `get_entries()`.

Actually, reviewing the code: `get_entries()` only excludes `'done'`. Other statuses like BRB, Skipped show in the queue. So "Left" entries would show in the queue too, which is wrong. Fix: `get_entries()` should also exclude `'left'` — `WHERE LOWER(status) NOT IN ('done', 'left')`.

### Singer stats computation

A new `get_singer_stats()` method on `RotationStore` fetches all entries (including done and left), groups by individual singer name (case-insensitive), and computes:

```python
{
    "name": str,              # display name (original casing from first entry)
    "entries_total": int,     # all entries for this singer
    "entries_sung": int,      # entries with status 'done'
    "entries_waiting": int,   # entries with non-terminal status (not done/left)
    "entries_left": int,      # entries with status 'left'
    "first_added": str,       # earliest created_at across all entries
    "has_tipped": bool,       # any entry with paid=1
    "status": str,            # "active" | "brb" | "left" | "done"
}
```

The `status` field is derived:
- "left" if all non-done entries are "Left"
- "brb" if all non-done entries are "On Hold (BRB)" or similar
- "done" if all entries are "Done" (no waiting entries)
- "active" otherwise

`next_estimated_time` is computed by `_add_time_estimates` logic already in routes.py — we reuse it: for each singer, find their first non-done, non-left entry in the queue and use its `estimated_time`.

## Backend Changes

### RotationStore (`rotation_store.py`)

**`get_entries()`** — exclude "left" status alongside "done":
```python
WHERE LOWER(status) NOT IN ('done', 'left')
```

**`get_singer_stats()`** — new method:
1. Fetch all entries: `SELECT * FROM rotation_entries ORDER BY position`
2. Group by individual singer name (unpack `singers_json`, case-insensitive)
3. Compute aggregates per singer
4. Return list of singer stat dicts, sorted by `first_added` (longest-waiting first)

**`rename_singer(old_name, new_name)`** — new method:
- Find all entries where `old_name` appears (in `singers_json` array or as the `singer` string for legacy entries)
- For each: replace `old_name` with `new_name` in `singers_json`, regenerate `singer` display string
- For legacy entries (null `singers_json`): update `singer` directly

**`merge_singers(source_name, target_name)`** — new method:
- Same as `rename_singer` — replaces `source_name` with `target_name` across all entries
- If an entry already has `target_name` in its `singers_json`, remove the duplicate

**`set_singer_status(name, new_status)`** — new method:
- Find all non-done entries where `name` appears
- Update their status to `new_status`
- Used for BRB toggle ("On Hold (BRB)"), remove ("Left"), restore ("Waiting")

### Routes (`routes.py`)

**`/rotation` GET** — add `singer_stats` to the response:
```python
return jsonify({"entries": entries, "singer_stats": singer_stats})
```

The singer stats are computed after `_add_time_estimates` so we can extract `estimated_time` for each singer's next entry.

**New routes** (all POST, all return updated `{entries, singer_stats}`):

- **`/rotation/singer/rename`** — `{old_name, new_name}` — renames across all entries
- **`/rotation/singer/merge`** — `{source_name, target_name}` — merges source into target
- **`/rotation/singer/brb`** — `{name, brb: true/false}` — toggles BRB on all non-done entries
- **`/rotation/singer/remove`** — `{name}` — marks all non-done entries as "Left"
- **`/rotation/singer/restore`** — `{name}` — changes "Left" entries back to "Waiting"

Each route follows the existing pattern: validate input, call store method, fetch updated entries + stats, add time estimates + songs_sung, return JSON.

### RotationManager (`rotation.py`)

Pass-through methods for each new store method, with `_after_mutation()` call.

## Frontend Changes

### HTML (`index.html`)

Add a new section inside `.rotation-panel`, after `#rotation-list`:

```html
<div id="singer-stats-panel" class="singer-stats-panel">
    <div class="singer-stats-header">
        <h3>Singers</h3>
        <button class="singer-stats-toggle" onclick="toggleSingerStats()">Hide</button>
    </div>
    <div id="singer-stats-list" class="singer-stats-list"></div>
</div>
```

### JS (`app.js`)

**`renderSingerStats(stats)`** — renders the singer stats list. Called from `renderRotation` whenever `rotationData` updates (since `/rotation` now returns `singer_stats`).

Each singer row shows:
- Singer name (bold)
- Time badge: "here 45m" (time since first_added)
- Song counts: "2/4 sung" (entries_sung / entries_total)
- Next sing: "~9:45 pm" (from estimated_time of their next queued entry)
- Tip indicator: heart icon if has_tipped
- Action buttons: Edit, Merge, BRB, Remove (or Restore for "left" singers)

**Row styling by status:**
- Active: normal
- BRB: dimmed, orange left border (matches rotation row BRB style)
- Left: dimmed, at bottom of list, with Restore button instead of Remove
- Done (all sung, none waiting): hidden from the panel (they have no actionable entries)

**`toggleSingerStats()`** — hide/show with `localStorage.setItem('kj-singer-stats-hidden', '1')`. Same pattern as VNC preview.

**Action handlers** — each pushes `rotationHistory.pushUndo(rotationData)` before calling the API. On success, updates both `rotationData` and `singerStatsData`, re-renders both.

**Edit action** — clicking Edit on a singer row replaces the name with an inline text input + Save/Cancel buttons. Save calls `/rotation/singer/rename`.

**Merge action** — clicking Merge shows a dropdown of all other singer names. Selecting one calls `/rotation/singer/merge`.

**BRB toggle** — single button that toggles between "BRB" and "Back" based on current singer status. Calls `/rotation/singer/brb`.

**Remove/Restore** — Remove calls `/rotation/singer/remove`. Restore calls `/rotation/singer/restore`.

### CSS (`style.css`)

- `.singer-stats-panel` — container styling, matching rotation panel aesthetic
- `.singer-stats-header` — flex row with title and toggle button
- `.singer-stats-row` — individual singer row (flex, hover effect)
- `.singer-stats-row.singer-brb` — dimmed, orange border
- `.singer-stats-row.singer-left` — dimmed, gray
- `.singer-stats-name`, `.singer-stats-time`, `.singer-stats-songs` — individual elements
- `.singer-stats-actions` — action button container

## Undo/Redo

All singer panel actions push to the shared `rotationHistory` stack before calling the API. The existing `_restore` mechanism (POST `/rotation/restore` with full entry snapshot) handles undoing any singer action since they all modify `rotation_entries`.

No changes to the undo/redo mechanism itself — just ensure singer panel actions call `rotationHistory.pushUndo(rotationData)` before each API call.

## Testing Strategy

### Unit tests (`tests/unit/test_rotation_store.py`)

New `TestSingerStats` class:
- `test_get_singer_stats_basic` — add entries for 3 singers, verify stats
- `test_get_singer_stats_multi_singer_entry` — duet entry credits both singers
- `test_get_singer_stats_mixed_statuses` — singer with done + waiting entries
- `test_get_singer_stats_left_status` — left singer shows correct status
- `test_get_singer_stats_brb_status` — BRB singer shows correct status
- `test_get_singer_stats_sorted_by_first_added` — earliest first
- `test_get_entries_excludes_left` — left entries filtered from queue

New `TestSingerActions` class:
- `test_rename_singer` — renames across all entries
- `test_rename_singer_in_multi_singer_entry` — updates singers_json correctly
- `test_merge_singers` — merges source into target
- `test_merge_singers_deduplicates` — if entry already has target, no duplicate
- `test_set_singer_status_brb` — marks all non-done entries as BRB
- `test_set_singer_status_left` — marks all non-done entries as Left
- `test_set_singer_status_waiting_restores` — restores Left entries to Waiting

### Integration tests (`tests/integration/test_rotation_routes.py`)

- `test_rotation_includes_singer_stats` — GET /rotation includes singer_stats
- `test_singer_rename_route` — POST /rotation/singer/rename
- `test_singer_merge_route` — POST /rotation/singer/merge
- `test_singer_brb_route` — POST /rotation/singer/brb
- `test_singer_remove_route` — POST /rotation/singer/remove
- `test_singer_restore_route` — POST /rotation/singer/restore

### E2E Playwright tests (`tests/e2e/test_singer_stats_e2e.py`)

- `test_singer_stats_panel_visible` — panel renders with singer rows
- `test_singer_stats_toggle_hide_show` — hide/show persists
- `test_singer_rename_inline` — edit name, save, verify updated
- `test_singer_brb_toggle` — mark BRB, verify dimmed, toggle back
- `test_singer_remove_and_restore` — remove singer, verify gone from queue, restore

## Out of Scope

- Singer name autocomplete/suggestions in the add form
- Historical stats across nights (archive data)
- Singer profile photos or extended metadata
