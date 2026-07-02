# Requests → dedicated right-rail section — Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax. Execute task-by-task; run tests between tasks.

**Goal:** Move singer requests out of the Rotation container into a permanent right-rail Requests section so Rotation never shifts when a request arrives.

**Architecture:** Relocate Screen Preview to the top of `#col2` so `#col2` is the right rail in both modes; add the Requests section directly below it. This lets a single static DOM placement be correct in advanced and simple mode, and lets us delete the Simple-mode grid special-case. Frontend-only (HTML template + CSS + one JS module). Tested via the existing live-Flask + Playwright e2e harness.

**Tech Stack:** Jinja template (`templates/index.html`), vanilla JS (`static/app.js`), CSS (`static/style.css`), pytest + Playwright (`tests/e2e/`).

## Global Constraints

- kjbox has **no pytest CI** (security workflow only) — tests are local-only; run `python -m pytest` (via `rtk proxy python -m pytest` if rtk mangles it).
- Section-header buttons **must** live inside `.header-actions` and stay classless-friendly (guarded by `TestHeaderButtonConsistency`). No bespoke per-button size CSS.
- Health dots use base `.yt-health-dot` + modifier `.yt-dot-ok` (green `#22c55e`) — never `green/yellow/red` class names.
- Bump `pyproject.toml` version so `?v={{ app_version }}` cache-busts.
- Preserve the shipped Simple-mode visual (Playback+Rotation left, Screen Preview top-right) — guarded by `TestSimpleModeLayout`.

**Reference:** design doc `docs/archive/2026-07-02-requests-right-rail-design.md`.

---

### Task 1: Failing e2e tests for the Requests section

**Files:**
- Create: `kj-controller/tests/e2e/test_requests_panel.py`

**Interfaces:**
- Consumes: `live_server`, `live_token`, `page` fixtures (from `tests/e2e/conftest.py`); request creation via `POST {live_server}/sing/submit?t={token}`; mode toggle via `POST {live_server}/rotation/requests/config` `{"simple_mode": bool}`.
- Produces: the behavioral contract the implementation must satisfy.

- [ ] **Step 1: Write the failing tests**

```python
"""E2E: dedicated right-rail Requests section (never shifts Rotation)."""

import json
import urllib.request

import pytest
from playwright.sync_api import expect


def _submit(live_server, live_token, name="Alice", title="Wonderwall", artist="Oasis"):
    body = {
        "singer_name": name, "phone": "",
        "song_artist": artist, "song_title": title,
        "source_type": "local", "source_ref": "/tmp/x.mp4",
    }
    req = urllib.request.Request(
        f"{live_server}/sing/submit?t={live_token}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["request"]


def _set_enabled(live_server, enabled):
    req = urllib.request.Request(
        f"{live_server}/rotation/requests/config",
        data=json.dumps({"enabled": enabled}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req) as r:
        r.read()


class TestRequestsSectionPlacement:
    @pytest.fixture(autouse=True)
    def _reset(self, live_server):
        yield
        _set_enabled(live_server, True)

    def test_panel_exists_and_is_in_col2(self, app_page):
        panel = app_page.locator("#requests-panel")
        expect(panel).to_be_visible()
        in_col2 = app_page.evaluate(
            "!!document.querySelector('#col2 #requests-panel')"
        )
        assert in_col2, "Requests panel must live inside #col2"

    def test_panel_is_after_preview_before_kn_search(self, app_page):
        order = app_page.evaluate(
            """() => {
                const ids = [...document.querySelectorAll('#col2 > .container')]
                    .map(e => e.id || e.className);
                return ids;
            }"""
        )
        # Screen Preview first, Requests second, KN search after.
        pv = order.index("vnc-preview-container")
        rq = order.index("requests-panel")
        kn = order.findIndex if False else next(
            i for i, v in enumerate(order) if "kn-search-section" in v
        )
        assert pv < rq < kn, f"col2 order wrong: {order}"

    def test_old_requests_button_and_inline_panel_removed(self, app_page):
        assert app_page.locator(".rotation-requests-btn").count() == 0
        assert app_page.locator("#pending-count-badge").count() == 0
        # The inline panel must no longer be a child of the rotation panel.
        inside_rotation = app_page.evaluate(
            "!!document.querySelector('.rotation-panel #pending-requests-panel')"
        )
        assert not inside_rotation

    def test_settings_button_opens_modal(self, app_page):
        app_page.locator("#requests-panel .header-actions button").click()
        expect(app_page.locator("#sing-requests-modal")).to_be_visible()


class TestRequestsStatusDot:
    @pytest.fixture(autouse=True)
    def _reset(self, live_server):
        yield
        _set_enabled(live_server, True)

    def test_dot_green_when_enabled(self, page, live_server):
        _set_enabled(live_server, True)
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "document.querySelector('#requests-status-dot')?.classList.contains('yt-dot-ok')"
        )
        color = page.evaluate(
            "getComputedStyle(document.querySelector('#requests-status-dot')).backgroundColor"
        )
        assert color == "rgb(34, 197, 94)"

    def test_dot_grey_when_disabled(self, page, live_server):
        _set_enabled(live_server, False)
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "document.querySelector('#requests-status-dot') && "
            "!document.querySelector('#requests-status-dot').classList.contains('yt-dot-ok')"
        )
        color = page.evaluate(
            "getComputedStyle(document.querySelector('#requests-status-dot')).backgroundColor"
        )
        assert color == "rgb(68, 68, 68)"  # base #444


class TestRotationInvariance:
    """The core guarantee: a request arriving must not move Rotation."""

    @pytest.fixture(autouse=True)
    def _reset(self, live_server):
        yield
        _set_enabled(live_server, True)

    def _rotation_box(self, page):
        return page.evaluate(
            "() => { const r = document.querySelector('.rotation-panel')"
            ".getBoundingClientRect(); return {top: r.top, left: r.left}; }"
        )

    def test_rotation_unmoved_when_request_arrives_advanced(self, page, live_server, live_token):
        page.set_viewport_size({"width": 1400, "height": 900})
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        before = self._rotation_box(page)
        _submit(live_server, live_token, name="Bob")
        # Wait for the 5s SingRequests poll to surface it.
        page.wait_for_function(
            "document.querySelectorAll('#pending-requests-list .pending-req-row').length >= 1",
            timeout=8000,
        )
        after = self._rotation_box(page)
        assert abs(before["top"] - after["top"]) < 1, (before, after)
        assert abs(before["left"] - after["left"]) < 1, (before, after)


class TestQueueGrowth:
    @pytest.fixture(autouse=True)
    def _reset(self, live_server):
        yield
        _set_enabled(live_server, True)

    def test_many_requests_scroll_not_grow(self, page, live_server, live_token):
        page.set_viewport_size({"width": 1400, "height": 900})
        for i in range(6):
            _submit(live_server, live_token, name=f"S{i}", title=f"Song {i}")
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "document.querySelectorAll('#pending-requests-list .pending-req-row').length >= 6",
            timeout=8000,
        )
        metrics = page.evaluate(
            "() => { const l = document.querySelector('#pending-requests-list');"
            " return {scroll: l.scrollHeight, client: l.clientHeight,"
            " count: document.querySelector('#pending-requests-count').textContent}; }"
        )
        assert metrics["scroll"] > metrics["client"], "list should scroll, not grow unbounded"
        assert metrics["count"] == "6"


class TestSimpleModeRail:
    @pytest.fixture(autouse=True)
    def _restore_advanced(self, live_server):
        yield
        req = urllib.request.Request(
            f"{live_server}/rotation/requests/config",
            data=json.dumps({"simple_mode": False}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            resp.read()

    def _enter_simple(self, page, live_server):
        page.set_viewport_size({"width": 1400, "height": 900})
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        page.locator("#mode-seg-simple").click()
        page.wait_for_function("document.body.classList.contains('simple-mode')")

    def test_requests_below_preview_in_right_rail(self, page, live_server):
        self._enter_simple(page, live_server)
        pos = page.evaluate(
            "() => { const pv = document.querySelector('#vnc-preview-container').getBoundingClientRect();"
            " const rq = document.querySelector('#requests-panel').getBoundingClientRect();"
            " const pc = document.querySelector('.playback-controls').getBoundingClientRect();"
            " return {reqBelowPreview: rq.top >= pv.top - 2,"
            " reqToRight: rq.left >= pc.right - 2, reqVisible: rq.width > 0}; }"
        )
        assert pos["reqVisible"], "Requests panel must be visible in simple mode"
        assert pos["reqToRight"], "Requests should be in the right rail"
        assert pos["reqBelowPreview"], "Requests should sit below Screen Preview"
```

> Note: `test_panel_is_after_preview_before_kn_search` uses a Python `next(...)` for the KN index (the inline `findIndex if False else` guard keeps it valid Python; simplify to a plain generator during implementation if preferred).

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `cd kj-controller && rtk proxy python -m pytest tests/e2e/test_requests_panel.py -q`
Expected: FAIL — `#requests-panel` / `#requests-status-dot` don't exist yet.

- [ ] **Step 3: Commit the failing tests**

```bash
git add kj-controller/tests/e2e/test_requests_panel.py
git commit -m "test: failing e2e for dedicated right-rail Requests section"
```

---

### Task 2: Template — relocate Screen Preview, add Requests section, remove old trigger

**Files:**
- Modify: `kj-controller/templates/index.html`

**Interfaces:**
- Produces DOM ids/classes the JS + CSS + tests depend on: `#requests-panel`, `#requests-status-dot`, `#pending-requests-count`, `#pending-requests-list`, `#pending-requests-empty`.

- [ ] **Step 1: Remove the old Requests button from the Rotation header**

In the `.rotation-header-btns` group, delete this line:

```html
<button class="rotation-requests-btn" onclick="openSingRequestsModal()" title="Manage public request form (QR, token, kill switch)">Requests <span id="pending-count-badge" class="pending-count-badge hidden">0</span></button>
```

- [ ] **Step 2: Remove the inline pending panel from `.rotation-panel`**

Delete this block (currently between the rotation header and `#rotation-add-form`):

```html
<div id="pending-requests-panel" class="pending-requests-panel hidden">
    <div class="pending-requests-header">
        <strong id="pending-requests-title">Pending Requests</strong>
        <span id="pending-requests-count" class="pending-requests-count">0</span>
    </div>
    <div id="pending-requests-list"></div>
</div>
```

- [ ] **Step 3: Cut Screen Preview (toolbar + container) from `#col1`**

Cut the two adjacent blocks `#vnc-max-toolbar` (`<div id="vnc-max-toolbar" …>…</div>`) and the whole `<div class="container" id="vnc-preview-container">…</div>` from `#col1` (they currently sit between the Overlays panel and the System panel).

- [ ] **Step 4: Paste them as the first children of `#col2`, followed by the new Requests section**

Immediately inside `<div class="column" id="col2">`, before `.kn-search-section`, paste the cut `#vnc-max-toolbar` + `#vnc-preview-container`, then add:

```html
<div class="container requests-panel" id="requests-panel">
    <div class="header-row">
        <h2>Requests <span id="requests-status-dot" class="yt-health-dot" title="Public request form status"></span></h2>
        <div class="header-actions">
            <span id="pending-requests-count" class="pending-requests-count">0</span>
            <button onclick="openSingRequestsModal()" title="Request form settings — QR code, kill switch, SMS">Settings</button>
        </div>
    </div>
    <div id="pending-requests-list" class="pending-requests-list"></div>
    <div id="pending-requests-empty" class="pending-requests-empty">No pending requests — singers submit via the QR code.</div>
</div>
```

- [ ] **Step 5: Sanity-check the template renders**

Run: `cd kj-controller && rtk proxy python -c "from app import create_app"` (import smoke) — expect no error. Full render is exercised by the e2e run in Task 5.

- [ ] **Step 6: Commit**

```bash
git add kj-controller/templates/index.html
git commit -m "feat: relocate Screen Preview to col2 + add Requests section markup"
```

---

### Task 3: CSS — delete simple-mode grid hack, styles, dot, mobile order

**Files:**
- Modify: `kj-controller/static/style.css`

- [ ] **Step 1: Remove `#col2` from the simple-mode hide list**

In the `body.simple-mode … { display: none !important }` selector list, delete the `body.simple-mode #col2,` line only (keep the six individual col2 sections + `.overlay-panel`). This makes `#col2` visible in simple mode while its advanced-only sections stay hidden.

- [ ] **Step 2: Delete the simple-mode grid special-case**

Remove these rules inside the `@media (min-width: 769px)` simple-mode block:

```css
body.simple-mode .main-layout { grid-template-columns: 1fr; max-width: 100%; }
body.simple-mode #col1 {
    max-width: none; width: 100%; display: grid;
    grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
    grid-template-areas: "playback preview" "rotation preview";
    align-items: start; gap: 0.75rem;
}
body.simple-mode .rotation-panel       { grid-area: rotation; }
body.simple-mode #vnc-preview-container { grid-area: preview; }
body.simple-mode .playback-controls    { grid-area: playback; }
```

Keep the mode-independent `.playback-controls { display: grid; … }` compact-bar rules that follow (they apply in both modes). Update the explanatory comment block to note Screen Preview now lives in `#col2` in both modes.

- [ ] **Step 3: Add Requests panel styles**

Append near the other section styles:

```css
/* Requests — permanent right-rail section (col2). Capped + scrolling so a
 * busy queue never pushes the sections below it. */
.pending-requests-list {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    max-height: 260px;      /* ~3–4 rows, then scroll */
    overflow-y: auto;
}
.pending-requests-list:empty { display: none; }
.pending-requests-list::-webkit-scrollbar { width: 6px; }
.pending-requests-list::-webkit-scrollbar-track { background: transparent; }
.pending-requests-list::-webkit-scrollbar-thumb { background: #2a2a2a; border-radius: 3px; }
.pending-requests-list::-webkit-scrollbar-thumb:hover { background: #ff5bb8; }
.pending-requests-count {
    font-size: 0.8em;
    color: #aaa;
    min-width: 1.4em;
    text-align: center;
}
.pending-requests-empty {
    color: #777;
    font-size: 0.85em;
    padding: 0.25rem 0;
}
```

- [ ] **Step 4: Add the mobile order for the Requests panel (renumber the sequence)**

In the `@media (max-width: 768px)` block, replace the section-order list so `.requests-panel` follows Rotation and the tail renumbers cleanly (keeps `available-songs` < `system-controls`):

```css
    .playback-controls  { order: 1; }
    .rotation-panel     { order: 2; }
    .requests-panel     { order: 3; }
    .available-songs    { order: 4; }
    .kn-search-section  { order: 5; }
    .db-search-section  { order: 6; }
    .yt-search-section  { order: 7; }
    .download-section   { order: 8; }
    .overlay-panel      { order: 9; }
    .browser-mode-panel { order: 10; }
    #vnc-preview-container { order: 11; }
    .system-controls    { order: 12; }
```

- [ ] **Step 5: Commit**

```bash
git add kj-controller/static/style.css
git commit -m "style: right-rail Requests section + delete simple-mode grid hack"
```

---

### Task 4: JS — permanent panel, empty state, status dot, retargeted badge

**Files:**
- Modify: `kj-controller/static/app.js` (`SingRequests` module)

**Interfaces:**
- Consumes: `#pending-requests-list`, `#pending-requests-empty`, `#pending-requests-count`, `#requests-status-dot`; `config.enabled`, `pending[]`.

- [ ] **Step 1: Rewrite `renderPanel()` to a permanent panel with empty state**

Replace the existing `renderPanel()` with:

```javascript
    function renderPanel() {
        const list = document.getElementById('pending-requests-list');
        const empty = document.getElementById('pending-requests-empty');
        const count = document.getElementById('pending-requests-count');
        if (!list) return;
        if (count) count.textContent = String(pending.length);
        if (empty) empty.style.display = pending.length ? 'none' : '';
        list.innerHTML = '';
        for (const req of pending) {
            list.appendChild(renderRow(req));
        }
    }
```

- [ ] **Step 2: Retarget `updateBadge()` and add `updateStatusDot()`**

Replace `updateBadge()` with a count+dot updater and a dot helper:

```javascript
    function updateBadge(count) {
        const el = document.getElementById('pending-requests-count');
        if (el) el.textContent = String(count || 0);
    }

    function updateStatusDot() {
        const dot = document.getElementById('requests-status-dot');
        if (!dot) return;
        dot.classList.toggle('yt-dot-ok', !!config.enabled);
        dot.title = config.enabled
            ? 'Public request form: ON'
            : 'Public request form: OFF';
    }
```

- [ ] **Step 3: Call `updateStatusDot()` from `fetchConfig()`**

In `fetchConfig()`, after `applyConfigToModal();`, add `updateStatusDot();`.

- [ ] **Step 4: Run the new e2e tests**

Run: `cd kj-controller && rtk proxy python -m pytest tests/e2e/test_requests_panel.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat: permanent Requests panel — empty state + status dot"
```

---

### Task 5: Full regression run + version bump

**Files:**
- Modify: `kj-controller/pyproject.toml` (version)

- [ ] **Step 1: Run the full e2e + integration suite**

Run: `cd kj-controller && rtk proxy python -m pytest tests/e2e tests/integration -q`
Expected: PASS. If `TestSimpleModeLayout` / `TestResponsiveLayout.test_mobile_section_order` / `TestHeaderButtonConsistency` fail, fix per the design (they should stay green — preview still top-right in col2; available-songs order 4 < system 12; Settings button classless inside `.header-actions`).

- [ ] **Step 2: Bump the version for cache-bust**

Increment the patch/minor in `kj-controller/pyproject.toml` `version = "…"` (e.g. `0.58.0` → `0.59.0`).

- [ ] **Step 3: Manual visual check (advanced + simple)**

Drive the live harness with Playwright (or the running app) at 1400px: confirm (advanced) col2 = Preview → Requests → Search KN…, Rotation doesn't move when a request is POSTed; (simple) left = Playback+Rotation, right rail = Preview then Requests. Capture screenshots to the scratchpad.

- [ ] **Step 4: Commit**

```bash
git add kj-controller/pyproject.toml
git commit -m "chore: bump version for Requests section cache-bust"
```

---

## Self-Review

**Spec coverage:**
- Dedicated right-rail section, top of col2 (advanced) / below preview (simple) → Tasks 2, 3; tests `TestRequestsSectionPlacement`, `TestSimpleModeRail`. ✓
- Settings button + green dot → Task 2 (button), Task 4 (`updateStatusDot`); tests `test_settings_button_opens_modal`, `TestRequestsStatusDot`. ✓
- Requests appear there, Rotation never moves → Task 2 (remove inline panel); test `TestRotationInvariance`. ✓
- Multiple requests queue (cap + scroll) → Task 3 CSS; test `TestQueueGrowth`. ✓
- Delete simple-mode grid hack / unify col2 → Task 3 Steps 1–2; guarded by `TestSimpleModeLayout` (unchanged, behavior-based). ✓
- Mobile order → Task 3 Step 4; guarded by `test_mobile_section_order`. ✓

**Placeholder scan:** none — all steps carry concrete code/edits.

**Type consistency:** id/class names consistent across HTML (`#requests-panel`, `#requests-status-dot`, `#pending-requests-count`, `#pending-requests-list`, `#pending-requests-empty`), CSS, JS, and tests. `updateStatusDot`/`updateBadge`/`renderPanel` names consistent with existing module.
