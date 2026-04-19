# Payment Integrations — Roadmap

**Date:** 2026-04-18
**Sub-project:** 3 of 4 (depends on #1 shipping; touches karaoke-gen)
**Status:** Scope sketch — not yet designed. Revisit in a fresh session; this one has the largest compliance/testing surface and should ship on its own PR.

## Problem

Two monetisation opportunities the current paper-slip flow can't support:

1. **Tip for priority.** On long-wait nights, singers would happily pay a few dollars to skip ahead in the rotation. The `rotation_entries.paid` flag exists today but is set manually by the KJ. We want a self-service flow where a singer pays via their phone and the rotation entry's `paid` flag flips automatically.
2. **Pay to gen a new karaoke track.** If a singer wants a song we don't have, they can ask the KJ to make it (already supported in sub-project #1 via the `make` source_type). Today that's free to the singer and costs us a karaoke-gen job. A paid flow lets the singer purchase the job themselves via the existing gen.nomadkaraoke.com payment.

## In scope

### 3a. Tip-for-priority

- Button on the singer's confirmation page: **"Skip ahead — tip $X"** (amount configurable by KJ)
- Taps opens Stripe Checkout or Stripe Payment Element (decide at brainstorming)
- On success, the linked rotation entry gets `paid = 1` and a priority-lane re-sort runs
- **Priority lane logic** — a decision point: options include (a) move to rotation position 2 (after "now singing"), (b) move ahead of all non-paid entries but behind other paid entries (FIFO within paid lane), (c) move by N positions. The existing `paid` flag already has some semantics in the drag-and-drop UI; need to audit.
- KJ controls: enable/disable, set tip amount, view tip history / revenue

### 3b. Pay-to-gen

- When a singer picks "Ask the KJ to make it" in the request form, offer: "Pay $X to have this made for you"
- Integrates with **karaoke-gen's existing payment flow** (gen.nomadkaraoke.com already does Stripe Checkout for user-review gen jobs)
- Likely mechanism: server-side call to gen's `POST /jobs` with a `payment_session_id` field; gen returns a Stripe Checkout URL; singer completes payment; gen processes the job; kjbox's existing `GenPoller` picks up the completed file and links it as before
- Contrast with free-for-KJ path: KJ can still trigger `POST /rotation/make` directly for free (internal job)

## Out of scope (explicitly not v1-of-this-subproject)

- Pre-paid credits / punch cards / memberships
- Splitting payments across multiple singers for duets
- Tipping the KJ directly (that's a separate flow — venue business model decision)
- Refunds automated from the kjbox UI (handle in Stripe dashboard for now)
- International currency handling (USD only to start)

## Dependencies

- **Blocks on #1** — needs the public request form as the payment entry point
- **Touches karaoke-gen** — 3b requires coordinating with the gen-side payment code. Might need changes to gen.nomadkaraoke.com's API (e.g. an idempotency key, or a kjbox-originated job marker)
- **Stripe account setup** — the Nomad Karaoke Stripe account already exists (used by karaoke-gen); just need a new product / price entity and webhook endpoint

## Open questions to resolve when we brainstorm this

### Architecture

- **Where does the Stripe webhook land?** Kjbox is intermittently online (event-only) and sits behind Cloudflare. Options: (a) webhook goes to gen.nomadkaraoke.com (always online) and gen relays to kjbox via polling or a callback URL, (b) webhook goes directly to `sing.nomadkaraoke.com/stripe/webhook` — but kjbox might be offline when the webhook fires
- **Idempotency and retries** — Stripe retries webhooks; the approval path must be safe to call twice with the same `payment_intent_id`
- **Payment before or after KJ approval?** If 3a charges before the KJ has approved the underlying request, we might need to refund. Lean: only offer "skip ahead" on already-approved rotation entries.

### Product

- **Tip amount:** fixed by KJ? tiered ($5 / $10 / $20)? open-ended with a minimum? Lean: tiered, configurable.
- **Refund triggers:** venue closes early, singer leaves, system failure. Need a refund button in the KJ UI, and someone decides policy.
- **Revenue split:** who gets the money — the KJ personally, Nomad Karaoke the business, the venue? Affects which Stripe account holds the funds.
- **Receipts:** Stripe sends email receipts automatically. Is that enough, or do we want a branded receipt showing the song / event?

### UX

- **Confirmation page real-estate.** The confirmation page from #1 shows position + wait. Adding a payment CTA risks clutter. A/B the placement.
- **"Paid" visual in rotation** — should the KJ-side rotation row show a tip icon + amount, and should the public "show upcoming rotation" hide the tip (or show it for social proof)?
- **Failed payment recovery** — what does the singer see? What does the KJ see?

## Non-goals for the brainstorming session

Don't bundle tip-for-priority and pay-to-gen into a single mega-feature. They share Stripe infra but the UX, the approval flow, and the integration surface are different. Consider splitting into 3a and 3b as separate specs if scope is too wide.
