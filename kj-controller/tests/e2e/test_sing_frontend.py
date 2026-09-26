"""End-to-end tests for the public /sing/* singer UI."""

import json
import re
from urllib.parse import unquote

from playwright.sync_api import expect


def _login(page, live_server, live_token, name="Alice"):
    """Land on the singer SPA with a valid token, seed identity, and wait for render.

    localStorage must be set after navigation (browser security requires a
    same-origin page to be loaded first).
    """
    page.goto(f"{live_server}/sing/?t={live_token}")
    expect(page.locator("#sing-root")).to_be_visible()
    # Seed identity so the SPA skips the name/phone step.
    page.evaluate("(n) => localStorage.setItem('sing_name', n)", name)
    page.evaluate("localStorage.setItem('sing_phone', '')")
    # Refresh state from localStorage (sing.js reads LS at module load time;
    # update the live state object so the confirm screen picks up the name).
    page.evaluate("window.__sing_state.name = localStorage.getItem('sing_name') || ''")
    page.evaluate("window.__sing_state.phone = ''")


class TestConfirmPartners:
    def test_partners_section_starts_collapsed(self, page, live_server, live_token):
        _login(page, live_server, live_token)
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
        expect(page.locator('[data-testid="add-singer"]')).to_be_visible()
        expect(page.locator('[data-testid="partner-row"]')).to_have_count(0)

    def test_can_add_up_to_three_partners(self, page, live_server, live_token):
        _login(page, live_server, live_token)
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
        expect(page.locator('[data-testid="add-singer"]')).to_be_hidden()

    def test_can_remove_a_partner_row(self, page, live_server, live_token):
        """Tapping the × removes the row and re-opens the add affordance."""
        _login(page, live_server, live_token)
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
        # Remove the first row; cap should release.
        page.locator('.partner-remove').first.click()
        expect(page.locator('[data-testid="partner-row"]')).to_have_count(2)
        expect(page.locator('[data-testid="add-singer"]')).to_be_visible()

    def test_submit_sends_partners(self, page, live_server, live_token):
        _login(page, live_server, live_token)
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
        expect(page.locator('text=Your songs tonight')).to_be_visible(timeout=5000)
        assert captured['body']['additional_singers'] == [
            {"name": "Sarah B.", "phone": "+61 400 111 222"},
        ]


class TestDoneMultiSong:
    def _submit_one(self, live_server, live_token, song="Wonderwall"):
        """Create a request via the live server's HTTP API.
        Returns the parsed request dict."""
        import urllib.request as _ur
        body = {
            "singer_name": "Alice", "phone": "",
            "song_artist": "Oasis", "song_title": song,
            "source_type": "local", "source_ref": "/tmp/x.mp4",
        }
        data = json.dumps(body).encode()
        req = _ur.Request(
            f"{live_server}/sing/submit?t={live_token}",
            data=data, headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _ur.urlopen(req) as r:
            return json.loads(r.read())["request"]

    def test_done_lists_all_submitted_songs(self, page, live_server, live_token):
        r1 = self._submit_one(live_server, live_token, song="Wonderwall")
        r2 = self._submit_one(live_server, live_token, song="Don't Look Back in Anger")
        page.goto(f"{live_server}/sing/?t={live_token}")
        page.evaluate(
            "(payload) => localStorage.setItem('sing_my_request_ids', JSON.stringify(payload))",
            {"token": live_token, "ids": [r1["id"], r2["id"]]},
        )
        page.evaluate("localStorage.setItem('sing_name', 'Alice')")
        page.evaluate("localStorage.setItem('sing_phone', '')")
        page.evaluate(
            "(rid) => { window.__sing_state.request = {id: rid}; window.__sing_state.step = 'done'; window.__sing_render(); }",
            r1["id"],
        )
        # Scoped to the card — the shell status tile also names the next song.
        expect(page.locator("#sing-root >> text=Wonderwall")).to_be_visible()
        expect(page.locator("#sing-root >> text=Don't Look Back in Anger")).to_be_visible()
        expect(page.locator('[data-testid="request-another"]')).to_be_visible()

    def test_request_another_returns_to_search(self, page, live_server, live_token):
        r1 = self._submit_one(live_server, live_token)
        page.goto(f"{live_server}/sing/?t={live_token}")
        page.evaluate(
            "(payload) => localStorage.setItem('sing_my_request_ids', JSON.stringify(payload))",
            {"token": live_token, "ids": [r1["id"]]},
        )
        page.evaluate("localStorage.setItem('sing_name', 'Alice')")
        page.evaluate(
            "(rid) => { window.__sing_state.request = {id: rid}; window.__sing_state.step = 'done'; window.__sing_render(); }",
            r1["id"],
        )
        page.locator('[data-testid="request-another"]').click()
        expect(page.locator('input[type="search"]')).to_be_visible()
        expect(page.locator("text=Hi Alice")).to_be_visible()


class TestSearchRace:
    def _goto_search(self, page, live_server, live_token, name="Alice"):
        _login(page, live_server, live_token, name=name)
        page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")

    # Deterministic in-browser fetch shim: control BOTH the body and the
    # resolution delay per query, so we can force an out-of-order
    # (stale-after-fresh) response without depending on real network timing or
    # on how Playwright serialises route handlers.
    _FETCH_SHIM = r"""
        window.__searchScript = {};
        window.__searchEvents = { started: [], resolved: [] };
        const _origFetch = window.fetch;
        window.fetch = (url, opts) => {
          const m = /\/sing\/search\?q=([^&]*)/.exec(String(url));
          if (m) {
            const q = decodeURIComponent(m[1]).trim();
            const s = window.__searchScript[q];
            if (s) {
              window.__searchEvents.started.push(q);
              return new Promise((resolve) => setTimeout(() => {
                window.__searchEvents.resolved.push(q);
                resolve(new Response(JSON.stringify(s.body),
                  {status: 200, headers: {'Content-Type': 'application/json'}}));
              }, s.delay));
            }
          }
          return _origFetch(url, opts);
        };
    """

    def test_stale_response_does_not_clobber_newer(self, page, live_server, live_token):
        """An earlier, slower query's response must not overwrite a newer one."""
        page.add_init_script(self._FETCH_SHIM)
        self._goto_search(page, live_server, live_token)
        page.evaluate("""() => {
          window.__searchScript = {
            'aaa':  {delay: 1500, body: {songs: [{key: 'q:1', artist: 'Queen', title: 'SLOW STALE',
                       version_count: 1, versions: [{source: 'local',
                       local: {path: '/x', artist: 'Queen', title: 'SLOW STALE'}}]}]}},
            'aaab': {delay: 0,    body: {songs: [{key: 'a:1', artist: 'ABBA', title: 'FAST FRESH',
                       version_count: 1, versions: [{source: 'local',
                       local: {path: '/y', artist: 'ABBA', title: 'FAST FRESH'}}]}]}},
          };
        }""")
        inp = page.locator('input[type="search"]')
        inp.fill("aaa")
        # Wait until the 'aaa' request has actually started (debounce fired) before
        # superseding it — otherwise the debounce would simply cancel it and the
        # out-of-order race would never be exercised (a false pass).
        page.wait_for_function("() => window.__searchEvents.started.includes('aaa')")
        inp.fill("aaab")             # supersede while 'aaa' is genuinely in flight
        expect(page.locator(".r-title")).to_have_text("FAST FRESH")   # fresh lands first
        # Wait until the slow, stale 'aaa' response has actually resolved (landed last).
        page.wait_for_function("() => window.__searchEvents.resolved.includes('aaa')")
        expect(page.locator(".r-title")).to_have_text("FAST FRESH")   # guard must discard the stale one
        expect(page.locator(".results")).not_to_contain_text("SLOW STALE")

    def test_typing_shows_searching_immediately(self, page, live_server, live_token):
        """The 'Searching…' hint appears on keystroke, before the 700ms debounce fires."""
        # The immediate hint comes from the oninput handler, before any fetch.
        page.route("**/sing/search*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps({"songs": []})))
        self._goto_search(page, live_server, live_token)
        page.locator('input[type="search"]').fill("bohemian")
        expect(page.locator(".results .hint")).to_have_text("Searching…", timeout=400)

    def test_pick_is_inert_briefly_after_render(self, page, live_server, live_token):
        """A freshly rendered pick button is inert (anti-mis-tap), then arms."""
        body = {"songs": [{"key": "q:1", "artist": "Queen", "title": "Bo Rhap",
                           "version_count": 1,
                           "versions": [{"source": "local",
                                         "local": {"path": "/x", "artist": "Queen",
                                                   "title": "Bo Rhap"}}]}]}
        page.route("**/sing/search*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)))
        # A huge cooldown makes the "inert" window deterministic (no wall-clock race).
        page.add_init_script("window.__SING_ARM_MS = 100000;")
        self._goto_search(page, live_server, live_token)
        page.locator('input[type="search"]').fill("queen")
        expect(page.locator(".btn-primary-cta")).to_be_visible()
        page.locator(".btn-primary-cta").click()   # within the cooldown → ignored
        assert page.evaluate("window.__sing_state.step") == "search"
        # Arm immediately and re-render; the auto-search re-populates and the tap works.
        page.evaluate("window.__SING_ARM_MS = 0; window.__sing_render();")
        expect(page.locator(".btn-primary-cta")).to_be_visible()
        page.locator(".btn-primary-cta").click()
        assert page.evaluate("window.__sing_state.step") == "confirm"


class TestConfirmHardening:
    def _confirm(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        page.evaluate("""
            window.__sing_state.query = 'bohemian';
            window.__sing_state.selected = {
                source_type: 'local', source_ref: '/x.mp4',
                song_artist: 'Queen', song_title: 'Bohemian Rhapsody',
                label: 'Bohemian Rhapsody — Queen (in library)',
            };
            window.__sing_state.step = 'confirm';
            window.__sing_render();
        """)

    def test_confirm_shows_song_source_and_breadcrumb(self, page, live_server, live_token):
        self._confirm(page, live_server, live_token)
        expect(page.locator(".confirm-title")).to_have_text("Bohemian Rhapsody")
        expect(page.locator(".confirm-artist")).to_have_text("Queen")
        expect(page.locator(".confirm-source")).to_have_text(
            "On the host's machine — definitely available")
        expect(page.locator(".confirm-searched")).to_contain_text("bohemian")
        expect(page.locator(".submit-btn")).to_have_text("Yes — send it in")
        expect(page.get_by_role("button", name="← Pick a different song")).to_be_visible()


class TestVersionList:
    def _multi(self, page, live_server, live_token):
        # No tap cooldown so version interactions are immediate in this test.
        page.add_init_script("window.__SING_ARM_MS = 0;")
        _login(page, live_server, live_token)
        # 1 community + 3 commercial-online versions; best-first order preserved.
        body = {"songs": [{
            "key": "q:multi", "artist": "Queen", "title": "Bo Rhap",
            "version_count": 4, "in_library": False,
            "versions": [
                {"source": "kn", "priority_stated": True,
                 "kn": {"brand_name": "SongService", "is_community": True}},
                {"source": "kn", "priority_stated": False, "kn": {"brand_name": "BrandA"}},
                {"source": "kn", "priority_stated": False, "kn": {"brand_name": "BrandB"}},
                {"source": "kn", "priority_stated": False, "kn": {"brand_name": "BrandC"}},
            ]}]}
        page.route("**/sing/search*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)))
        page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
        page.locator('input[type="search"]').fill("bo rhap")
        expect(page.locator(".result-row")).to_be_visible()
        page.locator(".sing-versions-toggle").click()

    def test_best_marker_on_first_version(self, page, live_server, live_token):
        self._multi(page, live_server, live_token)
        first_version = page.locator(".sing-version-card").nth(0)
        expect(first_version.locator(".sing-version-best")).to_be_visible()
        expect(page.locator(".sing-version-best")).to_have_count(1)

    def test_noisy_commercial_collapsed_when_good_option_present(self, page, live_server, live_token):
        self._multi(page, live_server, live_token)
        # The 3 commercial-online versions are hidden behind a toggle by default.
        expect(page.locator('[data-testid="online-collapse-toggle"]')).to_be_visible()
        expect(page.locator(
            '.sing-version-section[data-section="online"] .sing-version-card')).to_have_count(0)
        page.locator('[data-testid="online-collapse-toggle"]').click()
        expect(page.locator(
            '.sing-version-section[data-section="online"] .sing-version-card')).to_have_count(3)


class TestSelfServiceCancel:
    def test_cancel_button_shows_and_sends_edit_token(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        # Seed a stored request id + its edit_token, then land on the done screen.
        page.evaluate("""(t) => {
            localStorage.setItem('sing_my_request_ids',
              JSON.stringify({token: t, ids: [4242], tokens: {'4242': 'secret-xyz'}}));
        }""", live_token)
        page.route("**/sing/my-requests*", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({
                "now_playing": {"now_singing": None, "up_next": None, "queued_count": 0},
                "requests": [{"request": {"id": 4242, "singer_name": "Alice",
                    "song_artist": "Queen", "song_title": "Bo Rhap",
                    "source_type": "local", "status": "pending",
                    "created_at": "now", "linked_entry_id": None,
                    "additional_singers": None}}]})))
        page.route("**/sing/requests/4242/cancel", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"success": True, "request": {"id": 4242, "status": "cancelled"}})))
        page.on("dialog", lambda d: d.accept())
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")
        expect(page.locator('[data-testid="cancel-song"]')).to_be_visible()
        with page.expect_request("**/sing/requests/4242/cancel") as req_info:
            page.locator('[data-testid="cancel-song"]').click()
        assert "secret-xyz" in (req_info.value.post_data or "")

    def _done_with(self, page, live_server, live_token, items, ids, tokens=None):
        _login(page, live_server, live_token)
        page.evaluate("(s) => localStorage.setItem('sing_my_request_ids', JSON.stringify(s))",
                      {"token": live_token, "ids": ids, "tokens": tokens or {}})
        def handle(route):
            # Like the server: only the ids the phone still asks about come
            # back (it polls by name even with none left).
            m = re.search(r"[?&]ids=([^&]*)", route.request.url)
            asked = {int(x) for x in unquote(m.group(1) if m else "").split(",") if x}
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "now_playing": {"now_singing": None, "up_next": None, "queued_count": 0},
                "requests": [it for it in items if it["request"]["id"] in asked]}))
        page.route("**/sing/my-requests*", handle)
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")

    @staticmethod
    def _req(rid, title, status, **extra):
        item = {"request": {"id": rid, "singer_name": "Alice", "song_artist": "FOB",
                            "song_title": title, "source_type": "local", "status": status,
                            "created_at": "now", "linked_entry_id": 100 + rid,
                            "additional_singers": None}, "performed": False}
        item.update(extra)
        return item

    def test_cancelled_songs_are_not_listed(self, page, live_server, live_token):
        # Includes the original of a ⇄ Change (the server cancels it on approval).
        self._done_with(page, live_server, live_token, [
            self._req(1, "Dance, Dance", "approved", estimate={"position": 5, "now_singing": False,
                      "range_low_s": 900, "range_high_s": 1260}),
            self._req(2, "Dance, Dance", "cancelled"),
        ], [1, 2])
        expect(page.locator(".song-card")).to_have_count(1)
        expect(page.locator("#sing-root")).not_to_contain_text("Added to the queue.")

    def test_cancel_removes_card_immediately_with_notice(self, page, live_server, live_token):
        self._done_with(page, live_server, live_token,
                        [self._req(7, "Bo Rhap", "pending"), self._req(8, "Other", "pending")],
                        [7, 8], {"7": "tok7", "8": "tok8"})
        expect(page.locator(".song-card")).to_have_count(2)
        # From now on the server reports it cancelled; hang the poll so the
        # card must disappear from local state, not a refetch.
        page.unroute("**/sing/my-requests*")
        page.route("**/sing/my-requests*", lambda r: None)
        page.route("**/sing/requests/7/cancel", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"success": True, "request": {"id": 7, "status": "cancelled"}})))
        page.on("dialog", lambda d: d.accept())
        page.locator(".song-card", has_text="Bo Rhap").locator('[data-testid="cancel-song"]').click()
        expect(page.locator(".song-card")).to_have_count(1)
        expect(page.locator(".reorder-notice")).to_have_text("Cancelled: Bo Rhap — FOB")

    def test_rejected_and_host_removed_can_be_dismissed(self, page, live_server, live_token):
        self._done_with(page, live_server, live_token, [
            self._req(3, "Nope", "rejected"),
            self._req(4, "Gone", "approved", removed=True),
        ], [3, 4], {"3": "t3", "4": "t4"})
        gone = page.locator(".song-card", has_text="Gone")
        expect(gone).to_contain_text("The host took this song off the list")
        expect(gone.locator('[data-testid="cancel-song"]')).to_have_count(0)
        gone.locator('[data-testid="dismiss-song"]').click()
        expect(page.locator(".song-card", has_text="Gone")).to_have_count(0)
        page.locator(".song-card", has_text="Nope").locator('[data-testid="dismiss-song"]').click()
        expect(page.locator(".song-card")).to_have_count(0)
        # Forgotten on this device — never queried again.
        stored = page.evaluate("() => JSON.parse(localStorage.getItem('sing_my_request_ids'))")
        assert stored["ids"] == [] and stored["tokens"] == {}
        # Host-removed songs don't count as live (tab badge / status tile).
        expect(page.locator('[data-testid="mysongs-bar"]')).to_have_count(0)

    def test_no_cancel_button_without_edit_token(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        # Stored id but NO edit_token for it (e.g. a different device / legacy).
        page.evaluate("""(t) => {
            localStorage.setItem('sing_my_request_ids',
              JSON.stringify({token: t, ids: [4242], tokens: {}}));
        }""", live_token)
        page.route("**/sing/my-requests*", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({
                "now_playing": {"now_singing": None, "up_next": None, "queued_count": 0},
                "requests": [{"request": {"id": 4242, "singer_name": "Alice",
                    "song_artist": "Queen", "song_title": "Bo Rhap",
                    "source_type": "local", "status": "pending",
                    "created_at": "now", "linked_entry_id": None,
                    "additional_singers": None}}]})))
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")
        expect(page.locator(".song-card-title")).to_be_visible()
        expect(page.locator('[data-testid="cancel-song"]')).to_have_count(0)


class TestChangeReorderControls:
    def _seed_done(self, page, live_server, live_token, requests, ls_store):
        _login(page, live_server, live_token)
        page.evaluate("(s) => localStorage.setItem('sing_my_request_ids', JSON.stringify(s))", ls_store)
        page.route("**/sing/my-requests*", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({
                "now_playing": {"now_singing": None, "up_next": None, "queued_count": 0},
                "requests": requests})))
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")

    def test_change_button_enters_change_mode(self, page, live_server, live_token):
        req = {"request": {"id": 11, "singer_name": "A", "song_artist": "Q", "song_title": "BR",
                "source_type": "local", "status": "pending", "created_at": "now",
                "linked_entry_id": None, "additional_singers": None}}
        self._seed_done(page, live_server, live_token, [req],
                        {"token": live_token, "ids": [11], "tokens": {"11": "tok11"}})
        expect(page.locator('[data-testid="change-song"]')).to_be_visible()
        page.locator('[data-testid="change-song"]').click()
        assert page.evaluate("window.__sing_state.step") == "search"
        assert page.evaluate("window.__sing_state.changeRequestId") == 11

    def test_drag_reorder_saves_new_order_with_tokens(self, page, live_server, live_token):
        reqs = [
            {"request": {"id": 11, "singer_name": "A", "song_artist": "Q", "song_title": "One",
                "source_type": "local", "status": "approved", "created_at": "now",
                "linked_entry_id": 101, "additional_singers": None},
             "estimate": {"position": 3}},
            {"request": {"id": 12, "singer_name": "A", "song_artist": "Q", "song_title": "Two",
                "source_type": "local", "status": "approved", "created_at": "now",
                "linked_entry_id": 102, "additional_singers": None},
             "estimate": {"position": 5}},
        ]
        self._seed_done(page, live_server, live_token, reqs,
                        {"token": live_token, "ids": [11, 12], "tokens": {"11": "tok11", "12": "tok12"}})
        page.route("**/sing/requests/reorder*", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"success": True, "request": {"id": 99, "status": "pending",
                                                          "source_type": "reorder"}})))
        # Cards themselves carry no ▲▼ buttons any more.
        expect(page.locator('[data-testid="cancel-song"]').first).to_be_visible()
        expect(page.locator('[data-testid="reorder-up"]')).to_have_count(0)
        # Enter drag mode.
        page.locator('[data-testid="reorder-songs"]').click()
        rows = page.locator(".reorder-row")
        expect(rows).to_have_count(2)
        assert rows.nth(0).inner_text().find("One") >= 0
        # Drag the first row's handle below the second row.
        h = page.locator(".reorder-row").nth(0).locator(".reorder-handle")
        h_box = h.bounding_box()
        target = page.locator(".reorder-row").nth(1).bounding_box()
        page.mouse.move(h_box["x"] + h_box["width"] / 2, h_box["y"] + h_box["height"] / 2)
        page.mouse.down()
        end_y = target["y"] + target["height"] + 8
        for step in range(1, 6):
            page.mouse.move(h_box["x"], h_box["y"] + (end_y - h_box["y"]) * step / 5)
        page.mouse.up()
        assert page.locator(".reorder-row").nth(0).inner_text().find("Two") >= 0
        # Save posts the NEW order with both edit tokens.
        with page.expect_request("**/sing/requests/reorder*") as req_info:
            page.locator('[data-testid="reorder-save"]').click()
        body = json.loads(req_info.value.post_data or "{}")
        assert [it["id"] for it in body["items"]] == [12, 11]
        assert {it["edit_token"] for it in body["items"]} == {"tok11", "tok12"}
        # Mode exits with a confirmation notice.
        expect(page.locator(".reorder-notice")).to_contain_text("host will confirm")

    def test_reorder_cancel_restores_list(self, page, live_server, live_token):
        reqs = [
            {"request": {"id": 11, "singer_name": "A", "song_artist": "Q", "song_title": "One",
                "source_type": "local", "status": "approved", "created_at": "now",
                "linked_entry_id": 101, "additional_singers": None},
             "estimate": {"position": 3}},
            {"request": {"id": 12, "singer_name": "A", "song_artist": "Q", "song_title": "Two",
                "source_type": "local", "status": "approved", "created_at": "now",
                "linked_entry_id": 102, "additional_singers": None},
             "estimate": {"position": 5}},
        ]
        self._seed_done(page, live_server, live_token, reqs,
                        {"token": live_token, "ids": [11, 12], "tokens": {"11": "tok11", "12": "tok12"}})
        page.locator('[data-testid="reorder-songs"]').click()
        expect(page.locator(".reorder-row")).to_have_count(2)
        page.locator('[data-testid="reorder-exit"]').click()
        expect(page.locator(".song-card-title").first).to_be_visible()
        expect(page.locator(".reorder-row")).to_have_count(0)


class TestIdentityMatchedSongsUI:
    """KJ-added / partner-requested songs (matched by name, `entry_id` set)
    render in My songs and can be reordered alongside the phone's own songs."""

    OWN = {"request": {"id": 11, "singer_name": "Alice", "song_artist": "Q", "song_title": "Own",
           "source_type": "local", "status": "approved", "created_at": "now",
           "linked_entry_id": 101, "additional_singers": None},
           "estimate": {"position": 3}}
    HOST = {"request": {"id": None, "singer_name": "Alice", "song_artist": "",
            "song_title": "Host Added - X", "source_type": "rotation", "status": "approved",
            "created_at": "now", "linked_entry_id": 202, "additional_singers": None},
            "entry_id": 202, "added_by_host": True, "added_by": None,
            "estimate": {"position": 5}}

    def _seed(self, page, live_server, live_token, seen_urls):
        _login(page, live_server, live_token)
        page.evaluate("(s) => localStorage.setItem('sing_my_request_ids', JSON.stringify(s))",
                      {"token": live_token, "ids": [11], "tokens": {"11": "tok11"}})

        def handle(route):
            seen_urls.append(route.request.url)
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "now_playing": {"now_singing": None, "up_next": None, "queued_count": 0},
                "requests": [self.OWN, self.HOST]}))
        page.route("**/sing/my-requests*", handle)
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")

    def test_host_added_song_shows_with_label_and_no_edit_buttons(self, page, live_server, live_token):
        urls = []
        self._seed(page, live_server, live_token, urls)
        expect(page.locator(".song-card-title")).to_have_count(2)
        expect(page.locator('[data-testid="added-by-line"]')).to_have_text("Added by the host")
        # Only the phone's own song can be cancelled/changed.
        expect(page.locator('[data-testid="cancel-song"]')).to_have_count(1)
        # Identity is the device, never a claimed name.
        assert any("device_id=" in u for u in urls)
        assert not any("name=" in u for u in urls)

    def test_reorder_with_up_button_sends_entry_id_and_name(self, page, live_server, live_token):
        self._seed(page, live_server, live_token, [])
        page.route("**/sing/requests/reorder*", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"success": True, "auto_approved": True,
                             "request": {"id": 99, "status": "approved", "source_type": "reorder"}})))
        page.locator('[data-testid="reorder-songs"]').click()
        expect(page.locator('[data-testid="reorder-instructions"]')).to_contain_text("press and hold")
        rows = page.locator(".reorder-row")
        expect(rows).to_have_count(2)
        # First row can't go up; last can't go down.
        expect(rows.nth(0).locator('[data-testid="reorder-up"]')).to_be_disabled()
        expect(rows.nth(1).locator('[data-testid="reorder-down"]')).to_be_disabled()
        rows.nth(1).locator('[data-testid="reorder-up"]').click()
        expect(page.locator(".reorder-row").nth(0)).to_contain_text("Host Added")
        expect(page.locator(".reorder-row").nth(0).locator(".reorder-num")).to_have_text("1.")
        with page.expect_request("**/sing/requests/reorder*") as req_info:
            page.locator('[data-testid="reorder-save"]').click()
        body = json.loads(req_info.value.post_data or "{}")
        assert body["items"] == [{"entry_id": 202}, {"id": 11, "edit_token": "tok11"}]
        assert body["device_id"] and "name" not in body
        expect(page.locator(".reorder-notice")).to_contain_text("New order saved")


class TestDoneScreenOrderingAndSung:
    """The 'Your songs tonight' list orders by queue position and files sung
    songs under a collapsed 'Already sung' section instead of letting them pile
    up in the active list."""

    def _song(self, rid, title, status="approved", linked=None, estimate=None,
              performed=False):
        item = {"request": {"id": rid, "singer_name": "Alice", "song_artist": "Q",
                "song_title": title, "source_type": "local", "status": status,
                "created_at": "now", "linked_entry_id": linked,
                "additional_singers": None}, "performed": performed}
        if estimate is not None:
            item["estimate"] = estimate
        return item

    def _seed(self, page, live_server, live_token, requests, ls_store):
        _login(page, live_server, live_token)
        page.evaluate("(s) => localStorage.setItem('sing_my_request_ids', JSON.stringify(s))", ls_store)
        page.route("**/sing/my-requests*", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({
                "now_playing": {"now_singing": None, "up_next": None, "queued_count": 0},
                "requests": requests})))
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")

    def test_active_songs_render_in_queue_order(self, page, live_server, live_token):
        # Submission order is [Late, NowUp, Soon] but queue order is
        # NowUp (singing) → Soon (#2) → Late (#5).
        reqs = [
            self._song(11, "Late", linked=101, estimate={"position": 5, "now_singing": False}),
            self._song(12, "NowUp", linked=102, estimate={"position": 1, "now_singing": True}),
            self._song(13, "Soon", linked=103, estimate={"position": 2, "now_singing": False}),
        ]
        self._seed(page, live_server, live_token, reqs,
                   {"token": live_token, "ids": [11, 12, 13], "tokens": {}})
        titles = page.locator(".songs-list .song-card-title")
        expect(titles).to_have_count(3)
        assert titles.all_inner_texts() == ["NowUp — Q", "Soon — Q", "Late — Q"]

    def test_sung_songs_move_to_collapsed_section(self, page, live_server, live_token):
        reqs = [
            self._song(11, "Coming", linked=101, estimate={"position": 2, "now_singing": False}),
            self._song(12, "Sung", linked=102, performed=True),
        ]
        self._seed(page, live_server, live_token, reqs,
                   {"token": live_token, "ids": [11, 12], "tokens": {}})
        # Active list holds only the still-queued song.
        active = page.locator(".songs-list .song-card-title")
        expect(active).to_have_count(1)
        assert active.all_inner_texts() == ["Coming — Q"]
        # Sung song lives in the collapsed section, which reports the count.
        sung = page.locator('[data-testid="sung-section"]')
        expect(sung).to_be_visible()
        expect(sung).to_contain_text("Already sung tonight (1)")
        expect(sung.locator(".song-card-title")).to_have_text("Sung — Q")

    def test_sung_song_has_no_edit_controls(self, page, live_server, live_token):
        # Even though this device owns the edit_token, a performed song is
        # read-only (cancel/change would 409, reorder is meaningless).
        reqs = [self._song(12, "Sung", linked=102, performed=True)]
        self._seed(page, live_server, live_token, reqs,
                   {"token": live_token, "ids": [12], "tokens": {"12": "tok12"}})
        expect(page.locator('[data-testid="sung-section"]')).to_be_visible()
        expect(page.locator('[data-testid="cancel-song"]')).to_have_count(0)
        expect(page.locator('[data-testid="change-song"]')).to_have_count(0)

    def test_no_sung_section_when_nothing_performed(self, page, live_server, live_token):
        reqs = [self._song(11, "Coming", linked=101,
                           estimate={"position": 2, "now_singing": False})]
        self._seed(page, live_server, live_token, reqs,
                   {"token": live_token, "ids": [11], "tokens": {}})
        expect(page.locator(".songs-list .song-card-title")).to_have_count(1)
        expect(page.locator('[data-testid="sung-section"]')).to_have_count(0)


class TestMySongsPersistence:
    """Boot smart-restore + persistent 'My songs' bar (survives page reload)."""

    _NP = {"now_singing": None, "up_next": None, "queued_count": 0}

    def _seed_ls(self, page, live_server, live_token, store):
        """Navigate once (to get an origin), seed identity + stored ids."""
        page.goto(f"{live_server}/sing/?t={live_token}")
        page.evaluate("localStorage.setItem('sing_name', 'Alice')")
        page.evaluate("localStorage.setItem('sing_phone', '')")
        page.evaluate("(s) => localStorage.setItem('sing_my_request_ids', JSON.stringify(s))", store)

    def _route_my_requests(self, page, requests):
        page.route("**/sing/my-requests*", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"now_playing": self._NP, "requests": requests})))

    def _pending_song(self, rid=4242, title="Bo Rhap"):
        return {"request": {"id": rid, "singer_name": "Alice", "song_artist": "Queen",
                            "song_title": title, "source_type": "local", "status": "pending",
                            "created_at": "now", "linked_entry_id": None,
                            "additional_singers": None}}

    def test_reload_restores_to_your_songs(self, page, live_server, live_token):
        # A returning device with a live song for tonight lands straight on the
        # "Your songs tonight" list — not the bare "Request a song" screen.
        self._seed_ls(page, live_server, live_token,
                      {"token": live_token, "ids": [4242], "tokens": {"4242": "secret-xyz"}})
        self._route_my_requests(page, [self._pending_song()])
        page.reload()
        expect(page.locator(".song-card-title")).to_be_visible()
        expect(page.locator("#sing-root >> text=Bo Rhap")).to_be_visible()
        # On My songs the status tile is shown (same component as every tab)
        # but isn't a link — you're already on the list.
        tile = page.locator('[data-testid="mysongs-bar"]')
        expect(tile).to_be_visible()
        assert tile.evaluate("n => n.tagName") == "DIV"

    def test_bar_urgent_names_next_song_and_reopens_list(self, page, live_server, live_token):
        # Nearly-up (position ≤ 3) → the bar appears on any tab, naming the
        # actual next song; tapping returns to the list.
        self._seed_ls(page, live_server, live_token,
                      {"token": live_token, "ids": [4242], "tokens": {"4242": "secret-xyz"}})
        song = self._pending_song()
        song["request"]["status"] = "approved"
        song["estimate"] = {"position": 2, "range_low_s": 200, "range_high_s": 400,
                            "now_singing": False}
        self._route_my_requests(page, [song])
        page.reload()
        expect(page.locator(".song-card-title")).to_be_visible()   # restored to done
        page.locator('[data-testid="request-another"]').click()
        bar = page.locator('[data-testid="mysongs-bar"]')
        expect(bar).to_be_visible()
        expect(bar).to_contain_text("Your next song")
        expect(bar).to_contain_text("Bo Rhap")
        bar.click()
        expect(page.locator(".song-card-title")).to_be_visible()

    def test_bar_hidden_off_rotation_when_not_urgent(self, page, live_server, live_token):
        # A far-off song (#4) doesn't earn bar space on the Request tab; it
        # appears on the Rotation tab (queue-scanning context) with h/m waits.
        self._seed_ls(page, live_server, live_token,
                      {"token": live_token, "ids": [11], "tokens": {"11": "t11"}})
        song = {"request": {"id": 11, "singer_name": "Alice", "song_artist": "Q",
                            "song_title": "One", "source_type": "local", "status": "approved",
                            "created_at": "now", "linked_entry_id": 101, "additional_singers": None},
                "estimate": {"position": 4, "range_low_s": 4500, "range_high_s": 5400,
                             "now_singing": False}}
        self._route_my_requests(page, [song])
        page.route("**/sing/rotation*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps({"entries": []})))
        page.reload()
        expect(page.locator(".song-card-title")).to_be_visible()
        page.locator('[data-testid="request-another"]').click()
        expect(page.locator('[data-testid="mysongs-bar"]')).to_be_hidden()
        page.locator('[data-testid="tab-rotation"]').click()
        bar = page.locator('[data-testid="mysongs-bar"]')
        expect(bar).to_be_visible()
        expect(bar).to_contain_text("#4 in line · ~1h 15m–1h 30m")

    def test_stale_night_prunes_and_stays_on_boot_screen(self, page, live_server, live_token):
        # localStorage still holds last night's ids, but the server night-scopes
        # them out (empty). The singer stays on the boot (search) screen and the
        # dead ids are pruned so the bar never shows a phantom count.
        self._seed_ls(page, live_server, live_token,
                      {"token": live_token, "ids": [9999], "tokens": {"9999": "old"}})
        self._route_my_requests(page, [])
        with page.expect_request("**/sing/my-requests*") as req_info:
            page.reload()
        # The boot probe must actually carry the stored id (contract check).
        assert "ids=9999" in req_info.value.url
        expect(page.locator("h2:has-text('Pick your song')")).to_be_visible()   # boot screen
        expect(page.locator(".song-card-title")).to_have_count(0)
        expect(page.locator('[data-testid="mysongs-bar"]')).to_be_hidden()
        # Stored ids were pruned to empty.
        remaining = page.evaluate(
            "() => JSON.parse(localStorage.getItem('sing_my_request_ids')).ids")
        assert remaining == []

    def test_cancelled_only_stays_on_boot_screen(self, page, live_server, live_token):
        # A device whose only song was cancelled isn't yanked off the boot
        # screen (the bar filters cancelled out too), though the id resolves.
        self._seed_ls(page, live_server, live_token,
                      {"token": live_token, "ids": [7], "tokens": {"7": "t7"}})
        cancelled = {"request": {"id": 7, "singer_name": "Alice", "song_artist": "Q",
                                "song_title": "Gone", "source_type": "local",
                                "status": "cancelled", "created_at": "now",
                                "linked_entry_id": None, "additional_singers": None}}
        self._route_my_requests(page, [cancelled])
        with page.expect_request("**/sing/my-requests*"):
            page.reload()
        expect(page.locator("h2:has-text('Pick your song')")).to_be_visible()
        expect(page.locator('[data-testid="mysongs-bar"]')).to_be_hidden()

    def test_prune_preserves_ids_added_mid_flight(self, page, live_server, live_token):
        # A song submitted while a /my-requests fetch was in flight (its id added
        # to the store after the query snapshot) must NOT be pruned when the
        # response — which never knew about it — comes back. Prune drops only
        # ids that were queried AND not returned.
        self._seed_ls(page, live_server, live_token,
                      {"token": live_token, "ids": [111, 222], "tokens": {"111": "t1", "222": "t2"}})
        result = page.evaluate(
            """(t) => {
                // Queried only [111]; server returned [111]. Id 222 was added
                // mid-flight and was NOT in the queried snapshot.
                window.__sing_pruneRequestIds(t, [111], [111]);
                return window.__sing_readMyRequestIds(t);
            }""", live_token)
        assert 222 in result and 111 in result

    def test_prune_drops_queried_but_unreturned(self, page, live_server, live_token):
        # The prior-night case: an id we asked about that the server night-scoped
        # out is dropped.
        self._seed_ls(page, live_server, live_token,
                      {"token": live_token, "ids": [111, 999], "tokens": {"111": "t1", "999": "old"}})
        result = page.evaluate(
            """(t) => {
                window.__sing_pruneRequestIds(t, [111, 999], [111]);
                return window.__sing_readMyRequestIds(t);
            }""", live_token)
        assert result == [111]

    def test_no_bar_and_no_restore_without_songs(self, page, live_server, live_token):
        # A known-name device with no stored ids boots straight to search.
        page.goto(f"{live_server}/sing/?t={live_token}")
        page.evaluate("localStorage.setItem('sing_name', 'Alice')")
        page.reload()
        expect(page.locator("h2:has-text('Pick your song')")).to_be_visible()
        expect(page.locator('[data-testid="mysongs-bar"]')).to_be_hidden()

    def test_fresh_device_boots_to_name_screen(self, page, live_server, live_token):
        # No identity at all → the name screen, carrying the old landing's
        # welcome copy, with the Request tab highlighted.
        page.goto(f"{live_server}/sing/?t={live_token}")
        page.evaluate("localStorage.clear()")
        page.reload()
        expect(page.locator("h2:has-text('Request a song')")).to_be_visible()
        expect(page.locator("text=what should we call you")).to_be_visible()
        expect(page.locator('[data-testid="tab-request"]')).to_have_class(
            "sing-tab active")


class TestVersionRowEnrichment:
    """The expanded version list gives singers decision-grade info: tappable
    community/commercial pills, full brand names with an info modal, a format
    pill that opens technical details, and a Preview button."""

    def _open_versions(self, page, live_server, live_token):
        page.add_init_script("window.__SING_ARM_MS = 0;")
        _login(page, live_server, live_token)
        body = {"songs": [{
            "key": "q:multi", "artist": "Queen", "title": "Bo Rhap",
            "version_count": 3, "in_library": True,
            "versions": [
                {"source": "local", "priority_stated": True,
                 "priority_class": "community", "priority_brand": "NOMAD",
                 "priority_display": "Nomad Karaoke",
                 "local": {"path": "/media/NOMAD-1 - Q - B.mp4",
                           "disc_id": "NOMAD-1", "format": "mp4",
                           "filename": "NOMAD-1 - Q - B.mp4",
                           "artist": "Queen", "title": "Bo Rhap"}},
                {"source": "kn", "priority_stated": True,
                 "priority_class": "commercial", "priority_brand": "KV",
                 "priority_display": "Karaoke Version",
                 "kn": {"brand_code": "KV", "brand_name": "Karaoke Version",
                        "is_community": False,
                        "divebar": {"file_id": "dv1", "format": "zip",
                                    "file_size": 40000000}}},
                {"source": "kn", "priority_stated": False,
                 "priority_class": "commercial",
                 "kn": {"brand_code": "XX", "brand_name": "Mystery Brand",
                        "is_community": False,
                        "youtube_url": "https://youtu.be/x"}},
            ]}]}
        page.route("**/sing/search*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)))
        page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
        page.locator('input[type="search"]').fill("bo rhap")
        expect(page.locator(".result-row")).to_be_visible()
        page.locator(".sing-versions-toggle").click()

    def test_availability_tiers_say_how_sure_it_will_play(self, page, live_server, live_token):
        self._open_versions(page, live_server, live_token)
        cards = page.locator(".sing-version-card")
        local = cards.nth(0).locator('[data-testid="version-availability"]')
        expect(local).to_have_text("Definitely available — works even offline")
        expect(local).to_have_class(re.compile("sing-avail-sure"))
        cloud = cards.nth(1).locator('[data-testid="version-availability"]')
        expect(cloud).to_contain_text("Very reliable — from our cloud library")
        expect(cloud).to_have_class(re.compile("sing-avail-high"))
        page.locator('[data-testid="online-collapse-toggle"]').click()
        yt = cards.nth(2).locator('[data-testid="version-availability"]')
        expect(yt).to_have_text("Almost always works — YouTube downloads occasionally fail")
        expect(yt).to_have_class(re.compile("sing-avail-likely"))

    def test_cta_wording_is_request_this_song(self, page, live_server, live_token):
        self._open_versions(page, live_server, live_token)
        expect(page.locator(".btn-primary-cta")).to_have_text("Request this song →")

    def test_class_and_format_pills_render(self, page, live_server, live_token):
        self._open_versions(page, live_server, live_token)
        first = page.locator(".sing-version-card").nth(0)
        expect(first.locator(".sing-pill-community")).to_have_text("Community")
        expect(first.locator(".sing-pill-format")).to_have_text("MP4")
        second = page.locator(".sing-version-card").nth(1)
        expect(second.locator(".sing-pill-commercial")).to_have_text("Commercial")
        expect(second.locator(".sing-pill-format")).to_have_text("CDG+MP3")

    def test_brand_shows_full_display_name(self, page, live_server, live_token):
        self._open_versions(page, live_server, live_token)
        expect(page.locator(".sing-version-brand").nth(0)).to_have_text("Nomad Karaoke")
        expect(page.locator(".sing-version-brand").nth(1)).to_have_text("Karaoke Version")

    def test_class_pill_opens_explainer_modal(self, page, live_server, live_token):
        self._open_versions(page, live_server, live_token)
        page.locator(".sing-pill-community").first.click()
        expect(page.locator(".sing-modal-title")).to_have_text("Community track")
        expect(page.locator(".sing-modal-body")).to_contain_text("vocal removed by AI")
        page.locator(".sing-modal-close").click()
        expect(page.locator("#sing-modal-backdrop")).to_have_count(0)

    def test_brand_tap_opens_brand_info(self, page, live_server, live_token):
        self._open_versions(page, live_server, live_token)
        page.locator(".sing-version-brand").nth(1).click()
        expect(page.locator(".sing-modal-title")).to_have_text("Karaoke Version")
        expect(page.locator(".sing-modal-body")).to_contain_text("professional")

    def test_format_pill_opens_details_for_divebar(self, page, live_server, live_token):
        self._open_versions(page, live_server, live_token)
        second = page.locator(".sing-version-card").nth(1)
        second.locator(".sing-pill-format").click()
        expect(page.locator(".sing-modal-body")).to_contain_text("38.1 MB")
        expect(page.locator(".sing-modal-body")).to_contain_text("cloud library")

    def test_format_pill_fetches_media_info_for_local(self, page, live_server, live_token):
        self._open_versions(page, live_server, live_token)
        page.route("**/media-info*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"ok": True, "container": "mov,mp4",
                             "video": {"codec": "h264", "width": 1280, "height": 720},
                             "duration": 218, "size_bytes": 6700000})))
        page.locator(".sing-version-card").nth(0).locator(".sing-pill-format").click()
        expect(page.locator(".sing-modal-body")).to_contain_text("1280×720")
        expect(page.locator(".sing-modal-body")).to_contain_text("3:38")

    def test_preview_button_present_on_every_row(self, page, live_server, live_token):
        self._open_versions(page, live_server, live_token)
        expect(page.locator('[data-testid="version-preview"]')).to_have_count(2)
        # (third row sits behind the online-collapse toggle)

    def test_no_full_path_shown_to_singers(self, page, live_server, live_token):
        self._open_versions(page, live_server, live_token)
        expect(page.locator(".sing-version-path-summary")).to_have_count(0)
        expect(page.locator(".sing-version-expander")).not_to_contain_text("/media/")


class TestTabsAndRouting:
    def test_tab_bar_renders_three_tabs(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        expect(page.locator('[data-testid="tab-request"]')).to_be_visible()
        expect(page.locator('[data-testid="tab-mysongs"]')).to_be_visible()
        expect(page.locator('[data-testid="tab-rotation"]')).to_be_visible()

    def test_rotation_tab_opens_rotation_page(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        page.locator('[data-testid="tab-rotation"]').click()
        expect(page.locator("h2:has-text(\"Tonight's rotation\")")).to_be_visible()
        assert page.evaluate("window.location.hash") == "#rotation"

    def test_browser_back_navigates_inside_spa(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        page.locator('[data-testid="tab-rotation"]').click()
        expect(page.locator("h2:has-text(\"Tonight's rotation\")")).to_be_visible()
        page.go_back()
        # Back returns to the boot (search) screen, not out of the app.
        expect(page.locator("h2:has-text('Pick your song')")).to_be_visible()
        assert page.url.startswith(live_server)

    def test_reload_restores_section_from_hash(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        page.locator('[data-testid="tab-rotation"]').click()
        expect(page.locator("h2:has-text(\"Tonight's rotation\")")).to_be_visible()
        page.reload()
        expect(page.locator("h2:has-text(\"Tonight's rotation\")")).to_be_visible()

    def test_stale_confirm_hash_degrades_to_search(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        page.goto(f"{live_server}/sing/?t={live_token}#confirm")
        # No in-memory selection after a fresh load → search screen instead.
        expect(page.locator("h2:has-text('Pick your song')")).to_be_visible()

    def test_request_tab_gates_on_identity(self, page, live_server, live_token):
        page.goto(f"{live_server}/sing/?t={live_token}")
        expect(page.locator("#sing-root")).to_be_visible()
        page.evaluate("localStorage.removeItem('sing_name')")
        page.evaluate("window.__sing_state.name = ''")
        page.locator('[data-testid="tab-request"]').click()
        expect(page.locator("h2:has-text('Request a song')")).to_be_visible()
        expect(page.locator("text=what should we call you")).to_be_visible()


class TestRotationFreshness:
    _PAYLOAD = {"entries": [
        {"position": 1, "first_name": "Alice", "song_artist": "Song — Artist",
         "now_singing": False, "range_low_s": 60, "range_high_s": 240},
    ]}

    def _mock_rotation(self, page):
        # The e2e fixture's config lacks the wait-estimate keys the real
        # /sing/rotation needs; the freshness UI only cares about the payload.
        page.route("**/sing/rotation*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(self._PAYLOAD)))

    def test_rotation_page_has_refresh_and_age_label(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        self._mock_rotation(page)
        page.locator('[data-testid="tab-rotation"]').click()
        expect(page.locator('[data-testid="rotation-refresh"]')).to_be_visible()
        expect(page.locator(".rotation-updated")).to_contain_text("updated just now")

    def test_manual_refresh_refetches(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        self._mock_rotation(page)
        page.locator('[data-testid="tab-rotation"]').click()
        expect(page.locator('[data-testid="rotation-refresh"]')).to_be_visible()
        with page.expect_request("**/sing/rotation*"):
            page.locator('[data-testid="rotation-refresh"]').click()

    def test_age_label_ticks_as_data_ages(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        self._mock_rotation(page)
        page.locator('[data-testid="tab-rotation"]').click()
        label = page.locator(".rotation-updated")
        expect(label).to_be_visible()
        # Backdate the payload timestamp, then wait for the 5s ticker to fire.
        page.evaluate(
            "document.querySelector('.rotation-updated')"
            ".setAttribute('data-fetched-at', String(Date.now() - 45000))")
        expect(label).to_contain_text("s ago", timeout=8000)


class TestHouseRulesCollapsed:
    def test_rules_only_on_rotation_tab_and_single_layer(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        # Hidden on every non-rotation step.
        expect(page.locator(".rules-footer")).to_be_hidden()
        page.route("**/sing/rotation*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"entries": []})))
        page.locator('[data-testid="tab-rotation"]').click()
        rules = page.locator(".rules-footer")
        expect(rules).to_be_visible()
        # Collapsed by default; expanding shows the FULL rules directly —
        # no nested "Read the full rules" second layer.
        expect(page.locator(".rules-list")).to_be_hidden()
        page.locator(".rules-footer-summary").click()
        expect(page.locator(".rules-list")).to_be_visible()
        expect(page.locator(".rules-footer summary")).to_have_count(1)


class TestExistingSingerPicker:
    """Confirm screen: "Existing singer" / "New singer" instead of an
    always-visible wall of everyone's names."""

    _SINGERS = ["Sarah B.", "mike", "Alice", "Zoë", "Bob"] + [f"Guest {i:02d}" for i in range(50)]

    def _to_confirm(self, page, live_server, live_token, singers=None, status=200):
        page.add_init_script("window.__SING_ARM_MS = 0;")
        _login(page, live_server, live_token)   # identity = "Alice"
        page.route("**/sing/singers*", lambda r: r.fulfill(
            status=status, content_type="application/json",
            body=json.dumps({"singers": singers if singers is not None else self._SINGERS})))
        page.evaluate(
            """() => {
                window.__sing_state._knownSingers = undefined;
                window.__sing_state.selected = {
                    source_type: 'local', source_ref: '/m/x.mp4',
                    song_artist: 'Queen', song_title: 'Under Pressure',
                };
                window.__sing_state.step = 'confirm';
                window.__sing_render();
            }""")

    def test_no_names_shown_until_existing_tapped(self, page, live_server, live_token):
        self._to_confirm(page, live_server, live_token)
        expect(page.locator('[data-testid="add-existing-singer"]')).to_have_text("👥 Existing singer")
        expect(page.locator('[data-testid="add-singer"]')).to_have_text("+ New singer")
        expect(page.locator('[data-testid="partner-picker-row"]')).to_have_count(0)
        expect(page.locator(".sing-card")).not_to_contain_text("Sarah B.")

    def test_long_list_sorted_scrollable_excludes_self(self, page, live_server, live_token):
        self._to_confirm(page, live_server, live_token)
        page.locator('[data-testid="add-existing-singer"]').click()
        rows = page.locator('[data-testid="partner-picker-row"]')
        # 55 names minus the requester (Alice).
        expect(rows).to_have_count(54)
        names = rows.locator(".partner-picker-name").all_inner_texts()
        assert names == sorted(names, key=str.casefold)
        assert "Alice" not in names
        # Letter headers for a long unfiltered list; the list (not the page) scrolls.
        expect(page.locator(".partner-picker-letter").first).to_be_visible()
        scrolls = page.locator('[data-testid="partner-picker-list"]').evaluate(
            "n => n.scrollHeight > n.clientHeight")
        assert scrolls

    def test_filter_then_tap_adds_existing_partner(self, page, live_server, live_token):
        captured = {}
        def handle(route):
            captured["body"] = route.request.post_data_json
            route.continue_()
        page.route("**/sing/submit*", handle)
        self._to_confirm(page, live_server, live_token)
        page.locator('[data-testid="add-existing-singer"]').click()
        page.locator('[data-testid="partner-picker-filter"]').fill("zoe")   # accent-folded
        rows = page.locator('[data-testid="partner-picker-row"]')
        expect(rows).to_have_count(1)
        rows.first.click()
        expect(page.locator(".sing-modal")).to_have_count(0)
        expect(page.locator('[data-testid="partner-existing-0"]')).to_have_text("Zoë")
        # No name/phone inputs for someone already on the list.
        expect(page.locator('[data-testid="partner-name-0"]')).to_have_count(0)
        # Re-opening shows them as already added (disabled).
        page.locator('[data-testid="add-existing-singer"]').click()
        page.locator('[data-testid="partner-picker-filter"]').fill("zo")
        expect(rows.first).to_be_disabled()
        expect(rows.first).to_contain_text("Added")
        page.keyboard.press("Escape")
        page.locator(".submit-btn").click()
        expect(page.locator("text=Your songs tonight")).to_be_visible(timeout=5000)
        assert captured["body"]["additional_singers"] == [{"name": "Zoë", "phone": ""}]

    def test_no_match_offers_add_as_new(self, page, live_server, live_token):
        self._to_confirm(page, live_server, live_token)
        page.locator('[data-testid="add-existing-singer"]').click()
        page.locator('[data-testid="partner-picker-filter"]').fill("Priya K.")
        expect(page.locator('[data-testid="partner-picker-row"]')).to_have_count(0)
        page.locator('[data-testid="partner-picker-new"]').click()
        expect(page.locator('[data-testid="partner-name-0"]')).to_have_value("Priya K.")

    def test_fetch_failure_falls_back_to_new_singer(self, page, live_server, live_token):
        self._to_confirm(page, live_server, live_token, status=500)
        page.locator('[data-testid="add-existing-singer"]').click()
        page.locator('[data-testid="partner-picker-new"]').click()
        expect(page.locator('[data-testid="partner-name-0"]')).to_be_visible()


class TestShellHeader:
    """Brand + 🌐 + status tiles live in one persistent header, the same on
    every tab, painted from cached state (no pop-in on tab switches)."""

    _NP = {"now_singing": {"first_name": "Jasmine", "song_artist": "Cage The Elephant - Ain't No Rest"},
           "up_next": {"first_name": "Celeste"}, "queued_count": 9}
    _ITEM = {"request": {"id": 31, "singer_name": "Alice", "song_artist": "Hard-Fi",
                         "song_title": "Cash Machine", "source_type": "local",
                         "status": "approved", "created_at": "now",
                         "linked_entry_id": 5, "additional_singers": None},
             "performed": False,
             "estimate": {"position": 7, "now_singing": False,
                          "range_low_s": 1500, "range_high_s": 1800}}

    def _seed(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        page.route("**/sing/my-requests*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"now_playing": self._NP, "requests": [self._ITEM]})))
        page.route("**/sing/rotation*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps({"entries": []})))
        page.route("**/sing/now*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(self._NP)))
        page.evaluate(
            "(s) => localStorage.setItem('sing_my_request_ids', JSON.stringify(s))",
            {"token": live_token, "ids": [31], "tokens": {"31": "t"}})
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")
        expect(page.locator(".song-card-title")).to_have_text("Cash Machine — Hard-Fi")

    def test_lang_pill_in_topbar_not_in_cards(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
        expect(page.locator("#sing-header .sing-topbar [data-testid='lang-pill']")).to_be_visible()
        expect(page.locator("#sing-header [data-testid='brand-header']")).to_be_visible()
        expect(page.locator(".sing-card [data-testid='lang-pill']")).to_have_count(0)

    def test_header_and_cards_share_one_column(self, page, live_server, live_token):
        self._seed(page, live_server, live_token)
        page.locator('[data-testid="tab-rotation"]').click()
        sels = ('[data-testid="mysongs-bar"]', '[data-testid="status-stage"]', ".sing-rotation-page")
        for sel in sels:
            expect(page.locator(sel)).to_be_visible()
        boxes = [page.locator(sel).bounding_box() for sel in sels]
        assert len({round(b["x"]) for b in boxes}) == 1
        assert len({round(b["width"]) for b in boxes}) == 1

    def test_tab_switch_paints_from_cache_without_waiting(self, page, live_server, live_token):
        self._seed(page, live_server, live_token)
        page.locator('[data-testid="tab-rotation"]').click()
        expect(page.locator('[data-testid="status-stage"]')).to_contain_text("Jasmine")
        # Now make every API call hang: switching tabs must still show the
        # songs, the tiles and the stage from cached state immediately.
        page.unroute("**/sing/my-requests*")
        page.unroute("**/sing/now*")
        page.route("**/sing/my-requests*", lambda r: None)
        page.route("**/sing/now*", lambda r: None)
        page.locator('[data-testid="tab-mysongs"]').click()
        expect(page.locator(".song-card-title")).to_have_text("Cash Machine — Hard-Fi", timeout=500)
        expect(page.locator('[data-testid="mysongs-bar"]')).to_contain_text("#7 in line", timeout=500)
        page.locator('[data-testid="tab-rotation"]').click()
        stage = page.locator('[data-testid="status-stage"]')
        expect(stage).to_contain_text("Jasmine", timeout=500)
        expect(stage).to_contain_text("Up next: Celeste")
        expect(stage).not_to_have_class(re.compile("status-tile--loading"))


class TestTipTab:
    _INFO = {"enabled": True, "threshold": 20, "methods": [
        {"key": "venmo", "label": "Venmo", "url": "https://venmo.com/nomadkaraoke",
         "amount_style": "venmo"},
    ]}

    def _login_with_tips(self, page, live_server, live_token):
        # tip-info is fetched at boot — the route must exist before goto.
        page.route("**/sing/tip-info*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(self._INFO)))
        _login(page, live_server, live_token)

    def test_tab_shown_by_default_via_tip_page_fallback(self, page, live_server, live_token):
        # Zero config → the live nomadkaraoke.com/tip page fallback keeps
        # tipping ON, so the tab appears (and sits LAST in the bar).
        _login(page, live_server, live_token)
        tip_tab = page.locator('[data-testid="tab-tip"]')
        expect(tip_tab).to_be_visible()
        assert page.evaluate(
            "[...document.querySelectorAll('#sing-tabs .sing-tab')]"
            ".map(b => b.dataset.testid).pop()") == "tab-tip"

    def test_tab_hidden_when_explicitly_disabled(self, page, live_server, live_token):
        page.route("**/sing/tip-info*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"enabled": False, "threshold": 20, "methods": []})))
        _login(page, live_server, live_token)
        expect(page.locator('[data-testid="tab-rotation"]')).to_be_visible()
        expect(page.locator('[data-testid="tab-tip"]')).to_have_count(0)

    def test_tab_appears_and_opens_tip_page(self, page, live_server, live_token):
        self._login_with_tips(page, live_server, live_token)
        page.locator('[data-testid="tab-tip"]').click()
        expect(page.locator("h2:has-text('Tip the host')")).to_be_visible()
        expect(page.locator(".sing-tip-perk")).to_contain_text("$20+")
        link = page.locator(".sing-tip-method")
        # Threshold ($20) is the default chosen amount; venmo deep-links it.
        expect(link).to_have_attribute(
            "href", "https://venmo.com/nomadkaraoke?txn=pay&amount=20&note=Karaoke%20tip")
        # Switching the preset re-deep-links the method buttons.
        page.locator('.sing-tip-preset[data-amount="5"]').click()
        expect(link).to_have_attribute(
            "href", "https://venmo.com/nomadkaraoke?txn=pay&amount=5&note=Karaoke%20tip")
        assert page.evaluate("window.location.hash") == "#tip"

    def test_claim_posts_amount_and_method(self, page, live_server, live_token):
        self._login_with_tips(page, live_server, live_token)
        page.route("**/sing/tip-claim*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"request": {
                "id": 991, "source_type": "tip", "status": "pending",
                "singer_name": "Alice", "tip_amount": 25, "tip_method": "Venmo",
                "edit_token": "tok991",
            }})))
        page.locator('[data-testid="tab-tip"]').click()
        page.locator('[data-testid="tip-amount"]').fill("25")
        page.locator(".sing-tip-method-select").select_option("Venmo")
        with page.expect_request("**/sing/tip-claim*") as req_info:
            page.locator('[data-testid="tip-submit"]').click()
        body = req_info.value.post_data_json
        assert body["amount"] == 25
        assert body["method"] == "Venmo"
        assert body["singer_name"] == "Alice"


class TestSongHistoryInspiration:
    def test_upcoming_singers_expander_removed_from_done(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")
        expect(page.locator("h2:has-text('Your songs tonight')")).to_be_visible()
        expect(page.locator("text=Show upcoming singers")).to_have_count(0)

    def test_collapsed_history_expands_and_search_on_tap(self, page, live_server, live_token):
        page.add_init_script("window.__SING_ARM_MS = 0;")
        _login(page, live_server, live_token)
        page.route("**/sing/my-stats*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "my_songs": [{"artist": "Maximo Park", "title": "Books From Boxes",
                              "plays": 3, "last_sung": "2026-09-20 22:11:00"}],
                "top_songs": [{"artist": "Foo Fighters", "title": "My Hero", "plays": 15}],
            })))
        page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
        history = page.locator('[data-testid="song-history"]')
        expect(history).to_be_visible()
        # Collapsed by default — the body only renders after expanding.
        expect(page.locator(".sing-history-row")).to_have_count(0)
        page.locator(".sing-history-summary").click()
        expect(page.locator(".sing-history-body h4").nth(0)).to_have_text("You've sung here before")
        expect(page.locator(".sing-history-row")).to_have_count(2)
        expect(page.locator(".sing-history-row").nth(0)).to_contain_text("▶ 3")
        # Tapping a row runs the search for that song.
        with page.expect_request("**/sing/search*"):
            page.locator(".sing-history-row").nth(0).click()
        expect(page.locator('input[type="search"]')).to_have_value("Maximo Park Books From Boxes")

    def test_empty_history_message(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        page.route("**/sing/my-stats*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"my_songs": [], "top_songs": []})))
        page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
        page.locator(".sing-history-summary").click()
        expect(page.locator(".sing-history-body")).to_contain_text("tonight's the night")


class TestMySongsStatusBanner:
    def test_banner_shows_personal_position_not_venue_now_next(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        self_np = {"now_singing": {"first_name": "Lindsay", "song_artist": "X"},
                   "up_next": {"first_name": "Someone"}, "queued_count": 9}
        item = {"request": {"id": 1, "singer_name": "Alice", "song_artist": "Q",
                            "song_title": "Bo Rhap", "source_type": "local",
                            "status": "approved", "created_at": "now",
                            "linked_entry_id": 5, "additional_singers": None},
                "performed": False,
                "estimate": {"position": 5, "now_singing": False,
                             "range_low_s": 600, "range_high_s": 900}}
        page.route("**/sing/my-requests*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"now_playing": self_np, "requests": [item]})))
        page.evaluate(
            "(s) => localStorage.setItem('sing_my_request_ids', JSON.stringify(s))",
            {"token": live_token, "ids": [1], "tokens": {}})
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")
        banner = page.locator('[data-testid="mysongs-bar"]')
        expect(banner).to_be_visible()
        expect(banner).to_contain_text("#5")
        # The venue-wide stage tile is Rotation-tab only.
        expect(page.locator('[data-testid="status-stage"]')).to_have_count(0)

    def test_banner_hidden_without_live_songs(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        page.route("**/sing/my-requests*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"now_playing": {"now_singing": None, "up_next": None,
                                             "queued_count": 0}, "requests": []})))
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")
        expect(page.locator("h2:has-text('Your songs tonight')")).to_be_visible()
        expect(page.locator('[data-testid="mysongs-bar"]')).to_have_count(0)


class TestNotificationsSection:
    _ITEM = {"request": {"id": 21, "singer_name": "Alice", "song_artist": "Q",
                         "song_title": "One", "source_type": "local",
                         "status": "approved", "created_at": "now",
                         "linked_entry_id": 5, "additional_singers": None},
             "performed": False, "estimate": {"position": 5, "now_singing": False,
                                              "range_low_s": 600, "range_high_s": 900}}

    def _open_done(self, page, live_server, live_token, phone=""):
        _login(page, live_server, live_token)
        if phone:
            page.evaluate("(p) => localStorage.setItem('sing_phone', p)", phone)
            page.evaluate("(p) => { window.__sing_state.phone = p; }", phone)
        page.evaluate(
            "(s) => localStorage.setItem('sing_my_request_ids', JSON.stringify(s))",
            {"token": live_token, "ids": [21], "tokens": {"21": "tok21"}})
        page.route("**/sing/my-requests*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"now_playing": {"now_singing": None, "up_next": None,
                                             "queued_count": 1},
                             "requests": [self._ITEM]})))
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")
        # The section renders on a 2s delay ("you're in!" registers first).
        page.evaluate("window.setTimeout ? null : null")

    def test_explains_channels_with_number(self, page, live_server, live_token):
        self._open_done(page, live_server, live_token, phone="+1 555 123 4567")
        section = page.locator("#push-optin")
        # A number on file = set up → collapsed toggle; expand it.
        toggle = section.locator('[data-testid="pref-toggle-notify"]')
        expect(toggle).to_contain_text("Text message", timeout=8000)
        toggle.click()
        expect(section.locator(".pref-box-title")).to_contain_text("Notification preferences")
        expect(section).to_contain_text("Text message to +1 555 123 4567")
        expect(section.locator(".notify-summary")).to_be_visible()

    def test_add_number_after_signup_posts_update_phone(self, page, live_server, live_token):
        self._open_done(page, live_server, live_token)
        section = page.locator("#push-optin")
        expect(section).to_contain_text("Want a text when you're up?", timeout=8000)
        page.locator('[data-testid="notify-add-phone"]').click()
        page.locator('[data-testid="notify-phone"]').fill("+1 555 222 3333")
        with page.expect_request("**/sing/update-phone*") as req_info:
            page.locator('[data-testid="notify-phone-save"]').click()
        body = json.loads(req_info.value.post_data or "{}")
        assert body["phone"] == "+1 555 222 3333"
        assert body["items"] == [{"id": 21, "edit_token": "tok21"}]
        # Section re-renders showing the SMS channel is now on.
        expect(section).to_contain_text("Text message to +1 555 222 3333")

    def test_change_number_link(self, page, live_server, live_token):
        self._open_done(page, live_server, live_token, phone="+1 555 123 4567")
        section = page.locator("#push-optin")
        section.locator('[data-testid="pref-toggle-notify"]').click(timeout=8000)
        expect(section).to_contain_text("change number")
        page.locator('[data-testid="notify-change-phone"]').click()
        expect(page.locator('[data-testid="notify-phone"]')).to_have_value("+1 555 123 4567")
