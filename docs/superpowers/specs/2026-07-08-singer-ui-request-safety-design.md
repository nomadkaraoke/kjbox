# Singer Web UI — Request Safety & Self-Service (design)

- **Date:** 2026-07-08
- **Status:** Approved design — pending spec review before implementation
- **Author:** Andrew + Claude
- **Area:** `kj-controller/` `sing` blueprint (`sing.py`, `sing_store.py`, `static-sing/sing.js`, `static-sing/sing.css`, `templates/sing.html`) + small additions to the KJ requests panel (`static/app.js`, `routes.py`, `rotation_store.py`).

## Problem

Two recurring pain points reported by the KJ at live shows:

1. **Wrong song submitted.** Singers occasionally come to the KJ saying "this isn't the song I selected." There is no known reproduction, so the cause must be found and the flow made reliable.
2. **No way to change a request.** Singers change their mind — they want to cancel a song, sing a different one, or reorder two of their own songs — and today the only path is to physically find the KJ.

## Goals

- Eliminate the mechanisms by which a singer can end up with a song they did not pick.
- Let a singer act on **their own** requests: cancel, change the song, and reorder their own songs.
- Keep the KJ **aware of and in control of** changes: edits and reorders route through KJ approval (for now); cancels are instant but remain visible to the KJ.
- Bring the singer search up to the reliability + presentation standard of the KJ "Link song" search, which shares the same backend.

## Non-goals

- No per-singer login/account system. Ownership is proven by the submitting device (localStorage) + a per-request secret.
- No cross-device request management (clear storage / new phone → ask the KJ, same as today's read-only behavior).
- No "jump ahead of other singers" — reordering is limited to a singer's own requests. Priority jumps remain paid-priority (♥) / KJ discretion.
- No change to the KJ's fundamental approval model; we reuse the existing pending → approve/reject pipeline.

## Decisions (from brainstorming)

| # | Decision |
|---|---|
| Confirm strength | **Harden** the existing "Looking good?" confirm screen (don't add a separate inline step). |
| Change model | **Cancel is instant**; **edit (change song) and reorder need KJ approval.** |
| Edit scope | **Change the song only** (re-pick). Name/duet edits stay KJ-only for now. |
| Reorder scope | **Reorder the singer's OWN requests only** (e.g. bring Song B ahead of Song A). |
| Cancel visibility | Approved/in-rotation cancels are **soft-cancel**: excluded from being called up, but stay visible in the KJ rotation with **dismiss + restore**. Pending (not-yet-approved) cancels just delete the request. |
| Change representation | **Reuse the pending → approve/reject pipeline + KJ requests panel** (not a parallel changes subsystem). |
| Shipping | **Two PRs**: Part 1 (search reliability + confirm hardening) first, then Part 2 (self-service). |

---

## Part 1 — Search reliability & mis-tap prevention (PR #1)

The singer search (`static-sing/sing.js` `renderSearch()` / `doSearch()`, ~lines 517–998) calls the **same backend** as the KJ link search — `unified_search(query, app, grouped=True)` via `GET /sing/search` (`sing.py:491-522`) — which live-scrapes Karaoke Nerds (latency up to ~8s, highly variable). The KJ search has a suite of reliability protections against that latency; the singer search has **none** of them. This is the root of the "wrong song" reports.

### A1. Stale-response generation guard (the primary fix)

Port the KJ `rotSearchGen` pattern (`static/app.js:7121-7216`). Currently `doSearch` applies whatever comes back unconditionally (`sing.js:534` `results = data`), so a slow response for an *earlier* query can land after a newer one and overwrite the current results the singer is reading/tapping.

- Add a closure-scoped `let searchGen = 0;` in `renderSearch`.
- In the debounce callback: `const myGen = ++searchGen;` before the fetch.
- After `await search(...)`: `if (myGen !== searchGen) return;` **before** assigning `results`, `err`, or toggling `loading`.
- **Latest-owner rule:** only the latest generation may clear `loading` or set `err` (mirrors `app.js:7207-7214`) — a stale request finishing must not flip the spinner off or show an error over a live search.
- Bump `searchGen` on every new keystroke and whenever leaving the search step (pick → `state.step = "confirm"`), so a late response can never write into a screen the singer has moved past.

### A2. Debounce & instant feedback

- Raise the debounce **300ms → ~700ms** to match the KJ side (`app.js:7143-7148`). Correctness comes from A1; the longer delay only trims wasted live scrapes.
- Show a "Searching…" indicator **the instant the singer types** (before the debounce elapses), so key-mashing never feels dead. Today loading is only set after the debounce fires (`sing.js:531`).

### A3. Stop the results list shifting under a finger

The physical mis-tap: on mobile there is a ~300ms gap between deciding to tap a row and the tap landing; if the list rebuilds in that gap, the row under the finger becomes a different song. `update()` replaces the entire `.results` node on every response (`sing.js:628-632`).

- **Only replace on change:** compute a cheap signature of the rendered groups; if identical to what's shown, skip the `replaceWith` (kills gratuitous rebuilds from duplicate/near-duplicate responses).
- **Post-render activation cooldown:** for ~300ms after a results (re)render, the primary pick buttons ignore activation (visually settle, then arm), so a tap aimed at the previous layout can't fire a freshly-appeared row.

### A4. Harden the confirm screen (`renderConfirm`, `sing.js:1000`)

A confirm step already exists ("Looking good?") but singers breeze past it and, if the mis-tap happened upstream, the label they skim is already wrong.

- Make **song title + artist the visually dominant element** (large, high-contrast).
- Show the **source/version explicitly**: Commercial / Community / in-library / "KJ picks best version" / the specific brand.
- Add a **"you searched: '<query>'" breadcrumb** so a mismatch is obvious at a glance.
- Give an explicit primary **"Yes — send to the KJ"** and an equally-reachable **"← Pick a different song"** (currently "Change" / "Send to KJ", `sing.js:1135-1136`). Keep duet-partner rows.

### A5. Port KJ result-presentation wins (mis-tap surface + right-version confidence)

From the recent KJ link-search overhaul (`renderRotSearchDropdown`, `app.js:7240-7532`):

- **Collapse noisy commercial download versions** behind a "▸ N more versions" expander *when a good option is already shown* (a community version, or a KJ-trusted/stated brand) — mirror `collapseActive` (`app.js:7329-7372`). Fewer tap targets, steered toward good versions. Applies to the singer version list (`renderVersionsExpander`, `sing.js:756-793`).
- **"Best" pill + trusted-brand ⭐ markers** on versions (`rotLeadMark`, `app.js:7528-7532`) so a singer who opens the version list picks the right one confidently.
- **Keep cross-source priority ordering** (community-before-commercial, best leads). This is computed server-side in `unified_search` / `_group_search_results`; surface the `priority_class` / `priority_stated` / `priority_brand` metadata in the grouped `versions[]` (expose it there if not already present).
- **Do NOT** port: same-file dedup (handled server-side by `grouped=True`) and the `config`-shadow cache-bust fix — the singer template is already correct (`sing.html:8,60` use Jinja's auto-injected `config`). **Guard note:** do not add `config=cfg` to the singer's `render_template` (`sing.py:394-402`) or it reintroduces the exact shadow bug fixed on the KJ side in `123844b`.

### Part 1 does NOT need

- AbortController (the KJ side proves the generation guard alone is sufficient; the request still completes, its result is discarded).
- Arrow-key/Enter handling — the singer already selects only via explicit buttons, so it's already immune to "Enter links a stale row."

---

## Part 2 — Self-service on your own requests (PR #2)

### B1. Ownership model (security)

Reads (`GET /sing/my-requests`) trust the device's `localStorage` id list, scoped by event token + `created_at >= night_started_at` (`sing.py:125-138`, `798-846`). That is unsafe for **writes** — a bare request id is guessable, so id enumeration could cancel/edit anyone's song.

- Mint a per-request **`edit_token`** (`secrets.token_urlsafe(16)`) in `create_request`; return it **once** in the `/sing/submit` response; store it in localStorage alongside the id (extend the existing `sing_my_request_ids` store, `sing.js:32-65`).
- Every singer mutation requires matching `(request_id, edit_token)` **and** valid event token **and** night-scope. Mismatch/absent → `403`.
- Rate-limit mutation endpoints (reuse `_rate_limit_exceeded`, `sing.py:86-97`).
- Consequence: self-service works from the original device only. This matches the current read limitation and is acceptable.

### B2. Done-screen controls (`renderDone` / `_renderSongCard`, `sing.js:1178-1242`)

Per-card controls, shown by request status and whether the device holds the `edit_token`:

- **Cancel**
  - Pending (not yet approved): delete the request immediately.
  - Approved / in rotation (Waiting): **soft-cancel** — mark the linked rotation entry "Cancelled by singer," exclude it from being called up (Now Singing / Up Next selection), keep it visible to the KJ with dismiss + restore. Set the `sing_requests` status to `cancelled`.
  - Now Singing / Up Next: soft-cancel still flags it (the singer's intent is surfaced to the KJ), but the imminent case is fundamentally the KJ's call; the UI copy nudges "the KJ will see this."
- **Change song**
  - Re-opens search (reuses `/sing/search` + the hardened confirm).
  - If the original is still **pending**: update it in place; it remains pending for the KJ's normal approval.
  - If the original is **approved**: create a **pending change request** that `supersedes` the original (see B3). On KJ approval, the new song is swapped into the existing rotation slot (reusing `approve_sing_request` for any needed download), keeping the singer's position; the old request is closed.
- **Reorder** (only when the singer holds 2+ queued/approved songs)
  - Reorder *their own* entries. Creates a **pending reorder request** (see B3). On KJ approval, apply `/rotation/move` to the singer's linked entries.

### B3. Change representation — reuse the pending pipeline

Model changes as pending items in the **existing** `sing_requests` table + KJ requests panel, so the KJ approves/rejects with the flow they already use:

- **Edit (of an approved request):** a new pending `sing_requests` row with `supersedes_request_id` set to the original. It rides the entire existing approve path (`approve_sing_request`, download, `mark_approved`). The KJ panel labels it "change: replacing <old song>." On approval: swap the new source into the original's `linked_entry_id` rotation entry (or add + move to the original's position), close the superseded request.
- **Reorder:** a pending `sing_requests` row with `source_type = 'reorder'`, no song, and `source_meta = {ordered_entry_ids: [...]}` (the singer's own entries in desired order). The KJ panel renders it as "reorder: <singer>'s songs" with Approve/Reject. On approval: apply `move_entry` for each id (`rotation_store.py:419`); reject just discards.

This adds **two additive columns** (`edit_token`, `supersedes_request_id`), a `cancelled` status, and a `reorder` source type — no new tables, no new KJ approval UI beyond per-type labeling.

### B4. KJ-side changes

- **Requests panel** (`GET /rotation/requests`, KJ poll `app.js:7991`): render `supersedes`/`reorder` items with clear labels + the existing Approve/Reject. Approve handlers extend `approve_sing_request_route` (`routes.py:5081`) to handle the supersede-swap and reorder-apply cases.
- **Rotation:** a "Cancelled by singer" visual state on the entry, excluded from Now Singing / Up Next auto-advance, with **dismiss** (remove) and **restore** controls. Reuse the existing status/marker machinery (`rotation_entries.status`, `update_entry`/`delete_entry`); introduce a distinct marker rather than overloading `Left` so it's unambiguous in the UI.
- **Notification:** reuse the existing push/SMS `notify_request_decision` (`routes.py:5120`) to tell the singer when a change is approved/rejected.
- **Concurrency:** respect the rotation `rev` optimistic-concurrency counter (`rotation_store.py:1214`, exposed in `GET /rotation`) when applying approved changes.

### B5. New singer endpoints (all `@require_token` + `edit_token` + night-scope + rate-limited)

| Method + path | Behavior |
|---|---|
| `POST /sing/requests/<id>/cancel` | Body `{edit_token}`. Pending → delete. Approved → soft-cancel entry + status `cancelled`. |
| `POST /sing/requests/<id>/change` | Body `{edit_token, source_type, source_ref, source_meta, song_artist, song_title}`. Pending → update in place. Approved → create pending superseding request. |
| `POST /sing/requests/reorder` | Body `{items: [{id, edit_token}], ordered_entry_ids}`. Creates a pending reorder request for the singer's own approved entries. |

`GET /sing/my-requests` gains a per-item `can_edit` signal (client shows controls only for requests whose `edit_token` it holds).

---

## Data model changes (all additive)

`sing_requests` (`sing_store.py:101-160`):
- `edit_token TEXT` — per-request ownership secret (minted in `create_request`).
- `supersedes_request_id INTEGER` — links an edit's new pending request to the original.
- New status value `cancelled` (no schema change; `status` is free-text TEXT).
- New `source_type` value `reorder` (payload in `source_meta`).

`rotation_entries` (`rotation_store.py:152-170`):
- A "cancelled by singer" marker (dedicated flag/status value; details in the implementation plan) — must be excluded from Now Singing / Up Next selection and support dismiss/restore.

Migrations follow the existing additive-`ALTER TABLE` + swallow-"duplicate column" pattern (`sing_store.py:150-159`).

---

## Testing

- **Unit** (`tests/unit/test_sing_store.py`): new columns, `edit_token` mint + verify, `cancelled` status, `supersedes_request_id`, reorder payload round-trip.
- **Integration** (`tests/integration/test_sing_*`): each singer endpoint — pending vs approved paths; KJ approve/reject of supersede + reorder; soft-cancel exclusion from Now/Up-Next; **security: wrong/missing `edit_token` → 403**; rate-limiting.
- **E2E / Playwright** (`tests/e2e/test_sing_frontend.py` and peers):
  - **Stale-response race test** — fire two searches with slow-then-fast ordering (stub/delay the fetch) and assert the stale result **never** renders and doesn't flip loading/error.
  - Confirm-screen hardening (title/artist dominant, breadcrumb, both actions reachable).
  - Version-list collapse + Best/⭐ markers.
  - Done-screen self-service: cancel (pending vs approved), change song, reorder — including that the device only sees controls for its own requests.

## Shipping plan

- **PR #1 — Part 1** (search reliability + confirm hardening + presentation). Frontend-heavy (`sing.js`, `sing.css`) + verifying backend `priority_*` fields are present in grouped `versions[]`. Fast, high-value, low-risk (no schema change). Frontend-only deploy = no service restart.
- **PR #2 — Part 2** (self-service). Adds migrations + endpoints + KJ panel/rotation changes; requires a backend restart to deploy (coordinate around live shows per `CLAUDE.md` production-safety rules).

## Risks / notes

- **Production device is live.** Backend changes (PR #2) require a service restart that interrupts playback — deploy only with explicit permission and off-show.
- **localStorage-bound ownership** means self-service is device-scoped; this is by design and matches current behavior.
- **Superseding-request approval** must correctly swap into the existing rotation slot (and re-download if the new source needs it) without double-adding — covered by integration tests and the `rev` guard.
- Keep every singer interaction from triggering a gratuitous fresh `unified_search` (the KJ deliberately avoids close-on-outside-click for this reason, `app.js:7184-7187`).
