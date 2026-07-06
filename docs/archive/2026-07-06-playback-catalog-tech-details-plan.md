# Playback + Library UX: fade cleanup, tech-details, catalog normalization

Date: 2026-07-06
Branch: `feat/sess-20260706-1317-playback-catalog-tech-details`

## Goals (from user)

1. Remove the custom-duration fade entry box + big "Fade" button from Playback Controls (presets 3/6/10/20s are enough).
2. Click the format pill (e.g. `MP4`) on the **currently-playing** song → show technical details (container, codec, resolution, audio, bitrate, duration).
3. Same "click the format pill → technical details" on **Library** rows.
4. Make **Catalog** (4TB SSD) rows look & behave identically to internal-storage rows — same buttons, click handlers. Delete legacy bespoke catalog rendering.

## Decisions (confirmed with user)

- **Folder/path line**: show it for **all** rows (internal + catalog), not just catalog. Subtle grey secondary line under the title.
- **Catalog Delete**: NO delete for catalog rows (4TB master is curated/irreversible). Falls out naturally — `createMediaItemLi` gates Delete on `is_download`, Edit on `media_id`; catalog items have neither.
- **Tech details UI**: modal popup, matching existing modal pattern (`modal-backdrop`/`modal-content`), lazy-loads ffprobe data on open.

## Backend

New module `mediainfo.py` + endpoint `POST /media/info`:
- Body `{file_path}`. Validate via `media.validate_path()` with external-mount fallback (mirror `preview._resolve_local_path`).
- `probe_media_info(path)` shells `ffprobe -v quiet -print_format json -show_format -show_streams`.
- Returns `{ok, container, duration, size_bytes, bit_rate, video:{codec,width,height,fps,pix_fmt}, audio:{codec,sample_rate,channels,channel_layout,bit_rate}}` or `{ok:false, error}`.
- Unit tests for the parser (fixture ffprobe JSON → normalized dict); integration test for the route (path validation + 404/400 paths).

## Frontend

### Item 1 — fade cleanup
- `index.html`: delete `.fade-custom` div (input + Fade button).
- `app.js`: `fadeButtons()` selector drops `#fade-custom-go`; delete `fadeOutCustom()`.
- `style.css`: remove `.fade-custom*` rules.

### Items 2 & 3 — tech-details modal
- `index.html`: add `#mediainfo-modal` (loading spinner + body), matching existing modals.
- `app.js`: `openMediaInfoModal(filePath, displayName)` — open, spinner, fetch `/media/info`, render spec sheet; `closeMediaInfoModal()`.
- Now-playing: in `updateNowPlaying`, set `#np-filetype` clickable (class + title + onclick → current playing path) when a file is loaded.
- Library/catalog: `mediaFormatBadge` badge becomes clickable → `openMediaInfoModal(item.file_path)`. Add cursor/hover.

### Item 4 — normalize catalog rows
- `createMediaItemLi`: wrap name+badge+tags into `.media-title-row`; append `.media-folder` line (from `item.folder` or dirname(file_path)) for all rows.
- `renderUnifiedResults`: map each catalog result → `{display_name, file_path: path, folder, media_kind (from format), ext}` and call `createMediaItemLi`. Delete the bespoke inline catalog `<li>` block (incl. standalone Copy button — name becomes click-to-copy like internal rows).
- `style.css`: add `.media-title-row` (flex row), `.media-folder` (grey ellipsis); make `.media-title` a column. Keep `.catalog-*` only if still referenced (kn-local-match uses it) else prune.

## Version + tests
- Bump `pyproject.toml` (0.70.0 → 0.71.0 already taken by #169 on main? verify — use next free minor).
- Backend: pytest for `mediainfo` parser + route.
- Frontend: JS syntax pre-commit hook; extend e2e/DOM guard tests if present for row structure.

## Notes / gotchas
- App runs on `127.0.0.1:5001` on device. No pytest CI on repo (security.yml only) — run tests locally.
- `.playback-controls` is an `auto 1fr` grid; `.fade-controls` is one grid item (col 2), so removing its child `.fade-custom` is safe.
- Autodeploy OFF + live device — frontend changes need only browser refresh, `.py` changes need service restart (permission required).
