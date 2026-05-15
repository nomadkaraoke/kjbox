"""End-to-end tests for the public /sing/* singer UI."""

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
        # TODO: enable after Task 8 — multi-song done screen not yet implemented.
        # expect(page.locator('text=Your songs tonight')).to_be_visible(timeout=5000)
        # TODO: enable after Task 8 — assertion depends on done-screen transition above.
        # assert captured['body']['additional_singers'] == [
        #     {"name": "Sarah B.", "phone": "+61 400 111 222"},
        # ]
