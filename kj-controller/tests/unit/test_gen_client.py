"""Tests for GenClient — gen API HTTP client."""

import pytest
from unittest.mock import patch

from gen_client import GenClient, GenStatus, map_gen_status


class TestGenStatus:
    def test_processing_states(self):
        for state in ["pending", "downloading", "separating_stage1", "separating_stage2",
                       "transcribing", "generating_screens"]:
            assert map_gen_status(state) == GenStatus.PROCESSING

    def test_awaiting_review_states(self):
        for state in ["awaiting_review", "in_review"]:
            assert map_gen_status(state) == GenStatus.AWAITING_REVIEW

    def test_rendering_states(self):
        for state in ["review_complete", "rendering_video", "generating_video", "instrumental_selected"]:
            assert map_gen_status(state) == GenStatus.RENDERING

    def test_complete(self):
        assert map_gen_status("complete") == GenStatus.COMPLETE

    def test_failed(self):
        assert map_gen_status("failed") == GenStatus.FAILED

    def test_unknown_defaults_to_processing(self):
        assert map_gen_status("unknown_state") == GenStatus.PROCESSING

    def test_terminal_set(self):
        assert GenStatus.COMPLETE in GenStatus.TERMINAL
        assert GenStatus.FAILED in GenStatus.TERMINAL
        assert GenStatus.PROCESSING not in GenStatus.TERMINAL

    def test_active_set(self):
        assert GenStatus.PROCESSING in GenStatus.ACTIVE
        assert GenStatus.AWAITING_REVIEW in GenStatus.ACTIVE
        assert GenStatus.RENDERING in GenStatus.ACTIVE
        assert GenStatus.COMPLETE not in GenStatus.ACTIVE


class TestGenClient:
    @pytest.fixture
    def client(self):
        return GenClient("https://api.example.com", "test-token")

    @patch('gen_client.requests.post')
    def test_create_job(self, mock_post, client):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"job_id": "abc123", "status": "pending"}
        mock_post.return_value.raise_for_status.return_value = None
        result = client.create_job("Queen", "Bohemian Rhapsody")
        assert result["job_id"] == "abc123"
        call_args = mock_post.call_args
        assert call_args[1]["headers"]["X-Admin-Token"] == "test-token"
        body = call_args[1]["json"]
        assert body["artist"] == "Queen"
        assert body["title"] == "Bohemian Rhapsody"
        assert body["auto_download"] is True

    @patch('gen_client.requests.post')
    def test_create_job_error(self, mock_post, client):
        mock_post.return_value.raise_for_status.side_effect = Exception("Server error")
        with pytest.raises(Exception):
            client.create_job("Queen", "Bohemian Rhapsody")

    @patch('gen_client.requests.get')
    def test_get_job_status(self, mock_get, client):
        mock_get.return_value.status_code = 200
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "status": "transcribing", "state_data": {}, "file_urls": {}
        }
        result = client.get_job_status("abc123")
        assert result["status"] == "transcribing"

    @patch('gen_client.requests.get')
    def test_get_download_url_found(self, mock_get, client):
        mock_get.return_value.status_code = 200
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "download_urls": {"finals": {
                "lossy_720p_mp4": "/api/jobs/abc123/download/finals/lossy_720p_mp4"
            }}
        }
        url = client.get_download_url("abc123")
        assert "lossy_720p_mp4" in url
        assert "token=test-token" in url

    @patch('gen_client.requests.get')
    def test_get_download_url_not_found(self, mock_get, client):
        mock_get.return_value.status_code = 200
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {"download_urls": {"finals": {}}}
        url = client.get_download_url("abc123")
        assert url is None

    @patch('gen_client.requests.get')
    def test_get_download_url_error(self, mock_get, client):
        mock_get.side_effect = Exception("Network error")
        url = client.get_download_url("abc123")
        assert url is None

    def test_url_trailing_slash_stripped(self):
        client = GenClient("https://api.example.com/", "token")
        assert client.api_url == "https://api.example.com"
