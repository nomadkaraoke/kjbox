"""KJ rotation Gen modal: the guided karaoke-gen flow beside Add (kj_make.py).

gen is mocked at kjbox's /rotation/gen/* routes (page.route), so this drives the
real KJ UI + the shared gen-ported ranking (/sing/static/audio_rank.js).
"""

import json

from playwright.sync_api import expect

LOSSLESS = {"index": 0, "provider": "RED", "title": "This Is the Life", "artist": "Amy Macdonald",
            "is_lossless": True, "seeders": 120, "release_type": "Album", "year": 2007,
            "target_file": "03 - This Is the Life.flac"}
SPOTIFY = {"index": 1, "provider": "Spotify", "title": "This Is the Life", "artist": "Amy Macdonald",
           "is_lossless": False}
YOUTUBE = {"index": 2, "provider": "YouTube", "title": "Amy Macdonald - This Is The Life",
           "channel": "Amy Macdonald", "is_lossless": False, "view_count": 2500000}


def _json(route, body, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def _mock_gen(page, *, resolve=None, fast=None, full=None, results=None, create_status=200,
              busy_until_replace=False):
    calls = {"resolve": [], "check": [], "search": [], "create": [], "validate": []}

    def on_resolve(route):
        calls["resolve"].append(route.request.url)
        _json(route, resolve or {"artist": "Amy Macdonald", "title": "This Is the Life", "source": "gen"})

    def on_check(route):
        body = json.loads(route.request.post_data)
        calls["check"].append(body)
        _json(route, (full if body.get("stage") == "full" else fast) or {"kind": "none", "confident": False})

    def on_search(route):
        calls["search"].append(json.loads(route.request.post_data))
        _json(route, {"search_session_id": "ss-1",
                      "results": results if results is not None else [LOSSLESS, SPOTIFY, YOUTUBE]})

    def on_create(route):
        body = json.loads(route.request.post_data)
        calls["create"].append(body)
        if busy_until_replace and not body.get("replace"):
            _json(route, {"error": "already_generating", "gen_status": "processing"}, 409)
            return
        if create_status != 200:
            _json(route, {"error": "gen_unavailable"}, create_status)
            return
        entry = {"id": 7, "singer": ", ".join(body.get("singers") or ["Carol"]),
                 "song_artist": f"{body['title']} - {body['artist']}", "status": "Being Made (!)",
                 "position": 1, "songs_sung": 0, "wait_minutes": 0, "gen_job_id": "job-1",
                 "gen_status": "processing", "file_path": None}
        _json(route, {"success": True, "job_id": "job-1", "entry_id": 7, "entries": [entry]})

    def on_validate(route):
        calls["validate"].append(json.loads(route.request.post_data))
        _json(route, {"supported": True})

    page.route("**/rotation/gen/resolve*", on_resolve)
    page.route("**/rotation/gen/check", on_check)
    page.route("**/rotation/gen/search", on_search)
    page.route("**/rotation/gen/create", on_create)
    page.route("**/rotation/gen/validate-url", on_validate)
    return calls


def _open_add_form(page, singer="Andrew", song="amy macdonald this is the life"):
    page.evaluate("renderRotation([])")
    page.locator(".rotation-add-btn").click()
    page.locator("#rotation-singer").fill(singer)
    page.locator("#rotation-song").fill(song)


class TestKjGenModal:
    def test_prefill_search_pick_creates_being_made_entry(self, app_page):
        page = app_page
        calls = _mock_gen(page)
        _open_add_form(page)
        page.locator("#rotation-gen-btn").click()

        modal = page.locator("#gen-modal")
        expect(modal).to_be_visible()
        expect(page.locator("#gen-singer")).to_have_value("Andrew")
        # gen's resolver pre-fills artist/title, then the search runs by itself.
        expect(page.locator("#gen-artist")).to_have_value("Amy Macdonald")
        expect(page.locator("#gen-title")).to_have_value("This Is the Life")
        pick = page.locator('[data-testid="gen-pick"]')
        expect(modal).to_contain_text("Perfect match found")
        expect(pick).to_contain_text("03 - This Is the Life.flac")
        expect(pick).to_contain_text("High avail.")
        expect(pick).to_contain_text("[Album / 2007]")
        assert calls["search"] == [{"artist": "Amy Macdonald", "title": "This Is the Life"}]

        page.locator('[data-testid="gen-others-toggle"]').click()
        expect(page.locator('#gen-body [data-testid="gen-result"]')).to_have_count(3)

        pick.locator("button").click()
        expect(modal).to_be_hidden()
        assert calls["create"] == [{"artist": "Amy Macdonald", "title": "This Is the Life",
                                    "search_session_id": "ss-1", "selection_index": 0,
                                    "singers": ["Andrew"]}]
        row = page.locator("#rotation-list .rotation-entry").first
        expect(row).to_contain_text("This Is the Life - Amy Macdonald")
        expect(row.locator(".prep-making")).to_have_text("MAKING")
        # The add form is cleared for the next singer.
        expect(page.locator("#rotation-song")).to_have_value("")

    def test_correction_with_undo(self, app_page):
        page = app_page
        _mock_gen(page, resolve={"artist": "amy mcdonald", "title": "run", "source": "split"},
                  fast={"kind": "cosmetic", "confident": True, "needs_ai": False, "engine": "catalog",
                        "canonical_artist": "Amy Macdonald", "canonical_title": "Run"})
        _open_add_form(page, song="amy mcdonald run")
        page.locator("#rotation-gen-btn").click()
        notice = page.locator('[data-testid="gen-correction"]')
        expect(notice).to_contain_text("Corrected to Run — Amy Macdonald")
        expect(page.locator("#gen-artist")).to_have_value("Amy Macdonald")
        notice.locator("button").click()
        expect(page.locator("#gen-artist")).to_have_value("amy mcdonald")

    def test_did_you_mean_re_searches(self, app_page):
        page = app_page
        calls = _mock_gen(page, resolve={"artist": "x", "title": "creep", "source": "split"},
                          fast={"kind": "none", "confident": False, "needs_ai": True},
                          full={"kind": "ambiguous", "confident": False, "alternatives": [
                              {"artist": "Radiohead", "title": "Creep"}, {"artist": "TLC", "title": "Creep"}]})
        _open_add_form(page, song="creep")
        page.locator("#rotation-gen-btn").click()
        dym = page.locator('[data-testid="gen-didyoumean"]')
        expect(dym).to_contain_text("Creep — TLC")
        dym.get_by_role("button", name="Creep — TLC").click()
        expect(page.locator("#gen-artist")).to_have_value("TLC")
        page.wait_for_function("() => document.querySelectorAll('#gen-body [data-testid=\"gen-result\"]').length > 0")
        assert calls["search"][-1] == {"artist": "TLC", "title": "Creep"}

    def test_youtube_link_fallback_and_gen_error(self, app_page):
        page = app_page
        calls = _mock_gen(page, results=[], create_status=502)
        _open_add_form(page)
        page.locator("#rotation-gen-btn").click()
        expect(page.locator("#gen-body")).to_contain_text("No audio found")
        page.locator("#gen-yt-url").fill("https://youtu.be/abc")
        page.get_by_role("button", name="Use link").click()
        expect(page.locator('[data-testid="gen-error"]')).to_contain_text("Couldn't reach Nomad Gen")
        expect(page.locator("#gen-modal")).to_be_visible()     # stays open to retry
        assert calls["validate"] == [{"url": "https://youtu.be/abc"}]
        assert calls["create"][0]["youtube_url"] == "https://youtu.be/abc"

    def test_needs_singer(self, app_page):
        page = app_page
        calls = _mock_gen(page)
        _open_add_form(page, singer="")
        page.locator("#rotation-gen-btn").click()
        page.locator('[data-testid="gen-pick"] button').click()
        expect(page.locator('[data-testid="gen-error"]')).to_contain_text("singer")
        assert calls["create"] == []

    def test_link_mode_generates_for_existing_entry(self, app_page):
        page = app_page
        calls = _mock_gen(page)
        carol = {"id": 4, "singer": "Carol", "song_artist": "amy macdonald this is the life",
                 "status": "Waiting", "position": 1, "songs_sung": 0, "wait_minutes": 0, "file_path": None}
        page.route("**/rotation", lambda r: _json(r, {"entries": [carol]}))
        page.route("**/rotation/search*", lambda r: _json(r, {}))
        page.evaluate("(e) => { rotationData = [e]; renderRotation(rotationData); }", carol)
        page.evaluate("openLinkSearch(4, 'amy macdonald this is the life')")
        expect(page.locator("#rotation-gen-btn")).to_be_visible()
        page.locator("#rotation-gen-btn").click()
        expect(page.locator("#gen-target")).to_contain_text("Carol")
        expect(page.locator("#gen-singer")).to_have_count(0)
        page.locator('[data-testid="gen-pick"] button').click()
        expect(page.locator("#gen-modal")).to_be_hidden()
        assert calls["create"][0]["id"] == 4 and "singers" not in calls["create"][0]
        expect(page.locator("#rotation-add-form")).to_be_hidden()

    def test_search_dropdown_make_row_opens_modal(self, app_page):
        page = app_page
        _mock_gen(page)
        # Nothing found is when Gen matters most — the MAKE row must still be there.
        page.route("**/rotation/search*", lambda r: _json(r, {}))
        _open_add_form(page)
        page.locator("#rotation-search-btn").click()
        make_row = page.locator(".rotation-search-result", has_text="Generate with Nomad Gen")
        make_row.click()
        expect(page.locator("#gen-modal")).to_be_visible()
        expect(page.locator("#gen-title")).to_have_value("This Is the Life")

    def test_stuck_job_is_replaced_after_confirm(self, app_page):
        page = app_page
        calls = _mock_gen(page, busy_until_replace=True)
        page.on("dialog", lambda d: d.accept())
        _open_add_form(page)
        page.locator("#rotation-gen-btn").click()
        page.locator('[data-testid="gen-pick"] button').click()
        expect(page.locator("#gen-modal")).to_be_hidden()
        assert [c.get("replace") for c in calls["create"]] == [None, True]
