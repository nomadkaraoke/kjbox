# SMS Notifications — Design

**Date:** 2026-05-01
**Status:** Approved, implementing
**Author:** Andrew + Claude (brainstorm transcript in session log)

## Why

KJ needs a low-friction way to text singers "you're up next" when they're rotating into the Up Next slot. Tonight's show exposed the gap: phone numbers are collected on `sing_requests` but never used; calling singers' names on the mic loses anyone who's stepped outside or is mid-conversation. The fix is a manual, per-row "Send SMS" button that uses Telnyx.

## Scope

**V1 (this spec):**
- Manual "Send SMS" button on rotation rows where a phone number is available.
- One message type ("you're up next") with a per-event editable template + reset-to-default.
- Preview/edit panel before send — every send is reviewed by the KJ.
- Persistent per-row marker after send + "Re-send" affordance.
- TCPA-compliant disclosure on the singer-facing form + STOP footer on every outbound.

**V2+ (deferred):**
- Auto-send on status transitions.
- Multiple template types (sound check, setlist confirmed, etc.).
- Inbound STOP / HELP webhook handling with opt-out tracking in DB.
- Delivery receipt webhooks (currently we only know "Telnyx accepted").
- Bulk / group sends.

## Provider

**Telnyx.** Account already created, paid tier, toll-free number verification in progress (1-3 day turnaround). Credentials live in `.envrc` as `TELNYX_API_KEY` (REST auth) and `TELNYX_PUBLIC_KEY` (webhook signature verification — held for V2). The sender phone number is configured separately as `TELNYX_FROM_NUMBER` (the toll-free number being verified).

Feature is disabled at runtime if either `TELNYX_API_KEY` or `TELNYX_FROM_NUMBER` is missing — the button hides, and the modal shows "⚠ Not configured".

## Files

**New:**
- `kj-controller/sms.py` — Telnyx REST client (`send`), template rendering (`render_template`), phone normalization (`normalize_phone`). No Flask import.
- `kj-controller/sms_store.py` — `SmsStore` class wrapping a `sms_log` table. Per-thread connection pattern (same as RotationStore/SingStore after the 2026-05-01 fix).
- `kj-controller/tests/unit/test_sms.py` — render + truncation + normalization + Telnyx client (mocked `requests.post`).
- `kj-controller/tests/unit/test_sms_store.py` — CRUD + concurrent-write regression test.
- `kj-controller/tests/integration/test_sms_routes.py` — preview/send/config endpoints with mocked client.

**Touched:**
- `kj-controller/routes.py` — 3 new endpoints + extend `/rotation` response shape.
- `kj-controller/sing_store.py` — `get_sms_template`/`set_sms_template`, `get_sms_default_region`/`set_sms_default_region` meta helpers. Default template lives in code (or `sms.py`).
- `kj-controller/sing.py` — TCPA disclosure copy.
- `kj-controller/config.py` — read TELNYX env vars.
- `kj-controller/app.py` — instantiate `SmsStore`, attach to flask_app.
- `kj-controller/templates/index.html` — modal additions (template editor, default region, status indicator).
- `kj-controller/static/app.js` — per-row button rendering, preview panel, modal config wiring.
- `kj-controller/static/style.css` — button + panel styling.
- `kj-controller/static-sing/sing.js` — updated TCPA disclosure copy.
- `kj-controller/requirements.txt` — add `phonenumbers`.

## Data model

### New table — `sms_log` (in `rotation.db`)

```sql
CREATE TABLE IF NOT EXISTS sms_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at             TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    rotation_entry_id   INTEGER,
    sing_request_id     INTEGER,
    phone_e164          TEXT NOT NULL,
    body                TEXT NOT NULL,
    status              TEXT NOT NULL,        -- 'sent' | 'failed'
    telnyx_message_id   TEXT,
    error               TEXT,
    kj_user_agent       TEXT
);
CREATE INDEX idx_sms_log_entry   ON sms_log(rotation_entry_id);
CREATE INDEX idx_sms_log_sent_at ON sms_log(sent_at);
```

Append-only. Both `rotation_entry_id` and `sing_request_id` are nullable so the log survives entry deletion / cross-event archive.

### New meta keys (`rotation_meta`)

| Key | Default |
|---|---|
| `sms_template` | `"Hi {first_name}! You're up next at Nomad Karaoke — {song} by {artist}. Head to the stage. Reply STOP to opt out."` |
| `sms_default_region` | `"US"` |

### `/rotation` response — additive per-row

```json
"sms": {
    "available": true,
    "last_sent_at": "2026-05-01 00:34:12",
    "last_status": "sent"
}
```

`available` = true iff the row's `linked_entry_id` resolves to a `sing_requests` row with a non-empty phone. Lookup is one LEFT JOIN per `/rotation` call.

## Template + truncation

Default variables and per-variable caps:

| Variable | Max | Source |
|---|---|---|
| `{first_name}` | 20 chars | `singer_name.split()[0]` then truncate |
| `{song}` | 60 chars | `song_title` (or parsed from `song_artist` fallback) |
| `{artist}` | 40 chars | `song_artist` (parsed) |

Truncation strategy:
1. Truncate each variable to its cap, appending `…` (single Unicode ellipsis char, GSM-7 compatible) if cut.
2. Render the template.
3. If rendered body > 1600 chars (10 SMS segments, hard sanity cap), reject at server.
4. Frontend shows live char count + segment count; turns amber at 161, red at 1601.

Multi-segment sends are allowed (Telnyx handles automatically) but the default template fits in one segment to keep cost predictable.

## Phone normalization

Use `phonenumbers` (libphonenumber Python port). Process:
1. If raw starts with `+`, parse as-is.
2. Otherwise parse against the per-event `sms_default_region` (default `US`).
3. If `phonenumbers.is_valid_number(...)` is false, reject — preview returns 400 "phone not valid".
4. Format as `phonenumbers.format_number(..., E164)` for Telnyx.

## API

### `POST /rotation/sms/preview`

Renders the default template for a row so the preview panel populates server-side (single source of truth).

```
Request:  { "entry_id": 64 }
Response (200): {
    "phone_e164": "+18432594507",
    "first_name": "Celeste",
    "song": "Plump",
    "artist": "Hole",
    "body": "Hi Celeste! You're up next at Nomad Karaoke — Plump by Hole. Head to the stage. Reply STOP to opt out.",
    "length": 117,
    "segments": 1
}
Errors:
    400  no phone for this entry, phone invalid, entry not linked to a request
    404  entry not found
    503  SMS not configured
```

### `POST /rotation/sms/send`

Sends exactly the body the KJ approved (no server-side re-templating). Logs success and failure equally.

```
Request:  { "entry_id": 64, "body": "Hi Celeste! ..." }
Response (200): {
    "success": true,
    "sms_log_id": 17,
    "sent_at": "2026-05-01 00:34:12",
    "telnyx_message_id": "abc123…"
}
Failures (still logged):
    400  empty body / body > 1600 chars / no phone / entry not found
    502  Telnyx returned non-2xx; { "success": false, "error": "...", "sms_log_id": 17 }
    503  SMS not configured
```

### `POST /rotation/requests/sms-config`

Bolted onto the existing requests-config POST. Accepts optional `sms_template` and `sms_default_region`. `sms_template: null` resets to default.

### `GET /rotation/requests/config`

Gains: `sms_template`, `sms_default_region`, `sms_enabled` (true iff Telnyx env vars are set).

## Frontend UX

### Per-row button (rotation list)

- Phone available + never sent for this song → `✉ Send SMS` button in the existing actions cluster.
- Phone available + previously sent → `✉ Re-send` button + small grey marker `sent 9:34 PM`.
- No phone → button not rendered.

### Preview panel

Opens inline below the row (same pattern as the existing edit form).

```
┌─ Send SMS to Celeste (+1 843-259-4507) ──────────────┐
│  [text area pre-filled with rendered body]           │
│  117 chars · 1 segment             [Cancel] [Send]   │
└──────────────────────────────────────────────────────┘
```

- Auto-focus text area at end.
- Cmd/Ctrl+Enter = Send; Esc = Cancel.
- On Send: button disables + spinner. On 200: panel collapses, row's marker updates. On 4xx/5xx: error inline above buttons, panel stays open, body preserved.

### Modal additions (`sing-requests-modal`)

New "SMS notifications" section below the existing Set Code row:

```
SMS notifications
  [textarea with current template]
  Available variables: {first_name} (max 20), {song} (max 60), {artist} (max 40)
  [Reset to default] [Save template]

  Default country code: [US ▾]

  Status: ✓ Telnyx configured  /  ⚠ Not configured
```

### Singer-facing copy change (`sing.js`)

Replace current phone-field hint with:

> "By providing your number, you agree to receive a one-off SMS when you're up to sing. Msg & data rates may apply. Reply STOP to opt out."

## Testing

- **`test_sms.py`** — template rendering with all combinations of cap overflow, phone normalization for US/AU/GB local & E.164, malformed inputs, Telnyx client returns shape on 2xx/4xx/5xx + network error.
- **`test_sms_store.py`** — CRUD + 20-thread concurrent-write regression (mirrors the 2026-05-01 outage fix tests).
- **`test_sms_routes.py`** — preview/send happy paths, send with mocked Telnyx failure logs failure status, sms-config sets meta, GET config exposes new fields, /rotation includes sms block per row.

UI is not unit-tested (consistent with existing rotation UI). Manual smoke test post-deploy.

## Auth / security

No new auth — endpoints live behind the same network boundary as the rest of `/rotation/*` (LAN + Cloudflare tunnel). The Telnyx API key never leaves the server.

## Rollout

1. Land code on `main`, push.
2. Auto-deploy pulls within ~60s; backend changes need `systemctl restart kj-controller` to take effect (interrupts playback).
3. Verify env vars on device once Telnyx verification completes; restart again to pick them up.
4. Feature is dark until env vars are set — restart-now is safe.

## Open questions for V2

- Webhook for delivery receipts (mark `sent` → `delivered` / `bounced` based on Telnyx callbacks).
- Inbound STOP handling — auto-flag a phone as opted-out so we don't send again even if the KJ taps the button.
- Auto-send on `mark_up_next` transition (would slot into the existing `_after_mutation` hook).
- Cross-event opt-out (singer who STOP'd from last week's event shouldn't receive a button this week either).

## Telnyx setup — actual outcome (2026-06-04)

- Account: paid tier, balance ~$22.
- Messaging Profile: "Nomad Karaoke KJBox" (id `40019e4e-d369-4bd2-b3bf-2ec80e0825f2`), US whitelisted, no webhook URL.
- Toll-free path turned out to be **blocked on the user's paid tier** (account couldn't order toll-free), so we pivoted to a **local long-code**.
- Number provisioned: **`+18038053750`** (Columbia SC local), $1/mo, assigned to the messaging profile.
- `.envrc` + device systemd drop-in carry `TELNYX_API_KEY`, `TELNYX_PUBLIC_KEY`, `TELNYX_FROM_NUMBER`. Device returns `sms_enabled: True`.

## 10DLC follow-up (blocked on account verification)

The Brand registration submission below returns `403 / 20014 / "Account unverified"` until the user completes additional identity/business verifications at <https://portal.telnyx.com/#/account/verifications>. Once cleared:

```json
{
  "entityType": "PRIVATE_PROFIT",
  "displayName": "Nomad Karaoke",
  "companyName": "Nomad Karaoke LLC",
  "ein": "99-4114183",
  "phone": "+18036363267",
  "street": "612 Joan St",
  "city": "Columbia",
  "state": "SC",
  "postalCode": "29203",
  "country": "US",
  "email": "admin@nomadkaraoke.com",
  "website": "https://nomadkaraoke.com",
  "vertical": "ENTERTAINMENT",
  "brandRelationship": "BASIC_ACCOUNT"
}
```

Then register a `LOW_VOLUME` Campaign against the new Brand (sample message = the default template, opt-in URL = `https://sing.nomadkaraoke.com`) and assign `+18038053750` to it. Total cost: $4 brand + ~$2-4/mo campaign. Carrier approval: 1-3 weeks.

Until 10DLC is approved, Telnyx will accept sends and the button will appear to work, but US carriers may filter delivery unpredictably. Self-test sends ARE fine.

## 10DLC progress — actual outcome (2026-06-11)

Account verification cleared. Verified the full setup live via the Telnyx REST API:

- **Account:** verified. Balance ~$17.14.
- **Brand:** registered via the Telnyx web UI and **fully approved** — `identityStatus: VERIFIED`, `status: OK`, EIN matched.
  - `brandId: 4b20019e-b7e9-f2af-1f85-e4a1c203fd1e`, `tcrBrandId: BVPM26Y`.
  - Note: web-UI form set `vertical: TECHNOLOGY` (the earlier draft proposed `ENTERTAINMENT`) and `companyName: "Nomad Karaoke"` (not "…LLC"). Approved as-is; no action needed.
- **Campaign:** created via API `POST /v2/10dlc/campaignBuilder` on 2026-06-11 — `LOW_VOLUME`, sub-use-cases `ACCOUNT_NOTIFICATION` + `CUSTOMER_CARE`.
  - `campaignId / tcrCampaignId: 4b30019e-b816-36a3-5727-0074e6af09bb`.
  - `submissionStatus: PENDING`, `campaignStatus: TCR_PENDING`. Carrier/TCR review ~1-3 weeks.
  - Opt-in keywords can't be blank — TCR requires `START,UNSTOP` even when real opt-in is the web form (described in `messageFlow`). STOP/HELP handled by Telnyx automatically.
- **Number → campaign assignment:** BLOCKED. `POST /v2/10dlc/phoneNumberCampaign` returns `10036 "Campaign … is still pending and has not been approved yet."` Must retry once `campaignStatus` is approved. **This is the one remaining manual step.**

### Correction: self-test sends are NOT fine pre-registration

The 2026-06-04 note above ("Self-test sends ARE fine") is **wrong** under current carrier enforcement. A live test send 2026-06-11 (`from +18038053750` → `to +18036363267`, msg id `40319eb8-16b9-426b-bcdf-f89935c0d986`) was accepted/queued by Telnyx (cost $0.0141) but the carrier **hard-rejected** it:

```
status: delivery_failed
error 40010: "The sending number is not 10DLC-registered but is required to be by the carrier."
```

So US carriers now *reject* (not merely filter) all A2P traffic from this un-registered local long-code — including self-tests. **SMS will not deliver to any US mobile until the campaign is approved and the number is assigned.** The kjbox button correctly reports `sms_enabled: True` and the API accepts sends; the failure is downstream at the carrier. No further end-to-end testing is possible until approval.

### Optional config — outcome

- **Messaging-profile daily spend limit:** SET to **$5.00/day** (`daily_spend_limit_enabled: true`) to bound runaway cost.
- **Webhook + opt-out:** BUILT (v0.37.0, not the deferred-forever V2 it was). See below.

## Webhook + opt-out — implemented (2026-06-11, v0.37.0)

Brings forward three of the deferred "Open questions for V2": delivery receipts, inbound STOP handling, and cross-event opt-out.

**Endpoint:** `POST /sing/telnyx/webhook` — lives on the `sing` blueprint because the public host (`sing.nomadkaraoke.com`) only routes `sing.*` endpoints, and that's the one publicly-reachable surface. Unauthenticated but **Ed25519 signature-verified** against `TELNYX_PUBLIC_KEY` (added to `sms_config["public_key"]`); fails closed (401) if the key is unset or the signature/timestamp is bad (300s replay window). Always 200-acks recognised events so Telnyx won't retry.

**Behaviour:**
- **Delivery receipts** (`message.finalized` / `message.sent`) → `SmsStore.update_status_by_telnyx_id()` flips `sms_log.status` to the carrier's status (`delivered` / `delivery_failed`) and records the error. The KJ now sees real delivery state, not just "Telnyx accepted".
- **Inbound STOP** (`message.received`, first token in `_STOP_KEYWORDS`) → `SmsStore.record_opt_out(phone)`. **START/UNSTOP** → `clear_opt_out`. New `sms_opt_outs` table, keyed by E.164 so it persists across nights/events.
- **Send path** (`/rotation/sms/send`) now checks `is_opted_out()` and refuses with **403** + a logged `failed` row before calling Telnyx.

**Pure logic in `sms.py`:** `verify_webhook_signature`, `parse_webhook_event`, `classify_inbound_keyword` — all unit-tested with a generated Ed25519 keypair (no network). Telnyx auto-sends the carrier-side STOP/HELP replies; we only mirror the decision locally.

**Not yet wired (deploy-time, per decision):** the messaging-profile `webhook_url` is still unset. Set it only after the handler is deployed — see CHANGELOG deploy steps. End-to-end verification is impossible until the 10DLC campaign is approved and real messages flow; until then it's covered by mocked unit tests only.

## Related follow-up (low-pri)

`restore_from_sheet` (`rotation_sync.py`) resets `sqlite_sequence` for `rotation_entries`, allowing cross-event ID reuse. The SMS button-visibility lookup is now defensive against this (newest sing_request wins, mirroring the SEND path), but the underlying ID reuse is still latent. Either stop resetting the sequence, or clear stale `sing_requests.linked_entry_id` on archive.
