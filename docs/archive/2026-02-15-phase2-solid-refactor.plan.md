# Plan: Phase 2 - SOLID Refactor of app.py

**Created:** 2026-02-15
**Branch:** (create via `/start phase2-refactor`)
**Status:** Draft

## Overview

Refactor the 963-line `kj-controller/app.py` monolith into focused modules with an app factory pattern. This eliminates 12 module-level globals, enables proper test isolation, and aligns with sibling project conventions (karaoke-decide uses app factory).

**Non-goals:** No new features, no API changes, no frontend changes. The REST API contract stays identical.

## Requirements

- [ ] All 15 REST endpoints behave identically (same request/response format)
- [ ] `python app.py` still works as the entry point
- [ ] All existing 23 Phase 1 tests pass (updated for new imports)
- [ ] New tests for extracted classes bring coverage to 50%+
- [ ] VLC/playback behavior unchanged on Pi
- [ ] No new dependencies added

## Current Problems

1. **12 module-level globals** make tests fragile (require `importlib.reload` between tests)
2. **Single 963-line file** makes navigation and code review difficult
3. **No dependency injection** - functions reach into globals for config, state, VLC
4. **Routes tightly coupled** to global functions - can't test routes without importing all VLC/subprocess code
5. **Config loaded as side effect** - `load_config()` calls `log_message()` which reads from `app_config` global

## Technical Approach

### Module Structure (after refactor)

```
kj-controller/
  app.py              # create_app() factory + __main__ entry point (~60 lines)
  config.py           # load_config(), save_config_value(), is_pi(), constants (~80 lines)
  utils.py            # sanitize_filename_part(), parse_youtube_filename(), log_message() (~40 lines)
  media.py            # MediaIndex class (scan, save, load, validate, delete, download) (~250 lines)
  vlc.py              # VLCManager class (launch, command, fade, play, restart) (~250 lines)
  routes.py           # Flask Blueprint with all 15 route handlers (~280 lines)
```

### State Ownership

| Current Global | New Owner | Access Pattern |
|---------------|-----------|----------------|
| `app_config` | `app.kj_config` (dict on Flask app) | `current_app.kj_config` in routes |
| `media_index` | `MediaIndex` instance on `app.media` | `current_app.media` in routes |
| `vlc_processes` | `VLCManager.processes` | `current_app.vlc` in routes |
| `current_playing_path` | `VLCManager.current_playing_path` | `current_app.vlc` |
| `current_filler_track` | `VLCManager.current_filler_track` | `current_app.vlc` |
| `filler_music_target_volume` | `VLCManager.filler_volume` | `current_app.vlc` |
| `karaoke_music_target_volume` | `VLCManager.karaoke_volume` | `current_app.vlc` |
| `karaoke_player_is_active` | `VLCManager.karaoke_active` | `current_app.vlc` |
| `last_seek_time` | `VLCManager.last_seek_time` | `current_app.vlc` |
| `audio_error` | `VLCManager.audio_error` | `current_app.vlc` |
| `current_audio_device` | `VLCManager.audio_device` | `current_app.vlc` |
| `vlc_enabled` | `VLCManager.enabled` | `current_app.vlc` |

### DI Pattern

Standard Flask: store service objects on the app instance, access via `current_app` in routes.

```python
# app.py
def create_app(config=None):
    flask_app = Flask(__name__)
    cfg = config or load_config()
    flask_app.kj_config = cfg
    flask_app.media = MediaIndex(cfg)
    flask_app.vlc = VLCManager(cfg)
    flask_app.register_blueprint(routes_bp)
    return flask_app

# routes.py
from flask import current_app, Blueprint
routes_bp = Blueprint('routes', __name__)

@routes_bp.route('/media')
def list_media():
    return jsonify(current_app.media.list_items())
```

## Implementation Steps

Each step ends with `pytest -v` passing. This ordering minimizes risk by extracting leaf dependencies first.

### Step 1: Extract `config.py` (~80 lines)
- [ ] Move `MEDIA_EXTENSIONS`, `APP_DIR`, `CONFIG_FILE` constants
- [ ] Move `is_pi()` function
- [ ] Move `load_config()` function (decouple from `log_message` - use `print()` fallback during config load)
- [ ] Move `save_config_value()` function
- [ ] Update `app.py` imports: `from config import ...`
- [ ] Update `test_config.py` to import from `config` module directly
- [ ] **Checkpoint:** `pytest -v` passes

### Step 2: Extract `utils.py` (~40 lines)
- [ ] Move `log_message()` function (takes config dict as optional param instead of reading global)
- [ ] Move `sanitize_filename_part()` function
- [ ] Move `parse_youtube_filename()` function
- [ ] Update `app.py` imports
- [ ] Update `test_utils.py` to import from `utils` module directly
- [ ] **Checkpoint:** `pytest -v` passes

### Step 3: Extract `media.py` - MediaIndex class (~250 lines)
- [ ] Create `MediaIndex` class with `__init__(self, config)`:
  - `self.config = config`
  - `self.index = {}` (replaces global `media_index`)
- [ ] Move as methods: `scan_media_folders()`, `save_media_index()`, `load_media_index_file()`, `load_media_index()`, `validate_media_path()`, `is_in_download_folder()`, `download_video()`
- [ ] Each method uses `self.config` instead of global `app_config`
- [ ] Each method uses `self.index` instead of global `media_index`
- [ ] `list_items()` method for the /media route's data formatting
- [ ] `delete_file()` method encapsulating delete + sidecar cleanup + index removal
- [ ] Update `app.py`: replace global media functions with `MediaIndex` instance
- [ ] Update `test_media.py` to use `MediaIndex` class directly
- [ ] **Checkpoint:** `pytest -v` passes

### Step 4: Extract `vlc.py` - VLCManager class (~250 lines)
- [ ] Create `VLCManager` class with `__init__(self, config)`:
  - All former VLC/playback globals as instance attributes
  - `self.enabled` set based on `is_pi()` or explicit override
- [ ] Move as methods: `launch_instance()`, `send_command()`, `fade_music()`, `fade_in_filler()`, `fade_out_filler()`, `ensure_filler_stopped()`, `play_video()`, `restart_instances()`, `monitor_karaoke()`
- [ ] `play_video()` takes a `MediaIndex` reference (or callback) for display name lookup
- [ ] Update `app.py`: replace global VLC functions with `VLCManager` instance
- [ ] **Checkpoint:** `pytest -v` passes

### Step 5: Extract `routes.py` - Flask Blueprint (~280 lines)
- [ ] Create `routes_bp = Blueprint('routes', __name__)`
- [ ] Move all 15 route handlers, converting global access to `current_app.vlc`, `current_app.media`, `current_app.kj_config`
- [ ] Routes import only from Flask and standard lib (no direct dependency on vlc.py/media.py internals)
- [ ] Update `app.py` to register blueprint
- [ ] **Checkpoint:** `pytest -v` passes

### Step 6: Create app factory in `app.py` (~60 lines)
- [ ] Create `create_app(config=None)` function:
  1. Load config (use provided or `load_config()`)
  2. Create Flask app
  3. Store `kj_config` on app
  4. Create and store `MediaIndex` on `app.media`
  5. Create and store `VLCManager` on `app.vlc`
  6. Load media index via `app.media.load()`
  7. Register blueprint
  8. Return app
- [ ] `start_app()` becomes: call `create_app()`, then Pi-specific VLC launch, then `app.run()`
- [ ] Keep `if __name__ == '__main__': start_app()` entry point
- [ ] **Checkpoint:** `pytest -v` passes

### Step 7: Update test fixtures
- [ ] Update `conftest.py`:
  - `app_module` fixture → replaced by direct imports from `config`, `utils`, `media`, `vlc`
  - `configured_app` fixture → uses `create_app(config=mock_config)`
  - `flask_test_client` → uses `create_app(config=mock_config).test_client()`
  - Remove `importlib.reload` hack (no longer needed - no global state to reset)
- [ ] Update all test files for new import paths
- [ ] **Checkpoint:** `pytest -v` passes

### Step 8: Add tests for new classes
- [ ] `test_media.py`: Add MediaIndex tests:
  - `test_scan_empty_folder` - scan with no media files
  - `test_scan_finds_media_files` - create files, verify they appear in index
  - `test_scan_preserves_metadata` - existing index metadata survives rescan
  - `test_list_items_format` - verify list_items() output shape
  - `test_delete_removes_file_and_sidecars` - delete with sidecar cleanup
- [ ] `test_vlc.py`: Add VLCManager tests (all with VLC disabled):
  - `test_vlc_disabled_by_default` - non-Pi gets enabled=False
  - `test_send_command_noop_when_disabled` - returns None
  - `test_play_video_noop_when_disabled` - logs but doesn't crash
  - `test_initial_state` - verify default attribute values
- [ ] `test_routes.py`: Add Flask route integration tests:
  - `test_index_returns_html` - GET / returns 200
  - `test_media_list_empty` - GET /media returns []
  - `test_status_without_vlc` - GET /status returns stopped state
  - `test_play_requires_file_path` - POST /play without file_path returns 400
  - `test_download_requires_url` - POST /download without url returns 400
  - `test_delete_rejects_outside_download_folder` - returns 403
- [ ] Target: 35-40 total tests, 50%+ coverage
- [ ] **Checkpoint:** `pytest --cov --cov-report=term-missing` shows 50%+

### Step 9: Update docs
- [ ] Update `docs/ARCHITECTURE.md` module structure table for new files
- [ ] Update `docs/DEVELOPMENT.md` project structure listing
- [ ] **Checkpoint:** All docs reflect new structure

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `kj-controller/config.py` | Create | Constants, is_pi(), load_config(), save_config_value() |
| `kj-controller/utils.py` | Create | log_message(), sanitize_filename_part(), parse_youtube_filename() |
| `kj-controller/media.py` | Create | MediaIndex class (scan, validate, download, delete) |
| `kj-controller/vlc.py` | Create | VLCManager class (launch, command, fade, play, restart) |
| `kj-controller/routes.py` | Create | Flask Blueprint with all 15 route handlers |
| `kj-controller/app.py` | Rewrite | create_app() factory + start_app() + __main__ (~60 lines) |
| `kj-controller/tests/conftest.py` | Modify | New fixtures using create_app() |
| `kj-controller/tests/unit/test_config.py` | Modify | Import from config module |
| `kj-controller/tests/unit/test_utils.py` | Modify | Import from utils module |
| `kj-controller/tests/unit/test_media.py` | Modify | Test MediaIndex class |
| `kj-controller/tests/unit/test_vlc.py` | Create | VLCManager tests (disabled mode) |
| `kj-controller/tests/integration/test_routes.py` | Create | Flask route tests via test client |
| `docs/ARCHITECTURE.md` | Modify | Update module structure table |
| `docs/DEVELOPMENT.md` | Modify | Update project structure |

## Testing Strategy

- **Unit tests** for: config loading, utility functions, MediaIndex methods, VLCManager (disabled mode)
- **Integration tests** for: Flask routes via test client (VLC always disabled)
- **Manual testing:** `python app.py` on dev machine (verify web UI loads, media scan works, all routes respond)
- **Pi testing:** Deploy and verify VLC playback still works (post-merge)

## Key Design Decisions

### Why app factory over direct instantiation?
karaoke-decide uses it and it's the Flask-recommended pattern. Main benefit: each test gets a fresh app instance without `importlib.reload` hacks. The `configured_app` fixture becomes `create_app(config=test_config)`.

### Why classes for MediaIndex and VLCManager?
These are stateful services. MediaIndex holds the index dict. VLCManager holds process handles, volume levels, playback state. Classes are the natural way to group state + behavior. The alternative (passing state dicts around) would be more complex.

### Why keep it flat (no src/ or packages)?
This deploys to a Pi with `pip install -r requirements.txt` and `python app.py`. No package install step. Flat module structure keeps deploy simple and matches the current approach. A `services/` subdirectory would add an unnecessary layer for 5 modules.

### Why MediaIndex.download_video() instead of separate downloader module?
`download_video()` is tightly coupled to the media index (it creates entries, calls save). Extracting it would require passing the index instance anyway. Keeping it on MediaIndex is simpler.

## Open Questions

None - this is a straightforward structural refactor with well-defined boundaries.

## Rollback Plan

If the refactor introduces issues on the Pi:
1. `git revert <merge-commit>` to restore monolithic app.py
2. All changes are in a feature branch - main is untouched until merge
