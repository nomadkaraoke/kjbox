# Surface GCS Mirror (Divebar) Versions in the Rotation Track Selector

**Date:** 2026-06-25
**Worktree:** `kjbox-community-download-source`
**Branch:** `feat/sess-20260625-2125-community-download-source`

## Problem (diagnosed live against nomadpc, read-only)

When a KJ selects a **community** version in the rotation track selector, it almost
always downloads from **YouTube** (yt-dlp), even when our own GCS community mirror
has the song. The "prefer GCS" machinery already exists — the frontend picks
`local → divebar(GCS) → youtube` (`app.js:5464`) and a matched divebar file resolves
to `storage.googleapis.com/nomadkaraoke-divebar-files/…` (verified live) — but a KN row
only gets a `divebar` tag when the backend cross-ref matches **exactly** on
`(artist, title, brand_code)` lowercased (`routes.py:3237-3250`). That exact match
rarely fires for community brands.

### Evidence (live `/rotation/search`)
| Community pick | In GCS mirror? | Cross-ref | Source today |
|---|---|---|---|
| Incubus – I Miss You (FATBIRD) | yes, clean | ✅ match | **GCS** |
| Incubus – Anna Molly (FBK) | **yes** (`FBK204`, in_gcs) | ❌ `FBK`≠`FBK204` | YouTube *(fixable)* |
| Incubus – Love Hurts (CC) | **no** (mirror has 0) | ❌ no entry | YouTube *(unavoidable; local `CCK-01298` exists)* |

Two failure modes: **(1)** brittle exact match defeats songs that *are* mirrored
(brand-code granularity `FBK`/`FBK204`, garbled mirror parsing like
`CKK | Incubus - Admiration | brand_code=None`, casing/`&`-vs-`and`); **(2)** the mirror
is a subset and genuinely lacks some versions (not fixable in kjbox).

## Chosen direction (user)

> Robust matching (same brand) + cross-brand GCS substitution, delivered by
> **surfacing GCS-mirror versions as their own selectable rows** in the autocomplete,
> so the KJ can pick a mirror version over a YouTube one when both exist.

Today `divebar.search()` results are computed in `unified_search`, used only to silently
annotate KN rows, then **discarded**. We will instead surface them as first-class rows.

## Design

### Backend — `unified_search` (flat path), `routes.py`
1. **Search divebar independently of KN** (not gated on `kn_results`), best-effort —
   so GCS rows appear even when KN is slow/empty. Keep the 10s timeout + try/except.
2. **Canonical cross-ref** (replaces raw `brand_code` key). Build divebar index keyed by
   `(_normalize_song_key(artist,title), canonical_brand)` where `canonical_brand =
   version_priority.resolve_brand(brand_code=…, brand_name=…)[0]`. Look up KN tracks by the
   same key. Fixes `FBK`/`FBK204`, casing, punctuation, `&`/`and`. → "robust same-brand".
3. **Dedup + surface.** Partition divebar entries:
   - matches a **local** result (same norm-song + canonical brand) → drop (local link is best);
   - matches a **KN track** of same canonical brand → attach as `track["divebar"]` (KN row now
     downloads from GCS — existing behavior, now actually firing);
   - otherwise → emit as a **standalone divebar row** (brands KN didn't return → "cross-brand").
   Collapse standalone rows to **one per canonical brand** (prefer `in_gcs=True`, then
   smaller file) to avoid noise from the 100-row search.
4. **Annotate** standalone divebar rows with `version_priority.annotate_versions(…,
   shape="rotation_search_divebar")` so they sort into the right section (community/commercial)
   by the existing `priority_rank`, with `SOURCE_TIEBREAKER_DIVEBAR` already defined.
5. Return new key in the flat response: `{"local":…, "karaoke_nerds":…, "divebar":[…]}`.
   Keep legacy keys/shape intact (back-compat).

### version_priority.py
- Add `shape="rotation_search_divebar"` to `annotate_versions` (extract `brand_code`/
  `brand_name`; let classification fall out — mirror has both community & commercial brands).
- `_source_tiebreaker` / `_extract_brand_inputs`: handle a divebar row shape
  (`{file_id, brand_code, brand_name, …}`) → DIVEBAR tiebreaker.

### Frontend — `renderRotSearchDropdown`, `app.js`
- Read `data.divebar`; push `kind:'divebar'` rows into the unified `rows[]` (sorted by
  `priority_rank`, sectioned by `priority_class` — lands in Community automatically).
- New `renderRotDivebarRow(...)`: GCS/Drive badge (reuse `dl-source-*` styling), "DL & Link"
  button; build `result = {type:'divebar', file_id, artist, title, brand_code}` — already
  supported by `selectRotSearchResult` → `buildCall` → `/rotation/download-and-link`.
- Dedup against already-downloaded media (the `downloadedIdToPath`/✓ Downloaded path) where a
  local file already covers it.

## Out of scope (flag separately)
- The mirror's **garbled artist/title/brand_code parsing** is a divebar-pipeline
  (karaoke-decide / BigQuery index) issue, not kjbox. Fixing it would recover more matches but
  is a different repo.
- Grouped/singer-facing path (`_group_search_results`) — extend later if wanted; image is the
  flat admin path.

## Testing
- Unit: canonical cross-ref (FBK↔FBK204, casing, `&`/`and`); dedup partition (local-drop /
  KN-attach / standalone); one-row-per-brand collapse; divebar annotate shape; ranking places
  divebar community rows in community tier.
- Regression: existing exact-match still matches; flat response back-compat keys present.
- Manual: live `/rotation/search` on a dev run proxying prod divebar API — confirm Anna Molly
  now offers a GCS row.

## Deploy notes (PRODUCTION SAFETY)
- Backend change (`routes.py`, `version_priority.py`) → **requires service restart**, which
  interrupts active playback. Must deploy between shows / with explicit permission.
- Frontend (`app.js`) → effective on browser refresh, no interruption (but push triggers
  `git pull` on device → still needs permission).
- kj-autodeploy is OFF — deploy kjbox manually.
