# kj-controller/tests/unit/test_media_library.py
from media_library import MediaLibraryStore


def _store():
    return MediaLibraryStore(":memory:")


def test_upsert_and_get_roundtrip():
    s = _store()
    s.upsert({
        "media_id": "yt-UM1XiyBmhM", "source": "youtube", "source_ref": "UM1XiyBmhM",
        "artist": "Bella Kay", "title": "iloveit", "confidence": 0.4,
        "parse_method": "deterministic", "needs_review": 1,
        "raw_original_name": "-UM1XiyBmhM__Sing King__Bella Kay - iloveit.mp4",
        "file_path": "/x/a.mp4", "ext": ".mp4",
    })
    row = s.get("yt-UM1XiyBmhM")
    assert row["artist"] == "Bella Kay"
    assert row["needs_review"] == 1
    assert row["artist_norm"]  # normalized field populated


def test_upsert_is_idempotent_on_media_id():
    s = _store()
    base = {"media_id": "nomad-0729", "source": "master", "artist": "Cher", "title": "Believe"}
    s.upsert(base)
    s.upsert({**base, "title": "Believe (v2)"})
    assert s.get("nomad-0729")["title"] == "Believe (v2)"
    assert len(s.list_records()) == 1


def test_get_by_path():
    s = _store()
    s.upsert({"media_id": "up-abc12345", "source": "upload", "file_path": "/y/b.mp4"})
    assert s.get_by_path("/y/b.mp4")["media_id"] == "up-abc12345"
    assert s.get_by_path("/nope.mp4") is None


def test_set_metadata_marks_manual_and_clears_review():
    s = _store()
    s.upsert({"media_id": "yt-x", "source": "youtube", "artist": "A", "title": "B",
              "needs_review": 1, "confidence": 0.4})
    assert s.set_metadata("yt-x", "Real Artist", "Real Title") is True
    row = s.get("yt-x")
    assert (row["artist"], row["title"]) == ("Real Artist", "Real Title")
    assert row["needs_review"] == 0
    assert row["parse_method"] == "manual"
    assert row["confidence"] is None


def test_list_filters():
    s = _store()
    s.upsert({"media_id": "a", "source": "youtube", "needs_review": 1})
    s.upsert({"media_id": "b", "source": "master", "needs_review": 0})
    assert {r["media_id"] for r in s.list_records(source="youtube")} == {"a"}
    assert {r["media_id"] for r in s.list_records(needs_review=1)} == {"a"}
