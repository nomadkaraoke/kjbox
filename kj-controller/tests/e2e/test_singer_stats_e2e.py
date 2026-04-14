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
        row.locator('.singer-stats-btn', has_text='Remove').click()
        page.locator('.singer-stats-row.singer-left').first.wait_for(state='visible')
        left_row = page.locator('.singer-stats-row', has_text='StatsTestSinger').first
        left_row.locator('.singer-stats-btn', has_text='Restore').click()
        page.wait_for_timeout(500)
        assert page.locator('.singer-stats-row.singer-left').count() == 0
