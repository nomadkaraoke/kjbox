# Rotation Undo/Redo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add undo/redo to all rotation mutations so accidental clicks (e.g., "Done" on the wrong singer) can be reversed instantly.

**Architecture:** Snapshot-based undo. Before each frontend mutation, deep-copy the current `rotationData` array onto an undo stack (max 10). Undo/redo POST the saved snapshot to a new `/rotation/restore` backend endpoint that atomically replaces all rotation entries. Two buttons in the rotation header (disabled when stack is empty).

**Tech Stack:** Vanilla JS (frontend), Flask + SQLite (backend), pytest (tests)

**Spec:** `docs/archive/2026-04-04-rotation-undo-redo-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `kj-controller/rotation_store.py` | Modify | Add `restore_entries()` method |
| `kj-controller/rotation.py` | Modify | Add `restore_entries()` delegation |
| `kj-controller/routes.py` | Modify | Add `POST /rotation/restore` route |
| `kj-controller/static/app.js` | Modify | Undo stack object, snapshot capture in all mutation functions, undo/redo buttons |
| `kj-controller/templates/index.html` | Modify | Undo/Redo button HTML in rotation header |
| `kj-controller/static/style.css` | Modify | Undo/Redo button styles |
| `kj-controller/tests/unit/test_rotation_store.py` | Modify | Tests for `restore_entries()` |
| `kj-controller/tests/integration/test_rotation_routes.py` | Modify | Tests for `/rotation/restore` route |

---

### Task 1: `RotationStore.restore_entries()` — Tests

**Files:**
- Modify: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write tests for `restore_entries()`**

Add a new test class at the end of `kj-controller/tests/unit/test_rotation_store.py`:

```python
class TestRestoreEntries:
    """Tests for restore_entries() — atomic snapshot restore."""

    def test_restore_replaces_all_entries(self, store):
        """Restoring a snapshot replaces all current entries."""
        store.add_entry("Alice", "Song A")
        store.add_entry("Bob", "Song B")

        snapshot = [
            {"id": 10, "singer": "Xavier", "song_artist": "Song X", "status": "Waiting",
             "notes": "", "position": 1, "file_path": None, "duration": None,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None},
            {"id": 11, "singer": "Yolanda", "song_artist": "Song Y", "status": "Now Singing",
             "notes": "", "position": 2, "file_path": "/path/y.mp4", "duration": 180,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None},
        ]
        store.restore_entries(snapshot)

        entries = store.get_entries(include_done=True)
        assert len(entries) == 2
        assert entries[0]["id"] == 10
        assert entries[0]["singer"] == "Xavier"
        assert entries[1]["id"] == 11
        assert entries[1]["singer"] == "Yolanda"
        assert entries[1]["status"] == "Now Singing"
        assert entries[1]["file_path"] == "/path/y.mp4"
        assert entries[1]["duration"] == 180

    def test_restore_preserves_original_ids(self, store):
        """Restored entries keep their original IDs, not new autoincrement values."""
        snapshot = [
            {"id": 42, "singer": "Zara", "song_artist": "Song Z", "status": "Waiting",
             "notes": "test", "position": 1, "file_path": None, "duration": None,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None},
        ]
        store.restore_entries(snapshot)

        entry = store.get_entry(42)
        assert entry is not None
        assert entry["singer"] == "Zara"
        assert entry["notes"] == "test"

    def test_restore_to_empty(self, store):
        """Restoring an empty snapshot clears the rotation."""
        store.add_entry("Alice", "Song A")
        store.add_entry("Bob", "Song B")

        store.restore_entries([])

        entries = store.get_entries(include_done=True)
        assert len(entries) == 0

    def test_restore_fewer_entries(self, store):
        """Restoring fewer entries than current removes the extras."""
        store.add_entry("Alice", "Song A")
        store.add_entry("Bob", "Song B")
        store.add_entry("Carol", "Song C")

        snapshot = [
            {"id": 1, "singer": "Alice", "song_artist": "Song A", "status": "Done",
             "notes": "", "position": 1, "file_path": None, "duration": None,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None},
        ]
        store.restore_entries(snapshot)

        entries = store.get_entries(include_done=True)
        assert len(entries) == 1
        assert entries[0]["singer"] == "Alice"
        assert entries[0]["status"] == "Done"

    def test_restore_more_entries(self, store):
        """Restoring more entries than current adds the new ones."""
        store.add_entry("Alice", "Song A")

        snapshot = [
            {"id": 1, "singer": "Alice", "song_artist": "Song A", "status": "Waiting",
             "notes": "", "position": 1, "file_path": None, "duration": None,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None},
            {"id": 2, "singer": "Bob", "song_artist": "Song B", "status": "Up Next",
             "notes": "", "position": 2, "file_path": None, "duration": None,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None},
            {"id": 3, "singer": "Carol", "song_artist": "Song C", "status": "Waiting",
             "notes": "", "position": 3, "file_path": None, "duration": None,
             "download_source": None, "download_status": None, "download_id": None,
             "url_fallback": None, "gen_job_id": None, "gen_status": None},
        ]
        store.restore_entries(snapshot)

        entries = store.get_entries(include_done=True)
        assert len(entries) == 3

    def test_restore_preserves_download_and_gen_fields(self, store):
        """All download/gen tracking fields survive a restore."""
        snapshot = [
            {"id": 5, "singer": "Dan", "song_artist": "Song D", "status": "Waiting",
             "notes": "", "position": 1, "file_path": "/path/d.mp4", "duration": 200,
             "download_source": "youtube", "download_status": "complete",
             "download_id": "dl-123", "url_fallback": "https://example.com/d",
             "gen_job_id": "gen-456", "gen_status": "complete"},
        ]
        store.restore_entries(snapshot)

        entry = store.get_entry(5)
        assert entry["download_source"] == "youtube"
        assert entry["download_status"] == "complete"
        assert entry["download_id"] == "dl-123"
        assert entry["url_fallback"] == "https://example.com/d"
        assert entry["gen_job_id"] == "gen-456"
        assert entry["gen_status"] == "complete"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py::TestRestoreEntries -v`
Expected: FAIL — `AttributeError: 'RotationStore' object has no attribute 'restore_entries'`

- [ ] **Step 3: Commit test file**

```bash
git add kj-controller/tests/unit/test_rotation_store.py
git commit -m "test: add failing tests for RotationStore.restore_entries()"
```

---

### Task 2: `RotationStore.restore_entries()` — Implementation

**Files:**
- Modify: `kj-controller/rotation_store.py` (add method after `get_all_entries()`, around line 498)

- [ ] **Step 1: Implement `restore_entries()`**

Add this method at the end of the `RotationStore` class in `kj-controller/rotation_store.py`, after `get_all_entries()`:

```python
    def restore_entries(self, entries):
        """Atomically replace all rotation entries with the given snapshot.

        Used by the undo/redo system. Preserves original entry IDs.
        Each entry dict must have: id, singer, song_artist, status, notes,
        position, file_path, duration, download_source, download_status,
        download_id, url_fallback, gen_job_id, gen_status.
        """
        conn = self._get_conn()
        conn.execute("DELETE FROM rotation_entries")
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name = 'rotation_entries'"
        )
        for e in entries:
            conn.execute(
                "INSERT INTO rotation_entries "
                "(id, singer, song_artist, status, notes, position, "
                " file_path, duration, download_source, download_status, "
                " download_id, url_fallback, gen_job_id, gen_status, "
                " updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "        datetime('now', 'localtime'))",
                (
                    e["id"], e["singer"], e["song_artist"], e["status"],
                    e.get("notes", ""), e["position"],
                    e.get("file_path"), e.get("duration"),
                    e.get("download_source"), e.get("download_status"),
                    e.get("download_id"), e.get("url_fallback"),
                    e.get("gen_job_id"), e.get("gen_status"),
                ),
            )
        conn.commit()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py::TestRestoreEntries -v`
Expected: All 6 tests PASS

- [ ] **Step 3: Run full rotation store test suite (no regressions)**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add kj-controller/rotation_store.py
git commit -m "feat: add RotationStore.restore_entries() for undo/redo"
```

---

### Task 3: `RotationManager.restore_entries()` and Route — Tests

**Files:**
- Modify: `kj-controller/tests/integration/test_rotation_routes.py`

- [ ] **Step 1: Write route tests for `/rotation/restore`**

Add at the end of `kj-controller/tests/integration/test_rotation_routes.py`:

```python
class TestRestoreRoute:
    """Tests for POST /rotation/restore."""

    def test_restore_success(self, rotation_client, mock_rotation):
        """Restore endpoint calls restore_entries and returns updated entries."""
        mock_rotation.restore_entries.return_value = None
        mock_rotation.get_rotation.return_value = SAMPLE_ENTRIES

        resp = rotation_client.post('/rotation/restore', json={
            'entries': SAMPLE_ENTRIES,
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert 'entries' in data
        mock_rotation.restore_entries.assert_called_once_with(SAMPLE_ENTRIES)

    def test_restore_missing_entries_field(self, rotation_client):
        """Missing entries field returns 400."""
        resp = rotation_client.post('/rotation/restore', json={})
        assert resp.status_code == 400
        assert 'entries' in resp.get_json()['error'].lower()

    def test_restore_entries_not_list(self, rotation_client):
        """Non-list entries field returns 400."""
        resp = rotation_client.post('/rotation/restore', json={'entries': 'not a list'})
        assert resp.status_code == 400

    def test_restore_no_rotation(self, no_rotation_client):
        """Returns 503 when rotation is not configured."""
        resp = no_rotation_client.post('/rotation/restore', json={'entries': []})
        assert resp.status_code == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/integration/test_rotation_routes.py::TestRestoreRoute -v`
Expected: FAIL — 404 (route does not exist yet)

- [ ] **Step 3: Commit**

```bash
git add kj-controller/tests/integration/test_rotation_routes.py
git commit -m "test: add failing tests for /rotation/restore route"
```

---

### Task 4: `RotationManager.restore_entries()` and Route — Implementation

**Files:**
- Modify: `kj-controller/rotation.py` (add method in mutation section, around line 176)
- Modify: `kj-controller/routes.py` (add route after `/rotation/unlink`, around line 1935)

- [ ] **Step 1: Add `restore_entries()` to RotationManager**

Add this method in `kj-controller/rotation.py` after `restore_from_sheet()` (around line 196), inside the mutation methods section:

```python
    def restore_entries(self, entries):
        """Atomically replace rotation with a snapshot (undo/redo support)."""
        self.store.restore_entries(entries)
        self._after_mutation()
```

- [ ] **Step 2: Add the `/rotation/restore` route**

Add this route in `kj-controller/routes.py` after the `/rotation/unlink` route (around line 1935):

```python
@routes_bp.route('/rotation/restore', methods=['POST'])
def restore_rotation():
    """Restore rotation from a snapshot (undo/redo support)."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    entries = data.get('entries')
    if entries is None:
        return jsonify({"error": "entries is required"}), 400
    if not isinstance(entries, list):
        return jsonify({"error": "entries must be a list"}), 400

    try:
        rotation.restore_entries(entries)
        updated = rotation.get_rotation()
        _add_time_estimates(updated)
        return jsonify({"success": True, "entries": updated})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 3: Run route tests to verify they pass**

Run: `cd kj-controller && python -m pytest tests/integration/test_rotation_routes.py::TestRestoreRoute -v`
Expected: All 4 tests PASS

- [ ] **Step 4: Run full test suite (no regressions)**

Run: `cd kj-controller && python -m pytest -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation.py kj-controller/routes.py
git commit -m "feat: add /rotation/restore endpoint for undo/redo"
```

---

### Task 5: Frontend — Undo Stack and Mutation Wiring

**Files:**
- Modify: `kj-controller/static/app.js`

- [ ] **Step 1: Add the undo history object**

Add this at the top of `kj-controller/static/app.js`, after the `logArea` declaration (after line 5):

```javascript
// --- Undo/Redo History ---

const rotationHistory = {
    undoStack: [],
    redoStack: [],
    maxSize: 10,

    pushUndo(snapshot) {
        this.undoStack.push(JSON.parse(JSON.stringify(snapshot)));
        if (this.undoStack.length > this.maxSize) this.undoStack.shift();
        this.redoStack = [];
        this.updateButtons();
    },

    async undo() {
        if (this.undoStack.length === 0) return;
        this.redoStack.push(JSON.parse(JSON.stringify(rotationData)));
        const snapshot = this.undoStack.pop();
        await this._restore(snapshot);
    },

    async redo() {
        if (this.redoStack.length === 0) return;
        this.undoStack.push(JSON.parse(JSON.stringify(rotationData)));
        const snapshot = this.redoStack.pop();
        await this._restore(snapshot);
    },

    async _restore(snapshot) {
        showRotationIndicator('spin');
        try {
            const response = await fetch('/rotation/restore', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ entries: snapshot }),
            });
            const data = await response.json();
            if (!response.ok) {
                showRotationIndicator('error');
                return;
            }
            if (data.entries) {
                rotationData = data.entries;
                renderRotation(rotationData);
            }
            showRotationIndicator('success');
        } catch (e) {
            showRotationIndicator('error');
        }
        this.updateButtons();
    },

    updateButtons() {
        const undoBtn = document.getElementById('rotation-undo-btn');
        const redoBtn = document.getElementById('rotation-redo-btn');
        if (undoBtn) {
            undoBtn.disabled = this.undoStack.length === 0;
            undoBtn.title = this.undoStack.length > 0
                ? `Undo (${this.undoStack.length} remaining)`
                : 'Nothing to undo';
        }
        if (redoBtn) {
            redoBtn.disabled = this.redoStack.length === 0;
            redoBtn.title = this.redoStack.length > 0
                ? `Redo (${this.redoStack.length} remaining)`
                : 'Nothing to redo';
        }
    },
};
```

- [ ] **Step 2: Add `rotationHistory.pushUndo(rotationData)` to each mutation function**

In `kj-controller/static/app.js`, add `rotationHistory.pushUndo(rotationData);` as the first line inside each of these functions (before `showRotationIndicator('spin')` or any other logic):

**`updateRotationStatus` (line 3332):**
```javascript
async function updateRotationStatus(entryId, status) {
    rotationHistory.pushUndo(rotationData);
    showRotationIndicator('spin');
```

**`advanceRotationStatus` (line 3322):** This calls `updateRotationStatus` twice, which would double-push. Instead, push once here and skip the push inside `updateRotationStatus` by adding a flag parameter:

Change `updateRotationStatus` signature to accept an optional `skipUndo` parameter:
```javascript
async function updateRotationStatus(entryId, status, { skipUndo = false } = {}) {
    if (!skipUndo) rotationHistory.pushUndo(rotationData);
    showRotationIndicator('spin');
```

Then change `advanceRotationStatus`:
```javascript
async function advanceRotationStatus(entry, idx, entries) {
    rotationHistory.pushUndo(rotationData);
    await updateRotationStatus(entry.id, 'Now Singing', { skipUndo: true });
    const nextEntry = entries[idx + 1];
    if (nextEntry) {
        await updateRotationStatus(nextEntry.id, 'Up Next', { skipUndo: true });
    }
}
```

**`deleteRotationEntry` (line 3267):** Add after the confirm check:
```javascript
async function deleteRotationEntry(entryId, singerName) {
    if (!confirm(`Delete "${singerName}" from rotation?`)) return;
    rotationHistory.pushUndo(rotationData);
    showRotationIndicator('spin');
```

**`moveRotationEntry` (line 3355):**
```javascript
async function moveRotationEntry(entryId, newPosition) {
    rotationHistory.pushUndo(rotationData);
    showRotationIndicator('spin');
```

**`addRotationEntry` (line 3459):** Add after the empty-singer check:
```javascript
async function addRotationEntry() {
    const singerInput = document.getElementById('rotation-singer');
    const songInput = document.getElementById('rotation-song');
    const singer = singerInput.value.trim();
    const songArtist = songInput.value.trim();
    if (!singer) return;

    rotationHistory.pushUndo(rotationData);
    showRotationIndicator('spin');
```

**`saveRotationEdit` (line 3241):**
```javascript
async function saveRotationEdit(entryId, singer, songArtist) {
    rotationHistory.pushUndo(rotationData);
    showRotationIndicator('spin');
```

**Unlink handler (inline, around line 3113):** Inside the `unlinkItem.onclick` handler:
```javascript
unlinkItem.onclick = async (ev) => {
    ev.stopPropagation();
    dropdown.remove();
    rotationHistory.pushUndo(rotationData);
    try {
```

**`selectRotSearchResult` (line 3695):** This handles linking files to entries (edit + link in one flow). Add after `hideRotSearchDropdown()`, before `showRotationIndicator('spin')`:
```javascript
async function selectRotSearchResult(result) {
    const singerInput = document.getElementById('rotation-singer');
    const songInput = document.getElementById('rotation-song');
    const form = document.getElementById('rotation-add-form');
    const linkTargetId = form ? form.dataset.linkTargetId : null;

    // In link mode, we don't need a singer name (already exists)
    if (!linkTargetId) {
        const singer = singerInput ? singerInput.value.trim() : '';
        if (!singer) { if (singerInput) singerInput.focus(); return; }
    }

    hideRotSearchDropdown();
    rotationHistory.pushUndo(rotationData);
    showRotationIndicator('spin');
```

- [ ] **Step 3: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat: add undo stack and wire into all rotation mutations"
```

---

### Task 6: Frontend — Undo/Redo Buttons (HTML + CSS)

**Files:**
- Modify: `kj-controller/templates/index.html` (line 58-65, rotation header)
- Modify: `kj-controller/static/style.css`

- [ ] **Step 1: Add Undo/Redo buttons to the rotation header HTML**

In `kj-controller/templates/index.html`, modify the `rotation-header-btns` div (line 58). Insert the undo/redo buttons after the indicator span and before the Restore button:

Change:
```html
                    <div class="rotation-header-btns">
                        <span id="rotation-sync-dot" class="rotation-sync-dot" title="Sync status"></span>
                        <span id="rotation-indicator" class="rotation-indicator hidden"></span>
                        <button class="rotation-restore-btn" onclick="restoreFromSheet()" title="Restore from Google Sheet backup">Restore</button>
```

To:
```html
                    <div class="rotation-header-btns">
                        <span id="rotation-sync-dot" class="rotation-sync-dot" title="Sync status"></span>
                        <span id="rotation-indicator" class="rotation-indicator hidden"></span>
                        <button id="rotation-undo-btn" class="rotation-undo-btn" onclick="rotationHistory.undo()" title="Nothing to undo" disabled>&#x21A9;</button>
                        <button id="rotation-redo-btn" class="rotation-redo-btn" onclick="rotationHistory.redo()" title="Nothing to redo" disabled>&#x21AA;</button>
                        <button class="rotation-restore-btn" onclick="restoreFromSheet()" title="Restore from Google Sheet backup">Restore</button>
```

- [ ] **Step 2: Add CSS styles for the buttons**

In `kj-controller/static/style.css`, add after the `.rotation-restore-btn` block (after line 1914):

```css
.rotation-undo-btn, .rotation-redo-btn {
    font-size: 0.85em;
    background: transparent;
    color: #8892a4;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 2px 8px;
    cursor: pointer;
    line-height: 1;
}
.rotation-undo-btn:hover:not(:disabled), .rotation-redo-btn:hover:not(:disabled) {
    background: #333;
    color: #fff;
    border-color: #666;
}
.rotation-undo-btn:disabled, .rotation-redo-btn:disabled {
    opacity: 0.3;
    cursor: default;
}
```

- [ ] **Step 3: Verify the buttons render correctly**

Open the app (or use the test server) and confirm:
- Two buttons appear in the rotation header between the indicator and "Restore"
- Both show as grayed out initially (disabled)
- Performing any rotation action enables the Undo button
- Clicking Undo reverts the action and enables the Redo button

- [ ] **Step 4: Commit**

```bash
git add kj-controller/templates/index.html kj-controller/static/style.css
git commit -m "feat: add undo/redo buttons to rotation header"
```

---

### Task 7: Final Integration Test and Cleanup

**Files:**
- All modified files (verification only)

- [ ] **Step 1: Run full test suite**

Run: `cd kj-controller && python -m pytest -v`
Expected: All tests PASS

- [ ] **Step 2: Run coverage check**

Run: `cd kj-controller && python -m pytest --cov --cov-report=term`
Expected: Coverage at or above existing baseline

- [ ] **Step 3: Verify JS syntax**

Run: `cd kj-controller && node --check static/app.js`
Expected: No syntax errors

- [ ] **Step 4: Commit (if any fixups needed)**

Only if previous steps required changes.
