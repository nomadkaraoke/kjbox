"""Flask Blueprint with all route handlers."""

import glob
import json
import os
import queue
import random
import re
import struct
import subprocess
import threading
import time
import unicodedata

from flask import Blueprint, Response, current_app, jsonify, render_template, request, send_file

import divebar
import karaoke_nerds
import library_media
import local_grouping
import text_normalize
from text_normalize import normalize as _normalize_text, tokens as _tokens, group_key as _group_key
import version_priority
import youtube_health
import youtube_search
from config import APP_DIR, RENDER_MODE_MPV, RENDER_MODES, load_config, save_config_value
from playback import RendererSwitchRejected
from sing import get_event_url, sync_event_url_overlays
from sleep_mode import SleepManager
from utils import log_message, build_divebar_filename, divebar_ext
from naming import youtube_id_from_url, media_id_for
from preview import parse_range

# --- Browser mode state ---
# Tracks whether the system is in Browser mode (Chromium) vs VLC mode (default).
# This is module-level so it survives across requests but resets on service restart.
_browser_mode = False

routes_bp = Blueprint('routes', __name__)


def _check_sleep_mode():
    """Return a 409 JSON response if sleep mode is active, else None."""
    sleep_mgr = getattr(current_app, 'sleep_manager', None)
    if sleep_mgr and sleep_mgr.is_sleeping():
        return jsonify({
            "error": "Sleep mode is active. Disable sleep mode first."
        }), 409
    return None


# --- Search-result grouping (for the singer-facing /sing/search) -----------
#
# Two results share a group key iff their normalized (artist, title) forms are
# byte-equal. No fuzzy matching in v1 — see
# docs/archive/2026-04-23-song-selection-ux-master-plan.md decision #1.

def _normalize_song_key(artist, title):
    """Deterministic (artist, title) → group key for collapsing search results.

    Delegates to the shared normalizer (``text_normalize.group_key``) so the
    singer-facing grouping uses the exact same canonical space as catalog
    indexing and FTS query building. The shared normalizer lowercases, folds
    diacritics/Latin specials, strips feat./ft./featuring qualifiers, expands
    ``&``/``+`` to "and", drops apostrophes, canonicalizes numbers, etc.

    Note (decision D4): bracketed qualifiers such as ``(Live)`` /
    ``[Radio Edit]`` are intentionally NOT stripped, so distinct versions stay
    distinguishable. Grouping is therefore more granular than the legacy key.

    None-safe — callers pass parsed filename fields that may be missing.
    """
    return _group_key(artist, title)


def _record_play_stat(validated_path, entry_id):
    """Best-effort: credit one play for the media_id at validated_path. Never raises."""
    try:
        stats = getattr(current_app, 'stats', None)
        ml = getattr(current_app, 'media_library', None)
        if not stats:
            return
        row = ml.get_by_path(validated_path) if ml else None
        media_id = (row or {}).get('media_id')
        artist = (row or {}).get('artist')
        title = (row or {}).get('title')
        if not media_id:
            from naming import extract_media_id
            media_id = extract_media_id(os.path.basename(validated_path))
        if not media_id and library_media.is_library_path(
                validated_path, getattr(current_app, 'kj_config', None)):
            # SSD/library file with no row yet: hash + materialize off-thread —
            # a cold multi-hundred-MB MP4 must never stall /play (design D3).
            library_media.run_async(
                _record_library_play, current_app._get_current_object(),
                validated_path, entry_id)
            return
        if not media_id:
            return
        singer = None
        rotation = getattr(current_app, 'rotation', None)
        if entry_id and rotation:
            entry = rotation.store.get_entry(entry_id)
            if entry:
                singer = entry.get('singer')
                if not (artist or title):
                    sa = (entry.get('song_artist') or '').strip()
                    if ' - ' in sa:
                        a, t = sa.split(' - ', 1)
                        artist, title = a.strip(), t.strip()
                    else:
                        title = sa or None
        song_key = _normalize_song_key(artist, title)
        stats.record_play(media_id, entry_id=entry_id, singer=singer,
                          artist=artist, title=title, song_key=song_key)
    except Exception as e:  # never let stats break playback
        try:
            log_message(f"stats: play record failed: {e}", current_app.kj_config)
        except Exception:
            pass


def _record_preview_stat(descriptor):
    """Best-effort: credit one preview for the descriptor's media_id. Never raises."""
    try:
        stats = getattr(current_app, 'stats', None)
        if not stats or not isinstance(descriptor, dict):
            return
        ml = getattr(current_app, 'media_library', None)
        source = descriptor.get('source')
        title = descriptor.get('title')
        artist = None
        media_id = None
        if source == 'local' and ml:
            row = ml.get_by_path(descriptor.get('file_path')) or {}
            media_id = row.get('media_id')
            artist = row.get('artist')
            title = title or row.get('title')
            if not media_id and library_media.is_library_path(
                    descriptor.get('file_path'),
                    getattr(current_app, 'kj_config', None)):
                library_media.run_async(
                    _record_library_preview, current_app._get_current_object(),
                    descriptor.get('file_path'))
                return
        elif source == 'youtube':
            vid = youtube_id_from_url(descriptor.get('youtube_url'))
            if vid:
                media_id = f"yt-{vid}"
        # divebar previews: identity is the Phase-2-dependent fuzzy case (spec §10);
        # skip counting rather than guess. Tighten once P2 lands file_id-based ids.
        if not media_id:
            return
        song_key = _normalize_song_key(artist, title)
        stats.record_preview(media_id, artist=artist, title=title, song_key=song_key)
    except Exception as e:
        try:
            log_message(f"stats: preview record failed: {e}", current_app.kj_config)
        except Exception:
            pass


def _record_library_play(app, validated_path, entry_id):
    """Thread target: materialize an SSD file's identity row, then record the play.

    Runs off the request thread (hashing can take seconds cold). The
    per-rotation-entry dedup index in StatsStore keeps a delayed insert
    idempotent. Never raises.
    """
    try:
        row = library_media.ensure_library_row_for_app(app, validated_path)
        if not row:
            return
        singer = None
        rotation = getattr(app, 'rotation', None)
        if entry_id and rotation:
            entry = rotation.store.get_entry(entry_id)
            if entry:
                singer = entry.get('singer')
        song_key = _normalize_song_key(row.get('artist'), row.get('title'))
        app.stats.record_play(row['media_id'], entry_id=entry_id, singer=singer,
                              artist=row.get('artist'), title=row.get('title'),
                              song_key=song_key)
    except Exception as e:
        try:
            log_message(f"stats: library play record failed: {e}", app.kj_config)
        except Exception:
            pass


def _record_library_preview(app, file_path):
    """Thread target: materialize an SSD file's identity row, then record the preview."""
    try:
        row = library_media.ensure_library_row_for_app(app, file_path)
        if not row:
            return
        song_key = _normalize_song_key(row.get('artist'), row.get('title'))
        app.stats.record_preview(row['media_id'], artist=row.get('artist'),
                                 title=row.get('title'), song_key=song_key)
    except Exception as e:
        try:
            log_message(f"stats: library preview record failed: {e}", app.kj_config)
        except Exception:
            pass


def resolve_row_media_id(row, kind, ml):
    """Best-effort media_id for a rotation-search row. Never raises. See design §10."""
    try:
        if kind == "local":
            r = (ml.get_by_path(row.get("path")) if ml else None) or {}
            mid = r.get("media_id")
            if mid:
                return mid
            from naming import extract_media_id
            return extract_media_id(os.path.basename(row.get("filename") or row.get("path") or ""))
        if kind == "kn":
            vid = youtube_id_from_url(row.get("youtube_url"))
            return media_id_for("youtube", vid) if vid else None
        if kind == "divebar":
            fid = row.get("file_id")
            if not fid:
                return None
            brand = row.get("brand") or row.get("brand_code") or "DB"
            return media_id_for("community", f"{brand}-{fid}")
    except Exception:
        return None
    return None


def _enrich_search_stats(result):
    """Attach `stats` + `media_id` to each local/KN-track/divebar row. Best-effort; never raises."""
    stats = getattr(current_app, "stats", None)
    ml = getattr(current_app, "media_library", None)
    if not stats:
        return
    try:
        pairs = []  # (row, media_id, song_key)
        for r in result.get("local", []):
            pairs.append((r, resolve_row_media_id(r, "local", ml),
                          _normalize_song_key(r.get("artist"), r.get("title"))))
        for song in result.get("karaoke_nerds", []):
            sk = _normalize_song_key(song.get("artist"), song.get("title"))
            for t in song.get("tracks") or []:
                pairs.append((t, resolve_row_media_id(t, "kn", ml), sk))
        for r in result.get("divebar", []):
            pairs.append((r, resolve_row_media_id(r, "divebar", ml),
                          _normalize_song_key(r.get("artist"), r.get("title"))))
        ids = [mid for _, mid, _ in pairs if mid]
        agg = stats.stats_for(ids)
        by_song = {}
        for _row, mid, sk in pairs:
            if mid:
                by_song.setdefault(sk, []).append(mid)
        usual = set()
        for mids in by_song.values():
            u = stats.usual_media_id(mids)
            if u:
                usual.add(u)
        for row, mid, _sk in pairs:
            if not mid:
                continue
            s = dict(agg.get(mid, {"plays": 0, "previews": 0, "last_played": None}))
            s["is_usual"] = mid in usual
            note = stats.get_note(mid)
            s["note"] = (note or {}).get("note")
            s["label"] = (note or {}).get("label")
            row["stats"] = s
            row["media_id"] = mid
    except Exception as e:
        try:
            log_message(f"stats: search enrichment failed: {e}", current_app.kj_config)
        except Exception:
            pass


def _group_search_results(local_results, kn_results):
    """Collapse local + KN results into one group per normalized (artist, title).

    Args:
        local_results: list of local-file search hits, shape matches what
            ``unified_search`` produces — ``{path, filename, artist, title, ...}``.
        kn_results: list of Karaoke Nerds songs, each with a ``tracks`` sub-list
            already (optionally) cross-referenced against Divebar.

    Returns a list of group dicts:

        {
            "key":               normalized group key,
            "artist":            canonical display artist,
            "title":             canonical display title,
            "version_count":     len(versions),
            "in_library":        True iff any version is local or KN+divebar,
            "has_community_only": True iff every version is KN+is_community,
            "versions": [
                {"source": "local", "local": <full local result>},
                {"source": "kn",    "kn":    <full KN track, with parent
                                              song_artist/song_title folded in>},
                ...
            ],
        }

    Grouping rules:
        * Locals win display-name arbitration (inserted first into the group).
        * KN songs only set the display name if no local has claimed it.
        * Within a group, versions appear in insertion order: locals first,
          then KN tracks in the order the KN search returned them.
    """
    groups = {}  # key -> group dict (py3.7+ dict preserves insertion order)

    for r in local_results or []:
        key = _normalize_song_key(r.get("artist"), r.get("title"))
        g = groups.setdefault(key, {
            "key": key,
            "artist": r.get("artist") or "",
            "title": r.get("title") or "",
            "versions": [],
        })
        g["versions"].append({"source": "local", "local": r})

    for song in kn_results or []:
        song_artist = song.get("artist") or ""
        song_title = song.get("title") or ""
        for track in song.get("tracks") or []:
            key = _normalize_song_key(song_artist, song_title)
            g = groups.setdefault(key, {
                "key": key,
                "artist": song_artist,
                "title": song_title,
                "versions": [],
            })
            g["versions"].append({
                "source": "kn",
                "kn": {**track, "song_artist": song_artist,
                       "song_title": song_title},
            })

    try:
        cfg = current_app.kj_config
    except (RuntimeError, AttributeError):
        cfg = {}

    out = []
    for g in groups.values():
        versions = g["versions"]
        kn_versions = [v for v in versions if v["source"] == "kn"]
        g["version_count"] = len(versions)
        g["in_library"] = any(
            v["source"] == "local"
            or (v["source"] == "kn" and (v["kn"].get("divebar") or {}).get("file_id"))
            for v in versions
        )
        # "has_community_only" only makes sense when we have KN versions AND no
        # locals — a normie-facing hint for Phase B's UI copy. False when empty.
        g["has_community_only"] = (
            bool(kn_versions)
            and not any(v["source"] == "local" for v in versions)
            and all(v["kn"].get("is_community") for v in kn_versions)
        )
        # Annotate every version with priority_rank/brand/class, then sort
        # in-place so clients that ignore the rank field still see best-first.
        version_priority.annotate_versions(versions, cfg, shape="kj_pick")
        versions.sort(key=lambda v: v.get("priority_rank", 9999))
        out.append(g)

    return out

# --- Debounced volume persistence ---
_volume_save_timer = None
_volume_save_lock = threading.Lock()


def _do_save_volumes(vlc):
    """Write both volume values to config.json (runs on timer thread)."""
    save_config_value('karaoke_volume', vlc.karaoke_volume)
    save_config_value('filler_volume', vlc.filler_volume)


def _debounced_save_volumes(vlc):
    """Schedule a config write 2s from now, cancelling any pending write."""
    global _volume_save_timer
    with _volume_save_lock:
        if _volume_save_timer is not None:
            _volume_save_timer.cancel()
        _volume_save_timer = threading.Timer(2.0, _do_save_volumes, args=[vlc])
        _volume_save_timer.daemon = True
        _volume_save_timer.start()


@routes_bp.route('/')
def index():
    """Serves the main remote control page."""
    cfg = current_app.kj_config
    return render_template(
        'index.html',
        latin_special_map=text_normalize.LATIN_SPECIAL_MAP,
        abbrev_map=text_normalize.ABBREV_MAP,
        number_words=text_normalize.NUMBER_WORDS,
        roman_map=text_normalize.ROMAN_NUMERALS,
        normalizer_version=text_normalize.NORMALIZER_VERSION,
        config=cfg,
        # Pass the app version explicitly: `config=cfg` above shadows Flask's
        # auto-injected app config (which holds APP_VERSION) with kj_config,
        # which doesn't — so the static-asset cache-bust query string was always
        # empty and frontend deploys never busted the browser cache.
        app_version=current_app.config.get('APP_VERSION', ''),
    )


@routes_bp.route('/download', methods=['POST'])
def handle_download():
    """Add a URL to the download queue (max 5). Poll /status for progress."""
    blocked = _check_sleep_mode()
    if blocked:
        return blocked
    url = request.json.get('url')
    if not url:
        return jsonify({"error": "URL is required"}), 400

    app = current_app._get_current_object()
    cfg = current_app.kj_config

    # Dedup-skip: if we already have this YouTube video on disk, don't re-download.
    existing = _existing_media_for(app, _prospective_media_id("youtube", youtube_url=url))
    if existing:
        log_message(f"Dedup-skip: already have {existing['media_id']}", cfg)
        return jsonify({"success": True, "deduped": True,
                        "file_path": existing["file_path"]})

    from uuid import uuid4
    with app._download_lock:
        items = app.download_queue['items']
        # Reject duplicate URL already queued or downloading
        active = [i for i in items if i['status'] in ('queued', 'downloading')]
        if any(i['url'] == url for i in active):
            return jsonify({"error": "This URL is already in the queue"}), 409
        if len(active) >= 5:
            return jsonify({"error": "Queue is full (max 5)"}), 409

        item = {
            'id': str(uuid4()),
            'url': url,
            'status': 'queued',
            'title': None,
            'error': None,
            'file_path': None,
            'added_at': time.time(),
            'completed_at': None,
        }
        items.append(item)
        log_message(f"Queued download: {url}", cfg)

        if not app.download_queue['worker_running']:
            app.download_queue['worker_running'] = True
            threading.Thread(target=_download_worker, args=[app], daemon=True).start()

    return jsonify({"success": True, "id": item['id']})


@routes_bp.route('/upload', methods=['POST'])
def handle_upload():
    """Upload a media file to the download folder."""
    blocked = _check_sleep_mode()
    if blocked:
        return blocked
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    from config import MEDIA_EXTENSIONS
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in MEDIA_EXTENSIONS:
        return jsonify({"error": f"Unsupported format: {ext}"}), 400

    cfg = current_app.kj_config
    download_folder = cfg.get('download_folder', os.path.expanduser('~/kjdata/videos'))
    os.makedirs(download_folder, exist_ok=True)

    # Sanitize filename
    safe_name = re.sub(r'[^\w\s\-\.\(\)]', '', file.filename).strip()
    if not safe_name:
        safe_name = 'uploaded_file' + ext
    dest = os.path.join(download_folder, safe_name)

    # Avoid overwriting
    if os.path.exists(dest):
        base, extension = os.path.splitext(safe_name)
        dest = os.path.join(download_folder, f"{base}_{int(time.time())}{extension}")
        safe_name = os.path.basename(dest)

    file.save(dest)
    log_message(f"Uploaded file: {safe_name} ({os.path.getsize(dest)} bytes)", cfg)

    gate = _playability_gate(dest)
    if not gate.verdict.get("overall_ok"):
        try:
            os.remove(dest)
        except OSError:
            pass
        reasons = "; ".join(gate.verdict.get("reasons") or ["file is not playable"])
        return jsonify({
            "error": f"Upload rejected — not playable: {reasons}",
            "verdict": gate.verdict,
        }), 422

    # Add to media index
    current_app.media.scan()

    return jsonify({"success": True, "filename": safe_name, "path": dest})


_QUEUE_TO_ROTATION_STATUS = {
    'queued': 'queued',
    'downloading': 'downloading',
    'error': 'failed',
}


def _sync_rotation_download(app, item):
    """Mirror a download queue item's status onto its linked rotation entry.

    Only touches the entry if its download_id still points at this queue item,
    so a retry (new download_id) is never overwritten by a late state change
    from a stale worker iteration.
    """
    rotation_entry_id = item.get('rotation_entry_id')
    if not rotation_entry_id:
        return
    rotation = getattr(app, 'rotation', None)
    if rotation is None:
        return
    new_status = _QUEUE_TO_ROTATION_STATUS.get(item.get('status'))
    if new_status is None:
        return  # 'completed' is handled by rotation.complete_download
    try:
        entry = rotation.store.get_entry(rotation_entry_id)
        if entry is None or entry.get('download_id') != item.get('id'):
            return
        rotation.set_download_status(
            rotation_entry_id, item.get('source'), new_status, item.get('id'))
    except Exception:
        pass  # Best-effort


def _clear_rotation_download_for_item(app, item):
    """Clear rotation entry's download fields when a queue item is removed.

    No-op unless the entry's download_id still matches this queue item's id,
    so dismissing a stale errored item never clobbers a fresh retry.
    """
    rotation_entry_id = item.get('rotation_entry_id')
    if not rotation_entry_id:
        return
    rotation = getattr(app, 'rotation', None)
    if rotation is None:
        return
    try:
        entry = rotation.store.get_entry(rotation_entry_id)
        if entry is None or entry.get('download_id') != item.get('id'):
            return
        rotation.set_download_status(rotation_entry_id, None, None, None)
    except Exception:
        pass  # Best-effort


def _prospective_media_id(source, *, youtube_url=None, file_id=None, brand_code=None):
    """Cheap pre-download media_id for dedup-skip, or None if not derivable."""
    if source == "youtube":
        vid = youtube_id_from_url(youtube_url)
        return media_id_for("youtube", vid) if vid else None
    if source == "divebar" and file_id:
        return media_id_for("community", f"{(brand_code or 'DB')}-{file_id}")
    return None


def _existing_media_for(app, media_id):
    """Return the media_library row for media_id iff its file exists on disk."""
    if not media_id:
        return None
    store = getattr(app, "media_library", None)
    if store is None:
        return None
    try:
        row = store.get(media_id)
    except Exception:
        return None
    if row and row.get("file_path") and os.path.exists(row["file_path"]):
        return row
    return None


def _resolve_divebar_spec(file_id, artist, title, brand_code, fmt, cfg):
    """Resolve a divebar track into a download-queue spec, pairing a loose CDG
    with its sibling audio so a bare, silent .cdg is never queued.

    Some brands (e.g. Sandell Karaoke) store a CDG's graphics and audio as two
    separate Drive files rather than one cdg+mp3 zip, exposed by the index as
    independent track rows. The pairing decision is keyed off the *resolved*
    on-disk extension (``.cdg``) rather than the catalog ``fmt`` alone, so a CDG
    whose ``format`` is missing can never slip through to the single-file path
    and queue a silent, audioless ``.cdg``.

    Returns ``(spec, error)``. ``spec`` is a queue-item fragment to merge into
    the caller's item dict; ``error`` is ``None`` on success or a
    ``(message, http_status)`` tuple the caller maps to its own convention.
    """
    url = divebar.get_download_url(file_id, cfg)
    if not url:
        return None, ("Failed to get download URL from Divebar", 502)
    ext = divebar_ext(url, fmt)

    if ext == ".cdg":
        # A bare .cdg is graphics-only. Pair it with its sibling audio and ship a
        # cdg+mp3 zip, or fail closed — never queue a silent .cdg.
        sibling = divebar.find_sibling_audio(file_id, artist, title, brand_code, cfg)
        if not sibling:
            return None, ("This CDG has no audio track available in the mirror — "
                          "pick another version.", 422)
        mp3_url = divebar.get_download_url(sibling["file_id"], cfg)
        if not mp3_url:
            return None, ("Failed to get audio download URL from Divebar", 502)
        zip_title = build_divebar_filename(brand_code, artist, title, ext=".zip") \
            or f"divebar-{file_id}.zip"
        return {
            "pair": True,
            "cdg_url": url,
            "mp3_url": mp3_url,
            "title": zip_title,
            "source": "divebar",
            "source_detail": divebar.classify_download_url(url),
            "divebar_file_id": file_id,
            "brand_code": brand_code,
            "song_artist": artist,
            "song_title": title,
        }, None

    filename = build_divebar_filename(brand_code, artist, title, ext=ext) \
        or f"divebar-{file_id}{ext}"
    return {
        "pair": False,
        "url": url,
        "title": filename,
        "source": "divebar",
        "source_detail": divebar.classify_download_url(url),
        "divebar_file_id": file_id,
        "brand_code": brand_code,
        "song_artist": artist,
        "song_title": title,
    }, None


def _download_worker(app):
    """Process queued downloads sequentially until queue is drained."""
    while True:
        with app._download_lock:
            next_item = next(
                (i for i in app.download_queue['items'] if i['status'] == 'queued'),
                None,
            )
            if not next_item:
                app.download_queue['worker_running'] = False
                return
            next_item['status'] = 'downloading'
        _sync_rotation_download(app, next_item)

        try:
            if next_item.get('source') == 'divebar':
                # Canonical community identity: db-<brand>-<fileid>. song_artist/
                # song_title carry the real song fields (item['title'] is the zip
                # filename, not the song title).
                fid = next_item.get('divebar_file_id')
                brand = next_item.get('brand_code') or ''
                ref = f"{brand or 'DB'}-{fid}" if fid else None
                song_artist = next_item.get('song_artist')
                song_title = next_item.get('song_title')
                if next_item.get('pair'):
                    # Loose CDG: fetch the .cdg + its sibling .mp3 and package
                    # them into a single playable cdg+mp3 zip.
                    file_path, title = app.media.download_cdg_pair(
                        next_item['cdg_url'], next_item['mp3_url'],
                        filename=next_item.get('title'),
                        source="community", source_ref=ref,
                        artist=song_artist, title=song_title)
                else:
                    file_path, title = app.media.download_from_url(
                        next_item['url'], filename=next_item.get('title'),
                        source="community", source_ref=ref,
                        artist=song_artist, title=song_title)
            else:
                file_path, title = app.media.download_video(next_item['url'])
        except Exception:
            file_path, title = None, None

        with app._download_lock:
            if file_path:
                next_item.update(status='completed', title=title,
                                 file_path=file_path, completed_at=time.time())
            else:
                next_item.update(status='error', error='Download failed',
                                 completed_at=time.time())

        if file_path:
            # Auto-link rotation entry if this was a rotation-linked download.
            # complete_download sets download_status='complete' and links the file.
            rotation_entry_id = next_item.get('rotation_entry_id')
            if rotation_entry_id and hasattr(app, 'rotation') and app.rotation:
                try:
                    download_id = next_item.get('id')
                    if download_id:
                        app.rotation.complete_download(
                            download_id, file_path,
                            title=next_item.get('title'),
                        )
                except Exception:
                    pass  # Best-effort; entry can be linked manually
        else:
            _sync_rotation_download(app, next_item)  # status='error' → entry 'failed'


@routes_bp.route('/download/cancel', methods=['POST'])
def cancel_download():
    """Cancel a queued download by id. Cannot cancel an active download."""
    item_id = request.json.get('id')
    if not item_id:
        return jsonify({"error": "id is required"}), 400

    app = current_app._get_current_object()
    with app._download_lock:
        items = app.download_queue['items']
        item = next((i for i in items if i['id'] == item_id), None)
        if not item:
            return jsonify({"error": "Item not found"}), 404
        if item['status'] == 'downloading':
            return jsonify({"error": "Cannot cancel an active download"}), 409
        items.remove(item)
    _clear_rotation_download_for_item(app, item)
    return jsonify({"success": True})


@routes_bp.route('/download/ack', methods=['POST'])
def ack_download():
    """Dismiss completed/errored items. With id: specific item. Without: all finished."""
    app = current_app._get_current_object()
    item_id = request.json.get('id') if request.is_json else None

    removed = []
    with app._download_lock:
        items = app.download_queue['items']
        if item_id:
            item = next((i for i in items if i['id'] == item_id), None)
            if item and item['status'] in ('completed', 'error'):
                items.remove(item)
                removed.append(item)
        else:
            kept = []
            for i in items:
                if i['status'] in ('completed', 'error'):
                    removed.append(i)
                else:
                    kept.append(i)
            app.download_queue['items'] = kept

    # Clear stuck rotation state for any errored items we just dismissed.
    # Completed items already had their rotation state finalised by the worker.
    for item in removed:
        if item['status'] == 'error':
            _clear_rotation_download_for_item(app, item)
    return jsonify({"success": True})


@routes_bp.route('/play', methods=['POST'])
def handle_play():
    """Plays a media file by path (supports local media, external media, and ZIP files)."""
    blocked = _check_sleep_mode()
    if blocked:
        return blocked
    file_path = request.json.get('file_path')
    if not file_path:
        return jsonify({"error": "file_path is required"}), 400

    media = current_app.media
    vlc = current_app.vlc
    cfg = current_app.kj_config

    # Try local media folders first
    validated = media.validate_path(file_path)

    # If not in local folders, check if it's under the external media mount
    if not validated:
        mount = cfg.get('external_media_mount', '')
        if mount:
            real_mount = os.path.realpath(mount)
            for form in ('NFC', 'NFD'):
                real = os.path.realpath(unicodedata.normalize(form, file_path))
                if real.startswith(real_mount + os.sep) and os.path.exists(real):
                    validated = real
                    break

    if not validated:
        return jsonify({"error": "Invalid or inaccessible file path"}), 400

    if not vlc.enabled:
        return jsonify({"error": "VLC not available (running in local/dev mode)"}), 503

    # Handle ZIP files (CDG+MP3)
    actual_play_path = validated
    audio_file = None
    if validated.lower().endswith('.zip'):
        zip_playback = current_app.zip_playback
        mp3_path = zip_playback.extract_and_get_mp3(validated)
        if not mp3_path:
            return jsonify({"error": "ZIP file does not contain a playable .mp3 file"}), 400
        actual_play_path = mp3_path
        # mpv renders the CDG graphics only when handed the .cdg directly, with
        # the .mp3 attached as an external audio track. VLC instead auto-
        # discovers the sibling .cdg from the .mp3, so it keeps the mp3.
        if getattr(vlc, 'render_mode', None) == RENDER_MODE_MPV:
            cdg_path = zip_playback.current_cdg_path()
            if cdg_path:
                actual_play_path = cdg_path
                audio_file = mp3_path
    elif validated.lower().endswith('.cdg'):
        # A bare .cdg is graphics-only — playing it alone is silent. Only allow it
        # when a same-stem audio file sits beside it (the un-zipped X.cdg/X.mp3 pair),
        # wired the same way as the .zip branch.
        from playability import sibling_cdg_audio
        audio_sibling = sibling_cdg_audio(validated)
        if not audio_sibling:
            return jsonify({"error": "This is a graphics-only .cdg with no audio track — "
                                     "use the CDG+MP3 zip version instead."}), 400
        if getattr(vlc, 'render_mode', None) == RENDER_MODE_MPV:
            actual_play_path = validated         # mpv renders the .cdg directly
            audio_file = audio_sibling
        else:
            actual_play_path = audio_sibling     # VLC auto-discovers the sibling .cdg

    # Auto-disable browser mode before playing — kill Chromium and reset PipeWire
    # so VLC has exclusive access to the audio device and display.
    # Check both the flag AND actual process state to catch orphans after restart.
    global _browser_mode
    chromium = getattr(current_app, 'chromium', None)
    if _browser_mode or (chromium and chromium.is_running()):
        if chromium:
            chromium.kill()
        _browser_mode = False
        log_message("Browser mode auto-disabled for VLC playback.", cfg)

    log_message(f"Received play request for {os.path.basename(validated)}.", cfg)
    threading.Thread(target=vlc.play_video, args=(actual_play_path,),
                     kwargs={'display_path': validated,
                             'overlay_manager': current_app.overlay_manager,
                             'audio_file': audio_file}).start()
    _record_play_stat(validated, request.json.get('entry_id'))
    return jsonify({"success": True, "message": "Playback initiated."})


@routes_bp.route('/seek', methods=['POST'])
def handle_seek():
    """Handles seeking within the karaoke video."""
    seek_time = request.json.get('time')
    if seek_time is None:
        return jsonify({"error": "Time is required"}), 400

    vlc = current_app.vlc
    cfg = current_app.kj_config
    log_message(f"Received seek request to time: {int(seek_time)}s", cfg)
    vlc.seek_karaoke(int(seek_time))
    return jsonify({"success": True})


@routes_bp.route('/control', methods=['POST'])
def handle_control():
    """Handles playback controls like pause, resume, restart."""
    action = request.json.get('action')
    if not action:
        return jsonify({"error": "Action is required"}), 400

    vlc = current_app.vlc
    cfg = current_app.kj_config

    log_message(f"Received control action: {action}", cfg)
    overlay_mgr = current_app.overlay_manager
    if action == 'pause_resume':
        is_now_paused = vlc.pause_resume_karaoke()
        if is_now_paused:
            vlc.karaoke_active = False
            overlay_mgr.set_karaoke_playing(False)
            # Don't start filler — paused karaoke still holds the ALSA device
        else:
            vlc.karaoke_active = True
            overlay_mgr.set_karaoke_playing(True)
    elif action == 'restart':
        vlc.seek_karaoke(0)
    elif action == 'stop':
        vlc.stop_karaoke()
        vlc.ensure_karaoke_released()
        overlay_mgr.set_karaoke_playing(False)
        vlc.fade_in_filler()
    elif action == 'fadeout':
        # Coordinator drives the fade on the active player, restores volume,
        # clears overlay, and fades filler back in. Works for both renderers.
        # The KJ picks the fade length per use (3s / 10s / 20s / custom); default 3s
        # for back-compat, clamped to a sane range so a bad value can't stall the
        # fade thread for minutes or fire instantly.
        raw_duration = request.json.get('duration_s', 3.0)
        try:
            fade_duration = float(raw_duration)
        except (TypeError, ValueError):
            fade_duration = 3.0
        fade_duration = max(0.5, min(fade_duration, 60.0))
        vlc.fadeout(duration_s=fade_duration)

    return jsonify({"success": True, "message": f"Action '{action}' executed."})


@routes_bp.route('/volume', methods=['POST'])
def handle_volume():
    """Handles volume control for karaoke or filler music."""
    target = request.json.get('target')
    level_raw = request.json.get('level')
    if not target or level_raw is None:
        return jsonify({"error": "Target and level are required"}), 400
    try:
        level = int(level_raw)
    except (ValueError, TypeError):
        return jsonify({"error": "Level must be a number"}), 400

    vlc = current_app.vlc
    cfg = current_app.kj_config

    if target == 'karaoke':
        vlc.set_karaoke_volume_live(level)
    elif target == 'filler':
        port = cfg.get('filler_vlc_port', 8081)
        password = cfg.get('filler_vlc_password', 'filler')
        vlc.filler_volume = level
        vlc.send_command(port, password, f"volume&val={level}")
    else:
        return jsonify({"error": "Invalid target"}), 400
    _debounced_save_volumes(vlc)
    log_message(f"Set volume for '{target}' to {level}", cfg)
    return jsonify({"success": True})


@routes_bp.route('/pitch', methods=['POST'])
def handle_pitch():
    """Set karaoke pitch offset in semitones (-6 to +6)."""
    semitones = request.json.get('semitones')
    if semitones is None:
        return jsonify({"error": "semitones is required"}), 400
    try:
        semitones = int(semitones)
    except (ValueError, TypeError):
        return jsonify({"error": "semitones must be an integer"}), 400

    vlc = current_app.vlc
    vlc.set_pitch(semitones)
    log_message(f"Pitch set to {vlc.pitch_semitones} semitones", current_app.kj_config)
    return jsonify({"success": True, "pitch_semitones": vlc.pitch_semitones})


@routes_bp.route('/renderer', methods=['GET'])
def get_renderer():
    """Return active karaoke renderer mode and its capabilities."""
    return jsonify(current_app.vlc.describe_renderer())


@routes_bp.route('/renderer', methods=['POST'])
def set_renderer():
    """Switch karaoke renderer. Rejected with 409 while karaoke is active."""
    mode = (request.json or {}).get('mode')
    if mode not in RENDER_MODES:
        return jsonify({
            "error": "invalid_mode",
            "message": f"mode must be one of {list(RENDER_MODES)}",
        }), 400

    vlc = current_app.vlc
    try:
        state = vlc.switch_renderer(mode)
    except RendererSwitchRejected as e:
        return jsonify({"error": "karaoke_active", "message": str(e)}), e.status_code

    log_message(f"Renderer switched to '{mode}' via API.", current_app.kj_config)
    return jsonify({"success": True, **state})


@routes_bp.route('/media')
def list_media():
    """Returns the media index with display info, sorted by mtime desc."""
    return jsonify(current_app.media.list_items())


@routes_bp.route('/media/metadata', methods=['POST'])
def set_media_metadata():
    """Set canonical Artist/Title for a media_library row (Available Songs edit).

    Marks the row user-confirmed (parse_method='manual', confidence cleared,
    needs_review=0) and recomputes the *_norm fields for search/dedup.
    """
    data = request.get_json(force=True) or {}
    media_id = (data.get('media_id') or '').strip()
    artist = (data.get('artist') or '').strip()
    title = (data.get('title') or '').strip()
    if not media_id:
        return jsonify({"error": "media_id is required"}), 400
    if not artist and not title:
        return jsonify({"error": "artist or title is required"}), 400
    store = getattr(current_app, 'media_library', None)
    if store is None:
        return jsonify({"error": "media library not configured"}), 503
    existing = store.get(media_id)
    if existing is None:
        return jsonify({"error": "media_id not found"}), 404
    # Preserve a field the caller left blank rather than wiping the existing value.
    store.set_metadata(media_id, artist or existing.get("artist", ""),
                       title or existing.get("title", ""))
    return jsonify({"success": True, "record": store.get(media_id)})


@routes_bp.route('/delete', methods=['POST'])
def delete_media():
    """Deletes a media file (only from download folder)."""
    file_path = request.json.get('file_path')
    if not file_path:
        return jsonify({"error": "file_path is required"}), 400

    media = current_app.media
    cfg = current_app.kj_config

    validated = media.validate_path(file_path)
    if not validated:
        return jsonify({"error": "Invalid file path"}), 400

    if not media.is_in_download_folder(validated):
        return jsonify({"error": "Can only delete files from the download folder"}), 403

    log_message(f"Received delete request for: {os.path.basename(validated)}", cfg)

    try:
        media.delete_file(validated)
    except Exception as e:
        log_message(f"Error deleting file: {e}", cfg)
        return jsonify({"error": f"Error deleting file: {e}"}), 500

    return jsonify({"success": True, "message": f"Deleted {os.path.basename(validated)}"})


@routes_bp.route('/rescan', methods=['POST'])
def handle_rescan():
    """Reloads config and triggers a full media folder rescan."""
    cfg = load_config()
    current_app.kj_config = cfg
    media = current_app.media
    media.config = cfg
    current_app.vlc.config = cfg
    log_message("Rescan requested - reloading config...", cfg)
    media.scan()
    return jsonify({"success": True, "count": len(media.index)})


@routes_bp.route('/filler_music', methods=['GET'])
def list_filler_music():
    """Returns a list of available filler music files."""
    filler_dir = current_app.kj_config.get('filler_music_dir', '')
    if not filler_dir:
        return jsonify([])
    try:
        files = [
            f for f in os.listdir(filler_dir)
            if f.endswith(('.mp3', '.wav', '.ogg', '.flac'))
        ]
        return jsonify(files)
    except FileNotFoundError:
        return jsonify([])


@routes_bp.route('/filler_music', methods=['POST'])
def set_filler_music():
    """Sets the filler music track and starts playing it at a random time."""
    blocked = _check_sleep_mode()
    if blocked:
        return blocked
    track_name = request.json.get('track_name')
    if not track_name:
        return jsonify({"error": "Track name is required"}), 400

    vlc = current_app.vlc
    cfg = current_app.kj_config
    filler_dir = cfg.get('filler_music_dir', '')
    new_track_path = os.path.join(filler_dir, track_name)
    if not os.path.exists(new_track_path):
        return jsonify({"error": "Track not found"}), 404

    filler_port = cfg.get('filler_vlc_port', 8081)
    filler_pw = cfg.get('filler_vlc_password', 'filler')

    vlc.current_filler_track = track_name
    vlc._save_state()
    log_message(f"Changing filler music to: {track_name}", cfg)

    # Enqueue the new track (always — updates playlist for when filler resumes)
    vlc.send_command(filler_port, filler_pw, "pl_stop")
    time.sleep(0.1)
    vlc.send_command(filler_port, filler_pw, "pl_empty")
    time.sleep(0.1)
    vlc.send_command(filler_port, filler_pw, f"in_enqueue&input={new_track_path}", is_path=True)
    time.sleep(0.1)

    # Skip playback if karaoke is active — ALSA device is held by karaoke VLC.
    # The track will start when karaoke ends and fade_in_filler is called.
    if vlc.karaoke_active:
        log_message(f"Filler track queued (karaoke active, not starting playback).", cfg)
        return jsonify({"success": True})

    vlc.send_command(filler_port, filler_pw, "pl_play")

    time.sleep(0.5)

    status = vlc.send_command(filler_port, filler_pw, "")
    if status and 'length' in status and status['length'] > 0:
        duration = status['length']
        random_time = random.randint(0, max(0, int(duration) - 5))
        log_message(f"Seeking filler music to {random_time}s (duration: {duration}s)", cfg)
        vlc.send_command(filler_port, filler_pw, f"seek&val={random_time}")

    vlc.send_command(filler_port, filler_pw, f"volume&val={vlc.filler_volume}")

    return jsonify({"success": True})


@routes_bp.route('/status')
def get_status():
    """Gets the status of the karaoke player."""
    global _browser_mode
    vlc = current_app.vlc
    media = current_app.media

    status = vlc.get_karaoke_status()

    # Get display name for currently playing file
    current_playing = None
    cpp = vlc.current_playing_path
    if cpp and cpp in media.index:
        current_playing = media.index[cpp].get('display_name')
    elif cpp:
        current_playing = os.path.basename(cpp)

    dl_queue = current_app.download_queue['items']

    # Browser mode status (Chromium)
    # Sync _browser_mode flag with actual Chromium process state
    chromium = getattr(current_app, 'chromium', None)
    browser_status = chromium.get_status() if chromium else {'running': False, 'pid': None, 'url': None}
    if _browser_mode and not browser_status['running']:
        _browser_mode = False
    elif not _browser_mode and browser_status['running']:
        _browser_mode = True
    browser_status['enabled'] = _browser_mode

    # Build rotation download status map
    rotation_downloads = {}
    if hasattr(current_app, 'rotation') and current_app.rotation:
        with current_app._get_current_object()._download_lock:
            for item in current_app.download_queue['items']:
                rot_id = item.get('rotation_entry_id')
                if rot_id:
                    rotation_downloads[str(rot_id)] = {
                        "status": item.get('status', 'unknown'),
                        "progress": item.get('progress', 0),
                        "file_path": item.get('file_path'),
                        "source": item.get('source'),
                        "source_detail": item.get('source_detail'),
                    }

    response_payload = {
        "state": status.get('state', 'stopped'),
        "current_playing": current_playing,
        "current_playing_path": cpp,
        "current_filler_track": vlc.current_filler_track,
        "time": status.get('time', 0),
        "length": status.get('length', 0),
        "audio_device": vlc.audio_device,
        "vlc_enabled": vlc.enabled,
        "audio_error": vlc.audio_error,
        "download_queue": dl_queue,
        "karaoke_volume": vlc.karaoke_volume,
        "filler_volume": vlc.filler_volume,
        "browser_mode": browser_status,
        "rotation_downloads": rotation_downloads,
        "pitch_semitones": vlc.pitch_semitones,
        "renderer": vlc.describe_renderer(),
        "player_alert": vlc.player_alert,
        "player_health_events": vlc.player_health_events,
    }
    try:
        response_payload["simple_mode"] = current_app.sing_store.is_simple_mode()
    except Exception:
        response_payload["simple_mode"] = False
    return jsonify(response_payload)


@routes_bp.route('/fix_audio', methods=['POST'])
def fix_audio():
    """Emergency recovery: restarts both VLC instances to fix audio device conflicts."""
    vlc = current_app.vlc
    cfg = current_app.kj_config
    log_message("Fix audio requested - restarting playback instances...", cfg)
    vlc.audio_error = False
    vlc.restart_instances()
    return jsonify({"success": True, "message": "Playback instances restarted."})


@routes_bp.route('/player-crash/ack', methods=['POST'])
def player_crash_ack():
    """Operator dismissed the video-player crash banner — acknowledge events up
    to the given id so /status stops surfacing them."""
    data = request.get_json(silent=True) or {}
    current_app.vlc.ack_player_alerts(data.get('id', 0))
    return jsonify({"success": True})


@routes_bp.route('/audio_device', methods=['GET'])
def get_audio_device():
    """Returns the current audio device and available devices."""
    return jsonify({
        "current": current_app.vlc.audio_device,
        "available": current_app.kj_config.get('audio_devices', {}),
    })


@routes_bp.route('/search')
def search_catalog():
    """Full-text search across the external media catalog."""
    catalog = current_app.catalog
    if not catalog.is_available():
        return jsonify({"error": "Catalog not available. Build it first via POST /catalog/build"}), 503

    query = request.args.get('q', '').strip()
    if not query:
        return jsonify([])

    try:
        limit = min(int(request.args.get('limit', 50)), 200)
    except (ValueError, TypeError):
        limit = 50
    try:
        offset = max(int(request.args.get('offset', 0)), 0)
    except (ValueError, TypeError):
        offset = 0

    results = catalog.search(query, limit=limit, offset=offset)
    return jsonify(results)


@routes_bp.route('/catalog/stats')
def catalog_stats():
    """Return catalog statistics."""
    catalog = current_app.catalog
    available = catalog.is_available()
    if available:
        stats = catalog.stats()
        return jsonify({"available": True, **stats})
    return jsonify({"available": False, "total": 0, "by_format": {}})


@routes_bp.route('/catalog/build', methods=['POST'])
def catalog_build():
    """Build or rebuild the external media catalog from a file list."""
    catalog = current_app.catalog
    cfg = current_app.kj_config

    # Accept path from request body or fall back to config
    data = request.get_json(silent=True) or {}
    file_list_path = data.get('file_list_path') or cfg.get('external_file_list', '')

    if not file_list_path:
        return jsonify({"error": "No file_list_path provided and external_file_list not configured"}), 400
    if not os.path.isfile(file_list_path):
        return jsonify({"error": f"File list not found: {file_list_path}"}), 404

    # Auto-detect mount prefix rewriting
    mount_replace = None
    mount = cfg.get('external_media_mount', '').rstrip('/')
    if mount:
        # Common pattern: Mac uses /Volumes/X, Pi uses /mnt/X
        volume_name = os.path.basename(mount)
        if volume_name:
            mac_prefix = f'/Volumes/{volume_name}/'
            pi_prefix = mount + '/'
            mount_replace = (mac_prefix, pi_prefix)

    log_message(f"Building external catalog from {file_list_path}...", cfg)
    count = catalog.build_from_file_list(file_list_path, mount_replace=mount_replace)
    log_message(f"External catalog built: {count} entries.", cfg)
    return jsonify({"success": True, "count": count})


@routes_bp.route('/audio_device', methods=['POST'])
def set_audio_device():
    """Switches the audio output device by restarting VLC instances."""
    vlc = current_app.vlc
    cfg = current_app.kj_config
    device = request.json.get('device')
    available = cfg.get('audio_devices', {})
    if not device:
        return jsonify({"error": "Device is required"}), 400
    if device not in available:
        return jsonify({"error": f"Unknown device '{device}'. Available: {list(available.keys())}"}), 400
    if device == vlc.audio_device:
        return jsonify({"success": True, "message": "Already using that device."})

    log_message(f"Switching audio device from '{vlc.audio_device}' to '{device}'...", cfg)
    vlc.audio_device = device
    threading.Thread(target=vlc.restart_instances).start()
    return jsonify({"success": True, "message": f"Switching to {available[device]}. VLC restarting..."})


@routes_bp.route('/audio/scan', methods=['POST'])
def scan_hdmi_audio():
    """Scans ALSA for HDMI audio devices and their jack connection state."""
    cfg = current_app.kj_config
    devices = {}

    # Parse aplay -l for HDMI device names (e.g. "card 0: ... device 3: HDMI 0 ...")
    try:
        aplay_out = subprocess.run(
            ['aplay', '-l'], capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        aplay_out = ''

    hdmi_pcms = {}  # dev_num -> name, e.g. {3: "HDMI 0", 7: "HDMI 1"}
    for m in re.finditer(
        r'card 0: .+device (\d+): (.+?) \[', aplay_out,
    ):
        dev_num, dev_name = m.group(1), m.group(2).strip()
        if 'HDMI' in dev_name.upper():
            hdmi_pcms[dev_num] = dev_name

    if not hdmi_pcms:
        # Fallback: assume standard Intel HDA HDMI devices
        for num, name in [('3', 'HDMI 0'), ('7', 'HDMI 1'),
                          ('8', 'HDMI 2'), ('9', 'HDMI 3')]:
            hdmi_pcms[num] = name

    # Parse amixer -c 0 contents for jack state
    try:
        amixer_out = subprocess.run(
            ['amixer', '-c', '0', 'contents'], capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        amixer_out = ''

    for dev_num, dev_name in hdmi_pcms.items():
        hw_id = f'hw:0,{dev_num}'
        # Check jack state: look for "HDMI/DP,pcm=N Jack" then "values=on/off"
        pattern = rf'HDMI/DP,pcm={dev_num} Jack.*?values=(on|off)'
        match = re.search(pattern, amixer_out, re.DOTALL)
        connected = match.group(1) == 'on' if match else False
        devices[hw_id] = {'name': dev_name, 'connected': connected}

    # Parse current hw device from /etc/asound.conf
    current_hw = None
    try:
        asound_conf = open('/etc/asound.conf').read()
        hw_match = re.search(r'"(hw:\d+,\d+)"', asound_conf)
        if hw_match:
            current_hw = hw_match.group(1)
    except FileNotFoundError:
        pass

    log_message(f"HDMI scan: {devices}", cfg)
    return jsonify({
        'devices': devices,
        'current': current_app.vlc.audio_device,
        'current_hw': current_hw,
    })


@routes_bp.route('/audio/switch-hdmi', methods=['POST'])
def switch_hdmi_audio():
    """Switches the HDMI audio output by rewriting /etc/asound.conf and restarting VLC."""
    device = (request.json or {}).get('device', '').strip()
    if not re.match(r'^hw:\d+,\d+$', device):
        return jsonify({'error': f'Invalid device format: {device}'}), 400

    cfg = current_app.kj_config
    vlc = current_app.vlc

    asound_conf = f"""# HDMI audio output — configured from web UI
# Active device: {device}
pcm.hdmiout {{
    type plug
    slave {{
        pcm "{device}"
    }}
}}

ctl.hdmiout {{
    type hw
    card 0
}}
"""

    log_message(f"Switching HDMI audio to {device}...", cfg)
    result = subprocess.run(
        ['sudo', 'tee', '/etc/asound.conf'],
        input=asound_conf, capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        return jsonify({'error': f'Failed to write asound.conf: {result.stderr}'}), 500

    # Ensure VLC uses hdmiout (the ALSA alias we just updated)
    if vlc.audio_device != 'hdmiout':
        vlc.audio_device = 'hdmiout'

    threading.Thread(target=vlc.restart_instances).start()
    return jsonify({'success': True, 'message': f'Switched HDMI to {device}. VLC restarting...'})


# --- Overlay Management ---

@routes_bp.route('/overlays', methods=['GET'])
def list_overlays():
    """Returns all configured overlays."""
    return jsonify(current_app.overlay_manager.list_overlays())


@routes_bp.route('/overlays/import', methods=['POST'])
def import_overlays():
    """Replaces all overlays with imported config."""
    data = request.get_json(silent=True)
    if data is None or not isinstance(data, list):
        return jsonify({"error": "Expected a JSON array of overlays"}), 400
    try:
        imported = current_app.overlay_manager.import_overlays(data)
        return jsonify({"success": True, "count": len(imported), "overlays": imported})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/overlays', methods=['POST'])
def create_overlay():
    """Creates a new overlay."""
    data = request.get_json(silent=True)
    if not data or 'type' not in data:
        return jsonify({"error": "type is required"}), 400
    try:
        overlay = current_app.overlay_manager.create_overlay(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    overlay = current_app.overlay_manager.get_overlay(overlay['id'])
    return jsonify(overlay), 201


@routes_bp.route('/overlays/presets/<preset_name>', methods=['POST'])
def create_overlay_preset(preset_name):
    """Create a new overlay from a named preset."""
    try:
        overlay = current_app.overlay_manager.create_preset(preset_name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if preset_name == 'scan-to-sing':
        try:
            url = _scan_to_sing_url()
            sync_event_url_overlays(current_app.overlay_manager, url)
            overlay = current_app.overlay_manager.get_overlay(overlay['id'])
        except Exception:
            import logging
            logging.getLogger(__name__).exception("scan-to-sing url sync failed")

    return jsonify(overlay), 201


def _scan_to_sing_url():
    """Compose the current public sing URL for the active token."""
    cfg = current_app.kj_config or {}
    token = current_app.sing_store.get_token()
    return get_event_url(cfg, token, scope='public')




@routes_bp.route('/overlays/<overlay_id>', methods=['GET'])
def get_overlay(overlay_id):
    """Returns a single overlay by ID."""
    overlay = current_app.overlay_manager.get_overlay(overlay_id)
    if not overlay:
        return jsonify({"error": "Overlay not found"}), 404
    return jsonify(overlay)


@routes_bp.route('/overlays/<overlay_id>', methods=['PUT'])
def update_overlay(overlay_id):
    """Updates an existing overlay."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body required"}), 400
    overlay = current_app.overlay_manager.update_overlay(overlay_id, data)
    if not overlay:
        return jsonify({"error": "Overlay not found"}), 404

    overlay = current_app.overlay_manager.get_overlay(overlay_id)
    return jsonify(overlay)


@routes_bp.route('/overlays/<overlay_id>', methods=['DELETE'])
def delete_overlay(overlay_id):
    """Deletes an overlay."""
    if current_app.overlay_manager.delete_overlay(overlay_id):
        return jsonify({"success": True})
    return jsonify({"error": "Overlay not found"}), 404


@routes_bp.route('/overlays/<overlay_id>/toggle', methods=['POST'])
def toggle_overlay(overlay_id):
    """Toggles the enabled state of an overlay."""
    overlay = current_app.overlay_manager.toggle_enabled(overlay_id)
    if not overlay:
        return jsonify({"error": "Overlay not found"}), 404
    return jsonify(overlay)


@routes_bp.route('/overlays/<overlay_id>/toggle-video', methods=['POST'])
def toggle_overlay_video(overlay_id):
    """Toggles the show_over_video state of an overlay."""
    overlay = current_app.overlay_manager.toggle_show_over_video(overlay_id)
    if not overlay:
        return jsonify({"error": "Overlay not found"}), 404
    return jsonify(overlay)


# --- Wallpaper ---

@routes_bp.route('/wallpaper', methods=['GET'])
def get_wallpaper():
    """Serve the current desktop wallpaper image as a thumbnail."""
    kjdata_dir = os.path.expanduser('~/kjdata')
    desktop_dir = os.path.join(APP_DIR, '..', 'desktop')
    # Prefer custom wallpaper in ~/kjdata/, fall back to git-tracked default
    for path in [
        os.path.join(kjdata_dir, 'wallpaper.jpg'),
        os.path.join(kjdata_dir, 'rotation-bg.png'),
        os.path.join(desktop_dir, 'rotation-bg.png'),
    ]:
        if os.path.exists(path):
            from flask import send_file
            return send_file(os.path.abspath(path), mimetype='image/jpeg')
    return jsonify({"error": "No wallpaper found"}), 404


@routes_bp.route('/wallpaper', methods=['POST'])
def upload_wallpaper():
    """Upload a new desktop wallpaper image.

    Saves the original, generates a 1080p rotation-bg.png for conky,
    and sets the XFCE desktop wallpaper on all monitors.
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in {'.jpg', '.jpeg', '.png', '.webp'}:
        return jsonify({"error": f"Unsupported image format: {ext}"}), 400

    # Store wallpapers in ~/kjdata/ (survives git pull/reset) and also write
    # rotation-bg.png to the desktop dir for conky (gitignored)
    kjdata_dir = os.path.expanduser('~/kjdata')
    os.makedirs(kjdata_dir, exist_ok=True)
    desktop_dir = os.path.abspath(os.path.join(APP_DIR, '..', 'desktop'))
    wallpaper_path = os.path.join(kjdata_dir, 'wallpaper.jpg')
    rotation_bg_path = os.path.join(desktop_dir, 'rotation-bg.png')

    try:
        from PIL import Image
        # Save original upload as wallpaper.jpg
        img = Image.open(file.stream)
        img = img.convert('RGB')
        img.save(wallpaper_path, 'JPEG', quality=95)
        cfg = current_app.kj_config
        log_message(f"Wallpaper uploaded: {file.filename} ({img.size[0]}x{img.size[1]})", cfg)

        # Generate 1080p rotation background for conky + backup in ~/kjdata/
        bg = img.resize((1920, 1080), Image.LANCZOS)
        bg.save(rotation_bg_path, 'PNG')
        bg.save(os.path.join(kjdata_dir, 'rotation-bg.png'), 'PNG')
        log_message("Generated rotation-bg.png (1920x1080)", cfg)

        # Set XFCE desktop wallpaper on all monitors
        _set_xfce_wallpaper(wallpaper_path)

    except Exception as e:
        return jsonify({"error": f"Failed to process image: {e}"}), 500

    return jsonify({"success": True, "size": list(img.size)})


def _set_xfce_wallpaper(image_path):
    """Set the XFCE desktop wallpaper on all monitors via xfconf-query."""
    try:
        uid_result = subprocess.run(
            ['id', '-u', 'nomad'], capture_output=True, text=True, timeout=5,
        )
        uid = uid_result.stdout.strip()
        env = os.environ.copy()
        env['DISPLAY'] = ':0'
        env['DBUS_SESSION_BUS_ADDRESS'] = f'unix:path=/run/user/{uid}/bus'

        # List all wallpaper properties
        result = subprocess.run(
            ['sudo', '-u', 'nomad', 'xfconf-query', '-c', 'xfce4-desktop', '-l'],
            capture_output=True, text=True, timeout=10, env=env,
        )
        props = [line.strip() for line in result.stdout.splitlines() if 'last-image' in line]

        for prop in props:
            subprocess.run(
                ['sudo', '-u', 'nomad', 'xfconf-query', '-c', 'xfce4-desktop',
                 '-p', prop, '-s', image_path],
                capture_output=True, text=True, timeout=5, env=env,
            )
    except Exception:
        pass  # Best-effort; wallpaper via conky still works


# --- Karaoke Nerds Search ---

@routes_bp.route('/karaoke-nerds/search', methods=['POST'])
def kn_search():
    """Search karaokenerds.com for web-only karaoke tracks."""
    data = request.get_json(silent=True) or {}
    query = data.get('query', '').strip()
    if not query or len(query) < 2:
        return jsonify({"error": "Query must be at least 2 characters"}), 400

    cfg = current_app.kj_config
    log_message(f"Karaoke Nerds search: {query}", cfg)
    results = karaoke_nerds.search(query, config=cfg)
    # Annotate each track with priority_rank and sort the per-song track
    # lists best-first so the frontend can render in order without
    # duplicating the brand registry.
    for song in results:
        version_priority.annotate_versions(
            song.get("tracks") or [], cfg, shape="rotation_search_kn")
        (song.get("tracks") or []).sort(
            key=lambda t: t.get("priority_rank", 9999))
    return jsonify(results)


@routes_bp.route('/karaoke-nerds/config', methods=['GET'])
def kn_get_config():
    """Returns the brand-priority config: two ranked lists + alias hints.

    Design spec: docs/archive/2026-05-22-choose-best-version-design.md § 3b.
    """
    cfg = current_app.kj_config

    def _list_or_default(key, default):
        v = cfg.get(key)
        if not v:
            return list(default)
        return [str(c).upper().strip() for c in v if str(c).strip()]

    aliases = {}
    for canonical, alias_list, _display in version_priority.COMMUNITY_BRANDS:
        aliases[canonical] = list(alias_list)
    for canonical, alias_list, _display in version_priority.COMMERCIAL_BRANDS:
        aliases[canonical] = list(alias_list)

    return jsonify({
        "priority_community": _list_or_default(
            "kn_priority_community", version_priority.COMMUNITY_DEFAULTS),
        "priority_commercial": _list_or_default(
            "kn_priority_commercial", version_priority.COMMERCIAL_DEFAULTS),
        "aliases": aliases,
    })


@routes_bp.route('/karaoke-nerds/config', methods=['POST'])
def kn_set_config():
    """Updates brand-priority config (two lists of canonical codes)."""
    data = request.get_json(silent=True) or {}
    community = data.get("priority_community")
    commercial = data.get("priority_commercial")

    if not isinstance(community, list) or not isinstance(commercial, list):
        return jsonify({
            "error": "priority_community and priority_commercial must be lists"
        }), 400

    community = [str(c).upper().strip() for c in community if str(c).strip()]
    commercial = [str(c).upper().strip() for c in commercial if str(c).strip()]

    valid_community = {c for (c, _, _) in version_priority.COMMUNITY_BRANDS}
    valid_commercial = {c for (c, _, _) in version_priority.COMMERCIAL_BRANDS}

    bad_community = [c for c in community if c not in valid_community]
    bad_commercial = [c for c in commercial if c not in valid_commercial]
    if bad_community or bad_commercial:
        problems = []
        if bad_community:
            problems.append(
                f"Unknown community codes: {bad_community}. "
                f"Valid: {sorted(valid_community)}")
        if bad_commercial:
            problems.append(
                f"Unknown commercial codes: {bad_commercial}. "
                f"Valid: {sorted(valid_commercial)}")
        return jsonify({"error": " | ".join(problems)}), 400

    current_app.kj_config['kn_priority_community'] = community
    current_app.kj_config['kn_priority_commercial'] = commercial
    save_config_value('kn_priority_community', community)
    save_config_value('kn_priority_commercial', commercial)
    log_message(
        f"Updated brand priorities: community={community}, commercial={commercial}",
        current_app.kj_config,
    )
    return jsonify({
        "priority_community": community,
        "priority_commercial": commercial,
    })


# --- YouTube Search ---

@routes_bp.route('/youtube/search', methods=['POST'])
def yt_search():
    """Searches YouTube for videos matching the query."""
    data = request.get_json(silent=True) or {}
    query = (data.get('query') or '').strip()
    if len(query) < 2:
        return jsonify({"error": "Query must be at least 2 characters"}), 400

    results = youtube_search.search(query, current_app.kj_config)
    return jsonify(results)


@routes_bp.route('/youtube/status', methods=['GET'])
def youtube_status():
    """Returns YouTube download engine health: yt-dlp, EJS, Deno, cookies."""
    cfg = current_app.kj_config
    status = youtube_health.get_youtube_status(cfg)
    return jsonify(status)


@routes_bp.route('/youtube/cookies', methods=['POST'])
def youtube_upload_cookies():
    """Validates and saves YouTube cookies in Netscape format."""
    data = request.get_json(silent=True) or {}
    content = data.get('content', '')
    if not content:
        return jsonify({'error': 'Cookie content is required'}), 400

    valid, msg = youtube_health.validate_cookies_format(content)
    if not valid:
        return jsonify({'error': msg}), 400

    cfg = current_app.kj_config
    cookies_path = cfg.get('youtube_cookies_file', '')
    if not cookies_path:
        return jsonify({'error': 'youtube_cookies_file not configured'}), 500

    ok, write_msg = youtube_health.write_cookies_file(content, cookies_path)
    if not ok:
        return jsonify({'error': write_msg}), 500

    log_message(f"YouTube cookies uploaded ({msg})", cfg)
    return jsonify({'success': True, 'message': msg})


@routes_bp.route('/youtube/cookies', methods=['DELETE'])
def youtube_delete_cookies():
    """Deletes the YouTube cookies file."""
    cfg = current_app.kj_config
    cookies_path = cfg.get('youtube_cookies_file', '')
    if not cookies_path:
        return jsonify({'error': 'youtube_cookies_file not configured'}), 500

    if os.path.exists(cookies_path):
        os.remove(cookies_path)
        log_message("YouTube cookies deleted.", cfg)
        return jsonify({'success': True, 'message': 'Cookies deleted'})

    return jsonify({'success': True, 'message': 'No cookies file to delete'})


@routes_bp.route('/youtube/upgrade-ytdlp', methods=['POST'])
def youtube_upgrade_ytdlp():
    """Upgrades yt-dlp to the latest version and restarts the service."""
    cfg = current_app.kj_config
    log_message("YouTube: yt-dlp upgrade requested from web UI.", cfg)

    ok, msg = youtube_health.upgrade_ytdlp()
    if not ok:
        log_message(f"YouTube: yt-dlp upgrade failed: {msg}", cfg)
        return jsonify({'error': msg}), 500

    log_message(f"YouTube: {msg}, restarting service...", cfg)

    def do_restart():
        time.sleep(1)
        subprocess.run(['sudo', 'systemctl', 'restart', 'kj-controller'])

    threading.Thread(target=do_restart, daemon=True).start()
    return jsonify({'success': True, 'message': msg, 'restarting': True})


# --- Divebar Search ---

@routes_bp.route('/divebar/search', methods=['POST'])
def divebar_search():
    """Search the Divebar karaoke catalog for community tracks."""
    data = request.get_json(silent=True) or {}
    query = data.get('query', '').strip()
    if not query or len(query) < 2:
        return jsonify({"error": "Query must be at least 2 characters"}), 400

    cfg = current_app.kj_config
    if not cfg.get('divebar_api_url'):
        return jsonify({"error": "Divebar not configured"}), 503

    log_message(f"Divebar search: {query}", cfg)
    results = divebar.search(query, config=cfg)
    return jsonify(results)


@routes_bp.route('/divebar/kn-lookup', methods=['POST'])
def divebar_kn_lookup():
    """Look up which KN song IDs have Divebar versions."""
    data = request.get_json(silent=True) or {}
    kn_ids = data.get('kn_ids', [])
    if not kn_ids or not isinstance(kn_ids, list):
        return jsonify({"error": "kn_ids list required"}), 400

    cfg = current_app.kj_config
    if not cfg.get('divebar_api_url'):
        return jsonify({})

    matches = divebar.lookup_kn_ids(kn_ids, config=cfg)
    return jsonify(matches)


@routes_bp.route('/divebar/status', methods=['GET'])
def divebar_status():
    """Get Divebar catalog status: sync progress, file counts, pipeline health."""
    cfg = current_app.kj_config
    if not cfg.get('divebar_api_url'):
        return jsonify({"error": "Divebar not configured", "configured": False}), 503

    stats = divebar.get_stats(config=cfg)
    if stats:
        stats["configured"] = True
        return jsonify(stats)
    return jsonify({"error": "Could not fetch Divebar status", "configured": True}), 502


@routes_bp.route('/divebar/refresh', methods=['POST'])
def divebar_refresh():
    """Trigger an on-demand refresh of the Divebar pipeline.

    Force-runs the Drive→BigQuery index, Drive→GCS file sync, and xref-rebuild
    scheduler jobs so a track just published to the Nomad Drive (e.g. via
    gen.nomadkaraoke.com) shows up in the catalog without waiting for the
    nightly 2/3/6 AM runs.
    """
    cfg = current_app.kj_config
    if not cfg.get('divebar_api_url'):
        return jsonify({"error": "Divebar not configured"}), 503
    if not cfg.get('divebar_refresh_token'):
        return jsonify({"error": "Divebar refresh token not configured"}), 503

    log_message("Divebar refresh triggered", cfg)
    result = divebar.refresh(config=cfg)
    if result.get("status") == "ok":
        return jsonify(result)
    return jsonify({"error": result.get("message", "Refresh failed")}), 502


@routes_bp.route('/divebar/download', methods=['POST'])
def divebar_download():
    """Download a Divebar track by file_id. Queues it like a YouTube download.

    Body: ``{file_id (required), artist, title, brand_code}``. Filename is
    built server-side via ``build_divebar_filename`` so all enqueue paths
    produce consistent on-disk names.
    """
    data = request.get_json(silent=True) or {}
    file_id = data.get('file_id', '').strip()
    if not file_id:
        return jsonify({"error": "file_id is required"}), 400

    artist = (data.get('artist') or '').strip()
    title = (data.get('title') or '').strip()
    brand_code = (data.get('brand_code') or '').strip()
    fmt = (data.get('format') or '').strip()

    cfg = current_app.kj_config
    app = current_app._get_current_object()

    # Dedup-skip: if we already have this community file on disk, don't re-download.
    existing = _existing_media_for(app, _prospective_media_id(
        "divebar", file_id=file_id, brand_code=brand_code))
    if existing:
        return jsonify({"success": True, "deduped": True,
                        "file_path": existing["file_path"]})

    # Resolve the download spec — pairing a loose CDG with its sibling audio so a
    # bare, silent .cdg is never queued. divebar_ext keeps CDG/zip files off .mp4.
    spec, err = _resolve_divebar_spec(file_id, artist, title, brand_code, fmt, cfg)
    if err:
        msg, status = err
        return jsonify({"error": msg}), status

    # Reuse the existing download queue with the Drive/GCS URL(s)
    from uuid import uuid4
    with app._download_lock:
        items = app.download_queue['items']
        active = [i for i in items if i['status'] in ('queued', 'downloading')]
        if len(active) >= 5:
            return jsonify({"error": "Queue is full (max 5)"}), 409

        item = {
            'id': str(uuid4()),
            'status': 'queued',
            'error': None,
            'file_path': None,
            'added_at': time.time(),
            'completed_at': None,
            **spec,
        }
        items.append(item)
        log_message(f"Queued Divebar download: {item['title']}", cfg)

        if not app.download_queue['worker_running']:
            app.download_queue['worker_running'] = True
            threading.Thread(target=_download_worker, args=[app], daemon=True).start()

    return jsonify({"success": True, "id": item['id']})


# --- Display Resolution ---

def _query_xrandr():
    """Parse xrandr output for connected output, available modes, and current mode."""
    try:
        result = subprocess.run(
            ['xrandr'], capture_output=True, text=True, timeout=5,
            env={**os.environ, 'DISPLAY': ':0'},
        )
        output = result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    connected_output = None
    modes = []
    current = None

    for line in output.splitlines():
        # Match connected output line, e.g. "HDMI-1 connected primary 1920x1080+0+0 ..."
        if ' connected' in line and not ' disconnected' in line:
            connected_output = line.split()[0]
        # Match mode lines, e.g. "   1920x1080     60.00*+  50.00  ..."
        elif connected_output and line.startswith('   '):
            parts = line.split()
            if parts and re.match(r'^\d+x\d+$', parts[0]):
                mode = parts[0]
                if mode not in modes:
                    modes.append(mode)
                if '*' in line and current is None:
                    current = mode

    if not connected_output:
        return None

    return {'output': connected_output, 'modes': modes, 'current': current}


@routes_bp.route('/display/resolution', methods=['GET'])
def get_display_resolution():
    """Returns the current display resolution and available modes."""
    info = _query_xrandr()
    if not info:
        return jsonify({'current': '', 'available': [], 'output': '', 'error': 'xrandr not available'})
    return jsonify({
        'current': info['current'] or '',
        'available': info['modes'],
        'output': info['output'],
    })


@routes_bp.route('/display/resolution', methods=['POST'])
def set_display_resolution():
    """Sets the display resolution via xrandr (temporary — not persisted to config)."""
    resolution = (request.json or {}).get('resolution', '').strip()
    if not resolution:
        return jsonify({'error': 'Resolution is required'}), 400

    info = _query_xrandr()
    if not info:
        return jsonify({'error': 'xrandr not available'}), 503

    if resolution not in info['modes']:
        return jsonify({'error': f"Resolution '{resolution}' not available. Options: {info['modes']}"}), 400

    cfg = current_app.kj_config
    log_message(f"Setting display resolution to {resolution} on {info['output']}...", cfg)

    result = subprocess.run(
        ['xrandr', '--output', info['output'], '--mode', resolution],
        capture_output=True, text=True, timeout=10,
        env={**os.environ, 'DISPLAY': ':0'},
    )
    if result.returncode != 0:
        return jsonify({'error': f'xrandr failed: {result.stderr.strip()}'}), 500

    return jsonify({'success': True, 'message': f'Resolution set to {resolution}'})


# --- AV Status Helpers ---

def _get_edid_monitor_name(connector):
    """Read EDID binary from sysfs and extract the monitor name descriptor (tag 0xFC)."""
    # Map xrandr connector names to DRM sysfs names
    drm_name = connector.replace('HDMI-', 'HDMI-A-')  # HDMI-1 -> HDMI-A-1, DP-1 stays DP-1
    paths = glob.glob(f'/sys/class/drm/card*-{drm_name}/edid')
    if not paths:
        return None
    try:
        with open(paths[0], 'rb') as f:
            edid = f.read()
        if len(edid) < 128:
            return None
        # Verify EDID header: 00 FF FF FF FF FF FF 00
        if edid[:8] != b'\x00\xff\xff\xff\xff\xff\xff\x00':
            return None
        # Parse four 18-byte descriptor blocks starting at byte 54
        for i in range(4):
            offset = 54 + i * 18
            block = edid[offset:offset + 18]
            if len(block) < 18:
                break
            # Monitor Name descriptor: first two bytes 0x00 0x00, byte 3 = 0xFC
            if block[0] == 0 and block[1] == 0 and block[2] == 0 and block[3] == 0xFC:
                name = block[5:18].decode('ascii', errors='replace')
                return name.replace('\n', '').strip()
    except Exception:
        pass
    return None


def _get_eld_info():
    """Read all valid ELD (EDID-Like Data) entries from /proc/asound for audio capabilities."""
    results = []
    try:
        for eld_path in sorted(glob.glob('/proc/asound/card*/eld#*')):
            try:
                with open(eld_path) as f:
                    content = f.read()
                info = {'path': eld_path}
                for line in content.splitlines():
                    # ELD files use tab-separated key/value
                    if '\t' in line:
                        k, v = line.split('\t', 1)
                        info[k.strip()] = v.strip()
                if info.get('monitor_present') == '1':
                    results.append(info)
            except Exception:
                pass
    except Exception:
        pass
    return results


def _get_pipewire_profile():
    """Get the active PipeWire card profile for the Intel PCH audio card."""
    try:
        result = subprocess.run(
            ['sudo', '-u', 'nomad', 'env', 'XDG_RUNTIME_DIR=/run/user/1000',
             'pactl', 'list', 'cards'],
            capture_output=True, text=True, timeout=5,
        )
        output = result.stdout
        # Find the Intel PCH card section and extract Active Profile
        # The card name contains 'pci-0000_00_1f' for Intel PCH
        in_target_card = False
        for line in output.splitlines():
            if 'pci-0000_00_1f' in line or 'alsa_card' in line.lower():
                in_target_card = True
            if in_target_card and 'Active Profile:' in line:
                return line.split('Active Profile:', 1)[1].strip()
    except Exception:
        pass
    return None


def _get_av_video_status():
    """Get comprehensive video output status from xrandr."""
    try:
        result = subprocess.run(
            ['xrandr'], capture_output=True, text=True, timeout=5,
            env={**os.environ, 'DISPLAY': ':0'},
        )
        output = result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {'connectors': {}, 'active_output': None}

    connectors = {}
    current_connector = None
    current_connected = False

    for line in output.splitlines():
        # Connector status line: "HDMI-1 connected 1920x1080+0+0 ..."
        # or "HDMI-2 disconnected" or "HDMI-1 connected primary 1920x1080+0+0 ..."
        conn_match = re.match(r'^(\S+)\s+(connected|disconnected)', line)
        if conn_match:
            name = conn_match.group(1)
            connected = conn_match.group(2) == 'connected'
            current_connector = name
            current_connected = connected
            connectors[name] = {
                'connected': connected,
                'current_resolution': None,
                'current_refresh': None,
                'available_modes': [],
                'edid_name': None,
            }
        elif current_connector and current_connected and line.startswith('   '):
            # Mode line: "   1920x1080     60.00*+  50.00 ..."
            parts = line.split()
            if parts and re.match(r'^\d+x\d+$', parts[0]):
                mode = parts[0]
                if mode not in connectors[current_connector]['available_modes']:
                    connectors[current_connector]['available_modes'].append(mode)
                if '*' in line and connectors[current_connector]['current_resolution'] is None:
                    for part in parts[1:]:
                        if '*' in part:
                            refresh = part.replace('*', '').replace('+', '')
                            connectors[current_connector]['current_resolution'] = mode
                            connectors[current_connector]['current_refresh'] = refresh
                            break

    # Get EDID monitor names for connected outputs
    for conn_name, conn_info in connectors.items():
        if conn_info['connected']:
            conn_info['edid_name'] = _get_edid_monitor_name(conn_name)

    # Active output: first connected one with a current resolution
    active_output = None
    for conn_name, conn_info in connectors.items():
        if conn_info['connected'] and conn_info['current_resolution']:
            active_output = conn_name
            break

    return {'connectors': connectors, 'active_output': active_output}


def _get_av_audio_status(vlc_device):
    """Get comprehensive audio output status from ALSA, ELD, and PipeWire."""
    # Get HDMI PCM device names from aplay
    try:
        aplay_out = subprocess.run(
            ['aplay', '-l'], capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        aplay_out = ''

    hdmi_pcms = {}
    for m in re.finditer(r'card 0: .+device (\d+): (.+?) \[', aplay_out):
        dev_num, dev_name = m.group(1), m.group(2).strip()
        if 'HDMI' in dev_name.upper():
            hdmi_pcms[dev_num] = dev_name

    if not hdmi_pcms:
        for num, name in [('3', 'HDMI 0'), ('7', 'HDMI 1'), ('8', 'HDMI 2'), ('9', 'HDMI 3')]:
            hdmi_pcms[num] = name

    # Get jack states and IEC958 switch states from amixer
    try:
        amixer_out = subprocess.run(
            ['amixer', '-c', '0', 'contents'], capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        amixer_out = ''

    # Parse IEC958 Playback Switch states (indexed 0-3).
    # Note: amixer omits ",index=0" for the first entry — treat no-index as 0.
    iec958_states = {}
    for m in re.finditer(
        r"iface=MIXER,name='IEC958 Playback Switch'(?:,index=(\d+))?.*?values=(on|off)",
        amixer_out, re.DOTALL,
    ):
        idx = int(m.group(1)) if m.group(1) is not None else 0
        iec958_states[idx] = m.group(2) == 'on'

    # Build per-PCM-device info
    hdmi_pcm_info = {}
    iec958_idx = 0
    for dev_num in sorted(hdmi_pcms.keys(), key=int):
        dev_name = hdmi_pcms[dev_num]
        hw_id = f'hw:0,{dev_num}'

        jack_pattern = rf'HDMI/DP,pcm={dev_num} Jack.*?values=(on|off)'
        jack_match = re.search(jack_pattern, amixer_out, re.DOTALL)
        connected = jack_match.group(1) == 'on' if jack_match else False

        hdmi_pcm_info[hw_id] = {
            'name': dev_name,
            'connected': connected,
            'iec958': iec958_states.get(iec958_idx, False),
        }
        iec958_idx += 1

    # Get ELD info (monitor names from connected HDMI audio)
    eld_entries = _get_eld_info()
    eld_names = [e.get('monitor_name', '') for e in eld_entries if e.get('eld_valid') == '1']

    # Parse current hw device from /etc/asound.conf
    asound_hw = None
    try:
        with open('/etc/asound.conf') as f:
            asound_conf = f.read()
        hw_match = re.search(r'"(hw:\d+,\d+)"', asound_conf)
        if hw_match:
            asound_hw = hw_match.group(1)
    except FileNotFoundError:
        pass

    pipewire_profile = _get_pipewire_profile()
    pipewire_ok = pipewire_profile is not None and 'analog-stereo' in pipewire_profile

    return {
        'vlc_device': vlc_device,
        'asound_hw': asound_hw,
        'hdmi_pcms': hdmi_pcm_info,
        'eld_names': eld_names,
        'analog_device': 'hw:0,0',
        'pipewire_profile': pipewire_profile,
        'pipewire_ok': pipewire_ok,
        'iec958_states': iec958_states,
    }


# --- AV Output Routes ---

@routes_bp.route('/av/status', methods=['GET'])
def av_status():
    """Returns comprehensive AV output status: video connectors, audio devices, health."""
    cfg = current_app.kj_config
    vlc = current_app.vlc
    video = _get_av_video_status()
    audio = _get_av_audio_status(vlc.audio_device)

    # Health checks
    active_pcm = audio.get('asound_hw')
    active_pcm_info = audio['hdmi_pcms'].get(active_pcm, {}) if active_pcm else {}
    any_iec958_on = any(audio['iec958_states'].values()) if audio['iec958_states'] else False

    health = {
        'video_ok': video['active_output'] is not None,
        'audio_ok': bool(active_pcm_info.get('connected') and active_pcm_info.get('iec958')),
        'asound_matches_active_jack': bool(active_pcm_info.get('connected')),
        'pipewire_profile_ok': audio['pipewire_ok'],
        'iec958_ok': any_iec958_on,
    }

    # Browser audio config
    browser_audio = cfg.get('browser_audio_device', 'same')
    from chromium import PW_PROFILES, ALSA_TO_PW_PROFILE, PW_PROFILE_ANALOG
    if browser_audio == 'same':
        # Resolve what "same as VLC" actually means in PipeWire terms
        resolved = ALSA_TO_PW_PROFILE.get(vlc.audio_device, PW_PROFILE_ANALOG)
    else:
        resolved = browser_audio

    audio['browser_audio'] = {
        'setting': browser_audio,
        'resolved_profile': resolved,
        'available_profiles': PW_PROFILES,
    }

    audio_monitor = {}
    if hasattr(current_app, 'audio_monitor'):
        audio_monitor = current_app.audio_monitor.status()

    return jsonify({'video': video, 'audio': audio, 'health': health, 'audio_monitor': audio_monitor})


@routes_bp.route('/av/browser-audio', methods=['POST'])
def av_set_browser_audio():
    """Set the browser audio output device (PipeWire profile or 'same' to follow VLC)."""
    cfg = current_app.kj_config
    device = (request.json or {}).get('device', '').strip()
    if not device:
        return jsonify({"error": "Device is required"}), 400

    from chromium import PW_PROFILES
    valid = {'same'} | set(PW_PROFILES.keys()) | set(PW_PROFILES.values())
    if device not in valid:
        return jsonify({"error": f"Unknown browser audio device '{device}'"}), 400

    # Normalize: if a profile key like 'hdmi' was given, store the full profile string
    if device in PW_PROFILES:
        device = PW_PROFILES[device]

    save_config_value('browser_audio_device', device)
    cfg['browser_audio_device'] = device
    log_message(f"Browser audio device set to '{device}'.", cfg)
    return jsonify({"success": True, "device": device})


@routes_bp.route('/av/reset', methods=['POST'])
def av_reset():
    """Runs fix-hdmi-audio.sh to restore the full known-good AV state.

    Resets: ALSA hdmiout alias, IEC958 switches, PipeWire profile, display resolution.
    Then resets VLC audio device to 'hdmiout' and restarts VLC instances.
    No state is persisted to config.json.
    """
    cfg = current_app.kj_config
    vlc = current_app.vlc

    # Stop audio monitor if active (must happen before ALSA reset)
    if hasattr(current_app, 'audio_monitor') and current_app.audio_monitor.active:
        log_message("AV reset: stopping audio monitor first...", cfg)
        current_app.audio_monitor.stop()

    script_path = os.path.join(APP_DIR, 'fix-hdmi-audio.sh')
    log_message("AV reset requested — running fix-hdmi-audio.sh...", cfg)

    try:
        result = subprocess.run(
            ['sudo', script_path],  # must run as root to write /etc/asound.conf
            capture_output=True, text=True, timeout=30,
        )
        script_output = result.stdout + result.stderr
        log_message(f"fix-hdmi-audio.sh output: {script_output.strip()}", cfg)

        if result.returncode != 0:
            return jsonify({'error': f'fix-hdmi-audio.sh failed (exit {result.returncode}): {script_output.strip()}'}), 500
    except Exception as e:
        return jsonify({'error': f'Could not run fix-hdmi-audio.sh: {e}'}), 500

    # Reset VLC to use hdmiout (not persisted to config)
    vlc.audio_device = 'hdmiout'
    threading.Thread(target=vlc.restart_instances).start()

    log_message("AV reset complete — VLC restarting with hdmiout.", cfg)
    return jsonify({'success': True, 'message': 'AV reset complete. VLC restarting...'})


@routes_bp.route('/av/vlc-device', methods=['POST'])
def av_set_vlc_device():
    """Sets the ALSA audio output device for both karaoke player and filler.

    Accepts any valid ALSA device string (hw:X,Y format) or named device
    from the audio_devices config. Persists to config.default_audio_device
    and restarts both playback instances so the new device takes effect.
    """
    vlc = current_app.vlc
    cfg = current_app.kj_config
    device = (request.json or {}).get('device', '').strip()

    if not device:
        return jsonify({'error': 'device is required'}), 400

    # Accept hw:X,Y format directly, or named devices from config
    if not re.match(r'^hw:\d+,\d+$', device):
        available = cfg.get('audio_devices', {})
        if device not in available:
            return jsonify({'error': f"Unknown device '{device}'. Use hw:X,Y format or a configured device name."}), 400

    if device == vlc.audio_device and cfg.get('default_audio_device') == device:
        return jsonify({'success': True, 'message': 'Already using that device.'})

    log_message(f"AV: setting playback audio device to '{device}' (persisted)...", cfg)
    save_config_value('default_audio_device', device)
    cfg['default_audio_device'] = device
    vlc.audio_device = device
    threading.Thread(target=vlc.restart_instances).start()
    return jsonify({'success': True, 'message': f'Switching playback to {device}. Restarting...'})


# --- Audio Monitor Routes ---

@routes_bp.route('/audio-monitor/status', methods=['GET'])
def audio_monitor_status():
    """Returns audio monitor state."""
    return jsonify(current_app.audio_monitor.status())


@routes_bp.route('/audio-monitor/start', methods=['POST'])
def audio_monitor_start():
    """Enable audio monitor: switch to PipeWire, start capture stream."""
    monitor = current_app.audio_monitor
    if monitor.active:
        return jsonify({'success': True, 'stream_url': '/audio-monitor/stream', 'message': 'Already running.'})
    threading.Thread(target=monitor.start).start()
    return jsonify({'success': True, 'stream_url': '/audio-monitor/stream'})


@routes_bp.route('/audio-monitor/stop', methods=['POST'])
def audio_monitor_stop():
    """Disable audio monitor: stop capture, restore ALSA."""
    monitor = current_app.audio_monitor
    if not monitor.active:
        return jsonify({'success': True, 'message': 'Already stopped.'})
    threading.Thread(target=monitor.stop).start()
    return jsonify({'success': True})


@routes_bp.route('/audio-monitor/stream', methods=['GET'])
def audio_monitor_stream():
    """Chunked HTTP audio stream (audio/mpeg). Single client only."""
    monitor = current_app.audio_monitor
    if not monitor.active or not monitor._ffmpeg_proc:
        return jsonify({'error': 'Audio monitor not active'}), 404
    if monitor._client_connected:
        return jsonify({'error': 'Another client is already connected'}), 409
    return Response(monitor.stream_generator(), mimetype='audio/mpeg')


# --- System Control ---

@routes_bp.route('/system/restart-app', methods=['POST'])
def restart_app():
    """Restarts the kj-controller service via systemctl."""
    cfg = current_app.kj_config
    log_message("System: restart-app requested from web UI.", cfg)

    def do_restart():
        time.sleep(1)
        subprocess.run(['sudo', 'systemctl', 'restart', 'kj-controller'])

    threading.Thread(target=do_restart, daemon=True).start()
    return jsonify({"success": True, "message": "Restarting KJ Controller..."})


@routes_bp.route('/system/update', methods=['POST'])
def system_update():
    """Pulls latest code from GitHub and restarts the service."""
    cfg = current_app.kj_config
    log_message("System: update requested from web UI.", cfg)

    repo_dir = os.path.dirname(APP_DIR)  # kjbox/ (parent of kj-controller/)

    # Run git pull synchronously so we can report the result
    result = subprocess.run(
        ['git', 'pull', 'origin', 'main'],
        cwd=repo_dir, capture_output=True, text=True, timeout=30,
    )

    if result.returncode != 0:
        log_message(f"System: git pull failed: {result.stderr}", cfg)
        return jsonify({"error": f"git pull failed: {result.stderr.strip()}"}), 500

    pull_output = result.stdout.strip()
    log_message(f"System: git pull result: {pull_output}", cfg)

    def do_restart():
        time.sleep(1)
        subprocess.run(['sudo', 'systemctl', 'restart', 'kj-controller'])

    threading.Thread(target=do_restart, daemon=True).start()
    return jsonify({
        "success": True,
        "message": pull_output,
        "restarting": True,
    })


@routes_bp.route('/system/autodeploy', methods=['GET'])
def autodeploy_status():
    """Returns whether kj-autodeploy service is active."""
    result = subprocess.run(
        ['systemctl', 'is-active', 'kj-autodeploy'],
        capture_output=True, text=True,
    )
    active = result.stdout.strip() == 'active'
    return jsonify({"active": active})


@routes_bp.route('/system/autodeploy', methods=['POST'])
def autodeploy_toggle():
    """Starts/stops and enables/disables the kj-autodeploy service."""
    cfg = current_app.kj_config
    data = request.get_json() or {}
    enable = data.get('active', False)
    log_message(f"System: autodeploy {'enable' if enable else 'disable'} requested from web UI.", cfg)
    if enable:
        subprocess.run(['sudo', 'systemctl', 'enable', '--now', 'kj-autodeploy'])
    else:
        subprocess.run(['sudo', 'systemctl', 'disable', '--now', 'kj-autodeploy'])
    # Verify actual state
    result = subprocess.run(
        ['systemctl', 'is-active', 'kj-autodeploy'],
        capture_output=True, text=True,
    )
    active = result.stdout.strip() == 'active'
    return jsonify({"active": active})


# --- Sleep Mode ---

@routes_bp.route('/system/sleep-mode', methods=['GET'])
def sleep_mode_status():
    """Returns current sleep mode status."""
    sleep_mgr = current_app.sleep_manager
    return jsonify(sleep_mgr.get_status())


@routes_bp.route('/system/sleep-mode', methods=['POST'])
def sleep_mode_toggle():
    """Enter or exit sleep mode."""
    cfg = current_app.kj_config
    sleep_mgr = current_app.sleep_manager
    data = request.get_json() or {}
    active = data.get('active', False)

    sing_store = getattr(current_app, 'sing_store', None)
    if active:
        # Close public requests *before* the device goes quiet so no one
        # submits into a dead queue. We do NOT re-enable on exit — the KJ
        # must flip it back manually to prevent a surprise re-opening.
        if sing_store is not None:
            try:
                sing_store.set_enabled(False)
            except Exception:
                pass
        result = sleep_mgr.enter_sleep(cfg, vlc=current_app.vlc)
    else:
        result = sleep_mgr.exit_sleep(cfg, vlc=current_app.vlc)

    return jsonify(result)


@routes_bp.route('/system/reboot', methods=['POST'])
def system_reboot():
    """Reboots the entire system."""
    cfg = current_app.kj_config
    log_message("System: reboot requested from web UI.", cfg)

    def do_reboot():
        time.sleep(1)
        subprocess.run(['sudo', 'reboot'])

    threading.Thread(target=do_reboot, daemon=True).start()
    return jsonify({"success": True, "message": "Rebooting system..."})


@routes_bp.route('/system/shutdown', methods=['POST'])
def system_shutdown():
    """Shuts down the entire system."""
    cfg = current_app.kj_config
    log_message("System: shutdown requested from web UI.", cfg)

    def do_shutdown():
        time.sleep(1)
        subprocess.run(['sudo', 'shutdown', '-h', 'now'])

    threading.Thread(target=do_shutdown, daemon=True).start()
    return jsonify({"success": True, "message": "Shutting down system..."})


# --- System Stats ---

@routes_bp.route('/system/stats', methods=['GET'])
def system_stats():
    """Returns CPU, memory, and disk usage for the system stats widget."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        return jsonify({
            "cpu_percent": cpu,
            "mem_percent": mem.percent,
            "mem_used_gb": round(mem.used / (1024**3), 1),
            "mem_total_gb": round(mem.total / (1024**3), 1),
            "disk_percent": disk.percent,
            "disk_used_gb": round(disk.used / (1024**3), 1),
            "disk_total_gb": round(disk.total / (1024**3), 1),
        })
    except ImportError:
        return jsonify({"error": "psutil not installed"}), 501
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- Performance monitor ---

_PERF_CONTROLS = ('overlay', 'compositor', 'gpu-clock')


@routes_bp.route('/perf/stream', methods=['GET'])
def perf_stream():
    """Return the perf ring buffer (latest sample + ~5 min history).

    Polled ~1 Hz by the Performance panel, and only while it is open.
    """
    sampler = getattr(current_app, 'perf_sampler', None)
    if sampler is None:
        return jsonify({"error": "perf monitor unavailable"}), 501
    return jsonify(sampler.snapshot())


@routes_bp.route('/perf/toggle/<control>', methods=['POST'])
def perf_toggle(control):
    """A/B control: start/stop overlay, toggle compositor, pin/unpin GPU clock."""
    if control not in _PERF_CONTROLS:
        return jsonify({"error": f"unknown control: {control}"}), 400
    sampler = getattr(current_app, 'perf_sampler', None)
    if sampler is None:
        return jsonify({"error": "perf monitor unavailable"}), 501
    raw_on = (request.get_json(silent=True) or {}).get('on', True)
    # Accept a real JSON bool; tolerate string/number payloads from curl without
    # treating the string "false"/"0" as truthy.
    on = raw_on if isinstance(raw_on, bool) else str(raw_on).strip().lower() not in ('false', '0', '', 'none')
    ok, state, msg = sampler.toggle(control, on)
    return jsonify({"success": ok, "on": state, "message": msg}), (200 if ok else 500)


# --- Rotation ---


def _add_songs_sung(entries, rotation):
    """Add songs_sung field to each entry.

    For multi-singer entries (singers_json not null), uses the minimum count
    across all singers in the group.
    For legacy entries, matches on the singer display string.
    """
    import json as _json
    counts = rotation.store.get_songs_sung_counts()
    for entry in entries:
        singers_json = entry.get("singers_json")
        if singers_json:
            try:
                names = _json.loads(singers_json)
            except (ValueError, TypeError):
                names = None
            if isinstance(names, list) and names:
                individual_counts = [counts.get(n.strip().lower(), 0) for n in names]
                entry["songs_sung"] = min(individual_counts)
                continue
        entry["songs_sung"] = counts.get(entry["singer"].lower(), 0)


def _add_wait_pills(entries, rotation):
    """Add the two fields the rotation UI renders as the singer "happiness"
    pills (alongside ``songs_sung``, added separately):

    - ``last_sang_minutes``: whole minutes since the singer most recently
      finished a song tonight, or ``None`` if they have not sung yet. Kept for
      tooltip wording ("last sang X ago").
    - ``wait_minutes``: how long the singer has been waiting for their next
      turn — minutes since they last sang, or, for a singer who has NOT sung,
      minutes since their first song was entered into the rotation. ``None``
      only when no timestamp exists at all (rendered as ∞ / red). Drives the
      wait pill's value and colour.

    For multi-singer entries we surface the LONGEST wait across the group (max
    minutes) so both figures track the most under-served member — consistent
    with ``songs_sung`` showing the least-sung member's count.
    """
    import json as _json
    last_sang = rotation.store.get_last_sang_times()
    first_entered = rotation.store.get_first_entered_times()
    for entry in entries:
        singers_json = entry.get("singers_json")
        names = None
        if singers_json:
            try:
                parsed = _json.loads(singers_json)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, list) and parsed:
                names = parsed
        if names is None:
            names = [entry["singer"]]
        keys = [n.strip().lower() for n in names]
        sang = [last_sang[k] for k in keys if k in last_sang]
        entry["last_sang_minutes"] = max(sang) if sang else None
        # Per singer: minutes since they last sang, else since they first
        # entered. Group wait = the longest (most under-served member).
        waits = []
        for k in keys:
            if k in last_sang:
                waits.append(last_sang[k])
            elif k in first_entered:
                waits.append(first_entered[k])
        entry["wait_minutes"] = max(waits) if waits else None


def _add_last_sang_to_singer_stats(singer_stats, rotation):
    """Add last_sang_minutes field to each singer stat entry.

    Mirrors the last_sang half of _add_wait_pills() for rotation entries but
    operates on the consolidated per-singer dicts returned by get_singer_stats().
    """
    times = rotation.store.get_last_sang_times()
    for singer in singer_stats:
        key = singer["name"].strip().lower()
        mins = times.get(key)
        singer["last_sang_minutes"] = mins


def _singer_action_response(rotation):
    """Build standard response for singer action routes."""
    entries = rotation.get_rotation()
    _decorate_rotation_entries(entries, rotation)
    singer_stats = rotation.get_singer_stats()
    _add_last_sang_to_singer_stats(singer_stats, rotation)
    return jsonify({"success": True, "entries": entries, "singer_stats": singer_stats})


def _parse_singer_fields(data):
    """Parse singer/singers/song_artist fields from a request body.

    Accepts either ``singer`` (legacy single string) or ``singers`` (list of
    strings). When ``singers`` is provided, a display ``singer`` string is
    derived as ``" & ".join(...)`` unless one was already supplied.

    Returns ``(singer, singers, song_artist, err)`` where ``err`` is ``None``
    on success or a ``(flask Response, status_code)`` tuple on validation
    failure. On success, ``singer`` is non-empty and ``singers`` is either
    ``None`` or a list of trimmed non-empty strings.
    """
    singers = data.get('singers')
    singer_raw = data.get('singer', '')
    singer = singer_raw.strip() if isinstance(singer_raw, str) else ''
    song_artist = data.get('song_artist', '').strip()

    if singers is not None:
        if not isinstance(singers, list) or not all(isinstance(s, str) for s in singers):
            return None, None, None, (jsonify({"error": "singers must be a list of strings"}), 400)
        singers = [s.strip() for s in singers if s.strip()] or None
        if singers and not singer:
            singer = " & ".join(singers)

    if not singer:
        return None, None, None, (jsonify({"error": "singer or singers is required"}), 400)

    return singer, singers, song_artist, None


def _resolve_or_create_rotation_entry_id(data, rotation, song_artist_fallback=None):
    """Return ``(entry_id, err)`` for routes that act on an existing entry or
    create a new one.

    If ``data['id']`` is provided, validates it as an int and returns it.
    Otherwise creates a new rotation entry using singer/singers/song_artist
    fields, with ``song_artist_fallback`` used when no ``song_artist`` was
    supplied. On validation failure, ``err`` is a ``(Response, status)`` tuple
    suitable for returning directly from a Flask route.
    """
    if data.get('id') is not None:
        try:
            return int(data['id']), None
        except (TypeError, ValueError):
            return None, (jsonify({"error": "id must be an integer"}), 400)

    if not data.get('singers') and not (isinstance(data.get('singer'), str) and data['singer'].strip()):
        return None, (jsonify({"error": "id or singer is required"}), 400)

    singer, singers, song_artist, err = _parse_singer_fields(data)
    if err:
        return None, err

    if not song_artist and song_artist_fallback:
        song_artist = song_artist_fallback

    entry = rotation.add_entry(singer, song_artist, singers=singers)
    return entry["id"], None


def _add_sms_status(entries, app=None):
    """Attach an ``sms`` block to each rotation entry.

    Shape per entry:
        sms = {
            "configured": bool,       # SMS feature is globally configured at all
            "available": bool,        # SMS is globally configured AND row has a phone
            "last_sent_at": str|null, # timestamp of most recent send
            "last_status": str|null,  # "sent"/"failed" at send time, then the
                                      # Telnyx DLR overwrites with "delivered"/
                                      # "delivery_failed" once the receipt lands
            "last_error": str|null,   # failure reason from the last send/DLR
        }

    The frontend shows the SMS button on every row when ``configured`` is
    true (greyed-out/disabled when ``available`` is false, so rotation rows
    stay the same width) and hides it entirely when ``configured`` is false.

    "Available" requires BOTH (a) Telnyx env vars set so /sms/send won't
    503, AND (b) linked_entry_id resolving to a sing_request with a
    non-empty phone. KJ-added rows have no link and thus no SMS button.
    The bulk lookup is one tiny SQL query so this is cheap even for long
    rotations.
    """
    app = app or current_app._get_current_object()
    sing_store = getattr(app, "sing_store", None)
    sms_store = getattr(app, "sms_store", None)
    sms_cfg = getattr(app, "sms_config", None) or {}
    sms_enabled = bool(sms_cfg.get("api_key") and sms_cfg.get("from_number"))

    if sing_store is None or sms_store is None or not sms_enabled:
        # Feature disabled: every row reports unavailable so the frontend
        # hides the button entirely (rather than offering a button that
        # would 503 on click).
        for e in entries:
            e["sms"] = {"configured": False, "available": False, "last_sent_at": None, "last_status": None}
        return

    entry_ids = [e["id"] for e in entries if e.get("id") is not None]
    if not entry_ids:
        return

    # Map rotation_entry_id → newest sing_request's phone for that entry.
    #
    # Multiple sing_requests can link to one rotation_entry_id (duplicate
    # approval, cross-event ID reuse after restore_from_sheet, etc.). The
    # SEND path (_resolve_sms_target) deterministically picks the NEWEST
    # via ORDER BY id DESC LIMIT 1; we mirror that here so the button's
    # visibility matches what /sms/send would actually do. Without ORDER BY,
    # sqlite returned rows in implementation-defined order and the button
    # flickered between polls on the 2026-05-28 show.
    #
    # Scope to the current night (created_at >= night_started_at): a New
    # Rotation resets rotation_entries' autoincrement, so a recycled entry id
    # would otherwise phantom-match a PRIOR night's request and attach the
    # wrong singer's phone. _resolve_sms_target applies the same guard so the
    # button's visibility matches what /sms/send would actually do.
    # Fails CLOSED if night_started is unset (created_at >= NULL → no rows);
    # ensure_night_started() guarantees it's present on boot.
    conn = sing_store._get_conn()
    night_started = sing_store.get_night_started_at()
    placeholders = ",".join("?" * len(entry_ids))
    phone_rows = conn.execute(
        f"""
        SELECT linked_entry_id, phone, id
        FROM sing_requests
        WHERE linked_entry_id IN ({placeholders})
          AND created_at >= ?
        ORDER BY id DESC
        """,
        tuple(list(entry_ids) + [night_started]),
    ).fetchall()
    phone_by_entry = {}
    for row in phone_rows:
        eid = row["linked_entry_id"]
        if eid not in phone_by_entry:  # first occurrence wins → newest
            phone_by_entry[eid] = row["phone"] or ""

    latest_by_entry = sms_store.get_latest_for_entries(entry_ids)

    for entry in entries:
        eid = entry.get("id")
        phone = phone_by_entry.get(eid, "")
        latest = latest_by_entry.get(eid)
        entry["sms"] = {
            "configured": True,
            "available": bool(phone.strip()),
            "last_sent_at": latest["sent_at"] if latest else None,
            "last_status": latest["status"] if latest else None,
            # Surfaced so the row's ✗ marker tooltip can explain WHY a send
            # bounced (e.g. "40010 Not 10DLC registered", opted-out, carrier
            # reject). Only meaningful for failure states; None otherwise.
            "last_error": latest["error"] if latest else None,
        }


def _add_time_estimates(entries):
    """Add estimated_time field to each entry based on cumulative durations."""
    from datetime import datetime, timedelta
    default_duration = 240  # 4 minutes
    now = datetime.now()
    cumulative = 0
    for entry in entries:
        if entry.get("status", "").lower() in ("now singing", "singing now"):
            entry["estimated_time"] = "Now"
            continue
        est = now + timedelta(seconds=cumulative)
        entry["estimated_time"] = est.strftime("%I:%M %p").lstrip("0").lower()
        cumulative += entry.get("duration") or default_duration


def _add_media_meta(entries):
    """Attach canonical ``media_meta = {artist, title}`` to linked entries.

    media_library first (covers downloads + touched SSD rows, incl. manual ✎
    edits), external catalog fallback (untouched SSD files). Display-only and
    best-effort — a store/catalog error just leaves the key absent.
    """
    ml = getattr(current_app, 'media_library', None)
    catalog = getattr(current_app, 'catalog', None)
    for e in entries:
        fp = e.get('file_path')
        if not fp:
            continue
        try:
            row = ml.get_by_path(fp) if ml else None
            if not row and catalog is not None:
                row = catalog.get_by_path(fp)
            if row and ((row.get('artist') or '').strip()
                        or (row.get('title') or '').strip()):
                e['media_meta'] = {'artist': row.get('artist') or '',
                                   'title': row.get('title') or ''}
        except Exception:
            continue


def _decorate_rotation_entries(entries, rotation):
    """Attach every frontend-facing computed field to rotation entries.

    Single source of truth for the three decorators so that EVERY endpoint
    returning ``entries`` decorates them identically. The frontend sets
    ``rotationData = data.entries`` from any rotation response (not just the
    ``/rotation`` poll) and re-renders, so an endpoint that forgot one of
    these made the corresponding UI element flicker.

    Regression that motivated this helper (2026-06-11): the mutation endpoints
    called ``_add_time_estimates`` + ``_add_songs_sung`` but skipped
    ``_add_sms_status``. After any rotation action (move/edit/status/…) the
    re-render saw entries with no ``sms`` block, so the whole SMS button column
    disappeared until the next 2s ``/rotation`` poll re-added it — making it
    unpredictable which button the KJ would click.
    """
    _add_time_estimates(entries)
    _add_songs_sung(entries, rotation)
    _add_wait_pills(entries, rotation)
    _add_sms_status(entries)
    _add_media_meta(entries)


@routes_bp.route('/rotation', methods=['GET'])
def get_rotation():
    """Returns the current singer rotation queue (non-done entries)."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    try:
        entries = rotation.get_rotation()
        _decorate_rotation_entries(entries, rotation)
        singer_stats = rotation.get_singer_stats()
        _add_last_sang_to_singer_stats(singer_stats, rotation)
        return jsonify({
            "entries": entries,
            "singer_stats": singer_stats,
            "rev": rotation.store.get_rev(),
            "history": rotation.history_status(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/status', methods=['POST'])
def update_rotation_status():
    """Update a rotation entry's status."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)

    # Batch path: apply several status changes as one undoable action (e.g. the
    # Play button sets current → Now Singing and next → Up Next in one step).
    if isinstance(data.get('updates'), list):
        try:
            pairs = []
            for u in data['updates']:
                pairs.append((int(u['id']), u.get('status', '')))
        except (TypeError, ValueError, KeyError):
            return jsonify({"error": "each update needs an integer id and status"}), 400
        try:
            rotation.update_statuses(pairs)
            entries = rotation.get_rotation()
            _decorate_rotation_entries(entries, rotation)
            return jsonify({"success": True, "entries": entries})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    raw_id = data.get('id')
    status = data.get('status', '')
    if raw_id is None:
        return jsonify({"error": "id is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "id must be an integer"}), 400
    if entry_id < 1:
        return jsonify({"error": "id must be >= 1"}), 400

    try:
        if status.lower() in ('now singing', 'singing now', 'singing'):
            rotation.mark_singing(entry_id)
        elif status.lower() in ('up next', 'next'):
            rotation.mark_up_next(entry_id)
        else:
            rotation.update_status(entry_id, status)
        entries = rotation.get_rotation()
        _decorate_rotation_entries(entries, rotation)
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/edit', methods=['POST'])
def edit_rotation_entry():
    """Edit a rotation entry's singer name and/or song."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    raw_id = data.get('id')
    if raw_id is None:
        return jsonify({"error": "id is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "id must be an integer"}), 400
    if entry_id < 1:
        return jsonify({"error": "id must be >= 1"}), 400

    singer = data.get('singer')
    song_artist = data.get('song_artist')
    singers = data.get('singers')
    if singer is not None:
        singer = singer.strip()
    if song_artist is not None:
        song_artist = song_artist.strip()
    if singers is not None:
        if not isinstance(singers, list) or not all(isinstance(s, str) for s in singers):
            return jsonify({"error": "singers must be a list of strings"}), 400

    try:
        rotation.update_entry(entry_id, singer=singer, song_artist=song_artist, singers=singers)
        entries = rotation.get_rotation()
        _decorate_rotation_entries(entries, rotation)
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/delete', methods=['POST'])
def delete_rotation_entry():
    """Delete a rotation entry entirely."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    raw_id = data.get('id')
    if raw_id is None:
        return jsonify({"error": "id is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "id must be an integer"}), 400
    if entry_id < 1:
        return jsonify({"error": "id must be >= 1"}), 400

    try:
        rotation.delete_entry(entry_id)
        entries = rotation.get_rotation()
        _decorate_rotation_entries(entries, rotation)
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/add', methods=['POST'])
def add_rotation_entry():
    """Add a new singer to the rotation."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    singer, singers, song_artist, err = _parse_singer_fields(data)
    if err:
        return err
    notes = data.get('notes', '').strip()
    file_path = data.get('file_path', '').strip() or None
    url_fallback = data.get('url_fallback', '').strip() or None

    try:
        entry = rotation.add_entry(singer, song_artist, notes, file_path=file_path, singers=singers)
        if url_fallback:
            rotation.set_url_fallback(entry["id"], url_fallback)
            entry = rotation.store.get_entry(entry["id"])
        entries = rotation.get_rotation()
        _decorate_rotation_entries(entries, rotation)
        return jsonify({"success": True, "entry": entry, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/move', methods=['POST'])
def move_rotation_entry():
    """Move a rotation entry to a new position."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    raw_id = data.get('id')
    raw_pos = data.get('new_position')
    if raw_id is None or raw_pos is None:
        return jsonify({"error": "id and new_position are required"}), 400
    try:
        entry_id = int(raw_id)
        new_position = int(raw_pos)
    except (TypeError, ValueError):
        return jsonify({"error": "id and new_position must be integers"}), 400
    if entry_id < 1 or new_position < 1:
        return jsonify({"error": "id and new_position must be >= 1"}), 400

    try:
        rotation.move_entry(entry_id, new_position)
        entries = rotation.get_rotation()
        _decorate_rotation_entries(entries, rotation)
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/archive', methods=['POST'])
def archive_rotation():
    """Archive all rotation entries and clear the rotation.

    Re-enables public requests in case they were paused during the previous
    event. The event token is intentionally NOT regenerated here — KJs print
    a QR for the night and expect it to keep working across archive cycles.
    Use the modal's "Regenerate token" or "Set custom token" controls when a
    fresh code is actually wanted.
    """
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503

    try:
        count = rotation.archive_rotation()
        entries = rotation.get_rotation()
        sing_store = getattr(current_app, 'sing_store', None)
        if sing_store is not None:
            sing_store.set_enabled(True)
        return jsonify({"success": True, "archived": count, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _playability_gate(file_path):
    """Fast inline playability check (integrity + sampled decode, no render).
    Returns the PlayabilityResult; verdict.overall_ok is False for truncated,
    audio-only, or undecodable files."""
    from playability import PlayabilityChecker
    checker = PlayabilityChecker(config=current_app.kj_config)
    return checker.check(file_path, renderers=(), depth="quick")


# ---------------------------------------------------------------------------
# Tier-2: async render verification
#
# The inline gate (tier-1) hard-blocks on integrity + sampled decode but skips
# the expensive render proof. Tier-2 runs that proof against the *active*
# renderer off the request path after a file is linked, and stamps a
# playability_warning on the entry if the live renderer can't actually render
# it — so the KJ sees a ⚠️ before hitting play. A single worker thread drains
# the queue, so at most one off-screen Xvfb render runs at a time.
# ---------------------------------------------------------------------------

_tier2_queue = queue.Queue()
_tier2_worker_started = False
_tier2_worker_lock = threading.Lock()


def _run_tier2_check(app, entry_id, file_path, checker=None):
    """Render-verify a just-linked file against the active renderer.

    Stamps a playability_warning when the active renderer can't render it,
    clears a stale one when it can. Best-effort — never raises (a tier-2
    failure must not affect the already-successful link)."""
    try:
        from playability import PlayabilityChecker, classify_kind
        if classify_kind(file_path) == "audio":
            return  # nothing to render-verify for pure audio
        renderer = getattr(app.vlc, "render_mode", "vlc")
        chk = checker or PlayabilityChecker(config=app.kj_config)
        res = chk.check(file_path, renderers=(renderer,), depth="deep")
        store = app.rotation.store
        if not res.verdict.get("overall_ok"):
            reasons = "; ".join(res.verdict.get("reasons") or ["not playable"])
            store.set_playability_warning(entry_id, reasons)
            log_message(
                f"Tier-2: entry {entry_id} can't render in {renderer} — {reasons}",
                app.kj_config,
            )
        else:
            store.set_playability_warning(entry_id, None)
    except Exception as e:  # pragma: no cover - defensive, best-effort
        try:
            log_message(f"Tier-2 render check error (entry {entry_id}): {e}",
                        app.kj_config)
        except Exception:
            pass


def _tier2_worker():
    while True:
        app, entry_id, file_path = _tier2_queue.get()
        try:
            _run_tier2_check(app, entry_id, file_path)
        finally:
            _tier2_queue.task_done()


def _enqueue_tier2(app, entry_id, file_path):
    """Queue a background tier-2 render check (skips pure-audio files).

    The app object is threaded through each queue item rather than captured at
    worker-thread start, so warnings always land in the right store even if
    multiple app instances exist. Best-effort: any failure here is swallowed so
    it can never break the link flow that just succeeded."""
    try:
        from playability import classify_kind
        if classify_kind(file_path) == "audio":
            return
        global _tier2_worker_started
        with _tier2_worker_lock:
            if not _tier2_worker_started:
                _tier2_worker_started = True
                threading.Thread(target=_tier2_worker, daemon=True).start()
        _tier2_queue.put((app, entry_id, file_path))
    except Exception:  # pragma: no cover - defensive
        pass


@routes_bp.route('/rotation/link', methods=['POST'])
def link_rotation_file():
    """Link a media file to a rotation entry — or create one and link it.

    Two-mode body: pass ``id`` to link an existing entry, OR
    ``singers``/``song_artist`` to create a new entry and link in one call.
    Mirrors /rotation/download-and-link and /rotation/make so the frontend
    can build a single body shape for any add-with-source flow.
    """
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    file_path = data.get('file_path')
    if not file_path:
        return jsonify({"error": "file_path is required"}), 400

    entry_id, err = _resolve_or_create_rotation_entry_id(data, rotation)
    if err:
        return err
    if entry_id < 1:
        return jsonify({"error": "id must be >= 1"}), 400

    try:
        gate = _playability_gate(file_path)
        if not gate.verdict.get("overall_ok"):
            reasons = "; ".join(gate.verdict.get("reasons") or ["file is not playable"])
            return jsonify({
                "error": f"Playability check failed — not linked: {reasons}",
                "verdict": gate.verdict,
            }), 422
        rotation.link_file(entry_id, file_path)
        try:
            if library_media.is_library_path(
                    file_path, getattr(current_app, 'kj_config', None)):
                # Materialize the identity row now so canonical display + the
                # note editor work before the first play (design D3.3).
                library_media.run_async(
                    library_media.ensure_library_row_for_app,
                    current_app._get_current_object(), file_path)
        except Exception:
            pass  # best-effort — the link above already succeeded
        # Tier-1 (gate above) confirmed integrity+decode; tier-2 now render-
        # verifies against the active renderer in the background and flags the
        # entry if the live renderer can't actually render it.
        _enqueue_tier2(current_app._get_current_object(), entry_id, file_path)
        entries = rotation.get_rotation()
        _decorate_rotation_entries(entries, rotation)
        entry = next((e for e in entries if e.get('id') == entry_id), None)
        return jsonify({"success": True, "entry": entry, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/unlink', methods=['POST'])
def unlink_rotation_file():
    """Remove a file link from a rotation entry."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    raw_id = data.get('id')
    if raw_id is None:
        return jsonify({"error": "id is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "id must be an integer"}), 400
    if entry_id < 1:
        return jsonify({"error": "id must be >= 1"}), 400

    try:
        rotation.unlink_file(entry_id)
        entries = rotation.get_rotation()
        _decorate_rotation_entries(entries, rotation)
        entry = next((e for e in entries if e.get('id') == entry_id), None)
        return jsonify({"success": True, "entry": entry, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# SMS notifications — manual "you're up" texts from the rotation row
# ---------------------------------------------------------------------------

def _resolve_sms_target(entry_id):
    """Look up the rotation entry + its linked sing_request + a normalized phone.

    Returns ``(payload_or_None, err_or_None)`` where err is a ready-to-return
    ``(jsonify, status)`` tuple. payload has keys: entry, sing_request,
    phone_e164, first_name, song, artist.
    """
    import sms as sms_mod

    rotation = current_app.rotation
    sing_store = getattr(current_app, "sing_store", None)
    sms_cfg = getattr(current_app, "sms_config", None) or {}

    if rotation is None or sing_store is None:
        return None, (jsonify({"error": "Rotation not configured"}), 503)
    if not sms_cfg.get("api_key") or not sms_cfg.get("from_number"):
        return None, (jsonify({"error": "SMS not configured"}), 503)

    entry = rotation.store.get_entry(entry_id)
    if entry is None:
        return None, (jsonify({"error": "entry not found"}), 404)

    # Find the most recent sing_request that produced this rotation entry.
    #
    # Scope to the current night (created_at >= night_started_at). A New
    # Rotation resets rotation_entries' autoincrement counter, so without this
    # guard a recycled entry id phantom-matches a PRIOR night's request and we
    # would text the WRONG singer. _add_sms_status applies the same guard.
    # Fails CLOSED if the marker is unset (created_at >= NULL → no rows);
    # ensure_night_started() guarantees it's present on boot.
    conn = sing_store._get_conn()
    night_started = sing_store.get_night_started_at()
    req_row = conn.execute(
        """
        SELECT id, singer_name, phone, song_artist, song_title
        FROM sing_requests
        WHERE linked_entry_id = ? AND created_at >= ?
        ORDER BY id DESC LIMIT 1
        """,
        (entry_id, night_started),
    ).fetchone()
    if req_row is None or not (req_row["phone"] or "").strip():
        return None, (
            jsonify({"error": "no phone on file for this entry"}), 400,
        )

    default_region = sing_store.get_sms_default_region() or "US"
    try:
        phone_e164 = sms_mod.normalize_phone(req_row["phone"], default_region=default_region)
    except sms_mod.PhoneNormalizationError as exc:
        return None, (jsonify({"error": f"invalid phone: {exc}"}), 400)

    # Split "Andrew B." → "Andrew" for the {first_name} variable.
    first_name = (req_row["singer_name"] or "").split()[0] if req_row["singer_name"] else ""

    # Prefer the sing_request's structured song/title. Fall back to the rotation
    # entry if the request didn't carry structured fields. Rotation song_artist is
    # "Artist - Title" (P3 flip).
    song = (req_row["song_title"] or "").strip()
    artist = (req_row["song_artist"] or "").strip()
    if (not song or not artist) and entry.get("song_artist"):
        parts = entry["song_artist"].split(" - ", 1)
        if len(parts) > 1:
            if not artist:
                artist = parts[0].strip()
            if not song:
                song = parts[1].strip()
        elif not song:
            # No separator — treat the whole string as the song title.
            song = parts[0].strip()

    return {
        "entry": entry,
        "sing_request": dict(req_row),
        "phone_e164": phone_e164,
        "first_name": first_name,
        "song": song,
        "artist": artist,
    }, None


@routes_bp.route('/rotation/sms/preview', methods=['POST'])
def sms_preview():
    """Render the current SMS template for a row so the preview panel can
    populate without re-implementing rendering in JS (single source of truth)."""
    import sms as sms_mod

    data = request.get_json(force=True, silent=True) or {}
    raw_id = data.get('entry_id')
    if raw_id is None:
        return jsonify({"error": "entry_id is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "entry_id must be an integer"}), 400

    target, err = _resolve_sms_target(entry_id)
    if err:
        return err

    sing_store = current_app.sing_store
    template = sing_store.get_sms_template() or sms_mod.DEFAULT_TEMPLATE
    body = sms_mod.render_template(
        template,
        {
            "first_name": target["first_name"],
            "song": target["song"],
            "artist": target["artist"],
        },
    )

    return jsonify({
        "phone_e164": target["phone_e164"],
        "first_name": target["first_name"],
        "song": target["song"],
        "artist": target["artist"],
        "body": body,
        "length": len(body),
        "segments": sms_mod.segment_count(body),
    })


@routes_bp.route('/rotation/sms/detail', methods=['POST'])
def sms_detail():
    """Return the last SMS send's full detail for a row so the KJ can open a
    modal with the exact message body + delivery info (status, error, Telnyx id).

    Read-only: keyed on rotation_entry_id, which is globally unique (sqlite
    sequence is never reset — see the cross-night id-reuse fix), so the newest
    log row for an id belongs to this entry and no other. 404 when nothing has
    been sent for the row yet.
    """
    data = request.get_json(force=True, silent=True) or {}
    raw_id = data.get('entry_id')
    if raw_id is None:
        return jsonify({"error": "entry_id is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "entry_id must be an integer"}), 400

    latest = current_app.sms_store.get_latest_for_entry(entry_id)
    if not latest:
        return jsonify({"error": "no SMS has been sent for this row"}), 404

    return jsonify({
        "body": latest.get("body"),
        "phone_e164": latest.get("phone_e164"),
        "sent_at": latest.get("sent_at"),
        "status": latest.get("status"),
        "error": latest.get("error"),
        "telnyx_message_id": latest.get("telnyx_message_id"),
        "kj_user_agent": latest.get("kj_user_agent"),
    })


@routes_bp.route('/rotation/sms/send', methods=['POST'])
def sms_send():
    """Send the SMS body the KJ approved in the preview panel.

    The body is sent verbatim (no server-side re-templating) so per-singer
    edits in the preview are preserved exactly. Every attempt — success or
    failure — is logged to sms_log for the audit trail.
    """
    import sms as sms_mod

    data = request.get_json(force=True, silent=True) or {}
    raw_id = data.get('entry_id')
    body = (data.get('body') or '').strip()

    if raw_id is None:
        return jsonify({"error": "entry_id is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "entry_id must be an integer"}), 400
    if not body:
        return jsonify({"error": "body is required"}), 400
    if len(body) > sms_mod.MAX_BODY_LEN:
        return jsonify({
            "error": f"body too long ({len(body)} > {sms_mod.MAX_BODY_LEN})"
        }), 400

    target, err = _resolve_sms_target(entry_id)
    if err:
        return err

    sms_cfg = current_app.sms_config
    sms_store = current_app.sms_store
    user_agent = request.headers.get("User-Agent", "")[:500]

    # Respect opt-outs recorded from inbound STOP webhooks. Telnyx would reject
    # the send carrier-side anyway, but blocking here keeps us honest and gives
    # the KJ a clear reason instead of a cryptic Telnyx error.
    if sms_store.is_opted_out(target["phone_e164"]):
        log_row = sms_store.record_send(
            rotation_entry_id=entry_id,
            sing_request_id=target["sing_request"]["id"],
            phone_e164=target["phone_e164"],
            body=body,
            status="failed",
            telnyx_message_id=None,
            error="recipient opted out (replied STOP)",
            kj_user_agent=user_agent,
        )
        return jsonify({
            "success": False,
            "error": "Recipient has opted out (replied STOP); not sent.",
            "sms_log_id": log_row["id"],
        }), 403

    try:
        message_id = sms_mod.send(
            api_key=sms_cfg["api_key"],
            from_number=sms_cfg["from_number"],
            to_e164=target["phone_e164"],
            body=body,
        )
    except sms_mod.TelnyxError as exc:
        log_row = sms_store.record_send(
            rotation_entry_id=entry_id,
            sing_request_id=target["sing_request"]["id"],
            phone_e164=target["phone_e164"],
            body=body,
            status="failed",
            telnyx_message_id=None,
            error=str(exc),
            kj_user_agent=user_agent,
        )
        return jsonify({
            "success": False,
            "error": str(exc),
            "sms_log_id": log_row["id"],
        }), 502

    log_row = sms_store.record_send(
        rotation_entry_id=entry_id,
        sing_request_id=target["sing_request"]["id"],
        phone_e164=target["phone_e164"],
        body=body,
        status="sent",
        telnyx_message_id=message_id,
        error=None,
        kj_user_agent=user_agent,
    )
    return jsonify({
        "success": True,
        "sms_log_id": log_row["id"],
        "sent_at": log_row["sent_at"],
        "telnyx_message_id": message_id,
    })


@routes_bp.route('/rotation/set-paid', methods=['POST'])
def set_rotation_paid():
    """Toggle paid priority flag on a rotation entry."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    raw_id = data.get('id')
    if raw_id is None:
        return jsonify({"error": "id is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "id must be an integer"}), 400
    if entry_id < 1:
        return jsonify({"error": "id must be >= 1"}), 400

    raw_paid = data.get('paid')
    if raw_paid is not None and not isinstance(raw_paid, bool):
        return jsonify({"error": "paid must be a boolean"}), 400
    paid = bool(raw_paid) if raw_paid is not None else False

    try:
        rotation.set_paid(entry_id, paid)
        entries = rotation.get_rotation()
        _decorate_rotation_entries(entries, rotation)
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/sync-status', methods=['GET'])
def rotation_sync_status():
    """Return the current sheet sync status."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503

    try:
        status = rotation.get_sync_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/restore', methods=['POST'])
def restore_rotation_from_sheet():
    """Restore rotation — from a snapshot (undo/redo) or from Google Sheet.

    If the request body contains an ``entries`` list, atomically replaces the
    rotation with that snapshot (undo/redo support).  Otherwise falls back to
    the legacy behaviour of restoring from Google Sheets (emergency recovery).
    """
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503

    data = request.get_json(force=True, silent=True)
    if data is not None and not isinstance(data, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    if data is not None:
        # JSON body present — snapshot restore path (undo/redo support)
        if 'entries' not in data:
            return jsonify({"error": "entries is required"}), 400
        entries = data['entries']
        if not isinstance(entries, list):
            return jsonify({"error": "entries must be a list"}), 400
        try:
            rotation.restore_entries(entries)
            updated = rotation.get_rotation()
            _decorate_rotation_entries(updated, rotation)
            return jsonify({"success": True, "entries": updated})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    else:
        # No JSON body — legacy sheet restore path
        try:
            count = rotation.restore_from_sheet()
            sheet_entries = rotation.get_rotation()
            _decorate_rotation_entries(sheet_entries, rotation)
            return jsonify({"success": True, "restored": count, "entries": sheet_entries})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


def _undo_or_redo(direction):
    """Shared handler for /rotation/undo and /rotation/redo.

    Two-phase to make undo non-destructive: without ``confirm`` it returns a
    preview diff and applies nothing; with ``confirm: true`` it applies the
    change. The history is server-side and shared across all KJ devices.
    """
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503

    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    confirm = bool(data.get('confirm'))

    preview_fn = rotation.preview_undo if direction == 'undo' else rotation.preview_redo
    apply_fn = rotation.undo if direction == 'undo' else rotation.redo

    try:
        if not confirm:
            preview = preview_fn()
            if not preview.get('ok'):
                return jsonify({"success": False, "reason": preview.get('reason', 'empty')})
            return jsonify({
                "preview": True,
                "direction": direction,
                "label": preview.get('label'),
                "diff": preview.get('diff'),
                "rev": rotation.store.get_rev(),
                "history": rotation.history_status(),
            })

        # Version guard: if the client previewed against an older revision, the
        # rotation changed underneath it (e.g. a singer self-submitted between
        # preview and confirm). Reject so the client can re-preview the *real*
        # diff instead of silently applying a stale one.
        expected_rev = data.get('expected_rev')
        if expected_rev is not None:
            try:
                expected_rev = int(expected_rev)
            except (TypeError, ValueError):
                return jsonify({"error": "expected_rev must be an integer"}), 400
            if expected_rev != rotation.store.get_rev():
                return jsonify({
                    "success": False,
                    "reason": "stale",
                    "rev": rotation.store.get_rev(),
                    "history": rotation.history_status(),
                })

        result = apply_fn()
        if not result.get('ok'):
            return jsonify({"success": False, "reason": result.get('reason', 'empty')})

        entries = rotation.get_rotation()
        _decorate_rotation_entries(entries, rotation)
        singer_stats = rotation.get_singer_stats()
        _add_last_sang_to_singer_stats(singer_stats, rotation)
        return jsonify({
            "success": True,
            "direction": direction,
            "label": result.get('label'),
            "entries": entries,
            "singer_stats": singer_stats,
            "history": rotation.history_status(),
            "rev": rotation.store.get_rev(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/undo', methods=['POST'])
def undo_rotation():
    """Preview (no body / no confirm) or apply (confirm:true) a rotation undo."""
    return _undo_or_redo('undo')


@routes_bp.route('/rotation/redo', methods=['POST'])
def redo_rotation():
    """Preview or apply a rotation redo."""
    return _undo_or_redo('redo')


@routes_bp.route('/rotation/singer/rename', methods=['POST'])
def rename_singer_route():
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    old_name = data.get('old_name', '').strip()
    new_name = data.get('new_name', '').strip()
    if not old_name or not new_name:
        return jsonify({"error": "old_name and new_name are required"}), 400
    try:
        rotation.rename_singer(old_name, new_name)
        return _singer_action_response(rotation)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/singer/merge', methods=['POST'])
def merge_singers_route():
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    source = data.get('source_name', '').strip()
    target = data.get('target_name', '').strip()
    if not source or not target:
        return jsonify({"error": "source_name and target_name are required"}), 400
    try:
        rotation.merge_singers(source, target)
        return _singer_action_response(rotation)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/singer/brb', methods=['POST'])
def singer_brb_route():
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    name = data.get('name', '').strip()
    brb = data.get('brb', True)
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        new_status = "On Hold (BRB)" if brb else "Waiting"
        rotation.set_singer_status(name, new_status)
        return _singer_action_response(rotation)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/singer/remove', methods=['POST'])
def remove_singer_route():
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    name = data.get('name', '').strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        rotation.set_singer_status(name, "Left")
        rotation.mark_singer_left(name)
        return _singer_action_response(rotation)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/singer/restore', methods=['POST'])
def restore_singer_route():
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    name = data.get('name', '').strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        rotation.set_singer_status(name, "Waiting")
        rotation.unmark_singer_left(name)
        return _singer_action_response(rotation)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/singer/split', methods=['POST'])
def split_singer_route():
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    source = data.get('source_name', '').strip()
    new_name = data.get('new_name', '').strip()
    entry_ids = data.get('entry_ids')
    if not source:
        return jsonify({"error": "source_name is required"}), 400
    if not new_name:
        return jsonify({"error": "new_name is required"}), 400
    if not isinstance(entry_ids, list) or not entry_ids:
        return jsonify({"error": "entry_ids must be a non-empty list"}), 400
    if source.lower() == new_name.lower():
        return jsonify({"error": "new_name must differ from source_name"}), 400
    try:
        rotation.split_singer(source, new_name, entry_ids)
        return _singer_action_response(rotation)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _divebar_row_is_better(candidate, incumbent):
    """True if mirror row ``candidate`` should win over ``incumbent`` for the
    same (song, brand): prefer an in-GCS file, then a smaller file."""
    if bool(candidate.get("in_gcs")) != bool(incumbent.get("in_gcs")):
        return bool(candidate.get("in_gcs"))
    cand_size = candidate.get("file_size") or float("inf")
    inc_size = incumbent.get("file_size") or float("inf")
    return cand_size < inc_size


def _surface_divebar_versions(local_results, kn_results, db_results, cfg):
    """Cross-reference Divebar GCS-mirror results against local + KN results.

    Matching is on ``(normalized song, canonical brand)`` — the canonical brand
    folds differing code granularity (KN ``FBK`` vs mirror ``FBK204``) and
    brand-name-only entries together, so same-brand mirror files are recognised
    even when the raw codes differ.

    Mutates KN tracks in place: when a mirror file matches a KN track, attaches
    it as ``track['divebar']`` so that row downloads from the mirror (GCS)
    instead of YouTube.

    Returns a list of *standalone* Divebar rows — mirror versions of a brand
    that neither a local file nor a KN track already represents — so the KJ can
    pick a GCS-mirror version directly. Collapsed to one row per (song, brand),
    preferring in-GCS files then smaller files. Rows are annotated with
    priority_rank/brand/class so the frontend sorts them into the right section.
    """
    cbm = version_priority.canonical_brand_for_match

    # (song_key, canonical_brand) already represented by a local file.
    local_keys = set()
    for r in local_results or []:
        brand = cbm(disc_id=r.get("disc_id"), filename=r.get("filename"))
        local_keys.add((_normalize_song_key(r.get("artist"), r.get("title")), brand))

    # (song_key, canonical_brand) -> KN track, for same-brand attach.
    kn_index = {}
    for song in kn_results or []:
        song_key = _normalize_song_key(song.get("artist"), song.get("title"))
        for track in song.get("tracks") or []:
            brand = cbm(brand_code=track.get("brand_code"),
                        brand_name=track.get("brand_name"))
            kn_index.setdefault((song_key, brand), track)

    standalone = {}  # (song_key, canonical_brand) -> best mirror row
    for db_song in db_results or []:
        s_artist = db_song.get("artist") or ""
        s_title = db_song.get("title") or ""
        song_key = _normalize_song_key(s_artist, s_title)
        for db_track in db_song.get("tracks") or []:
            file_id = db_track.get("file_id")
            if not file_id:
                continue
            raw_code = db_track.get("brand_code")
            brand_name = db_track.get("brand")
            brand = cbm(brand_code=raw_code, brand_name=brand_name)
            key = (song_key, brand)
            kn_track = kn_index.get(key)
            if kn_track is not None:
                # Same brand as a KN row -> upgrade that row to the GCS mirror,
                # preferring an in-GCS / smaller file if one is already attached.
                # Always do this even when we also hold the brand locally: the KN
                # row is independently selectable and should prefer GCS over
                # YouTube (the local file shows as its own, higher-ranked row).
                existing = kn_track.get("divebar")
                if not (existing or {}).get("file_id") or \
                        _divebar_row_is_better(db_track, existing):
                    kn_track["divebar"] = db_track
                continue
            if key in local_keys:
                # No KN row to upgrade and a local file already covers this
                # brand -> skip a redundant standalone mirror row.
                continue
            # resolve_brand short-circuits on an unrecognized specific code
            # (e.g. "FBK204") and never consults the brand name. When the brand
            # resolves to a *registered* family, annotate/rank/display by the
            # canonical code so the row lands in the right tier; otherwise keep
            # the raw code so it classifies (correctly) as unknown.
            registered, _ = version_priority.resolve_brand(brand_code=brand)
            display_code = brand if registered else raw_code
            row = {
                "source": "divebar",
                "file_id": file_id,
                "brand_code": display_code,
                "brand_name": brand_name,
                "artist": s_artist,
                "title": s_title,
                "format": db_track.get("format"),
                "file_size": db_track.get("file_size"),
                "in_gcs": bool(db_track.get("in_gcs")),
            }
            incumbent = standalone.get(key)
            if incumbent is None or _divebar_row_is_better(row, incumbent):
                standalone[key] = row

    rows = list(standalone.values())
    version_priority.annotate_versions(rows, cfg, shape="rotation_search_divebar")
    return rows


def unified_search(query, app, *, grouped=False):
    """Unified search helper: local catalog + Karaoke Nerds + Divebar cross-reference.

    Shared by /rotation/search (KJ-side) and /sing/search (singer-side) so
    the same result shape (including Divebar file_id cross-ref and in_library
    flags) reaches both consumers.

    When ``grouped=True`` (singer-facing flow only) the return shape changes to
    ``{"songs": [...group dicts...], "karaoke_nerds_timeout": bool}`` — see
    ``_group_search_results``. The admin-side flow keeps ``grouped=False`` so
    existing callers aren't disrupted.
    """
    cfg = app.kj_config
    local_results = []
    if app.catalog.is_available():
        local_results = app.catalog.search(query, limit=10)

    # Add duration from media index where available
    for result in local_results:
        media_entry = app.media.index.get(result.get("path"))
        if media_entry:
            result["duration"] = media_entry.get("duration")

    # Also search downloaded media files (not in external catalog). Needles and
    # haystack both go through the shared normalizer so they meet in one
    # canonical space (e.g. an "and" query matches a "&" filename, diacritics
    # fold, etc.) — same pipeline as the catalog FTS path.
    local_paths = {r.get("path") for r in local_results}
    query_terms_clean = _tokens(query)
    for path, entry in app.media.index.items():
        if path in local_paths:
            continue
        searchable = _normalize_text(entry.get("display_name") or entry.get("filename", ""))
        if query_terms_clean and all(term in searchable for term in query_terms_clean):
            from catalog import parse_karaoke_filename
            from naming import strip_media_id_token
            fname = entry.get("filename", "") or ""
            # Prefer the clean, curated media_library identity when present:
            # canonical-slug download filenames carry a trailing ` [media_id]`
            # token that a raw filename parse would leak into the title
            # (e.g. "Vienna [yt-I8wu3lLbB0k]"). The scan already resolved the
            # real artist/title into media_library, so use that; otherwise fall
            # back to a deterministic parse of the token-stripped filename.
            ml_row = (app.media_library.get_by_path(path)
                      if getattr(app, "media_library", None) else None)
            if ml_row and ((ml_row.get("artist") or "").strip()
                           or (ml_row.get("title") or "").strip()):
                disc_id = None
                artist = ml_row.get("artist") or ""
                title = ml_row.get("title") or ""
            else:
                disc_id, artist, title = parse_karaoke_filename(
                    strip_media_id_token(fname))
            local_results.append({
                "path": path,
                "filename": entry.get("filename"),
                "artist": artist,
                "title": title or strip_media_id_token(
                    os.path.splitext(fname)[0]),
                "disc_id": disc_id,
                "format": os.path.splitext(fname)[1].lstrip('.'),
                "duration": entry.get("duration"),
            })

    kn_results = []
    kn_timeout = False
    try:
        # Send the raw user query: Karaoke Nerds does its own server-side
        # matching, so do not pre-normalize it here.
        kn_results = karaoke_nerds.search(query, cfg)
    except Exception:
        kn_timeout = True

    # Divebar GCS-mirror search runs independently of Karaoke Nerds so mirror
    # versions surface even when KN is slow or returns nothing. Best-effort.
    db_results = []
    try:
        # Raw user query: Divebar does its own server-side matching.
        db_results = divebar.search(query, cfg, limit=100)
    except Exception:
        db_results = []

    # Mark KN tracks already present in the local library (singer-facing hint).
    for song in kn_results:
        for track in song.get("tracks", []):
            track["in_library"] = any(
                r.get("artist", "").lower() == song.get("artist", "").lower()
                and r.get("title", "").lower() == song.get("title", "").lower()
                for r in local_results
            )

    # Cross-reference mirror files against local + KN: attaches track['divebar']
    # for same-brand matches (so that row downloads from GCS, not YouTube) and
    # returns standalone mirror rows the KJ can pick directly.
    divebar_rows = []
    try:
        divebar_rows = _surface_divebar_versions(
            local_results, kn_results, db_results, cfg)
    except Exception:
        divebar_rows = []  # best-effort; never break search

    if grouped:
        # Defensive filter (Phase B §4): a KN track with neither a YouTube URL
        # nor a divebar mirror is unapprovable — strip it so the singer never
        # sees it, which also keeps per-group `versions` cleaner.
        filtered_kn = []
        for song in kn_results:
            good_tracks = [
                t for t in song.get("tracks") or []
                if (t.get("youtube_url") or "").strip()
                or (t.get("divebar") or {}).get("file_id")
            ]
            if good_tracks:
                filtered_kn.append({**song, "tracks": good_tracks})
        return {
            "songs": _group_search_results(local_results, filtered_kn),
            "karaoke_nerds_timeout": kn_timeout,
        }

    # Flat path: annotate local results and every KN track in-place so the
    # frontend can sort + render with section headers without duplicating
    # the brand registry.
    cfg = app.kj_config if hasattr(app, "kj_config") else {}
    version_priority.annotate_versions(
        local_results, cfg, shape="rotation_search_local")
    for song in kn_results:
        version_priority.annotate_versions(
            song.get("tracks") or [], cfg, shape="rotation_search_kn")

    # Home the unknown-brand local files (the old "Unknown" dumping ground) into
    # meaningful groups: 4TB-SSD library by folder, YTDownloads by trust.
    # Recognized-brand local files keep their Community/Commercial tier.
    community_brand_keys = local_grouping.collect_community_brand_keys(
        kn_results, divebar_rows)
    media_is_download = getattr(app.media, "is_in_download_folder", None)
    for r in local_results:
        if r.get("priority_class") == "unknown":
            path = r.get("path") or ""
            is_dl = (media_is_download(path) if media_is_download
                     else local_grouping.path_in_download_folder(path, cfg))
            r["group"] = local_grouping.classify_local_file(
                path, r.get("filename"), cfg,
                is_download=is_dl,
                known_community_brands=community_brand_keys)

    return {
        "local": local_results,
        "karaoke_nerds": kn_results,
        "divebar": divebar_rows,
        "karaoke_nerds_timeout": kn_timeout,
    }


@routes_bp.route('/rotation/search', methods=['GET'])
def rotation_search():
    """Unified search: local catalog + Karaoke Nerds + Divebar cross-reference."""
    query = request.args.get('q', '').strip()
    if len(query) < 3:
        return jsonify({"error": "Query must be at least 3 characters"}), 400

    result = unified_search(query, current_app._get_current_object())
    _enrich_search_stats(result)

    # Back-compat shape: legacy response omitted the timeout flag when False,
    # and placed it at the top level when True. Preserve that.
    response = {"local": result["local"], "karaoke_nerds": result["karaoke_nerds"],
                "divebar": result.get("divebar", [])}
    if result["karaoke_nerds_timeout"]:
        response["karaoke_nerds_timeout"] = True
    return jsonify(response)


@routes_bp.route('/rotation/download-and-link', methods=['POST'])
def download_and_link_rotation():
    """Queue a download and link it to a rotation entry."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503

    data = request.get_json(force=True)
    source = data.get('source', '').strip()
    if not source:
        return jsonify({"error": "source is required"}), 400

    # Validate source-specific fields BEFORE creating any rotation entry
    if source == "divebar":
        file_id = data.get('file_id', '').strip()
        artist = (data.get('artist') or '').strip()
        title = (data.get('title') or '').strip()
        brand_code = (data.get('brand_code') or '').strip()
        fmt = (data.get('format') or '').strip()
        if not file_id:
            return jsonify({"error": "file_id is required for divebar"}), 400
    elif source == "youtube":
        youtube_url = data.get('youtube_url', '').strip()
        filename = data.get('filename', '').strip()
        if not youtube_url:
            return jsonify({"error": "youtube_url is required for youtube"}), 400
    else:
        return jsonify({"error": f"Unknown source: {source}"}), 400

    # A rotation target (an existing id, or a new singer) is required up-front, so
    # we reject a malformed request — keeping the missing-target 400 ahead of any
    # network work — BEFORE resolving the download or creating an entry. An id may
    # arrive as an int or a string; treat a blank/whitespace string as absent.
    _id = data.get('id')
    has_id = _id is not None and (not isinstance(_id, str) or _id.strip())
    if not has_id and not data.get('singers') and not (
            isinstance(data.get('singer'), str) and data['singer'].strip()):
        return jsonify({"error": "id or singer is required"}), 400

    app = current_app._get_current_object()

    # Dedup-skip: if we already have this exact media on disk, link it directly to
    # the (new or existing) rotation entry instead of downloading again.
    if source == "divebar":
        prospective = _prospective_media_id("divebar", file_id=file_id, brand_code=brand_code)
    else:
        prospective = _prospective_media_id("youtube", youtube_url=youtube_url)
    existing = _existing_media_for(app, prospective)
    if existing:
        entry_id, err = _resolve_or_create_rotation_entry_id(data, rotation)
        if err:
            return err
        # Link the file first; only mark complete once it's actually attached,
        # so a link failure can't leave an entry "complete" with no playable file.
        rotation.link_file(entry_id, existing["file_path"])
        rotation.set_download_status(entry_id, source, "complete", None)
        entry = rotation.store.get_entry(entry_id)
        entries = rotation.get_rotation()
        _decorate_rotation_entries(entries, rotation)
        return jsonify({"success": True, "deduped": True,
                        "entry": entry, "entries": entries})

    # Resolve the divebar download spec (single file, or a paired loose-CDG)
    # BEFORE creating a rotation entry, so a failure (no audio sibling / URL error)
    # can never leave an orphan entry behind.
    divebar_spec = None
    if source == "divebar":
        cfg = current_app.kj_config
        divebar_spec, err = _resolve_divebar_spec(
            file_id, artist, title, brand_code, fmt, cfg)
        if err:
            msg, status = err
            return jsonify({"error": msg}), status

    # Check queue capacity
    with app._download_lock:
        active = [i for i in app.download_queue['items'] if i['status'] in ('queued', 'downloading')]
        if len(active) >= 5:
            return jsonify({"error": "Download queue is full (max 5)"}), 409

    # Get or create rotation entry (only after all validations + spec resolution pass)
    entry_id, err = _resolve_or_create_rotation_entry_id(data, rotation)
    if err:
        return err

    from uuid import uuid4
    download_id = str(uuid4())

    try:
        if source == "divebar":
            queue_item = {
                'id': download_id,
                'status': 'queued',
                'error': None,
                'rotation_entry_id': entry_id,
                **divebar_spec,
            }
        else:  # youtube (already validated above)
            queue_item = {
                'id': download_id,
                'url': youtube_url,
                'title': filename,
                'source': 'youtube',
                'status': 'queued',
                'error': None,
                'rotation_entry_id': entry_id,
            }

        # Update rotation entry with download tracking FIRST so the worker's
        # sync helpers see the correct download_id when they run. Capture the
        # snapshot here so the response doesn't race with the worker's own
        # transitions once we dispatch it.
        rotation.set_download_status(entry_id, source, "queued", download_id)
        entry = rotation.store.get_entry(entry_id)
        entries = rotation.get_rotation()

        # Add to queue and start worker (one lock acquisition).
        with app._download_lock:
            app.download_queue['items'].append(queue_item)
            if not app.download_queue.get('worker_running'):
                app.download_queue['worker_running'] = True
                t = threading.Thread(target=_download_worker, args=(app,), daemon=True)
                t.start()

        _decorate_rotation_entries(entries, rotation)
        return jsonify({"success": True, "entry": entry, "entries": entries})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/make', methods=['POST'])
def make_rotation_entry():
    """Create a gen job and link it to a rotation entry."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503

    gen_client = getattr(current_app, 'gen_client', None)
    if gen_client is None:
        return jsonify({"error": "Gen API not configured"}), 503

    data = request.get_json(force=True)
    artist = data.get('artist', '').strip()
    title = data.get('title', '').strip()
    if not artist or not title:
        return jsonify({"error": "artist and title are required"}), 400

    # Get or create rotation entry
    entry_id, err = _resolve_or_create_rotation_entry_id(
        data, rotation, song_artist_fallback=f"{artist} - {title}"
    )
    if err:
        return err

    try:
        from gen_client import map_gen_status
        result = gen_client.create_job(artist, title)
        job_id = result.get("job_id")
        if not job_id:
            return jsonify({"error": "Gen API did not return a job_id"}), 502

        api_status = result.get("status", "pending")
        gen_status = map_gen_status(api_status)
        rotation.set_gen_status(entry_id, job_id, gen_status)

        entry = rotation.store.get_entry(entry_id)
        entries = rotation.get_rotation()
        _decorate_rotation_entries(entries, rotation)
        return jsonify({"success": True, "entry": entry, "entries": entries, "job_id": job_id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/gen-status', methods=['GET'])
def rotation_gen_status():
    """Return status of all active gen jobs for the current rotation."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503

    try:
        active = rotation.store.get_active_gen_entries()
        gen_entries = [
            {
                "entry_id": e["id"],
                "gen_job_id": e["gen_job_id"],
                "gen_status": e["gen_status"],
                "singer": e["singer"],
                "song_artist": e["song_artist"],
            }
            for e in active
        ]
        return jsonify({"gen_entries": gen_entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- Browser Mode routes ---

@routes_bp.route('/browser-mode/enable', methods=['POST'])
def browser_mode_enable():
    """Enable browser mode: stop VLC, switch PipeWire to HDMI, launch Chromium."""
    blocked = _check_sleep_mode()
    if blocked:
        return blocked
    global _browser_mode
    cfg = current_app.kj_config
    vlc = current_app.vlc
    chromium = getattr(current_app, 'chromium', None)

    if chromium is None:
        return jsonify({"error": "Chromium manager not available"}), 503

    url = (request.json or {}).get('url', '').strip() or 'https://youtube.com'

    # Stop VLC playback (both karaoke and filler)
    if vlc.enabled:
        log_message("Browser mode: stopping VLC instances...", cfg)
        vlc.fade_out_filler()
        vlc.ensure_filler_stopped()
        vlc.ensure_karaoke_released()
        vlc.karaoke_active = False
        vlc.current_playing_path = None

    # Determine browser audio device: use independent setting, or fall back to VLC's
    browser_audio = cfg.get('browser_audio_device', 'same')
    if browser_audio == 'same':
        audio_device = vlc.audio_device if vlc.enabled else cfg.get('default_audio_device', 'hdmiout')
    else:
        audio_device = browser_audio  # Direct PipeWire profile string
    success = chromium.launch(url, audio_device=audio_device)
    if not success:
        return jsonify({"error": "Failed to launch Chromium"}), 500

    _browser_mode = True

    # Persist last-used URL
    save_config_value('browser_mode_url', url)

    log_message(f"Browser mode enabled — Chromium at {url}", cfg)
    return jsonify({"success": True, "url": url})


@routes_bp.route('/browser-mode/navigate', methods=['POST'])
def browser_mode_navigate():
    """Navigate existing Chromium to a new URL without restarting."""
    blocked = _check_sleep_mode()
    if blocked:
        return blocked
    cfg = current_app.kj_config
    chromium = getattr(current_app, 'chromium', None)

    if chromium is None:
        return jsonify({"error": "Chromium manager not available"}), 503

    if not _browser_mode or not chromium.is_running():
        return jsonify({"error": "Browser mode not active"}), 409

    url = (request.json or {}).get('url', '').strip()
    if not url:
        return jsonify({"error": "url is required"}), 400

    success = chromium.navigate(url)
    if not success:
        # Fall back to full relaunch
        browser_audio = cfg.get('browser_audio_device', 'same')
        vlc = current_app.vlc
        if browser_audio == 'same':
            audio_device = vlc.audio_device if vlc.enabled else cfg.get('default_audio_device', 'hdmiout')
        else:
            audio_device = browser_audio
        success = chromium.launch(url, audio_device=audio_device)
        if not success:
            return jsonify({"error": "Failed to navigate"}), 500

    save_config_value('browser_mode_url', url)
    return jsonify({"success": True, "url": url})


@routes_bp.route('/browser-mode/disable', methods=['POST'])
def browser_mode_disable():
    """Disable browser mode: kill Chromium, reset PipeWire, restart VLC."""
    global _browser_mode
    cfg = current_app.kj_config
    vlc = current_app.vlc
    chromium = getattr(current_app, 'chromium', None)

    if chromium is None:
        return jsonify({"error": "Chromium manager not available"}), 503

    # Kill Chromium (handles PipeWire reset internally)
    chromium.kill()
    _browser_mode = False

    # Restart VLC instances (skip if sleep mode is active)
    sleep_mgr = getattr(current_app, 'sleep_manager', None)
    if sleep_mgr and sleep_mgr.is_sleeping():
        log_message("Browser mode disabled — skipping VLC restart (sleep mode active).", cfg)
    elif vlc.enabled:
        log_message("Browser mode disabled — restarting VLC instances...", cfg)
        vlc.restart_instances()

    log_message("Browser mode disabled — back to VLC.", cfg)
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Public singer request form — admin side
# ---------------------------------------------------------------------------

def _format_song_text(artist, title):
    artist = (artist or "").strip()
    title = (title or "").strip()
    if artist and title:
        return f"{title} - {artist}"
    return title or artist


def _pick_version_from_kj_pick(req, index):
    """Translate a ``kj_pick`` request + picked index into concrete source_* fields.

    Returns ``(source_type, source_ref, source_meta_dict_or_None)`` ready to be
    written back into the request row via ``SingStore.update_request_source``.

    Raises ``ValueError`` for a missing / out-of-range index so the approval
    route can return a 400 instead of 500 — the admin UI should always pass a
    valid index, but a cURL caller could trip this.
    """
    meta_raw = req.get("source_meta")
    if not meta_raw:
        raise ValueError("request has no source_meta — not a kj_pick?")
    try:
        meta = meta_raw if isinstance(meta_raw, dict) else json.loads(meta_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"source_meta is not valid JSON: {exc}") from exc
    versions = meta.get("versions") or []
    if index is None:
        raise ValueError("version_index required for kj_pick requests")
    try:
        index = int(index)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"version_index must be an integer: {exc}") from exc
    if not (0 <= index < len(versions)):
        raise ValueError(
            f"version_index out of range (got {index}, have {len(versions)} versions)"
        )
    v = versions[index]
    src = v.get("source")
    if src == "local":
        local = v.get("local") or {}
        path = local.get("path")
        if not path:
            raise ValueError("picked local version has no path")
        return ("local", path, None)
    if src == "kn":
        kn = v.get("kn") or {}
        divebar_info = kn.get("divebar") or {}
        file_id = divebar_info.get("file_id")
        if file_id:
            return (
                "divebar",
                file_id,
                {
                    "brand_code": kn.get("brand_code"),
                    "disc_id": divebar_info.get("drive_path"),
                },
            )
        youtube_url = kn.get("youtube_url")
        if not youtube_url:
            raise ValueError("picked KN version has neither divebar nor youtube_url")
        return ("youtube", youtube_url, {"brand_code": kn.get("brand_code")})
    raise ValueError(f"unknown version source: {src!r}")


def approve_sing_request(app, req, skip_download=False):
    """Dispatch approval of a sing request; create/link a rotation entry.

    Called from the admin /rotation/requests/<id>/approve route and from the
    auto-approve path in sing_bp.submit. Returns the rotation entry_id.

    ``skip_download``: only meaningful for download-style sources
    (divebar/youtube/kn). When True, create the rotation entry without
    queuing a download — the KJ will use the rotation 🔗 link button to
    attach a proper file (used when a pasted YouTube URL turns out not to
    be a karaoke version, but the singer should still be added to rotation).
    """
    rotation = getattr(app, 'rotation', None)
    if rotation is None:
        raise RuntimeError("Rotation not configured")

    singer = req["singer_name"]
    song_text = _format_song_text(req.get("song_artist"), req.get("song_title"))
    source_type = req["source_type"]
    source_ref = req.get("source_ref")

    # Build the multi-singer list, primary first, when partners are attached.
    # Passing None keeps the existing solo-entry behaviour (no singers_json).
    partners = req.get("additional_singers") or []
    partner_names = [p["name"] for p in partners if p.get("name")]
    singers_list = ([singer] + partner_names) if partner_names else None

    if source_type == "local":
        entry = rotation.add_entry(
            singer, song_text, file_path=source_ref or None,
            singers=singers_list,
        )
        return entry["id"]

    if source_type in ("divebar", "youtube", "kn"):
        if skip_download:
            # Unlinked entry — KJ will use the rotation 🔗 link button to
            # attach the file once they've found a proper karaoke version.
            entry = rotation.add_entry(singer, song_text, singers=singers_list)
            return entry["id"]
        # Resolve the download URL FIRST so we don't leave an orphan
        # rotation entry behind when upstream validation fails.
        from uuid import uuid4
        download_id = str(uuid4())
        divebar_spec = None
        if source_type == "divebar":
            # source_meta carries brand_code when this came via kj_pick;
            # direct singer-divebar picks won't have it.
            meta_raw = req.get("source_meta")
            if isinstance(meta_raw, str):
                try:
                    meta = json.loads(meta_raw)
                except (TypeError, ValueError):
                    meta = {}
            elif isinstance(meta_raw, dict):
                meta = meta_raw
            else:
                meta = {}
            brand_code = meta.get("brand_code") or ""
            if not source_ref:
                raise RuntimeError("source_ref (Divebar file_id) required")
            # Dedup-skip: link an already-downloaded copy instead of re-fetching.
            existing = _existing_media_for(app, _prospective_media_id(
                "divebar", file_id=source_ref, brand_code=brand_code))
            if existing:
                entry = rotation.add_entry(singer, song_text, singers=singers_list)
                rotation.link_file(entry["id"], existing["file_path"])
                rotation.set_download_status(entry["id"], "divebar", "complete", None)
                return entry["id"]
            # Resolve the spec (pairing a loose CDG with its audio) before
            # creating the rotation entry, so an unusable version fails cleanly
            # without leaving an orphan entry behind.
            divebar_spec, err = _resolve_divebar_spec(
                source_ref, req.get("song_artist"), req.get("song_title"),
                brand_code, meta.get("format"), app.kj_config)
            if err:
                raise RuntimeError(err[0])
            queue_src = "divebar"
        else:
            # youtube / kn — source_ref is a YouTube URL
            if not source_ref:
                raise RuntimeError("source_ref (YouTube URL) required")
            queue_src = "youtube"
            queue_url = source_ref
            title = song_text or (req.get("song_title") or "")
            # Dedup-skip: link an already-downloaded copy instead of re-fetching.
            existing = _existing_media_for(app, _prospective_media_id(
                "youtube", youtube_url=source_ref))
            if existing:
                entry = rotation.add_entry(singer, song_text, singers=singers_list)
                rotation.link_file(entry["id"], existing["file_path"])
                rotation.set_download_status(entry["id"], "youtube", "complete", None)
                return entry["id"]

        entry = rotation.add_entry(singer, song_text, singers=singers_list)
        if divebar_spec is not None:
            queue_item = {
                "id": download_id,
                "status": "queued",
                "error": None,
                "rotation_entry_id": entry["id"],
                **divebar_spec,
            }
        else:
            queue_item = {
                "id": download_id,
                "url": queue_url,
                "title": title,
                "source": queue_src,
                "source_detail": None,
                "status": "queued",
                "error": None,
                "rotation_entry_id": entry["id"],
            }
        rotation.set_download_status(
            entry["id"], queue_src, "queued", download_id
        )
        with app._download_lock:
            app.download_queue["items"].append(queue_item)
            if not app.download_queue.get("worker_running"):
                app.download_queue["worker_running"] = True
                threading.Thread(
                    target=_download_worker, args=(app,), daemon=True
                ).start()
        return entry["id"]

    if source_type == "make":
        gen_client = getattr(app, "gen_client", None)
        if gen_client is None:
            raise RuntimeError("Gen API not configured")
        # Queue the singer immediately, then *try* to start generation. Approval
        # must always succeed (so the request leaves the pending list and can
        # never be double-submitted): when gen can't start the job — transient
        # no-results, expired distribution creds, etc. — we keep the rotation
        # entry but leave it UNLINKED with a "Being Made (!)" status so the KJ
        # can start generation later via the rotation row's make button. Gen's
        # search endpoint creates its job *before* the flaky search runs, so a
        # raise here used to leave the request stuck pending AND spawn a fresh
        # gen job on every re-click — this avoids both.
        from gen_client import map_gen_status
        entry = rotation.add_entry(singer, song_text, singers=singers_list)
        job_id = None
        try:
            result = gen_client.create_job(
                req.get("song_artist", ""), req.get("song_title", "")
            )
            job_id = result.get("job_id")
            if not job_id:
                raise RuntimeError("Gen API did not return a job_id")
        except Exception as exc:
            app.logger.warning(
                "Make-approval: gen job did not start for entry %s (%s) — "
                "leaving it unlinked as 'Being Made (!)': %s",
                entry["id"], song_text, exc,
            )
            try:
                rotation.update_status(entry["id"], "Being Made (!)")
            except Exception:
                app.logger.exception(
                    "Failed to set 'Being Made (!)' status on entry %s",
                    entry["id"],
                )
            return entry["id"]

        # Gen job created — link it. The job is real even if this local write
        # fails, so never treat a set_gen_status error as a make failure (that
        # would risk a duplicate job when the KJ re-makes the row).
        try:
            rotation.set_gen_status(
                entry["id"], job_id,
                map_gen_status(result.get("status", "pending")),
            )
        except Exception:
            app.logger.exception(
                "Make-approval: gen job %s created but linking it to entry %s "
                "failed", job_id, entry["id"],
            )
        return entry["id"]

    raise ValueError(f"Unknown source_type: {source_type}")


def _require_sing_store():
    store = getattr(current_app, "sing_store", None)
    if store is None:
        return None, (jsonify({"error": "Sing store not configured"}), 503)
    return store, None


@routes_bp.route('/rotation/requests', methods=['GET'])
def list_sing_requests():
    """List pending/approved/rejected sing requests."""
    store, err = _require_sing_store()
    if err:
        return err
    status = request.args.get('status') or None
    limit = request.args.get('limit', type=int)
    requests = store.list_requests(status=status, limit=limit)
    return jsonify({
        "requests": requests,
        "counts": store.count_by_status(),
    })


@routes_bp.route('/rotation/requests/config', methods=['GET'])
def get_sing_config():
    """Return current token, enabled / auto-approve flags, and URLs."""
    store, err = _require_sing_store()
    if err:
        return err
    cfg = current_app.kj_config
    token = store.ensure_token()
    from sing import get_event_url
    import sms as sms_mod
    sms_cfg = getattr(current_app, "sms_config", {}) or {}
    return jsonify({
        "token": token,
        "enabled": store.is_enabled(),
        "auto_approve": store.is_auto_approve(),
        "accept_make_requests": store.is_accepting_make_requests(),
        "simple_mode": store.is_simple_mode(),
        "public_url": get_event_url(cfg, token, scope="public"),
        "local_url": get_event_url(cfg, token, scope="local"),
        "pending_count": store.count_pending(),
        # SMS config — template defaults to in-code value if KJ hasn't set one.
        "sms_enabled": bool(sms_cfg.get("api_key") and sms_cfg.get("from_number")),
        "sms_template": store.get_sms_template() or sms_mod.DEFAULT_TEMPLATE,
        "sms_template_is_custom": store.get_sms_template() is not None,
        "sms_default_region": store.get_sms_default_region(),
        "sms_from_number": sms_cfg.get("from_number") or None,
    })


@routes_bp.route('/rotation/requests/config', methods=['POST'])
def update_sing_config():
    """Regenerate the token, toggle enabled / auto-approve."""
    store, err = _require_sing_store()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    changed = {}

    def _on_token_changed(new_token):
        """Side effects shared by regenerate and explicit set: overlay URLs
        and push-sub cleanup must follow the new token."""
        try:
            from sing import get_event_url, sync_event_url_overlays
            url = get_event_url(current_app.kj_config, new_token, scope="public")
            sync_event_url_overlays(current_app.overlay_manager, url)
        except Exception:
            pass
        try:
            store.cleanup_stale_push_subscriptions(current_token=new_token)
        except Exception:
            current_app.logger.exception("push subscription cleanup failed")

    if data.get("regenerate"):
        new_token = store.regenerate_token()
        changed["token"] = new_token
        _on_token_changed(new_token)
    elif "token" in data and data["token"] is not None:
        # Explicit set — the KJ pinned a memorable 4-digit code (e.g. 2121)
        # and wants the printed QR to keep working across archives. Validation
        # mirrors regenerate's invariant (4 digits) so /sing keeps working.
        try:
            new_token = store.set_token(str(data["token"]).strip())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        changed["token"] = new_token
        _on_token_changed(new_token)

    if "enabled" in data:
        store.set_enabled(bool(data["enabled"]))
        changed["enabled"] = bool(data["enabled"])

    if "auto_approve" in data:
        store.set_auto_approve(bool(data["auto_approve"]))
        changed["auto_approve"] = bool(data["auto_approve"])

    if "accept_make_requests" in data:
        store.set_accepting_make_requests(bool(data["accept_make_requests"]))
        changed["accept_make_requests"] = bool(data["accept_make_requests"])

    if "simple_mode" in data:
        store.set_simple_mode(bool(data["simple_mode"]))
        changed["simple_mode"] = bool(data["simple_mode"])

    # ``sms_template`` is included on the same POST so the modal can save
    # everything in one call. Passing ``None`` clears the override and the
    # in-code default takes over.
    if "sms_template" in data:
        try:
            store.set_sms_template(data["sms_template"])
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        changed["sms_template"] = data["sms_template"]

    if "sms_default_region" in data and data["sms_default_region"] is not None:
        try:
            store.set_sms_default_region(data["sms_default_region"])
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        changed["sms_default_region"] = data["sms_default_region"]

    return jsonify({"success": True, "changed": changed})


@routes_bp.route('/rotation/requests/qr.svg', methods=['GET'])
def sing_qr_svg():
    """Return an SVG QR code pointing at the event URL."""
    store, err = _require_sing_store()
    if err:
        return err
    scope = request.args.get('scope', 'public')
    if scope not in ('public', 'local'):
        return jsonify({"error": "scope must be 'public' or 'local'"}), 400
    token = store.ensure_token()
    from sing import get_event_url
    url = get_event_url(current_app.kj_config, token, scope=scope)
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
        img = qrcode.make(url, image_factory=SvgPathImage, box_size=10, border=2)
        from io import BytesIO
        buf = BytesIO()
        img.save(buf)
        return Response(buf.getvalue(), mimetype='image/svg+xml')
    except Exception as exc:
        return jsonify({"error": f"QR generation failed: {exc}"}), 500


@routes_bp.route('/rotation/requests/<int:req_id>/approve', methods=['POST'])
def approve_sing_request_route(req_id):
    store, err = _require_sing_store()
    if err:
        return err
    req = store.get_request(req_id)
    if req is None:
        return jsonify({"error": "Request not found"}), 404
    if req["status"] != "pending":
        return jsonify({"error": f"Request is already {req['status']}"}), 409

    body = request.get_json(silent=True) or {}
    skip_download = bool(body.get("skip_download"))

    # kj_pick: the singer deferred version selection — the admin's approval
    # body must carry version_index so we can rewrite the row to a concrete
    # source_type/ref before approve_sing_request sees it.
    if req["source_type"] == "kj_pick":
        version_index = body.get("version_index")
        if version_index is None:
            return jsonify({
                "error": "version_index required for kj_pick requests"
            }), 400
        try:
            src_type, src_ref, src_meta = _pick_version_from_kj_pick(
                req, version_index
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        req = store.update_request_source(req_id, src_type, src_ref, src_meta)

    try:
        entry_id = approve_sing_request(
            current_app._get_current_object(), req, skip_download=skip_download
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    store.mark_approved(req_id, linked_entry_id=entry_id)
    # Fire push notification for approval (non-critical — never block on failure)
    dispatcher = getattr(current_app.rotation, "push_dispatcher", None)
    if dispatcher is not None:
        try:
            dispatcher.notify_request_decision(req_id, "approved", req)
        except Exception:
            current_app.logger.exception("approve push notify failed")
    return jsonify({
        "success": True,
        "request": store.get_request(req_id),
        "entry_id": entry_id,
    })


@routes_bp.route('/rotation/requests/<int:req_id>/edit', methods=['POST'])
def edit_sing_request_route(req_id):
    store, err = _require_sing_store()
    if err:
        return err
    req = store.get_request(req_id)
    if req is None:
        return jsonify({"error": "Request not found"}), 404
    data = request.get_json(force=True, silent=True) or {}
    allowed = {k: data[k] for k in (
        "singer_name", "song_artist", "song_title",
        "source_type", "source_ref", "source_meta", "notes",
    ) if k in data}
    try:
        updated = store.update_request(req_id, **allowed)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"success": True, "request": updated})


@routes_bp.route('/rotation/requests/<int:req_id>/reject', methods=['POST'])
def reject_sing_request_route(req_id):
    store, err = _require_sing_store()
    if err:
        return err
    req = store.get_request(req_id)
    if req is None:
        return jsonify({"error": "Request not found"}), 404
    data = request.get_json(force=True, silent=True) or {}
    reason = (data.get("reason") or "").strip() or None
    store.mark_rejected(req_id, reason=reason)
    dispatcher = getattr(current_app.rotation, "push_dispatcher", None)
    if dispatcher is not None:
        try:
            dispatcher.notify_request_decision(req_id, "rejected", req)
        except Exception:
            current_app.logger.exception("reject push notify failed")
    return jsonify({"success": True, "request": store.get_request(req_id)})


# --- Browser preview playback -------------------------------------------
# Audition any supported file in the KJ's browser (small video render + audio),
# with seek, without ever touching the live primary player / device A/V output.
# See preview.py + docs/archive/2026-06-30-browser-preview-playback-design.md.

_PREVIEW_CHUNK = 262144


@routes_bp.route('/preview/resolve', methods=['POST'])
def preview_resolve():
    descriptor = request.get_json(silent=True) or {}
    if not isinstance(descriptor, dict):
        return jsonify({"mode": "unavailable", "reason": "Invalid request"}), 400
    result = current_app.preview.resolve(descriptor)
    _record_preview_stat(descriptor)
    return jsonify(result)


@routes_bp.route('/preview/close', methods=['POST'])
def preview_close():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False}), 400
    current_app.preview.close(payload.get('token'))
    return jsonify({"ok": True})


def _preview_full_response(path, size, mime):
    def gen():
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(_PREVIEW_CHUNK)
                if not chunk:
                    break
                yield chunk
    resp = Response(gen(), status=200, mimetype=mime)
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Content-Length"] = str(size)
    return resp


@routes_bp.route('/preview/stream/<token>', methods=['GET'])
def preview_stream(token):
    info = current_app.preview.token_info(token)
    if not info or info.get("kind") not in ("native_video", "native_audio"):
        return ("Not found", 404)
    path = info["path"]
    mime = info.get("mime", "application/octet-stream")
    try:
        size = os.path.getsize(path)
    except OSError:
        return ("Not found", 404)
    rng = parse_range(request.headers.get("Range"), size)
    if rng is None:
        if request.headers.get("Range"):
            resp = Response(status=416)
            resp.headers["Content-Range"] = f"bytes */{size}"
            return resp
        return _preview_full_response(path, size, mime)
    start, end = rng
    length = end - start + 1

    def gen():
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(_PREVIEW_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    resp = Response(gen(), status=206, mimetype=mime)
    resp.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Content-Length"] = str(length)
    return resp


@routes_bp.route('/preview/cdg/<token>/<part>', methods=['GET'])
def preview_cdg(token, part):
    p = current_app.preview.cdg_part_path(token, part)
    if not p or not os.path.exists(p):
        return ("Not found", 404)
    mime = "audio/mpeg" if part == "audio" else "application/octet-stream"
    return send_file(p, mimetype=mime, conditional=True)


@routes_bp.route('/preview/hls/<token>/<path:name>', methods=['GET'])
def preview_hls(token, name):
    p = current_app.preview.hls_path(token, name)
    if not p or not os.path.exists(p):
        return ("Not found", 404)
    mime = "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t"
    return send_file(p, mimetype=mime, conditional=True)


@routes_bp.route('/media/note', methods=['POST'])
def media_note():
    data = request.get_json(silent=True) or {}
    media_id = (data.get('media_id') or '').strip()
    if not media_id:
        return jsonify({"error": "media_id is required"}), 400
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"error": "stats unavailable"}), 503
    note = stats.upsert_note(
        media_id, data.get('note') or '', (data.get('label') or '').strip() or None,
        artist=data.get('artist'), title=data.get('title'))
    return jsonify({"note": note})


@routes_bp.route('/media/note-labels', methods=['GET'])
def media_note_labels():
    stats = getattr(current_app, 'stats', None)
    return jsonify({"labels": stats.distinct_labels() if stats else []})


@routes_bp.route('/stats/top-songs', methods=['GET'])
def stats_top_songs():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"songs": []})
    singer = request.args.get('singer') or None
    since = request.args.get('since') or None
    try:
        limit = max(1, min(int(request.args.get('limit', 10)), 100))
    except (TypeError, ValueError):
        limit = 10
    return jsonify({"songs": stats.top_songs(singer=singer, since=since, limit=limit)})


@routes_bp.route('/stats/singers', methods=['GET'])
def stats_singers():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"singers": []})
    since = request.args.get('since') or None
    try:
        limit = max(1, min(int(request.args.get('limit', 50)), 200))
    except (TypeError, ValueError):
        limit = 50
    return jsonify({"singers": stats.top_singers(since=since, limit=limit)})


@routes_bp.route('/stats/overview', methods=['GET'])
def stats_overview():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"overview": {}})
    since = request.args.get('since') or None
    return jsonify({"overview": stats.overview(since=since)})


@routes_bp.route('/stats/top-artists', methods=['GET'])
def stats_top_artists():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"artists": []})
    since = request.args.get('since') or None
    try:
        limit = max(1, min(int(request.args.get('limit', 25)), 100))
    except (TypeError, ValueError):
        limit = 25
    return jsonify({"artists": stats.top_artists(since=since, limit=limit)})


@routes_bp.route('/stats/artist-songs', methods=['GET'])
def stats_artist_songs():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"songs": []})
    artist = request.args.get('artist') or ''
    since = request.args.get('since') or None
    try:
        limit = max(1, min(int(request.args.get('limit', 100)), 200))
    except (TypeError, ValueError):
        limit = 100
    return jsonify({"songs": stats.artist_songs(artist, since=since, limit=limit)})


@routes_bp.route('/stats/singer-songs', methods=['GET'])
def stats_singer_songs():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"songs": []})
    singer = request.args.get('singer') or ''
    since = request.args.get('since') or None
    try:
        limit = max(1, min(int(request.args.get('limit', 100)), 200))
    except (TypeError, ValueError):
        limit = 100
    return jsonify({"songs": stats.singer_songs(singer, since=since, limit=limit)})


@routes_bp.route('/stats/singer-song-history', methods=['GET'])
def stats_singer_song_history():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"history": []})
    singer = request.args.get('singer') or ''
    song_key = request.args.get('song_key') or ''
    try:
        limit = max(1, min(int(request.args.get('limit', 200)), 500))
    except (TypeError, ValueError):
        limit = 200
    return jsonify({"history": stats.singer_song_history(singer, song_key, limit=limit)})


@routes_bp.route('/stats/song-history', methods=['GET'])
def stats_song_history():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"history": []})
    song_key = request.args.get('song_key') or ''
    since = request.args.get('since') or None
    try:
        limit = max(1, min(int(request.args.get('limit', 200)), 500))
    except (TypeError, ValueError):
        limit = 200
    return jsonify({"history": stats.song_history(song_key, since=since, limit=limit)})


@routes_bp.route('/stats/nights', methods=['GET'])
def stats_nights():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"nights": []})
    try:
        limit = max(1, min(int(request.args.get('limit', 20)), 100))
    except (TypeError, ValueError):
        limit = 20
    return jsonify({"nights": stats.busiest_nights(limit=limit)})


@routes_bp.route('/stats/night-setlist', methods=['GET'])
def stats_night_setlist():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"setlist": []})
    night_date = request.args.get('night_date') or ''
    try:
        limit = max(1, min(int(request.args.get('limit', 200)), 500))
    except (TypeError, ValueError):
        limit = 200
    return jsonify({"setlist": stats.night_setlist(night_date, limit=limit)})


@routes_bp.route('/stats/most-repeated', methods=['GET'])
def stats_most_repeated():
    stats = getattr(current_app, 'stats', None)
    if not stats:
        return jsonify({"repeated": []})
    since = request.args.get('since') or None
    try:
        limit = max(1, min(int(request.args.get('limit', 10)), 50))
    except (TypeError, ValueError):
        limit = 10
    return jsonify({"repeated": stats.most_repeated(since=since, limit=limit)})
