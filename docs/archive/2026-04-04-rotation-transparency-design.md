# Rotation Transparency Design

**Date:** 2026-04-04
**Branch:** feat/sess-20260404-1649-rotation-transparency
**Status:** Design approved, pending implementation

## Motivation

Push towards greater transparency for singers at karaoke nights. Show rotation rules/policy on the public display so singers understand how the queue works, and mark paid priority bumps visibly so the system feels fair and open.

## Requirements

1. Display rotation rules on the right side of the conky rotation screen
2. Add a "paid" flag to rotation entries, shown as a ♥ on the conky display
3. Allow KJ to toggle "paid" via the "..." dropdown menu in KJ Controller
4. Provide a printable version of the rules for laminating

## Design Decisions

- **Approach:** All changes within the existing conky display system (no new processes or browser-based display)
- **Rules text:** Static file on disk (`desktop/rotation_rules.txt`), not editable from the web UI
- **Paid UI:** Context menu toggle (not a visible button), keeps the rotation row clean
- **Printable rules:** Standalone HTML file, not connected to the app

## Schema Change

Add `paid` column to `rotation_entries` table:

```sql
ALTER TABLE rotation_entries ADD COLUMN paid INTEGER NOT NULL DEFAULT 0;
```

Migration follows the existing pattern in `rotation_store.py` — check `PRAGMA table_info` and `ALTER TABLE` if missing.

## Store & Manager API

**RotationStore.set_paid(entry_id, paid):**
- Validates entry exists (raises `ValueError` if not)
- Updates `paid` column and `updated_at`
- Simple boolean toggle, no exclusivity rules
- Stores as `1`/`0` in SQLite; REST API accepts JSON boolean, Python converts with `int(bool(paid))`

**RotationManager.set_paid(entry_id, paid):**
- Delegates to `store.set_paid()`
- Calls `_after_mutation()` to write display cache and trigger sync

## REST API

**POST /rotation/set-paid**

Request body:
```json
{"id": 42, "paid": true}
```

Response: standard rotation response with updated `entries` array.

Follows the same pattern as `update_rotation_status` and other rotation mutation endpoints.

## Display Cache

`_write_display_cache()` in `rotation.py` adds `"paid"` to each queue entry:

```python
{
    "singer": e["singer"],
    "song_artist": e["song_artist"],
    "status": e["status"],
    "paid": bool(e["paid"]),
}
```

## Conky Display Layout

Split the 1920x1080 screen into two visual columns:

- **Left (px 90-960):** Rotation queue (existing, unchanged — text doesn't extend past ~900px)
- **Right (px 1020-1830):** Rules panel

### Rules Panel

Header: "HOW IT WORKS" in white bold (~size 28), matching the "ROTATION" header style.

Bullets in muted gray (~size 18):
```
HOW IT WORKS

• First come, first sing
• New singers get priority
• Multiple songs? We'll spread them out
• Need to leave? Ask the KJ
• ♥ = paid priority ($20+)
```

Rules are loaded from `desktop/rotation_rules.txt` (one bullet per line, no bullet character — the script adds it).

### Paid Indicator in Queue

A red ♥ appears after the singer's name for entries with `paid=1`:
```
3. Jenny ♥
   Don't Stop Believin' - Journey
```

### Conky Config Changes

Add a third `${execpi}` call in `rotation.conkyrc`:
```
${execpi 3 /usr/bin/python3 /opt/nomad/kjbox/desktop/rotation_data.py --rules}
```

`rotation_data.py` gets a `--rules` mode that reads `rotation_rules.txt` and outputs conky markup positioned on the right side of the screen.

## KJ Controller UI

### Dropdown Menu

In the "..." dropdown for each rotation entry, after the existing items, add:

- A separator
- "Mark as Paid ♥" (if not paid) or "Remove Paid ♥" (if paid)

Calls `POST /rotation/set-paid` with the entry ID and toggled boolean.

### Visual Indicator

When `entry.paid` is true, show a small red ♥ next to the singer name in the rotation list in the web UI.

## Printable Rules

`desktop/rotation_rules_printable.html` — standalone HTML file:

- Clean white background, black text, good print margins
- Nomad Karaoke branding at top
- 5 rules with 1-2 sentence explanations each:
  1. **First come, first sing** — Default order is the order you submit your request.
  2. **New singers get priority** — First-timers get bumped up to sing within the next few songs, so everyone gets a chance.
  3. **Multiple songs welcome** — Submit as many as you want. We'll spread them out so nobody sings twice in a row.
  4. **Need to leave?** — Let the KJ know and we'll try to get you one last song before you go.
  5. **Paid priority** — Pay $20+ to skip ahead. Marked with ♥ on the screen.
- Fits on a single printed page
- CSS `@media print` for clean output

## File Changes Summary

| Layer | File(s) | Change |
|-------|---------|--------|
| SQLite schema | `rotation_store.py` | Add `paid` column + migration |
| Store API | `rotation_store.py` | `set_paid(entry_id, paid)` method |
| Manager | `rotation.py` | `set_paid()` delegation + `paid` in display cache |
| REST API | `routes.py` | `POST /rotation/set-paid` endpoint |
| Frontend | `app.js` | Toggle in "..." dropdown, ♥ next to paid singers |
| Frontend CSS | `style.css` | Style for paid heart indicator |
| Conky config | `rotation.conkyrc` | Add `${execpi}` for rules panel |
| Conky data | `rotation_data.py` | `--rules` mode, ♥ for paid entries |
| Rules text | `desktop/rotation_rules.txt` | Static 5-line bullet list |
| Printable | `desktop/rotation_rules_printable.html` | Single-page printable version |
| Tests | `tests/` | `set_paid` store method, API endpoint, cache inclusion |

## Out of Scope

- Automated bump logic (KJ manually moves entries — this adds transparency only)
- UI-based rules editing (static text file is sufficient)
- Payment processing or tracking (paid is a simple boolean toggle)
