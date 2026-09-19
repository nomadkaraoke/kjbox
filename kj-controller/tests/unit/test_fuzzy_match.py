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
        # "boxs" is a typo of "boxes"; every significant token is covered -> match.
        res = _score("books from boxs", "Maximo Park - Books from Boxes")
        assert res is not None
        overlap, wratio = res
        assert overlap > 0
        assert wratio >= fuzzy_match.FUZZY_SCORE_CUTOFF

    def test_unrelated_text_is_rejected(self):
        assert _score("completely unrelated query", "Maximo Park - Books from Boxes") is None

    def test_missing_query_word_is_rejected(self):
        # Regression (v0.101.0 was too loose): "queen bohemian" must NOT match
        # other Queen songs just because "queen" overlaps and WRatio is high.
        # Token-AND: "bohemian" is covered by nothing in the haystack -> None.
        assert _score("queen bohemian", "Queen - We Will Rock You") is None
        assert _score("queen bohemian", "Queen - Bohemian Rhapsody") is not None

    def test_single_word_typo_is_covered(self):
        # A lone typo token within edit distance of a haystack word matches
        # (same tolerance as the community catalog: viena -> Vienna).
        assert _score("viena", "Billy Joel - Vienna") is not None
        assert _score("zomvie", "The Cranberries - Zombie") is not None

    def test_partial_word_prefix_is_covered(self):
        # Substring coverage: partially-typed word still matches.
        assert _score("queen bohem", "Queen - Bohemian Rhapsody") is not None

    def test_verbatim_matches_rank_above_typo_matches(self):
        exact = _score("books from boxes", "Maximo Park - Books from Boxes")
        typo = _score("books from boxs", "Maximo Park - Books from Boxes")
        assert exact is not None and typo is not None
        # overlap counts only VERBATIM tokens, so the exact query ranks higher.
        assert exact[0] > typo[0]

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
