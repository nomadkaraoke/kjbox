"""E2E: compact singer search results, audio-type pills, other-artists group,
sort (2026-10-29).

A singer browsing an artist ("lana del rey" → 83 songs) got ~290px per song and
no way to narrow the list. Rows are now title / artist · N versions / 🎵 Original
or 🎸 Cover band · brand, with Request over ▶ Preview; tapping a row opens the
version detail. Long lists get single-choice All / Original / Cover band pills
and a Best match / A–Z sort; for an artist search, songs by other artists sit
in a collapsed group at the end.
"""

import json

from playwright.sync_api import expect


def _login(page, live_server, live_token, name="Alice"):
    page.goto(f"{live_server}/sing/?t={live_token}")
    expect(page.locator("#sing-root")).to_be_visible()
    page.evaluate("(n) => localStorage.setItem('sing_name', n)", name)
    page.evaluate("localStorage.setItem('sing_phone', '')")
    page.evaluate("window.__sing_state.name = localStorage.getItem('sing_name') || ''")
    page.evaluate("window.__sing_state.phone = ''")


def _local(cls, brand, title, artist="Lana Del Rey"):
    return {"source": "local", "priority_class": cls, "priority_display": brand,
            "local": {"path": f"/m/{brand}-{title}.zip", "disc_id": f"{brand}-1", "format": "zip",
                      "filename": f"{brand}-{title}.zip", "artist": artist, "title": title}}


def _kn_community(brand="WOBK"):
    return {"source": "kn", "priority_class": "community", "priority_display": "",
            "kn": {"brand_code": brand, "is_community": True, "youtube_url": f"https://youtu.be/{brand}"}}


def _song(title, artist="Lana Del Rey", versions=None):
    versions = versions or [_local("commercial", "SF", title, artist)]
    return {"key": f"{artist.lower()}|||{title.lower()}", "artist": artist, "title": title,
            "version_count": len(versions), "in_library": True, "versions": versions}


# An artist search: mostly Lana (incl. featured credits), some community, and
# the unrelated near-name tail the catalogue returns.
LANA = [
    _song("Video Games", versions=[_local("commercial", "KV", "Video Games"), _kn_community("CARPY")]),
    _song("Radio", versions=[_kn_community("LEMMY")]),
    _song("Love", versions=[_local("commercial", "KV", "Love"), _local("commercial", "SF", "Love")]),
    _song("Ride (Explicit)", artist="Lana Del Rey (Wbgv)"),
    _song("Tough", artist="Quavo & Lana Del Rey", versions=[_kn_community("Q")]),
    _song("Cola"),
    _song("ILYSB", artist="LANY"),
    _song("Promised Land", artist="Skeletal Family"),
]


class TestCompactResults:
    def _search(self, page, live_server, live_token, songs, query="lana del rey", rows=None):
        page.add_init_script("window.__SING_ARM_MS = 0;")
        _login(page, live_server, live_token)
        body = {"songs": songs, "make_requests_enabled": True, "simple_mode": False}
        page.route("**/sing/search?*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)))
        # Request sends straight away: keep it off the live server.
        page.route("**/sing/submit*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps({
                "request": {"id": 9, "status": "pending", "edit_token": "t9", "singer_name": "Alice",
                            "song_title": "x"}, "auto_approved": False})))
        page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
        page.locator('input[type="search"]').fill(query)
        expect(page.locator(".result-row")).to_have_count(len(songs) if rows is None else rows)

    def _titles(self, page):
        return page.locator(".result-row .r-title").all_inner_texts()

    def test_closed_row_shows_audio_type_brand_and_version_count(self, page, live_server, live_token):
        self._search(page, live_server, live_token, LANA, rows=6)
        row = page.locator(".result-row", has_text="Video Games")
        expect(row.locator(".r-sub")).to_contain_text("Lana Del Rey")
        expect(row.locator(".sing-song-count")).to_have_text("2 versions")
        expect(row.locator('[data-testid="song-audio-type"]')).to_have_text("🎸 Cover band")
        expect(row.locator(".sing-song-brand")).to_have_text("KV")
        expect(row.locator('[data-testid="song-preview"]')).to_have_text("▶ Preview")
        expect(row.locator('[data-testid="request-song"]')).to_have_text("Request")
        expect(row.locator(".sing-version-card")).to_have_count(0)
        radio = page.locator(".result-row", has_text="Radio")
        expect(radio.locator('[data-testid="song-audio-type"]')).to_have_text("🎵 Original")
        expect(radio.locator(".sing-song-count")).to_have_count(0)   # single version: no count

    def test_tap_opens_and_closes_the_version_detail(self, page, live_server, live_token):
        self._search(page, live_server, live_token, LANA, rows=6)
        row = page.locator(".result-row", has_text="Video Games")
        toggle = row.locator('[data-testid="song-row-toggle"]')
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        expect(row.locator('[data-testid="song-format"]')).to_be_visible()
        expect(row.locator(".sing-versions-toggle")).to_have_text("Choose from 2 versions →")
        toggle.click()
        expect(row.locator('[data-testid="song-format"]')).to_have_count(0)
        expect(row.locator(".sing-versions-toggle")).to_have_count(0)

    def test_short_list_opens_straight_onto_versions(self, page, live_server, live_token):
        self._search(page, live_server, live_token, LANA[:1], query="video games")
        expect(page.locator('[data-testid="filter-bar"]')).to_have_count(0)
        expect(page.locator('.result-row [data-testid="song-format"]')).to_be_visible()
        expect(page.locator(".result-row .sing-versions-toggle")).to_be_visible()

    def test_audio_pills_are_single_choice(self, page, live_server, live_token):
        self._search(page, live_server, live_token, LANA, rows=6)
        all_, orig, cover = (page.locator(f'[data-testid="filter-{v}"]') for v in ("all", "community", "commercial"))
        expect(all_).to_have_attribute("aria-checked", "true")
        expect(all_).to_contain_text("8")
        expect(orig).to_contain_text("3")
        expect(cover).to_contain_text("6")
        orig.click()
        expect(orig).to_have_attribute("aria-checked", "true")
        expect(all_).to_have_attribute("aria-checked", "false")
        assert self._titles(page) == ["Video Games", "Radio", "Tough"]
        cover.click()
        expect(orig).to_have_attribute("aria-checked", "false")
        assert self._titles(page) == ["Video Games", "Love", "Ride (Explicit)", "Cola"]
        all_.click()
        expect(page.locator(".result-row")).to_have_count(6)

    def test_artist_search_groups_other_artists_at_the_end(self, page, live_server, live_token):
        self._search(page, live_server, live_token, LANA, rows=6)
        # Featured / tagged credits stay with Lana; LANY and Skeletal Family don't.
        assert "Tough" in self._titles(page) and "Ride (Explicit)" in self._titles(page)
        toggle = page.locator('[data-testid="others-toggle"]')
        expect(toggle).to_have_text("▸ 2 songs by other artists")
        toggle.click()
        expect(page.locator(".result-row")).to_have_count(8)
        assert self._titles(page)[-2:] == ["ILYSB", "Promised Land"]
        toggle.click()
        expect(page.locator(".result-row")).to_have_count(6)

    def test_song_search_does_not_group_by_artist(self, page, live_server, live_token):
        songs = [_song("Video Games"), _song("Video Games", artist="Tenacious D"),
                 _song("Video Games (Explicit)", artist="Tenacious D"),
                 _song("Video Games (-1)", artist="Tenacious D"),
                 _song("Video Games", artist="Bite Me Bambi"), _song("The Girl at the Video Games", artist="X")]
        self._search(page, live_server, live_token, songs, query="video games")
        expect(page.locator('[data-testid="others-toggle"]')).to_have_count(0)

    def test_sort_a_to_z(self, page, live_server, live_token):
        self._search(page, live_server, live_token, LANA, rows=6)
        page.locator('[data-testid="sort-select"]').select_option("az")
        assert self._titles(page) == ["Cola", "Love", "Radio", "Ride (Explicit)", "Tough", "Video Games"]

    def test_original_filter_requests_the_original_version(self, page, live_server, live_token):
        # Overall best is a cover band version; with Original on, the singer gets the original one.
        self._search(page, live_server, live_token, LANA, rows=6)
        page.locator('[data-testid="filter-community"]').click()
        row = page.locator(".result-row", has_text="Video Games")
        expect(row.locator('[data-testid="song-audio-type"]')).to_have_text("🎵 Original")
        expect(row.locator(".sing-song-brand")).to_have_text("CARPY")
        row.locator('[data-testid="request-song"]').click()
        sel = page.evaluate("window.__sing_state.selected")
        assert sel["source_type"] == "kn"
        assert sel["source_ref"] == "https://youtu.be/CARPY"

    def test_without_filter_request_is_the_kj_pick(self, page, live_server, live_token):
        self._search(page, live_server, live_token, LANA, rows=6)
        page.locator(".result-row", has_text="Video Games").locator('[data-testid="request-song"]').click()
        assert page.evaluate("window.__sing_state.selected.source_type") == "kj_pick"

    def test_filter_binds_request_even_when_best_already_matches(self, page, live_server, live_token):
        # Best version is already the Original one; with the filter on, Request must still
        # bind it (not kj_pick), so approval can't swap in the cover band version.
        songs = list(LANA)
        songs[0] = _song("Video Games", versions=[_kn_community("CARPY"), _local("commercial", "KV", "Video Games")])
        self._search(page, live_server, live_token, songs, rows=6)
        page.locator('[data-testid="filter-community"]').click()
        page.locator(".result-row", has_text="Video Games").locator('[data-testid="request-song"]').click()
        sel = page.evaluate("window.__sing_state.selected")
        assert sel["source_type"] == "kn" and sel["source_ref"] == "https://youtu.be/CARPY"

    def test_and_credit_counts_as_the_artist(self, page, live_server, live_token):
        songs = LANA + [_song("Duet Song", artist="Lana Del Rey and The Weeknd"),
                        _song("Feature Song", artist="Quavo (feat. Lana Del Rey)")]
        self._search(page, live_server, live_token, songs, rows=8)
        assert "Duet Song" in self._titles(page) and "Feature Song" in self._titles(page)

    def test_filter_matching_only_other_artists_shows_them(self, page, live_server, live_token):
        songs = [_song(t) for t in ("Love", "Ride", "Cola", "Yayo", "Carmen")] + [
            _song("ILYSB", artist="LANY", versions=[_kn_community("LANY")])]
        self._search(page, live_server, live_token, songs, rows=5)
        page.locator('[data-testid="filter-community"]').click()
        assert self._titles(page) == ["ILYSB"]
        expect(page.locator('[data-testid="others-toggle"]')).to_have_count(0)

    def _retype(self, page, query, rows):
        page.locator('input[type="search"]').fill(query)
        expect(page.locator(".result-row")).to_have_count(rows)

    def test_filter_is_not_applied_to_a_short_list_without_pills(self, page, live_server, live_token):
        self._search(page, live_server, live_token, LANA, rows=6)
        page.locator('[data-testid="filter-community"]').click()
        # Next search: 2 songs → no pills, so Request is the normal kj_pick again.
        short = [_song("Video Games", versions=[_local("commercial", "KV", "Video Games"), _kn_community("CARPY")]),
                 _song("Love")]
        page.unroute("**/sing/search?*")
        page.route("**/sing/search?*", lambda r: r.fulfill(status=200, content_type="application/json",
                   body=json.dumps({"songs": short, "make_requests_enabled": True, "simple_mode": False})))
        self._retype(page, "video games lana", 2)
        expect(page.locator('[data-testid="filter-bar"]')).to_have_count(0)
        page.locator(".result-row", has_text="Video Games").locator('[data-testid="request-song"]').click()
        assert page.evaluate("window.__sing_state.selected.source_type") == "kj_pick"

    def test_kept_filter_with_no_matches_falls_back_to_all(self, page, live_server, live_token):
        self._search(page, live_server, live_token, LANA, rows=6)
        page.locator('[data-testid="filter-community"]').click()
        covers = [_song(t, artist="Sunfly Band") for t in ("A", "B", "C", "D", "E", "F")]
        page.unroute("**/sing/search?*")
        page.route("**/sing/search?*", lambda r: r.fulfill(status=200, content_type="application/json",
                   body=json.dumps({"songs": covers, "make_requests_enabled": True, "simple_mode": False})))
        self._retype(page, "sunfly band", 6)
        expect(page.locator('[data-testid="filter-all"]')).to_have_attribute("aria-checked", "true")
        expect(page.locator('[data-testid="filter-community"]')).to_have_count(0)
