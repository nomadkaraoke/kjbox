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
