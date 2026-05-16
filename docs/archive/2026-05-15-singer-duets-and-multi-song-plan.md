# Singer UI: duet partners & multi-song flow — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let singers (a) add duet partners (up to 3 extras, optional phones) on the confirm screen, and (b) request multiple songs in one event with a multi-song "your night" done screen.

**Architecture:** Add one nullable JSON column (`additional_singers`) to `sing_requests`. Reuse existing `rotation_entries.singers_json` plumbing by passing `singers=` through `approve_sing_request`. New `/sing/my-requests?ids=…` endpoint backs a multi-song done screen, driven by a per-token id list in localStorage.

**Tech Stack:** Flask + SQLite (kj-controller), vanilla JS + pytest + Playwright. No build step.

**Spec:** [`docs/archive/2026-05-15-singer-duets-and-multi-song-design.md`](2026-05-15-singer-duets-and-multi-song-design.md)

---

## Working directory

All paths are relative to `/Users/andrew/Projects/nomadkaraoke/kjbox-singer-add-songs-duets/`. Run tests from inside `kj-controller/` (`cd kj-controller && pytest …`).

Branch: `feat/sess-20260514-2226-singer-add-songs-duets` (already created).

## Conventions

- **TDD:** write the failing test first, run it, see it fail, implement, see it pass, commit.
- **Commits are per-task.** Each task ends with one commit.
- **Subprocess / device safety:** this code runs on nomadpc (live device). Do NOT `git push` or restart the systemd unit at any point during implementation — local commits only.

---

## Task 1: Add `additional_singers` column + idempotent migration

**Files:**
- Modify: `kj-controller/sing_store.py` (function `init_schema`)
- Test: `kj-controller/tests/unit/test_sing_store.py` (new test inside `TestSchemaInit`)

- [ ] **Step 1: Write the failing test**

Append to `kj-controller/tests/unit/test_sing_store.py` inside `class TestSchemaInit:` (keep alongside `test_sing_requests_columns`):

```python
    def test_additional_singers_column_present(self, store):
        conn = store._get_conn()
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(sing_requests)"
        ).fetchall()}
        assert "additional_singers" in cols

    def test_additional_singers_migration_idempotent(self, tmp_path):
        """Re-initialising on an existing DB must not fail on the new column."""
        db_path = str(tmp_path / "rotation.db")
        SingStore(db_path).close()
        # Second open re-runs init_schema; must not raise.
        SingStore(db_path).close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd kj-controller && pytest tests/unit/test_sing_store.py::TestSchemaInit -v
```

Expected: `test_additional_singers_column_present` FAILs with `assert 'additional_singers' in cols`.

- [ ] **Step 3: Add the migration**

In `kj-controller/sing_store.py`, locate `init_schema` (around line 85) and add the additive migration **after** the existing `executescript(...)` block but **before** `conn.commit()`:

```python
        # Additive migration — `additional_singers` was added 2026-05-15 for
        # duet-partner support. Existing rows get NULL (= solo request).
        try:
            conn.execute(
                "ALTER TABLE sing_requests "
                "ADD COLUMN additional_singers TEXT DEFAULT NULL"
            )
        except sqlite3.OperationalError as e:
            # Column already exists on this DB; safe to ignore.
            if "duplicate column name" not in str(e).lower():
                raise
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd kj-controller && pytest tests/unit/test_sing_store.py::TestSchemaInit -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/sing_store.py kj-controller/tests/unit/test_sing_store.py
git commit -m "feat(sing): add additional_singers column with idempotent migration"
```

---

## Task 2: SingStore round-trips `additional_singers`

**Files:**
- Modify: `kj-controller/sing_store.py` (`create_request`, `update_request`, `_row_to_dict`)
- Test: `kj-controller/tests/unit/test_sing_store.py` (new `TestAdditionalSingers` class)

- [ ] **Step 1: Write the failing tests**

Append to `kj-controller/tests/unit/test_sing_store.py` (end of file):

```python
class TestAdditionalSingers:
    """additional_singers JSON column round-trips through create / get / update."""

    def _partners(self):
        return [
            {"name": "Sarah B.", "phone": "+61 400 111 222"},
            {"name": "Mike", "phone": ""},
        ]

    def test_create_with_partners_round_trips(self, store):
        req = store.create_request(
            singer_name="Alice",
            phone="+61 400 000 000",
            source_type="local",
            source_ref="/tmp/x.mp4",
            additional_singers=self._partners(),
        )
        assert req["additional_singers"] == self._partners()
        # And re-fetching deserialises too.
        again = store.get_request(req["id"])
        assert again["additional_singers"] == self._partners()

    def test_create_without_partners_is_none(self, store):
        req = store.create_request(
            singer_name="Solo",
            phone="",
            source_type="local",
            source_ref="/tmp/y.mp4",
        )
        assert req["additional_singers"] is None

    def test_update_can_set_partners(self, store):
        req = store.create_request(
            singer_name="Alice", phone="", source_type="local",
            source_ref="/tmp/x.mp4",
        )
        updated = store.update_request(req["id"], additional_singers=self._partners())
        assert updated["additional_singers"] == self._partners()

    def test_update_can_clear_partners(self, store):
        req = store.create_request(
            singer_name="Alice", phone="", source_type="local",
            source_ref="/tmp/x.mp4",
            additional_singers=self._partners(),
        )
        cleared = store.update_request(req["id"], additional_singers=[])
        assert cleared["additional_singers"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd kj-controller && pytest tests/unit/test_sing_store.py::TestAdditionalSingers -v
```

Expected: all FAIL (parameter not accepted, or stored as raw JSON string).

- [ ] **Step 3: Update `_row_to_dict` to deserialise**

In `kj-controller/sing_store.py`, replace the existing `_row_to_dict` (around line 144) with:

```python
    @staticmethod
    def _row_to_dict(row):
        if row is None:
            return None
        d = dict(row)
        # additional_singers is stored as JSON; deserialise for callers.
        # NULL → None (solo). Empty array → [] (cleared list).
        raw = d.get("additional_singers")
        if raw is not None:
            try:
                d["additional_singers"] = json.loads(raw)
            except (TypeError, ValueError):
                d["additional_singers"] = None
        return d
```

- [ ] **Step 4: Update `create_request` to accept and persist partners**

In `kj-controller/sing_store.py`, change the `create_request` signature and body. Replace the whole method with:

```python
    def create_request(
        self,
        singer_name,
        phone,
        song_artist="",
        song_title="",
        source_type="local",
        source_ref=None,
        source_meta=None,
        notes="",
        token=None,
        additional_singers=None,
    ):
        """Insert a new pending request and return the created row as a dict."""
        if not singer_name:
            raise ValueError("singer_name is required")
        if not source_type:
            raise ValueError("source_type is required")
        phone = phone or ""

        request_token = token if token is not None else (self.get_token() or "")
        meta_json = json.dumps(source_meta) if source_meta is not None else None
        partners_json = (
            json.dumps(additional_singers) if additional_singers is not None else None
        )
        conn = self._get_conn()
        cur = conn.execute(
            """
            INSERT INTO sing_requests
                (token, singer_name, phone, song_artist, song_title,
                 source_type, source_ref, source_meta, notes,
                 additional_singers)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        conn.commit()
        return self.get_request(cur.lastrowid)
```

- [ ] **Step 5: Update `update_request` to accept partners**

In `kj-controller/sing_store.py`, change the `update_request` method. The new field needs sentinel-vs-None handling because `None` means "clear it" for the caller but "leave existing" for other fields. We follow the existing convention (None = "leave existing"), and use a sentinel for explicit clears:

```python
    _SENTINEL = object()

    def update_request(
        self,
        request_id,
        singer_name=None,
        song_artist=None,
        song_title=None,
        source_type=None,
        source_ref=None,
        source_meta=None,
        notes=None,
        additional_singers=_SENTINEL,
    ):
        """Edit mutable fields on a request. Returns the updated row.

        For `additional_singers`: pass a list (incl. empty) to overwrite, or
        leave default to preserve the existing value. There is no "set to
        NULL" path through update_request — create with None instead.
        """
        existing = self.get_request(request_id)
        if existing is None:
            raise ValueError(f"Request {request_id} not found")

        if additional_singers is self._SENTINEL:
            partners_json = (
                json.dumps(existing["additional_singers"])
                if existing.get("additional_singers") is not None
                else None
            )
        else:
            partners_json = json.dumps(additional_singers)

        new_vals = {
            "singer_name": singer_name.strip() if singer_name is not None else existing["singer_name"],
            "song_artist": song_artist.strip() if song_artist is not None else existing["song_artist"],
            "song_title": song_title.strip() if song_title is not None else existing["song_title"],
            "source_type": source_type if source_type is not None else existing["source_type"],
            "source_ref": source_ref if source_ref is not None else existing["source_ref"],
            "source_meta": (
                json.dumps(source_meta) if source_meta is not None
                else (
                    json.dumps(existing["source_meta"])
                    if isinstance(existing.get("source_meta"), (dict, list))
                    else existing["source_meta"]
                )
            ),
            "notes": notes.strip() if notes is not None else existing["notes"],
        }
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE sing_requests
               SET singer_name = ?, song_artist = ?, song_title = ?,
                   source_type = ?, source_ref = ?, source_meta = ?,
                   notes = ?, additional_singers = ?
             WHERE id = ?
            """,
            (
                new_vals["singer_name"], new_vals["song_artist"], new_vals["song_title"],
                new_vals["source_type"], new_vals["source_ref"], new_vals["source_meta"],
                new_vals["notes"], partners_json, request_id,
            ),
        )
        conn.commit()
        return self.get_request(request_id)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd kj-controller && pytest tests/unit/test_sing_store.py -v
```

Expected: all pass, including pre-existing tests (the `_row_to_dict` change must not regress `source_meta` round-tripping — verify by re-running the whole file).

- [ ] **Step 7: Commit**

```bash
git add kj-controller/sing_store.py kj-controller/tests/unit/test_sing_store.py
git commit -m "feat(sing): round-trip additional_singers through SingStore CRUD"
```

---

## Task 3: `/sing/submit` validates + persists `additional_singers`

**Files:**
- Modify: `kj-controller/sing.py` (constants + `/submit` route)
- Test: `kj-controller/tests/integration/test_sing_public_routes.py` (new tests inside `class TestSubmit`)

- [ ] **Step 1: Write failing tests**

Append to `class TestSubmit:` in `kj-controller/tests/integration/test_sing_public_routes.py`:

```python
    def test_submit_with_partners(self, client, sing_app, token):
        body = self._body()
        body["additional_singers"] = [
            {"name": "Sarah B.", "phone": "+61 400 111 222"},
            {"name": "Mike", "phone": ""},
        ]
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["request"]["additional_singers"] == [
            {"name": "Sarah B.", "phone": "+61 400 111 222"},
            {"name": "Mike", "phone": ""},
        ]

    def test_submit_partners_omitted_is_solo(self, client, sing_app, token):
        resp = client.post(f"/sing/submit?t={token}", json=self._body())
        assert resp.status_code == 200
        assert resp.get_json()["request"]["additional_singers"] is None

    def test_submit_rejects_too_many_partners(self, client, token):
        body = self._body()
        body["additional_singers"] = [
            {"name": f"Singer {i}"} for i in range(4)
        ]
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 400
        assert "additional_singers" in resp.get_json()["error"]

    def test_submit_rejects_empty_partner_name(self, client, token):
        body = self._body()
        body["additional_singers"] = [{"name": "  ", "phone": ""}]
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 400
        assert "additional_singers" in resp.get_json()["error"]

    def test_submit_rejects_malformed_partner_phone(self, client, token):
        body = self._body()
        body["additional_singers"] = [
            {"name": "Sarah", "phone": "not-a-phone"}
        ]
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 400
        assert "additional_singers" in resp.get_json()["error"]

    def test_submit_accepts_partner_without_phone(self, client, sing_app, token):
        body = self._body()
        body["additional_singers"] = [{"name": "Phoneless Pete"}]
        resp = client.post(f"/sing/submit?t={token}", json=body)
        assert resp.status_code == 200
        partners = resp.get_json()["request"]["additional_singers"]
        # Server normalises missing phone → "".
        assert partners == [{"name": "Phoneless Pete", "phone": ""}]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd kj-controller && pytest tests/integration/test_sing_public_routes.py::TestSubmit -v -k partners
```

Expected: all the new tests FAIL.

- [ ] **Step 3: Add a validation helper to `sing.py`**

In `kj-controller/sing.py`, after the `_validate_kj_pick_payload` helper (around line 285), add:

```python
_MAX_ADDITIONAL_SINGERS = 3


def _validate_additional_singers(raw):
    """Return (normalised_list, error_message). One of the two is None.

    `None` raw → (None, None) — solo request, no partners.
    `[]`        → ([], None) — explicit clear (treated as solo).
    Otherwise: list of dicts, each `{"name": <required>, "phone": <opt>}`.
    Length must be ≤ _MAX_ADDITIONAL_SINGERS. Names are .strip()-ed and
    must be non-empty. Phones, when present, must match _PHONE_RE.
    """
    if raw is None:
        return None, None
    if not isinstance(raw, list):
        return None, "additional_singers must be a list"
    if len(raw) > _MAX_ADDITIONAL_SINGERS:
        return None, (
            f"additional_singers: max {_MAX_ADDITIONAL_SINGERS} extras "
            f"(got {len(raw)})"
        )
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return None, f"additional_singers[{i}]: must be an object"
        name = (item.get("name") or "").strip()
        phone = (item.get("phone") or "").strip()
        if not name:
            return None, f"additional_singers[{i}]: name is required"
        if len(name) > 100:
            return None, f"additional_singers[{i}]: name too long"
        if phone and not _PHONE_RE.match(phone):
            return None, f"additional_singers[{i}]: phone format invalid"
        out.append({"name": name, "phone": phone})
    return out, None
```

- [ ] **Step 4: Wire validation into `/submit`**

In `kj-controller/sing.py`, inside the `submit()` view (around line 469), after `notes = (data.get("notes") or "").strip()` and before the existing validations, add:

```python
    additional_raw = data.get("additional_singers")
    additional, additional_err = _validate_additional_singers(additional_raw)
    if additional_err:
        return jsonify({"error": additional_err}), 400
```

Then change the `store.create_request(...)` call to pass `additional_singers=additional`:

```python
    req = store.create_request(
        singer_name=singer_name,
        phone=phone,
        song_artist=song_artist,
        song_title=song_title,
        source_type=source_type,
        source_ref=source_ref,
        source_meta=source_meta,
        notes=notes,
        additional_singers=additional,
    )
```

- [ ] **Step 5: Extend `_public_request_view` to expose partners**

In `kj-controller/sing.py`, replace `_public_request_view` (around line 719):

```python
def _public_request_view(req):
    """Hide internal/PII fields from singer-facing responses."""
    return {
        "id": req["id"],
        "singer_name": req["singer_name"],
        "song_artist": req["song_artist"],
        "song_title": req["song_title"],
        "source_type": req["source_type"],
        "status": req["status"],
        "created_at": req["created_at"],
        "linked_entry_id": req.get("linked_entry_id"),
        "additional_singers": req.get("additional_singers"),
    }
```

- [ ] **Step 6: Run tests to verify all pass**

```bash
cd kj-controller && pytest tests/integration/test_sing_public_routes.py::TestSubmit -v
```

Expected: all pass (new partner tests AND pre-existing tests — `additional_singers: None` should naturally appear on existing happy-path responses).

If pre-existing tests broke because they assert exact-equality on the response request shape, leave them — the spec says response shape gains a field, and stale clients ignore unknown keys.

- [ ] **Step 7: Commit**

```bash
git add kj-controller/sing.py kj-controller/tests/integration/test_sing_public_routes.py
git commit -m "feat(sing): /submit validates & persists additional_singers (max 3)"
```

---

## Task 4: `approve_sing_request` plumbs partners into `rotation_entries.singers_json`

**Files:**
- Modify: `kj-controller/routes.py` (function `approve_sing_request`)
- Test: `kj-controller/tests/integration/test_sing_admin_routes.py` (new tests inside `TestApprove`)

- [ ] **Step 1: Write failing tests**

Append to `class TestApprove:` in `kj-controller/tests/integration/test_sing_admin_routes.py`. The existing `_make_pending` helper must accept the new field — check whether it already takes `**kwargs`; if not, treat this step as also adjusting the helper. Locate it via grep:

```bash
grep -nE 'def _make_pending' kj-controller/tests/integration/test_sing_admin_routes.py
```

Read the helper. If it takes `**kwargs`, pass `additional_singers=...` directly. Otherwise add the param. Then add these tests:

```python
    def test_approve_local_with_duet_partners(self, admin_client, admin_app):
        req = _make_pending(
            admin_app,
            singer_name="Alice",
            source_type="local",
            source_ref="/tmp/song.mp4",
            additional_singers=[
                {"name": "Sarah B.", "phone": "+61 400 111 222"},
                {"name": "Mike", "phone": ""},
            ],
        )
        resp = admin_client.post(f"/rotation/requests/{req['id']}/approve")
        assert resp.status_code == 200
        linked_id = resp.get_json()["request"]["linked_entry_id"]
        entry = admin_app.rotation.store.get_entry(linked_id)
        # Joined singer string for legacy text column.
        assert entry["singer"] == "Alice & Sarah B. & Mike"
        # Structured list survives in singers_json.
        import json as _json
        names = _json.loads(entry["singers_json"])
        assert names == ["Alice", "Sarah B.", "Mike"]

    def test_approve_solo_unchanged(self, admin_client, admin_app):
        """Solo approvals must still produce a single-singer entry (no singers_json)."""
        req = _make_pending(
            admin_app, singer_name="SoloAndrew",
            source_type="local", source_ref="/tmp/x.mp4",
        )
        resp = admin_client.post(f"/rotation/requests/{req['id']}/approve")
        assert resp.status_code == 200
        linked_id = resp.get_json()["request"]["linked_entry_id"]
        entry = admin_app.rotation.store.get_entry(linked_id)
        assert entry["singer"] == "SoloAndrew"
        assert entry["singers_json"] in (None, "")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd kj-controller && pytest tests/integration/test_sing_admin_routes.py::TestApprove -v -k duet
```

Expected: `test_approve_local_with_duet_partners` FAILs (singer is "Alice", not "Alice & Sarah B. & Mike").

- [ ] **Step 3: Build a `singers=` list inside `approve_sing_request`**

In `kj-controller/routes.py`, around line 3086, modify `approve_sing_request`. After the existing `singer = req["singer_name"]` line, derive the names list once and use it in all four `rotation.add_entry(...)` calls:

```python
def approve_sing_request(app, req):
    """..."""
    rotation = getattr(app, 'rotation', None)
    if rotation is None:
        raise RuntimeError("Rotation not configured")

    singer = req["singer_name"]
    song_text = _format_song_text(req.get("song_artist"), req.get("song_title"))
    source_type = req["source_type"]
    source_ref = req.get("source_ref")

    # Build the multi-singer list, primary first, when partners are attached.
    # Passing None keeps the existing solo-entry behaviour (no singers_json).
    partners = req.get("additional_singers") or []
    singers_list = (
        [singer] + [p["name"] for p in partners if p.get("name")]
        if partners else None
    )

    if source_type == "local":
        entry = rotation.add_entry(
            singer, song_text, file_path=source_ref or None,
            singers=singers_list,
        )
        return entry["id"]

    if source_type in ("divebar", "youtube", "kn"):
        from uuid import uuid4
        download_id = str(uuid4())
        if source_type == "divebar":
            try:
                download_url = divebar.get_download_url(source_ref, app.kj_config)
            except Exception as exc:
                raise RuntimeError(f"Divebar URL failed: {exc}") from exc
            if not download_url:
                raise RuntimeError("Failed to get download URL from Divebar")
            title = f"divebar-{source_ref}.mp4"
            queue_src = "divebar"
            queue_url = download_url
        else:
            if not source_ref:
                raise RuntimeError("source_ref (YouTube URL) required")
            queue_src = "youtube"
            queue_url = source_ref
            title = song_text or (req.get("song_title") or "")

        entry = rotation.add_entry(singer, song_text, singers=singers_list)
        queue_item = {
            "id": download_id,
            "url": queue_url,
            "title": title,
            "source": queue_src,
            "status": "queued",
            "error": None,
            "rotation_entry_id": entry["id"],
        }
        rotation.set_download_status(
            entry["id"], queue_src, "queued", download_id
        )
        with app._download_lock:
            app.download_queue["items"].append(queue_item)
            if not app.download_queue.get("worker_running"):
                app.download_queue["worker_running"] = True
                threading.Thread(
                    target=_download_worker, args=(app,), daemon=True
                ).start()
        return entry["id"]

    if source_type == "make":
        gen_client = getattr(app, "gen_client", None)
        if gen_client is None:
            raise RuntimeError("Gen API not configured")
        entry = rotation.add_entry(singer, song_text, singers=singers_list)
        result = gen_client.create_job(
            req.get("song_artist", ""), req.get("song_title", "")
        )
        job_id = result.get("job_id")
        if not job_id:
            raise RuntimeError("Gen API did not return a job_id")
        from gen_client import map_gen_status
        rotation.set_gen_status(
            entry["id"], job_id, map_gen_status(result.get("status", "pending"))
        )
        return entry["id"]

    raise ValueError(f"Unknown source_type: {source_type}")
```

- [ ] **Step 4: Run tests to verify all pass**

```bash
cd kj-controller && pytest tests/integration/test_sing_admin_routes.py::TestApprove -v
```

Expected: all pass (new duet tests AND pre-existing single-singer tests).

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/integration/test_sing_admin_routes.py
git commit -m "feat(sing): approve_sing_request plumbs partners into singers_json"
```

---

## Task 5: New endpoint `GET /sing/my-requests?ids=…`

**Files:**
- Modify: `kj-controller/sing.py` (new route)
- Test: `kj-controller/tests/integration/test_sing_public_routes.py` (new `TestMyRequests` class)

- [ ] **Step 1: Write failing tests**

Append to `kj-controller/tests/integration/test_sing_public_routes.py`:

```python
class TestMyRequests:
    """`GET /sing/my-requests?ids=...` — multi-song done-screen feed."""

    def _create(self, sing_app, **overrides):
        token = sing_app.sing_store.ensure_token()
        body = {
            "singer_name": "Alice",
            "phone": "",
            "source_type": "local",
            "source_ref": "/tmp/x.mp4",
        }
        body.update(overrides)
        return sing_app.sing_store.create_request(token=token, **body)

    def test_returns_requests_in_requested_order(self, client, sing_app, token):
        r1 = self._create(sing_app, song_artist="Queen", song_title="Bohemian Rhapsody")
        r2 = self._create(sing_app, song_artist="Oasis", song_title="Wonderwall")
        r3 = self._create(sing_app, song_artist="Abba",  song_title="Dancing Queen")
        ids = f"{r2['id']},{r3['id']},{r1['id']}"
        resp = client.get(f"/sing/my-requests?ids={ids}&t={token}")
        assert resp.status_code == 200
        out = resp.get_json()
        assert [r["request"]["id"] for r in out["requests"]] == [
            r2["id"], r3["id"], r1["id"],
        ]
        # now_playing is present and structurally sane (may be empty).
        assert "now_playing" in out

    def test_drops_unknown_ids_silently(self, client, sing_app, token):
        r1 = self._create(sing_app)
        resp = client.get(f"/sing/my-requests?ids=999999,{r1['id']}&t={token}")
        assert resp.status_code == 200
        ids = [r["request"]["id"] for r in resp.get_json()["requests"]]
        assert ids == [r1["id"]]

    def test_drops_foreign_token_rows(self, client, sing_app, token):
        # Create a row, then rotate the token; the old row's token no longer matches.
        r1 = self._create(sing_app)
        sing_app.sing_store.regenerate_token()
        new_token = sing_app.sing_store.get_token()
        resp = client.get(f"/sing/my-requests?ids={r1['id']}&t={new_token}")
        assert resp.status_code == 200
        assert resp.get_json()["requests"] == []

    def test_requires_token(self, client, sing_app):
        r1 = self._create(sing_app)
        resp = client.get(f"/sing/my-requests?ids={r1['id']}")
        assert resp.status_code == 403

    def test_caps_ids_count(self, client, token):
        too_many = ",".join(str(i) for i in range(21))
        resp = client.get(f"/sing/my-requests?ids={too_many}&t={token}")
        assert resp.status_code == 400

    def test_empty_ids_returns_empty_list(self, client, token):
        resp = client.get(f"/sing/my-requests?ids=&t={token}")
        assert resp.status_code == 200
        assert resp.get_json()["requests"] == []

    def test_estimate_present_when_linked(self, client, sing_app, token):
        r1 = self._create(sing_app)
        # Approve so a linked entry exists.
        from routes import approve_sing_request
        entry_id = approve_sing_request(sing_app, sing_app.sing_store.get_request(r1["id"]))
        sing_app.sing_store.mark_approved(r1["id"], linked_entry_id=entry_id)
        resp = client.get(f"/sing/my-requests?ids={r1['id']}&t={token}")
        item = resp.get_json()["requests"][0]
        assert "estimate" in item
        assert item["estimate"]["position"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd kj-controller && pytest tests/integration/test_sing_public_routes.py::TestMyRequests -v
```

Expected: all FAIL (route does not exist, returns 404).

- [ ] **Step 3: Implement the route**

In `kj-controller/sing.py`, add **after** the existing `status` route (around line 638) and **before** `_build_now_playing`:

```python
_MY_REQUESTS_MAX_IDS = 20


@sing_bp.route("/my-requests", methods=["GET"])
@require_token
def my_requests():
    """Multi-id status feed for the singer's 'your night' done screen.

    Returns the requested ids in order, dropping unknown ids and ids whose
    stored token differs from the current event token (cross-event safety,
    matches /sing/status). `now_playing` is included once at the top level so
    the done screen doesn't need a second round trip to populate the header.
    """
    store = current_app.sing_store
    raw = request.args.get("ids", "") or ""
    pieces = [p for p in raw.split(",") if p.strip()]
    if len(pieces) > _MY_REQUESTS_MAX_IDS:
        return jsonify({"error": f"max {_MY_REQUESTS_MAX_IDS} ids per call"}), 400
    try:
        ids = [int(p) for p in pieces]
    except ValueError:
        return jsonify({"error": "ids must be integers"}), 400

    token = _extract_token()
    rotation_mgr = getattr(current_app, "rotation", None)

    entries = []
    now_playing_dict = {"now_singing": None, "up_next": None, "queued_count": 0}
    if rotation_mgr is not None:
        entries, _active, now_playing_dict = _build_now_playing(rotation_mgr)

    out = []
    for rid in ids:
        req = store.get_request(rid)
        if req is None or req.get("token") != token:
            continue
        item = {"request": _public_request_view(req)}
        if req.get("linked_entry_id") and entries:
            estimate = compute_estimate(
                entries, req["linked_entry_id"], current_app.kj_config,
            )
            item["estimate"] = estimate
        out.append(item)

    return jsonify({"now_playing": now_playing_dict, "requests": out})
```

- [ ] **Step 4: Run tests to verify all pass**

```bash
cd kj-controller && pytest tests/integration/test_sing_public_routes.py::TestMyRequests -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/sing.py kj-controller/tests/integration/test_sing_public_routes.py
git commit -m "feat(sing): GET /sing/my-requests for multi-song done screen"
```

---

## Task 6: Admin approval card shows duet partners

**Files:**
- Modify: `kj-controller/static/app.js` (admin request-card render)
- Modify: `kj-controller/static/style.css` (small block styling)
- Test: `kj-controller/tests/integration/test_sing_admin_routes.py` (verify list endpoint exposes partners — admin UI is vanilla JS without unit harness, so coverage is via the JSON API)

- [ ] **Step 1: Verify the list endpoint already surfaces partners**

The admin endpoint `GET /rotation/requests` returns `store.list_requests()` raw rows. After Task 2's `_row_to_dict` change, every row already deserialises `additional_singers`. Add a defensive test:

Append to `kj-controller/tests/integration/test_sing_admin_routes.py`:

```python
class TestListRequestsExposesPartners:
    def test_list_includes_additional_singers(self, admin_client, admin_app):
        admin_app.sing_store.create_request(
            singer_name="Alice", phone="", source_type="local",
            source_ref="/tmp/x.mp4",
            additional_singers=[{"name": "Sarah", "phone": "+61 400 111 222"}],
        )
        resp = admin_client.get("/rotation/requests?status=pending")
        assert resp.status_code == 200
        rows = resp.get_json()["requests"]
        assert any(
            r.get("additional_singers") == [{"name": "Sarah", "phone": "+61 400 111 222"}]
            for r in rows
        )
```

- [ ] **Step 2: Run test to verify it passes**

```bash
cd kj-controller && pytest tests/integration/test_sing_admin_routes.py::TestListRequestsExposesPartners -v
```

Expected: PASS (no implementation change yet — Task 2's `_row_to_dict` already deserialises).

- [ ] **Step 3: Locate the admin request-card renderer**

```bash
grep -nE 'singer_name|sing_request|requests-list|renderRequest' kj-controller/static/app.js | head -20
```

The output should reveal which function builds the per-request DOM. Read its surrounding lines.

- [ ] **Step 4: Render the duet partners block**

Inside whichever function builds the request card (it'll be one that already renders `req.singer_name` and `req.song_title`), add a partners block. Conventionally just below the song info, before the approve/reject controls. Insert this exact snippet at the appropriate spot (adapt variable names to the local style — most likely `req` / `r`):

```javascript
  if (Array.isArray(req.additional_singers) && req.additional_singers.length > 0) {
    const block = document.createElement('div');
    block.className = 'request-duet-partners';
    const title = document.createElement('div');
    title.className = 'request-duet-title';
    title.textContent = `👥 Singing with ${req.additional_singers.length} other${req.additional_singers.length === 1 ? '' : 's'}`;
    block.appendChild(title);
    const list = document.createElement('ul');
    list.className = 'request-duet-list';
    for (const p of req.additional_singers) {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.className = 'duet-name';
      name.textContent = p.name;
      li.appendChild(name);
      if (p.phone) {
        const phone = document.createElement('a');
        phone.className = 'duet-phone';
        phone.href = `sms:${p.phone}`;
        phone.textContent = p.phone;
        li.appendChild(phone);
      } else {
        const noPhone = document.createElement('span');
        noPhone.className = 'duet-no-phone';
        noPhone.textContent = '(no phone)';
        li.appendChild(noPhone);
      }
      list.appendChild(li);
    }
    block.appendChild(list);
    // Append into the card body — adapt the parent element name to match
    // the surrounding render function (e.g. `cardBody`, `wrapper`).
    cardBody.appendChild(block);
  }
```

If the render function uses a different DOM-construction idiom (e.g. template strings), translate this block to the local style. Do NOT switch idioms.

- [ ] **Step 5: Add CSS**

Append to `kj-controller/static/style.css`:

```css
/* Duet-partner block on admin approval cards (sing requests). */
.request-duet-partners {
  margin: 6px 0;
  padding: 6px 10px;
  background: rgba(255, 91, 184, 0.08);
  border-left: 3px solid #ff5bb8;
  border-radius: 4px;
  font-size: 0.92em;
}
.request-duet-title {
  font-weight: 600;
  margin-bottom: 4px;
}
.request-duet-list {
  margin: 0;
  padding-left: 18px;
}
.request-duet-list li {
  display: flex;
  gap: 10px;
  align-items: baseline;
}
.duet-name { font-weight: 500; }
.duet-phone { color: #ff5bb8; text-decoration: none; }
.duet-phone:hover { text-decoration: underline; }
.duet-no-phone { color: #888; font-style: italic; }
```

- [ ] **Step 6: Manual smoke**

Start the dev server (no service restart on prod — local only):

```bash
cd kj-controller && python app.py
```

Open `http://localhost:8000/`, open browser devtools, run in the JS console:

```js
fetch('/rotation/requests/config').then(r => r.json()).then(d => console.log(d.token))
```

Then in a separate tab, POST a duet via `/sing/submit` (curl):

```bash
TOKEN=<the-token-printed-above>
curl -s -X POST "http://localhost:8000/sing/submit?t=$TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"singer_name":"Alice","phone":"","song_artist":"Queen","song_title":"Bohemian Rhapsody","source_type":"local","source_ref":"/tmp/x.mp4","additional_singers":[{"name":"Sarah B.","phone":"+61 400 111 222"},{"name":"Mike","phone":""}]}'
```

Refresh the admin tab; confirm the pending-requests panel shows the duet block under Alice's request, with Sarah's phone as a clickable `sms:` link and Mike as `(no phone)`.

Stop the dev server (`Ctrl+C`).

- [ ] **Step 7: Commit**

```bash
git add kj-controller/static/app.js kj-controller/static/style.css kj-controller/tests/integration/test_sing_admin_routes.py
git commit -m "feat(admin): show duet partners on sing-request approval card"
```

---

## Task 7: Singer UI — partners section on confirm screen

**Files:**
- Modify: `kj-controller/static-sing/sing.js` (`state`, `renderConfirm`, `send` payload)
- Modify: `kj-controller/static-sing/sing.css` (partner rows styling)
- Test: `kj-controller/tests/e2e/test_sing_frontend.py` (new file — Playwright)

The singer UI has no unit harness, so we drive Playwright through the existing e2e fixtures. If there is already a singer-side e2e file, append to it instead of creating new.

- [ ] **Step 1: Check for an existing singer e2e file**

```bash
ls kj-controller/tests/e2e/ | grep -i sing
```

If none exists, create `kj-controller/tests/e2e/test_sing_frontend.py`. If one exists (e.g. `test_sing_e2e.py`), use that.

- [ ] **Step 2: Inspect available Playwright fixtures**

```bash
sed -n '1,80p' kj-controller/tests/e2e/conftest.py
```

You'll need either a `page` + `live_server` pair (like `test_frontend.py`) or a higher-level `sing_page(token=...)` helper. Use whichever the file already provides.

- [ ] **Step 3: Write failing e2e tests**

Append to (or create) `kj-controller/tests/e2e/test_sing_frontend.py`:

```python
"""End-to-end tests for the public /sing/* singer UI."""

import json

from playwright.sync_api import expect


def _login(page, live_server, token):
    """Land on the singer SPA with a valid token; return after first render."""
    page.goto(f"{live_server}/sing/?t={token}")
    expect(page.locator("#sing-root")).to_be_visible()


def _seed_identity(page, name="Alice"):
    """Skip identity step by injecting localStorage."""
    page.evaluate(f"localStorage.setItem('sing_name', '{name}')")
    page.evaluate("localStorage.setItem('sing_phone', '')")


class TestConfirmPartners:
    def test_partners_section_starts_collapsed(self, page, live_server, token):
        _seed_identity(page)
        _login(page, live_server, token)
        # The simplest way to land on confirm: simulate state by injecting
        # selected song then triggering re-render. Tests use a small JS
        # bridge added in renderConfirm() (data-testid) — see Step 4.
        page.evaluate("""
            window.__sing_state.selected = {
                source_type: 'local',
                source_ref: '/tmp/x.mp4',
                song_artist: 'Queen', song_title: 'Bohemian Rhapsody',
                label: 'Bohemian Rhapsody — Queen (in library)',
            };
            window.__sing_state.step = 'confirm';
            window.__sing_render();
        """)
        # Add-singer button is visible; partner inputs are not yet.
        expect(page.locator('[data-testid="add-singer"]')).to_be_visible()
        expect(page.locator('[data-testid="partner-row"]')).to_have_count(0)

    def test_can_add_up_to_three_partners(self, page, live_server, token):
        _seed_identity(page)
        _login(page, live_server, token)
        page.evaluate("""
            window.__sing_state.selected = { source_type: 'local',
                source_ref: '/tmp/x.mp4',
                song_artist: 'Q', song_title: 'B', label: 'X' };
            window.__sing_state.step = 'confirm';
            window.__sing_render();
        """)
        for _ in range(3):
            page.locator('[data-testid="add-singer"]').click()
        expect(page.locator('[data-testid="partner-row"]')).to_have_count(3)
        # 4th add button is hidden.
        expect(page.locator('[data-testid="add-singer"]')).to_be_hidden()

    def test_submit_sends_partners(self, page, live_server, sing_app, token):
        _seed_identity(page)
        _login(page, live_server, token)
        # Capture the POST body.
        captured = {}
        def handle(route):
            captured['body'] = route.request.post_data_json
            route.continue_()
        page.route('**/sing/submit*', handle)

        page.evaluate("""
            window.__sing_state.selected = { source_type: 'local',
                source_ref: '/tmp/x.mp4',
                song_artist: 'Queen', song_title: 'Bohemian Rhapsody',
                label: 'X' };
            window.__sing_state.step = 'confirm';
            window.__sing_render();
        """)
        page.locator('[data-testid="add-singer"]').click()
        page.locator('[data-testid="partner-name-0"]').fill('Sarah B.')
        page.locator('[data-testid="partner-phone-0"]').fill('+61 400 111 222')
        page.locator('.submit-btn').click()
        # Wait for navigation to done state.
        expect(page.locator('text=Your songs tonight')).to_be_visible(timeout=5000)
        assert captured['body']['additional_singers'] == [
            {"name": "Sarah B.", "phone": "+61 400 111 222"},
        ]
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
cd kj-controller && pytest tests/e2e/test_sing_frontend.py -v
```

Expected: all FAIL — `data-testid` attributes don't exist; the test-bridge globals (`__sing_state`, `__sing_render`) don't exist; the confirm screen has no add-singer UI.

- [ ] **Step 5: Add the test bridge to `sing.js`**

In `kj-controller/static-sing/sing.js`, near the bottom (just before `renderRulesFooter();`), expose minimal hooks for tests:

```javascript
// Test bridge — only used by Playwright e2e tests. Cheap to leave in
// production: two globals on the window object, no behaviour change.
if (typeof window !== 'undefined') {
  window.__sing_state = state;
  window.__sing_render = render;
}
```

- [ ] **Step 6: Add `additional` to `state` and reset paths**

In `kj-controller/static-sing/sing.js`, update the `state` literal (around line 30):

```javascript
const state = {
  step: "landing",
  name: LS.get("sing_name"),
  phone: LS.get("sing_phone"),
  query: "",
  selected: null,
  makeArtist: "",
  makeTitle: "",
  // New: duet partners typed on the confirm screen. Array of
  // {name, phone}. Capped at MAX_PARTNERS in the render.
  additional: [],
  request: null,
  status: null,
  rotationCache: null,
  makeRequestsEnabled: INITIAL_MAKE_REQUESTS_ENABLED,
};

const MAX_PARTNERS = 3;
```

- [ ] **Step 7: Rewrite `renderConfirm` to host the partners section**

In `kj-controller/static-sing/sing.js`, replace `renderConfirm` (around line 936). Keep the existing send/error handling intact:

```javascript
function renderConfirm() {
  let submitting = false;
  let err = "";

  function rerender() {
    root.innerHTML = "";
    root.appendChild(renderConfirm());
  }

  const send = async () => {
    if (submitting) return;
    submitting = true; err = "";
    const submitBtn = root.querySelector(".submit-btn");
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Sending…";
    }
    try {
      // Normalise partners — drop rows where the name is blank.
      const cleaned = state.additional
        .map((p) => ({
          name: (p.name || "").trim(),
          phone: (p.phone || "").trim(),
        }))
        .filter((p) => p.name.length > 0);

      // Per-row phone format check — surface the first error inline.
      for (let i = 0; i < cleaned.length; i++) {
        const ph = cleaned[i].phone;
        if (ph && !PHONE_RE.test(ph)) {
          err = `Partner ${i + 1}: phone format looks off.`;
          submitting = false;
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = "Send to KJ";
          }
          const errEl = root.querySelector(".error");
          if (errEl) errEl.textContent = err;
          return;
        }
      }

      const payload = {
        singer_name: state.name,
        phone: state.phone,
        song_artist: state.selected.song_artist || "",
        song_title: state.selected.song_title || "",
        source_type: state.selected.source_type,
        source_ref: state.selected.source_ref,
        source_meta: state.selected.source_meta || null,
      };
      if (cleaned.length > 0) payload.additional_singers = cleaned;

      const data = await submit(payload);
      state.request = data.request;
      // Remember this request id on this device (per token) so the done
      // screen's "your songs tonight" list survives reloads.
      rememberRequestId(TOKEN, data.request.id);
      state.step = "done";
      render();
    } catch (e) {
      err = e.status === 429
        ? "You've submitted a lot — please wait a few minutes."
        : "Couldn't send — ask the KJ if requests are paused.";
      submitting = false;
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Send to KJ";
      }
      const errEl = root.querySelector(".error");
      if (errEl) errEl.textContent = err;
    }
  };

  function renderPartnersSection() {
    const wrap = el("div", { class: "partners-section" },
      el("div", { class: "partners-title" }, "Singing with anyone else? (optional)"),
    );
    state.additional.forEach((p, i) => {
      wrap.appendChild(el("div", {
        class: "partner-row",
        "data-testid": "partner-row",
      },
        el("input", {
          type: "text",
          placeholder: "Name (e.g. Sarah B.)",
          value: p.name || "",
          "data-testid": `partner-name-${i}`,
          oninput: (e) => { state.additional[i].name = e.target.value; },
        }),
        el("input", {
          type: "tel",
          placeholder: "Phone (optional)",
          value: p.phone || "",
          "data-testid": `partner-phone-${i}`,
          oninput: (e) => { state.additional[i].phone = e.target.value; },
        }),
        el("button", {
          type: "button",
          class: "partner-remove",
          "aria-label": "Remove",
          onclick: () => { state.additional.splice(i, 1); rerender(); },
        }, "×"),
      ));
    });
    if (state.additional.length < MAX_PARTNERS) {
      wrap.appendChild(el("button", {
        type: "button",
        class: "partners-add",
        "data-testid": "add-singer",
        onclick: () => {
          state.additional.push({ name: "", phone: "" });
          rerender();
        },
      }, state.additional.length === 0 ? "+ Add a singer" : "+ Add another singer"));
    } else {
      wrap.appendChild(el("div", { class: "partners-cap-hint" },
        "That's the max — 4 singers total."));
    }
    return wrap;
  }

  return el("main", { class: "sing-card" },
    el("h2", {}, "Looking good?"),
    el("div", { class: "pick-summary" },
      el("div", { class: "pick-label" }, state.selected?.label || ""),
    ),
    el("p", { class: "hint" },
      state.phone
        ? `Your details: ${state.name} · ${state.phone}`
        : `Your details: ${state.name}`),
    renderPartnersSection(),
    el("div", { class: "row" },
      el("button", { class: "btn ghost", onclick: back("search") }, "Change"),
      el("button", { class: "btn primary submit-btn", onclick: send }, "Send to KJ"),
    ),
    el("p", { class: "error" }, err),
  );
}
```

- [ ] **Step 8: Add a localStorage helper for the request-id list**

In `kj-controller/static-sing/sing.js`, just below the `LS` helper (around line 24), add:

```javascript
// Tracks the singer's submitted request ids for the current token so the
// done screen can render a multi-song "your night" list. We scope by token
// so that yesterday's ids don't leak into tonight's event.
const MY_REQUESTS_KEY = "sing_my_request_ids";

function _readMyRequestStore() {
  try {
    const raw = localStorage.getItem(MY_REQUESTS_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (obj && typeof obj === "object" && Array.isArray(obj.ids)) return obj;
    return null;
  } catch { return null; }
}

function rememberRequestId(token, id) {
  if (!token || !id) return;
  let store = _readMyRequestStore();
  if (!store || store.token !== token) store = { token, ids: [] };
  if (!store.ids.includes(id)) store.ids.push(id);
  try { localStorage.setItem(MY_REQUESTS_KEY, JSON.stringify(store)); }
  catch { /* private browsing — best-effort */ }
}

function readMyRequestIds(token) {
  const store = _readMyRequestStore();
  if (!store || store.token !== token) return [];
  return store.ids.slice();
}
```

- [ ] **Step 9: Add partner-section CSS**

Append to `kj-controller/static-sing/sing.css`:

```css
/* Duet partners on confirm screen. */
.partners-section {
  margin: 14px 0;
  padding: 10px 12px;
  background: rgba(255, 77, 207, 0.06);
  border: 1px solid rgba(255, 77, 207, 0.18);
  border-radius: 6px;
}
.partners-title {
  font-size: 0.95em;
  color: #ddd;
  margin-bottom: 8px;
}
.partner-row {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 6px;
  margin-bottom: 6px;
}
.partner-row input {
  font-size: 0.95em;
  padding: 8px;
}
.partner-remove {
  background: transparent;
  border: 1px solid #555;
  color: #ccc;
  width: 36px;
  border-radius: 4px;
  cursor: pointer;
}
.partner-remove:hover { background: #2a2a2a; color: #fff; }
.partners-add {
  background: transparent;
  border: 1px dashed #ff4dcf;
  color: #ff4dcf;
  padding: 8px 12px;
  border-radius: 4px;
  cursor: pointer;
  width: 100%;
}
.partners-add:hover { background: rgba(255, 77, 207, 0.08); }
.partners-cap-hint {
  font-size: 0.85em;
  color: #888;
  font-style: italic;
}
```

- [ ] **Step 10: Run tests to verify they pass**

```bash
cd kj-controller && pytest tests/e2e/test_sing_frontend.py::TestConfirmPartners -v
```

Expected: all pass. If the `test_submit_sends_partners` test fails on the "Your songs tonight" assertion (because the multi-song done screen lands in Task 8), comment that assertion line with `# TODO: enable after Task 8` and re-enable in Task 8. Do NOT delete the test.

- [ ] **Step 11: Commit**

```bash
git add kj-controller/static-sing/sing.js kj-controller/static-sing/sing.css kj-controller/tests/e2e/test_sing_frontend.py
git commit -m "feat(sing): duet-partner section on confirm screen (max 3)"
```

---

## Task 8: Singer UI — multi-song done screen + "Request another song"

**Files:**
- Modify: `kj-controller/static-sing/sing.js` (`renderDone`, replace `pollStatus`)
- Modify: `kj-controller/static-sing/sing.css` (per-song cards)
- Test: `kj-controller/tests/e2e/test_sing_frontend.py` (new `TestDoneMultiSong` class)

- [ ] **Step 1: Write failing e2e tests**

Append to `kj-controller/tests/e2e/test_sing_frontend.py`:

```python
class TestDoneMultiSong:
    def _submit_one(self, sing_app, song="Wonderwall"):
        token = sing_app.sing_store.ensure_token()
        return sing_app.sing_store.create_request(
            singer_name="Alice", phone="",
            song_artist="Oasis", song_title=song,
            source_type="local", source_ref="/tmp/x.mp4",
            token=token,
        )

    def test_done_lists_all_submitted_songs(self, page, live_server, sing_app, token):
        r1 = self._submit_one(sing_app, song="Wonderwall")
        r2 = self._submit_one(sing_app, song="Don't Look Back in Anger")
        # Seed the localStorage as if both were submitted from this browser.
        page.goto(f"{live_server}/sing/?t={token}")
        page.evaluate(
            "localStorage.setItem('sing_my_request_ids', "
            f"JSON.stringify({{'token': '{token}', 'ids': [{r1['id']}, {r2['id']}]}}))"
        )
        page.evaluate("localStorage.setItem('sing_name', 'Alice')")
        page.evaluate("localStorage.setItem('sing_phone', '')")
        # Land on done by setting state and re-rendering.
        page.evaluate(f"""
            window.__sing_state.request = {{ id: {r1['id']} }};
            window.__sing_state.step = 'done';
            window.__sing_render();
        """)
        # Both songs appear.
        expect(page.locator("text=Wonderwall")).to_be_visible()
        expect(page.locator("text=Don't Look Back in Anger")).to_be_visible()
        # 'Request another song' button is visible.
        expect(page.locator('[data-testid="request-another"]')).to_be_visible()

    def test_request_another_returns_to_search(self, page, live_server, sing_app, token):
        r1 = self._submit_one(sing_app)
        page.goto(f"{live_server}/sing/?t={token}")
        page.evaluate(
            "localStorage.setItem('sing_my_request_ids', "
            f"JSON.stringify({{'token': '{token}', 'ids': [{r1['id']}]}}))"
        )
        page.evaluate("localStorage.setItem('sing_name', 'Alice')")
        page.evaluate(f"""
            window.__sing_state.request = {{ id: {r1['id']} }};
            window.__sing_state.step = 'done';
            window.__sing_render();
        """)
        page.locator('[data-testid="request-another"]').click()
        # Now on the search screen — identity preserved.
        expect(page.locator('input[type="search"]')).to_be_visible()
        # Name lingers (so the "Hi Alice — search" line shows).
        expect(page.locator("text=Hi Alice")).to_be_visible()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd kj-controller && pytest tests/e2e/test_sing_frontend.py::TestDoneMultiSong -v
```

Expected: all FAIL — no `[data-testid="request-another"]`, no multi-song list.

- [ ] **Step 3: Replace `renderDone` and `pollStatus`**

In `kj-controller/static-sing/sing.js`, replace `renderDone` (around line 987) and `pollStatus` (around line 1009) with:

```javascript
async function fetchMyRequests(ids) {
  if (!ids || !ids.length) {
    return { now_playing: { now_singing: null, up_next: null, queued_count: 0 }, requests: [] };
  }
  const q = ids.join(",");
  const resp = await fetch(
    `${BASE}/my-requests?ids=${encodeURIComponent(q)}&t=${encodeURIComponent(TOKEN)}`,
    { credentials: "same-origin" },
  );
  if (!resp.ok) {
    const err = new Error("my-requests fetch failed");
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}

function _statusLine(item) {
  // Returns the human-friendly status string for a per-song card.
  const req = item.request;
  if (req.status === "rejected") {
    return "The KJ needs to talk to you — see them at the desk.";
  }
  if (req.status === "pending") return "Waiting for KJ to approve…";
  const est = item.estimate;
  if (!est) return "Added to the queue.";
  if (est.now_singing) return "🎤 You're up — break a leg!";
  if (est.position === 1) return "🎤 You're next — head to the mic";
  if (est.position === 2) return "About 1 song to go";
  if (est.position >= 3) {
    const low = Math.round(est.range_low_s / 60);
    const high = Math.round(est.range_high_s / 60);
    return `You're #${est.position} — about ${low}–${high} min`;
  }
  return "Added to the queue.";
}

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
    card.appendChild(el("div", { class: "song-card-partners" },
      `with ${names}`));
  }
  return card;
}

function renderDone() {
  // Show a multi-song "your night" view backed by /sing/my-requests.
  const card = el("main", { class: "sing-card" },
    renderNowPlaying(),
    el("h2", {}, "Your songs tonight"),
    el("div", { class: "songs-list" }, "Loading your songs…"),
    el("button", {
      class: "btn primary request-another",
      "data-testid": "request-another",
      onclick: () => {
        // Reset song-picking state; preserve identity.
        state.selected = null;
        state.makeArtist = "";
        state.makeTitle = "";
        state.additional = [];
        state.step = "search";
        render();
      },
    }, "+ Request another song"),
    el("div", { id: "push-optin", class: "push-optin" }),
    el("details", { class: "upcoming" },
      el("summary", {}, "Show upcoming singers"),
      el("div", { class: "queue-list" }, "Open to load…"),
    ),
    el("p", { class: "hint" },
      "Keep this page open — it'll update automatically. Good luck!"),
  );

  setTimeout(maybeShowPushPrompt, 2000);
  pollMyRequests(card);
  // The rotation expander still uses the existing /sing/rotation path.
  card.querySelector(".upcoming").addEventListener("toggle", async (e) => {
    if (!e.target.open) return;
    const slot = card.querySelector(".queue-list");
    if (slot.dataset.loaded === "1") return;
    slot.textContent = "Loading…";
    try {
      const payload = await fetchRotation();
      slot.dataset.loaded = "1";
      slot.replaceWith(_renderRotationBody({ ...payload, _fetchedAt: Date.now() }));
    } catch {
      slot.textContent = "Couldn't load — try closing and reopening.";
    }
  });
  return card;
}

async function pollMyRequests(card) {
  if (state._statusPollTimer) {
    clearInterval(state._statusPollTimer);
    state._statusPollTimer = null;
  }

  const tick = async () => {
    const ids = readMyRequestIds(TOKEN);
    // Always include the just-submitted request id if it's not in storage yet
    // (e.g. localStorage write failed silently in private browsing).
    if (state.request?.id && !ids.includes(state.request.id)) {
      ids.unshift(state.request.id);
    }
    try {
      const data = await fetchMyRequests(ids);
      onPollSuccess();
      // Update now-playing widget at the top of the card.
      const npNode = card.querySelector(".now-playing");
      if (npNode) updateNowPlaying(npNode, data.now_playing);
      // Render the per-song list.
      const slot = card.querySelector(".songs-list");
      if (slot) {
        slot.innerHTML = "";
        if (!data.requests.length) {
          slot.appendChild(el("p", { class: "hint" },
            "No songs yet — tap 'Request another song' below."));
        } else {
          for (const item of data.requests) slot.appendChild(_renderSongCard(item));
        }
      }
    } catch {
      onPollFailure();
    }
  };

  tick();
  state._statusPollTimer = setInterval(tick, 15000);
}
```

- [ ] **Step 4: Add per-song-card CSS**

Append to `kj-controller/static-sing/sing.css`:

```css
/* Multi-song done-screen list. */
.songs-list { margin: 10px 0; }
.song-card {
  margin: 8px 0;
  padding: 12px;
  background: rgba(255, 255, 255, 0.04);
  border-left: 3px solid #ff4dcf;
  border-radius: 4px;
}
.song-card[data-status="rejected"] { border-left-color: #ff6655; }
.song-card[data-status="pending"]  { border-left-color: #888; }
.song-card-title { font-weight: 600; margin-bottom: 4px; }
.song-card-status { font-size: 0.92em; color: #ccc; }
.song-card-partners {
  font-size: 0.85em;
  color: #ff4dcf;
  margin-top: 4px;
}
.request-another {
  width: 100%;
  margin: 12px 0;
}
```

- [ ] **Step 5: Restore the TODO-commented assertion from Task 7**

Edit `kj-controller/tests/e2e/test_sing_frontend.py` to un-comment the `expect(page.locator('text=Your songs tonight')).to_be_visible()` assertion (if it was commented during Task 7 Step 10).

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd kj-controller && pytest tests/e2e/test_sing_frontend.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add kj-controller/static-sing/sing.js kj-controller/static-sing/sing.css kj-controller/tests/e2e/test_sing_frontend.py
git commit -m "feat(sing): multi-song done screen + request-another-song button"
```

---

## Task 9: House rules footer copy

**Files:**
- Modify: `kj-controller/static-sing/sing.js` (`renderRulesFooter`)

- [ ] **Step 1: Add a new bullet to the short list and a new section to the full list**

In `kj-controller/static-sing/sing.js`, locate `renderRulesFooter` (around line 1233). In the `<ul class="rules-short">`, add a new `<li>` for duets after "Multiple songs? We'll spread them out":

```javascript
      el("li", {}, "Duets welcome — add partners on the confirm screen"),
```

And in the `<ol class="rules-list">`, add a new `<li>` block between "Multiple songs welcome" and "Need to leave early?":

```javascript
        el("li", {},
          el("h4", {}, "Duets welcome"),
          el("p", {}, "Singing with friends? On the 'Looking good?' screen "
            + "before sending the request, tap '+ Add a singer' to attach "
            + "up to 3 extra people. We'll list everyone on the rotation "
            + "so the KJ knows who to call up. Phone numbers for extras "
            + "are optional — they just help the KJ text them when you're "
            + "close to the front of the queue."),
        ),
```

- [ ] **Step 2: Manual smoke**

Start the dev server briefly:

```bash
cd kj-controller && python app.py
```

Open `http://localhost:8000/sing/?t=$TOKEN` (using the token from earlier), expand the "Read the full rules" expander, confirm the new section reads correctly. Stop the server.

- [ ] **Step 3: Commit**

```bash
git add kj-controller/static-sing/sing.js
git commit -m "docs(sing): rules-footer copy mentions duet partners"
```

---

## Task 10: Full test suite + manual end-to-end smoke

**Files:** none (verification only).

- [ ] **Step 1: Run the full unit + integration suite**

```bash
cd kj-controller && pytest tests/unit tests/integration -v
```

Expected: all pass. Total runtime: usually < 60s.

- [ ] **Step 2: Run the full e2e suite**

```bash
cd kj-controller && pytest tests/e2e -v
```

Expected: all pass. Slower (Playwright). If any flaky failure happens, re-run the failing test once before investigating.

- [ ] **Step 3: Manual smoke against the dev server**

Start the server:

```bash
cd kj-controller && python app.py
```

Capture the token:

```bash
curl -s http://localhost:8000/rotation/requests/config | python -c "import sys, json; print(json.load(sys.stdin)['token'])"
```

Open `http://localhost:8000/sing/?t=<TOKEN>` in a browser. Step through the flow:

1. Enter the name "Alice", skip phone, continue.
2. Search for "Bohemian Rhapsody", pick a version (any).
3. On the confirm screen, add two duet partners — "Sarah B." with a fake phone, "Mike" with no phone.
4. Submit.
5. Done screen: confirm the song appears in the list with "with Sarah B., Mike" sub-line.
6. Tap "+ Request another song". You land on the search screen.
7. Submit a second song (no partners). Done screen now lists both.
8. Open the admin tab at `http://localhost:8000/`. The pending-requests panel shows the partners block under Alice's first request, with Sarah's phone as a clickable `sms:` link.

If anything looks off, fix and commit before continuing. If everything works, stop the server.

- [ ] **Step 4: Final commit (only if there were fixes in Step 3)**

```bash
git status
# If clean, skip this step.
git add -p   # review changes per-hunk
git commit -m "fix: address findings from manual smoke"
```

- [ ] **Step 5: Hand off — do NOT push or deploy**

The branch is `feat/sess-20260514-2226-singer-add-songs-duets`. The user will run their own `/test-review`, `/docs-review`, `/coderabbit`, `/pr` workflow to ship. **Do not `git push` from this implementation session.**

---

## Spec coverage cross-check

| Spec section | Implemented in |
|---|---|
| Data model: `sing_requests.additional_singers` column | Task 1 |
| Constraints (≤ 3, name required, phone format) | Task 3 |
| `approve_sing_request` passes `singers=` | Task 4 |
| `_public_request_view` returns `additional_singers` | Task 3 (step 5) |
| Singer UI confirm-screen partners | Task 7 |
| Singer UI done screen multi-song + "Request another song" | Task 8 |
| `GET /sing/my-requests?ids=…` | Task 5 |
| KJ admin approval card duet block | Task 6 |
| Rules-footer copy | Task 9 |
| localStorage `sing_my_request_ids` per-token scoping | Task 7 (helper) + Task 8 (consumer) |
| Migration safety (idempotent ALTER) | Task 1 |
| Tests: store round-trip, /submit validation, approve, /my-requests, frontend e2e | Tasks 1, 2, 3, 4, 5, 7, 8, 10 |

All spec sections accounted for.
