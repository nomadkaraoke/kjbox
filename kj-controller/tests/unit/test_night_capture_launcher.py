"""'New Rotation' auto-starts the kj-night-capture sidecar for 12h."""

import datetime as dt
import subprocess
from unittest import mock

import pytest

import night_capture_launcher as ncl


class FakeRun:
    def __init__(self, active=False, start_rc=0):
        self.calls = []
        self.active = active
        self.start_rc = start_rc

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:2] == ['systemctl', 'is-active']:
            rc = 0 if self.active else 3
        elif 'systemd-run' in cmd:
            rc = self.start_rc
        else:
            rc = 0
        return subprocess.CompletedProcess(cmd, rc, stdout='', stderr='boom' if rc else '')


@pytest.fixture(autouse=True)
def _has_systemd_run():
    with mock.patch.object(ncl.shutil, 'which', return_value='/usr/bin/systemd-run'):
        yield


def test_starts_unit_with_12h_limit_and_night_dir(tmp_path):
    run = FakeRun()
    res = ncl.start_night_capture({'night_capture_dir': str(tmp_path)}, run=run,
                                  now=dt.datetime(2026, 10, 2, 1, 30))
    assert res == {'status': 'started', 'out': str(tmp_path / '2026-10-01')}
    start = next(c for c in run.calls if 'systemd-run' in c)
    assert '--unit=kj-night-capture' in start
    assert '--property=RuntimeMaxSec=43200' in start
    assert start[-3:] == [ncl.SCRIPT, '--out', str(tmp_path / '2026-10-01')]
    # stale failed unit from the previous night's timeout is cleared first
    assert ['sudo', '-n', 'systemctl', 'reset-failed', 'kj-night-capture'] in run.calls


def test_already_running_is_left_alone():
    run = FakeRun(active=True)
    assert ncl.start_night_capture({}, run=run) == {'status': 'already_running'}
    assert not any('systemd-run' in c for c in run.calls)


def test_start_failure_reported_not_raised():
    res = ncl.start_night_capture({}, run=FakeRun(start_rc=1))
    assert res['status'] == 'error' and 'boom' in res['error']


def test_exception_swallowed():
    def explode(*a, **k):
        raise OSError('no sudo')
    assert ncl.start_night_capture({}, run=explode)['status'] == 'error'


def test_unsupported_without_systemd_run():
    with mock.patch.object(ncl.shutil, 'which', return_value=None):
        assert ncl.start_night_capture({}, run=FakeRun()) == {'status': 'unsupported'}


def test_custom_max_hours():
    cmd = ncl.build_command('/x', max_hours=6, user='nomad')
    assert '--property=RuntimeMaxSec=21600' in cmd and '--uid=nomad' in cmd


def test_archive_route_triggers_capture_only_when_enabled(flask_app):
    with mock.patch('night_capture_launcher.start_night_capture_async') as start:
        with flask_app.test_client() as c:
            assert c.post('/rotation/archive').status_code == 200
        start.assert_not_called()  # disabled by default in tests
        flask_app.night_capture_enabled = True
        with flask_app.test_client() as c:
            assert c.post('/rotation/archive').status_code == 200
        start.assert_called_once()
