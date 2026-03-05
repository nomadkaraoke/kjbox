"""Flask Blueprint with all route handlers."""

import glob
import os
import random
import re
import struct
import subprocess
import threading
import time

from flask import Blueprint, current_app, jsonify, render_template, request

import karaoke_nerds
import youtube_health
import youtube_search
from catalog import LATIN_SPECIAL_MAP
from config import APP_DIR, load_config, save_config_value
from utils import log_message

routes_bp = Blueprint('routes', __name__)

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

        try:
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
    return jsonify({"success": True})


@routes_bp.route('/download/ack', methods=['POST'])
def ack_download():
    """Dismiss completed/errored items. With id: specific item. Without: all finished."""
    app = current_app._get_current_object()
    item_id = request.json.get('id') if request.is_json else None

    with app._download_lock:
        items = app.download_queue['items']
        if item_id:
            item = next((i for i in items if i['id'] == item_id), None)
            if item and item['status'] in ('completed', 'error'):
                items.remove(item)
        else:
            app.download_queue['items'] = [
                i for i in items if i['status'] not in ('completed', 'error')
            ]
    return jsonify({"success": True})


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
    overlay_mgr = current_app.overlay_manager
    if action == 'pause_resume':
        vlc.send_command(karaoke_port, karaoke_pw, "pl_pause")
        time.sleep(0.5)
        status = vlc.send_command(karaoke_port, karaoke_pw, "")
        if status and status.get('state') == 'paused':
            vlc.karaoke_active = False
            overlay_mgr.set_karaoke_playing(False)
            # Don't start filler — paused karaoke still holds the ALSA device
        else:
            vlc.karaoke_active = True
            overlay_mgr.set_karaoke_playing(True)
    elif action == 'restart':
        vlc.send_command(karaoke_port, karaoke_pw, "seek&val=0")
    elif action == 'stop':
        vlc.ensure_karaoke_released()
        vlc.karaoke_active = False
        vlc.current_playing_path = None
        vlc.audio_error = False
        overlay_mgr.set_karaoke_playing(False)
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
    _debounced_save_volumes(vlc)
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

    dl_queue = current_app.download_queue['items']

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
            "download_queue": dl_queue,
            "karaoke_volume": vlc.karaoke_volume,
            "filler_volume": vlc.filler_volume,
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
        "download_queue": dl_queue,
        "karaoke_volume": vlc.karaoke_volume,
        "filler_volume": vlc.filler_volume,
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

    return jsonify({'video': video, 'audio': audio, 'health': health})


@routes_bp.route('/av/reset', methods=['POST'])
def av_reset():
    """Runs fix-hdmi-audio.sh to restore the full known-good AV state.

    Resets: ALSA hdmiout alias, IEC958 switches, PipeWire profile, display resolution.
    Then resets VLC audio device to 'hdmiout' and restarts VLC instances.
    No state is persisted to config.json.
    """
    cfg = current_app.kj_config
    vlc = current_app.vlc

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


# --- Rotation (Google Sheet) ---

@routes_bp.route('/rotation', methods=['GET'])
def get_rotation():
    """Returns the current singer rotation queue (non-done entries)."""
    rotation = current_app.rotation
    if rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    try:
        force = request.args.get('refresh') == '1'
        entries = rotation.get_rotation(force_refresh=force)
        return jsonify({"entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/status', methods=['POST'])
def update_rotation_status():
    """Update a rotation entry's status."""
    rotation = current_app.rotation
    if rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    raw_index = data.get('row_index')
    status = data.get('status', '')
    if raw_index is None:
        return jsonify({"error": "row_index is required"}), 400
    try:
        row_index = int(raw_index)
    except (TypeError, ValueError):
        return jsonify({"error": "row_index must be an integer"}), 400
    if row_index < 1:
        return jsonify({"error": "row_index must be >= 1"}), 400

    try:
        if status.lower() in ('singing now', 'singing'):
            rotation.mark_singing(row_index)
        else:
            rotation.update_status(row_index, status)
        entries = rotation.get_rotation(force_refresh=True)
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes_bp.route('/rotation/add', methods=['POST'])
def add_rotation_entry():
    """Add a new singer to the rotation."""
    rotation = current_app.rotation
    if rotation is None:
        return jsonify({"error": "Rotation not configured"}), 503
    data = request.get_json(force=True)
    singer = data.get('singer', '').strip()
    song_artist = data.get('song_artist', '').strip()
    if not singer:
        return jsonify({"error": "singer is required"}), 400

    try:
        rotation.add_entry(singer, song_artist)
        entries = rotation.get_rotation(force_refresh=True)
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
