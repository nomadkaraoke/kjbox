"""Unit tests for karaoke_nerds.py.

The module no longer scrapes karaokenerds.com — it queries our own copies of
BOTH KaraokeNerds catalogs via ``divebar.kn_search`` (community = web-playable
tracks with YouTube URLs; full = every release incl. commercial disc brands)
and groups/merges the flat rows into songs+tracks. These tests exercise that
grouping, the full-catalog merge, and the YouTube-URL cleanup, and confirm no
HTTP is made to karaokenerds.com.
"""

from unittest.mock import patch

import karaoke_nerds

# Flat community rows as returned inside the Divebar Cloud Function's
# `kn_search` action response: {artist, title, brand (a CODE), watch}.
FIXTURE_ROWS = [
    {"artist": "Artist One", "title": "Test Song", "brand": "KV",
     "watch": "https://www.youtube.com/watch?v=abc123defgh&list=PLtest"},
    {"artist": "Artist One", "title": "Test Song", "brand": "OBSK",
     "watch": "https://youtu.be/def456ijklm"},
    {"artist": "Artist Two", "title": "Another Song", "brand": "SK",
     "watch": "https://www.youtube.com/watch?v=ghi789nopqr"},
]


class TestSearchGrouping:
    def _patch(self, rows, full=None):
        return patch.object(karaoke_nerds.divebar, "kn_search",
                            return_value={"community": rows, "full": full or []})

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
        with patch.object(karaoke_nerds.divebar, "kn_search",
                          side_effect=Exception("boom")):
            assert karaoke_nerds.search("x") == []


class TestFullCatalogMerge:
    """Full-catalog rows (commercial disc brands) merge in as non-community tracks."""

    def _patch(self, community, full):
        return patch.object(karaoke_nerds.divebar, "kn_search",
                            return_value={"community": community, "full": full})

    def test_disc_only_song_appears_with_all_brands(self):
        # The regression that motivated this: a song with ONLY commercial
        # releases (no community/YouTube version) must not vanish from search.
        full = [{"artist": "Jason Aldean", "title": "Big Green Tractor",
                 "brands": "AC,ASK,SBI"}]
        with self._patch([], full):
            songs = karaoke_nerds.search("big green tractor")
        assert len(songs) == 1
        song = songs[0]
        assert song["artist"] == "Jason Aldean"
        codes = [t["brand_code"] for t in song["tracks"]]
        assert codes == ["AC", "ASK", "SBI"]
        for t in song["tracks"]:
            assert t["is_community"] is False
            assert t["youtube_url"] is None

    def test_community_codes_in_full_row_not_duplicated(self):
        # KN's full-catalog Brands list includes the community codes too — a
        # code already present as a playable community track must not gain a
        # second, URL-less copy.
        community = [{"artist": "Tenacious D", "title": "Tribute",
                      "brand": "BELLY", "watch": "https://youtu.be/jrOYbFIYKlo"}]
        full = [{"artist": "Tenacious D", "title": "Tribute",
                 "brands": "BELLY,CK,KV"}]
        with self._patch(community, full):
            songs = karaoke_nerds.search("tribute")
        assert len(songs) == 1
        tracks = songs[0]["tracks"]
        assert [t["brand_code"] for t in tracks] == ["BELLY", "CK", "KV"]
        belly = tracks[0]
        assert belly["is_community"] is True
        assert belly["youtube_url"] == "https://www.youtube.com/watch?v=jrOYbFIYKlo"
        assert all(t["youtube_url"] is None for t in tracks[1:])

    def test_community_tracks_listed_before_commercial(self):
        community = [{"artist": "A", "title": "S", "brand": "NOMAD",
                      "watch": "https://youtu.be/aaaaaaaaaaa"}]
        full = [{"artist": "A", "title": "S", "brands": "KV,NOMAD"}]
        with self._patch(community, full):
            songs = karaoke_nerds.search("s")
        flags = [t["is_community"] for t in songs[0]["tracks"]]
        assert flags == [True, False]

    def test_song_key_matching_is_case_insensitive(self):
        community = [{"artist": "Artist One", "title": "Test Song",
                      "brand": "KV", "watch": "https://youtu.be/abc123defgh"}]
        full = [{"artist": "ARTIST ONE", "title": "TEST SONG", "brands": "SF"}]
        with self._patch(community, full):
            songs = karaoke_nerds.search("test")
        assert len(songs) == 1  # merged into the same song, not a duplicate

    def test_full_row_without_title_skipped(self):
        with self._patch([], [{"artist": "X", "title": "", "brands": "KV"}]):
            assert karaoke_nerds.search("x") == []

    def test_blank_and_duplicate_codes_skipped(self):
        full = [{"artist": "A", "title": "S", "brands": "KV, ,KV,,SF"}]
        with self._patch([], full):
            songs = karaoke_nerds.search("s")
        assert [t["brand_code"] for t in songs[0]["tracks"]] == ["KV", "SF"]


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
