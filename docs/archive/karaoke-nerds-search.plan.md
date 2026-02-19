# Plan: Karaoke Nerds Search Integration

**Created:** 2026-02-19
**Branch:** TBD (needs `/start`)
**Status:** Draft

## Overview

Add a "Search Karaoke Nerds" section to the KJ UI above the existing "Download Song" section. This searches karaokenerds.com with the `webFilter=OnlyWeb` parameter and displays results grouped by song+artist, with tracks expandable underneath. Tracks are sorted by quality tier: community tracks first (highlighted), then configurable preferred brands (highlighted differently), then everything else. Each track has a one-click download button that feeds the YouTube URL into the existing yt-dlp download pipeline.

## Research Summary (from Playwright exploration)

- **Search URL**: `https://karaokenerds.com/Search?query={query}&webFilter=OnlyWeb`
- **Data delivery**: All server-rendered HTML — no API. Track data for all songs is embedded in the initial page response (hidden rows toggled client-side).
- **HTML structure**: `<table>` with song rows (title, artist, track count), and hidden `<tr>` rows containing `<ul class="list-group">` with track `<li>` items.
- **Community tracks**: Identified by `<img class="check" title="Global Karaoke Community">` inside the badge span.
- **YouTube URLs**: Every web-only track has a link like `https://www.youtube.com/watch?v={id}&list={playlistId}` — we strip the `&list=` param for clean download URLs.
- **Brand info**: Each track has a brand name (e.g., "Karaoke Version", "ObsKure Karaoke") and brand code (e.g., "KV", "OBSK").

## Requirements

- [ ] New "Search Karaoke Nerds" section in right column, above "Download Song"
- [ ] Text input with search button, searches on Enter or click
- [ ] Results grouped by song+artist, expandable to show tracks
- [ ] **Three-tier track sorting**: community first, preferred brands second, then rest
- [ ] Community tracks visually highlighted (green badge/glow)
- [ ] Preferred brand tracks visually highlighted (different color, e.g., purple/gold badge)
- [ ] **Configurable preferred brands list** in `config.json` (brand codes in priority order)
- [ ] Preferred brands editable from the UI (in the KN search section header or System section)
- [ ] One-click download button per track (uses YouTube URL → existing `/download` endpoint)
- [ ] Progress/loading state during search
- [ ] Error handling (network errors, empty results, scrape failures)
- [ ] Mobile-responsive (follows existing patterns)

## Technical Approach

### Backend: New Python module `karaoke_nerds.py` + route in `routes.py`

Create a new module that:
1. Takes a search query string
2. Fetches `https://karaokenerds.com/Search?query={query}&webFilter=OnlyWeb` using `requests` (already available via yt-dlp dependency)
3. Parses the HTML response with BeautifulSoup
4. Extracts structured data: songs (title, artist) → tracks (brand_name, brand_code, youtube_url, is_community)
5. Returns as JSON (sorting happens client-side using the preferred brands list)

**Why server-side scraping?** The KJ device (Pi/mini PC) runs the Flask server. Scraping from the browser would hit CORS issues. Server-side avoids this cleanly.

**Parser choice**: Use `beautifulsoup4` — the HTML structure uses classes and nested tables that are straightforward to parse with BS4. Single `pip install`, far cleaner than regex for nested HTML.

### Config: Preferred brands list

Add `kn_preferred_brands` to `config.json` defaults:
```json
{
  "kn_preferred_brands": ["KV", "KFN"]
}
```
- `KV` = Karaoke Version, `KFN` = Karafun — these are known high-quality commercial brands
- Default list is sensible out of the box, KJ can customize per their preferences
- Saved via existing `save_config_value()` pattern
- Exposed to frontend via a new route `GET /karaoke-nerds/config` / `POST /karaoke-nerds/config`

### Track sort order (3 tiers)

Within each song group, tracks are sorted:
1. **Community** (`is_community: true`) — highest quality, community-created
2. **Preferred brands** — in the order specified in `kn_preferred_brands` config
3. **Everything else** — remaining brands in original order from KN

This sorting happens **client-side** in `renderKNResults()` so it updates instantly when the user changes their preferred brands without re-fetching.

### Frontend: New section in `index.html` + functions in `app.js` + styles in `style.css`

- Search input + button, following the existing Download section pattern
- Small "Preferred Brands" settings link/button in header row (opens inline editor or small popover)
- Results rendered as an expandable grouped list (similar to folder view in Available Songs)
- Three visual tiers: community (green), preferred (gold/amber), regular (default)
- Download button per track calls existing `/download` endpoint

### Data flow

```
User types "bohemian rhapsody" → clicks Search
  → JS POST /karaoke-nerds/search { query: "bohemian rhapsody" }
  → Flask route calls karaoke_nerds.search(query)
  → Python fetches karaokenerds.com HTML, parses, returns JSON
  → JS sorts tracks by tier (community → preferred → rest)
  → JS renders grouped results with expand/collapse
  → User clicks Download on "ObsKure Karaoke" track
  → JS calls existing /download with { url: "https://youtube.com/watch?v=..." }
  → yt-dlp downloads, adds to media index
  → Media list refreshes
```

## Implementation Steps

### 1. Add `beautifulsoup4` dependency
- Add to `requirements.txt`
- Verify `requests` is already available (it is — yt-dlp dependency)

### 2. Create `kj-controller/karaoke_nerds.py`
- `search(query: str) -> list[dict]` function
- Fetches the search page with `requests.get()` (User-Agent header, 5s timeout)
- Parses HTML with BeautifulSoup
- Extraction logic:
  - Find all song rows in the table (rows with title/artist cells and a track count link)
  - For each song row, find the adjacent hidden track row containing `<ul class="list-group">`
  - Extract each `<li class="track">`: brand name (first `<a>`), brand code (`.badge` text), YouTube URL (link with `youtube.com`), is_community (`img.check` present)
  - Strip `&list=` param from YouTube URLs
- Returns list of song dicts:
  ```python
  [
    {
      "title": "Bohemian Rhapsody",
      "artist": "Queen",
      "tracks": [
        {
          "brand_name": "ObsKure Karaoke",
          "brand_code": "OBSK",
          "youtube_url": "https://www.youtube.com/watch?v=wy7voMFbN7U",
          "is_community": true
        },
        {
          "brand_name": "Karaoke Version",
          "brand_code": "KV",
          "youtube_url": "https://www.youtube.com/watch?v=oVbXpK_BRbw",
          "is_community": false
        },
        ...
      ]
    },
    ...
  ]
  ```
- Returns empty list on any error (logged server-side)

### 3. Add config defaults and routes to `routes.py`
- Add `"kn_preferred_brands": ["KV", "KFN"]` to defaults in `config.py`
- Add routes in `routes.py`:
  - `POST /karaoke-nerds/search` — accepts `{ "query": "..." }`, returns search results JSON
  - `GET /karaoke-nerds/config` — returns `{ "preferred_brands": [...] }`
  - `POST /karaoke-nerds/config` — accepts `{ "preferred_brands": [...] }`, saves via `save_config_value()`
- Pass preferred brands to the frontend via `KJ_CONFIG` in the template (alongside `latinSpecialMap`, etc.) so it's available on page load without an extra fetch

### 4. Add HTML section to `index.html`
- New `<div class="container kn-search-section">` in col2, above the existing download section
- Contains:
  - Header row: `<h2>Search Karaoke Nerds</h2>` + small settings gear button for preferred brands
  - Text input + Search button
  - Loading spinner (same pattern as download)
  - Results container `<div id="kn-results"></div>`
- Small inline preferred brands editor (shown/hidden by gear button):
  - Text input for comma-separated brand codes
  - Save button
  - Current list displayed as small badges

### 5. Add JavaScript to `app.js`
- `searchKaraokeNerds()` — triggered by button click or Enter key
  - Validates input (min 2 chars), shows spinner
  - POST to `/karaoke-nerds/search` with query
  - On success: calls `renderKNResults(data)`
  - On error: logs error message
- `renderKNResults(songs)` — renders grouped song results
  - Each song: clickable header showing "Title — Artist (N tracks)"
  - First song auto-expanded, rest collapsed
  - Within each song, tracks sorted by tier:
    1. Community tracks (sorted alphabetically by brand name)
    2. Preferred brand tracks (in config order)
    3. Remaining tracks (alphabetically)
  - Each track row: `[tier indicator] Brand Name [Download btn]`
  - Community tracks: green left border + "Community" badge
  - Preferred tracks: amber/gold left border + star icon
  - Regular tracks: no special decoration
- `downloadKNTrack(youtubeUrl, brandName, songTitle)` — initiates download
  - Disables the clicked download button, shows spinner on it
  - Calls `/download` with `{ url: youtubeUrl }`
  - On success: log message, flash success, refresh media list
  - On error: re-enable button, log error
- `clearKNSearch()` — clears input and results
- `toggleKNPrefs()` — show/hide preferred brands editor
- `saveKNPrefs()` — POST to `/karaoke-nerds/config`, update local state

### 6. Add CSS to `style.css`
- `.kn-search-section` — inherits container styles, mobile order between download and available-songs
- `.kn-results` — scrollable results container (max-height similar to media list)
- `.kn-song-header` — expandable song header (reuses folder-header pattern)
- `.kn-track-list` — collapsible track list
- `.kn-track` — individual track row, flex layout
- `.kn-track.community` — green left border + subtle green background tint
- `.kn-track.preferred` — amber/gold left border + subtle amber tint
- `.kn-community-badge` — small green pill badge "Community"
- `.kn-preferred-badge` — small amber pill badge with star
- `.kn-download-btn` — compact download button per track
- `.kn-prefs` — preferred brands editor panel
- `.kn-brand-tag` — small tag/chip for preferred brand codes
- Mobile: `order: 2.5` (between available-songs at 2 and download at 3)

### 7. Write tests
- **Unit tests** (`tests/unit/test_karaoke_nerds.py`):
  - Test HTML parsing with fixture HTML (captured from real response)
  - Test community track detection
  - Test YouTube URL cleanup (strip list param)
  - Test empty results / no matches
  - Test malformed HTML handling (graceful degradation)
  - Test with songs that have only 1 track
- **Integration tests** (add to `tests/integration/test_routes.py`):
  - Test `POST /karaoke-nerds/search` with mocked `karaoke_nerds.search()`
  - Test query validation (missing, too short)
  - Test `GET /karaoke-nerds/config` returns preferred brands
  - Test `POST /karaoke-nerds/config` saves and returns updated config

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `kj-controller/karaoke_nerds.py` | **Create** | Server-side scraper for karaokenerds.com |
| `kj-controller/config.py` | Modify | Add `kn_preferred_brands` default |
| `kj-controller/routes.py` | Modify | Add 3 routes: search, get config, set config |
| `kj-controller/templates/index.html` | Modify | Add KN search section HTML + preferred brands in KJ_CONFIG |
| `kj-controller/static/app.js` | Modify | Add search/render/download/prefs functions |
| `kj-controller/static/style.css` | Modify | Add KN-specific styles with tier highlighting |
| `kj-controller/requirements.txt` | Modify | Add `beautifulsoup4` |
| `kj-controller/tests/unit/test_karaoke_nerds.py` | **Create** | Unit tests for HTML parser |
| `kj-controller/tests/integration/test_routes.py` | Modify | Add KN route integration tests |

## Testing Strategy

- **Unit tests**: Parse fixture HTML, verify song/track extraction, community detection, URL cleanup
- **Integration tests**: Routes return correct JSON, validate input, handle scraper errors, config CRUD
- **Manual testing**: Search for popular songs on device, verify results match karaokenerds.com, test download flow, test preferred brands persistence across restarts

## Open Questions

None — all clarified during research phase.

## Rollback Plan

- All changes are additive (new module, new routes, new UI section, new config key)
- No existing functionality is modified
- Revert the commit to remove entirely
