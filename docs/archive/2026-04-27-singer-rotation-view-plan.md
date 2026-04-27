# Singer Full-Rotation View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an inline `<details>` expander to the singer-facing landing page that shows the full active rotation (positions, first names, songs, cumulative wait estimates) plus a caveat that the order can change.

**Architecture:** New backend endpoint `GET /sing/rotation` reuses `compute_estimate`'s baseline + spread math via a new `compute_all_estimates(entries, cfg)` helper that computes cumulative wait times in one pass. Frontend renders a collapsed `<details>` element on the landing screen; lazy-fetches on first expand and caches for 30s on `state.rotationCache`.

**Tech Stack:** Python 3 / Flask (backend), vanilla JS / CSS (frontend), pytest (tests).

**Spec:** `docs/archive/2026-04-27-singer-rotation-view-design.md`

---

## Task 1: Add `compute_all_estimates` helper to `wait_estimate.py`

**Files:**
- Modify: `kj-controller/wait_estimate.py` (append new function)
- Test: `kj-controller/tests/unit/test_wait_estimate.py` (append new test class)

- [ ] **Step 1.1: Write the failing tests**

Append to `kj-controller/tests/unit/test_wait_estimate.py`:

```python
from wait_estimate import compute_all_estimates


class TestComputeAllEstimates:
    def test_empty_entries(self):
        estimates, spread_source = compute_all_estimates([], DEFAULT_CFG)
        assert estimates == []
        assert spread_source == "fallback"

    def test_done_and_left_excluded(self):
        entries = [
            _entry(1, status="Done", duration=200),
            _entry(2, status="Left"),
            _entry(3),
            _entry(4),
        ]
        estimates, _ = compute_all_estimates(entries, DEFAULT_CFG)
        assert [e["position"] for e in estimates] == [1, 2]

    def test_cumulative_sum_with_known_durations(self):
        entries = [
            _entry(1, duration=180),
            _entry(2, duration=240),
            _entry(3, duration=200),
        ]
        estimates, _ = compute_all_estimates(entries, DEFAULT_CFG)
        # transition_s=30
        assert estimates[0]["expected_s"] == 0
        assert estimates[1]["expected_s"] == 180 + 30
        assert estimates[2]["expected_s"] == 180 + 240 + 30 + 30

    def test_parity_with_compute_estimate(self):
        # For every active entry, compute_all_estimates[i] must produce the
        # same expected_s / range_low_s / range_high_s as
        # compute_estimate(entries, target_id, cfg).
        entries = [
            _entry(10, status="done", duration=180),
            _entry(11, status="done", duration=240),
            _entry(12, status="done", duration=300),
            _entry(1, duration=210),
            _entry(2, duration=180),
            _entry(3),
            _entry(4, duration=300),
        ]
        all_ests, _ = compute_all_estimates(entries, DEFAULT_CFG)
        active_ids = [1, 2, 3, 4]
        for i, target_id in enumerate(active_ids):
            single = compute_estimate(entries, target_id, DEFAULT_CFG)
            assert all_ests[i]["expected_s"] == single["expected_s"]
            assert all_ests[i]["range_low_s"] == single["range_low_s"]
            assert all_ests[i]["range_high_s"] == single["range_high_s"]
            assert all_ests[i]["position"] == single["position"]

    def test_spread_source_tonight_with_three_done(self):
        entries = [
            _entry(10, status="done", duration=180),
            _entry(11, status="done", duration=240),
            _entry(12, status="done", duration=300),
            _entry(1),
        ]
        _, spread_source = compute_all_estimates(entries, DEFAULT_CFG)
        assert spread_source == "tonight"

    def test_spread_source_fallback_with_two_done(self):
        entries = [
            _entry(10, status="done", duration=180),
            _entry(11, status="done", duration=240),
            _entry(1),
        ]
        _, spread_source = compute_all_estimates(entries, DEFAULT_CFG)
        assert spread_source == "fallback"

    def test_now_singing_flag_case_insensitive(self):
        entries = [
            _entry(1, status="now singing"),
            _entry(2, status="Now Singing"),
            _entry(3),
        ]
        estimates, _ = compute_all_estimates(entries, DEFAULT_CFG)
        assert estimates[0]["now_singing"] is True
        assert estimates[1]["now_singing"] is True
        assert estimates[2]["now_singing"] is False

    def test_close_to_front_flags(self):
        entries = [_entry(1), _entry(2), _entry(3), _entry(4)]
        estimates, _ = compute_all_estimates(entries, DEFAULT_CFG)
        assert estimates[0]["close_to_front"] is True
        assert estimates[1]["close_to_front"] is True
        assert estimates[2]["close_to_front"] is False
        assert estimates[3]["close_to_front"] is False

    def test_range_low_clamped_to_zero(self):
        # Position 1 has expected_s = 0; range_low_s must be max(0, ...).
        entries = [_entry(1)]
        estimates, _ = compute_all_estimates(entries, DEFAULT_CFG)
        assert estimates[0]["range_low_s"] == 0

    def test_negative_or_zero_durations_use_baseline(self):
        # Entry 1 has duration=0 → ahead total uses baseline (240) for entry 2.
        entries = [_entry(1, duration=0), _entry(2)]
        estimates, _ = compute_all_estimates(entries, DEFAULT_CFG)
        assert estimates[1]["expected_s"] == 240 + 30
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/unit/test_wait_estimate.py::TestComputeAllEstimates -v`
Expected: FAIL with `ImportError: cannot import name 'compute_all_estimates'`

- [ ] **Step 1.3: Implement `compute_all_estimates`**

Append to `kj-controller/wait_estimate.py` (after `_sanitise`):

```python
def compute_all_estimates(entries, cfg):
    """Return (estimates, spread_source) for every active entry in `entries`.

    Active = entries whose status is not 'done' or 'left' (matches the
    filter used by ``compute_estimate``). Estimates are computed cumulatively
    in one pass: each row's ``expected_s`` is the sum of every preceding
    active entry's (sanitised) duration plus a per-transition buffer.

    For any active entry at index ``i`` in the returned list,
    ``compute_all_estimates(entries, cfg)[0][i]`` produces the same
    ``expected_s`` / ``range_low_s`` / ``range_high_s`` as
    ``compute_estimate(entries, entries_active[i]["id"], cfg)``. This
    parity is asserted by the unit tests.

    ``spread_source`` is a property of tonight's variance and is the same
    for every entry; it is returned as a sibling rather than embedded
    per-row.
    """
    baseline, stdev_s, spread_source = _baseline(entries, cfg)
    raw_spread = int(stdev_s) if stdev_s is not None else 180
    spread = max(cfg["sing_estimate_min_spread_s"], raw_spread)
    transition = cfg["sing_estimate_transition_s"]

    active = [
        e for e in entries
        if (e.get("status") or "").lower() not in ("done", "left")
    ]

    estimates = []
    ahead_total = 0  # cumulative seconds of all active entries before this row
    for i, entry in enumerate(active):
        expected = ahead_total
        estimates.append({
            "position": i + 1,
            "expected_s": int(expected),
            "range_low_s": max(0, int(expected) - spread),
            "range_high_s": int(expected) + spread,
            "close_to_front": (i + 1) <= 2,
            "now_singing": (entry.get("status") or "").lower() == "now singing",
        })
        ahead_total += _sanitise(entry.get("duration"), baseline) + transition

    return estimates, spread_source
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/unit/test_wait_estimate.py -v`
Expected: All `TestComputeAllEstimates::*` PASS plus the existing `TestComputeEstimate::*` still PASS.

- [ ] **Step 1.5: Commit**

```bash
git add kj-controller/wait_estimate.py kj-controller/tests/unit/test_wait_estimate.py
git commit -m "feat(sing): add compute_all_estimates helper for full-rotation view"
```

---

## Task 2: Add `GET /sing/rotation` route to `sing.py`

**Files:**
- Modify: `kj-controller/sing.py` (add new route + import)
- Test: `kj-controller/tests/integration/test_sing_rotation_route.py` (new file)

- [ ] **Step 2.1: Write the failing tests**

Create `kj-controller/tests/integration/test_sing_rotation_route.py`:

```python
"""Integration tests for GET /sing/rotation."""

import pytest


def _enable_requests(sing_app):
    sing_app.sing_store.set_enabled(True)


class TestSingRotationRoute:
    def test_requires_token(self, client):
        resp = client.get("/sing/rotation")
        assert resp.status_code == 403

    def test_stale_token_rejected(self, client, sing_app, token):
        _enable_requests(sing_app)
        # Rotate the event token — the previously-handed-out one is now stale.
        sing_app.sing_store.rotate_token()
        resp = client.get(f"/sing/rotation?t={token}")
        assert resp.status_code == 403

    def test_disabled_when_sing_not_enabled(self, client, sing_app, token):
        # Default state: sing_store.is_enabled() is False until set_enabled(True).
        resp = client.get(f"/sing/rotation?t={token}")
        assert resp.status_code == 403

    def test_empty_rotation(self, client, sing_app, token):
        _enable_requests(sing_app)
        resp = client.get(f"/sing/rotation?t={token}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["entries"] == []
        assert data["spread_source"] == "fallback"

    def test_active_rotation_response_shape(self, client, sing_app, token):
        _enable_requests(sing_app)
        sing_app.rotation.add_entry("Sarah Smith", song_artist="ABBA — Dancing Queen")
        sing_app.rotation.add_entry("Mike", song_artist="Eagles — Hotel California")
        entries = sing_app.rotation.get_rotation()
        sing_app.rotation.update_status(entries[0]["id"], "Now Singing")

        resp = client.get(f"/sing/rotation?t={token}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["spread_source"] in ("tonight", "fallback")
        assert len(data["entries"]) == 2
        first = data["entries"][0]
        assert first["position"] == 1
        assert first["first_name"] == "Sarah"  # last name dropped
        assert first["song_artist"] == "ABBA — Dancing Queen"
        assert first["now_singing"] is True
        assert "expected_s" in first
        assert "range_low_s" in first
        assert "range_high_s" in first

        second = data["entries"][1]
        assert second["position"] == 2
        assert second["first_name"] == "Mike"
        assert second["now_singing"] is False

    def test_done_and_left_filtered_out(self, client, sing_app, token):
        _enable_requests(sing_app)
        sing_app.rotation.add_entry("Alice", song_artist="Queen — Bohemian Rhapsody")
        sing_app.rotation.add_entry("Bob", song_artist="Pop")
        sing_app.rotation.add_entry("Carol", song_artist="Jazz")
        entries = sing_app.rotation.get_rotation()
        sing_app.rotation.update_status(entries[0]["id"], "Done")
        sing_app.rotation.update_status(entries[1]["id"], "Left")

        resp = client.get(f"/sing/rotation?t={token}")
        data = resp.get_json()
        assert len(data["entries"]) == 1
        assert data["entries"][0]["first_name"] == "Carol"
        assert data["entries"][0]["position"] == 1

    def test_first_name_only(self, client, sing_app, token):
        _enable_requests(sing_app)
        sing_app.rotation.add_entry("Jane Smith Doe", song_artist="Test")
        resp = client.get(f"/sing/rotation?t={token}")
        data = resp.get_json()
        assert data["entries"][0]["first_name"] == "Jane"
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `cd kj-controller && pytest tests/integration/test_sing_rotation_route.py -v`
Expected: All FAIL — initially with 404 (route doesn't exist) or 403 (require_token returns 403 for an unmounted endpoint that the host guard catches). The point is they don't pass yet.

- [ ] **Step 2.3: Add the route**

In `kj-controller/sing.py`, change the `wait_estimate` import from:

```python
from wait_estimate import compute_estimate
```

to:

```python
from wait_estimate import compute_all_estimates, compute_estimate
```

Then append a new route after the `now_playing` function (around line 603, immediately after the existing `/now` route):

```python
@sing_bp.route("/rotation", methods=["GET"])
@require_token
def rotation():
    """Full active rotation with cumulative wait estimates per entry.

    Singer-facing landing page expander. Token-gated like /sing/now.
    Returns first-name + song + status + (expected_s, range_low_s,
    range_high_s) for every active (non-done/non-left) entry.
    """
    rotation_mgr = getattr(current_app, "rotation", None)
    if rotation_mgr is None:
        return jsonify({"entries": [], "spread_source": "fallback"})

    entries, active, _np = _build_now_playing(rotation_mgr)
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

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `cd kj-controller && pytest tests/integration/test_sing_rotation_route.py -v`
Expected: All PASS.

Also run the full sing-routes test set to confirm nothing regressed:

Run: `cd kj-controller && pytest tests/integration/test_sing_now_and_status.py tests/integration/test_sing_public_routes.py -v`
Expected: All PASS.

- [ ] **Step 2.5: Commit**

```bash
git add kj-controller/sing.py kj-controller/tests/integration/test_sing_rotation_route.py
git commit -m "feat(sing): add GET /sing/rotation endpoint with cumulative estimates"
```

---

## Task 3: Frontend — landing-page rotation expander

**Files:**
- Modify: `kj-controller/static-sing/sing.js`

This task is JS-only, no backend changes. The kjbox repo has no JS test suite — the pre-commit hook (`.githooks/pre-commit`) does syntax validation, and manual smoke testing covers behaviour.

- [ ] **Step 3.1: Add `state.rotationCache` field**

In `kj-controller/static-sing/sing.js`, find the `state` object (around line 30) and add `rotationCache: null,` after `status: null,`:

```javascript
const state = {
  step: "landing",
  name: LS.get("sing_name"),
  phone: LS.get("sing_phone"),
  query: "",
  selected: null,
  makeArtist: "",
  makeTitle: "",
  request: null,
  status: null,
  rotationCache: null,   // {fetchedAt: number, payload: object} — survives back-from-search
  // Phase C — updated on every /sing/search response so a KJ flipping the
  // toggle mid-session takes effect on the next keystroke-triggered search.
  makeRequestsEnabled: INITIAL_MAKE_REQUESTS_ENABLED,
};
```

- [ ] **Step 3.2: Add `fetchRotation` and rendering helpers**

Insert a new section just before `function renderLanding()` (around line 219), after the `renderNowError` function:

```javascript
// --- Full rotation expander -----------------------------------------------

const ROTATION_CACHE_TTL_MS = 30000;

async function fetchRotation() {
  const t = encodeURIComponent(TOKEN);
  const resp = await fetch(`${BASE}/rotation?t=${t}`, { credentials: "same-origin" });
  if (!resp.ok) {
    const err = new Error("rotation fetch failed");
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}

function _waitText(entry) {
  // Apply rules in order — first match wins.
  if (entry.now_singing) return "on now";
  if (entry.position === 1) return "up next";
  // Position 2 is "up next" only when there is actually someone on stage at #1.
  // Detected via the cached payload: the caller passes a hasNowSinging flag.
  if (entry._hasNowSinging && entry.position === 2) return "up next";
  const low = Math.round(entry.range_low_s / 60);
  const high = Math.round(entry.range_high_s / 60);
  return `~${low}–${high} min`;
}

function _formatUpdatedAt(fetchedAt) {
  const ageS = Math.round((Date.now() - fetchedAt) / 1000);
  if (ageS < 10) return "updated just now";
  if (ageS < 60) return `updated ${ageS}s ago`;
  const ageM = Math.round(ageS / 60);
  return ageM === 1 ? "updated 1 min ago" : `updated ${ageM} min ago`;
}

function _renderRotationBody(payload) {
  const body = el("div", { class: "rotation-body" });
  body.appendChild(el("p", { class: "rotation-caveat" },
    el("em", {},
      "Order can change — new singers get bumped up, paid spots jump ahead, "
      + "and times are rough. Treat this as a guide."),
  ));

  const entries = payload?.entries || [];
  if (entries.length === 0) {
    body.appendChild(el("p", { class: "rotation-empty" },
      "Rotation hasn't started yet — you could be the first!"));
    return body;
  }

  const hasNowSinging = entries.some((e) => e.now_singing);
  const list = el("ol", { class: "rotation-list" });
  for (const entry of entries) {
    const augmented = { ...entry, _hasNowSinging: hasNowSinging };
    list.appendChild(el("li", { class: "rotation-row" },
      el("span", { class: "rotation-pos" }, `#${entry.position}`),
      el("span", { class: "rotation-name" },
        entry.now_singing ? `🎤 ${entry.first_name || "—"}` : (entry.first_name || "—")),
      el("span", { class: "rotation-song" }, entry.song_artist || ""),
      el("span", { class: "rotation-wait" }, _waitText(augmented)),
    ));
  }
  body.appendChild(list);
  body.appendChild(el("p", { class: "rotation-updated" },
    _formatUpdatedAt(payload._fetchedAt)));
  return body;
}

function _renderRotationLoading() {
  return el("div", { class: "rotation-body" },
    el("p", { class: "rotation-loading" }, "Loading rotation…"));
}

function _renderRotationError(status) {
  const body = el("div", { class: "rotation-body" });
  if (status === 403) {
    body.appendChild(el("p", { class: "rotation-error" },
      "Requests just closed — ask the KJ."));
  } else {
    body.appendChild(el("p", { class: "rotation-error" },
      "Couldn't load rotation — close and tap again to retry."));
  }
  return body;
}

function _updateRotationSummary(detailsEl, count) {
  const summary = detailsEl.querySelector("summary");
  if (!summary) return;
  summary.textContent = count > 0
    ? `See full rotation (${count} ${count === 1 ? "singer" : "singers"})`
    : "See full rotation";
}

function renderRotationExpander() {
  const details = el("details", { class: "rotation-expander" },
    el("summary", {}, "See full rotation"),
    el("div", { class: "rotation-body" }),  // placeholder; populated on toggle
  );

  // If we already have a fresh cache, populate the body up-front so that
  // returning to the landing screen after Back-from-Search renders instantly
  // when the user re-expands.
  if (state.rotationCache && Date.now() - state.rotationCache.fetchedAt < ROTATION_CACHE_TTL_MS) {
    const payload = { ...state.rotationCache.payload, _fetchedAt: state.rotationCache.fetchedAt };
    details.querySelector(".rotation-body").replaceWith(_renderRotationBody(payload));
    _updateRotationSummary(details, payload.entries?.length || 0);
  }

  details.addEventListener("toggle", async () => {
    if (!details.open) return;
    // Serve from cache if fresh.
    if (state.rotationCache && Date.now() - state.rotationCache.fetchedAt < ROTATION_CACHE_TTL_MS) {
      const payload = { ...state.rotationCache.payload, _fetchedAt: state.rotationCache.fetchedAt };
      details.querySelector(".rotation-body").replaceWith(_renderRotationBody(payload));
      _updateRotationSummary(details, payload.entries?.length || 0);
      return;
    }
    // Otherwise refetch.
    details.querySelector(".rotation-body").replaceWith(_renderRotationLoading());
    try {
      const payload = await fetchRotation();
      const fetchedAt = Date.now();
      state.rotationCache = { fetchedAt, payload };
      details.querySelector(".rotation-body").replaceWith(
        _renderRotationBody({ ...payload, _fetchedAt: fetchedAt }),
      );
      _updateRotationSummary(details, payload.entries?.length || 0);
    } catch (e) {
      details.querySelector(".rotation-body").replaceWith(_renderRotationError(e.status));
    }
  });

  return details;
}
```

- [ ] **Step 3.3: Insert the expander into `renderLanding`**

Find `renderLanding` (around line 219) and add `renderRotationExpander()` between `renderNowPlaying()` and the `h1`:

```javascript
function renderLanding() {
  return el("main", { class: "sing-card" },
    renderNowPlaying(),
    renderRotationExpander(),
    el("h1", {}, "Request a song"),
    // ... rest unchanged
```

- [ ] **Step 3.4: Syntax check**

Run: `cd kj-controller && node --check static-sing/sing.js`
Expected: no output (success). The pre-commit hook does this same check.

- [ ] **Step 3.5: Commit**

```bash
git add kj-controller/static-sing/sing.js
git commit -m "feat(sing): show full rotation expander on landing page"
```

---

## Task 4: CSS for the rotation expander

**Files:**
- Modify: `kj-controller/static-sing/sing.css` (append new section)

- [ ] **Step 4.1: Add styles**

Append to `kj-controller/static-sing/sing.css`, after the existing `Now playing widget` section (around line 469):

```css
/* --- Full rotation expander (landing page) -------------------------------- */
.rotation-expander {
  margin: 0 0 1rem 0;
  padding: 0;
  background: var(--nk-card-2);
  border: 1px solid var(--nk-border);
  border-radius: 10px;
}
.rotation-expander > summary {
  padding: 0.75rem 1rem;
  cursor: pointer;
  font-weight: 600;
  color: var(--nk-text);
  list-style: none;
}
.rotation-expander > summary::marker { content: ""; }
.rotation-expander > summary::-webkit-details-marker { display: none; }
.rotation-expander > summary::before {
  content: "▸ ";
  display: inline-block;
  margin-right: 0.25rem;
  transition: transform 0.15s ease;
}
.rotation-expander[open] > summary::before {
  content: "▾ ";
}

.rotation-body {
  padding: 0 1rem 0.75rem;
}
.rotation-caveat {
  margin: 0 0 0.75rem 0;
  font-size: 0.85rem;
  color: var(--nk-text-dim);
}
.rotation-loading,
.rotation-empty,
.rotation-error {
  margin: 0.5rem 0;
  font-size: 0.9rem;
  color: var(--nk-text-dim);
  font-style: italic;
}
.rotation-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 360px;
  overflow-y: auto;
}
.rotation-row {
  display: grid;
  grid-template-columns: 2.4rem 1fr 2fr auto;
  gap: 0.5rem;
  align-items: baseline;
  padding: 0.4rem 0;
  border-bottom: 1px solid var(--nk-border);
  font-size: 0.9rem;
}
.rotation-row:last-child { border-bottom: 0; }
.rotation-pos {
  color: var(--nk-text-dim);
  font-variant-numeric: tabular-nums;
}
.rotation-name {
  font-weight: 600;
  color: var(--nk-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rotation-song {
  color: var(--nk-text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rotation-wait {
  color: var(--nk-accent);
  font-size: 0.85rem;
  white-space: nowrap;
  text-align: right;
}
.rotation-updated {
  margin: 0.5rem 0 0;
  font-size: 0.75rem;
  color: var(--nk-text-dim);
  text-align: right;
}

@media (max-width: 380px) {
  .rotation-row {
    grid-template-columns: 1.8rem 1fr auto;
    grid-template-areas:
      "pos name wait"
      "pos song wait";
    row-gap: 0.1rem;
  }
  .rotation-pos  { grid-area: pos; align-self: center; }
  .rotation-name { grid-area: name; }
  .rotation-song { grid-area: song; font-size: 0.8rem; }
  .rotation-wait { grid-area: wait; align-self: center; }
}
```

- [ ] **Step 4.2: Commit**

```bash
git add kj-controller/static-sing/sing.css
git commit -m "feat(sing): style the landing-page rotation expander"
```

---

## Task 5: Verify nothing regressed

- [ ] **Step 5.1: Run the full test suite**

Run: `cd kj-controller && pytest`
Expected: All tests pass. No new failures vs. baseline.

If tests fail that are unrelated to this work (e.g. flaky network tests), note them and continue — only stop if a failure is clearly caused by these changes.

- [ ] **Step 5.2: JS syntax check**

Run: `cd kj-controller && node --check static-sing/sing.js`
Expected: no output (success).

- [ ] **Step 5.3: Manual smoke (optional, time-permitting)**

Start the dev server locally:

```bash
cd kj-controller && python app.py
```

Then in a browser:
1. Visit `http://localhost:80/sing/?t=<token>` (or whatever port the test config uses).
2. Confirm the "See full rotation" expander appears between the "now playing" widget and the "Request a song" heading.
3. Tap to expand — confirm the caveat copy renders, plus an empty-state line if there's no rotation.
4. Add an entry via the admin UI, refresh, re-expand — confirm the new row shows with the correct position and wait estimate.

If the dev server can't be started locally without significant setup (this is a device-deployed app), skip the manual smoke and rely on the integration tests + post-deploy verification at `/shipit` time.

---

## Self-Review

After implementing all tasks:

1. **Spec coverage** — every section of `2026-04-27-singer-rotation-view-design.md` should map to a task above:
   - §2.1 `compute_all_estimates` → Task 1
   - §2.2 `/sing/rotation` route → Task 2
   - §3.1 placement → Task 3 (Step 3.3)
   - §3.2 markup → Task 3 (Step 3.2 `_renderRotationBody`)
   - §3.3 row format and wait-text rules → Task 3 (`_waitText`)
   - §3.4 fetch / cache behaviour → Task 3 (`renderRotationExpander`'s `toggle` handler + `state.rotationCache`)
   - §3.5 empty / error / closed states → Task 3 (`_renderRotationError`, empty-state branch in `_renderRotationBody`)
   - §4.1 unit tests → Task 1 (Step 1.1)
   - §4.2 integration tests → Task 2 (Step 2.1)
   - §4.3 frontend smoke → Task 5 (Step 5.3)
   - CSS treatment → Task 4

2. **Placeholder scan** — no TBD/TODO; every code step has full source; expected outcomes are explicit.

3. **Type consistency** — names match across tasks: `compute_all_estimates`, `/sing/rotation`, `state.rotationCache`, `renderRotationExpander`, `_renderRotationBody`, `_waitText`, `.rotation-expander`, `.rotation-row` — used consistently.
