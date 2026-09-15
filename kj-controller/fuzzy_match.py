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

# Fuzzy fallback score cutoff (0-100). Tunable; validated by scripts/search_metrics.py.
FUZZY_SCORE_CUTOFF = 80
# Min fraction of the query's significant tokens (len>=4) that must appear in a
# fuzzy candidate. Precision gate: stops WRatio's partial_ratio from inventing
# matches that share no real words (real-data analysis: 190/254 fuzzy hits were
# zero-overlap garbage at the old WRatio>=80-only setting).
FUZZY_MIN_TOKEN_OVERLAP = 0.5
# When the query has NO significant (len>=4) tokens, the overlap gate can't apply;
# require a near-exact score instead.
FUZZY_SHORT_QUERY_CUTOFF = 95
# A token this long or longer counts as "significant" for the overlap gate.
SIGNIFICANT_TOKEN_LEN = 4


def significant_tokens(norm_query):
    """Return the set of query tokens long enough to gate on (len >= 4).

    ``norm_query`` must already be normalized (space-joined canonical tokens).
    """
    return {t for t in norm_query.split() if len(t) >= SIGNIFICANT_TOKEN_LEN}


def score(norm_query, norm_hay, q_sig=None):
    """Fuzzy-match ``norm_hay`` against ``norm_query``; return ``(overlap, wratio)`` or ``None``.

    Both arguments must already be normalized via ``text_normalize.normalize``.
    Applies the same precision gates as the catalog fuzzy fallback:

    - ``WRatio`` must be at least ``FUZZY_SCORE_CUTOFF``.
    - At least ``FUZZY_MIN_TOKEN_OVERLAP`` of the query's significant (len>=4)
      tokens must appear verbatim in the haystack. This stops WRatio's
      partial_ratio component from matching text that shares no real words.
    - If the query has no significant tokens (all short), the overlap gate can't
      apply, so require a near-exact ``FUZZY_SHORT_QUERY_CUTOFF`` score instead.

    Returns a ``(overlap, wratio)`` tuple (both higher-is-better, so callers can
    rank by overlap first then score) when the haystack passes, else ``None``.
    Pass ``q_sig`` (from :func:`significant_tokens`) to avoid recomputing it in
    a hot loop over many candidates.
    """
    if not norm_query or not norm_hay:
        return None
    if q_sig is None:
        q_sig = significant_tokens(norm_query)
    wratio = fuzz.WRatio(norm_query, norm_hay)
    if wratio < FUZZY_SCORE_CUTOFF:
        return None
    if q_sig:
        hay_tokens = set(norm_hay.split())
        overlap = len(q_sig & hay_tokens) / len(q_sig)
        if overlap < FUZZY_MIN_TOKEN_OVERLAP:
            return None
    else:
        if wratio < FUZZY_SHORT_QUERY_CUTOFF:
            return None
        overlap = 0.0
    return (overlap, wratio)
