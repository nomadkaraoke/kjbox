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
from playback import PlaybackCoordinator
from rotation import RotationManager
from routes import routes_bp
from sleep_mode import SleepManager
from utils import log_message
from audio_monitor import AudioMonitor
from chromium import ChromiumManager
from zip_playback import ZipPlayback


def _init_rotation(cfg):
    """Create a RotationManager with SQLite DB and optional Sheet sync."""
    db_path = cfg.get('rotation_db_path', os.path.expanduser('~/kjdata/rotation.db'))
    sheet_id = cfg.get('rotation_sheet_id')
    creds_file = cfg.get('rotation_credentials_file')
    sync_interval = cfg.get('rotation_sync_interval', 30)
    return RotationManager(db_path, sheet_id, creds_file, sync_interval)


def _restore_wallpaper():
    """Restore custom wallpaper from ~/kjdata/ backup if git overwrote the desktop copy."""
    kjdata_bg = os.path.expanduser('~/kjdata/rotation-bg.png')
    desktop_bg = os.path.join(os.path.dirname(__file__), '..', 'desktop', 'rotation-bg.png')
    desktop_bg = os.path.abspath(desktop_bg)
    if os.path.exists(kjdata_bg):
        try:
            shutil.copy2(kjdata_bg, desktop_bg)
        except OSError:
            pass


def _start_http_redirect_thread():
    """Tiny HTTP listener on :80 that 301-redirects to HTTPS.

    Why: device only listens on 443, but mDNS-resolved clients sometimes try
    HTTP first (bookmarks, prefetch, Happy Eyeballs on IPv6 link-local). With
    no :80 listener those attempts stall for ~75s before failing — users feel
    this as the page "loading slowly".
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import socket

    class _Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            host = (self.headers.get('Host') or 'nomadpc.local').split(':')[0]
            self.send_response(301)
            self.send_header('Location', f'https://{host}{self.path}')
            self.send_header('Connection', 'close')
            self.end_headers()
        do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_GET
        def log_message(self, *a, **kw):
            pass

    class _DualStack(ThreadingHTTPServer):
        address_family = socket.AF_INET6
        def server_bind(self):
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            super().server_bind()

    def _run():
        try:
            _DualStack(('::', 80), _Redirect).serve_forever()
        except Exception as e:
            log_message(f"HTTP→HTTPS redirect listener not started: {e}")

    threading.Thread(target=_run, daemon=True).start()


def _get_version():
    """Read version from pyproject.toml for cache-busting static assets."""
    try:
        pyproject = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pyproject.toml')
        with open(pyproject) as f:
            for line in f:
                if line.strip().startswith('version'):
                    parts = line.split('"')
                    if len(parts) >= 2:
                        return parts[1]
    except Exception:
        pass
    return str(int(time.time()))


def create_app(config=None):
    """Create and configure the Flask application."""
    flask_app = Flask(__name__)
    flask_app.config['APP_VERSION'] = _get_version()
    cfg = config or load_config()
    flask_app.kj_config = cfg
    flask_app.media = MediaIndex(cfg)
    overlay_path = cfg.get('overlays_path') if config else None
    flask_app.overlay_manager = OverlayManager(config_path=overlay_path)
    flask_app.vlc = PlaybackCoordinator(
        cfg,
        enabled=False if config else None,
        overlay_manager=flask_app.overlay_manager,
    )
    flask_app.audio_monitor = AudioMonitor(flask_app.vlc, cfg)
    flask_app.catalog = ExternalCatalog(cfg)
    flask_app.zip_playback = ZipPlayback(cfg)
    flask_app.chromium = ChromiumManager(cfg)
    flask_app.rotation = _init_rotation(cfg)
    flask_app.rotation.media = flask_app.media
    flask_app.sleep_manager = SleepManager()
    flask_app.download_queue = {'items': [], 'worker_running': False}
    flask_app._download_lock = threading.Lock()

    # Restore custom wallpaper if git pull/reset overwrote it
    _restore_wallpaper()

    # Initialize gen API client and poller (optional — only if configured)
    gen_api_url = cfg.get('gen_api_url', '')
    gen_api_token = cfg.get('gen_api_token', '')
    if gen_api_url and gen_api_token:
        from gen_client import GenClient
        from gen_poller import GenPoller
        flask_app.gen_client = GenClient(gen_api_url, gen_api_token)
        flask_app.gen_poller = GenPoller(
            flask_app.gen_client, flask_app.rotation,
            flask_app.media, cfg.get('download_folder', ''),
            poll_interval=cfg.get('gen_poll_interval', 60),
        )
    else:
        flask_app.gen_client = None
        flask_app.gen_poller = None

    flask_app.register_blueprint(routes_bp)
    return flask_app


def start_app():  # pragma: no cover
    """Initializes and starts the application components."""
    log_message("--- KJ Controller Starting Up ---")

    # Load config
    cfg = load_config()
    os.makedirs(cfg['download_folder'], exist_ok=True)

    # Create shared overlay manager
    overlay_mgr = OverlayManager()

    # PlaybackCoordinator owns filler + one karaoke player; swappable at runtime.
    vlc = PlaybackCoordinator(cfg, overlay_manager=overlay_mgr)

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
    flask_app.audio_monitor = AudioMonitor(vlc, cfg)
    flask_app.catalog = ExternalCatalog(cfg)
    flask_app.zip_playback = ZipPlayback(cfg)
    flask_app.chromium = ChromiumManager(cfg)
    flask_app.overlay_manager = overlay_mgr
    flask_app.rotation = _init_rotation(cfg)
    flask_app.rotation.media = flask_app.media
    log_message("Rotation enabled (SQLite primary).", cfg)
    flask_app.rotation._write_display_cache()  # Refresh cache so conky doesn't show "Offline"
    if flask_app.rotation.sync:
        log_message("Sheet sync enabled.", cfg)
    flask_app.sleep_manager = SleepManager()
    if flask_app.sleep_manager.is_sleeping():
        log_message("Sleep mode is active (from previous session).", cfg)
    flask_app.download_queue = {'items': [], 'worker_running': False}
    flask_app._download_lock = threading.Lock()

    # Initialize gen API client and poller (optional)
    gen_api_url = cfg.get('gen_api_url', '')
    gen_api_token = cfg.get('gen_api_token', '')
    if gen_api_url and gen_api_token:
        from gen_client import GenClient
        from gen_poller import GenPoller
        flask_app.gen_client = GenClient(gen_api_url, gen_api_token)
        flask_app.gen_poller = GenPoller(
            flask_app.gen_client, flask_app.rotation,
            flask_app.media, cfg.get('download_folder', ''),
            poll_interval=cfg.get('gen_poll_interval', 60),
        )
        flask_app.gen_poller.start()
        log_message("Gen API integration enabled.", cfg)
    else:
        flask_app.gen_client = None
        flask_app.gen_poller = None

    flask_app.register_blueprint(routes_bp)

    # Log external catalog status
    if flask_app.catalog.is_available():
        log_message(f"External catalog available: {flask_app.catalog.count()} entries.", cfg)
    else:
        log_message("External catalog not yet built. Use POST /catalog/build to create.", cfg)

    # Launch playback instances (reconnect + launch + monitor thread + fade-in)
    vlc.init_playback()

    # Start Flask app.
    # behind_proxy=True: a reverse proxy (e.g. Caddy) terminates TLS on :443
    # and forwards to Flask on localhost. Yields HTTP/2 + keep-alive so
    # browsers don't re-handshake TLS per asset/status-poll.
    if cfg.get('behind_proxy', False):
        host = cfg.get('app_bind_host', '127.0.0.1')
        port = cfg.get('app_bind_port', 5001)
        log_message(f"Starting Flask on {host}:{port} (behind reverse proxy)...", cfg)
        flask_app.run(host=host, port=port, threaded=True)
        return

    flask_port = cfg.get('flask_port', 80)
    tls_cert = cfg.get('tls_cert', '')
    tls_key = cfg.get('tls_key', '')
    ssl_ctx = None
    if tls_cert and tls_key and os.path.isfile(tls_cert) and os.path.isfile(tls_key):
        ssl_ctx = (tls_cert, tls_key)
        if flask_port == 80:
            flask_port = 443
        log_message(f"Starting Flask server on port {flask_port} (HTTPS)...", cfg)
        _start_http_redirect_thread()
    else:
        log_message(f"Starting Flask server on port {flask_port}...", cfg)
    # Bind dual-stack (IPv6 + IPv4) so mDNS AAAA hits don't stall Happy Eyeballs.
    flask_app.run(host='::', port=flask_port, ssl_context=ssl_ctx, threaded=True)


if __name__ == '__main__':
    start_app()
