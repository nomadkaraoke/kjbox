# Singer Full-Rotation View — Design

**Date:** 2026-04-27
**Worktree:** `kjbox-singer-rotation-view`
**Branch:** `feat/sess-20260423-2038-singer-rotation-view`

---

## Problem

The singer-facing SPA at `sing.nomadkaraoke.com/?t=<token>` shows a "what's playing now" widget on the landing page (now singing + up next + queued count) and a full upcoming-singers list on the post-submit "done" screen. There is no way for a visitor to see the **full rotation** before deciding to submit, or to glance at it again later without re-finding their submission.

A new singer scanning the QR code wants to answer one question fast: *"How long is the line?"* The now/next pair plus a count is not enough. They need to see the whole list with rough wait times, with copy that makes clear the order can shift (new-singer priority, paid priority, song lengths vary).

## Non-goals

- Live polling on the landing page — the existing 15s `/sing/now` poll is enough; deeper visibility is the done-screen flow's job.
- Migrating the done-screen `upcoming` block to the new endpoint — current bundled-in-`/sing/status` shape works.
- Highlighting the current singer's own row on the landing-page expander — they don't have a request id on that screen.
- Surfacing paid-priority hearts (♥) — separate concern, the rotation row would need a backend field that `_public_queue_view` does not currently expose.
- Standalone rotation page or modal — inline `<details>` matches the established pattern.

---

## Section 1 — Component overview

**New files:**

- `kj-controller/tests/integration/test_sing_rotation_route.py` — integration tests for the new route.

**Modified files:**

- `kj-controller/wait_estimate.py` — add `compute_all_estimates(entries, cfg)` helper.
- `kj-controller/sing.py` — add `GET /sing/rotation` route; reuse `_build_now_playing` for the active list.
- `kj-controller/static-sing/sing.js` — add inline `<details>` expander to `renderLanding()`; lazy fetch `/sing/rotation` on expand; render rows + caveat copy.
- `kj-controller/static-sing/sing.css` — styles for the expander rows (position, name, song, estimate columns; mobile-friendly truncation; max-height + scroll on long rotations).
- `kj-controller/tests/unit/test_wait_estimate.py` — unit tests for `compute_all_estimates`.

**No changes:**

- `_public_queue_view` and the done-screen "upcoming" block stay as-is.
- No new config keys.
- No service-worker, VAPID, or push-subscription work.

---

## Section 2 — Backend

### 2.1 New helper: `compute_all_estimates(entries, cfg)`

Single-pass cumulative computation. Reuses `_baseline()` and `_sanitise()` from the existing module unchanged.

**Signature:**

```python
def compute_all_estimates(entries, cfg):
    """Return a list of estimate dicts, one per active entry, in rotation order.

    Active = entries whose status is not 'done' or 'left' (matches the
    filter used elsewhere in sing.py and wait_estimate.compute_estimate).

    Each estimate dict has the same shape as compute_estimate(), with
    `position` 1-based and `expected_s` / `range_low_s` / `range_high_s`
    computed cumulatively from the start of the active list.

    `spread_source` is the same value (`"tonight"` or `"fallback"`) for
    every entry — it's a property of tonight's variance, not per-row.
    Returned alongside as a sibling, not embedded per-row.
    """
```

**Returns** `(estimates: list[dict], spread_source: str)`.

**Per-entry shape** (matches `compute_estimate` for parity):

```python
{
  "position": int,       # 1-based
  "expected_s": int,     # cumulative sum of ahead_durations + transitions
  "range_low_s": int,    # max(0, expected - spread)
  "range_high_s": int,   # expected + spread
  "close_to_front": bool,   # position <= 2
  "now_singing": bool,      # entry.status == "Now Singing"
}
```

**Algorithm:**

```
baseline, stdev_s, spread_source = _baseline(entries, cfg)
spread = max(cfg["sing_estimate_min_spread_s"], int(stdev_s) if stdev_s else 180)
transition = cfg["sing_estimate_transition_s"]

active = [e for e in entries if (e.get("status") or "").lower() not in ("done", "left")]

ahead_total = 0  # seconds of singers ahead of position i
estimates = []
for i, e in enumerate(active):
    expected = ahead_total  # current entry waits behind all earlier active entries
    estimates.append({
        "position": i + 1,
        "expected_s": expected,
        "range_low_s": max(0, expected - spread),
        "range_high_s": expected + spread,
        "close_to_front": (i + 1) <= 2,
        "now_singing": (e.get("status") or "").lower() == "now singing",
    })
    # Add this entry's contribution to the running total for everyone behind.
    ahead_total += _sanitise(e.get("duration"), baseline) + transition
return estimates, spread_source
```

**Parity invariant:** for any active entry `e` at index `i`, `compute_all_estimates(...)[i]` produces the same `expected_s`, `range_low_s`, `range_high_s` as `compute_estimate(entries, e["id"], cfg)`. The unit test asserts this for every entry in a sample rotation.

### 2.2 New route: `GET /sing/rotation`

Token-gated, mirrors `/sing/now`'s decorator stack.

```python
@sing_bp.route("/rotation", methods=["GET"])
@require_token
def rotation():
    rotation = getattr(current_app, "rotation", None)
    if rotation is None:
        return jsonify({"entries": [], "spread_source": "fallback"})
    entries, active, _np = _build_now_playing(rotation)
    estimates, spread_source = compute_all_estimates(entries, current_app.kj_config)
    out = []
    for entry, est in zip(active, estimates):
        singer = entry.get("singer") or ""
        out.append({
            "position": est["position"],
            "first_name": singer.split()[0] if singer else "",
            "song_artist": entry.get("song_artist") or "",
            "status": entry.get("status") or "",
            "now_singing": est["now_singing"],
            "expected_s": est["expected_s"],
            "range_low_s": est["range_low_s"],
            "range_high_s": est["range_high_s"],
        })
    return jsonify({"entries": out, "spread_source": spread_source})
```

Responses:

- 200 with `{entries: [...], spread_source: "tonight"|"fallback"}` on success.
- 200 with `{entries: [], spread_source: "fallback"}` when there's no rotation (handled by the empty-state copy on the frontend).
- 403 when the token is invalid/closed (handled by the `require_token` decorator's existing JSON-aware response).

No rate-limiting — same trust posture as `/sing/now`.

---

## Section 3 — Frontend

### 3.1 Placement

The expander is appended to `renderLanding()`'s output, immediately after `renderNowPlaying()` and before the "Request a song" heading. Visual flow:

```
[ now playing widget — 🎤 Now / Up next ]
[ ▸ See full rotation (12 singers)      ]   ← the new expander, collapsed
   Request a song
   [Get started]
```

It does **not** appear on the identity, search, confirm, or done screens. The done screen already has its own `upcoming` expander; the others are mid-flow steps where adding it would be visual clutter and the user can hit "Back" to reach the landing page.

### 3.2 Markup

Inline `<details>` matching the established pattern:

```html
<details class="rotation-expander">
  <summary>See full rotation</summary>
  <div class="rotation-body">
    <p class="rotation-caveat">
      <em>Order can change — new singers get bumped up, paid spots jump
      ahead, and times are rough. Treat this as a guide.</em>
    </p>
    <ol class="rotation-list">
      <li class="rotation-row">…</li>
      …
    </ol>
    <p class="rotation-updated">updated just now</p>
  </div>
</details>
```

### 3.3 Row format

```
#1  🎤 Andrew      Sweet Caroline                — on now
#2     Bob         Bohemian Rhapsody             — up next
#3     Jenny       Don't Stop Believin'          — ~3–7 min
#4     Mike        Wonderwall                    — ~6–11 min
#5     Sarah       Africa                        — ~10–16 min
```

Wait-text rules (apply in order, first match wins):

- `entry.now_singing === true` → `"on now"` (and prepend 🎤 to the first-name cell)
- `position === 1` and not now-singing → `"up next"` (no-one currently on stage; this entry is first in line)
- `position === 2` and the rotation contains a now-singing entry at position 1 → `"up next"` (their wait is roughly the rest of the current song)
- otherwise → `~${Math.round(range_low_s/60)}–${Math.round(range_high_s/60)} min` (mirrors `pollStatus()`)

Note: the "position 2 = up next" branch only fires when there's actually a singer on stage. If position 1 is queued (no one on stage), position 2 falls through to the time-range branch — its expected wait is approximately one song length, which the time range already captures.

Long-name / long-song titles truncate with CSS `text-overflow: ellipsis` on the name and song columns. The estimate column is fixed-width-right.

### 3.4 Fetch / refresh behaviour

- The `<details>` element is rendered collapsed on first paint.
- A `toggle` event listener on the element fetches `/sing/rotation` only when expanding. On collapse, no work.
- Data is cached on `state.rotationCache = {fetchedAt, payload}` (state-level so the cache survives a back-from-search re-render of the landing view). On second expand, if `Date.now() - fetchedAt < 30000`, render the cached payload immediately. Otherwise refetch.
- The `<summary>` text updates from "See full rotation" to "See full rotation (12 singers)" once the payload arrives.
- "updated just now" / "updated 2 min ago" timestamp under the list, computed at render time from `fetchedAt`.

No live polling. Rationale captured in the non-goals.

### 3.5 Empty / error / closed states

- **Empty rotation** (`entries.length === 0`) → body shows *"Rotation hasn't started yet — you could be the first!"* (verbatim reuse of the now-playing empty-state line).
- **Fetch failure / network error** → body shows *"Couldn't load rotation — close and tap again to retry."* (collapsing the `<details>` clears the cache failure flag; re-expanding fires the `toggle` listener and refetches.)
- **403 (token revoked)** → body shows *"Requests just closed — ask the KJ."* The page itself will reload to the closed/code-entry state on the next `/sing/now` poll cycle, so this is a brief transitional state.

---

## Section 4 — Testing

### 4.1 Unit — `tests/unit/test_wait_estimate.py`

Add tests for `compute_all_estimates`:

1. **Parity with single-target** — for a 5-entry active rotation, assert `compute_all_estimates(entries, cfg)[i]` produces the same `expected_s`, `range_low_s`, `range_high_s` as `compute_estimate(entries, entries[i]["id"], cfg)` for every `i`.
2. **Cumulative sum** — given known durations `[180, 240, 200]` and `transition=30`, assert `expected_s` for positions 1/2/3 equals `0`, `210`, `480`.
3. **Done/left filtering** — a rotation with mixed statuses returns estimates only for active entries, with `position` 1-based over the filtered list.
4. **Spread source: tonight** — given ≥3 done entries with varying durations, `spread_source === "tonight"` and the spread reflects `pstdev` (clamped to `min_spread_s` floor).
5. **Spread source: fallback** — given <3 done entries, `spread_source === "fallback"` and spread is `max(min_spread_s, 180)`.
6. **Now-singing flag** — entry with status `"Now Singing"` (any case) gets `now_singing: true`; others `false`.

### 4.2 Integration — `tests/integration/test_sing_rotation_route.py`

1. **200 with token** — given a stocked `app.rotation`, returns the expected JSON shape; entries list length matches active rotation; first row has `position: 1`.
2. **403 without token** — returns 403 JSON `{error: "not_open"}`.
3. **403 with stale token** — submitting a token that no longer matches the stored event token returns 403.
4. **403 when sing not enabled** — `store.is_enabled() === false` → 403.
5. **Empty rotation** — `app.rotation` returns no entries → 200 with `{entries: [], spread_source: "fallback"}`.
6. **Done/left filtered** — rotation contains done + left + queued entries; only the queued ones appear in `entries`, positions are 1-based over the filtered list.
7. **First-name only** — entry with singer `"Jane Smith"` returns `first_name: "Jane"` (no last name).
8. **Spread source surfaces** — when `_baseline` returns "tonight", the response carries `spread_source: "tonight"`.

### 4.3 Frontend smoke (manual)

Vanilla JS, no test runner in this repo. Manual verification on dev:

- Expand on landing → list renders with correct positions, names, songs, estimates.
- Caveat copy visible at top of expanded body.
- Long rotation (≥20 entries) scrolls within the `<details>` body without pushing the CTA off-screen.
- 30s cache: collapse + re-expand within 30s shows cached body instantly; re-expand after 30s refetches.
- Network failure: kill backend mid-expand → error copy renders.
- Token revoked mid-session: clear token via admin UI → 403 path renders the closed copy until the parent page reloads.

---

## Open questions

None at design time. Implementation may surface CSS-tuning questions on narrow viewports (iPhone SE width 320px) — handled inline.
