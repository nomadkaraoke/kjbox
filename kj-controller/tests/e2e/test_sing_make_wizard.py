"""Singer make-it wizard (make.js): email code → corrected song → audio pick → confirm.

gen is mocked at kjbox's /sing/make/* proxy (page.route), so this drives the
real singer UI + the gen-ported categorisation / best-pick / correction logic.
"""

import json

import pytest

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


def _open_wizard(page, live_server, live_token, *, email=None, results=None, fast=None, full=None,
                 held=None, account=None):
    page.add_init_script("window.__SING_ARM_MS = 0;")
    _login(page, live_server, live_token)
    calls = {"send": [], "verify": [], "search": [], "check": []}

    def check(route):
        body = json.loads(route.request.post_data or "{}")
        calls["check"].append(body)
        if held is not None and body.get("stage") == "full":
            held.append(route)   # the test answers it later (a slow AI judge)
            return
        verdict = (full if body.get("stage") == "full" else fast) or {"kind": "none", "confident": False}
        _json(route, verdict)

    def search(route):
        calls["search"].append(json.loads(route.request.post_data or "{}"))
        _json(route, {"search_session_id": "ss-1", "results": results if results is not None else [LOSSLESS, SPOTIFY, YOUTUBE]})

    page.route("**/sing/make/account*", lambda r: _json(r, {"ready": True, "email": email, **(account or {})}))
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
        # The hero card shows what matters when picking audio — like gen's
        # Choose Audio step: the in-torrent filename, the release, format,
        # availability and the bracketed release metadata.
        pick = page.locator('[data-testid="make-pick"]')
        expect(pick).to_contain_text("High-quality lossless, from Album, 1993, reliable download")
        expect(pick.locator(".mk-mono")).to_have_text("02 - Creep.flac")
        expect(pick).to_contain_text("Release:Radiohead - Pablo Honey")
        expect(pick.locator(".mk-avail-high")).to_have_text("High avail.")
        expect(pick).to_contain_text("[Album / 1993]")
        # Other options are one tap away, grouped like gen (with counts).
        page.locator('[data-testid="make-change-audio"]').click()
        expect(page.locator(".mk-others-help")).to_contain_text("Check the filename matches your song")
        expect(page.locator(".mk-cat-count").first).to_have_text("(1)")
        expect(page.locator('[data-testid="make-change-audio"]')).to_have_text("Hide other options")
        # One screen, one send: no separate confirm step.
        page.route("**/sing/submit*", lambda r: _json(r, {"auto_approved": True, "request": {
            "id": 77, "edit_token": "et", "song_artist": "Radiohead", "song_title": "Creep",
            "source_type": "make", "status": "approved", "singer_name": "Alice", "linked_entry_id": 5}}))
        send = page.locator('[data-testid="make-submit"]')
        expect(send).to_have_text("🎤 Make it & add me to the list")
        with page.expect_request("**/sing/submit*") as req:
            send.click()
        body = json.loads(req.value.post_data)
        assert body["song_artist"] == "Radiohead" and body["song_title"] == "Creep"
        assert body["source_meta"] == {**body["source_meta"], "search_session_id": "ss-1", "selection_index": 0}
        banner = page.locator('[data-testid="sent-banner"]')
        expect(banner).to_contain_text("You're on the list!")
        expect(banner).to_contain_text("Creep — Radiohead")
        expect(page.locator('[data-testid="sent-tracker"]')).to_contain_text("Being made")
        expect(page.locator('[data-testid="sent-email"]')).to_contain_text("mary@example.com")

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

    def test_undo_drops_a_recording_chosen_for_the_other_title(self, page, live_server, live_token):
        _open_wizard(page, live_server, live_token, email="m@x.co", fast={
            "kind": "cosmetic", "confident": True, "needs_ai": False, "engine": "catalog",
            "canonical_artist": "Radiohead", "canonical_title": "Creep"})
        page.locator('[data-testid="make-change-audio"]').click()
        page.locator(".mk-select:not([disabled])").first.click()
        expect(page.locator('[data-testid="make-pick"]')).to_contain_text("Your choice")
        page.locator(".mk-undo").click()
        expect(page.locator(".mk-song-artist")).to_have_text("radiohed")
        expect(page.locator('[data-testid="make-pick"]')).not_to_contain_text("Your choice")

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
        expect(page.locator('[data-testid="make-guidance"]')).to_contain_text("Pick the recording to use")
        expect(page.locator(".mk-fallback-first")).to_be_visible()
        expect(page.locator('[data-testid="make-result"]')).to_have_count(1)
        # Not sure which recording: the singer picks one before the send unlocks.
        send = page.locator('[data-testid="make-submit"]')
        expect(send).to_be_disabled()
        expect(send).to_have_text("Pick a recording above")
        page.locator(".mk-select").first.click()
        expect(send).to_be_enabled()
        expect(page.locator('[data-testid="make-pick"]')).to_contain_text("Your choice")

    def test_spotify_title_match_is_recommended_and_youtube_collapsed(self, page, live_server, live_token):
        # Live 2026-10-01: a Spotify-only search showed "Limited sources found" with a
        # wrong-track pick and the YouTube box first, so the singer pasted a YouTube link.
        # Spotify `title` is the album, `target_file` the track.
        def sp(i, album, track, pop):
            return {"index": i, "provider": "Spotify", "title": album, "target_file": track,
                    "artist": "Radiohead", "is_lossless": False, "quality": "FLAC 16bit WEB",
                    "release_type": "Single", "view_count": pop * 10000}
        cover = {**sp(3, "Covers", "Creep", 95), "artist": "Postmodern Jukebox"}  # more popular, wrong artist
        _open_wizard(page, live_server, live_token, email="m@x.co",
                     results=[sp(0, "Pablo Honey", "Anyone Can Play Guitar", 60), cover,
                              sp(1, "Creep", "Creep", 80), sp(2, "Hits", "Creep", 30)])
        pick = page.locator('[data-testid="make-pick"]')
        expect(pick).to_contain_text("Recommended audio")
        expect(pick).to_contain_text("Official release on Spotify")
        expect(pick.locator(".mk-mono")).to_have_text("Creep")
        expect(pick).to_contain_text("Radiohead - Creep")  # not the Postmodern Jukebox cover
        expect(pick).not_to_contain_text("No lossless sources")
        expect(page.locator('[data-testid="make-guidance"]')).to_have_count(0)
        expect(page.locator('[data-testid="make-fallback"]')).to_have_count(0)
        page.locator('[data-testid="make-change-audio"]').click()
        expect(page.locator('[data-testid="make-fallback"]')).to_be_visible()

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

    def test_mismatched_filename_flagged_and_matches_sorted_first(self, page, live_server, live_token):
        wrong = {**LOSSLESS, "index": 3, "seeders": 60, "target_file": "Pablo Honey/03 - Anyone Can Play Guitar.flac"}
        right = {**LOSSLESS, "index": 4, "seeders": 55, "target_file": "Pablo Honey/02 - Creep.flac"}
        _open_wizard(page, live_server, live_token, email="m@x.co", results=[LOSSLESS, wrong, right])
        page.locator('[data-testid="make-change-audio"]').click()
        rows = page.locator('.mk-cat[data-category="BEST CHOICE"] .mk-row')
        expect(rows).to_have_count(3)
        expect(rows.nth(0)).to_contain_text("02 - Creep.flac")
        expect(rows.nth(0).locator(".mk-tag-match")).to_have_text("Title match")
        expect(rows.nth(2).locator(".mk-tag-warn")).to_have_text("Wrong track?")
        expect(rows.nth(0).locator(".mk-select")).to_have_text("✓ Selected")   # the auto-pick



class TestMySongsMakePhases:
    """My songs wording per make phase, and "Tap here" → gen review sign-in link."""

    def _open(self, page, live_server, live_token, phase):
        _login(page, live_server, live_token)
        page.evaluate("(t) => localStorage.setItem('sing_my_request_ids', JSON.stringify("
                      "{token: t, ids: [31], tokens: {'31': 'tok31'}}))", live_token)
        item = {"request": {"id": 31, "singer_name": "Alice", "song_artist": "The Strokes",
                            "song_title": "Machu Picchu", "source_type": "make", "status": "approved",
                            "created_at": "now", "linked_entry_id": 9, "additional_singers": None},
                "performed": False, "make": phase}
        page.route("**/sing/my-requests*", lambda r: _json(r, {
            "now_playing": {"now_singing": None, "up_next": None, "queued_count": 1},
            "requests": [item]}))
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")
        return page.locator(".song-card-status").first

    def test_wording_per_phase(self, page, live_server, live_token):
        status = self._open(page, live_server, live_token, "making")
        expect(status).to_have_text("✨ Being made for you — held off the rotation until it's ready.")
        page.unroute("**/sing/my-requests*")
        status = self._open(page, live_server, live_token, "rendering")
        expect(status).to_contain_text("lyrics review completed, rendering")
        expect(status).to_contain_text("~10 minutes")

    def test_review_link_opens_signed_in_review(self, page, live_server, live_token):
        status = self._open(page, live_server, live_token, "review")
        expect(status).to_contain_text("needs lyrics sync review")
        expect(status).to_contain_text("or the host will as soon as possible")
        target = f"{live_server}/sing/sw.js?review=ok"
        sent = {}

        def on_link(route):
            sent.update(json.loads(route.request.post_data))
            _json(route, {"url": target, "review_started_by": None})
        page.route("**/sing/make/review-link/31*", on_link)
        with page.expect_popup() as popup_info:
            status.locator('[data-testid="make-review-link"]').click()
        popup = popup_info.value
        popup.wait_for_url("**review=ok")
        assert sent["edit_token"] == "tok31" and sent["device_id"]

    def test_host_already_reviewing(self, page, live_server, live_token):
        status = self._open(page, live_server, live_token, "review_host")
        expect(status).to_contain_text("the host has already started the lyrics sync review")
        expect(status.locator('[data-testid="make-review-link"]')).to_have_text("Tap here")

    def test_review_over_is_explained(self, page, live_server, live_token):
        status = self._open(page, live_server, live_token, "review_self")
        page.route("**/sing/make/review-link/31*", lambda r: _json(r, {"error": "not_in_review"}, 409))
        msgs = []
        page.on("dialog", lambda d: (msgs.append(d.message), d.dismiss()))
        # The alert fires only after the 409 comes back — wait for it explicitly.
        with page.expect_event("dialog"):
            with page.expect_popup():
                status.locator('[data-testid="make-review-link"]').click()
        expect(status.locator('[data-testid="make-review-link"]')).to_have_text("Tap here")
        assert any("already finished" in m for m in msgs)


class TestMySongsQuickVersion:
    """Make-it quick (draft) version: offer → preview / sing it now → explained."""

    def _open(self, page, live_server, live_token, quick, make="making"):
        _login(page, live_server, live_token)
        page.evaluate("(t) => localStorage.setItem('sing_my_request_ids', JSON.stringify("
                      "{token: t, ids: [31], tokens: {'31': 'tok31'}}))", live_token)
        state = {"quick": quick, "make": make}

        def my_requests(route):
            item = {"request": {"id": 31, "singer_name": "Alice", "song_artist": "The Strokes",
                                "song_title": "Machu Picchu", "source_type": "make",
                                "status": "approved", "created_at": "now", "linked_entry_id": 9,
                                "additional_singers": None},
                    "performed": False, "make": state["make"], "quick": state["quick"]}
            _json(route, {"now_playing": {"now_singing": None, "up_next": None, "queued_count": 1},
                          "requests": [item]})
        page.route("**/sing/my-requests*", my_requests)
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")
        return state

    def test_offer_preview_and_sing_now(self, page, live_server, live_token):
        state = self._open(page, live_server, live_token, "ready")
        offer = page.locator('[data-testid="quick-offer"]')
        expect(offer).to_contain_text("A quick version is ready now!")
        # The card still says the full version is being made.
        expect(page.locator(".song-card-status").first).to_contain_text("Being made")

        previews = []
        page.route("**/sing/preview/resolve*", lambda r: (
            previews.append(json.loads(r.request.post_data)),
            _json(r, {"mode": "unavailable", "reason": "test"})))
        with page.expect_response("**/sing/preview/resolve*"):
            offer.locator('[data-testid="quick-preview"]').click()
        assert previews and previews[0]["source"] == "entry_quick" and previews[0]["entry_id"] == 9
        page.keyboard.press("Escape")

        sent = {}

        def use_quick(route):
            sent.update(json.loads(route.request.post_data))
            state.update(quick="chosen", make=None)
            _json(route, {"success": True})
        page.route("**/sing/make/use-quick/31*", use_quick)
        page.on("dialog", lambda d: d.accept())
        offer.locator('[data-testid="quick-sing-now"]').click()
        expect(page.locator('[data-testid="quick-chosen"]')).to_contain_text(
            "You're singing the quick version")
        expect(page.locator('[data-testid="quick-offer"]')).to_have_count(0)
        assert sent == {"edit_token": "tok31", "device_id": sent["device_id"]} and sent["device_id"]

    def test_upgraded_is_explained(self, page, live_server, live_token):
        self._open(page, live_server, live_token, "upgraded", make=None)
        expect(page.locator('[data-testid="quick-upgraded"]')).to_contain_text(
            "The full version is ready")

    def test_no_offer_without_quick(self, page, live_server, live_token):
        self._open(page, live_server, live_token, None)
        expect(page.locator(".song-card-status").first).to_contain_text("Being made")
        expect(page.locator('[data-testid="quick-offer"]')).to_have_count(0)


# --- 2026-10-22 show: a correct identification overruled, a late swap -------

I_WILL = {"index": 0, "provider": "Spotify", "title": "Helluva Boss: Season Two (Original Soundtrack)",
          "artist": "Helluva Boss, Barrett Wilbert Weed", "is_lossless": False, "target_file": "I Will Be Okay",
          "release_type": "Album", "year": 2025, "view_count": 610000}
YOU_WILL = {"index": 1, "provider": "Spotify", "title": "You Will Be Okay",
            "artist": "Helluva Boss, Bryce Pinkham, Sam Haft", "is_lossless": False, "target_file": "You Will Be Okay",
            "release_type": "Single", "year": 2025, "view_count": 500000}
AI_YOU_WILL = {"kind": "content", "confident": True, "engine": "ai", "needs_ai": False,
               "canonical_artist": "Helluva Boss", "canonical_title": "You Will Be Okay", "alternatives": []}


def _open_identified(page, live_server, live_token, *, full, events=None, email="anya@example.com"):
    """Type a query the device identifies confidently, with no karaoke version, and
    tap the empty-state "Generate it": the make form is pre-filled from the identification."""
    page.add_init_script("window.__SING_ARM_MS = 0;")
    _login(page, live_server, live_token)
    calls = {"check": []}

    def check(route):
        body = json.loads(route.request.post_data or "{}")
        calls["check"].append(body)
        _json(route, full if body.get("stage") == "full" else
              {"kind": "none", "confident": False, "needs_ai": True, "engine": "catalog"})

    page.route("**/sing/search/identify*", lambda r: _json(r, {
        "status": "confident", "kind": "cosmetic", "typed": "i will be okay helluva boss",
        "song": {"artist": "Helluva Boss", "title": "I Will Be Okay", "karaoke": True},
        "candidates": [{"artist": "Helluva Boss", "title": "You Will Be Okay", "karaoke": True}]}))
    page.route("**/sing/search?*", lambda r: _json(r, {"songs": [], "make_requests_enabled": True,
                                                      "simple_mode": False}))
    page.route("**/sing/search/resolve*", lambda r: _json(r, {}))
    if events is not None:
        page.route("**/sing/search/event*", lambda r: (events.append(json.loads(r.request.post_data or "{}")),
                                                       _json(r, {"ok": True})))
    page.route("**/sing/make/account*", lambda r: _json(r, {"ready": True, "email": email}))
    page.route("**/sing/make/check*", check)
    page.route("**/sing/make/search*", lambda r: _json(r, {"search_session_id": "ss-1",
                                                           "results": [I_WILL, YOU_WILL]}))
    page.evaluate("""
        window.__sing_state.makeRequestsEnabled = true;
        window.__sing_state.step = 'search';
        window.__sing_render();
    """)
    page.locator('input[type="search"]').fill("i will be okay helluva boss")
    # Confident identification, nothing in the library: one card, one tap.
    card = page.locator('[data-testid="song-missing"]')
    expect(card).to_contain_text("I Will Be Okay")
    expect(page.locator('[data-testid="make-card"]')).to_have_count(0)    # no long triage
    card.locator('[data-testid="song-missing-make"]').click()
    return calls


class TestIdentifiedSongIsTrusted:
    def test_ai_judge_cannot_overrule_a_confident_identification(self, page, live_server, live_token):
        calls = _open_identified(page, live_server, live_token, full=AI_YOU_WILL)
        pick = page.locator('[data-testid="make-pick"]')
        expect(pick.locator(".mk-mono")).to_have_text("I Will Be Okay")
        expect(page.locator('[data-testid="make-submit"]')).to_be_enabled()
        expect(page.locator(".mk-song-title")).to_have_text("I Will Be Okay")
        assert [c["stage"] for c in calls["check"]] == ["fast"]       # no AI judge
        expect(page.locator('[data-testid="make-correction"]')).to_have_count(0)

    def test_typed_song_still_gets_the_judge(self, page, live_server, live_token):
        # The singer edits the pre-filled title: their own text is not an
        # identification, so gen's judge runs as before.
        page.add_init_script("window.__SING_JUDGE_GATE_MS = 60000;")
        calls = _open_wizard(page, live_server, live_token, email="m@x.co",
                             fast={"kind": "none", "confident": False, "needs_ai": True},
                             full={"kind": "none", "confident": True})
        expect(page.locator('[data-testid="make-pick"]')).to_be_visible()
        expect(page.locator('[data-testid="make-submit"]')).to_be_enabled()
        assert "full" in [c["stage"] for c in calls["check"]]


class TestNoLateSwap:
    def test_correction_after_the_button_is_live_is_only_offered(self, page, live_server, live_token):
        # Live: the AI verdict landed ~20 s after the results, after the 12 s gate
        # had opened, and swapped the title and the recommended audio.
        page.add_init_script("window.__SING_JUDGE_GATE_MS = 50;")
        held = []
        _open_wizard(page, live_server, live_token, email="m@x.co", results=[I_WILL, YOU_WILL],
                     fast={"kind": "none", "confident": False, "needs_ai": True}, held=held)
        page.evaluate("""() => {
            window.__sing_state.make.artist = 'Helluva Boss';
            window.__sing_state.make.title = 'I Will Be Okay';
        }""")
        expect(page.locator('[data-testid="make-submit"]')).to_be_enabled()   # gate opened by timeout
        for _ in range(50):          # the full judge is asked after the fast one answers
            if held:
                break
            page.wait_for_timeout(50)
        assert held, "the full judge was asked"
        held[0].fulfill(status=200, content_type="application/json", body=json.dumps(AI_YOU_WILL))
        expect(page.locator('[data-testid="make-didyoumean"]')).to_contain_text("You Will Be Okay — Helluva Boss")
        expect(page.locator(".mk-song-title")).to_have_text("I Will Be Okay")
        expect(page.locator('[data-testid="make-correction"]')).to_have_count(0)


class TestRememberedEmail:
    def test_expired_sign_in_keeps_the_email(self, page, live_server, live_token):
        page.add_init_script("localStorage.setItem('nk_make_email', 'anya@example.com');")
        _open_wizard(page, live_server, live_token, email=None)
        expect(page.locator('[data-testid="make-email"]')).to_have_value("anya@example.com")

    def test_verified_email_is_remembered_and_not_you_forgets_it(self, page, live_server, live_token):
        _open_wizard(page, live_server, live_token)
        page.locator('[data-testid="make-email"]').fill("mary@example.com")
        page.locator('[data-testid="make-send-code"]').click()
        page.locator('[data-testid="make-code"]').fill("123456")
        expect(page.locator('[data-testid="make-pick"]')).to_be_visible()
        assert page.evaluate("localStorage.getItem('nk_make_email')") == "mary@example.com"
        page.route("**/sing/make/sign-out*", lambda r: _json(r, {}))
        page.locator(".mk-signed-in button").click()
        expect(page.locator('[data-testid="make-email"]')).to_have_value("")
        assert page.evaluate("localStorage.getItem('nk_make_email')") is None


    def test_switch_singer_forgets_the_email(self, page, live_server, live_token):
        page.add_init_script("localStorage.setItem('nk_make_email', 'anya@example.com');")
        page.add_init_script("window.__SING_ARM_MS = 0;")
        _login(page, live_server, live_token)
        page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
        page.locator('[data-testid="edit-name"]').click()
        page.locator('[data-testid="switch-identity"]').click()
        assert page.evaluate("localStorage.getItem('nk_make_email')") is None


class TestEmptyStateOfferLogged:
    def test_shown_and_tap_are_logged(self, page, live_server, live_token):
        events = []
        _open_identified(page, live_server, live_token, full=AI_YOU_WILL, events=events)
        expect(page.locator('[data-testid="make-step"]')).to_be_visible()
        page.wait_for_timeout(300)
        offers = [(e["action"], e["data"].get("where")) for e in events if e["action"].startswith("make_offer")]
        assert ("make_offer_shown", "top_card") in offers
        assert ("make_offer_tap", "top_card") in offers


SOCIAL_IDS = {"google_client_id": "g-client", "apple_client_id": "com.example.web"}


class TestOneScreen:
    def test_before_sign_in_the_send_says_what_is_missing(self, page, live_server, live_token):
        _open_wizard(page, live_server, live_token)
        send = page.locator('[data-testid="make-submit"]')
        expect(send).to_be_disabled()
        expect(send).to_have_text("Sign in above to continue")
        expect(page.locator('[data-testid="make-audio-later"]')).to_be_visible()
        # Email fallback is a real form the browser can autofill.
        email = page.locator('[data-testid="make-email"]')
        assert email.get_attribute("autocomplete") == "email" and email.get_attribute("name") == "email"
        assert page.locator("form.mk-email-form").count() == 1

    def test_social_buttons_hidden_off_the_public_host(self, page, live_server, live_token):
        _open_wizard(page, live_server, live_token, account=SOCIAL_IDS)
        expect(page.locator('[data-testid="make-email"]')).to_be_visible()
        expect(page.locator(".mk-socials")).to_have_count(0)

    @pytest.mark.parametrize("provider,label", [("google", "Continue with Google"), ("apple", "Continue with Apple")])
    def test_one_tap_sign_in_then_audio_then_send(self, page, live_server, live_token, provider, label):
        page.add_init_script("window.__SING_SOCIAL_STUB = {google: 'g-tok', apple: 'a-tok'};")
        social = []
        page.route("**/sing/make/social*", lambda r: (social.append(json.loads(r.request.post_data)),
                                                      _json(r, {"email": "anya@example.com"})))
        calls = _open_wizard(page, live_server, live_token, account=SOCIAL_IDS)
        page.locator(".mk-social", has_text=label).click()
        expect(page.locator('[data-testid="make-signed-in"]')).to_contain_text("anya@example.com")
        assert social[0]["provider"] == provider
        assert social[0]["id_token"] == ("g-tok" if provider == "google" else "a-tok")
        assert calls["send"] == []                                          # no email code
        expect(page.locator('[data-testid="make-pick"]')).to_be_visible()   # audio picked for them
        expect(page.locator('[data-testid="make-submit"]')).to_be_enabled()

    def test_apple_first_on_iphone(self, browser, live_server, live_token):
        ctx = browser.new_context(user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15")
        page = ctx.new_page()
        page.add_init_script("window.__SING_SOCIAL_STUB = {google: 'g-tok', apple: 'a-tok'};")
        _open_wizard(page, live_server, live_token, account=SOCIAL_IDS)
        expect(page.locator(".mk-social").first).to_contain_text("Continue with Apple")
        ctx.close()

    def test_social_failure_falls_back_to_email(self, page, live_server, live_token):
        page.add_init_script("window.__SING_SOCIAL_STUB = {google: 'g-tok', apple: 'a-tok'};")
        page.route("**/sing/make/social*", lambda r: _json(r, {"error": "social_failed"}, 400))
        _open_wizard(page, live_server, live_token, account=SOCIAL_IDS)
        page.locator(".mk-social", has_text="Continue with Google").click()
        expect(page.locator('[data-testid="make-error"]')).to_contain_text("use your email")
        expect(page.locator('[data-testid="make-email"]')).to_be_visible()

    def test_send_failure_keeps_the_screen(self, page, live_server, live_token):
        _open_wizard(page, live_server, live_token, email="m@x.co")
        page.route("**/sing/submit*", lambda r: _json(r, {"error": "make_limit"}, 429))
        page.locator('[data-testid="make-submit"]').click()
        expect(page.locator('[data-testid="make-submit-error"]')).to_be_visible()
        expect(page.locator('[data-testid="make-step"]')).to_be_visible()
        expect(page.locator('[data-testid="make-submit"]')).to_be_enabled()
