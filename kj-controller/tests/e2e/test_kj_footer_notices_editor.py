"""KJ UI: editable venue notices (re-word/re-icon pre-built ones, add your own)
and the phone contact link, saved through /rotation/requests/config."""
import json

from playwright.sync_api import expect


def _open(app_page):
    app_page.evaluate("openSingRequestsModal()")
    wrap = app_page.locator("#sing-footer-notices")
    expect(wrap.locator(".sing-footer-notice-row").first).to_be_attached()
    return wrap


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
    app_page.locator('#sing-footer-social input[data-social="phone"]').fill("+1 803 636 3267")
    with app_page.expect_request("**/rotation/requests/config") as req:
        app_page.evaluate("saveSingFooterSettings()")
    fs = json.loads(req.value.post_data)["footer_settings"]
    custom_key = custom.get_attribute("data-notice")
    assert custom_key.startswith("c-")
    assert fs["notices"] == ["water", custom_key]
    assert fs["notice_defs"]["water"] == {"icon": "", "text": "Water is on the house — ask the bar."}
    assert fs["notice_defs"][custom_key] == {"icon": "🍕", "text": "Pizza at 10!"}
    assert fs["social"]["phone"] == "+1 803 636 3267"
    # Round trip: the saved notices come back on switched-on rows, in order.
    app_page.wait_for_function(
        """() => document.querySelector('#sing-footer-notices .sing-footer-notice-row [data-role="text"]')
              .value === 'Water is on the house — ask the bar.'""")
    rows = wrap.locator(".sing-footer-notice-row")
    assert rows.nth(0).get_attribute("data-notice") == "water"
    assert rows.nth(1).get_attribute("data-notice") == custom_key
