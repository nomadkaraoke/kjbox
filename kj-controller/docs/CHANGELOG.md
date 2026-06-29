# KJ Controller — Changelog

Dated entries, newest first. Each entry notes any required deploy steps.

---

## 2026-06-29 - Rotation "Link song" search UX overhaul (v0.43.0)

**Why:** Linking a song to a rotation entry was slow and noisy during live shows:
the initial search didn't fire until you typed a throwaway character; there was no
"searching" feedback so you'd mash space and waste scrapes; clicking anywhere off the
results list wiped them (forcing a multi-second re-scrape); file type and download
source were shown inconsistently; popular tracks buried the good versions under 50+
low-quality commercial cover-band downloads; and the "Unknown" section lumped the
trusted 4TB-SSD library together with messy YouTube downloads.

**What (all in the rotation Link/Add search dropdown):**
- **Instant initial search.** Opening Link now fires the search immediately. Root cause
  of the old lag was a global "close on outside click" handler that fired on the *same*
  click that opened link mode and cleared the pending search timer — that handler is
  removed (#1, #4). The list now persists until Cancel / Esc / a completed link.
- **Dedicated Search button + "Searching…" indicator** beside the song input, so you can
  re-trigger on demand and always see when a search is in flight (#2).
- **File-type badge on every row** (mp4 / cdg+mp3 / …) and a **source badge** (GCS / Drive
  / YouTube) beside every "DL & Link" button so you know what you're downloading (#3).
- **Collapse low-quality commercial noise.** Non-"KJ-stated" commercial *download* options
  fold under a "▸ N more commercial versions to download" expander whenever a good option
  (community, or a stated-commercial brand) is already visible. The trusted set is the
  single source of truth already in `version_priority.py` (`COMMERCIAL_STATED` /
  `COMMUNITY_STATED`); stars now mark stated brands (#5A).
- **Meaningful groups instead of "Unknown."** Unknown-brand local files are split by the
  backend (`local_grouping.py`) into "Library — Karaoke - Digital/Active|Dead|…" (by SSD
  folder) and "From YouTube — community brand" vs "From YouTube — unverified" (by filename
  brand / our GCS-mirror naming / cross-reference against community brands in the same
  search) (#5B). Recognized-brand local files still sort into Community/Commercial.

**Deploy:** kj-autodeploy is OFF — deploy manually (`git pull` + service restart). This
release includes backend changes (`routes.py`, `version_priority.py`, new
`local_grouping.py`), so a **service restart is required** and will interrupt active
playback — restart between singers.

---

## 2026-06-04 — Fix stale results in rotation link search (v0.35.1)

**What changed**
- `doRotationSearch` (`static/app.js`) now bumps `rotSearchGen` at the start of every
  query, so an earlier, slower in-flight `/rotation/search` request supersedes itself
  and is discarded instead of rendered. Fixes the bug where the link autocomplete would
  show results for an *earlier* partial query (e.g. `frank`) after you'd finished typing
  a fuller one (e.g. `frank might`) — caused by out-of-order responses, since the live
  Karaoke Nerds scrape has highly variable latency (broad queries return more, slower).
  The latest query now always wins regardless of which response lands last.
- Input debounce raised 300ms → 700ms. `/rotation/search` does a live Karaoke Nerds
  scrape, so a longer debounce avoids firing intermediate queries on every keystroke
  (fewer wasted scrapes). Correctness is guaranteed by `rotSearchGen`, not the debounce.
- New e2e regression test `TestRotationSearchRace` reproduces the race in a real browser
  (shims `window.fetch` so a broad query resolves after a narrow one) — fails without the
  guard, passes with it.

**Deploy steps**
- Frontend + version-bump only (no Python behavior change). Auto-deploy pulls the code;
  the version bump (0.35.0 → 0.35.1) busts the `app.js?v=` cache so browsers load the fix
  on next refresh. No service restart required.

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
