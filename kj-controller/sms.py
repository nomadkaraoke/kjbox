"""SMS delivery via Telnyx + template rendering + phone normalization.

Pure-Python module — no Flask imports — so unit tests run fast and the
provider integration stays swappable. The Flask layer (routes.py) wires
this to SmsStore and the rotation row payload.
"""

import logging
import re

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
