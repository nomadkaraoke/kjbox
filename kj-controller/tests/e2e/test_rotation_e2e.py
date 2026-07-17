"""E2E Playwright tests for rotation multi-singer pill input."""

import pytest


@pytest.fixture
def rotation_page(app_page):
    """Navigate to the app and open the rotation add form."""
    page = app_page
    add_btn = page.locator('.rotation-add-btn')
    add_btn.click()
    page.locator('#singer-input-container').wait_for(state='visible')
    return page


class TestSingleSingerAdd:
    def test_type_name_and_submit(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').fill('TestSingleSinger')
        page.locator('#rotation-singer').press('Enter')
        page.locator('#rotation-song').fill('Test Song')
        page.locator('#rotation-add-btn-submit').click()
        # Wait for our specific entry to appear
        page.locator('.rotation-name', has_text='TestSingleSinger').first.wait_for(state='visible')
        assert page.locator('.rotation-name', has_text='TestSingleSinger').count() > 0


class TestMultiSingerPillCreation:
    def test_tab_creates_pill(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').fill('PillTestA')
        page.locator('#rotation-singer').press('Tab')
        pills = page.locator('.singer-pill')
        assert pills.count() == 1
        assert 'PillTestA' in pills.first.text_content()
        assert page.locator('#rotation-singer').input_value() == ''

    def test_comma_creates_pill(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').type('CommaTest,')
        pills = page.locator('.singer-pill')
        assert pills.count() == 1
        assert 'CommaTest' in pills.first.text_content()

    def test_ampersand_creates_pill(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').type('AmpTest&')
        pills = page.locator('.singer-pill')
        assert pills.count() == 1
        assert 'AmpTest' in pills.first.text_content()

    def test_multi_singer_submit_shows_pills_in_rotation(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').fill('DuetAlpha')
        page.locator('#rotation-singer').press('Tab')
        page.locator('#rotation-singer').fill('DuetBeta')
        page.locator('#rotation-singer').press('Enter')
        page.locator('#rotation-song').fill('Duet Test Song')
        page.locator('#rotation-add-btn-submit').click()
        # Wait for entry to appear
        page.locator('.rotation-entry').first.wait_for(state='visible')
        # Multi-singer entry should show individual singer pills
        singer_pills = page.locator('.rotation-singer-pill')
        assert singer_pills.count() >= 2
        texts = [singer_pills.nth(i).text_content() for i in range(singer_pills.count())]
        assert any('DuetAlpha' in t for t in texts)
        assert any('DuetBeta' in t for t in texts)


class TestRotationSearchRace:
    """Regression: a slow response for an EARLIER query must not overwrite the
    dropdown after a LATER query has already rendered.

    Reproduces the reported bug where typing 'frank might' briefly shows the
    right matches, then the dropdown is clobbered by stale 'frank' results
    because the in-flight broad search lands late (Karaoke Nerds scrape, up to
    8s, variable latency). See doRotationSearch / rotSearchGen in app.js.
    """

    # Shim window.fetch IN THE BROWSER so the two /rotation/search responses
    # resolve truly concurrently (a Playwright route handler runs on one thread,
    # so a sleeping handler would serialize the requests and hide the race).
    # The broad 'frank' query resolves 1800ms late (still in flight after the
    # narrow query has rendered, so it can attempt a late clobber); 'frank might'
    # resolves now. 1800ms comfortably exceeds the 700ms input debounce.
    _FETCH_SHIM = """
    () => {
        const orig = window.fetch;
        const mk = (fn) => ({
            local: [{ path: '/media/test/' + fn, filename: fn, artist: 'Tester',
                      title: fn, format: 'cdg', priority_rank: 9999,
                      priority_class: 'unknown' }],
            karaoke_nerds: [],
        });
        window.fetch = function(url, opts) {
            if (typeof url === 'string' && url.indexOf('/rotation/search') !== -1) {
                const raw = (url.split('q=')[1] || '').split('&')[0];
                const q = decodeURIComponent(raw).toLowerCase();
                const isNarrow = q.indexOf('might') !== -1;
                const body = isNarrow
                    ? mk('It Might As Well Be Spring - Frank Sinatra')
                    : mk('Frankie Teardrop - Suicide');
                const delay = isNarrow ? 0 : 1800;
                return new Promise(res => setTimeout(() => res(new Response(
                    JSON.stringify(body),
                    { status: 200, headers: { 'Content-Type': 'application/json' } }
                )), delay));
            }
            return orig.apply(this, arguments);
        };
    }
    """

    def test_stale_broad_query_does_not_clobber_newer_results(self, rotation_page):
        page = rotation_page
        page.evaluate(self._FETCH_SHIM)

        song = page.locator("#rotation-song")
        # Type the broad query and let its debounce elapse so the slow fetch is
        # actually in flight before we type more (margin above the 700ms debounce).
        song.fill("frank")
        page.wait_for_timeout(900)
        # Now narrow it; this fetch returns instantly and renders first.
        song.fill("frank might")
        page.wait_for_timeout(900)

        dropdown = page.locator("#rotation-search-dropdown")
        dropdown.wait_for(state="visible")
        assert "It Might As Well Be Spring" in dropdown.inner_text()

        # Let the stale 'frank' response land. It must NOT replace the results.
        page.wait_for_timeout(900)
        text = dropdown.inner_text()
        assert "It Might As Well Be Spring" in text, (
            "Stale broad-query response clobbered the newer results: " + text)
        assert "Frankie Teardrop" not in text, (
            "Dropdown shows stale 'frank' results for a 'frank might' query")


class TestPillDeletion:
    def test_backspace_removes_last_pill(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').fill('BackA')
        page.locator('#rotation-singer').press('Tab')
        page.locator('#rotation-singer').fill('BackB')
        page.locator('#rotation-singer').press('Tab')
        assert page.locator('.singer-pill').count() == 2
        page.locator('#rotation-singer').press('Backspace')
        assert page.locator('.singer-pill').count() == 1
        assert 'BackA' in page.locator('.singer-pill').first.text_content()

    def test_x_button_removes_pill(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').fill('XTestA')
        page.locator('#rotation-singer').press('Tab')
        page.locator('#rotation-singer').fill('XTestB')
        page.locator('#rotation-singer').press('Tab')
        assert page.locator('.singer-pill').count() == 2
        page.locator('.singer-pill-x').first.click()
        pills = page.locator('.singer-pill')
        assert pills.count() == 1
        assert 'XTestB' in pills.first.text_content()


# Render one rotation row directly (no server round-trip) so we can exercise the
# row's click gestures. Timers are cleared so the 10s rotation poll can't wipe
# the injected row mid-test.
_ROW_SETUP = """
() => {
    for (let i = 1; i < 100000; i++) clearInterval(i);
    window.rotationData = [{ id: 42, position: 1, singer: 'Jenny',
        song_artist: 'Kryptonite - 3 Doors Down', status: 'Up Next',
        songs_sung: 0, file_path: '/d/x.mp4' }];
    renderRotation(window.rotationData);
}
"""


class TestRotationClickGestures:
    """Shift+click edits a rotation row; there is no click-to-delete gesture.

    Gestures are exercised with synthetic click events (dispatched right after a
    render, with timers cleared) so the assertions are deterministic — a real
    Playwright click auto-waits and can race the 10s rotation poll re-rendering
    the injected row.
    """

    # Dispatch a click on `selector` with the given modifier and report what the
    # row handler did (delete attempt / edit mode / focused field).
    _DISPATCH = """
    ([selector, mods]) => {
        window.__deleted = [];
        window.deleteRotationEntry = function () { window.__deleted.push(Array.from(arguments)); };
        const el = document.querySelector(selector);
        el.dispatchEvent(new MouseEvent('click', Object.assign({ bubbles: true }, mods)));
        return {
            deleted: window.__deleted.length,
            editing: !!document.querySelector('.rotation-editing'),
            active: document.activeElement ? document.activeElement.className : '',
            rows: document.querySelectorAll('#rotation-list .rotation-entry').length,
        };
    }
    """

    def _render(self, page):
        page.evaluate(_ROW_SETUP)
        page.locator('#rotation-list .rotation-entry').first.wait_for(state='visible')

    def _click(self, page, selector, mods):
        return page.evaluate(self._DISPATCH, [selector, mods])

    def test_ctrl_or_cmd_click_does_not_delete(self, app_page):
        page = app_page
        self._render(page)
        # Ctrl/Cmd click on the row body (the position number) where the old
        # delete gesture used to fire: it must do nothing now.
        for mod in ({"ctrlKey": True}, {"metaKey": True}):
            res = self._click(page, '#rotation-list .rotation-num', mod)
            assert res["deleted"] == 0, res
            assert res["editing"] is False, res
            assert res["rows"] == 1, res

    def test_shift_click_row_enters_edit_mode(self, app_page):
        page = app_page
        self._render(page)
        res = self._click(page, '#rotation-list .rotation-num', {"shiftKey": True})
        assert res["editing"] is True, res

    def test_shift_click_song_focuses_song_field(self, app_page):
        page = app_page
        self._render(page)
        res = self._click(page, '#rotation-list .rotation-song', {"shiftKey": True})
        assert res["editing"] is True, res
        assert "rotation-edit-song" in res["active"], res

    def test_shift_click_singer_focuses_singer_field(self, app_page):
        page = app_page
        self._render(page)
        res = self._click(page, '#rotation-list .rotation-name', {"shiftKey": True})
        assert res["editing"] is True, res
        assert "rotation-edit-singer" in res["active"], res


# Render rotation rows across all "singer happiness" tiers and read back the two
# pills (sing count + wait time) each row produced, so the colour thresholds and
# the new-singer wait-time behaviour stay locked in.
_PILL_PROBE = """
(entries) => {
    for (let i = 1; i < 100000; i++) clearInterval(i);
    window.rotationData = entries;
    renderRotation(entries);
    const tier = el => (Array.from(el.classList).find(c => c.startsWith('pill-')) || '');
    return Array.from(document.querySelectorAll('#rotation-list .rotation-entry')).map(row => {
        const pills = row.querySelectorAll('.rotation-pill');
        return {
            n: pills.length,
            count_text: pills[0] ? pills[0].textContent : null,
            count_tier: pills[0] ? tier(pills[0]) : null,
            wait_text: pills[1] ? pills[1].textContent : null,
            wait_tier: pills[1] ? tier(pills[1]) : null,
            // Guard: the retired combined-pill markup must be gone.
            legacy: row.querySelectorAll('.rotation-songs-pill, .pill-new, .rotation-lastsang').length,
        };
    });
}
"""


class TestSingerHappinessPills:
    """The rotation row shows two compact pills — sing count and wait time —
    each coloured green=good / red=bad from the singer's perspective."""

    def _probe(self, page, entries):
        for i, e in enumerate(entries):
            e.setdefault("id", i + 1)
            e.setdefault("position", i + 1)
            e.setdefault("song_artist", "Song - Artist")
            e.setdefault("status", "Up Next")
            e.setdefault("file_path", "/d/x.mp4")
        return page.evaluate(_PILL_PROBE, entries)

    def test_count_pill_colour_tiers(self, app_page):
        rows = self._probe(app_page, [
            {"singer": "Zero", "songs_sung": 0, "wait_minutes": 5},
            {"singer": "One", "songs_sung": 1, "wait_minutes": 5},
            {"singer": "Four", "songs_sung": 4, "wait_minutes": 5},
            {"singer": "Five", "songs_sung": 5, "wait_minutes": 5},
        ])
        # <2 red, 2–4 yellow, >=5 green — count always shown, including ×0.
        assert [r["count_text"] for r in rows] == ["×0", "×1", "×4", "×5"]
        assert [r["count_tier"] for r in rows] == [
            "pill-bad", "pill-bad", "pill-warn", "pill-good"]

    def test_wait_pill_colour_tiers(self, app_page):
        rows = self._probe(app_page, [
            {"singer": "Fresh", "songs_sung": 3, "wait_minutes": 20},
            {"singer": "Waiting", "songs_sung": 3, "wait_minutes": 45},
            {"singer": "Overdue", "songs_sung": 3, "wait_minutes": 46},
        ])
        # <=20 green, 21–45 yellow, >45 red.
        assert [r["wait_text"] for r in rows] == ["20m", "45m", "46m"]
        assert [r["wait_tier"] for r in rows] == [
            "pill-good", "pill-warn", "pill-bad"]

    def test_new_singer_shows_wait_time(self, app_page):
        # The core ask: a brand-new singer (0 sung) still gets a wait pill,
        # measured from when their first song entered the rotation.
        rows = self._probe(app_page, [
            {"singer": "Newbie", "songs_sung": 0, "wait_minutes": 33},
        ])
        assert rows[0]["n"] == 2
        assert rows[0]["count_text"] == "×0"
        assert rows[0]["wait_text"] == "33m"
        assert rows[0]["wait_tier"] == "pill-warn"
        assert rows[0]["legacy"] == 0  # no old "NEW" / combined pill

    def test_unknown_wait_is_infinity_red(self, app_page):
        rows = self._probe(app_page, [
            {"singer": "Unknown", "songs_sung": 0, "wait_minutes": None},
        ])
        assert rows[0]["wait_text"] == "∞"
        assert rows[0]["wait_tier"] == "pill-bad"


class TestCancelledEntry:
    def test_cancelled_entry_renders_distinctly_with_dismiss(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').fill('CancelMe')
        page.locator('#rotation-singer').press('Enter')
        page.locator('#rotation-song').fill('Some Song')
        page.locator('#rotation-add-btn-submit').click()
        page.locator('.rotation-name', has_text='CancelMe').first.wait_for(state='visible')
        # Soft-cancel via the API (exactly as the singer cancel endpoint does),
        # then force a rotation refresh.
        page.evaluate("""async () => {
            const r = await fetch('/rotation').then(x => x.json());
            const e = r.entries.find(e => (e.singer || '').includes('CancelMe'));
            await fetch('/rotation/status', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: e.id, status: 'Cancelled'})});
            await fetchRotation();
        }""")
        cancelled = page.locator('.rotation-entry.rotation-cancelled')
        cancelled.first.wait_for(state='visible')
        assert cancelled.locator('.badge-cancelled').count() > 0
        assert cancelled.locator('.rotation-btn-dismiss').count() > 0


class TestChangeReorderPanel:
    def test_panel_renders_change_and_reorder(self, app_page):
        page = app_page
        # Seed approved songs, then create a reorder + a change (both pending)
        # through the singer endpoints, then reload so the panel fetches them.
        page.evaluate("""async () => {
            const cfg = await fetch('/rotation/requests/config').then(r => r.json());
            const t = cfg.token;
            await fetch('/rotation/requests/config', {method:'POST',
              headers:{'Content-Type':'application/json'},
              body: JSON.stringify({auto_approve: true})});
            async function submit(title) {
              const r = await fetch('/sing/submit?t=' + t, {method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({singer_name:'Alice', phone:'', song_artist:'Q',
                  song_title:title, source_type:'local', source_ref:'/'+title+'.mp4'})});
              return (await r.json()).request;
            }
            const a = await submit('AA'); const b = await submit('BB'); const c = await submit('CC');
            // Auto-approve now applies reorders immediately, so turn it OFF to
            // leave the reorder (and change) in the KJ's pending queue — which
            // is what this panel renders.
            await fetch('/rotation/requests/config', {method:'POST',
              headers:{'Content-Type':'application/json'},
              body: JSON.stringify({auto_approve: false})});
            await fetch('/sing/requests/reorder?t=' + t, {method:'POST',
              headers:{'Content-Type':'application/json'},
              body: JSON.stringify({items:[{id:b.id, edit_token:b.edit_token},
                                           {id:a.id, edit_token:a.edit_token}]})});
            await fetch('/sing/requests/' + c.id + '/change?t=' + t, {method:'POST',
              headers:{'Content-Type':'application/json'},
              body: JSON.stringify({edit_token:c.edit_token, source_type:'local',
                source_ref:'/new.mp4', song_artist:'ABBA', song_title:'SOS'})});
        }""")
        page.reload()
        page.locator('#pending-requests-list').wait_for(state='visible')
        page.locator('.pending-req-row.pr-reorder').first.wait_for(state='visible')
        assert page.locator('.pending-req-row.pr-reorder').count() >= 1
        assert page.locator('.pending-req-row.pr-change').count() >= 1
