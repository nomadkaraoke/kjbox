"""Single source of truth for song-text normalization.

Pure functions (no Flask / SQLite). The same pipeline is applied to indexed
catalog text AND to user queries so both meet in one canonical space. A JS twin
(static/text_normalize.js) mirrors this exactly and is verified by a parity test.

Two flavours, differing only in step 3:
  * ``normalize`` — SEARCH text (indexes + queries). Drops just the word
    feat./ft./featuring and KEEPS the featured artist, so "hayley stay the
    night" finds "Zedd feat. Hayley Williams - Stay the Night" (the remote
    Divebar/KN Cloud Function matches the same way).
  * ``identity_normalize`` — SONG IDENTITY (grouping keys, persisted play-stats
    ``song_key``, same-song joins). Strips the whole feat clause so
    "Zedd feat. X" and "Zedd" are one song.

Pipeline order (order matters):
  1. Unicode NFD + strip combining marks + Latin special-char fold
  2. lowercase
  3. feat./ft./featuring: drop the word (search) / the whole clause (identity)
  4. expand symbol conjunctions  &  +  -> " and "
  5. drop apostrophes; replace remaining punctuation with space
  6. token-level word abbreviations (pt->part, vs->versus, ...)
  7. number canonicalization: words/roman -> digits, with tens+ones adjacency
"""

import re
import unicodedata

# Bump whenever the pipeline or any map changes; gates catalog reindex.
NORMALIZER_VERSION = 3

# Version of the INDEXED search text (``index_text``): NORMALIZER_VERSION plus
# the index-only augmentation revision. Stamped by the FTS indexes (external
# catalog, catalog mirror) instead of NORMALIZER_VERSION so an index-side change
# reindexes them without invalidating song_id.db (which keys off
# NORMALIZER_VERSION alone). Bump the suffix when ``index_text`` changes.
SEARCH_INDEX_VERSION = f"{NORMALIZER_VERSION}.1"

# Non-decomposable Latin chars that NFD cannot handle.
LATIN_SPECIAL_MAP = {
    'ø': 'o', 'Ø': 'o', 'æ': 'ae', 'Æ': 'ae', 'ß': 'ss',
    'ð': 'd', 'Ð': 'd', 'ł': 'l', 'Ł': 'l', 'ı': 'i',
    'đ': 'd', 'Đ': 'd', 'þ': 'th', 'Þ': 'th',
}
_LATIN_SPECIAL_RE = re.compile(
    '[' + re.escape(''.join(LATIN_SPECIAL_MAP)) + ']'
)


# feat./ft./featuring qualifier; stops at a closing bracket or end of string.
# \b: only a whole word "ft" — without it "Soft Cell" normalized to "so", "Hayloft II"
# to "haylo", "Taylor Swift" to "taylor swi" (fixed in NORMALIZER_VERSION 2).
# Identity only: the clause runs to a closing bracket or END OF STRING, so it must
# never be applied to search text (it ate "... Hayley Williams Stay the Night" from
# concatenated "artist title" index text — fixed in NORMALIZER_VERSION 3).
_FEAT_RE = re.compile(
    r'\s*[\[(]?\s*\b(?:feat\.?|ft\.?|featuring)\s+[^\])]+[\])]?',
    re.IGNORECASE,
)
# Search: just the feat word (followed by whitespace), names kept.
_FEAT_WORD_RE = re.compile(r'\b(?:feat\.?|ft\.?|featuring)(?=\s)', re.IGNORECASE)
_SYMBOL_AND_RE = re.compile(r'\s*[&+]\s*')
_APOS_RE = re.compile("[‘’ʼ`']+")
_PUNCT_RE = re.compile(r'[^\w\s]', re.UNICODE)


def _strip_accents(text):
    s = unicodedata.normalize('NFD', text)
    # Strip U+0300–U+036F combining marks. Kept as an explicit range (not a
    # category check) so static/text_normalize.js can mirror it exactly.
    s = re.sub(r'[̀-ͯ]', '', s)
    s = _LATIN_SPECIAL_RE.sub(lambda m: LATIN_SPECIAL_MAP[m.group()], s)
    return s


def normalize(text):
    """Canonical SEARCH text for `text` (featured artists kept)."""
    return _normalize(text, _FEAT_WORD_RE)


def identity_normalize(text):
    """Canonical SONG-IDENTITY text for `text` (feat clause stripped)."""
    return _normalize(text, _FEAT_RE)


def _normalize(text, feat_re):
    if not text:
        return ""
    s = _strip_accents(text)
    s = s.lower()
    s = feat_re.sub(' ', s)
    s = _SYMBOL_AND_RE.sub(' and ', s)
    s = _APOS_RE.sub('', s)
    s = _PUNCT_RE.sub(' ', s)
    toks = s.split()
    toks = [ABBREV_MAP.get(t, t) for t in toks]
    toks = _canonicalize_numbers(toks)
    return ' '.join(toks)


def tokens(text):
    """Return the canonical token list for `text`."""
    n = normalize(text)
    return n.split() if n else []


# Conservative whole-token abbreviations (avoid ambiguous single letters).
ABBREV_MAP = {
    "pt": "part", "pts": "parts",
    "vs": "versus",
}

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

# Exclude ambiguous standalone i/v/x/l/c/d/m.
ROMAN_NUMERALS = {
    "ii": 2, "iii": 3, "iv": 4, "vi": 6, "vii": 7, "viii": 8, "ix": 9,
}

_TENS = {20, 30, 40, 50, 60, 70, 80, 90}


def _canonicalize_numbers(toks):
    out = []
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        val = NUMBER_WORDS.get(t)
        if val is None:
            val = ROMAN_NUMERALS.get(t)
        if val is None:
            out.append(t)
            i += 1
            continue
        # tens + ones adjacency: "twenty one" -> 21
        if val in _TENS and i + 1 < n:
            ones = NUMBER_WORDS.get(toks[i + 1])
            if ones is not None and 1 <= ones <= 9:
                val += ones
                i += 1
        out.append(str(val))
        i += 1
    return out


def joined_forms(norm_text):
    """Adjacent token pairs of already-normalized text, joined into one word.

    Titles like "(Over)Thinking", "Over, Thinking" or "AC/DC" normalize to two
    tokens, so a singer typing the run-together form ("overthinking", "acdc",
    "blink182") would miss them. Indexing every adjacent pair as one extra
    token makes those queries hit. Pairs that already equal a token are skipped.
    """
    toks = norm_text.split() if norm_text else []
    seen = set(toks)
    out = []
    for a, b in zip(toks, toks[1:]):
        j = a + b
        if j not in seen:
            seen.add(j)
            out.append(j)
    return out


def index_text(text):
    """Canonical text for the INDEXED side of a search (never the query).

    ``normalize(text)`` followed by its ``joined_forms``, so both "over
    thinking" and "overthinking" find "(Over)Thinking". Queries stay plain
    ``normalize`` — feeding them through here would AND in the joined tokens.
    """
    n = normalize(text)
    extra = joined_forms(n)
    return n + ' ' + ' '.join(extra) if extra else n


_VOWEL_RE = re.compile('[aeiou]')
_VOWEL_Y_RE = re.compile('[aeiouy]')


def consonant_skeleton(token):
    """``token`` with its vowels (a/e/i/o/u) dropped: "memories" -> "mmrs"."""
    return _VOWEL_RE.sub('', token)


def skeleton_alternative(token):
    """The vowel-dropped spelling a title might use for ``token``, or None.

    Some real titles are stylised without vowels — Fall Out Boy's "Thnks fr
    th Mmrs" — so a singer typing "thanks for the memories" must also match
    "thnks fr th mmrs". None when dropping vowels changes nothing or leaves
    fewer than 2 letters (too little signal to search on).
    """
    s = consonant_skeleton(token)
    if len(s) < 2 or s == token:
        return None
    return s


# A vowel-dropped query needs this many words that drop to 3+ letters: fewer
# turns ordinary queries into noise ("the marias" -> "th mrs" ~ "Mrs. O").
_SKELETON_MIN_WORDS = 2


def skeleton_query_tokens(toks):
    """Each query word's vowel-dropped form, or None when the query is not
    worth a vowel-dropped search.

    "thanks for the memories" -> ["thnks", "fr", "th", "mmrs"]. At least
    ``_SKELETON_MIN_WORDS`` words must drop to 3+ letters, so short or
    ordinary queries never expand. Matching accepts each word as typed OR
    dropped (artists aren't stylised: "fall out boy thnks fr th mmrs").
    """
    sk = [skeleton_alternative(t) or t for t in toks]
    strong = sum(1 for t, s in zip(toks, sk) if s != t and len(s) >= 3)
    if strong < _SKELETON_MIN_WORDS:
        return None
    return sk


def tokens_match_skeleton(toks, skel_toks, hay_tokens):
    """True when every query word is in ``hay_tokens`` as typed or
    vowel-dropped (the last word may be a typed-so-far prefix; a dropped form
    only when it has 3+ letters)."""
    last = len(toks) - 1
    for i, (t, s) in enumerate(zip(toks, skel_toks)):
        if i == last:
            forms = [t] + ([s] if s != t and len(s) >= 3 else [])
            ok = any(w.startswith(f) for f in forms for w in hay_tokens) \
                or s in hay_tokens
        else:
            ok = t in hay_tokens or s in hay_tokens
        if not ok:
            return False
    return True


def is_vowel_dropped(norm_text):
    """True when most words of ``norm_text`` have no vowels at all
    ("thnks fr th mmrs") — a stylised spelling, not ordinary words."""
    words = [w for w in norm_text.split() if len(w) >= 2 and w.isalpha()]
    if len(words) < 2:
        return False
    bare = sum(1 for w in words if not _VOWEL_Y_RE.search(w))
    return bare * 2 > len(words)


def fts_match_query(text):
    """Build an FTS5-safe MATCH string from canonical tokens.

    Each term quoted; last term prefix-matched. "" if no tokens.
    """
    toks = tokens(text)
    if not toks:
        return ""
    quoted = [f'"{t}"' for t in toks[:-1]]
    quoted.append(f'"{toks[-1]}"*')
    return ' '.join(quoted)


def fts_skeleton_query(text):
    """FTS5 MATCH string for ``text`` with vowel-dropped word forms, or "".

    Run as a SUPPLEMENTARY search after ``fts_match_query``, keeping only rows
    whose title ``is_vowel_dropped``: a stylised title (Fall Out Boy's "Thnks
    fr th Mmrs") is found from the ordinary spelling, and ordinary titles that
    merely share a dropped form ("mrs") never sneak in or reorder the results.
    """
    toks = tokens(text)
    sk = skeleton_query_tokens(toks)
    if not sk:
        return ""
    last = len(toks) - 1
    terms = []
    for i, (t, s) in enumerate(zip(toks, sk)):
        star = '*' if i == last else ''
        term = f'"{t}"{star}'
        if s != t:
            # A 2-letter dropped form as a prefix ("lv"*) matches too much.
            term = f'({term} OR "{s}"{star if len(s) >= 3 else ""})'
        terms.append(term)
    # FTS5 only implies AND between bare phrases, not around an OR group.
    return ' AND '.join(terms)


# A joined-pair supplement only joins pairs at least this long ("pawnshop"),
# so short pairs ("aha"*) can't pull in noise.
_JOINED_MIN_LEN = 5
# Queries longer than this skip the joined-pair supplement (one OR branch
# per adjacent pair).
_JOINED_MAX_WORDS = 6


def fts_joined_query(text):
    """FTS5 MATCH string accepting any one adjacent word pair of ``text``
    written as one word, or "".

    The index already holds every title's joined pairs (``index_text``), so
    "pawnshop blues" finds "Pawn Shop Blues"; this is the other direction:
    "pawn shop blues" finds a title written "Pawnshop Blues".
    """
    toks = tokens(text)
    if not 2 <= len(toks) <= _JOINED_MAX_WORDS:
        return ""
    branches = []
    for i in range(len(toks) - 1):
        joined = toks[i] + toks[i + 1]
        if len(joined) < _JOINED_MIN_LEN:
            continue
        vt = toks[:i] + [joined] + toks[i + 2:]
        quoted = [f'"{t}"' for t in vt[:-1]] + [f'"{vt[-1]}"*']
        branches.append('(' + ' AND '.join(quoted) + ')')
    return ' OR '.join(branches)


def fts_supplement_queries(text):
    """Supplementary FTS searches run after the ordinary ``fts_match_query``:
    a list of ``(match, vowel_dropped_only)``. When ``vowel_dropped_only`` is
    set, keep only rows whose title ``is_vowel_dropped``."""
    out = []
    skel = fts_skeleton_query(text)
    if skel:
        out.append((skel, True))
    joined = fts_joined_query(text)
    if joined:
        out.append((joined, False))
    return out


def group_key(artist, title):
    """Deterministic (artist, title) -> collapse key for grouping results.

    Also the persisted play-stats ``song_key`` — never change its output
    without migrating stored keys. Result grouping that should ignore
    file-quality tags uses ``result_group_key``.
    """
    return f"{identity_normalize(artist)}|||{identity_normalize(title)}"


# Bracketed file-quality tags that karaoke filenames carry on the artist or
# title ("Fall Out Boy (MPX)", "Dance, Dance [HM]"). They describe the file,
# not the song, so they must not split a song into its own search-result
# group. Musical qualifiers like "(Live)" are deliberately NOT here.
_FILE_TAG_RE = re.compile(
    r'\s*[\[(]\s*(?:mpx|multiplex|hm|homemade)\s*[\])]',
    re.IGNORECASE,
)


def result_group_key(artist, title):
    """``group_key`` with file-quality tags dropped — for collapsing search
    results only (never persisted, so the stored ``song_key`` is unchanged)."""
    return group_key(_FILE_TAG_RE.sub(' ', artist or ''),
                     _FILE_TAG_RE.sub(' ', title or ''))
