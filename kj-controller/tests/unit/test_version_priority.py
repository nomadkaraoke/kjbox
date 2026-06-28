"""Tests for version_priority: brand-priority ranking across version selection flows."""

import pytest

from version_priority import (
    COMMUNITY_BRANDS, COMMERCIAL_BRANDS,
    COMMUNITY_DEFAULTS, COMMERCIAL_DEFAULTS,
)


class TestBrandRegistry:
    def test_community_top_brands_in_order(self):
        canonicals = [c for (c, _, _) in COMMUNITY_BRANDS]
        # KJ-stated top community brands, in order
        assert canonicals[:8] == ["CC", "LC", "FBK", "BELLY", "NOMAD", "FAKEY", "PMK", "OBSK"]

    def test_commercial_top_brands_in_order(self):
        canonicals = [c for (c, _, _) in COMMERCIAL_BRANDS]
        # KJ-stated top commercial brands, in order
        assert canonicals[:6] == ["KV", "SC", "SBI", "SF", "CB", "ZM"]

    def test_community_defaults_match_registry_order(self):
        assert COMMUNITY_DEFAULTS == [c for (c, _, _) in COMMUNITY_BRANDS]

    def test_commercial_defaults_match_registry_order(self):
        assert COMMERCIAL_DEFAULTS == [c for (c, _, _) in COMMERCIAL_BRANDS]

    def test_cc_aliases_include_cck_and_ccx(self):
        cc = next(b for b in COMMUNITY_BRANDS if b[0] == "CC")
        aliases = [a.upper() for a in cc[1]]
        assert "CCK" in aliases
        assert "CCX" in aliases

    def test_lc_aliases_include_lemmy(self):
        lc = next(b for b in COMMUNITY_BRANDS if b[0] == "LC")
        aliases = [a.upper() for a in lc[1]]
        assert "LEMMY" in aliases

    def test_kv_aliases_include_kvd_and_kcd(self):
        kv = next(b for b in COMMERCIAL_BRANDS if b[0] == "KV")
        aliases = [a.upper() for a in kv[1]]
        assert "KVD" in aliases
        assert "KCD" in aliases

    def test_zm_aliases_include_zoom(self):
        zm = next(b for b in COMMERCIAL_BRANDS if b[0] == "ZM")
        aliases = [a.upper() for a in zm[1]]
        assert "ZOOM" in aliases


from version_priority import resolve_brand


class TestResolveBrandFromBrandCode:
    def test_canonical_community_code(self):
        code, cls = resolve_brand(brand_code="LC")
        assert code == "LC"
        assert cls == "community"

    def test_lemmy_alias_resolves_to_lc(self):
        code, cls = resolve_brand(brand_code="LEMMY")
        assert code == "LC"
        assert cls == "community"

    def test_kvd_alias_resolves_to_kv_commercial(self):
        code, cls = resolve_brand(brand_code="KVD")
        assert code == "KV"
        assert cls == "commercial"

    def test_kcd_alias_resolves_to_kv(self):
        code, cls = resolve_brand(brand_code="KCD")
        assert code == "KV"
        assert cls == "commercial"

    def test_zoom_alias_resolves_to_zm(self):
        code, cls = resolve_brand(brand_code="ZOOM")
        assert code == "ZM"
        assert cls == "commercial"

    def test_ccx_alias_resolves_to_cc(self):
        code, cls = resolve_brand(brand_code="CCX")
        assert code == "CC"
        assert cls == "community"

    def test_case_insensitive(self):
        code, cls = resolve_brand(brand_code="lemmy")
        assert code == "LC"
        assert cls == "community"

    def test_unknown_brand_code(self):
        code, cls = resolve_brand(brand_code="WHATEVER")
        assert code is None
        assert cls == "unknown"


class TestResolveBrandFromBrandName:
    def test_brand_name_lemmy_caution(self):
        code, cls = resolve_brand(brand_name="Lemmy Caution")
        assert code == "LC"
        assert cls == "community"

    def test_brand_name_obskure_karaoke(self):
        code, cls = resolve_brand(brand_name="ObsKure Karaoke")
        assert code == "OBSK"
        assert cls == "community"

    def test_brand_code_preferred_over_brand_name(self):
        # brand_code is more reliable; even an unknown code doesn't fall
        # through to brand_name (avoids matching the wrong brand on partial
        # name collisions).
        code, cls = resolve_brand(brand_code="WHATEVER", brand_name="Lemmy Caution")
        assert code is None  # brand_code took priority, no match
        assert cls == "unknown"


class TestResolveBrandFromDiscId:
    def test_kvd_disc_id(self):
        code, cls = resolve_brand(disc_id="KVD-22524")
        assert code == "KV"
        assert cls == "commercial"

    def test_lemmy_disc_id(self):
        code, cls = resolve_brand(disc_id="LEMMY-001")
        assert code == "LC"
        assert cls == "community"

    def test_cck_disc_id(self):
        code, cls = resolve_brand(disc_id="CCK-042")
        assert code == "CC"
        assert cls == "community"

    def test_sc_disc_id_with_number_run(self):
        code, cls = resolve_brand(disc_id="SC2411-08")
        assert code == "SC"
        assert cls == "commercial"

    def test_t2k_disc_id_unknown(self):
        # T2K has an embedded digit so the alpha-prefix regex stops at "T",
        # which doesn't match any alias. Acceptable — T2K isn't in our list.
        code, cls = resolve_brand(disc_id="T2K-0348")
        assert code is None
        assert cls == "unknown"

    def test_bmg6252_disc_id_unknown(self):
        code, cls = resolve_brand(disc_id="BMG6252")
        assert code is None
        assert cls == "unknown"


class TestResolveBrandFromYouTubeFilename:
    def test_obskure_youtube_filename(self):
        code, cls = resolve_brand(
            filename="wy7voMFbN7U__ObsKure Karaoke__Queen - Bohemian Rhapsody.mp4"
        )
        assert code == "OBSK"
        assert cls == "community"

    def test_sing_king_youtube_filename(self):
        code, cls = resolve_brand(
            filename="-UM1XiyBmhM__Sing King__Bella Kay - iloveitiloveitiloveit.mp4"
        )
        assert code == "SK"
        assert cls == "commercial"

    def test_unknown_brand_segment(self):
        code, cls = resolve_brand(
            filename="3BAz6Jm2BNs__Unknown__Journey - Don't Stop Believing.mp4"
        )
        assert code is None
        assert cls == "unknown"

    def test_no_double_underscore_pattern(self):
        code, cls = resolve_brand(filename="just a plain filename.mp4")
        assert code is None
        assert cls == "unknown"


class TestResolveBrandIsCommunityOverride:
    def test_is_community_true_overrides_commercial_brand(self):
        # Edge case: someone uploads a Karaoke Version mirror tagged as
        # community on KN. is_community flag wins.
        code, cls = resolve_brand(brand_code="KV", is_community=True)
        assert code == "KV"
        assert cls == "community"

    def test_is_community_false_keeps_commercial_classification(self):
        code, cls = resolve_brand(brand_code="KV", is_community=False)
        assert code == "KV"
        assert cls == "commercial"

    def test_is_community_true_with_unknown_brand(self):
        code, cls = resolve_brand(brand_code="WHATEVER", is_community=True)
        assert code is None
        assert cls == "community"


from version_priority import rank_version, annotate_versions


def _cfg(**overrides):
    """Minimal config dict for ranking tests."""
    base = {}
    base.update(overrides)
    return base


class TestRankVersionTiers:
    def test_recognized_community_below_1000(self):
        v = {"source": "kn", "kn": {"brand_code": "CC", "is_community": True}}
        r = rank_version(v, _cfg())
        assert r < 1000

    def test_unrecognized_community_in_1000s(self):
        v = {"source": "kn", "kn": {"brand_code": "WHATEVER", "is_community": True}}
        r = rank_version(v, _cfg())
        assert 1000 <= r < 2000

    def test_recognized_commercial_in_2000s(self):
        v = {"source": "kn", "kn": {"brand_code": "KV"}}
        r = rank_version(v, _cfg())
        assert 2000 <= r < 3000

    def test_unrecognized_commercial_in_3000s(self):
        # Real KN data always has is_community set; default real-world value
        # for commercial tracks is False.
        v = {"source": "kn", "kn": {"brand_code": "WHATEVER", "is_community": False}}
        r = rank_version(v, _cfg())
        assert 3000 <= r < 4000

    def test_unknown_at_4000(self):
        # Pure-unknown: a local entry with no parseable brand info at all.
        v = {"source": "local", "local": {"disc_id": "",
                                          "filename": "plain song.mp4"}}
        r = rank_version(v, _cfg())
        assert r >= 4000


class TestRankVersionPriorityOrder:
    def test_cc_outranks_lc(self):
        cc = {"source": "kn", "kn": {"brand_code": "CC", "is_community": True}}
        lc = {"source": "kn", "kn": {"brand_code": "LC", "is_community": True}}
        assert rank_version(cc, _cfg()) < rank_version(lc, _cfg())

    def test_lc_outranks_fbk(self):
        lc = {"source": "kn", "kn": {"brand_code": "LC", "is_community": True}}
        fbk = {"source": "kn", "kn": {"brand_code": "FBK", "is_community": True}}
        assert rank_version(lc, _cfg()) < rank_version(fbk, _cfg())

    def test_community_always_beats_commercial(self):
        # Worst-positioned recognized community beats best commercial
        last_community = COMMUNITY_BRANDS[-1][0]
        community = {"source": "kn", "kn": {"brand_code": last_community,
                                            "is_community": True}}
        commercial = {"source": "kn", "kn": {"brand_code": "KV"}}
        assert rank_version(community, _cfg()) < rank_version(commercial, _cfg())

    def test_unrecognized_community_beats_recognized_commercial(self):
        unrec = {"source": "kn", "kn": {"brand_code": "WEIRD", "is_community": True}}
        kv = {"source": "kn", "kn": {"brand_code": "KV"}}
        assert rank_version(unrec, _cfg()) < rank_version(kv, _cfg())

    def test_kv_outranks_sc(self):
        kv = {"source": "kn", "kn": {"brand_code": "KV"}}
        sc = {"source": "kn", "kn": {"brand_code": "SC"}}
        assert rank_version(kv, _cfg()) < rank_version(sc, _cfg())


class TestRankVersionSourceTiebreaker:
    def test_local_beats_divebar_beats_youtube_same_brand(self):
        local = {"source": "local", "local": {"disc_id": "LEMMY-001"}}
        divebar = {"source": "kn", "kn": {"brand_code": "LC", "is_community": True,
                                          "divebar": {"file_id": "abc"}}}
        youtube = {"source": "kn", "kn": {"brand_code": "LC", "is_community": True}}
        assert rank_version(local, _cfg()) < rank_version(divebar, _cfg())
        assert rank_version(divebar, _cfg()) < rank_version(youtube, _cfg())

    def test_tiebreaker_does_not_cross_brand_boundary(self):
        # Worst source of CC (youtube) still beats best source of LC (local)
        cc_youtube = {"source": "kn", "kn": {"brand_code": "CC", "is_community": True}}
        lc_local = {"source": "local", "local": {"disc_id": "LEMMY-001"}}
        assert rank_version(cc_youtube, _cfg()) < rank_version(lc_local, _cfg())


class TestRankVersionConfigOverride:
    def test_community_config_reorders(self):
        # Default has CC at 0, LC at 1. Override to put LC first.
        cfg = _cfg(kn_priority_community=["LC", "CC"])
        cc = {"source": "kn", "kn": {"brand_code": "CC", "is_community": True}}
        lc = {"source": "kn", "kn": {"brand_code": "LC", "is_community": True}}
        assert rank_version(lc, cfg) < rank_version(cc, cfg)

    def test_brand_dropped_from_config_becomes_unrecognized(self):
        # Override that omits CC entirely — CC should classify as
        # unrecognized community (tier 1000s).
        cfg = _cfg(kn_priority_community=["LC", "FBK"])
        cc = {"source": "kn", "kn": {"brand_code": "CC", "is_community": True}}
        r = rank_version(cc, cfg)
        assert 1000 <= r < 2000

    def test_empty_config_falls_back_to_defaults(self):
        cfg_empty = _cfg(kn_priority_community=[])
        cfg_default = _cfg()
        cc = {"source": "kn", "kn": {"brand_code": "CC", "is_community": True}}
        assert rank_version(cc, cfg_empty) == rank_version(cc, cfg_default)


class TestAnnotateVersionsKjPickShape:
    def test_annotates_local(self):
        versions = [
            {"source": "local", "local": {"disc_id": "CCK-042",
                                          "path": "/a.zip"}},
        ]
        annotate_versions(versions, _cfg(), shape="kj_pick")
        assert versions[0]["priority_brand"] == "CC"
        assert versions[0]["priority_class"] == "community"
        assert versions[0]["priority_rank"] < 1000

    def test_annotates_kn_with_divebar(self):
        versions = [
            {"source": "kn", "kn": {"brand_code": "LC", "is_community": True,
                                    "divebar": {"file_id": "abc"}}},
        ]
        annotate_versions(versions, _cfg(), shape="kj_pick")
        assert versions[0]["priority_brand"] == "LC"
        assert versions[0]["priority_class"] == "community"

    def test_annotates_unknown_kn(self):
        versions = [
            {"source": "kn", "kn": {"brand_code": "WHATEVER",
                                    "youtube_url": "https://yt"}},
        ]
        annotate_versions(versions, _cfg(), shape="kj_pick")
        assert versions[0]["priority_brand"] is None
        assert versions[0]["priority_class"] == "unknown"


class TestAnnotateVersionsRotationSearchShape:
    def test_annotates_local_row(self):
        rows = [{"path": "/a.zip", "disc_id": "KVD-22524", "filename": "x.zip"}]
        annotate_versions(rows, _cfg(), shape="rotation_search_local")
        assert rows[0]["priority_brand"] == "KV"
        assert rows[0]["priority_class"] == "commercial"

    def test_annotates_kn_track(self):
        tracks = [{"brand_code": "LC", "brand_name": "Lemmy Caution",
                   "youtube_url": "https://yt", "is_community": True}]
        annotate_versions(tracks, _cfg(), shape="rotation_search_kn")
        assert tracks[0]["priority_brand"] == "LC"
        assert tracks[0]["priority_class"] == "community"

    def test_annotates_kn_track_with_divebar_gets_better_rank(self):
        tracks = [
            {"brand_code": "LC", "youtube_url": "https://yt", "is_community": True},
            {"brand_code": "LC", "youtube_url": "https://yt", "is_community": True,
             "divebar": {"file_id": "abc"}},
        ]
        annotate_versions(tracks, _cfg(), shape="rotation_search_kn")
        # divebar-mirrored LC should rank better than youtube-only LC
        assert tracks[1]["priority_rank"] < tracks[0]["priority_rank"]


class TestCanonicalBrandForMatch:
    """canonical_brand_for_match: stable cross-source brand key for cross-ref."""

    def test_registered_code_resolves_to_canonical(self):
        from version_priority import canonical_brand_for_match
        assert canonical_brand_for_match(brand_code="FBK") == "FBK"

    def test_numeric_suffix_code_matches_via_brand_name(self):
        # Anna Molly bug: KN says "FBK", mirror says "FBK204" + "Funbox Karaoke".
        from version_priority import canonical_brand_for_match
        assert canonical_brand_for_match(
            brand_code="FBK204", brand_name="Funbox Karaoke") == "FBK"

    def test_numeric_suffix_code_matches_via_prefix_fallback(self):
        # Even with no brand_name, "FBK204" should fold to the "FBK" family.
        from version_priority import canonical_brand_for_match
        assert canonical_brand_for_match(brand_code="FBK204") == "FBK"

    def test_cc_aliases_collapse_together(self):
        from version_priority import canonical_brand_for_match
        assert canonical_brand_for_match(brand_code="CCK") == "CC"
        assert canonical_brand_for_match(brand_code="CCX") == "CC"

    def test_local_disc_id_resolves(self):
        from version_priority import canonical_brand_for_match
        assert canonical_brand_for_match(disc_id="KVD-22524") == "KV"

    def test_unregistered_brand_folds_to_alpha_prefix(self):
        from version_priority import canonical_brand_for_match
        # KFN not in registry -> stable "KFN" key from the alpha prefix
        assert canonical_brand_for_match(brand_code="KFN-1234") == "KFN"

    def test_empty_inputs_return_empty_string(self):
        from version_priority import canonical_brand_for_match
        assert canonical_brand_for_match() == ""


class TestAnnotateVersionsDivebarShape:
    def test_divebar_community_row_ranks_in_community_tier(self):
        rows = [{"source": "divebar", "file_id": "x", "brand_code": "CC",
                 "brand_name": "CC Karaoke", "artist": "Queen", "title": "X",
                 "in_gcs": True}]
        annotate_versions(rows, _cfg(), shape="rotation_search_divebar")
        assert rows[0]["priority_brand"] == "CC"
        assert rows[0]["priority_class"] == "community"
        assert rows[0]["priority_rank"] < 1000

    def test_divebar_row_beats_youtube_only_kn_same_brand(self):
        # A standalone divebar (GCS) LC row should outrank a youtube-only LC KN row.
        kn = {"brand_code": "LC", "youtube_url": "https://yt", "is_community": True}
        db = {"source": "divebar", "file_id": "x", "brand_code": "LC",
              "brand_name": "Lemmy Caution", "artist": "Q", "title": "X"}
        annotate_versions([kn], _cfg(), shape="rotation_search_kn")
        annotate_versions([db], _cfg(), shape="rotation_search_divebar")
        assert db["priority_rank"] < kn["priority_rank"]
