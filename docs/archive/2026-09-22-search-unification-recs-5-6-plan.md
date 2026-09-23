# Search unification recs 5–6 — plan — 2026-09-22

Continues `nomadkaraoke/docs/archive/2026-09-19-kjbox-search-unification-handoff.md` (recs 1–4
shipped; mirror live on nomadpc v0.105.1). Spec = the rec 5/6 text in that handoff.

## Rec 6 — Library local filter → shared Python engine

**New endpoint** `GET /library/search?q=&limit=` → `unified_search(query, app, local_only=True)`,
returns `{"results": [...]}` — the annotated, ranked local rows (external-catalog rows carry
`folder`; media-index rows come from `_build_local_media_row`). This is the one shared engine
(text_normalize + FTS5 + trigram + fuzzy_match), so the Library filter gains the same typo
tolerance as rotation search ("boks" finds "Books from Boxes").

**app.js `catalogSearch`**: keep the instant `filterLocalMedia` first paint (also the offline
fallback), then ONE fetch to `/library/search` (replaces the `/search` fetch). Partition the
response by `path ∈ localMediaItems` (Map file_path→item): matched → full library item objects
(then `applyMediaFilter` for the format toolbar) rendered as library rows with edit/delete;
rest → catalog rows. Render through the existing `renderUnifiedResults` unchanged.

## Rec 5 — KN panel → backend composition

**Backend**: `POST /karaoke-nerds/search` now returns the full `unified_search(query, app)`
flat payload `{local, karaoke_nerds, divebar, karaoke_nerds_timeout}` (per-song tracks sorted
by priority_rank). This gives the panel, by construction: server-side `in_library`, divebar
mirror xref (`track.divebar`), local-master suppression (`_suppress_mastered_kn_tracks`) —
identical semantics to rotation/singer search. Additionally, `unified_search` gains
`_attach_local_paths_to_kn(app, kn_results)`: joins each KN track's youtube_url video id
against the media index's `youtube_id` and sets `track.local_path` — the server-side
replacement for the panel's (and rotation search's) client-side id join.

**Frontend (app.js)**, obsolete-by-design deletions from #219:
- `renderKNResults`: drop `downloadedIdToPath` / `masterPathByNorm` / `songNorm` client
  matching. Row actions become: `track.local_path` → ✓ Downloaded + Play;
  `track.divebar.file_id` → Download (mirror) via existing `downloadDivebarTrack`;
  `youtube_url` → Download (YouTube); else Disc only.
- Delete `loadKNCatalogMatches` (per-song lazy "In your collection" with its client-side term
  filter). Replaced by an eager top-level "In your library (N)" section rendered from
  `data.local` — same query space, server-matched, typo-tolerant, ranked (masters first — a
  suppressed NOMAD row's local master shows here with Play).
- Render `data.divebar` standalone mirror rows in a small "GCS mirror" section with Download
  (reuses `downloadDivebarTrack`), so mirror-only versions aren't silently dropped.

## Tests

- Python: endpoint tests for `/library/search` (media + catalog rows, typo recall via the
  fuzzy path) and `/karaoke-nerds/search` (unified shape, sorted tracks, master suppression,
  divebar attach, local_path attach) following existing integration-test stub patterns.
- e2e: `TestKnDiscOnlyRendering` stays (render signature unchanged). `TestKnLocalMasterMatching`
  adapted, not deleted: master matching now arrives server-side, so (a) a track with
  `local_path` renders Downloaded+Play and no Download button; (b) the library section renders
  the master row with Play (was: collection section).

## Ship notes

- Backend `.py` diffs → auto-deploy WILL restart kj-controller on merge: never merge during a
  live show; get Andrew's go-ahead.
- Version bump (minor — API response shape change): 0.106.0.
