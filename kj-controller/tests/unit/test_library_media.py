# kj-controller/tests/unit/test_library_media.py
import os

import library_media
from media_library import MediaLibraryStore


class _FakeCatalog:
    def __init__(self, rows=None):
        self.rows = rows or {}

    def get_by_path(self, path):
        return self.rows.get(path)


def _touch(path, content=b"same-bytes"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


def test_is_library_path():
    cfg = {"external_media_mount": "/media/nomad/Nomad4TBOne"}
    assert library_media.is_library_path("/media/nomad/Nomad4TBOne/Discs/x.zip", cfg)
    assert not library_media.is_library_path("/opt/nomad/downloads/x.mp4", cfg)
    assert not library_media.is_library_path("/media/nomad/Nomad4TBOne-evil/x.zip", cfg)
    assert not library_media.is_library_path("/media/nomad/Nomad4TBOne/x.zip", {})
    assert not library_media.is_library_path(None, cfg)


def test_ensure_creates_row_from_catalog(tmp_path):
    p = str(tmp_path / "Discs" / "SC1 - ABBA - SOS.zip")
    _touch(p)
    ml = MediaLibraryStore(":memory:")
    cat = _FakeCatalog({p: {"artist": "ABBA", "title": "SOS", "disc_id": "SC1"}})
    row = library_media.ensure_library_row(p, cat, ml)
    assert row["media_id"].startswith("lib-") and len(row["media_id"]) == len("lib-") + 12
    assert row["source"] == "library" and row["artist"] == "ABBA"
    assert row["parse_method"] == "catalog" and row["needs_review"] == 0
    assert row["file_path"] == p and row["ext"] == ".zip"


def test_ensure_catalog_miss_falls_back_to_deterministic(tmp_path):
    p = str(tmp_path / "Discs" / "XY9 - Queen - Under Pressure.zip")
    _touch(p)
    ml = MediaLibraryStore(":memory:")
    row = library_media.ensure_library_row(p, _FakeCatalog(), ml)
    assert row["artist"] == "Queen" and row["title"] == "Under Pressure"
    assert row["parse_method"] == "deterministic" and row["needs_review"] == 1


def test_ensure_existing_by_path_row_skips_hashing(tmp_path, monkeypatch):
    p = str(tmp_path / "Discs" / "SC1 - ABBA - SOS.zip")
    _touch(p)
    ml = MediaLibraryStore(":memory:")
    library_media.ensure_library_row(p, _FakeCatalog(), ml)

    def boom(_):
        raise AssertionError("content_hash must not run when a by-path row exists")

    monkeypatch.setattr(library_media, "content_hash", boom)
    row = library_media.ensure_library_row(p, _FakeCatalog(), ml)
    assert row is not None


def test_ensure_moved_file_same_id_heals_path_keeps_identity(tmp_path):
    old = str(tmp_path / "Discs" / "SC1 - ABBA - SOS.zip")
    _touch(old, b"identical-content")
    ml = MediaLibraryStore(":memory:")
    row1 = library_media.ensure_library_row(old, _FakeCatalog(), ml)
    ml.set_metadata(row1["media_id"], "ABBA", "S.O.S.")  # manual ✎ edit
    new = str(tmp_path / "Reorganised" / "ABBA — SOS (SC1).zip")
    _touch(new, b"identical-content")
    os.remove(old)
    row2 = library_media.ensure_library_row(new, _FakeCatalog(), ml)
    assert row2["media_id"] == row1["media_id"]          # same content -> same id
    assert row2["file_path"] == new                       # path healed
    assert row2["title"] == "S.O.S."                      # manual edit NOT clobbered
    assert row2["parse_method"] == "manual"


def test_ensure_missing_or_none_inputs(tmp_path):
    ml = MediaLibraryStore(":memory:")
    assert library_media.ensure_library_row(str(tmp_path / "gone.zip"), _FakeCatalog(), ml) is None
    assert library_media.ensure_library_row(None, _FakeCatalog(), ml) is None
    assert library_media.ensure_library_row("/x.zip", _FakeCatalog(), None) is None


def test_run_async_executes_target():
    import threading
    done = threading.Event()
    library_media.run_async(lambda: done.set())
    assert done.wait(2.0)
