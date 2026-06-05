# Robust Server-Side Rotation Undo/Redo — Design & Plan

**Date:** 2026-06-04
**Branch:** `feat/sess-20260604-1915-server-side-undo`

## Background — the incident (2026-05-28)

During a live show the KJ clicked **Undo** and lost rotation history. Root cause:

1. **Undo was a whole-rotation overwrite, not an inverse op.** `undo()` in `app.js`
   POSTed a full client snapshot to `/rotation/restore`, which `restore_entries()`
   ran as `DELETE FROM rotation_entries` + re-insert. Anything not in that snapshot
   was destroyed.
2. **The undo stack was per-browser and blind to other writers.** Snapshots were
   only pushed before *that tab's* actions. Singer self-submissions
   (`sing.nomadkaraoke.com`), download/gen completions, and other devices were
   never recorded — so one undo silently reverted all of them.
3. **The stack survived a mid-show backend restart in browser memory** (auto-deploy
   ran `git pull` + `systemctl restart` at 21:06), so the snapshot was very stale.
4. **`restore_entries` reset `created_at`** (no value in the INSERT → `DEFAULT now`),
   corrupting the timeline even for rows that "survived" (all showed `22:18:33`).

Data was recoverable only because the Google Sheet sync writes rows in-place without
clearing trailing rows, leaving the pre-restore rotation visible below the new data.

## Goals

- **Layer 1:** undo can never silently destroy data — auto safety snapshot before any
  whole-table replace, a diff-confirm before applying, and preserve `created_at`.
- **Layer 2:** undo is correct under concurrent multi-writer rotation — a monotonic
  rotation revision guard, and **server-side shared undo/redo history** (survives
  restarts, shared across all KJ devices).

Out of scope: operation-based (inverse-op) undo (Layer 3).

## Design

### Revision counter (version guard)
- `rotation_meta.rotation_rev` — monotonic int, starts at 0, **bumped on every
  mutation** in `RotationManager._after_mutation()` (undo/redo included).
- Surfaced to clients on `GET /rotation` so the poll can detect divergence and act
  as an ETag.

### Server-side history table
```sql
CREATE TABLE rotation_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stack       TEXT NOT NULL,            -- 'undo' | 'redo'
    seq         INTEGER NOT NULL,         -- pop highest seq within a stack
    label       TEXT,                     -- human action, e.g. "Add Alice"
    rev         INTEGER,                  -- rotation_rev when snapshotted (informational)
    entries_json TEXT NOT NULL,           -- full snapshot of all rotation_entries
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
```
- **Checkpoint before each meaningful mutation** (`RotationManager._before_mutation(label)`):
  snapshot current state onto the `undo` stack, clear the `redo` stack, prune `undo`
  to `MAX_HISTORY = 30`.
- *Meaningful* = user-facing edits: add, edit, delete, move, status, paid,
  rename/merge/split singer, mark/unmark left, link/unlink. **Not** background
  tracking (`set_download_status`, `set_gen_status`, `set_url_fallback`,
  `complete_*`) — those would flood the stack with invisible steps.
- `undo()`: push current state → `redo`, pop newest `undo`, `restore_entries(snap,
  preserve_tracking=True)`. `redo()`: symmetric.
- `clear_history()` on `archive()` (session-scoped, like `left_singers`).

### `restore_entries` hardening
- **Preserve `created_at`** — include it in the INSERT (fallback to now if missing).
- **`preserve_tracking=True`** — for entries whose `id` still exists, keep the *live*
  `file_path/duration/download_*/gen_*/url_fallback`. Undo restores only the
  human-edited columns (singer, song, singers_json, status, position, notes, paid),
  so an undo never breaks a download/file link that completed in the background.

### Diff-confirm (Layer 1)
- Pure helper `diff_entries(current, target)` → `{removed, added, changed}` (by id,
  on human fields). Reused for preview.
- `POST /rotation/undo` (and `/redo`):
  - no `confirm` → **preview only**, returns the diff + label + snapshot time, applies
    nothing.
  - `confirm: true` → apply, return new entries + history status.
  - empty stack → `{success: false, reason: "empty"}`.
- Client shows a confirm dialog summarising the diff, then re-POSTs with `confirm`.

### Sheet restore stays reversible
- Before `restore_from_sheet()`, take a checkpoint so an emergency sheet restore is
  itself undoable.
- `/rotation/restore` keeps the **sheet** path only; the client snapshot path is
  removed (clients now call `/rotation/undo` `/rotation/redo`).

### Client (`app.js`)
- Replace the in-memory `rotationHistory` stacks with server-driven calls.
- Remove all `pushUndo(...)` call sites.
- Undo/redo buttons reflect server `history` counts from the `GET /rotation` poll.

## Build sequence (TDD)

1. `rotation_store.py` — rev counter, history table + methods, `restore_entries`
   (`created_at` + `preserve_tracking`), `diff_entries`, `clear_history`. **Tests first.**
2. `rotation.py` — `_before_mutation` hooks, `_after_mutation` rev bump, `undo/redo/
   preview_*/history_status`, archive clears history, sheet-restore checkpoint. **Tests.**
3. `routes.py` — `/rotation/undo`, `/rotation/redo`; surface `rev`+`history` on
   `GET /rotation`; trim `/rotation/restore` to the sheet path. **Integration tests.**
4. `static/app.js` — server-driven undo/redo + diff-confirm; remove local stacks.
5. Update `docs/CHANGELOG.md`, `docs/ARCHITECTURE.md`. Full `pytest`.

## Contributing-factor follow-up (separate)

Auto-deploy should not `git pull` + restart the backend mid-show. Tracked separately
from this PR.
