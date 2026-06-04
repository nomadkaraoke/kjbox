def test_rapidfuzz_importable():
    from rapidfuzz import fuzz
    assert fuzz.WRatio("simon and garfunkel", "simon & garfunkel") > 80


from text_normalize import (
    normalize, tokens, fts_match_query, group_key,
    LATIN_SPECIAL_MAP, ABBREV_MAP, NUMBER_WORDS, ROMAN_NUMERALS,
    NORMALIZER_VERSION,
)


class TestDiacritics:
    def test_acute_lowercased(self):
        assert normalize("Beyoncé") == "beyonce"

    def test_o_stroke(self):
        assert normalize("MØ") == "mo"

    def test_sharp_s(self):
        assert normalize("Straßenbande") == "strassenbande"

    def test_empty(self):
        assert normalize("") == ""

    def test_none_safe(self):
        assert normalize(None) == ""

    def test_version_is_int(self):
        assert isinstance(NORMALIZER_VERSION, int) and NORMALIZER_VERSION >= 1


class TestPunctAndFeat:
    def test_ampersand_to_and(self):
        assert normalize("Simon & Garfunkel") == "simon and garfunkel"

    def test_plus_to_and(self):
        assert normalize("Florence + The Machine") == "florence and the machine"

    def test_apostrophe_dropped(self):
        assert normalize("Don't Stop") == "dont stop"

    def test_punct_to_space(self):
        assert normalize("Hello, World! (x)") == "hello world x"

    def test_feat_stripped(self):
        assert normalize("Stay feat. Justin Bieber") == "stay"

    def test_ft_stripped(self):
        assert normalize("Title ft. Someone Else") == "title"

    def test_featuring_stripped(self):
        assert normalize("Song featuring The Band") == "song"

    def test_tokens(self):
        assert tokens("Simon & Garfunkel") == ["simon", "and", "garfunkel"]

    def test_tokens_empty(self):
        assert tokens("") == []
