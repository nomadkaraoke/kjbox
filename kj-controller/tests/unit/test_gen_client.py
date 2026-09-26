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


class TestStatusMapNewerGenStates:
    def test_needs_input_and_terminal_states(self):
        from gen_client import GenStatus, map_gen_status
        for s in ["awaiting_audio_selection", "awaiting_audio_edit", "awaiting_duration_confirm"]:
            assert map_gen_status(s) == GenStatus.NEEDS_INPUT
        for s in ["encoding", "packaging", "uploading", "render_pending_capacity"]:
            assert map_gen_status(s) == GenStatus.RENDERING
        assert map_gen_status("cancelled") == GenStatus.FAILED
        assert map_gen_status("prep_complete") == GenStatus.COMPLETE


class _Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data
        self.reason = "ERR"

    def json(self):
        return self._data


class TestSingerFlowCalls:
    def _client(self):
        from gen_client import GenClient
        return GenClient("https://api.example.com/", "admin-tok", "partner-secret")

    def test_partner_headers_and_session(self):
        c = self._client()
        with patch("gen_client.requests.request", return_value=_Resp(200, {"ok": 1})) as req:
            c.search_audio("sess-1", "Radiohead", "Creep")
        method, url = req.call_args.args
        headers = req.call_args.kwargs["headers"]
        assert (method, url) == ("POST", "https://api.example.com/api/audio-search/search-standalone")
        # gen stores custom request headers on jobs (readable by the singer) —
        # the partner secret must only ever go to /api/kjbox/*.
        assert "X-Kjbox-Secret" not in headers
        assert headers["Authorization"] == "Bearer sess-1"
        assert headers["X-Client-Id"] == "kjbox"
        assert "X-Admin-Token" not in headers          # singer calls never use admin power

    def test_create_from_search_is_public_minimal(self):
        c = self._client()
        with patch("gen_client.requests.request", return_value=_Resp(200, {"job_id": "j"})) as req:
            assert c.create_job_from_search("s", "ss", 2, "A", "T")["job_id"] == "j"
        body = req.call_args.kwargs["json"]
        assert body["is_private"] is False and body["requires_audio_edit"] is False
        assert body["review_mode"] == "auto" and body["selection_index"] == 2

    def test_errors_become_gen_api_error(self):
        from gen_client import GenApiError
        c = self._client()
        with patch("gen_client.requests.request", return_value=_Resp(401, {"detail": "invalid_code"})):
            with pytest.raises(GenApiError) as ei:
                c.verify_login_code("a@b.co", "000000")
        assert (ei.value.status, ei.value.detail) == (401, "invalid_code")
        with patch("gen_client.requests.request",
                   return_value=_Resp(402, {"detail": {"message": "Insufficient credits"}})):
            with pytest.raises(GenApiError) as ei:
                c.search_audio("s", "a", "t")
        assert ei.value.status == 402

    def test_network_failure(self):
        import requests
        from gen_client import GenApiError
        c = self._client()
        with patch("gen_client.requests.request", side_effect=requests.ConnectionError("down")):
            with pytest.raises(GenApiError) as ei:
                c.send_login_code("a@b.co")
        assert ei.value.status == 0

    def test_flow_needs_secret(self):
        from gen_client import GenClient
        assert GenClient("https://x", "t").singer_flow_configured() is False
        assert self._client().singer_flow_configured() is True


class TestPartnerSecretScope:
    def test_secret_only_on_partner_paths(self):
        from gen_client import GenClient
        c = GenClient("https://api.example.com", "admin-tok", "partner-secret")
        with patch("gen_client.requests.request", return_value=_Resp(200, {})) as req:
            c.send_login_code("a@b.co")
            c.grant_show_credit("s", "k" * 64, only_if_empty=True)
            c.create_job_from_url("s", "https://youtu.be/x", "A", "T")
        partner1, partner2, job = [call.kwargs["headers"] for call in req.call_args_list]
        assert partner1["X-Kjbox-Secret"] == partner2["X-Kjbox-Secret"] == "partner-secret"
        assert "X-Kjbox-Secret" not in job
        assert req.call_args_list[1].kwargs["json"]["only_if_empty"] is True
