# Public Singer Request Form — Design Spec

**Date:** 2026-04-18
**Sub-project:** 1 of 4 (MVP — core flow only)
**Scope:** Publicly accessible web form that lets singers submit song requests from their phones via a QR code, with a KJ-side review queue. Deferred: returning-singer identity, payments, expectations UI.

## Summary

Today, singers submit requests on paper slips at the KJ desk. As events grow, this creates a queue at the desk and eats into KJ attention. This spec introduces a public web request form reachable via QR code — from anywhere with internet (via a new Cloudflare tunnel hostname) or from the travel-router wifi at venues without internet.

Requests submitted via the web form do **not** go straight into the rotation by default; they land in a **pending review queue** on the KJ UI where the KJ approves, edits, or rejects each one. An auto-approve toggle lets the KJ switch to direct-to-rotation when they trust the crowd. Hand-written slips remain supported throughout — the web form is additive.

## Goals

- Singers can submit requests from their phones without approaching the KJ
- KJ keeps full control: pending requests are reviewed before entering the rotation
- Works with and without internet (local wifi + Cloudflare tunnel)
- Captures phone numbers for future returning-singer identity (sub-project #2)
- Supports all existing song sources: local catalog, Karaoke Nerds, Divebar, YouTube URL, and "ask KJ to make it"
- Abuse-resistant: short-lived event token + IP rate limit + kill switch
- One-click disable for the KJ (kill switch + sleep-mode auto-disable)

## Non-goals (explicitly deferred)

- SMS verification of phone numbers (sub-project #2)
- Returning-singer prefill by phone lookup (sub-project #2)
- Payment flows — tip-for-priority, pay-for-gen (sub-project #3)
- Push notifications / "you're up in N songs" (sub-project #4)
- Wait-time estimation beyond a crude sum of known durations (sub-project #4)
- Multi-device / per-venue request routing (future work; architecture leaves room)

## Architecture

### Public URL

- New Cloudflare tunnel hostname: `sing.nomadkaraoke.com` → `https://localhost:443` on NomadPC, **no Cloudflare Access rule**
- Existing `kjbox.nomadkaraoke.com` remains behind Access (KJ only)
- Offline / travel-wifi: QR encodes `http://<nomadpc-lan-ip>/sing/?t=<token>` — a DHCP reservation on the GL.inet router pins the IP (captive-portal DNS override is a nice-to-have, not required for v1)
- Future-proofed for per-device hostnames (e.g. `sing123.nomadkaraoke.com`) — tunnel-config-only change, no app code impact

### Flask blueprint — strict separation public vs. admin

**Public blueprint** `sing_bp` (new file `sing.py`), mounted at `/sing/*`:

- `GET  /sing/`                  → landing page (token required; sets session cookie)
- `GET  /sing/search?q=<query>`  → thin wrapper over existing `/rotation/search`
- `POST /sing/submit`            → create a pending request
- `GET  /sing/status/<req_id>`   → confirmation / position / estimate (for the singer's tab)
- `GET  /sing/static/*`          → separate static tree so singer UI evolves independently of KJ UI

**Admin endpoints** live on the existing `routes_bp` under `/rotation/requests/*`
(intentionally **not** under `/sing/*` so they can't accidentally be exposed via the public tunnel):

- `GET    /rotation/requests`              → list pending/approved/rejected requests
- `POST   /rotation/requests/<id>/approve` → create a rotation entry, mark approved
- `POST   /rotation/requests/<id>/edit`    → edit singer name / song fields, then approve
- `POST   /rotation/requests/<id>/reject`  → mark rejected (silent to singer)
- `GET    /rotation/requests/config`       → current token, enabled flag, auto-approve flag
- `POST   /rotation/requests/config`       → regen token, toggle enabled/auto-approve

### Host-based route guard (defence in depth)

cloudflared routes by hostname, not by path — so `sing.nomadkaraoke.com/<anything>` would otherwise reach any Flask route. Add a `before_request` hook on the Flask app:

```
if request.host matches the public sing hostname(s):
    only allow endpoints registered on sing_bp (+ /sing/static/*)
    anything else → 404
```

Public hostnames come from `config.json` (`sing_public_host`, plus the auto-detected LAN-IP variant). Requests from `kjbox.nomadkaraoke.com` and local LAN hostnames are unaffected (still gated by Cloudflare Access or LAN-only respectively).

This guard is a belt-and-braces check — even if a future contributor accidentally registers a sensitive route anywhere in the app, it stays invisible to the public tunnel.

### Token lifecycle

One active token per event, stored in the existing `rotation_meta` table:

| Key                        | Value                                    |
|----------------------------|------------------------------------------|
| `request_token`            | 16-char URL-safe random string           |
| `request_token_enabled`    | `'1'` or `'0'`                           |
| `request_auto_approve`     | `'1'` or `'0'` (default `'0'`)           |

- **Regenerate:** automatically when KJ clicks Archive rotation (new night); also manually from the Requests panel
- **Disable:** KJ kill-switch toggle; also auto-disabled when `SleepManager` enters sleep mode (hook into existing `enter_sleep()`)
- **Validation:** a `@require_token` decorator on every `/sing/*` public route checks `request.args['t']` (or session cookie) against `request_token`, and also requires `request_token_enabled == '1'`

### Data model — new table `sing_requests`

```sql
CREATE TABLE sing_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    token           TEXT NOT NULL,                       -- event correlation
    singer_name     TEXT NOT NULL,
    phone           TEXT NOT NULL,                        -- stored as-entered, no validation beyond E.164-ish regex
    song_artist     TEXT NOT NULL DEFAULT '',
    song_title      TEXT NOT NULL DEFAULT '',
    source_type     TEXT NOT NULL,                        -- 'local' | 'divebar' | 'kn' | 'youtube' | 'make'
    source_ref      TEXT,                                 -- file_path / file_id / youtube_url / null
    source_meta     TEXT,                                 -- JSON blob for source-specific fields (e.g. KN brand_code)
    notes           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',     -- 'pending' | 'approved' | 'rejected'
    rejected_reason TEXT,
    reviewed_at     TEXT,
    linked_entry_id INTEGER                               -- FK to rotation_entries.id after approval
);

CREATE INDEX idx_sing_requests_status ON sing_requests (status);
CREATE INDEX idx_sing_requests_token  ON sing_requests (token);
```

- Lives in the same SQLite DB as `rotation_entries` (`~/kjdata/rotation.db`)
- Phone number is plain text — SSD-only, never synced to Google Sheet
- Archiving a rotation does **not** archive `sing_requests` — they're event-scoped via `token`, but the full history stays on the device (useful for future analytics)

### Approval → rotation entry

When a request is approved (either by KJ action or auto-approve), the server calls the appropriate existing code path based on `source_type`:

| source_type | Action                                                                         |
|-------------|--------------------------------------------------------------------------------|
| `local`     | `rotation.add_entry(..., file_path=source_ref)` — instant link                  |
| `divebar`   | Reuse `POST /rotation/download-and-link` logic → queue download, auto-link      |
| `kn`        | Same as divebar (KN results carry a YouTube URL for yt-dlp)                     |
| `youtube`   | Same as above but source is a bare YouTube URL                                   |
| `make`      | Reuse `POST /rotation/make` logic → gen API job, poller links when complete     |

`sing_requests.linked_entry_id` is set to the new rotation entry's id for traceability.

## Singer UX flow

1. **Scan QR.** QR on the HDMI screen during filler music (new `qr_code` overlay pre-seeded by the Requests panel). URL: `https://sing.nomadkaraoke.com/?t=<token>`.
2. **Landing page.** Single-page vanilla-JS app; same tech stack as the KJ UI. Shows event name (from config), "Submit a song" button.
3. **Identity step.** Name (required) + phone number (required, E.164-ish client-side regex `^\+?[0-9\s\-()]{7,20}$`). Both saved to `localStorage` under keys `sing_name` and `sing_phone`; pre-filled on repeat visits.
4. **Song search step.**
   - Search-as-you-type input hits `/sing/search?q=...`, reusing `/rotation/search` output
   - Results grouped: "In our library" (local catalog + Divebar cross-reference), "Community karaoke" (KN without local match), "YouTube URL" (manual text box), "Ask the KJ to make it" (option)
   - Each result shows artist + title + source badge ("Good to go" / "Download needed" / "YouTube video" / "Make request")
5. **Confirm step.** Summary of the pick + optional note field + submit button.
6. **Confirmation page.** "You're #N in the queue" (if auto-approved) or "Sent to KJ for review" (if pending). Estimated wait = sum of durations of entries ahead. Expandable "Show upcoming rotation" — renders first names + songs only (no phones, no full names).
7. **Status tab stays open.** Confirmation page polls `/sing/status/<req_id>` every 30s; updates the position/estimate as the rotation advances. (Real-time push is sub-project #4.)

### Error / edge cases

- Invalid or missing token → plain HTML error page: "Requests aren't open right now. Ask the KJ."
- Token disabled / sleep mode → same error page
- Rate-limit hit → HTTP 429, friendly message "You've submitted a lot — wait a few minutes"
- Rejected by KJ → singer's status tab updates to "Sent to KJ" → "Please see KJ" (no reason given, keeps the rejection soft)

## KJ UX changes

### Pending Requests panel (inline, above rotation)

Shown only when `pending_count > 0`. For each pending request:

- Row: `[singer_name] ([phone]) — [artist] — [title] — [source_badge]` + action buttons
- Actions: **Approve** (→ creates rotation entry at bottom), **Edit & Approve** (opens inline edit, same fields as rotation edit), **Reject** (silent; row disappears from list)
- Header controls: **Approve all** (for trusted crowds), **Clear rejected** (cleanup)

### Requests settings panel (new tab or modal)

- **Event QR preview** — rendered from current token
- **Event URL** — `https://sing.nomadkaraoke.com/?t=<token>` (copy button) + local IP variant
- **Regenerate token** button (confirm: invalidates in-flight sessions)
- **Kill switch** toggle — disables `/sing/*` entirely
- **Auto-approve** toggle — skips the pending queue, creates rotation entries directly

### Integration points with existing flows

- **Archive rotation** (`POST /rotation/archive`) → regenerate token as a side effect, reset `request_token_enabled = '1'`
- **Sleep mode enter** → set `request_token_enabled = '0'`
- **Sleep mode exit** → **do not** auto-re-enable (KJ must flip it back manually — prevents surprise re-opening)
- **QR overlay** — seed / update the `qr_code` overlay's text field with the current event URL when the token changes

## Configuration

New keys in `config.json`:

| Key                        | Default                              | Description                                       |
|----------------------------|--------------------------------------|---------------------------------------------------|
| `sing_public_url_base`     | `"https://sing.nomadkaraoke.com"`    | Base URL for the tunnel QR                        |
| `sing_public_host`         | `"sing.nomadkaraoke.com"`            | Host value used by the route guard                |
| `sing_local_url_base`      | `""` (auto-detected from LAN IP)     | Override for the local QR (optional)              |
| `sing_rate_limit_per_ip`   | `5`                                  | Max submits per IP per window                     |
| `sing_rate_limit_window_s` | `300`                                | Rate-limit window in seconds                      |

First-install defaults:
- `request_token_enabled = '1'`
- `request_auto_approve = '0'` (review queue on)
- Token is generated on first app start if absent

## Abuse defense

- **Token gate** — all `/sing/*` public routes require a valid, enabled token (rejected with the "not open" page, not a 401)
- **Per-IP rate limit** — in-memory dict on the app object (reset on restart — good enough for v1). 5 submits / 5 min / IP → HTTP 429
- **Sleep mode** — `/sing/*` routes fail closed when `request_token_enabled == '0'`
- **Phone field** — stored only locally, not synced, not shown to other singers
- **No signup / login** — there are no credentials to leak or stuff
- **Future (out of scope for v1):** Cloudflare Turnstile CAPTCHA on the form; Cloudflare WAF rules on the `sing` hostname; event-code word-of-mouth fallback if QR screenshots get shared in group chats

## Testing

### Unit tests

- `sing_store.py`:
  - Create / list by status / approve / reject / get_by_id
  - Token validation decorator (missing, invalid, expired/disabled)
- `rotation_meta` token helpers:
  - Generate / regenerate / enable / disable

### Integration tests

- `sing_bp` public routes:
  - Landing page loads with valid token, 403-page with invalid/missing/disabled
  - Submit creates a pending row; phone + name required
  - Rate-limit blocks after N submits
  - `/sing/status/<id>` returns pending initially, then approved position after approval
- Admin routes under `/rotation/requests/*`:
  - List pending; approve creates rotation entry with correct `file_path` / `url_fallback` / gen-job depending on `source_type`
  - Reject marks row rejected, does not create rotation entry
  - Approve-all processes multiple in order
- **Host-based route guard:**
  - Request to `/rotation/requests` with `Host: sing.nomadkaraoke.com` → 404
  - Request to `/status` (KJ-only endpoint) with the public host → 404
  - Same request with `Host: kjbox.nomadkaraoke.com` → normal response
- Auto-approve config: submit → rotation entry created directly, no pending row
- Archive rotation regenerates the token and flips enabled back on
- Sleep-mode enter disables token; exit does not re-enable

### Manual smoke

- End-to-end phone → submit → KJ approves → rotation entry appears
- QR overlay displays correct URL after regenerate
- Offline mode: phone on travel router wifi hits `http://<ip>/sing/?t=...` without DNS

Coverage target: 70%+ for new modules (matches existing convention).

## Deployment steps

One-time ops work (requires user approval per production-safety rules):

1. **DNS**: add `sing.nomadkaraoke.com` CNAME to the Cloudflare tunnel
2. **cloudflared config**: on NomadPC, add a new `ingress` entry routing `sing.nomadkaraoke.com` → `https://localhost:443` with `noTLSVerify: true` (same as `kjbox`)
3. **Cloudflare Access**: confirm the `sing.*` hostname has no Access policy attached
4. **Service restart**: `systemctl restart cloudflared` + `systemctl restart kj-controller`
5. **Post-deploy**: open `https://sing.nomadkaraoke.com/?t=<token>` from a non-Access browser and confirm the landing page loads

Documentation updates:

- `docs/MINIPC-SETUP.md` — new cloudflared hostname row in the ingress table
- `docs/CHANGELOG.md` — dated entry
- `docs/archive/NETWORK-CONFIG-BACKUP.md` — updated tunnel config dump

## Open implementation questions (for the planner)

- Should the existing `qr_code` overlay auto-sync from the stored token, or should the KJ manually trigger a re-render? (Lean: auto-sync — fewer moving parts.)
- Should the `sing_requests.phone` column have any format normalisation on write, or stored exactly as typed? (Lean: exactly as typed; normalisation belongs in sub-project #2.)
- Should the review queue show phone numbers by default, or hidden behind a "show" toggle for over-shoulder privacy? (Lean: show — the KJ will want them visible for identity.)
- Where do rejected requests go — keep on screen as a collapsed log, or disappear entirely? (Lean: collapsed log for the night, cleared on Archive.)

## Build sequence (rough)

1. DB schema + `sing_store.py` + token helpers (with tests)
2. Public blueprint `sing.py` with landing/search/submit/status (stubbed templates)
3. Admin blueprint endpoints (list/approve/reject/edit/config)
4. KJ UI: Pending Requests panel + Requests settings panel
5. Singer UI: landing + identity + search + confirm + confirmation (mirror the existing UI's vanilla-JS style)
6. Integration: approval paths for each `source_type` + auto-approve config
7. Hooks: archive → regen, sleep → disable, overlay sync
8. Deploy: cloudflared config, DNS, Access review, docs
