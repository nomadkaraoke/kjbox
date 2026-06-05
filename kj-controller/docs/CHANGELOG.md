# KJ Controller — Changelog

Dated entries, newest first. Each entry notes any required deploy steps.

---

## 2026-06-04 — Unified song-text normalization + fuzzy matching

**What changed**
- New `text_normalize.py` (+ `static/text_normalize.js` twin, node-parity-tested) is
  the single source of truth for search normalization: `&`↔`and`, numbers↔words,
  diacritics/Unicode, `feat.`, abbreviations (pt/vs), apostrophes/punctuation. Every
  search call site (catalog FTS index+query, the `media_trigram` index,
  `routes.unified_search` local filter, `routes._normalize_song_key` grouping, and the
  frontend `app.js`) now routes through it. Fixes the "Simon and Garfunkel" vs
  "Simon & Garfunkel" link bug.
- rapidfuzz fuzzy fallback for typos (token-overlap gated, bm25-ranked `media_trigram`
  candidates). **New dependency: `rapidfuzz` (in `requirements.txt`).**
- `NORMALIZER_VERSION` stamp + `index_is_stale()` startup warning; `scripts/reindex_catalog.py`;
  recall metrics harness (`scripts/search_metrics.py`); 975 real rotation-history queries
  captured for regression testing. Decision: bracketed qualifiers like `(Live)` are NOT
  stripped, so version grouping is more granular than before.

**DEPLOY PROCEDURE (required for any normalizer change — order matters)**
Auto-deploy only `git pull`s; it does NOT install deps, reindex, or restart. On each device:
1. `git pull` (auto-deploy handles this).
2. **Install new deps in the venv** — `catalog.py` imports `rapidfuzz` at module load,
   so the service will NOT restart without it:
   `/opt/nomad/kjbox/kj-controller/venv/bin/pip install -r requirements.txt`
3. **Reindex** (full rebuild of FTS + trigram over ~398K rows; `reindex_catalog.py` calls
   `init_schema()` first, so it creates `media_trigram`/`catalog_meta` on a pre-existing DB):
   `/opt/nomad/kjbox/kj-controller/venv/bin/python scripts/reindex_catalog.py`
4. **Restart** (interrupts playback): `sudo systemctl restart kj-controller`
   (backend change — must restart to take effect; service runs on 127.0.0.1:5001).
Until reindexed, the query normalizer and the index disagree → degraded search + a stale-index
log warning.

**Deployment status**
- NomadPC: DEPLOYED + verified live 2026-06-04 (398,446 rows reindexed; `/search` for
  "Sound Of Silence - Simon and Garfunkel" returns the "& Garfunkel" rows).
- NomadPi: NOT yet deployed — run the procedure above if it's used for shows.
