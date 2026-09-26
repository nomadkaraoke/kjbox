"""E2E: singer UI i18n runtime, event footer, change-mode banner, single-version
decision row, empty-state numbering (2026-09-23 walkthrough fixes)."""

import json

from playwright.sync_api import expect


def _login(page, live_server, live_token, name="Alice"):
    page.goto(f"{live_server}/sing/?t={live_token}")
    expect(page.locator("#sing-root")).to_be_visible()
    page.evaluate("(n) => localStorage.setItem('sing_name', n)", name)
    page.evaluate("localStorage.setItem('sing_phone', '')")
    page.evaluate("window.__sing_state.name = localStorage.getItem('sing_name') || ''")
    page.evaluate("window.__sing_state.phone = ''")


ES_STUB = {
    "search": {"title": "Elige tu canción", "placeholder": "Escribe artista o título…"},
    "tabs": {"request": "Pedir", "mySongs": "Mis canciones", "rotation": "Turnos", "tip": "Propina"},
    "lang": {"button": "🌐 {name}"},
}

AR_STUB = {"search": {"title": "اختر أغنيتك"}}


class TestLanguageSwitcher:
    def test_pill_opens_picker_and_switches_in_place(self, page, live_server, live_token):
        page.route("**/static/messages/es.json*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(ES_STUB)))
        _login(page, live_server, live_token)
        page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
        expect(page.locator("h2")).to_have_text("Pick your song")
        page.locator('[data-testid="lang-pill"]').first.click()
        expect(page.locator(".sing-lang-option[data-lang='es']")).to_be_visible()
        page.locator(".sing-lang-option[data-lang='es']").click()
        expect(page.locator("h2")).to_have_text("Elige tu canción")
        expect(page.locator('[data-testid="tab-mysongs"]')).to_contain_text("Mis canciones")
        # Untranslated keys fall back to English rather than showing raw keys.
        expect(page.locator(".sing-history-summary")).to_contain_text("Need ideas?")
        assert page.evaluate("document.documentElement.lang") == "es"
        assert page.evaluate("localStorage.getItem('sing_lang')") == "es"

    def test_saved_language_survives_reload_and_lang_param_wins(self, page, live_server, live_token):
        page.route("**/static/messages/es.json*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(ES_STUB)))
        _login(page, live_server, live_token)
        page.evaluate("localStorage.setItem('sing_lang', 'es')")
        page.reload()
        expect(page.locator("h2")).to_have_text("Elige tu canción")
        page.goto(f"{live_server}/sing/?t={live_token}&lang=en")
        expect(page.locator("h2")).to_have_text("Pick your song")
        assert page.evaluate("localStorage.getItem('sing_lang')") == "en"

    def test_rtl_locale_flips_document_direction(self, page, live_server, live_token):
        page.route("**/static/messages/ar.json*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(AR_STUB)))
        _login(page, live_server, live_token)
        page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
        page.evaluate("window.__sing_setLocale('ar')")
        expect(page.locator("h2")).to_have_text("اختر أغنيتك")
        assert page.evaluate("document.documentElement.dir") == "rtl"
        page.evaluate("window.__sing_setLocale('en')")
        assert page.evaluate("document.documentElement.dir") == "ltr"

    def test_missing_locale_file_falls_back_to_english(self, page, live_server, live_token):
        page.route("**/static/messages/fr.json*", lambda r: r.fulfill(status=404, body="nope"))
        _login(page, live_server, live_token)
        page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
        page.evaluate("window.__sing_setLocale('fr')")
        expect(page.locator("h2")).to_have_text("Pick your song")
        assert page.evaluate("window.__sing_getLocale()") == "en"

    def test_code_entry_page_is_translatable(self, page, live_server):
        page.route("**/static/messages/es.json*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"code": {"title": "Introduce tu código"}})))
        page.goto(f"{live_server}/sing/")
        page.evaluate("localStorage.setItem('sing_lang', 'es')")
        page.reload()
        expect(page.locator("#sing-enter-code h1")).to_have_text("Introduce tu código")
        expect(page.locator("#sing-enter-code [data-testid='lang-pill']")).to_be_visible()


class TestEventFooter:
    def test_notices_and_message_render_under_every_screen(self, page, live_server, live_token):
        page.route("**/sing/event-info*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"kj_name": "", "footer_message": "Kitchen closes at 11!",
                             "notices": ["chargers", "wifi", "bogus"]})))
        _login(page, live_server, live_token)
        page.reload()
        footer = page.locator("#sing-event-footer")
        expect(footer).to_be_visible()
        expect(footer.locator(".sing-notice")).to_have_count(2)
        expect(footer.locator(".sing-notice").first).to_contain_text("Phone chargers")
        expect(footer.locator('[data-testid="footer-message"]')).to_contain_text("Kitchen closes at 11!")
        page.locator('[data-testid="tab-rotation"]').click()
        expect(footer).to_be_visible()

    def test_footer_hidden_when_nothing_configured(self, page, live_server, live_token):
        page.route("**/sing/event-info*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"kj_name": "", "footer_message": "", "notices": []})))
        _login(page, live_server, live_token)
        page.reload()
        expect(page.locator("#sing-event-footer")).to_be_hidden()


class TestSearchDecisionLayer:
    def _search(self, page, live_server, live_token, songs, make_enabled=True):
        page.add_init_script("window.__SING_ARM_MS = 0;")
        _login(page, live_server, live_token)
        body = {"songs": songs, "make_requests_enabled": make_enabled, "simple_mode": False}
        page.route("**/sing/search*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)))
        page.evaluate("window.__sing_state.step = 'search'; window.__sing_render();")
        page.locator('input[type="search"]').fill("query text")

    def test_single_version_song_gets_preview_pills_and_brand(self, page, live_server, live_token):
        self._search(page, live_server, live_token, [{
            "key": "g:one", "artist": "Glow", "title": "Dancing Queen",
            "version_count": 1, "in_library": False,
            "versions": [{"source": "kn", "priority_class": "community",
                          "priority_brand": None, "priority_display": "",
                          "kn": {"brand_code": "WOBK", "is_community": True,
                                 "youtube_url": "https://youtu.be/x"}}],
        }])
        row = page.locator(".result-row").first
        expect(row.locator(".btn-primary-cta")).to_have_text("Request this song →")
        compact = row.locator(".sing-version-compact")
        expect(compact).to_be_visible()
        expect(compact.locator(".sing-pill-community")).to_have_text("Community")
        expect(compact.locator(".sing-pill-format")).to_have_text("YouTube")
        expect(compact.locator('[data-testid="version-preview"]')).to_be_visible()
        expect(compact.locator(".sing-version-pick")).to_have_count(0)
        # No versions toggle for a single version.
        expect(row.locator(".sing-versions-toggle")).to_have_count(0)

    def test_multi_version_song_says_best_version_auto_picked(self, page, live_server, live_token):
        self._search(page, live_server, live_token, [{
            "key": "g:two", "artist": "ABBA", "title": "Dancing Queen",
            "version_count": 2, "in_library": True,
            "versions": [
                {"source": "local", "priority_class": "community", "priority_brand": "NOMAD",
                 "priority_display": "Nomad Karaoke",
                 "local": {"path": "/m/a.mp4", "disc_id": "NOMAD-1", "format": "mp4",
                           "filename": "a.mp4", "artist": "ABBA", "title": "Dancing Queen"}},
                {"source": "kn", "priority_class": "commercial", "priority_brand": "KV",
                 "priority_display": "Karaoke Version",
                 "kn": {"brand_code": "KV", "is_community": False, "youtube_url": "https://youtu.be/y"}},
            ],
        }])
        row = page.locator(".result-row").first
        expect(row.locator(".btn-primary-cta")).to_have_text("Request this song →")
        expect(row.locator(".sing-auto-hint")).to_have_text("Best version picked automatically")
        expect(row.locator(".sing-versions-toggle")).to_have_text("Choose from 2 versions →")

    def test_unknown_brand_shows_library_file_not_disc_id_as_brand(self, page, live_server, live_token):
        self._search(page, live_server, live_token, [{
            "key": "g:lib", "artist": "X", "title": "Y", "version_count": 1, "in_library": True,
            "versions": [{"source": "local", "priority_class": "unknown", "priority_brand": None,
                          "priority_display": "",
                          "local": {"path": "/m/EEK-01507.zip", "disc_id": "EEK-01507",
                                    "format": "zip", "filename": "EEK-01507.zip",
                                    "artist": "X", "title": "Y"}}],
        }])
        expect(page.locator(".sing-version-brand").first).to_have_text("Library file EEK-01507")

    def test_empty_state_numbering_follows_visible_cards(self, page, live_server, live_token):
        self._search(page, live_server, live_token, [], make_enabled=False)
        wrap = page.locator(".sing-empty-triage")
        expect(wrap).to_be_visible()
        # Make requests off → only the YouTube card, and no "1." numbering.
        heads = wrap.locator(".sing-empty-card h4")
        expect(heads).to_have_count(1)
        expect(heads.nth(0)).to_have_text("Paste a YouTube link")

    def test_empty_state_leads_with_make_card(self, page, live_server, live_token):
        self._search(page, live_server, live_token, [], make_enabled=True)
        wrap = page.locator(".sing-empty-triage")
        expect(wrap.locator(".sing-empty-header p")).to_contain_text("2 ways forward")
        heads = wrap.locator(".sing-empty-card h4")
        expect(heads).to_have_count(2)
        expect(heads.nth(0)).to_contain_text("1. We'll make it for you")
        expect(heads.nth(1)).to_contain_text("2. Paste a YouTube link")
        # No external gen.nomadkaraoke.com hand-off any more.
        expect(page.locator('a[href*="gen.nomadkaraoke.com"]')).to_have_count(0)
        # Missing fields are flagged inline instead of silently ignored.
        wrap.locator('[data-testid="make-card"] button').click()
        expect(wrap.locator(".sing-empty-missing")).to_be_visible()
        card = wrap.locator('[data-testid="make-card"]')
        card.locator("input").nth(0).fill("Radiohead")
        card.locator("input").nth(1).fill("Creep")
        page.route("**/sing/make/account*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"ready": True, "email": None})))
        card.locator("button").click()
        # The make wizard starts by verifying the singer's email.
        expect(page.locator('[data-testid="make-step"] h2')).to_have_text("We'll make it for you")
        expect(page.locator('[data-testid="make-email"]')).to_be_visible()

    def test_make_offer_under_results(self, page, live_server, live_token):
        song = {"key": "g:one", "artist": "Glow", "title": "Dancing Queen",
                "version_count": 1, "in_library": False,
                "versions": [{"source": "kn", "priority_class": "community",
                              "priority_brand": None, "priority_display": "",
                              "kn": {"brand_code": "WOBK", "is_community": True,
                                     "youtube_url": "https://youtu.be/x"}}]}
        self._search(page, live_server, live_token, [song])
        offer = page.locator('[data-testid="make-offer"]')
        expect(offer).to_be_visible()
        offer.click()
        expect(page.locator('[data-testid="make-card"]')).to_be_visible()
        # Search text survives opening the form (results re-render in place).
        expect(page.locator('input[type="search"]')).to_have_value("query text")

    def test_no_make_offer_when_make_disabled(self, page, live_server, live_token):
        song = {"key": "g:one", "artist": "Glow", "title": "Dancing Queen",
                "version_count": 1, "in_library": False,
                "versions": [{"source": "kn", "priority_class": "community",
                              "priority_brand": None, "priority_display": "",
                              "kn": {"brand_code": "WOBK", "is_community": True,
                                     "youtube_url": "https://youtu.be/x"}}]}
        self._search(page, live_server, live_token, [song], make_enabled=False)
        expect(page.locator(".result-row")).to_have_count(1)
        expect(page.locator('[data-testid="make-offer"]')).to_have_count(0)


class TestChangeSongMode:
    def test_banner_confirm_copy_and_keep_song_exit(self, page, live_server, live_token):
        page.add_init_script("window.__SING_ARM_MS = 0;")
        _login(page, live_server, live_token)
        body = {"songs": [{
            "key": "g:one", "artist": "Oasis", "title": "Wonderwall", "version_count": 1,
            "in_library": True,
            "versions": [{"source": "local", "priority_class": "community", "priority_brand": "NOMAD",
                          "priority_display": "Nomad Karaoke",
                          "local": {"path": "/m/w.mp4", "disc_id": "NOMAD-2", "format": "mp4",
                                    "filename": "w.mp4", "artist": "Oasis", "title": "Wonderwall"}}],
        }]}
        page.route("**/sing/search*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)))
        page.evaluate("""
            window.__sing_state.changeRequestId = 42;
            window.__sing_state.changeEditToken = 'tok';
            window.__sing_state.changeSongLabel = 'Dancing Queen — Glow';
            window.__sing_state.step = 'search';
            window.__sing_render();
        """)
        banner = page.locator('[data-testid="change-banner"]')
        expect(banner).to_contain_text("Swapping out: Dancing Queen — Glow")
        page.locator('input[type="search"]').fill("wonderwall")
        page.locator(".btn-primary-cta").first.click()
        expect(page.locator("h2")).to_have_text("Swap to this song?")
        expect(page.locator('[data-testid="confirm-change-hint"]')).to_contain_text("replaces Dancing Queen — Glow")
        expect(page.locator(".submit-btn")).to_have_text("Yes — swap my song")
        # Partners are not offered on a swap (they belong to the original request).
        expect(page.locator('[data-testid="add-singer"]')).to_have_count(0)
        page.go_back()
        expect(banner).to_be_visible()
        page.locator('[data-testid="change-keep"]').click()
        expect(page.locator("h2")).to_have_text("Your songs tonight")
        assert page.evaluate("window.__sing_state.changeRequestId") is None


class TestMySongsCards:
    def _seed(self, page, live_server, live_token, items, now_singing=None):
        _login(page, live_server, live_token)
        page.evaluate("""(t) => localStorage.setItem('sing_my_request_ids', JSON.stringify(
            {token: t, ids: [1, 2], tokens: {'1': 'tok1', '2': 'tok2'}}))""", live_token)
        payload = {"now_playing": {"now_singing": now_singing, "up_next": None, "queued_count": 3},
                   "requests": items}
        page.route("**/sing/my-requests*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(payload)))
        page.evaluate("window.__sing_state.step = 'done'; window.__sing_render();")

    def test_now_singing_card_has_no_change_or_cancel_but_can_preview(self, page, live_server, live_token):
        self._seed(page, live_server, live_token, [{
            "request": {"id": 1, "singer_name": "Alice", "song_artist": "Q", "song_title": "B",
                        "source_type": "local", "status": "approved", "linked_entry_id": 7},
            "estimate": {"position": 1, "now_singing": True, "range_low_s": 0, "range_high_s": 0},
            "performed": False, "previewable": True,
        }], now_singing={"first_name": "Alice", "song_artist": "B - Q"})
        card = page.locator(".song-card").first
        expect(card).to_contain_text("You're up — head to the stage!")
        expect(card.locator('[data-testid="change-song"]')).to_have_count(0)
        expect(card.locator('[data-testid="cancel-song"]')).to_have_count(0)
        expect(card.locator('[data-testid="song-preview"]')).to_be_visible()

    def test_pending_change_says_what_it_replaces_and_next_after_this_song(self, page, live_server, live_token):
        self._seed(page, live_server, live_token, [
            {"request": {"id": 1, "singer_name": "Alice", "song_artist": "Glow",
                         "song_title": "Dancing Queen", "source_type": "kn", "status": "approved",
                         "linked_entry_id": 7},
             "estimate": {"position": 2, "now_singing": False, "range_low_s": 120, "range_high_s": 480},
             "performed": False},
            {"request": {"id": 2, "singer_name": "Alice", "song_artist": "Paramore",
                         "song_title": "Hallelujah", "source_type": "kj_pick", "status": "pending",
                         "linked_entry_id": None, "supersedes_request_id": 1},
             "performed": False},
        ], now_singing={"first_name": "Bob", "song_artist": "X - Y"})
        cards = page.locator(".song-card")
        expect(cards.nth(0)).to_contain_text("You're next — right after this song")
        expect(cards.nth(1).locator('[data-testid="replaces-line"]')).to_have_text(
            "Replaces Dancing Queen — Glow once the host confirms")
        # The tile carries its own 🎤 icon; the status line doesn't repeat it.
        expect(page.locator('[data-testid="mysongs-bar"] .status-tile-detail')).to_have_text(
            "You're next — after this song")


class TestRotationDuetsAndPreview:
    def test_duet_display_name_and_preview_button(self, page, live_server, live_token):
        _login(page, live_server, live_token)
        payload = {"entries": [
            {"position": 1, "first_name": "José", "display_name": "José & Maria",
             "song_artist": "Despacito - Luis Fonsi", "status": "Waiting", "now_singing": False,
             "expected_s": 60, "range_low_s": 0, "range_high_s": 120, "entry_id": 11, "previewable": True},
            {"position": 2, "first_name": "Kim", "display_name": "Kim",
             "song_artist": "Song - Artist", "status": "Waiting", "now_singing": False,
             "expected_s": 300, "range_low_s": 120, "range_high_s": 480, "entry_id": 12, "previewable": False},
        ], "spread_source": "fallback"}
        page.route("**/sing/rotation*", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps(payload)))
        page.evaluate("window.__sing_state.step = 'rotation'; window.__sing_render();")
        rows = page.locator(".rotation-row")
        expect(rows.nth(0).locator(".rotation-name")).to_have_text("José & Maria")
        expect(rows.nth(0).locator('[data-testid="rotation-preview"]')).to_be_visible()
        expect(rows.nth(1).locator('[data-testid="rotation-preview"]')).to_have_count(0)
        # The button must actually be tappable even next to a long title
        # (a clipped song span used to swallow the tap on real phones).
        page.route("**/sing/preview/resolve*", lambda r: r.fulfill(
            status=404, content_type="application/json",
            body=json.dumps({"mode": "unavailable", "reason": "Not ready to preview yet"})))
        page.route("**/sing/lib/*", lambda r: r.fulfill(
            status=200, content_type="application/javascript",
            body="window.openPreview = (d) => { window.__previewed = d; };"))
        rows.nth(0).locator('[data-testid="rotation-preview"]').click(timeout=3000)
        page.wait_for_function("window.__previewed && window.__previewed.entry_id === 11")
