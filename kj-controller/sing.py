"""Public singer request blueprint (`/sing/*`).

Serves the QR-reachable request form. The companion admin endpoints at
`/rotation/requests/*` live on the KJ controller's main blueprint.

Design doc: docs/archive/2026-04-18-public-request-form-design.md
"""

import json
import os
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
    g,
    jsonify,
    render_template,
    request,
    send_from_directory,
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


# Singer self-service mutations (submit / cancel / change / reorder /
# update-phone / rename) share one budget. It used to be keyed purely by client
# IP, which on venue wifi is ONE address for the whole crowd — a group of six
# friends signing up together could lock the bar out for five minutes. Budget
# per device first (the SPA's localStorage device_id rides on every mutation),
# with a much looser per-IP ceiling as the backstop against a scripted flood
# that mints a new device_id per call.
_DEVICE_RATE_DEFAULT = 8
_IP_RATE_DEFAULT = 60


def _singer_rate_limited(req, data=None):
    """True when this request should 429. Consumes one slot on success."""
    cfg = current_app.kj_config or {}
    window = _safe_int(cfg.get("sing_rate_limit_window_s"), 300)
    ip_limit = _safe_int(cfg.get("sing_rate_limit_per_ip"), _IP_RATE_DEFAULT)
    dev_limit = _safe_int(cfg.get("sing_rate_limit_per_device"), _DEVICE_RATE_DEFAULT)
    if data is None:
        data = req.get_json(force=True, silent=True) or {}
    device_id = ""
    if isinstance(data, dict):
        device_id = str(data.get("device_id") or "").strip()[:64]
    ip = _client_ip(req)
    # Check BOTH budgets before recording either slot: a phone that has hit
    # its own limit must not keep burning the shared per-IP budget with every
    # rejected retry (that would eventually 429 everyone else at the venue).
    now = time.monotonic()
    cutoff = now - window
    keys = [(f"ip:{ip}", ip_limit)]
    if device_id:
        keys.append((f"dev:{device_id}", dev_limit))
    with _rate_limit_lock:
        for key, limit in keys:
            q = _rate_limit_state[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                return True
        for key, _limit in keys:
            _rate_limit_state[key].append(now)
    if device_id:
        # Remember exactly which slot this request took so a later 400 can
        # refund it without touching a concurrent request's timestamp.
        g.sing_rl_device_slot = (f"dev:{device_id}", now)
    return False


def _refund_device_rate_slot():
    """Give back the device slot a rejected (400) mutation consumed.

    A singer whose request fails validation will naturally retry; without this
    each retry burns their per-device budget and they end up locked out with
    "too many attempts" for a bug that isn't theirs. The per-IP slot is kept
    so a scripted flood of invalid payloads still hits the IP ceiling.
    """
    slot = g.pop("sing_rl_device_slot", None)
    if not slot:
        return
    key, ts = slot
    with _rate_limit_lock:
        q = _rate_limit_state.get(key)
        if q:
            try:
                q.remove(ts)
            except ValueError:
                pass  # already aged out of the window


def _safe_call(fn, default=None):
    """Call a store accessor that may not exist / may raise (older stores,
    test fixtures); template context must never 500 the singer page."""
    try:
        return fn()
    except Exception:
        return default


def _display_names(singer):
    """Every first name in a rotation singer string — "José Álvarez & Maria G."
    → "José & Maria" — so a duet partner can find themselves in the public
    rotation instead of seeing only the lead's name."""
    parts = [p.strip() for p in (singer or "").split("&")]
    firsts = [p.split()[0] for p in parts if p]
    return " & ".join(firsts)


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


# NOTE for anyone touching the singer SPA's asset URLs: on the public host the
# blueprint is mounted at `/` (see the rewriter below) but its static files are
# still served under `/sing/static/`. Derive asset paths from the running
# script's own URL (`import.meta.url`), never from the mount base — v0.108.0
# fetched `/static/messages/...` there and rendered raw i18n keys.
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
# Popular songs legitimately exceed 50 versions now that search surfaces every
# local copy (e.g. "I Want It That Way" = 60) — so an oversized snapshot is
# TRIMMED to its best-ranked _KJ_PICK_MAX_VERSIONS, never refused. Only a truly
# pathological payload (> _KJ_PICK_HARD_LIMIT) is rejected.
_KJ_PICK_MAX_VERSIONS = 50
_KJ_PICK_HARD_LIMIT = 1000


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
    if not all(isinstance(v, dict) for v in versions):
        return "kj_pick source_meta.versions[] entries must be objects"
    if len(versions) > _KJ_PICK_HARD_LIMIT:
        return (
            f"kj_pick too many versions ({len(versions)} > "
            f"{_KJ_PICK_HARD_LIMIT}) — refusing"
        )
    return None


def _trim_kj_pick_versions(meta, cfg):
    """Keep only the best-ranked ``_KJ_PICK_MAX_VERSIONS`` of a kj_pick snapshot.

    Uses the same ranking as auto-approve / the admin picker's ⭐ BEST, so the
    versions a KJ would actually choose are always the ones kept. Surviving
    versions stay in their original (search-time) order.
    """
    versions = meta.get("versions") or []
    if len(versions) <= _KJ_PICK_MAX_VERSIONS:
        return meta
    from routes import _ranked_version_indices

    keep = sorted(_ranked_version_indices(versions, cfg)[:_KJ_PICK_MAX_VERSIONS])
    return {**meta, "versions": [versions[i] for i in keep]}


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
    cfg = current_app.kj_config
    try:
        kj_name = _tip_settings(cfg, store).get("kj_name") or ""
    except Exception:
        kj_name = ""
    return render_template(
        "sing.html",
        closed=False,
        token=token,
        request_id=request.args.get("r", ""),
        vapid_public_key=cfg.get("vapid_public_key", ""),
        make_requests_enabled=store.is_accepting_make_requests(),
        simple_mode=store.is_simple_mode(),
        # Venue context for copy: phone-number example + branding strip.
        sms_region=(_safe_call(store.get_sms_default_region) or "US"),
        kj_name=kj_name,
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

    # A wider local-catalog cap than the KJ picker's 10: the grouped view ranks
    # songs by how many versions exist, and capping the FTS rows at 10 used to
    # truncate exactly the popular songs (10 local "Hallelujah" files for Jeff
    # Buckley alone) that singers are looking for. The local FTS is fast.
    data = unified_search(
        query, current_app._get_current_object(), grouped=True,
        catalog_limit=_safe_int(current_app.kj_config.get("sing_search_catalog_limit"), 60),
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


# --- Singer-facing version details + preview ------------------------------
# The public host (sing.nomadkaraoke.com) blocks every non-sing endpoint, so
# the version-picker's technical-details modal and preview player need their
# own token-gated routes here. They delegate to the same implementations the
# KJ UI uses (mediainfo probe, PreviewService) — no duplicated logic.

# KJ-static assets the singer preview modal reuses (preview player + CDG
# renderer + hls.js). Whitelist, never a raw path.
_LIB_FILES = {
    "preview.js": ("static", "preview.js"),
    "cdg.js": ("static", "cdg.js"),
    "hls.min.js": (os.path.join("static", "vendor"), "hls.min.js"),
}

# Preview transcodes can be expensive; keep one phone from hammering the box
# during a live show. Distinct bucket so it never eats the submit budget.
_preview_rate_limit_state = defaultdict(deque)

# Descriptor sources a singer can legitimately reach from their search
# results. "make"/"kj_pick" have nothing to stream; anything else is noise.
_SING_PREVIEW_SOURCES = {"local", "divebar", "youtube"}


@sing_bp.route("/lib/<name>", methods=["GET"])
@require_token
def lib_file(name):
    entry = _LIB_FILES.get(name)
    if not entry:
        abort(404)
    folder, fname = entry
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder)
    return send_from_directory(base, fname)


@sing_bp.route("/media-info", methods=["POST"])
@require_token
def media_info():
    """Technical details for a library file (format pill → details modal).

    Same path validation + ffprobe as the KJ's /media/info, but the on-disk
    path is withheld from the response — singers get the spec sheet, not the
    server's filesystem layout.
    """
    import mediainfo
    from routes import _resolve_media_path

    data = request.get_json(force=True, silent=True) or {}
    file_path = (data.get("file_path") or "").strip()
    if not file_path:
        return jsonify({"ok": False, "error": "file_path is required"}), 400
    real = _resolve_media_path(file_path)
    if not real:
        return jsonify({"ok": False, "error": "File not found"}), 404
    info = mediainfo.probe_media_info(real)
    info.pop("path", None)
    info["filename"] = os.path.basename(real)
    return jsonify(info)


def _entry_previewable(entry):
    """True when a rotation entry has a linked file present on disk."""
    if not entry:
        return False
    path = entry.get("file_path")
    if not path:
        return False
    try:
        return os.path.exists(path)
    except (TypeError, ValueError):
        return False


def _entry_preview_descriptor(entry_id):
    """Build a local preview descriptor for a rotation entry, or None."""
    try:
        entry_id = int(entry_id)
    except (TypeError, ValueError):
        return None
    rotation_mgr = getattr(current_app, "rotation", None)
    if rotation_mgr is None:
        return None
    entry = rotation_mgr.store.get_entry(entry_id)
    if not _entry_previewable(entry):
        return None
    return {
        "source": "local",
        "file_path": entry["file_path"],
        "title": entry.get("song_artist") or "",
    }


@sing_bp.route("/preview/resolve", methods=["POST"])
@require_token
def preview_resolve():
    cfg = current_app.kj_config
    limit = _safe_int(cfg.get("sing_preview_rate_limit"), 12)
    window = _safe_int(cfg.get("sing_preview_rate_window_s"), 60)
    if _rate_limit_exceeded(_client_ip(request), limit, window,
                            state=_preview_rate_limit_state):
        return jsonify({"mode": "unavailable",
                        "reason": "Too many previews — wait a moment"}), 429
    descriptor = request.get_json(silent=True) or {}
    if not isinstance(descriptor, dict):
        return jsonify({"mode": "unavailable", "reason": "Invalid request"}), 400
    # "entry" — preview a song already on tonight's rotation by its entry id
    # (My songs / Rotation tab). Resolved server-side so the file path never
    # leaves the box; only entries with a linked, present file qualify.
    if descriptor.get("source") == "entry":
        descriptor = _entry_preview_descriptor(descriptor.get("entry_id"))
        if descriptor is None:
            return jsonify({"mode": "unavailable",
                            "reason": "Not ready to preview yet"}), 404
    if descriptor.get("source") not in _SING_PREVIEW_SOURCES:
        return jsonify({"mode": "unavailable", "reason": "Invalid request"}), 400
    preview = getattr(current_app, "preview", None)
    if preview is None:
        return jsonify({"mode": "unavailable", "reason": "Preview not available"}), 503
    # Deliberately NOT recording preview stats — the KJ-side play/preview
    # counters mean "the KJ auditioned this file"; singer curiosity would
    # drown that signal.
    return jsonify(preview.resolve(descriptor))


@sing_bp.route("/preview/close", methods=["POST"])
@require_token
def preview_close():
    from routes import preview_close as _impl
    return _impl()


@sing_bp.route("/preview/stream/<tok>", methods=["GET"])
@require_token
def preview_stream(tok):
    from routes import preview_stream as _impl
    return _impl(tok)


@sing_bp.route("/preview/cdg/<tok>/<part>", methods=["GET"])
@require_token
def preview_cdg(tok, part):
    from routes import preview_cdg as _impl
    return _impl(tok, part)


@sing_bp.route("/preview/hls/<tok>/<path:name>", methods=["GET"])
@require_token
def preview_hls(tok, name):
    from routes import preview_hls as _impl
    return _impl(tok, name)


# --- Tipping (tip-for-heart priority) --------------------------------------
# The KJ configures payment handles in config.json; singers tip through their
# own payment app, then file a claim here. The claim rides the existing
# sing_requests queue (source_type="tip", like the "reorder" meta-request) so
# the KJ confirms it from the normal Requests panel — confirmation hearts the
# singer's entries and, at/above the threshold, applies the same +1 priority
# bump as the KJ-UI "bump up" button. Never auto-approved: the KJ should see
# the money arrive before priority changes.

# Zero-config fallback: the live tips page (Stripe + Cash App + Venmo +
# PayPal + Zelle) that already exists on the public website. Means tipping is
# ON out of the box; the KJ can disable or override from the Public Request
# Form modal.
_DEFAULT_TIP_PAGE_URL = "https://nomadkaraoke.com/tip"

_MAX_TIP_AMOUNT = 500

_tip_rate_limit_state = defaultdict(deque)


def _tip_settings(cfg, store):
    """Effective tip settings: KJ modal (rotation_meta) > config.json > defaults.

    Returns a plain dict with keys: enabled, kj_name, venmo, cashapp, paypal,
    zelle, stripe_url, threshold.
    """
    cfg = cfg or {}
    saved = {}
    if store is not None:
        try:
            saved = store.get_tip_settings()
        except Exception:
            saved = {}

    def pick(key, cfg_key, default=""):
        if key in saved:
            return saved[key]
        return cfg.get(cfg_key, default)

    enabled = saved.get("enabled")
    if enabled is None:
        enabled = cfg.get("sing_tips_enabled")
    threshold = pick("threshold", "sing_tip_priority_threshold", 20)
    try:
        threshold = max(0, float(threshold))
    except (TypeError, ValueError):
        threshold = 20
    return {
        "enabled": enabled,   # None = default-on
        "kj_name": str(pick("kj_name", "sing_tip_kj_name") or "").strip(),
        "venmo": str(pick("venmo", "sing_tip_venmo") or "").strip(),
        "cashapp": str(pick("cashapp", "sing_tip_cashapp") or "").strip(),
        "paypal": str(pick("paypal", "sing_tip_paypal") or "").strip(),
        "zelle": str(pick("zelle", "sing_tip_zelle") or "").strip(),
        "stripe_url": str(pick("stripe_url", "sing_tip_stripe_url") or "").strip(),
        "threshold": int(threshold) if float(threshold).is_integer() else threshold,
    }


def _tip_methods(settings, cfg=None):
    """Build the singer-facing method list from effective settings.

    amount_style tells the client how to deep-link a chosen amount:
    "path" appends /<amount> (Cash App, PayPal.me), "venmo" appends the
    Venmo pay-intent query, "copy" is a copy-to-clipboard value (Zelle),
    "none" opens the URL as-is (Stripe card link, tip page).
    """
    methods = []
    if settings["cashapp"]:
        methods.append({"key": "cashapp", "label": "CashApp",
                        "url": f"https://cash.app/${settings['cashapp'].lstrip('$')}",
                        "amount_style": "path"})
    if settings["venmo"]:
        methods.append({"key": "venmo", "label": "Venmo",
                        "url": f"https://venmo.com/{settings['venmo'].lstrip('@')}",
                        "amount_style": "venmo"})
    if settings["paypal"]:
        methods.append({"key": "paypal", "label": "PayPal",
                        "url": f"https://paypal.me/{settings['paypal']}",
                        "amount_style": "path"})
    if settings["zelle"]:
        methods.append({"key": "zelle", "label": "Zelle",
                        "value": settings["zelle"],
                        "amount_style": "copy"})
    if settings["stripe_url"].startswith("https://"):
        methods.append({"key": "stripe", "label": "Card",
                        "url": settings["stripe_url"],
                        "amount_style": "none"})
    # Legacy custom-URL config key still honoured (config.json only).
    custom = str((cfg or {}).get("sing_tip_url") or "").strip()
    if custom.startswith(("http://", "https://")):
        methods.append({
            "key": "custom",
            "label": str((cfg or {}).get("sing_tip_url_label") or "Tip link").strip(),
            "url": custom,
            "amount_style": "none",
        })
    if not methods:
        methods.append({
            "key": "page",
            "label": "Tip — card, Venmo, Cash App & more",
            "url": _DEFAULT_TIP_PAGE_URL,
            "amount_style": "none",
        })
    return methods


def _tips_enabled(settings):
    if settings["enabled"] is False:
        return False
    return True   # methods list always has at least the page fallback


@sing_bp.route("/tip-info", methods=["GET"])
@require_token
def tip_info():
    cfg = current_app.kj_config
    settings = _tip_settings(cfg, getattr(current_app, "sing_store", None))
    return jsonify({
        "enabled": _tips_enabled(settings),
        "threshold": settings["threshold"],
        "kj_name": settings["kj_name"],
        "methods": _tip_methods(settings, cfg),
    })


@sing_bp.route("/event-info", methods=["GET"])
@require_token
def event_info():
    """Venue context for the singer UI footer: the KJ's free-text message, the
    pre-built notices they've switched on (phone chargers, wifi, …), their
    social links, and whether to ask singers for photo/video consent."""
    store = getattr(current_app, "sing_store", None)
    footer = {"message": "", "notices": [], "social": {}, "ask_photo_consent": False}
    if store is not None:
        try:
            footer = store.get_footer_settings()
        except Exception:
            pass
    settings = _tip_settings(current_app.kj_config, store)
    return jsonify({
        "kj_name": settings.get("kj_name") or "",
        "footer_message": footer.get("message") or "",
        "notices": footer.get("notices") or [],
        "social": footer.get("social") or {},
        "ask_photo_consent": bool(footer.get("ask_photo_consent")),
    })


@sing_bp.route("/tip-claim", methods=["POST"])
@require_token
def tip_claim():
    cfg = current_app.kj_config
    store = current_app.sing_store
    if not _tips_enabled(_tip_settings(cfg, store)):
        return jsonify({"error": "tips_disabled"}), 400
    if _rate_limit_exceeded(_client_ip(request), 5, 600,
                            state=_tip_rate_limit_state):
        return jsonify({"error": "rate_limited"}), 429

    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "body must be a JSON object"}), 400
    singer_name = str(data.get("singer_name") or "").strip()
    device_id = str(data.get("device_id") or "").strip()[:64]
    method = str(data.get("method") or "").strip()[:32]
    if not singer_name:
        return jsonify({"error": "singer_name is required"}), 400
    try:
        amount = round(float(data.get("amount")), 2)
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a number"}), 400
    if not (0 < amount <= _MAX_TIP_AMOUNT):
        return jsonify({"error": f"amount must be between 0 and {_MAX_TIP_AMOUNT}"}), 400

    # Same device-alias override as /submit — a KJ-corrected name wins.
    if device_id:
        canonical = store.get_alias(device_id)
        if canonical:
            singer_name = canonical

    # Phone is optional context for the KJ; silently drop a malformed one
    # rather than failing the claim (the money already moved).
    phone = (data.get("phone") or "").strip()
    if phone and not _PHONE_RE.match(phone):
        phone = ""

    req = store.create_request(
        singer_name=singer_name,
        phone=phone,
        song_artist="",
        song_title="",
        source_type="tip",
        source_ref=None,
        source_meta={"amount": amount, "method": method},
        notes=f"Tip claim: ${amount:g}" + (f" via {method}" if method else ""),
        user_agent=request.headers.get("User-Agent", "")[:500],
        device_id=device_id or None,
    )
    return jsonify({
        "request": {**_public_request_view(req), "edit_token": req.get("edit_token")},
    })


@sing_bp.route("/submit", methods=["POST"])
@require_token
def submit():
    """Create a new pending request (or auto-approve if configured)."""
    cfg = current_app.kj_config
    store = current_app.sing_store

    data = request.get_json(force=True, silent=True) or {}
    if _singer_rate_limited(request, data):
        return jsonify({"error": "rate_limited"}), 429

    def _reject(error):
        _refund_device_rate_slot()
        return jsonify({"error": error}), 400

    singer_name = (data.get("singer_name") or "").strip()
    device_id = (data.get("device_id") or "").strip()[:64]
    phone = (data.get("phone") or "").strip()
    song_artist = (data.get("song_artist") or "").strip()
    song_title = (data.get("song_title") or "").strip()
    source_type = (data.get("source_type") or "").strip()
    source_ref = data.get("source_ref") or None
    source_meta = data.get("source_meta") or None
    notes = (data.get("notes") or "").strip()
    photo_consent = data.get("photo_consent")
    if photo_consent not in (None, "", "yes", "no"):
        return _reject("photo_consent must be 'yes' or 'no'")
    additional_raw = data.get("additional_singers")
    additional, additional_err = _validate_additional_singers(additional_raw)
    if additional_err:
        return _reject(additional_err)

    if not singer_name:
        return _reject("singer_name is required")
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
        return _reject("phone format invalid")
    if source_type not in _ALLOWED_SOURCES:
        return _reject(f"source_type must be one of {sorted(_ALLOWED_SOURCES)}")
    if store.is_simple_mode() and source_type not in _SIMPLE_MODE_SOURCES:
        return _reject("simple_mode_disabled_source")
    if source_type in {"local", "divebar", "kn", "youtube"} and not source_ref:
        return _reject("source_ref is required for this source_type")
    if source_type == "make":
        # Phase C — the KJ can turn this feature off per-event when they're
        # too busy to do same-night lyrics reviews. Defence-in-depth against
        # a stale sing.js from before the toggle flipped.
        if not store.is_accepting_make_requests():
            return _reject("make_requests_disabled")
        if not (song_artist and song_title):
            return _reject("song_artist and song_title are required for make")
    if source_type == "kj_pick":
        err = _validate_kj_pick_payload(data)
        if err:
            return _reject(err)
        source_meta = _trim_kj_pick_versions(source_meta, cfg)
        if not (song_artist and song_title):
            return _reject("song_artist and song_title are required for kj_pick")

    # Duet-partner dedup: fold typed partner names onto tonight's canonical
    # singer spellings ("sara" → "Sarah B.") so the rotation's exact-string
    # identity doesn't sprout near-duplicate singers.
    if additional:
        additional = _canonicalize_partners(
            current_app._get_current_object(), additional, singer_name)

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

    # Social-media photo consent rides along with each request (the device
    # remembers the singer's choice), recorded against the canonical name so
    # the KJ's rotation shows it. Best-effort — never fail a song request.
    if photo_consent:
        try:
            store.set_photo_consent(singer_name, photo_consent, source="singer")
        except Exception:
            current_app.logger.exception("submit: photo consent write failed")

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
            "display_name": _display_names(singer),
            "song_artist": entry.get("song_artist") or "",
            "status": entry.get("status") or "",
            # Singers can ▶ preview any song that's already on the box (the
            # path itself is never exposed — see /preview/resolve "entry").
            "entry_id": entry.get("id"),
            "previewable": _entry_previewable(entry),
            "now_singing": est["now_singing"],
            "expected_s": est["expected_s"],
            "range_low_s": est["range_low_s"],
            "range_high_s": est["range_high_s"],
        })
    return jsonify({"entries": out, "spread_source": spread_source})


# --- Known-singer matching (duet partner dedup) ----------------------------
# Rotation identity is the exact singer-name string (fairness weave, stats,
# bias all match on it), so a partner typed as "sara" when "Sarah B." already
# sings tonight creates a phantom duplicate. Submissions canonicalize partner
# names against tonight's known singers; the confirm screen offers the same
# list as tap-to-add chips (the names are already public on the venue screen).

def _known_singer_names(app):
    """Every singer name known to tonight's event, first-seen casing kept.

    Sources: rotation entries (primary ``singer`` + every ``singers_json``
    member, any status — a Done singer is still a known person) and active
    sing requests (pending/approved primaries + their partners).
    """
    names = []
    seen = set()

    def add(name):
        n = (name or "").strip()
        if not n:
            return
        key = _fold_name(n)
        if key and key not in seen:
            seen.add(key)
            names.append(n)

    rotation_mgr = getattr(app, "rotation", None)
    if rotation_mgr is not None:
        try:
            for entry in rotation_mgr.get_rotation():
                raw = entry.get("singers_json")
                members = None
                if raw:
                    try:
                        members = json.loads(raw) if isinstance(raw, str) else raw
                    except (ValueError, TypeError):
                        members = None
                if not members:
                    # Duets typed by the KJ as one "Anya & Celeste" singer
                    # (no singers_json) are two people — list each, so the
                    # partner picker never offers a pair as a person.
                    members = _split_duet_name(entry.get("singer"))
                for n in members:
                    add(n)
        except Exception:
            current_app.logger.exception("known-singers: rotation scan failed")
    store = getattr(app, "sing_store", None)
    if store is not None:
        try:
            for status_filter in ("pending", "approved"):
                for req in store.list_requests(status=status_filter):
                    if not _belongs_to_current_night(store, req):
                        continue
                    add(req.get("singer_name"))
                    for p in (req.get("additional_singers") or []):
                        add(p.get("name"))
        except Exception:
            current_app.logger.exception("known-singers: request scan failed")
    return names


def _split_duet_name(name):
    """Split a KJ-typed duet label ("Anya & Celeste", "Cam + Taylor") into
    member names. Only '&' and '+' separate — 'and' can be part of a name."""
    parts = re.split(r"\s*[&+]\s*", (name or "").strip())
    return [p for p in (x.strip() for x in parts) if p]


def _fold_name(name):
    """Casefolded, accent-stripped, alnum+space form for name comparison."""
    import unicodedata
    decomposed = unicodedata.normalize("NFKD", name or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = "".join(c if (c.isalnum() or c.isspace()) else " " for c in stripped)
    return " ".join(cleaned.casefold().split())


def match_known_singer(typed, known_names, exclude=None):
    """Return the canonical known-singer spelling for ``typed``, or None.

    Match ladder (conservative — a wrong merge is worse than a duplicate):
      1. Exact folded equality.
      2. Typed name equals the FIRST NAME of exactly one known singer
         ("sarah" → "Sarah B.", but ambiguous across "Sarah B."/"Sarah K."
         stays unmatched).
      3. Whole-name typo: Damerau-Levenshtein distance within a
         length-scaled budget (1 edit for 4-6 chars, 2 for 7+; short names
         must match exactly) with the same first letter, against exactly one
         known singer.
    ``exclude`` (the requester's own name) never matches — a partner "who is
    the requester" is user error, not a dedup target.
    """
    typed_fold = _fold_name(typed)
    if not typed_fold:
        return None
    exclude_fold = _fold_name(exclude) if exclude else None
    candidates = [
        (n, _fold_name(n)) for n in known_names
        if _fold_name(n) and _fold_name(n) != exclude_fold
    ]
    for name, fold in candidates:
        if fold == typed_fold:
            return name
    first_name_hits = [
        name for name, fold in candidates
        if " " not in typed_fold and fold.split()[0] == typed_fold
    ]
    if len(first_name_hits) == 1:
        return first_name_hits[0]
    if len(first_name_hits) > 1:
        # Ambiguous first name ("sarah" with Sarah B. AND Sarah C.) — stop
        # here; the typo pass could otherwise "uniquely" pick whichever
        # variant happens to sit within edit distance. Wrong merge > dup.
        return None
    try:
        from rapidfuzz.distance import DamerauLevenshtein

        def _budget(n):
            if n < 4:
                return 0
            return 1 if n < 7 else 2

        typo_hits = [
            name for name, fold in candidates
            if fold[:1] == typed_fold[:1]
            and DamerauLevenshtein.distance(typed_fold, fold)
                <= _budget(max(len(typed_fold), len(fold)))
        ]
        if len(typo_hits) == 1:
            return typo_hits[0]
    except Exception:
        pass
    return None


def _canonicalize_partners(app, partners, self_name):
    """Rewrite each partner's name to tonight's canonical spelling when an
    unambiguous known singer matches. Unmatched names pass through verbatim."""
    if not partners:
        return partners
    known = _known_singer_names(app)
    out = []
    for p in partners:
        matched = match_known_singer(p.get("name"), known, exclude=self_name)
        out.append({**p, "name": matched} if matched else p)
    return out


@sing_bp.route("/singers", methods=["GET"])
@require_token
def known_singers():
    """Tonight's known singer names for the confirm screen's partner chips."""
    return jsonify({
        "singers": _known_singer_names(current_app._get_current_object()),
    })


@sing_bp.route("/my-stats", methods=["GET"])
@require_token
def my_stats():
    """Song-history inspiration for the search screen.

    Returns the named singer's past songs (from the KJ's play-stats DB,
    matched on normalized singer name — same matching the KJ Song Stats
    panel uses) plus the venue's overall top songs. Both are "what gets sung
    here" data that's already public on the venue screen; no phone numbers
    or per-person data beyond song titles and counts.
    """
    stats = getattr(current_app, "stats", None)
    if stats is None:
        return jsonify({"my_songs": [], "top_songs": []})
    name = (request.args.get("name") or "").strip()

    def slim(rows, with_last=False):
        out = []
        for r in rows or []:
            item = {
                "artist": r.get("artist") or "",
                "title": r.get("title") or "",
                "plays": r.get("plays") or 0,
            }
            if with_last:
                item["last_sung"] = r.get("last_sung")
            out.append(item)
        return out

    my_songs = []
    if name:
        try:
            my_songs = stats.singer_songs(name, limit=50)
        except Exception:
            current_app.logger.exception("my-stats: singer_songs failed")
    try:
        top = stats.top_songs(limit=10)
    except Exception:
        current_app.logger.exception("my-stats: top_songs failed")
        top = []
    return jsonify({
        "my_songs": slim(my_songs, with_last=True),
        "top_songs": slim(top),
    })


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
    # Host-cancelled entries stay in the rotation (visible to the KJ) but are
    # not the singer's queue slot any more — report them as removed, never
    # with a "#N in line" estimate.
    cancelled_ids = {e["id"] for e in entries
                     if (e.get("status") or "").lower() == "cancelled"}

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
            if linked in cancelled_ids:
                item["removed"] = True
            elif linked in active_ids:
                item["estimate"] = compute_estimate(
                    entries, linked, current_app.kj_config,
                )
                active_entry = next((e for e in entries if e["id"] == linked), None)
                item["previewable"] = _entry_previewable(active_entry)
            elif rotation_mgr is not None:
                # Not in the active queue — check whether it was sung (Done) or
                # the singer left, so the done screen files it under "Already
                # sung tonight" rather than showing a stale "in the queue".
                entry = rotation_mgr.store.get_entry(linked)
                status = ((entry or {}).get("status") or "").lower()
                if status in ("done", "left"):
                    performed = True
                else:
                    # Approved, but its entry is gone from the queue without
                    # being sung — the host cancelled or deleted it. Say so
                    # (the phone used to show "Added to the queue." forever).
                    item["removed"] = True
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

    if _singer_rate_limited(request):
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
    if _singer_rate_limited(request):
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
        source_meta = _trim_kj_pick_versions(source_meta, current_app.kj_config or {})

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
    if _singer_rate_limited(request):
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


@sing_bp.route("/update-phone", methods=["POST"])
def update_phone():
    """Singer adds/changes their contact number after submitting.

    The "you're up" SMS resolves the phone from the singer's own request rows
    (newest non-empty wins — see routes._resolve_sms_target), so writing the
    new number onto every request this device proves ownership of (via each
    request's edit_token) makes texting work retroactively for songs already
    in the queue. Future submissions carry the number via the client's stored
    state; the push subscription re-syncs client-side after this call.

    Body: ``{phone, device_id, items: [{id, edit_token}, ...]}``.
    """
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return jsonify({"error": "not_configured"}), 503

    if _singer_rate_limited(request):
        return jsonify({"error": "rate_limited"}), 429

    token = _extract_token()
    if not token or not _is_token_valid(store, token):
        return jsonify({"error": "not_open"}), 403

    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    if not phone or not _PHONE_RE.match(phone):
        return jsonify({"error": "phone format invalid"}), 400
    items = data.get("items") or []
    if not isinstance(items, list):
        return jsonify({"error": "items must be a list"}), 400

    updated = 0
    for item in items[:_MY_REQUESTS_MAX_IDS]:
        if not isinstance(item, dict):
            continue
        req = store.get_request(item.get("id"))
        if req is None or req.get("token") != token:
            continue
        if not _belongs_to_current_night(store, req):
            continue
        supplied = (item.get("edit_token") or "").strip()
        if not supplied or supplied != (req.get("edit_token") or ""):
            continue
        store.set_request_phone(req["id"], phone)
        updated += 1

    return jsonify({"success": True, "updated": updated})


@sing_bp.route("/photo-consent", methods=["POST"])
def photo_consent():
    """Singer changes their social-media photo/video consent after submitting.

    Ownership is proven the same way as /update-phone (each item's
    edit_token); the choice is recorded for the singer name on every verified
    tonight request. A device with no requests yet gets ``updated: 0`` — its
    choice is sent with its first /submit instead.

    Body: ``{consent: "yes"|"no", items: [{id, edit_token}, ...]}``.
    """
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return jsonify({"error": "not_configured"}), 503

    if _singer_rate_limited(request):
        return jsonify({"error": "rate_limited"}), 429

    token = _extract_token()
    if not token or not _is_token_valid(store, token):
        return jsonify({"error": "not_open"}), 403

    data = request.get_json(silent=True) or {}
    consent = data.get("consent")
    if consent not in ("yes", "no"):
        return jsonify({"error": "consent must be 'yes' or 'no'"}), 400
    items = data.get("items") or []
    if not isinstance(items, list):
        return jsonify({"error": "items must be a list"}), 400

    names = []
    for item in items[:_MY_REQUESTS_MAX_IDS]:
        if not isinstance(item, dict):
            continue
        req = store.get_request(item.get("id"))
        if req is None or req.get("token") != token:
            continue
        if not _belongs_to_current_night(store, req):
            continue
        stored = req.get("edit_token") or ""
        if not stored or not secrets.compare_digest(str(item.get("edit_token") or ""), str(stored)):
            continue
        key = store.photo_consent_key(req.get("singer_name"))
        if key and key not in {store.photo_consent_key(n) for n in names}:
            names.append(req["singer_name"])

    for name in names:
        store.set_photo_consent(name, consent, source="singer")
    return jsonify({"success": True, "updated": len(names)})


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

    if _singer_rate_limited(request):
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

    # The singer's photo-consent choice follows them to the new name.
    for old in verified_old_names:
        try:
            store.carry_photo_consent(old, new_name)
        except Exception:
            current_app.logger.exception("self-rename: photo consent carry failed")

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
        "display_name": _display_names(singer),
        "song_artist": entry.get("song_artist") or "",
    }


def _public_request_view(req):
    """Hide internal/PII fields from singer-facing responses."""
    view = {
        "id": req["id"],
        "singer_name": req["singer_name"],
        "song_artist": req["song_artist"],
        "song_title": req["song_title"],
        "source_type": req["source_type"],
        "status": req["status"],
        "created_at": req["created_at"],
        "linked_entry_id": req.get("linked_entry_id"),
        "additional_singers": req.get("additional_singers"),
        # A pending "change" names the request it will replace so the singer's
        # list can say "replaces X" instead of showing two unrelated songs.
        "supersedes_request_id": req.get("supersedes_request_id"),
    }
    # Tip claims: surface the claimed amount/method so the Tip tab can show
    # "$25 via Venmo — waiting for KJ" without exposing raw source_meta.
    if req.get("source_type") == "tip":
        meta_raw = req.get("source_meta")
        try:
            meta = meta_raw if isinstance(meta_raw, dict) else json.loads(meta_raw or "{}")
        except (TypeError, ValueError):
            meta = {}
        view["tip_amount"] = meta.get("amount")
        view["tip_method"] = meta.get("method")
    return view


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
