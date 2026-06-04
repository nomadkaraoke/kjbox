"""Single source of truth for song-text normalization.

Pure functions (no Flask / SQLite). The same pipeline is applied to indexed
catalog text AND to user queries so both meet in one canonical space. A JS twin
(static/text_normalize.js) mirrors this exactly and is verified by a parity test.

Pipeline order (order matters):
  1. Unicode NFD + strip combining marks + Latin special-char fold
  2. lowercase
  3. strip feat./ft./featuring qualifiers
  4. expand symbol conjunctions  &  +  -> " and "
  5. drop apostrophes; replace remaining punctuation with space
  6. token-level word abbreviations (pt->part, vs->versus, ...)
  7. number canonicalization: words/roman -> digits, with tens+ones adjacency
"""

import re
import unicodedata

# Bump whenever the pipeline or any map changes; gates catalog reindex.
NORMALIZER_VERSION = 1

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
_FEAT_RE = re.compile(
    r'\s*[\[(]?\s*(?:feat\.?|ft\.?|featuring)\s+[^\])]+[\])]?',
    re.IGNORECASE,
)
_SYMBOL_AND_RE = re.compile(r'\s*[&+]\s*')
_APOS_RE = re.compile("[‘’ʼ`']+")
_PUNCT_RE = re.compile(r'[^\w\s]', re.UNICODE)


def _strip_accents(text):
    s = unicodedata.normalize('NFD', text)
    s = re.sub(r'[̀-ͯ]', '', s)
    s = _LATIN_SPECIAL_RE.sub(lambda m: LATIN_SPECIAL_MAP[m.group()], s)
    return s


def normalize(text):
    """Return the canonical, space-joined token string for `text`."""
    if not text:
        return ""
    s = _strip_accents(text)
    s = s.lower()
    s = _FEAT_RE.sub(' ', s)
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
    "hundred": 100, "thousand": 1000,
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


def group_key(artist, title):
    """Deterministic (artist, title) -> collapse key for grouping results."""
    return f"{normalize(artist)}|||{normalize(title)}"
