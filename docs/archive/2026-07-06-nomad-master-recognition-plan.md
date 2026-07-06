# NOMAD master recognition + KN YouTube-row suppression

**Date:** 2026-07-06
**Repo:** kjbox (`kj-controller/`)
**Status:** design approved, implementing

## Problem

In the rotation "Link song" search (and the singer-facing grouped search), a
song we already hold in the deliberate NOMAD master mirror
(`/opt/nomad/downloads/NOMAD-720p/`, e.g. `NOMAD-1272 - Maximo Park - By the
Monument.mp4`) is:

1. **Misclassified** as **"From YouTube — unverified"** instead of being
   recognised as the official NOMAD (community) release, and
2. **Not mapped** to the KaraokeNerds "Nomad Karaoke … (community)" result, so
   KN independently offers a **YouTube "DL & Link"** download for a file we
   already have on disk.

### Root cause (both confirmed by running the real functions)

**Gap 1 — the master's brand is discarded.** In `routes.py:unified_search`
(~L4072), when a downloaded file has a `media_library` row with an artist/title
(masters always do — `parse_method="master"`, confidence 1.0), the code sets
`disc_id = None` and uses the clean ml_row title. But `disc_id` is what carries
the brand:

```
resolve_brand(disc_id="NOMAD-1272")            -> ('NOMAD', 'community')   # wanted
resolve_brand(disc_id=None, filename="NOMAD-…") -> (None, 'unknown')        # today
```

NOMAD is a registered community brand (`version_priority.py:21`), but with the
disc_id gone, brand resolution falls back to the filename regex, which only
matches the YouTube `VIDEOID__Brand__` pattern — not `NOMAD-####`. So
`priority_class="unknown"`.

**Gap 2 — the unknown-brand fallback doesn't know masters exist.**
`priority_class="unknown"` + path under the download folder routes to
`local_grouping.classify_local_file`, which only recognises a `divebar__`
prefix or a YT-community brand token. A `NOMAD-####` master matches neither →
**"From YouTube — unverified"** (reproduced exactly).

**Why the KN row still offers YouTube:** `_surface_divebar_versions`
cross-references KN tracks against the divebar **GCS mirror only** — it has no
awareness of the local `NOMAD-720p` masters. The frontend same-file dedup
(`app.js:7086`) only matches a KN track to a local file via `youtube_id`;
masters carry `nomad-####` ids, so they never match.

## Design (approved)

Scope decision: **NOMAD masters only.** Redundant-KN-row decision: **suppress
the KN row entirely.** (One canonical NOMAD release per song; we fully trust
the mirror.)

### Part A — recognise the local master (`unified_search`)

Preserve the brand-bearing `disc_id` when a `media_library` row supplies the
clean artist/title. Parse the filename once; keep its `disc_id`; override only
artist/title from the ml_row. This makes the ml_row branch consistent with the
else branch (which already keeps disc_id).

Effect: `resolve_brand("NOMAD-1272")` → `('NOMAD','community')` → the master
renders in the **Best — NOMAD (community)** section with a **Play/Link** button.
No download. `classify_local_file` is no longer reached for masters (they are no
longer `priority_class="unknown"`).

Low risk: regular slug downloads (`Artist - Title [yt-id].ext`) parse to a
2-part name whose first token has no digits → `disc_id=None` (unchanged).
Only disc-numbered files (masters `NOMAD-####`, commercial `KCD-…`) get a real
disc_id — which is exactly what should drive their brand.

### Part B — suppress the redundant KN NOMAD row (new helper)

`_suppress_mastered_kn_tracks(local_results, kn_results)` — pure, in `routes.py`
near `_surface_divebar_versions`, called from `unified_search` immediately after
it (mutates `kn_results` in place before both the grouped and flat branches, so
both search surfaces are fixed at once):

1. Build the set of song-keys we hold as local masters:
   `_normalize_song_key(r.artist, r.title)` for each `local_results` row where
   `naming.classify_source(r.filename) == naming.SOURCE_MASTER`.
2. For each KN song whose key is in that set, drop tracks whose canonical brand
   resolves to **NOMAD** (`resolve_brand(...) == 'NOMAD'`). Keep every other
   brand — commercial and other-community alternatives stay selectable.

`_normalize_song_key` (→ `text_normalize.group_key`) folds case and diacritics,
so the master's DB `Maximo Park / Books from Boxes` matches KN's
`Maxïmo Park / Books From Boxes` (verified: identical key).

## Net effect

- **Screenshot 1 (KJ rotation search):** "Best — NOMAD (community)" becomes the
  local `NOMAD-720p` master → **Play**; the red-YouTube "DL & Link" NOMAD row is
  gone; nothing lands in "FROM YOUTUBE — UNVERIFIED".
- **Screenshot 2 (singer grouped search):** the master joins "IN YOUR
  COLLECTION" as **Play**; the "Nomad Karaoke … Download" row is suppressed.

## Not touched

Divebar GCS mirror logic, commercial rows, non-NOMAD brands (per scope),
download/playback plumbing. The local Best-row title keeps its `NOMAD-####`
prefix (desirable — shows the official catalog number).

## Tests (kjbox runs pytest locally; no pytest gate in CI)

- **unit (Part A):** a master local-result → `priority_class="community"`,
  `priority_brand="NOMAD"` after the `unified_search` local-annotation path
  (or a focused test of the disc_id-preservation branch).
- **unit (Part B):** `_suppress_mastered_kn_tracks` drops the NOMAD KN track when
  a master covers the song, keeps a commercial track, and is a no-op when no
  master is present.
- **integration:** `unified_search` end-to-end with a fake catalog/media index
  holding a master + a KN NOMAD track → master surfaces community, KN NOMAD
  track suppressed.

## Deploy

kjbox auto-deploy is ON and restarts kj-controller on `.py` changes (these are
`.py` changes). No live show currently. Push + let auto-deploy restart, or
restart manually with permission. Bump `pyproject.toml` version in the same PR.
