"""KJ Controller - app factory and entry point."""

import os
import subprocess
import threading
import time

from flask import Flask

from config import is_pi, load_config
from media import MediaIndex
from routes import routes_bp
from utils import log_message
from vlc import VLCManager


def create_app(config=None):
    """Create and configure the Flask application."""
    flask_app = Flask(__name__)
    cfg = config or load_config()
    flask_app.kj_config = cfg
    flask_app.media = MediaIndex(cfg)
    flask_app.vlc = VLCManager(cfg, enabled=False if config else None)
    flask_app.register_blueprint(routes_bp)
    return flask_app


def start_app():  # pragma: no cover
    """Initializes and starts the application components."""
    log_message("--- KJ Controller Starting Up ---")

    # Load config
    cfg = load_config()
    os.makedirs(cfg['download_folder'], exist_ok=True)

    # Create VLCManager (auto-detects Pi)
    vlc = VLCManager(cfg)

    # Set default filler track from config, or auto-detect from filler music dir
    configured_filler = cfg.get('default_filler_track', '')
    filler_dir = cfg.get('filler_music_dir', '')
    if configured_filler:
        vlc.current_filler_track = configured_filler
    elif filler_dir and os.path.isdir(filler_dir):
        for f in sorted(os.listdir(filler_dir)):
            if os.path.splitext(f)[1].lower() in {'.mp3', '.wav', '.flac', '.ogg'}:
                vlc.current_filler_track = f
                break
    if vlc.current_filler_track:
        log_message(f"Default filler track: {vlc.current_filler_track}", cfg)
    else:
        log_message("WARNING: No filler music track configured or found.", cfg)

    # Pi-specific setup
    if is_pi():
        log_message("Running on NomadPi - enabling VLC and X11 setup.", cfg)
        subprocess.run(['xhost', '+SI:localuser:dietpi'], env={**os.environ, 'DISPLAY': ':0'}, capture_output=True)
        os.makedirs('/run/user/1000', exist_ok=True)
        subprocess.run(['chown', 'dietpi:dietpi', '/run/user/1000'], capture_output=True)
    else:
        log_message("Running in local/dev mode - VLC disabled, web UI and media scanning only.", cfg)

    # Create Flask app with services
    flask_app = Flask(__name__)
    flask_app.kj_config = cfg
    media = MediaIndex(cfg)
    media.load()
    flask_app.media = media
    flask_app.vlc = vlc
    flask_app.register_blueprint(routes_bp)

    # Launch VLC instances (only on Pi)
    if vlc.enabled:
        karaoke_port = cfg.get('karaoke_vlc_port', 8080)
        karaoke_pw = cfg.get('karaoke_vlc_password', 'karaoke')
        filler_port = cfg.get('filler_vlc_port', 8081)
        filler_pw = cfg.get('filler_vlc_password', 'filler')

        vlc.launch_instance("karaoke", karaoke_port, karaoke_pw)
        filler_path = os.path.join(filler_dir, vlc.current_filler_track) if filler_dir and vlc.current_filler_track else ''
        vlc.launch_instance("filler", filler_port, filler_pw, filler_path, True)

        time.sleep(3)
        vlc.fade_in_filler()

        # Start the karaoke player monitor in a background thread
        monitor_thread = threading.Thread(target=vlc.monitor_karaoke, daemon=True)
        monitor_thread.start()

    # Start Flask app
    flask_port = cfg.get('flask_port', 5000)
    log_message(f"Starting Flask server on port {flask_port}...", cfg)
    flask_app.run(host='0.0.0.0', port=flask_port, threaded=True)


if __name__ == '__main__':
    start_app()
