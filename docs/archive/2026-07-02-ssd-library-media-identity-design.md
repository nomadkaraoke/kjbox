# SSD Commercial Library → `media_library`: canonical identity + play stats

**Date:** 2026-07-02
**Status:** DRAFT — awaiting Andrew's review (written autonomously from the
download-naming follow-up handoff; assumptions to confirm are listed at the end)

## Problem

The 4TB SSD commercial-disc library (`/media/nomad/Nomad4TBOne`, 3.2 TB used) is
searchable via the external catalog (`external_media.db`, ~415K rows built from
`external_file_list`) and heavily played (466 rotation links: 38 active + 428
archive), but it is **not** in `media_folders`, so its files have no `media_id`
and no `media_library` row. Consequences:

- **Play stats (v0.55.0) don't cover SSD tracks.** `resolve_row_media_id`
  resolves local rows via `media_library.get_by_path()` with a `[media_id]`
  filename-token fallback — SSD paths have neither, so `/play` records nothing
  and the backfill skipped ~482 historic Done rows (~half of real plays).
- SSD tracks can show raw filenames in rotation instead of canonical
  `Artist - Title`.

## Goals

1. Every SSD/catalog file has a stable, deterministic `media_id`.
2. `/play`, `/preview`, and search-row stats badges cover SSD tracks; the
   historic backfill attributes the previously-skipped SSD plays.
3. Canonical `Artist - Title` display for SSD tracks in rotation, sourced from
   the catalog's parsed metadata (with `media_library` overrides once a row
   exists, editable via the existing ✎ flow).
4. `media_library.db` relocated off the repo dir (reimage durability) before any
   growth.

## Non-goals (YAGNI, revisit later)

- Bulk-importing all ~415K catalog rows into `media_library` (see decision D2).
- Available Songs browsing of the whole SSD library (the view lists the media
  index, which the SSD is deliberately not part of).
- "We own this" dedup hints in KN/download flows (search already surfaces SSD
  copies via the catalog; a dedicated hint is a separate feature).
- Refreshing the 2025-02-28 `external_file_list` (orthogonal; see D7).
- Moving/renaming any SSD file. The library is curated; identity is attached
  in-place.

## Design decisions

### D1. `media_id` scheme: `lib-<sha1(NFC(relpath))[:12]>`

New source `library` (prefix `lib`) in `naming.py`. The hash input is the
file's path **relative to the SSD mount** (`external_media_mount` config,
e.g. `/media/nomad/Nomad4TBOne`), NFC-normalized (the file list may carry NFD;
match `relink_references`' tolerance), exactly as stored in the catalog after
its own mount rebase.

- `source_ref` = the relpath (auditable derivation), `raw_original_name` = filename.
- Why not `disc-<discid>-<track>`: `disc_id` comes from a heuristic filename
  split — it's missing or ambiguous for many rows and not collision-safe.
  It stays available as display/parse metadata, just not as the key.
- Why not absolute-path hash: breaks if the SSD remounts elsewhere. Relative
  paths survive a remount (the catalog build already rebases mounts via
  `mount_replace`).
- Why not content hash: hashing 3.2 TB is infeasible.
- 12 hex chars (48 bits) matches the `up-` precedent; expected collisions over
  415K files ≈ 0.0003. A one-off audit (unit-testable helper + script) asserts
  zero collisions across the catalog before rollout.
- If `external_media_mount` is unset, derivation refuses (returns `None`) rather
  than silently hashing absolute paths; the rollout runbook sets the config key
  explicitly on the device.

### D2. Lazy materialization, not bulk import

`media_library` gains rows for SSD tracks **on touch** (link, play, preview,
note edit, backfill) — not via a 415K-row import.

Rationale: the id is pure (derivable from path + config), so every consumer
that needs ids for *display* (search-row stats badges) computes them without a
row existing; consumers that need *metadata* (record a play with artist/title,
rotation display, ✎ editing) materialize a row from the catalog's parsed
artist/title at that moment. Bulk import would add ~415K rows whose only reader
is a by-path lookup that pure derivation already serves — while bloating the
shared stats DB and requiring re-import whenever the catalog rebuilds.

Because ids are deterministic, a bulk importer can still be added later and
will produce byte-identical ids — no migration risk. (A `scripts/import_ssd_library.py`
is therefore *deferred*, not designed-in.)

Materialized rows: `source='library'`, `parse_method='catalog'`,
`confidence=NULL`, `needs_review=0`, `artist`/`title` from the catalog row,
`file_path` = the catalog path, `ext` from the filename. Materialization uses
upsert-if-absent semantics (mirroring `upsert_scanned`'s guarantee): an
existing row — including one a KJ manually edited via ✎ — is **never**
overwritten by re-materialization.

### D3. Resolution seams (all best-effort, never raise)

New pure helper in `naming.py`:

```
library_media_id(path, mount) -> "lib-…" | None   # NFC, strip mount, sha1[:12]
```

New catalog-backed helper (small, lives beside `ExternalCatalog`):

```
catalog.get_by_path(path) -> row | None   # exact, then NFC/NFD variants
```

Integration points (each wrapped in the existing try/except patterns):

1. **`routes.resolve_row_media_id` local branch** — third fallback: path under
   `external_media_mount` → `library_media_id(...)`. Pure; search rows for SSD
   files come from the catalog anyway, so no extra I/O on the hot path.
2. **`_record_play_stat` / `_record_preview_stat` local branch** — on
   `get_by_path` miss: `catalog.get_by_path(path)` → derive id, materialize the
   `media_library` row, record the event with catalog artist/title.
3. **`link_rotation_file`** — after a successful link of a path under the SSD
   mount, materialize the row (so rotation display + note editor work
   immediately, before the first play).
4. **`scripts/backfill_play_stats.py`** — `--catalog-db` arg (default:
   the configured `external_catalog_db`); `_resolve_media_id` gains the same
   catalog fallback and materializes rows for attributed SSD plays. Re-run is
   idempotent (`--execute` deletes `source='backfill'` first) → expect most of
   the 482 skips to attribute.

### D4. Rotation display enrichment

`_decorate_rotation_entries` attaches `media_meta = {artist, title}` for linked
entries, resolved `media_library.get_by_path` → catalog fallback. The frontend
uses it wherever it currently falls back to the linked file's raw basename.
(Entries linked via the search UI already get decent `song_artist` text from
the catalog row; this covers entries created from singer requests and
pre-existing archive rows.) Exact frontend touchpoints to be confirmed during
implementation — this is a display-only, additive field.

### D5. `media_db_path` relocation (device runbook, off-show, before backfill)

`media_library.db` (identity + stats + notes) moves from the repo dir to
`~/kjdata/` — same home as `rotation.db`, which survives reimage/reclone.
The backfill script's defaults already assume `~/kjdata/media_library.db`.

Runbook (needs Andrew / off-show window; service restart interrupts playback):

1. `sudo systemctl stop kj-controller`
2. `sqlite3 /opt/nomad/kjbox/kj-controller/media_library.db "PRAGMA wal_checkpoint(TRUNCATE);"`
   then copy `media_library.db` → `/home/nomad/kjdata/media_library.db`
   (never copy a live WAL DB — service is stopped).
3. `config.json`: set `media_db_path=/home/nomad/kjdata/media_library.db`, set
   `external_media_mount` explicitly, and (loose end #2) set
   `master_sync_source`/`master_sync_dest` explicitly.
4. Start service; verify row counts (2565 media_library rows, play_events
   intact); rename the old repo-dir DB to `.bak-<date>`.

### D6. Error handling

Catalog missing/closed, mount unset, or any lookup failure → behave exactly as
today (no id, no recording, no enrichment). No new failure can reach the live
playback path — same fault-isolation contract as the v0.55.0 stats work.

### D7. Freshness (out of scope, noted)

The catalog reflects the 2025-02-28 file list. SSD files added since are
invisible to search today and stay invisible to stats — consistent, not a
regression. Regenerating the list + `POST /catalog/build` is an independent
operational task; ids are relpath-stable across rebuilds.

## Testing

- Unit: `library_media_id` (mount strip, NFC/NFD equivalence, unset-mount →
  None, stability); catalog `get_by_path` variants; resolver fallback order
  (row wins over token wins over lib-derivation); materialize-on-play/link
  (including "existing manual row is not clobbered"); backfill attribution with
  a catalog fixture.
- Integration: unified search over a catalog fixture yields `lib-` ids +
  stats badges; `/play` on an SSD path records + materializes.
- Collision audit script over the real catalog (run on device, read-only).
- kjbox has no pytest CI — run locally via `rtk proxy python -m pytest`.

## Rollout

1. PR (kjbox only; no gen changes) → review → squash-merge.
2. Deploy off-show (backend change → restart required).
3. Device: D5 relocation runbook → collision audit → backfill `--dry-run`
   report for Andrew → `--execute` → verify stats UI (leaderboards now include
   commercial-disc plays; search badges on SSD rows).

## Assumptions to confirm with Andrew (design gate)

1. **Goals**: play stats + canonical display are in; Available-Songs browsing of
   the SSD and dedup hints are out. OK?
2. **Lazy materialization** over bulk import (D2). OK?
3. **`lib-` + relpath-hash** id scheme (D1) over `disc-<discid>` ids. OK?
4. **Relocation** of `media_db_path` to `~/kjdata` as part of this rollout (D5)
   — approve the off-show device window?
5. File-list refresh stays a separate follow-up (D7). OK?
