"""Unit tests for the shared fuzzy_match scorer.

fuzzy_match is the single source of truth for the precision-gated rapidfuzz
scoring used by BOTH catalog._fuzzy_search and the media-index typo fallback in
routes.unified_search. Inputs are pre-normalized (text_normalize.normalize).
"""
import fuzzy_match
from text_normalize import normalize


def _score(query, hay):
    return fuzzy_match.score(normalize(query), normalize(hay))


class TestFuzzyScore:
    def test_typo_with_other_matching_words_passes(self):
        # "boxs" is a typo, but "books"/"from" still overlap -> match.
        res = _score("books from boxs", "Maximo Park - Books from Boxes")
        assert res is not None
        overlap, wratio = res
        assert overlap >= fuzzy_match.FUZZY_MIN_TOKEN_OVERLAP
        assert wratio >= fuzzy_match.FUZZY_SCORE_CUTOFF

    def test_unrelated_text_is_rejected(self):
        assert _score("completely unrelated query", "Maximo Park - Books from Boxes") is None

    def test_single_word_typo_no_overlap_rejected(self):
        # Only significant token is the typo itself -> zero overlap -> None.
        # (Matches catalog behavior; KN/Divebar covers this case.)
        assert _score("viena", "Billy Joel - Vienna") is None

    def test_empty_inputs_return_none(self):
        assert fuzzy_match.score("", "anything") is None
        assert fuzzy_match.score("anything", "") is None

    def test_precomputed_q_sig_matches_auto(self):
        nq = normalize("books from boxs")
        q_sig = fuzzy_match.significant_tokens(nq)
        hay = normalize("Maximo Park - Books from Boxes")
        assert fuzzy_match.score(nq, hay, q_sig=q_sig) == fuzzy_match.score(nq, hay)

    def test_significant_tokens_drops_short_words(self):
        # len>=4 kept; shorter dropped.
        assert fuzzy_match.significant_tokens(normalize("the books from boxs")) == {
            "books", "from", "boxs",
        }
