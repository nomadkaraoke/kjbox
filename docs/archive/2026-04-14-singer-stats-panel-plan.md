# Singer Stats Panel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a collapsible singer-focused stats panel below the rotation queue with per-singer info and bulk actions (rename, merge, BRB, remove/restore), sharing the existing undo/redo stack.

**Architecture:** Backend computes per-singer aggregates from all rotation entries (including done/left). New store methods handle singer actions (rename, merge, status changes). Frontend renders a collapsible panel below the rotation list, refreshed on every rotation data update. All actions push to the shared `rotationHistory` undo/redo stack.

**Tech Stack:** Python/Flask, SQLite, vanilla JS, pytest + Playwright

**Design spec:** `docs/archive/2026-04-14-singer-stats-panel-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `kj-controller/rotation_store.py` | Modify | get_entries excludes "left", get_singer_stats, rename_singer, merge_singers, set_singer_status |
| `kj-controller/rotation.py` | Modify | Pass-through methods for new store methods |
| `kj-controller/routes.py` | Modify | singer_stats in /rotation response, 5 new singer action routes |
| `kj-controller/static/app.js` | Modify | renderSingerStats, toggleSingerStats, action handlers |
| `kj-controller/static/style.css` | Modify | Singer stats panel styling |
| `kj-controller/templates/index.html` | Modify | Singer stats panel HTML |
| `kj-controller/tests/unit/test_rotation_store.py` | Modify | TestSingerStats, TestSingerActions |
| `kj-controller/tests/integration/test_rotation_routes.py` | Modify | Singer route tests |
| `kj-controller/tests/e2e/test_singer_stats_e2e.py` | Create | Playwright tests |

---

### Task 1: get_entries() excludes "Left" status + get_singer_stats() + Unit Tests

**Files:**
- Modify: `kj-controller/rotation_store.py`
- Test: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write failing tests**

Add new test classes at the end of `test_rotation_store.py`:

```python
class TestLeftStatus:
    def test_get_entries_excludes_left(self, store):
        store.add_entry("Alice")
        e2 = store.add_entry("Bob")
        store.update_status(e2["id"], "Left")
        entries = store.get_entries()
        assert len(entries) == 1
        assert entries[0]["singer"] == "Alice"

    def test_get_entries_include_done_includes_left(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Bob")
        store.update_status(e1["id"], "Done")
        store.update_status(e2["id"], "Left")
        entries = store.get_entries(include_done=True)
        assert len(entries) == 2


class TestGetSingerStats:
    def test_basic_stats(self, store):
        store.add_entry("Alice", song_artist="Song 1")
        store.add_entry("Alice", song_artist="Song 2")
        store.add_entry("Bob", song_artist="Song 3")
        stats = store.get_singer_stats()
        assert len(stats) == 2
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["entries_total"] == 2
        assert alice["entries_waiting"] == 2
        assert alice["entries_sung"] == 0
        assert alice["status"] == "active"

    def test_multi_singer_entry_credits_both(self, store):
        store.add_entry("ignored", singers=["Phil", "Anya"])
        stats = store.get_singer_stats()
        names = [s["name"].lower() for s in stats]
        assert "phil" in names
        assert "anya" in names

    def test_done_entries_counted(self, store):
        e1 = store.add_entry("Alice", song_artist="Song 1")
        store.add_entry("Alice", song_artist="Song 2")
        store.update_status(e1["id"], "Done")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["entries_sung"] == 1
        assert alice["entries_waiting"] == 1
        assert alice["entries_total"] == 2

    def test_left_singer_status(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Left")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "left"
        assert alice["entries_left"] == 1

    def test_brb_singer_status(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "On Hold (BRB)")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "brb"

    def test_sorted_by_first_added(self, store):
        store.add_entry("Bob")
        store.add_entry("Alice")
        stats = store.get_singer_stats()
        assert stats[0]["name"].lower() == "bob"  # added first

    def test_has_tipped(self, store):
        e1 = store.add_entry("Alice")
        store.set_paid(e1["id"], True)
        store.add_entry("Alice")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["has_tipped"] is True

    def test_all_done_status(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "done"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py::TestLeftStatus tests/unit/test_rotation_store.py::TestGetSingerStats -v`
Expected: FAIL

- [ ] **Step 3: Update get_entries() to exclude "left"**

In `rotation_store.py`, change the WHERE clause in `get_entries()` (line 165):

```python
            rows = conn.execute(
                "SELECT * FROM rotation_entries "
                "WHERE LOWER(status) NOT IN ('done', 'left') "
                "ORDER BY position"
            ).fetchall()
```

- [ ] **Step 4: Implement get_singer_stats()**

Add after `get_songs_sung_counts()` in `rotation_store.py`:

```python
    def get_singer_stats(self):
        """Return per-singer aggregate stats from all entries (including done/left).

        Returns a list of dicts sorted by first_added (earliest first).
        Each dict: name, entries_total, entries_sung, entries_waiting,
        entries_left, first_added, has_tipped, status.
        """
        import json as _json
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM rotation_entries ORDER BY created_at"
        ).fetchall()

        # Group by individual singer name (case-insensitive)
        singers = {}  # lowercase_name -> {display_name, entries: [...]}
        for row in rows:
            entry = self._row_to_dict(row)
            if entry.get("singers_json"):
                try:
                    names = _json.loads(entry["singers_json"])
                except (ValueError, TypeError):
                    names = [entry["singer"]]
            else:
                names = [entry["singer"]]

            for name in names:
                key = name.strip().lower()
                if key not in singers:
                    singers[key] = {"display_name": name.strip(), "entries": []}
                singers[key]["entries"].append(entry)

        # Compute stats per singer
        result = []
        for key, data in singers.items():
            entries = data["entries"]
            statuses = [e["status"].lower() for e in entries]
            non_done = [s for s in statuses if s != "done"]

            entries_sung = sum(1 for s in statuses if s == "done")
            entries_left = sum(1 for s in statuses if s == "left")
            entries_waiting = len(entries) - entries_sung - entries_left

            if not non_done:
                status = "done"
            elif all(s == "left" for s in non_done):
                status = "left"
            elif all(s in ("on hold (brb)", "on hold") for s in non_done):
                status = "brb"
            else:
                status = "active"

            result.append({
                "name": data["display_name"],
                "entries_total": len(entries),
                "entries_sung": entries_sung,
                "entries_waiting": entries_waiting,
                "entries_left": entries_left,
                "first_added": entries[0]["created_at"],
                "has_tipped": any(e.get("paid") for e in entries),
                "status": status,
            })

        # Sort by first_added (earliest first)
        result.sort(key=lambda s: s["first_added"])
        return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py::TestLeftStatus tests/unit/test_rotation_store.py::TestGetSingerStats -v`
Expected: All PASS

- [ ] **Step 6: Run full test suite for regressions**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py tests/integration/test_rotation_routes.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat: get_singer_stats and exclude Left status from queue"
```

---

### Task 2: Singer Action Methods (rename, merge, set_status) + Unit Tests

**Files:**
- Modify: `kj-controller/rotation_store.py`
- Test: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write failing tests**

Add `TestSingerActions` class:

```python
class TestSingerActions:
    def test_rename_singer(self, store):
        store.add_entry("Phill", song_artist="Song 1")
        store.add_entry("Phill", song_artist="Song 2")
        store.add_entry("Bob", song_artist="Song 3")
        store.rename_singer("Phill", "Phil")
        entries = store.get_entries(include_done=True)
        phill_entries = [e for e in entries if "Phill" in e["singer"]]
        phil_entries = [e for e in entries if "Phil" in e["singer"] and "Phill" not in e["singer"]]
        assert len(phill_entries) == 0
        assert len(phil_entries) == 2

    def test_rename_singer_in_multi_singer_entry(self, store):
        store.add_entry("ignored", singers=["Phill", "Anya"])
        store.rename_singer("Phill", "Phil")
        entry = store.get_entries(include_done=True)[0]
        import json
        names = json.loads(entry["singers_json"])
        assert "Phil" in names
        assert "Phill" not in names
        assert entry["singer"] == "Phil & Anya"

    def test_merge_singers(self, store):
        store.add_entry("Phill")
        store.add_entry("Phil")
        store.merge_singers("Phill", "Phil")
        entries = store.get_entries(include_done=True)
        assert all(e["singer"] == "Phil" for e in entries)

    def test_merge_deduplicates_in_multi_singer(self, store):
        store.add_entry("ignored", singers=["Phill", "Phil"])
        store.merge_singers("Phill", "Phil")
        entry = store.get_entries(include_done=True)[0]
        import json
        names = json.loads(entry["singers_json"])
        assert names == ["Phil"]
        assert entry["singer"] == "Phil"

    def test_set_singer_status_brb(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Alice")
        e3 = store.add_entry("Bob")
        store.set_singer_status("Alice", "On Hold (BRB)")
        alice_entries = [e for e in store.get_entries(include_done=True) if e["singer"] == "Alice"]
        assert all(e["status"] == "On Hold (BRB)" for e in alice_entries)
        bob = store.get_entry(e3["id"])
        assert bob["status"] == "Waiting"

    def test_set_singer_status_left(self, store):
        e1 = store.add_entry("Alice")
        store.set_singer_status("Alice", "Left")
        entry = store.get_entry(e1["id"])
        assert entry["status"] == "Left"

    def test_set_singer_status_skips_done(self, store):
        e1 = store.add_entry("Alice")
        e2 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        store.set_singer_status("Alice", "Left")
        done_entry = store.get_entry(e1["id"])
        left_entry = store.get_entry(e2["id"])
        assert done_entry["status"] == "Done"
        assert left_entry["status"] == "Left"

    def test_set_singer_status_restores_left_to_waiting(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Left")
        store.set_singer_status("Alice", "Waiting")
        entry = store.get_entry(e1["id"])
        assert entry["status"] == "Waiting"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py::TestSingerActions -v`

- [ ] **Step 3: Implement rename_singer()**

Add to `rotation_store.py`:

```python
    def rename_singer(self, old_name, new_name):
        """Rename a singer across all entries (both singer and singers_json)."""
        import json as _json
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM rotation_entries").fetchall()
        old_lower = old_name.strip().lower()
        new_name = new_name.strip()

        for row in rows:
            entry = self._row_to_dict(row)
            changed = False

            if entry.get("singers_json"):
                try:
                    names = _json.loads(entry["singers_json"])
                except (ValueError, TypeError):
                    names = None

                if names and isinstance(names, list):
                    updated = [new_name if n.strip().lower() == old_lower else n for n in names]
                    if updated != names:
                        conn.execute(
                            "UPDATE rotation_entries SET singer = ?, singers_json = ?, "
                            "updated_at = datetime('now', 'localtime') WHERE id = ?",
                            (" & ".join(updated), _json.dumps(updated), entry["id"]),
                        )
                        changed = True

            if not changed and entry["singer"].strip().lower() == old_lower:
                conn.execute(
                    "UPDATE rotation_entries SET singer = ?, "
                    "updated_at = datetime('now', 'localtime') WHERE id = ?",
                    (new_name, entry["id"]),
                )

        conn.commit()
```

- [ ] **Step 4: Implement merge_singers()**

```python
    def merge_singers(self, source_name, target_name):
        """Merge source singer into target across all entries, deduplicating."""
        import json as _json
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM rotation_entries").fetchall()
        source_lower = source_name.strip().lower()
        target_name = target_name.strip()

        for row in rows:
            entry = self._row_to_dict(row)
            changed = False

            if entry.get("singers_json"):
                try:
                    names = _json.loads(entry["singers_json"])
                except (ValueError, TypeError):
                    names = None

                if names and isinstance(names, list):
                    # Replace source with target, then deduplicate
                    updated = []
                    seen = set()
                    for n in names:
                        actual = target_name if n.strip().lower() == source_lower else n
                        key = actual.strip().lower()
                        if key not in seen:
                            seen.add(key)
                            updated.append(actual)
                    if updated != names:
                        conn.execute(
                            "UPDATE rotation_entries SET singer = ?, singers_json = ?, "
                            "updated_at = datetime('now', 'localtime') WHERE id = ?",
                            (" & ".join(updated), _json.dumps(updated), entry["id"]),
                        )
                        changed = True

            if not changed and entry["singer"].strip().lower() == source_lower:
                conn.execute(
                    "UPDATE rotation_entries SET singer = ?, "
                    "updated_at = datetime('now', 'localtime') WHERE id = ?",
                    (target_name, entry["id"]),
                )

        conn.commit()
```

- [ ] **Step 5: Implement set_singer_status()**

```python
    def set_singer_status(self, name, new_status):
        """Set status on all non-done entries for a singer.

        Matches by individual singer name (unpacking singers_json).
        Skips entries with status 'Done'.
        """
        import json as _json
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM rotation_entries WHERE LOWER(status) != 'done'"
        ).fetchall()
        name_lower = name.strip().lower()

        ids_to_update = []
        for row in rows:
            entry = self._row_to_dict(row)
            if entry.get("singers_json"):
                try:
                    names = _json.loads(entry["singers_json"])
                except (ValueError, TypeError):
                    names = [entry["singer"]]
            else:
                names = [entry["singer"]]

            if any(n.strip().lower() == name_lower for n in names):
                ids_to_update.append(entry["id"])

        for eid in ids_to_update:
            conn.execute(
                "UPDATE rotation_entries SET status = ?, "
                "updated_at = datetime('now', 'localtime') WHERE id = ?",
                (new_status, eid),
            )
        conn.commit()
```

- [ ] **Step 6: Run tests**

Run: `cd kj-controller && pytest tests/unit/test_rotation_store.py::TestSingerActions tests/unit/test_rotation_store.py::TestLeftStatus tests/unit/test_rotation_store.py::TestGetSingerStats -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat: singer action methods — rename, merge, set_status"
```

---

### Task 3: RotationManager Pass-Through Methods

**Files:**
- Modify: `kj-controller/rotation.py`

- [ ] **Step 1: Add pass-through methods**

Add after the existing methods in `rotation.py`, before `_after_mutation()`:

```python
    def get_singer_stats(self):
        """Return per-singer aggregate stats."""
        return self.store.get_singer_stats()

    def rename_singer(self, old_name, new_name):
        """Rename a singer across all entries."""
        self.store.rename_singer(old_name, new_name)
        self._after_mutation()

    def merge_singers(self, source_name, target_name):
        """Merge source singer into target across all entries."""
        self.store.merge_singers(source_name, target_name)
        self._after_mutation()

    def set_singer_status(self, name, new_status):
        """Set status on all non-done entries for a singer."""
        self.store.set_singer_status(name, new_status)
        self._after_mutation()
```

- [ ] **Step 2: Run existing tests for regressions**

Run: `cd kj-controller && pytest tests/unit/test_rotation.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add kj-controller/rotation.py
git commit -m "feat: RotationManager pass-through for singer actions"
```

---

### Task 4: Routes — singer_stats in /rotation + singer action endpoints + Integration Tests

**Files:**
- Modify: `kj-controller/routes.py`
- Test: `kj-controller/tests/integration/test_rotation_routes.py`

- [ ] **Step 1: Write failing integration tests**

Add to `test_rotation_routes.py`:

```python
class TestSingerStats:
    def test_rotation_includes_singer_stats(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = [
            {"name": "Alice", "entries_total": 2, "entries_sung": 1,
             "entries_waiting": 1, "entries_left": 0, "first_added": "2026-04-14 20:00:00",
             "has_tipped": False, "status": "active"},
        ]
        resp = rotation_client.get('/rotation')
        data = resp.get_json()
        assert 'singer_stats' in data
        assert len(data['singer_stats']) == 1
        assert data['singer_stats'][0]['name'] == 'Alice'


class TestSingerRenameRoute:
    def test_rename_singer(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/rename',
            data=json.dumps({"old_name": "Phill", "new_name": "Phil"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.rename_singer.assert_called_once_with("Phill", "Phil")

    def test_rename_missing_params(self, rotation_client, mock_rotation):
        resp = rotation_client.post('/rotation/singer/rename',
            data=json.dumps({"old_name": "Phil"}),
            content_type='application/json')
        assert resp.status_code == 400


class TestSingerMergeRoute:
    def test_merge_singers(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/merge',
            data=json.dumps({"source_name": "Phill", "target_name": "Phil"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.merge_singers.assert_called_once_with("Phill", "Phil")


class TestSingerBrbRoute:
    def test_brb_toggle(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/brb',
            data=json.dumps({"name": "Alice", "brb": True}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.set_singer_status.assert_called_once_with("Alice", "On Hold (BRB)")

    def test_brb_restore(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/brb',
            data=json.dumps({"name": "Alice", "brb": False}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.set_singer_status.assert_called_once_with("Alice", "Waiting")


class TestSingerRemoveRoute:
    def test_remove_singer(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/remove',
            data=json.dumps({"name": "Alice"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.set_singer_status.assert_called_once_with("Alice", "Left")


class TestSingerRestoreRoute:
    def test_restore_singer(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/restore',
            data=json.dumps({"name": "Alice"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.set_singer_status.assert_called_once_with("Alice", "Waiting")
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Add singer_stats to /rotation response**

In `routes.py`, in the `get_rotation()` route, add after the `_add_songs_sung` call:

```python
        singer_stats = rotation.get_singer_stats()
        return jsonify({"entries": entries, "singer_stats": singer_stats})
```

- [ ] **Step 4: Add helper to build standard singer response**

Add a helper used by all singer action routes:

```python
def _singer_action_response(rotation):
    """Build standard response for singer action routes."""
    entries = rotation.get_rotation()
    _add_time_estimates(entries)
    _add_songs_sung(entries, rotation)
    singer_stats = rotation.get_singer_stats()
    return jsonify({"success": True, "entries": entries, "singer_stats": singer_stats})
```

- [ ] **Step 5: Add singer action routes**

```python
@routes_bp.route('/rotation/singer/rename', methods=['POST'])
def rename_singer():
    """Rename a singer across all rotation entries."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    old_name = data.get('old_name', '').strip()
    new_name = data.get('new_name', '').strip()
    if not old_name or not new_name:
        return jsonify({"error": "old_name and new_name are required"}), 400
    try:
        rotation.rename_singer(old_name, new_name)
        return _singer_action_response(rotation)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/singer/merge', methods=['POST'])
def merge_singers():
    """Merge source singer into target across all entries."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    source = data.get('source_name', '').strip()
    target = data.get('target_name', '').strip()
    if not source or not target:
        return jsonify({"error": "source_name and target_name are required"}), 400
    try:
        rotation.merge_singers(source, target)
        return _singer_action_response(rotation)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/singer/brb', methods=['POST'])
def singer_brb():
    """Toggle BRB status for all non-done entries of a singer."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    name = data.get('name', '').strip()
    brb = data.get('brb', True)
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        new_status = "On Hold (BRB)" if brb else "Waiting"
        rotation.set_singer_status(name, new_status)
        return _singer_action_response(rotation)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/singer/remove', methods=['POST'])
def remove_singer():
    """Mark all non-done entries for a singer as Left."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    name = data.get('name', '').strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        rotation.set_singer_status(name, "Left")
        return _singer_action_response(rotation)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/singer/restore', methods=['POST'])
def restore_singer():
    """Restore Left entries for a singer back to Waiting."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    name = data.get('name', '').strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        rotation.set_singer_status(name, "Waiting")
        return _singer_action_response(rotation)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 6: Update mock_rotation fixture**

In the `mock_rotation` fixture in `test_rotation_routes.py`, add:
```python
    rotation.get_singer_stats.return_value = []
```

- [ ] **Step 7: Run tests**

Run: `cd kj-controller && pytest tests/integration/test_rotation_routes.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_rotation_routes.py
git commit -m "feat: singer stats in /rotation response + singer action routes"
```

---

### Task 5: Frontend — Singer Stats Panel HTML + CSS + Rendering

**Files:**
- Modify: `kj-controller/templates/index.html`
- Modify: `kj-controller/static/style.css`
- Modify: `kj-controller/static/app.js`

- [ ] **Step 1: Add HTML for singer stats panel**

In `index.html`, add after the `#rotation-list` div (before the closing `</div>` of `.rotation-panel`):

```html
    <div id="singer-stats-panel" class="singer-stats-panel">
        <div class="singer-stats-header">
            <h3>Singers</h3>
            <button class="singer-stats-toggle" onclick="toggleSingerStats()">Hide</button>
        </div>
        <div id="singer-stats-list" class="singer-stats-list"></div>
    </div>
```

- [ ] **Step 2: Add CSS for singer stats panel**

In `style.css`:

```css
/* Singer stats panel */
.singer-stats-panel {
    margin-top: 8px;
    border-top: 1px solid #333;
    padding-top: 8px;
}
.singer-stats-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
}
.singer-stats-header h3 {
    margin: 0;
    font-size: 0.95em;
    color: #ffdf6b;
}
.singer-stats-toggle {
    background: #2a2a2a;
    border: 1px solid #444;
    color: #999;
    padding: 2px 10px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.8em;
}
.singer-stats-toggle:hover {
    color: #ccc;
    background: #3a3a3a;
}
.singer-stats-list {
    display: flex;
    flex-direction: column;
    gap: 3px;
}
.singer-stats-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 5px 8px;
    border-radius: 6px;
    background: #1a1a1a;
    transition: background 0.15s;
    gap: 8px;
}
.singer-stats-row:hover {
    background: #222;
}
.singer-stats-info {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 1;
    min-width: 0;
    overflow: hidden;
}
.singer-stats-name {
    color: #ffdf6b;
    font-weight: 600;
    font-size: 0.85em;
    white-space: nowrap;
}
.singer-stats-detail {
    color: #888;
    font-size: 0.75em;
    white-space: nowrap;
}
.singer-stats-tip {
    color: #e74c3c;
    font-size: 0.8em;
}
.singer-stats-actions {
    display: flex;
    gap: 3px;
    flex-shrink: 0;
}
.singer-stats-btn {
    background: #2a2a2a;
    border: 1px solid #444;
    color: #999;
    padding: 3px 8px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.7em;
}
.singer-stats-btn:hover {
    color: #ccc;
    background: #3a3a3a;
}
.singer-stats-row.singer-brb {
    opacity: 0.6;
    border-left: 3px solid #f59e0b;
}
.singer-stats-row.singer-left {
    opacity: 0.4;
    border-left: 3px solid #666;
}
.singer-stats-row.singer-done {
    opacity: 0.3;
}

/* Singer merge dropdown */
.singer-merge-dropdown {
    position: absolute;
    background: #2a2a2a;
    border: 1px solid #555;
    border-radius: 6px;
    padding: 4px;
    z-index: 100;
    max-height: 200px;
    overflow-y: auto;
    min-width: 120px;
}
.singer-merge-option {
    display: block;
    width: 100%;
    text-align: left;
    background: none;
    border: none;
    color: #ccc;
    padding: 6px 10px;
    cursor: pointer;
    font-size: 0.85em;
    border-radius: 4px;
}
.singer-merge-option:hover {
    background: #3a3a3a;
    color: #ffdf6b;
}
```

- [ ] **Step 3: Add JS for singer stats panel**

In `app.js`, add a global variable near the top:

```javascript
let singerStatsData = [];
```

Add `renderSingerStats` function and `toggleSingerStats`:

```javascript
function toggleSingerStats() {
    const list = document.getElementById('singer-stats-list');
    const btn = document.querySelector('.singer-stats-toggle');
    if (!list || !btn) return;
    if (list.classList.contains('hidden')) {
        list.classList.remove('hidden');
        btn.textContent = 'Hide';
        localStorage.removeItem('kj-singer-stats-hidden');
    } else {
        list.classList.add('hidden');
        btn.textContent = 'Show';
        localStorage.setItem('kj-singer-stats-hidden', '1');
    }
}

function renderSingerStats(stats) {
    const list = document.getElementById('singer-stats-list');
    if (!list) return;
    singerStatsData = stats || [];

    if (!stats || stats.length === 0) {
        list.innerHTML = '<div style="color:#666;font-size:0.8em;padding:4px;">No singers yet</div>';
        return;
    }

    // Sort: active first, then brb, then left, then done
    const order = { active: 0, brb: 1, left: 2, done: 3 };
    const sorted = [...stats].sort((a, b) => {
        const oa = order[a.status] ?? 0;
        const ob = order[b.status] ?? 0;
        if (oa !== ob) return oa - ob;
        return 0; // preserve backend sort (first_added) within same status
    });

    list.innerHTML = '';
    sorted.forEach((singer) => {
        // Skip pure "done" singers (no actionable entries)
        if (singer.status === 'done' && singer.entries_waiting === 0 && singer.entries_left === 0) return;

        const row = document.createElement('div');
        row.className = 'singer-stats-row';
        if (singer.status === 'brb') row.classList.add('singer-brb');
        if (singer.status === 'left') row.classList.add('singer-left');
        if (singer.status === 'done') row.classList.add('singer-done');

        const info = document.createElement('div');
        info.className = 'singer-stats-info';

        const name = document.createElement('span');
        name.className = 'singer-stats-name';
        name.textContent = singer.name;
        info.appendChild(name);

        if (singer.has_tipped) {
            const tip = document.createElement('span');
            tip.className = 'singer-stats-tip';
            tip.textContent = ' \u2665';
            tip.title = 'Tipped tonight';
            info.appendChild(tip);
        }

        // Time since first added
        if (singer.first_added) {
            const elapsed = document.createElement('span');
            elapsed.className = 'singer-stats-detail';
            const added = new Date(singer.first_added.replace(' ', 'T'));
            const mins = Math.round((Date.now() - added.getTime()) / 60000);
            if (mins < 60) {
                elapsed.textContent = mins + 'm ago';
            } else {
                elapsed.textContent = Math.floor(mins / 60) + 'h ' + (mins % 60) + 'm ago';
            }
            elapsed.title = 'First added to rotation';
            info.appendChild(elapsed);
        }

        const songs = document.createElement('span');
        songs.className = 'singer-stats-detail';
        songs.textContent = singer.entries_sung + '/' + singer.entries_total + ' sung';
        songs.title = singer.entries_sung + ' sung, ' + singer.entries_waiting + ' waiting';
        info.appendChild(songs);

        // Find next estimated time from rotationData
        if (singer.entries_waiting > 0) {
            const singerLower = singer.name.toLowerCase();
            const nextEntry = rotationData.find(e => {
                if (e.singers_json) {
                    try {
                        const names = JSON.parse(e.singers_json);
                        return names.some(n => n.trim().toLowerCase() === singerLower);
                    } catch (err) { return false; }
                }
                return e.singer.toLowerCase() === singerLower;
            });
            if (nextEntry && nextEntry.estimated_time) {
                const est = document.createElement('span');
                est.className = 'singer-stats-detail';
                est.textContent = '~' + nextEntry.estimated_time;
                est.title = 'Estimated next sing time';
                info.appendChild(est);
            }
        }

        row.appendChild(info);

        // Action buttons
        const actions = document.createElement('div');
        actions.className = 'singer-stats-actions';

        if (singer.status === 'left') {
            const restoreBtn = document.createElement('button');
            restoreBtn.className = 'singer-stats-btn';
            restoreBtn.textContent = 'Restore';
            restoreBtn.onclick = () => singerAction('restore', { name: singer.name });
            actions.appendChild(restoreBtn);
        } else if (singer.status !== 'done') {
            const editBtn = document.createElement('button');
            editBtn.className = 'singer-stats-btn';
            editBtn.textContent = 'Edit';
            editBtn.onclick = () => enterSingerEditMode(row, singer);
            actions.appendChild(editBtn);

            const mergeBtn = document.createElement('button');
            mergeBtn.className = 'singer-stats-btn';
            mergeBtn.textContent = 'Merge';
            mergeBtn.onclick = (ev) => showMergeDropdown(ev, singer);
            actions.appendChild(mergeBtn);

            const brbBtn = document.createElement('button');
            brbBtn.className = 'singer-stats-btn';
            brbBtn.textContent = singer.status === 'brb' ? 'Back' : 'BRB';
            brbBtn.onclick = () => singerAction('brb', { name: singer.name, brb: singer.status !== 'brb' });
            actions.appendChild(brbBtn);

            const removeBtn = document.createElement('button');
            removeBtn.className = 'singer-stats-btn';
            removeBtn.textContent = 'Remove';
            removeBtn.onclick = () => singerAction('remove', { name: singer.name });
            actions.appendChild(removeBtn);
        }

        row.appendChild(actions);
        list.appendChild(row);
    });
}

async function singerAction(action, data) {
    rotationHistory.pushUndo(rotationData);
    showRotationIndicator('spin');
    try {
        const resp = await fetch('/rotation/singer/' + action, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const result = await resp.json();
        if (!resp.ok) { showRotationIndicator('error'); return; }
        if (result.entries) { rotationData = result.entries; renderRotation(rotationData); }
        if (result.singer_stats) { renderSingerStats(result.singer_stats); }
        showRotationIndicator('success');
    } catch (e) {
        showRotationIndicator('error');
    }
}

function enterSingerEditMode(row, singer) {
    const info = row.querySelector('.singer-stats-info');
    const actions = row.querySelector('.singer-stats-actions');
    const origInfoHTML = info.innerHTML;
    const origActionsHTML = actions.innerHTML;

    info.innerHTML = '';
    const input = document.createElement('input');
    input.type = 'text';
    input.value = singer.name;
    input.className = 'rotation-edit-input';
    input.style.flex = '1';
    info.appendChild(input);

    actions.innerHTML = '';
    const saveBtn = document.createElement('button');
    saveBtn.className = 'singer-stats-btn';
    saveBtn.textContent = 'Save';
    saveBtn.onclick = async () => {
        const newName = input.value.trim();
        if (!newName || newName === singer.name) {
            info.innerHTML = origInfoHTML;
            actions.innerHTML = origActionsHTML;
            return;
        }
        await singerAction('rename', { old_name: singer.name, new_name: newName });
    };
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'singer-stats-btn';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.onclick = () => {
        info.innerHTML = origInfoHTML;
        actions.innerHTML = origActionsHTML;
    };
    actions.appendChild(saveBtn);
    actions.appendChild(cancelBtn);
    input.focus();
    input.select();
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') saveBtn.click();
        if (e.key === 'Escape') cancelBtn.click();
    });
}

function showMergeDropdown(ev, singer) {
    // Remove any existing dropdown
    document.querySelectorAll('.singer-merge-dropdown').forEach(d => d.remove());

    const others = singerStatsData
        .filter(s => s.name.toLowerCase() !== singer.name.toLowerCase() && s.status !== 'done')
        .map(s => s.name);

    if (others.length === 0) return;

    const dropdown = document.createElement('div');
    dropdown.className = 'singer-merge-dropdown';
    others.forEach(targetName => {
        const opt = document.createElement('button');
        opt.className = 'singer-merge-option';
        opt.textContent = targetName;
        opt.onclick = () => {
            dropdown.remove();
            singerAction('merge', { source_name: singer.name, target_name: targetName });
        };
        dropdown.appendChild(opt);
    });

    // Position near the button
    const btn = ev.currentTarget;
    const rect = btn.getBoundingClientRect();
    dropdown.style.position = 'fixed';
    dropdown.style.top = (rect.bottom + 2) + 'px';
    dropdown.style.left = rect.left + 'px';
    document.body.appendChild(dropdown);

    // Close on click outside
    const close = (e) => {
        if (!dropdown.contains(e.target)) {
            dropdown.remove();
            document.removeEventListener('click', close);
        }
    };
    setTimeout(() => document.addEventListener('click', close), 0);
}
```

- [ ] **Step 4: Hook renderSingerStats into data flow**

In `app.js`, find `fetchRotation()`. After it sets `rotationData = data.entries` and calls `renderRotation(rotationData)`, add:

```javascript
        if (data.singer_stats) { renderSingerStats(data.singer_stats); }
```

Also in the undo/redo `_restore` handler, add the same line after `renderRotation`.

Also update every mutation handler that receives `data.entries` to also check for `data.singer_stats` and call `renderSingerStats`.

- [ ] **Step 5: Add localStorage persistence for hide/show**

In the DOMContentLoaded handler, add:

```javascript
    // Restore singer stats panel visibility
    if (localStorage.getItem('kj-singer-stats-hidden') === '1') {
        const statsList = document.getElementById('singer-stats-list');
        const statsBtn = document.querySelector('.singer-stats-toggle');
        if (statsList) statsList.classList.add('hidden');
        if (statsBtn) statsBtn.textContent = 'Show';
    }
```

- [ ] **Step 6: Validate JS syntax**

Run: `node -c kj-controller/static/app.js`

- [ ] **Step 7: Commit**

```bash
git add kj-controller/templates/index.html kj-controller/static/style.css kj-controller/static/app.js
git commit -m "feat: singer stats panel with actions — rename, merge, BRB, remove/restore"
```

---

### Task 6: E2E Playwright Tests

**Files:**
- Create: `kj-controller/tests/e2e/test_singer_stats_e2e.py`

- [ ] **Step 1: Create e2e test file**

```python
"""E2E Playwright tests for singer stats panel."""

import pytest


@pytest.fixture
def stats_page(app_page):
    """Navigate to the app, add a singer, and ensure stats panel is visible."""
    page = app_page
    # Add a test singer
    page.locator('.rotation-add-btn').click()
    page.locator('#singer-input-container').wait_for(state='visible')
    page.locator('#rotation-singer').fill('StatsTestSinger')
    page.locator('#rotation-singer').press('Enter')
    page.locator('#rotation-song').fill('Stats Test Song')
    page.locator('#rotation-add-btn-submit').click()
    page.locator('.rotation-entry').first.wait_for(state='visible')
    # Ensure singer stats panel is visible
    page.locator('#singer-stats-panel').wait_for(state='visible')
    return page


class TestSingerStatsPanel:
    def test_panel_renders_with_singer(self, stats_page):
        page = stats_page
        page.locator('.singer-stats-row').first.wait_for(state='visible')
        assert page.locator('.singer-stats-name', has_text='StatsTestSinger').count() > 0

    def test_toggle_hide_show(self, stats_page):
        page = stats_page
        page.locator('.singer-stats-toggle').click()
        assert page.locator('#singer-stats-list').first.is_hidden()
        page.locator('.singer-stats-toggle').click()
        assert page.locator('#singer-stats-list').first.is_visible()


class TestSingerRename:
    def test_rename_inline(self, stats_page):
        page = stats_page
        # Click Edit on the singer
        row = page.locator('.singer-stats-row', has_text='StatsTestSinger')
        row.locator('.singer-stats-btn', has_text='Edit').click()
        # Should show an input with current name
        input_el = row.locator('input')
        input_el.fill('RenamedSinger')
        row.locator('.singer-stats-btn', has_text='Save').click()
        # Verify name updated in both stats panel and rotation
        page.locator('.singer-stats-name', has_text='RenamedSinger').first.wait_for(state='visible')
        assert page.locator('.rotation-name', has_text='RenamedSinger').count() > 0


class TestSingerBrb:
    def test_brb_toggle(self, stats_page):
        page = stats_page
        row = page.locator('.singer-stats-row', has_text='StatsTestSinger')
        row.locator('.singer-stats-btn', has_text='BRB').click()
        # Row should get dimmed styling
        page.locator('.singer-stats-row.singer-brb').first.wait_for(state='visible')
        # Toggle back
        row = page.locator('.singer-stats-row', has_text='StatsTestSinger')
        row.locator('.singer-stats-btn', has_text='Back').click()
        page.wait_for_timeout(500)
        assert page.locator('.singer-stats-row.singer-brb').count() == 0


class TestSingerRemoveRestore:
    def test_remove_and_restore(self, stats_page):
        page = stats_page
        row = page.locator('.singer-stats-row', has_text='StatsTestSinger')
        row.locator('.singer-stats-btn', has_text='Remove').click()
        # Singer should be dimmed with "left" styling and Restore button
        page.locator('.singer-stats-row.singer-left').first.wait_for(state='visible')
        # Entry should be gone from main rotation
        page.wait_for_timeout(500)
        # Restore
        left_row = page.locator('.singer-stats-row', has_text='StatsTestSinger')
        left_row.locator('.singer-stats-btn', has_text='Restore').click()
        page.wait_for_timeout(500)
        assert page.locator('.singer-stats-row.singer-left').count() == 0
```

- [ ] **Step 2: Run e2e tests**

Run: `cd kj-controller && pytest tests/e2e/test_singer_stats_e2e.py -v`

Fix any selector/timing issues until all pass.

- [ ] **Step 3: Commit**

```bash
git add kj-controller/tests/e2e/test_singer_stats_e2e.py
git commit -m "test: e2e Playwright tests for singer stats panel"
```

---

### Task 7: Final Integration — Full Test Suite

- [ ] **Step 1: Run full test suite**

Run: `cd kj-controller && pytest --tb=line`
Expected: All pass

- [ ] **Step 2: Validate JS**

Run: `node -c kj-controller/static/app.js`

- [ ] **Step 3: Fix any issues and commit**

Only if needed.
