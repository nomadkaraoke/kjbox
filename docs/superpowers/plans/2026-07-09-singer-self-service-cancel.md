# Singer Self-Service — Ownership + Cancel (PR #2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a singer cancel their own request from their phone — instantly for a not-yet-approved request, and as a KJ-visible "soft cancel" for one already in the rotation — proven by a per-request ownership secret.

**Architecture:** Adds a per-request `edit_token` secret (minted at creation, returned once from `/sing/submit`, stored in the device's `localStorage`). A new token-gated `POST /sing/requests/<id>/cancel` endpoint verifies `(event token + edit_token + night-scope)` and either marks a pending request `cancelled` (nothing downstream) or, for an approved request, sets its linked rotation entry to a new visible `Cancelled` status (via `RotationManager` so `rev`/push/display-cache all fire) and marks the request `cancelled`. The KJ requests panel drops it automatically (it polls `status=pending`); the KJ rotation shows the `Cancelled` entry distinctly with dismiss (delete) / restore (→Waiting) — both reuse existing endpoints.

**Tech Stack:** Flask + vanilla JS (no build step), SQLite (`rotation.db`), pytest (unit/integration) + Playwright (e2e). Backend change → **requires a service restart to deploy** (interrupts playback): build/test/PR now, deploy only in a maintenance window.

## Global Constraints

- **Backend deploy = restart.** Do NOT push/deploy to the live NomadPC without an explicit maintenance-window go-ahead. Building, testing, and opening the PR are safe.
- **`import secrets` already exists** in `sing_store.py:9` — reuse it; do not re-import.
- **sing_store migration idiom** (match exactly): one `ALTER TABLE ... ADD COLUMN` per column wrapped in `try/except sqlite3.OperationalError` that re-raises unless `"duplicate column name" in str(e).lower()` (see `sing_store.py:150-159`).
- **`SELECT *`** in `get_request`/`list_requests` means new columns auto-surface through `_row_to_dict`.
- **Never add `edit_token` to `_public_request_view`** (`sing.py:889`) — it feeds `/my-requests` and `/status`. Expose it ONLY in the single `/submit` response.
- **Ownership guard for mutations:** copy the inline guard from `status()` (`sing.py:765-778`): valid event token, `req["token"] == token`, and `_belongs_to_current_night(store, req)`. Additionally require the `edit_token` to match using `secrets.compare_digest` (constant-time). Missing/mismatch → `403`.
- **Concurrency guard idiom:** only act on the expected status; return `409` otherwise (mirrors `approve_sing_request_route`, `routes.py:5089`).
- **Rotation mutations go through `RotationManager`** (e.g. `current_app.rotation.update_status(...)`), never `store.*` directly, so `_after_mutation` fires (`rev` bump + push + display cache).
- **Rotation `Cancelled` status is free-text**, stays visible: `get_entries()` only excludes `done`/`left` (`rotation_store.py:287-303`), so `Cancelled` shows by default — do NOT add it to that exclusion.
- **Frontend cache-bust:** bump `pyproject.toml` version in this PR.
- **Run tests:** `cd kj-controller && python -m pytest <path>` (this environment: prefix with `rtk proxy` to see raw output).
- **Commit trailer:** `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: `edit_token` column + minting in `create_request`

**Files:**
- Modify: `kj-controller/sing_store.py` — `init_schema` (~150-159), `create_request` (346-397)
- Test: `kj-controller/tests/unit/test_sing_store.py`

**Interfaces:**
- Produces: every `create_request(...)` result dict now has a non-empty `edit_token` (str, `secrets.token_urlsafe(16)`); the column persists and round-trips via `get_request`/`_row_to_dict`.

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_sing_store.py`:

```python
def test_create_request_mints_unique_edit_token(sing_store):
    a = sing_store.create_request(singer_name="Alice", phone="", source_type="local", source_ref="/a.mp4")
    b = sing_store.create_request(singer_name="Bob", phone="", source_type="local", source_ref="/b.mp4")
    assert a["edit_token"] and isinstance(a["edit_token"], str)
    assert len(a["edit_token"]) >= 16
    assert a["edit_token"] != b["edit_token"]
    # Round-trips through get_request unchanged.
    assert sing_store.get_request(a["id"])["edit_token"] == a["edit_token"]
```

(Use the existing `sing_store` fixture in that file; if the fixture has a different name, match it — check the top of `test_sing_store.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && python -m pytest tests/unit/test_sing_store.py::test_create_request_mints_unique_edit_token -v`
Expected: FAIL — `KeyError: 'edit_token'` (column doesn't exist).

- [ ] **Step 3: Add the migration** — in `init_schema`, right after the existing `additional_singers` ALTER block (`sing_store.py:159`, before `conn.commit()`), add:

```python
        # Additive migration — `edit_token` (2026-07-09) is a per-request secret
        # proving device ownership for singer self-service (cancel/edit).
        try:
            conn.execute(
                "ALTER TABLE sing_requests ADD COLUMN edit_token TEXT DEFAULT NULL"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
```

- [ ] **Step 4: Mint the token in `create_request`** — add the column to the INSERT. Replace the INSERT statement + params in `create_request` (`sing_store.py:375-395`):

```python
        edit_token = secrets.token_urlsafe(16)
        conn = self._get_conn()
        cur = conn.execute(
            """
            INSERT INTO sing_requests
                (token, singer_name, phone, song_artist, song_title,
                 source_type, source_ref, source_meta, notes,
                 additional_singers, edit_token)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_token,
                singer_name.strip(),
                phone.strip(),
                song_artist.strip(),
                song_title.strip(),
                source_type,
                source_ref,
                meta_json,
                notes.strip(),
                partners_json,
                edit_token,
            ),
        )
        conn.commit()
        return self.get_request(cur.lastrowid)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd kj-controller && python -m pytest tests/unit/test_sing_store.py::test_create_request_mints_unique_edit_token -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/sing_store.py kj-controller/tests/unit/test_sing_store.py
git commit -m "feat(sing): mint per-request edit_token for ownership

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `mark_cancelled` store method

**Files:**
- Modify: `kj-controller/sing_store.py` — after `mark_rejected` (~561)
- Test: `kj-controller/tests/unit/test_sing_store.py`

**Interfaces:**
- Produces: `SingStore.mark_cancelled(request_id) -> dict` — sets `status='cancelled'`, `reviewed_at=now`; raises `ValueError` if not found. Mirrors `mark_rejected`.

- [ ] **Step 1: Write the failing test**

```python
def test_mark_cancelled_sets_status(sing_store):
    r = sing_store.create_request(singer_name="Alice", phone="", source_type="local", source_ref="/a.mp4")
    out = sing_store.mark_cancelled(r["id"])
    assert out["status"] == "cancelled"
    assert out["reviewed_at"]
    import pytest
    with pytest.raises(ValueError):
        sing_store.mark_cancelled(999999)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && python -m pytest tests/unit/test_sing_store.py::test_mark_cancelled_sets_status -v`
Expected: FAIL — `AttributeError: 'SingStore' object has no attribute 'mark_cancelled'`.

- [ ] **Step 3: Implement `mark_cancelled`** — add after `mark_rejected` (`sing_store.py:561`):

```python
    def mark_cancelled(self, request_id):
        """Set status=cancelled, reviewed_at=now. Raises ValueError if not found."""
        if self.get_request(request_id) is None:
            raise ValueError(f"Request {request_id} not found")
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE sing_requests
               SET status = 'cancelled',
                   reviewed_at = datetime('now', 'localtime')
             WHERE id = ?
            """,
            (request_id,),
        )
        conn.commit()
        return self.get_request(request_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kj-controller && python -m pytest tests/unit/test_sing_store.py::test_mark_cancelled_sets_status -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/sing_store.py kj-controller/tests/unit/test_sing_store.py
git commit -m "feat(sing): add mark_cancelled store method

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Return `edit_token` in the `/sing/submit` response

**Files:**
- Modify: `kj-controller/sing.py` — `submit()` return (607-612)
- Test: `kj-controller/tests/integration/test_sing_public_routes.py`

**Interfaces:**
- Consumes: `req["edit_token"]` (Task 1).
- Produces: `POST /sing/submit` response `request` object additionally contains `edit_token`. `_public_request_view` is unchanged (so `/my-requests` and `/status` still omit it).

- [ ] **Step 1: Write the failing test** — append to `tests/integration/test_sing_public_routes.py` (match the existing client/token fixtures in that file):

```python
def test_submit_returns_edit_token_but_status_does_not(client, enabled_token):
    resp = client.post("/sing/submit", json={
        "t": enabled_token, "singer_name": "Alice",
        "source_type": "local", "source_ref": "/x.mp4",
        "song_artist": "Queen", "song_title": "Bo Rhap",
    })
    assert resp.status_code == 200
    req = resp.get_json()["request"]
    assert req["edit_token"]
    # The status endpoint must NOT leak the edit_token.
    st = client.get(f"/sing/status/{req['id']}?t={enabled_token}")
    assert "edit_token" not in st.get_json()["request"]
```

(If the fixtures are named differently, adapt — read the top of the file. The point: submit returns `edit_token`, status does not.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && python -m pytest tests/integration/test_sing_public_routes.py::test_submit_returns_edit_token_but_status_does_not -v`
Expected: FAIL — `edit_token` missing from submit response.

- [ ] **Step 3: Add `edit_token` to the submit response only** — replace the `return jsonify(...)` at the end of `submit()` (`sing.py:607-612`):

```python
    return jsonify(
        {
            # edit_token is returned ONCE here so the submitting device can store
            # it for self-service (cancel/edit). It is intentionally absent from
            # _public_request_view (used by /my-requests and /status).
            "request": {**_public_request_view(req), "edit_token": req.get("edit_token")},
            "auto_approved": auto_approved,
        }
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kj-controller && python -m pytest tests/integration/test_sing_public_routes.py::test_submit_returns_edit_token_but_status_does_not -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/sing.py kj-controller/tests/integration/test_sing_public_routes.py
git commit -m "feat(sing): return edit_token once from /submit (not in status/my-requests)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `POST /sing/requests/<id>/cancel` endpoint

**Files:**
- Modify: `kj-controller/sing.py` — add route after `my_requests` (~846); reuse `_client_ip`, `_rate_limit_exceeded`, `_extract_token`, `_is_token_valid`, `_belongs_to_current_night`
- Test: `kj-controller/tests/integration/test_sing_public_routes.py`

**Interfaces:**
- Consumes: `store.get_request`, `store.mark_cancelled` (Task 2), `current_app.rotation.update_status(entry_id, "Cancelled")`.
- Produces: `POST /sing/requests/<int:req_id>/cancel` with JSON `{t, edit_token}`. Returns `{"success": true, "request": <public view>}`. Guards: bad/absent event token → `403 not_open`; unknown/foreign/prior-night req → `404`; missing/mismatched `edit_token` → `403 forbidden`; already-terminal (`cancelled`/`rejected`) → `409`. For an approved request with a `linked_entry_id`, sets that rotation entry's status to `Cancelled` before marking the request cancelled.

- [ ] **Step 1: Write the failing tests**

```python
def test_cancel_pending_requires_matching_edit_token(client, enabled_token):
    r = client.post("/sing/submit", json={
        "t": enabled_token, "singer_name": "Alice",
        "source_type": "local", "source_ref": "/x.mp4",
        "song_artist": "Q", "song_title": "BR"}).get_json()["request"]
    # Wrong token → 403, request untouched.
    bad = client.post(f"/sing/requests/{r['id']}/cancel",
                      json={"t": enabled_token, "edit_token": "nope"})
    assert bad.status_code == 403
    assert client.get(f"/sing/status/{r['id']}?t={enabled_token}").get_json()["request"]["status"] == "pending"
    # Correct token → cancelled.
    ok = client.post(f"/sing/requests/{r['id']}/cancel",
                     json={"t": enabled_token, "edit_token": r["edit_token"]})
    assert ok.status_code == 200
    assert ok.get_json()["request"]["status"] == "cancelled"

def test_cancel_is_idempotent_409_when_already_cancelled(client, enabled_token):
    r = client.post("/sing/submit", json={
        "t": enabled_token, "singer_name": "Alice",
        "source_type": "local", "source_ref": "/x.mp4",
        "song_artist": "Q", "song_title": "BR"}).get_json()["request"]
    client.post(f"/sing/requests/{r['id']}/cancel", json={"t": enabled_token, "edit_token": r["edit_token"]})
    again = client.post(f"/sing/requests/{r['id']}/cancel", json={"t": enabled_token, "edit_token": r["edit_token"]})
    assert again.status_code == 409
```

(If the integration harness has a rotation configured, add a test that an approved request's linked entry becomes `Cancelled`. If not, cover that in Step 5 manually and note it — do not fake a rotation.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd kj-controller && python -m pytest tests/integration/test_sing_public_routes.py -k cancel -v`
Expected: FAIL — route 404 (doesn't exist yet).

- [ ] **Step 3: Implement the endpoint** — add to `sing.py` (after `my_requests`, ~line 846), importing `secrets` at top if not already imported there (it's imported in `sing_store`, not `sing.py` — add `import secrets` to `sing.py`'s imports):

```python
@sing_bp.route("/requests/<int:req_id>/cancel", methods=["POST"])
def cancel_request(req_id):
    """Singer cancels their own request (proven by the per-request edit_token).

    Pending → marked cancelled (nothing downstream). Approved → the linked
    rotation entry is set to 'Cancelled' (visible to the KJ, excluded from the
    active queue selection) and the request is marked cancelled. The KJ can
    dismiss (delete) or restore (→ Waiting) from the rotation row.
    """
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return jsonify({"error": "not_configured"}), 503

    cfg = current_app.kj_config
    limit = _safe_int(cfg.get("sing_rate_limit_per_ip"), 5)
    window = _safe_int(cfg.get("sing_rate_limit_window_s"), 300)
    if _rate_limit_exceeded(_client_ip(request), limit, window):
        return jsonify({"error": "rate_limited"}), 429

    token = _extract_token()
    if not token or not _is_token_valid(store, token):
        return jsonify({"error": "not_open"}), 403

    req = store.get_request(req_id)
    # Night-scope + event-token match (mirror status()): a prior-night or
    # foreign request must be indistinguishable from a missing one.
    if req is None or req.get("token") != token or not _belongs_to_current_night(store, req):
        return jsonify({"error": "not_found"}), 404

    data = request.get_json(silent=True) or {}
    provided = (data.get("edit_token") or "")
    stored = (req.get("edit_token") or "")
    # Constant-time compare; empty stored token (legacy rows) can never match.
    if not stored or not secrets.compare_digest(str(provided), str(stored)):
        return jsonify({"error": "forbidden"}), 403

    if req["status"] in ("cancelled", "rejected"):
        return jsonify({"error": f"already {req['status']}"}), 409

    # If it reached the rotation, soft-cancel the linked entry (visible to KJ).
    if req["status"] == "approved" and req.get("linked_entry_id"):
        rotation = getattr(current_app, "rotation", None)
        if rotation is not None:
            try:
                rotation.update_status(req["linked_entry_id"], "Cancelled")
            except Exception:
                current_app.logger.exception("cancel: failed to soft-cancel entry")

    store.mark_cancelled(req_id)
    return jsonify({"success": True, "request": _public_request_view(store.get_request(req_id))})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd kj-controller && python -m pytest tests/integration/test_sing_public_routes.py -k cancel -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/sing.py kj-controller/tests/integration/test_sing_public_routes.py
git commit -m "feat(sing): singer cancel endpoint (edit_token-gated, soft-cancel if approved)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Verify a `Cancelled` entry is excluded from active-queue selection

Confirm the soft-cancel actually keeps a `Cancelled` entry out of Now Singing / Up Next auto-selection (the spec's "excluded from being called up"), and that it stays visible in `get_rotation`.

**Files:**
- Read/verify: `kj-controller/rotation.py` (mark_up_next / "next" selection), `rotation_store.py` `get_entries` (287-303)
- Modify (only if needed): the "up next" selection to filter to `Waiting`
- Test: `kj-controller/tests/unit/` (rotation store/manager test file — match existing naming)

**Interfaces:**
- Produces: a guarantee (with a test) that `get_rotation()` includes a `Cancelled` entry, and that whatever auto-selects "up next" does not pick a `Cancelled` entry.

- [ ] **Step 1: Investigate** — read how "up next"/next is chosen.

Run: `cd kj-controller && rtk proxy grep -n "def mark_up_next\|def mark_singing\|Up Next\|def get_next\|def advance" rotation.py rotation_store.py`
Determine whether next-selection filters by `status='Waiting'`. Two cases:
  - **It already filters to `Waiting`** (most likely): no code change — write the confirming test in Step 2.
  - **It picks by position regardless of status:** add a `WHERE LOWER(status) = 'waiting'` (or equivalent) filter so `Cancelled`/`Skipped`/`On Hold` are skipped.

- [ ] **Step 2: Write the test** (adjust to the actual manager/store API discovered in Step 1):

```python
def test_cancelled_entry_is_visible_but_not_auto_up_next(rotation):
    a = rotation.add_entry("Alice", "Song A")
    b = rotation.add_entry("Bob", "Song B")
    rotation.update_status(a["id"], "Cancelled")
    ids = [e["id"] for e in rotation.get_rotation()]
    assert a["id"] in ids          # still visible to the KJ
    # Auto "up next" must skip the cancelled entry and choose Bob.
    # (Use whatever the real next-selection call is; assert it is NOT a['id'].)
```

- [ ] **Step 3: Run the test**

Run: `cd kj-controller && python -m pytest tests/unit -k "cancelled and up_next" -v`
Expected: FAIL first if a filter is needed; PASS after adding it (or PASS immediately if already filtered — then this task is verification-only, keep the test as a regression guard).

- [ ] **Step 4: Commit**

```bash
git add kj-controller/rotation.py kj-controller/rotation_store.py kj-controller/tests/unit/
git commit -m "test(rotation): cancelled entries stay visible but are skipped for up-next

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

(If no source change was needed, commit just the test with message `test(rotation): guard that Cancelled entries aren't auto-selected as up-next`.)

---

### Task 6: KJ rotation UI — render `Cancelled` distinctly + one-tap dismiss

**Files:**
- Modify: `kj-controller/static/app.js` — status-class ladder (`5233-5242`), badge ladder (`5409-5436`), and the per-entry actions to add a dismiss button on cancelled rows; `allStatuses` dropdown (`5626`) to allow manual restore
- Modify: `kj-controller/static/style.css` — `.rotation-cancelled` + `.badge-cancelled`
- Test: `kj-controller/tests/e2e/` (KJ rotation e2e — match existing naming, e.g. `test_rotation_*` )

**Interfaces:**
- Consumes: rotation entries with `status === 'Cancelled'`.
- Produces: a `Cancelled` row renders with class `rotation-cancelled` + a `CANCELLED` badge; a "Dismiss" button POSTs `/rotation/delete`; "Waiting" in the "…" dropdown restores it (existing `updateRotationStatus`).

- [ ] **Step 1: Write the failing e2e test** — in the KJ rotation e2e file (find it: `rtk proxy grep -rl "rotation-entry" tests/e2e`), drive a cancelled entry (seed via the rotation API or `window` bridge the file already uses) and assert:

```python
    # After seeding an entry with status 'Cancelled':
    expect(page.locator(".rotation-entry.rotation-cancelled")).to_be_visible()
    expect(page.locator(".rotation-cancelled .badge-cancelled")).to_have_text("CANCELLED")
    expect(page.locator(".rotation-cancelled .rotation-btn-dismiss")).to_be_visible()
```

(Match the existing KJ e2e seeding pattern in that file — do not invent a new harness.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && python -m pytest <that file> -k cancel -v`
Expected: FAIL — no `.rotation-cancelled` styling/badge/button.

- [ ] **Step 3: Add the status class** — in `renderRotation`'s status-class ladder (`app.js:5233-5242`), add a branch:

```javascript
        } else if (statusLower === 'cancelled') {
            row.classList.add('rotation-cancelled');
```

- [ ] **Step 4: Add the badge** — in the badge ladder (`app.js:5409-5436`), add:

```javascript
        } else if (statusLower === 'cancelled') {
            badge.textContent = 'CANCELLED';
            badge.classList.add('badge-cancelled');
            badge.title = 'Cancelled by the singer — dismiss to remove or set Waiting to restore';
```

- [ ] **Step 5: Add a Dismiss button on cancelled rows** — in the actions-building section (near the "…" more button, `app.js:~5617`), before appending `moreBtn`, add:

```javascript
        if (statusLower === 'cancelled') {
            const dismissBtn = document.createElement('button');
            dismissBtn.className = 'rotation-btn rotation-btn-dismiss';
            dismissBtn.textContent = 'Dismiss';
            dismissBtn.title = 'Remove this cancelled singer from the rotation';
            dismissBtn.onclick = (e) => {
                e.stopPropagation();
                if (!confirm('Remove this cancelled singer from the rotation?')) return;
                deleteRotationEntry(entry.id);   // existing helper → POST /rotation/delete
            };
            actions.appendChild(dismissBtn);
        }
```

Verify the existing delete helper's name: `rtk proxy grep -n "rotation/delete\|function deleteRotationEntry" static/app.js` and use the actual function (if it's inline, call it the same way the edit-mode delete does).

- [ ] **Step 6: Allow manual restore** — add `'Cancelled'` is NOT needed in `allStatuses` (the KJ doesn't set it); restore uses the existing `'Waiting'` entry already in `allStatuses` (`app.js:5626`). No change needed — confirm `'Waiting'` is present.

- [ ] **Step 7: Add styles** — append to `kj-controller/static/style.css`:

```css
.rotation-entry.rotation-cancelled { opacity: 0.6; }
.rotation-entry.rotation-cancelled .rotation-song,
.rotation-entry.rotation-cancelled .rotation-singer { text-decoration: line-through; }
.rotation-badge.badge-cancelled { background: #7a2e0b; color: #ffd6a2; }
.rotation-btn-dismiss { border-color: #7a2e0b; }
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd kj-controller && python -m pytest <that file> -k cancel -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add kj-controller/static/app.js kj-controller/static/style.css kj-controller/tests/e2e/
git commit -m "feat(kj): show singer-cancelled entries distinctly with a dismiss button

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Singer UI — store `edit_token` + Cancel control on the done screen

**Files:**
- Modify: `kj-controller/static-sing/sing.js` — `rememberRequestId` + store shape (27-65), submit caller (~1119-1123), `_renderSongCard` (1259-1273)
- Modify: `kj-controller/static-sing/sing.css` — cancel button styles
- Test: `kj-controller/tests/e2e/test_sing_frontend.py`

**Interfaces:**
- Consumes: `data.request.edit_token` from `/submit` (Task 3); `/sing/requests/<id>/cancel` (Task 4).
- Produces: `localStorage` `sing_my_request_ids` gains `tokens: {[id]: edit_token}`; `readEditToken(token, id)` helper; `_renderSongCard` shows a "Cancel this song" button for cancellable statuses (`pending`/`approved`) when the device holds the edit_token; clicking it confirms, POSTs cancel, and refreshes.

- [ ] **Step 1: Write the failing test** — append to `tests/e2e/test_sing_frontend.py`:

```python
class TestSelfServiceCancel:
    def test_cancel_button_shows_and_cancels(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        # Seed a stored request + edit_token, land on done screen.
        page.evaluate("""(t) => {
            localStorage.setItem('sing_my_request_ids',
              JSON.stringify({token: t, ids: [4242], tokens: {'4242': 'secret-xyz'}}));
        }""", live_token)
        captured = {}
        def handle_my(route):
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "now_playing": {"now_singing": None, "up_next": None, "queued_count": 0},
                "requests": [{"request": {"id": 4242, "singer_name": "Alice",
                    "song_artist": "Queen", "song_title": "Bo Rhap",
                    "source_type": "local", "status": "pending",
                    "created_at": "now", "linked_entry_id": None,
                    "additional_singers": None}}]}))
        page.route("**/sing/my-requests*", handle_my)
        def handle_cancel(route):
            captured["hit"] = route.request.post_data
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True, "request": {"id": 4242, "status": "cancelled"}}))
        page.route("**/sing/requests/4242/cancel", handle_cancel)
        page.on("dialog", lambda d: d.accept())
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")
        expect(page.locator('[data-testid="cancel-song"]')).to_be_visible()
        page.locator('[data-testid="cancel-song"]').click()
        page.wait_for_function("() => window.__lastCancelPostData !== undefined")
        assert "secret-xyz" in page.evaluate("() => window.__lastCancelPostData")
```

To make the edit_token observable, have the cancel handler stash the POST body on `window.__lastCancelPostData` inside `sing.js` right before the fetch (a one-line test aid, or assert via `captured` using `page.expect_request`). Prefer `page.expect_request`:

```python
        with page.expect_request("**/sing/requests/4242/cancel") as req_info:
            page.locator('[data-testid="cancel-song"]').click()
        assert "secret-xyz" in (req_info.value.post_data or "")
```

(Use the `expect_request` form — no production test-aid needed.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kj-controller && python -m pytest tests/e2e/test_sing_frontend.py::TestSelfServiceCancel -v`
Expected: FAIL — no `[data-testid="cancel-song"]`.

- [ ] **Step 3: Extend the localStorage store to hold edit tokens** — update `rememberRequestId` and add `readEditToken` (`sing.js:52-65`):

```javascript
function rememberRequestId(token, id, editToken) {
  if (!token || !id) return;
  let store = _readMyRequestStore();
  if (!store || store.token !== token) store = { token, ids: [], tokens: {} };
  if (!store.tokens) store.tokens = {};
  if (!store.ids.includes(id)) store.ids.push(id);
  if (editToken) store.tokens[String(id)] = editToken;
  try { localStorage.setItem(MY_REQUESTS_KEY, JSON.stringify(store)); }
  catch { /* private browsing — best-effort */ }
}

function readEditToken(token, id) {
  const store = _readMyRequestStore();
  if (!store || store.token !== token || !store.tokens) return "";
  return store.tokens[String(id)] || "";
}
```

- [ ] **Step 4: Capture the edit_token at submit** — at the submit caller (`sing.js:~1122`), pass it through:

```javascript
      const data = await submit(payload);
      state.request = data.request;
      rememberRequestId(TOKEN, data.request.id, data.request.edit_token);
```

- [ ] **Step 5: Add the Cancel control to `_renderSongCard`** — extend the card (`sing.js:1259-1273`) so a cancellable, owned request gets a Cancel button:

```javascript
function _renderSongCard(item) {
  const req = item.request;
  const song = (req.song_title || "") + (req.song_artist ? ` — ${req.song_artist}` : "");
  const partners = req.additional_singers || [];
  const card = el("div", { class: "song-card", "data-status": req.status },
    el("div", { class: "song-card-title" }, song || "(song)"),
    el("div", { class: "song-card-status" }, _statusLine(item)),
  );
  if (partners.length > 0) {
    const names = partners.map((p) => p.name).join(", ");
    card.appendChild(el("div", { class: "song-card-partners" }, `with ${names}`));
  }
  const editToken = readEditToken(TOKEN, req.id);
  const cancellable = editToken && (req.status === "pending" || req.status === "approved");
  if (cancellable) {
    card.appendChild(el("button", {
      class: "btn ghost song-card-cancel",
      "data-testid": "cancel-song",
      onclick: async (e) => {
        e.stopPropagation();
        if (!confirm(`Cancel "${song}"? The KJ will see it's cancelled.`)) return;
        e.target.disabled = true;
        try {
          const resp = await fetch(`${BASE}/requests/${req.id}/cancel`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            body: JSON.stringify({ t: TOKEN, edit_token: editToken }),
          });
          if (!resp.ok) { e.target.disabled = false; alert("Couldn't cancel — see the KJ."); return; }
        } catch { e.target.disabled = false; alert("Couldn't cancel — check your connection."); return; }
        // Let the 15s poll refresh; nudge an immediate re-render.
        if (typeof window.__sing_render === "function") window.__sing_render();
      },
    }, "Cancel this song"));
  }
  return card;
}
```

- [ ] **Step 6: Add styles** — append to `kj-controller/static-sing/sing.css`:

```css
.song-card-cancel { margin-top: 8px; font-size: 0.85rem; opacity: 0.85; }
.song-card[data-status="cancelled"] .song-card-title { text-decoration: line-through; opacity: 0.6; }
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd kj-controller && python -m pytest tests/e2e/test_sing_frontend.py::TestSelfServiceCancel -v`
Expected: PASS.

- [ ] **Step 8: Run the whole singer e2e file (regression)**

Run: `cd kj-controller && python -m pytest tests/e2e/test_sing_frontend.py -v`
Expected: all PASS (the extra `rememberRequestId` arg is backward-compatible — existing single-arg callers still work).

- [ ] **Step 9: Commit**

```bash
git add kj-controller/static-sing/sing.js kj-controller/static-sing/sing.css kj-controller/tests/e2e/test_sing_frontend.py
git commit -m "feat(sing): Cancel-this-song control on the done screen (edit_token-gated)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Version bump

**Files:**
- Modify: `kj-controller/pyproject.toml`

- [ ] **Step 1: Bump the minor version** — `0.76.0` → `0.77.0` (new user-facing feature).

- [ ] **Step 2: Commit**

```bash
git add kj-controller/pyproject.toml
git commit -m "chore(sing): bump version to 0.77.0 for self-service cancel

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage:** ownership `edit_token` (Tasks 1,3,7); `mark_cancelled` (2); cancel endpoint w/ soft-cancel + guards (4); `Cancelled` visible-but-not-auto-selected (5); KJ dismiss/restore visual (6); singer Cancel control (7); version (8). Edit-song + reorder are deferred to PR #2b (out of scope here).
- **Type consistency:** `mark_cancelled(request_id)` (Task 2) used by the endpoint (Task 4). `rememberRequestId(token, id, editToken)` (Task 7 Step 3) — the third arg is optional so the existing `?r=` bootstrap caller (`sing.js:1662`) still compiles; update that call only if it errs. `readEditToken(token, id)` defined + used in Task 7. Cancel endpoint path `/sing/requests/<id>/cancel` matches the singer fetch in Task 7 Step 5 and the KJ never calls it.
- **Security:** `edit_token` never enters `_public_request_view`; constant-time compare; empty stored token can't match; night-scope + event-token guard mirrors `status()`.
- **Deploy:** backend change → restart required; hold for a maintenance window. Reuses existing `/rotation/delete` + `/rotation/status` for KJ dismiss/restore (no new KJ endpoints).
