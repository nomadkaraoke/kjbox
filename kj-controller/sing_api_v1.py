"""App-facing singer API, version 1 (``/sing/api/v1/*``).

Served on the public host as ``https://sing.nomadkaraoke.com/api/v1/...`` (the
public-host rewriter prefixes ``/sing``; the host guard allows the path).
Contract: ``docs/api/singer-v1.openapi.json`` — every response here must match
it (tests/integration/v1_contract.py enforces that in the integration suite).

Every endpoint is wrapped in ``@app_auth``, which checks, in order:

1. ``Authorization: Bearer <gen session token>`` is present  → 401 unauthenticated
2. the event code ``t`` (query string or JSON body only; v1 never reads or
   sets the Flask session cookie) is valid and requests are enabled
                                                               → 403 not_open
3. gen accepts the token (GenTokenVerifier, cached)            → 401 / 503
4. the night marker is set (fail closed)                       → 403 not_open
5. a ``night_id`` the client sends (query or JSON) is tonight's → 409 night_changed

``GET /night`` turns the "not open" outcomes (2 and 4) into a 200
``{open: false}`` so the app can show the closed state with the host's message.

The bearer token only ever lives on ``g`` for the request; it's never stored,
logged, or recorded (the ActionRecorder redacts ``Authorization``).
"""

import hashlib
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request

import sing
from app_identity import AppAuthError, suggested_name

API_VERSION = "1.0"
DEFAULT_BOX_ID = "nomadpc"
# Footer "social" keys that are ways to reach the host rather than links to follow.
_CONTACT_KEYS = ("email", "phone")

app_api_bp = Blueprint("app_api", __name__, url_prefix="/sing/api/v1")


# --- Response helpers ----------------------------------------------------

def v1_json(payload, status=200):
    """JSON response with ``api_version`` added; never cached by intermediaries."""
    resp = jsonify({"api_version": API_VERSION, **payload})
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp


def v1_error(code, status, message=None, retry_after=None, **extra):
    """``{"error": code, "message"?, "retry_after"?}`` (+ contract-declared
    extras such as ``night_id`` on ``night_changed``). ``code`` must be in the
    contract's ``ErrorCode`` enum."""
    body = {"error": code}
    if message:
        body["message"] = message
    if retry_after is not None:
        body["retry_after"] = int(retry_after)
        extra_headers = {"Retry-After": str(int(retry_after))}
    else:
        extra_headers = {}
    body.update(extra)
    resp = v1_json(body, status)
    resp.headers.update(extra_headers)
    return resp


def night_id_for(store, cfg):
    """``sha256("<box_id>:<night_started_at>")[:16]``, or None with no night marker."""
    night = store.get_night_started_at()
    if not night:
        return None
    box_id = (cfg or {}).get("box_id") or DEFAULT_BOX_ID
    return hashlib.sha256(f"{box_id}:{night}".encode()).hexdigest()[:16]


# --- Request parsing -----------------------------------------------------

def _json_body():
    if not request.is_json:
        return {}
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _bearer_token():
    header = request.headers.get("Authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def _param(name):
    """A request parameter from the query string, else the JSON body."""
    value = request.args.get(name)
    if value:
        return value
    value = _json_body().get(name)
    return value if isinstance(value, str) and value else None


# --- Auth decorator ------------------------------------------------------

def app_auth(view=None, *, on_closed=None):
    """Authenticate a v1 call and scope it to tonight.

    On success sets on ``g``:
      ``app_user``         AppUser(email, display_name, device_id)
      ``night_started_at`` str, the current night marker
      ``night_id``         str, ``night_id_for(...)``
      ``account_devices``  list[str], every device linked to the account tonight
                           (the app device plus claimed web devices)
      ``app_gen_account``  {"email", "session_token"} for calls forwarded to gen;
                           request-scoped only — never persist or log it.

    The app device is linked in ``singer_accounts`` (origin ``app``) on its
    first call of the night. ``on_closed(reason)`` (reason ``closed`` |
    ``bad_code`` | ``no_night``) replaces the 403 ``not_open`` response; GET
    /night uses it to return ``{open: false}``.
    """
    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            store = getattr(current_app, "sing_store", None)
            if store is None:
                return v1_error("not_open", 403, "requests are not configured")

            token = _bearer_token()
            if not token:
                return v1_error("unauthenticated", 401)

            if not store.is_enabled():
                return _not_open(on_closed, "closed")
            code = _param("t")
            if not code or not sing._is_token_valid(store, code):
                return _not_open(on_closed, "bad_code")

            verifier = getattr(current_app, "gen_verifier", None)
            if verifier is None:
                return v1_error("gen_unavailable", 503, "gen is not configured")
            try:
                user = verifier.verify(token)
            except AppAuthError as exc:
                if exc.code == "gen_unavailable":
                    return v1_error("gen_unavailable", 503, retry_after=5)
                return v1_error("unauthenticated", 401)

            night = store.get_night_started_at()
            if not night:
                return _not_open(on_closed, "no_night")
            night_id = night_id_for(store, current_app.kj_config)
            sent = _param("night_id")
            if sent and sent != night_id:
                return v1_error("night_changed", 409,
                                "the host started a new night", night_id=night_id)

            if store.account_for_device(user.device_id, night) != user.email:
                store.link_account(user.device_id, user.email, "app", night)

            g.app_user = user
            g.night_started_at = night
            g.night_id = night_id
            g.account_devices = store.account_devices(user.email, night) or [user.device_id]
            g.app_gen_account = {"email": user.email, "session_token": token}
            return fn(*args, **kwargs)

        return wrapper

    return decorate(view) if view is not None else decorate


def _not_open(on_closed, reason):
    if on_closed is not None:
        return on_closed(reason)
    return v1_error("not_open", 403)


# --- GET /night ----------------------------------------------------------

def _venue_info(store):
    """kj_name + footer settings, as the web's /sing/event-info reads them."""
    footer = sing._safe_call(store.get_footer_settings, None) or {}
    tips = sing._tip_settings(current_app.kj_config, store)
    return footer, tips


def _closed_night(reason):
    store = current_app.sing_store
    payload = {"open": False, "error": "not_open", "reason": reason}
    if reason == "closed":
        # The host's own words for a closed night (e.g. "Back next Thursday").
        footer, tips = _venue_info(store)
        payload["kj_name"] = tips.get("kj_name") or ""
        payload["footer_message"] = footer.get("message") or ""
    return v1_json(payload)


def _rotation_rev():
    rotation = getattr(current_app, "rotation", None)
    store = getattr(rotation, "store", None)
    rev = sing._safe_call(store.get_rev, 0) if store is not None else 0
    return int(rev or 0)


@app_api_bp.route("/night", methods=["GET"])
@app_auth(on_closed=_closed_night)
def get_night():
    store = current_app.sing_store
    cfg = current_app.kj_config or {}
    footer, tips = _venue_info(store)
    notices = footer.get("notices") or []
    social = footer.get("social") or {}
    payload = {
        "open": True,
        "night_id": g.night_id,
        "kj_name": tips.get("kj_name") or "",
        "footer_message": footer.get("message") or "",
        "notices": notices,
        "notice_defs": {k: v for k, v in (footer.get("notice_defs") or {}).items()
                        if k in notices},
        "social": {k: v for k, v in social.items() if k not in _CONTACT_KEYS},
        "contact": {k: v for k, v in social.items() if k in _CONTACT_KEYS},
        "features": {
            "make": bool(sing._safe_call(lambda: sing._make_enabled(store), False)),
            "simple_mode": bool(store.is_simple_mode()),
            "ask_photo_consent": bool(footer.get("ask_photo_consent")),
            "quick_version": bool(cfg.get("make_quick_version_enabled", True)),
            "tips": sing._tips_enabled(tips),
        },
        "rev": _rotation_rev(),
    }
    name = store.get_alias(g.app_user.device_id)
    if name:
        payload["needs_name"] = False
        payload["me"] = {"name": name}
    else:
        payload["needs_name"] = True
        payload["suggested_name"] = suggested_name(g.app_user) or None
    return v1_json(payload)


# --- POST /join ----------------------------------------------------------

@app_api_bp.route("/join", methods=["POST"])
@app_auth
def join_night():
    raw = _json_body().get("name")
    name = " ".join(raw.split()) if isinstance(raw, str) else ""
    if not name:
        return v1_error("invalid", 400, "name is required")
    if len(name) > sing._MAX_SINGER_NAME_LEN:
        return v1_error("invalid", 400, "name is too long")
    store = current_app.sing_store
    if store.get_alias(g.app_user.device_id) != name:
        store.set_alias(g.app_user.device_id, name, origin="self")
    return v1_json({"me": {"name": name}})
