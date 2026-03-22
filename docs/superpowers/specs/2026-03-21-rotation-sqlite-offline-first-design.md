# Rotation: SQLite-Primary Offline-First Design

**Date:** 2026-03-21
**Branch:** feat/sess-20260319-2126-rotation-backup-sqlite
**Goal:** Replace Google Sheets as the rotation source of truth with a local SQLite database, enabling fully offline operation with optional Sheet backup when internet is available.

## Context & Motivation

The singer rotation currently depends on Google Sheets via the `gspread` API. This creates two problems:

1. **Data loss risk** — The `move_entry` operation does a delete-then-insert on the sheet. If something fails between these steps, the entry is lost. Entries were lost during a live show (cause unknown, likely a mid-operation failure).
2. **Internet dependency** — Venue internet is often patchy. The rotation should work without any network connection.

### Current Architecture

```
KJ Controller (Flask)
  rotation.py → Google Sheet (source of truth)
              → /tmp/rotation_cache.json (display cache for conky)
```

### Target Architecture

```
KJ Controller (Flask)
  rotation.py (coordinator)
    → rotation_store.py → SQLite DB (source of truth)
    → rotation_sync.py  → Google Sheet (background backup, optional)
    → /tmp/rotation_cache.json (display cache for conky, unchanged)
```

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Storage | SQLite with WAL mode | Matches existing `catalog.py` pattern. Fast reads, durable writes. |
| Ordering | Explicit `position` column | Sheet row indices shift on delete — this caused the lost entries. SQLite IDs + position are stable. |
| Identifiers | Auto-increment `id` (not row index) | Stable across reorders and deletes. Frontend switches from `row_index` to `id`. |
| Sheet sync | One-way push, manual restore | SQLite is always authoritative. Sheet is a read-only backup mirror. "Restore from Sheet" button for emergencies. |
| Migration | Fresh start per night | Archive operation is a natural cutover point. No migration of existing sheet data needed. |
| DB location | Configurable, default `~/kjdata/rotation.db` | Survives git pulls and deployments. Follows existing config pattern. |
| Conky display | Keep cache file, drop Sheet CSV fallback | Cache file is a clean interface. Sheet fallback no longer makes sense. |
| Scope | Full feature parity + file linking | All current features work identically. New: link rotation entries to media files for playback and time estimates. |

## SQLite Schema

```sql
CREATE TABLE rotation_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    singer TEXT NOT NULL,
    song_artist TEXT DEFAULT '',
    status TEXT DEFAULT 'Waiting',
    notes TEXT DEFAULT '',
    position INTEGER NOT NULL,
    file_path TEXT DEFAULT NULL,
    duration INTEGER DEFAULT NULL,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX idx_rotation_position ON rotation_entries(position);
CREATE INDEX idx_rotation_status ON rotation_entries(status);

CREATE TABLE rotation_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE rotation_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    night_date TEXT NOT NULL,
    singer TEXT NOT NULL,
    song_artist TEXT DEFAULT '',
    status TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    position INTEGER,
    file_path TEXT DEFAULT NULL,
    duration INTEGER DEFAULT NULL,
    created_at TEXT
);

CREATE INDEX idx_rotation_archive_night ON rotation_archive(night_date);
```

### Field Details

| Field | Type | Purpose |
|-------|------|---------|
| `id` | INTEGER PK | Stable identifier, never changes. Replaces sheet `row_index`. |
| `singer` | TEXT | Singer name (required). |
| `song_artist` | TEXT | Song and artist info (free text, as entered by KJ). |
| `status` | TEXT | One of: Waiting, Now Singing, Up Next, Done, Being Made (!), On Hold (BRB), Skipped. |
| `notes` | TEXT | Optional KJ notes. |
| `position` | INTEGER | Explicit sort order. Reorder = update positions, no delete+insert. |
| `file_path` | TEXT nullable | Linked media file path. Null until KJ maps a catalog/media file to this entry. |
| `duration` | INTEGER nullable | Track duration in seconds. Populated from `MediaIndex` when `file_path` is set. |
| `created_at` | TEXT | Timestamp for when singer was added. |
| `updated_at` | TEXT | Timestamp for last modification. |

### `rotation_meta` Keys

| Key | Purpose |
|-----|---------|
| `night_started_at` | When the current rotation was started (for stats). |
| `last_sheet_sync` | ISO timestamp of last successful sync to Sheet. |
| `default_duration` | Fallback duration in seconds for unlinked entries (default: 240 = 4 min). |

## Module Structure

### `rotation_store.py` (NEW)

Pure SQLite storage. No network calls, no MediaIndex dependency. All position operations are atomic within transactions. Uses `check_same_thread=False` (matching `catalog.py` pattern) for Flask's thread pool.

```python
class RotationStore:
    def __init__(self, db_path):
        """Initialize with path to SQLite DB. Creates schema if needed."""

    # Read
    def get_entries(self, include_done=False) -> list[dict]:
        """All entries ordered by position. Excludes Done by default."""

    def get_entry(self, entry_id) -> dict | None:
        """Single entry by ID."""

    def get_stats(self) -> dict:
        """Returns {singers: int, sung: int, queued: int, started: str}."""

    # Write
    def add_entry(self, singer, song_artist='', notes='') -> dict:
        """Insert at end (max position + 1). Returns the new entry."""

    def update_entry(self, entry_id, singer=None, song_artist=None) -> dict:
        """Edit singer name and/or song. Returns updated entry."""

    def update_status(self, entry_id, new_status) -> dict:
        """Set status. For 'Now Singing' and 'Up Next', clears that status from
        all other entries (sets them back to 'Waiting'). Status normalization
        (e.g., 'singing now' → 'Now Singing') happens in the route layer."""

    def delete_entry(self, entry_id):
        """Delete and recompact positions."""

    def move_entry(self, entry_id, new_position):
        """Move entry to new position. Shifts others atomically."""

    def link_file(self, entry_id, file_path, duration=None) -> dict:
        """Link a media file to a rotation entry. Duration must be provided by caller
        (looked up from MediaIndex by the coordinator, not the store)."""

    def unlink_file(self, entry_id) -> dict:
        """Remove file link from a rotation entry."""

    # Archive
    def archive(self, starter_singer='Andrew', starter_song='First Song of the Night') -> int:
        """Move all entries to rotation_archive with tonight's date. Creates a starter
        entry (matching current Sheet behavior). Returns count archived."""

    def get_all_entries(self) -> list[dict]:
        """All entries including Done, ordered by position. Used for Sheet sync."""
```

### `rotation_sync.py` (NEW)

Background sync to Google Sheets. Runs in a daemon thread.

```python
class SheetSync:
    def __init__(self, store, sheet_id, credentials_file, sync_interval=30):
        """Initialize with a RotationStore and Sheet config."""

    def start(self):
        """Start background sync thread."""

    def stop(self):
        """Stop background sync thread."""

    def sync_now(self):
        """Force an immediate sync. Returns True if successful."""

    def restore_from_sheet(self) -> int:
        """Pull sheet data into SQLite (emergency restore). Returns entry count."""

    def get_status(self) -> dict:
        """Returns {last_sync: str, is_online: bool, next_sync_in: int}."""
```

**Sync strategy (push):**
1. Fetch all entries from `RotationStore.get_all_entries()`
2. Overwrite sheet data rows in-place using `batch_update` (avoids the clear-then-write race where a crash between clear and write leaves the sheet empty)
3. Also push archived entries to the "Past events" sheet (mirrors current `archive_rotation` behavior)
4. Update `rotation_meta.last_sheet_sync`

**Restore strategy (pull):**
1. Read all sheet rows
2. Within a single SQLite transaction: clear `rotation_entries` table, insert sheet rows with auto-assigned positions
3. Return count

Note: `restore_from_sheet()` acquires exclusive access to the store — the coordinator should reject other write operations while restore is in progress (short-lived, <2s typically).

**Error handling:**
- Network errors → log warning, retry next cycle
- Auth errors → log error, disable sync until restart
- Never blocks the main thread or rotation operations

### `rotation.py` (MODIFIED)

Becomes a thin coordinator. Delegates storage to `RotationStore`, sync to `SheetSync`.

```python
class RotationManager:
    def __init__(self, db_path, sheet_id=None, credentials_file=None, sync_interval=30):
        self.store = RotationStore(db_path)
        self.sync = None
        if sheet_id and credentials_file:
            self.sync = SheetSync(self.store, sheet_id, credentials_file, sync_interval)
            self.sync.start()

    # All CRUD methods delegate to self.store, then kick sync + write display cache
    # Public interface stays the same (minus row_index → id)
    # get_rotation() wraps store.get_entries() (force_refresh param is ignored — SQLite is always fresh)
    # link_file() looks up duration from MediaIndex before delegating to store
```

## API Changes

### Modified Endpoints

All existing rotation endpoints change `row_index` to `id`:

| Endpoint | Method | Body Change |
|----------|--------|-------------|
| `GET /rotation` | GET | Response: entries now have `id` instead of `row_index` |
| `POST /rotation/status` | POST | `{"id": int, "status": str}` |
| `POST /rotation/edit` | POST | `{"id": int, "singer": str, "song_artist": str}` |
| `POST /rotation/delete` | POST | `{"id": int}` |
| `POST /rotation/add` | POST | `{"singer": str, "song_artist": str, "notes": str}` — notes optional, now accepted |
| `POST /rotation/move` | POST | `{"id": int, "new_position": int}` |
| `POST /rotation/archive` | POST | Unchanged |

### New Endpoints

| Endpoint | Method | Body | Purpose |
|----------|--------|------|---------|
| `POST /rotation/link` | POST | `{"id": int, "file_path": str}` | Link a media file to a rotation entry. Looks up duration from MediaIndex. |
| `POST /rotation/unlink` | POST | `{"id": int}` | Remove file link from a rotation entry. |
| `GET /rotation/sync-status` | GET | — | Returns `{last_sync, is_online, next_sync_in}` |
| `POST /rotation/restore` | POST | — | Emergency restore from Sheet |

### Response Format

Each entry in the rotation response:
```json
{
    "id": 12,
    "singer": "Alice",
    "song_artist": "Bon Jovi - Livin' On A Prayer",
    "status": "Waiting",
    "notes": "",
    "position": 3,
    "file_path": "/mnt/Nomad4TBOne/PHK004 - Bon Jovi - Livin On A Prayer.zip",
    "duration": 251,
    "created_at": "2026-03-21 20:15:00",
    "estimated_time": "21:03"
}
```

The `estimated_time` field is computed at response time by summing durations of entries with lower positions. Rules:
- The "Now Singing" entry is excluded from the sum (already in progress, remaining time unknown)
- Unlinked entries use the default duration from `rotation_meta` (default: 240s = 4 min)
- Times crossing midnight are handled correctly (just keep adding)
- This is a computed field, not stored

## Frontend Changes

### `app.js`

**Mechanical replacements:**
- `entry.row_index` → `entry.id` in all fetch calls and DOM data attributes
- `{from_row, to_row}` → `{id, new_position}` for drag-and-drop. Note: the current code sends `entries[fromIdx].row_index` and `entries[toIdx].row_index`. The new code sends `entry.id` and the target entry's `position` value (not another entry's ID).
- `{row_index, status}` → `{id, status}` for status updates
- Status normalization (e.g., fuzzy matching "singing now" → "Now Singing") stays in the route layer, matching current behavior

**New: File linking UI**
- Each rotation entry without `file_path` shows a "Link" button
- Clicking "Link" opens an inline catalog search (reuses existing `/search` endpoint)
- Selecting a result calls `POST /rotation/link` with the file path
- Linked entries show:
  - Duration badge (e.g., "3:42")
  - "Play" button (calls existing `/play` endpoint with `file_path`)
  - "Unlink" button (small, secondary)

**New: Time estimates**
- Each entry shows estimated sing time (e.g., "~9:03 PM") based on cumulative durations
- Computed client-side from the `estimated_time` field in the response

**New: Sync indicator**
- Small status dot in rotation header: green (synced), yellow (syncing), gray (offline/no sheet config)
- Tooltip shows last sync time
- Polls `GET /rotation/sync-status` every 30s

**New: Restore button**
- In a dropdown/overflow menu (not prominent)
- Confirms before calling `POST /rotation/restore`

### `style.css`

- Duration badge styling (small, muted, inline with song text)
- Estimated time styling (right-aligned, muted)
- Sync indicator dot (small colored circle)
- Link/Play button styling (consistent with existing action buttons)

## Conky Display Changes

### `desktop/rotation_data.py`

- Remove `fetch_from_sheet()`, `fetch_all_rows()`, and the Sheet CSV fallback path
- Remove related imports (`csv`, `io`, `urlopen`) and constants (`SHEET_ID`, `SHEET_GID`, `SHEET_CSV_URL`)
- Keep `read_local_cache()` as the only data source
- If cache is stale (>120s), show "Offline" (same as current behavior on failure)
- The cache JSON format stays the same — no changes to `format_conky()`

## Configuration

In `config.json`:
```json
{
    "rotation_db_path": "~/kjdata/rotation.db",
    "rotation_sheet_id": "1OzNxqJB-pYHhI0VJkkPjJc1Ba242TL6Kadov52GHWl8",
    "rotation_credentials_file": "~/kjdata/rotation-sa-key.json",
    "rotation_sync_interval": 30
}
```

- `rotation_db_path` — required (default: `~/kjdata/rotation.db`)
- `rotation_sheet_id` + `rotation_credentials_file` — optional. If absent, sync is disabled (fully offline mode).
- `rotation_sync_interval` — seconds between sync pushes (default: 30)

## Testing

### `test_rotation_store.py` (NEW)

Tests against in-memory SQLite (`":memory:"`):

- **CRUD:** add, get, update, delete entries
- **Ordering:** position assignment, recompaction after delete
- **Move:** move up, move down, move to same position (no-op)
- **Exclusive statuses:** marking Now Singing clears other singing entries
- **File linking:** link, unlink, duration lookup
- **Archive:** entries move to archive table, rotation resets
- **Stats:** correct counts for singers, sung, queued
- **Edge cases:** empty rotation, single entry, all done

### `test_rotation_sync.py` (NEW)

Tests with mocked gspread:

- **Push:** correct sheet format, handles empty rotation
- **Restore:** sheet data imported correctly, positions assigned
- **Offline resilience:** network error doesn't crash, retries next cycle
- **Auth error:** disables sync, logs error

### `test_routes_rotation.py` (MODIFIED)

- Update all existing rotation route tests: `row_index` → `id`
- Add tests for new endpoints: `/rotation/link`, `/rotation/unlink`, `/rotation/sync-status`, `/rotation/restore`
- Test 503 when rotation not configured (unchanged behavior)

## Rollback Plan

If issues arise during a live show:
1. The "Restore from Sheet" button pulls the last synced state
2. If SQLite is completely broken, revert the code and restart — the old Sheet-based rotation.py still works
3. The Sheet always has a recent copy (synced every 30s when online)
