# KJ Controller — Changelog

Dated entries, newest first. Each entry notes any required deploy steps.

---

## 2026-06-04 — Unified song-text normalization + fuzzy matching

- New `text_normalize.py` (+ `static/text_normalize.js` twin) is the single source
  of truth for search normalization: &<->and, numbers<->words, diacritics, feat,
  abbreviations. Fixes the "Simon and Garfunkel" vs "Simon & Garfunkel" link bug.
- rapidfuzz fuzzy fallback for typos; new `media_trigram` index.
- **DEPLOY STEP (required):** after pulling, run
  `python scripts/reindex_catalog.py` on each device to rebuild the catalog index
  with the new normalizer (the service logs a warning if `NORMALIZER_VERSION` is stale).
