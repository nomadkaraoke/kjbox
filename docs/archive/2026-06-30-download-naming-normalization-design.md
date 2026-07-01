# Download filename normalization + canonical media identity — design — 2026-06-30

## Problem

Downloaded karaoke files in `/opt/nomad/YTDownloads` have accumulated over a year of a
weekly karaoke night under wildly inconsistent naming: an early ad-hoc yt-dlp-and-upload
era, several generations of in-app download code, noisy YouTube channel/title strings, and
varied community-producer conventions in the divebar GCS mirror.

The library is an **asset** (a growing offline copy of songs popular at the night — never
re-download the same file twice), but the inconsistent filenames mean we cannot **reliably
identify artist + title** for a downloaded file. That hurts two flows:

1. **On-device library search** can't match what it can't parse.
2. **Linking a file to a rotation entry** shows a messy filename (YouTube id, channel,
   `(Karaoke Version)` noise, brand codes) in the rotation instead of a clean `Artist - Title`.

## Goals

- Reliably derive **canonical `Artist` + `Title`** for every downloaded file, regardless of source.
- Give every file a **standardized on-disk name** that is at-a-glance identifiable *and*
  codifies its source + a stable id.
- Persist canonical metadata in a store keyed by a **stable id** so identity survives any
  rename/move.
- Separate downloads by **source folder** (req A).
- **Tidy the existing backlog** into the new scheme via a reviewed migration (req B).
- On link, set the rotation `song_artist` field to a consistent **`Artist - Title`** (req C).

## Current state (researched 2026-06-30, post-cleanup)

Two media roots are configured (`config.json`: `media_folders`):

| Root | Count | Contents |
|------|-------|----------|
| `/opt/nomad/MP4-720p` | 1305 | karaoke-gen **masters**, clean `NOMAD-#### - Artist - Title.mp4`, `is_download:false`. **Stale**: an old, incomplete copy — GCS holds **1409** (≈104 newer releases missing locally). |
| `/opt/nomad/YTDownloads` | 1087 | **downloads** (the messy set), `download_folder` |

The masters copy is out of date because nothing keeps it in sync with the catalog — this design
adds an automated GCS mirror (see *GCS master-catalog auto-sync*).

YTDownloads breakdown (after the 2026-06-30 dedup cleanup, which removed 393 litter files and
quarantined 95 YT twins to the non-indexed sibling `/opt/nomad/_redundant_quarantine`):

- **1005 (93%)** YouTube-pattern `{id}__{channel}__{title}.mp4` (`parse_youtube_filename`).
- **16** divebar mirror `divebar__{brand} - {artist} - {title}.{ext}`.
- **66** residual mp4 — mostly Nomad's own gen lossy renders `Artist - Title (Final Karaoke Lossy 4k).mp4`,
  old yt-dlp `Title (Karaoke Version) [ytid].mp4`, and a few raw uploads.
- 5 `.zip` (CDG pairs), 1 `.part`, 1 extension-less.

### Why deterministic parsing alone is insufficient

Even the 93% "parseable" YouTube titles are noisy *and the artist/title order is inconsistent*:

- Most channels: `Artist - Title` → `Bella Kay - iloveitiloveit (Karaoke Version)`.
- **KaraFun is reversed**: `Santeria - Sublime _ Karaoke Version _ KaraFun` (Santeria is the *song*,
  Sublime the *artist*).
- Separators vary (` - `, ` _ `, ` • `, `《》`, fullwidth `｜`), channel is often `Unknown`,
  trailing `[karaoke]` / `(Karaoke Version)` / `KARAOKE` / non-Latin scripts.

The current `catalog.parse_karaoke_filename()` splits on `" - "` and *assumes* order, so it gets
KaraFun-style files **backwards**. Resolving artist/title and their order is the residue an LLM
handles and deterministic rules cannot.

### Relevant existing code

- **Filename generation**: `media.download_video` (YouTube `{id}__{channel}__{title}`),
  `media.download_from_url` (hardcodes `divebar__` prefix — also wrongly applied to gen
  downloads via `gen_poller`), `media.download_cdg_pair` (`divebar__….zip`),
  `utils.build_divebar_filename` / `utils.divebar_ext` / `utils.sanitize_filename_part`.
- **Index**: `media.MediaIndex.scan()` persists `media_index.json` **keyed by file path** —
  exactly what breaks on rename. Stores `display_name` (= YouTube title only for YT-pattern
  files, else raw stem). No artist/title split at index time for non-YT files.
- **Search**: `routes.unified_search` parses local files at *query time* via
  `parse_karaoke_filename`; UI shows the **raw filename** (`app.js`), not the parsed split.
- **Rotation link**: `song_artist` is one free-text column (`rotation_store.py`). On link the
  frontend writes `song_artist = "{title} - {artist}"` (**Title - Artist** today) from the
  parsed result; `link_file` only sets `file_path`. Invariant comment says `"Song - Artist"`.
- **LLM/auth**: kjbox has **no LLM today** and **no Vertex/GCS credential** on the device (only
  a Sheets-scoped SA key; GCS reached anonymously via a Cloud Function). It *does* have an
  authenticated HTTP client to **karaoke-gen** (`gen_client.py`, `X-Admin-Token`), and gen
  already runs the Gemini "artist/title match judge".
- **Concurrency precedent**: every cloud call is wrapped try/except → offline fallback
  (`divebar.py`, `gen_poller.py`); the device runs live shows and must degrade offline.
- **Recently shipped (v0.50.0, build on this)**: a unified rotation-search row renderer
  `renderRotRowHtml` + `.rs-*` classes; same-file search dedup; and
  `scripts/cleanup_redundant_downloads.py` whose quarantine/relink/NFC-NFD-safe rotation-guard
  patterns we reuse.

## Locked decisions

| Concern | Decision |
|---|---|
| LLM routing | New **batch endpoint in karaoke-gen** (reuses gen's Gemini/match-judge; no new device secret; offline → heuristic + manual). |
| Identity | Stable `media_id` = **source-prefixed natural key**, in a new SQLite `media_library` table. |
| Root folder | Rename `/opt/nomad/YTDownloads` → **`/opt/nomad/downloads`**; per-source subfolders inside it. |
| Masters | Move stale `/opt/nomad/MP4-720p` → **`/opt/nomad/downloads/NOMAD-720p`**, kept current by an automated GCS sync. |
| Master sync | **systemd timer, `gcloud storage rsync`, every 5 min**, auth via a **dedicated read-only SA key**. |
| On disk | `<source folder>/Artist - Title [media_id].ext`; canonical full values live in the DB. (The `NOMAD-720p` mirror is exempt — it keeps GCS-native `NOMAD-####` names.) |
| Format | `Artist - Title` everywhere (flip rotation + KN/divebar builders). |
| Pipeline | deterministic quick-wins → gen LLM (esp. order) → confidence gate → KJ review. |
| Edit UX | Expand the existing **Available Songs** view (editable Artist/Title + "Needs review" filter); no new screen. |
| Migration | One-off backfill, **dry-run report you approve** before applying; runs *after* the dedup cleanup. |
| Linking | rotation `song_artist` ← canonical `"Artist - Title"` from the DB record. |
| Dedup-skip (bonus) | Before downloading, if the prospective `media_id` already exists with a present file, **link the existing file instead of re-downloading**. |

## Architecture

### 1. Canonical identity model

A new SQLite store, `media_library` (config `media_db_path`, e.g.
`/opt/nomad/kjbox/kj-controller/media_library.db`), decoupled from `rotation.db`:

```sql
CREATE TABLE media_library (
  media_id          TEXT PRIMARY KEY,   -- e.g. 'yt-UM1XiyBmhM'
  source            TEXT NOT NULL,      -- youtube | community | gen | master | upload
  source_ref        TEXT,              -- raw natural key (video id, brand+fileid, job id, disc no)
  artist            TEXT NOT NULL DEFAULT '',
  title             TEXT NOT NULL DEFAULT '',
  artist_norm       TEXT NOT NULL DEFAULT '',   -- text_normalize.normalize(artist) for search/dedup
  title_norm        TEXT NOT NULL DEFAULT '',
  confidence        REAL,              -- 0..1 from parser; NULL once user-confirmed
  parse_method      TEXT,              -- deterministic | llm | manual | master
  needs_review      INTEGER NOT NULL DEFAULT 0,
  raw_original_name TEXT,              -- pre-migration filename (audit / undo)
  file_path         TEXT,              -- current on-disk path (NULL if file gone)
  ext               TEXT,
  created_at        TEXT,
  updated_at        TEXT
);
CREATE INDEX idx_media_source       ON media_library(source);
CREATE INDEX idx_media_needs_review ON media_library(needs_review);
CREATE INDEX idx_media_norm         ON media_library(artist_norm, title_norm);
```

**`media_id` scheme** (source prefix + natural key → meaningful, dedup-friendly, stable):

| Source | `media_id` | `source_ref` from |
|--------|------------|-------------------|
| YouTube | `yt-<11-char-video-id>` | yt-dlp `info["id"]` / `parse_youtube_filename` |
| Community (divebar) | `db-<brand>-<file_id>` (fallback `db-<brand>-<hash8>`) | divebar lookup brand_code + file id |
| Gen "make" | `gen-<job_id[:8]>` | gen job id |
| Master | `nomad-<disc#>` (e.g. `nomad-0729`) | `NOMAD-####` disc number |
| Upload / unknown | `up-<sha1(file)[:8]>` | content hash (identical bytes → dedup) |

**File ↔ id binding**: the `media_id` is embedded in the filename as a trailing `[media_id]`
token. `scan()` recovers it with a regex on the stem; `file_path` in the DB is a secondary
anchor for repair. Metadata therefore survives any rename as long as the bracket token (or the
DB row) is intact.

### 2. On-disk scheme

The download root is renamed **`/opt/nomad/YTDownloads` → `/opt/nomad/downloads`** (done properly
for clarity going forward), holding per-source subfolders:

```
/opt/nomad/downloads/
  youtube/      # source=youtube   → Artist - Title [yt-<id>].ext
  community/    # source=community → Artist - Title [db-<brand>-<fileid>].ext
  gen/          # source=gen       → Artist - Title [gen-<job8>].ext
  uploads/      # source=upload    → Artist - Title [up-<hash8>].ext
  NOMAD-720p/   # source=master    → GCS mirror, NATIVE names (see §9), exempt from the slug
```

- **Managed-download filename**: `{Artist} - {Title} [{media_id}].{ext}`, e.g.
  `youtube/Bella Kay - iloveitiloveit [yt-UM1XiyBmhM].mp4`.
  - Artist/Title are sanitized slugs (extend `sanitize_filename_part`), truncated so the whole
    filename stays within filesystem limits (≤255 bytes); the **full** canonical values are in
    the DB.
  - At-a-glance readable; `[yt-…]` codifies source + stable key; the scanner round-trips the id.
- **`NOMAD-720p/` is the exception**: it is an rsync mirror of the GCS masters (§9) and keeps the
  GCS-native `NOMAD-#### - Artist - Title.mp4` names (renaming would make rsync re-download the
  whole catalog). The scanner derives `media_id = nomad-####` from the `NOMAD-####` prefix and
  stores the parsed canonical artist/title in the DB.
- **Renaming the root** touches `config.json` (`download_folder`, `media_folders`), and must be
  audited against other references to the old path: the dedup script's quarantine sibling logic
  (`_redundant_quarantine`/`_playability_quarantine` resolve as siblings of the download folder —
  still correct under `/opt/nomad/downloads`), the `playability-run` harness, and `preview-cache`
  config. `/opt/nomad/FillerMusic` is unrelated (filler, not a karaoke source) and stays.

### 3. Parsing pipeline

A `naming.py` module (new) exposes `resolve_identity(file) -> {source, media_id, artist, title,
confidence, parse_method, needs_review}`:

1. **Deterministic quick-wins** (free, offline): classify source; strip `{id}__{channel}__`,
   `[ytid]` brackets, `divebar__{brand}` prefix, `NOMAD-####` prefix, and karaoke-noise affixes
   (`(Karaoke Version)`, `(Final Karaoke Lossy 4k)`, `[karaoke]`, `_ KaraFun`, `KARAOKE`, …);
   normalize separators. Produces a candidate + a deterministic confidence. High-confidence,
   unambiguous cases (masters, divebar rows that already carry explicit artist/title fields)
   **skip the LLM**.
2. **LLM via karaoke-gen** for the residue — crucially to fix artist/title **order** (the
   KaraFun-reversed problem). Returns `{artist, title, confidence}`.
3. **Confidence gate** (default threshold configurable, e.g. 0.75): at/above → accept; below →
   `needs_review = 1` with the best deterministic guess as a placeholder.

### 4. karaoke-gen endpoint (cross-repo)

New `POST /api/parse-karaoke-titles` in karaoke-gen, reusing its match-judge/Gemini:

```
Request : { "items": [ { "id": "...", "filename": "...", "channel": "...", "source": "youtube" }, ... ] }
Response: { "results": [ { "id": "...", "artist": "...", "title": "...", "confidence": 0.0-1.0 }, ... ] }
```

- Batch (sized for the ~1k-file migration); auth via the existing `X-Admin-Token`.
- kjbox adds `GenClient.parse_titles(items)`; on any failure the caller catches and falls back to
  the deterministic guess + `needs_review`, exactly like `divebar.py` / `gen_poller.py`.
- This is a **separate karaoke-gen PR**. The kjbox PR degrades gracefully if the endpoint is
  absent (treats it as offline).

### 5. Download flow changes (new downloads)

- Each download path computes its `media_id` from the source natural key, runs `resolve_identity`,
  writes the slug name into the correct source folder, and upserts the `media_library` row.
- Fixes the existing `divebar__` mislabel of gen downloads (`gen_poller` path now `source=gen`).
- **Dedup-skip**: at enqueue, if the prospective `media_id` already exists in `media_library`
  with a present `file_path`, link that file instead of downloading again.

### 6. MediaIndex integration

- `scan()` recovers `media_id` from each filename's `[…]` token (or path-match fallback) and
  joins the `media_library` row; `display_name` becomes the canonical `Artist - Title`.
- The path-keyed `media_index.json` remains the disk-truth layer but now carries `media_id`.
- `scan()` continues to skip `_playability_quarantine` and must also skip
  `_redundant_quarantine` (already a non-indexed sibling) and `preview-cache`.

### 7. Available Songs view expansion (edit/review UX)

- **Backend**: `list_items()` joins `media_library` → returns `artist`, `title`, `source`,
  `confidence`, `needs_review`. New `POST /media/metadata { media_id, artist, title }` sets the
  values, `parse_method='manual'`, `confidence=NULL`, `needs_review=0` (shared by the
  link-time inline edit).
- **Frontend**: show canonical `Artist - Title` (not raw filename); editable Artist/Title;
  a **"Needs review"** filter/badge for low-confidence rows. Styling follows the v0.50.0 `.rs-*`
  aesthetic and its deliberate badge reduction — source shown as a subtle tag, review as a
  small amber marker, no pill clutter.

### 8. Rotation linking (req C)

- `selectRotSearchResult` writes `song_artist = "{artist} - {title}"` (**Artist - Title**).
- Flip the row builders that currently emit `title - artist` (local, KN, divebar rows in
  `app.js`) and the backend `song_artist_fallback`; update the invariant comment.
- For local files, `artist`/`title` now come from `media_library` (canonical), not a query-time
  `parse_karaoke_filename`.

### 9. GCS master-catalog auto-sync (NOMAD-720p mirror)

Keep the on-device master catalog automatically current with the Nomad release catalog in GCS, so
new releases appear in the library (and become rotation-linkable) with no manual step.

- **Source**: `gs://nomadkaraoke-divebar-files/files/Nomad Karaoke/MP4-720p/` (1409 objects today;
  1305 present locally → ≈104 to backfill on first run). Names are canonical
  `NOMAD-#### - Artist - Title.mp4`, Unicode-safe.
- **Destination**: `/opt/nomad/downloads/NOMAD-720p/` (kept name-identical to GCS).
- **Mechanism**: a **systemd timer** (`nomad-master-sync.timer`, `OnUnitActiveSec=5min`) running a
  small script that invokes `gcloud storage rsync` **download-only / additive** (no
  `--delete-unmatched-destination-objects` — a release pulled from GCS is not deleted locally).
  On a run that changed files, the script curls the local Flask `/rescan` so new masters index
  immediately; `media_library` rows for new masters are created on scan (`source=master`,
  `media_id=nomad-####`).
- **Auth**: a **dedicated read-only service account** with `roles/storage.objectViewer` on
  `nomadkaraoke-divebar-files`, key file on the device (mirrors the existing Sheets-SA pattern),
  referenced by config (`master_sync_credentials_file`). Least-privilege; not tied to a personal
  account. (The device happens to already have working user gcloud creds, but the appliance should
  not depend on a person's OAuth.)
- **Cost/efficiency**: one LIST of ~1.4k objects per run ≈ a Class-A op; ~288 runs/day → cents per
  month. Transfers only new/changed objects.
- **First-run reconcile note**: because rsync compares size+mtime, the first post-migration run may
  re-pull some already-present masters if local mtimes differ; run it once off-show to settle,
  after which 5-min deltas are tiny. (Optionally seed by moving the existing `MP4-720p` files into
  `NOMAD-720p` first — see Migration — so only the ≈104 genuinely-new files transfer.)
- **Independence**: this sync needs none of the LLM parsing pipeline (master names are already
  clean), so it can ship as an **early, standalone phase** — an immediate library-freshness win.

## Migration / backfill (req B)

A standalone `kj-controller/scripts/normalize_download_library.py` (mirrors the dedup script's
ergonomics): **dry-run by default**, `--execute` to apply.

0. **Restructure the roots** (once): rename `/opt/nomad/YTDownloads` → `/opt/nomad/downloads`
   and move `/opt/nomad/MP4-720p` → `/opt/nomad/downloads/NOMAD-720p`; update `config.json`
   (`download_folder`, `media_folders`) and audit other references to the old paths (systemd,
   `playability-run`, `preview-cache`). Seeding `NOMAD-720p` from the existing masters means the
   first GCS rsync only transfers the ≈104 genuinely-new releases.
1. Re-scan the roots (post-cleanup); skip `_redundant_quarantine`, `_playability_quarantine`,
   `preview-cache`; ignore `.part`/junk.
2. For each file: classify source → `media_id`; deterministic parse.
3. Batch-call gen for low-confidence/ambiguous files.
4. **Emit a dry-run report (CSV + MD)**: `old_path → {source, media_id, artist, title,
   confidence, needs_review, proposed_new_path}`. You review/correct it (the corrected CSV is
   consumable by `--execute`, so manual fixes are honored before anything moves).
5. `--execute`: back up `media_index.json`, `rotation.db`, `media_library.db`; create source
   folders; move+rename into the slug scheme; upsert `media_library` rows; **repoint live
   `rotation_entries` / `rotation_archive` `file_path`** (reuse the cleanup script's
   `relink_references`, NFC/NFD-tolerant); trigger a rescan. A move log + `raw_original_name`
   make it reversible.

**Masters (`NOMAD-720p`)**: get `media_library` rows (`source=master`, `media_id=nomad-####`,
artist/title parsed from the already-clean names). They are **moved** (into `downloads/NOMAD-720p`)
but **not renamed** — the GCS-native `NOMAD-####` names are kept so the rsync mirror (§9) stays
efficient. Andrew is not concerned about preserving legacy past-rotation file paths, and the next
live show is days away, so the migration can run and settle well before then.

## Offline behavior

- Deterministic parse + manual edit are fully offline.
- The gen LLM call is best-effort: failure → deterministic guess + `needs_review`, never blocks a
  download or a link. New downloads still get a usable name offline; the LLM refines later (a
  re-run can upgrade `needs_review` rows).

## Out of scope / future

- Activating the dead `in_library` flag ("we own this song, hide the KN download option") — still
  gated on a robust song-identity key. **This design builds that key** (canonical artist/title +
  stable ids + `*_norm`), so it becomes a viable follow-up.
- The 9 rotation rows referencing already-missing `divebar__…` mirror files (pre-existing; the
  identity pass can surface them for re-download/unlink).
- Divebar/BigQuery `brand_code: None` parsing (upstream pipeline concern).

## Testing

- **Unit**: `media_id` derivation per source; deterministic parser (noise stripping, separator
  variants, order cases incl. KaraFun-reversed) with fixtures drawn from the real sampled names;
  slug builder (sanitize + length); `media_library` CRUD + upsert; dedup-skip; migration
  classify + `relink_references` (NFC/NFD); `GenClient.parse_titles` happy-path + offline
  fallback.
- **Frontend**: Available Songs edit + "Needs review" filter; rotation `song_artist` is
  `Artist - Title`.
- **Migration**: dry-run on a fixture tree; idempotency (re-run is a no-op); report round-trips
  through `--execute`.
- **Master sync**: script builds the correct `gcloud storage rsync` invocation (additive, scoped
  SA); a changed run triggers `/rescan`; new masters get `source=master` / `media_id=nomad-####`
  rows; a run with no remote changes is a no-op. (Live rsync verified manually on-device.)
- Note: kjbox has no pytest CI (only `security.yml`) — tests run locally.

## Rollout notes

- Two PRs: karaoke-gen (parse endpoint) and kjbox (everything else). kjbox degrades gracefully if
  the gen endpoint isn't deployed yet.
- kjbox autodeploy is OFF; backend changes need a manual service restart (interrupts playback) —
  deploy off-show. Frontend changes need a version bump (`app.js?v=`) to cache-bust.
- The root rename + masters move + migration `--execute` run once on NomadPC off-show, after DB
  backups, like the dedup run.
- **Master sync one-time setup** (out-of-band from the app deploy): create the read-only SA +
  `roles/storage.objectViewer` grant on `nomadkaraoke-divebar-files`, drop the key on the device,
  install the `nomad-master-sync` systemd service + timer. This is IaC/device-config, likely a
  small Pulumi/manual step separate from the code PRs.
- **Suggested phasing** (each independently shippable): (P1) root restructure + `media_library`
  store + scan integration + **master GCS sync** — immediate library-freshness win, no LLM needed;
  (P2) parsing pipeline + gen endpoint + download-flow renaming + dedup-skip; (P3) Available Songs
  edit/review UX + rotation `Artist - Title` flip; (P4) reviewed backlog migration.
