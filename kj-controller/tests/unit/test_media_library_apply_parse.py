from media_library import MediaLibraryStore


def _store():
    s = MediaLibraryStore(":memory:")
    s.upsert({"media_id": "yt-x", "source": "youtube", "artist": "A", "title": "B",
              "needs_review": 1, "confidence": 0.4})
    return s


def test_apply_parse_high_confidence_clears_review():
    s = _store()
    assert s.apply_parse("yt-x", "Queen", "Bohemian Rhapsody", 0.9, 0.75) is True
    row = s.get("yt-x")
    assert (row["artist"], row["title"]) == ("Queen", "Bohemian Rhapsody")
    assert row["needs_review"] == 0
    assert row["parse_method"] == "llm"
    assert row["confidence"] == 0.9
    assert row["artist_norm"]  # recomputed


def test_apply_parse_low_confidence_keeps_review():
    s = _store()
    s.apply_parse("yt-x", "Sublime", "Santeria", 0.5, 0.75)
    assert s.get("yt-x")["needs_review"] == 1


def test_apply_parse_missing_row_returns_false():
    s = _store()
    assert s.apply_parse("nope", "A", "B", 0.9, 0.75) is False
