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


# Module-level alias lookup table, built once on import.
# Maps alias.upper() -> (canonical, classification).
_ALIAS_TO_CANONICAL: dict = {}

for _canonical, _aliases, _display in COMMUNITY_BRANDS:
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL[_alias.upper().strip()] = (_canonical, "community")

for _canonical, _aliases, _display in COMMERCIAL_BRANDS:
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL[_alias.upper().strip()] = (_canonical, "commercial")


# Regex for extracting alpha-prefix brand code from a local disc_id.
# Stops at the first non-letter character so "KVD-22524" -> "KVD",
# "SC2411-08" -> "SC", "LEMMY-001" -> "LEMMY".
_DISC_ID_PREFIX_RE = re.compile(r'^([A-Z]+)')

# Regex for the YouTube-download filename pattern:
# VIDEOID__BrandName__Artist - Title.ext
# (Allows brand_name to contain spaces, punctuation, anything except __.)
_YT_FILENAME_RE = re.compile(r'^[^_]+__([^_]+?)__')


def _lookup_alias(raw):
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
    # Precedence: brand_code wins outright if given (even unrecognized — KN
    # giving us a specific code we don't know is more reliable than guessing
    # via brand_name, which can collide on partial matches). Then brand_name.
    # Local-file paths (disc_id / filename) only consulted when neither
    # KN field is present, but DO fall through between themselves.
    hit = None
    if brand_code:
        hit = _lookup_alias(brand_code)
    elif brand_name:
        hit = _lookup_alias(brand_name)
    else:
        if disc_id:
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
        if is_community is False:
            # KN said "not community" but we don't know the brand — treat
            # as unrecognized commercial so it still ranks in the commercial
            # tier (above pure-unknown).
            return (None, "commercial")
        return (None, "unknown")

    canonical, cls = hit
    if is_community is True:
        return (canonical, "community")
    if is_community is False and cls == "community":
        # An explicit non-community flag on a community-classified brand
        # would be self-contradictory; respect it and re-tag as commercial.
        return (canonical, "commercial")
    return (canonical, cls)


# --- Ranking ---
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
    """Pull brand-resolution inputs from any of the supported version shapes."""
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
        priority_rank: int          (lower = better)
        priority_brand: str | None  (canonical code)
        priority_class: str         ("community" | "commercial" | "unknown")

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
