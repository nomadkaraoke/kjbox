"""KJ rotation: ⚡ badges for a make-it entry's quick (draft) version."""

import json

from playwright.sync_api import expect


def _entry(eid, **extra):
    e = {"id": eid, "singer": "Mary", "song_artist": "Creep - Radiohead", "status": "Being Made (!)",
         "position": eid, "songs_sung": 0, "wait_minutes": 0, "gen_job_id": f"job-{eid}",
         "gen_status": "processing", "file_path": None}
    e.update(extra)
    return e


class TestKjQuickBadges:
    def test_quick_ready_badge_links_draft(self, app_page):
        page = app_page
        posted = []

        def on_post(route):
            posted.append(json.loads(route.request.post_data))
            linked = _entry(1, status="Waiting", file_path="/u/q.mp4",
                            quick={"state": "chosen", "lyrics_tier": "synced"})
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"success": True, "entries": [linked]}))
        page.route("**/rotation/use-quick", on_post)
        page.on("dialog", lambda d: d.accept())
        page.evaluate("(entries) => renderRotation(entries)", [
            _entry(1, quick={"state": "ready", "lyrics_tier": "synced"}),
            _entry(2),
        ])
        rows = page.locator("#rotation-list .rotation-entry")
        ready = rows.nth(0).locator(".prep-quick-ready")
        expect(ready).to_have_text("⚡ QUICK READY")
        expect(rows.nth(0).locator(".prep-making")).to_have_text("MAKING")   # gen badge stays
        expect(rows.nth(1).locator(".prep-quick-ready")).to_have_count(0)

        ready.click()
        expect(rows.nth(0).locator(".rotation-prep-badge.prep-quick")).to_have_text("⚡ QUICK")
        assert posted == [{"id": 1}]
        assert "replaces it automatically" in rows.nth(0).locator(".prep-quick").get_attribute("title")

    def test_upgraded_entry_shows_ready(self, app_page):
        page = app_page
        page.evaluate("(entries) => renderRotation(entries)", [
            _entry(1, status="Waiting", file_path="/m/NOMAD-1.mp4", gen_status="complete",
                   quick={"state": "upgraded", "lyrics_tier": "synced"}),
        ])
        row = page.locator("#rotation-list .rotation-entry").first
        expect(row.locator(".prep-ready")).to_have_text("READY")
        expect(row.locator(".prep-quick")).to_have_count(0)
