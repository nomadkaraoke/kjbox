"""SMS delivery via Telnyx + template rendering + phone normalization.

Pure-Python module — no Flask imports — so unit tests run fast and the
provider integration stays swappable. The Flask layer (routes.py) wires
this to SmsStore and the rotation row payload.
"""

import base64
import logging
import re
import time

import requests

try:
    import phonenumbers
    from phonenumbers import NumberParseException, PhoneNumberFormat
    _HAS_PHONENUMBERS = True
except ImportError:  # pragma: no cover — only hit in dev envs without the dep
    phonenumbers = None
    NumberParseException = Exception
    PhoneNumberFormat = None
    _HAS_PHONENUMBERS = False


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

DEFAULT_TEMPLATE = (
    "Hi {first_name}! You're up next at Nomad Karaoke — "
    "{song} by {artist}. Head to the stage. Reply STOP to opt out."
)

# Per-variable caps. Picked so the default template fits in one SMS segment
# (160 GSM-7 chars) even with worst-case inputs:
#   fixed text in default template = ~86 chars
#   20 + 60 + 40 variable budget    = 120 chars
#   defensive headroom for custom templates that grow slightly.
DEFAULT_CAPS = {
    "first_name": 20,
    "song": 60,
    "artist": 40,
}

# Hard sanity cap on rendered body (~10 SMS segments). Anything longer is a
# KJ typo or a runaway template — we reject rather than silently send 50
# segments and a $0.40 bill.
MAX_BODY_LEN = 1600

# Segment thresholds — single-segment GSM-7 = 160 chars; multi-segment SMS
# headers chop each subsequent segment to 153. Telnyx auto-segments anything
# past 160, but the UI shows segment count so the KJ can see what they'll send.
SEGMENT_GSM7_SINGLE = 160
SEGMENT_GSM7_MULTI = 153

# Variable placeholders we recognise — anything else in the template renders
# verbatim (so a typo like {firstname} stays visible to the KJ rather than
# silently dropping).
_VAR_RE = re.compile(r"\{(first_name|song|artist)\}")


def _truncate(value, cap):
    """Truncate ``value`` to ``cap`` chars, appending ``…`` if shortened.

    The ellipsis is a single Unicode char that's part of the GSM-7 extension
    table, so it stays in single-segment territory rather than forcing UCS-2.
    """
    if value is None:
        return ""
    value = str(value)
    if len(value) <= cap:
        return value
    if cap <= 1:
        return value[:cap]
    return value[: cap - 1] + "…"


def render_template(template, vars, caps=None):
    """Render ``template`` with ``vars``, truncating each variable to its cap.

    ``vars`` is a dict like ``{"first_name": "Celeste B.", "song": "Plump",
    "artist": "Hole"}``. Missing keys render as empty strings — preview shows
    the broken result so the KJ notices a bad row rather than silently
    omitting context.

    Returns the rendered string. No length validation here; that's the
    caller's job (so the route can return the right HTTP error).
    """
    caps = caps or DEFAULT_CAPS
    truncated = {k: _truncate(vars.get(k, ""), caps.get(k, len(str(vars.get(k, ""))))) for k in caps}
    # Cover any extra keys the caller passed (e.g. for a future template
    # variable) without truncating them — we trust the caller.
    for k, v in (vars or {}).items():
        if k not in truncated:
            truncated[k] = "" if v is None else str(v)
    try:
        return template.format(**truncated)
    except (KeyError, IndexError):
        # A custom template referenced a variable we don't know about.
        # Substitute the recognised vars manually so the KJ at least sees
        # a partial render and a visible placeholder for the typo.
        out = template
        for k, v in truncated.items():
            out = out.replace("{" + k + "}", v)
        return out


def segment_count(body):
    """How many SMS segments ``body`` will use (GSM-7 assumed).

    UCS-2 (emoji / accents outside GSM-7) shrinks the budget to 70/67 but
    Telnyx handles that transparently; we report GSM-7 numbers because that's
    the common case and over-reporting confuses the KJ more than under-
    reporting hurts cost.
    """
    if not body:
        return 0
    n = len(body)
    if n <= SEGMENT_GSM7_SINGLE:
        return 1
    # Multi-segment: each segment carries 153 chars after the UDH overhead.
    return (n + SEGMENT_GSM7_MULTI - 1) // SEGMENT_GSM7_MULTI


# ---------------------------------------------------------------------------
# Phone normalization
# ---------------------------------------------------------------------------

class PhoneNormalizationError(ValueError):
    """Raised when a phone number can't be parsed/validated."""


def normalize_phone(raw, default_region="US"):
    """Parse ``raw`` to E.164 format.

    If ``raw`` starts with ``+`` it's parsed as international. Otherwise it's
    parsed against ``default_region`` (ISO 3166-1 alpha-2 like ``US``,
    ``AU``, ``GB``). Falls back to a permissive heuristic if libphonenumber
    isn't installed (dev convenience only — production has the dep).
    """
    if raw is None:
        raise PhoneNormalizationError("phone is empty")
    raw = str(raw).strip()
    if not raw:
        raise PhoneNormalizationError("phone is empty")

    if not _HAS_PHONENUMBERS:
        # Best-effort fallback: strip non-digit/+, ensure leading +.
        digits = re.sub(r"[^\d+]", "", raw)
        if not digits:
            raise PhoneNormalizationError("phone has no digits")
        if digits.startswith("+"):
            return digits
        # Naïve country-code prefix — only correct for US/CA defaults.
        return "+1" + digits if default_region == "US" else "+" + digits

    region = default_region if not raw.startswith("+") else None
    try:
        parsed = phonenumbers.parse(raw, region)
    except NumberParseException as exc:
        raise PhoneNormalizationError(f"can't parse phone: {exc}") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise PhoneNormalizationError("phone number is not valid")
    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


# ---------------------------------------------------------------------------
# Telnyx client
# ---------------------------------------------------------------------------

TELNYX_MESSAGES_URL = "https://api.telnyx.com/v2/messages"


class TelnyxError(RuntimeError):
    """Raised when Telnyx rejects the send (network or HTTP error)."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


def send(api_key, from_number, to_e164, body, timeout=10):
    """POST a message to Telnyx. Returns the ``data.id`` on success.

    Raises ``TelnyxError`` on any non-2xx response or network error; the
    caller logs the failure and surfaces the error string to the KJ.
    """
    if not api_key:
        raise TelnyxError("TELNYX_API_KEY not set")
    if not from_number:
        raise TelnyxError("TELNYX_FROM_NUMBER not set")

    try:
        resp = requests.post(
            TELNYX_MESSAGES_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": from_number,
                "to": to_e164,
                "text": body,
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise TelnyxError(f"network error: {exc}") from exc

    if resp.status_code // 100 != 2:
        # Telnyx error bodies follow JSON:API shape: {"errors": [{"title":..., "detail":...}]}
        message = f"HTTP {resp.status_code}"
        try:
            body_json = resp.json()
            errors = body_json.get("errors") or []
            if errors and isinstance(errors, list):
                first = errors[0]
                detail = first.get("detail") or first.get("title") or ""
                if detail:
                    message = f"HTTP {resp.status_code}: {detail}"
        except ValueError:
            pass
        raise TelnyxError(message, status_code=resp.status_code, response_body=resp.text)

    try:
        data = resp.json().get("data") or {}
    except ValueError:
        raise TelnyxError("Telnyx returned non-JSON body", status_code=resp.status_code)

    message_id = data.get("id")
    if not message_id:
        raise TelnyxError("Telnyx response missing data.id", status_code=resp.status_code)
    return message_id


# ---------------------------------------------------------------------------
# Inbound webhook — signature verification + event parsing
# ---------------------------------------------------------------------------

# Telnyx signs each webhook with Ed25519 over ``f"{timestamp}|{raw_body}"``.
# Reject anything older than this to blunt replay attacks.
WEBHOOK_TOLERANCE_SECONDS = 300

# Standard carrier opt-out / opt-in keywords (CTIA). Telnyx auto-handles the
# carrier-side response; we mirror the decision locally so the send path and
# the audit log stay honest.
_STOP_KEYWORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "QUIT", "END", "OPTOUT"}
_START_KEYWORDS = {"START", "UNSTOP", "YES", "OPTIN"}


def verify_webhook_signature(public_key_b64, signature_b64, timestamp, raw_body,
                             tolerance_seconds=WEBHOOK_TOLERANCE_SECONDS, now=None):
    """Verify a Telnyx Ed25519 webhook signature. Returns True/False only.

    ``raw_body`` is the exact request body string (must not be re-serialised —
    any whitespace change breaks the signature). ``now`` is injectable epoch
    seconds for tests; defaults to wall-clock. Fails closed on anything
    malformed (missing key/sig/timestamp, bad base64, stale timestamp).
    """
    if not public_key_b64 or not signature_b64 or not timestamp or raw_body is None:
        return False

    # Replay guard: timestamp must be recent.
    try:
        ts_int = int(timestamp)
    except (TypeError, ValueError):
        return False
    now = time.time() if now is None else now
    if abs(now - ts_int) > tolerance_seconds:
        return False

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        from cryptography.exceptions import InvalidSignature

        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key_b64)
        )
        signature = base64.b64decode(signature_b64)
    except Exception:
        return False

    signed = f"{timestamp}|{raw_body}".encode()
    try:
        public_key.verify(signature, signed)
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False


def classify_inbound_keyword(text):
    """Return 'stop', 'start', or None for an inbound message body.

    Matches only when the first whitespace-delimited token is a recognised
    keyword, so "STOP texting me" opts out but "can I stop by?" does not.
    """
    if not text:
        return None
    tokens = text.strip().split()
    first = tokens[0].upper() if tokens else ""
    if first in _STOP_KEYWORDS:
        return "stop"
    if first in _START_KEYWORDS:
        return "start"
    return None


def parse_webhook_event(payload):
    """Normalise a Telnyx webhook body into a small dict.

    Returns ``{"kind": "dlr"|"inbound"|"unknown", "message_id", "status",
    "error", "from", "text"}``. Unknown / malformed payloads return
    ``{"kind": "unknown"}`` so the route can 200-ack without acting.
    """
    base = {"kind": "unknown", "message_id": None, "status": None,
            "error": None, "from": None, "text": None}
    if not isinstance(payload, dict):
        return base
    data = payload.get("data") or {}
    event_type = data.get("event_type") or ""
    inner = data.get("payload") or {}

    if event_type == "message.received" or inner.get("direction") == "inbound":
        sender = inner.get("from") or {}
        base["kind"] = "inbound"
        base["message_id"] = inner.get("id")
        base["from"] = sender.get("phone_number") if isinstance(sender, dict) else None
        base["text"] = inner.get("text")
        return base

    if event_type in ("message.sent", "message.finalized") or \
            inner.get("direction") == "outbound":
        to_list = inner.get("to") or []
        status = to_list[0].get("status") if to_list else None
        errors = inner.get("errors") or []
        error = None
        if errors and isinstance(errors, list):
            first = errors[0]
            error = "; ".join(
                str(x) for x in (first.get("code"), first.get("title"),
                                 first.get("detail")) if x
            ) or None
        base["kind"] = "dlr"
        base["message_id"] = inner.get("id")
        base["status"] = status
        base["error"] = error
        return base

    return base
