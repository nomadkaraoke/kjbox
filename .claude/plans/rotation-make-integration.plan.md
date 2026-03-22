# Rotation MAKE Integration (Gen API) — Phase 2

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "MAKE" button to the rotation search dropdown that creates karaoke videos via the gen API, with background status polling, auto-download on completion, and NEEDS REVIEW badges linking to the gen review UI.

**Architecture:** New `gen_client.py` handles HTTP communication with gen API. New `gen_poller.py` runs a background thread polling active gen jobs every 60s. Schema adds `gen_job_id` and `gen_status` columns. Frontend adds MAKE button in search, MAKING/NEEDS REVIEW/RENDERING badges, and review link on badge tap.

**Tech Stack:** Python 3, Flask, SQLite, requests, vanilla JS, gen API (`api.nomadkaraoke.com`)

**Spec:** `docs/superpowers/specs/2026-03-22-rotation-unified-search-design.md` § Phase 2

---

## Gen API Reference (from karaoke-gen docs/API.md)

**Create job:** `POST /api/audio-search/search`
- Body: `{"artist": "...", "title": "...", "auto_download": true, "theme_id": "nomad"}`
- Auth: `X-Admin-Token` header
- Returns job with `job_id` and `status`

**Get job status:** `GET /api/jobs/{job_id}`
- Auth: `X-Admin-Token` header
- Returns: `{status, file_urls, state_data, timeline}`

**Get download URLs:** `GET /api/jobs/{job_id}/download-urls`
- Auth: `X-Admin-Token` header
- Returns: `{download_urls: {finals: {lossy_720p_mp4: "/api/jobs/{job_id}/download/finals/lossy_720p_mp4", ...}}}`

**Download file:** `GET /api/jobs/{job_id}/download/finals/lossy_720p_mp4`
- Auth: `X-Admin-Token` header or `?token=...` query param
- Returns: streaming binary file (video/mp4)

**Job states:** pending → downloading → separating_stage1 → separating_stage2 → transcribing → generating_screens → awaiting_review → in_review → review_complete → rendering_video → generating_video → complete | failed

**Status mapping:**
| Gen API Status | Rotation gen_status | Badge |
|---|---|---|
| pending, downloading, separating_*, transcribing, generating_screens | processing | MAKING (purple) |
| awaiting_review, in_review | awaiting_review | NEEDS REVIEW (yellow) |
| review_complete, rendering_video, generating_video, instrumental_selected | rendering | RENDERING (purple) |
| complete | complete | → auto-download → READY (green) |
| failed | failed | FAILED (red) |

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `kj-controller/gen_client.py` | HTTP client for gen API |
| Create | `kj-controller/gen_poller.py` | Background thread polling active gen jobs |
| Modify | `kj-controller/rotation_store.py` | Add gen_job_id, gen_status columns |
| Modify | `kj-controller/rotation.py` | Add gen-aware methods |
| Modify | `kj-controller/routes.py` | POST /rotation/make, GET /rotation/gen-status |
| Modify | `kj-controller/app.py` | Initialize GenClient + GenPoller |
| Modify | `kj-controller/config.py` | Add gen_api_url/token defaults |
| Modify | `kj-controller/static/app.js` | MAKE button, gen badges, review link |
| Modify | `kj-controller/static/style.css` | MAKE badge styling |
| Create | `kj-controller/tests/unit/test_gen_client.py` | GenClient unit tests |
| Create | `kj-controller/tests/unit/test_gen_poller.py` | GenPoller unit tests |
| Modify | `kj-controller/tests/unit/test_rotation_store.py` | Gen column tests |

---

## Task 1: GenClient — HTTP Client for Gen API

**Files:**
- Create: `kj-controller/gen_client.py`
- Create: `kj-controller/tests/unit/test_gen_client.py`

- [ ] **Step 1: Write GenClient tests**

```python
# kj-controller/tests/unit/test_gen_client.py
"""Tests for GenClient — gen API HTTP client."""

import pytest
from unittest.mock import patch, MagicMock
from gen_client import GenClient, GenStatus, map_gen_status

class TestGenStatus:
    def test_processing_states(self):
        for state in ["pending", "downloading", "separating_stage1", "separating_stage2",
                       "transcribing", "generating_screens"]:
            assert map_gen_status(state) == GenStatus.PROCESSING

    def test_awaiting_review_states(self):
        for state in ["awaiting_review", "in_review"]:
            assert map_gen_status(state) == GenStatus.AWAITING_REVIEW

    def test_rendering_states(self):
        for state in ["review_complete", "rendering_video", "generating_video", "instrumental_selected"]:
            assert map_gen_status(state) == GenStatus.RENDERING

    def test_complete(self):
        assert map_gen_status("complete") == GenStatus.COMPLETE

    def test_failed(self):
        assert map_gen_status("failed") == GenStatus.FAILED

    def test_unknown_defaults_to_processing(self):
        assert map_gen_status("unknown_state") == GenStatus.PROCESSING


class TestGenClient:
    @pytest.fixture
    def client(self):
        return GenClient("https://api.example.com", "test-token")

    @patch('gen_client.requests.post')
    def test_create_job(self, mock_post, client):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"job_id": "abc123", "status": "pending"}
        result = client.create_job("Queen", "Bohemian Rhapsody")
        assert result["job_id"] == "abc123"
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["headers"]["X-Admin-Token"] == "test-token"

    @patch('gen_client.requests.post')
    def test_create_job_error(self, mock_post, client):
        mock_post.return_value.status_code = 500
        mock_post.return_value.raise_for_status.side_effect = Exception("Server error")
        with pytest.raises(Exception):
            client.create_job("Queen", "Bohemian Rhapsody")

    @patch('gen_client.requests.get')
    def test_get_job_status(self, mock_get, client):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "status": "transcribing", "state_data": {}, "file_urls": {}
        }
        result = client.get_job_status("abc123")
        assert result["status"] == "transcribing"

    @patch('gen_client.requests.get')
    def test_get_download_url_found(self, mock_get, client):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "download_urls": {"finals": {
                "lossy_720p_mp4": "/api/jobs/abc123/download/finals/lossy_720p_mp4"
            }}
        }
        url = client.get_download_url("abc123")
        assert "lossy_720p_mp4" in url
        assert "token=test-token" in url

    @patch('gen_client.requests.get')
    def test_get_download_url_not_found(self, mock_get, client):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"download_urls": {"finals": {}}}
        url = client.get_download_url("abc123")
        assert url is None

    @patch('gen_client.requests.get')
    def test_get_download_url_error(self, mock_get, client):
        mock_get.side_effect = Exception("Network error")
        url = client.get_download_url("abc123")
        assert url is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/unit/test_gen_client.py -v`

- [ ] **Step 3: Implement GenClient**

```python
# kj-controller/gen_client.py
"""GenClient: HTTP client for the gen API (karaoke video generation)."""

import logging

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


class GenStatus:
    """Mapped gen status values stored in rotation entries."""
    PROCESSING = "processing"
    AWAITING_REVIEW = "awaiting_review"
    RENDERING = "rendering"
    COMPLETE = "complete"
    FAILED = "failed"

    TERMINAL = {COMPLETE, FAILED}
    ACTIVE = {PROCESSING, AWAITING_REVIEW, RENDERING}


_STATUS_MAP = {
    "pending": GenStatus.PROCESSING,
    "downloading": GenStatus.PROCESSING,
    "separating_stage1": GenStatus.PROCESSING,
    "separating_stage2": GenStatus.PROCESSING,
    "transcribing": GenStatus.PROCESSING,
    "generating_screens": GenStatus.PROCESSING,
    "awaiting_review": GenStatus.AWAITING_REVIEW,
    "in_review": GenStatus.AWAITING_REVIEW,
    "review_complete": GenStatus.RENDERING,
    "rendering_video": GenStatus.RENDERING,
    "generating_video": GenStatus.RENDERING,
    "instrumental_selected": GenStatus.RENDERING,
    "complete": GenStatus.COMPLETE,
    "failed": GenStatus.FAILED,
}


def map_gen_status(api_status):
    """Map a gen API job status string to a rotation display status."""
    return _STATUS_MAP.get(api_status, GenStatus.PROCESSING)


class GenClient:
    """HTTP client for the gen API."""

    def __init__(self, api_url, token):
        self.api_url = api_url.rstrip("/")
        self.token = token

    def _headers(self):
        return {"X-Admin-Token": self.token, "Content-Type": "application/json"}

    def _bearer_headers(self):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def create_job(self, artist, title):
        """Create a gen job via audio search with auto_download.

        Returns dict with job_id and status.
        """
        resp = requests.post(
            f"{self.api_url}/api/audio-search/search",
            json={"artist": artist, "title": title, "auto_download": True, "theme_id": "nomad"},
            headers=self._headers(),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def get_job_status(self, job_id):
        """Poll job status. Returns dict with status, state_data, file_urls."""
        resp = requests.get(
            f"{self.api_url}/api/jobs/{job_id}",
            headers=self._headers(),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def get_download_url(self, job_id, quality="lossy_720p_mp4"):
        """Get download URL for a completed job.

        Returns full URL string for streaming download, or None if not available.
        """
        try:
            resp = requests.get(
                f"{self.api_url}/api/jobs/{job_id}/download-urls",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            finals = data.get("download_urls", {}).get("finals", {})
            relative_url = finals.get(quality)
            if relative_url:
                return f"{self.api_url}{relative_url}?token={self.token}"
            return None
        except Exception as e:
            logger.error("Failed to get download URL for job %s: %s", job_id, e)
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && python -m pytest tests/unit/test_gen_client.py -v`

- [ ] **Step 5: Commit**

```bash
git add kj-controller/gen_client.py kj-controller/tests/unit/test_gen_client.py
git commit -m "feat(rotation): add GenClient for gen API communication"
```

---

## Task 2: Schema — Add Gen Columns to RotationStore

**Files:**
- Modify: `kj-controller/rotation_store.py`
- Modify: `kj-controller/tests/unit/test_rotation_store.py`

- [ ] **Step 1: Write gen column tests**

```python
# Append to test_rotation_store.py

class TestGenColumns:
    def test_add_entry_has_gen_fields(self, store):
        entry = store.add_entry("Alice", "Song A")
        assert entry["gen_job_id"] is None
        assert entry["gen_status"] is None

    def test_set_gen_status(self, store):
        entry = store.add_entry("Alice", "Song A")
        updated = store.set_gen_status(entry["id"], job_id="job-123", status="processing")
        assert updated["gen_job_id"] == "job-123"
        assert updated["gen_status"] == "processing"

    def test_update_gen_status(self, store):
        entry = store.add_entry("Alice", "Song A")
        store.set_gen_status(entry["id"], job_id="job-123", status="processing")
        updated = store.set_gen_status(entry["id"], job_id="job-123", status="awaiting_review")
        assert updated["gen_status"] == "awaiting_review"

    def test_get_active_gen_entries(self, store):
        e1 = store.add_entry("Alice", "Song A")
        e2 = store.add_entry("Bob", "Song B")
        e3 = store.add_entry("Carol", "Song C")
        store.set_gen_status(e1["id"], "job-1", "processing")
        store.set_gen_status(e2["id"], "job-2", "complete")  # terminal
        store.set_gen_status(e3["id"], "job-3", "awaiting_review")
        active = store.get_active_gen_entries()
        assert len(active) == 2
        job_ids = {e["gen_job_id"] for e in active}
        assert job_ids == {"job-1", "job-3"}
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Add columns and methods**

Add to schema (after url_fallback):
```sql
gen_job_id TEXT DEFAULT NULL,
gen_status TEXT DEFAULT NULL
```

Add methods to RotationStore:
```python
def set_gen_status(self, entry_id, job_id, status):
    """Set gen job tracking fields on a rotation entry."""
    ...

def get_active_gen_entries(self):
    """Return entries with active (non-terminal) gen jobs."""
    ...
```

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

---

## Task 3: Extend RotationManager for Gen Operations

**Files:**
- Modify: `kj-controller/rotation.py`
- Modify: `kj-controller/tests/unit/test_rotation.py`

- [ ] **Step 1: Write coordinator gen tests**

```python
class TestCoordinatorGen:
    def test_set_gen_status(self, mgr):
        entry = mgr.add_entry("Alice", "Song A")
        updated = mgr.set_gen_status(entry["id"], "job-123", "processing")
        assert updated["gen_job_id"] == "job-123"

    def test_complete_gen_job(self, mgr):
        """complete_gen_job links file and sets gen_status to complete."""
        entry = mgr.add_entry("Alice", "Song A")
        mgr.set_gen_status(entry["id"], "job-123", "rendering")
        updated = mgr.complete_gen_job("job-123", "/media/song.mp4")
        assert updated["file_path"] == "/media/song.mp4"
        assert updated["gen_status"] == "complete"
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement coordinator methods**

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

---

## Task 4: GenPoller — Background Job Status Polling

**Files:**
- Create: `kj-controller/gen_poller.py`
- Create: `kj-controller/tests/unit/test_gen_poller.py`

- [ ] **Step 1: Write GenPoller tests**

Test: poll_once updates gen_status from API, auto-downloads on complete, handles failures, skips terminal entries.

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement GenPoller**

GenPoller has:
- `__init__(gen_client, rotation_store, media, download_folder)` — stores references
- `poll_once()` — gets active gen entries, polls each, updates status, downloads completed
- `start()` — launches daemon thread polling every `poll_interval` seconds
- `stop()` — sets stop event

On complete: calls `gen_client.get_download_url()`, downloads via `media.download_from_url()`, links to rotation entry.

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

---

## Task 5: Routes — /rotation/make and /rotation/gen-status

**Files:**
- Modify: `kj-controller/routes.py`
- Modify: `kj-controller/app.py`
- Modify: `kj-controller/config.py`
- Create or modify: tests

- [ ] **Step 1: Add config defaults**

In `config.py` defaults dict:
```python
"gen_api_url": "",
"gen_api_token": "",
"gen_poll_interval": 60,
```

- [ ] **Step 2: Initialize GenClient + GenPoller in app.py**

In `create_app()` and `start_app()`:
```python
gen_api_url = cfg.get('gen_api_url', '')
gen_api_token = cfg.get('gen_api_token', '')
if gen_api_url and gen_api_token:
    from gen_client import GenClient
    from gen_poller import GenPoller
    flask_app.gen_client = GenClient(gen_api_url, gen_api_token)
    flask_app.gen_poller = GenPoller(
        flask_app.gen_client, flask_app.rotation.store,
        flask_app.media, cfg.get('download_folder', ''))
    flask_app.gen_poller.start()
else:
    flask_app.gen_client = None
    flask_app.gen_poller = None
```

- [ ] **Step 3: Write route tests**

Test POST /rotation/make with mock gen_client. Test GET /rotation/gen-status. Test 503 when gen not configured.

- [ ] **Step 4: Implement routes**

POST /rotation/make:
- Validate artist, title required
- Get or create rotation entry
- Call gen_client.create_job(artist, title)
- Set gen_status on rotation entry
- Return entry + entries

GET /rotation/gen-status:
- Get active gen entries from store
- Return list with gen_job_id, gen_status, entry_id

- [ ] **Step 5: Run tests**

- [ ] **Step 6: Run full test suite**

- [ ] **Step 7: Commit**

---

## Task 6: Frontend — MAKE Button and Gen Badges

**Files:**
- Modify: `kj-controller/static/app.js`
- Modify: `kj-controller/static/style.css`

- [ ] **Step 1: Add MAKE option to search dropdown**

In the expanded search results, add a "MAKE" button at the bottom that triggers a gen job creation:
- Appears in expanded mode only (after "More results")
- Shows "MAKE this song" with purple badge
- On click: calls POST /rotation/make with artist/title parsed from the search query

- [ ] **Step 2: Update prep badges for gen statuses**

The Phase 1 code already has placeholder badge logic for `entry.gen_status`. Update:
- `gen_status === "processing"` → MAKING (purple)
- `gen_status === "awaiting_review"` → NEEDS REVIEW (yellow), clickable
- `gen_status === "rendering"` → RENDERING (purple)
- gen_status complete → handled by file_path being set (READY)

- [ ] **Step 3: Add review link on NEEDS REVIEW badge**

When NEEDS REVIEW badge is tapped, open gen review URL:
```javascript
window.open(`https://gen.nomadkaraoke.com/app/jobs#/${entry.gen_job_id}/review`, '_blank');
```

- [ ] **Step 4: Add gen status polling**

In the status update handler, poll /rotation/gen-status periodically (every 30s) and refresh rotation when gen statuses change.

- [ ] **Step 5: CSS for MAKE button in dropdown**

```css
.search-badge-make { background: #7c3aed; }
```

Already added in Phase 1 prep badge styles.

- [ ] **Step 6: Commit**

---

## Task 7: Integration Testing and Cleanup

- [ ] **Step 1: Run full test suite**
- [ ] **Step 2: Run with coverage (target 70%+)**
- [ ] **Step 3: Manual end-to-end test plan**

1. Configure gen_api_url and gen_api_token in test config
2. Add singer, type song, expand search
3. Click MAKE → purple MAKING badge appears
4. Wait for processing → NEEDS REVIEW badge appears
5. Click NEEDS REVIEW → gen review UI opens in new tab
6. After review → RENDERING badge appears
7. After completion → auto-download → READY badge with play button

- [ ] **Step 4: Commit any final fixes**
