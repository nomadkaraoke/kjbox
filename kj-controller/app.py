"""KJ Controller - app factory and entry point."""

import os
import shutil
import subprocess
import sys
import threading
import time

from flask import Flask

from catalog import ExternalCatalog
from config import is_pi, load_config
from media import MediaIndex
from overlay import OverlayManager
from routes import routes_bp
from utils import log_message
from vlc import VLCManager
from zip_playback import ZipPlayback


def create_app(config=None):
    """Create and configure the Flask application."""
    flask_app = Flask(__name__)
    cfg = config or load_config()
    flask_app.kj_config = cfg
    flask_app.media = MediaIndex(cfg)
    flask_app.vlc = VLCManager(cfg, enabled=False if config else None)
    flask_app.catalog = ExternalCatalog(cfg)
    flask_app.zip_playback = ZipPlayback(cfg)
    overlay_path = cfg.get('overlays_path') if config else None
    flask_app.overlay_manager = OverlayManager(config_path=overlay_path)
    flask_app.download_state = {'status': 'idle'}
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

    # Create shared overlay manager
    overlay_mgr = OverlayManager()
    vlc.on_karaoke_end = lambda: overlay_mgr.set_karaoke_playing(False)

    # Platform-specific setup
    if is_pi():
        log_message("Running on NomadPi - enabling VLC and X11 setup.", cfg)
        subprocess.run(['xhost', '+SI:localuser:dietpi'], env={**os.environ, 'DISPLAY': ':0'}, capture_output=True)
        os.makedirs('/run/user/1000', exist_ok=True)
        subprocess.run(['chown', 'dietpi:dietpi', '/run/user/1000'], capture_output=True)
    elif cfg.get('enable_vlc', False):
        log_message("Running on karaoke device (non-Pi) - VLC enabled via config.", cfg)
    else:
        log_message("Running in local/dev mode - VLC disabled, web UI and media scanning only.", cfg)

    # Start websockify on any karaoke device (Pi or mini PC with enable_vlc)
    if (is_pi() or cfg.get('enable_vlc', False)) and cfg.get('websockify_enabled', True):
        ws_port = cfg.get('websockify_port', 6080)
        vnc_target = cfg.get('vnc_target', 'localhost:5900')
        # Find websockify binary: check venv bin dir first, then system PATH
        venv_bin = os.path.dirname(sys.executable)
        ws_bin = os.path.join(venv_bin, 'websockify')
        if not os.path.isfile(ws_bin):
            ws_bin = shutil.which('websockify')
        if ws_bin:
            log_message(f"Starting websockify on :{ws_port} -> {vnc_target}...", cfg)
            ws_cmd = [ws_bin, str(ws_port), vnc_target]
            tls_cert = cfg.get('tls_cert', '')
            tls_key = cfg.get('tls_key', '')
            if tls_cert and tls_key and os.path.isfile(tls_cert) and os.path.isfile(tls_key):
                ws_cmd[1:1] = ['--cert', tls_cert, '--key', tls_key]
            subprocess.Popen(
                ws_cmd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            log_message("WARNING: websockify not found - VNC preview unavailable.", cfg)

    # Create Flask app with services
    flask_app = Flask(__name__)
    flask_app.kj_config = cfg
    media = MediaIndex(cfg)
    media.load()
    flask_app.media = media
    flask_app.vlc = vlc
    flask_app.catalog = ExternalCatalog(cfg)
    flask_app.zip_playback = ZipPlayback(cfg)
    flask_app.overlay_manager = overlay_mgr
    flask_app.download_state = {'status': 'idle'}
    flask_app.register_blueprint(routes_bp)

    # Log external catalog status
    if flask_app.catalog.is_available():
        log_message(f"External catalog available: {flask_app.catalog.count()} entries.", cfg)
    else:
        log_message("External catalog not yet built. Use POST /catalog/build to create.", cfg)

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
    flask_port = cfg.get('flask_port', 80)
    tls_cert = cfg.get('tls_cert', '')
    tls_key = cfg.get('tls_key', '')
    ssl_ctx = None
    if tls_cert and tls_key and os.path.isfile(tls_cert) and os.path.isfile(tls_key):
        ssl_ctx = (tls_cert, tls_key)
        if flask_port == 80:
            flask_port = 443
        log_message(f"Starting Flask server on port {flask_port} (HTTPS)...", cfg)
    else:
        log_message(f"Starting Flask server on port {flask_port}...", cfg)
    flask_app.run(host='0.0.0.0', port=flask_port, ssl_context=ssl_ctx, threaded=True)


if __name__ == '__main__':
    start_app()
