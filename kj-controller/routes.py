"""Flask Blueprint with all route handlers."""

import glob
import json
import os
import random
import re
import struct
import subprocess
import threading
import time
import unicodedata

from flask import Blueprint, Response, current_app, jsonify, render_template, request

import divebar
import karaoke_nerds
import youtube_health
import youtube_search
from catalog import LATIN_SPECIAL_MAP
from config import APP_DIR, RENDER_MODES, load_config, save_config_value
from playback import RendererSwitchRejected
from sleep_mode import SleepManager
from utils import log_message

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

_FEAT_RE = re.compile(
    r"\s*[\[(]?\s*(?:feat\.?|ft\.?|featuring)\s+[^\])]+[\])]?\s*",
    re.IGNORECASE,
)
_PAREN_RE = re.compile(r"\s*[\[(][^\])]*[\])]\s*")
# Apostrophes (straight + curly + backtick) strip to nothing so "Don't" and
# "Dont" collapse to the same key. Other punctuation becomes whitespace.
_APOS_RE = re.compile(r"[‘’ʼ'`]+")
_PUNCT_RE = re.compile(r"[^\w\s]+")
_WS_RE = re.compile(r"\s+")


def _normalize_song_key(artist, title):
    """Deterministic (artist, title) → group key for collapsing search results.

    Lowercases, strips feat./ft./featuring qualifiers, strips any bracketed
    qualifier (e.g. ``(Live)``, ``[Radio Edit]``), drops apostrophes, replaces
    remaining punctuation with whitespace, collapses whitespace. Returns a
    single string so callers can use it as a dict key.

    None-safe — callers pass parsed filename fields that may be missing.
    """
    def _norm(s):
        s = (s or "").lower()
        s = _FEAT_RE.sub(" ", s)
        s = _PAREN_RE.sub(" ", s)
        s = _APOS_RE.sub("", s)
        s = _PUNCT_RE.sub(" ", s)
        s = _WS_RE.sub(" ", s).strip()
        return s
    return f"{_norm(artist)}|||{_norm(title)}"


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
    return render_template('index.html', latin_special_map=LATIN_SPECIAL_MAP,
                           config=cfg,
                           kn_preferred_brands=cfg.get('kn_preferred_brands', []))


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
                file_path, title = app.media.download_from_url(
                    next_item['url'], filename=next_item.get('title'))
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
                        app.rotation.complete_download(download_id, file_path)
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
    if validated.lower().endswith('.zip'):
        zip_playback = current_app.zip_playback
        mp3_path = zip_playback.extract_and_get_mp3(validated)
        if not mp3_path:
            return jsonify({"error": "ZIP file does not contain a playable .mp3 file"}), 400
        actual_play_path = mp3_path

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
                             'overlay_manager': current_app.overlay_manager}).start()
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
        vlc.fadeout(duration_s=3.0)

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
                    }

    return jsonify({
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
    })


@routes_bp.route('/fix_audio', methods=['POST'])
def fix_audio():
    """Emergency recovery: restarts both VLC instances to fix audio device conflicts."""
    vlc = current_app.vlc
    cfg = current_app.kj_config
    log_message("Fix audio requested - restarting playback instances...", cfg)
    vlc.audio_error = False
    vlc.restart_instances()
    return jsonify({"success": True, "message": "Playback instances restarted."})


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
        return jsonify(overlay), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


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
    return jsonify(results)


@routes_bp.route('/karaoke-nerds/config', methods=['GET'])
def kn_get_config():
    """Returns Karaoke Nerds preferred brands config."""
    cfg = current_app.kj_config
    return jsonify({
        "preferred_brands": cfg.get('kn_preferred_brands', []),
    })


@routes_bp.route('/karaoke-nerds/config', methods=['POST'])
def kn_set_config():
    """Updates Karaoke Nerds preferred brands config."""
    data = request.get_json(silent=True) or {}
    preferred = data.get('preferred_brands')
    if preferred is None or not isinstance(preferred, list):
        return jsonify({"error": "preferred_brands must be a list"}), 400

    # Sanitize: uppercase, strip whitespace, remove empties
    preferred = [b.strip().upper() for b in preferred if b.strip()]

    current_app.kj_config['kn_preferred_brands'] = preferred
    save_config_value('kn_preferred_brands', preferred)
    log_message(f"Updated KN preferred brands: {preferred}", current_app.kj_config)
    return jsonify({"preferred_brands": preferred})


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


@routes_bp.route('/divebar/download', methods=['POST'])
def divebar_download():
    """Download a Divebar track by file_id. Queues it like a YouTube download."""
    data = request.get_json(silent=True) or {}
    file_id = data.get('file_id', '').strip()
    filename = data.get('filename', '').strip()
    if not file_id:
        return jsonify({"error": "file_id is required"}), 400

    cfg = current_app.kj_config
    url = divebar.get_download_url(file_id, config=cfg)
    if not url:
        return jsonify({"error": "Could not get download URL"}), 500

    # Reuse the existing download queue with the Drive URL
    app = current_app._get_current_object()
    from uuid import uuid4
    with app._download_lock:
        items = app.download_queue['items']
        active = [i for i in items if i['status'] in ('queued', 'downloading')]
        if len(active) >= 5:
            return jsonify({"error": "Queue is full (max 5)"}), 409

        item = {
            'id': str(uuid4()),
            'url': url,
            'status': 'queued',
            'title': filename or f"Divebar track {file_id[:8]}",
            'error': None,
            'file_path': None,
            'added_at': time.time(),
            'completed_at': None,
            'source': 'divebar',
            'divebar_file_id': file_id,
        }
        items.append(item)
        log_message(f"Queued Divebar download: {filename or file_id}", cfg)

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
    """Temporarily sets VLC's audio output device without persisting to config.

    Accepts any valid ALSA device string (hw:X,Y format) or named device
    from the audio_devices config. Restarts VLC with the new device.
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

    if device == vlc.audio_device:
        return jsonify({'success': True, 'message': 'Already using that device.'})

    log_message(f"AV: switching VLC audio to '{device}' (temporary)...", cfg)
    vlc.audio_device = device
    threading.Thread(target=vlc.restart_instances).start()
    return jsonify({'success': True, 'message': f'Switching VLC to {device}. Restarting...'})


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


def _singer_action_response(rotation):
    """Build standard response for singer action routes."""
    entries = rotation.get_rotation()
    _add_time_estimates(entries)
    _add_songs_sung(entries, rotation)
    singer_stats = rotation.get_singer_stats()
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


@routes_bp.route('/rotation', methods=['GET'])
def get_rotation():
    """Returns the current singer rotation queue (non-done entries)."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    try:
        entries = rotation.get_rotation()
        _add_time_estimates(entries)
        _add_songs_sung(entries, rotation)
        singer_stats = rotation.get_singer_stats()
        return jsonify({"entries": entries, "singer_stats": singer_stats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/status', methods=['POST'])
def update_rotation_status():
    """Update a rotation entry's status."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
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
        _add_time_estimates(entries)
        _add_songs_sung(entries, rotation)
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
        _add_time_estimates(entries)
        _add_songs_sung(entries, rotation)
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
        _add_time_estimates(entries)
        _add_songs_sung(entries, rotation)
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
        _add_time_estimates(entries)
        _add_songs_sung(entries, rotation)
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
        _add_time_estimates(entries)
        _add_songs_sung(entries, rotation)
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/archive', methods=['POST'])
def archive_rotation():
    """Archive all rotation entries and clear the rotation.

    Also regenerates the event token for the public request form so a
    new night gets a new QR — and re-enables requests in case they were
    paused during the previous event.
    """
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503

    try:
        count = rotation.archive_rotation()
        entries = rotation.get_rotation()
        # Regen the sing token for the new event so yesterday's QR stops working.
        sing_store = getattr(current_app, 'sing_store', None)
        if sing_store is not None:
            new_token = sing_store.regenerate_token()
            sing_store.set_enabled(True)
            # Push new URL to any qr_code overlays linked to the event URL.
            try:
                from sing import get_event_url, sync_event_url_overlays
                url = get_event_url(current_app.kj_config, new_token, scope="public")
                sync_event_url_overlays(current_app.overlay_manager, url)
            except Exception:
                pass  # Best-effort — the main archive must still succeed.
        return jsonify({"success": True, "archived": count, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/link', methods=['POST'])
def link_rotation_file():
    """Link a media file to a rotation entry."""
    rotation = current_app.rotation
    if not hasattr(current_app, 'rotation') or current_app.rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    raw_id = data.get('id')
    file_path = data.get('file_path')
    if raw_id is None:
        return jsonify({"error": "id is required"}), 400
    if not file_path:
        return jsonify({"error": "file_path is required"}), 400
    try:
        entry_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "id must be an integer"}), 400
    if entry_id < 1:
        return jsonify({"error": "id must be >= 1"}), 400

    try:
        rotation.link_file(entry_id, file_path)
        entries = rotation.get_rotation()
        _add_time_estimates(entries)
        _add_songs_sung(entries, rotation)
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
        _add_time_estimates(entries)
        _add_songs_sung(entries, rotation)
        entry = next((e for e in entries if e.get('id') == entry_id), None)
        return jsonify({"success": True, "entry": entry, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        _add_time_estimates(entries)
        _add_songs_sung(entries, rotation)
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
            _add_time_estimates(updated)
            _add_songs_sung(updated, rotation)
            return jsonify({"success": True, "entries": updated})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    else:
        # No JSON body — legacy sheet restore path
        try:
            count = rotation.restore_from_sheet()
            sheet_entries = rotation.get_rotation()
            _add_time_estimates(sheet_entries)
            _add_songs_sung(sheet_entries, rotation)
            return jsonify({"success": True, "restored": count, "entries": sheet_entries})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


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

    # Also search downloaded media files (not in external catalog)
    local_paths = {r.get("path") for r in local_results}
    query_lower = query.lower()
    query_terms = query_lower.split()
    import re as _re
    _strip_punct = lambda s: _re.sub(r'[^\w\s]', '', s)
    query_terms_clean = [_strip_punct(t) for t in query_terms]
    for path, entry in app.media.index.items():
        if path in local_paths:
            continue
        searchable = (entry.get("display_name") or entry.get("filename", "")).lower()
        searchable_clean = _strip_punct(searchable)
        if all(term in searchable or term in searchable_clean
               for term in query_terms_clean):
            from catalog import parse_karaoke_filename
            disc_id, artist, title = parse_karaoke_filename(entry.get("filename", ""))
            local_results.append({
                "path": path,
                "filename": entry.get("filename"),
                "artist": artist,
                "title": title or entry.get("display_name", ""),
                "disc_id": disc_id,
                "format": os.path.splitext(entry.get("filename", ""))[1].lstrip('.'),
                "duration": entry.get("duration"),
            })

    kn_results = []
    kn_timeout = False
    try:
        kn_results = karaoke_nerds.search(query, cfg)
    except Exception:
        kn_timeout = True

    if kn_results and not kn_timeout:
        try:
            db_results = divebar.search(query, cfg, limit=100)
            db_index = {}
            for db_song in db_results:
                for db_track in db_song.get("tracks", []):
                    key = (
                        (db_song.get("artist") or "").lower().strip(),
                        (db_song.get("title") or "").lower().strip(),
                        (db_track.get("brand_code") or "").upper().strip(),
                    )
                    db_index[key] = db_track
            for song in kn_results:
                artist_lower = (song.get("artist") or "").lower().strip()
                title_lower = (song.get("title") or "").lower().strip()
                for track in song.get("tracks", []):
                    brand = (track.get("brand_code") or "").upper().strip()
                    db_match = db_index.get((artist_lower, title_lower, brand))
                    if db_match and db_match.get("file_id"):
                        track["divebar"] = db_match
        except Exception:
            pass  # Divebar cross-ref is best-effort

        for song in kn_results:
            for track in song.get("tracks", []):
                track["in_library"] = any(
                    r.get("artist", "").lower() == song.get("artist", "").lower()
                    and r.get("title", "").lower() == song.get("title", "").lower()
                    for r in local_results
                )

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

    return {
        "local": local_results,
        "karaoke_nerds": kn_results,
        "karaoke_nerds_timeout": kn_timeout,
    }


@routes_bp.route('/rotation/search', methods=['GET'])
def rotation_search():
    """Unified search: local catalog + Karaoke Nerds + Divebar cross-reference."""
    query = request.args.get('q', '').strip()
    if len(query) < 3:
        return jsonify({"error": "Query must be at least 3 characters"}), 400

    result = unified_search(query, current_app._get_current_object())

    # Back-compat shape: legacy response omitted the timeout flag when False,
    # and placed it at the top level when True. Preserve that.
    response = {"local": result["local"], "karaoke_nerds": result["karaoke_nerds"]}
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
        filename = data.get('filename', '').strip()
        if not file_id:
            return jsonify({"error": "file_id is required for divebar"}), 400
    elif source == "youtube":
        youtube_url = data.get('youtube_url', '').strip()
        filename = data.get('filename', '').strip()
        if not youtube_url:
            return jsonify({"error": "youtube_url is required for youtube"}), 400
    else:
        return jsonify({"error": f"Unknown source: {source}"}), 400

    # Check queue capacity
    app = current_app._get_current_object()
    with app._download_lock:
        active = [i for i in app.download_queue['items'] if i['status'] in ('queued', 'downloading')]
        if len(active) >= 5:
            return jsonify({"error": "Download queue is full (max 5)"}), 409

    # Get or create rotation entry (only after all validations pass)
    entry_id, err = _resolve_or_create_rotation_entry_id(data, rotation)
    if err:
        return err

    from uuid import uuid4
    download_id = str(uuid4())

    try:
        cfg = current_app.kj_config

        if source == "divebar":
            download_url = divebar.get_download_url(file_id, cfg)
            if not download_url:
                return jsonify({"error": "Failed to get download URL from Divebar"}), 502
            queue_item = {
                'id': download_id,
                'url': download_url,
                'title': filename or f"divebar-{file_id}.mp4",
                'source': 'divebar',
                'status': 'queued',
                'error': None,
                'rotation_entry_id': entry_id,
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

        _add_time_estimates(entries)
        _add_songs_sung(entries, rotation)
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
        data, rotation, song_artist_fallback=f"{title} - {artist}"
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
        _add_time_estimates(entries)
        _add_songs_sung(entries, rotation)
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


def approve_sing_request(app, req):
    """Dispatch approval of a sing request; create/link a rotation entry.

    Called from the admin /rotation/requests/<id>/approve route and from the
    auto-approve path in sing_bp.submit. Returns the rotation entry_id.
    """
    rotation = getattr(app, 'rotation', None)
    if rotation is None:
        raise RuntimeError("Rotation not configured")

    singer = req["singer_name"]
    song_text = _format_song_text(req.get("song_artist"), req.get("song_title"))
    source_type = req["source_type"]
    source_ref = req.get("source_ref")

    if source_type == "local":
        entry = rotation.add_entry(
            singer, song_text, file_path=source_ref or None
        )
        return entry["id"]

    if source_type in ("divebar", "youtube", "kn"):
        # Resolve the download URL FIRST so we don't leave an orphan
        # rotation entry behind when upstream validation fails.
        from uuid import uuid4
        download_id = str(uuid4())
        if source_type == "divebar":
            try:
                download_url = divebar.get_download_url(source_ref, app.kj_config)
            except Exception as exc:
                raise RuntimeError(f"Divebar URL failed: {exc}") from exc
            if not download_url:
                raise RuntimeError("Failed to get download URL from Divebar")
            title = f"divebar-{source_ref}.mp4"
            queue_src = "divebar"
            queue_url = download_url
        else:
            # youtube / kn — source_ref is a YouTube URL
            if not source_ref:
                raise RuntimeError("source_ref (YouTube URL) required")
            queue_src = "youtube"
            queue_url = source_ref
            title = song_text or (req.get("song_title") or "")

        entry = rotation.add_entry(singer, song_text)
        queue_item = {
            "id": download_id,
            "url": queue_url,
            "title": title,
            "source": queue_src,
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
        entry = rotation.add_entry(singer, song_text)
        result = gen_client.create_job(
            req.get("song_artist", ""), req.get("song_title", "")
        )
        job_id = result.get("job_id")
        if not job_id:
            raise RuntimeError("Gen API did not return a job_id")
        from gen_client import map_gen_status
        rotation.set_gen_status(
            entry["id"], job_id, map_gen_status(result.get("status", "pending"))
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
    return jsonify({
        "token": token,
        "enabled": store.is_enabled(),
        "auto_approve": store.is_auto_approve(),
        "accept_make_requests": store.is_accepting_make_requests(),
        "public_url": get_event_url(cfg, token, scope="public"),
        "local_url": get_event_url(cfg, token, scope="local"),
        "pending_count": store.count_pending(),
    })


@routes_bp.route('/rotation/requests/config', methods=['POST'])
def update_sing_config():
    """Regenerate the token, toggle enabled / auto-approve."""
    store, err = _require_sing_store()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}
    changed = {}

    if data.get("regenerate"):
        new_token = store.regenerate_token()
        changed["token"] = new_token
        # Also refresh any linked overlays.
        try:
            from sing import get_event_url, sync_event_url_overlays
            url = get_event_url(current_app.kj_config, new_token, scope="public")
            sync_event_url_overlays(current_app.overlay_manager, url)
        except Exception:
            pass
        # Garbage-collect push subs on old tokens (>7 days)
        try:
            store.cleanup_stale_push_subscriptions(current_token=new_token)
        except Exception:
            current_app.logger.exception("push subscription cleanup failed")

    if "enabled" in data:
        store.set_enabled(bool(data["enabled"]))
        changed["enabled"] = bool(data["enabled"])

    if "auto_approve" in data:
        store.set_auto_approve(bool(data["auto_approve"]))
        changed["auto_approve"] = bool(data["auto_approve"])

    if "accept_make_requests" in data:
        store.set_accepting_make_requests(bool(data["accept_make_requests"]))
        changed["accept_make_requests"] = bool(data["accept_make_requests"])

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

    # kj_pick: the singer deferred version selection — the admin's approval
    # body must carry version_index so we can rewrite the row to a concrete
    # source_type/ref before approve_sing_request sees it.
    if req["source_type"] == "kj_pick":
        body = request.get_json(silent=True) or {}
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
        entry_id = approve_sing_request(current_app._get_current_object(), req)
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
