# Singer Expectations UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship sub-project #4 — upgraded wait-time estimates, Web Push notifications (PWA), a rules page, and a "what's playing now" widget for the public singer-facing `/sing/*` flow.

**Architecture:** Backend additions are isolated to a new `push_dispatcher.py` module plus schema/route additions in existing `sing_store.py` / `sing.py`. Frontend extends the existing vanilla-JS SPA in `static-sing/sing.js` with a registered service worker at `static-sing/sw.js`. The push dispatcher hooks into `RotationManager._after_mutation()` (the existing mutation hook). Polling remains the universal fallback — push is additive.

**Tech Stack:** Flask + SQLite + `pywebpush` (server), vanilla JS + Web Push API + Service Worker (client), pytest + Playwright (tests).

**Spec:** `docs/archive/2026-04-22-singer-expectations-design.md`

**Working directory:** `/Users/andrew/Projects/nomadkaraoke/kjbox-singer-expectations` (worktree on branch `feat/sess-20260422-1808-singer-expectations`).

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `kj-controller/push_dispatcher.py` | `PushDispatcher` class: VAPID config, subscription CRUD (thin wrapper over SingStore methods), ladder-step decision, dedup, `ThreadPoolExecutor`-backed send loop. |
| `kj-controller/templates/sing_rules.html` | Rules page template (public, no token gate). |
| `kj-controller/static-sing/manifest.json` | PWA manifest — rendered dynamically so `start_url` includes the current token. Not a static asset; route handler returns it. |
| `kj-controller/static-sing/sw.js` | Service worker — handles `push` and `notificationclick` events. Basic shell cache. |
| `kj-controller/static-sing/icon-192.png` | PWA / notification icon (192×192). Port of Nomad logo on transparent bg. |
| `kj-controller/static-sing/icon-512.png` | PWA maskable icon (512×512). |
| `kj-controller/static-sing/badge-72.png` | Notification badge (72×72, monochrome). |
| `kj-controller/tests/unit/test_wait_estimate.py` | Unit tests for `compute_estimate()`. |
| `kj-controller/tests/unit/test_push_dispatcher.py` | Unit tests for `PushDispatcher` — ladder decision, dedup, debounce, webpush mocked. |
| `kj-controller/tests/integration/test_sing_push_routes.py` | Integration tests for `/sing/push/*`, `/sing/manifest.json`, `/sing/rules`, `/sing/now`. |
| `kj-controller/tests/e2e/test_sing_push_e2e.py` | End-to-end test: full subscribe → rotation mutations → assert webpush calls in sequence. |

### Modified files

| Path | Changes |
|---|---|
| `kj-controller/sing_store.py` | Add `sing_push_subscriptions` table schema, CRUD methods, housekeeping query. |
| `kj-controller/sing.py` | New routes: `GET /sing/rules`, `GET /sing/manifest.json`, `GET /sing/sw.js` (token-aware), `GET /sing/now`, `POST /sing/push/subscribe`, `POST /sing/push/unsubscribe`. Update `/sing/status/<id>` response shape (add `estimate`, `now_playing`). |
| `kj-controller/rotation.py` | `_after_mutation()` calls `self.push_dispatcher.notify_rotation_changed()` when `self.push_dispatcher` is set. |
| `kj-controller/app.py` | Initialise `PushDispatcher`, bootstrap VAPID keys, wire onto `app.rotation.push_dispatcher`. |
| `kj-controller/config.py` | Add new defaults: `sing_estimate_transition_s`, `sing_estimate_default_song_s`, `sing_estimate_min_spread_s`. |
| `kj-controller/templates/sing.html` | Add manifest link, iOS meta tags, theme color, touch icon. |
| `kj-controller/static-sing/sing.html` | (Not applicable — sing.html lives in templates/.) |
| `kj-controller/static-sing/sing.js` | Service worker registration; subscription flow (Android/desktop + iOS detection); install-prompt UX; "what's playing now" widget; offline banner; upgraded wait-estimate rendering. |
| `kj-controller/static-sing/sing.css` | Styles for new widget, push prompt, offline banner, rules short-form `<details>`. |
| `kj-controller/requirements.txt` | Add `pywebpush` (pulls `cryptography`). |
| `CHANGELOG.md` | Entry for sub-project #4. |
| `docs/TESTING.md` | Manual test runbook for push notifications. |
| `docs/ARCHITECTURE.md` | Module map update (new push_dispatcher module + schema). |

---

## Task Ordering Rationale

- **Phase A (Tasks 1–6)** ships the non-push features — wait-estimate upgrade, rules page, "what's playing now", offline banner. Each is an independent user-visible win that works without any PWA/push infrastructure. If push turns out to be a multi-session job, Phase A is shippable on its own.
- **Phase B (Tasks 7–8)** adds the PWA shell and service worker scaffolding — no behaviour change yet, just the registration plumbing.
- **Phase C (Tasks 9–14)** backs the push system: schema, VAPID, routes, dispatcher, hooks.
- **Phase D (Tasks 15–17)** wires the client subscription flow and end-to-end test.
- **Phase E (Task 18)** docs.

Each task ends with a commit. Frequent commits are explicitly favoured.

---

## Phase A — Non-push features

### Task 1: `compute_estimate()` helper + unit tests

**Files:**
- Create: `kj-controller/tests/unit/test_wait_estimate.py`
- Create: `kj-controller/wait_estimate.py`

- [ ] **Step 1: Write the failing test file**

Write `kj-controller/tests/unit/test_wait_estimate.py`:

```python
"""Unit tests for wait-time estimate calculation."""

import pytest

from wait_estimate import compute_estimate


DEFAULT_CFG = {
    "sing_estimate_transition_s": 30,
    "sing_estimate_default_song_s": 240,
    "sing_estimate_min_spread_s": 120,
}


def _entry(id, status="Waiting", duration=None):
    return {"id": id, "status": status, "duration": duration}


class TestComputeEstimate:
    def test_target_not_in_list(self):
        result = compute_estimate([_entry(1), _entry(2)], 99, DEFAULT_CFG)
        assert result["position"] is None

    def test_target_at_position_1(self):
        entries = [_entry(1), _entry(2)]
        result = compute_estimate(entries, 1, DEFAULT_CFG)
        assert result["position"] == 1
        assert result["expected_s"] == 0  # nothing ahead
        assert result["close_to_front"] is True
        assert result["now_singing"] is False

    def test_now_singing_flag(self):
        entries = [_entry(1, status="Now Singing")]
        result = compute_estimate(entries, 1, DEFAULT_CFG)
        assert result["now_singing"] is True

    def test_fallback_baseline_when_no_sung_entries(self):
        # position 3 with no done entries → use fallback baseline for all ahead
        entries = [_entry(1), _entry(2), _entry(3)]
        result = compute_estimate(entries, 3, DEFAULT_CFG)
        # 2 ahead at baseline 240s + 2 transitions of 30s
        assert result["expected_s"] == 240 * 2 + 30 * 2
        assert result["spread_source"] == "fallback"

    def test_tonight_baseline_used_with_3_done(self):
        entries = [
            _entry(10, status="done", duration=180),
            _entry(11, status="done", duration=240),
            _entry(12, status="done", duration=300),  # mean=240, pstdev=48.99
            _entry(1),  # target ahead position 1 (active), 0 songs ahead
            _entry(2),  # target
        ]
        result = compute_estimate(entries, 2, DEFAULT_CFG)
        assert result["position"] == 2
        assert result["spread_source"] == "tonight"
        # 1 ahead uses mean 240s + 1 transition 30s
        assert result["expected_s"] == 240 + 30

    def test_linked_file_duration_preferred_over_baseline(self):
        entries = [
            _entry(10, status="done", duration=180),
            _entry(11, status="done", duration=240),
            _entry(12, status="done", duration=300),
            _entry(1, duration=420),  # custom duration ahead
            _entry(2),
        ]
        result = compute_estimate(entries, 2, DEFAULT_CFG)
        # 1 ahead uses its own 420s + transition 30s
        assert result["expected_s"] == 420 + 30

    def test_negative_and_zero_durations_treated_as_missing(self):
        entries = [
            _entry(1, duration=-1),
            _entry(2, duration=0),
            _entry(3),  # target
        ]
        result = compute_estimate(entries, 3, DEFAULT_CFG)
        # Both ahead fall back to baseline
        assert result["expected_s"] == 240 * 2 + 30 * 2

    def test_done_and_left_entries_excluded_from_ahead(self):
        entries = [
            _entry(1, status="Done", duration=100),
            _entry(2, status="Left"),
            _entry(3),  # target — nothing actually ahead
        ]
        result = compute_estimate(entries, 3, DEFAULT_CFG)
        assert result["position"] == 1
        assert result["expected_s"] == 0

    def test_spread_clamped_to_min(self):
        # Only 3 done entries with near-identical durations → tiny stdev
        entries = [
            _entry(10, status="done", duration=200),
            _entry(11, status="done", duration=200),
            _entry(12, status="done", duration=200),
            _entry(1),
            _entry(2),
        ]
        result = compute_estimate(entries, 2, DEFAULT_CFG)
        spread = result["range_high_s"] - result["expected_s"]
        assert spread == DEFAULT_CFG["sing_estimate_min_spread_s"]

    def test_range_never_below_zero(self):
        # position 1, expected_s = 0; range_low should be clamped to 0
        result = compute_estimate([_entry(1)], 1, DEFAULT_CFG)
        assert result["range_low_s"] == 0

    def test_close_to_front_flag(self):
        entries = [_entry(1), _entry(2), _entry(3), _entry(4)]
        # position 2 → close
        assert compute_estimate(entries, 2, DEFAULT_CFG)["close_to_front"] is True
        # position 3 → not close
        assert compute_estimate(entries, 3, DEFAULT_CFG)["close_to_front"] is False
```

- [ ] **Step 2: Run tests and confirm ImportError**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-singer-expectations/kj-controller
pytest tests/unit/test_wait_estimate.py -v
```

Expected: `ModuleNotFoundError: No module named 'wait_estimate'` (or all tests fail at collection time).

- [ ] **Step 3: Implement `compute_estimate`**

Write `kj-controller/wait_estimate.py`:

```python
"""Wait-time estimate for singer-facing status page.

Computes the expected wait for a target rotation entry, producing both a
point estimate and a range derived from tonight's actual sung-entry
variance (falling back to a configurable minimum spread when we have
too little data).

Called from `sing.py` on each `/sing/status/<id>` poll.
"""

from statistics import mean, pstdev


def compute_estimate(entries, target_id, cfg):
    """Return the estimate dict for `target_id` given `entries`.

    `entries` is a list of rotation entry dicts as returned by
    `RotationManager.get_rotation()`. `target_id` is the entry id whose
    wait we're estimating. `cfg` is the app config dict.

    Returned dict shape:
        {
          "position": int | None,     # 1-based active position; None if not found
          "expected_s": int,          # point estimate (sum of ahead + transitions)
          "range_low_s": int,         # clamped to 0
          "range_high_s": int,
          "spread_source": "tonight" | "fallback",
          "close_to_front": bool,     # position is not None and <= 2
          "now_singing": bool,        # target's status == "Now Singing"
        }
    """
    baseline, stdev_s, spread_source = _baseline(entries, cfg)

    active = [e for e in entries if e.get("status", "").lower() not in ("done", "left")]

    position = None
    target_status = None
    ahead_durations = []
    for i, e in enumerate(active):
        if e["id"] == target_id:
            position = i + 1
            target_status = e.get("status", "")
            break
        ahead_durations.append(_sanitise(e.get("duration"), baseline))

    buffer_s = cfg["sing_estimate_transition_s"] * len(ahead_durations)
    expected = int(sum(ahead_durations) + buffer_s)

    raw_spread = int(stdev_s) if stdev_s is not None else 180
    spread = max(cfg["sing_estimate_min_spread_s"], raw_spread)

    return {
        "position": position,
        "expected_s": expected,
        "range_low_s": max(0, expected - spread),
        "range_high_s": expected + spread,
        "spread_source": spread_source,
        "close_to_front": position is not None and position <= 2,
        "now_singing": (target_status or "").lower() == "now singing",
    }


def _baseline(entries, cfg):
    """Return (baseline_seconds, stdev_or_None, spread_source)."""
    done_durations = [
        e["duration"]
        for e in entries
        if e.get("status", "").lower() == "done"
        and e.get("duration")
        and e["duration"] > 0
    ]
    if len(done_durations) >= 3:
        return mean(done_durations), pstdev(done_durations), "tonight"
    return cfg["sing_estimate_default_song_s"], None, "fallback"


def _sanitise(duration, baseline):
    """Treat missing, zero, or negative durations as the baseline value."""
    if duration is None or duration <= 0:
        return baseline
    return duration
```

- [ ] **Step 4: Run tests and confirm all pass**

```bash
pytest tests/unit/test_wait_estimate.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/wait_estimate.py kj-controller/tests/unit/test_wait_estimate.py
git commit -m "feat: compute_estimate helper for singer wait time

Pure function producing {position, expected_s, range_low_s, range_high_s,
spread_source, close_to_front, now_singing}. Uses tonight's sung-entry
mean/stdev when >=3 entries available, falls back to configurable defaults
otherwise. Sanitises negative/zero durations and clamps range floor to 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Status endpoint + `/sing/now` return new shape

**Files:**
- Modify: `kj-controller/sing.py` (around lines 352–394 and helpers at 413–428)
- Modify: `kj-controller/config.py` (add new config defaults)
- Modify: `kj-controller/tests/integration/test_sing_public_routes.py` (add tests)
- Create: `kj-controller/tests/integration/test_sing_now_and_status.py`

- [ ] **Step 1: Add config defaults**

Find the config defaults block in `kj-controller/config.py` (look for existing `sing_*` keys and add alongside):

```python
# Wait-estimate tuning (sub-project #4)
CONFIG_DEFAULTS.setdefault("sing_estimate_transition_s", 30)
CONFIG_DEFAULTS.setdefault("sing_estimate_default_song_s", 240)
CONFIG_DEFAULTS.setdefault("sing_estimate_min_spread_s", 120)
```

(If `config.py` doesn't have a `CONFIG_DEFAULTS` dict or equivalent, place these inline in the same pattern the existing `sing_rate_limit_*` keys use — read the file to confirm the convention before editing.)

- [ ] **Step 2: Write failing integration tests**

Create `kj-controller/tests/integration/test_sing_now_and_status.py`:

```python
"""Integration tests for /sing/now and the updated /sing/status response shape."""

import json
import pytest


def _enable_token(client, app, token="testtok1234"):
    with app.app_context():
        app.sing_store.set_token(token)
        app.sing_store.set_enabled(True)
    return token


def _submit(client, token, singer="Alice", phone="+61400000001"):
    resp = client.post(
        f"/sing/submit?t={token}",
        json={
            "singer_name": singer,
            "phone": phone,
            "song_artist": "Queen",
            "song_title": "Bohemian Rhapsody",
            "source_type": "make",
        },
    )
    assert resp.status_code == 200
    return resp.get_json()["request"]["id"]


class TestSingNow:
    def test_requires_token(self, sing_client):
        resp = sing_client.get("/sing/now")
        assert resp.status_code == 403

    def test_empty_rotation(self, sing_client, sing_app):
        token = _enable_token(sing_client, sing_app)
        resp = sing_client.get(f"/sing/now?t={token}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == {
            "now_singing": None,
            "up_next": None,
            "queued_count": 0,
        }

    def test_with_rotation(self, sing_client, sing_app):
        token = _enable_token(sing_client, sing_app)
        with sing_app.app_context():
            sing_app.rotation.add_entry("Sarah", song_artist="ABBA — Dancing Queen")
            sing_app.rotation.add_entry("Mike", song_artist="Eagles — Hotel California")
            entries = sing_app.rotation.get_rotation()
            sing_app.rotation.update_status(entries[0]["id"], "Now Singing")
        resp = sing_client.get(f"/sing/now?t={token}")
        data = resp.get_json()
        assert data["now_singing"]["first_name"] == "Sarah"
        assert data["up_next"]["first_name"] == "Mike"
        assert data["queued_count"] == 2


class TestStatusResponseShape:
    def test_status_includes_estimate_and_now_playing(self, sing_client, sing_app):
        token = _enable_token(sing_client, sing_app)
        request_id = _submit(sing_client, token)
        # Approve the request to link it to a rotation entry
        with sing_app.app_context():
            from routes import approve_sing_request
            entry_id = approve_sing_request(sing_app, {"id": request_id,
                                                        "singer_name": "Alice",
                                                        "phone": "+61400000001",
                                                        "song_artist": "Queen",
                                                        "song_title": "Bohemian Rhapsody",
                                                        "source_type": "make",
                                                        "source_ref": None,
                                                        "source_meta": None})
            sing_app.sing_store.mark_approved(request_id, linked_entry_id=entry_id)

        resp = sing_client.get(f"/sing/status/{request_id}?t={token}")
        data = resp.get_json()
        assert "estimate" in data
        assert set(data["estimate"].keys()) >= {
            "position", "expected_s", "range_low_s", "range_high_s",
            "spread_source", "close_to_front", "now_singing"
        }
        assert "now_playing" in data
        # Legacy keys kept during transition
        assert "position" in data
        assert "estimated_wait_s" in data
```

Add fixtures to `kj-controller/tests/conftest.py` (or the nearest conftest) if `sing_client` and `sing_app` don't already exist. Inspect `tests/integration/test_sing_public_routes.py` first — it will already have a suitable fixture, just reuse its name:

```python
# kj-controller/tests/conftest.py — only if not already present
@pytest.fixture
def sing_app(mock_config, tmp_path):
    """App factory for sing/* routes, isolated DB per test."""
    from app import create_app
    cfg = dict(mock_config)
    cfg["rotation_db_path"] = str(tmp_path / "rotation.db")
    app = create_app(cfg)
    return app


@pytest.fixture
def sing_client(sing_app):
    return sing_app.test_client()
```

- [ ] **Step 3: Run failing tests**

```bash
pytest tests/integration/test_sing_now_and_status.py -v
```

Expected: `test_requires_token` passes (endpoint missing → 404 which will need route guard); other tests fail (endpoint or response shape missing).

- [ ] **Step 4: Implement `/sing/now` endpoint**

Add to `kj-controller/sing.py`, after the existing `status` route:

```python
@sing_bp.route("/now", methods=["GET"])
@require_token
def now_playing():
    """Lightweight 'what's playing now' payload for the landing page widget."""
    rotation = getattr(current_app, "rotation", None)
    if rotation is None:
        return jsonify({"now_singing": None, "up_next": None, "queued_count": 0})
    entries = rotation.get_rotation()
    active = [e for e in entries if (e.get("status") or "").lower() not in ("done", "left")]

    now_singing_entry = next(
        (e for e in active if (e.get("status") or "").lower() == "now singing"),
        None,
    )
    up_next_entry = next(
        (e for e in active if e is not now_singing_entry),
        None,
    )
    return jsonify({
        "now_singing": _now_view(now_singing_entry),
        "up_next": _now_view(up_next_entry),
        "queued_count": len(active),
    })


def _now_view(entry):
    if not entry:
        return None
    singer = entry.get("singer") or ""
    return {
        "first_name": singer.split()[0] if singer else "",
        "song_artist": entry.get("song_artist") or "",
    }
```

- [ ] **Step 5: Update `/sing/status/<id>` to return new shape**

In `kj-controller/sing.py`, modify the `status()` function. Locate the block that builds `response` (around line 378) and replace with:

```python
    response = {"request": _public_request_view(req)}

    rotation = getattr(current_app, "rotation", None)
    if rotation is not None:
        entries = rotation.get_rotation()
        # Add now_playing regardless of whether this request is linked
        active = [e for e in entries if (e.get("status") or "").lower() not in ("done", "left")]
        now_singing_entry = next(
            (e for e in active if (e.get("status") or "").lower() == "now singing"),
            None,
        )
        up_next_entry = next(
            (e for e in active if e is not now_singing_entry),
            None,
        )
        response["now_playing"] = {
            "now_singing": _now_view(now_singing_entry),
            "up_next": _now_view(up_next_entry),
            "queued_count": len(active),
        }

        if req.get("linked_entry_id"):
            from wait_estimate import compute_estimate
            estimate = compute_estimate(entries, req["linked_entry_id"], current_app.kj_config)
            response["estimate"] = estimate
            # Legacy fields kept during transition — client will stop reading them in Task 3
            response["position"] = estimate["position"]
            ahead = 0
            for entry in entries:
                if entry.get("id") == req["linked_entry_id"]:
                    break
                if (entry.get("status") or "").lower() not in ("done", "left"):
                    ahead += int(entry.get("duration") or current_app.kj_config["sing_estimate_default_song_s"])
            response["estimated_wait_s"] = ahead
            response["queue"] = _public_queue_view(entries)

    return jsonify(response)
```

- [ ] **Step 6: Run tests and confirm all pass**

```bash
pytest tests/integration/test_sing_now_and_status.py -v
pytest tests/integration/test_sing_public_routes.py -v  # make sure we didn't break existing
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add kj-controller/sing.py kj-controller/config.py \
  kj-controller/tests/integration/test_sing_now_and_status.py \
  kj-controller/tests/conftest.py
git commit -m "feat: /sing/now endpoint + estimate in /sing/status

Adds lightweight 'what's playing now' endpoint for the landing-page widget
and folds an 'estimate' sub-object plus 'now_playing' sub-object into
/sing/status. Legacy position/estimated_wait_s keys kept during the
client-side transition.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Client wait-estimate rendering upgrade

**Files:**
- Modify: `kj-controller/static-sing/sing.js` (around lines 436–467 — `pollStatus` tick function)
- Modify: `kj-controller/static-sing/sing.css` (minor additions for the new status-line copy)

- [ ] **Step 1: Update `pollStatus` to consume new `estimate` shape**

In `kj-controller/static-sing/sing.js`, find the `tick` function inside `pollStatus` (around line 436). Replace the status-line logic with:

```javascript
  const tick = async () => {
    try {
      const data = await fetchStatus(reqId);
      state.status = data;
      const est = data.estimate;
      if (est && est.now_singing) {
        live.textContent = "🎤 You're up — break a leg!";
      } else if (est && est.position === 1) {
        live.textContent = "🎤 You're next — head to the mic";
      } else if (est && est.position === 2) {
        live.textContent = "About 1 song to go";
      } else if (est && est.position >= 3) {
        const low = Math.round(est.range_low_s / 60);
        const high = Math.round(est.range_high_s / 60);
        live.textContent = `You're #${est.position} — about ${low}–${high} min`;
      } else if (data.request?.status === "pending") {
        live.textContent = "Waiting for KJ to approve…";
      } else if (data.request?.status === "rejected") {
        live.textContent = "The KJ needs to talk to you — see them at the desk.";
      } else {
        live.textContent = "Added to the queue.";
      }
      if (queueEl && data.queue) {
        queueEl.innerHTML = "";
        for (const entry of data.queue) {
          queueEl.appendChild(el("div", { class: "queue-row" },
            el("span", { class: "q-name" }, entry.first_name || "—"),
            el("span", { class: "q-song" }, entry.song_artist || ""),
            el("span", { class: "q-status" }, entry.status || ""),
          ));
        }
      }
    } catch {
      live.textContent = "Couldn't update — checking again in a moment.";
    }
  };
```

The old `±20%` math (`const low = Math.max(1, Math.round(waitMin * 0.8));` etc.) is removed entirely.

- [ ] **Step 2: Manually verify the rendering**

Start the dev server:

```bash
cd kj-controller
python app.py
```

In another terminal, enable the event token and prime the rotation:

```bash
curl -X POST http://localhost:80/sing/admin/token/regenerate  # get the token
# enable requests, add a few rotation entries via the KJ UI at http://localhost:80/
```

Open `http://localhost:80/sing/?t=<token>` in a browser, submit a song, and observe the status line. Verify:

- Position 5+ shows "You're #N — about low–high min" with an honest range.
- No more "±20%" artifact.

Stop the dev server. (Manual verification for pure-frontend tweak — no automated JS test suite in this project.)

- [ ] **Step 3: Commit**

```bash
git add kj-controller/static-sing/sing.js
git commit -m "feat(sing): consume /sing/status estimate sub-object

Replace the fake ±20% range with honest low/high pulled from
wait_estimate.compute_estimate. Add position 2 special-case
('about 1 song to go') and now_singing special-case copy.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Rules page — route, template, links

**Files:**
- Create: `kj-controller/templates/sing_rules.html`
- Modify: `kj-controller/sing.py` (add `/rules` route)
- Modify: `kj-controller/static-sing/sing.js` (add rules link on landing page, inline short-form on confirmation)
- Modify: `kj-controller/static-sing/sing.css` (small styles for rules short-form)
- Modify: `kj-controller/tests/integration/test_sing_public_routes.py` (add tests)

- [ ] **Step 1: Write failing integration test for `/sing/rules`**

Add to `kj-controller/tests/integration/test_sing_public_routes.py` (or create it if missing — use the same `sing_client` / `sing_app` fixtures as Task 2):

```python
class TestRulesPage:
    def test_rules_page_unauthenticated(self, sing_client):
        """Rules page is public — no token required."""
        resp = sing_client.get("/sing/rules")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "First come, first sing" in body
        assert "New singers get priority" in body
        assert "Multiple songs welcome" in body
        assert "Need to leave early" in body
        assert "Paid priority" in body
```

- [ ] **Step 2: Run test and confirm failure**

```bash
pytest tests/integration/test_sing_public_routes.py::TestRulesPage -v
```

Expected: 404 (route missing).

- [ ] **Step 3: Implement the route**

Add to `kj-controller/sing.py` after the `now_playing` route:

```python
@sing_bp.route("/rules", methods=["GET"])
def rules():
    """Public rules page — no token gate (bookmarkable, shareable)."""
    return render_template("sing_rules.html")
```

- [ ] **Step 4: Create the template**

Write `kj-controller/templates/sing_rules.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>House rules — Nomad Karaoke</title>
<link rel="icon" href="{{ url_for('sing.static', filename='favicon.ico') }}">
<link rel="stylesheet" href="{{ url_for('sing.static', filename='sing.css') }}">
</head>
<body class="sing">
<main class="sing-card sing-rules">
  <h1>House rules</h1>
  <p class="subtitle">How we keep things fair and fun</p>

  <ol class="rules-list">
    <li>
      <h2>First come, first sing</h2>
      <p>The default order is the order you submit your request.
      If Jim, Bob, and Jenny each send in a song, they'll sing in that order.</p>
    </li>
    <li>
      <h2>New singers get priority</h2>
      <p>First time singing tonight? You'll get bumped up to sing within the next
      few songs, so everyone gets a chance to perform at least once. The next 2
      people in line won't be moved — we respect their spot too.</p>
    </li>
    <li>
      <h2>Multiple songs welcome</h2>
      <p>Submit as many songs as you want! We'll spread them out in the rotation
      so nobody sings twice in a row.</p>
    </li>
    <li>
      <h2>Need to leave early?</h2>
      <p>Let the KJ know and we'll try to get you one last song before you go.
      On a busy night when you've already sung 5+ times we may not be able to
      accommodate — but we'll always try.</p>
    </li>
    <li>
      <h2>Paid priority <span class="heart">♥</span></h2>
      <p>Want to skip ahead? Pay $20+ and you'll be bumped up to sing very soon.
      Paid entries are marked with a <span class="heart">♥</span> on the rotation
      screen so everyone can see it's fair.</p>
    </li>
  </ol>

  <p class="rules-back">
    <a href="javascript:history.back()">← Back</a>
  </p>
</main>
</body>
</html>
```

- [ ] **Step 5: Add CSS for the rules page**

Append to `kj-controller/static-sing/sing.css`:

```css
.sing-rules { max-width: 640px; }
.sing-rules h1 { margin-bottom: 4px; }
.sing-rules .subtitle { color: var(--sing-muted, #aaa); margin-bottom: 24px; }
.sing-rules .rules-list { list-style: decimal; padding-left: 1.5rem; }
.sing-rules .rules-list li { margin-bottom: 1.25rem; }
.sing-rules .rules-list h2 { font-size: 1.1rem; margin-bottom: 4px; }
.sing-rules .rules-list p { color: var(--sing-muted, #bbb); }
.sing-rules .heart { color: #e74c3c; }
.sing-rules .rules-back { margin-top: 2rem; text-align: center; }
```

(If `sing.css` already defines CSS variables different from `--sing-muted`, match those. Read the existing stylesheet first.)

- [ ] **Step 6: Run the integration test — confirm pass**

```bash
pytest tests/integration/test_sing_public_routes.py::TestRulesPage -v
```

Expected: pass.

- [ ] **Step 7: Add inline short-form rules + link on sing.js**

In `kj-controller/static-sing/sing.js`, extend `renderLanding()` to append a footer link. Locate the `renderLanding` function (around line 112) and change the returned element to include a rules link:

```javascript
function renderLanding() {
  return el("main", { class: "sing-card" },
    el("h1", {}, "Request a song"),
    renderNowPlaying(),  // placeholder — implemented in Task 5; safe to call if returns empty div
    el("p", {},
      "Tap below to add your song to the rotation. The KJ will call you up when you're on."),
    el("button", {
      class: "btn primary",
      onclick: () => {
        state.step = state.name && state.phone && PHONE_RE.test(state.phone)
          ? "search"
          : "identity";
        render();
      },
    }, state.name ? "Continue" : "Get started"),
    state.name ? el("p", { class: "hint" },
      `Not ${state.name}? `,
      el("a", { href: "#", onclick: (e) => {
        e.preventDefault();
        state.name = state.phone = "";
        LS.set("sing_name", ""); LS.set("sing_phone", "");
        state.step = "identity"; render();
      } }, "switch")
    ) : null,
    el("p", { class: "sing-footer-links" },
      el("a", { href: "/sing/rules", target: "_blank" }, "House rules"),
    ),
  );
}
```

Add to `renderDone()` before the "Keep this page open" hint:

```javascript
    el("details", { class: "rules-inline" },
      el("summary", {}, "🎤 House rules"),
      el("ul", { class: "rules-short" },
        el("li", {}, "First come, first sing"),
        el("li", {}, "New singers get priority"),
        el("li", {}, "Multiple songs? We'll spread them out"),
        el("li", {}, "Need to leave? Ask the KJ"),
        el("li", {}, "♥ = paid priority ($20+)"),
      ),
      el("p", {},
        el("a", { href: "/sing/rules", target: "_blank" }, "Read full rules →")),
    ),
```

Define a `renderNowPlaying()` stub that returns an empty `<div>` for now — Task 5 wires it up:

```javascript
function renderNowPlaying() {
  return el("div", { class: "now-playing", hidden: true });
}
```

- [ ] **Step 8: Add CSS for inline rules and footer**

Append to `kj-controller/static-sing/sing.css`:

```css
.sing-footer-links { text-align: center; font-size: 0.85rem; margin-top: 1.5rem; }
.sing-footer-links a { color: var(--sing-muted, #aaa); }
.rules-inline { margin-top: 1rem; padding: 0.75rem; border-radius: 8px; background: rgba(255,255,255,0.04); }
.rules-inline summary { cursor: pointer; font-weight: 600; }
.rules-inline .rules-short { list-style: none; padding-left: 0; margin-top: 0.75rem; }
.rules-inline .rules-short li { padding: 0.25rem 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
```

- [ ] **Step 9: Manual verification**

Dev server → open `/sing/rules` in a new tab → confirm styling looks right. Open `/sing/?t=<token>` → see "House rules" link in footer. Submit a song → on the confirmation screen expand the "🎤 House rules" `<details>` and see the 5 lines.

- [ ] **Step 10: Commit**

```bash
git add kj-controller/templates/sing_rules.html kj-controller/sing.py \
  kj-controller/static-sing/sing.js kj-controller/static-sing/sing.css \
  kj-controller/tests/integration/test_sing_public_routes.py
git commit -m "feat(sing): rules page + inline short-form on confirm

Adds public /sing/rules page (no token gate), footer link on landing,
and collapsed <details> with the 5-line short form on the confirmation
step. Restyled port of desktop/rotation_rules_printable.html content.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: "What's playing now" widget (landing + confirmation)

**Files:**
- Modify: `kj-controller/static-sing/sing.js` (implement `renderNowPlaying`, poll `/sing/now` on landing)
- Modify: `kj-controller/static-sing/sing.css` (widget styles)

- [ ] **Step 1: Implement `renderNowPlaying` and its poller**

Replace the `renderNowPlaying` stub in `sing.js` with:

```javascript
// --- "What's playing now" widget -------------------------------------------

let nowPlayingTimer = null;

function renderNowPlaying() {
  const node = el("div", { class: "now-playing", "data-loading": "true" },
    el("div", { class: "np-loading" }, "Checking rotation…"),
  );
  fetchNowPlaying(node);
  return node;
}

async function fetchNowPlaying(node) {
  if (nowPlayingTimer) { clearInterval(nowPlayingTimer); nowPlayingTimer = null; }
  const tick = async () => {
    try {
      const resp = await fetch(`/sing/now?t=${encodeURIComponent(TOKEN)}`, {
        credentials: "same-origin",
      });
      if (!resp.ok) return renderNowError(node);
      updateNowPlaying(node, await resp.json());
    } catch {
      renderNowError(node);
    }
  };
  await tick();
  nowPlayingTimer = setInterval(tick, 15000);
}

function updateNowPlaying(node, data) {
  node.innerHTML = "";
  node.removeAttribute("data-loading");
  const { now_singing, up_next, queued_count } = data || {};
  if (!now_singing && !up_next && !queued_count) {
    node.appendChild(el("div", { class: "np-empty" },
      "Rotation hasn't started yet — you could be the first!"));
    return;
  }
  if (now_singing) {
    node.appendChild(el("div", { class: "np-line np-now" },
      el("span", { class: "np-label" }, "🎤 Now:"),
      el("span", { class: "np-singer" }, now_singing.first_name || "—"),
      now_singing.song_artist
        ? el("span", { class: "np-song" }, `— ${now_singing.song_artist}`)
        : null,
    ));
  }
  if (up_next) {
    node.appendChild(el("div", { class: "np-line np-next" },
      el("span", { class: "np-label" }, "Up next:"),
      el("span", { class: "np-singer" }, up_next.first_name || "—"),
    ));
  } else if (!now_singing && queued_count) {
    node.appendChild(el("div", { class: "np-line" },
      "Between singers — next up soon"));
  }
}

function renderNowError(node) {
  node.innerHTML = "";
  node.removeAttribute("data-loading");
  // Silent failure — don't clutter the card if the status poll hasn't had a chance to fire yet.
}
```

- [ ] **Step 2: Wire into the confirmation page**

In `renderDone`, replace the first line of the `card` construction to include `renderNowPlaying()` at the top:

```javascript
function renderDone() {
  const card = el("main", { class: "sing-card" },
    renderNowPlaying(),  // widget at top of confirmation
    el("h2", {}, state.request?.status === "approved" ? "You're in!" : "Sent!"),
    el("p", {}, state.request?.status === "approved"
      ? "The KJ has added you to the queue."
      : "The KJ will look at it and add you to the queue."),
    el("div", { class: "status-live" }, "Checking your position…"),
    // ...rest unchanged
```

Also, on any navigation *away* from the landing/done step (e.g. to `"identity"` or `"search"`), clear the timer. Add to the `render()` function at the top:

```javascript
function render() {
  if (nowPlayingTimer && state.step !== "landing" && state.step !== "done") {
    clearInterval(nowPlayingTimer);
    nowPlayingTimer = null;
  }
  root.innerHTML = "";
  const view = {
    landing: renderLanding,
    identity: renderIdentity,
    search: renderSearch,
    confirm: renderConfirm,
    done: renderDone,
  }[state.step] || renderLanding;
  root.appendChild(view());
}
```

- [ ] **Step 3: Extend status poll to also drive the widget on confirmation page**

In the `tick` function of `pollStatus`, after the estimate/queue updates, add:

```javascript
      if (data.now_playing) {
        const npNode = card.querySelector(".now-playing");
        if (npNode) updateNowPlaying(npNode, data.now_playing);
      }
```

This avoids two polls on the confirmation page — the 15s `/sing/now` timer is still active from `renderNowPlaying` on first render, but the `/sing/status` response also feeds fresh data every 15s so the widget stays current even if one poll path fails.

- [ ] **Step 4: CSS**

Append to `kj-controller/static-sing/sing.css`:

```css
.now-playing {
  padding: 0.75rem;
  margin-bottom: 1rem;
  border-radius: 10px;
  background: linear-gradient(135deg, rgba(255,91,184,0.08), rgba(111,0,255,0.08));
  border: 1px solid rgba(255,255,255,0.06);
  font-size: 0.95rem;
}
.now-playing[data-loading="true"] { color: var(--sing-muted, #aaa); font-style: italic; }
.np-loading, .np-empty { padding: 0.25rem 0; color: var(--sing-muted, #bbb); }
.np-line { display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: baseline; padding: 0.2rem 0; }
.np-label { font-weight: 600; color: var(--sing-accent, #ff5bb8); }
.np-singer { font-weight: 500; }
.np-song { color: var(--sing-muted, #bbb); }
```

- [ ] **Step 5: Manual verification**

1. Dev server running, event token enabled, rotation empty → open `/sing/?t=<token>` → widget shows "Rotation hasn't started yet".
2. Use KJ UI to add "Sarah — Dancing Queen" and mark her "Now Singing" → refresh landing → widget shows "🎤 Now: Sarah — Dancing Queen".
3. Add "Mike — Hotel California" → widget updates within 15s to include "Up next: Mike".
4. Submit a song as "Alice" → on confirmation page, widget appears at top and updates alongside status.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/static-sing/sing.js kj-controller/static-sing/sing.css
git commit -m "feat(sing): what's playing now widget (landing + confirm)

Shows current Now Singing entry + next-up singer on landing and
confirmation pages. Polls /sing/now on landing; consumes the
now_playing sub-object of /sing/status on confirmation. Empty-state
copy invites the singer to be first when rotation is empty.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Offline banner

**Files:**
- Modify: `kj-controller/static-sing/sing.js`
- Modify: `kj-controller/static-sing/sing.css`
- Modify: `kj-controller/templates/sing.html`

- [ ] **Step 1: Add the banner DOM element**

In `kj-controller/templates/sing.html`, add inside `<body class="sing">`, just before the `{% if closed %}` block:

```html
<div id="sing-offline" class="offline-banner" hidden>
  No internet — keep this page open and we'll update when we're back.
</div>
```

- [ ] **Step 2: Add offline detection logic to sing.js**

Add at the top of `sing.js` (after the `state` object):

```javascript
// --- Offline detection -----------------------------------------------------

let consecutivePollFailures = 0;
const OFFLINE_FAIL_THRESHOLD = 2;

function setOfflineBanner(visible) {
  const el = document.getElementById("sing-offline");
  if (!el) return;
  if (visible) el.removeAttribute("hidden");
  else el.setAttribute("hidden", "");
}

function onPollSuccess() {
  consecutivePollFailures = 0;
  setOfflineBanner(false);
}

function onPollFailure() {
  consecutivePollFailures++;
  if (consecutivePollFailures >= OFFLINE_FAIL_THRESHOLD) setOfflineBanner(true);
}

window.addEventListener("online", () => setOfflineBanner(false));
window.addEventListener("offline", () => setOfflineBanner(true));
```

- [ ] **Step 3: Call `onPollSuccess` / `onPollFailure` from the status poller**

In `pollStatus`'s `tick`, wrap the body:

```javascript
  const tick = async () => {
    try {
      const data = await fetchStatus(reqId);
      onPollSuccess();
      // ...existing rendering logic unchanged
    } catch {
      onPollFailure();
      live.textContent = "Couldn't update — checking again in a moment.";
    }
  };
```

Do the same in `fetchNowPlaying`'s `tick` function:

```javascript
  const tick = async () => {
    try {
      const resp = await fetch(...);
      if (!resp.ok) { onPollFailure(); return renderNowError(node); }
      onPollSuccess();
      updateNowPlaying(node, await resp.json());
    } catch {
      onPollFailure();
      renderNowError(node);
    }
  };
```

- [ ] **Step 4: CSS for the banner**

Append to `sing.css`:

```css
.offline-banner {
  position: sticky;
  top: 0;
  z-index: 10;
  background: #8a3a00;
  color: #fff;
  padding: 0.6rem 1rem;
  text-align: center;
  font-size: 0.9rem;
  box-shadow: 0 2px 8px rgba(0,0,0,0.3);
}
```

- [ ] **Step 5: Manual verification**

1. Dev server running, landing page open → banner hidden.
2. Toggle macOS wifi off (or Chrome DevTools → Network → Offline) → within ~30s the banner appears.
3. Toggle back on → banner disappears within one poll cycle.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/static-sing/sing.js kj-controller/static-sing/sing.css \
  kj-controller/templates/sing.html
git commit -m "feat(sing): offline banner with consecutive-fail fallback

Uses both navigator.onLine events and a 2-failed-poll threshold so
captive-portal networks (where onLine lies) still surface the banner.
Banner clears automatically on next successful poll.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase B — PWA shell + service worker

### Task 7: PWA manifest, iOS meta tags, icons

**Files:**
- Create: `kj-controller/static-sing/icon-192.png`
- Create: `kj-controller/static-sing/icon-512.png`
- Create: `kj-controller/static-sing/badge-72.png`
- Modify: `kj-controller/sing.py` (new `/sing/manifest.json` route — dynamic)
- Modify: `kj-controller/templates/sing.html` (link manifest, iOS meta)
- Modify: `kj-controller/tests/integration/test_sing_public_routes.py` (test manifest)

- [ ] **Step 1: Generate icon assets**

Use an existing Nomad logo PNG and resize. Minimum requirements: 192×192 and 512×512 with transparent background for the app icon, 72×72 monochrome white on transparent for the notification badge.

```bash
cd kj-controller/static-sing
# Port from public-website Nomad logo — adjust path if needed.
# Use whatever image tool you prefer; here's sips (macOS built-in):
sips -Z 192 ../../public-website/public/nomad-karaoke-logo.png --out icon-192.png
sips -Z 512 ../../public-website/public/nomad-karaoke-logo.png --out icon-512.png
# Badge is monochrome — if you don't have one ready, reuse icon-192 as placeholder
# and revisit before ship:
cp icon-192.png badge-72.png
sips -Z 72 badge-72.png
```

Visually verify: `open icon-192.png icon-512.png badge-72.png`.

- [ ] **Step 2: Write failing manifest integration test**

Add to `kj-controller/tests/integration/test_sing_public_routes.py`:

```python
class TestPWAManifest:
    def test_manifest_served_with_current_token(self, sing_client, sing_app):
        with sing_app.app_context():
            sing_app.sing_store.set_token("tok-manifest-1")
            sing_app.sing_store.set_enabled(True)
        resp = sing_client.get("/sing/manifest.json?t=tok-manifest-1")
        assert resp.status_code == 200
        assert resp.content_type.startswith("application/json")
        data = resp.get_json()
        assert data["name"] == "Nomad Karaoke"
        assert data["display"] == "standalone"
        assert data["start_url"] == "/sing/?t=tok-manifest-1"
        assert any(icon["sizes"] == "192x192" for icon in data["icons"])
        assert any(icon["sizes"] == "512x512" for icon in data["icons"])

    def test_manifest_rejects_without_token(self, sing_client):
        resp = sing_client.get("/sing/manifest.json")
        assert resp.status_code == 403
```

- [ ] **Step 3: Run test — confirm 404**

```bash
pytest tests/integration/test_sing_public_routes.py::TestPWAManifest -v
```

Expected: both tests fail (route missing).

- [ ] **Step 4: Implement the dynamic manifest route**

Add to `kj-controller/sing.py`:

```python
@sing_bp.route("/manifest.json", methods=["GET"])
@require_token
def manifest():
    """Dynamic PWA manifest — start_url carries the current event token."""
    token = _extract_token()
    return jsonify({
        "name": "Nomad Karaoke",
        "short_name": "Nomad",
        "description": "Request a song at the karaoke night.",
        "start_url": f"/sing/?t={token}",
        "scope": "/sing/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#0f0f0f",
        "theme_color": "#ff5bb8",
        "icons": [
            {
                "src": url_for("sing.static", filename="icon-192.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": url_for("sing.static", filename="icon-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
        ],
    })
```

- [ ] **Step 5: Add manifest link + iOS meta tags to sing.html**

Modify `kj-controller/templates/sing.html`. Add inside `<head>`:

```html
{% if not closed %}
<link rel="manifest" href="{{ url_for('sing.manifest') }}?t={{ token }}">
<meta name="theme-color" content="#ff5bb8">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Nomad">
<link rel="apple-touch-icon" href="{{ url_for('sing.static', filename='icon-192.png') }}">
{% endif %}
```

- [ ] **Step 6: Run tests — confirm pass**

```bash
pytest tests/integration/test_sing_public_routes.py::TestPWAManifest -v
```

Expected: both pass.

- [ ] **Step 7: Manual verification**

Dev server → open `/sing/?t=<token>` in Chrome desktop → DevTools → Application → Manifest → verify manifest loads, icons render, `start_url` includes token. On Android Chrome, confirm "Install app" option appears in the menu.

- [ ] **Step 8: Commit**

```bash
git add kj-controller/static-sing/icon-192.png kj-controller/static-sing/icon-512.png \
  kj-controller/static-sing/badge-72.png \
  kj-controller/sing.py kj-controller/templates/sing.html \
  kj-controller/tests/integration/test_sing_public_routes.py
git commit -m "feat(sing): PWA manifest + iOS meta tags + icons

Dynamic manifest.json route with token-carrying start_url so installed
home-screen icon lands on a valid gated page. iOS meta tags for
Add-to-Home-Screen behaviour. 192/512 icons + 72 badge placeholder
(badge asset to be replaced with a proper monochrome version later).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Service worker + registration

**Files:**
- Create: `kj-controller/static-sing/sw.js`
- Modify: `kj-controller/sing.py` (serve sw.js with query-string token via Flask so we're at `/sing/sw.js` with service-worker scope `/sing/`)
- Modify: `kj-controller/static-sing/sing.js` (register SW)
- Modify: `kj-controller/tests/integration/test_sing_public_routes.py` (test sw.js served)

- [ ] **Step 1: Write failing test for SW serving**

Add to `test_sing_public_routes.py`:

```python
class TestServiceWorker:
    def test_sw_served_at_sing_scope(self, sing_client):
        """sw.js must be served from /sing/ so its scope is /sing/."""
        resp = sing_client.get("/sing/sw.js")
        assert resp.status_code == 200
        assert "javascript" in resp.content_type
        body = resp.get_data(as_text=True)
        assert "self.addEventListener('push'" in body
        assert "self.addEventListener('notificationclick'" in body
```

- [ ] **Step 2: Run test — confirm failure**

```bash
pytest tests/integration/test_sing_public_routes.py::TestServiceWorker -v
```

Expected: 404 (sw.js at `/sing/static/sw.js` would work but not at `/sing/sw.js` which is what we need for scope).

- [ ] **Step 3: Create `sw.js`**

Write `kj-controller/static-sing/sw.js`:

```javascript
// Nomad Karaoke singer service worker.
// Handles Web Push + notification click. Scope: /sing/
// sw-v0.22.0

const CACHE = 'nomad-sing-shell-v1';
const SHELL = [
  '/sing/static/sing.css',
  '/sing/static/sing.js',
  '/sing/static/icon-192.png',
  '/sing/static/icon-512.png',
  '/sing/static/badge-72.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)),
    )).then(() => self.clients.claim()),
  );
});

// Pull token from the registration URL's query string. The client registers
// sw.js with ?t=TOKEN so we can build notificationclick URLs that include it.
function getToken() {
  try {
    return new URL(self.location.href).searchParams.get('t') || '';
  } catch {
    return '';
  }
}

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { /* non-JSON */ }
  const title = data.title || 'Nomad Karaoke';
  const opts = {
    body: data.body || '',
    tag: data.tag || 'sing-default',
    icon: data.icon || '/sing/static/icon-192.png',
    badge: data.badge || '/sing/static/badge-72.png',
    data: data.data || {},
    renotify: true,
  };
  event.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const d = (event.notification.data || {});
  const token = getToken();
  const base = `/sing/?t=${encodeURIComponent(token)}`;
  const url = d.request_id ? `${base}&r=${d.request_id}` : base;
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if (w.url.includes('/sing/')) {
          w.focus();
          w.postMessage({ type: 'push-focus', data: d });
          return;
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
```

- [ ] **Step 4: Serve sw.js at `/sing/sw.js`**

Add to `kj-controller/sing.py`:

```python
@sing_bp.route("/sw.js", methods=["GET"])
def service_worker():
    """Serve sw.js from /sing/ so its scope covers the sing route.

    Deliberately NOT token-gated — the SW file itself must be reachable
    with or without a token so the browser can fetch updates. The token
    comes through as a query string param for building notificationclick
    URLs, not for auth.
    """
    from flask import send_from_directory, make_response
    resp = make_response(send_from_directory(
        sing_bp.static_folder,
        "sw.js",
        mimetype="application/javascript",
    ))
    # Ensure SW never caches for long (browsers already mostly enforce this)
    resp.headers["Cache-Control"] = "no-cache"
    return resp
```

- [ ] **Step 5: Register SW from sing.js**

Add to `kj-controller/static-sing/sing.js`, near the bottom (before the final `render()` call):

```javascript
// --- Service worker registration ------------------------------------------

async function registerServiceWorker() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return null;
  try {
    const scriptUrl = `/sing/sw.js?t=${encodeURIComponent(TOKEN)}`;
    const reg = await navigator.serviceWorker.register(scriptUrl, { scope: "/sing/" });
    return reg;
  } catch (e) {
    console.warn("SW registration failed:", e);
    return null;
  }
}

let swRegistration = null;
registerServiceWorker().then((reg) => { swRegistration = reg; });
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/integration/test_sing_public_routes.py::TestServiceWorker -v
```

Expected: pass.

- [ ] **Step 7: Manual verification**

Dev server → open `/sing/?t=<token>` in Chrome → DevTools → Application → Service Workers → confirm `sw.js` is registered with scope `/sing/`, status "activated and running".

- [ ] **Step 8: Commit**

```bash
git add kj-controller/static-sing/sw.js kj-controller/sing.py \
  kj-controller/static-sing/sing.js \
  kj-controller/tests/integration/test_sing_public_routes.py
git commit -m "feat(sing): service worker + registration

Registers /sing/sw.js with scope /sing/. SW handles push + notificationclick
(no actual push sends yet — wiring comes in later tasks). Basic shell cache
for offline render of the page. skipWaiting + clients.claim so updates
activate on next page load.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase C — Push backend

### Task 9: `sing_push_subscriptions` schema + CRUD

**Files:**
- Modify: `kj-controller/sing_store.py` (add schema, CRUD methods)
- Modify: `kj-controller/tests/unit/test_sing_store.py` (add tests)

- [ ] **Step 1: Write failing tests for subscription CRUD**

Add to `kj-controller/tests/unit/test_sing_store.py`:

```python
class TestPushSubscriptions:
    def test_insert_subscription(self, tmp_sing_store):
        sub_id = tmp_sing_store.insert_push_subscription(
            token="tok1", phone="+61400000001", singer_name="Alice",
            endpoint="https://fcm.googleapis.com/fcm/send/abc",
            p256dh="p256dh1", auth="auth1", user_agent="Mozilla/5.0",
        )
        assert sub_id > 0
        subs = tmp_sing_store.list_active_push_subscriptions("tok1")
        assert len(subs) == 1
        assert subs[0]["phone"] == "+61400000001"
        assert subs[0]["disabled_at"] is None

    def test_insert_or_replace_on_same_endpoint(self, tmp_sing_store):
        tmp_sing_store.insert_push_subscription(
            token="tok1", phone="+61400000001", singer_name="Alice",
            endpoint="https://fcm.googleapis.com/fcm/send/same", p256dh="a", auth="a",
        )
        # Re-subscribe same (token, endpoint) with fresh keys
        tmp_sing_store.insert_push_subscription(
            token="tok1", phone="+61400000001", singer_name="Alice",
            endpoint="https://fcm.googleapis.com/fcm/send/same", p256dh="b", auth="b",
        )
        subs = tmp_sing_store.list_active_push_subscriptions("tok1")
        assert len(subs) == 1
        assert subs[0]["p256dh"] == "b"

    def test_disable_subscription_filters_it_out(self, tmp_sing_store):
        sid = tmp_sing_store.insert_push_subscription(
            token="tok1", phone="+61400000001", singer_name="Alice",
            endpoint="https://fcm.googleapis.com/fcm/send/abc", p256dh="p", auth="a",
        )
        tmp_sing_store.disable_push_subscription(sid)
        assert tmp_sing_store.list_active_push_subscriptions("tok1") == []

    def test_update_last_sent_state(self, tmp_sing_store):
        sid = tmp_sing_store.insert_push_subscription(
            token="tok1", phone="+61400000001", singer_name="Alice",
            endpoint="ep", p256dh="p", auth="a",
        )
        tmp_sing_store.update_push_sent_state(sid, {"entry_id": 5, "ladder_step": "up_next"})
        subs = tmp_sing_store.list_active_push_subscriptions("tok1")
        import json
        assert json.loads(subs[0]["last_sent_state"])["ladder_step"] == "up_next"

    def test_token_rotation_cleanup(self, tmp_sing_store):
        # Old sub on tok1
        tmp_sing_store.insert_push_subscription(
            token="tok1", phone="+1", singer_name="Old",
            endpoint="old-ep", p256dh="p", auth="a",
        )
        # Manually age the row by 8 days
        import sqlite3
        tmp_sing_store._get_conn().execute(
            "UPDATE sing_push_subscriptions SET created_at = datetime('now', '-8 days', 'localtime')"
        )
        # New sub on tok2
        tmp_sing_store.insert_push_subscription(
            token="tok2", phone="+2", singer_name="New",
            endpoint="new-ep", p256dh="p", auth="a",
        )
        tmp_sing_store.cleanup_stale_push_subscriptions(current_token="tok2")
        # old sub on tok1 (>7d) deleted; new sub on tok2 kept
        assert tmp_sing_store.list_active_push_subscriptions("tok1") == []
        assert len(tmp_sing_store.list_active_push_subscriptions("tok2")) == 1
```

If `tmp_sing_store` fixture doesn't exist yet, add to `kj-controller/tests/conftest.py`:

```python
@pytest.fixture
def tmp_sing_store(tmp_path):
    from sing_store import SingStore
    db = str(tmp_path / "sing.db")
    return SingStore(db)
```

- [ ] **Step 2: Run tests — confirm failure**

```bash
pytest tests/unit/test_sing_store.py::TestPushSubscriptions -v
```

Expected: all fail (methods missing).

- [ ] **Step 3: Add schema and CRUD to SingStore**

In `kj-controller/sing_store.py`, add to the `init_schema()` method's executescript:

```python
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sing_requests ( ... );  # existing

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
            """
        )
```

(Read the existing `init_schema` first — append the new table block at the appropriate point in the script. Don't remove existing tables.)

Add CRUD methods at the bottom of the class:

```python
    # ------------------------------------------------------------------
    # Push subscription CRUD
    # ------------------------------------------------------------------

    def insert_push_subscription(self, token, phone, singer_name, endpoint,
                                  p256dh, auth, user_agent=None):
        """Insert-or-replace on UNIQUE(token, endpoint). Returns row id."""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO sing_push_subscriptions "
            "  (token, phone, singer_name, endpoint, p256dh, auth, user_agent, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime')) "
            "ON CONFLICT(token, endpoint) DO UPDATE SET "
            "  phone=excluded.phone, singer_name=excluded.singer_name, "
            "  p256dh=excluded.p256dh, auth=excluded.auth, "
            "  user_agent=excluded.user_agent, "
            "  updated_at=datetime('now', 'localtime'), "
            "  disabled_at=NULL",
            (token, phone, singer_name, endpoint, p256dh, auth, user_agent),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM sing_push_subscriptions WHERE token=? AND endpoint=?",
            (token, endpoint),
        ).fetchone()
        return row["id"] if row else None

    def list_active_push_subscriptions(self, token):
        """Return all non-disabled subs for `token` as a list of dict rows."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM sing_push_subscriptions "
            "WHERE token=? AND disabled_at IS NULL "
            "ORDER BY id",
            (token,),
        ).fetchall()
        return [dict(r) for r in rows]

    def disable_push_subscription(self, sub_id):
        conn = self._get_conn()
        conn.execute(
            "UPDATE sing_push_subscriptions "
            "SET disabled_at=datetime('now', 'localtime') "
            "WHERE id=?",
            (sub_id,),
        )
        conn.commit()

    def update_push_sent_state(self, sub_id, state_dict):
        import json
        conn = self._get_conn()
        conn.execute(
            "UPDATE sing_push_subscriptions "
            "SET last_sent_state=?, updated_at=datetime('now', 'localtime') "
            "WHERE id=?",
            (json.dumps(state_dict), sub_id),
        )
        conn.commit()

    def cleanup_stale_push_subscriptions(self, current_token):
        """Delete subs for other tokens older than 7 days."""
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM sing_push_subscriptions "
            "WHERE token != ? "
            "  AND created_at < datetime('now', '-7 days', 'localtime')",
            (current_token,),
        )
        conn.commit()

    def find_subs_by_phone(self, token, phone):
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM sing_push_subscriptions "
            "WHERE token=? AND phone=? AND disabled_at IS NULL",
            (token, phone),
        ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests — confirm pass**

```bash
pytest tests/unit/test_sing_store.py::TestPushSubscriptions -v
pytest tests/unit/test_sing_store.py -v  # full sing_store suite still green
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/sing_store.py kj-controller/tests/unit/test_sing_store.py \
  kj-controller/tests/conftest.py
git commit -m "feat(sing): sing_push_subscriptions schema + CRUD

Adds table + CRUD on SingStore: insert_push_subscription (upsert on
token,endpoint), list_active_push_subscriptions, disable, update_sent_state,
cleanup_stale, find_subs_by_phone.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: VAPID key bootstrap

**Files:**
- Modify: `kj-controller/requirements.txt` (add pywebpush)
- Modify: `kj-controller/app.py` (bootstrap VAPID keys)
- Create: `kj-controller/tests/unit/test_vapid_bootstrap.py`

- [ ] **Step 1: Install dependency**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-singer-expectations
# Add to requirements.txt
echo "pywebpush>=2.0.0" >> kj-controller/requirements.txt
# Install in current venv (adjust if using venv)
pip install pywebpush
```

- [ ] **Step 2: Write failing test**

Create `kj-controller/tests/unit/test_vapid_bootstrap.py`:

```python
"""Unit tests for VAPID key bootstrap logic."""

import json
import pytest


def test_bootstrap_generates_keys_when_missing(tmp_path):
    from app import _bootstrap_vapid_keys
    cfg_path = tmp_path / "config.json"
    cfg = {"vapid_subject": "mailto:test@example.com"}
    cfg_path.write_text(json.dumps(cfg))
    _bootstrap_vapid_keys(cfg, str(cfg_path))
    assert cfg["vapid_public_key"]
    assert cfg["vapid_private_key"]
    # Persisted to disk
    saved = json.loads(cfg_path.read_text())
    assert saved["vapid_public_key"] == cfg["vapid_public_key"]


def test_bootstrap_preserves_existing_keys(tmp_path):
    from app import _bootstrap_vapid_keys
    cfg_path = tmp_path / "config.json"
    cfg = {
        "vapid_subject": "mailto:test@example.com",
        "vapid_public_key": "PRESERVED_PUBLIC",
        "vapid_private_key": "PRESERVED_PRIVATE",
    }
    cfg_path.write_text(json.dumps(cfg))
    _bootstrap_vapid_keys(cfg, str(cfg_path))
    assert cfg["vapid_public_key"] == "PRESERVED_PUBLIC"
    assert cfg["vapid_private_key"] == "PRESERVED_PRIVATE"


def test_bootstrap_tolerates_unwritable_config(tmp_path, caplog):
    from app import _bootstrap_vapid_keys
    cfg = {"vapid_subject": "mailto:test@example.com"}
    # Non-existent path — write will fail
    _bootstrap_vapid_keys(cfg, "/nonexistent/path/config.json")
    # Keys still generated in-memory
    assert cfg["vapid_public_key"]
    assert cfg["vapid_private_key"]
    # Error logged
    assert any("VAPID" in rec.message for rec in caplog.records)
```

- [ ] **Step 3: Run test — confirm failure**

```bash
pytest tests/unit/test_vapid_bootstrap.py -v
```

Expected: `ImportError` on `_bootstrap_vapid_keys`.

- [ ] **Step 4: Implement bootstrap**

Add to `kj-controller/app.py`:

```python
def _bootstrap_vapid_keys(cfg, config_path):
    """Ensure cfg has vapid_public_key + vapid_private_key; generate on first boot.

    Writes the generated pair back to config.json on disk. On write failure
    (read-only FS, missing file), logs an error and proceeds with in-memory
    keys — push still works for this process lifetime but subscriptions
    invalidate on restart.
    """
    if cfg.get("vapid_public_key") and cfg.get("vapid_private_key"):
        return
    import base64
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_bytes = priv.private_numbers().private_value.to_bytes(32, "big")
    pub_bytes = priv.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    cfg["vapid_private_key"] = base64.urlsafe_b64encode(priv_bytes).decode("ascii").rstrip("=")
    cfg["vapid_public_key"] = base64.urlsafe_b64encode(pub_bytes).decode("ascii").rstrip("=")
    cfg.setdefault("vapid_subject", "mailto:andrew@beveridge.uk")
    try:
        import json
        with open(config_path, "r") as f:
            on_disk = json.load(f)
        on_disk["vapid_public_key"] = cfg["vapid_public_key"]
        on_disk["vapid_private_key"] = cfg["vapid_private_key"]
        on_disk["vapid_subject"] = cfg["vapid_subject"]
        with open(config_path, "w") as f:
            json.dump(on_disk, f, indent=2)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(
            "VAPID key generation succeeded but persisting to %s failed: %s. "
            "Keys will be regenerated on next restart.",
            config_path, e,
        )
```

Wire the call into `create_app` — find where `config.json` is loaded and call `_bootstrap_vapid_keys(cfg, config_path)` immediately after.

- [ ] **Step 5: Run tests — confirm pass**

```bash
pytest tests/unit/test_vapid_bootstrap.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/requirements.txt kj-controller/app.py \
  kj-controller/tests/unit/test_vapid_bootstrap.py
git commit -m "feat: VAPID key bootstrap on app startup

Generates P-256 keypair on first boot using cryptography. Persists to
config.json; logs error and proceeds with in-memory keys if write fails.
Required for Web Push subscribe flow.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: `/sing/push/subscribe` + `/sing/push/unsubscribe` routes

**Files:**
- Modify: `kj-controller/sing.py` (add routes)
- Create: `kj-controller/tests/integration/test_sing_push_routes.py`

- [ ] **Step 1: Write failing integration tests**

Create `kj-controller/tests/integration/test_sing_push_routes.py`:

```python
"""Integration tests for /sing/push/* routes."""


VALID_SUB = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/abcdefg",
    "keys": {"p256dh": "p256dh-value", "auth": "auth-value"},
}


def _enable_token(app, token="tok-push-1"):
    with app.app_context():
        app.sing_store.set_token(token)
        app.sing_store.set_enabled(True)
    return token


class TestSubscribe:
    def test_requires_token(self, sing_client):
        resp = sing_client.post("/sing/push/subscribe", json={
            "phone": "+61400000001",
            "singer_name": "Alice",
            "subscription": VALID_SUB,
        })
        assert resp.status_code == 403

    def test_persists_subscription(self, sing_client, sing_app):
        token = _enable_token(sing_app)
        resp = sing_client.post(f"/sing/push/subscribe?t={token}", json={
            "phone": "+61400000001",
            "singer_name": "Alice",
            "subscription": VALID_SUB,
        })
        assert resp.status_code == 204
        with sing_app.app_context():
            subs = sing_app.sing_store.list_active_push_subscriptions(token)
        assert len(subs) == 1
        assert subs[0]["phone"] == "+61400000001"

    def test_rejects_malformed_payload(self, sing_client, sing_app):
        token = _enable_token(sing_app)
        resp = sing_client.post(f"/sing/push/subscribe?t={token}", json={
            "phone": "+61400000001",
            # missing singer_name and subscription
        })
        assert resp.status_code == 400


class TestUnsubscribe:
    def test_disables_by_endpoint(self, sing_client, sing_app):
        token = _enable_token(sing_app)
        sing_client.post(f"/sing/push/subscribe?t={token}", json={
            "phone": "+61400000001", "singer_name": "Alice",
            "subscription": VALID_SUB,
        })
        resp = sing_client.post(f"/sing/push/unsubscribe?t={token}", json={
            "endpoint": VALID_SUB["endpoint"],
        })
        assert resp.status_code == 204
        with sing_app.app_context():
            subs = sing_app.sing_store.list_active_push_subscriptions(token)
        assert subs == []
```

- [ ] **Step 2: Run tests — confirm failure**

```bash
pytest tests/integration/test_sing_push_routes.py -v
```

Expected: most fail (routes missing).

- [ ] **Step 3: Implement routes**

Add to `kj-controller/sing.py`:

```python
@sing_bp.route("/push/subscribe", methods=["POST"])
@require_token
def push_subscribe():
    store = current_app.sing_store
    token = _extract_token()
    data = request.get_json(force=True, silent=True) or {}
    phone = (data.get("phone") or "").strip()
    singer_name = (data.get("singer_name") or "").strip()
    sub = data.get("subscription") or {}
    endpoint = (sub.get("endpoint") or "").strip()
    keys = sub.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth_key = (keys.get("auth") or "").strip()

    if not (phone and singer_name and endpoint and p256dh and auth_key):
        return jsonify({"error": "missing fields"}), 400
    if not _PHONE_RE.match(phone):
        return jsonify({"error": "phone format invalid"}), 400

    user_agent = request.headers.get("User-Agent", "")[:500]
    store.insert_push_subscription(
        token=token, phone=phone, singer_name=singer_name,
        endpoint=endpoint, p256dh=p256dh, auth=auth_key,
        user_agent=user_agent,
    )
    return ("", 204)


@sing_bp.route("/push/unsubscribe", methods=["POST"])
@require_token
def push_unsubscribe():
    store = current_app.sing_store
    token = _extract_token()
    data = request.get_json(force=True, silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    if not endpoint:
        return jsonify({"error": "endpoint required"}), 400
    conn = store._get_conn()
    row = conn.execute(
        "SELECT id FROM sing_push_subscriptions WHERE token=? AND endpoint=?",
        (token, endpoint),
    ).fetchone()
    if row:
        store.disable_push_subscription(row["id"])
    return ("", 204)
```

- [ ] **Step 4: Run tests — confirm pass**

```bash
pytest tests/integration/test_sing_push_routes.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/sing.py kj-controller/tests/integration/test_sing_push_routes.py
git commit -m "feat(sing): push subscribe/unsubscribe routes

Token-gated POST /sing/push/subscribe persists { phone, singer_name,
endpoint, keys } via SingStore.insert_push_subscription. Companion
unsubscribe endpoint soft-disables by endpoint match.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: `PushDispatcher` class (ladder, dedup, send)

**Files:**
- Create: `kj-controller/push_dispatcher.py`
- Create: `kj-controller/tests/unit/test_push_dispatcher.py`

- [ ] **Step 1: Write failing tests**

Create `kj-controller/tests/unit/test_push_dispatcher.py`:

```python
"""Unit tests for PushDispatcher — ladder decisions, dedup, send loop."""

import json
from unittest.mock import MagicMock, patch

import pytest

from push_dispatcher import PushDispatcher, decide_ladder_step


def _entry(id, status="Waiting", duration=240):
    return {"id": id, "status": status, "duration": duration, "singer": "X"}


class TestDecideLadderStep:
    def test_now_singing(self):
        target = _entry(1, status="Now Singing")
        assert decide_ladder_step(target, [target]) == "now_singing"

    def test_position_1_up_next(self):
        target = _entry(1)
        assert decide_ladder_step(target, [_entry(1), _entry(2)]) == "up_next"

    def test_position_2_up_next(self):
        target = _entry(2)
        assert decide_ladder_step(target, [_entry(1), _entry(2)]) == "up_next"

    def test_position_3_up_in_2(self):
        target = _entry(3)
        assert decide_ladder_step(target, [_entry(1), _entry(2), _entry(3)]) == "up_in_2"

    def test_position_4_no_ladder(self):
        target = _entry(4)
        assert decide_ladder_step(target, [_entry(i) for i in (1, 2, 3, 4)]) is None

    def test_done_entry_excluded_from_active(self):
        target = _entry(5)
        entries = [
            _entry(10, status="Done"),
            _entry(11, status="Done"),
            _entry(1),
            _entry(5),  # target at active position 2
        ]
        assert decide_ladder_step(target, entries) == "up_next"


class TestDispatcher:
    def _make_dispatcher(self, subs_by_phone=None):
        store = MagicMock()
        store.list_active_push_subscriptions.return_value = []
        store.find_subs_by_phone.side_effect = lambda token, phone: (subs_by_phone or {}).get(phone, [])
        rotation = MagicMock()
        cfg = {
            "vapid_public_key": "pub",
            "vapid_private_key": "priv",
            "vapid_subject": "mailto:test@x.com",
        }
        return PushDispatcher(store=store, rotation=rotation, cfg=cfg,
                              get_current_token=lambda: "tok1",
                              get_linked_phone_for_entry=lambda e: e.get("singer")),\
               store, rotation

    def test_no_token_no_send(self):
        d, store, rotation = self._make_dispatcher()
        d.get_current_token = lambda: None
        with patch("push_dispatcher.webpush") as wp:
            d._dispatch_now()
        wp.assert_not_called()

    def test_dispatches_up_in_2(self):
        sub = {
            "id": 99, "endpoint": "ep", "p256dh": "p", "auth": "a",
            "phone": "+1", "last_sent_state": None,
        }
        d, store, rotation = self._make_dispatcher(subs_by_phone={"+1": [sub]})
        store.list_active_push_subscriptions.return_value = [sub]
        rotation.get_rotation.return_value = [
            {"id": 1, "status": "Waiting", "singer": "+2"},
            {"id": 2, "status": "Waiting", "singer": "+3"},
            {"id": 3, "status": "Waiting", "singer": "+1"},  # target
        ]
        # next_entry_for_phone uses singer field as phone in our stub
        d.get_linked_phone_for_entry = lambda e: e["singer"]
        with patch("push_dispatcher.webpush") as wp:
            d._dispatch_now()
        assert wp.call_count == 1
        args, kwargs = wp.call_args
        payload = json.loads(kwargs.get("data") or args[1])
        assert payload["data"]["step"] == "up_in_2"
        store.update_push_sent_state.assert_called_once()

    def test_dedup_blocks_same_state(self):
        sub = {
            "id": 99, "endpoint": "ep", "p256dh": "p", "auth": "a",
            "phone": "+1",
            "last_sent_state": json.dumps({"entry_id": 3, "ladder_step": "up_in_2"}),
        }
        d, store, rotation = self._make_dispatcher(subs_by_phone={"+1": [sub]})
        store.list_active_push_subscriptions.return_value = [sub]
        rotation.get_rotation.return_value = [
            {"id": 1, "status": "Waiting", "singer": "+2"},
            {"id": 2, "status": "Waiting", "singer": "+3"},
            {"id": 3, "status": "Waiting", "singer": "+1"},
        ]
        d.get_linked_phone_for_entry = lambda e: e["singer"]
        with patch("push_dispatcher.webpush") as wp:
            d._dispatch_now()
        wp.assert_not_called()

    def test_410_disables_sub(self):
        sub = {
            "id": 99, "endpoint": "ep", "p256dh": "p", "auth": "a",
            "phone": "+1", "last_sent_state": None,
        }
        d, store, rotation = self._make_dispatcher(subs_by_phone={"+1": [sub]})
        store.list_active_push_subscriptions.return_value = [sub]
        rotation.get_rotation.return_value = [{"id": 3, "status": "Now Singing", "singer": "+1"}]
        d.get_linked_phone_for_entry = lambda e: e["singer"]
        from pywebpush import WebPushException
        response_mock = MagicMock(); response_mock.status_code = 410
        exc = WebPushException("gone", response=response_mock)
        with patch("push_dispatcher.webpush", side_effect=exc):
            d._dispatch_now()
        store.disable_push_subscription.assert_called_once_with(99)
```

- [ ] **Step 2: Run tests — confirm failure**

```bash
pytest tests/unit/test_push_dispatcher.py -v
```

Expected: `ModuleNotFoundError` on `push_dispatcher`.

- [ ] **Step 3: Implement `PushDispatcher`**

Create `kj-controller/push_dispatcher.py`:

```python
"""PushDispatcher — Web Push sender for singer expectations UI.

Plugged into RotationManager._after_mutation(). Scans active subscriptions
for the current event token, decides which ladder step applies to each
singer's next-closest entry, dedups against last_sent_state, and fires
webpush() on a ThreadPoolExecutor so slow push-service responses don't
block the KJ UI critical path.

Performance budget: ≤50 subs × ≤100ms/call / 2 workers ≈ 2.5s drain.
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from pywebpush import WebPushException, webpush


log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Ladder decision (module-level for testability)
# ----------------------------------------------------------------------

def decide_ladder_step(target_entry, all_entries):
    """Return the ladder step for `target_entry`, or None.

    - "now_singing" if status is Now Singing.
    - "up_next" for positions 1 and 2 (covers big-reorder jumps).
    - "up_in_2" for position 3.
    - None for position 4+.
    """
    status = (target_entry.get("status") or "").lower()
    if status == "now singing":
        return "now_singing"
    active = [e for e in all_entries if (e.get("status") or "").lower() not in ("done", "left")]
    try:
        pos = [e["id"] for e in active].index(target_entry["id"]) + 1
    except ValueError:
        return None
    if pos <= 2:
        return "up_next"
    if pos == 3:
        return "up_in_2"
    return None


def next_entry_for_phone(entries, phone, get_linked_phone):
    """First non-done/left entry whose linked phone matches `phone`."""
    for e in entries:
        if (e.get("status") or "").lower() in ("done", "left"):
            continue
        if get_linked_phone(e) == phone:
            return e
    return None


# Copy per ladder step
_PAYLOADS = {
    "approved":    ("You're in! 🎶",        "The KJ added you — {song}."),
    "rejected":    ("The KJ needs a word",   "Come to the desk about: {song}."),
    "up_in_2":     ("You're up in 2! 🎤",   "{song} — head back to the venue."),
    "up_next":     ("You're up NEXT 🎤",    "{song} — stand by the mic."),
    "now_singing": ("🎤 You're singing now", "{song} — you're up!"),
}


def render_payload(step, entry, request_id=None):
    song = (entry.get("song_artist") or "your song") if entry else "your song"
    title, body_tpl = _PAYLOADS[step]
    return {
        "title": title,
        "body": body_tpl.format(song=song),
        "tag": f"sing-ladder-{entry.get('id')}" if entry else f"sing-{step}",
        "icon": "/sing/static/icon-192.png",
        "badge": "/sing/static/badge-72.png",
        "data": {
            "request_id": request_id,
            "step": step,
        },
    }


# ----------------------------------------------------------------------
# Dispatcher
# ----------------------------------------------------------------------

class PushDispatcher:
    def __init__(self, store, rotation, cfg, get_current_token, get_linked_phone_for_entry):
        """
        store: SingStore instance.
        rotation: RotationManager instance (needs get_rotation()).
        cfg: app config dict with vapid_* keys.
        get_current_token: callable returning the currently-active event token string.
        get_linked_phone_for_entry: callable(entry dict) -> phone string or None.
            Implementation in app.py uses sing_requests.phone via linked_entry_id.
        """
        self.store = store
        self.rotation = rotation
        self.cfg = cfg
        self.get_current_token = get_current_token
        self.get_linked_phone_for_entry = get_linked_phone_for_entry
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="push-")
        self._debounce_timer = None
        self._debounce_lock = threading.Lock()
        self.DEBOUNCE_SECONDS = 0.5

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify_rotation_changed(self):
        """Called from RotationManager._after_mutation(). Debounced."""
        with self._debounce_lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(self.DEBOUNCE_SECONDS, self._dispatch_now)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def notify_request_decision(self, request_id, decision, request_dict):
        """Immediate push for approval/rejection — bypasses rotation scan."""
        token = self.get_current_token()
        if not token:
            return
        phone = (request_dict or {}).get("phone")
        if not phone:
            return
        subs = self.store.find_subs_by_phone(token, phone)
        if not subs:
            return
        step = "approved" if decision == "approved" else "rejected"
        # Fake an entry dict for payload rendering
        fake_entry = {
            "id": request_id,
            "song_artist": f"{request_dict.get('song_title') or ''} — {request_dict.get('song_artist') or ''}".strip(" —"),
        }
        payload = render_payload(step, fake_entry, request_id=request_id)
        for sub in subs:
            self.executor.submit(self._send, sub, payload,
                                 {"entry_id": request_id, "ladder_step": step})

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _dispatch_now(self):
        token = self.get_current_token()
        if not token:
            return
        try:
            entries = self.rotation.get_rotation()
        except Exception:
            log.exception("Rotation fetch failed during push dispatch")
            return
        subs = self.store.list_active_push_subscriptions(token)
        for sub in subs:
            target = next_entry_for_phone(entries, sub["phone"], self.get_linked_phone_for_entry)
            if target is None:
                continue
            step = decide_ladder_step(target, entries)
            if step is None:
                continue
            proposed = {"entry_id": target["id"], "ladder_step": step}
            last = {}
            if sub.get("last_sent_state"):
                try:
                    last = json.loads(sub["last_sent_state"])
                except Exception:
                    last = {}
            if (last.get("entry_id") == proposed["entry_id"]
                    and last.get("ladder_step") == proposed["ladder_step"]):
                continue
            payload = render_payload(step, target, request_id=None)
            self.executor.submit(self._send, sub, payload, proposed)

    def _send(self, sub, payload, proposed_state):
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=json.dumps(payload),
                vapid_private_key=self.cfg["vapid_private_key"],
                vapid_claims={"sub": self.cfg.get("vapid_subject", "mailto:admin@nomadkaraoke.com")},
            )
            self.store.update_push_sent_state(sub["id"], proposed_state)
        except WebPushException as e:
            status = getattr(e.response, "status_code", None) if getattr(e, "response", None) else None
            if status in (404, 410):
                self.store.disable_push_subscription(sub["id"])
            elif status in (400, 401, 403, 413):
                log.error("push send failed sub=%s status=%s", sub["id"], status)
            elif status == 429:
                log.warning("push service rate-limited sub=%s", sub["id"])
            else:
                log.warning("push send error sub=%s: %s", sub["id"], e)
        except Exception:
            log.exception("unexpected push send error sub=%s", sub.get("id"))
```

- [ ] **Step 4: Run tests — confirm pass**

```bash
pytest tests/unit/test_push_dispatcher.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/push_dispatcher.py kj-controller/tests/unit/test_push_dispatcher.py
git commit -m "feat: PushDispatcher class — ladder, dedup, send loop

Module-level decide_ladder_step (now_singing / up_next / up_in_2 / None)
plus PushDispatcher class with debounce, executor pool, dedup via
last_sent_state, and per-status-code error handling (410→disable,
429→retry next tick).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Wire `PushDispatcher` into `RotationManager._after_mutation()`

**Files:**
- Modify: `kj-controller/rotation.py` (call into dispatcher)
- Modify: `kj-controller/app.py` (wire dispatcher onto rotation)
- Create: `kj-controller/tests/integration/test_rotation_push_hook.py`

- [ ] **Step 1: Write failing hook test**

Create `kj-controller/tests/integration/test_rotation_push_hook.py`:

```python
"""Integration test — RotationManager._after_mutation fires push dispatch."""

from unittest.mock import MagicMock


def test_mutation_invokes_push_dispatcher(sing_app):
    dispatcher = MagicMock()
    with sing_app.app_context():
        sing_app.rotation.push_dispatcher = dispatcher
        sing_app.rotation.add_entry("Alice", song_artist="Test Song")
    dispatcher.notify_rotation_changed.assert_called()


def test_mutation_no_push_dispatcher_no_op(sing_app):
    with sing_app.app_context():
        sing_app.rotation.push_dispatcher = None
        # Should not raise
        sing_app.rotation.add_entry("Bob", song_artist="Another Song")
```

- [ ] **Step 2: Run — confirm failure**

```bash
pytest tests/integration/test_rotation_push_hook.py -v
```

Expected: fails because `push_dispatcher` attribute isn't referenced in `_after_mutation`.

- [ ] **Step 3: Update `_after_mutation`**

In `kj-controller/rotation.py`, modify `RotationManager.__init__` to add the attribute default:

```python
    def __init__(self, db_path, sheet_id=None, credentials_file=None, sync_interval=30):
        self.store = RotationStore(db_path)
        self.sync = None
        self.media = None
        self.push_dispatcher = None  # Set by app.py if configured
        # ... existing sync init
```

Modify `_after_mutation`:

```python
    def _after_mutation(self):
        """Write display cache, trigger sync cycle, and notify push dispatcher."""
        self._write_display_cache()
        if self.push_dispatcher is not None:
            try:
                self.push_dispatcher.notify_rotation_changed()
            except Exception:
                # Push is non-critical — never let a dispatcher bug block rotation ops
                import logging
                logging.getLogger(__name__).exception("push dispatch notify failed")
        if self.sync is not None:
            pass
```

- [ ] **Step 4: Run tests — confirm pass**

```bash
pytest tests/integration/test_rotation_push_hook.py -v
pytest tests/unit/test_rotation.py -v  # existing rotation tests still green
```

Expected: all pass.

- [ ] **Step 5: Wire dispatcher into `app.py`**

In `kj-controller/app.py` `create_app`, after the rotation manager is instantiated and after SingStore is set up, add:

```python
    # ----------------------------------------------------------------
    # PushDispatcher — Web Push for singer-facing expectations UI
    # ----------------------------------------------------------------
    _bootstrap_vapid_keys(cfg, config_path)
    from push_dispatcher import PushDispatcher

    def _phone_for_rotation_entry(entry):
        """Look up the phone of the sing_request linked to this entry, if any."""
        entry_id = entry.get("id")
        if entry_id is None:
            return None
        conn = app.sing_store._get_conn()
        row = conn.execute(
            "SELECT phone FROM sing_requests WHERE linked_entry_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (entry_id,),
        ).fetchone()
        return row["phone"] if row else None

    app.rotation.push_dispatcher = PushDispatcher(
        store=app.sing_store,
        rotation=app.rotation,
        cfg=cfg,
        get_current_token=lambda: app.sing_store.get_token(),
        get_linked_phone_for_entry=_phone_for_rotation_entry,
    )
```

(Exact location: find where `app.sing_store` is assigned; this block goes after. The `config_path` variable should already be available — if not, capture it from wherever `config.json` was loaded.)

- [ ] **Step 6: Commit**

```bash
git add kj-controller/rotation.py kj-controller/app.py \
  kj-controller/tests/integration/test_rotation_push_hook.py
git commit -m "feat: wire PushDispatcher into RotationManager._after_mutation

RotationManager now calls self.push_dispatcher.notify_rotation_changed()
after every mutation when the dispatcher is set (matches the existing
self.media optional-attribute pattern). app.py constructs the dispatcher
with a phone-lookup callback that joins sing_requests.phone via
linked_entry_id.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Approve/reject push path + token rotation housekeeping

**Files:**
- Modify: `kj-controller/routes.py` (hook `notify_request_decision` into approve / reject helpers)
- Modify: `kj-controller/sing.py` / `kj-controller/sing_store.py` — wherever `set_token` lives, call cleanup after
- Modify: `kj-controller/tests/integration/test_sing_admin_routes.py` (add test)

- [ ] **Step 1: Locate existing approve/reject code**

```bash
grep -nE "approve_sing_request|reject_sing_request|mark_approved|mark_rejected" kj-controller/routes.py kj-controller/sing_store.py
```

Note the exact function names and signatures. If a standalone `reject_sing_request` doesn't exist, one will need adding (mirroring `approve_sing_request`).

- [ ] **Step 2: Write failing test for approve-triggered push**

Add to `test_sing_admin_routes.py`:

```python
from unittest.mock import MagicMock


def test_approve_triggers_push_notification(sing_client, sing_app):
    # Subscribe a device
    with sing_app.app_context():
        sing_app.sing_store.set_token("tok-a")
        sing_app.sing_store.set_enabled(True)
        sing_app.sing_store.insert_push_subscription(
            token="tok-a", phone="+61400000099", singer_name="Bob",
            endpoint="ep-bob", p256dh="p", auth="a",
        )

    # Install dispatcher mock
    with sing_app.app_context():
        dispatcher = MagicMock()
        sing_app.rotation.push_dispatcher = dispatcher

        # Submit a request
        req = sing_app.sing_store.create_request(
            singer_name="Bob", phone="+61400000099",
            song_artist="Queen", song_title="Radio Ga Ga",
            source_type="make", source_ref=None, source_meta=None, notes="",
        )

        # Approve via admin route
    resp = sing_client.post(
        f"/rotation/requests/{req['id']}/approve",
        json={},
    )
    assert resp.status_code == 200
    dispatcher.notify_request_decision.assert_called_once()
    args, _ = dispatcher.notify_request_decision.call_args
    assert args[1] == "approved"
```

- [ ] **Step 3: Run — confirm failure**

```bash
pytest tests/integration/test_sing_admin_routes.py::test_approve_triggers_push_notification -v
```

Expected: `notify_request_decision` was never called.

- [ ] **Step 4: Hook into approve path**

Find the approve handler in `routes.py`. After `store.mark_approved(...)` succeeds, add:

```python
    dispatcher = getattr(app.rotation, "push_dispatcher", None)
    if dispatcher is not None:
        try:
            dispatcher.notify_request_decision(req["id"], "approved", req)
        except Exception:
            app.logger.exception("push notify_request_decision failed")
```

(Substitute `app`/`req`/field names to match the actual code structure.)

Mirror the same for the reject handler (add `reject_sing_request` helper if needed — structure analogous to approve, with `status='rejected'` and a `rejected_reason` parameter).

- [ ] **Step 5: Add token-rotation housekeeping**

Find where `SingStore.set_token` is called from a route (likely an admin route that rotates the token). After the new token is set, call:

```python
    app.sing_store.cleanup_stale_push_subscriptions(current_token=new_token)
```

- [ ] **Step 6: Run tests — confirm pass**

```bash
pytest tests/integration/test_sing_admin_routes.py -v
```

Expected: new test passes, existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add kj-controller/routes.py kj-controller/sing.py kj-controller/sing_store.py \
  kj-controller/tests/integration/test_sing_admin_routes.py
git commit -m "feat: approve/reject push + token rotation cleanup

Approve and reject admin routes now call push_dispatcher.notify_request_decision,
bypassing the rotation scan for immediate singer feedback. Token rotation
triggers cleanup_stale_push_subscriptions to garbage-collect subs >7 days old.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase D — Push frontend + e2e

### Task 15: Client push subscription UI (Android/desktop happy path)

**Files:**
- Modify: `kj-controller/static-sing/sing.js`
- Modify: `kj-controller/static-sing/sing.css`

- [ ] **Step 1: Add subscription helpers**

Add to `sing.js`, just after the service-worker registration block:

```javascript
// --- Push subscription -----------------------------------------------------

const VAPID_PUBLIC_KEY_META = () => {
  const m = document.querySelector('meta[name="vapid-public-key"]');
  return m ? m.getAttribute("content") : "";
};

function urlB64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function ensurePushSubscription() {
  if (!swRegistration || !("PushManager" in window)) return null;
  const vapidPub = VAPID_PUBLIC_KEY_META();
  if (!vapidPub) return null;
  let sub = await swRegistration.pushManager.getSubscription();
  if (!sub) {
    try {
      sub = await swRegistration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlB64ToUint8Array(vapidPub),
      });
    } catch (e) {
      console.warn("push subscribe failed:", e);
      return null;
    }
  }
  try {
    await fetch(`/sing/push/subscribe?t=${encodeURIComponent(TOKEN)}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        phone: state.phone,
        singer_name: state.name,
        subscription: sub.toJSON(),
      }),
    });
    return sub;
  } catch (e) {
    console.warn("push subscribe POST failed:", e);
    return null;
  }
}

async function requestPushPermission() {
  if (!("Notification" in window)) return "unsupported";
  if (Notification.permission === "granted") {
    await ensurePushSubscription();
    return "granted";
  }
  if (Notification.permission === "denied") return "denied";
  const result = await Notification.requestPermission();
  if (result === "granted") await ensurePushSubscription();
  return result;
}
```

- [ ] **Step 2: Expose VAPID public key in the page**

Modify `kj-controller/sing.py` — the `landing` route renders `sing.html`. Pass the VAPID public key:

```python
    return render_template(
        "sing.html",
        closed=False,
        token=token,
        request_id=request.args.get("r", ""),
        vapid_public_key=current_app.kj_config.get("vapid_public_key", ""),
    )
```

In `kj-controller/templates/sing.html`, add inside `<head>` (conditional on the key being present):

```html
{% if not closed and vapid_public_key %}
<meta name="vapid-public-key" content="{{ vapid_public_key }}">
{% endif %}
```

- [ ] **Step 3: Add the confirmation-screen prompt**

Modify `renderDone` in `sing.js` to insert a push-opt-in block. After the `status-live` element:

```javascript
    el("div", { class: "push-optin", id: "push-optin" }),
```

Add a post-render function (called at the end of `renderDone` before returning card):

```javascript
function maybeShowPushPrompt() {
  const el_ = document.getElementById("push-optin");
  if (!el_) return;
  if (!("Notification" in window) || !swRegistration) {
    el_.remove();
    return;
  }
  const perm = Notification.permission;
  if (perm === "granted") {
    // Silently ensure subscription is POSTed to the server
    ensurePushSubscription();
    el_.textContent = "✓ Notifications on — we'll buzz you when you're up.";
    el_.classList.add("push-on");
    return;
  }
  if (perm === "denied") {
    el_.textContent = "Notifications blocked — keep this tab open for updates.";
    el_.classList.add("push-blocked");
    return;
  }
  // perm === "default" — show prompt button
  el_.innerHTML = "";
  const btn = el("button", {
    class: "btn primary",
    onclick: async () => {
      btn.disabled = true;
      btn.textContent = "Asking…";
      const result = await requestPushPermission();
      if (result === "granted") {
        el_.innerHTML = "";
        el_.textContent = "✓ Notifications on — we'll buzz you when you're up.";
        el_.classList.add("push-on");
      } else {
        btn.disabled = false;
        btn.textContent = "🔔 Notify me when I'm up";
        if (result === "denied") {
          const hint = el("p", { class: "hint" },
            "You blocked notifications — keep this tab open for updates.");
          el_.appendChild(hint);
        }
      }
    },
  }, "🔔 Notify me when I'm up");
  el_.appendChild(btn);
}
```

Call `setTimeout(maybeShowPushPrompt, 2000)` inside `renderDone` after the card is constructed (the 2s delay lets the status message register first):

```javascript
  setTimeout(maybeShowPushPrompt, 2000);
  pollStatus(card);
  return card;
```

- [ ] **Step 4: CSS**

Append to `sing.css`:

```css
.push-optin { margin: 0.75rem 0; text-align: center; }
.push-optin.push-on { color: #6ad29a; font-size: 0.9rem; }
.push-optin.push-blocked { color: var(--sing-muted, #bbb); font-size: 0.9rem; font-style: italic; }
```

- [ ] **Step 5: Manual verification (desktop Chrome)**

Dev server → open `/sing/?t=<token>` → submit a song → on confirmation screen after 2s, see "🔔 Notify me when I'm up" button → tap → browser permission prompt → accept → button swaps to "✓ Notifications on".

Check DevTools → Application → Service Workers → Push → click Push button with sample data → notification fires locally.

Check SQLite: `sqlite3 rotation.db "SELECT * FROM sing_push_subscriptions"` — row exists.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/static-sing/sing.js kj-controller/static-sing/sing.css \
  kj-controller/sing.py kj-controller/templates/sing.html
git commit -m "feat(sing): client push subscription flow

Confirmation page shows a 🔔 Notify me when I'm up button 2s after the
'you're in!' message settles. On grant, subscribes via PushManager with
the VAPID public key (exposed via <meta> tag) and POSTs to
/sing/push/subscribe. Handles granted/denied/default states with distinct
UI copy.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: iOS instructional card + PWA install detection

**Files:**
- Modify: `kj-controller/static-sing/sing.js`
- Modify: `kj-controller/static-sing/sing.css`

- [ ] **Step 1: Add iOS detection and conditional card**

Add to `sing.js`, near the subscription helpers:

```javascript
// --- iOS / standalone detection -------------------------------------------

const IS_IOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
const IS_STANDALONE = window.matchMedia("(display-mode: standalone)").matches
  || window.navigator.standalone === true;

function maybeShowIosInstructions() {
  if (!IS_IOS || IS_STANDALONE) return;
  const container = document.getElementById("push-optin");
  if (!container) return;
  container.innerHTML = "";
  container.classList.add("ios-install");
  container.appendChild(
    el("div", {},
      el("strong", {}, "📱 iPhone? Get tapped when you're up."),
      el("p", {}, "Tap the Share button, then Add to Home Screen, then reopen from your home screen. You'll then be able to enable notifications."),
      el("button", {
        class: "btn ghost",
        onclick: (e) => { e.target.closest(".push-optin").remove(); },
      }, "Got it"),
    ),
  );
}
```

Modify `maybeShowPushPrompt` — at the top, short-circuit:

```javascript
function maybeShowPushPrompt() {
  const el_ = document.getElementById("push-optin");
  if (!el_) return;
  if (IS_IOS && !IS_STANDALONE) {
    maybeShowIosInstructions();
    return;
  }
  // ...rest unchanged
```

- [ ] **Step 2: Add beforeinstallprompt handler (Android/desktop Chrome)**

Near the top of `sing.js`, add:

```javascript
// Capture the install prompt for later — used by an optional install button.
let deferredInstallPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
});
```

Not surfacing the install button in UI for v1 — the push flow's own prompt is enough. Code hook is here in case Task 18 decides to enable it.

- [ ] **Step 3: CSS**

Append to `sing.css`:

```css
.push-optin.ios-install {
  padding: 0.9rem;
  border-radius: 10px;
  background: rgba(255,91,184,0.08);
  border: 1px solid rgba(255,91,184,0.25);
  text-align: left;
}
.push-optin.ios-install strong { display: block; margin-bottom: 0.4rem; }
.push-optin.ios-install p { font-size: 0.9rem; color: var(--sing-muted, #bbb); margin-bottom: 0.6rem; }
.push-optin.ios-install .btn { width: 100%; }
```

- [ ] **Step 4: Manual verification (real iPhone)**

1. Open `https://sing.nomadkaraoke.com/sing/?t=<token>` in Safari on an iPhone → submit a song → on confirmation, the iOS install card appears. Tap "Got it" dismisses it.
2. Tap Share → Add to Home Screen → open the new home-screen icon → submit another song → now see the standard 🔔 prompt.

If no real iPhone available during implementation, defer this manual check to the final test runbook (Task 18) and verify via iOS Simulator in Xcode if desperate.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/static-sing/sing.js kj-controller/static-sing/sing.css
git commit -m "feat(sing): iOS instructional card for PWA install

Detects iOS Safari running outside a standalone PWA and shows a dismissible
install card on the confirmation screen explaining Share → Add to Home Screen.
When running as an installed PWA (or on non-iOS), falls through to the
standard push permission prompt. beforeinstallprompt captured but not
yet surfaced in UI.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 17: End-to-end test

**Files:**
- Create: `kj-controller/tests/e2e/test_sing_push_e2e.py`

- [ ] **Step 1: Write the e2e test**

Create `kj-controller/tests/e2e/test_sing_push_e2e.py`:

```python
"""End-to-end: subscribe → rotation mutations → webpush call sequence."""

import json
import time
from unittest.mock import patch


def test_full_push_ladder_flow(sing_client, sing_app):
    token = "tok-e2e-1"
    with sing_app.app_context():
        sing_app.sing_store.set_token(token)
        sing_app.sing_store.set_enabled(True)

        # Submit a request as singer A
        req = sing_app.sing_store.create_request(
            singer_name="Alice", phone="+61400000001",
            song_artist="Queen", song_title="Somebody to Love",
            source_type="make", source_ref=None, source_meta=None, notes="",
        )
        # Approve so it's in the rotation
        from routes import approve_sing_request
        entry_id = approve_sing_request(sing_app, req)
        sing_app.sing_store.mark_approved(req["id"], linked_entry_id=entry_id)

        # Subscribe Alice's device
        sing_app.sing_store.insert_push_subscription(
            token=token, phone="+61400000001", singer_name="Alice",
            endpoint="alice-ep", p256dh="p", auth="a",
        )

    # Add filler entries ahead of Alice so she starts at position 3
    with sing_app.app_context():
        sing_app.rotation.add_entry("FillerA", song_artist="Filler 1")
        sing_app.rotation.add_entry("FillerB", song_artist="Filler 2")

    # Now reorder so Alice's entry is at position 3 (2 ahead)
    with sing_app.app_context():
        sing_app.rotation.move_entry(entry_id, 3)

    # Let the debounce timer fire
    time.sleep(1.0)

    with patch("push_dispatcher.webpush") as wp:
        # Moving to position 2 → up_next
        with sing_app.app_context():
            sing_app.rotation.move_entry(entry_id, 2)
        time.sleep(1.0)
        # Moving to position 1 → still up_next (same step, no new push)
        with sing_app.app_context():
            sing_app.rotation.move_entry(entry_id, 1)
        time.sleep(1.0)
        # Mark Now Singing → now_singing step
        with sing_app.app_context():
            sing_app.rotation.mark_singing(entry_id)
        time.sleep(1.0)

        # Collect dispatched payload steps in call order
        steps = []
        for call in wp.call_args_list:
            data = call.kwargs.get("data") or call.args[1]
            payload = json.loads(data)
            steps.append(payload["data"]["step"])
        # Expected: up_next (first time at pos <=2), now_singing
        # (no duplicate up_next for pos 1→2 swap because same ladder_step)
        assert "up_next" in steps
        assert "now_singing" in steps
        assert steps.count("up_next") == 1  # dedup worked
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/e2e/test_sing_push_e2e.py -v
```

Expected: pass (may need timing adjustments if debounce feels flaky — bump sleeps to 1.5s if needed).

- [ ] **Step 3: Commit**

```bash
git add kj-controller/tests/e2e/test_sing_push_e2e.py
git commit -m "test(e2e): subscribe + rotation flow pushes correct ladder steps

End-to-end test: approve → subscribe → reorder entries → verify the
expected sequence of ladder-step payloads fires via mocked webpush,
and dedup blocks a redundant up_next at positions 1 and 2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Phase E — Docs

### Task 18: CHANGELOG, TESTING runbook, ARCHITECTURE update

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/TESTING.md`
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: CHANGELOG entry**

Add a new top-of-file entry to `CHANGELOG.md` (check the existing format first — match it):

```markdown
## 0.22.0 — 2026-04-22

### Added (singer expectations UI — sub-project #4)

- **Wait-time estimates:** honest range grounded in tonight's sung-entry variance
  (or a configurable fallback with ±3min when we have <3 sung entries).
  Replaces the bogus `±20%` point-estimate fakery.
- **Web Push notifications:** PWA manifest, service worker, `PushDispatcher`
  server-side. Singers opt in on the confirmation screen and get
  `up_in_2` / `up_next` / `now_singing` pushes as their entry reaches
  the front of the rotation. `approved` and `rejected` pushes fire on
  KJ decision.
- **Rules page** at `/sing/rules` — public, styled, with the existing 5 rules.
  Inline short-form on the confirmation screen.
- **"What's playing now" widget** on landing and confirmation pages —
  shows the current singer + song plus up-next.
- **Offline banner** with `navigator.onLine` + consecutive-poll-failure
  fallback for captive-portal networks.

### Changed

- `/sing/status/<id>` response: new `estimate` and `now_playing` sub-objects
  (legacy top-level `position`, `estimated_wait_s`, `queue` kept for transition).
- `RotationManager._after_mutation()` now notifies `push_dispatcher` when one
  is wired (defaults to None; no effect if not configured).
- New config keys: `sing_estimate_transition_s` (30), `sing_estimate_default_song_s`
  (240), `sing_estimate_min_spread_s` (120), `vapid_public_key`,
  `vapid_private_key`, `vapid_subject`.

### Database

- New table: `sing_push_subscriptions` in `rotation.db`.

### Docs

- `docs/TESTING.md` — manual push-testing runbook added.
- `docs/ARCHITECTURE.md` — new module map entry for `push_dispatcher`.
- `docs/archive/2026-04-22-singer-expectations-design.md` — design spec.
- `docs/archive/2026-04-22-singer-expectations-plan.md` — implementation plan.
```

- [ ] **Step 2: TESTING runbook**

Append to `docs/TESTING.md` (or create if missing):

```markdown
## Push notifications (manual runbook)

Web Push is partially untestable in CI — service worker lifecycle and
real push delivery require a browser. Run this checklist on at least
one Android device and one iPhone before shipping push changes to prod.

### Setup

1. Local HTTPS: you'll need HTTPS for Web Push on mobile.
   Option A: deploy to staging (`https://sing-staging.nomadkaraoke.com` if exists).
   Option B: use `ngrok http 80` or similar.
2. Enable the event token and auto-approve off on the KJ UI.

### Android Chrome

- [ ] Open `/sing/?t=<token>` — submit a song.
- [ ] On confirmation, 🔔 button appears after ~2s → tap → permission prompt → accept.
- [ ] Button swaps to "✓ Notifications on".
- [ ] `sqlite3 rotation.db "SELECT phone FROM sing_push_subscriptions"` shows the row.
- [ ] In KJ UI, move the submitted entry to position 3 → phone receives a "You're up in 2!" push.
- [ ] Move to position 2 → "You're up NEXT" push.
- [ ] Mark Now Singing → "You're singing now" push.
- [ ] Tap the notification → page reopens/focuses and shows the singer's status.

### iPhone Safari

- [ ] Open `/sing/?t=<token>` — submit a song → on confirmation see the iOS install card.
- [ ] Tap Share → Add to Home Screen → reopen from home screen.
- [ ] Submit another song → confirmation now shows the 🔔 button.
- [ ] Accept notifications → KJ UI moves entry → push arrives.

### Desktop Chrome

- [ ] Install as desktop PWA.
- [ ] Push arrives while the window is backgrounded or the browser closed.

### Offline

- [ ] Submit a song while online.
- [ ] Turn wifi off → within ~30s see the offline banner at top of the page.
- [ ] Turn wifi back on → banner clears within one poll cycle.

### Dedup

- [ ] Two phones, same singer first name ("Alex" + "Alex") → only the
      correct phone's entry triggers their push.
- [ ] Single phone, two songs in queue → first Done'd → ladder resets
      cleanly for the second entry.
```

- [ ] **Step 3: ARCHITECTURE update**

Find the module map / file listing in `docs/ARCHITECTURE.md` and add entries for:

- `kj-controller/push_dispatcher.py` — PushDispatcher (VAPID, subscription CRUD, ladder, dedup, send pool).
- `kj-controller/wait_estimate.py` — pure function for singer wait-time estimate.
- `kj-controller/static-sing/sw.js` — service worker for push + notificationclick.
- `kj-controller/static-sing/manifest.json` — served dynamically by `/sing/manifest.json` route so `start_url` includes the current token.

Add a short "Singer Push" section describing the flow:

```markdown
### Singer Web Push (sub-project #4)

- `push_dispatcher.py` hooks into `RotationManager._after_mutation()`.
- Every mutation triggers a 500ms-debounced dispatch that scans active
  subscriptions (`sing_push_subscriptions` table, scoped to the current
  event token) and sends `up_in_2` / `up_next` / `now_singing` pushes via
  `pywebpush` on a 2-worker thread pool.
- Dedup via `last_sent_state` JSON column on each subscription — same
  `(entry_id, ladder_step)` pair never fires twice.
- Approve/reject from admin routes calls `notify_request_decision` for
  immediate singer feedback, bypassing the rotation scan.
- VAPID keys auto-generated on first boot (stored in `config.json`).
- Client registers `/sing/sw.js?t=<token>`; `start_url` in the dynamic
  manifest at `/sing/manifest.json` also carries the token so installed
  home-screen icons land on a valid gated page.
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md docs/TESTING.md docs/ARCHITECTURE.md
git commit -m "docs: CHANGELOG + testing runbook + architecture for sub-project #4

Version 0.22.0 adds singer expectations UI: wait estimates, Web Push,
rules page, what's-playing widget, offline banner. Manual push-testing
runbook added to docs/TESTING.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review (written against the spec)

**Spec coverage:** every section and feature listed below has a task:

- Section 1 (components): every new/modified file listed in Tasks 1–18.
- Section 2 (data model): Task 9 (schema+CRUD), Task 2 (config keys), Task 10 (VAPID bootstrap).
- Section 3a (wait estimates): Tasks 1, 2, 3.
- Section 3b (push notifications): Tasks 7 (PWA), 8 (SW), 9–14 (backend), 15–17 (frontend + e2e).
- Section 3c (rules page): Task 4.
- Section 3d (what's playing widget): Tasks 2 (endpoint) and 5 (UI).
- Section 4 (data flows): the e2e test in Task 17 exercises flow B end-to-end; Task 6 implements flow E; Task 16 implements flow A variants.
- Section 5 (error handling): covered in the PushDispatcher unit tests (Task 12) — 410, 429, non-JSON payloads.
- Section 6 (testing): every file listed is created by the corresponding feature task.
- Open implementation notes: `rotate_token` cleanup is Task 14 step 5; `reject_sing_request` helper is Task 14 step 4; SW token passing via query string is Task 8 step 3; dynamic manifest is Task 7 step 4; transition from polling to push-aware status (no dedup needed) is in Task 3 and Task 15 (push overlays polling; no shared DOM).

**Placeholder scan:** every step has complete code or a concrete command. No TBDs, no "add appropriate error handling" waves.

**Type consistency:**
- `PushDispatcher.notify_rotation_changed()` — consistent across Task 12, 13, 14.
- `PushDispatcher.notify_request_decision(request_id, decision, request_dict)` — three-arg signature consistent in Task 12 and Task 14.
- `SingStore.insert_push_subscription(...)` kwargs match between Task 9, 11, 14, 17.
- `decide_ladder_step(target_entry, all_entries)` signature consistent in the design doc and Task 12.
- `compute_estimate(entries, target_id, cfg)` signature consistent in Task 1, 2, spec.
- Endpoint paths consistent: `/sing/rules`, `/sing/now`, `/sing/manifest.json`, `/sing/sw.js`, `/sing/push/subscribe`, `/sing/push/unsubscribe`.
- Table name `sing_push_subscriptions` and column set consistent across Tasks 9, 11, 14, 17.
- Config keys consistent: `sing_estimate_transition_s`, `sing_estimate_default_song_s`, `sing_estimate_min_spread_s`, `vapid_public_key`, `vapid_private_key`, `vapid_subject`.

No gaps found; no inconsistencies found. Plan ready for execution.
