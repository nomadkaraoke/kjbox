"""Reusable test fixture data from real karaoke catalog entries.

All paths and filenames are real examples from the Nomad Karaoke catalog.
Characters are stored in NFD form (decomposed) matching the actual file listing.

Usage:
    from tests.fixtures import KARAOKE_CATALOG_ENTRIES, SEARCH_NORMALIZATION_CASES
"""

# ---------------------------------------------------------------------------
# Real karaoke file paths organized by character category.
# Every entry is a real file from the 415K-entry Nomad 4TB catalog.
# Paths use the original /Volumes/Nomad4TBOne mount point.
# ---------------------------------------------------------------------------

# Standard ASCII filenames (control group)
ASCII_ENTRIES = [
    "/Volumes/Nomad4TBOne/HyperMule/Karaoke/SC2411-08 - Rascal Flatts - Life Is A Highway.zip",
    "/Volumes/Nomad4TBOne/HyperMule/Karaoke/PHK004 - Bon Jovi - Livin On A Prayer.zip",
    "/Volumes/Nomad4TBOne/HyperMule/Videos/Michael Jackson - Billie Jean.mp4",
    "/Volumes/Nomad4TBOne/HyperMule/Karaoke/Journey - Don't Stop Believin.zip",
    "/Volumes/Nomad4TBOne/HyperMule/Videos/Queen - Bohemian Rhapsody.mp4",
]

# --- Combining diacritical marks (NFD-decomposable) ---
# These are base letter + combining mark; NFD normalization strips the mark.
COMBINING_DIACRITICS_ENTRIES = [
    # tilde: ñ (n + U+0303)
    "/Volumes/Nomad4TBOne/HyperMule/BREZ (by Breezy Karaoke)/BREZ-0074 - Chance Pen\u0303a - In My Room.mp4",
    # acute: í (i + U+0301)
    "/Volumes/Nomad4TBOne/HyperMule/BREZ (by Breezy Karaoke)/BREZ-0201 - Mari\u0301as - Run Your Mouth.mp4",
    # diaeresis: ü (u + U+0308)
    "/Volumes/Nomad4TBOne/HyperMule/Master Karaoke Folder/Karaoke - Digital/Active/Karaoke Cloud Digitrax (KCD)/KVD-22524/KVD-22524 - Udo Ju\u0308rgens - Der Erste Sahne Mix.zip",
    # circumflex: û (u + U+0302)
    "/Volumes/Nomad4TBOne/HyperMule/Master Karaoke Folder/Karaoke - Digital/Active/Karaoke Cloud Digitrax (KCD)/KVD-23370/KVD-23370 - Charles Aznavour & Laura Pausini (Duet) - Paris Au Mois D'aou\u0302t (French).zip",
    # grave: è (e + U+0300)
    "/Volumes/Nomad4TBOne/HyperMule/Master Karaoke Folder/Karaoke - Digital/Active/Karaoke Cloud Digitrax (KCD)/KVD-38887/KVD-38887 - Charles Aznavour & Edith Piaf (Duet) - Plus Bleu Que Tes Yeux (Live Palais des congre\u0300s 97-98).zip",
    # ring above: å (a + U+030A)
    "/Volumes/Nomad4TBOne/HyperMule/Alernus/ALERNUS-300 - Ma\u030Aneskin - Torna A Casa.mp4",
    # cedilla: ç (c + U+0327)
    "/Volumes/Nomad4TBOne/HyperMule/Master Karaoke Folder/Karaoke - Digital/Inactive/Sunfly (SF)/SF-1100368/SF-1100368 - Andre\u0327 Hazes - De Vlieger.zip",
    # macron: ū (u + U+0304)
    "/Volumes/Nomad4TBOne/HyperMule/Kamikaze Karaoke/KAMIKAZE-006 - Baru\u0304n - Sharuru.mp4",
    # caron: š (s + U+030C)
    "/Volumes/Nomad4TBOne/HyperMule/Master Karaoke Folder/Karaoke - Digital/Active/Nox Karaoke (NOX)/NOX-0268/NOX-0268 - Elina Nets\u030Cajeva - Volcano Man.zip",
    # ogonek: ę (e + U+0328)  — also has ł (non-decomposable)
    "/Volumes/Nomad4TBOne/HyperMule/SARUX Karaoke/SARUX-406 - Cypis - Gdzie jest biały we\u0328gorz.mp4",
]

# --- Non-decomposable Latin characters ---
# NFD does NOT decompose these; they need explicit character mapping.
NON_DECOMPOSABLE_LATIN_ENTRIES = [
    # ø (O with stroke) — Scandinavian
    "/Volumes/Nomad4TBOne/HyperMule/Master Karaoke Folder/Karaoke - Digital/Active/Karaoke Cloud Digitrax (KCD)/CKD-083963/CKD-083963 - Major Lazer & Justin Bieber & MØ (Duet) - Cold Water.zip",
    "/Volumes/Nomad4TBOne/HyperMule/BALKA Karaoke/BALKA-0058 - Eivør - Slør.mp4",
    "/Volumes/Nomad4TBOne/HyperMule/Master Karaoke Folder/Karaoke - Digital/Active/Chartbuster Karaoke (CKO)/CKO053-20 - Sissel Kyrkjebø (MPX) - For The Good Times.zip",
    # æ (AE ligature) — Scandinavian/Old English
    "/Volumes/Nomad4TBOne/HyperMule/Caritasd/CARITASD-0376 - Tool - Ænima.mp4",
    "/Volumes/Nomad4TBOne/HyperMule/Master Karaoke Folder/Karaoke - Digital/Active/All Entertainment Karaoke (AEK)/AEK15-03 - Nik & Jay - Lækker.zip",
    # ß (sharp S) — German
    "/Volumes/Nomad4TBOne/HyperMule/Alernus/ALERNUS-001 - 187 Straßenbande - Milliona\u0308r.mp4",
    # ð (eth) — Icelandic
    "/Volumes/Nomad4TBOne/HyperMule/Master Karaoke Folder/Karaoke - Digital/Active/Karaoke Cloud Digitrax (KCD)/KVD-60923/KVD-60923 - Daði Freyr - Think About Things.zip",
    # ł (L with stroke) — Polish
    "/Volumes/Nomad4TBOne/HyperMule/Master Karaoke Folder/Karaoke - Homemade/diveBar and Dropped Mic Ministry/Eduardo's ObsKure Karaoke MP4/1501-2000/OBSK-1714 - Faun Fables - Ta Nasza Młodość.mp4",
    # ı (dotless I) — Turkish
    "/Volumes/Nomad4TBOne/HyperMule/Alernus/ALERNUS-019 - Aleyna Tilki - Yalnız Çiçek.mp4",
]

# --- Cyrillic script ---
CYRILLIC_ENTRIES = [
    "/Volumes/Nomad4TBOne/HyperMule/SARUX Karaoke/SARUX-337 - Молчат Дома (Molchat Doma) - Судно (Sudno) karaoke.mp4",
    "/Volumes/Nomad4TBOne/HyperMule/SARUX Karaoke/SARUX-479 - Сплин - Алиса.mp4",
    "/Volumes/Nomad4TBOne/HyperMule/SARUX Karaoke/SARUX-086 - Альянс - На заре.mp4",
    "/Volumes/Nomad4TBOne/HyperMule/SARUX Karaoke/SARUX-516 - ПОЛЮСА - Поэзия.mp4",
]

# --- Greek script ---
GREEK_ENTRIES = [
    "/Volumes/Nomad4TBOne/HyperMule/Alernus/ALERNUS-301 - Manos - Μπλα Μπλα.mp4",
    "/Volumes/Nomad4TBOne/HyperMule/Alernus/ALERNUS-302 - Manos - Παγωτό Καραμέλα.mp4",
    "/Volumes/Nomad4TBOne/HyperMule/WIZ Karaoke/WIZ-024 - Axwell Λ Ingrosso - Thinking About You.mp4",
]

# --- CJK / Katakana / Hiragana ---
CJK_KANA_ENTRIES = [
    "/Volumes/Nomad4TBOne/HyperMule/FatBirdP4/FATBIRDP4-011 - Miracle Musical ミラクルミュージカル - Dream Sweet in Sea Major.mp4",
    "/Volumes/Nomad4TBOne/HyperMule/Friends of Dave/FOD-0054 - Kyary Pamyu Pamyu - Isshin Doutai (きゃりーぱみゅぱみゅ - 一心同体).mp4",
    "/Volumes/Nomad4TBOne/HyperMule/Friends of Dave/FOD-0085 - ATARASHII GAKKO! 新しい学校のリーダーズ - HANAKO.mp4",
    "/Volumes/Nomad4TBOne/HyperMule/Friends of Dave/FOD-0086 - Hikaru Utada (宇多田ヒカル) - One Last Kiss シン・エヴァンゲリオン劇場版.mp4",
]

# --- Fullwidth characters ---
FULLWIDTH_ENTRIES = [
    "/Volumes/Nomad4TBOne/HyperMule/ACLG/ACLG-0271 - Siakol - Bakit ba？.mp4",
    "/Volumes/Nomad4TBOne/HyperMule/Gerry/GERRY-009 - Sugar Ray - Rona Envy (Parody of ＂Fly＂).mp4",
]

# --- Smart punctuation (curly quotes, dashes, ellipsis) ---
SMART_PUNCTUATION_ENTRIES = [
    # right single quote (apostrophe)
    "/Volumes/Nomad4TBOne/HyperMule/Master Karaoke Folder/Karaoke - Digital/Active/Karaoke Cloud Digitrax (KCD)/DCK419-03/DCK419-03 - Drake - What\u2019s Next.zip",
    # ellipsis
    "/Volumes/Nomad4TBOne/HyperMule/VEVO/VEVO-2343 - Maroon 5 - This Summer\u2019s Gonna Hurt Like A\u2026.mp4",
    # en dash
    "/Volumes/Nomad4TBOne/HyperMule/Master Karaoke Folder/Karaoke - Digital/Active/Karaoke Cloud Digitrax (KCD)/DCK424-10/DCK424-10 - Bree Runway \u2013 Hot Hot.zip",
]

# --- Symbols in artist/song names ---
SYMBOL_ENTRIES = [
    # dagger (†)
    "/Volumes/Nomad4TBOne/HyperMule/Allen/ALLEN-0091 - ††† (Crosses) - Vivien.mp4",
    # big solidus (⧸) — used as slash substitute in filenames
    "/Volumes/Nomad4TBOne/HyperMule/DMICS/DMICS-136 - Brand New - Same Logic⧸Teeth.mp4",
    # pound sign
    "/Volumes/Nomad4TBOne/HyperMule/Master Karaoke Folder/Karaoke - Digital/Active/Karaoke Cloud Digitrax (KCD)/KVD-42513/KVD-42513 - £1 Fish Man - One Pound Fish.zip",
    # registered trademark
    "/Volumes/Nomad4TBOne/HyperMule/All Keys Karaoke/ALLKEYS-570 - John Legend - A Good Night ft BloodPop®.mp4",
    # heart
    "/Volumes/Nomad4TBOne/HyperMule/LOPEP4/LOPEP4-023 - We ♥ Katamari - Katamari on the Swing.mp4",
]


# ---------------------------------------------------------------------------
# Combined list: every entry above in a single list for building a
# comprehensive test catalog. Use with catalog.build_from_file_list().
# ---------------------------------------------------------------------------
KARAOKE_CATALOG_ENTRIES = (
    ASCII_ENTRIES
    + COMBINING_DIACRITICS_ENTRIES
    + NON_DECOMPOSABLE_LATIN_ENTRIES
    + CYRILLIC_ENTRIES
    + GREEK_ENTRIES
    + CJK_KANA_ENTRIES
    + FULLWIDTH_ENTRIES
    + SMART_PUNCTUATION_ENTRIES
    + SYMBOL_ENTRIES
)


# ---------------------------------------------------------------------------
# Search normalization test cases: (query, expected_match_artist_or_title).
# Each pair tests that an ASCII query finds the special-char original.
# Use for both client-side filterLocalMedia and server-side FTS search.
# ---------------------------------------------------------------------------
SEARCH_NORMALIZATION_CASES = [
    # Combining diacritics (NFD-decomposable)
    ("Pena", "Peña"),               # tilde
    ("Marias", "Marías"),           # acute
    ("Jurgens", "Jürgens"),         # diaeresis
    ("daout", "août"),              # circumflex; apostrophe in "D'août" drops -> "daout"
    ("congres", "congrès"),         # grave
    ("Maneskin", "Måneskin"),       # ring above
    ("Hazes", "Hazes"),             # cedilla (on preceding ç, artist is Andrç)
    ("Barun", "Barūn"),             # macron
    ("Netsajeva", "Netšajeva"),     # caron
    ("wegorz", "węgorz"),           # ogonek

    # Non-decomposable Latin characters
    ("MO Cold Water", "MØ"),        # ø → o (uppercase)
    ("Eivor", "Eivør"),             # ø → o (lowercase)
    ("Kyrkjebo", "Kyrkjebø"),       # ø → o (in longer word)
    ("Aenima", "Ænima"),            # æ → ae (uppercase)
    ("Laekker", "Lækker"),          # æ → ae (lowercase)
    ("Strassenbande", "Straßenbande"),  # ß → ss
    ("Dadi Freyr", "Daði"),         # ð → d
    ("Mlodosc", "Młodość"),          # ł → l
    ("Yalniz", "Yalnız"),           # ı → i
]


# ---------------------------------------------------------------------------
# Expected parse results for filenames with special characters.
# Format: (filename, (disc_id, artist, title))
# Use for testing parse_karaoke_filename with non-ASCII inputs.
# ---------------------------------------------------------------------------
FILENAME_PARSE_CASES = [
    # Standard three-part with diacritics
    ("BREZ-0074 - Chance Pen\u0303a - In My Room.mp4",
     ("BREZ-0074", "Chance Peña", "In My Room")),
    ("KVD-22524 - Udo Ju\u0308rgens - Der Erste Sahne Mix.zip",
     ("KVD-22524", "Udo Jürgens", "Der Erste Sahne Mix")),
    ("ALERNUS-300 - Ma\u030Aneskin - Torna A Casa.mp4",
     ("ALERNUS-300", "Måneskin", "Torna A Casa")),

    # Non-decomposable chars
    ("CKD-083963 - Major Lazer & Justin Bieber & MØ (Duet) - Cold Water.zip",
     ("CKD-083963", "Major Lazer & Justin Bieber & MØ (Duet)", "Cold Water")),
    ("BALKA-0058 - Eivør - Slør.mp4",
     ("BALKA-0058", "Eivør", "Slør")),
    ("CARITASD-0376 - Tool - Ænima.mp4",
     ("CARITASD-0376", "Tool", "Ænima")),
    ("KVD-60923 - Daði Freyr - Think About Things.zip",
     ("KVD-60923", "Daði Freyr", "Think About Things")),

    # Cyrillic artist
    ("SARUX-337 - Молчат Дома (Molchat Doma) - Судно (Sudno) karaoke.mp4",
     ("SARUX-337", "Молчат Дома (Molchat Doma)", "Судно (Sudno) karaoke")),

    # Symbols in names
    ("ALLEN-0091 - ††† (Crosses) - Vivien.mp4",
     ("ALLEN-0091", "††† (Crosses)", "Vivien")),
    ("DMICS-136 - Brand New - Same Logic⧸Teeth.mp4",
     ("DMICS-136", "Brand New", "Same Logic⧸Teeth")),

    # Fullwidth question mark
    ("ACLG-0271 - Siakol - Bakit ba？.mp4",
     ("ACLG-0271", "Siakol", "Bakit ba？")),

    # CJK/Kana in filename
    ("FOD-0085 - ATARASHII GAKKO! 新しい学校のリーダーズ - HANAKO.mp4",
     ("FOD-0085", "ATARASHII GAKKO! 新しい学校のリーダーズ", "HANAKO")),
]
