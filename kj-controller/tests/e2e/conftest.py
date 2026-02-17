"""Playwright e2e test fixtures — starts a live Flask server for browser tests."""

import threading
import time

import pytest

from app import create_app


@pytest.fixture(scope="session")
def flask_port():
    """Fixed port for the test server."""
    return 5099


@pytest.fixture(scope="session")
def live_server(tmp_path_factory, flask_port):
    """Start a live Flask server in a background thread for Playwright tests."""
    tmp = tmp_path_factory.mktemp("e2e")
    downloads = tmp / "downloads"
    media = tmp / "media"
    downloads.mkdir()
    media.mkdir()

    config = {
        "download_folder": str(downloads),
        "media_folders": [str(downloads), str(media)],
        "media_index_path": str(tmp / "media_index.json"),
        "filler_music_dir": str(tmp),
        "log_file": str(tmp / "test.log"),
        "karaoke_vlc_port": 8080,
        "filler_vlc_port": 8081,
        "karaoke_vlc_password": "karaoke",
        "filler_vlc_password": "filler",
        "audio_devices": {"hdmiout": "HDMI Output", "headphones": "Headphones"},
        "default_audio_device": "hdmiout",
        "default_filler_track": "",
        "flask_port": flask_port,
        "external_catalog_db": str(tmp / "test_catalog.db"),
        "external_file_list": "",
        "external_media_mount": "",
    }

    app = create_app(config=config)
    app.config["TESTING"] = True

    server_thread = threading.Thread(
        target=lambda: app.run(
            host="127.0.0.1", port=flask_port, use_reloader=False
        ),
        daemon=True,
    )
    server_thread.start()

    # Wait for server to be ready
    import urllib.request
    for _ in range(30):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{flask_port}/status")
            break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("Flask test server failed to start")

    yield f"http://127.0.0.1:{flask_port}"

    # Server thread is daemon — dies with the test process


@pytest.fixture
def app_page(page, live_server):
    """Navigate to the app and wait for it to initialize."""
    page.goto(live_server)
    page.wait_for_load_state("networkidle")
    return page
