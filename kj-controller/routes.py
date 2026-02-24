"""Flask Blueprint with all route handlers."""

import os
import random
import re
import subprocess
import threading
import time

from flask import Blueprint, current_app, jsonify, render_template, request

import karaoke_nerds
import youtube_search
from catalog import LATIN_SPECIAL_MAP
from config import load_config, save_config_value
from utils import log_message

routes_bp = Blueprint('routes', __name__)


@routes_bp.route('/')
def index():
    """Serves the main remote control page."""
    cfg = current_app.kj_config
    return render_template('index.html', latin_special_map=LATIN_SPECIAL_MAP,
                           config=cfg,
                           kn_preferred_brands=cfg.get('kn_preferred_brands', []))


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
        save_config_value('default_audio_device', 'hdmiout')

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
    """Sets the display resolution via xrandr and persists to config."""
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

    save_config_value('display_resolution', resolution)
    cfg['display_resolution'] = resolution
    return jsonify({'success': True, 'message': f'Resolution set to {resolution}'})


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
