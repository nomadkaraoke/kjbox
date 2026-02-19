# Plan: Search YouTube

**Created:** 2026-02-19
**Branch:** main (will create feat/youtube-search)
**Status:** Draft

## Overview

Add a "Search YouTube" section to the KJ Controller UI, positioned between "Search Karaoke Nerds" and "Download Song". Uses yt-dlp's built-in `ytsearch:` capability with `extract_flat=True` for fast server-side search (~1.2s for 10 results). No new dependencies — yt-dlp is already installed. Download reuses the existing "Download Song" flow (inject URL + trigger), same pattern as KN search.

## Requirements

- [ ] Search YouTube from the KJ UI, returning 10 results
- [ ] Display: title, channel name, duration, view count per result
- [ ] Toggle checkbox to auto-prepend "karaoke" to queries (persisted in localStorage)
- [ ] One-click download: injects YouTube URL into existing Download Song input and triggers download
- [ ] Clear button to reset search results and input
- [ ] Auto-clear results on download click (same as KN behavior)
- [ ] Respect existing `youtube_cookies_file` config for authenticated searches
- [ ] Configurable result count (default 10, stored in config)

## Technical Approach

**Backend:** New `youtube_search.py` module (mirrors `karaoke_nerds.py` pattern). Uses `yt_dlp.YoutubeDL` as a Python library with `extract_flat=True` and `ytsearch10:` prefix. This returns lightweight metadata (title, channel, duration, view_count, video ID) without resolving stream URLs — much faster than full extraction.

**Frontend:** New UI section between KN Search and Download Song. Flat list of results (no grouping needed — each result is a single video, unlike KN where songs have multiple tracks). Each result row shows metadata + a Download button.

**Download flow:** Same as KN — clicking Download injects the YouTube URL into the existing Download Song input, clears YT search results, and triggers `downloadSong()`.

**Karaoke toggle:** A checkbox in the UI stored in `localStorage`. When checked, the frontend prepends "karaoke " to the query before sending to the backend. This keeps the backend simple (it just searches whatever query it receives).

**No new dependencies.** yt-dlp is already in requirements.txt and used by `media.py`.

## Implementation Steps

1. [ ] **Create `youtube_search.py`** — new module with `search(query, config=None, max_results=10)` function
   - Uses `yt_dlp.YoutubeDL` with `extract_flat=True`, `quiet=True`, `no_warnings=True`
   - Respects `youtube_cookies_file` from config
   - Returns list of dicts: `{id, title, channel, duration, duration_str, view_count, view_count_str, url}`
   - Helper `_format_duration(seconds)` → "M:SS" or "H:MM:SS"
   - Helper `_format_views(count)` → "1.2M views", "45K views", etc.
   - Catches exceptions and returns empty list on error

2. [ ] **Add route `POST /youtube/search`** in `routes.py`
   - Accepts `{query}` JSON body, validates min 2 chars
   - Calls `youtube_search.search(query, config)`
   - Returns JSON array of results

3. [ ] **Add HTML section** in `index.html` between KN Search and Download Song
   - Header: "Search YouTube"
   - Search row: text input + Search button + Clear button
   - Karaoke toggle: checkbox with label "Prefix 'karaoke'" (above or inline with search row)
   - Loading spinner (reuses `download-progress` pattern)
   - Results container

4. [ ] **Add JS functions** in `app.js`
   - `searchYouTube()` — reads query, applies karaoke prefix if toggled, calls API, renders results
   - `renderYTResults(results)` — flat list of result rows with metadata + download button
   - `clearYTResults()` — clear results and input
   - `downloadYTTrack(url)` — inject into Download Song input, clear YT results, trigger `downloadSong()`
   - Karaoke toggle state saved/loaded from `localStorage`

5. [ ] **Add CSS styles** in `style.css`
   - `.yt-search-section` — container styling
   - `.yt-result` — result row (title, channel, metadata, download button)
   - `.yt-channel`, `.yt-meta` — subdued text for channel/duration/views
   - Reuse existing button styles, keep consistent with KN section
   - Mobile responsive ordering

6. [ ] **Add unit tests** for `youtube_search.py`
   - Mock `yt_dlp.YoutubeDL` to test search result parsing
   - Test `_format_duration()` edge cases (None, 0, short, long)
   - Test `_format_views()` edge cases (None, 0, thousands, millions)
   - Test error handling (yt-dlp exception returns empty list)
   - Test cookies file integration

7. [ ] **Add integration tests** for the route in `test_routes.py`
   - Test `/youtube/search` with valid query (mocked yt-dlp)
   - Test validation (empty query, too short)
   - Test error handling

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `kj-controller/youtube_search.py` | Create | yt-dlp search module |
| `kj-controller/routes.py` | Modify | Add `POST /youtube/search` route |
| `kj-controller/templates/index.html` | Modify | Add YouTube search section |
| `kj-controller/static/app.js` | Modify | Add YT search/render/download/clear functions |
| `kj-controller/static/style.css` | Modify | Add YT result styles |
| `kj-controller/tests/unit/test_youtube_search.py` | Create | Unit tests for youtube_search module |
| `kj-controller/tests/integration/test_routes.py` | Modify | Add route integration tests |
| `docs/ARCHITECTURE.md` | Modify | Add module, route, dependency flow |
| `CLAUDE.md` | Modify | Update route count, add youtube_search.py to key files |
| `README.md` | Modify | Add YouTube search to features list |

## Testing Strategy

- **Unit tests:** Mock `yt_dlp.YoutubeDL` to test result parsing, formatting helpers, error handling, cookies config
- **Integration tests:** Mock `youtube_search.search()` at the route level to test HTTP request/response, validation
- **Manual testing:** Search on nomadpc, verify results render, download flow works, karaoke toggle persists

## Open Questions

None — all key decisions made.

## Rollback Plan

Single feature branch, revert commit if needed. No database changes, no config migrations.
