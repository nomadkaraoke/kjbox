"""Unit tests for youtube_health module."""

import os
import stat
import subprocess
from unittest.mock import MagicMock, patch

from youtube_health import (
    _get_deno_version,
    _get_ejs_version,
    _get_ytdlp_latest,
    _get_ytdlp_version,
    _normalize_version,
    get_youtube_status,
    upgrade_ytdlp,
    validate_cookies_format,
    write_cookies_file,
)

# --- Netscape cookie format fixtures ---

VALID_COOKIES = """\
# Netscape HTTP Cookie File
.youtube.com\tTRUE\t/\tTRUE\t1893456000\tLOGIN_INFO\tAFmmF2swRA
.youtube.com\tTRUE\t/\tFALSE\t1893456000\tSID\tabcdef123456
.google.com\tTRUE\t/\tTRUE\t1893456000\tNID\txyz789
"""

VALID_COOKIES_MINIMAL = (
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc123\n"
)

NO_YOUTUBE_COOKIES = (
    ".example.com\tTRUE\t/\tFALSE\t1893456000\tSESSION\tabc\n"
)

MALFORMED_COOKIES = "not a cookie file\njust random text\n"

PARTIAL_MALFORMED = (
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc\n"
    "bad line\n"
)


# --- validate_cookies_format ---

class TestValidateCookiesFormat:
    def test_valid_cookies(self):
        valid, msg = validate_cookies_format(VALID_COOKIES)
        assert valid is True
        assert 'cookies loaded' in msg

    def test_valid_minimal(self):
        valid, msg = validate_cookies_format(VALID_COOKIES_MINIMAL)
        assert valid is True

    def test_empty_string(self):
        valid, msg = validate_cookies_format('')
        assert valid is False
        assert 'empty' in msg.lower()

    def test_none(self):
        valid, msg = validate_cookies_format(None)
        assert valid is False

    def test_only_comments(self):
        valid, msg = validate_cookies_format('# comment\n# another\n')
        assert valid is False
        assert 'only comments' in msg.lower() or 'No cookie entries' in msg

    def test_no_youtube_domains(self):
        valid, msg = validate_cookies_format(NO_YOUTUBE_COOKIES)
        assert valid is False
        assert 'YouTube' in msg or 'Google' in msg

    def test_all_malformed(self):
        valid, msg = validate_cookies_format(MALFORMED_COOKIES)
        assert valid is False
        assert 'tab-separated' in msg.lower() or 'malformed' in msg.lower()

    def test_partial_malformed(self):
        valid, msg = validate_cookies_format(PARTIAL_MALFORMED)
        assert valid is False
        assert 'malformed' in msg.lower()

    def test_whitespace_only(self):
        valid, msg = validate_cookies_format('   \n  \n  ')
        assert valid is False


# --- write_cookies_file ---

class TestWriteCookiesFile:
    def test_writes_file(self, tmp_path):
        path = str(tmp_path / 'cookies.txt')
        ok, msg = write_cookies_file(VALID_COOKIES, path)
        assert ok is True
        assert os.path.exists(path)
        with open(path) as f:
            assert f.read() == VALID_COOKIES

    def test_permissions(self, tmp_path):
        path = str(tmp_path / 'cookies.txt')
        write_cookies_file(VALID_COOKIES, path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600

    def test_atomic_overwrite(self, tmp_path):
        path = str(tmp_path / 'cookies.txt')
        write_cookies_file('old content', path)
        write_cookies_file('new content', path)
        with open(path) as f:
            assert f.read() == 'new content'

    def test_creates_parent_dir(self, tmp_path):
        path = str(tmp_path / 'subdir' / 'cookies.txt')
        ok, msg = write_cookies_file(VALID_COOKIES, path)
        assert ok is True
        assert os.path.exists(path)

    def test_invalid_path_returns_error(self):
        ok, msg = write_cookies_file('data', '/proc/nonexistent/impossible/path.txt')
        assert ok is False
        assert 'Failed' in msg


# --- get_youtube_status ---

class TestGetYoutubeStatus:
    def test_basic_structure(self):
        status = get_youtube_status({})
        assert 'ytdlp_version' in status
        assert 'ejs_installed' in status
        assert 'deno_available' in status
        assert 'cookies_present' in status
        assert 'cookies_valid' in status
        assert 'cookies_last_updated' in status

    def test_no_cookies_file(self):
        status = get_youtube_status({'youtube_cookies_file': '/tmp/nonexistent_cookies_12345.txt'})
        assert status['cookies_present'] is False
        assert status['cookies_valid'] is False

    def test_with_cookies_file(self, tmp_path):
        cookie_file = tmp_path / 'cookies.txt'
        cookie_file.write_text(VALID_COOKIES)
        status = get_youtube_status({'youtube_cookies_file': str(cookie_file)})
        assert status['cookies_present'] is True
        assert status['cookies_valid'] is True
        assert status['cookies_last_updated'] is not None

    def test_with_invalid_cookies_file(self, tmp_path):
        cookie_file = tmp_path / 'cookies.txt'
        cookie_file.write_text(MALFORMED_COOKIES)
        status = get_youtube_status({'youtube_cookies_file': str(cookie_file)})
        assert status['cookies_present'] is True
        assert status['cookies_valid'] is False

    @patch('youtube_health._get_ytdlp_version', return_value='2025.01.15')
    def test_ytdlp_version(self, mock_ver):
        status = get_youtube_status({})
        assert status['ytdlp_version'] == '2025.01.15'

    @patch('youtube_health._get_ejs_version', return_value='1.0.0')
    def test_ejs_installed(self, mock_ver):
        status = get_youtube_status({})
        assert status['ejs_installed'] is True
        assert status['ejs_version'] == '1.0.0'

    @patch('youtube_health._get_deno_version', return_value='2.1.4')
    def test_deno_available(self, mock_ver):
        status = get_youtube_status({})
        assert status['deno_available'] is True
        assert status['deno_version'] == '2.1.4'

    def test_unreadable_cookies_file(self, tmp_path):
        """Cookie file exists but can't be read (e.g., permission denied)."""
        cookie_file = tmp_path / 'cookies.txt'
        cookie_file.write_text(VALID_COOKIES)
        cookie_file.chmod(0o000)
        try:
            status = get_youtube_status({'youtube_cookies_file': str(cookie_file)})
            assert status['cookies_present'] is True
            # Can't read the file, so valid stays False
            assert status['cookies_valid'] is False
        finally:
            cookie_file.chmod(0o644)

    def test_empty_youtube_cookies_file_config(self):
        """Empty string for youtube_cookies_file config."""
        status = get_youtube_status({'youtube_cookies_file': ''})
        assert status['cookies_present'] is False


# --- Private helper functions ---

class TestGetYtdlpVersion:
    def test_returns_version_string(self):
        """yt-dlp is installed in test env, should return a version."""
        version = _get_ytdlp_version()
        assert version is not None
        assert isinstance(version, str)

    def test_returns_none_on_import_error(self):
        """Returns None when yt-dlp import fails."""
        with patch.dict('sys.modules', {'yt_dlp': None, 'yt_dlp.version': None}):
            # Force re-import to fail by removing cached module
            import sys
            saved = sys.modules.pop('yt_dlp', None)
            saved_ver = sys.modules.pop('yt_dlp.version', None)
            sys.modules['yt_dlp'] = None  # makes `import yt_dlp` raise ImportError
            try:
                result = _get_ytdlp_version()
                assert result is None
            finally:
                if saved is not None:
                    sys.modules['yt_dlp'] = saved
                if saved_ver is not None:
                    sys.modules['yt_dlp.version'] = saved_ver


class TestGetEjsVersion:
    def test_returns_none_when_not_installed(self):
        """EJS is likely not installed in test env."""
        version = _get_ejs_version()
        # Might be None (not installed) or a string (installed)
        assert version is None or isinstance(version, str)

    @patch('importlib.metadata.version', return_value='1.2.3')
    def test_returns_version_when_installed(self, mock_ver):
        version = _get_ejs_version()
        assert version == '1.2.3'


class TestGetDenoVersion:
    @patch('shutil.which', return_value=None)
    def test_returns_none_when_not_on_path(self, mock_which):
        version = _get_deno_version()
        assert version is None

    @patch('subprocess.run')
    @patch('shutil.which', return_value='/usr/bin/deno')
    def test_returns_version_string(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='deno 2.1.4 (release, aarch64-apple-darwin)\nv8 13.0.245.12-rusty\ntypescript 5.6.2\n',
        )
        version = _get_deno_version()
        assert version == '2.1.4 (release, aarch64-apple-darwin)'

    @patch('subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='deno', timeout=5))
    @patch('shutil.which', return_value='/usr/bin/deno')
    def test_returns_none_on_timeout(self, mock_which, mock_run):
        version = _get_deno_version()
        assert version is None

    @patch('subprocess.run')
    @patch('shutil.which', return_value='/usr/bin/deno')
    def test_returns_none_on_nonzero_exit(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout='')
        version = _get_deno_version()
        assert version is None


# --- _normalize_version ---

class TestNormalizeVersion:
    def test_strips_leading_zeros(self):
        assert _normalize_version('2026.03.03') == '2026.3.3'

    def test_already_normalized(self):
        assert _normalize_version('2026.3.3') == '2026.3.3'

    def test_mixed_zeros(self):
        assert _normalize_version('2026.01.15') == '2026.1.15'

    def test_none_passthrough(self):
        assert _normalize_version(None) is None

    def test_single_segment(self):
        assert _normalize_version('2026') == '2026'


# --- _get_ytdlp_latest ---

class TestGetYtdlpLatest:
    @patch('youtube_health.urllib.request.urlopen')
    def test_returns_version_from_pypi(self, mock_urlopen):
        import youtube_health
        youtube_health._ytdlp_latest_cache['checked_at'] = 0  # force fetch
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"info": {"version": "2026.3.3"}}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp
        version = _get_ytdlp_latest()
        assert version == '2026.3.3'

    @patch('youtube_health.urllib.request.urlopen', side_effect=Exception('network error'))
    def test_returns_cached_on_failure(self, mock_urlopen):
        import youtube_health
        youtube_health._ytdlp_latest_cache['version'] = '2026.2.1'
        youtube_health._ytdlp_latest_cache['checked_at'] = 0
        version = _get_ytdlp_latest()
        assert version == '2026.2.1'


# --- get_youtube_status ytdlp_outdated ---

class TestYtdlpOutdated:
    @patch('youtube_health._get_ytdlp_latest', return_value='2026.3.3')
    @patch('youtube_health._get_ytdlp_version', return_value='2026.03.03')
    def test_not_outdated_when_versions_match_after_normalize(self, mock_ver, mock_latest):
        status = get_youtube_status({})
        assert status['ytdlp_outdated'] is False

    @patch('youtube_health._get_ytdlp_latest', return_value='2026.4.1')
    @patch('youtube_health._get_ytdlp_version', return_value='2026.03.03')
    def test_outdated_when_behind(self, mock_ver, mock_latest):
        status = get_youtube_status({})
        assert status['ytdlp_outdated'] is True

    @patch('youtube_health._get_ytdlp_latest', return_value=None)
    @patch('youtube_health._get_ytdlp_version', return_value='2026.03.03')
    def test_not_outdated_when_latest_unknown(self, mock_ver, mock_latest):
        status = get_youtube_status({})
        assert status['ytdlp_outdated'] is False

    @patch('youtube_health._get_ytdlp_latest', return_value='2026.3.3')
    @patch('youtube_health._get_ytdlp_version', return_value=None)
    def test_not_outdated_when_not_installed(self, mock_ver, mock_latest):
        status = get_youtube_status({})
        assert status['ytdlp_outdated'] is False


# --- upgrade_ytdlp ---

class TestUpgradeYtdlp:
    @patch('youtube_health._get_ytdlp_version_from_pip', return_value='2026.3.3')
    @patch('subprocess.run')
    def test_success(self, mock_run, mock_ver):
        mock_run.return_value = MagicMock(returncode=0, stdout='', stderr='')
        ok, msg = upgrade_ytdlp()
        assert ok is True
        assert '2026.3.3' in msg

    @patch('subprocess.run')
    def test_pip_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout='', stderr='Permission denied')
        ok, msg = upgrade_ytdlp()
        assert ok is False
        assert 'failed' in msg.lower()

    @patch('subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='pip', timeout=120))
    def test_timeout(self, mock_run):
        ok, msg = upgrade_ytdlp()
        assert ok is False
        assert 'timed out' in msg.lower()
