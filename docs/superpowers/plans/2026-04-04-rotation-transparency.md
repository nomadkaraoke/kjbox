# Rotation Transparency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add rotation rules display to the conky screen and a "paid" priority indicator visible both in the conky overlay and the KJ Controller web UI.

**Architecture:** Adds a `paid` column to the existing SQLite rotation schema, exposes it through the store → manager → REST API → frontend chain, and extends the conky display with a right-side rules panel and per-entry heart indicator. Static rules text is read from a file on disk.

**Tech Stack:** Python (Flask, SQLite), vanilla JavaScript, Conky, HTML/CSS (printable rules)

**Spec:** `docs/archive/2026-04-04-rotation-transparency-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `kj-controller/rotation_store.py` | Add `paid` column, migration, `set_paid()` method |
| Modify | `kj-controller/rotation.py` | `set_paid()` delegation, include `paid` in display cache |
| Modify | `kj-controller/routes.py` | `POST /rotation/set-paid` endpoint |
| Modify | `kj-controller/static/app.js` | Dropdown toggle, ♥ indicator in rotation list |
| Modify | `kj-controller/static/style.css` | Style for paid heart indicator |
| Modify | `desktop/rotation.conkyrc` | Add `${execpi}` for rules panel |
| Modify | `desktop/rotation_data.py` | `--rules` mode, ♥ for paid entries |
| Create | `desktop/rotation_rules.txt` | Static rules text (5 lines) |
| Create | `desktop/rotation_rules_printable.html` | Printable detailed rules |
| Modify | `kj-controller/tests/unit/test_rotation_store.py` | Tests for `set_paid`, schema migration |
| Modify | `kj-controller/tests/unit/test_rotation.py` | Tests for manager `set_paid`, cache includes `paid` |
| Modify | `kj-controller/tests/unit/test_rotation_data.py` | Tests for ♥ indicator, `--rules` mode |
| Modify | `kj-controller/tests/integration/test_rotation_routes.py` | Tests for `/rotation/set-paid` endpoint |

---

### Task 1: Add `paid` column to SQLite schema

**Files:**
- Modify: `kj-controller/rotation_store.py:98-105` (migration list)
- Modify: `kj-controller/tests/unit/test_rotation_store.py:41-47` (column check)
- Test: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write failing test for `paid` column in schema**

Add `"paid"` to the expected columns set and add a test for `set_paid` in `test_rotation_store.py`:

```python
# In TestSchemaInit.test_rotation_entries_columns, add "paid" to expected set:
expected = {
    "id", "singer", "song_artist", "status", "notes",
    "position", "file_path", "duration",
    "download_source", "download_status", "download_id", "url_fallback",
    "gen_job_id", "gen_status", "paid",
    "created_at", "updated_at",
}
```

Add a new test class at the end of the file:

```python
# ---------------------------------------------------------------------------
# Paid flag
# ---------------------------------------------------------------------------

class TestSetPaid:
    def test_set_paid_true(self, store):
        entry = store.add_entry("Alice", "Song A")
        store.set_paid(entry["id"], True)
        updated = store.get_entry(entry["id"])
        assert updated["paid"] == 1

    def test_set_paid_false(self, store):
        entry = store.add_entry("Alice", "Song A")
        store.set_paid(entry["id"], True)
        store.set_paid(entry["id"], False)
        updated = store.get_entry(entry["id"])
        assert updated["paid"] == 0

    def test_set_paid_default_is_zero(self, store):
        entry = store.add_entry("Alice", "Song A")
        assert entry["paid"] == 0

    def test_set_paid_nonexistent_entry(self, store):
        with pytest.raises(ValueError, match="not found"):
            store.set_paid(9999, True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency/kj-controller && pytest tests/unit/test_rotation_store.py::TestSchemaInit::test_rotation_entries_columns tests/unit/test_rotation_store.py::TestSetPaid -v`

Expected: FAIL — `"paid"` not in columns, `set_paid` not defined.

- [ ] **Step 3: Add `paid` column migration and `set_paid` method**

In `rotation_store.py`, add to the migrations list (line ~98-105):

```python
migrations = [
    ("download_source", "TEXT DEFAULT NULL"),
    ("download_status", "TEXT DEFAULT NULL"),
    ("download_id", "TEXT DEFAULT NULL"),
    ("url_fallback", "TEXT DEFAULT NULL"),
    ("gen_job_id", "TEXT DEFAULT NULL"),
    ("gen_status", "TEXT DEFAULT NULL"),
    ("paid", "INTEGER NOT NULL DEFAULT 0"),
]
```

Add `set_paid` method after the `get_entry_by_gen_job_id` method (after line ~428):

```python
# ------------------------------------------------------------------
# Paid flag
# ------------------------------------------------------------------

def set_paid(self, entry_id, paid):
    """Set paid priority flag on a rotation entry.

    Raises ValueError if entry_id not found.
    """
    if self.get_entry(entry_id) is None:
        raise ValueError(f"Entry {entry_id} not found")
    conn = self._get_conn()
    conn.execute(
        "UPDATE rotation_entries SET paid = ?, updated_at = datetime('now', 'localtime') "
        "WHERE id = ?",
        (int(bool(paid)), entry_id),
    )
    conn.commit()
    return self.get_entry(entry_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency/kj-controller && pytest tests/unit/test_rotation_store.py::TestSchemaInit::test_rotation_entries_columns tests/unit/test_rotation_store.py::TestSetPaid -v`

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat(rotation): add paid column to schema with set_paid method"
```

---

### Task 2: Add `set_paid` to RotationManager and include `paid` in display cache

**Files:**
- Modify: `kj-controller/rotation.py:161-165` (after `set_gen_status`), `rotation.py:218-230` (display cache)
- Test: `kj-controller/tests/unit/test_rotation.py`

- [ ] **Step 1: Write failing tests**

Add to `test_rotation.py`:

```python
class TestSetPaid:
    def test_set_paid_delegates_to_store(self, mgr):
        entry = mgr.add_entry("Alice", "Song A")
        result = mgr.set_paid(entry["id"], True)
        assert result["paid"] == 1

    def test_set_paid_triggers_cache_write(self, mgr, tmp_path, monkeypatch):
        cache_path = str(tmp_path / "cache.json")
        monkeypatch.setattr("rotation.ROTATION_CACHE_FILE", cache_path)
        entry = mgr.add_entry("Alice", "Song A")
        mgr.set_paid(entry["id"], True)
        import json
        with open(cache_path) as f:
            data = json.load(f)
        assert data["queue"][0]["paid"] is True


class TestDisplayCacheIncludesPaid:
    def test_cache_has_paid_field(self, mgr, tmp_path, monkeypatch):
        cache_path = str(tmp_path / "cache.json")
        monkeypatch.setattr("rotation.ROTATION_CACHE_FILE", cache_path)
        mgr.add_entry("Alice", "Song A")
        import json
        with open(cache_path) as f:
            data = json.load(f)
        assert "paid" in data["queue"][0]
        assert data["queue"][0]["paid"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency/kj-controller && pytest tests/unit/test_rotation.py::TestSetPaid tests/unit/test_rotation.py::TestDisplayCacheIncludesPaid -v`

Expected: FAIL — `set_paid` not defined on manager, `paid` not in cache.

- [ ] **Step 3: Add `set_paid` to RotationManager and update display cache**

In `rotation.py`, add after `set_gen_status` method (after line ~165):

```python
def set_paid(self, entry_id, paid):
    """Set paid priority flag on a rotation entry."""
    entry = self.store.set_paid(entry_id, paid)
    self._after_mutation()
    return entry
```

In `_write_display_cache`, update the queue list comprehension (line ~222-227):

```python
queue = [
    {
        "singer": e["singer"],
        "song_artist": e["song_artist"],
        "status": e["status"],
        "paid": bool(e["paid"]),
    }
    for e in entries
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency/kj-controller && pytest tests/unit/test_rotation.py::TestSetPaid tests/unit/test_rotation.py::TestDisplayCacheIncludesPaid -v`

Expected: All PASS.

- [ ] **Step 5: Run all rotation tests to check for regressions**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency/kj-controller && pytest tests/unit/test_rotation.py tests/unit/test_rotation_store.py -v`

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency
git add kj-controller/rotation.py kj-controller/tests/unit/test_rotation.py
git commit -m "feat(rotation): add set_paid to manager, include paid in display cache"
```

---

### Task 3: Add `POST /rotation/set-paid` REST endpoint

**Files:**
- Modify: `kj-controller/routes.py` (after `/rotation/unlink` route, ~line 1932)
- Test: `kj-controller/tests/integration/test_rotation_routes.py`

- [ ] **Step 1: Write failing integration tests**

Add to `test_rotation_routes.py`:

```python
class TestSetPaidRoute:
    def test_set_paid_success(self, rotation_client, mock_rotation):
        mock_rotation.set_paid.return_value = {
            "id": 1, "singer": "Alice", "paid": 1,
        }
        mock_rotation.get_rotation.return_value = [
            {"id": 1, "singer": "Alice", "song_artist": "Song A", "status": "Waiting", "paid": 1},
        ]
        resp = rotation_client.post('/rotation/set-paid',
            data=json.dumps({"id": 1, "paid": True}),
            content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "entries" in data
        mock_rotation.set_paid.assert_called_once_with(1, True)

    def test_set_paid_missing_id(self, rotation_client):
        resp = rotation_client.post('/rotation/set-paid',
            data=json.dumps({"paid": True}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_set_paid_invalid_id(self, rotation_client):
        resp = rotation_client.post('/rotation/set-paid',
            data=json.dumps({"id": "abc", "paid": True}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_set_paid_not_configured(self, no_rotation_client):
        resp = no_rotation_client.post('/rotation/set-paid',
            data=json.dumps({"id": 1, "paid": True}),
            content_type='application/json')
        assert resp.status_code == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency/kj-controller && pytest tests/integration/test_rotation_routes.py::TestSetPaidRoute -v`

Expected: FAIL — 404 (route not defined).

- [ ] **Step 3: Add the route**

In `routes.py`, add after the `/rotation/unlink` route (after line ~1932):

```python
@routes_bp.route('/rotation/set-paid', methods=['POST'])
def set_rotation_paid():
    """Toggle paid priority flag on a rotation entry."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    raw_id = data.get('id')
    if raw_id is None:
        return jsonify({"error": "id is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "id must be an integer"}), 400
    if entry_id < 1:
        return jsonify({"error": "id must be >= 1"}), 400

    paid = bool(data.get('paid', False))

    try:
        rotation.set_paid(entry_id, paid)
        entries = rotation.get_rotation()
        _add_time_estimates(entries)
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency/kj-controller && pytest tests/integration/test_rotation_routes.py::TestSetPaidRoute -v`

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency
git add kj-controller/routes.py kj-controller/tests/integration/test_rotation_routes.py
git commit -m "feat(rotation): add POST /rotation/set-paid endpoint"
```

---

### Task 4: Add ♥ paid indicator and dropdown toggle in KJ Controller frontend

**Files:**
- Modify: `kj-controller/static/app.js:3088-3134` (dropdown menu), `app.js:2990-3010` (singer info rendering)
- Modify: `kj-controller/static/style.css`

- [ ] **Step 1: Add paid heart indicator to rotation entry rendering**

In `app.js`, find where the singer name span and song span are appended to `info` (~line 2955-2957):

```javascript
info.appendChild(num);
info.appendChild(name);
if (entry.song_artist) info.appendChild(song);
```

Add the paid heart indicator right after `info.appendChild(name)`:

```javascript
info.appendChild(num);
info.appendChild(name);
if (entry.paid) {
    const heart = document.createElement('span');
    heart.className = 'rotation-paid-heart';
    heart.textContent = ' ♥';
    heart.title = 'Paid priority';
    info.appendChild(heart);
}
if (entry.song_artist) info.appendChild(song);
```

- [ ] **Step 2: Add "Mark as Paid" toggle to the "..." dropdown menu**

In `app.js`, in the `moreBtn.onclick` handler (~line 3088-3134), after the unlink section (after line ~3130), add:

```javascript
// Add paid toggle
const paidSep = document.createElement('div');
paidSep.className = 'rotation-dropdown-sep';
dropdown.appendChild(paidSep);
const paidItem = document.createElement('button');
paidItem.className = 'rotation-dropdown-item';
paidItem.textContent = entry.paid ? 'Remove Paid ♥' : 'Mark as Paid ♥';
paidItem.onclick = async (ev) => {
    ev.stopPropagation();
    dropdown.remove();
    try {
        const resp = await fetch('/rotation/set-paid', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id: entry.id, paid: !entry.paid }),
        });
        const data = await resp.json();
        if (data.entries) { rotationData = data.entries; renderRotation(rotationData); }
        showRotationIndicator('success');
    } catch (err) {
        showRotationIndicator('error');
    }
};
dropdown.appendChild(paidItem);
```

This should go just before the `row.appendChild(dropdown)` line.

- [ ] **Step 3: Add CSS for paid heart**

In `style.css`, add:

```css
.rotation-paid-heart {
    color: #e74c3c;
    font-size: 0.9em;
}
```

- [ ] **Step 4: Test manually by loading the KJ Controller in a browser**

Open `http://localhost:5000` (or whatever port kj-controller runs on locally), add a rotation entry, open the "..." dropdown, click "Mark as Paid ♥", verify:
- The ♥ appears next to the singer name
- The dropdown text changes to "Remove Paid ♥" on subsequent opens
- Toggling off removes the ♥

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency
git add kj-controller/static/app.js kj-controller/static/style.css
git commit -m "feat(frontend): add paid heart indicator and dropdown toggle"
```

---

### Task 5: Add ♥ indicator for paid entries in conky display

**Files:**
- Modify: `desktop/rotation_data.py:96-117` (format_conky function)
- Test: `kj-controller/tests/unit/test_rotation_data.py`

- [ ] **Step 1: Write failing tests**

Add to `test_rotation_data.py`:

```python
class TestPaidIndicator:
    def test_paid_entry_shows_heart(self, capsys):
        rotation_data.format_conky([
            {"singer": "Alice", "song_artist": "Test Song", "status": "Waiting", "paid": True},
        ])
        output = capsys.readouterr().out
        assert "♥" in output
        assert "Alice" in output

    def test_unpaid_entry_no_heart(self, capsys):
        rotation_data.format_conky([
            {"singer": "Bob", "song_artist": "Test Song", "status": "Waiting", "paid": False},
        ])
        output = capsys.readouterr().out
        assert "♥" not in output

    def test_paid_missing_field_no_heart(self, capsys):
        """Backward compat: old cache data without paid field."""
        rotation_data.format_conky([
            {"singer": "Carol", "song_artist": "Test Song", "status": "Waiting"},
        ])
        output = capsys.readouterr().out
        assert "♥" not in output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency/kj-controller && pytest tests/unit/test_rotation_data.py::TestPaidIndicator -v`

Expected: FAIL — ♥ not in output for paid entry.

- [ ] **Step 3: Add paid heart to conky output**

In `desktop/rotation_data.py`, add a color constant near the top (after `COLOR_TEXT`):

```python
COLOR_PAID_HEART = "e74c3c"  # red for paid heart
```

In the `format_conky` function, modify the singer line print (line ~112-113). Replace:

```python
        # Singer line: single font block so number and name share baseline
        print(f"{MARGIN}${{font {FONT_NAME}}}${{color ffffff}}{idx}. ${{color}}"
              f"${{color {COLOR_NAME}}}{entry['singer']}${{color}}${{font}}{entry_badge}")
```

With:

```python
        # Paid heart indicator
        paid_mark = f" ${{color {COLOR_PAID_HEART}}}♥${{color}}" if entry.get("paid") else ""

        # Singer line: single font block so number and name share baseline
        print(f"{MARGIN}${{font {FONT_NAME}}}${{color ffffff}}{idx}. ${{color}}"
              f"${{color {COLOR_NAME}}}{entry['singer']}${{color}}{paid_mark}${{font}}{entry_badge}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency/kj-controller && pytest tests/unit/test_rotation_data.py -v`

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency
git add desktop/rotation_data.py kj-controller/tests/unit/test_rotation_data.py
git commit -m "feat(conky): show red heart for paid entries in rotation display"
```

---

### Task 6: Add rules panel to conky display

**Files:**
- Create: `desktop/rotation_rules.txt`
- Modify: `desktop/rotation_data.py` (add `--rules` mode)
- Modify: `desktop/rotation.conkyrc` (add rules `${execpi}`)
- Test: `kj-controller/tests/unit/test_rotation_data.py`

- [ ] **Step 1: Create the rules text file**

Create `desktop/rotation_rules.txt`:

```
First come, first sing
New singers get priority
Multiple songs? We'll spread them out
Need to leave? Ask the KJ
♥ = paid priority ($20+)
```

- [ ] **Step 2: Write failing tests for `--rules` mode**

Add to `test_rotation_data.py`:

```python
class TestRulesMode:
    def test_rules_output_contains_header(self, capsys, tmp_path, monkeypatch):
        rules_file = str(tmp_path / "rules.txt")
        with open(rules_file, "w") as f:
            f.write("First come, first sing\nNew singers get priority\n")
        monkeypatch.setattr(rotation_data, "RULES_FILE", rules_file)
        rotation_data.format_rules()
        output = capsys.readouterr().out
        assert "HOW IT WORKS" in output

    def test_rules_output_contains_bullets(self, capsys, tmp_path, monkeypatch):
        rules_file = str(tmp_path / "rules.txt")
        with open(rules_file, "w") as f:
            f.write("First come, first sing\nNew singers get priority\n")
        monkeypatch.setattr(rotation_data, "RULES_FILE", rules_file)
        rotation_data.format_rules()
        output = capsys.readouterr().out
        assert "First come, first sing" in output
        assert "New singers get priority" in output

    def test_rules_missing_file_shows_nothing(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setattr(rotation_data, "RULES_FILE", str(tmp_path / "nonexistent.txt"))
        rotation_data.format_rules()
        output = capsys.readouterr().out
        assert output.strip() == ""

    def test_rules_positioned_on_right(self, capsys, tmp_path, monkeypatch):
        rules_file = str(tmp_path / "rules.txt")
        with open(rules_file, "w") as f:
            f.write("Test rule\n")
        monkeypatch.setattr(rotation_data, "RULES_FILE", rules_file)
        rotation_data.format_rules()
        output = capsys.readouterr().out
        assert "${goto 1020}" in output
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency/kj-controller && pytest tests/unit/test_rotation_data.py::TestRulesMode -v`

Expected: FAIL — `format_rules` not defined, `RULES_FILE` not defined.

- [ ] **Step 4: Implement `format_rules` and `--rules` mode**

In `desktop/rotation_data.py`, add constants near the top:

```python
# Rules panel
RULES_FILE = "/opt/nomad/kjbox/desktop/rotation_rules.txt"
RULES_MARGIN = "${goto 1020}"  # right column start
FONT_RULES_HEADER = "DejaVu Sans:bold:size=28"
FONT_RULES_BODY = "DejaVu Sans:size=18"
COLOR_RULES_HEADER = "ffffff"
COLOR_RULES_BODY = "8892a4"
```

Add the `format_rules` function after `format_conky`:

```python
def format_rules():
    """Output conky markup for the rules panel on the right side of the screen."""
    try:
        with open(RULES_FILE) as f:
            lines = [line.strip() for line in f if line.strip()]
    except OSError:
        return

    # Header
    print(f"{RULES_MARGIN}${{voffset -30}}${{font {FONT_RULES_HEADER}}}${{color {COLOR_RULES_HEADER}}}HOW IT WORKS${{color}}${{font}}")
    print()

    # Bullet points
    for line in lines:
        print(f"{RULES_MARGIN}${{font {FONT_RULES_BODY}}}${{color {COLOR_RULES_BODY}}}• {line}${{color}}${{font}}")
```

In the `main()` function, add handling for `--rules`:

```python
def main():
    stats_only = "--stats" in sys.argv
    rules_only = "--rules" in sys.argv

    if rules_only:
        format_rules()
        return

    cached = read_local_cache()
    if cached is None:
        print("--" if stats_only else f"{MARGIN}${{color {COLOR_DEFAULT}}}${{font DejaVu Sans:size=28}}Offline${{font}}${{color}}")
        return

    queue, stats = cached

    if stats_only:
        parts = []
        if stats.get("started"):
            parts.append(f"Started: {stats['started']}")
        parts.append(f"{stats['singers']} singers | {stats['sung']} sung | {stats['queued']} queued")
        print("    ".join(parts))
    else:
        format_conky(queue)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency/kj-controller && pytest tests/unit/test_rotation_data.py -v`

Expected: All PASS.

- [ ] **Step 6: Update conky config to include rules panel**

In `desktop/rotation.conkyrc`, update `conky.text` to add the rules panel. Replace:

```lua
conky.text = [[
${image /opt/nomad/kjbox/desktop/rotation-bg.png -p 0,0 -s 1920x1080}\
${voffset 70}${goto 90}${font DejaVu Sans:bold:size=40}${color ffffff}ROTATION${color}${font}${goto 460}${font DejaVu Sans:size=21}${color 8892a4}${execpi 3 /usr/bin/python3 /opt/nomad/kjbox/desktop/rotation_data.py --stats}${color}${font}
${execpi 3 /usr/bin/python3 /opt/nomad/kjbox/desktop/rotation_data.py}
]]
```

With:

```lua
conky.text = [[
${image /opt/nomad/kjbox/desktop/rotation-bg.png -p 0,0 -s 1920x1080}\
${voffset 70}${goto 90}${font DejaVu Sans:bold:size=40}${color ffffff}ROTATION${color}${font}${goto 460}${font DejaVu Sans:size=21}${color 8892a4}${execpi 3 /usr/bin/python3 /opt/nomad/kjbox/desktop/rotation_data.py --stats}${color}${font}
${execpi 3 /usr/bin/python3 /opt/nomad/kjbox/desktop/rotation_data.py}
${voffset -900}${execpi 60 /usr/bin/python3 /opt/nomad/kjbox/desktop/rotation_data.py --rules}
]]
```

Notes:
- `${voffset -900}` jumps the cursor back to near the top so the rules panel renders at the top of the right column. The exact value may need tuning on the actual device.
- Rules refresh every 60 seconds (not 3s like the queue) since the rules text file rarely changes.

- [ ] **Step 7: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency
git add desktop/rotation_rules.txt desktop/rotation_data.py desktop/rotation.conkyrc kj-controller/tests/unit/test_rotation_data.py
git commit -m "feat(conky): add rules panel on right side of rotation display"
```

---

### Task 7: Create printable rules document

**Files:**
- Create: `desktop/rotation_rules_printable.html`

- [ ] **Step 1: Create the printable HTML file**

Create `desktop/rotation_rules_printable.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Karaoke Rotation Rules - Nomad Karaoke</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 700px;
            margin: 40px auto;
            padding: 20px;
            color: #1a1a1a;
            line-height: 1.6;
        }
        h1 {
            font-size: 28px;
            margin-bottom: 4px;
        }
        .subtitle {
            font-size: 16px;
            color: #666;
            margin-bottom: 32px;
        }
        .rule {
            margin-bottom: 24px;
            padding-left: 8px;
        }
        .rule h2 {
            font-size: 18px;
            margin-bottom: 4px;
        }
        .rule p {
            font-size: 15px;
            color: #444;
        }
        .rule .number {
            display: inline-block;
            width: 28px;
            height: 28px;
            line-height: 28px;
            text-align: center;
            background: #1a1a1a;
            color: #fff;
            border-radius: 50%;
            font-size: 14px;
            font-weight: bold;
            margin-right: 8px;
            vertical-align: middle;
        }
        .heart { color: #e74c3c; }
        footer {
            margin-top: 40px;
            padding-top: 16px;
            border-top: 1px solid #ddd;
            font-size: 13px;
            color: #999;
            text-align: center;
        }
        @media print {
            body { margin: 0; padding: 20px; }
            footer { position: fixed; bottom: 20px; left: 0; right: 0; }
        }
    </style>
</head>
<body>
    <h1>Karaoke Rotation Rules</h1>
    <p class="subtitle">How we keep things fair and fun</p>

    <div class="rule">
        <h2><span class="number">1</span> First come, first sing</h2>
        <p>The default order is the order you submit your request. If Jim, Bob, and Jenny each give me a song, they'll sing in that order.</p>
    </div>

    <div class="rule">
        <h2><span class="number">2</span> New singers get priority</h2>
        <p>First time singing tonight? You'll get bumped up to sing within the next few songs, so everyone gets a chance to perform at least once. The next 3 people in line won't be moved — we respect their spot too.</p>
    </div>

    <div class="rule">
        <h2><span class="number">3</span> Multiple songs welcome</h2>
        <p>Submit as many songs as you want! We'll spread them out in the rotation so nobody sings twice in a row. This keeps things fair while letting you sing as much as you'd like.</p>
    </div>

    <div class="rule">
        <h2><span class="number">4</span> Need to leave early?</h2>
        <p>Let the KJ know and we'll try to get you one last song before you go. If it's a busy night and you've already sung 5+ times, we may not be able to accommodate — but we'll always try.</p>
    </div>

    <div class="rule">
        <h2><span class="number">5</span> Paid priority <span class="heart">♥</span></h2>
        <p>Want to skip ahead? Pay $20+ and you'll be bumped up to sing very soon. Paid entries are marked with a <span class="heart">♥</span> on the rotation screen so everyone can see it's fair.</p>
    </div>

    <footer>Nomad Karaoke — nomadkaraoke.com</footer>
</body>
</html>
```

- [ ] **Step 2: Open in browser and verify it looks good for printing**

Open `desktop/rotation_rules_printable.html` in a browser. Check:
- Fits on one printed page
- Clean layout, readable fonts
- Print preview (Cmd+P) shows clean output

- [ ] **Step 3: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency
git add desktop/rotation_rules_printable.html
git commit -m "feat: add printable rotation rules for laminating"
```

---

### Task 8: Run full test suite and verify

- [ ] **Step 1: Run all tests**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency/kj-controller && pytest -v`

Expected: All PASS, no regressions.

- [ ] **Step 2: Run with coverage**

Run: `cd /Users/andrew/Projects/nomadkaraoke/kjbox-rotation-transparency/kj-controller && pytest --cov --cov-report=term`

Expected: Coverage at or above 70%.

- [ ] **Step 3: Fix any failures**

If any tests fail, fix them before proceeding.
