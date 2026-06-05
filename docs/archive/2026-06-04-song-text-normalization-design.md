# Unified Song-Text Normalization & Fuzzy Matching — Design

**Date:** 2026-06-04
**Project:** kjbox / kj-controller
**Branch:** `feat/sess-20260604-1812-song-normalization`
**Status:** Design — approved, pending spec review

---

## 1. Problem & Motivation

### The reported bug
The rotation "Link song" modal searched for `Sound Of Silence - Simon and Garfunkel`
and showed **zero** local-library/catalog matches, even though the catalog holds 32
"Simon & Garfunkel - Sound Of Silence" files.

**Root cause (verified by reproduction):** the query token `and` (from "Simon **and**
Garfunkel") never matches catalog entries stored as "Simon **&** Garfunkel".

- `catalog._fts5_safe_query` AND-joins every query term, including `and`:
  `"Sound" "Of" "Silence" "Simon" "and" "Garfunkel"*`
- The FTS5 `unicode61` tokenizer drops `&` as punctuation, so **no `and` token exists**
  in the index → FTS5 MATCH returns nothing.
- The `_like_fallback` also requires the literal substring `and` → also nothing.
- The naive term-matching in `routes.unified_search` for downloaded media likewise
  requires every term incl. `and`.

Reproduction (real functions + in-memory FTS5):
```
MATCH with "and":    []                                  ← link modal query
MATCH without "and": [('Simon & Garfunkel','Sound Of Silence')]   ← matches fine
LIKE all-terms-present? False                             ← fallback also fails on "and"
```

### The broader problem
`and`/`&` is one instance of a **class** of normalization gaps. The codebase currently
has **three divergent normalizers** and several search paths with *no* normalization:

| Concern | Current implementation | Gaps |
|---|---|---|
| Catalog FTS search | `catalog._normalize_for_search` (diacritics + Latin map) | no `&`/`and`, no numbers, no feat, no abbrev |
| Result grouping | `routes._normalize_song_key` (feat/parens/apostrophe strip) | no diacritics; incompatible with search normalizer |
| Frontend local filter | `app.js normalizeForSearch` (diacritics + Latin map) | hand-mirrored; can drift from Python |
| Downloaded-media filter | `routes.unified_search` naive `lower()` + punct strip | no diacritics, no canonicalization |
| KN / divebar / youtube | none (raw query) | n/a (external engines) |

This means a query matches in one surface and silently fails in another, and there is
no single place to fix a normalization rule.

### Goals
1. One shared, well-tested normalization mechanism used by **every** song-text search.
2. Handle the whole bug class: `&`↔`and`, numbers↔words, diacritics/Unicode,
   punctuation/apostrophes, `feat.`/`ft.`, common abbreviations.
3. Tolerate typos via a fuzzy fallback (rapidfuzz).
4. High confidence backed by hundreds of real-world-derived test cases + a
   recall/precision metrics harness.
5. DRY: remove the divergent normalizers; Python and JS share the same rules and a
   cross-language parity test.

### Non-goals
- Changing third-party search engines' (KaraokeNerds, Divebar, YouTube) internal matching.
- Phonetic matching (Soundex/Metaphone) — out of scope this pass.
- Stripping bracketed qualifiers (`(Live)`, `(Power Version)`) — deliberately **kept**
  searchable (see §4 decisions).

---

## 2. Approved Decisions

| # | Decision | Choice |
|---|---|---|
| D1 | Test-data source | **Hybrid**: synthesize messy variants from the real 415K catalog **and** fold in any reachable real Google-Sheet rotation samples as a regression layer |
| D2 | Ambition | **Deterministic canonicalizer + fuzzy typo layer** (Approach B) |
| D3 | Transforms in scope | numbers↔words, strip `feat.`/`ft.`/`featuring`, common abbreviations (incl. `&`→`and`) |
| D4 | Bracketed qualifiers | **Not stripped** — `(Live)`/`(Power Version)` stay searchable; grouping therefore becomes more granular (accepted) |
| D5 | `rapidfuzz` dependency | **Approved** |

---

## 3. Architecture

### 3.1 New module: `kj-controller/text_normalize.py`
The single source of truth. Pure functions — **no Flask, no SQLite** — so it is trivially
unit-testable and importable from any layer.

**Canonicalization pipeline** (applied *identically* to indexed text and to queries, so
both sides meet in one canonical space — this is what makes `&`↔`and` work):

1. Unicode `NFD` decompose + strip combining marks (`̀-ͯ`) + `LATIN_SPECIAL_MAP`
   fold (`ø→o`, `æ→ae`, `ß→ss`, …) — reuses today's map.
2. lowercase.
3. strip featured-artist qualifiers: `feat.` / `ft.` / `featuring …` (regex).
4. expand abbreviations via curated `ABBREV_MAP` applied token-wise: `&`→`and`,
   `pt`→`part`, `vs`→`versus`, `w/`→`with`, … (small, documented).
5. numbers → canonical digit form via curated `NUMBER_MAP`: word→digit for 0–100, tens,
   and roman numerals I–X. (Direction: **digits are canonical**, since the catalog
   predominantly uses digits.) Documented coverage limits; compound parsing
   ("one eighty two") is **not** attempted in v1.
6. drop apostrophes (`don't`→`dont`); replace remaining punctuation with space; collapse
   whitespace.

**Public API**
```python
normalize(text: str) -> str            # canonical space-joined token string (index + query)
tokens(text: str) -> list[str]         # canonical tokens
fts_match_query(text: str) -> str      # FTS5-safe quoted+prefix query (absorbs _fts5_safe_query)
group_key(artist: str, title: str) -> str   # "<artist>|||<title>" (absorbs _normalize_song_key)
```

**Exported constants** (defined once, importable + serialised to JS):
`LATIN_SPECIAL_MAP`, `ABBREV_MAP`, `NUMBER_MAP`, `NORMALIZER_VERSION`.

### 3.2 Fuzzy matching layer (catalog, backend only)
- **Primary:** canonical FTS5 MATCH over normalized tokens (fast, ranked).
- **Fallback** (when primary returns fewer than a small threshold of hits): `rapidfuzz`
  scoring (`token_set_ratio` / `WRatio`) over a **candidate pool**, not all 415K rows.
  Candidate pool is narrowed first by a permissive query (FTS5 `trigram` index or a
  relaxed prefix MATCH) so fuzzy scoring stays cheap. A tuned score cutoff filters
  results; ranked by score.
- Fuzzy is **backend-side only** (the big catalog). The JS frontend keeps
  canonical-substring matching for the small in-memory downloaded-media list — no fuzzy,
  to avoid JS/Python fuzzy parity burden.

### 3.3 DRY integration — every call site routes through the module
| File | Today | After |
|---|---|---|
| `catalog.py` | `_normalize_for_search`, `_fts5_safe_query`, `_like_fallback`, `_flush_batch` index-time normalize | delegate all to `text_normalize`; add fuzzy fallback to `search()` |
| `routes.py` | `_normalize_song_key`; `unified_search` naive local-media filter | `group_key`; local-media filter uses `tokens()` |
| `app.js` | `normalizeForSearch`, `filterLocalMedia` | mirrored JS pipeline driven by injected maps |
| KN/divebar/youtube | raw query | send a lightly-cleaned query (feat/punct tidy); engines keep their own matching |

### 3.4 Frontend parity
Maps (`LATIN_SPECIAL_MAP`, `ABBREV_MAP`, `NUMBER_MAP`, `NORMALIZER_VERSION`) are injected
into `window.KJ_CONFIG` (today only the Latin map is). The JS pipeline mirrors the Python
stages. A **parity test** runs the shared corpus through both Python and the JS normalizer
(via node) and asserts identical canonical output, extending the existing
`TestNormalizationConsistency`.

### 3.5 Reindex (required migration)
Because index-time normalization changes, the `media_fts` shadow table in
`external_media.db` must be **rebuilt** (the `media` table keeps the originals for display;
only the FTS shadow stores normalized tokens). Deliver:
- a `reindex` management command (rebuild `media_fts` from `media`, batched),
- a `NORMALIZER_VERSION` stamp persisted in catalog metadata, and a startup/health check
  that warns when the index was built with a stale normalizer version,
- (if a trigram candidate index is used for fuzzy) build it in the same pass.

~415K rows; runs on-device. **Deploy must run reindex** or search returns stale results.

---

## 4. Data Flow (link-modal example, after change)

```
"Sound Of Silence - Simon and Garfunkel"
        │  text_normalize.normalize (both query & index used this)
        ▼
"sound of silence simon and garfunkel"        ← "&" indexed as "and" too
        │  fts_match_query
        ▼
"sound" "of" "silence" "simon" "and" "garfunkel"*
        │  FTS5 MATCH on media_fts (normalized)
        ▼
[KK7008-03 …, SC2414-02 …, ZOOM-02990 …, …]   ← 32 hits now returned
        │  (if <threshold) rapidfuzz fallback over candidate pool
        ▼
ranked local results shown in the Link modal alongside KaraokeNerds
```

---

## 5. Testing & Confidence (the core deliverable)

### 5.1 Deterministic golden unit tests
Hundreds of `normalize(input) == expected` assertions, one cluster per transform
(diacritics, Latin map, `&`/`and`, numbers↔words, feat, abbrev, apostrophes, punctuation,
whitespace). Exact assertions — no probabilistic checks here.

### 5.2 Synthesized end-to-end corpus (deterministic)
From a deterministic sample of real catalog rows, generate messy query variants:
`and`↔`&`, digit↔word, dropped apostrophe, dropped diacritic, added `feat. X`, added
`(Live)`, abbreviations, and seeded single-character typos. **Invariant:** each variant
must retrieve its source row in top-K. Reproducible (fixed seed / fixed sample).

### 5.3 Hybrid real-world regression layer (D1)
Where the Google-Sheet rotation backup (or other real request logs) is reachable, capture
real `song_artist` strings and label each with its expected catalog match (or "no match").
Stored as a committed fixture so it runs offline thereafter.

### 5.4 Metrics harness
A script that runs the whole corpus and reports **recall@K and precision** for
(a) canonical-only and (b) canonical+fuzzy, plus per-transform breakdowns. Purpose:
- tune the fuzzy score cutoff against data, not vibes;
- produce a baseline number we can cite for "high confidence";
- guard against regressions (CI can assert recall/precision floors).

### 5.5 Cross-language parity test
Shared corpus → assert Python `normalize` output == JS `normalizeForSearch` output.

---

## 6. Risks & Edge Cases

| Risk | Mitigation |
|---|---|
| numbers↔words vs token boundaries ("U2" = 1 token, "u two" = 2) | corpus quantifies impact; consider an adjacent letter+digit join rule if recall suffers |
| `group_key` no longer strips brackets → finer-grained version groups in singer UI | accepted per D4; verify singer-UI grouping still reads well |
| reindex not run on deploy → stale/empty search | `NORMALIZER_VERSION` stamp + startup warning; document in deploy steps |
| fuzzy false positives | tuned cutoff + precision floor in metrics harness; fuzzy only as fallback |
| rapidfuzz over 415K cost | candidate-pool narrowing before scoring; measure in harness |
| JS/Python drift | parity test on shared corpus in CI |

---

## 7. Out of Scope / Follow-ups
- Phonetic matching (Metaphone/Soundex).
- Compound number-word parsing ("one eighty two" → 182).
- Frontend fuzzy matching for downloaded-media list.
- Replacing third-party engines' matching.
