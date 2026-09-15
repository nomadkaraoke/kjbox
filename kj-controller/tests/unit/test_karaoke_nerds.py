"""Unit tests for karaoke_nerds.py.

The module no longer scrapes karaokenerds.com — it queries our own
`karaokenerds_community` catalog via ``divebar.kn_community_search`` and groups
the flat rows into songs+tracks. These tests exercise that grouping and the
YouTube-URL cleanup, and confirm no HTTP is made to karaokenerds.com.
"""

from unittest.mock import patch

import karaoke_nerds

# Flat community rows as returned by the Divebar Cloud Function's
# `kn_community_search` action: {artist, title, brand, watch}.
FIXTURE_ROWS = [
    {"artist": "Artist One", "title": "Test Song", "brand": "Karaoke Version",
     "watch": "https://www.youtube.com/watch?v=abc123&list=PLtest"},
    {"artist": "Artist One", "title": "Test Song", "brand": "ObsKure Karaoke",
     "watch": "https://www.youtube.com/watch?v=def456&list=PLother"},
    {"artist": "Artist Two", "title": "Another Song", "brand": "Sing King",
     "watch": "https://www.youtube.com/watch?v=ghi789"},
]


class TestSearchGrouping:
    def _patch(self, rows):
        return patch.object(karaoke_nerds.divebar, "kn_community_search", return_value=rows)

    def test_groups_rows_into_songs(self):
        with self._patch(FIXTURE_ROWS):
            songs = karaoke_nerds.search("test")
        assert len(songs) == 2
        assert songs[0]["title"] == "Test Song"
        assert songs[0]["artist"] == "Artist One"
        assert len(songs[0]["tracks"]) == 2
        assert songs[1]["title"] == "Another Song"
        assert len(songs[1]["tracks"]) == 1

    def test_track_shape_and_community_flag(self):
        with self._patch(FIXTURE_ROWS):
            songs = karaoke_nerds.search("test")
        kv = songs[0]["tracks"][0]
        assert kv["brand_name"] == "Karaoke Version"
        # Community catalog carries no brand code; ranking resolves via brand_name.
        assert kv["brand_code"] == ""
        # Every catalog row is a community/web track.
        assert kv["is_community"] is True

    def test_youtube_url_list_param_stripped(self):
        with self._patch(FIXTURE_ROWS):
            songs = karaoke_nerds.search("test")
        assert songs[0]["tracks"][0]["youtube_url"] == "https://www.youtube.com/watch?v=abc123"
        assert songs[1]["tracks"][0]["youtube_url"] == "https://www.youtube.com/watch?v=ghi789"

    def test_dedupes_identical_brand_and_url(self):
        rows = FIXTURE_ROWS + [dict(FIXTURE_ROWS[0])]  # exact duplicate row
        with self._patch(rows):
            songs = karaoke_nerds.search("test")
        assert len(songs[0]["tracks"]) == 2  # duplicate collapsed

    def test_empty_results(self):
        with self._patch([]):
            assert karaoke_nerds.search("nothing") == []

    def test_row_without_title_skipped(self):
        with self._patch([{"artist": "X", "title": "", "brand": "B", "watch": "u"}]):
            assert karaoke_nerds.search("x") == []

    def test_backend_error_returns_empty(self):
        with patch.object(karaoke_nerds.divebar, "kn_community_search",
                          side_effect=Exception("boom")):
            assert karaoke_nerds.search("x") == []


class TestCleanYoutubeUrl:
    def test_strips_list_param(self):
        url = "https://www.youtube.com/watch?v=abc123&list=PLtest123"
        assert karaoke_nerds._clean_youtube_url(url) == "https://www.youtube.com/watch?v=abc123"

    def test_no_list_param(self):
        url = "https://www.youtube.com/watch?v=abc123"
        assert karaoke_nerds._clean_youtube_url(url) == "https://www.youtube.com/watch?v=abc123"

    def test_list_in_middle(self):
        url = "https://www.youtube.com/watch?v=abc&list=PLtest&index=1"
        assert karaoke_nerds._clean_youtube_url(url) == "https://www.youtube.com/watch?v=abc&index=1"
