# Download-naming P3 + P4 — execution notes & gotchas (2026-07-02)

Context for the next session. **P1 + P2 are shipped** (P2 merged to main both repos 2026-07-02; see
`project_kjbox_download_naming_normalization` memory). This doc captures the P3/P4 scope plus the
**non-obvious gotchas discovered while mapping the code**, so P3 can be executed cleanly.

Authoritative design: `docs/archive/2026-06-30-download-naming-normalization-design.md` (§7 Available
Songs UX, §8 rotation linking, Migration section for P4).

Worktree already created: `kjbox-download-naming-p3` on branch `feat/sess-20260702-0050-download-naming-p3`
(off main @ v0.52.0). It currently contains only this notes doc.

---

## P3 — Available Songs edit/review UX + rotation `Artist - Title` flip

### Part A — Rotation `Artist - Title` flip (design §8)  ⚠️ CROSS-CUTTING INVARIANT

Today rotation `song_artist` is stored/displayed as **`Title - Artist`**. The design flips it to
**`Artist - Title`** everywhere. This is an invariant with **multiple consumers that must flip together** —
found via grep, verify each before changing:

- **Frontend builders (set `song_artist` = `title + ' - ' + artist` today)** — `static/app.js`:
  - `~5723` KN row: `song_artist: (match.title||'') + ' - ' + (match.artist||'')`
  - `~5845` local row: `{ song_artist: song.title + ' - ' + song.artist }`
  - `~5903` local `title:` (display) `song.title + ' - ' + song.artist`
  - `~5922` divebar row: `song_artist: (dv.title||'') + ' - ' + (dv.artist||'')`
  - `~5936` divebar `title:` (display) same
  Flip all five to `artist + ' - ' + title`.
- **Backend SMS-target split** — `routes.py:~3043-3047` `_resolve_sms_target`: when the sing_request
  lacks structured fields it splits `entry["song_artist"].split(" - ", 1)` as `song=parts[0],
  artist=parts[1]` (assumes Title-Artist). After the flip it must be `artist=parts[0], song=parts[1]`.
  Update the comment ("Rotation entries are typically Title - Artist").
- **`song_artist_fallback`** — `routes.py:~2453 _resolve_or_create_rotation_entry_id` + its callers
  that build a fallback from title/artist. Grep every `song_artist_fallback=` call site.
- **Invariant comment** — `rotation_store.py` has a "Song - Artist" invariant comment; update it.
- **`_normalize_song_key(artist, title)`** (`routes.py:57`) takes SEPARATE artist/title args, so it's
  **order-independent** — no change needed, just don't accidentally pass the combined string.

⚠️ **Existing-data interpretation:** rows already in `rotation_entries`/`rotation_archive` store
`song_artist` as `Title - Artist`. Display is verbatim (fine — just cosmetic order), but the SMS-split
fallback (@3045) would MIS-interpret old rows after the flip. That fallback rarely fires (structured
`song_title` usually present). Options: accept (documented), or gate the split on a heuristic. Decide
explicitly. Do NOT rewrite historical `song_artist` values.

⚠️ **Parallel initiative conflict:** a "play/preview stats" initiative is editing the SAME rotation row
builders / `renderRotRowHtml`. Coordinate / rebase — expect conflicts in `app.js` around the row
renderers. Check `git log origin/main` before starting.

- For local rows, artist/title should come from **`media_library`** (canonical), not a query-time
  `parse_karaoke_filename` (design §8). Verify where the local-row artist/title originate in the
  `/rotation/search` backend + `renderRotLocalRow`.

### Part B — Available Songs edit/review UX (design §7)

- **Backend:** `MediaIndex.list_items()` (routes `list_media` @ `routes.py:779`) should JOIN
  `media_library` → include `artist`, `title`, `source`, `confidence`, `needs_review`, `media_id`.
  New `POST /media/metadata {media_id, artist, title}` → `MediaLibraryStore.set_metadata` (already
  exists: sets `parse_method='manual'`, `confidence=NULL`, `needs_review=0`, recomputes `*_norm`).
- **Frontend:** Available Songs view shows canonical `Artist - Title` (not raw filename); inline-editable
  Artist/Title; a **"Needs review"** filter/badge for low-confidence rows. Follow the v0.50.0 `.rs-*`
  aesthetic + its badge restraint (subtle source tag, small amber review marker, no pill clutter).
- ⚠️ **No unit-test safety net for kjbox frontend** — only flaky Playwright e2e. Test `list_items` +
  `/media/metadata` at the backend (pytest); manually verify the UI (or add a focused e2e).

### P3 testing
Backend: `list_items` media_library join; `/media/metadata` happy + missing-row; SMS-split flip
(`_resolve_sms_target` artist/song order after flip); any `song_artist_fallback` change. Run via
`rtk proxy python -m pytest ... > file` then read the file (bare rtk mangles the pytest summary; avoid
`2>&1` and trailing `; tail` — the rtk hook errors on them).

---

## P4 — reviewed backlog migration (design "Migration" section)

`scripts/normalize_download_library.py` (mirror `cleanup_redundant_downloads.py` ergonomics):
- **Dry-run by default** → CSV + MD report: `old_path → {source, media_id, artist, title, confidence,
  needs_review, proposed_new_path}`. **Andrew reviews/corrects the CSV**; the corrected CSV feeds
  `--execute` (honor manual fixes).
- `--execute`: back up `media_index.json` + `rotation.db` + `media_library.db` FIRST; create source
  folders; move+rename existing downloads into the slug scheme (finally uses
  `naming.build_slug_filename`); upsert `media_library`; **repoint live `rotation_entries` /
  `rotation_archive` `file_path`** (reuse cleanup script's `relink_references`, NFC/NFD-tolerant); rescan.
- Skip `_redundant_quarantine`, `_playability_quarantine`, `preview-cache`.
- **`NOMAD-720p` masters are EXEMPT from the slug** — keep GCS-native `NOMAD-####` names or the 5-min
  rsync mirror re-downloads the whole catalog.
- ⚠️ **`--execute` is device-only, off-show, needs Andrew's approval of the dry-run report + DB backups.**

---

## P2 device deploy (the pending user-facing win) — see the runbook

`docs/archive/2026-07-02-download-naming-p2-deploy-runbook.md`. Not done yet (needs off-show + no live
event). `main` is merged but kjbox autodeploy is OFF so the device is untouched until a manual pull+restart.
