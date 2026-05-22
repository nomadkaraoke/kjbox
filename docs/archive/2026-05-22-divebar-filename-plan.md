# Divebar Download Filename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ID-based filenames for Divebar GCS-mirror downloads with human-readable `<brand_code|"DB"> - <artist> - <title>.mp4` filenames at all three enqueue sites.

**Architecture:** A single pure helper `build_divebar_filename` in `utils.py` is called server-side from the three Divebar enqueue paths in `routes.py`. The JS callers send structured `{file_id, artist, title, brand_code}` instead of pre-built `filename`. The `divebar__` on-disk prefix in `media.download_from_url` is unchanged.

**Tech Stack:** Python 3 + Flask backend, vanilla JS frontend, pytest, SQLite.

**Spec:** `docs/archive/2026-05-22-divebar-filename-design.md`

---

### Task 1: `build_divebar_filename` helper

**Files:**
- Modify: `kj-controller/utils.py` (add new function near `sanitize_filename_part` at line 21)
- Test: `kj-controller/tests/unit/test_utils.py`

- [ ] **Step 1: Write the failing test**

Append to `kj-controller/tests/unit/test_utils.py`:

```python
from utils import build_divebar_filename


def test_build_divebar_filename_all_fields():
    assert build_divebar_filename("WTF", "Queen", "Bohemian Rhapsody") == \
        "WTF - Queen - Bohemian Rhapsody.mp4"


def test_build_divebar_filename_missing_brand_code_uses_db():
    assert build_divebar_filename(None, "Queen", "Bohemian Rhapsody") == \
        "DB - Queen - Bohemian Rhapsody.mp4"


def test_build_divebar_filename_empty_brand_code_uses_db():
    assert build_divebar_filename("", "Queen", "Bohemian Rhapsody") == \
        "DB - Queen - Bohemian Rhapsody.mp4"


def test_build_divebar_filename_missing_artist():
    assert build_divebar_filename("WTF", None, "Bohemian Rhapsody") == \
        "WTF - Bohemian Rhapsody.mp4"


def test_build_divebar_filename_missing_title():
    assert build_divebar_filename("WTF", "Queen", None) == \
        "WTF - Queen.mp4"


def test_build_divebar_filename_only_brand_returns_none():
    # Brand prefix alone isn't useful — caller falls back to file_id name.
    assert build_divebar_filename("WTF", None, None) is None
    assert build_divebar_filename(None, None, None) is None
    assert build_divebar_filename("WTF", "", "") is None


def test_build_divebar_filename_sanitizes_unsafe_chars():
    # Slashes etc. must be removed (filesystem-unsafe).
    result = build_divebar_filename("WTF", "Queen/Bowie", "Under Pressure")
    assert "/" not in result
    assert result.endswith(".mp4")


def test_build_divebar_filename_custom_extension():
    assert build_divebar_filename("WTF", "Queen", "Song", ext=".zip") == \
        "WTF - Queen - Song.zip"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && pytest tests/unit/test_utils.py -v -k build_divebar`
Expected: All 8 tests FAIL with `ImportError: cannot import name 'build_divebar_filename'`.

- [ ] **Step 3: Implement the helper**

Append to `kj-controller/utils.py`:

```python
def build_divebar_filename(brand_code, artist, title, ext=".mp4"):
    """Build a human-readable filename for a Divebar download.

    Format: `<brand_code | "DB"> - <artist> - <title><ext>`. Returns None
    when neither artist nor title is present — caller applies its own
    fallback (typically `divebar-{file_id}.mp4`).
    """
    bc = (brand_code or "DB").strip() or "DB"
    artist_part = sanitize_filename_part(artist).strip() if artist else ""
    title_part = sanitize_filename_part(title).strip() if title else ""
    if not artist_part and not title_part:
        return None
    parts = [bc]
    if artist_part:
        parts.append(artist_part)
    if title_part:
        parts.append(title_part)
    return " - ".join(parts) + ext
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_utils.py -v -k build_divebar`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/utils.py kj-controller/tests/unit/test_utils.py
git commit -m "feat(media): add build_divebar_filename helper

Pure helper that assembles \`brand_code - artist - title.mp4\` from
structured metadata. Returns None when there's nothing useful to build,
so callers can fall back to file_id-based names."
```

---

### Task 2: `/divebar/download` route uses builder

**Files:**
- Modify: `kj-controller/routes.py:1361-1404` (the `divebar_download` route)
- Test: `kj-controller/tests/integration/test_routes.py`

- [ ] **Step 1: Write failing tests**

Append to `kj-controller/tests/integration/test_routes.py` (use existing fixtures `client`, `app` — match the style of nearby tests):

```python
from unittest.mock import patch


class TestDivebarDownloadFilename:
    def test_uses_structured_fields(self, client, app):
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/x.mp4"):
            resp = client.post('/divebar/download', json={
                "file_id": "abc123",
                "artist": "Queen",
                "title": "Bohemian Rhapsody",
                "brand_code": "WTF",
            })
        assert resp.status_code == 200
        items = app.download_queue['items']
        assert items[-1]['title'] == "WTF - Queen - Bohemian Rhapsody.mp4"
        assert items[-1]['divebar_file_id'] == "abc123"

    def test_falls_back_to_db_when_brand_missing(self, client, app):
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/x.mp4"):
            resp = client.post('/divebar/download', json={
                "file_id": "abc",
                "artist": "Queen",
                "title": "Bohemian Rhapsody",
            })
        assert resp.status_code == 200
        items = app.download_queue['items']
        assert items[-1]['title'] == "DB - Queen - Bohemian Rhapsody.mp4"

    def test_falls_back_to_file_id_when_no_metadata(self, client, app):
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/x.mp4"):
            resp = client.post('/divebar/download', json={"file_id": "abc"})
        assert resp.status_code == 200
        items = app.download_queue['items']
        assert items[-1]['title'] == "divebar-abc.mp4"
```

Check that fixtures `client` and `app` exist in the file — open `tests/integration/test_routes.py` and confirm. If they're scoped differently (e.g. `dl_client`/`dl_app`), use those names instead and reuse the matching fixture pattern.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/integration/test_routes.py::TestDivebarDownloadFilename -v`
Expected: Three tests FAIL — current route ignores artist/title and falls back to `f"Divebar track {file_id[:8]}"`.

- [ ] **Step 3: Update the route**

Replace `kj-controller/routes.py:1361-1404` (the entire `divebar_download` function) with:

```python
@routes_bp.route('/divebar/download', methods=['POST'])
def divebar_download():
    """Download a Divebar track by file_id. Queues it like a YouTube download.

    Body: {file_id (required), artist, title, brand_code}.
    """
    data = request.get_json(silent=True) or {}
    file_id = data.get('file_id', '').strip()
    if not file_id:
        return jsonify({"error": "file_id is required"}), 400

    artist = data.get('artist', '').strip()
    title = data.get('title', '').strip()
    brand_code = data.get('brand_code', '').strip()

    cfg = current_app.kj_config
    url = divebar.get_download_url(file_id, config=cfg)
    if not url:
        return jsonify({"error": "Could not get download URL"}), 500

    filename = build_divebar_filename(brand_code, artist, title) \
               or f"divebar-{file_id}.mp4"

    app = current_app._get_current_object()
    from uuid import uuid4
    with app._download_lock:
        items = app.download_queue['items']
        active = [i for i in items if i['status'] in ('queued', 'downloading')]
        if len(active) >= 5:
            return jsonify({"error": "Queue is full (max 5)"}), 409

        item = {
            'id': str(uuid4()),
            'url': url,
            'status': 'queued',
            'title': filename,
            'error': None,
            'file_path': None,
            'added_at': time.time(),
            'completed_at': None,
            'source': 'divebar',
            'source_detail': divebar.classify_download_url(url),
            'divebar_file_id': file_id,
        }
        items.append(item)
        log_message(f"Queued Divebar download: {filename}", cfg)

        if not app.download_queue['worker_running']:
            app.download_queue['worker_running'] = True
            threading.Thread(target=_download_worker, args=[app], daemon=True).start()

    return jsonify({"success": True, "id": item['id']})
```

Add the import at the top of `routes.py` (find the existing line `from utils import ...` or `from media import ...` and add `build_divebar_filename` to the appropriate import — it's defined in `utils.py`):

```python
from utils import log_message, build_divebar_filename
```

(Merge with existing `from utils import ...` line if one exists; if `log_message` is already imported via a star import or different module, just add the new symbol on its own line.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/integration/test_routes.py::TestDivebarDownloadFilename -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_routes.py
git commit -m "feat(divebar): /divebar/download builds filename from artist/title

Route now accepts structured {file_id, artist, title, brand_code} and
calls build_divebar_filename to produce human-readable on-disk names
like \`WTF - Queen - Bohemian Rhapsody.mp4\`. Falls back to
\`divebar-{file_id}.mp4\` only when both artist and title are missing."
```

---

### Task 3: `/rotation/download-and-link` route uses builder for divebar

**Files:**
- Modify: `kj-controller/routes.py:2751-2839` (the divebar branch of `rotation_download_and_link`)
- Modify: `kj-controller/tests/integration/test_download_link_routes.py` (existing tests pass `"filename": "..."` for divebar, switch to structured fields)

- [ ] **Step 1: Update existing tests to use structured fields**

Open `kj-controller/tests/integration/test_download_link_routes.py`. For every divebar test (the ones containing `"source": "divebar"`), replace the body `"filename": "..."` with `"artist": "Queen", "title": "Bohemian Rhapsody", "brand_code": "KFN-1234"` style fields. Specifically:

- Line ~37-44 (`test_divebar_download`): replace `"filename": "KFN-1234 - Queen - Bohemian Rhapsody.mp4"` with `"artist": "Queen", "title": "Bohemian Rhapsody", "brand_code": "KFN"`.
- Line ~58-61 (`test_divebar_download_classifies_gcs_url`): replace `"filename": "x.mp4"` with `"artist": "Song", "title": "Track"`.
- Line ~73-76 (`test_divebar_download_classifies_drive_url`): same replacement as above.
- Any other divebar test bodies in this file: same replacement.

YouTube tests (line ~80-94 onwards) keep `"filename": "..."` — that path is unchanged.

- [ ] **Step 2: Add a new test asserting the builder output**

Append a new class to `kj-controller/tests/integration/test_download_link_routes.py`:

```python
class TestDownloadAndLinkDivebarFilename:
    def test_builds_filename_from_structured_fields(self, dl_client, dl_app):
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice", "song_artist": "Bohemian Rhapsody"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/x.mp4"):
            dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "id": entry_id, "source": "divebar", "file_id": "abc",
                    "artist": "Queen", "title": "Bohemian Rhapsody",
                    "brand_code": "WTF",
                }),
                content_type='application/json')
        items = dl_app.download_queue['items']
        assert items[-1]['title'] == "WTF - Queen - Bohemian Rhapsody.mp4"

    def test_falls_back_to_file_id_when_no_metadata(self, dl_client, dl_app):
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Bob", "song_artist": "X"}),
            content_type='application/json')
        entry_id = resp.get_json()["entry"]["id"]
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/x.mp4"):
            dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "id": entry_id, "source": "divebar", "file_id": "xyz",
                }),
                content_type='application/json')
        items = dl_app.download_queue['items']
        assert items[-1]['title'] == "divebar-xyz.mp4"
```

- [ ] **Step 3: Run tests to verify failures**

Run: `cd kj-controller && pytest tests/integration/test_download_link_routes.py -v`
Expected: New `TestDownloadAndLinkDivebarFilename` tests FAIL. Existing divebar tests that had `filename` should still pass (the route ignores extra fields), but the new ones fail because the route currently uses `filename or f"divebar-{file_id}.mp4"`.

- [ ] **Step 4: Update the route**

Modify `kj-controller/routes.py` around line 2764-2808. Find this block:

```python
    if source == "divebar":
        file_id = data.get('file_id', '').strip()
        filename = data.get('filename', '').strip()
        if not file_id:
            return jsonify({"error": "file_id is required for divebar"}), 400
```

Replace with:

```python
    if source == "divebar":
        file_id = data.get('file_id', '').strip()
        artist = data.get('artist', '').strip()
        title = data.get('title', '').strip()
        brand_code = data.get('brand_code', '').strip()
        if not file_id:
            return jsonify({"error": "file_id is required for divebar"}), 400
```

Then find this block (around line 2799-2808):

```python
        if source == "divebar":
            download_url = divebar.get_download_url(file_id, cfg)
            if not download_url:
                return jsonify({"error": "Failed to get download URL from Divebar"}), 502
            queue_item = {
                'id': download_id,
                'url': download_url,
                'title': filename or f"divebar-{file_id}.mp4",
                'source': 'divebar',
                'source_detail': divebar.classify_download_url(download_url),
                'status': 'queued',
                'error': None,
                'rotation_entry_id': entry_id,
            }
```

Replace the `'title': filename or f"divebar-{file_id}.mp4",` line with:

```python
                'title': build_divebar_filename(brand_code, artist, title)
                         or f"divebar-{file_id}.mp4",
```

Also add `'divebar_file_id': file_id,` to the queue_item dict (mirrors Task 2 — keeps the file_id on the queue item for future use).

The YouTube branch is unchanged.

- [ ] **Step 5: Run all download-link tests**

Run: `cd kj-controller && pytest tests/integration/test_download_link_routes.py -v`
Expected: All tests pass, including the two new ones.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_download_link_routes.py
git commit -m "feat(rotation): download-and-link builds divebar filename from metadata

Switches the divebar branch of /rotation/download-and-link from
client-supplied \`filename\` to structured {artist, title, brand_code}
and uses build_divebar_filename server-side. YouTube branch unchanged."
```

---

### Task 4: Sing-request approval uses builder for divebar

**Files:**
- Modify: `kj-controller/routes.py:3124-3151` (the `approve_sing_request` divebar branch)
- Test: `kj-controller/tests/integration/test_sing_admin_routes.py` or `test_sing_public_routes.py` — pick the file that already has a passing test exercising the approval path.

- [ ] **Step 1: Find the existing approval test pattern**

Run: `cd kj-controller && grep -rn "approve_sing_request\|/rotation/requests/.*/approve\|source_type.*divebar" tests/integration/ | head -20`

Pick the most appropriate existing test file. The mostly likely candidate is `tests/integration/test_sing_admin_routes.py`. Read its existing fixtures and request-creation helpers so the new test reuses them.

- [ ] **Step 2: Write the failing test**

Append a new test to the test file identified in Step 1. Below is the structure assuming `test_sing_admin_routes.py` with a `client` fixture and a `store` helper to create requests directly (adapt names to whatever the file uses):

```python
class TestSingApproveDivebarFilename:
    def test_divebar_approval_builds_filename_from_song_metadata(
        self, client, app, store
    ):
        # Create a divebar sing-request with song_artist + song_title.
        req = store.create_request(
            singer_name="Alice",
            phone="",
            song_artist="Queen",
            song_title="Bohemian Rhapsody",
            source_type="divebar",
            source_ref="divebar-file-id-xyz",
            source_meta=None,
            notes="",
            additional_singers=[],
        )
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/x.mp4"):
            resp = client.post(f'/rotation/requests/{req["id"]}/approve')
        assert resp.status_code == 200
        items = app.download_queue['items']
        assert items[-1]['title'] == "DB - Queen - Bohemian Rhapsody.mp4"

    def test_divebar_approval_uses_brand_code_from_source_meta(
        self, client, app, store
    ):
        # kj_pick-style request whose source_meta carries brand_code from
        # the picked version (set by _pick_version_from_kj_pick).
        req = store.create_request(
            singer_name="Bob",
            phone="",
            song_artist="Queen",
            song_title="We Will Rock You",
            source_type="divebar",
            source_ref="divebar-file-id-abc",
            source_meta={"brand_code": "WTF"},
            notes="",
            additional_singers=[],
        )
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/x.mp4"):
            resp = client.post(f'/rotation/requests/{req["id"]}/approve')
        assert resp.status_code == 200
        items = app.download_queue['items']
        assert items[-1]['title'] == "WTF - Queen - We Will Rock You.mp4"
```

If the test file's existing pattern uses a different approve URL or test client signature, mirror that pattern. The asserts on `title` are the load-bearing part.

- [ ] **Step 3: Run the test to verify failure**

Run: `cd kj-controller && pytest tests/integration/test_sing_admin_routes.py::TestSingApproveDivebarFilename -v`
Expected: Both tests FAIL with `assert 'divebar-divebar-file-id-xyz.mp4' == 'DB - Queen - Bohemian Rhapsody.mp4'` (the hardcoded title is what runs today).

- [ ] **Step 4: Update the approval logic**

In `kj-controller/routes.py`, find lines 3134-3143 (inside `approve_sing_request`):

```python
        if source_type == "divebar":
            try:
                download_url = divebar.get_download_url(source_ref, app.kj_config)
            except Exception as exc:
                raise RuntimeError(f"Divebar URL failed: {exc}") from exc
            if not download_url:
                raise RuntimeError("Failed to get download URL from Divebar")
            title = f"divebar-{source_ref}.mp4"
            queue_src = "divebar"
            queue_url = download_url
```

Replace with:

```python
        if source_type == "divebar":
            try:
                download_url = divebar.get_download_url(source_ref, app.kj_config)
            except Exception as exc:
                raise RuntimeError(f"Divebar URL failed: {exc}") from exc
            if not download_url:
                raise RuntimeError("Failed to get download URL from Divebar")
            # source_meta carries brand_code when this came via kj_pick;
            # direct singer-divebar picks won't have it — fall back to "DB".
            meta_raw = req.get("source_meta")
            if isinstance(meta_raw, str):
                try:
                    meta = json.loads(meta_raw)
                except (TypeError, ValueError):
                    meta = {}
            elif isinstance(meta_raw, dict):
                meta = meta_raw
            else:
                meta = {}
            brand_code = meta.get("brand_code") or ""
            title = build_divebar_filename(
                brand_code,
                req.get("song_artist"),
                req.get("song_title"),
            ) or f"divebar-{source_ref}.mp4"
            queue_src = "divebar"
            queue_url = download_url
```

(`json` is already imported at the top of `routes.py` — verify with `grep '^import json\|^from json' kj-controller/routes.py`. If not, add `import json` at the top.)

- [ ] **Step 5: Run tests to verify pass**

Run: `cd kj-controller && pytest tests/integration/test_sing_admin_routes.py::TestSingApproveDivebarFilename -v`
Expected: Both tests pass.

Then run the full sing test suite to make sure nothing else regressed:

Run: `cd kj-controller && pytest tests/integration/test_sing_*.py -v`
Expected: All previously-passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_sing_admin_routes.py
git commit -m "feat(sing): divebar approval builds filename from request metadata

approve_sing_request no longer hardcodes \`divebar-{file_id}.mp4\` for
divebar source. Uses req.song_artist / req.song_title (and brand_code
from source_meta when available via kj_pick) to produce filenames like
\`WTF - Queen - Bohemian Rhapsody.mp4\`.

This is the actual code path that produced the observed ID-named files
in the user's downloads folder."
```

---

### Task 5: JS — Divebar panel button passes structured fields

**Files:**
- Modify: `kj-controller/static/app.js:2931-2952`

- [ ] **Step 1: Update the button handler**

In `kj-controller/static/app.js`, find lines 2931-2936:

```javascript
                dlBtn.onclick = (e) => {
                    e.stopPropagation();
                    downloadDivebarTrack(track.file_id, track.drive_path || track.brand);
                    dlBtn.disabled = true;
                    dlBtn.textContent = 'Queued';
                };
```

Replace with:

```javascript
                dlBtn.onclick = (e) => {
                    e.stopPropagation();
                    downloadDivebarTrack({
                        file_id: track.file_id,
                        artist: song.artist,
                        title: song.title,
                        brand_code: track.brand_code,
                    });
                    dlBtn.disabled = true;
                    dlBtn.textContent = 'Queued';
                };
```

- [ ] **Step 2: Update the helper function**

In `kj-controller/static/app.js`, find lines 2949-2952:

```javascript
function downloadDivebarTrack(fileId, filename) {
    log(`Queuing Divebar download: ${filename}`);
    apiCall('/divebar/download', { file_id: fileId, filename: filename });
}
```

Replace with:

```javascript
function downloadDivebarTrack(payload) {
    const label = [payload.artist, payload.title].filter(Boolean).join(' - ') || payload.file_id;
    log(`Queuing Divebar download: ${label}`);
    apiCall('/divebar/download', payload);
}
```

- [ ] **Step 3: Lint / quick syntax sanity**

Run: `cd kj-controller && node --check static/app.js`
Expected: No output (success). If the project uses the pre-commit hook `.githooks/pre-commit`, it will run this automatically on commit — but check now to fail fast.

- [ ] **Step 4: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat(divebar): panel download sends structured {artist,title,brand_code}

Stops relying on \`track.drive_path || track.brand\` as a filename
guess. Sends the artist/title/brand_code that the search result
already carries, so the server can build a consistent filename via
build_divebar_filename."
```

---

### Task 6: JS — Rotation-search Divebar result passes structured fields

**Files:**
- Modify: `kj-controller/static/app.js:4892-4900` and `:4979-4982`

- [ ] **Step 1: Replace the pre-built filename with structured fields**

In `kj-controller/static/app.js`, find lines 4892-4900 (inside the `sorted.forEach(track => { ... })` loop):

```javascript
            } else if (track.divebar) {
                result.type = 'divebar';
                result.file_id = track.divebar.file_id;
                result.filename = (track.brand_code || 'DB') + ' - ' + song.artist + ' - ' + song.title + '.mp4';
            } else if (track.youtube_url) {
                result.type = 'youtube';
                result.youtube_url = track.youtube_url;
                result.filename = (track.brand_code || 'YT') + ' - ' + song.artist + ' - ' + song.title + '.mp4';
            } else {
                return;
            }
```

Replace with:

```javascript
            } else if (track.divebar) {
                result.type = 'divebar';
                result.file_id = track.divebar.file_id;
                result.artist = song.artist;
                result.title = song.title;
                result.brand_code = track.brand_code;
            } else if (track.youtube_url) {
                result.type = 'youtube';
                result.youtube_url = track.youtube_url;
                // YouTube keeps the pre-built filename path for now —
                // the backend's youtube branch still uses `filename`.
                result.filename = (track.brand_code || 'YT') + ' - ' + song.artist + ' - ' + song.title + '.mp4';
            } else {
                return;
            }
```

- [ ] **Step 2: Update the `buildCall` divebar body**

In `kj-controller/static/app.js`, find lines 4979-4982:

```javascript
        if (result.type === 'divebar') {
            return { endpoint: '/rotation/download-and-link', body: {
                ...base, source: 'divebar', file_id: result.file_id, filename: result.filename,
            }};
        }
```

Replace with:

```javascript
        if (result.type === 'divebar') {
            return { endpoint: '/rotation/download-and-link', body: {
                ...base, source: 'divebar', file_id: result.file_id,
                artist: result.artist, title: result.title, brand_code: result.brand_code,
            }};
        }
```

- [ ] **Step 3: Syntax check**

Run: `cd kj-controller && node --check static/app.js`
Expected: No output.

- [ ] **Step 4: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat(rotation): divebar search result sends structured metadata

Drops the client-built \`filename\` for divebar results; sends
{file_id, artist, title, brand_code} so the server is the single
source of truth for filename construction. YouTube path unchanged."
```

---

### Task 7: Run full test suite

- [ ] **Step 1: Full pytest run**

Run: `cd kj-controller && pytest`
Expected: All tests pass. If any pre-existing test fails because it was reading `filename` on a divebar queue item or hitting the old hardcoded title, update the assertion to match the new behaviour (it should already match the spec's expected output).

- [ ] **Step 2: Coverage check (optional but recommended)**

Run: `cd kj-controller && pytest --cov=. --cov-report=term-missing -q`
Expected: Coverage stable or higher. The new function is exercised by unit + integration tests.

- [ ] **Step 3: Final review commit (only if any test fixes were needed)**

If Step 1 turned up legacy assertion drift to fix, commit those updates:

```bash
git add kj-controller/tests/
git commit -m "test: align assertions with new divebar filename format"
```

If no extra commits were needed, skip this step.

---

### Task 8: One-off rename of existing files on nomadpctunnel

Performed **interactively in the implementing session**, not as code in the repo. The Python below is run from the agent's working directory via `subprocess`/`Bash`, shelling to `ssh nomadpctunnel`.

- [ ] **Step 1: Get user confirmation before mutating production**

This step mutates files on the live device. Even though the user has already authorised renaming in the spec, re-confirm at this point with a one-liner before any rename runs.

- [ ] **Step 2: Inventory affected files**

Run:

```bash
ssh nomadpctunnel 'ls -1 ~/kjdata/videos/divebar__divebar-*.mp4 2>/dev/null'
```

Save the output. If empty: skip Tasks 8.3-8.6 and proceed to ship.

- [ ] **Step 3: Recover metadata via rotation DB**

For each path in the inventory, run:

```bash
ssh nomadpctunnel 'sqlite3 -separator "|" ~/kjdata/rotation.db \
  "SELECT id, song_artist, file_path FROM rotation_entries WHERE file_path = '"'"'<path>'"'"';"'
```

Parse `song_artist` (format: `"Title - Artist"`) into `(title, artist)` by splitting on `" - "` once from the right. (`rsplit(" - ", 1)` in Python — title is the last part, artist is the first.) Brand code is unknown → pass `None` to the builder.

Build the new filename in Python locally using the builder logic from this plan (`<brand_code|"DB"> - <artist> - <title>.mp4`), then prefix with `divebar__` (since `media.download_from_url` adds that prefix on real downloads). So the final on-disk name is `divebar__DB - <Artist> - <Title>.mp4`.

- [ ] **Step 4: Show the user the proposed renames**

Print a table:

```
OLD                                                        →  NEW
divebar__divebar-1slF4D84xyFdmHprHmIX9Fvt5CLGdZ7ne.mp4    →  divebar__DB - Queen - Bohemian Rhapsody.mp4
...
```

Wait for user confirmation. Skip orphans (files with no rotation_entries match) — report them at the end.

- [ ] **Step 5: Pre-flight safety**

Run, in order:

```bash
ssh nomadpctunnel 'cp ~/kjdata/rotation.db ~/kjdata/rotation.db.bak-divebar-rename-20260522'
```

Check current playback via the status endpoint (or directly):

```bash
ssh nomadpctunnel 'curl -s http://localhost/status | python3 -c "import json,sys; print(json.load(sys.stdin).get(\"current_playing_path\") or \"\")"'
```

If any file in the inventory matches the currently-playing path, refuse to rename that one (skip + report).

- [ ] **Step 6: Rename + update DB, one file at a time**

For each safe file:

```bash
ssh nomadpctunnel 'mv "<OLD>" "<NEW>"'
ssh nomadpctunnel 'sqlite3 ~/kjdata/rotation.db "UPDATE rotation_entries SET file_path = '"'"'<NEW>'"'"' WHERE file_path = '"'"'<OLD>'"'"';"'
```

After all renames, hit `/status` once to trigger the kjbox `MediaIndex` rescan:

```bash
ssh nomadpctunnel 'curl -s http://localhost/status >/dev/null'
```

- [ ] **Step 7: Verify**

Run:

```bash
ssh nomadpctunnel 'ls -1 ~/kjdata/videos/divebar__divebar-*.mp4 2>/dev/null'
```

Expected: empty (or only the deliberately-skipped playing file, if any).

Report to the user:
- Renamed: N files
- Skipped (playing): 0 or 1
- Orphaned (no rotation match): N

No commit — this work isn't in the repo.

---

### Task 9: /shipit

After Tasks 1-7 are committed and Task 8 is complete (or determined unnecessary), the work is ready to ship.

- [ ] **Step 1: Invoke /shipit**

Run the `/shipit` skill. It chains test → test-review → docs-review → coderabbit → version bump → pr → merge → wait for deploy → verify prod.

The /shipit command will:
- Run the test suite (already done in Task 7, but it will re-run).
- Assess test coverage and quality.
- Run CodeRabbit locally and address findings (max 3 cycles per the global workflow).
- Create the PR with `@coderabbitai ignore` and a Test Plan.
- Wait for auto-merge and CI deploy.
- Verify prod health post-deploy.

If /shipit asks for confirmation before any production-affecting step (push, merge, restart), provide it explicitly.
