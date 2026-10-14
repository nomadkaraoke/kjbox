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


def _slow_typed_search(page, live_server, live_token, typed, ident):
    """Route setup where the typed-text search is HELD (fulfilled later by the
    test, never by blocking Playwright's callback thread)."""
    held = []
    page.add_init_script("window.__SING_ARM_MS = 0;")
    _login(page, live_server, live_token)

    def body(q):
        return json.dumps({"songs": [STROKES] if q == "The Strokes Machu Picchu" else [],
                           "make_requests_enabled": True, "simple_mode": False})

    def on_search(route):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(route.request.url).query).get("q", [""])[0]
        if q == typed:
            held.append(lambda: route.fulfill(status=200, content_type="application/json", body=body(q)))
        else:
            route.fulfill(status=200, content_type="application/json", body=body(q))
    page.route("**/sing/search?*", on_search)
    page.route("**/sing/search/identify*", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps(ident)))
    page.route("**/sing/search/event*", lambda r: r.fulfill(status=200, content_type="application/json", body="{}"))
    page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
    return held


_IDENT = {"status": "confident", "kind": "content", "typed": "the stokes max picu",
          "song": {"artist": "The Strokes", "title": "Machu Picchu", "karaoke": True},
          "candidates": [{"artist": "Evaluna Montaner", "title": "Machu Picchu", "karaoke": False}]}


def _release(page, held):
    with page.expect_response(lambda r: "the%20stokes%20max%20picu" in r.url or "the+stokes+max+picu" in r.url):
        while held:
            held.pop()()
    expect(page.locator('[data-testid="search-searching"]')).to_have_count(0)


def test_song_card_shows_before_slow_karaoke_search(page, live_server, live_token):
    """Identification (~100 ms) must not wait for karaoke search (can take seconds)."""
    held = _slow_typed_search(page, live_server, live_token, "the stokes max picu", _IDENT)
    page.locator('input[type="search"]').fill("the stokes max picu")
    expect(page.locator('[data-testid="song-card-song"]')).to_have_text("Machu Picchu — The Strokes", timeout=5000)
    expect(page.locator(".result-row .r-title")).to_have_text("Machu Picchu", timeout=5000)
    page.wait_for_function("true")
    assert held, "typed-text search should still be pending"
    _release(page, held)
    expect(page.locator('[data-testid="song-card-song"]')).to_have_text("Machu Picchu — The Strokes")
    expect(page.locator(".result-row .r-title")).to_have_text("Machu Picchu")


def test_early_choice_survives_the_slow_typed_results(page, live_server, live_token):
    held = _slow_typed_search(page, live_server, live_token, "the stokes max picu", _IDENT)
    page.locator('input[type="search"]').fill("the stokes max picu")
    page.locator('[data-testid="song-not-it"]').click(timeout=5000)      # before the typed results land
    expect(page.locator('[data-testid="song-candidate"]')).to_have_text(["Machu Picchu — Evaluna Montaner"])
    _release(page, held)
    # The singer's "not it?" stands — the original identification is not re-applied.
    expect(page.locator('[data-testid="song-candidate"]')).to_have_text(["Machu Picchu — Evaluna Montaner"])
    expect(page.locator('[data-testid="song-card-song"]')).to_have_count(0)


# ── Song identified but no karaoke version: make-it must be findable ──────────

def _other(i):
    return {"key": f"g:o{i}", "artist": f"Other Artist {i}", "title": f"Brightside Song {i}", "version_count": 1,
            "in_library": False, "versions": [{"source": "local", "priority_class": "unknown",
            "local": {"path": f"/m/o{i}.mp4", "filename": f"o{i}.mp4", "disc_id": f"X-{i}"}}]}


OTHERS = [_other(i) for i in range(8)]
_SAME_MISSING = {"status": "confident", "kind": "same", "typed": "mr brightside the killers",
                 "song": {"artist": "The Killers", "title": "Mr. Brightside", "karaoke": False}, "candidates": []}


def _offer_events(calls, action):
    return [e["data"].get("where") for e in calls["events"] if e["action"] == action]


def test_missing_song_gets_top_make_card_and_near_misses_are_capped(page, live_server, live_token):
    calls = _setup(page, live_server, live_token, _SAME_MISSING, lambda q: OTHERS)
    page.locator('input[type="search"]').fill("mr brightside the killers")
    missing = page.locator('[data-testid="song-missing"]')
    expect(missing).to_contain_text("Mr. Brightside")
    expect(missing).to_contain_text("No karaoke version yet")
    # The top card comes BEFORE any result row.
    assert page.evaluate("""() => {
        const m = document.querySelector('[data-testid="song-missing"]');
        const r = document.querySelector('.result-row');
        return !!(m.compareDocumentPosition(r) & Node.DOCUMENT_POSITION_FOLLOWING);
    }""")
    expect(page.locator('[data-testid="similar-heading"]')).to_be_visible()
    expect(page.locator(".result-row")).to_have_count(3)
    page.locator('[data-testid="similar-more"]').click()
    expect(page.locator(".result-row")).to_have_count(8)
    # Confident identification → no sticky bar.
    expect(page.locator('[data-testid="make-bar"]')).to_have_count(0)
    page.locator('[data-testid="song-missing-make"]').click()
    expect(page.locator('[data-testid="song-missing"]')).to_have_count(0)   # left search for the make wizard
    page.wait_for_timeout(300)
    assert "top_card" in _offer_events(calls, "make_offer_shown")
    assert _offer_events(calls, "make_offer_tap") == ["top_card"]
    sub = [e for e in calls["events"] if e["action"] == "make_submit"]
    assert sub and sub[0]["data"]["artist"] == "The Killers" and sub[0]["data"]["title"] == "Mr. Brightside"


def test_identified_content_song_without_karaoke_shows_missing_card(page, live_server, live_token):
    """Corrected song's own karaoke search is empty → the typed-text rows are near-misses."""
    _setup(page, live_server, live_token, {
        "status": "confident", "kind": "content", "typed": "mr brihgtside",
        "song": {"artist": "The Killers", "title": "Mr. Brightside", "karaoke": False}, "candidates": [],
    }, lambda q: [] if q == "The Killers Mr. Brightside" else OTHERS[:2])
    page.locator('input[type="search"]').fill("mr brihgtside")
    expect(page.locator('[data-testid="song-missing"]')).to_contain_text("Mr. Brightside")
    expect(page.locator('[data-testid="similar-heading"]')).to_be_visible()
    expect(page.locator(".result-row")).to_have_count(2)
    expect(page.locator('[data-testid="similar-more"]')).to_have_count(0)


def test_same_song_found_shows_no_card_or_bar(page, live_server, live_token):
    _setup(page, live_server, live_token, {
        "status": "confident", "kind": "same", "typed": "machu picchu strokes",
        "song": {"artist": "The Strokes", "title": "Machu Picchu", "karaoke": True}, "candidates": [],
    }, lambda q: [STROKES] + OTHERS[:5])
    page.locator('input[type="search"]').fill("machu picchu strokes")
    expect(page.locator(".result-row")).to_have_count(6)
    expect(page.locator('[data-testid="search-correction"]')).to_have_count(0)
    expect(page.locator('[data-testid="song-missing"]')).to_have_count(0)
    expect(page.locator('[data-testid="similar-heading"]')).to_have_count(0)
    expect(page.locator('[data-testid="make-bar"]')).to_have_count(0)


def test_unidentified_search_shows_sticky_make_bar(page, live_server, live_token):
    calls = _setup(page, live_server, live_token, {"status": "none"}, lambda q: OTHERS)
    page.set_viewport_size({"width": 390, "height": 760})
    page.locator('input[type="search"]').fill("some obscure thing")
    bar = page.locator('[data-testid="make-bar"]')
    expect(bar).to_be_visible()
    expect(page.locator('[data-testid="song-missing"]')).to_have_count(0)
    # Fixed to the viewport bottom — reachable without scrolling a long list.
    box = bar.bounding_box()
    assert box and box["y"] + box["height"] <= 760 and box["y"] > 600, box
    page.locator('[data-testid="make-bar-btn"]').click()
    expect(page.locator('[data-testid="make-card"]')).to_be_visible()
    expect(page.locator('[data-testid="make-card"]')).to_be_in_viewport()
    expect(bar).to_have_count(0)
    page.wait_for_timeout(300)
    assert "sticky_bar" in _offer_events(calls, "make_offer_shown")
    assert _offer_events(calls, "make_offer_tap") == ["sticky_bar"]


def test_no_make_offers_when_make_requests_disabled(page, live_server, live_token):
    page.add_init_script("window.__SING_ARM_MS = 0;")
    _login(page, live_server, live_token)
    page.route("**/sing/search?*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(
        {"songs": OTHERS, "make_requests_enabled": False, "simple_mode": False})))
    page.route("**/sing/search/identify*", lambda r: r.fulfill(
        status=200, content_type="application/json", body=json.dumps(_SAME_MISSING)))
    page.route("**/sing/search/event*", lambda r: r.fulfill(status=200, content_type="application/json", body="{}"))
    page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
    page.locator('input[type="search"]').fill("mr brightside the killers")
    missing = page.locator('[data-testid="song-missing"]')
    expect(missing).to_contain_text("No karaoke version yet")
    expect(page.locator('[data-testid="song-missing-make"]')).to_have_count(0)
    expect(page.locator('[data-testid="make-bar"]')).to_have_count(0)


def test_index_says_karaoke_exists_never_claims_missing(page, live_server, live_token):
    """Rows may hold the song under a name sameSong() can't match ("Killers" vs
    "The Killers") — don't say "no karaoke version yet"; fall back to the bar."""
    _setup(page, live_server, live_token, dict(_SAME_MISSING, song={
        "artist": "The Killers", "title": "Mr. Brightside", "karaoke": True}), lambda q: OTHERS)
    page.locator('input[type="search"]').fill("mr brightside the killers")
    expect(page.locator('[data-testid="make-bar"]')).to_be_visible()
    expect(page.locator('[data-testid="song-missing"]')).to_have_count(0)
    expect(page.locator(".result-row")).to_have_count(8)


def test_corrected_search_with_only_near_misses_is_still_missing(page, live_server, live_token):
    """The corrected song's own search can return other songs — they're not
    "Karaoke versions of" it, and the make-it card must still lead."""
    _setup(page, live_server, live_token, {
        "status": "confident", "kind": "content", "typed": "mr brihgtside",
        "song": {"artist": "The Killers", "title": "Mr. Brightside", "karaoke": False}, "candidates": [],
    }, lambda q: OTHERS[:5] if q == "The Killers Mr. Brightside" else OTHERS[5:])
    page.locator('input[type="search"]').fill("mr brihgtside")
    expect(page.locator('[data-testid="song-missing"]')).to_contain_text("Mr. Brightside")
    expect(page.locator('[data-testid="karaoke-for-heading"]')).to_have_count(0)
    expect(page.locator('[data-testid="similar-heading"]')).to_be_visible()
    expect(page.locator(".result-row")).to_have_count(3)
    expect(page.locator('[data-testid="make-bar"]')).to_have_count(0)
