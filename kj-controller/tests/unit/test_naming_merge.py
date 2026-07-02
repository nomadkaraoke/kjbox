import naming


def _det():
    return {"source": "youtube", "source_ref": "UM1XiyBmhM",
            "artist": "Bella Kay", "title": "iloveit",
            "confidence": 0.4, "needs_review": 1, "parse_method": "deterministic"}


def test_merge_none_keeps_deterministic():
    assert naming.merge_llm_result(_det(), None, 0.75) == _det()


def test_merge_empty_llm_keeps_deterministic():
    assert naming.merge_llm_result(
        _det(), {"artist": "", "title": "", "confidence": 0.0}, 0.75) == _det()


def test_merge_high_confidence_clears_review():
    out = naming.merge_llm_result(
        _det(), {"artist": "Bella Kay", "title": "iloveit", "confidence": 0.9}, 0.75)
    assert out["parse_method"] == "llm"
    assert out["needs_review"] == 0
    assert out["confidence"] == 0.9
    assert out["source_ref"] == "UM1XiyBmhM"  # identity preserved


def test_merge_low_confidence_keeps_review_but_takes_values():
    out = naming.merge_llm_result(
        _det(), {"artist": "Sublime", "title": "Santeria", "confidence": 0.5}, 0.75)
    assert (out["artist"], out["title"]) == ("Sublime", "Santeria")
    assert out["needs_review"] == 1
    assert out["parse_method"] == "llm"


def test_merge_partial_llm_preserves_deterministic_field():
    # LLM returns only a title -> keep the deterministic artist, don't blank it.
    out = naming.merge_llm_result(
        _det(), {"artist": "", "title": "I Love It", "confidence": 0.9}, 0.75)
    assert out["artist"] == "Bella Kay"  # preserved
    assert out["title"] == "I Love It"   # taken from LLM
    # LLM returns only an artist -> keep the deterministic title.
    out2 = naming.merge_llm_result(
        _det(), {"artist": "Icona Pop", "title": "", "confidence": 0.9}, 0.75)
    assert out2["artist"] == "Icona Pop"
    assert out2["title"] == "iloveit"


def test_youtube_id_from_url():
    assert naming.youtube_id_from_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert naming.youtube_id_from_url("https://youtu.be/dQw4w9WgXcQ?t=3") == "dQw4w9WgXcQ"
    assert naming.youtube_id_from_url("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert naming.youtube_id_from_url("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert naming.youtube_id_from_url("not a url") is None
