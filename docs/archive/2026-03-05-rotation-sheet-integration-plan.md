# Rotation Sheet Integration Plan

**Date:** 2026-03-05
**Branch:** feat/sess-20260305-0041-rotation-sheet-integration
**Goal:** Integrate the Google Sheets singer rotation with the KJ Controller UI so it's easy to keep the rotation updated during a show.

## Context

Currently the singer rotation is managed entirely via a Google Sheet:
- Sheet: `1OzNxqJB-pYHhI0VJkkPjJc1Ba242TL6Kadov52GHWl8`
- Columns: Timestamp | Singer | Song & Artist | Status | Notes
- A Conky desktop widget (`desktop/rotation_data.py`) reads it periodically and shows it on screen
- The KJ updates the sheet manually, but often forgets until several songs late

## Phase 1: Sheet Integration in KJ Controller (THIS PR)

Add a "Rotation" panel below Playback Controls that reads from and writes to the Google Sheet.

### Backend

**New module: `kj-controller/rotation.py`**
- Uses Google Sheets API via `gspread` + `google-auth` (service account)
- `get_rotation()` — fetch all non-done rows (returns list of dicts with row_index, singer, song_artist, status, notes)
- `update_status(row_index, new_status)` — update the Status cell for a given row
- `add_entry(singer, song_artist)` — append a new row with timestamp
- Caches the sheet data for ~10s to avoid hammering the API during polling
- Config: `rotation_sheet_id` and `rotation_credentials_file` in config.json

**New routes in `routes.py`:**
- `GET /rotation` — returns current rotation queue (non-done entries)
- `POST /rotation/status` — update a row's status (`{row_index, status}`)
- `POST /rotation/add` — add a new singer entry (`{singer, song_artist}`)

### Frontend

**New "Rotation" panel in `index.html`** (column 1, below Playback Controls):
- Shows next ~8 entries from the rotation
- Each entry shows: position number, singer name, song & artist, status badge
- Status badges: "Singing" (green), "Next" (orange), "Queued" (gray), "WIP" (red)
- Action buttons per row:
  - "Singing" — mark as currently singing (sets status to "Singing Now")
  - "Done" — mark as completed (sets status to "Done")
  - "Next" — mark as next up
- "Add Singer" button opens inline form (singer name + song & artist)
- Auto-refreshes every 10s (separate from the 2s status poll)
- Manual refresh button

**New JS in `app.js`:**
- `fetchRotation()` — GET /rotation, render the list
- `updateRotationStatus(rowIndex, status)` — POST /rotation/status
- `addRotationEntry()` — POST /rotation/add from the form
- 10s polling interval for rotation data

### Dependencies

Add to `requirements.txt`:
- `gspread` (Google Sheets Python library)
- `google-auth` (service account auth — already a dep of gspread)

### Setup Required

1. Create a GCP service account (in the existing `nomadkaraoke` GCP project)
2. Download the JSON key file to NomadPC (e.g., `~/kjdata/rotation-sa-key.json`)
3. Share the Google Sheet with the service account email (Editor access)
4. Add to `config.json`:
   ```json
   {
     "rotation_sheet_id": "1OzNxqJB-pYHhI0VJkkPjJc1Ba242TL6Kadov52GHWl8",
     "rotation_credentials_file": "~/kjdata/rotation-sa-key.json"
   }
   ```

## Phase 2: Full Rotation Management (FUTURE — separate PR)

Replace the Google Sheet entirely with built-in rotation management:
- SQLite or JSON-based rotation storage
- Full CRUD for rotation entries (add, edit, reorder, delete)
- Drag-and-drop reorder
- Singer history / stats (songs sung, last time sung)
- Auto-suggest returning singers
- "On Deck" display overlay (replaces Conky widget)
- Auto-advance rotation when song ends
- Integration with song catalog (link rotation entries to actual files)
- QR code sign-up form for singers

This would fully replace the Google Sheet + Conky widget setup.

## Implementation Order

1. Create `rotation.py` module with Sheet API logic
2. Add routes to `routes.py`
3. Add HTML panel to `index.html`
4. Add JS logic to `app.js`
5. Add CSS styles to `style.css`
6. Add tests
7. Update requirements.txt
8. Test locally, then deploy
