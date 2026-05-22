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
        return (None, "unknown")

    canonical, cls = hit
    if is_community is True:
        return (canonical, "community")
    if is_community is False and cls == "community":
        # An explicit non-community flag on a community-classified brand
        # would be self-contradictory; respect it and re-tag as commercial.
        return (canonical, "commercial")
    return (canonical, cls)
