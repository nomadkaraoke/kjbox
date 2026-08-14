"""E2E Playwright tests for singer stats panel."""

import pytest


@pytest.fixture
def stats_page(app_page):
    """Navigate to the app, add a singer, and ensure stats panel is visible."""
    page = app_page
    page.locator('.rotation-add-btn').click()
    page.locator('#singer-input-container').wait_for(state='visible')
    page.locator('#rotation-singer').fill('StatsTestSinger')
    page.locator('#rotation-singer').press('Enter')
    page.locator('#rotation-song').fill('Stats Test Song')
    page.locator('#rotation-add-btn-submit').click()
    page.locator('.rotation-entry').first.wait_for(state='visible')
    # Stats panel is always in the DOM; wait for the row to appear after
    # the next fetchRotation poll (up to 10s interval).
    page.locator('.singer-stats-row', has_text='StatsTestSinger').first.wait_for(
        state='visible', timeout=15000
    )
    return page


class TestSingerStatsPanel:
    def test_panel_renders_with_singer(self, stats_page):
        page = stats_page
        page.locator('.singer-stats-row').first.wait_for(state='visible')
        assert page.locator('.singer-stats-name', has_text='StatsTestSinger').count() > 0

    def test_toggle_hide_show(self, stats_page):
        page = stats_page
        # Ensure list is currently visible before toggling
        page.locator('#singer-stats-list').wait_for(state='visible')
        page.locator('.singer-stats-toggle').click()
        assert page.locator('#singer-stats-list').is_hidden()
        page.locator('.singer-stats-toggle').click()
        assert page.locator('#singer-stats-list').is_visible()


class TestSingerRename:
    def test_rename_inline(self, stats_page):
        page = stats_page
        # Use .first to handle accumulated entries from the session-scoped server
        row = page.locator('.singer-stats-row', has_text='StatsTestSinger').first
        row.locator('.singer-stats-btn', has_text='Edit').click()
        # After Edit is clicked the row's inner HTML is replaced — has_text no longer
        # matches, so find the input and Save button directly on the page.
        input_el = page.locator('.singer-stats-row .rotation-edit-input').first
        input_el.wait_for(state='visible', timeout=5000)
        input_el.fill('RenamedSinger')
        page.locator('.singer-stats-row .singer-stats-btn', has_text='Save').first.click()
        page.locator('.singer-stats-name', has_text='RenamedSinger').first.wait_for(
            state='visible', timeout=5000
        )


class TestSingerBrb:
    def test_brb_toggle(self, stats_page):
        page = stats_page
        row = page.locator('.singer-stats-row', has_text='StatsTestSinger').first
        row.locator('.singer-stats-btn', has_text='BRB').click()
        page.locator('.singer-stats-row.singer-brb').first.wait_for(state='visible')
        # After marking Back, the brb class should disappear
        row = page.locator('.singer-stats-row', has_text='StatsTestSinger').first
        row.locator('.singer-stats-btn', has_text='Back').click()
        page.wait_for_timeout(500)
        assert page.locator('.singer-stats-row.singer-brb').count() == 0


class TestSingerRemoveRestore:
    def test_remove_and_restore(self, stats_page):
        page = stats_page
        row = page.locator('.singer-stats-row', has_text='StatsTestSinger').first
        row.locator('.singer-stats-btn', has_text='Left').click()
        # Gone section is collapsed by default — expand it to see the row
        gone_section = page.locator('.singer-section-gone')
        gone_section.wait_for(state='visible', timeout=10000)
        gone_section.locator('.singer-section-header').click()
        page.locator('.singer-stats-row.singer-left').first.wait_for(state='visible', timeout=10000)
        left_row = page.locator('.singer-stats-row', has_text='StatsTestSinger').first
        left_row.locator('.singer-stats-btn', has_text='Restore').click()
        page.wait_for_timeout(500)
        assert page.locator('.singer-stats-row.singer-left').count() == 0


class TestDoneSingerLeftRoundTrip:
    def test_done_singer_can_be_marked_left_and_restored(self, app_page):
        page = app_page

        # Add a singer with a song via the UI
        page.locator('.rotation-add-btn').click()
        page.locator('#singer-input-container').wait_for(state='visible')
        page.locator('#rotation-singer').fill('AliceRoundTrip')
        page.locator('#rotation-singer').press('Enter')
        page.locator('#rotation-song').fill('Alice Round Trip Song')
        page.locator('#rotation-add-btn-submit').click()
        page.locator('.rotation-entry', has_text='AliceRoundTrip').first.wait_for(state='visible')

        # Click Done on the rotation entry
        entry = page.locator('.rotation-entry', has_text='AliceRoundTrip').first
        entry.locator('.rotation-btn-done').click()

        # Expand the Done section in singer stats (collapsed by default)
        done_section = page.locator('.singer-section-done')
        done_section.wait_for(state='visible', timeout=15000)
        # Click the header to expand if collapsed
        done_section.locator('.singer-section-header').click()
        page.locator('.singer-section-done .singer-stats-row', has_text='AliceRoundTrip').first.wait_for(
            state='visible', timeout=10000
        )

        # Click Left to mark the singer as having left
        done_row = page.locator('.singer-section-done .singer-stats-row', has_text='AliceRoundTrip').first
        done_row.locator('.singer-stats-btn', has_text='Left').click()

        # Gone section is collapsed by default — expand it to see the row
        gone_section = page.locator('.singer-section-gone')
        gone_section.wait_for(state='visible', timeout=10000)
        gone_section.locator('.singer-section-header').click()
        page.locator('.singer-stats-row.singer-left', has_text='AliceRoundTrip').first.wait_for(
            state='visible', timeout=10000
        )

        # Click Restore to bring Alice back
        left_row = page.locator('.singer-stats-row.singer-left', has_text='AliceRoundTrip').first
        left_row.locator('.singer-stats-btn', has_text='Restore').click()

        # Singer should no longer have the singer-left class
        page.wait_for_timeout(1000)
        assert page.locator('.singer-stats-row.singer-left', has_text='AliceRoundTrip').count() == 0
        # Alice should be back in the Done section
        assert page.locator('.singer-stats-row', has_text='AliceRoundTrip').count() > 0


class TestSplitSingerE2E:
    def test_split_kai_into_kai_p(self, app_page):
        page = app_page

        # Open the add form once — it stays open after each submit
        page.locator('.rotation-add-btn').click()
        page.locator('#singer-input-container').wait_for(state='visible')

        # Add two entries for "KaiSplit"
        for song in ('KaiSplit Song A', 'KaiSplit Song B'):
            page.locator('#rotation-singer').fill('KaiSplit')
            page.locator('#rotation-singer').press('Enter')
            page.locator('#rotation-song').fill(song)
            page.locator('#rotation-add-btn-submit').click()
            page.locator('.rotation-entry', has_text='KaiSplit').last.wait_for(state='visible')

        # Wait for singer stats to show KaiSplit (may be collapsed in Active)
        kai_stats_row = page.locator('.singer-stats-row', has_text='KaiSplit').first
        kai_stats_row.wait_for(state='visible', timeout=15000)

        # Click Split on KaiSplit's row in singer stats
        kai_stats_row.locator('.singer-stats-btn', has_text='Split').click()

        # Split modal should appear
        page.locator('.singer-split-modal-backdrop').wait_for(state='visible', timeout=5000)

        # Check the checkbox for Song B
        song_b_label = page.locator('.singer-split-entry', has_text='KaiSplit Song B')
        song_b_label.locator('input[type=checkbox]').check()

        # Type the new name "KaiSplit P"
        page.locator('.singer-split-new-input').fill('KaiSplit P')

        # Click the Split confirm button
        page.locator('.singer-split-confirm').click()

        # Modal should close
        page.locator('.singer-split-modal-backdrop').wait_for(state='hidden', timeout=5000)

        # Both "KaiSplit" and "KaiSplit P" should appear in singer stats
        page.locator('.singer-stats-row', has_text='KaiSplit P').first.wait_for(
            state='visible', timeout=10000
        )
        assert page.locator('.singer-stats-name', has_text='KaiSplit').count() > 0
        assert page.locator('.singer-stats-name', has_text='KaiSplit P').count() > 0


class TestMergeModalE2E:
    def test_merge_modal_flow_and_no_device_icon_for_kj_added(self, app_page):
        page = app_page

        # Two KJ-added singers so Merge has a target.
        page.locator('.rotation-add-btn').click()
        page.locator('#singer-input-container').wait_for(state='visible')
        for singer, song in (('MergeA', 'Song A'), ('MergeB', 'Song B')):
            page.locator('#rotation-singer').fill(singer)
            page.locator('#rotation-singer').press('Enter')
            page.locator('#rotation-song').fill(song)
            page.locator('#rotation-add-btn-submit').click()
            page.locator('.rotation-entry', has_text=singer).last.wait_for(state='visible')

        row_a = page.locator('.singer-stats-row', has_text='MergeA').first
        row_a.wait_for(state='visible', timeout=15000)

        # KJ-added singers have no linked device session → no 📱 icon.
        assert row_a.locator('.singer-device-icon').count() == 0

        # Merge opens the new modal (not the old inline dropdown).
        row_a.locator('.singer-stats-btn', has_text='Merge').click()
        page.locator('.merge-modal').wait_for(state='visible', timeout=5000)
        assert page.locator('.merge-search').count() == 1

        # Pick MergeB as the partner → confirm step spells out keep/remove.
        # Both are KJ-added (no device), so the default keeper is the picked
        # partner, MergeB.
        page.locator('.merge-option', has_text='MergeB').first.click()
        page.locator('.merge-confirm').wait_for(state='visible', timeout=5000)
        assert 'MergeB' in page.locator('.merge-keep .merge-side-name').inner_text()

        # Swap flips the keeper to MergeA (removing MergeB instead).
        page.locator('.merge-btn-swap').click()
        assert 'MergeA' in page.locator('.merge-keep .merge-side-name').inner_text()

        # Confirm performs the merge. Assert the real outcome — MergeB is absorbed
        # into MergeA, so only MergeA's name remains in the singer list.
        page.locator('.merge-btn-confirm').click()
        page.locator('.merge-modal').wait_for(state='hidden', timeout=5000)
        page.locator('.singer-stats-name', has_text='MergeB').first.wait_for(
            state='detached', timeout=10000)
        assert page.locator('.singer-stats-name', has_text='MergeA').count() > 0
