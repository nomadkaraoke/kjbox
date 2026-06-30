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
    # Set explicit mtimes (k1 older) so ordering is deterministic regardless of
    # filesystem timestamp resolution.
    os.utime(os.path.join(c.transcode_dir("k1"), ".done"), (1000, 1000))
    os.utime(os.path.join(c.transcode_dir("k2"), ".done"), (2000, 2000))
    c.evict_if_needed()  # 80 bytes of complete entries > 50 cap -> oldest (k1) removed
    assert not os.path.isdir(c.transcode_dir("k1"))
    assert os.path.isdir(c.transcode_dir("k2"))


def test_evict_covers_gcsblob_and_cdg(tmp_path):
    c = PreviewCache(str(tmp_path), max_bytes=50)
    blob = c.gcsblob_path("fid1", "blob.mp4")
    with open(blob, "wb") as fh:
        fh.write(b"y" * 80)
    cdir = c.cdg_dir("k")
    os.makedirs(cdir)
    with open(os.path.join(cdir, "audio.mp3"), "wb") as fh:
        fh.write(b"z" * 10)
    os.utime(os.path.dirname(blob), (1000, 1000))   # oldest -> evicted first
    os.utime(cdir, (2000, 2000))
    c.evict_if_needed()  # 90 bytes > 50 cap -> oldest (the gcsblob) removed
    assert not os.path.isdir(os.path.dirname(blob))
    assert os.path.isdir(cdir)


def test_touch_transcode_refreshes_lru_position(tmp_path):
    c = PreviewCache(str(tmp_path), max_bytes=50)
    for name in ("k1", "k2"):
        d = c.transcode_dir(name)
        os.makedirs(d)
        with open(os.path.join(d, "seg-0.ts"), "wb") as fh:
            fh.write(b"y" * 40)
        c.mark_done(name)
    os.utime(os.path.join(c.transcode_dir("k1"), ".done"), (1000, 1000))  # k1 older
    os.utime(os.path.join(c.transcode_dir("k2"), ".done"), (2000, 2000))
    c.touch_transcode("k1")  # accessing k1 must move it to the front of the queue
    c.evict_if_needed()      # now k2 is oldest -> evicted instead of k1
    assert os.path.isdir(c.transcode_dir("k1"))
    assert not os.path.isdir(c.transcode_dir("k2"))


def test_evict_keeps_incomplete(tmp_path):
    c = PreviewCache(str(tmp_path), max_bytes=10)
    d = c.transcode_dir("k")
    os.makedirs(d)
    with open(os.path.join(d, "seg-0.ts"), "wb") as fh:
        fh.write(b"z" * 40)
    c.evict_if_needed()  # not .done -> never evicted
    assert os.path.isdir(d)


def test_gcsblob_path_hashes_id_and_creates_dir(tmp_path):
    c = _cache(tmp_path)
    p = c.gcsblob_path("FID", "blob.mp4")
    # dir is keyed by the hashed file_id, not the raw id
    assert os.path.basename(os.path.dirname(p)) == c.gcs_key("FID")
    assert os.path.basename(p) == "blob.mp4"
    assert os.path.isdir(os.path.dirname(p))
    # stays inside the cache root
    assert os.path.commonpath([os.path.realpath(p), os.path.realpath(str(tmp_path))]) \
        == os.path.realpath(str(tmp_path))


def test_gcsblob_path_rejects_traversal_names(tmp_path):
    import pytest
    c = _cache(tmp_path)
    for bad in ("../escape.mp4", "a/b.mp4", "..", ".", ""):
        with pytest.raises(ValueError):
            c.gcsblob_path("FID", bad)
