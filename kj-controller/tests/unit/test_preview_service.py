import os

import pytest

import preview
from preview import PreviewService


class FakeMedia:
    def __init__(self, roots):
        self.roots = roots

    def validate_path(self, p):
        # Proper directory containment (mirrors MediaIndex.validate_path) so the
        # path-escape tests can't pass via a mere shared prefix.
        rp = os.path.realpath(p)
        ok = any(
            os.path.commonpath([rp, os.path.realpath(r)]) == os.path.realpath(r)
            for r in self.roots
        ) and os.path.exists(rp)
        return rp if ok else None


@pytest.fixture
def svc(tmp_path):
    cfg = {"preview_cache_dir": str(tmp_path / "cache"), "preview_cache_max_bytes": 10 ** 9}
    return PreviewService(cfg, FakeMedia([str(tmp_path)]))


# ---- local ---------------------------------------------------------------

def test_local_audio(svc, tmp_path):
    f = tmp_path / "s.mp3"
    f.write_bytes(b"ID3" + b"\0" * 100)
    r = svc.resolve({"source": "local", "file_path": str(f)})
    assert r["mode"] == "native_audio" and r["token"]
    info = svc.token_info(r["token"])
    assert info["path"] == os.path.realpath(str(f))


def test_local_unknown_unavailable(svc, tmp_path):
    f = tmp_path / "s.xyz"
    f.write_bytes(b"..")
    r = svc.resolve({"source": "local", "file_path": str(f)})
    assert r["mode"] == "unavailable" and r["reason"]


def test_local_path_escape_blocked(svc):
    r = svc.resolve({"source": "local", "file_path": "/etc/passwd"})
    assert r["mode"] == "unavailable"


def test_local_video_native(svc, tmp_path, monkeypatch):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"\0" * 500)
    monkeypatch.setattr(preview, "_ffprobe_codecs", lambda p: ("h264", "aac"))
    r = svc.resolve({"source": "local", "file_path": str(f)})
    assert r["mode"] == "native_video"


def test_local_mkv_h264_goes_hls_due_to_container(svc, tmp_path, monkeypatch):
    # h264 but .mkv container is not reliably browser-native -> transcode
    f = tmp_path / "v.mkv"
    f.write_bytes(b"\0" * 500)
    monkeypatch.setattr(preview, "_ffprobe_codecs", lambda p: ("h264", "aac"))
    monkeypatch.setattr(svc.transcoder, "ensure_hls",
                        lambda src, dest, mark_done: os.path.join(dest, "index.m3u8"))
    r = svc.resolve({"source": "local", "file_path": str(f)})
    assert r["mode"] == "hls" and svc.token_info(r["token"])["hls_dir"]


def test_local_video_exotic_codec_goes_hls(svc, tmp_path, monkeypatch):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"\0" * 500)
    monkeypatch.setattr(preview, "_ffprobe_codecs", lambda p: ("hevc", "aac"))
    monkeypatch.setattr(svc.transcoder, "ensure_hls",
                        lambda src, dest, mark_done: os.path.join(dest, "index.m3u8"))
    r = svc.resolve({"source": "local", "file_path": str(f)})
    assert r["mode"] == "hls"


def test_prefer_transcode_forces_hls(svc, tmp_path, monkeypatch):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"\0" * 500)
    monkeypatch.setattr(preview, "_ffprobe_codecs", lambda p: ("h264", "aac"))
    monkeypatch.setattr(svc.transcoder, "ensure_hls",
                        lambda src, dest, mark_done: os.path.join(dest, "index.m3u8"))
    r = svc.resolve({"source": "local", "file_path": str(f), "prefer_transcode": True})
    assert r["mode"] == "hls"


def test_hls_cache_hit_skips_transcode(svc, tmp_path, monkeypatch):
    f = tmp_path / "v.mkv"
    f.write_bytes(b"\0" * 500)
    monkeypatch.setattr(preview, "_ffprobe_codecs", lambda p: ("hevc", "aac"))
    key = svc.cache.local_key(os.path.realpath(str(f)))
    os.makedirs(svc.cache.transcode_dir(key), exist_ok=True)
    svc.cache.mark_done(key)

    def boom(*a, **k):
        raise AssertionError("should not transcode on cache hit")

    monkeypatch.setattr(svc.transcoder, "ensure_hls", boom)
    r = svc.resolve({"source": "local", "file_path": str(f)})
    assert r["mode"] == "hls"


def test_local_cdg_extracts(svc, tmp_path, monkeypatch):
    f = tmp_path / "song.zip"
    f.write_bytes(b"PK\x03\x04rest")

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
    r = svc.resolve({"source": "local", "file_path": str(f)})
    assert r["mode"] == "cdg"
    assert svc.cdg_part_path(r["token"], "audio").endswith("audio.mp3")
    assert os.path.exists(svc.cdg_part_path(r["token"], "graphics"))


# ---- youtube -------------------------------------------------------------

def test_youtube(svc):
    r = svc.resolve({"source": "youtube", "youtube_url": "https://youtu.be/abc123"})
    assert r["mode"] == "youtube" and r["youtube_url"].endswith("abc123")


def test_youtube_missing_url(svc):
    r = svc.resolve({"source": "youtube"})
    assert r["mode"] == "unavailable"


def test_youtube_rejects_non_youtube_url(svc):
    r = svc.resolve({"source": "youtube", "youtube_url": "https://evil.example.com/x"})
    assert r["mode"] == "unavailable"


def test_youtube_rejects_http(svc):
    r = svc.resolve({"source": "youtube", "youtube_url": "http://youtube.com/watch?v=x"})
    assert r["mode"] == "unavailable"


# ---- token accessors -----------------------------------------------------

def test_close_clears_token(svc, tmp_path):
    f = tmp_path / "s.mp3"
    f.write_bytes(b"x" * 10)
    tok = svc.resolve({"source": "local", "file_path": str(f)})["token"]
    svc.close(tok)
    assert svc.token_info(tok) is None


def test_hls_path_rejects_bad_name(svc, tmp_path, monkeypatch):
    f = tmp_path / "v.mkv"
    f.write_bytes(b"\0" * 10)
    monkeypatch.setattr(preview, "_ffprobe_codecs", lambda p: ("hevc", None))
    monkeypatch.setattr(svc.transcoder, "ensure_hls",
                        lambda src, dest, mark_done: os.path.join(dest, "index.m3u8"))
    tok = svc.resolve({"source": "local", "file_path": str(f)})["token"]
    assert svc.hls_path(tok, "index.m3u8")
    assert svc.hls_path(tok, "seg-3.ts")
    assert svc.hls_path(tok, "../../etc/passwd") is None
    assert svc.hls_path(tok, "evil.txt") is None
