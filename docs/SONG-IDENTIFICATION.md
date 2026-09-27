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
| D5 | **Index = popular Spotify tracks plus karaoke catalogues**, with popularity and a karaoke-availability boost, built in GCP and shipped with `nomad-catalog-sync`. | Popularity alone misranks: title-only "machu picchu" would pick Evaluna Montaner (pop 72) over The Strokes (64). "Has a karaoke version" is a strong signal of what singers mean. Karaoke catalogues also cover songs the July 2025 Spotify snapshot lacks. |
| D6 | **The matcher must handle how singers actually type** (from real NomadPC logs): mostly **title only** ("Espresso", "Dark on me") or **title plus an artist fragment in any order** ("Why I am Dave", "Buy me presents Sabrina", "beer reel big", "day in the life fool sinatra", "Main Street bob seg"), plus typos ("Black eyes pee", "Ella langket", "cheery pie"). | Tokens can belong to the artist or the title in any order, and the artist is often a first name, surname or one word. It is **not** "full artist at the start or end". |
| D7 | **Scoring: artist-constrained title matching, plus spelling, sound-alike and popularity signals, with a margin gate** (top result must clearly beat the runner-up) before auto-applying. Otherwise show "Which one?" candidates or fall back to Gemini. | A prototype (rapidfuzz WRatio against one artist's titles) got "max picu"→Machu Picchu (70 vs 60), "adults r talkin"→The Adults Are Talking, "last night"→Last Nite. The margin is thin on the worst typos, hence adding a sound-alike score (e.g. Double Metaphone). An exact obscure match ("the stokes" = a real band, The Stokes) must lose to a popular near-match. |
| D8 | **Measure against a real test set before and while building** (`kj-controller/tests/fixtures/song_id_eval.jsonl`). Headline metrics: local hit rate, wrong-auto-apply rate (must be about 0), and the Gemini-fallback rate (drives cost). | Tune thresholds on data, not intuition. The fallback rate is the cost. |
| D9 | **Formatting-only tidies** (casing and punctuation of the same song) show gen's wording "Tidied to X · keep what I typed"; real changes show "Corrected to X — you typed … · Undo". Strings reuse gen's translations. | Consistency with gen's job form, which singers may also use. |
| D10 | **The make-it form is pre-filled from the identified song.** An edited field is never overwritten. | The make-it job then starts with a canonical artist/title (gen's own judge still runs on it). |

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

## 8. Implementation & results (2026-09-27 prototype)

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

Results on this Mac (index: 1.98M songs, 497K words, 322 MB, 65 s build):

| Set | Auto-applied correct | Right song in "Which one?" | none | **Wrong auto-apply** | Latency p50 / p95 |
|---|---|---|---|---|---|
| Labelled real queries (97) | 78% | 14% | 5% (4 are descriptions → Gemini) | **0** | 82 / 234 ms |
| Held-out synthetic (720, seed 7) | 95% | 4% | 0.4% | 1 (same song, differently credited) | 97 / 239 ms |
| Held-out synthetic (900) | 95% | 5% | 0.2% | 1 (ambiguous: "secen rainbow") | 99 / 251 ms |

Known gaps:
- Songs newer than the July 2025 Spotify snapshot that also aren't on KaraokeNerds (→ Gemini).
- Very popular same-title songs where the singer means an obscure version (→ "not it?" list).
- The NomadPC (N97) will be slower than this Mac: measure before shipping.

**Side-finding (fixed in kjbox #256):** `text_normalize`'s feat/ft regex had no word boundary.
"Soft Cell" normalised to "so" and "Hayloft II" to "haylo", in every on-device search index.
The fix bumps `NORMALIZER_VERSION` to 2, so indexes must be rebuilt (deploy steps in the
CHANGELOG).
