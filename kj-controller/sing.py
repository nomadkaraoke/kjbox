"""Public singer request blueprint (`/sing/*`).

Serves the QR-reachable request form. The companion admin endpoints at
`/rotation/requests/*` live on the KJ controller's main blueprint.

Design doc: docs/archive/2026-04-18-public-request-form-design.md
"""

import json
import re
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
    phone = (data.get("phone") or "").strip()
    song_artist = (data.get("song_artist") or "").strip()
    song_title = (data.get("song_title") or "").strip()
    source_type = (data.get("source_type") or "").strip()
    source_ref = data.get("source_ref") or None
    source_meta = data.get("source_meta") or None
    notes = (data.get("notes") or "").strip()

    if not singer_name:
        return jsonify({"error": "singer_name is required"}), 400
    # Phone is optional — KJs use it to text singers when they're up, but
    # singers can opt out. If supplied, the format must still parse so the
    # KJ doesn't waste time trying to dial garbage.
    if phone and not _PHONE_RE.match(phone):
        return jsonify({"error": "phone format invalid"}), 400
    if source_type not in _ALLOWED_SOURCES:
        return jsonify({"error": f"source_type must be one of {sorted(_ALLOWED_SOURCES)}"}), 400
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
    )

    auto_approved = False
    # Auto-approve skips kj_pick — the whole point is to defer version binding
    # to the KJ, so bypassing review would leave the rotation entry without a
    # file attached.
    if store.is_auto_approve() and source_type != "kj_pick":
        try:
            from routes import approve_sing_request

            entry_id = approve_sing_request(current_app._get_current_object(), req)
            store.mark_approved(req["id"], linked_entry_id=entry_id)
            req = store.get_request(req["id"])
            auto_approved = True
        except Exception:
            current_app.logger.exception("Auto-approve failed; keeping pending")

    return jsonify(
        {
            "request": _public_request_view(req),
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
    if req.get("token") != token:
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
        if (e.get("status") or "").lower() not in ("done", "left")
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
    }


def _public_queue_view(entries):
    """First-name-only view of the rotation for the expandable 'show upcoming' list."""
    out = []
    for entry in entries:
        if entry.get("status", "").lower() in {"done", "left"}:
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
