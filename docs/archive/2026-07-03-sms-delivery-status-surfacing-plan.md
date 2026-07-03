# SMS delivery-status surfacing on the rotation row

**Date:** 2026-07-03
**Version:** kj-controller 0.65.0
**Context:** Follow-up to the 2026-07-02 SMS outage (number never linked to the
approved 10DLC campaign → every send hard-rejected `40010`). Root-caused +
fixed operationally; this change closes the *observability* gap that let it rot
silently for ~2 weeks.

## Problem

The rotation row showed an optimistic "sent 11:39 PM" marker whenever an SMS had
been POSTed to Telnyx. But "sent" only means Telnyx *accepted* the API call
(HTTP 2xx / queued). The actual delivery outcome arrives later via the Telnyx
delivery-receipt (DLR) webhook, which writes `delivered` / `delivery_failed`
to `sms_log.status`.

The frontend only styled failures when `last_status === 'failed'` — but the DLR
never writes `'failed'`; it writes `'delivery_failed'`. So a carrier hard-reject
rendered as a plain neutral "sent" marker. The KJ had **no signal** that texts
were bouncing. (`'failed'` is only used for send-time API errors, a different,
rarer path.)

## Design

Three delivery states, derived from the latest `sms_log` row:

| State | Statuses | Marker | Colour | Button |
|-------|----------|--------|--------|--------|
| delivered | `delivered` | ✓ delivered {t} | green | ✉ SMS |
| failed | `failed`, `delivery_failed`, `sending_failed` | ✗ failed {t} | red (bold) | **✉ Retry** (red) |
| pending | `sent`, `queued`, anything else non-null | ⋯ sent {t} | amber | ✉ SMS |

- **Backend** (`routes.py` `_add_sms_status`): add `last_error` to each row's
  `sms` block (from the latest log row's `error`) so the ✗ tooltip can explain
  *why* — e.g. `40010 Not 10DLC registered`, opted-out, carrier reject. Docstring
  updated to reflect the DLR statuses (was stale: `"sent"|"failed"`).
- **Frontend** (`static/app.js`): new pure `smsDeliveryState(status)` collapses a
  raw status into one of the three states (the failure set now includes the DLR
  statuses — the actual bug). Marker text/colour/tooltip by state; the SMS button
  becomes a loud red **Retry** on failure (reuses the existing re-send flow — no
  new endpoint).
- **CSS** (`static/style.css`): `.rotation-sms-marker-{delivered,failed,pending}`
  and `.rotation-btn-sms-failed`.

## Non-goals / YAGNI

- No new retry endpoint — the existing SMS preview/send flow already re-sends.
- No historical delivery log UI — the row's latest state is enough.

## Deploy note (production-safety)

- `kj-autodeploy.service` is currently **inactive**; deploy is manual
  (`git pull` on the device). The change is frontend + a version bump + a tiny
  additive `routes.py` field.
- The `last_error` line touches `routes.py` — its effect (the error text in the
  ✗ tooltip) only appears after the next service **restart**. The ✓/✗/⋯ colours
  are pure frontend and appear on the next browser load once `app.js?v=` busts
  (version read at startup → new query param after restart, or a hard refresh).
- **Do the device restart between shows, never mid-song.** The colours degrade
  gracefully before the restart (tooltip just omits the reason).

## Testing

- Integration (`tests/integration/test_sms_routes.py`): `last_error` present in
  the `sms` block; `delivery_failed`/`delivered` pass through with correct error.
- Visual: booted the real app against a seeded in-memory DB with all three states
  and confirmed the rendered marker text, computed colours, and Retry button via a
  headless browser.
