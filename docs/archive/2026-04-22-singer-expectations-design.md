# Singer Expectations UI — Design

**Date:** 2026-04-22
**Sub-project:** 4 of 4 (follows #1 public request form, which shipped in PR #80)
**Scope decision:** Full scope — 4a wait estimates + 4b Web Push + 4c rules page + 4d "what's playing now" widget. (See "Scope rationale" below.)
**Roadmap source:** `docs/archive/2026-04-18-singer-expectations-roadmap.md`

---

## Problem

Singers at a busy night ask two questions constantly: *"When am I up?"* and *"What are the rules?"* The public request form shipped in sub-project #1 gives us a web surface the singer can reach from their phone; this sub-project uses it to set expectations properly.

The four pieces, in order of singer-facing impact:

1. **Wait-time estimates** — the current confirmation page shows `sum(durations) ± 20%`, which is false precision. Singers deserve an honest range grounded in tonight's actual data.
2. **Push / live-update notifications** — "you're up in 2" is the single most-requested feature on any karaoke night. Without it, singers either hover at the stage or miss their turn.
3. **Rules page** — answers "what are the rules?" without the KJ repeating themselves 30 times a night.
4. **"What's playing now" widget** — makes the landing page feel alive and gives singers a pulse of the night while they wait.

## Scope rationale

The full-scope decision (A from Q1) accepts that Web Push is meaningfully more work than the other three combined — service worker, VAPID keys, subscription lifecycle, iOS Add-to-Home-Screen friction. The argument for shipping it with this project rather than deferring: singers notice what's missing, and a "keep your tab open" contract is a worse UX than a proper notification. The design below makes the push layer **best-effort with an honest polling fallback**, so a failure of the push channel degrades to today's working polling flow rather than a missed-turn outage.

## Non-goals

- SMS fallback — deferred (roadmap non-goal; 10DLC + carrier cost).
- Per-event rule overrides — single global ruleset for now.
- LAN-only push fallback via SSE for offline events — considered and deferred (Section 4, Q7 decision: rely on `navigator.onLine` banner + existing polling).
- Big-delay notifications ("wait grew by 10min") — chatty, hard to tune without data; add later if singers complain.
- Anything singer-identity / returning-singer — that's sub-project #2, starts after this ships.
- Tip-for-priority UI — sub-project #3. This design leaves data-model space for it (ladder-reset on entry change) but doesn't implement it.

---

## Section 1 — Component overview

**New files:**

- `kj-controller/templates/sing_rules.html` — standalone rules page. Restyled port of `desktop/rotation_rules_printable.html`'s copy.
- `kj-controller/static-sing/manifest.json` — PWA manifest. Rendered dynamically so `start_url` includes the current event token.
- `kj-controller/static-sing/sw.js` — service worker. Handles `push` / `notificationclick`. Basic shell cache for offline page render (no runtime caching).
- `kj-controller/push_dispatcher.py` — new module. `PushDispatcher` class owns VAPID config, subscription CRUD, ladder-decision logic, and send loop. Wired onto `RotationManager.push_dispatcher` (mirrors existing `self.media` pattern).
- `kj-controller/tests/test_wait_estimate.py`
- `kj-controller/tests/test_push_dispatcher.py`
- `kj-controller/tests/test_push_subscriptions.py`
- `kj-controller/tests/test_sing_routes_push.py`
- `kj-controller/tests/test_rotation_push_hook.py`
- `kj-controller/tests/test_sing_push_e2e.py`

**Modified files:**

- `kj-controller/templates/sing.html` — add manifest link, iOS meta tags, touch icon, theme color.
- `kj-controller/static-sing/sing.js` — service-worker registration, subscription flow, install-prompt UX, "what's playing now" widget rendering, offline banner, upgraded wait-estimate rendering.
- `kj-controller/static-sing/sing.css` — styles for new widget, push prompt button, offline banner, rules short-form `<details>`.
- `kj-controller/sing.py` — new routes: `GET /sing/rules`, `GET /sing/manifest.json`, `POST /sing/push/subscribe`, `POST /sing/push/unsubscribe`, `GET /sing/now`. Updated response shape on `GET /sing/status/<id>`.
- `kj-controller/sing_store.py` — new table schema (`sing_push_subscriptions`), CRUD helpers, housekeeping delete.
- `kj-controller/rotation.py` — `_after_mutation()` calls `self.push_dispatcher.notify_rotation_changed()` when `self.push_dispatcher` is set.
- `kj-controller/app.py` — initialise `PushDispatcher`, bootstrap VAPID keys, wire onto `app.rotation`.
- `kj-controller/config.py` / `config.json` — new config keys (listed in Section 2).
- `kj-controller/requirements.txt` — add `pywebpush` + transitive `cryptography`.

**Explicitly unchanged:**

- Conky rotation overlay and `desktop/rotation_rules.txt` / `desktop/rotation_rules_printable.html` — singer page reuses the printable copy but does not consolidate (accepting drift; see Q5).
- Existing `/sing/status` polling loop — push sits alongside as a second channel, not a replacement. Polling continues to be the safety net.

---

## Section 2 — Data model

### New SQLite table (in `rotation.db`)

```sql
CREATE TABLE IF NOT EXISTS sing_push_subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    token           TEXT NOT NULL,
    phone           TEXT NOT NULL,
    singer_name     TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    p256dh          TEXT NOT NULL,
    auth            TEXT NOT NULL,
    user_agent      TEXT,
    last_sent_state TEXT,
    last_seen_at    TEXT,
    disabled_at     TEXT,
    UNIQUE(token, endpoint)
);
CREATE INDEX IF NOT EXISTS idx_sing_push_token_phone
    ON sing_push_subscriptions(token, phone);
```

Columns:

| Column | Purpose |
|---|---|
| `token` | Event token — scopes the subscription to a single event. When the KJ rotates tokens, orphan subs become ignorable. |
| `phone` | Normalised phone (same value as `sing_requests.phone`). Join key for "find this singer's entries". |
| `singer_name` | Debug/logging only. Not a join key. |
| `endpoint` / `p256dh` / `auth` | Standard Web Push subscription triple. |
| `user_agent` | Debug aid — Safari vs. Chrome Android vs. desktop. |
| `last_sent_state` | JSON. Dedup state: `{"entry_id": N, "ladder_step": "up_in_2", "sent_at": "..."}`. Only fire a push if `(entry_id, ladder_step)` differs from this. |
| `last_seen_at` | Updated on each `/sing/status` poll from this device. Not currently used; reserved for future "was this subscription heard from recently" checks. |
| `disabled_at` | Soft-disable flag. Set when push service returns 404/410 (endpoint gone). |

### Config keys (added to `config.json`)

| Key | Default | Purpose |
|---|---|---|
| `sing_estimate_transition_s` | `30` | Per-slot buffer added between songs. |
| `sing_estimate_default_song_s` | `240` | Fallback duration when the singer's entry has no linked file and we have fewer than 3 done entries tonight. |
| `sing_estimate_min_spread_s` | `120` | Minimum ± spread on the estimate range. Prevents the range from collapsing. |
| `vapid_subject` | `"mailto:andrew@beveridge.uk"` | VAPID contact (required by spec). |
| `vapid_public_key` | auto-gen on first boot | Served in `/sing/manifest.json` and embedded in the page for `pushManager.subscribe()`. |
| `vapid_private_key` | auto-gen on first boot | Server-side only. Never exposed. |

### VAPID keypair bootstrap

In `app.py` startup:

1. If both `vapid_public_key` and `vapid_private_key` are set in config → use them.
2. Else → generate a fresh pair via `pywebpush`'s built-in helper, write to `config.json`, log the action.
3. If `config.json` is not writable → fall back to in-memory keys; log ERROR; push still works for this process lifetime but subscriptions invalidate on restart.

### Housekeeping

When `SingStore.rotate_token(new_token)` is called (KJ starts a new event), also run:

```sql
DELETE FROM sing_push_subscriptions
 WHERE token != :new_token
   AND created_at < datetime('now', '-7 days', 'localtime');
```

Rationale: keep this week's subs (a singer might reopen a bookmark tomorrow and their sub still works); garbage-collect anything older. Table stays bounded.

---

## Section 3 — Feature walkthrough

### 3a. Wait-time estimates

Server-side helper `compute_estimate(entries, target_entry_id, config) -> dict`:

```python
def compute_estimate(entries, target_id, cfg):
    done = [e for e in entries if e["status"].lower() == "done"]
    done_durations = [e["duration"] for e in done
                      if e.get("duration") and e["duration"] > 0]
    if len(done_durations) >= 3:
        baseline = mean(done_durations)
        stdev = pstdev(done_durations)
        spread_source = "tonight"
    else:
        baseline = cfg["sing_estimate_default_song_s"]
        stdev = None
        spread_source = "fallback"

    active = [e for e in entries
              if e["status"].lower() not in ("done", "left")]
    ahead = []
    position = None
    target_status = None
    for i, e in enumerate(active):
        if e["id"] == target_id:
            position = i + 1
            target_status = e["status"]
            break
        dur = e.get("duration") or baseline
        if dur <= 0:
            dur = baseline
        ahead.append(dur)

    buffer = cfg["sing_estimate_transition_s"] * len(ahead)
    expected = int(sum(ahead) + buffer)

    if stdev is not None:
        raw_spread = int(stdev)
    else:
        raw_spread = 180  # 3 min fallback
    spread = max(cfg["sing_estimate_min_spread_s"], raw_spread)

    return {
        "position": position,
        "expected_s": expected,
        "range_low_s": max(0, expected - spread),
        "range_high_s": expected + spread,
        "spread_source": spread_source,
        "close_to_front": position is not None and position <= 2,
        "now_singing": target_status == "Now Singing",
    }
```

Response shape change to `GET /sing/status/<id>` — adds an `estimate` sub-object:

```json
{
  "request": {...},
  "estimate": {
    "position": 5,
    "expected_s": 1140,
    "range_low_s": 960,
    "range_high_s": 1320,
    "spread_source": "tonight",
    "close_to_front": false,
    "now_singing": false
  },
  "now_playing": {...},
  "queue": [...]
}
```

The existing top-level `position` and `estimated_wait_s` keys are kept for a transition period so pre-deploy cached clients don't break — client code switches to reading from `estimate.*` immediately, and the legacy keys can be removed in a later cleanup PR.

Client rendering in `sing.js` `pollStatus()`:

| Condition | Display |
|---|---|
| `estimate.now_singing` | `🎤 You're up — break a leg!` |
| `estimate.position === 1` | `🎤 You're next — head to the mic` |
| `estimate.position === 2` | `About 1 song to go` |
| `estimate.position >= 3` | `You're #N — about {low}–{high} min` where low/high are `Math.round(range_*_s / 60)` |
| `estimate.position == null` | (fall through to existing "waiting for KJ" / "rejected" / etc. copy) |

Replaces the existing `±20%` math (drop lines 442–446 of `sing.js`).

### 3b. Push notifications

#### Subscription flow — Android, desktop Chrome, installed iOS PWA

1. Singer submits → confirmation screen renders. (Existing flow.)
2. After a **2 second delay** (so the "you're in!" status line registers first), check `Notification.permission`:
   - `"default"` → render a prominent **🔔 Notify me when I'm up** primary button above the existing status line. Tap handler:
     1. `Notification.requestPermission()`
     2. On `"granted"` → `await sw.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: <vapid_public_key> })`
     3. `POST /sing/push/subscribe` with `{token, phone, singer_name, endpoint, keys: {p256dh, auth}}`
     4. Swap the button for a muted `✓ Notifications on` confirmation.
   - `"granted"` → auto-subscribe silently (opted in from a prior visit this event). No UI flash.
   - `"denied"` → render a muted hint: `"Notifications blocked — keep this tab open for updates."`
3. Independent of the push prompt: listen for `beforeinstallprompt` (Android/desktop Chrome). Capture the event, offer an **Install home-screen icon** secondary button. Not required for push but a nice-to-have.

#### Subscription flow — iOS Safari, not installed as PWA

1. Detect iOS via `/iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream`.
2. Detect standalone via `window.matchMedia('(display-mode: standalone)').matches`.
3. If iOS and not standalone → render an inline dismissible card on the confirmation screen:

   > **📱 iPhone? Get tapped when you're up.**
   > Tap [share-icon] → **Add to Home Screen**, then reopen from your home screen. You'll then be able to enable notifications.

   Non-blocking. Singer can dismiss or ignore and still use the polling flow.
4. If iOS and standalone → treat as the Android flow (iOS 16.4+ supports Web Push in installed PWAs).

#### Server-side dispatch

`PushDispatcher.notify_rotation_changed()`:

```
on rotation change (called from _after_mutation, debounced 500ms):
    token = sing_store.get_token()
    if not token: return
    entries = rotation.get_entries()
    active_subs = db.query(
        "SELECT * FROM sing_push_subscriptions "
        "WHERE token = ? AND disabled_at IS NULL",
        (token,))
    for sub in active_subs:
        target = next_entry_for_phone(entries, sub["phone"])
        if target is None:
            continue  # singer has no active entries
        step = decide_ladder_step(target, entries)
        if step is None:
            continue  # too far from front
        proposed = {"entry_id": target["id"], "ladder_step": step}
        last = json.loads(sub["last_sent_state"] or "{}")
        if (last.get("entry_id") == proposed["entry_id"]
                and last.get("ladder_step") == proposed["ladder_step"]):
            continue  # dedup
        payload = render_payload(step, target)
        executor.submit(send_push, sub, payload, proposed)  # daemon pool
```

`next_entry_for_phone(entries, phone)` — return the singer's closest-to-front non-done, non-left entry (i.e. `min(position) WHERE phone(entry) == phone AND status NOT IN done/left`). Phone-to-entry matching goes through `sing_requests.phone` via the entry's `linked_entry_id` backlink, so KJ-only manual entries (no phone) are correctly ignored.

`decide_ladder_step(target_entry, all_entries)`:

```
if target_entry["status"].lower() == "now singing":
    return "now_singing"
active = [e for e in all_entries if e["status"].lower() not in ("done", "left")]
try:
    pos = [e["id"] for e in active].index(target_entry["id"]) + 1
except ValueError:
    return None
if pos <= 2: return "up_next"   # positions 1 and 2 both get "up next"
if pos == 3: return "up_in_2"   # position 3 means 2 songs ahead
return None
```

Mapping: position 3 = 2 entries ahead = "up in 2"; position 2 = 1 entry ahead = "up next" (stand by); position 1 = top of queue, about to be called = also "up next" (catches big-reorder jumps where a singer skips from pos 4 to pos 1). Dedup on `(entry_id, ladder_step)` means a normal 3→2→1 progression only sends `up_in_2` once then `up_next` once.

`send_push(sub, payload, proposed_state)`:

```python
try:
    webpush(
        subscription_info={
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        },
        data=json.dumps(payload),
        vapid_private_key=self.vapid_private_key,
        vapid_claims={"sub": self.vapid_subject},
    )
    db.update_last_sent_state(sub["id"], proposed_state)
except WebPushException as e:
    status = getattr(e.response, "status_code", None)
    if status in (404, 410):
        db.disable_subscription(sub["id"])
    elif status in (400, 401, 403, 413):
        log.error("push send failed sub=%s status=%s", sub["id"], status)
    elif status == 429:
        log.warning("push service rate-limited sub=%s", sub["id"])
    else:
        log.warning("push send error sub=%s: %s", sub["id"], e)
```

Approve/reject pushes take a separate code path — wrapped onto `approve_sing_request` and the (existing or new) `reject_sing_request` helpers. These bypass the rotation scan and fire `PushDispatcher.notify_request_decision(request_id, decision)` which looks up subs by phone and sends immediately with a fixed ladder step of `"approved"` or `"rejected"`.

#### Payload shape

Compact JSON (<500 bytes) so we're comfortably under the push payload limit:

```json
{
  "title": "You're up in 2! 🎤",
  "body": "Bohemian Rhapsody — head back to the venue",
  "tag": "sing-ladder-<entry_id>",
  "icon": "/sing/static/icon-192.png",
  "badge": "/sing/static/badge-72.png",
  "data": {
    "request_id": 47,
    "step": "up_in_2"
  }
}
```

Copy per ladder step:

| Step | Title | Body |
|---|---|---|
| `approved` | You're in! 🎶 | The KJ added you — {song}. |
| `rejected` | The KJ needs a word | Come to the desk about: {song}. |
| `up_in_2` | You're up in 2! 🎤 | {song} — head back to the venue. |
| `up_next` | You're up NEXT 🎤 | {song} — stand by the mic. |
| `now_singing` | 🎤 You're singing now | {song} — you're up! |

Service worker `push` handler:

```js
self.addEventListener('push', (event) => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      tag: data.tag,       // replaces prior notification with same tag
      icon: data.icon,
      badge: data.badge,
      data: data.data,
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const url = `/sing/?t=${self.SING_TOKEN}&r=${data.request_id || ''}`;
  event.waitUntil(
    clients.matchAll({ type: 'window' }).then((wins) => {
      for (const w of wins) {
        if (w.url.includes('/sing/')) {
          w.focus();
          w.postMessage({ type: 'push-focus', data });
          return;
        }
      }
      return clients.openWindow(url);
    })
  );
});
```

(The `SING_TOKEN` constant is injected into the SW at registration time via query string: the client registers `/sing/sw.js?t=TOKEN` so the SW can read `self.location.search`.)

#### Debouncing

`_after_mutation()` can fire in rapid bursts when the KJ reorders entries. `PushDispatcher` uses a simple `threading.Timer(0.5, self._do_dispatch)` — if another `notify_rotation_changed()` arrives before the timer fires, reset it. Coalesces bursts into one dispatch.

### 3c. Rules page

Route: `@sing_bp.route("/rules")` — **no token gate** (bookmarkable, shareable, not event-scoped). Renders `sing_rules.html`.

Template: ports the numbered-rule structure from `desktop/rotation_rules_printable.html`, re-themed to match `sing.css` (dark background, Nomad purple accents, mobile-first).

Copy (same 5 rules):

1. First come, first sing
2. New singers get priority
3. Multiple songs welcome
4. Need to leave early?
5. Paid priority ♥ (once sub-project #3 ships; stays phrased as a forward-promise for now)

**Inline short version on the confirmation page:** below the "what's playing now" widget, a `<details>` titled **🎤 House rules** that expands to the 5 short lines from `rotation_rules.txt`, plus a `Read full rules →` link pointing at `/sing/rules`.

**Landing page footer:** small `Rules` link alongside existing branding.

### 3d. "What's playing now" widget

New endpoint `GET /sing/now` — token-gated, lightweight (no request ID required; used on the landing page before a singer has submitted):

```json
{
  "now_singing": {"first_name": "Sarah", "song_artist": "Dancing Queen — ABBA"},
  "up_next":    {"first_name": "Mike",  "song_artist": "Hotel California — Eagles"},
  "queued_count": 12
}
```

All fields nullable. First-name-only; we don't leak full names.

`GET /sing/status/<id>` folds the same fields into its response as a `now_playing` sub-object (avoids a second HTTP round-trip per poll for singers who already have a request).

Rendering:

- **Landing page** — top card above "Request a song" CTA. States:
  - Singing + up_next → `🎤 Now: Sarah — Dancing Queen · Up next: Mike`
  - Singing only → `🎤 Now: Sarah — Dancing Queen`
  - Up next only → `Between singers · Up next: Mike`
  - Empty → `Rotation hasn't started yet — you could be the first!`
- **Confirmation page** — smaller card at the top, same copy rules.

Landing page polls `/sing/now` every 15s (mirrors existing `/sing/status` cadence). Confirmation page gets the data from `/sing/status` — no second poll needed.

---

## Section 4 — Data flow

### A. Singer subscribes to push (Android)

```
singer phone              kj-controller              push service (FCM)
------------              -------------              ------------------
GET /sing/?t=TOK   ───▶   render sing.html + /sing/manifest.json
                  ◀───    200 page + manifest + sw.js ref
register sw.js
[submit song]
POST /sing/submit  ───▶   SingStore.create_request()
                  ◀───    200 {request_id}
render confirm
[2s delay, tap 🔔]
Notification.requestPermission() → "granted"
sw.pushManager.subscribe(vapid_public_key)  ───▶  FCM issues endpoint + keys
                                            ◀───  subscription object
POST /sing/push/subscribe ───▶ INSERT OR REPLACE sing_push_subscriptions
                  ◀───    204
```

### B. Rotation mutation → push fires

```
KJ UI                    rotation.py                   push_dispatcher.py        FCM         singer device
-----                    -----------                   ------------------        ---         -------------
POST /rotation/...  ───▶ RotationManager.move_entry()
                         ├ store.move_entry()
                         └ _after_mutation()
                             ├ _write_display_cache()
                             └ push_dispatcher.notify_rotation_changed()
                                 ├ debounce 500ms
                                 ├ fetch active subs for current token
                                 ├ for each sub:
                                 │   next_entry_for_phone(entries, sub.phone)
                                 │   decide_ladder_step() → "up_in_2"
                                 │   dedup vs. last_sent_state
                                 │   executor.submit(send_push, ...)
                                 ↓
                                     pywebpush.webpush(...)  ───▶  FCM  ───▶  sw.js 'push'
                                     on success: UPDATE last_sent_state                 ↓
                                                                            showNotification()
```

### C. Singer taps push notification

```
sw.js 'notificationclick':
  notification.close()
  clients.matchAll({type: 'window'})
    if any URL contains /sing/ → focus + postMessage({type: 'push-focus', data})
    else → clients.openWindow('/sing/?t=TOK&r=<id>')
  page bootstraps, INITIAL_REQUEST_ID triggers existing ?r= path → "done" step + polling starts
```

### D. Wait-estimate recompute on each status poll

```
singer page (every 15s)     kj-controller
-----------                  --------------
GET /sing/status/<id>  ───▶ SingStore.get_request(id)
                             ↓
                            RotationManager.get_rotation()
                             ↓
                            compute_estimate(entries, linked_entry_id, cfg)
                             ↓
                            build response { request, estimate, now_playing, queue }
                       ◀───  200
render update
```

### E. Offline banner

```
on page load:
  window.addEventListener('online',  () => hideOfflineBanner())
  window.addEventListener('offline', () => showOfflineBanner(...))

on every status poll failure:
  consecutiveFailures++
  if consecutiveFailures >= 2 (~30s since last success):
    showOfflineBanner(
      "No internet — keep this page open and we'll update when we're back.")

on any successful poll:
  consecutiveFailures = 0
  hideOfflineBanner()
```

### F. Event-token rotation housekeeping

When `SingStore.rotate_token(new_token)` is called, also execute:

```sql
DELETE FROM sing_push_subscriptions
 WHERE token != :new_token
   AND created_at < datetime('now', '-7 days', 'localtime');
```

---

## Section 5 — Error handling & failure modes

### Push send failures

Handled in `send_push()` — see pseudo-code in Section 3b. Summary:

| HTTP status | Action |
|---|---|
| 201 / 204 | Update `last_sent_state`. |
| 400 / 401 / 403 / 413 | Log ERROR; don't retry; don't disable. (Our bug, not the subscription's fault.) |
| 404 / 410 | Set `disabled_at`. Endpoint is gone. |
| 429 | Log WARN; skip this tick. Retries naturally on next mutation. |
| 5xx / network error | Log WARN; skip. Retries on next mutation. |

Send attempts run on a 2-worker `ThreadPoolExecutor` owned by `PushDispatcher` so a slow push service never blocks `_after_mutation()` (which is on the critical path for KJ UI responsiveness).

### VAPID key generation failure at boot

- `pywebpush` import failure → log ERROR; run without push; singers see "notifications blocked or unsupported" hint.
- `config.json` not writable → log ERROR; fall back to in-memory keys; subscriptions invalidate on next restart.

### Service worker registration failures

- `navigator.serviceWorker.register()` wrapped in try/catch. Failure → no push, polling fallback works. Offline banner logic is independent (uses `navigator.onLine` + fetch errors, not SW).
- Stale SW after deploy: `sw.js` carries a version comment (`// sw-v<kj-controller-version>`); combined with `self.skipWaiting()` + `clients.claim()` in the lifecycle events, updates activate on next page load.

### Subscription endpoint rot

- Push services expire endpoints periodically (Chrome ~48h of inactivity; Safari variable). `disabled_at` catches these via `410`.
- Client auto-resubscribes on page load: if `sw.pushManager.getSubscription()` returns null, we re-subscribe and re-POST. `UNIQUE(token, endpoint)` + `INSERT OR REPLACE ... updated_at=now()` handles this.

### Wait-estimate pathological cases

- Zero active entries ahead → `expected_s = 0`, `position = 1`, display falls into "you're next".
- Target entry deleted by KJ → `/sing/status` returns `position: null`; existing "waiting for KJ" copy covers it.
- Linked file duration is 0 or negative → treated as null; baseline used.
- No `linked_entry_id` on request (still pending review) → no estimate returned; "Waiting for KJ to approve…" copy as today.

### Dedup-race edge cases

- Two rotation mutations within the 500ms debounce window → coalesced to one dispatch.
- Ladder step oscillates (pos 3 → 2 → 3) — the `up_next` fires, but we don't re-fire `up_in_2` after because that's still the last-sent step. Intentional: don't spam the singer while they're near the front.
- Target entry changes (previous Done'd) → `last_sent_state.entry_id` differs → ladder resets cleanly for the new target.

### Offline false positives

`navigator.onLine === true` is unreliable on captive-portal networks. That's why the banner logic uses consecutive poll failures as a second signal (flow E).

### Security

- `POST /sing/push/subscribe` is token-gated (same `require_token` decorator as other sing routes).
- VAPID private key lives in `config.json`, which is `.gitignore`d (verified: `kj-controller/config.json` is listed in `.gitignore`).
- Payloads are Web-Push-encrypted end-to-end at the `pywebpush` layer. Payload contents are non-sensitive (first name, song title, position number) but encryption is still performed.
- Subscription endpoints are not surfaced back to clients — write-only for the singer, read-only for the dispatcher.
- Event-token rotation orphans subscriptions (dispatcher filters on current token), so even if a subscription leaks, it stops working at the next event.

---

## Section 6 — Testing strategy

### Unit tests

| File | Covers |
|---|---|
| `test_wait_estimate.py` | `compute_estimate()` — fallback baseline, tonight's-mean baseline, variance spread, min-spread clamp, transition buffer scaling with position, edge cases (0 entries, missing durations, negative durations, target not in list, target is "Now Singing"). |
| `test_push_dispatcher.py` | `PushDispatcher` — `decide_ladder_step()` for all positions, dedup via `last_sent_state`, ladder reset on entry change, debounce coalescing, disabled-sub filtering, event-token scoping, approve-and-reject decision paths, VAPID key bootstrap (`pywebpush` mocked). |
| `test_push_subscriptions.py` | SQLite CRUD — insert/upsert on `UNIQUE(token, endpoint)`, `disabled_at` flag, housekeeping DELETE. |
| `test_sing_routes_push.py` | HTTP layer — `/sing/push/subscribe` token gate + payload validation + persistence; `/sing/push/unsubscribe`; `/sing/manifest.json` renders with current token in `start_url`; `/sing/rules` renders unauthenticated; `/sing/now` gated and returns expected shape. |
| `test_rotation_push_hook.py` | Integration — `RotationManager._after_mutation()` calls `push_dispatcher.notify_rotation_changed()` when wired; no-op when attribute is None (back-compat). |

### End-to-end test

`test_sing_push_e2e.py` — Flask test client:

1. Seed event token + auto-approve off.
2. `POST /sing/submit` → request_id.
3. `POST /sing/push/subscribe` with stubbed subscription blob.
4. Patch `pywebpush.webpush` to a `MagicMock`.
5. Approve the request → assert mock called with `"approved"` payload.
6. Walk the rotation: add fillers, then `move_entry` the target into position 3 → assert `"up_in_2"`; position 2 → `"up_next"`; `Now Singing` → `"now_singing"`. Assert no duplicate calls for the same (entry_id, step).
7. Submit a second song from same phone → mark first Done → assert ladder resets cleanly for the second entry.

### Mocking

- Patch `pywebpush.webpush` at module level in every test that touches dispatch. Don't hit real push services in CI.
- `tests/manual/test_real_push.py` (excluded from CI via `pytest.ini` marker) for one-off sanity checks during development against a real phone.

### Untested automatically (documented in `docs/TESTING.md`)

- Service worker behaviour in a real browser (no Playwright in this project; adding it for one feature is overkill).
- Real iOS Add-to-Home-Screen → standalone detection → push delivery — requires a physical iPhone.
- Cross-browser `beforeinstallprompt` handling.

### Manual test runbook (`docs/TESTING.md`)

1. Android Chrome: open `https://sing.nomadkaraoke.com/sing/?t=...`, submit a song, accept push prompt, verify subscription in SQLite. Use KJ UI to move the entry through positions; confirm pushes arrive.
2. Desktop Chrome: same (easier debug).
3. iPhone Safari: verify iOS instructional card shows; install as PWA; reopen from home screen; verify push fires.
4. Airplane-mode toggle mid-session → offline banner shows; toggle back → disappears.
5. Two phones, same first name → each gets only their own pushes (phone-scoped dedup).
6. Single phone, two songs → first Done'd → ladder resets cleanly for second.

### Coverage target

70%+ on new Python modules (`push_dispatcher.py`, `compute_estimate`). Matches project target.

### Performance budget

- Push dispatch runs on a 2-worker thread pool owned by `PushDispatcher`.
- Realistic event size: ≤50 active subscriptions per token.
- Worst-case burst: every sub gets a push on a single mutation (full rotation rewrite).
- At ~100ms/call to FCM: 50 sends / 2 workers × 100ms = ~2.5s drain.
- Acceptable because it's off the KJ-UI critical path (the `_after_mutation()` hook fires the dispatch asynchronously).
- Document this in `push_dispatcher.py` header.

---

## Open implementation notes

(Not decisions — just things the implementation plan needs to spell out.)

- **Icon assets.** Need a 192×192 `icon-192.png` and a 72×72 monochrome `badge-72.png` in `static-sing/`. Can port existing Nomad logo; ensure transparency for iOS home-screen look.
- **`sw.js` token handling.** Register SW as `/sing/sw.js?t=TOKEN` so `self.location.search` gives the SW the current token for constructing `notificationclick` URLs. Alternative: `postMessage` the token from page to SW on register — cleaner but more plumbing. Lean: query-string is adequate.
- **PWA `start_url`.** Must include `?t=TOKEN` for installed-icon launches to land on a valid gated page. `/sing/manifest.json` is dynamic (token substituted server-side) to make this work.
- **Transition from polling-only to push-aware status display.** Keep both code paths active; push updates don't replace polling, they're ambient overlays. No client-side dedup needed between push-received and poll-received — they don't update the same DOM.
- **`rotate_token()` needs adding** to `SingStore` if it doesn't already exist. (Current code appears to set tokens via admin routes; verify during implementation. Housekeeping hook can live on the route handler instead if no single store method exists.)
- **`reject_sing_request` helper.** The dispatch path for rejection assumes a helper exists. If rejection is currently inline in a route, add a small helper and call `PushDispatcher.notify_request_decision()` from there.

---

## Success criteria

Shipping is complete when:

1. All unit + e2e tests pass, coverage ≥70% on new code.
2. Manual test runbook items 1–6 all green on real devices.
3. The wait-estimate range on the confirmation page reflects `estimate.range_low_s`/`range_high_s` and the legacy `±20%` code is removed.
4. A singer on Android Chrome who opts in receives `up_in_2`, `up_next`, `now_singing` pushes in the right order for their next-closest song, and the ladder resets cleanly when that song's entry is Done'd.
5. A singer on iOS Safari without PWA install sees the instructional card and can still use the page via polling.
6. A singer without internet sees the offline banner within ~30s and the page doesn't throw errors.
7. `/sing/rules` renders correctly with the five existing rules, unauthenticated.
8. The "what's playing now" widget shows on both landing and confirmation pages and handles empty-rotation states.
