# kj-controller/tests/unit/test_media_library_prune.py
"""delete_file must remove the media_library row; scan() must prune
download-source rows whose file vanished — and never touch masters or
rows under an unmounted root."""
import os

from media import MediaIndex
from media_library import MediaLibraryStore


def _touch(path):
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 16)


def _mi(tmp_path, store):
    dl = tmp_path / "downloads"
    dl.mkdir(exist_ok=True)
    cfg = {
        "media_folders": [str(dl)],
        "download_folder": str(dl),
        "media_index_path": str(tmp_path / "media_index.json"),
    }
    return MediaIndex(cfg, media_library=store), dl


YT_NAME = "-UM1XiyBmhM__Sing King__Bella Kay - iloveit (Karaoke Version).mp4"


def test_delete_file_removes_media_library_row(tmp_path):
    store = MediaLibraryStore(":memory:")
    mi, dl = _mi(tmp_path, store)
    _touch(dl / YT_NAME)
    mi.scan()
    path = str(dl / YT_NAME)
    assert store.get_by_path(path) is not None

    mi.delete_file(path)

    assert store.get_by_path(path) is None
    assert store.get("yt--UM1XiyBmhM") is None
    assert path not in mi.index


def test_delete_file_without_media_library_still_works(tmp_path):
    mi, dl = _mi(tmp_path, None)
    _touch(dl / YT_NAME)
    mi.scan()
    path = str(dl / YT_NAME)
    mi.delete_file(path)  # must not raise
    assert not os.path.exists(path)


def test_scan_prunes_rows_for_vanished_download_files(tmp_path):
    store = MediaLibraryStore(":memory:")
    mi, dl = _mi(tmp_path, store)
    _touch(dl / YT_NAME)
    mi.scan()
    assert store.get("yt--UM1XiyBmhM") is not None

    os.remove(dl / YT_NAME)
    mi.scan()

    assert store.get("yt--UM1XiyBmhM") is None


def test_scan_never_prunes_master_rows(tmp_path):
    store = MediaLibraryStore(":memory:")
    mi, dl = _mi(tmp_path, store)
    _touch(dl / "NOMAD-0729 - Cher - Believe.mp4")
    mi.scan()
    assert store.get("nomad-0729") is not None

    # A master can be transiently absent mid GCS-rsync; its row must survive.
    os.remove(dl / "NOMAD-0729 - Cher - Believe.mp4")
    mi.scan()

    assert store.get("nomad-0729") is not None


def test_scan_keeps_rows_when_root_unmounted(tmp_path):
    """An unmounted/missing media root must never mass-prune its rows."""
    import shutil

    store = MediaLibraryStore(":memory:")
    mi, dl = _mi(tmp_path, store)
    _touch(dl / YT_NAME)
    mi.scan()
    assert store.get("yt--UM1XiyBmhM") is not None

    shutil.rmtree(dl)  # simulate the whole drive/folder disappearing
    mi.scan()

    assert store.get("yt--UM1XiyBmhM") is not None


def test_scan_keeps_rows_outside_scanned_roots(tmp_path):
    """Rows whose file_path lives outside every media root (e.g. future SSD
    'library' rows) are out of scan's jurisdiction — never pruned."""
    store = MediaLibraryStore(":memory:")
    mi, _dl = _mi(tmp_path, store)
    store.upsert({
        "media_id": "up-deadbeef0000",
        "source": "upload",
        "artist": "A", "title": "T",
        "file_path": str(tmp_path / "elsewhere" / "gone.mp4"),
        "ext": ".mp4",
    })

    mi.scan()

    assert store.get("up-deadbeef0000") is not None
