# Design: promote "Song Stats" from a modal into a dedicated, explorable section

**Date:** 2026-07-02
**Repo:** kjbox (`kj-controller/`)
**Worktree/branch:** `kjbox-song-stats-section` / `feat/sess-20260702-1718-song-stats-section`
**Builds on:** play/preview stats + version notes (v0.55.0, PR #141) and the SSD-library
extension (v0.58.0, PRs #143/#145/#146) — see memory `project_kjbox_play_stats_version_notes`,
`project_kjbox_ssd_library_design`.

## Problem

The play/preview-stats feature currently surfaces its leaderboard in a cramped **modal**
launched from a "Song Stats" button on the Rotation panel header (`loadStats` →
`/stats/top-songs` + `/stats/singers`). Andrew wants it promoted into a **dedicated,
always-present "Song Stats" section below the Library section**, with multiple views,
usable filters, and expandable/clickable drill-downs (e.g. click a singer → every song
they've sung and when). It must stay **short by default — internally scrollable**, never
dominating the page.

## Non-goals / constraints

- **Read-only, non-disruptive.** All new endpoints are `GET`s over `play_events` (+ a couple
  over `preview_events` are *not* needed). Nothing touches live playback. A `StatsStore`
  error surfacing as a 500 on a `/stats/*` endpoint is acceptable (not the live path), but
  keep the existing **empty-on-unavailable** graceful shape.
- **No new frameworks / build step.** Vanilla JS, Jinja template, `style.css` — same as today.
- **Follow the `StatsStore` idiom exactly:** per-thread connection + WAL, **never nest
  `with self._lock()`** (the memory lock is non-reentrant; call helpers only after releasing
  the lock, like `upsert_note` does), parameterize every user value, clamp limits
  `max(1, min(int, CAP))`, additive `PRAGMA table_info` migrations.
- kjbox has **no pytest CI** (only `security.yml`); tests are local-only. Run with
  `rtk proxy python -m pytest <file> -v` (plain pytest output is mangled by a repo shell hook).

## Confirmed design decisions

Three points were genuinely open; resolved on best judgment (Andrew away), all handoff-aligned:

1. **Extra stats:** include a small set of fun extras beyond the four core views —
   *Most repeated* (same singer sang same song N×), *Busiest night ever*, and *per-singer
   variety* (distinct songs ÷ total plays). Cheap `GROUP BY`s; high delight, low clutter.
2. **Default state:** the section is **always visible**; the overview strip + view switcher +
   filter row are always shown, and only the **list area is bounded (`max-height: ~40vh`,
   `overflow-y: auto`)** — matching the verbatim ask "always-present … not super tall by
   default, maybe internally scrollable." (Not collapsed-by-default.)
3. **Singer filter scope:** the singer autocomplete narrows **Top Songs only**. Top
   Singers / Artists / Nights stay global. Drilling *into* a singer row still shows that
   singer's full history, so nothing is lost.

## Backend

### New `StatsStore` methods (all read-only, in `kj-controller/stats_store.py`)

| Method | Returns |
|---|---|
| `top_artists(*, since=None, limit=25)` | `[{artist, plays, distinct_songs}]`, grouped by normalized artist |
| `singer_songs(singer, *, since=None, limit=100)` | `[{song_key, artist, title, plays, first_sung, last_sung}]` for one singer, plays desc |
| `singer_song_history(singer, song_key, *, limit=200)` | `[{played_at, night_date}]` — the actual timestamps a singer sang a song |
| `song_history(song_key, *, since=None, limit=200)` | `[{singer, played_at, night_date, media_id}]` — who sang a song & when |
| `overview(*, since=None)` | `{total_plays, distinct_songs, distinct_singers, distinct_artists, first_played, last_played, plays_last_30d}` |
| `busiest_nights(*, limit=20)` | `[{night_date, plays, distinct_singers, distinct_songs}]`, plays desc |
| `artist_songs(artist, *, since=None, limit=100)` | `[{song_key, artist, title, plays, distinct_singers}]` for one normalized artist, plays desc — powers the Top Artists drill-down |
| `night_setlist(night_date, *, limit=200)` | `[{played_at, singer, artist, title, song_key, media_id}]` — Nights drill-down (extra) |
| `most_repeated(*, since=None, limit=10)` | `[{singer, song_key, artist, title, plays}]` — singer+song combos, count desc (extra) |

All group/order over `play_events`. `since` filters `played_at >= ?`. Song identity is
`song_key`; singer identity is `singer_norm`; artist identity is `artist_norm` (new — see below).
Display columns use `MAX(artist)` / `MAX(title)` to pick a representative label within a group,
mirroring the existing `top_songs`/`top_singers` methods.

### `artist_norm` migration

`play_events` has `artist` but no normalized column, so `top_artists` can't group cleanly
(e.g. case/whitespace variants would split). Add `artist_norm` mirroring `singer_norm`:

1. Additive migration in `init_schema` (or a dedicated `_migrate` helper): `PRAGMA
   table_info(play_events)` → if `artist_norm` absent, `ALTER TABLE play_events ADD COLUMN
   artist_norm TEXT`, then one-time `UPDATE play_events SET artist_norm = <normalized artist>`
   for existing rows. Copy the migration idiom used by `RotationStore`.
2. Add a `_norm_artist(s)` helper (whitespace-collapse + lower, same as `_norm_singer`).
3. Populate `artist_norm` in `record_play` going forward (add to the INSERT column list +
   value). Add a `CREATE INDEX IF NOT EXISTS idx_play_events_artist ON play_events(artist_norm)`.
4. **Scope note (explicit to avoid ambiguity):** `artist_norm` lives on `play_events` **only** —
   that is `top_artists`'s sole source. `record_preview` writes to the separate `preview_events`
   table, which no artists view reads, so it is left untouched. The handoff's "populate in
   record_play/record_preview" is satisfied by populating the one table that has the column.

### Derived (no new endpoint)

- **Per-singer variety** — computed client-side from a `top_singers` row: `distinct_songs / plays`.
- **Busiest night ever** — just row 1 of the Nights view (already ordered plays desc); shown
  with a subtle "🔥 busiest" badge, no separate query.

### New routes (in `kj-controller/routes.py`, beside the existing `/stats/*`)

All read-only `GET`, `getattr(current_app, 'stats', None)` → empty shape if unavailable,
`limit` clamped `max(1, min(int, CAP))`, `since`/`singer`/`song_key`/`night_date` as **query
params** (names contain spaces/punctuation — never path params):

- `GET /stats/top-artists?since=&limit=` → `{"artists": [...]}`
- `GET /stats/artist-songs?artist=&since=&limit=` → `{"songs": [...]}`
- `GET /stats/singer-songs?singer=&since=&limit=` → `{"songs": [...]}`
- `GET /stats/singer-song-history?singer=&song_key=&limit=` → `{"history": [...]}`
- `GET /stats/song-history?song_key=&since=&limit=` → `{"history": [...]}`
- `GET /stats/overview?since=` → `{"overview": {...}}`
- `GET /stats/nights?limit=` → `{"nights": [...]}`
- `GET /stats/night-setlist?night_date=&limit=` → `{"setlist": [...]}`
- `GET /stats/most-repeated?since=&limit=` → `{"repeated": [...]}`

Existing `/stats/top-songs` and `/stats/singers` are reused unchanged (they already power the
Top Songs / Top Singers lists and the singer autocomplete).

## Frontend — dedicated `#song-stats` section below Library

### Markup (in `templates/index.html`, immediately after the Library `.container` closes)

Matches the existing section pattern (`<div class="container …">` + `.header-row` with an
`<h2>` and a `.header-actions` button group — the PR #144 framework auto-sizes a classless
`<button>`).

```
<div class="container song-stats" id="song-stats">
  <div class="header-row">
    <h2>Song Stats</h2>
    <div class="header-actions"><button onclick="refreshSongStats()">Refresh</button></div>
  </div>
  <div id="statsOverview" class="stats-overview"></div>           <!-- headline cards -->
  <div class="stats-filters">
    <div class="stats-range" id="statsRange">…preset buttons…</div>
    <input type="date" id="statsSince" class="hidden">            <!-- custom since -->
    <input type="text" id="statsSingerFilter" list="statsSingerList"
           placeholder="Filter Top Songs by singer…">
    <datalist id="statsSingerList"></datalist>
  </div>
  <div class="stats-seg" id="statsViewSwitch" role="group">       <!-- segmented pill -->
    <button class="stats-seg-btn stats-seg-active" data-view="top-songs">Top Songs</button>
    <button class="stats-seg-btn" data-view="top-singers">Top Singers</button>
    <button class="stats-seg-btn" data-view="top-artists">Top Artists</button>
    <button class="stats-seg-btn" data-view="nights">Nights</button>
  </div>
  <div id="statsBody" class="stats-body"></div>                   <!-- bounded scroll list -->
</div>
```

`.stats-seg` is a lightweight parallel of the existing `.mode-segmented` pill (styled the
same, but without the Simple/Advanced-specific colour coupling). `.stats-body` gets
`max-height: 40vh; overflow-y: auto`.

### Behaviour (in `static/app.js`)

Remove the modal entirely: delete `rotation-stats-btn` (index.html:88), the `#stats-modal`
block, and `openStatsModal`/`closeStatsModal`. Rework `loadStats` into the new loaders.

State: `songStats = { view: 'top-songs', since: null, singer: '', cache: {}, loaded: false }`.

- **Lazy first load:** an `IntersectionObserver` on `#song-stats` fires once, when the section
  first scrolls near the viewport → fetch `/stats/overview` (cards) + the default view
  (`/stats/top-songs`) only. Nothing else fetches on page load.
- **View switch** (`switchStatsView`): toggle active pill, render from `cache` or fetch the
  view's endpoint on first open (`top-singers`→`/stats/singers`+`/stats/most-repeated` for the
  fun-fact line, `top-artists`→`/stats/top-artists`, `nights`→`/stats/nights`). Cache per
  `view|since` (+`singer` for top-songs).
- **Drill-downs** — inline expand a `.stats-drill` div under the clicked row, fetch on first
  expand, cache:
  - Top Songs row → `/stats/song-history?song_key=` → who sang it & when; mark the ⭐ "usually
    played" version (via the already-computed usual media id where available).
  - Top Singers row → `/stats/singer-songs?singer=` → their songs (plays, first/last sung) +
    a variety figure in the row header; clicking a song → `/stats/singer-song-history` → dates.
  - Top Artists row → `/stats/artist-songs?artist=` → that artist's top songs (see "Artist
    drill-down" below for why this gets its own endpoint).
  - Nights row → `/stats/night-setlist?night_date=` → that night's songs + singers.
- **Filters:** range presets (All time / This year / Last 30 days / Custom) set `since` and
  reload the overview + current view (clearing cache for the new `since`). The singer filter
  (`change` event) reloads **Top Songs only**.
- **Refresh** button clears `cache`, re-fetches overview + current view.
- **Escaping:** `escHtml` in element content, `escAttr` in attributes. Any nested clickable
  inside a row that also has a row-level `onclick` calls `event.stopPropagation()`.
- **Fun facts placement (respects lazy-load):** *Busiest night* = row 1 of the Nights view
  (badge). *Most repeated* = a one-line callout at the top of the Top Singers view, fetched
  alongside `/stats/singers` when that view first opens. *Variety* = in each singer's expanded
  header. No always-on extra fetches.

### Artist drill-down

"Top Artists row → the artist's top songs" needs an artist-scoped list, which no existing
endpoint provides. Rather than hack it client-side, add one small method/route that mirrors
`singer_songs`, keeping every drill-down a clean single query:

- **`artist_songs(artist, *, since=None, limit=100)`** → `[{song_key, artist, title, plays,
  distinct_singers}]` for one normalized (`artist_norm`) artist, plays desc. Route
  `GET /stats/artist-songs?artist=&since=&limit=` → `{"songs": [...]}`.

### Cache-bust

Bump `pyproject.toml` version so `app.js?v=` changes (`APP_VERSION` is read at startup; the
frontend is served with the version query string).

## Testing

- **Unit (`tests/unit/test_stats_store.py`):** TDD each new method against a `:memory:`
  `StatsStore` — seed `record_play` rows, assert ordering, `since` filtering, limit clamping,
  empty results, `artist_norm` migration + backfill, and NULL/whitespace singer/artist handling.
- **Routes (`tests/unit/test_routes_stats.py`):** each new `/stats/*` endpoint via
  `flask_test_client` — happy path, empty-on-unavailable (no `current_app.stats`), limit clamp,
  missing required param (e.g. `singer-songs` with no `singer` → empty/400 per existing idiom).
- **Frontend:** `node --check static/app.js` + a local mock-Flask Playwright harness (renders
  the real template + static, stubs the `/stats/*` endpoints with fixture JSON) to exercise
  view switching, drill-down expand, filters, and the bounded-scroll layout — the way prior
  kjbox frontend PRs were validated (there is no JS test harness in-repo). Then eyeball against
  the device's ~786 real `play_events` rows read-only via `ssh nomadpctunnel`.
- Run backend tests with `rtk proxy python -m pytest tests/unit/test_stats_store.py
  tests/unit/test_routes_stats.py -v`.

## Deploy (later, with Andrew, off-show)

Frontend changes (JS/CSS/HTML) take effect on browser refresh after auto-deploy pulls, but the
**`artist_norm` migration + new routes are backend** and require a `kj-controller` restart —
which interrupts live playback. So this ships in one PR (`@coderabbitai ignore`) and deploys
**off-show only, with explicit permission**, per kjbox production-safety rules. The migration
is additive and idempotent; the one-time `artist_norm` backfill runs at first schema init after
deploy.

## Rollout summary

1. Backend: `artist_norm` migration + `_norm_artist`, then the 9 read methods + routes (TDD).
2. Frontend: remove modal, add `#song-stats` section + `.stats-seg`/`.stats-body` CSS + the
   new JS loaders/drill-downs, bump version.
3. `node --check`, mock-harness Playwright pass, backend pytest green.
4. CodeRabbit (`coderabbit review --agent --type committed --base origin/main`) → fix → PR.
5. Merge + off-show deploy with Andrew; verify against real rows.
