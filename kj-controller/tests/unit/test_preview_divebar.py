import os

import pytest

import preview
from preview import PreviewService


class FakeMedia:
    def validate_path(self, p):
        return None  # divebar blobs live in the cache, never in media_folders


@pytest.fixture
def svc(tmp_path):
    cfg = {"preview_cache_dir": str(tmp_path / "cache"), "preview_cache_max_bytes": 10 ** 9}
    return PreviewService(cfg, FakeMedia())


def test_divebar_downloads_then_classifies_native(svc, monkeypatch):
    blob = b"\0" * 300
    monkeypatch.setattr(preview.divebar, "get_download_url",
                        lambda fid, cfg: "https://storage.googleapis.com/x/y.mp4")
    monkeypatch.setattr(preview.utils, "divebar_ext", lambda url, fmt: ".mp4")
    monkeypatch.setattr(preview, "_download_to", lambda url, dst: open(dst, "wb").write(blob))
    monkeypatch.setattr(preview, "_ffprobe_codecs", lambda p: ("h264", "aac"))
    r = svc.resolve({"source": "divebar", "file_id": "FID", "format": "mp4"})
    assert r["mode"] == "native_video"

    # second call must hit the cached blob (no re-download)
    def boom(url, dst):
        raise AssertionError("re-downloaded a cached blob")

    monkeypatch.setattr(preview, "_download_to", boom)
    r2 = svc.resolve({"source": "divebar", "file_id": "FID", "format": "mp4"})
    assert r2["mode"] == "native_video"


def test_divebar_cdg_zip(svc, tmp_path, monkeypatch):
    monkeypatch.setattr(preview.divebar, "get_download_url",
                        lambda fid, cfg: "https://storage.googleapis.com/x/y.zip")
    monkeypatch.setattr(preview.utils, "divebar_ext", lambda url, fmt: ".zip")
    monkeypatch.setattr(preview, "_download_to", lambda url, dst: open(dst, "wb").write(b"PK\x03\x04"))

    def fake_extract(self, zp):
        self._temp_dir = str(tmp_path / "ztmp")
        os.makedirs(self._temp_dir, exist_ok=True)
        mp3 = os.path.join(self._temp_dir, "a.mp3")
        open(mp3, "wb").write(b"x")
        open(os.path.join(self._temp_dir, "a.cdg"), "wb").write(b"c")
        return mp3

    monkeypatch.setattr(preview.ZipPlayback, "extract_and_get_mp3", fake_extract)
    monkeypatch.setattr(preview.ZipPlayback, "current_cdg_path",
                        lambda self: os.path.join(self._temp_dir, "a.cdg"))
    monkeypatch.setattr(preview.ZipPlayback, "cleanup", lambda self: None)
    r = svc.resolve({"source": "divebar", "file_id": "FID2", "format": "zip"})
    assert r["mode"] == "cdg"


def test_divebar_no_url_unavailable(svc, monkeypatch):
    monkeypatch.setattr(preview.divebar, "get_download_url", lambda fid, cfg: None)
    r = svc.resolve({"source": "divebar", "file_id": "FID"})
    assert r["mode"] == "unavailable"


def test_divebar_no_file_id(svc):
    r = svc.resolve({"source": "divebar"})
    assert r["mode"] == "unavailable"
