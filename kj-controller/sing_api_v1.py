"""App-facing singer API, version 1 (``/sing/api/v1/*``).

Served on the public host as ``https://sing.nomadkaraoke.com/api/v1/...`` (the
public-host rewriter prefixes ``/sing``; the host guard allows the path).
Contract: ``docs/api/singer-v1.openapi.json`` — every response here must match
it (tests/integration/v1_contract.py enforces that in the integration suite).

Every endpoint is wrapped in ``@app_auth``, which checks, in order:

1. ``Authorization: Bearer <gen session token>`` is present  → 401 unauthenticated
   (then: an IP with 10 failed event codes in 5 min           → 429 rate_limited)
2. the event code ``t`` (query string or JSON body only; v1 never reads or
   sets the Flask session cookie) is valid and requests are enabled
                                                               → 403 not_open
3. gen accepts the token (GenTokenVerifier, cached)            → 401 / 503
   (an IP with 30 cache-missing verifications in 1 min        → 429 rate_limited)
4. the night marker is set (fail closed)                       → 403 not_open
5. a ``night_id`` the client sends (query or JSON) is tonight's → 409 night_changed

``GET /night`` turns the "not open" outcomes (2 and 4) into a 200
``{open: false}`` so the app can show the closed state with the host's message.

The bearer token only ever lives on ``g`` for the request; it's never stored,
logged, or recorded (the ActionRecorder redacts ``Authorization``).
"""

import hashlib
import logging
import math
import threading
import time
import uuid
from collections import defaultdict, deque
from functools import wraps

from flask import Blueprint, Response, current_app, g, jsonify, request
from werkzeug.exceptions import HTTPException

import sing
from app_identity import AppAuthError, suggested_name

API_VERSION = "1.0"
DEFAULT_BOX_ID = "nomadpc"
# Footer "social" keys that are ways to reach the host rather than links to follow.
_CONTACT_KEYS = ("email", "phone")

V1_PATH_PREFIX = "/sing/api/v1/"

logger = logging.getLogger(__name__)

app_api_bp = Blueprint("app_api", __name__, url_prefix="/sing/api/v1")


# --- In-memory per-IP limits (reset on restart, like the web's) -----------

class SlidingWindow:
    """Per-key sliding-window counter. ``retry_after(key)`` is None while the
    key is under ``limit`` hits in ``window_s``, else whole seconds until the
    oldest hit ages out; ``hit(key)`` records one."""

    def __init__(self, limit, window_s, clock=time.monotonic):
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def _pruned(self, key, now):
        q = self._hits[key]
        while q and q[0] <= now - self.window_s:
            q.popleft()
        return q

    def retry_after(self, key):
        now = self._clock()
        with self._lock:
            q = self._pruned(key, now)
            if not q:
                self._hits.pop(key, None)  # don't keep an entry per IP ever seen
                return None
            if len(q) < self.limit:
                return None
            return max(1, math.ceil(q[0] + self.window_s - now))

    def hit(self, key):
        with self._lock:
            now = self._clock()
            self._pruned(key, now).append(now)

    def clear(self):
        with self._lock:
            self._hits.clear()


# Event-code guessing: only FAILED codes count (same 10 / 5 min as
# /sing/validate, separate bucket). Once over, every v1 call from the IP gets
# 429 — even with the right code — so the limit can't be used as an oracle.
failed_code_limit = SlidingWindow(10, 300)
# gen token checks that miss the verifier cache, per IP.
gen_verify_limit = SlidingWindow(30, 60)


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

            ip = sing._client_ip(request)
            wait = failed_code_limit.retry_after(ip)
            if wait is not None:
                return v1_error("rate_limited", 429, "too many wrong event codes",
                                retry_after=wait)
            if not store.is_enabled():
                return _not_open(on_closed, "closed")
            code = _param("t")
            if not code or not sing._is_token_valid(store, code):
                failed_code_limit.hit(ip)
                return _not_open(on_closed, "bad_code")

            verifier = getattr(current_app, "gen_verifier", None)
            if verifier is None:
                return v1_error("gen_unavailable", 503, "gen is not configured")
            if not verifier.is_cached(token):
                wait = gen_verify_limit.retry_after(ip)
                if wait is not None:
                    return v1_error("rate_limited", 429, "too many sign-in checks",
                                    retry_after=wait)
                gen_verify_limit.hit(ip)
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


# --- Delegation to the web /sing/* views ----------------------------------
#
# v1 mutations run the web's own view functions instead of re-implementing them,
# so the app and the web page can't drift apart. The view runs in a nested
# request context that looks like the web call it replaces.

def delegate(view_fn, *, method, path, json=None, args=None, device_id=None,
             view_args=None):
    """Run a ``/sing/*`` view as if the web page had called it; return
    ``(body: dict, status: int)`` for the v1 endpoint to reshape.

    The nested request carries ``t`` (the event code of this v1 call) and
    ``device_id`` (``device_id`` if given — the owning device of the request
    being changed — else the app device) in the JSON body (or the query string
    for a GET), plus the outer client's IP (as a trusted-proxy hop:
    REMOTE_ADDR 127.0.0.1 + ``CF-Connecting-IP``, so the web's per-IP limits
    see the real client), ``Accept-Language`` and ``User-Agent``. Edit tokens,
    when the view needs one, are the caller's to put in ``json`` (read
    server-side from the request row, never from the app).

    Side effects kept in check: the nested context's session is never saved
    (no cookie reaches the v1 response); the ActionRecorder is paused so it
    records only the outer v1 call; the web rate limiter's per-call slot
    (``g.sing_rl_device_slot``, ``g`` is shared with the outer context) is
    dropped afterwards — a 400 inside the view has already refunded it.
    ``view_args`` are passed to ``view_fn`` (e.g. ``{"req_id": 5}``).
    """
    dev = device_id or g.app_user.device_id
    t = _param("t")
    query = dict(args or {})
    query["t"] = t
    headers = {"CF-Connecting-IP": sing._client_ip(request)}
    for name in ("Accept-Language", "User-Agent"):
        if request.headers.get(name):
            headers[name] = request.headers[name]
    ctx_kwargs = {
        "method": method,
        "headers": headers,
        "environ_base": {"REMOTE_ADDR": "127.0.0.1"},
        "base_url": request.host_url,
    }
    if method.upper() == "GET":
        query["device_id"] = dev
    else:
        ctx_kwargs["json"] = {**(json or {}), "t": t, "device_id": dev}
    ctx_kwargs["query_string"] = query

    had_rec = "_action_rec" in g
    saved_rec = g.get("_action_rec")
    g._action_rec = False   # the outer request's after_request records the v1 call
    try:
        with current_app.test_request_context(path, **ctx_kwargs):
            resp = current_app.make_response(view_fn(**(view_args or {})))
    finally:
        g.pop("sing_rl_device_slot", None)
        if had_rec:
            g._action_rec = saved_rec
        else:
            g.pop("_action_rec", None)
    body = resp.get_json(silent=True)
    return (body if isinstance(body, dict) else {}), resp.status_code


# Web error string -> (v1 code, HTTP status). The ONE place web errors are
# translated. A key ending in "*" matches by prefix. Anything not listed falls
# back on its status (404 not_found, 409 conflict, 429 rate_limited,
# 5xx internal, else 400 invalid); the web's own string always rides along in
# ``message``.
_WEB_ERROR_MAP = {
    "rate_limited": ("rate_limited", 429),
    "not_open": ("not_open", 403),
    "not_configured": ("not_open", 403),
    "not_found": ("not_found", 404),
    "forbidden": ("not_owner", 403),
    "already_sung": ("already_sung", 409),
    "kj_pick too many versions*": ("kj_pick_too_large", 400),
    "make_requests_disabled": ("make_disabled", 400),
    "make_not_changeable": ("make_disabled", 400),
    "make_limit": ("make_disabled", 400),
    "youtube_blocked": ("youtube_blocked", 400),
    "gen_rejected": ("gen_rejected", 400),
    "no_credits": ("gen_rejected", 400),
    "already_generating": ("already_generating", 409),
    "busy_retry": ("busy_retry", 409),
    # v1 callers always carry a verified gen account, so this is never "your
    # session expired" (which the app would treat as a sign-out signal).
    "signin_required": ("invalid", 400),
    "gen_unavailable": ("gen_unavailable", 503),
}
_STATUS_FALLBACK = {401: "unauthenticated", 404: "not_found", 409: "conflict",
                    429: "rate_limited"}


def map_web_error(body, status, device_id=None):
    """A v1 error response for a web view's error ``(body, status)``."""
    err = body.get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, str) else ""
    mapped = _WEB_ERROR_MAP.get(err)
    if mapped is None:
        for key, value in _WEB_ERROR_MAP.items():
            if key.endswith("*") and err.startswith(key[:-1]):
                mapped = value
                break
    if mapped is None:
        if status >= 500:
            mapped = ("internal", 500)
        else:
            mapped = (_STATUS_FALLBACK.get(status, "invalid"),
                      status if status in _STATUS_FALLBACK else 400)
    code, v1_status = mapped
    retry_after = _web_rate_retry_after(device_id) if code == "rate_limited" else None
    return v1_error(code, v1_status, err if err and err != code else None,
                    retry_after=retry_after)


def _web_rate_retry_after(device_id):
    """Seconds until the web limiter's device or IP budget frees a slot."""
    cfg = current_app.kj_config or {}
    window = sing._safe_int(cfg.get("sing_rate_limit_window_s"), 300)
    budgets = [(f"ip:{sing._client_ip(request)}",
                sing._safe_int(cfg.get("sing_rate_limit_per_ip"), sing._IP_RATE_DEFAULT))]
    if device_id:
        budgets.append((f"dev:{device_id}", sing._safe_int(
            cfg.get("sing_rate_limit_per_device"), sing._DEVICE_RATE_DEFAULT)))
    now = time.monotonic()
    waits = []
    with sing._rate_limit_lock:
        for key, limit in budgets:
            live = [ts for ts in sing._rate_limit_state.get(key, ()) if ts >= now - window]
            if limit > 0 and len(live) >= limit:
                waits.append(live[len(live) - limit] + window - now)
    return max(1, math.ceil(max(waits))) if waits else window


# --- Ownership -------------------------------------------------------------

def owned_request_or_error(req_id):
    """The ``sing_requests`` row ``req_id`` when the caller's account owns it
    tonight, else a v1 error response: 404 ``not_found`` when it's missing,
    from another event code or from a previous night (indistinguishable, as
    on the web); 403 ``not_owner`` unless its ``device_id`` is one of
    ``g.account_devices`` (the app device or a web device linked tonight)."""
    store = current_app.sing_store
    req = store.get_request(req_id)
    if (req is None or req.get("token") != store.get_token()
            or not sing._belongs_to_current_night(store, req)):
        return v1_error("not_found", 404)
    if not req.get("device_id") or req["device_id"] not in g.account_devices:
        return v1_error("not_owner", 403)
    return req


def _linked_entry_status(req):
    if req.get("status") != "approved" or not req.get("linked_entry_id"):
        return ""
    rotation = getattr(current_app, "rotation", None)
    if rotation is None:
        return ""
    entry = rotation.store.get_entry(req["linked_entry_id"])
    return ((entry or {}).get("status") or "").lower()


def _request_view(view, for_friend):
    """A web request view for the app: never the edit_token; plus for_friend."""
    out = {k: v for k, v in (view or {}).items() if k != "edit_token"}
    out["for_friend"] = bool(for_friend)
    return out


def _clean_name(raw):
    return " ".join(raw.split()) if isinstance(raw, str) else ""


# --- POST /requests ----------------------------------------------------------

# The web /submit fields the app may send (device_id and singer_name are
# derived server-side).
_SUBMIT_FIELDS = ("phone", "song_artist", "song_title", "source_type", "source_ref",
                  "source_meta", "notes", "photo_consent", "instagram",
                  "additional_singers")
_BUSY_RETRY_AFTER_S = 2


def _replay(stored):
    return v1_json(stored["body"], stored.get("status", 200))


@app_api_bp.route("/requests", methods=["POST"])
@app_auth
def create_request():
    store = current_app.sing_store
    body = _json_body()
    raw_cid = body.get("client_request_id")
    try:
        cid = str(uuid.UUID(raw_cid)) if isinstance(raw_cid, str) else None
    except ValueError:
        cid = None
    if not cid:
        return v1_error("invalid", 400, "client_request_id (a UUID) is required")
    device = g.app_user.device_id
    key = f"{device}:{cid}"
    stored = store.idempotent_get(key)
    if stored is not None:
        return _replay(stored)

    for_friend = body.get("for_friend") is True
    if for_friend:
        singer_name = _clean_name(body.get("friend_name"))
        if not singer_name:
            return v1_error("invalid", 400, "friend_name is required")
        if len(singer_name) > sing._MAX_SINGER_NAME_LEN:
            return v1_error("invalid", 400, "friend_name is too long")
    else:
        singer_name = store.get_alias(device)
        if not singer_name:
            return v1_error("needs_name", 400)

    if not store.idempotent_claim(key):
        stored = store.idempotent_get(key)
        if stored is not None:
            return _replay(stored)
        return v1_error("busy_retry", 409, "this request is still being sent",
                        retry_after=_BUSY_RETRY_AFTER_S)

    done = False
    try:
        fields = {k: body[k] for k in _SUBMIT_FIELDS if k in body}
        fields.update(singer_name=singer_name, for_friend=for_friend)
        result, status = delegate(sing.submit, method="POST", path="/sing/submit",
                                  json=fields)
        if status != 200 or not isinstance(result.get("request"), dict):
            return map_web_error(result, status, device)
        # The request exists now: from here on the claim must never be
        # released, or a retry would create a second one. If storing the
        # response fails, the claim stays pending until its TTL (retries get
        # busy_retry meanwhile) rather than duplicating the song.
        done = True
        req = result["request"]
        payload = {"request": _request_view(req, for_friend),
                   "auto_approved": bool(result.get("auto_approved"))}
        try:
            store.idempotent_put(key, {"status": 200, "body": payload})
        except Exception:
            logger.exception("v1 submit: storing the idempotent response failed")
        if for_friend:
            try:
                store.mark_friend_request(req["id"], singer_name)
            except Exception:
                logger.exception("v1 submit: marking request %s as a friend song failed",
                                 req["id"])
        return v1_json(payload)
    finally:
        if not done:
            store.idempotent_release(key)   # a failed attempt mustn't block the retry


# --- POST /requests/<id>/cancel ----------------------------------------------

@app_api_bp.route("/requests/<int:id>/cancel", methods=["POST"])
@app_auth
def cancel_request(id):  # noqa: A002 — the contract's path parameter is {id}
    req_id = id
    store = current_app.sing_store
    req = owned_request_or_error(req_id)
    if isinstance(req, Response):
        return req
    friend = store.is_friend_request(req_id)
    if req["status"] == "cancelled":   # a retried cancel already took effect
        return v1_json({"request": _request_view(sing._public_request_view(req), friend)})
    entry_status = _linked_entry_status(req)
    if entry_status in ("done", "left"):
        return v1_error("already_sung", 409)
    if entry_status == "now singing":
        return v1_error("on_stage", 409)
    result, status = delegate(
        sing.cancel_request, method="POST", path=f"/sing/requests/{req_id}/cancel",
        json={"edit_token": req.get("edit_token")}, device_id=req["device_id"],
        view_args={"req_id": req_id})
    if status != 200:
        return map_web_error(result, status, req["device_id"])
    return v1_json({"request": _request_view(result.get("request"), friend)})


# --- POST /requests/<id>/change ----------------------------------------------

_CHANGE_FIELDS = ("source_type", "source_ref", "source_meta", "song_artist", "song_title")


@app_api_bp.route("/requests/<int:id>/change", methods=["POST"])
@app_auth
def change_request(id):  # noqa: A002 — the contract's path parameter is {id}
    req_id = id
    store = current_app.sing_store
    req = owned_request_or_error(req_id)
    if isinstance(req, Response):
        return req
    entry_status = _linked_entry_status(req)
    if entry_status in ("done", "left"):
        return v1_error("already_sung", 409)
    if entry_status == "now singing":
        return v1_error("on_stage", 409)
    body = _json_body()
    fields = {k: body[k] for k in _CHANGE_FIELDS if k in body}
    fields["edit_token"] = req.get("edit_token")
    result, status = delegate(
        sing.change_request, method="POST", path=f"/sing/requests/{req_id}/change",
        json=fields, device_id=req["device_id"], view_args={"req_id": req_id})
    if status != 200 or not isinstance(result.get("request"), dict):
        return map_web_error(result, status, req["device_id"])
    new = result["request"]
    friend = store.is_friend_request(req_id)
    if friend and new.get("id") != req_id:
        store.mark_friend_request(new["id"], new.get("singer_name") or "")
    return v1_json({"request": _request_view(new, friend),
                    "auto_approved": bool(result.get("auto_approved"))})


# --- POST /requests/reorder --------------------------------------------------

@app_api_bp.route("/requests/reorder", methods=["POST"])
@app_auth
def reorder_requests():
    """``items``: the singer's songs in the new order, each ``{id}`` (a request
    the account owns) or ``{entry_id}`` (a queued entry naming the singer that
    no request of theirs created: host-added or a partner's duet)."""
    store = current_app.sing_store
    items = _json_body().get("items")
    if not isinstance(items, list) or len(items) < 2:
        return v1_error("invalid", 400, "at least two items required")
    web_items = []
    entry_members = []
    for it in items:
        if not isinstance(it, dict) or ("id" in it) == ("entry_id" in it):
            return v1_error("invalid", 400, "each item needs exactly one of id or entry_id")
        if "id" in it:
            if not isinstance(it["id"], int) or isinstance(it["id"], bool):
                return v1_error("invalid", 400, "id must be an integer")
            req = owned_request_or_error(it["id"])
            if isinstance(req, Response):
                return req
            web_items.append({"id": req["id"], "edit_token": req.get("edit_token")})
            continue
        eid = it["entry_id"]
        if not isinstance(eid, int) or isinstance(eid, bool):
            return v1_error("invalid", 400, "entry_id must be an integer")
        rotation = getattr(current_app, "rotation", None)
        entry = rotation.store.get_entry(eid) if rotation is not None else None
        if entry is None:
            return v1_error("not_found", 404)
        entry_members.append([sing._fold_name(m) for m in sing._entry_members(entry)])
        web_items.append({"entry_id": eid})

    # {entry_id} items must name the singer. v1 proves that against the
    # account's own identity: its devices' aliases plus the names on its live,
    # non-friend requests tonight. The web's device identity also counts
    # friend-song and cancelled names, so a "song for Bob" (even cancelled)
    # would otherwise grant reorder rights over Bob's entries.
    device = g.app_user.device_id
    if entry_members:
        identity = _account_identity_folds(store)
        if not all(any(m in identity for m in members) for members in entry_members):
            return v1_error("not_owner", 403)
        # The web view then re-proves the items against ONE device's identity:
        # use the first of the account's devices (app device first) whose web
        # identity covers them all.
        devices = [device] + [d for d in g.account_devices if d != device]
        device = None
        for d in devices:
            identity = sing._device_identity_folds(store, d)
            if all(any(m in identity for m in members) for members in entry_members):
                device = d
                break
        if device is None:
            return v1_error("not_owner", 403)

    result, status = delegate(sing.reorder_requests, method="POST",
                              path="/sing/requests/reorder",
                              json={"items": web_items}, device_id=device)
    if status != 200 or not isinstance(result.get("request"), dict):
        return map_web_error(result, status, device)
    return v1_json({"request": _request_view(result["request"], False),
                    "auto_approved": bool(result.get("auto_approved"))})


def _account_identity_folds(store):
    """Folded names the caller's account is known as tonight: every account
    device's alias plus the singer names on its requests tonight that are
    neither friend songs nor cancelled/rejected."""
    names = list(store.account_request_names(g.account_devices, g.night_started_at))
    for d in g.account_devices:
        alias = sing._safe_call(lambda d=d: store.get_alias(d))
        if alias:
            names.append(alias)
    return {f for f in (sing._fold_name(n) for n in names) if f}


# --- PUT /requests/<id>/consent ----------------------------------------------

@app_api_bp.route("/requests/<int:id>/consent", methods=["PUT"])
@app_auth
def set_request_consent(id):  # noqa: A002 — the contract's path parameter is {id}
    req_id = id
    store = current_app.sing_store
    body = _json_body()
    if "consent" not in body or body["consent"] not in ("yes", "no", None):
        return v1_error("invalid", 400, "consent must be 'yes', 'no' or null")
    req = owned_request_or_error(req_id)
    if isinstance(req, Response):
        return req
    if store.is_friend_request(req_id):
        # The friend decides about their own song, not the phone's owner (A.94).
        return v1_error("invalid", 400, "consent for a friend's song is theirs to give")
    result, status = delegate(
        sing.song_consent, method="POST", path="/sing/song-consent",
        json={"consent": body["consent"],
              "item": {"id": req_id, "edit_token": req.get("edit_token")}},
        device_id=req["device_id"])
    if status != 200 or not isinstance(result.get("request"), dict):
        return map_web_error(result, status, req["device_id"])
    return v1_json({"request": _request_view(result["request"], False)})


# --- JSON errors for every /sing/api/v1/ path ------------------------------

_HTTP_ERROR_CODES = {404: "not_found", 405: "invalid", 409: "conflict", 429: "rate_limited"}


def _is_v1_path():
    return request.path.startswith(V1_PATH_PREFIX)


def install_v1_error_handlers(flask_app):
    """App-level handlers so unmatched v1 routes (404/405, including the public
    host guard's abort) and unhandled exceptions answer with the v1 error shape.
    A blueprint handler wouldn't see routing errors. Other paths are untouched."""

    @flask_app.errorhandler(HTTPException)
    def _v1_http_error(exc):
        if not _is_v1_path():
            return exc
        status = exc.code or 500
        if status >= 500:
            return v1_error("internal", status)
        return v1_error(_HTTP_ERROR_CODES.get(status, "invalid"), status)

    @flask_app.errorhandler(Exception)
    def _v1_unhandled(exc):
        if not _is_v1_path():
            raise exc
        # Path + method only: never the Authorization header or the body.
        logger.error("v1 %s %s failed", request.method, request.path,
                     exc_info=(type(exc), exc, exc.__traceback__))
        return v1_error("internal", 500)
