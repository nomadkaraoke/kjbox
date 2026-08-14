"""Routes: /master-sync/run + /master-sync/status (the "Sync Masters" button)."""

import time

import scripts.sync_masters as sm


def _wait_until_idle(client, timeout=2.0):
    """Poll /master-sync/status until the background worker finishes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get('/master-sync/status').get_json()
        if not data['running']:
            return data
        time.sleep(0.02)
    raise AssertionError("master-sync worker did not finish in time")


def test_master_sync_run_reports_copied(flask_app, flask_test_client, monkeypatch):
    monkeypatch.setattr(sm, "run_master_sync_now",
                        lambda cfg: {"changed": True, "copied": 3, "error": None})
    resp = flask_test_client.post('/master-sync/run', json={})
    assert resp.status_code == 200
    assert resp.get_json()['started'] is True

    status = _wait_until_idle(flask_test_client)
    assert status['result']['copied'] == 3
    assert status['result']['error'] is None


def test_master_sync_run_surfaces_worker_exception(flask_app, flask_test_client, monkeypatch):
    def boom(cfg):
        raise RuntimeError("gcloud missing")

    monkeypatch.setattr(sm, "run_master_sync_now", boom)
    flask_test_client.post('/master-sync/run', json={})
    status = _wait_until_idle(flask_test_client)
    assert "gcloud missing" in status['result']['error']


def test_master_sync_run_rejects_concurrent_run(flask_app, flask_test_client, monkeypatch):
    # A slow worker keeps state["running"] True so the second request is rejected.
    release = {"go": False}

    def slow(cfg):
        while not release["go"]:
            time.sleep(0.01)
        return {"copied": 0, "error": None}

    monkeypatch.setattr(sm, "run_master_sync_now", slow)
    first = flask_test_client.post('/master-sync/run', json={}).get_json()
    assert first['started'] is True

    second = flask_test_client.post('/master-sync/run', json={}).get_json()
    assert second['started'] is False and second['running'] is True

    release["go"] = True
    _wait_until_idle(flask_test_client)
