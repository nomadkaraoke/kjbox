"""Routes: player_health/player_alert in /status + /player-crash/ack."""


def test_status_player_alert_none_when_no_crash(flask_app, flask_test_client):
    resp = flask_test_client.get('/status')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['player_alert'] is None
    assert data['player_health_events'] == []


def test_status_includes_player_alert_after_crash(flask_app, flask_test_client):
    flask_app.vlc._record_crash({'engine': 'mpv', 'song': '/songs/av1.mp4'})
    data = flask_test_client.get('/status').get_json()
    assert data['player_alert'] is not None
    assert data['player_alert']['engine'] == 'mpv'
    assert data['player_alert']['song'] == '/songs/av1.mp4'
    assert len(data['player_health_events']) == 1


def test_player_crash_ack_clears_the_alert(flask_app, flask_test_client):
    flask_app.vlc._record_crash({'engine': 'mpv', 'song': '/songs/av1.mp4'})
    alert = flask_app.vlc.player_alert
    assert alert is not None
    resp = flask_test_client.post('/player-crash/ack', json={'id': alert['id']})
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True
    assert flask_app.vlc.player_alert is None


def test_player_crash_ack_tolerates_missing_id(flask_app, flask_test_client):
    resp = flask_test_client.post('/player-crash/ack', json={})
    assert resp.status_code == 200
