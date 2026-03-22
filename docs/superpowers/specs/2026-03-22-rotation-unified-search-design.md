# Rotation Unified Search & Preparation Status

**Date:** 2026-03-22
**Branch:** feat/sess-20260321-2303-rotation-unified-search
**Goal:** Streamline the KJ's live event workflow by integrating song search directly into the add-singer flow, showing preparation status on every rotation entry, and enabling one-click play from the rotation.

## Context & Motivation

### Current Workflow (Friction Points)

When a singer walks up and requests a song, the KJ currently:

1. Types singer name + song into the "Add Singer" form
2. Immediately searches "Search Karaoke Nerds" to verify the song exists
3. Checks if it's already in the local library (indicated by a play button) or needs downloading
4. If not in library, clicks download on an available version
5. Gives the singer a thumbs up (or tells them the song isn't available)
6. **Later**, when the singer's turn comes: opens "Available Songs", copy/pastes the song title, searches, clicks play

**Problems:**
- **Duplicate search:** Song searched in KN (to verify/download) then again in Available Songs (to play). Manual copy/paste under time pressure.
- **No prep visibility:** The rotation doesn't show which songs are ready vs. still downloading vs. not found. The KJ has to remember.
- **Context switching:** Three separate UI panels (rotation, KN search, Available Songs) for one logical workflow.

### Target Workflow

1. KJ types singer name + starts typing song → **search results appear inline**
2. KJ taps a result → singer added with file **linked and downloading** (or ready) in one action
3. If nothing found, KJ can paste a URL or trigger a MAKE job
4. Rotation shows **preparation status badges** on every entry — at a glance, the KJ sees what's ready and what needs attention
5. When the singer's turn comes → **one-click play** from the rotation entry

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Search UI | Hybrid inline + expandable | Fast for common case (local match), full power when needed |
| Search sources | Local catalog + Karaoke Nerds (with Divebar cross-ref) | These are the KJ's two go-to sources during live requests |
| Badge colors | Green=reliable, Orange=YouTube, Purple=MAKE, Yellow=review, Gray=unlinked | Divebar is reliable + quality, deserves same green confidence as local |
| MAKE integration | Gen API with auto_download + status polling | Full pipeline: create job → poll status → auto-download 720p MP4 |
| MAKE review | Pause at NEEDS REVIEW, KJ reviews between songs | Lyrics review is essential for quality; KJ does this during performances |
| YouTube search | Removed from unified search | Too slow; KJ searches YouTube faster in a browser tab |
| Phasing | Phase 1 (search + status) then Phase 2 (MAKE) | Phase 1 eliminates all current friction; Phase 2 adds new capability |

## Phase 1: Unified Search + Preparation Status

### 1.1 Song Field Search-As-You-Type

The song/artist input in the "Add Singer" form becomes a search-as-you-type field. After the KJ types 3+ characters and pauses for 300ms:

**Parallel search execution:**
1. **Local catalog** — `GET /search?q=<query>` (existing endpoint, SQLite FTS5, <10ms)
2. **Karaoke Nerds** — `POST /karaoke-nerds/search` (web scrape, 1-3s) with Divebar cross-reference

Results appear in an **inline dropdown** below the song field, ranked by immediacy:

**Result ranking (top to bottom):**
1. Local library matches (green READY badge)
2. Divebar-available KN tracks (green DIVEBAR badge — reliable download from GCS mirror)
3. YouTube-available KN tracks (orange YOUTUBE badge)
4. "More results + options ▾" link (expands to Mode 2)

**Each result row shows:**
- Badge (READY / DIVEBAR / YOUTUBE)
- Song title — Artist
- Metadata: brand code, format, duration, source
- For local: folder path hint

**Max 4 results in inline mode.** Keeps the dropdown compact — if the song is popular, the right version is in the top 4. If not, expand for more.

### 1.2 Expanded Mode ("More results + options")

Clicking "More results + options ▾" expands the dropdown into a taller scrollable panel with grouped sections:

**LOCAL LIBRARY** — all local matches (not just top 2)
**KARAOKE NERDS — DIVEBAR** — tracks downloadable from Divebar GCS mirror
**KARAOKE NERDS — YOUTUBE** — tracks downloadable from YouTube
**FALLBACK** — paste URL field (plays via browser mode)

The expanded mode uses sticky section headers. Each section is collapsible. The URL fallback field is always visible at the bottom.

### 1.3 What Happens When You Tap a Result

**Local library match (READY):**
1. Frontend calls `POST /rotation/add` with `{singer, song_artist, file_path}` — the existing add endpoint is extended to accept optional `file_path` and `duration` fields for single-action add+link
2. Duration looked up from MediaIndex by the backend
3. Singer added to rotation with green READY status
4. Form clears, dropdown closes
5. Confirmation flash: "✓ Sarah added — Bohemian Rhapsody linked and ready"

**Divebar download (DIVEBAR):**
1. Frontend calls `POST /rotation/download-and-link` with `{source: "divebar", file_id: "abc123"}`
2. Backend queues the download (via existing Divebar download machinery using `file_id`), gets a `download_id`
3. Singer added to rotation with `download_source: "divebar"`, `download_status: "queued"`, `download_id` set
4. Badge shows green DOWNLOADING
5. When download completes (see auto-linking mechanism below), `file_path` and `duration` set, `download_status` → `"complete"`

**YouTube download (YOUTUBE):**
1. Frontend calls `POST /rotation/download-and-link` with `{source: "youtube", youtube_url: "..."}`
2. Backend queues the download (via existing YouTube download machinery), gets a `download_id`
3. Singer added to rotation with `download_source: "youtube"`, `download_status: "queued"`, `download_id` set
4. Badge shows orange DOWNLOADING
5. When download completes, `file_path` and `duration` set, `download_status` → `"complete"`

**Auto-linking mechanism (backend push):** The `_download_worker` in `routes.py` is extended with a post-completion hook. After a download finishes, if the download queue item has an associated `rotation_entry_id`, the worker calls `rotation.link_file(entry_id, file_path)` and updates `download_status` to `"complete"`. This ensures auto-linking works even if the KJ closes their browser. The `download_id` on the rotation entry correlates to the download queue item UUID.

**URL fallback:**
1. URL stored on rotation entry (`url_fallback` field)
2. Singer added with gray URL status
3. Play button launches the URL via browser mode

**No result selected (Tab to skip):**
1. Singer added with whatever text is in the song field
2. No file linked — gray UNLINKED status
3. KJ can link later using the existing link button on the entry

### 1.4 Preparation Status Badges on Rotation Entries

Every rotation entry shows a preparation badge alongside the existing status badges (Now Singing, Up Next, etc.):

| Badge | Color | Meaning | Action on Tap |
|-------|-------|---------|---------------|
| ▶ READY | Green | File linked, ready to play | Play the song |
| ⬇ DOWNLOADING | Green | Divebar download in progress | Show progress |
| ⬇ DOWNLOADING | Orange | YouTube download in progress | Show progress |
| 🔗 URL | Gray | URL fallback linked | Play via browser mode |
| ? UNLINKED | Gray | No file mapped | Open link search |
| ! FAILED | Red | Download failed | Retry or re-search |
| ✦ MAKING | Purple | Gen job in progress (Phase 2) | Show gen status |
| ! NEEDS REVIEW | Yellow | Gen job paused for review (Phase 2) | Open review in browser |
| ◎ RENDERING | Purple | Gen job rendering video (Phase 2) | Show progress |

**Download status polling:** The `/status` endpoint (polled every 2s) is extended with a new `rotation_downloads` field:
```json
{
  "rotation_downloads": {
    "5": {"status": "downloading", "progress": 45},
    "8": {"status": "complete", "file_path": "/media/.../song.mp4"}
  }
}
```
This maps rotation entry IDs to their download state, using the `download_id` on the rotation entry to correlate with download queue items. When the frontend sees `"complete"`, it refreshes the rotation to pick up the auto-linked file. The backend push (download worker calling `rotation.link_file()`) is the authoritative linking mechanism; the frontend poll is just for UI responsiveness.

### 1.5 One-Click Play from Rotation

For READY entries, the play button (▶) on the rotation row directly calls `POST /play` with the linked `file_path`. No separate search needed.

For URL entries, the play button launches browser mode with the stored URL.

### 1.6 Rotation Entry Schema Additions

New fields on `rotation_entries` (SQLite):

```sql
ALTER TABLE rotation_entries ADD COLUMN download_source TEXT DEFAULT NULL;
-- "divebar", "youtube", "gen", or NULL (local/manual)

ALTER TABLE rotation_entries ADD COLUMN download_status TEXT DEFAULT NULL;
-- "queued", "downloading", "complete", "failed", or NULL

ALTER TABLE rotation_entries ADD COLUMN download_id TEXT DEFAULT NULL;
-- UUID correlating to the in-memory download queue item, for auto-linking on completion

ALTER TABLE rotation_entries ADD COLUMN url_fallback TEXT DEFAULT NULL;
-- Manual URL for browser mode playback
```

Note: The `rotation_archive` table does NOT need these columns. Archive preserves the final state (`file_path`, `duration`) but not transient download state.

### 1.7 Backend: Unified Search Endpoint

New endpoint that queries local catalog + Karaoke Nerds in parallel:

**`GET /rotation/search?q=<query>`**

Response:
```json
{
  "local": [
    {"path": "/media/.../song.zip", "artist": "Queen", "title": "Bohemian Rhapsody", "format": "cdg+mp3", "duration": 355, "disc_id": "ASK-002204"}
  ],
  "karaoke_nerds": [
    {
      "song": "Bohemian Rhapsody",
      "artist": "Queen",
      "tracks": [
        {
          "brand_name": "Karaoke Funhouse",
          "brand_code": "KFN-1234",
          "youtube_url": "https://youtube.com/watch?v=...",
          "is_community": true,
          "divebar": {
            "file_id": "abc123",
            "format": "mp4",
            "quality": "720p",
            "size_mb": 45
          },
          "in_library": false,
          "preferred": true
        }
      ]
    }
  ]
}
```

The frontend merges and ranks these results for display. The `song_artist` field for the rotation entry is built as `"{title} - {artist}"` to match the existing convention.

**Implementation:**
1. Runs local catalog search immediately (FTS5, <10ms)
2. Fires KN search in parallel via `karaoke_nerds.search()` (1-3s, 3s timeout)
3. After KN results arrive, extracts `kn_id` values and calls `divebar.lookup_kn_ids()` to cross-reference Divebar availability (adds `divebar` field to matching tracks)
4. Checks each KN track against local catalog by title/artist to set `in_library`
5. Sorts tracks: preferred brands first (per `kn_preferred_brands` config), then community, then others
6. Returns combined response

If KN times out, returns local results only with `"karaoke_nerds_timeout": true`.

### 1.8 Backend: Download-and-Link Endpoint

New endpoint that combines download + rotation linking:

**`POST /rotation/download-and-link`**

For Divebar (uses `file_id`, not a URL — the backend resolves the GCS URL via `divebar.get_download_url()`):
```json
{
  "id": 5,
  "source": "divebar",
  "file_id": "abc123",
  "filename": "KFN-1234 - Queen - Bohemian Rhapsody.mp4"
}
```

For YouTube:
```json
{
  "id": 5,
  "source": "youtube",
  "youtube_url": "https://youtube.com/watch?v=...",
  "filename": "KV-5678 - Queen - Bohemian Rhapsody.mp4"
}
```

This endpoint:
1. Queues the download (reuses existing download machinery), gets back a `download_id`
2. Updates the rotation entry: `download_source`, `download_status = "queued"`, `download_id`
3. Stores `rotation_entry_id` on the download queue item so the download worker can auto-link on completion
4. Returns the updated rotation entry

If `id` is omitted, creates a new rotation entry first (requires `singer` and `song_artist` in the body). This enables the one-action "tap result → add + download" flow.

### 1.9 Frontend Changes

**Modified: Add Singer form** (`app.js`, `index.html`)
- Song field gets `input` event listener with 300ms debounce
- Dropdown div rendered below form when results arrive
- Keyboard navigation: ↑↓ to navigate, Enter to select, Tab to skip (add without linking), Esc to close
- Selecting a result calls the appropriate endpoint (link for local, download-and-link for remote)

**Modified: Rotation rendering** (`app.js`)
- Each entry shows preparation badge based on `file_path`, `download_status`, `download_source`, `url_fallback`
- Play button behavior depends on prep status
- Link button (🔗) on unlinked entries opens the inline search pre-filled with the song text

**Modified: Status polling** (`app.js`)
- Extend existing 2s status poll to check for download completions relevant to rotation entries
- When a download completes, refresh rotation to update badges

**New CSS** (`style.css`)
- Prep badge styles (green/orange/purple/yellow/gray variants)
- Inline search dropdown styles
- Expanded mode styles with sticky headers

### 1.10 Conky Display

No changes needed. The conky display shows singer name + song + status badges. Preparation badges are a frontend-only concern for the KJ's admin view.

## Phase 2: MAKE Integration (Gen API)

### 2.1 Gen API Client

New module: `kj-controller/gen_client.py`

Handles communication with the gen API (`api.nomadkaraoke.com`):
- Authentication via admin token (stored in `config.json` as `gen_api_token`)
- Job creation via guided flow with `auto_download: true`
- Status polling
- Download URL retrieval

```python
class GenClient:
    def __init__(self, api_url, token):
        """Initialize with gen API base URL and auth token."""

    def create_job(self, artist, title) -> dict:
        """Create a gen job via audio search with auto_download.
        Uses POST /api/audio-search/search with auto_download=true,
        which performs audio search, auto-selects best result, creates
        the job, and starts processing — all in one call.
        Auth: X-Admin-Token header.
        Returns {job_id, status}."""

    def get_job_status(self, job_id) -> dict:
        """Poll job status. GET /api/jobs/{job_id}
        Returns {status, state_data, file_urls}."""

    def get_download_url(self, job_id) -> str | None:
        """Get 720p MP4 download URL when job is complete.
        GET /api/jobs/{job_id}/download-urls
        Returns signed URL for lossless_720p_mp4."""
```

### 2.2 Rotation Entry Schema Additions (Phase 2)

```sql
ALTER TABLE rotation_entries ADD COLUMN gen_job_id TEXT DEFAULT NULL;
-- Gen API job ID for MAKE entries

ALTER TABLE rotation_entries ADD COLUMN gen_status TEXT DEFAULT NULL;
-- Maps gen job states to display states:
-- "creating", "processing", "awaiting_review", "rendering", "complete", "failed"
```

### 2.3 MAKE Flow

**Trigger:** KJ clicks "MAKE" in the expanded search results, or taps a "Create new" button.

1. Frontend calls `POST /rotation/make` with `{id, artist, title}` (or creates a new rotation entry first)
2. Backend calls `GenClient.create_job(artist, title)`
3. Rotation entry updated: `gen_job_id` set, `gen_status = "creating"`
4. Entry shows purple MAKING badge

**Status polling:** A background thread (or periodic task) polls gen API for all active gen jobs once per minute:

New module: `kj-controller/gen_poller.py`

```python
class GenPoller:
    def __init__(self, gen_client, rotation_store, media_index, download_folder):
        """Polls gen API for active jobs and auto-downloads completed videos."""

    def poll_once(self):
        """Check all rotation entries with gen_job_id and non-terminal gen_status.
        Update gen_status based on API response.
        If complete: download 720p MP4, link to rotation entry."""

    def start(self):
        """Start background polling thread (every 60s)."""

    def stop(self):
        """Stop background polling."""
```

**Status mapping (gen API → rotation display):**

| Gen API Status | Rotation `gen_status` | Badge |
|----------------|----------------------|-------|
| `pending`, `downloading` | `processing` | MAKING (purple) |
| `separating_stage1`, `separating_stage2` | `processing` | MAKING (purple) |
| `transcribing`, `generating_screens` | `processing` | MAKING (purple) |
| `awaiting_review`, `in_review` | `awaiting_review` | NEEDS REVIEW (yellow) |
| `review_complete`, `rendering_video`, `generating_video` | `rendering` | RENDERING (purple) |
| `complete` | `complete` | Auto-downloads → READY (green) |
| `failed` | `failed` | FAILED (red) |

### 2.4 Auto-Download on Completion

When `GenPoller` detects a job is `complete`:

1. Calls `GenClient.get_download_url(job_id)` for the 720p MP4
2. Downloads the file to the configured download folder
3. Filename: `GEN-{job_id} - {artist} - {title}.mp4`
4. Updates MediaIndex (rescans or adds entry directly)
5. Links file to rotation entry: `file_path`, `duration`, `download_status = "complete"`
6. Badge transitions: MAKING/RENDERING → READY

### 2.5 NEEDS REVIEW Action

When a rotation entry shows NEEDS REVIEW (yellow ! badge):
- Tapping the badge opens the gen review UI in a new browser tab: `https://gen.nomadkaraoke.com/app/jobs#/{job_id}/review`
- Alternatively, if browser mode is preferred: launches the review URL via browser mode on the NomadPC display (KJ reviews on the big screen with headphones while someone sings)

### 2.6 Backend Endpoints (Phase 2)

**`POST /rotation/make`**
```json
{
  "id": 5,           // existing rotation entry ID (optional — creates new if omitted)
  "artist": "Queen",
  "title": "Bohemian Rhapsody",
  "singer": "River"  // only needed if creating new entry
}
```

Creates gen job, updates rotation entry with `gen_job_id` and `gen_status`.

**`GET /rotation/gen-status`**
Returns status of all active gen jobs for the current rotation. Used by frontend polling.

### 2.7 Configuration

New `config.json` fields:
```json
{
  "gen_api_url": "https://api.nomadkaraoke.com",
  "gen_api_token": "<admin-token>",
  "gen_poll_interval": 60,
  "gen_download_quality": "lossless_720p_mp4"
}
```

Setup: obtain an admin token from `gcloud secrets versions access latest --secret=admin-tokens --project=nomadkaraoke` and add to `config.json` on NomadPC.

## Testing

### Phase 1

**`test_rotation_search.py`** (new)
- Unified search endpoint: local + KN results merged
- KN timeout handling (returns local results only)
- Empty results
- Result ranking (local first, then Divebar, then YouTube)

**`test_download_and_link.py`** (new)
- Divebar download + link flow
- YouTube download + link flow
- Download completion auto-links file
- Invalid rotation entry ID

**Modified: `test_rotation_routes.py`**
- Add tests for new endpoints
- Test preparation badge logic in GET /rotation response

**Frontend: manual testing**
- Type in song field → results appear after 300ms
- Tap local result → singer added with READY badge
- Tap Divebar result → singer added with green DOWNLOADING badge
- Tap YouTube result → singer added with orange DOWNLOADING badge
- Tab to skip → singer added with UNLINKED badge
- Expand "More results" → grouped sections visible
- Play button on READY entry → VLC plays the song

### Phase 2

**`test_gen_client.py`** (new, mocked HTTP)
- Job creation
- Status polling
- Download URL retrieval
- Auth error handling
- Network error handling

**`test_gen_poller.py`** (new)
- Polls active jobs
- Status mapping (gen → rotation display states)
- Auto-download on completion
- File linking after download
- Handles job failure

**`test_rotation_make_routes.py`** (new)
- POST /rotation/make creates gen job
- GET /rotation/gen-status returns active job statuses
- MAKE without gen config returns 503

## Rollback Plan

Phase 1 is additive — the existing rotation UI continues to work. The search dropdown is a progressive enhancement on the song field. If it breaks, singers can still be added manually and files linked via the existing (improved) link button.

Phase 2 depends on gen API availability. If the gen API is down, the MAKE option simply doesn't appear in search results and existing MAKING entries show a connection error status.
