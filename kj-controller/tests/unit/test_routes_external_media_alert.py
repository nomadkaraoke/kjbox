"""Routes: external_media_alert in /status (SSD-disconnect banner)."""


def test_status_external_media_alert_none_by_default(flask_app, flask_test_client):
    resp = flask_test_client.get('/status')
    assert resp.status_code == 200
    assert resp.get_json()['external_media_alert'] is None


def test_status_surfaces_monitor_alert(flask_app, flask_test_client):
    flask_app.external_media_monitor.config['external_media_mount'] = '/media/nomad/Nomad4TBOne'
    flask_app.external_media_monitor.check_once()
    flask_app.external_media_monitor.check_once()  # cross FAILURE_THRESHOLD

    data = flask_test_client.get('/status').get_json()
    alert = data['external_media_alert']
    # The configured mount doesn't exist in the test sandbox, so the probe
    # fails immediately (mirrors a real "medium not present" mount).
    assert alert is not None
    assert alert['mount'] == '/media/nomad/Nomad4TBOne'
    assert 'unplug and replug' in alert['message']


def test_status_clears_alert_once_mount_recovers(flask_app, flask_test_client, tmp_path):
    mon = flask_app.external_media_monitor
    mon.config['external_media_mount'] = '/media/nomad/Nomad4TBOne'
    mon.check_once()
    mon.check_once()
    assert flask_test_client.get('/status').get_json()['external_media_alert'] is not None

    mon.config['external_media_mount'] = str(tmp_path)
    mon.check_once()
    assert flask_test_client.get('/status').get_json()['external_media_alert'] is None
