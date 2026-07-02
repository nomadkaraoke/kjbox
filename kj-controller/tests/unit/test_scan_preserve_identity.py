"""Regression: a rescan must NOT clobber curated (llm/manual) media_library rows.

Before this fix, scan() re-derived deterministic identity from the (canonical
slug) filename and overwrote refined/edited artist/title on every rescan, resetting
needs_review and polluting the title with the [media_id] token.
"""
import os

import naming
from media import MediaIndex
from media_library import MediaLibraryStore


def _touch(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"\x00" * 16)


def test_strip_media_id_token():
    assert naming.strip_media_id_token(
        "The Animals - The House of the Rising Sun [yt-rUpPWuFP--o].mp4"
    ) == "The Animals - The House of the Rising Sun.mp4"
    assert naming.strip_media_id_token("no token here.mp4") == "no token here.mp4"


def test_upsert_scanned_preserves_curated_updates_path():
    s = MediaLibraryStore(":memory:")
    s.upsert({"media_id": "yt-a", "source": "youtube", "artist": "Queen",
              "title": "Bohemian Rhapsody", "needs_review": 0,
              "parse_method": "llm", "file_path": "/old.mp4", "ext": ".mp4"})
    s.upsert_scanned({"media_id": "yt-a", "source": "upload", "artist": "wrong",
                      "title": "bad [yt-a]", "needs_review": 1,
                      "parse_method": "deterministic", "file_path": "/new.mp4",
                      "ext": ".mp4"})
    row = s.get("yt-a")
    assert (row["artist"], row["title"]) == ("Queen", "Bohemian Rhapsody")  # preserved
    assert row["needs_review"] == 0 and row["parse_method"] == "llm"
    assert row["file_path"] == "/new.mp4"  # path refreshed


def test_upsert_scanned_creates_new_row():
    s = MediaLibraryStore(":memory:")
    s.upsert_scanned({"media_id": "yt-b", "source": "youtube", "artist": "A",
                      "title": "B", "needs_review": 1, "file_path": "/x.mp4"})
    assert s.get("yt-b")["artist"] == "A"


def test_rescan_does_not_clobber_refined_row(tmp_path):
    dl = str(tmp_path / "downloads")
    # A migrated, refined file living at its canonical slug path.
    slug = os.path.join(dl, "youtube", "The Animals - The House of the Rising Sun [yt-rUpPWuFP--o].mp4")
    _touch(slug)
    store = MediaLibraryStore(":memory:")
    store.upsert({"media_id": "yt-rUpPWuFP--o", "source": "youtube",
                  "artist": "The Animals", "title": "The House of the Rising Sun",
                  "needs_review": 0, "parse_method": "llm", "file_path": slug,
                  "ext": ".mp4"})
    mi = MediaIndex({"media_folders": [dl], "download_folder": dl,
                     "media_index_path": str(tmp_path / "i.json")},
                    media_library=store)
    mi.scan()
    row = store.get("yt-rUpPWuFP--o")
    assert row["artist"] == "The Animals"
    assert row["title"] == "The House of the Rising Sun"  # NOT polluted with token
    assert row["needs_review"] == 0 and row["parse_method"] == "llm"
    assert row["source"] == "youtube"


def test_new_tokened_file_gets_clean_identity(tmp_path):
    dl = str(tmp_path / "downloads")
    slug = os.path.join(dl, "youtube", "Paramore - Conspiracy [yt-sqrEE9VbXQA].mp4")
    _touch(slug)
    store = MediaLibraryStore(":memory:")  # no prior row
    mi = MediaIndex({"media_folders": [dl], "download_folder": dl,
                     "media_index_path": str(tmp_path / "i.json")},
                    media_library=store)
    mi.scan()
    row = store.get("yt-sqrEE9VbXQA")
    assert row is not None and row["source"] == "youtube"  # from token prefix
    assert "[yt-" not in row["title"]  # token stripped from the parsed title
