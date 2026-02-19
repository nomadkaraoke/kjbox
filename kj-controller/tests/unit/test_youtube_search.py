"""Unit tests for youtube_search module."""

import pytest
from unittest.mock import patch, MagicMock

import youtube_search
from youtube_search import _format_duration, _format_views


class TestFormatDuration:
    """Tests for _format_duration helper."""

    def test_none(self):
        assert _format_duration(None) == ''

    def test_zero(self):
        assert _format_duration(0) == '0:00'

    def test_negative(self):
        assert _format_duration(-5) == ''

    def test_seconds_only(self):
        assert _format_duration(45) == '0:45'

    def test_minutes_and_seconds(self):
        assert _format_duration(195) == '3:15'

    def test_exact_minute(self):
        assert _format_duration(120) == '2:00'

    def test_hours(self):
        assert _format_duration(3661) == '1:01:01'

    def test_large_duration(self):
        assert _format_duration(7200) == '2:00:00'

    def test_float_truncated(self):
        assert _format_duration(65.9) == '1:05'


class TestFormatViews:
    """Tests for _format_views helper."""

    def test_none(self):
        assert _format_views(None) == ''

    def test_zero(self):
        assert _format_views(0) == '0'

    def test_small_number(self):
        assert _format_views(999) == '999'

    def test_thousands(self):
        assert _format_views(45_000) == '45.0K'

    def test_millions(self):
        assert _format_views(1_200_000) == '1.2M'

    def test_billions(self):
        assert _format_views(2_500_000_000) == '2.5B'

    def test_exact_thousand(self):
        assert _format_views(1_000) == '1.0K'

    def test_exact_million(self):
        assert _format_views(1_000_000) == '1.0M'


class TestSearch:
    """Tests for the search function."""

    def _mock_entries(self):
        return [
            {
                'id': 'abc123',
                'title': 'Bohemian Rhapsody Karaoke',
                'channel': 'Sing King',
                'uploader': 'Sing King',
                'duration': 355.0,
                'view_count': 5_200_000,
            },
            {
                'id': 'def456',
                'title': 'Bohemian Rhapsody - Karaoke Version',
                'channel': None,
                'uploader': 'KaraFun',
                'duration': 360.0,
                'view_count': 1_000,
            },
        ]

    @patch('youtube_search.yt_dlp')
    def test_basic_search(self, mock_yt_dlp):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {'entries': self._mock_entries()}
        mock_yt_dlp.YoutubeDL.return_value = mock_ydl

        results = youtube_search.search('bohemian rhapsody')

        assert len(results) == 2
        assert results[0]['id'] == 'abc123'
        assert results[0]['title'] == 'Bohemian Rhapsody Karaoke'
        assert results[0]['channel'] == 'Sing King'
        assert results[0]['duration_str'] == '5:55'
        assert results[0]['view_count_str'] == '5.2M'
        assert results[0]['url'] == 'https://www.youtube.com/watch?v=abc123'

    @patch('youtube_search.yt_dlp')
    def test_channel_falls_back_to_uploader(self, mock_yt_dlp):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {'entries': self._mock_entries()}
        mock_yt_dlp.YoutubeDL.return_value = mock_ydl

        results = youtube_search.search('bohemian rhapsody')

        # Second entry has channel=None, should fall back to uploader
        assert results[1]['channel'] == 'KaraFun'

    @patch('youtube_search.yt_dlp')
    def test_search_error_returns_empty(self, mock_yt_dlp):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.side_effect = Exception('Network error')
        mock_yt_dlp.YoutubeDL.return_value = mock_ydl

        results = youtube_search.search('test query')
        assert results == []

    @patch('youtube_search.yt_dlp')
    def test_empty_results(self, mock_yt_dlp):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {'entries': []}
        mock_yt_dlp.YoutubeDL.return_value = mock_ydl

        results = youtube_search.search('xyznonesense')
        assert results == []

    @patch('youtube_search.yt_dlp')
    def test_none_result(self, mock_yt_dlp):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = None
        mock_yt_dlp.YoutubeDL.return_value = mock_ydl

        results = youtube_search.search('test')
        assert results == []

    @patch('youtube_search.yt_dlp')
    def test_null_entries_skipped(self, mock_yt_dlp):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {
            'entries': [None, {'id': 'abc', 'title': 'Test', 'duration': 60, 'view_count': 100}, None]
        }
        mock_yt_dlp.YoutubeDL.return_value = mock_ydl

        results = youtube_search.search('test')
        assert len(results) == 1
        assert results[0]['id'] == 'abc'

    @patch('youtube_search.yt_dlp')
    @patch('os.path.exists', return_value=True)
    def test_cookies_file_used(self, mock_exists, mock_yt_dlp):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {'entries': []}
        mock_yt_dlp.YoutubeDL.return_value = mock_ydl

        youtube_search.search('test', config={'youtube_cookies_file': '/tmp/cookies.txt'})

        opts = mock_yt_dlp.YoutubeDL.call_args[0][0]
        assert opts['cookiefile'] == '/tmp/cookies.txt'

    @patch('youtube_search.yt_dlp')
    def test_max_results_in_query(self, mock_yt_dlp):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {'entries': []}
        mock_yt_dlp.YoutubeDL.return_value = mock_ydl

        youtube_search.search('test', max_results=5)

        call_args = mock_ydl.extract_info.call_args
        assert 'ytsearch5:test' == call_args[0][0]

    @patch('youtube_search.yt_dlp')
    def test_missing_fields_handled(self, mock_yt_dlp):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {
            'entries': [{'id': 'vid1'}]
        }
        mock_yt_dlp.YoutubeDL.return_value = mock_ydl

        results = youtube_search.search('test')
        assert len(results) == 1
        assert results[0]['title'] == ''
        assert results[0]['channel'] == ''
        assert results[0]['duration_str'] == ''
        assert results[0]['view_count_str'] == ''
