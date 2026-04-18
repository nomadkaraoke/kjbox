# Public Singer Request Form — Implementation Plan

**Created:** 2026-04-18
**Branch:** `feat/sess-20260418-0116-public-request-form`
**Design spec:** [2026-04-18-public-request-form-design.md](./2026-04-18-public-request-form-design.md)
**Status:** In progress

## Overview

Implement sub-project #1 from the public-request-form roadmap: a QR-code-reachable web form for singers to submit song requests, a KJ-side pending-review queue, a short-lived event token tied to rotation lifecycle, and a Cloudflare tunnel hostname that's public (no Access gate) while the rest of the device stays gated.

User has authorised shipping through `/shipit` without further approvals. Ops steps that affect the live device (DNS record creation, `systemctl restart cloudflared`, `systemctl restart kj-controller`) remain **manual** and are listed as post-merge actions for the user to run.

## Requirements

### Functional

- [ ] Public URL `sing.nomadkaraoke.com` reachable without Cloudflare Access; serves only `/sing/*` routes
- [ ] Offline mode works via `http://<nomadpc-lan-ip>/sing/?t=<token>` on the travel router
- [ ] QR overlay on the HDMI singer screen displays the current event URL
- [ ] Singer flow: scan → name+phone → song search → confirm → confirmation with position/estimate
- [ ] Song search reuses `/rotation/search` (local + Divebar + KN) and adds YouTube URL + "make" option
- [ ] Review queue in KJ UI: approve / edit & approve / reject per pending row
- [ ] Auto-approve toggle skips the review queue
- [ ] Kill switch disables all public `/sing/*` routes
- [ ] Archive-rotation regenerates the token; sleep-mode-enter disables it (exit does not re-enable)
- [ ] Rate limit: 5 submits / 5 min / IP
- [ ] Host-based route guard: `sing.nomadkaraoke.com` can only reach `sing_bp` routes

### Non-functional

- [ ] 70%+ test coverage on new modules (matches project convention)
- [ ] No regression in existing rotation / playback / sleep flows
- [ ] Phone numbers never leave the device's SQLite (no sheet sync, no public API exposure)
- [ ] Singer UI follows the existing vanilla-JS pattern (no build step, no framework)

## Technical Approach

### Architecture at a glance

```
┌──────────────────────────────────────────────────────────────────┐
│  Flask app (single process)                                       │
│                                                                   │
│   ┌─────────────┐       ┌───────────────┐                        │
│   │  sing_bp    │  new  │  routes_bp    │ existing + new admins  │
│   │  /sing/*    │       │  /rotation/*  │ /rotation/requests/*   │
│   └──────┬──────┘       └───────┬───────┘                        │
│          │                      │                                 │
│          └──────────┬───────────┘                                 │
│                     │ before_request: host-guard                  │
│                     │                                             │
│                     ▼                                             │
│             ┌───────────────┐                                     │
│             │  sing_store   │ new — SQLite CRUD on sing_requests │
│             │  RotationStore│ existing — adds token helpers      │
│             └───────┬───────┘                                     │
│                     │                                             │
│                     ▼                                             │
│         ~/kjdata/rotation.db (shared SQLite)                      │
└──────────────────────────────────────────────────────────────────┘

cloudflared ingress (manual, post-merge):
  sing.nomadkaraoke.com  → https://localhost:443  (no Access rule)
  kjbox.nomadkaraoke.com → https://localhost:443  (Access-gated, existing)
```

### Key decisions / trade-offs

- **Same SQLite DB.** The `sing_requests` table lives alongside `rotation_entries` in `~/kjdata/rotation.db`. Keeps backup / archive simple; single-writer already works for rotation.
- **Admin endpoints on `routes_bp`, path-prefixed `/rotation/requests/*`.** Keeps public blueprint surgically small. Host-guard adds defence-in-depth.
- **In-memory rate limit.** A module-level `defaultdict(deque)` on the Flask app. Resets on service restart — good enough for v1 given we also have the token gate.
- **QR generation server-side.** Use `qrcode` pip package with its `SvgPathImage` factory — no Pillow dependency. Endpoint returns `image/svg+xml`. Smaller than a PNG and scales cleanly.
- **Approval dispatch via existing endpoints.** Rather than re-implementing download/gen/link logic, the approval route calls the existing helper functions (or imports them from `routes.py`) keyed on `source_type`.
- **Auto-sync QR overlay.** When a token regenerates, iterate overlays looking for any `qr_code` overlay with `config.follow_event_url == True`; update its `config.url`. KJ opts-in per overlay.

### New dependencies

- `qrcode` (pure Python, no Pillow needed when using `SvgPathImage`) — add to `requirements.txt`

## Implementation Steps

### Phase A — data + token plumbing

- [ ] A1. **`kj-controller/sing_store.py`** — new file. Class `SingStore`:
  - `__init__(db_path)` — reuses WAL-mode settings from `RotationStore`
  - `init_schema()` — creates `sing_requests` table + indexes per design spec
  - CRUD: `create_request(...)`, `list_requests(status=None, limit=None)`, `get_request(id)`, `update_status(id, status, reviewed_at, linked_entry_id=None, rejected_reason=None)`, `update_request(id, singer_name=None, song_artist=None, song_title=None, source_type=None, source_ref=None, source_meta=None)`
  - Token helpers on `rotation_meta` (since table is already there): `get_token()`, `regenerate_token()` (returns new value), `is_enabled()`, `set_enabled(bool)`, `is_auto_approve()`, `set_auto_approve(bool)`
  - `count_pending()` for badge display
- [ ] A2. **`kj-controller/app.py`** — wire `SingStore`:
  - Import + instantiate `flask_app.sing_store = SingStore(db_path)` (same `db_path` as rotation)
  - On first init, ensure a token exists (`get_token() or regenerate_token()`) and `set_enabled(True)` by default
- [ ] A3. **`kj-controller/config.py`** — add defaults:
  - `sing_public_url_base` (default `"https://sing.nomadkaraoke.com"`)
  - `sing_public_host` (default `"sing.nomadkaraoke.com"`)
  - `sing_local_url_base` (default `""`)
  - `sing_rate_limit_per_ip` (default `5`)
  - `sing_rate_limit_window_s` (default `300`)
- [ ] A4. **`kj-controller/tests/unit/test_sing_store.py`** — new file:
  - Schema created; columns and indexes present
  - `create_request` inserts with pending status; default fields
  - `list_requests` filters by status
  - `update_status` transitions + sets `reviewed_at`
  - `regenerate_token` returns new value, idempotent reads
  - `is_enabled` / `set_enabled` round-trip
  - `is_auto_approve` / `set_auto_approve` round-trip

### Phase B — public blueprint + host guard

- [ ] B1. **`kj-controller/sing.py`** — new file. Blueprint `sing_bp` with URL prefix `/sing`:
  - `@sing_bp.before_request` → token gate (via `@require_token` helper)
  - `GET  /`                  → render `sing.html`; sets `sing_token` session cookie
  - `GET  /search?q=<q>`      → reuse `rotation_search()` logic (refactor: extract shared helper)
  - `POST /submit`            → validate fields, rate-limit, `sing_store.create_request(...)`
  - `GET  /status/<id>`       → return JSON with position, estimated wait, approval status
  - `GET  /rules`             → render rotation rules (source: `desktop/rotation_rules.txt`)
  - Static assets served via `url_for('sing.static', ...)` — separate static folder
- [ ] B2. **Rate limiter** — module-level `defaultdict(deque)` keyed by `request.remote_addr`. On each submit: prune entries older than window, reject if `len >= sing_rate_limit_per_ip`. Add `X-Forwarded-For` awareness for Cloudflare tunnel (trust first hop).
- [ ] B3. **`kj-controller/app.py`** — register `sing_bp`:
  - `flask_app.register_blueprint(sing_bp, url_prefix='/sing')`
- [ ] B4. **Host-based route guard** — `@flask_app.before_request` hook in `app.py`:
  - Read allowed public hosts from config (`sing_public_host` + any config-provided aliases)
  - If `request.host` matches an allowed public host, only allow endpoints starting with `sing.` (blueprint name) → else `abort(404)`
  - Other hosts (LAN, `kjbox.*`) unaffected
- [ ] B5. **`kj-controller/tests/integration/test_sing_public_routes.py`** — new file:
  - Token missing → 403 / "not open" page
  - Token invalid → same
  - Token valid & disabled → same
  - Landing renders + sets cookie
  - `/search?q=x` returns JSON (short-circuit `len < 3`)
  - `/submit` rejects missing name/phone, accepts valid body, persists pending row
  - `/submit` rate limit: 6th request inside window → 429
  - `/status/<id>` returns pending then (after admin approval) approved with `rotation_entry_id`
- [ ] B6. **`kj-controller/tests/integration/test_host_guard.py`** — new file:
  - `GET /status` with `Host: sing.nomadkaraoke.com` → 404
  - `GET /rotation` with `Host: sing.nomadkaraoke.com` → 404
  - `GET /sing/` with `Host: sing.nomadkaraoke.com` + valid token → 200
  - `GET /status` with `Host: kjbox.nomadkaraoke.com` → 200 (baseline)

### Phase C — admin endpoints

- [ ] C1. **`kj-controller/routes.py`** — add admin routes on `routes_bp`:
  - `GET    /rotation/requests`                → `list_requests()` + counts grouped by status
  - `GET    /rotation/requests/config`         → `{token, enabled, auto_approve, public_url, local_url, pending_count}`
  - `POST   /rotation/requests/config`         → body `{enabled?, auto_approve?, regenerate?}` → apply changes
  - `GET    /rotation/requests/qr.svg`         → SVG QR of current event URL (tunnel or local, via `?scope=public|local`)
  - `POST   /rotation/requests/<id>/approve`   → dispatch on `source_type`, link rotation entry
  - `POST   /rotation/requests/<id>/edit`      → body `{singer_name?, song_artist?, song_title?, source_type?, source_ref?}` then approve
  - `POST   /rotation/requests/<id>/reject`    → `{reason?}` → mark rejected
- [ ] C2. **Approval dispatch** — helper `_approve_request(req)` in `routes.py`:
  - `local` → `rotation.add_entry(singer, song_artist+' - '+title, file_path=source_ref)` (use existing duration lookup)
  - `divebar` / `kn` / `youtube` → call existing `/rotation/download-and-link` logic by extracting it into a helper `_queue_download_and_link(singer, source, ...)`; link singer name + song
  - `make` → call existing `/rotation/make` logic via helper `_create_gen_job(singer, artist, title)`
  - Set `linked_entry_id` on the sing_request row on success
  - If auto_approve is on during submit → call the same `_approve_request` directly from `sing_bp.submit`
- [ ] C3. **Refactor: extract helpers** — in `routes.py`, lift the in-route logic from `/rotation/download-and-link` and `/rotation/make` into module-level functions callable by both the existing routes and the new approval dispatcher. Keep the existing routes as thin wrappers.
- [ ] C4. **`kj-controller/tests/integration/test_sing_admin_routes.py`** — new file:
  - List filters by status; includes counts
  - Approve with `source_type=local` creates rotation entry with `file_path`
  - Approve with `source_type=divebar` queues download + links; verify `download_id` set on rotation entry
  - Approve with `source_type=make` creates gen job (mock `gen_client.create_job`)
  - Reject marks row, doesn't create rotation entry
  - Edit-then-approve applies edits before dispatch
  - Config GET returns current state; POST regenerate changes token
  - Config POST toggles enabled + auto_approve

### Phase D — integration hooks

- [ ] D1. **`/rotation/archive` handler in `routes.py`** → on success, call `sing_store.regenerate_token()` + `set_enabled(True)`, then sync the QR overlay (D3).
- [ ] D2. **`/system/sleep-mode` POST in `routes.py`** → when entering sleep, call `sing_store.set_enabled(False)` **before** the sleep-enter script runs. Do NOT re-enable on exit.
- [ ] D3. **QR overlay auto-sync** — helper `_sync_event_url_overlays(overlay_manager, url)` in `sing.py`:
  - Iterate `overlay_manager.list_overlays()`
  - For each overlay where `type == 'qr_code'` and `config.get('follow_event_url') == True`: update `config['url']` to the new URL
  - Called from the admin config route (on regenerate) and from the archive hook
- [ ] D4. **KJ UI overlay editor** (minor) — add a checkbox "Link to current event URL" on the qr_code overlay form that sets/clears `config.follow_event_url`. Purely frontend change in `app.js`/`index.html`.
- [ ] D5. **Tests:**
  - `test_sing_admin_routes.py::test_archive_regenerates_token_and_reenables`
  - `test_sing_admin_routes.py::test_sleep_enter_disables_token`
  - `test_sing_admin_routes.py::test_sleep_exit_does_not_reenable_token`
  - `test_sing_admin_routes.py::test_overlay_sync_on_regenerate`

### Phase E — singer UI

- [ ] E1. **`kj-controller/templates/sing.html`** — new Jinja template. Single-page vanilla JS app. Mobile-first. Four visual steps: landing → identity → search → confirm/confirmation. Links to `/sing/static/sing.css` + `/sing/static/sing.js`.
- [ ] E2. **`kj-controller/static-sing/sing.js`** — new file (kept in a separate static tree registered on `sing_bp`):
  - `localStorage` persistence for `sing_name` and `sing_phone`
  - E.164-ish regex validation client-side; show inline error
  - Search-as-you-type (250ms debounce) → `/sing/search?q=...`
  - Results grouped by source with "Good to go" / "Download needed" / "YouTube" / "Make request" badges
  - Submit → POST `/sing/submit` → redirect to `/sing/?r=<id>` confirmation view
  - Confirmation: poll `/sing/status/<id>` every 30s; render position, estimated wait range (sum of known durations ± 20%), and expandable "Show upcoming" list (first names only)
- [ ] E3. **`kj-controller/static-sing/sing.css`** — new file. Mobile-optimised. Reuse dark theme / Nomad brand colours from `style.css` but styles scoped to `.sing` root class.
- [ ] E4. **Asset serving** — in `sing.py`, `Blueprint(..., static_folder='static-sing', static_url_path='/static')` so assets live at `/sing/static/*` (separate from KJ UI).
- [ ] E5. **Manual smoke** — from phone on LAN: open `http://<ip>/sing/?t=<token>` → walk through all four steps → confirm rotation entry appears in KJ UI.

### Phase F — KJ UI

- [ ] F1. **`kj-controller/templates/index.html`** — add new section `<div id="pending-requests-panel">` above the rotation queue (hidden if count=0). Also add a button in the settings area to open a new "Requests" modal.
- [ ] F2. **`kj-controller/static/app.js`** — new logic:
  - Poll `/rotation/requests/config` every 5s when the Requests panel is visible (to refresh pending count and token)
  - Render pending rows with Approve / Edit & Approve / Reject buttons
  - Requests modal: show current token, event URL (tunnel + local), embedded QR (`<img src="/rotation/requests/qr.svg?scope=public">`), Regenerate button (with confirm), Kill-switch toggle, Auto-approve toggle
  - Handle overlay editor addition for `follow_event_url` checkbox
- [ ] F3. **`kj-controller/static/style.css`** — styles for the pending panel (inline card) + Requests modal
- [ ] F4. **Manual smoke** — add a request via the singer flow; verify it appears in KJ UI within 5s; approve → rotation entry created; reject → row disappears; flip auto-approve toggle → new requests skip pending.

### Phase G — docs

- [ ] G1. **`docs/ARCHITECTURE.md`** — add a "Public Request Form" section with module list + host guard description; new API rows in the REST table.
- [ ] G2. **`docs/MINIPC-SETUP.md`** — new cloudflared ingress row for `sing.nomadkaraoke.com`; instructions for creating the DNS record.
- [ ] G3. **`docs/CHANGELOG.md`** — dated entry.
- [ ] G4. **`CLAUDE.md`** — update the "Key Files" list with `sing.py`, `sing_store.py`.
- [ ] G5. **`kj-controller/README.md`** — mention the singer request UI + its URL.

### Phase H — ship

- [ ] H1. `/test` — run full suite
- [ ] H2. `/test-review` — verify coverage target met for new code
- [ ] H3. `/docs-review` — ensure no doc gaps
- [ ] H4. `/coderabbit` — fix any real issues (skip nitpicks)
- [ ] H5. `/pr` — open PR with `@coderabbitai ignore`, summary of all 4 docs + MVP scope
- [ ] H6. **Merge** to main (auto-deploys in ~60s; code-only, frontend works without restart)

### Post-merge (manual, user only — not automatable from this session)

- [ ] U1. **DNS:** add CNAME `sing.nomadkaraoke.com` → `<tunnel-id>.cfargotunnel.com`
- [ ] U2. **cloudflared config on NomadPC:** add ingress entry
  ```yaml
  - hostname: sing.nomadkaraoke.com
    service: https://localhost:443
    originRequest:
      noTLSVerify: true
  ```
  before the catch-all `service: http_status:404`
- [ ] U3. **Cloudflare Access:** confirm no Access policy is attached to `sing.nomadkaraoke.com`
- [ ] U4. **Reload cloudflared:** `ssh nomadpc 'sudo systemctl restart cloudflared'`
- [ ] U5. **Restart kj-controller** (ideally during a break, not mid-song): `ssh nomadpc 'sudo systemctl restart kj-controller'`
- [ ] U6. **Smoke:** open `https://sing.nomadkaraoke.com/?t=<token>` from a non-Access browser; verify landing page; open the KJ UI and verify the Requests panel shows the token + URL

## Files to Create / Modify

| File | Action | Purpose |
|---|---|---|
| `kj-controller/sing_store.py` | Create | `SingStore` + `rotation_meta` token helpers |
| `kj-controller/sing.py` | Create | `sing_bp` public blueprint + token decorator + rate limiter + host guard helper |
| `kj-controller/templates/sing.html` | Create | Singer-facing SPA template |
| `kj-controller/static-sing/sing.js` | Create | Singer UI logic |
| `kj-controller/static-sing/sing.css` | Create | Singer UI styles |
| `kj-controller/app.py` | Modify | Register `sing_bp`, init `SingStore`, install host-guard before_request |
| `kj-controller/config.py` | Modify | Add sing_* config defaults |
| `kj-controller/routes.py` | Modify | Add `/rotation/requests/*` admin routes + refactor download/gen helpers + archive hook + sleep hook |
| `kj-controller/templates/index.html` | Modify | Pending Requests panel + Requests modal |
| `kj-controller/static/app.js` | Modify | Wire panel/modal actions; overlay `follow_event_url` checkbox |
| `kj-controller/static/style.css` | Modify | Panel + modal styling |
| `kj-controller/requirements.txt` | Modify | Add `qrcode` |
| `kj-controller/tests/unit/test_sing_store.py` | Create | Unit tests |
| `kj-controller/tests/integration/test_sing_public_routes.py` | Create | Public blueprint tests |
| `kj-controller/tests/integration/test_sing_admin_routes.py` | Create | Admin endpoint tests |
| `kj-controller/tests/integration/test_host_guard.py` | Create | Host-based route guard tests |
| `docs/ARCHITECTURE.md` | Modify | New module + API rows |
| `docs/MINIPC-SETUP.md` | Modify | cloudflared ingress row + DNS instructions |
| `docs/CHANGELOG.md` | Modify | Dated entry |
| `CLAUDE.md` | Modify | Mention new modules |
| `kj-controller/README.md` | Modify | Mention singer request UI |

## Testing Strategy

- **Unit:** `test_sing_store.py` covers the storage + token helpers (target ~95% coverage since it's pure logic)
- **Integration:** three new files for public routes, admin routes, and the host guard. All hit the Flask test client with `create_app(test_config)` — existing pattern. Mock `gen_client` where needed; reuse `test_rotation_routes` fixtures for DB setup.
- **Manual:** singer flow end-to-end from phone on LAN; KJ approval flow; token regenerate on archive; sleep mode disables.

## Open questions (during implementation)

- Should `sing_requests` archive alongside `rotation_entries` on night archive, or persist as a growing log? (Lean: persist — useful for sub-project #2.)
- Should the confirmation page's "Show upcoming" list refresh live as rotation moves forward, or snapshot-at-submit? (Lean: live refresh with the 30s status poll.)
- Should edit-and-approve allow changing the `singer_name` only, or also the phone? (Lean: singer_name only; phone is effectively identity.)

## Rollback Plan

Code-only rollback is fast:
- `git revert <merge-commit>` on `main` → auto-deploy pulls within 60s; `systemctl restart kj-controller` if needed
- The new `sing_requests` table remains but is harmless (ignored by rotation code)
- Cloudflare DNS record and cloudflared ingress entry can be left in place (they'll 502 gracefully if the app doesn't serve the hostname)

To fully tear down:
- Remove the `sing.nomadkaraoke.com` cloudflared ingress entry + DNS record
- Drop `sing_requests` table and remove `request_token*` rows from `rotation_meta`
