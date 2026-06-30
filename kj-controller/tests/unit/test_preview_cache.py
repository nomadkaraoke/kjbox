import os
import time

from preview_cache import PreviewCache


def _cache(tmp_path):
    return PreviewCache(str(tmp_path), max_bytes=1000)


def test_local_key_changes_with_mtime(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x" * 10)
    c = _cache(tmp_path)
    k1 = c.local_key(str(f))
    os.utime(f, (time.time() + 50, time.time() + 50))
    k2 = c.local_key(str(f))
    assert k1 != k2 and len(k1) == 40


def test_gcs_key_stable(tmp_path):
    c = _cache(tmp_path)
    assert c.gcs_key("abc") == c.gcs_key("abc") and len(c.gcs_key("abc")) == 40


def test_done_gating(tmp_path):
    c = _cache(tmp_path)
    d = c.transcode_dir("k")
    os.makedirs(d)
    assert c.is_done("k") is False
    c.mark_done("k")
    assert c.is_done("k") is True


def test_evict_drops_oldest_complete_over_cap(tmp_path):
    c = PreviewCache(str(tmp_path), max_bytes=50)
    for name in ("k1", "k2"):
        d = c.transcode_dir(name)
        os.makedirs(d)
        with open(os.path.join(d, "seg-0.ts"), "wb") as fh:
            fh.write(b"y" * 40)
        c.mark_done(name)
        time.sleep(0.02)
    c.evict_if_needed()  # 80 bytes of complete entries > 50 cap -> oldest (k1) removed
    assert not os.path.isdir(c.transcode_dir("k1"))
    assert os.path.isdir(c.transcode_dir("k2"))


def test_evict_keeps_incomplete(tmp_path):
    c = PreviewCache(str(tmp_path), max_bytes=10)
    d = c.transcode_dir("k")
    os.makedirs(d)
    with open(os.path.join(d, "seg-0.ts"), "wb") as fh:
        fh.write(b"z" * 40)
    c.evict_if_needed()  # not .done -> never evicted
    assert os.path.isdir(d)


def test_gcsblob_path_creates_dir(tmp_path):
    c = _cache(tmp_path)
    p = c.gcsblob_path("FID", "blob.mp4")
    assert p.endswith(os.path.join("gcsblob", "FID", "blob.mp4"))
    assert os.path.isdir(os.path.dirname(p))
