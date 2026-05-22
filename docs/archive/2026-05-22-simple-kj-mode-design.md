# Simple KJ Mode — Design

**Date:** 2026-05-22
**Status:** Spec — ready for implementation plan
**Branch:** `feat/sess-20260522-0428-simple-kj-mode`

## Motivation

Andrew's brief (verbatim):

> "We need to add a toggle-able mode to make the KJbox system super simple to use for a novice or stand-in KJ. In that mode, we should only allow singers to request songs from the existing collection / downloadable known web versions (KN lookup), disable entering arbitrary YouTube URLs or free text, disable made-for-you on demand, etc.
>
> Then on the KJ controller side, strip it back to only showing a more minimal KJ UI, collapsing/hiding options which shouldn't be necessary for a 'good enough' karaoke show. Basically when I have someone else standing in for me, I think it's acceptable to simplify things massively for the person running the show so they don't need to worry about manually searching for tracks (karaokenerds, divebar, youtube, etc.) and they don't need to accept manual rotation additions either — they'll announce at the start of the show that it's a QR-code-only night, have a couple of printed posters of the 'Scan to sing' QR code, and use a simplified KJ UI where requests come in from people who have selected an existing song in the kjbox library / something we can confidently download (e.g. from divebar gcs), so all the operator has to do is approve the incoming requests and manage the rotation, click play and 'done', announce each singer, etc."

## Goals

1. **Restrict singer requests** to sources we can confidently fulfil without operator judgment: local library, Divebar (GCS-mirrored), and Karaoke Nerds (yt-dlp-downloadable). No paste-a-YouTube-URL, no make-on-demand, no defer-version-to-KJ.
2. **Hide** all KJ controls the stand-in shouldn't need. Leave: pending-request approval, rotation management, playback controls, and a toggle to switch back to advanced.

## Non-goals

- **Tamper-resistance.** Per Andrew's call: "we don't need to hide it or secure it, I'll actively tell the stand-in KJ they can enable advanced mode if they're feeling confident." The toggle is plainly visible in the System section.
- **Per-event scoping.** Like the existing singer-config flags, this persists across rotation archives. Andrew flips it back to off when he's running shows again.
- **Backend enforcement of hidden KJ controls.** A poking stand-in could still send `POST /upload` from devtools. We only enforce server-side on `/sing/submit` because that endpoint is on the public internet via `sing.nomadkaraoke.com` and singers can craft requests freely; the KJ UI is kiosk-physical.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  sing_store.sing_meta (existing key/value table)                 │
│    kj_simple_mode  ←  new key (default "0")                      │
└─────────────────────┬────────────────────────────────────────────┘
                      │
       ┌──────────────┴───────────────┐
       │                              │
       ▼                              ▼
  KJ controller (advisory)    Singer SPA (advisory + server-enforced)
  body.simple-mode CSS        /sing/ context: simple_mode
  hides ~half the panels      /sing/submit: narrowed source allowlist
  banner: "Simple Mode ON"    sing.js: trims triage cards & kj_pick
```

Single flag, two consumers. Server enforces only what's reachable from the public internet (the singer endpoint).

## Data model

One new key in the existing `sing_meta` table — no migration needed (`_get_meta` returns the default when absent):

```python
# sing_store.py
SIMPLE_MODE_KEY = "kj_simple_mode"  # "1" | "0", default "0"

def is_simple_mode(self):
    return self._get_meta(SIMPLE_MODE_KEY, "0") == "1"

def set_simple_mode(self, enabled):
    self._set_meta(SIMPLE_MODE_KEY, "1" if enabled else "0")
```

## API changes

All adjustments to existing endpoints; no new routes.

### `GET /rotation/requests/config`

Add `simple_mode` to the response:

```diff
 {
   "token": "1234",
   "enabled": true,
   "auto_approve": false,
   "accept_make_requests": true,
+  "simple_mode": false,
   "public_url": "...",
   "local_url": "...",
   "pending_count": 2
 }
```

### `POST /rotation/requests/config`

Accept `simple_mode` in the request body, alongside the existing flags:

```python
if "simple_mode" in data:
    store.set_simple_mode(bool(data["simple_mode"]))
    changed["simple_mode"] = bool(data["simple_mode"])
```

### `GET /status`

Add `simple_mode` to the `/status` payload, which is polled every 2s by `app.js`:

```python
status["simple_mode"] = current_app.sing_store.is_simple_mode()
```

This is what drives the KJ-side body class — the flag is in lockstep with the existing poll, no separate fetch needed.

### `GET /sing/`

Pass `simple_mode` to `render_template("sing.html", ...)`. `sing.html` propagates it via `data-simple-mode` on `#sing-root` (mirrors the existing `data-make-requests-enabled` pattern).

### `POST /sing/submit`

Narrow the allowlist when the flag is on, after the existing `_ALLOWED_SOURCES` check (defence-in-depth for stale clients):

```python
_SIMPLE_MODE_SOURCES = {"local", "divebar", "kn"}

if store.is_simple_mode() and source_type not in _SIMPLE_MODE_SOURCES:
    return jsonify({"error": "simple_mode_disabled_source"}), 400
```

The existing `make_requests_disabled` short-circuit is unchanged — both checks apply.

### `GET /sing/search`

Add `simple_mode` to the response alongside `make_requests_enabled`, so the singer SPA can re-read it after each search and adapt mid-session if the KJ flips the toggle:

```diff
 {
   "songs": [...],
+  "simple_mode": false,
   "make_requests_enabled": true,
   "karaoke_nerds_timeout": false
 }
```

## Singer SPA (`static-sing/sing.js`) — changes

`state.simpleMode` is initialised from `#sing-root[data-simple-mode]` on boot and refreshed from each `/sing/search` response.

When `simpleMode` is true:

- **Empty-state triage cards are suppressed.** The three fallback cards in the search-came-up-empty state are skipped: paste-YouTube card (~line 802), ask-the-KJ-to-make-it card (~line 855), and the `kj_pick` deferral card (~line 950).
- **Per-result `kj_pick` link is hidden.** If a song has multiple versions, the singer still sees them — they pick a specific one. We just don't offer the "let the KJ choose" shortcut.
- **Empty-results message** changes from triage cards to a single line: *"We don't have that one. Try another search, or talk to the KJ at the front."*
- **Submit guard.** If the singer somehow reaches a disallowed `source_type` (cached PWA with stale code), the server returns 400 with `simple_mode_disabled_source`. The SPA already has a generic "couldn't send" error path; it shows that plus a "refresh the page" hint.

## KJ controller — changes

### Mode toggle (new System subsection)

A new subsection at the **top** of `<div class="container system-controls">`, above "Media & Output":

```html
<div class="system-subsection" id="kj-mode-section">
  <div class="system-subsection-label">Mode</div>
  <div class="system-subsection-row">
    <div class="simple-mode-toggle">
      <span>Simple Mode (for stand-in KJ)</span>
      <label class="overlay-toggle">
        <input type="checkbox" id="simple-mode-switch"
               onchange="toggleSimpleMode(this.checked)">
        <span class="slider"></span>
      </label>
    </div>
    <p class="simple-mode-hint">
      Hides search panels, manual add, and advanced controls. Singers can only
      request from the local library, Divebar, or Karaoke Nerds.
    </p>
  </div>
</div>
```

`toggleSimpleMode(checked)` POSTs to `/rotation/requests/config` with `{ simple_mode: checked }`. The `/status` poll picks up the change on the next tick and applies the body class.

### Body class + CSS-driven hide

`app.js` reads `status.simple_mode` each poll and toggles `<body class="simple-mode">`. A single `style.css` block hides panels:

```css
body.simple-mode .kn-search-section,
body.simple-mode .yt-search-section,
body.simple-mode .db-search-section,
body.simple-mode .download-section,
body.simple-mode .available-songs,
body.simple-mode .browser-mode-panel,
body.simple-mode .overlay-panel,
body.simple-mode #col2,
body.simple-mode .rotation-add-btn,
body.simple-mode .rotation-new-btn,
body.simple-mode .rotation-restore-btn,
body.simple-mode .rotation-paths-btn,
body.simple-mode .rotation-undo-btn,
body.simple-mode .rotation-redo-btn,
body.simple-mode #np-pitch-group,
body.simple-mode .system-subsection:not(#kj-mode-section) {
  display: none !important;
}

body.simple-mode .main-layout { justify-content: center; }
body.simple-mode #col1 { max-width: 720px; width: 100%; }
```

Notes:
- `#col2` is hidden entirely (it contains all six right-column panels). The individual `.<panel>-section` selectors are listed for clarity but `#col2` alone would do it; we keep both for grep-ability when someone refactors.
- `.system-subsection:not(#kj-mode-section)` collapses every System subsection except the Mode one.
- `#np-pitch-group` is the pitch control inside the floating now-playing bar; the bar itself stays visible.

### Guidance banner

A small banner is inserted above the rotation list when simple mode is on, rendered by `app.js` (toggle existence based on `status.simple_mode`):

```html
<div class="simple-mode-banner">
  Simple Mode is ON · Approve incoming requests → tap a row to play →
  mark done → announce next singer.
</div>
```

Styling: muted, single line, dismissible-looking but not dismissible (always-on reminder).

## Behavior summary — what's visible when

| Panel / control                                | Advanced | Simple |
|------------------------------------------------|:--------:|:------:|
| Playback Controls (pause/restart/fade/stop)    | ✅       | ✅     |
| Karaoke / Filler volume + Seek slider          | ✅       | ✅     |
| Now-playing pitch control                      | ✅       | ❌     |
| Rotation list, Refresh, Requests button        | ✅       | ✅     |
| Rotation Undo/Redo, Paths, Restore             | ✅       | ❌     |
| Rotation **+ Add** (manual entry)              | ✅       | ❌     |
| Rotation **New Rotation** (archive)            | ✅       | ❌     |
| Pending Requests panel                         | ✅       | ✅     |
| Singer stats panel                             | ✅       | ✅     |
| Overlays panel                                 | ✅       | ❌     |
| Screen Preview (VNC)                           | ✅       | ✅     |
| System → Mode toggle                           | ✅       | ✅     |
| System → everything else                       | ✅       | ❌     |
| Col 2 entire (KN/YT/Divebar/Upload/Songs/Browser) | ✅    | ❌     |
| Singer SPA: paste YouTube URL                  | ✅       | ❌     |
| Singer SPA: ask the KJ to make it              | ✅¹      | ❌     |
| Singer SPA: `kj_pick` defer-to-KJ              | ✅       | ❌     |
| Singer SPA: search local/divebar/KN            | ✅       | ✅     |

¹ Subject to the existing `accept_make_requests` flag — Simple Mode hides it regardless.

Decision on Screen Preview: **kept visible** in simple mode. It's a low-cost confidence check — stand-in can glance at the thumbnail to confirm the venue display matches the room, see when a song actually starts, etc.

## Edge cases

- **Pending request whose source is now disallowed.** If a singer submitted a `youtube` request before Simple Mode was switched on, the request stays in the pending panel and remains approvable. The allowlist applies only to **new** submissions. (The pending panel doesn't disappear — only the search/add panels do.)
- **Stale singer PWA.** A singer with a cached service-worker copy of `sing.js` from before Simple Mode might still see the paste-YouTube card. Server-side rejection on `/sing/submit` catches it; UI shows the generic "couldn't send" error. On next `/sing/search` the SPA picks up `simple_mode: true` and trims its UI.
- **KJ flips the toggle mid-show.** `/status` polls every 2s; KJ UI reconfigures within a tick. Singers update on their next search or page refresh. No connections break, no in-flight requests are cancelled.
- **Multi-version songs in KN search.** The version picker UI is unchanged — singer sees all versions and selects one. We just don't offer the kj_pick shortcut.
- **Mode toggle while pending requests exist.** No effect on pending requests. Toggle changes future behavior only.

## Out of scope / future work

- A "test mode" or dummy-submit preview for Andrew to verify the singer UI without occupying the rotation.
- Auto-enabling Simple Mode on a schedule (by event tag, day of week, etc.).
- A separate KJ login for the stand-in. The kiosk model assumes physical presence; no auth.
- Restricting KN results to community-brand-ranked versions only. Decided against: yt-dlp downloads any KN result reliably, and forcing brand preference at search time would surprise singers who can already see all versions in advanced mode.

## Testing

### Backend (pytest in `kj-controller/tests/`)

- `test_sing_store.py`: round-trip `is_simple_mode()` / `set_simple_mode()`; verify default `False` when key absent.
- `test_sing_routes.py`:
  - `/sing/submit` returns 400 with `simple_mode_disabled_source` for `youtube`, `make`, and `kj_pick` when flag is on.
  - `/sing/submit` returns 200 for `local`, `divebar`, `kn` when flag is on.
  - `/sing/search` response includes `simple_mode` field.
  - `/sing/` render context includes `simple_mode` (smoke-check the template arg).
- `test_routes.py`:
  - `GET /rotation/requests/config` includes `simple_mode`.
  - `POST /rotation/requests/config` with `{simple_mode: true}` flips the flag.
  - `GET /status` includes `simple_mode`.

### Frontend (manual smoke)

- Flip toggle in System → Mode. Confirm:
  - body class `.simple-mode` applied.
  - Col 2 disappears; rotation header trims; System subsections collapse except Mode; overlays panel gone; screen preview gone.
  - Rotation banner appears above the list.
- Toggle back off. Confirm everything returns.
- Open the singer SPA on a phone with the flag on. Search for a song that has no results; confirm the empty-state message is the new one (not the triage cards). Search for a multi-version song; confirm `kj_pick` chip is absent. Submit a `local` request; confirm it succeeds.

No new integration tests beyond the above — the SQLite + Flask layer is already covered, and one parameterized unit case per endpoint is enough.

## Implementation order

1. `sing_store.py`: add `SIMPLE_MODE_KEY`, `is_simple_mode`, `set_simple_mode` + unit tests.
2. `routes.py` (`get_sing_config` / `update_sing_config` / `get_status`): wire the flag in/out + tests.
3. `sing.py` (`landing`, `search`, `submit`): pass `simple_mode` to template, add to search response, narrow submit allowlist + tests.
4. `templates/sing.html`: add `data-simple-mode` attribute on `#sing-root`.
5. `templates/index.html`: add the System "Mode" subsection.
6. `style.css`: simple-mode hide rules + banner styling.
7. `app.js`: read `simple_mode` from `/status`, toggle body class, render/clear rotation banner, POST when the switch flips.
8. `static-sing/sing.js`: read `data-simple-mode` and `/sing/search` response; hide triage cards, hide `kj_pick`, update empty-state copy.
9. Local pytest pass; manual smoke locally; commit; push to main (with explicit permission — auto-deploy on NomadPC).

Estimated change surface: ~9 files, ~250 net lines added.
