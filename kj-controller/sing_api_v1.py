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

import re
import logging
import math
import threading
import time
import urllib.parse
import uuid
from collections import defaultdict, deque
from functools import wraps

from flask import Blueprint, Response, current_app, g, jsonify, request
from werkzeug.exceptions import HTTPException

import sing
import sing_make
from app_identity import AppAuthError, suggested_name
from history_ids import DEFAULT_BOX_ID, history_id_for, night_id_for_marker

API_VERSION = "1.0"
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
    return night_id_for_marker(box_id, night)


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
            # Simple Mode refuses make-its too, so the app hides make (A.78).
            "make": bool(not store.is_simple_mode() and sing._safe_call(
                lambda: sing._make_enabled(store), False)),
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
    # gen refused the app's own session during a make-it call (A7-R3). The
    # app's session check decides whether that means signing out.
    "gen_unauthenticated": ("unauthenticated", 401),
    "quick_disabled": ("make_disabled", 400),
    "gen_unavailable": ("gen_unavailable", 503),
}
_STATUS_FALLBACK = {401: "unauthenticated", 404: "not_found", 409: "conflict",
                    429: "rate_limited"}


def map_web_error(body, status, device_id=None, overrides=None):
    """A v1 error response for a web view's error ``(body, status)``.
    ``overrides`` (web error -> (code, status)) take precedence over the table,
    for a caller whose context makes an error more specific."""
    err = body.get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, str) else ""
    mapped = (overrides or {}).get(err) or _WEB_ERROR_MAP.get(err)
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


def idempotent_call(store, key, work, ttl_s=None):
    """Run ``work()`` at most once per idempotency ``key`` (A4-R1 claim).

    ``work`` returns an error ``Response`` (the claim is released so a retry can
    run) or ``(build, after)``: ``build()`` makes the success payload, stored as
    the replayable 200 response, and ``after()`` is optional bookkeeping. The
    claim is marked done as soon as ``work`` returns (the effect exists), BEFORE
    ``build``/``after`` run, so a failure there can never release the claim and
    let a retry duplicate the effect; the claim then stays pending until its
    TTL (retries get busy_retry) and this call answers 500 internal.
    ``ttl_s`` overrides the claim's TTL (the store's 300 s default); it bounds
    how long ``work`` may run before a retry could start a second attempt.
    """
    stored = store.idempotent_get(key)
    if stored is not None:
        return _replay(stored)
    claimed = (store.idempotent_claim(key) if ttl_s is None
               else store.idempotent_claim(key, ttl_s=ttl_s))
    if not claimed:
        stored = store.idempotent_get(key)
        if stored is not None:
            return _replay(stored)
        return v1_error("busy_retry", 409, "this request is still being sent",
                        retry_after=_BUSY_RETRY_AFTER_S)
    done = False
    try:
        out = work()
        if isinstance(out, Response):
            return out
        done = True
        build, after = out
        payload = None
        try:
            payload = build()
            store.idempotent_put(key, {"status": 200, "body": payload})
        except Exception:
            logger.exception("v1: building or storing the idempotent response failed")
        if after is not None:
            try:
                after()
            except Exception:
                logger.exception("v1: post-processing after an idempotent call failed")
        if payload is None:
            return v1_error("internal", 500)
        return v1_json(payload)
    finally:
        if not done:
            store.idempotent_release(key)   # a failed attempt mustn't block the retry


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

    is_make = body.get("source_type") == "make"

    def work():
        fields = {k: body[k] for k in _SUBMIT_FIELDS if k in body}
        fields.update(singer_name=singer_name, for_friend=for_friend)
        result, status = delegate(sing.submit, method="POST", path="/sing/submit",
                                  json=fields)
        if status != 200 or not isinstance(result.get("request"), dict):
            return map_web_error(result, status, device,
                                 overrides=_MAKE_ERROR_OVERRIDES if is_make else None)
        req = result["request"]

        def build():
            return {"request": _request_view(req, for_friend),
                    "auto_approved": bool(result.get("auto_approved"))}

        def after():
            if for_friend:
                store.mark_friend_request(req["id"], singer_name)
        return build, after

    # A make-it submit waits on gen's create-job: the claim must outlive it, or
    # a retry after the claim expired would create a second gen job (A7-R2).
    ttl = sing_make.MAKE_SUBMIT_BUDGET_S + 60 if is_make else None
    return idempotent_call(store, key, work, ttl_s=ttl)


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


# --- GET /me -------------------------------------------------------------------
#
# "Your songs": the account's own requests tonight (from every linked device,
# never ids the client sends), the web's /my-requests view of each, plus queued
# entries naming the singer that the host typed in or a duet partner requested.

# Request kinds that ride the request table but aren't songs.
_NOT_SONGS = ("reorder", "tip")


def _owned_rows(store):
    """Tonight's song requests from the account's devices, oldest first."""
    rows = store.account_requests(g.account_devices, g.night_started_at, store.get_token())
    return [r for r in rows if r.get("source_type") not in _NOT_SONGS]


def _active_sort_key(item):
    """The web's ``_activeSortKey`` (A.49): on stage, then queue position, then
    approved without an estimate, then pending, then rejected or removed."""
    est = item.get("estimate") or {}
    if est.get("now_singing"):
        return -1
    if isinstance(est.get("position"), int):
        return est["position"]
    status = (item.get("request") or {}).get("status")
    if status == "pending":
        return 1e6
    if status == "rejected" or item.get("removed"):
        return 2e6
    return 1.5e6


def _requester_by_entry(night_rows):
    """{entry_id: requester singer_name} for tonight's approved requests."""
    return {r["linked_entry_id"]: r.get("singer_name") for r in night_rows
            if r.get("status") == "approved" and r.get("linked_entry_id")}


def _identity_owner(item):
    """Who put a song naming the singer on the list that no request of the
    account created: the host typed it in, another singer requested it (a
    duet), or the singer's own request from a device not linked to the
    account (the requester's name is the singer's own)."""
    if item.get("added_by_host"):
        return "host"
    return "partner" if item.get("added_by") else "web"


def _identity_items(store, entries, identity, already_linked, requesters):
    """Queued entries naming the singer (account identity, A5-R1) that none of
    the account's requests created."""
    if not identity or not entries:
        return []
    items = sing._identity_matched_items(store, entries, identity, store.get_token(),
                                         already_linked=already_linked,
                                         requester_by_entry=requesters)
    for it in items:
        it["request"].setdefault("media_consent", None)
        it["request"]["for_friend"] = False
        it["owner"] = _identity_owner(it)
    return items


_IDENTITY_OWNERS = ("host", "partner", "web")


def _attributable(store, item, identity):
    """True when an entry matched to the singer by name can safely count as
    theirs: no device outside the account claims any of the matching names
    tonight (A5-R10; ``SingStore.name_claimed_by_others``)."""
    entry_id = item.get("entry_id")
    rotation = getattr(current_app, "rotation", None)
    entry = rotation.store.get_entry(entry_id) if rotation is not None else None
    matched = {sing._fold_name(m) for m in sing._entry_members(entry or {})} & identity
    return bool(matched) and not any(
        store.name_claimed_by_others(f, g.account_devices, g.night_started_at)
        for f in matched)


def _sung_identity_items(identity, already_linked, requesters):
    """Tonight's Done entries naming the singer that none of the account's
    requests created (host- or partner-added songs the singer sang), as
    performed Your-songs items. Rotation entries are tonight's: a New Rotation
    archives the previous night's."""
    rotation = getattr(current_app, "rotation", None)
    if rotation is None or not identity:
        return []
    items = []
    for entry in rotation.store.get_completed_entries():
        eid = entry.get("id")
        if eid in already_linked or (entry.get("status") or "").lower() != "done":
            continue
        members = sing._entry_members(entry)
        if not any(sing._fold_name(m) in identity for m in members):
            continue
        requester = requesters.get(eid)
        item = {
            "request": {
                "id": None,
                "singer_name": entry.get("singer") or "",
                "song_artist": "",
                "song_title": entry.get("song_artist") or "",
                "source_type": "rotation",
                "status": "approved",
                "created_at": entry.get("created_at"),
                "linked_entry_id": eid,
                "additional_singers": [
                    {"name": m} for m in members if sing._fold_name(m) not in identity
                ] or None,
                "supersedes_request_id": None,
                "media_consent": None,
                "for_friend": False,
            },
            "entry_id": eid,
            "added_by_host": eid not in requesters,
            "added_by": (requester if requester
                         and sing._fold_name(requester) not in identity else None),
            "performed": True,
        }
        item["owner"] = _identity_owner(item)
        items.append(item)
    return items


def _unlinked_identity_count(store, identity, night_rows):
    """Tonight's live requests naming the singer from a device that isn't
    linked to any account (typically the singer's own web page before "Save
    tonight's songs"): drives the app's "open the web page" hint."""
    if not identity:
        return 0
    linked = {}
    count = 0
    for req in night_rows:
        device = req.get("device_id")
        if not device or device in g.account_devices:
            continue
        if req.get("source_type") in _NOT_SONGS or req.get("status") in ("cancelled", "rejected"):
            continue
        if sing._fold_name(req.get("singer_name")) not in identity:
            continue
        if device not in linked:
            linked[device] = store.account_for_device(device, g.night_started_at) is not None
        if not linked[device]:
            count += 1
    return count


def _me_profile(store, rows=None, friend_ids=None):
    name = store.get_alias(g.app_user.device_id)
    if rows is None:
        rows = _owned_rows(store)
        friend_ids = store.friend_request_ids([r["id"] for r in rows])
    phone = None
    for r in reversed(rows):
        if r["id"] not in friend_ids and (r.get("phone") or "").strip():
            phone = r["phone"].strip()
            break
    consent = {}
    if name:
        consent = store.get_photo_consents().get(store.photo_consent_key(name)) or {}
    return {"name": name or None, "phone": phone,
            "photo_consent": consent.get("consent"),
            "instagram": consent.get("instagram")}


def _next_turn(active):
    """The singer's next turn: the first own, host- or partner-added song with
    a queue estimate. Friend songs are the friend's turn (A.13)."""
    for it in active:
        est = it.get("estimate")
        if it["owner"] == "friend" or it.get("removed") or not est:
            continue
        if it["request"].get("status") in ("cancelled", "rejected"):
            continue
        if not isinstance(est.get("position"), int):
            continue
        return {"entry_id": it["request"].get("linked_entry_id") or it.get("entry_id"),
                "position": est["position"],
                "eta_s": int(est.get("expected_s") or 0),
                "range_low_s": int(est.get("range_low_s") or 0),
                "range_high_s": int(est.get("range_high_s") or 0)}
    return None


@app_api_bp.route("/me", methods=["GET"])
@app_auth
def get_me():
    store = current_app.sing_store
    token = store.get_token()
    # Everything below reads tonight only (night-scoped SQL), so the cost of a
    # 15 s poll doesn't grow with past nights under the reused event code.
    night_rows = store.night_requests(token, g.night_started_at)
    devices = set(g.account_devices)
    rows = [r for r in night_rows
            if r.get("device_id") in devices and r.get("source_type") not in _NOT_SONGS]
    friend_ids = store.friend_request_ids([r["id"] for r in rows])

    rotation = getattr(current_app, "rotation", None)
    entries = sing._build_now_playing(rotation)[0] if rotation is not None else []
    items = sing._my_request_items(
        store, [r["id"] for r in rows if r.get("status") != "cancelled"], token,
        rotation, entries)
    gen_jobs = {r["id"]: r.get("gen_job_id") for r in rows}
    for it in items:
        friend = it["request"]["id"] in friend_ids
        it["request"] = _request_view(it["request"], friend)
        it["owner"] = "friend" if friend else "me"
        if "make" in it:
            # A-R9: the app opens its native review of the account's own job.
            job = gen_jobs.get(it["request"]["id"])
            it["gen_job_id"] = str(job) if job else None

    identity = _account_identity_folds(store)
    already = {r.get("linked_entry_id") for r in rows if r.get("linked_entry_id")}
    requesters = _requester_by_entry(night_rows)
    items.extend(_identity_items(store, entries, identity, already, requesters))
    items.extend(_sung_identity_items(identity, already, requesters))

    box_id = (current_app.kj_config or {}).get("box_id") or DEFAULT_BOX_ID
    active, sung = [], []
    for it in items:
        if it.get("performed"):
            # A friend's song is the friend's performance, not this account's.
            if it["owner"] != "friend":
                entry_id = it["request"].get("linked_entry_id") or it.get("entry_id")
                it["history_id"] = history_id_for(box_id, entry_id, g.app_user.email)
                if it["owner"] in _IDENTITY_OWNERS and not _attributable(store, it, identity):
                    # Matched by name only, and someone else may have that name
                    # tonight: listed, but never recorded as this account's (A5-R10).
                    it["history_id"] = None
            sung.append(it)
        else:
            active.append(it)
    active.sort(key=_active_sort_key)
    return v1_json({
        "me": _me_profile(store, rows, friend_ids),
        "songs": active,
        "next": _next_turn(active),
        "sung_tonight": sung,
        "unlinked_identity_entries": _unlinked_identity_count(store, identity, night_rows),
    })


# --- PUT /me -------------------------------------------------------------------

def _first_name_fold(name):
    parts = (name or "").split()
    return sing._fold_name(parts[0]) if parts else ""


def _delegate_chunks(view, path, payload, items):
    """Run a web view that takes ``items: [{id, edit_token}]`` (capped at the
    web's id limit) over ``items`` in chunks; the summed ``updated``, or an
    error response."""
    chunk = sing._MY_REQUESTS_MAX_IDS
    updated = 0
    for i in range(0, len(items), chunk):
        body, status = delegate(view, method="POST", path=path,
                                json={**payload, "items": items[i:i + chunk]})
        if status != 200:
            return None, map_web_error(body, status, g.app_user.device_id)
        updated += int(body.get("updated") or 0)
    return updated, None


@app_api_bp.route("/me", methods=["PUT"])
@app_auth
def update_me():
    """Name (with the rename guard, A.6–A.11), phone and photo consent, applied
    to every song the account owns tonight except friend songs (A.13)."""
    store = current_app.sing_store
    body = _json_body()
    if not any(k in body for k in ("name", "phone", "photo_consent", "instagram")):
        return v1_error("invalid", 400, "nothing to change")
    confirm = body.get("confirm_rename", False)
    if not isinstance(confirm, bool):
        return v1_error("invalid", 400, "confirm_rename must be a boolean")

    name = None
    if "name" in body:
        name = _clean_name(body["name"])
        if not name:
            return v1_error("invalid", 400, "name is required")
        if len(name) > sing._MAX_SINGER_NAME_LEN:
            return v1_error("invalid", 400, "name is too long")
    phone = None
    if "phone" in body:
        phone = body["phone"].strip() if isinstance(body["phone"], str) else ""
        if not sing._PHONE_RE.match(phone):
            return v1_error("invalid", 400, "phone format invalid")
    consent = body.get("photo_consent")
    if "photo_consent" in body and consent not in ("yes", "no"):
        return v1_error("invalid", 400, "photo_consent must be 'yes' or 'no'")
    instagram = None
    if "instagram" in body:
        if body["instagram"] is not None and not isinstance(body["instagram"], str):
            return v1_error("invalid", 400, "instagram must be a string")
        try:
            instagram = store.normalize_instagram(body["instagram"])
        except ValueError as exc:
            return v1_error("invalid", 400, str(exc))

    device = g.app_user.device_id
    current = store.get_alias(device)
    if not current and not name:
        return v1_error("needs_name", 400)
    if "instagram" in body and consent is None:
        # A handle only means something with a "yes" (the web drops it otherwise).
        known = store.get_photo_consent(current) if current else None
        if known != "yes":
            return v1_error("invalid", 400, "instagram needs photo_consent 'yes'")
        consent = "yes"

    rows = _owned_rows(store)
    friend_ids = store.friend_request_ids([r["id"] for r in rows])
    own = [r for r in rows if r["id"] not in friend_ids and r.get("status") != "cancelled"]
    # Done/Left songs are history: a rename never rewrites them (A.7).
    unsung = [r for r in own if _linked_entry_status(r) not in ("done", "left")]

    if name and name != current:
        # Renaming a name the host set or merged is the host's call (A5-R8),
        # because the web rename would widen to a whole-group rename that
        # rewrites sung (Done) songs and other devices' songs. Refused only
        # when that could actually happen: a KJ alias set on one of the
        # account's devices tonight (aliases outlive the night), or a
        # KJ-established identity among the names the web rename would rewrite
        # (exactly its own escalation test). A case-only change rewrites
        # nothing and always goes through.
        if name.lower() != (current or "").lower():
            old_names = {r.get("singer_name").strip() for r in unsung
                         if (r.get("singer_name") or "").strip()
                         and r["singer_name"].strip().lower() != name.lower()}
            if (any(store.host_alias_tonight(d, g.night_started_at) for d in g.account_devices)
                    or any(store.is_canonical_identity(n) for n in old_names)):
                return v1_error("conflict", 409,
                                "the host set this name; ask the host to change it",
                                reason="host_named", **{"from": current or "", "to": name})
        queued = [r for r in unsung if r.get("status") in ("pending", "approved")
                  and _linked_entry_status(r) != "cancelled"]
        if (current and queued and not confirm
                and _first_name_fold(name) != _first_name_fold(current)):
            return v1_error("conflict", 409, "rename yourself, or request for a friend?",
                            reason="rename_guard", **{"from": current, "to": name})
        # One web rename per linked device: it rewrites that device's songs
        # (server-side edit_tokens) and its alias, so every device the account
        # sings from tonight resolves to the new name.
        devices = [device] + [d for d in g.account_devices if d != device]
        for dev in devices:
            dev_items = [{"id": r["id"], "edit_token": r.get("edit_token")}
                         for r in unsung if r.get("device_id") == dev]
            if dev != device and not dev_items and not store.get_alias(dev):
                continue
            result, status = delegate(sing.rename_me, method="POST", path="/sing/rename",
                                      json={"new_name": name, "items": dev_items},
                                      device_id=dev)
            if status != 200:
                return map_web_error(result, status, dev)
        if current:
            # The web rename carries photo consent only for names on the songs
            # it rewrote; with no songs the choice must still follow the name.
            try:
                store.carry_photo_consent(current, name)
            except Exception:
                logger.exception("v1 rename: carrying photo consent failed")
        rows = _owned_rows(store)
        own = [r for r in rows if r["id"] not in friend_ids and r.get("status") != "cancelled"]

    items = [{"id": r["id"], "edit_token": r.get("edit_token")} for r in own]
    if phone is not None and items:
        _n, error = _delegate_chunks(sing.update_phone, "/sing/update-phone",
                                     {"phone": phone}, items)
        if error is not None:
            return error
    if consent is not None:
        payload = {"consent": consent}
        if "instagram" in body:
            payload["instagram"] = instagram
        updated, error = _delegate_chunks(sing.photo_consent, "/sing/photo-consent",
                                          payload, items)
        if error is not None:
            return error
        alias = store.get_alias(device)
        if not updated and alias:
            # No songs yet: record the choice for the name now rather than
            # waiting for the first request (the web sends it with /submit).
            kwargs = {"instagram": instagram} if "instagram" in body and consent == "yes" else {}
            store.set_photo_consent(alias, consent, source="singer", **kwargs)

    profile = _me_profile(store)
    if phone is not None and not items:
        profile["phone"] = phone   # nothing to store it on yet; the app sends it on submit
    return v1_json({"me": profile})


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


# --- Read endpoints: search, singers, rotation, now, preview, tips, stats ----
# Each one delegates to the web view (same logic, same rate limits) with the
# app device injected, then reshapes the answer into the v1 envelope.

# Windows of the web limiters the read endpoints sit behind, for retry_after
# (the web's 429 body carries no timing).
_RESOLVE_WINDOW_S = 300
_TIP_WINDOW_S = 600


def _read_error(body, status, retry_s=None, device_id=None):
    if status == 429:
        return v1_error("rate_limited", 429, retry_after=retry_s or 60)
    if not isinstance(body, dict) or "error" not in body:
        reason = body.get("reason") if isinstance(body, dict) else None
        body = {"error": reason} if reason else {}
    return map_web_error(body, status, device_id)


def _read(view, path, args=None, *, retry_s=None, json=None, method="GET", keep=None):
    """Delegate; on 200 return ``v1_json`` of the body (limited to ``keep`` keys
    when given), else the mapped v1 error."""
    body, status = delegate(view, method=method, path=path, args=args, json=json)
    if status != 200:
        return _read_error(body, status, retry_s)
    if keep is not None:
        body = {k: v for k, v in body.items() if k in keep}
    return v1_json(body)


@app_api_bp.route("/search", methods=["GET"])
@app_auth
def search_songs():
    args = {"q": request.args.get("q", ""), "sid": request.args.get("sid", "")}
    return _read(sing.search, "/sing/search", args)


@app_api_bp.route("/search/identify", methods=["GET"])
@app_auth
def identify_song():
    args = {"q": request.args.get("q", ""), "sid": request.args.get("sid", "")}
    return _read(sing.search_identify, "/sing/search/identify", args, retry_s=60)


@app_api_bp.route("/search/resolve", methods=["GET"])
@app_auth
def resolve_search():
    args = {"q": request.args.get("q", ""), "sid": request.args.get("sid", "")}
    return _read(sing.search_resolve, "/sing/search/resolve", args, retry_s=_RESOLVE_WINDOW_S)


@app_api_bp.route("/search/event", methods=["POST"])
@app_auth
def log_search_event():
    body = _json_body()
    payload = {k: v for k, v in body.items() if k in ("sid", "action", "q", "data")}
    return _read(sing.search_event, "/sing/search/event", json=payload, method="POST",
                 retry_s=_RESOLVE_WINDOW_S, keep={"ok"})


@app_api_bp.route("/singers", methods=["GET"])
@app_auth
def list_singers():
    return _read(sing.known_singers, "/sing/singers")


@app_api_bp.route("/now", methods=["GET"])
@app_auth
def get_now():
    return _read(sing.now_playing, "/sing/now")


def _etag_matches(header, etag_value):
    """``If-None-Match`` against ``"<rev>"``, compared with the quotes (and any
    weak prefix) stripped; ``*`` matches anything."""
    for raw in (header or "").split(","):
        candidate = raw.strip()
        if candidate == "*":
            return True
        if candidate.startswith("W/"):
            candidate = candidate[2:]
        if candidate.strip('"') == etag_value:
            return True
    return False


@app_api_bp.route("/rotation", methods=["GET"])
@app_auth
def get_rotation():
    # The rev is read BEFORE the body is built: a change in between can only
    # make the ETag older than the body (an extra refetch), never newer.
    rev = _rotation_rev()
    etag = f'"{rev}"'
    if _etag_matches(request.headers.get("If-None-Match"), str(rev)):
        resp = Response(status=304)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "no-store"
        return resp
    body, status = delegate(sing.rotation, method="GET", path="/sing/rotation")
    if status != 200:
        return _read_error(body, status)
    resp = v1_json({"entries": body.get("entries", []),
                    "spread_source": body.get("spread_source", "fallback"), "rev": rev})
    resp.headers["ETag"] = etag
    return resp


def _preview_base():
    """Absolute base for the media URLs: the request's public host. On the
    public host the WSGI rewriter adds ``/sing``, so it is dropped there; on
    the admin host it stays. ``X-Forwarded-Proto`` wins over the (plain-HTTP)
    tunnel hop's scheme so the app is handed https."""
    cfg = current_app.kj_config or {}
    on_public = (request.host or "").split(":")[0].lower() in sing._public_hosts(cfg)
    proto = ""
    if (request.remote_addr or "") in sing._TRUSTED_PROXIES:   # as _client_ip
        proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
    base = request.host_url.rstrip("/")
    if proto in ("http", "https") and not base.startswith(proto + "://"):
        base = proto + base[base.index("://"):]
    return base + ("" if on_public else "/sing")


def _preview_payload(result):
    out = {"mode": result.get("mode") or "unavailable", "title": result.get("title") or ""}
    for key in ("reason", "format", "ext", "youtube_url"):
        if result.get(key) is not None:
            out[key] = result[key]
    tok = result.get("token")
    if tok and out["mode"] != "unavailable":
        out["token"] = tok
        base = _preview_base()
        q = "?t=" + urllib.parse.quote(_param("t") or "", safe="")
        if out["mode"] in ("native_video", "native_audio"):
            out["stream_url"] = f"{base}/preview/stream/{tok}{q}"
        elif out["mode"] == "hls":
            out["hls_url"] = f"{base}/preview/hls/{tok}/index.m3u8{q}"
        elif out["mode"] == "cdg":
            out["cdg_audio_url"] = f"{base}/preview/cdg/{tok}/audio{q}"
            out["cdg_graphics_url"] = f"{base}/preview/cdg/{tok}/graphics{q}"
    return out


@app_api_bp.route("/preview/resolve", methods=["POST"])
@app_auth
def resolve_preview():
    descriptor = {k: v for k, v in _json_body().items() if k not in ("t", "night_id", "device_id")}
    cfg = current_app.kj_config or {}
    window = sing._safe_int(cfg.get("sing_preview_rate_window_s"), 60)
    body, status = delegate(sing.preview_resolve, method="POST", path="/sing/preview/resolve",
                            json=descriptor)
    if status == 503:   # no preview service on this box: a normal "can't preview"
        return v1_json(_preview_payload({"mode": "unavailable", "reason": body.get("reason")}))
    if status != 200:
        return _read_error(body, status, window)
    return v1_json(_preview_payload(body))


@app_api_bp.route("/preview/close", methods=["POST"])
@app_auth
def close_preview():
    tok = _json_body().get("token")
    payload = {"token": tok} if isinstance(tok, str) else {}
    return _read(_preview_close_view, "/sing/preview/close", json=payload, method="POST",
                 keep={"ok"})


def _preview_close_view():
    return sing.preview_close()


@app_api_bp.route("/tips", methods=["GET"])
@app_auth
def get_tips():
    return _read(sing.tip_info, "/sing/tip-info")


@app_api_bp.route("/tips/claim", methods=["POST"])
@app_auth
def claim_tip():
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
    key = f"tip:{device}:{cid}"
    stored = store.idempotent_get(key)
    if stored is not None:
        return _replay(stored)
    alias = store.get_alias(device)
    if not alias:
        return v1_error("needs_name", 400, "join with a name before claiming a tip")
    # The name is always the device alias; the device id is injected by delegate.
    payload = {"singer_name": alias, "amount": body.get("amount"),
               "method": body.get("method"), "phone": body.get("phone")}

    def work():
        result, status = delegate(sing.tip_claim, method="POST", path="/sing/tip-claim",
                                  json=payload)
        if status != 200 or not isinstance(result.get("request"), dict):
            return _read_error(result, status, _TIP_WINDOW_S)
        return (lambda: {"request": _request_view(result["request"], False)}), None

    return idempotent_call(store, key, work)


@app_api_bp.route("/my-stats", methods=["GET"])
@app_auth
def get_my_stats():
    # Crowd favourites only. No `name` is forwarded, so the web view never
    # does a name-based history lookup, whatever the caller sent.
    return _read(sing.my_stats, "/sing/my-stats", keep={"top_songs"})


# --- POST /push-token (A10) ---------------------------------------------------

_EXPO_TOKEN_RE = re.compile(r"^Expo(?:nent)?PushToken\[[^\[\]\s]{1,200}\]$")


@app_api_bp.route("/push-token", methods=["POST"])
@app_auth
def register_push_token():
    body = _json_body()
    token = body.get("expo_push_token")
    platform = body.get("platform")
    if not isinstance(token, str) or not _EXPO_TOKEN_RE.match(token):
        return v1_error("invalid", 400, "expo_push_token is not an Expo push token")
    if platform not in ("ios", "android"):
        return v1_error("invalid", 400, "platform must be 'ios' or 'android'")
    current_app.sing_store.add_expo_subscription(g.app_user.device_id, token, platform)
    return v1_json({"ok": True})


# --- Make on the spot (A7) ------------------------------------------------
#
# The web make-it views run with the app device; sing_make._gen_account then
# acts as the app's own gen session (the v1 bearer), never a stored sign-in,
# so the job lands in the app user's gen account. The web views keep their
# rate limit (the make bucket, per device and per IP), the show-credit top-up
# and its per-device cap.

# Simple Mode refuses make-its: for a make call that's "make is off", not the
# generic source refusal it is for other sources.
_MAKE_ERROR_OVERRIDES = {"simple_mode_disabled_source": ("make_disabled", 400)}


_GEN_RATE_RETRY_S = 60


def _make_rate_limited(device):
    """429 for the make bucket, or gen's own limit (no timing given: a minute)."""
    retry = sing_make.rate_retry_after(device) or _GEN_RATE_RETRY_S
    return v1_error("rate_limited", 429, retry_after=retry)


def _make_error(body, status):
    device = g.app_user.device_id
    if status == 429 and (body or {}).get("error") != "make_limit":
        return _make_rate_limited(device)
    return map_web_error(body, status, device, overrides=_MAKE_ERROR_OVERRIDES)


def _make_call(view, path, fields):
    body = _json_body()
    payload = {k: body[k] for k in fields if k in body}
    result, status = delegate(view, method="POST", path=f"/sing/make/{path}", json=payload)
    if status != 200:
        return _make_error(result, status)
    return v1_json(result)


@app_api_bp.route("/make/check", methods=["POST"])
@app_auth
def check_make():
    return _make_call(sing_make.make_check, "check", ("artist", "title", "stage", "tier"))


@app_api_bp.route("/make/search", methods=["POST"])
@app_auth
def make_search():
    return _make_call(sing_make.make_search, "search", ("artist", "title"))


@app_api_bp.route("/make/validate-url", methods=["POST"])
@app_auth
def validate_make_url():
    return _make_call(sing_make.make_validate_url, "validate-url", ("url",))


@app_api_bp.route("/make/use-quick/<int:id>", methods=["POST"])
@app_auth
def use_quick_version(id):  # noqa: A002 — the contract's path parameter is {id}
    req_id = id
    req = owned_request_or_error(req_id)
    if isinstance(req, Response):
        return req
    result, status = delegate(
        sing_make.make_use_quick, method="POST", path=f"/sing/make/use-quick/{req_id}",
        json={"edit_token": req.get("edit_token")}, device_id=req["device_id"],
        view_args={"req_id": req_id})
    if status != 200:
        if status == 429:
            return _make_rate_limited(req["device_id"])
        return map_web_error(result, status, req["device_id"])
    return v1_json({"ok": True})


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
