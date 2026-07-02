# SSD Commercial Library → `media_library`: canonical identity + play stats

**Date:** 2026-07-02 (rev 2 — incorporates Andrew's review: content-hash ids,
rotation-scoped bulk import, `/opt/nomad/data` relocation)
**Status:** REVISED — awaiting Andrew's re-approval

## Problem

The 4TB SSD commercial-disc library (`/media/nomad/Nomad4TBOne`, 3.2 TB used) is
searchable via the external catalog (`external_media.db`, ~415K rows built from
`external_file_list`) and heavily played (rotation history references ~400
distinct SSD files), but it is **not** in `media_folders`, so its files have no
`media_id` and no `media_library` row. Consequences:

- **Play stats (v0.55.0) don't cover SSD tracks.** `resolve_row_media_id`
  resolves local rows via `media_library.get_by_path()` with a `[media_id]`
  filename-token fallback — SSD paths have neither, so `/play` records nothing
  and the backfill skipped ~482 historic Done rows (~half of real plays).
- SSD tracks can show raw filenames in rotation instead of canonical
  `Artist - Title`.

Device measurements (2026-07-02, read-only): 381 distinct `/media/nomad/…`
file_paths in `rotation_archive` alone (380 still exist, 1 missing), totalling
only **2.9 GB** — mostly small CDG zips. A cold sample read hashed at ~5 MB/s,
so hashing the historic set is a minutes-scale one-off, but large MP4s make
inline hashing on the `/play` request path unacceptable.

## Goals

1. Every touched SSD/catalog file gets a stable `media_id` that survives
   **renames and moves** on the SSD (Andrew's requirement).
2. `/play`, `/preview`, and search-row stats badges cover SSD tracks; the
   historic backfill attributes the previously-skipped SSD plays.
3. Canonical `Artist - Title` display for SSD tracks in rotation, sourced from
   the catalog's parsed metadata (with `media_library` overrides once a row
   exists, editable via the existing ✎ flow).
4. `media_library.db` relocated off the repo dir (reimage durability) before any
   growth.

## Non-goals (revisit later)

- Bulk-importing the whole ~415K catalog now. A future **deliberate multi-day
  batch process** hashing the full 3.2 TB stays open (Andrew may want it to
  unblock other opportunities) and produces *identical* ids because ids are
  content-derived — no migration when that day comes.
- Available Songs browsing of the whole SSD library.
- "We own this" dedup hints in KN/download flows.
- Refreshing the 2025-02-28 `external_file_list` (orthogonal; see D7).
- Moving/renaming any SSD file. The library is curated; identity is attached
  in-place.

## Design decisions

### D1. `media_id` scheme: `lib-<content_hash>` (sha1 of file bytes, first 12 hex)

New source `library` (prefix `lib`) in `naming.py`, reusing the existing
`naming.content_hash(path)` (full-file sha1, `[:12]`) already used as primary
identity for keyless uploads (`up-…`, P1 foundation plan).

- **Why content hash, not path:** path-derived ids (rejected rev-1 proposal)
  would break stats/rotation identity on any SSD rename/move and force a
  migration. Content hashes survive arbitrary reorganisation; after a move, the
  next touch re-hashes and lands on the **same id**, and the row's `file_path`
  is simply refreshed.
- **Why not `disc-<discid>-<track>`:** `disc_id` comes from a heuristic
  filename split — missing/ambiguous for many rows, not collision-safe. It
  stays available as parse metadata, not the key.
- **Cost is bounded by D2's laziness:** hashing happens for a few hundred
  rotation-referenced files at import time (2.9 GB ≈ minutes) and then
  on-demand, one file per first-touch. Never on the search hot path (D3), and
  never inline in a request that a KJ is waiting on (hashing runs in a
  background thread; see D3).
- Duplicate content (identical file on two discs) → same id → merged stats.
  That's treated as correct ("same version"); `file_path` reflects the most
  recently touched copy.
- `source_ref` = the content hash; `raw_original_name` = filename at first
  touch. Hash failure (unreadable file) → no id, skip recording — same
  behaviour as today.

### D2. Lazy materialization + one-off rotation-scoped import

`media_library` gains rows for SSD tracks **on touch** (link, play, preview,
note edit) — plus a **one-off bulk import of every SSD track referenced by
rotation history** (~400 distinct paths across `rotation_entries` +
`rotation_archive`), so the play-stats backfill has ids to attribute against.

- `scripts/import_rotation_ssd_tracks.py` (dry-run default): enumerate distinct
  `file_path LIKE '<mount>/%'` from both rotation tables, hash each existing
  file, materialize rows with artist/title from the catalog (by-path lookup,
  NFC/NFD-tolerant; fallback: deterministic `parse_karaoke_filename` on the
  filename, `needs_review=1`), and report missing files (1 known) as skipped.
- No whole-catalog import now (415K rows whose only reader would be a by-path
  lookup); the future full-library batch (see Non-goals) is compatible.
- Materialized rows: `source='library'`, `parse_method='catalog'` (or
  `'deterministic'` on catalog miss), `needs_review=0` from catalog,
  `file_path` = current path, `ext` from filename. Materialization is
  upsert-if-absent for identity fields (mirroring `upsert_scanned`): an
  existing row — including one manually edited via ✎ — is never clobbered;
  only `file_path`/`ext` refresh on re-touch (this is also what heals paths
  after an SSD reorganisation).

### D3. Resolution seams (all best-effort, never raise)

New helpers:

```
naming.SOURCE_LIBRARY = "library"          # media_id_for prefix "lib"
catalog.get_by_path(path) -> row | None    # exact, then NFC/NFD variants
```

Integration points:

1. **Search-row stats badges** (`resolve_row_media_id` local branch): resolved
   via `media_library.get_by_path()` **only** — no hashing on the search hot
   path. A never-touched SSD track has no stats to display by definition, so a
   missing row is the correct answer. (This is the one place rev-1's pure
   path-derivation had an edge; content-hash ids trade it away deliberately.)
2. **`_record_play_stat` / `_record_preview_stat`**: on `get_by_path` miss for
   a path under `external_media_mount`, spawn a daemon thread that hashes the
   file, materializes the row (catalog artist/title), and records the event.
   The `/play` response never waits on a hash (cold large MP4s can take
   seconds). The existing per-rotation-entry dedup index applies at insert
   time, so delayed inserts stay idempotent.
3. **`link_rotation_file`**: after a successful link of an SSD path, same
   background materialization (so the row, canonical display, and note editor
   exist before the first play).
4. **`scripts/backfill_play_stats.py`**: resolve via `get_by_path` (rows now
   exist thanks to the D2 import — run the import first). No hashing inside
   backfill itself; unresolved paths report as skipped, as today.

### D4. Rotation display enrichment

`_decorate_rotation_entries` attaches `media_meta = {artist, title}` for linked
entries, resolved `media_library.get_by_path` → catalog fallback. The frontend
uses it wherever it currently falls back to the linked file's raw basename.
(Entries linked via the search UI already get decent `song_artist` text from
the catalog row; this covers entries created from singer requests and
pre-existing archive rows.) Exact frontend touchpoints to be confirmed during
implementation — display-only, additive field.

### D5. `media_db_path` relocation → `/opt/nomad/data/media_library.db`

Everything kjbox lives under `/opt/nomad/`; the repo dir is the wrong home for
a database (reimage/reclone loses it). New `/opt/nomad/data/` directory (owned
`nomad`, outside the repo, outside `media_folders`/downloads so it can never be
scanned) holds `media_library.db`.

**What still uses `~/kjdata/` (device-verified 2026-07-02):** it is *live*, not
legacy — `rotation.db` (+active WAL) lives there via the `rotation_db_path`
default (device config.json has no override), and `flask_secret`,
`rotation-sa-key.json` (config points at it), and `rotation-bg.png` (wallpaper
restore) are all read from there. The rest is leftovers (old `videos/` download
folder, old filler mp3s, superseded cookie files). **Migrating those four to
`/opt/nomad/data/` is a sensible follow-up tidy-up** (needs `rotation_db_path`,
`flask_secret_key_path`, `rotation_credentials_file` config keys + off-show file
moves) but is out of scope here — this project moves only `media_library.db`.

Runbook (off-show; service restart interrupts playback):

1. `sudo systemctl stop kj-controller`
2. `sudo mkdir -p /opt/nomad/data && sudo chown nomad:nomad /opt/nomad/data`
3. `sqlite3 /opt/nomad/kjbox/kj-controller/media_library.db "PRAGMA wal_checkpoint(TRUNCATE);"`
   then copy `media_library.db` → `/opt/nomad/data/media_library.db`
   (never copy a live WAL DB — service is stopped) and
   `sudo chown nomad:nomad /opt/nomad/data/media_library.db` (a sudo `cp`
   leaves it root-owned and the service can't reopen it).
4. `config.json`: set `media_db_path=/opt/nomad/data/media_library.db`
   (`external_media_mount` already set; `master_sync_source`/`dest` explicit —
   loose end #2 — can ride along, coordinating with the parallel master-sync
   session).
5. Start service; verify row counts (≥2565 media_library rows, play_events
   intact); rename the old repo-dir DB to `.bak-<date>`.

### D6. Error handling

Catalog missing/closed, mount unset, hash failure, or any lookup failure →
behave exactly as today (no id, no recording, no enrichment). Background
materialization threads are fault-isolated (log-and-drop). No new failure can
reach the live playback path — same contract as the v0.55.0 stats work.

### D7. Freshness (out of scope, noted)

The catalog reflects the 2025-02-28 file list. SSD files added since are
invisible to search today and stay invisible to stats — consistent, not a
regression. Regenerating the list + `POST /catalog/build` is an independent
operational task. Content-hash ids are unaffected by list rebuilds.

## Testing

- Unit: `library` source + `lib-` prefix in `media_id_for`; catalog
  `get_by_path` NFC/NFD variants; resolver fallback order (row wins over token;
  no hashing in search enrichment); materialize-on-play/link including
  "existing manual row is not clobbered" and "file_path refreshes after a
  simulated move (same content, new path, same id)"; backfill attribution with
  materialized rows; import script dry-run/execute with tmp files + catalog
  fixture (including a missing-file skip).
- Threaded-materialization test: record path completes without the request
  thread blocking (synchronous test seam — thread runner injectable).
- Integration: `/play` on an SSD path materializes + records; search enrichment
  returns badges once the row exists.
- kjbox has no pytest CI — run locally via `rtk proxy python -m pytest`.

## Rollout

1. PR (kjbox only; no gen changes) → review → squash-merge.
2. Deploy off-show (backend change → restart required).
3. Device, in order: D5 relocation runbook → `import_rotation_ssd_tracks.py`
   dry-run → Andrew reviews report → `--execute` (hashes ~2.9 GB, minutes) →
   play-stats backfill dry-run → `--execute` → verify stats UI (leaderboards
   now include commercial-disc plays; badges on SSD search rows).

## Resolved review points (rev 2)

- ~~Path-hash ids~~ → **content-hash ids** (`naming.content_hash`, matching the
  `up-` precedent) so SSD files can be renamed/moved freely.
- ~~No bulk import at all~~ → **rotation-scoped import (~400 files)** now;
  whole-library multi-day batch remains a compatible future option.
- ~~Relocate to `~/kjdata/`~~ → **`/opt/nomad/data/`**; `~/kjdata` documented
  as still-live for rotation.db/flask_secret/SA-key/wallpaper, with its
  migration noted as a separate tidy-up.
