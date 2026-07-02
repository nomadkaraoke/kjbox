"""Configuration loading, constants, and platform detection."""

import json
import os
import tempfile

# --- Constants ---
MEDIA_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.webm', '.mov', '.mp3', '.wav', '.flac', '.ogg', '.zip', '.cdg'}
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Karaoke renderer modes — keys accepted by PlaybackCoordinator.switch_renderer
RENDER_MODE_MPV = 'mpv'
RENDER_MODE_VLC = 'vlc'
RENDER_MODES = (RENDER_MODE_MPV, RENDER_MODE_VLC)
DEFAULT_RENDER_MODE = RENDER_MODE_MPV


def is_pi():
    """Detect if running on NomadPi (DietPi on Linux ARM)."""
    return os.path.exists('/boot/dietpi.txt')


def load_config(config_file=None):
    """Loads config from config.json, falling back to platform-appropriate defaults."""
    if config_file is None:
        config_file = CONFIG_FILE
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
            "hw:0,0": "Analog / 3.5mm jack",
            "usbmixer": "USB Mixer",
        },
        "default_audio_device": "hdmiout",
        "default_filler_track": "",
        "flask_port": 80,
        "external_catalog_db": os.path.join(APP_DIR, 'external_media.db'),
        "external_file_list": "",
        "external_media_mount": "",
        "media_db_path": os.path.join(APP_DIR, "media_library.db"),
        # LLM parse confidence gate: >= threshold clears needs_review.
        "parse_confidence_threshold": 0.75,
        "master_sync_source": "gs://nomadkaraoke-divebar-files/files/Nomad Karaoke/MP4-720p/",
        "master_sync_dest": "",
        "master_sync_credentials_file": "",
        "master_sync_enabled": False,
        # "" -> derived as http://127.0.0.1:<app_bind_port>/rescan (the app's internal
        # port); NOT flask_port (the public proxy, e.g. Caddy on 80).
        "master_sync_rescan_url": "",
        "websockify_port": 6080,
        "vnc_target": "localhost:5900",
        "websockify_enabled": True,
        "tls_cert": os.path.join(APP_DIR, 'certs', 'cert.pem'),
        "tls_key": os.path.join(APP_DIR, 'certs', 'key.pem'),
        "kn_preferred_brands": ["KV", "KFN"],
        "divebar_api_url": "",
        "gen_api_url": "",
        "gen_api_token": "",
        "gen_poll_interval": 60,
        "render_mode": DEFAULT_RENDER_MODE,
        "sing_public_url_base": "https://sing.nomadkaraoke.com",
        "sing_public_host": "sing.nomadkaraoke.com",
        "sing_local_url_base": "",
        "sing_rate_limit_per_ip": 5,
        "sing_rate_limit_window_s": 300,
        "sing_estimate_transition_s": 30,
        "sing_estimate_default_song_s": 240,
        "sing_estimate_min_spread_s": 120,
        # Browser preview playback (audition files in the KJ browser; never the
        # device A/V output). Cache transcodes once, ever.
        "preview_cache_dir": "",  # "" -> resolve_preview_cache_dir(): sibling "preview-cache" of download_folder
        "preview_cache_max_bytes": 8 * 1024 * 1024 * 1024,  # 8 GiB LRU cap
        "preview_transcode_height": 480,
        "preview_transcode_preset": "veryfast",
    }
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                user_config = json.load(f)
            defaults.update(user_config)
            print(f"Loaded config from {config_file}")
        except Exception as e:
            print(f"Error loading config file, using defaults: {e}")
    else:
        print(f"No config file at {config_file}, using defaults.")
    return defaults


def resolve_preview_cache_dir(config):
    """Resolve the browser-preview cache directory.

    An explicit ``preview_cache_dir`` wins. Otherwise the cache lives in a
    ``preview-cache`` directory that is a *sibling* of the download folder, NOT a
    child of it — so its artifacts (transcodes, extracted CDG halves) fall outside
    every indexed media path and can never be re-indexed as library/download items.
    On the device ``dirname(/opt/nomad/YTDownloads)`` -> ``/opt/nomad/preview-cache``.
    """
    configured = (config or {}).get("preview_cache_dir")
    if configured:
        return configured
    download_folder = (config or {}).get("download_folder")
    if download_folder:
        base_dir = os.path.dirname(os.path.realpath(download_folder))
    else:
        # No download folder configured: fall back to a writable temp location,
        # NOT dirname("/tmp") (which is "/" on Linux).
        base_dir = "/tmp"
    return os.path.join(base_dir, "preview-cache")


def save_config_value(key, value, config_file=None):
    """Persists a runtime setting to a config file (default CONFIG_FILE).

    Uses atomic write (temp file + fsync + rename) so power loss mid-write
    cannot corrupt the config file.

    An optional ``config_file`` path overrides the module-level ``CONFIG_FILE``
    constant — useful in tests that target a temporary path.
    """
    target = config_file or CONFIG_FILE
    try:
        config_data = {}
        if os.path.exists(target):
            with open(target, 'r') as f:
                config_data = json.load(f)
        config_data[key] = value
        dir_name = os.path.dirname(target)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.json')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(config_data, f, indent=2)
                f.write('\n')
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        print(f"Error saving config value {key}: {e}")
        raise
