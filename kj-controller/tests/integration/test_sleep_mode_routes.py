"""Integration tests for sleep mode routes."""

import json
from unittest.mock import patch


class TestSleepModeRoutes:
    def test_get_sleep_mode_status(self, flask_test_client):
        resp = flask_test_client.get('/system/sleep-mode')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['active'] is False
        assert data['entering'] is False
        assert data['exiting'] is False

    @patch('sleep_mode.subprocess.run')
    def test_post_sleep_mode_enter(self, mock_run, flask_test_client, flask_app):
        mock_run.return_value = type('R', (), {
            'returncode': 0, 'stdout': 'ok', 'stderr': ''
        })()
        # Simulate the script creating the flag
        import sleep_mode
        orig_flag = sleep_mode.SLEEP_FLAG
        import tempfile, os
        tmp = tempfile.mkdtemp()
        flag_path = os.path.join(tmp, 'kj-sleep-mode')
        sleep_mode.SLEEP_FLAG = flag_path
        sleep_mode.SLEEP_STATE = os.path.join(tmp, 'state.json')

        def create_flag(*args, **kwargs):
            if args[0][0].endswith('sleep-enter.sh'):
                open(flag_path, 'w').close()
            return mock_run.return_value
        mock_run.side_effect = create_flag

        try:
            resp = flask_test_client.post(
                '/system/sleep-mode',
                data=json.dumps({'active': True}),
                content_type='application/json',
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['active'] is True
        finally:
            sleep_mode.SLEEP_FLAG = orig_flag
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestSleepModeGuards:
    """Playback routes should return 409 when sleep mode is active."""

    def _enter_sleep(self, flask_app):
        """Set sleep mode flag directly for testing."""
        import sleep_mode
        import tempfile, os
        tmp = tempfile.mkdtemp()
        flag = os.path.join(tmp, 'kj-sleep-mode')
        open(flag, 'w').close()
        self._orig_flag = sleep_mode.SLEEP_FLAG
        self._tmp = tmp
        sleep_mode.SLEEP_FLAG = flag

    def _cleanup(self):
        import sleep_mode, shutil
        sleep_mode.SLEEP_FLAG = self._orig_flag
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_play_blocked_during_sleep(self, flask_test_client, flask_app):
        self._enter_sleep(flask_app)
        try:
            resp = flask_test_client.post(
                '/play',
                data=json.dumps({'file_path': '/some/file.mp4'}),
                content_type='application/json',
            )
            assert resp.status_code == 409
            assert 'Sleep mode' in resp.get_json()['error']
        finally:
            self._cleanup()

    def test_download_blocked_during_sleep(self, flask_test_client, flask_app):
        self._enter_sleep(flask_app)
        try:
            resp = flask_test_client.post(
                '/download',
                data=json.dumps({'url': 'https://youtube.com/watch?v=test'}),
                content_type='application/json',
            )
            assert resp.status_code == 409
            assert 'Sleep mode' in resp.get_json()['error']
        finally:
            self._cleanup()

    def test_filler_music_blocked_during_sleep(self, flask_test_client, flask_app):
        self._enter_sleep(flask_app)
        try:
            resp = flask_test_client.post(
                '/filler_music',
                data=json.dumps({'track_name': 'test.mp3'}),
                content_type='application/json',
            )
            assert resp.status_code == 409
            assert 'Sleep mode' in resp.get_json()['error']
        finally:
            self._cleanup()

    def test_play_allowed_when_awake(self, flask_test_client, flask_app):
        """Play returns a normal error (not 409) when not in sleep mode."""
        resp = flask_test_client.post(
            '/play',
            data=json.dumps({'file_path': '/nonexistent/file.mp4'}),
            content_type='application/json',
        )
        # Should get a 400 or 404, NOT 409
        assert resp.status_code != 409
