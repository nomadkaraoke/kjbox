"""E2E: dedicated right-rail Requests section (never shifts Rotation).

See docs/archive/2026-07-02-requests-right-rail-design.md.
"""

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


def _set_config(live_server, **fields):
    req = urllib.request.Request(
        f"{live_server}/rotation/requests/config",
        data=json.dumps(fields).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req) as r:
        r.read()


def _clear_rate_limits():
    """The public /sing/submit endpoint rate-limits to 5/IP/300s. Tests run in
    the same process as the live server, so we can reset the in-memory sliding
    window between submit-heavy tests rather than tripping a 429."""
    import sing

    sing._rate_limit_state.clear()
    sing._validate_rate_limit_state.clear()


class TestRequestsSectionPlacement:
    @pytest.fixture(autouse=True)
    def _reset(self, live_server):
        yield
        _set_config(live_server, enabled=True)

    def test_panel_exists_and_is_in_col2(self, app_page):
        expect(app_page.locator("#requests-panel")).to_be_visible()
        in_col2 = app_page.evaluate("!!document.querySelector('#col2 #requests-panel')")
        assert in_col2, "Requests panel must live inside #col2"

    def test_panel_is_after_preview_before_kn_search(self, app_page):
        order = app_page.evaluate(
            """() => [...document.querySelectorAll('#col2 > .container')]
                .map(e => e.id || e.className)"""
        )
        pv = next(i for i, v in enumerate(order) if v == "vnc-preview-container")
        rq = next(i for i, v in enumerate(order) if v == "requests-panel")
        kn = next(i for i, v in enumerate(order) if "kn-search-section" in v)
        assert pv < rq < kn, f"col2 order wrong: {order}"

    def test_old_requests_button_and_inline_panel_removed(self, app_page):
        assert app_page.locator(".rotation-requests-btn").count() == 0
        assert app_page.locator("#pending-count-badge").count() == 0
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
        _set_config(live_server, enabled=True)

    def test_dot_green_when_enabled(self, page, live_server):
        _set_config(live_server, enabled=True)
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
        _set_config(live_server, enabled=False)
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
        _set_config(live_server, enabled=True)

    def _rotation_box(self, page):
        return page.evaluate(
            "() => { const r = document.querySelector('.rotation-panel')"
            ".getBoundingClientRect(); return {top: r.top, left: r.left}; }"
        )

    def test_rotation_unmoved_when_request_arrives_advanced(self, page, live_server, live_token):
        _clear_rate_limits()
        page.set_viewport_size({"width": 1400, "height": 900})
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        before = self._rotation_box(page)
        _submit(live_server, live_token, name="Bob")
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
        _set_config(live_server, enabled=True)

    def test_queue_is_capped_and_scrollable(self, page, live_server, live_token):
        """The list is height-capped and scrollable, so a growing queue can
        never push the sections below it — the whole point of the section."""
        _clear_rate_limits()
        n = 5  # the /sing/submit per-IP cap; enough to populate the queue
        for i in range(n):
            _submit(live_server, live_token, name=f"S{i}", title=f"Song {i}")
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            f"document.querySelectorAll('#pending-requests-list .pending-req-row').length >= {n}",
            timeout=8000,
        )
        metrics = page.evaluate(
            "() => { const l = document.querySelector('#pending-requests-list');"
            " const cs = getComputedStyle(l);"
            " return {overflowY: cs.overflowY, maxHeight: cs.maxHeight,"
            " client: l.clientHeight,"
            " count: document.querySelector('#pending-requests-count').textContent,"
            " rows: document.querySelectorAll('#pending-requests-list .pending-req-row').length}; }"
        )
        # The session-scoped server may carry pending requests from earlier
        # tests, so assert "at least our n" rather than an exact total.
        assert int(metrics["count"]) >= n, metrics
        assert metrics["rows"] == int(metrics["count"]), metrics  # list renders all pending
        # Capped + scrollable → cannot grow unbounded and shove siblings down.
        assert metrics["overflowY"] == "auto", metrics
        assert metrics["maxHeight"] == "260px", metrics
        assert metrics["client"] <= 260, metrics


class TestSimpleModeRail:
    @pytest.fixture(autouse=True)
    def _restore_advanced(self, live_server):
        yield
        _set_config(live_server, simple_mode=False)

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
