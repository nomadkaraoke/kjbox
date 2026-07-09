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
        const _origFetch = window.fetch;
        window.fetch = (url, opts) => {
          const m = /\/sing\/search\?q=([^&]*)/.exec(String(url));
          if (m) {
            const q = decodeURIComponent(m[1]).trim();
            const s = window.__searchScript[q];
            if (s) {
              return new Promise((resolve) => setTimeout(() => resolve(
                new Response(JSON.stringify(s.body),
                  {status: 200, headers: {'Content-Type': 'application/json'}})
              ), s.delay));
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
        page.wait_for_timeout(800)   # let the 700ms debounce fire so 'aaa' is in flight (resolves +1500ms)
        inp.fill("aaab")             # newer query; 'aaa' is already fetching (not cancelled)
        expect(page.locator(".r-title")).to_have_text("FAST FRESH")   # fresh lands first
        page.wait_for_timeout(1400)  # let the slow, stale 'aaa' resolve last
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
