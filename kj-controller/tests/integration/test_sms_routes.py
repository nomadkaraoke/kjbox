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
    entry = app.rotation.add_entry(singer, f"{song_artist} - {song_title}")
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

    def test_unlinked_entry_still_marked_configured(self, sms_client, sms_app):
        # configured=True even with no phone, so the frontend shows a disabled
        # (greyed) SMS button and the action row keeps a constant width.
        sms_app.rotation.add_entry("Andrew (KJ)", "Some Song")
        resp = sms_client.get("/rotation")
        entries = resp.get_json()["entries"]
        assert all(e["sms"]["configured"] is True for e in entries)

    def test_button_hidden_entirely_when_unconfigured(self, unconfigured_app):
        # configured=False when Telnyx env vars are missing, so the frontend
        # omits the SMS button rather than showing a permanently-disabled one.
        _seed_request_and_link(unconfigured_app)
        with unconfigured_app.test_client() as c:
            entries = c.get("/rotation").get_json()["entries"]
            assert all(e["sms"]["configured"] is False for e in entries)

    def test_linked_entry_marked_available(self, sms_client, sms_app):
        _seed_request_and_link(sms_app)
        resp = sms_client.get("/rotation")
        entries = resp.get_json()["entries"]
        celeste = [e for e in entries if e["singer"] == "Celeste"][0]
        assert celeste["sms"]["available"] is True
        assert celeste["sms"]["configured"] is True
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

    def test_dlr_failure_surfaces_status_and_error(self, sms_client, sms_app):
        """After a delivery receipt marks a send failed, the row's sms block
        must report last_status='delivery_failed' AND last_error so the KJ sees
        WHY it bounced.

        Regression (2026-07-02): a carrier hard-reject (40010 "not 10DLC
        registered") arrives via the DLR webhook as status 'delivery_failed',
        but the frontend only recognised the send-time 'failed' string and the
        error text never left the backend — so the row rendered a plain neutral
        "sent" marker and the failure was invisible for ~2 weeks.
        """
        _, entry_id = _seed_request_and_link(sms_app)
        sms_app.sms_store.record_send(
            rotation_entry_id=entry_id, sing_request_id=None,
            phone_e164="+18432594507", body="hi", status="sent",
            telnyx_message_id="m1",
        )
        sms_app.sms_store.update_status_by_telnyx_id(
            "m1", "delivery_failed",
            error="40010; Not 10DLC registered; The sending number is not "
                  "10DLC-registered but is required to be by the carrier.",
        )
        resp = sms_client.get("/rotation")
        row = [e for e in resp.get_json()["entries"] if e["id"] == entry_id][0]
        assert row["sms"]["last_status"] == "delivery_failed"
        assert "40010" in (row["sms"]["last_error"] or "")

    def test_delivered_status_surfaces_with_no_error(self, sms_client, sms_app):
        """A successful DLR reports last_status='delivered' and last_error=None."""
        _, entry_id = _seed_request_and_link(sms_app)
        sms_app.sms_store.record_send(
            rotation_entry_id=entry_id, sing_request_id=None,
            phone_e164="+18432594507", body="hi", status="sent",
            telnyx_message_id="m2",
        )
        sms_app.sms_store.update_status_by_telnyx_id("m2", "delivered", error=None)
        resp = sms_client.get("/rotation")
        row = [e for e in resp.get_json()["entries"] if e["id"] == entry_id][0]
        assert row["sms"]["last_status"] == "delivered"
        assert row["sms"]["last_error"] is None


class TestMutationResponsesIncludeSmsBlock:
    """Every rotation endpoint that returns ``entries`` must attach the ``sms``
    block, not just the ``/rotation`` poll.

    Regression (2026-06-11): the mutation endpoints (status change, edit, move,
    delete, set-paid, link/unlink, restore, …) decorated entries with time
    estimates and songs-sung but skipped ``_add_sms_status``. The frontend sets
    ``rotationData = data.entries`` from those responses and re-renders, so the
    whole SMS button column vanished after every action and only reappeared on
    the next 2-second ``/rotation`` poll — an unpredictable appear/disappear
    that made it a coin-flip which button the KJ would actually click.
    """

    def _entries(self, resp):
        data = resp.get_json()
        assert resp.status_code == 200, data
        return data["entries"]

    def _assert_sms_block(self, entries):
        assert entries, "expected at least one rotation entry"
        for e in entries:
            assert "sms" in e, f"entry {e.get('id')} missing sms block"
            assert e["sms"]["configured"] is True

    def test_status_change_includes_sms_block(self, sms_client, sms_app):
        _, entry_id = _seed_request_and_link(sms_app)
        resp = sms_client.post("/rotation/status", json={"id": entry_id, "status": "Up Next"})
        self._assert_sms_block(self._entries(resp))

    def test_move_includes_sms_block(self, sms_client, sms_app):
        _seed_request_and_link(sms_app)
        _, entry_id = _seed_request_and_link(sms_app, singer="Dana", phone="843-111-2222")
        resp = sms_client.post("/rotation/move", json={"id": entry_id, "new_position": 1})
        self._assert_sms_block(self._entries(resp))

    def test_edit_includes_sms_block(self, sms_client, sms_app):
        _, entry_id = _seed_request_and_link(sms_app)
        resp = sms_client.post("/rotation/edit", json={"id": entry_id, "singer": "Celeste B."})
        self._assert_sms_block(self._entries(resp))

    def test_set_paid_includes_sms_block(self, sms_client, sms_app):
        _, entry_id = _seed_request_and_link(sms_app)
        resp = sms_client.post("/rotation/set-paid", json={"id": entry_id, "paid": True})
        self._assert_sms_block(self._entries(resp))

    def test_add_includes_sms_block(self, sms_client, sms_app):
        resp = sms_client.post("/rotation/add", json={"singer": "Walk-in", "song_artist": "Song"})
        self._assert_sms_block(self._entries(resp))

    def test_unlink_includes_sms_block(self, sms_client, sms_app):
        entry = sms_app.rotation.add_entry("Celeste", "Plump - Hole", file_path="/x.mp4")
        resp = sms_client.post("/rotation/unlink", json={"id": entry["id"]})
        self._assert_sms_block(self._entries(resp))


# ---------------------------------------------------------------------------
# Cross-night id-reuse phantom match (the "Connie" bug)
# ---------------------------------------------------------------------------

class TestCrossNightPhantomMatch:
    """A New Rotation resets rotation_entries' autoincrement, so a fresh entry
    can reuse an id that a PRIOR night's sing_request still points at. Phone
    resolution must be scoped to the current night so the recycled id never
    attaches the wrong singer's phone to the SMS button / send / preview.
    """

    def _seed_prior_night_request(self, sms_app, entry_id, *, phone="843-259-4507"):
        """Stale request from a previous night, linked to ``entry_id``."""
        from sing_store import NIGHT_STARTED_KEY

        sing = sms_app.sing_store
        sing._set_meta(NIGHT_STARTED_KEY, "2026-06-04 20:00:00")
        stale = sing.create_request(
            singer_name="Connie", phone=phone, source_type="local",
            source_ref="/x.mp4", song_title="Sippy Cup",
            song_artist="Melanie Martinez",
        )
        conn = sing._get_conn()
        conn.execute(
            "UPDATE sing_requests SET created_at = ?, linked_entry_id = ? WHERE id = ?",
            ("2026-05-28 21:00:00", entry_id, stale["id"]),
        )
        conn.commit()
        return stale["id"]

    def test_button_unavailable_for_recycled_id(self, sms_client, sms_app):
        entry = sms_app.rotation.add_entry("Brain Brawn", "Some Song")
        self._seed_prior_night_request(sms_app, entry["id"])
        resp = sms_client.get("/rotation")
        row = [e for e in resp.get_json()["entries"] if e["id"] == entry["id"]][0]
        assert row["sms"]["available"] is False
        assert row["sms"]["configured"] is True

    def test_preview_rejects_recycled_id(self, sms_client, sms_app):
        entry = sms_app.rotation.add_entry("Brain Brawn", "Some Song")
        self._seed_prior_night_request(sms_app, entry["id"])
        resp = sms_client.post("/rotation/sms/preview", json={"entry_id": entry["id"]})
        # Must NOT resolve to Connie's phone — no current-night phone on file.
        assert resp.status_code == 400

    def test_current_night_request_still_resolves(self, sms_client, sms_app):
        """The guard must not break the normal case: a request created during
        the current night still makes the button available and previewable."""
        from sing_store import NIGHT_STARTED_KEY

        # Night started in the distant past so the request's now() created_at is
        # unambiguously within the current night regardless of the test clock.
        sms_app.sing_store._set_meta(NIGHT_STARTED_KEY, "2000-01-01 00:00:00")
        _, entry_id = _seed_request_and_link(sms_app)  # created_at = now (tonight)
        resp = sms_client.get("/rotation")
        row = [e for e in resp.get_json()["entries"] if e["id"] == entry_id][0]
        assert row["sms"]["available"] is True
        preview = sms_client.post("/rotation/sms/preview", json={"entry_id": entry_id})
        assert preview.status_code == 200


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

    @patch("sms.requests.post")
    def test_send_to_opted_out_refused(self, mock_post, sms_client, sms_app):
        _, entry_id = _seed_request_and_link(sms_app, phone="843-259-4507")
        sms_app.sms_store.record_opt_out("+18432594507", keyword="STOP")
        resp = sms_client.post("/rotation/sms/send", json={
            "entry_id": entry_id, "body": "Hi Celeste!",
        })
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["success"] is False
        assert "opted out" in data["error"].lower()
        # Never hit Telnyx, and logged for the audit trail.
        mock_post.assert_not_called()
        log = sms_app.sms_store.get_log(data["sms_log_id"])
        assert log["status"] == "failed"


# ---------------------------------------------------------------------------
# Telnyx inbound webhook — /sing/telnyx/webhook
# ---------------------------------------------------------------------------

import base64
import json
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _keypair():
    priv = Ed25519PrivateKey.generate()
    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv, base64.b64encode(pub_raw).decode()


class TestTelnyxWebhook:
    def _signed_post(self, client, app, body_dict, *, valid=True):
        priv, pub_b64 = _keypair()
        app.sms_config["public_key"] = pub_b64
        raw = json.dumps(body_dict)
        ts = str(int(time.time()))
        if valid:
            sig = base64.b64encode(priv.sign(f"{ts}|{raw}".encode())).decode()
        else:
            sig = base64.b64encode(b"x" * 64).decode()  # valid b64, wrong sig
        return client.post(
            "/sing/telnyx/webhook", data=raw,
            headers={
                "telnyx-signature-ed25519": sig,
                "telnyx-timestamp": ts,
                "Content-Type": "application/json",
            },
        )

    def test_valid_dlr_updates_status(self, sms_client, sms_app):
        sms_app.sms_store.record_send(
            rotation_entry_id=1, sing_request_id=None, phone_e164="+1",
            body="hi", status="sent", telnyx_message_id="msg_abc",
        )
        resp = self._signed_post(sms_client, sms_app, {"data": {
            "event_type": "message.finalized",
            "payload": {"id": "msg_abc", "direction": "outbound",
                        "to": [{"phone_number": "+1", "status": "delivered"}]},
        }})
        assert resp.status_code == 200
        assert sms_app.sms_store.get_latest_for_entry(1)["status"] == "delivered"

    def test_invalid_signature_401(self, sms_client, sms_app):
        resp = self._signed_post(sms_client, sms_app, {"data": {
            "event_type": "message.finalized", "payload": {"id": "x"},
        }}, valid=False)
        assert resp.status_code == 401

    def test_inbound_stop_records_optout(self, sms_client, sms_app):
        assert sms_app.sms_store.is_opted_out("+18432594507") is False
        resp = self._signed_post(sms_client, sms_app, {"data": {
            "event_type": "message.received",
            "payload": {"id": "in_1", "direction": "inbound",
                        "from": {"phone_number": "+18432594507"},
                        "text": "STOP"},
        }})
        assert resp.status_code == 200
        assert sms_app.sms_store.is_opted_out("+18432594507") is True

    def test_inbound_start_clears_optout(self, sms_client, sms_app):
        sms_app.sms_store.record_opt_out("+18432594507", keyword="STOP")
        resp = self._signed_post(sms_client, sms_app, {"data": {
            "event_type": "message.received",
            "payload": {"id": "in_2", "direction": "inbound",
                        "from": {"phone_number": "+18432594507"},
                        "text": "START"},
        }})
        assert resp.status_code == 200
        assert sms_app.sms_store.is_opted_out("+18432594507") is False

    def test_unknown_event_acked_200(self, sms_client, sms_app):
        resp = self._signed_post(sms_client, sms_app, {"data": {
            "event_type": "call.hangup", "payload": {},
        }})
        assert resp.status_code == 200


class TestSmsDetail:
    """POST /rotation/sms/detail returns the last send's full detail so the KJ
    can open a modal with the exact message body + delivery info."""

    def test_detail_returns_last_send(self, sms_client, sms_app):
        _, entry_id = _seed_request_and_link(sms_app)
        sms_app.sms_store.record_send(
            rotation_entry_id=entry_id, sing_request_id=None,
            phone_e164="+18432594507", body="Hi Celeste! You're up next.",
            status="sent", telnyx_message_id="mid-42",
        )
        sms_app.sms_store.update_status_by_telnyx_id("mid-42", "delivered")
        resp = sms_client.post("/rotation/sms/detail",
                               json={"entry_id": entry_id})
        assert resp.status_code == 200
        d = resp.get_json()
        assert d["body"] == "Hi Celeste! You're up next."
        assert d["status"] == "delivered"
        assert d["telnyx_message_id"] == "mid-42"
        assert d["phone_e164"] == "+18432594507"
        assert d["sent_at"]

    def test_detail_404_when_no_send(self, sms_client, sms_app):
        _, entry_id = _seed_request_and_link(sms_app)
        resp = sms_client.post("/rotation/sms/detail",
                               json={"entry_id": entry_id})
        assert resp.status_code == 404

    def test_detail_requires_entry_id(self, sms_client):
        resp = sms_client.post("/rotation/sms/detail", json={})
        assert resp.status_code == 400

    def test_detail_400_on_non_integer_entry_id(self, sms_client):
        resp = sms_client.post("/rotation/sms/detail",
                               json={"entry_id": "abc"})
        assert resp.status_code == 400

    def test_detail_returns_newest_send(self, sms_client, sms_app):
        _, entry_id = _seed_request_and_link(sms_app)
        sms_app.sms_store.record_send(
            rotation_entry_id=entry_id, sing_request_id=None,
            phone_e164="+18432594507", body="first attempt",
            status="sent", telnyx_message_id="mid-old")
        sms_app.sms_store.record_send(
            rotation_entry_id=entry_id, sing_request_id=None,
            phone_e164="+18432594507", body="second attempt",
            status="sent", telnyx_message_id="mid-new")
        resp = sms_client.post("/rotation/sms/detail",
                               json={"entry_id": entry_id})
        assert resp.status_code == 200
        assert resp.get_json()["telnyx_message_id"] == "mid-new"
        assert resp.get_json()["body"] == "second attempt"
