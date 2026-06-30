# Search-row dedup + unified renderer + redundant-download cleanup — 2026-06-30

## Summary

Searching the rotation **"Link song"** box for a song held in multiple forms (the
trigger case was *Maxïmo Park – Books from Boxes*) surfaced **three rows that all play the
same Nomad Karaoke video**, and **one row rendered with different fonts / pills / alignment**
than the rest. This session investigated why, then shipped a three-part fix: clean up the
on-disk mess (A), stop the same-file double-surfacing in search (B), and render every search
row through one consistent template (C).

## Investigation findings (the "why")

For *Books from Boxes* there are only **2 real files on disk**, surfaced as **3 rows**:

| File | What it is |
|------|-----------|
| `/opt/nomad/MP4-720p/NOMAD-0729 - … Books from Boxes.mp4` (6.7 MB, `is_download:false`, no `youtube_id`) | the karaoke-gen **master** |
| `/opt/nomad/YTDownloads/RlBlAKxyqZw__Unknown__… (Karaoke).mp4` (50 MB, `is_download:true`, `youtube_id:RlBlAKxyqZw`) | a **YouTube re-download** of that same Nomad upload |

- The YT file surfaced **twice**: once as a `local` row ("From YouTube — Unverified") and once
  as a Karaoke Nerds **"✓ Downloaded"** row (its `youtube_url` resolves to the same on-disk
  path). Nothing dedups `local` ↔ `karaoke_nerds` for the same resolved path. The id-match is
  **frontend-only** (`extractYouTubeId(KN url)` vs `localMediaItems[].youtube_id`); backend
  `local_results` carry no `youtube_id`, and the `in_library` flag is computed but **dead** in
  the flat search path.
- All three "look the same" because YouTube id `RlBlAKxyqZw` **is** the Nomad upload of the
  same production as the master.
- The odd-looking row was the only one rendered by `renderRotKnRow` (`.kn-track` template,
  `0.85em`, grey title, brand-name + Community pill, single line, green left-accent) amid
  `renderRotLocalRow` rows (`.kn-local-match`, `0.8em`, green title, two-line title+folder).
  The v0.43.2 polish had only aligned the right-hand tag column, not the left content block.

**Scale across the library:** 89 songs (later 95 with the shared normalizer, of which 13 are
rotation-linked) have both a master and a redundant YT re-download; **393** verified litter
files (`.webp` thumbnails / `.part` fragments, each with a same-video-id completed playable)
plus **1 orphan** `.webp` (a *failed* download — flagged, never deleted).

## Key changes

### A — `kj-controller/scripts/cleanup_redundant_downloads.py` (+ unit tests)
- Standalone, **dry-run by default**; `--execute` deletes litter and **quarantines** twins
  (reversible move to `_redundant_quarantine/` + `.reason.txt`, mirroring
  `media._quarantine_download`).
- **Litter rule:** a `.webp`/`.part`/`.fNNN.*` is deleted only when a **same-video-id**
  completed playable exists (matched by video id, since a re-download's `__channel__` string
  can differ from the finished file's). No completed companion → **orphan**, flagged only.
- **Twin rule:** a YTDownloads playable whose normalized song key matches a `NOMAD-#### …`
  master → quarantine. Reuses `text_normalize.normalize`, `catalog.parse_karaoke_filename`,
  `utils.parse_youtube_filename`.
- **Rotation safety:** never removes a path referenced by `rotation_entries.file_path` **or**
  `rotation_archive.file_path` (NFC/NFD-tolerant). This guard did not exist in the `/delete`
  route.
- Reports CSV/MD; triggers a `/rescan` after `--execute`.
- 13 pure-logic unit tests in `tests/unit/test_cleanup_redundant_downloads.py`.

### B — Same-file search dedup (`static/app.js`, `renderRotSearchDropdown`)
- Before building the row list, compute the set of local paths **claimed** by a downloaded KN
  track (`extractYouTubeId(youtube_url)` → `downloadedIdToPath`) and drop the redundant local
  row. The KN community row (nicer grouping) survives; when no KN row claims the file the
  local row stays, so there is always ≥1 row. Frontend-only; no backend change.

### C — One unified rotation-search row template (`static/app.js` + `static/style.css`)
- New `renderRotRowHtml(opts)` skeleton (`.rs-row` / `.rs-main` / `.rs-line` / `.rs-title` /
  `.rs-sub` / `.rs-brand-name` / `.rs-actions`). `renderRotLocalRow` / `renderRotKnRow` /
  `renderRotDivebarRow` now each compute their `result` + actions and delegate the markup.
- Action column is a **fixed 170px** so the fmt/brand pills and Link/DL buttons line up on
  every row regardless of source.
- **Removed the inline "Community" and "✓ Downloaded" pills** (per request): community is
  still shown by the section header + the green left-accent on `.rs-row.community`; "Link" vs
  "DL & Link" already signals on-disk vs download.
- The shared `.kn-track*` / `.kn-local-match` classes (also used by the main Karaoke Nerds
  browse view) were left untouched — the rotation rows use new `.rs-*` classes instead.
- Version bumped `0.49.0 → 0.50.0` (cache-bust for `app.js?v=`).

## Decisions made

- **Quarantine** YT twins rather than delete (reversible); masters are canonical.
- **Same-file dedup only** — did not activate the dead `in_library` flag to also hide KN
  downloads for songs we own (relies on fuzzy artist/title matching; could hide a wanted
  community version).
- Litter is verified safe per-file (same-id playable) before deletion; orphans surfaced
  separately as re-download candidates.

## Verification

- Cleanup logic: 13 unit tests; full unit suite green. **Live dry-run** (read-only): 393
  litter, 1 orphan (*Twenty One Pilots – Migraine*), 82 twins to quarantine, 13 twins skipped
  as rotation-linked (incl. *Books from Boxes* itself).
- B/C: rendered the real *Books from Boxes* data through the actual modified `app.js`/`style.css`
  in a Playwright harness. Dedup took rows 5 → 4 (raw "From YouTube — Unverified" duplicate
  gone). Computed styles: single title font (12.8px) and colour across all rows; fmt pills,
  brand pills and buttons pixel-aligned; no Community/Downloaded badges; no action overflow.

## Execution on NomadPC — 2026-06-30 (no show running)

Ran `--execute --relink-twins` against the live device after backing up
`rotation.db` and `media_index.json` (`*.bak-20260630-cleanup`). A `--relink-twins`
mode was added so the 13 rotation-linked twins are handled too: it re-points each
referenced twin's `rotation_entries`/`rotation_archive` row onto the master, then
quarantines the twin (function `relink_references`, unit-tested, NFC/NFD-tolerant).

Quarantine moves to a **sibling** of the download folder
(`/opt/nomad/_redundant_quarantine`), not a subdir — `media.scan()` only skips
`_playability_quarantine`, so a quarantine dir *inside* an indexed `media_folders`
path would be re-indexed on rescan and the twins would reappear.

**Result (verified):**
- 393 litter deleted; **95** twins quarantined (all `videoid__…`, reversible); 1 orphan
  webp (*Migraine*) preserved.
- **14 rotation rows re-linked** to masters (13 twins; one twin was referenced by both a
  live entry and an archive row). YTDownloads-linked rotation rows 464 → 450.
- Post-checks: **0** rotation rows point at the quarantine; **0** broken links *caused* by
  the cleanup; `media_index` has 0 quarantined paths and masters intact; YTDownloads
  1575 → 1087.

**Pre-existing issue discovered (out of scope, NOT caused by this run):** 9 rotation rows
(7 archive, 2 live) reference `divebar__…` mirror files that were already missing before
this run (confirmed absent from the pre-run index backup). The cleanup never touches
`divebar__` files (7-char prefix, never matches the 11-char video-id twin logic). Worth a
separate pass to re-download or unlink those.

## Future considerations
- The deeper fix for "we own this song, hide the KN download option" (activating `in_library`)
  remains a deliberate non-goal until a robust song-identity key exists — see
  `project_song_text_normalization`.
- Mirror parsing that yields `brand_code: None` rows is a divebar/BigQuery-pipeline concern,
  out of scope here.
