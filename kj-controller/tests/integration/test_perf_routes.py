"""Integration tests for the /perf/* endpoints.

The sampler is attached in create_app but its background thread is NOT started
under tests (config is passed) — snapshot() samples on demand, so the endpoint
works without a live device.
"""


def test_perf_stream_returns_snapshot(flask_test_client):
    resp = flask_test_client.get('/perf/stream')
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data) >= {"sample_interval_s", "controls", "now", "samples"}
    assert isinstance(data["samples"], list) and len(data["samples"]) >= 1
    now = data["now"]
    # Idle test box: nothing playing, but the sample is well-formed.
    assert now["playing"] is False
    assert "health" in now and now["health"] in ("green", "amber", "red")
    assert "drops" in now and "cpu" in now and "gpu" in now


def test_perf_toggle_rejects_unknown_control(flask_test_client):
    resp = flask_test_client.post('/perf/toggle/bogus', json={"on": True})
    assert resp.status_code == 400
    assert "unknown control" in resp.get_json()["error"]


def test_perf_toggle_overlay_success(flask_test_client, monkeypatch):
    import perf_sampler
    monkeypatch.setattr(perf_sampler, "apply_toggle",
                        lambda control, on: (True, on, "ok"))
    resp = flask_test_client.post('/perf/toggle/overlay', json={"on": False})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["on"] is False


def test_perf_toggle_reports_failure_as_500(flask_test_client, monkeypatch):
    import perf_sampler
    monkeypatch.setattr(perf_sampler, "apply_toggle",
                        lambda control, on: (False, None, "systemctl missing"))
    resp = flask_test_client.post('/perf/toggle/compositor', json={"on": True})
    assert resp.status_code == 500
    assert resp.get_json()["success"] is False
