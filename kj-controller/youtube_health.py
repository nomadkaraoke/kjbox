"""YouTube health checks and cookie management."""

import os
import shutil
import subprocess
import tempfile


def get_youtube_status(config):
    """Return YouTube download engine status: yt-dlp, EJS, Deno, cookies."""
    status = {
        'ytdlp_version': _get_ytdlp_version(),
        'ejs_installed': False,
        'ejs_version': None,
        'deno_available': False,
        'deno_version': None,
        'cookies_present': False,
        'cookies_valid': False,
        'cookies_last_updated': None,
    }

    # EJS solver (yt-dlp plugin, auto-discovered)
    ejs_version = _get_ejs_version()
    if ejs_version:
        status['ejs_installed'] = True
        status['ejs_version'] = ejs_version

    # Deno runtime (required by EJS solver)
    deno_version = _get_deno_version()
    if deno_version:
        status['deno_available'] = True
        status['deno_version'] = deno_version

    # Cookie file
    cookies_file = config.get('youtube_cookies_file', '')
    if cookies_file and os.path.exists(cookies_file):
        status['cookies_present'] = True
        try:
            mtime = os.path.getmtime(cookies_file)
            status['cookies_last_updated'] = mtime
        except OSError:
            pass
        try:
            with open(cookies_file, 'r') as f:
                content = f.read()
            valid, _ = validate_cookies_format(content)
            status['cookies_valid'] = valid
        except OSError:
            pass

    return status


def validate_cookies_format(content):
    """Validate Netscape cookie file format.

    Returns (bool, message) — True if valid, False with reason if not.
    Checks: has tab-separated lines with 7 fields, includes YouTube/Google domains.
    """
    if not content or not content.strip():
        return False, 'Cookie file is empty'

    lines = content.strip().splitlines()
    cookie_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip comments and blank lines
        if not stripped or stripped.startswith('#'):
            continue
        cookie_lines.append(stripped)

    if not cookie_lines:
        return False, 'No cookie entries found (only comments/blank lines)'

    # Check format: each cookie line must have 7 tab-separated fields
    malformed = 0
    for line in cookie_lines:
        fields = line.split('\t')
        if len(fields) != 7:
            malformed += 1

    if malformed == len(cookie_lines):
        return False, 'No valid Netscape cookie lines found (expected 7 tab-separated fields per line)'

    if malformed > 0:
        return False, f'{malformed} of {len(cookie_lines)} cookie lines are malformed'

    # Check for YouTube/Google domains
    youtube_domains = {'.youtube.com', '.google.com', '.google.co.uk',
                       'youtube.com', 'google.com', '.googlevideo.com'}
    has_youtube = False
    for line in cookie_lines:
        domain = line.split('\t')[0].strip().lower()
        if any(domain == yd or domain.endswith(yd) for yd in youtube_domains):
            has_youtube = True
            break

    if not has_youtube:
        return False, 'No YouTube/Google domain cookies found'

    return True, f'{len(cookie_lines)} cookies loaded ({len(cookie_lines) - malformed} valid)'


def write_cookies_file(content, path):
    """Write cookie content to file atomically with restrictive permissions.

    Returns (bool, message).
    """
    try:
        dir_name = os.path.dirname(os.path.abspath(path))
        os.makedirs(dir_name, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.txt')
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return True, 'Cookies saved successfully'
    except Exception as e:
        return False, f'Failed to write cookies: {e}'


def _get_ytdlp_version():
    """Get yt-dlp version string."""
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except Exception:
        return None


def _get_ejs_version():
    """Get yt-dlp-ejs plugin version if installed."""
    try:
        import yt_dlp_plugins
        # yt-dlp-ejs registers as a plugin; check via importlib
        import importlib.metadata
        return importlib.metadata.version('yt-dlp-ejs')
    except Exception:
        pass
    # Fallback: check if the package is installed via pip
    try:
        import importlib.metadata
        return importlib.metadata.version('yt-dlp-ejs')
    except Exception:
        return None


def _get_deno_version():
    """Get Deno version if available on PATH."""
    deno_bin = shutil.which('deno')
    if not deno_bin:
        return None
    try:
        result = subprocess.run(
            [deno_bin, '--version'], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            # First line: "deno 1.40.0 ..."
            first_line = result.stdout.strip().splitlines()[0]
            return first_line.replace('deno ', '').strip()
    except Exception:
        pass
    return None
