"""Shared typo-tolerant fuzzy scoring for song search.

Single source of truth for the precision-gated rapidfuzz scoring used by BOTH
the external catalog fuzzy fallback (``catalog._fuzzy_search``) and the
downloaded-media search in ``routes.unified_search``. Keeping one implementation
means a misspelled query like "books from boxs" surfaces an already-downloaded
local file the same way it surfaces Karaoke Nerds / Divebar catalog results.

Inputs are expected to be pre-normalized via ``text_normalize.normalize`` so
needle and haystack meet in the same canonical space (diacritics folded,
"&" -> "and", apostrophes dropped, numbers canonicalized, etc.).
"""

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

# Historical WRatio cutoff (0-100). No longer a gate — full token coverage
# replaced it (see score()) — kept for scripts/search_metrics.py analyses and
# the catalog re-export.
FUZZY_SCORE_CUTOFF = 80
# When the query has NO significant (len>=4) tokens, the coverage gate can't apply;
# require a near-exact score instead.
FUZZY_SHORT_QUERY_CUTOFF = 95
# A token this long or longer counts as "significant" for the coverage gate.
SIGNIFICANT_TOKEN_LEN = 4


def significant_tokens(norm_query):
    """Return the set of query tokens long enough to gate on (len >= 4).

    ``norm_query`` must already be normalized (space-joined canonical tokens).
    """
    return {t for t in norm_query.split() if len(t) >= SIGNIFICANT_TOKEN_LEN}


def _typo_threshold(tok):
    # Max edit distance for a token to count as a typo of a haystack word,
    # scaled by length so short tokens stay near-exact. Mirrors the community
    # catalog's fuzzy matching (divebar-lookup CF _kn_match_parts).
    n = len(tok)
    if n < SIGNIFICANT_TOKEN_LEN:
        return 0
    return 1 if n <= 6 else 2


def _token_covered(tok, hay_tokens, norm_hay):
    """A query token is covered when it appears in the haystack as a substring
    (exact word or partial typing, e.g. "bohem" in "bohemian") or within a
    small Levenshtein distance of some haystack word (typo, e.g. "boxs" for
    "boxes")."""
    if tok in norm_hay:
        return True
    thr = _typo_threshold(tok)
    return any(Levenshtein.distance(tok, w) <= thr for w in hay_tokens)


def score(norm_query, norm_hay, q_sig=None):
    """Fuzzy-match ``norm_hay`` against ``norm_query``; return ``(overlap, wratio)`` or ``None``.

    Both arguments must already be normalized via ``text_normalize.normalize``.
    Precision gate (token-AND, mirroring the community-catalog search):

    - EVERY significant (len>=4) query token must be covered by the haystack —
      present as a substring, or a typo within a small edit distance of some
      haystack word (see :func:`_token_covered`). Typo tolerance never excuses
      a MISSING word, so "queen bohemian" cannot match "Queen - We Will Rock
      You". (The previous gate required only 50% of tokens verbatim plus
      WRatio>=80, which let any two-word query match everything else by that
      artist.) Full coverage is a stronger relevance signal than WRatio, so
      when it applies WRatio is ranking-only — a lone typo like "viena" still
      matches "Billy Joel - Vienna" even though WRatio is diluted by the long
      haystack.
    - If the query has no significant tokens (all short), the coverage gate
      can't apply, so require a near-exact ``FUZZY_SHORT_QUERY_CUTOFF`` WRatio.

    Returns a ``(overlap, wratio)`` tuple when the haystack passes, else
    ``None``. ``overlap`` is the fraction of significant tokens present
    VERBATIM (typo/partial coverage counts for the gate but not the rank), so
    exact-word matches sort above typo matches; both elements are
    higher-is-better for (overlap, score) ranking. Pass ``q_sig`` (from
    :func:`significant_tokens`) to avoid recomputing it in a hot loop.
    """
    if not norm_query or not norm_hay:
        return None
    if q_sig is None:
        q_sig = significant_tokens(norm_query)
    wratio = fuzz.WRatio(norm_query, norm_hay)
    if q_sig:
        hay_tokens = set(norm_hay.split())
        if not all(_token_covered(t, hay_tokens, norm_hay) for t in q_sig):
            return None
        overlap = len(q_sig & hay_tokens) / len(q_sig)
    else:
        if wratio < FUZZY_SHORT_QUERY_CUTOFF:
            return None
        overlap = 0.0
    return (overlap, wratio)
