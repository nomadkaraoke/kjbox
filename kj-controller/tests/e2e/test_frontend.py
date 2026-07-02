"""End-to-end Playwright tests for the KJ Controller frontend."""

import json

from playwright.sync_api import expect


# ---------------------------------------------------------------------------
# Page load & metadata
# ---------------------------------------------------------------------------

class TestPageLoad:
    """Basic page load, title, and static asset tests."""

    def test_page_loads_with_correct_title(self, app_page):
        expect(app_page).to_have_title("Nomad KJ Control")

    def test_css_loaded(self, app_page):
        """External stylesheet is linked and loaded."""
        # href carries a cache-bust query string (?v=<version>), so match the prefix.
        link = app_page.locator('link[rel="stylesheet"][href^="/static/style.css"]')
        expect(link).to_have_count(1)
        # Verify body has dark background from our CSS
        bg = app_page.evaluate("getComputedStyle(document.body).backgroundColor")
        assert bg in ("rgb(15, 15, 15)", "#0f0f0f"), f"Unexpected body background: {bg}"

    def test_js_loaded_and_initialized(self, app_page):
        """app.js loaded and DOMContentLoaded handler ran."""
        log_area = app_page.locator("#log-area")
        expect(log_area).to_contain_text("Nomad KJ Control initialized.")

    def test_favicon_links_present(self, app_page):
        """Favicon <link> tags point to static assets."""
        assert app_page.locator('link[href="/static/favicon.ico"]').count() == 1
        assert app_page.locator('link[href="/static/favicon-16x16.png"]').count() == 1
        assert app_page.locator('link[href="/static/favicon-32x32.png"]').count() == 1
        assert app_page.locator('link[href="/static/apple-touch-icon.png"]').count() == 1

    def test_static_css_served(self, page, live_server):
        """GET /static/style.css returns 200 with CSS content."""
        resp = page.request.get(f"{live_server}/static/style.css")
        assert resp.status == 200
        assert "text/css" in resp.headers.get("content-type", "")
        body = resp.text()
        assert "#ff5bb8" in body  # brand pink

    def test_static_js_served(self, page, live_server):
        """GET /static/app.js returns 200 with JS content."""
        resp = page.request.get(f"{live_server}/static/app.js")
        assert resp.status == 200
        body = resp.text()
        assert "DOMContentLoaded" in body

    def test_favicon_ico_served(self, page, live_server):
        """GET /static/favicon.ico returns 200."""
        resp = page.request.get(f"{live_server}/static/favicon.ico")
        assert resp.status == 200


# ---------------------------------------------------------------------------
# Layout structure
# ---------------------------------------------------------------------------

class TestLayoutStructure:
    """Verify the main layout containers are present and visible."""

    def test_two_column_layout(self, app_page):
        expect(app_page.locator("#col1")).to_be_visible()
        expect(app_page.locator("#col2")).to_be_visible()

    def test_status_bar(self, app_page):
        expect(app_page.locator("#status-bar")).to_be_visible()

    def test_log_area(self, app_page):
        expect(app_page.locator("#log-area")).to_be_visible()

    def test_now_playing_idle_initially(self, app_page):
        """With nothing playing, the now-playing block (inside Playback
        Controls) shows the idle placeholder and hides the detail row."""
        expect(app_page.locator("#np-title")).to_have_text("Nothing playing")
        expect(app_page.locator("#np-info-meta")).to_be_hidden()


# ---------------------------------------------------------------------------
# Download section
# ---------------------------------------------------------------------------

class TestDownloadSection:
    """Upload / Download card."""

    def test_download_heading(self, app_page):
        expect(app_page.locator("h2", has_text="Upload / Download")).to_be_visible()

    def test_url_input(self, app_page):
        url_input = app_page.locator("#youtube-url")
        expect(url_input).to_be_visible()
        expect(url_input).to_have_attribute("placeholder", "Enter YouTube URL")

    def test_download_button(self, app_page):
        btn = app_page.locator("#download-btn")
        expect(btn).to_be_visible()
        expect(btn).to_have_text("Download")

    def test_download_queue_empty_initially(self, app_page):
        queue = app_page.locator("#download-queue")
        expect(queue).to_be_empty()


# ---------------------------------------------------------------------------
# Playback controls
# ---------------------------------------------------------------------------

class TestPlaybackControls:
    """Playback Controls card."""

    def test_playback_heading(self, app_page):
        expect(app_page.locator("h2", has_text="Playback Controls")).to_be_visible()

    def test_pause_resume_button(self, app_page):
        btn = app_page.locator("#btn-pause")
        expect(btn).to_be_visible()
        # Default idle state shows "Pause / Resume"
        expect(btn).to_have_text("Pause / Resume")

    def test_restart_button_disabled_when_stopped(self, app_page):
        """Restart is disabled when nothing is playing."""
        btn = app_page.locator("#btn-restart")
        expect(btn).to_be_visible()
        expect(btn).to_be_disabled()

    def test_stop_button_disabled_when_stopped(self, app_page):
        """Stop is disabled when nothing is playing."""
        btn = app_page.locator("#btn-stop")
        expect(btn).to_be_visible()
        expect(btn).to_be_disabled()

    def test_seek_slider(self, app_page):
        slider = app_page.locator("#seek-slider")
        expect(slider).to_be_visible()
        expect(slider).to_have_attribute("type", "range")

    def test_karaoke_volume_slider(self, app_page):
        slider = app_page.locator("#karaoke-volume")
        expect(slider).to_be_visible()
        expect(slider).to_have_attribute("max", "256")

    def test_karaoke_volume_label(self, app_page):
        """Volume slider shows percentage label."""
        label = app_page.locator("#karaoke-volume-label")
        expect(label).to_be_visible()
        # Default value 200 = ~78%
        expect(label).to_have_text("78%")

    def test_filler_volume_slider(self, app_page):
        slider = app_page.locator("#filler-volume")
        expect(slider).to_be_visible()

    def test_filler_volume_label(self, app_page):
        """Filler volume slider shows percentage label."""
        label = app_page.locator("#filler-volume-label")
        expect(label).to_be_visible()
        # Default value 100 = ~39%
        expect(label).to_have_text("39%")



# ---------------------------------------------------------------------------
# Available Songs section
# ---------------------------------------------------------------------------

class TestAvailableSongs:
    """Available Songs card."""

    def test_available_songs_heading(self, app_page):
        expect(app_page.locator("h2", has_text="Available Songs")).to_be_visible()

    def test_rescan_button(self, app_page):
        btn = app_page.locator("#rescan-btn")
        expect(btn).to_be_visible()
        expect(btn).to_have_text("Rescan Media")

    def test_search_input(self, app_page):
        search = app_page.locator("#catalog-search")
        expect(search).to_be_visible()

    def test_search_clear_hidden_initially(self, app_page):
        expect(app_page.locator("#search-clear")).to_be_hidden()

    def test_media_list_shows_empty_message(self, app_page):
        media_list = app_page.locator("#media-list")
        expect(media_list).to_contain_text("No media files found")

    def test_folder_controls_hidden_when_no_folders(self, app_page):
        expect(app_page.locator("#folder-controls")).to_be_hidden()


# ---------------------------------------------------------------------------
# Status bar
# ---------------------------------------------------------------------------

class TestStatusBar:
    """Status bar at the bottom."""

    def test_shows_status_label(self, app_page):
        expect(app_page.locator("#status-bar")).to_contain_text("Status:")

    def test_shows_stopped_state(self, app_page):
        # The status poll should have run and set "stopped"
        expect(app_page.locator("#player-state")).to_have_text("stopped")

    def test_shows_filler_info(self, app_page):
        expect(app_page.locator("#status-bar")).to_contain_text("Filler:")

    def test_filler_selector_in_system(self, app_page):
        """Filler selector is in the System section."""
        system = app_page.locator(".system-controls")
        expect(system.locator("#filler-selector")).to_be_visible()

    def test_av_output_button_in_system(self, app_page):
        """AV Output button is in the System section."""
        system = app_page.locator(".system-controls")
        btn = system.locator("button", has_text="AV Output")
        expect(btn).to_be_visible()

    def test_audio_warning_hidden(self, app_page):
        """Audio warning should be hidden when no audio error."""
        warning = app_page.locator("#audio-warning")
        # display: none via CSS
        expect(warning).not_to_be_visible()


# ---------------------------------------------------------------------------
# Overlay modal
# ---------------------------------------------------------------------------

class TestOverlayModal:
    """Overlay form is in a modal dialog."""

    def test_overlay_modal_hidden_initially(self, app_page):
        expect(app_page.locator("#overlay-modal")).to_be_hidden()

    def test_add_button_opens_modal(self, app_page):
        app_page.locator("#overlay-add-btn").click()
        expect(app_page.locator("#overlay-modal")).to_be_visible()
        expect(app_page.locator("#overlay-modal-title")).to_have_text("Add Overlay")

    def test_cancel_closes_modal(self, app_page):
        app_page.locator("#overlay-add-btn").click()
        expect(app_page.locator("#overlay-modal")).to_be_visible()
        app_page.locator(".overlay-cancel-btn").click()
        expect(app_page.locator("#overlay-modal")).to_be_hidden()

    def test_escape_closes_modal(self, app_page):
        app_page.locator("#overlay-add-btn").click()
        expect(app_page.locator("#overlay-modal")).to_be_visible()
        app_page.keyboard.press("Escape")
        expect(app_page.locator("#overlay-modal")).to_be_hidden()

    def test_close_button_closes_modal(self, app_page):
        app_page.locator("#overlay-add-btn").click()
        expect(app_page.locator("#overlay-modal")).to_be_visible()
        app_page.locator("#overlay-modal .modal-close").click()
        expect(app_page.locator("#overlay-modal")).to_be_hidden()


# ---------------------------------------------------------------------------
# Interactions
# ---------------------------------------------------------------------------

class TestInteractions:
    """Test basic user interactions work correctly."""

    def test_search_shows_clear_button(self, app_page):
        """Typing in search shows the clear button."""
        search = app_page.locator("#catalog-search")
        search.fill("test query")
        # Wait for debounce (300ms) + processing
        app_page.wait_for_timeout(500)
        expect(app_page.locator("#search-clear")).to_be_visible()

    def test_search_clear_resets(self, app_page):
        """Clicking clear resets search state."""
        search = app_page.locator("#catalog-search")
        search.fill("test query")
        app_page.wait_for_timeout(500)
        app_page.locator("#search-clear").click()
        expect(search).to_have_value("")
        expect(app_page.locator("#search-clear")).to_be_hidden()

    def test_escape_clears_search(self, app_page):
        """Pressing Escape in search field clears it."""
        search = app_page.locator("#catalog-search")
        search.fill("something")
        app_page.wait_for_timeout(500)
        search.press("Escape")
        expect(search).to_have_value("")

    def test_download_empty_url_logs_error(self, app_page):
        """Clicking download with empty URL shows error in log."""
        app_page.locator("#download-btn").click()
        expect(app_page.locator("#log-area")).to_contain_text("Please enter a YouTube URL")

    def test_slash_focuses_search(self, app_page):
        """Pressing '/' focuses the search input."""
        # Click body first to ensure no input is focused
        app_page.locator("h2", has_text="Playback").click()
        app_page.keyboard.press("/")
        focused_id = app_page.evaluate("document.activeElement.id")
        assert focused_id == "catalog-search"


# ---------------------------------------------------------------------------
# Responsive layout
# ---------------------------------------------------------------------------

class TestResponsiveLayout:
    """Test responsive breakpoints."""

    def test_mobile_layout_single_column(self, page, live_server):
        """At mobile width, layout switches to single column with reordered sections."""
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(live_server)
        page.wait_for_load_state("networkidle")

        # In mobile, main-layout should be flex column (not grid)
        display = page.evaluate(
            "getComputedStyle(document.querySelector('.main-layout')).display"
        )
        assert display == "flex"

        # Columns use display:contents so sections are reordered
        col1_display = page.evaluate(
            "getComputedStyle(document.querySelector('#col1')).display"
        )
        assert col1_display == "contents"

    def test_mobile_section_order(self, page, live_server):
        """On mobile, song library comes before overlays and system."""
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(live_server)
        page.wait_for_load_state("networkidle")

        # Available songs should have order 2, system should have order 6
        songs_order = page.evaluate(
            "getComputedStyle(document.querySelector('.available-songs')).order"
        )
        system_order = page.evaluate(
            "getComputedStyle(document.querySelector('.system-controls')).order"
        )
        assert int(songs_order) < int(system_order)

    def test_desktop_layout_grid(self, page, live_server):
        """At desktop width, layout uses CSS grid."""
        page.set_viewport_size({"width": 1400, "height": 900})
        page.goto(live_server)
        page.wait_for_load_state("networkidle")

        display = page.evaluate(
            "getComputedStyle(document.querySelector('.main-layout')).display"
        )
        assert display == "grid"


# ---------------------------------------------------------------------------
# Brand / visual identity
# ---------------------------------------------------------------------------

class TestBrandIdentity:
    """Verify Nomad brand colors are applied."""

    def test_heading_color(self, app_page):
        """h2 headings use the brand pink."""
        color = app_page.evaluate(
            "getComputedStyle(document.querySelector('h2')).color"
        )
        # #ff7acc = rgb(255, 122, 204)
        assert color == "rgb(255, 122, 204)"

    def test_button_background_color(self, app_page):
        """Primary action buttons use dark outlined style with pink border."""
        bg = app_page.evaluate(
            "getComputedStyle(document.getElementById('download-btn')).backgroundColor"
        )
        # #222 = rgb(34, 34, 34) — outlined style base
        assert bg == "rgb(34, 34, 34)"

    def test_dark_theme_background(self, app_page):
        """Cards use the dark card background."""
        bg = app_page.evaluate(
            "getComputedStyle(document.querySelector('.container')).backgroundColor"
        )
        # #1a1a1a = rgb(26, 26, 26)
        assert bg == "rgb(26, 26, 26)"


# ---------------------------------------------------------------------------
# Download completion → media refresh
# ---------------------------------------------------------------------------

class TestDownloadCompletionRefresh:
    """Download completion refreshes media data even when search is active."""

    def test_download_complete_shows_new_track(self, page, live_server):
        """Completed download refreshes media list and track appears."""
        test_media = [
            {
                "file_path": "downloads/Test Song - Artist.mp4",
                "display_name": "Test Song - Artist",
                "filename": "Test Song - Artist.mp4",
                "folder_name": "downloads",
                "folder": "/tmp/downloads",
                "is_download": True,
                "mtime": 9999999999,
                "size": 50000000,
            }
        ]

        page.goto(live_server)
        page.wait_for_load_state("networkidle")

        # Intercept /media to return our test track
        page.route(
            "**/media",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(test_media),
            ),
        )

        # Directly call handleDownloadQueue with a completed download
        page.evaluate(
            """async () => {
                await handleDownloadQueue([{id: 'test1', status: 'completed', title: 'Test Song - Artist', url: 'http://test', error: null}]);
            }"""
        )

        # Verify the success log message
        expect(page.locator("#log-area")).to_contain_text(
            'Downloaded "Test Song - Artist" successfully!'
        )

        # Verify the track appears in the media list
        expect(page.locator("#media-list")).to_contain_text("Test Song - Artist")

    def test_download_complete_during_search_refreshes_data(self, page, live_server):
        """When download completes during active search, localMediaItems is still refreshed."""
        test_media = [
            {
                "file_path": "downloads/Bohemian Rhapsody - Queen.mp4",
                "display_name": "Bohemian Rhapsody - Queen",
                "filename": "Bohemian Rhapsody - Queen.mp4",
                "folder_name": "downloads",
                "folder": "/tmp/downloads",
                "is_download": True,
                "mtime": 9999999999,
                "size": 50000000,
            }
        ]

        page.goto(live_server)
        page.wait_for_load_state("networkidle")

        # Activate search so searchActive = true
        page.locator("#catalog-search").fill("bohemian")
        page.wait_for_timeout(500)  # debounce

        # Intercept /media to return the downloaded track
        page.route(
            "**/media",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(test_media),
            ),
        )

        # Simulate download completion while search is active
        page.evaluate(
            """async () => {
                await handleDownloadQueue([{id: 'test2', status: 'completed', title: 'Bohemian Rhapsody - Queen', url: 'http://test', error: null}]);
            }"""
        )

        # Verify localMediaItems was updated despite searchActive being true
        item_count = page.evaluate("localMediaItems.length")
        assert item_count == 1, f"Expected localMediaItems to have 1 item, got {item_count}"

        display_name = page.evaluate("localMediaItems[0].display_name")
        assert display_name == "Bohemian Rhapsody - Queen"
