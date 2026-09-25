"""Unit tests for ExternalMediaMonitor — the SSD-disconnect health check that
drives the KJ UI's alert banner (see docs/TROUBLESHOOTING.md, "4TB USB SSD
Drops Offline")."""

import os
import threading
import time

from external_media_monitor import ExternalMediaMonitor, FAILURE_THRESHOLD


def _config(mount):
    return {"external_media_mount": mount}


def test_no_alert_when_mount_not_configured():
    mon = ExternalMediaMonitor(_config(""))
    mon.check_once()
    assert mon.alert is None


def test_healthy_mount_never_alerts(tmp_path, monkeypatch):
    # tmp_path is a real, readable directory but not an actual mount point —
    # simulate the "genuinely mounted" case os.path.ismount() would report on
    # a real device, since _probe() requires both checks to pass.
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    mon = ExternalMediaMonitor(_config(str(tmp_path)))
    for _ in range(FAILURE_THRESHOLD + 2):
        mon.check_once()
    assert mon.alert is None


def test_missing_mount_point_is_unhealthy(tmp_path):
    missing = tmp_path / "not-there"
    mon = ExternalMediaMonitor(_config(str(missing)))
    for _ in range(FAILURE_THRESHOLD):
        mon.check_once()
    assert mon.alert is not None
    assert mon.alert["mount"] == str(missing)
    assert "unplug and replug" in mon.alert["message"]


def test_single_failure_does_not_alert_yet(tmp_path, monkeypatch):
    mon = ExternalMediaMonitor(_config(str(tmp_path)))
    monkeypatch.setattr(
        ExternalMediaMonitor, "_probe", staticmethod(lambda mount: False)
    )
    mon.check_once()
    assert mon.alert is None  # below FAILURE_THRESHOLD


def test_alert_trips_after_threshold_failures(tmp_path, monkeypatch):
    mon = ExternalMediaMonitor(_config(str(tmp_path)))
    monkeypatch.setattr(
        ExternalMediaMonitor, "_probe", staticmethod(lambda mount: False)
    )
    for _ in range(FAILURE_THRESHOLD):
        mon.check_once()
    assert mon.alert is not None
    assert mon.alert["mount"] == str(tmp_path)
    assert mon.alert["since"] > 0


def test_alert_clears_once_mount_recovers(tmp_path, monkeypatch):
    mon = ExternalMediaMonitor(_config(str(tmp_path)))
    monkeypatch.setattr(
        ExternalMediaMonitor, "_probe", staticmethod(lambda mount: False)
    )
    for _ in range(FAILURE_THRESHOLD):
        mon.check_once()
    assert mon.alert is not None

    monkeypatch.setattr(
        ExternalMediaMonitor, "_probe", staticmethod(lambda mount: True)
    )
    mon.check_once()
    assert mon.alert is None


def test_probe_returns_false_on_oserror_listdir(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    real_listdir = os.listdir

    def _boom(path):
        if path == str(tmp_path):
            raise OSError("Input/output error")
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", _boom)
    assert ExternalMediaMonitor._probe(str(tmp_path)) is False


def test_probe_returns_false_when_cleanly_unmounted(tmp_path, monkeypatch):
    # The mount point reverted to an ordinary (readable) directory on the
    # underlying filesystem — os.listdir() would succeed, but ismount() says
    # it's no longer the drive, so the probe must still report unhealthy.
    monkeypatch.setattr(os.path, "ismount", lambda p: False)
    assert ExternalMediaMonitor._probe(str(tmp_path)) is False


def test_start_is_noop_without_configured_mount():
    mon = ExternalMediaMonitor(_config(""))
    mon.start()
    assert mon._thread is None


def test_start_after_stop_resumes_polling(tmp_path):
    mon = ExternalMediaMonitor(_config(str(tmp_path)))
    mon.start()
    assert mon._thread is not None
    mon.stop()
    mon._thread.join(timeout=2)
    assert not mon._thread.is_alive()

    mon.start()  # must actually spawn a new poll thread, not no-op
    assert mon._thread.is_alive()
    mon.stop()
    mon._thread.join(timeout=2)


def test_start_immediately_after_stop_still_restarts(tmp_path):
    # No join() between stop() and start() — the old loop may still be mid
    # check_once() when start() is called. start() must wait it out rather
    # than silently leaving monitoring off (see CodeRabbit finding: restart
    # race between stop() and start()).
    mon = ExternalMediaMonitor(_config(str(tmp_path)))
    mon.stop_join_timeout = 2  # keep the test fast
    mon.start()
    first_thread = mon._thread
    assert first_thread is not None

    mon.stop()
    mon.start()  # called before first_thread has necessarily exited yet
    assert mon._thread is not None
    assert mon._thread.is_alive()

    mon.stop()
    mon._thread.join(timeout=2)
    assert not mon._thread.is_alive()


def test_check_once_treats_a_stuck_probe_as_unhealthy(tmp_path, monkeypatch):
    mon = ExternalMediaMonitor(_config(str(tmp_path)))
    mon.probe_timeout = 0.05  # keep the test fast

    def _stuck(mount):
        time.sleep(0.3)
        return True

    monkeypatch.setattr(ExternalMediaMonitor, "_probe", staticmethod(_stuck))
    for _ in range(FAILURE_THRESHOLD):
        mon.check_once()
    assert mon.alert is not None


def test_stuck_probe_does_not_accumulate_a_thread_per_poll(tmp_path, monkeypatch):
    # A prolonged failure polls every CHECK_INTERVAL_SECONDS — if _probe()
    # stays blocked, each check_once() must NOT spawn another probe thread on
    # top of the one still running, or a long outage would leak threads
    # without bound (see CodeRabbit finding: unbounded outstanding probes).
    mon = ExternalMediaMonitor(_config(str(tmp_path)))
    mon.probe_timeout = 0.05

    call_count = {"n": 0}
    release = threading.Event()

    def _stuck(mount):
        call_count["n"] += 1
        release.wait(5)  # stays "in flight" until the test releases it
        return True

    monkeypatch.setattr(ExternalMediaMonitor, "_probe", staticmethod(_stuck))
    try:
        mon.check_once()
        mon.check_once()
        mon.check_once()
        assert call_count["n"] == 1
    finally:
        release.set()
        mon._probe_thread.join(timeout=2)
