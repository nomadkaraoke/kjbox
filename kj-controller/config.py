"""Configuration loading, constants, and platform detection."""

import json
import os

# --- Constants ---
MEDIA_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.webm', '.mov', '.mp3', '.wav', '.flac', '.ogg', '.zip', '.cdg'}
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
APP_DIR = os.path.dirname(os.path.abspath(__file__))


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
            "usbmixer": "USB Mixer",
        },
        "default_audio_device": "hdmiout",
        "default_filler_track": "",
        "flask_port": 5000,
        "external_catalog_db": os.path.join(APP_DIR, 'external_media.db'),
        "external_file_list": "",
        "external_media_mount": "",
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


def save_config_value(key, value):
    """Persists a runtime setting to config.json so it survives restarts."""
    try:
        config_data = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config_data = json.load(f)
        config_data[key] = value
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config_data, f, indent=2)
            f.write('\n')
    except Exception as e:
        print(f"Error saving config value {key}: {e}")
