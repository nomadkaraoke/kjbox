"""Integration tests for POST /media/info (technical-details probe).

The ffprobe subprocess is monkeypatched so these tests don't depend on ffprobe
being installed; they cover the route's path-validation guard and response
shaping, which is where the risk lives.
"""
import os

import mediainfo


def _make_file(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 16)
    return path


def test_missing_path_is_400(flask_test_client):
    resp = flask_test_client.post('/media/info', json={})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_path_outside_allowed_folders_is_404(flask_test_client):
    resp = flask_test_client.post('/media/info', json={"file_path": "/etc/passwd"})
    assert resp.status_code == 404
    assert resp.get_json()["ok"] is False


def test_probes_allowed_internal_file(flask_test_client, flask_app, mock_config, monkeypatch):
    target = _make_file(os.path.join(mock_config["download_folder"], "song.mp4"))

    captured = {}

    def fake_probe(path):
        captured["path"] = path
        return {"ok": True, "container": "mov,mp4", "video": {"codec": "h264",
                "width": 1280, "height": 720}, "audio": {"codec": "aac"},
                "duration": 200.0}

    monkeypatch.setattr(mediainfo, "probe_media_info", fake_probe)

    resp = flask_test_client.post('/media/info', json={"file_path": target})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["video"]["codec"] == "h264"
    assert body["filename"] == "song.mp4"
    # The route must probe the validated real path, not the raw client string.
    assert os.path.realpath(captured["path"]) == os.path.realpath(target)


def test_external_mount_file_is_allowed(flask_test_client, flask_app, tmp_path, monkeypatch):
    # Point the external mount at a temp dir and drop a "catalog" file in it.
    mount = tmp_path / "ssd"
    catalog_file = _make_file(str(mount / "disc" / "track.zip"))
    flask_app.media.config["external_media_mount"] = str(mount)

    monkeypatch.setattr(mediainfo, "probe_media_info",
                        lambda p: {"ok": True, "container": "zip"})

    resp = flask_test_client.post('/media/info', json={"file_path": catalog_file})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
