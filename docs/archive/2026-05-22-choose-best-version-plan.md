# Choose-Best-Version Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a brand-priority ranking system that surfaces the KJ's in-head version-quality rules ("community always beats commercial; CC > LC > FBK …") in both the kj_pick approval card and the rotation Add/Link search dropdown.

**Architecture:** New `version_priority.py` Python module owns canonical brand registry, alias resolution, and rank computation. Backend annotates every version in `/rotation/search` responses and the kj_pick versions snapshot with a `priority_rank` integer. Frontend reads the rank, sorts by it, and applies a hero-card UX in the kj_pick picker plus section-header sorting in the rotation dropdown. Settings panel grows from one to two priority lists.

**Tech Stack:** Python 3 / Flask backend, vanilla JS frontend (no build step), pytest, SQLite-backed config via `save_config_value`.

**Spec:** [`docs/archive/2026-05-22-choose-best-version-design.md`](2026-05-22-choose-best-version-design.md)

**Files we'll create or modify:**

| Path | Action | Responsibility |
|---|---|---|
| `kj-controller/version_priority.py` | Create | Canonical brand registry, alias resolution, rank computation, annotation helper |
| `kj-controller/tests/unit/test_version_priority.py` | Create | Unit tests for the new module |
| `kj-controller/routes.py` | Modify | Annotate versions in `_group_search_results` + `unified_search`; rewrite `/karaoke-nerds/config` GET/POST |
| `kj-controller/tests/unit/test_rotation_search.py` | Modify | Assert annotation appears on responses |
| `kj-controller/tests/unit/test_search_grouping.py` | Modify | Assert grouped versions get sorted by `priority_rank` |
| `kj-controller/tests/unit/test_karaoke_nerds_config.py` | Create | Tests for the new config endpoint shape |
| `kj-controller/templates/index.html` | Modify | Replace single-input KN prefs panel with two-list version |
| `kj-controller/static/app.js` | Modify | Remove `sortKNTracks` + `versionBucket`; new `renderKjPickPicker` hero card; new `renderRotSearchDropdown` global sort + section headers; new prefs save/load |
| `kj-controller/static/style.css` | Modify | Hero card styles + section header styles + top-3 highlight |

---

## Task 1: Create `version_priority` module skeleton with brand registries

**Files:**
- Create: `kj-controller/version_priority.py`
- Create: `kj-controller/tests/unit/test_version_priority.py`

- [ ] **Step 1: Write failing test for registry shape**

Create `kj-controller/tests/unit/test_version_priority.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run from `kj-controller/`:
```bash
pytest tests/unit/test_version_priority.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'version_priority'`.

- [ ] **Step 3: Create the module with the registries**

Create `kj-controller/version_priority.py`:

```python
"""Brand-priority registry, alias resolution, and ranking for karaoke versions.

Used to surface the KJ's in-head version-quality rules in the kj_pick approval
card and the rotation Add/Link search dropdown. Community brands always rank
above commercial; within each tier, priority follows a configurable order.

Design spec: docs/archive/2026-05-22-choose-best-version-design.md
"""

import re
from typing import Optional, Tuple

# (canonical_code, aliases_including_canonical, display_name)
# Order in these lists is the DEFAULT priority order (top wins).
# Config can re-order via kn_priority_community / kn_priority_commercial.
COMMUNITY_BRANDS = [
    ("CC",     ["CC", "CCK", "CCX", "CC Karaoke", "CC Karaoke X"], "CC Karaoke"),
    ("LC",     ["LC", "LEMMY", "Lemmy Caution"],                   "Lemmy Caution"),
    ("FBK",    ["FBK", "Funbox", "Funbox Karaoke"],                "Funbox Karaoke"),
    ("BELLY",  ["BELLY", "BellySings"],                            "BellySings"),
    ("NOMAD",  ["NOMAD", "Nomad", "Nomad Karaoke"],                "Nomad Karaoke"),
    ("FAKEY",  ["FAKEY", "FakeyOke"],                              "FakeyOke"),
    ("PMK",    ["PMK", "Punk Media Karaoke"],                      "Punk Media Karaoke"),
    ("OBSK",   ["OBSK", "ObsKure", "ObsKure Karaoke"],             "ObsKure Karaoke"),
    # high-frequency unlisted, ranked below KJ-stated brands
    ("SDK",    ["SDK", "SNDL", "SNDL Karaoke"],                    "SNDL Karaoke"),
    ("DBK",    ["DBK", "Deep Bench", "Deep Bench Karaoke"],        "Deep Bench Karaoke"),
]

COMMERCIAL_BRANDS = [
    ("KV",     ["KV", "KVD", "KCD", "Karaoke Version",
                "Karaoke Cloud Digitrax", "Karafun"],              "Karaoke Version"),
    ("SC",     ["SC", "Sound Choice"],                             "Sound Choice"),
    ("SBI",    ["SBI", "SBI Karaoke"],                             "SBI Karaoke"),
    ("SF",     ["SF", "Sunfly"],                                   "Sunfly"),
    ("CB",     ["CB", "CBD", "Chart Buster", "Chartbuster"],       "Chart Buster"),
    ("ZM",     ["ZM", "ZOOM", "Zoom"],                             "Zoom"),
    # high-frequency unlisted
    ("VS",     ["VS", "Vocal Star"],                               "Vocal Star"),
    ("SK",     ["SK", "Sing King"],                                "Sing King"),
    ("MR",     ["MR", "Mr. Entertainer", "Mr Entertainer"],        "Mr. Entertainer"),
    ("PT",     ["PT", "Party Tyme"],                               "Party Tyme"),
    ("EK",     ["EK", "EDK", "EdKara", "EDKARA", "Easy Karaoke"],  "Easy Karaoke"),
]

COMMUNITY_DEFAULTS = [c for (c, _, _) in COMMUNITY_BRANDS]
COMMERCIAL_DEFAULTS = [c for (c, _, _) in COMMERCIAL_BRANDS]
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pytest tests/unit/test_version_priority.py -v
```
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-choose-best-version
git add kj-controller/version_priority.py kj-controller/tests/unit/test_version_priority.py
git commit -m "feat(version-priority): add canonical brand registry with aliases"
```

---

## Task 2: Build alias lookup table + `resolve_brand` function

**Files:**
- Modify: `kj-controller/version_priority.py`
- Modify: `kj-controller/tests/unit/test_version_priority.py`

- [ ] **Step 1: Write failing tests for `resolve_brand`**

Append to `kj-controller/tests/unit/test_version_priority.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_version_priority.py -v
```
Expected: FAIL with `ImportError: cannot import name 'resolve_brand'`.

- [ ] **Step 3: Implement `resolve_brand` and the alias table**

Append to `kj-controller/version_priority.py`:

```python
# Module-level alias lookup tables, built once on import.
# Maps alias.upper() -> (canonical, classification).
_ALIAS_TO_CANONICAL: dict = {}
_CANONICAL_TO_CLASS: dict = {}

for canonical, aliases, _display in COMMUNITY_BRANDS:
    _CANONICAL_TO_CLASS[canonical] = "community"
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.upper().strip()] = (canonical, "community")

for canonical, aliases, _display in COMMERCIAL_BRANDS:
    _CANONICAL_TO_CLASS[canonical] = "commercial"
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.upper().strip()] = (canonical, "commercial")


# Regex for extracting alpha-prefix brand code from a local disc_id.
# Stops at the first non-letter character so "KVD-22524" -> "KVD", "SC2411-08" -> "SC".
_DISC_ID_PREFIX_RE = re.compile(r'^([A-Z]+)')

# Regex for the YouTube-download filename pattern:
# VIDEOID__BrandName__Artist - Title.ext
# (Allows brand_name to contain spaces, punctuation, anything except __.)
_YT_FILENAME_RE = re.compile(r'^[^_]+__([^_]+?)__')


def _lookup_alias(raw: str) -> Optional[Tuple[str, str]]:
    if not raw:
        return None
    key = raw.upper().strip()
    return _ALIAS_TO_CANONICAL.get(key)


def resolve_brand(
    *,
    disc_id: Optional[str] = None,
    filename: Optional[str] = None,
    brand_code: Optional[str] = None,
    brand_name: Optional[str] = None,
    is_community: Optional[bool] = None,
) -> Tuple[Optional[str], str]:
    """Resolve any of the available identifiers to (canonical_code, classification).

    Resolution order (first hit wins):
        1. brand_code  -> alias lookup
        2. brand_name  -> alias lookup
        3. disc_id     -> extract alpha prefix, alias lookup
        4. filename    -> match YT-download pattern, alias lookup on middle segment

    classification is one of: "community", "commercial", "unknown".

    is_community (when not None) overrides the classification — a community
    track tagged with a commercial brand_code still classifies as community.
    """
    hit = _lookup_alias(brand_code)
    if hit is None:
        hit = _lookup_alias(brand_name)
    if hit is None and disc_id:
        m = _DISC_ID_PREFIX_RE.match(disc_id.strip())
        if m:
            hit = _lookup_alias(m.group(1))
    if hit is None and filename:
        m = _YT_FILENAME_RE.match(filename.strip())
        if m:
            hit = _lookup_alias(m.group(1))

    if hit is None:
        if is_community is True:
            return (None, "community")
        return (None, "unknown")

    canonical, cls = hit
    if is_community is True:
        return (canonical, "community")
    if is_community is False:
        # Respect the explicit non-community flag.
        return (canonical, cls if cls == "commercial" else "commercial")
    return (canonical, cls)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_version_priority.py -v
```
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/version_priority.py kj-controller/tests/unit/test_version_priority.py
git commit -m "feat(version-priority): resolve_brand with alias + disc_id + filename + brand_name"
```

---

## Task 3: Implement `rank_version` and `annotate_versions`

**Files:**
- Modify: `kj-controller/version_priority.py`
- Modify: `kj-controller/tests/unit/test_version_priority.py`

- [ ] **Step 1: Write failing tests for ranking**

Append to `test_version_priority.py`:

```python
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
        v = {"source": "kn", "kn": {"brand_code": "WHATEVER"}}
        r = rank_version(v, _cfg())
        assert 3000 <= r < 4000

    def test_unknown_at_4000(self):
        v = {"source": "kn", "kn": {"brand_code": "", "youtube_url": "x"}}
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
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/unit/test_version_priority.py -v
```
Expected: FAIL with `ImportError: cannot import name 'rank_version'`.

- [ ] **Step 3: Implement `rank_version` and `annotate_versions`**

Append to `kj-controller/version_priority.py`:

```python
# Rank tier constants. See design spec § Section 1 Ranking.
TIER_COMMUNITY_BASE        = 0
TIER_COMMUNITY_UNKNOWN     = 1000
TIER_COMMERCIAL_BASE       = 2000
TIER_COMMERCIAL_UNKNOWN    = 3000
TIER_TOTAL_UNKNOWN         = 4000

SOURCE_TIEBREAKER_LOCAL    = 0
SOURCE_TIEBREAKER_DIVEBAR  = 1
SOURCE_TIEBREAKER_YOUTUBE  = 2


def _priority_list(cfg, key, default):
    """Read a priority list from config; fall back to default if missing/empty."""
    value = (cfg or {}).get(key)
    if not value:
        return default
    # Sanitize: uppercase, strip, drop empties
    return [str(c).upper().strip() for c in value if str(c).strip()]


def _source_tiebreaker(version):
    """Map a version dict (either kj_pick or rotation_search shape) to a
    source tiebreaker offset.
    """
    src = version.get("source")
    if src == "local":
        return SOURCE_TIEBREAKER_LOCAL
    if src == "kn":
        kn = version.get("kn") or {}
        if (kn.get("divebar") or {}).get("file_id"):
            return SOURCE_TIEBREAKER_DIVEBAR
        return SOURCE_TIEBREAKER_YOUTUBE
    # rotation_search local-row shape (raw {path, disc_id, ...}): treat as local
    if version.get("path") and not version.get("source"):
        return SOURCE_TIEBREAKER_LOCAL
    # rotation_search kn-track shape (raw {brand_code, youtube_url, ...}):
    # divebar if cross-ref present, else youtube
    if (version.get("divebar") or {}).get("file_id"):
        return SOURCE_TIEBREAKER_DIVEBAR
    if version.get("youtube_url"):
        return SOURCE_TIEBREAKER_YOUTUBE
    return SOURCE_TIEBREAKER_YOUTUBE


def _extract_brand_inputs(version):
    """Pull brand-resolution inputs from any of the supported version shapes.
    Returns kwargs for resolve_brand().
    """
    src = version.get("source")
    if src == "local":
        local = version.get("local") or {}
        return {"disc_id": local.get("disc_id"),
                "filename": local.get("filename")}
    if src == "kn":
        kn = version.get("kn") or {}
        return {"brand_code": kn.get("brand_code"),
                "brand_name": kn.get("brand_name"),
                "is_community": kn.get("is_community")}
    # rotation_search shapes have no "source" key — use field presence.
    if version.get("path") or version.get("disc_id"):
        return {"disc_id": version.get("disc_id"),
                "filename": version.get("filename")}
    return {"brand_code": version.get("brand_code"),
            "brand_name": version.get("brand_name"),
            "is_community": version.get("is_community")}


def rank_version(version, cfg) -> int:
    """Return a sortable integer rank for a version. Lower = better.

    See design spec § Section 1 Ranking for the tier layout.
    """
    inputs = _extract_brand_inputs(version)
    canonical, classification = resolve_brand(**inputs)
    tiebreaker = _source_tiebreaker(version)

    if classification == "community":
        if canonical is None:
            return TIER_COMMUNITY_UNKNOWN + tiebreaker
        priority_list = _priority_list(cfg, "kn_priority_community",
                                       COMMUNITY_DEFAULTS)
        try:
            idx = priority_list.index(canonical)
            return TIER_COMMUNITY_BASE + (idx * 10) + tiebreaker
        except ValueError:
            return TIER_COMMUNITY_UNKNOWN + tiebreaker

    if classification == "commercial":
        if canonical is None:
            return TIER_COMMERCIAL_UNKNOWN + tiebreaker
        priority_list = _priority_list(cfg, "kn_priority_commercial",
                                       COMMERCIAL_DEFAULTS)
        try:
            idx = priority_list.index(canonical)
            return TIER_COMMERCIAL_BASE + (idx * 10) + tiebreaker
        except ValueError:
            return TIER_COMMERCIAL_UNKNOWN + tiebreaker

    return TIER_TOTAL_UNKNOWN + tiebreaker


def annotate_versions(versions, cfg, *, shape="kj_pick"):
    """Mutate each version dict in `versions` to add three fields:
        priority_rank: int   (lower = better)
        priority_brand: str | None  (canonical code)
        priority_class: "community" | "commercial" | "unknown"

    shape controls how brand inputs are extracted:
        "kj_pick"                - versions in kj_pick snapshot format
                                   ({source: "local"|"kn", ...})
        "rotation_search_local"  - raw local-result dicts (path, disc_id, ...)
        "rotation_search_kn"     - raw KN track dicts
                                   (brand_code, brand_name, youtube_url,
                                    is_community, divebar?)
    """
    if not versions:
        return versions
    for v in versions:
        if shape == "rotation_search_local":
            inputs = {"disc_id": v.get("disc_id"),
                      "filename": v.get("filename")}
        elif shape == "rotation_search_kn":
            inputs = {"brand_code": v.get("brand_code"),
                      "brand_name": v.get("brand_name"),
                      "is_community": v.get("is_community")}
        else:
            inputs = _extract_brand_inputs(v)
        canonical, classification = resolve_brand(**inputs)
        v["priority_brand"] = canonical
        v["priority_class"] = classification
        v["priority_rank"] = rank_version(v, cfg)
    return versions
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_version_priority.py -v
```
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/version_priority.py kj-controller/tests/unit/test_version_priority.py
git commit -m "feat(version-priority): rank_version + annotate_versions"
```

---

## Task 4: Wire `annotate_versions` into `_group_search_results`

**Files:**
- Modify: `kj-controller/routes.py` (around line 83 — `_group_search_results`)
- Modify: `kj-controller/tests/unit/test_search_grouping.py`

- [ ] **Step 1: Write failing test for grouped-result annotation**

Append to `kj-controller/tests/unit/test_search_grouping.py`:

```python
import pytest

from routes import _group_search_results


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
        # First version should be community (LC)
        assert versions[0]["priority_class"] == "community"
        assert versions[0]["priority_brand"] == "LC"
        # Second version should be commercial (KVD -> KV)
        assert versions[1]["priority_class"] == "commercial"
        assert versions[1]["priority_brand"] == "KV"

    def test_unranked_versions_keep_existing_behaviour(self):
        # No brand info anywhere — versions still get annotated as unknown
        local = [{"path": "/a.zip", "disc_id": "",
                  "filename": "plain.zip",
                  "artist": "X", "title": "Y"}]
        groups = _group_search_results(local, [])
        assert groups[0]["versions"][0]["priority_class"] == "unknown"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/unit/test_search_grouping.py -v
```
Expected: New tests FAIL — `priority_rank` missing.

- [ ] **Step 3: Wire the annotation into `_group_search_results`**

In `kj-controller/routes.py`, locate the existing `_group_search_results`
(around line 83). Add the import at the top of the file (with the other
local imports — search for `from sing import`):

```python
import version_priority
```

Then modify `_group_search_results` to annotate + sort versions before
returning. Find the existing return block at the end of the function:

```python
    out = []
    for g in groups.values():
        versions = g["versions"]
        kn_versions = [v for v in versions if v["source"] == "kn"]
        g["version_count"] = len(versions)
        g["in_library"] = any(
            v["source"] == "local"
            or (v["source"] == "kn" and (v["kn"].get("divebar") or {}).get("file_id"))
            for v in versions
        )
        # "has_community_only" only makes sense when we have KN versions AND no
        # locals — a normie-facing hint for Phase B's UI copy. False when empty.
        g["has_community_only"] = (
            bool(kn_versions)
            and not any(v["source"] == "local" for v in versions)
            and all(v["kn"].get("is_community") for v in kn_versions)
        )
        out.append(g)

    return out
```

Add the annotation + sort just before `out.append(g)`:

```python
    out = []
    for g in groups.values():
        versions = g["versions"]
        kn_versions = [v for v in versions if v["source"] == "kn"]
        g["version_count"] = len(versions)
        g["in_library"] = any(
            v["source"] == "local"
            or (v["source"] == "kn" and (v["kn"].get("divebar") or {}).get("file_id"))
            for v in versions
        )
        g["has_community_only"] = (
            bool(kn_versions)
            and not any(v["source"] == "local" for v in versions)
            and all(v["kn"].get("is_community") for v in kn_versions)
        )
        # Annotate every version with priority_rank/brand/class so the
        # frontend can sort + render section headers + hero card without
        # duplicating the registry. Then sort versions in-place so clients
        # that ignore the rank field still see best-first ordering.
        cfg = current_app.kj_config if current_app else {}
        version_priority.annotate_versions(versions, cfg, shape="kj_pick")
        versions.sort(key=lambda v: v.get("priority_rank", 9999))
        out.append(g)

    return out
```

The `current_app` reference at module level is safe inside this function
because it's only called from within a request context. The `if current_app
else {}` guard keeps test paths that import `_group_search_results` directly
(without an app context) from blowing up — they'll just use default
priorities, which is what the tests expect.

Actually for safety in tests, do this: import `current_app` is already
present in `routes.py` (top of file). Reference it inside a try/except:

```python
        try:
            cfg = current_app.kj_config
        except (RuntimeError, AttributeError):
            cfg = {}
        version_priority.annotate_versions(versions, cfg, shape="kj_pick")
        versions.sort(key=lambda v: v.get("priority_rank", 9999))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_search_grouping.py -v
```
Expected: All tests PASS, including the new three.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/unit/test_search_grouping.py
git commit -m "feat(rotation-search): annotate grouped versions with priority_rank"
```

---

## Task 5: Annotate `unified_search` results for the rotation Add/Link flow

**Files:**
- Modify: `kj-controller/routes.py` (`unified_search` around line 2622, returns at 2722+2727)
- Modify: `kj-controller/tests/unit/test_rotation_search.py`

- [ ] **Step 1: Write failing tests**

Append to `kj-controller/tests/unit/test_rotation_search.py`:

```python
class TestPriorityAnnotation:
    def test_local_results_annotated(self, search_client, search_app):
        with patch.object(search_app.catalog, 'is_available', return_value=True), \
             patch.object(search_app.catalog, 'search', return_value=[
                 {"path": "/media/song.zip", "artist": "Queen",
                  "title": "Bohemian Rhapsody", "format": "cdg+mp3",
                  "disc_id": "KVD-22524", "filename": "KVD-22524 - Queen - Bohemian Rhapsody.zip"}
             ]), \
             patch('routes.karaoke_nerds.search', return_value=[]):
            resp = search_client.get('/rotation/search?q=bohemian')
            data = resp.get_json()
            assert data["local"][0]["priority_brand"] == "KV"
            assert data["local"][0]["priority_class"] == "commercial"
            assert "priority_rank" in data["local"][0]

    def test_kn_tracks_annotated(self, search_client, search_app):
        with patch.object(search_app.catalog, 'is_available', return_value=True), \
             patch.object(search_app.catalog, 'search', return_value=[]), \
             patch('routes.karaoke_nerds.search', return_value=[
                 {"title": "Bohemian Rhapsody", "artist": "Queen", "tracks": [
                     {"brand_name": "Lemmy Caution", "brand_code": "LC",
                      "youtube_url": "https://youtube.com/watch?v=abc",
                      "is_community": True}
                 ]}
             ]):
            resp = search_client.get('/rotation/search?q=bohemian')
            data = resp.get_json()
            track = data["karaoke_nerds"][0]["tracks"][0]
            assert track["priority_brand"] == "LC"
            assert track["priority_class"] == "community"
            assert "priority_rank" in track
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/unit/test_rotation_search.py::TestPriorityAnnotation -v
```
Expected: FAIL — fields missing.

- [ ] **Step 3: Add annotation in `unified_search`**

In `kj-controller/routes.py`, locate the `unified_search` function (around
line 2622). It has two return paths at the bottom: the `grouped=True` path
(line ~2722) and the flat path (line ~2727).

Add annotation calls just before the final return statements. Find:

```python
    if grouped:
        # Defensive filter ...
        filtered_kn = []
        for song in kn_results:
            good_tracks = [...]
            if good_tracks:
                filtered_kn.append({**song, "tracks": good_tracks})
        return {
            "songs": _group_search_results(local_results, filtered_kn),
            "karaoke_nerds_timeout": kn_timeout,
        }

    return {
        "local": local_results,
        "karaoke_nerds": kn_results,
        "karaoke_nerds_timeout": kn_timeout,
    }
```

Modify the flat-return branch to annotate local + KN results in place:

```python
    if grouped:
        filtered_kn = []
        for song in kn_results:
            good_tracks = [
                t for t in song.get("tracks") or []
                if (t.get("youtube_url") or "").strip()
                or (t.get("divebar") or {}).get("file_id")
            ]
            if good_tracks:
                filtered_kn.append({**song, "tracks": good_tracks})
        return {
            "songs": _group_search_results(local_results, filtered_kn),
            "karaoke_nerds_timeout": kn_timeout,
        }

    # Flat path: annotate local results and every KN track in-place so the
    # frontend can sort + render with section headers.
    cfg = app.kj_config if hasattr(app, "kj_config") else {}
    version_priority.annotate_versions(
        local_results, cfg, shape="rotation_search_local")
    for song in kn_results:
        version_priority.annotate_versions(
            song.get("tracks") or [], cfg, shape="rotation_search_kn")

    return {
        "local": local_results,
        "karaoke_nerds": kn_results,
        "karaoke_nerds_timeout": kn_timeout,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_rotation_search.py -v
```
Expected: All tests PASS, including the new two.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/unit/test_rotation_search.py
git commit -m "feat(rotation-search): annotate flat-return local + KN results with priority_rank"
```

---

## Task 6: Replace `/karaoke-nerds/config` with two-list API

**Files:**
- Modify: `kj-controller/routes.py` (`kn_get_config` line 1203, `kn_set_config` line 1212)
- Create: `kj-controller/tests/unit/test_karaoke_nerds_config.py`

- [ ] **Step 1: Write failing tests for the new endpoint shape**

Create `kj-controller/tests/unit/test_karaoke_nerds_config.py`:

```python
"""Tests for the two-list /karaoke-nerds/config endpoint."""

import pytest

from app import create_app
from version_priority import COMMUNITY_DEFAULTS, COMMERCIAL_DEFAULTS


@pytest.fixture
def client(mock_config):
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


class TestGetConfig:
    def test_returns_defaults_when_unset(self, client):
        resp = client.get('/karaoke-nerds/config')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["priority_community"] == COMMUNITY_DEFAULTS
        assert data["priority_commercial"] == COMMERCIAL_DEFAULTS
        assert "aliases" in data
        assert "CC" in data["aliases"]
        assert "CCK" in [a.upper() for a in data["aliases"]["CC"]]

    def test_returns_overrides_when_set(self, client, mock_config):
        mock_config["kn_priority_community"] = ["LC", "CC"]
        mock_config["kn_priority_commercial"] = ["SC", "KV"]
        resp = client.get('/karaoke-nerds/config')
        data = resp.get_json()
        assert data["priority_community"] == ["LC", "CC"]
        assert data["priority_commercial"] == ["SC", "KV"]

    def test_ignores_legacy_kn_preferred_brands(self, client, mock_config):
        # Legacy field present — new endpoint returns defaults, not the
        # legacy list, so existing config doesn't leak through.
        mock_config["kn_preferred_brands"] = ["WHATEVER"]
        resp = client.get('/karaoke-nerds/config')
        data = resp.get_json()
        assert data["priority_community"] == COMMUNITY_DEFAULTS


class TestSetConfig:
    def test_saves_both_lists(self, client):
        resp = client.post('/karaoke-nerds/config',
                           json={"priority_community": ["LC", "CC"],
                                 "priority_commercial": ["SC", "KV"]})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["priority_community"] == ["LC", "CC"]
        assert data["priority_commercial"] == ["SC", "KV"]

    def test_rejects_unknown_canonical_code(self, client):
        resp = client.post('/karaoke-nerds/config',
                           json={"priority_community": ["WHATEVER"],
                                 "priority_commercial": []})
        assert resp.status_code == 400
        assert "WHATEVER" in resp.get_json()["error"]

    def test_uppercases_and_trims(self, client):
        resp = client.post('/karaoke-nerds/config',
                           json={"priority_community": [" lc ", "cc"],
                                 "priority_commercial": ["kv"]})
        assert resp.status_code == 200
        assert resp.get_json()["priority_community"] == ["LC", "CC"]

    def test_rejects_non_list(self, client):
        resp = client.post('/karaoke-nerds/config',
                           json={"priority_community": "not a list",
                                 "priority_commercial": []})
        assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/unit/test_karaoke_nerds_config.py -v
```
Expected: FAIL — current endpoint returns `preferred_brands`, not the new shape.

- [ ] **Step 3: Rewrite the endpoints**

In `kj-controller/routes.py`, replace `kn_get_config` (around line 1203)
and `kn_set_config` (around line 1212) with:

```python
@routes_bp.route('/karaoke-nerds/config', methods=['GET'])
def kn_get_config():
    """Return brand-priority config: two lists + alias hints.

    See docs/archive/2026-05-22-choose-best-version-design.md § Section 3b.
    """
    cfg = current_app.kj_config

    def _list_or_default(key, default):
        v = cfg.get(key)
        if not v:
            return list(default)
        return [str(c).upper().strip() for c in v if str(c).strip()]

    aliases = {}
    for canonical, alias_list, _display in version_priority.COMMUNITY_BRANDS:
        aliases[canonical] = list(alias_list)
    for canonical, alias_list, _display in version_priority.COMMERCIAL_BRANDS:
        aliases[canonical] = list(alias_list)

    return jsonify({
        "priority_community": _list_or_default(
            "kn_priority_community", version_priority.COMMUNITY_DEFAULTS),
        "priority_commercial": _list_or_default(
            "kn_priority_commercial", version_priority.COMMERCIAL_DEFAULTS),
        "aliases": aliases,
    })


@routes_bp.route('/karaoke-nerds/config', methods=['POST'])
def kn_set_config():
    """Update brand-priority config (two lists of canonical codes)."""
    data = request.get_json(silent=True) or {}
    community = data.get("priority_community")
    commercial = data.get("priority_commercial")

    if not isinstance(community, list) or not isinstance(commercial, list):
        return jsonify({
            "error": "priority_community and priority_commercial must be lists"
        }), 400

    community = [str(c).upper().strip() for c in community if str(c).strip()]
    commercial = [str(c).upper().strip() for c in commercial if str(c).strip()]

    valid_community = {c for (c, _, _) in version_priority.COMMUNITY_BRANDS}
    valid_commercial = {c for (c, _, _) in version_priority.COMMERCIAL_BRANDS}

    bad_community = [c for c in community if c not in valid_community]
    bad_commercial = [c for c in commercial if c not in valid_commercial]
    if bad_community or bad_commercial:
        problems = []
        if bad_community:
            problems.append(
                f"Unknown community codes: {bad_community}. "
                f"Valid: {sorted(valid_community)}")
        if bad_commercial:
            problems.append(
                f"Unknown commercial codes: {bad_commercial}. "
                f"Valid: {sorted(valid_commercial)}")
        return jsonify({"error": " | ".join(problems)}), 400

    current_app.kj_config['kn_priority_community'] = community
    current_app.kj_config['kn_priority_commercial'] = commercial
    save_config_value('kn_priority_community', community)
    save_config_value('kn_priority_commercial', commercial)
    log_message(
        f"Updated brand priorities: community={community}, commercial={commercial}",
        current_app.kj_config,
    )
    return jsonify({
        "priority_community": community,
        "priority_commercial": commercial,
    })
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_karaoke_nerds_config.py -v
```
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add kj-controller/routes.py kj-controller/tests/unit/test_karaoke_nerds_config.py
git commit -m "feat(api): two-list /karaoke-nerds/config (priority_community + priority_commercial)"
```

---

## Task 7: Run full backend test suite to catch regressions

**Files:** none (verification)

- [ ] **Step 1: Run the full backend test suite**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-choose-best-version/kj-controller
pytest -x --tb=short 2>&1 | tail -40
```
Expected: All tests PASS. If any test fails, fix root-cause issues before
moving to the frontend.

Common likely failures:
- An existing test that mocked `/karaoke-nerds/config` response shape and
  expected `preferred_brands` — update the assertion to the new shape.
- An existing search test that checked exact version ordering inside a
  group — should now match the priority-sorted order; update fixtures or
  assertions to reflect that.

- [ ] **Step 2: If any cascading failures: fix + commit per file**

For each file you fix, commit separately:
```bash
git add tests/unit/<file>.py
git commit -m "test(<area>): update for two-list config / priority sort"
```

---

## Task 8: Frontend — replace KN prefs panel HTML with two-list UI

**Files:**
- Modify: `kj-controller/templates/index.html` (line ~274)

- [ ] **Step 1: Locate the existing panel**

Open `kj-controller/templates/index.html`. Find the panel around line 274:

```html
<div id="kn-prefs-panel" class="kn-prefs hidden">
    <label for="kn-prefs-input">Preferred brand codes (comma-separated):</label>
    <div class="kn-prefs-row">
        <input type="text" id="kn-prefs-input" placeholder="e.g. KV, KFN">
        <button class="kn-prefs-save" onclick="saveKNPrefs()">Save</button>
    </div>
    <div id="kn-prefs-tags" class="kn-prefs-tags"></div>
</div>
```

- [ ] **Step 2: Replace with the two-list panel**

Replace with:

```html
<div id="kn-prefs-panel" class="kn-prefs hidden">
    <div class="kn-prefs-section">
        <label for="kn-prefs-community-input">
            Community brands (top wins):
        </label>
        <textarea id="kn-prefs-community-input" class="kn-prefs-textarea"
                  rows="2"
                  placeholder="CC, LC, FBK, BELLY, NOMAD, FAKEY, PMK, OBSK"></textarea>
        <div id="kn-prefs-community-aliases" class="kn-prefs-aliases"></div>
    </div>
    <div class="kn-prefs-section">
        <label for="kn-prefs-commercial-input">
            Commercial brands (used when no community version):
        </label>
        <textarea id="kn-prefs-commercial-input" class="kn-prefs-textarea"
                  rows="2"
                  placeholder="KV, SC, SBI, SF, CB, ZM"></textarea>
        <div id="kn-prefs-commercial-aliases" class="kn-prefs-aliases"></div>
    </div>
    <div class="kn-prefs-actions">
        <button class="kn-prefs-save" onclick="saveKNPrefs()">Save</button>
        <button class="kn-prefs-reset" onclick="resetKNPrefs()">Reset to defaults</button>
    </div>
</div>
```

- [ ] **Step 3: Update the Jinja config injection**

Locate around line 763:

```html
knPreferredBrands: {{ kn_preferred_brands | tojson }}
```

Leave that line in place for back-compat (the variable name will still be
read by removed code; we'll remove it in Task 12). No edit yet.

- [ ] **Step 4: Visual sanity check (no JS yet)**

Run the dev server and open `http://localhost:5000`. Click the "Prefs"
button next to "Search Karaoke Nerds". Confirm both textareas render and
the Save / Reset buttons appear. Save will not work yet (no JS) — that's
expected.

(If you can't run the dev server, skip — Task 9's JS reset/save will
exercise this.)

- [ ] **Step 5: Commit**

```bash
git add kj-controller/templates/index.html
git commit -m "feat(ui): two-list KN prefs panel (community + commercial)"
```

---

## Task 9: Frontend — rewrite `toggleKNPrefs` / `saveKNPrefs` / add `resetKNPrefs`

**Files:**
- Modify: `kj-controller/static/app.js` (around lines 2664–2700)

- [ ] **Step 1: Replace the three prefs functions**

Find the block in `app.js` starting at `function toggleKNPrefs()` (line
~2664) and ending after `async function saveKNPrefs()` (line ~2700).

Replace with:

```javascript
// --- KN brand-priority preferences ---
// Server response shape: {priority_community, priority_commercial, aliases}
let knPriorityCommunity = [];
let knPriorityCommercial = [];
let knAliases = {};

async function fetchKnPrefs() {
    try {
        const resp = await fetch('/karaoke-nerds/config');
        if (!resp.ok) return;
        const data = await resp.json();
        knPriorityCommunity = data.priority_community || [];
        knPriorityCommercial = data.priority_commercial || [];
        knAliases = data.aliases || {};
    } catch (_) { /* leave empty */ }
}

function renderKnAliasHints(canonicals, containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    const hints = canonicals
        .filter(c => (knAliases[c] || []).some(a => a.toUpperCase() !== c.toUpperCase()))
        .slice(0, 6)
        .map(c => {
            const extra = (knAliases[c] || [])
                .filter(a => a.toUpperCase() !== c.toUpperCase());
            return `${c} → ${extra.join(', ')}`;
        });
    el.textContent = hints.length
        ? 'Aliases recognized: ' + hints.join('; ')
        : '';
}

async function toggleKNPrefs() {
    const panel = document.getElementById('kn-prefs-panel');
    panel.classList.toggle('hidden');
    if (!panel.classList.contains('hidden')) {
        await fetchKnPrefs();
        document.getElementById('kn-prefs-community-input').value =
            knPriorityCommunity.join(', ');
        document.getElementById('kn-prefs-commercial-input').value =
            knPriorityCommercial.join(', ');
        renderKnAliasHints(knPriorityCommunity, 'kn-prefs-community-aliases');
        renderKnAliasHints(knPriorityCommercial, 'kn-prefs-commercial-aliases');
    }
}

async function saveKNPrefs() {
    const community = document.getElementById('kn-prefs-community-input').value
        .split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
    const commercial = document.getElementById('kn-prefs-commercial-input').value
        .split(',').map(s => s.trim().toUpperCase()).filter(Boolean);

    const resp = await fetch('/karaoke-nerds/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            priority_community: community,
            priority_commercial: commercial,
        }),
    });
    const data = await resp.json();
    if (!resp.ok) {
        log(`Save failed: ${data.error || resp.statusText}`, 'error');
        return;
    }
    knPriorityCommunity = data.priority_community;
    knPriorityCommercial = data.priority_commercial;
    log(`Updated brand priorities`, 'success');
    // Re-trigger a search if one is currently rendered so the new order applies.
    const results = document.getElementById('kn-results');
    if (results && results.children.length > 0) searchKaraokeNerds();
}

async function resetKNPrefs() {
    if (!confirm('Reset brand priorities to defaults?')) return;
    // Empty post means "use defaults" — but we need to send the canonical
    // defaults explicitly so the backend persists them (the GET endpoint
    // falls back to defaults when missing, but POST stores what we send).
    // Fetch defaults via a fresh GET on a config we know is the default,
    // then save them.
    const resp = await fetch('/karaoke-nerds/config?_fresh=1');
    const data = await resp.json();
    // Re-save the defaults so the persisted config matches what the user sees.
    const saveResp = await fetch('/karaoke-nerds/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            priority_community: data.priority_community,
            priority_commercial: data.priority_commercial,
        }),
    });
    if (saveResp.ok) {
        knPriorityCommunity = data.priority_community;
        knPriorityCommercial = data.priority_commercial;
        document.getElementById('kn-prefs-community-input').value =
            knPriorityCommunity.join(', ');
        document.getElementById('kn-prefs-commercial-input').value =
            knPriorityCommercial.join(', ');
        renderKnAliasHints(knPriorityCommunity, 'kn-prefs-community-aliases');
        renderKnAliasHints(knPriorityCommercial, 'kn-prefs-commercial-aliases');
        log('Reset brand priorities to defaults', 'success');
    }
}
```

Also remove the now-unused legacy bits. Locate at line ~2401:

```javascript
let knPreferredBrands = window.KJ_CONFIG.knPreferredBrands || [];
```

Leave that line in place for now — `sortKNTracks` and a couple of other call
sites still reference it. We'll delete in Task 12 once they're all gone.

Also remove `function renderKNPrefsTags()` (it referenced the dead
`#kn-prefs-tags` element which we deleted from the template).

Find and delete:
```javascript
function renderKNPrefsTags() {
    const container = document.getElementById('kn-prefs-tags');
    container.innerHTML = '';
    knPreferredBrands.forEach(code => {
        const tag = document.createElement('span');
        tag.className = 'kn-brand-tag';
        tag.textContent = code;
        container.appendChild(tag);
    });
}
```

- [ ] **Step 2: Browser smoke check**

Restart your dev server, open the page, click Prefs:
- Both textareas populate with the defaults
- Alias hints render below each textarea
- Save shows "Updated brand priorities" log entry
- Reset to defaults restores the original values

(Skip if you can't run a dev server — the changes are vanilla JS and the
template binding is straightforward.)

- [ ] **Step 3: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat(ui): two-list KN prefs save/load + alias hints"
```

---

## Task 10: Frontend — rewrite `renderKjPickPicker` (hero card + collapsed alternates)

**Files:**
- Modify: `kj-controller/static/app.js` (lines 5375–5444)

- [ ] **Step 1: Replace `renderKjPickPicker`, `versionBucket`, `renderVersionCard`**

Find the block:

```javascript
    function renderKjPickPicker(req) {
        // ... (sort by 4-bucket scheme)
    }

    function versionBucket(v) {
        if (v.source === 'local') return 0;
        if (v.source === 'kn' && v.kn && v.kn.divebar && v.kn.divebar.file_id) return 1;
        if (v.source === 'kn' && v.kn && v.kn.is_community) return 2;
        return 3;
    }

    function renderVersionCard(reqId, v, idx) {
        // ... current per-version card with Approve button
    }
```

Replace with:

```javascript
    function renderKjPickPicker(req) {
        let versions;
        try {
            const meta = typeof req.source_meta === 'string'
                ? JSON.parse(req.source_meta || '{}')
                : (req.source_meta || {});
            versions = meta.versions || [];
        } catch (e) {
            return null;
        }
        if (!versions.length) {
            const empty = document.createElement('div');
            empty.className = 'pr-picker empty';
            empty.textContent = 'No versions in snapshot — edit or reject.';
            return empty;
        }

        // Prefer backend-computed priority_rank; fall back to legacy
        // 4-bucket sort for snapshots written before this deploy.
        const hasRank = versions.some(v => typeof v.priority_rank === 'number');
        const ranked = versions
            .map((v, idx) => ({
                v, idx,
                rank: hasRank
                    ? (typeof v.priority_rank === 'number' ? v.priority_rank : 9999)
                    : versionBucketLegacy(v),
            }))
            .sort((a, b) => a.rank - b.rank);

        const container = document.createElement('div');
        container.className = 'pr-picker';

        // Hero card for the best version
        const best = ranked[0];
        container.appendChild(renderHeroCard(req.id, best.v, best.idx));

        // Alternates inside <details>, collapsed by default unless
        // all versions are tier-unknown (rank >= 4000).
        if (ranked.length > 1) {
            const alternates = ranked.slice(1);
            const details = document.createElement('details');
            details.className = 'pr-picker-alternates';
            const allUnknown = ranked.every(r => r.rank >= 4000);
            if (allUnknown) details.setAttribute('open', '');
            const summary = document.createElement('summary');
            summary.textContent = `Show ${alternates.length} other version${alternates.length === 1 ? '' : 's'}`;
            details.appendChild(summary);
            for (const { v, idx } of alternates) {
                details.appendChild(renderVersionCard(req.id, v, idx));
            }
            container.appendChild(details);
        }

        return container;
    }

    // Legacy bucket sort, retained for back-compat with snapshots written
    // before priority_rank was added to the version schema.
    function versionBucketLegacy(v) {
        if (v.source === 'local') return 0;
        if (v.source === 'kn' && v.kn && v.kn.divebar && v.kn.divebar.file_id) return 1;
        if (v.source === 'kn' && v.kn && v.kn.is_community) return 2;
        return 3;
    }

    function renderHeroCard(reqId, v, idx) {
        const meta = describeVersion(v);
        const card = document.createElement('div');
        card.className = 'pr-version-hero pr-class-' + meta.klass;
        card.innerHTML = `
          <div class="pr-hero-header">
            <span class="pr-hero-star">⭐</span>
            <span class="pr-hero-title">BEST: ${escapeHtml(meta.primary)}</span>
            <span class="pr-hero-class">${escapeHtml(meta.klass)}</span>
          </div>
          <div class="pr-hero-secondary">${escapeHtml(meta.secondary)}</div>
          <div class="pr-hero-source">${escapeHtml(meta.sourceLabel)}</div>
          <div class="pr-hero-cta-wrap">
            <button class="pr-hero-cta btn-approve-version" data-idx="${idx}">
              Approve with this →
            </button>
          </div>
        `;
        card.querySelector('.pr-hero-cta').addEventListener('click', () => {
            approve(reqId, { versionIndex: idx });
        });
        return card;
    }

    function renderVersionCard(reqId, v, idx) {
        const meta = describeVersion(v);
        const card = document.createElement('div');
        card.className = 'pr-version pr-class-' + meta.klass;
        card.innerHTML = `
          <div class="pr-v-icon">${meta.icon}</div>
          <div class="pr-v-main">
            <div class="pr-v-primary">${escapeHtml(meta.primary)}</div>
            <div class="pr-v-secondary">${escapeHtml(meta.secondary)}</div>
          </div>
          <button class="btn-approve-version" data-idx="${idx}">Approve →</button>
        `;
        card.querySelector('.btn-approve-version').addEventListener('click', () => {
            approve(reqId, { versionIndex: idx });
        });
        return card;
    }

    // Describe a version for both hero card and alternate card rendering.
    // Returns {icon, primary, secondary, sourceLabel, klass}.
    function describeVersion(v) {
        const klass = v.priority_class || 'unknown';
        if (v.source === 'local') {
            const local = v.local || {};
            const brand = v.priority_brand
                ? `${v.priority_brand} (local file)`
                : 'Local file';
            return {
                icon: '📁',
                primary: brand,
                secondary: local.filename || local.path || '',
                sourceLabel: 'Local file — plays instantly',
                klass,
            };
        }
        if (v.source === 'kn' && v.kn) {
            const kn = v.kn;
            const hasDivebar = kn.divebar && kn.divebar.file_id;
            const isCommunity = !!kn.is_community;
            const icon = hasDivebar ? '💿' : (isCommunity ? '🎤' : '📺');
            const brandLabel = v.priority_brand
                ? `${kn.brand_name || kn.brand_code || v.priority_brand} (${v.priority_brand})`
                : (kn.brand_name || kn.brand_code || 'Karaoke Nerds');
            const sourceLabel = hasDivebar
                ? 'Divebar mirror — will download on approve'
                : 'YouTube — will download on approve';
            return {
                icon,
                primary: `${brandLabel} — ${isCommunity ? 'community' : 'commercial'}`,
                secondary: kn.title || kn.song_title || '',
                sourceLabel,
                klass,
            };
        }
        return {
            icon: '🎵',
            primary: 'Unknown source',
            secondary: '',
            sourceLabel: '',
            klass,
        };
    }
```

- [ ] **Step 2: Manual sanity check (if able)**

If you can submit a kj_pick request from a singer device while in dev mode,
verify:
- The hero card shows the best version with a green Approve CTA
- The `<details>` "Show N other versions" collapses by default
- Expanding shows the alternates ranked by priority

If no test singer device available, defer to live verification post-deploy.

- [ ] **Step 3: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat(ui): kj_pick approval hero card + collapsed alternates"
```

---

## Task 11: Frontend — global priority sort + section headers in rotation dropdown

**Files:**
- Modify: `kj-controller/static/app.js` (`renderRotSearchDropdown` ~line 4825)

- [ ] **Step 1: Rewrite `renderRotSearchDropdown`**

Locate the function starting `function renderRotSearchDropdown(data)` (line
4825). Replace the whole function with:

```javascript
function renderRotSearchDropdown(data) {
    const dropdown = document.getElementById('rotation-search-dropdown');
    if (!dropdown) return;
    rotSearchResults = [];
    rotSearchSelectedIdx = -1;

    const localResults = data.local || [];
    const knSongs = data.karaoke_nerds || [];
    const downloadedIdToPath = new Map(
        localMediaItems.filter(i => i.youtube_id).map(i => [i.youtube_id, i.file_path])
    );

    let html = '';

    if (localResults.length === 0 && knSongs.length === 0) {
        html = '<div class="search-header">No results found</div>';
        html += '<div class="rotation-search-hint">↑↓ navigate · Enter select · Tab skip · Esc close</div>';
        dropdown.innerHTML = html;
        dropdown.classList.remove('hidden');
        return;
    }

    // Build a single flat list of renderable rows with priority info,
    // then sort by priority_rank across the WHOLE result set.
    const rows = [];

    for (const match of localResults) {
        rows.push({
            kind: 'local',
            rank: typeof match.priority_rank === 'number' ? match.priority_rank : 9999,
            klass: match.priority_class || 'unknown',
            brand: match.priority_brand,
            data: match,
        });
    }
    for (const song of knSongs) {
        for (const track of (song.tracks || [])) {
            rows.push({
                kind: 'kn',
                rank: typeof track.priority_rank === 'number' ? track.priority_rank : 9999,
                klass: track.priority_class || 'unknown',
                brand: track.priority_brand,
                song, track,
            });
        }
    }
    rows.sort((a, b) => a.rank - b.rank);

    // Walk sorted rows, emit section headers when priority class changes,
    // and highlight the top-3 (recognized-community OR best-3 if no community).
    let currentClass = null;
    const topCount = Math.min(3, rows.length);
    const highlightAll = !rows.some(r => r.klass === 'community' && r.rank < 1000);

    rows.forEach((r, displayIdx) => {
        if (r.klass !== currentClass) {
            html += renderRotSectionHeader(r.klass, r.brand, displayIdx === 0);
            currentClass = r.klass;
        }
        const idx = rotSearchResults.length;
        const isTop = (r.klass === 'community' && r.rank < 1000)
            || (highlightAll && displayIdx < topCount);
        const isBest = displayIdx === 0;
        if (r.kind === 'local') {
            html += renderRotLocalRow(r.data, idx, isTop, isBest);
            const match = r.data;
            rotSearchResults.push({
                type: 'local', path: match.path, duration: match.duration,
                song_artist: (match.title || '') + ' - ' + (match.artist || ''),
            });
        } else {
            const built = renderRotKnRow(r.song, r.track, idx, isTop, isBest, downloadedIdToPath);
            if (built) {
                html += built.html;
                rotSearchResults.push(built.result);
            }
        }
    });

    // MAKE option always at the bottom
    const songInput = document.getElementById('rotation-song');
    const rawQuery = songInput ? songInput.value.trim() : '';
    const makeIdx = rotSearchResults.length;
    rotSearchResults.push({
        type: 'make', badge: 'MAKE', badgeClass: 'search-badge-make',
        title: 'Create karaoke video for: ' + rawQuery,
        meta: 'Generate via Nomad Gen · Takes ~5 min',
        rawQuery: rawQuery,
    });
    html += '<div class="rotation-search-result' + (makeIdx === rotSearchSelectedIdx ? ' selected' : '') + '" data-idx="' + makeIdx + '" onclick="selectRotSearchResult(rotSearchResults[' + makeIdx + '])">' +
        '<span class="search-badge search-badge-make">MAKE</span>' +
        '<div class="search-info">' +
            '<div class="search-title">' + escHtml('Create karaoke video for: ' + rawQuery) + '</div>' +
            '<div class="search-meta">Generate via Nomad Gen · Takes ~5 min</div>' +
        '</div>' +
    '</div>';

    html += '<div class="rotation-search-hint">↑↓ navigate · Enter select · Tab skip · Esc close</div>';

    dropdown.innerHTML = html;
    dropdown.classList.remove('hidden');

    // Default-select the top row so Enter picks the best version.
    if (rotSearchResults.length > 0) {
        rotSearchSelectedIdx = 0;
        highlightRotSearchResult();
    }
}

function renderRotSectionHeader(klass, brand, isFirst) {
    if (klass === 'community' && isFirst) {
        const brandLabel = brand ? brand : 'community';
        return `<div class="kn-section-header rs-section-best">⭐ Best — ${escHtml(brandLabel)} (community)</div>`;
    }
    if (klass === 'community') {
        return `<div class="kn-section-header">── Community ──</div>`;
    }
    if (klass === 'commercial') {
        return `<div class="kn-section-header">── Commercial ──</div>`;
    }
    return `<div class="kn-section-header">── Unknown ──</div>`;
}

function renderRotLocalRow(match, idx, isTop, isBest) {
    const fname = match.filename
        ? match.filename.replace(/\.\w+$/, '')
        : (match.disc_id || '') + ' - ' + (match.artist || '') + ' - ' + (match.title || '');
    const formatClass = match.format ? getFormatBadgeClass(match.format) : 'other';
    const rowClass = 'kn-local-match rs-clickable'
        + (idx === rotSearchSelectedIdx ? ' selected' : '')
        + (isTop ? ' rs-top' : '')
        + (isBest ? ' rs-best' : '');
    let html = `<div class="${rowClass}" data-idx="${idx}" onclick="selectRotSearchResult(rotSearchResults[${idx}])">`;
    html += '<div class="catalog-detail">';
    html += '<span>';
    if (isBest) html += '<span class="rs-best-pill">Best</span> ';
    else if (isTop) html += '<span class="rs-top-star">⭐</span> ';
    html += escHtml(fname) + ' ';
    if (match.format) html += `<span class="format-badge ${formatClass}">${escHtml(match.format)}</span>`;
    if (match.priority_brand) html += `<span class="rs-brand-pill">${escHtml(match.priority_brand)}</span>`;
    html += '</span>';
    if (match.path) {
        const folder = match.path
            .replace(/\/[^/]+$/, '')
            .replace(/^\/media\/nomad\//, '')
            .replace(/^\/opt\/nomad\//, '');
        html += `<div class="catalog-folder" title="${escHtml(match.path)}">${escHtml(folder)}</div>`;
    }
    html += '</div>';
    html += '<span class="kn-play-btn">Link</span>';
    html += '</div>';
    return html;
}

function renderRotKnRow(song, track, idx, isTop, isBest, downloadedIdToPath) {
    const videoId = extractYouTubeId(track.youtube_url || '');
    const downloadedPath = videoId ? downloadedIdToPath.get(videoId) : null;
    const result = { song_artist: song.title + ' - ' + song.artist };
    if (downloadedPath) {
        result.type = 'local';
        result.path = downloadedPath;
    } else if (track.divebar) {
        result.type = 'divebar';
        result.file_id = track.divebar.file_id;
        result.filename = (track.brand_code || 'DB') + ' - ' + song.artist + ' - ' + song.title + '.mp4';
    } else if (track.youtube_url) {
        result.type = 'youtube';
        result.youtube_url = track.youtube_url;
        result.filename = (track.brand_code || 'YT') + ' - ' + song.artist + ' - ' + song.title + '.mp4';
    } else {
        return null;
    }
    const rowClass = 'kn-track rs-clickable'
        + (idx === rotSearchSelectedIdx ? ' selected' : '')
        + (track.is_community ? ' community' : '')
        + (isTop ? ' rs-top' : '')
        + (isBest ? ' rs-best' : '');
    let html = `<div class="${rowClass}" data-idx="${idx}" onclick="selectRotSearchResult(rotSearchResults[${idx}])">`;
    html += '<span class="kn-track-info">';
    if (isBest) html += '<span class="rs-best-pill">Best</span> ';
    else if (isTop) html += '<span class="rs-top-star">⭐</span> ';
    html += `<span class="kn-brand-name">${escHtml(track.brand_name || '')}</span>`;
    html += `<span class="kn-brand-code">${escHtml(track.brand_code || '')}</span>`;
    if (track.is_community) html += '<span class="kn-community-badge">Community</span>';
    html += `<span class="kn-song-title">${escHtml(song.title + ' - ' + song.artist)}</span>`;
    html += '</span>';
    html += '<span class="kn-track-actions">';
    if (downloadedPath) {
        html += '<span class="kn-downloaded-badge">✓ Downloaded</span>';
        html += '<span class="kn-play-btn">Link</span>';
    } else {
        html += '<span class="kn-download-btn">DL & Link</span>';
    }
    html += '</span>';
    html += '</div>';
    return { html, result };
}
```

- [ ] **Step 2: Commit**

```bash
git add kj-controller/static/app.js
git commit -m "feat(ui): rotation search dropdown — global priority sort + section headers + top-3 highlight"
```

---

## Task 12: Frontend — remove now-dead `sortKNTracks` and update `renderKNResults`

**Files:**
- Modify: `kj-controller/static/app.js`

- [ ] **Step 1: Update `renderKNResults` to use backend-supplied priority**

Locate `function renderKNResults(songs)` (line ~2456). The function uses
`sortKNTracks(song.tracks)` and recomputes `isPreferred` per track.

The KN search endpoint (`/karaoke-nerds/search`) is currently not annotated
by the backend — it just returns raw `karaoke_nerds.search(...)` results.
We have two options:
1. Annotate KN search results in `/karaoke-nerds/search` too
2. Re-sort on the frontend using the existing knPriority* globals

Pick (1) for consistency. First, modify `kn_search` in `routes.py`:

Locate `def kn_search()` (around line 1189):

```python
@routes_bp.route('/karaoke-nerds/search', methods=['POST'])
def kn_search():
    """Search karaokenerds.com for web-only karaoke tracks."""
    data = request.get_json(silent=True) or {}
    query = data.get('query', '').strip()
    if not query or len(query) < 2:
        return jsonify({"error": "Query must be at least 2 characters"}), 400

    cfg = current_app.kj_config
    log_message(f"Karaoke Nerds search: {query}", cfg)
    results = karaoke_nerds.search(query, config=cfg)
    return jsonify(results)
```

Modify to annotate each track:

```python
@routes_bp.route('/karaoke-nerds/search', methods=['POST'])
def kn_search():
    """Search karaokenerds.com for web-only karaoke tracks."""
    data = request.get_json(silent=True) or {}
    query = data.get('query', '').strip()
    if not query or len(query) < 2:
        return jsonify({"error": "Query must be at least 2 characters"}), 400

    cfg = current_app.kj_config
    log_message(f"Karaoke Nerds search: {query}", cfg)
    results = karaoke_nerds.search(query, config=cfg)
    # Annotate each track so the frontend can sort by priority_rank without
    # duplicating the registry.
    for song in results:
        version_priority.annotate_versions(
            song.get("tracks") or [], cfg, shape="rotation_search_kn")
        # Sort the song's tracks in-place so frontends that ignore the rank
        # still see best-first ordering.
        (song.get("tracks") or []).sort(
            key=lambda t: t.get("priority_rank", 9999))
    return jsonify(results)
```

- [ ] **Step 2: Update `renderKNResults` in `app.js` to read priority fields**

Find `function renderKNResults(songs)` (~line 2456). Replace the per-track
loop with priority-aware rendering. The full function replacement:

```javascript
function renderKNResults(songs) {
    const container = document.getElementById('kn-results');
    container.innerHTML = '';
    knExpandedSongs = {};
    knSongData = {};

    const downloadedIdToPath = new Map(
        localMediaItems.filter(i => i.youtube_id).map(i => [i.youtube_id, i.file_path])
    );

    songs.forEach((song, idx) => {
        const songId = `kn-song-${idx}`;
        const trackCount = song.tracks.length;
        const isExpanded = false;
        knExpandedSongs[songId] = isExpanded;
        knSongData[songId] = { song, catalogLoaded: false };

        const header = document.createElement('div');
        header.className = 'kn-song-header';
        header.onclick = () => toggleKNSong(songId);

        const chevron = document.createElement('span');
        chevron.className = 'folder-chevron' + (isExpanded ? ' expanded' : '');
        chevron.id = 'kn-chevron-' + idx;
        chevron.textContent = '▶';

        const titleText = document.createElement('span');
        titleText.className = 'kn-song-title';
        titleText.textContent = `${song.title} — ${song.artist}`;

        const count = document.createElement('span');
        count.className = 'kn-track-count';
        count.textContent = `${trackCount} track${trackCount !== 1 ? 's' : ''}`;

        header.appendChild(chevron);
        header.appendChild(titleText);
        header.appendChild(createCopyBtn(`${song.artist} - ${song.title}`));
        header.appendChild(count);
        container.appendChild(header);

        const trackList = document.createElement('div');
        trackList.className = 'kn-track-list' + (isExpanded ? '' : ' collapsed');
        trackList.id = songId;

        // Backend has sorted tracks by priority_rank already. Just render
        // in order. Highlight top-3 recognized-community as preferred.
        song.tracks.forEach((track, tIdx) => {
            const isCommunity = !!track.is_community;
            const isPreferred = track.priority_class === 'community'
                && (track.priority_rank ?? 9999) < 1000;
            const trackEl = document.createElement('div');
            trackEl.className = 'kn-track'
                + (isCommunity ? ' community' : '')
                + (isPreferred ? ' preferred' : '');

            const info = document.createElement('span');
            info.className = 'kn-track-info';

            const brandSpan = document.createElement('span');
            brandSpan.className = 'kn-brand-name';
            brandSpan.textContent = track.brand_name;
            info.appendChild(brandSpan);

            const codeSpan = document.createElement('span');
            codeSpan.className = 'kn-brand-code';
            codeSpan.textContent = track.brand_code;
            info.appendChild(codeSpan);

            if (track.priority_brand && track.priority_brand !== track.brand_code) {
                const canon = document.createElement('span');
                canon.className = 'kn-brand-canonical';
                canon.textContent = '→ ' + track.priority_brand;
                info.appendChild(canon);
            }

            if (isCommunity) {
                const badge = document.createElement('span');
                badge.className = 'kn-community-badge';
                badge.textContent = 'Community';
                info.appendChild(badge);
            } else if (isPreferred) {
                const badge = document.createElement('span');
                badge.className = 'kn-preferred-badge';
                badge.textContent = '★';
                badge.title = 'Preferred brand';
                info.appendChild(badge);
            }

            const videoId = extractYouTubeId(track.youtube_url);
            const downloadedPath = videoId ? downloadedIdToPath.get(videoId) : null;

            const actions = document.createElement('span');
            actions.className = 'kn-track-actions';

            if (downloadedPath) {
                const badge = document.createElement('span');
                badge.className = 'kn-downloaded-badge';
                badge.textContent = '✓ Downloaded';
                actions.appendChild(badge);

                const playBtn = document.createElement('button');
                playBtn.className = 'kn-play-btn';
                playBtn.textContent = 'Play';
                playBtn.onclick = (e) => {
                    e.stopPropagation();
                    playMedia(downloadedPath);
                };
                actions.appendChild(playBtn);
            } else {
                const dlBtn = document.createElement('button');
                dlBtn.className = 'kn-download-btn';
                dlBtn.textContent = 'Download';
                dlBtn.onclick = (e) => {
                    e.stopPropagation();
                    downloadKNTrack(track.youtube_url);
                };
                actions.appendChild(dlBtn);
            }

            trackEl.appendChild(info);
            trackEl.appendChild(actions);
            trackList.appendChild(trackEl);
        });

        container.appendChild(trackList);
    });
}
```

- [ ] **Step 3: Delete `sortKNTracks` and legacy `knPreferredBrands`**

Find and delete the entire function `function sortKNTracks(tracks)` (line
~2442).

Find and delete:
```javascript
let knPreferredBrands = window.KJ_CONFIG.knPreferredBrands || [];
```

Search the file for remaining references:
```bash
grep -n "knPreferredBrands\|sortKNTracks" kj-controller/static/app.js
```

If any references remain in code that's still live, remove them or refactor
to use `knPriorityCommunity` / `knPriorityCommercial` / per-row
`priority_*` fields.

- [ ] **Step 4: Remove legacy template binding**

In `kj-controller/templates/index.html` find line ~763:
```html
knPreferredBrands: {{ kn_preferred_brands | tojson }}
```

Delete the line (and trim a trailing comma if needed). Also find the
`index()` route in `routes.py` (line ~187) and remove the
`kn_preferred_brands=cfg.get('kn_preferred_brands', [])` kwarg:

```python
@routes_bp.route('/')
def index():
    """Serves the main remote control page."""
    cfg = current_app.kj_config
    return render_template('index.html', latin_special_map=LATIN_SPECIAL_MAP,
                           config=cfg,
                           kn_preferred_brands=cfg.get('kn_preferred_brands', []))
```

becomes:

```python
@routes_bp.route('/')
def index():
    """Serves the main remote control page."""
    cfg = current_app.kj_config
    return render_template('index.html', latin_special_map=LATIN_SPECIAL_MAP,
                           config=cfg)
```

- [ ] **Step 5: Commit**

```bash
git add kj-controller/static/app.js kj-controller/templates/index.html kj-controller/routes.py
git commit -m "refactor(ui): remove sortKNTracks + legacy knPreferredBrands; use backend priority fields"
```

---

## Task 13: Add CSS for hero card, section headers, top-3 highlight

**Files:**
- Modify: `kj-controller/static/style.css`

- [ ] **Step 1: Append the new styles**

Add to the end of `kj-controller/static/style.css`:

```css
/* === Choose-best-version: kj_pick hero card === */
.pr-picker .pr-version-hero {
    background: linear-gradient(135deg, #1a3a1a 0%, #2a4a2a 100%);
    border: 2px solid #4caf50;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 10px;
    box-shadow: 0 2px 8px rgba(76, 175, 80, 0.2);
}
.pr-version-hero.pr-class-commercial {
    background: linear-gradient(135deg, #2a2a3a 0%, #3a3a4a 100%);
    border-color: #5c8df0;
    box-shadow: 0 2px 8px rgba(92, 141, 240, 0.2);
}
.pr-version-hero.pr-class-unknown {
    background: linear-gradient(135deg, #3a2a2a 0%, #4a3a3a 100%);
    border-color: #c79a4a;
    box-shadow: 0 2px 8px rgba(199, 154, 74, 0.2);
}
.pr-hero-header {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 1.1em;
    font-weight: 600;
    margin-bottom: 4px;
}
.pr-hero-star { font-size: 1.3em; }
.pr-hero-class {
    font-size: 0.75em;
    text-transform: uppercase;
    background: rgba(255, 255, 255, 0.1);
    padding: 2px 6px;
    border-radius: 3px;
    margin-left: auto;
}
.pr-hero-secondary {
    color: #ccc;
    font-size: 0.95em;
    margin-bottom: 4px;
}
.pr-hero-source {
    color: #888;
    font-size: 0.85em;
    font-style: italic;
    margin-bottom: 12px;
}
.pr-hero-cta-wrap {
    text-align: center;
}
.pr-hero-cta {
    background: #4caf50;
    color: white;
    border: none;
    padding: 10px 24px;
    border-radius: 4px;
    font-size: 1em;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.15s;
}
.pr-hero-cta:hover { background: #5cbf60; }

.pr-picker-alternates {
    margin-top: 8px;
}
.pr-picker-alternates summary {
    cursor: pointer;
    padding: 6px 10px;
    color: #aaa;
    font-size: 0.9em;
    user-select: none;
}
.pr-picker-alternates summary:hover { color: #ddd; }
.pr-picker-alternates[open] summary { color: #ddd; }
.pr-picker-alternates .pr-version {
    margin-top: 4px;
}

/* === Choose-best-version: rotation search section headers === */
.rotation-search-dropdown .kn-section-header {
    padding: 4px 10px;
    color: #888;
    font-size: 0.8em;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    background: rgba(255, 255, 255, 0.03);
}
.rotation-search-dropdown .rs-section-best {
    color: #4caf50;
    background: rgba(76, 175, 80, 0.08);
    font-weight: 600;
    text-transform: none;
}
.rotation-search-dropdown .rs-top {
    border-left: 3px solid rgba(255, 215, 0, 0.4);
}
.rotation-search-dropdown .rs-best {
    border-left: 3px solid #ffd700;
    background: rgba(255, 215, 0, 0.05);
}
.rs-best-pill {
    background: #ffd700;
    color: #000;
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 0.7em;
    font-weight: 700;
    margin-right: 4px;
}
.rs-top-star {
    color: #ffd700;
    margin-right: 4px;
}
.rs-brand-pill {
    background: rgba(255, 255, 255, 0.08);
    color: #ccc;
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 0.7em;
    margin-left: 4px;
}

/* === Choose-best-version: two-list prefs panel === */
.kn-prefs-section {
    margin-bottom: 10px;
}
.kn-prefs-textarea {
    width: 100%;
    box-sizing: border-box;
    background: #1a1a1a;
    color: #ddd;
    border: 1px solid #444;
    border-radius: 3px;
    padding: 6px;
    font-family: inherit;
    font-size: 0.9em;
    resize: vertical;
}
.kn-prefs-aliases {
    color: #888;
    font-size: 0.78em;
    margin-top: 3px;
    font-style: italic;
}
.kn-prefs-actions {
    display: flex;
    gap: 8px;
    margin-top: 8px;
}
.kn-prefs-reset {
    background: #555;
    color: white;
    border: none;
    padding: 4px 10px;
    border-radius: 3px;
    cursor: pointer;
}
.kn-prefs-reset:hover { background: #666; }

.kn-brand-canonical {
    color: #888;
    font-size: 0.8em;
    margin-left: 4px;
}
```

- [ ] **Step 2: Commit**

```bash
git add kj-controller/static/style.css
git commit -m "feat(ui): styles for hero card, section headers, top-3 highlight, two-list prefs"
```

---

## Task 14: Full backend test pass + manual smoke test

**Files:** none (verification)

- [ ] **Step 1: Run full pytest with coverage**

```bash
cd /Users/andrew/Projects/nomadkaraoke/kjbox-choose-best-version/kj-controller
pytest --cov=. --cov-report=term-missing 2>&1 | tail -40
```
Expected: All tests PASS. `version_priority.py` should show ≥90% coverage.

- [ ] **Step 2: Verify no JS syntax errors**

If the pre-commit hook is enabled (`git config core.hooksPath .githooks`)
this is automatic. Otherwise:
```bash
cd kj-controller/static
node --check app.js
```
Expected: no output (success).

- [ ] **Step 3: Manual smoke (optional, if dev environment available)**

Start dev server:
```bash
cd kj-controller
python3 dev_server.py
```

Open `http://localhost:5000`. Verify:
- KN Prefs panel: two textareas with defaults populated
- Rotation Add: typing a song name shows results with section headers (best
  community at top with ⭐ Best pill)

If any visual issue, fix CSS in a follow-up commit and retest.

---

## Task 15: Update CHANGELOG and bump version

**Files:**
- Modify: `kj-controller/pyproject.toml` (bump patch version)
- Modify: `docs/CHANGELOG.md` (if exists) or `CHANGELOG.md`

- [ ] **Step 1: Check current version**

```bash
grep -E '^version' kj-controller/pyproject.toml
```

- [ ] **Step 2: Bump patch version**

If e.g. current is `version = "0.5.2"`, change to `version = "0.5.3"`:

```bash
# Edit kj-controller/pyproject.toml manually
```

- [ ] **Step 3: Add changelog entry**

Append to `docs/CHANGELOG.md` (the appropriate doc per CLAUDE.md — check
which is the canonical one). Use existing entry format. New entry should
describe:
- Two-list brand-priority config (`kn_priority_community`/`commercial`)
- kj_pick approval hero card with collapsed alternates
- Rotation search dropdown global sort + section headers + top-3 highlight
- Migration note: old `kn_preferred_brands` field ignored, left untouched

- [ ] **Step 4: Commit**

```bash
git add kj-controller/pyproject.toml docs/CHANGELOG.md
git commit -m "chore: bump version + changelog for choose-best-version feature"
```

---

## Spec Coverage Self-Review

| Spec section | Task(s) | Notes |
|---|---|---|
| § 1 Brand registry with aliases | Task 1 | Constants + tests |
| § 1 `resolve_brand` | Task 2 | All paths: brand_code, brand_name, disc_id, YT filename |
| § 1 `rank_version` with tier widths 0/1000/2000/3000/4000 | Task 3 | Includes source tiebreaker |
| § 1 `annotate_versions` (3 shapes) | Task 3 | kj_pick, rotation_search_local, rotation_search_kn |
| § 1 Backend wiring: `_group_search_results` | Task 4 | |
| § 1 Backend wiring: `unified_search` | Task 5 | Flat path (grouped path already covered by Task 4) |
| § 1 Backend wiring: `/karaoke-nerds/search` annotation | Task 12 Step 1 | |
| § 2 kj_pick hero card + collapsed alternates | Task 10 | |
| § 2 Back-compat fallback to legacy bucket sort | Task 10 (`versionBucketLegacy`) | |
| § 2 1 version → no `<details>`; all-unknown → open by default | Task 10 | |
| § 3a Global priority sort | Task 11 | |
| § 3a Section headers | Task 11 | |
| § 3a Top-3 highlight | Task 11 + Task 13 (CSS) | |
| § 3a Default-select row 0 | Task 11 | |
| § 3a Remove `sortKNTracks` | Task 12 | |
| § 3b Two-list settings UI | Task 8 (HTML) + Task 9 (JS) + Task 13 (CSS) | |
| § 3b GET returns `{priority_community, priority_commercial, aliases}` | Task 6 | |
| § 3b POST validates canonicals, rejects unknown | Task 6 | |
| § 3b Save to `kn_priority_community`/`commercial`, leave legacy alone | Task 6 | |
| § Testing (`test_version_priority.py`) | Tasks 1, 2, 3 | |
| § Testing (rotation_search annotation) | Task 5 | |
| § Testing (search_grouping annotation) | Task 4 | |
| § Testing (karaoke_nerds_config) | Task 6 | |
| § Deployment notes | Honored at /shipit time | |
