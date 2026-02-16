"""Flask Blueprint with all route handlers."""

import os
import random
import threading
import time

from flask import Blueprint, current_app, jsonify, render_template, request

from config import load_config, save_config_value
from utils import log_message

routes_bp = Blueprint('routes', __name__)


@routes_bp.route('/')
def index():
    """Serves the main remote control page."""
    return render_template('index.html')


@routes_bp.route('/download', methods=['POST'])
def handle_download():
    """Handles the video download request."""
    url = request.json.get('url')
    if not url:
        return jsonify({"error": "URL is required"}), 400

    media = current_app.media
    cfg = current_app.kj_config
    log_message(f"Received download request for URL: {url}", cfg)
    file_path, title = media.download_video(url)

    if file_path:
        return jsonify({"success": True, "file_path": file_path, "title": title})
    else:
        return jsonify({"error": "Failed to download video"}), 500


@routes_bp.route('/play', methods=['POST'])
def handle_play():
    """Plays a media file by path (supports local media, external media, and ZIP files)."""
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
            real = os.path.realpath(file_path)
            real_mount = os.path.realpath(mount)
            if real.startswith(real_mount + os.sep) and os.path.exists(real):
                validated = real

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

    log_message(f"Received play request for {os.path.basename(validated)}.", cfg)
    vlc.current_playing_path = validated
    threading.Thread(target=vlc.play_video, args=(actual_play_path,)).start()
    return jsonify({"success": True, "message": "Playback initiated."})


@routes_bp.route('/seek', methods=['POST'])
def handle_seek():
    """Handles seeking within the karaoke video."""
    seek_time = request.json.get('time')
    if seek_time is None:
        return jsonify({"error": "Time is required"}), 400

    vlc = current_app.vlc
    cfg = current_app.kj_config
    karaoke_port = cfg.get('karaoke_vlc_port', 8080)
    karaoke_pw = cfg.get('karaoke_vlc_password', 'karaoke')
    log_message(f"Received seek request to time: {int(seek_time)}s", cfg)
    vlc.last_seek_time = time.time()
    vlc.send_command(karaoke_port, karaoke_pw, f"seek&val={int(seek_time)}")
    return jsonify({"success": True})


@routes_bp.route('/control', methods=['POST'])
def handle_control():
    """Handles playback controls like pause, resume, restart."""
    action = request.json.get('action')
    if not action:
        return jsonify({"error": "Action is required"}), 400

    vlc = current_app.vlc
    cfg = current_app.kj_config
    karaoke_port = cfg.get('karaoke_vlc_port', 8080)
    karaoke_pw = cfg.get('karaoke_vlc_password', 'karaoke')

    log_message(f"Received control action: {action}", cfg)
    if action == 'pause_resume':
        vlc.send_command(karaoke_port, karaoke_pw, "pl_pause")
        time.sleep(0.5)
        status = vlc.send_command(karaoke_port, karaoke_pw, "")
        if status and status.get('state') == 'paused':
            vlc.karaoke_active = False
            vlc.fade_in_filler()
        else:
            vlc.karaoke_active = True
            vlc.fade_out_filler()
    elif action == 'restart':
        vlc.send_command(karaoke_port, karaoke_pw, "seek&val=0")
    elif action == 'stop':
        vlc.send_command(karaoke_port, karaoke_pw, "pl_stop")
        vlc.send_command(karaoke_port, karaoke_pw, "pl_empty")
        vlc.karaoke_active = False
        vlc.current_playing_path = None
        vlc.audio_error = False
        vlc.fade_in_filler()

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
        port = cfg.get('karaoke_vlc_port', 8080)
        password = cfg.get('karaoke_vlc_password', 'karaoke')
        vlc.karaoke_volume = level
    elif target == 'filler':
        port = cfg.get('filler_vlc_port', 8081)
        password = cfg.get('filler_vlc_password', 'filler')
        vlc.filler_volume = level
    else:
        return jsonify({"error": "Invalid target"}), 400

    vlc.send_command(port, password, f"volume&val={level}")
    log_message(f"Set volume for '{target}' to {level}", cfg)
    return jsonify({"success": True})


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
    log_message(f"Changing filler music to: {track_name}", cfg)

    vlc.send_command(filler_port, filler_pw, "pl_stop")
    time.sleep(0.1)
    vlc.send_command(filler_port, filler_pw, "pl_empty")
    time.sleep(0.1)
    vlc.send_command(filler_port, filler_pw, f"in_enqueue&input={new_track_path}", is_path=True)
    time.sleep(0.1)
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
    vlc = current_app.vlc
    media = current_app.media
    cfg = current_app.kj_config
    karaoke_port = cfg.get('karaoke_vlc_port', 8080)
    karaoke_pw = cfg.get('karaoke_vlc_password', 'karaoke')

    status = vlc.send_command(karaoke_port, karaoke_pw, "")

    # Get display name for currently playing file
    current_playing = None
    cpp = vlc.current_playing_path
    if cpp and cpp in media.index:
        current_playing = media.index[cpp].get('display_name')
    elif cpp:
        current_playing = os.path.basename(cpp)

    if status:
        return jsonify({
            "state": status.get('state'),
            "current_playing": current_playing,
            "current_playing_path": cpp,
            "current_filler_track": vlc.current_filler_track,
            "time": status.get('time'),
            "length": status.get('length'),
            "audio_device": vlc.audio_device,
            "vlc_enabled": vlc.enabled,
            "audio_error": vlc.audio_error,
        })

    # VLC not running - return status without VLC data
    return jsonify({
        "state": "stopped",
        "current_playing": current_playing,
        "current_playing_path": cpp,
        "current_filler_track": vlc.current_filler_track,
        "time": 0,
        "length": 0,
        "audio_device": vlc.audio_device,
        "vlc_enabled": vlc.enabled,
    })


@routes_bp.route('/fix_audio', methods=['POST'])
def fix_audio():
    """Emergency recovery: restarts both VLC instances to fix audio device conflicts."""
    vlc = current_app.vlc
    cfg = current_app.kj_config
    log_message("Fix audio requested - restarting VLC instances...", cfg)
    vlc.audio_error = False
    vlc.restart_instances()
    return jsonify({"success": True, "message": "VLC instances restarted."})


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
    save_config_value('default_audio_device', device)
    threading.Thread(target=vlc.restart_instances).start()
    return jsonify({"success": True, "message": f"Switching to {available[device]}. VLC restarting..."})
