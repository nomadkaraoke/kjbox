# Singer Expectations UI — Roadmap

**Date:** 2026-04-18
**Sub-project:** 4 of 4 (depends on #1 shipping)
**Status:** Scope sketch — not yet designed. Revisit in a fresh session; some pieces (push notifications) have real complexity.

## Problem

Singers at a busy night ask two questions constantly: *"When am I up?"* and *"What are the rules?"* The paper-slip flow can't answer either well. Sub-project #1 gives us a web surface; this sub-project uses it to set expectations properly.

## In scope

### 4a. Better wait-time estimates

- Sub-project #1 ships a crude estimate: sum of durations of entries ahead. This sub-project upgrades it:
  - Account for unlinked entries (use avg song duration across tonight's sung entries as a default)
  - Account for entries being re-ordered (priority lane, BRB, Left)
  - Show a time **range**, not a false-precision single number ("~18–25 min")
  - Live-update as the rotation changes (already polling every 30s from #1; tighten to every 10s or use SSE)

### 4b. Push / live-update notifications

- "You're up in 2 songs" warning → give singers time to queue up at the mic
- "You're next" warning
- "You're now singing" (in case they missed the call)

Implementation options (decide at brainstorming):

- **Held-open confirmation tab** polling every 5–10s — simplest, works without permissions, but relies on the singer not closing the tab
- **Server-Sent Events** from `/sing/status/<id>` — efficient, still requires an open tab
- **Web Push (PWA)** — proper push notifications to a phone even when the browser is closed. Requires HTTPS (we have it), service-worker registration, VAPID keys, and a push backend. Works on modern Android + iOS 16.4+.
- **SMS** — most reliable, but cost + 10DLC + carrier registration hassle. Punt unless critical.

Lean: 4b ships as a **PWA with Web Push** if feasible, otherwise the held-open tab with SSE as a fallback.

### 4c. Rotation rules display

- Page at `/sing/rules` (linked from the landing page and the confirmation page)
- Content: the existing `desktop/rotation_rules.txt` — rendered nicely, not as raw text
- Explains the BRB rule, the turn-order rule, the priority/tip rule (once #3 ships), no-repeat rule, etc.
- Also surfaced **inline** on the confirmation page — short version: "Leave the mic area and miss your turn → you go to the back"

### 4d. "What's playing now" widget

- On the landing page and confirmation page: live-updated card showing the currently singing singer + song (drawn from rotation state)
- Fun factor: singers can see the flow of the night without watching the main screen

## Out of scope (defer further)

- **Singer-facing setlist / song preview.** Showing what songs are likely coming based on singer history — too crystal-ball-y and could spoil surprises. No.
- **Song request voting / tipping other singers' requests.** Different product. No.
- **In-browser audio monitor for singers to practice before their turn.** Fun but big scope. Not this project.

## Dependencies

- **Blocks on #1** — needs the public request form's confirmation page as the display surface
- **Complements #3** — once tip-for-priority ships, this UI needs to show "your entry has been bumped ahead by your tip"
- **Pulls from existing data** — rotation position, singer status, durations — all already tracked

## Open questions to resolve when we brainstorm this

### Wait-time estimates

- **Baseline song duration** — hard-code 4 min, or compute from tonight's sung entries, or from all-time archive?
- **Pauses and setup time** — the KJ spends time between songs announcing, muting, etc. Do we add a per-transition buffer (e.g. 30s)? Is it configurable?
- **Show range vs. single value** — a range is more honest but less reassuring. Some nights the range is so wide it's useless. Decision needed.
- **What do we show the singer while they're "Now Singing"?** "You're up! Break a leg" and stop computing estimates for them.

### Push notifications

- **PWA service-worker scope** — if the singer uses multiple devices or re-opens the page, how do we de-duplicate subscriptions?
- **iOS Web Push caveats** — requires installing the site as a PWA on iOS (Add to Home Screen). Is that friction acceptable, or do we fall back to held-tab polling?
- **Permission UX** — when do we ask for notification permission? On confirmation page? After first submit? Too early = decline; too late = miss the warning.
- **Offline events** — on the travel router with no internet, Web Push won't work (it requires internet). We may need SSE as the local fallback.

### Rules page

- **Source of truth** — today `rotation_rules.txt` lives in the `desktop/` tree and is also rendered as a printable HTML. Pick one source and make the web page reuse it.
- **Event-specific rules** — some venues have extra rules (e.g. "2-drink minimum", "last call at 1am"). Do we support per-event overrides, or just a single global ruleset?

## Non-goals for the brainstorming session

- Don't design a full account system or persistent singer preferences here — that's #2's territory
- Don't design the payment UI here — that's #3's territory. This project just needs to render the "your entry is paid" state once #3 ships.
