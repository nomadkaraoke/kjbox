# Rotation Undo/Redo Design

**Date:** 2026-04-04
**Problem:** During live shows, it's easy to click the wrong button (e.g., "Done" on the wrong singer row). There's no way to reverse accidental actions.

## Decisions

- **All mutations undoable:** status changes, deletes, moves, edits, adds, link/unlink
- **10 levels of undo** (and redo)
- **In-memory only** — undo stack lives in browser JS, lost on page refresh
- **Undo/Redo buttons in rotation header** — next to add-singer button, always visible
- **Snapshot-based approach** — store full rotation state before each mutation; undo restores the previous snapshot via a single backend endpoint

## Approach: Snapshot-Based Undo

Before each mutation, snapshot the current `rotationData` array. Undo = POST the previous snapshot to `/rotation/restore`. Redo = POST the next snapshot.

**Why snapshot over command-based (inverse operations):**
- `update_status("Now Singing")` has side effects (clears other singing entries). Inverting that requires tracking all affected entries, not just one.
- Delete requires re-adding with exact position, file_path, duration, download state, etc.
- Snapshot handles all mutations uniformly with zero per-mutation inverse logic.

## 1. Frontend Undo Stack

A JS object managing two arrays: `undoStack` and `redoStack`, max 10 entries each.

```
const rotationHistory = {
    undoStack: [],   // Array of rotationData snapshots
    redoStack: [],   // Array of rotationData snapshots
    maxSize: 10,

    pushUndo(snapshot) — deep-copy snapshot onto undoStack, clear redoStack, trim to maxSize
    undo() — pop undoStack, push current state onto redoStack, POST popped snapshot to /rotation/restore
    redo() — pop redoStack, push current state onto undoStack, POST popped snapshot to /rotation/restore
    canUndo() — undoStack.length > 0
    canRedo() — redoStack.length > 0
}
```

## 2. Backend: `/rotation/restore` Endpoint

**Route:** `POST /rotation/restore`

**Request body:**
```json
{
    "entries": [
        {
            "id": 1,
            "singer": "Alice",
            "song_artist": "Bohemian Rhapsody - Queen",
            "status": "Waiting",
            "notes": "",
            "position": 1,
            "file_path": "/path/to/file.mp4",
            "duration": 240,
            "download_source": null,
            "download_status": null,
            "download_id": null,
            "url_fallback": null,
            "gen_job_id": null,
            "gen_status": null
        }
    ]
}
```

**Implementation:**
1. Validate entries array is present.
2. `RotationManager.restore_entries(entries)` delegates to `RotationStore.restore_entries(entries)`.
3. Store method in a single transaction: `DELETE FROM rotation_entries`, reset autoincrement, `INSERT` each entry preserving all fields including original `id`.
4. Call `_after_mutation()` for display cache and sync.
5. Return `{ success: true, entries: [...] }` with time estimates (same as other mutation endpoints).

**Preserving original IDs** is important — in-flight downloads or gen jobs reference entry IDs and must still resolve.

## 3. UI: Undo/Redo Buttons

- Two buttons in the rotation header bar, next to the sync dot / add-singer button.
- Undo: `↩` icon. Redo: `↪` icon.
- Styled with `rotation-btn` class, muted/neutral color to not compete with action buttons.
- Grayed out (disabled) when respective stack is empty.
- Tooltip: "Undo (N remaining)" / "Redo (N remaining)".
- On click: show spinning indicator, call undo/redo, show success/error indicator.

## 4. Snapshot Capture Points

Every mutation function calls `rotationHistory.pushUndo(rotationData)` before the API call:

| Function | Notes |
|---|---|
| `updateRotationStatus()` | Status changes (Done, Singing, Up Next, etc.) |
| `advanceRotationStatus()` | Snapshot once before both status updates (single undo step) |
| `deleteRotationEntry()` | Keep existing confirm dialog; also undoable |
| `moveRotationEntry()` | Drag/reorder |
| `addRotationEntry()` | Add new singer |
| Edit handler (inline) | Singer/song name edits |
| Unlink handler (inline) | File unlinking from dropdown |
| Link handler | File linking |

## 5. Testing

**Backend unit tests (`test_rotation_store.py`):**
- `restore_entries()` replaces all entries atomically
- Preserves entry IDs
- Handles empty rotation (restore to empty)
- Handles restore with more/fewer entries than current state

**Backend route tests (`test_routes.py`):**
- `POST /rotation/restore` happy path
- Missing `entries` field returns 400
- Invalid entry data returns error

**Frontend:** Manual testing on device (no JS test framework in this project).
