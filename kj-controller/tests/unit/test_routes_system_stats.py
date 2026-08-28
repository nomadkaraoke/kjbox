"""Routes: /system/stats temperatures (ambient ACPI zone).

The 4TB USB SSD smartctl polling that used to live here was removed in
v0.95.0 — the `sntasmedia` passthrough hung the drive's USB bridge firmware
mid-show (see docs/TROUBLESHOOTING.md). These tests also pin that the ssd_*
fields stay gone.
"""

from collections import namedtuple

import pytest

import routes


_Shwt = namedtuple("Shwt", ["label", "current", "high", "critical"])


# --- ambient ---------------------------------------------------------------

def test_ambient_picks_lowest_plausible_and_drops_sentinel(monkeypatch):
    # acpitz reports a -273 invalid sentinel plus the real ~28C board sensor.
    fake = {"acpitz": [_Shwt("", -273.3, None, None), _Shwt("", 27.8, None, None)]}
    monkeypatch.setattr(
        "psutil.sensors_temperatures", lambda *a, **k: fake, raising=False
    )
    assert routes._read_ambient_temp_c() == 27.8


def test_ambient_none_when_no_valid_reading(monkeypatch):
    fake = {"acpitz": [_Shwt("", -273.3, None, None)]}
    monkeypatch.setattr(
        "psutil.sensors_temperatures", lambda *a, **k: fake, raising=False
    )
    assert routes._read_ambient_temp_c() is None


def test_ambient_none_when_sensors_raise(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no sensors")
    monkeypatch.setattr("psutil.sensors_temperatures", boom, raising=False)
    assert routes._read_ambient_temp_c() is None


# --- endpoint --------------------------------------------------------------

def test_system_stats_includes_ambient_temperature(monkeypatch, flask_test_client):
    monkeypatch.setattr(routes, "_read_ambient_temp_c", lambda: 27.8)
    data = flask_test_client.get("/system/stats").get_json()
    assert data["ambient_temp_c"] == 27.8
    # existing fields still present
    assert "cpu_percent" in data and "disk_percent" in data


def test_system_stats_omits_absent_ambient_sensor(monkeypatch, flask_test_client):
    monkeypatch.setattr(routes, "_read_ambient_temp_c", lambda: None)
    data = flask_test_client.get("/system/stats").get_json()
    assert "ambient_temp_c" not in data
    assert "cpu_percent" in data  # core stats unaffected


def test_system_stats_never_reports_ssd_fields(monkeypatch, flask_test_client):
    # Regression guard: SSD smartctl polling must stay removed (it hung the
    # SanDisk's USB bridge firmware — physical replug required to recover).
    monkeypatch.setattr(routes, "_read_ambient_temp_c", lambda: 27.8)
    data = flask_test_client.get("/system/stats").get_json()
    assert "ssd_temp_c" not in data
    assert "ssd_warning_time_min" not in data
    assert "ssd_critical_time_min" not in data
    assert not hasattr(routes, "_read_usb_ssd_temp")
    assert not hasattr(routes, "_find_usb_ssd_device")
