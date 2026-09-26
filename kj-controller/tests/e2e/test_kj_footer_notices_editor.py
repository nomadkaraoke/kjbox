"""KJ UI: editable venue notices (re-word/re-icon pre-built ones, add your own)
and the phone contact link, saved through /rotation/requests/config."""
import json

from playwright.sync_api import expect


def _open(app_page):
    app_page.evaluate("openSingRequestsModal()")
    wrap = app_page.locator("#sing-footer-notices")
    expect(wrap.locator(".sing-footer-notice-row").first).to_be_attached()
    return wrap


def test_rows_lay_out_emoji_text_and_buttons_on_one_line(app_page, live_server):
    """2026-09-26: the global input width rule made the emoji box fill the row,
    pushing the wording box and ↑/↓ off-screen."""
    wrap = _open(app_page)
    row = wrap.locator('.sing-footer-notice-row[data-notice="water"]')
    rb = row.bounding_box()
    icon = row.locator('[data-role="icon"]').bounding_box()
    text = row.locator('[data-role="text"]').bounding_box()
    down = row.locator(".sing-footer-notice-btn").last.bounding_box()
    assert icon["width"] < 80
    assert text["width"] > rb["width"] * 0.4
    assert icon["x"] < text["x"] < down["x"]
    assert down["x"] + down["width"] <= rb["x"] + rb["width"] + 1   # nothing overflows the row
    expect(row.locator('[data-role="text"]')).to_have_attribute(
        "placeholder", "Free water for singers at the bar. (default, translated)")


def test_edit_add_and_save_notices(app_page, live_server):
    wrap = _open(app_page)
    expect(wrap.locator('.sing-footer-notice-row[data-notice="water"] [data-role="icon"]')).to_have_value("💧")
    water = wrap.locator('.sing-footer-notice-row[data-notice="water"]')
    water.locator('[data-role="on"]').check()
    water.locator('[data-role="text"]').fill("Water is on the house — ask the bar.")
    app_page.locator(".sing-footer-add-notice").click()
    custom = wrap.locator(".sing-footer-notice-row").last
    custom.locator('[data-role="icon"]').fill("🍕")
    custom.locator('[data-role="text"]').fill("Pizza at 10!")
    custom_key = custom.get_attribute("data-notice")   # rows re-render after save
    app_page.locator('#sing-footer-social input[data-social="phone"]').fill("+1 803 636 3267")
    with app_page.expect_response(
            lambda r: r.url.endswith("/rotation/requests/config") and r.request.method == "POST") as resp:
        app_page.evaluate("saveSingFooterSettings()")
    assert resp.value.ok
    fs = json.loads(resp.value.request.post_data)["footer_settings"]
    assert custom_key.startswith("c-")
    assert fs["notices"] == ["water", custom_key]
    assert fs["notice_defs"]["water"] == {"icon": "", "text": "Water is on the house — ask the bar."}
    assert fs["notice_defs"][custom_key] == {"icon": "🍕", "text": "Pizza at 10!"}
    assert fs["social"]["phone"] == "+1 803 636 3267"
    # Round trip from the SERVER: reload the page and reopen the editor.
    app_page.reload()
    app_page.wait_for_load_state("networkidle")
    wrap = _open(app_page)
    rows = wrap.locator(".sing-footer-notice-row")
    expect(rows.nth(0).locator('[data-role="text"]')).to_have_value("Water is on the house — ask the bar.")
    expect(rows.nth(0).locator('[data-role="on"]')).to_be_checked()
    assert rows.nth(0).get_attribute("data-notice") == "water"
    assert rows.nth(1).get_attribute("data-notice") == custom_key
    expect(rows.nth(1).locator('[data-role="icon"]')).to_have_value("🍕")
    expect(app_page.locator('#sing-footer-social input[data-social="phone"]')).to_have_value("+1 803 636 3267")
