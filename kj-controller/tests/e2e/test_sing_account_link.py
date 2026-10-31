"""Web claim (spec 5.6): "Save tonight's songs to your Nomad Karaoke account" on
Your songs / Sung tonight, and offered with one tap after a make-it sign-in.

gen is faked at kjbox's own routes (page.route), like the make-it wizard tests.
Set SING_E2E_SCREENSHOTS=<dir> to save screenshots of each state.
"""

import json
import os

from playwright.sync_api import expect

from tests.e2e.test_sing_i18n_footer import _login
from tests.e2e.test_sing_make_wizard import _open_wizard

EMAIL = "mary@example.com"
LINK = "Save tonight's songs to an account"
EXPLAINER = ("We'll save the songs you sing tonight to your Nomad Karaoke account, "
             "so the app can show your history and recommend songs.")


def _json(route, body, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def _shot(page, name):
    out = os.environ.get("SING_E2E_SCREENSHOTS")
    if out:
        os.makedirs(out, exist_ok=True)
        page.screenshot(path=os.path.join(out, f"{name}.png"), full_page=True)


def _song(rid, title, performed=False):
    return {"request": {"id": rid, "singer_name": "Alice", "song_artist": "Nova Lane",
                        "song_title": title, "source_type": "local", "status": "approved",
                        "created_at": "now", "linked_entry_id": 100 + rid,
                        "additional_singers": None},
            "performed": performed,
            **({} if performed else {"estimate": {"position": 2, "now_singing": False}})}


def _fake_account(page, email=None, linked=False):
    """Fake kjbox's account + claim routes. Returns the recorded calls."""
    acct = {"ready": True, "email": email, "linked": linked,
            "google_client_id": "g-client", "apple_client_id": "a-client"}
    calls = {"send": [], "verify": [], "link": [], "unlink": [], "signout": [], "social": []}

    def send(route):
        calls["send"].append(json.loads(route.request.post_data))
        _json(route, {"status": "sent"})

    def verify(route):
        body = json.loads(route.request.post_data)
        calls["verify"].append(body)
        acct["email"] = body["email"]
        _json(route, {"email": body["email"]})

    def link(route):
        calls["link"].append(json.loads(route.request.post_data))
        acct["linked"] = True
        _json(route, {"linked": True, "email": acct["email"]})

    def unlink(route):
        calls["unlink"].append(json.loads(route.request.post_data))
        acct["linked"] = False
        _json(route, {"linked": False})

    page.route("**/sing/make/account*", lambda r: _json(r, dict(acct)))
    page.route("**/sing/make/send-code*", send)
    page.route("**/sing/make/verify-code*", verify)
    page.route("**/sing/account/link*", link)
    page.route("**/sing/account/unlink*", unlink)

    def signout(route):
        calls["signout"].append(json.loads(route.request.post_data))
        acct.update(email=None, linked=False)
        _json(route, {"status": "ok"})
    page.route("**/sing/make/sign-out*", signout)

    def social(route):
        body = json.loads(route.request.post_data)
        calls["social"].append(body)
        acct["email"] = "new@example.com"
        _json(route, {"email": "new@example.com"})
    page.route("**/sing/make/social*", social)
    return calls


def _open_your_songs(page, live_server, live_token):
    _login(page, live_server, live_token)
    page.evaluate("(s) => localStorage.setItem('sing_my_request_ids', JSON.stringify(s))",
                  {"token": live_token, "ids": [11, 12], "tokens": {}})
    page.route("**/sing/my-requests*", lambda r: _json(r, {
        "now_playing": {"now_singing": None, "up_next": None, "queued_count": 0},
        "requests": [_song(11, "Golden Hour"), _song(12, "Paper Moons", performed=True)]}))
    page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")


class TestSaveTonightsSongs:
    def test_link_flow_from_your_songs(self, page, live_server, live_token):
        calls = _fake_account(page)
        _open_your_songs(page, live_server, live_token)
        main = page.locator('[data-testid="save-songs-main"]')
        expect(main.locator('[data-testid="save-songs-link"]')).to_have_text(LINK)
        # One quiet line at the bottom of the card: the explainer waits behind ⓘ,
        # and the line sits below the buttons, not up by the name (2026-10-29).
        explainer = main.locator('[data-testid="save-songs-explainer"]')
        expect(explainer).to_be_hidden()
        main.locator('[data-testid="save-songs-info"]').click()
        expect(explainer).to_have_text(EXPLAINER)
        assert page.evaluate("""() => {
            const line = document.querySelector('[data-testid="save-songs-main"]');
            const btn = document.querySelector('[data-testid="request-another"]');
            return !!(btn.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING);
        }""")
        # The line shows once, never again in Sung tonight (A9-R5).
        page.locator(".sung-section summary").click()
        expect(page.locator('[data-testid="sung-section"]')).to_be_visible()
        expect(page.locator('[data-testid="save-songs-sung"]')).to_have_count(0)
        expect(page.locator('[data-testid="save-songs-link"]')).to_have_count(1)
        _shot(page, "a9-1-your-songs-link")

        # A make-it wizard opened earlier, still at its email step.
        page.evaluate("window.__sing_state.make = {stage: 'account', email: ''}")
        # Not signed in on this phone: the email code comes first.
        main.locator('[data-testid="save-songs-link"]').click()
        main.locator('[data-testid="save-songs-cancel"]').click()     # changed their mind
        expect(main.locator('[data-testid="save-songs-email"]')).to_have_count(0)
        main.locator('[data-testid="save-songs-link"]').click()
        main.locator('[data-testid="save-songs-email"]').fill(EMAIL)
        _shot(page, "a9-2-email")
        main.locator('[data-testid="save-songs-send-code"]').click()
        code = main.locator('[data-testid="save-songs-code"]')
        expect(code).to_be_visible()
        _shot(page, "a9-3-code")
        assert calls["link"] == []                     # nothing linked before the code
        code.fill("123456")                             # six digits auto-submit

        status = main.locator('[data-testid="save-songs-status"]')
        expect(status).to_contain_text(f"Saving tonight's songs to {EMAIL}")
        expect(page.locator('[data-testid="save-songs-status"]')).to_have_count(1)
        expect(page.locator('[data-testid="save-songs-sung"]')).to_have_count(0)
        _shot(page, "a9-4-linked")
        assert calls["send"][0]["email"] == EMAIL
        assert calls["send"][0]["purpose"] == "save" and calls["verify"][0]["purpose"] == "save"
        # The wizard knows about the sign-in, so it won't ask for the email again.
        assert page.evaluate("window.__sing_state.make.email") == EMAIL
        assert calls["verify"][0]["code"] == "123456"
        device = calls["verify"][0]["device_id"]
        assert len(device) == 32 and calls["link"][0]["device_id"] == device

        main.locator('[data-testid="save-songs-stop"]').click()
        # Still signed in, so the line names the account it would save to.
        expect(main.locator('[data-testid="save-songs-link"]')).to_have_text(
            f"Save tonight's songs to {EMAIL}")
        assert calls["unlink"][0]["device_id"] == device

    def test_signed_in_device_links_with_one_tap(self, page, live_server, live_token):
        calls = _fake_account(page, email=EMAIL)
        _open_your_songs(page, live_server, live_token)
        main = page.locator('[data-testid="save-songs-main"]')
        main.locator('[data-testid="save-songs-link"]').click()
        expect(main.locator('[data-testid="save-songs-status"]')).to_contain_text(
            f"Saving tonight's songs to {EMAIL}")
        assert calls["send"] == [] and len(calls["link"]) == 1

    def test_signed_in_device_names_the_account_before_linking(self, page, live_server, live_token):
        _fake_account(page, email=EMAIL)
        _open_your_songs(page, live_server, live_token)
        main = page.locator('[data-testid="save-songs-main"]')
        expect(main.locator('[data-testid="save-songs-link"]')).to_have_text(
            f"Save tonight's songs to {EMAIL}")
        expect(main.locator('[data-testid="save-songs-logout"]')).to_have_text("Log out")

    def test_log_out_signs_out_and_offers_sign_in(self, page, live_server, live_token):
        calls = _fake_account(page, email=EMAIL)
        _open_your_songs(page, live_server, live_token)
        page.evaluate("localStorage.setItem('nk_make_email', 'old@example.com')")
        main = page.locator('[data-testid="save-songs-main"]')
        main.locator('[data-testid="save-songs-logout"]').click()
        expect(main.locator('[data-testid="save-songs-email"]')).to_be_visible()
        assert len(calls["signout"]) == 1 and calls["link"] == []
        assert page.evaluate("localStorage.getItem('nk_make_email')") is None

    def test_log_out_then_google_saves_to_the_new_account(self, page, live_server, live_token):
        page.add_init_script("window.__SING_SOCIAL_STUB = {google: 'g-tok', apple: 'a-tok'};")
        calls = _fake_account(page, email=EMAIL, linked=True)
        _open_your_songs(page, live_server, live_token)
        main = page.locator('[data-testid="save-songs-main"]')
        main.locator('[data-testid="save-songs-logout"]').click()
        expect(main.locator('[data-testid="save-songs-apple"]')).to_be_visible()
        main.locator('[data-testid="save-songs-google"] button').click()
        expect(main.locator('[data-testid="save-songs-status"]')).to_contain_text(
            "Saving tonight's songs to new@example.com")
        assert calls["social"][0]["provider"] == "google"
        assert calls["social"][0]["id_token"] == "g-tok"
        assert calls["social"][0]["purpose"] == "save"
        assert len(calls["link"]) == 1

    def test_linked_account_can_be_switched(self, page, live_server, live_token):
        calls = _fake_account(page, email=EMAIL, linked=True)
        _open_your_songs(page, live_server, live_token)
        main = page.locator('[data-testid="save-songs-main"]')
        expect(main.locator('[data-testid="save-songs-status"]')).to_contain_text(EMAIL)
        main.locator('[data-testid="save-songs-logout"]').click()
        expect(main.locator('[data-testid="save-songs-email"]')).to_be_visible()
        expect(main.locator('[data-testid="save-songs-status"]')).to_have_count(0)
        assert len(calls["signout"]) == 1

    def test_already_linked_shows_status(self, page, live_server, live_token):
        _fake_account(page, email=EMAIL, linked=True)
        _open_your_songs(page, live_server, live_token)
        expect(page.locator('[data-testid="save-songs-main"] [data-testid="save-songs-stop"]')
               ).to_have_text("Stop saving")

    def test_make_sign_in_never_links_and_keeps_the_make_screen_clean(self, page, live_server, live_token):
        # The one-screen make-it has a single job; "Save tonight's songs" is
        # offered on the done screen (the "main" slot) once the song is sent.
        calls = {"link": []}
        page.route("**/sing/account/link*", lambda r: (
            calls["link"].append(1), _json(r, {"linked": True, "email": EMAIL})))
        _open_wizard(page, live_server, live_token)
        page.locator('[data-testid="make-email"]').fill(EMAIL)
        page.locator('[data-testid="make-send-code"]').click()
        page.locator('[data-testid="make-code"]').fill("123456")
        expect(page.locator('[data-testid="make-pick"]')).to_be_visible()
        expect(page.locator('[data-testid="save-songs-offer"]')).to_have_count(0)
        assert calls["link"] == []                      # signing in never links by itself

    def test_signed_in_make_without_fresh_sign_in_has_no_offer(self, page, live_server, live_token):
        _open_wizard(page, live_server, live_token, email=EMAIL)
        expect(page.locator('[data-testid="make-pick"]')).to_be_visible()
        expect(page.locator('[data-testid="save-songs-offer"]')).to_be_hidden()


class TestSaveWithMakeItOff:
    """Make-it switched off (or simple mode): the save sign-in still works,
    end to end through the real kjbox routes with only gen faked."""

    def test_make_off_signed_out_link_code_linked(self, page, live_server, live_token, live_app):
        app = live_app
        store = app.sing_store
        gen = _FakeGen()
        old_gen = getattr(app, "gen_client", None)
        app.gen_client = gen
        store.set_accepting_make_requests(False)
        try:
            _open_your_songs(page, live_server, live_token)
            main = page.locator('[data-testid="save-songs-main"]')
            main.locator('[data-testid="save-songs-link"]').click()
            main.locator('[data-testid="save-songs-email"]').fill(EMAIL)
            main.locator('[data-testid="save-songs-send-code"]').click()
            main.locator('[data-testid="save-songs-code"]').fill("123456")
            expect(main.locator('[data-testid="save-songs-status"]')).to_contain_text(
                f"Saving tonight's songs to {EMAIL}")
            _shot(page, "a9-7-make-off-linked")
            device = page.evaluate("localStorage.getItem('sing_device_id')")
            assert store.account_for_device(device, store.get_night_started_at()) == EMAIL
            assert gen.sent == [EMAIL]
        finally:
            store.set_accepting_make_requests(True)
            app.gen_client = old_gen


class _FakeGen:
    def __init__(self):
        self.sent = []

    def singer_flow_configured(self):
        return True

    def send_login_code(self, email, locale=None, venue=None):
        self.sent.append(email)

    def verify_login_code(self, email, code, locale=None):
        return {"session_token": "sess-e2e", "user": {}}
