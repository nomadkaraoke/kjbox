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
- `flask_port` - web server port (default: 5000)

## Running the App

```bash
python3 app.py
```

On non-Pi platforms, the app starts in **dev mode**:
- VLC is disabled (no playback)
- Web UI is served normally
- Media scanning and indexing work
- Download functionality works (if yt-dlp is installed)

Access the UI at `http://localhost:5000`.

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
  routes.py              # Flask Blueprint with all 15 route handlers
  config.example.json    # Example configuration
  requirements.txt       # Production dependencies
  requirements-dev.txt   # Test dependencies
  pyproject.toml         # Project metadata and tool config
  templates/
    index.html           # Web UI (vanilla JS)
  tests/
    conftest.py          # Shared fixtures (create_app with test config)
    unit/
      test_config.py     # Config loading and platform detection
      test_utils.py      # Utility function tests
      test_media.py      # MediaIndex class tests
      test_vlc.py        # VLCManager tests (disabled mode)
    integration/
      test_routes.py     # Flask route tests via test client
```
