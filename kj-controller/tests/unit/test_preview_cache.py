import os
import time

from preview_cache import PreviewCache, content_signature


def _cache(tmp_path):
    return PreviewCache(str(tmp_path), max_bytes=1000)


def test_local_key_stable_across_mtime_and_rename(tmp_path):
    """Content-addressed: same bytes -> same key, regardless of path or mtime."""
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x" * 10)
    c = _cache(tmp_path)
    k1 = c.local_key(str(f))
    # An mtime touch (content unchanged) must NOT invalidate the cache.
    os.utime(f, (time.time() + 50, time.time() + 50))
    assert c.local_key(str(f)) == k1
    # A rename/move (content unchanged) must NOT invalidate the cache.
    g = tmp_path / "renamed.mp4"
    os.rename(f, g)
    assert c.local_key(str(g)) == k1
    assert len(k1) == 40


def test_local_key_changes_with_content(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x" * 10)
    c = _cache(tmp_path)
    k1 = c.local_key(str(f))
    f.write_bytes(b"y" * 10)  # same size, different bytes
    assert c.local_key(str(f)) != k1


def test_content_signature_same_content_different_path(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    a = tmp_path / "a.mp4"
    b = sub / "b.mp4"
    a.write_bytes(b"hello world" * 100)
    b.write_bytes(b"hello world" * 100)
    assert content_signature(str(a)) == content_signature(str(b))


def test_content_signature_differs_on_one_byte(tmp_path):
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    a.write_bytes(b"A" + b"x" * 100)
    b.write_bytes(b"B" + b"x" * 100)  # same size, one differing byte
    assert content_signature(str(a)) != content_signature(str(b))


def test_content_signature_large_file_uses_head_tail(tmp_path):
    """>2 MiB exercises the head/tail-sample branch; a differing tail is caught,
    identical large content matches."""
    base = b"\0" * (3 * 1024 * 1024)
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    c = tmp_path / "c.bin"
    a.write_bytes(base + b"TAILA")
    b.write_bytes(base + b"TAILB")
    c.write_bytes(base + b"TAILA")
    assert content_signature(str(a)) != content_signature(str(b))
    assert content_signature(str(a)) == content_signature(str(c))


def test_content_signature_empty_file(tmp_path):
    f = tmp_path / "empty.mp4"
    f.write_bytes(b"")
    assert len(content_signature(str(f))) == 40


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
