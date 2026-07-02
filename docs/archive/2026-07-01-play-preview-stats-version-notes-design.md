# Play/Preview Stats + Version Notes + Singer Leaderboards — Design

**Date:** 2026-07-01
**Repo:** kjbox (`kj-controller/`)
**Status:** Design — approved decisions captured; pending spec review → plan.
**Builds on:** Phase 1 of the download-naming initiative (v0.51.1, PRs #130+#131) — the
`media_library` table + stable `media_id` are the foundation this feature keys off.

## 1. Goal

Start recording, forever and library-wide, **how many times each version of each song has
been played and previewed** on kjbox, plus let the KJ attach **notes** to specific versions.
Surface this so that when linking a version in rotation search, the KJ can see "▶ 12 — that's
the version I usually play" and "📝 censored version — someone asked for it once". Also make the
data queryable: "top 10 songs ever" and "top 10 songs sung by Celeste".

We wish this had existed 9 months ago; it did not — the only recoverable history is ~3.3 months
(see §7). So the priority is to **start accruing clean data now** and backfill what exists.

## 2. Non-goals

- Not a general analytics warehouse. SQLite on-device, simple aggregate queries.
- Not fuzzy singer identity resolution — singer names are matched by normalized exact string;
  two different people named "Celeste" merge. Accepted (aliasing is a future add).
- Not off-device durability in this iteration (see §13 follow-up).
- Not the Available-Songs editing UX — that belongs to the naming initiative's Phase 3.

## 3. Identity model

Three keys, all already used elsewhere in the codebase:

| Key | Meaning | Grain | Source |
|---|---|---|---|
| `media_id` | one concrete version/file | per-version | `media_library` PK (`yt-<vid>`, `db-<brand>-<ref>`, `gen-<job8>`, `nomad-<disc#>`, `up-<hash>`) |
| `song_key` | logical song | per-song (all versions) | `routes._normalize_song_key(artist, title)` |
| `singer_norm` | a singer | per-singer | `lower(trim(singer))` from the rotation entry |

- **Play/preview counts** and the **⭐ "usually play" badge** are keyed on `media_id` (per-version).
- **Leaderboards** ("top songs") are keyed on `song_key` (logical song), so multiple versions of
  the same song don't split the vote.
- **Singer leaderboards** filter on `singer_norm`.

`media_id` is stored as plain `TEXT` with **no foreign key** to `media_library`, because we must
record events/notes for versions that have no `media_library` row (e.g. a YouTube version noted
but never downloaded). `artist`/`title` (and `song_key`) are **denormalized onto every row** so a
stat or note is self-describing even without a `media_library` join.

## 4. Storage

New module `kj-controller/stats_store.py` → `StatsStore(db_path)`, mirroring
`MediaLibraryStore`/`RotationStore`: per-thread `sqlite3` connections via `threading.local()`,
WAL, `busy_timeout`, a `:memory:` special case for tests, and `init_schema()` using
`CREATE TABLE IF NOT EXISTS` + the `PRAGMA table_info(...)` additive-migration loop (no
`user_version`).

Instantiated in `app.py` alongside the others, pointed at the **same file** as `media_library`:

```python
flask_app.stats = StatsStore(cfg.get('media_db_path'))   # same file as media_library.db
```

Why the same file as `media_library.db` (not `rotation.db`): the stats key off `media_id`, which
is that DB's primary key; `media_library.db` is library-wide and never wiped (unlike
`rotation_entries`, which `archive()` clears nightly); and sharing a DB file across sibling store
modules is the established convention (`sing_store.py`/`sms_store.py` share `rotation.db`). WAL +
`busy_timeout` make concurrent access from `MediaLibraryStore` and `StatsStore` safe.

### 4.1 Schema

```sql
-- One row per counted play. Append-only.
CREATE TABLE IF NOT EXISTS play_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id    TEXT NOT NULL,
    song_key    TEXT,
    singer      TEXT,             -- as displayed
    singer_norm TEXT,             -- lower(trim(singer)) for grouping
    played_at   TEXT NOT NULL,    -- ISO8601 (live: now; backfill: archived night_date)
    night_date  TEXT,             -- date('now','localtime') at record time / archived night
    entry_id    INTEGER,          -- rotation entry played (NULL if not from a rotation entry)
    source      TEXT NOT NULL DEFAULT 'live',   -- 'live' | 'backfill'
    artist      TEXT,
    title       TEXT
);
CREATE INDEX IF NOT EXISTS idx_play_events_media   ON play_events(media_id);
CREATE INDEX IF NOT EXISTS idx_play_events_song    ON play_events(song_key);
CREATE INDEX IF NOT EXISTS idx_play_events_singer  ON play_events(singer_norm);
-- Dedup: at most one LIVE play per rotation entry (re-press / seek-restart = no double count).
-- Partial index excludes NULL entry_ids and backfill rows.
CREATE UNIQUE INDEX IF NOT EXISTS idx_play_events_entry
    ON play_events(entry_id) WHERE entry_id IS NOT NULL AND source='live';

-- One row per counted preview. Append-only.
CREATE TABLE IF NOT EXISTS preview_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id     TEXT NOT NULL,
    song_key     TEXT,
    previewed_at TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'live',
    artist       TEXT,
    title        TEXT
);
CREATE INDEX IF NOT EXISTS idx_preview_events_media ON preview_events(media_id);

-- One editable note per version.
CREATE TABLE IF NOT EXISTS version_notes (
    media_id   TEXT PRIMARY KEY,
    note       TEXT,
    label      TEXT,             -- short tag, e.g. 'censored', 'video-bg'
    artist     TEXT,             -- denormalized context
    title      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 4.2 Store API (public methods)

- `record_play(media_id, *, entry_id=None, singer=None, artist=None, title=None, song_key=None, played_at=None, night_date=None, source='live')` — `INSERT OR IGNORE` (the partial unique index enforces per-entry dedup). If `entry_id is None` and `source='live'`, apply a short same-`media_id` time-window guard (default 120 s) so replays from Available Songs don't double count.
- `record_preview(media_id, *, artist=None, title=None, song_key=None, source='live')` — 60 s same-`media_id` dedup window (preview.js can re-resolve).
- `stats_for(media_ids)` → `{media_id: {plays, previews, last_played}}` — one grouped query each over `play_events`/`preview_events` for the given set (used by search enrichment).
- `get_note(media_id)` / `upsert_note(media_id, note, label, artist=None, title=None)` / `distinct_labels()`.
- `top_songs(*, singer=None, since=None, limit=10)` → `[{song_key, artist, title, plays, singers?}]` grouped by `song_key`, optional `singer_norm` filter and `played_at >= since`.
- `top_singers(*, since=None, limit=10)` → `[{singer, plays, distinct_songs}]` grouped by `singer_norm`.
- `usual_media_id(media_ids)` → the `media_id` with the most plays within a given set, or `None` if all zero (used for the ⭐ badge within a search song-group).

All reads/writes are wrapped by callers in try/except (see §11).

## 5. Recording

### 5.1 Plays — `POST /play`

`static/app.js` `playMedia()` already originates from a rotation entry, so thread the entry's id:
`apiCall('/play', { file_path, entry_id })`. In the route (after playback is successfully
started, so a stats failure can never block playing):

1. `media_id = current_app.media_library.get_by_path(file_path)` (reliable post-scan; scan()
   derives `media_id` for every library file incl. masters). If unscanned, fall back to
   `naming.extract_media_id(basename)` (slug files embed `[media_id]`), else
   `parse_identity`→`media_id_for` **only when `source_ref` is non-null** (never fabricate
   `up-None` for a keyless, unscanned upload). If still unresolved → skip recording (log DEBUG).
2. `singer` / `artist` / `title` from the rotation entry (`rotation_store`) via `entry_id`; if no
   `entry_id`, from the `media_library` row.
3. `song_key = routes._normalize_song_key(artist, title)`.
4. `current_app.stats.record_play(media_id, entry_id=entry_id, singer=singer, artist=artist, title=title, song_key=song_key)`.

Dedup: the partial unique index means re-`/play` on the same entry is a no-op; two different
rotation entries for the same version each count.

### 5.2 Previews — `POST /preview/resolve`

After `PreviewManager.resolve(descriptor)` succeeds, compute `media_id` from the descriptor:

- `local` → `media_library.get_by_path(descriptor['file_path'])`
- `youtube` → `yt-<11-char-id>` parsed from `descriptor['youtube_url']`
- `divebar` → resolve via the cached download path if present (`get_by_path`), else the
  `media_library` (brand, song_key) match (§10); else skip.

Then `current_app.stats.record_preview(...)` with denormalized artist/title/song_key from the
descriptor's title metadata. 60 s dedup window.

## 6. Historic backfill

`kj-controller/scripts/backfill_play_stats.py` (run on device, off-show):

1. Open `~/kjdata/rotation.db` (read-only) and `media_library.db`.
2. `SELECT ... FROM rotation_archive WHERE status='Done'` (~880 rows, 2026-03-21 → 2026-06-25).
3. For each row: `media_id = media_library.get_by_path(file_path)` (P1 already repointed the
   archive `file_path`s to `/opt/nomad/downloads/…`, so ~88% resolve). Fall back to
   `extract_media_id`/`parse_identity` on the basename; if still unresolved, log & skip
   (counted in the summary).
4. Insert `play_event(source='backfill', played_at=night_date, night_date=night_date,
   singer=<archived singer>, artist/title/song_key from song_artist)`.

**Idempotent:** `--dry-run` prints an attribution report (attributed / skipped counts, top songs,
top singers) for approval; `--execute` runs `DELETE FROM play_events WHERE source='backfill'`
then re-inserts, so re-runs are safe. Backfill rows carry no `entry_id` (archive doesn't preserve
it) → they bypass the live per-entry unique index.

## 7. Display on rotation-search rows (headline use case)

`/rotation/search` enriches each version row with a `stats` block:

```json
"stats": { "plays": 12, "previews": 3, "last_played": "2026-06-25", "is_usual": true,
            "note": "censored version", "label": "censored" }
```

- Resolve each row → `media_id` via `resolve_row_media_id(row)` (§10), collect the set, one
  `stats.stats_for(ids)` + one `distinct notes` query, and `usual_media_id()` **within each song
  group** to set `is_usual` on the top-played version (only when plays > 0).

In `static/app.js` row rendering (additive, self-contained helper — see §13 coordination):

- `▶ N` play count per version row; dim `· 👁 N` previews secondary.
- `⭐` badge on the `is_usual` version — the derived "that's the version I usually play".
- `📝 <label>` chip + note text inline when present; a small pencil affordance opens a **modal**
  (not `prompt()`) → `POST /media/note`. Label field autocompletes from `GET /media/note-labels`.

## 8. Notes API + editor

- `POST /media/note` `{media_id, note, label, artist?, title?}` → `upsert_note`. Returns the saved note.
- `GET /media/note-labels` → `["censored", "video-bg", …]` distinct prior labels, for autocomplete.
- Read path for rows is folded into the search `stats` block; no separate per-row GET needed.
- Editor is a small modal reused from the existing modal patterns in `app.js` (consistent with the
  standing preference against `prompt()`), fields: note (multiline) + label (text w/ datalist).

## 9. Stats view (leaderboards)

Read-only, self-contained, non-conflicting with Phase 3.

- `GET /stats/top-songs?singer=&since=&limit=` → `top_songs(...)`.
- `GET /stats/singers?since=&limit=` → `top_singers(...)` (also feeds the singer autocomplete).

UI: a lightweight "Stats" panel/tab in the KJ UI:
- **Top songs** overall, with a date-range selector (all-time / this year / custom `since`).
- A **singer** search/filter (autocomplete from `/stats/singers`) → top songs for that singer.
- **Top singers** overall.
Each leaderboard row shows `Artist – Title — ▶ N`. A song row may expand to its per-version
breakdown (nice-to-have; deferred if it complicates the first cut).

## 10. Row → media_id resolution & Phase-2 interplay

`resolve_row_media_id(row)`:

- **local row** → `media_library.get_by_path(row.path)` → `media_id`. Exact, stable. ✓
- **youtube / KN row** → `yt-<vid>` from `row.youtube_url`. Exact, stable (matches the id scanned
  from downloaded `<vid>__…` filenames). ✓
- **divebar row** → the fuzzy case. Shipped `parse_identity` derives community ids as
  `db-<brand>-<hash8(filename-stem)>` (filename-derived, not Drive `file_id`), so a *pre-download*
  divebar row can't be matched to a played file by exact id today. Resolve instead by looking up
  `media_library` rows where `source='community'`, `source_ref` starts with the row's `brand`, and
  `(artist_norm, title_norm)` match, taking that row's `media_id`. If no match (never
  played/downloaded), show no count (it would be 0 anyway).

**Known limitation, honestly scoped:** YouTube and local rows get exact counts immediately;
divebar rows get counts via the best-effort (brand + song_key) match. Phase 2 of the naming
initiative formalizes download-time `media_id` (with the Drive `file_id` as `source_ref`), after
which divebar rows can be matched exactly via `db-<brand>-<file_id>`. This design tolerates that
change — `resolve_row_media_id` is the single place to tighten later.

## 11. Offline / failure posture

The device runs live shows; stats must never degrade core behavior:

- Every stats read/write in a route is wrapped in try/except, logged at WARNING, and swallowed.
- `/play` plays and `/preview` previews regardless of stats outcome; `/rotation/search` returns
  rows without the `stats` block if enrichment fails.
- `StatsStore` requires no network, LLM, or GCS — pure local SQLite. Matches the `media_library=None`
  back-compat P1 already established.

## 12. Testing

- `tests/unit/test_stats_store.py` (copy `test_sing_store.py` idiom: `:memory:` fixture +
  `tmp_path` migration-idempotency): per-entry play dedup, no-entry time-window dedup, preview
  dedup, note upsert + labels, `stats_for`, `top_songs` (overall + singer-filtered + `since`),
  `top_singers`, `usual_media_id`.
- Route tests: `/play` records + dedups (mocked store), `/preview/resolve` records,
  `/rotation/search` enrichment shape, `/media/note` upsert, `/stats/*` endpoints.
- `tests/unit/test_backfill_play_stats.py`: temp `rotation.db` archive + temp `media_library.db`,
  assert attribution counts and idempotent re-run.

## 13. Deployment & coordination

- **Backend** (routes + `stats_store.py` + `app.py` wiring): needs a service restart — deploy
  **off-show** only.
- **Frontend** (`app.js` row enrichment + note modal + Stats panel): bump `pyproject` version so
  `app.js?v=` cache-busts; takes effect on hard-refresh.
- **Backfill**: run `backfill_play_stats.py --dry-run` on device, review, then `--execute`, after deploy.
- **Phase-3 coordination:** the only overlap is `static/app.js` row rendering + `/rotation/search`
  enrichment. Build the count/note display as a self-contained render helper + a discrete
  enrichment block, and flag it in the PR + memory so the Phase-3 session rebases cleanly. The
  Stats panel and all `stats_store.py`/backfill/API code are fully disjoint from P3.
- **Phase-2 coordination:** `resolve_row_media_id` (§10) is the single seam to tighten once P2's
  download-time `media_id` (Drive `file_id`) lands.

## 14. Open follow-ups (out of scope)

- **Reimage durability.** Both `rotation.db` and `media_library.db` live under `~/kjdata`. If a
  full device **reimage** wipes that mount, stats + notes are lost. Confirm whether `~/kjdata` is a
  preserved mount (`readlink -f ~/kjdata; df -h ~/kjdata`); if not, add a periodic export
  (Sheet/GCS, following existing `rotation_sync.py` / divebar-BigQuery precedents). Tracked, not built here.
- **Fuzzy singer identity / aliasing** — merge "Celeste"/"Celest"/"Celeste B." Future.
- **Per-version drilldown** on leaderboard rows, and richer analytics (busiest nights, new vs
  returning songs). Future.

## 15. File-change map

New:
- `kj-controller/stats_store.py` — `StatsStore` (tables, recording, aggregates, notes).
- `kj-controller/scripts/backfill_play_stats.py` — one-off historic backfill.
- `tests/unit/test_stats_store.py`, `tests/unit/test_backfill_play_stats.py`.

Modified:
- `kj-controller/app.py` — instantiate `flask_app.stats`.
- `kj-controller/routes.py` — `/play` (record), `/preview/resolve` (record), `/rotation/search`
  (enrich), new `/media/note`, `/media/note-labels`, `/stats/top-songs`, `/stats/singers`;
  `resolve_row_media_id` helper.
- `kj-controller/static/app.js` — thread `entry_id` into `/play`; row stats/notes render helper +
  note modal; Stats panel.
- `kj-controller/static/style.css` — badges/chips + Stats panel styles.
- `kj-controller/templates/index.html` — Stats panel markup / tab.
- `pyproject.toml` — version bump.
