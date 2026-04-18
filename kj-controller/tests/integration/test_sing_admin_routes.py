"""Integration tests for the admin /rotation/requests/* endpoints and token hooks."""

from unittest.mock import MagicMock, patch

import pytest

from app import create_app


@pytest.fixture
def admin_app(mock_config):
    app = create_app(config=mock_config)
    app.config["TESTING"] = True
    yield app
    app.catalog.close()


@pytest.fixture
def admin_client(admin_app):
    with admin_app.test_client() as c:
        yield c


def _make_pending(app, **overrides):
    body = {
        "singer_name": "Andrew",
        "phone": "+1 555 0000",
        "song_artist": "Queen",
        "song_title": "Bohemian Rhapsody",
        "source_type": "local",
        "source_ref": "/tmp/song.mp4",
    }
    body.update(overrides)
    return app.sing_store.create_request(**body)


class TestList:
    def test_empty(self, admin_client):
        resp = admin_client.get("/rotation/requests")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["requests"] == []
        assert data["counts"] == {}

    def test_filtered_by_status(self, admin_client, admin_app):
        _make_pending(admin_app)
        r2 = _make_pending(admin_app, singer_name="Bea")
        admin_app.sing_store.mark_rejected(r2["id"])
        resp = admin_client.get("/rotation/requests?status=pending")
        data = resp.get_json()
        assert len(data["requests"]) == 1
        assert data["requests"][0]["singer_name"] == "Andrew"
        assert data["counts"]["pending"] == 1
        assert data["counts"]["rejected"] == 1


class TestConfig:
    def test_get_config_shape(self, admin_client):
        resp = admin_client.get("/rotation/requests/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "token" in data and data["token"]
        assert data["enabled"] is True
        assert data["auto_approve"] is False
        assert data["public_url"].startswith("http")
        assert data["pending_count"] == 0

    def test_regenerate_changes_token(self, admin_client, admin_app):
        original = admin_app.sing_store.get_token()
        resp = admin_client.post("/rotation/requests/config", json={"regenerate": True})
        assert resp.status_code == 200
        new_token = admin_app.sing_store.get_token()
        assert new_token != original

    def test_toggle_enabled(self, admin_client, admin_app):
        resp = admin_client.post("/rotation/requests/config", json={"enabled": False})
        assert resp.status_code == 200
        assert admin_app.sing_store.is_enabled() is False

    def test_toggle_auto_approve(self, admin_client, admin_app):
        resp = admin_client.post(
            "/rotation/requests/config", json={"auto_approve": True}
        )
        assert resp.status_code == 200
        assert admin_app.sing_store.is_auto_approve() is True


class TestQr:
    def test_qr_svg_returns_svg(self, admin_client):
        resp = admin_client.get("/rotation/requests/qr.svg")
        assert resp.status_code == 200
        assert resp.mimetype == "image/svg+xml"
        assert b"<svg" in resp.data

    def test_qr_invalid_scope(self, admin_client):
        resp = admin_client.get("/rotation/requests/qr.svg?scope=bogus")
        assert resp.status_code == 400


class TestApprove:
    def test_approve_local(self, admin_client, admin_app):
        req = _make_pending(admin_app, source_type="local", source_ref="/tmp/song.mp4")
        resp = admin_client.post(f"/rotation/requests/{req['id']}/approve")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["request"]["status"] == "approved"
        # A rotation entry exists and is linked
        entries = admin_app.rotation.get_rotation()
        assert any(e["singer"] == "Andrew" for e in entries)
        linked_id = data["request"]["linked_entry_id"]
        matching = [e for e in entries if e["id"] == linked_id]
        assert len(matching) == 1

    def test_approve_youtube_queues_download(self, admin_client, admin_app):
        req = _make_pending(
            admin_app,
            singer_name="Youtube Jane",
            source_type="youtube",
            source_ref="https://youtu.be/abc123",
        )
        with patch("routes._download_worker") as _w:
            resp = admin_client.post(f"/rotation/requests/{req['id']}/approve")
            assert resp.status_code == 200
        # Download queued
        items = admin_app.download_queue["items"]
        assert len(items) == 1
        assert items[0]["source"] == "youtube"
        assert items[0]["url"] == "https://youtu.be/abc123"
        # Rotation entry marked queued
        entry = admin_app.rotation.store.get_entry(items[0]["rotation_entry_id"])
        assert entry["download_status"] == "queued"

    def test_approve_divebar_queues_download(self, admin_client, admin_app):
        req = _make_pending(
            admin_app,
            singer_name="Divebar Dan",
            source_type="divebar",
            source_ref="gdrive_abc",
        )
        with patch("routes.divebar.get_download_url", return_value="https://dl/x.mp4"), \
             patch("routes._download_worker"):
            resp = admin_client.post(f"/rotation/requests/{req['id']}/approve")
        assert resp.status_code == 200
        items = admin_app.download_queue["items"]
        assert len(items) == 1
        assert items[0]["source"] == "divebar"
        assert items[0]["url"] == "https://dl/x.mp4"

    def test_approve_make_creates_gen_job(self, admin_client, admin_app):
        gen = MagicMock()
        gen.create_job.return_value = {"job_id": "job_42", "status": "pending"}
        admin_app.gen_client = gen
        req = _make_pending(
            admin_app,
            singer_name="Make Mary",
            source_type="make",
            source_ref=None,
            song_artist="Radiohead",
            song_title="Creep",
        )
        resp = admin_client.post(f"/rotation/requests/{req['id']}/approve")
        assert resp.status_code == 200
        gen.create_job.assert_called_once_with("Radiohead", "Creep")
        entry_id = resp.get_json()["entry_id"]
        entry = admin_app.rotation.store.get_entry(entry_id)
        assert entry["gen_job_id"] == "job_42"

    def test_approve_twice_conflicts(self, admin_client, admin_app):
        req = _make_pending(admin_app)
        admin_client.post(f"/rotation/requests/{req['id']}/approve")
        resp = admin_client.post(f"/rotation/requests/{req['id']}/approve")
        assert resp.status_code == 409

    def test_approve_unknown_404(self, admin_client):
        resp = admin_client.post("/rotation/requests/9999/approve")
        assert resp.status_code == 404


class TestEdit:
    def test_edit_applies_updates(self, admin_client, admin_app):
        req = _make_pending(admin_app, singer_name="Andrew")
        resp = admin_client.post(
            f"/rotation/requests/{req['id']}/edit",
            json={"singer_name": "Andy", "song_title": "We Will Rock You"},
        )
        assert resp.status_code == 200
        updated = resp.get_json()["request"]
        assert updated["singer_name"] == "Andy"
        assert updated["song_title"] == "We Will Rock You"

    def test_edit_unknown_404(self, admin_client):
        resp = admin_client.post(
            "/rotation/requests/9999/edit", json={"singer_name": "X"}
        )
        assert resp.status_code == 404


class TestReject:
    def test_reject_marks_request(self, admin_client, admin_app):
        req = _make_pending(admin_app)
        resp = admin_client.post(
            f"/rotation/requests/{req['id']}/reject",
            json={"reason": "duplicate"},
        )
        assert resp.status_code == 200
        req_after = admin_app.sing_store.get_request(req["id"])
        assert req_after["status"] == "rejected"
        assert req_after["rejected_reason"] == "duplicate"
        # No rotation entry
        entries = admin_app.rotation.get_rotation()
        assert not any(e["singer"] == "Andrew" for e in entries)

    def test_reject_unknown_404(self, admin_client):
        resp = admin_client.post("/rotation/requests/9999/reject", json={})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Integration hooks (archive regen, sleep disable)
# ---------------------------------------------------------------------------

class TestArchiveRegenHook:
    def test_archive_regenerates_token(self, admin_client, admin_app):
        original = admin_app.sing_store.get_token()
        resp = admin_client.post("/rotation/archive")
        assert resp.status_code == 200
        assert admin_app.sing_store.get_token() != original

    def test_archive_reenables_requests(self, admin_client, admin_app):
        admin_app.sing_store.set_enabled(False)
        resp = admin_client.post("/rotation/archive")
        assert resp.status_code == 200
        assert admin_app.sing_store.is_enabled() is True

    def test_archive_syncs_linked_overlay(self, admin_client, admin_app):
        # Create an overlay linked to the event URL
        overlay = admin_app.overlay_manager.create_overlay({
            "type": "qr_code", "name": "Event QR",
            "config": {"url": "stale", "follow_event_url": True},
        })
        admin_client.post("/rotation/archive")
        updated = admin_app.overlay_manager.get_overlay(overlay["id"])
        assert updated["config"]["url"] != "stale"
        assert admin_app.sing_store.get_token() in updated["config"]["url"]


class TestSleepDisableHook:
    def test_sleep_enter_disables_token(self, admin_client, admin_app):
        with patch.object(admin_app.sleep_manager, "enter_sleep", return_value={"active": True}):
            resp = admin_client.post("/system/sleep-mode", json={"active": True})
            assert resp.status_code == 200
        assert admin_app.sing_store.is_enabled() is False

    def test_sleep_exit_does_not_reenable_token(self, admin_client, admin_app):
        admin_app.sing_store.set_enabled(False)
        with patch.object(admin_app.sleep_manager, "exit_sleep", return_value={"active": False}):
            resp = admin_client.post("/system/sleep-mode", json={"active": False})
            assert resp.status_code == 200
        # Stays disabled — KJ must re-enable manually
        assert admin_app.sing_store.is_enabled() is False
