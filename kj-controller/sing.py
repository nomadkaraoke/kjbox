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


sing_bp = Blueprint(
    "sing",
    __name__,
    url_prefix="/sing",
    template_folder="templates",
    static_folder="static-sing",
    static_url_path="/static",
)


# --- Rate limiter --------------------------------------------------------

_rate_limit_state = defaultdict(deque)
_rate_limit_lock = threading.Lock()


def _client_ip(req):
    """Best-effort client IP: honour the first Cloudflare-forwarded hop."""
    forwarded = req.headers.get("CF-Connecting-IP") or req.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return req.remote_addr or "unknown"


def _rate_limit_exceeded(ip, limit, window_s):
    """Slide a window over timestamps in `_rate_limit_state[ip]`, mutate in place."""
    now = time.monotonic()
    with _rate_limit_lock:
        q = _rate_limit_state[ip]
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
    """Build the event URL (QR target) for the given scope."""
    if scope == "local":
        base = (cfg.get("sing_local_url_base") or "").rstrip("/")
        if not base:
            # Fall back to the request's host if available (e.g. http://<lan-ip>)
            try:
                base = f"{request.scheme}://{request.host}"
            except RuntimeError:  # outside request context
                base = ""
    else:
        base = cfg.get("sing_public_url_base", "https://sing.nomadkaraoke.com").rstrip("/")
    if not token:
        return f"{base}/sing/"
    return f"{base}/sing/?t={token}"


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


# --- Routes --------------------------------------------------------------

_PHONE_RE = re.compile(r"^\+?[0-9 \-()]{7,20}$")
_ALLOWED_SOURCES = {"local", "divebar", "kn", "youtube", "make"}


@sing_bp.route("/", methods=["GET"])
def landing():
    """Singer-facing SPA entry point. Token required."""
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return render_template("sing.html", closed=True), 503

    token = _extract_token()
    if not token or not _is_token_valid(store, token):
        return render_template("sing.html", closed=True), 403

    session["sing_token"] = token
    return render_template(
        "sing.html",
        closed=False,
        token=token,
        request_id=request.args.get("r", ""),
    )


@sing_bp.route("/search", methods=["GET"])
@require_token
def search():
    """Thin wrapper over /rotation/search (local + KN + Divebar)."""
    query = (request.args.get("q") or "").strip()
    if len(query) < 3:
        return jsonify({"error": "Query must be at least 3 characters"}), 400

    local_results = []
    catalog = getattr(current_app, "catalog", None)
    if catalog is not None and catalog.is_available():
        local_results = catalog.search(query, limit=10)

    media = getattr(current_app, "media", None)
    if media is not None:
        for result in local_results:
            entry = media.index.get(result.get("path")) if hasattr(media, "index") else None
            if entry:
                result["duration"] = entry.get("duration")

    # KN + Divebar cross-ref — import lazily so tests without network deps still work
    kn_results = []
    try:
        import karaoke_nerds as kn

        kn_results = kn.search(query, current_app.kj_config) or []
    except Exception:
        kn_results = []

    return jsonify({"local": local_results, "karaoke_nerds": kn_results})


@sing_bp.route("/submit", methods=["POST"])
@require_token
def submit():
    """Create a new pending request (or auto-approve if configured)."""
    cfg = current_app.kj_config
    store = current_app.sing_store

    limit = int(cfg.get("sing_rate_limit_per_ip", 5))
    window = int(cfg.get("sing_rate_limit_window_s", 300))
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
    if not phone:
        return jsonify({"error": "phone is required"}), 400
    if not _PHONE_RE.match(phone):
        return jsonify({"error": "phone format invalid"}), 400
    if source_type not in _ALLOWED_SOURCES:
        return jsonify({"error": f"source_type must be one of {sorted(_ALLOWED_SOURCES)}"}), 400
    if source_type in {"local", "divebar", "kn", "youtube"} and not source_ref:
        return jsonify({"error": "source_ref is required for this source_type"}), 400
    if source_type == "make" and not (song_artist and song_title):
        return jsonify({"error": "song_artist and song_title are required for make"}), 400

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
    if store.is_auto_approve():
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


@sing_bp.route("/status/<int:request_id>", methods=["GET"])
def status(request_id):
    """Return the singer's own request status. No token required (id is the secret)."""
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return jsonify({"error": "not_configured"}), 503

    req = store.get_request(request_id)
    if req is None:
        return jsonify({"error": "not_found"}), 404

    response = {"request": _public_request_view(req)}

    rotation = getattr(current_app, "rotation", None)
    if rotation is not None and req.get("linked_entry_id"):
        entries = rotation.get_rotation()
        position = None
        estimated_wait_s = 0
        for i, entry in enumerate(entries):
            if entry.get("id") == req["linked_entry_id"]:
                position = i + 1  # 1-based from top
                break
            if entry.get("status", "").lower() not in {"done", "left"}:
                estimated_wait_s += int(entry.get("duration") or 240)
        response["position"] = position
        response["estimated_wait_s"] = estimated_wait_s
        response["queue"] = _public_queue_view(entries)
    return jsonify(response)


# --- Response shaping ----------------------------------------------------

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
