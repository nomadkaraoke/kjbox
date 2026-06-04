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
