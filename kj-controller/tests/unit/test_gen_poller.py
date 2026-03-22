"""Tests for GenPoller — background gen job status polling."""

from unittest.mock import MagicMock, patch

import pytest

from gen_poller import GenPoller


@pytest.fixture
def mock_gen_client():
    return MagicMock()


@pytest.fixture
def mock_rotation():
    return MagicMock()


@pytest.fixture
def mock_media():
    return MagicMock()


@pytest.fixture
def poller(mock_gen_client, mock_rotation, mock_media):
    return GenPoller(mock_gen_client, mock_rotation, mock_media, "/tmp/downloads", poll_interval=1)


class TestPollOnce:
    def test_no_active_entries(self, poller, mock_rotation):
        mock_rotation.store.get_active_gen_entries.return_value = []
        poller.poll_once()
        mock_rotation.store.get_active_gen_entries.assert_called_once()

    def test_updates_status(self, poller, mock_gen_client, mock_rotation):
        mock_rotation.store.get_active_gen_entries.return_value = [
            {"id": 1, "gen_job_id": "job-1", "gen_status": "processing", "song_artist": "Song - Artist"}
        ]
        mock_gen_client.get_job_status.return_value = {"status": "awaiting_review"}
        poller.poll_once()
        mock_rotation.set_gen_status.assert_called_once_with(1, "job-1", "awaiting_review")

    def test_no_update_when_status_same(self, poller, mock_gen_client, mock_rotation):
        mock_rotation.store.get_active_gen_entries.return_value = [
            {"id": 1, "gen_job_id": "job-1", "gen_status": "processing", "song_artist": "Song - Artist"}
        ]
        mock_gen_client.get_job_status.return_value = {"status": "transcribing"}  # maps to processing
        poller.poll_once()
        mock_rotation.set_gen_status.assert_not_called()

    def test_handles_api_error(self, poller, mock_gen_client, mock_rotation):
        mock_rotation.store.get_active_gen_entries.return_value = [
            {"id": 1, "gen_job_id": "job-1", "gen_status": "processing", "song_artist": "Song - Artist"}
        ]
        mock_gen_client.get_job_status.side_effect = Exception("API error")
        poller.poll_once()  # Should not raise


class TestHandleComplete:
    def test_downloads_and_links(self, poller, mock_gen_client, mock_rotation, mock_media):
        entry = {"id": 1, "gen_job_id": "job-123", "gen_status": "rendering",
                 "song_artist": "Bohemian Rhapsody - Queen"}

        mock_rotation.store.get_active_gen_entries.return_value = [entry]
        mock_gen_client.get_job_status.return_value = {"status": "complete"}
        mock_gen_client.get_download_url.return_value = "https://api.example.com/download"
        mock_media.download_from_url.return_value = ("/tmp/downloads/song.mp4", "song.mp4")

        poller.poll_once()

        mock_gen_client.get_download_url.assert_called_once_with("job-123")
        mock_media.download_from_url.assert_called_once()
        mock_rotation.complete_gen_job.assert_called_once_with("job-123", "/tmp/downloads/song.mp4")

    def test_handles_no_download_url(self, poller, mock_gen_client, mock_rotation, mock_media):
        entry = {"id": 1, "gen_job_id": "job-123", "gen_status": "rendering",
                 "song_artist": "Song - Artist"}

        mock_rotation.store.get_active_gen_entries.return_value = [entry]
        mock_gen_client.get_job_status.return_value = {"status": "complete"}
        mock_gen_client.get_download_url.return_value = None

        poller.poll_once()
        mock_media.download_from_url.assert_not_called()

    def test_handles_download_failure(self, poller, mock_gen_client, mock_rotation, mock_media):
        entry = {"id": 1, "gen_job_id": "job-123", "gen_status": "rendering",
                 "song_artist": "Song - Artist"}

        mock_rotation.store.get_active_gen_entries.return_value = [entry]
        mock_gen_client.get_job_status.return_value = {"status": "complete"}
        mock_gen_client.get_download_url.return_value = "https://api.example.com/download"
        mock_media.download_from_url.return_value = (None, None)

        poller.poll_once()
        mock_rotation.complete_gen_job.assert_not_called()


class TestStartStop:
    def test_start_stop(self, poller):
        poller.start()
        assert poller._thread.is_alive()
        poller.stop()
        assert not poller._thread.is_alive()

    def test_start_idempotent(self, poller):
        poller.start()
        thread1 = poller._thread
        poller.start()
        assert poller._thread is thread1
        poller.stop()
