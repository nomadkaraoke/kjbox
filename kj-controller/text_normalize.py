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
    return ' '.join(toks)


def tokens(text):
    """Return the canonical token list for `text`."""
    n = normalize(text)
    return n.split() if n else []
