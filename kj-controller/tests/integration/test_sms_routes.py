"""Integration tests for /rotation/sms/* + SMS fields on /rotation/requests/config."""

from unittest.mock import MagicMock, patch

import pytest

import sms as sms_mod
from app import create_app


@pytest.fixture
def sms_app(mock_config):
    """App with rotation + sing_store + sms_store ready, Telnyx env mocked."""
    with patch.dict(
        "os.environ",
        {"TELNYX_API_KEY": "test_key", "TELNYX_FROM_NUMBER": "+18005551234"},
        clear=False,
    ):
        app = create_app(config=mock_config)
        app.config["TESTING"] = True
        yield app
        app.catalog.close()


@pytest.fixture
def sms_client(sms_app):
    with sms_app.test_client() as c:
        yield c


@pytest.fixture
def unconfigured_app(mock_config):
    """App where Telnyx env vars are missing — sms_enabled must be False."""
    import os
    original_api = os.environ.pop("TELNYX_API_KEY", None)
    original_from = os.environ.pop("TELNYX_FROM_NUMBER", None)
    try:
        app = create_app(config=mock_config)
        app.config["TESTING"] = True
        yield app
        app.catalog.close()
    finally:
        if original_api is not None:
            os.environ["TELNYX_API_KEY"] = original_api
        if original_from is not None:
            os.environ["TELNYX_FROM_NUMBER"] = original_from


def _seed_request_and_link(app, *, phone="843-259-4507", singer="Celeste",
                            song_title="Plump", song_artist="Hole"):
    """Create a sing_request, approve it (creates rotation entry), return ids."""
    req = app.sing_store.create_request(
        singer_name=singer,
        phone=phone,
        song_artist=song_artist,
        song_title=song_title,
        source_type="local",
        source_ref="/x.mp4",
    )
    # Link to a rotation entry directly (skip the approve route's KN/divebar
    # plumbing — we just need the linked_entry_id for the SMS resolver).
    entry = app.rotation.add_entry(singer, f"{song_title} - {song_artist}")
    app.sing_store.mark_approved(req["id"], linked_entry_id=entry["id"])
    return req["id"], entry["id"]


# ---------------------------------------------------------------------------
# /rotation/requests/config — new SMS fields
# ---------------------------------------------------------------------------

class TestConfigSmsFields:
    def test_get_exposes_sms_enabled_true(self, sms_client):
        resp = sms_client.get("/rotation/requests/config")
        data = resp.get_json()
        assert data["sms_enabled"] is True
        assert data["sms_template"] == sms_mod.DEFAULT_TEMPLATE
        assert data["sms_template_is_custom"] is False
        assert data["sms_default_region"] == "US"
        assert data["sms_from_number"] == "+18005551234"

    def test_get_exposes_sms_enabled_false_when_env_missing(self, unconfigured_app):
        with unconfigured_app.test_client() as c:
            resp = c.get("/rotation/requests/config")
            data = resp.get_json()
            assert data["sms_enabled"] is False
            assert data["sms_from_number"] is None

    def test_set_custom_template(self, sms_client, sms_app):
        resp = sms_client.post(
            "/rotation/requests/config",
            json={"sms_template": "Hey {first_name}, you're up!"},
        )
        assert resp.status_code == 200
        assert sms_app.sing_store.get_sms_template() == "Hey {first_name}, you're up!"
        resp2 = sms_client.get("/rotation/requests/config")
        assert resp2.get_json()["sms_template_is_custom"] is True

    def test_reset_template_to_default(self, sms_client, sms_app):
        sms_app.sing_store.set_sms_template("custom")
        resp = sms_client.post(
            "/rotation/requests/config", json={"sms_template": None},
        )
        assert resp.status_code == 200
        assert sms_app.sing_store.get_sms_template() is None

    def test_empty_template_rejected(self, sms_client):
        resp = sms_client.post(
            "/rotation/requests/config", json={"sms_template": "   "},
        )
        assert resp.status_code == 400

    def test_set_default_region(self, sms_client, sms_app):
        resp = sms_client.post(
            "/rotation/requests/config", json={"sms_default_region": "AU"},
        )
        assert resp.status_code == 200
        assert sms_app.sing_store.get_sms_default_region() == "AU"

    def test_set_invalid_region_rejected(self, sms_client):
        resp = sms_client.post(
            "/rotation/requests/config", json={"sms_default_region": "USA"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /rotation response — sms block per entry
# ---------------------------------------------------------------------------

class TestRotationSmsBlock:
    def test_unlinked_entry_marked_unavailable(self, sms_client, sms_app):
        # KJ-added entry with no sing_request linkage.
        sms_app.rotation.add_entry("Andrew (KJ)", "Some Song")
        resp = sms_client.get("/rotation")
        entries = resp.get_json()["entries"]
        assert all(e["sms"]["available"] is False for e in entries)
        assert all(e["sms"]["last_sent_at"] is None for e in entries)

    def test_linked_entry_marked_available(self, sms_client, sms_app):
        _seed_request_and_link(sms_app)
        resp = sms_client.get("/rotation")
        entries = resp.get_json()["entries"]
        celeste = [e for e in entries if e["singer"] == "Celeste"][0]
        assert celeste["sms"]["available"] is True
        assert celeste["sms"]["last_sent_at"] is None

    def test_newest_request_wins_when_multiple_link_to_same_entry(self, sms_client, sms_app):
        """Regression: a rotation_entry_id pointed at by multiple sing_requests
        must use the NEWEST request's phone for the button visibility — mirroring
        what /sms/send does — so the KJ never sees a flickering button or one
        that would 400 on click.

        2026-05-28 outage: the listing query had no ORDER BY and let SQLite pick
        whichever order it felt like, so a row with several requests (one with a
        phone, one without) flickered visible/hidden between polls.
        """
        _, entry_id = _seed_request_and_link(
            sms_app, phone="843-259-4507", singer="Celeste",
        )
        # Newer request linked to the same entry, no phone (e.g. KJ re-approved
        # the request after the singer opted out by editing the row).
        req2 = sms_app.sing_store.create_request(
            singer_name="Celeste", phone="", source_type="local",
            source_ref="/x.mp4", song_title="Plump", song_artist="Hole",
        )
        sms_app.sing_store.mark_approved(req2["id"], linked_entry_id=entry_id)

        resp = sms_client.get("/rotation")
        celeste = [e for e in resp.get_json()["entries"] if e["id"] == entry_id][0]
        # Newest request has no phone → button must be hidden, deterministically.
        assert celeste["sms"]["available"] is False

    def test_newest_request_with_phone_keeps_button_visible(self, sms_client, sms_app):
        """The flip side of the above: an OLDER no-phone request must not
        suppress the button when the newest request DOES have a phone."""
        # First (oldest) request: no phone.
        first = sms_app.sing_store.create_request(
            singer_name="Celeste", phone="", source_type="local",
            source_ref="/x.mp4", song_title="Plump", song_artist="Hole",
        )
        entry = sms_app.rotation.add_entry("Celeste", "Plump - Hole")
        sms_app.sing_store.mark_approved(first["id"], linked_entry_id=entry["id"])
        # Newer request: same entry, has a phone.
        newer = sms_app.sing_store.create_request(
            singer_name="Celeste", phone="843-259-4507", source_type="local",
            source_ref="/x.mp4", song_title="Plump", song_artist="Hole",
        )
        sms_app.sing_store.mark_approved(newer["id"], linked_entry_id=entry["id"])

        resp = sms_client.get("/rotation")
        celeste = [e for e in resp.get_json()["entries"] if e["id"] == entry["id"]][0]
        assert celeste["sms"]["available"] is True

    def test_button_hidden_when_telnyx_unconfigured(self, unconfigured_app):
        # Regression: even a linked entry with a real phone must report
        # sms.available=False if Telnyx env vars aren't set, so the KJ UI
        # doesn't render a button that would 503 on click.
        _seed_request_and_link(unconfigured_app)
        with unconfigured_app.test_client() as c:
            resp = c.get("/rotation")
            entries = resp.get_json()["entries"]
            assert all(e["sms"]["available"] is False for e in entries)

    def test_send_reflects_on_next_fetch(self, sms_client, sms_app):
        _, entry_id = _seed_request_and_link(sms_app)
        sms_app.sms_store.record_send(
            rotation_entry_id=entry_id, sing_request_id=None,
            phone_e164="+18432594507", body="hi", status="sent",
            telnyx_message_id="m1",
        )
        resp = sms_client.get("/rotation")
        celeste = [e for e in resp.get_json()["entries"] if e["id"] == entry_id][0]
        assert celeste["sms"]["last_status"] == "sent"
        assert celeste["sms"]["last_sent_at"]


# ---------------------------------------------------------------------------
# /rotation/sms/preview
# ---------------------------------------------------------------------------

class TestPreview:
    def test_happy_path(self, sms_client, sms_app):
        _, entry_id = _seed_request_and_link(sms_app)
        resp = sms_client.post("/rotation/sms/preview", json={"entry_id": entry_id})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["phone_e164"] == "+18432594507"
        assert data["first_name"] == "Celeste"
        assert data["song"] == "Plump"
        assert data["artist"] == "Hole"
        assert "Celeste" in data["body"]
        assert "Plump" in data["body"]
        assert "Reply STOP" in data["body"]
        assert data["length"] == len(data["body"])
        assert data["segments"] >= 1

    def test_missing_entry_id(self, sms_client):
        resp = sms_client.post("/rotation/sms/preview", json={})
        assert resp.status_code == 400

    def test_unknown_entry_id_404(self, sms_client):
        resp = sms_client.post("/rotation/sms/preview", json={"entry_id": 99999})
        assert resp.status_code == 404

    def test_unlinked_entry_400(self, sms_client, sms_app):
        # Rotation entry not linked to a sing_request → no phone available.
        entry = sms_app.rotation.add_entry("KJ Walkup", "Some Song")
        resp = sms_client.post("/rotation/sms/preview", json={"entry_id": entry["id"]})
        assert resp.status_code == 400

    def test_invalid_phone_400(self, sms_client, sms_app):
        # Seed a request with a malformed phone bypassing the public-form
        # validation (legacy data).
        _seed_request_and_link(sms_app, phone="garbage")
        entry_id = sms_app.rotation.get_rotation()[-1]["id"]
        resp = sms_client.post("/rotation/sms/preview", json={"entry_id": entry_id})
        assert resp.status_code == 400

    def test_unconfigured_503(self, unconfigured_app):
        _, entry_id = _seed_request_and_link(unconfigured_app)
        with unconfigured_app.test_client() as c:
            resp = c.post("/rotation/sms/preview", json={"entry_id": entry_id})
            assert resp.status_code == 503

    def test_uses_custom_template(self, sms_client, sms_app):
        _, entry_id = _seed_request_and_link(sms_app)
        sms_app.sing_store.set_sms_template("Yo {first_name}!")
        resp = sms_client.post("/rotation/sms/preview", json={"entry_id": entry_id})
        assert resp.get_json()["body"] == "Yo Celeste!"


# ---------------------------------------------------------------------------
# /rotation/sms/send
# ---------------------------------------------------------------------------

class TestSend:
    @patch("sms.requests.post")
    def test_happy_path(self, mock_post, sms_client, sms_app):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": {"id": "msg_xyz"}},
        )
        _, entry_id = _seed_request_and_link(sms_app)
        resp = sms_client.post("/rotation/sms/send", json={
            "entry_id": entry_id, "body": "Hi Celeste! Up next.",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["telnyx_message_id"] == "msg_xyz"
        assert data["sms_log_id"] > 0
        # Telnyx call shape is correct.
        sent_kwargs = mock_post.call_args.kwargs
        assert sent_kwargs["json"]["to"] == "+18432594507"
        assert sent_kwargs["json"]["from"] == "+18005551234"
        assert sent_kwargs["json"]["text"] == "Hi Celeste! Up next."
        # Logged.
        log = sms_app.sms_store.get_log(data["sms_log_id"])
        assert log["status"] == "sent"
        assert log["rotation_entry_id"] == entry_id

    @patch("sms.requests.post")
    def test_telnyx_failure_logged_and_502(self, mock_post, sms_client, sms_app):
        mock_post.return_value = MagicMock(
            status_code=400,
            text="...",
            json=lambda: {"errors": [{"title": "Invalid", "detail": "bad number"}]},
        )
        _, entry_id = _seed_request_and_link(sms_app)
        resp = sms_client.post("/rotation/sms/send", json={
            "entry_id": entry_id, "body": "Hi Celeste!",
        })
        assert resp.status_code == 502
        data = resp.get_json()
        assert data["success"] is False
        assert "bad number" in data["error"]
        log = sms_app.sms_store.get_log(data["sms_log_id"])
        assert log["status"] == "failed"
        assert log["error"]

    def test_missing_body_400(self, sms_client, sms_app):
        _, entry_id = _seed_request_and_link(sms_app)
        resp = sms_client.post("/rotation/sms/send", json={"entry_id": entry_id})
        assert resp.status_code == 400

    def test_body_too_long_400(self, sms_client, sms_app):
        _, entry_id = _seed_request_and_link(sms_app)
        resp = sms_client.post("/rotation/sms/send", json={
            "entry_id": entry_id, "body": "x" * (sms_mod.MAX_BODY_LEN + 1),
        })
        assert resp.status_code == 400

    def test_unknown_entry_404(self, sms_client):
        resp = sms_client.post("/rotation/sms/send", json={
            "entry_id": 99999, "body": "x",
        })
        assert resp.status_code == 404

    def test_unconfigured_503(self, unconfigured_app):
        _, entry_id = _seed_request_and_link(unconfigured_app)
        with unconfigured_app.test_client() as c:
            resp = c.post("/rotation/sms/send", json={
                "entry_id": entry_id, "body": "x",
            })
            assert resp.status_code == 503
