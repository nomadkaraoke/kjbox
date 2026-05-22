# Choose-Best-Version: brand-priority ranking across version selection flows

**Status:** Design approved 2026-05-22 — ready for implementation plan.
**Branch:** `feat/sess-20260522-0026-choose-best-version`

## Problem

When a KJ has to pick which version of a song to play, they currently apply
unwritten rules in their head: community brands (original-audio AI-vocal-removed
tracks) always beat commercial brands; within each tier there's a known quality
ordering (CC > Lemmy > FBK > … for community; KV > SC > SBI > … for commercial);
unrecognized brands are picked randomly only when nothing better is available.
The UI doesn't reflect any of this — it sorts by simple heuristics that miss
the brand-quality dimension entirely.

Two flows need the new ranking:

1. **kj_pick approval** (singer side defers to KJ): the admin pending-requests
   panel currently lists every version with its own "Approve with this" button
   in a 4-bucket sort that ignores brand priority. The KJ should see the best
   version surfaced as a hero card and the rest collapsed.

2. **Rotation Add/Link search** (KJ-driven): the search dropdown that appears
   when adding a singer or linking a file to an unlinked rotation row should
   sort/highlight the best versions first so the top result is usually the
   right one for an Enter-key pick.

## Real-world data (gathered 2026-05-22 mid-show)

Pulled from live nomadpc over LAN — 104 approved requests tonight + 10 diverse
song searches. Key findings that shaped the design:

- **Brand-code mismatch across sources.** Lemmy Caution = `LC` in KN brand
  codes, `LEMMY` in local disc-ids, "Lemmy Caution" in YouTube-download
  filename pattern. Karaoke Version family uses `KV` on KN, `KVD`/`KCD` on
  local. Zoom is `ZM` on KN, `ZOOM` on local. **An alias map is essential.**
- **YouTube-download filenames carry the brand in the middle segment**:
  `VIDEOID__ObsKure Karaoke__Queen - Bohemian Rhapsody.mp4`. Need a
  second parser path for this format.
- **Tonight's source-type breakdown** (after kj_pick translation): 69 local,
  25 youtube, 5 divebar, 3 kn, 2 make. *0 of these show source_type =
  "kj_pick"* — because `_pick_version_from_kj_pick` overwrites the source
  fields, so the original "kj_pick" marker is lost. KJ believes most were
  originally kj_pick. **Tracked as a follow-up** — not in scope here.
- **High-frequency brands NOT in the KJ's stated list**: commercial — VS
  (Vocal Star, 17 occurrences), SK (Sing King, 13), MR (Mr. Entertainer, 11),
  PT (Party Tyme, 9), EK (Easy/EdKara, 8). Community — SDK (SNDL, 6), DBK
  (Deep Bench), CC (CC Karaoke, 2 KN + 4 local CCK). Added to defaults at
  the end of their respective tiers so they outrank truly unknown brands.
- **CC is the KJ's top community pick** (correction made mid-design). Goes
  above LC in the priority list, with CCK and CCX as aliases.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Backend (Python)                                                │
│                                                                  │
│  version_priority.py                                             │
│    ├─ COMMUNITY_BRANDS, COMMERCIAL_BRANDS (defaults + aliases)   │
│    ├─ resolve_brand(disc_id?, filename?, brand_code?,            │
│    │                 brand_name?, is_community?) → (code, class) │
│    ├─ rank_version(version_dict, cfg) → int                      │
│    └─ annotate_versions(versions, cfg) → mutates each            │
│                                                                  │
│  routes.py                                                       │
│    ├─ _group_search_results  ─ annotate before return            │
│    ├─ unified_search          ─ annotate local results too       │
│    └─ /karaoke-nerds/config GET+POST ─ two priority lists        │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
                              │ JSON
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  Frontend (vanilla JS)                                           │
│                                                                  │
│  static/app.js                                                   │
│    ├─ renderKjPickPicker     ─ hero card + collapsed alternates  │
│    ├─ renderRotSearchDropdown ─ global sort by priority_rank,    │
│    │                            section headers, top-3 highlight │
│    ├─ KN prefs panel          ─ two textareas + reset button     │
│    └─ sortKNTracks()          ─ removed (backend supplants)      │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

## Section 1 — Ranking logic

### Module: `kj-controller/version_priority.py`

Canonical brand registry with aliases. Each entry: `(canonical_code, aliases, display_name)`.

```python
COMMUNITY_BRANDS = [
    ("CC",     ["CC", "CCK", "CCX", "CC Karaoke", "CC Karaoke X"], "CC Karaoke"),
    ("LC",     ["LC", "LEMMY", "Lemmy Caution"],                   "Lemmy Caution"),
    ("FBK",    ["FBK", "Funbox", "Funbox Karaoke"],                "Funbox Karaoke"),
    ("BELLY",  ["BELLY", "BellySings"],                            "BellySings"),
    ("NOMAD",  ["NOMAD", "Nomad", "Nomad Karaoke"],                "Nomad Karaoke"),
    ("FAKEY",  ["FAKEY", "FakeyOke"],                              "FakeyOke"),
    ("PMK",    ["PMK", "Punk Media Karaoke"],                      "Punk Media Karaoke"),
    ("OBSK",   ["OBSK", "ObsKure", "ObsKure Karaoke"],             "ObsKure Karaoke"),
    # high-frequency unlisted (lower priority, easily edited)
    ("SDK",    ["SDK", "SNDL", "SNDL Karaoke"],                    "SNDL Karaoke"),
    ("DBK",    ["DBK", "Deep Bench", "Deep Bench Karaoke"],        "Deep Bench Karaoke"),
]

COMMERCIAL_BRANDS = [
    ("KV",     ["KV", "KVD", "KCD", "Karaoke Version",
                "Karaoke Cloud Digitrax", "Karafun"],              "Karaoke Version"),
    ("SC",     ["SC", "Sound Choice"],                             "Sound Choice"),
    ("SBI",    ["SBI", "SBI Karaoke"],                             "SBI Karaoke"),
    ("SF",     ["SF", "Sunfly"],                                   "Sunfly"),
    ("CB",     ["CB", "CBD", "Chart Buster", "Chartbuster"],       "Chart Buster"),
    ("ZM",     ["ZM", "ZOOM", "Zoom"],                             "Zoom"),
    # high-frequency unlisted
    ("VS",     ["VS", "Vocal Star"],                               "Vocal Star"),
    ("SK",     ["SK", "Sing King"],                                "Sing King"),
    ("MR",     ["MR", "Mr. Entertainer", "Mr Entertainer"],        "Mr. Entertainer"),
    ("PT",     ["PT", "Party Tyme"],                               "Party Tyme"),
    ("EK",     ["EK", "EDK", "EdKara", "EDKARA", "Easy Karaoke"],  "Easy Karaoke"),
]
```

**Module-level alias-lookup table** built once on import: case-insensitive
mapping `alias.upper() → (canonical, "community" | "commercial")`. Includes
canonical codes themselves so a raw `LC` resolves directly.

### Config overrides

Two config keys, each a list of canonical codes in priority order:

- `kn_priority_community` — defaults to `[c for (c, _, _) in COMMUNITY_BRANDS]`
- `kn_priority_commercial` — defaults to `[c for (c, _, _) in COMMERCIAL_BRANDS]`

Aliases are always loaded from the hardcoded registry — config only controls
the priority *order*. To add a new brand alias, edit the registry and
redeploy.

The existing single-list `kn_preferred_brands` field is **ignored** by the
new code but left untouched in `config.json` for safe rollback.

### Brand resolution (`resolve_brand`)

Signature:
```python
def resolve_brand(*, disc_id=None, filename=None, brand_code=None,
                  brand_name=None, is_community=None) -> (str | None, str):
    """Returns (canonical_code, classification).
    classification is one of: "community", "commercial", "unknown".
    """
```

Resolution order (first match wins):

1. If `brand_code` resolves via alias map → use that, plus is_community
   override (KN is_community flag wins over registry classification when
   present — the same canonical brand can legitimately appear as both a
   commercial original recording and a community re-cover).
2. If `brand_name` resolves via alias map → same.
3. If `disc_id` is set, extract leading alpha-or-mixed prefix
   (`^([A-Z][A-Z0-9]*?)(?:[-_]|$)`) and resolve via alias map.
4. If `filename` matches YouTube-download format
   (`^[^_]+__([^_]+)__`), extract the middle segment, resolve as brand_name.
5. If `is_community is True` but no canonical match → return `(None, "community")`.
6. Otherwise return `(None, "unknown")`.

Edge cases:
- A KN track with `is_community=True` but a recognized commercial brand_code
  (e.g. someone uploaded a Karaoke Version mirror) → classified as community
  (is_community wins).
- A local file with disc_id `Long Faces - Jane (Final Karaoke Lossy 4k)` →
  no alpha prefix match, no YT format match → unknown.
- `T2K-0348` → prefix `T2K` doesn't match any alias → unknown.

### Ranking (`rank_version`)

Returns an integer; lower = better.

| Rank range | Tier | Inner ordering |
|---|---|---|
| 0–999 | recognized community | `kn_priority_community.index(canonical) * 10` |
| 1000–1009 | unrecognized community (is_community, no canonical) | 1000 |
| 2000–2999 | recognized commercial | `2000 + kn_priority_commercial.index(canonical) * 10` |
| 3000–3009 | unrecognized commercial | 3000 |
| 4000 | unknown | 4000 |

**Source tiebreaker** within tier (added to base rank):
- `local`: +0
- `divebar mirror`: +1
- `youtube-only`: +2

(With `*10` multiplier on the priority index and a 1000-wide gap between
tiers, ties between brands at the same priority position never cross into
the next brand's slot, and we can add up to 100 brands per tier before
hitting the next tier boundary — `LC` local (10) beats `LC` youtube (12)
but `FBK` local (20) still ranks below `LC` youtube.)

Source detection:
- `version.source == "local"` → local
- `version.source == "kn"` and `version.kn.divebar.file_id` → divebar mirror
- `version.source == "kn"` otherwise → youtube
- (Other sources from the rotation search, e.g. raw `divebar` results, map
  the same way — see `annotate_versions` below.)

### Annotation (`annotate_versions`)

Mutates each version dict in place to add three fields:

```python
{
    "priority_rank":  int,                 # for sort
    "priority_brand": str | None,          # canonical code, for badge display
    "priority_class": "community" | "commercial" | "unknown",
}
```

Signature accepts either the `kj_pick` snapshot shape (`{source, local|kn}`)
or the `/rotation/search` shape (raw local result dicts + KN song.tracks with
optional divebar nesting). Pass `shape="kj_pick"` or `shape="rotation_search"`
to disambiguate.

## Section 2 — kj_pick approval card UX

**File:** `static/app.js` — `renderKjPickPicker` and `renderVersionCard`.

Current behaviour: 4-bucket sort, every version rendered as its own card
with its own approve button.

New behaviour:

```
┌──────────────────────────────────────────────────────────┐
│ ⭐ BEST: CC Karaoke (CC) — community                     │
│ 🎤 In My Room — Chance Peña                              │
│ from karaokenerds (YouTube · will download on approve)   │
│                                                          │
│                  [ Approve with this → ]                 │
└──────────────────────────────────────────────────────────┘
  ▶ Show 4 other versions

  (when expanded)
  │ Lemmy Caution (LC) — community         [ Approve → ] │
  │ BellySings (Belly) — community         [ Approve → ] │
  │ Karaoke Version (KV) — commercial      [ Approve → ] │
  │ Sunfly (SF) — commercial               [ Approve → ] │
```

Implementation notes:

- Sort `meta.versions` by `priority_rank` ascending.
- The lowest-rank version is promoted to a hero card with prominent green
  CTA.
- Alternates render inside `<details>` (collapsed by default), with the
  count + brand summary in `<summary>` text.
- Hero card content: ⭐ + canonical brand display name + brand code +
  community/commercial badge + source descriptor (`Local file` /
  `Divebar mirror` / `YouTube — will download on approve`) + song title and
  artist for sanity check.
- Each alternate row keeps the existing single-line layout with its own
  Approve button (so a quick override is still 1 click).
- `versionBucket` function is removed; `priority_rank` from backend supplants it.

**No backend API changes for approval** — `POST /rotation/requests/<id>/approve
{version_index}` already exists; the hero card just sends the index of the
best-ranked version.

Edge cases:
- 0 versions → existing "No versions in snapshot" message (unchanged).
- 1 version → hero card only, no `<details>`.
- All versions tied at rank 400 (no recognized brands) → hero card still
  shows the first one with a subtle "unknown brand — pick carefully" tooltip;
  alternates `<details>` opens by default.
- `priority_rank` field missing from snapshot (back-compat for old
  pre-deploy snapshots) → fall back to the current bucket sort.

## Section 3 — Rotation Add/Link search dropdown

**File:** `static/app.js` — `renderRotSearchDropdown`.

### Sort + section headers

- Build a single flat array of result rows (local + KN-derived).
- Annotate each with `priority_rank` from the backend response.
- Sort the array ascending by rank.
- As you walk the sorted array, emit a section header whenever
  `priority_class` changes:
  - `⭐ Best — <canonical brand> (community)` before the first community block
  - `─── Community (other) ───` before the unrecognized-community block
  - `─── Commercial ───` before the first commercial block
  - `─── Unknown ───` before the unknown block

### Top-3 highlight

Top-3 rows (rank < 1000, i.e. recognized community, OR top 3 overall if no
community matches) get:
- Subtle gold left-border
- ⭐ icon prefix
- The #1 row additionally gets a bold "Best" pill

### Default selection

- `rotSearchSelectedIdx` starts at `0` instead of `-1`.
- Pressing Enter immediately picks row 0 (the best) — muscle memory matches
  the kj_pick hero card.

### Cleanup

- `sortKNTracks()` function is removed; the backend-annotated sort supplants it.

### MAKE row

Stays at the bottom, untouched.

## Settings panel (KN preferences)

**File:** `static/app.js` — KN prefs panel (`#kn-prefs-panel`) + corresponding
HTML template + `routes.py` `/karaoke-nerds/config` GET+POST.

### New UI

```
┌─ Karaoke version preferences ────────────────────────────┐
│                                                          │
│ Community brands (top wins):                             │
│ ┌────────────────────────────────────────────────────┐  │
│ │ CC, LC, FBK, BELLY, NOMAD, FAKEY, PMK, OBSK, SDK,  │  │
│ │ DBK                                                │  │
│ └────────────────────────────────────────────────────┘  │
│ Aliases recognized: CC → CCK/CCX/CC Karaoke;             │
│ LC → LEMMY/Lemmy Caution; …                              │
│                                                          │
│ Commercial brands (top wins, used when no community):    │
│ ┌────────────────────────────────────────────────────┐  │
│ │ KV, SC, SBI, SF, CB, ZM, VS, SK, MR, PT, EK        │  │
│ └────────────────────────────────────────────────────┘  │
│ Aliases: KV → KVD/KCD/Karafun; ZM → ZOOM; …              │
│                                                          │
│ [ Save ]  [ Reset to defaults ]                          │
└──────────────────────────────────────────────────────────┘
```

The alias hints render dynamically from the canonical registry so they stay
in sync with code changes.

### API changes

- `GET /karaoke-nerds/config` → `{priority_community, priority_commercial, aliases}`.
  `aliases` is `{canonical: [alias1, alias2, …]}` for the hint display.
- `POST /karaoke-nerds/config` accepts
  `{priority_community: [...], priority_commercial: [...]}`. Each entry
  must be a canonical code (case-insensitive). Unknown entries reject
  with HTTP 400 listing valid codes.
- Saved as `kn_priority_community` + `kn_priority_commercial` in
  `config.json` via `save_config_value`.
- Old `kn_preferred_brands` field untouched for rollback safety. The Reset
  button writes the defaults explicitly to config (rather than removing the
  keys) so the UI reflects what's persisted.

## Testing

### `tests/unit/test_version_priority.py` (new)

- Alias resolution from each known variant for every canonical brand.
- disc_id parsing: `KVD-22524 → KV`, `LEMMY-001 → LC`, `CCK-042 → CC`,
  `T2K-0348 → unknown`, `BMG6252 → unknown`.
- YouTube-download filename parsing: `wy7voMFbN7U__ObsKure Karaoke__Queen - …mp4 → OBSK`,
  `_id__Unknown__Artist - … → unknown`.
- Classification: community always beats commercial regardless of source.
- Within tier, config-order wins.
- Source tiebreaker: local < divebar mirror < youtube within same brand.
- Config override (`kn_priority_community = ["FBK", "CC"]`) re-orders correctly.
- is_community flag overrides commercial-brand classification.

### `tests/unit/test_rotation_search.py` (extend)

- `/rotation/search` response includes `priority_rank`, `priority_brand`,
  `priority_class` on local results.
- `/rotation/search` response includes the same fields on KN tracks.
- `_group_search_results` annotates versions in the snapshot.
- The grouped response's `versions[]` is sorted by `priority_rank` (so
  clients that ignore the rank field still see best-first ordering).

### `tests/unit/test_routes_karaoke_nerds_config.py` (extend or add)

- GET returns both lists + aliases.
- POST with valid canonical codes saves both lists.
- POST with unknown canonical code returns 400 with a helpful message.
- Migration: GET when only the old `kn_preferred_brands` exists returns
  the new shape with defaults (proving the old field is ignored).

## Out of scope (called out as follow-ups)

- **Track `original_source_type` on `sing_requests`** so kj_pick history is
  preserved after approval. Today, every kj_pick approval rewrites
  `source_type` to `local`/`youtube`/`divebar`/`kn`, so we can never measure
  how often the feature is used. Worth a separate PR.
- **Auto-approve to best version on the singer side**, skipping the KJ
  approval card entirely for kj_pick when confidence is high (e.g. when
  the top version is recognized community). Defer until we trust the ranking
  in practice.
- **Backfill `priority_rank` on existing rotation entries** — not needed,
  the field is only used in live search/approve flows.
- **Visual brand-code badges in the rotation table itself** (showing what
  brand each linked entry is). Nice-to-have but separate UX work.

## Deployment notes

- Frontend-only files (`static/app.js`, `templates/index.html` if the
  prefs panel HTML changes) auto-deploy with no service interruption.
- Backend files (`version_priority.py`, `routes.py` changes,
  `_group_search_results` annotation, tests) require a service restart
  to take effect, which will interrupt active playback. **Push and restart
  only during a quiet moment.**
- `version_priority.py` is a fresh module — no existing data migrations.
- Config-file changes are additive (two new keys); rollback is safe (old
  code just ignores them).
