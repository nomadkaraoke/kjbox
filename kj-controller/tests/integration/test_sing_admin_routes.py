"""Integration tests for the admin /rotation/requests/* endpoints and token hooks."""

import json
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

    def test_youtube_request_exposes_url_for_kj_preview(self, admin_client, admin_app):
        # The KJ UI shows a clickable YouTube link so the KJ can verify the
        # pasted URL is an actual karaoke version before approving. That
        # depends on source_ref (the URL the singer pasted) being present in
        # the admin response — guard against future projections stripping it.
        _make_pending(
            admin_app,
            source_type="youtube",
            source_ref="https://youtu.be/dQw4w9WgXcQ",
        )
        resp = admin_client.get("/rotation/requests?status=pending")
        assert resp.status_code == 200
        req = resp.get_json()["requests"][0]
        assert req["source_type"] == "youtube"
        assert req["source_ref"] == "https://youtu.be/dQw4w9WgXcQ"


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

    def test_set_custom_token(self, admin_client, admin_app):
        resp = admin_client.post(
            "/rotation/requests/config", json={"token": "2121"}
        )
        assert resp.status_code == 200
        assert resp.get_json()["changed"]["token"] == "2121"
        assert admin_app.sing_store.get_token() == "2121"

    def test_set_custom_token_validates_format(self, admin_client, admin_app):
        original = admin_app.sing_store.get_token()
        resp = admin_client.post(
            "/rotation/requests/config", json={"token": "abc"}
        )
        assert resp.status_code == 400
        assert admin_app.sing_store.get_token() == original

    def test_set_custom_token_syncs_overlay(self, admin_client, admin_app):
        overlay = admin_app.overlay_manager.create_overlay({
            "type": "qr_code", "name": "Event QR",
            "config": {"url": "stale", "follow_event_url": True},
        })
        admin_client.post("/rotation/requests/config", json={"token": "2121"})
        updated = admin_app.overlay_manager.get_overlay(overlay["id"])
        assert "2121" in updated["config"]["url"]

    def test_regenerate_takes_precedence_over_token(self, admin_client, admin_app):
        # If both fields are sent, regenerate wins — the explicit-set path
        # is for pinning a chosen value, the random regen is the safer default.
        resp = admin_client.post(
            "/rotation/requests/config",
            json={"regenerate": True, "token": "2121"},
        )
        assert resp.status_code == 200
        assert admin_app.sing_store.get_token() != "2121"

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

    def test_get_config_exposes_accept_make_requests(self, admin_client):
        resp = admin_client.get("/rotation/requests/config")
        data = resp.get_json()
        assert data["accept_make_requests"] is True  # default on

    def test_get_config_exposes_simple_mode(self, admin_client):
        """The admin config endpoint surfaces the simple_mode flag (default off)."""
        resp = admin_client.get("/rotation/requests/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "simple_mode" in data
        assert data["simple_mode"] is False  # default off

    def test_toggle_accept_make_requests_off(self, admin_client, admin_app):
        resp = admin_client.post(
            "/rotation/requests/config",
            json={"accept_make_requests": False},
        )
        assert resp.status_code == 200
        assert admin_app.sing_store.is_accepting_make_requests() is False
        # Reflects in the GET response too.
        resp2 = admin_client.get("/rotation/requests/config")
        assert resp2.get_json()["accept_make_requests"] is False

    def test_partial_post_does_not_touch_other_flags(
        self, admin_client, admin_app,
    ):
        """Toggling accept_make_requests must not reset auto_approve."""
        admin_app.sing_store.set_auto_approve(True)
        admin_client.post(
            "/rotation/requests/config", json={"accept_make_requests": False}
        )
        assert admin_app.sing_store.is_auto_approve() is True

    def test_toggle_simple_mode_on(self, admin_client, admin_app):
        resp = admin_client.post(
            "/rotation/requests/config",
            json={"simple_mode": True},
        )
        assert resp.status_code == 200
        assert resp.get_json()["changed"]["simple_mode"] is True
        # Confirm via GET
        resp2 = admin_client.get("/rotation/requests/config")
        assert resp2.get_json()["simple_mode"] is True

    def test_toggle_simple_mode_isolated_from_other_flags(
        self, admin_client, admin_app,
    ):
        """Toggling simple_mode must not reset auto_approve or
        accept_make_requests."""
        admin_app.sing_store.set_auto_approve(True)
        admin_app.sing_store.set_accepting_make_requests(False)
        resp = admin_client.post(
            "/rotation/requests/config", json={"simple_mode": True},
        )
        assert resp.status_code == 200
        cfg = admin_client.get("/rotation/requests/config").get_json()
        assert cfg["simple_mode"] is True
        assert cfg["auto_approve"] is True
        assert cfg["accept_make_requests"] is False


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

    def test_approve_youtube_skip_download_creates_unlinked_entry(self, admin_client, admin_app):
        # KJ watched the pasted URL, decided it's not actually a karaoke
        # version, but wants to keep the singer in rotation and link a
        # proper file manually. skip_download bypasses the download queue
        # and leaves the entry without a file_path, so the rotation 🔗 link
        # button appears.
        req = _make_pending(
            admin_app,
            singer_name="Wrong Url Wendy",
            source_type="youtube",
            source_ref="https://youtu.be/notKaraoke",
        )
        with patch("routes._download_worker") as _w:
            resp = admin_client.post(
                f"/rotation/requests/{req['id']}/approve",
                json={"skip_download": True},
            )
            assert resp.status_code == 200
            _w.assert_not_called()
        assert admin_app.download_queue["items"] == []
        entry_id = resp.get_json()["entry_id"]
        entry = admin_app.rotation.store.get_entry(entry_id)
        assert entry["file_path"] is None
        assert entry["download_status"] is None

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

    def test_approve_divebar_builds_filename_from_song_metadata(
        self, admin_client, admin_app
    ):
        # The sing-request approval used to hardcode `divebar-{file_id}.mp4`
        # for the queue title, throwing away the song_artist/song_title that
        # are right there on the request. This is the regression guard.
        req = _make_pending(
            admin_app,
            singer_name="Divebar Dan",
            song_artist="Queen",
            song_title="Bohemian Rhapsody",
            source_type="divebar",
            source_ref="gdrive_abc",
        )
        with patch("routes.divebar.get_download_url", return_value="https://dl/x.mp4"), \
             patch("routes._download_worker"):
            resp = admin_client.post(f"/rotation/requests/{req['id']}/approve")
        assert resp.status_code == 200
        items = admin_app.download_queue["items"]
        assert items[-1]["title"] == "DB - Queen - Bohemian Rhapsody.mp4"

    def test_approve_divebar_uses_brand_code_from_source_meta(
        self, admin_client, admin_app
    ):
        # kj_pick path writes brand_code into source_meta; the approval
        # path should honour it so we get 'WTF - Queen - ...' not 'DB - ...'.
        req = _make_pending(
            admin_app,
            singer_name="Pick Pete",
            song_artist="Queen",
            song_title="We Will Rock You",
            source_type="divebar",
            source_ref="gdrive_xyz",
            source_meta={"brand_code": "WTF"},
        )
        with patch("routes.divebar.get_download_url", return_value="https://dl/y.mp4"), \
             patch("routes._download_worker"):
            resp = admin_client.post(f"/rotation/requests/{req['id']}/approve")
        assert resp.status_code == 200
        items = admin_app.download_queue["items"]
        assert items[-1]["title"] == "WTF - Queen - We Will Rock You.mp4"

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

    def test_approve_local_with_duet_partners(self, admin_client, admin_app):
        req = _make_pending(
            admin_app,
            singer_name="Alice",
            source_type="local",
            source_ref="/tmp/song.mp4",
            additional_singers=[
                {"name": "Sarah B.", "phone": "+61 400 111 222"},
                {"name": "Mike", "phone": ""},
            ],
        )
        resp = admin_client.post(f"/rotation/requests/{req['id']}/approve")
        assert resp.status_code == 200
        linked_id = resp.get_json()["request"]["linked_entry_id"]
        entry = admin_app.rotation.store.get_entry(linked_id)
        # Joined singer string for legacy text column.
        assert entry["singer"] == "Alice & Sarah B. & Mike"
        # Structured list survives in singers_json.
        names = json.loads(entry["singers_json"])
        assert names == ["Alice", "Sarah B.", "Mike"]

    def test_approve_solo_unchanged(self, admin_client, admin_app):
        """Solo approvals must still produce a single-singer entry (no singers_json)."""
        req = _make_pending(
            admin_app, singer_name="SoloAndrew",
            source_type="local", source_ref="/tmp/x.mp4",
        )
        resp = admin_client.post(f"/rotation/requests/{req['id']}/approve")
        assert resp.status_code == 200
        linked_id = resp.get_json()["request"]["linked_entry_id"]
        entry = admin_app.rotation.store.get_entry(linked_id)
        assert entry["singer"] == "SoloAndrew"
        assert entry["singers_json"] is None

    def test_approve_partners_all_blank_names_is_solo(self, admin_client, admin_app):
        """Defensive: if partners list contains only blank names (bypassing
        /submit validation via direct store write), approve still produces
        a solo entry — singers_json stays NULL."""
        req = _make_pending(
            admin_app,
            singer_name="Alice",
            source_type="local",
            source_ref="/tmp/x.mp4",
            additional_singers=[{"name": "", "phone": ""}],
        )
        resp = admin_client.post(f"/rotation/requests/{req['id']}/approve")
        assert resp.status_code == 200
        linked_id = resp.get_json()["request"]["linked_entry_id"]
        entry = admin_app.rotation.store.get_entry(linked_id)
        assert entry["singer"] == "Alice"
        assert entry["singers_json"] is None

    def test_approve_youtube_with_duet_partners(self, admin_client, admin_app):
        """Branch coverage: the YouTube/divebar/kn branch passes singers= too.
        A kwarg drift between source-type branches would silently regress duets
        on non-local sources without this test."""
        req = _make_pending(
            admin_app,
            singer_name="Alice",
            source_type="youtube",
            source_ref="https://youtu.be/abc123",
            additional_singers=[{"name": "Sarah B.", "phone": "+61 400 111 222"}],
        )
        with patch("routes._download_worker"):
            resp = admin_client.post(f"/rotation/requests/{req['id']}/approve")
        assert resp.status_code == 200
        linked_id = resp.get_json()["request"]["linked_entry_id"]
        entry = admin_app.rotation.store.get_entry(linked_id)
        assert entry["singer"] == "Alice & Sarah B."
        names = json.loads(entry["singers_json"])
        assert names == ["Alice", "Sarah B."]
        # Sanity: download was still queued.
        assert any(
            item["rotation_entry_id"] == linked_id
            for item in admin_app.download_queue["items"]
        )


def _make_kj_pick_pending(app, versions):
    """Insert a pending kj_pick request carrying the given versions snapshot."""
    return app.sing_store.create_request(
        singer_name="KJPickSinger",
        phone="+1 555 9999",
        song_artist="Queen",
        song_title="Bohemian Rhapsody",
        source_type="kj_pick",
        source_ref=None,
        source_meta={"versions": versions},
    )


class TestApproveKjPick:
    def test_approve_with_local_version(self, admin_client, admin_app):
        req = _make_kj_pick_pending(admin_app, [
            {"source": "local", "local": {"path": "/media/queen.mp4"}},
            {"source": "kn", "kn": {"youtube_url": "https://yt/x"}},
        ])
        resp = admin_client.post(
            f"/rotation/requests/{req['id']}/approve",
            json={"version_index": 0},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        # Row was rewritten to reflect the chosen version — audit trail shows
        # what actually played, not the ambiguous kj_pick placeholder.
        assert data["request"]["source_type"] == "local"
        assert data["request"]["source_ref"] == "/media/queen.mp4"
        # Rotation entry exists and is linked.
        entries = admin_app.rotation.get_rotation()
        assert any(e["singer"] == "KJPickSinger" for e in entries)

    def test_approve_with_youtube_version_queues_download(
        self, admin_client, admin_app,
    ):
        req = _make_kj_pick_pending(admin_app, [
            {"source": "local", "local": {"path": "/media/queen.mp4"}},
            {"source": "kn", "kn": {
                "brand_code": "KV", "youtube_url": "https://yt/abc",
            }},
        ])
        with patch("routes._download_worker"):
            resp = admin_client.post(
                f"/rotation/requests/{req['id']}/approve",
                json={"version_index": 1},
            )
        assert resp.status_code == 200
        # Stored as youtube (not kj_pick) so the download worker + history
        # see a concrete source type.
        stored = admin_app.sing_store.get_request(req["id"])
        assert stored["source_type"] == "youtube"
        assert stored["source_ref"] == "https://yt/abc"
        items = admin_app.download_queue["items"]
        assert any(it["url"] == "https://yt/abc" for it in items)

    def test_approve_with_divebar_version_queues_download(
        self, admin_client, admin_app,
    ):
        req = _make_kj_pick_pending(admin_app, [
            {"source": "kn", "kn": {
                "brand_code": "SF",
                "divebar": {"file_id": "drive-xyz", "drive_path": "/SF/q.mp4"},
                "youtube_url": "https://yt/backup",
            }},
        ])
        with patch(
            "routes.divebar.get_download_url",
            return_value="https://dl/q.mp4",
        ), patch("routes._download_worker"):
            resp = admin_client.post(
                f"/rotation/requests/{req['id']}/approve",
                json={"version_index": 0},
            )
        assert resp.status_code == 200
        items = admin_app.download_queue["items"]
        assert items[-1]["source"] == "divebar"
        assert items[-1]["url"] == "https://dl/q.mp4"

    def test_approve_without_version_index_400(self, admin_client, admin_app):
        req = _make_kj_pick_pending(admin_app, [
            {"source": "local", "local": {"path": "/x.mp4"}},
        ])
        resp = admin_client.post(f"/rotation/requests/{req['id']}/approve")
        assert resp.status_code == 400
        assert "version_index" in resp.get_json()["error"]
        # Row stays pending so the admin can retry with an index.
        assert admin_app.sing_store.get_request(req["id"])["status"] == "pending"

    def test_approve_with_out_of_range_index_400(self, admin_client, admin_app):
        req = _make_kj_pick_pending(admin_app, [
            {"source": "local", "local": {"path": "/x.mp4"}},
        ])
        resp = admin_client.post(
            f"/rotation/requests/{req['id']}/approve",
            json={"version_index": 5},
        )
        assert resp.status_code == 400
        assert admin_app.sing_store.get_request(req["id"])["status"] == "pending"

    def test_version_index_ignored_for_non_kj_pick(self, admin_client, admin_app):
        """Backwards-compat — existing Approve button on non-kj_pick requests
        keeps working even if a stray version_index lands in the body."""
        req = _make_pending(admin_app)  # source_type=local, no source_meta
        resp = admin_client.post(
            f"/rotation/requests/{req['id']}/approve",
            json={"version_index": 3},
        )
        assert resp.status_code == 200


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
    def test_archive_preserves_token(self, admin_client, admin_app):
        # Regression guard for the surprise-changing-token bug: KJs print a QR
        # for the night and expect it to keep working across archive cycles.
        # A fresh code is opt-in via the modal's Regenerate / Set buttons.
        original = admin_app.sing_store.get_token()
        resp = admin_client.post("/rotation/archive")
        assert resp.status_code == 200
        assert admin_app.sing_store.get_token() == original

    def test_archive_reenables_requests(self, admin_client, admin_app):
        admin_app.sing_store.set_enabled(False)
        resp = admin_client.post("/rotation/archive")
        assert resp.status_code == 200
        assert admin_app.sing_store.is_enabled() is True


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


class TestPushHooks:
    def test_approve_triggers_push_notification(self, client, sing_app, token):
        from unittest.mock import MagicMock
        # Mock the dispatcher — the real one is wired but we want to inspect calls
        dispatcher = MagicMock()
        sing_app.rotation.push_dispatcher = dispatcher

        gen = MagicMock()
        gen.create_job.return_value = {"job_id": "job_push_1", "status": "pending"}
        sing_app.gen_client = gen

        sing_app.sing_store.set_enabled(True)
        # Submit a request via the public route
        resp = client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Bob",
            "phone": "+61400000099",
            "song_artist": "Queen",
            "song_title": "Radio Ga Ga",
            "source_type": "make",
        })
        assert resp.status_code == 200
        req_id = resp.get_json()["request"]["id"]

        # Approve via admin route
        resp = client.post(f"/rotation/requests/{req_id}/approve")
        assert resp.status_code == 200, resp.get_data(as_text=True)

        dispatcher.notify_request_decision.assert_called_once()
        args, _ = dispatcher.notify_request_decision.call_args
        # Signature: (request_id, decision, request_dict)
        assert args[0] == req_id
        assert args[1] == "approved"
        assert args[2]["phone"] == "+61400000099"

    def test_reject_triggers_push_notification(self, client, sing_app, token):
        from unittest.mock import MagicMock
        dispatcher = MagicMock()
        sing_app.rotation.push_dispatcher = dispatcher

        sing_app.sing_store.set_enabled(True)
        resp = client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Carol",
            "phone": "+61400000088",
            "song_artist": "Beatles",
            "song_title": "Yesterday",
            "source_type": "make",
        })
        req_id = resp.get_json()["request"]["id"]

        resp = client.post(f"/rotation/requests/{req_id}/reject",
                           json={"reason": "song unavailable"})
        assert resp.status_code == 200

        dispatcher.notify_request_decision.assert_called_once()
        args, _ = dispatcher.notify_request_decision.call_args
        assert args[0] == req_id
        assert args[1] == "rejected"
        assert args[2]["phone"] == "+61400000088"

    def test_token_regenerate_triggers_cleanup(self, client, sing_app, token):
        from unittest.mock import patch
        # Seed a sub so we can observe cleanup behaviour
        sing_app.sing_store.insert_push_subscription(
            token=token, phone="+1", singer_name="X",
            endpoint="ep", p256dh="p", auth="a",
        )
        with patch.object(
            sing_app.sing_store, "cleanup_stale_push_subscriptions",
            wraps=sing_app.sing_store.cleanup_stale_push_subscriptions,
        ) as mock_cleanup:
            resp = client.post("/rotation/requests/config",
                               json={"regenerate": True})
            assert resp.status_code == 200
            mock_cleanup.assert_called_once()
            args, kwargs = mock_cleanup.call_args
            # Current token is the freshly-regenerated one
            new_token = resp.get_json()["changed"]["token"]
            # cleanup was called with the new token (either positional or kwarg)
            call_token = kwargs.get("current_token") or (args[0] if args else None)
            assert call_token == new_token

    def test_push_hook_failure_does_not_fail_approve(self, client, sing_app, token):
        """A crashing dispatcher must not break the approve admin route."""
        from unittest.mock import MagicMock
        dispatcher = MagicMock()
        dispatcher.notify_request_decision.side_effect = RuntimeError("boom")
        sing_app.rotation.push_dispatcher = dispatcher

        gen = MagicMock()
        gen.create_job.return_value = {"job_id": "job_push_2", "status": "pending"}
        sing_app.gen_client = gen

        sing_app.sing_store.set_enabled(True)
        resp = client.post(f"/sing/submit?t={token}", json={
            "singer_name": "Dan",
            "phone": "+61400000077",
            "song_artist": "Pink Floyd",
            "song_title": "Money",
            "source_type": "make",
        })
        req_id = resp.get_json()["request"]["id"]
        resp = client.post(f"/rotation/requests/{req_id}/approve")
        # Approve still succeeds despite dispatcher exception
        assert resp.status_code == 200


class TestListRequestsExposesPartners:
    def test_list_includes_additional_singers(self, admin_client, admin_app):
        admin_app.sing_store.create_request(
            singer_name="Alice", phone="", source_type="local",
            source_ref="/tmp/x.mp4",
            additional_singers=[{"name": "Sarah", "phone": "+61 400 111 222"}],
        )
        resp = admin_client.get("/rotation/requests?status=pending")
        assert resp.status_code == 200
        rows = resp.get_json()["requests"]
        assert any(
            r.get("additional_singers") == [{"name": "Sarah", "phone": "+61 400 111 222"}]
            for r in rows
        )
