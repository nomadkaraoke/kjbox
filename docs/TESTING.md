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
- `mock_config` - test config dict with temp paths
- `flask_app` - Flask app via `create_app(config=mock_config)` with VLC disabled
- `flask_test_client` - Flask test client for route testing

### Mocking Strategy
- **VLC**: Disabled via `create_app(config=...)` which sets `VLCManager(enabled=False)`
- **Filesystem**: Use `tmp_path` / `tmp_media_dir` fixtures for real file operations
- **Config**: Pass test config dict to `create_app(config=mock_config)`
- **Platform detection**: Mock `vlc.is_pi()` to return False (auto in tests via enabled=False)

## Coverage Targets

- Overall: 70%+ (focusing on testable pure functions and route handlers)
- Utility functions: 90%+
- Config loading: 80%+
- Media validation: 90%+
- VLC/subprocess code: excluded (requires real VLC)
