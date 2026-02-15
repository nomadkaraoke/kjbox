import os
import subprocess
import threading
import time
import random
import requests
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO
import yt_dlp

# --- Configuration ---
KARAOKE_VLC_PORT = 8080
FILLER_VLC_PORT = 8081
KARAOKE_VLC_PASSWORD = "karaoke"
FILLER_VLC_PASSWORD = "filler"
VIDEO_DIR = os.path.expanduser("~/kjdata/videos")
FILLER_MUSIC_DIR = os.path.expanduser("~/kjdata")
LOG_FILE = os.path.expanduser("~/kj-controller.log")
YOUTUBE_COOKIES_FILE = os.path.expanduser("~/kjdata/youtube_cookies.txt")

# --- Flask App Initialization ---
app = Flask(__name__)
socketio = SocketIO(app, async_mode='threading')

# --- Global State ---
# This will hold the subprocess objects for our VLC instances
vlc_processes = {
    "karaoke": None,
    "filler": None
}
current_video_id = None
current_filler_track = "wii.mp3" # Default
downloaded_videos = {} # Cache for video titles
filler_music_target_volume = 100 # Default volume for filler music (0-256)
karaoke_music_target_volume = 200 # Default volume for karaoke video (0-256, 256 is 100%)
karaoke_player_is_active = False # Tracks if a karaoke song is supposed to be playing
sync_offset_ms = 0 # Manual sync offset in milliseconds
wait_for_external_enabled = False # If False, do not wait for external screen readiness

# --- Logging ---
def log_message(message):
    """Appends a message to the log file."""
    with open(LOG_FILE, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")
    print(message)

# --- VLC Management ---
def launch_vlc_instance(name, port, password, media_file=None, loop=False):
    """Launches a VLC instance with the HTTP interface enabled."""
    if vlc_processes[name] and vlc_processes[name].poll() is None:
        log_message(f"VLC instance '{name}' is already running.")
        return

    log_message(f"Launching VLC instance '{name}' on port {port}...")
    command = [
        'cvlc',
        '--extraintf', 'http',
        '--http-host', '0.0.0.0',
        '--http-port', str(port),
        '--http-password', password,
        '--no-video-title-show', # Hide title overlay
    ]
    # Make the karaoke player always start in fullscreen
    if name == 'karaoke':
        command.append('--fullscreen')
        
    if media_file:
        command.append(media_file)
    if loop:
        command.extend(['--loop'])

    # For Linux, ensure the display is set correctly
    env = os.environ.copy()
    env['DISPLAY'] = ':0'

    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    vlc_processes[name] = process
    log_message(f"VLC instance '{name}' launched with PID {process.pid}.")
    # Give VLC a moment to start up
    time.sleep(2)

def send_vlc_command(port, password, command, is_path=False, debug=False):
    """Sends a command to a VLC HTTP interface, with optional verbose logging."""
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
    log_message("Fading in filler music...")
    # Ensure it's playing, but at 0 volume first
    send_vlc_command(FILLER_VLC_PORT, FILLER_VLC_PASSWORD, "volume&val=0")
    send_vlc_command(FILLER_VLC_PORT, FILLER_VLC_PASSWORD, "pl_play")
    threading.Thread(target=fade_music, args=(FILLER_VLC_PORT, FILLER_VLC_PASSWORD, 0, filler_music_target_volume)).start()

def fade_out_filler():
    """Fades out the filler music and then pauses it."""
    log_message("Fading out filler music...")
    def fade_and_pause():
        fade_music(FILLER_VLC_PORT, FILLER_VLC_PASSWORD, filler_music_target_volume, 0)
        send_vlc_command(FILLER_VLC_PORT, FILLER_VLC_PASSWORD, "pl_pause")
        log_message("Filler music faded out and paused.")
    threading.Thread(target=fade_and_pause).start()


# --- YouTube Downloader ---
def download_video(youtube_url):
    """Downloads a YouTube video, saves metadata, and returns ID and title."""
    video_id = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=8))
    # Note: The final extension is determined by yt-dlp, so we handle it later.
    output_template = os.path.join(VIDEO_DIR, f"{video_id}")

    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': output_template,
        'merge_output_format': 'mp4',
        'force_overwrites': True,
        'quiet': True,
        'noplaylist': True,
        'writethumbnail': True, # Save thumbnail
    }
    
    # Add cookies file if it exists
    if os.path.exists(YOUTUBE_COOKIES_FILE):
        ydl_opts['cookiefile'] = YOUTUBE_COOKIES_FILE
        log_message(f"Using YouTube cookies file: {YOUTUBE_COOKIES_FILE}")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=True)
            title = info.get('title', 'Unknown Title')
            # The actual downloaded file path
            downloaded_file = ydl.prepare_filename(info)

            # Save metadata to a .json file
            metadata = {
                "id": video_id,
                "title": title,
                "original_url": youtube_url,
                "download_date": time.time()
            }
            with open(f"{output_template}.json", "w") as f:
                import json
                json.dump(metadata, f)

            log_message(f"Successfully downloaded '{title}' with ID {video_id}")
            # Update our in-memory cache
            downloaded_videos[video_id] = title
            return video_id, title
    except Exception as e:
        log_message(f"Error downloading video: {e}")
        return None, None

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
    video_id, title = download_video(url)

    if video_id:
        return jsonify({"success": True, "video_id": video_id, "title": title})
    else:
        return jsonify({"error": "Failed to download video"}), 500

@app.route('/externalscreen')
def external_screen():
    """Serves the external player page."""
    return render_template('external_screen.html')

@app.route('/video/<video_id>')
def video_stream(video_id):
    """Streams the video file."""
    # Find the video file, ignoring the specific extension
    video_path = None
    found_filename = None
    for f in os.listdir(VIDEO_DIR):
        if f.startswith(video_id) and not f.endswith(('.json', '.webp', '.jpg')):
            video_path = VIDEO_DIR
            found_filename = f
            break
    if not video_path:
        return "Video not found", 404
    return send_from_directory(video_path, found_filename)

# --- New Sync Logic State ---
external_client_ready = threading.Event()
master_vlc_ready = threading.Event()

def preload_and_trigger_playback(video_id):
    """
    Coordinates the entire preload and sync-play process in the background.
    """
    # --- 1. Preload on Master VLC (in a paused state) ---
    video_path = None
    for f in os.listdir(VIDEO_DIR):
        if f.startswith(video_id) and not f.endswith(('.json', '.webp', '.jpg')):
            video_path = os.path.join(VIDEO_DIR, f)
            break
    
    if not video_path:
        log_message(f"ERROR: Could not find video path for {video_id} during preload.")
        return

    # Fade out filler music before loading
    status = send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, "")
    if not (status and status.get('state') != 'stopped'):
        fade_out_filler()
        time.sleep(3.5) # Wait for fade

    # Load the video, set the volume, and then pause
    send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, "pl_empty")
    time.sleep(0.1)
    send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, f"in_enqueue&input={video_path}", is_path=True)
    time.sleep(0.1)
    send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, f"volume&val={karaoke_music_target_volume}")
    time.sleep(0.1)
    send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, "pl_play")
    time.sleep(0.5) # Give VLC time to load and enter the 'playing' state
    send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, "pl_pause") # Immediately pause it
    
    # Check that VLC is now paused
    time.sleep(0.2)
    vlc_status = send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, "")
    if vlc_status and vlc_status.get('state') == 'paused':
        log_message("Master VLC is preloaded and paused.")
        master_vlc_ready.set() # Signal that the master is ready
    else:
        log_message(f"ERROR: Master VLC failed to enter paused state. Status: {vlc_status}")
        return # Abort if master VLC isn't ready

    # --- 2. Wait for both players to be ready (optional for external) ---
    if wait_for_external_enabled:
        log_message(f"Waiting for players to be ready for {video_id}...")
        external_ready = external_client_ready.wait(timeout=10)
        master_ready = master_vlc_ready.wait(timeout=10) # This should already be set, but we wait just in case

        if not external_ready or not master_ready:
            log_message(f"ERROR: Timed out waiting for players. External: {external_ready}, Master: {master_ready}")
            return
    else:
        log_message("Wait for external disabled. Proceeding with master-only readiness.")
        master_ready = master_vlc_ready.wait(timeout=10)
        if not master_ready:
            log_message("ERROR: Timed out waiting for master VLC readiness.")
            return

    # --- 3. Trigger simultaneous playback with offset ---
    log_message(f"All players ready. Triggering playback with {sync_offset_ms}ms offset.")
    
    offset_seconds = sync_offset_ms / 1000.0

    if offset_seconds >= 0:
        # Positive offset: External screen starts first
        socketio.emit('player_action', {'action': 'play_now'})
        time.sleep(offset_seconds)
        send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, "pl_pause")
    else:
        # Negative offset: Master VLC starts first
        send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, "pl_pause")
        time.sleep(abs(offset_seconds))
        socketio.emit('player_action', {'action': 'play_now'})

    global karaoke_player_is_active
    karaoke_player_is_active = True
    log_message(f"Playback started for {video_id}.")

    # --- 4. Clean up events ---
    external_client_ready.clear()
    master_vlc_ready.clear()

@app.route('/play', methods=['POST'])
def handle_play():
    """
    Receives the play request and kicks off the background preload and sync process.
    """
    global current_video_id
    video_id = request.json.get('video_id')
    if not video_id:
        return jsonify({"error": "Video ID is required"}), 400

    log_message(f"Received play request for {video_id}. Starting background sync process.")
    current_video_id = video_id
    
    # Clear any stale ready signals from a previous run
    external_client_ready.clear()
    master_vlc_ready.clear()

    # --- Preload on External Client (only if we care to sync external) --- 
    if wait_for_external_enabled:
        socketio.emit('player_action', {'action': 'preload', 'video_id': video_id})

    # --- Start the background thread to manage the whole process ---
    threading.Thread(target=preload_and_trigger_playback, args=(video_id,)).start()

    return jsonify({"success": True, "message": "Preload initiated."})

@socketio.on('video_ready')
def on_video_ready(data):
    """
    This event is triggered by the external screen when it has buffered
    and is ready to play.
    """
    video_id = data.get('video_id')
    log_message(f"External client is ready to play {video_id}.")
    external_client_ready.set() # Signal that the external client is ready

@app.route('/sync_offset', methods=['POST'])
def handle_sync_offset():
    """Handles updating the manual sync offset."""
    global sync_offset_ms
    offset = request.json.get('offset')
    if offset is None:
        return jsonify({"error": "Offset is required"}), 400
    
    sync_offset_ms = int(offset)
    log_message(f"Set sync offset to {sync_offset_ms}ms.")
    return jsonify({"success": True})

@app.route('/seek', methods=['POST'])
def handle_seek():
    """Handles seeking within the karaoke video."""
    seek_time = request.json.get('time')
    if seek_time is None:
        return jsonify({"error": "Time is required"}), 400

    log_message(f"Received seek request to time: {seek_time}")
    send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, f"seek&val={seek_time}")
    socketio.emit('player_action', {'action': 'seek', 'time': float(seek_time)})
    return jsonify({"success": True})


@app.route('/control', methods=['POST'])
def handle_control():
    """Handles playback controls like pause, resume, restart."""
    action = request.json.get('action')
    if not action:
        return jsonify({"error": "Action is required"}), 400

    log_message(f"Received control action: {action}")
    global karaoke_player_is_active
    if action == 'pause_resume':
        send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, "pl_pause")
        # Check if we should resume filler music
        time.sleep(0.5) # Give vlc time to process
        status = send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, "")
        if status and status.get('state') == 'paused':
            karaoke_player_is_active = False
            fade_in_filler()
            socketio.emit('player_action', {'action': 'pause'})
        else:
            karaoke_player_is_active = True
            fade_out_filler()
            socketio.emit('player_action', {'action': 'resume'})
    elif action == 'restart':
        send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, "seek&val=0")
        socketio.emit('player_action', {'action': 'restart'})
    elif action == 'stop':
        send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, "pl_stop")
        karaoke_player_is_active = False
        global current_video_id
        current_video_id = None
        fade_in_filler()
        socketio.emit('player_action', {'action': 'stop'})

    return jsonify({"success": True, "message": f"Action '{action}' executed."})

@app.route('/volume', methods=['POST'])
def handle_volume():
    """Handles volume control for karaoke or filler music."""
    target = request.json.get('target')
    level = int(request.json.get('level')) # Should be 0-256 for VLC
    if not all([target, level is not None]):
        return jsonify({"error": "Target and level are required"}), 400

    if target == 'karaoke':
        port, password = KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD
        global karaoke_music_target_volume
        karaoke_music_target_volume = level
    elif target == 'filler':
        port, password = FILLER_VLC_PORT, FILLER_VLC_PASSWORD
        global filler_music_target_volume
        filler_music_target_volume = level
    else:
        return jsonify({"error": "Invalid target"}), 400

    send_vlc_command(port, password, f"volume&val={level}")
    log_message(f"Set volume for '{target}' to {level}")
    return jsonify({"success": True})

@app.route('/videos')
def list_videos():
    """Returns a list of all downloaded videos, sorted by download date."""
    video_details = []
    for video_id, title in downloaded_videos.items():
        json_path = os.path.join(VIDEO_DIR, f"{video_id}.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r') as f:
                    import json
                    metadata = json.load(f)
                    video_details.append({
                        "id": video_id,
                        "title": title,
                        "date": metadata.get("download_date", 0),
                        "url": metadata.get("original_url", "")
                    })
            except Exception as e:
                log_message(f"Could not read metadata for {video_id}: {e}")
    
    # Sort by date, newest first
    sorted_videos = sorted(video_details, key=lambda x: x['date'], reverse=True)
    return jsonify(sorted_videos)

@app.route('/delete', methods=['POST'])
def delete_video():
    """Deletes a video and its associated files."""
    video_id = request.json.get('video_id')
    if not video_id:
        return jsonify({"error": "Video ID is required"}), 400

    log_message(f"Received delete request for video ID: {video_id}")
    
    # Remove from cache
    if video_id in downloaded_videos:
        del downloaded_videos[video_id]

    # Find and delete all associated files
    files_deleted = False
    for filename in os.listdir(VIDEO_DIR):
        if filename.startswith(video_id):
            try:
                os.remove(os.path.join(VIDEO_DIR, filename))
                log_message(f"Deleted file: {filename}")
                files_deleted = True
            except Exception as e:
                log_message(f"Error deleting file {filename}: {e}")
                return jsonify({"error": f"Error deleting file {filename}"}), 500
    
    if files_deleted:
        return jsonify({"success": True, "message": f"Deleted video {video_id}"})
    else:
        return jsonify({"error": "Video not found"}), 404

@app.route('/filler_music', methods=['GET'])
def list_filler_music():
    """Returns a list of available filler music files."""
    try:
        files = [
            f for f in os.listdir(FILLER_MUSIC_DIR) 
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

    new_track_path = os.path.join(FILLER_MUSIC_DIR, track_name)
    if not os.path.exists(new_track_path):
        return jsonify({"error": "Track not found"}), 404
    
    current_filler_track = track_name
    log_message(f"Changing filler music to: {track_name}")

    # Stop current filler, clear playlist, add new track, and play
    send_vlc_command(FILLER_VLC_PORT, FILLER_VLC_PASSWORD, "pl_stop")
    time.sleep(0.1)
    send_vlc_command(FILLER_VLC_PORT, FILLER_VLC_PASSWORD, "pl_empty")
    time.sleep(0.1)
    send_vlc_command(FILLER_VLC_PORT, FILLER_VLC_PASSWORD, f"in_enqueue&input={new_track_path}", is_path=True)
    time.sleep(0.1)
    send_vlc_command(FILLER_VLC_PORT, FILLER_VLC_PASSWORD, "pl_play")
    
    # Let it play for a moment to get metadata
    time.sleep(0.5) 
    
    # Get duration and seek to a random spot
    status = send_vlc_command(FILLER_VLC_PORT, FILLER_VLC_PASSWORD, "")
    if status and 'length' in status and status['length'] > 0:
        duration = status['length']
        # Seek to a random time, leaving a little buffer at the end
        random_time = random.randint(0, max(0, int(duration) - 5))
        log_message(f"Seeking filler music to {random_time}s (duration: {duration}s)")
        send_vlc_command(FILLER_VLC_PORT, FILLER_VLC_PASSWORD, f"seek&val={random_time}")

    send_vlc_command(FILLER_VLC_PORT, FILLER_VLC_PASSWORD, f"volume&val={filler_music_target_volume}")

    return jsonify({"success": True})

@app.route('/status')
def get_status():
    """Gets the status of the karaoke player."""
    status = send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, "")
    if status:
        # Add current volume to the status response
        status['karaoke_volume'] = status.get('volume', 0)
        filler_status = send_vlc_command(FILLER_VLC_PORT, FILLER_VLC_PASSWORD, "")
        status['filler_volume'] = filler_status.get('volume', 0) if filler_status else 0
        
        return jsonify({
            "state": status.get('state'),
            "current_video_id": current_video_id,
            "current_filler_track": current_filler_track,
            "time": status.get('time'),
            "length": status.get('length'),
            "wait_for_external_enabled": wait_for_external_enabled
        })
    return jsonify({"error": "Could not get status"}), 500

@app.route('/settings/wait_for_external', methods=['POST'])
def set_wait_for_external():
    """Enable/disable waiting for external player readiness."""
    global wait_for_external_enabled
    value = request.json.get('enabled')
    if value is None:
        return jsonify({"error": "'enabled' is required"}), 400
    wait_for_external_enabled = bool(value)
    log_message(f"Wait for external set to {wait_for_external_enabled}")
    return jsonify({"success": True, "wait_for_external_enabled": wait_for_external_enabled})


# --- Main Execution ---
def load_video_cache():
    """Scans the video directory and populates the title cache."""
    log_message("Loading video cache...")
    os.makedirs(VIDEO_DIR, exist_ok=True)
    for filename in os.listdir(VIDEO_DIR):
        if filename.endswith(".json"):
            try:
                with open(os.path.join(VIDEO_DIR, filename), 'r') as f:
                    import json
                    metadata = json.load(f)
                    downloaded_videos[metadata['id']] = metadata['title']
            except Exception as e:
                log_message(f"Could not load metadata from {filename}: {e}")
    log_message(f"Loaded {len(downloaded_videos)} videos into cache.")

def monitor_karaoke_player():
    """A background thread to check if a song has ended and broadcast sync events."""
    global karaoke_player_is_active, current_video_id
    while True:
        time.sleep(2) # Sync interval
        if not karaoke_player_is_active:
            continue

        # If a song is supposed to be active, get its status.
        status = send_vlc_command(KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD, "", debug=False)
        if not status:
            continue

        # If VLC reports it's playing, send a sync event with the current time.
        if status.get('state') == 'playing':
            current_time = status.get('time', 0)
            socketio.emit('sync', {'time': current_time})
        # If VLC reports it's stopped, the song has ended.
        elif status.get('state') == 'stopped':
            log_message("Karaoke video finished playing.")
            karaoke_player_is_active = False
            current_video_id = None
            fade_in_filler()
            socketio.emit('player_action', {'action': 'stop'})

def start_app():
    """Initializes and starts the application components."""
    log_message("--- KJ Controller Starting Up ---")
    load_video_cache()

    # Launch VLC instances
    launch_vlc_instance("karaoke", KARAOKE_VLC_PORT, KARAOKE_VLC_PASSWORD)
    filler_path = os.path.join(FILLER_MUSIC_DIR, current_filler_track)
    launch_vlc_instance("filler", FILLER_VLC_PORT, FILLER_VLC_PASSWORD, filler_path, True)

    # Wait for VLC instances to be ready
    time.sleep(3)

    # Start filler music
    fade_in_filler()

    # Start the karaoke player monitor in a background thread
    monitor_thread = threading.Thread(target=monitor_karaoke_player, daemon=True)
    monitor_thread.start()

    # Start Flask app using SocketIO
    log_message("Starting Flask-SocketIO server...")
    socketio.run(app, host='0.0.0.0', port=5000)

if __name__ == '__main__':
    start_app()
