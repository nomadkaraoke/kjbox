"""E2E: draggable separator between the KJ UI's two columns, and wrapping
rotation song titles (so a long title is readable instead of ellipsized)."""

import pytest


def _col_widths(page):
    return page.evaluate(
        "[document.getElementById('col1').getBoundingClientRect().width,"
        " document.getElementById('col2').getBoundingClientRect().width]"
    )


def _drag_resizer_to(page, x):
    box = page.locator('#column-resizer').bounding_box()
    page.mouse.move(box['x'] + box['width'] / 2, box['y'] + 50)
    page.mouse.down()
    page.mouse.move(x, box['y'] + 50, steps=5)
    page.mouse.up()


@pytest.fixture
def desktop_page(page, live_server):
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(live_server)
    page.wait_for_load_state("networkidle")
    page.evaluate("localStorage.removeItem('kj-col1-share')")
    page.reload()
    page.wait_for_load_state("networkidle")
    return page


class TestColumnResizer:
    def test_default_split_is_two_to_one(self, desktop_page):
        col1, col2 = _col_widths(desktop_page)
        assert col1 / (col1 + col2) == pytest.approx(2 / 3, abs=0.01)

    def test_drag_widens_left_column_and_persists(self, desktop_page):
        page = desktop_page
        before, _ = _col_widths(page)
        box = page.locator('#column-resizer').bounding_box()
        _drag_resizer_to(page, box['x'] + box['width'] / 2 + 120)
        after, col2 = _col_widths(page)
        assert after == pytest.approx(before + 120, abs=4)
        assert not page.evaluate("document.body.classList.contains('col-resizing')")

        page.reload()
        page.wait_for_load_state("networkidle")
        reloaded, _ = _col_widths(page)
        assert reloaded == pytest.approx(after, abs=4)

    def test_drag_is_clamped_so_right_column_stays_usable(self, desktop_page):
        page = desktop_page
        _drag_resizer_to(page, 1395)
        _, col2 = _col_widths(page)
        assert col2 >= 255

    def test_double_click_resets(self, desktop_page):
        page = desktop_page
        box = page.locator('#column-resizer').bounding_box()
        _drag_resizer_to(page, box['x'] - 250)
        page.locator('#column-resizer').dblclick()
        col1, col2 = _col_widths(page)
        assert col1 / (col1 + col2) == pytest.approx(2 / 3, abs=0.01)
        assert page.evaluate("localStorage.getItem('kj-col1-share')") is None

    def test_arrow_keys_nudge(self, desktop_page):
        page = desktop_page
        before, _ = _col_widths(page)
        page.locator('#column-resizer').focus()
        page.keyboard.press('ArrowRight')
        after, _ = _col_widths(page)
        assert after > before + 10

    def test_hidden_on_mobile(self, page, live_server):
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        assert not page.locator('#column-resizer').is_visible()


class TestRotationSongWraps:
    def test_long_song_title_wraps_not_ellipsized(self, desktop_page):
        page = desktop_page
        page.locator('.rotation-add-btn').click()
        page.locator('#singer-input-container').wait_for(state='visible')
        page.locator('#rotation-singer').fill('WrapTester')
        page.locator('#rotation-singer').press('Enter')
        long_title = ('An Extraordinarily Long Invented Song Title (Extended Club Remix '
                      'Featuring Several Guests) - The Imaginary Band With A Long Name')
        page.locator('#rotation-song').fill(long_title)
        page.locator('#rotation-add-btn-submit').click()
        song = page.locator('.rotation-entry .rotation-song', has_text='Extraordinarily').first
        song.wait_for(state='visible')

        # Narrow col1 as far as allowed so the title can't fit on one line.
        _drag_resizer_to(page, 0)
        metrics = song.evaluate(
            "el => ({ws: getComputedStyle(el).whiteSpace,"
            " sw: el.scrollWidth, cw: el.clientWidth,"
            " h: el.getBoundingClientRect().height,"
            " lh: parseFloat(getComputedStyle(el).lineHeight) || 0})"
        )
        assert metrics['ws'] == 'normal'
        assert metrics['sw'] <= metrics['cw'] + 1   # nothing clipped
        assert metrics['h'] > 20                     # spans multiple lines
