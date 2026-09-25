"""Routes: external_media_alert in /status (SSD-disconnect banner)."""

import os


def test_status_external_media_alert_none_by_default(flask_app, flask_test_client):
    resp = flask_test_client.get('/status')
    assert resp.status_code == 200
    assert resp.get_json()['external_media_alert'] is None


def test_status_surfaces_monitor_alert(flask_app, flask_test_client, tmp_path):
    # A test-owned, definitely-nonexistent path — not a real device path — so
    # this can't accidentally pass on a machine where that path happens to
    # exist (e.g. if tests ever ran on NomadPC itself).
    missing = str(tmp_path / "not-mounted")
    flask_app.external_media_monitor.config['external_media_mount'] = missing
    flask_app.external_media_monitor.check_once()
    flask_app.external_media_monitor.check_once()  # cross FAILURE_THRESHOLD

    data = flask_test_client.get('/status').get_json()
    alert = data['external_media_alert']
    assert alert is not None
    assert alert['mount'] == missing
    assert 'unplug and replug' in alert['message']


def test_status_clears_alert_once_mount_recovers(flask_app, flask_test_client, tmp_path, monkeypatch):
    mon = flask_app.external_media_monitor
    missing = str(tmp_path / "not-mounted")
    mon.config['external_media_mount'] = missing
    mon.check_once()
    mon.check_once()
    assert flask_test_client.get('/status').get_json()['external_media_alert'] is not None

    # tmp_path is a real, readable directory but not an actual mount point —
    # simulate the "genuinely mounted and responding" case for recovery.
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    mon.config['external_media_mount'] = str(tmp_path)
    mon.check_once()
    assert flask_test_client.get('/status').get_json()['external_media_alert'] is None
