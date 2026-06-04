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


class TestAbbrevAndNumbers:
    def test_pt_to_part(self):
        assert normalize("Song Pt 2") == "song part 2"

    def test_vs_to_versus(self):
        assert normalize("A vs B") == "a versus b"

    def test_word_to_digit(self):
        assert normalize("Two Princes") == "2 princes"

    def test_digit_passthrough(self):
        assert normalize("Blink 182") == "blink 182"

    def test_tens_ones_combine(self):
        assert normalize("Twenty One Pilots") == "21 pilots"

    def test_plain_tens(self):
        assert normalize("Thirty Seconds To Mars") == "30 seconds to mars"

    def test_roman_safe(self):
        assert normalize("Rocky IV") == "rocky 4"

    def test_ambiguous_roman_left_alone(self):
        assert normalize("I Will Survive") == "i will survive"

    def test_maps_exposed(self):
        assert ABBREV_MAP["pt"] == "part"
        assert NUMBER_WORDS["two"] == 2
        assert ROMAN_NUMERALS["iv"] == 4


class TestQueryAndGroupKey:
    def test_fts_quotes_and_prefixes_last(self):
        assert fts_match_query("bon jovi livin") == '"bon" "jovi" "livin"*'

    def test_fts_uses_canonical_tokens(self):
        assert fts_match_query("Simon & Garfunkel") == '"simon" "and" "garfunkel"*'

    def test_fts_empty(self):
        assert fts_match_query("   ") == ""

    def test_group_key(self):
        assert group_key("Simon & Garfunkel", "The Sound of Silence") == \
            "simon and garfunkel|||the sound of silence"

    def test_group_key_none_safe(self):
        assert group_key(None, None) == "|||"
