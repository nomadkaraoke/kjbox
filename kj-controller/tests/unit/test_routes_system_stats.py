"""Routes: /system/stats temperatures (ambient ACPI + 4TB USB SSD via smartctl)."""

import json
from collections import namedtuple

import pytest

import routes


_Shwt = namedtuple("Shwt", ["label", "current", "high", "critical"])


@pytest.fixture(autouse=True)
def _reset_ssd_caches():
    """Each test starts with a cold cache so stubbed values are picked up."""
    cold = {"ts": 0.0, "data": None, "populated": False}
    routes._SSD_TEMP_CACHE.update(cold)
    yield
    routes._SSD_TEMP_CACHE.update(cold)


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


# --- USB SSD device detection ---------------------------------------------

def _stub_lsblk(monkeypatch, stdout):
    class _Res:
        def __init__(self):
            self.stdout = stdout

    monkeypatch.setattr(routes.subprocess, "run", lambda *a, **k: _Res())


def test_find_usb_ssd_prefers_sandisk_model(monkeypatch):
    # A random USB stick appears first; the SanDisk must still win.
    _stub_lsblk(monkeypatch,
                'NAME="sdb" TRAN="usb" MODEL="Generic Flash Disk"\n'
                'NAME="sda" TRAN="usb" MODEL="Extreme Pro 55AF"\n')
    assert routes._find_usb_ssd_device() == "/dev/sda"


def test_find_usb_ssd_falls_back_to_first_usb(monkeypatch):
    _stub_lsblk(monkeypatch, 'NAME="sdc" TRAN="usb" MODEL="Some Disk"\n')
    assert routes._find_usb_ssd_device() == "/dev/sdc"


def test_find_usb_ssd_ignores_non_usb_and_returns_none(monkeypatch):
    _stub_lsblk(monkeypatch, 'NAME="sda" TRAN="sata" MODEL="Internal"\n')
    assert routes._find_usb_ssd_device() is None


def test_find_usb_ssd_none_when_lsblk_raises(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("lsblk")
    monkeypatch.setattr(routes.subprocess, "run", boom)
    assert routes._find_usb_ssd_device() is None


# --- USB SSD (smartctl) ----------------------------------------------------

_SMART_JSON = json.dumps({
    "nvme_smart_health_information_log": {
        "temperature": 44,
        "warning_temp_time": 0,
        "critical_comp_time": 0,
    }
})


def _stub_smartctl(monkeypatch, stdout, returncode=4):
    """smartctl exits non-zero (status bitmask) even on success; emulate that."""
    monkeypatch.setattr(routes, "_find_usb_ssd_device", lambda: "/dev/sda")

    class _Res:
        def __init__(self):
            self.stdout = stdout
            self.returncode = returncode

    monkeypatch.setattr(routes.subprocess, "run", lambda *a, **k: _Res())


def test_usb_ssd_parses_temp_and_lifetime_history(monkeypatch):
    _stub_smartctl(monkeypatch, _SMART_JSON)
    got = routes._read_usb_ssd_temp()
    assert got == {"temp_c": 44, "warning_time_min": 0, "critical_time_min": 0}


def test_usb_ssd_none_when_no_device(monkeypatch):
    monkeypatch.setattr(routes, "_find_usb_ssd_device", lambda: None)
    assert routes._read_usb_ssd_temp() is None


def test_usb_ssd_none_on_garbage_output(monkeypatch):
    _stub_smartctl(monkeypatch, "not json")
    assert routes._read_usb_ssd_temp() is None


def test_usb_ssd_reading_is_cached(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(routes, "_find_usb_ssd_device", lambda: "/dev/sda")

    class _Res:
        stdout = _SMART_JSON
        returncode = 4

    def _run(*a, **k):
        calls["n"] += 1
        return _Res()

    monkeypatch.setattr(routes.subprocess, "run", _run)
    routes._read_usb_ssd_temp()
    routes._read_usb_ssd_temp()
    assert calls["n"] == 1  # second read served from cache


def test_usb_ssd_failure_is_cached(monkeypatch):
    # A failing/absent smartctl must not spawn a fresh (blocking) subprocess on
    # every poll — the None result is cached for the TTL too.
    calls = {"n": 0}
    monkeypatch.setattr(routes, "_find_usb_ssd_device", lambda: "/dev/sda")

    def _boom(*a, **k):
        calls["n"] += 1
        raise FileNotFoundError("smartctl")

    monkeypatch.setattr(routes.subprocess, "run", _boom)
    assert routes._read_usb_ssd_temp() is None
    assert routes._read_usb_ssd_temp() is None
    assert calls["n"] == 1  # second read served from the (negative) cache


# --- endpoint --------------------------------------------------------------

def test_system_stats_includes_temperatures(monkeypatch, flask_test_client):
    monkeypatch.setattr(routes, "_read_ambient_temp_c", lambda: 27.8)
    monkeypatch.setattr(
        routes, "_read_usb_ssd_temp",
        lambda: {"temp_c": 44, "warning_time_min": 0, "critical_time_min": 0},
    )
    data = flask_test_client.get("/system/stats").get_json()
    assert data["ambient_temp_c"] == 27.8
    assert data["ssd_temp_c"] == 44
    assert data["ssd_warning_time_min"] == 0
    assert data["ssd_critical_time_min"] == 0
    # existing fields still present
    assert "cpu_percent" in data and "disk_percent" in data


def test_system_stats_omits_null_ssd_counters(monkeypatch, flask_test_client):
    # Drive reports temp but not the lifetime counters — keys omitted, not null.
    monkeypatch.setattr(routes, "_read_ambient_temp_c", lambda: None)
    monkeypatch.setattr(
        routes, "_read_usb_ssd_temp",
        lambda: {"temp_c": 44, "warning_time_min": None, "critical_time_min": None},
    )
    data = flask_test_client.get("/system/stats").get_json()
    assert data["ssd_temp_c"] == 44
    assert "ssd_warning_time_min" not in data
    assert "ssd_critical_time_min" not in data


def test_system_stats_omits_absent_sensors(monkeypatch, flask_test_client):
    monkeypatch.setattr(routes, "_read_ambient_temp_c", lambda: None)
    monkeypatch.setattr(routes, "_read_usb_ssd_temp", lambda: None)
    data = flask_test_client.get("/system/stats").get_json()
    assert "ambient_temp_c" not in data
    assert "ssd_temp_c" not in data
    assert "cpu_percent" in data  # core stats unaffected
