"""Singer make-it wizard (make.js): email code → corrected song → audio pick → confirm.

gen is mocked at kjbox's /sing/make/* proxy (page.route), so this drives the
real singer UI + the gen-ported categorisation / best-pick / correction logic.
"""

import json

from playwright.sync_api import expect

from tests.e2e.test_sing_i18n_footer import _login

LOSSLESS = {"index": 0, "provider": "RED", "title": "Pablo Honey", "artist": "Radiohead",
            "is_lossless": True, "seeders": 120, "release_type": "Album", "year": 1993,
            "target_file": "02 - Creep.flac"}
SPOTIFY = {"index": 1, "provider": "Spotify", "title": "Creep", "artist": "Radiohead",
           "is_lossless": False}
YOUTUBE = {"index": 2, "provider": "YouTube", "title": "Radiohead - Creep (Official)",
           "channel": "Radiohead", "is_lossless": False, "view_count": 1500000}


def _json(route, body, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def _open_wizard(page, live_server, live_token, *, email=None, results=None, fast=None, full=None):
    page.add_init_script("window.__SING_ARM_MS = 0;")
    _login(page, live_server, live_token)
    calls = {"send": [], "verify": [], "search": [], "check": []}

    def check(route):
        body = json.loads(route.request.post_data or "{}")
        calls["check"].append(body)
        verdict = (full if body.get("stage") == "full" else fast) or {"kind": "none", "confident": False}
        _json(route, verdict)

    def search(route):
        calls["search"].append(json.loads(route.request.post_data or "{}"))
        _json(route, {"search_session_id": "ss-1", "results": results if results is not None else [LOSSLESS, SPOTIFY, YOUTUBE]})

    page.route("**/sing/make/account*", lambda r: _json(r, {"ready": True, "email": email}))
    page.route("**/sing/make/send-code*", lambda r: (calls["send"].append(1), _json(r, {"status": "sent"})))
    page.route("**/sing/make/verify-code*", lambda r: (calls["verify"].append(json.loads(r.request.post_data)),
                                                     _json(r, {"email": "mary@example.com"})))
    page.route("**/sing/make/check*", check)
    page.route("**/sing/make/search*", search)
    page.evaluate("""
        window.__sing_state.makeRequestsEnabled = true;
        window.__sing_state.makeArtist = 'radiohed';
        window.__sing_state.makeTitle = 'creep';
        window.__sing_state.step = 'search';
        window.__sing_render();
    """)
    page.route("**/sing/search*", lambda r: _json(r, {"songs": [], "make_requests_enabled": True, "simple_mode": False}))
    page.locator('input[type="search"]').fill("radiohed creep")
    page.locator('[data-testid="make-card"] button').click()
    return calls


class TestMakeWizard:
    def test_email_code_then_best_pick_to_confirm(self, page, live_server, live_token):
        calls = _open_wizard(page, live_server, live_token, fast={
            "kind": "cosmetic", "confident": True, "needs_ai": False, "engine": "catalog",
            "canonical_artist": "Radiohead", "canonical_title": "Creep"})
        page.locator('[data-testid="make-email"]').fill("mary@example.com")
        page.locator('[data-testid="make-send-code"]').click()
        code = page.locator('[data-testid="make-code"]')
        expect(code).to_be_visible()
        code.fill("123456")              # six digits auto-submit
        expect(page.locator('[data-testid="make-pick"]')).to_contain_text("Perfect match found")
        assert calls["verify"][0]["code"] == "123456"
        # Lazy typing corrected by gen's match-judge, with undo.
        notice = page.locator('[data-testid="make-correction"]')
        expect(notice).to_contain_text("Corrected to Creep — Radiohead")
        # Other options are one tap away, grouped like gen.
        page.locator('[data-testid="make-others-toggle"]').click()
        expect(page.locator(".mk-cat-title").first).to_have_text("Spotify")
        page.locator(".mk-use.primary").click()
        expect(page.locator("h2")).to_have_text("Is this the right song?")
        expect(page.locator('[data-testid="confirm-make-email"]')).to_contain_text("mary@example.com")
        state = page.evaluate("window.__sing_state.selected")
        assert state["song_artist"] == "Radiohead" and state["song_title"] == "Creep"
        assert state["source_meta"] == {**state["source_meta"], "search_session_id": "ss-1", "selection_index": 0}

    def test_signed_in_device_skips_straight_to_audio(self, page, live_server, live_token):
        calls = _open_wizard(page, live_server, live_token, email="mary@example.com")
        expect(page.locator('[data-testid="make-pick"]')).to_be_visible()
        assert calls["send"] == []
        expect(page.locator(".mk-signed-in")).to_contain_text("mary@example.com")

    def test_undo_restores_what_was_typed(self, page, live_server, live_token):
        _open_wizard(page, live_server, live_token, email="m@x.co", fast={
            "kind": "cosmetic", "confident": True, "needs_ai": False, "engine": "catalog",
            "canonical_artist": "Radiohead", "canonical_title": "Creep"})
        page.locator(".mk-undo").click()
        expect(page.locator(".mk-song-artist")).to_have_text("radiohed")

    def test_did_you_mean_re_searches(self, page, live_server, live_token):
        calls = _open_wizard(page, live_server, live_token, email="m@x.co",
                             fast={"kind": "none", "confident": False, "needs_ai": True},
                             full={"kind": "ambiguous", "confident": False,
                                   "canonical_artist": "Radiohead", "canonical_title": "Creep",
                                   "alternatives": [{"artist": "Stone Temple Pilots", "title": "Creep"}]})
        dym = page.locator('[data-testid="make-didyoumean"]')
        expect(dym).to_be_visible()
        dym.locator(".mk-suggestion").nth(1).click()
        expect(page.locator(".mk-song-artist")).to_have_text("Stone Temple Pilots")
        expect(page.locator('[data-testid="make-pick"]')).to_be_visible()
        assert calls["search"][-1]["artist"] == "Stone Temple Pilots"

    def test_youtube_only_shows_guidance_and_link_first(self, page, live_server, live_token):
        _open_wizard(page, live_server, live_token, email="m@x.co", results=[YOUTUBE])
        expect(page.locator('[data-testid="make-guidance"]')).to_contain_text("Limited sources found")
        expect(page.locator(".mk-fallback-first")).to_be_visible()
        expect(page.locator('[data-testid="make-result"]')).to_have_count(1)

    def test_no_results(self, page, live_server, live_token):
        _open_wizard(page, live_server, live_token, email="m@x.co", results=[])
        expect(page.locator(".mk-none")).to_contain_text("couldn't find any audio")
        expect(page.locator('[data-testid="make-fallback"]')).to_be_visible()

    def test_wrong_code_message(self, page, live_server, live_token):
        _open_wizard(page, live_server, live_token)
        page.unroute("**/sing/make/verify-code*")
        page.route("**/sing/make/verify-code*", lambda r: _json(r, {"error": "invalid_code"}, 400))
        page.locator('[data-testid="make-email"]').fill("mary@example.com")
        page.locator('[data-testid="make-send-code"]').click()
        page.locator('[data-testid="make-code"]').fill("000000")
        expect(page.locator('[data-testid="make-error"]')).to_contain_text("That code isn't right")
