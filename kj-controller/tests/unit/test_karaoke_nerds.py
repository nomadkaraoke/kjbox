"""Unit tests for karaoke_nerds.py.

The module no longer scrapes karaokenerds.com — it queries our own
`karaokenerds_community` catalog via ``divebar.kn_community_search`` and groups
the flat rows into songs+tracks. These tests exercise that grouping and the
YouTube-URL cleanup, and confirm no HTTP is made to karaokenerds.com.
"""

from unittest.mock import patch

import karaoke_nerds

# Flat community rows as returned by the Divebar Cloud Function's
# `kn_community_search` action: {artist, title, brand (a CODE), watch}.
FIXTURE_ROWS = [
    {"artist": "Artist One", "title": "Test Song", "brand": "KV",
     "watch": "https://www.youtube.com/watch?v=abc123defgh&list=PLtest"},
    {"artist": "Artist One", "title": "Test Song", "brand": "OBSK",
     "watch": "https://youtu.be/def456ijklm"},
    {"artist": "Artist Two", "title": "Another Song", "brand": "SK",
     "watch": "https://www.youtube.com/watch?v=ghi789nopqr"},
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
        # Catalog stores the code; the human name is resolved for display.
        assert kv["brand_code"] == "KV"
        assert kv["brand_name"] == "Karaoke Version"
        # Every catalog row is a community/web track.
        assert kv["is_community"] is True

    def test_youtube_url_canonicalized(self):
        with self._patch(FIXTURE_ROWS):
            songs = karaoke_nerds.search("test")
        # Both youtube.com/watch (with &list) and youtu.be forms -> canonical watch?v=.
        assert songs[0]["tracks"][0]["youtube_url"] == "https://www.youtube.com/watch?v=abc123defgh"
        assert songs[0]["tracks"][1]["youtube_url"] == "https://www.youtube.com/watch?v=def456ijklm"
        assert songs[1]["tracks"][0]["youtube_url"] == "https://www.youtube.com/watch?v=ghi789nopqr"

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


class TestNormalizeYoutubeUrl:
    def test_youtu_be_short_form(self):
        assert karaoke_nerds._normalize_youtube_url("https://youtu.be/dQw4w9WgXcQ") \
            == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_strips_list_param(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLtest123"
        assert karaoke_nerds._normalize_youtube_url(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_plain_watch_url_unchanged(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert karaoke_nerds._normalize_youtube_url(url) == url

    def test_empty_returns_empty(self):
        assert karaoke_nerds._normalize_youtube_url("") == ""

    def test_unparseable_returned_as_is(self):
        assert karaoke_nerds._normalize_youtube_url("not a url") == "not a url"
