# Singer Identity & Returning-Singer UX — Roadmap

**Date:** 2026-04-18
**Sub-project:** 2 of 4 (depends on #1 shipping)
**Status:** Scope sketch — not yet designed. Revisit in a fresh session once #1 has collected phone data in production.

## Problem

Today, singer identity in kjbox is just a free-text `singer` column on `rotation_entries`. Two issues:

1. **Name collisions merge stats.** Two different "Andrew"s get counted as one in `get_singer_stats()`. The singer stats panel can't tell them apart.
2. **No returning-singer UX.** If the same person sings at an event we hosted last month, they type their name fresh and there's no "welcome back, Andrew — last time you sang Bohemian Rhapsody" moment.

Sub-project #1 lands phone numbers into `sing_requests.phone`. This sub-project turns that data into identity.

## In scope

- **Phone-based dedup.** When a sing request comes in, look up prior requests by phone; if found, surface "returning singer: Andrew B. — sang 4 songs last event" in the KJ review queue.
- **Returning-singer prefill.** If a phone has submitted before, pre-fill their last-used singer name in the form (still editable).
- **Singer profile page.** At `/sing/me?t=<token>` — shows the singer their history on this device: songs sung tonight, across recent events, a basic "you've sung N times here" counter.
- **Merge into existing stats panel.** The KJ's current singer stats panel (shipped 2026-04-14) aggregates by name. Extend to aggregate by `(phone, name)` where phone is known, falling back to name-only for historical/manual entries.
- **KJ-side identity merge tool.** "These two names are the same person" → link their histories. Complements the existing `merge_singers` by working across events.

## Out of scope (defer further)

- **SMS verification.** Significant infra (Twilio/etc), cost, and a 10DLC registration. Only pursue if abuse becomes real.
- **Accounts / passwords / social login.** Too much friction for a live karaoke UX.
- **Cross-device identity.** This is local-SQLite-only. If we ever run multiple kjboxes, singer identity might need a cloud backend — separate future work.
- **GDPR-style data export / deletion endpoints.** Good hygiene but not critical for the volumes we see. Add when needed.

## Dependencies

- **Blocks on #1:** the `sing_requests.phone` column must exist and be populated. Can't start this until #1 has been live long enough to have a few events' data.
- **Existing singer stats panel** (shipped) is the UI entry point — this project extends it rather than replacing it.

## Open questions to resolve when we brainstorm this

- **Identity primary key:** phone string (normalised how?), or a new `singers` table with surrogate PK that phone rows point to?
- **Name conflict UX:** when two people share a phone (family member using the same phone), how do we disambiguate at review time?
- **Privacy:** do we show partial phone masks (`+1 ***-***-4567`) on the stats panel, or full numbers? (Lean: masked by default.)
- **History depth:** do we aggregate across archived nights (`rotation_archive` + `sing_requests` going back forever), or cap at last-N events?
- **Returning-singer in the review queue:** should the KJ see a one-line "returning singer" badge on the pending-request row, and what does it show? Song count? Last-event date? Tip history?
- **What happens to requests without phone numbers** (e.g. hand-written slips entered by KJ manually)? They bypass this identity system — keep the name-only fallback path working.

## Non-goals for the brainstorming session

Don't scope-creep into payments (#3) or expectations (#4). This sub-project is purely about *who is this person, and what do we know about them*.
