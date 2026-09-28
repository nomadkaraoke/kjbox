# Song identification — implementation plan (2026-09-27)

Design, data sources and decisions: [`../SONG-IDENTIFICATION.md`](../SONG-IDENTIFICATION.md). Read that first.

## Origin (Andrew's words, condensed)

1. "remind me if any of our recent sessions tackled adding the autocomplete / musicbrainz-powered song
   database match thing to the kjbox singer song search … i'd expect this to match the real song and fix the
   capitalization, perhaps pre-fill the artist/title into the Generate on demand form"
2. "i said i wanted it to do the same (ideally reusing code paths) as the karaoke-gen job creation flow does"
   ("Tidied to Rihanna — Push Up on Me")
3. "i'm a bit worried by relying on gemini for anything here as that could end up costing a bunch of money"
4. "we should be able to design and build a solution which can leverage our database of all known artists and
   songs to support effectively matching/correcting the fuzzy, misspelled and messy user inputs drunken users
   enter without sacrificing speed (… a deterministic database query to our own cloud DBs, or possibly even a
   pre-fetched lookup table downloaded to the kjbox)"
5. After learning gen's job form uses Gemini: "i am actually more open to including use of gemini … but ideally
   i'd still like to use something on-device for the majority of 'easy' cases … then only making an API call to
   gemini if the easy matching system didn't match anything with high confidence" + support "that song from
   Titanic" + "make sure the UX is designed in a way which makes song suggestions/did you mean results separate
   from _karaoke search_ results".

## Phases

### Phase 0: groundwork ✅ (2026-09-27)
- [x] Trace how gen's job form corrects (Gemini `full` pass; the catalogue is prefix-only). Confirmed in prod logs.
- [x] Diagnose why kjbox `fuzzy_match` can't identify songs (data, gate, whole-string scoring).
- [x] Survey data sources and measure Spotify index size by popularity band.
- [x] Test set v1: `kj-controller/tests/fixtures/song_id_eval.jsonl` (97 cases, from NomadPC and gen logs plus manual cases).
- [x] Design doc `docs/SONG-IDENTIFICATION.md`.
- [ ] Andrew reviews the test set labels (the `label` field says Claude set them).

### Phase 1: index export (GCP → GCS). Code done; **deploy pending (Andrew, GCP write)**
- [x] Prototype export run locally (111 s, 2.0M rows, 37 MB gz) → `/tmp/song_id/songs.tsv.gz`
- [x] gen PR #1066: `kn-data-sync` `full` mode `EXPORT DATA` → `song-id/<run>/` + `latest.json` manifest, keeps 3 runs (dry run: 160 MB scanned/day)
- [ ] Deploy #1066 (`deploy.sh` + `pulumi up`) and trigger one run
- [x] (original task) BigQuery export query that writes songs as `artist, title, popularity, karaoke_flag, source`:
  - `spotify_tracks_normalized` (popularity cut-off chosen by test set coverage vs size; start ≥ 30)
  - UNION `karaokenerds_raw` (karaoke_flag = 1; artists/titles not in Spotify are added with a neutral popularity)
  - dedupe on normalised artist + title, keeping the best display spelling (highest popularity)
  - optionally MusicBrainz artist aliases for canonical spellings
- [x] Decide where the job lives: inside `kn-data-sync` (Cloud Run job + scheduler, weekly), writing
      `gs://nomadkaraoke-kn-data/song-id/songs-latest.json.gz` (or Parquet) plus a hash.
- [x] Measure: export 37 MB gz; SQLite 322 MB (1.98M songs, 497K words), 65 s build on the Mac.

### Phase 2: on-device index + matcher (kjbox). Prototype done; NomadPC latency still to measure
Results: `docs/SONG-IDENTIFICATION.md` §8 (78% auto / 0 wrong on real queries; 95% on held-out synthetic).
- [x] `sync_catalogs.py`: new source → `song_id.db` (artists table with popularity + trigram FTS; songs table
      with artist_id, title, popularity, karaoke_flag + title trigram FTS; sound-alike keys). Atomic swap + reload
      as for the mirror.
- [x] `song_identify.py`: `identify(query) -> {status: confident|candidates|none, song, candidates, kind}`
      following design §6 (artist sub-spans at any position, title within the artist, title-only path, margin gate).
- [x] `scripts/song_id_eval.py`: runs the test set and prints hit rate / wrong auto-applies / none rate by `kind`,
      plus p50/p95 latency on NomadPC.
- [~] Tune until (met on the Mac; p95 on NomadPC unmeasured): exact, title-only, artist-fragment and typo cases ≥ 90% hit; wrong auto-apply ≈ 0;
      p95 < 50 ms on NomadPC.
- [x] Unit tests for the matcher (the test set as a regression test, with a threshold).

### Phase 3: singer UI (kjbox)
- [ ] `/sing/search` returns an `identified` block alongside the karaoke results (or a separate
      `/sing/search/identify` call if that's faster in the UI).
- [ ] Song card vs karaoke results split (design §5); "Which one?" list; "Tidied to" line for cosmetic-only changes.
- [ ] Karaoke results for the identified song; make-it pre-fill (keep the #255 per-field logic).
- [ ] "Can't remember the name? Describe it" entry point → Gemini path.
- [ ] i18n for all locales; e2e tests.

### Phase 4: Gemini fallback (gen)
- [ ] Rework gen #1065: the free-text resolver returns `{kind, song, candidates[]}` and handles descriptions
      ("that song from Titanic") with up to about 4 candidates.
- [ ] kjbox calls it only when on-device is `none` / low-confidence on an empty karaoke search, or explicitly via "Describe it".
- [ ] Log fallback count per night (a cost sanity check).

### Phase 5: ship + observe
- [ ] Deploy (gen first, then kjbox). Watch the first show: fallback rate, wrong corrections, latency.
- [ ] Keep growing the test set from real misses (NomadPC journal only keeps about a week, so snapshot it).

### Side-fix
- [x] kjbox #256: `text_normalize` feat/ft word boundary (NORMALIZER_VERSION 2). Needs reindex + mirror sync on deploy.

### 2026-09-28 progress
- [x] Andrew reviewed test-set labels: OK. Asked for a persistent choice log → `search_log.py` + `scripts/search_log_report.py` (D11).
- [x] Freshness: switched the index to a MusicBrainz backbone (D5/D5a). v3 = 5.43M songs; matcher sped up ~2× (see design §8).
- [x] ListenBrainz import handed off (workspace `docs/archive/2026-09-28-listenbrainz-import-handoff.md` + BACKLOG inbox).
- [x] kjbox #256 merged + deployed; NomadPC SSD reindex (398K rows) + mirror rebuild done.
- [x] Phase 3 UI: song card / "Which song do you mean?" / karaoke-for heading / describe link / choice logging; e2e tests.
- [x] Phase 4: gen #1065 prompt handles descriptions (live-checked: Titanic → My Heart Will Go On; Top Gun → ambiguous list).
- [x] gen #1066 merged; `kn-data-sync` redeployed with `gcloud functions deploy` (the live function isn't tracking Pulumi's source object); first export run `20260928-055228` (111 shards, 126 MB)
- [x] gen #1065 merged + deployed (backend Cloud Run); kjbox #255 (v0.123.0) merged + deployed; `nomad-catalog-sync` built `song_id.db` on the NomadPC (5,432,020 songs, ~11 min incl. mirror, ~4 GB RAM peak)
- [x] NomadPC latency: identify ~60–160 ms per call live; test sets p50 ~105 / p95 ~290 ms
- [x] Follow-ups found live and shipped: #257 (MB misspelled duplicates block confidence, v0.123.1), #258 (don't cache transient Gemini misses, v0.123.2), #259 (song card shows before the slow typed-text karaoke search, v0.123.3; card in ~1.3 s incl. debounce)

## Next (after a few live shows)
- Run `scripts/search_log_report.py --days 7` on the NomadPC and review the flagged sessions (undone tidies, "not it?", lower candidates, edited make-it pre-fills, make-its with no identification).
- Add real misses to `kj-controller/tests/fixtures/song_id_eval.jsonl`; re-tune with `scripts/song_id_eval.py <db> [--frozen]`.
- When the ListenBrainz import lands (handoff doc), add its popularity to `SONG_ID_SQL` in karaoke-gen `infrastructure/functions/kn_data_sync/main.py`.

## Status of earlier PRs
- gen #1065 (Gemini split → `judge_match` catalog tidy) is **held**. Its free-text resolver becomes the Phase 4 fallback.
- kjbox #255 ("Tidied to" + make-it pre-fill) is **held** and becomes Phase 3's base (notice + per-field pre-fill logic).
