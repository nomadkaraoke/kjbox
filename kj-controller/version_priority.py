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
