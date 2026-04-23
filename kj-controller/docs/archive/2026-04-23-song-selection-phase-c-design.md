# Phase C — Empty-state + the make-it flow for punks

**Date:** 2026-04-23
**Parent:** [2026-04-23-song-selection-ux-master-plan.md](2026-04-23-song-selection-ux-master-plan.md)
**Depends on:** Phase A (grouped `/sing/search` response — empty result == `songs: []`)
**Blocks:** nothing

---

## Problem

From the master plan, the punks:

> "punks" who often want to sing something from niche local band

They search for an obscure song and get nothing. Today the singer UI has two small `<details>` elements at the bottom of the search screen — "Paste a YouTube link" and "Ask the KJ to make this one" — neither visually promoted, both minimally explained. The singer doesn't know what "make this one" actually means for them (how long? guaranteed? what are they waiting for?) and the YouTube-link escape hatch is buried.

The KJ's framing on the caveats for the "ask the KJ to make it" path:

> making a track live on the night isn't always available (as if the KJ is too busy to do lyrics reviews during a show, they need to be able to turn the feature off), and even when it is available, it isn't always possible for every song on the night (some songs need a long and high amount of focus to correct/sync lyrics during the generator lyrics review phase), and how long it takes will vary (sometimes 20 mins, sometimes 1+ hours).

And the sleeper feature — gen.nomadkaraoke.com as a DIY escape hatch:

> if they're willing to do the lyrics review themselves they can just make the song same-night themselves on their own phone, then just input the youtube url into the song request form once it's published. that process can take as little as 5 minutes of them focusing on their phone to complete.

Phase C reshapes the empty-state into a deliberate, expectation-setting triage with three clearly-ranked options.

## Non-goals for this phase

- **New singer-side capabilities.** Everything a singer can do post-Phase-C, they can already do today — paste a YouTube link, ask the KJ to make it, or visit gen.nomadkaraoke.com. Phase C is copy, layout, and one KJ-side toggle.
- **Tracking gen.nomadkaraoke.com usage / referral attribution.** Out of scope. If we want attribution later, it's a karaoke-gen concern, not kjbox.
- **Automated capacity detection** ("can the KJ handle a make-it request right now?"). Out of scope — master plan already called this out. The KJ's single toggle + per-request reject is enough.
- **Changing the existing `source_type=youtube` or `source_type=make` backends.** Discovered in the codebase audit: a gen-published YouTube URL Just Works through the existing yt-dlp download path. No backend changes needed for the DIY path.

## Section 1 — What goes where

### 1a. The three-option triage

When `/sing/search` returns an empty `songs: []` AND the query is ≥ 3 characters AND `make_requests_enabled === true` (all three), show the full triage. When `make_requests_enabled === false`, hide option 2 (make-it) and show only 1 and 3.

The three options, in order:

1. **Paste a YouTube link** (fast, familiar)
2. **Ask the KJ to make it tonight** (free, variable time, may be declined) — *only if make-requests are enabled*
3. **Make it yourself on gen.nomadkaraoke.com right now** (free, 5–20min, best fidelity, you control the lyrics)

Order chosen to present options in **ascending singer-effort**. The singer picks based on their willingness to do work.

### 1b. KJ-side toggle: `sing_accept_make_requests`

New meta key on `sing_store` / `rotation_meta` table, same pattern as `request_token_enabled` + `request_auto_approve`:

```python
# sing_store.py
ACCEPT_MAKE_REQUESTS_KEY = "sing_accept_make_requests"

def is_accepting_make_requests(self) -> bool:
    return self._get_meta(ACCEPT_MAKE_REQUESTS_KEY, "1") == "1"

def set_accepting_make_requests(self, enabled: bool):
    self._set_meta(ACCEPT_MAKE_REQUESTS_KEY, "1" if enabled else "0")
```

Default **ON** (`"1"`) so new installs don't silently break the existing feature. KJs turn it off per-event when they're too busy.

The KJ admin UI's "Requests settings" modal gets a new checkbox:

> ☐ Accept "make it" requests tonight
> &nbsp;&nbsp;&nbsp;*When off, singers won't see the option to ask you to make a custom karaoke track.*

Same modal as the existing `auto_approve` toggle. The backend config endpoint (`GET|POST /rotation/requests/config`) grows the field.

### 1c. Propagating the flag to singers

Two paths:

1. `/sing/search` response gains a `make_requests_enabled` field alongside `songs[]`. Cheap — `SingStore.is_accepting_make_requests()` is a single SQLite read, already cached in the same connection.
2. The initial page render (`/sing/` landing after token validation) includes `make_requests_enabled` on the `#sing-root` dataset so the singer UI has it before the first search completes. Used by a future state where we'd want to show "ask the KJ" independently of search — though Phase C only surfaces it from empty-state, so for this phase the search-response field is sufficient. Include both for consistency with how we pass `token` today.

### 1d. Enforcement

Even if a misconfigured / stale client submits `source_type=make` while the flag is off, `/sing/submit` must reject it:

```python
if source_type == "make" and not store.is_accepting_make_requests():
    return jsonify({"error": "make_requests_disabled"}), 400
```

Defence-in-depth — a cached `sing.js` from before the toggle flipped doesn't leak to the KJ as a pending request.

## Section 2 — UX design

### 2a. Empty-state layout

The singer UI renders the search screen with results; when results are empty AND the query is ≥ 3 chars, we replace the "no results" void with the triage.

```
┌─────────────────────────────────────────────────┐
│  Pick your song                                 │
│                                                 │
│  [ search: heartbeat city tribute         ]     │
│                                                 │
│  ┌─────────────────────────────────────────┐   │
│  │  Can't find it in our catalogue.         │   │
│  │  Three ways forward — pick the one       │   │
│  │  that fits how much effort you want.     │   │
│  └─────────────────────────────────────────┘   │
│                                                 │
│  ┌ 1. Paste a YouTube link ─────────────────┐   │
│  │  Fastest. If you can find the song on    │   │
│  │  YouTube, paste the link and we'll use   │   │
│  │  that directly. Quality varies.          │   │
│  │  [ https://youtu.be/…              ] →   │   │
│  └──────────────────────────────────────────┘   │
│                                                 │
│  ┌ 2. Ask the KJ to make it ─────────────────┐  │  ← only if make_requests_enabled
│  │  Free, but takes time — usually 20 min    │  │
│  │  to 1 hour. The KJ can't always fit it    │  │
│  │  in on a busy night, and some songs are   │  │
│  │  too complex to do live. If they can't    │  │
│  │  do it tonight, they'll let you know.     │  │
│  │                                           │  │
│  │  Artist  [                           ]    │  │
│  │  Title   [                           ]    │  │
│  │  [ Ask the KJ →                      ]    │  │
│  └───────────────────────────────────────────┘  │
│                                                 │
│  ┌ 3. Make it yourself (fastest for niche) ──┐  │
│  │  If you don't mind some phone time, you   │  │
│  │  can make the karaoke track yourself on   │  │
│  │  gen.nomadkaraoke.com — takes ~5 min if   │  │
│  │  you do the lyrics review, longer if you  │  │
│  │  don't. Then paste the YouTube URL back   │  │
│  │  here.                                    │  │
│  │  [ How it works (30s) ▸ ]                 │  │
│  │  [ Open gen.nomadkaraoke.com →  ]         │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

Cards are stacked, each with a distinct primary action. The previous-screen `<details>` elements for "paste youtube link" and "ask the KJ to make" are retired — their functionality moves into the triage cards. No collapse/expand behaviour; these are full cards on empty-state.

### 2b. "How it works" explainer (option 3)

Inline `<details>` under option 3's description. When expanded, shows a minimal step-by-step illustration. No video, no animated gif — static screenshots / text. If we later have bandwidth for a screen-recording, it slots in here; for now keep it image-free to avoid hosting concerns.

Copy (final draft — tune during implementation):

```
How gen.nomadkaraoke.com works (the fast path)
  1. Find your song on YouTube — any version with clear vocals.
  2. Open gen.nomadkaraoke.com, paste the link, pay nothing.
  3. Wait ~2 min while we separate the vocal from the audio.
  4. You get a lyrics review screen. Tap any wrong line, fix it,
     save. This is the focused-phone bit — usually ~3 min.
  5. We render the karaoke video and publish it to YouTube.
  6. Copy the new YouTube URL, come back here, paste it into
     option 1 above.

Total time: ~5–10 min if you focus. The KJ does nothing —
your song lands in the rotation like any YouTube submission.
```

### 2c. Option-2 form (make-it)

Already partially exists today as the bottom `<details>`. Move it up into the triage card and enrich the validation:

- Artist + Title required.
- Max length (singer-name convention) on each: 200 chars.
- Client-side confirm before submit: a small `confirm()` with the key caveats pulled out:

  > "Asking the KJ to make **[Title] by [Artist]** — this can take 20 min to 1 hour, or may not be possible tonight. Sure?"

  Cheap guardrail against impulse clicks. Rewritable into a prettier inline confirmation step later if it grates.

### 2d. Non-empty result state — behaviour unchanged

When search returns ≥ 1 group, Phase C adds nothing visible. The triage is strictly for empty-state. The existing top-level "Paste a YouTube link" / "Ask the KJ to make" `<details>` on the non-empty search screen **goes away** — a singer with a valid result set doesn't need these as secondary options (and if they do, they can re-search with a deliberately blank or missing query to reach empty-state, or hit the back button).

**Alternative considered:** keep tiny "other options" links visible even when results exist, for when a singer rejects every search result. Rejected because it clutters the normie path. If it turns out to matter, add a small "Not finding it? Try a different query or [paste a YouTube link]" link under the results in Phase C.5.

## Section 3 — Backend changes

### 3a. New store methods

Added to `sing_store.py`:

```python
def is_accepting_make_requests(self) -> bool: ...
def set_accepting_make_requests(self, enabled: bool) -> None: ...
```

Default value `"1"` (on). Both round-trip through `rotation_meta`, same pattern as the other meta keys.

### 3b. Expose on `/sing/search`

```python
@sing_bp.route("/search", methods=["GET"])
@require_token
def search():
    ...
    return jsonify({
        "songs": data["songs"],
        "karaoke_nerds_timeout": data.get("karaoke_nerds_timeout", False),
        "make_requests_enabled": current_app.sing_store.is_accepting_make_requests(),
    })
```

### 3c. Expose on landing render (for UI consistency)

`sing.py::landing()` — pass `make_requests_enabled` into the template. Template forwards it on `#sing-root` dataset:

```html
<div id="sing-root"
     data-token="{{ token }}"
     data-request-id="{{ request_id }}"
     data-make-requests-enabled="{{ '1' if make_requests_enabled else '0' }}">
</div>
```

`sing.js` reads it once at boot: `state.makeRequestsEnabled = root.dataset.makeRequestsEnabled === "1"`. Phase C's render uses `state.makeRequestsEnabled` (updated on each `/sing/search` response to catch mid-session toggle flips).

### 3d. Submit-time enforcement

In `/sing/submit`, before the `create_request(...)` call:

```python
if source_type == "make":
    if not store.is_accepting_make_requests():
        return jsonify({"error": "make_requests_disabled"}), 400
    if not (song_artist and song_title):
        return jsonify({"error": "song_artist and song_title required"}), 400
```

### 3e. Admin config endpoint

`GET|POST /rotation/requests/config` in `routes.py` today exposes `request_token_enabled`, `request_auto_approve`, the token itself. Add `accept_make_requests`:

GET response grows:
```json
{
  "enabled": true,
  "auto_approve": false,
  "accept_make_requests": true,
  "token": "5721",
  ...
}
```

POST payload accepts `accept_make_requests`:
```json
{"accept_make_requests": false}
```

Idempotent — PUT-like semantics. `null` / absent means "don't change".

## Section 4 — Admin UI — Requests settings modal

The existing modal in `static/app.js` / `templates/index.html` gains a new checkbox row, in the same style as the existing `auto_approve` checkbox:

```
☐  Accept "make it" requests tonight
   When off, singers won't see the option to ask you to make a
   custom karaoke track. Pending requests are not affected.
```

Wire:

1. Modal-open fetches `GET /rotation/requests/config`, populates the checkbox from `accept_make_requests`.
2. On toggle → POST `{accept_make_requests: <new value>}` → success toast.
3. On failure → revert and show error.

Single change, no new UI patterns. The "Pending requests are not affected" sub-copy is a promise the backend keeps: flipping to off does **not** mass-reject existing pending make-requests.

## Section 5 — Data flow

### A. KJ toggles make-requests off

```
KJ admin UI            routes.py               sing_store.py
-----------            ---------               -------------
POST /rotation/requests/config {accept_make_requests: false}
                ───▶  store.set_accepting_make_requests(False)
                                ─▶ UPDATE rotation_meta
                                   SET value='0'
                                   WHERE key='sing_accept_make_requests'
                ◀── {accept_make_requests: false, ...}
```

### B. Singer searches while make-requests off, gets empty result

```
singer              sing.py / routes.py
------              -------------------
GET /sing/search?q=obscure   ───▶ unified_search(...)  → songs: []
                                  store.is_accepting_make_requests()  → False
                             ◀── {songs: [], make_requests_enabled: false}
                                (UI hides option 2, shows only 1 and 3)
```

### C. Stale-client protection

```
singer with cached JS (from when flag was on)
  ───▶ POST /sing/submit {source_type: "make", ...}
       sing.py: store.is_accepting_make_requests() == False
       ← 400 {"error": "make_requests_disabled"}
  (sing.js displays generic "couldn't send" error — same as any rejection)
```

### D. Punk pastes a gen.nomadkaraoke.com URL into option 1

```
singer pastes https://www.youtube.com/watch?v=<gen_published_id>
  ───▶ client treats it as youtube (existing path, no code change)
       state.selected = { source_type: "youtube",
                          source_ref: url, ... }
       [confirm step]
       POST /sing/submit  ───▶ create_request(source_type="youtube", source_ref=url)
                           ─── on KJ approve → existing yt-dlp download path
                                              → plays like any YouTube video
```

**Codebase audit confirmed** this flow already works end-to-end with no special handling. Gen-published URLs are just YouTube URLs from yt-dlp's perspective.

## Section 6 — Error handling & edge cases

| Situation | Behaviour |
|---|---|
| Singer has cached JS from before flag flipped | Backend rejects at submit time (§3d). UI surfaces generic error. Next `/sing/search` refresh updates the flag. |
| Search is still loading, singer rapid-fire submits empty-state card | Actions are click-disabled while `loading` state is true — same guard as the non-empty flow. |
| Singer pastes a non-YouTube URL into option 1 | Existing URL validation handles it — YouTube-only regex, unchanged. |
| Singer fills in option 2 with offensive content | Same as today — pending-review queue, KJ rejects with reason. |
| Singer clicks option 3's "Open gen.nomadkaraoke.com" then never comes back | Not our problem. No tracking, no retention hook. |
| KJ toggles make-requests off with pending make-requests in queue | They remain pending. KJ approves or rejects individually. Explicit per §4. |
| Empty-state triage rendered because query is < 3 chars | No — empty-state triage is only shown when the query is ≥ 3 chars AND the server returned `songs: []`. For shorter queries we keep the "type at least 3 characters" hint. |
| localStorage `sing_name` / `sing_phone` absent on empty-state submit | Already handled — any submission routes through the identity screen if name/phone missing. |

## Section 7 — Testing strategy

### Unit tests

| File | Covers |
|---|---|
| `test_sing_store.py` (extend) | `is_accepting_make_requests` default True; set → get round trip; persistence across store restart. |
| `test_sing_routes_config.py` (extend, or new) | `GET /rotation/requests/config` exposes `accept_make_requests`; POST updates it; partial-POST only changes specified field. |
| `test_sing_routes_search.py` (extend from Phase A's test) | `/sing/search` response includes `make_requests_enabled`; value reflects current store setting. |
| `test_sing_routes_submit.py` (likely already covers submit — extend) | `source_type=make` with flag off → 400 `make_requests_disabled`; with flag on → normal pending request creation. |

### Integration test

`test_sing_make_request_disable_e2e.py` (new):

1. Seed event; token enabled; make-requests flag on.
2. Singer searches (empty result); response includes `make_requests_enabled: true`.
3. KJ toggles off via `POST /rotation/requests/config`.
4. Singer re-searches (simulating next call); response now `make_requests_enabled: false`.
5. Singer submits `source_type=make` payload → 400 `make_requests_disabled`.
6. KJ toggles back on; submit succeeds.
7. KJ approves a make-request normally (no backend changes to make-flow).

### Manual verification runbook (`docs/TESTING.md`)

- [ ] Search obscure query → empty-state triage renders with 3 cards (flag on).
- [ ] KJ toggles make-requests off → singer refreshes search → only cards 1 and 3 render.
- [ ] Paste a YouTube link in option 1 → submit works, routes to confirmation.
- [ ] Fill artist+title in option 2, tap "Ask the KJ" → confirmation dialog pops, tap OK → submits.
- [ ] Tap "How it works" in option 3 → explainer expands inline.
- [ ] Tap "Open gen.nomadkaraoke.com" in option 3 → new tab opens to `https://gen.nomadkaraoke.com` (target="_blank", noopener).
- [ ] Submit a gen-published YouTube URL via option 1 → KJ approves → rotation plays it correctly end-to-end (one-time full integration test).

## Section 8 — Implementation plan

| # | Task | Files touched |
|---|---|---|
| 1 | `SingStore.is_accepting_make_requests` / `set_accepting_make_requests` + tests | `sing_store.py`, `tests/unit/test_sing_store.py` |
| 2 | Expose `accept_make_requests` on `GET/POST /rotation/requests/config` | `routes.py`, `tests/integration/test_sing_routes_config.py` |
| 3 | Expose `make_requests_enabled` on `GET /sing/search` + landing template dataset | `sing.py`, `templates/sing.html`, `tests/integration/test_sing_routes_search.py` |
| 4 | Submit-time enforcement for `source_type=make` | `sing.py`, `tests/integration/test_sing_routes_submit.py` (or existing test file) |
| 5 | Admin UI checkbox in Requests settings modal | `templates/index.html`, `static/app.js` |
| 6 | Singer-side empty-state triage (3 cards, option 3 explainer) | `static-sing/sing.js`, `static-sing/sing.css` |
| 7 | Remove the old top-level "Paste a YouTube link" / "Ask the KJ to make" `<details>` on non-empty search | `static-sing/sing.js` |
| 8 | E2E integration test | `tests/integration/test_sing_make_request_disable_e2e.py` (new) |
| 9 | Docs | `docs/CHANGELOG.md`, `docs/ARCHITECTURE.md`, `docs/TESTING.md`, `CLAUDE.md` if key files change |
| 10 | Version bump | `pyproject.toml` → `0.27.0` (minor — new config field + singer-visible flow) |

## Section 9 — Success criteria for Phase C

1. A punk searches for their niche song, gets zero hits, and sees a visible three-card triage — with card #2 hidden if make-requests are off.
2. Each card explains what the singer is committing to in terms of time and effort: YouTube is fastest, Ask-KJ is variable and may be declined, DIY is ~5–10 min of focused phone time.
3. The KJ can toggle make-requests on/off in the Requests settings modal. Toggling off hides card #2 for singers on next search, and the backend also rejects stale-client `source_type=make` submissions.
4. A singer who pastes a gen.nomadkaraoke.com-published YouTube URL via card #1 gets their request processed end-to-end with no backend changes — confirmed by the codebase audit.
5. The existing non-empty search flow is unchanged: normies and nerds never see the triage unless they deliberately empty their search results.
6. All new + existing tests pass.
