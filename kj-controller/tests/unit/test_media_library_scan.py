# kj-controller/tests/unit/test_media_library_scan.py
import os
from media import MediaIndex
from media_library import MediaLibraryStore


def _touch(path):
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 16)


def test_scan_populates_media_library(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    _touch(dl / "NOMAD-0729 - Cher - Believe.mp4")
    _touch(dl / "-UM1XiyBmhM__Sing King__Bella Kay - iloveit (Karaoke Version).mp4")
    idx_path = tmp_path / "media_index.json"

    store = MediaLibraryStore(":memory:")
    cfg = {
        "media_folders": [str(dl)],
        "download_folder": str(dl),
        "media_index_path": str(idx_path),
    }
    mi = MediaIndex(cfg, media_library=store)
    mi.scan()

    master = store.get("nomad-0729")
    assert master and master["artist"] == "Cher" and master["needs_review"] == 0
    yt = store.get("yt--UM1XiyBmhM")
    assert yt and yt["source"] == "youtube" and yt["needs_review"] == 1
    assert yt["file_path"].endswith(".mp4")


def test_scan_reuses_media_id_for_keyless_upload_without_rehash(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    _touch(dl / "Some Random Upload.mp4")
    store = MediaLibraryStore(":memory:")
    cfg = {"media_folders": [str(dl)], "download_folder": str(dl),
           "media_index_path": str(tmp_path / "i.json")}
    mi = MediaIndex(cfg, media_library=store)
    mi.scan()
    rows = store.list_records(source="upload")
    assert len(rows) == 1
    first_id = rows[0]["media_id"]
    assert first_id.startswith("up-")
    mi.scan()  # rescan must not create a duplicate row
    assert len(store.list_records(source="upload")) == 1
    assert store.get(first_id) is not None
