# Singer "My Songs" persistence & visibility — plan

**Date:** 2026-07-16
**Repo:** kjbox / kj-controller (singer-facing `/sing/*` SPA)
**Branch:** feat/sess-20260716-2209-singer-my-songs

## Problem

A singer only sees their submitted songs on the **"done" screen** (`state.step === "done"`,
"Your songs tonight"). On a page **refresh**, `state.step` resets to `"landing"` and the
bootstrap block (`sing.js` bottom) only routes to `done` when the server injected a legacy
`INITIAL_REQUEST_ID` (`?r=<id>` — nothing generates this anymore). It **never consults
localStorage**, so the singer drops back to the bare "Request a song" screen and loses the
ability to see / cancel / change / reorder their songs — even though the request ids +
per-request `edit_token`s are already persisted in `localStorage` (`sing_my_request_ids`,
token-scoped) and the backend `/sing/my-requests` + done-screen UI fully support showing them.

## Root cause

Pure **routing / entry-point** gap. Data + backend + edit UI already exist. Fix is client-side
in `static-sing/sing.js` (+ a slot in `templates/sing.html`, styles in `static-sing/sing.css`).

## Design (chosen: persistent bar + smart restore + stale-night prune)

1. **Smart restore on boot** — if localStorage holds ids for the current token, probe
   `/sing/my-requests`; if tonight's songs come back (≥1), route to the "Your songs tonight"
   screen. Render landing first, upgrade to `done` async so there's no flash of an empty
   list on a fresh night.

2. **Persistent "My songs (N)" bar** — a slot (`#sing-mysongs-bar`) OUTSIDE `#sing-root`
   (survives the `root.innerHTML` reset), shown on every screen **except** `done` (which is
   itself the list). Shows count + a status-at-a-glance summary derived from the most-advanced
   song (🎤 You're up! / 🎤 You're next / #3 · ~12 min / Waiting for KJ…). Tapping opens `done`.
   Kept fresh by a light 20s bar poll while visible and not on `done`.

3. **Clear stale night data** — the token is reused across nights and localStorage isn't
   cleared, so last night's ids linger. `/my-requests` night-scopes them out server-side
   (returns them filtered). On a successful 200 we prune any stored id NOT present in the
   returned set → new-night boot cleanly shows landing with no phantom count. Cancelled songs
   still return (status cancelled) so they stay visible; only genuinely-gone ids are pruned.
   Never prune on a thrown (network/5xx) fetch.

## Implementation notes

- New `state.mySongs = { items, nowPlaying, loaded }`; `refreshMySongs()` (fetch + prune +
  update bar); `pruneRequestIds(token, keepIds)`; `_mySongsPillSummary(items)`;
  `updateMySongsBar()`; `startBarPoll()`/`stopBarPoll()` (`state._barPollTimer`).
- `render()` calls `updateMySongsBar()` on every step change.
- `pollMyRequests` (done screen) also updates `state.mySongs` so the bar is fresh on back-nav.
- Reuse `_statusLine`'s estimate fields (now_singing / position / range_low_s / range_high_s).

## Tests (e2e, Playwright — `tests/e2e/test_sing_frontend.py`)

- Refresh with tonight's songs → boots into "Your songs tonight" (async restore), bar hidden.
- Bar visible on landing with count; tapping opens done.
- Stale-night (`/my-requests` → empty) → bar hidden, stays on landing, localStorage pruned.
- Bar hidden when device owns no songs.
