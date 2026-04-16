# Multi-Singer Data Model + Pill Input UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce structured multi-singer support so the system properly tracks each individual singer across all their rotation entries, with a pill-based input UI for entering multiple singers per song.

**Architecture:** Add a `singers_json` TEXT column to `rotation_entries` storing a JSON array of individual singer names. The existing `singer` column stays as the display string. Backend counting unpacks the JSON to credit individuals. Frontend uses a pill input component (Tab/comma/& to create pills) and renders multi-singer rows with individual name pills.

**Tech Stack:** Python/Flask backend, SQLite, vanilla JS frontend, pytest + Playwright for testing

**Design spec:** `docs/archive/2026-04-14-multi-singer-data-model-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `kj-controller/rotation_store.py` | Modify | Schema migration, add_entry, update_entry, get_songs_sung_counts, restore_entries |
| `kj-controller/routes.py` | Modify | _add_songs_sung min-count logic, add/edit route singers param |
| `kj-controller/static/app.js` | Modify | Pill input component, renderRotation multi-singer pills, addRotationEntry, pill color swap |
| `kj-controller/static/style.css` | Modify | Pill color swap, new pill input + rotation singer pill classes |
| `kj-controller/templates/index.html` | Modify | Wrap singer input in pill container |
| `kj-controller/tests/unit/test_rotation_store.py` | Modify | TestSingersJson class |
| `kj-controller/tests/integration/test_rotation_routes.py` | Modify | Multi-singer route tests |
| `kj-controller/tests/e2e/test_rotation_e2e.py` | Create | Playwright e2e tests for pill input |

---

### Task 1: Schema Migration + Unit Tests

**Files:**
- Modify: `kj-controller/rotation_store.py:98-106` (migrations list)
- Test: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write failing test for schema migration**

Add to `test_rotation_store.py` after the last test class (after line 801):

```python
class TestSingersJson:
    def test_schema_has_singers_json_column(self, store):
        conn = store._get_conn()
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(rotation_entries)"
        ).fetchall()}
        assert "singers_json" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py::TestSingersJson::test_schema_has_singers_json_column -v`
Expected: FAIL — `singers_json` not in column set

- [ ] **Step 3: Add migration**

In `rotation_store.py`, add to the `migrations` list at line 106 (after the `paid` entry):

```python
        ("singers_json", "TEXT DEFAULT NULL"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py::TestSingersJson -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat: add singers_json column to rotation_entries schema"
```

---

### Task 2: add_entry() with singers list + Unit Tests

**Files:**
- Modify: `kj-controller/rotation_store.py:129-142` (add_entry method)
- Test: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write failing tests**

Add to the `TestSingersJson` class:

```python
    def test_add_entry_with_singers_list(self, store):
        entry = store.add_entry("ignored", singers=["Phil", "Anya"])
        assert entry["singer"] == "Phil & Anya"
        assert entry["singers_json"] == '["Phil", "Anya"]'

    def test_add_entry_with_single_singer_list(self, store):
        entry = store.add_entry("ignored", singers=["Sarah"])
        assert entry["singer"] == "Sarah"
        assert entry["singers_json"] == '["Sarah"]'

    def test_add_entry_without_singers_has_null_singers_json(self, store):
        entry = store.add_entry("Sarah")
        assert entry["singer"] == "Sarah"
        assert entry["singers_json"] is None

    def test_add_entry_singers_trims_whitespace(self, store):
        entry = store.add_entry("ignored", singers=["  Phil  ", " Anya "])
        assert entry["singer"] == "Phil & Anya"
        import json
        assert json.loads(entry["singers_json"]) == ["Phil", "Anya"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py::TestSingersJson -v`
Expected: FAIL — `add_entry() got an unexpected keyword argument 'singers'`

- [ ] **Step 3: Implement add_entry singers parameter**

In `rotation_store.py`, modify `add_entry` (line 129). Replace the full method:

```python
    def add_entry(self, singer, song_artist='', notes='', file_path=None, duration=None, singers=None):
        """Insert a new entry at max(position)+1 and return the new entry dict.

        If ``singers`` is provided (a list of names), it populates ``singers_json``
        and auto-generates the ``singer`` display string.
        """
        import json as _json
        singers_json = None
        if singers is not None:
            singers = [s.strip() for s in singers]
            singer = " & ".join(singers)
            singers_json = _json.dumps(singers)

        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO rotation_entries (singer, song_artist, notes, position, file_path, duration, singers_json) "
            "VALUES (?, ?, ?, (SELECT COALESCE(MAX(position), 0) + 1 FROM rotation_entries), ?, ?, ?)",
            (singer, song_artist, notes, file_path, duration, singers_json),
        )
        conn.commit()
        return self._row_to_dict(
            conn.execute(
                "SELECT * FROM rotation_entries WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py::TestSingersJson -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Run full rotation store test suite for regressions**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat: add_entry accepts singers list, populates singers_json"
```

---

### Task 3: update_entry() with singers list + Unit Tests

**Files:**
- Modify: `kj-controller/rotation_store.py:174-195` (update_entry method)
- Test: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write failing tests**

Add to `TestSingersJson`:

```python
    def test_update_entry_with_singers(self, store):
        entry = store.add_entry("Phil", singers=["Phil"])
        updated = store.update_entry(entry["id"], singers=["Phil", "Anya"])
        assert updated["singer"] == "Phil & Anya"
        assert updated["singers_json"] == '["Phil", "Anya"]'

    def test_update_entry_without_singers_preserves_singers_json(self, store):
        entry = store.add_entry("ignored", singers=["Phil", "Anya"])
        updated = store.update_entry(entry["id"], song_artist="New Song")
        assert updated["singers_json"] == '["Phil", "Anya"]'
        assert updated["singer"] == "Phil & Anya"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py::TestSingersJson::test_update_entry_with_singers -v`
Expected: FAIL — `update_entry() got an unexpected keyword argument 'singers'`

- [ ] **Step 3: Implement update_entry singers parameter**

In `rotation_store.py`, replace the `update_entry` method (line 174):

```python
    def update_entry(self, entry_id, singer=None, song_artist=None, singers=None):
        """Edit singer, song_artist, and/or singers fields.

        If ``singers`` is provided, updates both ``singers_json`` and the
        ``singer`` display string.

        Raises ValueError if entry_id not found.
        Returns updated entry dict.
        """
        import json as _json
        existing = self.get_entry(entry_id)
        if existing is None:
            raise ValueError(f"Entry {entry_id} not found")

        if singers is not None:
            singers = [s.strip() for s in singers]
            new_singer = " & ".join(singers)
            new_singers_json = _json.dumps(singers)
        else:
            new_singer = singer if singer is not None else existing["singer"]
            new_singers_json = existing["singers_json"]

        new_song_artist = song_artist if song_artist is not None else existing["song_artist"]

        conn = self._get_conn()
        conn.execute(
            "UPDATE rotation_entries "
            "SET singer = ?, song_artist = ?, singers_json = ?, "
            "    updated_at = datetime('now', 'localtime') "
            "WHERE id = ?",
            (new_singer, new_song_artist, new_singers_json, entry_id),
        )
        conn.commit()
        return self.get_entry(entry_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py::TestSingersJson -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat: update_entry accepts singers list"
```

---

### Task 4: get_songs_sung_counts() unpack JSON + Unit Tests

**Files:**
- Modify: `kj-controller/rotation_store.py:302-314` (get_songs_sung_counts)
- Test: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write failing tests**

Add to `TestSingersJson`:

```python
    def test_get_songs_sung_counts_unpacks_json(self, store):
        e = store.add_entry("ignored", song_artist="Duet Song", singers=["Phil", "Anya"])
        store.update_status(e["id"], "Done")
        counts = store.get_songs_sung_counts()
        assert counts["phil"] == 1
        assert counts["anya"] == 1

    def test_get_songs_sung_counts_mixed_legacy_and_json(self, store):
        # Legacy entry (no singers_json)
        e1 = store.add_entry("Phil")
        store.update_status(e1["id"], "Done")
        # New structured entry
        e2 = store.add_entry("ignored", singers=["Phil", "Anya"])
        store.update_status(e2["id"], "Done")
        counts = store.get_songs_sung_counts()
        assert counts["phil"] == 2  # 1 legacy + 1 structured
        assert counts["anya"] == 1

    def test_get_songs_sung_counts_ignores_non_done(self, store):
        store.add_entry("ignored", singers=["Phil", "Anya"])  # still Waiting
        e2 = store.add_entry("ignored", singers=["Phil"])
        store.update_status(e2["id"], "Done")
        counts = store.get_songs_sung_counts()
        assert counts["phil"] == 1
        assert "anya" not in counts
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py::TestSingersJson::test_get_songs_sung_counts_unpacks_json -v`
Expected: FAIL — `"phil & anya"` in counts but not `"phil"` or `"anya"` individually

- [ ] **Step 3: Rewrite get_songs_sung_counts()**

In `rotation_store.py`, replace the method (line 302):

```python
    def get_songs_sung_counts(self):
        """Return a dict mapping singer name -> count of 'done' entries tonight.

        For entries with singers_json, credits each individual singer.
        For legacy entries (singers_json is NULL), credits the singer string as-is.
        Case-insensitive matching (lowered keys).
        """
        import json as _json
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT singer, singers_json FROM rotation_entries "
            "WHERE LOWER(status) = 'done'"
        ).fetchall()
        counts = {}
        for row in rows:
            if row["singers_json"]:
                names = _json.loads(row["singers_json"])
            else:
                names = [row["singer"]]
            for name in names:
                key = name.strip().lower()
                counts[key] = counts.get(key, 0) + 1
        return counts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py::TestSingersJson -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite for regressions**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py tests/integration/test_rotation_routes.py -v`
Expected: All PASS (existing songs_sung tests still work because legacy entries have null singers_json)

- [ ] **Step 6: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat: get_songs_sung_counts unpacks singers_json for individual credit"
```

---

### Task 5: restore_entries() includes singers_json + Unit Test

**Files:**
- Modify: `kj-controller/rotation_store.py:535-571` (restore_entries)
- Test: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write failing test**

Add to `TestSingersJson`:

```python
    def test_restore_entries_preserves_singers_json(self, store):
        e = store.add_entry("ignored", singers=["Phil", "Anya"])
        snapshot = store.get_all_entries()
        store.add_entry("Extra")  # change state
        store.restore_entries(snapshot)
        entries = store.get_all_entries()
        assert len(entries) == 1
        assert entries[0]["singers_json"] == '["Phil", "Anya"]'
        assert entries[0]["singer"] == "Phil & Anya"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py::TestSingersJson::test_restore_entries_preserves_singers_json -v`
Expected: FAIL — `singers_json` not in the INSERT or missing from the restore

- [ ] **Step 3: Update restore_entries()**

In `rotation_store.py`, update the `restore_entries` method. Replace the INSERT statement (around line 551) to include `singers_json`:

```python
    def restore_entries(self, entries):
        """Atomically replace all rotation entries with the given snapshot.

        Used by the undo/redo system. Preserves original entry IDs.
        Each entry dict must have: id, singer, song_artist, status, notes,
        position, file_path, duration, download_source, download_status,
        download_id, url_fallback, gen_job_id, gen_status.
        Optional: singers_json.
        """
        conn = self._get_conn()
        try:
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
                    " singers_json, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "        datetime('now', 'localtime'))",
                    (
                        e["id"], e["singer"], e["song_artist"], e["status"],
                        e.get("notes", ""), e["position"],
                        e.get("file_path"), e.get("duration"),
                        e.get("download_source"), e.get("download_status"),
                        e.get("download_id"), e.get("url_fallback"),
                        e.get("gen_job_id"), e.get("gen_status"),
                        e.get("singers_json"),
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py::TestSingersJson -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat: restore_entries preserves singers_json for undo/redo"
```

---

### Task 6: Route changes — /rotation/add and /rotation/edit + Integration Tests

**Files:**
- Modify: `kj-controller/routes.py:1924-1949` (add route), `kj-controller/routes.py:1863-1893` (edit route)
- Test: `kj-controller/tests/integration/test_rotation_routes.py`

- [ ] **Step 1: Write failing integration tests**

Add to `test_rotation_routes.py` after the existing test classes:

```python
class TestMultiSingerAdd:
    def test_add_with_singers_array(self, rotation_client, mock_rotation):
        mock_rotation.add_entry.return_value = {
            "id": 10, "singer": "Phil & Anya", "singers_json": '["Phil", "Anya"]',
            "position": 4, "status": "Waiting",
        }
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"singers": ["Phil", "Anya"], "song_artist": "Duet Song"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.add_entry.assert_called_once()
        call_kwargs = mock_rotation.add_entry.call_args
        assert call_kwargs[1].get("singers") == ["Phil", "Anya"]

    def test_add_without_singers_backward_compat(self, rotation_client, mock_rotation):
        mock_rotation.add_entry.return_value = {
            "id": 10, "singer": "Sarah", "singers_json": None,
            "position": 4, "status": "Waiting",
        }
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"singer": "Sarah", "song_artist": "My Song"}),
            content_type='application/json')
        assert resp.status_code == 200
        call_kwargs = mock_rotation.add_entry.call_args
        assert call_kwargs[1].get("singers") is None


class TestMultiSingerEdit:
    def test_edit_with_singers_array(self, rotation_client, mock_rotation):
        mock_rotation.update_entry.return_value = {
            "id": 1, "singer": "Phil & Anya", "singers_json": '["Phil", "Anya"]',
        }
        resp = rotation_client.post('/rotation/edit',
            data=json.dumps({"id": 1, "singers": ["Phil", "Anya"]}),
            content_type='application/json')
        assert resp.status_code == 200
        call_kwargs = mock_rotation.update_entry.call_args
        assert call_kwargs[1].get("singers") == ["Phil", "Anya"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/integration/test_rotation_routes.py::TestMultiSingerAdd -v`
Expected: FAIL — route does not pass `singers` to `add_entry`

- [ ] **Step 3: Update add route**

In `routes.py`, modify `add_rotation_entry()` (around line 1930). After the existing `singer = data.get('singer', '').strip()` line, add singers handling:

```python
    singers = data.get('singers')
    singer = data.get('singer', '').strip()
    song_artist = data.get('song_artist', '').strip()
    notes = data.get('notes', '').strip()
    if singers:
        if not isinstance(singers, list) or not all(isinstance(s, str) for s in singers):
            return jsonify({"error": "singers must be a list of strings"}), 400
        if not singer:
            singer = " & ".join(s.strip() for s in singers)
    if not singer and not singers:
        return jsonify({"error": "singer or singers is required"}), 400
```

And update the `add_entry` call to pass `singers`:

```python
        entry = rotation.add_entry(singer, song_artist, notes, file_path=file_path, singers=singers if singers else None)
```

- [ ] **Step 4: Update edit route**

In `routes.py`, modify `edit_rotation_entry()` (around line 1879). Add singers handling:

```python
    singer = data.get('singer')
    song_artist = data.get('song_artist')
    singers = data.get('singers')
    if singer is not None:
        singer = singer.strip() or None
    if song_artist is not None:
        song_artist = song_artist.strip() or None
    if singers is not None:
        if not isinstance(singers, list) or not all(isinstance(s, str) for s in singers):
            return jsonify({"error": "singers must be a list of strings"}), 400
```

Update the `update_entry` call:

```python
        result = rotation.update_entry(entry_id, singer=singer, song_artist=song_artist, singers=singers)
```

- [ ] **Step 5: Update RotationManager.add_entry and update_entry to pass through singers**

In `rotation.py`, update `add_entry` (line 77) to accept and forward `singers`:

```python
    def add_entry(self, singer, song_artist='', notes='', file_path=None, duration=None, singers=None):
        """Add a new singer entry and return the entry dict."""
        if file_path and duration is None:
            duration = self._lookup_duration(file_path)
        result = self.store.add_entry(singer, song_artist, notes, file_path=file_path, duration=duration, singers=singers)
        self._after_mutation()
        return result
```

Update `update_entry` (line 85) similarly:

```python
    def update_entry(self, entry_id, singer=None, song_artist=None, singers=None):
        """Update singer name and/or song for entry_id. Returns updated entry."""
        result = self.store.update_entry(entry_id, singer=singer, song_artist=song_artist, singers=singers)
        self._after_mutation()
        return result
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/integration/test_rotation_routes.py::TestMultiSingerAdd tests/integration/test_rotation_routes.py::TestMultiSingerEdit -v`
Expected: All PASS

- [ ] **Step 7: Run full integration test suite for regressions**

Run: `cd kj-controller && pytest tests/integration/test_rotation_routes.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add kj-controller/routes.py kj-controller/rotation.py kj-controller/tests/integration/test_rotation_routes.py
git commit -m "feat: /rotation/add and /rotation/edit accept singers array"
```

---

### Task 7: _add_songs_sung() min-count for multi-singer entries + Integration Test

**Files:**
- Modify: `kj-controller/routes.py:1792-1796` (_add_songs_sung)
- Test: `kj-controller/tests/integration/test_rotation_routes.py`

- [ ] **Step 1: Write failing test**

Add to `test_rotation_routes.py`:

```python
class TestMultiSingerSongsSung:
    def test_songs_sung_min_for_multi_singer(self, rotation_client, mock_rotation):
        """Multi-singer entry shows minimum songs_sung across its singers."""
        mock_rotation.store.get_songs_sung_counts.return_value = {"phil": 3, "anya": 1}
        mock_rotation.get_rotation.return_value = [
            {"id": 1, "position": 1, "singer": "Phil & Anya",
             "singers_json": '["Phil", "Anya"]', "status": "Waiting",
             "song_artist": "Duet", "notes": "", "file_path": None, "duration": None},
        ]
        resp = rotation_client.get('/rotation')
        entries = resp.get_json()['entries']
        assert entries[0]['songs_sung'] == 1  # min(phil=3, anya=1)

    def test_songs_sung_legacy_entry_unchanged(self, rotation_client, mock_rotation):
        """Legacy entries (singers_json=null) use singer string as before."""
        mock_rotation.store.get_songs_sung_counts.return_value = {"sarah": 2}
        mock_rotation.get_rotation.return_value = [
            {"id": 1, "position": 1, "singer": "Sarah",
             "singers_json": None, "status": "Waiting",
             "song_artist": "Song", "notes": "", "file_path": None, "duration": None},
        ]
        resp = rotation_client.get('/rotation')
        entries = resp.get_json()['entries']
        assert entries[0]['songs_sung'] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/integration/test_rotation_routes.py::TestMultiSingerSongsSung::test_songs_sung_min_for_multi_singer -v`
Expected: FAIL — `_add_songs_sung` uses `entry["singer"].lower()` not `singers_json`

- [ ] **Step 3: Update _add_songs_sung()**

In `routes.py`, replace the `_add_songs_sung` function (line 1792):

```python
def _add_songs_sung(entries, rotation):
    """Add songs_sung field to each entry.

    For multi-singer entries (singers_json not null), uses the minimum count
    across all singers in the group — fairest to the least-served person.
    For legacy entries, matches on the singer display string.
    """
    import json as _json
    counts = rotation.store.get_songs_sung_counts()
    for entry in entries:
        singers_json = entry.get("singers_json")
        if singers_json:
            names = _json.loads(singers_json)
            individual_counts = [counts.get(n.strip().lower(), 0) for n in names]
            entry["songs_sung"] = min(individual_counts) if individual_counts else 0
        else:
            entry["songs_sung"] = counts.get(entry["singer"].lower(), 0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/integration/test_rotation_routes.py::TestMultiSingerSongsSung -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite for regressions**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py tests/integration/test_rotation_routes.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_rotation_routes.py
git commit -m "feat: songs_sung uses min count for multi-singer entries"
```

---

### Task 8: Swap pill colors (green=new, red=many) — Frontend + CSS

**Files:**
- Modify: `kj-controller/static/app.js:3108-3128` (pill rendering)
- Modify: `kj-controller/static/style.css:2090-2093` (pill classes)

- [ ] **Step 1: Update pill color classes in CSS**

In `style.css`, replace the pill color classes (around line 2090):

```css
.pill-new  { background: #2d8a4e33; color: #2d8a4e; }
.pill-once { background: #f59e0b33; color: #f59e0b; }
.pill-few  { background: #d4720a33; color: #d4720a; }
.pill-many { background: #e74c3c33; color: #e74c3c; }
```

- [ ] **Step 2: Update pill threshold logic in JS**

In `app.js`, find the songs-sung pill rendering (around line 3108). Replace the threshold block:

```javascript
        const pill = document.createElement('span');
        pill.className = 'rotation-songs-pill';
        const sung = entry.songs_sung || 0;
        if (sung === 0) {
            pill.classList.add('pill-new');
            pill.textContent = 'NEW';
            pill.title = 'Hasn\u2019t sung yet tonight';
        } else if (sung === 1) {
            pill.classList.add('pill-once');
            pill.textContent = '\u00d71';
            pill.title = '1 song sung tonight';
        } else if (sung <= 4) {
            pill.classList.add('pill-few');
            pill.textContent = '\u00d7' + sung;
            pill.title = sung + ' songs sung tonight';
        } else {
            pill.classList.add('pill-many');
            pill.textContent = '\u00d7' + sung;
            pill.title = sung + ' songs sung tonight';
        }
        info.appendChild(pill);
```

- [ ] **Step 3: Validate JS syntax**

Run: `node -c kj-controller/static/app.js`
Expected: No output (valid syntax)

- [ ] **Step 4: Commit**

```bash
git add kj-controller/static/app.js kj-controller/static/style.css
git commit -m "feat: swap pill colors — green=new, orange=2-4, red=5+"
```

---

### Task 9: Pill input component — HTML + JS

**Files:**
- Modify: `kj-controller/templates/index.html:76` (singer input)
- Modify: `kj-controller/static/app.js` (pill input logic)
- Modify: `kj-controller/static/style.css` (pill input styles)

- [ ] **Step 1: Update HTML — wrap singer input in pill container**

In `index.html`, replace the singer input line (line 76):

```html
            <div id="singer-input-container" class="singer-input-container" onclick="document.getElementById('rotation-singer').focus()">
                <input type="text" id="rotation-singer" placeholder="Singer name" autocomplete="off">
            </div>
```

- [ ] **Step 2: Add pill input CSS**

In `style.css`, add after the existing `.rotation-songs-pill` block:

```css
/* Singer pill input (add form) */
.singer-input-container {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px;
    background: #2a2a2a;
    border: 1px solid #444;
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 34px;
    cursor: text;
    flex: 1;
}
.singer-input-container:focus-within {
    border-color: #ffdf6b;
}
.singer-input-container input {
    background: none;
    border: none;
    outline: none;
    color: #ffdf6b;
    font-size: 14px;
    flex: 1;
    min-width: 60px;
    padding: 2px 0;
}
.singer-pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: #3a3a3a;
    color: #ffdf6b;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 13px;
    white-space: nowrap;
}
.singer-pill-x {
    cursor: pointer;
    color: #666;
    font-size: 11px;
    line-height: 1;
}
.singer-pill-x:hover {
    color: #e74c3c;
}
```

- [ ] **Step 3: Add pill input JS logic**

In `app.js`, add the pill input state and functions near the top of the file (after the `rotationHistory` object, around line 75):

```javascript
// --- Singer pill input state ---
const singerPillInput = {
    pills: [],

    render() {
        const container = document.getElementById('singer-input-container');
        if (!container) return;
        container.querySelectorAll('.singer-pill').forEach(el => el.remove());
        const input = document.getElementById('rotation-singer');
        this.pills.forEach((name, idx) => {
            const pill = document.createElement('span');
            pill.className = 'singer-pill';
            pill.textContent = name;
            const x = document.createElement('span');
            x.className = 'singer-pill-x';
            x.textContent = '\u00d7';
            x.onclick = (e) => { e.stopPropagation(); this.removePill(idx); };
            pill.appendChild(x);
            container.insertBefore(pill, input);
        });
    },

    addPill(name) {
        const trimmed = name.trim();
        if (!trimmed) return;
        this.pills.push(trimmed);
        this.render();
    },

    removePill(idx) {
        this.pills.splice(idx, 1);
        this.render();
        document.getElementById('rotation-singer').focus();
    },

    clear() {
        this.pills = [];
        this.render();
    },

    getSingers() {
        const input = document.getElementById('rotation-singer');
        const remaining = input ? input.value.trim() : '';
        const all = [...this.pills];
        if (remaining) all.push(remaining);
        return all;
    },
};
```

- [ ] **Step 4: Wire up keydown handlers on singer input**

In `app.js`, replace the existing `onkeyup` handler. Find where the rotation add form is initialized (the `toggleRotationAddForm` function or the DOMContentLoaded init). Add an event listener setup. Add this after the `singerPillInput` object:

```javascript
document.addEventListener('DOMContentLoaded', () => {
    const singerInput = document.getElementById('rotation-singer');
    if (!singerInput) return;

    singerInput.addEventListener('keydown', (e) => {
        const val = singerInput.value;

        // Tab, comma, or & with text -> create pill
        if ((e.key === 'Tab' || e.key === ',' || e.key === '&') && val.trim()) {
            e.preventDefault();
            singerPillInput.addPill(val);
            singerInput.value = '';
            return;
        }

        // Backspace on empty input -> remove last pill
        if (e.key === 'Backspace' && !val && singerPillInput.pills.length > 0) {
            singerPillInput.removePill(singerPillInput.pills.length - 1);
            return;
        }

        // Enter -> move focus to song field (existing behavior)
        if (e.key === 'Enter') {
            e.preventDefault();
            document.getElementById('rotation-song').focus();
        }
    });
});
```

Also remove the inline `onkeyup` from the HTML input (already done in Step 1 — the new HTML has no `onkeyup`).

- [ ] **Step 5: Update addRotationEntry() to use pill input**

In `app.js`, find `addRotationEntry()` (around line 3669). Replace the singer reading logic:

```javascript
async function addRotationEntry() {
    const singers = singerPillInput.getSingers();
    if (singers.length === 0) return;
    const songField = document.getElementById('rotation-song');
    const song_artist = songField ? songField.value.trim() : '';
```

And update the fetch body to send `singers` array:

```javascript
        body: JSON.stringify({ singers: singers, song_artist }),
```

And after successful add, clear the pill input:

```javascript
        singerPillInput.clear();
        document.getElementById('rotation-singer').value = '';
```

- [ ] **Step 6: Validate JS syntax**

Run: `node -c kj-controller/static/app.js`
Expected: No output (valid syntax)

- [ ] **Step 7: Commit**

```bash
git add kj-controller/templates/index.html kj-controller/static/app.js kj-controller/static/style.css
git commit -m "feat: pill-based singer input for multi-singer entries"
```

---

### Task 10: Multi-singer rendering in rotation rows

**Files:**
- Modify: `kj-controller/static/app.js:3093-3097` (name rendering in renderRotation)
- Modify: `kj-controller/static/style.css` (rotation-singer-pill class)

- [ ] **Step 1: Add rotation-singer-pill CSS**

In `style.css`, add after the `.singer-pill-x:hover` block:

```css
/* Multi-singer pills in rotation rows */
.rotation-singer-pill {
    background: #2a2a2a;
    color: #ffdf6b;
    padding: 1px 7px;
    border-radius: 10px;
    font-size: 13px;
    white-space: nowrap;
}
```

- [ ] **Step 2: Update renderRotation() name rendering**

In `app.js`, find where `entry.singer` is rendered as the name span (around line 3093). Replace the name rendering block:

```javascript
        // Singer name(s)
        let singers_parsed = null;
        if (entry.singers_json) {
            try { singers_parsed = JSON.parse(entry.singers_json); } catch (e) { /* ignore */ }
        }

        if (singers_parsed && singers_parsed.length > 1) {
            // Multi-singer: render individual pills
            singers_parsed.forEach((s) => {
                const sp = document.createElement('span');
                sp.className = 'rotation-singer-pill rotation-copyable';
                sp.textContent = s;
                sp.title = 'Click to copy \u2022 Shift+click to edit';
                sp.onclick = (ev) => { if (!ev.shiftKey && !ev.ctrlKey && !ev.metaKey) copyRotationText(sp); };
                info.appendChild(sp);
            });
        } else {
            // Single singer or legacy: plain text (unchanged)
            const name = document.createElement('span');
            name.className = 'rotation-name rotation-copyable';
            name.textContent = entry.singer;
            name.title = 'Click to copy \u2022 Shift+click to edit';
            name.onclick = (ev) => { if (!ev.shiftKey && !ev.ctrlKey && !ev.metaKey) copyRotationText(name); };
            info.appendChild(name);
        }
```

Remove the old `name` span creation (lines 3093-3097) and the old `info.appendChild(name)` (line 3107) — they're replaced by the block above.

- [ ] **Step 3: Validate JS syntax**

Run: `node -c kj-controller/static/app.js`
Expected: No output

- [ ] **Step 4: Commit**

```bash
git add kj-controller/static/app.js kj-controller/static/style.css
git commit -m "feat: render multi-singer entries as individual name pills in rotation"
```

---

### Task 11: E2E Playwright Tests

**Files:**
- Create: `kj-controller/tests/e2e/test_rotation_e2e.py`
- Read: `kj-controller/tests/e2e/conftest.py` (for live_server fixture pattern)

- [ ] **Step 1: Create e2e test file with fixtures and single-singer test**

Create `kj-controller/tests/e2e/test_rotation_e2e.py`:

```python
"""E2E Playwright tests for rotation pill input and multi-singer support."""

import pytest


@pytest.fixture
def rotation_page(app_page):
    """Navigate to the app and open the rotation add form."""
    page = app_page
    # Click the "+ Add" button to reveal the add form
    page.click('.rotation-add-btn')
    page.wait_for_selector('#singer-input-container', state='visible')
    return page


class TestSingleSingerAdd:
    def test_type_name_and_submit(self, rotation_page):
        page = rotation_page
        page.fill('#rotation-singer', 'Sarah')
        page.press('#rotation-singer', 'Enter')
        page.fill('#rotation-song', 'Sweet Caroline')
        page.click('#rotation-add-btn-submit')
        # Verify the entry appears in the rotation list
        page.wait_for_selector('.rotation-entry')
        entry_text = page.text_content('.rotation-entry')
        assert 'Sarah' in entry_text


class TestMultiSingerPillCreation:
    def test_tab_creates_pill(self, rotation_page):
        page = rotation_page
        page.fill('#rotation-singer', 'Phil')
        page.press('#rotation-singer', 'Tab')
        # Verify pill appeared
        pills = page.query_selector_all('.singer-pill')
        assert len(pills) == 1
        assert 'Phil' in pills[0].text_content()
        # Input should be cleared
        assert page.input_value('#rotation-singer') == ''

    def test_comma_creates_pill(self, rotation_page):
        page = rotation_page
        page.type('#rotation-singer', 'Phil,')
        pills = page.query_selector_all('.singer-pill')
        assert len(pills) == 1
        assert 'Phil' in pills[0].text_content()

    def test_ampersand_creates_pill(self, rotation_page):
        page = rotation_page
        page.type('#rotation-singer', 'Phil&')
        pills = page.query_selector_all('.singer-pill')
        assert len(pills) == 1
        assert 'Phil' in pills[0].text_content()

    def test_multi_singer_submit(self, rotation_page):
        page = rotation_page
        page.fill('#rotation-singer', 'Phil')
        page.press('#rotation-singer', 'Tab')
        page.fill('#rotation-singer', 'Anya')
        page.press('#rotation-singer', 'Enter')
        page.fill('#rotation-song', "Don't Go Breaking My Heart")
        page.click('#rotation-add-btn-submit')
        page.wait_for_selector('.rotation-entry')
        # Multi-singer entry should show individual pills
        singer_pills = page.query_selector_all('.rotation-singer-pill')
        assert len(singer_pills) >= 2
        pill_texts = [p.text_content() for p in singer_pills]
        assert 'Phil' in pill_texts
        assert 'Anya' in pill_texts


class TestPillDeletion:
    def test_backspace_removes_last_pill(self, rotation_page):
        page = rotation_page
        page.fill('#rotation-singer', 'Phil')
        page.press('#rotation-singer', 'Tab')
        page.fill('#rotation-singer', 'Anya')
        page.press('#rotation-singer', 'Tab')
        assert len(page.query_selector_all('.singer-pill')) == 2
        # Backspace on empty input removes last pill
        page.press('#rotation-singer', 'Backspace')
        assert len(page.query_selector_all('.singer-pill')) == 1
        assert 'Phil' in page.query_selector_all('.singer-pill')[0].text_content()

    def test_x_button_removes_pill(self, rotation_page):
        page = rotation_page
        page.fill('#rotation-singer', 'Phil')
        page.press('#rotation-singer', 'Tab')
        page.fill('#rotation-singer', 'Anya')
        page.press('#rotation-singer', 'Tab')
        assert len(page.query_selector_all('.singer-pill')) == 2
        # Click x on first pill
        page.click('.singer-pill-x')
        pills = page.query_selector_all('.singer-pill')
        assert len(pills) == 1
        assert 'Anya' in pills[0].text_content()


class TestMultiSingerSongsSung:
    def test_duet_done_credits_both_singers(self, rotation_page):
        page = rotation_page
        # Add a duet entry
        page.fill('#rotation-singer', 'Phil')
        page.press('#rotation-singer', 'Tab')
        page.fill('#rotation-singer', 'Anya')
        page.press('#rotation-singer', 'Enter')
        page.fill('#rotation-song', 'Duet Song')
        page.click('#rotation-add-btn-submit')
        page.wait_for_selector('.rotation-entry')
        # Mark it as Done
        page.click('.rotation-btn-done')
        page.wait_for_timeout(500)
        # Add a solo entry for Phil
        page.click('.rotation-add-btn')
        page.wait_for_selector('#singer-input-container', state='visible')
        page.fill('#rotation-singer', 'Phil')
        page.press('#rotation-singer', 'Enter')
        page.fill('#rotation-song', 'Solo Song')
        page.click('#rotation-add-btn-submit')
        page.wait_for_selector('.rotation-entry')
        # Phil's entry should show x1 (yellow pill, not NEW)
        pill = page.query_selector('.rotation-songs-pill')
        assert pill is not None
        pill_text = pill.text_content()
        assert '\u00d71' in pill_text or '1' in pill_text
```

- [ ] **Step 2: Run e2e tests**

Run: `cd kj-controller && pytest tests/e2e/test_rotation_e2e.py -v --headed`
Expected: All PASS (or adjust selectors/timing if needed)

Note: If tests fail due to timing, add appropriate `wait_for_selector` or `wait_for_timeout` calls. The live server fixture from `conftest.py` handles Flask app startup.

- [ ] **Step 3: Commit**

```bash
git add kj-controller/tests/e2e/test_rotation_e2e.py
git commit -m "test: add e2e Playwright tests for multi-singer pill input"
```

---

### Task 12: Final Integration — Full Test Suite + Regression Check

**Files:** All modified files

- [ ] **Step 1: Run full unit test suite**

Run: `cd kj-controller && pytest tests/unit/ -v`
Expected: All PASS

- [ ] **Step 2: Run full integration test suite**

Run: `cd kj-controller && pytest tests/integration/ -v`
Expected: All PASS

- [ ] **Step 3: Run e2e tests**

Run: `cd kj-controller && pytest tests/e2e/test_rotation_e2e.py -v`
Expected: All PASS

- [ ] **Step 4: Run full test suite with coverage**

Run: `cd kj-controller && pytest --cov=rotation_store --cov=routes --cov-report=term-missing tests/unit/test_rotation_store.py tests/integration/test_rotation_routes.py`
Expected: rotation_store.py >= 95% coverage

- [ ] **Step 5: Validate JS syntax**

Run: `node -c kj-controller/static/app.js`
Expected: No output

- [ ] **Step 6: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: address test failures from integration testing"
```

Only commit if changes were needed. If all tests passed clean, skip this step.
