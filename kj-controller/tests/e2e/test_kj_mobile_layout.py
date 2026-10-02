"""KJ UI on a phone: rotation rows stack so names/songs stay visible, and the
playback sliders can be collapsed (remembered per device)."""

import pytest
from playwright.sync_api import expect

# A busy row: two singers, long song, every pill/badge, SMS configured — the
# worst case for horizontal space.
_RENDER_ROWS = (
    "() => {"
    "  for (let i = 1; i < 100000; i++) clearInterval(i);"  # stop the poll re-rendering
    "  window.rotationData = ["
    "    { id: 1, position: 1, singer: 'Andrew & Jenny',"
    "      singers_json: JSON.stringify(['Andrew', 'Jenny']),"
    "      song_artist: 'Hellogoodbye - Here (In Your Arms) (Extended Version)',"
    "      status: 'Waiting', songs_sung: 0, wait_minutes: 125, paid: true,"
    "      estimated_time: '8:30 pm', file_path: '/d/x.mp4',"
    "      sms: { configured: true, available: true } },"
    "    { id: 2, position: 2, singer: 'Christopher', song_artist: 'The Strokes - Machu Picchu',"
    "      status: 'Up Next', songs_sung: 2, wait_minutes: 40, file_path: '/d/y.mp4',"
    "      sms: { configured: true, available: false } },"
    "  ];"
    "  renderRotation(window.rotationData);"
    "}"
)


def _overflow_report(page):
    return page.evaluate(
        "() => {"
        "  const cw = document.documentElement.clientWidth;"
        "  const out = [];"
        "  document.querySelectorAll('#rotation-list *').forEach(e => {"
        "    const r = e.getBoundingClientRect();"
        "    if (r.width && r.right > cw + 1) out.push(e.className);"
        "  });"
        "  return out;"
        "}"
    )


@pytest.mark.parametrize("width", [360, 390])
class TestRotationOnPhone:
    @pytest.fixture
    def phone(self, page, live_server, width):
        page.set_viewport_size({"width": width, "height": 844})
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        page.evaluate(_RENDER_ROWS)
        return page

    def test_names_and_song_are_visible(self, phone, width):
        row = phone.locator(".rotation-entry").first
        for sel in (".rotation-singer-pill >> nth=0", ".rotation-song"):
            box = row.locator(sel).bounding_box()
            assert box and box["width"] > 30, f"{sel} squeezed: {box}"
        # Song gets its own line, below the singer line.
        name_box = row.locator(".rotation-singer-pill").first.bounding_box()
        song_box = row.locator(".rotation-song").bounding_box()
        assert song_box["y"] > name_box["y"] + name_box["height"] / 2

    def test_actions_sit_below_info_full_width(self, phone, width):
        row = phone.locator(".rotation-entry").first
        info = row.locator(".rotation-info").bounding_box()
        actions = row.locator(".rotation-actions").bounding_box()
        assert actions["y"] >= info["y"] + info["height"] - 1
        assert info["width"] > width * 0.6

    def test_nothing_overflows_or_clips(self, phone, width):
        assert _overflow_report(phone) == []
        clipped = phone.evaluate(
            "() => [...document.querySelectorAll('#rotation-list .rotation-btn')]"
            "  .filter(b => b.scrollWidth > b.clientWidth + 1).map(b => b.textContent)"
        )
        assert clipped == []


def test_desktop_rotation_row_stays_single_line(page, live_server):
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(live_server)
    page.wait_for_load_state("networkidle")
    page.evaluate(_RENDER_ROWS)
    row = page.locator(".rotation-entry").nth(1)
    info = row.locator(".rotation-info").bounding_box()
    actions = row.locator(".rotation-actions").bounding_box()
    assert abs((info["y"] + info["height"] / 2) - (actions["y"] + actions["height"] / 2)) < 8


class TestSlidersToggle:
    @pytest.fixture(autouse=True)
    def _clear_pref(self, page, live_server):
        yield
        page.evaluate("() => localStorage.removeItem('kj-sliders-hidden')")

    def test_toggle_hides_and_persists(self, page, live_server):
        page.goto(live_server)
        page.wait_for_load_state("networkidle")
        btn = page.locator("#pc-sliders-toggle")
        sliders = page.locator("#pc-sliders")
        expect(sliders).to_be_visible()
        expect(btn).to_have_text("Hide sliders")

        btn.click()
        expect(sliders).to_be_hidden()
        expect(btn).to_have_text("Show sliders")
        expect(btn).to_have_attribute("aria-expanded", "false")

        page.reload()
        page.wait_for_load_state("networkidle")
        expect(page.locator("#pc-sliders")).to_be_hidden()

        page.locator("#pc-sliders-toggle").click()
        expect(page.locator("#pc-sliders")).to_be_visible()
        assert page.evaluate("() => localStorage.getItem('kj-sliders-hidden')") is None
