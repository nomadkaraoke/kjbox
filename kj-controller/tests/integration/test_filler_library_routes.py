"""Route tests for the Manage Filler Music endpoints."""

import io

import pytest


@pytest.fixture
def fdir(flask_app, tmp_path):
    d = tmp_path / 'filler'
    d.mkdir()
    flask_app.kj_config['filler_music_dir'] = str(d)
    return d


def test_library_lists_tracks_with_labels(flask_test_client, fdir):
    (fdir / 'a.mp3').write_bytes(b'xx')
    r = flask_test_client.post('/filler_music/rename', json={'name': 'a.mp3', 'label': 'Alpha'})
    assert r.status_code == 200
    data = flask_test_client.get('/filler_music/library').get_json()
    assert data['tracks'][0]['label'] == 'Alpha' and data['tracks'][0]['name'] == 'a.mp3'


def test_legacy_list_unchanged(flask_test_client, fdir):
    (fdir / 'a.mp3').write_bytes(b'xx')
    assert flask_test_client.get('/filler_music').get_json() == ['a.mp3']


def test_rename_unknown_404(flask_test_client, fdir):
    assert flask_test_client.post('/filler_music/rename', json={'name': '../x.mp3', 'label': 'a'}).status_code == 404


def test_file_preview_serves_and_blocks_traversal(flask_test_client, fdir):
    (fdir / 'a.mp3').write_bytes(b'audio-bytes')
    assert flask_test_client.get('/filler_music/file/a.mp3').data == b'audio-bytes'
    assert flask_test_client.get('/filler_music/file/..%2Fsecret.mp3').status_code == 404
    assert flask_test_client.get('/filler_music/file/.filler-labels.json').status_code == 404


def test_delete_refuses_current_track(flask_test_client, flask_app, fdir):
    (fdir / 'a.mp3').write_bytes(b'x')
    flask_app.vlc.current_filler_track = 'a.mp3'
    r = flask_test_client.post('/filler_music/delete', json={'name': 'a.mp3'})
    assert r.status_code == 409 and (fdir / 'a.mp3').exists()
    flask_app.vlc.current_filler_track = 'other.mp3'
    assert flask_test_client.post('/filler_music/delete', json={'name': 'a.mp3'}).status_code == 200
    assert not (fdir / 'a.mp3').exists()


def test_import_rejects_non_youtube_url(flask_test_client, fdir):
    r = flask_test_client.post('/filler_music/import', json={'url': 'https://evil.example/x'})
    assert r.status_code == 400


def test_import_rejects_bad_clip_before_starting(flask_test_client, fdir):
    r = flask_test_client.post('/filler_music/import',
                               json={'url': 'https://youtu.be/abc', 'duration': 'zzz'})
    assert r.status_code == 400


def test_upload_requires_file(flask_test_client, fdir):
    assert flask_test_client.post('/filler_music/upload', data={}).status_code == 400


def test_upload_bad_clip_cleans_temp(flask_test_client, fdir):
    r = flask_test_client.post('/filler_music/upload', data={
        'file': (io.BytesIO(b'abc'), 'x.wav'), 'start': 'oops'},
        content_type='multipart/form-data')
    assert r.status_code == 400
    assert [p.name for p in fdir.iterdir()] == []


def test_unknown_job_404(flask_test_client, fdir):
    assert flask_test_client.get('/filler_music/jobs/nope').status_code == 404


def test_smooth_unknown_track_404(flask_test_client, fdir):
    assert flask_test_client.post('/filler_music/smooth', json={'name': 'nope.mp3'}).status_code == 404
