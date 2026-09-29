# Song Identification — design, data sources & decisions

> **Read this before touching singer song search, "Did you mean", auto-correct/tidy, or the
> make-it (Generate on demand) pre-fill.** It records what Andrew wants, what exists today
> (including some non-obvious facts that caused earlier misunderstandings), and the reasoning
> behind each decision. Plan / progress: [`archive/2026-09-27-song-identification-plan.md`](archive/2026-09-27-song-identification-plan.md).

## 1. The two separate questions

Singer song search answers **two different questions**. They must stay separate in the code
and **especially in the UX**:

| | Question | Universe | Answered by |
|---|---|---|---|
| **1. Song identification** | *Which real song does the singer mean?* | **Every song in existence** (Spotify / MusicBrainz scale) | This document: on-device matcher → Gemini fallback |
| **2. Karaoke availability** | *Is there a karaoke version of that song we can play, and how do we get it?* | Our library, HyperMule SSD, KaraokeNerds, Divebar, YouTube, or make one with karaoke-gen | Existing `unified_search` (`routes.py`) + the empty-state triage in `sing.js` |

Once question 1 is answered, question 2 is asked **for that one song**.

**UX rule (Andrew, 2026-09-27):** never mix candidate *songs* (identification) with *karaoke
results* (availability). Mixing them "can be super confusing". The identified song, or a
short "Which one?" list, gets its own card. Karaoke rows below it are always *for* a specific
song. Picking a different identification never adds a request; it only changes what the
karaoke results are for. See §5.

## 2. What exists today (and why it was confusing)

Facts established 2026-09-27. Check them before relying on them.

- **gen's job-form "Tidied to / Corrected to" is mostly Gemini, not a database.** gen's match
  judge (`karaoke-gen/backend/services/match_judge/`) has two passes:
  1. **`fast` pass:** a catalogue lookup via `catalog_proxy_service.search_tracks` (decide
     API, see below) plus an exact-after-normalisation classifier (`classifier.py`). It is
     only confident when the typed artist/title **equals** a catalogue entry after
     case/punctuation/accent folding. That makes it a pure formatting tidy with **no typo
     tolerance**.
  2. **`full` pass:** otherwise `ai.py` asks **Vertex `gemini-3.8-flash`** (`MATCH_JUDGE_MODEL`;
     prod doesn't override the default). Gemini gets the typed text plus any catalogue
     candidates, which are often none.
  - Prod logs show most job submissions go `fast … needs_ai=True` then `full … engine=ai`.
  - Example: "the stokes" + "max picu" got zero catalogue candidates, and Gemini corrected it
    to The Strokes — Machu Picchu about 10 s later.
  - Andrew did not know gen used Gemini here. He had assumed it was database-only.
- **decide's catalogue API is prefix-only.** `GET /api/catalog/tracks` and `/artists`
  (`karaoke-decide/karaoke_decide/services/bigquery_catalog.py`) run BigQuery
  `LIKE 'q%'` on normalised fields.
  - A one-line "artist title" query finds nothing ("rihanna push up on me" → 0 results).
  - Any typo finds nothing ("max picu", "rihana", "the stroks" → 0).
  - With a correct split it works ("rihanna" + "push up on me" → Push Up On Me).
- **kjbox's typo-tolerant search** (`fuzzy_match.py`, used by `catalog.py`,
  `catalog_mirror.py` and `unified_search`) answers question 2 only, over karaoke catalogues.
  It fails for song identification for three reasons:
  1. **Data:** it only knows karaoke tracks. "rihanna push up on me" scores a perfect 1.0
     against the right song, but that song isn't in any on-box catalogue. Make-it requests are
     by definition songs *without* a karaoke version.
  2. **Strict gate:** every query word of 4+ letters must be within 1 edit (4–6 letters) or 2
     edits (7+) of a catalogue word. "picu"→"picchu" is 2 edits, so it's rejected. Words
     under 4 letters give no signal ("max"→"machu" is 3 edits).
  3. **Whole-string scoring:** it compares the query to the whole "artist title" haystack.
     There's no artist-first narrowing, so it has to stay strict for precision.
- **kjbox search auto-correct, v0.119.0 (#246).** On an empty singer search, kjbox calls gen
  `POST /api/kjbox/catalog/resolve` (`free_text.py`), which is one Gemini call to split and
  typo-fix the query.
  - The verdict was dropped when the corrected search found no karaoke songs, so the Rihanna
    case showed nothing and nothing was pre-filled.
  - Open PRs as of 2026-09-27: gen #1065 (route the Gemini split through `judge_match`) and
    kjbox #255 ("Tidied to" notice + make-it pre-fill). Both are **held**. They will be
    reworked under this design rather than merged as they are.

## 3. Data sources available

| Source | Where | Size | Notes |
|---|---|---|---|
| `spotify_tracks_normalized` | BigQuery `nomadkaraoke.karaoke_decide` | **2.08M rows / ~1.9M distinct songs** (popularity ≥ 30): 50+: 191K songs / 46K artists; 40–49: 454K / 111K; 30–39: 1.26M / 259K | Fields: `artist_name`, `track_name`, `normalized_*`, `popularity` (0–100), `duration_ms`. **Snapshot of Anna's Archive Spotify dump, July 2025**, so it misses newer releases (e.g. Breaking Rust) and niche tracks under 30 (e.g. Narrow Head — See You Around). Spelling follows Spotify ("Maximo Park", not "Maxïmo Park"). |
| `spotify_tracks` | same | 256M | Full dump; far too big for the device. |
| `mb_recordings` / `mb_artists(_normalized)` | same | 37.5M / 2.78M | MusicBrainz. No real popularity (artists default to 50). Useful for canonical spelling and for rare songs. |
| `spotify_artists` | same | 15M | Artist popularity. |
| `lb_recording_popularity` / `lb_artist_popularity` | same | 25.7M / 3.6M rows | **ListenBrainz** listen statistics per recording/artist MBID and stats range (`all_time`, `this_year`, `year`, `half_yearly`, `month`, `this_month`, …), `listeners` + `total_listens`. Refreshed from each fortnightly dump by karaoke-decide's `lb-refresh`. The **fresh** popularity signal: a new release has listeners within weeks. ~85K users, so it undercounts the mainstream long tail, but ranks well. |
| `karaokenerds_raw` | same (+ GCS `gs://nomadkaraoke-kn-data/full/`) | ~281K | Every song with a commercial karaoke version. **A strong "people sing this" prior.** Already mirrored to the box (`catalog_mirror.db`). |
| `karaokenerds_community`, `divebar_catalog`, `kn_divebar_xref`, `karaoke_recording_links` | same | 60K / 48.5K / 86K / 162K | Karaoke availability, plus karaoke→MusicBrainz links. |
| HyperMule SSD index | NomadPC `external_media.db` | 414,933 files | Karaoke files on the 4TB USB SSD (question 2). |
| gen search logs | Cloud Logging, `karaoke-backend`, "Standalone search complete for {artist} - {title}" | ~480 per 30 days | Raw typed job-form searches. A typed search followed by a re-search within seconds is usually a Gemini `content` correction, which makes a good test pair. |
| kjbox access logs | NomadPC `journalctl -u kj-controller` (`GET /search?q=`, `/rotation/search?q=`) | ~400 queries since 2026-09-20 | Real singer and KJ queries; the journal only keeps about a week. |

**Getting data onto the box:** `scripts/sync_catalogs.py` (`deploy/nomad-catalog-sync.timer`,
daily 12:15 UTC and 10 minutes after boot):
- downloads GCS exports (skipped when the hash is unchanged)
- builds `<db>.new` and swaps it in with an atomic `os.replace`
- POSTs a reload

See [`CATALOG-MIRROR.md`](CATALOG-MIRROR.md). A song-identification index ships the same way.

**Device budget:** NomadPC is an N97 with 16GB RAM and ~411GB NVMe free, so a few hundred MB
of SQLite is fine. The NomadPi (2GB RAM) is effectively retired; if the index ever has to run
there, keep it on disk (SQLite), not in memory.

## 4. Decisions & reasoning

| # | Decision | Why |
|---|---|---|
| D1 | **Identification is a separate step and a separate UI element from karaoke search** (§1, §5). | Andrew: mixing them is confusing. |
| D2 | **On-device matcher first; Gemini only when it isn't confident.** | Most real queries are "easy" (exact, one small typo, title only, title plus part of the artist). Answering them locally is free, takes milliseconds and works offline. Andrew is cost-conscious about per-search LLM calls but happy with cheap Flash calls as a fallback. |
| D3 | **Gemini fallback = gen's model (`gemini-3.8-flash`), via gen**, reusing the free-text resolver from gen #1065 and extending it to return candidates for descriptions. | One place for model choice, caching and rate limits. kjbox holds the partner secret, not a Vertex credential. |
| D4 | **Support descriptive queries** ("that song from Titanic"). Explicit entry point: "Can't remember the name? Describe it". Also reachable as the automatic fallback for empty searches. | A nice feature for singers who can't remember a song's name. Only an LLM can answer these. An explicit mode makes the intent and the cost visible. |
| D5 | **Index = MusicBrainz (the backbone) + the Spotify snapshot + all KaraokeNerds**, with popularity and a karaoke-availability boost, exported daily by gen's `kn-data-sync` and shipped with `nomad-catalog-sync`. *(Revised 2026-09-28.)* | **Freshness is a requirement** (Andrew: "it's not acceptable for the quality of the search / auto-correction to be slowly degrading over time due to stale data"). The first prototype used only the static July 2025 Spotify snapshot, chosen for its popularity scores, and missed newer songs. MusicBrainz is refreshed weekly by karaoke-decide's `mb-refresh` and already has late-2025 songs the snapshot lacks (Breaking Rust — Walk My Walk, Taylor Swift — Elizabeth Taylor, Olivia Dean — Man I Need). KaraokeNerds (daily) covers new songs that have karaoke versions. Popularity alone misranks ("machu picchu" → Evaluna Montaner 72 over The Strokes 64), so "has a karaoke version" also boosts. |
| D5a | **MusicBrainz rows kept** when Spotify track popularity ≥ 30 (via ISRC), or ≥ 3 recordings, or the artist's Spotify popularity ≥ 50, **or ListenBrainz shows current listening** (≥ 5 listeners this/last year or ≥ 3 last/this month; *2026-09-29*). That's ~4.5M of MB's 30.7M distinct songs. **Popularity = the higher of the Spotify track score and the ListenBrainz score**; songs with neither get **0.8 × artist popularity (Spotify or ListenBrainz), capped at 50**. Junk filtered: live/demo/karaoke disambiguations, cover/tribute credits, self-titled rows without karaoke/popularity. | An estimate must never outrank a measured score (it made MusicBrainz's alternate spellings beat the canonical track). The artist rule lets a known artist's new single in; the ListenBrainz rule lets a **brand-new artist's** songs in (Ninajirachi, Geese, CORTIS) and ranks new hits by current listening (Olivia Dean "Man I Need": capped estimate 50 → 75; Tame Impala "Dracula" beats older same-title songs). A ListenBrainz-popular artist does **not** pull in all its songs (that added ~440K unlistened rows); its songs get in through their own listeners. |
| D6 | **The matcher must handle how singers actually type** (from real NomadPC logs): mostly **title only** ("Espresso", "Dark on me") or **title plus an artist fragment in any order** ("Why I am Dave", "Buy me presents Sabrina", "beer reel big", "day in the life fool sinatra", "Main Street bob seg"), plus typos ("Black eyes pee", "Ella langket", "cheery pie"). | Tokens can belong to the artist or the title in any order, and the artist is often a first name, surname or one word. It is **not** "full artist at the start or end". |
| D7 | **Scoring: artist-constrained title matching, plus spelling, sound-alike and popularity signals, with a margin gate** (top result must clearly beat the runner-up) before auto-applying. Otherwise show "Which one?" candidates or fall back to Gemini. | A prototype (rapidfuzz WRatio against one artist's titles) got "max picu"→Machu Picchu (70 vs 60), "adults r talkin"→The Adults Are Talking, "last night"→Last Nite. The margin is thin on the worst typos, hence adding a sound-alike score (e.g. Double Metaphone). An exact obscure match ("the stokes" = a real band, The Stokes) must lose to a popular near-match. |
| D8 | **Measure against a real test set before and while building** (`kj-controller/tests/fixtures/song_id_eval.jsonl`). Headline metrics: local hit rate, wrong-auto-apply rate (must be about 0), and the Gemini-fallback rate (drives cost). | Tune thresholds on data, not intuition. The fallback rate is the cost. |
| D9 | **Formatting-only tidies** (casing and punctuation of the same song) show gen's wording "Tidied to X · keep what I typed"; real changes show "Corrected to X — you typed … · Undo". Strings reuse gen's translations. | Consistency with gen's job form, which singers may also use. |
| D10 | **The make-it form is pre-filled from the identified song.** An edited field is never overwritten. | The make-it job then starts with a canonical artist/title (gen's own judge still runs on it). |
| D11 | **Persistent search log** (`search_log.py` → `search_log.db` on the device): searches, identifications, Gemini answers and every singer choice, tied by a per-search id. Review with `scripts/search_log_report.py`. | Andrew (2026-09-28): collect "what options singers actually choose after the search/auto-correction results land, so after a few live events we can review the results and identify any issues or edge cases". The report flags undone tidies, "not it?", lower candidates picked, edited make-it pre-fills, and make-it/YouTube requests with no identification. |
| D12 | **ListenBrainz** popularity (per-recording listen counts, fortnightly dumps, imported by karaoke-decide's `lb-refresh`) is the fresh popularity signal. **Shipped 2026-09-29:** each stats range is mapped onto Spotify's 0–100 as `offset + 19·log10(listeners)` (offsets: all_time 8, this_year 11, year/half_yearly 12, month 14, this_month 20; artists `2 / 6 + 17·log10`), fitted to the median Spotify popularity of songs with both. A song takes the max over its recordings and ranges; ranges under 3 listeners are ignored. | MusicBrainz has no popularity, and the Spotify snapshot is frozen at July 2025. Per-range offsets mean a release scored on this month's listeners ranks like an older hit with the same share of listeners. Calibration is data-driven, so re-fit if ListenBrainz's user base changes a lot (queries in the 2026-09-29 session record). |

## 5. Target UX

```
 [ that song from titanic                    ]      ← one search box (unchanged)

 ┌─ 🎵 The song you mean ─────────────────────┐      ← QUESTION 1 (identification)
 │  My Heart Will Go On — Céline Dion         │
 │  not it?  ▸ See other matches              │
 └────────────────────────────────────────────┘

 Karaoke versions of this song                       ← QUESTION 2 (availability)
 ┌────────────────────────────────────────────┐
 │  My Heart Will Go On   Sound Choice  ▶     │
 └────────────────────────────────────────────┘
   none? → "No karaoke version yet": Generate it (Artist/Title pre-filled) · Paste a YouTube link
```

- If the typed text is already exact, or only needs capitalising, there's no song card, just
  the small "Tidied to … · keep what I typed" line.
- An unclear identification gets a "Which one?" list inside the song card, never interleaved
  with karaoke rows.
- The literal-text karaoke search still runs instantly as today. Identification runs alongside
  it (on-device, milliseconds). The Gemini fallback shows "Figuring out which song you mean…".

## 6. Matching pipeline (target)

1. **Normalise:** reuse `text_normalize.normalize`. Also strip noise words ("song by",
   "karaoke", "the song", "-").
2. **On-device identification** (new module; index shipped by `nomad-catalog-sync`):
   - **Artist candidates:** each contiguous sub-span of the query tokens (any position) is
     scored against an artist index (trigram and sound-alike keys, plus artist popularity).
     Fragments like "sabrina" or "bob seg" must be able to hit the right artist.
   - **Title candidates within that artist:** the remaining tokens are fuzzy-scored against
     the artist's songs (small set, generous typo tolerance).
   - **Title-only path:** a trigram and full-text search of titles ranked by
     score × popularity × karaoke boost.
   - **Decide:** `confident` (auto-apply), `candidates` (show "Which one?"), or `none`.
3. **Gemini fallback** (gen): only when step 2 returns `none`, or low confidence on an empty
   karaoke search, or when the singer explicitly uses "Describe it". Cached, and rate-limited
   per device, IP and partner (existing caps).
4. **Karaoke availability:** `unified_search("<artist> <title>")` for the identified song,
   then the existing triage (make-it pre-filled, YouTube).

## 7. Test set

`kj-controller/tests/fixtures/song_id_eval.jsonl`, one case per line:
`{q, artist, title, kind, source, label}`.
- `artist: null` means any artist is fine.
- `artist` and `title` both null means it **must not** identify anything.
- `kind` ∈ exact, title-only, title-only-ambiguous, artist-fragment, partial-title, typo,
  swapped, noise-words, obscure-keep, descriptive, negative.
- Sources: NomadPC logs (2026-09-20..27), gen job-form search logs (Gemini corrections), and
  manual cases for descriptions.
- Labels were first set by Claude on 2026-09-27 (`label` field). Andrew should review them.

Expected outcome by kind: `descriptive` should fall through to Gemini (local `none` is correct
there). Everything else should be answered locally where the song is in the index.

Run it: `python scripts/song_id_eval.py path/to/song_id.db [--verbose]`.
`--synthetic N` generates a **held-out** set from N random popular karaoke songs × 6 kinds
of drunk typing (typos, keyboard slips, doubled or dropped letters, title only, artist
fragment). The matcher was never tuned on it, so it guards against overfitting the labelled set.

## 8. Implementation & results

**Shipped 2026-09-28** (kjbox v0.123.0–v0.123.3, karaoke-gen #1065/#1066); **ListenBrainz popularity + matcher fixes 2026-09-29** (kjbox v0.124.0 + the kn-data-sync export). Operations:
- **Index freshness:** popularity/inclusion come from MusicBrainz (weekly `mb-refresh`), ListenBrainz (fortnightly dumps, `lb-refresh`; check `karaoke_decide.lb_refresh_log`), the Spotify snapshot (static) and KaraokeNerds (daily). gen's `kn-data-sync` (daily, after the KN refresh) exports to `gs://nomadkaraoke-kn-data/song-id/<run>/` + `latest.json`. The NomadPC's `nomad-catalog-sync` (daily 12:15 UTC + after boot) rebuilds `kj-controller/song_id.db` when the run id, the normaliser version or the builder's `SCHEMA_VERSION` changes (~9 min, ~4.4 GB RAM). Check it: `journalctl -u nomad-catalog-sync | grep song-id-sync`.
- **Redeploying the export function:** `infrastructure/functions/kn_data_sync/deploy.sh`, then `gcloud functions deploy kn-data-sync --gen2 --region=us-central1 --project=nomadkaraoke --source=gs://kn-data-sync-source-nomadkaraoke/kn-data-sync-source.zip --runtime=python312 --entry-point=sync_kn_data --quiet`. The live function doesn't follow Pulumi's source object, so `pulumi up` alone won't update the code.
- **Review what singers did:** `sudo -u nomad ./venv/bin/python scripts/search_log_report.py --days 7` on the NomadPC.
- **Evaluating an export change before it goes live:** don't pull the export through a Mac. Run the candidate `SONG_ID_SQL` as `EXPORT DATA` to a scratch prefix (`gs://nomadkaraoke-kn-data/song-id-eval/<name>/`, outside `song-id/` so `latest.json` and pruning are untouched; needs a GCS-writing account, e.g. `admin@`), download it on the NomadPC with the device key (`CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE=/opt/nomad/secrets/nomad-master-sync.json /opt/nomad/google-cloud-sdk/bin/gcloud storage cp …`), build to a side file under `/var/tmp` with `nice`/`ionice`, and run `scripts/song_id_eval.py <db> [--frozen]` against both the live and the candidate index.

### History (2026-09-27 prototype)

Code (kjbox):
- `kj-controller/song_identify.py`: `SongIdentifier.identify(q)` returns
  `{status: confident|candidates|none, best, candidates}`. `song_norm()` is `text_normalize`
  plus single-letter runs joined ("U.S.A." = "usa").
- `kj-controller/scripts/build_song_id_db.py`: TSV shard(s) → `song_id.db`
  - `songs` + FTS5 word index
  - `artists` + FTS5 word index (artist-first path)
  - `vocab` + trigram index (typo expansion)
  - merges on space-less normalised artist+title and swaps the file in atomically
- `kj-controller/scripts/sync_catalogs.py` `run_song_id_sync`: reads gen's manifest
  `gs://nomadkaraoke-kn-data/song-id/latest.json`, downloads that run's shards, rebuilds,
  POSTs `/song-id/reload`. Skipped when the run and normaliser are unchanged.
- Export (gen, `infrastructure/functions/kn_data_sync`): `EXPORT DATA` after the daily KN
  refresh.

Matching (see the module docstring for detail):
- **Retrieval:** three routes are pooled.
  1. FTS OR of each word's spelling variants (trigram vocab lookup; edit budget 1/2/3 by length)
  2. title phrase runs ("dark on me", "my tears richo*")
  3. artist-first: any 1–4 word run whose words (variants/prefixes) are all in an artist's
     name and string-similar to it pulls in that artist's songs ("the stokes", "bob seg",
     "sabrina")
- **Scoring:**
  - q_cov: how much of what was typed the song explains (one-to-one word matching)
  - title_cov: how much of the title was typed
  - artist_cov
  - fuzzy similarity of the whole string, and of the leftover-after-artist to the title
    ("max picu" ≈ "machu picchu")
  - popularity and karaoke-availability priors
  - exact-title bonus
- **Decision:**
  - `confident` needs score ≥ 0.80, a margin over the runner-up, q_cov ≥ 0.8, and title
    evidence. Title evidence means title_cov ≥ 0.8, or artist typed + the title's leading
    words typed, or whole artist + a close mangled title.
  - Same title by several artists: the runner-up by popularity must be ≥ 12 points behind.
  - `candidates` needs score ≥ 0.62 and q_cov ≥ 0.7. Anything else is `none` (→ Gemini).
  - Descriptions ("that song from titanic") leave most words unexplained, so they're `none`.

**Current index (v4, 2026-09-29, + ListenBrainz):** 5.77M songs, 929 MB; build ~9 min / 4.4 GB RAM on the N97. The builder also folds title spelling variants of the same artist into one song ("Breaking Dishes" = "Breakin' Dishes", "Get Your Freak On" = "Get Ur Freak On"), showing the spelling with a karaoke version, else the more popular one. Measured on the NomadPC with the v0.124.0 matcher (same export):

| Index + matcher | Labelled 123 (97 real + 26 freshness): auto / wrong | Freshness 26: auto | Frozen 900: auto / "Which one?" / wrong | p95 |
|---|---|---|---|---|
| v3 + v0.123.3 (before) | 89 (72%) / 2 | 18 | 756 (84%) / 106 / 8 | 307 ms |
| **v4 + v0.124.0** | **98 (80%) / 0** | **26** | 753 (84%) / 109 / 7 | 254–292 ms |

Only 2 labelled cases remain misses, both descriptive (→ Gemini by design). The display spelling now more often follows MusicBrainz (e.g. typographic apostrophes "You’re"), since MusicBrainz rows carry the higher ListenBrainz-backed score. Preferring KaraokeNerds' spelling everywhere was tried and rejected: KN has its own oddities ("Love Game", "Nathan Dawe (feat. KSI)") and it added 10 wrong answers on the frozen set.

**Previous index (v3, 2026-09-28, MusicBrainz-based):** 5.43M songs, 974K words, 880 MB. Build: ~3.5 min / 4 GB RAM on the Mac.
Frozen held-out set (`--frozen`, 900 cases) vs the earlier Spotify-only index, same matcher:

| Index | Auto-applied correct | "Which one?" correct | Wrong auto-apply | none | Mac latency p50 / p95 |
|---|---|---|---|---|---|
| Spotify-only (2M songs) | 89% | 8% | 6 | 2% | 23 / 57 ms |
| **MusicBrainz-based v3 (5.4M)** | **88%** | 9% | 8 | 2% | 37 / 101 ms |

On the labelled real queries v3 gets 76% auto / 12% candidates / 2 "wrong", and both "wrong" ones are the
same song under an alternate spelling ("Get Your Freak On", "Breaking Dishes").
Performance work needed for the bigger index:
- Word-variant lookup: in-memory (first letter, length) buckets + rapidfuzz, instead of a trigram FTS.
- Candidate retrieval: FTS AND of the distinctive words + leave-one-out queries (instead of a broad bm25 OR).
- FTS prefix expansion only for 4+ letter non-stop words.
- A C-speed rapidfuzz pre-filter (top 200) before the Python scoring.

Earlier prototype results (Spotify-only index, 1.98M songs, 322 MB, 65 s build):

| Set | Auto-applied correct | Right song in "Which one?" | none | **Wrong auto-apply** | Latency p50 / p95 |
|---|---|---|---|---|---|
| Labelled real queries (97) | 78% | 14% | 5% (4 are descriptions → Gemini) | **0** | 82 / 234 ms |
| Held-out synthetic (720, seed 7) | 95% | 4% | 0.4% | 1 (same song, differently credited) | 97 / 239 ms |
| Held-out synthetic (900) | 95% | 5% | 0.2% | 1 (ambiguous: "secen rainbow") | 99 / 251 ms |

Known gaps:
- Songs newer than the July 2025 Spotify snapshot that also aren't on KaraokeNerds (→ Gemini). *Largely fixed 2026-09-29 by ListenBrainz; a release only appears once the next ListenBrainz dump (1st/15th, ~2 days to publish) shows listeners.*
- Very popular same-title songs where the singer means an obscure version (→ "not it?" list).
- The NomadPC (N97) will be slower than this Mac: measure before shipping.

**Side-finding (fixed in kjbox #256):** `text_normalize`'s feat/ft regex had no word boundary.
"Soft Cell" normalised to "so" and "Hayloft II" to "haylo", in every on-device search index.
The fix bumps `NORMALIZER_VERSION` to 2, so indexes must be rebuilt (deploy steps in the
CHANGELOG).
