"""Unit tests for search-result grouping helpers in routes.py.

Covers:

- ``_normalize_song_key`` — deterministic ``(artist, title)`` → string key used to
  collapse equivalent search results into a single group.
- ``_group_search_results`` — merges local + KN results into a ``songs`` list
  with stable ordering, derived summary fields, and display-name arbitration.

Design doc: docs/archive/2026-04-23-song-selection-phase-a-design.md
"""

from routes import _group_search_results, _normalize_song_key


class TestNormalizeSongKey:
    """Whitebox tests for the key function. Two results share a key iff their
    normalized forms are byte-equal. No fuzzy matching — see master plan
    decision #1: we accept that ``"Don't Stop Believin'"`` and
    ``"Dont Stop Believin"`` won't merge until we revisit.
    """

    def test_identity_lowercases(self):
        k = _normalize_song_key("Queen", "Bohemian Rhapsody")
        assert k == "queen|||bohemian rhapsody"

    def test_case_insensitive(self):
        assert _normalize_song_key("QUEEN", "Bohemian Rhapsody") \
            == _normalize_song_key("queen", "bohemian rhapsody")

    def test_trims_whitespace(self):
        assert _normalize_song_key("  Queen  ", " Bohemian Rhapsody ") \
            == _normalize_song_key("Queen", "Bohemian Rhapsody")

    def test_collapses_internal_whitespace(self):
        assert _normalize_song_key("Queen", "Bohemian   Rhapsody") \
            == _normalize_song_key("Queen", "Bohemian Rhapsody")

    def test_strips_feat_parenthesised(self):
        # "Song (feat. Artist)" and "Song" should collide.
        assert _normalize_song_key("Pharrell Williams", "Happy (feat. Gru)") \
            == _normalize_song_key("Pharrell Williams", "Happy")

    def test_strips_feat_bare(self):
        assert _normalize_song_key("Drake", "One Dance feat. Kyla") \
            == _normalize_song_key("Drake", "One Dance")

    def test_strips_ft_abbreviation(self):
        assert _normalize_song_key("Kanye West", "All of the Lights ft. Rihanna") \
            == _normalize_song_key("Kanye West", "All of the Lights")

    def test_strips_featuring_long_form(self):
        assert _normalize_song_key("Beyoncé", "Crazy in Love featuring Jay-Z") \
            == _normalize_song_key("Beyoncé", "Crazy in Love")

    def test_keeps_bracketed_qualifiers_distinct(self):
        # Decision D4: bracketed qualifiers like "(Live)" / "[Radio Edit]" /
        # "(Remastered)" are NO LONGER stripped — distinct versions must stay
        # distinguishable, so they group separately from the bare title. (The
        # shared normalizer only strips feat./ft./featuring, not brackets.)
        assert _normalize_song_key("Queen", "Bohemian Rhapsody (Live)") \
            != _normalize_song_key("Queen", "Bohemian Rhapsody")
        assert _normalize_song_key("Queen", "Bohemian Rhapsody (Remastered 2011)") \
            != _normalize_song_key("Queen", "Bohemian Rhapsody")
        assert _normalize_song_key("Queen", "Bohemian Rhapsody [Radio Edit]") \
            != _normalize_song_key("Queen", "Bohemian Rhapsody")

    def test_strips_punctuation_from_title(self):
        # Apostrophes and commas are noise; hyphens in song titles become spaces.
        a = _normalize_song_key("Journey", "Don't Stop Believin'")
        b = _normalize_song_key("Journey", "Dont Stop Believin")
        # Both reduce to the same key once apostrophes are stripped.
        assert a == b

    def test_strips_punctuation_from_artist(self):
        assert _normalize_song_key("Guns N' Roses", "Sweet Child o' Mine") \
            == _normalize_song_key("Guns N Roses", "Sweet Child o Mine")

    def test_empty_inputs(self):
        # None-safe — upstream callers pass possibly-empty strings from parsed
        # filenames. Don't crash, produce a deterministic empty key.
        assert _normalize_song_key(None, None) == "|||"
        assert _normalize_song_key("", "") == "|||"
        assert _normalize_song_key("Queen", None) == "queen|||"
        assert _normalize_song_key(None, "Bohemian Rhapsody") == "|||bohemian rhapsody"

    def test_distinct_songs_do_not_collide(self):
        # Sanity: genuinely different songs must still produce different keys.
        assert _normalize_song_key("Queen", "Bohemian Rhapsody") \
            != _normalize_song_key("Queen", "Somebody to Love")
        assert _normalize_song_key("Queen", "Bohemian Rhapsody") \
            != _normalize_song_key("Beatles", "Bohemian Rhapsody")


class TestGroupSearchResults:
    """Integration-ish: feeds realistic result shapes and asserts on the
    grouped output. Uses the same object shapes the real local catalog and
    karaoke_nerds modules produce (see unified_search in routes.py).
    """

    @staticmethod
    def _local(artist, title, path, **extra):
        return {"path": path, "filename": path.rsplit("/", 1)[-1],
                "artist": artist, "title": title, **extra}

    @staticmethod
    def _kn_song(artist, title, tracks):
        return {"artist": artist, "title": title, "tracks": tracks}

    @staticmethod
    def _kn_track(brand_code, brand_name, youtube_url, is_community=False, divebar=None):
        t = {"brand_code": brand_code, "brand_name": brand_name,
             "youtube_url": youtube_url, "is_community": is_community}
        if divebar is not None:
            t["divebar"] = divebar
        return t

    def test_empty_inputs(self):
        assert _group_search_results([], []) == []

    def test_single_local_only(self):
        groups = _group_search_results(
            [self._local("Queen", "Bohemian Rhapsody",
                         "/media/Queen - Bohemian Rhapsody.mp4")],
            [],
        )
        assert len(groups) == 1
        g = groups[0]
        assert g["artist"] == "Queen"
        assert g["title"] == "Bohemian Rhapsody"
        assert g["version_count"] == 1
        assert g["in_library"] is True
        assert g["has_community_only"] is False
        assert len(g["versions"]) == 1
        assert g["versions"][0]["source"] == "local"

    def test_single_kn_only(self):
        groups = _group_search_results(
            [],
            [self._kn_song("Queen", "Bohemian Rhapsody", [
                self._kn_track("KV", "Karaoke Version",
                               "https://youtu.be/abc"),
            ])],
        )
        assert len(groups) == 1
        g = groups[0]
        assert g["in_library"] is False
        assert g["has_community_only"] is False
        assert g["versions"][0]["source"] == "kn"

    def test_local_and_kn_same_song_merged(self):
        # Same song from both sources collapses to one group with 2 versions.
        groups = _group_search_results(
            [self._local("Queen", "Bohemian Rhapsody",
                         "/media/Queen - Bohemian Rhapsody.mp4")],
            [self._kn_song("Queen", "Bohemian Rhapsody", [
                self._kn_track("KV", "Karaoke Version",
                               "https://youtu.be/abc"),
            ])],
        )
        assert len(groups) == 1
        assert groups[0]["version_count"] == 2
        sources = [v["source"] for v in groups[0]["versions"]]
        # Priority-driven sort: KN's KV is recognized commercial (tier 2000s);
        # local has no parseable disc_id so it's unknown (tier 4000s). KN wins.
        # (See docs/archive/2026-05-22-choose-best-version-design.md § Ranking.)
        assert sources == ["kn", "local"]

    def test_locals_win_display_name_arbitration(self):
        # The local result's casing/form wins the display name, even if KN's
        # version is more "canonical". Rationale: the local file is what the KJ
        # explicitly imported, so it should be the authoritative display form.
        #
        # D4: bracketed qualifiers are no longer stripped, so the local and KN
        # rows must share the SAME (non-bracketed) title to land in one group.
        # Casing differs only — the shared normalizer folds case, so they
        # still collide, exercising display-name arbitration.
        groups = _group_search_results(
            [self._local("Queen", "BOHEMIAN RHAPSODY",
                         "/media/Queen - Bohemian Rhapsody.mp4")],
            [self._kn_song("Queen", "Bohemian Rhapsody", [
                self._kn_track("KV", "Karaoke Version", "https://youtu.be/a"),
            ])],
        )
        # Grouped together (case folds under normalization).
        assert len(groups) == 1
        # Display name came from the local entry (its casing wins).
        assert groups[0]["title"] == "BOHEMIAN RHAPSODY"

    def test_kn_sets_display_when_no_local(self):
        groups = _group_search_results(
            [],
            [self._kn_song("Queen", "Bohemian Rhapsody", [
                self._kn_track("KV", "Karaoke Version", "https://youtu.be/a"),
                self._kn_track("SK", "Sound Choice", "https://youtu.be/b"),
            ])],
        )
        assert groups[0]["artist"] == "Queen"
        assert groups[0]["title"] == "Bohemian Rhapsody"

    def test_multiple_kn_tracks_grouped(self):
        # One KN song with N tracks produces N versions in the same group.
        groups = _group_search_results(
            [],
            [self._kn_song("Queen", "Bohemian Rhapsody", [
                self._kn_track("KV", "Karaoke Version", "https://youtu.be/a"),
                self._kn_track("SK", "Sound Choice", "https://youtu.be/b"),
                self._kn_track("OBSK", "ObsKure", "https://youtu.be/c",
                               is_community=True),
            ])],
        )
        assert len(groups) == 1
        assert groups[0]["version_count"] == 3

    def test_has_community_only_true(self):
        # All KN versions are community, no local → community-only.
        groups = _group_search_results(
            [],
            [self._kn_song("Bon Iver", "Obscure B-Side", [
                self._kn_track("OBSK", "ObsKure", "https://youtu.be/a",
                               is_community=True),
            ])],
        )
        assert groups[0]["has_community_only"] is True

    def test_has_community_only_false_when_local_present(self):
        groups = _group_search_results(
            [self._local("Bon Iver", "Obscure B-Side", "/media/x.mp4")],
            [self._kn_song("Bon Iver", "Obscure B-Side", [
                self._kn_track("OBSK", "ObsKure", "https://youtu.be/a",
                               is_community=True),
            ])],
        )
        assert groups[0]["has_community_only"] is False

    def test_has_community_only_false_when_any_commercial_kn(self):
        groups = _group_search_results(
            [],
            [self._kn_song("Queen", "Bohemian Rhapsody", [
                self._kn_track("KV", "Karaoke Version",
                               "https://youtu.be/a", is_community=False),
                self._kn_track("OBSK", "ObsKure", "https://youtu.be/b",
                               is_community=True),
            ])],
        )
        assert groups[0]["has_community_only"] is False

    def test_in_library_true_for_kn_with_divebar(self):
        # A KN track with a divebar mirror counts as "in library" even with
        # no local result — the KJ can download it from our own mirror.
        groups = _group_search_results(
            [],
            [self._kn_song("Queen", "Bohemian Rhapsody", [
                self._kn_track("KV", "Karaoke Version",
                               "https://youtu.be/a",
                               divebar={"file_id": "gdrive-abc",
                                        "format": "mp4",
                                        "quality": "HD"}),
            ])],
        )
        assert groups[0]["in_library"] is True

    def test_in_library_false_for_youtube_only(self):
        groups = _group_search_results(
            [],
            [self._kn_song("Queen", "Bohemian Rhapsody", [
                self._kn_track("KV", "Karaoke Version", "https://youtu.be/a"),
            ])],
        )
        assert groups[0]["in_library"] is False

    def test_different_songs_produce_separate_groups(self):
        groups = _group_search_results(
            [self._local("Queen", "Bohemian Rhapsody", "/a.mp4"),
             self._local("Queen", "Somebody to Love", "/b.mp4")],
            [],
        )
        assert len(groups) == 2
        titles = {g["title"] for g in groups}
        assert titles == {"Bohemian Rhapsody", "Somebody to Love"}

    def test_insertion_order_is_stable(self):
        # Locals first (in order), then KN (in order) for songs not yet seen.
        groups = _group_search_results(
            [self._local("A", "Song One", "/1.mp4"),
             self._local("B", "Song Two", "/2.mp4")],
            [self._kn_song("C", "Song Three", [
                self._kn_track("KV", "Karaoke Version", "https://youtu.be/x"),
            ])],
        )
        titles = [g["title"] for g in groups]
        assert titles == ["Song One", "Song Two", "Song Three"]

    def test_kn_track_carries_parent_song_name(self):
        # The grouped version needs access to the KN song's title/artist for
        # the KJ picker to render it without a parent lookup.
        groups = _group_search_results(
            [],
            [self._kn_song("Queen", "Bohemian Rhapsody", [
                self._kn_track("KV", "Karaoke Version", "https://youtu.be/a"),
            ])],
        )
        kn_version = groups[0]["versions"][0]
        assert kn_version["kn"]["song_artist"] == "Queen"
        assert kn_version["kn"]["song_title"] == "Bohemian Rhapsody"

    def test_feat_strip_merges_variants(self):
        # "Happy feat. Gru" and "Happy" collapse to the same group.
        groups = _group_search_results(
            [self._local("Pharrell Williams", "Happy (feat. Gru)", "/x.mp4")],
            [self._kn_song("Pharrell Williams", "Happy", [
                self._kn_track("KV", "Karaoke Version", "https://youtu.be/a"),
            ])],
        )
        assert len(groups) == 1
        assert groups[0]["version_count"] == 2


class TestGroupedResultPriorityAnnotation:
    def test_versions_get_priority_rank(self):
        local = [{"path": "/a.zip", "disc_id": "KVD-22524",
                  "artist": "Queen", "title": "Bohemian Rhapsody"}]
        kn = [{"title": "Bohemian Rhapsody", "artist": "Queen",
               "tracks": [{"brand_code": "LC", "brand_name": "Lemmy Caution",
                           "is_community": True,
                           "youtube_url": "https://yt"}]}]
        groups = _group_search_results(local, kn)
        assert len(groups) == 1
        for v in groups[0]["versions"]:
            assert "priority_rank" in v
            assert "priority_brand" in v
            assert "priority_class" in v

    def test_versions_sorted_by_priority(self):
        # LC community on KN should sort above local KVD (community > commercial)
        local = [{"path": "/a.zip", "disc_id": "KVD-22524",
                  "artist": "Queen", "title": "Bohemian Rhapsody"}]
        kn = [{"title": "Bohemian Rhapsody", "artist": "Queen",
               "tracks": [{"brand_code": "LC", "brand_name": "Lemmy Caution",
                           "is_community": True,
                           "youtube_url": "https://yt"}]}]
        groups = _group_search_results(local, kn)
        versions = groups[0]["versions"]
        assert versions[0]["priority_class"] == "community"
        assert versions[0]["priority_brand"] == "LC"
        assert versions[1]["priority_class"] == "commercial"
        assert versions[1]["priority_brand"] == "KV"

    def test_unparseable_local_versions_classify_as_unknown(self):
        local = [{"path": "/a.zip", "disc_id": "",
                  "filename": "plain.zip",
                  "artist": "X", "title": "Y"}]
        groups = _group_search_results(local, [])
        assert groups[0]["versions"][0]["priority_class"] == "unknown"
