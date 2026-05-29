# Divebar Download Filename — Design

**Date:** 2026-05-22
**Worktree:** `kjbox-mirror-download-filename`
**Branch:** `feat/sess-20260522-0037-mirror-download-filename`

## Problem

Tracks downloaded from the community GCS mirror land on disk with raw
ID-based filenames, e.g. `divebar__divebar-1slF4D84xyFdmHprHmIX9Fvt5CLGdZ7ne.mp4`.
This is unhelpful when browsing the downloads folder, copying files, or
inspecting them outside of kjbox. The Divebar API already returns rich
metadata (`artist`, `title`, `brand`, `brand_code`, `drive_path`) and three
of three enqueue sites in `routes.py` have artist/title in hand — the metadata
is dropped on the way to disk.

### Smoking gun

Three backend paths produce ID-based names:

1. `routes.py:1388` — `/divebar/download` panel button. Falls back to
   `f"Divebar track {file_id[:8]}"` if the client doesn't pass a `filename`.
2. `routes.py:2802` — `/rotation/download-and-link`. Falls back to
   `f"divebar-{file_id}.mp4"` if `filename` is empty.
3. `routes.py:3141` — sing-request approval. **Hardcoded**
   `title = f"divebar-{source_ref}.mp4"` despite `req.song_title` and
   `req.song_artist` sitting right there on the request object. This is
   almost certainly the path that produced the observed file.

`media.download_from_url` then prepends `divebar__`, producing the observed
`divebar__divebar-{file_id}.mp4` pattern.

## Goal

Make every Divebar download land on disk with a human-readable filename,
using metadata the call site already has. No extra round-trips to the
Divebar Cloud Function (which lives outside this repo). One small set of
existing files on `nomadpctunnel` gets renamed in this session as a one-off.

## Non-goals

- Changing the `divebar__` on-disk prefix (kept — useful provenance signal).
- Adding a new Divebar Cloud Function action for metadata-by-file-id.
  All three call sites have the data; we don't need a lookup.
- Renaming files in the long term via an automatic scan/rename job.
  Once new downloads are nice, the existing few are renamed by hand once,
  then it's done.

## Filename format

```
<brand_code | "DB"> - <artist> - <title>.<ext>
```

with the `divebar__` prefix added by `media.download_from_url` as today.

Examples:

| Inputs | Result on disk |
|---|---|
| brand_code=`WTF`, artist=`Queen`, title=`Bohemian Rhapsody` | `divebar__WTF - Queen - Bohemian Rhapsody.mp4` |
| brand_code=`None`, artist=`Queen`, title=`Bohemian Rhapsody` | `divebar__DB - Queen - Bohemian Rhapsody.mp4` |
| brand_code=`WTF`, artist=`None`, title=`Bohemian Rhapsody` | `divebar__WTF - Bohemian Rhapsody.mp4` |
| brand_code=`None`, artist=`None`, title=`None` | helper returns `None`; caller falls back to `divebar-{file_id}.mp4` |

This matches the convention `app.js:4895` already uses when building filenames
client-side (e.g. for the rotation-search path that already works).

## Architecture

### New: `media.build_divebar_filename(brand_code, artist, title, ext=".mp4")`

Pure helper, lives next to `sanitize_filename_part` in `kj-controller/media.py`.

```python
def build_divebar_filename(brand_code, artist, title, ext=".mp4"):
    """Build a human-readable filename for a Divebar download.

    Returns None when no useful parts are present, so the caller can
    apply its own fallback (e.g. `divebar-{file_id}.mp4`).
    """
    parts = []
    bc = (brand_code or "DB").strip()
    if bc:
        parts.append(bc)
    if artist:
        parts.append(sanitize_filename_part(artist))
    if title:
        parts.append(sanitize_filename_part(title))
    # Need at least one of artist/title to be useful — a lone brand prefix
    # isn't worth keeping.
    if len(parts) < 2:
        return None
    return " - ".join(parts) + ext
```

### Three call sites in `routes.py`

All three call the helper and apply the same last-resort fallback:

```python
title = build_divebar_filename(brand_code, artist, title) \
        or f"divebar-{file_id}.mp4"
```

The fallback string is only ever hit when artist AND title are both empty,
which should be vanishingly rare once the frontend changes ship.

#### Site 1 — `/divebar/download` (`routes.py:1361`)

Accepts new fields in the JSON body:

```
{
  file_id:    str (required),
  artist:     str (optional),
  title:      str (optional),
  brand_code: str (optional),
}
```

Existing `filename` field is dropped (single-deploy repo; no backwards-compat
shim).

#### Site 2 — `/rotation/download-and-link` (`routes.py:2751`)

Same shape changes for `source=divebar` requests. `filename` field dropped
for divebar; YouTube source still uses `filename` since YouTube downloads
build their own filename from `yt-dlp` metadata.

#### Site 3 — Sing-request approval (`routes.py:3124`)

Replaces the hardcoded `title = f"divebar-{source_ref}.mp4"`. Uses
`req.get("song_artist")`, `req.get("song_title")`, and `source_meta` (which
already carries `brand_code` when the pick came from `kj_pick`, per
`routes.py:3079`).

### Frontend changes (`kj-controller/static/app.js`)

Two call sites:

- **Divebar search panel button (line 2933, 2949-2952)**: send
  `{file_id, artist, title, brand_code}` instead of `{file_id, filename}`.
  The data is already in `track` / `song`.

- **Rotation-search Divebar result (line 4895, 4980-4982)**: send structured
  fields directly instead of pre-building a `filename` string. Drop the
  `result.filename` construction at line 4895; replace with structured
  fields on the result object, surfaced in the `buildCall` body.

Server is the single source of truth from this point on.

## One-off cleanup of existing files

Done in this session, not landed in the repo.

1. **Inventory.**
   ```
   ssh nomadpctunnel 'ls -1 ~/kjdata/videos/divebar__divebar-*.mp4 2>/dev/null'
   ```

2. **Recover metadata via rotation DB.** Each file is (almost certainly)
   linked to a rotation entry whose `song_artist` column holds "Title - Artist":
   ```
   SELECT id, song_artist, file_path FROM rotation_entries WHERE file_path = ?;
   ```
   Parse `song_artist` by `" - "` (same split the JS uses elsewhere) to
   recover artist/title. Brand code isn't recoverable from local data
   alone, so the rename uses `DB - Artist - Title.mp4`.

3. **Confirm with user.** Print the proposed (old → new) mapping for every
   file. User eyeballs it before anything mutates.

4. **Pre-flight safety.**
   - Back up the rotation DB:
     ```
     cp ~/kjdata/rotation.db ~/kjdata/rotation.db.bak-divebar-rename-20260522
     ```
   - Skip any file matching `/status`'s `current_playing_path`.
   - Confirm no active show in progress.

5. **Rename + DB update, per file.**
   ```
   mv "$OLD" "$NEW"
   sqlite3 ~/kjdata/rotation.db \
     "UPDATE rotation_entries SET file_path = ? WHERE file_path = ?" \
     -- "$NEW" "$OLD"
   ```

6. **Media index.** `MediaIndex` re-scans on the next `/status`-driven
   refresh and rebuilds path keys, so no manual JSON edit needed. Verify
   by hitting `/status` and confirming the renamed files appear with the
   new path.

7. **Orphans.** Any file with no matching rotation entry is left alone
   (we lack metadata) and reported to the user. Expected to be empty in
   practice.

## Testing

### Unit — `tests/unit/test_media.py`

`build_divebar_filename`:
- all three fields present → `"WTF - Queen - Bohemian Rhapsody.mp4"`
- missing `brand_code` → `"DB - Queen - Bohemian Rhapsody.mp4"`
- missing `artist` → `"WTF - Bohemian Rhapsody.mp4"`
- missing `title` → `"WTF - Queen.mp4"`
- missing artist AND title → returns `None`
- sanitization: artist `"Queen/Bowie"` → no slashes in result

### Integration — `tests/integration/test_routes.py`

- `POST /divebar/download` with `{file_id, artist, title, brand_code}`
  → queue item `title == "WTF - Queen - Bohemian Rhapsody.mp4"`
- Same with `brand_code` omitted → `title == "DB - Queen - Bohemian Rhapsody.mp4"`
- Same with only `file_id` (no artist/title) → fallback
  `title == "divebar-{file_id}.mp4"`
- `POST /rotation/download-and-link` with `source=divebar` and structured
  fields → same set of assertions.

### Integration — sing-request approval

`tests/integration/test_sing_*.py` (closest existing file —
`test_sing_kj_pick_e2e.py` or similar):
- Approving a divebar sing-request with `song_title="Bohemian Rhapsody"`
  and `song_artist="Queen"` produces a queue item with
  `title == "DB - Queen - Bohemian Rhapsody.mp4"` (or with brand_code when
  `source_meta` provides it), NOT the legacy hardcoded `divebar-{file_id}.mp4`.

### No test for the one-off rename script

Lives in this session only, not in the repo.

## Risks

- **Sanitization** drops or remaps characters that might already appear in
  curated files (slashes, colons). Acceptable — same sanitizer is used
  for YouTube downloads today.
- **Existing file rename** updates `rotation_entries.file_path`. If a
  rotation entry isn't linked (file is orphaned in the videos folder),
  the rename is skipped. Risk of partial cleanup is bounded by the
  inventory size, which the user has indicated is small.
- **Live show during cleanup** — mitigated by skipping the currently-playing
  file and requiring user confirmation before mutating.

## Out of scope (deliberate)

- Migrating display logic to use structured fields stored in the media
  index. Not needed for the goal — once on-disk names are nice, the UI
  shows them.
- Adding a Divebar Cloud Function `metadata` action. Not needed at any of
  the three call sites; only the one-off rename would benefit, and the
  rotation DB linkage covers that case.
- Backwards-compat shim for client sending `filename` to the changed
  routes. Single repo, single deploy.
