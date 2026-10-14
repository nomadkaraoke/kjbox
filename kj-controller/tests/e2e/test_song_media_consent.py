"""E2E: per-song media consent + Instagram handle (2026-10-14).

Singer: a 📷 toggle on each of their own song cards, the "just one song or
nothing from tonight?" question when flipping yes → no after singing, and the
optional Instagram handle. KJ: the 📷 marker menu's per-song override.
"""

import json

from playwright.sync_api import expect

from tests.e2e.test_footer_social_photo_consent import _entry, _event_info, _login, _to_confirm

_NOW = {"now_singing": None, "up_next": None, "queued_count": 0}


def _req(rid, status="approved", media_consent=None, title="Wonderwall"):
    return {"id": rid, "singer_name": "Alice", "song_artist": "Oasis", "song_title": title,
            "source_type": "local", "status": status, "created_at": "now",
            "linked_entry_id": rid + 100, "additional_singers": None,
            "media_consent": media_consent}


def _done_with_songs(page, live_server, live_token, items, consent="yes", friends=None):
    _event_info(page, ask_photo_consent=True)
    # Serve `items` live so a saved override survives the next poll.
    page.route("**/sing/my-requests*", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"now_playing": _NOW, "requests": items})))
    _login(page, live_server, live_token, consent=consent)
    page.reload()
    page.wait_for_function("!!window.__sing_state")
    ids = [it["request"]["id"] for it in items]
    page.evaluate("""([t, ids, friends]) => localStorage.setItem('sing_my_request_ids', JSON.stringify(
        {token: t, ids, tokens: Object.fromEntries(ids.map(i => [String(i), 'tok' + i])),
         friends}))""", [live_token, ids, friends or {}])
    page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")
    # Sung songs sit in a collapsed <details>.
    if any(it.get("performed") for it in items):
        page.locator(".sung-section summary").click()


class TestSongCardConsent:
    def test_toggle_posts_override_for_that_song(self, page, live_server, live_token):
        posted = []

        items = [{"request": _req(1), "performed": True}]

        def on_song(route):
            body = json.loads(route.request.post_data)
            posted.append(body)
            items[0]["request"]["media_consent"] = body["consent"]
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True, "request": items[0]["request"]}))
        page.route("**/sing/song-consent*", on_song)
        _done_with_songs(page, live_server, live_token, items)
        btn = page.locator('[data-testid="song-consent"]')
        expect(btn).to_have_attribute("data-consent", "yes")
        expect(btn).to_contain_text("OK to post")
        btn.click()
        page.wait_for_function("document.querySelector('[data-testid=\"song-consent\"]')"
                               ".dataset.consent === 'no'")
        assert posted[0]["consent"] == "no"
        assert posted[0]["item"] == {"id": 1, "edit_token": "tok1"}

    def test_matching_night_choice_clears_override(self, page, live_server, live_token):
        posted = []
        items = [{"request": _req(1, media_consent="no"), "performed": True}]

        def on_song(route):
            body = json.loads(route.request.post_data)
            posted.append(body)
            items[0]["request"]["media_consent"] = body["consent"]
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True, "request": items[0]["request"]}))
        page.route("**/sing/song-consent*", on_song)
        _done_with_songs(page, live_server, live_token, items)
        page.locator('[data-testid="song-consent"]').click()
        page.wait_for_function("document.querySelector('[data-testid=\"song-consent\"]')"
                               ".dataset.consent === 'yes'")
        assert posted[0]["consent"] is None

    def test_no_toggle_on_friend_song_or_when_not_asked(self, page, live_server, live_token):
        _done_with_songs(page, live_server, live_token,
                         [{"request": _req(1), "performed": True}], friends={"1": "Bob"})
        expect(page.locator(".song-card-title")).to_be_visible()
        expect(page.locator('[data-testid="song-consent"]')).to_have_count(0)


class TestScopeQuestion:
    def test_yes_to_no_after_singing_asks_scope(self, page, live_server, live_token):
        posts = []
        page.route("**/sing/photo-consent*", lambda r: (posts.append(json.loads(r.request.post_data)),
            r.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"success": True, "updated": 1})))[1])
        _done_with_songs(page, live_server, live_token,
                         [{"request": _req(1), "performed": True}])
        page.locator('[data-testid="pref-toggle-photo"]').click()
        picker = page.locator('#push-optin [data-testid="photo-consent"]')
        picker.locator('[data-testid="photo-consent-no"]').click()
        scope = page.locator('[data-testid="consent-scope"]')
        expect(scope).to_be_visible()
        assert posts == []
        # "Just one song" keeps the night choice and points at the song toggles.
        page.locator(".sung-section summary").click()   # collapse; the choice re-opens it
        page.locator('[data-testid="consent-scope-one"]').click()
        expect(picker.locator(".photo-consent-saved")).to_contain_text("song")
        expect(page.locator('[data-testid="song-consent"]')).to_be_visible()
        assert posts == []
        assert page.evaluate("localStorage.getItem('sing_photo_consent')") == "yes"
        # Asking again and choosing "nothing from tonight" saves the night-wide no.
        picker.locator('[data-testid="photo-consent-no"]').click()
        page.locator('[data-testid="consent-scope-all"]').click()
        expect(picker.locator(".photo-consent-saved")).to_contain_text("Saved")
        assert [p["consent"] for p in posts] == ["no"]

    def test_no_question_before_singing(self, page, live_server, live_token):
        posts = []
        page.route("**/sing/photo-consent*", lambda r: (posts.append(1),
            r.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"success": True, "updated": 1})))[1])
        _done_with_songs(page, live_server, live_token,
                         [{"request": _req(1), "performed": False}])
        page.locator('[data-testid="pref-toggle-photo"]').click()
        page.locator('#push-optin [data-testid="photo-consent-no"]').click()
        expect(page.locator('#push-optin .photo-consent-saved')).to_contain_text("Saved")
        expect(page.locator('[data-testid="consent-scope"]')).to_have_count(0)
        assert posts == [1]


class TestInstagramHandle:
    def test_handle_offered_after_yes_and_sent_with_request(self, page, live_server, live_token):
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
        handle = page.locator('[data-testid="photo-consent-instagram"]')
        expect(handle).to_be_hidden()
        page.locator('[data-testid="photo-consent-yes"]').click()
        expect(handle).to_be_visible()
        handle.fill("@Alice.Sings")
        handle.dispatch_event("change")
        assert page.evaluate("localStorage.getItem('sing_instagram')") == "alice.sings"
        page.locator(".submit-btn").click()
        page.wait_for_function("window.__sing_state.step === 'done'")
        assert sent["photo_consent"] == "yes" and sent["instagram"] == "alice.sings"

    def test_invalid_handle_shows_error(self, page, live_server, live_token):
        _event_info(page, ask_photo_consent=True)
        _login(page, live_server, live_token)
        page.reload()
        _to_confirm(page)
        page.locator('[data-testid="photo-consent-yes"]').click()
        handle = page.locator('[data-testid="photo-consent-instagram"]')
        handle.fill("not a handle!")
        handle.dispatch_event("change")
        expect(page.locator(".photo-consent-handle-error")).to_be_visible()
        assert not page.evaluate("localStorage.getItem('sing_instagram')")


class TestKjSongOverride:
    def test_menu_sets_song_override_and_badge(self, app_page):
        page = app_page
        posted = []
        page.route("**/rotation/entry/media-consent", lambda r: (
            posted.append(json.loads(r.request.post_data)),
            r.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"success": True})))[1])
        e = _entry(5, "Mija", {"Mija": "yes"})
        e2 = dict(_entry(6, "Katie", {"Katie": "yes"}), song_consent={"Katie": "no"})
        page.evaluate("(entries) => renderRotation(entries)", [e, e2])
        rows = page.locator("#rotation-list .rotation-entry")
        marker = rows.nth(1).locator(".rotation-photo-consent")
        expect(marker).to_have_attribute("data-consent", "no")
        assert "photo-consent-song" in marker.get_attribute("class")
        assert "THIS song" in marker.get_attribute("title")
        rows.nth(0).locator(".rotation-photo-consent").click()
        page.locator(".photo-consent-menu button", has_text="post THIS song").click()
        marker.click()
        page.locator(".photo-consent-menu button", has_text="Clear song override").click()
        for _ in range(50):
            if len(posted) >= 2:
                break
            page.wait_for_timeout(50)
        assert posted == [{"entry_id": 5, "name": "Mija", "consent": "no"},
                          {"entry_id": 6, "name": "Katie", "consent": None}]
