# Phase A — Simple-default search + KJ version picker

**Date:** 2026-04-23
**Parent:** [2026-04-23-song-selection-ux-master-plan.md](2026-04-23-song-selection-ux-master-plan.md)
**Depends on:** nothing (ships first)
**Blocks:** Phase B and Phase C both assume the grouped search response shape this phase introduces.

---

## Problem

From the master plan, the normie's pain:

> "normies" who don't care to think about there being different versions of a song, they typically want to sing something common and don't care what version it is as long as it's fairly normal.

Today they see a flat list of every variant of every match. Example query `"bohemian rhapsody"` currently returns something like:

- Local: `Queen - Bohemian Rhapsody.cdg`
- Local: `Queen - Bohemian Rhapsody (SC1234).mp4`
- KN: Queen — Bohemian Rhapsody · KV (YouTube)
- KN: Queen — Bohemian Rhapsody · SK (YouTube)
- KN: Queen — Bohemian Rhapsody · OBSK (community)
- KN: Queen — Bohemian Rhapsody · SC (YouTube) — also has divebar file

Six rows for one song. The KJ's ask: collapse those to **one** tile that says "Bohemian Rhapsody — Queen" with "Let the KJ pick the best version" as the primary action, and hand the candidate set to the KJ at approval time so they can bind the specific version in one tap.

## Non-goals for this phase

- Per-row metadata display for the expander — that's Phase B.
- Empty-state improvements or "make it" copy — that's Phase C.
- Fuzzy matching — explicitly deferred (decision #1 in master).

## Decisions applied

Confirmed in the master (don't re-litigate here):

- Grouping: normalize → exact match.
- Picker: inline expansion on the pending-request card, not a modal.

## Section 1 — Data shape

### 1a. Normalization function

New helper `_normalize_song_key(artist: str, title: str) -> str` in `routes.py` (same module where `unified_search` lives). Lowercase → strip → drop common noise. Deterministic, used identically on the search-response group key and anywhere else that needs to dedupe.

```python
_FEAT_RE = re.compile(
    r"\s*[\[(]?\s*(?:feat\.?|ft\.?|featuring)\s+[^\])]+[\])]?\s*",
    re.IGNORECASE,
)
_PAREN_RE = re.compile(r"\s*[\[(][^\])]*[\])]\s*")
_PUNCT_RE = re.compile(r"[^\w\s]+")  # drop apostrophes, hyphens, etc.
_WS_RE = re.compile(r"\s+")


def _normalize_song_key(artist: str, title: str) -> str:
    """Deterministic (artist, title) → group key for collapsing search results.

    Two results share a key iff their normalized forms are byte-equal. No fuzzy
    matching — accept that "Don't Stop Believin'" and "Dont Stop Believin" won't
    merge until we revisit. See master plan decision #1.
    """
    def norm(s: str) -> str:
        s = (s or "").lower()
        s = _FEAT_RE.sub(" ", s)
        s = _PAREN_RE.sub(" ", s)
        s = _PUNCT_RE.sub(" ", s)
        s = _WS_RE.sub(" ", s).strip()
        return s
    return f"{norm(artist)}|||{norm(title)}"
```

### 1b. New grouped response shape

`/sing/search` response changes from flat lists to a unified `songs` list. Each group has a canonical display `artist` + `title`, a `versions` array containing the source-specific candidate objects *unchanged from today*, and a few derived summary fields the UI needs to render the collapsed tile.

```json
{
  "songs": [
    {
      "key": "queen|||bohemian rhapsody",
      "artist": "Queen",
      "title": "Bohemian Rhapsody",
      "version_count": 6,
      "in_library": true,
      "has_community_only": false,
      "versions": [
        {"source": "local", "local": {<full current local-result object>}},
        {"source": "kn", "kn": {<full current kn track>, "song_title": "...", "song_artist": "..."}},
        {"source": "kn", "kn": {...}},
        ...
      ]
    }
  ],
  "karaoke_nerds_timeout": true
}
```

Key shape rules:

- **`key`** — output of `_normalize_song_key` for the group. Stable between queries; singer UI uses it to correlate state.
- **`artist` / `title`** — the canonical display form, chosen by "first local result wins, else first KN result wins" — whichever source ordered the match into the group first.
- **`version_count`** — `len(versions)`. Duplicate of length but precomputed for UI simplicity.
- **`in_library`** — `True` iff any version is `source=local` or `source=kn` with a divebar match (i.e. singer can sing this *now* without a download).
- **`has_community_only`** — `True` iff every KN variant has `is_community=True` AND no local files exist. (Useful downstream for Phase B copy; not used by the UI this phase but precomputed to avoid re-walking the versions array.)
- **`versions[i].source`** — one of `"local"`, `"kn"`. (Divebar doesn't get its own source here — it remains attached as `versions[i].kn.divebar` on the KN parent, consistent with the current model.)
- **`versions[i].local` / `.kn`** — the full existing result object, unchanged. This preserves round-trip fidelity: we can submit a `kj_pick` request carrying the entire snapshot forward, and the KJ's picker renders from the same object shape as Phase B's expander.

The old top-level `local` and `karaoke_nerds` keys are removed. No transition period — nothing in the wild depends on them outside the kjbox-singer app and the one admin route that wraps it; the consumer is `sing.js` which ships in the same PR.

### 1c. Grouping algorithm

In `unified_search` in `routes.py`, after local + KN lookups complete:

```python
def _group_search_results(local_results, kn_results):
    groups = {}  # key -> group dict; preserves insertion order in py3.7+
    # Locals first — they win the display-name arbitration.
    for r in local_results:
        key = _normalize_song_key(r.get("artist"), r.get("title"))
        g = groups.setdefault(key, {
            "key": key,
            "artist": r.get("artist") or "",
            "title": r.get("title") or "",
            "versions": [],
        })
        g["versions"].append({"source": "local", "local": r})
    # Then KN — only sets the display name if no local already claimed it.
    for song in kn_results:
        for track in song.get("tracks") or []:
            key = _normalize_song_key(song.get("artist"), song.get("title"))
            g = groups.setdefault(key, {
                "key": key,
                "artist": song.get("artist") or "",
                "title": song.get("title") or "",
                "versions": [],
            })
            g["versions"].append({
                "source": "kn",
                "kn": {**track, "song_title": song.get("title"),
                       "song_artist": song.get("artist")},
            })
    # Post-process summary fields.
    out = []
    for g in groups.values():
        versions = g["versions"]
        g["version_count"] = len(versions)
        g["in_library"] = any(
            v["source"] == "local" or
            (v["source"] == "kn" and (v["kn"].get("divebar") or {}).get("file_id"))
            for v in versions
        )
        kn_versions = [v for v in versions if v["source"] == "kn"]
        g["has_community_only"] = (
            not any(v["source"] == "local" for v in versions)
            and kn_versions
            and all(v["kn"].get("is_community") for v in kn_versions)
        )
        out.append(g)
    return out
```

`unified_search` gets a new return shape alongside the old one so internal callers outside `/sing/search` (the admin-side search on the rotation UI) aren't disrupted. Add a `grouped` kwarg; default False preserves today's behaviour; `/sing/search` passes `grouped=True`.

## Section 2 — Singer UI changes (sing.js)

### 2a. Grouped-results rendering

`renderSearch()` in `sing.js` currently builds a flat list from `results.local` and `results.karaoke_nerds`. Replace the body of `renderResults()` with a single loop over `results.songs` producing one card per group.

Card shape (normie default — no version info exposed):

```
┌────────────────────────────────────────────────┐
│  Bohemian Rhapsody                             │
│  Queen                                          │
│                                                 │
│  [ Let the KJ pick the best version → ]        │
│  6 versions available →                         │   <— small link, Phase B activates it
└────────────────────────────────────────────────┘
```

Phase A renders the "N versions available →" link visible but inert (no click handler). That way Phase B only adds behaviour — no template churn.

Corollary: if `version_count === 1`, don't show the "N versions available" link at all, and change the CTA to just "Add to queue" (no "best version" framing — there's only one). The submission in that case uses the single version directly; no KJ pick needed.

### 2b. "Let the KJ pick" submission

New client-side action `pickKjChoice(group)`:

```js
state.selected = {
  source_type: "kj_pick",
  source_ref: null,
  song_artist: group.artist,
  song_title: group.title,
  label: `${group.title} — ${group.artist} (KJ picks best version)`,
  source_meta: {
    group_key: group.key,
    version_count: group.version_count,
    versions: group.versions,  // full snapshot — KJ picker renders from this
  },
};
state.step = "confirm"; render();
```

`source_meta` carries the full candidate snapshot. This is stored verbatim in `sing_requests.source_meta` (JSON column — already exists from sub-project #1) and reappears in the KJ admin API as `request.source_meta`. No new columns.

**Snapshot rationale:** we capture candidates at submit time rather than re-querying at approve time because (a) search is eventually-consistent (KN can be down, divebar can add/remove mirrors), and the singer effectively "agreed to this set" when they submitted, and (b) it saves the KJ waiting for a fresh KN fetch at approval time. Trade-off: a song newly downloaded between submit and approve won't appear as a pick option, which is fine since the KJ can still search manually.

### 2c. Single-version shortcut

When `group.version_count === 1`, the "Let the KJ pick" framing is misleading. Short-circuit to the current happy-path by constructing `state.selected` directly from the single version — same as today's `pickLocal(r)` or `pickKN(song, track)` — and set `source_type` to the specific type (`local`, `divebar`, `kn`) it was before grouping. No `kj_pick` submission, no picker work for the KJ.

## Section 3 — New submission source type: `kj_pick`

### 3a. Backend validation

`_ALLOWED_SOURCES` in `sing.py` gains `"kj_pick"`. Validation:

- Requires `song_artist` and `song_title` (same as `make`).
- Requires `source_meta.versions` to be a non-empty array.
- Does **not** require `source_ref` (picker binds the ref at approval time).

Add a short helper in `sing.py`:

```python
def _validate_kj_pick_payload(data):
    meta = data.get("source_meta") or {}
    versions = meta.get("versions") or []
    if not isinstance(versions, list) or not versions:
        return "kj_pick requires source_meta.versions[]"
    if len(versions) > 50:
        return "kj_pick too many versions (>50) — refusing"
    return None
```

The 50-version cap exists because the full snapshot round-trips through JSON in two database rows and two HTTP responses; a pathological query like `"love"` could return hundreds of candidates. If we ever hit the cap in practice, that's a signal the grouping missed a normalization opportunity.

### 3b. Submit flow

`/sing/submit` in `sing.py` grows a branch for `source_type == "kj_pick"`:

```python
if source_type == "kj_pick":
    err = _validate_kj_pick_payload(data)
    if err:
        return jsonify({"error": err}), 400
    if not (song_artist and song_title):
        return jsonify({"error": "song_artist and song_title are required for kj_pick"}), 400
```

Then falls through to the existing `store.create_request(...)` call — `source_meta` is serialized as JSON already, no changes there.

### 3c. Auto-approve interaction

`store.is_auto_approve()` currently calls `approve_sing_request(...)` inline on submit. For `kj_pick` requests we **skip** auto-approve — the whole point is to defer to the KJ. Add an early return:

```python
if source_type == "kj_pick":
    auto_approved = False  # KJ must pick the version manually
else:
    # existing auto-approve logic
```

Document this inline: auto-approve means "skip review" but `kj_pick` explicitly requires review (for version binding).

## Section 4 — KJ approval UI: inline version picker

### 4a. Admin-side request list: the affordance

The existing pending-request card in the admin UI (in `static/app.js`) shows a "Details" expansion with Approve/Edit/Reject actions. For `source_type === "kj_pick"` requests:

- Replace the single "Approve" button with an inline version picker row.
- Render each candidate as its own small card with a "Approve with this" button.
- The "Edit" and "Reject" buttons stay — they work the same as for any other request type.
- The pick list order: locals first, then KN tracks with divebar mirrors, then KN community, then KN YouTube-only. Stable within each bucket.

### 4b. Per-version card (v1 — Phase B will enrich this)

Minimum viable display per candidate in Phase A:

```
┌─────────────────────────────────────────────┐
│  📁 Local file                               │
│  Queen - Bohemian Rhapsody.cdg              │
│  [ Approve with this → ]                    │
└─────────────────────────────────────────────┘
┌─────────────────────────────────────────────┐
│  🎤 KN — Karaoke Version (community)        │
│  [ Approve with this → ]                    │
└─────────────────────────────────────────────┘
```

Phase B grows these with brand/format/quality/filepath. Phase A just needs enough to pick from — a human-readable one-liner per row.

### 4c. Approve-with-version endpoint

Extend `POST /rotation/requests/<id>/approve` (admin route in `routes.py`) to accept a `version_index` in the JSON body. When present and the request's `source_type == "kj_pick"`:

1. Look up `request.source_meta.versions[version_index]`.
2. Translate that version into the concrete `source_type` / `source_ref` / `source_meta` the existing `approve_sing_request` knows how to handle:
   - `source="local"` → `source_type="local"`, `source_ref=local.path`
   - `source="kn"` with divebar → `source_type="divebar"`, `source_ref=kn.divebar.file_id`
   - `source="kn"` without divebar → `source_type="youtube"`, `source_ref=kn.youtube_url`
3. Mutate the `sing_requests` row in-place so the post-approval record reflects the chosen version (not the ambiguous `kj_pick` placeholder). This is important for audit trails and for the "now playing" history view later.
4. Call the existing `approve_sing_request(app, req)` with the rewritten row.

Skeleton:

```python
def _pick_version_from_kj_pick(req, index):
    meta = json.loads(req.get("source_meta") or "{}")
    versions = meta.get("versions") or []
    if index is None or not (0 <= index < len(versions)):
        raise ValueError("version_index out of range")
    v = versions[index]
    if v["source"] == "local":
        return ("local", v["local"]["path"], None)
    kn = v["kn"]
    if (kn.get("divebar") or {}).get("file_id"):
        return ("divebar", kn["divebar"]["file_id"],
                {"brand_code": kn.get("brand_code"),
                 "disc_id": (kn.get("divebar") or {}).get("drive_path")})
    return ("youtube", kn["youtube_url"],
            {"brand_code": kn.get("brand_code")})
```

If the admin approves without passing `version_index` on a `kj_pick` request: return 400 with `{"error": "version_index required for kj_pick requests"}`. This keeps the contract strict — the frontend must pick.

For all *other* source types, `version_index` is ignored (backwards-compat) so the existing Approve button from the admin UI keeps working.

### 4d. Rejection

No changes. `source_type == "kj_pick"` rejects through the existing reject path — KJ typing a reason like "we don't have any version of this song tonight, sorry" works fine.

## Section 5 — Data flow

### A. Singer submits a "KJ picks" request

```
singer                        sing.py                         sing_store.py
------                        -------                         -------------
POST /sing/submit   ───▶ validate source_type=kj_pick
                          _validate_kj_pick_payload
                              ↓
                          store.create_request(
                            source_type="kj_pick",
                            source_ref=None,
                            source_meta={group_key, versions[...]},
                          )
                              ↓                       INSERT INTO sing_requests
                                                        (source_type='kj_pick',
                                                         source_meta=<json>)
                   ◀─── {request: {..., source_type:'kj_pick'}}
```

### B. KJ approves with a chosen version

```
KJ admin UI                   routes.py                       sing_store.py
-----------                   ---------                       -------------
POST /rotation/requests/<id>/approve {version_index: 2}
                     ───▶    req = sing_store.get_request(id)
                             if req.source_type == 'kj_pick':
                               (type, ref, meta) =
                                 _pick_version_from_kj_pick(req, 2)
                               sing_store.update_request_source(
                                 id, type, ref, meta)
                               req = sing_store.get_request(id)
                             approve_sing_request(app, req)
                             # existing path: creates rotation entry,
                             # queues download if youtube/divebar, etc.
                   ◀─── {ok: true, entry_id: ..., source_type: 'local'}
```

### C. Version-index mismatch

```
If version_index missing OR out of range on a kj_pick:
  → 400 {"error": "version_index required" | "version_index out of range"}
  (request row stays as kj_pick; KJ can retry)
```

## Section 6 — Error handling & failure modes

| Failure | Behaviour |
|---|---|
| Singer submits `kj_pick` with empty `versions` | 400 at `/sing/submit` — UI prevents this but defence-in-depth. |
| Singer submits `kj_pick` with >50 versions | 400 at `/sing/submit`. Log at WARN so we can tune the limit. |
| KJ approves without `version_index` | 400. Admin UI wouldn't send it; this catches manual cURL. |
| KJ approves with out-of-range `version_index` | 400 with specific error. |
| Version's backing file is no longer valid (local deleted, KN track gone) | The per-source approval path already handles this — e.g. `local` with missing path falls back to download, `divebar` with missing file_id returns error to admin UI. No new handling. |
| `source_meta` is corrupt JSON | Existing error path — `approve_sing_request` logs and returns 500. Treat as "KJ must reject and ask singer to resubmit". Expected frequency: ~never. |
| Group normalization collides two genuinely different songs | Accepted outcome (decision #1). Manual workaround: KJ rejects, asks singer to use the search bar with more specific text, or the KJ searches manually and adds via the non-kj_pick path. |

## Section 7 — Testing strategy

### Unit tests

| File | Covers |
|---|---|
| `test_search_grouping.py` (new) | `_normalize_song_key()` (feat/ft stripping, bracket stripping, punctuation, whitespace); `_group_search_results()` (locals win display name, KN adds-if-missing, summary fields correct, `in_library`/`has_community_only` edge cases, empty inputs). |
| `test_sing_store.py` (extend) | Round-trip `source_meta` as dict → serialize → deserialize; verify large-versions snapshots (up to 50) survive. |
| `test_sing_routes_search.py` (new) | `/sing/search` grouped response shape; single-version songs keep their source_type; normal queries produce expected group counts. |
| `test_sing_kj_pick.py` (new) | `_validate_kj_pick_payload` rejections; `/sing/submit` with `source_type=kj_pick` happy + error paths; auto-approve skipped for `kj_pick`; `_pick_version_from_kj_pick` for each version kind (local / kn+divebar / kn-only). |
| `test_admin_approve_kj_pick.py` (new) | `POST /rotation/requests/<id>/approve` with version_index: success, out-of-range, missing-for-kj_pick, ignored-for-non-kj_pick; verify row is rewritten from `kj_pick` to concrete source_type post-approve. |

### End-to-end test

`test_sing_kj_pick_e2e.py` (new):

1. Seed event token + 2 local files + 1 KN stub with divebar match.
2. Singer submits a `kj_pick` request for "Bohemian Rhapsody / Queen" carrying the 3-version snapshot.
3. KJ approves with `version_index=1` (the KN+divebar variant).
4. Assert: rotation entry created, source_type now `divebar` in both `sing_requests` and the rotation row, queued for download.
5. Reject path: submit another `kj_pick`, reject with reason, assert sing_requests row status = rejected, notify dispatcher called.

### Coverage target

≥ 75% on new modules (`search_grouping.py` if extracted — otherwise counted against `routes.py` deltas). Matches project target.

### Manual verification runbook (`docs/TESTING.md` update)

- [ ] Search common query — verify 1 row per unique song, N-versions-available link visible but inert.
- [ ] Search query with only one local hit — verify short-circuit to single-version flow, no `kj_pick`.
- [ ] Submit `kj_pick`, see the request appear in admin UI with inline picker.
- [ ] Admin taps a version — request approved, rotation row has the right source_type.
- [ ] Admin rejects — request marked rejected, singer gets push (from sub-project #4).

## Section 8 — Implementation plan

Ordered tasks. Each task is a single logical commit.

| # | Task | Files touched | Notes |
|---|---|---|---|
| 1 | Add `_normalize_song_key` + tests | `routes.py`, `tests/unit/test_search_grouping.py` (new) | Pure function. Test first. |
| 2 | Add `_group_search_results` + tests | `routes.py`, same test file | Pure function over list inputs. |
| 3 | Wire `unified_search(..., grouped=True)` → `/sing/search` | `routes.py`, `sing.py`, `tests/integration/test_sing_routes_search.py` (new) | `grouped=False` stays default to preserve admin-side search. |
| 4 | Update `sing.js` `renderSearch()` to consume grouped response | `static-sing/sing.js` | Includes inert "N versions available →" affordance. Single-version short-circuit. `pickKjChoice()` handler. |
| 5 | Add `kj_pick` source_type + `_validate_kj_pick_payload` | `sing.py`, `tests/unit/test_sing_kj_pick.py` (new) | Auto-approve skip path in same commit. |
| 6 | Add `_pick_version_from_kj_pick` + `sing_store.update_request_source` | `routes.py`, `sing_store.py`, `tests/unit/test_sing_store.py` (extend) | Row-mutation helper carries the translated source_* fields back into the request row. |
| 7 | Extend `/rotation/requests/<id>/approve` with `version_index` | `routes.py`, `tests/integration/test_admin_approve_kj_pick.py` (new) | Backwards-compat: ignored when not `kj_pick`. |
| 8 | Admin UI: inline picker on `kj_pick` request cards | `static/app.js`, `static/style.css` | One card per version, "Approve with this" buttons. Minimum copy — Phase B enriches. |
| 9 | E2E test | `tests/integration/test_sing_kj_pick_e2e.py` (new) | Full flow per Section 7. |
| 10 | Docs | `docs/CHANGELOG.md`, `docs/ARCHITECTURE.md`, `CLAUDE.md`, `docs/TESTING.md` | CHANGELOG entry, architecture note on grouped search + picker, testing runbook. |
| 11 | Version bump | `pyproject.toml` | `0.24.0` → `0.25.0` (minor — new response shape, new source type). |

### Sequencing notes

- Tasks 1–3 are backend; can land before touching JS. Tasks 1–2 are pure functions, safe to ship incrementally if we split the PR.
- Task 4 (sing.js) is the first singer-visible change; it depends on 1–3.
- Tasks 5–7 (kj_pick submission + approval) can develop in parallel with task 4 but must ship together.
- Task 8 (admin UI picker) ships in the same PR — shipping without it means approving `kj_pick` requests requires cURL.

### Out-of-scope for this PR — noted for followup

- The inline picker's candidate-card copy is bare-bones in Phase A. Phase B upgrades it with full metadata, explainer, and filepath handling.
- No singer-side animation/transition for group expansion — Phase B adds the real expander; this phase's "N versions available →" link does nothing.

## Section 9 — Success criteria for Phase A

Phase A is done when:

1. A search for `"bohemian rhapsody"` returns one group with ~6 versions in the grouped response. `/sing/search` response validates against the new schema.
2. Singer UI shows one tile per song with "Let the KJ pick" CTA.
3. Single-version songs short-circuit to today's flow — no `kj_pick` submission, no picker on the KJ side.
4. A `kj_pick` submission round-trips candidate versions in `source_meta` and reappears verbatim in the admin API.
5. KJ admin UI shows the picker inline on `kj_pick` requests; approving a version rewrites the request's source fields and calls `approve_sing_request` correctly for each of the 3 candidate kinds (local / divebar / youtube).
6. Rejecting a `kj_pick` works with no picker selection.
7. All new + existing tests pass (≥ 75% coverage on new code).
8. No regressions on existing (non-kj_pick) submissions.
