# Singer UI: duet partners & multi-song flow — design

**Date:** 2026-05-15
**Status:** Approved, ready for implementation plan
**Worktree:** `kjbox-singer-add-songs-duets`
**Branch:** `feat/sess-20260514-2226-singer-add-songs-duets`

## Motivation

Two papercuts in the public singer UI (`/sing/`):

1. **Adding another song is hidden.** After a singer submits a request and the KJ approves it, the "You're in!" screen shows position-in-queue but no obvious way to request a second song. Singers have to refresh, clear localStorage, or pester the KJ — every option is bad.
2. **No duet support.** Singers can only attach a single `singer_name` to a request. There is no way to say "I'm singing this with Sarah and Mike". KJs work around it manually (typing "Alice & Sarah" into the name field, losing the second person's phone).

The rotation side already supports multi-singer entries via `rotation_entries.singers_json` and `rotation.add_entry(singers=...)`. The gap is on the singer-facing request path and the bridge from request → rotation.

## Out of scope

- Per-partner push subscriptions / SMS dispatch (partner phones are display-only for the KJ).
- Duet partners scanning a "join my song" QR code from their own phone.
- Schema changes to `rotation_entries` (existing `singers_json` is already what we need).
- Changes to Google Sheets sync column layout (the joined `singer` text remains the only sheet-visible field).
- Reworking the existing identity flow (name + optional phone, stored in localStorage).
- Changes to the search, source-type picker, or admin approval back-end logic beyond surfacing duet info.

## Decisions made during brainstorming

| Question | Decision |
|---|---|
| How are duet partners modeled? | Each partner is a real participant with optional phone — stored as structured JSON, surfaced to the KJ. |
| Where does the singer add partners? | On the confirm ("Looking good?") screen, before submitting. |
| Max partners per request? | 3 partners (4 singers total). Enforced server-side and client-side. |
| What does the system do with a partner's phone? | Display-only on the KJ admin card. The KJ texts the partner manually using their own phone. No push subscription, no SMS dispatch. |
| Done-screen UX after submit? | Show the full list of the singer's submitted requests (this event), each with live status. Add a prominent "Request another song" button. |
| How does the done screen know "the singer's songs"? | localStorage holds an array of request IDs submitted from this device. A new `/sing/my-requests?ids=…` endpoint returns each, filtered by current token. No phone-based lookup, no cross-device merging. |

## Data model

### `sing_requests` — one new column

```sql
ALTER TABLE sing_requests
  ADD COLUMN additional_singers TEXT DEFAULT NULL;
```

Stored as a JSON array (or `NULL` for solo requests):

```json
[
  {"name": "Sarah B.", "phone": "+61 400 111 222"},
  {"name": "Mike",     "phone": ""}
]
```

**Constraints (enforced in `sing.py` `/submit` and `sing_store.create_request` / `update_request`):**

- `array` of at most **3** objects.
- Each `name`: non-empty after `.strip()`, max 100 chars (matches `singer_name` informal cap).
- Each `phone`: optional. When present, must match the existing `_PHONE_RE` (`^\+?[0-9 \-()]{7,20}$`).
- Reject the request with 400 if any constraint fails. Message: `"additional_singers: <reason>"`.

### `rotation_entries` — no schema change

The existing `singers_json` column is the destination. `approve_sing_request` will build:

```python
names = [req["singer_name"]] + [p["name"] for p in (additional or [])]
rotation.add_entry(primary_singer, song_text, ..., singers=names)
```

`rotation_store.add_entry` already joins the names with " & " into the `singer` text column and writes `singers_json`, so sheet sync, rotation queries, rename/merge/split, and stats all work unchanged.

**Partner phones do NOT propagate to `rotation_entries`.** They live only on the `sing_requests` row. The KJ admin approval card reads from there.

## UI flow

### Singer side

**Confirm screen ("Looking good?")** — adds an inline partners section between the pick summary and the "Send to KJ" button:

```
┌─────────────────────────────────────┐
│  Looking good?                      │
│  ┌───────────────────────────────┐  │
│  │ Don't Stop Believin' —        │  │
│  │ Journey (in library)          │  │
│  └───────────────────────────────┘  │
│                                     │
│  Your details: Alice · +61 4…       │
│                                     │
│  Singing with anyone else?  (opt)   │
│  ┌─────────────────────────────┐    │
│  │ [+ Add a singer]            │    │
│  └─────────────────────────────┘    │
│                                     │
│  [ Change ]    [ Send to KJ ]       │
└─────────────────────────────────────┘
```

Tapping "+ Add a singer" expands a row with name + optional phone inputs and a small "×" to remove. Up to 3 rows. The 4th "Add a singer" button is hidden once the cap is reached, replaced by a hint: *"That's the max — 4 singers total."*

The form state lives in `state.additional` (an array on the existing `state` object). On "Send to KJ":

- Strip whitespace.
- Drop rows where the name is empty (silent — treat as "they didn't fill it in").
- Validate phone format on the remaining rows; show inline error if any row fails.
- POST `additional_singers: [...]` alongside the existing payload.

**Done screen** — replaces the single-status view with a multi-song list:

```
┌─────────────────────────────────────┐
│  🎤 Now: Bob — Wonderwall            │
│  Up next: Carla                     │
│                                     │
│  Your songs tonight                 │
│  ┌───────────────────────────────┐  │
│  │ #3 — Don't Stop Believin'     │  │
│  │ About 8–12 min                │  │
│  │ (with Sarah B., Mike)         │  │
│  └───────────────────────────────┘  │
│  ┌───────────────────────────────┐  │
│  │ Pending — Mr. Brightside      │  │
│  │ Waiting for KJ to approve…    │  │
│  └───────────────────────────────┘  │
│                                     │
│  [ + Request another song ]         │
│                                     │
│  🔔 Notify me when I'm up           │
│                                     │
│  ▸ Show upcoming singers            │
└─────────────────────────────────────┘
```

Each card renders the per-request live text from the existing `compute_estimate` output (now singing / up next / position with range / pending / rejected). The push opt-in block stays as-is (one subscription per device).

"Request another song" resets `state.selected`, `state.makeArtist`, `state.makeTitle`, and `state.additional`, then sets `state.step = "search"` and re-renders. Identity (`state.name`, `state.phone`) is preserved. On the next submit, the new request id is appended to the localStorage list and the done screen reloads with the longer list.

### KJ admin side

The existing approval card (admin `/rotation/requests/`) gains a "Duet partners" block when `additional_singers` is non-empty:

```
👥 Duet partners
   • Sarah B. — +61 400 111 222
   • Mike     — (no phone)
```

No behavioural change to approve / reject. After approval, the rotation row shows "Alice & Sarah B. & Mike" in the `singer` column — that's already how `singers_json` flows downstream.

### Rules footer

Add one bullet to the short rules and one paragraph to the full rules:

- Short: *"Duets welcome — add partners on the confirm screen (up to 3 extras)."*
- Full: *"Singing with friends? Add their names (and optionally phone numbers) on the 'Looking good?' screen before sending the request to the KJ. We'll list everyone on the rotation so the KJ knows who to call up."*

## API surface

### Changed: `POST /sing/submit`

Payload gains:

```json
"additional_singers": [
  {"name": "Sarah B.", "phone": "+61 400 111 222"},
  {"name": "Mike", "phone": ""}
]
```

Optional. Validation rules above. Response shape unchanged (the existing `_public_request_view` is extended to include `additional_singers` so the done screen can render partner names without a refetch).

### New: `GET /sing/my-requests?ids=1,2,3`

Token-gated. Returns the singer's known request rows in one round trip so the done screen polls a single endpoint.

**Request:**

```
GET /sing/my-requests?ids=12,15,21&t=4827
```

- `ids`: comma-separated integers, max 20.
- Token must match the current event (existing `_extract_token` / `_is_token_valid` flow).

**Response:**

```json
{
  "now_playing": {
    "now_singing": {"first_name": "Bob", "song_artist": "Oasis — Wonderwall"},
    "up_next":     {"first_name": "Carla", "song_artist": ""},
    "queued_count": 7
  },
  "requests": [
    {
      "request":  {...same as _public_request_view, with additional_singers...},
      "estimate": {...same as /sing/status...},
      "queue":    [...]   // only present once per response (top-level, see below)
    },
    ...
  ]
}
```

To keep the payload tight:

- `now_playing` is returned **once** at the top level (not per request).
- Per-request `queue` is dropped from this endpoint; the existing "Show upcoming singers" expander on the done screen pulls from `now_playing.queued_count` + the existing `/sing/rotation` endpoint when expanded. (One fewer source of duplication.)
- `estimate` is computed per request only when `linked_entry_id` is set.

**Filtering rules:**

- Each requested id is looked up in `sing_requests`.
- Drop rows whose stored `token` doesn't match the current event (same cross-event guard as `/sing/status`).
- Drop unknown ids silently.
- Return ids in the order the client requested.

### Unchanged: `GET /sing/status/<id>`

Stays as-is. The done screen no longer uses it; the only remaining caller is a singer who lands on `/sing/?r=<id>` (legacy entry — KJ shared the link). That path renders the single-request flow exactly as before, with `additional_singers` now included in the view so partner names render.

## Backend implementation order

1. **`sing_store.py`** — schema migration (add column via `ALTER TABLE` in `init_schema` with `IF NOT EXISTS` semantics via try/except `OperationalError: duplicate column`), helper to serialize/deserialize `additional_singers`, extend `create_request` and `update_request`, include the field in row dicts so callers naturally see it.
2. **`sing.py`** — validation in `/submit`, include in `_public_request_view`, implement `/sing/my-requests`.
3. **`routes.approve_sing_request`** — read `additional_singers` from the request row, build the `names` list, pass `singers=` in the 4 source-type branches.
4. **KJ admin** — render the duet block on the approval card (template + minimal JS).
5. **Frontend `sing.js`** — confirm-screen partners UI, multi-song done screen, `/sing/my-requests` polling, "Request another song" reset path, rules-footer copy.

## Migration & rollout

- Live nomadpc DB: the new column is additive and nullable; safe to add at service restart. The `IF NOT EXISTS`-style add idempotently no-ops on a fresh dev DB.
- localStorage migration: the existing `sing_name` / `sing_phone` keys stay. New key `sing_my_request_ids` (JSON array of integers, scoped per token via a stored `{token, ids}` shape so a stale token doesn't leak old ids into a new event).
- Stale client compatibility: a singer running the old JS submits `additional_singers: undefined`, which the server treats as no partners — fully backwards-compatible.

## Testing

Unit tests (pytest, existing fixtures):

- `test_sing_store.py`
  - `additional_singers` round-trips on create/get/update.
  - Schema migration is idempotent.
- `test_sing.py`
  - `/submit` accepts a valid 3-partner payload, returns them in the response.
  - `/submit` rejects: >3 partners, empty name, malformed phone.
  - `/submit` with `additional_singers` omitted still works.
  - `/sing/my-requests` filters out foreign-token rows, returns in requested order, drops unknown ids.
- `test_routes.py` (or wherever `approve_sing_request` is tested)
  - Approving a request with 2 partners creates a rotation entry whose `singer` is `"Alice & Sarah & Mike"` and whose `singers_json` round-trips the names list.
  - Approving a solo request behaves exactly as before (no `singers=` kwarg semantically observable to the rotation).

Manual smoke (post-deploy):

- Submit a 3-partner duet via the public form; verify KJ card shows the partner block with phones.
- Approve and confirm the rotation row reads "Alice & Sarah & Mike" on the controller UI and on the venue display overlay.
- Submit a second song from the same browser; verify both appear on the done screen with live position.
- Reject the second song; verify the done card flips to "the KJ needs to talk to you".

## Open questions / future work

- **Partner identity vs primary singer.** If "Sarah" appears as a duet partner on Alice's song and later submits her own request as the primary, the stats (`get_singer_stats`) will count them separately. That matches existing multi-singer semantics — out of scope to deduplicate.
- **Sheet sync.** No change required, but worth confirming on first prod test that the joined "Alice & Sarah" name renders cleanly on the existing sheet template.
- **Per-partner notifications.** Deliberately out of scope. If a future feature wants to SMS or push partners, the structured phone is already persisted on `sing_requests.additional_singers` — no extra schema work needed.

## File touch list

| Path | Type of change |
|---|---|
| `kj-controller/sing_store.py` | Schema migration + CRUD changes |
| `kj-controller/sing.py` | Validation, `_public_request_view`, new `/sing/my-requests` route |
| `kj-controller/routes.py` | `approve_sing_request` passes `singers=` |
| `kj-controller/templates/index.html` (or wherever the request admin UI renders) | Partner block on approval card |
| `kj-controller/static/app.js` | Partner block on approval card |
| `kj-controller/static-sing/sing.js` | Confirm-screen partners, multi-song done screen, request-another-song button, rules-footer copy |
| `kj-controller/static-sing/sing.css` | Styles for partner rows and per-song cards |
| `kj-controller/tests/test_sing_store.py` | Round-trip tests |
| `kj-controller/tests/test_sing.py` | Endpoint tests |
| `kj-controller/tests/test_routes.py` (or equivalent) | `approve_sing_request` test |
| `docs/archive/2026-05-15-singer-duets-and-multi-song-design.md` | This file |
