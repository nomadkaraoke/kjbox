# Live-show singer management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Singers panel usable late in a long karaoke night — hide done/left singers behind collapsed sections, let the KJ mark done singers as Left, show a singer's song history inline, and split a collision-name singer into two.

**Architecture:** All backend changes confined to `rotation_store.py` + thin `rotation.py` pass-throughs + one new route + two existing-route updates. Frontend changes confined to `static/app.js` (new section rendering, new Songs expand, new Split modal) and a small CSS block. "Left" is tracked as a rotation-session-scoped JSON list in `rotation_meta.left_singers_json` — no schema changes to `rotation_entries`.

**Tech Stack:** Python 3 / Flask / SQLite (WAL) / vanilla JS / pytest.

Spec: `docs/superpowers/specs/2026-04-17-live-show-singer-management-design.md`

---

## File Structure

**Modify:**
- `kj-controller/rotation_store.py` — new methods (`mark_singer_left`, `unmark_singer_left`, `get_left_singer_names`, `split_singer`); update `get_singer_stats` (left-set consultation + entries projection); update `rename_singer`, `merge_singers`, `archive` for left-set consistency.
- `kj-controller/rotation.py` — pass-throughs for new store methods.
- `kj-controller/routes.py` — update `/rotation/singer/remove` and `/rotation/singer/restore` handlers; add `/rotation/singer/split` route.
- `kj-controller/static/app.js` — refactor `renderSingerStats` into three sections with collapse state; rename Remove→Left label; add Songs button + inline expand; add Split modal; new `singerAction('split', ...)` branch.
- `kj-controller/static/style.css` — section header styling, Songs expand panel, Split modal styling.

**Tests:**
- `kj-controller/tests/unit/test_rotation_store.py` — new classes `TestLeftSingersMeta`, `TestSplitSinger`; extend `TestGetSingerStats` (left-set override + entries projection).
- `kj-controller/tests/integration/test_rotation_routes.py` — extend `TestSingerRemoveRoute`/`TestSingerRestoreRoute`; new `TestSingerSplitRoute`.
- `kj-controller/tests/e2e/test_singer_stats_e2e.py` — new scenario: done-singer Left round-trip; Split scenario.

**Conventions observed:** existing file uses TDD-shaped tests with `TestClassName` groupings, `store` fixture for unit tests, `rotation_client` + `mock_rotation` fixtures for integration tests, `localStorage` keys prefixed `kj-`.

---

## Task 1: Left-singers meta storage (`mark_singer_left` / `unmark_singer_left` / `get_left_singer_names`)

**Files:**
- Modify: `kj-controller/rotation_store.py` (add methods near the other `rotation_meta`-related code, after `set_singer_status`)
- Test: `kj-controller/tests/unit/test_rotation_store.py` (new class `TestLeftSingersMeta` at end of file)

- [ ] **Step 1: Write the failing tests**

Append to `kj-controller/tests/unit/test_rotation_store.py`:

```python
class TestLeftSingersMeta:
    def test_empty_by_default(self, store):
        assert store.get_left_singer_names() == set()

    def test_mark_adds_lowercased(self, store):
        store.mark_singer_left("Kai")
        assert store.get_left_singer_names() == {"kai"}

    def test_mark_strips_whitespace(self, store):
        store.mark_singer_left("  Kai  ")
        assert store.get_left_singer_names() == {"kai"}

    def test_mark_is_idempotent(self, store):
        store.mark_singer_left("Kai")
        store.mark_singer_left("kai")
        store.mark_singer_left("KAI")
        assert store.get_left_singer_names() == {"kai"}

    def test_mark_multiple_names(self, store):
        store.mark_singer_left("Kai")
        store.mark_singer_left("Anya")
        assert store.get_left_singer_names() == {"kai", "anya"}

    def test_unmark_removes(self, store):
        store.mark_singer_left("Kai")
        store.mark_singer_left("Anya")
        store.unmark_singer_left("Kai")
        assert store.get_left_singer_names() == {"anya"}

    def test_unmark_is_idempotent(self, store):
        store.unmark_singer_left("Kai")  # no-op
        assert store.get_left_singer_names() == set()
        store.mark_singer_left("Kai")
        store.unmark_singer_left("Kai")
        store.unmark_singer_left("Kai")  # second unmark no-op
        assert store.get_left_singer_names() == set()

    def test_unmark_case_insensitive(self, store):
        store.mark_singer_left("Kai")
        store.unmark_singer_left("KAI")
        assert store.get_left_singer_names() == set()

    def test_persisted_across_get(self, store):
        store.mark_singer_left("Kai")
        # Second call reads from DB, not cached state
        assert store.get_left_singer_names() == {"kai"}
        assert store.get_left_singer_names() == {"kai"}
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd kj-controller && pytest tests/unit/test_rotation_store.py::TestLeftSingersMeta -v
```

Expected: all 9 tests FAIL with `AttributeError: 'RotationStore' object has no attribute 'mark_singer_left'` (or `get_left_singer_names` / `unmark_singer_left`).

- [ ] **Step 3: Implement in `rotation_store.py`**

Add after the `set_singer_status` method (around line 667), before the `# Task 5: Archive` section:

```python
    # ------------------------------------------------------------------
    # Left-singers meta (session-scoped list of names who have left)
    # ------------------------------------------------------------------

    _LEFT_META_KEY = "left_singers_json"

    def get_left_singer_names(self):
        """Return the set of lowercased singer names marked as 'left'.

        Backed by rotation_meta.left_singers_json. Returns an empty set if
        the key is unset or unparseable (malformed JSON is treated as empty).
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT value FROM rotation_meta WHERE key = ?",
            (self._LEFT_META_KEY,),
        ).fetchone()
        if row is None or row[0] is None:
            return set()
        try:
            names = json.loads(row[0])
        except (ValueError, TypeError):
            return set()
        return set(names) if isinstance(names, list) else set()

    def _set_left_singer_names(self, names):
        """Internal: overwrite the left-singers meta list."""
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO rotation_meta (key, value) VALUES (?, ?)",
            (self._LEFT_META_KEY, json.dumps(sorted(names))),
        )
        conn.commit()

    def mark_singer_left(self, name):
        """Add a singer name to the left set (case-insensitive, idempotent)."""
        key = name.strip().lower()
        if not key:
            return
        names = self.get_left_singer_names()
        names.add(key)
        self._set_left_singer_names(names)

    def unmark_singer_left(self, name):
        """Remove a singer name from the left set (case-insensitive, idempotent)."""
        key = name.strip().lower()
        if not key:
            return
        names = self.get_left_singer_names()
        names.discard(key)
        self._set_left_singer_names(names)
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd kj-controller && pytest tests/unit/test_rotation_store.py::TestLeftSingersMeta -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat(rotation): add left_singers_json meta storage"
```

---

## Task 2: `get_singer_stats` forces `status='left'` for left-set names

**Files:**
- Modify: `kj-controller/rotation_store.py` (`get_singer_stats`, lines 341-400)
- Test: `kj-controller/tests/unit/test_rotation_store.py` (extend `TestGetSingerStats`)

- [ ] **Step 1: Write the failing tests**

Append to `TestGetSingerStats` class (around line 886):

```python
    def test_done_singer_forced_left_by_meta(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        store.mark_singer_left("Alice")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "left"

    def test_active_singer_forced_left_by_meta(self, store):
        store.add_entry("Alice")
        store.mark_singer_left("Alice")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "left"

    def test_unmarked_done_singer_stays_done(self, store):
        e1 = store.add_entry("Alice")
        store.update_status(e1["id"], "Done")
        store.mark_singer_left("Alice")
        store.unmark_singer_left("Alice")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "done"

    def test_left_meta_case_insensitive_match(self, store):
        store.add_entry("Kai")
        store.mark_singer_left("kai")  # lowercase mark
        stats = store.get_singer_stats()
        kai = next(s for s in stats if s["name"].lower() == "kai")
        assert kai["status"] == "left"
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd kj-controller && pytest tests/unit/test_rotation_store.py::TestGetSingerStats -v
```

Expected: the 4 new tests FAIL (`assert 'done' == 'left'` etc.). Existing tests still pass.

- [ ] **Step 3: Modify `get_singer_stats` in `rotation_store.py`**

Replace the status-computation block (current lines ~379-386) and the result append (~388-397). Locate this existing code:

```python
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
                ...
                "status": status,
            })
```

Before the `for key, data in singers.items():` loop, fetch the left set once:

```python
        left_set = self.get_left_singer_names()
```

Then in the loop body, after the existing status decision, override for left-set names:

```python
            if not non_done:
                status = "done"
            elif all(s == "left" for s in non_done):
                status = "left"
            elif all(s in ("on hold (brb)", "on hold") for s in non_done):
                status = "brb"
            else:
                status = "active"

            if key in left_set:
                status = "left"
```

(The `key` variable in that loop is already `name.strip().lower()` from the entries aggregation above, so it aligns with the lowercased left_set.)

- [ ] **Step 4: Run tests to verify they pass**

```
cd kj-controller && pytest tests/unit/test_rotation_store.py::TestGetSingerStats -v
```

Expected: all tests pass (including the 4 new and existing ones).

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat(rotation): force singer status=left when in left_singers_json"
```

---

## Task 3: Left-set consistency across `rename_singer`, `merge_singers`, `archive`

**Files:**
- Modify: `kj-controller/rotation_store.py` (`rename_singer`, `merge_singers`, `archive`)
- Test: `kj-controller/tests/unit/test_rotation_store.py` (new tests in `TestSingerActions` and near `TestArchive` if present, else `TestLeftSingersMeta`)

- [ ] **Step 1: Write the failing tests**

Append to `TestLeftSingersMeta`:

```python
    def test_rename_migrates_left_set(self, store):
        store.add_entry("Kai")
        store.mark_singer_left("Kai")
        store.rename_singer("Kai", "Kai P")
        assert store.get_left_singer_names() == {"kai p"}

    def test_rename_unlisted_singer_no_effect(self, store):
        store.add_entry("Kai")
        store.add_entry("Anya")
        store.mark_singer_left("Anya")
        store.rename_singer("Kai", "Kai P")
        assert store.get_left_singer_names() == {"anya"}

    def test_merge_drops_source_from_left_set(self, store):
        store.add_entry("Kai")
        store.add_entry("Kai P")
        store.mark_singer_left("Kai")
        store.merge_singers("Kai", "Kai P")
        assert store.get_left_singer_names() == set()

    def test_merge_preserves_target_in_left_set(self, store):
        store.add_entry("Kai")
        store.add_entry("Kai P")
        store.mark_singer_left("Kai P")
        store.merge_singers("Kai", "Kai P")
        assert store.get_left_singer_names() == {"kai p"}

    def test_archive_clears_left_set(self, store):
        store.add_entry("Kai")
        store.mark_singer_left("Kai")
        store.archive()
        assert store.get_left_singer_names() == set()
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd kj-controller && pytest tests/unit/test_rotation_store.py::TestLeftSingersMeta -v
```

Expected: the 5 new tests FAIL (e.g., `{'kai'} != {'kai p'}`). The original 9 still pass.

- [ ] **Step 3: Update `rename_singer` in `rotation_store.py`**

At the end of `rename_singer` (after the `conn.commit()` at the existing method's bottom), add left-set migration:

```python
        # Migrate left-singer meta if applicable
        left = self.get_left_singer_names()
        old_key = old_name.strip().lower()
        if old_key in left:
            left.discard(old_key)
            left.add(new_name.strip().lower())
            self._set_left_singer_names(left)
```

- [ ] **Step 4: Update `merge_singers` in `rotation_store.py`**

At the end of `merge_singers`, add:

```python
        # Drop source from left-singer meta; target's state is untouched
        self.unmark_singer_left(source_name)
```

- [ ] **Step 5: Update `archive` in `rotation_store.py`**

Inside `archive`, after `conn.execute("DELETE FROM rotation_entries")` but before the `night_started_at` meta write, add:

```python
        # Clear left-singers meta — it's session-scoped
        conn.execute(
            "DELETE FROM rotation_meta WHERE key = ?",
            (self._LEFT_META_KEY,),
        )
```

- [ ] **Step 6: Run tests to verify they pass**

```
cd kj-controller && pytest tests/unit/test_rotation_store.py::TestLeftSingersMeta -v
cd kj-controller && pytest tests/unit/test_rotation_store.py -v
```

Expected: all tests in `TestLeftSingersMeta` pass (14 total). Full file still green.

- [ ] **Step 7: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat(rotation): keep left_singers_json consistent across rename/merge/archive"
```

---

## Task 4: `RotationStore.split_singer`

**Files:**
- Modify: `kj-controller/rotation_store.py` (add after `merge_singers`)
- Test: `kj-controller/tests/unit/test_rotation_store.py` (new class `TestSplitSinger`)

- [ ] **Step 1: Write the failing tests**

Append to the test file:

```python
class TestSplitSinger:
    def test_split_single_singer_entry(self, store):
        e1 = store.add_entry("Kai", song_artist="Song A")
        e2 = store.add_entry("Kai", song_artist="Song B")
        store.split_singer("Kai", "Kai P", [e1["id"]])
        updated = store.get_entry(e1["id"])
        assert updated["singer"] == "Kai P"
        assert updated["singers_json"] is None
        unchanged = store.get_entry(e2["id"])
        assert unchanged["singer"] == "Kai"

    def test_split_multi_singer_replaces_within_array(self, store):
        e1 = store.add_entry("ignored", singers=["Kai", "Anya"])
        store.split_singer("Kai", "Kai P", [e1["id"]])
        updated = store.get_entry(e1["id"])
        import json as _j
        names = _j.loads(updated["singers_json"])
        assert names == ["Kai P", "Anya"]
        assert updated["singer"] == "Kai P & Anya"

    def test_split_multi_to_single_clears_singers_json(self, store):
        # Entry with just one name in singers_json should collapse to legacy shape
        e1 = store.add_entry("Kai", singers=["Kai"])
        store.split_singer("Kai", "Kai P", [e1["id"]])
        updated = store.get_entry(e1["id"])
        assert updated["singer"] == "Kai P"
        assert updated["singers_json"] is None

    def test_split_case_insensitive_source_match(self, store):
        e1 = store.add_entry("Kai", song_artist="Song A")
        store.split_singer("kai", "Kai P", [e1["id"]])
        updated = store.get_entry(e1["id"])
        assert updated["singer"] == "Kai P"

    def test_split_skips_entries_without_source(self, store):
        e1 = store.add_entry("Kai", song_artist="Song A")
        e2 = store.add_entry("Anya", song_artist="Song B")
        store.split_singer("Kai", "Kai P", [e1["id"], e2["id"]])
        assert store.get_entry(e1["id"])["singer"] == "Kai P"
        # e2 untouched because source name wasn't present
        assert store.get_entry(e2["id"])["singer"] == "Anya"

    def test_split_nonexistent_entry_raises(self, store):
        with pytest.raises(ValueError, match="not found"):
            store.split_singer("Kai", "Kai P", [9999])

    def test_split_empty_entry_ids_noop(self, store):
        e1 = store.add_entry("Kai")
        store.split_singer("Kai", "Kai P", [])
        assert store.get_entry(e1["id"])["singer"] == "Kai"

    def test_split_updates_updated_at(self, store):
        e1 = store.add_entry("Kai")
        original_updated = store.get_entry(e1["id"])["updated_at"]
        # Bump time deterministically by forcing a second write
        import time as _t
        _t.sleep(1.01)
        store.split_singer("Kai", "Kai P", [e1["id"]])
        new_updated = store.get_entry(e1["id"])["updated_at"]
        assert new_updated != original_updated

    def test_split_preserves_other_entry_fields(self, store):
        e1 = store.add_entry("Kai", song_artist="Song A", notes="vip")
        store.update_status(e1["id"], "Done")
        store.split_singer("Kai", "Kai P", [e1["id"]])
        updated = store.get_entry(e1["id"])
        assert updated["song_artist"] == "Song A"
        assert updated["notes"] == "vip"
        assert updated["status"] == "Done"
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd kj-controller && pytest tests/unit/test_rotation_store.py::TestSplitSinger -v
```

Expected: all 9 tests FAIL with `AttributeError: ... split_singer`.

- [ ] **Step 3: Implement `split_singer` in `rotation_store.py`**

Add after `merge_singers`:

```python
    def split_singer(self, source_name, new_name, entry_ids):
        """Reassign specific entries from source_name to new_name.

        For multi-singer entries (singers_json set), replace source_name with
        new_name in the array (case-insensitive match on source_name), preserving
        other names. If the resulting array has a single name, collapse to the
        legacy single-singer shape (singers_json = NULL).

        For legacy single-singer entries whose `singer` matches source_name
        case-insensitively, overwrite singer = new_name.

        Entries whose content doesn't include source_name are silently skipped.

        Raises ValueError if any entry_id is not found.
        """
        if not entry_ids:
            return
        source_key = source_name.strip().lower()
        new_name = new_name.strip()
        conn = self._get_conn()

        for entry_id in entry_ids:
            existing = self.get_entry(entry_id)
            if existing is None:
                raise ValueError(f"Entry {entry_id} not found")

            singers_json_raw = existing.get("singers_json")
            if singers_json_raw:
                try:
                    names = json.loads(singers_json_raw)
                except (ValueError, TypeError):
                    names = [existing["singer"]]
                # Case-insensitive replacement of source with new_name
                if not any(n.strip().lower() == source_key for n in names):
                    continue  # source not present; skip
                new_names = [new_name if n.strip().lower() == source_key else n for n in names]
                if len(new_names) == 1:
                    conn.execute(
                        "UPDATE rotation_entries "
                        "SET singer = ?, singers_json = NULL, "
                        "    updated_at = datetime('now', 'localtime') "
                        "WHERE id = ?",
                        (new_names[0], entry_id),
                    )
                else:
                    conn.execute(
                        "UPDATE rotation_entries "
                        "SET singer = ?, singers_json = ?, "
                        "    updated_at = datetime('now', 'localtime') "
                        "WHERE id = ?",
                        (" & ".join(new_names), json.dumps(new_names), entry_id),
                    )
            else:
                if existing["singer"].strip().lower() != source_key:
                    continue  # legacy single-singer but name doesn't match
                conn.execute(
                    "UPDATE rotation_entries "
                    "SET singer = ?, updated_at = datetime('now', 'localtime') "
                    "WHERE id = ?",
                    (new_name, entry_id),
                )

        conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd kj-controller && pytest tests/unit/test_rotation_store.py::TestSplitSinger -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat(rotation): add split_singer for reassigning entries to new name"
```

---

## Task 5: Include per-singer `entries` projection in `get_singer_stats`

**Files:**
- Modify: `kj-controller/rotation_store.py` (`get_singer_stats`)
- Test: `kj-controller/tests/unit/test_rotation_store.py` (extend `TestGetSingerStats`)

The frontend needs to show each singer's songs inline. Currently `get_singer_stats` aggregates counts but doesn't expose entries. Add a trimmed `entries` list per singer.

- [ ] **Step 1: Write the failing test**

Append to `TestGetSingerStats`:

```python
    def test_entries_projection_included(self, store):
        e1 = store.add_entry("Alice", song_artist="Song A")
        e2 = store.add_entry("Alice", song_artist="Song B")
        store.update_status(e1["id"], "Done")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert "entries" in alice
        assert len(alice["entries"]) == 2
        fields = set(alice["entries"][0].keys())
        assert fields == {"id", "song_artist", "status", "position", "created_at"}
        # Ordered by created_at (earliest first, same as stat ordering)
        assert alice["entries"][0]["song_artist"] == "Song A"
        assert alice["entries"][0]["status"] == "Done"
        assert alice["entries"][1]["song_artist"] == "Song B"
        assert alice["entries"][1]["status"] == "Waiting"

    def test_entries_projection_excludes_heavy_fields(self, store):
        e1 = store.add_entry("Alice", song_artist="Song A", file_path="/some/path.mp3")
        stats = store.get_singer_stats()
        alice = next(s for s in stats if s["name"].lower() == "alice")
        entry = alice["entries"][0]
        assert "file_path" not in entry
        assert "download_source" not in entry
        assert "singers_json" not in entry

    def test_multi_singer_entry_appears_under_each_singer(self, store):
        e1 = store.add_entry("ignored", singers=["Phil", "Anya"], song_artist="Duet")
        stats = store.get_singer_stats()
        phil = next(s for s in stats if s["name"].lower() == "phil")
        anya = next(s for s in stats if s["name"].lower() == "anya")
        assert phil["entries"][0]["song_artist"] == "Duet"
        assert anya["entries"][0]["song_artist"] == "Duet"
        # Same underlying entry id
        assert phil["entries"][0]["id"] == anya["entries"][0]["id"]
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd kj-controller && pytest tests/unit/test_rotation_store.py::TestGetSingerStats -v
```

Expected: the 3 new tests FAIL (`KeyError: 'entries'`).

- [ ] **Step 3: Modify `get_singer_stats`**

In the final `result.append({...})` inside `get_singer_stats`, add an `"entries"` key with the trimmed projection:

```python
            trimmed_entries = [
                {
                    "id": e["id"],
                    "song_artist": e["song_artist"],
                    "status": e["status"],
                    "position": e["position"],
                    "created_at": e["created_at"],
                }
                for e in entries
            ]

            result.append({
                "name": data["display_name"],
                "entries_total": len(entries),
                "entries_sung": entries_sung,
                "entries_waiting": entries_waiting,
                "entries_left": entries_left,
                "first_added": entries[0]["created_at"],
                "has_tipped": any(e.get("paid") for e in entries),
                "status": status,
                "entries": trimmed_entries,
            })
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd kj-controller && pytest tests/unit/test_rotation_store.py::TestGetSingerStats -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat(rotation): include trimmed entries projection in singer_stats"
```

---

## Task 6: `RotationManager` pass-throughs

**Files:**
- Modify: `kj-controller/rotation.py`
- Test: none needed (thin pass-throughs — covered by integration tests in Task 7/8)

- [ ] **Step 1: Add three pass-through methods after `set_singer_status` in `rotation.py`**

```python
    def mark_singer_left(self, name):
        """Mark a singer as having left (session-scoped meta flag)."""
        self.store.mark_singer_left(name)
        self._after_mutation()

    def unmark_singer_left(self, name):
        """Remove a singer from the left set."""
        self.store.unmark_singer_left(name)
        self._after_mutation()

    def split_singer(self, source_name, new_name, entry_ids):
        """Reassign specific entries from source_name to new_name."""
        self.store.split_singer(source_name, new_name, entry_ids)
        self._after_mutation()
```

- [ ] **Step 2: Verify existing rotation tests still pass**

```
cd kj-controller && pytest tests/unit/test_rotation.py -v
```

Expected: all pass (no behavior change for existing callers).

- [ ] **Step 3: Commit**

```bash
git add kj-controller/rotation.py
git commit -m "feat(rotation): pass-throughs for mark/unmark_singer_left and split_singer"
```

---

## Task 7: Update `/rotation/singer/remove` and `/rotation/singer/restore` routes

**Files:**
- Modify: `kj-controller/routes.py` (both handlers around lines 2398-2427)
- Test: `kj-controller/tests/integration/test_rotation_routes.py`

A Done-only singer has no non-done entries, so `set_singer_status(name, "Left")` is a no-op. We need both operations.

- [ ] **Step 1: Update the existing tests to assert the new double-call pattern**

Edit `kj-controller/tests/integration/test_rotation_routes.py` — replace:

```python
    def test_remove_singer(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/remove',
            data=json.dumps({"name": "Alice"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.set_singer_status.assert_called_once_with("Alice", "Left")
```

With:

```python
    def test_remove_singer(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/remove',
            data=json.dumps({"name": "Alice"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.set_singer_status.assert_called_once_with("Alice", "Left")
        mock_rotation.mark_singer_left.assert_called_once_with("Alice")
```

And replace:

```python
    def test_restore_singer(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/restore',
            data=json.dumps({"name": "Alice"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.set_singer_status.assert_called_once_with("Alice", "Waiting")
```

With:

```python
    def test_restore_singer(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/restore',
            data=json.dumps({"name": "Alice"}),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.set_singer_status.assert_called_once_with("Alice", "Waiting")
        mock_rotation.unmark_singer_left.assert_called_once_with("Alice")
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd kj-controller && pytest tests/integration/test_rotation_routes.py::TestSingerRemoveRoute tests/integration/test_rotation_routes.py::TestSingerRestoreRoute -v
```

Expected: both tests FAIL on the new `assert_called_once_with` line (`mark_singer_left` / `unmark_singer_left` never called).

- [ ] **Step 3: Modify the routes in `routes.py`**

Locate `remove_singer_route` (~lines 2398-2411). Replace the body of the `try`:

```python
    try:
        rotation.set_singer_status(name, "Left")
        rotation.mark_singer_left(name)
        return _singer_action_response(rotation)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

Locate `restore_singer_route` (~lines 2414-2427). Replace the body of the `try`:

```python
    try:
        rotation.set_singer_status(name, "Waiting")
        rotation.unmark_singer_left(name)
        return _singer_action_response(rotation)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd kj-controller && pytest tests/integration/test_rotation_routes.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_rotation_routes.py
git commit -m "feat(routes): /singer/remove and /restore also toggle left_singers_json"
```

---

## Task 8: New `/rotation/singer/split` route

**Files:**
- Modify: `kj-controller/routes.py` (add after `restore_singer_route`)
- Test: `kj-controller/tests/integration/test_rotation_routes.py` (new class `TestSingerSplitRoute`)

- [ ] **Step 1: Write the failing tests**

Append to `test_rotation_routes.py`:

```python
class TestSingerSplitRoute:
    def test_split_success(self, rotation_client, mock_rotation):
        mock_rotation.get_singer_stats.return_value = []
        resp = rotation_client.post('/rotation/singer/split',
            data=json.dumps({
                "source_name": "Kai",
                "new_name": "Kai P",
                "entry_ids": [1, 2],
            }),
            content_type='application/json')
        assert resp.status_code == 200
        mock_rotation.split_singer.assert_called_once_with("Kai", "Kai P", [1, 2])

    def test_split_missing_source_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/singer/split',
            data=json.dumps({"new_name": "X", "entry_ids": [1]}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_split_missing_new_name_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/singer/split',
            data=json.dumps({"source_name": "X", "entry_ids": [1]}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_split_missing_entry_ids_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/singer/split',
            data=json.dumps({"source_name": "X", "new_name": "Y"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_split_empty_entry_ids_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/singer/split',
            data=json.dumps({
                "source_name": "X", "new_name": "Y", "entry_ids": [],
            }),
            content_type='application/json')
        assert resp.status_code == 400

    def test_split_same_name_returns_400(self, rotation_client):
        resp = rotation_client.post('/rotation/singer/split',
            data=json.dumps({
                "source_name": "Kai", "new_name": "  kai  ", "entry_ids": [1],
            }),
            content_type='application/json')
        assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd kj-controller && pytest tests/integration/test_rotation_routes.py::TestSingerSplitRoute -v
```

Expected: all 6 tests FAIL (404, route doesn't exist yet).

- [ ] **Step 3: Add the route in `routes.py`**

After `restore_singer_route` (~line 2427):

```python
@routes_bp.route('/rotation/singer/split', methods=['POST'])
def split_singer_route():
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    source = data.get('source_name', '').strip()
    new_name = data.get('new_name', '').strip()
    entry_ids = data.get('entry_ids')
    if not source:
        return jsonify({"error": "source_name is required"}), 400
    if not new_name:
        return jsonify({"error": "new_name is required"}), 400
    if not isinstance(entry_ids, list) or not entry_ids:
        return jsonify({"error": "entry_ids must be a non-empty list"}), 400
    if source.lower() == new_name.lower():
        return jsonify({"error": "new_name must differ from source_name"}), 400
    try:
        rotation.split_singer(source, new_name, entry_ids)
        return _singer_action_response(rotation)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd kj-controller && pytest tests/integration/test_rotation_routes.py::TestSingerSplitRoute -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_rotation_routes.py
git commit -m "feat(routes): POST /rotation/singer/split"
```

---

## Task 9: Frontend — three-section rendering with collapse

**Files:**
- Modify: `kj-controller/static/app.js` (`renderSingerStats`, ~lines 3658-3800)
- Modify: `kj-controller/static/style.css` (new section-header styling)

The current `renderSingerStats` appends all singer rows into `#singer-stats-list`. We restructure into three sections, each with a clickable header that persists collapse state in localStorage.

- [ ] **Step 1: Replace the body of `renderSingerStats` in `app.js`**

Locate `function renderSingerStats(stats)` (starts ~line 3658). Replace everything from that line down to (but not including) `async function singerAction` (~line 3781) with:

```javascript
function renderSingerStats(stats) {
    const list = document.getElementById('singer-stats-list');
    if (!list) return;
    if (list.querySelector('.singer-editing')) return;

    singerStatsData = stats || [];
    list.innerHTML = '';

    const sections = {
        active: [],
        done: [],
        gone: [],
    };
    for (const singer of singerStatsData) {
        if (singer.status === 'left') sections.gone.push(singer);
        else if (singer.status === 'done') sections.done.push(singer);
        else sections.active.push(singer);
    }

    renderSingerSection(list, 'active', 'Active', sections.active, false);
    renderSingerSection(list, 'done', 'Done', sections.done, true);
    renderSingerSection(list, 'gone', 'Gone', sections.gone, true);
}

function renderSingerSection(container, key, label, singers, collapsedByDefault) {
    if (singers.length === 0) return;

    const storageKey = 'kj-singers-' + key + '-collapsed';
    const stored = localStorage.getItem(storageKey);
    const collapsed = stored === null ? collapsedByDefault : (stored === '1');

    const section = document.createElement('div');
    section.className = 'singer-section singer-section-' + key;
    if (collapsed) section.classList.add('collapsed');

    const header = document.createElement('div');
    header.className = 'singer-section-header';
    header.innerHTML = '<span class="singer-section-caret">\u25B8</span> '
        + '<span class="singer-section-label">' + label + '</span> '
        + '<span class="singer-section-count">(' + singers.length + ')</span>';
    header.onclick = () => {
        const isCollapsed = section.classList.toggle('collapsed');
        localStorage.setItem(storageKey, isCollapsed ? '1' : '0');
    };
    section.appendChild(header);

    const body = document.createElement('div');
    body.className = 'singer-section-body';
    for (const singer of singers) {
        body.appendChild(buildSingerRow(singer));
    }
    section.appendChild(body);
    container.appendChild(section);
}
```

- [ ] **Step 2: Extract the row-building logic into `buildSingerRow(singer)`**

Below `renderSingerSection`, define `buildSingerRow` containing the existing per-row construction. Copy verbatim what used to live inside the `singerStatsData.forEach(...)` block — the `row` element, `info`, stats labels, actions. Return `row`.

```javascript
function buildSingerRow(singer) {
    const row = document.createElement('div');
    row.className = 'singer-stats-row';
    if (singer.status === 'brb') row.classList.add('singer-brb');
    if (singer.status === 'left') row.classList.add('singer-left');

    const info = document.createElement('div');
    info.className = 'singer-stats-info';

    const name = document.createElement('span');
    name.className = 'singer-stats-name';
    name.textContent = singer.name;
    info.appendChild(name);

    if (singer.has_tipped) {
        const tip = document.createElement('span');
        tip.className = 'singer-stats-tip';
        tip.textContent = '\u2764\uFE0F';
        info.appendChild(tip);
    }

    if (singer.first_added) {
        const joined = document.createElement('span');
        joined.className = 'singer-stats-label';
        const added = new Date(singer.first_added.replace(' ', 'T'));
        const mins = Math.round((Date.now() - added.getTime()) / 60000);
        const ago = mins < 60 ? mins + ' mins ago' : Math.floor(mins / 60) + 'h ' + (mins % 60) + 'm ago';
        joined.innerHTML = '<span class="singer-label-key">Joined:</span> ' + ago;
        info.appendChild(joined);
    }

    const sung = document.createElement('span');
    sung.className = 'singer-stats-label';
    const sungCount = singer.entries_sung;
    let pillClass = 'pill-new';
    if (sungCount === 1) pillClass = 'pill-once';
    else if (sungCount >= 2 && sungCount <= 4) pillClass = 'pill-few';
    else if (sungCount >= 5) pillClass = 'pill-many';
    sung.innerHTML = '<span class="singer-label-key">Sung:</span> <span class="singer-sung-pill ' + pillClass + '">' + sungCount + '</span>';
    info.appendChild(sung);

    if (singer.entries_waiting > 0) {
        const queued = document.createElement('span');
        queued.className = 'singer-stats-label';
        queued.innerHTML = '<span class="singer-label-key">Queued:</span> ' + singer.entries_waiting;
        info.appendChild(queued);
    }

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
            est.className = 'singer-stats-label';
            est.innerHTML = '<span class="singer-label-key">Next:</span> ~' + nextEntry.estimated_time;
            info.appendChild(est);
        }
    }

    row.appendChild(info);

    const actions = document.createElement('div');
    actions.className = 'singer-stats-actions';
    buildSingerActions(actions, singer, row);
    row.appendChild(actions);

    return row;
}

function buildSingerActions(actions, singer, row) {
    // Placeholder — filled in by Task 10.
    if (singer.status === 'left') {
        const restoreBtn = document.createElement('button');
        restoreBtn.className = 'singer-stats-btn';
        restoreBtn.textContent = 'Restore';
        restoreBtn.title = 'Bring this singer back \u2014 restore their songs to the queue';
        restoreBtn.onclick = () => singerAction('restore', { name: singer.name });
        actions.appendChild(restoreBtn);
    } else if (singer.status !== 'done') {
        const editBtn = document.createElement('button');
        editBtn.className = 'singer-stats-btn';
        editBtn.textContent = 'Edit';
        editBtn.title = 'Rename this singer (fixes typos across all their entries)';
        editBtn.onclick = () => enterSingerEditMode(row, singer);
        actions.appendChild(editBtn);

        const mergeBtn = document.createElement('button');
        mergeBtn.className = 'singer-stats-btn';
        mergeBtn.textContent = 'Merge';
        mergeBtn.title = 'Merge this singer into another \u2014 use when the same person was added under two different names';
        mergeBtn.onclick = (ev) => showMergeDropdown(ev, singer);
        actions.appendChild(mergeBtn);

        const brbBtn = document.createElement('button');
        brbBtn.className = 'singer-stats-btn';
        brbBtn.textContent = singer.status === 'brb' ? 'Back' : 'BRB';
        brbBtn.title = singer.status === 'brb'
            ? 'Singer is back \u2014 restore their songs to the active queue'
            : 'Singer stepped away \u2014 hold all their songs until they return';
        brbBtn.onclick = () => singerAction('brb', { name: singer.name, brb: singer.status !== 'brb' });
        actions.appendChild(brbBtn);

        const removeBtn = document.createElement('button');
        removeBtn.className = 'singer-stats-btn';
        removeBtn.textContent = 'Remove';
        removeBtn.title = 'Singer is leaving \u2014 remove their songs from the queue (can be restored later)';
        removeBtn.onclick = () => singerAction('remove', { name: singer.name });
        actions.appendChild(removeBtn);
    }
}
```

- [ ] **Step 3: Add CSS for section headers**

Append to `kj-controller/static/style.css`:

```css
.singer-section {
    margin-bottom: 0.5rem;
}

.singer-section-header {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.3rem 0.4rem;
    cursor: pointer;
    user-select: none;
    color: var(--text-dim, #aaa);
    font-size: 0.9rem;
    border-top: 1px dashed rgba(255, 255, 255, 0.08);
}

.singer-section-header:hover {
    color: var(--text, #ddd);
}

.singer-section-caret {
    display: inline-block;
    transition: transform 0.15s ease;
}

.singer-section:not(.collapsed) > .singer-section-header > .singer-section-caret {
    transform: rotate(90deg);
}

.singer-section.collapsed .singer-section-body {
    display: none;
}

.singer-section-count {
    opacity: 0.6;
}

/* The Active section shouldn't have a border-top (it's the first/expected state) */
.singer-section-active > .singer-section-header {
    border-top: none;
}
```

- [ ] **Step 4: Verify manually in the browser**

Start the dev server:

```
cd kj-controller && python dev_server.py
```

Open http://localhost:5001 (or whatever port), add a few singers, mark some as "Done" (use the existing Done button on rotation entries), mark one as Left. The Singers panel should show three collapsible sections. Click each header — state should persist on reload.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/static/app.js kj-controller/static/style.css
git commit -m "feat(frontend): section singers into Active / Done / Gone with collapse"
```

---

## Task 10: Frontend — rename Remove→Left, add Left button to Done singers, add Songs button

**Files:**
- Modify: `kj-controller/static/app.js` (`buildSingerActions`)

- [ ] **Step 1: Replace `buildSingerActions` with the final per-section action set**

In `app.js`, replace the Task-9 placeholder `buildSingerActions` with:

```javascript
function buildSingerActions(actions, singer, row) {
    const songsBtn = document.createElement('button');
    songsBtn.className = 'singer-stats-btn';
    songsBtn.textContent = 'Songs';
    songsBtn.title = 'Show all songs this singer has queued or sung tonight';
    songsBtn.onclick = () => toggleSingerSongs(row, singer);
    actions.appendChild(songsBtn);

    if (singer.status === 'left') {
        const restoreBtn = document.createElement('button');
        restoreBtn.className = 'singer-stats-btn';
        restoreBtn.textContent = 'Restore';
        restoreBtn.title = 'Bring this singer back \u2014 restore their songs to the queue';
        restoreBtn.onclick = () => singerAction('restore', { name: singer.name });
        actions.appendChild(restoreBtn);
        return;
    }

    // Edit + Merge + Split available on both active and done
    const editBtn = document.createElement('button');
    editBtn.className = 'singer-stats-btn';
    editBtn.textContent = 'Edit';
    editBtn.title = 'Rename this singer (fixes typos across all their entries)';
    editBtn.onclick = () => enterSingerEditMode(row, singer);
    actions.appendChild(editBtn);

    const mergeBtn = document.createElement('button');
    mergeBtn.className = 'singer-stats-btn';
    mergeBtn.textContent = 'Merge';
    mergeBtn.title = 'Merge this singer into another \u2014 use when the same person was added under two different names';
    mergeBtn.onclick = (ev) => showMergeDropdown(ev, singer);
    actions.appendChild(mergeBtn);

    const splitBtn = document.createElement('button');
    splitBtn.className = 'singer-stats-btn';
    splitBtn.textContent = 'Split';
    splitBtn.title = 'Split this singer \u2014 reassign some of their songs to a different name';
    splitBtn.onclick = () => openSplitModal(singer);
    actions.appendChild(splitBtn);

    // BRB only meaningful when there's an active queue
    if (singer.status !== 'done') {
        const brbBtn = document.createElement('button');
        brbBtn.className = 'singer-stats-btn';
        brbBtn.textContent = singer.status === 'brb' ? 'Back' : 'BRB';
        brbBtn.title = singer.status === 'brb'
            ? 'Singer is back \u2014 restore their songs to the active queue'
            : 'Singer stepped away \u2014 hold all their songs until they return';
        brbBtn.onclick = () => singerAction('brb', { name: singer.name, brb: singer.status !== 'brb' });
        actions.appendChild(brbBtn);
    }

    const leftBtn = document.createElement('button');
    leftBtn.className = 'singer-stats-btn';
    leftBtn.textContent = 'Left';
    leftBtn.title = 'Mark this singer as having left \u2014 hides them from the active list (can be restored).';
    leftBtn.onclick = () => singerAction('remove', { name: singer.name });
    actions.appendChild(leftBtn);
}
```

- [ ] **Step 2: Add stub functions `toggleSingerSongs` and `openSplitModal`**

Immediately after `buildSingerActions`, add stubs so click handlers don't throw before Tasks 11/12 implement them:

```javascript
function toggleSingerSongs(_row, _singer) { /* Task 11 */ }
function openSplitModal(_singer) { /* Task 12 */ }
```

- [ ] **Step 3: Manual check**

Reload the browser. Done singers (Andrew, Julia in the tonight-image) should now show Songs · Edit · Merge · Split · Left. Click "Left" on a Done singer — they should move to the Gone section. Click Restore — they come back to the Done section.

- [ ] **Step 4: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat(frontend): Left/Songs/Split buttons on every singer row"
```

---

## Task 11: Frontend — inline Songs expand panel

**Files:**
- Modify: `kj-controller/static/app.js` (`toggleSingerSongs`)
- Modify: `kj-controller/static/style.css` (songs-expand panel)

- [ ] **Step 1: Implement `toggleSingerSongs`**

In `app.js`, replace the `toggleSingerSongs` stub with:

```javascript
function toggleSingerSongs(row, singer) {
    // Toggle: if already open, close it
    const existing = row.nextElementSibling;
    if (existing && existing.classList.contains('singer-songs-panel')
        && existing.dataset.singer === singer.name) {
        existing.remove();
        return;
    }
    // Close any other open panel
    document.querySelectorAll('.singer-songs-panel').forEach(p => p.remove());

    const panel = document.createElement('div');
    panel.className = 'singer-songs-panel';
    panel.dataset.singer = singer.name;

    const entries = singer.entries || [];
    if (entries.length === 0) {
        panel.innerHTML = '<div class="singer-songs-empty">No songs recorded for this singer.</div>';
    } else {
        const table = document.createElement('table');
        table.className = 'singer-songs-table';
        const tbody = document.createElement('tbody');
        for (const entry of entries) {
            const tr = document.createElement('tr');

            const songCell = document.createElement('td');
            songCell.className = 'singer-songs-song';
            songCell.textContent = entry.song_artist || '(no song)';
            tr.appendChild(songCell);

            const statusCell = document.createElement('td');
            statusCell.className = 'singer-songs-status';
            const statusLower = (entry.status || '').toLowerCase();
            const pillClass = statusLower.includes('done') ? 'status-done'
                : statusLower.includes('left') ? 'status-left'
                : statusLower.includes('hold') ? 'status-brb'
                : statusLower.includes('now') ? 'status-singing'
                : statusLower.includes('next') ? 'status-next'
                : 'status-waiting';
            statusCell.innerHTML = '<span class="singer-songs-status-pill ' + pillClass + '">'
                + (entry.status || 'Waiting') + '</span>';
            tr.appendChild(statusCell);

            const timeCell = document.createElement('td');
            timeCell.className = 'singer-songs-time';
            if (entry.created_at) {
                const added = new Date(entry.created_at.replace(' ', 'T'));
                const mins = Math.round((Date.now() - added.getTime()) / 60000);
                timeCell.textContent = mins < 60 ? mins + 'm ago' : Math.floor(mins / 60) + 'h ' + (mins % 60) + 'm ago';
            }
            tr.appendChild(timeCell);

            tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        panel.appendChild(table);
    }

    row.parentNode.insertBefore(panel, row.nextSibling);
}
```

- [ ] **Step 2: Add CSS for the songs panel**

Append to `style.css`:

```css
.singer-songs-panel {
    margin: 0.2rem 0 0.4rem 1rem;
    padding: 0.4rem 0.6rem;
    background: rgba(255, 255, 255, 0.03);
    border-left: 2px solid rgba(255, 255, 255, 0.1);
    border-radius: 0 4px 4px 0;
}

.singer-songs-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
}

.singer-songs-table td {
    padding: 0.15rem 0.4rem;
    vertical-align: middle;
}

.singer-songs-song {
    color: var(--text, #ddd);
}

.singer-songs-status {
    width: 90px;
}

.singer-songs-status-pill {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 8px;
    font-size: 0.7rem;
    text-transform: uppercase;
}

.singer-songs-status-pill.status-done {
    background: rgba(100, 200, 120, 0.2);
    color: #8ecc94;
}
.singer-songs-status-pill.status-waiting {
    background: rgba(255, 180, 80, 0.15);
    color: #e8a85a;
}
.singer-songs-status-pill.status-left {
    background: rgba(200, 100, 100, 0.2);
    color: #cc8a8a;
}
.singer-songs-status-pill.status-brb {
    background: rgba(150, 150, 200, 0.2);
    color: #9ea6d0;
}
.singer-songs-status-pill.status-singing {
    background: rgba(100, 200, 180, 0.25);
    color: #8fd9c8;
}
.singer-songs-status-pill.status-next {
    background: rgba(200, 180, 100, 0.2);
    color: #c8b664;
}

.singer-songs-time {
    width: 80px;
    text-align: right;
    color: var(--text-dim, #888);
    font-size: 0.75rem;
}

.singer-songs-empty {
    padding: 0.3rem;
    color: var(--text-dim, #888);
    font-style: italic;
}
```

- [ ] **Step 3: Manual check**

Reload browser. Click Songs on any singer — panel appears below the row showing their songs with status pills and relative sung-at times. Click Songs again on the same singer — panel closes. Click Songs on another singer — previous panel closes, new one opens.

- [ ] **Step 4: Commit**

```bash
git add kj-controller/static/app.js kj-controller/static/style.css
git commit -m "feat(frontend): inline Songs panel for per-singer history"
```

---

## Task 12: Frontend — Split modal

**Files:**
- Modify: `kj-controller/static/app.js` (`openSplitModal` + supporting)
- Modify: `kj-controller/static/style.css` (split modal)

- [ ] **Step 1: Implement `openSplitModal`**

In `app.js`, replace the `openSplitModal` stub with the full modal:

```javascript
function openSplitModal(singer) {
    document.querySelectorAll('.singer-split-modal-backdrop').forEach(d => d.remove());

    const entries = singer.entries || [];
    if (entries.length === 0) {
        alert('This singer has no entries to split.');
        return;
    }

    const backdrop = document.createElement('div');
    backdrop.className = 'singer-split-modal-backdrop';
    backdrop.onclick = (ev) => { if (ev.target === backdrop) backdrop.remove(); };

    const modal = document.createElement('div');
    modal.className = 'singer-split-modal';

    const heading = document.createElement('h3');
    heading.textContent = 'Split "' + singer.name + '"';
    modal.appendChild(heading);

    const help = document.createElement('p');
    help.className = 'singer-split-help';
    help.textContent = 'Pick the entries that actually belong to a different person, then give that person a name.';
    modal.appendChild(help);

    // Entry checkboxes
    const listWrap = document.createElement('div');
    listWrap.className = 'singer-split-entries';
    for (const entry of entries) {
        const label = document.createElement('label');
        label.className = 'singer-split-entry';

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = String(entry.id);
        label.appendChild(cb);

        const song = document.createElement('span');
        song.className = 'singer-split-entry-song';
        song.textContent = entry.song_artist || '(no song)';
        label.appendChild(song);

        const status = document.createElement('span');
        status.className = 'singer-split-entry-status';
        status.textContent = entry.status || 'Waiting';
        label.appendChild(status);

        listWrap.appendChild(label);
    }
    modal.appendChild(listWrap);

    // Reassign-to pick
    const formRow = document.createElement('div');
    formRow.className = 'singer-split-form-row';
    formRow.innerHTML = '<span class="singer-split-form-label">Reassign selected to:</span>';
    modal.appendChild(formRow);

    const modeWrap = document.createElement('div');
    modeWrap.className = 'singer-split-mode';

    const newLabel = document.createElement('label');
    const newRadio = document.createElement('input');
    newRadio.type = 'radio';
    newRadio.name = 'split-mode';
    newRadio.value = 'new';
    newRadio.checked = true;
    newLabel.appendChild(newRadio);
    newLabel.appendChild(document.createTextNode(' New name: '));
    const newInput = document.createElement('input');
    newInput.type = 'text';
    newInput.className = 'singer-split-new-input';
    newInput.placeholder = singer.name + ' P';
    newLabel.appendChild(newInput);
    modeWrap.appendChild(newLabel);

    const existingLabel = document.createElement('label');
    const existingRadio = document.createElement('input');
    existingRadio.type = 'radio';
    existingRadio.name = 'split-mode';
    existingRadio.value = 'existing';
    existingLabel.appendChild(existingRadio);
    existingLabel.appendChild(document.createTextNode(' Existing singer: '));
    const existingSelect = document.createElement('select');
    existingSelect.className = 'singer-split-existing-select';
    const others = (singerStatsData || [])
        .filter(s => s.name.toLowerCase() !== singer.name.toLowerCase())
        .map(s => s.name);
    if (others.length === 0) {
        existingRadio.disabled = true;
    }
    for (const other of others) {
        const opt = document.createElement('option');
        opt.value = other;
        opt.textContent = other;
        existingSelect.appendChild(opt);
    }
    existingLabel.appendChild(existingSelect);
    modeWrap.appendChild(existingLabel);

    modal.appendChild(modeWrap);

    // Buttons
    const buttons = document.createElement('div');
    buttons.className = 'singer-split-buttons';

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'singer-stats-btn';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.onclick = () => backdrop.remove();
    buttons.appendChild(cancelBtn);

    const splitBtn = document.createElement('button');
    splitBtn.className = 'singer-stats-btn singer-split-confirm';
    splitBtn.textContent = 'Split';
    splitBtn.onclick = async () => {
        const checkedIds = Array.from(listWrap.querySelectorAll('input[type=checkbox]:checked'))
            .map(cb => parseInt(cb.value, 10));
        if (checkedIds.length === 0) {
            alert('Select at least one entry to reassign.');
            return;
        }
        const mode = modeWrap.querySelector('input[name=split-mode]:checked').value;
        const newName = mode === 'new' ? newInput.value.trim() : existingSelect.value;
        if (!newName) {
            alert('Enter a new name.');
            return;
        }
        if (newName.toLowerCase() === singer.name.toLowerCase()) {
            alert('New name must differ from the original.');
            return;
        }
        backdrop.remove();
        await singerAction('split', {
            source_name: singer.name,
            new_name: newName,
            entry_ids: checkedIds,
        });
    };
    buttons.appendChild(splitBtn);

    modal.appendChild(buttons);

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);
    newInput.focus();
}
```

- [ ] **Step 2: Add CSS for the split modal**

Append to `style.css`:

```css
.singer-split-modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.7);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
}

.singer-split-modal {
    background: var(--bg-elevated, #1e1e22);
    color: var(--text, #ddd);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 6px;
    padding: 1.2rem;
    min-width: 480px;
    max-width: 90vw;
    max-height: 90vh;
    overflow: auto;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.6);
}

.singer-split-modal h3 {
    margin: 0 0 0.4rem;
    font-size: 1.1rem;
}

.singer-split-help {
    margin: 0 0 0.8rem;
    color: var(--text-dim, #999);
    font-size: 0.85rem;
}

.singer-split-entries {
    max-height: 300px;
    overflow-y: auto;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 4px;
    padding: 0.3rem 0.4rem;
    margin-bottom: 0.8rem;
}

.singer-split-entry {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.2rem 0;
    cursor: pointer;
}

.singer-split-entry:hover {
    background: rgba(255, 255, 255, 0.04);
}

.singer-split-entry-song {
    flex: 1;
}

.singer-split-entry-status {
    font-size: 0.7rem;
    text-transform: uppercase;
    color: var(--text-dim, #888);
}

.singer-split-form-row {
    margin-bottom: 0.5rem;
    font-size: 0.9rem;
}

.singer-split-mode {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    margin-bottom: 1rem;
}

.singer-split-new-input,
.singer-split-existing-select {
    background: var(--bg, #111);
    color: var(--text, #ddd);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 3px;
    padding: 0.2rem 0.4rem;
    margin-left: 0.3rem;
}

.singer-split-buttons {
    display: flex;
    gap: 0.5rem;
    justify-content: flex-end;
}

.singer-split-confirm {
    background: rgba(100, 200, 120, 0.2);
    color: #8ecc94;
    border-color: rgba(100, 200, 120, 0.4);
}
```

- [ ] **Step 3: Extend `singerAction` to handle `split`**

`singerAction('split', {...})` already POSTs to `/rotation/singer/split` via its URL-concat pattern (`/rotation/singer/' + action`). No code change needed — the existing function handles the response (re-renders entries + singer_stats).

Verify by searching: `grep -n "singerAction" kj-controller/static/app.js | head -5`.

- [ ] **Step 4: Manual check**

Reload browser. Click Split on Kai → modal appears with all Kai's entries. Check two, type "Kai P", click Split. The rotation list should update — those two rows now show "Kai P". The Singers panel should show a new Kai P singer with 2 entries.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/static/app.js kj-controller/static/style.css
git commit -m "feat(frontend): Split modal for reassigning entries to a new singer"
```

---

## Task 13: End-to-end tests

**Files:**
- Modify: `kj-controller/tests/e2e/test_singer_stats_e2e.py`

- [ ] **Step 1: Check existing patterns**

```
cd kj-controller && head -60 tests/e2e/test_singer_stats_e2e.py
```

Note the fixture shape (Playwright-based or API-driven) used in current e2e tests.

- [ ] **Step 2: Add Done→Left→Restore scenario**

Append a new test class following the file's existing conventions. The test should:

1. Add singer "Alice" with a song.
2. Mark that entry Done.
3. Confirm `GET /rotation` singer_stats shows Alice with status='done'.
4. POST `/rotation/singer/remove` with name=Alice.
5. Confirm Alice now shows status='left'.
6. POST `/rotation/singer/restore` with name=Alice.
7. Confirm Alice is back to status='done'.

Example body (adapt to the file's fixtures — if it uses a `client` vs `page` differs):

```python
class TestDoneSingerLeftRoundTrip:
    def test_done_singer_can_be_marked_left_and_restored(self, client):
        # Add + mark done
        r = client.post('/rotation', json={"singer": "Alice", "song_artist": "Song A"})
        entry_id = r.get_json()["id"]
        client.post(f'/rotation/{entry_id}/status', json={"status": "Done"})

        # Confirm status=done
        stats = client.get('/rotation').get_json()["singer_stats"]
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "done"

        # Mark left
        client.post('/rotation/singer/remove', json={"name": "Alice"})
        stats = client.get('/rotation').get_json()["singer_stats"]
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "left"

        # Restore
        client.post('/rotation/singer/restore', json={"name": "Alice"})
        stats = client.get('/rotation').get_json()["singer_stats"]
        alice = next(s for s in stats if s["name"].lower() == "alice")
        assert alice["status"] == "done"
```

- [ ] **Step 3: Add Split scenario**

```python
class TestSplitSingerE2E:
    def test_split_kai_into_kai_p(self, client):
        r1 = client.post('/rotation', json={"singer": "Kai", "song_artist": "Song A"})
        e1_id = r1.get_json()["id"]
        r2 = client.post('/rotation', json={"singer": "Kai", "song_artist": "Song B"})
        e2_id = r2.get_json()["id"]

        client.post('/rotation/singer/split', json={
            "source_name": "Kai",
            "new_name": "Kai P",
            "entry_ids": [e2_id],
        })

        stats = client.get('/rotation').get_json()["singer_stats"]
        names = {s["name"] for s in stats}
        assert "Kai" in names
        assert "Kai P" in names

        entries = client.get('/rotation').get_json()["entries"]
        e1 = next(e for e in entries if e["id"] == e1_id)
        e2 = next(e for e in entries if e["id"] == e2_id)
        assert e1["singer"] == "Kai"
        assert e2["singer"] == "Kai P"
```

- [ ] **Step 4: Run e2e tests**

```
cd kj-controller && pytest tests/e2e/test_singer_stats_e2e.py -v
```

Expected: new tests pass. If existing tests use Playwright rather than `client`, swap the fixture name and assertion shape accordingly to match the file.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/tests/e2e/test_singer_stats_e2e.py
git commit -m "test(e2e): done-singer Left round-trip and Split scenarios"
```

---

## Task 14: Full test run + coverage check

- [ ] **Step 1: Run the full suite**

```
cd kj-controller && pytest --cov --cov-report=term
```

Expected: all tests pass; coverage at or above baseline for `rotation_store.py`, `rotation.py`, `routes.py`.

- [ ] **Step 2: If any pre-existing tests broke**

Read the failure, see if it's legitimate (code change invalidated an assumption) or ours (test needs updating). Fix the test to match the new behavior — the goal is green, not to dodge work.

- [ ] **Step 3: Commit any final fixes**

```bash
git add -A
git commit -m "test: adjust existing tests for singer management changes"
```

(Skip if no fixes needed.)

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Sectioning Active/Done/Gone → Task 9
- ✅ Mark done singers as Left → Tasks 1, 2, 7
- ✅ Left-set consistency with rename/merge/archive → Task 3
- ✅ Show singer's songs inline → Tasks 5, 11
- ✅ Split singer → Tasks 4, 8, 12
- ✅ Label Remove→Left → Task 10
- ✅ Unit tests → Tasks 1-5 (store), Tasks 7-8 (routes)
- ✅ E2E tests → Task 13

**Placeholder scan:** Each step contains executable code or exact commands. No TBDs. ✅

**Type consistency:**
- `mark_singer_left(name)` / `unmark_singer_left(name)` / `get_left_singer_names()` — consistent across Tasks 1, 2, 3, 6, 7.
- `split_singer(source_name, new_name, entry_ids)` — consistent across Tasks 4, 6, 8, 12.
- Frontend function names: `buildSingerRow`, `buildSingerActions`, `renderSingerSection`, `toggleSingerSongs`, `openSplitModal` — all referenced consistently.
- localStorage keys: `kj-singers-{active,done,gone}-collapsed` — all three match the pattern.

**Scope:** Fits into one worktree, one PR. No decomposition needed.

---

## Out-of-scope (not in this plan)

- Multi-select reassign from the rotation list.
- Favourite/regular singer tagging.
- Cross-night singer history (querying `rotation_archive`).
