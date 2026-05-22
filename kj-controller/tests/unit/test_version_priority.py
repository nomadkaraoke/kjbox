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
