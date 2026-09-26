"""ActionRecorder: every meaningful KJ / singer request lands in the per-night JSONL."""

import datetime as dt
import json

import pytest

from action_recorder import ActionRecorder, night_date


@pytest.fixture
def rec_app(mock_config, tmp_path):
    from app import create_app
    mock_config['action_log_dir'] = str(tmp_path / 'action-logs')
    mock_config['action_log_enabled'] = True
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    yield app
    app.catalog.close()


def _records(app):
    path = app.action_recorder.path_for()
    with open(path) as fh:
        return [json.loads(line) for line in fh]


def test_disabled_by_default_in_tests(flask_app):
    assert not hasattr(flask_app, 'action_recorder')


def test_singer_submit_recorded_with_body_and_response(rec_app):
    token = rec_app.sing_store.ensure_token()
    with rec_app.test_client() as c:
        resp = c.post(f'/sing/submit?t={token}', json={
            'singer_name': 'Bob', 'phone': '+61400000099', 'device_id': 'dev-1',
            'song_artist': 'Queen', 'song_title': 'Radio Ga Ga', 'source_type': 'make',
        }, headers={'User-Agent': 'TestPhone/1.0', 'CF-Connecting-IP': '203.0.113.9'})
    assert resp.status_code == 200
    (rec,) = [r for r in _records(rec_app) if r['path'] == '/sing/submit']
    assert rec['actor'] == 'singer'
    assert rec['method'] == 'POST'
    assert rec['body']['singer_name'] == 'Bob'
    assert rec['body']['phone'] == '+61400000099'
    assert rec['query'] == {'t': [token]}
    assert rec['status'] == 200
    assert rec['response']['request']['id'] == resp.get_json()['request']['id']
    assert rec['client']['User-Agent'] == 'TestPhone/1.0'
    assert rec['client']['CF-Connecting-IP'] == '203.0.113.9'
    assert rec['duration_ms'] >= 0
    assert rec['seq'] >= 1


def test_kj_mutation_recorded_and_polls_skipped(rec_app):
    with rec_app.test_client() as c:
        c.get('/status')
        c.get('/rotation')
        resp = c.post('/rotation', json={'singer': 'Alice', 'artist': 'ABBA', 'title': 'Waterloo'})
    recs = _records(rec_app)
    paths = [(r['method'], r['path']) for r in recs]
    assert ('GET', '/status') not in paths
    assert ('GET', '/rotation') not in paths
    (post,) = [r for r in recs if r['method'] == 'POST' and r['path'] == '/rotation']
    assert post['actor'] == 'kj'
    assert post['body']['singer'] == 'Alice'
    assert post['status'] == resp.status_code


def test_static_and_preview_segments_skipped(tmp_path):
    rec = ActionRecorder(str(tmp_path))
    assert not rec.should_record('GET', '/static/app.js')
    assert not rec.should_record('GET', '/sing/preview/hls/abc/seg1.ts')
    assert not rec.should_record('OPTIONS', '/sing/submit')
    assert rec.should_record('GET', '/search')
    assert rec.should_record('POST', '/rotation')
    assert rec.should_record('POST', '/status')  # only GET polls are skipped


def test_large_response_truncated(rec_app):
    import action_recorder
    from flask import jsonify
    rec_app.add_url_rule('/_big', 'big', lambda: jsonify(x='y' * (action_recorder.MAX_RESPONSE_BYTES + 10)))
    with rec_app.test_client() as c:
        c.get('/_big')
    (rec,) = [r for r in _records(rec_app) if r['path'] == '/_big']
    assert rec['response']['__truncated__'] is True
    assert rec['response']['bytes'] > action_recorder.MAX_RESPONSE_BYTES


def test_unhandled_exception_still_recorded(rec_app):
    rec_app.config['TESTING'] = False
    rec_app.config['PROPAGATE_EXCEPTIONS'] = False

    def boom():
        raise RuntimeError('kaboom')
    rec_app.add_url_rule('/_boom', 'boom', boom, methods=['POST'])
    with rec_app.test_client() as c:
        resp = c.post('/_boom', json={'a': 1})
    assert resp.status_code == 500
    recs = [r for r in _records(rec_app) if r['path'] == '/_boom']
    assert len(recs) == 1
    assert recs[0]['status'] == 500
    assert recs[0]['body'] == {'a': 1}


def test_recorder_write_failure_never_breaks_request(rec_app, monkeypatch):
    def fail(_record):
        raise OSError('disk full')
    monkeypatch.setattr(rec_app.action_recorder, 'write', fail)
    with rec_app.test_client() as c:
        resp = c.post('/rotation', json={'singer': 'Zed', 'artist': 'A', 'title': 'B'})
    assert resp.status_code < 500


@pytest.mark.parametrize('when,expected', [
    (dt.datetime(2026, 9, 24, 21, 0), '2026-09-24'),
    (dt.datetime(2026, 9, 25, 1, 30), '2026-09-24'),   # after midnight = same night
    (dt.datetime(2026, 9, 25, 12, 0), '2026-09-25'),
])
def test_night_date_rolls_at_noon(when, expected):
    assert night_date(when) == expected


def test_scanner_probe_on_unknown_path_is_not_labelled_kj(rec_app):
    """2026-09-24: ~300 internet scanner probes (`/sing/.env`, `/sing/wp-login.php`)
    404'd and were logged as `actor: kj`, polluting KJ fixtures."""
    with rec_app.test_client() as c:
        for path in ('/sing/.env', '/sing/wp-login.php', '/sing/.git/config'):
            assert c.get(path).status_code == 404
    recs = [r for r in _records(rec_app) if r['status'] == 404]
    assert len(recs) == 3
    assert {r['actor'] for r in recs} == {'anonymous'}


def test_scanner_probe_on_public_host_root_is_anonymous(rec_app):
    """On sing.<domain> the rewriter mounts the blueprint at `/`, so a probe
    for `/.env` arrives as `/sing/.env` — still anonymous, never kj."""
    rec_app.kj_config['sing_public_host'] = 'sing.example.com'
    with rec_app.test_client() as c:
        c.get('/.env', headers={'Host': 'sing.example.com'})
    (rec,) = [r for r in _records(rec_app) if r['path'].endswith('/.env')]
    assert rec['status'] == 404
    assert rec['actor'] == 'anonymous'
