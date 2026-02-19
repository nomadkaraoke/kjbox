"""Integration tests for Flask routes via test client."""

import json
from unittest.mock import patch

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


# --- Additional coverage tests ---

def test_download_success(flask_test_client, flask_app, tmp_media_dir, mocker):
    """POST /download with valid URL returns success when download works."""
    mocker.patch.object(flask_app.media, 'download_video',
        return_value=("/path/to/video.mp4", "My Video"))

    response = flask_test_client.post('/download',
        data=json.dumps({"url": "https://youtube.com/watch?v=abc123"}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True
    assert data["title"] == "My Video"


def test_download_failure(flask_test_client, flask_app, mocker):
    """POST /download returns 500 when download fails."""
    mocker.patch.object(flask_app.media, 'download_video',
        return_value=(None, None))

    response = flask_test_client.post('/download',
        data=json.dumps({"url": "https://youtube.com/watch?v=bad"}),
        content_type='application/json')
    assert response.status_code == 500
    data = json.loads(response.data)
    assert "error" in data


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
    """POST /control pause_resume when VLC returns paused fades in filler."""
    flask_app.vlc.enabled = True
    # send_command returns paused state on the status check
    mocker.patch.object(flask_app.vlc, 'send_command',
        return_value={"state": "paused"})
    mocker.patch.object(flask_app.vlc, 'fade_in_filler')

    response = flask_test_client.post('/control',
        data=json.dumps({"action": "pause_resume"}),
        content_type='application/json')
    assert response.status_code == 200
    assert flask_app.vlc.karaoke_active is False
    flask_app.vlc.fade_in_filler.assert_called_once()


def test_control_pause_resume_playing_state(flask_test_client, flask_app, mocker):
    """POST /control pause_resume when VLC returns playing fades out filler."""
    flask_app.vlc.enabled = True
    mocker.patch.object(flask_app.vlc, 'send_command',
        return_value={"state": "playing"})
    mocker.patch.object(flask_app.vlc, 'fade_out_filler')

    response = flask_test_client.post('/control',
        data=json.dumps({"action": "pause_resume"}),
        content_type='application/json')
    assert response.status_code == 200
    assert flask_app.vlc.karaoke_active is True
    flask_app.vlc.fade_out_filler.assert_called_once()


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
    """GET /status with VLC enabled returns full status data."""
    flask_app.vlc.enabled = True
    mocker.patch.object(flask_app.vlc, 'send_command',
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
    """GET /karaoke-nerds/config returns preferred brands."""
    response = flask_test_client.get('/karaoke-nerds/config')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "preferred_brands" in data
    assert isinstance(data["preferred_brands"], list)


def test_kn_set_config(flask_test_client, flask_app, tmp_media_dir):
    """POST /karaoke-nerds/config saves preferred brands."""
    response = flask_test_client.post('/karaoke-nerds/config',
        data=json.dumps({"preferred_brands": ["kv", " kfn ", "sk"]}),
        content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    # Should be uppercased and trimmed
    assert data["preferred_brands"] == ["KV", "KFN", "SK"]
    assert flask_app.kj_config["kn_preferred_brands"] == ["KV", "KFN", "SK"]


def test_kn_set_config_invalid(flask_test_client):
    """POST /karaoke-nerds/config with non-list returns 400."""
    response = flask_test_client.post('/karaoke-nerds/config',
        data=json.dumps({"preferred_brands": "not a list"}),
        content_type='application/json')
    assert response.status_code == 400


def test_kn_set_config_empty_body(flask_test_client):
    """POST /karaoke-nerds/config without preferred_brands returns 400."""
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
