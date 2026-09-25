"""E2E: footer social links + singer social-media photo consent (v0.112.0).

Singer side: icon row in the event footer, the consent question on the confirm
step (sent with the request), and the change control on My songs. KJ side: the
📷 marker beside names in the rotation and its click-to-cycle.
"""

import json

from playwright.sync_api import expect


def _login(page, live_server, live_token, name="Alice", consent=""):
    page.goto(f"{live_server}/sing/?t={live_token}")
    expect(page.locator("#sing-root")).to_be_visible()
    page.evaluate("(n) => localStorage.setItem('sing_name', n)", name)
    page.evaluate("(c) => localStorage.setItem('sing_photo_consent', c)", consent)
    page.evaluate("localStorage.setItem('sing_phone', '')")


def _event_info(page, **extra):
    body = {"kj_name": "", "footer_message": "", "notices": [], "social": {},
            "ask_photo_consent": False, **extra}
    page.route("**/sing/event-info*", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps(body)))


def _to_confirm(page):
    page.wait_for_function("!!window.__sing_state")
    page.evaluate("""
        window.__sing_state.name = 'Alice';
        window.__sing_state.selected = {
            song_artist: 'Oasis', song_title: 'Wonderwall', label: 'Wonderwall',
            source_type: 'youtube', source_ref: 'https://www.youtube.com/watch?v=abc',
        };
        window.__sing_state.step = 'confirm';
        window.__sing_render();
    """)


class TestFooterSocialLinks:
    def test_icons_render_in_order_with_safe_hrefs(self, page, live_server, live_token):
        _event_info(page, social={
            "email": "andrew@nomadkaraoke.com",
            "instagram": "https://www.instagram.com/nomadkaraoke",
            "website": "https://nomadkaraoke.com",
            "facebook": "javascript:alert(1)",   # never trusted, even from the server
        })
        _login(page, live_server, live_token)
        page.reload()
        social = page.locator('[data-testid="footer-social"]')
        expect(social).to_be_visible()
        expect(social).to_contain_text("Follow us")
        links = social.locator("a.sing-social-link")
        expect(links).to_have_count(3)
        assert links.evaluate_all("as => as.map(a => a.dataset.social)") == \
            ["instagram", "website", "email"]
        insta = social.locator('[data-social="instagram"]')
        expect(insta).to_have_attribute("href", "https://www.instagram.com/nomadkaraoke")
        expect(insta).to_have_attribute("target", "_blank")
        expect(insta).to_have_attribute("rel", "noopener noreferrer")
        expect(insta.locator("svg path")).to_have_count(1)
        expect(social.locator('[data-social="email"]')).to_have_attribute(
            "href", "mailto:andrew@nomadkaraoke.com")

    def test_footer_shows_with_only_social_links(self, page, live_server, live_token):
        _event_info(page, social={"website": "https://nomadkaraoke.com"})
        _login(page, live_server, live_token)
        page.reload()
        expect(page.locator("#sing-event-footer")).to_be_visible()
        expect(page.locator(".sing-notice")).to_have_count(0)


class TestConfirmStepConsent:
    def test_asks_and_sends_choice_with_request(self, page, live_server, live_token):
        _event_info(page, ask_photo_consent=True)
        sent = {}

        def on_submit(route):
            sent.update(json.loads(route.request.post_data))
            route.fulfill(status=200, content_type="application/json", body=json.dumps(
                {"request": {"id": 7, "status": "pending", "edit_token": "t7",
                             "singer_name": "Alice", "song_artist": "Oasis",
                             "song_title": "Wonderwall"}, "auto_approved": False}))
        page.route("**/sing/submit*", on_submit)
        _login(page, live_server, live_token)
        page.reload()
        _to_confirm(page)
        picker = page.locator('[data-testid="photo-consent"]')
        expect(picker).to_be_visible()
        expect(picker).to_contain_text("social media")
        page.locator('[data-testid="photo-consent-no"]').click()
        expect(page.locator('[data-testid="photo-consent-no"]')).to_have_attribute("aria-pressed", "true")
        assert page.evaluate("localStorage.getItem('sing_photo_consent')") == "no"
        page.locator(".submit-btn").click()
        page.wait_for_function("window.__sing_state.step === 'done'")
        assert sent["photo_consent"] == "no"

    def test_not_asked_again_once_chosen(self, page, live_server, live_token):
        _event_info(page, ask_photo_consent=True)
        _login(page, live_server, live_token, consent="yes")
        page.reload()
        _to_confirm(page)
        expect(page.locator(".submit-btn")).to_be_visible()
        expect(page.locator('[data-testid="photo-consent"]')).to_have_count(0)

    def test_not_asked_when_host_has_it_off(self, page, live_server, live_token):
        _event_info(page, ask_photo_consent=False)
        sent = {}

        def on_submit(route):
            sent.update(json.loads(route.request.post_data))
            route.fulfill(status=200, content_type="application/json", body=json.dumps(
                {"request": {"id": 8, "status": "pending", "edit_token": "t8",
                             "singer_name": "Alice"}, "auto_approved": False}))
        page.route("**/sing/submit*", on_submit)
        _login(page, live_server, live_token, consent="yes")
        page.reload()
        _to_confirm(page)
        expect(page.locator('[data-testid="photo-consent"]')).to_have_count(0)
        page.locator(".submit-btn").click()
        page.wait_for_function("window.__sing_state.step === 'done'")
        assert "photo_consent" not in sent


class TestMySongsConsent:
    def test_change_posts_owned_items(self, page, live_server, live_token):
        _event_info(page, ask_photo_consent=True)
        posted = {}

        def on_consent(route):
            posted.update(json.loads(route.request.post_data))
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True, "updated": 1}))
        page.route("**/sing/photo-consent*", on_consent)
        page.route("**/sing/my-requests*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps({
                "now_playing": {"now_singing": None, "up_next": None, "queued_count": 0},
                "requests": []})))
        _login(page, live_server, live_token, consent="yes")
        page.reload()
        page.wait_for_function("!!window.__sing_state")
        page.evaluate("""(t) => localStorage.setItem('sing_my_request_ids', JSON.stringify(
            {token: t, ids: [1], tokens: {'1': 'tok1'}}))""", live_token)
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")
        picker = page.locator('#push-optin [data-testid="photo-consent"]')
        expect(picker).to_be_visible()
        expect(picker.locator('[data-testid="photo-consent-yes"]')).to_have_attribute("aria-pressed", "true")
        # Both choices fit inside the picker (the .push-optin 240px button
        # min-width used to push "Please don't" out of the card).
        assert page.evaluate("""() => {
            const box = document.querySelector('#push-optin [data-testid="photo-consent"]')
                .getBoundingClientRect();
            return [...document.querySelectorAll('#push-optin .photo-consent-btn')]
                .every((b) => b.getBoundingClientRect().right <= box.right + 0.5);
        }""")
        picker.locator('[data-testid="photo-consent-no"]').click()
        expect(picker.locator(".photo-consent-saved")).to_contain_text("Saved")
        assert posted == {"consent": "no", "items": [{"id": 1, "edit_token": "tok1"}]}
        assert page.evaluate("localStorage.getItem('sing_photo_consent')") == "no"


def _entry(eid, singer, consent, singers=None):
    return {"id": eid, "singer": singer, "song_artist": "Song", "status": "Waiting",
            "position": eid, "songs_sung": 1, "wait_minutes": 5,
            "singers_json": json.dumps(singers) if singers else None,
            "photo_consent": consent}


class TestKjRotationMarker:
    def test_markers_reflect_consent_and_click_cycles(self, app_page):
        page = app_page
        posted = []

        def on_post(route):
            posted.append(json.loads(route.request.post_data))
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True}))
        page.route("**/rotation/singer/photo-consent", on_post)
        page.evaluate("""(entries) => renderRotation(entries)""", [
            _entry(1, "Lindsay", {"Lindsay": "yes"}),
            _entry(2, "Celine", {"Celine": "no"}),
            _entry(3, "Bevbot", {"Bevbot": None}),
            _entry(4, "A & B", {"A": "no", "B": None}, singers=["A", "B"]),
        ])
        rows = page.locator("#rotation-list .rotation-entry")
        expect(rows.nth(0).locator(".rotation-photo-consent")).to_have_attribute("data-consent", "yes")
        expect(rows.nth(1).locator(".rotation-photo-consent")).to_have_attribute("data-consent", "no")
        expect(rows.nth(2).locator(".rotation-photo-consent")).to_have_attribute("data-consent", "unknown")
        # Duet rows get one marker per singer pill.
        expect(rows.nth(3).locator(".rotation-photo-consent")).to_have_count(2)
        assert "does NOT want" in rows.nth(1).locator(".rotation-photo-consent").get_attribute("title")

        # Unknown = no consent: rendered struck-through like "no".
        assert "assume NO photos" in rows.nth(2).locator(".rotation-photo-consent").get_attribute("title")
        # Click toggles: yes → no, no → yes, unknown → yes. (The stubbed
        # response carries no entries, so the rows don't re-render between clicks.)
        rows.nth(0).locator(".rotation-photo-consent").click()
        rows.nth(1).locator(".rotation-photo-consent").click()
        rows.nth(2).locator(".rotation-photo-consent").click()
        for _ in range(50):
            if len(posted) >= 3:
                break
            page.wait_for_timeout(50)
        assert posted == [
            {"singer": "Lindsay", "consent": "no"},
            {"singer": "Celine", "consent": "yes"},
            {"singer": "Bevbot", "consent": "yes"},
        ]
