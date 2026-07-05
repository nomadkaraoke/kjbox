"""End-to-end Playwright tests for the KJ Controller frontend."""

import json
import re
import urllib.request

import pytest
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

    def test_now_playing_block(self, app_page):
        expect(app_page.locator("#np-info")).to_be_visible()

    def test_log_area(self, app_page):
        expect(app_page.locator("#log-area")).to_be_visible()

    def test_now_playing_idle_initially(self, app_page):
        """With nothing playing, the now-playing block (inside Playback
        Controls) shows the idle placeholder + a STOPPED pill, and hides the
        detail row. No filler track is configured in the test env, so the
        filler indicator row is hidden too."""
        expect(app_page.locator("#np-title")).to_have_text("Nothing playing")
        expect(app_page.locator("#np-state")).to_have_text("Stopped")
        expect(app_page.locator("#np-info-meta")).to_be_hidden()
        expect(app_page.locator("#np-filler-row")).to_be_hidden()


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
# Library section
# ---------------------------------------------------------------------------

class TestAvailableSongs:
    """Library (formerly "Available Songs") card."""

    def test_available_songs_heading(self, app_page):
        expect(app_page.locator("h2", has_text="Library")).to_be_visible()

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
# Library row structure — play/preview/edit/delete buttons unified with the
# rotation row (no Copy button, colorised format pill, click-to-copy name,
# two-click delete confirm).
# ---------------------------------------------------------------------------

class TestLibraryRow:
    """Structure and styling of a rendered Library (local media) row."""

    _ITEM = (
        "() => {"
        "  const items = [{ display_name: 'Seal - Soul Medley', media_kind: 'mp4',"
        "    ext: '.mp4', file_path: '/d/seal.mp4', media_id: 'm1', is_download: true,"
        "    folder_name: 'Downloads' }];"
        "  window.localMediaItems = items; window.searchActive = false;"
        "  renderFolderView(items);"
        "}"
    )

    def _row(self, app_page):
        app_page.evaluate(self._ITEM)
        return app_page.locator("#media-list li.media-file-row").first

    def test_row_has_preview_play_edit_delete_buttons(self, app_page):
        row = self._row(app_page)
        expect(row.locator(".media-btn-preview")).to_be_visible()
        expect(row.locator(".media-btn-play")).to_be_visible()
        expect(row.locator(".media-btn-edit")).to_be_visible()
        expect(row.locator(".media-btn-delete")).to_be_visible()

    def test_row_has_no_copy_button(self, app_page):
        # Copy button was removed — clicking the name copies instead.
        row = self._row(app_page)
        expect(row.locator(".copy-btn")).to_have_count(0)

    def test_name_is_click_to_copy(self, app_page):
        row = self._row(app_page)
        name = row.locator(".media-name")
        expect(name).to_have_text("Seal - Soul Medley")
        expect(name).to_have_class(re.compile(r"rotation-copyable"))

    def test_format_pill_is_colorised_badge(self, app_page):
        # The "mp4 · .mp4" media-type-badge is replaced by the catalog-style
        # colorised .format-badge.
        row = self._row(app_page)
        badge = row.locator(".format-badge")
        expect(badge).to_have_text("mp4")
        expect(row.locator(".media-type-badge")).to_have_count(0)

    def test_delete_arms_on_first_click(self, app_page):
        row = self._row(app_page)
        del_btn = row.locator(".media-btn-delete")
        del_btn.click()
        expect(del_btn).to_have_class(re.compile(r"media-btn-armed"))
        expect(del_btn).to_contain_text("Confirm")

    def test_buttons_match_rotation_font_size(self, app_page):
        # Drift guard: Library action buttons must render at the same font size
        # as the rotation row's buttons.
        self._row(app_page)
        sizes = app_page.evaluate(
            "() => {"
            "  for (let i = 1; i < 100000; i++) clearInterval(i);"
            "  window.rotationData = [{ id: 1, position: 1, singer: 'X',"
            "    song_artist: 'A - B', status: 'Up Next', songs_sung: 1,"
            "    file_path: '/d/x.mp4' }];"
            "  renderRotation(window.rotationData);"
            "  const rb = document.querySelector('#rotation-list .rotation-btn-play');"
            "  const lb = document.querySelector('#media-list li.media-file-row .media-btn-play');"
            "  const f = el => getComputedStyle(el).fontSize;"
            "  return { rot: f(rb), lib: f(lb) };"
            "}"
        )
        assert sizes["lib"] == sizes["rot"]


# ---------------------------------------------------------------------------
# Library default view — empty until searched; "*" surfaces newest files.
# ---------------------------------------------------------------------------

class TestLibraryDefaultView:
    """The Library holds thousands of files, so it stays empty (just a hint)
    until the KJ searches. Typing "*" surfaces the most-recent files."""

    # localMediaItems / searchActive / mediaReviewOnly are top-level `let`
    # bindings (not window properties), so assign to the bare names.
    _SEED = (
        "(n) => {"
        "  const items = [];"
        "  for (let i = 0; i < n; i++) items.push({"
        "    display_name: 'Song ' + i, media_kind: 'mp4', ext: '.mp4',"
        "    file_path: '/d/s' + i + '.mp4', media_id: 'm' + i,"
        "    folder_name: 'Downloads', is_download: true, mtime: 1000 - i });"
        "  localMediaItems = items; searchActive = false;"
        "  mediaReviewOnly = false;"
        "}"
    )

    def test_empty_by_default_when_search_blank(self, app_page):
        # A populated library with a blank search box shows NO file rows.
        app_page.evaluate(self._SEED, 5)
        app_page.evaluate(
            "() => { document.getElementById('catalog-search').value = '';"
            " renderLibraryDefault(); }"
        )
        expect(app_page.locator("#media-list li.media-file-row")).to_have_count(0)
        expect(app_page.locator("#media-list")).to_contain_text("Type to search")
        # Header count still reflects the whole library.
        expect(app_page.locator("#media-count")).to_have_text("(5)")

    def test_star_shows_ten_newest(self, app_page):
        app_page.evaluate(self._SEED, 15)
        app_page.evaluate(
            "() => { document.getElementById('catalog-search').value = '*';"
            " renderNewestLibraryItems(); }"
        )
        rows = app_page.locator("#media-list li.media-file-row")
        expect(rows).to_have_count(10)
        # Newest-first: Song 0 is present, Song 14 (oldest) is not.
        expect(app_page.locator("#media-list")).to_contain_text("Song 0")
        expect(app_page.locator("#media-list")).not_to_contain_text("Song 14")
        expect(app_page.locator("#search-meta")).to_contain_text("newest")

    def test_star_via_search_input_shows_newest(self, app_page):
        # Driving it through the real input path (catalogSearch) must also work
        # and must NOT flip into catalog-search mode.
        app_page.evaluate(self._SEED, 12)
        search = app_page.locator("#catalog-search")
        search.fill("*")
        app_page.wait_for_timeout(500)  # debounce
        expect(app_page.locator("#media-list li.media-file-row")).to_have_count(10)
        assert app_page.evaluate("searchActive") is False

    def test_placeholder_has_no_press_slash_hint(self, app_page):
        # The confusing "(press /)" hint was removed from the placeholder.
        placeholder = app_page.locator("#catalog-search").get_attribute("placeholder")
        assert "press /" not in placeholder.lower(), placeholder


# ---------------------------------------------------------------------------
# Now-playing status + filler indicator
# ---------------------------------------------------------------------------

class TestNowPlayingStatus:
    """The now-playing block inside Playback Controls surfaces player state as
    a pill and the filler track (idle state only). The old bottom status bar
    ("Status: ... | Filler: ...") was removed in favour of this."""

    def test_shows_stopped_pill(self, app_page):
        # The status poll should have run and set the idle STOPPED pill.
        expect(app_page.locator("#np-state")).to_have_text("Stopped")

    def test_filler_indicator_driven(self, app_page):
        """Drive updateNowPlaying() directly to verify the filler indicator:
        visible with the track name when stopped, hidden during playback."""
        # Stop the 2s status poll so it doesn't overwrite our driven state.
        app_page.evaluate("for (let i = 1; i < 100000; i++) clearInterval(i);")

        # Stopped + a filler track set -> filler row shows the track name.
        app_page.evaluate(
            "updateNowPlaying({state: 'stopped', current_filler_track: 'wii.mp3'})"
        )
        expect(app_page.locator("#np-state")).to_have_text("Stopped")
        expect(app_page.locator("#np-filler-row")).to_be_visible()
        expect(app_page.locator("#np-filler-track")).to_have_text("wii.mp3")

        # Playing -> filler row hidden even though a track is still configured.
        app_page.evaluate(
            "updateNowPlaying({state: 'playing', current_playing: 'Artist - Song', "
            "current_filler_track: 'wii.mp3'})"
        )
        expect(app_page.locator("#np-state")).to_have_text("Playing")
        expect(app_page.locator("#np-filler-row")).to_be_hidden()

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
# Fade Out controls — preset durations + reliable availability
# ---------------------------------------------------------------------------

class TestFadeControls:
    """Fade Out offers preset durations (3/6/10/20s) + a custom length, and the
    controls gate on whether a song is LOADED (current_playing_path) rather than the
    live player state. The live state flickers to 'stopped' on the VLC renderer, which
    used to grey out Fade Out mid-song. See
    docs/archive/2026-07-02-fade-out-durations-plan.md.
    """

    def _stop_poll(self, page):
        # Stop the 2s status poll so it doesn't overwrite our driven state.
        page.evaluate("for (let i = 1; i < 100000; i++) clearInterval(i);")

    def test_presets_present(self, app_page):
        expect(app_page.locator(".fade-preset")).to_have_count(4)
        expect(app_page.locator('.fade-preset[data-seconds="3"]')).to_have_text("3s")
        expect(app_page.locator('.fade-preset[data-seconds="20"]')).to_have_text("20s")

    def test_fade_controls_stay_one_flex_row(self, app_page):
        """Regression: .fade-controls is a plain <div> and must be excluded from the
        Playback Controls' Seek/Volume grid rule — otherwise it's forced into a 2-col
        label+slider grid that drops the custom field to a second line. At the desktop
        breakpoint (default e2e viewport is 1280px, >=769px) it must stay a flex row."""
        display = app_page.evaluate(
            "getComputedStyle(document.querySelector('.fade-controls')).display")
        assert display == "flex", f"expected flex, got {display!r} (grid rule leaked in)"
        # Presets and the custom group sit on the same visual line (heights differ
        # slightly; assert their vertical offset is small, i.e. not wrapped).
        offset = app_page.evaluate(
            "(() => { const p = document.querySelector('.fade-presets').getBoundingClientRect();"
            " const c = document.querySelector('.fade-custom').getBoundingClientRect();"
            " return Math.abs(c.top - p.top); })()")
        assert offset < 20, f"fade custom group wrapped to a second line (offset {offset}px)"

    def test_fade_enabled_when_loaded_even_if_state_stopped(self, app_page):
        """Core fix: a transient 'stopped' must NOT disable Fade Out while a song is
        loaded — the exact 'only sometimes clickable' symptom on the VLC renderer."""
        self._stop_poll(app_page)
        app_page.evaluate(
            "currentPlayingPath = '/songs/x.mp4'; updatePlaybackButtons('stopped');"
        )
        for secs in (3, 6, 10, 20):
            expect(app_page.locator(f'.fade-preset[data-seconds="{secs}"]')).to_be_enabled()
        expect(app_page.locator("#fade-custom-go")).to_be_enabled()
        expect(app_page.locator("#btn-restart")).to_be_enabled()
        expect(app_page.locator("#btn-stop")).to_be_enabled()

    def test_fade_disabled_when_no_song_loaded(self, app_page):
        self._stop_poll(app_page)
        app_page.evaluate(
            "currentPlayingPath = null; updatePlaybackButtons('stopped');"
        )
        expect(app_page.locator('.fade-preset[data-seconds="10"]')).to_be_disabled()
        expect(app_page.locator("#fade-custom-go")).to_be_disabled()
        expect(app_page.locator("#btn-stop")).to_be_disabled()

    def test_preset_posts_chosen_duration(self, app_page):
        self._stop_poll(app_page)
        captured = app_page.evaluate(
            """
            (() => {
                window.__call = null;
                apiCall = (url, body) => { window.__call = {url, body}; return Promise.resolve({}); };
                currentPlayingPath = '/songs/x.mp4';
                updatePlaybackButtons('playing');
                fadeOut(20);
                return window.__call;
            })()
            """
        )
        assert captured["url"] == "/control"
        assert captured["body"]["action"] == "fadeout"
        assert captured["body"]["duration_s"] == 20

    def test_custom_field_posts_clamped_duration(self, app_page):
        self._stop_poll(app_page)
        captured = app_page.evaluate(
            """
            (() => {
                window.__call = null;
                apiCall = (url, body) => { window.__call = {url, body}; return Promise.resolve({}); };
                currentPlayingPath = '/songs/x.mp4';
                document.getElementById('fade-custom-seconds').value = '99';
                fadeOutCustom();
                return window.__call;
            })()
            """
        )
        # Client clamps to the backend's 60s ceiling before posting.
        assert captured["body"]["duration_s"] == 60


# ---------------------------------------------------------------------------
# Mode toggle placement + Simple-mode layout
# ---------------------------------------------------------------------------

class TestModeTogglePlacement:
    """The Simple/Advanced toggle and the now-playing status block now live in
    the Playback Controls header/body, not the System section."""

    def test_toggle_in_playback_header(self, app_page):
        """Mode toggle sits in the Playback Controls header-row."""
        expect(
            app_page.locator(".playback-controls .header-row #mode-segmented")
        ).to_have_count(1)

    def test_toggle_not_in_system(self, app_page):
        """Mode toggle no longer lives in the System section."""
        expect(app_page.locator(".system-controls #mode-segmented")).to_have_count(0)

    def test_now_playing_in_playback_controls(self, app_page):
        """Now-playing status block lives in Playback Controls, not System."""
        expect(app_page.locator(".playback-controls #np-info")).to_have_count(1)
        expect(app_page.locator(".system-controls #np-info")).to_have_count(0)


class TestSimpleModeLayout:
    """Simple mode hides System entirely and puts Playback Controls at the same
    width as Rotation, letting Screen Preview rise to the top-right corner."""

    @pytest.fixture(autouse=True)
    def _restore_advanced(self, live_server):
        """live_server is session-scoped, so simple mode set here would leak
        into later advanced-mode tests. Restore advanced mode after each test."""
        yield
        req = urllib.request.Request(
            f"{live_server}/rotation/requests/config",
            data=json.dumps({"simple_mode": False}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            resp.read()

    def _enter_simple(self, page, live_server):
        # Desktop width so the >=769px simple-mode grid applies (not the mobile
        # stack). The toggle round-trips through the real backend, so simple
        # mode sticks across the 2s status poll.
        page.set_viewport_size({"width": 1400, "height": 900})
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        page.locator("#mode-seg-simple").click()
        page.wait_for_function(
            "document.body.classList.contains('simple-mode')"
        )

    def test_system_hidden_in_simple(self, page, live_server):
        self._enter_simple(page, live_server)
        expect(page.locator(".system-controls")).not_to_be_visible()

    def test_playback_matches_rotation_width(self, page, live_server):
        self._enter_simple(page, live_server)
        widths = page.evaluate(
            "() => {"
            " const pc = document.querySelector('.playback-controls').getBoundingClientRect();"
            " const r = document.querySelector('.rotation-panel').getBoundingClientRect();"
            " return [pc.width, r.width]; }"
        )
        assert abs(widths[0] - widths[1]) < 2, f"widths differ: {widths}"

    def test_preview_rises_to_top_right(self, page, live_server):
        self._enter_simple(page, live_server)
        pos = page.evaluate(
            "() => {"
            " const pc = document.querySelector('.playback-controls').getBoundingClientRect();"
            " const pv = document.querySelector('#vnc-preview-container').getBoundingClientRect();"
            " return {sameTop: Math.abs(pc.top - pv.top) < 2, toRight: pv.left >= pc.right - 2}; }"
        )
        assert pos["sameTop"], "Screen Preview top should align with Playback Controls top"
        assert pos["toRight"], "Screen Preview should sit to the right of Playback Controls"


class TestPlaybackControlsUnified:
    """The Playback Controls sliders use the SAME split layout in both simple and
    advanced modes: the two volume sliders (Karaoke, Filler) stack in a
    fixed-width left column at equal width, and Seek is the long bar filling the
    right column. (Replaces the earlier buttons+seek / volumes-side-by-side
    arrangement, which #154's .fade-controls had broken by displacing Seek.)"""

    @pytest.fixture(autouse=True)
    def _restore_advanced(self, live_server):
        """live_server is session-scoped; leaving simple mode set would leak
        into later advanced-mode tests. Restore advanced mode after each test."""
        yield
        req = urllib.request.Request(
            f"{live_server}/rotation/requests/config",
            data=json.dumps({"simple_mode": False}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()

    # Desktop width so the >=769px compact grid applies (not the mobile stack).
    _WIDTH = {"width": 1400, "height": 900}

    _LAYOUT_JS = """() => {
        const rect = (el) => el.getBoundingClientRect();
        const kar = rect(document.querySelector('#karaoke-volume').closest('.pc-slider-row'));
        const fil = rect(document.querySelector('#filler-volume').closest('.pc-slider-row'));
        const seek = rect(document.querySelector('#seek-slider').closest('.pc-slider-row'));
        return {
            // Two volumes stack in the left column at equal width.
            volumesSameWidth: Math.abs(kar.width - fil.width) < 2,
            fillerBelowKaraoke: fil.top > kar.top + 4 && Math.abs(fil.left - kar.left) < 2,
            // Seek is the long bar in the right column, sharing the volumes' band.
            seekRightOfVolumes: seek.left >= kar.right - 2,
            seekLongerThanVolumes: seek.width > kar.width + 20,
            seekAlignsWithVolumeBand: seek.top < fil.bottom && seek.bottom > kar.top,
        };
    }"""

    def _pitch_display(self, page):
        # Pitch lives inside the now-playing meta row, which is CSS-hidden while
        # idle; force the playing class and clear any renderer-set inline style
        # so we read the mode stylesheet's own verdict on #np-pitch-group.
        return page.evaluate(
            "() => {"
            " document.querySelector('#np-info').classList.add('np-info--playing');"
            " const pg = document.querySelector('#np-pitch-group');"
            " pg.style.display = '';"
            " return getComputedStyle(pg).display; }"
        )

    def _enter_simple(self, page, live_server):
        page.set_viewport_size(self._WIDTH)
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        page.locator("#mode-seg-simple").click()
        page.wait_for_function("document.body.classList.contains('simple-mode')")

    def _assert_split_layout(self, layout):
        assert layout["volumesSameWidth"], layout
        assert layout["fillerBelowKaraoke"], layout
        assert layout["seekRightOfVolumes"], layout
        assert layout["seekLongerThanVolumes"], layout
        assert layout["seekAlignsWithVolumeBand"], layout

    def test_advanced_mode_volumes_left_seek_right(self, page, live_server):
        page.set_viewport_size(self._WIDTH)
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        assert not page.evaluate(
            "document.body.classList.contains('simple-mode')"
        )
        self._assert_split_layout(page.evaluate(self._LAYOUT_JS))

    def test_simple_mode_volumes_left_seek_right(self, page, live_server):
        self._enter_simple(page, live_server)
        self._assert_split_layout(page.evaluate(self._LAYOUT_JS))

    def test_layout_matches_between_modes(self, page, live_server):
        page.set_viewport_size(self._WIDTH)
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        advanced = page.evaluate(self._LAYOUT_JS)
        page.locator("#mode-seg-simple").click()
        page.wait_for_function("document.body.classList.contains('simple-mode')")
        simple = page.evaluate(self._LAYOUT_JS)
        assert advanced == simple, f"advanced={advanced} simple={simple}"

    def test_pitch_control_present_in_advanced_mode(self, page, live_server):
        page.set_viewport_size(self._WIDTH)
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        assert self._pitch_display(page) != "none"

    def test_pitch_control_present_in_simple_mode(self, page, live_server):
        # Regression: simple mode used to force #np-pitch-group { display:none }.
        self._enter_simple(page, live_server)
        assert self._pitch_display(page) != "none"


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

    def test_download_complete_during_search_preserves_query(self, page, live_server):
        """A background download completing MUST NOT wipe the KJ's in-progress
        search query — it should refresh results in place, keeping the box."""
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
        page.locator("#catalog-search").fill("bohemian")
        page.wait_for_timeout(500)  # debounce -> searchActive
        page.route(
            "**/media",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(test_media),
            ),
        )
        page.evaluate(
            """async () => {
                await handleDownloadQueue([{id: 'q1', status: 'completed', title: 'Bohemian Rhapsody - Queen', url: 'http://test', error: null}]);
            }"""
        )
        page.wait_for_timeout(400)  # let the in-place re-search render
        # The query is preserved (NOT reset to '' or '*').
        assert page.locator("#catalog-search").input_value() == "bohemian"


# ---------------------------------------------------------------------------
# Section-header action-button framework (regression guard)
# ---------------------------------------------------------------------------

class TestHeaderButtonConsistency:
    """All top-right section-header buttons must share one size via the
    `.header-actions` framework. This guards against the recurring drift where
    a new/altered header button (classically the classless "Song Stats") ends
    up a different size. See docs/DEVELOPMENT.md § "Section header buttons"
    and docs/archive/2026-07-02-header-button-framework-design.md.
    """

    def test_all_header_buttons_share_one_size(self, app_page):
        """Every `.header-actions button` resolves to the SAME font-size,
        weight, padding and radius — regardless of its own class or lack of
        one. Computed styles resolve even for display:none buttons, so this
        holds in both Simple and Advanced mode."""
        styles = app_page.evaluate(
            """() => {
                const btns = [...document.querySelectorAll('.header-actions button')];
                const seen = {};
                for (const b of btns) {
                    const c = getComputedStyle(b);
                    const key = [c.fontSize, c.fontWeight, c.paddingTop,
                                 c.paddingRight, c.borderTopLeftRadius].join('|');
                    (seen[key] ||= []).push((b.textContent || '').trim().slice(0, 20));
                }
                return { count: btns.length, groups: seen };
            }"""
        )
        assert styles["count"] > 0, "No .header-actions buttons found"
        distinct = list(styles["groups"].keys())
        assert len(distinct) == 1, (
            "Section-header buttons drifted into multiple sizes — every header "
            "button must be sized by the .header-actions framework, not bespoke "
            f"per-button CSS. Groups: {styles['groups']}"
        )

    def test_song_stats_matches_its_siblings(self, app_page):
        """Song Stats is now a dedicated section (below Library) rather than a
        Rotation-header button; its section-header Refresh button is a classless
        `.header-actions button` and must still resolve to the same size as the
        other section-header buttons via the framework."""
        sizes = app_page.evaluate(
            """() => {
                const pick = s => {
                    const el = document.querySelector(s);
                    if (!el) return null;
                    const c = getComputedStyle(el);
                    return c.fontSize + '|' + c.paddingTop + '|' + c.fontWeight;
                };
                return { stats: pick('#song-stats .header-actions button'),
                         refresh: pick('.rotation-refresh-btn') };
            }"""
        )
        assert sizes["stats"] is not None and sizes["refresh"] is not None
        assert sizes["stats"] == sizes["refresh"], (
            f"Song Stats section button ({sizes['stats']}) must match sibling "
            f"section-header buttons ({sizes['refresh']})"
        )

    def test_no_header_button_outside_header_actions(self, app_page):
        """Structural guard: a <button> placed in a section header must live
        inside `.header-actions` (the only documented exception is the
        Simple/Advanced segmented pill in `.mode-segmented`). A bare button in
        a `.header-row` is exactly how the drift starts."""
        strays = app_page.evaluate(
            """() => {
                const out = [];
                for (const b of document.querySelectorAll('.header-row button')) {
                    if (!b.closest('.header-actions') && !b.closest('.mode-segmented')) {
                        out.push((b.textContent || '').trim().slice(0, 20));
                    }
                }
                return out;
            }"""
        )
        assert strays == [], (
            "Section-header button(s) outside .header-actions (wrap them so they "
            f"get consistent sizing): {strays}"
        )


class TestSongStatsCollapse:
    """Song Stats is a collapsible section: hidden entirely in simple mode,
    collapsed by default in advanced mode with only its header-row showing
    (matching the collapsed Screen Preview footprint), and expandable by
    clicking the header."""

    def test_collapsed_by_default_in_advanced(self, app_page):
        """On load (advanced mode) the section carries `.collapsed` and its
        body is hidden, while the header + Refresh button stay visible."""
        section = app_page.locator("#song-stats")
        expect(section).to_have_class(re.compile(r"\bcollapsed\b"))
        # Header row is always visible; the body content is not.
        expect(app_page.locator("#song-stats .song-stats-header")).to_be_visible()
        expect(app_page.locator("#song-stats .header-actions button")).to_be_visible()
        expect(app_page.locator("#statsOverview")).not_to_be_visible()
        expect(app_page.locator("#statsViewSwitch")).not_to_be_visible()
        expect(app_page.locator("#statsBody")).not_to_be_visible()

    def test_collapsed_footprint_is_just_the_header(self, app_page):
        """Collapsed, the section is only as tall as its header-row plus the
        container padding — i.e. no body area is reserved. This is what makes
        it match the collapsed Screen Preview section's footprint."""
        gap = app_page.evaluate(
            "() => {"
            " const s = document.querySelector('#song-stats').getBoundingClientRect();"
            " const h = document.querySelector('#song-stats .header-row').getBoundingClientRect();"
            " return s.height - h.height; }"
        )
        # Only the container's vertical padding (~2 * 0.85rem ≈ 27px) beyond the
        # header — nothing like the 40vh stats body.
        assert 0 <= gap < 48, f"Collapsed section reserves body space: extra={gap}px"

    def test_header_click_toggles_expand_collapse(self, app_page):
        """Clicking the header (not the Refresh button) expands the section and
        loads its content; clicking again collapses it back."""
        section = app_page.locator("#song-stats")
        # Click the title area — safely outside .header-actions.
        app_page.locator("#song-stats .song-stats-header h2").click()
        expect(section).not_to_have_class(re.compile(r"\bcollapsed\b"))
        expect(app_page.locator("#statsOverview")).to_be_visible()
        # Lazy-load fired: overview cards render (empty DB => zero-value cards).
        expect(app_page.locator("#statsOverview .stat-card").first).to_be_visible()

        app_page.locator("#song-stats .song-stats-header h2").click()
        expect(section).to_have_class(re.compile(r"\bcollapsed\b"))
        expect(app_page.locator("#statsOverview")).not_to_be_visible()

    def test_refresh_button_reveals_when_collapsed(self, app_page):
        """Refresh on a collapsed section expands it (so the click isn't a
        silent no-op) rather than toggling collapse."""
        section = app_page.locator("#song-stats")
        expect(section).to_have_class(re.compile(r"\bcollapsed\b"))
        app_page.locator("#song-stats .header-actions button").click()
        expect(section).not_to_have_class(re.compile(r"\bcollapsed\b"))
        expect(app_page.locator("#statsOverview")).to_be_visible()

    def test_refresh_while_collapsed_seeds_singer_datalist(self, app_page):
        """Regression: Refresh sets loaded=true, so it must itself populate the
        singer-filter datalist — otherwise the later lazy-load short-circuits and
        #statsSingerList is never seeded when the KJ refreshes while collapsed."""
        app_page.evaluate(
            "() => { window.__popCalls = 0;"
            " const o = window.populateSingerDatalist;"
            " window.populateSingerDatalist = function () {"
            "   window.__popCalls++; return o.apply(this, arguments); }; }"
        )
        expect(app_page.locator("#song-stats")).to_have_class(
            re.compile(r"\bcollapsed\b")
        )
        app_page.locator("#song-stats .header-actions button").click()
        expect(app_page.locator("#statsOverview")).to_be_visible()
        assert app_page.evaluate("() => window.__popCalls") >= 1, (
            "Refresh on a collapsed section must seed the singer datalist"
        )

    def test_expanded_state_persists_across_reload(self, app_page, live_server):
        """The KJ's expand choice is remembered per browser via localStorage."""
        app_page.locator("#song-stats .song-stats-header h2").click()
        expect(app_page.locator("#song-stats")).not_to_have_class(
            re.compile(r"\bcollapsed\b")
        )
        app_page.reload()
        app_page.wait_for_load_state("networkidle")
        expect(app_page.locator("#song-stats")).not_to_have_class(
            re.compile(r"\bcollapsed\b")
        )

    def test_hidden_in_simple_mode(self, page, live_server):
        """Simple mode (stand-in KJ) hides the whole Song Stats section."""
        try:
            page.set_viewport_size({"width": 1400, "height": 900})
            page.goto(live_server)
            page.wait_for_load_state("networkidle")
            page.locator("#mode-seg-simple").click()
            page.wait_for_function(
                "document.body.classList.contains('simple-mode')"
            )
            expect(page.locator("#song-stats")).not_to_be_visible()
        finally:
            # live_server is session-scoped — restore advanced mode so we don't
            # leak simple mode into later tests.
            req = urllib.request.Request(
                f"{live_server}/rotation/requests/config",
                data=json.dumps({"simple_mode": False}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                resp.read()


# ---------------------------------------------------------------------------
# VNC Screen Preview — Max (fullscreen) mode
# ---------------------------------------------------------------------------

class TestVncMaxMode:
    """Max mode docks the interactive controls into the fixed top toolbar (they
    render below the preview otherwise, hidden behind the fullscreen canvas) and
    restores them when leaving."""

    @staticmethod
    def _parent_id(page, selector):
        return page.evaluate(
            f"document.querySelector('{selector}').parentElement.id"
        )

    def test_max_mode_docks_controls_into_toolbar(self, app_page):
        app_page.evaluate("window.setVncSize('max')")
        try:
            assert self._parent_id(app_page, "#vnc-controls") == "vnc-max-toolbar"
            assert (
                self._parent_id(app_page, "#vnc-interactive-controls")
                == "vnc-max-toolbar"
            )
            expect(app_page.locator("#vnc-max-toolbar")).to_have_class(
                re.compile(r"\bvnc-max-toolbar-full\b")
            )
        finally:
            app_page.evaluate("window.setVncSize('400px')")

    def test_leaving_max_restores_controls(self, app_page):
        app_page.evaluate("window.setVncSize('max')")
        app_page.evaluate("window.setVncSize('400px')")
        assert (
            self._parent_id(app_page, "#vnc-controls") == "vnc-preview-container"
        )
        assert (
            self._parent_id(app_page, "#vnc-interactive-controls")
            == "vnc-preview-container"
        )
        expect(app_page.locator("#vnc-max-toolbar")).not_to_have_class(
            re.compile(r"\bvnc-max-toolbar-full\b")
        )

    def test_canvas_not_force_stretched(self, page, live_server):
        """Regression: the canvas must not be forced to width/height:100% — that
        stretch broke click coordinate mapping in Max mode. It should cap with
        max-width/max-height so noVNC's aspect-preserving sizing wins."""
        css = page.request.get(f"{live_server}/static/style.css").text()
        match = re.search(r"\.vnc-thumbnail canvas\s*\{([^}]*)\}", css)
        assert match, ".vnc-thumbnail canvas rule not found"
        block = match.group(1)
        assert "max-width" in block and "max-height" in block
        assert "width: 100% !important" not in block
        assert "height: 100% !important" not in block
