# Simple KJ Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `simple_mode` toggle in the KJ controller that (a) restricts singer requests to `local`/`divebar`/`kn` sources only, and (b) collapses the KJ UI to just pending-request approval + rotation + playback + the toggle itself.

**Architecture:** Single persistent flag (`kj_simple_mode`) stored in the existing `sing_meta` key/value table. Server enforces the narrowed source allowlist on `POST /sing/submit` (defence-in-depth, since the singer endpoint is internet-reachable via `sing.nomadkaraoke.com`). KJ UI is CSS-driven — a `<body class="simple-mode">` class hides panels via one style block. The flag rides on the existing 2s `/status` poll, so the KJ side reacts within a tick.

**Tech Stack:** Flask + SQLite (`sing_store.py`) on the server; vanilla JS / Jinja templates / CSS on the client. pytest for backend tests; manual smoke for frontend.

**Spec:** [docs/archive/2026-05-22-simple-kj-mode-design.md](2026-05-22-simple-kj-mode-design.md)

---

## Conventions

- All paths are relative to the worktree root (`/Users/andrew/Projects/nomadkaraoke/kjbox-simple-kj-mode/`).
- Backend tests live under `kj-controller/tests/unit/` (no Flask) and `kj-controller/tests/integration/` (with Flask client).
- Tests use the fixtures defined in `kj-controller/tests/conftest.py`: `client` + `sing_app` + `token` for end-to-end singer-side tests; `flask_test_client` for KJ-side; `SingStore(":memory:")` or a tmp-path DB for unit tests.
- Run tests with `cd kj-controller && pytest <path> -v`.
- Commit after each task. Do **not** push during implementation — the user must explicitly approve `git push` (auto-deploy lives on NomadPC).

---

## Task 1: SingStore — add `is_simple_mode` / `set_simple_mode`

**Files:**
- Modify: `kj-controller/sing_store.py` (constants block at top + the methods block near `is_accepting_make_requests`)
- Test: `kj-controller/tests/unit/test_sing_store.py`

- [ ] **Step 1: Write the failing test**

Append to `kj-controller/tests/unit/test_sing_store.py` inside the existing class that holds `test_accept_make_requests_default_true` (search for that test name):

```python
    def test_simple_mode_default_false(self, store):
        assert store.is_simple_mode() is False

    def test_simple_mode_round_trip(self, store):
        store.set_simple_mode(True)
        assert store.is_simple_mode() is True
        store.set_simple_mode(False)
        assert store.is_simple_mode() is False

    def test_simple_mode_persists_across_instances(self, tmp_path):
        from sing_store import SingStore
        db = tmp_path / "simple_mode.db"
        s1 = SingStore(str(db))
        s1.set_simple_mode(True)
        s1.close()
        s2 = SingStore(str(db))
        assert s2.is_simple_mode() is True
        s2.close()
```

- [ ] **Step 2: Run test to verify it fails**

```
cd kj-controller && pytest tests/unit/test_sing_store.py -v -k simple_mode
```

Expected: 3 failures with `AttributeError: 'SingStore' object has no attribute 'is_simple_mode'`.

- [ ] **Step 3: Add the constant**

Edit `kj-controller/sing_store.py`. Find the existing block:

```python
TOKEN_KEY = "request_token"
ENABLED_KEY = "request_token_enabled"
AUTO_APPROVE_KEY = "request_auto_approve"
ACCEPT_MAKE_REQUESTS_KEY = "sing_accept_make_requests"
```

Add one line below it:

```python
SIMPLE_MODE_KEY = "kj_simple_mode"
```

- [ ] **Step 4: Add the methods**

In the same file, find `def set_accepting_make_requests(self, enabled):` (around line 254) and add immediately after it:

```python
    def is_simple_mode(self):
        """Return True if the KJ controller is in stand-in / simple-operator mode.

        When on, the singer UI restricts the source allowlist to local/
        divebar/kn (no YouTube paste, make, or kj_pick deferral), and the KJ
        UI hides search panels, manual rotation entry, and most System
        controls. Persistent across rotation archives; toggled from the KJ
        controller's System → Mode subsection.
        """
        return self._get_meta(SIMPLE_MODE_KEY, "0") == "1"

    def set_simple_mode(self, enabled):
        self._set_meta(SIMPLE_MODE_KEY, "1" if enabled else "0")
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd kj-controller && pytest tests/unit/test_sing_store.py -v -k simple_mode
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/sing_store.py kj-controller/tests/unit/test_sing_store.py
git commit -m "feat(sing-store): add kj_simple_mode flag

Mirrors the existing accept_make_requests pattern: persistent meta flag,
default off, round-trip getter/setter. Wired into routes in subsequent
commits."
```

---

## Task 2: `GET /rotation/requests/config` returns `simple_mode`

**Files:**
- Modify: `kj-controller/routes.py:3296-3313` (the `get_sing_config` handler)
- Test: `kj-controller/tests/integration/test_sing_admin_routes.py`

- [ ] **Step 1: Write the failing test**

Append a new test method to the same class that already contains `test_get_config_exposes_accept_make_requests` in `kj-controller/tests/integration/test_sing_admin_routes.py`:

```python
    def test_get_config_exposes_simple_mode(self, admin_client):
        """The admin config endpoint surfaces the simple_mode flag (default off)."""
        resp = admin_client.get("/rotation/requests/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "simple_mode" in data
        assert data["simple_mode"] is False  # default off
```

- [ ] **Step 2: Run the test to verify it fails**

```
cd kj-controller && pytest tests/integration/test_sing_admin_routes.py::TestSingAdminConfig::test_get_config_exposes_simple_mode -v
```

(If the class isn't named `TestSingAdminConfig`, search the file for `test_get_config_exposes_accept_make_requests` and use that class name.)

Expected: FAIL — `KeyError: 'simple_mode'` or `assert 'simple_mode' in data`.

- [ ] **Step 3: Wire the flag into the response**

In `kj-controller/routes.py`, find the `get_sing_config` function (around line 3296). Add `simple_mode` to the returned dict, placed next to the other booleans:

```python
    return jsonify({
        "token": token,
        "enabled": store.is_enabled(),
        "auto_approve": store.is_auto_approve(),
        "accept_make_requests": store.is_accepting_make_requests(),
        "simple_mode": store.is_simple_mode(),
        "public_url": get_event_url(cfg, token, scope="public"),
        "local_url": get_event_url(cfg, token, scope="local"),
        "pending_count": store.count_pending(),
    })
```

- [ ] **Step 4: Run the test to verify it passes**

```
cd kj-controller && pytest tests/integration/test_sing_admin_routes.py -v -k simple_mode
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_sing_admin_routes.py
git commit -m "feat(routes): expose simple_mode in GET /rotation/requests/config"
```

---

## Task 3: `POST /rotation/requests/config` accepts `simple_mode`

**Files:**
- Modify: `kj-controller/routes.py:3316-3366` (the `update_sing_config` handler)
- Test: `kj-controller/tests/integration/test_sing_admin_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to the same test class:

```python
    def test_toggle_simple_mode_on(self, admin_client, admin_app):
        resp = admin_client.post(
            "/rotation/requests/config",
            json={"simple_mode": True},
        )
        assert resp.status_code == 200
        assert resp.get_json()["changed"]["simple_mode"] is True
        # Confirm via GET
        resp2 = admin_client.get("/rotation/requests/config")
        assert resp2.get_json()["simple_mode"] is True

    def test_toggle_simple_mode_isolated_from_other_flags(
        self, admin_client, admin_app,
    ):
        """Toggling simple_mode must not reset auto_approve or
        accept_make_requests."""
        admin_app.sing_store.set_auto_approve(True)
        admin_app.sing_store.set_accepting_make_requests(False)
        resp = admin_client.post(
            "/rotation/requests/config", json={"simple_mode": True},
        )
        assert resp.status_code == 200
        cfg = admin_client.get("/rotation/requests/config").get_json()
        assert cfg["simple_mode"] is True
        assert cfg["auto_approve"] is True
        assert cfg["accept_make_requests"] is False
```

If your test file uses a different fixture name than `admin_app` for the Flask app, mirror the convention of the surrounding tests (search for `accept_make_requests` to find the right names).

- [ ] **Step 2: Run the tests to verify they fail**

```
cd kj-controller && pytest tests/integration/test_sing_admin_routes.py -v -k "simple_mode and toggle"
```

Expected: 2 failures — `simple_mode` is not in `changed`.

- [ ] **Step 3: Wire the POST handler**

In `kj-controller/routes.py`, find `update_sing_config`. After the existing `if "accept_make_requests" in data:` block (around line 3362), add:

```python
    if "simple_mode" in data:
        store.set_simple_mode(bool(data["simple_mode"]))
        changed["simple_mode"] = bool(data["simple_mode"])
```

- [ ] **Step 4: Run the tests to verify they pass**

```
cd kj-controller && pytest tests/integration/test_sing_admin_routes.py -v -k simple_mode
```

Expected: 3 passed (Task 2's GET test + the two new ones).

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_sing_admin_routes.py
git commit -m "feat(routes): accept simple_mode in POST /rotation/requests/config"
```

---

## Task 4: `GET /status` includes `simple_mode`

**Files:**
- Modify: `kj-controller/routes.py:744-806` (the `get_status` handler)
- Test: `kj-controller/tests/integration/test_routes.py`

- [ ] **Step 1: Locate the existing test class for `/status`**

```
cd kj-controller && grep -n "GET /status\|/status'\|get_status\|status.*endpoint" tests/integration/test_routes.py | head -10
```

Pick the existing class that tests `GET /status` (look for a method like `test_status_returns_*` or `test_get_status_*`).

- [ ] **Step 2: Write the failing test**

Append to that class (replace `TestStatusEndpoint` below with the actual class name from Step 1):

```python
    def test_status_includes_simple_mode(self, flask_test_client, flask_app):
        # Default off
        resp = flask_test_client.get("/status")
        assert resp.status_code == 200
        assert resp.get_json().get("simple_mode") is False
        # Flip it on, status reflects it
        flask_app.sing_store.set_simple_mode(True)
        resp2 = flask_test_client.get("/status")
        assert resp2.get_json().get("simple_mode") is True
```

- [ ] **Step 3: Run the test to verify it fails**

```
cd kj-controller && pytest tests/integration/test_routes.py -v -k simple_mode
```

Expected: FAIL — `simple_mode` not in response.

- [ ] **Step 4: Wire the flag into `/status`**

In `kj-controller/routes.py`, find the `get_status` function (route `/status`, around line 744). The function builds a `status` dict and returns `jsonify(status)`. Add this line **just before** the `return jsonify(status)`:

```python
    try:
        status["simple_mode"] = current_app.sing_store.is_simple_mode()
    except Exception:
        status["simple_mode"] = False
```

The try/except keeps `/status` resilient if `sing_store` is somehow not configured — `/status` is the heartbeat poll and must never 500.

- [ ] **Step 5: Run the test to verify it passes**

```
cd kj-controller && pytest tests/integration/test_routes.py -v -k simple_mode
```

Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_routes.py
git commit -m "feat(routes): include simple_mode in GET /status payload

KJ frontend polls /status every 2s; piggy-backing the flag avoids a
separate fetch and keeps the body class in lockstep with backend state."
```

---

## Task 5: `POST /sing/submit` narrows allowlist in simple mode

**Files:**
- Modify: `kj-controller/sing.py:506-591` (the `submit` handler) — narrow the allowlist after the existing `_ALLOWED_SOURCES` validation
- Test: new file `kj-controller/tests/integration/test_sing_simple_mode_e2e.py` (mirrors `test_sing_make_request_disable_e2e.py`)

- [ ] **Step 1: Write the failing E2E test**

Create `kj-controller/tests/integration/test_sing_simple_mode_e2e.py`:

```python
"""End-to-end: KJ flips Simple Mode on, singer submits get narrowed.

Walks through:
  1. Default (off) → all source types accepted.
  2. KJ flips Simple Mode on.
  3. Singer (stale client) submits source_type=youtube → 400.
  4. Singer submits source_type=make → 400 (simple_mode wins over the
     existing make_requests_disabled message).
  5. Singer submits source_type=kj_pick → 400.
  6. Singer submits source_type=local → 200.
  7. KJ flips back off; youtube submit succeeds again.
"""


def _body(**overrides):
    body = {
        "singer_name": "Test Singer",
        "phone": "+1 555 0100",
        "song_artist": "Test Artist",
        "song_title": "Test Title",
        "source_type": "local",
        "source_ref": "/library/test.mp4",
    }
    body.update(overrides)
    return body


class TestSimpleModeE2E:
    def test_simple_mode_narrows_sources(
        self, client, sing_app, token, monkeypatch,
    ):
        # Stub KN so /sing/search doesn't reach the network.
        import karaoke_nerds
        monkeypatch.setattr(karaoke_nerds, "search", lambda *a, **kw: [])
        admin = sing_app.test_client()

        # 1. Default: youtube submit works.
        sub0 = client.post(
            f"/sing/submit?t={token}",
            json=_body(source_type="youtube", source_ref="https://youtu.be/x"),
        )
        assert sub0.status_code == 200, sub0.get_json()

        # 2. KJ enables Simple Mode.
        cfg = admin.post(
            "/rotation/requests/config", json={"simple_mode": True},
        )
        assert cfg.status_code == 200
        assert cfg.get_json()["changed"]["simple_mode"] is True

        # 3. youtube → 400.
        sub_yt = client.post(
            f"/sing/submit?t={token}",
            json=_body(source_type="youtube", source_ref="https://youtu.be/y"),
        )
        assert sub_yt.status_code == 400
        assert sub_yt.get_json()["error"] == "simple_mode_disabled_source"

        # 4. make → 400.
        sub_mk = client.post(
            f"/sing/submit?t={token}",
            json=_body(source_type="make", source_ref=None),
        )
        assert sub_mk.status_code == 400
        assert sub_mk.get_json()["error"] == "simple_mode_disabled_source"

        # 5. kj_pick → 400.
        sub_kp = client.post(
            f"/sing/submit?t={token}",
            json=_body(
                source_type="kj_pick",
                source_ref=None,
                source_meta={
                    "versions": [
                        {"brand": "CC", "youtube_url": "https://youtu.be/a"},
                    ],
                },
            ),
        )
        assert sub_kp.status_code == 400
        assert sub_kp.get_json()["error"] == "simple_mode_disabled_source"

        # 6. local → 200.
        sub_lc = client.post(
            f"/sing/submit?t={token}",
            json=_body(source_type="local", source_ref="/library/t.mp4"),
        )
        assert sub_lc.status_code == 200

        # 7. Flip off; youtube works again.
        admin.post("/rotation/requests/config", json={"simple_mode": False})
        sub_yt2 = client.post(
            f"/sing/submit?t={token}",
            json=_body(source_type="youtube", source_ref="https://youtu.be/z"),
        )
        assert sub_yt2.status_code == 200

    def test_simple_mode_allows_divebar_and_kn(
        self, client, sing_app, token,
    ):
        sing_app.sing_store.set_simple_mode(True)
        # divebar
        sub_db = client.post(
            f"/sing/submit?t={token}",
            json=_body(
                source_type="divebar",
                source_ref="https://storage.googleapis.com/divebar/x.mp4",
            ),
        )
        assert sub_db.status_code == 200, sub_db.get_json()
        # kn
        sub_kn = client.post(
            f"/sing/submit?t={token}",
            json=_body(
                source_type="kn",
                source_ref="https://youtu.be/kn-track",
            ),
        )
        assert sub_kn.status_code == 200, sub_kn.get_json()
```

- [ ] **Step 2: Run the tests to verify they fail**

```
cd kj-controller && pytest tests/integration/test_sing_simple_mode_e2e.py -v
```

Expected: at least the "→ 400" assertions in step 3-5 fail because the allowlist isn't narrowed yet.

- [ ] **Step 3: Add the allowlist constant**

Edit `kj-controller/sing.py`. Find the existing `_ALLOWED_SOURCES = {"local", "divebar", "kn", "youtube", "make", "kj_pick"}` constant (around line 281). Add immediately after it:

```python
_SIMPLE_MODE_SOURCES = {"local", "divebar", "kn"}
```

- [ ] **Step 4: Add the gate in `submit()`**

In `kj-controller/sing.py`, find the `submit()` function. After the existing `_ALLOWED_SOURCES` validation (around line 540 — `if source_type not in _ALLOWED_SOURCES:`), add the simple-mode check **before** the `if source_type in {"local", "divebar", "kn", "youtube"} and not source_ref:` line:

```python
    if store.is_simple_mode() and source_type not in _SIMPLE_MODE_SOURCES:
        return jsonify({"error": "simple_mode_disabled_source"}), 400
```

- [ ] **Step 5: Run the tests to verify they pass**

```
cd kj-controller && pytest tests/integration/test_sing_simple_mode_e2e.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Run the full integration suite to catch regressions**

```
cd kj-controller && pytest tests/integration/ -v
```

Expected: all green. (If any test that previously assumed all sources were always allowed now fails, the test was carrying a hidden coupling — investigate before adjusting.)

- [ ] **Step 7: Commit**

```bash
git add kj-controller/sing.py kj-controller/tests/integration/test_sing_simple_mode_e2e.py
git commit -m "feat(sing): enforce simple_mode source allowlist on /sing/submit

When simple_mode is on, /sing/submit rejects source_type youtube, make,
and kj_pick with a 400 simple_mode_disabled_source. This is the
defence-in-depth layer for stale singer PWAs that haven't picked up the
flag from /sing/search yet."
```

---

## Task 6: `/sing/` and `/sing/search` expose `simple_mode` to the singer SPA

**Files:**
- Modify: `kj-controller/sing.py:346-384` (the `landing` handler — pass `simple_mode` to `render_template`)
- Modify: `kj-controller/sing.py:473-503` (the `search` handler — include `simple_mode` in the response)
- Modify: `kj-controller/templates/sing.html:52-56` (the `#sing-root` block — add `data-simple-mode`)
- Test: `kj-controller/tests/integration/test_sing_simple_mode_e2e.py` (extend the existing class)

- [ ] **Step 1: Write the failing tests**

Append to the `TestSimpleModeE2E` class in `kj-controller/tests/integration/test_sing_simple_mode_e2e.py`:

```python
    def test_landing_dataset_carries_simple_mode_flag(
        self, client, sing_app, token,
    ):
        """The landing template forwards the flag so singer JS has it before
        the first search."""
        # Default off
        resp = client.get(f"/sing/?t={token}")
        assert resp.status_code == 200
        assert b'data-simple-mode="0"' in resp.data
        # Flip on
        sing_app.sing_store.set_simple_mode(True)
        resp2 = client.get(f"/sing/?t={token}")
        assert b'data-simple-mode="1"' in resp2.data

    def test_search_response_includes_simple_mode(
        self, client, sing_app, token, monkeypatch,
    ):
        import karaoke_nerds
        monkeypatch.setattr(karaoke_nerds, "search", lambda *a, **kw: [])
        # Default off
        r1 = client.get(f"/sing/search?q=anything&t={token}")
        assert r1.status_code == 200
        assert r1.get_json()["simple_mode"] is False
        # Flip on
        sing_app.sing_store.set_simple_mode(True)
        r2 = client.get(f"/sing/search?q=anything&t={token}")
        assert r2.get_json()["simple_mode"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

```
cd kj-controller && pytest tests/integration/test_sing_simple_mode_e2e.py -v -k "dataset or search_response"
```

Expected: 2 failures — `data-simple-mode` not present; `simple_mode` not in search response.

- [ ] **Step 3: Pass `simple_mode` to the landing template**

In `kj-controller/sing.py`, find the `landing()` handler. The final `return render_template("sing.html", closed=False, ...)` call (around line 377) currently lists `make_requests_enabled=store.is_accepting_make_requests()`. Add one more kwarg:

```python
    return render_template(
        "sing.html",
        closed=False,
        token=token,
        request_id=request.args.get("r", ""),
        vapid_public_key=current_app.kj_config.get("vapid_public_key", ""),
        make_requests_enabled=store.is_accepting_make_requests(),
        simple_mode=store.is_simple_mode(),
    )
```

- [ ] **Step 4: Wire `data-simple-mode` into `sing.html`**

Edit `kj-controller/templates/sing.html`. Find the `<div id="sing-root"` element (around line 52). Change it from:

```html
<div id="sing-root"
     data-token="{{ token }}"
     data-request-id="{{ request_id }}"
     data-make-requests-enabled="{{ '1' if make_requests_enabled else '0' }}">
</div>
```

to:

```html
<div id="sing-root"
     data-token="{{ token }}"
     data-request-id="{{ request_id }}"
     data-make-requests-enabled="{{ '1' if make_requests_enabled else '0' }}"
     data-simple-mode="{{ '1' if simple_mode else '0' }}">
</div>
```

- [ ] **Step 5: Add `simple_mode` to `/sing/search` response**

In `kj-controller/sing.py`, find the `search()` handler. The `response` dict currently looks like:

```python
    response = {
        "songs": data["songs"],
        "make_requests_enabled": store.is_accepting_make_requests(),
    }
```

Add one key:

```python
    response = {
        "songs": data["songs"],
        "make_requests_enabled": store.is_accepting_make_requests(),
        "simple_mode": store.is_simple_mode(),
    }
```

- [ ] **Step 6: Run the tests to verify they pass**

```
cd kj-controller && pytest tests/integration/test_sing_simple_mode_e2e.py -v
```

Expected: 4 passed (the 2 from Task 5 + the 2 new ones).

- [ ] **Step 7: Commit**

```bash
git add kj-controller/sing.py kj-controller/templates/sing.html kj-controller/tests/integration/test_sing_simple_mode_e2e.py
git commit -m "feat(sing): expose simple_mode to singer SPA via landing & search

Landing template emits data-simple-mode on #sing-root for first-paint
awareness; /sing/search response echoes the flag so the SPA stays in
sync if the KJ flips it mid-session."
```

---

## Task 7: Singer SPA — read `simple_mode` and adapt the UI

**Files:**
- Modify: `kj-controller/static-sing/sing.js` (several touchpoints — listed below)

This task is frontend-only with no automated tests (the singer SPA has no JS test framework). Each step is one focused change; manual smoke happens at the end.

- [ ] **Step 1: Read `simple_mode` into the state on boot**

Open `kj-controller/static-sing/sing.js`. Find the `state` declaration (around line 60-80). It contains existing keys like `selected`, `makeRequestsEnabled`. Add `simpleMode: false` to the initial state object.

Then find where the state is hydrated from `#sing-root` dataset attributes (search for `dataset.makeRequestsEnabled` or `data-make-requests-enabled` usage). Beside the existing line that reads `makeRequestsEnabled`, add:

```javascript
state.simpleMode = root.dataset.simpleMode === "1";
```

- [ ] **Step 2: Keep state in sync on each search response**

Find the `/sing/search` fetch handler (search for `make_requests_enabled` — around line 525). Beside the existing:

```javascript
if (typeof data.make_requests_enabled === "boolean") {
  state.makeRequestsEnabled = data.make_requests_enabled;
}
```

Add:

```javascript
if (typeof data.simple_mode === "boolean") {
  state.simpleMode = data.simple_mode;
}
```

- [ ] **Step 3: Suppress the "paste YouTube URL" triage card in simple mode**

Find the empty-state triage block. The comment around line 783 says "ascending singer-effort: paste URL (fastest) → ask KJ (variable time, ...)". The paste-YouTube card is built around line 793-810. Wrap the entire card-construction block with a guard:

```javascript
// Card 1 — paste YouTube link. Hidden in Simple Mode (singers can't paste arbitrary URLs).
if (!state.simpleMode) {
  // ... existing card-1 construction ...
}
```

(Read the surrounding code carefully to find the right open/close braces — the card likely ends before "Card 2 —" or similar.)

- [ ] **Step 4: Suppress the "ask the KJ to make it" triage card**

Same file, around line 855-870. The card starts with the "Open gen.nomadkaraoke.com..." copy. Wrap it:

```javascript
// Card 2 — ask the KJ to make it on demand. Hidden in Simple Mode (no on-demand jobs).
if (!state.simpleMode && state.makeRequestsEnabled) {
  // ... existing card-2 construction ...
}
```

Note: the existing `state.makeRequestsEnabled` guard already gates this card. Simply **add** `!state.simpleMode &&` to the front of the condition.

- [ ] **Step 5: Suppress the `kj_pick` triage card**

Same file, around line 950 (search for the comment "search results now render a dedicated 3-card triage" or for `kj_pick` in the card-construction code). Wrap that card:

```javascript
// Card 3 — defer version to KJ. Hidden in Simple Mode (singer must pick a specific version).
if (!state.simpleMode) {
  // ... existing kj_pick card construction ...
}
```

- [ ] **Step 6: Hide per-result `kj_pick` chip on multi-version songs**

Search the file for other `kj_pick` references that aren't the card from Step 5 (look for lines around 573-590 where `source_type: "kj_pick"` is constructed for the per-song picker). Find the link/button that lets the singer choose "let the KJ pick" on a song with multiple versions. Add a guard around it:

```javascript
if (!state.simpleMode) {
  // ... existing per-result kj_pick chip / link ...
}
```

If the per-result kj_pick option is rendered inline alongside the versions list (rather than as a separate element you can `if`-guard), instead add early-return logic inside the click handler:

```javascript
if (state.simpleMode) return;
```

(Read the surrounding code to pick the cleaner of the two approaches. **Both are acceptable** — the goal is that in simple mode, multi-version songs do not offer a "defer to KJ" action.)

- [ ] **Step 7: Update the empty-results message in simple mode**

In the same empty-state code path (after the triage cards), the file falls back to some "no results" copy. After the cards are conditionally added, append a simple-mode-only fallback message. Find the block that constructs the empty-state UI (search for "no results", "empty", or the wrapper element where the cards are appended) and add:

```javascript
if (state.simpleMode) {
  // Replace the triage cards with a single line. In simple mode the singer
  // can't paste URLs or request on-demand jobs, so the only paths forward
  // are: try another search, or talk to the KJ.
  emptyStateRoot.innerHTML = "";  // (use the actual variable name from the surrounding code)
  emptyStateRoot.appendChild(
    el("p", { class: "sing-empty-message" },
      "We don't have that one. Try another search, or talk to the KJ at the front.")
  );
}
```

Adjust `emptyStateRoot` to whatever the surrounding code calls its container element (commonly `wrap`, `card`, or similar — read 2–3 lines above the place where the original cards are appended to find the name).

- [ ] **Step 8: Manual smoke (no automated test)**

On a local dev server (`cd kj-controller && python dev_server.py` or equivalent — check `docs/DEVELOPMENT.md`):

1. Hit `/sing/?t=<token>` with simple_mode off; search for a real song; confirm all 3 triage cards still appear when results are empty, and that multi-version songs still offer the kj_pick chip.
2. POST to `/rotation/requests/config` with `{simple_mode: true}` (curl or admin UI — admin UI not built yet, so curl):

   ```bash
   curl -X POST http://localhost:80/rotation/requests/config \
     -H 'Content-Type: application/json' \
     -d '{"simple_mode": true}'
   ```

3. Reload `/sing/?t=<token>`. Confirm:
   - Empty search results show only "We don't have that one..." (no triage cards).
   - Multi-version songs do **not** offer a "let the KJ pick" option (singer must select a specific version).
   - Submitting `local` / `divebar` / `kn` requests still works.

4. POST `simple_mode: false` and confirm everything returns.

- [ ] **Step 9: Commit**

```bash
git add kj-controller/static-sing/sing.js
git commit -m "feat(sing): hide YouTube/make/kj_pick paths in singer SPA simple mode

Reads simple_mode from #sing-root dataset and /sing/search responses.
When on: empty-state triage cards are suppressed, multi-version songs
don't offer the defer-to-KJ shortcut, and the no-results message is
trimmed to a single instructional line."
```

---

## Task 8: KJ UI — System "Mode" subsection in `templates/index.html`

**Files:**
- Modify: `kj-controller/templates/index.html:174-265` (the `<div class="container system-controls">` block — insert a new subsection at the top)

This task is frontend-only. Manual verification only.

- [ ] **Step 1: Insert the Mode subsection**

In `kj-controller/templates/index.html`, find the `<div class="container system-controls">` element (around line 174). Immediately after the opening `<h2>System</h2>` (line 175) and **before** the existing `<div class="system-subsection">` for Media & Output (line 177), insert:

```html
                <div class="system-subsection" id="kj-mode-section">
                    <div class="system-subsection-label">Mode</div>
                    <div class="system-subsection-row">
                        <div class="simple-mode-toggle">
                            <span>Simple Mode (for stand-in KJ)</span>
                            <label class="overlay-toggle">
                                <input type="checkbox" id="simple-mode-switch" onchange="toggleSimpleMode(this.checked)">
                                <span class="slider"></span>
                            </label>
                        </div>
                    </div>
                    <p class="simple-mode-hint">
                        Hides search panels, manual add, and advanced controls. Singers can only request from the local library, Divebar, or Karaoke Nerds.
                    </p>
                </div>
```

The indentation should match the surrounding subsection blocks (8 leading spaces for the outer `<div>`).

- [ ] **Step 2: Visual smoke**

Open the KJ UI in a browser (`http://nomadpc.local` or a local dev server). Confirm:
- A new "Mode" subsection appears at the top of the System panel.
- The toggle is clickable but does nothing yet (no JS handler — that's Task 10).
- The hint text wraps nicely on narrow viewports.

- [ ] **Step 3: Commit**

```bash
git add kj-controller/templates/index.html
git commit -m "feat(ui): add Simple Mode toggle to System section

Just the HTML; JS handler and CSS hide-rules in subsequent commits."
```

---

## Task 9: KJ UI — `style.css` simple-mode rules + banner styling

**Files:**
- Modify: `kj-controller/static/style.css` (append a new block at the bottom)

- [ ] **Step 1: Append the simple-mode block**

Append to `kj-controller/static/style.css`:

```css
/* ------------------------------------------------------------------
 * Simple Mode — stand-in KJ UI surface
 * Toggled via body.simple-mode (set by app.js from /status response).
 * Spec: docs/archive/2026-05-22-simple-kj-mode-design.md
 * ------------------------------------------------------------------ */

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
body.simple-mode #rotation-add-form,
body.simple-mode #np-pitch-group,
body.simple-mode .system-subsection:not(#kj-mode-section) {
    display: none !important;
}

body.simple-mode .main-layout {
    justify-content: center;
}

body.simple-mode #col1 {
    max-width: 720px;
    width: 100%;
}

/* Mode toggle styling — matches the existing autodeploy-toggle / sleep-mode-toggle look */
.simple-mode-toggle {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    width: 100%;
}

.simple-mode-hint {
    font-size: 0.85rem;
    color: var(--muted-text, #888);
    margin: 0.5rem 0 0 0;
    line-height: 1.4;
}

/* The always-on guidance banner above the rotation list */
.simple-mode-banner {
    background: rgba(255, 77, 207, 0.12);
    border: 1px solid rgba(255, 77, 207, 0.4);
    border-radius: 6px;
    padding: 0.6rem 0.8rem;
    margin: 0 0 0.6rem 0;
    color: #ffb3e6;
    font-size: 0.9rem;
    line-height: 1.4;
}
```

If `--muted-text` is not defined in this codebase, the fallback `#888` is used. Search the file for `--muted-text` to confirm; if absent, leave the fallback as the value (no harm — `var()` will fall back).

- [ ] **Step 2: Visual smoke**

In the browser devtools console, run:

```javascript
document.body.classList.add('simple-mode');
```

Confirm:
- Right column (`#col2`) disappears.
- Rotation header buttons trim to just Refresh, Requests (and any I missed — note them).
- System section collapses to just the Mode subsection.
- Manual + Add rotation form is hidden.
- Pitch buttons in the now-playing bar are hidden, but the rest of the bar (state/title/time/pause/fade/stop) stays.
- The `#col1` column centers and caps at 720px.

Then run:

```javascript
document.body.classList.remove('simple-mode');
```

Confirm everything returns. No JS errors in the console.

- [ ] **Step 3: Commit**

```bash
git add kj-controller/static/style.css
git commit -m "feat(ui): simple-mode CSS rules + Mode toggle styling

Single block at end of style.css. Hides col2 entirely, trims rotation
header, collapses System to just the Mode subsection, hides pitch
control in the now-playing bar. Also defines .simple-mode-banner for
the rotation guidance banner rendered by app.js."
```

---

## Task 10: KJ UI — `app.js` toggle handler + banner rendering

**Files:**
- Modify: `kj-controller/static/app.js` (several touchpoints — see steps)

- [ ] **Step 1: Add the `toggleSimpleMode` POST handler**

Open `kj-controller/static/app.js`. Find an existing handler that POSTs to `/rotation/requests/config` (search for `accept_make_requests` or `auto_approve`) and use it as a pattern. Append (or place near the other singer-config handlers):

```javascript
async function toggleSimpleMode(checked) {
    try {
        const resp = await fetch('/rotation/requests/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ simple_mode: !!checked }),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        // Apply optimistically; /status poll will reconcile in <=2s anyway.
        applySimpleMode(!!checked);
    } catch (e) {
        console.error('toggleSimpleMode failed:', e);
        // Revert the checkbox to reflect actual state on the next /status poll.
        // We don't try to restore the previous value here — the poll will catch up.
        alert('Could not change Simple Mode. Please try again.');
    }
}
```

- [ ] **Step 2: Add the `applySimpleMode` function**

In the same file, add:

```javascript
function applySimpleMode(on) {
    document.body.classList.toggle('simple-mode', !!on);
    // Keep the switch reflecting reality even if /status changed underfoot.
    const sw = document.getElementById('simple-mode-switch');
    if (sw) sw.checked = !!on;
    // Banner — render or remove based on flag.
    const rotationPanel = document.querySelector('.rotation-panel');
    let banner = document.getElementById('simple-mode-banner');
    if (on && rotationPanel && !banner) {
        banner = document.createElement('div');
        banner.id = 'simple-mode-banner';
        banner.className = 'simple-mode-banner';
        banner.textContent =
            'Simple Mode is ON · Approve incoming requests → tap a row to play → mark done → announce next singer.';
        // Insert before the rotation list (after the header).
        const list = document.getElementById('rotation-list');
        if (list) {
            rotationPanel.insertBefore(banner, list);
        } else {
            rotationPanel.appendChild(banner);
        }
    } else if (!on && banner) {
        banner.remove();
    }
}
```

- [ ] **Step 3: Read `simple_mode` from `/status` on each poll**

Find the `/status` poll handler in `app.js` (search for `fetch('/status')` or `'/status'` — there will be a function that updates UI from the status payload on every 2s poll). Inside that handler, after it processes other status fields, add:

```javascript
if (typeof data.simple_mode === 'boolean') {
    applySimpleMode(data.simple_mode);
}
```

- [ ] **Step 4: Manual smoke**

Reload the KJ UI. Confirm:
- Flipping the Mode toggle POSTs to `/rotation/requests/config` (check Network tab) and the body class flips.
- The rotation banner appears above the rotation list.
- Refresh the page — body class is restored from `/status` on the first poll (≤2s).
- Open the UI in a second browser tab; flip the toggle in tab A; tab B reflects the change within 2s.
- Flipping off removes the banner.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat(ui): wire Simple Mode toggle + status-poll body class

toggleSimpleMode POSTs the new flag; applySimpleMode adds/removes the
body class and the rotation banner. /status poll reconciles state every
tick, so multi-tab and remote toggles converge automatically."
```

---

## Task 11: End-to-end verification on a dev server

This task validates the integrated behavior across backend + both frontends with no production push.

- [ ] **Step 1: Start a local dev server**

From the worktree root:

```
cd kj-controller && python dev_server.py
```

(If `dev_server.py` doesn't exist or has a different invocation, check `docs/DEVELOPMENT.md` for the recommended local-run command. The Flask app factory is in `app.py:create_app`.)

- [ ] **Step 2: Walk the singer happy path (simple mode off)**

1. Open `http://localhost:<port>/sing/?t=<token>` on a second browser or device.
2. Search for any song. Confirm:
   - Multi-version results offer the kj_pick chip.
   - Empty results show all three triage cards (paste YT, ask KJ, kj_pick).

- [ ] **Step 3: Enable simple mode from the KJ UI**

1. In the KJ UI, flip the Mode toggle.
2. Confirm KJ UI collapses as expected (col2 hidden, system trimmed, banner present).
3. Reload the singer SPA. Confirm:
   - Empty search results show only the "We don't have that one..." message.
   - Multi-version songs no longer offer kj_pick.

- [ ] **Step 4: Verify server-side enforcement**

From a separate terminal, with simple_mode still on:

```bash
curl -X POST "http://localhost:<port>/sing/submit?t=<token>" \
  -H 'Content-Type: application/json' \
  -d '{"singer_name":"T","phone":"+1 555 0100","song_artist":"A","song_title":"T","source_type":"youtube","source_ref":"https://youtu.be/x"}'
```

Expected: HTTP 400 with `{"error": "simple_mode_disabled_source"}`.

- [ ] **Step 5: Disable simple mode and confirm full UI returns**

Flip the toggle off; confirm KJ UI restores; confirm singer SPA re-shows triage cards after a reload.

- [ ] **Step 6: Run the full pytest suite once more**

```
cd kj-controller && pytest -v
```

Expected: all green. If anything is red, fix before merging.

- [ ] **Step 7: Push (with explicit user approval)**

**Stop here.** Do not push without explicit user permission — auto-deploy runs on NomadPC and this branch contains template/CSS/JS/Python changes.

When the user says push:

```bash
git push -u origin feat/sess-20260522-0428-simple-kj-mode
```

Then open a PR via the standard `/pr` workflow (don't push directly to main).

---

## Spec coverage check

| Spec section                                  | Implemented by |
|-----------------------------------------------|----------------|
| Data model — `kj_simple_mode` meta key        | Task 1         |
| `GET /rotation/requests/config` includes flag | Task 2         |
| `POST /rotation/requests/config` accepts flag | Task 3         |
| `GET /status` includes flag                   | Task 4         |
| `POST /sing/submit` narrows allowlist         | Task 5         |
| `GET /sing/` template context                 | Task 6         |
| `GET /sing/search` response payload           | Task 6         |
| `templates/sing.html` `data-simple-mode`      | Task 6         |
| Singer SPA hides triage cards                 | Task 7         |
| Singer SPA hides per-result kj_pick           | Task 7         |
| Singer SPA empty-state message                | Task 7         |
| System Mode subsection in `index.html`        | Task 8         |
| `style.css` simple-mode hide rules            | Task 9         |
| `.simple-mode-banner` styling                 | Task 9         |
| `app.js` `toggleSimpleMode` POST              | Task 10        |
| `app.js` body class + banner from `/status`   | Task 10        |
| Backend tests for store + routes              | Tasks 1, 2, 3, 4, 5, 6 |
| E2E singer-side test                          | Tasks 5, 6     |
| Frontend manual smoke                         | Tasks 7, 8, 9, 10, 11 |
| Stale-PWA edge case                           | Task 5 (server returns 400 simple_mode_disabled_source) |
| Mid-show toggle convergence                   | Task 10 step 4 (multi-tab smoke) |

No spec section is uncovered.
