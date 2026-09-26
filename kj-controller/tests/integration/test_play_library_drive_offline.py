"""/play when the external library SSD has dropped out (2026-09-24 real night).

At 22:11 the 4TB SSD's USB bridge hung ("Medium not present"). /play answered
"ZIP file does not contain a playable .mp3 file" and "Invalid or inaccessible
file path" for files that were fine, and the KJ tried 4 versions of the song.
An infra failure must read as "drive offline", never "bad file".
"""

import json
import os
import zipfile

import pytest

import external_media_monitor as emm


@pytest.fixture
def ssd(flask_app, tmp_path):
    """A fake external mount holding a real, valid CDG+MP3 zip."""
    mount = tmp_path / "Nomad4TBOne"
    (mount / "karaoke").mkdir(parents=True)
    zip_path = mount / "karaoke" / "Lady A - Downtown [KCD-75052].zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("song.cdg", b"cdg")
        zf.writestr("song.mp3", b"mp3")
    flask_app.kj_config["external_media_mount"] = str(mount)
    flask_app.vlc.enabled = True
    return mount, zip_path


def _play(client, path):
    return client.post("/play", data=json.dumps({"file_path": str(path)}),
                       content_type="application/json")


def _drive(monkeypatch, healthy):
    monkeypatch.setattr(emm.ExternalMediaMonitor, "_probe", staticmethod(lambda m: healthy))


def test_zip_read_failing_on_dropped_drive_is_offline_not_bad_zip(
        flask_app, flask_test_client, ssd, monkeypatch):
    _, zip_path = ssd
    _drive(monkeypatch, healthy=False)
    # The I/O error surfaces as "no mp3" from the extractor.
    monkeypatch.setattr(flask_app.zip_playback, "extract_and_get_mp3", lambda p: None)
    resp = _play(flask_test_client, zip_path)
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["error"] == "library_drive_offline"
    assert "replug" in body["message"]


def test_vanished_path_on_dropped_drive_is_offline_not_invalid_path(
        flask_test_client, ssd, monkeypatch):
    mount, _ = ssd
    _drive(monkeypatch, healthy=False)
    resp = _play(flask_test_client, mount / "karaoke" / "Lady A - Downtown [KVD-42951].mp4")
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "library_drive_offline"


def test_monitor_alert_answers_without_reprobing(flask_app, flask_test_client, ssd, monkeypatch):
    """Once the banner is up, /play reuses its message (no extra probe)."""
    mount, _ = ssd
    mon = flask_app.external_media_monitor
    monkeypatch.setattr(os.path, "ismount", lambda p: False)
    mon.check_once()
    mon.check_once()
    assert mon.alert
    monkeypatch.setattr(emm.ExternalMediaMonitor, "probe_now",
                        lambda self: pytest.fail("should use the existing alert"))
    resp = _play(flask_test_client, mount / "karaoke" / "gone.mp4")
    assert resp.status_code == 503
    assert resp.get_json()["message"] == mon.alert["message"]


def test_missing_file_on_healthy_drive_is_still_a_400(flask_test_client, ssd, monkeypatch):
    mount, _ = ssd
    _drive(monkeypatch, healthy=True)
    resp = _play(flask_test_client, mount / "karaoke" / "really-missing.mp4")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Invalid or inaccessible file path"


def test_bad_zip_on_healthy_drive_is_still_a_400(flask_app, flask_test_client, ssd, monkeypatch):
    mount, _ = ssd
    _drive(monkeypatch, healthy=True)
    bad = mount / "karaoke" / "cdg-only.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("song.cdg", b"cdg")
    resp = _play(flask_test_client, bad)
    assert resp.status_code == 400
    assert "playable .mp3" in resp.get_json()["error"]


def test_path_outside_the_mount_never_probes(flask_test_client, ssd, monkeypatch, tmp_path):
    monkeypatch.setattr(emm.ExternalMediaMonitor, "probe_now",
                        lambda self: pytest.fail("not on the external drive"))
    resp = _play(flask_test_client, tmp_path / "elsewhere" / "x.mp4")
    assert resp.status_code == 400


def test_probe_now_is_bounded_when_the_filesystem_call_hangs(monkeypatch, tmp_path):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(emm.ExternalMediaMonitor, "_probe",
                        staticmethod(lambda m: gate.wait(5)))
    mon = emm.ExternalMediaMonitor({"external_media_mount": str(tmp_path)})
    mon.probe_timeout = 0.05
    try:
        assert mon.probe_now() is False
    finally:
        gate.set()
