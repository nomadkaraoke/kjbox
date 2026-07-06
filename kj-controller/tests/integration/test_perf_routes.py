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


def _use_tmp_recordings(client, tmp_path):
    client.application.perf_sampler.recorder._dir = str(tmp_path)


def test_perf_record_start_stop_list(flask_test_client, tmp_path):
    _use_tmp_recordings(flask_test_client, tmp_path)
    r = flask_test_client.post('/perf/record/start', json={"label": "unit test"})
    assert r.status_code == 200 and r.get_json()["recording"] is True

    lst = flask_test_client.get('/perf/record/list').get_json()
    assert lst["status"]["recording"] is True
    assert len(lst["recordings"]) == 1

    stop = flask_test_client.post('/perf/record/stop').get_json()
    assert stop["recording"] is False


def test_perf_record_summary_and_download(flask_test_client, tmp_path):
    import json
    _use_tmp_recordings(flask_test_client, tmp_path)
    sid = "20260706-010203-demo"
    rows = [{"_meta": True, "label": "demo", "id": sid, "started": 0}]
    rows.append({"t": 0.0, "playing": True, "engine": "mpv", "render_fps": 30.0,
                 "fps_target": 30, "video": {"container_fps": 30, "display_fps": 60},
                 "drops_delta": {"vo": 0}, "drops_meaningful": True, "health": "green"})
    (tmp_path / (sid + ".jsonl")).write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    summ = flask_test_client.get(f'/perf/record/{sid}/summary')
    assert summ.status_code == 200 and summ.get_json()["id"] == sid

    dl = flask_test_client.get(f'/perf/record/{sid}/download')
    assert dl.status_code == 200
    assert 'attachment' in dl.headers.get('Content-Disposition', '')


def test_perf_record_summary_404(flask_test_client, tmp_path):
    _use_tmp_recordings(flask_test_client, tmp_path)
    assert flask_test_client.get('/perf/record/nope/summary').status_code == 404
