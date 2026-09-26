"""E2E: /play answering 503 library_drive_offline shows a replug toast (2026-09-24)."""

import json

from playwright.sync_api import expect


def test_drive_offline_play_shows_replug_toast_and_banner(app_page):
    page = app_page
    alert = {"mount": "/mnt/Nomad4TBOne", "since": 1727215878,
             "message": "SSD disconnected (Nomad4TBOne) — unplug and replug it now!"}
    page.route("**/play", lambda r: r.fulfill(
        status=503, content_type="application/json",
        body=json.dumps({"error": "library_drive_offline", "message": alert["message"],
                         "alert": alert})))
    page.evaluate("playMedia('/mnt/Nomad4TBOne/karaoke/Lady A - Downtown [KCD-75052].zip')")
    expect(page.locator("#playability-toast")).to_contain_text("unplug and replug")
    expect(page.locator("#ssd-alert-banner")).to_be_visible()


def test_bad_file_error_does_not_claim_drive_offline(app_page):
    page = app_page
    page.route("**/play", lambda r: r.fulfill(
        status=400, content_type="application/json",
        body=json.dumps({"error": "Invalid or inaccessible file path"})))
    page.evaluate("playMedia('/mnt/Nomad4TBOne/karaoke/missing.mp4')")
    page.wait_for_timeout(300)
    expect(page.locator("#playability-toast")).to_have_count(0)
