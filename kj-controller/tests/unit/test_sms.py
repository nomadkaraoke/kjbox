"""Unit tests for sms.py — template rendering, phone normalization, Telnyx client."""

from unittest.mock import MagicMock, patch

import pytest
import requests

import sms


# ---------------------------------------------------------------------------
# render_template
# ---------------------------------------------------------------------------

class TestRenderTemplate:
    def test_default_template_with_short_vars(self):
        body = sms.render_template(
            sms.DEFAULT_TEMPLATE,
            {"first_name": "Celeste", "song": "Plump", "artist": "Hole"},
        )
        assert body == (
            "Hi Celeste! You're up next at Nomad Karaoke — "
            "Plump by Hole. Head to the stage. Reply STOP to opt out."
        )
        assert len(body) <= sms.SEGMENT_GSM7_SINGLE

    def test_truncates_first_name_with_ellipsis(self):
        body = sms.render_template(
            "{first_name}",
            {"first_name": "A" * 50},
        )
        assert len(body) == sms.DEFAULT_CAPS["first_name"]
        assert body.endswith("…")

    def test_truncates_song(self):
        body = sms.render_template(
            "{song}",
            {"song": "S" * 100},
        )
        assert len(body) == sms.DEFAULT_CAPS["song"]
        assert body.endswith("…")

    def test_truncates_artist(self):
        body = sms.render_template(
            "{artist}",
            {"artist": "A" * 100},
        )
        assert len(body) == sms.DEFAULT_CAPS["artist"]
        assert body.endswith("…")

    def test_no_truncation_when_within_cap(self):
        # 20-char first_name fits the cap exactly — should not get an ellipsis.
        name = "A" * 20
        body = sms.render_template("{first_name}", {"first_name": name})
        assert body == name

    def test_missing_var_renders_empty(self):
        body = sms.render_template(
            "{first_name}-{song}-{artist}",
            {"first_name": "Bea"},
        )
        assert body == "Bea--"

    def test_unknown_variable_in_template_stays_visible(self):
        # Custom templates with typos shouldn't crash — the unknown var
        # renders as a literal placeholder so the KJ notices and fixes it.
        body = sms.render_template(
            "{first_name} {firstname}",
            {"first_name": "Bea"},
        )
        assert "Bea" in body
        assert "{firstname}" in body

    def test_default_template_worst_case_under_one_segment(self):
        # Realistic upper-bound inputs: max name + long song + long artist.
        body = sms.render_template(
            sms.DEFAULT_TEMPLATE,
            {
                "first_name": "Maximilian Alexander",
                "song": "Bohemian Rhapsody (Operatic Mega Mix Extended Karaoke)",
                "artist": "Queen feat. Some Long Featured Artist",
            },
        )
        # May spill into 2 segments but must NEVER exceed the sanity cap.
        assert len(body) <= sms.MAX_BODY_LEN
        assert "Reply STOP to opt out." in body


class TestSegmentCount:
    def test_empty(self):
        assert sms.segment_count("") == 0
        assert sms.segment_count(None) == 0

    def test_single_segment(self):
        assert sms.segment_count("a" * 1) == 1
        assert sms.segment_count("a" * 160) == 1

    def test_two_segments(self):
        assert sms.segment_count("a" * 161) == 2
        assert sms.segment_count("a" * 306) == 2

    def test_three_segments(self):
        assert sms.segment_count("a" * 307) == 3


# ---------------------------------------------------------------------------
# normalize_phone
# ---------------------------------------------------------------------------

class TestNormalizePhone:
    def test_us_local_format(self):
        assert sms.normalize_phone("8432594507", default_region="US") == "+18432594507"

    def test_us_local_with_dashes(self):
        assert sms.normalize_phone("843-259-4507", default_region="US") == "+18432594507"

    def test_us_local_with_spaces_and_parens(self):
        assert sms.normalize_phone("(843) 259-4507", default_region="US") == "+18432594507"

    def test_already_e164(self):
        # Preserved verbatim regardless of default_region.
        assert sms.normalize_phone("+18432594507", default_region="AU") == "+18432594507"

    def test_au_local_format(self):
        # Common mobile local format: 04XX XXX XXX → +61 4XX XXX XXX
        assert sms.normalize_phone("0400 123 456", default_region="AU") == "+61400123456"

    def test_uk_local_format(self):
        # 07XXX XXXXXX (UK mobile) → +44 7XXX XXXXXX
        assert sms.normalize_phone("07123 456789", default_region="GB") == "+447123456789"

    def test_empty_rejected(self):
        with pytest.raises(sms.PhoneNormalizationError):
            sms.normalize_phone("", default_region="US")

    def test_none_rejected(self):
        with pytest.raises(sms.PhoneNormalizationError):
            sms.normalize_phone(None, default_region="US")

    def test_garbage_rejected(self):
        # Only "abc" — no digits — must fail.
        with pytest.raises(sms.PhoneNormalizationError):
            sms.normalize_phone("abc", default_region="US")

    def test_too_short_rejected(self):
        # 3 digits can't be a valid number anywhere.
        with pytest.raises(sms.PhoneNormalizationError):
            sms.normalize_phone("123", default_region="US")


# ---------------------------------------------------------------------------
# Telnyx send
# ---------------------------------------------------------------------------

class TestSend:
    @patch("sms.requests.post")
    def test_happy_path(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": {"id": "msg_abc123"}},
        )
        msg_id = sms.send(
            api_key="key_xyz",
            from_number="+18005551234",
            to_e164="+18432594507",
            body="hi",
        )
        assert msg_id == "msg_abc123"
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer key_xyz"
        assert call_kwargs["json"]["from"] == "+18005551234"
        assert call_kwargs["json"]["to"] == "+18432594507"
        assert call_kwargs["json"]["text"] == "hi"

    @patch("sms.requests.post")
    def test_4xx_raises_with_detail(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=400,
            text='{"errors": [...]}',
            json=lambda: {
                "errors": [{"title": "Invalid Number", "detail": "to is not a valid number"}]
            },
        )
        with pytest.raises(sms.TelnyxError) as excinfo:
            sms.send("key", "+1", "+1", "x")
        assert "400" in str(excinfo.value)
        assert "to is not a valid number" in str(excinfo.value)
        assert excinfo.value.status_code == 400

    @patch("sms.requests.post")
    def test_5xx_raises(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=502,
            text="upstream error",
            json=lambda: (_ for _ in ()).throw(ValueError("not json")),
        )
        with pytest.raises(sms.TelnyxError) as excinfo:
            sms.send("key", "+1", "+1", "x")
        assert excinfo.value.status_code == 502

    @patch("sms.requests.post")
    def test_network_error_wrapped(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("dns broken")
        with pytest.raises(sms.TelnyxError) as excinfo:
            sms.send("key", "+1", "+1", "x")
        assert "network error" in str(excinfo.value)

    @patch("sms.requests.post")
    def test_2xx_without_data_id_raises(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": {}},
        )
        with pytest.raises(sms.TelnyxError):
            sms.send("key", "+1", "+1", "x")

    def test_missing_api_key_raises(self):
        with pytest.raises(sms.TelnyxError) as excinfo:
            sms.send(api_key=None, from_number="+1", to_e164="+1", body="x")
        assert "TELNYX_API_KEY" in str(excinfo.value)

    def test_missing_from_number_raises(self):
        with pytest.raises(sms.TelnyxError) as excinfo:
            sms.send(api_key="k", from_number=None, to_e164="+1", body="x")
        assert "TELNYX_FROM_NUMBER" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _make_keypair():
    """Return (public_key_b64, sign(timestamp, body) -> signature_b64)."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    from cryptography.hazmat.primitives import serialization
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_b64 = base64.b64encode(pub_raw).decode()

    def sign(timestamp, body):
        signed = f"{timestamp}|{body}".encode()
        return base64.b64encode(priv.sign(signed)).decode()

    return pub_b64, sign


class TestVerifyWebhookSignature:
    def test_valid_signature_passes(self):
        pub_b64, sign = _make_keypair()
        body = '{"data":{"event_type":"message.finalized"}}'
        ts = "1700000000"
        sig = sign(ts, body)
        assert sms.verify_webhook_signature(
            pub_b64, sig, ts, body, now=1700000010
        ) is True

    def test_tampered_body_fails(self):
        pub_b64, sign = _make_keypair()
        ts = "1700000000"
        sig = sign(ts, '{"original":true}')
        assert sms.verify_webhook_signature(
            pub_b64, sig, ts, '{"tampered":true}', now=1700000010
        ) is False

    def test_wrong_key_fails(self):
        _, sign = _make_keypair()
        other_pub_b64, _ = _make_keypair()
        ts = "1700000000"
        body = "{}"
        sig = sign(ts, body)
        assert sms.verify_webhook_signature(
            other_pub_b64, sig, ts, body, now=1700000010
        ) is False

    def test_stale_timestamp_fails(self):
        pub_b64, sign = _make_keypair()
        ts = "1700000000"
        body = "{}"
        sig = sign(ts, body)
        # 10 minutes later, default tolerance 300s -> reject (replay guard).
        assert sms.verify_webhook_signature(
            pub_b64, sig, ts, body, now=1700000000 + 600
        ) is False

    def test_missing_pieces_fail(self):
        pub_b64, sign = _make_keypair()
        assert sms.verify_webhook_signature(pub_b64, "", "1", "{}", now=1) is False
        assert sms.verify_webhook_signature(pub_b64, "sig", "", "{}", now=1) is False
        assert sms.verify_webhook_signature(None, "sig", "1", "{}", now=1) is False

    def test_garbage_signature_fails(self):
        pub_b64, _ = _make_keypair()
        assert sms.verify_webhook_signature(
            pub_b64, "!!!not-base64!!!", "1700000000", "{}", now=1700000000
        ) is False


# ---------------------------------------------------------------------------
# Webhook event parsing
# ---------------------------------------------------------------------------

class TestParseWebhookEvent:
    def _payload(self, **payload):
        # Telnyx serialises the sender under the JSON key "from"; map the
        # Python-safe kwarg from_ onto it.
        if "from_" in payload:
            payload["from"] = payload.pop("from_")
        return {"data": {"event_type": payload.pop("event_type"),
                         "payload": payload}}

    def test_dlr_delivered(self):
        evt = sms.parse_webhook_event(self._payload(
            event_type="message.finalized",
            direction="outbound",
            id="msg_abc",
            to=[{"phone_number": "+18432594507", "status": "delivered"}],
        ))
        assert evt["kind"] == "dlr"
        assert evt["message_id"] == "msg_abc"
        assert evt["status"] == "delivered"
        assert evt["error"] is None

    def test_dlr_failed_includes_error(self):
        evt = sms.parse_webhook_event(self._payload(
            event_type="message.finalized",
            direction="outbound",
            id="msg_abc",
            to=[{"phone_number": "+1", "status": "delivery_failed"}],
            errors=[{"code": "40010", "title": "Not 10DLC registered"}],
        ))
        assert evt["kind"] == "dlr"
        assert evt["status"] == "delivery_failed"
        assert "40010" in evt["error"]

    def test_inbound_stop(self):
        evt = sms.parse_webhook_event(self._payload(
            event_type="message.received",
            direction="inbound",
            id="in_1",
            from_={"phone_number": "+18432594507"},
            text="STOP",
        ))
        assert evt["kind"] == "inbound"
        assert evt["from"] == "+18432594507"
        assert evt["text"] == "STOP"

    def test_inbound_uses_from_key(self):
        # Telnyx serialises the sender under the JSON key "from"; ensure the
        # parser reads it (our _payload helper passes from_ -> normalised).
        payload = {"data": {"event_type": "message.received", "payload": {
            "direction": "inbound", "id": "in_2",
            "from": {"phone_number": "+19998887777"}, "text": "hello",
        }}}
        evt = sms.parse_webhook_event(payload)
        assert evt["from"] == "+19998887777"

    def test_unknown_event(self):
        evt = sms.parse_webhook_event({"data": {"event_type": "call.hangup",
                                                "payload": {}}})
        assert evt["kind"] == "unknown"

    def test_malformed_payload(self):
        assert sms.parse_webhook_event({})["kind"] == "unknown"
        assert sms.parse_webhook_event(None)["kind"] == "unknown"


class TestIsOptOutKeyword:
    def test_stop_variants(self):
        for kw in ["STOP", "stop", " Stop ", "STOPALL", "UNSUBSCRIBE", "CANCEL",
                   "QUIT", "END"]:
            assert sms.classify_inbound_keyword(kw) == "stop"

    def test_start_variants(self):
        for kw in ["START", "start", "UNSTOP", "YES"]:
            assert sms.classify_inbound_keyword(kw) == "start"

    def test_other_text_is_none(self):
        assert sms.classify_inbound_keyword("can I sing next?") is None
        assert sms.classify_inbound_keyword("") is None
        assert sms.classify_inbound_keyword(None) is None
