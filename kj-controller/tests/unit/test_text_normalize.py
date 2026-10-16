import pytest


def test_rapidfuzz_importable():
    from rapidfuzz import fuzz
    assert fuzz.WRatio("simon and garfunkel", "simon & garfunkel") > 80


from text_normalize import (
    normalize, identity_normalize, tokens, fts_match_query, group_key, result_group_key,
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

    # identity_normalize (grouping / song_key / same-song joins) strips the whole
    # feat clause; normalize (search) drops only the feat word and keeps names.
    def test_feat_stripped(self):
        assert identity_normalize("Stay feat. Justin Bieber") == "stay"
        assert normalize("Stay feat. Justin Bieber") == "stay justin bieber"

    def test_ft_stripped(self):
        assert identity_normalize("Title ft. Someone Else") == "title"
        assert normalize("Title ft. Someone Else") == "title someone else"

    def test_ft_inside_a_word_is_not_a_feat(self):
        # Regression: the feat regex had no word boundary, so any "ft" mid-word
        # truncated the rest of the string ("Soft Cell" -> "so").
        for fn in (normalize, identity_normalize):
            assert fn("Soft Cell") == "soft cell"
            assert fn("Hayloft II") == "hayloft 2"
            assert fn("Left Outside Alone") == "left outside alone"
            assert fn("Жft Song") == "жft song"
        assert identity_normalize("Taylor Swift ft. Drake") == "taylor swift"
        assert identity_normalize("Craft (feat. X)") == "craft"
        assert normalize("Taylor Swift ft. Drake") == "taylor swift drake"
        assert normalize("Craft (feat. X)") == "craft x"

    def test_featuring_stripped(self):
        assert identity_normalize("Song featuring The Band") == "song"
        assert normalize("Song featuring The Band") == "song the band"

    def test_feat_with_trailing_paren_behavior(self):
        # Identity: intentionally lossy (the clause runs to the closing bracket).
        assert identity_normalize("Stay (Acoustic) feat. Justin Bieber (Live)") == "stay acoustic"
        assert normalize("Stay (Acoustic) feat. Justin Bieber (Live)") == \
            "stay acoustic justin bieber live"

    def test_search_keeps_title_after_unbracketed_feat(self):
        # Regression (v0.131.1): index text is "artist title" concatenated, and the
        # clause strip ran to END OF STRING — "Zedd feat. Hayley Williams of Paramore
        # Stay the Night" indexed as just "zedd", so "hayley stay the night" found
        # neither NOMAD-1567 nor KPARTY-354.
        text = "Zedd feat. Hayley Williams of Paramore Stay the Night"
        assert normalize(text) == "zedd hayley williams of paramore stay the night"
        assert set(tokens("hayley stay the night")) <= set(tokens(text))
        assert set(tokens("zedd ft hayley stay the night")) <= set(tokens(text))

    def test_feat_word_needs_following_space(self):
        assert normalize("Feats Of Strength") == "feats of strength"
        assert normalize("The Feat") == "the feat"

    def test_group_key_still_strips_feat_clause(self):
        # group_key is the persisted play-stats song_key — must not change.
        assert group_key("Zedd feat. Hayley Williams", "Stay the Night (feat. X)") == \
            "zedd|||stay the night"

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

    def test_compound_hundreds_out_of_scope(self):
        # Standalone hundreds/thousands are NOT canonicalized (compound parsing
        # is out of scope); the word passes through unchanged.
        assert normalize("One Hundred Years") == "1 hundred years"


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

    def test_fts_single_token_prefix(self):
        assert fts_match_query("jovi") == '"jovi"*'

    def test_result_group_key_strips_file_quality_tags(self):
        assert result_group_key("Fall Out Boy (MPX)", "Dance, Dance") == \
            group_key("Fall Out Boy", "Dance Dance")
        assert result_group_key("Fall Out Boy", "Dance, Dance [HM]") == \
            group_key("Fall Out Boy", "Dance Dance")

    def test_result_group_key_keeps_musical_qualifiers(self):
        assert result_group_key("Queen", "Bohemian Rhapsody (Live)") != \
            result_group_key("Queen", "Bohemian Rhapsody")

    def test_group_key_unchanged_for_persisted_song_keys(self):
        """group_key is the stored play-stats song_key: tags stay in it."""
        assert group_key("Fall Out Boy (MPX)", "Dance, Dance") != \
            group_key("Fall Out Boy", "Dance, Dance")

    def test_group_key_none_safe(self):
        assert group_key(None, None) == "|||"


class TestIdempotency:
    @pytest.mark.parametrize("text", [
        "Simon & Garfunkel - Sound Of Silence",
        "Twenty One Pilots",
        "Beyoncé feat. Jay-Z",
        "Don't Stop Believin'",
        "Blink 182",
        "Rocky IV",
    ])
    def test_normalize_is_idempotent(self, text):
        once = normalize(text)
        assert normalize(once) == once
