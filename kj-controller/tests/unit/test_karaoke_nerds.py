"""Unit tests for karaoke_nerds.py HTML parsing."""

from unittest.mock import MagicMock, patch

import pytest

from karaoke_nerds import _clean_youtube_url, _parse_single_track, parse_results, search

# Minimal fixture: 3 songs, one with multiple tracks including community
FIXTURE_HTML = """
<html><body>
<table class="table table-sm">
<thead><tr><th>Title</th><th>Artist</th><th>Tracks</th></tr></thead>
<tbody>
    <tr class="group">
        <td><a href="/Song/Test/Artist1/">Test Song</a></td>
        <td><a href="/Artist/Artist1/">Artist One</a></td>
        <td><a class="details-link" href="#">2 Brands &gt;&gt;</a></td>
    </tr>
    <tr class="details d-none">
        <td colspan="30">
            <ul class="list-group">
                <li class="track list-group-item d-flex p-0">
                    <a class="pr-1" href="/Song/Test/Artist1/KV/">Karaoke Version</a>
                    <div class="ml-auto">
                        <a href="https://www.youtube.com/watch?v=abc123&amp;list=PLtest" target="_blank">
                            <span title="You can watch this version online"><img class="web" src="/Content/Images/globe.svg"></span>
                        </a>
                        <a href="/Song/Test/Artist1/KV/">
                            <span class="badge badge-primary badge-pill">KV</span>
                        </a>
                    </div>
                </li>
                <li class="track list-group-item d-flex p-0">
                    <a class="pr-1" href="/Song/Test/Artist1/OBSK/">ObsKure Karaoke</a>
                    <div class="ml-auto">
                        <a href="https://www.youtube.com/watch?v=def456&amp;list=PLother" target="_blank">
                            <span title="You can watch this version online"><img class="web" src="/Content/Images/globe.svg"></span>
                        </a>
                        <a href="/Song/Test/Artist1/OBSK/">
                            <span class="badge badge-primary badge-pill">
                                OBSK<img class="check" src="/Content/Images/check.svg" title="Global Karaoke Community">
                            </span>
                        </a>
                    </div>
                </li>
            </ul>
        </td>
    </tr>
    <tr class="group">
        <td><a href="/Song/Another/Artist2/">Another Song</a></td>
        <td><a href="/Artist/Artist2/">Artist Two</a></td>
        <td><a class="details-link" href="#">1 Brand &gt;&gt;</a></td>
    </tr>
    <tr class="details d-none">
        <td colspan="30">
            <ul class="list-group">
                <li class="track list-group-item d-flex p-0">
                    <a class="pr-1" href="/Song/Another/Artist2/SK/">Sing King</a>
                    <div class="ml-auto">
                        <a href="https://www.youtube.com/watch?v=ghi789" target="_blank">
                            <span title="You can watch this version online"><img class="web" src="/Content/Images/globe.svg"></span>
                        </a>
                        <a href="/Song/Another/Artist2/SK/">
                            <span class="badge badge-primary badge-pill">SK</span>
                        </a>
                    </div>
                </li>
            </ul>
        </td>
    </tr>
</tbody>
</table>
</body></html>
"""


class TestParseResults:
    def test_extracts_songs(self):
        songs = parse_results(FIXTURE_HTML)
        assert len(songs) == 2
        assert songs[0]["title"] == "Test Song"
        assert songs[0]["artist"] == "Artist One"
        assert songs[1]["title"] == "Another Song"
        assert songs[1]["artist"] == "Artist Two"

    def test_extracts_tracks(self):
        songs = parse_results(FIXTURE_HTML)
        assert len(songs[0]["tracks"]) == 2
        assert len(songs[1]["tracks"]) == 1

    def test_track_brand_info(self):
        songs = parse_results(FIXTURE_HTML)
        kv = songs[0]["tracks"][0]
        assert kv["brand_name"] == "Karaoke Version"
        assert kv["brand_code"] == "KV"

    def test_track_youtube_url(self):
        songs = parse_results(FIXTURE_HTML)
        kv = songs[0]["tracks"][0]
        # Should strip &list= param
        assert kv["youtube_url"] == "https://www.youtube.com/watch?v=abc123"

    def test_community_detection(self):
        songs = parse_results(FIXTURE_HTML)
        kv = songs[0]["tracks"][0]
        obsk = songs[0]["tracks"][1]
        assert kv["is_community"] is False
        assert obsk["is_community"] is True
        assert obsk["brand_code"] == "OBSK"

    def test_non_community_single_track(self):
        songs = parse_results(FIXTURE_HTML)
        sk = songs[1]["tracks"][0]
        assert sk["brand_name"] == "Sing King"
        assert sk["brand_code"] == "SK"
        assert sk["is_community"] is False
        # No &list= param to strip
        assert sk["youtube_url"] == "https://www.youtube.com/watch?v=ghi789"

    def test_empty_html(self):
        assert parse_results("") == []
        assert parse_results("<html><body></body></html>") == []

    def test_no_table(self):
        assert parse_results("<html><body><p>No results</p></body></html>") == []

    def test_empty_tbody(self):
        html = '<table><thead></thead><tbody></tbody></table>'
        assert parse_results(html) == []

    def test_malformed_rows(self):
        """Rows without proper cells should be skipped."""
        html = """
        <table><tbody>
            <tr class="group"><td></td></tr>
            <tr class="details d-none"><td></td></tr>
        </tbody></table>
        """
        songs = parse_results(html)
        assert len(songs) == 0


class TestCleanYoutubeUrl:
    def test_strips_list_param(self):
        url = "https://www.youtube.com/watch?v=abc123&list=PLtest123"
        assert _clean_youtube_url(url) == "https://www.youtube.com/watch?v=abc123"

    def test_no_list_param(self):
        url = "https://www.youtube.com/watch?v=abc123"
        assert _clean_youtube_url(url) == "https://www.youtube.com/watch?v=abc123"

    def test_list_in_middle(self):
        url = "https://www.youtube.com/watch?v=abc&list=PLtest&index=1"
        assert _clean_youtube_url(url) == "https://www.youtube.com/watch?v=abc&index=1"


class TestSearch:
    @patch('karaoke_nerds.requests.get')
    def test_successful_search(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = FIXTURE_HTML
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        results = search("test song")
        assert len(results) == 2
        mock_get.assert_called_once()
        assert "query=test+song" in mock_get.call_args[0][0]
        assert "webFilter=OnlyWeb" in mock_get.call_args[0][0]

    @patch('karaoke_nerds.requests.get')
    def test_network_error_returns_empty(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")
        results = search("test")
        assert results == []

    @patch('karaoke_nerds.requests.get')
    def test_timeout(self, mock_get):
        import requests as req
        mock_get.side_effect = req.Timeout("Request timed out")
        results = search("test")
        assert results == []

    @patch('karaoke_nerds.requests.get')
    def test_uses_correct_headers(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = "<html></html>"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        search("test")
        headers = mock_get.call_args[1]["headers"]
        assert "User-Agent" in headers
