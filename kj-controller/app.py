import os
import sys
import json
import re
import subprocess
import threading
import time
import random
import requests
from flask import Flask, render_template, request, jsonify

# --- Constants ---
MEDIA_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.webm', '.mov', '.mp3', '.wav', '.flac', '.ogg'}
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
APP_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Global State ---
vlc_processes = {
    "karaoke": None,
    "filler": None
}
current_playing_path = None
current_filler_track = "wii.mp3"
media_index = {}
app_config = {}
filler_music_target_volume = 100
karaoke_music_target_volume = 200
karaoke_player_is_active = False
current_audio_device = "hdmiout"
vlc_enabled = False

# --- Environment Detection ---
def is_pi():
    """Detect if running on NomadPi (DietPi on Linux ARM)."""
    return os.path.exists('/boot/dietpi.txt')

# --- Config Loading ---
def load_config():
    """Loads config from config.json, falling back to platform-appropriate defaults."""
    defaults = {
        "download_folder": os.path.expanduser("~/kjdata/videos"),
        "media_folders": [os.path.expanduser("~/kjdata/videos")],
        "media_index_path": os.path.join(APP_DIR, 'media_index.json'),
        "filler_music_dir": os.path.expanduser("~/kjdata"),
        "log_file": os.path.expanduser("~/kj-controller.log"),
        "youtube_cookies_file": os.path.expanduser("~/kjdata/youtube_cookies.txt"),
        "karaoke_vlc_port": 8080,
        "filler_vlc_port": 8081,
        "karaoke_vlc_password": "karaoke",
        "filler_vlc_password": "filler",
        "audio_devices": {
            "hdmiout": "HDMI Output",
            "usbmixer": "USB Mixer",
        },
        "default_audio_device": "hdmiout",
        "flask_port": 5000,
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                user_config = json.load(f)
            defaults.update(user_config)
            log_message(f"Loaded config from {CONFIG_FILE}")
        except Exception as e:
            log_message(f"Error loading config file, using defaults: {e}")
    else:
        log_message(f"No config file at {CONFIG_FILE}, using defaults.")
    return defaults

# --- Logging ---
def log_message(message):
    """Appends a message to the log file and prints to stdout."""
    log_file = app_config.get('log_file') if app_config else None
    if log_file:
        try:
            with open(log_file, "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")
        except Exception:
            pass
    print(message)

# --- Utility Functions ---
def sanitize_filename_part(text):
    """Replace filesystem-unsafe chars and __ (our separator) with _. Truncate to 100 chars."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text)
    text = re.sub(r'__', '_', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text[:100]
    return text

def parse_youtube_filename(filename):
    """Parse youtube_id, channel, title from filename like {id}__{channel}__{title}.ext.
    Returns (youtube_id, channel, title) or None if not in this format."""
    stem = os.path.splitext(filename)[0]
    parts = stem.split('__', 2)
    if len(parts) == 3 and len(parts[0]) == 11:
        return (parts[0], parts[1], parts[2])
    return None

# --- Media Index Functions ---
def scan_media_folders():
    """Walk all configured media_folders, build index, persist to disk."""
    global media_index
    new_index = {}
    download_folder = os.path.realpath(app_config.get('download_folder', ''))

    # Load existing index to preserve download-time metadata (duration, upload_date)
    existing = load_media_index_file()

    for folder in app_config.get('media_folders', []):
        folder = os.path.realpath(folder)
        if not os.path.isdir(folder):
            log_message(f"Media folder not found, skipping: {folder}")
            continue
        for dirpath, _dirnames, filenames in os.walk(folder):
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in MEDIA_EXTENSIONS:
                    continue
                full_path = os.path.join(dirpath, fname)
                real_path = os.path.realpath(full_path)
                try:
                    stat = os.stat(real_path)
                except OSError:
                    continue

                is_download = real_path.startswith(download_folder + os.sep) or real_path == download_folder
                parsed = parse_youtube_filename(fname)

                entry = {
                    "path": real_path,
                    "filename": fname,
                    "folder": folder,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "is_download": is_download,
                }

                if parsed:
                    entry["youtube_id"] = parsed[0]
                    entry["channel"] = parsed[1]
                    entry["title"] = parsed[2]
                    entry["display_name"] = parsed[2]
                else:
                    entry["display_name"] = os.path.splitext(fname)[0]

                # Preserve duration and upload_date from existing index
                if real_path in existing:
                    for key in ('duration', 'upload_date', 'original_url'):
                        if key in existing[real_path]:
                            entry[key] = existing[real_path][key]

                new_index[real_path] = entry

    media_index = new_index
    save_media_index()
    log_message(f"Media scan complete: {len(media_index)} files indexed.")
    return media_index

def save_media_index():
    """Persist media_index to disk."""
    index_path = app_config.get('media_index_path', 'media_index.json')
    try:
        with open(index_path, 'w') as f:
            json.dump(media_index, f, indent=2)
    except Exception as e:
        log_message(f"Error saving media index: {e}")

def load_media_index_file():
    """Read media_index.json from disk, return dict."""
    index_path = app_config.get('media_index_path', 'media_index.json')
    if os.path.exists(index_path):
        try:
            with open(index_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            log_message(f"Error reading media index file: {e}")
    return {}

def load_media_index():
    """Load index into memory; if no file exists, do initial full scan."""
    global media_index
    loaded = load_media_index_file()
    if loaded:
        media_index = loaded
        log_message(f"Loaded media index with {len(media_index)} entries.")
    else:
        log_message("No media index found, performing initial scan...")
        scan_media_folders()

def validate_media_path(filepath):
    """Verify path resolves to within a configured media_folders entry. Returns real path or None."""
    real = os.path.realpath(filepath)
    for folder in app_config.get('media_folders', []):
        real_folder = os.path.realpath(folder)
        if real.startswith(real_folder + os.sep) or real == real_folder:
            if os.path.exists(real):
                return real
    return None

def is_in_download_folder(filepath):
    """Check if file is in the download folder (eligible for deletion)."""
    real = os.path.realpath(filepath)
    download_folder = os.path.realpath(app_config.get('download_folder', ''))
    return real.startswith(download_folder + os.sep)

# --- VLC Management ---
def launch_vlc_instance(name, port, password, media_file=None, loop=False):
    """Launches a VLC instance with the HTTP interface enabled."""
    if not vlc_enabled:
        return

    if vlc_processes[name] and vlc_processes[name].poll() is None:
        log_message(f"VLC instance '{name}' is already running.")
        return

    log_message(f"Launching VLC instance '{name}' on port {port} with audio device '{current_audio_device}'...")
    command = [
        'cvlc',
        '--extraintf', 'http',
        '--http-host', '0.0.0.0',
        '--http-port', str(port),
        '--http-password', password,
        '--no-video-title-show',
        '--aout', 'alsa',
        '--alsa-audio-device', current_audio_device,
    ]
    if name == 'karaoke':
        command.append('--fullscreen')

    if media_file:
        command.append(media_file)
    if loop:
        command.extend(['--loop'])

    # On Pi: VLC refuses to run as root; wrap with sudo -u dietpi and set display env
    if is_pi():
        wrapper = [
            'sudo', '-u', 'dietpi', 'env',
            'DISPLAY=:0',
            'XDG_RUNTIME_DIR=/run/user/1000',
        ]
        full_command = wrapper + command
    else:
        full_command = command

    try:
        process = subprocess.Popen(full_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        vlc_processes[name] = process
        log_message(f"VLC instance '{name}' launched with PID {process.pid}.")
        time.sleep(2)
    except FileNotFoundError:
        log_message(f"VLC not found - '{name}' instance not launched.")

def send_vlc_command(port, password, command, is_path=False, debug=False):
    """Sends a command to a VLC HTTP interface, with optional verbose logging."""
    if not vlc_enabled:
        return None

    if '&' in command and not is_path:
        url = f"http://localhost:{port}/requests/status.json?command={command}"
    else:
        parts = command.split('&input=', 1)
        cmd_part = parts[0]
        input_part = parts[1] if len(parts) > 1 else ''
        encoded_input = requests.utils.quote(input_part)
        url = f"http://localhost:{port}/requests/status.json?command={cmd_part}&input={encoded_input}"

    if debug:
        log_message(f"DEBUG: Sending VLC command to {url}")

    try:
        s = requests.Session()
        s.auth = ('', password)
        response = s.get(url, timeout=5)
        response_json = response.json()
        if debug:
            log_message(f"DEBUG: VLC response status: {response.status_code}")
            log_message(f"DEBUG: VLC response body: {response_json}")
        response.raise_for_status()
        return response_json
    except requests.exceptions.RequestException as e:
        if debug:
            log_message(f"Error sending command to VLC on port {port}: {e}")
        return None
    except Exception as e:
        log_message(f"An unexpected error occurred when calling VLC: {e}")
        return None

# --- Fading and Music Control ---
def fade_music(port, password, start_vol, end_vol, duration_s=3):
    """Gradually fades volume over a set duration."""
    steps = 20
    delay = duration_s / steps
    for i in range(steps + 1):
        volume = int(start_vol + (end_vol - start_vol) * (i / steps))
        send_vlc_command(port, password, f"volume&val={volume}")
        time.sleep(delay)

def fade_in_filler():
    """Fades in the filler music."""
    if not vlc_enabled:
        return
    log_message("Fading in filler music...")
    filler_port = app_config.get('filler_vlc_port', 8081)
    filler_pw = app_config.get('filler_vlc_password', 'filler')
    send_vlc_command(filler_port, filler_pw, "volume&val=0")
    send_vlc_command(filler_port, filler_pw, "pl_play")
    threading.Thread(target=fade_music, args=(filler_port, filler_pw, 0, filler_music_target_volume)).start()

def fade_out_filler():
    """Fades out the filler music and then pauses it."""
    if not vlc_enabled:
        return
    log_message("Fading out filler music...")
    filler_port = app_config.get('filler_vlc_port', 8081)
    filler_pw = app_config.get('filler_vlc_password', 'filler')
    def fade_and_pause():
        fade_music(filler_port, filler_pw, filler_music_target_volume, 0)
        send_vlc_command(filler_port, filler_pw, "pl_pause")
        log_message("Filler music faded out and paused.")
    threading.Thread(target=fade_and_pause).start()


# --- YouTube Downloader ---
def download_video(youtube_url):
    """Downloads a YouTube video with descriptive filename, updates media index."""
    import yt_dlp

    download_folder = app_config.get('download_folder', os.path.expanduser("~/kjdata/videos"))
    os.makedirs(download_folder, exist_ok=True)
    cookies_file = app_config.get('youtube_cookies_file', '')

    # Phase 1: Extract metadata without downloading
    extract_opts = {
        'quiet': True,
        'noplaylist': True,
    }
    if cookies_file and os.path.exists(cookies_file):
        extract_opts['cookiefile'] = cookies_file

    try:
        with yt_dlp.YoutubeDL(extract_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            title = info.get('title', 'Unknown Title')
            channel = info.get('channel', info.get('uploader', 'Unknown'))
            youtube_id = info.get('id', 'unknown')
            duration = info.get('duration')
            upload_date = info.get('upload_date')
    except Exception as e:
        log_message(f"Error extracting video info: {e}")
        return None, None

    # Phase 2: Build descriptive filename and download
    safe_channel = sanitize_filename_part(channel)
    safe_title = sanitize_filename_part(title)
    basename = f"{youtube_id}__{safe_channel}__{safe_title}"
    output_template = os.path.join(download_folder, basename)

    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': output_template,
        'merge_output_format': 'mp4',
        'force_overwrites': True,
        'quiet': True,
        'noplaylist': True,
        'writethumbnail': True,
    }
    if cookies_file and os.path.exists(cookies_file):
        ydl_opts['cookiefile'] = cookies_file
        log_message(f"Using YouTube cookies file: {cookies_file}")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(youtube_url, download=True)

        # Find the actual downloaded file (might be .mp4, .mkv, etc.)
        file_path = None
        for ext in ['.mp4', '.mkv', '.webm', '.avi', '.mov']:
            candidate = output_template + ext
            if os.path.exists(candidate):
                file_path = candidate
                break

        if not file_path:
            log_message(f"ERROR: Downloaded file not found for {basename}")
            return None, None

        real_path = os.path.realpath(file_path)

        # Add to media index
        stat = os.stat(real_path)
        entry = {
            "path": real_path,
            "filename": os.path.basename(real_path),
            "folder": os.path.realpath(download_folder),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "is_download": True,
            "youtube_id": youtube_id,
            "channel": safe_channel,
            "title": safe_title,
            "display_name": safe_title,
            "original_url": youtube_url,
        }
        if duration is not None:
            entry["duration"] = duration
        if upload_date is not None:
            entry["upload_date"] = upload_date

        media_index[real_path] = entry
        save_media_index()

        log_message(f"Successfully downloaded '{title}' as {os.path.basename(real_path)}")
        return real_path, title
    except Exception as e:
        log_message(f"Error downloading video: {e}")
        return None, None

# --- Video Playback ---
def play_video(file_path):
    """Plays a video on the karaoke VLC instance (fade out filler, load, play)."""
    global karaoke_player_is_active
    karaoke_port = app_config.get('karaoke_vlc_port', 8080)
    karaoke_pw = app_config.get('karaoke_vlc_password', 'karaoke')

    if not os.path.exists(file_path):
        log_message(f"ERROR: File not found: {file_path}")
        return

    if not vlc_enabled:
        log_message(f"VLC disabled - cannot play {os.path.basename(file_path)}")
        return

    # Fade out filler music
    fade_out_filler()
    time.sleep(3.5)

    # Load and play the video
    send_vlc_command(karaoke_port, karaoke_pw, "pl_empty")
    time.sleep(0.1)
    send_vlc_command(karaoke_port, karaoke_pw, f"in_enqueue&input={file_path}", is_path=True)
    time.sleep(0.1)
    send_vlc_command(karaoke_port, karaoke_pw, f"volume&val={karaoke_music_target_volume}")
    time.sleep(0.1)
    send_vlc_command(karaoke_port, karaoke_pw, "pl_play")

    karaoke_player_is_active = True
    log_message(f"Playback started for {os.path.basename(file_path)}.")

# --- Audio Device Switching ---
def restart_vlc_instances():
    """Terminates both VLC processes and relaunches them with the current audio device."""
    global karaoke_player_is_active, current_playing_path
    if not vlc_enabled:
        return

    karaoke_port = app_config.get('karaoke_vlc_port', 8080)
    karaoke_pw = app_config.get('karaoke_vlc_password', 'karaoke')
    filler_port = app_config.get('filler_vlc_port', 8081)
    filler_pw = app_config.get('filler_vlc_password', 'filler')

    log_message(f"Restarting VLC instances with audio device '{current_audio_device}'...")

    # Terminate existing VLC processes
    for name, proc in vlc_processes.items():
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log_message(f"Terminated VLC instance '{name}'.")
        vlc_processes[name] = None

    karaoke_player_is_active = False
    current_playing_path = None
    time.sleep(1)

    # Relaunch both instances
    launch_vlc_instance("karaoke", karaoke_port, karaoke_pw)
    filler_dir = app_config.get('filler_music_dir', '')
    filler_path = os.path.join(filler_dir, current_filler_track) if filler_dir else ''
    launch_vlc_instance("filler", filler_port, filler_pw, filler_path, True)
    time.sleep(3)
    fade_in_filler()
    log_message("VLC instances restarted successfully.")

# --- Flask Routes ---
@app.route('/')
def index():
    """Serves the main remote control page."""
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def handle_download():
    """Handles the video download request."""
    url = request.json.get('url')
    if not url:
        return jsonify({"error": "URL is required"}), 400

    log_message(f"Received download request for URL: {url}")
    file_path, title = download_video(url)

    if file_path:
        return jsonify({"success": True, "file_path": file_path, "title": title})
    else:
        return jsonify({"error": "Failed to download video"}), 500

@app.route('/play', methods=['POST'])
def handle_play():
    """Plays a media file by path."""
    global current_playing_path
    file_path = request.json.get('file_path')
    if not file_path:
        return jsonify({"error": "file_path is required"}), 400

    validated = validate_media_path(file_path)
    if not validated:
        return jsonify({"error": "Invalid or inaccessible file path"}), 400

    if not vlc_enabled:
        return jsonify({"error": "VLC not available (running in local/dev mode)"}), 503

    log_message(f"Received play request for {os.path.basename(validated)}.")
    current_playing_path = validated
    threading.Thread(target=play_video, args=(validated,)).start()
    return jsonify({"success": True, "message": "Playback initiated."})

@app.route('/seek', methods=['POST'])
def handle_seek():
    """Handles seeking within the karaoke video."""
    seek_time = request.json.get('time')
    if seek_time is None:
        return jsonify({"error": "Time is required"}), 400

    karaoke_port = app_config.get('karaoke_vlc_port', 8080)
    karaoke_pw = app_config.get('karaoke_vlc_password', 'karaoke')
    log_message(f"Received seek request to time: {seek_time}")
    send_vlc_command(karaoke_port, karaoke_pw, f"seek&val={seek_time}")
    return jsonify({"success": True})


@app.route('/control', methods=['POST'])
def handle_control():
    """Handles playback controls like pause, resume, restart."""
    action = request.json.get('action')
    if not action:
        return jsonify({"error": "Action is required"}), 400

    karaoke_port = app_config.get('karaoke_vlc_port', 8080)
    karaoke_pw = app_config.get('karaoke_vlc_password', 'karaoke')

    log_message(f"Received control action: {action}")
    global karaoke_player_is_active
    if action == 'pause_resume':
        send_vlc_command(karaoke_port, karaoke_pw, "pl_pause")
        time.sleep(0.5)
        status = send_vlc_command(karaoke_port, karaoke_pw, "")
        if status and status.get('state') == 'paused':
            karaoke_player_is_active = False
            fade_in_filler()
        else:
            karaoke_player_is_active = True
            fade_out_filler()
    elif action == 'restart':
        send_vlc_command(karaoke_port, karaoke_pw, "seek&val=0")
    elif action == 'stop':
        send_vlc_command(karaoke_port, karaoke_pw, "pl_stop")
        karaoke_player_is_active = False
        global current_playing_path
        current_playing_path = None
        fade_in_filler()

    return jsonify({"success": True, "message": f"Action '{action}' executed."})

@app.route('/volume', methods=['POST'])
def handle_volume():
    """Handles volume control for karaoke or filler music."""
    target = request.json.get('target')
    level = int(request.json.get('level'))
    if not all([target, level is not None]):
        return jsonify({"error": "Target and level are required"}), 400

    if target == 'karaoke':
        port = app_config.get('karaoke_vlc_port', 8080)
        password = app_config.get('karaoke_vlc_password', 'karaoke')
        global karaoke_music_target_volume
        karaoke_music_target_volume = level
    elif target == 'filler':
        port = app_config.get('filler_vlc_port', 8081)
        password = app_config.get('filler_vlc_password', 'filler')
        global filler_music_target_volume
        filler_music_target_volume = level
    else:
        return jsonify({"error": "Invalid target"}), 400

    send_vlc_command(port, password, f"volume&val={level}")
    log_message(f"Set volume for '{target}' to {level}")
    return jsonify({"success": True})

@app.route('/media')
def list_media():
    """Returns the media index with display info, grouped by folder."""
    items = []
    for path, entry in media_index.items():
        folder = entry.get('folder', '')
        folder_name = os.path.basename(folder) if folder else 'Unknown'
        item = {
            "file_path": entry["path"],
            "display_name": entry.get("display_name", entry.get("filename", "")),
            "filename": entry.get("filename", ""),
            "folder_name": folder_name,
            "folder": folder,
            "is_download": entry.get("is_download", False),
            "mtime": entry.get("mtime", 0),
            "size": entry.get("size", 0),
        }
        if "channel" in entry:
            item["channel"] = entry["channel"]
        if "youtube_id" in entry:
            item["youtube_id"] = entry["youtube_id"]
        if "duration" in entry:
            item["duration"] = entry["duration"]
        items.append(item)

    # Sort by mtime descending (newest first) within each folder
    items.sort(key=lambda x: x['mtime'], reverse=True)
    return jsonify(items)

@app.route('/delete', methods=['POST'])
def delete_media():
    """Deletes a media file (only from download folder)."""
    file_path = request.json.get('file_path')
    if not file_path:
        return jsonify({"error": "file_path is required"}), 400

    validated = validate_media_path(file_path)
    if not validated:
        return jsonify({"error": "Invalid file path"}), 400

    if not is_in_download_folder(validated):
        return jsonify({"error": "Can only delete files from the download folder"}), 403

    log_message(f"Received delete request for: {os.path.basename(validated)}")

    # Delete the main file
    try:
        os.remove(validated)
        log_message(f"Deleted file: {os.path.basename(validated)}")
    except Exception as e:
        log_message(f"Error deleting file: {e}")
        return jsonify({"error": f"Error deleting file: {e}"}), 500

    # Delete same-basename sidecar files (.json, .webp, .jpg, etc.)
    basename_no_ext = os.path.splitext(validated)[0]
    parent_dir = os.path.dirname(validated)
    for fname in os.listdir(parent_dir):
        full = os.path.join(parent_dir, fname)
        if full != validated and os.path.splitext(full)[0] == basename_no_ext:
            try:
                os.remove(full)
                log_message(f"Deleted sidecar: {fname}")
            except Exception as e:
                log_message(f"Error deleting sidecar {fname}: {e}")

    # Remove from index
    if validated in media_index:
        del media_index[validated]
        save_media_index()

    return jsonify({"success": True, "message": f"Deleted {os.path.basename(validated)}"})

@app.route('/rescan', methods=['POST'])
def handle_rescan():
    """Triggers a full media folder rescan."""
    log_message("Rescan requested...")
    scan_media_folders()
    return jsonify({"success": True, "count": len(media_index)})

@app.route('/filler_music', methods=['GET'])
def list_filler_music():
    """Returns a list of available filler music files."""
    filler_dir = app_config.get('filler_music_dir', '')
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

@app.route('/filler_music', methods=['POST'])
def set_filler_music():
    """Sets the filler music track and starts playing it at a random time."""
    global current_filler_track
    track_name = request.json.get('track_name')
    if not track_name:
        return jsonify({"error": "Track name is required"}), 400

    filler_dir = app_config.get('filler_music_dir', '')
    new_track_path = os.path.join(filler_dir, track_name)
    if not os.path.exists(new_track_path):
        return jsonify({"error": "Track not found"}), 404

    filler_port = app_config.get('filler_vlc_port', 8081)
    filler_pw = app_config.get('filler_vlc_password', 'filler')

    current_filler_track = track_name
    log_message(f"Changing filler music to: {track_name}")

    send_vlc_command(filler_port, filler_pw, "pl_stop")
    time.sleep(0.1)
    send_vlc_command(filler_port, filler_pw, "pl_empty")
    time.sleep(0.1)
    send_vlc_command(filler_port, filler_pw, f"in_enqueue&input={new_track_path}", is_path=True)
    time.sleep(0.1)
    send_vlc_command(filler_port, filler_pw, "pl_play")

    time.sleep(0.5)

    status = send_vlc_command(filler_port, filler_pw, "")
    if status and 'length' in status and status['length'] > 0:
        duration = status['length']
        random_time = random.randint(0, max(0, int(duration) - 5))
        log_message(f"Seeking filler music to {random_time}s (duration: {duration}s)")
        send_vlc_command(filler_port, filler_pw, f"seek&val={random_time}")

    send_vlc_command(filler_port, filler_pw, f"volume&val={filler_music_target_volume}")

    return jsonify({"success": True})

@app.route('/status')
def get_status():
    """Gets the status of the karaoke player."""
    karaoke_port = app_config.get('karaoke_vlc_port', 8080)
    karaoke_pw = app_config.get('karaoke_vlc_password', 'karaoke')
    filler_port = app_config.get('filler_vlc_port', 8081)
    filler_pw = app_config.get('filler_vlc_password', 'filler')

    status = send_vlc_command(karaoke_port, karaoke_pw, "")

    # Get display name for currently playing file
    current_playing = None
    if current_playing_path and current_playing_path in media_index:
        current_playing = media_index[current_playing_path].get('display_name')
    elif current_playing_path:
        current_playing = os.path.basename(current_playing_path)

    if status:
        filler_status = send_vlc_command(filler_port, filler_pw, "")

        return jsonify({
            "state": status.get('state'),
            "current_playing": current_playing,
            "current_playing_path": current_playing_path,
            "current_filler_track": current_filler_track,
            "time": status.get('time'),
            "length": status.get('length'),
            "audio_device": current_audio_device,
            "vlc_enabled": vlc_enabled,
        })

    # VLC not running - return status without VLC data
    return jsonify({
        "state": "stopped",
        "current_playing": current_playing,
        "current_playing_path": current_playing_path,
        "current_filler_track": current_filler_track,
        "time": 0,
        "length": 0,
        "audio_device": current_audio_device,
        "vlc_enabled": vlc_enabled,
    })

@app.route('/audio_device', methods=['GET'])
def get_audio_device():
    """Returns the current audio device and available devices."""
    return jsonify({
        "current": current_audio_device,
        "available": app_config.get('audio_devices', {}),
    })

@app.route('/audio_device', methods=['POST'])
def set_audio_device():
    """Switches the audio output device by restarting VLC instances."""
    global current_audio_device
    device = request.json.get('device')
    available = app_config.get('audio_devices', {})
    if not device:
        return jsonify({"error": "Device is required"}), 400
    if device not in available:
        return jsonify({"error": f"Unknown device '{device}'. Available: {list(available.keys())}"}), 400
    if device == current_audio_device:
        return jsonify({"success": True, "message": "Already using that device."})

    log_message(f"Switching audio device from '{current_audio_device}' to '{device}'...")
    current_audio_device = device
    threading.Thread(target=restart_vlc_instances).start()
    return jsonify({"success": True, "message": f"Switching to {available[device]}. VLC restarting..."})


# --- Main Execution ---
def monitor_karaoke_player():
    """A background thread to check if a song has ended."""
    global karaoke_player_is_active, current_playing_path
    karaoke_port = app_config.get('karaoke_vlc_port', 8080)
    karaoke_pw = app_config.get('karaoke_vlc_password', 'karaoke')
    while True:
        time.sleep(2)
        if not karaoke_player_is_active:
            continue

        status = send_vlc_command(karaoke_port, karaoke_pw, "", debug=False)
        if not status:
            continue

        if status.get('state') == 'stopped':
            log_message("Karaoke video finished playing.")
            karaoke_player_is_active = False
            current_playing_path = None
            fade_in_filler()

def start_app():
    """Initializes and starts the application components."""
    global app_config, vlc_enabled, current_audio_device
    log_message("--- KJ Controller Starting Up ---")

    # Load config
    app_config = load_config()
    current_audio_device = app_config.get('default_audio_device', 'hdmiout')
    os.makedirs(app_config['download_folder'], exist_ok=True)

    # Pi-specific setup
    if is_pi():
        log_message("Running on NomadPi - enabling VLC and X11 setup.")
        vlc_enabled = True

        # Grant X11 access to dietpi user and ensure runtime dir exists
        subprocess.run(['xhost', '+SI:localuser:dietpi'], env={**os.environ, 'DISPLAY': ':0'}, capture_output=True)
        os.makedirs('/run/user/1000', exist_ok=True)
        subprocess.run(['chown', 'dietpi:dietpi', '/run/user/1000'], capture_output=True)
    else:
        log_message("Running in local/dev mode - VLC disabled, web UI and media scanning only.")
        vlc_enabled = False

    # Load media index
    load_media_index()

    # Launch VLC instances (only on Pi)
    if vlc_enabled:
        karaoke_port = app_config.get('karaoke_vlc_port', 8080)
        karaoke_pw = app_config.get('karaoke_vlc_password', 'karaoke')
        filler_port = app_config.get('filler_vlc_port', 8081)
        filler_pw = app_config.get('filler_vlc_password', 'filler')

        launch_vlc_instance("karaoke", karaoke_port, karaoke_pw)
        filler_dir = app_config.get('filler_music_dir', '')
        filler_path = os.path.join(filler_dir, current_filler_track) if filler_dir else ''
        launch_vlc_instance("filler", filler_port, filler_pw, filler_path, True)

        time.sleep(3)
        fade_in_filler()

        # Start the karaoke player monitor in a background thread
        monitor_thread = threading.Thread(target=monitor_karaoke_player, daemon=True)
        monitor_thread.start()

    # Start Flask app
    flask_port = app_config.get('flask_port', 5000)
    log_message(f"Starting Flask server on port {flask_port}...")
    app.run(host='0.0.0.0', port=flask_port, threaded=True)

if __name__ == '__main__':
    start_app()
