"""Shared test fixtures for kj-controller."""

import json
import os
import sys

import pytest

# Ensure kj-controller directory is on sys.path
app_dir = os.path.join(os.path.dirname(__file__), '..')
if app_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(app_dir))


@pytest.fixture
def tmp_media_dir(tmp_path):
    """Temp directory structure with downloads/ and media/ subdirs."""
    downloads = tmp_path / "downloads"
    media = tmp_path / "media"
    downloads.mkdir()
    media.mkdir()
    return tmp_path


@pytest.fixture
def mock_config(tmp_media_dir):
    """Test config dict with temp paths."""
    return {
        "download_folder": str(tmp_media_dir / "downloads"),
        "media_folders": [
            str(tmp_media_dir / "downloads"),
            str(tmp_media_dir / "media"),
        ],
        "media_index_path": str(tmp_media_dir / "media_index.json"),
        "filler_music_dir": str(tmp_media_dir),
        "log_file": str(tmp_media_dir / "test.log"),
        "karaoke_vlc_port": 8080,
        "filler_vlc_port": 8081,
        "karaoke_vlc_password": "karaoke",
        "filler_vlc_password": "filler",
        "audio_devices": {"hdmiout": "HDMI Output"},
        "default_audio_device": "hdmiout",
        "default_filler_track": "",
        "flask_port": 5000,
    }


@pytest.fixture
def flask_app(mock_config):
    """Create a Flask app via the app factory with test config."""
    from app import create_app
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    return app


@pytest.fixture
def flask_test_client(flask_app):
    """Flask test client for route testing."""
    with flask_app.test_client() as client:
        yield client
