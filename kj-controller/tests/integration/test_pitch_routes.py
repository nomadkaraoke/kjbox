"""Integration tests for pitch control routes."""

import json

import routes as routes_mod
import pytest


@pytest.fixture(autouse=True)
def _cancel_volume_timers():
    yield
    with routes_mod._volume_save_lock:
        if routes_mod._volume_save_timer is not None:
            routes_mod._volume_save_timer.cancel()
            routes_mod._volume_save_timer = None


def test_pitch_set(flask_test_client):
    """POST /pitch sets pitch_semitones."""
    response = flask_test_client.post('/pitch',
        data=json.dumps({"semitones": 3}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['success'] is True
    assert data['pitch_semitones'] == 3


def test_pitch_negative(flask_test_client):
    """POST /pitch accepts negative semitones."""
    response = flask_test_client.post('/pitch',
        data=json.dumps({"semitones": -2}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['pitch_semitones'] == -2


def test_pitch_clamped_high(flask_test_client):
    """POST /pitch clamps to +6."""
    response = flask_test_client.post('/pitch',
        data=json.dumps({"semitones": 12}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['pitch_semitones'] == 6


def test_pitch_clamped_low(flask_test_client):
    """POST /pitch clamps to -6."""
    response = flask_test_client.post('/pitch',
        data=json.dumps({"semitones": -10}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['pitch_semitones'] == -6


def test_pitch_reset(flask_test_client):
    """POST /pitch with 0 resets pitch."""
    flask_test_client.post('/pitch',
        data=json.dumps({"semitones": 4}),
        content_type='application/json')
    response = flask_test_client.post('/pitch',
        data=json.dumps({"semitones": 0}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['pitch_semitones'] == 0


def test_pitch_missing_semitones(flask_test_client):
    """POST /pitch without semitones returns 400."""
    response = flask_test_client.post('/pitch',
        data=json.dumps({}),
        content_type='application/json')
    assert response.status_code == 400


def test_pitch_invalid_type(flask_test_client):
    """POST /pitch with non-integer returns 400."""
    response = flask_test_client.post('/pitch',
        data=json.dumps({"semitones": "abc"}),
        content_type='application/json')
    assert response.status_code == 400


def test_status_includes_pitch(flask_test_client):
    """GET /status includes pitch_semitones field."""
    response = flask_test_client.get('/status')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'pitch_semitones' in data
    assert data['pitch_semitones'] == 0


def test_status_reflects_pitch_change(flask_test_client):
    """GET /status reflects pitch after POST /pitch."""
    flask_test_client.post('/pitch',
        data=json.dumps({"semitones": -3}),
        content_type='application/json')
    response = flask_test_client.get('/status')
    data = json.loads(response.data)
    assert data['pitch_semitones'] == -3
