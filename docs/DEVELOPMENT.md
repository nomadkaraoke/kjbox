# Development

## Prerequisites

- Python 3.11+
- pip
- VLC (optional - only needed on the Pi for actual playback)

## Local Setup

```bash
cd kj-controller
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt  # for testing
```

## Configuration

Copy the example config and customize:

```bash
cp config.example.json config.json
```

Key settings in `config.json`:
- `download_folder` - where yt-dlp saves videos
- `media_folders` - list of directories to scan for media
- `filler_music_dir` - directory containing filler music files
- `flask_port` - web server port (default: 80)
- `external_file_list` - path to text file listing external media (one path per line)
- `external_media_mount` - mount point for external media drive (e.g. `/mnt/Nomad4TBOne`)
- `websockify_port` - WebSocket proxy port for VNC preview (default: 6080)
- `vnc_target` - RealVNC host:port to proxy to (default: `localhost:5900`)
- `websockify_enabled` - enable/disable websockify subprocess (default: true, Pi-only)

## Running the App

```bash
python3 app.py
```

On non-Pi platforms, the app starts in **dev mode**:
- VLC is disabled (no playback)
- websockify is not started (VNC preview unavailable)
- Web UI is served normally
- Media scanning and indexing work
- Download functionality works (if yt-dlp is installed)

Access the UI at `http://localhost`.

## Dev Mode Behavior

The app detects the platform via `is_pi()` (checks for `/boot/dietpi.txt`). When not on the Pi:
- `VLCManager.enabled = False`
- All VLC commands are no-ops
- `/play` returns 503
- Media listing, downloading, and deletion all work normally

This lets you develop and test the web UI and media management without VLC installed.

## Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run only unit tests
pytest tests/unit/

# Run only integration tests
pytest tests/integration/

# Run with coverage report
pytest --cov --cov-report=term-missing

# Run a specific test file
pytest tests/unit/test_utils.py
```

## Project Structure

```
kj-controller/
  app.py                 # App factory (create_app) + entry point (start_app)
  config.py              # Constants, is_pi(), load/save config
  utils.py               # log_message(), filename utilities
  media.py               # MediaIndex class (scan, validate, download, delete)
  vlc.py                 # VLCManager class (launch, command, fade, play)
  catalog.py             # ExternalCatalog class (SQLite FTS5 search)
  zip_playback.py        # ZipPlayback class (CDG+MP3 ZIP extraction)
  routes.py              # Flask Blueprint with all 18 route handlers
  config.example.json    # Example configuration
  requirements.txt       # Production dependencies
  requirements-dev.txt   # Test dependencies
  pyproject.toml         # Project metadata and tool config
  static/
    style.css            # Extracted CSS (Nomad branding)
    app.js               # Extracted JS (controls, status polling)
    novnc/               # noVNC v1.6.0 vendored ES6 library (~56 files)
      core/rfb.js        # Main RFB client (imported by index.html)
      vendor/pako/       # Compression library used by noVNC
  templates/
    index.html           # Web UI (vanilla JS + noVNC module)
  tests/
    conftest.py          # Shared fixtures (create_app with test config)
    unit/
      test_config.py     # Config loading and platform detection
      test_utils.py      # Utility function tests
      test_media.py      # MediaIndex class tests
      test_vlc.py        # VLCManager tests (disabled mode)
      test_catalog.py    # ExternalCatalog + filename parser tests
      test_zip_playback.py # ZipPlayback extraction tests
    integration/
      test_routes.py     # Flask route tests via test client
      test_search_routes.py # Search, catalog, ZIP playback route tests
```
