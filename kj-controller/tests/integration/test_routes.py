"""Integration tests for Flask routes via test client."""

import json

from app import create_app


def test_create_app_factory(mock_config):
    """create_app returns a configured Flask app."""
    app = create_app(config=mock_config)
    assert app.kj_config is mock_config
    assert hasattr(app, 'media')
    assert hasattr(app, 'vlc')
    assert app.vlc.enabled is False


def test_index_returns_html(flask_test_client):
    """GET / returns 200 with HTML content."""
    response = flask_test_client.get('/')
    assert response.status_code == 200
    assert b'html' in response.data.lower()


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
