"""KJ "Gen" flow — make a rotation entry's karaoke video with karaoke-gen.

The KJ-side twin of the singer make-it (``sing_make.py``): the rotation's Gen
button opens a modal that runs gen's guided job submission — match-judge tidies
the artist/title, gen's lossless-first audio search finds sources, the KJ picks
one (or pastes a YouTube link) — then the job is created and its rotation entry
added as "Being Made (!)". The GenPoller links the finished NOMAD master (or
the quick draft, if the KJ uses it) exactly as for a singer's make-it.

Calls run with gen's admin token (``GenClient.kj_*``), so there is no email
sign-in or show credit: the job belongs to gen's admin account.
"""

from flask import current_app, jsonify, request

from gen_client import GenApiError, GenStatus
from routes import (
    _decorate_rotation_entries,
    _format_song_text,
    _parse_singer_fields,
    routes_bp,
)


def _guard():
    """(gen_client, rotation, error_response) for the /rotation/gen/* routes."""
    rotation = getattr(current_app, "rotation", None)
    if rotation is None:
        return None, None, (jsonify({"error": "Rotation not configured"}), 503)
    gen = getattr(current_app, "gen_client", None)
    if gen is None:
        return None, None, (jsonify({"error": "Gen API not configured"}), 503)
    return gen, rotation, None


def _gen_error(exc):
    """Map a GenApiError to a JSON reply the modal can show."""
    if exc.status in (401, 403):
        current_app.logger.warning("kj gen: gen refused the admin token: %s", exc)
        return jsonify({"error": "gen_auth"}), 502
    if exc.status == 429:
        return jsonify({"error": "rate_limited"}), 429
    if exc.status in (400, 422):
        return jsonify({"error": "gen_rejected",
                        "detail": exc.detail if isinstance(exc.detail, str) else ""}), 400
    current_app.logger.warning("kj gen: gen call failed: %s", exc)
    return jsonify({"error": "gen_unavailable"}), 502


def _artist_title(data):
    artist = str(data.get("artist") or "").strip()[:200]
    title = str(data.get("title") or "").strip()[:200]
    return artist, title


def _naive_split(query):
    """Best-effort "Title - Artist" split of the rotation song box (the KJ's
    usual order, matching ``_format_song_text``); otherwise it's all title."""
    parts = [p.strip() for p in query.split(" - ") if p.strip()]
    if len(parts) >= 2:
        return {"artist": parts[-1], "title": " - ".join(parts[:-1])}
    return {"artist": "", "title": query.strip()}


@routes_bp.route("/rotation/gen/resolve", methods=["GET"])
def kj_gen_resolve():
    """Pre-fill the Gen modal's artist/title from the rotation song box.

    Uses gen's free-text resolver (cached; the singer search uses the same one)
    when the partner secret is set, else a plain "Title - Artist" split."""
    query = (request.args.get("q") or "").strip()[:200]
    if not query:
        return jsonify({"artist": "", "title": "", "source": "none"})
    from sing import _resolve_query

    verdict = _resolve_query(query) or {}
    if verdict.get("kind") in ("cosmetic", "content") and verdict.get("confident"):
        artist = (verdict.get("canonical_artist") or "").strip()
        title = (verdict.get("canonical_title") or "").strip()
        if artist and title:
            return jsonify({"artist": artist, "title": title, "source": "gen"})
    artist = (verdict.get("typed_artist") or "").strip()
    title = (verdict.get("typed_title") or "").strip()
    if artist and title:
        return jsonify({"artist": artist, "title": title, "source": "split"})
    return jsonify({**_naive_split(query), "source": "naive"})


@routes_bp.route("/rotation/gen/check", methods=["POST"])
def kj_gen_check():
    """gen's match-judge verdict for the typed artist/title."""
    gen, _rotation, err = _guard()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    artist, title = _artist_title(data)
    if not (artist and title):
        return jsonify({"error": "artist and title are required"}), 400
    stage = "full" if data.get("stage") == "full" else "fast"
    tier = data.get("tier") if data.get("tier") in (1, 2, 3) else None
    try:
        return jsonify(gen.kj_match_judge(artist, title, stage=stage, audio_confidence_tier=tier))
    except GenApiError as exc:
        # Matching is a nice-to-have (gen's own UI fails open too).
        current_app.logger.info("kj gen: match-judge unavailable: %s", exc)
        return jsonify({"kind": "none", "confident": False})


@routes_bp.route("/rotation/gen/search", methods=["POST"])
def kj_gen_search():
    """gen's audio search (flacfetch → Spotify / YouTube fallbacks)."""
    gen, _rotation, err = _guard()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    artist, title = _artist_title(data)
    if not (artist and title):
        return jsonify({"error": "artist and title are required"}), 400
    try:
        result = gen.kj_search_audio(artist, title)
    except GenApiError as exc:
        return _gen_error(exc)
    return jsonify({
        "search_session_id": result.get("search_session_id"),
        "results": result.get("results") or [],
    })


@routes_bp.route("/rotation/gen/validate-url", methods=["POST"])
def kj_gen_validate_url():
    gen, _rotation, err = _guard()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    url = str(data.get("url") or "").strip()[:2000]
    if not url:
        return jsonify({"supported": False})
    try:
        return jsonify(gen.kj_validate_url(url))
    except GenApiError as exc:
        return _gen_error(exc)


@routes_bp.route("/rotation/gen/create", methods=["POST"])
def kj_gen_create():
    """Create the gen job, then add (or update) its rotation entry.

    Body: ``artist``, ``title`` and either ``search_session_id`` +
    ``selection_index`` or ``youtube_url``; plus ``singers`` for a new entry or
    ``id`` to generate for an existing one (the rotation's link mode), with
    ``replace: true`` to supersede a gen job the entry already has in progress.

    The job is created FIRST, so a gen failure leaves the rotation untouched
    and the KJ can retry from the modal without a stray "Being Made" row.
    """
    gen, rotation, err = _guard()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    artist, title = _artist_title(data)
    if not (artist and title):
        return jsonify({"error": "artist and title are required"}), 400
    youtube_url = str(data.get("youtube_url") or "").strip()[:2000]
    session_id = str(data.get("search_session_id") or "").strip()
    index = data.get("selection_index")
    if not youtube_url and not (session_id and isinstance(index, int) and not isinstance(index, bool)):
        return jsonify({"error": "pick an audio source first"}), 400

    existing = None
    singer = singers = None
    if data.get("id") is not None:
        try:
            existing = rotation.store.get_entry(int(data["id"]))
        except (TypeError, ValueError):
            return jsonify({"error": "id must be an integer"}), 400
        if existing is None:
            return jsonify({"error": "entry not found"}), 404
        # A job still in progress is only replaced when the KJ confirms (a
        # stuck job); the old job keeps running on gen but is no longer tracked.
        if (existing.get("gen_job_id") and existing.get("gen_status") in GenStatus.ACTIVE
                and data.get("replace") is not True):
            return jsonify({"error": "already_generating",
                            "gen_status": existing.get("gen_status")}), 409
    else:
        if not data.get("singers") and not str(data.get("singer") or "").strip():
            return jsonify({"error": "singer is required"}), 400
        singer, singers, _song, perr = _parse_singer_fields(data)
        if perr:
            return perr

    try:
        if youtube_url:
            result = gen.kj_create_job_from_url(youtube_url, artist, title)
        else:
            result = gen.kj_create_job_from_search(session_id, index, artist, title)
    except GenApiError as exc:
        if exc.status == 404:
            # gen's search sessions expire; the modal re-searches.
            return jsonify({"error": "search_expired"}), 409
        return _gen_error(exc)
    job_id = result.get("job_id")
    if not job_id:
        return jsonify({"error": "gen_unavailable"}), 502

    song_text = _format_song_text(artist, title)
    # The job is real from here on: never report failure (a retry would start
    # a duplicate job) — log and carry on, like the singer make-it approval.
    try:
        if existing is None:
            entry = rotation.add_entry(singer, song_text, singers=singers)
            entry_id = entry["id"]
        else:
            entry_id = existing["id"]
            if (existing.get("song_artist") or "") != song_text:
                rotation.update_entry(entry_id, song_artist=song_text)
        # Not singable until gen delivers: pinned below ready songs until the
        # GenPoller links the video and flips it to "Waiting".
        if existing is None or not existing.get("file_path"):
            rotation.mark_being_made(entry_id)
        rotation.set_gen_status(entry_id, job_id, GenStatus.PROCESSING)
    except Exception:
        current_app.logger.exception("kj gen: job %s created but the rotation update failed", job_id)
        return jsonify({"success": True, "job_id": job_id, "warning": "rotation_update_failed"})

    entries = rotation.get_rotation()
    _decorate_rotation_entries(entries, rotation)
    return jsonify({"success": True, "job_id": job_id, "entry_id": entry_id, "entries": entries})
