# Singer UI search UX — plan (2026-09-22)

Four workstreams from Andrew's prompt (verbatim spec in the session start message).

## 1. Auto-select "best version" — verify + reword

**Finding (verified in code):** the singer `kj_pick` auto-approve path and the KJ-side
rotation-link "Best" tag share one ranking implementation:

- Singer search (`/sing/search` → `unified_search(grouped=True)` → `_group_search_results`)
  annotates every version with `version_priority.annotate_versions(shape="kj_pick")` and
  sorts by `priority_rank` (routes.py:369-372). `versions[0]` renders the gold "Best" pill.
- Auto-approve (`sing.py` submit → `resolve_kj_pick_best` → `_ranked_version_indices`,
  routes.py:5554-5606) **re-annotates with the same function + same cfg** and binds the
  lowest-rank resolvable version.
- KJ rotation-link search sorts client-side by (group, priority_rank) where group =
  community(10) < commercial(20) < library/other; `rank_version` tiers
  (community 0-1000 < commercial 2000-3000 < unknown 4000) produce the same order, so
  `displayIdx === 0` ("Best" pill, app.js:8443) = the same version auto-pick binds.

Same brand registry, same config keys (`kn_priority_community` / `kn_priority_commercial`),
same source tiebreaker (local < divebar < youtube). Candidate-set differences exist (the KJ
search also surfaces standalone Divebar mirror rows and YouTube), but among the shared
candidates the winner is identical. Add a unit test pinning resolve_kj_pick_best to the
rank_version winner for a mixed local/divebar/KN snapshot.

**Wording changes (sing.js):**
- CTA: "Let the KJ pick the best version →" → "Auto-select best version →"
- kj_pick label: "(KJ picks best version)" → "(best version auto-selected)"
- Confirm source line: "The KJ will pick the best version" → "We'll auto-select the best
  version for you"

## 2. Version rows that help singers decide

Backend:
- `annotate_versions` gains `priority_display` (via existing `display_name_for`) so rows
  can show "Karaoke Version" instead of "KV", "Sound Choice" instead of "SC".
- New token-gated singer endpoints in `sing_bp` (public host blocks everything else):
  - `POST /sing/media-info` — same guard as `/media/info` (`_resolve_media_path`) + ffprobe.
  - `POST /sing/preview/resolve|close`, `GET /sing/preview/stream/<tok>`,
    `/sing/preview/cdg/<tok>/<part>`, `/sing/preview/hls/<tok>/<name>` — thin delegates to
    `current_app.preview` (same code the KJ modal uses). Skip `_record_preview_stat` so KJ
    audition stats stay meaningful.
  - `GET /sing/lib/<name>` — whitelist passthrough serving `static/cdg.js`,
    `static/vendor/hls.min.js` to the public host.
- 2A: tappable Community/Commercial pill per row → explainer modal (reuse existing
  cover-band vs original-audio copy).
- 2B: tappable brand pill (full display name) → brand info modal from a curated
  `BRAND_INFO` dict in sing.js (registry brands + generic fallback). NO scraping of
  karaokenerds.com (hard rule); blurbs are hand-written.
- 2C: filename/`show full path` replaced by a format pill (MP4 / CDG+MP3 / ZIP…); tap →
  technical-details modal (ffprobe via /sing/media-info for local files; known
  format/size/quality for divebar; "streams from YouTube" note for online rows).
- 2D: Preview button per row → same modal experience as KJ. Refactor `static/preview.js`
  to route requests through an overridable URL-builder hook (`window.__PREVIEW_URL`),
  default identity (KJ UI unchanged); sing.js loads preview.js + cdg.js via /sing/lib,
  creates the modal DOM, and sets the hook to prefix `${BASE}/preview` + `?t=TOKEN`.

## 3. Navigation: tabs + real back button

- Persistent bottom tab bar (sibling of #sing-mysongs-bar in sing.html):
  🎵 Request · 🎤 My songs (live count badge) · 📋 Rotation.
- New `rotation` step: full-page rotation list (reuses `_renderRotationBody`).
- Hash routing: step ↔ location.hash (`#search`, `#confirm`, `#mysongs`, `#rotation`,
  `#name`); `popstate` restores the step so browser Back navigates inside the SPA instead
  of leaving; reload restores from hash (guarded by smart-restore/identity fallbacks).
- The "My songs" bar stays (it's a status summon); tabs make sections reachable from
  anywhere.

## 4. Honest, refreshable rotation data

- Root cause of "updated just now" being stale: `_formatUpdatedAt` renders once and never
  ticks, and the 30s cache can serve an old payload into a fresh render.
- Fix: shared rotation view gets (a) a ticking "Updated Xs ago" label (10s interval),
  (b) auto-refetch every 30s while visible, (c) a manual ↻ Refresh button that bypasses
  the cache. Applies to the landing expander, done-screen expander, and the new Rotation
  tab.

## Order of implementation

1 (wording+test) → 4 (refresh) → 3 (tabs/routing) → 2 (rows: pills → modals → preview).

## Testing / deploy

- pytest unit tests for new sing endpoints + resolve_kj_pick_best pin; update
  `tests/e2e/test_sing_frontend.py` wording assertions.
- Backend changes ⇒ service restart needed on NomadPC (ask Andrew before push/restart —
  live-show safety rules in CLAUDE.md).
