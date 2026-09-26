"""Start the night-capture sidecar when the KJ clicks "New Rotation".

The sidecar (``scripts/night_capture.py``) runs as the transient systemd unit
``kj-night-capture`` — separate from kj-controller, so it survives app restarts /
auto-deploys — and systemd stops it after ``RuntimeMaxSec`` (12h by default). The
SIGTERM makes the sidecar take its final snapshot. See docs/NIGHT-RECORDING.md.
"""

import getpass
import os
import shutil
import subprocess
import sys
import threading

from action_recorder import night_date

UNIT = 'kj-night-capture'
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts', 'night_capture.py')


def _python():
    return '/usr/bin/python3' if os.path.exists('/usr/bin/python3') else sys.executable


def build_command(out_dir, max_hours=12, user=None):
    user = user or getpass.getuser()
    return [
        'sudo', '-n', 'systemd-run', f'--unit={UNIT}', f'--uid={user}', f'--gid={user}',
        '--property=Nice=10',
        f'--property=RuntimeMaxSec={int(max_hours * 3600)}',
        '--property=SupplementaryGroups=systemd-journal',
        _python(), SCRIPT, '--out', out_dir,
    ]


def start_night_capture(cfg, run=subprocess.run, now=None):
    """Start the sidecar unless it's already recording. Returns a status dict; never raises."""
    try:
        if not shutil.which('systemd-run'):
            return {'status': 'unsupported'}
        active = run(['systemctl', 'is-active', '--quiet', UNIT], timeout=10)
        if active.returncode == 0:
            # Same night (it self-stops after max_hours) — keep one continuous capture.
            return {'status': 'already_running'}
        # A previous run that hit RuntimeMaxSec ends as "failed"; that blocks name reuse.
        run(['sudo', '-n', 'systemctl', 'reset-failed', UNIT], timeout=10,
            capture_output=True)
        base = cfg.get('night_capture_dir') or os.path.expanduser('~/kjdata/night-captures')
        out_dir = os.path.join(base, night_date(now))
        cmd = build_command(out_dir, max_hours=cfg.get('night_capture_max_hours', 12))
        res = run(cmd, timeout=20, capture_output=True, text=True)
        if res.returncode != 0:
            return {'status': 'error', 'error': (res.stderr or '').strip()[:500]}
        return {'status': 'started', 'out': out_dir}
    except Exception as exc:  # recording must never break "New Rotation"
        return {'status': 'error', 'error': repr(exc)[:500]}


def start_night_capture_async(cfg, log=None):
    """Fire-and-forget from a request handler."""
    def _go():
        result = start_night_capture(cfg)
        if log:
            log(f"Night capture on New Rotation: {result}")
    threading.Thread(target=_go, daemon=True, name='night-capture-start').start()
