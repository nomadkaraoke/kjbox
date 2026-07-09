# Singer Self-Service — Edit-song + Reorder-your-own (PR #2b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let a singer change the song of their own request, and reorder their own songs — both routed through KJ approval — building on PR #2a's `edit_token` ownership.

**Architecture:** Reuses the existing pending → approve/reject pipeline. **Edit** a still-pending request updates it in place; **edit** an approved request creates a new *pending* request carrying `supersedes_request_id` — on KJ approval it rides the normal `approve_sing_request` machinery (download etc.), then takes over the original entry's rotation slot and removes the original. **Reorder** creates a pending request with `source_type='reorder'` and the desired `ordered_entry_ids` in `source_meta`; on KJ approval it applies `move_entry`. The KJ approves both from the existing requests panel. All singer endpoints require the per-request `edit_token`.

**Tech Stack:** Flask + vanilla JS, SQLite, pytest + Playwright. Backend change → **deploy needs a restart** (maintenance window).

## Global Constraints

- Builds on PR #2a (already merged/deployed): `sing_requests.edit_token`, `mark_cancelled`, `POST /sing/requests/<id>/cancel`, `Cancelled` rotation status. Base = current `origin/main` (v0.78.0).
- **Ownership guard** (reuse the 2a pattern from `cancel_request`): valid event token + `req["token"] == token` + `_belongs_to_current_night` + `secrets.compare_digest(edit_token)`; empty stored token never authorizes.
- **`_public_request_view` must not gain `edit_token`**; the admin `/rotation/requests` list already projects it out (2a) — `reorder`/supersede requests flow through the same list, so no new leak.
- Rotation mutations go through `RotationManager` (`current_app.rotation.*`) so `_after_mutation` fires.
- **Migration idiom:** sing_store per-column try/except on "duplicate column name" (add `supersedes_request_id`).
- **Concurrency:** the `/approve` route already 409s unless `status=='pending'` — keep that.
- Bump `pyproject.toml` to **0.79.0** in this PR. Update `docs/CHANGELOG.md`.
- Tests: `cd kj-controller && python -m pytest <path>` (prefix `rtk proxy` for raw output). Fixtures: unit `store`; integration `client`/`token`/`sing_app` + admin `admin_client`/`admin_app`/`_make_pending`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: `supersedes_request_id` column + create_request param

**Files:** Modify `kj-controller/sing_store.py` (`init_schema` migration block; `create_request`). Test `tests/unit/test_sing_store.py`.

**Interfaces:** `create_request(..., supersedes_request_id=None)` persists the value; it round-trips via `get_request`.

- [ ] **Step 1: Failing test** — append to `test_sing_store.py`:

```python
class TestSupersedes:
    def test_supersedes_request_id_persists(self, store):
        orig = store.create_request(singer_name="Al", phone="", source_type="local", source_ref="/a.mp4")
        new = store.create_request(singer_name="Al", phone="", source_type="local", source_ref="/b.mp4",
                                   supersedes_request_id=orig["id"])
        assert new["supersedes_request_id"] == orig["id"]
        assert store.get_request(orig["id"])["supersedes_request_id"] is None
```

- [ ] **Step 2: Run → fail** (`KeyError`/`TypeError`).
  `cd kj-controller && python -m pytest tests/unit/test_sing_store.py::TestSupersedes -v`

- [ ] **Step 3: Migration** — after the `edit_token` ALTER block in `init_schema`:

```python
        try:
            conn.execute(
                "ALTER TABLE sing_requests ADD COLUMN supersedes_request_id INTEGER DEFAULT NULL"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
```

- [ ] **Step 4: `create_request` param** — add `supersedes_request_id=None` to the signature (after `additional_singers=None`), add the column to the INSERT column list + a param, mint value = `supersedes_request_id`. (Add `supersedes_request_id` to the column list and pass the value in the tuple, alongside the existing `edit_token`.)

- [ ] **Step 5: Run → pass.** **Step 6: Commit** `feat(sing): add supersedes_request_id column`.

---

### Task 2: Singer `POST /sing/requests/<id>/change`

**Files:** Modify `kj-controller/sing.py` (new route after `cancel_request`). Test `tests/integration/test_sing_public_routes.py`.

**Interfaces:** Body `{t, edit_token, source_type, source_ref, source_meta, song_artist, song_title}`. Guards identical to `cancel_request`. Pending original → `update_request`/`update_request_source` in place (stays pending). Approved original → `create_request(..., supersedes_request_id=orig_id, token=orig.token)` copying `singer_name`/`phone`/`additional_singers`; returns the new pending request (public view). Terminal original (cancelled/rejected) → 409.

- [ ] **Step 1: Failing tests:**

```python
class TestChangeSong:
    def _body(self, **o):
        b = {"singer_name": "Alice", "phone": "", "song_artist": "Queen",
             "song_title": "Bo Rhap", "source_type": "local", "source_ref": "/x.mp4"}
        b.update(o); return b

    def test_change_pending_updates_in_place(self, client, sing_app, token):
        r = client.post(f"/sing/submit?t={token}", json=self._body()).get_json()["request"]
        resp = client.post(f"/sing/requests/{r['id']}/change?t={token}", json={
            "edit_token": r["edit_token"], "source_type": "local", "source_ref": "/new.mp4",
            "song_artist": "ABBA", "song_title": "SOS"})
        assert resp.status_code == 200
        got = sing_app.sing_store.get_request(r["id"])
        assert got["status"] == "pending" and got["song_title"] == "SOS"
        assert got["supersedes_request_id"] is None   # same row, no supersede

    def test_change_requires_edit_token(self, client, token):
        r = client.post(f"/sing/submit?t={token}", json=self._body()).get_json()["request"]
        bad = client.post(f"/sing/requests/{r['id']}/change?t={token}", json={
            "edit_token": "nope", "source_type": "local", "source_ref": "/n.mp4",
            "song_artist": "X", "song_title": "Y"})
        assert bad.status_code == 403

    def test_change_approved_creates_supersede(self, client, sing_app, token):
        sing_app.sing_store.set_auto_approve(True)
        r = client.post(f"/sing/submit?t={token}", json=self._body()).get_json()["request"]
        assert r["status"] == "approved"
        resp = client.post(f"/sing/requests/{r['id']}/change?t={token}", json={
            "edit_token": r["edit_token"], "source_type": "local", "source_ref": "/new.mp4",
            "song_artist": "ABBA", "song_title": "SOS"})
        assert resp.status_code == 200
        new = resp.get_json()["request"]
        assert new["status"] == "pending" and new["id"] != r["id"]
        assert sing_app.sing_store.get_request(new["id"])["supersedes_request_id"] == r["id"]
        # original still approved until the KJ approves the change
        assert sing_app.sing_store.get_request(r["id"])["status"] == "approved"
```

- [ ] **Step 2: Run → fail** (route 404).

- [ ] **Step 3: Implement** — add to `sing.py` after `cancel_request`. Reuse the same guard preamble as `cancel_request` (factor the shared guard into a helper `_authorize_own_request(store) -> (req, error_response)` if clean; otherwise inline). Body:

```python
@sing_bp.route("/requests/<int:req_id>/change", methods=["POST"])
def change_request(req_id):
    """Singer changes the SONG of their own request (edit_token-gated).

    Pending original → updated in place (stays pending). Approved original →
    a new pending request is created carrying supersedes_request_id so the KJ
    can approve the swap (see approve_sing_request_route)."""
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return jsonify({"error": "not_configured"}), 503
    cfg = current_app.kj_config
    if _rate_limit_exceeded(_client_ip(request),
                            _safe_int(cfg.get("sing_rate_limit_per_ip"), 5),
                            _safe_int(cfg.get("sing_rate_limit_window_s"), 300)):
        return jsonify({"error": "rate_limited"}), 429
    token = _extract_token()
    if not token or not _is_token_valid(store, token):
        return jsonify({"error": "not_open"}), 403
    req = store.get_request(req_id)
    if req is None or req.get("token") != token or not _belongs_to_current_night(store, req):
        return jsonify({"error": "not_found"}), 404
    data = request.get_json(silent=True) or {}
    stored = req.get("edit_token") or ""
    if not stored or not secrets.compare_digest(str(data.get("edit_token") or ""), str(stored)):
        return jsonify({"error": "forbidden"}), 403
    if req["status"] not in ("pending", "approved"):
        return jsonify({"error": f"cannot change a {req['status']} request"}), 409

    # Validate the new song's source (subset of submit()'s rules).
    source_type = (data.get("source_type") or "").strip()
    source_ref = data.get("source_ref") or None
    source_meta = data.get("source_meta") or None
    song_artist = (data.get("song_artist") or "").strip()
    song_title = (data.get("song_title") or "").strip()
    if source_type not in _ALLOWED_SOURCES:
        return jsonify({"error": f"source_type must be one of {sorted(_ALLOWED_SOURCES)}"}), 400
    if store.is_simple_mode() and source_type not in _SIMPLE_MODE_SOURCES:
        return jsonify({"error": "simple_mode_disabled_source"}), 400
    if source_type in {"local", "divebar", "kn", "youtube"} and not source_ref:
        return jsonify({"error": "source_ref is required for this source_type"}), 400
    if source_type == "make" and not store.is_accepting_make_requests():
        return jsonify({"error": "make_requests_disabled"}), 400
    if source_type == "kj_pick":
        err = _validate_kj_pick_payload(data)
        if err:
            return jsonify({"error": err}), 400

    if req["status"] == "pending":
        updated = store.update_request(
            req_id, song_artist=song_artist, song_title=song_title,
            source_type=source_type, source_ref=source_ref, source_meta=source_meta)
        return jsonify({"success": True, "request": _public_request_view(updated)})

    # Approved → create a superseding pending request the KJ approves.
    new_req = store.create_request(
        singer_name=req["singer_name"], phone=req.get("phone") or "",
        song_artist=song_artist, song_title=song_title,
        source_type=source_type, source_ref=source_ref, source_meta=source_meta,
        token=req["token"], additional_singers=req.get("additional_singers"),
        supersedes_request_id=req_id)
    return jsonify({"success": True, "request": _public_request_view(new_req)})
```

- [ ] **Step 4: Run → pass.** **Step 5: Commit** `feat(sing): singer change-song endpoint`.

---

### Task 3: KJ approval takes over the original slot (supersede)

**Files:** Modify `kj-controller/routes.py` (`approve_sing_request_route`). Test `tests/integration/test_sing_admin_routes.py`.

**Interfaces:** When the just-approved request has `supersedes_request_id`, after `mark_approved`, the new entry takes the original entry's position and the original entry+request are removed.

- [ ] **Step 1: Failing test** (admin side; uses `admin_app`, `_make_pending`, and the approve route):

```python
class TestSupersedeApproval:
    def test_approving_supersede_takes_over_slot(self, admin_client, admin_app):
        rot = admin_app.rotation
        # Original approved song at a known slot, with a filler entry after it.
        orig = _make_pending(admin_app, source_type="local", source_ref="/orig.mp4")
        e_orig = rot.add_entry("Alice", "Orig - Queen", file_path="/orig.mp4")
        admin_app.sing_store.mark_approved(orig["id"], linked_entry_id=e_orig["id"])
        rot.add_entry("Bob", "Filler")
        old_pos = rot.store.get_entry(e_orig["id"])["position"]
        # A superseding pending request (new song).
        sup = admin_app.sing_store.create_request(
            singer_name="Alice", phone="", song_artist="ABBA", song_title="SOS",
            source_type="local", source_ref="/new.mp4",
            supersedes_request_id=orig["id"])
        resp = admin_client.post(f"/rotation/requests/{sup['id']}/approve")
        assert resp.status_code == 200
        new_entry_id = resp.get_json()["entry_id"]
        # Old entry gone; new entry sits in the old slot; original request closed.
        assert rot.store.get_entry(e_orig["id"]) is None
        assert rot.store.get_entry(new_entry_id)["position"] == old_pos
        assert admin_app.sing_store.get_request(orig["id"])["status"] == "cancelled"
```

- [ ] **Step 2: Run → fail** (new entry appended at end, old entry still present).

- [ ] **Step 3: Implement** — in `approve_sing_request_route`, after `store.mark_approved(req_id, linked_entry_id=entry_id)` and before the push-notify, add:

```python
    # Supersede: this request replaces an approved one (singer edited the song).
    # Take over the original entry's slot and remove the original.
    sup_id = req.get("supersedes_request_id")
    if sup_id:
        rotation = current_app.rotation
        orig = store.get_request(sup_id)
        old_entry_id = (orig or {}).get("linked_entry_id")
        old_entry = rotation.store.get_entry(old_entry_id) if old_entry_id else None
        if old_entry:
            old_pos = old_entry["position"]
            rotation.delete_entry(old_entry_id)      # recompacts positions
            rotation.move_entry(entry_id, old_pos)   # new song takes the old slot
        if orig:
            store.mark_cancelled(sup_id)             # original replaced
```

- [ ] **Step 4: Run → pass.** **Step 5: Commit** `feat(kj): approving a superseding request takes over the original slot`.

---

### Task 4: Singer `POST /sing/requests/reorder`

**Files:** Modify `kj-controller/sing.py`. Test `tests/integration/test_sing_public_routes.py`.

**Interfaces:** Body `{t, items:[{id, edit_token}, ...]}` — the singer's own request ids in desired order (≥2). Each must be owned (edit_token match), night-scoped, `status=='approved'`, with a `linked_entry_id`. Creates a pending request `source_type='reorder'`, `source_meta={ordered_entry_ids:[...]}`, `token`=first req's token. Returns the reorder request (public view). Errors: <2 items → 400; any item not owned/approved → 403/409.

- [ ] **Step 1: Failing tests:**

```python
class TestReorder:
    def _approved(self, client, sing_app, token, title):
        sing_app.sing_store.set_auto_approve(True)
        return client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Alice", "phone": "", "song_artist": "Q", "song_title": title,
            "source_type": "local", "source_ref": f"/{title}.mp4"}).get_json()["request"]

    def test_reorder_creates_pending_reorder_request(self, client, sing_app, token):
        a = self._approved(client, sing_app, token, "A")
        b = self._approved(client, sing_app, token, "B")
        resp = client.post(f"/sing/requests/reorder?t={token}", json={"items": [
            {"id": b["id"], "edit_token": b["edit_token"]},
            {"id": a["id"], "edit_token": a["edit_token"]}]})
        assert resp.status_code == 200
        rr = resp.get_json()["request"]
        assert rr["source_type"] == "reorder" and rr["status"] == "pending"

    def test_reorder_rejects_unowned_item(self, client, sing_app, token):
        a = self._approved(client, sing_app, token, "A")
        b = self._approved(client, sing_app, token, "B")
        resp = client.post(f"/sing/requests/reorder?t={token}", json={"items": [
            {"id": b["id"], "edit_token": "WRONG"},
            {"id": a["id"], "edit_token": a["edit_token"]}]})
        assert resp.status_code == 403

    def test_reorder_needs_two(self, client, sing_app, token):
        a = self._approved(client, sing_app, token, "A")
        resp = client.post(f"/sing/requests/reorder?t={token}",
                           json={"items": [{"id": a["id"], "edit_token": a["edit_token"]}]})
        assert resp.status_code == 400
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — add to `sing.py`:

```python
@sing_bp.route("/requests/reorder", methods=["POST"])
def reorder_requests():
    """Singer reorders their OWN approved songs. Creates a pending 'reorder'
    request (KJ approves → move_entry). All items must be owned (edit_token)."""
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return jsonify({"error": "not_configured"}), 503
    cfg = current_app.kj_config
    if _rate_limit_exceeded(_client_ip(request),
                            _safe_int(cfg.get("sing_rate_limit_per_ip"), 5),
                            _safe_int(cfg.get("sing_rate_limit_window_s"), 300)):
        return jsonify({"error": "rate_limited"}), 429
    token = _extract_token()
    if not token or not _is_token_valid(store, token):
        return jsonify({"error": "not_open"}), 403
    data = request.get_json(silent=True) or {}
    items = data.get("items")
    if not isinstance(items, list) or len(items) < 2:
        return jsonify({"error": "at least two items required"}), 400

    ordered_entry_ids = []
    first_req = None
    for it in items:
        try:
            rid = int(it.get("id"))
        except (TypeError, ValueError):
            return jsonify({"error": "each item needs an integer id"}), 400
        req = store.get_request(rid)
        if req is None or req.get("token") != token or not _belongs_to_current_night(store, req):
            return jsonify({"error": "not_found"}), 404
        stored = req.get("edit_token") or ""
        if not stored or not secrets.compare_digest(str(it.get("edit_token") or ""), str(stored)):
            return jsonify({"error": "forbidden"}), 403
        if req["status"] != "approved" or not req.get("linked_entry_id"):
            return jsonify({"error": "each item must be an approved queued song"}), 409
        ordered_entry_ids.append(req["linked_entry_id"])
        first_req = first_req or req

    rr = store.create_request(
        singer_name=first_req["singer_name"], phone="",
        source_type="reorder", source_ref=None,
        source_meta={"ordered_entry_ids": ordered_entry_ids},
        token=token)
    return jsonify({"success": True, "request": _public_request_view(rr)})
```

Note: `reorder` must be accepted by `create_request` (no source_ref) — it is (source_ref is nullable). `_public_request_view` already returns `source_type`, so the client sees `reorder`.

- [ ] **Step 4: Run → pass.** **Step 5: Commit** `feat(sing): singer reorder-own endpoint`.

---

### Task 5: KJ approval applies a reorder

**Files:** Modify `kj-controller/routes.py` (`approve_sing_request_route`). Test `tests/integration/test_sing_admin_routes.py`.

**Interfaces:** A pending request with `source_type=='reorder'` is handled BEFORE `approve_sing_request` (which would raise on the unknown type): apply `move_entry` so the singer's entries end up in `ordered_entry_ids` order within the slots they currently occupy; then `mark_approved(req_id, linked_entry_id=None)`.

- [ ] **Step 1: Failing test:**

```python
    def test_approving_reorder_applies_move(self, admin_client, admin_app):
        rot = admin_app.rotation
        a = rot.add_entry("Alice", "Song A")
        rot.add_entry("Bob", "Filler")
        b = rot.add_entry("Alice", "Song B")
        # Desired: B before A (swap their two slots: positions of a=1, b=3).
        rr = admin_app.sing_store.create_request(
            singer_name="Alice", phone="", source_type="reorder", source_ref=None,
            source_meta={"ordered_entry_ids": [b["id"], a["id"]]})
        resp = admin_client.post(f"/rotation/requests/{rr['id']}/approve")
        assert resp.status_code == 200
        entries = {e["id"]: e["position"] for e in rot.get_rotation()}
        assert entries[b["id"]] < entries[a["id"]]   # B now ahead of A
        assert admin_app.sing_store.get_request(rr["id"])["status"] == "approved"
```

- [ ] **Step 2: Run → fail** (approve raises "Unknown source_type: reorder" → 500).

- [ ] **Step 3: Implement** — in `approve_sing_request_route`, right after the `if req["status"] != "pending": 409` guard (before the kj_pick handling), add:

```python
    # Reorder request: not a song — apply the singer's requested order to their
    # own entries within the slots they currently occupy, then mark approved.
    if req["source_type"] == "reorder":
        rotation = current_app.rotation
        meta = req.get("source_meta")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (TypeError, ValueError):
                meta = {}
        ordered = (meta or {}).get("ordered_entry_ids") or []
        # Target slots = the current positions of these entries, ascending.
        cur = []
        for eid in ordered:
            e = rotation.store.get_entry(eid)
            if e is not None:
                cur.append((eid, e["position"]))
        target_positions = sorted(p for _, p in cur)
        # Fill ascending target slots in the requested order.
        for eid in ordered:
            if not target_positions:
                break
            rotation.move_entry(eid, target_positions.pop(0))
        store.mark_approved(req_id, linked_entry_id=None)
        return jsonify({"success": True, "request": store.get_request(req_id), "entry_id": None})
```

(`json` is already imported in routes.py.)

- [ ] **Step 4: Run → pass.** Also run the full admin + public suites for regressions. **Step 5: Commit** `feat(kj): approving a reorder request applies the singer's order`.

---

### Task 6: KJ panel renders change + reorder items

**Files:** Modify `kj-controller/static/app.js` (`renderRow`, ~8047). Test `tests/e2e/test_rotation_e2e.py` (seed via `sing_store` + `fetchPending()`), or an admin-panel e2e if one exists.

**Interfaces:** A pending request with `supersedes_request_id` renders a "Change" label (what → what); `source_type==='reorder'` renders a "Reorder" label. Both keep the existing Approve/Reject buttons (the `/approve` route handles them). Reorder rows must not render the kj_pick picker or song badges that assume a song.

- [ ] **Step 1: Failing e2e** — seed a reorder + a supersede pending request via the page's fetch to `sing_store` isn't possible; instead POST through the admin API or use `admin_app`. Simplest: drive the KJ page, create the pending requests via `page.evaluate(fetch('/rotation/...'))` is not available (no admin create endpoint). Use the singer endpoints via `page.evaluate` against the live server: submit + auto-approve off, then… Given harness limits, seed by inserting through the singer submit + change/reorder endpoints in `page.evaluate`, then assert `#pending-requests-list` shows a `.pr-change` / `.pr-reorder` row. Write the assertion:

```python
        # after seeding a supersede + reorder pending request and calling SingRequests.fetchPending():
        expect(page.locator('.pending-req-row .pr-reorder')).to_be_visible()
        expect(page.locator('.pending-req-row .pr-change')).to_be_visible()
```

(Implementer: seed via the singer HTTP endpoints in `page.evaluate` — submit two songs with auto-approve on to get approved requests, then call `/sing/requests/reorder` and `/sing/requests/<id>/change`; flip auto-approve off first so the change/reorder stay pending. Then `SingRequests.fetchPending()` and assert. If this proves too fiddly in e2e, cover rendering with a focused DOM unit assertion by calling `renderRow` via `page.evaluate` with a synthetic req object and asserting the returned node's classes.)

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — in `renderRow(req)`, before building the default `song`/`approveButton`, branch:

```javascript
        const isReorder = req.source_type === 'reorder';
        const isChange = !!req.supersedes_request_id;
```

Then set the main line + a tag class accordingly:
- Reorder: main shows `↕ Reorder — ${singer}'s songs`; add class `pr-reorder`; no song badge, no kj_pick picker, no YouTube preview.
- Change: main shows `✎ Change — ${singer}: ${song}`; add class `pr-change`; otherwise render like a normal request (it has a real new song to approve/download).

Concretely, wrap the existing song/badge/approve construction so that for `isReorder` you render a minimal row:

```javascript
        if (isReorder) {
            row.className = 'pending-req-row pr-reorder';
            row.innerHTML = `
              <div class="pr-main"><strong>↕ Reorder</strong>
                <span class="pr-song">${escapeHtml(req.singer_name)}'s songs</span></div>
              <div class="pr-actions">
                <button class="btn-approve" data-id="${req.id}">Approve</button>
                <button class="btn-reject" data-id="${req.id}">Reject</button>
              </div>`;
            row.querySelector('.btn-approve').addEventListener('click', () => approve(req.id));
            row.querySelector('.btn-reject').addEventListener('click', () => reject(req.id));
            return row;
        }
```

For `isChange`, keep the normal row but prefix the song label with `✎ Change — ` and add `row.classList.add('pr-change')` after `row.className = 'pending-req-row'`. (A change request has `source_type` local/divebar/kn/youtube/make/kj_pick like any request, so the existing approve/download path applies unchanged.)

- [ ] **Step 4: Run → pass.** **Step 5: Commit** `feat(kj): render change + reorder requests in the panel`.

---

### Task 7: Singer done-screen Change + Reorder controls

**Files:** Modify `kj-controller/static-sing/sing.js` (`_renderSongCard`, `renderDone`). Modify `sing.css`. Test `tests/e2e/test_sing_frontend.py`.

**Interfaces:** On the done card, when the device owns the request (`readEditToken`) and it's cancellable, add a **Change song** button → re-enters the search flow in "change mode" (on pick, POST `/requests/<id>/change` instead of `/submit`). When the device owns ≥2 approved requests, show a **Reorder** control (a simple up/down or a "move to top" per card) → POST `/requests/reorder`.

- [ ] **Step 1: Failing tests** (mirror the 2a cancel e2e with routed `expect_request`):

```python
class TestChangeReorderControls:
    def test_change_button_enters_change_mode(self, page, live_server, live_token):
        # seed one owned pending request, land on done, assert a Change button exists
        ...
        expect(page.locator('[data-testid="change-song"]')).to_be_visible()

    def test_reorder_control_shows_with_two_owned(self, page, live_server, live_token):
        # seed two owned approved requests, assert a reorder affordance exists
        ...
        expect(page.locator('[data-testid="reorder-up"]').first).to_be_visible()
```

(Implementer: seed `localStorage` `sing_my_request_ids` with two ids + tokens and stub `/sing/my-requests` as in the 2a cancel test. For change, assert clicking sets `state.step==='search'` and a `state.changeRequestId`. For reorder, assert clicking POSTs `/sing/requests/reorder` with both edit_tokens via `page.expect_request`.)

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement:**
  - Add `state.changeRequestId` + `state.changeEditToken` (default null). In `_renderSongCard`, when `cancellable`, add a `Change song` button (`data-testid="change-song"`) that sets `state.changeRequestId=req.id; state.changeEditToken=editToken; state.step='search'; render()`.
  - In the search→confirm→send flow, when `state.changeRequestId` is set, the send handler POSTs `${BASE}/requests/${state.changeRequestId}/change` with `{t, edit_token: state.changeEditToken, source_type, source_ref, source_meta, song_artist, song_title}` instead of `/submit`; on success clear the change-mode state and go to `done`.
  - Reorder: when the done list has ≥2 owned approved cards, render per-card `▲`/`▼` (`data-testid="reorder-up"`/`down`) that reorder the local list and POST `/requests/reorder` with the resulting `items:[{id,edit_token}]` order; show "reorder requested — waiting for KJ".
  - Keep it minimal; reuse existing confirm screen for change.

- [ ] **Step 4: Run → pass; run whole `test_sing_frontend.py`.** **Step 5: Commit** `feat(sing): Change-song + Reorder controls on the done screen`.

---

### Task 8: Version bump + CHANGELOG + review + ship

- [ ] **Step 1:** Bump `pyproject.toml` → `0.79.0`; add a `docs/CHANGELOG.md` entry (v0.79.0, backend/restart deploy note).
- [ ] **Step 2:** Full affected suite green (unit + integration + e2e sing + rotation e2e cancelled/change/reorder).
- [ ] **Step 3:** Whole-branch code review (subagent) + CodeRabbit (`coderabbit review --agent --base-commit <PR#2a-merge-base>` or `--base main`); fix findings; re-review clean.
- [ ] **Step 4:** Commit; open PR (`@coderabbitai ignore`; **maintenance-window deploy** note).
- [ ] **Step 5:** With the user's go-ahead (they've cleared tonight): merge (squash), wait for device auto-deploy, verify on-device (version, change + reorder routes live, app healthy).

## Self-review notes
- Spec coverage: edit-song (T2 pending in-place, T3 approved-supersede) · reorder (T4 create, T5 apply) · KJ panel (T6) · singer UI (T7) · ship (T8). Cancel is already shipped (PR #2a).
- Reuse: `create_request` (supersede + reorder rows), `approve_sing_request` (supersede's new song incl. download), `move_entry`/`delete_entry`, the existing Approve/Reject route + panel, the 2a ownership guard.
- Types: `create_request(..., supersedes_request_id=None)` (T1) consumed by T2/T4; `approve_sing_request_route` branches on `source_type=='reorder'` (T5) and `supersedes_request_id` (T3); `_public_request_view` unchanged (returns `source_type`, so client sees `reorder`).
- Risk: supersede slot-takeover position math — covered by T3 test (filler entry + assert new sits at old_pos). Reorder position math — covered by T5 test (filler between the two).
