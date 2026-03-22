# Rotation Unified Search — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replace the fragmented add-singer + search + link workflow with an integrated search-as-you-type experience and preparation status badges on every rotation entry.

**Architecture:** The song field in the add-singer form becomes a search input. A new `/rotation/search` endpoint queries local catalog + Karaoke Nerds + Divebar in parallel. Results appear inline with badges (READY/DIVEBAR/YOUTUBE). Tapping a result adds the singer with the file linked or download queued. The download worker is extended to auto-link files to rotation entries on completion. Every rotation entry shows a prep badge.

**Tech Stack:** Python 3, Flask, SQLite, vanilla JS, existing catalog/KN/Divebar modules

**Spec:** `docs/superpowers/specs/2026-03-22-rotation-unified-search-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `kj-controller/rotation_store.py` | Add download_source, download_status, download_id, url_fallback columns |
| Modify | `kj-controller/rotation.py` | Extend add_entry to accept linking fields; add download-aware methods |
| Modify | `kj-controller/routes.py` | New `/rotation/search`, `/rotation/download-and-link` endpoints; extend `/rotation/add`, `/status`; extend download worker |
| Modify | `kj-controller/static/app.js` | Search-as-you-type dropdown, prep badges, one-click play, keyboard nav |
| Modify | `kj-controller/templates/index.html` | Search dropdown container below add-singer form |
| Modify | `kj-controller/static/style.css` | Dropdown styles, prep badge styles, expanded mode |
| Create | `kj-controller/tests/unit/test_rotation_search.py` | Unified search endpoint tests |
| Create | `kj-controller/tests/integration/test_download_link_routes.py` | Download-and-link route tests |
| Modify | `kj-controller/tests/integration/test_rotation_routes.py` | Extended add_entry tests, prep badge in responses |
| Modify | `kj-controller/tests/conftest.py` | Fixtures for unified search mocking |

---

## Task 1: Schema — Add Download/Prep Columns to RotationStore

**Files:**
- Modify: `kj-controller/rotation_store.py`
- Modify: `kj-controller/tests/unit/test_rotation_store.py`

- [x] **Step 1: Write schema migration tests**

```python
# Append to test_rotation_store.py

class TestDownloadColumns:
    def test_add_entry_has_download_fields(self, store):
        entry = store.add_entry("Alice", "Song A")
        assert entry["download_source"] is None
        assert entry["download_status"] is None
        assert entry["download_id"] is None
        assert entry["url_fallback"] is None

    def test_add_entry_with_file_path(self, store):
        """add_entry accepts optional file_path and duration for single-action add+link."""
        entry = store.add_entry("Alice", "Song A", file_path="/media/song.mp4", duration=213)
        assert entry["file_path"] == "/media/song.mp4"
        assert entry["duration"] == 213

    def test_set_download_status(self, store):
        entry = store.add_entry("Alice", "Song A")
        updated = store.set_download_status(entry["id"], source="divebar", status="queued", download_id="uuid-123")
        assert updated["download_source"] == "divebar"
        assert updated["download_status"] == "queued"
        assert updated["download_id"] == "uuid-123"

    def test_set_url_fallback(self, store):
        entry = store.add_entry("Alice", "Song A")
        updated = store.set_url_fallback(entry["id"], "https://youtube.com/watch?v=abc")
        assert updated["url_fallback"] == "https://youtube.com/watch?v=abc"

    def test_get_entries_by_download_id(self, store):
        e1 = store.add_entry("Alice", "Song A")
        store.set_download_status(e1["id"], source="divebar", status="queued", download_id="uuid-123")
        found = store.get_entry_by_download_id("uuid-123")
        assert found is not None
        assert found["singer"] == "Alice"

    def test_get_entry_by_download_id_missing(self, store):
        assert store.get_entry_by_download_id("nonexistent") is None
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py::TestDownloadColumns -v`
Expected: FAIL

- [x] **Step 3: Add columns to schema and implement methods**

In `rotation_store.py`, add to the `_SCHEMA` string after the existing `rotation_entries` columns (before the closing `);`):

```sql
    download_source TEXT DEFAULT NULL,
    download_status TEXT DEFAULT NULL,
    download_id TEXT DEFAULT NULL,
    url_fallback TEXT DEFAULT NULL
```

Extend `add_entry` to accept optional `file_path` and `duration`:

```python
def add_entry(self, singer, song_artist='', notes='', file_path=None, duration=None):
    conn = self._get_conn()
    cur = conn.execute(
        "INSERT INTO rotation_entries (singer, song_artist, notes, position, file_path, duration) "
        "VALUES (?, ?, ?, (SELECT COALESCE(MAX(position), 0) + 1 FROM rotation_entries), ?, ?)",
        (singer, song_artist, notes, file_path, duration),
    )
    conn.commit()
    return self._row_to_dict(
        conn.execute("SELECT * FROM rotation_entries WHERE id = ?", (cur.lastrowid,)).fetchone()
    )
```

Add new methods:

```python
def set_download_status(self, entry_id, source, status, download_id=None):
    """Set download tracking fields on a rotation entry."""
    conn = self._get_conn()
    existing = self.get_entry(entry_id)
    if existing is None:
        raise ValueError(f"Entry {entry_id} not found")
    conn.execute(
        """UPDATE rotation_entries
           SET download_source = ?, download_status = ?, download_id = ?,
               updated_at = datetime('now', 'localtime')
           WHERE id = ?""",
        (source, status, download_id, entry_id),
    )
    conn.commit()
    return self.get_entry(entry_id)

def set_url_fallback(self, entry_id, url):
    """Set a URL fallback for browser mode playback."""
    conn = self._get_conn()
    existing = self.get_entry(entry_id)
    if existing is None:
        raise ValueError(f"Entry {entry_id} not found")
    conn.execute(
        """UPDATE rotation_entries SET url_fallback = ?, updated_at = datetime('now', 'localtime')
           WHERE id = ?""",
        (url, entry_id),
    )
    conn.commit()
    return self.get_entry(entry_id)

def get_entry_by_download_id(self, download_id):
    """Find a rotation entry by its download queue correlation ID."""
    conn = self._get_conn()
    row = conn.execute(
        "SELECT * FROM rotation_entries WHERE download_id = ?", (download_id,)
    ).fetchone()
    return self._row_to_dict(row)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_store.py -v`
Expected: All pass

- [x] **Step 5: Commit**

```bash
git add kj-controller/rotation_store.py kj-controller/tests/unit/test_rotation_store.py
git commit -m "feat(rotation): add download/prep tracking columns to RotationStore"
```

---

## Task 2: Extend RotationManager Coordinator

**Files:**
- Modify: `kj-controller/rotation.py`
- Modify: `kj-controller/tests/unit/test_rotation.py`

- [x] **Step 1: Write coordinator tests**

```python
# Append to test_rotation.py

class TestCoordinatorDownload:
    def test_add_entry_with_file_path(self, manager):
        entry = manager.add_entry("Alice", "Song A", file_path="/media/song.mp4")
        assert entry["file_path"] == "/media/song.mp4"

    def test_set_download_status(self, manager):
        entry = manager.add_entry("Alice", "Song A")
        updated = manager.set_download_status(entry["id"], "divebar", "queued", "uuid-123")
        assert updated["download_source"] == "divebar"

    def test_set_url_fallback(self, manager):
        entry = manager.add_entry("Alice", "Song A")
        updated = manager.set_url_fallback(entry["id"], "https://youtube.com/watch?v=abc")
        assert updated["url_fallback"] == "https://youtube.com/watch?v=abc"

    def test_complete_download(self, manager):
        """complete_download links file and clears download status."""
        entry = manager.add_entry("Alice", "Song A")
        manager.set_download_status(entry["id"], "divebar", "downloading", "uuid-123")
        updated = manager.complete_download("uuid-123", "/media/song.mp4")
        assert updated["file_path"] == "/media/song.mp4"
        assert updated["download_status"] == "complete"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation.py::TestCoordinatorDownload -v`

- [x] **Step 3: Extend RotationManager**

In `rotation.py`, extend `add_entry` to pass through `file_path` and `duration`:

```python
def add_entry(self, singer, song_artist='', notes='', file_path=None, duration=None):
    if file_path and duration is None and self.media and hasattr(self.media, 'index'):
        media_entry = self.media.index.get(file_path)
        if media_entry:
            duration = media_entry.get("duration")
    entry = self.store.add_entry(singer, song_artist, notes, file_path, duration)
    self._after_mutation()
    return entry
```

Add new coordinator methods:

```python
def set_download_status(self, entry_id, source, status, download_id=None):
    entry = self.store.set_download_status(entry_id, source, status, download_id)
    self._after_mutation()
    return entry

def set_url_fallback(self, entry_id, url):
    entry = self.store.set_url_fallback(entry_id, url)
    self._after_mutation()
    return entry

def complete_download(self, download_id, file_path):
    """Called by download worker when a rotation-linked download completes."""
    entry = self.store.get_entry_by_download_id(download_id)
    if entry is None:
        return None
    self.store.link_file(entry["id"], file_path, self._lookup_duration(file_path))
    self.store.set_download_status(entry["id"], entry["download_source"], "complete")
    self._after_mutation()
    return self.store.get_entry(entry["id"])

def _lookup_duration(self, file_path):
    if self.media and hasattr(self.media, 'index'):
        media_entry = self.media.index.get(file_path)
        if media_entry:
            return media_entry.get("duration")
    return None
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation.py -v`

- [x] **Step 5: Commit**

```bash
git add kj-controller/rotation.py kj-controller/tests/unit/test_rotation.py
git commit -m "feat(rotation): add download tracking to RotationManager coordinator"
```

---

## Task 3: Unified Search Endpoint

**Files:**
- Modify: `kj-controller/routes.py`
- Create: `kj-controller/tests/unit/test_rotation_search.py`

- [x] **Step 1: Write search endpoint tests**

```python
# kj-controller/tests/unit/test_rotation_search.py
"""Tests for the unified rotation search endpoint."""

import json
from unittest.mock import MagicMock, patch
import pytest
from app import create_app


@pytest.fixture
def search_app(mock_config):
    mock_config["rotation_db_path"] = ":memory:"
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    yield app
    app.catalog.close()


@pytest.fixture
def search_client(search_app):
    with search_app.test_client() as client:
        yield client


class TestUnifiedSearch:
    def test_returns_local_results(self, search_client, search_app):
        """Local catalog results returned under 'local' key."""
        with patch.object(search_app.catalog, 'search', return_value=[
            {"path": "/media/song.zip", "artist": "Queen", "title": "Bohemian Rhapsody",
             "format": "cdg+mp3", "disc_id": "ASK-002204"}
        ]):
            resp = search_client.get('/rotation/search?q=bohemian')
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data["local"]) == 1
            assert data["local"][0]["artist"] == "Queen"

    def test_returns_kn_results(self, search_client, search_app):
        """Karaoke Nerds results returned under 'karaoke_nerds' key."""
        with patch.object(search_app.catalog, 'search', return_value=[]), \
             patch('routes.kn_search', return_value=[
                 {"title": "Bohemian Rhapsody", "artist": "Queen", "tracks": [
                     {"brand_name": "KFN", "brand_code": "KFN-1234",
                      "youtube_url": "https://youtube.com/watch?v=abc", "is_community": True}
                 ]}
             ]):
            resp = search_client.get('/rotation/search?q=bohemian')
            data = resp.get_json()
            assert len(data["karaoke_nerds"]) == 1

    def test_empty_query_returns_400(self, search_client):
        resp = search_client.get('/rotation/search?q=')
        assert resp.status_code == 400

    def test_short_query_returns_400(self, search_client):
        resp = search_client.get('/rotation/search?q=bo')
        assert resp.status_code == 400

    def test_kn_timeout_returns_local_only(self, search_client, search_app):
        """If KN search times out, returns local results with timeout flag."""
        with patch.object(search_app.catalog, 'search', return_value=[
            {"path": "/media/song.zip", "artist": "Queen", "title": "Bohemian Rhapsody",
             "format": "cdg+mp3", "disc_id": "ASK-002204"}
        ]), patch('routes.kn_search', side_effect=Exception("timeout")):
            resp = search_client.get('/rotation/search?q=bohemian')
            data = resp.get_json()
            assert len(data["local"]) == 1
            assert data.get("karaoke_nerds_timeout") is True
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_search.py -v`

- [x] **Step 3: Implement the unified search endpoint**

Add to the rotation section of `routes.py`:

```python
@routes_bp.route('/rotation/search', methods=['GET'])
def rotation_search():
    """Unified search: local catalog + Karaoke Nerds + Divebar cross-reference."""
    query = request.args.get('q', '').strip()
    if len(query) < 3:
        return jsonify({"error": "Query must be at least 3 characters"}), 400

    # Local catalog search (fast, <10ms)
    local_results = []
    if current_app.catalog.is_available():
        local_results = current_app.catalog.search(query, limit=10)

    # Add duration from media index where available
    for result in local_results:
        media_entry = current_app.media.index.get(result.get("path"))
        if media_entry:
            result["duration"] = media_entry.get("duration")

    # Karaoke Nerds search (slower, 1-3s)
    kn_results = []
    kn_timeout = False
    try:
        from karaoke_nerds import search as kn_search
        kn_results = kn_search(query, current_app.kj_config)
    except Exception:
        kn_timeout = True

    # Divebar cross-reference for KN results
    if kn_results and not kn_timeout:
        try:
            from divebar import lookup_kn_ids
            # Extract KN IDs from tracks
            all_kn_ids = []
            for song in kn_results:
                for track in song.get("tracks", []):
                    kn_id = track.get("brand_code")
                    if kn_id:
                        all_kn_ids.append(kn_id)
            if all_kn_ids:
                divebar_matches = lookup_kn_ids(all_kn_ids, current_app.kj_config)
                # Merge Divebar availability into KN tracks
                for song in kn_results:
                    for track in song.get("tracks", []):
                        kn_id = track.get("brand_code")
                        if kn_id and kn_id in divebar_matches:
                            db_match = divebar_matches[kn_id]
                            if isinstance(db_match, list) and db_match:
                                track["divebar"] = db_match[0]
                            elif isinstance(db_match, dict):
                                track["divebar"] = db_match
        except Exception:
            pass  # Divebar cross-ref is best-effort

        # Check local library for KN tracks
        for song in kn_results:
            for track in song.get("tracks", []):
                # Simple title+artist match against local catalog
                track["in_library"] = any(
                    r.get("artist", "").lower() == song.get("artist", "").lower()
                    and r.get("title", "").lower() == song.get("title", "").lower()
                    for r in local_results
                )

    response = {"local": local_results, "karaoke_nerds": kn_results}
    if kn_timeout:
        response["karaoke_nerds_timeout"] = True
    return jsonify(response)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && python -m pytest tests/unit/test_rotation_search.py -v`

- [x] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/unit/test_rotation_search.py
git commit -m "feat(rotation): add unified search endpoint /rotation/search"
```

---

## Task 4: Download-and-Link Endpoint + Worker Extension

**Files:**
- Modify: `kj-controller/routes.py`
- Create: `kj-controller/tests/integration/test_download_link_routes.py`

- [x] **Step 1: Write download-and-link tests**

```python
# kj-controller/tests/integration/test_download_link_routes.py
"""Tests for the download-and-link rotation endpoint."""

import json
from unittest.mock import MagicMock, patch
import pytest
from app import create_app


@pytest.fixture
def dl_app(mock_config):
    mock_config["rotation_db_path"] = ":memory:"
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    yield app
    app.catalog.close()


@pytest.fixture
def dl_client(dl_app):
    with dl_app.test_client() as client:
        yield client


class TestDownloadAndLink:
    def test_divebar_download(self, dl_client, dl_app):
        """Divebar download sets download_source and queues download."""
        # First add a singer
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice", "song_artist": "Bohemian Rhapsody"}),
            content_type='application/json')
        entry_id = resp.get_json()["entries"][0]["id"]

        with patch('routes.divebar') as mock_divebar:
            mock_divebar.get_download_url.return_value = "https://storage.googleapis.com/test"
            resp = dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "id": entry_id,
                    "source": "divebar",
                    "file_id": "abc123",
                    "filename": "KFN-1234 - Queen - Bohemian Rhapsody.mp4"
                }),
                content_type='application/json')
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["entry"]["download_source"] == "divebar"
            assert data["entry"]["download_status"] == "queued"

    def test_youtube_download(self, dl_client, dl_app):
        """YouTube download sets download_source and queues download."""
        resp = dl_client.post('/rotation/add',
            data=json.dumps({"singer": "Bob", "song_artist": "Song B"}),
            content_type='application/json')
        entry_id = resp.get_json()["entries"][0]["id"]

        resp = dl_client.post('/rotation/download-and-link',
            data=json.dumps({
                "id": entry_id,
                "source": "youtube",
                "youtube_url": "https://youtube.com/watch?v=abc",
                "filename": "KV-5678 - Queen - Bohemian Rhapsody.mp4"
            }),
            content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["entry"]["download_source"] == "youtube"

    def test_missing_id_returns_400(self, dl_client):
        resp = dl_client.post('/rotation/download-and-link',
            data=json.dumps({"source": "divebar", "file_id": "abc"}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_missing_source_returns_400(self, dl_client):
        resp = dl_client.post('/rotation/download-and-link',
            data=json.dumps({"id": 1}),
            content_type='application/json')
        assert resp.status_code == 400

    def test_creates_entry_if_id_omitted(self, dl_client, dl_app):
        """If id is omitted but singer provided, creates entry first."""
        with patch('routes.divebar') as mock_divebar:
            mock_divebar.get_download_url.return_value = "https://storage.googleapis.com/test"
            resp = dl_client.post('/rotation/download-and-link',
                data=json.dumps({
                    "source": "divebar",
                    "file_id": "abc123",
                    "filename": "KFN-1234 - Queen - Bohemian Rhapsody.mp4",
                    "singer": "Alice",
                    "song_artist": "Bohemian Rhapsody - Queen"
                }),
                content_type='application/json')
            assert resp.status_code == 200
            assert resp.get_json()["entry"]["singer"] == "Alice"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/integration/test_download_link_routes.py -v`

- [x] **Step 3: Implement download-and-link endpoint**

Add to `routes.py`:

```python
@routes_bp.route('/rotation/download-and-link', methods=['POST'])
def download_and_link_rotation():
    """Queue a download and link it to a rotation entry."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503

    data = request.get_json(force=True)
    source = data.get('source', '').strip()
    if not source:
        return jsonify({"error": "source is required"}), 400

    # Get or create rotation entry
    entry_id = data.get('id')
    if entry_id is None:
        singer = data.get('singer', '').strip()
        song_artist = data.get('song_artist', '').strip()
        if not singer:
            return jsonify({"error": "id or singer is required"}), 400
        entry = rotation.add_entry(singer, song_artist)
        entry_id = entry["id"]

    try:
        entry_id = int(entry_id)
    except (TypeError, ValueError):
        return jsonify({"error": "id must be an integer"}), 400

    import uuid
    download_id = str(uuid.uuid4())

    try:
        if source == "divebar":
            file_id = data.get('file_id', '').strip()
            filename = data.get('filename', '').strip()
            if not file_id:
                return jsonify({"error": "file_id is required for divebar"}), 400
            from divebar import get_download_url
            download_url = get_download_url(file_id, current_app.kj_config)
            # Queue the download
            queue_item = {
                'id': download_id,
                'url': download_url,
                'filename': filename or f"divebar-{file_id}.mp4",
                'source': 'divebar',
                'status': 'queued',
                'rotation_entry_id': entry_id,
            }
        elif source == "youtube":
            youtube_url = data.get('youtube_url', '').strip()
            filename = data.get('filename', '').strip()
            if not youtube_url:
                return jsonify({"error": "youtube_url is required for youtube"}), 400
            queue_item = {
                'id': download_id,
                'url': youtube_url,
                'filename': filename,
                'source': 'youtube',
                'status': 'queued',
                'rotation_entry_id': entry_id,
            }
        else:
            return jsonify({"error": f"Unknown source: {source}"}), 400

        # Add to download queue
        with current_app._download_lock:
            current_app.download_queue['items'].append(queue_item)

        # Update rotation entry with download tracking
        rotation.set_download_status(entry_id, source, "queued", download_id)

        entry = rotation.store.get_entry(entry_id)
        entries = rotation.get_rotation()
        return jsonify({"success": True, "entry": entry, "entries": entries})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

- [x] **Step 4: Extend the download worker to auto-link on completion**

In `routes.py`, find the `_download_worker` function (~line 115). After a download completes successfully (where `file_path` is set), add a check for `rotation_entry_id`:

```python
# After successful download, check if this was a rotation-linked download
rotation_entry_id = item.get('rotation_entry_id')
if rotation_entry_id and hasattr(app, 'rotation') and app.rotation:
    try:
        download_id = item.get('id')
        if download_id:
            app.rotation.complete_download(download_id, file_path)
    except Exception:
        pass  # Best-effort; entry can be linked manually
```

- [x] **Step 5: Extend /status to include rotation_downloads**

In the `get_status()` route (~line 449), add a `rotation_downloads` field to the response:

```python
# Build rotation download status map
rotation_downloads = {}
if hasattr(current_app, 'rotation') and current_app.rotation:
    with current_app._download_lock:
        for item in current_app.download_queue['items']:
            rot_id = item.get('rotation_entry_id')
            if rot_id:
                rotation_downloads[str(rot_id)] = {
                    "status": item.get('status', 'unknown'),
                    "progress": item.get('progress', 0),
                    "file_path": item.get('file_path'),
                }
```

Add `"rotation_downloads": rotation_downloads` to the status response dict.

- [x] **Step 6: Run tests**

Run: `cd kj-controller && python -m pytest tests/integration/test_download_link_routes.py tests/unit/test_rotation_search.py -v`

- [x] **Step 7: Run full test suite**

Run: `cd kj-controller && python -m pytest tests/ -q`

- [x] **Step 8: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_download_link_routes.py
git commit -m "feat(rotation): add download-and-link endpoint with auto-link on completion"
```

---

## Task 5: Extend /rotation/add to Accept Linking Fields

**Files:**
- Modify: `kj-controller/routes.py`
- Modify: `kj-controller/tests/integration/test_rotation_routes.py`

- [x] **Step 1: Write tests for extended add**

```python
# Append to test_rotation_routes.py, in TestAddRotationEntry class

    def test_add_with_file_path(self, rotation_client):
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"singer": "Alice", "song_artist": "Song A", "file_path": "/media/song.mp4"}),
            content_type='application/json')
        assert resp.status_code == 200
        entry = next(e for e in resp.get_json()["entries"] if e["singer"] == "Alice")
        assert entry["file_path"] == "/media/song.mp4"

    def test_add_with_url_fallback(self, rotation_client):
        resp = rotation_client.post('/rotation/add',
            data=json.dumps({"singer": "Bob", "song_artist": "Song B", "url_fallback": "https://youtube.com/watch?v=abc"}),
            content_type='application/json')
        assert resp.status_code == 200
        entry = next(e for e in resp.get_json()["entries"] if e["singer"] == "Bob")
        assert entry["url_fallback"] == "https://youtube.com/watch?v=abc"
```

- [x] **Step 2: Update the /rotation/add route**

In `routes.py`, update `add_rotation_entry()` (~line 1740) to accept optional `file_path` and `url_fallback`:

```python
file_path = data.get('file_path', '').strip() or None
url_fallback = data.get('url_fallback', '').strip() or None

rotation.add_entry(singer, song_artist, notes, file_path=file_path)
if url_fallback:
    # Get the just-added entry (last one)
    entries = rotation.get_rotation()
    new_entry = entries[-1] if entries else None
    if new_entry:
        rotation.set_url_fallback(new_entry["id"], url_fallback)
```

- [x] **Step 3: Run tests**

Run: `cd kj-controller && python -m pytest tests/integration/test_rotation_routes.py -v`

- [x] **Step 4: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_rotation_routes.py
git commit -m "feat(rotation): extend /rotation/add to accept file_path and url_fallback"
```

---

## Task 6: Frontend — Search-As-You-Type Dropdown

**Files:**
- Modify: `kj-controller/static/app.js`
- Modify: `kj-controller/templates/index.html`
- Modify: `kj-controller/static/style.css`

- [x] **Step 1: Add dropdown container to HTML**

In `templates/index.html`, after the `rotation-add-form` div (~line 67), add:

```html
<div id="rotation-search-dropdown" class="rotation-search-dropdown hidden"></div>
```

- [x] **Step 2: Add CSS for search dropdown**

Append to `static/style.css`:

```css
/* Rotation search dropdown */
.rotation-search-dropdown {
    background: #1a1f2e;
    border: 1px solid #333;
    border-radius: 0 0 6px 6px;
    margin-top: -1px;
    max-height: 300px;
    overflow-y: auto;
    z-index: 100;
}
.rotation-search-dropdown.hidden { display: none; }
.rotation-search-dropdown .search-header {
    padding: 6px 10px;
    font-size: 10px;
    color: #8892a4;
    border-bottom: 1px solid #333;
    display: flex;
    justify-content: space-between;
}
.rotation-search-result {
    padding: 7px 10px;
    border-bottom: 1px solid #222;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 8px;
    transition: background 0.1s;
}
.rotation-search-result:hover,
.rotation-search-result.selected { background: #252b3b; }
.rotation-search-result .search-badge {
    font-size: 9px;
    padding: 2px 6px;
    border-radius: 3px;
    white-space: nowrap;
    min-width: 52px;
    text-align: center;
    color: white;
}
.search-badge-ready { background: #2d8a4e; }
.search-badge-divebar { background: #2d8a4e; }
.search-badge-youtube { background: #d4720a; }
.search-badge-url { background: #555; }
.search-badge-make { background: #7c3aed; }
.rotation-search-result .search-info { flex: 1; }
.rotation-search-result .search-title { color: #e0e6f0; font-size: 12px; }
.rotation-search-result .search-meta { color: #8892a4; font-size: 10px; }
.rotation-search-more {
    padding: 8px;
    text-align: center;
    cursor: pointer;
    color: #5b9bd5;
    font-size: 11px;
}
.rotation-search-more:hover { text-decoration: underline; }
.rotation-search-hint {
    padding: 6px 10px;
    font-size: 10px;
    color: #666;
    border-top: 1px solid #222;
}

/* Expanded mode section headers */
.rotation-search-section {
    padding: 6px 10px;
    font-size: 10px;
    color: #8892a4;
    border-bottom: 1px solid #333;
    position: sticky;
    top: 0;
    background: #1a1f2e;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

/* Prep badges on rotation entries */
.rotation-prep-badge {
    font-size: 9px;
    padding: 2px 6px;
    border-radius: 3px;
    white-space: nowrap;
}
.prep-ready { background: #2d8a4e33; color: #2d8a4e; }
.prep-downloading-green { background: #2d8a4e33; color: #2d8a4e; }
.prep-downloading-orange { background: #d4720a33; color: #d4720a; }
.prep-url { background: #55555533; color: #888; }
.prep-unlinked { background: #55555533; color: #888; }
.prep-failed { background: #ef444433; color: #ef4444; }
.prep-making { background: #7c3aed33; color: #7c3aed; }
.prep-review { background: #f59e0b33; color: #f59e0b; }
.prep-rendering { background: #7c3aed33; color: #7c3aed; }
```

- [x] **Step 3: Implement search-as-you-type JS**

In `static/app.js`, add the search dropdown logic in the rotation section:

```javascript
// --- Rotation Search-As-You-Type ---

let searchDebounceTimer = null;
let searchSelectedIdx = -1;
let searchResults = [];
let searchExpanded = false;

function initRotationSearch() {
    const songInput = document.getElementById('rotation-song');
    if (!songInput) return;

    songInput.addEventListener('input', () => {
        clearTimeout(searchDebounceTimer);
        const query = songInput.value.trim();
        if (query.length < 3) {
            hideSearchDropdown();
            return;
        }
        searchDebounceTimer = setTimeout(() => rotationSearch(query), 300);
    });

    songInput.addEventListener('keydown', (e) => {
        const dropdown = document.getElementById('rotation-search-dropdown');
        if (dropdown.classList.contains('hidden')) return;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            searchSelectedIdx = Math.min(searchSelectedIdx + 1, searchResults.length - 1);
            highlightSearchResult();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            searchSelectedIdx = Math.max(searchSelectedIdx - 1, -1);
            highlightSearchResult();
        } else if (e.key === 'Enter' && searchSelectedIdx >= 0) {
            e.preventDefault();
            selectSearchResult(searchResults[searchSelectedIdx]);
        } else if (e.key === 'Escape') {
            hideSearchDropdown();
        } else if (e.key === 'Tab') {
            hideSearchDropdown();
            // Allow default Tab behavior (add without linking)
        }
    });
}

async function rotationSearch(query) {
    try {
        const resp = await fetch('/rotation/search?q=' + encodeURIComponent(query));
        if (!resp.ok) return;
        const data = await resp.json();
        renderSearchDropdown(data);
    } catch (e) {
        hideSearchDropdown();
    }
}

function renderSearchDropdown(data) {
    const dropdown = document.getElementById('rotation-search-dropdown');
    searchResults = [];
    searchSelectedIdx = -1;

    // Build flat ranked result list
    // Tier 1: Local library (READY)
    for (const r of (data.local || [])) {
        searchResults.push({
            type: 'local', badge: 'READY', badgeClass: 'search-badge-ready',
            title: `${r.artist || ''} - ${r.title || r.filename || ''}`.replace(/^- /, ''),
            meta: `${r.disc_id || ''} · ${r.format || ''} · ${r.duration ? formatDuration(r.duration) : ''}`.replace(/^ · /, ''),
            path: r.path, duration: r.duration,
        });
    }

    // Tier 2: KN tracks with Divebar (DIVEBAR)
    // Tier 3: KN tracks YouTube only (YOUTUBE)
    for (const song of (data.karaoke_nerds || [])) {
        for (const track of (song.tracks || [])) {
            if (track.in_library) continue; // Skip if already in local results
            if (track.divebar) {
                searchResults.push({
                    type: 'divebar', badge: 'DIVEBAR', badgeClass: 'search-badge-divebar',
                    title: `${song.artist} - ${song.title}`,
                    meta: `${track.brand_name || track.brand_code || ''} · ${track.divebar.format || 'mp4'} · Divebar mirror`,
                    file_id: track.divebar.file_id,
                    filename: `${track.brand_code || 'DB'} - ${song.artist} - ${song.title}.mp4`,
                    song_artist: `${song.title} - ${song.artist}`,
                });
            } else if (track.youtube_url) {
                searchResults.push({
                    type: 'youtube', badge: 'YOUTUBE', badgeClass: 'search-badge-youtube',
                    title: `${song.artist} - ${song.title}`,
                    meta: `${track.brand_name || track.brand_code || ''} · ${track.is_community ? 'community' : ''} · YouTube`,
                    youtube_url: track.youtube_url,
                    filename: `${track.brand_code || 'YT'} - ${song.artist} - ${song.title}.mp4`,
                    song_artist: `${song.title} - ${song.artist}`,
                });
            }
        }
    }

    // Render
    const maxInline = searchExpanded ? 999 : 4;
    let html = '';

    if (searchResults.length === 0) {
        html = '<div class="search-header">No results found</div>';
    } else {
        html = `<div class="search-header"><span>🔍 ${searchResults.length} result${searchResults.length > 1 ? 's' : ''}</span></div>`;
    }

    searchResults.slice(0, maxInline).forEach((r, i) => {
        html += `<div class="rotation-search-result${i === searchSelectedIdx ? ' selected' : ''}" data-idx="${i}" onclick="selectSearchResult(searchResults[${i}])">
            <span class="search-badge ${r.badgeClass}">${r.badge}</span>
            <div class="search-info">
                <div class="search-title">${escapeHtml(r.title)}</div>
                <div class="search-meta">${escapeHtml(r.meta)}</div>
            </div>
        </div>`;
    });

    if (!searchExpanded && searchResults.length > maxInline) {
        html += `<div class="rotation-search-more" onclick="expandSearch()">More results + options ▾</div>`;
    } else if (searchExpanded) {
        html += `<div class="rotation-search-more" onclick="collapseSearch()">▴ Show less</div>`;
    }

    html += '<div class="rotation-search-hint">↑↓ navigate · Enter select · Tab skip · Esc close</div>';

    dropdown.innerHTML = html;
    dropdown.classList.remove('hidden');
}

function highlightSearchResult() {
    document.querySelectorAll('.rotation-search-result').forEach((el, i) => {
        el.classList.toggle('selected', i === searchSelectedIdx);
    });
}

function expandSearch() {
    searchExpanded = true;
    const songInput = document.getElementById('rotation-song');
    if (songInput) rotationSearch(songInput.value.trim());
}

function collapseSearch() {
    searchExpanded = false;
    const songInput = document.getElementById('rotation-song');
    if (songInput) rotationSearch(songInput.value.trim());
}

async function selectSearchResult(result) {
    const singerInput = document.getElementById('rotation-singer');
    const songInput = document.getElementById('rotation-song');
    const singer = singerInput ? singerInput.value.trim() : '';
    if (!singer) { singerInput.focus(); return; }

    hideSearchDropdown();
    showRotationIndicator('spin');

    try {
        if (result.type === 'local') {
            // Add singer with file already linked
            const resp = await fetch('/rotation/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    singer,
                    song_artist: songInput.value.trim(),
                    file_path: result.path,
                }),
            });
            const data = await resp.json();
            if (data.entries) { rotationData = data.entries; renderRotation(rotationData); }
        } else if (result.type === 'divebar' || result.type === 'youtube') {
            // Add singer + queue download
            const body = {
                singer,
                song_artist: result.song_artist || songInput.value.trim(),
                source: result.type,
            };
            if (result.type === 'divebar') {
                body.file_id = result.file_id;
                body.filename = result.filename;
            } else {
                body.youtube_url = result.youtube_url;
                body.filename = result.filename;
            }
            const resp = await fetch('/rotation/download-and-link', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (data.entries) { rotationData = data.entries; renderRotation(rotationData); }
        }

        // Clear form
        singerInput.value = '';
        songInput.value = '';
        showRotationIndicator('success');
    } catch (e) {
        showRotationIndicator('error');
    }
}

function hideSearchDropdown() {
    const dropdown = document.getElementById('rotation-search-dropdown');
    if (dropdown) dropdown.classList.add('hidden');
    searchSelectedIdx = -1;
    searchExpanded = false;
}

function formatDuration(seconds) {
    if (!seconds) return '';
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return m + ':' + String(s).padStart(2, '0');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}
```

Call `initRotationSearch()` at the bottom of the file or in the DOMContentLoaded handler.

- [x] **Step 4: Test manually in browser**

Start the dev server and test:
- Type 3+ chars in song field → dropdown appears
- Results show READY/DIVEBAR/YOUTUBE badges
- ↑↓ to navigate, Enter to select
- Tab to skip and add without linking
- Esc to close

- [x] **Step 5: Commit**

```bash
git add kj-controller/static/app.js kj-controller/templates/index.html kj-controller/static/style.css
git commit -m "feat(rotation): search-as-you-type dropdown in add-singer form"
```

---

## Task 7: Preparation Status Badges on Rotation Entries

**Files:**
- Modify: `kj-controller/static/app.js`

- [x] **Step 1: Update renderRotation to show prep badges**

In `app.js`, update the `renderRotation` function. After the existing status badge rendering, add prep badge logic:

```javascript
// Preparation status badge
const prepBadge = document.createElement('span');
prepBadge.className = 'rotation-prep-badge';

if (entry.file_path) {
    prepBadge.textContent = 'READY';
    prepBadge.classList.add('prep-ready');
} else if (entry.download_status === 'queued' || entry.download_status === 'downloading') {
    prepBadge.textContent = 'DOWNLOADING';
    prepBadge.classList.add(entry.download_source === 'youtube' ? 'prep-downloading-orange' : 'prep-downloading-green');
} else if (entry.download_status === 'failed') {
    prepBadge.textContent = 'FAILED';
    prepBadge.classList.add('prep-failed');
} else if (entry.url_fallback) {
    prepBadge.textContent = 'URL';
    prepBadge.classList.add('prep-url');
} else if (entry.gen_status) {
    // Phase 2 badges (future)
    if (entry.gen_status === 'awaiting_review') {
        prepBadge.textContent = 'NEEDS REVIEW';
        prepBadge.classList.add('prep-review');
    } else if (entry.gen_status === 'rendering') {
        prepBadge.textContent = 'RENDERING';
        prepBadge.classList.add('prep-rendering');
    } else {
        prepBadge.textContent = 'MAKING';
        prepBadge.classList.add('prep-making');
    }
} else {
    prepBadge.textContent = 'UNLINKED';
    prepBadge.classList.add('prep-unlinked');
}

// Append prep badge at end of info div
info.appendChild(prepBadge);
```

- [x] **Step 2: Update play button behavior**

The existing play button (▶) should check prep status:

```javascript
// Replace existing play/link button logic with:
if (entry.file_path) {
    const playBtn = document.createElement('button');
    playBtn.className = 'rotation-btn rotation-btn-play';
    playBtn.textContent = '\u25B6';
    playBtn.title = 'Play this song';
    playBtn.onclick = () => playMedia(entry.file_path);
    actions.insertBefore(playBtn, actions.firstChild);
} else if (entry.url_fallback) {
    const playBtn = document.createElement('button');
    playBtn.className = 'rotation-btn rotation-btn-play';
    playBtn.textContent = '\u25B6';
    playBtn.title = 'Play via browser mode';
    playBtn.onclick = () => enableBrowserMode(entry.url_fallback);
    actions.insertBefore(playBtn, actions.firstChild);
} else if (!entry.download_status || entry.download_status === 'failed') {
    const linkBtn = document.createElement('button');
    linkBtn.className = 'rotation-btn rotation-btn-link';
    linkBtn.textContent = '\uD83D\uDD17';
    linkBtn.title = 'Search and link a song';
    linkBtn.onclick = () => openRotationLinkSearch(entry.id, entry.song_artist);
    actions.insertBefore(linkBtn, actions.firstChild);
}
```

Add the link search opener that pre-fills the song field:

```javascript
function openRotationLinkSearch(entryId, songText) {
    // Open the add form with the song text pre-filled, in "link mode"
    const form = document.getElementById('rotation-add-form');
    if (form.classList.contains('hidden')) toggleRotationAddForm();
    const songInput = document.getElementById('rotation-song');
    songInput.value = songText || '';
    songInput.focus();
    songInput.dispatchEvent(new Event('input'));
    // TODO: In link mode, selecting a result should link to existing entry instead of adding new
}
```

- [x] **Step 3: Update status polling to check rotation downloads**

In the `updateStatus` function, after receiving `/status` response, check `rotation_downloads`:

```javascript
// In updateStatus(), after parsing status data:
if (data.rotation_downloads) {
    let needsRefresh = false;
    for (const [entryId, dl] of Object.entries(data.rotation_downloads)) {
        if (dl.status === 'complete') needsRefresh = true;
    }
    if (needsRefresh) fetchRotation();
}
```

- [x] **Step 4: Test manually**

Verify:
- Each rotation entry shows appropriate prep badge
- READY entries have ▶ play button
- UNLINKED entries have 🔗 link button
- Link button pre-fills search
- Downloads trigger badge updates

- [x] **Step 5: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat(rotation): preparation status badges and one-click play"
```

---

## Task 8: Final Integration Test and Cleanup

**Files:**
- All modified files

- [x] **Step 1: Run full test suite**

Run: `cd kj-controller && python -m pytest tests/ -q`
Expected: All pass

- [x] **Step 2: Run with coverage**

Run: `cd kj-controller && python -m pytest tests/ --cov --cov-report=term`
Expected: rotation modules above 70%

- [x] **Step 3: Manual end-to-end test**

Start the app and test the complete flow:
1. Open the add-singer form
2. Type a singer name
3. Type a song name (3+ chars) → search results appear
4. Select a local match → singer added with READY badge and ▶ button
5. Add another singer, select a Divebar result → green DOWNLOADING badge
6. Add another singer, skip linking → UNLINKED badge with 🔗 button
7. Click 🔗 on unlinked entry → search pre-fills with song text
8. Verify time estimates update when entries are linked

- [x] **Step 4: Commit any final fixes**

```bash
git add -A
git commit -m "test(rotation): final integration fixes for unified search"
```
