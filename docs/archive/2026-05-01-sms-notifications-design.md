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
