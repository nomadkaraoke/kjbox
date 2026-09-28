"""Singer search: song identification (question 1) kept separate from karaoke
results (question 2) — docs/SONG-IDENTIFICATION.md §5 — and the choice log."""
import json
import urllib.parse

from playwright.sync_api import expect

from tests.e2e.test_sing_i18n_footer import _login

STROKES = {"key": "g:mp", "artist": "The Strokes", "title": "Machu Picchu", "version_count": 1,
           "in_library": True, "versions": [{"source": "local", "priority_class": "unknown",
           "local": {"path": "/m/x.mp4", "filename": "x.mp4", "disc_id": "TOOL-017"}}]}


def _setup(page, live_server, live_token, identify, songs_for):
    """Route search (per query), identify and the event log; returns the logged events."""
    page.add_init_script("window.__SING_ARM_MS = 0;")
    _login(page, live_server, live_token)
    calls = {"events": [], "resolve": 0, "identify_sids": []}

    def on_search(route):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(route.request.url).query).get("q", [""])[0]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"songs": songs_for(q), "make_requests_enabled": True, "simple_mode": False}))

    def on_identify(route):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(route.request.url).query)
        calls["identify_sids"].append(qs.get("sid", [""])[0])
        route.fulfill(status=200, content_type="application/json", body=json.dumps(identify))

    def on_event(route):
        calls["events"].append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body='{"ok": true}')

    def on_resolve(route):
        calls["resolve"] += 1
        route.fulfill(status=200, content_type="application/json", body="{}")

    page.route("**/sing/search?*", on_search)
    page.route("**/sing/search/identify*", on_identify)
    page.route("**/sing/search/event*", on_event)
    page.route("**/sing/search/resolve*", on_resolve)
    page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
    return calls


def test_identified_song_card_and_its_karaoke_rows(page, live_server, live_token):
    calls = _setup(page, live_server, live_token, {
        "status": "confident", "kind": "content", "typed": "the stokes max picu",
        "song": {"artist": "The Strokes", "title": "Machu Picchu", "karaoke": True},
        "candidates": [{"artist": "Evaluna Montaner", "title": "Machu Picchu", "karaoke": False}],
    }, lambda q: [STROKES] if q == "The Strokes Machu Picchu" else [])
    page.locator('input[type="search"]').fill("the stokes max picu")
    expect(page.locator('[data-testid="song-card-song"]')).to_have_text("Machu Picchu — The Strokes")
    expect(page.locator('[data-testid="karaoke-for-heading"]')).to_have_text("Karaoke versions of Machu Picchu")
    expect(page.locator(".result-row .r-title")).to_have_text("Machu Picchu")
    assert calls["resolve"] == 0          # on-device answer: no Gemini call
    # "not it?" → the other candidates, as a separate "Which song do you mean?" list.
    page.locator('[data-testid="song-not-it"]').click()
    expect(page.locator('[data-testid="search-didyoumean"]')).to_contain_text("Which song do you mean?")
    expect(page.locator('[data-testid="song-candidate"]')).to_have_text(["Machu Picchu — Evaluna Montaner"])
    actions = [e["action"] for e in calls["events"]]
    assert actions == ["not_it"]
    assert calls["events"][0]["sid"] in calls["identify_sids"]


def test_which_one_pick_prefills_make_form_and_is_logged(page, live_server, live_token):
    calls = _setup(page, live_server, live_token, {
        "status": "candidates", "typed": "hallelujah",
        "candidates": [{"artist": "Jeff Buckley", "title": "Hallelujah", "karaoke": True},
                       {"artist": "Leonard Cohen", "title": "Hallelujah", "karaoke": True}],
    }, lambda q: [])
    page.locator('input[type="search"]').fill("hallelujah")
    which = page.locator('[data-testid="search-didyoumean"]')
    expect(which).to_contain_text("Which song do you mean?")
    page.locator('[data-testid="song-candidate"]').nth(1).click()
    expect(page.locator('[data-testid="song-card-song"]')).to_have_text("Hallelujah — Leonard Cohen")
    card = page.locator('[data-testid="make-card"]')
    expect(card.locator("input").nth(0)).to_have_value("Leonard Cohen")
    expect(card.locator("input").nth(1)).to_have_value("Hallelujah")
    page.wait_for_function("true")
    ev = [e for e in calls["events"] if e["action"] == "pick_candidate"]
    assert ev and ev[0]["data"]["picked"] == "Leonard Cohen — Hallelujah" and ev[0]["data"]["index"] == 1


def test_no_card_when_results_already_show_the_song(page, live_server, live_token):
    _setup(page, live_server, live_token, {
        "status": "confident", "kind": "completed", "typed": "machu picchu",
        "song": {"artist": "The Strokes", "title": "Machu Picchu", "karaoke": True}, "candidates": [],
    }, lambda q: [STROKES])
    page.locator('input[type="search"]').fill("machu picchu")
    expect(page.locator(".result-row .r-title")).to_have_text("Machu Picchu")
    expect(page.locator('[data-testid="search-correction"]')).to_have_count(0)
    expect(page.locator('[data-testid="karaoke-for-heading"]')).to_have_count(0)


def test_request_is_logged_with_identification(page, live_server, live_token):
    calls = _setup(page, live_server, live_token, {
        "status": "confident", "kind": "content", "typed": "the stokes max picu",
        "song": {"artist": "The Strokes", "title": "Machu Picchu", "karaoke": True}, "candidates": [],
    }, lambda q: [STROKES] if q == "The Strokes Machu Picchu" else [])
    page.locator('input[type="search"]').fill("the stokes max picu")
    page.locator(".result-row .btn-primary-cta").click()
    page.wait_for_timeout(300)
    ev = [e for e in calls["events"] if e["action"] == "request_song"]
    assert ev, calls["events"]
    assert ev[0]["data"]["identified"] == "The Strokes — Machu Picchu"
    assert ev[0]["data"]["identified_kind"] == "content" and ev[0]["data"]["identified_active"] is True


def test_describe_goes_to_gemini_fallback(page, live_server, live_token):
    calls = _setup(page, live_server, live_token, {"status": "none"}, lambda q: [])
    page.locator('input[type="search"]').fill("that song from titanic")
    page.locator('[data-testid="describe-open"]').click()
    page.locator('[data-testid="describe-input"]').fill("the song from titanic")
    page.locator('[data-testid="describe-submit"]').click()
    page.wait_for_timeout(300)
    assert calls["resolve"] >= 2       # once for the empty search, once for the description
    assert [e["action"] for e in calls["events"]] == ["describe_open", "describe_submit"]
