"""Singer "make it" flow — karaoke-gen's job submission, inside the singer UI.

The singer verifies their email with a 6-digit code (gen emails it), which
signs them in to a REAL karaoke-gen account; kjbox keeps that session token
server-side (``sing_gen_accounts``, per device) and proxies gen's own customer
endpoints with it, so the flow reuses gen's backend rather than re-implementing
it:

- ``/make/check``   → gen's match-judge (fixes lazily typed artist/title)
- ``/make/search``  → quiet +1 "show credit" top-up (Andrew pays for jobs made
                      at his shows) then gen's lossless-first audio search
                      (flacfetch → Spotify / YouTube fallbacks)
- ``/sing/submit`` (source_type=make) → gen's create-from-search /
                      create-from-url, owned by the singer (see ``create_job``)

Audio categorisation / best-pick / confidence tiers are gen-frontend logic and
are ported to ``static-sing/make.js``. Gen routes partner calls through
``/api/kjbox/*`` (X-Kjbox-Secret), which also exempts venue sign-ups from gen's
per-IP signup cap.
"""

import hashlib
import re
import secrets
import threading
import time
from collections import defaultdict, deque

from flask import current_app, jsonify, request

from gen_client import GenApiError
from sing import _client_ip, require_token, sing_bp

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CODE_RE = re.compile(r"^\d{6}$")

# Own budget (not the submit limiter): one make-it takes several calls
# (account, check ×2, search, maybe a re-search after a correction).
_MAKE_RATE_WINDOW_S = 600
_MAKE_RATE_PER_DEVICE = 40
_MAKE_RATE_PER_IP = 400
# Emailing codes is the abusable bit (inbox spam, gen's signup budget), so it
# gets a much tighter budget of its own — per device AND per venue IP.
_CODE_RATE_WINDOW_S = 3600
_CODE_RATE_PER_DEVICE = 5
_CODE_RATE_PER_IP = 40
_lock = threading.Lock()


def _state(app):
    """Per-app in-memory state: rate-limit windows + tonight's credited songs.

    Show-credit grants are tracked per device per night so a singer comparing
    a couple of spellings doesn't mint a credit each time (gen also caps show
    credits per user per day)."""
    return app.extensions.setdefault("sing_make", {
        "rate": defaultdict(deque),
        "credit_keys": defaultdict(set),   # (night, device) -> {song keys}
        "search_keys": {},                 # gen search_session_id -> credit key
        "last_key": {},                    # (night, device) -> latest credit key
    })


def make_flow_ready(app):
    """True when the singer make-it flow can run (gen + partner secret set)."""
    gen = getattr(app, "gen_client", None)
    return bool(gen is not None and gen.singer_flow_configured())


def _rate_limited(device_id, bucket="make", window=_MAKE_RATE_WINDOW_S,
                  per_ip=_MAKE_RATE_PER_IP, per_device=_MAKE_RATE_PER_DEVICE):
    now = time.monotonic()
    keys = [(f"{bucket}:ip:{_client_ip(request)}", per_ip)]
    if device_id:
        keys.append((f"{bucket}:dev:{device_id}", per_device))
    rate = _state(current_app._get_current_object())["rate"]
    with _lock:
        for key, limit in keys:
            q = rate[key]
            while q and q[0] < now - window:
                q.popleft()
            if len(q) >= limit:
                return True
        for key, _limit in keys:
            rate[key].append(now)
    return False


def _body():
    data = request.get_json(force=True, silent=True) or {}
    device_id = str(data.get("device_id") or request.args.get("device_id") or "").strip()[:64]
    return data, device_id


def _guard(device_id, need_account=True):
    """Common checks. Returns (account, error_response)."""
    app = current_app._get_current_object()
    store = app.sing_store
    if not (store.is_accepting_make_requests() and make_flow_ready(app)):
        return None, (jsonify({"error": "make_requests_disabled"}), 400)
    if store.is_simple_mode():
        return None, (jsonify({"error": "simple_mode_disabled_source"}), 400)
    # The device id selects the stored gen sign-in, so it must be the random
    # 32-hex id (sing.js falls back to a short guessable one without crypto).
    if not re.fullmatch(r"[0-9a-f]{32}", device_id or ""):
        return None, (jsonify({"error": "device_unsupported"}), 400)
    if _rate_limited(device_id):
        return None, (jsonify({"error": "rate_limited"}), 429)
    if not need_account:
        return None, None
    account = store.get_gen_account(device_id)
    if not account:
        return None, (jsonify({"error": "signin_required"}), 401)
    return account, None


def gen_error_response(exc, device_id=None):
    """Translate a GenApiError from a singer-session call into a JSON reply."""
    if exc.status == 401:
        # Session expired/revoked on gen — forget it; the singer re-verifies.
        if device_id:
            current_app.sing_store.clear_gen_account(device_id)
        return jsonify({"error": "signin_required"}), 401
    if exc.status == 402:
        return jsonify({"error": "no_credits"}), 402
    if exc.status == 429:
        return jsonify({"error": exc.detail if isinstance(exc.detail, str) and exc.detail
                        else "rate_limited"}), 429
    if exc.status in (400, 403, 422):
        current_app.logger.warning("make: gen rejected the request: %s", exc)
        return jsonify({"error": "gen_rejected"}), 400
    current_app.logger.warning("make: gen call failed: %s", exc)
    return jsonify({"error": "gen_unavailable"}), 502


def _locale(data):
    loc = str(data.get("locale") or "").strip()[:12]
    return loc or None


@sing_bp.route("/make/account", methods=["GET"])
@require_token
def make_account():
    device_id = str(request.args.get("device_id") or "").strip()[:64]
    account = current_app.sing_store.get_gen_account(device_id) if device_id else None
    return jsonify({
        "ready": make_flow_ready(current_app._get_current_object()),
        "email": account["email"] if account else None,
    })


@sing_bp.route("/make/send-code", methods=["POST"])
@require_token
def make_send_code():
    data, device_id = _body()
    _, err = _guard(device_id, need_account=False)
    if err:
        return err
    email = str(data.get("email") or "").strip().lower()[:254]
    if not _EMAIL_RE.match(email):
        return jsonify({"error": "email_invalid"}), 400
    if _rate_limited(device_id, bucket="code", window=_CODE_RATE_WINDOW_S,
                     per_ip=_CODE_RATE_PER_IP, per_device=_CODE_RATE_PER_DEVICE):
        return jsonify({"error": "too_many_codes"}), 429
    try:
        current_app.gen_client.send_login_code(
            email, locale=_locale(data), venue=current_app.kj_config.get("venue_name"))
    except GenApiError as exc:
        if exc.status == 422:
            return jsonify({"error": "email_invalid"}), 400
        if exc.status == 429:
            return jsonify({"error": exc.detail or "rate_limited"}), 429
        current_app.logger.warning("make: send-code failed: %s", exc)
        return jsonify({"error": "gen_unavailable"}), 502
    return jsonify({"status": "sent", "email": email})


@sing_bp.route("/make/verify-code", methods=["POST"])
@require_token
def make_verify_code():
    data, device_id = _body()
    _, err = _guard(device_id, need_account=False)
    if err:
        return err
    email = str(data.get("email") or "").strip().lower()[:254]
    code = re.sub(r"\s+", "", str(data.get("code") or ""))
    if not _EMAIL_RE.match(email) or not _CODE_RE.match(code):
        return jsonify({"error": "invalid_code"}), 400
    try:
        result = current_app.gen_client.verify_login_code(email, code, locale=_locale(data))
    except GenApiError as exc:
        if exc.status in (400, 401):
            return jsonify({"error": exc.detail if exc.detail in ("expired",) else "invalid_code"}), 400
        if exc.status == 429:
            return jsonify({"error": "too_many_attempts"}), 429
        current_app.logger.warning("make: verify-code failed: %s", exc)
        return jsonify({"error": "gen_unavailable"}), 502
    token = result.get("session_token")
    if not token:
        return jsonify({"error": "gen_unavailable"}), 502
    current_app.sing_store.set_gen_account(device_id, email, token)
    return jsonify({"email": email})


@sing_bp.route("/make/sign-out", methods=["POST"])
@require_token
def make_sign_out():
    _data, device_id = _body()
    if device_id:
        current_app.sing_store.clear_gen_account(device_id)
    return jsonify({"status": "ok"})


@sing_bp.route("/make/check", methods=["POST"])
@require_token
def make_check():
    """gen's match-judge: canonical artist/title for what the singer typed."""
    data, device_id = _body()
    account, err = _guard(device_id)
    if err:
        return err
    artist = str(data.get("artist") or "").strip()[:200]
    title = str(data.get("title") or "").strip()[:200]
    if not (artist and title):
        return jsonify({"error": "artist and title are required"}), 400
    stage = "full" if data.get("stage") == "full" else "fast"
    tier = data.get("tier") if data.get("tier") in (1, 2, 3) else None
    try:
        verdict = current_app.gen_client.match_judge(
            account["session_token"], artist, title, stage=stage, audio_confidence_tier=tier)
    except GenApiError as exc:
        if exc.status == 401:
            return gen_error_response(exc, device_id)
        # Matching is a nice-to-have (gen's UI fails open too).
        return jsonify({"kind": "none", "confident": False})
    return jsonify(verdict)


def _song_key(artist, title):
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()  # noqa: E731
    return f"{norm(artist)}|{norm(title)}"


def _credit_key(app, device_id, artist, title):
    """(key, night) for this device's free credit for this song tonight.

    Hashed: gen caps idempotency keys at 128 chars and song names can be long."""
    night = app.sing_store.get_night_started_at() or ""
    raw = f"{device_id}:{night}:{_song_key(artist, title)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), night


def _grant(app, account, key, only_if_empty):
    """gen show-credit; retried once on 409 busy_retry (transaction contention)."""
    for attempt in (1, 2):
        try:
            return app.gen_client.grant_show_credit(
                account["session_token"], key, venue=app.kj_config.get("venue_name"),
                only_if_empty=only_if_empty)
        except GenApiError as exc:
            if exc.status != 409 or attempt == 2:
                raise
            time.sleep(0.3)


def _show_credit(account, device_id, artist, title):
    """Search-time top-up: +1 credit only if the singer's balance is empty.

    Free at the show without letting credits pile up: a new singer keeps their
    welcome credit (the submit-time call then covers the job), and an
    abandoned search leaves at most one spare credit. Returns False when this
    device has hit tonight's distinct-song cap."""
    app = current_app._get_current_object()
    key, night = _credit_key(app, device_id, artist, title)
    limit = int(app.kj_config.get("sing_make_max_per_device", 3))
    st = _state(app)
    with _lock:
        keys = st["credit_keys"][(night, device_id)]
        if limit > 0 and key not in keys and len(keys) >= limit * 2:
            return False
        keys.add(key)
        st["last_key"][(night, device_id)] = key
    _grant(app, account, key, only_if_empty=True)
    return True


def _submit_credit(app, account, device_id, meta):
    """Submit-time top-up for the job about to be created (same key as the
    search, so gen grants it only if the search-time call didn't)."""
    st = _state(app)
    night = app.sing_store.get_night_started_at() or ""
    with _lock:
        key = (st["search_keys"].get(str(meta.get("search_session_id") or ""))
               or st["last_key"].get((night, device_id)))
    if not key:
        return
    try:
        _grant(app, account, key, only_if_empty=False)
    except GenApiError as exc:
        # Not fatal: the singer may have their own credit; create reports 402 if not.
        app.logger.warning("make: submit-time show credit not granted: %s", exc)


@sing_bp.route("/make/search", methods=["POST"])
@require_token
def make_search():
    """Top up tonight's show credit, then run gen's audio search as the singer."""
    data, device_id = _body()
    account, err = _guard(device_id)
    if err:
        return err
    artist = str(data.get("artist") or "").strip()[:200]
    title = str(data.get("title") or "").strip()[:200]
    if not (artist and title):
        return jsonify({"error": "artist and title are required"}), 400
    try:
        if not _show_credit(account, device_id, artist, title):
            return jsonify({"error": "make_limit"}), 429
    except GenApiError as exc:
        if exc.status == 401:
            return gen_error_response(exc, device_id)
        # No top-up (cap hit / gen hiccup) — the singer may still have their
        # own credits; the search's own 402 reports it if not.
        current_app.logger.warning("make: show credit not granted: %s", exc)
    try:
        result = current_app.gen_client.search_audio(account["session_token"], artist, title)
    except GenApiError as exc:
        return gen_error_response(exc, device_id)
    app = current_app._get_current_object()
    if result.get("search_session_id"):
        key, _night = _credit_key(app, device_id, artist, title)
        with _lock:
            _state(app)["search_keys"][str(result["search_session_id"])] = key
    return jsonify({
        "search_session_id": result.get("search_session_id"),
        "results": result.get("results") or [],
    })


@sing_bp.route("/make/validate-url", methods=["POST"])
@require_token
def make_validate_url():
    data, device_id = _body()
    account, err = _guard(device_id)
    if err:
        return err
    url = str(data.get("url") or "").strip()[:2000]
    if not url:
        return jsonify({"supported": False})
    try:
        return jsonify(current_app.gen_client.validate_url(account["session_token"], url))
    except GenApiError as exc:
        return gen_error_response(exc, device_id)


def create_job(app, device_id, artist, title, source_meta):
    """Create the singer's gen job at submit time. Returns (job_id, error_response).

    ``source_meta`` is either {search_session_id, selection_index} (a result the
    singer picked) or {youtube_url} (gen's URL fallback).
    """
    account = app.sing_store.get_gen_account(device_id) if device_id else None
    if not account:
        return None, (jsonify({"error": "signin_required"}), 401)
    meta = source_meta if isinstance(source_meta, dict) else {}
    gen = app.gen_client
    if not (meta.get("youtube_url")
            or (meta.get("search_session_id") and isinstance(meta.get("selection_index"), int))):
        return None, (jsonify({"error": "make requires a chosen audio source"}), 400)
    _submit_credit(app, account, device_id, meta)
    try:
        if meta.get("youtube_url"):
            result = gen.create_job_from_url(
                account["session_token"], str(meta["youtube_url"])[:2000], artist, title)
        elif meta.get("search_session_id") and isinstance(meta.get("selection_index"), int):
            result = gen.create_job_from_search(
                account["session_token"], str(meta["search_session_id"]),
                meta["selection_index"], artist, title)
        else:
            return None, (jsonify({"error": "make requires a chosen audio source"}), 400)
    except GenApiError as exc:
        # 404 = session expired; 403 = it belongs to another gen account (the
        # singer re-verified with a different email since searching).
        if exc.status in (403, 404):
            return None, (jsonify({"error": "search_expired"}), 409)
        return None, gen_error_response(exc, device_id)
    job_id = result.get("job_id")
    if not job_id:
        return None, (jsonify({"error": "gen_unavailable"}), 502)
    return job_id, None


@sing_bp.route("/make/review-link/<int:req_id>", methods=["POST"])
@require_token
def make_review_link(req_id):
    """One-click link to the singer's own lyrics review on gen ("Tap here").

    Proven like cancel (the request's edit_token); gen mints the link for the
    job's owner only, using this device's stored gen sign-in."""
    from sing import _belongs_to_current_night, _extract_token

    data, device_id = _body()
    # Not _guard(): an already-made song's review link must keep working even
    # if the host has since switched make-it requests off.
    if not make_flow_ready(current_app._get_current_object()):
        return jsonify({"error": "make_requests_disabled"}), 400
    if _rate_limited(device_id):
        return jsonify({"error": "rate_limited"}), 429
    store = current_app.sing_store
    account = store.get_gen_account(device_id) if device_id else None
    if not account:
        return jsonify({"error": "signin_required"}), 401
    req = store.get_request(req_id)
    if (req is None or req.get("token") != _extract_token()
            or not _belongs_to_current_night(store, req)):
        return jsonify({"error": "not_found"}), 404
    stored = req.get("edit_token") or ""
    if not stored or not secrets.compare_digest(str(data.get("edit_token") or ""), str(stored)):
        return jsonify({"error": "forbidden"}), 403
    job_id = req.get("gen_job_id")
    if req.get("source_type") != "make" or not job_id:
        return jsonify({"error": "not_found"}), 404
    try:
        result = current_app.gen_client.review_link(
            account["session_token"], job_id, locale=_locale(data))
    except GenApiError as exc:
        if exc.status == 409:
            return jsonify({"error": "not_in_review"}), 409
        if exc.status == 404:
            # Signed in on this phone as a different gen account than the job's.
            return jsonify({"error": "wrong_account"}), 403
        return gen_error_response(exc, device_id)
    if not result.get("url"):
        return jsonify({"error": "gen_unavailable"}), 502
    return jsonify({"url": result["url"], "review_started_by": result.get("review_started_by")})
