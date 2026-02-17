# KJ Remote Controller

## Overview

Web-based karaoke show management app for NomadPi. Controls dual VLC instances for video playback and filler music, with YouTube downloading via yt-dlp.

For full architecture details, see [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md).

## Features

- **One-click playback** of karaoke videos via VLC
- **YouTube downloading** with yt-dlp for building a song library
- **Filler music** with intelligent crossfading between songs
- **Independent volume controls** for karaoke and filler
- **Live status updates** with current track, timing, and player state
- **Remote control** from any device on the local network
- **Audio device switching** between HDMI and USB mixer

## Quick Start

```bash
cd kj-controller
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
# Access at http://localhost
```

On non-Pi platforms, the app runs in **dev mode** (VLC disabled, web UI and media management work normally). See [docs/DEVELOPMENT.md](../docs/DEVELOPMENT.md) for details.

## Configuration

Copy and customize the config file:

```bash
cp config.example.json config.json
```

Key settings in `config.json`:
- `download_folder` - where yt-dlp saves videos
- `media_folders` - list of directories to scan for media
- `filler_music_dir` - directory containing filler music files
- `flask_port` - web server port (default: 80)

## Testing

```bash
pip install -r requirements-dev.txt
pytest                              # run all 115 tests
pytest --cov --cov-report=term      # with coverage (88%)
```

See [docs/TESTING.md](../docs/TESTING.md) for conventions and strategy.

## Project Structure

```
app.py              # App factory (create_app) + entry point
config.py           # Constants, is_pi(), load/save config
utils.py            # Logging and filename utilities
media.py            # MediaIndex class (scan, validate, download, delete)
vlc.py              # VLCManager class (dual VLC instance control)
routes.py           # Flask Blueprint with 15 REST API handlers
templates/
  index.html        # Web UI (vanilla JS, no build step)
tests/
  conftest.py       # Shared fixtures
  unit/             # Pure function and class tests
  integration/      # Flask route tests via test client
```
