import os
import shutil

import pytest


@pytest.fixture
def media_dir(mock_config):
    # mock_config media_folders = [<tmp>/downloads, <tmp>/media]
    return mock_config["media_folders"][1]


def test_resolve_audio_then_range_stream(flask_test_client, media_dir):
    p = os.path.join(media_dir, "x.mp3")
    with open(p, "wb") as fh:
        fh.write(b"abcdefghij")
    r = flask_test_client.post("/preview/resolve", json={"source": "local", "file_path": p})
    body = r.get_json()
    assert body["mode"] == "native_audio"
    tok = body["token"]

    full = flask_test_client.get(f"/preview/stream/{tok}")
    assert full.status_code == 200 and full.data == b"abcdefghij"
    assert full.headers["Accept-Ranges"] == "bytes"

    rng = flask_test_client.get(f"/preview/stream/{tok}", headers={"Range": "bytes=2-4"})
    assert rng.status_code == 206 and rng.data == b"cde"
    assert rng.headers["Content-Range"] == "bytes 2-4/10"

    suffix = flask_test_client.get(f"/preview/stream/{tok}", headers={"Range": "bytes=-3"})
    assert suffix.status_code == 206 and suffix.data == b"hij"


def test_unsatisfiable_range_416(flask_test_client, media_dir):
    p = os.path.join(media_dir, "y.mp3")
    with open(p, "wb") as fh:
        fh.write(b"0123456789")
    tok = flask_test_client.post(
        "/preview/resolve", json={"source": "local", "file_path": p}).get_json()["token"]
    bad = flask_test_client.get(f"/preview/stream/{tok}", headers={"Range": "bytes=500-600"})
    assert bad.status_code == 416


def test_stream_bad_token_404(flask_test_client):
    assert flask_test_client.get("/preview/stream/nope").status_code == 404


def test_resolve_youtube(flask_test_client):
    r = flask_test_client.post(
        "/preview/resolve", json={"source": "youtube", "youtube_url": "https://youtu.be/abc"})
    assert r.get_json()["mode"] == "youtube"


def test_resolve_unavailable_for_outside_path(flask_test_client):
    r = flask_test_client.post(
        "/preview/resolve", json={"source": "local", "file_path": "/etc/passwd"})
    assert r.get_json()["mode"] == "unavailable"


def test_close_is_ok(flask_test_client, media_dir):
    p = os.path.join(media_dir, "z.mp3")
    with open(p, "wb") as fh:
        fh.write(b"x" * 5)
    tok = flask_test_client.post(
        "/preview/resolve", json={"source": "local", "file_path": p}).get_json()["token"]
    r = flask_test_client.post("/preview/close", json={"token": tok})
    assert r.get_json()["ok"] is True
    # token now gone -> stream 404s
    assert flask_test_client.get(f"/preview/stream/{tok}").status_code == 404


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_transcode_caches(flask_test_client, media_dir, flask_app):
    # Generate a tiny non-native (mkv) clip so resolve must transcode to HLS.
    import subprocess
    src = os.path.join(media_dir, "clip.mkv")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
         "-c:v", "mpeg4", src],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    r = flask_test_client.post("/preview/resolve", json={"source": "local", "file_path": src})
    body = r.get_json()
    assert body["mode"] == "hls"
    tok = body["token"]
    pl = flask_test_client.get(f"/preview/hls/{tok}/index.m3u8")
    assert pl.status_code == 200 and b"#EXTM3U" in pl.data

    # wait for the transcode to finish (.done written), then a second resolve is a cache hit
    import time
    key = flask_app.preview.cache.local_key(os.path.realpath(src))
    for _ in range(100):
        if flask_app.preview.cache.is_done(key):
            break
        time.sleep(0.1)
    assert flask_app.preview.cache.is_done(key)

    calls = {"n": 0}
    orig = flask_app.preview.transcoder.ensure_hls

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    flask_app.preview.transcoder.ensure_hls = counting
    r2 = flask_test_client.post("/preview/resolve", json={"source": "local", "file_path": src})
    assert r2.get_json()["mode"] == "hls" and calls["n"] == 0  # served from cache, no transcode
