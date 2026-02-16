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
        "external_catalog_db": str(tmp_media_dir / "test_catalog.db"),
        "external_file_list": "",
        "external_media_mount": "",
    }


@pytest.fixture
def catalog_db_path(tmp_path):
    """Temporary path for a test catalog database."""
    return str(tmp_path / "test_catalog.db")


@pytest.fixture
def sample_file_list(tmp_path):
    """Create a sample file list for catalog building."""
    lines = [
        "/Volumes/Nomad4TBOne/HyperMule/Karaoke/SC2411-08 - Rascal Flatts - Life Is A Highway.zip",
        "/Volumes/Nomad4TBOne/HyperMule/Karaoke/PHK004 - Bon Jovi - Livin On A Prayer.zip",
        "/Volumes/Nomad4TBOne/HyperMule/Videos/Michael Jackson - Billie Jean.mp4",
        "/Volumes/Nomad4TBOne/HyperMule/Karaoke/Journey - Don't Stop Believin.zip",
        "/Volumes/Nomad4TBOne/HyperMule/Videos/Queen - Bohemian Rhapsody.mp4",
    ]
    file_list = tmp_path / "file_list.txt"
    file_list.write_text('\n'.join(lines) + '\n')
    return str(file_list)


@pytest.fixture
def flask_app(mock_config):
    """Create a Flask app via the app factory with test config."""
    from app import create_app
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    yield app
    # Close catalog DB connection to avoid ResourceWarning
    app.catalog.close()


@pytest.fixture
def flask_test_client(flask_app):
    """Flask test client for route testing."""
    with flask_app.test_client() as client:
        yield client
