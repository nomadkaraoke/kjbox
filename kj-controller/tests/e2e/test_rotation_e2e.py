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
