"""Public singer request blueprint (`/sing/*`).

Serves the QR-reachable request form. The companion admin endpoints at
`/rotation/requests/*` live on the KJ controller's main blueprint.

Design doc: docs/archive/2026-04-18-public-request-form-design.md
"""

import json
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    session,
    url_for,
)
from wait_estimate import compute_all_estimates, compute_estimate


sing_bp = Blueprint(
    "sing",
    __name__,
    url_prefix="/sing",
    template_folder="templates",
    static_folder="static-sing",
    static_url_path="/static",
)


# --- Rate limiter --------------------------------------------------------

# Separate buckets per concern: guessing the event token (`/validate`) should
# not eat into the legit-submission budget, and vice versa. Both are keyed by
# client IP within the same lock since contention is minimal.
_rate_limit_state = defaultdict(deque)
_validate_rate_limit_state = defaultdict(deque)
_rate_limit_lock = threading.Lock()


def _safe_int(value, default):
    """Best-effort int conversion, falling back to ``default`` on any error.

    Guards against malformed values in config.json (e.g. a string that won't
    parse) crashing the submission path.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# Peers we're willing to trust when they send CF-Connecting-IP / X-Forwarded-For.
# In production, both cloudflared and the Caddy reverse proxy terminate on
# loopback — other remote addresses must NOT be able to spoof these headers to
# bypass rate limiting.
_TRUSTED_PROXIES = frozenset({"127.0.0.1", "::1"})


def _client_ip(req):
    """Best-effort client IP.

    Only honours forwarded headers when the immediate peer is a trusted
    reverse proxy (cloudflared, Caddy) — otherwise an internet-origin request
    could spoof CF-Connecting-IP to escape the per-IP rate limit.
    """
    peer = req.remote_addr or ""
    if peer in _TRUSTED_PROXIES:
        forwarded = (
            req.headers.get("CF-Connecting-IP")
            or req.headers.get("X-Forwarded-For", "")
        )
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer or "unknown"


def _rate_limit_exceeded(ip, limit, window_s, state=_rate_limit_state):
    """Slide a window over timestamps in ``state[ip]``, mutate in place."""
    now = time.monotonic()
    with _rate_limit_lock:
        q = state[ip]
        cutoff = now - window_s
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        return False


# --- Token gate ----------------------------------------------------------

def _extract_token():
    """Pull token from query string, form, JSON body, or session cookie."""
    t = request.args.get("t")
    if t:
        return t
    if request.is_json:
        body = request.get_json(silent=True) or {}
        t = body.get("t")
        if t:
            return t
    t = request.form.get("t") if request.form else None
    if t:
        return t
    return session.get("sing_token")


def _is_token_valid(store, token):
    if not store.is_enabled():
        return False
    current = store.get_token()
    return bool(current) and current == token


def _belongs_to_current_night(store, req):
    """True if ``req`` was created during the current night.

    Token-match alone is NOT enough to scope a singer-facing read to "tonight":
    the event token is reused across nights — a New Rotation does not rotate it,
    and KJs pin a memorable code — so a returning singer's device still holds
    prior-night request ids that resolve under the still-current token. Mirror
    the night-scoping defense applied to phone/push resolution: only requests
    with ``created_at >= night_started_at`` count as tonight's. Fails CLOSED when
    the marker is unset (``ensure_night_started()`` guarantees it on boot)."""
    night_started = store.get_night_started_at()
    if not night_started:
        return False
    return (req.get("created_at") or "") >= night_started


def _unauthorised_response():
    """Return a 403 page for browsers, JSON for AJAX calls."""
    if request.accept_mimetypes.best == "application/json" or request.path != "/sing/":
        return jsonify({"error": "not_open"}), 403
    return (
        render_template(
            "sing_closed.html" if _template_exists("sing_closed.html") else "sing.html",
            closed=True,
        ),
        403,
    )


def _template_exists(name):
    try:
        current_app.jinja_env.get_template(name)
        return True
    except Exception:
        return False


def require_token(view):
    """Decorator that rejects requests without a valid enabled token."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        store = getattr(current_app, "sing_store", None)
        if store is None:
            return jsonify({"error": "not_configured"}), 503
        token = _extract_token()
        if not token or not _is_token_valid(store, token):
            return _unauthorised_response()
        session["sing_token"] = token  # remember for subsequent AJAX calls
        return view(*args, **kwargs)

    return wrapper


# --- Helpers for event URL -----------------------------------------------

def get_event_url(cfg, token, scope="public"):
    """Build the event URL (QR target) for the given scope.

    Public scope serves the singer UI at the host root via WSGI path rewrite
    (see ``install_public_host_rewriter``), so we emit ``<base>/?t=TOK`` — no
    ``/sing/`` segment for singers to read or type. Local scope still uses
    ``/sing/`` because the admin device serves its KJ controller at ``/``.
    """
    if scope == "local":
        base = (cfg.get("sing_local_url_base") or "").rstrip("/")
        if not base:
            # Fall back to the request's host if available (e.g. http://<lan-ip>)
            try:
                base = f"{request.scheme}://{request.host}"
            except RuntimeError:  # outside request context
                base = ""
        if not token:
            return f"{base}/sing/"
        return f"{base}/sing/?t={token}"

    base = cfg.get("sing_public_url_base", "https://sing.nomadkaraoke.com").rstrip("/")
    if not token:
        return f"{base}/"
    return f"{base}/?t={token}"


def sync_event_url_overlays(overlay_manager, url):
    """Update any qr_code overlay with `config.follow_event_url=True` to point at `url`.

    Returns number of overlays updated. Swallows errors — best-effort.
    """
    if overlay_manager is None:
        return 0
    updated = 0
    try:
        for overlay in overlay_manager.list_overlays():
            if overlay.get("type") != "qr_code":
                continue
            cfg = overlay.get("config") or {}
            if not cfg.get("follow_event_url"):
                continue
            new_cfg = dict(cfg)
            new_cfg["url"] = url
            overlay_manager.update_overlay(overlay["id"], {"config": new_cfg})
            updated += 1
    except Exception:
        pass
    return updated


# --- Host guard ----------------------------------------------------------

def _public_hosts(cfg):
    hosts = set()
    primary = (cfg.get("sing_public_host") or "").strip().lower()
    if primary:
        hosts.add(primary)
    aliases = cfg.get("sing_public_host_aliases") or []
    for h in aliases:
        if h:
            hosts.add(h.strip().lower())
    return hosts


def install_host_guard(flask_app):
    """Register a before_request hook that blocks non-`sing.*` endpoints on the public host."""

    @flask_app.before_request
    def _sing_host_guard():
        cfg = getattr(flask_app, "kj_config", None) or {}
        hosts = _public_hosts(cfg)
        if not hosts:
            return None
        incoming = (request.host or "").split(":")[0].lower()
        if incoming not in hosts:
            return None
        endpoint = request.endpoint or ""
        if endpoint.startswith("sing."):
            return None
        abort(404)

    return _sing_host_guard


def install_public_host_rewriter(flask_app):
    """Mount the sing blueprint at the ROOT of the public host.

    Internally the blueprint stays at ``/sing/`` so the admin host (nomadpc.local,
    kjbox.nomadkaraoke.com) keeps working unchanged — but on ``sing.nomadkaraoke.com``
    the ``/sing/`` prefix is ugly on a printed QR target and impossible to type. A
    tiny WSGI middleware prepends ``/sing`` to incoming paths on the public host so
    both ``/`` and ``/sing/`` hit the same routes.

    Must be installed BEFORE routing — hence a WSGI middleware rather than a
    ``before_request`` hook (those run after routing and can't change dispatch).
    """
    original_wsgi = flask_app.wsgi_app

    def _rewritten(environ, start_response):
        cfg = getattr(flask_app, "kj_config", None) or {}
        hosts = _public_hosts(cfg)
        if hosts:
            incoming = (environ.get("HTTP_HOST") or "").split(":")[0].lower()
            if incoming in hosts:
                path = environ.get("PATH_INFO", "/")
                if not (path == "/sing" or path.startswith("/sing/")):
                    environ["PATH_INFO"] = "/sing" + (path if path != "/" else "/")
        return original_wsgi(environ, start_response)

    flask_app.wsgi_app = _rewritten
    return _rewritten


# --- Routes --------------------------------------------------------------

_PHONE_RE = re.compile(r"^\+?[0-9 \-()]{7,20}$")
_ALLOWED_SOURCES = {"local", "divebar", "kn", "youtube", "make", "kj_pick"}
_SIMPLE_MODE_SOURCES = {"local", "divebar", "kn"}
_KJ_PICK_MAX_VERSIONS = 50  # refuse pathological snapshots (see Phase A §3a)


def _validate_kj_pick_payload(data):
    """Return an error string for a malformed kj_pick payload, or None.

    A kj_pick request defers version selection to the KJ at approval time — so
    the singer must submit the full candidate snapshot in ``source_meta.versions``
    and the server must round-trip it faithfully (stored as JSON on the
    ``sing_requests`` row). This validates the shape without introspecting
    individual version objects; the admin approval path (Phase A §4c) is
    responsible for translating a picked version into a concrete source ref.
    """
    meta = data.get("source_meta") or {}
    versions = meta.get("versions") or []
    if not isinstance(versions, list) or not versions:
        return "kj_pick requires source_meta.versions[]"
    if len(versions) > _KJ_PICK_MAX_VERSIONS:
        # Guardrail — if we hit this in practice, grouping normalization
        # missed a dedup opportunity and should be widened.
        return (
            f"kj_pick too many versions ({len(versions)} > "
            f"{_KJ_PICK_MAX_VERSIONS}) — refusing"
        )
    return None


_MAX_ADDITIONAL_SINGERS = 3


def _validate_additional_singers(raw):
    """Return (normalised_list, error_message). One of the two is None.

    `None` raw → (None, None) — solo request, no partners.
    `[]`        → ([], None) — explicit clear (treated as solo).
    Otherwise: list of dicts, each `{"name": <required>, "phone": <opt>}`.
    Length must be ≤ _MAX_ADDITIONAL_SINGERS. Names are .strip()-ed and
    must be non-empty. Phones, when present, must match _PHONE_RE.
    """
    if raw is None:
        return None, None
    if not isinstance(raw, list):
        return None, "additional_singers must be a list"
    if len(raw) > _MAX_ADDITIONAL_SINGERS:
        return None, (
            f"additional_singers: max {_MAX_ADDITIONAL_SINGERS} extras "
            f"(got {len(raw)})"
        )
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return None, f"additional_singers[{i}]: must be an object"
        name = (item.get("name") or "").strip()
        phone = (item.get("phone") or "").strip()
        if not name:
            return None, f"additional_singers[{i}]: name is required"
        if len(name) > 100:
            return None, f"additional_singers[{i}]: name too long"
        if phone and not _PHONE_RE.match(phone):
            return None, f"additional_singers[{i}]: phone format invalid"
        out.append({"name": name, "phone": phone})
    return out, None


@sing_bp.route("/", methods=["GET"])
def landing():
    """Singer-facing SPA entry point.

    Three states, same template:

    * ``closed=True`` — the KJ has disabled public requests entirely.
    * ``code_entry=True`` — requests are open, but the visitor hasn't supplied
      a valid event token. Shows the 4-digit code entry form.
    * default — valid token, render the main SPA.
    """
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return render_template("sing.html", closed=True), 503

    if not store.is_enabled():
        return render_template("sing.html", closed=True), 403

    token = _extract_token()
    if not token or not _is_token_valid(store, token):
        # Requests are open; invite the singer to enter the code shown on the
        # venue screen. Invalid-token paths flow here too, so brute-force
        # scanners don't learn anything from this response.
        bad_code = bool(token) and token != ""
        return render_template(
            "sing.html",
            code_entry=True,
            bad_code=bad_code,
        ), (400 if bad_code else 200)

    session["sing_token"] = token
    return render_template(
        "sing.html",
        closed=False,
        token=token,
        request_id=request.args.get("r", ""),
        vapid_public_key=current_app.kj_config.get("vapid_public_key", ""),
        make_requests_enabled=store.is_accepting_make_requests(),
        simple_mode=store.is_simple_mode(),
    )


@sing_bp.route("/validate", methods=["POST"])
def validate_code():
    """Check a singer-entered event code without leaking a response time signal.

    Rate-limited per IP (10 attempts per 5 minutes) so the 10 000-combo token
    space isn't brute-forceable from a single attacker. Legitimate singers who
    mistype one or two codes won't hit the limit.
    """
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return jsonify({"ok": False, "error": "not_configured"}), 503

    ip = _client_ip(request)
    if _rate_limit_exceeded(ip, 10, 300, state=_validate_rate_limit_state):
        return jsonify({"ok": False, "error": "rate_limited"}), 429

    data = request.get_json(silent=True) or {}
    code = (data.get("t") or "").strip()
    if not code or not _is_token_valid(store, code):
        return jsonify({"ok": False}), 400
    return jsonify({"ok": True})


@sing_bp.route("/manifest.json", methods=["GET"])
@require_token
def manifest():
    """Dynamic PWA manifest — start_url carries the current event token.

    On the public host (``sing.nomadkaraoke.com``) the singer UI lives at the
    host root, so ``start_url`` and ``scope`` are root-relative. On the admin
    host the same page is under ``/sing/``; detect which by inspecting the
    inbound request host.
    """
    token = _extract_token()
    cfg = getattr(current_app, "kj_config", None) or {}
    on_public_host = (request.host or "").split(":")[0].lower() in _public_hosts(cfg)
    base = "/" if on_public_host else "/sing/"
    return jsonify({
        "name": "Nomad Karaoke",
        "short_name": "Nomad",
        "description": "Request a song at the karaoke night.",
        "start_url": f"{base}?t={token}",
        "scope": base,
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#0a0a0a",
        "theme_color": "#ff4dcf",
        "icons": [
            {
                "src": url_for("sing.static", filename="icon-192.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": url_for("sing.static", filename="icon-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
        ],
    })


@sing_bp.route("/sw.js", methods=["GET"])
def service_worker():
    """Serve sw.js with the app version injected as the cache key.

    Bumping APP_VERSION (via the version file) automatically invalidates
    the shell cache so singers don't run stale assets after a deploy.

    Not token-gated — browsers fetch updates independent of token state.
    """
    import os
    from flask import make_response
    sw_path = os.path.join(sing_bp.static_folder, "sw.js")
    with open(sw_path, "r") as f:
        body = f.read()
    version = current_app.config.get("APP_VERSION", "dev")
    body = body.replace("__APP_VERSION__", version)
    resp = make_response(body)
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@sing_bp.route("/search", methods=["GET"])
@require_token
def search():
    """Thin wrapper over the shared unified search helper.

    Returns the grouped shape (``{songs: [...], karaoke_nerds_timeout}``) —
    one entry per unique song, each carrying a ``versions[]`` snapshot of all
    available candidates. See
    ``docs/archive/2026-04-23-song-selection-phase-a-design.md`` §1.
    """
    query = (request.args.get("q") or "").strip()
    if len(query) < 3:
        return jsonify({"error": "Query must be at least 3 characters"}), 400

    # Lazy import avoids a circular dependency at module import time.
    from routes import unified_search

    data = unified_search(
        query, current_app._get_current_object(), grouped=True,
    )
    # Phase C — carries the KJ's current "accept make requests" flag alongside
    # the results so the empty-state triage can show/hide card 2 without a
    # second round-trip. Cheap: one SQLite read already hot in the connection.
    store = current_app.sing_store
    response = {
        "songs": data["songs"],
        "make_requests_enabled": store.is_accepting_make_requests(),
        "simple_mode": store.is_simple_mode(),
    }
    if data.get("karaoke_nerds_timeout"):
        response["karaoke_nerds_timeout"] = True
    return jsonify(response)


@sing_bp.route("/submit", methods=["POST"])
@require_token
def submit():
    """Create a new pending request (or auto-approve if configured)."""
    cfg = current_app.kj_config
    store = current_app.sing_store

    limit = _safe_int(cfg.get("sing_rate_limit_per_ip"), 5)
    window = _safe_int(cfg.get("sing_rate_limit_window_s"), 300)
    ip = _client_ip(request)
    if _rate_limit_exceeded(ip, limit, window):
        return jsonify({"error": "rate_limited"}), 429

    data = request.get_json(force=True, silent=True) or {}
    singer_name = (data.get("singer_name") or "").strip()
    device_id = (data.get("device_id") or "").strip()[:64]
    phone = (data.get("phone") or "").strip()
    song_artist = (data.get("song_artist") or "").strip()
    song_title = (data.get("song_title") or "").strip()
    source_type = (data.get("source_type") or "").strip()
    source_ref = data.get("source_ref") or None
    source_meta = data.get("source_meta") or None
    notes = (data.get("notes") or "").strip()
    additional_raw = data.get("additional_singers")
    additional, additional_err = _validate_additional_singers(additional_raw)
    if additional_err:
        return jsonify({"error": additional_err}), 400

    if not singer_name:
        return jsonify({"error": "singer_name is required"}), 400
    # Device alias override — a KJ or the singer themselves may have renamed this
    # device's singer; the typed name (from the device's localStorage) is stale
    # until they refresh, so the canonical name wins. Keeps a renamed singer from
    # re-splitting into their old name every time they add another song.
    if device_id:
        canonical = store.get_alias(device_id)
        if canonical:
            singer_name = canonical
    # Phone is optional — KJs use it to text singers when they're up, but
    # singers can opt out. If supplied, the format must still parse so the
    # KJ doesn't waste time trying to dial garbage.
    if phone and not _PHONE_RE.match(phone):
        return jsonify({"error": "phone format invalid"}), 400
    if source_type not in _ALLOWED_SOURCES:
        return jsonify({"error": f"source_type must be one of {sorted(_ALLOWED_SOURCES)}"}), 400
    if store.is_simple_mode() and source_type not in _SIMPLE_MODE_SOURCES:
        return jsonify({"error": "simple_mode_disabled_source"}), 400
    if source_type in {"local", "divebar", "kn", "youtube"} and not source_ref:
        return jsonify({"error": "source_ref is required for this source_type"}), 400
    if source_type == "make":
        # Phase C — the KJ can turn this feature off per-event when they're
        # too busy to do same-night lyrics reviews. Defence-in-depth against
        # a stale sing.js from before the toggle flipped.
        if not store.is_accepting_make_requests():
            return jsonify({"error": "make_requests_disabled"}), 400
        if not (song_artist and song_title):
            return jsonify({"error": "song_artist and song_title are required for make"}), 400
    if source_type == "kj_pick":
        err = _validate_kj_pick_payload(data)
        if err:
            return jsonify({"error": err}), 400
        if not (song_artist and song_title):
            return jsonify({"error": "song_artist and song_title are required for kj_pick"}), 400

    req = store.create_request(
        singer_name=singer_name,
        phone=phone,
        song_artist=song_artist,
        song_title=song_title,
        source_type=source_type,
        source_ref=source_ref,
        source_meta=source_meta,
        notes=notes,
        additional_singers=additional,
        user_agent=request.headers.get("User-Agent", "")[:500],
        device_id=device_id or None,
    )

    auto_approved = False
    # Auto-approve also handles kj_pick: rather than deferring to the KJ, bind
    # the request to its highest-priority version (the same one the admin picker
    # marks ⭐ BEST) so a rotation entry with a real file is created. Any failure
    # to resolve a version keeps the request pending for manual review.
    if store.is_auto_approve():
        try:
            from routes import approve_sing_request, resolve_kj_pick_best

            to_approve = req
            if source_type == "kj_pick":
                to_approve = resolve_kj_pick_best(
                    current_app._get_current_object(), req, cfg
                )
            entry_id = approve_sing_request(
                current_app._get_current_object(), to_approve
            )
            store.mark_approved(to_approve["id"], linked_entry_id=entry_id)
            req = store.get_request(req["id"])
            auto_approved = True
            # Auto-reorder if the KJ enabled it (best-effort; never fail the submit).
            from routes import maybe_auto_reorder
            maybe_auto_reorder(current_app._get_current_object())
        except Exception:
            current_app.logger.exception("Auto-approve failed; keeping pending")

    return jsonify(
        {
            # edit_token is returned ONCE here so the submitting device can store
            # it for self-service (cancel/edit). It is intentionally absent from
            # _public_request_view (used by /my-requests and /status).
            "request": {**_public_request_view(req), "edit_token": req.get("edit_token")},
            "auto_approved": auto_approved,
        }
    )


@sing_bp.route("/push/subscribe", methods=["POST"])
@require_token
def push_subscribe():
    """Persist a Web Push subscription for the current event token + singer."""
    store = current_app.sing_store
    token = _extract_token()
    data = request.get_json(force=True, silent=True) or {}
    phone = (data.get("phone") or "").strip()
    singer_name = (data.get("singer_name") or "").strip()
    sub = data.get("subscription") or {}
    endpoint = (sub.get("endpoint") or "").strip()
    keys = sub.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth_key = (keys.get("auth") or "").strip()

    if not (phone and singer_name and endpoint and p256dh and auth_key):
        return jsonify({"error": "missing fields"}), 400
    if not _PHONE_RE.match(phone):
        return jsonify({"error": "phone format invalid"}), 400

    user_agent = request.headers.get("User-Agent", "")[:500]
    store.insert_push_subscription(
        token=token, phone=phone, singer_name=singer_name,
        endpoint=endpoint, p256dh=p256dh, auth=auth_key,
        user_agent=user_agent,
    )
    return ("", 204)


@sing_bp.route("/push/unsubscribe", methods=["POST"])
@require_token
def push_unsubscribe():
    """Soft-disable a subscription by endpoint for the current event token."""
    store = current_app.sing_store
    token = _extract_token()
    data = request.get_json(force=True, silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    if not endpoint:
        return jsonify({"error": "endpoint required"}), 400
    store.disable_push_subscription_by_endpoint(token, endpoint)
    return ("", 204)


@sing_bp.route("/telnyx/webhook", methods=["POST"])
def telnyx_webhook():
    """Inbound Telnyx webhook: delivery receipts + STOP/HELP opt-outs.

    Lives on the sing blueprint because the public host (sing.nomadkaraoke.com)
    only routes `sing.*` endpoints — this is the one publicly-reachable surface.
    Unauthenticated but Ed25519 signature-verified against TELNYX_PUBLIC_KEY
    (``sms_config['public_key']``); if that's unset the check fails closed.

    Always 200-acks recognised events so Telnyx won't retry. 401 only on a
    signature failure. Carrier-side STOP responses are auto-sent by Telnyx; we
    mirror the opt-out locally so the KJ send path refuses opted-out numbers.
    """
    import sms as sms_mod

    sms_cfg = getattr(current_app, "sms_config", None) or {}
    sms_store = getattr(current_app, "sms_store", None)

    raw_body = request.get_data(as_text=True)
    signature = request.headers.get("telnyx-signature-ed25519", "")
    timestamp = request.headers.get("telnyx-timestamp", "")

    if not sms_mod.verify_webhook_signature(
        sms_cfg.get("public_key"), signature, timestamp, raw_body,
    ):
        return jsonify({"error": "invalid signature"}), 401

    try:
        payload = json.loads(raw_body) if raw_body else {}
    except ValueError:
        return ("", 200)  # acked; malformed body, nothing to act on

    evt = sms_mod.parse_webhook_event(payload)
    if sms_store is None:
        return ("", 200)

    if evt["kind"] == "dlr" and evt["message_id"]:
        sms_store.update_status_by_telnyx_id(
            evt["message_id"], evt["status"] or "unknown", error=evt["error"],
        )
    elif evt["kind"] == "inbound":
        keyword = sms_mod.classify_inbound_keyword(evt["text"])
        phone = evt["from"]
        if phone and keyword == "stop":
            sms_store.record_opt_out(phone, keyword=(evt["text"] or "").strip()[:32])
        elif phone and keyword == "start":
            sms_store.clear_opt_out(phone)

    return ("", 200)


@sing_bp.route("/now", methods=["GET"])
@require_token
def now_playing():
    """Lightweight 'what's playing now' payload for the landing page widget."""
    rotation = getattr(current_app, "rotation", None)
    if rotation is None:
        return jsonify({"now_singing": None, "up_next": None, "queued_count": 0})
    _entries, _active, now_playing_dict = _build_now_playing(rotation)
    return jsonify(now_playing_dict)


@sing_bp.route("/rotation", methods=["GET"])
@require_token
def rotation():
    """Full active rotation with cumulative wait estimates per entry.

    Singer-facing landing page expander. Token-gated like /sing/now.
    Returns first-name + song + status + (expected_s, range_low_s,
    range_high_s) for every active (non-done/non-left) entry.
    """
    rotation_mgr = getattr(current_app, "rotation", None)
    if rotation_mgr is None:
        return jsonify({"entries": [], "spread_source": "fallback"})

    entries, active, _np = _build_now_playing(rotation_mgr)
    estimates, spread_source = compute_all_estimates(entries, current_app.kj_config)

    out = []
    for entry, est in zip(active, estimates):
        singer = entry.get("singer") or ""
        out.append({
            "position": est["position"],
            "first_name": singer.split()[0] if singer else "",
            "song_artist": entry.get("song_artist") or "",
            "status": entry.get("status") or "",
            "now_singing": est["now_singing"],
            "expected_s": est["expected_s"],
            "range_low_s": est["range_low_s"],
            "range_high_s": est["range_high_s"],
        })
    return jsonify({"entries": out, "spread_source": spread_source})


@sing_bp.route("/status/<int:request_id>", methods=["GET"])
def status(request_id):
    """Return the singer's own request status.

    Requires a valid enabled event token AND the request row must belong to
    the current event (i.e. its stored token must match the active one).
    This prevents ID-guessing attacks and cross-event leakage after the KJ
    archives a rotation.
    """
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return jsonify({"error": "not_configured"}), 503

    token = _extract_token()
    if not token or not _is_token_valid(store, token):
        return jsonify({"error": "not_open"}), 403

    req = store.get_request(request_id)
    if req is None:
        return jsonify({"error": "not_found"}), 404

    # Scope the lookup to the current event — an old request from last week's
    # event must not be readable via today's token, even if the id is known.
    # Token-match is necessary but not sufficient: the token is reused across
    # nights, so also night-scope by created_at (see _belongs_to_current_night).
    if req.get("token") != token or not _belongs_to_current_night(store, req):
        return jsonify({"error": "not_found"}), 404

    response = {"request": _public_request_view(req)}

    rotation = getattr(current_app, "rotation", None)
    if rotation is not None:
        entries, _active, now_playing_dict = _build_now_playing(rotation)
        response["now_playing"] = now_playing_dict

        if req.get("linked_entry_id"):
            estimate = compute_estimate(entries, req["linked_entry_id"], current_app.kj_config)
            response["estimate"] = estimate
            response["queue"] = _public_queue_view(entries)

    return jsonify(response)


_MY_REQUESTS_MAX_IDS = 20


@sing_bp.route("/my-requests", methods=["GET"])
@require_token
def my_requests():
    """Multi-id status feed for the singer's 'your night' done screen.

    Returns the requested ids in order, dropping unknown ids, ids whose stored
    token differs from the current event token, AND prior-night ids (the token
    is reused across nights, so token-match alone leaks yesterday's songs into
    "tonight" — see _belongs_to_current_night). Matches /sing/status.
    `now_playing` is included once at the top level so the done screen doesn't
    need a second round trip to populate the header.
    """
    store = current_app.sing_store
    raw = request.args.get("ids", "") or ""
    pieces = [p for p in raw.split(",") if p.strip()]
    if len(pieces) > _MY_REQUESTS_MAX_IDS:
        return jsonify({"error": f"max {_MY_REQUESTS_MAX_IDS} ids per call"}), 400
    try:
        ids = [int(p) for p in pieces]
    except ValueError:
        return jsonify({"error": "ids must be integers"}), 400

    token = _extract_token()
    rotation_mgr = getattr(current_app, "rotation", None)

    entries = []
    now_playing_dict = {"now_singing": None, "up_next": None, "queued_count": 0}
    if rotation_mgr is not None:
        entries, _active, now_playing_dict = _build_now_playing(rotation_mgr)

    # A sing_request stays 'approved' even after its rotation entry has been
    # sung, so the done screen needs to know which songs are already performed
    # (Done/Left) to move them out of the active list. `entries` is the ACTIVE
    # queue only (get_rotation drops done/left), so a linked id that isn't in
    # it is either performed or gone — resolve those with a targeted lookup.
    active_ids = {e["id"] for e in entries}

    out = []
    for rid in ids:
        req = store.get_request(rid)
        # Drop unknown ids, foreign-token rows, and prior-night rows. The token
        # is reused across nights, so night-scope by created_at as well — else a
        # returning singer's stale localStorage ids leak into "tonight".
        if req is None or req.get("token") != token:
            continue
        if not _belongs_to_current_night(store, req):
            continue
        item = {"request": _public_request_view(req)}
        linked = req.get("linked_entry_id")
        performed = False
        if linked:
            if linked in active_ids:
                item["estimate"] = compute_estimate(
                    entries, linked, current_app.kj_config,
                )
            elif rotation_mgr is not None:
                # Not in the active queue — check whether it was sung (Done) or
                # the singer left, so the done screen files it under "Already
                # sung tonight" rather than showing a stale "in the queue".
                entry = rotation_mgr.store.get_entry(linked)
                status = ((entry or {}).get("status") or "").lower()
                if status in ("done", "left"):
                    performed = True
        item["performed"] = performed
        out.append(item)

    return jsonify({"now_playing": now_playing_dict, "requests": out})


@sing_bp.route("/requests/<int:req_id>/cancel", methods=["POST"])
def cancel_request(req_id):
    """Singer cancels their own request (proven by the per-request edit_token).

    Pending → marked cancelled (nothing downstream). Approved → the linked
    rotation entry is set to 'Cancelled' (visible to the KJ, excluded from the
    active queue selection) and the request is marked cancelled. The KJ can
    dismiss (delete) or restore (→ Waiting) from the rotation row.
    """
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return jsonify({"error": "not_configured"}), 503

    cfg = current_app.kj_config
    limit = _safe_int(cfg.get("sing_rate_limit_per_ip"), 5)
    window = _safe_int(cfg.get("sing_rate_limit_window_s"), 300)
    if _rate_limit_exceeded(_client_ip(request), limit, window):
        return jsonify({"error": "rate_limited"}), 429

    token = _extract_token()
    if not token or not _is_token_valid(store, token):
        return jsonify({"error": "not_open"}), 403

    req = store.get_request(req_id)
    # Night-scope + event-token match (mirror status()): a prior-night or
    # foreign request must be indistinguishable from a missing one.
    if req is None or req.get("token") != token or not _belongs_to_current_night(store, req):
        return jsonify({"error": "not_found"}), 404

    data = request.get_json(silent=True) or {}
    provided = data.get("edit_token") or ""
    stored = req.get("edit_token") or ""
    # Constant-time compare; empty stored token (legacy rows) can never match.
    if not stored or not secrets.compare_digest(str(provided), str(stored)):
        return jsonify({"error": "forbidden"}), 403

    if req["status"] in ("cancelled", "rejected"):
        return jsonify({"error": f"already {req['status']}"}), 409

    # If it reached the rotation, soft-cancel the linked entry (visible to KJ).
    # A sing_request stays 'approved' even after its entry is sung, so guard
    # against cancelling a finished song: flipping a Done/Left entry to
    # 'Cancelled' would resurrect it in the queue and corrupt sung-counts.
    if req["status"] == "approved" and req.get("linked_entry_id"):
        rotation = getattr(current_app, "rotation", None)
        if rotation is not None:
            entry = rotation.store.get_entry(req["linked_entry_id"])
            entry_status = ((entry or {}).get("status") or "").lower()
            if entry_status in ("done", "left"):
                # Already performed / gone — nothing to cancel.
                return jsonify({"error": "already_sung"}), 409
            try:
                rotation.update_status(req["linked_entry_id"], "Cancelled")
            except Exception:
                current_app.logger.exception("cancel: failed to soft-cancel entry")

    store.mark_cancelled(req_id)
    return jsonify({"success": True, "request": _public_request_view(store.get_request(req_id))})


@sing_bp.route("/requests/<int:req_id>/change", methods=["POST"])
def change_request(req_id):
    """Singer changes the SONG of their own request (edit_token-gated).

    Pending original → updated in place (stays pending). Approved original →
    a new pending request is created carrying supersedes_request_id so the KJ
    can approve the swap (see approve_sing_request_route)."""
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return jsonify({"error": "not_configured"}), 503
    cfg = current_app.kj_config
    if _rate_limit_exceeded(
        _client_ip(request),
        _safe_int(cfg.get("sing_rate_limit_per_ip"), 5),
        _safe_int(cfg.get("sing_rate_limit_window_s"), 300),
    ):
        return jsonify({"error": "rate_limited"}), 429
    token = _extract_token()
    if not token or not _is_token_valid(store, token):
        return jsonify({"error": "not_open"}), 403
    req = store.get_request(req_id)
    if req is None or req.get("token") != token or not _belongs_to_current_night(store, req):
        return jsonify({"error": "not_found"}), 404
    data = request.get_json(silent=True) or {}
    stored = req.get("edit_token") or ""
    if not stored or not secrets.compare_digest(str(data.get("edit_token") or ""), str(stored)):
        return jsonify({"error": "forbidden"}), 403
    if req["status"] not in ("pending", "approved"):
        return jsonify({"error": f"cannot change a {req['status']} request"}), 409
    # Only real song requests can be changed — never a meta-request (reorder).
    if req["source_type"] == "reorder":
        return jsonify({"error": "not a song request"}), 400
    # A sing_request stays 'approved' even after its entry is sung. Refuse to
    # change a finished/gone song (mirrors the cancel-after-sung guard): the
    # supersede takeover would delete the Done entry and corrupt sung-counts.
    if req["status"] == "approved" and req.get("linked_entry_id"):
        rotation = getattr(current_app, "rotation", None)
        if rotation is not None:
            entry = rotation.store.get_entry(req["linked_entry_id"])
            if entry and (entry.get("status") or "").lower() in ("done", "left"):
                return jsonify({"error": "already_sung"}), 409

    # Validate the new song's source (subset of submit()'s rules).
    source_type = (data.get("source_type") or "").strip()
    source_ref = data.get("source_ref") or None
    source_meta = data.get("source_meta") or None
    song_artist = (data.get("song_artist") or "").strip()
    song_title = (data.get("song_title") or "").strip()
    if source_type not in _ALLOWED_SOURCES:
        return jsonify({"error": f"source_type must be one of {sorted(_ALLOWED_SOURCES)}"}), 400
    if store.is_simple_mode() and source_type not in _SIMPLE_MODE_SOURCES:
        return jsonify({"error": "simple_mode_disabled_source"}), 400
    if source_type in {"local", "divebar", "kn", "youtube"} and not source_ref:
        return jsonify({"error": "source_ref is required for this source_type"}), 400
    if source_type == "make" and not store.is_accepting_make_requests():
        return jsonify({"error": "make_requests_disabled"}), 400
    if source_type == "kj_pick":
        err = _validate_kj_pick_payload(data)
        if err:
            return jsonify({"error": err}), 400

    if req["status"] == "pending":
        # update_request keeps existing values when a field is None, which would
        # preserve a stale source_ref when changing TO a null-ref source (e.g.
        # kj_pick). Set song fields via update_request, then overwrite the
        # source_* fields verbatim (incl. None) via update_request_source.
        store.update_request(req_id, song_artist=song_artist, song_title=song_title)
        updated = store.update_request_source(req_id, source_type, source_ref, source_meta)
        # edit_token echoed back (owner already holds it) so the device keeps
        # the same self-service capability after the change.
        return jsonify({"success": True, "request": {
            **_public_request_view(updated), "edit_token": updated.get("edit_token")}})

    # Approved → create a superseding pending request the KJ approves. Return
    # its fresh edit_token so the device can manage the new pending request too.
    new_req = store.create_request(
        singer_name=req["singer_name"], phone=req.get("phone") or "",
        song_artist=song_artist, song_title=song_title,
        source_type=source_type, source_ref=source_ref, source_meta=source_meta,
        token=req["token"], additional_singers=req.get("additional_singers"),
        supersedes_request_id=req_id,
        user_agent=request.headers.get("User-Agent", "")[:500],
        device_id=req.get("device_id"),
    )
    return jsonify({"success": True, "request": {
        **_public_request_view(new_req), "edit_token": new_req.get("edit_token")}})


@sing_bp.route("/requests/reorder", methods=["POST"])
def reorder_requests():
    """Singer reorders their OWN approved songs. Creates a pending 'reorder'
    request (KJ approves → move_entry). Every item must be owned (edit_token)."""
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return jsonify({"error": "not_configured"}), 503
    cfg = current_app.kj_config
    if _rate_limit_exceeded(
        _client_ip(request),
        _safe_int(cfg.get("sing_rate_limit_per_ip"), 5),
        _safe_int(cfg.get("sing_rate_limit_window_s"), 300),
    ):
        return jsonify({"error": "rate_limited"}), 429
    token = _extract_token()
    if not token or not _is_token_valid(store, token):
        return jsonify({"error": "not_open"}), 403
    data = request.get_json(silent=True) or {}
    items = data.get("items")
    if not isinstance(items, list) or len(items) < 2:
        return jsonify({"error": "at least two items required"}), 400

    ordered_entry_ids = []
    first_req = None
    seen_ids = set()
    for it in items:
        if not isinstance(it, dict):
            return jsonify({"error": "each item must be an object"}), 400
        try:
            rid = int(it.get("id"))
        except (TypeError, ValueError):
            return jsonify({"error": "each item needs an integer id"}), 400
        if rid in seen_ids:
            return jsonify({"error": "duplicate id"}), 400
        seen_ids.add(rid)
        req = store.get_request(rid)
        if req is None or req.get("token") != token or not _belongs_to_current_night(store, req):
            return jsonify({"error": "not_found"}), 404
        stored = req.get("edit_token") or ""
        if not stored or not secrets.compare_digest(str(it.get("edit_token") or ""), str(stored)):
            return jsonify({"error": "forbidden"}), 403
        if req["status"] != "approved" or not req.get("linked_entry_id"):
            return jsonify({"error": "each item must be an approved queued song"}), 409
        ordered_entry_ids.append(req["linked_entry_id"])
        if first_req is None:
            first_req = req

    rr = store.create_request(
        singer_name=first_req["singer_name"], phone="",
        source_type="reorder", source_ref=None,
        source_meta={"ordered_entry_ids": ordered_entry_ids},
        token=token,
        device_id=first_req.get("device_id"),
    )

    # Auto-approve applies the singer's reorder immediately — it only shuffles
    # their OWN entries within slots they already hold, so there's nothing for
    # the KJ to vet. Any failure keeps the reorder pending for manual review.
    auto_approved = False
    if store.is_auto_approve():
        try:
            from routes import apply_reorder_request

            apply_reorder_request(current_app._get_current_object(), rr)
            store.mark_approved(rr["id"], linked_entry_id=None)
            rr = store.get_request(rr["id"])
            auto_approved = True
        except Exception:
            current_app.logger.exception("Auto-approve reorder failed; keeping pending")

    return jsonify({
        "success": True,
        "request": _public_request_view(rr),
        "auto_approved": auto_approved,
    })


_MAX_SINGER_NAME_LEN = 100


@sing_bp.route("/rename", methods=["POST"])
def rename_me():
    """Singer renames THEMSELVES from the portal, persistently.

    Unlike the landing "switch" (which forgets the device identity and starts
    fresh), this keeps the device's ownership of its songs: it rewrites the
    singer's name on the rotation entries + requests it proves ownership of (via
    each request's edit_token), and records a device alias so EVERY future
    submission from this device resolves to the new name too. That's what makes
    the rename stick — the singer stops re-appearing under their old typed name.

    Body: ``{new_name, device_id, items: [{id, edit_token}, ...]}``. ``items``
    is the device's own request list (id + per-request secret) from localStorage;
    an empty list is valid (a singer with no live songs still sets their alias
    for future submissions).
    """
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return jsonify({"error": "not_configured"}), 503

    cfg = current_app.kj_config
    if _rate_limit_exceeded(
        _client_ip(request),
        _safe_int(cfg.get("sing_rate_limit_per_ip"), 5),
        _safe_int(cfg.get("sing_rate_limit_window_s"), 300),
    ):
        return jsonify({"error": "rate_limited"}), 429

    token = _extract_token()
    if not token or not _is_token_valid(store, token):
        return jsonify({"error": "not_open"}), 403

    data = request.get_json(silent=True) or {}
    new_name = (data.get("new_name") or "").strip()
    if not new_name:
        return jsonify({"error": "new_name is required"}), 400
    if len(new_name) > _MAX_SINGER_NAME_LEN:
        return jsonify({"error": "new_name too long"}), 400
    device_id = (data.get("device_id") or "").strip()[:64]
    # A persistent rename is meaningless without the device id — the alias is
    # what makes it stick to future submissions. The singer UI always sends one;
    # reject the request rather than silently doing a one-off (non-sticky) rename.
    if not device_id:
        return jsonify({"error": "device_id is required"}), 400

    items = data.get("items") or []
    if not isinstance(items, list):
        return jsonify({"error": "items must be a list"}), 400

    rotation = getattr(current_app, "rotation", None)
    # Group the entries we're allowed to rewrite by their current name so a
    # duet name is replaced precisely (rename_singer_in_entries is case-
    # insensitive on the old name). Only edit_token-verified, tonight, own-token
    # requests count — a device can never rename someone else's entries.
    entry_ids_by_old = {}
    verified_request_ids = []
    verified_old_names = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            rid = int(it.get("id"))
        except (TypeError, ValueError):
            continue
        req = store.get_request(rid)
        if req is None or req.get("token") != token or not _belongs_to_current_night(store, req):
            continue
        stored = req.get("edit_token") or ""
        provided = it.get("edit_token") or ""
        if not stored or not secrets.compare_digest(str(provided), str(stored)):
            continue
        verified_request_ids.append(rid)
        old = (req.get("singer_name") or "").strip()
        if old and old.lower() != new_name.lower():
            verified_old_names.add(old)
            if req.get("status") == "approved" and req.get("linked_entry_id"):
                entry_ids_by_old.setdefault(old, []).append(req["linked_entry_id"])

    # Rewrite the rotation entries. Two modes, decided per old-name:
    #
    #  • Established identity (a KJ merged/renamed this singer into ``old``):
    #    the singer is deliberately asserted to be ONE person, so a rename must
    #    carry the WHOLE name-group across the rotation — not just the songs this
    #    one device owns — else she re-splits under the stale name (the reported
    #    "Jasmine" / "Jasmine!" bug). We also migrate every device aliased to
    #    ``old`` and rewrite tonight's requests so no session reverts later.
    #  • Otherwise (a plain typed name, no merge): stay scoped to edit_token-owned
    #    entries so two coincidental same-name walk-ins never rename each other.
    night_started = None
    try:
        night_started = store.get_night_started_at()
    except Exception:
        current_app.logger.exception("self-rename: night lookup failed")

    for old in verified_old_names:
        try:
            # Escalate to a whole-group rename ONLY for a KJ-established identity
            # AND only when we have a night marker to scope the request rewrite —
            # without one, persist_rename would touch every historical request
            # under this name, so we fail closed to the safe edit_token-scoped
            # path rather than risk clobbering prior nights.
            if store.is_canonical_identity(old) and night_started:
                if rotation is not None:
                    rotation.rename_singer(old, new_name)
                store.persist_rename(old, new_name, night_started=night_started)
                store.remap_aliases(old, new_name)
            elif rotation is not None and old in entry_ids_by_old:
                rotation.rename_singer_in_entries(
                    old, new_name, entry_ids_by_old[old]
                )
        except Exception:
            current_app.logger.exception("self-rename: entry rewrite failed")

    # Rewrite the verified requests' stored name (keeps provenance + the done
    # screen consistent, and means a pending request is approved under the new
    # name).
    for rid in verified_request_ids:
        try:
            store.update_request(rid, singer_name=new_name)
        except Exception:
            current_app.logger.exception("self-rename: request rewrite failed")

    # Alias the device so future submissions resolve to the new name even before
    # the singer's localStorage catches up. The singer's own choice always wins
    # over any earlier KJ-set alias for this device. Best-effort — the entry and
    # request rewrites above already succeeded, so an alias-write failure must
    # not turn the whole rename into a 500.
    try:
        store.set_alias(device_id, new_name)
    except Exception:
        current_app.logger.exception("self-rename: alias write failed")

    return jsonify({"success": True, "new_name": new_name})


@sing_bp.route("/forget", methods=["POST"])
def forget_me():
    """Drop this device's canonical-name alias.

    Called when a device declares a NEW identity via the landing "switch" link —
    a different person on the same phone must not inherit the previous singer's
    KJ-corrected name. Best-effort; always 204 so the client never blocks on it.
    """
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return ("", 204)
    data = request.get_json(silent=True) or {}
    device_id = (data.get("device_id") or "").strip()[:64]
    if device_id:
        try:
            store.clear_alias(device_id)
        except Exception:
            current_app.logger.exception("forget_me: clear_alias failed")
    return ("", 204)


# --- Response shaping ----------------------------------------------------

def _build_now_playing(rotation):
    """Return (entries, active, now_playing_dict) for the /sing/now response body.

    `entries` is the full rotation list (useful to the caller for estimate
    computation). `active` is entries filtered to non-done/non-left (useful
    for `queued_count` and any further filtering). `now_playing_dict` has
    the three keys `now_singing`, `up_next`, `queued_count` matching the
    /sing/now response shape and ready to embed as a sub-object of any
    response.
    """
    entries = rotation.get_rotation()
    active = [
        e for e in entries
        # 'cancelled' stays visible to the KJ but must not appear in the
        # singer-facing now/next/queue counts.
        if (e.get("status") or "").lower() not in ("done", "left", "cancelled")
    ]
    now = next(
        (e for e in active if (e.get("status") or "").lower() == "now singing"),
        None,
    )
    nxt = next((e for e in active if e is not now), None)
    return entries, active, {
        "now_singing": _now_view(now),
        "up_next": _now_view(nxt),
        "queued_count": len(active),
    }


def _now_view(entry):
    """Minimal singer/song view for now_playing payloads."""
    if not entry:
        return None
    singer = entry.get("singer") or ""
    return {
        "first_name": singer.split()[0] if singer else "",
        "song_artist": entry.get("song_artist") or "",
    }


def _public_request_view(req):
    """Hide internal/PII fields from singer-facing responses."""
    return {
        "id": req["id"],
        "singer_name": req["singer_name"],
        "song_artist": req["song_artist"],
        "song_title": req["song_title"],
        "source_type": req["source_type"],
        "status": req["status"],
        "created_at": req["created_at"],
        "linked_entry_id": req.get("linked_entry_id"),
        "additional_singers": req.get("additional_singers"),
    }


def _public_queue_view(entries):
    """First-name-only view of the rotation for the expandable 'show upcoming' list."""
    out = []
    for entry in entries:
        if entry.get("status", "").lower() in {"done", "left", "cancelled"}:
            continue
        singer = entry.get("singer") or ""
        first_name = singer.split()[0] if singer else ""
        out.append(
            {
                "first_name": first_name,
                "song_artist": entry.get("song_artist", ""),
                "status": entry.get("status", ""),
            }
        )
    return out
