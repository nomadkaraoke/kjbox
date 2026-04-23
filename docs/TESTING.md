# Testing

## Test Types

### Unit Tests (`tests/unit/`)
Test pure functions and isolated logic with no external dependencies. Mock filesystem access where needed, use `tmp_path` for real filesystem tests.

### Integration Tests (`tests/integration/`)
Test Flask routes via the test client. These exercise the full request/response cycle but mock VLC and external services.

## Running Tests

```bash
cd kj-controller

# All tests
pytest

# Verbose with test names
pytest -v

# Unit tests only
pytest tests/unit/

# Integration tests only
pytest tests/integration/

# With coverage
pytest --cov --cov-report=term-missing

# HTML coverage report
pytest --cov --cov-report=html
# Open htmlcov/index.html
```

## Conventions

### File Naming
- Test files: `test_<module>.py`
- Test functions: `test_<function>_<scenario>()`
- Example: `test_sanitize_filename_part_removes_unsafe_chars()`

### Fixtures
Shared fixtures live in `tests/conftest.py`:
- `tmp_media_dir` - temp directory with `downloads/` and `media/` subdirs
- `mock_config` - test config dict with temp paths (includes catalog config)
- `catalog_db_path` - temporary path for a test catalog database
- `sample_file_list` - sample karaoke file list for catalog building
- `flask_app` - Flask app via `create_app(config=mock_config)` with VLC disabled
- `flask_test_client` - Flask test client for route testing

### Mocking Strategy
- **VLC (integration tests)**: Disabled via `create_app(config=...)` which sets `VLCManager(enabled=False)`
- **VLC (unit tests)**: Enabled with `VLCManager(config, enabled=True)`, mock `send_command` or `requests` to test logic without a real VLC binary
- **Filesystem**: Use `tmp_path` / `tmp_media_dir` fixtures for real file operations
- **Config**: Pass test config dict to `create_app(config=mock_config)`
- **Platform detection**: Mock `vlc.is_pi()` to return False (auto in tests via enabled=False)

## Coverage Targets

- Overall: 70%+
- Utility functions: 90%+
- Config loading: 80%+
- Media index: 90%+
- Routes: 70%+
- Catalog: 90%+
- ZIP playback: 90%+
- VLC logic (HTTP, state machine, orchestration): 70%+
- YouTube health: 90%+
- VLC subprocess launching: excluded (requires real `cvlc` binary)

## VLC Testing Strategy

VLC code is testable at three levels:

| Level | What to mock | What you're testing |
|-------|-------------|---------------------|
| **HTTP layer** | `vlc.requests.Session` | URL construction, encoding, error handling in `send_command` |
| **Orchestration** | `vm.send_command` (patch on instance) | State transitions in `play_video`, `monitor_karaoke`, `ensure_filler_stopped`, `fade_music` |
| **Process mgmt** | Mock process objects in `vm.processes` | Terminate/wait/kill logic in `restart_instances` |

Only `launch_instance` truly requires a VLC binary (it calls `subprocess.Popen` with `cvlc`). Everything else is testable through mocking.

## Push notifications (manual runbook)

Web Push is partially untestable in CI — service worker lifecycle and
real push delivery require a browser. Run this checklist on at least
one Android device and one iPhone before shipping push changes to prod.

### Setup

1. HTTPS is required for Web Push on mobile. Options:
   - Deploy to staging (if a staging hostname exists).
   - Run `cloudflared tunnel` locally forwarding to your dev server.
   - Use `ngrok http 80` to expose the dev server.
2. Enable the event token in the KJ UI (Requests panel) and toggle auto-approve OFF so you can observe the approve push.

### Android Chrome

- [ ] Open `/sing/?t=<token>` — submit a song.
- [ ] On confirmation, the 🔔 "Notify me when I'm up" button appears after ~2s. Tap → grant permission.
- [ ] Button swaps to "✓ Notifications on".
- [ ] `sqlite3 rotation.db "SELECT phone, endpoint FROM sing_push_subscriptions"` shows the row.
- [ ] In KJ UI, approve the request → phone receives a "You're in!" push.
- [ ] Move the entry to position 3 → phone receives a "You're up in 2" push.
- [ ] Move the entry to position 2 → "You're up NEXT" push.
- [ ] Mark Now Singing → "You're singing now" push.
- [ ] Tap the notification → page reopens/focuses at the singer's status.

### iPhone Safari

- [ ] Open `/sing/?t=<token>` — submit a song → on confirmation see the iOS install card (📱 iPhone? Get tapped when you're up.).
- [ ] Tap Share → Add to Home Screen → reopen from home screen.
- [ ] Submit another song → confirmation now shows the standard 🔔 button.
- [ ] Accept notifications → KJ UI moves entry → push arrives on phone.

### Desktop Chrome

- [ ] Open the sing URL in desktop Chrome — install as a desktop PWA (three-dot menu → Install).
- [ ] Submit a song, grant push permission.
- [ ] Push arrives while the PWA window is backgrounded or the browser closed (on macOS, confirm via Notification Center).

### Offline banner

- [ ] Submit a song while online — no banner visible.
- [ ] Toggle wifi off (or Chrome DevTools → Network → Offline) → banner appears at top within ~30s.
- [ ] Toggle back on → banner clears within one poll cycle.

### Dedup behaviour

- [ ] Two phones subscribed as the same first name ("Alex" + "Alex") → only the correct phone's entry triggers that device's push.
- [ ] Single phone with two songs in the queue → first song Done'd → ladder resets cleanly for the second entry (you'll see a fresh up_next when the second entry reaches position ≤2).

### Rejection

- [ ] Subscribe as a singer, submit a request, KJ rejects with a reason → phone receives a "The KJ needs a word" push.

### Rules page

- [ ] Open `/sing/rules` in a fresh browser tab (no token). All 5 rules render styled; "← Back" link works.
- [ ] On the submit confirmation screen, expand "🎤 House rules" — 5 short lines + "Read full rules →" link point to /sing/rules.

### What's playing now widget

- [ ] Landing page with no rotation entries → empty-state "Rotation hasn't started yet — you could be the first!".
- [ ] KJ adds an entry and marks it Now Singing → within 15s the widget shows "🎤 Now: {singer} — {song}".
- [ ] Second entry added → within 15s widget updates to include "Up next: {name}".
