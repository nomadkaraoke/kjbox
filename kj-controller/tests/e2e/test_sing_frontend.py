"""End-to-end tests for the public /sing/* singer UI."""

import json

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
        expect(page.locator("text=Wonderwall")).to_be_visible()
        expect(page.locator("text=Don't Look Back in Anger")).to_be_visible()
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
        expect(page.locator(".confirm-source")).to_have_text("In our library")
        expect(page.locator(".confirm-searched")).to_contain_text("bohemian")
        expect(page.locator(".submit-btn")).to_have_text("Yes — send to the KJ")
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

    def test_reorder_down_sends_both_tokens(self, page, live_server, live_token):
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
        page.on("dialog", lambda d: d.accept())
        first_down = page.locator('[data-testid="reorder-down"]').first
        expect(first_down).to_be_visible()
        with page.expect_request("**/sing/requests/reorder*") as req_info:
            first_down.click()
        body = req_info.value.post_data or ""
        assert "tok11" in body and "tok12" in body


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
        expect(page.locator("text=Bo Rhap")).to_be_visible()
        # Bar is redundant on the done screen (which IS the list), so it hides.
        expect(page.locator('[data-testid="mysongs-bar"]')).to_be_hidden()

    def test_bar_visible_off_done_and_reopens_list(self, page, live_server, live_token):
        self._seed_ls(page, live_server, live_token,
                      {"token": live_token, "ids": [4242], "tokens": {"4242": "secret-xyz"}})
        self._route_my_requests(page, [self._pending_song()])
        page.reload()
        expect(page.locator(".song-card-title")).to_be_visible()   # restored to done
        # Leave the done screen — the persistent bar should appear.
        page.locator('[data-testid="request-another"]').click()
        bar = page.locator('[data-testid="mysongs-bar"]')
        expect(bar).to_be_visible()
        expect(bar).to_contain_text("My song (1)")
        # Tapping the bar returns to the list.
        bar.click()
        expect(page.locator(".song-card-title")).to_be_visible()

    def test_bar_shows_status_at_a_glance(self, page, live_server, live_token):
        # A queued song with a position surfaces its wait on the bar.
        self._seed_ls(page, live_server, live_token,
                      {"token": live_token, "ids": [11], "tokens": {"11": "t11"}})
        song = {"request": {"id": 11, "singer_name": "Alice", "song_artist": "Q",
                            "song_title": "One", "source_type": "local", "status": "approved",
                            "created_at": "now", "linked_entry_id": 101, "additional_singers": None},
                "estimate": {"position": 4, "range_low_s": 600, "range_high_s": 900,
                             "now_singing": False}}
        self._route_my_requests(page, [song])
        page.reload()
        expect(page.locator(".song-card-title")).to_be_visible()
        page.locator('[data-testid="request-another"]').click()
        expect(page.locator('[data-testid="mysongs-bar"]')).to_contain_text("#4")

    def test_stale_night_prunes_and_stays_on_landing(self, page, live_server, live_token):
        # localStorage still holds last night's ids, but the server night-scopes
        # them out (empty). The singer stays on landing and the dead ids are
        # pruned so the bar never shows a phantom count.
        self._seed_ls(page, live_server, live_token,
                      {"token": live_token, "ids": [9999], "tokens": {"9999": "old"}})
        self._route_my_requests(page, [])
        with page.expect_request("**/sing/my-requests*") as req_info:
            page.reload()
        # The boot probe must actually carry the stored id (contract check).
        assert "ids=9999" in req_info.value.url
        expect(page.locator("h1:has-text('Request a song')")).to_be_visible()   # landing
        expect(page.locator(".song-card-title")).to_have_count(0)
        expect(page.locator('[data-testid="mysongs-bar"]')).to_be_hidden()
        # Stored ids were pruned to empty.
        remaining = page.evaluate(
            "() => JSON.parse(localStorage.getItem('sing_my_request_ids')).ids")
        assert remaining == []

    def test_cancelled_only_stays_on_landing(self, page, live_server, live_token):
        # A device whose only song was cancelled isn't yanked off landing (the
        # bar filters cancelled out too), even though the id still resolves.
        self._seed_ls(page, live_server, live_token,
                      {"token": live_token, "ids": [7], "tokens": {"7": "t7"}})
        cancelled = {"request": {"id": 7, "singer_name": "Alice", "song_artist": "Q",
                                "song_title": "Gone", "source_type": "local",
                                "status": "cancelled", "created_at": "now",
                                "linked_entry_id": None, "additional_singers": None}}
        self._route_my_requests(page, [cancelled])
        with page.expect_request("**/sing/my-requests*"):
            page.reload()
        expect(page.locator("h1:has-text('Request a song')")).to_be_visible()
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
        # A fresh device (no stored ids) sees the normal landing, no bar.
        page.goto(f"{live_server}/sing/?t={live_token}")
        page.evaluate("localStorage.setItem('sing_name', 'Alice')")
        page.reload()
        expect(page.locator("text=Request a song")).to_be_visible()
        expect(page.locator('[data-testid="mysongs-bar"]')).to_be_hidden()
