"""Integration tests for Flask routes via test client."""

import json
import threading
import time
from unittest.mock import patch

from app import create_app
import routes as routes_mod


import pytest


@pytest.fixture(autouse=True)
def _cancel_volume_timers():
    """Cancel any pending debounced volume save timers between tests."""
    yield
    with routes_mod._volume_save_lock:
        if routes_mod._volume_save_timer is not None:
            routes_mod._volume_save_timer.cancel()
            routes_mod._volume_save_timer = None


def test_create_app_factory(mock_config):
    """create_app returns a configured Flask app."""
    app = create_app(config=mock_config)
    assert app.kj_config is mock_config
    assert hasattr(app, 'media')
    assert hasattr(app, 'vlc')
    assert app.vlc.enabled is False
    assert 'items' in app.download_queue
    assert hasattr(app, '_download_lock')


def test_index_returns_html(flask_test_client):
    """GET / returns 200 with HTML content."""
    response = flask_test_client.get('/')
    assert response.status_code == 200
    assert b'html' in response.data.lower()


def test_index_contains_vnc_preview(flask_test_client):
    """GET / includes the VNC Screen Preview section."""
    response = flask_test_client.get('/')
    assert b'vnc-preview-container' in response.data
    assert b'Screen Preview' in response.data
    assert b'vnc-password' in response.data


def test_media_list_empty(flask_test_client):
    """GET /media returns empty list when no media files exist."""
    response = flask_test_client.get('/media')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data == []


def test_status_without_vlc(flask_test_client):
    """GET /status returns stopped state when VLC is disabled."""
    response = flask_test_client.get('/status')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['state'] == 'stopped'
    assert data['vlc_enabled'] is False


def test_status_includes_simple_mode(flask_test_client, flask_app):
    # Default off
    resp = flask_test_client.get("/status")
    assert resp.status_code == 200
    assert resp.get_json().get("simple_mode") is False
    # Flip it on, status reflects it
    flask_app.sing_store.set_simple_mode(True)
    resp2 = flask_test_client.get("/status")
    assert resp2.get_json().get("simple_mode") is True


def test_status_rotation_downloads_surfaces_source_detail(flask_test_client, flask_app):
    """GET /status exposes source + source_detail per active rotation download
    so the UI can show GCS-vs-Drive-vs-YouTube on the prep badge."""
    # Seed a rotation entry + queue items: one GCS, one Drive, one YouTube.
    e1 = flask_app.rotation.add_entry("Alice", "Song A")
    e2 = flask_app.rotation.add_entry("Bob", "Song B")
    e3 = flask_app.rotation.add_entry("Carol", "Song C")
    with flask_app._download_lock:
        flask_app.download_queue['items'].extend([
            {'id': 'd1', 'status': 'downloading', 'source': 'divebar',
             'source_detail': 'gcs', 'rotation_entry_id': e1['id'],
             'url': 'https://storage.googleapis.com/divebar/x.mp4', 'progress': 0.42},
            {'id': 'd2', 'status': 'queued', 'source': 'divebar',
             'source_detail': 'drive', 'rotation_entry_id': e2['id'],
             'url': 'https://drive.google.com/uc?id=y', 'progress': 0},
            {'id': 'd3', 'status': 'downloading', 'source': 'youtube',
             'source_detail': None, 'rotation_entry_id': e3['id'],
             'url': 'https://youtu.be/abc', 'progress': 0.1},
        ])

    response = flask_test_client.get('/status')
    assert response.status_code == 200
    rd = json.loads(response.data)['rotation_downloads']
    assert rd[str(e1['id'])]['source'] == 'divebar'
    assert rd[str(e1['id'])]['source_detail'] == 'gcs'
    assert rd[str(e2['id'])]['source_detail'] == 'drive'
    assert rd[str(e3['id'])]['source'] == 'youtube'
    assert rd[str(e3['id'])]['source_detail'] is None


def test_play_requires_file_path(flask_test_client):
    """POST /play without file_path returns 400."""
    response = flask_test_client.post('/play',
        data=json.dumps({}),
        content_type='application/json')
    assert response.status_code == 400


def test_download_requires_url(flask_test_client):
    """POST /download without url returns 400."""
    response = flask_test_client.post('/download',
        data=json.dumps({}),
        content_type='application/json')
    assert response.status_code == 400


def test_delete_rejects_outside_download_folder(flask_test_client, flask_app, tmp_media_dir):
    """DELETE rejects files not in download folder with 403."""
    media_dir = tmp_media_dir / "media"
    test_file = media_dir / "song.mp4"
    test_file.write_text("fake video")

    response = flask_test_client.post('/delete',
        data=json.dumps({"file_path": str(test_file)}),
        content_type='application/json')
    assert response.status_code == 403


def test_delete_requires_file_path(flask_test_client):
    """POST /delete without file_path returns 400."""
    response = flask_test_client.post('/delete',
        data=json.dumps({}),
        content_type='application/json')
    assert response.status_code == 400


def test_seek_requires_time(flask_test_client):
    """POST /seek without time returns 400."""
    response = flask_test_client.post('/seek',
        data=json.dumps({}),
        content_type='application/json')
    assert response.status_code == 400


def test_control_requires_action(flask_test_client):
    """POST /control without action returns 400."""
    response = flask_test_client.post('/control',
        data=json.dumps({}),
        content_type='application/json')
    assert response.status_code == 400


def test_volume_invalid_target(flask_test_client):
    """POST /volume with invalid target returns 400."""
    response = flask_test_client.post('/volume',
        data=json.dumps({"target": "invalid", "level": 100}),
        content_type='application/json')
    assert response.status_code == 400


def test_play_vlc_disabled(flask_test_client, tmp_media_dir):
    """POST /play with valid file returns 503 when VLC is disabled."""
    media_dir = tmp_media_dir / "media"
    test_file = media_dir / "song.mp4"
    test_file.write_text("fake video")

    response = flask_test_client.post('/play',
        data=json.dumps({"file_path": str(test_file)}),
        content_type='application/json')
    assert response.status_code == 503


def test_play_invalid_path(flask_test_client):
    """POST /play with invalid file path returns 400."""
    response = flask_test_client.post('/play',
        data=json.dumps({"file_path": "/nonexistent/file.mp4"}),
        content_type='application/json')
    assert response.status_code == 400


def test_filler_music_list_empty(flask_test_client, flask_app, tmp_media_dir):
    """GET /filler_music returns empty when no audio files in filler dir."""
    response = flask_test_client.get('/filler_music')
    assert response.status_code == 200
    # tmp_media_dir may or may not have .mp3 files


def test_filler_music_set_requires_track_name(flask_test_client):
    """POST /filler_music without track_name returns 400."""
    response = flask_test_client.post('/filler_music',
        data=json.dumps({}),
        content_type='application/json')
    assert response.status_code == 400


def test_filler_music_set_not_found(flask_test_client):
    """POST /filler_music with nonexistent track returns 404."""
    response = flask_test_client.post('/filler_music',
        data=json.dumps({"track_name": "nonexistent.mp3"}),
        content_type='application/json')
    assert response.status_code == 404


def test_audio_device_get(flask_test_client):
    """GET /audio_device returns current device and available devices."""
    response = flask_test_client.get('/audio_device')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "current" in data
    assert "available" in data


def test_audio_device_set_requires_device(flask_test_client):
    """POST /audio_device without device returns 400."""
    response = flask_test_client.post('/audio_device',
        data=json.dumps({}),
        content_type='application/json')
    assert response.status_code == 400


def test_audio_device_set_unknown_device(flask_test_client):
    """POST /audio_device with unknown device returns 400."""
    response = flask_test_client.post('/audio_device',
        data=json.dumps({"device": "nonexistent"}),
        content_type='application/json')
    assert response.status_code == 400


def test_audio_device_already_active(flask_test_client):
    """POST /audio_device with current device returns success."""
    response = flask_test_client.post('/audio_device',
        data=json.dumps({"device": "hdmiout"}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True


def test_rescan(flask_test_client):
    """POST /rescan reloads config and rescans."""
    response = flask_test_client.post('/rescan')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "count" in data


def test_delete_valid_download_file(flask_test_client, flask_app, tmp_media_dir):
    """POST /delete with valid file in download folder succeeds."""
    import os
    download_dir = tmp_media_dir / "downloads"
    test_file = download_dir / "video.mp4"
    test_file.write_text("fake video")
    real_path = os.path.realpath(str(test_file))

    # Add to media index so deletion can find it
    flask_app.media.index[real_path] = {
        "path": real_path, "filename": "video.mp4",
        "folder": str(download_dir), "is_download": True,
    }

    response = flask_test_client.post('/delete',
        data=json.dumps({"file_path": str(test_file)}),
        content_type='application/json')
    assert response.status_code == 200
    assert not test_file.exists()


def test_media_list_with_files(flask_test_client, flask_app, tmp_media_dir):
    """GET /media returns indexed files after scan."""
    media_dir = tmp_media_dir / "media"
    (media_dir / "song.mp4").write_text("fake")
    flask_app.media.scan()

    response = flask_test_client.get('/media')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert len(data) == 1
    assert data[0]["filename"] == "song.mp4"


def test_media_list_includes_youtube_id(flask_test_client, flask_app, tmp_media_dir):
    """GET /media includes youtube_id for YouTube-format filenames.

    The KN downloaded-state detection in the frontend (renderKNResults) reads
    item.youtube_id from the /media response to build the set of downloaded IDs.
    This test ensures the field is present so that detection works end-to-end.
    """
    media_dir = tmp_media_dir / "media"
    (media_dir / "dQw4w9WgXcQ__RickAstley__Never Gonna Give You Up.mp4").write_text("fake")
    flask_app.media.scan()

    response = flask_test_client.get('/media')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert len(data) == 1
    item = data[0]
    assert "youtube_id" in item, "youtube_id must be present for KN downloaded-state detection"
    assert item["youtube_id"] == "dQw4w9WgXcQ"


def test_fix_audio(flask_test_client):
    """POST /fix_audio returns success (VLC disabled, no-op)."""
    response = flask_test_client.post('/fix_audio')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True


# --- Tests with VLC mocked as enabled ---

def test_seek_with_vlc_disabled(flask_test_client):
    """POST /seek executes when VLC is disabled (send_command returns None)."""
    response = flask_test_client.post('/seek',
        data=json.dumps({"time": 30}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True


def test_control_pause_resume(flask_test_client):
    """POST /control with pause_resume action."""
    response = flask_test_client.post('/control',
        data=json.dumps({"action": "pause_resume"}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True


def test_control_restart(flask_test_client):
    """POST /control with restart action."""
    response = flask_test_client.post('/control',
        data=json.dumps({"action": "restart"}),
        content_type='application/json')
    assert response.status_code == 200


def test_control_stop(flask_test_client):
    """POST /control with stop action."""
    response = flask_test_client.post('/control',
        data=json.dumps({"action": "stop"}),
        content_type='application/json')
    assert response.status_code == 200


def test_volume_karaoke(flask_test_client):
    """POST /volume with karaoke target."""
    response = flask_test_client.post('/volume',
        data=json.dumps({"target": "karaoke", "level": 150}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True


def test_volume_filler(flask_test_client):
    """POST /volume with filler target."""
    response = flask_test_client.post('/volume',
        data=json.dumps({"target": "filler", "level": 80}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True


def test_filler_music_list_with_files(flask_test_client, flask_app, tmp_media_dir):
    """GET /filler_music returns audio files from filler dir."""
    # Create audio files in the filler music dir
    (tmp_media_dir / "track1.mp3").write_text("fake")
    (tmp_media_dir / "track2.wav").write_text("fake")
    (tmp_media_dir / "readme.txt").write_text("not audio")

    response = flask_test_client.get('/filler_music')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "track1.mp3" in data
    assert "track2.wav" in data
    assert "readme.txt" not in data


def test_filler_music_list_no_dir(flask_test_client, flask_app):
    """GET /filler_music returns empty when filler_music_dir not configured."""
    flask_app.kj_config['filler_music_dir'] = ''
    response = flask_test_client.get('/filler_music')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data == []


def test_filler_music_set_valid_track(flask_test_client, flask_app, tmp_media_dir):
    """POST /filler_music with valid track succeeds."""
    (tmp_media_dir / "song.mp3").write_text("fake audio")
    response = flask_test_client.post('/filler_music',
        data=json.dumps({"track_name": "song.mp3"}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True
    assert flask_app.vlc.current_filler_track == "song.mp3"


def test_status_with_current_playing(flask_test_client, flask_app, tmp_media_dir):
    """GET /status includes display_name when a file is playing."""
    media_dir = tmp_media_dir / "media"
    test_file = media_dir / "song.mp4"
    test_file.write_text("fake")
    import os
    real_path = os.path.realpath(str(test_file))

    flask_app.vlc.current_playing_path = real_path
    flask_app.media.index[real_path] = {
        "path": real_path, "filename": "song.mp4", "display_name": "My Song"
    }

    response = flask_test_client.get('/status')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["current_playing"] == "My Song"
    assert data["current_playing_path"] == real_path


def test_status_with_unknown_playing_path(flask_test_client, flask_app):
    """GET /status falls back to basename when path not in index."""
    flask_app.vlc.current_playing_path = "/some/path/video.mp4"

    response = flask_test_client.get('/status')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["current_playing"] == "video.mp4"


def test_delete_error_handling(flask_test_client, flask_app, tmp_media_dir, mocker):
    """POST /delete returns 500 when file deletion fails."""
    import os
    download_dir = tmp_media_dir / "downloads"
    test_file = download_dir / "video.mp4"
    test_file.write_text("fake video")
    real_path = os.path.realpath(str(test_file))

    flask_app.media.index[real_path] = {
        "path": real_path, "filename": "video.mp4",
        "folder": str(download_dir), "is_download": True,
    }

    mocker.patch.object(flask_app.media, 'delete_file', side_effect=PermissionError("denied"))

    response = flask_test_client.post('/delete',
        data=json.dumps({"file_path": str(test_file)}),
        content_type='application/json')
    assert response.status_code == 500


def test_audio_device_switch(flask_test_client, flask_app, mocker):
    """POST /audio_device switches to a new device."""
    flask_app.kj_config['audio_devices'] = {
        "hdmiout": "HDMI Output",
        "usbmixer": "USB Mixer",
    }
    flask_app.vlc.audio_device = "hdmiout"

    # Mock restart_instances since it starts a thread
    mocker.patch.object(flask_app.vlc, 'restart_instances')

    response = flask_test_client.post('/audio_device',
        data=json.dumps({"device": "usbmixer"}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True
    assert flask_app.vlc.audio_device == "usbmixer"


# --- Additional coverage tests ---

def test_download_queues_item(flask_test_client, flask_app, mocker):
    """POST /download adds an item to the queue and returns its id."""
    mocker.patch.object(flask_app.media, 'download_video',
        return_value=("/path/to/video.mp4", "My Video"))

    response = flask_test_client.post('/download',
        data=json.dumps({"url": "https://youtube.com/watch?v=abc123"}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True
    assert "id" in data

    # Wait for worker to finish
    import time
    for _ in range(50):
        with flask_app._download_lock:
            item = flask_app.download_queue['items'][0]
            if item['status'] != 'downloading':
                break
        time.sleep(0.05)

    assert item['status'] == 'completed'
    assert item['title'] == 'My Video'


def test_download_failure_tracked(flask_test_client, flask_app, mocker):
    """POST /download tracks failure state when download fails."""
    mocker.patch.object(flask_app.media, 'download_video',
        return_value=(None, None))

    response = flask_test_client.post('/download',
        data=json.dumps({"url": "https://youtube.com/watch?v=bad"}),
        content_type='application/json')
    assert response.status_code == 200

    import time
    for _ in range(50):
        with flask_app._download_lock:
            item = flask_app.download_queue['items'][0]
            if item['status'] != 'downloading':
                break
        time.sleep(0.05)

    assert item['status'] == 'error'


def test_download_rejects_duplicate_url(flask_test_client, flask_app, mocker):
    """POST /download rejects duplicate URL already in queue."""
    mocker.patch.object(flask_app.media, 'download_video',
        side_effect=lambda url: time.sleep(10))  # block forever

    flask_test_client.post('/download',
        data=json.dumps({"url": "https://youtube.com/watch?v=dup"}),
        content_type='application/json')

    import time
    time.sleep(0.1)  # let worker start

    response = flask_test_client.post('/download',
        data=json.dumps({"url": "https://youtube.com/watch?v=dup"}),
        content_type='application/json')
    assert response.status_code == 409


def test_download_allows_different_urls(flask_test_client, flask_app, mocker):
    """POST /download allows different URLs in the queue."""
    mocker.patch.object(flask_app.media, 'download_video',
        side_effect=lambda url: time.sleep(10))

    r1 = flask_test_client.post('/download',
        data=json.dumps({"url": "https://youtube.com/watch?v=a"}),
        content_type='application/json')
    assert r1.status_code == 200

    r2 = flask_test_client.post('/download',
        data=json.dumps({"url": "https://youtube.com/watch?v=b"}),
        content_type='application/json')
    assert r2.status_code == 200

    with flask_app._download_lock:
        assert len(flask_app.download_queue['items']) == 2


def test_download_rejects_full_queue(flask_test_client, flask_app, mocker):
    """POST /download rejects when 5 items are already queued/downloading."""
    mocker.patch.object(flask_app.media, 'download_video',
        side_effect=lambda url: time.sleep(10))

    for i in range(5):
        r = flask_test_client.post('/download',
            data=json.dumps({"url": f"https://youtube.com/watch?v=q{i}"}),
            content_type='application/json')
        assert r.status_code == 200

    response = flask_test_client.post('/download',
        data=json.dumps({"url": "https://youtube.com/watch?v=q5"}),
        content_type='application/json')
    assert response.status_code == 409


def test_download_queue_in_status(flask_test_client, flask_app):
    """GET /status includes download_queue list."""
    flask_app.download_queue['items'] = [
        {'id': '1', 'url': 'https://example.com', 'status': 'downloading',
         'title': None, 'error': None, 'file_path': None,
         'added_at': 0, 'completed_at': None}
    ]
    response = flask_test_client.get('/status')
    data = json.loads(response.data)
    assert 'download_queue' in data
    assert len(data['download_queue']) == 1
    assert data['download_queue'][0]['status'] == 'downloading'
    flask_app.download_queue['items'] = []


def test_download_ack_specific_id(flask_test_client, flask_app):
    """POST /download/ack with id removes that specific completed item."""
    flask_app.download_queue['items'] = [
        {'id': 'a', 'url': 'u1', 'status': 'completed', 'title': 'T1',
         'error': None, 'file_path': '/f', 'added_at': 0, 'completed_at': 1},
        {'id': 'b', 'url': 'u2', 'status': 'queued', 'title': None,
         'error': None, 'file_path': None, 'added_at': 0, 'completed_at': None},
    ]
    response = flask_test_client.post('/download/ack',
        data=json.dumps({"id": "a"}),
        content_type='application/json')
    assert response.status_code == 200
    assert len(flask_app.download_queue['items']) == 1
    assert flask_app.download_queue['items'][0]['id'] == 'b'


def test_download_ack_legacy_no_id(flask_test_client, flask_app):
    """POST /download/ack without id removes all completed/errored items."""
    flask_app.download_queue['items'] = [
        {'id': 'a', 'url': 'u1', 'status': 'completed', 'title': 'T1',
         'error': None, 'file_path': '/f', 'added_at': 0, 'completed_at': 1},
        {'id': 'b', 'url': 'u2', 'status': 'error', 'title': None,
         'error': 'fail', 'file_path': None, 'added_at': 0, 'completed_at': 1},
        {'id': 'c', 'url': 'u3', 'status': 'queued', 'title': None,
         'error': None, 'file_path': None, 'added_at': 0, 'completed_at': None},
    ]
    response = flask_test_client.post('/download/ack')
    assert response.status_code == 200
    assert len(flask_app.download_queue['items']) == 1
    assert flask_app.download_queue['items'][0]['id'] == 'c'


def test_download_cancel_queued_item(flask_test_client, flask_app):
    """POST /download/cancel removes a queued item."""
    flask_app.download_queue['items'] = [
        {'id': 'x', 'url': 'u1', 'status': 'queued', 'title': None,
         'error': None, 'file_path': None, 'added_at': 0, 'completed_at': None},
    ]
    response = flask_test_client.post('/download/cancel',
        data=json.dumps({"id": "x"}),
        content_type='application/json')
    assert response.status_code == 200
    assert len(flask_app.download_queue['items']) == 0


def test_download_cancel_downloading_rejected(flask_test_client, flask_app):
    """POST /download/cancel rejects cancelling an active download."""
    flask_app.download_queue['items'] = [
        {'id': 'y', 'url': 'u1', 'status': 'downloading', 'title': None,
         'error': None, 'file_path': None, 'added_at': 0, 'completed_at': None},
    ]
    response = flask_test_client.post('/download/cancel',
        data=json.dumps({"id": "y"}),
        content_type='application/json')
    assert response.status_code == 409


def test_download_cancel_not_found(flask_test_client, flask_app):
    """POST /download/cancel returns 404 for nonexistent id."""
    response = flask_test_client.post('/download/cancel',
        data=json.dumps({"id": "nonexistent"}),
        content_type='application/json')
    assert response.status_code == 404


def test_download_worker_sequential(flask_test_client, flask_app, mocker):
    """Worker processes items sequentially — second item starts after first."""
    call_order = []

    def fake_download(url):
        call_order.append(url)
        import time
        time.sleep(0.05)
        return (f"/path/{url}", f"Title {url}")

    mocker.patch.object(flask_app.media, 'download_video', side_effect=fake_download)

    flask_test_client.post('/download',
        data=json.dumps({"url": "url1"}), content_type='application/json')
    flask_test_client.post('/download',
        data=json.dumps({"url": "url2"}), content_type='application/json')

    import time
    for _ in range(100):
        with flask_app._download_lock:
            statuses = [i['status'] for i in flask_app.download_queue['items']]
            if all(s == 'completed' for s in statuses):
                break
        time.sleep(0.05)

    assert call_order == ['url1', 'url2']
    assert all(i['status'] == 'completed' for i in flask_app.download_queue['items'])


def test_download_worker_continues_after_error(flask_test_client, flask_app, mocker):
    """Worker continues to next item when one fails."""
    def fake_download(url):
        if 'fail' in url:
            return (None, None)
        return (f"/path/{url}", f"Title {url}")

    mocker.patch.object(flask_app.media, 'download_video', side_effect=fake_download)

    flask_test_client.post('/download',
        data=json.dumps({"url": "fail-url"}), content_type='application/json')
    flask_test_client.post('/download',
        data=json.dumps({"url": "good-url"}), content_type='application/json')

    import time
    for _ in range(100):
        with flask_app._download_lock:
            items = flask_app.download_queue['items']
            if len(items) == 2 and all(i['status'] in ('completed', 'error') for i in items):
                break
        time.sleep(0.05)

    assert items[0]['status'] == 'error'
    assert items[1]['status'] == 'completed'


def test_volume_non_numeric_level(flask_test_client):
    """POST /volume with non-numeric level returns 400."""
    response = flask_test_client.post('/volume',
        data=json.dumps({"target": "karaoke", "level": "not-a-number"}),
        content_type='application/json')
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "number" in data["error"].lower()


def test_volume_requires_both_params(flask_test_client):
    """POST /volume without level returns 400."""
    response = flask_test_client.post('/volume',
        data=json.dumps({"target": "karaoke"}),
        content_type='application/json')
    assert response.status_code == 400


def test_filler_music_list_missing_dir(flask_test_client, flask_app):
    """GET /filler_music returns empty when dir doesn't exist."""
    flask_app.kj_config['filler_music_dir'] = '/nonexistent/dir'
    response = flask_test_client.get('/filler_music')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data == []


def test_play_with_vlc_enabled(flask_test_client, flask_app, tmp_media_dir, mocker):
    """POST /play with VLC enabled starts playback thread."""
    media_dir = tmp_media_dir / "media"
    test_file = media_dir / "song.mp4"
    test_file.write_text("fake video")

    flask_app.vlc.enabled = True
    mocker.patch.object(flask_app.vlc, 'play_video')

    response = flask_test_client.post('/play',
        data=json.dumps({"file_path": str(test_file)}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True


def test_control_pause_resume_paused_state(flask_test_client, flask_app, mocker):
    """POST /control pause_resume when paused does NOT start filler (ALSA held)."""
    flask_app.vlc.enabled = True
    # pause_resume_karaoke returns True (now paused)
    mocker.patch.object(flask_app.vlc, 'pause_resume_karaoke', return_value=True)
    fade_in_mock = mocker.patch.object(flask_app.vlc, 'fade_in_filler')

    response = flask_test_client.post('/control',
        data=json.dumps({"action": "pause_resume"}),
        content_type='application/json')
    assert response.status_code == 200
    assert flask_app.vlc.karaoke_active is False
    # Filler must NOT start — paused karaoke still holds the ALSA device
    fade_in_mock.assert_not_called()


def test_control_pause_resume_playing_state(flask_test_client, flask_app, mocker):
    """POST /control resume does NOT fade out filler (karaoke reclaims ALSA)."""
    flask_app.vlc.enabled = True
    # pause_resume_karaoke returns False (now playing/resumed)
    mocker.patch.object(flask_app.vlc, 'pause_resume_karaoke', return_value=False)
    fade_out_mock = mocker.patch.object(flask_app.vlc, 'fade_out_filler')

    response = flask_test_client.post('/control',
        data=json.dumps({"action": "pause_resume"}),
        content_type='application/json')
    assert response.status_code == 200
    assert flask_app.vlc.karaoke_active is True
    # Filler should NOT be faded out — it was never started during pause
    fade_out_mock.assert_not_called()


def test_delete_invalid_path(flask_test_client):
    """POST /delete with path outside media folders returns 400."""
    response = flask_test_client.post('/delete',
        data=json.dumps({"file_path": "/tmp/not-in-media-folders/file.mp4"}),
        content_type='application/json')
    assert response.status_code == 400


def test_filler_music_set_with_seek(flask_test_client, flask_app, tmp_media_dir, mocker):
    """POST /filler_music with VLC enabled seeks to random position."""
    (tmp_media_dir / "song.mp3").write_text("fake audio")
    flask_app.vlc.enabled = True

    # send_command returns status with length for the status check
    mocker.patch.object(flask_app.vlc, 'send_command',
        return_value={"state": "playing", "length": 300})

    response = flask_test_client.post('/filler_music',
        data=json.dumps({"track_name": "song.mp3"}),
        content_type='application/json')
    assert response.status_code == 200
    # Verify seek command was sent (one of the send_command calls should contain "seek")
    calls = flask_app.vlc.send_command.call_args_list
    seek_calls = [c for c in calls if "seek" in str(c)]
    assert len(seek_calls) > 0


def test_status_with_vlc_enabled(flask_test_client, flask_app, mocker):
    """GET /status with playback enabled returns full status data."""
    flask_app.vlc.enabled = True
    mocker.patch.object(flask_app.vlc, 'get_karaoke_status',
        return_value={"state": "playing", "time": 42, "length": 200})

    response = flask_test_client.get('/status')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["state"] == "playing"
    assert data["time"] == 42
    assert data["length"] == 200
    assert data["vlc_enabled"] is True


# --- System Control Tests ---

def test_system_restart_app(flask_test_client, mocker):
    """POST /system/restart-app returns success and spawns restart thread."""
    mock_run = mocker.patch('routes.subprocess.run')
    mock_thread = mocker.patch('routes.threading.Thread')
    response = flask_test_client.post('/system/restart-app')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True
    mock_thread.assert_called_once()
    mock_thread.return_value.start.assert_called_once()


def test_system_update_pulls_and_restarts(flask_test_client, mocker):
    """POST /system/update runs git pull and restarts if .py files changed."""
    mock_run = mocker.patch('routes.subprocess.run')
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "Updating abc123..def456\n routes.py | 5 ++---"
    mock_run.return_value.stderr = ""
    mock_thread = mocker.patch('routes.threading.Thread')
    response = flask_test_client.post('/system/update')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True
    assert data["restarting"] is True
    mock_thread.assert_called_once()


def test_system_update_always_restarts_even_static_only(flask_test_client, mocker):
    """POST /system/update always restarts, even if only static files changed."""
    mock_run = mocker.patch('routes.subprocess.run')
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "Updating abc..def\n static/app.js | 2 +-"
    mock_run.return_value.stderr = ""
    mock_thread = mocker.patch('routes.threading.Thread')
    response = flask_test_client.post('/system/update')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True
    assert data["restarting"] is True
    mock_thread.assert_called_once()


def test_system_update_git_pull_failure(flask_test_client, mocker):
    """POST /system/update returns 500 if git pull fails."""
    mock_run = mocker.patch('routes.subprocess.run')
    mock_run.return_value.returncode = 1
    mock_run.return_value.stdout = ""
    mock_run.return_value.stderr = "fatal: not a git repository"
    response = flask_test_client.post('/system/update')
    assert response.status_code == 500
    data = json.loads(response.data)
    assert "error" in data


def test_system_reboot(flask_test_client, mocker):
    """POST /system/reboot returns success and spawns reboot thread."""
    mock_run = mocker.patch('routes.subprocess.run')
    mock_thread = mocker.patch('routes.threading.Thread')
    response = flask_test_client.post('/system/reboot')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True
    mock_thread.assert_called_once()


def test_system_shutdown(flask_test_client, mocker):
    """POST /system/shutdown returns success and spawns shutdown thread."""
    mock_run = mocker.patch('routes.subprocess.run')
    mock_thread = mocker.patch('routes.threading.Thread')
    response = flask_test_client.post('/system/shutdown')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True
    mock_thread.assert_called_once()


# --- Auto-Deploy Tests ---

def test_autodeploy_status(flask_test_client, mocker):
    """GET /system/autodeploy returns active boolean."""
    mock_run = mocker.patch('routes.subprocess.run')
    mock_run.return_value.stdout = 'active\n'
    response = flask_test_client.get('/system/autodeploy')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['active'] is True


def test_autodeploy_status_inactive(flask_test_client, mocker):
    """GET /system/autodeploy returns false when inactive."""
    mock_run = mocker.patch('routes.subprocess.run')
    mock_run.return_value.stdout = 'inactive\n'
    response = flask_test_client.get('/system/autodeploy')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['active'] is False


def test_autodeploy_enable(flask_test_client, mocker):
    """POST /system/autodeploy enables the service."""
    mock_run = mocker.patch('routes.subprocess.run')
    # First call is enable, second is verify
    mock_run.return_value.stdout = 'active\n'
    response = flask_test_client.post('/system/autodeploy', json={'active': True})
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['active'] is True


def test_autodeploy_disable(flask_test_client, mocker):
    """POST /system/autodeploy disables the service."""
    mock_run = mocker.patch('routes.subprocess.run')
    mock_run.return_value.stdout = 'inactive\n'
    response = flask_test_client.post('/system/autodeploy', json={'active': False})
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['active'] is False


# --- Karaoke Nerds Search Tests ---

def test_kn_search_requires_query(flask_test_client):
    """POST /karaoke-nerds/search without query returns 400."""
    response = flask_test_client.post('/karaoke-nerds/search',
        data=json.dumps({}),
        content_type='application/json')
    assert response.status_code == 400


def test_kn_search_short_query(flask_test_client):
    """POST /karaoke-nerds/search with 1-char query returns 400."""
    response = flask_test_client.post('/karaoke-nerds/search',
        data=json.dumps({"query": "a"}),
        content_type='application/json')
    assert response.status_code == 400


@patch('karaoke_nerds.requests.get')
def test_kn_search_returns_results(mock_get, flask_test_client):
    """POST /karaoke-nerds/search returns parsed results."""
    from unittest.mock import MagicMock
    mock_resp = MagicMock()
    mock_resp.text = """
    <table class="table"><tbody>
        <tr class="group">
            <td><a>Test Song</a></td>
            <td><a>Test Artist</a></td>
            <td><a href="#">1 Brand</a></td>
        </tr>
        <tr class="details d-none">
            <td colspan="30"><ul class="list-group">
                <li class="track list-group-item d-flex p-0">
                    <a class="pr-1">Brand Name</a>
                    <div class="ml-auto">
                        <a href="https://www.youtube.com/watch?v=test123"><img class="web"></a>
                        <a><span class="badge badge-primary badge-pill">BN</span></a>
                    </div>
                </li>
            </ul></td>
        </tr>
    </tbody></table>
    """
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    response = flask_test_client.post('/karaoke-nerds/search',
        data=json.dumps({"query": "test song"}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert len(data) == 1
    assert data[0]["title"] == "Test Song"
    assert len(data[0]["tracks"]) == 1


@patch('karaoke_nerds.requests.get')
def test_kn_search_handles_error(mock_get, flask_test_client):
    """POST /karaoke-nerds/search returns empty on network error."""
    mock_get.side_effect = Exception("Connection refused")

    response = flask_test_client.post('/karaoke-nerds/search',
        data=json.dumps({"query": "test song"}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data == []


def test_kn_get_config(flask_test_client):
    """GET /karaoke-nerds/config returns the two priority lists + aliases."""
    response = flask_test_client.get('/karaoke-nerds/config')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "priority_community" in data
    assert isinstance(data["priority_community"], list)
    assert "priority_commercial" in data
    assert isinstance(data["priority_commercial"], list)
    assert "aliases" in data


def test_kn_set_config(flask_test_client, flask_app, tmp_media_dir):
    """POST /karaoke-nerds/config saves both priority lists, uppercased + trimmed."""
    response = flask_test_client.post('/karaoke-nerds/config',
        data=json.dumps({"priority_community": [" lc ", "cc"],
                         "priority_commercial": ["kv", "sf"]}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["priority_community"] == ["LC", "CC"]
    assert data["priority_commercial"] == ["KV", "SF"]
    assert flask_app.kj_config["kn_priority_community"] == ["LC", "CC"]
    assert flask_app.kj_config["kn_priority_commercial"] == ["KV", "SF"]


def test_kn_set_config_invalid(flask_test_client):
    """POST /karaoke-nerds/config with non-list returns 400."""
    response = flask_test_client.post('/karaoke-nerds/config',
        data=json.dumps({"priority_community": "not a list",
                         "priority_commercial": []}),
        content_type='application/json')
    assert response.status_code == 400


def test_kn_set_config_empty_body(flask_test_client):
    """POST /karaoke-nerds/config without the two list keys returns 400."""
    response = flask_test_client.post('/karaoke-nerds/config',
        data=json.dumps({}),
        content_type='application/json')
    assert response.status_code == 400


# --- YouTube Search ---

@patch('youtube_search.search')
def test_yt_search_returns_results(mock_search, flask_test_client):
    """POST /youtube/search returns search results."""
    mock_search.return_value = [
        {'id': 'abc', 'title': 'Test Song Karaoke', 'channel': 'Singer',
         'duration': 200, 'duration_str': '3:20', 'view_count': 50000,
         'view_count_str': '50.0K', 'url': 'https://www.youtube.com/watch?v=abc'},
    ]
    response = flask_test_client.post('/youtube/search',
        data=json.dumps({'query': 'test song'}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert len(data) == 1
    assert data[0]['id'] == 'abc'


@patch('youtube_search.search')
def test_yt_search_empty_results(mock_search, flask_test_client):
    """POST /youtube/search with no matches returns empty array."""
    mock_search.return_value = []
    response = flask_test_client.post('/youtube/search',
        data=json.dumps({'query': 'xyznonesense'}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data == []


def test_yt_search_short_query(flask_test_client):
    """POST /youtube/search with too-short query returns 400."""
    response = flask_test_client.post('/youtube/search',
        data=json.dumps({'query': 'a'}),
        content_type='application/json')
    assert response.status_code == 400


def test_yt_search_empty_query(flask_test_client):
    """POST /youtube/search with empty query returns 400."""
    response = flask_test_client.post('/youtube/search',
        data=json.dumps({'query': ''}),
        content_type='application/json')
    assert response.status_code == 400


def test_yt_search_no_body(flask_test_client):
    """POST /youtube/search with no body returns 400."""
    response = flask_test_client.post('/youtube/search',
        content_type='application/json')
    assert response.status_code == 400


# --- Bug fix: stop uses ensure_karaoke_released ---

def test_control_stop_calls_stop_karaoke(flask_test_client, flask_app, mocker):
    """POST /control stop calls stop_karaoke() and fades in filler."""
    flask_app.vlc.enabled = True
    flask_app.vlc.karaoke_active = True
    flask_app.vlc.current_playing_path = "/some/path.mp4"
    stop_mock = mocker.patch.object(flask_app.vlc, 'stop_karaoke')
    mocker.patch.object(flask_app.vlc, 'fade_in_filler')

    response = flask_test_client.post('/control',
        data=json.dumps({"action": "stop"}),
        content_type='application/json')
    assert response.status_code == 200
    stop_mock.assert_called_once()


def test_control_fadeout(flask_test_client):
    """POST /control with fadeout action returns success immediately."""
    response = flask_test_client.post('/control',
        data=json.dumps({"action": "fadeout"}),
        content_type='application/json')
    assert response.status_code == 200


def test_control_fadeout_delegates_to_coordinator(flask_test_client, flask_app, mocker):
    """POST /control fadeout delegates to coordinator.fadeout (polymorphic
    across renderers; implementation tested in player-specific suites)."""
    flask_app.vlc.enabled = True
    flask_app.vlc.karaoke_active = True

    fadeout_mock = mocker.patch.object(flask_app.vlc, 'fadeout')

    response = flask_test_client.post('/control',
        data=json.dumps({"action": "fadeout"}),
        content_type='application/json')
    assert response.status_code == 200
    fadeout_mock.assert_called_once_with(duration_s=3.0)


# --- Bug fix: filler music skips playback during karaoke ---

def test_filler_music_set_skips_play_during_karaoke(flask_test_client, flask_app, tmp_media_dir, mocker):
    """POST /filler_music queues track but skips pl_play when karaoke is active."""
    (tmp_media_dir / "song.mp3").write_text("fake audio")
    flask_app.vlc.enabled = True
    flask_app.vlc.karaoke_active = True

    send_mock = mocker.patch.object(flask_app.vlc, 'send_command',
        return_value={"state": "stopped"})

    response = flask_test_client.post('/filler_music',
        data=json.dumps({"track_name": "song.mp3"}),
        content_type='application/json')
    assert response.status_code == 200
    assert flask_app.vlc.current_filler_track == "song.mp3"

    # Should have enqueued but NOT called pl_play
    commands = [str(c) for c in send_mock.call_args_list]
    assert any("in_enqueue" in c for c in commands)
    assert not any("pl_play" in c for c in commands)


def test_filler_music_set_plays_when_karaoke_inactive(flask_test_client, flask_app, tmp_media_dir, mocker):
    """POST /filler_music starts playback when karaoke is not active."""
    (tmp_media_dir / "song.mp3").write_text("fake audio")
    flask_app.vlc.enabled = True
    flask_app.vlc.karaoke_active = False

    send_mock = mocker.patch.object(flask_app.vlc, 'send_command',
        return_value={"state": "playing", "length": 300})

    response = flask_test_client.post('/filler_music',
        data=json.dumps({"track_name": "song.mp3"}),
        content_type='application/json')
    assert response.status_code == 200

    # Should have called pl_play
    commands = [str(c) for c in send_mock.call_args_list]
    assert any("pl_play" in c for c in commands)


# --- Bug fix: play route passes display_path to play_video ---

def test_play_passes_display_path_and_overlay(flask_test_client, flask_app, tmp_media_dir, mocker):
    """POST /play passes display_path and overlay_manager to play_video."""
    media_dir = tmp_media_dir / "media"
    test_file = media_dir / "song.mp4"
    test_file.write_text("fake video")

    flask_app.vlc.enabled = True
    play_mock = mocker.patch.object(flask_app.vlc, 'play_video')

    response = flask_test_client.post('/play',
        data=json.dumps({"file_path": str(test_file)}),
        content_type='application/json')
    assert response.status_code == 200

    # play_video should NOT have been called directly (it's threaded)
    # but we can check the Thread was created with the right kwargs
    # Since play_video is mocked, it captures the call from the thread
    import time
    time.sleep(0.2)  # let the thread call the mock
    play_mock.assert_called_once()
    kwargs = play_mock.call_args[1]
    assert 'display_path' in kwargs
    assert 'overlay_manager' in kwargs


def test_play_zip_mpv_renderer_plays_cdg_with_audio_file(
    flask_test_client, flask_app, tmp_media_dir, mocker
):
    """On the mpv renderer a CDG zip is played as the .cdg with the mp3 as an
    external audio track — handing mpv the mp3 alone renders no graphics."""
    media_dir = tmp_media_dir / "media"
    zip_file = media_dir / "song.zip"
    zip_file.write_text("zip")
    mp3 = str(media_dir / "x.mp3")
    cdg = str(media_dir / "x.cdg")

    flask_app.vlc.enabled = True
    flask_app.vlc.render_mode = 'mpv'
    mocker.patch.object(flask_app.zip_playback, 'extract_and_get_mp3', return_value=mp3)
    mocker.patch.object(flask_app.zip_playback, 'current_cdg_path', return_value=cdg)
    play_mock = mocker.patch.object(flask_app.vlc, 'play_video')

    response = flask_test_client.post('/play',
        data=json.dumps({"file_path": str(zip_file)}),
        content_type='application/json')
    assert response.status_code == 200

    import time
    time.sleep(0.2)
    play_mock.assert_called_once()
    args, kwargs = play_mock.call_args
    assert args[0] == cdg
    assert kwargs['audio_file'] == mp3
    # The display path stays the original zip so the UI shows the song name.
    assert kwargs['display_path'] == str(zip_file)


def test_play_zip_vlc_renderer_plays_mp3_without_audio_file(
    flask_test_client, flask_app, tmp_media_dir, mocker
):
    """On VLC a CDG zip is played as the mp3 (VLC auto-discovers the sibling
    .cdg); no external audio file is attached."""
    media_dir = tmp_media_dir / "media"
    zip_file = media_dir / "song.zip"
    zip_file.write_text("zip")
    mp3 = str(media_dir / "x.mp3")

    flask_app.vlc.enabled = True
    flask_app.vlc.render_mode = 'vlc'
    mocker.patch.object(flask_app.zip_playback, 'extract_and_get_mp3', return_value=mp3)
    cdg_mock = mocker.patch.object(flask_app.zip_playback, 'current_cdg_path', return_value=str(media_dir / "x.cdg"))
    play_mock = mocker.patch.object(flask_app.vlc, 'play_video')

    response = flask_test_client.post('/play',
        data=json.dumps({"file_path": str(zip_file)}),
        content_type='application/json')
    assert response.status_code == 200

    import time
    time.sleep(0.2)
    play_mock.assert_called_once()
    args, kwargs = play_mock.call_args
    assert args[0] == mp3
    assert kwargs['audio_file'] is None
    cdg_mock.assert_not_called()


# --- Volume persistence ---

def test_status_includes_volume(flask_test_client, flask_app):
    """GET /status includes karaoke_volume and filler_volume."""
    flask_app.vlc.karaoke_volume = 180
    flask_app.vlc.filler_volume = 75

    response = flask_test_client.get('/status')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['karaoke_volume'] == 180
    assert data['filler_volume'] == 75


def test_volume_schedules_debounced_save(flask_test_client, flask_app, mocker):
    """POST /volume schedules a debounced save (does not write immediately)."""
    save_mock = mocker.patch('routes.save_config_value')

    response = flask_test_client.post('/volume',
        data=json.dumps({"target": "karaoke", "level": 180}),
        content_type='application/json')
    assert response.status_code == 200

    # save_config_value should NOT have been called yet (2s debounce)
    save_mock.assert_not_called()

    # But a timer should be pending
    with routes_mod._volume_save_lock:
        assert routes_mod._volume_save_timer is not None


def test_do_save_volumes_writes_both_keys(flask_app, mocker):
    """_do_save_volumes calls save_config_value for both volume keys."""
    save_mock = mocker.patch('routes.save_config_value')
    flask_app.vlc.karaoke_volume = 190
    flask_app.vlc.filler_volume = 60

    routes_mod._do_save_volumes(flask_app.vlc)

    assert save_mock.call_count == 2
    save_mock.assert_any_call('karaoke_volume', 190)
    save_mock.assert_any_call('filler_volume', 60)


def test_volume_updates_vlc_state(flask_test_client, flask_app):
    """POST /volume updates the VLCManager volume attribute."""
    flask_test_client.post('/volume',
        data=json.dumps({"target": "karaoke", "level": 175}),
        content_type='application/json')
    assert flask_app.vlc.karaoke_volume == 175

    flask_test_client.post('/volume',
        data=json.dumps({"target": "filler", "level": 55}),
        content_type='application/json')
    assert flask_app.vlc.filler_volume == 55


def test_status_includes_volume_with_vlc_enabled(flask_test_client, flask_app, mocker):
    """GET /status with VLC enabled also includes volume fields."""
    flask_app.vlc.enabled = True
    flask_app.vlc.karaoke_volume = 210
    flask_app.vlc.filler_volume = 90
    mocker.patch.object(flask_app.vlc, 'send_command',
        return_value={"state": "playing", "time": 10, "length": 100})

    response = flask_test_client.get('/status')
    data = json.loads(response.data)
    assert data['karaoke_volume'] == 210
    assert data['filler_volume'] == 90


class TestDivebarDownloadFilename:
    """Server-side filename construction for /divebar/download."""

    def test_uses_structured_fields(self, flask_test_client, flask_app):
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/x.mp4"):
            resp = flask_test_client.post('/divebar/download', json={
                "file_id": "abc123",
                "artist": "Queen",
                "title": "Bohemian Rhapsody",
                "brand_code": "WTF",
            })
        assert resp.status_code == 200
        items = flask_app.download_queue['items']
        assert items[-1]['title'] == "WTF - Queen - Bohemian Rhapsody.mp4"
        assert items[-1]['divebar_file_id'] == "abc123"

    def test_falls_back_to_db_when_brand_missing(self, flask_test_client, flask_app):
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/x.mp4"):
            resp = flask_test_client.post('/divebar/download', json={
                "file_id": "abc",
                "artist": "Queen",
                "title": "Bohemian Rhapsody",
            })
        assert resp.status_code == 200
        items = flask_app.download_queue['items']
        assert items[-1]['title'] == "DB - Queen - Bohemian Rhapsody.mp4"

    def test_falls_back_to_file_id_when_no_metadata(self, flask_test_client, flask_app):
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/x.mp4"):
            resp = flask_test_client.post('/divebar/download', json={"file_id": "abc"})
        assert resp.status_code == 200
        items = flask_app.download_queue['items']
        assert items[-1]['title'] == "divebar-abc.mp4"

    def test_zip_url_produces_zip_extension(self, flask_test_client, flask_app):
        # A CDG+MP3 zip mirror file must land on disk as .zip, not .mp4, or the
        # playability gate misclassifies it as video and rejects the download.
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/CKK%20-%20Incubus%20-%20Admiration.zip"):
            resp = flask_test_client.post('/divebar/download', json={
                "file_id": "z1", "artist": "Incubus", "title": "Admiration",
                "brand_code": "CKK",
            })
        assert resp.status_code == 200
        items = flask_app.download_queue['items']
        assert items[-1]['title'] == "CKK - Incubus - Admiration.zip"

    def test_zip_fallback_name_uses_zip_extension(self, flask_test_client, flask_app):
        with patch('routes.divebar.get_download_url',
                   return_value="https://storage.googleapis.com/m/y.zip"):
            resp = flask_test_client.post('/divebar/download', json={"file_id": "z2"})
        assert resp.status_code == 200
        items = flask_app.download_queue['items']
        assert items[-1]['title'] == "divebar-z2.zip"

    def test_drive_url_uses_format_for_extension(self, flask_test_client, flask_app):
        # Drive URLs carry no path extension — the client-supplied format wins.
        with patch('routes.divebar.get_download_url',
                   return_value="https://drive.google.com/uc?export=download&id=d1"):
            resp = flask_test_client.post('/divebar/download', json={
                "file_id": "d1", "artist": "A", "title": "B",
                "brand_code": "RSK", "format": "zip",
            })
        assert resp.status_code == 200
        items = flask_app.download_queue['items']
        assert items[-1]['title'] == "RSK - A - B.zip"


class TestDivebarRefresh:
    """On-demand pipeline refresh trigger: POST /divebar/refresh."""

    def test_503_when_not_configured(self, flask_test_client, flask_app):
        flask_app.kj_config.pop('divebar_api_url', None)
        resp = flask_test_client.post('/divebar/refresh')
        assert resp.status_code == 503

    def test_503_when_token_missing(self, flask_test_client, flask_app):
        flask_app.kj_config['divebar_api_url'] = 'http://test'
        flask_app.kj_config.pop('divebar_refresh_token', None)
        resp = flask_test_client.post('/divebar/refresh')
        assert resp.status_code == 503
        assert 'token' in json.loads(resp.data)['error'].lower()

    def test_200_on_success(self, flask_test_client, flask_app):
        flask_app.kj_config['divebar_api_url'] = 'http://test'
        flask_app.kj_config['divebar_refresh_token'] = 's3cret'
        with patch('routes.divebar.refresh',
                   return_value={"status": "ok", "triggered": ["divebar-mirror-daily"],
                                 "failed": []}) as mock_refresh:
            resp = flask_test_client.post('/divebar/refresh')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['triggered'] == ["divebar-mirror-daily"]
        # The route passes the app config through to the client helper.
        assert mock_refresh.call_args.kwargs['config'] is flask_app.kj_config

    def test_502_when_helper_reports_error(self, flask_test_client, flask_app):
        flask_app.kj_config['divebar_api_url'] = 'http://test'
        flask_app.kj_config['divebar_refresh_token'] = 's3cret'
        with patch('routes.divebar.refresh',
                   return_value={"status": "error", "message": "refresh token rejected (403)"}):
            resp = flask_test_client.post('/divebar/refresh')
        assert resp.status_code == 502
        assert '403' in json.loads(resp.data)['error']
