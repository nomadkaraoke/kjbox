"""Shared test fixtures for kj-controller."""

import json
import os
import sys

import pytest

import sing
from tests.fixtures import ASCII_ENTRIES, KARAOKE_CATALOG_ENTRIES

# Ensure kj-controller directory is on sys.path
app_dir = os.path.join(os.path.dirname(__file__), '..')
if app_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(app_dir))


# --- Background threads must not outlive their test ------------------------
#
# Many tests patch a module global such as `routes.subprocess.run` or
# `routes.threading.Thread`. That patches the shared `subprocess` / `threading`
# module for EVERY thread, so a worker a previous test left running (rotation
# duration probe -> ffprobe, download worker, VLC verify) lands its call in this
# test's mock: `assert_called_once()` sees two calls, `call_args` holds the
# wrong argv. These failed 1-3 random tests per full run, each passing alone.
# So: after each test's teardown, wait (bounded) for the threads its body
# started, before the next test runs.
_THREAD_JOIN_BUDGET_S = 3.0


@pytest.fixture(autouse=True, scope="session")
def _short_idle_workers():
    # Session-wide, not monkeypatch: a per-test undo runs in teardown, BEFORE
    # the thread wait below, and puts the worker back on a 30 s idle wait.
    import rotation
    rotation.RotationManager._DURATION_IDLE_EXIT_S = 0.2


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    import threading
    item._threads_before = set(threading.enumerate())
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    # After fixture teardown, so fixtures that stop their own workers (e.g. the
    # AudioMonitor drain loop) do so first and this is only the fallback.
    yield
    import threading
    import time
    before = getattr(item, "_threads_before", None)
    if before is None:
        return
    # e2e live-server request threads live as long as the browser's keep-alive
    # connection; they never touch a test's mocks.
    started = [t for t in threading.enumerate()
               if t not in before and t is not threading.current_thread()
               and "process_request_thread" not in t.name]
    # Cancel every pending timer (push debounce, volume save) before any wait,
    # so none is still scheduled if a slow worker uses up the budget.
    for t in started:
        if isinstance(t, threading.Timer):
            t.cancel()
    start = time.monotonic()
    deadline = start + _THREAD_JOIN_BUDGET_S
    for t in started:
        remaining = deadline - time.monotonic()
        t0 = time.monotonic()
        if remaining > 0:
            t.join(remaining)
        tgt = getattr(t, "_target", None)
        key = getattr(tgt, "__qualname__", None) or type(t).__name__
        _thread_join_stats["by_kind"][key] = (
            _thread_join_stats["by_kind"].get(key, 0) + time.monotonic() - t0)
    if any(t.is_alive() for t in started):
        _thread_join_stats["timed_out"].append(item.nodeid)
    _thread_join_stats["waited_s"] += time.monotonic() - start


_thread_join_stats = {"waited_s": 0.0, "timed_out": [], "by_kind": {}}


def pytest_terminal_summary(terminalreporter):
    s = _thread_join_stats
    terminalreporter.write_line(
        f"background-thread wait: {s['waited_s']:.1f}s total; "
        f"{len(s['timed_out'])} test(s) left a thread running past "
        f"{_THREAD_JOIN_BUDGET_S:.0f}s")
    for k, v in sorted(s["by_kind"].items(), key=lambda kv: -kv[1])[:10]:
        terminalreporter.write_line(f"  waited {v:6.1f}s on {k}")
    for nodeid in s["timed_out"][:20]:
        terminalreporter.write_line(f"  thread still running after: {nodeid}")


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
        "media_db_path": str(tmp_media_dir / "media_library.db"),
        "filler_music_dir": str(tmp_media_dir),
        "log_file": str(tmp_media_dir / "test.log"),
        "karaoke_vlc_port": 8080,
        "filler_vlc_port": 8081,
        "karaoke_vlc_password": "karaoke",
        "filler_vlc_password": "filler",
        "audio_devices": {"hdmiout": "HDMI Output"},
        "default_audio_device": "hdmiout",
        "default_filler_track": "",
        "flask_port": 80,
        "external_catalog_db": str(tmp_media_dir / "test_catalog.db"),
        "song_id_db": str(tmp_media_dir / "song_id.db"),
        "search_log_db": str(tmp_media_dir / "search_log.db"),
        "external_file_list": "",
        "external_media_mount": "",
        "overlays_path": str(tmp_media_dir / "overlays.json"),
        "websockify_port": 6080,
        "vnc_target": "localhost:5900",
        "websockify_enabled": False,
        "tls_cert": "",
        "tls_key": "",
        "youtube_cookies_file": str(tmp_media_dir / "youtube_cookies.txt"),
        "rotation_db_path": ":memory:",
        "sing_rate_limit_per_ip": 5,
        "sing_rate_limit_window_s": 300,
        "sing_estimate_transition_s": 30,
        "sing_estimate_default_song_s": 240,
        "sing_estimate_min_spread_s": 120,
        # Feature tests exercise pitch shift + the original-vocals guide mix, so
        # enable audio processing here (production default is False — see
        # test_config_audio_processing_default). Disabled-path tests override it.
        "audio_processing_enabled": True,
    }


@pytest.fixture
def catalog_db_path(tmp_path):
    """Temporary path for a test catalog database."""
    return str(tmp_path / "test_catalog.db")


@pytest.fixture
def sample_file_list(tmp_path):
    """Create a sample file list with ASCII-only entries for basic tests."""
    file_list = tmp_path / "file_list.txt"
    file_list.write_text('\n'.join(ASCII_ENTRIES) + '\n')
    return str(file_list)


@pytest.fixture
def full_catalog_file_list(tmp_path):
    """Create a file list with the full real-world karaoke catalog fixtures.

    Includes ASCII, combining diacritics, non-decomposable Latin chars,
    Cyrillic, Greek, CJK/Kana, fullwidth, smart punctuation, and symbols.
    See tests/fixtures.py for the complete list and documentation.
    """
    file_list = tmp_path / "full_catalog.txt"
    file_list.write_text('\n'.join(KARAOKE_CATALOG_ENTRIES) + '\n')
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


# --- Shared sing-blueprint fixtures (used by integration tests) ----------

@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Each test starts with an empty rate-limit window."""
    import sing_api_v1
    for clear in (sing._rate_limit_state.clear, sing._validate_rate_limit_state.clear,
                  sing_api_v1.failed_code_limit.clear, sing_api_v1.failed_code_ip_limit.clear,
                  sing_api_v1.gen_verify_limit.clear, sing_api_v1.history_read_limit.clear,
                  sing_api_v1.history_claim_limit.clear):
        clear()
    yield
    for clear in (sing._rate_limit_state.clear, sing._validate_rate_limit_state.clear,
                  sing_api_v1.failed_code_limit.clear, sing_api_v1.failed_code_ip_limit.clear,
                  sing_api_v1.gen_verify_limit.clear, sing_api_v1.history_read_limit.clear,
                  sing_api_v1.history_claim_limit.clear):
        clear()


@pytest.fixture(autouse=True)
def _reset_divebar_search_cache():
    """Each test starts with an empty divebar CF search cache."""
    import divebar
    divebar.clear_search_cache()
    yield
    divebar.clear_search_cache()


@pytest.fixture
def sing_app(mock_config):
    from app import create_app
    app = create_app(config=mock_config)
    app.config["TESTING"] = True
    yield app
    app.catalog.close()


@pytest.fixture
def client(sing_app):
    with sing_app.test_client() as c:
        yield c


@pytest.fixture
def token(sing_app):
    return sing_app.sing_store.ensure_token()


# --- App-facing singer API v1 fixtures (tests/integration/v1_contract.py) --

@pytest.fixture
def gen_stub(sing_app, monkeypatch):
    """Stub gen's ``GET /api/users/me`` for the v1 token verifier only.

    Installs ``app.gen_verifier = GenTokenVerifier(stub, secret=app.app_identity_secret)``; ``app.gen_client``
    is left alone so make-it paths behave as in any other test (stub it in the
    test that needs it). ``users`` maps a session token to the user dict gen
    returns; an unknown token gets gen's 401. ``down = True`` simulates gen
    being unreachable (GenApiError status 0). ``calls`` records verified tokens.
    """
    from app_identity import GenTokenVerifier
    from gen_client import GenApiError

    class _GenStub:
        def __init__(self):
            self.users = {}
            self.down = False
            self.calls = []

        def get_me(self, session_token):
            self.calls.append(session_token)
            if self.down:
                raise GenApiError(0, "connection refused")
            user = self.users.get(session_token)
            if user is None:
                raise GenApiError(401, "Invalid session")
            return {"user": dict(user), "has_session": True}

    stub = _GenStub()
    verifier = GenTokenVerifier(stub, secret=sing_app.app_identity_secret)
    monkeypatch.setattr(sing_app, "gen_verifier", verifier, raising=False)
    return stub


@pytest.fixture
def app_token(gen_stub):
    """Factory: ``app_token(email=..., display_name=...)`` → a bearer token gen
    (stubbed) accepts for that user. Pass ``display_name=None`` for a user
    without one."""
    counter = iter(range(1, 10_000))

    def make(email="sam.singer@example.com", display_name="Sam Singer"):
        tok = f"gen-session-{next(counter)}-{email}"
        gen_stub.users[tok] = {
            "email": email, "display_name": display_name, "tenant_id": None,
        }
        return tok

    return make
