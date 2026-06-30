"""Tests for the Divebar karaoke catalog client."""

import json
from unittest.mock import patch, MagicMock

import pytest

import divebar


class TestSearch:
    """Tests for divebar.search()."""

    def test_returns_empty_when_no_api_url(self):
        result = divebar.search("queen", config={})
        assert result == []

    def test_returns_empty_when_api_url_blank(self):
        result = divebar.search("queen", config={"divebar_api_url": ""})
        assert result == []

    @patch("divebar.requests.post")
    def test_search_returns_grouped_results(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "ok",
            "results": [
                {"file_id": "a1", "artist": "Queen", "title": "Bohemian Rhapsody",
                 "brand": "WTF Karaoke", "format": "mp4", "file_size": 45000000},
                {"file_id": "a2", "artist": "Queen", "title": "Bohemian Rhapsody",
                 "brand": "Nomad Karaoke", "format": "cdg", "file_size": 200000},
            ],
        }
        mock_post.return_value = mock_resp

        results = divebar.search("bohemian rhapsody", config={"divebar_api_url": "http://test"})

        assert len(results) == 1
        assert results[0]["artist"] == "Queen"
        assert results[0]["title"] == "Bohemian Rhapsody"
        assert len(results[0]["tracks"]) == 2
        assert results[0]["tracks"][0]["brand"] == "WTF Karaoke"
        assert results[0]["tracks"][1]["brand"] == "Nomad Karaoke"

    @patch("divebar.requests.post")
    def test_search_handles_timeout(self, mock_post):
        import requests as req
        mock_post.side_effect = req.Timeout()

        results = divebar.search("queen", config={"divebar_api_url": "http://test"})
        assert results == []

    @patch("divebar.requests.post")
    def test_search_handles_api_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "error", "message": "internal error"}
        mock_post.return_value = mock_resp

        results = divebar.search("queen", config={"divebar_api_url": "http://test"})
        assert results == []


class TestLookupKnIds:
    """Tests for divebar.lookup_kn_ids()."""

    def test_returns_empty_when_no_api_url(self):
        result = divebar.lookup_kn_ids([123, 456], config={})
        assert result == {}

    def test_returns_empty_when_no_ids(self):
        result = divebar.lookup_kn_ids([], config={"divebar_api_url": "http://test"})
        assert result == {}

    @patch("divebar.requests.post")
    def test_lookup_returns_matches(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "ok",
            "matches": {
                "123": [{"file_id": "abc", "brand": "WTF", "format": "mp4"}],
            },
        }
        mock_post.return_value = mock_resp

        result = divebar.lookup_kn_ids([123, 456], config={"divebar_api_url": "http://test"})
        assert "123" in result
        assert len(result["123"]) == 1


class TestGetDownloadUrl:
    """Tests for divebar.get_download_url()."""

    def test_returns_none_when_no_api_url(self):
        result = divebar.get_download_url("abc", config={})
        assert result is None

    @patch("divebar.requests.post")
    def test_returns_download_url(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "ok",
            "download_url": "https://drive.google.com/uc?export=download&id=abc",
        }
        mock_post.return_value = mock_resp

        result = divebar.get_download_url("abc", config={"divebar_api_url": "http://test"})
        assert "abc" in result


class TestGroupResults:
    """Tests for divebar._group_results()."""

    def test_groups_same_song_different_brands(self):
        results = [
            {"artist": "Queen", "title": "Bohemian Rhapsody", "brand": "A", "format": "mp4", "file_id": "1"},
            {"artist": "Queen", "title": "Bohemian Rhapsody", "brand": "B", "format": "cdg", "file_id": "2"},
            {"artist": "Queen", "title": "We Will Rock You", "brand": "A", "format": "mp4", "file_id": "3"},
        ]
        grouped = divebar._group_results(results)
        assert len(grouped) == 2
        bohemian = next(s for s in grouped if s["title"] == "Bohemian Rhapsody")
        assert len(bohemian["tracks"]) == 2

    def test_handles_missing_artist(self):
        results = [{"artist": None, "title": "Some Song", "brand": "X", "format": "mp4", "file_id": "1"}]
        grouped = divebar._group_results(results)
        assert len(grouped) == 1
        assert grouped[0]["artist"] == "Unknown"


class TestClassifyDownloadUrl:
    """Tests for divebar.classify_download_url() — surfaces fast-mirror vs Drive
    so the KJ knows whether an active download is GCS or Drive-backed."""

    def test_gcs_bucket_root(self):
        assert divebar.classify_download_url(
            "https://storage.googleapis.com/divebar-mirror/abc.mp4"
        ) == "gcs"

    def test_gcs_bucket_subdomain(self):
        # Virtual-host style: bucket-name.storage.googleapis.com/path
        assert divebar.classify_download_url(
            "https://divebar-mirror.storage.googleapis.com/abc.mp4"
        ) == "gcs"

    def test_drive_google_com(self):
        assert divebar.classify_download_url(
            "https://drive.google.com/uc?export=download&id=abc"
        ) == "drive"

    def test_drive_usercontent(self):
        # Drive sometimes redirects through this host for the actual blob
        assert divebar.classify_download_url(
            "https://drive.usercontent.google.com/download?id=abc"
        ) == "drive"

    def test_googleusercontent_subdomain(self):
        assert divebar.classify_download_url(
            "https://lh3.googleusercontent.com/foo"
        ) == "drive"

    def test_unknown_host_returns_none(self):
        assert divebar.classify_download_url("https://example.com/file.mp4") is None

    def test_empty_or_none(self):
        assert divebar.classify_download_url("") is None
        assert divebar.classify_download_url(None) is None

    def test_malformed_url_does_not_raise(self):
        # urlparse is forgiving — should not crash on garbage input
        assert divebar.classify_download_url("not a url") is None


class TestRefresh:
    """Tests for divebar.refresh()."""

    def test_error_when_no_api_url(self):
        result = divebar.refresh(config={"divebar_refresh_token": "t"})
        assert result["status"] == "error"
        assert "divebar_api_url" in result["message"]

    def test_error_when_no_token(self):
        result = divebar.refresh(config={"divebar_api_url": "http://test"})
        assert result["status"] == "error"
        assert "divebar_refresh_token" in result["message"]

    @patch("divebar.requests.post")
    def test_sends_action_and_token(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "ok", "triggered": ["divebar-mirror-daily"], "failed": [],
        }
        mock_post.return_value = mock_resp

        result = divebar.refresh(config={
            "divebar_api_url": "http://test",
            "divebar_refresh_token": "s3cret",
        })

        assert result["status"] == "ok"
        assert result["triggered"] == ["divebar-mirror-daily"]
        sent = mock_post.call_args.kwargs["json"]
        assert sent == {"action": "refresh", "token": "s3cret"}

    @patch("divebar.requests.post")
    def test_403_returns_token_rejected(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_post.return_value = mock_resp

        result = divebar.refresh(config={
            "divebar_api_url": "http://test",
            "divebar_refresh_token": "wrong",
        })
        assert result["status"] == "error"
        assert "403" in result["message"]

    @patch("divebar.requests.post")
    def test_network_error_returns_error_dict(self, mock_post):
        import requests as req
        mock_post.side_effect = req.RequestException("boom")

        result = divebar.refresh(config={
            "divebar_api_url": "http://test",
            "divebar_refresh_token": "t",
        })
        assert result["status"] == "error"
        assert "boom" in result["message"]


class TestFindSiblingAudio:
    """Tests for divebar.find_sibling_audio() — resolving the MP3 that belongs
    to a loose (un-zipped) CDG track, e.g. Sandell Karaoke."""

    # A search result mimicking Sandell's loose cdg+mp3 pair plus an unrelated
    # Nomad zip for the same song. Grouped exactly like divebar.search() returns.
    SDK_GROUP = [{
        "artist": "ABBA", "title": "Dancing Queen",
        "tracks": [
            {"file_id": "nomad_zip", "brand_code": "NOMAD", "format": "zip",
             "drive_path": "Nomad Karaoke/CDG/NOMAD-0520 - ABBA - Dancing Queen.zip"},
            {"file_id": "sdk_cdg", "brand_code": "SDK", "format": "cdg",
             "drive_path": "Sandell Karaoke/CDG/CDG #-B/(SDK) ABBA - Dancing Queen.cdg"},
            {"file_id": "sdk_mp3", "brand_code": "SDK", "format": "mp3",
             "drive_path": "Sandell Karaoke/CDG/CDG #-B/(SDK) ABBA - Dancing Queen.mp3"},
        ],
    }]

    @patch("divebar.search")
    def test_finds_sibling_mp3_by_brand_and_basename(self, mock_search):
        mock_search.return_value = self.SDK_GROUP
        sib = divebar.find_sibling_audio(
            "sdk_cdg", "ABBA", "Dancing Queen", "SDK",
            config={"divebar_api_url": "http://test"})
        assert sib is not None
        assert sib["file_id"] == "sdk_mp3"
        assert sib["format"] == "mp3"

    @patch("divebar.search")
    def test_returns_none_when_no_audio_sibling(self, mock_search):
        # Only the cdg exists in the mirror — no companion audio.
        mock_search.return_value = [{
            "artist": "ABBA", "title": "Dancing Queen",
            "tracks": [
                {"file_id": "sdk_cdg", "brand_code": "SDK", "format": "cdg",
                 "drive_path": "Sandell Karaoke/CDG/CDG #-B/(SDK) ABBA - Dancing Queen.cdg"},
            ],
        }]
        sib = divebar.find_sibling_audio(
            "sdk_cdg", "ABBA", "Dancing Queen", "SDK",
            config={"divebar_api_url": "http://test"})
        assert sib is None

    @patch("divebar.search")
    def test_does_not_match_audio_from_another_brand(self, mock_search):
        # A different brand has an mp3 for the same song; it must NOT be paired
        # with SDK's cdg (different basename + brand).
        mock_search.return_value = [{
            "artist": "ABBA", "title": "Dancing Queen",
            "tracks": [
                {"file_id": "sdk_cdg", "brand_code": "SDK", "format": "cdg",
                 "drive_path": "Sandell Karaoke/CDG/CDG #-B/(SDK) ABBA - Dancing Queen.cdg"},
                {"file_id": "other_mp3", "brand_code": "ESK", "format": "mp3",
                 "drive_path": "Easy Karaoke/(ESK) ABBA - Dancing Queen.mp3"},
            ],
        }]
        sib = divebar.find_sibling_audio(
            "sdk_cdg", "ABBA", "Dancing Queen", "SDK",
            config={"divebar_api_url": "http://test"})
        assert sib is None

    @patch("divebar.search")
    def test_returns_none_when_search_empty(self, mock_search):
        mock_search.return_value = []
        sib = divebar.find_sibling_audio(
            "sdk_cdg", "ABBA", "Dancing Queen", "SDK",
            config={"divebar_api_url": "http://test"})
        assert sib is None
