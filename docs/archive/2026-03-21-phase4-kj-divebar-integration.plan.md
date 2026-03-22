# Plan: Phase 4 — KJ Controller Divebar Integration

**Created:** 2026-03-21
**Branch:** feat/sess-20260320-0015-divebar-drive-download
**Status:** Draft
**Depends on:** Phase 1 (GCS mirror), Phase 3 (cross-reference index + API)
**Blocks:** Nothing (final phase)

## Overview

Add Divebar karaoke track search and download to the KJ Controller web app. Two integration points:

1. **Dedicated Divebar search tab** — search the entire Divebar catalog by artist/title, browse by brand, download tracks to the miniPC
2. **KN search cross-reference** — when searching Karaoke Nerds, show "Available in Divebar" badges on tracks that exist in the Divebar collection, with one-click download from GCS instead of YouTube

### Why

- **Free content:** Divebar tracks are free community karaoke — no YouTube download needed, no copyright concerns
- **Better quality:** Many Divebar tracks are purpose-built CDG/MP4 karaoke files, higher quality than YouTube rips
- **Faster downloads:** GCS → miniPC is faster and more reliable than YouTube (no rate limits, no cookies, no yt-dlp issues)
- **Discovery:** Dedicated search helps KJs discover community karaoke content they didn't know existed

## Requirements

- [ ] New "Divebar" tab/section in KJ Controller UI (alongside Catalog, KN, YouTube)
- [ ] Divebar search with artist/title text input
- [ ] Search results show: artist, title, brand, format (CDG/MP4/etc.), file size
- [ ] One-click download from GCS mirror to miniPC download folder
- [ ] Download progress tracking (reuse existing download queue UI)
- [ ] KN search results show "Divebar" badge when a track has cross-reference matches
- [ ] KN results: "Download from Divebar" button alongside existing YouTube download
- [ ] CDG+MP3 pair handling — download both files as a pair
- [ ] ZIP handling — download and optionally extract

## Technical Approach

### Backend: New Module `divebar.py`

New Python module following the pattern of `karaoke_nerds.py` and `youtube_search.py`:

```python
class DivebarClient:
    """Client for the Divebar catalog API (served by Phase 3 Cloud Function)."""

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """Search Divebar catalog by artist/title."""
        # Calls Phase 3 API: GET /api/divebar/search?q=...

    def lookup_kn_ids(self, kn_ids: list[int]) -> dict[int, list[dict]]:
        """Bulk lookup which KN songs have Divebar versions."""
        # Calls Phase 3 API: GET /api/divebar/lookup?kn_ids=...

    def get_download_url(self, file_id: str) -> str:
        """Get a signed GCS download URL for a Divebar file."""
        # Returns a time-limited signed URL for direct download
```

### Backend: Download from GCS

Extend `media.py` download flow to handle GCS URLs:

- Detect URL type: YouTube URL → existing yt-dlp flow; GCS signed URL → direct HTTP download
- For CDG+MP3 pairs: download both files (they're separate in GCS)
- For ZIPs: download and extract to download folder
- Same indexing flow after download (add to MediaIndex, save to media_index.json)

### Backend: New Routes

```python
# Divebar search
POST /divebar/search          {"query": "bohemian rhapsody"}
  → [{"file_id": "abc", "artist": "Queen", "title": "Bohemian Rhapsody",
      "brand": "WTF Karaoke", "format": "mp4", "size": 45000000, "gcs_path": "gs://..."}]

# Divebar download (queues download from GCS)
POST /divebar/download        {"file_id": "abc", "gcs_path": "gs://..."}
  → {"id": "uuid", "status": "queued"}

# KN cross-reference lookup (called when KN results are displayed)
POST /divebar/kn-lookup       {"kn_ids": [123, 456, 789]}
  → {"123": [{"file_id": "abc", "brand": "WTF Karaoke", "format": "mp4"}]}
```

### Frontend: Divebar Tab

New section in `index.html` (following KN/YouTube tab pattern):

```
┌─────────────────────────────────────────────┐
│ [Catalog] [Karaoke Nerds] [Divebar] [YouTube] │
├─────────────────────────────────────────────┤
│ 🔍 [Search Divebar catalog...        ] [Search] │
│                                               │
│ ▼ Queen - Bohemian Rhapsody                   │
│   ├ WTF Karaoke  [MP4] 45MB  [Download]      │
│   ├ Nomad Karaoke [CDG+MP3] 8MB [Download]   │
│   └ CKK          [ZIP] 12MB  [Download]      │
│                                               │
│ ▼ Queen - Don't Stop Me Now                   │
│   └ Funbox Karaoke [MP4] 38MB [Download]     │
└─────────────────────────────────────────────┘
```

Results grouped by song (artist + title), with multiple brand/format options expandable underneath. This mirrors the KN results layout (collapsible song headers with track list).

### Frontend: KN Cross-Reference

When KN search results render, make a background call to `/divebar/kn-lookup` with all KN song IDs. For matches, inject a "Divebar" badge and download button:

```
▼ Queen - Bohemian Rhapsody
  ├ Sing King    [KV] YouTube  [Download]
  ├ Karaoke Version [KV] YouTube  [Download]
  └ 🟢 WTF Karaoke [Divebar] MP4  [Download from Divebar]  ← NEW
```

### Authentication for GCS Downloads

The KJ Controller runs on LAN. For downloading from GCS:

**Option A:** Signed URLs — Phase 3 API returns time-limited signed URLs that the miniPC can download directly. No credentials needed on the miniPC.

**Option B:** Service account key on miniPC — Store a key file on the device, use `google-cloud-storage` Python library. More complex but no URL expiry issues.

**Recommendation:** Option A (signed URLs). Simpler, no credentials on device, URLs valid for 1 hour (plenty for download).

### Configuration

Add to `config.json`:
```json
{
  "divebar_api_url": "https://divebar-lookup-HASH-uc.a.run.app",
  "divebar_enabled": true
}
```

## Implementation Steps

1. [ ] **Create `divebar.py` backend module**
   - `DivebarClient` class with `search()`, `lookup_kn_ids()`, `get_download_url()`
   - HTTP client calling Phase 3 API
   - Error handling (API unavailable, timeout)

2. [ ] **Extend `media.py` for GCS downloads**
   - Add `download_from_url(url, filename)` method (generic HTTP download, not yt-dlp)
   - Handle CDG+MP3 pairs (download both files)
   - Handle ZIP download + extraction
   - Reuse existing `MediaIndex` indexing after download

3. [ ] **Add routes to `routes.py`**
   - `POST /divebar/search`
   - `POST /divebar/download`
   - `POST /divebar/kn-lookup`
   - Follow existing patterns (error handling, JSON response format)

4. [ ] **Add Divebar tab to `index.html`**
   - New tab button in the search section tab bar
   - Search input + button
   - Results container

5. [ ] **Add Divebar search JS to `app.js`**
   - `searchDivebar()` function
   - `renderDivebarResults()` — grouped by song, expandable tracks
   - `downloadDivebarTrack(fileId, gcsPath)` — queue download
   - Format badges (CDG+MP3, MP4, ZIP)
   - Brand labels

6. [ ] **Add KN cross-reference to `app.js`**
   - After KN results render, call `/divebar/kn-lookup` with all KN song IDs
   - Inject Divebar badges and download buttons into existing KN results
   - "Download from Divebar" button handler

7. [ ] **Add CSS for Divebar UI**
   - Divebar brand colors/badges
   - Format badges (distinct from KN brand badges)
   - "Available in Divebar" highlight

8. [ ] **Add config support**
   - `divebar_api_url` and `divebar_enabled` in config.json
   - Graceful degradation when API unavailable (hide tab, no badges)

9. [ ] **Write tests**
   - Unit tests for `divebar.py` (mock API responses)
   - Unit tests for GCS download in `media.py`
   - Unit tests for new routes
   - Manual testing with real API

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `kj-controller/divebar.py` | Create | DivebarClient — search, lookup, download URLs |
| `kj-controller/media.py` | Modify | Add generic HTTP download + ZIP extraction |
| `kj-controller/routes.py` | Modify | Add /divebar/* routes |
| `kj-controller/config.py` | Modify | Add divebar config fields |
| `kj-controller/templates/index.html` | Modify | Add Divebar tab UI |
| `kj-controller/static/app.js` | Modify | Divebar search, results, KN cross-reference |
| `kj-controller/static/style.css` | Modify | Divebar styling |
| `kj-controller/tests/test_divebar.py` | Create | Unit tests |
| `kj-controller/tests/test_media_download.py` | Create | Download tests |
| `kj-controller/requirements.txt` | Modify | Add requests (if not already present) |

## Testing Strategy

- **Unit tests** for `DivebarClient` — mock HTTP responses, test search parsing, lookup batching
- **Unit tests** for GCS download — mock HTTP, verify file written correctly, verify ZIP extraction
- **Unit tests** for routes — test request/response format, error cases
- **Manual testing** — run KJ Controller locally, test search, download, KN integration
- **Device testing** — deploy to NomadPC, test real downloads from GCS to local catalog

## Open Questions

- [ ] Should Divebar results also appear in the main Catalog search (unified with USB catalog)? Or only in the dedicated tab?
- [ ] Download folder — same as YouTube downloads, or separate `divebar/` subfolder?
- [ ] Should we show file size in results? (Helps KJ decide between MP4 vs CDG+MP3)
- [ ] Should downloaded Divebar tracks be tagged with their brand in the media index?
- [ ] Offline fallback — if the Divebar API is unreachable, should we cache the last successful search index locally?

## Rollback Plan

- Remove Divebar tab from `index.html`
- Remove new routes from `routes.py`
- Delete `divebar.py`
- Revert `media.py` and `app.js` changes
- Downloaded files remain in catalog (they're just regular media files)
