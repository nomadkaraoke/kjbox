"""Unit tests for ExternalMediaMonitor — the SSD-disconnect health check that
drives the KJ UI's alert banner (see docs/TROUBLESHOOTING.md, "4TB USB SSD
Drops Offline")."""

import os

from external_media_monitor import ExternalMediaMonitor, FAILURE_THRESHOLD


def _config(mount):
    return {"external_media_mount": mount}


def test_no_alert_when_mount_not_configured():
    mon = ExternalMediaMonitor(_config(""))
    mon.check_once()
    assert mon.alert is None


def test_healthy_mount_never_alerts(tmp_path):
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
    real_listdir = os.listdir

    def _boom(path):
        if path == str(tmp_path):
            raise OSError("Input/output error")
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", _boom)
    assert ExternalMediaMonitor._probe(str(tmp_path)) is False


def test_start_is_noop_without_configured_mount():
    mon = ExternalMediaMonitor(_config(""))
    mon.start()
    assert mon._thread is None
